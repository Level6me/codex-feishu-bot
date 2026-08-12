"""飞书 WS 事件入口与卡片动作分发（阶段 5 重构：自 main.py 抽出）。"""
import asyncio
import json
import time

import app_state
from card_builder import CardBuilder, TIER_LABELS
from commands import handle_slash_command, get_system_status_card_data
from config import ALLOWED_USERS, ALLOWED_CHATS
from database import get_session_async, get_profile_async, save_session_async, save_profile_async
from lark_client import send_reply_sdk, send_interactive_card_sdk, patch_interactive_card_sdk, send_card_to_chat_async
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger, P2CardActionTriggerResponse
try:
    # lark-oapi >= 1.7 中消息事件模型位于 api/im/v1
    from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
except ImportError:  # lark-oapi < 1.7 的旧路径
    from lark_oapi.event.callback.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
from logger import log
from utils.auth import (
    allow_message, get_admin_chat_id, get_auth_session, get_role,
    is_bootstrapped, request_access, save_auth_session, try_bootstrap_admin,
    resolve_display_name, is_admin, set_session_role, SCOPE_TIERS,
    start_display_name_refresh,
)
from handlers.messages import (
    handle_message_async, _handle_message_async_internal,
    _handle_guest_message, _admin_welcome, _rate_hint,
)

from handlers.card_actions import (
    _handle_auth_card_action, _handle_cron_card_action, _handle_plugin_panel_action,
)


def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    if not data or not data.event or not data.event.message:
        log.warning("Received malformed message event")
        return
        
    # 最前端 Raw 物理日志打印，百分百捕捉 WebSocket 传入的一切数据包
    log.info(f"[RAW RECEIVE EVENT] message_id={data.event.message.message_id}, message_type={data.event.message.message_type}, content_raw={data.event.message.content}")
    
    message_id = data.event.message.message_id
    chat_id = data.event.message.chat_id
    message_type = data.event.message.message_type
    content_raw = data.event.message.content
    chat_type = data.event.message.chat_type or "p2p"
    sender_open_id = ""
    if data.event.sender and data.event.sender.sender_id:
        sender_open_id = data.event.sender.sender_id.open_id or ""

    create_time = getattr(data.event.message, "create_time", None) or int(time.time())
    if not app_state._mark_seen(message_id, chat_id, create_time):
        log.warning(f"Duplicate message ignored: message_id={message_id}")
        return

    if not isinstance(content_raw, str):
        log.warning(f"Invalid content type received: {type(content_raw)}")
        return
    
    # Check whitelist if configured
    is_allowed = True
    if ALLOWED_USERS or ALLOWED_CHATS:
        is_allowed = False
        sender_id = data.event.sender.sender_id.open_id if data.event.sender and data.event.sender.sender_id else None
        if ALLOWED_USERS and sender_id in ALLOWED_USERS:
            is_allowed = True
        if ALLOWED_CHATS and chat_id in ALLOWED_CHATS:
            is_allowed = True
            
    if not is_allowed:
        log.warning(f"Unauthorized message event ignored. chat_id: {chat_id}, sender_id: {sender_id if 'sender_id' in locals() else None}")
        return

    # Bootstrap: bind the first usable chat as admin (group allowed when
    # AUTH_BOOTSTRAP_ALLOW_GROUP enabled, see utils/auth.py).
    if not is_bootstrapped():
        if try_bootstrap_admin(chat_id, chat_type):
            log.info(f"[auth] Admin bound to chat {chat_id}")
            if app_state.main_loop and app_state.main_loop.is_running():
                asyncio.run_coroutine_threadsafe(_admin_welcome(chat_id), app_state.main_loop)
        else:
            log.info(f"[auth] Bootstrap pending; ignoring message from chat {chat_id}")
            return

    # Permission gate
    role = get_role(chat_id, sender_open_id)
    if role == "banned":
        log.info(f"[auth] Banned chat {chat_id} message ignored")
        return
    if role in ("guest", "pending"):
        if app_state.main_loop and app_state.main_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                _handle_guest_message(chat_id, chat_type, sender_open_id, role, message_type, content_raw),
                app_state.main_loop,
            )
        return

    # Authorized chats: apply rate limiting (admins exempt).
    if role == "user" and not allow_message(chat_id):
        if app_state.main_loop and app_state.main_loop.is_running():
            asyncio.run_coroutine_threadsafe(_rate_hint(chat_id), app_state.main_loop)
        return

    if app_state.main_loop and app_state.main_loop.is_running():
        asyncio.run_coroutine_threadsafe(handle_message_async(message_id, chat_id, message_type, content_raw), app_state.main_loop)
    else:
        log.error("app_state.main_loop is not running!")

