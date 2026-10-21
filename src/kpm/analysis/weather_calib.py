"""Weather calibration study: pre-registered analysis (reports/output/weather_calibration/PREREGISTRATION.md).

Reads data/weather.sqlite READ-ONLY (safe while the collector writes). Only markets whose candles are fully collected
(wx_done) enter the analysis. Prices are integer 1e-4 dollars throughout; money sums are integers (exact).

Net per 10-contract order, in e4 dollars: 10 * payout_e4 - 10 * price_e4 - fee_e4, with
fee_e4 = ceil(0.07 * 10 * P * (1 - P) in e4)  (rounded UP to $0.0001, as on real fills).
"""
from __future__ import annotations

import csv
import json
import logging
import math
import sqlite3
import time
import warnings
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from kpm.collectors.kalshi_weather import peak_rss_mb, strikes_from_rules, weather_db_path

log = logging.getLogger(__name__)

OUT_DIR = Path("reports/output/weather_calibration")
SPLIT_DAY = "2026-06-01"                     # discovery: D < this, validation: D >= this
SNAPS = ("S1", "S2", "S3", "S4")
# (days before D, local hour): S1 = D-1 18:00, S2 = D 07:00, S3 = D 12:00, S4 = D 16:00
SNAP_RULES = ((1, 18), (0, 7), (0, 12), (0, 16))
# entry-price buckets in e4 dollars: (lo_exclusive, hi_inclusive); the first bucket includes its lower edge (0.01)
BUCKET_EDGES = (100, 300, 600, 1000, 2000, 3500, 5000, 6500, 8000, 9000, 9400, 9700, 9900)
BUCKET_LABELS = tuple(f"{BUCKET_EDGES[i] / 100:g}-{BUCKET_EDGES[i + 1] / 100:g}c" for i in range(len(BUCKET_EDGES) - 1))
N_BUCKETS = len(BUCKET_LABELS)
N_CONTRACTS = 10
SEED = 20261013
N_BOOT = 10_000
CAND = dict(min_n=200, min_dates=30)
VALID_MIN_N = 100
SIZE_MIN_PER_WEEK = 2.0
CONFIRM_WINDOW_S = 600
CONFIRM_MIN_CONTRACTS = 10
W2_MIN_SHARE = 0.99
EPS = 1e-9


# ---------------------------------------------------------------- pure helpers
def snapshot_times(day: str, tz: str) -> list[int]:
    """UTC epoch seconds of S1..S4 for measured day `day` (YYYY-MM-DD) in IANA zone `tz` (DST-correct)."""
    d = date.fromisoformat(day)
    z = ZoneInfo(tz)
    out = []
    for back, hour in SNAP_RULES:
        local = datetime.combine(d - timedelta(days=back), dtime(hour, 0), tzinfo=z)
        out.append(int(local.timestamp()))
    return out


def bucket_index(price_e4: int) -> int | None:
    """Index 0..11 of the entry-price bucket, None outside [0.01, 0.99]. Lower edge exclusive except the first."""
    if price_e4 < BUCKET_EDGES[0] or price_e4 > BUCKET_EDGES[-1]:
        return None
    if price_e4 <= BUCKET_EDGES[1]:
        return 0
    for i in range(1, N_BUCKETS):
        if BUCKET_EDGES[i] < price_e4 <= BUCKET_EDGES[i + 1]:
            return i
    return None


