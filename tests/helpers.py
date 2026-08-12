"""测试公共工具：环境隔离 + 通用 mock。"""
import os
import re
import subprocess
import tempfile
import types

# ---- 数据库隔离：必须在任何业务模块 import 之前设置 ----
_TMP_DIR = tempfile.mkdtemp(prefix="codex_bot_tests_")
os.environ["DB_FILE"] = os.path.join(_TMP_DIR, "test.db")
os.environ.setdefault("DANGEROUSLY_SKIP_PERMISSIONS", "true")
os.environ.setdefault("AUTH_BOOTSTRAP_ALLOW_GROUP", "true")

TEST_CHAT_ID = "oc_test"

# ---- 全局捕获 ----
sent_cards = []
sent_texts = []
patched_cards = []


def fake_result(stdout="", returncode=0):
    r = types.SimpleNamespace()
    r.stdout = stdout
    r.stderr = ""
    r.returncode = returncode
    return r


def card_title(card):
    return (card.get("header") or {}).get("title", {}).get("content", "")


def sanitize(obj):
    """归一化时间戳，用于卡片快照稳定对比。"""
    _time_re = re.compile(r"(Powered by codex \| 🕒 )[\d\-: ]+")
    _ts_re = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    if isinstance(obj, str):
        return _ts_re.sub("TIME", _time_re.sub(r"\1TIME", obj))
    return obj


def patch_database():
    """把数据库读取函数替换为内存/固定假数据（插件与命令测试用）。"""
    import database

    for name in (
        "get_profile_async", "save_profile_async", "save_session_async",
        "get_session_async", "list_auth_sessions", "set_bot_meta",
        "get_all_cron_tasks", "save_cron_task", "get_and_clear_pending_update_notice",
    ):
        if name not in _DB_ORIGINALS:
            _DB_ORIGINALS[name] = getattr(database, name)

    async def noop(*a, **k):
        return None

    async def get_profile(chat_id):
        return ["喜欢简洁回复"]

    async def get_session(chat_id):
        return {"notes": ["已有笔记"]}

    database.get_profile_async = get_profile
    database.save_profile_async = noop
    database.save_session_async = noop
    database.get_session_async = get_session
    database.list_auth_sessions = lambda: []
    database.set_bot_meta = lambda *a, **k: None
    database.get_all_cron_tasks = lambda chat_id: []
    database.save_cron_task = lambda *a, **k: None
    database.get_and_clear_pending_update_notice = lambda: None


_DB_ORIGINALS = {}


def restore_database():
    """恢复被 patch_database 覆盖的真实 database 函数（需要真实 DB 的测试用）。"""
    import database

    for name, fn in _DB_ORIGINALS.items():
        setattr(database, name, fn)


def patch_lark():
    """把飞书发送函数替换为捕获器。"""
    import lark_client

    lark_client.send_reply_sdk = lambda m, t: sent_texts.append(t) or True
    lark_client.send_interactive_card_sdk = lambda m, c: sent_cards.append(c) or True
    lark_client.patch_interactive_card_sdk = lambda m, c: patched_cards.append((m, c)) or True
    lark_client.send_card_to_chat_sdk = lambda c, card: None
    lark_client.send_text_to_chat_sdk = lambda c, t: None
    lark_client.send_card_to_chat_async = lambda c, card: _coro_append_card(card)
    lark_client.send_text_to_chat_async = lambda c, t: _coro_append_text(t)
    lark_client.get_chat_name_async = lambda c: _coro_str()
    lark_client.get_user_name_async = lambda u: _coro_str()


async def _coro_append_card(card):
    sent_cards.append(card)
    return True


async def _coro_append_text(text):
    sent_texts.append(text)
    return True


async def _coro_str():
    return ""


def patch_codex_rpc():
    """mock codex_quota 的 RPC 查询。"""
    import codex_quota

    async def models(*a, **k):
        return []

    async def default_model(*a, **k):
        return ""

    async def quota(*a, **k):
        return {"ok": False, "error": "mock", "message": "mock"}

    codex_quota.fetch_codex_models = models
    codex_quota.fetch_codex_default_model = default_model
    codex_quota.fetch_codex_quota = quota
    codex_quota.fetch_codex_account = quota


def patch_auth():
    """把权限函数替换为管理员视角。"""
    import utils.auth

    for name in ("get_role", "get_admin_chat_id", "set_session_role", "start_display_name_refresh"):
        if name not in _AUTH_ORIGINALS:
            _AUTH_ORIGINALS[name] = getattr(utils.auth, name)

    utils.auth.get_role = lambda chat_id, sender_open_id="": "admin"
    utils.auth.get_admin_chat_id = lambda: "oc_admin"
    utils.auth.set_session_role = lambda *a, **k: None
    utils.auth.start_display_name_refresh = lambda s: _coro_none()


_AUTH_ORIGINALS = {}


def restore_auth():
    """恢复被 patch_auth 覆盖的真实权限函数（需要真实角色逻辑的测试用）。"""
    import utils.auth

    for name, fn in _AUTH_ORIGINALS.items():
        setattr(utils.auth, name, fn)


async def _coro_none():
    return None


def patch_subprocess_for_update():
    """拦截 git fetch/rev-parse，其余转发真实命令。"""
    calls = []
    orig_run = subprocess.run

    def fake_run(*args, **kwargs):
        cmd = args[0]
        if cmd[:2] == ["git", "fetch"]:
            calls.append(cmd)
            return fake_result()
        if cmd[:3] == ["git", "rev-parse", "--short"]:
            calls.append(cmd)
            return fake_result(stdout="abc1234\n")
        return orig_run(*args, **kwargs)

    subprocess.run = fake_run
    fake_run.calls = calls
    return fake_run
