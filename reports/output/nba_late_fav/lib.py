"""Pure functions for research log #35 (buy the NBA favourite late in Q4).  See PREREGISTRATION.md.

Conventions: all times are UTC epoch seconds; prices are dollars on the 0.01 grid.
"""
from __future__ import annotations

import bisect
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
PBP_DIR = HERE.parents[2] / "data" / "raw" / "nba_open"
SEED = 20261021
COUNT = 10
EPS = 1e-9
XS = (540, 360, 180, 90)                          # seconds left in Q4: 9:00, 6:00, 3:00, 1:30 (Amendment 2026-10-22)
BANDS = {"60-80": (0.60, 0.80), "70-92": (0.70, 0.92), "80-95": (0.80, 0.95), "90-97": (0.90, 0.97)}
PRIMARY = (360, "70-92")                          # the ORIGINAL primary; still reported (the selected version replaces it)
SLIP = 0.01                                       # +1c slippage on every entry (Amendment 2026-10-22)
MIN_DISC_TRADES = 100                             # a version needs >= 100 discovery trades to be eligible for selection
SEED2 = 20261022                                  # discovery luck test
LUCK_REPS = 500
FILL_WINDOW_S = 60
LATENCY_S = 60                                    # E_X = first whole-minute boundary >= t_X + 60 s
MAX_QUOTE_AGE_S = 600                             # carried-forward quote must come from a candle ending within 10 min
WC_MIN_H, WC_MAX_H, WC_MAX_BACKSTEP_S = 1.25, 4.0, 300

