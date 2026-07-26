"""Codex CLI adapter for the isolated Feishu bot."""
import asyncio
import json
import os
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
    download_success, running_processes,
):
    loop = asyncio.get_running_loop()
    project = session_data.get("project")
    cwd = project if project and project not in {"Default"} and os.path.isdir(project) else None
    thread_id = session_data.get("codex_conversation", "")
    prompt = system_instruction + final_prompt
    if thread_id:
        cmd = [CODEX_BIN, "exec", "resume", thread_id, "--json", "--skip-git-repo-check"]
    else:
        cmd = [CODEX_BIN, "exec", "--json", "--sandbox", "workspace-write", "--skip-git-repo-check"]
        if cwd:
            cmd.extend(["--cd", cwd])
    model = session_data.get("codex_model") or CODEX_MODEL
    if model:
        cmd.extend(["--model", model])
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
    try:
        while process.returncode is None:
            await asyncio.sleep(0.75)
            seconds = int(time.time() - started)
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
    if failed:
        reply = "".join(errors).strip() or "Codex did not produce a response. Check Codex authentication and bot logs."
    else:
        allowed_dirs = [path for path in (cwd, session_data.get("workspace_root")) if path]
        await loop.run_in_executor(None, lambda: extract_and_upload_resources(reply, message_id, api_client, allowed_dirs))
        stats.record_tokens(len(user_text) + len(reply))

    final_card = CardBuilder.build_ai_response(
        reply, current_model=model or "Codex", current_role=session_data.get("role", "None"),
        current_project=session_data.get("project", "Default"), is_error=failed, is_streaming=False,
    )
    if bot_reply_msg_id:
        await loop.run_in_executor(None, lambda: patch_interactive_card_sdk(bot_reply_msg_id, final_card))
    return failed


async def execute_agent(*args, **kwargs):
    return await execute_codex(*args, **kwargs)
