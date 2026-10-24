"""Research log #45: UNSEEN run (2024-25, 2025-26) for the selected version, once.
REFUSES to run unless reports/output/nba_roster_turnover/AUDIT_OK exists (Opus creates it after the audit)."""
from __future__ import annotations

import json
import sqlite3
import sys

import numpy as np
import pandas as pd

import lib

HERE = lib.HERE
K2H = {"GSW": "GS", "NYK": "NY", "NOP": "NO", "SAS": "SA", "UTA": "UTAH", "WAS": "WSH"}


def guard():
    if not (HERE / "AUDIT_OK").exists():
        sys.exit("REFUSED: AUDIT_OK not found. The unseen seasons stay closed until Opus audits and creates it.")


def main():
    guard()
    import pipeline
    d = json.loads((HERE / "results_discovery.json").read_text())
    if d["luck_test"]["share_reps_best_lb_ge_real"] > 0.05 or d["selected"] is None:
        sys.exit("REFUSED: discovery luck test failed (or no version selected); unseen is not to be opened.")
    sel = d["selected"]
    v = {k: sel[k] for k in ("market", "dir", "thr", "win", "inv")}
    res = pipeline.build_many(lib.UNSEEN)
    g = pd.concat([res[y]["games"] for y in lib.UNSEEN], ignore_index=True)
    idx, side = lib.version_sides(g, v)
    s_ref, t_ref = lib.sign_outcomes(g)
    ref = (s_ref if v["market"] == "spread" else t_ref)[idx]
    x = lib.nets(ref, side)
    st = lib.stats(x)
    lo, hi = lib.boot_lb(x)
    share = lib.coin_flip(g, v, st["mean"])
    out = {"version": lib.vname(v), "unseen": st, "boot95": [lo, hi], "coin_flip_share_as_good": share,
           "pass_1_mean_and_boot_lb_positive": bool(st["mean"] > 0 and lo > 0), "pass_2_n_ge_40": bool(st["n"] >= 40),
           "pass_3_coin_flip_le_5pct": bool(share <= 0.05), "pass_4_luck_test_passed": True}
    if v["market"] == "total":
        kt = pd.read_csv(HERE / "kalshi_main.csv")
        kt["home"] = kt.home.map(lambda a: K2H.get(a, a))
        kt["away"] = kt.away.map(lambda a: K2H.get(a, a))
        con = sqlite3.connect(f"file:{(lib.TOTALS_PACE / 'totals.sqlite').as_posix()}?mode=ro", uri=True)
        mk = pd.read_sql("SELECT ticker,result,status FROM markets", con)
        con.close()
        kt = kt.dropna(subset=["ticker"]).merge(mk, on="ticker")
        sel_g = g.iloc[idx].assign(side=side)
        sel_g = sel_g[sel_g.season_end == 2026]
        m = sel_g.merge(kt, on=["date", "home", "away"])
        m = m[(m.status == "finalized") & m.result.isin(["yes", "no"])]
        k = [lib.kalshi_net("over" if r.side > 0 else "under", r.bid, r.ask, r.result == "yes") for r in m.itertuples()]
        out["kalshi_crosscheck"] = {"n": len(k), "mean_net_per_contract": float(np.mean(k)) if k else None,
                                    "pass_5": (None if len(k) < 20 else bool(np.mean(k) >= -0.02)),
                                    "note": "information only if n < 20"}
    (HERE / "results_unseen.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
