"""Research log #46 pipeline: checkpoints from pbp, L0 / L_C, per-game-checkpoint records, 48-version stats, luck test.
Pure functions are unit-tested in test_ingame.py. Data access (sqlite/parquet) is in build_records()."""
from __future__ import annotations

import bisect
import math
import sqlite3
import statistics
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C


# ---------------------------------------------------------------- pbp
def checkpoints_from_pbp(g: pd.DataFrame) -> dict:
    """g: one game's pbp with columns game_play_number, period_number, wallclock, home_score, away_score.
    Returns {'Q1': epoch of first event of period 2, 'HT': first of period 3, 'Q3': first of period 4,
    'end_q4': wallclock of the last period-4 event, 'game_end': wallclock of the last event, 'final_total': ...}.
    'First/last event' = by game_play_number (wallclock is not monotone in hoopR); None when the period is missing."""
    g = g.sort_values("game_play_number")
    ts = pd.to_datetime(g["wallclock"], utc=True).map(lambda x: int(x.timestamp()))
    g = g.assign(_ts=ts.values)
    out = {}
    for cp, per in C.CP_PERIOD.items():
        s = g[g.period_number == per]
        out[cp] = int(s._ts.iloc[0]) if len(s) else None
    s4 = g[g.period_number == 4]
    out["end_q4"] = int(s4._ts.iloc[-1]) if len(s4) else None
    out["game_end"] = int(g._ts.iloc[-1])
    last = g.iloc[-1]
    out["final_total"] = int(last.home_score) + int(last.away_score)
    return out


# ---------------------------------------------------------------- quotes
class EventCandles:
    """All candles of one event: {ticker: (sorted end_ts list, [(bid, ask), ...])}."""

    def __init__(self, series: dict, strikes: dict, results: dict):
        self.s = series
        self.strikes = strikes
        self.results = results
        self.all_ts = sorted({t for ts, _ in series.values() for t in ts})

    def at(self, ticker, t):
        """Latest candle of this market ending at or before t -> (end_ts, bid, ask) or None (carry-forward)."""
        if ticker not in self.s:
            return None
        ts, q = self.s[ticker]
        i = bisect.bisect_right(ts, t)
        if i == 0:
            return None
        return (ts[i - 1],) + q[i - 1]

    def points_at(self, t):
        out = []
        for tk in self.s:
            c = self.at(tk, t)
            if c is not None:
                out.append((self.strikes[tk], c[1], c[2], tk, c[0]))
        return out

    def first_candle_ge(self, t):
        i = bisect.bisect_left(self.all_ts, t)
        return self.all_ts[i] if i < len(self.all_ts) else None

    def last_candle_le(self, t):
        i = bisect.bisect_right(self.all_ts, t)
        return self.all_ts[i - 1] if i else None


def decision_time(ec: EventCandles, c_ts: int, game_end: int):
    """First candle (any market of the event) ending at or after C+60s. None if none, or it is after the game's last pbp event."""
    d = ec.first_candle_ge(c_ts + C.DECISION_LAG_S)
    if d is None or d > game_end:
        return None
    assert d >= c_ts + C.DECISION_LAG_S  # no-look-ahead guard
    return d


def line_at(ec: EventCandles, t):
    return C.implied_total([(s, b, a) for s, b, a, _, _ in ec.points_at(t)])


# ---------------------------------------------------------------- records
def make_record(ec: EventCandles, cp: str, cps: dict, tip: int, l0: float | None, exit_t: int | None, game_end: int):
    """One (game, checkpoint) record. Returns dict with 'skip' reason or the quotes needed for all versions."""
    r = {"cp": cp, "skip": None}
    c_ts = cps.get(cp)
    if c_ts is None:
        r["skip"] = "no_pbp_period"
        return r
    if l0 is None:
        r["skip"] = "no_L0_crossing"
        return r
    d = decision_time(ec, c_ts, game_end)
    if d is None:
        r["skip"] = "no_decision_candle"
        return r
    r.update(C_ts=c_ts, D=d, lag=d - (c_ts + C.DECISION_LAG_S), L0=l0)
    pts = ec.points_at(d)
    lc = C.implied_total([(s, b, a) for s, b, a, _, _ in pts])
    if lc is None:
        r["skip"] = "no_L_C_crossing"
        return r
    r.update(LC=lc, move=lc - l0)
    ms = C.main_strike([(s, b, a) for s, b, a, _, _ in pts])
    if ms is None:
        r["skip"] = "no_main_strike_spread"
        return r
    s, b, a = ms
    tk = [p[3] for p in pts if p[0] == s][0]
    res = ec.results.get(tk)
    if res not in ("yes", "no"):
        r["skip"] = "no_result"
        return r
    r.update(strike=s, ticker=tk, yes_bid=b, yes_ask=a, spread=a - b, result=res, age_s=d - [p[4] for p in pts if p[3] == tk][0])
    # exit quotes (NEXT): carry-forward quote of the same strike at exit_t (must be later than D)
    r["exit_t"] = exit_t
    r["xbid"] = r["xask"] = None
    if exit_t is not None and exit_t > d:
        c = ec.at(tk, exit_t)
        if c is not None:
            r["xbid"], r["xask"] = c[1], c[2]
    return r


