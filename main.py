import asyncio
import json
import os
import re
import sys
import signal

# Add local bin paths to PATH
home = os.path.expanduser("~")
os.environ["PATH"] += os.pathsep + os.path.join(home, ".npm-global/bin") + os.pathsep + os.path.join(home, ".local/bin")


import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger, P2CardActionTriggerResponse

from config import APP_ID, APP_SECRET, SESSION_FILE, PROFILE_FILE, CODEX_BIN, ALLOWED_USERS, ALLOWED_CHATS, BASE_DIR, AGENT_BACKEND
from database import get_session_async, get_profile_async, save_session_async, get_session_sync, save_session_sync, save_profile_async
from multimodal import extract_and_upload_resources
from lark_client import (
    api_client, send_reply_sdk, send_interactive_card_sdk, patch_interactive_card_sdk,
    download_message_resource_sdk, set_emoji_sdk, delete_emoji_sdk,
    send_card_to_chat_async, send_text_to_chat_async,
)
from commands import handle_slash_command
from logger import log
from card_builder import CardBuilder
from utils.auth import (
    allow_message,
    get_admin_chat_id,
    get_auth_session,
    get_role,
    is_bootstrapped,
    request_access,
    save_auth_session,
    try_bootstrap_admin,
    resolve_display_name,
)
import stats
from agent_executor import execute_agent
from garbage_collection import garbage_collector
import time

main_loop = None
running_processes = {}
chat_queues = {}
chat_workers = {}
chat_media_batches = {}

_SEEN_MESSAGE_IDS = {}
_SEEN_MESSAGE_IDS_MAX = 1000


def _mark_seen(message_id):
    if message_id in _SEEN_MESSAGE_IDS:
        return False
    if len(_SEEN_MESSAGE_IDS) >= _SEEN_MESSAGE_IDS_MAX:
        oldest = next(iter(_SEEN_MESSAGE_IDS))
        del _SEEN_MESSAGE_IDS[oldest]
    _SEEN_MESSAGE_IDS[message_id] = True
    return True

async def process_chat_queue(chat_id):
    queue = chat_queues[chat_id]
    try:
        while not queue.empty():
            task = await queue.get()
            try:
                await _process_single_task(chat_id, task)
                stats.record_success()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                stats.record_failure()
                log.error(f"Error processing queued task for {chat_id}: {e}")
            finally:
                queue.task_done()
    except asyncio.CancelledError:
        log.info(f"Chat worker for {chat_id} was cancelled by /stop")
    finally:
        chat_workers.pop(chat_id, None)
        if chat_id in chat_queues and chat_queues[chat_id].empty():
            chat_queues.pop(chat_id, None)

async def _process_image_message(loop, message_id, content_json, content_raw):
    image_key = content_json.get("image_key", "")
    if not image_key:
        match = re.search(r'img_[a-zA-Z0-9_\-]+', content_raw)
        if match:
            image_key = match.group(0)

    if not image_key and content_raw.startswith("[Image: ") and content_raw.endswith("]"):
        image_key = content_raw[8:-1]
    
    bot_reply_msg_id = None
    if image_key:
        os.makedirs("downloads", exist_ok=True)
        output_filename = f"downloads/img_{image_key}.jpg"
        
        dl_card = CardBuilder.build_download_indicator(os.path.basename(output_filename), "图片")
        bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))
        
        output_path = os.path.abspath(output_filename)
        success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(message_id, image_key, "image", output_path))
        
        image_paths = [output_path] if success else []
        return f"请查看这张图片并做出回应。图片路径: {output_path}", os.path.basename(output_filename), success, bot_reply_msg_id, image_paths
    else:
        return "[未获取到图片]", None, True, None, []

async def _process_post_message(loop, message_id, content_json):
    texts = []
    image_keys = []
    for line in content_json.get("content", []):
        for elem in line:
            if elem.get("tag") == "text":
                texts.append(elem.get("text", ""))
            elif elem.get("tag") == "img":
                image_keys.append(elem.get("image_key", ""))
    
    user_text = " ".join(texts)
    bot_reply_msg_id = None
    downloaded_file_name = None
    download_success = True
    image_paths = []
    
    if image_keys:
        image_key = image_keys[0]
        os.makedirs("downloads", exist_ok=True)
        output_filename = f"downloads/img_{image_key}.jpg"
        
        dl_card = CardBuilder.build_download_indicator("图片内容")
        bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))
        
        output_path = os.path.abspath(output_filename)
        download_success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(message_id, image_key, "image", output_path))
        
        downloaded_file_name = os.path.basename(output_filename)
        user_text += f"\n[附加图片路径: {output_path}]"
        if download_success:
            image_paths.append(output_path)
        
    return user_text, downloaded_file_name, download_success, bot_reply_msg_id, image_paths

async def _process_link_message(content_json):
    if isinstance(content_json, dict):
        user_text = content_json.get("url", content_json.get("href", ""))
    else:
        user_text = str(content_json)
    return user_text, None, True, None

