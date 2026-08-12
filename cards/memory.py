"""卡片构建域：偏好记忆（阶段 5 第 3 步：自 card_builder.py 迁出）。"""
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
        elements.append(_create_footer())
        
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "purple",
                "title": {"content": "🧠 偏好记忆管理器", "tag": "plain_text"}
            },
            "elements": elements
        }


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
                
        elements.append(_create_footer())
        
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "purple",
                "title": {"content": "🧠 机器人全局记忆库", "tag": "plain_text"}
            },
            "elements": elements
        }
