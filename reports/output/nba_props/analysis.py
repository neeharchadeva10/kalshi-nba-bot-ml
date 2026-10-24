"""Research log #36 analysis helpers: /model-test gates and the betting rule (PREREGISTRATION.md).  Pure functions on arrays/lists;
unit-tested on synthetic data.  Nothing here reads the data files.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

import nbmodel as NB

SEED = 20261021
SIZE = 10
MAX_PX = 0.99           # Kalshi's top price; a fill never exceeds it (caps ask+1c when ask >= 0.99)
SLIP = 0.01             # resume amendment 2026-10-22: every betting version pays +1c on top of the taker price
P_CLIP = 1e-3            # model / mid probabilities are clipped to [1e-3, 1-1e-3] inside logit() only


# ------------------------------------------------------------------ scores
def brier(p, y):
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def logloss(p, y, eps=1e-6):
    p, y = np.clip(np.asarray(p, float), eps, 1 - eps), np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def logit(p):
    p = np.clip(np.asarray(p, float), P_CLIP, 1 - P_CLIP)
    return np.log(p / (1 - p))


def calib_table(p, y, edges=(0, .05, .15, .30, .50, .70, .85, .95, 1.0001)):
    p, y = np.asarray(p, float), np.asarray(y, float)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi)
        out.append({"lo": lo, "hi": min(hi, 1.0), "n": int(m.sum()), "mean_p": float(p[m].mean()) if m.any() else None,
                    "freq": float(y[m].mean()) if m.any() else None})
    return out


# ------------------------------------------------------------------ residual test
def logit_fit(X, y, w=None, iters=50, ridge=1e-8):
    """Weighted logistic regression by IRLS.  X includes the intercept column."""
    n, k = X.shape
    w = np.ones(n) if w is None else w
    b = np.zeros(k)
    for _ in range(iters):
        eta = np.clip(X @ b, -30, 30)
        mu = 1 / (1 + np.exp(-eta))
        s = w * mu * (1 - mu)
        A = X.T @ (X * s[:, None]) + ridge * np.eye(k)
        g = X.T @ (w * (y - mu))
        step = np.linalg.solve(A, g)
        b = b + step
        if np.max(np.abs(step)) < 1e-9:
            break
    return b


def residual_design(p_model, p_mid):
    lm, lk = logit(p_model), logit(p_mid)
    return np.column_stack([np.ones(len(lm)), lk, lm - lk])


def residual_test(p_model, p_mid, y, games, draws=1000, seed=SEED):
    """outcome ~ 1 + logit(mid) + (logit(model) - logit(mid)).  Returns coefficients, disagreement coefficient and its 95% CI from a
    bootstrap over games (percentile 2.5 / 97.5)."""
    X = residual_design(p_model, p_mid)
    y = np.asarray(y, float)
    b = logit_fit(X, y)
    gid = np.unique(np.asarray(games), return_inverse=True)[1]
    ng = gid.max() + 1
    rng = np.random.default_rng(seed)
    coefs = np.empty(draws)
    for d in range(draws):
        cnt = np.bincount(rng.integers(0, ng, size=ng), minlength=ng)
        coefs[d] = logit_fit(X, y, w=cnt[gid].astype(float))[2]
    return {"coef": b.tolist(), "disagreement": float(b[2]), "ci_lo": float(np.percentile(coefs, 2.5)),
            "ci_hi": float(np.percentile(coefs, 97.5)), "draws": draws}


# ------------------------------------------------------------------ gate 3
def cost_of_side(p, bid, ask, count=SIZE):
    """Round-trip cost for the side the model prefers: half spread + per-contract fee at the price paid (YES: ask, NO: 1-bid)."""
    mid = (bid + ask) / 2
    half = (ask - bid) / 2
    price = ask if p > mid else 1 - bid
    return half + NB.fee_per_contract(price, count)


def iso_week(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isocalendar()[:2]


def gate3(p, bid, ask, tips, game_tips_all):
    """Markets with |model - mid| > half spread + fee, per week.  weeks = ISO weeks (UTC tip) with at least one game in the period
    (game_tips_all = tips of ALL games of the period)."""
    p, bid, ask = (np.asarray(a, float) for a in (p, bid, ask))
    mid = (bid + ask) / 2
    cost = np.array([cost_of_side(pi, b, a) for pi, b, a in zip(p, bid, ask)])
    hit = np.abs(p - mid) > cost
    weeks = sorted({iso_week(t) for t in game_tips_all})
    per = defaultdict(int)
    for h, t in zip(hit, tips):
        if h:
            per[iso_week(t)] += 1
    return {"events": int(hit.sum()), "weeks": len(weeks), "per_week": float(hit.sum() / max(len(weeks), 1)),
            "min_week": int(min((per.get(w, 0) for w in weeks), default=0)),
            "mean_gap_when_hit": float(np.abs(p - mid)[hit].mean()) if hit.any() else None}


# ------------------------------------------------------------------ betting rule
def decide(p, bid, ask, theta, lo=0.15, hi=0.85, price_filter=True, count=SIZE):
    """-> None or dict(side, price, fee, edge).  With ask' = ask + 1c and (1-bid)' = 1 - bid + 1c (slippage):
    YES if p - ask' - fee(ask') >= theta; NO if (1-p) - (1-bid)' - fee((1-bid)') >= theta;
    one side per market (the larger edge if both qualify); price filter on the price paid, lo <= price <= hi (inclusive)."""
    py, pn = min(ask + SLIP, MAX_PX), min((1 - bid) + SLIP, MAX_PX)        # price paid = taker quote + 1c slippage; the fee is charged on the price paid
    fy, fn = NB.fee_per_contract(py, count), NB.fee_per_contract(pn, count)
    ey = p - py - fy
    en = (1 - p) - pn - fn
    cands = []
    if ey >= theta - 1e-12:
        cands.append(("yes", py, fy, ey))
    if en >= theta - 1e-12:
        cands.append(("no", pn, fn, en))
    if not cands:
        return None
    side, price, fee, edge = max(cands, key=lambda c: c[3])
    if price_filter and not (lo - 1e-12 <= price <= hi + 1e-12):
        return None
    return {"side": side, "price": price, "fee": fee, "edge": edge}


def net_per_contract(side, price, fee, result):
    won = (result == "yes") if side == "yes" else (result == "no")
    return (1.0 if won else 0.0) - price - fee


def bootstrap_mean(pnl, games, draws=10000, seed=SEED):
    """Resample whole games (cluster bootstrap) -> (2.5th pct, 97.5th pct) of the mean net per contract."""
    pnl = np.asarray(pnl, float)
    if len(pnl) == 0:
        return float("nan"), float("nan")
    gid = np.unique(np.asarray(games), return_inverse=True)[1]
    ng = gid.max() + 1
    s = np.bincount(gid, weights=pnl, minlength=ng)
    c = np.bincount(gid, minlength=ng).astype(float)
    rng = np.random.default_rng(seed)
    means = np.empty(draws)
    for i in range(0, draws, 500):
        j = min(500, draws - i)
        idx = rng.integers(0, ng, size=(j, ng))
        den = c[idx].sum(axis=1)
        means[i:i + j] = np.where(den > 0, s[idx].sum(axis=1) / np.maximum(den, 1), np.nan)
    return float(np.nanpercentile(means, 2.5)), float(np.nanpercentile(means, 97.5))


def coin_flip(bid, ask, result, strat_mean, lo=0.15, hi=0.85, price_filter=True, copies=500, seed=SEED, count=SIZE):
    """Each copy: the strategy's own markets, a uniformly random side per market, same price filter (a copy bet whose price paid
    falls outside the range is not placed) and same fees.  Returns the share of copies whose mean net per contract >= strat_mean."""
    bid, ask = np.asarray(bid, float), np.asarray(ask, float)
    yes = np.asarray([r == "yes" for r in result])
    rng = np.random.default_rng(seed)
    ay, an = np.minimum(ask + SLIP, MAX_PX), np.minimum((1 - bid) + SLIP, MAX_PX)                    # price paid incl. 1c slippage
    fy = np.array([NB.fee_per_contract(float(a), count) for a in ay])
    fn = np.array([NB.fee_per_contract(float(a), count) for a in an])
    pnl_y = yes.astype(float) - ay - fy
    pnl_n = (~yes).astype(float) - an - fn
    ok_y = (ay >= lo - 1e-12) & (ay <= hi + 1e-12) if price_filter else np.ones(len(ask), bool)
    ok_n = (an >= lo - 1e-12) & (an <= hi + 1e-12) if price_filter else np.ones(len(ask), bool)
    means = []
    for _ in range(copies):
        side_y = rng.random(len(ask)) < 0.5
        pnl = np.where(side_y, pnl_y, pnl_n)
        ok = np.where(side_y, ok_y, ok_n)
        means.append(pnl[ok].mean() if ok.any() else -np.inf)
    means = np.array(means)
    return {"share": float(np.mean(means >= strat_mean - 1e-12)), "copy_mean": float(np.mean(means[np.isfinite(means)]))}


def summarize_bets(bets, tips_period):
    """bets: list of dicts with pnl, game.  tips_period: tips of all games in the period (for ISO weeks)."""
    n = len(bets)
    weeks = len({iso_week(t) for t in tips_period})
    if n == 0:
        return {"n": 0, "mean": None, "weeks": weeks, "usd_per_week": 0.0}
    u = np.array([b["pnl"] for b in bets])
    return {"n": n, "mean": float(u.mean()), "total": float(u.sum()), "win_rate": float(np.mean([b["won"] for b in bets])),
            "weeks": weeks, "usd_per_week": float(u.mean() * SIZE * n / max(weeks, 1)),
            "worst_streak": worst_streak([b["won"] for b in bets])}


def worst_streak(won_list):
    best = cur = 0
    for w in won_list:
        cur = 0 if w else cur + 1
        best = max(best, cur)
    return best
