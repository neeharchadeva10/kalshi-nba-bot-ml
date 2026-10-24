"""Backtest for research log #20 (reports/output/nfl_margin_hist/PREREGISTRATION.md): NFL win-margin buckets vs the
fair value implied by the deep winner / tie / spread markets, at one-minute resolution.

Everything is decided from minute-grid closes. A signal at minute t (close values) is entered at minute t+1 only if the
t+1 close still offers the signal price or better, at the t+1 price. Prices inside are floats in dollars rounded to
1/10000. The fee function, validity rule and the Family A fair-value mapping (bucket parsing, spread-strike lookup, tie
adjustment) come from kpm.analysis.ws_sim.

Run:  python -m kpm.analysis.margin_hist        (reads data/nfl_margin.sqlite, writes reports/output/nfl_margin_hist/)
Public-data research only: this module never touches order, portfolio or API-key code.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from kpm.analysis.ws_sim import BUCKET_RE, EPS, MAX_COUNT, Universe, fee1, taker_fee, valid_mid

DB_PATH = Path("data/nfl_margin.sqlite")
OUT_DIR = Path("reports/output/nfl_margin_hist")
PRE_S, POST_S, STEP = 6 * 3600, 4 * 3600 + 1800, 60
SPLIT_DATE = "2026-10-01"                 # discovery = event date before this, unseen = on or after
MIN_UNSEEN_GAMES = 25
SEED = 20261020
BOOT_DRAWS, COIN_COPIES = 10_000, 500
GAMES_PER_WEEK = 15
DOLLARS_PER_ORDER = 1.0
EXIT_SLACK = 0.01
MARKOUTS = (1, 5, 15)
PRIMARY = "T-th2-X2"
MAKER_WINDOW_S = 60
NAN = float("nan")


# ------------------------------------------------------------------------------------------------ versions
@dataclass(frozen=True)
class Version:
    vid: str
    kind: str          # T taker | M maker
    theta: float = 0.0
    exit: str = "X2"
    delta: float = 0.0


def all_versions() -> list[Version]:
    out = [Version(f"T-th{th}-{ex}", "T", theta=th / 100, exit=ex) for th in (1, 2, 3) for ex in ("X1", "X2")]
    out += [Version(f"M-d{d}", "M", exit="X2", delta=d / 100) for d in (1, 2)]
    return out


# ------------------------------------------------------------------------------------------------ fees
@lru_cache(maxsize=None)
def fee_unit(price: float) -> float:
    return fee1(round(price, 4))


def maker_fee(price: float, count: int, fee_type: str | None = "quadratic") -> float:
    """Quadratic series (no maker fee): 0. Anything else falls back to the project's maker_fraction of the taker fee."""
    if fee_type == "quadratic":
        return 0.0
    return round(0.25 * taker_fee(price, count), 4)


def order_count(price: float) -> int:
    return max(1, min(MAX_COUNT, int(math.floor(DOLLARS_PER_ORDER / price + EPS))))


# ------------------------------------------------------------------------------------------------ game data
@dataclass
class Game:
    name: str                                   # e.g. 26OCT04DALHOU
    kickoff: int
    event_date: str
    grid: np.ndarray                            # (T,) int64 minute-end timestamps
    bid: dict[str, np.ndarray]                  # ticker -> (T,) float, NaN = empty side or unknown
    ask: dict[str, np.ndarray]
    known: dict[str, np.ndarray]                # ticker -> (T,) bool: a quote row exists (carried) at that minute
    result: dict[str, str]                      # ticker -> 'yes' | 'no'
    trades: dict[str, tuple] = field(default_factory=dict)      # ticker -> (ts, yes_price, count) sorted arrays
    fee_type: str = "quadratic"
    universe: Universe | None = None
    period: str = ""

    def __post_init__(self):
        if self.universe is None:
            self.universe = build_universe(self.name, list(self.bid), {})


def build_universe(game: str, tickers: list[str], strikes: dict) -> Universe:
    return Universe([{"games": {game: {"tickers": tickers, "kickoff": None, "meta": strikes}}}], check_titles=False)


