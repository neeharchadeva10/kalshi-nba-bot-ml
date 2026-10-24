"""Research log #42: the bet rule, per-strategy statistics, winner selection, luck check, coin flip, pass bar.
Pure functions; unit-tested in test_grid.py.

Bet (PREREGISTRATION.md): per game take the KXNBATOTAL strike whose mid is nearest 0.50 (mid in 15-85c, ties to the
lower strike); side = OVER if proj > strike, UNDER if proj < strike (proj == strike: no side, no bet); bet only if
|proj - strike| >= T; buy 1 contract as a taker at the side's ask (YES ask, or NO ask = 1 - YES bid) + 0.01 slippage
+ Kalshi's taker fee at count 1 computed AT THE ASK (micro_ceil); hold to settlement (Kalshi yes/no decides).
net = 1(win) - ask - 0.01 - fee."""
from __future__ import annotations

import numpy as np
import pandas as pd

import grid_lib as gl
from grid_lib import lib

SLIP = 0.01
THRESHOLDS = (0, 2, 4, 6, 8)
MIN_N = 150
SEED = lib.SEED                    # 20261021
Z95 = 1.96
LB_TIE = 1e-9                      # LBs within this of the max are tied (duplicates differ by float noise)


# ------------------------------------------------------------------ the strike, prices, net per contract
def fee1(price):
    """Taker fee for 1 contract at `price`, rounded up to $0.000001 (#37's FeeModel micro_ceil)."""
    return lib.fee_pc(float(price), 1)


def net_over(ask, fee, win):
    return float(win) - ask - SLIP - fee


def main_strike_table(games, s):
    """One row per game of `games` (same order): the strike with mid nearest 0.50 among valid quotes with mid in
    [0.15, 0.85] (ties to the lower strike), its quotes, costs, the result y (NaN if `s` has no y or it is unsettled)
    and the net per contract of an OVER and of an UNDER bet.  `valid` = has a strike and a settled result."""
    ev = pd.Index(games.event.values)
    q = s[s.event.isin(ev)].dropna(subset=["bid", "ask"]).copy()
    q["mid"] = (q.bid + q.ask) / 2.0
    q = q[(q.mid >= 0.15 - 1e-12) & (q.mid <= 0.85 + 1e-12) & ((q.ask - q.bid) <= 0.10 + 1e-9)]   # amendment: spread <= 10c
    q["dist"] = (q.mid - 0.5).abs().round(9)
    q = q.sort_values(["event", "dist", "strike"], kind="stable").groupby("event").head(1).set_index("event")
    q = q.reindex(ev)
    has = q.ticker.notna().values
    bid, ask = q.bid.values.astype(float), q.ask.values.astype(float)
    y = q["y"].values.astype(float) if "y" in q else np.full(len(q), np.nan)
    fo = np.array([fee1(a) if h else np.nan for a, h in zip(ask, has)])
    fu = np.array([fee1(1.0 - b) if h else np.nan for b, h in zip(bid, has)])
    out = pd.DataFrame({"event": ev.values, "has_strike": has, "ticker": q.ticker.values, "K": q.strike.values.astype(float),
                        "bid": bid, "ask": ask, "y": y, "price_over": ask, "price_under": 1.0 - bid,
                        "fee_over": fo, "fee_under": fu})
    out["paid_over"] = out.price_over + SLIP
    out["paid_under"] = out.price_under + SLIP
    out["win_over"] = (out.y == 1.0).astype(float)
    out["win_under"] = (out.y == 0.0).astype(float)
    out["net_over"] = out.win_over - out.paid_over - out.fee_over
    out["net_under"] = out.win_under - out.paid_under - out.fee_under
    out["valid"] = out.has_strike & np.isfinite(out.y)
    return out


def bet_arrays(bt):
    """bt (main_strike_table) -> dict of float arrays over the VALID games, and the boolean mask used."""
    v = bt.valid.values
    return {k: bt[k].values[v].astype(float) for k in
            ("K", "net_over", "net_under", "win_over", "win_under", "paid_over", "paid_under")}, v


# ------------------------------------------------------------------ per-strategy statistics (vectorised)
def lower_bound(mean, sd, n):
    return mean - Z95 * sd / np.sqrt(n)


