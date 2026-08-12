"""卡片构建域：对话回复与引导卡片（阶段 5 第 3 步：自 card_builder.py 迁出）。"""
import os
import re
from datetime import datetime

from config import WORKSPACE_ROOT
from logger import log
from utils.auth import SCOPE_TIERS
from cards.constants import TIER_LABELS, ROLE_LABELS, TIER_NOTES, MAX_MARKDOWN_CHARS, TRUNCATION_NOTICE
from cards.common import (
    _create_footer, _guess_intent, _get_dynamic_think_text,
    _short_id, _tier_label, _fmt_ts, _format_ts,
)


def build_ai_response(reply_text, choice_card_data=None, current_model="Default", current_project="默认", is_error=False, is_streaming=False):
        elements = []

        # 1. Main Text
        if reply_text:
            content = reply_text
            if len(content) > MAX_MARKDOWN_CHARS:
                content = content[:MAX_MARKDOWN_CHARS] + TRUNCATION_NOTICE
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
        elements.append(_create_footer())

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
                _create_footer()
            ]
        }


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
            _create_footer()
        ]
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"content": "💡 Codex 机器人操作指南", "tag": "plain_text"}
            },
            "elements": elements
        }


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
                _create_footer()
            ]
        }
