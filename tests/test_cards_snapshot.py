"""卡片构建快照测试：41 个 CardBuilder 方法输出与基线完全一致。"""
import json
import os
import unittest

from card_builder import CardBuilder
from tests import helpers

BASELINE = os.path.join(os.path.dirname(__file__), "baseline", "cards_baseline.json")


def collect():
    """调用全部 41 个 CardBuilder 方法（代表性参数）。"""
    out = {}
    out["_create_footer"] = CardBuilder._create_footer()
    out["build_model_panel"] = CardBuilder.build_model_panel(["gpt-5.1-codex", "deepseek-v4-flash"], "deepseek-v4-flash")
    out["build_model_switch_result_card"] = CardBuilder.build_model_switch_result_card("deepseek-v4-flash", "gpt-5.1-codex")
    out["_guess_intent"] = CardBuilder._guess_intent("打开 /tmp/hello.py")
    out["_get_dynamic_think_text"] = CardBuilder._get_dynamic_think_text("AI 思考中", 3)
    out["build_typing_indicator"] = CardBuilder.build_typing_indicator("test.txt", True, "你好", 3)
    out["build_tool_indicator"] = CardBuilder.build_tool_indicator("执行命令", "你好", "test.txt", True, 3)
    out["build_download_indicator"] = CardBuilder.build_download_indicator("test.pdf", "文件")
    out["build_streaming_indicator"] = CardBuilder.build_streaming_indicator("正在生成的部分文本", "执行命令", "你好", 3)
    out["build_stall_warning_card"] = CardBuilder.build_stall_warning_card("测试提示", 200, 190)
    out["build_stall_error_card"] = CardBuilder.build_stall_error_card("测试提示", 650, 620)
    out["build_ai_response"] = CardBuilder.build_ai_response(
        "回复内容", choice_card_data={"question": "请选择：", "options": ["选项1", "选项2"]},
        current_model="deepseek-v4-flash", current_project="默认", is_error=False, is_streaming=False,
    )
    out["build_dir_browser_card"] = CardBuilder.build_dir_browser_card("/tmp", ["/tmp/a", "/tmp/b"], 1, "/tmp", [])
    out["build_no_update_card"] = CardBuilder.build_no_update_card("v1.0.1")
    out["build_update_card"] = CardBuilder.build_update_card("v1.0.1", "v1.0.2", "- fix: something")
    out["build_welcome_card"] = CardBuilder.build_welcome_card()
    out["build_memory_card"] = CardBuilder.build_memory_card(["偏好一", "偏好二"])
    out["build_security_warning"] = CardBuilder.build_security_warning("rm -rf /")
    out["build_note_list_card"] = CardBuilder.build_note_list_card(["买牛奶", "写周报"])
    out["build_help_card"] = CardBuilder.build_help_card()
    out["build_status_card"] = CardBuilder.build_status_card(0.12, 4096, "1天2小时", "online", 3, "无", "clean", {"version": "v1.0.1"})
    out["build_global_memory_card"] = CardBuilder.build_global_memory_card([{"text": "全局记忆1", "time": "2026-08-12"}])
    out["build_quota_card"] = CardBuilder.build_quota_card(
        {"ok": True, "data": {"rate_limits": [{"name": "GPT-5", "period": "week", "used": 10, "limit": 50}]}},
        {"ok": True, "data": {"account": {"email": "u@example.com", "plan": "Plus"}}},
    )
    out["build_context_card"] = CardBuilder.build_context_card(
        {"total_input_tokens": 1000, "total_output_tokens": 500, "turns": 3}, "thread_1",
    )
    out["build_brain_card"] = CardBuilder.build_brain_card("# 记忆内容", "/tmp/AGENTS.md")
    out["_short_id"] = CardBuilder._short_id("oc_bf2eaae8c596319ccf1507218952cb5b")
    out["_tier_label"] = CardBuilder._tier_label(["对话", "项目切换"])
    out["_fmt_ts"] = CardBuilder._fmt_ts(1786500000)
    out["_format_ts"] = CardBuilder._format_ts(1786500000)
    out["build_auth_hint_card"] = CardBuilder.build_auth_hint_card()
    out["build_admin_welcome_card"] = CardBuilder.build_admin_welcome_card()
    out["build_auth_request_card"] = CardBuilder.build_auth_request_card({"chat_id": "oc_x", "display_name": "测试用户"})
    out["build_auth_result_card"] = CardBuilder.build_auth_result_card(True, "授权成功")
    out["build_user_edit_card"] = CardBuilder.build_user_edit_card({"chat_id": "oc_x", "role": "user", "scopes": "[]"})
    out["build_user_panel_card"] = CardBuilder.build_user_panel_card([], 1, 6)
    out["build_rate_limit_card"] = CardBuilder.build_rate_limit_card()
    out["build_cron_panel_card"] = CardBuilder.build_cron_panel_card([], "user", {})
    out["build_cron_start_card"] = CardBuilder.build_cron_start_card(
        {"name": "测试任务", "id": "t1", "category": "user", "cron_expr": "30s", "prompt": "回复ok"},
    )
    out["build_cron_execution_card"] = CardBuilder.build_cron_execution_card(
        {"name": "测试任务", "id": "t1", "category": "user", "cron_expr": "30s", "prompt": "回复ok"},
        "执行结果", is_error=False, duration_ms=1234,
    )
    out["build_cron_created_card"] = CardBuilder.build_cron_created_card(
        {"name": "测试任务", "id": "t1", "category": "user", "task_type": "delay", "cron_expr": "30s",
         "prompt": "回复ok", "next_run_at": 1786500000, "last_run_at": 0, "run_count": 0},
    )
    out["build_plugin_panel_card"] = CardBuilder.build_plugin_panel_card(
        [{"id": "ai_memory", "name": "记忆", "version": "1.0.0", "commands": ["/memory"], "enabled": True}],
        "installed",
    )
    return helpers.sanitize(out)


class CardsSnapshotTest(unittest.TestCase):
    def test_all_methods_match_baseline(self):
        with open(BASELINE, encoding="utf-8") as f:
            baseline = json.load(f)
        current = collect()
        diffs = [k for k in current if baseline.get(k) != current[k]]
        self.assertEqual(diffs, [], f"卡片输出与基线不一致: {diffs}")
        self.assertEqual(len(current), len(baseline), "方法数量变化")


if __name__ == "__main__":
    # 生成/更新基线：python3 -m tests.test_cards_snapshot --gen
    import sys
    if "--gen" in sys.argv:
        os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(collect(), f, ensure_ascii=False, sort_keys=True, indent=1)
        print(f"baseline written: {BASELINE}")
    else:
        unittest.main()
