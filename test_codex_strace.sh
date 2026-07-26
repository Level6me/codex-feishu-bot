#!/bin/bash
strace -f -e trace=connect,sendto,write /home/ubuntu/.local/bin/codex -p "hi" 2>&1 | grep -E "googleapis.com|retrieveUserQuota"
