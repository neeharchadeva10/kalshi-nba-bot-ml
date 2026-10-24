"""Research log #51 (late-Q4 fouling vs blowouts). Reuses #46 helpers (fee_taker, implied_total, main_strike, EventCandles,
lb, bootstrap_lb) by import; everything else (late checkpoints, 12 versions, luck test) is here. No network access."""
from __future__ import annotations

import math
import statistics
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
P46_DIR = ROOT / "reports" / "output" / "nba_ingame_overreaction"
sys.path.append(str(P46_DIR))   # appended (not inserted) so this folder's run_*.py win over #46's
import common as C46          # noqa: E402  (#46 common: fee_taker, implied_total, main_strike, lb, coverage, INJ ...)
import pipeline as P46        # noqa: E402  (#46 pipeline: EventCandles, bootstrap_lb)

# #46's pipeline.py inserts its own folder at sys.path[0]; remove it so this folder's run_*.py / tests win.
sys.path[:] = [p for p in sys.path if Path(p).resolve() != P46_DIR]
sys.path.insert(0, str(HERE))

INJ = C46.INJ
SEED = 20261022
DISC_FIRST, DISC_LAST = "2025-10-21", "2026-01-22"
UNSEEN_FIRST, UNSEEN_LAST = "2026-01-23", "2026-04-12"
RS = (300, 180, 120)
STATES = (("close", 3), ("close", 6), ("blow", 15), ("blow", 20))
SLIP = 0.01
SLIP_SENS = 0.02
LAG_S = 60
MAX_DECISION_WAIT_S = 300          # decision candle must end within 5 min of C_R + 60 s
Q1_WINDOW_MIN = (20, 80)           # bad_wallclock guard (#46)
CR_WINDOW_MIN = (100, 220)         # C_R must lie in [tip+100min, tip+220min]
MIN_N = 60
BUCKETS = ((0, 3), (4, 6), (7, 10), (11, 14), (15, 19), (20, 10 ** 6))
BUCKET_NAMES = ("0-3", "4-6", "7-10", "11-14", "15-19", "20+")

fee_taker = C46.fee_taker
implied_total = C46.implied_total
main_strike = C46.main_strike
EventCandles = P46.EventCandles
bootstrap_lb = P46.bootstrap_lb
lb = C46.lb


def versions():
    return [(R, k, M) for R in RS for (k, M) in STATES]


def vname(v):
    return f"R{v[0]}_{v[1]}{'<=' if v[1] == 'close' else '>='}{v[2]}"


def bucket(margin):
    for (lo, hi), nm in zip(BUCKETS, BUCKET_NAMES):
        if lo <= margin <= hi:
            return nm
    return None


# ------------------------------------------------------------------ pbp
def late_checkpoints(g: pd.DataFrame) -> dict:
    """g: one game's pbp (game_play_number, period_number, clock_minutes, clock_seconds, wallclock, home_score,
    away_score, type_text). Returns {'q1': epoch of first period-2 event (by game_play_number) or None,
    'game_end': wallclock of last event, 'final_total', 'ot': bool, 'R': {R: None | {ts, margin, ft_after}}}.
    C_R = wallclock of the first period-4 event (by game_play_number) whose clock shows <= R seconds left."""
    g = g.sort_values("game_play_number").reset_index(drop=True)
    ts = pd.to_datetime(g["wallclock"], utc=True).map(lambda x: int(x.timestamp()))
    g = g.assign(_ts=ts.values)
    s2 = g[g.period_number == 2]
    out = {"q1": int(s2._ts.iloc[0]) if len(s2) else None, "game_end": int(g._ts.iloc[-1]),
           "final_total": int(g.home_score.iloc[-1]) + int(g.away_score.iloc[-1]),
           "ot": bool((g.period_number > 4).any()), "has_p4": bool((g.period_number == 4).any()), "R": {}}
    rem = g.clock_minutes.astype(float) * 60 + g.clock_seconds.astype(float)
    ft = g.type_text.fillna("").str.startswith("Free Throw")
    for R in RS:
        m = (g.period_number == 4) & (rem <= R)
        if not m.any():
            out["R"][R] = None
            continue
        i = int(np.flatnonzero(m.values)[0])
        row = g.iloc[i]
        out["R"][R] = {"ts": int(row._ts), "margin": abs(int(row.home_score) - int(row.away_score)),
                       "ft_after": int(ft.iloc[i + 1:].sum())}
    return out


