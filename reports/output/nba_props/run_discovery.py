"""Research log #36 DISCOVERY run: the three /model-test gates (two runs: mid 15-85c, all prices) on the discovery half of the games
(first half by scheduled start).  If ANY gate fails (either run) -> results_discovery.json with verdict
"STOP: model not better than market" and NO betting rule is run.  If all pass -> the 4 betting versions on discovery (no unseen data).
Markets of unseen-half games are dropped before anything is built.  Prices: shared raw caches (#48 PTS/REB/AST, 3PT job 1).
Refuses unless every selected market of a discovery game is cached.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analysis as A  # noqa: E402
import data as D  # noqa: E402
import pipeline as P  # noqa: E402

OUT = HERE / "results_discovery.json"
STOP_TEXT = "STOP: model not better than market"


def main():
    t0 = time.time()
    S = P.load_all("disc", progress=True)
    miss = [m for m in S["selected_missing"]]
    if miss:
        sys.exit(f"REFUSED: {len(miss)} selected markets of discovery games are not cached yet (e.g. {miss[0]['ticker']})")
    models = P.fit_models(S["rows"], S["disc"])
    P.attach_probs(S["rows"], S["mrows"], models)
    recs = P.usable(S["mrows"])
    tips = [S["games_tip"][k] for k in S["disc"]]
    res = {"period": "discovery", "n_games": len(S["disc"]), "n_usable_markets": len(recs),
           "models": {s: {"alpha": m["alpha"], "n_fit": m["n_fit"], "beta": m["beta"].tolist()} for s, m in models.items()}}
    if not recs:
        res["verdict"] = STOP_TEXT + " (no usable markets)"
        res["gates"] = {}
    else:
        res["gates"] = {"15-85c": P.gate_run(recs, tips, True), "all": P.gate_run(recs, tips, False)}
        ok = all(g["all_pass"] for g in res["gates"].values())
        res["verdict"] = "gates passed" if ok else STOP_TEXT
    bets = {}
    if res["verdict"] == "gates passed":
        res["betting_discovery"] = {}
        for theta in P.THETAS:
            for pf in (True, False):
                name = P.version_name(theta, pf)
                rep, b = P.period_report(recs, tips, theta, pf, with_tests=False)
                res["betting_discovery"][name] = rep
                bets[name] = b
    res["reported_regardless"] = P.reported_regardless(S, recs, bets)
    res["runtime_s"] = round(time.time() - t0)
    OUT.write_text(json.dumps(res, indent=1, default=str))
    print(res["verdict"])


if __name__ == "__main__":
    main()
