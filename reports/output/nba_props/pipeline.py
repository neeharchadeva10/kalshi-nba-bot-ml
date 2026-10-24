"""Research log #36 pipeline LIBRARY (no entry point).  Entry points: run_discovery.py, run_unseen.py (the old smoke/gates/bets stages
of run.py were removed: smoke printed model p next to market mids).  Prices come from the shared raw caches via data.load_markets.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analysis as A  # noqa: E402
import build as B  # noqa: E402
import data as D  # noqa: E402
import features as F  # noqa: E402
import nbmodel as NB  # noqa: E402

THETAS = (0.03, 0.06)


def load_all(period="all", progress=False):
    """period "disc": markets (and so their Kalshi results / box outcomes) of the UNSEEN games are dropped before anything is built, so
    run_discovery.py never touches an unseen-period outcome.  The split itself uses all games' scheduled starts (no outcomes)."""
    box = D.Box()
    store = D.ReportStore()
    markets = D.load_markets()
    games_tip = {(m["game_date"], m["game_code"]): m["tip"] for m in markets if m["tip"] is not None}
    disc, uns = D.split_games({(t, k) for k, t in games_tip.items()})
    if period == "disc":
        markets = [m for m in markets if (m["game_date"], m["game_code"]) in disc]
    rows, mrows, counts, games, unm = B.build(markets, box, store, None, progress=progress)
    store.save()
    selected = D.read_selected_tickers()
    sel_missing = [m for m in markets if m["ticker"] in selected and not m["downloaded"]]
    return {"selected_missing": sel_missing, "box": box, "markets": markets, "games_tip": games_tip, "disc": disc, "uns": uns, "rows": rows, "mrows": mrows,
            "counts": counts, "games": games, "unmatched": unm}


def fit_models(rows, disc):
    """One NB per stat, fitted on discovery games only; one dispersion per stat.  Rows with an actual count only."""
    models = {}
    for stat in D.STATS:
        idx = [i for i, r in enumerate(rows) if r["stat"] == stat and r["gk"] in disc and r["y"] is not None]
        X = np.array([rows[i]["vec"] for i in idx])
        y = np.array([rows[i]["y"] for i in idx], dtype=float)
        m = NB.fit_nb(X, y)
        m["n_fit"] = len(idx)
        models[stat] = m
    return models


def attach_probs(rows, mrows, models):
    """model mean / P(stat >= strike) for every included market record (excl None and a row)."""
    by_stat = defaultdict(list)
    for r in mrows:
        if r["excl"] is None and r["row"] is not None:
            by_stat[r["stat"]].append(r)
    for stat, rs in by_stat.items():
        X = np.array([rows[r["row"]]["vec"] for r in rs])
        n = np.array([r["strike"] for r in rs])
        mu = NB.predict_mu(models[stat], X)
        p = NB.nb_sf(mu, models[stat]["alpha"], n)
        for r, m_, p_ in zip(rs, mu, p):
            r["mu"], r["p"] = float(m_), float(p_)
            r["tm_out"] = rows[r["row"]]["info"]["tm_out"]


def usable(mrows):
    """market records with a model probability and a valid T-1h quote."""
    return [r for r in mrows if r["excl"] is None and r["row"] is not None and r["quote_excl"] is None and "p" in r]


# ------------------------------------------------------------------ gates
def gate_run(recs, games_tip_period, price_filter):
    """recs: usable discovery records.  price_filter: mid in [0.15, 0.85] if True."""
    sel = [r for r in recs if (not price_filter) or (0.15 <= (r["bid"] + r["ask"]) / 2 <= 0.85)]
    p = np.array([r["p"] for r in sel])
    bid = np.array([r["bid"] for r in sel])
    ask = np.array([r["ask"] for r in sel])
    mid = (bid + ask) / 2
    y = np.array([1.0 if r["result"] == "yes" else 0.0 for r in sel])
    games = [r["gk"] for r in sel]
    tips = [r["tip"] for r in sel]
    out = {"n_markets": len(sel), "price_filter": "mid 15-85c" if price_filter else "all prices"}
    out["brier_model"], out["brier_mid"] = A.brier(p, y), A.brier(mid, y)
    out["logloss_model"], out["logloss_mid"] = A.logloss(p, y), A.logloss(mid, y)
    blend = 1 / (1 + np.exp(-(A.logit(p) + A.logit(mid)) / 2))
    out["brier_blend"], out["logloss_blend"] = A.brier(blend, y), A.logloss(blend, y)
    out["gate1_pass"] = bool(out["brier_model"] <= out["brier_mid"] + 0.002)
    out["residual"] = A.residual_test(p, mid, y, games)
    out["gate2_pass"] = bool(out["residual"]["disagreement"] > 0 and out["residual"]["ci_lo"] > 0)
    out["gate3"] = A.gate3(p, bid, ask, tips, list(games_tip_period))
    out["gate3_pass"] = bool(out["gate3"]["per_week"] >= 20)
    out["all_pass"] = bool(out["gate1_pass"] and out["gate2_pass"] and out["gate3_pass"])
    out["calibration_model"] = A.calib_table(p, y)
    out["calibration_mid"] = A.calib_table(mid, y)
    return out