def carry_forward(grid_start: int, T: int, rows: list[tuple], end_ts: float | None) -> tuple:
    """rows = [(ts, bid, ask)] with bid/ask in dollars or None. Candles exist only for minutes where something changed,
    so each market's quote ROW (bid and ask together, None = empty) is carried forward from its last candle until its end
    (nothing at or after end_ts). Minutes before the first candle are unknown. Bid > ask snapshots are unknown.
    A candle ending exactly at the grid start is the pre-window state: it seeds minute 0 unless minute 0 has its own candle.
    Returns bid, ask, known arrays and the number of off-grid candles dropped."""
    bid, ask = np.full(T, NAN), np.full(T, NAN)
    has = np.zeros(T, dtype=bool)
    off = 0
    for ts, b, a in sorted(rows, key=lambda r: r[0]):
        k, rem = divmod(int(ts) - grid_start, STEP)
        if rem != 0 or not 0 <= k <= T:
            off += 1
            continue
        i = max(k - 1, 0)                                   # k == 0: the candle ending at the window start is the baseline
                                                  # grid[i] = grid_start + 60 * (i + 1)
        has[i] = True
        bid[i] = NAN if b is None else b
        ask[i] = NAN if a is None else a
    last = np.maximum.accumulate(np.where(has, np.arange(T), -1))
    known = last >= 0
    lc = np.maximum(last, 0)
    bid, ask = np.where(known, bid[lc], NAN), np.where(known, ask[lc], NAN)
    if end_ts is not None and np.isfinite(end_ts):
        after = (grid_start + STEP * (np.arange(T) + 1)) >= end_ts
        known = known & ~after
        bid[after], ask[after] = NAN, NAN
    crossed = known & (bid > ask + EPS)                     # NaN compares False
    known = known & ~crossed
    bid[crossed], ask[crossed] = NAN, NAN
    return bid, ask, known, off


# ------------------------------------------------------------------------------------------------ fair values
def mid_series(g: Game, ticker: str) -> np.ndarray:
    """ws_sim.valid_mid applied minute by minute (NaN = unusable input)."""
    out = np.full(len(g.grid), NAN)
    b, a, kn = g.bid.get(ticker), g.ask.get(ticker), g.known.get(ticker)
    if b is None:
        return out
    for i in np.flatnonzero(kn):
        m = valid_mid(None if np.isnan(b[i]) else float(b[i]), None if np.isnan(a[i]) else float(a[i]))
        if m is not None:
            out[i] = m
    return out


def p_over(leg: tuple, mids: dict) -> np.ndarray:
    """P(team wins by more than the threshold) for a ws_sim leg; winner leg = win mid - tie mid / 2."""
    if leg[0] == "sp":
        return mids[leg[1]]
    return mids[leg[1]] - mids[leg[2]] / 2


def fair_series(g: Game, tg, mids: dict) -> np.ndarray:
    """Family A fair = P(over lo) - P(over hi); NaN when an input is invalid or the pair is inconsistent (outside [0,1]
    before clamping); clamped to [0, 1] otherwise."""
    lo = p_over(tg.legs[0], mids)
    hi = np.zeros(len(g.grid)) if tg.legs[1] is None else p_over(tg.legs[1], mids)
    f = lo - hi
    with np.errstate(invalid="ignore"):
        bad = ~((f >= -EPS) & (f <= 1 + EPS))
    f = np.clip(f, 0.0, 1.0)
    f[bad] = NAN
    return f


def bucket_type(ticker: str) -> str:
    m = BUCKET_RE.fullmatch(ticker)
    if not m:
        return "?"
    return "15+" if m["plus"] else f"{int(m['lo'])}-{int(m['hi'])}"