def strategy_stats(P, ba, thresholds=THRESHOLDS, chunk=2000):
    """P (S, N) projections on the valid games; ba = bet_arrays()[0].  Returns dict of (nT, S) arrays:
    n, wins, paid (sum of price paid incl. slippage), s1 (sum net), s2 (sum net^2)."""
    S, N = P.shape
    nT = len(thresholds)
    out = {k: np.zeros((nT, S)) for k in ("n", "wins", "paid", "s1", "s2")}
    K = ba["K"]
    for lo in range(0, S, chunk):
        d = P[lo:lo + chunk] - K
        over = d > 0
        under = d < 0
        ad = np.abs(d)
        net = np.where(over, ba["net_over"], ba["net_under"])
        win = np.where(over, ba["win_over"], ba["win_under"])
        paid = np.where(over, ba["paid_over"], ba["paid_under"])
        for ti, T in enumerate(thresholds):
            m = (ad >= T) & (over | under)
            out["n"][ti, lo:lo + chunk] = m.sum(axis=1)
            out["wins"][ti, lo:lo + chunk] = (win * m).sum(axis=1)
            out["paid"][ti, lo:lo + chunk] = (paid * m).sum(axis=1)
            nm = net * m
            out["s1"][ti, lo:lo + chunk] = nm.sum(axis=1)
            out["s2"][ti, lo:lo + chunk] = (nm * net).sum(axis=1)
    return out


def finalize_stats(st):
    """Sums -> n, wins, win_rate, avg_paid, mean, sd (ddof 1), LB, total $ (1 contract per bet).  n < 2 -> NaN."""
    n = st["n"]
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = st["s1"] / n
        var = (st["s2"] - n * mean ** 2) / (n - 1)
        sd = np.sqrt(np.clip(var, 0.0, None))
        lb = lower_bound(mean, sd, n)
        return {"n": n, "wins": st["wins"], "win_rate": st["wins"] / n, "avg_paid": st["paid"] / n, "mean": mean,
                "sd": sd, "lb": lb, "total": st["s1"]}


def nondefault_counts():
    idx = np.indices(gl.SHAPE).reshape(len(gl.SHAPE), -1)
    return sum((idx[j] != gl.DEFAULT_IDX[j]).astype(int) for j in range(len(gl.SHAPE)))


def pick_winner(lb, n, nondef, thresholds=THRESHOLDS, min_n=MIN_N, tol=LB_TIE):
    """lb, n: (nT, S) arrays.  Eligible: n >= min_n and finite LB.  Winner = max LB; strategies within `tol` of the
    max are tied: fewest non-default parts, then the smaller threshold, then the smaller flat formula index.
    Returns (T_index, flat_index) or None if nothing is eligible."""
    ok = (n >= min_n) & np.isfinite(lb)
    if not ok.any():
        return None
    best = np.max(np.where(ok, lb, -np.inf))
    cand = np.argwhere(ok & (lb >= best - tol))
    key = [(int(nondef[s]), int(thresholds[t]), int(s)) for t, s in cand]
    j = min(range(len(key)), key=lambda i: key[i])
    return int(cand[j][0]), int(cand[j][1])


# ------------------------------------------------------------------ luck check
def luck_check(P, ba, rng, n_strat=2000, thresholds=THRESHOLDS, min_n=MIN_N):
    """n_strat random-side strategies on the same games/strike/costs.  Each draws a formula and a threshold uniformly
    (so the bet subsets have the same structure as the real strategies: |proj - K| >= T), then a fair coin per game for
    the side.  Returns arrays of LB/mean/n of the ELIGIBLE ones (n >= min_n) and the count drawn."""
    S, N = P.shape
    lbs, means, ns = [], [], []
    for _ in range(n_strat):
        fi, ti = int(rng.integers(S)), int(rng.integers(len(thresholds)))
        side_over = rng.integers(0, 2, N).astype(bool)
        d = P[fi] - ba["K"]
        m = (np.abs(d) >= thresholds[ti]) & (d != 0)
        net = np.where(side_over, ba["net_over"], ba["net_under"])[m]
        if len(net) >= min_n:
            mean, sd = float(net.mean()), float(net.std(ddof=1))
            lbs.append(float(lower_bound(mean, sd, len(net))))
            means.append(mean)
            ns.append(len(net))
    return {"drawn": n_strat, "eligible": len(lbs), "lb": np.array(lbs), "mean": np.array(means), "n": np.array(ns)}


def luck_full(P, ba, rng, thresholds=THRESHOLDS, min_n=MIN_N, chunk=2000):
    """INFO ONLY: one random-side strategy for EVERY real (formula, threshold) bet subset (134,400), so the max can be
    compared with the real max over the same number of tries.  Returns the eligible LBs."""
    S, N = P.shape
    lbs = []
    for lo in range(0, S, chunk):
        d = P[lo:lo + chunk] - ba["K"]
        side = rng.integers(0, 2, d.shape).astype(bool)
        net = np.where(side, ba["net_over"], ba["net_under"])
        ad = np.abs(d)
        for T in thresholds:
            m = (ad >= T) & (d != 0)
            n = m.sum(axis=1)
            nm = net * m
            with np.errstate(invalid="ignore", divide="ignore"):
                mean = nm.sum(axis=1) / n
                var = ((nm * net).sum(axis=1) - n * mean ** 2) / (n - 1)
                lb = lower_bound(mean, np.sqrt(np.clip(var, 0, None)), n)
            lbs.append(lb[n >= min_n])
    return np.concatenate(lbs)


