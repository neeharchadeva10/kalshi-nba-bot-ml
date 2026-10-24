# Registers the Windows scheduled task for the Discord notifier. Run once, after secrets\discord_webhook.txt is set.
# Delete later with:  schtasks /Delete /TN "KalshiPM early overs notify" /F
# Every 5 min from 11:00 for 12 h (to 23:00 local = US Eastern; weekend games tip as early as ~12:00), daily, ending 11/03/2026 (this PC's schtasks wants mm/dd/yyyy).
# The task runs scripts\early_overs_notify_run.ps1 hidden (a -File path avoids nested quoting in /TR).
$tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File %USERPROFILE%\Documents\Kalshi-PM\scripts\early_overs_notify_run.ps1"
schtasks /Create /TN "KalshiPM early overs notify" /TR $tr /SC DAILY /ST 11:00 /RI 5 /DU 12:00 /ED 11/03/2026 /F
schtasks /Query /TN "KalshiPM early overs notify" /V /FO LIST | Select-String "Task To Run|Next Run|Repeat: Every|Repeat: Until|End Date"
