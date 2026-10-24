# Launcher for the forward paper tape (scripts\totals_snapshot.py). One run = one snapshot of open markets.
# Meant to be run by a scheduled task every ~5 minutes; THIS FILE DOES NOT REGISTER ANY TASK.
# Native stderr is merged by cmd.exe, NOT by PowerShell's *>> (under ErrorActionPreference=Stop that pattern
# turns any native stderr line into a terminating error and crashed the NBA recorder launcher).
$ErrorActionPreference = 'Continue'
$repo = 'C:\Users\Jarvis\Documents\Kalshi-PM'
Set-Location $repo
New-Item -ItemType Directory -Force -Path "$repo\logs" | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
cmd.exe /c "`"$repo\.venv\Scripts\python.exe`" `"$repo\scripts\totals_snapshot.py`" >> `"$repo\logs\paper_tape_task.out`" 2>&1"
$code = $LASTEXITCODE
if ($code -ne 0) {
    Add-Content -Path "$repo\logs\paper_tape_task.out" -Value "$stamp totals_snapshot.py exited with code $code"
}
exit $code
