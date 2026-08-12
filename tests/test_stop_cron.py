"""/stop 真实进程中断 + 定时任务生命周期测试。"""
import asyncio
import subprocess
import time
import unittest

from tests import helpers

helpers.patch_lark()

import app_state
import commands
import database


class StopCronTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        helpers.sent_texts.clear()
        # 其他测试（含运行时的 re-patch）可能覆盖 database；本测试需要真实 DB 函数
        helpers.restore_database()

    async def test_stop_interrupts_real_process(self):
        proc = subprocess.Popen(["sleep", "300"], start_new_session=True)
        app_state.running_processes[helpers.TEST_CHAT_ID] = proc
        app_state.chat_queues[helpers.TEST_CHAT_ID] = asyncio.Queue()
        await app_state.chat_queues[helpers.TEST_CHAT_ID].put({"message_id": "q1"})
        app_state.chat_workers[helpers.TEST_CHAT_ID] = asyncio.create_task(asyncio.sleep(30))

        handled, _ = await commands.handle_slash_command(
            "/stop", "om_stop", helpers.TEST_CHAT_ID, {},
            app_state.running_processes, app_state.chat_queues, chat_workers=app_state.chat_workers,
        )
        self.assertTrue(handled)
        self.assertIn("任务已被紧急叫停", helpers.sent_texts[-1])
        await asyncio.sleep(0.3)
        self.assertIsNotNone(proc.poll(), "子进程必须被终止")
        self.assertNotIn(helpers.TEST_CHAT_ID, app_state.running_processes)
        self.assertTrue(app_state.chat_queues[helpers.TEST_CHAT_ID].empty())
        self.assertNotIn(helpers.TEST_CHAT_ID, app_state.chat_workers)

    async def test_cron_lifecycle(self):
        ts = int(time.time())
        tid = f"task_usr_life_{ts}"
        data = {
            "id": tid, "chat_id": helpers.TEST_CHAT_ID, "category": "user", "name": "生命周期测试",
            "task_type": "delay", "cron_expr": "30s", "prompt": "回复ok", "project_path": "",
            "is_active": True, "created_by": helpers.TEST_CHAT_ID, "created_at": ts,
            "updated_at": ts, "last_run_at": 0, "next_run_at": ts + 30, "run_count": 0,
        }
        try:
            database.save_cron_task(data)
            self.assertTrue(any(t["id"] == tid for t in database.get_all_cron_tasks(helpers.TEST_CHAT_ID)))
            self.assertTrue(any(t["id"] == tid for t in database.get_active_cron_tasks()))

            database.update_cron_task_status(tid, False)
            self.assertFalse(any(t["id"] == tid for t in database.get_active_cron_tasks()))
            database.update_cron_task_status(tid, True)
            self.assertTrue(any(t["id"] == tid for t in database.get_active_cron_tasks()))

            database.update_cron_task_run(tid, ts, ts + 60)
            row = database.get_cron_task(tid)
            self.assertEqual(row["run_count"], 1)
            self.assertEqual(row["next_run_at"], ts + 60)
        finally:
            database.delete_cron_task(tid)
        self.assertFalse(any(t["id"] == tid for t in database.get_all_cron_tasks(helpers.TEST_CHAT_ID)))
