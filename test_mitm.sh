#!/bin/bash
sudo apt-get update && sudo apt-get install -y mitmproxy >/dev/null 2>&1
# Start mitmdump in background
mitmdump -w dump.mitm -q &
MITM_PID=$!
sleep 2

# Export proxy and cert
export HTTPS_PROXY="http://127.0.0.1:8080"
export HTTP_PROXY="http://127.0.0.1:8080"
export SSL_CERT_FILE=~/.mitmproxy/mitmproxy-ca-cert.pem

# Run codex
/home/ubuntu/.local/bin/codex -p "hi" >/dev/null 2>&1
kill $MITM_PID
