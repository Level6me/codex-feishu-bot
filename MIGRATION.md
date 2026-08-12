# Codex Feishu Bot ← 源项目 v2.0 迁移方案

> 目标：把当前项目（Codex CLI 后端，单文件架构）逐步补齐源项目 `Level6me/antigravity-feishu-bot` v2.0 的功能，包括权限体系、定时任务、插件生态，以及模块化重构。
>
> 对比基线：上游 `ffae72a`（v2.0 微内核架构），当前运行目录 `/Users/jiang/codex-feishu-bot`。

## 依赖关系总览

```
阶段 1 数据库/配置层扩展（所有上层的地基）
   ├── 阶段 2 权限体系（/auth /user + 限流）
   ├── 阶段 3 定时任务（/cron /schedule）
   └── 阶段 4 插件框架 + 6 个内置插件（复用 2/3 的能力）
阶段 5 架构模块化重构（功能稳定后做，风险最高）
阶段 6 工程化对齐（Docker/守护/补丁）
```

阶段 1 必须最先做；2 和 3 可并行；4 依赖 2/3；5 建议最后。

---

## 阶段 1：数据库与配置层扩展（地基）✅ 已完成

**目标**：让数据库和配置具备承接权限/定时任务/插件的能力。

| 动作 | 源项目文件 | 落到当前项目 | 状态 |
|---|---|---|---|
| 新增 6 张表 | `database.py` | `database.py`：`bot_meta`、`auth_sessions`、`pending_tasks`、`cron_tasks`、`cron_logs`、`recent_messages` | ✅ |
| 配置项 | `config.py` | 增加 `DANGEROUSLY_SKIP_PERMISSIONS`、`WORKSPACE_ROOT` alias、`GITEE_MIRROR_URL` | ✅ |

**适配点**：
- `DB_FILE` 保持 `codex_bot.db`（不沿用上游 `antigravity_bot.db`）。
- 会话默认模型保持 `Codex CLI 默认模型`（上游是 Gemini），`Gemini 3.5 Flash` 历史数据转换逻辑保留。
- 旧 `database.py` 备份为 `database.py.bak`，旧 `config.py` 备份为 `config.py.bak`。

**验证**：8 张表创建成功；`bot_meta`/`auth_sessions` 读写正常；旧会话数据读取兼容。

---

## 阶段 2：权限体系（/auth /user + 限流）

**目标**：补齐"首会话绑定管理员 → 未授权静默 → /auth 申请 → 管理员授权/拉黑 → 限流"完整闭环。

### ✅ 已完成（2026-08-12）

| 动作 | 源项目文件 | 落到当前项目 |
|---|---|---|
| 权限核心逻辑 | `utils/auth.py` | ✅ 新建 `utils/auth.py`（角色、scopes、bootstrap、限流器、显示名解析） |
| 授权/管理卡片 | `cards/auth.py` | ✅ `card_builder.py` 增加 7 个 auth 卡片方法 |
| 消息入口检查点 | `handlers/events.py` | ✅ `main.py` `do_p2_im_message_receive_v1` 插入 bootstrap → banned 静默 → guest/pending → 限流 |
| 命令路由 | `commands.py` 的 `/user` | ✅ 增加 `/user`（grant/revoke/ban/unban/promote/demote/reset-admin）与 `/auth` 提示 |
| 卡片按钮回调 | `handlers/auth_actions.py` | ✅ `main.py` `_handle_auth_card_action`（approve/deny/ban/action/edit/tier/page） |
| 白名单兼容 | `utils/auth.py` 的 `_legacy_allowed` | ✅ 保留 `ALLOWED_USERS`/`ALLOWED_CHATS` 自动入册（dev tier） |
| 沙箱联动 | `agent_executor.py` | ✅ `execute_codex` 按角色选择 sandbox：admin=`danger-full-access`，user=`workspace-write` |
| lark 能力 | `lark_client.py` | ✅ 补齐 `send_card_to_chat_*`、`send_text_to_chat_*`、`get_chat_name_*`、`get_user_name_*` |

**本 fork 差异**：上游仅 p2p 私聊可绑定管理员；本 fork 新增 `AUTH_BOOTSTRAP_ALLOW_GROUP`（默认 True），自部署场景允许首个群聊绑定管理员，可用环境变量关闭。

**验证**：bootstrap（群聊）✅、guest 申请 → pending ✅、user 限流 5 条/分钟 ✅、授权/管理卡片构建 ✅、沙箱参数按角色切换 ✅。

---

## 阶段 3：定时任务（/cron /schedule）

**目标**：补齐 cron 引擎和任务管理卡片。

### ✅ 已完成（2026-08-12）

