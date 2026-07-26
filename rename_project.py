import os

files_to_update = [
    "README.md",
    "install.sh",
    "CHANGELOG.md",
    "docker-compose.yml",
    "commands.py",
    "main.py"
]

for filename in files_to_update:
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Replace the hyphenated project name
        content = content.replace("codex-feishu-bot", "codex-feishu-bot")
        # Replace the capitalized project name
        content = content.replace("codex Feishu Bot", "Codex Feishu Bot")
        
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Updated {filename}")
