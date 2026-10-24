# Launched every 10 minutes by the scheduled task "KalshiPM trade watcher". READ-ONLY: GETs three public RSS feeds.
# Writes logs\trades\ (alerts.jsonl, summary.json, seen.json). Nothing is sent to Discord or to Kalshi.
Set-Location '%USERPROFILE%\Documents\Kalshi-PM'
New-Item -ItemType Directory -Force -Path logs\trades | Out-Null
$out = 'logs\trades\task.out'
if ((Test-Path $out) -and ((Get-Item $out).Length -gt 1MB)) { Move-Item -Force $out 'logs\trades\task.out.old' }
& .venv\Scripts\python.exe scripts\trade_watch.py *>> $out
