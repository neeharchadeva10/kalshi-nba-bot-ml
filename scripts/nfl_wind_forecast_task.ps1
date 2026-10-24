# Launcher for the forward NFL wind-forecast tape (scripts\nfl_wind_forecast.py). One run = one forecast fetch for every
# open-air game kicking off in the next 8 days. Meant to be run by a scheduled task (e.g. every 6 h on game weeks);
# THIS FILE DOES NOT REGISTER ANY TASK.
# Native stderr is merged by cmd.exe, NOT by PowerShell's *>> (under ErrorActionPreference=Stop that pattern
# turns any native stderr line into a terminating error and crashed the NBA recorder launcher).
$ErrorActionPreference = 'Continue'
$repo = '%USERPROFILE%\Documents\Kalshi-PM'
Set-Location $repo
New-Item -ItemType Directory -Force -Path "$repo\logs" | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
cmd.exe /c "`"$repo\.venv\Scripts\python.exe`" `"$repo\scripts\nfl_wind_forecast.py`" >> `"$repo\logs\nfl_wind_forecast_task.out`" 2>&1"
$code = $LASTEXITCODE
if ($code -ne 0) {
    Add-Content -Path "$repo\logs\nfl_wind_forecast_task.out" -Value "$stamp nfl_wind_forecast.py exited with code $code"
}
exit $code
