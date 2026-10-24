#!/bin/bash
# Copies reports/output/ohio_trial/lockbot.py to the Ohio server and syntax-checks it there. It does NOT start, stop or
# restart any bot (the user restarts bots with their own command), and it never talks to Kalshi.
#   bash reports/output/ohio_trial/deploy_bot.sh
set -e
cd "$(dirname "$0")/../../.."
.venv/Scripts/python.exe -m pytest reports/output/ohio_trial -q
"/c/Windows/System32/OpenSSH/scp.exe" -q -i ~/.ssh/kalshi-ohio.pem reports/output/ohio_trial/lockbot.py \
  ubuntu@${KALSHI_SERVER_HOST}:kpm/reports/output/ohio_trial/lockbot.py
"/c/Windows/System32/OpenSSH/ssh.exe" -o BatchMode=yes -i ~/.ssh/kalshi-ohio.pem ubuntu@${KALSHI_SERVER_HOST} \
  "cd ~/kpm && .venv/bin/python -m py_compile reports/output/ohio_trial/lockbot.py && sha256sum reports/output/ohio_trial/lockbot.py"
sha256sum reports/output/ohio_trial/lockbot.py
echo "deployed (running bots keep the old code until the user restarts them)"
