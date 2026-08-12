"""Codex CLI adapter for the isolated Feishu bot."""
import asyncio
import json
import os
import re
import shlex
import signal
import time

from card_builder import CardBuilder
from config import AGENT_BACKEND, CODEX_BIN, CODEX_MODEL
from database import save_session_async
from lark_client import api_client, patch_interactive_card_sdk, send_interactive_card_sdk, send_reply_sdk
from multimodal import extract_and_upload_resources
from logger import log
import stats

STALL_TIMEOUT = 600          # 连续 10 分钟无任何进度判定为卡死
QUIET_WARNING_THRESHOLD = 180  # 连续 3 分钟无输出时推送停滞预警卡片
GLOBAL_TIMEOUT = 43200       # 12 小时全局超时保护上限
STREAM_PATCH_INTERVAL = 1.0  # 流式卡片固定刷新间隔（秒），保证打字机与读秒平滑
FEISHU_TIMEOUT = 30
SAVE_TIMEOUT = 3


async def _feishu_call(loop, func, *args):
    return await asyncio.wait_for(
        loop.run_in_executor(None, func, *args),
        timeout=FEISHU_TIMEOUT,
    )


# 常见命令的简短中文概括，避免在卡片上暴露完整路径和参数
_COMMAND_SUMMARY = {
    ("git", "status"): "检查 Git 仓库状态",
    ("git", "log"): "查看 Git 提交记录",
    ("git", "diff"): "对比代码差异",
    ("git", "pull"): "拉取远程代码",
    ("git", "fetch"): "拉取远程代码",
    ("git", "push"): "推送本地代码",
    ("git", "checkout"): "切换 Git 分支",
    ("git", "switch"): "切换 Git 分支",
    ("git", "clone"): "克隆代码仓库",
    ("git", "add"): "暂存代码变更",
    ("git", "commit"): "提交代码变更",
    ("git", "reset"): "重置代码变更",
    ("git", "stash"): "暂存代码改动",
    ("git", "branch"): "管理 Git 分支",
    ("npm", "install"): "安装依赖",
    ("npm", "i"): "安装依赖",
    ("npm", "run"): "运行项目脚本",
    ("npm", "build"): "构建项目",
    ("npm", "test"): "运行测试",
    ("yarn", "install"): "安装依赖",
    ("yarn", "build"): "构建项目",
    ("yarn", "test"): "运行测试",
    ("pnpm", "install"): "安装依赖",
    ("pnpm", "build"): "构建项目",
    ("pnpm", "test"): "运行测试",
    ("pip", "install"): "安装 Python 依赖",
    ("pip3", "install"): "安装 Python 依赖",
    ("docker", "compose"): "操作 Docker 服务",
    ("docker", "build"): "构建 Docker 镜像",
    ("docker", "ps"): "查看 Docker 容器",
}

_READONLY_CMDS = {"ls", "ll", "dir", "tree", "find", "du", "stat", "cat", "head", "tail", "less", "more", "wc"}
_SEARCH_CMDS = {"rg", "grep", "ack", "ag", "find", "rgrep"}
_EDIT_CMDS = {"sed", "awk", "perl", "touch", "chmod", "chown", "vim", "vi", "nano"}
_FILE_OPS = {"cp", "mv", "rm", "mkdir", "rmdir", "unzip", "zip", "tar", "curl", "wget"}
_PYTHON_CMDS = {"python", "python3", "python2", "uv", "pip", "pip3"}
_NODE_CMDS = {"node", "npx", "bun", "npm", "yarn", "pnpm"}


def _summarize_command(command):
    """把完整 shell 命令压缩成简短动作描述，不暴露具体路径。"""
    if not command:
        return "执行命令"
    text = str(command).strip().splitlines()[0] if str(command).strip() else ""
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()
    if not tokens:
        return "执行命令"
    # 跳过 cd 前缀及其路径，定位 && 之后的真正命令
    if tokens[0] == "cd":
        rest = tokens[1:]
        if rest and rest[0] not in ("&&", ";"):
            rest = rest[1:]
        if rest and rest[0] in ("&&", ";"):
            rest = rest[1:]
        tokens = rest
    if not tokens:
        return "切换目录"

    cmd = os.path.basename(tokens[0]).lower().rstrip("/")
    sub = ""
    run_script = ""
    option_with_value = False
    for tok in tokens[1:]:
        if option_with_value:
            option_with_value = False
            continue
        if tok in ("-C", "-c"):
            option_with_value = True
            continue
        if not tok.startswith("-"):
            sub = tok
            break
    if cmd in _NODE_CMDS and sub == "run":
        for tok in tokens[2:]:
            if tok.startswith("-"):
                continue
            run_script = tok
            break
        if run_script in ("build", "release"):
            return "构建项目"
        if run_script == "test":
            return "运行测试"
        if run_script in ("dev", "start", "serve"):
            return "启动开发服务"
        return "运行项目脚本"

    key = (cmd, sub)
    if key in _COMMAND_SUMMARY:
        return _COMMAND_SUMMARY[key]
    if cmd in _SEARCH_CMDS:
        return "搜索代码"
    if cmd in _READONLY_CMDS:
        return "检查文件或目录"
    if cmd in _EDIT_CMDS:
        return "编辑文件"
    if cmd in _FILE_OPS:
        return "操作文件或下载资源"
    if cmd == "git":
        return "执行 Git 操作"
    if cmd in _PYTHON_CMDS:
        return "执行 Python 脚本" if cmd.startswith("python") else "安装 Python 依赖"
    if cmd in _NODE_CMDS:
        return "运行项目脚本"
    if cmd == "docker":
        return "操作 Docker"
    return f"执行 {cmd} 命令"


