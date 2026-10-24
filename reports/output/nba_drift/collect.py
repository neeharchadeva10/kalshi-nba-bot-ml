"""Collector for research log #24 (reports/output/nba_drift/PREREGISTRATION.md).
KXNBAGAME settled markets of the 2025-26 season -> data/nba_drift.sqlite. Public GET only, via KalshiClient (cached).
Run:  KALSHI_ENV=prod .venv/Scripts/python.exe reports/output/nba_drift/collect.py [--limit N]
Hourly candles: [max(open, tip-50h), tip]  (2 h extra history so the T-24h entry has its 24h lookback);
1-minute candles: [tip-20m, tip].  tip = occurrence_datetime - 3h.
"""
import json
import sqlite3
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from kpm.clients.kalshi import KalshiClient  # noqa: E402
from kpm.core.timeutil import parse_iso  # noqa: E402

DB = Path(__file__).resolve().parents[3] / "data" / "nba_drift.sqlite"
SERIES = "KXNBAGAME"
LO = int(datetime(2025, 10, 1, tzinfo=timezone.utc).timestamp())
HI = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp())
SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (ticker TEXT PRIMARY KEY, event_ticker TEXT, title TEXT, yes_sub_title TEXT,
  status TEXT, result TEXT, open_ts INTEGER, close_ts INTEGER, occurrence_ts INTEGER, occ_source TEXT, expected_exp_ts INTEGER,
  settled_ts INTEGER, volume REAL, raw_json TEXT);
CREATE TABLE IF NOT EXISTS events (event_ticker TEXT PRIMARY KEY, title TEXT);
CREATE TABLE IF NOT EXISTS candles (ticker TEXT, kind TEXT, ts INTEGER, bid REAL, ask REAL, volume REAL,
  PRIMARY KEY (ticker, kind, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS done (ticker TEXT, step TEXT, n INTEGER, PRIMARY KEY (ticker, step));
CREATE TABLE IF NOT EXISTS raw_responses (request_hash TEXT PRIMARY KEY, url TEXT NOT NULL, params TEXT,
  path TEXT NOT NULL, http_status INTEGER, fetched_ts INTEGER NOT NULL);
"""


def f(d, k):
    v = (d or {}).get(k)
    return float(v) if v not in (None, "") else None


def candle_rows(ticker, kind, cs):
    return [(ticker, kind, int(c["end_period_ts"]), f(c.get("yes_bid"), "close"), f(c.get("yes_ask"), "close"),
             float(c["volume"]) if c.get("volume") not in (None, "") else None) for c in cs]


def main(limit=None):
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    cl = KalshiClient(conn)
    print("cutoff", cl.cutoff(), flush=True)
    n = 0
    for m in cl.markets(SERIES, historical=True):
        occ_raw = parse_iso(m.get("occurrence_datetime"))
        exp = parse_iso(m.get("expected_expiration_time"))
        # most historical markets carry no occurrence_datetime; there expected_expiration_time (== occurrence where both exist)
        occ = occ_raw if occ_raw is not None else exp
        if occ is None or not (LO <= occ < HI):
            continue
        conn.execute("INSERT OR REPLACE INTO markets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            m["ticker"], m["event_ticker"], m.get("title"), m.get("yes_sub_title"), m.get("status"),
            m.get("result") or None, parse_iso(m.get("open_time")), parse_iso(m.get("close_time")), occ,
            "occurrence" if occ_raw is not None else "expected_exp", exp, parse_iso(m.get("settlement_ts")),
            float(m["volume_fp"]) if m.get("volume_fp") else None, json.dumps(m, sort_keys=True)))
        n += 1
    conn.commit()
    print("historical markets in scope:", n, flush=True)
    # event titles ("X at Y"): historical market titles are 'Team wins' style, so read the events endpoint per event
    evs = [r[0] for r in conn.execute("SELECT DISTINCT event_ticker FROM markets WHERE event_ticker NOT IN (SELECT event_ticker FROM events)")]
    for et in evs:
        try:
            e = cl.http.get(f"/events/{et}")
            conn.execute("INSERT OR REPLACE INTO events VALUES (?,?)", (et, (e.get("event") or {}).get("title")))
        except Exception as ex:  # noqa: BLE001
            conn.execute("INSERT OR REPLACE INTO events VALUES (?,?)", (et, None))
            print("event fail", et, str(ex)[:120], flush=True)
        cl.http.flush_index(conn)
    conn.commit()
    done = {(a, b) for a, b in conn.execute("SELECT ticker, step FROM done")}
    rows = conn.execute("SELECT ticker, open_ts, occurrence_ts FROM markets ORDER BY occurrence_ts").fetchall()
    if limit:
        rows = rows[:limit]
    jobs = []
    for t, op, occ in rows:
        tip = occ - 3 * 3600
        if (t, "hour") not in done:
            jobs.append((t, "hour", max(op or 0, tip - 50 * 3600), tip, 60))
        if (t, "min") not in done:
            jobs.append((t, "min", tip - 20 * 60, tip, 1))
    print("jobs", len(jobs), flush=True)

    def fetch(j):
        t, kind, s, e, iv = j
        return j, cl.candlesticks(SERIES, t, s, e, iv, True)

    t0 = time.time()
    it = iter(jobs)
    k = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        infl = set()
        while True:
            while len(infl) < 18 and (j := next(it, None)) is not None:
                infl.add(pool.submit(fetch, j))
            if not infl:
                break
            fin, infl = wait(infl, return_when=FIRST_COMPLETED)
            for fu in fin:
                j, cs = fu.result()
                conn.executemany("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?)", candle_rows(j[0], j[1], cs))
                conn.execute("INSERT OR REPLACE INTO done VALUES (?,?,?)", (j[0], j[1], len(cs)))
                k += 1
                if k % 200 == 0:
                    cl.http.flush_index(conn)
                    conn.commit()
                    el = time.time() - t0
                    print(f"{k}/{len(jobs)} elapsed {el:.0f}s eta {el / k * (len(jobs) - k):.0f}s", flush=True)
    cl.http.flush_index(conn)
    conn.commit()
    print("DONE", k, "jobs", cl.http.stats, flush=True)


if __name__ == "__main__":
    lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    main(lim)