def bet_net(rec: dict, side: str, exit_: str):
    """Net per contract for one record/side/exit, and a flag when NEXT fell back to HOLD (no exit quote)."""
    ask, _ = C.side_quotes(side, rec["yes_bid"], rec["yes_ask"])
    if exit_ == "hold":
        return C.net_hold(side, rec["result"], ask), False
    if rec["xbid"] is None or rec["xask"] is None:
        return C.net_hold(side, rec["result"], ask), True
    _, bid = C.side_quotes(side, rec["xbid"], rec["xask"])
    if bid is None or not (0 < bid <= 1):   # bid 0/None = nothing to sell into -> position is held
        return C.net_hold(side, rec["result"], ask), True
    return C.net_next(ask, bid), False


def version_bets(recs, cp, T, direction, exit_, flip=None):
    """recs: {(game, cp): record}. flip: optional {(game, cp): bool}; when given, the direction is randomised:
    fade-versions use the coin, follow-versions the opposite. Returns list of (game, net, fallback)."""
    out = []
    for (g, c), r in recs.items():
        if c != cp or r["skip"] or abs(r["move"]) < T:
            continue
        d = direction
        if flip is not None:
            coin_fade = flip[(g, c)]
            d = ("fade" if coin_fade else "follow") if direction == "fade" else ("follow" if coin_fade else "fade")
        side = C.side_for(d, r["LC"], r["L0"])
        net, fb = bet_net(r, side, exit_)
        out.append((g, net, fb))
    return out


def stats(nets):
    n = len(nets)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "lb": None, "win": None}
    m = statistics.fmean(nets)
    sd = statistics.stdev(nets) if n > 1 else float("nan")
    return {"n": n, "mean": m, "sd": sd, "lb": C.lb(nets), "win": sum(x > 0 for x in nets) / n}


def all_version_stats(recs):
    rows = []
    for cp, T, d, ex in C.grid():
        b = version_bets(recs, cp, T, d, ex)
        rows.append({"cp": cp, "T": T, "dir": d, "exit": ex, "n_fallback_hold": sum(x[2] for x in b),
                     **stats([x[1] for x in b])})
    return rows


def select(rows):
    """Highest LB among versions with n >= 60."""
    el = [r for r in rows if r["n"] >= C.MIN_N and r["lb"] is not None and not math.isnan(r["lb"])]
    return max(el, key=lambda r: r["lb"]) if el else None


