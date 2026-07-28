"""Codex 官方订阅额度查询客户端。

通过启动 `codex app-server --stdio` 子进程，使用 JSON-RPC 协议调用
`account/rateLimits/read` 方法读取当前登录账号的额度信息。
该方案参考 Codex-Meter (https://github.com/Oldleeo/Codex-Meter)。

前置条件：用户必须先运行 `codex login` 登录官方 ChatGPT 账号。
自定义 provider 配置（config.toml 中的 model_providers）与本查询无关。
"""
import asyncio
import json
import os
import shutil
from logger import log

CODEX_QUOTA_TIMEOUT = 10.0


def _locate_codex() -> str:
    """查找本地 codex 可执行文件。"""
    explicit = os.environ.get("CODEX_BIN")
    if explicit and os.path.isfile(explicit) and os.access(explicit, os.X_OK):
        return explicit
    which = shutil.which("codex")
    if which:
        return which
    home = os.path.expanduser("~")
    candidates = [
        "/Applications/ChatGPT.app/Contents/Resources/codex",
        "/Applications/Codex.app/Contents/Resources/codex",
        "/opt/homebrew/bin/codex",
        "/usr/local/bin/codex",
        os.path.join(home, ".local/bin/codex"),
        os.path.join(home, ".npm-global/bin/codex"),
    ]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return ""


async def fetch_codex_quota(timeout: float = CODEX_QUOTA_TIMEOUT) -> dict:
    """查询 Codex 官方订阅额度。

    返回结构：
      {"ok": True, "data": {primary, planType, credits, lifetimeTokens}}
      {"ok": False, "error": "not_installed" | "not_logged_in" | "timeout" | "protocol" | "unknown", "message": "..."}
    """
    codex_bin = _locate_codex()
    if not codex_bin:
        return {"ok": False, "error": "not_installed",
                "message": "未找到 codex 可执行文件，请先安装 Codex CLI"}

    process = await asyncio.create_subprocess_exec(
        codex_bin, "app-server", "--stdio",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.PIPE,
    )

    result = {"ok": False, "error": "protocol", "message": "未收到响应"}
    got_init = False
    got_rate_limits = False

    async def _read_stdout():
        nonlocal result, got_init, got_rate_limits
        buffer = b""
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            buffer += line
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # initialize 响应
                if msg.get("id") == 1 and "result" in msg:
                    got_init = True
                    await _send({"method": "initialized"})
                    await _send({"id": 2, "method": "account/rateLimits/read", "params": None})
                    continue
                # account/rateLimits/read 响应
                if msg.get("id") == 2:
                    if "error" in msg:
                        err = msg["error"]
                        code = err.get("code") if isinstance(err, dict) else None
                        message = err.get("message", "") if isinstance(err, dict) else str(err)
                        if code == -32600 or "authentication" in message.lower():
                            result = {"ok": False, "error": "not_logged_in",
                                      "message": "未检测到 Codex 官方登录状态，请先运行 `codex login`"}
                        else:
                            result = {"ok": False, "error": "protocol",
                                      "message": f"RPC 错误: {message}"}
                    elif "result" in msg:
                        result = {"ok": True, "data": msg["result"]}
                    got_rate_limits = True
                    return
                # 通知类消息（remoteControl/status/changed 等）直接忽略

    async def _send(obj):
        try:
            data = json.dumps(obj) + "\n"
            process.stdin.write(data.encode("utf-8"))
            await process.stdin.drain()
        except Exception as e:
            log.error(f"[codex_quota] send failed: {e}")

    try:
        await _send({
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "codex-feishu-bot", "title": "Codex Feishu Bot", "version": "1.0"},
                "capabilities": {"experimentalApi": True},
            },
        })

        read_task = asyncio.create_task(_read_stdout())
        try:
            await asyncio.wait_for(read_task, timeout=timeout)
        except asyncio.TimeoutError:
            read_task.cancel()
            result = {"ok": False, "error": "timeout",
                      "message": f"查询超时（{int(timeout)}s），Codex 服务未响应"}
    except FileNotFoundError:
        result = {"ok": False, "error": "not_installed",
                  "message": "codex 可执行文件启动失败"}
    except Exception as e:
        log.error(f"[codex_quota] unexpected error: {e}")
        result = {"ok": False, "error": "unknown", "message": f"未知错误: {e}"}
    finally:
        try:
            process.stdin.close()
        except Exception:
            pass
        try:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                process.kill()
        except Exception:
            pass

    return result
