# 开发记录与进度追踪 (Development Log)

## [v3.0.0] - 2026-08-12（功能冻结基线）

### 🚀 重大演进（迁移阶段 1~4 全部完成）
- **数据库与配置层扩展**：新增 6 张表（`bot_meta`、`auth_sessions`、`pending_tasks`、`cron_tasks`、`cron_logs`、`recent_messages`）；配置层迁移至 `pydantic-settings`。
- **完整权限体系**：管理员引导、`admin/user/guest/banned` 角色、授权作用域（basic/dev/full）、未授权静默、限流、`/user` 管理面板、`/auth` 申请、按角色联动沙箱（admin=full-access / user=workspace-write）。
- **定时任务引擎**：`cron_engine.py` + croniter，支持标准 Cron 与秒级倒计时；面板创建/启停/删除/立即触发；执行报告卡片。
- **插件框架**：微内核底座 + 在线插件源；内置 6 插件（ai_memory / cron_scheduler / notes_manager / rpi_gpio_status / server_health / system_updater）。
- **AI 记忆与备忘录**：偏好记忆自动注入对话上下文；备忘录增删查清。
- **服务器巡检与树莓派 GPIO**：`/sysinfo` `/health` 巡检卡片；三色 LED 状态灯（非树莓派 Mock 降级）。

### 🐞 修复
- 会话线程损坏自愈：检测到 "No tool output found" 后自动清空会话并以新线程重试一次，避免持续失败。
- 定时任务会话隔离：cron 任务使用独立会话副本 + 全新线程，不再串入用户对话。
- 停滞检测 300s → 600s，并将 stderr 与 `custom_tool_call` 纳入进度信号，降低长任务误杀。

### ✅ 回归验证（2026-08-12）
- 插件函数级冒烟 7 组、系统命令函数级 21 组、修复单测 3 组全部通过。
- 定时任务真实端到端：30s 倒计时任务成功触发并推送执行报告卡片。
- 功能冻结基线打标：`v1.0-freeze-base`；回归清单见 `REGRESSION_CHECKLIST.md`。

### ⚙️ 工程化完善（2026-08-12 补充）
- **多平台部署脚本**：`install.sh` 自动检测操作系统与包管理器（macOS / Debian·Ubuntu / Fedora·RHEL / Arch / Alpine），macOS 用户级 npm 安装 pm2 与 Codex CLI（免 sudo），开机自启按平台适配。
- **平台定位**：项目为多平台架构；树莓派 GPIO 仅为可选插件（`rpi_gpio_status`），非树莓派自动 Mock 降级，不影响其他平台。
- **架构模块化（阶段 5）**：main.py 1395→99 行，handlers + cards 分层，38 卡片方法快照零漂移。
- **工程化对齐（阶段 6）**：Dockerfile/compose、.dockerignore、DB_FILE 可配置。

## [v2.x] - 2026-07

### 🚀 新增功能
- **卡片化交互升级**：`/help` 面板按钮化；移除 `/role` `/remember` `/forget` 文字命令，改为面板按钮 + `/memory` 偏好管理。
- **模型面板**：`/model` 弹出候选模型面板，一键热切换；自动探测 CLI 默认模型。
- **上下文与额度看板**：`/context` Token 用量统计；`/quota` 通过 Codex app-server JSON-RPC 查询官方订阅额度与账号。
- **项目浏览器**：`/project` 目录浏览/切换，支持设定公共项目根目录与新建项目；绝对路径 pending 状态修复。
- **全局记忆**：`/brain` 查看 `~/.codex/AGENTS.md`。
- **CHOICE_CARD 原生选择卡片**：模型输出选项时自动渲染按钮。
- **多模态修复**：媒体解析崩溃修复、多图防抖合流分析。

## [v1.1.0] - 2026-06-21

### 🚀 新增功能 (Features)
- **纯异步重构**：将飞书事件接收器与大模型处理逻辑完全解耦，引入了 Python 原生的 `asyncio` 协程架构。
- **高并发支持**：实现了多任务后台并发派发，支持在群聊或多人群发场景下的大并发访问而不会发生阻塞。
- **动态 Emoji 跑马灯**：引入了状态流转动画。在等待大模型响应的期间，机器人会在消息上动态轮播 `THINKING` (🤔)、`Typing` (⌨️)、`Mac` (💻)、`Communicate` (💬) 等表情，实时给用户正向的响应反馈。
- **状态清理机制**：在表情状态轮换以及最终生成回复后，自动销毁过期的表情包（`delete_emoji`），保证视觉上的整洁。

### 🐞 修复缺陷 (Bug Fixes)
- **修复了大模型无限挂起（卡死）的问题**：在此前版本中，使用 `PM2` 挂载非交互式守护进程时，大模型 `codex` 因为标准输入流（`stdin`）未关闭而陷入无限等待。现通过强制传入 `stdin=subprocess.DEVNULL` 参数彻底解决了在后台环境的死锁现象。

### ⚙️ 工程化构建 (Chore)
- **后台常驻服务化**：使用 `PM2` 工具成功将 `bot.py` 注册为了可靠的持久化后台进程（名称：`feishu-bot`），可免疫终端关闭，同时具备崩溃级秒级自动重启的能力。
- **代码版本化**：自动通过调用 指定 GitHub 仓库维护代码并通过 Git 推送发布。
- **文档完善**：构建了一份详尽的 `README.md`，规范化了一键部署教程及依赖清单。

---

## [v1.0.0] - 早前版本

### 🎯 初始设计
- 基于 `lark-cli` 事件消费（`im.message.receive_v1`）。
- 同步模式下调用本地大模型。
- 最初测试仅具备有限的表情响应（如 `StatusReading`）。
