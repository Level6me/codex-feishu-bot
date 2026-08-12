"""卡片构建兼容层（阶段 5 第 3 步：实现迁至 cards/*，CardBuilder 转发）。
调用方继续使用 `CardBuilder.build_xxx(...)`，接口保持不变。
"""
import re
import os
from datetime import datetime
from config import WORKSPACE_ROOT
from logger import log
from utils.auth import SCOPE_TIERS

from cards.constants import TIER_LABELS, ROLE_LABELS, TIER_NOTES
from cards import common, model, chat, project, notes, memory, system, auth, cron, plugin


class CardBuilder:
    @staticmethod
    def _create_footer(*args, **kwargs):
        return common._create_footer(*args, **kwargs)

    @staticmethod
    def build_model_panel(*args, **kwargs):
        return model.build_model_panel(*args, **kwargs)

    @staticmethod
    def build_model_switch_result_card(*args, **kwargs):
        return model.build_model_switch_result_card(*args, **kwargs)

    @staticmethod
    def _guess_intent(*args, **kwargs):
        return common._guess_intent(*args, **kwargs)

    @staticmethod
    def _get_dynamic_think_text(*args, **kwargs):
        return common._get_dynamic_think_text(*args, **kwargs)

    @staticmethod
    def build_typing_indicator(*args, **kwargs):
        return common.build_typing_indicator(*args, **kwargs)

    @staticmethod
    def build_tool_indicator(*args, **kwargs):
        return common.build_tool_indicator(*args, **kwargs)

    @staticmethod
    def build_download_indicator(*args, **kwargs):
        return common.build_download_indicator(*args, **kwargs)

    @staticmethod
    def build_streaming_indicator(*args, **kwargs):
        return common.build_streaming_indicator(*args, **kwargs)

    @staticmethod
    def build_stall_warning_card(*args, **kwargs):
        return common.build_stall_warning_card(*args, **kwargs)

    @staticmethod
    def build_stall_error_card(*args, **kwargs):
        return common.build_stall_error_card(*args, **kwargs)

    @staticmethod
    def build_ai_response(*args, **kwargs):
        return chat.build_ai_response(*args, **kwargs)

    @staticmethod
    def build_dir_browser_card(*args, **kwargs):
        return project.build_dir_browser_card(*args, **kwargs)

    @staticmethod
    def build_no_update_card(*args, **kwargs):
        return system.build_no_update_card(*args, **kwargs)

    @staticmethod
    def build_update_card(*args, **kwargs):
        return system.build_update_card(*args, **kwargs)

    @staticmethod
    def build_welcome_card(*args, **kwargs):
        return chat.build_welcome_card(*args, **kwargs)

    @staticmethod
    def build_memory_card(*args, **kwargs):
        return memory.build_memory_card(*args, **kwargs)

    @staticmethod
    def build_security_warning(*args, **kwargs):
        return chat.build_security_warning(*args, **kwargs)

    @staticmethod
    def build_note_list_card(*args, **kwargs):
        return notes.build_note_list_card(*args, **kwargs)

    @staticmethod
    def build_help_card(*args, **kwargs):
        return chat.build_help_card(*args, **kwargs)

    @staticmethod
    def build_status_card(*args, **kwargs):
        return system.build_status_card(*args, **kwargs)

    @staticmethod
    def build_global_memory_card(*args, **kwargs):
        return memory.build_global_memory_card(*args, **kwargs)

    @staticmethod
    def build_quota_card(*args, **kwargs):
        return system.build_quota_card(*args, **kwargs)

    @staticmethod
    def build_context_card(*args, **kwargs):
        return system.build_context_card(*args, **kwargs)

    @staticmethod
    def build_brain_card(*args, **kwargs):
        return system.build_brain_card(*args, **kwargs)

    @staticmethod
    def _short_id(*args, **kwargs):
        return common._short_id(*args, **kwargs)

    @staticmethod
    def _tier_label(*args, **kwargs):
        return common._tier_label(*args, **kwargs)

    @staticmethod
    def _fmt_ts(*args, **kwargs):
        return common._fmt_ts(*args, **kwargs)

    @staticmethod
    def build_auth_hint_card(*args, **kwargs):
        return auth.build_auth_hint_card(*args, **kwargs)

    @staticmethod
    def build_admin_welcome_card(*args, **kwargs):
        return auth.build_admin_welcome_card(*args, **kwargs)

    @staticmethod
    def build_auth_request_card(*args, **kwargs):
        return auth.build_auth_request_card(*args, **kwargs)

    @staticmethod
    def build_auth_result_card(*args, **kwargs):
        return auth.build_auth_result_card(*args, **kwargs)

    @staticmethod
    def build_user_edit_card(*args, **kwargs):
        return auth.build_user_edit_card(*args, **kwargs)

    @staticmethod
    def build_user_panel_card(*args, **kwargs):
        return auth.build_user_panel_card(*args, **kwargs)

    @staticmethod
    def build_rate_limit_card(*args, **kwargs):
        return auth.build_rate_limit_card(*args, **kwargs)

    @staticmethod
    def _format_ts(*args, **kwargs):
        return common._format_ts(*args, **kwargs)

    @staticmethod
    def build_cron_panel_card(*args, **kwargs):
        return cron.build_cron_panel_card(*args, **kwargs)

    @staticmethod
    def build_cron_start_card(*args, **kwargs):
        return cron.build_cron_start_card(*args, **kwargs)

    @staticmethod
    def build_cron_execution_card(*args, **kwargs):
        return cron.build_cron_execution_card(*args, **kwargs)

    @staticmethod
    def build_cron_created_card(*args, **kwargs):
        return cron.build_cron_created_card(*args, **kwargs)

    @staticmethod
    def build_plugin_panel_card(*args, **kwargs):
        return plugin.build_plugin_panel_card(*args, **kwargs)