def luck_shared(P, ba, rng, reps=200, thresholds=THRESHOLDS, min_n=MIN_N, chunk=2000):
    """Shared-flip null (amendment 6): per rep ONE random side per game, applied to every strategy (same bet games);
    the maximum LB over all eligible strategies.  Returns the array of `reps` maxima."""
    S, N = P.shape
    side = rng.integers(0, 2, (N, reps)).astype(bool)
    net = np.where(side, ba["net_over"][:, None], ba["net_under"][:, None])      # (N, reps)
    net2 = net ** 2
    best = np.full(reps, -np.inf)
    for lo in range(0, S, chunk):
        d = P[lo:lo + chunk] - ba["K"]
        ad = np.abs(d)
        for T in thresholds:
            m = ((ad >= T) & (d != 0)).astype(np.float64)
            n = m.sum(axis=1)
            ok = n >= min_n
            if not ok.any():
                continue
            m, n = m[ok], n[ok][:, None]
            mean = (m @ net) / n
            var = np.clip((m @ net2 - n * mean ** 2) / (n - 1), 0, None)
            best = np.maximum(best, lower_bound(mean, np.sqrt(var), n).max(axis=0))
    return best


def dist_summary(x):
    if len(x) == 0:
        return {"n": 0}
    q = np.quantile(x, [0.05, 0.25, 0.5, 0.75, 0.95, 0.99])
    return {"n": int(len(x)), "min": float(x.min()), "q05": float(q[0]), "q25": float(q[1]), "median": float(q[2]),
            "q75": float(q[3]), "q95": float(q[4]), "q99": float(q[5]), "max": float(x.max())}


# ------------------------------------------------------------------ unseen test pieces
def bets_for(proj, bt, T):
    """Per-game bets of ONE strategy: DataFrame of the bet games (event, K, proj, side, paid, fee, win, net)."""
    v = bt.valid.values
    d = np.asarray(proj, float) - bt.K.values
    m = v & (np.abs(d) >= T) & (d != 0)
    over = d > 0
    sub = bt[m]
    side = np.where(over[m], "over", "under")
    return pd.DataFrame({"event": sub.event.values, "K": sub.K.values, "proj": np.asarray(proj)[m], "side": side,
                         "paid": np.where(over[m], sub.paid_over, sub.paid_under),
                         "fee": np.where(over[m], sub.fee_over, sub.fee_under),
                         "win": np.where(over[m], sub.win_over, sub.win_under),
                         "net": np.where(over[m], sub.net_over, sub.net_under)})


def coin_flip(bets, bt, n=500, seed=SEED):
    """Same bet games and strike, random side, same costs.  Returns the share of copies whose mean net >= the actual."""
    b = bt.set_index("event").loc[bets.event.values]
    rng = np.random.default_rng(seed)
    side = rng.integers(0, 2, (n, len(b))).astype(bool)
    net = np.where(side, b.net_over.values, b.net_under.values)
    means = net.mean(axis=1)
    actual = float(bets.net.mean())
    return {"n_games": int(len(b)), "actual_mean": actual, "copies_mean": float(means.mean()),
            "frac_copies_ge_actual": float((means >= actual).mean()), "copies": n}


def pass_bar(bets, weeks, coin):
    """The pre-registered unseen pass bar items 1-5."""
    n = len(bets)
    if n == 0:
        return {k: False for k in ("1_mean_net_pos", "2_boot_lo_gt0", "3_bets_ge_100", "4_coin_flip_le_5pct",
                                   "5_usd_per_week_ge_2")}, {}
    mean = float(bets.net.mean())
    lo, hi = lib.boot_mean_ci(bets.net.values, n=10000, seed=SEED)
    usd_wk = float(bets.net.sum() * lib.COUNT / weeks)
    bar = {"1_mean_net_pos": bool(mean > 0), "2_boot_lo_gt0": bool(lo > 0), "3_bets_ge_100": bool(n >= 100),
           "4_coin_flip_le_5pct": bool(coin["frac_copies_ge_actual"] <= 0.05), "5_usd_per_week_ge_2": bool(usd_wk >= 2)}
    return bar, {"n": n, "mean": mean, "ci95": [lo, hi], "usd_per_week_at_10": usd_wk, "weeks": weeks}
