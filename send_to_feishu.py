#!/usr/bin/env python3
"""通过飞书 API 把本地文件发送到指定会话（默认最近活跃会话）。

用法：
    python3 send_to_feishu.py <file_path> [chat_id] [--caption "说明文字"]

示例：
    python3 send_to_feishu.py docs/FLIGHT_CHECK.md
    python3 send_to_feishu.py report.pdf oc_xxxx --caption "这是验收报告"

chat_id 省略时自动取 recent_messages 中最近一条消息的会话。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import get_db  # noqa: E402
from lark_client import send_local_file_to_chat  # noqa: E402


def resolve_default_chat_id():
    """返回最近活跃会话 chat_id：优先 recent_messages，回退 chat_sessions。"""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT chat_id FROM recent_messages ORDER BY create_time DESC LIMIT 1"
            ).fetchone()
            if row:
                return row[0]
            row = conn.execute("SELECT chat_id FROM chat_sessions LIMIT 1").fetchone()
            return row[0] if row else None
    except Exception as e:
        print(f"[send_to_feishu] 读取默认会话失败: {e}")
        return None


def send_file(file_path, chat_id=None, caption=None):
    """发送本地文件到会话；chat_id 省略时自动定位最近活跃会话。

    返回 (ok, message)。
    """
    if not os.path.isfile(file_path):
        return False, f"文件不存在: {file_path}"

    chat_id = chat_id or resolve_default_chat_id()
    if not chat_id:
        return False, "未找到目标会话，请显式传入 chat_id"

    ok = send_local_file_to_chat(chat_id, file_path, caption=caption)
    if ok:
        return True, f"✅ 已发送 {file_path} -> {chat_id}"
    return False, f"❌ 发送失败: {file_path} -> {chat_id}"


def main():
    parser = argparse.ArgumentParser(description="把本地文件通过飞书 API 发送到会话")
    parser.add_argument("file_path", help="要发送的本地文件路径")
    parser.add_argument("chat_id", nargs="?", default=None, help="目标会话 ID（省略则取最近活跃会话）")
    parser.add_argument("--caption", default=None, help="可选：文件附带说明文字")
    args = parser.parse_args()

    ok, msg = send_file(args.file_path, args.chat_id, args.caption)
    print(f"[send_to_feishu] {msg}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
