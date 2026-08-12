#!/usr/bin/env bash

# ==============================================
# Codex Feishu Bot 一键部署脚本（多平台版）
# 支持：macOS / Debian·Ubuntu / Fedora·RHEL / Arch / Alpine
# 用法：./install.sh   或   bash <(curl -sL https://raw.githubusercontent.com/Level6me/codex-feishu-bot/main/install.sh)
# ==============================================

set -e

REPO_URL="https://github.com/Level6me/codex-feishu-bot.git"
PROJECT_DIR="codex-feishu-bot"
PM2_NAME="codex-feishu-bot"

# ---------- 0. 全局状态与辅助函数 ----------
ALLOW_ROOT=0
SUDO_CMD="sudo"
if [ "$(id -u)" -eq 0 ]; then
    SUDO_CMD=""
fi

# 校验当前目录确实是本项目（含 main.py 的 git 仓库），防止误删/误更新
require_project_dir() {
    if [ ! -f main.py ] || ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "❌ 错误：${1:-本命令} 必须在项目目录内运行（当前目录需包含 main.py 且为 git 仓库）。"
        echo "   当前目录: $(pwd)"
        exit 1
    fi
}

# 查找满足版本要求的 Python 解释器（3.10+），结果写入 PYTHON_BIN / PY_VER
find_python() {
    for cand in python3 python3.12 python3.11 python3.10; do
        command -v "$cand" >/dev/null 2>&1 || continue
        ver="$("$cand" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
        [ -z "$ver" ] && continue
        maj="${ver%%.*}"; min="${ver#*.}"
        if [ "$maj" -gt 3 ] || { [ "$maj" -eq 3 ] && [ "$min" -ge 10 ]; }; then
            PYTHON_BIN="$cand"; PY_VER="$ver"
            return 0
        fi
    done
    return 1
}

# 将 .env.example 中出现而 .env 缺失的字段补齐（不覆盖已有值）。
# 默认值直接取自 .env.example（布尔字段为 true 等），避免空值导致配置解析失败。
ensure_env_fields() {
    [ -f .env.example ] || return 0
    [ -f .env ] || return 0
    local key missing=0
    while IFS= read -r key; do
        [ -z "$key" ] && continue
        if ! grep -q "^${key}=" .env; then
            default="$(sed -n "s/^${key}=//p" .env.example | tail -n 1)"
            echo "# ${key}（新增字段，默认值来自 .env.example，请按需调整）" >> .env
            echo "${key}=${default}" >> .env
            missing=1
        fi
    done < <(grep -E '^[A-Z][A-Z0-9_]*=' .env.example | sed 's/=.*//')
    [ "$missing" = "1" ] && echo "✅ .env 已补充缺失字段（默认值来自 .env.example，请按需调整）。"
}

# ---------- 0. 子命令（update / uninstall） ----------
do_update() {
    require_project_dir update
    echo "🔄 开始升级 ${PM2_NAME} ..."
    if git pull origin main; then
        echo "✅ 代码已更新。"
    else
        echo "⚠️ git pull 失败（可能存在本地冲突），继续使用当前代码。"
    fi
    if [ ! -d venv ]; then
        echo "❌ 未找到 venv，请先执行 ./install.sh 完成首次部署。"
        exit 1
    fi
    source venv/bin/activate
    pip install -r requirements.txt
    ensure_env_fields
    pm2 restart "$PM2_NAME" || pm2 start venv/bin/python3 --name "$PM2_NAME" -- main.py
    echo "✅ 升级完成。"
}

do_uninstall() {
    require_project_dir uninstall
    echo "🗑️ 开始卸载 ${PM2_NAME} ..."
    pm2 stop "$PM2_NAME" 2>/dev/null || true
    pm2 delete "$PM2_NAME" 2>/dev/null || true
    pm2 save || true
    read -r -p "是否删除 venv 与 .env？（源代码目录保留）[y/N]: " del_data
    if [[ "$del_data" =~ ^[Yy]$ ]]; then
        rm -rf venv .env 2>/dev/null || true
        echo "✅ 已删除 venv 与 .env。"
    fi
    echo "✅ 卸载完成（源代码目录保留，如需彻底删除请手动移除 $(pwd)）。"
}

case "${1:-}" in
    --allow-root) ALLOW_ROOT=1; shift ;;
esac

