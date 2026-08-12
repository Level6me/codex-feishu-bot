FROM node:22-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENT_BACKEND=codex \
    CODEX_BIN=codex \
    TZ=Asia/Shanghai

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip git ca-certificates tzdata \
    && npm install -g @openai/codex \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt
COPY . .

ENV PATH="/root/.npm-global/bin:${PATH}"

CMD ["python3", "main.py"]
