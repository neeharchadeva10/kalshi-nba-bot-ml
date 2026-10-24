"""#53 UNSEEN run, ONCE, for the version selected on discovery.  Never run by the builder.

REFUSES unless ALL hold (checked first, before any data is touched):
  1. nba_maker_nearmiss/AUDIT_OK exists (created by Opus only);  2. results_unseen.json does not exist;
  3. results_discovery.json exists with a selected version and luck_test.verdict == PASS;
  4. for a #49-family selection: the #49 unseen team/1H download exists (nba_internal_mismatch/candles.sqlite covers the unseen games).
Pass bar (PREREGISTRATION.md): mean net > 0 and bootstrap-by-game 95% LB > 0 (10,000 draws, seed 20261022); >= 60 unseen FILLED bets;
the coin flip under the same fill rule: <= 5% of 500 copies do as well (on mean net); the discovery luck test passed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nm_lib as N  # noqa: E402


def gate():
    ok, msg = N.unseen_gate(HERE)
    if not ok:
        sys.exit(msg)


def main():
    gate()
    import numpy as np
    import fam48 as F48
    import fam49 as F49
    import nm_pipeline as P
    disc = json.loads(N.RESULTS_DISC.read_text())
    v = P.version_from_dict(disc["selected_version"])
    fee_types = N.load_fee_types()
    k = json.loads(F49.K_FILE.read_text())["k"]
    rng = np.random.default_rng(N.SEED)
    res = dict(version=disc["selected_version"])
    if v[0] == "49":
        games, skips = F49.build_games("unseen", k, fee_types)
        us = [(g, s) for g, s, _ in F49.version_units(games, v[1:])]
        filled = [(g, s) for g, s in us if s["filled"]]
        nets = [s["net"] for _, s in filled]
        by_game = {g["event"]: [s["net"]] for g, s in filled}
        pair, direction, th = v[1:]
        ny, nn, fy, fn = [], [], [], []
        for g, _ in us:
            o = g[(pair, direction)]
            sy, sn = F49.unit_summary(o["yes"]), F49.unit_summary(o["no"])
            ny.append(sy["net"] or 0.0); nn.append(sn["net"] or 0.0); fy.append(sy["filled"]); fn.append(sn["filled"])
        ny, nn, fy, fn = map(np.array, (ny, nn, fy, fn))
        real_mean = float(np.mean(nets)) if nets else None
        as_good = 0
        for _ in range(N.LUCK_REPS):
            s = rng.integers(0, 2, size=len(us)).astype(bool)
            f = np.where(s, fy, fn)
            x = np.where(s, ny, nn)[f]
            if real_mean is not None and len(x) and x.mean() >= real_mean - 1e-15:
                as_good += 1
        res.update(n_filled=len(nets), n_games=len(games), game_skips=skips, signals_posted=len(us))
    else:
        box, reports = F48.L.Box(), F48.L.Reports()
        sel = [r for r in F48.L.read_selection() if F48.L.UNSEEN_LO <= r["game_date"] <= box.reg_end]
        settle = F48.load_settlements([r["ticker"] for r in sel])
        out = F48.collect(box, reports, sel, F48.L.UNSEEN_LO, box.reg_end, settle, fee_types)
        bets = out["bets"][v[1:]]
        filled = [b for b in bets if b["filled"]]
        nets = [b["net"] for b in filled]
        by_game = {}
        for b in filled:
            by_game.setdefault(b["unit"], []).append(b["net"])
        real = {b["key"]: dict(night=b["night"], stat=b["stat"], limit=b["limit"], player=b["aid"]) for b in bets}
        cand, n_fb, n_empty = N.luck_candidates(real, out["pool"])
        pf = np.array([p["filled"] for p in out["pool"]], bool)
        pn = np.array([p["net"] if p["filled"] else 0.0 for p in out["pool"]], float)
        keys = sorted(real)
        bykey = {b["key"]: b for b in bets}
        real_mean = float(np.mean(nets)) if nets else None
        as_good = 0
        for _ in range(N.LUCK_REPS):
            x = []
            for kk in keys:
                c = cand[kk]
                if c:
                    i = c[int(rng.random() * len(c))]
                    if pf[i]:
                        x.append(pn[i])
                elif bykey[kk]["filled"]:
                    x.append(bykey[kk]["net"])
            if real_mean is not None and x and float(np.mean(x)) >= real_mean - 1e-15:
                as_good += 1
        res.update(n_filled=len(nets), signals_posted=len(bets), counts=dict(out["counts"]))
    if nets:
        mean, lb = N.bootstrap_lb(by_game, N.BOOT_DRAWS, N.SEED)
        share = as_good / N.LUCK_REPS
        res.update(mean_net=mean, bootstrap_lb=lb, coin_flip_share_as_good=share)
        res["pass_bar"] = dict(mean_and_lb_positive=bool(mean > 0 and lb > 0), at_least_60_filled=len(nets) >= 60,
                               coin_flip_ok=bool(share <= 0.05), discovery_luck_passed=disc["luck_test"]["verdict"] == "PASS")
        res["PASS"] = all(res["pass_bar"].values())
    N.RESULTS_UNSEEN.write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
