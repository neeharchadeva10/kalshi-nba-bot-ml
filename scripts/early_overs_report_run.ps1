# Launched every 15 min (23:00-~02:15 local = US Eastern) by the scheduled task "KalshiPM early overs report".
# Sends the nightly win/loss summary to Discord once the night's games are settled. Public Kalshi GETs only; never places orders.
Set-Location 'C:\Users\Jarvis\Documents\Kalshi-PM'
& .venv\Scripts\python.exe scripts\early_overs_report.py *>> logs\early_overs_report_task.out