# ------------------------------------------------------------------ quotes / records
def decision_time(ec, c_ts, game_end):
    """First in-game candle (any market of the event) ending at or after C_R + 60 s -> (d, reason_or_None)."""
    d = ec.first_candle_ge(c_ts + LAG_S)
    if d is None:
        return None, "no_decision_candle"
    assert d >= c_ts + LAG_S                       # no-look-ahead guard
    if d - (c_ts + LAG_S) > MAX_DECISION_WAIT_S:
        return None, "no_decision_candle"
    if d > game_end:
        return None, "decision_after_game_end"
    return d, None


def make_record(ec, R, info, tip, game_end, final_total, ot):
    r = {"R": R, "skip": None}
    if info is None:
        r["skip"] = "no_checkpoint"
        return r
    c_ts = info["ts"]
    r.update(C_ts=c_ts, margin=info["margin"], bucket=bucket(info["margin"]), ft_after=info["ft_after"],
             final=final_total, ot=ot)
    lo, hi = tip + CR_WINDOW_MIN[0] * 60, tip + CR_WINDOW_MIN[1] * 60
    if not (lo <= c_ts <= hi):
        r["skip"] = "checkpoint_outside_window"
        return r
    d, why = decision_time(ec, c_ts, game_end)
    if why:
        r["skip"] = why
        return r
    r.update(D=d, lag=d - (c_ts + LAG_S))
    pts = ec.points_at(d)
    r["LC"] = implied_total([(s, b, a) for s, b, a, _, _ in pts])      # None when no 0.50 crossing (calibration only)
    ms = main_strike([(s, b, a) for s, b, a, _, _ in pts])
    if ms is None:
        r["skip"] = "no_main_strike_spread"
        return r
    s, b, a = ms
    tk = [p[3] for p in pts if p[0] == s][0]
    res = ec.results.get(tk)
    if res not in ("yes", "no"):
        r["skip"] = "no_result"
        return r
    r.update(strike=s, ticker=tk, yes_bid=b, yes_ask=a, spread=a - b, result=res,
             age_s=d - [p[4] for p in pts if p[3] == tk][0])
    return r


def process_game(event, date, tip, cps, ing_series, strikes, results):
    """Pure per-game logic -> (records {(event, R): rec} or None, game-level skip reason or None)."""
    if cps["q1"] is None or not cps["has_p4"]:
        return None, "pbp_missing_period"
    dq = (cps["q1"] - tip) / 60.0
    if not (Q1_WINDOW_MIN[0] <= dq <= Q1_WINDOW_MIN[1]):
        return None, "bad_wallclock"
    if not ing_series:
        return None, "no_candles"
    ec = EventCandles(ing_series, strikes, results)
    out = {}
    for R in RS:
        rec = make_record(ec, R, cps["R"][R], tip, cps["game_end"], cps["final_total"], cps["ot"])
        rec.update(game=event, date=date)
        out[(event, R)] = rec
    return out, None


# ------------------------------------------------------------------ bets
def side_for_state(kind):
    return "yes" if kind == "close" else "no"


def entry_price(side, yes_bid, yes_ask, slip=SLIP):
    ask = yes_ask if side == "yes" else 1.0 - yes_bid
    return ask + slip


def net_bet(side, result, yes_bid, yes_ask, slip=SLIP):
    """Net per contract, held to settlement; None when the entry (after slippage) is >= 0.99 or <= 0.01."""
    p = entry_price(side, yes_bid, yes_ask, slip)
    p = round(p, 10)
    if p >= 0.99 or p <= 0.01:
        return None
    return (1.0 if result == side else 0.0) - p - fee_taker(p)


def in_state(rec, kind, M):
    return rec["margin"] <= M if kind == "close" else rec["margin"] >= M


def version_bets(recs, v, flip=None, slip=SLIP):
    """Returns (bets [(game, net)], n_price_skips). flip: optional {(game,R): 'yes'|'no'} random direction override."""
    R, kind, M = v
    out, ps = [], 0
    for (g, r_), r in recs.items():
        if r_ != R or r["skip"] or not in_state(r, kind, M):
            continue
        side = flip[(g, r_)] if flip is not None else side_for_state(kind)
        n = net_bet(side, r["result"], r["yes_bid"], r["yes_ask"], slip)
        if n is None:
            ps += 1
            continue
        out.append((g, n))
    return out, ps


def stats(nets):
    n = len(nets)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "lb": None, "win": None}
    m = statistics.fmean(nets)
    return {"n": n, "mean": m, "sd": statistics.stdev(nets) if n > 1 else float("nan"), "lb": lb(nets),
            "win": sum(x > 0 for x in nets) / n}