async def _process_file_audio_media_message(loop, message_id, message_type, content_json):
    file_key = content_json.get("file_key", "")
    file_name = content_json.get("file_name", "")
    bot_reply_msg_id = None
    download_success = True
    downloaded_file_name = None
    user_text = ""
    
    if file_key:
        if not file_name:
            if message_type == "audio":
                file_name = f"audio_{file_key}.ogg"
            elif message_type == "media":
                file_name = f"video_{file_key}.mp4"
            else:
                file_name = f"file_{file_key}"
        
        if message_type == "media" and not file_name.lower().endswith(".mp4"):
            file_name = file_key + ".mp4"
        if message_type == "audio" and "." not in file_name:
            file_name = file_key + ".ogg"
        
        # Purify file_name to prevent directory traversal
        file_name = os.path.basename(file_name)
        
        os.makedirs("downloads", exist_ok=True)
        output_filename = os.path.join("downloads", file_name)
        dl_card = CardBuilder.build_download_indicator(file_name, message_type)
        bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))

        output_path = os.path.abspath(output_filename)
        download_success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(message_id, file_key, "file", output_path))
        
        downloaded_file_name = file_name
        
        if message_type == "file":
            user_text = f"请详细阅读这份文件（{file_name}），并做出响应。文件路径: {output_path}"
        elif message_type == "audio":
            user_text = f"请仔细听这段语音内容（语音文件路径: {output_path}），并做出响应。"
        elif message_type == "media":
            user_text = f"请仔细观看这段视频内容（视频文件路径: {output_path}），并做出响应。"
            
    return user_text, downloaded_file_name, download_success, bot_reply_msg_id

async def _process_batch_media_message(loop, message_id, content_json):
    items = content_json.get("items", [])
    media_hints = []
    download_success = True
    image_paths = []
    
    # 批量下发资源加载指示器
    dl_card = CardBuilder.build_download_indicator(f"合并批处理 ({len(items)} 个文件)", "多媒体组")
    bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))
    
    os.makedirs("downloads", exist_ok=True)
    
    for idx, item in enumerate(items):
        m_type = item["message_type"]
        c_json = item["content_json"]
        c_raw = item["content_raw"]
        
        file_key = ""
        file_name = ""
        if m_type == "image":
            file_key = c_json.get("image_key", "")
            if not file_key:
                match = re.search(r'img_[a-zA-Z0-9_\-]+', c_raw)
                if match:
                    file_key = match.group(0)
            file_name = f"batch_img_{idx}_{file_key}.jpg"
        else:
            file_key = c_json.get("file_key", "")
            file_name = c_json.get("file_name", f"batch_file_{idx}_{file_key}")
            file_name = os.path.basename(file_name)
            
        if file_key:
            output_path = os.path.abspath(os.path.join("downloads", file_name))
            success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(item["message_id"], file_key, "image" if m_type == "image" else "file", output_path))
            if success:
                media_hints.append(f"{idx+1}. 多模态 {m_type.upper()} 文件路径: `{output_path}`")
                if m_type == "image":
                    image_paths.append(output_path)
            else:
                download_success = False
                media_hints.append(f"{idx+1}. 多模态 {m_type.upper()} 文件 `{file_name}` (下载失败)")
                
    user_text = f"请查看以下 {len(items)} 个关联多模态文件并做出综合关联回应：\n\n" + "\n".join(media_hints)
    downloaded_file_name = f"合并批处理 ({len(items)} 个文件)"
    return user_text, downloaded_file_name, download_success, bot_reply_msg_id, image_paths

