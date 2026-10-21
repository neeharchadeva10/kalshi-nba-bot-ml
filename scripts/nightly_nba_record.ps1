# READ-ONLY nightly NBA order-book recorder launcher (research logs #23, #26, #33).
# Run daily at 16:00 ET by the Windows scheduled task "KalshiPM NBA recorder" (created 2026-10-21 at the user's request).
#
# What it does:
#   1. Lists tonight's open KXNBA markets (public GETs) into data/ws/nba/<DATE>/markets.json.
#   2. Starts `kpm exec record` (the read-only WebSocket recorder: market data only, never orders) until 09:00Z
#      tomorrow, with tag nba-<DATE>.
# It does nothing if that night's recorder is already running, or if there are no games.
# Delete with:  schtasks /Delete /TN "KalshiPM NBA recorder" /F
$ErrorActionPreference = 'Stop'
Set-Location 'C:\Users\Jarvis\Documents\Kalshi-PM'

$etZone = [System.TimeZoneInfo]::FindSystemTimeZoneById('Eastern Standard Time')
$et = [System.TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $etZone)
$D = $et.ToString('yyMMMdd', [Globalization.CultureInfo]::InvariantCulture).ToUpper()
$tag = "nba-$D"
$log = "logs\nightly_record_$D.log"
function Log($msg) { "$([DateTime]::UtcNow.ToString('s'))Z $msg" | Out-File -FilePath $log -Append -Encoding utf8 }

# 1. Already recording tonight? Two recorders writing one raw folder would invalidate the tests.
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*exec record*--tag $tag*" }
if ($running) { Log "recorder $tag already running (pid $($running.ProcessId -join ',')); nothing to do"; exit 0 }

# 2. Discover tonight's markets (keep a backup of any earlier list for this date).
$mj = "data\ws\nba\$D\markets.json"
if (Test-Path $mj) { Copy-Item $mj "$mj.bak-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmss'))" }
& .venv\Scripts\python.exe -m kpm nfl-markets --date $D --prefix KXNBA *>> $log
if (-not (Test-Path $mj)) { Log "no markets file written; stopping"; exit 1 }
$games = @((Get-Content $mj -Raw | ConvertFrom-Json).games.PSObject.Properties).Count
if ($games -eq 0) { Log "no NBA games for $D; not starting a recorder"; exit 0 }

# 3. Start the read-only recorder until 09:00Z tomorrow (UTC).
$until = [DateTime]::UtcNow.Date.AddDays(1).AddHours(9).ToString('yyyy-MM-ddTHH:mm:ssZ')
$env:KALSHI_ENV = 'prod'
$p = Start-Process -WindowStyle Hidden -PassThru -FilePath '.venv\Scripts\python.exe' `
    -ArgumentList '-m','kpm','exec','record','--markets',$mj,'--until',$until,'--tag',$tag `
    -RedirectStandardOutput "logs\ws_recorder-$tag.out" -RedirectStandardError "logs\ws_recorder-$tag.err"
Log "started recorder $tag pid $($p.Id): $games games, until $until"