def do_p2_card_action_trigger(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    log.info(f"Card action received: {data.event.action.value}")
    
    action_value = data.event.action.value
    chat_id = data.event.context.open_chat_id
    card_message_id = data.event.context.open_message_id

    # ---- Auth domain (admin-only card actions) ----
    _AUTH_ACTIONS = {
        "auth_approve", "auth_deny", "auth_ban",
        "user_action", "user_page", "user_edit", "user_set_tier", "user_edit_cancel",
    }
    if action_value.get("action") in _AUTH_ACTIONS:
        return _handle_auth_card_action(action_value, chat_id, card_message_id)
    
    # Check whitelist if configured
    is_allowed = True
    if ALLOWED_USERS or ALLOWED_CHATS:
        is_allowed = False
        sender_id = data.event.operator.operator_id.open_id if data.event.operator and data.event.operator.operator_id else None
        if ALLOWED_USERS and sender_id in ALLOWED_USERS:
            is_allowed = True
        if ALLOWED_CHATS and chat_id in ALLOWED_CHATS:
            is_allowed = True
            
    if not is_allowed:
        log.warning(f"Unauthorized card action ignored. chat_id: {chat_id}, operator_id: {sender_id if 'sender_id' in locals() else None}")
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "您无权操作此卡片！"}})

    # ---- Cron domain (scheduled-task card actions) ----
    _CRON_ACTIONS = {
        "switch_cron_tab", "open_cron_panel", "open_cron_create",
        "toggle_cron_active", "delete_cron_task", "run_cron_now",
    }
    if action_value.get("action") in _CRON_ACTIONS:
        return _handle_cron_card_action(action_value, chat_id, card_message_id)

    if action_value.get("action") == "switch_model":
        new_model = action_value.get("model")
        
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_switch():
                session_data = await get_session_async(chat_id)
                old_model = session_data.get("codex_model")
                if not old_model:
                    try:
                        from codex_quota import fetch_codex_default_model
                        default_model = await fetch_codex_default_model()
                    except Exception:
                        default_model = ""
                    old_model = default_model or "默认 (CLI 配置)"
                session_data["codex_model"] = new_model
                await save_session_async(chat_id, session_data)
                log.info(f"Switched codex model to {new_model} in chat {chat_id}")
                result_card = CardBuilder.build_model_switch_result_card(new_model, old_model)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, result_card))
            asyncio.run_coroutine_threadsafe(do_switch(), app_state.main_loop)

        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"模型已切换为 {new_model}"}})

    elif action_value.get("action") == "user_choice":
        choice = action_value.get("choice")
        label = action_value.get("label", choice)
        log.info(f"User selected choice: {choice}")
        
        if app_state.main_loop and app_state.main_loop.is_running():
            async def notify_and_process():
                if choice.startswith("/"):
                    # For slash commands, directly call the command handler
                    # Use card_message_id as the reply target
                    session_data = await get_session_async(chat_id)
                    await handle_slash_command(choice, card_message_id, chat_id, session_data, app_state.running_processes, app_state.chat_queues, app_state.chat_workers)
                else:
                    # For regular choices, notify and send to LLM
                    user_display_text = f"✅ **您已选择：{label}**\n*(选项内容已发送给 AI 进行下一步处理...)*"
                    await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, user_display_text))
                    simulated_content = json.dumps({"text": f"我的选择是：{choice}"})
                    await _handle_message_async_internal(card_message_id, chat_id, "text", simulated_content)

            asyncio.run_coroutine_threadsafe(notify_and_process(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已确认：{label[:15]}"}})
        
    elif action_value.get("action") == "browse_dir":
        target_path = action_value.get("path")
        
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_browse_dir():
                session_data = await get_session_async(chat_id)
                recent_projects = session_data.get("recent_projects", [])
                ignored_projects = session_data.get("ignored_projects", [])
                ws_root = session_data.get("workspace_root")
                new_card = CardBuilder.build_dir_browser_card(target_path, recent_projects, workspace_root=ws_root, ignored_projects=ignored_projects)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_browse_dir(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "正在载入目录..."}})
        
    elif action_value.get("action") == "select_project":
        target_path = action_value.get("path")
        
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_select_project():
                session_data = await get_session_async(chat_id)
                session_data["project"] = target_path
                
                # 记录最近使用的项目
                recent = session_data.get("recent_projects", [])
                if target_path in recent:
                    recent.remove(target_path)
                recent.insert(0, target_path)
                session_data["recent_projects"] = recent[:5]
                
                await save_session_async(chat_id, session_data)
                
                success_text = f"📂 **工作区项目切换成功！**\n\n当前已将活跃目录设定为：\n`{target_path}`"
                success_card = CardBuilder.build_ai_response(
                    success_text,
                    current_model=session_data.get('model', 'Default'),
                    current_project=target_path
                )
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(card_message_id, success_card))
            asyncio.run_coroutine_threadsafe(do_select_project(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "项目设定成功！"}})

    elif action_value.get("action") == "remove_project_from_list":
        target_path = action_value.get("path")
        
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_remove_project():
                session_data = await get_session_async(chat_id)
                ignored = session_data.get("ignored_projects", [])
                if target_path not in ignored:
                    ignored.append(target_path)
                session_data["ignored_projects"] = ignored
                
                recent = session_data.get("recent_projects", [])
                if target_path in recent:
                    recent.remove(target_path)
                session_data["recent_projects"] = recent
                
                await save_session_async(chat_id, session_data)
                
                active_project = session_data.get("project", "默认")
                ws_root = session_data.get("workspace_root")
                new_card = CardBuilder.build_dir_browser_card(
                    active_project, 
                    recent, 
                    recent_page=1, 
                    workspace_root=ws_root, 
                    ignored_projects=ignored
                )
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_remove_project(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "项目已成功从列表中移出！"}})
    elif action_value.get("action") == "view_note_detail":
        idx = int(action_value.get("index"))
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_view_note():
                session_data = await get_session_async(chat_id)
                notes = session_data.get("notes", [])
                if 0 <= idx < len(notes):
                    note_content = notes[idx]
                    await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, f"📝 **笔记详情**:\\n{note_content}"))
            asyncio.run_coroutine_threadsafe(do_view_note(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "详情已发送到当前会话！"}})
        
    elif action_value.get("action") == "delete_note":
        idx = int(action_value.get("index"))
        
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_delete_note():
                session_data = await get_session_async(chat_id)
                notes = session_data.get("notes", [])
                if 0 <= idx < len(notes):
                    removed = notes.pop(idx)
                    session_data["notes"] = notes
                    await save_session_async(chat_id, session_data)
                    log.info(f"Removed note: '{removed}' in chat {chat_id}")
                    
                    new_card = CardBuilder.build_note_list_card(notes)
                    await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_delete_note(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已成功删除该条笔记！"}})
        
    elif action_value.get("action") == "clear_notes":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_clear_notes():
                session_data = await get_session_async(chat_id)
                session_data["notes"] = []
                await save_session_async(chat_id, session_data)
                
                new_card = CardBuilder.build_note_list_card([])
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_clear_notes(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "您的记事本已被全部清空！"}})
        
    elif action_value.get("action") == "refresh_status":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_refresh_status():
                from commands import get_system_status_card_data
                cpu, mem_mb, uptime_str, status, restarts, err_logs, git_status, bot_stats = get_system_status_card_data()
                new_card = CardBuilder.build_status_card(cpu, mem_mb, uptime_str, status, restarts, err_logs, git_status, bot_stats)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_refresh_status(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "状态已刷新！"}})

    elif action_value.get("action") == "refresh_quota":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_refresh_quota():
                from codex_quota import fetch_codex_quota
                loop = asyncio.get_running_loop()
                quota_result = await loop.run_in_executor(None, lambda: asyncio.run(fetch_codex_quota()))
                new_card = CardBuilder.build_quota_card(quota_result)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_refresh_quota(), app_state.main_loop)

        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "额度已刷新！"}})

    elif action_value.get("action") == "forget_single_memory":
        idx = int(action_value.get("index"))
        
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_forget():
                memories = await get_profile_async(chat_id)
                if 0 <= idx < len(memories):
                    removed = memories.pop(idx)
                    await save_profile_async(chat_id, memories)
                    log.info(f"Removed memory preference: '{removed}' in chat {chat_id}")
                    
                    new_card = CardBuilder.build_memory_card(memories)
                    await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_forget(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已成功擦除该偏好记录！"}})

    elif action_value.get("action") == "create_project_prompt":
        parent_path = action_value.get("parent_path")
        
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_create_project_prompt():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = "create_project"
                session_data["create_project_parent"] = parent_path
                await save_session_async(chat_id, session_data)
                
                prompt_msg = f"📂 **请输入新建项目的名称，或直接输入项目的 Git 仓库地址**：\n\n*(支持通过 Git URL 克隆；若输入项目名，将在公共根目录 `{parent_path}` 下新建并初始化)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, prompt_msg))
            asyncio.run_coroutine_threadsafe(do_create_project_prompt(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "请输入项目名或Git仓库地址！"}})

    elif action_value.get("action") == "browse_recent_page":
        target_path = action_value.get("current_path")
        target_page = action_value.get("page", 1)
        
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_browse_recent_page():
                session_data = await get_session_async(chat_id)
                recent_projects = session_data.get("recent_projects", [])
                ignored_projects = session_data.get("ignored_projects", [])
                ws_root = session_data.get("workspace_root")
                new_card = CardBuilder.build_dir_browser_card(target_path, recent_projects, target_page, workspace_root=ws_root, ignored_projects=ignored_projects)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_browse_recent_page(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"正在载入第 {target_page} 页项目..."}})

    elif action_value.get("action") == "set_workspace_prompt":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_set_workspace_prompt():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = "project"
                await save_session_async(chat_id, session_data)
                prompt_msg = "📂 **请直接回复一个路径以设置当前活跃开发工作区**：\n\n*(支持 `~` 开头的路径，例如：`~/github/my-project`；路径必须已存在且为目录)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, prompt_msg))
            asyncio.run_coroutine_threadsafe(do_set_workspace_prompt(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "请回复路径以设置开发工作区！"}})

    elif action_value.get("action") == "set_workspace_root_prompt":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_set_workspace_root_prompt():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = "workspace_root"
                await save_session_async(chat_id, session_data)
                prompt_msg = "⚙️ **请直接回复一个路径以设置公共项目根目录**：\n\n*(支持 `~` 开头的路径，例如：`~/projects`；路径必须已存在且为目录，后续新建项目与列表面板将绑定至此根目录)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, prompt_msg))
            asyncio.run_coroutine_threadsafe(do_set_workspace_root_prompt(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "请回复路径以设置公共项目根目录！"}})

    elif action_value.get("action") == "add_note_prompt":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_add_note_prompt():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = "note_add"
                await save_session_async(chat_id, session_data)
                prompt_msg = "📝 **请直接回复笔记内容以添加笔记**：\n\n*(格式：第一个空格前的内容为标题，其余为正文。例如：`购物清单 牛奶、鸡蛋、面包`)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, prompt_msg))
            asyncio.run_coroutine_threadsafe(do_add_note_prompt(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "请回复笔记内容！"}})

    elif action_value.get("action") == "add_memory_prompt":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_add_memory_prompt():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = "remember"
                await save_session_async(chat_id, session_data)
                prompt_msg = "🧠 **请直接回复您想让我长期记住的偏好或设定**：\n\n*(例如：`我开发只用 Python`、`回复请使用中文`)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, prompt_msg))
            asyncio.run_coroutine_threadsafe(do_add_memory_prompt(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "请回复要记住的偏好！"}})

    # ---- Plugin panel / store domain (built-in plugin-management buttons) ----
    _PLUGIN_PANEL_ACTIONS = {
        "switch_plugin_tab", "reload_plugins", "refresh_store_plugins",
        "update_plugin", "uninstall_plugin", "install_github_repo",
        "prompt_install_github", "prompt_add_source", "already_active",
    }
    if action_value.get("action") in _PLUGIN_PANEL_ACTIONS:
        return _handle_plugin_panel_action(action_value, chat_id, card_message_id)

    # ---- Plugin domain (dispatch to loaded plugins, AFTER built-in actions) ----
    from plugin_manager import plugin_manager
    plugin_action = action_value.get("action", "")
    if plugin_action and plugin_manager.plugins:
        if app_state.main_loop and app_state.main_loop.is_running():
            async def _do_plugin_action():
                await plugin_manager.dispatch_card_action(plugin_action, action_value, chat_id, card_message_id)
            asyncio.run_coroutine_threadsafe(_do_plugin_action(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已处理"}})
    