async def _process_single_task(chat_id, task):
    message_id = task["message_id"]
    message_type = task["message_type"]
    content_json = task["content_json"]
    content_raw = task["content_raw"]
    raw_text = task["raw_text"]
    
    loop = asyncio.get_running_loop()
    session_data = await get_session_async(chat_id)
    
    # 首次部署成功后的欢迎引导消息推送
    if not session_data.get("welcome_sent"):
        session_data["welcome_sent"] = True
        await save_session_async(chat_id, session_data)
        welcome_card = CardBuilder.build_welcome_card()
        await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, welcome_card))
        
    downloaded_file_name = None
    download_success = True
    bot_reply_msg_id = None
    image_paths = []

    if message_type == "text":
        user_text = raw_text
    elif message_type == "image":
        user_text, downloaded_file_name, download_success, bot_reply_msg_id, image_paths = await _process_image_message(loop, message_id, content_json, content_raw)
    elif message_type == "post":
        user_text, downloaded_file_name, download_success, bot_reply_msg_id, image_paths = await _process_post_message(loop, message_id, content_json)
    elif message_type == "link":
        user_text, downloaded_file_name, download_success, bot_reply_msg_id = await _process_link_message(content_json)
    elif message_type in ["file", "audio", "media"]:
        user_text, downloaded_file_name, download_success, bot_reply_msg_id = await _process_file_audio_media_message(loop, message_id, message_type, content_json)
    elif message_type == "batch_media":
        user_text, downloaded_file_name, download_success, bot_reply_msg_id, image_paths = await _process_batch_media_message(loop, message_id, content_json)
    else:
        user_text = f"[暂不支持的消息类型: {message_type}]"

    if not user_text:
        return

    # 方案二：安全沙箱前置命令高危扫描过滤
    dangerous_patterns = [
        r"\brm\s+-rf\b",
        r"\bchmod\s+-(R\s+)?777\b",
        r"\bdd\s+if=\b",
        r"\bmkfs\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bpoweroff\b",
        r":\(\){\s*:\s*\|\s*:\s*&\s*}\s*;\s*:"
    ]
    is_dangerous = False
    for pattern in dangerous_patterns:
        if re.search(pattern, user_text, re.IGNORECASE):
            is_dangerous = True
            break
            
    if is_dangerous:
        warn_card = CardBuilder.build_security_warning(user_text)
        await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, warn_card))
        return

    # Sessions ar    # Inject protocol into prompt
    current_proj = session_data.get("project", "默认")
    system_instruction = f"[System Rule: MUST ALWAYS communicate, reply, explain, and write responses in Simplified Chinese (简体中文). Any English text in the response must be limited to code syntax or technical names only. If you need the user to make a choice, format your options inside [CHOICE_CARD] Q: <Question> \n - <Option1> \n - <Option2> [/CHOICE_CARD] tags. NEVER ask normal text multi-choice questions. ONLY output plain text choices, avoid complex formatting inside choices.]\n\n"
    
    # 注入长任务执行规范：避免 30 秒截断 + write_stdin 轮询导致停滞检测误杀
    system_instruction += "[Agent 执行规范]\n- 执行可能超过 30 秒的长命令（如 scp、ssh、cargo build、编译部署、git push 大仓库等）时，请在 exec_command 中直接设置足够的 yield_time_ms（建议 600000），一次性等待命令完成。\n- 不要依赖 exec_command 30 秒截断返回 session_id 后用 write_stdin 反复轮询等待（write_stdin 等待期间无 stdout 事件，超过 600 秒会被停滞检测误杀）。\n- 若命令确实需要后台长时间运行，请先向用户发送一条说明消息，再启动命令，并在下次任务中检查结果。\n\n"
    
    # 注入当前活跃项目环境参数
    system_instruction += f"[System Active Project Context]\n- Current active project workspace path is: {current_proj}\n- All file reads, writes, and analysis commands you execute should target this active workspace directory.\n\n"
    
    # 注入该项目专属 Prompt
    project_prompts = session_data.get("project_prompts", {})
    if current_proj in project_prompts and project_prompts[current_proj]:
        proj_prompt_text = project_prompts[current_proj]
        system_instruction += f"[Active Project Specific Rules & Description]\n{proj_prompt_text}\n\n"
        
    # 注入用户备忘录 Notes
    notes = session_data.get("notes", [])
    if notes:
        notes_block = "\n".join([f"- {note}" for note in notes])
        system_instruction += f"[User's Permanent Notes / 备忘录]\n{notes_block}\n\n"
    
    # Load long-term memory if this is a new conversation
    # Run plugin on_before_ai hooks (plugins may modify prompt / session_data)
    from plugin_manager import plugin_manager
    if plugin_manager.plugins:
        try:
            user_text, session_data = await plugin_manager.dispatch_before_ai(user_text, chat_id, session_data)
        except Exception as e:
            log.warning(f"dispatch_before_ai failed: {e}")

    final_prompt = user_text
    is_new_conversation = not session_data.get("codex_conversation" if AGENT_BACKEND == "codex" else "conversation")
    if is_new_conversation:
        memories = await get_profile_async(chat_id)
        if memories:
            memory_block = "\n".join([f"- {m}" for m in memories])
            final_prompt = f"[System Context: Please strictly follow the user's permanent preferences below:]\n{memory_block}\n\n[User's Message:]\n{user_text}"
            
    # Delegate execution to executor
    try:
        is_error = await asyncio.wait_for(
            execute_agent(
                chat_id, user_text, message_id, bot_reply_msg_id, session_data,
                is_new_conversation, system_instruction, final_prompt, downloaded_file_name,
                download_success, running_processes, image_paths=image_paths,
                role=get_role(chat_id),
            ),
            timeout=2000,
        )
    except asyncio.TimeoutError:
        log.error(f"execute_agent timed out after 2000s for chat {chat_id}")
        is_error = True
    
    if is_error:
        await set_emoji(message_id, "CrossMark")
    else:
        await set_emoji(message_id, "DONE")




async def set_emoji(message_id, emoji_type):
    # Map custom / obsolete emojis to standard Lark emoji names
    mapping = {
        "StatusReading": "Typing",
        "CrossMark": "CrossMark",
        "DONE": "DONE"
    }
    mapped_type = mapping.get(emoji_type, emoji_type)
    
    loop = asyncio.get_running_loop()
    try:
        reaction_id = await loop.run_in_executor(None, lambda: set_emoji_sdk(message_id, mapped_type))
        return reaction_id
    except Exception as e:
        log.error(f"Failed to set emoji reaction {emoji_type}: {e}")
        return None

async def delete_emoji(message_id, reaction_id):
    if not reaction_id:
        return
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, lambda: delete_emoji_sdk(message_id, reaction_id))
    except Exception as e:
        log.error(f"Failed to delete emoji reaction: {e}")

# emoji_spinner removed


async def handle_message_async(message_id, chat_id, message_type, content_raw):
    try:
        stats.record_request()
        await _handle_message_async_internal(message_id, chat_id, message_type, content_raw)
    except Exception as e:
        stats.record_failure()
        import traceback
        log.error(f"[FATAL ERROR in handle_message_async]: {e}")
        traceback.print_exc()

