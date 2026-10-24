"""#53 assembly: 40 versions (24 from #48 + 16 from #49), stats over FILLED bets, selection, luck test.

Unit of a bet: #48 = one resting order; #49 = one game per version (team 'other follows' posts 2 legs; the unit's net is the mean
over its FILLED legs, so legs of one game never count twice towards n or the LB).
"""
from __future__ import annotations

import math

import numpy as np

import nm_lib as N
import fam48 as F48
import fam49 as F49

ORDER = [("48",) + v for v in F48.VERSIONS] + [("49",) + v for v in F49.VERSIONS]      # 40, grid order, 48 first


def vlabel(v):
    return "48|" + "|".join(map(str, v[1:])) if v[0] == "48" else "49|" + F49.vname(v[1:])


def version_dict(v):
    if v[0] == "48":
        return dict(family="48", stat=v[1], M_star=v[2], beneficiaries=v[3], timing=v[4], name=vlabel(v))
    return dict(family="49", pair=v[1], direction=v[2], threshold=v[3], name=vlabel(v))


def version_from_dict(d):
    if d["family"] == "48":
        return ("48", d["stat"], d["M_star"], d["beneficiaries"], d["timing"])
    return ("49", d["pair"], d["direction"], d["threshold"])


def _frac(x, f):
    x = list(x)
    return (sum(1 for a in x if f(a)) / len(x)) if x else None


def build_units(res48, games49):
    """-> {version: [unit dict]}.  unit: id, filled, net, net1c, win_values_filled, win_values_unfilled, n_legs_*."""
    units = {v: [] for v in ORDER}
    for v48 in F48.VERSIONS:
        for b in res48["bets"][v48]:
            units[("48",) + v48].append(dict(
                id=b["key"], game=b["unit"], filled=b["filled"], net=b["net"], net1c=b["net1c"], limit=b["limit"],
                signals=1, fills=1 if b["filled"] else 0,
                fw=[b["win_value"]] if b["filled"] else [], uw=[] if b["filled"] else [b["win_value"]]))
    for v49 in F49.VERSIONS:
        for g, s, side in F49.version_units(games49, v49):
            units[("49",) + v49].append(dict(
                id=g["event"], game=g["event"], filled=s["filled"], net=s["net"], net1c=s["net1c"], limit=None,
                signals=s["legs_posted"], fills=s["legs_filled"], fw=s["filled_win_values"], uw=s["unfilled_win_values"], side=side))
    return units


def version_table(units):
    stats = {}
    for v in ORDER:
        u = units[v]
        filled = [x for x in u if x["filled"]]
        st = N.mean_lb([x["net"] for x in filled])
        fw = [w for x in u for w in x["fw"]]
        uw = [w for x in u for w in x["uw"]]
        sig = sum(x["signals"] for x in u)
        fil = sum(x["fills"] for x in u)
        st.update(
            signals=sig, fills=fil, fill_rate=(fil / sig) if sig else None, n_units=len(u), n_filled_units=len(filled),
            win_pct=(100 * _frac(fw, lambda a: a == 1.0)) if fw else None,
            mean_win_value_filled=(sum(fw) / len(fw)) if fw else None,
            unfilled_signals=len(uw), unfilled_win_pct=(100 * _frac(uw, lambda a: a == 1.0)) if uw else None,
            unfilled_mean_win_value=(sum(uw) / len(uw)) if uw else None,
            mean_net_plus1c=(sum(x["net1c"] for x in filled) / len(filled)) if filled else None)
        stats[v] = st
    return stats


