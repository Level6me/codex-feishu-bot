import os
import sys
import shutil
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

class Settings(BaseSettings):
    app_id: str = Field(default="", alias="APP_ID")
    feishu_app_id: str = Field(default="", alias="FEISHU_APP_ID")
    app_secret: str = Field(default="", alias="APP_SECRET")
    feishu_app_secret: str = Field(default="", alias="FEISHU_APP_SECRET")
    allowed_users: str = Field(default="", alias="ALLOWED_USERS")
    allowed_chats: str = Field(default="", alias="ALLOWED_CHATS")
    dangerously_skip_permissions: bool = Field(default=True, alias="DANGEROUSLY_SKIP_PERMISSIONS")
    # 自部署场景允许首个群聊也绑定管理员（上游仅 p2p 私聊）
    auth_bootstrap_allow_group: bool = Field(default=True, alias="AUTH_BOOTSTRAP_ALLOW_GROUP")
    workspace_root: str = Field(
        default_factory=lambda: os.path.expanduser("~"),
        alias="WORKSPACE_ROOT",
    )
    # 可选镜像源，用于 /update 在 GitHub 不可达时回退
    gitee_mirror_url: str = Field(default="", alias="GITEE_MIRROR_URL")
    agent_backend: str = Field(default="codex", alias="AGENT_BACKEND")
    codex_bin: str = Field(default="codex", alias="CODEX_BIN")
    codex_model: str = Field(default="", alias="CODEX_MODEL")
    codex_models: str = Field(
        default="gpt-5.1-codex,gpt-5.1-codex-mini,gpt-5.1,gpt-5-codex,gpt-5",
        alias="CODEX_MODELS",
    )
    git_mirror_url: str = Field(default="", alias="GIT_MIRROR_URL")

    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

settings = Settings()

APP_ID = settings.feishu_app_id or settings.app_id
APP_SECRET = settings.feishu_app_secret or settings.app_secret

SESSION_FILE = os.path.join(BASE_DIR, "chat_sessions.json")
PROFILE_FILE = os.path.join(BASE_DIR, "user_profiles.json")

AGENT_BACKEND = settings.agent_backend.strip().lower()
CODEX_BIN = shutil.which(settings.codex_bin) or settings.codex_bin
CODEX_MODEL = settings.codex_model.strip()
CODEX_MODELS = [m.strip() for m in settings.codex_models.split(",") if m.strip()]
GIT_MIRROR_URL = settings.git_mirror_url.strip()
GITEE_MIRROR_URL = settings.gitee_mirror_url.strip()
BOT_PROCESS_NAME = "codex-feishu-bot"

# --- Versioning Configuration ---
BASE_VERSION_PREFIX = "v1.0."
VERSION_START_COMMIT = 0  # Used to calculate patch number (commit_count - start_commit)

# --- Whitelist & Permission Configuration ---
ALLOWED_USERS = [uid.strip() for uid in settings.allowed_users.split(",") if uid.strip()]
ALLOWED_CHATS = [cid.strip() for cid in settings.allowed_chats.split(",") if cid.strip()]
DANGEROUSLY_SKIP_PERMISSIONS = settings.dangerously_skip_permissions
AUTH_BOOTSTRAP_ALLOW_GROUP = settings.auth_bootstrap_allow_group

# --- Workspace & Project Directory Configuration ---
WORKSPACE_ROOT = settings.workspace_root