| 动作 | 源项目文件 | 落到当前项目 |
|---|---|---|
| 调度引擎 | `cron_engine.py` + `croniter` | ✅ 新建 `cron_engine.py`（调度循环 5s、任务去重、执行日志、延迟一次性任务） |
| 任务卡片 | `cards/cron.py` | ✅ `card_builder.py` 增加 4 个 cron 卡片（面板/触发/结果/创建确认） |
| 命令路由 | `commands.py` 的 `/cron`、`/schedule` | ✅ 增加 `/cron`、`/schedule` 面板；`PendingCommand.CRON_ADD` 创建流程（名称 \| 规则 \| Prompt） |
| 卡片按钮 | `handlers/card_actions.py` | ✅ `main.py` `_handle_cron_card_action`（切换 tab/打开面板/新建/启停/删除/立即触发） |
| 启动挂载 | `main.py` 启动段 | ✅ `cron_engine.start()`（启动）、`stop()`（退出） |
| 依赖 | `requirements.txt` | ✅ 增加 `croniter>=2.0.0` |

**适配点**：`CronEngine._run_task_wrapper` 调 `execute_codex(silent=True)`（新增静默模式：不发卡片、直接返回回复文本），结果由 cron 引擎统一发执行报告卡片。

**验证**：cron/delay 表达式计算 ✅、任务创建/读取/激活/删除 ✅、面板与创建卡片构建 ✅、语法全通过。

---

## 阶段 4：插件框架 + 内置插件

**目标**：微内核底座 + 6 个内置插件，具备 GitHub 在线安装能力。

### ✅ 已完成（2026-08-12）

| 动作 | 源项目文件 | 落到当前项目 |
|---|---|---|
| 插件底座 | `plugin_base.py`、`plugin_manager.py`、`plugin_store.py`、`plugin_sources.json` | ✅ 直接移植，创建 `plugins/` 目录 |
| 6 个内置插件 | `plugins/*` | ✅ 全部移植（ai_memory / cron_scheduler / notes_manager / rpi_gpio_status / server_health / system_updater） |
| 插件卡片 | `cards/plugin.py` | ✅ `card_builder.py` 增加 `build_plugin_panel_card`（已安装/插件源双 Tab） |
| 命令路由 | `/plugin`、`/plugins` | ✅ `commands.py` 注册；系统命令优先，插件命令兜底分发 |
| AI hooks | `PluginManager.dispatch_before_ai/after_ai` | ✅ before 挂在 main.py 组装 prompt 前；after 挂在 execute_codex 返回回复后 |
| 卡片动作 | `handlers/card_actions.py` | ✅ `do_p2_card_action_trigger` 分发到 `plugin_manager.dispatch_card_action` |
| 启动加载 | `main.py` | ✅ 启动时 `register_system_commands` + `load_all_plugins()` |

**适配点**：
- 所有插件 `from cards import CardBuilder` → `from card_builder import CardBuilder`。
- `system_updater`：`pm2 restart feishu-bot` → `codex-feishu-bot`；`create_footer` 改用 `CardBuilder._create_footer()`；沿用 `GITEE_MIRROR_URL` 回退。
- `ai_memory` 偏好注入直接复用现有 `get_profile_async`（无需读 agy global memory）。
- `rpi_gpio_status` 在非树莓派环境自动 Mock 模拟（GPIO 不可用时安全降级）。
- 命令冲突策略：`/memory`、`/cron`、`/note`、`/update` 等系统命令优先，插件命令兜底，不会重复处理。

**验证**：6 个插件全部加载 ✅、命令映射 10 个 ✅、server_health 巡检卡片（Mac 降级）✅、ai_memory before_ai hook ✅、cron_scheduler 命令 ✅、rpi_gpio mock 模式 ✅。

---

## 阶段 5：架构模块化重构（可选，最重）

**目标**：把单文件架构对齐到上游的 handlers/cards 模块化。

- `main.py`（44KB）拆出：`handlers/events.py`、`handlers/messages.py`、`handlers/media.py`、`handlers/pipeline.py`、`handlers/card_actions.py`
- `card_builder.py`（53KB）拆成 `cards/*.py`，保留 `card_builder.CardBuilder` 兼容导入层
- 全局状态抽到 `app_state.py`（上游已有，可直接搬）
- 验证项：是否需要上游的 lark-oapi CARD 帧补丁（当前卡片按钮能用，先验证再决定）

**验收**：功能与重构前完全一致，`main.py` 瘦身到 10KB 左右。

---

## 阶段 6：工程化对齐

- Docker/compose 对齐上游双服务形态（不需要 agy-daemon 则只对齐镜像基础）
- `.env.example`、README、CHANGELOG 同步
- 停滞检测已对齐（600 秒 + stderr + custom_tool_call），无需再动

---

## 迁移日志

### 2026-08-12
- 阶段 1 完成：数据库 6 张新表 + 配置项扩展。
- 停滞检测修复：`STALL_TIMEOUT` 300 → 600，stderr 与 `custom_tool_call` 纳入进度信号，系统指令新增 [Agent 执行规范]。
