"""消息去重持久化 + 飞书发文件工具 + reaction 处理器测试。"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

from tests import helpers

helpers.patch_lark()

import app_state
import database


class DedupTest(unittest.TestCase):
    """DB 持久化去重：跨"重启"（清空内存缓存）仍能拦截重放消息。"""

    def setUp(self):
        # 清空内存缓存，模拟服务重启
        app_state._SEEN_MESSAGE_IDS.clear()
        self.mid = f"dedup_{time.time_ns()}"

    def test_new_message_persisted(self):
        self.assertTrue(app_state._mark_seen(self.mid, "oc_dedup", int(time.time())))
        self.assertFalse(app_state._mark_seen(self.mid, "oc_dedup", int(time.time())))

    def test_duplicate_survives_restart(self):
        self.assertTrue(app_state._mark_seen(self.mid, "oc_dedup", int(time.time())))
        # 模拟重启：清空内存后同一消息仍被 DB 拦截
        app_state._SEEN_MESSAGE_IDS.clear()
        self.assertFalse(app_state._mark_seen(self.mid, "oc_dedup", int(time.time())))

    def test_distinct_message_allowed_after_restart(self):
        self.assertTrue(app_state._mark_seen(self.mid, "oc_dedup", int(time.time())))
        app_state._SEEN_MESSAGE_IDS.clear()
        other = f"{self.mid}_other"
        self.assertTrue(app_state._mark_seen(other, "oc_dedup", int(time.time())))


class SendToFeishuTest(unittest.TestCase):
    """send_to_feishu.py 脚本逻辑：默认会话定位 + 发送路径。"""

    def setUp(self):
        import send_to_feishu
        self.mod = send_to_feishu
        # 清空 recent_messages 干扰项，保证默认会话定位可预期
        with database.get_db() as conn:
            conn.execute("DELETE FROM recent_messages")

    def test_resolve_default_chat_fallback(self):
        # recent_messages 为空时回退 chat_sessions
        with database.get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO chat_sessions (chat_id, data) VALUES (?, ?)",
                         ("oc_send_default", "{}"))
        self.assertEqual(self.mod.resolve_default_chat_id(), "oc_send_default")

    def test_resolve_default_prefers_recent_messages(self):
        with database.get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO recent_messages (message_id, chat_id, create_time) VALUES (?, ?, ?)",
                         ("m_recent", "oc_recent", int(time.time())))
        self.assertEqual(self.mod.resolve_default_chat_id(), "oc_recent")

    def test_send_missing_file_exits_error(self):
        with mock.patch.object(self.mod, "send_local_file_to_chat") as mock_send:
            ok, msg = self.mod.send_file("/nonexistent/report.md", "oc_send")
        self.assertFalse(ok)
        self.assertIn("文件不存在", msg)
        mock_send.assert_not_called()

    def test_send_success(self):
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tmp:
            tmp.write("# 测试文档".encode("utf-8"))
            path = tmp.name
        try:
            with mock.patch.object(self.mod, "send_local_file_to_chat", return_value=True) as mock_send:
                ok, msg = self.mod.send_file(path, "oc_send", caption="说明")
            self.assertTrue(ok)
            mock_send.assert_called_once()
            self.assertEqual(mock_send.call_args[0][0], "oc_send")
            self.assertEqual(mock_send.call_args[0][1], path)
            self.assertEqual(mock_send.call_args.kwargs.get("caption"), "说明")
        finally:
            os.unlink(path)


class ReactionHandlerTest(unittest.TestCase):
    """表情回复事件处理器：纯忽略、返回成功，不再报 processor not found。"""

    def test_ignore_reaction_event(self):
        from main import _ignore_reaction_event
        resp = _ignore_reaction_event(None)
        self.assertEqual(resp["code"], 0)
        self.assertEqual(resp["msg"], "ignored")


if __name__ == "__main__":
    unittest.main()
