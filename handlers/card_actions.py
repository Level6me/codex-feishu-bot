"""卡片动作域处理：权限 / 定时任务 / 插件面板（阶段 5 第 2 步）。"""
import asyncio

import app_state
from card_builder import CardBuilder, TIER_LABELS
from database import get_session_async, save_session_async, get_auth_session
from lark_client import (
    send_reply_sdk, patch_interactive_card_sdk, send_card_to_chat_async,
)
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse
from logger import log
from utils.auth import (
    get_auth_session, is_admin, set_session_role, SCOPE_TIERS,
    start_display_name_refresh,
)


def _refresh_auth_panel(card_message_id):
    """Rebuild and patch the admin management panel card."""
    from database import list_auth_sessions
    from utils.auth import start_display_name_refresh
    sessions = list_auth_sessions()
    task = start_display_name_refresh(sessions)
    try:
        asyncio.run(asyncio.wait_for(asyncio.shield(task), timeout=3.0))
    except Exception:
        pass
    return patch_interactive_card_sdk(card_message_id, CardBuilder.build_user_panel_card(list_auth_sessions()))


def _handle_auth_card_action(action_value, chat_id, card_message_id):
    """Handle admin auth-management card actions. Returns P2CardActionTriggerResponse."""
    from card_builder import TIER_LABELS
    from utils.auth import SCOPE_TIERS, is_admin, set_session_role

    if not is_admin(chat_id):
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "仅管理员可执行此操作。"}})

    action = action_value.get("action")
    target_chat = action_value.get("chat_id", "")

    if action == "auth_approve":
        tier = action_value.get("tier", "basic")
        scopes = list(SCOPE_TIERS.get(tier, SCOPE_TIERS["basic"]))
        tier_label = TIER_LABELS.get(tier, TIER_LABELS["basic"])
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_approve():
                set_session_role(target_chat, "user", scopes, operator=chat_id)
                await send_card_to_chat_async(
                    target_chat,
                    CardBuilder.build_auth_result_card(True, f"✅ 授权成功（{tier_label}）。现在可以使用该机器人了。"),
                )
                await asyncio.get_running_loop().run_in_executor(None, lambda: _refresh_auth_panel(card_message_id))
            asyncio.run_coroutine_threadsafe(do_approve(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已授权（{tier_label}）"}})

    if action == "auth_deny":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_deny():
                set_session_role(target_chat, "guest", [], operator=chat_id)
                await send_card_to_chat_async(
                    target_chat,
                    CardBuilder.build_auth_result_card(False, "您的权限申请已被管理员拒绝。"),
                )
                await asyncio.get_running_loop().run_in_executor(None, lambda: _refresh_auth_panel(card_message_id))
            asyncio.run_coroutine_threadsafe(do_deny(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已拒绝该申请"}})

    if action == "auth_ban":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_ban():
                set_session_role(target_chat, "banned", [], operator=chat_id)
                await send_card_to_chat_async(
                    target_chat,
                    CardBuilder.build_auth_result_card(False, "该会话已被管理员加入黑名单。"),
                )
                await asyncio.get_running_loop().run_in_executor(None, lambda: _refresh_auth_panel(card_message_id))
            asyncio.run_coroutine_threadsafe(do_ban(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已拉黑该会话"}})

    if action == "user_action":
        op = action_value.get("op", "")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_user_action():
                if op == "revoke":
                    set_session_role(target_chat, "guest", [], operator=chat_id)
                elif op == "promote":
                    set_session_role(target_chat, "admin", [], operator=chat_id)
                elif op == "unban":
                    set_session_role(target_chat, "guest", [], operator=chat_id)
                await asyncio.get_running_loop().run_in_executor(None, lambda: _refresh_auth_panel(card_message_id))
            asyncio.run_coroutine_threadsafe(do_user_action(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "操作成功"}})

    if action == "user_edit":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_edit():
                sess = get_auth_session(target_chat) or {"chat_id": target_chat}
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: patch_interactive_card_sdk(card_message_id, CardBuilder.build_user_edit_card(sess)),
                )
            asyncio.run_coroutine_threadsafe(do_edit(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "正在编辑该会话权限"}})

    if action == "user_set_tier":
        tier = action_value.get("tier", "basic")
        tier_label = TIER_LABELS.get(tier, TIER_LABELS["basic"])
        scopes = list(SCOPE_TIERS.get(tier, SCOPE_TIERS["basic"]))
        set_session_role(target_chat, "user", scopes, operator=chat_id)
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_set_tier():
                await asyncio.get_running_loop().run_in_executor(None, lambda: _refresh_auth_panel(card_message_id))
            asyncio.run_coroutine_threadsafe(do_set_tier(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已设为{tier_label}"}})

    if action == "user_edit_cancel":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_cancel_edit():
                await asyncio.get_running_loop().run_in_executor(None, lambda: _refresh_auth_panel(card_message_id))
            asyncio.run_coroutine_threadsafe(do_cancel_edit(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已取消编辑"}})

    if action == "user_page":
        page = int(action_value.get("page", 1))
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_user_page():
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: patch_interactive_card_sdk(
                        card_message_id,
                        CardBuilder.build_user_panel_card(list_auth_sessions(), page=page),
                    ),
                )
            asyncio.run_coroutine_threadsafe(do_user_page(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已翻页"}})

    return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "未知操作"}})


def _handle_cron_card_action(action_value, chat_id, card_message_id):
    """Handle scheduled-task (cron) card actions."""
    from database import get_all_cron_tasks, get_cron_task, update_cron_task_status, delete_cron_task
    from cron_engine import cron_engine

    action = action_value.get("action")

    if action == "switch_cron_tab":
        tab = action_value.get("tab", "user")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_switch_cron():
                tasks = await asyncio.get_running_loop().run_in_executor(None, lambda: get_all_cron_tasks(chat_id))
                session_data = await get_session_async(chat_id)
                new_card = CardBuilder.build_cron_panel_card(tasks, active_tab=tab, session_data=session_data)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_switch_cron(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已切换至 {'用户' if tab == 'user' else '系统'} 任务面板"}})

    if action == "open_cron_panel":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_open_cron():
                from lark_client import send_card_to_chat_sdk
                tasks = await asyncio.get_running_loop().run_in_executor(None, lambda: get_all_cron_tasks(chat_id))
                session_data = await get_session_async(chat_id)
                new_card = CardBuilder.build_cron_panel_card(tasks, active_tab="user", session_data=session_data)
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_card_to_chat_sdk(chat_id, new_card))
            asyncio.run_coroutine_threadsafe(do_open_cron(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "正在打开计划任务中心..."}})

    if action == "open_cron_create":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_open_create():
                from commands import PendingCommand
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = PendingCommand.CRON_ADD.value
                await save_session_async(chat_id, session_data)

                msg = (
                    "⏱️ **新建计划任务**\n\n"
                    "请直接在此回复 3 段信息，中间用竖线 `|` 隔开：\n"
                    "`任务名称 | 触发规则(Cron表达式/秒数) | 执行 Prompt`\n\n"
                    "📌 **示例 1 (标准 Cron 每天 09:00 执行)**：\n"
                    "`每日总结 | 0 9 * * * | 检查当前工作区的 Git 提交并生成日报`\n\n"
                    "📌 **示例 2 (倒计时 10 分钟后一次性执行)**：\n"
                    "`磁盘压测汇报 | 600s | 提取 /tmp/iscsi_stab_test.log 并分析报告`"
                )
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, msg))
            asyncio.run_coroutine_threadsafe(do_open_create(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请发送格式为 '名称 | 规则 | Prompt' 的任务文本"}})

    if action == "toggle_cron_active":
        task_id = action_value.get("task_id")
        is_active = bool(action_value.get("is_active", True))
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_toggle_cron():
                await asyncio.get_running_loop().run_in_executor(None, lambda: update_cron_task_status(task_id, is_active))
                tasks = await asyncio.get_running_loop().run_in_executor(None, lambda: get_all_cron_tasks(chat_id))
                session_data = await get_session_async(chat_id)
                new_card = CardBuilder.build_cron_panel_card(tasks, active_tab="user", session_data=session_data)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_toggle_cron(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"任务已{'启用' if is_active else '暂停'}"}})

    if action == "delete_cron_task":
        task_id = action_value.get("task_id")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_delete_cron():
                await asyncio.get_running_loop().run_in_executor(None, lambda: delete_cron_task(task_id))
                tasks = await asyncio.get_running_loop().run_in_executor(None, lambda: get_all_cron_tasks(chat_id))
                session_data = await get_session_async(chat_id)
                new_card = CardBuilder.build_cron_panel_card(tasks, active_tab="user", session_data=session_data)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_delete_cron(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "计划任务已物理删除！"}})

    if action == "run_cron_now":
        task_id = action_value.get("task_id")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_run_now():
                task = await asyncio.get_running_loop().run_in_executor(None, lambda: get_cron_task(task_id))
                if task:
                    asyncio.create_task(cron_engine._run_task_wrapper(task))
            asyncio.run_coroutine_threadsafe(do_run_now(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已触发即刻运行计划任务！"}})

    return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "未知操作"}})