# ---------------------------------------------------------------- luck test (vectorised)
def luck_test(recs, reps=500, seed=C.SEED):
    """Shared random direction per (game, checkpoint); the same 48-version selection re-run in each rep.
    Returns share of reps whose best LB >= the real best LB (reps with no eligible version count as 'not as good')."""
    real = select(all_version_stats(recs))
    real_lb = real["lb"] if real else float("-inf")
    keys = sorted(k for k, r in recs.items() if not r["skip"])
    idx = {k: i for i, k in enumerate(keys)}
    mv = np.array([abs(recs[k]["move"]) for k in keys])
    cpi = np.array([C.CHECKPOINTS.index(k[1]) for k in keys])
    # net for fade side and follow side, both exits
    nets = {}
    for ex in C.EXITS:
        nf = np.array([bet_net(recs[k], C.side_for("fade", recs[k]["LC"], recs[k]["L0"]), ex)[0] for k in keys])
        nw = np.array([bet_net(recs[k], C.side_for("follow", recs[k]["LC"], recs[k]["L0"]), ex)[0] for k in keys])
        nets[ex] = (nf, nw)
    rng = np.random.default_rng(seed)
    best = []
    for _ in range(reps):
        coin = rng.random(len(keys)) < 0.5      # True: "fade" version bets the fade side
        bl = float("-inf")
        for cp, T, d, ex in C.grid():
            m = (cpi == C.CHECKPOINTS.index(cp)) & (mv >= T)
            n = int(m.sum())
            if n < C.MIN_N:
                continue
            nf, nw = nets[ex]
            use_fade = coin if d == "fade" else ~coin
            x = np.where(use_fade, nf, nw)[m]
            lbv = x.mean() - 1.96 * x.std(ddof=1) / math.sqrt(n)
            bl = max(bl, lbv)
        best.append(bl)
    best = np.array(best)
    return {"real_best_lb": real_lb, "reps": reps, "seed": seed, "share_reps_ge_real": float((best >= real_lb).mean()),
            "rep_best_lb_mean": float(best[np.isfinite(best)].mean()) if np.isfinite(best).any() else None,
            "rep_best_lb_p95": float(np.quantile(best[np.isfinite(best)], 0.95)) if np.isfinite(best).any() else None,
            "n_reps_no_eligible": int((~np.isfinite(best)).sum()),
            "verdict": "PASS" if (best >= real_lb).mean() <= 0.05 else "FAIL"}


# ---------------------------------------------------------------- calibration
def calibration(recs):
    out = {}
    for cp in C.CHECKPOINTS:
        rs = [r for (g, c), r in recs.items() if c == cp and r.get("LC") is not None and r.get("final") is not None]
        if len(rs) < 3:
            out[cp] = {"n": len(rs)}
            continue
        x = np.array([r["move"] for r in rs])
        y = np.array([r["final"] - r["LC"] for r in rs])
        xm = x - x.mean()
        slope = float((xm * (y - y.mean())).sum() / (xm ** 2).sum())
        resid = y - y.mean() - slope * xm
        se = float(math.sqrt((resid ** 2).sum() / (len(x) - 2) / (xm ** 2).sum()))
        out[cp] = {"n": len(rs), "slope": slope, "se": se, "intercept": float(y.mean() - slope * x.mean()),
                   "mean_move": float(x.mean()), "mean_final_minus_LC": float(y.mean()),
                   "sd_move": float(x.std(ddof=1))}
    return out



STALE_L0_S = 3600


def process_game(event, date, tip, T, cps, pre_series, ing_series, strikes, results):
    """Pure per-game logic. Returns (records or None, game-level skip reason or None).
    pre_series: pre-tip candles (used for L0 ONLY). ing_series: candles ending after tip (used for L_C, main strike, exits)."""
    if any(cps[cp] is None for cp in C.CHECKPOINTS) or cps["end_q4"] is None:
        return None, "pbp_missing_period"
    dq = (cps["Q1"] - tip) / 60.0
    if not (C.BAD_WALLCLOCK_MIN[0] <= dq <= C.BAD_WALLCLOCK_MIN[1]):
        return None, "bad_wallclock"
    if tip - T > STALE_L0_S:
        return None, "stale_L0"
    if not ing_series and not pre_series:
        return None, "no_candles"
    l0 = None
    if pre_series:
        l0 = C.implied_total([(s, b, a) for s, b, a, _, _ in EventCandles(pre_series, strikes, results).points_at(tip)])
    ec = EventCandles(ing_series, strikes, results)
    dts = {cp: decision_time(ec, cps[cp], cps["game_end"]) for cp in C.CHECKPOINTS}
    exit_t = {"Q1": dts["HT"], "HT": dts["Q3"], "Q3": ec.last_candle_le(cps["end_q4"])}
    out = {}
    for cp in C.CHECKPOINTS:
        rec = make_record(ec, cp, cps, tip, l0, exit_t[cp], cps["game_end"])
        rec.update(game=event, date=date, final=cps["final_total"])
        out[(event, cp)] = rec
    return out, None


