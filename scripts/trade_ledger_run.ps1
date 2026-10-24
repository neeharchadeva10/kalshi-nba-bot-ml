# Launched daily 09:30 ET by the scheduled task "KalshiPM trade ledger". READ-ONLY: GET /portfolio/* only.
# Rebuilds logs\trade_ledger.csv, trade_ledger_summary.txt, bet_journal.csv. Nothing is sent to Discord.
Set-Location 'C:\Users\Jarvis\Documents\Kalshi-PM'
& .venv\Scripts\python.exe scripts\trade_ledger.py *>> logs\trade_ledger_task.out
