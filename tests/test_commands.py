"""系统命令冒烟测试（mock 飞书发送 / 数据库 / RPC / git fetch）。"""
import unittest

from tests import helpers

helpers.patch_database()
helpers.patch_lark()
helpers.patch_codex_rpc()
helpers.patch_auth()
helpers.patch_subprocess_for_update()

import commands


class CommandsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 其他测试（如 test_auth）可能 restore 了真实 auth；命令测试需要管理员视角
        helpers.patch_auth()
        helpers.patch_codex_rpc()
        helpers.sent_cards.clear()
        helpers.sent_texts.clear()

    async def handle(self, user_text, session=None, msg_id="om_t"):
        session = session if session is not None else {}
        return await commands.handle_slash_command(
            user_text, msg_id, helpers.TEST_CHAT_ID, session, {}, {}, chat_workers={}
        )

    async def test_basic_commands(self):
        ok, _ = await self.handle("/ping")
        self.assertTrue(ok)
        self.assertEqual(helpers.sent_texts[-1], "🏓 Pong! 核心系统运行正常，网络连接畅通。")

        session = {"conversation": "x", "codex_conversation": "t", "context_usage": {"turns": 3}}
        ok, _ = await self.handle("/clear", session)
        self.assertTrue(ok)
        self.assertEqual(session["conversation"], "")
        self.assertEqual(helpers.sent_texts[-1], "🔄 上下文已清空，开启新对话！")

        ok, _ = await self.handle("/memory", {})
        self.assertTrue(ok)
        self.assertIn("偏好记忆管理器", helpers.card_title(helpers.sent_cards[-1]))

    async def test_notes(self):
        session = {"notes": []}
        ok, _ = await self.handle("/note add 买牛奶", session)
        self.assertTrue(ok)
        self.assertEqual(session["notes"], ["买牛奶"])
        ok, _ = await self.handle("/note 写周报", session)
        self.assertEqual(session["notes"], ["买牛奶", "写周报"])
        ok, _ = await self.handle("/note del 1", session)
        self.assertEqual(session["notes"], ["写周报"])
        ok, _ = await self.handle("/notes", session)
        self.assertIn("机器人记事本", helpers.card_title(helpers.sent_cards[-1]))
        ok, _ = await self.handle("/note clear", session)
        self.assertEqual(session["notes"], [])

    async def test_panels(self):
        ok, _ = await self.handle("/context", {"context_usage": {"turns": 1}, "codex_conversation": "t"})
        self.assertTrue(ok)
        ok, _ = await self.handle("/status", {})
        self.assertTrue(ok)
        ok, _ = await self.handle("/help", {})
        self.assertTrue(helpers.card_title(helpers.sent_cards[-1]))
        ok, _ = await self.handle("/project", {})
        self.assertTrue(ok)
        ok, _ = await self.handle("/model", {})
        self.assertIn("模型", helpers.card_title(helpers.sent_cards[-1]))
        ok, _ = await self.handle("/quota", {})
        self.assertIn("额度", helpers.card_title(helpers.sent_cards[-1]))
        ok, _ = await self.handle("/cron", {})
        self.assertIn("计划任务", helpers.card_title(helpers.sent_cards[-1]))

    async def test_project_path(self):
        session = {}
        ok, _ = await self.handle("/project /Users/user/codex-feishu-bot", session)
        self.assertTrue(ok)
        self.assertEqual(session["workspace_root"], "/Users/user/codex-feishu-bot")
        ok, _ = await self.handle("/project /no/such/dir/xyz", session)
        self.assertIn("不存在", helpers.sent_texts[-1])

    async def test_auth_user(self):
        ok, _ = await self.handle("/auth", {})
        self.assertIn("已是管理员", helpers.sent_texts[-1])
        ok, _ = await self.handle("/user", {})
        self.assertIn("用户", helpers.card_title(helpers.sent_cards[-1]))
        ok, _ = await self.handle("/user grant oc_x basic", {})
        self.assertIn("已授权 oc_x", helpers.sent_texts[-1])

    async def test_update_check(self):
        ok, _ = await self.handle("/update", {})
        self.assertTrue(ok)
        self.assertTrue(
            any("已是最新版本" in helpers.card_title(c) for c in helpers.sent_cards),
            "expected no-update card",
        )

    async def test_pending_flows(self):
        session = {"pending_command": "note_add", "notes": []}
        ok, _ = await self.handle("下午开会", session)
        self.assertEqual(session["notes"], ["下午开会"])
        self.assertNotIn("pending_command", session)

        session = {"pending_command": "remember"}
        ok, _ = await self.handle("用 Python", session)
        self.assertIn("已为您永久记录偏好", helpers.sent_texts[-1])

        session = {"pending_command": "project"}
        ok, _ = await self.handle("/Users/user/codex-feishu-bot", session)
        self.assertEqual(session["project"], "/Users/user/codex-feishu-bot")

        session = {"pending_command": "cron_add"}
        ok, _ = await self.handle("测试任务 | 30s | 回复：ok", session)
        self.assertIn("计划任务", helpers.card_title(helpers.sent_cards[-1]))

    async def test_unknown_command(self):
        ok, _ = await self.handle("/not_a_command", {})
        self.assertIn("未知指令", helpers.sent_texts[-1])