def _summarize_tool(item):
    """把工具调用压缩成简短描述，工具名过长时只保留最后一段。"""
    tool = item.get("tool") or item.get("name") or item.get("title") or ""
    if not tool:
        return "调用工具"
    name = str(tool).strip()
    # 先取文件名，去掉扩展名，再去掉 mcp 前缀，只保留有意义的最后一段
    base = os.path.basename(name.rstrip("/"))
    base = re.sub(r"\.(py|js|ts|sh|exe|json|rb|go)$", "", base, flags=re.IGNORECASE)
    short = base.split("__")[-1].strip()
    if not short:
        short = name.split("__")[-1].strip()
    if len(short) > 40:
        short = short[-40:]
    return f"调用工具 {short}"


def _event_action(event):
    item = event.get("item") or event
    kind = item.get("type", "")
    if kind in {"command_execution", "command", "shell"}:
        return _summarize_command(item.get("command") or item.get("text") or item.get("content") or "")
    if kind in ("file_change", "patch"):
        return "更新文件"
    if kind in ("mcp_tool_call", "tool_call", "custom_tool_call"):
        return _summarize_tool(item)
    return ""


def _agent_text(event):
    item = event.get("item") or event
    if item.get("type") not in ("agent_message", "message"):
        return ""
    text = item.get("text") or item.get("content") or ""
    return text if isinstance(text, str) else ""