async def _handle_message_async_internal(message_id, chat_id, message_type, content_raw):
    loop = asyncio.get_running_loop()
    bot_reply_msg_id = None

    try:
        content_json = json.loads(content_raw)
    except Exception as e:
        log.error(f"Failed to parse content_raw JSON: {e}")
        return

    # Quick parsing for slash commands
    raw_text = ""
    if message_type == "text":
        if isinstance(content_json, dict):
            raw_text = content_json.get("text", "") if content_json.get("text") else content_raw
        else:
            raw_text = str(content_json)
        raw_text = raw_text.strip()
    elif message_type == "post":
        # 兼容飞书将 URL 或富文本转换为 post 的行为，优先抽取 a 标签的 href 真实的 URL，避免友好文本屏蔽 URL
        texts = []
        if isinstance(content_json, dict):
            for line in content_json.get("content", []):
                for elem in line:
                    if elem.get("tag") == "text":
                        texts.append(elem.get("text", ""))
                    elif elem.get("tag") == "a":
                        texts.append(elem.get("href", elem.get("text", "")))
        raw_text = " ".join(texts).strip()
    elif message_type == "link":
        if isinstance(content_json, dict):
            raw_text = content_json.get("url", content_json.get("href", ""))
        else:
            raw_text = str(content_json)
        raw_text = raw_text.strip()

    # Load sessions early for slash commands
    session_data = await get_session_async(chat_id)
    log.info(f"Message received: chat_id={chat_id}, message_type={message_type}, raw_text='{raw_text}', pending_command='{session_data.get('pending_command')}'")

    # Handle slash commands first (this allows /stop to bypass the lock)
    if session_data.get("pending_command") or (raw_text.startswith("/") and message_type in ["text", "post", "link"]):
        handled, override_text = await handle_slash_command(raw_text, message_id, chat_id, session_data, running_processes, chat_queues, chat_workers)
        if handled:
            stats.record_success()
            return
        if override_text:
            raw_text = override_text

    # 辅助任务分发函数
    async def dispatch_task(c_id, msg_id, m_type, c_json, c_raw, r_text):
        if c_id not in chat_queues:
            chat_queues[c_id] = asyncio.Queue()
            
        task_payload = {
            "message_id": msg_id,
            "message_type": m_type,
            "content_json": c_json,
            "content_raw": c_raw,
            "raw_text": r_text
        }
        
        if c_id in chat_workers and not chat_workers[c_id].done():
            qsize = chat_queues[c_id].qsize()
            warning_msg = f"⏳ 收到！当前有任务正在执行，该请求已加入队列排队处理 (前方还有 {qsize + 1} 个任务)..."
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(msg_id, warning_msg))
            await chat_queues[c_id].put(task_payload)
        else:
            await chat_queues[c_id].put(task_payload)
            chat_workers[c_id] = asyncio.create_task(process_chat_queue(c_id))

    # 方案四：多模态多图合并批处理防抖机制
    if message_type in ["image", "file", "audio", "media"]:
        if chat_id not in chat_media_batches:
            chat_media_batches[chat_id] = {
                "items": [],
                "timer_task": None
            }
            
        batch = chat_media_batches[chat_id]
        batch["items"].append({
            "message_id": message_id,
            "message_type": message_type,
            "content_json": content_json,
            "content_raw": content_raw
        })
        
        if batch["timer_task"] and not batch["timer_task"].done():
            batch["timer_task"].cancel()
            
        async def delay_dispatch():
            try:
                # Dynamic debounce: 1.5s + 0.1s per item, up to 3.0s max
                delay = min(1.5 + len(batch["items"]) * 0.1, 3.0)
                await asyncio.sleep(delay)
                items = batch["items"]
                chat_media_batches.pop(chat_id, None)
                
                if len(items) == 1:
                    single = items[0]
                    await dispatch_task(
                        chat_id, single["message_id"], single["message_type"], 
                        single["content_json"], single["content_raw"], raw_text
                    )
                else:
                    target_msg_id = items[-1]["message_id"]
                    await dispatch_task(
                        chat_id, target_msg_id, "batch_media", 
                        {"items": items}, "", ""
                    )
            except asyncio.CancelledError:
                pass
                
        batch["timer_task"] = asyncio.create_task(delay_dispatch())
        return
        
    # 普通非媒体消息直接分发
    await dispatch_task(chat_id, message_id, message_type, content_json, content_raw, raw_text)


def _extract_text(message_type, content_raw):
    if message_type == "text" and isinstance(content_raw, str):
        try:
            parsed = json.loads(content_raw)
            if isinstance(parsed, dict):
                return (parsed.get("text") or "").strip()
        except Exception:
            pass
    return ""


async def _handle_auth_request(chat_id, chat_type, sender_open_id, message_text):
    """Guest /auth flow: persist request, resolve name, notify admin."""
    status = request_access(chat_id, chat_type, sender_open_id, message_text)
    if status == "ok":
        display = await resolve_display_name(chat_id, chat_type, sender_open_id)
        if display:
            sess = get_auth_session(chat_id) or {}
            sess["display_name"] = display
            save_auth_session(sess)

        admin_id = get_admin_chat_id()
        if admin_id:
            sess = get_auth_session(chat_id) or {}
            await send_card_to_chat_async(admin_id, CardBuilder.build_auth_request_card(sess))
            log.info(f"[auth] access request from {chat_id} notified admin {admin_id}")
        await send_text_to_chat_async(chat_id, "📨 已向管理员发送授权申请，请等待审批。")
    elif status == "rate":
        await send_text_to_chat_async(chat_id, "⏳ 申请过于频繁，请 10 分钟后再试。")
    elif status == "already":
        await send_text_to_chat_async(chat_id, "✅ 当前会话已授权，无需重复申请。")
    # admin / banned: 静默


