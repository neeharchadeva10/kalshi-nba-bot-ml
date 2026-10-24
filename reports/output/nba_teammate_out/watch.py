"""Babysitter for the overnight job (research log #48).  Every 2 minutes:
  * if the downloader (props_download.py) is not running and the STOP file is absent and markets remain uncached -> restart it;
  * once discovery_download_complete.flag exists and results_discovery.json does not -> run run_discovery.py (DISCOVERY ONLY).
It never runs run_unseen.py and never creates AUDIT_OK.  Log: watch.log.   Stop it by creating the file STOP_WATCH.
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
sys.path.insert(0, str(HERE))
import tm_lib as L  # noqa: E402


def log(m):
    with (HERE / "watch.log").open("a") as fh:
        fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + m + "\n")


def downloader_running():
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ForEach-Object { $_.CommandLine }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60).stdout
    return "props_download.py" in out


def n_uncached():
    return sum(1 for r in L.read_selection() if not L.candle_path(r["ticker"], r["game_date"]).exists())


def main():
    log("watcher start")
    last_n = None
    tries = 0
    while not (HERE / "STOP_WATCH").exists():
        try:
            flag = (HERE / "discovery_download_complete.flag").exists()
            if flag and not (HERE / "results_discovery.json").exists() and tries < 3:
                tries += 1
                log("discovery download complete -> running run_discovery.py")
                r = subprocess.run([PY, str(HERE / "run_discovery.py")], capture_output=True, text=True, cwd=str(ROOT))
                (HERE / "run_discovery_stdout.log").write_text(r.stdout + "\n" + r.stderr)
                log(f"run_discovery.py exit {r.returncode}")
            if not (HERE / "STOP").exists() and not downloader_running():
                n = n_uncached()
                if n > 0:
                    log(f"downloader not running, {n} markets uncached -> restart")
                    subprocess.Popen([PY, str(HERE / "props_download.py")], cwd=str(ROOT),
                                     stdout=open(HERE / "download_stdout.log", "a"), stderr=subprocess.STDOUT)
                elif last_n != 0:
                    log("all selected markets cached")
                last_n = n
        except Exception as e:  # noqa: BLE001
            log(f"watcher error {type(e).__name__}: {e}")
        time.sleep(120)
    log("watcher stop")


if __name__ == "__main__":
    main()
