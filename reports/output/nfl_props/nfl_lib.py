"""Pure functions for research log #50 (NFL props after usage shocks). See PREREGISTRATION.md."""
from __future__ import annotations
import math
import numpy as np

SEED = 20261022
LUCK_REPS = 500
SLIP = 0.01
MIN_N = 40
PRICE_LO, PRICE_HI, MAX_SPREAD = 0.15, 0.85, 0.10
MAX_QUOTE_AGE_S = 7200          # latest hourly candle must end within 2 h before the decision time
VERSIONS = [(s, k) for s in ("rush", "recyds", "rec") for k in ("A", "B10", "B20")]
EPS = 1e-9


def fee_e4(price: float, count: int = 1) -> int:
    """Taker fee of ONE order in 1/10000 dollars: ceil(0.07*C*P*(1-P)/0.0001), exact integer maths on the cent price."""
    pc = round(price * 100)
    num = 7 * count * pc * (100 - pc)
    return -(-num // 100)


def fee(price: float, count: int = 1) -> float:
    return fee_e4(price, count) / 10_000


def entry_price(ask: float) -> float:
    return round(ask + SLIP, 2)


def net_per_contract(paid: float, win: bool) -> float:
    return (1.0 if win else 0.0) - paid - fee(paid, 1)


def _px(d, key):
    """Candle price field in either format ('close' or 'close_dollars'); None if missing."""
    if not isinstance(d, dict):
        return None
    v = d.get(key + "_dollars", d.get(key))
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def quote_at(cands: list, decision_ts: int, max_age: int = MAX_QUOTE_AGE_S):
    """(bid, ask, candle_end) from the CLOSE of the latest hourly candle with end_period_ts <= decision_ts, else None.
    Candles ending after the decision time are never used."""
    best = None
    for c in cands:
        e = c.get("end_period_ts")
        if e is None or e > decision_ts or decision_ts - e > max_age:
            continue
        if best is None or e > best["end_period_ts"]:
            best = c
    if best is None:
        return None
    bid, ask = _px(best.get("yes_bid"), "close"), _px(best.get("yes_ask"), "close")
    if bid is None or ask is None:
        return None
    return bid, ask, best["end_period_ts"]


def usable(bid: float, ask: float) -> bool:
    """15-85c mid, spread <= 0.10, and a real two-sided book (bid>0, ask<1)."""
    if not (0.0 < bid <= ask < 1.0):
        return False
    mid = (bid + ask) / 2
    return PRICE_LO - EPS <= mid <= PRICE_HI + EPS and (ask - bid) <= MAX_SPREAD + EPS


def scope_strikes(thresholds: list, mean4: float, k: int = 2) -> list:
    """The k strikes nearest the player's 4-game mean (ties -> lower strike)."""
    return sorted(sorted(thresholds), key=lambda t: abs(t - mean4))[:k]


def pick_main(quotes: dict):
    """quotes: strike -> (bid, ask).  Main strike = usable quote whose mid is nearest 0.50 (ties -> lower strike)."""
    ok = {t: q for t, q in quotes.items() if q is not None and usable(*q)}
    if not ok:
        return None
    return min(sorted(ok), key=lambda t: abs((ok[t][0] + ok[t][1]) / 2 - 0.5))


def boot_lb(per_game_sum, per_game_n, draws=10_000, seed=SEED):
    """Bootstrap over games.  Inputs: per game total net and number of bets.  Returns 2.5th percentile of resampled mean-per-bet."""
    s = np.asarray(per_game_sum, float); n = np.asarray(per_game_n, float)
    if len(s) == 0:
        return float("nan")
    rng = np.random.default_rng(seed)
    out = np.empty(draws)
    for k in range(0, draws, 500):
        m = min(500, draws - k)
        idx = rng.integers(0, len(s), size=(m, len(s)))
        out[k:k + m] = s[idx].sum(1) / n[idx].sum(1)
    return float(np.percentile(out, 2.5))


def by_game(games, nets):
    g = {}
    for k, v in zip(games, nets):
        a = g.setdefault(k, [0.0, 0]); a[0] += v; a[1] += 1
    ks = sorted(g)
    return [g[k][0] for k in ks], [g[k][1] for k in ks]


def version_stats(games, nets, draws=10_000, seed=SEED):
    s, n = by_game(games, nets)
    nn = len(nets)
    return dict(n=nn, games=len(s), mean=float(np.mean(nets)) if nn else float("nan"),
                lb=boot_lb(s, n, draws, seed) if nn else float("nan"))


def select_version(stats: dict, min_n: int = MIN_N):
    """Highest discovery LB among versions with n >= min_n; ties broken by version order."""
    elig = [v for v in sorted(stats) if stats[v]["n"] >= min_n and stats[v]["lb"] == stats[v]["lb"]]
    if not elig:
        return None, []
    best = max(elig, key=lambda v: (stats[v]["lb"], -elig.index(v)))
    return best, elig


def draw_matched(rng, pool, paid: float, tol: float = 0.03):
    """pool: list of dicts(paid, net, game).  Uniform random pool entry with |paid - price| <= tol; if none, the nearest-priced ones."""
    near = [p for p in pool if abs(p["paid"] - paid) <= tol + EPS]
    if not near:
        d = min(abs(p["paid"] - paid) for p in pool)
        near = [p for p in pool if abs(p["paid"] - paid) <= d + EPS]
    return near[int(rng.random() * len(near))]


def luck_test(real: dict, pools: dict, real_best_lb: float, eligible: list, reps=LUCK_REPS, seed=SEED, draws=1000, tol=0.03):
    """real: version -> list of dicts(paid, week, stat, game, player).  pools: (stat, week) -> list of pool dicts(paid, net, game, player).
    Each rep: every real bet of an eligible version is replaced by a random same-stat same-week OTHER-player bet at a matched price;
    per-version bootstrap LB; the best LB over eligible versions (selection re-run) is the rep result.  Returns share of reps >= real best LB."""
    rng = np.random.default_rng(seed)
    best = np.full(reps, np.nan)
    for r in range(reps):
        lbs = []
        for v in eligible:
            games, nets = [], []
            for b in real[v]:
                pool = [p for p in pools.get((b["stat"], b["week"]), []) if p["player"] != b["player"]]
                if not pool:
                    continue
                p = draw_matched(rng, pool, b["paid"], tol)
                games.append(p["game"]); nets.append(p["net"])
            if nets:
                s, n = by_game(games, nets)
                lbs.append(boot_lb(s, n, draws, int(rng.integers(2 ** 31))))
        best[r] = max(lbs) if lbs else float("nan")
    ok = ~np.isnan(best)
    share = float((best[ok] >= real_best_lb).mean()) if ok.any() else None
    return dict(best_lb_per_rep=best, share_ge_real=share, n_reps=reps, n_valid=int(ok.sum()),
                verdict="FAIL" if (share is None or share > 0.05) else "PASS")
