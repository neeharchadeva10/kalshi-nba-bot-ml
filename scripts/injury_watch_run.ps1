# Launched every 10 minutes by the scheduled task "KalshiPM injury watcher". READ-ONLY: GETs NBA injury-report PDFs
# (ak-static.cms.nba.com, at most 6 per run) and three public RSS feeds. Writes data\raw\nba_injury\, data\derived\injury_reports_2027.jsonl
# and logs\injuries\ (alerts.jsonl, summary.json). Nothing is sent to Discord or to Kalshi.
Set-Location 'C:\Users\Jarvis\Documents\Kalshi-PM'
New-Item -ItemType Directory -Force -Path logs\injuries | Out-Null
$out = 'logs\injuries\task.out'
if ((Test-Path $out) -and ((Get-Item $out).Length -gt 1MB)) { Move-Item -Force $out 'logs\injuries\task.out.old' }
& .venv\Scripts\python.exe scripts\injury_watch.py *>> $out
