"""执行器修复回归：线程损坏自动恢复 / 定时任务会话隔离 / 防误触发。"""
import asyncio
import json
import types
import unittest

from tests import helpers

helpers.patch_lark()


class FakeStream:
    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""

    def _transport(self):
        return None


class FakeProcess:
    def __init__(self, stdout_lines, auto_exit=True):
        self.stdout = FakeStream(stdout_lines)
        self.stderr = FakeStream([])
        self.returncode = None
        self.pid = 99999
        if auto_exit:
            orig = self.stdout.readline

            async def readline_with_exit():
                line = await orig()
                if not line:
                    self.returncode = 0
                return line

            self.stdout.readline = readline_with_exit

    async def wait(self):
        return self.returncode


class ExecutorFixesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import agent_executor
        self.agent_executor = agent_executor

    async def run_codex(self, session_data, process_queue, silent=True):
        captured = []

        async def fake_create_subprocess_exec(*args, **kwargs):
            captured.append(list(args))
            return process_queue.pop(0)

        self.agent_executor.asyncio.create_subprocess_exec = fake_create_subprocess_exec
        self.agent_executor.save_session_async = lambda *a, **k: self._noop()
        self.agent_executor.extract_and_upload_resources = lambda *a, **k: None
        result = await self.agent_executor.execute_codex(
            chat_id=helpers.TEST_CHAT_ID,
            user_text="测试消息",
            message_id="om_test",
            bot_reply_msg_id=None,
            session_data=session_data,
            is_new_conversation=False,
            system_instruction="",
            final_prompt="测试消息",
            downloaded_file_name=None,
            download_success=False,
            running_processes={},
            role="admin",
            silent=silent,
        )
        return result, captured

    async def _noop(self):
        return None

    async def test_thread_corruption_recovery(self):
        err = json.dumps({"type": "error", "message": "No tool output found for tool call xyz"}).encode() + b"\n"
        ok = json.dumps({"type": "agent_message", "text": "新线程回复成功"}).encode() + b"\n"
        session = {"codex_conversation": "corrupt_thread", "project": "Default"}
        (failed, reply), captured = await self.run_codex(session, [FakeProcess([err]), FakeProcess([ok])])
        self.assertEqual(len(captured), 2, "必须恰好重试一次")
        self.assertIn("resume", captured[0])
        self.assertNotIn("resume", captured[1], "重试必须使用新线程")
        self.assertFalse(failed)
        self.assertIn("新线程回复成功", reply)
        self.assertEqual(session["codex_conversation"], "", "损坏线程必须被清空")

    async def test_no_retry_on_unrelated_error(self):
        err = json.dumps({"type": "error", "message": "rate limit exceeded"}).encode() + b"\n"
        session = {"codex_conversation": "thread_ok", "project": "Default"}
        (failed, reply), captured = await self.run_codex(session, [FakeProcess([err])])
        self.assertEqual(len(captured), 1, "无关错误不应触发重试")
        self.assertTrue(failed)
        self.assertIn("rate limit exceeded", reply)
        self.assertEqual(session["codex_conversation"], "thread_ok", "会话不应被清空")

    async def test_cron_session_isolation(self):
        import cron_engine

        calls = []

        async def fake_execute_codex(**kwargs):
            calls.append(kwargs)
            return False, "cron 执行成功"

        original_exec = self.agent_executor.execute_codex
        original_get = cron_engine.get_session_async
        original_send = cron_engine.send_card_to_chat_sdk
        original_status = cron_engine.update_cron_task_status
        original_run = cron_engine.update_cron_task_run
        original_log = cron_engine.record_cron_log

        self.agent_executor.execute_codex = fake_execute_codex
        async def fake_get_session(chat_id):
            return {"codex_conversation": "user_thread", "project": "Default"}
        cron_engine.get_session_async = fake_get_session
        cron_engine.send_card_to_chat_sdk = lambda *a, **k: None
        cron_engine.update_cron_task_status = lambda *a, **k: None
        cron_engine.update_cron_task_run = lambda *a, **k: None
        cron_engine.record_cron_log = lambda *a, **k: None

        try:
            engine = cron_engine.CronEngine()
            engine._running_tasks.add("t1")
            await engine._run_task_wrapper({
                "id": "t1", "chat_id": helpers.TEST_CHAT_ID, "name": "测试",
                "cron_expr": "5s", "task_type": "delay", "prompt": "回复ok",
                "next_run_at": 1, "project_path": "",
            })
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["session_data"]["codex_conversation"], "", "cron 必须用全新线程")
            self.assertTrue(calls[0]["silent"])
        finally:
            self.agent_executor.execute_codex = original_exec
            cron_engine.get_session_async = original_get
            cron_engine.send_card_to_chat_sdk = original_send
            cron_engine.update_cron_task_status = original_status
            cron_engine.update_cron_task_run = original_run
            cron_engine.record_cron_log = original_log
