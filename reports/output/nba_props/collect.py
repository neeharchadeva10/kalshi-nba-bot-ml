"""Collector for research log #36 (reports/output/nba_props/PREREGISTRATION.md). Public GET only via KalshiClient (cached).

Phase 1 (markets):  KXNBAPTS / KXNBAREB / KXNBAAST / KXNBA3PT definitions + results, 2025-26, historical + live tiers
                    -> reports/output/nba_props/nba_props.sqlite  table mk
Phase 2 (candles):  per market ONE hourly-candle request [max(open, T-240h), T-1h]  (T = scheduled start = occurrence - 3 h);
                    all returned candles are stored in table q; the decision quote is the last candle with
                    end_period_ts <= T - 3600 (chosen in data.py, not here).  Resumable (table done), <= ~6.7 req/s.
Run:  KALSHI_ENV=prod .venv/Scripts/python.exe reports/output/nba_props/collect.py markets
      KALSHI_ENV=prod .venv/Scripts/python.exe reports/output/nba_props/collect.py candles [--limit N]
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))
from kpm.clients.kalshi import KalshiClient  # noqa: E402
from kpm.clients.http import HttpError  # noqa: E402
from kpm.core.timeutil import parse_iso  # noqa: E402

DB = HERE / "nba_props.sqlite"
DRIFT_DB = ROOT / "data" / "nba_drift.sqlite"
SERIES = {"KXNBAPTS": "PTS", "KXNBAREB": "REB", "KXNBAAST": "AST", "KXNBA3PT": "3PT"}
D_LO, D_HI = date(2025, 10, 21), date(2026, 6, 30)
MON = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
EV = re.compile(r"^(KXNBA[A-Z0-9]+)-(\d{2})([A-Z]{3})(\d{2})([A-Z0-9]+)$")
MIN_INTERVAL_S = 0.33          # ~3.3 requests/s: measured 2026-10-21, >= ~5 req/s gets heavy 429s (another builder downloads too)
WORKERS = 6
SCHEMA = """
CREATE TABLE IF NOT EXISTS mk (ticker TEXT PRIMARY KEY, series TEXT, stat TEXT, tier TEXT, event_ticker TEXT,
  game_code TEXT, game_date TEXT, team TEXT, player_key TEXT, player_name TEXT, title TEXT, floor_strike REAL,
  result TEXT, status TEXT, occ_ts INTEGER, exp_ts INTEGER, close_ts INTEGER, open_ts INTEGER, volume REAL, raw_json TEXT);
