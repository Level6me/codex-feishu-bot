"""Codex CLI adapter for the isolated Feishu bot."""
import asyncio
import json
import os
import re
import signal
import time

from card_builder import CardBuilder
from config import AGENT_BACKEND, CODEX_BIN, CODEX_MODEL
from database import save_session_async
from lark_client import api_client, patch_interactive_card_sdk, send_interactive_card_sdk
from multimodal import extract_and_upload_resources
import stats


def _event_action(event):
    item = event.get("item") or event
    kind = item.get("type", "")
    if kind in {"command_execution", "command"}:
        return item.get("command") or item.get("text") or "Running command"
    if kind in {"file_change", "patch"}:
        return "Updating files"
    if kind in {"mcp_tool_call", "tool_call"}:
        return item.get("tool") or item.get("name") or "Calling tool"
    return ""


def _agent_text(event):
    item = event.get("item") or event
    if item.get("type") not in {"agent_message", "message"}:
        return ""
    text = item.get("text") or item.get("content") or ""
    return text if isinstance(text, str) else ""


async def execute_codex(
    chat_id, user_text, message_id, bot_reply_msg_id, session_data,
    is_new_conversation, system_instruction, final_prompt, downloaded_file_name,
    download_success, running_processes, image_paths=None,
):
    loop = asyncio.get_running_loop()
    project = session_data.get("project")
    cwd = project if project and project not in {"Default"} and os.path.isdir(project) else None
    thread_id = session_data.get("codex_conversation", "")
    prompt = system_instruction + final_prompt
    if thread_id:
        cmd = [CODEX_BIN, "exec", "resume", thread_id, "--json", "--skip-git-repo-check",
               "-c", 'sandbox_mode="workspace-write"']
    else:
        cmd = [CODEX_BIN, "exec", "--json", "--sandbox", "workspace-write", "--skip-git-repo-check"]
        if cwd:
            cmd.extend(["--cd", cwd])
    model = session_data.get("codex_model") or CODEX_MODEL
    if model:
        cmd.extend(["--model", model])
    for img in (image_paths or []):
        if os.path.isfile(img):
            cmd.extend(["--image", img])
    cmd.append(prompt)

    env = os.environ.copy()
    env.update({"GIT_TERMINAL_PROMPT": "0", "DEBIAN_FRONTEND": "noninteractive", "GIT_ASKPASS": "echo"})
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            preexec_fn=os.setsid,
        )
    except FileNotFoundError:
        err_card = CardBuilder.build_ai_response(
            f"❌ 未找到 Codex CLI（`{CODEX_BIN}`）。请在服务器上安装 codex 并确认 PATH 或 CODEX_BIN 配置。",
            is_error=True,
        )
        if bot_reply_msg_id:
            await loop.run_in_executor(None, lambda: patch_interactive_card_sdk(bot_reply_msg_id, err_card))
        else:
            await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, err_card))
        return True

    running_processes[chat_id] = process
    card = CardBuilder.build_typing_indicator(downloaded_file_name, download_success, user_text)
    if bot_reply_msg_id:
        await loop.run_in_executor(None, lambda: patch_interactive_card_sdk(bot_reply_msg_id, card))
    else:
        bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, card))

    replies, errors = [], []
    latest_action = ""
    started = time.time()

    async def consume_stdout():
        nonlocal latest_action, thread_id
        while raw := await process.stdout.readline():
            try:
                event = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if event.get("type") == "thread.started" and event.get("thread_id"):
                thread_id = event["thread_id"]
                session_data["codex_conversation"] = thread_id
                await save_session_async(chat_id, session_data)
            text = _agent_text(event)
            if text:
                replies.append(text)
            action = _event_action(event)
            if action:
                latest_action = action

    async def consume_stderr():
        while raw := await process.stderr.readline():
            errors.append(raw.decode("utf-8", errors="replace"))

    stdout_task = asyncio.create_task(consume_stdout())
    stderr_task = asyncio.create_task(consume_stderr())
    timed_out = False
    try:
        while process.returncode is None:
            await asyncio.sleep(0.75)
            seconds = int(time.time() - started)
            if seconds > 1800:
                timed_out = True
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                break
            card = (CardBuilder.build_tool_indicator(latest_action, user_text, downloaded_file_name, download_success, seconds)
                    if latest_action else CardBuilder.build_typing_indicator(downloaded_file_name, download_success, user_text, seconds))
            if bot_reply_msg_id:
                await loop.run_in_executor(None, lambda: patch_interactive_card_sdk(bot_reply_msg_id, card))
        await process.wait()
        await asyncio.gather(stdout_task, stderr_task)
    finally:
        running_processes.pop(chat_id, None)

    reply = "\n\n".join(part.strip() for part in replies if part.strip()).strip()
    failed = process.returncode != 0 or not reply
    if timed_out:
        failed = True
        reply = "⏰ 任务执行超过 30 分钟已被强制终止。请拆分任务或使用 /stop 后重试。"
    elif failed:
        reply = "".join(errors).strip() or "Codex did not produce a response. Check Codex authentication and bot logs."
    else:
        allowed_dirs = [path for path in (cwd, session_data.get("workspace_root")) if path]
        await loop.run_in_executor(None, lambda: extract_and_upload_resources(reply, message_id, api_client, allowed_dirs))
        stats.record_tokens(len(user_text) + len(reply))

    choice_card_data = None
    if not failed and reply:
        choice_pattern = re.compile(r'\[CHOICE_CARD\]\s*Q:\s*(.*?)\n(.*?)\s*\[/CHOICE_CARD\]', re.DOTALL | re.IGNORECASE)
        match = choice_pattern.search(reply)
        if match:
            question = match.group(1).strip()
            options_text = match.group(2).strip()
            options = [opt.strip()[1:].strip() if opt.strip().startswith('-') else opt.strip()
                       for opt in options_text.split('\n') if opt.strip()]
            reply = choice_pattern.sub('', reply).strip()
            if options:
                choice_card_data = {"question": question, "options": options}

    final_card = CardBuilder.build_ai_response(
        reply, choice_card_data=choice_card_data,
        current_model=model or "Codex", current_role=session_data.get("role", "None"),
        current_project=session_data.get("project", "Default"), is_error=failed, is_streaming=False,
    )
    if bot_reply_msg_id:
        await loop.run_in_executor(None, lambda: patch_interactive_card_sdk(bot_reply_msg_id, final_card))
    return failed


async def execute_agent(*args, **kwargs):
    return await execute_codex(*args, **kwargs)
