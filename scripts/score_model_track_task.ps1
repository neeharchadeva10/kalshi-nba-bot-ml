# Registers the Windows scheduled task for the research #59 score-model tracker. NOT registered yet: run this once after review.
# Delete later with:  schtasks /Delete /TN "KalshiPM score model tracker" /F
# Daily from 10/23/2026, starting 10:00 local (this PC runs on US Eastern), repeating every 30 min for 16 h (last run 02:00),
# ending 06/30/2027 (schtasks wants mm/dd/yyyy). Every run is idempotent; a lock file stops overlapping runs.
$tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File %USERPROFILE%\Documents\Kalshi-PM\scripts\score_model_track_run.ps1"
schtasks /Create /TN "KalshiPM score model tracker" /TR $tr /SC DAILY /ST 10:00 /SD 10/23/2026 /RI 30 /DU 16:00 /ED 06/30/2027 /F
schtasks /Query /TN "KalshiPM score model tracker" /V /FO LIST | Select-String "Task To Run|Next Run|Repeat: Every|Repeat: Until|End Date"
