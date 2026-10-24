"""Research log #61: DISCOVERY run (ET dates 2025-10-21..2026-01-22) + luck test. Never touches unseen dates.
Run ONCE: .venv/Scripts/python.exe reports/output/nba_ladder_shape/run_discovery.py"""
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ls_common as C
import ls_pipeline as P


def main():
    ev = P.select_events(C.DISC_FIRST, C.DISC_LAST)            # filtered by date before any result/final is read
    assert ev.game_date_et.min() >= C.DISC_FIRST and ev.game_date_et.max() <= C.DISC_LAST
    excl = P.excluded_games(P.quote_check_rows(ev))             # quotes only
    games, cands, skips = P.build_events(ev, exclude=excl)
    assert all(g["date"] <= C.DISC_LAST for g in games)
    # Part A
    finals = P.load_finals([g["gid"] for g in games if g["gid"] is not None])
    fits, summ, cal = P.part_a(games, finals)
    pd.DataFrame(fits).to_csv(C.HERE / "partA_games_discovery.csv", index=False)
    pd.DataFrame(cal).to_csv(C.HERE / "calibration_discovery.csv", index=False)
    # Part B
    rows = P.all_version_stats(cands)
    pd.DataFrame(rows).to_csv(C.HERE / "discovery_versions.csv", index=False)
    sel = P.select(rows)
    luck = P.luck_test(cands, reps=C.LUCK_REPS, seed=C.SEED)
    P.candidates_frame(cands).to_csv(C.HERE / "records_discovery.csv", index=False)
    skip_counts = Counter(r["skip"] or "bet_possible" for r in cands)
    res = {"split": "discovery", "dates": [C.DISC_FIRST, C.DISC_LAST], "events_in_window": int(len(ev)),
           "games_processed": len(games), "quote_check_excluded_games": excl,
           "game_level_skips": {k: len(v) for k, v in skips.items()},
           "candidate_outcomes_per_game_band_tail": dict(skip_counts),
           "part_A": {"summary": summ, "calibration": cal, "n_games_with_final": sum(f["final"] is not None for f in fits)},
           "versions": rows, "selected": sel, "luck_test": luck, "n_eligible_n_ge_60": sum(r["n"] >= C.MIN_N for r in rows),
           "pass_bar_note": "Pass bar items 1-3 apply to UNSEEN only; the discovery luck test must be PASS to proceed."}
    (C.HERE / "results_discovery.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("events_in_window", "games_processed", "game_level_skips",
                                          "candidate_outcomes_per_game_band_tail", "selected", "luck_test")}, indent=1, default=str))
    print(json.dumps(summ, indent=1))
    print(pd.DataFrame(cal).to_string())
    print(pd.DataFrame(rows).to_string())


if __name__ == "__main__":
    main()
