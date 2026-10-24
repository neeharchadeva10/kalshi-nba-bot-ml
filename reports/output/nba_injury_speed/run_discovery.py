"""Research log #44: DISCOVERY run only (ET game dates 2025-10-21 .. 2026-01-22).
Run: .venv/Scripts/python.exe reports/output/nba_injury_speed/run_discovery.py"""
import json

import common as C
import pipeline as PL


def main():
    rows, res = PL.analyze_split(C.DISC_FIRST, C.DISC_LAST, "discovery")
    PL.write_rows(rows, C.HERE / "events_discovery.csv")
    (C.HERE / "results_discovery.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("events_in_window_ok", "events_by_direction", "trade_skips_by_reason", "n_traded",
                                           "descriptive", "kill_rule", "trading", "pass_bar", "pass_bar_all")}, indent=1, default=str))


if __name__ == "__main__":
    main()
