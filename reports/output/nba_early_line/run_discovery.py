"""Research log #58 DISCOVERY run (ET dates 2025-10-21..2026-01-22 only; filtered before any result is read).
18 versions, n >= 60 selection by LB, the luck test, and the 'reported regardless' blocks.
Run ONCE: .venv/Scripts/python.exe reports/output/nba_early_line/run_discovery.py      (--smoke: 10 days, writes nothing)"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import el_lib as E  # noqa: E402


def main(smoke=False):
    t0 = time.time()
    first, last = E.DISC_FIRST, E.DISC_LAST
    if smoke:
        last = "2025-10-31"
    # 1. F56 reproduction against #56 BEFORE any bet is computed
    repro = E.f56_reproduction()
    print("F56 reproduction:", repro, flush=True)
    if not (repro["max_abs_diff"] == 0.0 and repro["frozen_json_sha256_matches"]):
        raise SystemExit("ABORT: F56 reproduction is not exactly 0.0")
    # 2. universe (dates filtered first) and the hourly-vs-1m exclusion
    uni, _ = E.load_universe(first, last)
    hsum, excl = E.hourly_summary(E.hourly_check(uni, 4))
    print("hourly check:", {k: v for k, v in hsum.items() if k != "excluded_games"}, flush=True)
    # 3. records
    rec, u, uinfo = E.build_records(first, last, exclude=excl)
    assert rec.date.between(first, last).all() and rec.date.max() <= E.DISC_LAST
    fin = E.finals_hoopr(u.hoopr_id.unique())
    rec["final"] = rec.hoopr_id.map(fin)
    # 4. versions, selection, luck
    rows = E.all_version_rows(rec)
    sel = E.select(rows)
    luck = E.luck_test(rec, reps=(40 if smoke else E.REPS))
    extra = None
    if sel:
        b = E.bet_arrays(rec, sel["form"], sel["te_h"], sel["gap"])
        mean, lo, hi = E.bootstrap_lb(b.event.values, b.net.values, draws=(500 if smoke else E.N_BOOT))
        cf = E.coin_flip(rec, sel["form"], sel["te_h"], sel["gap"], mean, reps=(40 if smoke else E.REPS))
        extra = {"selected_version_game_bootstrap": {"mean": mean, "lb2.5": lo, "ub97.5": hi, "n_games": int(b.event.nunique())},
                 "selected_version_coin_flip_informational": cf}
    # 5. reported regardless
    mae = E.mae_block(rec, fin, n_boot=(500 if smoke else E.N_BOOT))
    drift = E.drift_block(rec)
    drift_alt = E.drift_block(rec, alt_col="median_T1h")
    avail = E.availability_block(rec)
    res = {"split": "discovery", "dates": [first, last], "games_in_universe": int(len(uni)), "games_analysed": int(u.event.nunique()),
           "excluded_hourly_gap_gt_1.5": excl, "hourly_check_T-4h": hsum, "f56_reproduction": repro,
           "n_records": int(len(rec)), "record_skips": {f"T-{h}h|{k}": int(v) for (h, k), v in
                                                           rec.assign(skip=rec.skip.fillna("ok")).groupby(["te_h", "skip"]).size().items()},
           "versions": rows, "selected": sel, "n_eligible_n_ge_60": sum(r["n"] >= E.MIN_N for r in rows),
           "luck_test": luck, **(extra or {}), "mae_by_t_e": mae, "drift_L_T1h_1m_minus_L_e": drift,
           "drift_alt_T1h_median_42_minus_L_e": drift_alt, "availability": avail, "runtime_s": time.time() - t0,
           "pass_bar_note": "Pass bar items 1-3 apply to UNSEEN only; the discovery luck test must be PASS to proceed."}
    if smoke:
        print(json.dumps({k: res[k] for k in ("n_records", "record_skips", "selected", "luck_test", "runtime_s")}, indent=1, default=str))
        print(json.dumps(mae, indent=1, default=str)[:1500])
        return
    pd.DataFrame(rows).to_csv(HERE / "discovery_versions.csv", index=False)
    keep = ["event", "date", "te_h", "T", "te", "L_e", "L_T1h_1m", "median_T1h", "n_quoted", "skip", "ticker", "strike", "yes_bid", "yes_ask",
            "age_s", "result", "net_over", "net_under", "proj_F56", "proj_F42c", "final", "hoopr_id"]
    rec[keep].to_csv(HERE / "records_discovery.csv", index=False)
    (HERE / "results_discovery.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("n_records", "record_skips", "selected", "luck_test", "runtime_s")}, indent=1, default=str))


if __name__ == "__main__":
    main(smoke="--smoke" in sys.argv)
