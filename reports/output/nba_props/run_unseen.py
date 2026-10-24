"""Research log #36 UNSEEN run (second half of the games), ONCE.  REFUSES unless:
  * reports/output/nba_props/AUDIT_OK exists (created only by the Opus audit);
  * results_unseen.json does not exist;
  * threes_download_complete.flag exists and #48's PTS/REB/AST selection and the 3PT selection are fully cached;
  * results_discovery.json exists and its verdict is not STOP.
Models are refitted on discovery games only (same as run_discovery.py); the 4 betting versions are evaluated on unseen games; pass bar on
the primary version (theta 3c, 15-85c): six conditions of the pre-registration.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import guards as G  # noqa: E402

OUT = HERE / "results_unseen.json"


def main():
    import numpy as np
    import analysis as A
    import data as D
    import pipeline as P
    reasons = G.unseen_refusals(HERE, D.download_status())
    stop = G.discovery_stop(HERE)
    if stop:
        reasons.append(stop)
    if reasons:
        sys.exit("REFUSED: " + "; ".join(reasons))
    t0 = time.time()
    disc_res = json.loads((HERE / "results_discovery.json").read_text())
    S = P.load_all("all", progress=True)
    if S["selected_missing"]:
        sys.exit(f"REFUSED: {len(S['selected_missing'])} selected markets not cached")
    models = P.fit_models(S["rows"], S["disc"])
    P.attach_probs(S["rows"], S["mrows"], models)
    recs = [r for r in P.usable(S["mrows"]) if r["gk"] in S["uns"]]
    tips = [S["games_tip"][k] for k in S["uns"]]
    versions, bets = {}, {}
    for theta in P.THETAS:
        for pf in (True, False):
            name = P.version_name(theta, pf)
            rep, b = P.period_report(recs, tips, theta, pf, with_tests=True)
            versions[name] = rep
            bets[name] = b
    sel = [r for r in recs if 0.15 <= (r["bid"] + r["ask"]) / 2 <= 0.85]
    y = np.array([1.0 if r["result"] == "yes" else 0.0 for r in sel])
    bm = A.brier([r["p"] for r in sel], y) if sel else float("inf")
    bk = A.brier([(r["bid"] + r["ask"]) / 2 for r in sel], y) if sel else float("-inf")
    prim = "theta3c_15-85c"
    disc_prim = disc_res["betting_discovery"][prim]
    bar = P.pass_bar(disc_prim, versions[prim], bm, bk)
    res = {"period": "unseen", "n_games": len(S["uns"]), "n_usable_markets": len(recs), "versions": versions,
           "primary": prim, "pass_bar": bar, "PASS": bool(all(bar.values())),
           "unseen_brier_15_85": {"model": bm, "mid": bk, "n": len(sel)},
           "reported_regardless": P.reported_regardless(S, recs, bets), "runtime_s": round(time.time() - t0)}
    OUT.write_text(json.dumps(res, indent=1, default=str))
    print("PASS" if res["PASS"] else "FAIL", bar)


if __name__ == "__main__":
    main()
