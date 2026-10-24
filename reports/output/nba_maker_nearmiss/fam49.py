"""#53, #49 family: resting orders on Kalshi's own NBA total markets (game / team / first-half) when they disagree.

Reuses #49's mismatch.py (implied median, main strike, D formulas, k frozen = k_frozen.json = 0.5113) and its population.csv split.
Data (all cached, read-only): game total candles from totals.sqlite (raw JSON column carries trade-price OHLC), team and 1H candles
from the raw gz cache data/raw/kalshi_teamtotal_1h_hourly (scanned once to map ticker -> candles).
Post time E0 (game level) = the earliest candle end among all the game's markets that have candles; markets without a candle ending
exactly at E0 have no quote at the post time and are not used.  E0 must be <= T - 2h.
"""
from __future__ import annotations

import glob
import gzip
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
D49 = ROOT / "reports" / "output" / "nba_internal_mismatch"
sys.path.insert(0, str(D49))
import mismatch as MM  # noqa: E402

import nm_lib as N  # noqa: E402

K_FILE = D49 / "k_frozen.json"
TOTALS = ROOT / "reports" / "output" / "nba_totals_pace" / "totals.sqlite"
TEAM_1H_CACHE = ROOT / "data" / "raw" / "kalshi_teamtotal_1h_hourly"
PAIRS = MM.PAIRS
DIRECTIONS = MM.DIRECTIONS
THRESHOLDS = MM.THRESHOLDS
VERSIONS = MM.grid()                    # 16 : (pair, direction, threshold)


def vname(v):
    return MM.vname(v)


def ro(path: Path):
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def index_gz(wanted: set, cache_dir: Path = TEAM_1H_CACHE):
    """Scan the raw cache once; {ticker: [candle tuples]} for the wanted tickers only (stream, one file at a time)."""
    out = {}
    for f in glob.glob(str(Path(cache_dir) / "*" / "*.json.gz")):
        try:
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:   # noqa: BLE001
            continue
        d = d.get("response", d)
        t = d.get("ticker")
        if t in wanted:
            out[t] = N.parse_candles(d)
    return out


def totals_candles(tc, ticker, T):
    rows = tc.execute("SELECT raw FROM candles WHERE ticker=? ORDER BY end_ts", (ticker,)).fetchall()
    cs = [N.parse_candle(json.loads(r[0])) for r in rows]
    return [c for c in cs if c[0] <= T - N.FILL_DEADLINE]       # the cache window already ends at T-1h; re-checked


def quotes_at(markets, candles, e0):
    """markets [(ticker, strike)] -> [(ticker, strike, bid, ask)] from the candle ending exactly at e0 (valid two-sided only)."""
    out = []
    for t, s in markets:
        c = N.candle_at(candles.get(t, []), e0)
        if c is None or c[1] is None or c[2] is None:
            continue
        out.append((t, s, c[1], c[2]))
    return out


def build_games(split, k, fee_types):
    """-> (games, skips).  Population filtered by `split` BEFORE any candle / result is read."""
    pop = N.read_population(D49 / "population.csv", split)
    mk, tc = ro(D49 / "markets.sqlite"), ro(TOTALS)
    # tickers of the (already filtered) games
    plan = []
    wanted = set()
    for ev in pop:
        suf = MM.event_suffix(ev["event"])
        team = mk.execute("SELECT ticker, team, strike FROM markets WHERE event_ticker=? ORDER BY strike",
                          ("KXNBATEAMTOTAL-" + suf,)).fetchall()
        half = mk.execute("SELECT ticker, strike FROM markets WHERE event_ticker=? ORDER BY strike",
                          ("KXNBA1HTOTAL-" + suf,)).fetchall()
        gt = tc.execute("SELECT ticker, floor_strike FROM markets WHERE event_ticker=? ORDER BY floor_strike", (ev["event"],)).fetchall()
        plan.append((ev, team, half, gt))
        wanted |= {t for t, *_ in team} | {t for t, _ in half}
    cache = index_gz(wanted)
    skips = Counter()
    games = []
    for ev, team, half, gt in plan:
        T = int(ev["T"])
        candles = {t: totals_candles(tc, t, T) for t, _ in gt}
        for t, *_ in team:
            if t in cache:
                candles[t] = [c for c in cache[t] if c[0] <= T - N.FILL_DEADLINE]
        for t, _ in half:
            if t in cache:
                candles[t] = [c for c in cache[t] if c[0] <= T - N.FILL_DEADLINE]
        firsts = [c[0][0] for c in candles.values() if c]
        if not firsts:
            skips["no_candles"] += 1
            continue
        e0 = min(firsts)
        if e0 > T - N.MIN_LEAD_E0:
            skips["no_post_window"] += 1
            continue
        g = dict(event=ev["event"], date=ev["game_date_et"], stype=ev["season_type"], T=T, e0=e0, home=ev["home"], away=ev["away"])
        gq = quotes_at(gt, candles, e0)
        g["Mg"] = MM.implied_median([(s, b, a) for _, s, b, a in gq])
        if g["Mg"] is None:
            skips["no_game_total_median_at_E0"] += 1
            continue
        g["game_main"] = MM.main_strike(gq)
        team_of = {t: tm for t, tm, _ in team}
        tq = quotes_at([(t, s) for t, _, s in team], candles, e0)
        qs = {"home": [q for q in tq if team_of[q[0]] == ev["home"]], "away": [q for q in tq if team_of[q[0]] == ev["away"]]}
        g["Mh"], g["Ma"] = (MM.implied_median([(s, b, a) for _, s, b, a in qs[x]]) for x in ("home", "away"))
        g["team_main"] = {x: MM.main_strike(qs[x]) for x in ("home", "away")}
        g["D_team"] = MM.d_team(g["Mh"], g["Ma"], g["Mg"]) if g["Mh"] is not None and g["Ma"] is not None else None
        hq = quotes_at(half, candles, e0)
        g["M1H"] = MM.implied_median([(s, b, a) for _, s, b, a in hq])
        g["half_main"] = MM.main_strike(hq)
        g["D_1H"] = MM.d_1h(g["M1H"], g["Mg"], k) if g["M1H"] is not None else None
        if g["D_team"] is None:
            skips["team_median_missing"] += 1
        if g["M1H"] is None:
            skips["1H_median_missing"] += 1
        # ---- orders for the 4 (pair, direction) combos x 2 sides (outcomes are read here, after quotes/fills are fixed)
        g["res"] = {}
        for pair in PAIRS:
            for direction in DIRECTIONS:
                g[(pair, direction)] = _orders(g, pair, direction, candles, T, tc, mk, fee_types)
        games.append(g)
    return games, dict(skips)