def fee_e4(price_e4: int, contracts: int = N_CONTRACTS) -> int:
    """Taker fee of ONE order of `contracts` at `price_e4`: 0.07*C*P*(1-P) rounded up to $0.0001 (integer maths)."""
    num = 7 * contracts * price_e4 * (10_000 - price_e4)      # = fee in e4 * 1_000_000
    return -(-num // 1_000_000)


def payout_e4(side: str, result: str) -> int:
    """YES pays 1 if the market resolved yes; NO pays 1 if it resolved no."""
    return 10_000 if (result == "yes" if side == "yes" else result == "no") else 0


def net10_e4(side: str, price_e4: int, result: str) -> int:
    """Net of one 10-contract order in e4 dollars, after the taker fee."""
    return N_CONTRACTS * (payout_e4(side, result) - price_e4) - fee_e4(price_e4)


def carried_quote(conn: sqlite3.Connection, mid: int, t: int) -> tuple[int | None, int | None] | None:
    """Last candle with ts <= t as (bid_c, ask_c); None if the market has no candle yet."""
    r = conn.execute("SELECT bid_c, ask_c FROM wx_quotes WHERE mid=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                     (mid, t)).fetchone()
    return None if r is None else (r[0], r[1])


def entries_from_quote(bid: int | None, ask: int | None):
    """Hypothetical taker entries from one quote: (side, price_e4). Prices outside [0.01, 0.99] are dropped later."""
    if ask is not None:
        yield "yes", ask
    if bid is not None:
        yield "no", 10_000 - bid


def confirmed(trades: list[tuple[float, float, float, str]], side: str, ref_yes_price: float, t: int,
              window: int = CONFIRM_WINDOW_S, need: float = CONFIRM_MIN_CONTRACTS) -> bool:
    """trades: (ts, yes_price, count, taker_side). Buy YES at ask `ref`: taker_side 'yes' and yes_price <= ref.
    Buy NO against bid `ref`: taker_side 'no' and yes_price >= ref. Prints within [t-window, t+window]; total >= need."""
    tot = 0.0
    for ts, px, cnt, taker in trades:
        if not (t - window <= ts <= t + window) or taker != side:
            continue
        if (px <= ref_yes_price + EPS) if side == "yes" else (px >= ref_yes_price - EPS):
            tot += cnt
    return tot >= need


def _int_interval(stype: str, fl: float | None, cp: float | None) -> tuple[float, float]:
    """Integer-degree interval covered by a market: between [fl, cp]; greater (fl, inf) = [fl+1, inf]; less [-inf, cp-1]."""
    if stype == "between":
        return fl, cp
    if stype == "greater":
        return fl + 1, math.inf
    return -math.inf, cp - 1


def w1_event_reasons(markets: list[dict]) -> set[str]:
    """Reasons an event fails W1 (empty set = valid). markets: dicts with result, strike_src, strike_type, floor, cap."""
    reasons: set[str] = set()
    if any(m["result"] == "scalar" for m in markets):
        reasons.add("scalar_result")
    if any(m["strike_src"] == "none" or m["strike_type"] is None for m in markets):
        reasons.add("strike_unparsed")
    else:
        iv = sorted(_int_interval(m["strike_type"], m["floor"], m["cap"]) for m in markets)
        if any(iv[i][1] >= iv[i + 1][0] for i in range(len(iv) - 1)):
            reasons.add("overlapping_ranges")
    n_yes = sum(1 for m in markets if m["result"] == "yes")
    if n_yes == 0:
        reasons.add("no_yes")
    elif n_yes > 1:
        reasons.add("multiple_yes")
    return reasons


def covers_all(markets: list[dict]) -> bool:
    """True if the event's markets tile all integer degrees with no gap (needed for bracket-sum bundles)."""
    if any(m["strike_type"] is None for m in markets):
        return False
    iv = sorted(_int_interval(m["strike_type"], m["floor"], m["cap"]) for m in markets)
    if iv[0][0] != -math.inf or iv[-1][1] != math.inf:
        return False
    return all(iv[i][1] + 1 == iv[i + 1][0] for i in range(len(iv) - 1))


def strike_disagrees(stype, fl, cp, rules: str | None) -> bool | None:
    """W4: True if the rules text names a different strike range than floor/cap; None if the text is unparseable."""
    rt, rf, rc = strikes_from_rules(rules)
    if rt is None:
        return None
    return not (rt == stype and rf == fl and rc == cp)


def bootstrap_ratio(S: np.ndarray, N: np.ndarray, n_boot: int = N_BOOT, seed: int = SEED,
                    chunk: int = 500) -> np.ndarray:
    """Ratio-estimator bootstrap over dates. S, N: (n_dates, n_cells) per-date sums and counts. Returns an
    (n_boot, n_cells) array of resampled means sum(S*w)/sum(N*w), w = multinomial counts of dates (NaN if the
    resample has no trades)."""
    D, C = S.shape
    rng = np.random.default_rng(seed)
    out = np.empty((n_boot, C))
    p = np.full(D, 1.0 / D)
    done = 0
    while done < n_boot:
        k = min(chunk, n_boot - done)
        W = rng.multinomial(D, p, size=k).astype(np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[done:done + k] = (W @ S) / (W @ N)
        done += k
    return out


# ---------------------------------------------------------------- loading + data quality
def open_ro(path: str | Path | None = None) -> sqlite3.Connection:
    p = Path(path) if path else weather_db_path()
    conn = sqlite3.connect(f"file:{p.resolve().as_posix()}?mode=ro", uri=True, timeout=120)
    conn.row_factory = sqlite3.Row
    return conn


def load_markets(conn: sqlite3.Connection) -> tuple[list[dict], dict]:
    """All markets (slim dicts, rules text dropped after the W4 check) + W4 counters."""
    w4 = {"markets_checked": 0, "unparseable_rules": 0, "disagree": 0, "disagree_examples": [],
          "source_split_by_series": {}}
    split: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    out = []
    q = ("SELECT m.mid, m.ticker, m.series, m.event_ticker, m.kind, m.city, m.tz, m.day, m.strike_type, "
         "m.floor_strike, m.cap_strike, m.strike_src, m.open_ts, m.close_ts, m.result, m.source, m.rules_primary, "
         "EXISTS(SELECT 1 FROM wx_done d WHERE d.ticker = m.ticker AND d.step = 'quotes') AS done FROM wx_markets m")
    for r in conn.execute(q):
        d = dict(mid=r[0], ticker=r[1], series=r[2], event=r[3], kind=r[4], city=r[5], tz=r[6], day=r[7],
                 strike_type=r[8], floor=r[9], cap=r[10], strike_src=r[11], open_ts=r[12], close_ts=r[13],
                 result=r[14], source=r[15], done=bool(r[17]))
        split[r[2]][r[15]] += 1
        if r[11] == "api":                      # rules-derived strikes agree with themselves by construction
            w4["markets_checked"] += 1
            dis = strike_disagrees(r[8], r[9], r[10], r[16])
            if dis is None:
                w4["unparseable_rules"] += 1
            elif dis:
                w4["disagree"] += 1
                if len(w4["disagree_examples"]) < 20:
                    w4["disagree_examples"].append(r[1])
        out.append(d)
    w4["source_split_by_series"] = {s: dict(v) for s, v in sorted(split.items())}
    return out, w4


def w1_filter(markets: list[dict]) -> tuple[dict[str, list[dict]], dict]:
    """Group by event; returns (valid + fully collected events, W1 report)."""
    events: dict[str, list[dict]] = defaultdict(list)
    for m in markets:
        events[m["event"]].append(m)
    rep = {"events_total": len(events), "markets_total": len(markets),
           "by_reason": {}, "by_series": {}, "excluded_events": 0, "excluded_markets": 0,
           "events_candles_incomplete": 0, "events_analysed": 0, "markets_analysed": 0}
    by_reason: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_series: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    valid: dict[str, list[dict]] = {}
    for ev, ms in events.items():
        reasons = w1_event_reasons(ms)
        ser = ms[0]["series"]
        if reasons:
            rep["excluded_events"] += 1
            rep["excluded_markets"] += len(ms)
            for r in reasons:
                by_reason[r][0] += 1
                by_reason[r][1] += len(ms)
                by_series[ser][r][0] += 1
                by_series[ser][r][1] += len(ms)
            continue
        if not all(m["done"] for m in ms) or any(m["day"] is None for m in ms):
            rep["events_candles_incomplete"] += 1
            continue
        valid[ev] = ms
    rep["events_analysed"] = len(valid)
    rep["markets_analysed"] = sum(len(v) for v in valid.values())
    rep["by_reason"] = {r: {"events": a, "markets": b} for r, (a, b) in sorted(by_reason.items())}
    rep["by_series"] = {s: {r: {"events": a, "markets": b} for r, (a, b) in sorted(d.items())}
                        for s, d in sorted(by_series.items())}
    return valid, rep


def w2_w3(conn: sqlite3.Connection) -> dict:
    """W2 carry-forward validity and W3 crossed quotes, over all candles (SQL window functions)."""
    sql = ("WITH x AS (SELECT ts, bid_o, ask_o, LAG(ts) OVER w AS pts, LAG(bid_c) OVER w AS pb, LAG(ask_c) OVER w AS pa "
           "FROM wx_quotes WINDOW w AS (PARTITION BY mid ORDER BY ts)) "
           "SELECT (ts - pts) > 60 AS gap, COUNT(*), SUM(bid_o IS pb AND ask_o IS pa) FROM x "
           "WHERE pts IS NOT NULL GROUP BY gap")
    res = {int(g): (n, ok or 0) for g, n, ok in conn.execute(sql)}
    gn, gok = res.get(1, (0, 0))
    cn, cok = res.get(0, (0, 0))
    total = conn.execute("SELECT COUNT(*) FROM wx_quotes").fetchone()[0]
    crossed = conn.execute("SELECT COUNT(*) FROM wx_quotes WHERE bid_c IS NOT NULL AND ask_c IS NOT NULL "
                           "AND bid_c > ask_c").fetchone()[0]
    share = gok / gn if gn else None
    return {"W2": {"candles_after_gap": gn, "gap_open_equals_prev_close": gok, "gap_share": share,
                   "consecutive_minute_candles": cn, "consecutive_open_equals_prev_close": cok,
                   "consecutive_share": cok / cn if cn else None, "threshold": W2_MIN_SHARE,
                   "stop": bool(share is not None and share < W2_MIN_SHARE)},
            "W3": {"candles_total": total, "crossed_quotes": crossed}}


# ---------------------------------------------------------------- snapshot enumeration
def event_snapshots(conn: sqlite3.Connection, valid: dict[str, list[dict]]):
    """Yield (event, snap_idx, t, [(market, quote|None), ...]) for every snapshot at which the event has markets open."""
    cache: dict[tuple[str, str], list[int]] = {}
    for ev, ms in valid.items():
        key = (ms[0]["day"], ms[0]["tz"])
        times = cache.get(key) or cache.setdefault(key, snapshot_times(*key))
        for si, t in enumerate(times):
            opened = [m for m in ms if m["open_ts"] is not None and m["close_ts"] is not None
                      and m["open_ts"] <= t < m["close_ts"]]
            if not opened:
                continue
            yield ev, si, t, [(m, carried_quote(conn, m["mid"], t)) for m in opened]
        if len(cache) > 5000:
            cache.clear()


def bracket_check(qs: list[tuple[dict, tuple | None]], n_total: int) -> tuple[int | None, int | None]:
    """(yes_bundle_profit10_e4, no_bundle_profit10_e4) for buying every market of a complete event at one snapshot
    (10 sets, fees per order); None where the bundle is unavailable (missing ask/bid or a market not open)."""
    if len(qs) != n_total or any(q is None for _, q in qs):
        return None, None
    yes = no = None
    if all(q[1] is not None for _, q in qs):
        cost = sum(N_CONTRACTS * q[1] + fee_e4(q[1]) for _, q in qs)
        yes = N_CONTRACTS * 10_000 - cost
    if all(q[0] is not None for _, q in qs):
        cost = sum(N_CONTRACTS * (10_000 - q[0]) + fee_e4(10_000 - q[0]) for _, q in qs)
        no = N_CONTRACTS * (n_total - 1) * 10_000 - cost
    return yes, no


def aggregate(conn: sqlite3.Connection, valid: dict[str, list[dict]]) -> tuple[pd.DataFrame, dict, list[dict], dict]:
    """First pass. Returns (agg DataFrame, W5 per series, bracket rows, bracket summary)."""
    acc: dict[tuple, list[int]] = {}
    w5: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    brackets: list[dict] = []
    bsum = {"event_snapshots_evaluated": 0, "yes_bundle_profitable": 0, "no_bundle_profitable": 0}
    complete = {ev: covers_all(ms) for ev, ms in valid.items()}
    for ev, si, t, qs in event_snapshots(conn, valid):
        ms0 = valid[ev][0]
        for m, q in qs:
            w5[m["series"]][0] += 1
            if q is None:
                continue
            w5[m["series"]][1] += 1
            for side, price in entries_from_quote(*q):
                b = bucket_index(price)
                if b is None:
                    continue
                k = (m["city"], m["source"], m["kind"], si, side, b, m["day"])
                a = acc.get(k)
                v = net10_e4(side, price, m["result"])
                if a is None:
                    acc[k] = [v, 1]
                else:
                    a[0] += v
                    a[1] += 1
        if complete[ev]:
            y, n = bracket_check(qs, len(valid[ev]))
            if y is not None or n is not None:
                bsum["event_snapshots_evaluated"] += 1
                if y is not None and y > 0:
                    bsum["yes_bundle_profitable"] += 1
                if n is not None and n > 0:
                    bsum["no_bundle_profitable"] += 1
                if (y is not None and y > 0) or (n is not None and n > 0):
                    brackets.append({"event": ev, "series": ms0["series"], "city": ms0["city"], "day": ms0["day"],
                                     "snapshot": SNAPS[si], "n_markets": len(valid[ev]),
                                     "yes_bundle_profit10": None if y is None else y / 1e4,
                                     "no_bundle_profit10": None if n is None else n / 1e4})
    rows = [(*k, v[0], v[1]) for k, v in acc.items()]
    df = pd.DataFrame(rows, columns=["city", "source", "kind", "snap", "side", "bucket", "day", "net", "n"])
    return df, {s: {"slots": a, "with_quote": b, "share": (b / a if a else None)} for s, (a, b) in sorted(w5.items())}, \
        brackets, bsum


# ---------------------------------------------------------------- cell statistics
def cell_table(df: pd.DataFrame, group_cols: list[str], days_by_period: dict[str, list[str]], n_boot: int,
               seed: int = SEED, min_n: int = 0, full_grid: bool = False) -> pd.DataFrame:
    """Per (group_cols x kind x snap x side x bucket) x period: n, n_dates, mean net per contract ($), bootstrap
    percentiles. `days_by_period` lists every analysed date (resampling universe). Cells below `min_n` trades
    (in that period) are skipped unless full_grid (the 192-cell main table keeps every cell)."""
    keys = group_cols + ["kind", "snap", "side", "bucket"]
    out = []
    for period, days in days_by_period.items():
        sub = df[df["day"].isin(set(days))] if len(df) else df
        if not len(days):
            continue
        g = sub.groupby(keys + ["day"], sort=True)[["net", "n"]].sum().reset_index() if len(sub) else sub
        tot = g.groupby(keys)[["net", "n"]].sum() if len(g) else pd.DataFrame(columns=["net", "n"])
        if full_grid:
            cells = [(kd, s_, sd, b_) for kd in ("high", "low") for s_ in range(4) for sd in ("yes", "no")
                     for b_ in range(N_BUCKETS)]
        else:
            cells = [k if isinstance(k, tuple) else (k,) for k in tot.index[tot["n"] >= max(min_n, 1)]]
        if not cells:
            continue
        didx = {d: i for i, d in enumerate(days)}
        cidx = {c: i for i, c in enumerate(cells)}
        S = np.zeros((len(days), len(cells)))
        N = np.zeros((len(days), len(cells)))
        if len(g):
            for row in g.itertuples(index=False):
                kk = tuple(getattr(row, c) for c in keys)
                j = cidx.get(kk)
                if j is not None:
                    S[didx[row.day], j] = row.net
                    N[didx[row.day], j] = row.n
        boot = bootstrap_ratio(S, N, n_boot, seed)
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            p = np.nanpercentile(boot, [2.5, 5.0, 97.5], axis=0)
        sums, ns = S.sum(0), N.sum(0)
        nd = (N > 0).sum(0)
        for j, c in enumerate(cells):
            rec = dict(zip(keys, c))
            rec["snap"] = SNAPS[rec["snap"]]
            rec["bucket"] = BUCKET_LABELS[rec["bucket"]]
            rec.update(period=period, n=int(ns[j]), n_dates=int(nd[j]),
                       mean_net=(sums[j] / ns[j] / N_CONTRACTS / 1e4) if ns[j] else np.nan,
                       p2_5=p[0, j] / N_CONTRACTS / 1e4, p5=p[1, j] / N_CONTRACTS / 1e4,
                       p97_5=p[2, j] / N_CONTRACTS / 1e4)
            out.append(rec)
    cols = keys + ["period", "n", "n_dates", "mean_net", "p2_5", "p5", "p97_5"]
    return pd.DataFrame(out, columns=cols)


def is_candidate(r) -> bool:
    return bool(r.n >= CAND["min_n"] and r.n_dates >= CAND["min_dates"] and r.mean_net > 0 and r.p2_5 > 0)


def passes_validation(r) -> bool:
    return bool(r.n >= VALID_MIN_N and r.mean_net > 0 and r.p5 > 0)


# ---------------------------------------------------------------- size check (public trade prints)
def _entries_for_cells(conn, valid, cells: set[tuple], period_start: str):
    """Second pass: validation-period entries (ticker, t, side, price_e4, net10_e4, day, cell) for the given cells."""
    for ev, si, t, qs in event_snapshots(conn, {e: m for e, m in valid.items() if m[0]["day"] >= period_start}):
        for m, q in qs:
            if q is None:
                continue
            for side, price in entries_from_quote(*q):
                b = bucket_index(price)
                if b is None:
                    continue
                cell = (m["kind"], SNAPS[si], side, BUCKET_LABELS[b])
                if cell in cells:
                    yield dict(ticker=m["ticker"], t=t, side=side, price=price, net=net10_e4(side, price, m["result"]),
                               day=m["day"], cell=cell)


def fetch_window(client, ticker: str, t: int) -> list[tuple[float, float, float, str]]:
    """Public trade prints for one (market, snapshot) window [t-600, t+600]; GET only, cached by the client."""
    from kpm.core.prices import parse_dollars
    from kpm.core.timeutil import parse_iso
    lo, hi = t - CONFIRM_WINDOW_S, t + CONFIRM_WINDOW_S
    rows = []
    for hist in {client.trades_historical(lo), client.trades_historical(hi)}:
        for tr in client.trades(ticker, lo, hi, hist):
            rows.append((float(parse_iso(tr["created_time"])), float(parse_dollars(tr["yes_price_dollars"])),
                         float(parse_dollars(tr.get("count_fp")) or tr.get("count") or 0), tr.get("taker_side") or ""))
    return rows


def size_check(conn, valid, passing_cells: set[tuple], last_day: str, max_fetch: int, client=None) -> dict:
    """Confirm validation entries of the passing cells against trade prints; returns $/week per cell."""
    entries = list(_entries_for_cells(conn, valid, passing_cells, SPLIT_DAY))
    windows = sorted({(e["ticker"], e["t"]) for e in entries})
    capped = len(windows) > max_fetch
    allowed = set(windows[:max_fetch])
    if client is None:
        from kpm.clients.kalshi import KalshiClient
        client = KalshiClient(None)
        client.http.min_interval_s = max(client.http.min_interval_s, 0.25)     # <= 4 req/s
        client.cutoff()
    prints: dict[tuple[str, int], list] = {}

    def work(w):
        return w, fetch_window(client, *w)

    with ThreadPoolExecutor(max_workers=4) as pool:
        for w, rows in pool.map(work, [w for w in windows if w in allowed]):
            prints[w] = rows
    weeks = max(((date.fromisoformat(last_day) - date.fromisoformat(SPLIT_DAY)).days + 1) / 7.0, 1 / 7.0)
    res: dict[tuple, dict] = {c: dict(entries=0, confirmed=0, confirmed_net10=0.0, unfetched=0) for c in passing_cells}
    for e in entries:
        r = res[e["cell"]]
        r["entries"] += 1
        w = (e["ticker"], e["t"])
        if w not in prints:
            r["unfetched"] += 1
            continue
        ref = e["price"] / 1e4 if e["side"] == "yes" else 1 - e["price"] / 1e4     # yes-price reference
        if confirmed(prints[w], e["side"], ref, e["t"]):
            r["confirmed"] += 1
            r["confirmed_net10"] += e["net"] / 1e4
    for r in res.values():
        r["usd_per_week"] = r["confirmed_net10"] / weeks
        r["size_pass"] = r["usd_per_week"] >= SIZE_MIN_PER_WEEK
    return {"cells": res, "windows_needed": len(windows), "windows_fetched": len(prints), "cap_hit": capped,
            "validation_weeks": weeks}


# ---------------------------------------------------------------- run
def _write_csv(path: Path, df: pd.DataFrame) -> None:
    df.to_csv(path, index=False)


def run(db_path: str | None = None, fills: bool = True, max_fetch: int = 20_000, out_dir: str | None = None,
        n_boot: int = N_BOOT, n_boot_exploratory: int = 2000, client=None) -> dict:
    t0 = time.monotonic()
    out = Path(out_dir) if out_dir else OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    conn = open_ro(db_path)
    try:
        markets, w4 = load_markets(conn)
        valid, w1 = w1_filter(markets)
        del markets
        dq = {"generated_ts": int(time.time()), "W1": w1, "W4": w4, **w2_w3(conn)}
        log.info("W1: %d events analysed, %d excluded, %d incomplete", w1["events_analysed"], w1["excluded_events"],
                 w1["events_candles_incomplete"])
        stop = dq["W2"]["stop"]
        if stop:
            msg = (f"!!! W2 STOP: only {dq['W2']['gap_share']:.4f} of post-gap candles open at the previous close "
                   f"(< {W2_MIN_SHARE}). Carry-forward is not validated; do not trust any verdict. !!!")
            log.error(msg)
            print("\n" + "=" * 100 + "\n" + msg + "\n" + "=" * 100 + "\n")
        df, w5, brackets, bsum = aggregate(conn, valid)
        dq["W5"] = w5
        dq["stop_flag"] = bool(stop)
        days = sorted({m[0]["day"] for m in valid.values()})
        periods = {"discovery": [d for d in days if d < SPLIT_DAY], "validation": [d for d in days if d >= SPLIT_DAY]}
        cells = cell_table(df, [], periods, n_boot, full_grid=True)
        cand_rows = [r for r in cells[cells.period == "discovery"].itertuples() if is_candidate(r)]
        val = cells[cells.period == "validation"].set_index(["kind", "snap", "side", "bucket"])
        cand_df = pd.DataFrame([r._asdict() for r in cand_rows]).drop(columns=["Index"], errors="ignore")
        passing: set[tuple] = set()
        verdict_cells = []
        for r in cand_rows:
            key = (r.kind, r.snap, r.side, r.bucket)
            v = val.loc[key] if key in val.index else None
            vp = bool(v is not None and passes_validation(v))
            if vp:
                passing.add(key)
            verdict_cells.append(dict(kind=r.kind, snapshot=r.snap, side=r.side, bucket=r.bucket,
                                      discovery=dict(n=r.n, n_dates=r.n_dates, mean_net=r.mean_net, p2_5=r.p2_5),
                                      validation=None if v is None else dict(n=int(v.n), n_dates=int(v.n_dates),
                                                                              mean_net=float(v.mean_net),
                                                                              p5=float(v.p5)),
                                      candidate=True, validation_pass=vp, size_pass=None, usd_per_week=None))
        size = None
        if passing and fills:
            size = size_check(conn, valid, passing, periods["validation"][-1] if periods["validation"] else SPLIT_DAY,
                              max_fetch, client)
            for vc in verdict_cells:
                s = size["cells"].get((vc["kind"], vc["snapshot"], vc["side"], vc["bucket"]))
                if s:
                    vc.update(size_pass=bool(s["size_pass"]), usd_per_week=s["usd_per_week"], size_detail=s)
        all_pass = [vc for vc in verdict_cells if vc["validation_pass"] and vc["size_pass"]]
        if stop:
            verdict = "STOP_W2"
        elif all_pass:
            verdict = "PASS"
        elif passing and not fills:
            verdict = "UNDETERMINED_FILLS_SKIPPED"
        else:
            verdict = "FAIL"
        # exploratory splits
        expl = {}
        for name, col in (("by_source", "source"), ("by_city", "city")):
            expl[name] = cell_table(df, [col], periods, n_boot_exploratory, min_n=100)
    finally:
        conn.close()

    _write_csv(out / "cells.csv", cells)
    _write_csv(out / "candidates.csv", cand_df if len(cand_df) else pd.DataFrame(columns=list(cells.columns)))
    vcols = cells[cells.period == "validation"]
    ckeys = {(r.kind, r.snap, r.side, r.bucket) for r in cand_rows}
    _write_csv(out / "validation.csv", vcols[[k in ckeys for k in zip(vcols.kind, vcols.snap, vcols.side,
                                                                      vcols.bucket)]])
    _write_csv(out / "exploratory_by_source.csv", expl["by_source"])
    _write_csv(out / "exploratory_by_city.csv", expl["by_city"])
    _write_csv(out / "exploratory_bracket_sums.csv",
               pd.DataFrame(brackets, columns=["event", "series", "city", "day", "snapshot", "n_markets",
                                               "yes_bundle_profit10", "no_bundle_profit10"]))
    run_info = {"elapsed_s": round(time.monotonic() - t0, 1), "peak_rss_mb": round(peak_rss_mb() or 0, 1),
                "fills": fills, "n_boot": n_boot, "n_boot_exploratory": n_boot_exploratory, "seed": SEED}
    size_info = None if size is None else {k: v for k, v in size.items() if k != "cells"}
    verdict_doc = {"verdict": verdict, "exploratory_note": "none of the exploratory files affect the verdict",
                   "analysed": {"events": w1["events_analysed"], "markets": w1["markets_analysed"],
                                "days": len(days), "discovery_days": len(periods["discovery"]),
                                "validation_days": len(periods["validation"])},
                   "n_candidates": len(cand_rows), "n_validation_pass": len(passing),
                   "n_pass_all": len(all_pass), "criteria": {
                       "candidate": "discovery mean>0, 2.5th pct>0, n>=200, dates>=30",
                       "validation": "validation mean>0, 5th pct>0, n>=100",
                       "size": f">= ${SIZE_MIN_PER_WEEK}/week at 10 contracts, trade-print confirmed"},
                   "cells": verdict_cells, "size_check": size_info, "bracket_sums": bsum, "run": run_info}
    (out / "verdict.json").write_text(json.dumps(verdict_doc, indent=2, default=float), encoding="utf-8")
    dq["run"] = run_info
    (out / "dq.json").write_text(json.dumps(dq, indent=2, default=float), encoding="utf-8")
    print(f"verdict: {verdict}  | events analysed {w1['events_analysed']} (excluded {w1['excluded_events']}, "
          f"incomplete {w1['events_candles_incomplete']}) | candidates {len(cand_rows)} | validation pass {len(passing)} "
          f"| W2 gap share {dq['W2']['gap_share']} | W3 crossed {dq['W3']['crossed_quotes']} | W4 disagree "
          f"{w4['disagree']}/{w4['markets_checked']} | bracket profitable yes/no "
          f"{bsum['yes_bundle_profitable']}/{bsum['no_bundle_profitable']} of {bsum['event_snapshots_evaluated']} "
          f"| {run_info['elapsed_s']}s, peak RSS {run_info['peak_rss_mb']} MB")
    return verdict_doc