def all_version_stats(recs):
    rows = []
    for v in versions():
        b, ps = version_bets(recs, v)
        b2, _ = version_bets(recs, v, slip=SLIP_SENS)
        s2 = stats([x[1] for x in b2])
        rows.append({"version": vname(v), "R": v[0], "state": v[1], "M": v[2], "price_skips": ps, **stats([x[1] for x in b]),
                     "plus2c_n": s2["n"], "plus2c_mean": s2["mean"], "plus2c_lb": s2["lb"]})
    return rows


def select(rows):
    el = [r for r in rows if r["n"] >= MIN_N and r["lb"] is not None and not math.isnan(r["lb"])]
    return max(el, key=lambda r: r["lb"]) if el else None


def luck_test(recs, reps=500, seed=SEED):
    """Random YES/NO per (game, R), shared across versions, full 12-version selection re-run per rep."""
    real = select(all_version_stats(recs))
    real_lb = real["lb"] if real else float("-inf")
    keys = sorted(k for k, r in recs.items() if not r["skip"])
    ny = np.array([_nan(net_bet("yes", recs[k]["result"], recs[k]["yes_bid"], recs[k]["yes_ask"])) for k in keys])
    nn = np.array([_nan(net_bet("no", recs[k]["result"], recs[k]["yes_bid"], recs[k]["yes_ask"])) for k in keys])
    masks = []
    for (R, kind, M) in versions():
        masks.append(np.array([k[1] == R and in_state(recs[k], kind, M) for k in keys], dtype=bool))
    rng = np.random.default_rng(seed)
    best = []
    for _ in range(reps):
        coin = rng.random(len(keys)) < 0.5          # True -> YES
        x_all = np.where(coin, ny, nn)
        bl = float("-inf")
        for m in masks:
            x = x_all[m]
            x = x[~np.isnan(x)]
            n = len(x)
            if n < MIN_N:
                continue
            bl = max(bl, x.mean() - 1.96 * x.std(ddof=1) / math.sqrt(n))
        best.append(bl)
    best = np.array(best)
    fin = np.isfinite(best)
    share = float((best >= real_lb).mean())
    return {"real_best_lb": real_lb, "reps": reps, "seed": seed, "share_reps_ge_real": share,
            "rep_best_lb_mean": float(best[fin].mean()) if fin.any() else None,
            "rep_best_lb_p95": float(np.quantile(best[fin], 0.95)) if fin.any() else None,
            "n_reps_no_eligible": int((~fin).sum()), "verdict": "PASS" if (real and share <= 0.05) else "FAIL"}


def _nan(x):
    return float("nan") if x is None else x


def coin_flip_share(recs, v, mean, reps=500, seed=SEED):
    """Pass-bar item 3: random direction at the same candles (the selected version's bets), share of copies with mean >= real."""
    R, kind, M = v
    keys = [k for k, r in recs.items() if k[1] == R and not r["skip"] and in_state(r, kind, M)]
    rng = np.random.default_rng(seed)
    cf = []
    for _ in range(reps):
        tot = []
        for k in keys:
            side = "yes" if rng.random() < 0.5 else "no"
            n = net_bet(side, recs[k]["result"], recs[k]["yes_bid"], recs[k]["yes_ask"])
            if n is not None:
                tot.append(n)
        cf.append(float(np.mean(tot)) if tot else float("nan"))
    return float((np.array(cf) >= mean).mean()) if mean is not None and keys else None


# ------------------------------------------------------------------ reported-regardless blocks
def calibration_and_descriptives(recs):
    """By R x margin bucket: calibration (final total - LC at decision candle), OT rate, free throws after C_R.
    Uses records that reached a decision candle with a LC crossing (calibration) / not skipped (OT, FT)."""
    out = {}
    for R in RS:
        for nm in BUCKET_NAMES:
            cal = [r["final"] - r["LC"] for (g, r_), r in recs.items()
                   if r_ == R and r.get("bucket") == nm and r.get("D") is not None and r.get("LC") is not None]
            ok = [r for (g, r_), r in recs.items() if r_ == R and not r["skip"] and r["bucket"] == nm]
            e = {"n_cal": len(cal), "mean_final_minus_LC": float(np.mean(cal)) if cal else None,
                 "se": float(np.std(cal, ddof=1) / math.sqrt(len(cal))) if len(cal) > 1 else None,
                 "n_bets_records": len(ok),
                 "ot_rate": float(np.mean([r["ot"] for r in ok])) if ok else None,
                 "mean_ft_attempts_after_CR": float(np.mean([r["ft_after"] for r in ok])) if ok else None}
            out[f"R{R}|{nm}"] = e
    return out


