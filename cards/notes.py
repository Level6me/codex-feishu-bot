"""卡片构建域：备忘录（阶段 5 第 3 步：自 card_builder.py 迁出）。"""
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
            
        elements.append(_create_footer())
        
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {"content": "📔 机器人记事本", "tag": "plain_text"}
            },
            "elements": elements
        }
