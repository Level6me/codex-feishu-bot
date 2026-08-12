"""卡片常量（阶段 5 第 3 步：自 card_builder.py 迁出）。"""

TIER_LABELS = {
    "basic": "基础权限",
    "dev": "开发权限",
    "full": "完全权限",
}

ROLE_LABELS = {
    "admin": "管理员",
    "user": "已授权",
    "pending": "待审批",
    "guest": "未授权",
    "banned": "已拉黑",
}

TIER_NOTES = {
    "basic": "对话、图片/文件解析、笔记偏好",
    "dev": "基础权限 + 项目切换、文件回传",
    "full": "开发权限 + 执行 Shell、查看额度",
}

MAX_MARKDOWN_CHARS = 25000
TRUNCATION_NOTICE = "\n\n\n> ⚠️ **回复内容过长，已截断显示。完整内容请拆分任务后分批查看。**"
