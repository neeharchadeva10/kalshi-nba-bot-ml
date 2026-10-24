"""Research log #46: DISCOVERY run (ET dates 2025-10-21..2026-01-22) + luck test. Never touches unseen dates.
Run: .venv/Scripts/python.exe reports/output/nba_ingame_overreaction/run_discovery.py [--allow-partial]"""
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C
import pipeline as P


def coverage():
    ev = pd.read_csv(C.INJ / "events_list.csv")
    ev = ev[(ev.game_date_et <= C.DISC_LAST) & ev.hoopr_tip.notna()]
    inj = sqlite3.connect(f"file:{(C.INJ / 'candles.sqlite').as_posix()}?mode=ro", uri=True)
    tk = {t for (t,) in inj.execute("SELECT ticker FROM mkts WHERE event IN (%s)" % ",".join("?" * len(ev)), list(ev.event))}
    ing = sqlite3.connect(f"file:{(C.HERE / 'candles_ingame.sqlite').as_posix()}?mode=ro", uri=True)
    done = dict(ing.execute("SELECT ticker, note FROM done").fetchall())
    miss = [t for t in tk if done.get(t) not in ("ok", "http_404")]
    return {"discovery_markets": len(tk), "not_downloaded": len(miss), "notes": dict(Counter(done[t] for t in tk if t in done))}


def main():
    cov = coverage()
    print("coverage", cov, flush=True)
    partial = cov["not_downloaded"] > 0
    if partial and "--allow-partial" not in sys.argv:
        print("REFUSING: discovery download incomplete (use --allow-partial only for debugging)")
        sys.exit(2)
    recs, games, skips = P.build_records(C.DISC_FIRST, C.DISC_LAST)
    assert all(v["date"] <= C.DISC_LAST for v in recs.values())
    rows = P.all_version_stats(recs)
    pd.DataFrame(rows).to_csv(C.HERE / "discovery_versions.csv", index=False)
    sel = P.select(rows)
    luck = P.luck_test(recs, reps=500, seed=C.SEED)
    cal = P.calibration(recs)
    pd.DataFrame([{"checkpoint": k, **v} for k, v in cal.items()]).to_csv(C.HERE / "calibration_discovery.csv", index=False)
    # per game-checkpoint records for audit
    keep = ["game", "date", "cp", "skip", "C_ts", "D", "lag", "age_s", "L0", "LC", "move", "final", "strike", "ticker",
            "yes_bid", "yes_ask", "spread", "result", "exit_t", "xbid", "xask"]
    pd.DataFrame([{k: r.get(k) for k in keep} for r in recs.values()]).to_csv(C.HERE / "records_discovery.csv", index=False)
    dg = P.diag_blocks(recs)
    res = {"split": "discovery", "dates": [C.DISC_FIRST, C.DISC_LAST], "partial_download": partial, "coverage": cov,
           "games_processed": len(games), "game_level_skips": {k: len(v) for k, v in skips.items()},
           "game_level_skip_events": {k: v[:20] for k, v in skips.items()},
           "checkpoint_skips": dg["checkpoint_skips"],
           "selected": sel, "luck_test": luck, "calibration": cal,
           "checkpoint_spread_main_strike": dg["checkpoint_spread_main_strike"], "decision_lag_s_after_C_plus_60": dg["decision_lag_s_after_C_plus_60"], "main_strike_quote_age_s": dg["main_strike_quote_age_s"],
           "n_versions": len(rows), "n_eligible_n_ge_60": sum(r["n"] >= C.MIN_N for r in rows),
           "pass_bar_note": "Pass bar items 1-3 apply to UNSEEN only; discovery luck test below must be PASS to proceed."}
    (C.HERE / "results_discovery.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("games_processed", "game_level_skips", "checkpoint_skips", "selected", "luck_test",
                                          "calibration")}, indent=1, default=str))


if __name__ == "__main__":
    main()
