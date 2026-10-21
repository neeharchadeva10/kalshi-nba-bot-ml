"""Statistics for the NFL ws-sim study (reports/output/nfl_ws/PREREGISTRATION.md): game split, bootstrap, coin flip, verdict.

Pure functions over trade rows (dicts with ``game``, ``count``, ``net``, ``gross`` ...); no engine code here.
"""
from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean, median

import numpy as np

SEED = 20261020
DRAWS = 10_000
COPIES = 500
GAMES_PER_WEEK = 15
MIN_GAMES, MIN_UNSEEN = 6, 3
MARKOUT_S = (10, 60, 300)


def split_games(order_key: dict[str, float]) -> tuple[list[str], list[str]]:
    """Games ordered by kickoff (caller supplies the key); the first half, rounded down, is discovery."""
    games = sorted(order_key, key=lambda g: (order_key[g], g))
    k = len(games) // 2
    return games[:k], games[k:]


def pooled(rows: list[dict]) -> dict:
    n = sum(r["count"] for r in rows)
    net = sum(r["net"] for r in rows)
    return {"trades": len(rows), "contracts": n, "gross": sum(r["gross"] for r in rows), "net": net,
            "net_per_contract": net / n if n else None}


def per_game(rows: list[dict]) -> dict[str, tuple[float, float]]:
    """game -> (net, contracts)."""
    out: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        out[r["game"]][0] += r["net"]
        out[r["game"]][1] += r["count"]
    return {g: (v[0], v[1]) for g, v in out.items()}


def bootstrap_lower(game_stats: dict[str, tuple[float, float]], draws: int = DRAWS, seed: int = SEED,
                    pct: float = 2.5) -> float | None:
    """Lower end of the two-sided 95% interval (2.5th percentile) of pooled net P&L per contract, resampling whole games."""
    games = sorted(game_stats)
    if not games:
        return None
    net = np.array([game_stats[g][0] for g in games])
    con = np.array([game_stats[g][1] for g in games])
    idx = np.random.default_rng(seed).integers(0, len(games), size=(draws, len(games)))
    n, c = net[idx].sum(axis=1), con[idx].sum(axis=1)
    stat = np.where(c > 0, n / np.where(c > 0, c, 1.0), 0.0)
    return float(np.percentile(stat, pct))


def plan_coin(slots: list[tuple[str, str]], windows: dict[str, tuple[int, int]], copies: int = COPIES,
              seed: int = SEED) -> list[tuple[int, str, str, int]]:
    """Random decisions: for every real entry slot (ticker, side) and every copy, a uniform time in the market's window.
    Same markets, same entry count and side split as the real entries. Deterministic given the seed."""
    rng = np.random.default_rng(seed)
    plan = []
    for ticker, side in slots:
        lo, hi = windows[ticker]
        for c, ns in enumerate(rng.integers(lo, hi + 1, size=copies)):
            plan.append((c, ticker, side, int(ns)))
    return plan


def coin_share(real_per_contract: float | None, copy_values: list[float | None]) -> float | None:
    """Share of copies whose net per contract is at least the real one (a copy with no fills counts as 0)."""
    if real_per_contract is None or not copy_values:
        return None
    return sum((v or 0.0) >= real_per_contract for v in copy_values) / len(copy_values)


def _mean_or_none(xs):
    xs = [x for x in xs if x is not None]
    return mean(xs) if xs else None


def version_row(vid: str, rows: list[dict], orders: int, fills: int, discovery: set, unseen: set) -> dict:
    """One line per version: totals, rates, segment splits and markouts."""
    out = {"version": vid, **pooled(rows), "orders": orders, "fills": fills,
           "fill_rate": fills / orders if orders else None, "phantom_rate": 1 - fills / orders if orders else None}
    for name, sel in (("discovery", lambda r: r["game"] in discovery), ("unseen", lambda r: r["game"] in unseen),
                      ("pregame", lambda r: r["phase"] == "pregame"), ("ingame", lambda r: r["phase"] == "ingame")):
        p = pooled([r for r in rows if sel(r)])
        out.update({f"{name}_trades": p["trades"], f"{name}_net": p["net"],
                    f"{name}_net_per_contract": p["net_per_contract"]})
    for h in MARKOUT_S:
        out[f"markout_thin_{h}s"] = _mean_or_none(r.get(f"mo_thin_{h}") for r in rows)
        out[f"markout_fair_{h}s"] = _mean_or_none(r.get(f"mo_fair_{h}") for r in rows)
    return out


