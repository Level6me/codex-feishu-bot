"""权限门禁测试：引导 / 角色 / 申请流程 / 限流 / 访客静默（真实临时 DB）。"""
import asyncio
import os
import sqlite3
import unittest

from tests import helpers

helpers.patch_lark()

import lark_client

async def _coro_str():
    return ""

lark_client.get_chat_name_async = lambda c: _coro_str()
lark_client.get_user_name_async = lambda u: _coro_str()

import utils.auth
from utils.auth import (
    allow_message, get_role, has_scope, is_admin, request_access,
    set_session_role, try_bootstrap_admin,
)


def clean_db():
    conn = sqlite3.connect(os.environ["DB_FILE"])
    conn.execute("DELETE FROM auth_sessions")
    conn.execute("DELETE FROM bot_meta")
    conn.commit()
    conn.close()


class AuthTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        clean_db()
        # 其他测试模块可能 patch 了 auth；本测试需要真实权限逻辑
        helpers.restore_auth()
        helpers.sent_cards.clear()
        helpers.sent_texts.clear()

    def test_bootstrap(self):
        self.assertFalse(utils.auth.is_bootstrapped())
        self.assertTrue(try_bootstrap_admin("oc_admin", "p2p"))
        self.assertTrue(utils.auth.is_bootstrapped())
        self.assertFalse(try_bootstrap_admin("oc_other", "p2p"), "已引导后不能再绑定")
        self.assertEqual(utils.auth.get_admin_chat_id(), "oc_admin")
        self.assertTrue(is_admin("oc_admin"))
        self.assertEqual(get_role("oc_admin"), "admin")

    def test_bootstrap_group_flag(self):
        clean_db()
        original = utils.auth.AUTH_BOOTSTRAP_ALLOW_GROUP
        utils.auth.AUTH_BOOTSTRAP_ALLOW_GROUP = False
        try:
            self.assertFalse(try_bootstrap_admin("oc_group", "group"), "群聊绑定被关闭时应拒绝")
        finally:
            utils.auth.AUTH_BOOTSTRAP_ALLOW_GROUP = original
        self.assertTrue(try_bootstrap_admin("oc_group", "group"), "默认允许群聊绑定")

    def test_roles_and_scopes(self):
        try_bootstrap_admin("oc_admin", "p2p")
        set_session_role("oc_user", "user", list(utils.auth.SCOPE_TIERS["basic"]))
        self.assertEqual(get_role("oc_user"), "user")
        self.assertTrue(has_scope("oc_user", "chat"))
        self.assertFalse(has_scope("oc_user", "shell"))
        self.assertTrue(has_scope("oc_admin", "shell"), "admin 拥有全部作用域")
        set_session_role("oc_user", "banned", [])
        self.assertEqual(get_role("oc_user"), "banned")

    def test_request_access_flow(self):
        clean_db()
        self.assertEqual(request_access("oc_new", "p2p", "ou_1"), "ok")
        self.assertEqual(get_role("oc_new"), "pending")
        self.assertEqual(request_access("oc_new", "p2p", "ou_1"), "rate", "600 秒内重复申请应限流")

        clean_db()
        try_bootstrap_admin("oc_admin", "p2p")
        set_session_role("oc_user", "user", [])
        self.assertEqual(request_access("oc_user", "p2p", "ou_2"), "already")
        self.assertEqual(request_access("oc_admin", "p2p", ""), "admin")
        set_session_role("oc_bad", "banned", [])
        self.assertEqual(request_access("oc_bad", "p2p", ""), "banned")

    def test_rate_limit(self):
        try_bootstrap_admin("oc_admin", "p2p")
        set_session_role("oc_user", "user", [])
        for _ in range(5):
            self.assertTrue(allow_message("oc_user"))
        self.assertFalse(allow_message("oc_user"), "第 6 条应被限流")
        self.assertTrue(allow_message("oc_admin"), "管理员不受限流")

    async def test_guest_silent_flow(self):
        from handlers.messages import _handle_guest_message

        # guest 发普通消息：收到一次性提示卡（24h 内只一次）
        await _handle_guest_message("oc_guest", "p2p", "ou_g", "guest", "text", '{"text":"你好"}')
        self.assertEqual(len(helpers.sent_cards), 1, "guest 首次应收到授权提示卡")
        # 再次普通消息：静默
        helpers.sent_cards.clear()
        await _handle_guest_message("oc_guest", "p2p", "ou_g", "guest", "text", '{"text":"你好"}')
        self.assertEqual(len(helpers.sent_cards), 0, "24h 内重复消息应静默")

        # guest 发 /auth：触发申请通知
        helpers.sent_cards.clear()
        await _handle_guest_message("oc_guest", "p2p", "ou_g", "guest", "text", '{"text":"/auth"}')
        self.assertTrue(
            any("授权申请" in t for t in helpers.sent_texts),
            "申请应回复提示文本",
        )

        # pending：普通消息完全静默
        helpers.sent_cards.clear()
        helpers.sent_texts.clear()
        await _handle_guest_message("oc_pending", "p2p", "ou_p", "pending", "text", '{"text":"你好"}')
        self.assertEqual(len(helpers.sent_cards), 0)
        self.assertEqual(len(helpers.sent_texts), 0)

    async def test_admin_welcome_and_rate_hint(self):
        from handlers.messages import _admin_welcome, _rate_hint

        await _admin_welcome("oc_admin")
        self.assertGreaterEqual(len(helpers.sent_cards), 1)
        helpers.sent_cards.clear()
        await _rate_hint("oc_user")
        self.assertGreaterEqual(len(helpers.sent_cards), 1)
