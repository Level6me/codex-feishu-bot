"""卡片构建域：系统状态与额度（阶段 5 第 3 步：自 card_builder.py 迁出）。"""
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
                _create_footer()
            ]
        }


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
                _create_footer()
            ]
        }


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
        
        elements.append(_create_footer())
        
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"content": "📊 服务器运行状态", "tag": "plain_text"}
            },
            "elements": elements
        }


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

        elements.append(_create_footer())

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
        elements.append(_create_footer())
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "wathet",
                "title": {"content": "🧮 上下文 Token 用量看板", "tag": "plain_text"}
            },
            "elements": elements
        }


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
        elements.append(_create_footer())
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "purple",
                "title": {"content": "🧠 Codex 全局记忆库", "tag": "plain_text"}
            },
            "elements": elements
        }
