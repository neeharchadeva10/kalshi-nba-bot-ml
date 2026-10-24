# Launcher for the forward paper league (scripts\paper_league.py). One run = decide any games whose window has passed,
# settle finished ones, rewrite logs\paper_league\summary.json. Idempotent; meant to be run by a scheduled task every
# 30 minutes. THIS FILE DOES NOT REGISTER ANY TASK. Paper only: no orders, no keys, nothing to Discord.
# Native stderr is merged by cmd.exe, NOT by PowerShell's *>> (under ErrorActionPreference=Stop that pattern
# turns any native stderr line into a terminating error and crashed the NBA recorder launcher).
$ErrorActionPreference = 'Continue'
$repo = '%USERPROFILE%\Documents\Kalshi-PM'
Set-Location $repo
New-Item -ItemType Directory -Force -Path "$repo\logs" | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
cmd.exe /c "`"$repo\.venv\Scripts\python.exe`" `"$repo\scripts\paper_league.py`" >> `"$repo\logs\paper_league_task.out`" 2>&1"
$code = $LASTEXITCODE
if ($code -ne 0) {
    Add-Content -Path "$repo\logs\paper_league_task.out" -Value "$stamp paper_league.py exited with code $code"
}
exit $code