# ---------------------------------------------------------------- data build
def build_records(first: str, last: str, only_events=None):
    """Per game-checkpoint records for ET dates first..last (inclusive). Streams per game."""
    ev = pd.read_csv(C.INJ / "events_list.csv")
    ev = ev[(ev.game_date_et >= first) & (ev.game_date_et <= last)].sort_values(["game_date_et", "event"])
    if only_events is not None:
        ev = ev[ev.event.isin(only_events)]
    pbp = pd.read_parquet(C.ROOT / "data/raw/nba_open/play_by_play_2026.parquet",
                          columns=["game_id", "game_play_number", "period_number", "wallclock", "home_score", "away_score",
                                   "season_type"])
    pbp = pbp[pbp.season_type == 2]
    pre = sqlite3.connect(f"file:{(C.INJ / 'candles.sqlite').as_posix()}?mode=ro", uri=True)
    ing = sqlite3.connect(f"file:{(C.HERE / 'candles_ingame.sqlite').as_posix()}?mode=ro", uri=True)
    recs, games, skips = {}, [], {}

    def skip(ev_, why):
        skips.setdefault(why, []).append(ev_)

    for r in ev.itertuples():
        if pd.isna(r.hoopr_tip) or pd.isna(r.hoopr_gid):
            skip(r.event, "no_pbp_match")
            continue
        g = pbp[pbp.game_id == int(r.hoopr_gid)]
        if g.empty:
            skip(r.event, "no_pbp_rows")
            continue
        cps = checkpoints_from_pbp(g)
        tip = int(r.hoopr_tip)
        mk = pre.execute("SELECT ticker, strike, result FROM mkts WHERE event=?", (r.event,)).fetchall()
        strikes = {t: s for t, s, _ in mk}
        results = {t: res for t, _, res in mk}
        pre_series, ing_series = {}, {}
        for t in strikes:
            rows = pre.execute("SELECT end_ts, bid, ask FROM candles WHERE ticker=? AND end_ts<=? ORDER BY end_ts DESC LIMIT 1",
                               (t, tip)).fetchall()
            if rows:
                pre_series[t] = ([rows[0][0]], [(rows[0][1], rows[0][2])])
            rows2 = ing.execute("SELECT end_ts, bid, ask FROM candles WHERE ticker=? AND end_ts>? ORDER BY end_ts", (t, tip)).fetchall()
            if rows2:
                ing_series[t] = ([x[0] for x in rows2], [(x[1], x[2]) for x in rows2])
        grecs, why = process_game(r.event, r.game_date_et, tip, int(r.T), cps, pre_series, ing_series, strikes, results)
        if why:
            skip(r.event, why)
        if grecs is None:
            continue
        recs.update(grecs)
        games.append(r.event)
    pre.close()
    ing.close()
    return recs, games, skips


def diag_blocks(recs):
    """Checkpoint skip reasons, main-strike spread, decision lag and quote age blocks (shared by discovery and unseen)."""
    from collections import Counter
    sk = Counter((r["cp"], r["skip"]) for r in recs.values())
    ok = [r for r in recs.values() if not r["skip"]]

    def desc(a):
        return {"n": len(a), "mean": float(np.mean(a)), "median": float(np.median(a)), "p90": float(np.quantile(a, 0.9)),
                "max": float(np.max(a))} if len(a) else {"n": 0}
    return {"checkpoint_skips": {f"{cp}|{why}": n for (cp, why), n in sorted(sk.items(), key=str)},
            "checkpoint_spread_main_strike": {cp: desc([r["spread"] for r in ok if r["cp"] == cp]) for cp in C.CHECKPOINTS},
            "decision_lag_s_after_C_plus_60": {cp: desc([r["lag"] for r in ok if r["cp"] == cp]) for cp in C.CHECKPOINTS},
            "main_strike_quote_age_s": {cp: desc([r["age_s"] for r in ok if r["cp"] == cp]) for cp in C.CHECKPOINTS}}


def bootstrap_lb(nets_by_game, draws=10_000, seed=C.SEED):
    """95% bootstrap lower bound of the mean net, resampling games. None when there are no bets."""
    gl = [np.array(v) for v in nets_by_game.values() if len(v)]
    if not gl:
        return None
    rng = np.random.default_rng(seed)
    boots = [np.concatenate([gl[i] for i in rng.integers(0, len(gl), len(gl))]).mean() for _ in range(draws)]
    return float(np.quantile(boots, 0.025))
