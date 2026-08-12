"""沙箱联动测试：admin=danger-full-access / user=workspace-write。"""
import asyncio
import json
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
    def __init__(self):
        self.stdout = FakeStream([json.dumps({"type": "agent_message", "text": "ok"}).encode() + b"\n"])
        self.stderr = FakeStream([])
        self.returncode = None
        self.pid = 99999
        orig = self.stdout.readline

        async def readline_with_exit():
            line = await orig()
            if not line:
                self.returncode = 0
            return line

        self.stdout.readline = readline_with_exit

    async def wait(self):
        return self.returncode


class SandboxTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import agent_executor
        self.agent_executor = agent_executor
        self.captured = []

        async def fake_create_subprocess_exec(*args, **kwargs):
            self.captured.append(list(args))
            return FakeProcess()

        agent_executor.asyncio.create_subprocess_exec = fake_create_subprocess_exec
        agent_executor.save_session_async = lambda *a, **k: self._noop()
        agent_executor.extract_and_upload_resources = lambda *a, **k: None

    async def _noop(self):
        return None

    async def run_with_role(self, role, session_data):
        await self.agent_executor.execute_codex(
            chat_id=helpers.TEST_CHAT_ID,
            user_text="测试",
            message_id="om_t",
            bot_reply_msg_id=None,
            session_data=session_data,
            is_new_conversation=False,
            system_instruction="",
            final_prompt="测试",
            downloaded_file_name=None,
            download_success=False,
            running_processes={},
            role=role,
            silent=True,
        )
        return self.captured[-1]

    async def test_admin_full_access_new_thread(self):
        cmd = await self.run_with_role("admin", {"codex_conversation": "", "project": "Default"})
        self.assertIn("--sandbox", cmd)
        self.assertIn("danger-full-access", cmd)

    async def test_user_workspace_write_new_thread(self):
        cmd = await self.run_with_role("user", {"codex_conversation": "", "project": "Default"})
        self.assertIn("--sandbox", cmd)
        self.assertIn("workspace-write", cmd)

    async def test_admin_full_access_resume(self):
        cmd = await self.run_with_role("admin", {"codex_conversation": "thread_1", "project": "Default"})
        self.assertIn("resume", cmd)
        joined = " ".join(cmd)
        self.assertIn('sandbox_mode="danger-full-access"', joined)

    async def test_user_workspace_write_resume(self):
        cmd = await self.run_with_role("user", {"codex_conversation": "thread_1", "project": "Default"})
        self.assertIn("resume", cmd)
        joined = " ".join(cmd)
        self.assertIn('sandbox_mode="workspace-write"', joined)
