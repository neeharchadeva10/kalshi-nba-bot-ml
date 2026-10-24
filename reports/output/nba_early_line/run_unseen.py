"""Research log #58 UNSEEN run (ET dates 2026-01-23..2026-04-12, regular season), ONCE, for the version selected on discovery.
REFUSES unless: AUDIT_OK exists as JSON {"sha256": {path: hash}} matching every code file this depends on (el_lib.AUDIT_FILES);
the discovery luck test is PASS; results_unseen.json and unseen_started.json do not exist.  unseen_started.json is written
before any outcome is loaded.  Builders never run this file and never create AUDIT_OK."""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import el_lib as E  # noqa: E402


def pass_bar(n, mean, lb, cf_share, luck_passed):
    return {"1_mean_pos_and_boot_lb_pos": bool(mean is not None and lb is not None and mean > 0 and lb > 0),
            "2_n_ge_60": bool(n >= E.MIN_N),
            "3_coin_flip_share_le_5pct": bool(cf_share is not None and cf_share <= 0.05),
            "4_discovery_luck_passed": bool(luck_passed)}


def main():
    disc = E.unseen_gates()                                     # refuses before anything is loaded
    sel = disc["selected"]
    form, h, g = sel["form"], int(sel["te_h"]), int(sel["gap"])
    (HERE / "unseen_started.json").write_text(json.dumps(
        {"started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "version": [form, h, g]}))   # BEFORE any outcome
    uni, _ = E.load_universe(E.UNSEEN_FIRST, E.UNSEEN_LAST, allow_unseen=True)
    hsum, excl = E.hourly_summary(E.hourly_check(uni, 4))
    repro = E.f56_reproduction()
    if repro["max_abs_diff"] != 0.0:
        raise SystemExit("ABORT: F56 reproduction not exactly 0.0")
    rec, u, uinfo = E.build_records(E.UNSEEN_FIRST, E.UNSEEN_LAST, allow_unseen=True, exclude=excl)
    assert rec.date.between(E.UNSEEN_FIRST, E.UNSEEN_LAST).all()
    b = E.bet_arrays(rec, form, h, g)
    mean, lo, hi = E.bootstrap_lb(b.event.values, b.net.values)
    cf = E.coin_flip(rec, form, h, g, mean) if len(b) else None
    bar = pass_bar(len(b), mean, lo, cf["share_as_good"] if cf else None, True)
    fin = E.finals_hoopr(u.hoopr_id.unique())
    rec["final"] = rec.hoopr_id.map(fin)
    keep = ["event", "date", "te_h", "T", "te", "L_e", "L_T1h_1m", "median_T1h", "n_quoted", "skip", "ticker", "strike", "yes_bid", "yes_ask",
            "age_s", "result", "net_over", "net_under", "proj_F56", "proj_F42c", "final", "hoopr_id"]
    rec[keep].to_csv(HERE / "records_unseen.csv", index=False)
    res = {"selected": sel, "n": int(len(b)), "n_games": int(b.event.nunique()), "mean_net": mean, "bootstrap_lb95": lo, "bootstrap_ub95": hi,
           "win_rate": float(b.win.mean()) if len(b) else None, "avg_paid": float(b.paid.mean()) if len(b) else None,
           "coin_flip": cf, "pass_bar": bar, "PASS": all(bar.values()), "hourly_check_T-4h": hsum, "excluded": excl,
           "f56_reproduction": repro, "games": int(len(u)), "all_versions_informational": E.all_version_rows(rec)}
    (HERE / "results_unseen.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("selected", "n", "mean_net", "bootstrap_lb95", "coin_flip", "pass_bar", "PASS")}, indent=1, default=str))


if __name__ == "__main__":
    main()