case "${1:-}" in
    update) do_update; exit 0 ;;
    uninstall) do_uninstall; exit 0 ;;
esac

# ---------- 0. 平台检测 ----------
detect_os() {
    case "$(uname -s)" in
        Linux*)  OS="linux" ;;
        Darwin*) OS="macos" ;;
        *)       OS="unknown" ;;
    esac

    DISTRO=""
    PKG_MGR=""
    if [ "$OS" = "linux" ]; then
        if   command -v apt-get >/dev/null 2>&1; then DISTRO="debian"; PKG_MGR="apt-get"
        elif command -v dnf     >/dev/null 2>&1; then DISTRO="fedora"; PKG_MGR="dnf"
        elif command -v yum     >/dev/null 2>&1; then DISTRO="rhel";   PKG_MGR="yum"
        elif command -v pacman  >/dev/null 2>&1; then DISTRO="arch";   PKG_MGR="pacman"
        elif command -v apk     >/dev/null 2>&1; then DISTRO="alpine"; PKG_MGR="apk"
        fi
    elif [ "$OS" = "macos" ]; then
        PKG_MGR="brew"
    fi
}

# ---------- 1. 环境检测 ----------
check_root() {
    if [ "$(id -u)" -eq 0 ] && [ "$ALLOW_ROOT" -ne 1 ]; then
        echo "❌ 错误: 请不要使用 root 权限运行此脚本（普通用户即可，需要提权时会自动询问 sudo）。"
        echo "   如确认要在 root 下运行（不推荐），请使用: bash install.sh --allow-root"
        exit 1
    fi
}

check_python() {
    if ! find_python; then
        echo "⚠️ 未检测到 Python 3.10+，尝试自动安装..."
        install_system_pkgs
        if ! find_python; then
            echo "❌ 需要 Python 3.10+，但自动安装后仍未找到可用的解释器。请手动安装："
            case "$DISTRO" in
                debian) echo "   sudo apt-get install -y python3.11 python3-venv" ;;
                fedora) echo "   sudo dnf install -y python3.11" ;;
                rhel)   echo "   sudo yum install -y python3.11" ;;
                arch)   echo "   sudo pacman -S --noconfirm python" ;;
                alpine) echo "   sudo apk add --no-cache python3" ;;
                *)      echo "   macOS: brew install python@3.12；其他系统请安装 Python 3.10+ 并加入 PATH" ;;
            esac
            exit 1
        fi
    fi
    echo "✅ Python $PY_VER（$PYTHON_BIN）"
}

# ---------- 2. 按平台安装系统依赖 ----------
install_system_pkgs() {
    echo "👉 按平台安装系统依赖 (${PKG_MGR:-未识别})..."
    case "$DISTRO" in
        debian)
            $SUDO_CMD apt-get update
            $SUDO_CMD apt-get install -y --no-install-recommends nodejs npm python3 python3-venv python3-pip git ca-certificates
            ;;
        fedora)
            $SUDO_CMD dnf install -y nodejs npm python3 python3-pip git ca-certificates || true
            ;;
        rhel)
            $SUDO_CMD yum install -y nodejs npm python3 python3-pip git ca-certificates || true
            ;;
        arch)
            $SUDO_CMD pacman -S --noconfirm nodejs npm python python-pip git ca-certificates || true
            ;;
        alpine)
            $SUDO_CMD apk add --no-cache nodejs npm python3 py3-pip git ca-certificates || true
            ;;
        "")
            if [ "$OS" = "macos" ]; then
                if ! command -v brew &>/dev/null; then
                    echo "❌ 未检测到 Homebrew。请先安装："
                    echo '   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
                    exit 1
                fi
                if ! brew install node python@3.12 git; then
                    echo "❌ Homebrew 安装依赖失败，请手动执行: brew install node python@3.12 git"
                    exit 1
                fi
            else
                echo "⚠️ 无法识别的 Linux 发行版，请手动安装 nodejs / npm / python3 / git。"
            fi
            ;;
    esac
}

# ---------- 3. Node / npm / pm2 ----------
ensure_npm() {
    if ! command -v node &>/dev/null || ! command -v npm &>/dev/null; then
        install_system_pkgs
    fi
    command -v npm &>/dev/null || { echo "❌ npm 安装失败，请手动安装 Node.js。"; exit 1; }
    echo "✅ Node $(node -v 2>/dev/null) / npm $(npm -v 2>/dev/null)"
}