# ------------------------------------------------------------------------------------------------ one side of one target
class Side:
    """Arrays in the position's own side space: price = what buying costs, own_bid = what selling would get."""

    def __init__(self, g: Game, tg, fair: np.ndarray, side: str):
        t = tg.ticker
        b, a = g.bid[t], g.ask[t]
        self.g, self.ticker, self.side, self.bucket = g, t, side, bucket_type(t)
        if side == "yes":
            self.price, self.own_bid, self.fair = a.copy(), b.copy(), fair.copy()
        else:
            self.price, self.own_bid, self.fair = np.round(1 - b, 4), np.round(1 - a, 4), 1 - fair
        self.mid = (self.price + self.own_bid) / 2
        self.T = len(g.grid)
        self.won = g.result[t] == side
        ev = np.full(self.T, NAN)
        for i in np.flatnonzero(np.isfinite(self.price) & np.isfinite(self.fair)):
            ev[i] = self.fair[i] - self.price[i] - fee_unit(float(self.price[i]))
        self.ev = ev
        self._nxt: np.ndarray | None = None

    @property
    def nxt(self) -> np.ndarray:
        """nxt[t] = first t' >= t whose X2 exit would be sold at t'+1 (trigger at t', same-or-better bid at t'+1)."""
        if self._nxt is None:
            T, ok = self.T, np.zeros(self.T, dtype=bool)
            for i in np.flatnonzero(np.isfinite(self.own_bid) & np.isfinite(self.fair))[:]:
                if i + 1 >= T or not np.isfinite(self.own_bid[i + 1]):
                    continue
                pb = float(self.own_bid[i])
                if (pb - fee_unit(pb) >= self.fair[i] - EXIT_SLACK - EPS and self.own_bid[i + 1] >= pb - EPS):
                    ok[i] = True
            nxt = np.full(T + 1, T, dtype=np.int64)
            for i in range(T - 1, -1, -1):
                nxt[i] = i if ok[i] else nxt[i + 1]
            self._nxt = nxt
        return self._nxt

    def first_exit(self, e: int) -> int | None:
        """X2: index of the first sell (t'+1) for a position entered at minute e (t' starts at e+1), else None."""
        x = int(self.nxt[min(e + 1, self.T)])
        return None if x >= self.T else x + 1


def make_position(s: Side, kind: str, sig_i: int, e: int, price: float, count: int, fee_in: float, exit_mode: str,
                  version: str, ev: float | None, fair: float | None, maker_ts: float | None = None) -> dict:
    """Settle or X2-exit a position entered at minute index e. Returns the trade record and its busy-until index."""
    g = s.g
    sell = s.first_exit(e) if exit_mode == "X2" else None
    if sell is not None:
        px = float(s.own_bid[sell])
        fee_out = taker_fee(px, count)
        proceeds, exit_kind, exit_ts = count * px - fee_out, "x2", int(g.grid[sell])
    else:
        px, fee_out, exit_kind, exit_ts = (1.0 if s.won else 0.0), 0.0, "settle", None
        proceeds = count * px
    pnl = round(proceeds - count * price - fee_in, 6)
    rec = {"version": version, "game": g.name, "period": g.period, "ticker": s.ticker, "bucket": s.bucket, "side": s.side,
           "signal_ts": int(g.grid[sig_i]), "entry_ts": int(g.grid[e]), "price": price, "count": count,
           "fee_in": fee_in, "exit_kind": exit_kind, "exit_ts": exit_ts, "exit_px": px, "fee_out": fee_out, "pnl": pnl,
           "ev": ev, "fair": fair, "phase": "pregame" if g.grid[e] <= g.kickoff else "ingame", "kind": kind,
           "sell_idx": sell, "entry_idx": e}
    for h in MARKOUTS:
        j = e + h
        ok = j < s.T and np.isfinite(s.mid[j])
        rec[f"mo{h}"] = round(float(s.mid[j]) - price, 6) if ok else None
    return rec


def busy_until(rec: dict) -> int:
    return rec["sell_idx"] if rec["exit_kind"] == "x2" else 10**9


def entry_fill_check(g: Game, s: Side, e: int, price: float) -> bool:
    """A public print at the entry price or better inside the entry minute (grid[e]-60, grid[e]]."""
    tr = g.trades.get(s.ticker)
    if tr is None or len(tr[0]) == 0:
        return False
    ts, px = tr[0], tr[1]
    hi = float(g.grid[e])
    lo = hi - STEP
    m = (ts > lo) & (ts <= hi)
    if not m.any():
        return False
    return bool((px[m] <= price + EPS).any()) if s.side == "yes" else bool((px[m] >= 1 - price - EPS).any())


