"""Research log #55: sell 10-15c / buy 85-90c in-game NBA total tails, ONE pooled rule (see PREREGISTRATION.md).
Reuses #52's tails_lib (data access, #46 guards, fee, bootstrap, calibration) loaded by file path under a private module
name; #52 itself restores sys.path after loading #46's pipeline (which inserts its folder at sys.path[0])."""
from __future__ import annotations

import importlib.util
import math
import statistics
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
_saved = list(sys.path)
_spec = importlib.util.spec_from_file_location("tails52_lib", HERE.parent / "nba_total_tails" / "tails_lib.py")
T52 = importlib.util.module_from_spec(_spec)
sys.modules["tails52_lib"] = T52
_spec.loader.exec_module(T52)
sys.path[:] = _saved                      # guard: never leave #46's folder (it holds a run_unseen.py) on sys.path

SEED = T52.SEED                           # 20261022
DISC_FIRST, DISC_LAST = "2025-10-21", "2026-01-22"
UNSEEN_FIRST, UNSEEN_LAST = "2026-01-23", "2026-04-12"
CHECKPOINTS = ("HT", "Q3")
TAILS = ("over", "under")
OVER_LO, OVER_HI, OVER_CENTER = 0.10, 0.15, 0.125      # [0.10, 0.15)
UNDER_LO, UNDER_HI, UNDER_CENTER = 0.85, 0.90, 0.875   # (0.85, 0.90]
MAX_SPREAD = 0.10
EPS = 1e-9
MIN_N = 100
fee_taker = T52.fee_taker
mid_of = T52.mid_of


def tail_pick(points, tail):
    """points: (strike, ticker, bid, ask, age, result). Over: YES mid in [0.10,0.15), nearest 0.125, ties -> lower strike.
    Under: YES mid in (0.85,0.90], nearest 0.875, ties -> higher strike. Quotes with a missing bid/ask are not candidates.
    Requirements are checked AFTER on the chosen strike only (check_pick); no fallback."""
    best = None
    for s, tk, b, a, age, res in points:
        if b is None or a is None:
            continue
        m = mid_of(b, a)
        if tail == "over":
            ok = OVER_LO - EPS <= m < OVER_HI - EPS
            key = (round(abs(m - OVER_CENTER), 6), s)
        else:
            ok = UNDER_LO + EPS < m <= UNDER_HI + EPS
            key = (round(abs(m - UNDER_CENTER), 6), -s)
        if ok and (best is None or key < best[0]):
            best = (key, dict(strike=s, ticker=tk, bid=b, ask=a, age=age, result=res, mid=m))
    if best is None:
        return None, "no_strike_in_band"
    return best[1], None


def check_pick(pick):
    b, a = pick["bid"], pick["ask"]
    if a < b - EPS:
        return "crossed_quote"
    if a - b > MAX_SPREAD + EPS:
        return "spread_gt_10c"
    if b < 0.01 - EPS:
        return "yes_bid_below_1c"
    if a > 0.99 + EPS:
        return "yes_ask_above_99c"
    if pick["result"] not in ("yes", "no"):
        return "no_binary_result"
    return None


def entry_price(tail, bid, ask, fav=True):
    return T52.entry_price(tail, bid, ask, fav)      # +1c slippage; fav: over->NO at 1-bid, under->YES at ask


def net_side(tail, bid, ask, result, fav=True):
    return T52.net_side(tail, bid, ask, result, fav)  # win - entry - fee_taker(entry), 1 contract


def bet_rows(rec):
    rows = []
    for tail in TAILS:
        r = {"game": rec["game"], "date": rec["date"], "cp": rec["cp"], "tail": tail, "D": rec["D"], "strike": None,
             "ticker": None, "yes_bid": None, "yes_ask": None, "spread": None, "age_s": None, "result": None,
             "skip": None, "entry": None, "net_fav": None, "net_ls": None, "ls_entry": None}
        pick, why = tail_pick(rec["points"], tail)
        if pick is not None:
            r.update(strike=pick["strike"], ticker=pick["ticker"], yes_bid=pick["bid"], yes_ask=pick["ask"],
                     spread=round(pick["ask"] - pick["bid"], 4), age_s=pick["age"], result=pick["result"])
            why = check_pick(pick)
        r["skip"] = why
        if why is None:
            b, a, res = pick["bid"], pick["ask"], pick["result"]
            r["entry"] = entry_price(tail, b, a, True)
            r["ls_entry"] = entry_price(tail, b, a, False)
            r["net_fav"] = net_side(tail, b, a, res, True)
            r["net_ls"] = net_side(tail, b, a, res, False)
        rows.append(r)
    return rows


def assert_window(recs, first, last):
    bad = [r["date"] for r in recs if not (first <= r["date"] <= last)]
    if bad:
        raise AssertionError(f"records outside {first}..{last}: {bad[:3]}")


def rule_records(recs):
    """Only HT / Q3 records are in the rule (T-1h excluded)."""
    return [r for r in recs if r["cp"] in CHECKPOINTS]


def group_stats(bets):
    nets = [b["net_fav"] for b in bets]
    n = len(nets)
    byg = {}
    for b in bets:
        byg.setdefault(b["game"], []).append(b["net_fav"])
    return {"n": n, "win": (sum(x > 0 for x in nets) / n) if n else None,
            "mean_entry": statistics.fmean(b["entry"] for b in bets) if n else None,
            "mean": statistics.fmean(nets) if n else None,
            "bootstrap_lb95": T52.IGP.bootstrap_lb(byg, draws=10_000, seed=SEED) if n else None}


def coin_flip_share(bets, reps=500, seed=SEED):
    """Share of `reps` copies (random side per bet at the same strike/candle: rule side or long shot at its ask+1c+fee)
    whose mean net >= the real mean."""
    return T52.coin_flip_share(bets, reps, seed)


def analyse(recs, skips):
    rr = rule_records(recs)
    rows = [b for r in rr for b in bet_rows(r)]
    bets = [r for r in rows if r["skip"] is None]
    out = {"pooled": group_stats(bets),
           "by_checkpoint_tail": {f"{cp}|{t}": group_stats([b for b in bets if b["cp"] == cp and b["tail"] == t])
                                  for cp in CHECKPOINTS for t in TAILS},
           "games_with_bets": len({b["game"] for b in bets}),
           "game_checkpoint_records": {cp: sum(r["cp"] == cp for r in rr) for cp in CHECKPOINTS},
           "game_level_skips": {k: len(v) for k, v in skips.items()}}
    sk = {}
    for r in rows:
        k = f'{r["cp"]}|{r["tail"]}|{r["skip"]}'
        sk[k] = sk.get(k, 0) + 1
    out["bet_skip_reasons"] = sk
    out["scalar_results_skipped"] = sum(r["skip"] == "no_binary_result" for r in rows)
    desc = {}
    for cp in CHECKPOINTS:
        for t in TAILS:
            for nm, key in (("chosen_spread", "spread"), ("chosen_quote_age_s", "age_s")):
                a = [r[key] for r in rows if r["cp"] == cp and r["tail"] == t and r[key] is not None]
                desc[f"{cp}|{t}|{nm}"] = ({"n": len(a), "mean": float(np.mean(a)), "median": float(np.median(a)),
                                           "p90": float(np.quantile(a, .9)), "max": float(np.max(a))} if a else {"n": 0})
    out["spread_and_quote_age"] = desc
    out["loss_streak_drawdown"] = T52.loss_streak(bets) if bets else None
    out["calibration_8_buckets_HT_Q3"] = T52.calibration(rr)
    return out, rows, bets