def diag_blocks(recs):
    from collections import Counter
    sk = Counter((r["R"], r["skip"]) for r in recs.values())
    ok = [r for r in recs.values() if not r["skip"]]

    def desc(a):
        return {"n": len(a), "mean": float(np.mean(a)), "median": float(np.median(a)), "p90": float(np.quantile(a, 0.9)),
                "max": float(np.max(a))} if len(a) else {"n": 0}
    return {"checkpoint_skips": {f"R{R}|{why}": n for (R, why), n in sorted(sk.items(), key=str)},
            "spread_main_strike": {f"R{R}": desc([r["spread"] for r in ok if r["R"] == R]) for R in RS},
            "decision_lag_s_after_C_plus_60": {f"R{R}": desc([r["lag"] for r in ok if r["R"] == R]) for R in RS},
            "main_strike_quote_age_s": {f"R{R}": desc([r["age_s"] for r in ok if r["R"] == R]) for R in RS}}


RECORD_KEYS = ["game", "date", "R", "skip", "C_ts", "D", "lag", "age_s", "margin", "bucket", "LC", "final", "ot", "ft_after",
               "strike", "ticker", "yes_bid", "yes_ask", "spread", "result"]


def records_frame(recs):
    return pd.DataFrame([{k: r.get(k) for k in RECORD_KEYS} for r in recs.values()])


def bets_frame(recs):
    rows = []
    for v in versions():
        R, kind, M = v
        for (g, r_), r in recs.items():
            if r_ != R or r["skip"] or not in_state(r, kind, M):
                continue
            side = side_for_state(kind)
            n = net_bet(side, r["result"], r["yes_bid"], r["yes_ask"])
            n2 = net_bet(side, r["result"], r["yes_bid"], r["yes_ask"], SLIP_SENS)
            rows.append({"version": vname(v), "game": g, "date": r["date"], "side": side, "margin": r["margin"],
                         "strike": r["strike"], "yes_bid": r["yes_bid"], "yes_ask": r["yes_ask"],
                         "entry": None if n is None else round(entry_price(side, r["yes_bid"], r["yes_ask"]), 4),
                         "result": r["result"], "net": n, "net_plus2c": n2})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ data build
def build_records(first: str, last: str, only_events=None):
    """Per game-R records for ET dates first..last. Date filter is applied to events_list BEFORE any pbp score or Kalshi
    result is read. Streams per game; only needed pbp columns and the game's own tickers are loaded."""
    import sqlite3
    import pyarrow.parquet as pq
    ev = pd.read_csv(INJ / "events_list.csv")
    ev = ev[(ev.game_date_et >= first) & (ev.game_date_et <= last)].sort_values(["game_date_et", "event"])
    if only_events is not None:
        ev = ev[ev.event.isin(only_events)]
    gids = [int(x) for x in ev.hoopr_gid.dropna().unique()]
    cols = ["game_id", "game_play_number", "period_number", "clock_minutes", "clock_seconds", "wallclock", "home_score",
            "away_score", "season_type", "type_text"]
    pbp = pq.read_table(ROOT / "data/raw/nba_open/play_by_play_2026.parquet", columns=cols,
                        filters=[("game_id", "in", gids)]).to_pandas()
    pbp = pbp[pbp.season_type == 2]
    pre = sqlite3.connect(f"file:{(INJ / 'candles.sqlite').as_posix()}?mode=ro", uri=True)
    ing = sqlite3.connect(f"file:{(C46.HERE / 'candles_ingame.sqlite').as_posix()}?mode=ro", uri=True)
    recs, games, skips = {}, [], {}

    def skip(e, why):
        skips.setdefault(why, []).append(e)

    for r in ev.itertuples():
        if pd.isna(r.hoopr_tip) or pd.isna(r.hoopr_gid):
            skip(r.event, "no_pbp_match")
            continue
        g = pbp[pbp.game_id == int(r.hoopr_gid)]
        if g.empty:
            skip(r.event, "no_pbp_rows")
            continue
        cps = late_checkpoints(g)
        tip = int(r.hoopr_tip)
        mk = pre.execute("SELECT ticker, strike, result FROM mkts WHERE event=?", (r.event,)).fetchall()
        strikes = {t: s for t, s, _ in mk}
        results = {t: res for t, _, res in mk}
        ing_series = {}
        for t in strikes:
            rows2 = ing.execute("SELECT end_ts, bid, ask FROM candles WHERE ticker=? AND end_ts>? ORDER BY end_ts",
                                (t, tip)).fetchall()
            if rows2:
                ing_series[t] = ([x[0] for x in rows2], [(x[1], x[2]) for x in rows2])
        grecs, why = process_game(r.event, r.game_date_et, tip, cps, ing_series, strikes, results)
        if why:
            skip(r.event, why)
        if grecs is None:
            continue
        recs.update(grecs)
        games.append(r.event)
    pre.close()
    ing.close()
    return recs, games, skips