def taker_entry(s: Side, i: int) -> tuple[float, int] | None:
    """Signal at minute i; fill at i+1 at the i+1 price if it is still the signal price or better."""
    if i + 1 >= s.T:
        return None
    p0, p1 = s.price[i], s.price[i + 1]
    if not np.isfinite(p1) or p1 > p0 + EPS:
        return None
    return float(p1), order_count(float(p1))


def run_taker_side(s: Side, v: Version) -> list[dict]:
    out: list[dict] = []
    busy = -1
    with np.errstate(invalid="ignore"):
        cand = np.flatnonzero(s.ev >= v.theta - EPS)
    for i in cand:
        i = int(i)
        if i <= busy:
            continue
        fill = taker_entry(s, i)
        if fill is None:
            continue
        price, count = fill
        rec = make_position(s, "T", i, i + 1, price, count, taker_fee(price, count), v.exit, v.vid,
                            float(s.ev[i]), float(s.fair[i]))
        rec["fill_check"] = entry_fill_check(s.g, s, i + 1, price)
        out.append(rec)
        busy = busy_until(rec)
    return out


def run_maker_side(s: Side, v: Version) -> list[dict]:
    """YES bids only. Rest at floor((fair-delta)*100)/100 when strictly inside the spread; filled by public prints
    strictly below our price in the next 60 s (fill count = min(size, whole contracts printed below us))."""
    if s.side != "yes":
        return []
    g, out, busy = s.g, [], -1
    tr = g.trades.get(s.ticker)
    if tr is None or len(tr[0]) == 0:
        return out
    ts_all, px_all, ct_all = tr
    for i in range(s.T - 1):
        if i <= busy or not (np.isfinite(s.fair[i]) and np.isfinite(s.own_bid[i]) and np.isfinite(s.price[i])):
            continue
        q = math.floor((float(s.fair[i]) - v.delta) * 100 + EPS) / 100
        if not (s.own_bid[i] + EPS < q < s.price[i] - EPS):
            continue
        t0 = float(g.grid[i])
        m = (ts_all > t0) & (ts_all <= t0 + MAKER_WINDOW_S) & (px_all < q - EPS)
        if not m.any():
            continue
        size = order_count(q)
        n = min(size, int(math.floor(float(ct_all[m].sum()) + EPS)))
        if n < 1:
            continue
        rec = make_position(s, "M", i, i + 1, q, n, maker_fee(q, n, g.fee_type), v.exit, v.vid, None,
                            float(s.fair[i]))
        rec["fill_check"] = None
        out.append(rec)
        busy = busy_until(rec)
    return out


# ------------------------------------------------------------------------------------------------ game level
def build_sides(g: Game) -> list[Side]:
    needed = {x for tg in g.universe.targets.values() for lg in tg.legs if lg for x in lg[1:] if x}
    mids = {t: mid_series(g, t) for t in needed}
    sides = []
    for tg in g.universe.targets.values():
        if tg.ticker not in g.bid or tg.ticker not in g.result:
            continue
        f = fair_series(g, tg, mids)
        sides += [Side(g, tg, f, "yes"), Side(g, tg, f, "no")]
    return sides


def run_game(g: Game, versions: list[Version], sides: list[Side] | None = None) -> list[dict]:
    sides = sides if sides is not None else build_sides(g)
    out = []
    for v in versions:
        for s in sides:
            out += run_taker_side(s, v) if v.kind == "T" else run_maker_side(s, v)
    return out


# ------------------------------------------------------------------------------------------------ statistics
def _bootstrap(game_pnl: np.ndarray, game_cnt: np.ndarray, seed: int = SEED, draws: int = BOOT_DRAWS) -> tuple:
    """95% percentile CI of net-per-contract (cents) resampling whole games. Deterministic."""
    G = len(game_pnl)
    if G == 0 or game_cnt.sum() == 0:
        return None, None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, G, size=(draws, G))
    c = game_cnt[idx].sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        m = np.where(c > 0, game_pnl[idx].sum(axis=1) / c, np.nan) * 100
    m = m[np.isfinite(m)]
    return (float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))) if len(m) else (None, None)


