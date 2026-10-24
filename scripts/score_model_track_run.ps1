# Launched every 30 min (10:00-02:00 local = US Eastern) by the scheduled task "KalshiPM score model tracker".
# Research #59 forward tracking: refreshes the hoopR team box if stale, locks predictions >= 30 min before tip, records finals,
# rewrites logs\score_model\summary.json. Public GETs only (Kalshi public API + the hoopR GitHub raw file); never places orders.
Set-Location 'C:\Users\Jarvis\Documents\Kalshi-PM'
& .venv\Scripts\python.exe scripts\score_model_track.py *>> logs\score_model_track_task.out
