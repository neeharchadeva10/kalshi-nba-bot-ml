"""Research log #29 analysis (reports/output/nba_model/PREREGISTRATION.md). Implements the pre-registration as written.

Run: .venv/Scripts/python.exe reports/output/nba_model/model.py   -> results.json + README.md
Read-only: uses only data/nba_drift.sqlite and data/nba_model.sqlite (already downloaded). Never imports kpm.execution.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import features as F  # noqa: E402

drift = F.drift
SEED = 20261021
SIZE = 10
THETAS = [0.02, 0.04, 0.06]
ENTRY_S = 3600
FULL_COLS = ["elo_diff", "net_diff", "rest_diff", "b2b_diff", "miss_diff"]
ELO_COLS = ["elo_diff"]
INTERP = [
    "Quotes: T-1h = last hourly candle closing at or before tip - 3600 s (drift.at); a game is usable when BOTH team "
    "markets have a valid bid/ask there (drift.valid_quote), as in #24. No staleness limit. The close candle is not needed.",
    "Market probability for Brier / log loss = home mid / (home mid + away mid) at T-1h (removes the overround).",
    "Model: logistic regression for P(home win), one row per game, intercept = home flag. No scikit-learn or scipy in "
    "the venv, so a numpy Newton/IRLS fit with a tiny ridge (1e-6 on the slopes, none on the intercept) is used. "
    "Features are standardized with discovery mean / std only. Fitted on ALL discovery games with a clean result "
    "(quotes not required).",
    "Edge for a team = model prob - YES ask - fee(ask) (10 contracts, drift.fee). Bet the team with the larger edge "
    "if edge >= theta (1e-12 tolerance); at most one bet per game; hold to settlement; net P&L per contract = "
    "result - ask - fee.",
    "$/week = unseen mean net x 10 x unseen bets / unseen weeks; unseen weeks = ISO weeks with at least one unseen game "
    "(as #24's amendment).",
    "Rule 6 'within 0.002 while the betting rules still pass' = model Brier - market Brier <= 0.002 AND rules 1-5 pass.",
    "Coin flip: 500 copies keeping the same bet games, random side (uniform) per game, that side's own ask and fee, "
    "seed 20261021 (fresh generator per version). Share = fraction of copies with mean net >= the strategy's.",
    "Bootstrap: 10,000 resamples of the unseen bets (one per game), seed 20261021, percentile 2.5 / 97.5.",
    "'Key player missing' = a regular with rolling average >= 15 points is absent for either team. "
    "'Availability disagrees with the market' = |home - away missing points| >= 8 and the team with fewer missing points "
    "is the market underdog (normalized mid < 0.5).",
]


# ------------------------------------------------------------------ logistic regression (numpy)
def fit_logit(X, y, ridge=1e-6, iters=100):
    n, k = X.shape
    A = np.hstack([np.ones((n, 1)), X])
    w = np.zeros(k + 1)
    R = np.eye(k + 1) * ridge
    R[0, 0] = 0.0
    for _ in range(iters):
        z = A @ w
        p = 1 / (1 + np.exp(-z))
        g = A.T @ (y - p) - R @ w
        H = A.T @ (A * (p * (1 - p))[:, None]) + R
        step = np.linalg.solve(H, g)
        w = w + step
        if np.max(np.abs(step)) < 1e-10:
            break
    return w


def predict(w, X):
    return 1 / (1 + np.exp(-(w[0] + X @ w[1:])))


def brier(p, y):
    p, y = np.asarray(p), np.asarray(y)
    return float(np.mean((p - y) ** 2)) if len(p) else float("nan")


def logloss(p, y, eps=1e-6):
    p, y = np.clip(np.asarray(p), eps, 1 - eps), np.asarray(y)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))) if len(p) else float("nan")


def calib(p, y, nb=10):
    p, y = np.asarray(p), np.asarray(y)
    b = np.minimum((p * nb).astype(int), nb - 1)
    out = []
    for k in range(nb):
        m = b == k
        if m.sum():
            out.append({"bucket": f"{k / nb:.1f}-{(k + 1) / nb:.1f}", "n": int(m.sum()), "mean_p": float(p[m].mean()),
                        "win_rate": float(y[m].mean())})
    return out


# ------------------------------------------------------------------ quotes
def quotes_for(games):
    """{gid: (ask_h, ask_a, mid_h, mid_a)} for games with a valid quote on both teams at T-1h."""
    q, excl = {}, Counter()
    for g in games:
        e_t = g.tip - ENTRY_S
        vals = []
        for x in g.mk:
            ent = drift.at(x.h_ts, x.h_bid, x.h_ask, e_t)
            if ent is None:
                excl["no entry candle"] += 1
                break
            if not drift.valid_quote(ent[0], ent[1]):
                excl["no valid bid/ask at entry"] += 1
                break
            vals.append((ent[0], ent[1]))
        else:
            hi, ai = g.home_idx, 1 - g.home_idx
            q[g.gid] = (vals[hi][1], vals[ai][1], drift.mid(*vals[hi]), drift.mid(*vals[ai]))
    return q, dict(excl)


# ------------------------------------------------------------------ betting
def make_bets(rows, p_home, quotes, theta):
    """rows aligned with p_home. Returns list of dicts, one per bet."""
    bets = []
    for r, ph in zip(rows, p_home):
        q = quotes.get(r["gid"])
        if q is None:
            continue
        ah, aa = q[0], q[1]
        eh = ph - ah - drift.fee(ah)
        ea = (1 - ph) - aa - drift.fee(aa)
        side = 0 if eh >= ea else 1  # 0 = home
        e = eh if side == 0 else ea
        if e >= theta - 1e-12:
            ask = ah if side == 0 else aa
            res = r["home_win"] if side == 0 else 1.0 - r["home_win"]
            bets.append({"gid": r["gid"], "side": side, "edge": e, "ask": ask, "pnl": drift.settle_pnl(res, ask),
                         "pnl_both": (drift.settle_pnl(r["home_win"], ah), drift.settle_pnl(1.0 - r["home_win"], aa)),
                         "key_missing": r["key_missing"], "both_obs": r["both_observed"]})
    return bets


def summarize(bets_d, bets_u, unseen_weeks, rng_seed=SEED):
    d = np.array([b["pnl"] for b in bets_d])
    u = np.array([b["pnl"] for b in bets_u])
    lo, hi = drift.bootstrap_ci(u, 10000, seed=SEED) if len(u) else (float("nan"), float("nan"))
    v = {"n_disc": len(d), "n_unseen": len(u), "disc_mean": float(d.mean()) if len(d) else None,
         "unseen_mean": float(u.mean()) if len(u) else None, "unseen_ci_lo": lo, "unseen_ci_hi": hi,
         "unseen_usd_per_week": float(u.mean() * SIZE * len(u) / unseen_weeks) if len(u) else None,
         "unseen_total_usd": float(u.sum() * SIZE) if len(u) else 0.0}
    if len(u):
        both = np.array([b["pnl_both"] for b in bets_u])
        rng = np.random.default_rng(rng_seed)
        pr = rng.integers(0, 2, size=(500, len(both)))
        cf = both[np.arange(len(both))[None, :], pr].mean(axis=1)
        v["coinflip_share_ge"] = float((cf >= u.mean() - 1e-15).mean())
        v["coinflip_mean"] = float(cf.mean())
    return v


def fmt(x, d=4):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.{d}f}"


def verdict(v, mod_brier, mkt_brier):
    r = []
    r.append(("1 mean net per contract > 0 in discovery and unseen", (v["disc_mean"] or -1) > 0 and (v["unseen_mean"] or -1) > 0,
              f"discovery {fmt(v['disc_mean'])}, unseen {fmt(v['unseen_mean'])}"))
    r.append(("2 unseen 95% bootstrap lower bound > 0", bool(v["unseen_ci_lo"] > 0), f"CI [{fmt(v['unseen_ci_lo'])}, {fmt(v['unseen_ci_hi'])}]"))
    r.append(("3 at least 100 unseen bets", v["n_unseen"] >= 100, f"n_unseen {v['n_unseen']}"))
    cf = v.get("coinflip_share_ge", 1.0)
    r.append(("4 coin flip: at most 5% of 500 copies do as well", cf <= 0.05, f"share {cf:.3f}"))
    r.append(("5 at least $2/week at 10 contracts (unseen)", (v["unseen_usd_per_week"] or -1) >= 2, f"${(v['unseen_usd_per_week'] or 0):.2f}/wk"))
    first5 = all(x[1] for x in r)
    diff = mod_brier - mkt_brier
    ok6 = diff <= 0 or (diff <= 0.002 and first5)
    r.append(("6 model unseen Brier <= market Brier (or within 0.002 while rules 1-5 pass)", bool(ok6),
              f"model {mod_brier:.4f} vs market {mkt_brier:.4f}, diff {diff:+.4f}"))
    return r


def main():
    pts, spread, expv = F.load_tables()
    games, excl, flags = drift.load_nba()
    games = sorted(games, key=lambda g: (g.tip, g.gid))
    n = len(games)
    k = drift.split_index(n)
    disc_ids = {g.gid for g in games[:k]}
    rows, fstats = F.build_features(games, pts, spread, expv)
    assert [r["gid"] for r in rows] == [g.gid for g in games]
    quotes, qex = quotes_for(games)
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()  # noqa: E731
    unseen_games = games[k:]
    unseen_weeks = len({datetime.fromtimestamp(g.tip, timezone.utc).isocalendar()[:2] for g in unseen_games})
    is_d = np.array([r["gid"] in disc_ids for r in rows])
    y = np.array([r["home_win"] for r in rows])
    res = {"n_games": n, "discovery_games": k, "unseen_games": n - k, "split_tip_utc": iso(games[k].tip),
           "first_tip_utc": iso(games[0].tip), "last_tip_utc": iso(games[-1].tip), "unseen_weeks": unseen_weeks,
           "loader_exclusions": dict(excl), "feature_stats": fstats, "quote_exclusions": qex,
           "games_with_valid_quotes": len(quotes), "versions": {}, "models": {}}
    res["listing_coverage"] = {"both_teams_observed": int(sum(r["both_observed"] for r in rows)),
                               "one_team": int(sum(1 for r in rows if r["obs_h"] + r["obs_a"] == 1)),
                               "none": int(sum(1 for r in rows if r["obs_h"] + r["obs_a"] == 0))}
    mkt_p = {gid: q[2] / (q[2] + q[3]) for gid, q in quotes.items()}
    preds = {}
    for name, cols in (("full", FULL_COLS), ("elo_only", ELO_COLS)):
        X = np.array([[r[c] for c in cols] for r in rows], dtype=float)
        mu, sd = X[is_d].mean(axis=0), X[is_d].std(axis=0)
        sd[sd == 0] = 1.0
        Z = (X - mu) / sd
        w = fit_logit(Z[is_d], y[is_d])
        ph = predict(w, Z)
        preds[name] = ph
        res["models"][name] = {"cols": cols, "intercept_std": float(w[0]),
                               "weights_standardized": dict(zip(cols, map(float, w[1:]))),
                               "weights_per_raw_unit": dict(zip(cols, map(float, w[1:] / sd))),
                               "feature_mean_disc": dict(zip(cols, map(float, mu))),
                               "feature_sd_disc": dict(zip(cols, map(float, sd)))}
    # ---- probability quality (same games: those with valid quotes)
    qual = {}
    for part, mask in (("discovery", is_d), ("unseen", ~is_d)):
        idx = [i for i, r in enumerate(rows) if mask[i] and r["gid"] in quotes]
        yy = y[idx]
        pm = np.array([mkt_p[rows[i]["gid"]] for i in idx])
        d = {"n_games": len(idx), "market": {"brier": brier(pm, yy), "logloss": logloss(pm, yy)}}
        for name in preds:
            pp = preds[name][idx]
            d[name] = {"brier": brier(pp, yy), "logloss": logloss(pp, yy)}
            avg = (pp + pm) / 2
            d[name + "+market avg"] = {"brier": brier(avg, yy), "logloss": logloss(avg, yy)}
        d["calibration_full"] = calib(preds["full"][idx], yy)
        d["calibration_market"] = calib(pm, yy)
        # all games of the part (no quote needed) for the model alone
        ia = [i for i in range(len(rows)) if mask[i]]
        d["all_games_model_brier"] = {nm: brier(preds[nm][ia], y[ia]) for nm in preds}
        d["home_win_rate"] = float(y[ia].mean())
        qual[part] = d
    res["quality"] = qual
    # ---- bets
    rows_d = [r for r in rows if r["gid"] in disc_ids]
    rows_u = [r for r in rows if r["gid"] not in disc_ids]
    allbets = {}
    for name in ("full", "elo_only"):
        pd_, pu_ = preds[name][is_d], preds[name][~is_d]
        for th in THETAS:
            bd = make_bets(rows_d, pd_, quotes, th)
            bu = make_bets(rows_u, pu_, quotes, th)
            allbets[(name, th)] = (bd, bu)
            res["versions"][f"{name} theta={th * 100:.0f}c"] = summarize(bd, bu, unseen_weeks)
    # ---- primary
    pv = res["versions"]["full theta=4c"]
    rules = verdict(pv, qual["unseen"]["full"]["brier"], qual["unseen"]["market"]["brier"])
    res["primary"] = {"pass": all(x[1] for x in rules), "rules": [(a, bool(b), c) for a, b, c in rules]}
    # ---- breakdowns on the full model
    bd2, bu2 = allbets[("full", 0.02)]
    edges = [(0.02, 0.04), (0.04, 0.06), (0.06, 0.10), (0.10, 9.0)]
    by_edge = []
    for lo, hi in edges:
        d_ = [b["pnl"] for b in bd2 if lo - 1e-12 <= b["edge"] < hi - 1e-12]
        u_ = [b["pnl"] for b in bu2 if lo - 1e-12 <= b["edge"] < hi - 1e-12]
        by_edge.append({"edge_range": f"{lo:.2f}-{hi:.2f}", "n_disc": len(d_), "mean_disc": float(np.mean(d_)) if d_ else None,
                        "n_unseen": len(u_), "mean_unseen": float(np.mean(u_)) if u_ else None})
    res["bets_by_edge_full_theta2c"] = by_edge
    bd4, bu4 = allbets[("full", 0.04)]
    br = {}
    for lab, fn in (("key player missing", lambda b: b["key_missing"] == 1.0), ("no key player missing", lambda b: b["key_missing"] == 0.0),
                    ("both teams' listings observed", lambda b: b["both_obs"] == 1.0)):
        d_ = [b["pnl"] for b in bd4 if fn(b)]
        u_ = [b["pnl"] for b in bu4 if fn(b)]
        br[lab] = {"n_disc": len(d_), "mean_disc": float(np.mean(d_)) if d_ else None, "n_unseen": len(u_),
                   "mean_unseen": float(np.mean(u_)) if u_ else None}
    res["bets_by_key_missing_full_theta4c"] = br
    # sensitivity: primary bets restricted to games observed for both teams
    sd_ = [b for b in bd4 if b["both_obs"] == 1.0]
    su_ = [b for b in bu4 if b["both_obs"] == 1.0]
    res["sensitivity_primary_both_observed"] = summarize(sd_, su_, unseen_weeks)
    # key-missing games: model vs market quality (unseen + discovery)
    km = {}
    for part, mask in (("discovery", is_d), ("unseen", ~is_d)):
        for lab, val in (("key_missing", 1.0), ("not_key_missing", 0.0)):
            idx = [i for i, r in enumerate(rows) if mask[i] and r["gid"] in quotes and r["key_missing"] == val]
            if idx:
                pm = np.array([mkt_p[rows[i]["gid"]] for i in idx])
                km[f"{part} {lab}"] = {"n": len(idx), "model_brier": brier(preds["full"][idx], y[idx]), "market_brier": brier(pm, y[idx])}
    res["quality_by_key_missing"] = km
    # availability vs market disagreement
    dis, tot, fav_win_avail, fav_win_mkt = 0, 0, [], []
    for i, r in enumerate(rows):
        if r["gid"] not in quotes or abs(r["miss_diff"]) < 8:
            continue
        tot += 1
        avail_home = r["miss_diff"] < 0  # home has fewer missing points
        mkt_home = mkt_p[r["gid"]] > 0.5
        if avail_home != mkt_home:
            dis += 1
            fav_win_avail.append((r["home_win"] if avail_home else 1 - r["home_win"]))
            fav_win_mkt.append((r["home_win"] if mkt_home else 1 - r["home_win"]))
    res["availability_vs_market"] = {"games_with_abs_missing_diff_ge_8": tot, "disagree": dis,
                                     "disagree_share": dis / tot if tot else None,
                                     "when_disagree_availability_side_win_rate": float(np.mean(fav_win_avail)) if fav_win_avail else None,
                                     "when_disagree_market_side_win_rate": float(np.mean(fav_win_mkt)) if fav_win_mkt else None}
    res["feature_summary"] = {c: {"mean": float(np.mean([r[c] for r in rows])), "sd": float(np.std([r[c] for r in rows]))}
                              for c in FULL_COLS + ["key_missing", "both_observed"]}
    (HERE / "results.json").write_text(json.dumps(res, indent=1, default=str))
    write_readme(res)
    print(json.dumps({"primary": res["primary"], "quality": {p: {k_: v_ for k_, v_ in qual[p].items() if k_ in ("n_games", "market", "full", "elo_only")} for p in qual}}, indent=1, default=str))
    print(table(res))


def table(res):
    L = ["| version | disc bets | unseen bets | disc mean net | unseen mean net | unseen 95% CI | coin-flip share | $/wk |",
         "|---|---|---|---|---|---|---|---|"]
    for nm, v in res["versions"].items():
        cf = f"{v['coinflip_share_ge']:.3f}" if v.get("coinflip_share_ge") is not None else "n/a"
        ci = f"[{fmt(v['unseen_ci_lo'])}, {fmt(v['unseen_ci_hi'])}]" if v["n_unseen"] else "n/a"
        L.append(f"| {nm} | {v['n_disc']} | {v['n_unseen']} | {fmt(v['disc_mean'])} | {fmt(v['unseen_mean'])} | {ci} | {cf} | "
                 f"{fmt(v['unseen_usd_per_week'], 2)} |")
    return "\n".join(L)


def write_readme(res):
    p = res["primary"]
    q = res["quality"]
    L = ["# Research log #29 results: an NBA game-winner model (strength, rest, home, key-player availability)\n",
         f"**Primary verdict (full model, theta = 4c): {'PASS' if p['pass'] else 'FAIL'}**\n"]
    L += [f"- {'PASS' if b else 'FAIL'}  rule {a}: {c}" for a, b, c in p["rules"]]
    L.append(f"\nGames {res['n_games']} (discovery {res['discovery_games']}, unseen {res['unseen_games']}), "
             f"{res['first_tip_utc']} to {res['last_tip_utc']}, split at {res['split_tip_utc']} (same split as #24). "
             f"Games with a usable T-1h quote on both teams: {res['games_with_valid_quotes']}. "
             f"Unseen weeks with games: {res['unseen_weeks']}.\n")
    L.append("In plain English: dollars are for 10 contracts per bet. A bet is made only when the model's chance for a team, "
             "minus the ask and the fee, is at least theta. The pass bar needs every rule to hold on games the model never saw.\n")
    L.append("## All 6 versions\n")
    L.append(table(res))
    L.append("\nMean net is dollars per contract after the exact fee, held to settlement. $/wk = 10 contracts.\n")
    L.append("## Model against the market (Brier and log loss; lower is better; same games = valid T-1h quotes)\n")
    L.append("| set | n | market Brier | full Brier | Elo-only Brier | full+market avg Brier | market log loss | full log loss | Elo-only log loss | avg log loss |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for part in ("discovery", "unseen"):
        d = q[part]
        L.append(f"| {part} | {d['n_games']} | {d['market']['brier']:.4f} | {d['full']['brier']:.4f} | {d['elo_only']['brier']:.4f} | "
                 f"{d['full+market avg']['brier']:.4f} | {d['market']['logloss']:.4f} | {d['full']['logloss']:.4f} | "
                 f"{d['elo_only']['logloss']:.4f} | {d['full+market avg']['logloss']:.4f} |")
    L.append("\nDiscovery numbers for the model are in-sample (it was fitted there); the market has no fitting.\n")
    L.append("## Feature weights (logistic regression on discovery)\n")
    for nm, m in res["models"].items():
        L.append(f"**{nm}**: intercept (home flag, standardized scale) {m['intercept_std']:+.3f}")
        L.append("| feature | weight per 1 sd | weight per raw unit | discovery sd |\n|---|---|---|---|")
        for c in m["cols"]:
            L.append(f"| {c} | {m['weights_standardized'][c]:+.3f} | {m['weights_per_raw_unit'][c]:+.4f} | {m['feature_sd_disc'][c]:.2f} |")
        L.append("")
    L.append("## Calibration (unseen; 10 buckets)\n")
    L.append("| bucket | full n | full mean p | full win rate | market n | market mean p | market win rate |\n|---|---|---|---|---|---|---|")
    mk = {b["bucket"]: b for b in q["unseen"]["calibration_market"]}
    for b in q["unseen"]["calibration_full"]:
        m = mk.get(b["bucket"])
        L.append(f"| {b['bucket']} | {b['n']} | {b['mean_p']:.3f} | {b['win_rate']:.3f} | "
                 + (f"{m['n']} | {m['mean_p']:.3f} | {m['win_rate']:.3f} |" if m else "0 | | |"))
    L.append("\n## Bets by edge size (full model, theta 2c bets, held to settlement)\n")
    L.append("| edge | disc n | disc mean | unseen n | unseen mean |\n|---|---|---|---|---|")
    for b in res["bets_by_edge_full_theta2c"]:
        L.append(f"| {b['edge_range']} | {b['n_disc']} | {fmt(b['mean_disc'])} | {b['n_unseen']} | {fmt(b['mean_unseen'])} |")
    L.append("\n## Bets by key-player-missing (full model, theta 4c)\n")
    L.append("| group | disc n | disc mean | unseen n | unseen mean |\n|---|---|---|---|---|")
    for lab, b in res["bets_by_key_missing_full_theta4c"].items():
        L.append(f"| {lab} | {b['n_disc']} | {fmt(b['mean_disc'])} | {b['n_unseen']} | {fmt(b['mean_unseen'])} |")
    L.append("\nModel vs market Brier by key-player-missing games:\n")
    for kk, b in res["quality_by_key_missing"].items():
        L.append(f"- {kk}: n {b['n']}, model {b['model_brier']:.4f}, market {b['market_brier']:.4f}")
    a = res["availability_vs_market"]
    L.append(f"\n## Availability vs the market\n\nGames with a missing-points gap of at least 8: {a['games_with_abs_missing_diff_ge_8']}; "
             f"availability points to the market underdog in {a['disagree']} ({fmt(a['disagree_share'], 3) if a['disagree_share'] is not None else 'n/a'}). "
             f"When they disagree the availability side won {fmt(a['when_disagree_availability_side_win_rate'], 3)} and the market side won "
             f"{fmt(a['when_disagree_market_side_win_rate'], 3)} (shares).\n")
    s = res["sensitivity_primary_both_observed"]
    L.append(f"Sensitivity, primary bets only in games where both teams' PTS listings exist: disc n {s['n_disc']}, mean {fmt(s['disc_mean'])}; "
             f"unseen n {s['n_unseen']}, mean {fmt(s['unseen_mean'])}.\n")
    L.append(f"Listing coverage: {res['listing_coverage']}. Margin sources: {res['feature_stats']}. Quote exclusions: {res['quote_exclusions']}. "
             f"Loader exclusions: {res['loader_exclusions']}.\n")
    L.append("## Interpretation choices\n")
    L += [f"- {s_}" for s_ in F.INTERPRETATION + INTERP]
    (HERE / "README.md").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
