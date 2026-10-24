"""Fits r1 = mean over nflverse 2015-2024 games of (end-of-Q2 points / final points) and writes ../main_out/r1.json.
No Kalshi data involved (allowed by the pre-registration).  Run: .venv/Scripts/python.exe reports/output/nfl_derivative_totals/main/fit_r1_main.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ldr_lib as L  # noqa: E402

if __name__ == "__main__":
    g = L.N.load_games()
    ex = L.implausible_ids()
    halves = L.load_halves(L.R1_SEASONS, exclude=ex)
    res = L.fit_r1(g, halves)
    res["n_excluded_ids_listed"] = len(ex)
    L.MAIN_OUT.mkdir(exist_ok=True)
    (L.MAIN_OUT / "r1.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))
