"""Static checks of the desktop START/STOP scripts (never runs them)."""
import re
import sys
from pathlib import Path, PureWindowsPath

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import overs_bot as ob  # noqa: E402

START = (ROOT / "scripts" / "start_overs_bot.ps1").read_text(encoding="utf-8")
STOP = (ROOT / "scripts" / "stop_overs_bot.ps1").read_text(encoding="utf-8")
SHORT = (ROOT / "scripts" / "make_bot_shortcuts.ps1").read_text(encoding="utf-8")


def ps_join(text, var):
    m = re.search(rf"^\${var}\s*=\s*Join-Path \$Root '([^']+)'", text, re.M)
    assert m, var
    return ROOT.joinpath(*PureWindowsPath(m.group(1)).parts).resolve()      # .ps1 paths use backslashes: split them the Windows way on any OS


def test_stop_file_paths_match_the_bot():
    assert ps_join(START, "StopFile") == ob.STOP_PATH.resolve()
    assert ps_join(STOP, "StopFile") == ob.STOP_PATH.resolve()


def test_heartbeat_paths_match_the_bot():
    assert ps_join(START, "Heartbeat") == ob.HEARTBEAT_PATH.resolve()
    assert ps_join(STOP, "Heartbeat") == ob.HEARTBEAT_PATH.resolve()


def test_start_script_starts_exactly_the_live_bot():
    assert "--live" in START and "$env:KALSHI_ENV = 'prod'" in START
    assert "reports\\output\\early_overs_bot\\overs_bot.py" in START
    assert "Kalshi overs bot (LIVE)" in START and "-NoExit" in START
    assert START.count("Start-Process") == 1
    for bad in ("Invoke-WebRequest", "Invoke-RestMethod", "curl", "git ", "Remove-Item -Recurse", "secrets", ".env",
                "demo", "Stop-Process"):
        assert bad not in START, bad
    assert START.count("Remove-Item") == 1                      # only the STOP file is removed
    assert "overs_bot.py" in START and "180" in START           # pid command-line check, 3-minute freshness


def test_stop_script_only_kills_a_verified_bot_pid():
    assert STOP.count("Stop-Process") == 1
    assert "-Force" in STOP and "overs_bot.py" in STOP and "Win32_Process" in STOP
    assert STOP.index("overs_bot.py") < STOP.index("Stop-Process")
    assert "-lt 45" in STOP and "Start-Sleep -Seconds 3" in STOP and "Start-Sleep -Seconds 10" in STOP
    assert "CHECK THE KALSHI APP" in STOP
    assert "Get-Process" not in STOP and "taskkill" not in STOP.lower()


def test_shortcuts_script():
    assert "START Kalshi bot" in SHORT and "STOP Kalshi bot" in SHORT
    assert "GetFolderPath('Desktop')" in SHORT and "WScript.Shell" in SHORT
    assert "-NoProfile -ExecutionPolicy Bypass -File" in SHORT and "WorkingDirectory = $Root" in SHORT
