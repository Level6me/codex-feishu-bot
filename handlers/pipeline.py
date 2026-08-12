"""消息队列与单任务执行管道（阶段 5 重构：自 main.py 抽出）。"""
import asyncio
import re

import app_state
from agent_executor import execute_agent
from card_builder import CardBuilder
from config import AGENT_BACKEND
from database import get_session_async, save_session_async, get_profile_async
from lark_client import send_interactive_card_sdk, set_emoji_sdk, delete_emoji_sdk
from logger import log
from utils.auth import get_role
import stats


async def process_chat_queue(chat_id):
    queue = app_state.chat_queues[chat_id]
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
        app_state.chat_workers.pop(chat_id, None)
        if chat_id in app_state.chat_queues and app_state.chat_queues[chat_id].empty():
            app_state.chat_queues.pop(chat_id, None)


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
                download_success, app_state.running_processes, image_paths=image_paths,
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

