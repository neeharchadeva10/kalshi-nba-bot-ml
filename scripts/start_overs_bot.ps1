# START button for the early-overs bot (one click). Does nothing if the bot is already running.
$ErrorActionPreference = 'Stop'
$Root      = Split-Path -Parent $PSScriptRoot
$Heartbeat = Join-Path $Root 'logs\early_overs_bot.heartbeat'
$StopFile  = Join-Path $Root 'reports\output\early_overs_bot\STOP'
$Launcher  = Join-Path $Root 'logs\early_overs_bot_launcher.log'

function Write-Log($msg) {
    $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Launcher)
    Add-Content -Path $Launcher -Value ("{0}  start: {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
}

function Test-BotPid($botPid) {
    # True only if that PID is alive AND its command line contains overs_bot.py
    if (-not $botPid) { return $false }
    $p = Get-CimInstance Win32_Process -Filter "ProcessId = $botPid" -ErrorAction SilentlyContinue
    return [bool]($p -and $p.CommandLine -and $p.CommandLine -like '*overs_bot.py*')
}

# 1) already running? (fresh heartbeat, live:true, and the pid really is overs_bot.py)
if (Test-Path $Heartbeat) {
    try {
        $hb  = Get-Content $Heartbeat -Raw | ConvertFrom-Json
        $age = ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($hb.ts_utc)).TotalSeconds
        if ($hb.live -eq $true -and $age -le 180) {
            if (Test-BotPid $hb.pid) {
                Write-Host "Bot already running (pid $($hb.pid))"
                Write-Log "already running (pid $($hb.pid)); nothing started"
                Start-Sleep -Seconds 5
                exit 0
            }
            Write-Host "Heartbeat is fresh but pid $($hb.pid) is not overs_bot.py (killed bot?). Starting a new one."
        }
    } catch {
        Write-Host "Could not read the heartbeat ($($_.Exception.Message)); continuing."
    }
}

# 2) clear a leftover STOP file, otherwise the new run would stop immediately
if (Test-Path $StopFile) {
    Remove-Item $StopFile -Force
    Write-Host "Cleared the old STOP file."
    Write-Log "cleared STOP file"
}

# 3) start the bot in a NEW visible window that stays open (Ctrl+C there stops it)
$cmd = "`$Host.UI.RawUI.WindowTitle = 'Kalshi overs bot (LIVE)'; Set-Location '$Root'; " +
       "`$env:KALSHI_ENV = 'prod'; " +
       "& '.\.venv\Scripts\python.exe' 'reports\output\early_overs_bot\overs_bot.py' --live"
Start-Process -FilePath 'powershell.exe' -WorkingDirectory $Root `
    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-Command', $cmd)
Write-Host "Started the bot in a new window titled 'Kalshi overs bot (LIVE)'."
Write-Log "started overs_bot.py --live (KALSHI_ENV=prod) in a new window"
Start-Sleep -Seconds 5