async def _handle_guest_message(chat_id, chat_type, sender_open_id, role, message_type, content_raw):
    """Silent mode for guests/pending chats:
    - /auth triggers an access request
    - pending chats stay fully silent while awaiting approval
    - guests get a one-time hint per 24h, then silent"""
    text = _extract_text(message_type, content_raw)
    if text.startswith("/auth"):
        await _handle_auth_request(chat_id, chat_type, sender_open_id, text)
        return
    if role == "pending":
        return

    now = int(time.time())
    sess = get_auth_session(chat_id) or {}
    last_hint = sess.get("last_hint_at") or 0
    if now - last_hint >= 86400:
        sess["chat_id"] = chat_id
        sess["chat_type"] = chat_type
        sess["sender_open_id"] = sender_open_id
        sess["last_hint_at"] = now
        sess["updated_at"] = now
        save_auth_session(sess)
        await send_card_to_chat_async(chat_id, CardBuilder.build_auth_hint_card())


async def _admin_welcome(chat_id):
    await send_card_to_chat_async(chat_id, CardBuilder.build_admin_welcome_card())


async def _rate_hint(chat_id):
    await send_card_to_chat_async(chat_id, CardBuilder.build_rate_limit_card())


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
        if main_loop and main_loop.is_running():
            async def do_approve():
                set_session_role(target_chat, "user", scopes, operator=chat_id)
                await send_card_to_chat_async(
                    target_chat,
                    CardBuilder.build_auth_result_card(True, f"✅ 授权成功（{tier_label}）。现在可以使用该机器人了。"),
                )
                await asyncio.get_running_loop().run_in_executor(None, lambda: _refresh_auth_panel(card_message_id))
            asyncio.run_coroutine_threadsafe(do_approve(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已授权（{tier_label}）"}})

    if action == "auth_deny":
        if main_loop and main_loop.is_running():
            async def do_deny():
                set_session_role(target_chat, "guest", [], operator=chat_id)
                await send_card_to_chat_async(
                    target_chat,
                    CardBuilder.build_auth_result_card(False, "您的权限申请已被管理员拒绝。"),
                )
                await asyncio.get_running_loop().run_in_executor(None, lambda: _refresh_auth_panel(card_message_id))
            asyncio.run_coroutine_threadsafe(do_deny(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已拒绝该申请"}})

    if action == "auth_ban":
        if main_loop and main_loop.is_running():
            async def do_ban():
                set_session_role(target_chat, "banned", [], operator=chat_id)
                await send_card_to_chat_async(
                    target_chat,
                    CardBuilder.build_auth_result_card(False, "该会话已被管理员加入黑名单。"),
                )
                await asyncio.get_running_loop().run_in_executor(None, lambda: _refresh_auth_panel(card_message_id))
            asyncio.run_coroutine_threadsafe(do_ban(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已拉黑该会话"}})

    if action == "user_action":
        op = action_value.get("op", "")
        if main_loop and main_loop.is_running():
            async def do_user_action():
                if op == "revoke":
                    set_session_role(target_chat, "guest", [], operator=chat_id)
                elif op == "promote":
                    set_session_role(target_chat, "admin", [], operator=chat_id)
                elif op == "unban":
                    set_session_role(target_chat, "guest", [], operator=chat_id)
                await asyncio.get_running_loop().run_in_executor(None, lambda: _refresh_auth_panel(card_message_id))
            asyncio.run_coroutine_threadsafe(do_user_action(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "操作成功"}})

    if action == "user_edit":
        if main_loop and main_loop.is_running():
            async def do_edit():
                sess = get_auth_session(target_chat) or {"chat_id": target_chat}
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: patch_interactive_card_sdk(card_message_id, CardBuilder.build_user_edit_card(sess)),
                )
            asyncio.run_coroutine_threadsafe(do_edit(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "正在编辑该会话权限"}})

    if action == "user_set_tier":
        tier = action_value.get("tier", "basic")
        tier_label = TIER_LABELS.get(tier, TIER_LABELS["basic"])
        scopes = list(SCOPE_TIERS.get(tier, SCOPE_TIERS["basic"]))
        set_session_role(target_chat, "user", scopes, operator=chat_id)
        if main_loop and main_loop.is_running():
            async def do_set_tier():
                await asyncio.get_running_loop().run_in_executor(None, lambda: _refresh_auth_panel(card_message_id))
            asyncio.run_coroutine_threadsafe(do_set_tier(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已设为{tier_label}"}})

    if action == "user_edit_cancel":
        if main_loop and main_loop.is_running():
            async def do_cancel_edit():
                await asyncio.get_running_loop().run_in_executor(None, lambda: _refresh_auth_panel(card_message_id))
            asyncio.run_coroutine_threadsafe(do_cancel_edit(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已取消编辑"}})

    if action == "user_page":
        page = int(action_value.get("page", 1))
        if main_loop and main_loop.is_running():
            async def do_user_page():
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: patch_interactive_card_sdk(
                        card_message_id,
                        CardBuilder.build_user_panel_card(list_auth_sessions(), page=page),
                    ),
                )
            asyncio.run_coroutine_threadsafe(do_user_page(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已翻页"}})

    return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "未知操作"}})


def _handle_cron_card_action(action_value, chat_id, card_message_id):
    """Handle scheduled-task (cron) card actions."""
    from database import get_all_cron_tasks, get_cron_task, update_cron_task_status, delete_cron_task
    from cron_engine import cron_engine

    action = action_value.get("action")

    if action == "switch_cron_tab":
        tab = action_value.get("tab", "user")
        if main_loop and main_loop.is_running():
            async def do_switch_cron():
                tasks = await asyncio.get_running_loop().run_in_executor(None, lambda: get_all_cron_tasks(chat_id))
                session_data = await get_session_async(chat_id)
                new_card = CardBuilder.build_cron_panel_card(tasks, active_tab=tab, session_data=session_data)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_switch_cron(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已切换至 {'用户' if tab == 'user' else '系统'} 任务面板"}})

    if action == "open_cron_panel":
        if main_loop and main_loop.is_running():
            async def do_open_cron():
                from lark_client import send_card_to_chat_sdk
                tasks = await asyncio.get_running_loop().run_in_executor(None, lambda: get_all_cron_tasks(chat_id))
                session_data = await get_session_async(chat_id)
                new_card = CardBuilder.build_cron_panel_card(tasks, active_tab="user", session_data=session_data)
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_card_to_chat_sdk(chat_id, new_card))
            asyncio.run_coroutine_threadsafe(do_open_cron(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "正在打开计划任务中心..."}})

    if action == "open_cron_create":
        if main_loop and main_loop.is_running():
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
            asyncio.run_coroutine_threadsafe(do_open_create(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请发送格式为 '名称 | 规则 | Prompt' 的任务文本"}})

    if action == "toggle_cron_active":
        task_id = action_value.get("task_id")
        is_active = bool(action_value.get("is_active", True))
        if main_loop and main_loop.is_running():
            async def do_toggle_cron():
                await asyncio.get_running_loop().run_in_executor(None, lambda: update_cron_task_status(task_id, is_active))
                tasks = await asyncio.get_running_loop().run_in_executor(None, lambda: get_all_cron_tasks(chat_id))
                session_data = await get_session_async(chat_id)
                new_card = CardBuilder.build_cron_panel_card(tasks, active_tab="user", session_data=session_data)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_toggle_cron(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"任务已{'启用' if is_active else '暂停'}"}})

    if action == "delete_cron_task":
        task_id = action_value.get("task_id")
        if main_loop and main_loop.is_running():
            async def do_delete_cron():
                await asyncio.get_running_loop().run_in_executor(None, lambda: delete_cron_task(task_id))
                tasks = await asyncio.get_running_loop().run_in_executor(None, lambda: get_all_cron_tasks(chat_id))
                session_data = await get_session_async(chat_id)
                new_card = CardBuilder.build_cron_panel_card(tasks, active_tab="user", session_data=session_data)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_delete_cron(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "计划任务已物理删除！"}})

    if action == "run_cron_now":
        task_id = action_value.get("task_id")
        if main_loop and main_loop.is_running():
            async def do_run_now():
                task = await asyncio.get_running_loop().run_in_executor(None, lambda: get_cron_task(task_id))
                if task:
                    asyncio.create_task(cron_engine._run_task_wrapper(task))
            asyncio.run_coroutine_threadsafe(do_run_now(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已触发即刻运行计划任务！"}})

    return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "未知操作"}})


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

    if not _mark_seen(message_id):
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
            if main_loop and main_loop.is_running():
                asyncio.run_coroutine_threadsafe(_admin_welcome(chat_id), main_loop)
        else:
            log.info(f"[auth] Bootstrap pending; ignoring message from chat {chat_id}")
            return

    # Permission gate
    role = get_role(chat_id, sender_open_id)
    if role == "banned":
        log.info(f"[auth] Banned chat {chat_id} message ignored")
        return
    if role in ("guest", "pending"):
        if main_loop and main_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                _handle_guest_message(chat_id, chat_type, sender_open_id, role, message_type, content_raw),
                main_loop,
            )
        return

    # Authorized chats: apply rate limiting (admins exempt).
    if role == "user" and not allow_message(chat_id):
        if main_loop and main_loop.is_running():
            asyncio.run_coroutine_threadsafe(_rate_hint(chat_id), main_loop)
        return

    if main_loop and main_loop.is_running():
        asyncio.run_coroutine_threadsafe(handle_message_async(message_id, chat_id, message_type, content_raw), main_loop)
    else:
        log.error("main_loop is not running!")

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

    # ---- Plugin domain (dispatch to loaded plugins) ----
    from plugin_manager import plugin_manager
    plugin_action = action_value.get("action", "")
    if plugin_action and plugin_manager.plugins:
        if main_loop and main_loop.is_running():
            async def _do_plugin_action():
                await plugin_manager.dispatch_card_action(plugin_action, action_value, chat_id, card_message_id)
            asyncio.run_coroutine_threadsafe(_do_plugin_action(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已处理"}})

    if action_value.get("action") == "switch_model":
        new_model = action_value.get("model")
        
        if main_loop and main_loop.is_running():
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
            asyncio.run_coroutine_threadsafe(do_switch(), main_loop)

        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"模型已切换为 {new_model}"}})

    elif action_value.get("action") == "user_choice":
        choice = action_value.get("choice")
        label = action_value.get("label", choice)
        log.info(f"User selected choice: {choice}")
        
        if main_loop and main_loop.is_running():
            async def notify_and_process():
                if choice.startswith("/"):
                    # For slash commands, directly call the command handler
                    # Use card_message_id as the reply target
                    session_data = await get_session_async(chat_id)
                    await handle_slash_command(choice, card_message_id, chat_id, session_data, running_processes, chat_queues, chat_workers)
                else:
                    # For regular choices, notify and send to LLM
                    user_display_text = f"✅ **您已选择：{label}**\n*(选项内容已发送给 AI 进行下一步处理...)*"
                    await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, user_display_text))
                    simulated_content = json.dumps({"text": f"我的选择是：{choice}"})
                    await _handle_message_async_internal(card_message_id, chat_id, "text", simulated_content)

            asyncio.run_coroutine_threadsafe(notify_and_process(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已确认：{label[:15]}"}})
        
    elif action_value.get("action") == "browse_dir":
        target_path = action_value.get("path")
        
        if main_loop and main_loop.is_running():
            async def do_browse_dir():
                session_data = await get_session_async(chat_id)
                recent_projects = session_data.get("recent_projects", [])
                ignored_projects = session_data.get("ignored_projects", [])
                ws_root = session_data.get("workspace_root")
                new_card = CardBuilder.build_dir_browser_card(target_path, recent_projects, workspace_root=ws_root, ignored_projects=ignored_projects)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_browse_dir(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "正在载入目录..."}})
        
    elif action_value.get("action") == "select_project":
        target_path = action_value.get("path")
        
        if main_loop and main_loop.is_running():
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
            asyncio.run_coroutine_threadsafe(do_select_project(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "项目设定成功！"}})

    elif action_value.get("action") == "remove_project_from_list":
        target_path = action_value.get("path")
        
        if main_loop and main_loop.is_running():
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
            asyncio.run_coroutine_threadsafe(do_remove_project(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "项目已成功从列表中移出！"}})
    elif action_value.get("action") == "view_note_detail":
        idx = int(action_value.get("index"))
        if main_loop and main_loop.is_running():
            async def do_view_note():
                session_data = await get_session_async(chat_id)
                notes = session_data.get("notes", [])
                if 0 <= idx < len(notes):
                    note_content = notes[idx]
                    await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, f"📝 **笔记详情**:\\n{note_content}"))
            asyncio.run_coroutine_threadsafe(do_view_note(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "详情已发送到当前会话！"}})
        
    elif action_value.get("action") == "delete_note":
        idx = int(action_value.get("index"))
        
        if main_loop and main_loop.is_running():
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
            asyncio.run_coroutine_threadsafe(do_delete_note(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已成功删除该条笔记！"}})
        
    elif action_value.get("action") == "clear_notes":
        if main_loop and main_loop.is_running():
            async def do_clear_notes():
                session_data = await get_session_async(chat_id)
                session_data["notes"] = []
                await save_session_async(chat_id, session_data)
                
                new_card = CardBuilder.build_note_list_card([])
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_clear_notes(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "您的记事本已被全部清空！"}})
        
    elif action_value.get("action") == "refresh_status":
        if main_loop and main_loop.is_running():
            async def do_refresh_status():
                from commands import get_system_status_card_data
                cpu, mem_mb, uptime_str, status, restarts, err_logs, git_status, bot_stats = get_system_status_card_data()
                new_card = CardBuilder.build_status_card(cpu, mem_mb, uptime_str, status, restarts, err_logs, git_status, bot_stats)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_refresh_status(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "状态已刷新！"}})

    elif action_value.get("action") == "refresh_quota":
        if main_loop and main_loop.is_running():
            async def do_refresh_quota():
                from codex_quota import fetch_codex_quota
                loop = asyncio.get_running_loop()
                quota_result = await loop.run_in_executor(None, lambda: asyncio.run(fetch_codex_quota()))
                new_card = CardBuilder.build_quota_card(quota_result)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_refresh_quota(), main_loop)

        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "额度已刷新！"}})

    elif action_value.get("action") == "forget_single_memory":
        idx = int(action_value.get("index"))
        
        if main_loop and main_loop.is_running():
            async def do_forget():
                memories = await get_profile_async(chat_id)
                if 0 <= idx < len(memories):
                    removed = memories.pop(idx)
                    await save_profile_async(chat_id, memories)
                    log.info(f"Removed memory preference: '{removed}' in chat {chat_id}")
                    
                    new_card = CardBuilder.build_memory_card(memories)
                    await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_forget(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已成功擦除该偏好记录！"}})

    elif action_value.get("action") == "create_project_prompt":
        parent_path = action_value.get("parent_path")
        
        if main_loop and main_loop.is_running():
            async def do_create_project_prompt():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = "create_project"
                session_data["create_project_parent"] = parent_path
                await save_session_async(chat_id, session_data)
                
                prompt_msg = f"📂 **请输入新建项目的名称，或直接输入项目的 Git 仓库地址**：\n\n*(支持通过 Git URL 克隆；若输入项目名，将在公共根目录 `{parent_path}` 下新建并初始化)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, prompt_msg))
            asyncio.run_coroutine_threadsafe(do_create_project_prompt(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "请输入项目名或Git仓库地址！"}})

    elif action_value.get("action") == "browse_recent_page":
        target_path = action_value.get("current_path")
        target_page = action_value.get("page", 1)
        
        if main_loop and main_loop.is_running():
            async def do_browse_recent_page():
                session_data = await get_session_async(chat_id)
                recent_projects = session_data.get("recent_projects", [])
                ignored_projects = session_data.get("ignored_projects", [])
                ws_root = session_data.get("workspace_root")
                new_card = CardBuilder.build_dir_browser_card(target_path, recent_projects, target_page, workspace_root=ws_root, ignored_projects=ignored_projects)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_browse_recent_page(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"正在载入第 {target_page} 页项目..."}})

    elif action_value.get("action") == "set_workspace_prompt":
        if main_loop and main_loop.is_running():
            async def do_set_workspace_prompt():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = "project"
                await save_session_async(chat_id, session_data)
                prompt_msg = "📂 **请直接回复一个路径以设置当前活跃开发工作区**：\n\n*(支持 `~` 开头的路径，例如：`~/github/my-project`；路径必须已存在且为目录)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, prompt_msg))
            asyncio.run_coroutine_threadsafe(do_set_workspace_prompt(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "请回复路径以设置开发工作区！"}})

    elif action_value.get("action") == "set_workspace_root_prompt":
        if main_loop and main_loop.is_running():
            async def do_set_workspace_root_prompt():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = "workspace_root"
                await save_session_async(chat_id, session_data)
                prompt_msg = "⚙️ **请直接回复一个路径以设置公共项目根目录**：\n\n*(支持 `~` 开头的路径，例如：`~/projects`；路径必须已存在且为目录，后续新建项目与列表面板将绑定至此根目录)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, prompt_msg))
            asyncio.run_coroutine_threadsafe(do_set_workspace_root_prompt(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "请回复路径以设置公共项目根目录！"}})

    elif action_value.get("action") == "add_note_prompt":
        if main_loop and main_loop.is_running():
            async def do_add_note_prompt():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = "note_add"
                await save_session_async(chat_id, session_data)
                prompt_msg = "📝 **请直接回复笔记内容以添加笔记**：\n\n*(格式：第一个空格前的内容为标题，其余为正文。例如：`购物清单 牛奶、鸡蛋、面包`)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, prompt_msg))
            asyncio.run_coroutine_threadsafe(do_add_note_prompt(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "请回复笔记内容！"}})

    elif action_value.get("action") == "add_memory_prompt":
        if main_loop and main_loop.is_running():
            async def do_add_memory_prompt():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = "remember"
                await save_session_async(chat_id, session_data)
                prompt_msg = "🧠 **请直接回复您想让我长期记住的偏好或设定**：\n\n*(例如：`我开发只用 Python`、`回复请使用中文`)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, prompt_msg))
            asyncio.run_coroutine_threadsafe(do_add_memory_prompt(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "请回复要记住的偏好！"}})
    
    return P2CardActionTriggerResponse()

