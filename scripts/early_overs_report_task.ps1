# Registers the Windows scheduled task for the nightly results report. Run once, after secrets\discord_webhook.txt is set.
# Delete later with:  schtasks /Delete /TN "KalshiPM early overs report" /F
# Daily from 10/22/2026, starting 23:00 local (this PC runs on US Eastern, same as the notifier task), repeating every
# 15 min for 3 h 15 min (last run 02:15), ending 11/05/2026 (schtasks wants mm/dd/yyyy). The script sends once per night.
$tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\Users\Jarvis\Documents\Kalshi-PM\scripts\early_overs_report_run.ps1"
schtasks /Create /TN "KalshiPM early overs report" /TR $tr /SC DAILY /ST 23:00 /SD 10/22/2026 /RI 15 /DU 03:15 /ED 11/05/2026 /F
schtasks /Query /TN "KalshiPM early overs report" /V /FO LIST | Select-String "Task To Run|Next Run|Repeat: Every|Repeat: Until|End Date"
