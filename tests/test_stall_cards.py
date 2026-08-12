"""停滞预警 / 卡死错误 / 流式打字机卡片 + 超时常量测试。"""
import unittest

from card_builder import CardBuilder


class StallCardsTest(unittest.TestCase):
    """三个新卡片方法的结构与按钮动作验证。"""

    def test_streaming_indicator_contains_partial_text(self):
        card = CardBuilder.build_streaming_indicator("正在生成的内容", "执行命令", "你好", 3)
        self.assertEqual(card["header"]["template"], "blue")
        self.assertIn("正在生成", card["header"]["title"]["content"])
        content = card["elements"][0]["content"]
        self.assertIn("正在生成的内容", content)
        self.assertIn("执行命令", content)
        self.assertIn("3s", content)

    def test_streaming_indicator_truncates_long_text(self):
        long_text = "长" * 5000
        card = CardBuilder.build_streaming_indicator(long_text)
        content = card["elements"][0]["content"]
        self.assertIn("前文已自动隐藏", content)
        self.assertLess(len(content), 3800)

    def test_stall_warning_card_buttons(self):
        card = CardBuilder.build_stall_warning_card("测试", 200, 190)
        self.assertEqual(card["header"]["template"], "orange")
        actions = card["elements"][1]["actions"]
        self.assertEqual(len(actions), 2)
        continue_btn = actions[0]["value"]
        stop_btn = actions[1]["value"]
        self.assertEqual(continue_btn["action"], "user_choice")
        self.assertEqual(continue_btn["choice"], "继续等待")
        self.assertEqual(stop_btn["action"], "user_choice")
        self.assertEqual(stop_btn["choice"], "/stop")

    def test_stall_error_card_buttons(self):
        card = CardBuilder.build_stall_error_card("请写一个测试脚本", 650, 620)
        self.assertEqual(card["header"]["template"], "red")
        actions = card["elements"][1]["actions"]
        self.assertEqual(len(actions), 2)
        retry_btn = actions[0]["value"]
        model_btn = actions[1]["value"]
        self.assertEqual(retry_btn["action"], "user_choice")
        self.assertEqual(retry_btn["choice"], "请写一个测试脚本")
        self.assertEqual(model_btn["action"], "user_choice")
        self.assertEqual(model_btn["choice"], "/model")

    def test_stall_error_prompt_truncated(self):
        card = CardBuilder.build_stall_error_card("长" * 200, 1, 2)
        retry_choice = card["elements"][1]["actions"][0]["value"]["choice"]
        self.assertEqual(len(retry_choice), 80)


class TimeoutConstantsTest(unittest.TestCase):
    """停滞/超时阈值与源项目对齐验证。"""

    def test_constants_aligned(self):
        import agent_executor
        self.assertEqual(agent_executor.STALL_TIMEOUT, 600)
        self.assertEqual(agent_executor.QUIET_WARNING_THRESHOLD, 180)
        self.assertEqual(agent_executor.GLOBAL_TIMEOUT, 43200)


if __name__ == "__main__":
    unittest.main()