def gap_stats(lifetimes_s: list[float]) -> dict:
    if not lifetimes_s:
        return {"n": 0}
    s = sorted(lifetimes_s)
    return {"n": len(s), "median_s": median(s), "mean_s": mean(s), "p90_s": s[min(len(s) - 1, int(0.9 * len(s)))]}


def lower_by_first_k(rows: list[dict], unseen_order: list[str], draws: int, seed: int) -> dict[int, float | None]:
    """The primary's unseen bootstrap lower bound using only the first k unseen games (by kickoff), k = 3..all."""
    stats = {g: (0.0, 0.0) for g in unseen_order} | per_game(rows)
    return {k: bootstrap_lower({g: stats[g] for g in unseen_order[:k]}, draws, seed)
            for k in range(3, len(unseen_order) + 1)}


def verdict(rows: list[dict], discovery: set, unseen: set, coin: float | None, *, draws: int = DRAWS,
            seed: int = SEED, games_per_week: int = GAMES_PER_WEEK, min_games: int = MIN_GAMES,
            min_unseen: int = MIN_UNSEEN, unseen_order: list[str] | None = None, lines_out_of_order: int = 0,
            tickers_in_two_streams: int = 0) -> dict:
    """Pass bar of the pre-registration for the primary version's rows (rules 1-6)."""
    if lines_out_of_order:
        return {"verdict": "INVALID: lines out of order", "lines_out_of_order": lines_out_of_order}
    if tickers_in_two_streams:
        return {"verdict": "INVALID: a ticker appears in two raw dirs", "tickers_in_two_streams": tickers_in_two_streams}
    n_games = len(discovery) + len(unseen)
    if n_games < min_games or len(unseen) < min_unseen:
        return {"verdict": "NOT ENOUGH GAMES", "games": n_games, "unseen_games": len(unseen),
                "need": f">= {min_games} games and >= {min_unseen} unseen"}
    dis = pooled([r for r in rows if r["game"] in discovery])
    uns_rows = [r for r in rows if r["game"] in unseen]
    uns = pooled(uns_rows)
    lb = bootstrap_lower({g: (0.0, 0.0) for g in unseen} | per_game(uns_rows), draws, seed)   # games with no trades count as 0
    weekly = uns["net"] / len(unseen) * games_per_week
    fc = [r["fill_check"] for r in rows if r.get("fill_check") is not None]
    fc_share = sum(fc) / len(fc) if fc else None
    rules = {
        "1_positive_discovery_and_unseen": bool((dis["net_per_contract"] or 0) > 0 and (uns["net_per_contract"] or 0) > 0),
        "2_unseen_bootstrap_lower_gt_0": bool(lb is not None and lb > 0),
        "3_unseen_trades_ge_100": uns["trades"] >= 100,
        "4_coin_flip_le_5pct": bool(coin is not None and coin <= 0.05),
        "5_weekly_ge_2": bool(weekly >= 2),
        "6_fill_check_ge_50pct": bool(fc_share is not None and fc_share >= 0.5),
    }
    return {"verdict": "PASS" if all(rules.values()) else "FAIL", "rules": rules, "games": n_games,
            "unseen_games": len(unseen), "discovery": dis, "unseen": uns, "bootstrap_lower_95": lb,
            "coin_share_as_good": coin, "projected_per_week": weekly, "fill_check_share": fc_share,
            "unseen_lower_bound_by_first_k": lower_by_first_k(uns_rows, unseen_order or sorted(unseen), draws, seed),
            "unseen_ci_note": f"bootstrap over {len(unseen)} unseen games; unseen_lower_bound_by_first_k is the effect of "
                              f"the {min_games}-game minimum (first k unseen games by kickoff, same seed and draws)"}


def finite(x):
    return None if isinstance(x, float) and not math.isfinite(x) else x