ensure_pm2() {
    if command -v pm2 &>/dev/null; then
        echo "✅ pm2 已存在 ($(pm2 -v 2>/dev/null))"
        return
    fi

    echo "⚠️ 未检测到 pm2，尝试安装..."
    NPM_PREFIX="$(npm config get prefix 2>/dev/null || true)"
    if [ "$OS" = "macos" ] && [ ! -w "$NPM_PREFIX" ] 2>/dev/null; then
        # macOS 用户级安装，避免 sudo 污染系统目录
        echo "👉 使用用户级 npm 目录安装 pm2..."
        mkdir -p "$HOME/.npm-global"
        npm config set prefix "$HOME/.npm-global" 2>/dev/null || true
        export PATH="$HOME/.npm-global/bin:$PATH"
    fi

    if npm install -g pm2 2>/dev/null; then
        :
    else
        echo "👉 用户级安装失败，尝试 sudo 全局安装..."
        $SUDO_CMD npm install -g pm2
    fi

    command -v pm2 &>/dev/null || { echo "❌ pm2 安装失败，请手动执行 npm install -g pm2。"; exit 1; }
    echo "✅ pm2 $(pm2 -v 2>/dev/null)"
}

# ---------- 4. Codex CLI ----------
find_codex() {
    if command -v codex &>/dev/null; then
        echo "codex"
        return 0
    fi
    for cand in "$HOME/.npm-global/bin/codex" "$HOME/.local/bin/codex" "/opt/homebrew/bin/codex" "/usr/local/bin/codex"; do
        if [ -x "$cand" ]; then
            echo "$cand"
            return 0
        fi
    done
    return 1
}

ensure_codex() {
    DEPLOY_BACKEND="${AGENT_BACKEND:-codex}"
    if [ -f .env ]; then
        DEPLOY_BACKEND="$(sed -n "s/^AGENT_BACKEND=//p" .env | tail -n 1)"
        DEPLOY_BACKEND="${DEPLOY_BACKEND:-codex}"
    fi
    if [ "$DEPLOY_BACKEND" != "codex" ]; then
        echo "✅ 后端非 codex（${DEPLOY_BACKEND}），跳过 Codex 检测。"
        return
    fi

    CODEX_PATH="$(find_codex || true)"
    if [ -z "$CODEX_PATH" ]; then
        echo "⚠️ 未检测到 Codex CLI，尝试通过 npm 安装..."
        ensure_npm
        npm install -g @openai/codex 2>/dev/null || $SUDO_CMD npm install -g @openai/codex || true
        CODEX_PATH="$(find_codex || true)"
    fi
    if [ -z "$CODEX_PATH" ]; then
        echo "❌ 未找到 Codex CLI。安装后请确认 PATH 包含其可执行文件目录，或设置 CODEX_BIN 环境变量。"
        exit 1
    fi
    echo "✅ Codex CLI: $CODEX_PATH"
    # 将用户级目录补进 .env，保证 PM2 启动时能找到
    CODEX_DIR="$(dirname "$CODEX_PATH")"
    if [ "$CODEX_DIR" != "/usr/bin" ] && [ "$CODEX_DIR" != "/usr/local/bin" ] && [ "$CODEX_DIR" != "/opt/homebrew/bin" ]; then
        # 幂等写入：先删除旧行再追加，避免 .env 积累重复的 CODEX_BIN 配置
        [ -f .env ] && sed -i'.bak' "/^CODEX_BIN=/d" .env && rm -f .env.bak
        echo "CODEX_BIN=$CODEX_PATH" >> .env
    fi
}

