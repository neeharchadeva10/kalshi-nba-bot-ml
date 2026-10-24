# Registers the 24/7 trade watcher: every 10 minutes, all day, every day, from 2026-10-23 through 2027-06-30.
# NOT registered by whoever wrote it; Opus (or you) runs this file once.
# Delete later with:  schtasks /Delete /TN "KalshiPM trade watcher" /F
# Dates are US format MM/DD/YYYY (this PC's locale). No /DU = it keeps repeating all day.
$tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\Users\Jarvis\Documents\Kalshi-PM\scripts\trade_watch_run.ps1"
schtasks /Create /TN "KalshiPM trade watcher" /TR $tr /SC MINUTE /MO 10 /SD 10/23/2026 /ST 00:00 /ED 06/30/2027 /F
schtasks /Query /TN "KalshiPM trade watcher" /V /FO LIST | Select-String "Task To Run|Next Run|Schedule Type|Start Time|End Date"
