"""卡片构建域：通用工具与状态指示卡片（阶段 5 第 3 步：自 card_builder.py 迁出）。"""
import os
import re
from datetime import datetime

from config import WORKSPACE_ROOT
from logger import log
from utils.auth import SCOPE_TIERS
from cards.constants import TIER_LABELS, ROLE_LABELS, TIER_NOTES, MAX_MARKDOWN_CHARS, TRUNCATION_NOTICE


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


def _short_id(cid):
        return str(cid or "")


def _tier_label(scopes):
        scope_set = set(scopes or [])
        if not scope_set:
            return "全部权限"
        for tier in ("full", "dev", "basic"):
            if scope_set >= set(SCOPE_TIERS[tier]):
                return TIER_LABELS[tier]
        return "自定义权限"


def _fmt_ts(ts):
        if not ts:
            return "-"
        try:
            return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return "-"


def _format_ts(ts):
        if not ts or ts <= 0:
            return "尚未运行"
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')


def build_typing_indicator(downloaded_file_name=None, download_success=True, user_text="", think_seconds=0):
        title, content = _guess_intent(user_text)
        content = _get_dynamic_think_text(content, think_seconds)
        
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
                _create_footer()
            ]
        }


def build_tool_indicator(tool_action, user_text="", downloaded_file_name=None, download_success=True, think_seconds=0):
        title, content = _guess_intent(user_text)
        
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
                _create_footer()
            ]
        }


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
                _create_footer()
            ]
        }


def build_streaming_indicator(partial_text, tool_action=None, user_text="", think_seconds=0):
        """流式打字机卡片：实时展示模型已输出的部分文本。"""
        title, _ = _guess_intent(user_text)
        header_title = f"⚡ 正在生成 | {title}"

        status_bar = f"*( ⏱️ 已思考 {think_seconds}s"
        if tool_action:
            status_bar += f" | 🛠️ {tool_action}"
        status_bar += " )*\n\n---\n\n"

        # 超长文本截断到安全载荷，保留末尾最新内容
        max_streaming_chars = 3500
        display_text = partial_text or ""
        if len(display_text) > max_streaming_chars:
            display_text = "... (前文已自动隐藏) ...\n\n" + display_text[-max_streaming_chars:]

        full_content = status_bar + display_text + " ▌"

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"content": header_title, "tag": "plain_text"}
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": full_content
                },
                _create_footer()
            ]
        }


def build_stall_warning_card(user_prompt, think_seconds, stall_seconds):
        """停滞预警卡片：连续多分钟无输出时推送，带继续等待 / 叫停按钮。"""
        mins = stall_seconds // 60
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {"content": "⏱️ AI 处于深度推理等待中", "tag": "plain_text"}
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"模型已思考 **{think_seconds} 秒**（静默生成等待已达 **{mins} 分钟**）。\n\n"
                               "*模型 API 正在云端进行思维链推理或长数据检索，后台守护机制正常工作中，已为您自动延长等待时间。*"
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🟢 继续静默等待"},
                            "type": "primary",
                            "value": {"action": "user_choice", "choice": "继续等待", "label": "继续静默等待"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🛑 叫停任务"},
                            "type": "danger",
                            "value": {"action": "user_choice", "choice": "/stop", "label": "叫停任务"}
                        }
                    ]
                },
                _create_footer()
            ]
        }


def build_stall_error_card(user_prompt, think_seconds, stall_seconds):
        """卡死错误卡片：判定任务卡死并终止后推送，带重发 / 切换模型按钮。"""
        clean_prompt = (user_prompt or "重试上条请求")[:80]
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "red",
                "title": {"content": "⚠️ 任务无响应已终止", "tag": "plain_text"}
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"任务已连续 **{stall_seconds // 60} 分钟** 无任何 Token、CPU 计算或日志写入，"
                               "已判定为卡死并自动终止。\n\n**建议操作选项**："
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🔄 重新发送此请求"},
                            "type": "primary",
                            "value": {"action": "user_choice", "choice": clean_prompt, "label": "重新发送请求"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "⚙️ 切换更快的模型"},
                            "type": "default",
                            "value": {"action": "user_choice", "choice": "/model", "label": "打开模型切换面板"}
                        }
                    ]
                },
                _create_footer()
            ]
        }
