"""Research log #51: DISCOVERY run (ET dates 2025-10-21..2026-01-22) + luck test. Never touches unseen dates. No network.
Run: .venv/Scripts/python.exe reports/output/nba_late_fouling/run_discovery.py [--allow-partial]"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib as L


def main():
    cov = L.C46.coverage(L.DISC_FIRST, L.DISC_LAST)
    print("coverage", cov, flush=True)
    partial = cov["not_downloaded"] > 0
    if partial and "--allow-partial" not in sys.argv:
        print("REFUSING: discovery download incomplete (use --allow-partial only for debugging)")
        sys.exit(2)
    recs, games, skips = L.build_records(L.DISC_FIRST, L.DISC_LAST)
    assert all(v["date"] <= L.DISC_LAST for v in recs.values())
    rows = L.all_version_stats(recs)
    pd.DataFrame(rows).to_csv(L.HERE / "discovery_versions.csv", index=False)
    sel = L.select(rows)
    luck = L.luck_test(recs, reps=500, seed=L.SEED)
    cal = L.calibration_and_descriptives(recs)
    L.records_frame(recs).to_csv(L.HERE / "records_discovery.csv", index=False)
    dg = L.diag_blocks(recs)
    res = {"split": "discovery", "dates": [L.DISC_FIRST, L.DISC_LAST], "partial_download": partial, "coverage": cov,
           "games_processed": len(games), "game_level_skips": {k: len(v) for k, v in skips.items()},
           "game_level_skip_events": {k: v[:20] for k, v in skips.items()},
           "versions": rows, "selected": sel, "luck_test": luck, "calibration_by_R_margin": cal, **dg,
           "n_versions": len(rows), "n_eligible_n_ge_60": sum(r["n"] >= L.MIN_N for r in rows),
           "pass_bar_note": "Pass bar items 1-3 apply to UNSEEN only; discovery luck test must be PASS to proceed."}
    (L.HERE / "results_discovery.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("games_processed", "game_level_skips", "checkpoint_skips", "selected", "luck_test")},
                     indent=1, default=str))


if __name__ == "__main__":
    main()