# ESPN (hoopR) abbreviation -> Kalshi ticker code
ESPN_TO_KALSHI = {
    "ATL": "ATL", "BKN": "BKN", "BOS": "BOS", "CHA": "CHA", "CHI": "CHI", "CLE": "CLE", "DAL": "DAL", "DEN": "DEN",
    "DET": "DET", "GS": "GSW", "HOU": "HOU", "IND": "IND", "LAC": "LAC", "LAL": "LAL", "MEM": "MEM", "MIA": "MIA",
    "MIL": "MIL", "MIN": "MIN", "NO": "NOP", "NY": "NYK", "OKC": "OKC", "ORL": "ORL", "PHI": "PHI", "PHX": "PHX",
    "POR": "POR", "SA": "SAS", "SAC": "SAC", "TOR": "TOR", "UTAH": "UTA", "WSH": "WAS",
}
MONTHS = {m: i + 1 for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
EVENT_RE = re.compile(r"^KXNBAGAME-(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})$")


# ---------------------------------------------------------------------------------------------------- fee
def fee_e4(price: float, count: int = COUNT) -> int:
    """Taker fee of ONE order in 1/10000 dollars: ceil(0.07 * C * P * (1-P) / 0.0001), exact integer maths on the cent price.
    KXNBAGAME has fee_type quadratic_with_maker_fees, fee_multiplier 1: takers pay the 0.07 formula."""
    pc = round(price * 100)
    num = 7 * count * pc * (100 - pc)             # fee in dollars = num / 1e6
    return -(-num // 100)                         # ceil(num / 1e6 / 1e-4)


def fee(price: float, count: int = COUNT) -> float:
    return fee_e4(price, count) / 10_000


def entry_price(ask: float, slip: float = SLIP) -> float:
    """Price actually paid: displayed ask + 1c slippage (cent grid)."""
    return round(ask + slip, 2)


def net_per_contract(price: float, win: bool, count: int = COUNT) -> float:
    """1(win) - price - fee/count, where price is the price PAID (slippage already added; fee is on the paid price)."""
    return (1.0 if win else 0.0) - price - fee(price, count) / count


# ---------------------------------------------------------------------------------------------------- tickers
def parse_event_ticker(et: str):
    """KXNBAGAME-26JUN13NYKSAS -> (date(2026,6,13), 'NYK' away, 'SAS' home) or None."""
    m = EVENT_RE.match(et)
    if not m or m.group(2) not in MONTHS:
        return None
    return date(2000 + int(m.group(1)), MONTHS[m.group(2)], int(m.group(3))), m.group(4), m.group(5)


def team_of(ticker: str) -> str:
    return ticker.rsplit("-", 1)[1]


# ---------------------------------------------------------------------------------------------------- pbp
@dataclass
class PbpGame:
    game_id: int
    home: str                       # ESPN abbreviations
    away: str
    date: str                       # game_date (US Eastern)
    season_type: int
    t: dict = field(default_factory=dict)          # X seconds -> wallclock epoch of the decision event (or None)
    margin: dict = field(default_factory=dict)     # X -> home_score - away_score at that event
    max_period: int = 0
    first_wall: float = 0.0
    last_wall: float = 0.0
    wall_sorted: np.ndarray = None                 # event wallclocks sorted ascending
    period_sorted: np.ndarray = None               # period of each, same order
    max_backstep_q4: dict = field(default_factory=dict)   # X -> largest wallclock backstep (s) in Q4 events up to t_X
    n_wall_backsteps: int = 0                      # events whose wallclock is earlier than the previous one (data quality)


def entry_event_time(period, clock_s, wall, X):
    """t_X = wallclock of the FIRST event (sequence order) with period == 4 and game clock <= X seconds (X:00 itself counts).
    Inputs are parallel arrays in sequence order.  Returns (index, wall) or (None, None)."""
    period = np.asarray(period)
    clock_s = np.asarray(clock_s, dtype=float)
    ok = (period == 4) & (clock_s <= X + 1e-9)
    if not ok.any():
        return None, None
    i = int(np.argmax(ok))
    return i, float(np.asarray(wall)[i])


def load_pbp(pbp_dir: Path = PBP_DIR) -> dict[int, PbpGame]:
    cols = ["game_id", "game_play_number", "sequence_number", "period", "clock_minutes", "clock_seconds", "wallclock",
            "home_team_abbrev", "away_team_abbrev", "game_date", "season_type", "home_score", "away_score"]
    parts = [pd.read_parquet(pbp_dir / f"play_by_play_{y}.parquet", columns=cols) for y in (2025, 2026)]
    d = pd.concat(parts, ignore_index=True).drop_duplicates(["game_id", "game_play_number", "sequence_number"])
    d["wall"] = pd.to_datetime(d["wallclock"], utc=True).dt.as_unit("s").astype("int64")   # explicit unit: pandas 3 parses to us/s
    d["clock_s"] = d["clock_minutes"].astype(float) * 60 + d["clock_seconds"].astype(float)
    d = d.sort_values(["game_id", "game_play_number", "sequence_number"], kind="stable")
    return {int(gid): build_game(int(gid), g) for gid, g in d.groupby("game_id", sort=False)}


def build_game(gid, g: pd.DataFrame) -> PbpGame:
    pg = PbpGame(gid, str(g["home_team_abbrev"].iloc[0]), str(g["away_team_abbrev"].iloc[0]), str(g["game_date"].iloc[0]),
                 int(g["season_type"].iloc[0]))
    per, cs, wall = g["period"].to_numpy(), g["clock_s"].to_numpy(), g["wall"].to_numpy()
    marg = (g["home_score"] - g["away_score"]).to_numpy()
    for X in XS:
        i, w = entry_event_time(per, cs, wall, X)
        pg.t[X] = w
        pg.margin[X] = int(marg[i]) if i is not None else None
        if i is not None:
            q4 = np.where(per[:i + 1] == 4)[0]
            w4 = wall[q4]
            pg.max_backstep_q4[X] = float(max(0.0, (np.maximum.accumulate(w4) - w4).max())) if len(w4) else 0.0
    pg.max_period = int(per.max())
    pg.first_wall, pg.last_wall = float(wall.min()), float(wall.max())
    o = np.argsort(wall, kind="stable")
    pg.wall_sorted, pg.period_sorted = wall[o].astype(float), per[o]
    pg.n_wall_backsteps = int((np.diff(wall) < 0).sum())
    return pg


def period_at(pg: PbpGame, t: float):
    """Period of the last pbp event with wallclock <= t (None before the first event)."""
    i = int(np.searchsorted(pg.wall_sorted, t, side="right")) - 1
    return None if i < 0 else int(pg.period_sorted[i])


# ---------------------------------------------------------------------------------------------------- matching
def match_games(events: dict[str, tuple], pbp: dict[int, PbpGame]):
    """events: event_ticker -> (date, away_k, home_k).  Returns (matches {event_ticker: game_id}, unmatched list, shifted list).
    Exact (ET date, away, home) first; only if that has no free game, the day before / after (reported as 'shifted')."""
    k_index: dict[tuple, list[int]] = {}
    for gid, g in pbp.items():
        h, a = ESPN_TO_KALSHI.get(g.home), ESPN_TO_KALSHI.get(g.away)
        if h is None or a is None:
            continue
        k_index.setdefault((g.date, a, h), []).append(gid)
    used, matches, unmatched, shifted = set(), {}, [], []
    for et, (d, a, h) in sorted(events.items(), key=lambda kv: kv[1][0]):
        gid = None
        for off in (0, -1, 1):
            c = [x for x in k_index.get(((d + timedelta(days=off)).isoformat(), a, h), []) if x not in used]
            if c:
                gid = c[0]
                if off:
                    shifted.append(et)
                break
        if gid is None:
            unmatched.append(et)
        else:
            used.add(gid)
            matches[et] = gid
    return matches, unmatched, shifted


# ---------------------------------------------------------------------------------------------------- candles / decision
@dataclass
class Series:
    ts: np.ndarray
    bid: np.ndarray                 # nan where missing
    ask: np.ndarray
    vol: np.ndarray

    def latest(self, E: int, max_age: float = MAX_QUOTE_AGE_S):
        """(end_ts, bid, ask) of the latest candle with end_period_ts <= E, only if it ended within max_age seconds; else None."""
        i = int(np.searchsorted(self.ts, E, side="right")) - 1
        if i < 0 or E - self.ts[i] > max_age:
            return None
        return int(self.ts[i]), float(self.bid[i]), float(self.ask[i])


def decision_minute(t_x: float, latency: float = LATENCY_S) -> int:
    """E_X = first whole-minute boundary at or after t_x + latency.  Never earlier than t_x + latency."""
    return int(-(-(t_x + latency - 1e-9) // 60) * 60)


def wallclock_ok(pg: "PbpGame", X: int, tip: float):
    """Game valid only if t_X in [tip + 1.25 h, tip + 4 h] and no Q4 wallclock backstep > 5 min before t_X."""
    t = pg.t.get(X)
    if t is None:
        return False
    return (tip + WC_MIN_H * 3600 <= t <= tip + WC_MAX_H * 3600) and pg.max_backstep_q4.get(X, 0.0) <= WC_MAX_BACKSTEP_S


def pick_favorite(asks: dict, lo: float, hi: float):
    """asks: team -> yes ask close at E_X (nan/None = no quote).  Exactly one team inside [lo, hi] -> (team, 'ok')."""
    inb = [t for t, a in asks.items() if a is not None and not math.isnan(a) and lo - EPS <= a <= hi + EPS]
    if len(inb) == 1:
        return inb[0], "ok"
    return None, ("none_in_band" if not inb else "both_in_band")


def confirmed_fill(prints, E: float, ask: float, need: int = COUNT, window: float = FILL_WINDOW_S):
    """prints: iterable of (ts, yes_price, count, taker_side).  Confirmed = YES-buy takers in [E, E+window] at price <= ask
    with total count >= need.  Returns (bool, total)."""
    tot = 0.0
    for ts, p, c, side in prints:
        if E - 1e-9 <= ts <= E + window + 1e-9 and side == "yes" and p <= ask + EPS:
            tot += c
    return tot >= need - 1e-9, tot


# ---------------------------------------------------------------------------------------------------- stats
def boot_lower(vals, draws: int = 10_000, seed: int = SEED):
    """Bootstrap over games (one value per game).  Returns (2.5th pct, 5th pct) of the resampled mean."""
    v = np.asarray(vals, dtype=float)
    if len(v) == 0:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    m = np.empty(draws)
    for k in range(0, draws, 1000):               # chunked to bound memory
        n = min(1000, draws - k)
        m[k:k + n] = v[rng.integers(0, len(v), size=(n, len(v)))].mean(axis=1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 5.0))


def coin_candidates_matched(minutes, price: float, tol: float = 0.01):
    """minutes: list of dicts {period, asks: {team: ask}}.  Candidates (minute_idx, team): period in 1..3 and ask within tol of price."""
    out = []
    for i, m in enumerate(minutes):
        if m["period"] not in (1, 2, 3):
            continue
        for team, a in m["asks"].items():
            if a is not None and not math.isnan(a) and abs(a - price) <= tol + EPS:
                out.append((i, team))
    return out


def coin_flip(groups, n_copies: int = 500, seed: int = SEED):
    """groups: one entry per real trade = list of minutes, each minute a list of net-per-contract values (one per qualifying team).
    Trades with no candidate minute are skipped.  Each copy: per trade pick a uniform random minute, then a uniform qualifying
    team.  Returns (array of copy means, number of trades used)."""
    groups = [g for g in groups if g]
    if not groups:
        return np.array([]), 0
    rng = np.random.default_rng(seed)
    means = np.empty(n_copies)
    for c in range(n_copies):
        tot = 0.0
        for g in groups:
            opts = g[rng.integers(len(g))]
            tot += opts[rng.integers(len(opts))]
        means[c] = tot / len(groups)
    return means, len(groups)


def select_version(stats: dict, min_trades: int = MIN_DISC_TRADES):
    """stats: version -> dict(n=..., lb=...).  Highest bootstrap lower bound among versions with n >= min_trades.
    Ties break on the version name (sorted) so the choice is deterministic.  Returns (name or None, eligible list)."""
    elig = sorted(v for v, s in stats.items() if s["n"] >= min_trades and s["lb"] == s["lb"])
    if not elig:
        return None, []
    best = max(elig, key=lambda v: (stats[v]["lb"], -elig.index(v)))
    return best, elig


def luck_test(groups: dict, eligible: list, real_best_lb: float, reps: int = LUCK_REPS, seed: int = SEED2,
              draws: int = 10_000):
    """Discovery luck test.  groups: version -> list (one per real trade) of candidate minutes, each minute a list of net values
    (one per qualifying team).  Every rep: per real trade a uniform random minute then a uniform team (trades without a candidate
    are dropped from that copy); per eligible version the bootstrap 2.5th-percentile lower bound of the copy mean; the best LB over
    the eligible versions is the rep's result (the selection rule re-run).  Returns dict with per-rep best LBs and the share >= real."""
    rng = np.random.default_rng(seed)
    best = np.full(reps, np.nan)
    for r in range(reps):
        lbs = []
        for v in eligible:
            vals = []
            for g in groups[v]:
                if not g:
                    continue
                opts = g[int(rng.random() * len(g))]
                vals.append(opts[int(rng.random() * len(opts))])
            if vals:
                lbs.append(boot_lower(vals, draws, seed=int(rng.integers(2 ** 31)))[0])
        best[r] = max(lbs) if lbs else float("nan")
    ok = ~np.isnan(best)
    share = float((best[ok] >= real_best_lb).mean()) if ok.any() else None
    return dict(best_lb_per_rep=best, share_ge_real=share, n_reps=int(reps), n_reps_valid=int(ok.sum()),
                verdict=("FAIL" if (share is None or share > 0.05) else "PASS"))


# ---------------------------------------------------------------------------------------------------- db access
def load_events(conn: sqlite3.Connection):
    """Returns (events {et: dict(markets {team: ticker}, result {team: 'yes'/'no'}, tip, settled)}, series {ticker: Series})."""
    mk = pd.read_sql("SELECT ticker, event_ticker, result, occurrence_ts, settled_ts FROM markets", conn)
    cd = pd.read_sql("SELECT ticker, ts, bid, ask, volume FROM candles ORDER BY ticker, ts", conn)
    series = {}
    for t, g in cd.groupby("ticker", sort=False):
        series[t] = Series(g["ts"].to_numpy(dtype=np.int64), g["bid"].to_numpy(dtype=float), g["ask"].to_numpy(dtype=float),
                           g["volume"].to_numpy(dtype=float))
    events = {}
    for et, g in mk.groupby("event_ticker"):
        events[et] = dict(markets={team_of(r.ticker): r.ticker for r in g.itertuples()},
                          result={team_of(r.ticker): r.result for r in g.itertuples()},
                          tip=int(g["occurrence_ts"].iloc[0]) - 3 * 3600,
                          settled=int(g["settled_ts"].max()) if g["settled_ts"].notna().any() else None)
    return events, series


def decision(ev: dict, series: dict, pg: PbpGame, X: int, band: tuple):
    """One (game, X, band) decision.  E_X = first whole minute >= t_X + 60 s (same for both teams).  Each team's quote is the
    close of its latest candle with end <= E_X (carried forward), valid only if that candle ended within 10 minutes.
    Returns a dict with status trade/skip; uses nothing timestamped after E_X."""
    tX = pg.t.get(X)
    if tX is None:
        return dict(status="skip", reason="no_q4_clock")
    if not wallclock_ok(pg, X, ev["tip"]):
        return dict(status="skip", reason="bad_wallclock", tX=tX)
    E = decision_minute(tX)
    teams = list(ev["markets"])
    q, cend = {}, {}
    for t in teams:
        s_ = series.get(ev["markets"][t])
        r = s_.latest(E) if s_ is not None and len(s_.ts) else None
        q[t] = (r[1], r[2]) if r else None
        cend[t] = r[0] if r else None
    asks = {t: (q[t][1] if q[t] else float("nan")) for t in teams}
    fav, why = pick_favorite(asks, *band)
    if fav is None:
        if why == "none_in_band" and any(q[t] is None for t in teams):
            why = "stale_quote"
        return dict(status="skip", reason=why, tX=tX, E=E, cend=cend, asks=asks)
    bid, ask = q[fav]
    return dict(status="trade", tX=tX, E=E, team=fav, ask=ask, bid=bid, asks=asks, cend=cend)


def build_universe(conn):
    """Matched games sorted by scheduled start: list of (event_ticker, ev, PbpGame), plus match report."""
    pbp = load_pbp()
    events, series = load_events(conn)
    parsed = {et: parse_event_ticker(et) for et in events}
    bad_parse = [et for et, v in parsed.items() if v is None]
    matches, unmatched, shifted = match_games({k: v for k, v in parsed.items() if v}, pbp)
    games = [(et, events[et], pbp[gid]) for et, gid in matches.items()]
    games.sort(key=lambda x: (x[1]["tip"], x[0]))
    return games, series, dict(n_events=len(events), n_matched=len(matches), unmatched=unmatched, shifted=shifted,
                               bad_parse=bad_parse)


def trade_jobs(conn: sqlite3.Connection):
    """(ticker, min_ts, max_ts) print windows [E-1, E+61] for every (game, version) that would TRADE (exactly one team in band),
    over all 16 versions and all games.  Slippage does not change which market/window is needed."""
    games, series, _ = build_universe(conn)
    jobs = set()
    for et, ev, pg in games:
        for X in XS:
            for band in BANDS.values():
                r = decision(ev, series, pg, X, band)
                if r["status"] == "trade":
                    E = int(r["E"])
                    jobs.add((ev["markets"][r["team"]], E - 1, E + FILL_WINDOW_S + 1))
    return sorted(jobs)