def summarize(trades: pd.DataFrame, game_names: list[str]) -> dict:
    n_games = len(game_names)
    out = {"games": n_games, "trades": int(len(trades))}
    if len(trades) == 0:
        out.update(contracts=0, net_per_contract_c=None, total_usd=0.0, usd_per_week=0.0, boot_lo_c=None, boot_hi_c=None,
                   fill_check_share=None)
        for h in MARKOUTS:
            out[f"markout_{h}m_c"] = None
        return out
    cnt, pnl = int(trades["count"].sum()), float(trades["pnl"].sum())
    by = trades.groupby("game")[["pnl", "count"]].sum()
    gp = np.array([by["pnl"].get(n, 0.0) for n in game_names])
    gc = np.array([by["count"].get(n, 0) for n in game_names], dtype=float)
    lo, hi = _bootstrap(gp, gc)
    fc = trades["fill_check"].dropna()
    out.update(contracts=cnt, net_per_contract_c=pnl / cnt * 100, total_usd=pnl,
               usd_per_week=pnl / max(n_games, 1) * GAMES_PER_WEEK, boot_lo_c=lo, boot_hi_c=hi,
               fill_check_share=float(fc.astype(bool).mean()) if len(fc) else None,
               win_rate=float((trades["pnl"] > 0).mean()),
               share_x2=float((trades["exit_kind"] == "x2").mean()))
    for h in MARKOUTS:
        col = trades[f"mo{h}"].dropna()
        out[f"markout_{h}m_c"] = float(col.mean() * 100) if len(col) else None
        out[f"markout_{h}m_n"] = int(len(col))
    return out


def breakdown(trades: pd.DataFrame, col: str) -> dict:
    res = {}
    for key, d in trades.groupby(col):
        c = int(d["count"].sum())
        res[str(key)] = {"trades": int(len(d)), "contracts": c, "net_per_contract_c": float(d["pnl"].sum() / c * 100),
                         "total_usd": float(d["pnl"].sum())}
    return res


# ------------------------------------------------------------------------------------------------ coin flip
def coin_flip(games: list[Game], sides_by_game: dict[str, list[Side]], real: pd.DataFrame, v: Version,
              real_mean_c: float, copies: int = COIN_COPIES, seed: int = SEED) -> dict:
    """500 random copies of the strategy on the given games: same markets, sides and entries per game as the real run,
    each entry's signal minute drawn uniformly over the grid; same fill (price must survive to t+1), sizing and exit
    rules. One position per market and side. Returns the share of copies whose net per contract >= the real one."""
    rng = np.random.default_rng(seed)
    lookup = {(s.g.name, s.ticker, s.side): s for ss in sides_by_game.values() for s in ss}
    plan = [(k, n) for k, n in Counter(zip(real["game"], real["ticker"], real["side"])).items()]
    plan.sort()
    means = []
    for _ in range(copies):
        pnl = cnt = 0.0
        for (gm, tk, sd), n in plan:
            s = lookup[(gm, tk, sd)]
            busy = -1
            for i in sorted(rng.integers(0, s.T, size=n).tolist()):
                if i <= busy:
                    continue
                fill = taker_entry(s, i)
                if fill is None:
                    continue
                price, count = fill
                rec = make_position(s, "T", i, i + 1, price, count, taker_fee(price, count), v.exit, "coin", None, None)
                pnl += rec["pnl"]
                cnt += count
                busy = busy_until(rec)
        means.append(pnl / cnt * 100 if cnt else float("-inf"))
    means_a = np.array(means)
    return {"copies": copies, "real_c": real_mean_c, "share_as_good": float((means_a >= real_mean_c - 1e-12).mean()),
            "copy_mean_c": float(np.mean(means_a[np.isfinite(means_a)])) if np.isfinite(means_a).any() else None,
            "copy_p95_c": float(np.percentile(means_a[np.isfinite(means_a)], 95)) if np.isfinite(means_a).any() else None}


