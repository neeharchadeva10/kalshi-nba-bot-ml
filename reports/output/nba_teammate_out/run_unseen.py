"""Research log #48 UNSEEN run (2026-01-23 .. end of the regular season), ONCE, for the version selected on discovery.
REFUSES to run unless reports/output/nba_teammate_out/AUDIT_OK exists (created by the user/Opus after the code audit).
Pass bar (PREREGISTRATION.md): mean net > 0 and bootstrap-by-game LB > 0 (10,000 draws, seed 20261022); >= 60 bets;
price-matched coin flip: <= 5% of 500 copies do as well; discovery luck test passed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if not (HERE / "AUDIT_OK").exists():
    sys.exit("REFUSED: reports/output/nba_teammate_out/AUDIT_OK does not exist (the audit has not been signed off).")

sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402

import analysis as A  # noqa: E402
import tm_lib as L  # noqa: E402


def main():
    disc = json.loads((HERE / "results_discovery.json").read_text())
    sv = disc["selected_version"]
    if sv is None:
        sys.exit("no version was selected on discovery (no version with n >= 60): nothing to run on unseen")
    version = (sv["stat"], sv["M_star"], sv["beneficiaries"], sv["timing"])
    box = L.Box()
    reports = L.Reports()
    reg_end = box.reg_end
    sel = [r for r in L.read_selection() if L.UNSEEN_LO <= r["game_date"] <= reg_end]
    settle = A.load_settlements([r["ticker"] for r in sel])
    out = A.collect(box, reports, sel, L.UNSEEN_LO, reg_end, settle)
    bets = out["bets"][version]
    nets = [b["net"] for b in bets]
    by_game = {}
    for b in bets:
        by_game.setdefault(b["game"], []).append(b["net"])
    res = dict(version=version, n=len(bets), counts=dict(out["counts"]), skips=dict(out["vskips"][version]))
    if bets:
        mean, lb = L.bootstrap_lb(by_game, 10000, L.SEED)
        real = {b["key"]: dict(night=b["night"], stat=b["stat"], ask=b["ask"], player=b["aid"], net=b["net"]) for b in bets}
        cand, n_fb, n_empty = L.luck_candidates(real, out["pool"])
        pool_nets = np.array([p["net"] for p in out["pool"]], float)
        rng = np.random.default_rng(L.SEED)
        keys = sorted(real)
        real_mean = float(np.mean([real[k]["net"] for k in keys]))
        as_good = 0
        for _ in range(500):
            m = []
            for k in keys:
                c = cand[k]
                m.append(pool_nets[c[int(rng.random() * len(c))]] if c else real[k]["net"])
            as_good += 1 if float(np.mean(m)) >= real_mean else 0
        share = as_good / 500
        res.update(mean_net=mean, bootstrap_lb=lb, win_pct=100 * sum(1 for b in bets if b["settle"] == 1.0) / len(bets),
                   coin_flip_share=share, n_fallback=n_fb, n_no_other_player=n_empty)
        res["pass_bar"] = dict(
            mean_and_lb_positive=bool(mean > 0 and lb > 0), at_least_60_bets=len(bets) >= 60, coin_flip_ok=bool(share <= 0.05),
            discovery_luck_passed=bool(disc["luck_test"]["share"] is not None and disc["luck_test"]["share"] <= 0.05))
        res["PASS"] = all(res["pass_bar"].values())
    (HERE / "results_unseen.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
