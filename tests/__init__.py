"""Codex Feishu Bot 自动化测试套件。

运行：cd 项目根目录 && venv/bin/python3 -m unittest discover -s tests -v
所有测试均 mock 飞书发送 / 数据库 / Codex CLI 子进程 / git fetch，
数据库通过 DB_FILE 环境变量隔离到临时目录，绝不触碰真实 codex_bot.db。
"""
