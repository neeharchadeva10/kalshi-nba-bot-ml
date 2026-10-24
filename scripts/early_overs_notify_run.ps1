# Launched every 5 min (17:00-23:00 ET) by the scheduled task "KalshiPM early overs notify".
# Sends Discord pings for the nightly NBA totals pick list. Read-only on Kalshi (public GETs); never places orders.
Set-Location '%USERPROFILE%\Documents\Kalshi-PM'
& .venv\Scripts\python.exe scripts\early_overs_notify.py *>> logs\early_overs_notify_task.out
