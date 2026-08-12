"""卡片构建域：定时任务（阶段 5 第 3 步：自 card_builder.py 迁出）。"""
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

                last_run = _format_ts(t.get('last_run_at'))
                next_run = _format_ts(t.get('next_run_at'))
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

        elements.append(_create_footer())
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "⏱️ 计划任务管理中心 (Cron Center)"},
                "template": "blue",
            },
            "elements": elements,
        }


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
            _create_footer(),
        ]
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"✅ 计划任务创建成功: {t_name}"},
                "template": "green",
            },
            "elements": elements,
        }