def _handle_plugin_panel_action(action_value, chat_id, card_message_id):
    """Handle plugin-panel / plugin-store card actions (built-in plugin management)."""
    from plugin_manager import plugin_manager

    action = action_value.get("action")

    def _refresh_panel(tab="installed"):
        return patch_interactive_card_sdk(
            card_message_id,
            CardBuilder.build_plugin_panel_card(plugin_manager.get_plugin_list(), active_tab=tab),
        )

    if action == "switch_plugin_tab":
        tab = "sources" if action_value.get("tab") == "sources" else "installed"
        if app_state.main_loop and app_state.main_loop.is_running():
            async def _do_switch_tab():
                await asyncio.get_running_loop().run_in_executor(None, lambda: _refresh_panel(tab))
            asyncio.run_coroutine_threadsafe(_do_switch_tab(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已切换至插件源" if tab == "sources" else "已切换至已安装插件"}})

    if action == "reload_plugins":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def _do_reload():
                plugin_manager.reload_plugins()
                await asyncio.get_running_loop().run_in_executor(None, lambda: _refresh_panel("installed"))
            asyncio.run_coroutine_threadsafe(_do_reload(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "插件库已热重载"}})

    if action == "refresh_store_plugins":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def _do_refresh_store():
                try:
                    from plugin_store import fetch_remote_store_plugins
                    fetch_remote_store_plugins(force_refresh=True)
                except Exception as e:
                    log.warning(f"refresh_store_plugins failed: {e}")
                await asyncio.get_running_loop().run_in_executor(None, lambda: _refresh_panel("sources"))
            asyncio.run_coroutine_threadsafe(_do_refresh_store(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已刷新插件商店"}})

    if action == "update_plugin":
        plugin_id = action_value.get("plugin_id", "")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def _do_update():
                from plugin_store import update_plugin
                ok, msg = await asyncio.get_running_loop().run_in_executor(None, lambda: update_plugin(plugin_id))
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, ("✅ " if ok else "❌ ") + msg))
                plugin_manager.reload_plugins()
            asyncio.run_coroutine_threadsafe(_do_update(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "正在检查插件更新..."}})

    if action == "uninstall_plugin":
        plugin_id = action_value.get("plugin_id", "")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def _do_uninstall():
                from plugin_store import uninstall_plugin
                ok, msg = await asyncio.get_running_loop().run_in_executor(None, lambda: uninstall_plugin(plugin_id))
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, ("✅ " if ok else "❌ ") + msg))
                plugin_manager.reload_plugins()
                await asyncio.get_running_loop().run_in_executor(None, lambda: _refresh_panel("installed"))
            asyncio.run_coroutine_threadsafe(_do_uninstall(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "正在卸载插件..."}})

    if action == "install_github_repo":
        repo_url = action_value.get("repo_url", "")
        custom_id = action_value.get("plugin_id", "")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def _do_install():
                from plugin_store import install_plugin_from_github
                ok, msg = await asyncio.get_running_loop().run_in_executor(None, lambda: install_plugin_from_github(repo_url, custom_id))
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, ("✅ " if ok else "❌ ") + msg))
                plugin_manager.reload_plugins()
                await asyncio.get_running_loop().run_in_executor(None, lambda: _refresh_panel("sources"))
            asyncio.run_coroutine_threadsafe(_do_install(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "正在从 GitHub 安装插件..."}})

    if action == "prompt_install_github":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def _do_prompt_install():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = "plugin_install"
                await save_session_async(chat_id, session_data)
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: send_reply_sdk(
                        card_message_id,
                        "📥 **请输入要安装插件的 GitHub 仓库地址**\n\n*(例如：`https://github.com/xxx/plugin-repo`)*",
                    ),
                )
            asyncio.run_coroutine_threadsafe(_do_prompt_install(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请发送 GitHub 仓库地址"}})

    if action == "prompt_add_source":
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "当前版本使用默认官方插件源，暂不支持自定义源"}})

    if action == "already_active":
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "该插件已激活"}})

    return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "未知插件操作"}})