# ------------------------------------------------------------------------------------------------ loading
def _iso(ts) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_games(db_path: Path) -> tuple[list[Game], dict]:
    conn = sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    mk = pd.read_sql_query("SELECT * FROM mm_markets", conn)
    fee_types = json.loads((conn.execute("SELECT value FROM mm_meta WHERE key='fee_types'").fetchone() or ["{}"])[0])
    cov = {"games_in_db": int(mk["game"].nunique()), "dropped": {}, "offgrid_candles": 0, "inputs_skipped": Counter()}
    games: list[Game] = []
    for name, d in mk.groupby("game"):
        margin = d[d["series"] == "KXNFLWINMARGIN"]
        ko = d["kickoff_ts"].dropna()
        if len(ko) == 0:
            cov["dropped"][name] = "no kickoff (no KXNFLGAME market)"
            continue
        if len(margin) != 7 or not margin["result"].isin(["yes", "no"]).all():
            cov["dropped"][name] = f"unsettled or missing win-margin bucket ({len(margin)} markets, results " \
                                   f"{sorted(map(str, margin['result'].unique()))})"
            continue
        kickoff = int(ko.iloc[0])
        start = kickoff - PRE_S
        T = (PRE_S + POST_S) // STEP
        grid = start + STEP * (np.arange(T) + 1)
        bid, ask, known, strikes = {}, {}, {}, {}
        q = pd.read_sql_query("SELECT mid, ts, bid_e4, ask_e4 FROM mm_quotes WHERE mid IN (%s)" %
                              ",".join(str(int(x)) for x in d["mid"]), conn)
        qg = {m: grp for m, grp in q.groupby("mid")}
        for r in d.itertuples():
            grp = qg.get(r.mid)
            rows = [] if grp is None else [(int(t), None if pd.isna(b) else b / 10000, None if pd.isna(a) else a / 10000)
                                           for t, b, a in zip(grp["ts"], grp["bid_e4"], grp["ask_e4"])]
            end = min(x for x in (r.close_ts, r.q_end_ts) if pd.notna(x)) if (pd.notna(r.close_ts) or pd.notna(r.q_end_ts)) else None
            b, a, kn, off = carry_forward(start, T, rows, end)
            bid[r.ticker], ask[r.ticker], known[r.ticker] = b, a, kn
            cov["offgrid_candles"] += off
            if r.series == "KXNFLSPREAD" and pd.notna(r.floor_strike):
                strikes[r.ticker] = float(r.floor_strike)
        results = {r.ticker: r.result for r in margin.itertuples()}
        tr = {}
        tq = pd.read_sql_query("SELECT ticker, ts, yes_price, count FROM mm_trades WHERE ticker IN (%s) ORDER BY ts" %
                               ",".join("'" + t + "'" for t in margin["ticker"]), conn)
        for t, grp in tq.groupby("ticker"):
            tr[t] = (grp["ts"].to_numpy(float), grp["yes_price"].to_numpy(float), grp["count"].to_numpy(float))
        uni = build_universe(name, list(bid), {t: {"floor_strike": s} for t, s in strikes.items()})
        cov["inputs_skipped"].update(uni.skipped)
        if not uni.targets:
            cov["dropped"][name] = "no bucket with listed inputs"
            continue
        games.append(Game(name, kickoff, str(d["event_date"].iloc[0]), grid, bid, ask, known, results, tr,
                          fee_types.get("KXNFLWINMARGIN") or "quadratic", uni,
                          "discovery" if str(d["event_date"].iloc[0]) < SPLIT_DATE else "unseen"))
    games.sort(key=lambda g: g.kickoff)
    cov["inputs_skipped"] = dict(cov["inputs_skipped"])
    cov["fee_types"] = fee_types
    conn.close()
    return games, cov


def game_coverage(games: list[Game]) -> dict:
    out = {}
    for period in ("discovery", "unseen"):
        gs = [g for g in games if g.period == period]
        cands = defaultdict(list)
        for g in gs:
            for t in g.bid:
                cands[t.split("-")[0]].append(float(g.known[t].mean()))
        out[period] = {"games": len(gs), "first": _iso(gs[0].kickoff) if gs else None,
                       "last": _iso(gs[-1].kickoff) if gs else None,
                       "markets": sum(len(g.bid) for g in gs),
                       "targets": sum(len(g.universe.targets) for g in gs),
                       "mean_known_share_by_series": {k: float(np.mean(v)) for k, v in cands.items()}}
    return out


