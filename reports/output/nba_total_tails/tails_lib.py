"""Research log #52 library: far-out NBA total-line tails (sell the long shot). Pure functions are unit-tested in
test_total_tails.py; build_records() does the data access (read-only sqlite + pbp parquet, no network).

Reuses #46 helpers (fee_taker, EventCandles, decision_time, checkpoints_from_pbp, select, bootstrap_lb) by importing them
from reports/output/nba_ingame_overreaction (its dir is put on sys.path; its modules are named common/pipeline)."""
from __future__ import annotations

import itertools
import math
import sqlite3
import statistics
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
INJ = ROOT / "reports" / "output" / "nba_injury_speed"
ING = ROOT / "reports" / "output" / "nba_ingame_overreaction"


def _load(name, path):
    """Load a #46 module from its file under a private path entry, without leaving ING on sys.path (it holds a run_unseen.py)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


_saved = list(sys.path)
IGC = _load("common", ING / "common.py")          # #46 common: fee_taker, constants (pipeline does `import common as C`)
IGP = _load("ig_pipeline", ING / "pipeline.py")   # #46 pipeline: EventCandles, decision_time, checkpoints_from_pbp, bootstrap_lb
sys.path[:] = _saved                              # pipeline.py prepends its own dir; undo that

SEED = 20261022
DISC_FIRST, DISC_LAST = "2025-10-21", "2026-01-22"
UNSEEN_FIRST, UNSEEN_LAST = "2026-01-23", "2026-04-12"
CHECKPOINTS = ("T-1h", "HT", "Q3")           # Q3 = end of Q3 (first pbp event of Q4)
CP_PBP = {"HT": "HT", "Q3": "Q3"}            # name in #46's checkpoints_from_pbp
BANDS = (0.05, 0.10)
TAILS = ("over", "under")
MAX_SPREAD = 0.10
ENTRY_SLIP = 0.01
SKIP_ENTRY_AT = 0.995
MIN_N = 60
EPS = 1e-9
PRE_LEAD_S = 3600
CAL_BUCKETS_C = [(0, 2), (2, 5), (5, 10), (10, 15), (85, 90), (90, 95), (95, 98), (98, 100)]

fee_taker = IGC.fee_taker


def versions():
    return [(cp, band, tail) for cp in CHECKPOINTS for band in BANDS for tail in TAILS]


# ------------------------------------------------------------------ strike choice
def mid_of(bid, ask):
    return round((bid + ask) / 2.0, 6)


def tail_pick(points, tail, band):
    """points: iterable of dicts/tuples (strike, ticker, bid, ask, age, result). Choose the tail strike by the pre-registered rule
    (selection first, requirements checked AFTER on the chosen strike only).
      over : YES mid in [0.01, X] -> LARGEST YES mid.   under: YES mid in [1-X, 0.99] -> SMALLEST YES mid.
    Ties on mid: the lower strike. Quotes with a missing bid or ask are not candidates.
    Returns (pick or None, reason or None); pick = dict(strike,ticker,bid,ask,age,result,mid)."""
    best = None
    for p in points:
        s, tk, b, a, age, res = p
        if b is None or a is None:
            continue
        m = mid_of(b, a)
        if tail == "over":
            ok = 0.01 - EPS <= m <= band + EPS
            key = (-m, s)
        else:
            ok = (1.0 - band) - EPS <= m <= 0.99 + EPS
            key = (m, s)
        if ok and (best is None or key < best[0]):
            best = (key, dict(strike=s, ticker=tk, bid=b, ask=a, age=age, result=res, mid=m))
    if best is None:
        return None, "no_strike_in_band"
    return best[1], None


def check_pick(pick, tail):
    """Requirements on the chosen strike. Returns skip reason or None."""
    b, a = pick["bid"], pick["ask"]
    if a < b - EPS:
        return "crossed_quote"
    if tail == "over" and b < 0.01 - EPS:
        return "yes_bid_below_1c"
    if tail == "under" and a > 0.99 + EPS:
        return "yes_ask_above_99c"
    if a - b > MAX_SPREAD + EPS:
        return "spread_gt_10c"
    if entry_price(tail, b, a, fav=True) >= SKIP_ENTRY_AT - EPS:
        return "entry_ge_995"
    if pick["result"] not in ("yes", "no"):
        return "no_binary_result"
    return None


# ------------------------------------------------------------------ prices
def entry_price(tail, bid, ask, fav=True):
    """Entry after +1c slippage. Over-tail favourite = NO at 1-yes_bid; long shot = YES at yes_ask.
    Under-tail favourite = YES at yes_ask; long shot = NO at 1-yes_bid."""
    buy_yes = (tail == "under") if fav else (tail == "over")
    base = ask if buy_yes else 1.0 - bid
    return round(base + ENTRY_SLIP, 4)


def net_side(tail, bid, ask, result, fav=True):
    """One contract held to settlement: win - entry - fee_taker(entry)."""
    buy_yes = (tail == "under") if fav else (tail == "over")
    p = entry_price(tail, bid, ask, fav)
    win = 1.0 if result == ("yes" if buy_yes else "no") else 0.0
    return win - p - fee_taker(min(max(p, 0.0), 1.0))


# ------------------------------------------------------------------ bet rows
def bet_rows(rec):
    """All (tail, band) rows of one game-checkpoint record. Each row has skip (reason or None) and, when placed, entry/net_fav/net_ls."""
    rows = []
    for tail in TAILS:
        for band in BANDS:
            r = {"game": rec["game"], "date": rec["date"], "cp": rec["cp"], "tail": tail, "band": band, "D": rec["D"],
                 "strike": None, "ticker": None, "yes_bid": None, "yes_ask": None, "spread": None, "age_s": None, "result": None,
                 "skip": None, "entry": None, "net_fav": None, "net_ls": None, "ls_entry": None}
            pick, why = tail_pick(rec["points"], tail, band)
            if pick is not None:
                r.update(strike=pick["strike"], ticker=pick["ticker"], yes_bid=pick["bid"], yes_ask=pick["ask"],
                         spread=round(pick["ask"] - pick["bid"], 4), age_s=pick["age"], result=pick["result"])
                why = check_pick(pick, tail)
            r["skip"] = why
            if why is None:
                b, a, res = pick["bid"], pick["ask"], pick["result"]
                r["entry"] = entry_price(tail, b, a, True)
                r["ls_entry"] = entry_price(tail, b, a, False)
                r["net_fav"] = net_side(tail, b, a, res, True)
                r["net_ls"] = net_side(tail, b, a, res, False)
            rows.append(r)
    return rows


# ------------------------------------------------------------------ stats
def lb(vals):
    n = len(vals)
    if n < 2:
        return float("nan")
    return statistics.fmean(vals) - 1.96 * statistics.stdev(vals) / math.sqrt(n)


def version_stats(rows):
    out = []
    for cp, band, tail in versions():
        b = [r for r in rows if r["cp"] == cp and r["band"] == band and r["tail"] == tail and r["skip"] is None]
        nets = [r["net_fav"] for r in b]
        n = len(nets)
        out.append({"cp": cp, "band": band, "tail": tail, "n": n,
                    "win": (sum(x > 0 for x in nets) / n) if n else None,
                    "mean_entry": statistics.fmean(r["entry"] for r in b) if n else None,
                    "mean": statistics.fmean(nets) if n else None,
                    "sd": statistics.stdev(nets) if n > 1 else None,
                    "lb": lb(nets) if n > 1 else None})
    return out


def select(vrows):
    """Highest LB among versions with n >= 60."""
    el = [r for r in vrows if r["n"] >= MIN_N and r["lb"] is not None and not math.isnan(r["lb"])]
    return max(el, key=lambda r: r["lb"]) if el else None


def luck_test(rows, reps=500, seed=SEED):
    """Random side per (game, checkpoint, tail) shared across both bands: favourite (as in the rule) or the long shot at its own
    ask + 1c + fee, same strike and candle. The 12-version selection is re-run in every rep.
    Share = reps whose best LB >= the real best LB. Verdict PASS iff share <= 5% (NO_ELIGIBLE when the real selection is empty)."""
    placed = [r for r in rows if r["skip"] is None]
    real = select(version_stats(rows))
    keys = sorted({(r["game"], r["cp"], r["tail"]) for r in placed})
    kidx = {k: i for i, k in enumerate(keys)}
    vecs = []
    for cp, band, tail in versions():
        b = [r for r in placed if r["cp"] == cp and r["band"] == band and r["tail"] == tail]
        if len(b) >= MIN_N:
            vecs.append((np.array([kidx[(r["game"], r["cp"], r["tail"])] for r in b]),
                         np.array([r["net_fav"] for r in b]), np.array([r["net_ls"] for r in b])))
    if real is None:
        return {"real_best_lb": None, "reps": reps, "seed": seed, "share_reps_ge_real": None, "verdict": "NO_ELIGIBLE"}
    rng = np.random.default_rng(seed)
    best = []
    for _ in range(reps):
        coin = rng.random(len(keys)) < 0.5      # True: favourite side (as in the rule)
        bl = float("-inf")
        for idx, nf, nl in vecs:
            x = np.where(coin[idx], nf, nl)
            n = len(x)
            bl = max(bl, x.mean() - 1.96 * x.std(ddof=1) / math.sqrt(n))
        best.append(bl)
    best = np.array(best)
    share = float((best >= real["lb"]).mean())
    return {"real_best_lb": real["lb"], "reps": reps, "seed": seed, "share_reps_ge_real": share,
            "rep_best_lb_mean": float(best.mean()), "rep_best_lb_p95": float(np.quantile(best, 0.95)),
            "verdict": "PASS" if share <= 0.05 else "FAIL"}


def coin_flip_share(bets, reps=500, seed=SEED):
    """Pass-bar item 3 for one version: share of 500 copies (random side at the same strike and candle per bet) whose mean net >= real mean."""
    if not bets:
        return None
    real = float(np.mean([r["net_fav"] for r in bets]))
    rng = np.random.default_rng(seed)
    nf = np.array([r["net_fav"] for r in bets])
    nl = np.array([r["net_ls"] for r in bets])
    ms = [float(np.where(rng.random(len(bets)) < 0.5, nf, nl).mean()) for _ in range(reps)]
    return float((np.array(ms) >= real).mean())


def loss_streak(bets):
    """Worst consecutive-loss run and max cumulative drawdown of the bets in time order (decision time D, then game)."""
    seq = sorted(bets, key=lambda r: (r["D"], r["game"]))
    run = worst = 0
    cum = peak = dd = 0.0
    for r in seq:
        run = run + 1 if r["net_fav"] < 0 else 0
        worst = max(worst, run)
        cum += r["net_fav"]
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return {"worst_loss_streak": worst, "max_drawdown_dollars": dd, "n": len(seq)}


# ------------------------------------------------------------------ calibration / band counts
def cal_bucket(mid):
    c = round(mid * 100.0, 4)
    for lo, hi in CAL_BUCKETS_C:
        if (lo <= c < hi) or (hi == 100 and c == 100):
            return (lo, hi)
    return None


def calibration(recs):
    """Pooled over all strikes and checkpoints (discovery only). Every strike with a quote (bid, ask not None, ask>=bid) and a yes/no result."""
    acc = {b: {"n": 0, "yes": 0, "mid_sum": 0.0, "n_sp10": 0, "yes_sp10": 0} for b in CAL_BUCKETS_C}
    for rec in recs:
        for s, tk, b, a, age, res in rec["points"]:
            if b is None or a is None or a < b - EPS or res not in ("yes", "no"):
                continue
            k = cal_bucket(mid_of(b, a))
            if k is None:
                continue
            d = acc[k]
            d["n"] += 1
            d["yes"] += res == "yes"
            d["mid_sum"] += mid_of(b, a)
            if a - b <= MAX_SPREAD + EPS:
                d["n_sp10"] += 1
                d["yes_sp10"] += res == "yes"
    out = []
    for (lo, hi), d in acc.items():
        n = d["n"]
        out.append({"bucket": f"{lo}-{hi}c", "n": n, "yes_rate": d["yes"] / n if n else None,
                    "mean_mid": d["mid_sum"] / n if n else None,
                    "yes_rate_minus_mid": (d["yes"] / n - d["mid_sum"] / n) if n else None,
                    "n_spread_le_10c": d["n_sp10"], "yes_rate_spread_le_10c": d["yes_sp10"] / d["n_sp10"] if d["n_sp10"] else None})
    return out


def band_counts(recs):
    """Per checkpoint: records, and games with any strike (quote not missing) whose YES mid is in each band. Also bet-eligible counts."""
    out = {}
    for cp in CHECKPOINTS:
        rs = [r for r in recs if r["cp"] == cp]
        d = {"game_checkpoints": len(rs)}
        for tail in TAILS:
            for band in BANDS:
                n_any = n_ok = 0
                for r in rs:
                    pick, _ = tail_pick(r["points"], tail, band)
                    n_any += pick is not None
                    n_ok += pick is not None and check_pick(pick, tail) is None
                d[f"{tail}_{band:.2f}_any_strike_in_band"] = n_any
                d[f"{tail}_{band:.2f}_bet_eligible"] = n_ok
        out[cp] = d
    return out


def spread_desc(rows):
    out = {}
    for tail in TAILS:
        for band in BANDS:
            for cp in CHECKPOINTS:
                sp = [r["spread"] for r in rows if r["cp"] == cp and r["tail"] == tail and r["band"] == band and r["spread"] is not None]
                ag = [r["age_s"] for r in rows if r["cp"] == cp and r["tail"] == tail and r["band"] == band and r["age_s"] is not None]
                f = lambda a: ({"n": len(a), "mean": float(np.mean(a)), "median": float(np.median(a)), "p90": float(np.quantile(a, .9)), "max": float(np.max(a))}
                               if len(a) else {"n": 0})
                out[f"{cp}|{tail}|{band:.2f}"] = {"chosen_spread": f(sp), "chosen_quote_age_s": f(ag)}
    return out


# ------------------------------------------------------------------ data build
def _ro(p):
    return sqlite3.connect(f"file:{Path(p).as_posix()}?mode=ro", uri=True)


def build_records(first, last, only_events=None):
    """One record per (game, checkpoint) for ET dates first..last: {game,date,cp,D,points:[(strike,ticker,bid,ask,age_s,result)]}.
    Streams per event. T-1h: last candle of each strike ending at or before T-3600 (pre-tip table). HT / Q3: #46 guards
    (pbp periods present, bad_wallclock, stale_L0 [tip-T > 1h]) and the first candle ending >= C+60s, in-game candles only (end_ts > tip)."""
    ev = pd.read_csv(INJ / "events_list.csv")
    ev = ev[(ev.game_date_et >= first) & (ev.game_date_et <= last)].sort_values(["game_date_et", "event"])
    if only_events is not None:
        ev = ev[ev.event.isin(only_events)]
    gids = [int(x) for x in ev.hoopr_gid.dropna().unique()]
    pbp = pd.read_parquet(ROOT / "data/raw/nba_open/play_by_play_2026.parquet",
                          columns=["game_id", "game_play_number", "period_number", "wallclock", "home_score", "away_score", "season_type"],
                          filters=[("game_id", "in", gids)])
    pbp = pbp[pbp.season_type == 2]
    pre, ing = _ro(INJ / "candles.sqlite"), _ro(ING / "candles_ingame.sqlite")
    recs, games, skips = [], set(), {}

    def skip(e, why):
        skips.setdefault(why, []).append(e)

    for r in ev.itertuples():
        mk = pre.execute("SELECT ticker, strike, result FROM mkts WHERE event=?", (r.event,)).fetchall()
        if not mk:
            skip(r.event, "no_markets")
            continue
        strikes = {t: s for t, s, _ in mk}
        results = {t: res for t, _, res in mk}
        T = int(r.T)
        # ---- T-1h (pregame table; uses nothing after T-3600)
        D = T - PRE_LEAD_S
        pts = []
        for t in strikes:
            row = pre.execute("SELECT end_ts, bid, ask FROM candles WHERE ticker=? AND end_ts<=? ORDER BY end_ts DESC LIMIT 1", (t, D)).fetchone()
            if row:
                assert row[0] <= D
                pts.append((strikes[t], t, row[1], row[2], D - row[0], results[t]))
        if pts:
            recs.append({"game": r.event, "date": r.game_date_et, "cp": "T-1h", "D": D, "points": pts})
        else:
            skip(r.event, "no_pre_candles_at_T-1h")
        # ---- in-game
        if pd.isna(r.hoopr_tip) or pd.isna(r.hoopr_gid):
            skip(r.event, "no_pbp_match")
            continue
        g = pbp[pbp.game_id == int(r.hoopr_gid)]
        if g.empty:
            skip(r.event, "no_pbp_rows")
            continue
        cps = IGP.checkpoints_from_pbp(g)
        tip = int(r.hoopr_tip)
        why = ingame_game_guard(cps, tip, T)
        if why:
            skip(r.event, why)
            continue
        ing_series = {}
        for t in strikes:
            rows2 = ing.execute("SELECT end_ts, bid, ask FROM candles WHERE ticker=? AND end_ts>? ORDER BY end_ts", (t, tip)).fetchall()
            if rows2:
                ing_series[t] = ([x[0] for x in rows2], [(x[1], x[2]) for x in rows2])
        if not ing_series:
            skip(r.event, "no_ingame_candles")
            continue
        ec = IGP.EventCandles(ing_series, strikes, results)
        for cp in ("HT", "Q3"):
            d = IGP.decision_time(ec, cps[CP_PBP[cp]], cps["game_end"])
            if d is None:
                skip(r.event, f"no_decision_candle_{cp}")
                continue
            pts = [(s, tk, b, a, d - ets, results[tk]) for s, b, a, tk, ets in ec.points_at(d)]
            recs.append({"game": r.event, "date": r.game_date_et, "cp": cp, "D": d, "points": pts})
        games.add(r.event)
    pre.close()
    ing.close()
    return recs, games, skips


def ingame_game_guard(cps, tip, T):
    """#46 game-level guards (L0 itself is not used here, so no_L0_crossing does not apply)."""
    if any(cps[cp] is None for cp in ("Q1", "HT", "Q3")) or cps["end_q4"] is None:
        return "pbp_missing_period"
    dq = (cps["Q1"] - tip) / 60.0
    if not (IGC.BAD_WALLCLOCK_MIN[0] <= dq <= IGC.BAD_WALLCLOCK_MIN[1]):
        return "bad_wallclock"
    if tip - T > 3600:
        return "stale_L0"
    return None
