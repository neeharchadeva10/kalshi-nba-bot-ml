#!/bin/bash
# READ-ONLY. Waits (up to 30 min) for the next live lock-bot trade / stop / error line in the Ohio server's logs,
# prints it and exits. It only runs tail/grep on log files over SSH; it cannot send anything to Kalshi.
#   bash reports/output/ohio_trial/watch_trades.sh [DATE like 26OCT20]
DATE="${1:-26OCT20}"
[[ "$DATE" =~ ^[0-9]{2}[A-Z]{3}[0-9]{2}$ ]] || { echo "bad date: $DATE"; exit 2; }
exec "/c/Windows/System32/OpenSSH/ssh.exe" -o BatchMode=yes -o ServerAliveInterval=30 -i ~/.ssh/kalshi-ohio.pem \
  ubuntu@${KALSHI_SERVER_HOST} "cd ~/kpm/logs && timeout 1800 tail -n 0 -F \$(ls lockbot_${DATE}_*.jsonl | grep -v dry) \
  | grep -m 1 -E '\"event\": ?\"(attempt|stop|bot_error|attempt_error|cap_block)\"'"
