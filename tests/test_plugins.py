"""插件系统冒烟测试（6 插件加载 + 核心功能，全部 mock 外部 IO）。"""
import unittest

from tests import helpers

helpers.patch_database()
helpers.patch_lark()

from plugin_manager import PluginManager


class PluginsTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # 运行时重新 patch（其他模块可能已 restore），确保插件动态加载时绑定假数据
        helpers.patch_database()
        helpers.patch_lark()
        cls.manager = PluginManager()
        cls.manager.load_all_plugins()

    async def asyncSetUp(self):
        helpers.sent_cards.clear()
        helpers.sent_texts.clear()
        helpers.patched_cards.clear()

    def test_loading(self):
        self.assertEqual(len(self.manager.plugins), 6)
        self.assertEqual(
            sorted(self.manager.command_map),
            ["/cron", "/health", "/led", "/light", "/memory", "/note", "/notes",
             "/schedule", "/sysinfo", "/update"],
        )

    async def test_server_health(self):
        plugin = self.manager.plugins["server_health"]
        card = plugin.build_health_card()
        self.assertTrue(card["header"]["title"]["content"].startswith("🖥️"))
        self.assertIn("CPU 负载", card["elements"][0]["content"])
        ok, _ = await self.manager.dispatch_command("/health", "om_h", helpers.TEST_CHAT_ID, {})
        self.assertTrue(ok)

    async def test_ai_memory(self):
        plugin = self.manager.plugins["ai_memory"]
        text, session = await plugin.on_before_ai("hi", helpers.TEST_CHAT_ID, {})
        self.assertEqual(session["memory_context"], "喜欢简洁回复")
        ok, _ = await self.manager.dispatch_command("/memory", "om_m", helpers.TEST_CHAT_ID, {})
        self.assertTrue(ok)

    async def test_notes_manager(self):
        session = {"notes": []}
        ok, _ = await self.manager.dispatch_command("/note add 测试笔记", "om_n", helpers.TEST_CHAT_ID, session)
        self.assertTrue(ok)
        self.assertEqual(session["notes"], ["测试笔记"])
        plugin = self.manager.plugins["notes_manager"]
        handled = await plugin.on_card_action("delete_note", {"index": 0}, helpers.TEST_CHAT_ID, "om_nc")
        self.assertTrue(handled)
        self.assertTrue(helpers.patched_cards)

    async def test_rpi_gpio_mock(self):
        plugin = self.manager.plugins["rpi_gpio_status"]
        ok, _ = await self.manager.dispatch_command("/light thinking", "om_l", helpers.TEST_CHAT_ID, {})
        self.assertTrue(ok)
        self.assertEqual(plugin.current_state, "thinking_solid_yellow")
        handled = await plugin.on_command("/stop", "", helpers.TEST_CHAT_ID, "om_s", {})
        self.assertFalse(handled)
        self.assertEqual(plugin.current_state, "solid_red_error")
        plugin.turn_all_off()

    async def test_cron_scheduler(self):
        ok, _ = await self.manager.dispatch_command("/cron", "om_c", helpers.TEST_CHAT_ID, {})
        self.assertTrue(ok)
        plugin = self.manager.plugins["cron_scheduler"]
        handled = await plugin.on_card_action("switch_cron_tab", {"tab": "system"}, helpers.TEST_CHAT_ID, "om_c2")
        self.assertTrue(handled)

    async def test_system_updater_command_fallback(self):
        # /update 由系统命令优先处理；插件命令表里存在但不会重复触发
        self.assertIn("/update", self.manager.command_map)
