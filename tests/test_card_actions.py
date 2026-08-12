"""卡片按钮动作分发测试（内置域 / 定时任务域 / 插件面板域 / 插件兜底）。"""
import unittest
from types import SimpleNamespace

from tests import helpers

helpers.patch_database()
helpers.patch_lark()
helpers.patch_auth()


def make_data(action_value):
    return SimpleNamespace(event=SimpleNamespace(
        action=SimpleNamespace(value=action_value),
        context=SimpleNamespace(open_chat_id=helpers.TEST_CHAT_ID, open_message_id="om_t"),
        operator=None,
    ))


class CardActionsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import app_state
        from handlers import events, card_actions

        self.app_state = app_state
        self.events = events
        self.card_actions = card_actions
        app_state.main_loop = self._loop = __import__("asyncio").get_running_loop()

        events.get_session_async = lambda c: self._coro_dict()
        events.save_session_async = lambda *a, **k: self._coro_none()
        events.handle_slash_command = lambda *a, **k: self._coro_done()
        events._handle_message_async_internal = lambda *a, **k: self._coro_none()
        events.send_reply_sdk = lambda m, t: None
        events.send_interactive_card_sdk = lambda m, c: None
        events.patch_interactive_card_sdk = lambda m, c: None
        events.get_profile_async = lambda c: self._coro_list()
        events.save_profile_async = lambda *a, **k: self._coro_none()
        card_actions.get_session_async = lambda c: self._coro_dict()
        card_actions.save_session_async = lambda *a, **k: self._coro_none()
        card_actions.send_reply_sdk = lambda m, t: None
        card_actions.patch_interactive_card_sdk = lambda m, c: None

    async def _coro_dict(self):
        return {}

    async def _coro_none(self):
        return None

    async def _coro_list(self):
        return []

    async def _coro_done(self):
        return True, ""

    def toast(self, action_value):
        resp = self.events.do_p2_card_action_trigger(make_data(action_value))
        return resp.toast.content if resp and resp.toast else ""

    def test_builtin_actions(self):
        self.assertTrue(self.toast({"action": "user_choice", "choice": "A", "label": "A"}).startswith("已确认"))
        self.assertEqual(self.toast({"action": "select_project", "path": "/tmp"}), "项目设定成功！")
        self.assertIn("模型已切换", self.toast({"action": "switch_model", "model": "m1"}))
        self.assertEqual(self.toast({"action": "delete_note", "index": 0}), "已成功删除该条笔记！")
        self.assertEqual(self.toast({"action": "refresh_status"}), "状态已刷新！")

    def test_cron_domain(self):
        self.assertIn("已切换至", self.toast({"action": "switch_cron_tab", "tab": "system"}))
        self.assertIn("请发送格式", self.toast({"action": "open_cron_create"}))

    def test_plugin_panel_domain(self):
        self.assertIn("已切换至插件源", self.toast({"action": "switch_plugin_tab", "tab": "sources"}))
        self.assertIn("默认官方插件源", self.toast({"action": "prompt_add_source"}))

    async def test_plugin_fallback(self):
        from plugin_manager import plugin_manager

        handled = []

        class FakePlugin:
            plugin_id = "fake"

            async def on_card_action(self, action, value, chat_id, card_message_id):
                handled.append(action)
                return True

        original = plugin_manager.plugins
        plugin_manager.plugins = {"fake": FakePlugin()}
        try:
            self.assertEqual(self.toast({"action": "set_rpi_light", "state": "thinking"}), "已处理")
            await __import__("asyncio").sleep(0.3)
            self.assertEqual(handled, ["set_rpi_light"])
        finally:
            plugin_manager.plugins = original
