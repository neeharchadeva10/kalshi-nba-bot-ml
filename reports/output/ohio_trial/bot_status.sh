#!/bin/bash
# READ-ONLY. Prints the live lock bots' processes, heartbeats, night ledger and last 30 trade/stop/error lines from
# the Ohio server's logs. It only runs ps/cat/grep over SSH; it cannot send anything to Kalshi.
#   bash reports/output/ohio_trial/bot_status.sh [DATE like 26OCT20]
DATE="${1:-26OCT20}"
[[ "$DATE" =~ ^[0-9]{2}[A-Z]{3}[0-9]{2}$ ]] || { echo "bad date: $DATE"; exit 2; }
exec "/c/Windows/System32/OpenSSH/ssh.exe" -o BatchMode=yes -o ConnectTimeout=10 -i ~/.ssh/kalshi-ohio.pem \
  ubuntu@18.223.21.99 "cd ~/kpm/logs && date -u +%H:%M:%SZ; ps -eo pid,args | grep '[p]ython.*lockbot' | cut -c1-90; \
  for f in lockbot_${DATE}_*.heartbeat; do case \"\$f\" in *dry*) continue;; esac; echo \"== \$f\"; cat \"\$f\"; echo; done; \
  echo '== ledger'; cat lockbot_${DATE}_ledger.json 2>/dev/null; echo; echo '== recent'; \
  grep -h -E '\"event\": ?\"(attempt|stop|bot_error|attempt_error|cap_block)\"' \$(ls lockbot_${DATE}_*.jsonl | grep -v dry) | tail -30"
