import re
import os
from datetime import datetime
from config import WORKSPACE_ROOT
from logger import log
from utils.auth import SCOPE_TIERS


TIER_LABELS = {
    "basic": "基础权限",
    "dev": "开发权限",
    "full": "完全权限",
}

ROLE_LABELS = {
    "admin": "管理员",
    "user": "已授权",
    "pending": "待审批",
    "guest": "未授权",
    "banned": "已拉黑",
}

TIER_NOTES = {
    "basic": "对话、图片/文件解析、笔记偏好",
    "dev": "基础权限 + 项目切换、文件回传",
    "full": "开发权限 + 执行 Shell、查看额度",
}

class CardBuilder:
    @staticmethod
    def _create_footer():
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": f"⚡ Powered by codex | 🕒 {now}"
                }
            ]
        }

    @staticmethod
    def build_model_panel(available_models, current_model):
        model_groups = {}
        for model_name in available_models[:12]:
            lower = model_name.lower()
            if "gemini" in lower:
                group = "gemini"
            elif "claude" in lower:
                group = "claude"
            elif "gpt" in lower:
                group = "gpt"
            else:
                group = "other"
            model_groups.setdefault(group, []).append(model_name)
        
        group_meta = {
            "gemini": {"icon": "💎", "title": "Gemini 系列", "color": "blue"},
            "claude": {"icon": "🧠", "title": "Claude 系列", "color": "purple"},
            "gpt":    {"icon": "⚡", "title": "GPT 系列", "color": "green"},
            "other":  {"icon": "🔮", "title": "其他模型", "color": "grey"},
        }
        
        elements = [
            {
                "tag": "markdown",
                "content": f"🎯 **当前活跃模型**：`{current_model}`\n\n从下方选择一个模型，即可一键热切换："
            }
        ]
        
        for group_key in ["gemini", "claude", "gpt", "other"]:
            models = model_groups.get(group_key, [])
            if not models:
                continue
            meta = group_meta[group_key]
            
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "markdown",
                "content": f"{meta['icon']} **{meta['title']}**"
            })
            
            actions = []
            for model_name in models:
                is_current = (model_name == current_model)
                display = f"✅ {model_name}" if is_current else model_name
                btn_type = "primary" if is_current else "default"
                actions.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": display},
                    "type": btn_type,
                    "value": {"action": "switch_model", "model": model_name}
                })
            
            elements.append({
                "tag": "action",
                "layout": "flow",
                "actions": actions
            })
        
        elements.append({"tag": "hr"})
        elements.append(CardBuilder._create_footer())

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "violet",
                "title": {"content": "🎛️ 大模型切换控制台", "tag": "plain_text"}
            },
            "elements": elements
        }

    @staticmethod
    def build_model_switch_result_card(new_model, old_model):
        model_lower = new_model.lower()
        if "gemini" in model_lower:
            icon, color = "💎", "blue"
        elif "claude" in model_lower:
            icon, color = "🧠", "purple"
        elif "gpt" in model_lower:
            icon, color = "⚡", "green"
        else:
            icon, color = "🔮", "grey"
            
        elements = [
            {
                "tag": "markdown",
                "content": f"🎉 **模型切换成功！**\n\n{icon} 当前活跃模型已变更为：\n\n> **`{new_model}`**\n\n🔄 上一个模型：~~{old_model}~~\n\n*接下来的所有对话都将使用新模型进行响应。*"
            },
            {"tag": "hr"},
            CardBuilder._create_footer()
        ]
        
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": color,
                "title": {"content": f"{icon} 模型已切换", "tag": "plain_text"}
            },
            "elements": elements
        }

    @staticmethod
    def _guess_intent(text):
        if not text:
            return "✨ AI 思考中...", "正在为您生成回复，请稍候..."
        
        text = text.lower()
        if any(kw in text for kw in ["代码", "脚本", "编程", "重构", "xcode", "编译", "bug", "报错", "前端", "后端", "python", "swift"]):
            return "💻 代码工程模式", "AI 正在理解代码逻辑并为您进行开发与调试，请稍候..."
        elif any(kw in text for kw in ["搜", "查一下", "找一下", "检索", "全网"]):
            return "🔍 数据检索模式", "AI 正在跨域检索并为您归纳相关信息，请稍候..."
        elif any(kw in text for kw in ["翻译", "英文", "中文"]):
            return "🌐 翻译模式", "AI 正在为您进行精准翻译，请稍候..."
        elif any(kw in text for kw in ["总结", "归纳", "提炼", "重点"]):
            return "📝 总结提炼模式", "AI 正在帮您提炼核心要点，请稍候..."
        elif any(kw in text for kw in ["选项", "我的选择是"]):
            return "🎯 选项执行中", "AI 已收到您的选择，正在进行处理..."
        else:
            return "✨ AI 思考中...", "正在为您深度分析与生成回复，请稍候..."

    @staticmethod
    def _get_dynamic_think_text(base_text, think_seconds):
        if think_seconds <= 0:
            return base_text
            
        phrases = [
            "🧠 正在深度思考上下文...",
            "🔍 正在系统内检索相关线索...",
            "⚙️ 正在为您规划行动路径...",
            "💡 马上就好，正在组织语言...",
            "🚀 正在全速冲刺，请稍等..."
        ]
        # Rotate phrase every 2 seconds
        idx = (think_seconds // 2) % len(phrases)
        return f"{base_text}\n\n*( {phrases[idx]} 已耗时 {think_seconds}s )*"

    @staticmethod
    def build_typing_indicator(downloaded_file_name=None, download_success=True, user_text="", think_seconds=0):
        title, content = CardBuilder._guess_intent(user_text)
        content = CardBuilder._get_dynamic_think_text(content, think_seconds)
        
        if downloaded_file_name:
            if download_success:
                content = f"✅ 已成功获取资源：**{downloaded_file_name}**\n\n{content}"
            else:
                content = f"❌ 获取资源失败：**{downloaded_file_name}**\n\n{content}"

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"content": title, "tag": "plain_text"}
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": content
                },
                CardBuilder._create_footer()
            ]
        }

    @staticmethod
    def build_tool_indicator(tool_action, user_text="", downloaded_file_name=None, download_success=True, think_seconds=0):
        title, content = CardBuilder._guess_intent(user_text)
        
        # Override the text with the actual tool action
        tool_action = re.sub(r"[\r\n`]+", " ", str(tool_action or "")).strip()
        if len(tool_action) > 30:
            tool_action = tool_action[:30] + "…"
        time_hint = f"已运行 {think_seconds}s" if think_seconds > 0 else "请稍候..."
        content = f"**当前动作：** `{tool_action}`\n\n*(AI 正在运行底层命令或操作文件，{time_hint})*"
        
        if downloaded_file_name:
            if download_success:
                content = f"✅ 已成功获取资源：**{downloaded_file_name}**\n\n{content}"
            else:
                content = f"❌ 获取资源失败：**{downloaded_file_name}**\n\n{content}"

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "turquoise",
                "title": {"content": "🛠️ " + tool_action, "tag": "plain_text"}
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": content
                },
                CardBuilder._create_footer()
            ]
        }

    @staticmethod
    def build_download_indicator(file_name, media_type="文件"):
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "wathet",
                "title": {"content": "📥 资源加载中...", "tag": "plain_text"}
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"正在为您下载并解析多媒体资源：**{file_name}**\n\n大文件（如视频、PDF）可能需要数秒至一分钟，请稍候..."
                },
                CardBuilder._create_footer()
            ]
        }

    MAX_MARKDOWN_CHARS = 25000
    TRUNCATION_NOTICE = "\n\n\n> ⚠️ **回复内容过长，已截断显示。完整内容请拆分任务后分批查看。**"

    @staticmethod
    def build_ai_response(reply_text, choice_card_data=None, current_model="Default", current_project="默认", is_error=False, is_streaming=False):
        elements = []

        # 1. Main Text
        if reply_text:
            content = reply_text
            if len(content) > CardBuilder.MAX_MARKDOWN_CHARS:
                content = content[:CardBuilder.MAX_MARKDOWN_CHARS] + CardBuilder.TRUNCATION_NOTICE
            if is_streaming:
                content += " ⏳" # Blinking cursor effect
            elements.append({
                "tag": "markdown",
                "content": content
            })
            
        # 2. Interactive Options
        if choice_card_data and choice_card_data.get("options"):
            if reply_text:
                elements.append({"tag": "hr"})
                
            actions = []
            markdown_options = []
            is_long_options = any(len(opt) > 6 for opt in choice_card_data["options"])
            
            for i, opt in enumerate(choice_card_data["options"][:10]):
                prefix_match = re.match(r'^([a-zA-Z0-9\u4e00-\u9fa5]+)[:：.、]\s*(.*)$', opt)
                
                if prefix_match:
                    prefix = prefix_match.group(1).strip()
                    rest_text = prefix_match.group(2).strip()
                    if len(prefix) == 1 and prefix.encode('utf-8').isalpha():
                        btn_label = f"选项 {prefix}"
                    elif len(prefix) <= 4:
                        btn_label = prefix
                    else:
                        btn_label = f"选项 {i+1}"
                else:
                    btn_label = f"选项 {i+1}"
                    rest_text = opt
                
                if not is_long_options:
                    btn_label = opt[:50]
                    
                actions.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": btn_label},
                    "type": "default",
                    "value": {"action": "user_choice", "choice": opt, "label": btn_label}
                })
                if is_long_options:
                    markdown_options.append(f"- **{btn_label}**: {rest_text}")

            question_text = f"**{choice_card_data.get('question', '请选择：')}**"
            if is_long_options:
                question_text += "\n\n" + "\n".join(markdown_options)
                
            elements.append({
                "tag": "markdown",
                "content": question_text
            })
            
            # 根据选项数量动态设置布局，保证按钮等宽对齐
            layout_mode = "flow"
            if len(actions) == 2:
                layout_mode = "bisect"
            elif len(actions) == 3:
                layout_mode = "trisection"
                
            elements.append({
                "tag": "action",
                "layout": layout_mode,
                "actions": actions
            })

        # 3. Context Info Row
        if not is_error:
            project_name_only = "默认"
            if current_project and current_project not in ["默认", "Default"]:
                project_name_only = os.path.basename(current_project) or current_project
            else:
                project_name_only = current_project or "默认"
                
            elements.append({
                "tag": "markdown",
                "content": f"<font color='grey'>🤖 模型: {current_model} | 📂 项目: {project_name_only} | 💡 键入 /help 查看指令</font>"
            })

        # 4. Standard Footer
        elements.append(CardBuilder._create_footer())

        header_template = "red" if is_error else ("wathet" if is_streaming else "blue")
        header_title = "❌ 发生错误" if is_error else ("✨ AI 回复中..." if is_streaming else "✨ AI 回复")

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue" if not is_error else "red",
                "title": {"content": header_title, "tag": "plain_text"}
            },
            "elements": elements
        }

    @staticmethod
    def build_dir_browser_card(active_project_path, recent_projects=None, recent_page=1, workspace_root=None, ignored_projects=None):
        elements = []

        # 确定公共根目录
        proj_root = workspace_root if workspace_root else WORKSPACE_ROOT
        
        # 1. 顶部公共项目根目录展示（附带设置按钮）
        elements.append({
            "tag": "column_set",
            "flex_mode": "none",
            "columns": [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "vertical_align": "top",
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"⚙️ **当前公共项目根目录**：\n`{proj_root}`"
                        }
                    ]
                },
                {
                    "tag": "column",
                    "width": "auto",
                    "vertical_align": "top",
                    "elements": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "⚙️ 设置"},
                            "type": "default",
                            "size": "small",
                            "value": {"action": "set_workspace_root_prompt"}
                        }
                    ]
                }
            ]
        })
        
        # 2. 当前活跃开发工作区展示（无设置按钮）
        elements.append({
            "tag": "markdown",
            "content": f"📂 **当前活跃开发工作区**：\n`{active_project_path}`"
        })
        
        # 3. 新建项目动作行
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "➕ 新建项目"},
                    "type": "default",
                    "value": {"action": "create_project_prompt", "parent_path": proj_root}
                }
            ]
        })
        elements.append({"tag": "hr"})
        
        # 3. 扫描项目根目录 proj_root 下的所有子文件夹作为项目列表
        proj_root = proj_root
        all_projects = []
        try:
            for name in os.listdir(proj_root):
                if name.startswith('.') or name in ["venv", "downloads"]:
                    continue
                full_path = os.path.join(proj_root, name)
                if ignored_projects and full_path in ignored_projects:
                    continue
                if os.path.isdir(full_path):
                    all_projects.append((name, full_path))
            # 排序
            all_projects.sort(key=lambda x: x[0].lower())
        except Exception as e:
            log.error(f"Failed to scan project root: {e}")
            
        # 4. 合并数据库中记录的 recent_projects（防止有用户在外部路径单独添加的项目）
        if recent_projects:
            for p in recent_projects:
                if ignored_projects and p in ignored_projects:
                    continue
                if os.path.exists(p) and p not in [x[1] for x in all_projects] and p != "/":
                    all_projects.append((os.path.basename(p) or p, p))
                    
        # 5. 内嵌分页展示全部项目列表
        if all_projects:
            items_per_page = 5
            total_items = len(all_projects)
            total_pages = max(1, (total_items + items_per_page - 1) // items_per_page)
            
            page = max(1, min(recent_page, total_pages))
            
            elements.append({
                "tag": "markdown",
                "content": f"📁 **项目选择列表** (第 {page}/{total_pages} 页)："
            })
            
            start_idx = (page - 1) * items_per_page
            end_idx = start_idx + items_per_page
            page_items = all_projects[start_idx:end_idx]
            
            for name, p in page_items:
                # 高亮当前活跃项目
                is_active = (p == active_project_path)
                name_display = f"🌟 **{name} (当前活跃)**" if is_active else f"📁 **{name}**"
                
                columns = [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": f"{name_display}\n*`{p}`*"
                            }
                        ]
                    }
                ]
                
                # 如果不是当前活跃项目，提供选择按钮和列表删除按钮并排在右侧
                if not is_active:
                    columns.append({
                        "tag": "column",
                        "width": "auto",
                        "elements": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "选择"},
                                "type": "primary",
                                "value": {"action": "select_project", "path": p}
                            }
                        ]
                    })
                    columns.append({
                        "tag": "column",
                        "width": "auto",
                        "elements": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "删除"},
                                "type": "danger",
                                "value": {"action": "remove_project_from_list", "path": p}
                            }
                        ]
                    })
                else:
                    columns.append({
                        "tag": "column",
                        "width": "auto",
                        "elements": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "✅ 已选"},
                                "type": "default",
                                "value": {"action": "already_active"}
                            }
                        ]
                    })
                    
                elements.append({
                    "tag": "column_set",
                    "flex_mode": "none",
                    "columns": columns
                })
                
            # 分页控制
            page_actions = []
            if page > 1:
                page_actions.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "◀️ 上一页"},
                    "type": "default",
                    "value": {"action": "browse_recent_page", "page": page - 1, "current_path": active_project_path}
                })
            if page < total_pages:
                page_actions.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "下一页 ▶️"},
                    "type": "default",
                    "value": {"action": "browse_recent_page", "page": page + 1, "current_path": active_project_path}
                })
                
            if page_actions:
                elements.append({
                    "tag": "action",
                    "actions": page_actions
                })
        else:
            elements.append({
                "tag": "markdown",
                "content": "📭 *当前没有可用的项目，请点击上方按钮新建一个项目！*"
            })
            
        elements.append(CardBuilder._create_footer())
        
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"content": "📁 项目管理器", "tag": "plain_text"}
            },
            "elements": elements
        }

    @staticmethod
    def build_no_update_card(current_version):
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "green",
                "title": {"content": "✅ 系统已是最新版本", "tag": "plain_text"}
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"**当前运行版本**：`{current_version}`\n\n🎉 太棒了！经过全网云端探测，您的机器人的核心引擎已经是最新形态，无需任何更新操作。"
                },
                CardBuilder._create_footer()
            ]
        }

    @staticmethod
    def build_update_card(current_version, latest_version, changelog):
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {"content": "🔄 系统 OTA 升级提醒", "tag": "plain_text"}
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"**当前版本**：`{current_version}`\n**发现新版本**：`{latest_version}`\n\n**更新日志 (Changelog)**：\n{changelog}\n\n<font color='red'>⚠️ 警告：执行升级将进行强制同步，会覆盖本地所有未提交的代码修改（您的 .env 配置和本地数据库不受影响）。</font>"
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "确认并执行升级"},
                            "type": "primary",
                            "value": {"action": "user_choice", "choice": "/update confirm", "label": "确认并执行升级"}
                        }
                    ]
                },
                CardBuilder._create_footer()
            ]
        }

    @staticmethod
    def build_welcome_card():
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "green",
                "title": {"content": "🎉 部署成功！欢迎使用 codex 助手", "tag": "plain_text"}
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": "您好！我是您的 **codex 智能编程与系统开发助理**。\n\n当您看到这条消息，说明您的飞书机器人已经**成功部署并激活绑定**！\n\n我可以读取并修改您电脑上的文件、直接执行终端命令、跨网检索知识，还能接收并分析您发送给我的 PDF 文件、语音或截图。\n\n期待与您的合作，让我们开始吧！"
                },
                {
                    "tag": "hr"
                },
                {
                    "tag": "markdown",
                    "content": "💡 **快捷功能推荐** (点击下方按钮立即体验)："
                },
                {
                    "tag": "action",
                    "layout": "flow",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "📁 工作区项目"},
                            "type": "primary",
                            "value": {"action": "user_choice", "choice": "/project", "label": "工作区项目"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🤖 切换模型"},
                            "type": "default",
                            "value": {"action": "user_choice", "choice": "/model", "label": "切换模型"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🎭 查看帮助"},
                            "type": "default",
                            "value": {"action": "user_choice", "choice": "/help", "label": "查看帮助"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🧹 清空上下文"},
                            "type": "default",
                            "value": {"action": "user_choice", "choice": "/clear", "label": "清空上下文"}
                        }
                    ]
                },
                CardBuilder._create_footer()
            ]
        }

    @staticmethod
    def build_memory_card(memories):
        elements = []
        if not memories:
            elements.append({
                "tag": "markdown",
                "content": "📭 **当前没有记录您的任何长时偏好。**\n\n点击下方「➕ 新增偏好」按钮即可添加（例如：我开发只用 Python）。"
            })
        else:
            elements.append({
                "tag": "markdown",
                "content": "🧠 **您的长时偏好与设定记录**：\n*(点击右侧「忘记」可立即在机器人记忆中擦除对应条目，点击下方「➕ 新增偏好」可添加新条目)*"
            })
            elements.append({"tag": "hr"})
            
            for idx, m in enumerate(memories):
                columns = [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": f"🔹 {m}"
                            }
                        ]
                    },
                    {
                        "tag": "column",
                        "width": "auto",
                        "elements": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "忘记"},
                                "type": "danger",
                                "value": {"action": "forget_single_memory", "index": idx}
                            }
                        ]
                    }
                ]
                elements.append({
                    "tag": "column_set",
                    "flex_mode": "none",
                    "columns": columns
                })
                
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "➕ 新增偏好"},
                    "type": "primary",
                    "value": {"action": "add_memory_prompt"}
                }
            ]
        })
        elements.append(CardBuilder._create_footer())
        
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "purple",
                "title": {"content": "🧠 偏好记忆管理器", "tag": "plain_text"}
            },
            "elements": elements
        }

    @staticmethod
    def build_security_warning(blocked_command):
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "red",
                "title": {"content": "⚠️ 安全威胁拦截警告", "tag": "plain_text"}
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"🚨 **高危系统命令执行请求已被安全沙箱拦截！**\n\n您的输入中检测到了包含系统破坏性或高风险的黑名单特征指令，为了保护基座宿主系统的运行安全，已对该请求进行强行拦截与截断。\n\n**拦截的请求特征**：\n> `{blocked_command}`\n\n*(如果您确实有系统维护管理需求，请登录物理终端进行手动执行。)*"
                },
                CardBuilder._create_footer()
            ]
        }

    @staticmethod
    def build_note_list_card(notes):
        elements = []
        if not notes:
            elements.append({
                "tag": "markdown",
                "content": "📝 **您的记事本目前是空的。**\n\n点击下方「➕ 添加笔记」按钮即可添加。"
            })
        else:
            elements.append({
                "tag": "markdown",
                "content": "📝 **您的记事本内容：**"
            })
            for i, note in enumerate(notes):
                parts = note.split(' ', 1)
                title = parts[0]
                preview = parts[1][:40].replace('\n', ' ') + ("..." if len(parts[1]) > 40 else "") if len(parts) > 1 else ""
                
                md_content = f"**{i+1}.** {title}"
                if preview:
                    md_content += f"\n<font color='grey'>{preview}</font>"

                elements.append({
                    "tag": "column_set",
                    "flex_mode": "none",
                    "background_style": "default",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "vertical_align": "top",
                            "elements": [
                                {
                                    "tag": "markdown",
                                    "content": md_content
                                }
                            ]
                        },
                        {
                            "tag": "column",
                            "width": "auto",
                            "vertical_align": "top",
                            "elements": [
                                {
                                    "tag": "button",
                                    "text": {"tag": "plain_text", "content": "🔍 详情"},
                                    "type": "default",
                                    "size": "small",
                                    "value": {"action": "view_note_detail", "index": i}
                                },
                                {
                                    "tag": "button",
                                    "text": {"tag": "plain_text", "content": "🗑️ 删除"},
                                    "type": "danger",
                                    "size": "small",
                                    "value": {"action": "delete_note", "index": i}
                                }
                            ]
                        }
                    ]
                })
            
            elements.append({
                "tag": "hr"
            })
            
        note_actions = [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "➕ 添加笔记"},
                "type": "primary",
                "value": {"action": "add_note_prompt"}
            }
        ]
        if notes:
            note_actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🧹 清空全部记事本"},
                "type": "danger",
                "confirm": {
                    "title": {"tag": "plain_text", "content": "确认清空"},
                    "text": {"tag": "plain_text", "content": "您确定要清空所有笔记吗？此操作不可撤销。"}
                },
                "value": {"action": "clear_notes"}
            })
        elements.append({
            "tag": "action",
            "actions": note_actions
        })
            
        elements.append(CardBuilder._create_footer())
        
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {"content": "📔 机器人记事本", "tag": "plain_text"}
            },
            "elements": elements
        }

    @staticmethod
    def build_help_card():
        elements = [
            {
                "tag": "markdown",
                "content": (
                    "**🤖 会话与模型**\n"
                    "🔹 `/model` : 弹出模型切换面板，自动获取当前账号支持的 Codex 模型\n"
                    "🔹 `/clear` : 清空当前对话的上下文记忆，重新开始\n"
                    "🔹 `/context` : 查看当前会话的真实 Token 用量看板\n"
                    "🔹 `/quota` : 查询 Codex 官方订阅额度用量\n"
                    "🔹 `/stop` : 紧急刹车！强制中止正在后台生成的耗时任务"
                )
            },
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": (
                    "**📂 项目与工作区**\n"
                    "🔹 `/project` : 打开可视化项目管理器（翻页选择、新建项目、⚙️ 设置活跃工作区）\n"
                    "🔹 `/project <路径>` : 设定公共项目根目录"
                )
            },
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": (
                    "**🧠 记忆与笔记**\n"
                    "🔹 `/memory` : 偏好记忆管理器（面板内可 ➕ 新增偏好 / 忘记条目）\n"
                    "🔹 `/note` 或 `/notes` : 机器人记事本（面板内可 ➕ 添加笔记 / 查看 / 删除 / 清空）\n"
                    "🔹 `/brain` : 查看 Codex 全局记忆（~/.codex/AGENTS.md）"
                )
            },
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": (
                    "**🛠️ 系统运维**\n"
                    "🔹 `/status` : 查看机器人进程 CPU / 内存 / 运行状态\n"
                    "🔹 `/update` : 检查并获取云端最新版本的机器人引擎核心\n"
                    "🔹 `/ping` : 测试机器人是否在线\n"
                    "🔹 `/help` : 显示此帮助菜单"
                )
            },
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": (
                    "*✨ 隐藏黑科技提示：*\n"
                    "* **多模态解析**：直接向我发送图片或文档 (PDF/Word/文本)，我能直接阅读分析！*\n"
                    "* **远程终端**：我可以读取你电脑上的文件，甚至直接执行如 `ls -al` 等终端命令！*\n"
                    "* **全网搜索**：发给我任意网页链接，我可以帮你提取摘要！*"
                )
            },
            CardBuilder._create_footer()
        ]
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"content": "💡 Codex 机器人操作指南", "tag": "plain_text"}
            },
            "elements": elements
        }

    @staticmethod
    def build_status_card(cpu, mem_mb, uptime_str, status, restarts, err_logs, git_status="未知", bot_stats=None):
        status_emoji = "🟢" if status == "online" else "🔴"
        if not bot_stats:
            bot_stats = {"total_requests": 0, "success_requests": 0, "failed_requests": 0}
            
        elements = [
            {
                "tag": "markdown",
                "content": f"**服务状态**：{status_emoji} {status.upper()}\n**运行时长**：{uptime_str}\n**重启次数**：{restarts} 次"
            },
            {
                "tag": "hr"
            },
            {
                "tag": "markdown",
                "content": f"**🌿 代码库状态 (Git)**\n{git_status}"
            },
            {
                "tag": "hr"
            },
            {
                "tag": "markdown",
                "content": f"**📈 机器人请求统计**\n- **总请求数**: {bot_stats.get('total_requests', 0)}\n- **成功处理**: {bot_stats.get('success_requests', 0)}\n- **执行异常**: {bot_stats.get('failed_requests', 0)}"
            },
            {
                "tag": "hr"
            },
            {
                "tag": "markdown",
                "content": f"**💡 模型算力消耗统计 (自带模型)**\n- **累计消耗 Tokens**: {bot_stats.get('total_tokens', 0):,}"
            },
            {
                "tag": "hr"
            },
            {
                "tag": "markdown",
                "content": f"**💻 资源占用**\n- **CPU**：{cpu}%\n- **内存**：{mem_mb} MB"
            }
        ]
        
        elements.append({
            "tag": "hr"
        })
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔄 刷新状态"},
                    "type": "primary",
                    "value": {"action": "refresh_status"}
                }
            ]
        })
        
        elements.append(CardBuilder._create_footer())
        
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"content": "📊 服务器运行状态", "tag": "plain_text"}
            },
            "elements": elements
        }

    @staticmethod
    def build_global_memory_card(memories):
        elements = [
            {
                "tag": "markdown",
                "content": "**🧠 codex 全局记忆核心看板**\n这是一个自动生成的专属看板，用于展示机器人的长期跨会话记忆。所有的内容都会被持久化存储在宿主机本地。"
            },
            {
                "tag": "hr"
            }
        ]
        
        if not memories:
            elements.append({
                "tag": "markdown",
                "content": "*目前记忆库为空。*"
            })
        else:
            # Reverse to show newest first, limit to last 10 for card size
            recent_memories = list(reversed(memories))[:10]
            for idx, mem in enumerate(recent_memories):
                time_str = mem.get("time", mem.get("timestamp", "未知时间"))
                content = mem.get("memory", mem.get("content", ""))
                elements.append({
                    "tag": "markdown",
                    "content": f"**🕒 {time_str}**\n{content}"
                })
                if idx < len(recent_memories) - 1:
                    elements.append({
                        "tag": "hr"
                    })
                    
            if len(memories) > 10:
                elements.append({
                    "tag": "hr"
                })
                elements.append({
                    "tag": "markdown",
                    "content": f"*(还有 {len(memories) - 10} 条较早的记忆被折叠...)*"
                })
                
        elements.append(CardBuilder._create_footer())
        
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "purple",
                "title": {"content": "🧠 机器人全局记忆库", "tag": "plain_text"}
            },
            "elements": elements
        }

    @staticmethod
    def build_quota_card(quota_result, account_result=None):
        elements = []

        account = None
        if account_result and account_result.get("ok"):
            account = (account_result.get("data") or {}).get("account")

        if not quota_result or not isinstance(quota_result, dict):
            elements.append({
                "tag": "markdown",
                "content": "❌ **未获取到额度数据**\n查询返回为空，请稍后重试。"
            })
        elif not quota_result.get("ok"):
            err = quota_result.get("error", "unknown")
            msg = quota_result.get("message", "未知错误")
            if err == "not_installed":
                elements.append({
                    "tag": "markdown",
                    "content": (
                        "❌ **未找到 Codex CLI**\n"
                        "请先安装 Codex CLI（`npm i -g @openai/codex`）并确保 `codex` 命令位于 PATH 中。"
                    )
                })
            elif err == "not_logged_in":
                elements.append({
                    "tag": "markdown",
                    "content": (
                        "🔐 **未检测到 Codex 官方登录状态**\n"
                        "额度查询需要 ChatGPT 订阅账号（Plus / Pro / Team）。\n\n"
                        "请在宿主机终端运行：\n"
                        "`codex login`\n\n"
                        "完成 OAuth 登录后，再次发送 `/quota` 即可查询。"
                    )
                })
            elif err == "timeout":
                elements.append({
                    "tag": "markdown",
                    "content": f"⏱️ **查询超时**\n{msg}"
                })
            else:
                elements.append({
                    "tag": "markdown",
                    "content": f"❌ **查询失败**\n{msg}"
                })
        else:
            data = quota_result.get("data") or {}
            rate_limits = data.get("rateLimits") or data
            primary = rate_limits.get("primary") or {}
            secondary = rate_limits.get("secondary") or {}

            plan_type = (rate_limits.get("planType") or rate_limits.get("plan_type") or "未知套餐")
            credits = rate_limits.get("credits") or {}
            credits_balance = credits.get("balance")

            def _render_window(win, title_prefix):
                if not win:
                    return None
                used_pct = win.get("usedPercent")
                if used_pct is None:
                    return None
                try:
                    used_pct = float(used_pct)
                except (TypeError, ValueError):
                    return None
                remaining_pct = max(0.0, 100.0 - used_pct)
                filled = int(remaining_pct / 5)
                bar_str = "█" * filled + "░" * (20 - filled)

                duration_mins = win.get("windowDurationMins") or win.get("window_duration_mins")
                if duration_mins:
                    try:
                        mins = int(duration_mins)
                        if mins >= 7 * 24 * 60:
                            window_label = "周窗口"
                        elif mins >= 24 * 60:
                            window_label = "日窗口"
                        elif mins >= 60:
                            window_label = f"{mins // 60} 小时窗口"
                        else:
                            window_label = f"{mins} 分钟窗口"
                    except (TypeError, ValueError):
                        window_label = "窗口"
                else:
                    window_label = "窗口"

                resets_at = win.get("resetsAt") or win.get("resets_at")
                reset_line = ""
                if resets_at:
                    try:
                        from datetime import datetime, timezone
                        reset_ts = float(resets_at)
                        reset_dt = datetime.fromtimestamp(reset_ts, tz=timezone.utc).astimezone()
                        reset_line = f"\n🕒 重置时间: `{reset_dt.strftime('%Y-%m-%d %H:%M:%S')}`"
                    except Exception:
                        reset_line = ""

                progress_emoji = "🟢" if remaining_pct > 50 else ("🟡" if remaining_pct > 20 else "🔴")
                return (
                    f"{title_prefix}**{window_label}**\n"
                    f"`[{bar_str}]` **{remaining_pct:.1f}%** 剩余 ({progress_emoji})\n"
                    f"已使用 {used_pct:.1f}%{reset_line}"
                )

            header_line = f"📇 **套餐类型**: `{plan_type}`"
            if account:
                if account.get("type") == "chatgpt":
                    acct_email = account.get("email") or "未知"
                    acct_plan = account.get("planType") or ""
                    acct_line = f"👤 **登录账号**: `{acct_email}`" + (f" (ChatGPT {acct_plan})" if acct_plan else "")
                elif account.get("type") == "apiKey":
                    acct_line = "👤 **登录方式**: API Key"
                else:
                    acct_line = f"👤 **登录方式**: {account.get('type', '未知')}"
                elements.append({"tag": "markdown", "content": acct_line})
            elements.append({"tag": "markdown", "content": header_line})
            elements.append({"tag": "hr"})

            primary_block = _render_window(primary, "🔹 ")
            if primary_block:
                elements.append({"tag": "markdown", "content": primary_block})

            secondary_block = _render_window(secondary, "🔸 ")
            if secondary_block:
                if primary_block:
                    elements.append({"tag": "hr"})
                elements.append({"tag": "markdown", "content": secondary_block})

            if not primary_block and not secondary_block:
                elements.append({
                    "tag": "markdown",
                    "content": "ℹ️ 当前账号未返回窗口配额数据（可能是 usage-based 计费账号）。"
                })

            if credits_balance:
                elements.append({"tag": "hr"})
                elements.append({
                    "tag": "markdown",
                    "content": f"💰 **积分余额**: `{credits_balance}`"
                })

            elements.append({
                "tag": "hr"
            })
            elements.append({
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "数据来源：本机 Codex CLI (codex app-server --stdio) · 仅反映官方订阅额度，自定义 provider 不在此列"
                    }
                ]
            })

        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔄 刷新额度"},
                    "type": "primary",
                    "value": {"action": "refresh_quota"}
                }
            ]
        })

        elements.append(CardBuilder._create_footer())

        is_error = (not quota_result or not quota_result.get("ok")) if isinstance(quota_result, dict) else True
        header_template = "red" if is_error else "blue"
        header_title = "❌ 额度查询失败" if is_error else "📊 Codex 订阅额度看板"

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": header_template,
                "title": {"content": header_title, "tag": "plain_text"}
            },
            "elements": elements
        }

    @staticmethod
    def build_context_card(ctx, thread_id="", context_window=400000):
        elements = []
        if not ctx or not ctx.get("turns"):
            elements.append({
                "tag": "markdown",
                "content": "*当前会话还没有任何 token 用量记录。发送一条消息后再来查看吧。*"
            })
        else:
            last_input = ctx.get("last_input_tokens", 0)
            last_cached = ctx.get("last_cached_tokens", 0)
            last_output = ctx.get("last_output_tokens", 0)
            pct = min(100.0, round(last_input / context_window * 100, 1)) if context_window else 0
            filled = int(pct / 5)
            bar = "█" * filled + "░" * (20 - filled)
            level = "🟢" if pct < 50 else ("🟡" if pct < 80 else "🔴")
            elements.append({
                "tag": "markdown",
                "content": (
                    f"📥 **当前上下文占用（最近一轮输入）**\n"
                    f"`[{bar}]` **{pct}%** {level}\n"
                    f"`{last_input:,}` / `{context_window:,}` tokens（其中缓存命中 `{last_cached:,}`）\n\n"
                    f"📤 最近一轮输出：`{last_output:,}` tokens"
                )
            })
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "markdown",
                "content": (
                    f"📊 **本会话累计**\n"
                    f"- 对话轮数：`{ctx.get('turns', 0)}`\n"
                    f"- 累计输入：`{ctx.get('total_input_tokens', 0):,}` tokens\n"
                    f"- 累计输出：`{ctx.get('total_output_tokens', 0):,}` tokens\n"
                    f"- 活跃模型：`{ctx.get('model', '默认')}`"
                )
            })
            if thread_id:
                elements.append({
                    "tag": "note",
                    "elements": [{"tag": "plain_text", "content": f"会话线程: {thread_id}"}]
                })
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "markdown",
            "content": "💡 使用 `/clear` 可清空上下文并重置统计。"
        })
        elements.append(CardBuilder._create_footer())
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "wathet",
                "title": {"content": "🧮 上下文 Token 用量看板", "tag": "plain_text"}
            },
            "elements": elements
        }

    @staticmethod
    def build_brain_card(content, file_path):
        elements = [
            {
                "tag": "markdown",
                "content": "**🧠 Codex 全局记忆（AGENTS.md）**\n以下内容对本机所有 Codex 会话生效，直接编辑该文件即可修改。"
            },
            {
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": f"文件: {file_path}"}]
            },
            {"tag": "hr"},
        ]
        if content:
            display = content if len(content) <= 3000 else content[:3000] + "\n\n*(内容过长已截断...)*"
            elements.append({"tag": "markdown", "content": display})
        else:
            elements.append({
                "tag": "markdown",
                "content": "*全局记忆文件为空或不存在。可让我帮你写入，例如：*\n`把 \"回复始终使用中文\" 写进你的全局记忆 AGENTS.md`"
            })
        elements.append({"tag": "hr"})
        elements.append(CardBuilder._create_footer())
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "purple",
                "title": {"content": "🧠 Codex 全局记忆库", "tag": "plain_text"}
            },
            "elements": elements
        }

    # ------------------------------------------------------------------
    # Auth / permission cards (ported from upstream cards/auth.py)
    # ------------------------------------------------------------------

    @staticmethod
    def _short_id(cid):
        return str(cid or "")

    @staticmethod
    def _tier_label(scopes):
        scope_set = set(scopes or [])
        if not scope_set:
            return "全部权限"
        for tier in ("full", "dev", "basic"):
            if scope_set >= set(SCOPE_TIERS[tier]):
                return TIER_LABELS[tier]
        return "自定义权限"

    @staticmethod
    def _fmt_ts(ts):
        if not ts:
            return "-"
        try:
            return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return "-"

    @staticmethod
    def build_auth_hint_card():
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "grey",
                "title": {"content": "🔒 当前会话未授权", "tag": "plain_text"},
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        "您好，当前飞书会话尚未获得本机器人的使用权限。\n\n"
                        "如需使用，请发送 **`/auth`** 向管理员申请授权。"
                    ),
                },
                CardBuilder._create_footer(),
            ],
        }

    @staticmethod
    def build_admin_welcome_card():
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "green",
                "title": {"content": "👑 您已成为管理员", "tag": "plain_text"},
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        "首次部署绑定成功，本会话已被设为**最高管理员权限**。\n\n"
                        "- 其他会话发来的 `/auth` 申请会推送到这里，您可以在卡片上直接通过 / 拒绝。\n"
                        "- 使用 **`/user`** 管理已授权用户/群。\n"
                        "- 您的会话不受权限与限流限制。"
                    ),
                },
                CardBuilder._create_footer(),
            ],
        }

    @staticmethod
    def build_auth_request_card(req):
        chat_type = req.get("chat_type", "p2p")
        chat_type_label = "私聊" if chat_type != "group" else "群聊"
        display_name = req.get("display_name") or "（未获取到名称）"
        if req.get("display_name") is None or not str(req.get("display_name", "")).strip():
            display_name = CardBuilder._short_id(req.get("chat_id", "")) or "（未获取到名称）"
        last_msg = (req.get("last_message") or "").strip() or "（无）"

        elements = [
            {
                "tag": "markdown",
                "content": (
                    f"**{chat_type_label}会话**：`{display_name}`\n"
                    f"**会话 ID**：`{req.get('chat_id', '')}`\n"
                    f"**申请者**：`{req.get('sender_open_id') or '-'}`\n"
                    f"**申请时间**：{CardBuilder._fmt_ts(req.get('last_request_at'))}\n"
                    f"**累计申请**：{req.get('request_count') or 0} 次\n"
                    f"**最近消息**：{last_msg}"
                ),
            },
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": "请选择授权档位，或拒绝该申请：",
            },
        ]

        actions = []
        for tier in ("basic", "dev", "full"):
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"✅ {TIER_LABELS[tier]}"},
                "type": "primary",
                "value": {"action": "auth_approve", "chat_id": req.get("chat_id", ""), "tier": tier},
            })
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "❌ 拒绝"},
            "type": "default",
            "value": {"action": "auth_deny", "chat_id": req.get("chat_id", "")},
        })
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "🚫 拉黑"},
            "type": "danger",
            "value": {"action": "auth_ban", "chat_id": req.get("chat_id", "")},
        })

        elements.append({"tag": "action", "actions": actions})
        elements.append(CardBuilder._create_footer())

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {"content": "🔔 新的权限申请", "tag": "plain_text"},
            },
            "elements": elements,
        }

    @staticmethod
    def build_auth_result_card(ok, detail=""):
        if ok:
            title = "✅ 授权成功"
            template = "green"
        else:
            title = "⛔ 申请未通过"
            template = "red"
        return {
            "config": {"wide_screen_mode": True},
            "header": {"template": template, "title": {"content": title, "tag": "plain_text"}},
            "elements": [
                {"tag": "markdown", "content": detail or "管理员已处理您的权限申请。"},
                CardBuilder._create_footer(),
            ],
        }

    @staticmethod
    def build_user_edit_card(sess):
        display = (sess.get("display_name") or "").strip()
        chat_short = CardBuilder._short_id(sess.get("chat_id", ""))
        if display and display != chat_short and not display.startswith("oc_"):
            who = f"**{display}** (`{chat_short}`)"
        else:
            who = f"**{display or chat_short or '未知会话'}**"

        current_tier = CardBuilder._tier_label(sess.get("scopes") or [])
        elements = [
            {
                "tag": "markdown",
                "content": (
                    f"✏️ 正在编辑：{who}\n"
                    f"当前权限级别：**{current_tier}**\n\n"
                    "请选择新的权限级别："
                ),
            },
            {"tag": "hr"},
        ]

        for tier in ("basic", "dev", "full"):
            elements.append({
                "tag": "markdown",
                "content": f"**{TIER_LABELS[tier]}**：{TIER_NOTES[tier]}",
            })
            elements.append({
                "tag": "action",
                "actions": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": f"设为{TIER_LABELS[tier]}"},
                    "type": "primary",
                    "value": {"action": "user_set_tier", "chat_id": sess.get("chat_id", ""), "tier": tier},
                }],
            })

        elements.append({
            "tag": "action",
            "actions": [{
                "tag": "button",
                "text": {"tag": "plain_text", "content": "取消"},
                "type": "default",
                "value": {"action": "user_edit_cancel"},
            }],
        })
        elements.append(CardBuilder._create_footer())

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "turquoise",
                "title": {"content": "✏️ 编辑会话权限", "tag": "plain_text"},
            },
            "elements": elements,
        }

    @staticmethod
    def build_user_panel_card(sessions, page=1, page_size=6):
        total = len(sessions)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        start = (page - 1) * page_size
        page_sessions = sessions[start:start + page_size]

        elements = [
            {
                "tag": "markdown",
                "content": f"**会话管理面板**（第 {page}/{total_pages} 页，共 {total} 个会话）",
            }
        ]

        for sess in page_sessions:
            role = sess.get("role", "guest")
            chat_type = sess.get("chat_type", "p2p")
            label = ROLE_LABELS.get(role, role)
            display = (sess.get("display_name") or "").strip()
            chat_short = CardBuilder._short_id(sess.get("chat_id", ""))
            if display and display != chat_short and not display.startswith("oc_"):
                name_part = f"**{display}** (`{chat_short}`)"
            else:
                name_part = f"**{display or chat_short or '未知会话'}**"
            scopes = sess.get("scopes") or []

            content = (
                f"{name_part}\n"
                f"类型：{'群聊' if chat_type == 'group' else '私聊'} | 角色：**{label}** | 权限级别：**{CardBuilder._tier_label(scopes)}**\n"
                f"更新时间：{CardBuilder._fmt_ts(sess.get('updated_at'))}"
            )

            row_actions = []
            if role == "pending":
                row_actions.append({
                    "tag": "button", "text": {"tag": "plain_text", "content": "✅ 通过"},
                    "type": "primary",
                    "value": {"action": "auth_approve", "chat_id": sess.get("chat_id", ""), "tier": "basic"},
                })
                row_actions.append({
                    "tag": "button", "text": {"tag": "plain_text", "content": "❌ 拒绝"},
                    "type": "default",
                    "value": {"action": "auth_deny", "chat_id": sess.get("chat_id", "")},
                })
            elif role == "user":
                row_actions.append({
                    "tag": "button", "text": {"tag": "plain_text", "content": "撤销"},
                    "type": "default",
                    "value": {"action": "user_action", "op": "revoke", "chat_id": sess.get("chat_id", "")},
                })
                row_actions.append({
                    "tag": "button", "text": {"tag": "plain_text", "content": "编辑"},
                    "type": "primary",
                    "value": {"action": "user_edit", "chat_id": sess.get("chat_id", "")},
                })
                row_actions.append({
                    "tag": "button", "text": {"tag": "plain_text", "content": "🚫 拉黑"},
                    "type": "danger",
                    "value": {"action": "auth_ban", "chat_id": sess.get("chat_id", "")},
                })
            elif role == "banned":
                row_actions.append({
                    "tag": "button", "text": {"tag": "plain_text", "content": "解除拉黑"},
                    "type": "default",
                    "value": {"action": "user_action", "op": "unban", "chat_id": sess.get("chat_id", "")},
                })

            left_col = {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "elements": [{"tag": "markdown", "content": content}],
            }
            right_col = {
                "tag": "column",
                "width": "auto",
                "elements": list(row_actions),
            }
            elements.append({
                "tag": "column_set",
                "flex_mode": "none",
                "columns": [left_col, right_col],
            })
            elements.append({"tag": "hr"})

        if not page_sessions:
            elements.append({"tag": "markdown", "content": "📭 暂无会话记录。"})

        page_actions = []
        if page > 1:
            page_actions.append({
                "tag": "button", "text": {"tag": "plain_text", "content": "◀️ 上一页"},
                "type": "default",
                "value": {"action": "user_page", "page": page - 1},
            })
        if page < total_pages:
            page_actions.append({
                "tag": "button", "text": {"tag": "plain_text", "content": "下一页 ▶️"},
                "type": "default",
                "value": {"action": "user_page", "page": page + 1},
            })
        if page_actions:
            elements.append({"tag": "action", "actions": page_actions})

        elements.append(CardBuilder._create_footer())
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"content": "👥 用户 / 群权限管理", "tag": "plain_text"},
            },
            "elements": elements,
        }

    @staticmethod
    def build_rate_limit_card():
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "yellow",
                "title": {"content": "⏳ 操作过于频繁", "tag": "plain_text"},
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": "请求过于频繁，请稍等片刻再试。",
                },
                CardBuilder._create_footer(),
            ],
        }

    # ------------------------------------------------------------------
    # Cron / scheduled-task cards (ported from upstream cards/cron.py)
    # ------------------------------------------------------------------

    @staticmethod
    def _format_ts(ts):
        if not ts or ts <= 0:
            return "尚未运行"
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')

    @staticmethod
    def build_cron_panel_card(tasks, active_tab="user", session_data=None):
        user_tasks = [t for t in tasks if t.get('category') == 'user']
        sys_tasks = [t for t in tasks if t.get('category') == 'system']
        displayed_tasks = user_tasks if active_tab == 'user' else sys_tasks

        elements = [
            {
                "tag": "markdown",
                "content": (
                    f"**⏱️ 计划任务管理中心 (Cron Center)**\n"
                    f"包含用户创建的周期指令与系统级后台任务。"
                    f"选中的分类：**{'👤 用户主动任务' if active_tab == 'user' else '⚙️ 系统后台任务'}**"
                ),
            }
        ]

        header_actions = [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "👤 用户任务" if active_tab != 'user' else "🔵 👤 用户任务"},
                "type": "primary" if active_tab == 'user' else "default",
                "value": {"action": "switch_cron_tab", "tab": "user"},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "⚙️ 系统任务" if active_tab != 'system' else "🔵 ⚙️ 系统任务"},
                "type": "primary" if active_tab == 'system' else "default",
                "value": {"action": "switch_cron_tab", "tab": "system"},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "➕ 新建任务"},
                "type": "primary",
                "value": {"action": "open_cron_create"},
            },
        ]
        elements.append({"tag": "action", "layout": "bisect", "actions": header_actions})
        elements.append({"tag": "hr"})

        if not displayed_tasks:
            elements.append({
                "tag": "markdown",
                "content": (
                    f"*(暂无{'用户主动' if active_tab == 'user' else '系统后台'}计划任务)*\n"
                    "点击上方 **[ ➕ 新建任务 ]** 即可添加一个定时任务！"
                ),
            })
        else:
            for t in displayed_tasks:
                t_id = t.get('id')
                is_active = bool(t.get('is_active', 1))
                status_icon = "🟢 启用中" if is_active else "🔴 已暂停"
                cron_expr = t.get('cron_expr', '')
                task_type = "标准 Cron" if t.get('task_type') == 'cron' else "延迟倒计时"
                proj = t.get('project_path', '')
                proj_name = os.path.basename(proj) if proj else "默认工作区"

                last_run = CardBuilder._format_ts(t.get('last_run_at'))
                next_run = CardBuilder._format_ts(t.get('next_run_at'))
                prompt_preview = t.get('prompt', '')
                if len(prompt_preview) > 60:
                    prompt_preview = prompt_preview[:60] + "..."

                task_md = (
                    f"**{t.get('name', '未命名任务')}** (`{t_id}`) | 状态：**{status_icon}**\n"
                    f"• **触发规则**：`{cron_expr}` ({task_type})\n"
                    f"• **执行指令**：`{prompt_preview}`\n"
                    f"• **关联项目**：`{proj_name}` | **累计运行**：{t.get('run_count', 0)} 次\n"
                    f"• **上次运行**：{last_run} | **下次触发**：{next_run}"
                )
                elements.append({"tag": "markdown", "content": task_md})

                task_actions = [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⚡ 立即触发"},
                        "type": "default",
                        "value": {"action": "run_cron_now", "task_id": t_id},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⏸️ 暂停" if is_active else "▶️ 启用"},
                        "type": "default",
                        "value": {"action": "toggle_cron_active", "task_id": t_id, "is_active": not is_active},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🗑️ 删除"},
                        "type": "danger",
                        "value": {"action": "delete_cron_task", "task_id": t_id},
                    },
                ]
                elements.append({"tag": "action", "layout": "flow", "actions": task_actions})
                elements.append({"tag": "hr"})

        elements.append(CardBuilder._create_footer())
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "⏱️ 计划任务管理中心 (Cron Center)"},
                "template": "blue",
            },
            "elements": elements,
        }

    @staticmethod
    def build_cron_start_card(task_data):
        t_name = task_data.get('name', '计划任务')
        t_id = task_data.get('id', '')
        cat = "👤 用户任务" if task_data.get('category') == 'user' else "⚙️ 系统任务"
        expr = task_data.get('cron_expr', '')
        prompt = task_data.get('prompt', '')

        content = (
            f"**▶️ 计划任务已触发，正在后台启动执行...**\n\n"
            f"• **任务名称**：**{t_name}** (`{t_id}`)\n"
            f"• **任务类别**：{cat} | **触发规则**：`{expr}`\n"
            f"• **执行指令**：`{prompt}`\n\n"
            f"⏳ *正在运行 Agent 分析并生成结果，请稍候...*"
        )
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"⏰ 触发提示: {t_name}"},
                "template": "orange",
            },
            "elements": [{"tag": "markdown", "content": content}],
        }

    @staticmethod
    def build_cron_execution_card(task_data, result_text, is_error=False, duration_ms=0):
        t_name = task_data.get('name', '计划任务')
        t_id = task_data.get('id', '')
        cat = "👤 用户任务" if task_data.get('category') == 'user' else "⚙️ 系统任务"
        dur_sec = f"{duration_ms / 1000.0:.1f} 秒" if duration_ms > 0 else "< 1 秒"

        header_color = "red" if is_error else "green"
        status_title = "❌ 计划任务执行异常" if is_error else "✅ 计划任务报告"

        elements = [
            {
                "tag": "markdown",
                "content": (
                    f"**📌 任务基础信息**\n"
                    f"• **任务名称**：**{t_name}** (`{t_id}`) | **类别**：{cat}\n"
                    f"• **完成耗时**：{dur_sec} | **完成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                ),
            },
            {"tag": "hr"},
            {"tag": "markdown", "content": f"**📊 执行结果与报告**\n\n{result_text}"},
            {"tag": "hr"},
            {
                "tag": "action",
                "layout": "flow",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔄 再次运行"},
                        "type": "primary",
                        "value": {"action": "run_cron_now", "task_id": t_id},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⚙️ 管理任务中心"},
                        "type": "default",
                        "value": {"action": "open_cron_panel"},
                    },
                ],
            },
        ]
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"{status_title}: {t_name}"},
                "template": header_color,
            },
            "elements": elements,
        }

    @staticmethod
    def build_cron_created_card(task_data):
        t_name = task_data.get('name', '计划任务')
        t_id = task_data.get('id', '')
        cat = "👤 用户主动任务" if task_data.get('category') == 'user' else "⚙️ 系统后台任务"
        expr = task_data.get('cron_expr', '')
        task_type = "标准 Cron 表达式" if task_data.get('task_type') == 'cron' else "倒计时定时器"
        prompt = task_data.get('prompt', '')

        next_ts = task_data.get('next_run_at', 0)
        next_str = datetime.fromtimestamp(next_ts).strftime('%Y-%m-%d %H:%M:%S') if next_ts > 0 else "算中..."

        elements = [
            {
                "tag": "markdown",
                "content": (
                    f"**📌 任务基本信息**\n"
                    f"• **任务名称**：**{t_name}** (`{t_id}`)\n"
                    f"• **任务类别**：{cat} | **规则类型**：{task_type}\n"
                    f"• **触发规则**：`{expr}` | **下次预计触发**：`{next_str}`"
                ),
            },
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": (
                    f"**📝 预设执行 Prompt**\n`{prompt}`\n\n"
                    "🛡️ *该任务已持久化存入数据库，中途发生重启亦可自动恢复倒计时与触发。*"
                ),
            },
            {"tag": "hr"},
            {
                "tag": "action",
                "layout": "flow",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⚡ 立即触发一次"},
                        "type": "primary",
                        "value": {"action": "run_cron_now", "task_id": t_id},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⚙️ 打开任务中心"},
                        "type": "default",
                        "value": {"action": "open_cron_panel"},
                    },
                ],
            },
            CardBuilder._create_footer(),
        ]
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"✅ 计划任务创建成功: {t_name}"},
                "template": "green",
            },
            "elements": elements,
        }

    # ------------------------------------------------------------------
    # Plugin-center cards (ported from upstream cards/plugin.py)
    # ------------------------------------------------------------------

    @staticmethod
    def build_plugin_panel_card(plugin_list, active_tab="installed"):
        from plugin_store import load_plugin_sources

        is_installed = active_tab == "installed"
        tab_actions = [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"{'▶ ' if is_installed else ''}📦 已安装插件 ({len(plugin_list)})"},
                "type": "primary" if is_installed else "default",
                "value": {"action": "switch_plugin_tab", "tab": "installed"},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"{'▶ ' if not is_installed else ''}🏪 插件源与商店"},
                "type": "primary" if not is_installed else "default",
                "value": {"action": "switch_plugin_tab", "tab": "sources"},
            },
        ]

        elements = [
            {"tag": "action", "layout": "flow", "actions": tab_actions},
            {"tag": "hr"},
        ]

        if is_installed:
            if not plugin_list:
                elements.append({
                    "tag": "markdown",
                    "content": "⚠️ *当前 `plugins/` 目录下暂无安装的插件。切换到【🏪 插件源与商店】Tab 或使用 GitHub URL 即可一键安装扩展能力。*",
                })
            else:
                for p in plugin_list:
                    pid = p.get("id", "")
                    name = p.get("name", pid)
                    version = p.get("version", "1.0.0")
                    cmds = p.get("commands", [])
                    cmd_str = ", ".join([f"`{c}`" for c in cmds]) if cmds else "无专属指令"
                    enabled = p.get("enabled", True)
                    status_tag = "🟢 已激活" if enabled else "⚪ 已禁用"

                    elements.append({
                        "tag": "markdown",
                        "content": (
                            f"**{name}** (`{pid}` v{version})\n"
                            f"• **运行状态**：{status_tag}\n"
                            f"• **注册指令**：{cmd_str}"
                        ),
                    })
                    elements.append({
                        "tag": "action",
                        "layout": "flow",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "🔄 检查更新"},
                                "type": "default",
                                "value": {"action": "update_plugin", "plugin_id": pid},
                            },
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "🗑️ 物理卸载"},
                                "type": "danger",
                                "value": {"action": "uninstall_plugin", "plugin_id": pid},
                            },
                        ],
                    })
                    elements.append({"tag": "hr"})

            elements.append({
                "tag": "action",
                "layout": "flow",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📥 从 GitHub URL 安装插件"},
                        "type": "primary",
                        "value": {"action": "prompt_install_github"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔄 热重载插件库 (Reload)"},
                        "type": "default",
                        "value": {"action": "reload_plugins"},
                    },
                ],
            })
        else:
            load_plugin_sources()
            elements.append({
                "tag": "markdown",
                "content": "**🏪 插件源与商店**\n• **当前插件源仓库**：`https://github.com/Level6me/feishu-bot-plugin`",
            })
            elements.append({"tag": "hr"})

            try:
                from plugin_store import fetch_remote_store_plugins
                remote_plugins = fetch_remote_store_plugins(force_refresh=False) or []
            except Exception:
                remote_plugins = []

            if not remote_plugins:
                remote_plugins = [{
                    "id": "server_health", "name": "🖥️ 服务器巡检与健康报告",
                    "repo_url": "https://github.com/Level6me/feishu-bot-plugin",
                    "description": "监控 CPU 负载、内存率、磁盘余量，发送 /sysinfo 即可查看",
                }]

            elements.append({"tag": "markdown", "content": f"**🌟 在线插件扩展库 (共 {len(remote_plugins)} 个插件)**"})
            installed_ids = {p.get("id") for p in plugin_list}

            for rem in remote_plugins:
                r_id = rem["id"]
                r_name = rem.get("name", r_id)
                r_url = rem.get("repo_url", "https://github.com/Level6me/feishu-bot-plugin")
                r_desc = rem.get("description", "")
                is_installed_rem = r_id in installed_ids

                btn_text = "卸载" if is_installed_rem else "安装"
                btn_type = "danger" if is_installed_rem else "primary"
                action_dict = (
                    {"action": "uninstall_plugin", "plugin_id": r_id}
                    if is_installed_rem
                    else {"action": "install_github_repo", "repo_url": r_url, "plugin_id": r_id}
                )

                elements.append({
                    "tag": "column_set",
                    "flex_mode": "none",
                    "background_style": "default",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 4,
                            "elements": [{"tag": "markdown", "content": f"**{r_name}** (`{r_id}`)\n{r_desc}"}],
                        },
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "vertical_align": "center",
                            "elements": [
                                {
                                    "tag": "button",
                                    "text": {"tag": "plain_text", "content": btn_text},
                                    "type": btn_type,
                                    "value": action_dict,
                                }
                            ],
                        },
                    ],
                })
                elements.append({"tag": "hr"})

            elements.append({
                "tag": "action",
                "layout": "flow",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔄 刷新插件列表"},
                        "type": "primary",
                        "value": {"action": "refresh_store_plugins"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "➕ 添加 GitHub 插件源"},
                        "type": "default",
                        "value": {"action": "prompt_add_source"},
                    },
                ],
            })

        elements.append(CardBuilder._create_footer())
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🧩 机器人插件中心与应用商店"},
                "template": "indigo",
            },
            "elements": elements,
        }