async def main():
    global main_loop
    main_loop = asyncio.get_running_loop()
    log.info("Starting Lark WS Client...")
    
    # Send post-update notification if applicable
    pending_file = os.path.join(BASE_DIR, ".update_pending.json")
    if os.path.exists(pending_file):
        try:
            with open(pending_file, "r") as f:
                data = json.load(f)
            os.remove(pending_file)
            msg_id = data.get("message_id")
            if msg_id:
                from commands import get_version_string
                v_str = get_version_string("HEAD")
                text = f"✨ 升级完毕！系统已成功重新上线。\n当前运行版本：{v_str}"
                # Send the notification in a background task so it doesn't block startup
                asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(msg_id, text))
                log.info(f"Sent post-update notification to {msg_id}")
        except Exception as e:
            log.error(f"Failed to process post-update notification: {e}")
            
    # Start background GC task
    gc_task = asyncio.create_task(garbage_collector())

    # Start cron engine (background scheduled tasks)
    from cron_engine import cron_engine
    cron_engine.start()

    # Load plugin system
    from plugin_manager import plugin_manager
    plugin_manager.register_system_commands([
        "/help", "/model", "/card", "/menu", "/project", "/note", "/notes",
        "/status", "/context", "/quota", "/clear", "/stop", "/update", "/ping",
        "/remember", "/memory", "/forget", "/brain", "/newproj_resolve",
        "/user", "/auth", "/cron", "/schedule", "/plugin", "/plugins",
    ])
    plugin_manager.load_all_plugins()
    
    event_handler = lark.EventDispatcherHandler.builder("", "") \
        .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1) \
        .register_p2_card_action_trigger(do_p2_card_action_trigger) \
        .build()

    cli = lark.ws.Client(
        APP_ID, 
        APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.DEBUG
    )
    
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, cli.start)

def cleanup(signum, frame):
    log.warning("Gracefully shutting down... killing zombie processes")
    try:
        from cron_engine import cron_engine
        cron_engine.stop()
    except Exception as e:
        log.error(f"Failed to stop cron engine: {e}")
    for process in running_processes.values():
        try:
            pgid = os.getpgid(process.pid)
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as e:
            log.error(f"Failed to kill process group {process.pid}: {e}")
            try:
                process.kill()
            except Exception:
                pass
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    asyncio.run(main())
