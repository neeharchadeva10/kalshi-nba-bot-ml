# STOP (kill) button for the early-overs bot. Asks it to stop via the STOP file; kills it only if it does not.
$ErrorActionPreference = 'Stop'
$Root      = Split-Path -Parent $PSScriptRoot
$Heartbeat = Join-Path $Root 'logs\early_overs_bot.heartbeat'
$StopFile  = Join-Path $Root 'reports\output\early_overs_bot\STOP'
$Launcher  = Join-Path $Root 'logs\early_overs_bot_launcher.log'

function Write-Log($msg) {
    $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Launcher)
    Add-Content -Path $Launcher -Value ("{0}  stop: {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
}

function Read-Heartbeat {
    try { return (Get-Content $Heartbeat -Raw | ConvertFrom-Json) } catch { return $null }
}

function Finish($msg) {
    Write-Host $msg
    Write-Log $msg
    Start-Sleep -Seconds 10
    exit 0
}

$hb = Read-Heartbeat
$null = New-Item -ItemType File -Force -Path $StopFile
Write-Host "STOP file created. Waiting up to 45 s for the bot to stop by itself..."
Write-Log "STOP file created"

$waited = 0
while ($waited -lt 45) {
    $hb = Read-Heartbeat
    if ($null -eq $hb -or $hb.live -eq $false) {
        Finish "Bot stopped by itself (heartbeat live:false). Instant-fill orders normally leave nothing open; check the Kalshi app if unsure."
    }
    Start-Sleep -Seconds 3
    $waited += 3
}

$hb = Read-Heartbeat
if ($null -eq $hb -or $hb.live -eq $false) {
    Finish "Bot stopped by itself (heartbeat live:false)."
}
$botPid = $hb.pid
$p = $null
if ($botPid) { $p = Get-CimInstance Win32_Process -Filter "ProcessId = $botPid" -ErrorAction SilentlyContinue }
if ($p -and $p.CommandLine -and $p.CommandLine -like '*overs_bot.py*') {
    Stop-Process -Id $botPid -Force
    # tell the notifier the bot is gone (the killed process cannot do it itself)
    try {
        $ts  = [DateTimeOffset]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss+00:00')
        $obj = [ordered]@{ ts_utc = $ts; pid = $botPid; live = $false; placing = $false; night = $hb.night }
        $tmp = "$Heartbeat.tmp"
        ($obj | ConvertTo-Json -Compress) | Set-Content -Path $tmp -Encoding UTF8
        Move-Item -Path $tmp -Destination $Heartbeat -Force
    } catch { }
    Finish "Bot did not stop in 45 s: killed pid $botPid (verified overs_bot.py). A hard kill cannot cancel orders: CHECK THE KALSHI APP for any open order (instant-fill orders normally leave none)."
} else {
    Finish "Bot still shows live but pid $botPid is not an overs_bot.py process; killed nothing. Close the bot window by hand (Ctrl+C) and check the Kalshi app."
}