async def execute_codex(
    chat_id, user_text, message_id, bot_reply_msg_id, session_data,
    is_new_conversation, system_instruction, final_prompt, downloaded_file_name,
    download_success, running_processes, image_paths=None, role="admin", silent=False,
):
    loop = asyncio.get_running_loop()
    project = session_data.get("project")
    cwd = project if project and project not in ("Default",) and os.path.isdir(project) else None
    thread_id = session_data.get("codex_conversation", "")
    resumed_thread = bool(thread_id)
    prompt = system_instruction + final_prompt
    # 权限联动：管理员/未知角色保持完整沙箱；普通用户强制受限工作区沙箱
    sandbox = "danger-full-access" if role == "admin" else "workspace-write"
    if thread_id:
        cmd = [CODEX_BIN, "exec", "resume", thread_id, "--json", "--skip-git-repo-check",
               "-c", f'sandbox_mode="{sandbox}"']
    else:
        cmd = [CODEX_BIN, "exec", "--json", "--sandbox", sandbox, "--skip-git-repo-check"]
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

    # 防止同一会话并发启动多个 Codex 进程：直接覆盖 running_processes[chat_id]
    # 会丢失进程追踪，且两个进程 resume 同一线程会造成线程错乱、卡片卡死。
    existing = running_processes.get(chat_id)
    if existing is not None and getattr(existing, "returncode", None) is None:
        msg = (
            "⚠️ 当前会话已有任务正在执行，已拒绝并发启动第二个任务"
            f"（现有进程 pid={getattr(existing, 'pid', '?')}）。"
            "请等待当前任务完成，或使用 /stop 终止后再试。"
        )
        log.warning(f"[execute_codex] {msg}")
        if not silent:
            err_card = CardBuilder.build_ai_response(msg, is_error=True)
            try:
                if bot_reply_msg_id:
                    await _feishu_call(loop, patch_interactive_card_sdk, bot_reply_msg_id, err_card)
                else:
                    await _feishu_call(loop, send_interactive_card_sdk, message_id, err_card)
            except Exception as e:
                log.error(f"Failed to send concurrent-task error card: {e}")
        return True

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            preexec_fn=os.setsid,
        )
    except FileNotFoundError:
        if silent:
            return True, f"❌ 未找到 Codex CLI（`{CODEX_BIN}`）。"
        err_card = CardBuilder.build_ai_response(
            f"❌ 未找到 Codex CLI（`{CODEX_BIN}`）。请在服务器上安装 codex 并确认 PATH 或 CODEX_BIN 配置。",
            is_error=True,
        )
        try:
            if bot_reply_msg_id:
                await _feishu_call(loop, patch_interactive_card_sdk, bot_reply_msg_id, err_card)
            else:
                await _feishu_call(loop, send_interactive_card_sdk, message_id, err_card)
        except Exception as e:
            log.error(f"Failed to send error card: {e}")
        return True

    running_processes[chat_id] = process

    if not silent:
        typing_card = CardBuilder.build_typing_indicator(downloaded_file_name, download_success, user_text)
        try:
            if bot_reply_msg_id:
                await _feishu_call(loop, patch_interactive_card_sdk, bot_reply_msg_id, typing_card)
            else:
                bot_reply_msg_id = await _feishu_call(loop, send_interactive_card_sdk, message_id, typing_card)
        except Exception as e:
            log.error(f"Failed to send typing card: {e}")

    replies, errors, stdout_errors = [], [], []
    latest_action = ""
    thread_broken = False
    last_progress_time = time.time()
    last_stdout_size = 0
    stream_text = ""
    last_streamed_length = 0
    started = time.time()
    next_patch_time = 0.0

    async def consume_stdout():
        nonlocal latest_action, thread_id, last_progress_time, last_stdout_size, thread_broken, stream_text
        while raw := await process.stdout.readline():
            last_stdout_size += len(raw)
            last_progress_time = time.time()
            try:
                event = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            event_type = event.get("type", "")
            if event_type == "error" or event.get("error"):
                err_msg = event.get("message") or event.get("error", {}).get("message") or str(event)
                stdout_errors.append(err_msg)
                if "No tool output found" in str(err_msg) or "invalid_request_error" in str(err_msg):
                    thread_broken = True
            if event_type == "thread.started" and event.get("thread_id"):
                thread_id = event["thread_id"]
                if not silent:
                    session_data["codex_conversation"] = thread_id
                    try:
                        await asyncio.wait_for(save_session_async(chat_id, session_data), timeout=SAVE_TIMEOUT)
                    except Exception as e:
                        log.warning(f"save_session timed out or failed: {e}")
            if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
                usage = event["usage"]
                ctx = session_data.get("context_usage") or {}
                ctx["last_input_tokens"] = usage.get("input_tokens", 0)
                ctx["last_cached_tokens"] = usage.get("cached_input_tokens", 0)
                ctx["last_output_tokens"] = usage.get("output_tokens", 0)
                ctx["total_input_tokens"] = ctx.get("total_input_tokens", 0) + usage.get("input_tokens", 0)
                ctx["total_output_tokens"] = ctx.get("total_output_tokens", 0) + usage.get("output_tokens", 0)
                ctx["turns"] = ctx.get("turns", 0) + 1
                ctx["model"] = model or "默认"
                session_data["context_usage"] = ctx
                if not silent:
                    try:
                        await asyncio.wait_for(save_session_async(chat_id, session_data), timeout=SAVE_TIMEOUT)
                    except Exception as e:
                        log.warning(f"save_session timed out or failed: {e}")
            text = _agent_text(event)
            if text:
                replies.append(text)
                stream_text += text + "\n"
            action = _event_action(event)
            if action and action != latest_action:
                latest_action = action
                last_progress_time = time.time()

    async def consume_stderr():
        nonlocal last_progress_time
        while raw := await process.stderr.readline():
            last_progress_time = time.time()
            errors.append(raw.decode("utf-8", errors="replace"))

    stdout_task = asyncio.create_task(consume_stdout())
    stderr_task = asyncio.create_task(consume_stderr())
    timed_out = False
    stalled = False
    try:
        while process.returncode is None:
            await asyncio.sleep(0.75)
            now = time.time()
            seconds = int(now - started)
            stall_seconds = int(now - last_progress_time)

            if stall_seconds >= STALL_TIMEOUT:
                stalled = True
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                break

            if seconds > GLOBAL_TIMEOUT:
                timed_out = True
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                break

            if not silent and bot_reply_msg_id and now >= next_patch_time:
                if stall_seconds >= QUIET_WARNING_THRESHOLD:
                    # 连续静默超阈值：推送带"继续等待 / 叫停任务"按钮的预警卡片
                    card = CardBuilder.build_stall_warning_card(user_text, seconds, stall_seconds)
                elif stream_text.strip():
                    # 已有部分输出：流式打字机展示最新文本
                    clean_partial = re.sub(
                        r'\[CHOICE_CARD\]\s*Q:.*?(?:\[/CHOICE_CARD\]|\Z)', '',
                        stream_text, flags=re.DOTALL | re.IGNORECASE,
                    ).strip()
                    if not clean_partial:
                        clean_partial = stream_text
                    target_len = len(clean_partial)
                    # 打字机增量：短文本逐字显现，长文本约 50 秒内打完全文
                    tick_chars = max(6, min(50, target_len // 50))
                    if last_streamed_length < target_len:
                        last_streamed_length = min(target_len, last_streamed_length + tick_chars)
                    display_partial = clean_partial[:last_streamed_length]
                    card = CardBuilder.build_streaming_indicator(display_partial, latest_action, user_text, seconds)
                else:
                    card = (
                        CardBuilder.build_tool_indicator(latest_action, user_text, downloaded_file_name, download_success, seconds)
                        if latest_action
                        else CardBuilder.build_typing_indicator(downloaded_file_name, download_success, user_text, seconds)
                    )
                try:
                    await _feishu_call(loop, patch_interactive_card_sdk, bot_reply_msg_id, card)
                except Exception as e:
                    log.warning(f"Indicator patch failed: {e}")
                next_patch_time = now + STREAM_PATCH_INTERVAL

        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            log.warning("process.wait() timed out after 5s, forcing pipe close")
            try:
                process.stdout._transport.close()
                process.stderr._transport.close()
            except Exception:
                pass
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        await asyncio.gather(stdout_task, stderr_task)
    finally:
        running_processes.pop(chat_id, None)

    reply = "\n\n".join(part.strip() for part in replies if part.strip()).strip()
    failed = process.returncode != 0 or not reply

    # 会话线程损坏（工具调用输出丢失）时，清空会话并新建线程重试一次，避免每次消息都失败
    if thread_broken and resumed_thread:
        log.warning(
            f"Thread {thread_id} corrupted (missing tool output), clearing conversation and retrying with a fresh thread"
        )
        session_data["codex_conversation"] = ""
        try:
            await asyncio.wait_for(save_session_async(chat_id, session_data), timeout=SAVE_TIMEOUT)
        except Exception as e:
            log.warning(f"save_session failed while clearing corrupted thread: {e}")
        return await execute_codex(
            chat_id, user_text, message_id, bot_reply_msg_id, session_data,
            is_new_conversation, system_instruction, final_prompt, downloaded_file_name,
            download_success, running_processes, image_paths=image_paths, role=role, silent=silent,
        )

    if stalled:
        failed = True
        reply = f"⚠️ 任务已 {STALL_TIMEOUT} 秒无任何进展（无 stdout 输出、无工具动作变化），疑似内部死锁，已强制终止。请拆分任务或 /stop 后重试。"
        if not silent and bot_reply_msg_id:
            try:
                error_card = CardBuilder.build_stall_error_card(
                    user_text, int(time.time() - started), int(time.time() - last_progress_time)
                )
                await _feishu_call(loop, patch_interactive_card_sdk, bot_reply_msg_id, error_card)
            except Exception as e:
                log.warning(f"Stall error card patch failed: {e}")
    elif timed_out:
        failed = True
        reply = "⏰ 任务执行超过 12 小时已被强制终止（系统保护上限）。请拆分任务或使用 /stop 后重试。"
    elif failed:
        all_errors = "".join(errors).strip()
        if stdout_errors:
            all_errors += "\n" + "\n".join(stdout_errors)
        reply = all_errors or "Codex did not produce a response. Check Codex authentication and bot logs."
    else:
        allowed_dirs = [path for path in (cwd, session_data.get("workspace_root")) if path]
        try:
            await loop.run_in_executor(None, lambda: extract_and_upload_resources(reply, message_id, api_client, allowed_dirs))
        except Exception as e:
            log.warning(f"extract_and_upload_resources failed: {e}")
        stats.record_tokens(len(user_text) + len(reply))

    # Run plugin on_after_ai hooks (plugins may post-process the reply)
    try:
        from plugin_manager import plugin_manager
        if plugin_manager.plugins:
            reply = await plugin_manager.dispatch_after_ai(reply, chat_id, session_data)
    except Exception as e:
        log.warning(f"dispatch_after_ai failed: {e}")

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

    if not silent:
        final_card = CardBuilder.build_ai_response(
            reply, choice_card_data=choice_card_data,
            current_model=model or "Codex",
            current_project=session_data.get("project", "Default"), is_error=failed, is_streaming=False,
        )
        try:
            if bot_reply_msg_id:
                await _feishu_call(loop, patch_interactive_card_sdk, bot_reply_msg_id, final_card)
            else:
                await _feishu_call(loop, send_interactive_card_sdk, message_id, final_card)
        except Exception as e:
            log.error(f"Final card failed, falling back to plain text: {e}")
            try:
                await _feishu_call(loop, send_reply_sdk, message_id, reply[:28000])
            except Exception as e2:
                log.error(f"Plain text fallback also failed: {e2}")
    if silent:
        return failed, reply
    return failed


async def execute_agent(*args, **kwargs):
    return await execute_codex(*args, **kwargs)
