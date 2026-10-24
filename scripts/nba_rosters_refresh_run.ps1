# Launched daily 09:00 ET by the scheduled task "KalshiPM NBA rosters refresh". Read-only GETs to the hoopR GitHub data repo.
# Rebuilds data/derived/rosters_current.csv, player_moves.csv and docs/ROSTERS_CURRENT.md, ROSTERS_CHECK.md, PLAYER_MOVES.md.
Set-Location '%USERPROFILE%\Documents\Kalshi-PM'
& powershell -NoProfile -ExecutionPolicy Bypass -File scripts\nba_rosters_refresh.ps1 *>> logs\nba_rosters_refresh.out