# ------------------------------------------------------------------ bets
def make_bets(recs, theta, price_filter):
    bets = []
    for r in recs:
        d = A.decide(r["p"], r["bid"], r["ask"], theta, price_filter=price_filter)
        if d is None:
            continue
        pnl = A.net_per_contract(d["side"], d["price"], d["fee"], r["result"])
        bets.append({**d, "ticker": r["ticker"], "gk": r["gk"], "tip": r["tip"], "stat": r["stat"], "strike": r["strike"],
                     "p": r["p"], "bid": r["bid"], "ask": r["ask"], "mu": r["mu"], "result": r["result"], "pnl": pnl,
                     "won": pnl + d["price"] + d["fee"] > 0.5, "tm_out": r["tm_out"]})
    bets.sort(key=lambda b: (b["tip"], b["ticker"]))
    return bets


def period_report(recs, tips, theta, price_filter, with_tests):
    """One betting version on one period.  with_tests (unseen only): cluster bootstrap by game (10,000 draws, seed 20261021) and the
    500-copy coin flip (same markets, random side, same price filter / slippage / fees, seed 20261021)."""
    bets = make_bets(recs, theta, price_filter)
    out = A.summarize_bets(bets, tips)
    if with_tests and bets:
        lo, hi = A.bootstrap_mean([b["pnl"] for b in bets], [b["gk"] for b in bets], draws=10000, seed=A.SEED)
        out.update({"boot_lo": lo, "boot_hi": hi})
        out["coin_flip"] = A.coin_flip([b["bid"] for b in bets], [b["ask"] for b in bets], [b["result"] for b in bets], out["mean"],
                                       price_filter=price_filter, copies=500, seed=A.SEED)
    return out, bets


def version_name(theta, price_filter):
    return f"theta{int(round(theta * 100))}c_{'15-85c' if price_filter else 'all'}"


def spread_summary(recs):
    sp = np.array([r["ask"] - r["bid"] for r in recs]) if recs else np.array([])
    if not len(sp):
        return {"n": 0}
    return {"n": int(len(sp)), "mean": float(sp.mean()), "median": float(np.median(sp)),
            "share_le_2c": float(np.mean(sp <= 0.02 + 1e-9)), "share_gt_10c": float(np.mean(sp > 0.10 + 1e-9))}


def reported_regardless(S, recs, bets_by_version):
    """The "Reported regardless" block of the pre-registration: by stat / strike distance / price bucket / teammates-out (per betting
    version, if any bets), calibration of model vs market, spread at T-1h, match and coverage counts, exclusions."""
    cal = {}
    for name, pf in (("15-85c", True), ("all", False)):
        sel = [r for r in recs if (not pf) or (0.15 <= (r["bid"] + r["ask"]) / 2 <= 0.85)]
        if sel:
            y = np.array([1.0 if r["result"] == "yes" else 0.0 for r in sel])
            cal[name] = {"n": len(sel), "model": A.calib_table([r["p"] for r in sel], y),
                         "mid": A.calib_table([(r["bid"] + r["ask"]) / 2 for r in sel], y)}
    c = S["counts"]
    by_stat_nd = Counter(m["stat"] for m in S["mrows"] if m.get("quote_excl") == "not_downloaded")
    return {"breakdowns": {v: breakdowns(b) for v, b in bets_by_version.items() if b},
            "calibration": cal, "spread_T_minus_1h": spread_summary(recs),
            "coverage": {"counts": dict(c), "not_downloaded_by_stat": dict(by_stat_nd),
                         "unmatched_player_keys": len(S["unmatched"]), "n_usable_markets_in_period": len(recs),
                         "games_with_injury_report": sum(1 for g in S["games"].values() if g["has_report"]),
                         "games_report_covers_game": sum(1 for g in S["games"].values() if g["covered"]),
                         "games_total": len(S["games"])},
            "exclusions": {k: v for k, v in c.items() if k.startswith("excl_")}}


def breakdowns(bets):
    def agg(key):
        d = defaultdict(list)
        for b in bets:
            d[key(b)].append(b["pnl"])
        return {str(k): {"n": len(v), "mean": float(np.mean(v))} for k, v in sorted(d.items(), key=lambda kv: str(kv[0]))}
    dist = lambda b: ("<=-2" if b["strike"] - b["mu"] <= -2 else "-2..0" if b["strike"] - b["mu"] <= 0 else  # noqa: E731
                      "0..2" if b["strike"] - b["mu"] <= 2 else "2..5" if b["strike"] - b["mu"] <= 5 else ">5")
    price = lambda b: ("<15c" if b["price"] < 0.15 else "15-30c" if b["price"] < 0.30 else "30-50c" if b["price"] < 0.50  # noqa: E731
                       else "50-70c" if b["price"] < 0.70 else "70-85c" if b["price"] <= 0.85 else ">85c")
    return {"by_stat": agg(lambda b: b["stat"]), "by_strike_distance": agg(dist), "by_price": agg(price),
            "by_teammates_out": agg(lambda b: "tm_out>0" if b["tm_out"] > 0 else "tm_out=0"), "by_side": agg(lambda b: b["side"])}




def pass_bar(disc, unseen, brier_model, brier_mid):
    """The six pass-bar conditions of the pre-registration (primary version theta 3c, 15-85c).  disc / unseen: period_report dicts."""
    u = unseen
    return {
        "1_mean_pos_both": bool(disc.get("mean") is not None and u.get("mean") is not None and disc["mean"] > 0 and u["mean"] > 0),
        "2_boot_lo_pos": bool(u.get("boot_lo") is not None and u["boot_lo"] > 0),
        "3_n_unseen_ge_200": bool(u["n"] >= 200),
        "4_coin_flip_le_5pct": bool(u.get("coin_flip", {}).get("share", 1.0) <= 0.05),
        "5_usd_week_ge_2": bool(u["usd_per_week"] >= 2.0),
        "6_unseen_brier_ok": bool(brier_model <= brier_mid + 0.002),
    }