def _result(tc, mk, ticker):
    for conn in (tc, mk):
        r = conn.execute("SELECT result FROM markets WHERE ticker=?", (ticker,)).fetchone()
        if r:
            return r[0]
    return None


def _legs(g, pair, direction):
    """[(ticker, bid, ask)] bet markets, or None (main strike missing).  Same selection as #49's legs_for, at E0."""
    if pair == "team" and direction == "other_follows":
        h, a = g["team_main"]["home"], g["team_main"]["away"]
        return None if h is None or a is None else [(h[0], h[2], h[3]), (a[0], a[2], a[3])]
    mm = g["game_main"] if direction == "game_follows" else g["half_main"]
    return None if mm is None else [(mm[0], mm[2], mm[3])]


def _orders(g, pair, direction, candles, T, tc, mk, fee_types):
    """-> None (no legs / unsettled-or-nonbinary leg) or {'yes': unit, 'no': unit}; unit = dict(legs=[order dicts]).
    The result is read only to value fills (binary yes/no only; anything else drops the game for this combo)."""
    legs = _legs(g, pair, direction)
    D_ = g["D_team"] if pair == "team" else g["D_1H"]
    if legs is None or D_ is None:
        return None
    sv = []
    for t, _, _ in legs:
        r = _result(tc, mk, t)
        if r not in ("yes", "no"):
            return None
        sv.append(1.0 if r == "yes" else 0.0)
    out = {}
    for side in ("yes", "no"):
        os_ = []
        for (t, b, a), s in zip(legs, sv):
            o = N.simulate_order(side, b, a, candles[t], g["e0"], T, s, fee_types.get(N.series_of(t)))
            o["ticker"] = t
            os_.append(o)
        out[side] = os_
    return out


def unit_summary(orders):
    """orders: list of per-leg order dicts for ONE game/side.  -> dict(posted, filled, net, net1c, legs_posted, legs_filled,
    wins_filled, unfilled_win_values).  Unit net = mean over FILLED legs (per contract); posted=False when no leg was posted."""
    posted = [o for o in orders if o["posted"]]
    filled = [o for o in posted if o["filled"]]
    unf = [o for o in posted if not o["filled"]]
    return dict(
        posted=bool(posted), filled=bool(filled),
        net=(sum(o["net"] for o in filled) / len(filled)) if filled else None,
        net1c=(sum(o["net1c"] for o in filled) / len(filled)) if filled else None,
        legs_posted=len(posted), legs_filled=len(filled),
        filled_win_values=[o["win_value"] for o in filled], unfilled_win_values=[o["win_value"] for o in unf],
        limits=[o["limit"] for o in posted], tickers=[o["ticker"] for o in posted])


def real_side(direction, D_):
    sf = MM.side_for(direction, D_)
    return None if sf is None else sf[1]


def version_units(games, v):
    """Real-side units of version v = (pair, direction, th): [(game, summary)] for games with |D| >= th and a posted order."""
    pair, direction, th = v
    out = []
    for g in games:
        D_ = g["D_team"] if pair == "team" else g["D_1H"]
        o = g.get((pair, direction))
        if D_ is None or o is None or abs(D_) < th - MM.EPS:
            continue
        side = real_side(direction, D_)
        if side is None:
            continue
        s = unit_summary(o[side])
        if s["posted"]:
            out.append((g, s, side))
    return out
