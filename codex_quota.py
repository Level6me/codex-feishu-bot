"""Codex 官方订阅额度与模型列表查询客户端。

通过启动 `codex app-server --stdio` 子进程，使用 JSON-RPC 协议调用
`account/rateLimits/read` / `model/list` 方法。
该方案参考 Codex-Meter (https://github.com/Oldleeo/Codex-Meter)。

前置条件：用户必须先运行 `codex login` 登录官方 ChatGPT 账号。
自定义 provider 配置（config.toml 中的 model_providers）与本查询无关。
"""
import asyncio
import json
import os
import shutil
import time
from logger import log

CODEX_QUOTA_TIMEOUT = 10.0
MODEL_CACHE_TTL = 600

_model_cache = {"models": None, "fetched_at": 0.0}


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


async def _rpc_call(method: str, params, timeout: float = CODEX_QUOTA_TIMEOUT) -> dict:
    """启动 codex app-server 并调用单个 JSON-RPC 方法。

    返回结构：
      {"ok": True, "data": <result>}
      {"ok": False, "error": "not_installed" | "not_logged_in" | "timeout" | "protocol" | "unknown", "message": "..."}
    """
    codex_bin = _locate_codex()
    if not codex_bin:
        return {"ok": False, "error": "not_installed",
                "message": "未找到 codex 可执行文件，请先安装 Codex CLI"}

    try:
        process = await asyncio.create_subprocess_exec(
            codex_bin, "app-server", "--stdio",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "not_installed",
                "message": "codex 可执行文件启动失败"}

    result = {"ok": False, "error": "protocol", "message": "未收到响应"}

    async def _send(obj):
        try:
            data = json.dumps(obj) + "\n"
            process.stdin.write(data.encode("utf-8"))
            await process.stdin.drain()
        except Exception as e:
            log.error(f"[codex_rpc] send failed: {e}")

    async def _read_stdout():
        nonlocal result
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            raw = line.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == 1 and "result" in msg:
                await _send({"method": "initialized"})
                await _send({"id": 2, "method": method, "params": params})
                continue
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
                return

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
    except Exception as e:
        log.error(f"[codex_rpc] unexpected error: {e}")
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


async def fetch_codex_quota(timeout: float = CODEX_QUOTA_TIMEOUT) -> dict:
    """查询 Codex 官方订阅额度。"""
    return await _rpc_call("account/rateLimits/read", None, timeout)


async def fetch_codex_models(timeout: float = CODEX_QUOTA_TIMEOUT, force_refresh: bool = False) -> list:
    """获取当前账号真实可用的 Codex 模型列表（10 分钟缓存）。

    返回模型 id 字符串列表；查询失败时返回空列表，调用方应回退到静态配置。
    """
    now = time.time()
    if (not force_refresh and _model_cache["models"]
            and now - _model_cache["fetched_at"] < MODEL_CACHE_TTL):
        return _model_cache["models"]

    result = await _rpc_call("model/list", {"limit": 50}, timeout)
    if not result.get("ok"):
        log.warning(f"[codex_models] fetch failed: {result.get('message')}")
        return _model_cache["models"] or []

    models = []
    for item in result["data"].get("data", []):
        if item.get("hidden"):
            continue
        model_id = item.get("model") or item.get("id")
        if model_id:
            models.append(model_id)

    if models:
        _model_cache["models"] = models
        _model_cache["fetched_at"] = now
    return models
