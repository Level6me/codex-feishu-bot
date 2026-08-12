"""飞书消息处理管道（阶段 5 重构：自 main.py 抽出）。"""
import asyncio
import json
import re
import time

import app_state
from card_builder import CardBuilder
from commands import handle_slash_command
from database import get_session_async, save_session_async, save_profile_async, get_profile_async
from lark_client import send_reply_sdk, send_interactive_card_sdk, send_card_to_chat_async, send_text_to_chat_async
from logger import log
from utils.auth import get_role, request_access, get_admin_chat_id, get_auth_session, save_auth_session, resolve_display_name
from handlers.pipeline import process_chat_queue
import stats


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
        handled, override_text = await handle_slash_command(raw_text, message_id, chat_id, session_data, app_state.running_processes, app_state.chat_queues, app_state.chat_workers)
        if handled:
            stats.record_success()
            return
        if override_text:
            raw_text = override_text

    # 辅助任务分发函数
    async def dispatch_task(c_id, msg_id, m_type, c_json, c_raw, r_text):
        if c_id not in app_state.chat_queues:
            app_state.chat_queues[c_id] = asyncio.Queue()
            
        task_payload = {
            "message_id": msg_id,
            "message_type": m_type,
            "content_json": c_json,
            "content_raw": c_raw,
            "raw_text": r_text
        }
        
        if c_id in app_state.chat_workers and not app_state.chat_workers[c_id].done():
            qsize = app_state.chat_queues[c_id].qsize()
            warning_msg = f"⏳ 收到！当前有任务正在执行，该请求已加入队列排队处理 (前方还有 {qsize + 1} 个任务)..."
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(msg_id, warning_msg))
            await app_state.chat_queues[c_id].put(task_payload)
        else:
            await app_state.chat_queues[c_id].put(task_payload)
            app_state.chat_workers[c_id] = asyncio.create_task(process_chat_queue(c_id))

    # 方案四：多模态多图合并批处理防抖机制
    if message_type in ["image", "file", "audio", "media"]:
        if chat_id not in app_state.chat_media_batches:
            app_state.chat_media_batches[chat_id] = {
                "items": [],
                "timer_task": None
            }
            
        batch = app_state.chat_media_batches[chat_id]
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
                app_state.chat_media_batches.pop(chat_id, None)
                
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

