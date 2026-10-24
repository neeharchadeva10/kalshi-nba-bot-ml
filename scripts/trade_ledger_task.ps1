# Registers the daily Windows task for the read-only trade ledger + bet journal. Run once (Opus does this).
# Delete later with:  schtasks /Delete /TN "KalshiPM trade ledger" /F
# Daily at 09:30 local (this PC is on US Eastern, after all games have settled), no end date.
$tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File %USERPROFILE%\Documents\Kalshi-PM\scripts\trade_ledger_run.ps1"
schtasks /Create /TN "KalshiPM trade ledger" /TR $tr /SC DAILY /ST 09:30 /F
schtasks /Query /TN "KalshiPM trade ledger" /V /FO LIST | Select-String "Task To Run|Next Run|Schedule Type|Start Time"