# ------------------------------------------------------------------------------------------------ main
def verdict(res: dict, coin: dict | None, n_unseen_games: int) -> dict:
    """Pass bar for the primary version (all lines must hold). Lines 5 and 6 are checked in discovery AND unseen."""
    d, u = res["discovery"], res["unseen"]
    if n_unseen_games < MIN_UNSEEN_GAMES:
        return {"run": False, "reason": f"only {n_unseen_games} unseen games (< {MIN_UNSEEN_GAMES})", "pass": False}
    lines = {}
    lines["1 mean net/contract > 0 in discovery and unseen"] = bool(
        (d["net_per_contract_c"] or 0) > 0 and (u["net_per_contract_c"] or 0) > 0)
    lines["2 unseen bootstrap lower bound > 0"] = bool((u.get("boot_lo_c") or 0) > 0)
    lines["3 at least 100 unseen trades"] = u["trades"] >= 100
    lines["4 coin flip: <= 5% of copies as good on unseen"] = bool(coin is not None and coin["share_as_good"] <= 0.05)
    lines["5 >= $2/week at real size (discovery and unseen)"] = bool(d["usd_per_week"] >= 2 and u["usd_per_week"] >= 2)
    lines["6 >= 50% of entries with a print at the entry price within the minute (discovery and unseen)"] = bool(
        (d.get("fill_check_share") or 0) >= 0.5 and (u.get("fill_check_share") or 0) >= 0.5)
    return {"run": True, "lines": lines, "pass": all(lines.values())}


def main(db_path: Path = DB_PATH, out_dir: Path = OUT_DIR) -> dict:
    t0 = time.time()
    games, cov = load_games(db_path)
    versions = all_versions()
    out_dir.mkdir(parents=True, exist_ok=True)
    sides_by_game = {g.name: build_sides(g) for g in games}
    trades = []
    for g in games:
        trades += run_game(g, versions, sides_by_game[g.name])
    df = pd.DataFrame(trades)
    names = {p: [g.name for g in games if g.period == p] for p in ("discovery", "unseen")}
    n_unseen = len(names["unseen"])
    table, rows = {}, []
    for v in versions:
        table[v.vid] = {}
        for p in ("discovery", "unseen"):
            sub = df[(df["version"] == v.vid) & (df["period"] == p)] if len(df) else df
            r = summarize(sub, names[p])
            if v.kind == "M":
                r["fill_check_share"] = None
            table[v.vid][p] = r
            rows.append({"version": v.vid, "period": p, **r})
    coin = None
    if n_unseen >= MIN_UNSEEN_GAMES and len(df):
        prim = df[(df["version"] == PRIMARY) & (df["period"] == "unseen")]
        pv = next(v for v in versions if v.vid == PRIMARY)
        if len(prim):
            ug = [g for g in games if g.period == "unseen"]
            coin = coin_flip(ug, {g.name: sides_by_game[g.name] for g in ug}, prim, pv,
                             table[PRIMARY]["unseen"]["net_per_contract_c"])
    detail = {}
    if len(df):
        prim_df = df[df["version"] == PRIMARY]
        for p in ("discovery", "unseen"):
            sp = prim_df[prim_df["period"] == p]
            detail[p] = {"by_phase": breakdown(sp, "phase") if len(sp) else {},
                         "by_bucket": breakdown(sp, "bucket") if len(sp) else {},
                         "by_side": breakdown(sp, "side") if len(sp) else {}}
    summary = {"generated": _iso(time.time()), "runtime_s": round(time.time() - t0, 1), "primary": PRIMARY,
               "coverage": cov, "games": game_coverage(games), "unseen_games": n_unseen,
               "versions": table, "primary_detail": detail, "coin_flip_primary_unseen": coin,
               "verdict": verdict(table[PRIMARY], coin, n_unseen)}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    if len(df):
        df.drop(columns=["sell_idx", "entry_idx"]).to_csv(out_dir / "trades.csv", index=False)
    pd.DataFrame(rows).to_csv(out_dir / "versions.csv", index=False)
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--out", default=str(OUT_DIR))
    a = ap.parse_args()
    s = main(Path(a.db), Path(a.out))
    print(json.dumps({k: s[k] for k in ("runtime_s", "unseen_games", "verdict")}, indent=2))