# ---------------------------------------------------------------------------------------------------------------- luck test
def luck_test(res48, games49, units, reps=N.LUCK_REPS, seed=N.SEED, min_n=N.MIN_N, versions=None):
    """Real selection over all 40 versions, then `reps` replications.  Each rep (one rng stream, fixed draw order):
      #48 family: every distinct real bet (by ticker, shared across versions) is replaced by one random pool order of another active
        player, same stat and night, limit within 2c (the pool order has its own fill outcome under the same post and fill rule);
      #49 family: one random side per game (shared by all #49 versions); that side's orders under the same post and fill rule.
    Selection (best LB among versions with n FILLED >= min_n, over all 40) is re-run in every rep.  Deterministic given seed.
    -> dict(real_best_lb, selected, share, rep_best_lbs).   `versions` restricts which versions take part (default all 40);
    stat='mean' compares mean net instead of LB (used by the unseen coin-flip)."""
    versions = list(versions or ORDER)
    real_stats = version_table(units)
    sel = N.select_version(real_stats, [v for v in ORDER if v in versions], min_n)
    real_best = real_stats[sel]["lb"] if sel else None
    rng = np.random.default_rng(seed)
    # ---- #48 arrays
    keys, real_rec = [], {}
    for v in versions:
        if v[0] != "48":
            continue
        for b in res48["bets"][v[1:]]:
            if b["key"] not in real_rec:
                real_rec[b["key"]] = b
    keys = sorted(real_rec)
    kix = {k: i for i, k in enumerate(keys)}
    pool = res48["pool"]
    cand, n_fb, n_empty = N.luck_candidates(
        {k: dict(night=r["night"], stat=r["stat"], limit=r["limit"], player=r["aid"]) for k, r in real_rec.items()}, pool)
    base_f = np.array([bool(real_rec[k]["filled"]) for k in keys], bool)
    base_n = np.array([real_rec[k]["net"] if real_rec[k]["filled"] else 0.0 for k in keys], float)
    pool_f = np.array([bool(p["filled"]) for p in pool], bool)
    pool_n = np.array([p["net"] if p["filled"] else 0.0 for p in pool], float)
    ncand = np.array([len(cand[k]) for k in keys])
    vix48 = {v: np.array([kix[b["key"]] for b in res48["bets"][v[1:]]], int) for v in versions if v[0] == "48"}
    # ---- #49 arrays: per version column, per game: structural inclusion + yes/no fill + net
    v49 = [v for v in versions if v[0] == "49"]
    G = len(games49)
    incl = np.zeros((G, len(v49)), bool)
    fy, fn = np.zeros((G, len(v49)), bool), np.zeros((G, len(v49)), bool)
    ny, nn = np.zeros((G, len(v49))), np.zeros((G, len(v49)))
    for j, v in enumerate(v49):
        pair, direction, th = v[1:]
        for i, g in enumerate(games49):
            D_ = g["D_team"] if pair == "team" else g["D_1H"]
            o = g.get((pair, direction))
            if D_ is None or o is None or abs(D_) < th - F49.MM.EPS or F49.real_side(direction, D_) is None:
                continue
            incl[i, j] = True
            sy, sn = F49.unit_summary(o["yes"]), F49.unit_summary(o["no"])
            fy[i, j], fn[i, j] = sy["filled"], sn["filled"]
            ny[i, j] = sy["net"] if sy["filled"] else 0.0
            nn[i, j] = sn["net"] if sn["filled"] else 0.0
    best_lbs = []
    for _ in range(reps):
        nets, fil = base_n.copy(), base_f.copy()
        if len(keys):
            r = rng.random(len(keys))
            for j in range(len(keys)):
                if ncand[j]:
                    p = cand[keys[j]][int(r[j] * ncand[j])]
                    nets[j], fil[j] = pool_n[p], pool_f[p]
        s = rng.integers(0, 2, size=G).astype(bool) if G else np.zeros(0, bool)
        bl = -math.inf
        for v in versions:
            if v[0] == "48":
                ix = vix48[v]
                x = nets[ix][fil[ix]]
            else:
                j = v49.index(v)
                f = np.where(s, fy[:, j], fn[:, j]) & incl[:, j]
                x = np.where(s, ny[:, j], nn[:, j])[f]
            if len(x) >= min_n:
                l = x.mean() - 1.96 * x.std(ddof=1) / math.sqrt(len(x))
                bl = max(bl, l)
        best_lbs.append(float(bl))
    out = dict(real_best_lb=real_best, selected=sel, reps=reps, seed=seed, n_distinct_real_48_bets=len(keys), pool_size=len(pool),
               n_bets_nearest_price_fallback=n_fb, n_bets_no_other_player=n_empty, rep_best_lbs=best_lbs)
    if real_best is None:
        out["share"] = None
    else:
        out["share"] = float(np.mean([b >= real_best - 1e-15 for b in best_lbs]))
    return out
