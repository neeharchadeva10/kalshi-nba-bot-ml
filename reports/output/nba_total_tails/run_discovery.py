"""Research log #52: DISCOVERY run (ET dates 2025-10-21..2026-01-22) + luck test. Never touches unseen dates.
Run: .venv/Scripts/python.exe reports/output/nba_total_tails/run_discovery.py"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tails_lib as L


def main():
    recs, games, skips = L.build_records(L.DISC_FIRST, L.DISC_LAST)
    assert all(L.DISC_FIRST <= r["date"] <= L.DISC_LAST for r in recs), "record outside discovery window"
    rows = [b for r in recs for b in L.bet_rows(r)]
    vrows = L.version_stats(rows)
    pd.DataFrame(vrows).to_csv(L.HERE / "discovery_versions.csv", index=False)
    pd.DataFrame(rows).to_csv(L.HERE / "records_discovery.csv", index=False)
    sel = L.select(vrows)
    luck = L.luck_test(rows, reps=500, seed=L.SEED)
    cal = L.calibration(recs)
    pd.DataFrame(cal).to_csv(L.HERE / "calibration_discovery.csv", index=False)
    streak = None
    if sel:
        sb = [r for r in rows if r["skip"] is None and (r["cp"], r["band"], r["tail"]) == (sel["cp"], sel["band"], sel["tail"])]
        streak = L.loss_streak(sb)
    skip_counts = {}
    for r in rows:
        k = f'{r["cp"]}|{r["tail"]}|{r["band"]:.2f}|{r["skip"]}'
        skip_counts[k] = skip_counts.get(k, 0) + 1
    res = {"split": "discovery", "dates": [L.DISC_FIRST, L.DISC_LAST], "games_with_ingame_records": len(games),
           "game_checkpoint_records": {cp: sum(r["cp"] == cp for r in recs) for cp in L.CHECKPOINTS},
           "game_level_skips": {k: len(v) for k, v in skips.items()},
           "versions": vrows, "n_eligible_n_ge_60": sum(v["n"] >= L.MIN_N for v in vrows),
           "selected": sel, "luck_test": luck, "selected_loss_streak": streak,
           "calibration": cal, "band_counts": L.band_counts(recs), "tail_spread_and_age": L.spread_desc(rows),
           "bet_skip_reasons": skip_counts,
           "pass_bar_note": "Pass bar items 1-3 apply to UNSEEN only; the discovery luck test must be PASS to proceed."}
    (L.HERE / "results_discovery.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("games_with_ingame_records", "game_checkpoint_records", "game_level_skips", "selected",
                                          "luck_test", "selected_loss_streak")}, indent=1, default=str))


if __name__ == "__main__":
    main()