# ---------- 5. 主流程 ----------
main() {
    detect_os
    echo "=========================================="
    echo "    Codex Feishu Bot 一键部署脚本"
    echo "    平台: ${OS} / ${DISTRO:-${PKG_MGR:-unknown}}"
    echo "=========================================="
    echo ""

    check_root
    check_python
    ensure_npm
    ensure_pm2

    # ---------- 源码获取 ----------
    if [ ! -f "main.py" ]; then
        echo "⚠️ 未检测到核心文件 (main.py)，准备克隆项目..."
        if [ ! -d "$PROJECT_DIR" ]; then
            git clone "$REPO_URL" "$PROJECT_DIR"
        fi
        cd "$PROJECT_DIR"
    fi
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "⬇️ 尝试拉取最新代码..."
        git pull origin main || true
    fi
    echo ""

    # ---------- 环境变量 ----------
    configure_env=true
    if [ -f .env ]; then
        read -r -p "⚠️ 检测到 .env 已存在，是否覆盖？[y/N]: " overwrite_env
        if [[ ! "$overwrite_env" =~ ^[Yy]$ ]]; then
            configure_env=false
            echo "⏭️ 使用现有 .env。"
            ensure_env_fields
        fi
    fi
    if [ "$configure_env" = true ]; then
        echo "------------------------------------------"
        echo "请输入飞书应用配置 (飞书开发者后台获取):"
        read -r -p "👉 FEISHU_APP_ID (例: cli_a4...): " app_id
        read -r -p "👉 FEISHU_APP_SECRET: " app_secret
        if [ -z "$app_id" ] || [ -z "$app_secret" ]; then
            echo "❌ APP_ID / APP_SECRET 不能为空。"
            exit 1
        fi
        {
            echo "FEISHU_APP_ID=$app_id"
            echo "FEISHU_APP_SECRET=$app_secret"
            echo "AGENT_BACKEND=codex"
            echo "CODEX_BIN=codex"
        } > .env
        echo "✅ .env 已生成。"
        ensure_env_fields
    fi
    ensure_codex
    echo ""

    # ---------- 虚拟环境与依赖 ----------
    echo "📦 配置 Python 虚拟环境..."
    if [ ! -d "venv" ]; then
        if ! "$PYTHON_BIN" -m venv venv; then
            echo "⚠️ venv 创建失败，尝试安装 python3-venv..."
            case "$DISTRO" in
                debian) $SUDO_CMD apt-get update && $SUDO_CMD apt-get install -y python3-venv ;;
                fedora) $SUDO_CMD dnf install -y python3-virtualenv || true ;;
                rhel)   $SUDO_CMD yum install -y python3-virtualenv || true ;;
                arch)   $SUDO_CMD pacman -S --noconfirm python-virtualenv || true ;;
                alpine) $SUDO_CMD apk add --no-cache py3-virtualenv || true ;;
            esac
            "$PYTHON_BIN" -m venv venv || { echo "❌ venv 创建失败，请手动解决。"; exit 1; }
        fi
        echo "✅ venv 创建成功。"
    fi
    source venv/bin/activate
    pip install --upgrade pip
    if [ -f requirements.txt ]; then
        pip install -r requirements.txt
    else
        pip install lark-oapi pydantic pydantic-settings aiosqlite croniter
    fi
    echo "✅ 依赖安装完成。"
    echo ""

    # ---------- PM2 启动 ----------
    echo "🚀 准备启动服务..."
    read -r -p "是否立即用 PM2 启动 ${PM2_NAME} 服务？[Y/n]: " start_pm2
    if [[ ! "$start_pm2" =~ ^[Nn]$ ]]; then
        if pm2 describe "$PM2_NAME" >/dev/null 2>&1; then
            pm2 restart "$PM2_NAME"
        else
            pm2 start venv/bin/python3 --name "$PM2_NAME" -- main.py
        fi
        pm2 save || true

        read -r -p "是否配置开机自启？[Y/n]: " setup_startup
        if [[ ! "$setup_startup" =~ ^[Nn]$ ]]; then
            echo "👉 执行 pm2 startup（将输出需要 sudo 的引导命令，请输入开机密码）："
            STARTUP_CMD="$(pm2 startup 2>&1 | grep -E 'pm2 startup|launchctl|systemctl' | head -n 1 || true)"
            if [ -n "$STARTUP_CMD" ]; then
                eval "$STARTUP_CMD" || true
                pm2 save || true
                echo "✅ 开机自启配置完成。"
            else
                echo "⚠️ 未能自动解析 startup 命令，请手动执行上方提示。"
            fi
        fi
        echo ""
        echo "🎉 部署完成！机器人已上线。"
        echo "👉 日志: pm2 logs $PM2_NAME"
        echo "👉 重启: pm2 restart $PM2_NAME"
    else
        echo "⏭️ 跳过启动。稍后手动运行: pm2 start venv/bin/python3 --name \"$PM2_NAME\" -- main.py"
    fi
}

main "$@"
