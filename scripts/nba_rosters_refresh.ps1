# Refresh NBA rosters + player moves from the free hoopR repo (read-only GET; idempotent).
# 2027 files are re-downloaded only if the cached copy is older than 12 h. NOT registered as a scheduled task.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
& $py (Join-Path $root "scripts\nba_rosters.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $py (Join-Path $root "scripts\nba_player_moves.py")
exit $LASTEXITCODE