CREATE TABLE IF NOT EXISTS parse_fail (ticker TEXT, series TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS q (ticker TEXT, end_ts INTEGER, bid REAL, ask REAL, volume REAL, PRIMARY KEY (ticker, end_ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS done (ticker TEXT PRIMARY KEY, n INTEGER, win_lo INTEGER, win_hi INTEGER);
CREATE TABLE IF NOT EXISTS raw_responses (request_hash TEXT PRIMARY KEY, url TEXT NOT NULL, params TEXT,
  path TEXT NOT NULL, http_status INTEGER, fetched_ts INTEGER NOT NULL);
"""


def parse_event(ev):
    """'KXNBAREB-26JUN13NYKSAS' -> (series, date, 'NYKSAS') or None."""
    m = EV.match(ev or "")
    if not m or m.group(3) not in MON:
        return None
    try:
        return m.group(1), date(2000 + int(m.group(2)), MON[m.group(3)], int(m.group(4))), m.group(5)
    except ValueError:
        return None


def parse_prop_ticker(ticker, game_code):
    """'KXNBAPTS-26JUN13NYKSAS-SASVWEMBANYAMA1-40' -> (team 'SAS', player_key 'VWEMBANYAMA1'); None if it does not parse."""
    parts = ticker.split("-")
    if len(parts) < 4 or len(game_code) != 6:
        return None
    p3 = parts[2]
    for tc in (game_code[:3], game_code[3:]):
        if p3.startswith(tc) and len(p3) > 3:
            return tc, p3[3:]
    return None


def name_from_title(t):
    return (t or "").split(":", 1)[0].strip() or None


def f(d, k):
    v = (d or {}).get(k)
    return float(v) if v not in (None, "") else None


def phase_markets(conn, cl):
    print("cutoff", cl.cutoff(), flush=True)
    for s, stat in SERIES.items():
        for hist in (True, False):
            seen = kept = 0
            for m in cl.markets(s, historical=hist):
                seen += 1
                pe = parse_event(m.get("event_ticker"))
                if pe is None:
                    conn.execute("INSERT INTO parse_fail VALUES (?,?,?)", (m.get("ticker"), s, "event ticker"))
                    continue
                _, d, gc = pe
                if not (D_LO <= d <= D_HI):
                    continue
                pp = parse_prop_ticker(m["ticker"], gc)
                if pp is None:
                    conn.execute("INSERT INTO parse_fail VALUES (?,?,?)", (m["ticker"], s, "team/player parse"))
                    continue
                team, pk = pp
                title = m.get("title")
                conn.execute("INSERT OR REPLACE INTO mk VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    m["ticker"], s, stat, "historical" if hist else "live", m["event_ticker"], gc, d.isoformat(), team, pk,
                    name_from_title(m.get("yes_sub_title") or title), title, m.get("floor_strike"), m.get("result") or None,
                    m.get("status"), parse_iso(m.get("occurrence_datetime")), parse_iso(m.get("expected_expiration_time")),
                    parse_iso(m.get("close_time")), parse_iso(m.get("open_time")),
                    float(m["volume_fp"]) if m.get("volume_fp") else None, json.dumps(m, sort_keys=True)))
                kept += 1
            conn.commit()
            print(s, "historical" if hist else "live", "seen", seen, "in 2025-26 window", kept, flush=True)
    cl.http.flush_index(conn)
    conn.commit()
    for r in conn.execute("SELECT series, tier, count(*), sum(result IS NULL) FROM mk GROUP BY 1,2"):
        print(r)
    print("parse failures:", conn.execute("SELECT series, reason, count(*) FROM parse_fail GROUP BY 1,2").fetchall())


def game_tips():
    """{(date_iso_of_ticker, game_code): tip_ts} from the KXNBAGAME markets of #24 (occurrence - 3 h)."""
    out = {}
    if not DRIFT_DB.exists():
        return out
    c = sqlite3.connect(f"file:{DRIFT_DB.as_posix()}?mode=ro", uri=True)
    for ev, occ in c.execute("SELECT event_ticker, max(occurrence_ts) FROM markets GROUP BY event_ticker"):
        pe = parse_event(ev)
        if pe:
            out[(pe[1].isoformat(), pe[2])] = occ - 3 * 3600
    c.close()
    return out


def tip_of(row_game_date, game_code, occ_ts, exp_ts, tips):
    """(tip_ts, source). KXNBAGAME-matched first; else the prop's own occurrence_datetime / expected_expiration - 3 h."""
    k = (row_game_date, game_code)
    if k in tips:
        return tips[k], "kxnbagame"
    if occ_ts is not None:
        return occ_ts - 3 * 3600, "prop_occurrence"
    if exp_ts is not None:
        return exp_ts - 3 * 3600, "prop_expected_exp"
    return None, None


def phase_candles(conn, cl, limit=None):
    cl.http.min_interval_s = MIN_INTERVAL_S
    tips = game_tips()
    done = {r[0] for r in conn.execute("SELECT ticker FROM done")}
    rows = conn.execute("SELECT ticker, series, game_date, game_code, occ_ts, exp_ts, result, tier, open_ts FROM mk "
                        "ORDER BY game_date, ticker").fetchall()
    jobs, no_tip = [], 0
    for t, s, gd, gc, occ, exp, res, tier, op in rows:
        if t in done:
            continue
        if res == "scalar":          # fair-price (inactive player) settlements are excluded by the pre-registration: no quote needed
            continue
        tip, _ = tip_of(gd, gc, occ, exp, tips)
        if tip is None:
            no_tip += 1
            continue
        # Kalshi candles exist only for periods in which the book changed or traded, so "no candle in T-4h..T-1h" means an
        # unchanged book, not a missing one.  The decision quote is the last candle at/before T-60min whatever its age
        # (as #24: no staleness limit); one request covers the whole life of the market (<= 5000 hourly candles).
        lo = max(op, tip - 240 * 3600) if op else tip - 240 * 3600
        jobs.append((t, s, lo, tip - 3600, tier == "historical"))
    if limit:
        jobs = jobs[:limit]
    print("markets", len(rows), "already done", len(done), "no tip", no_tip, "jobs", len(jobs), flush=True)

    def fetch(j):
        t, s, lo, hi, hist = j
        try:
            return j, cl.candlesticks(s, t, lo, hi, 60, hist), None
        except HttpError as e:
            return j, None, e.status
        except Exception as e:  # noqa: BLE001
            return j, None, "ERR " + str(e)[:100]

    t0 = time.time()
    it = iter(jobs)
    k = errs = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        infl = set()
        while True:
            while len(infl) < 2 * WORKERS and (j := next(it, None)) is not None:
                infl.add(pool.submit(fetch, j))
            if not infl:
                break
            fin, infl = wait(infl, return_when=FIRST_COMPLETED)
            for fu in fin:
                j, cs, err = fu.result()
                t, s, lo, hi, hist = j
                if err is None:
                    conn.executemany("INSERT OR REPLACE INTO q VALUES (?,?,?,?,?)", [
                        (t, int(c["end_period_ts"]), f(c.get("yes_bid"), "close"), f(c.get("yes_ask"), "close"),
                         f(c, "volume")) for c in cs])
                    conn.execute("INSERT OR REPLACE INTO done VALUES (?,?,?,?)", (t, len(cs), lo, hi))
                elif err == 404:
                    conn.execute("INSERT OR REPLACE INTO done VALUES (?,?,?,?)", (t, -1, lo, hi))   # market not found: no quote
                else:
                    errs += 1
                    print("fail", t, err, flush=True)          # not marked done -> retried on the next run
                k += 1
                if k % 200 == 0:
                    cl.http.flush_index(conn)
                    conn.commit()
                    el = time.time() - t0
                    print(f"{k}/{len(jobs)} elapsed {el:.0f}s eta {el / k * (len(jobs) - k):.0f}s errs {errs} {cl.http.stats}",
                          flush=True)
    cl.http.flush_index(conn)
    conn.commit()
    print("DONE", k, "jobs, errors", errs, cl.http.stats, "elapsed", round(time.time() - t0), "s", flush=True)


def main():
    conn = sqlite3.connect(DB, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    cl = KalshiClient(conn)
    ph = sys.argv[1] if len(sys.argv) > 1 else ""
    if ph == "markets":
        phase_markets(conn, cl)
    elif ph == "candles":
        lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
        phase_candles(conn, cl, lim)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
