"""Data collector for research log #35 (reports/output/nba_late_fav/PREREGISTRATION.md).  Public GET only, via the cached KalshiClient.
Writes ONLY to reports/output/nba_late_fav/nlf.sqlite (nba_drift.sqlite is never opened).

  python collect.py markets     # KXNBAGAME markets with occurrence/expected-expiration in [2025-04-15, 2026-07-01)
  python collect.py candles     # 1-minute candles [tip-10m, settlement] per market (tip = occurrence - 3 h)
  python collect.py trades      # public prints in [E_X-1, E_X+61] for markets whose ask at E_X is in 70-95c (needs pbp+candles)
"""
import json
import sqlite3
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))
sys.path.insert(0, str(HERE))
from kpm.clients.kalshi import KalshiClient  # noqa: E402
from kpm.core.timeutil import parse_iso  # noqa: E402

DB = HERE / "nlf.sqlite"
SERIES = "KXNBAGAME"
LO = int(datetime(2025, 4, 15, tzinfo=timezone.utc).timestamp())
HI = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp())
SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (ticker TEXT PRIMARY KEY, event_ticker TEXT, yes_sub_title TEXT, status TEXT,
  result TEXT, open_ts INTEGER, close_ts INTEGER, occurrence_ts INTEGER, occ_source TEXT, settled_ts INTEGER);
CREATE TABLE IF NOT EXISTS candles (ticker TEXT, ts INTEGER, bid REAL, ask REAL, volume REAL, PRIMARY KEY (ticker, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS prints (trade_id TEXT PRIMARY KEY, ticker TEXT, ts REAL, yes_price REAL, count REAL, taker_side TEXT);
CREATE TABLE IF NOT EXISTS done (ticker TEXT, step TEXT, n INTEGER, PRIMARY KEY (ticker, step));
CREATE TABLE IF NOT EXISTS raw_responses (request_hash TEXT PRIMARY KEY, url TEXT NOT NULL, params TEXT,
  path TEXT NOT NULL, http_status INTEGER, fetched_ts INTEGER NOT NULL);
"""


def fnum(d, k):
    v = (d or {}).get(k)
    return float(v) if v not in (None, "") else None


def frac_s(iso):
    """Sub-second part of an ISO timestamp as a float."""
    if not iso or "." not in iso:
        return 0.0
    tail = iso.split(".", 1)[1]
    digits = "".join(ch for ch in tail if ch.isdigit())
    return float("0." + digits) if digits else 0.0


def connect():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def do_markets(conn, cl):
    n = 0
    for m in cl.markets(SERIES, historical=True):
        occ_raw = parse_iso(m.get("occurrence_datetime"))
        exp = parse_iso(m.get("expected_expiration_time"))
        occ = occ_raw if occ_raw is not None else exp      # same rule as nba_drift/collect.py
        if occ is None or not (LO <= occ < HI):
            continue
        conn.execute("INSERT OR REPLACE INTO markets VALUES (?,?,?,?,?,?,?,?,?,?)", (
            m["ticker"], m["event_ticker"], m.get("yes_sub_title"), m.get("status"), m.get("result") or None,
            parse_iso(m.get("open_time")), parse_iso(m.get("close_time")), occ,
            "occurrence" if occ_raw is not None else "expected_exp", parse_iso(m.get("settlement_ts"))))
        n += 1
    cl.http.flush_index(conn)
    conn.commit()
    print("markets in scope:", n, "cutoff", cl.cutoff(), flush=True)


def pool_run(jobs, fn, sink, cl, conn, workers=4):
    t0 = time.time()
    it = iter(jobs)
    k = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        infl = set()
        while True:
            while len(infl) < workers * 3 and (j := next(it, None)) is not None:
                infl.add(pool.submit(fn, j))
            if not infl:
                break
            fin, infl = wait(infl, return_when=FIRST_COMPLETED)
            for fu in fin:
                sink(fu.result())
                k += 1
                if k % 200 == 0:
                    cl.http.flush_index(conn)
                    conn.commit()
                    el = time.time() - t0
                    print(f"{k}/{len(jobs)} elapsed {el:.0f}s eta {el / k * (len(jobs) - k):.0f}s", flush=True)
    cl.http.flush_index(conn)
    conn.commit()
    print("DONE", k, cl.http.stats, flush=True)


def do_candles(conn, cl):
    done = {r[0] for r in conn.execute("SELECT ticker FROM done WHERE step='candles'")}
    rows = conn.execute("SELECT ticker, occurrence_ts, settled_ts, close_ts FROM markets ORDER BY occurrence_ts").fetchall()
    jobs = []
    for t, occ, st, cl_ts in rows:
        if t in done:
            continue
        tip = occ - 3 * 3600
        end = st or cl_ts or (tip + 6 * 3600)
        jobs.append((t, tip - 600, end))
    print("candle jobs", len(jobs), flush=True)

    def fetch(j):
        t, s, e = j
        return t, cl.candlesticks(SERIES, t, s, e, 1, True)

    def sink(res):
        t, cs = res
        conn.executemany("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?)", [
            (t, int(c["end_period_ts"]), fnum(c.get("yes_bid"), "close"), fnum(c.get("yes_ask"), "close"),
             fnum(c, "volume")) for c in cs])
        conn.execute("INSERT OR REPLACE INTO done VALUES (?,?,?)", (t, "candles", len(cs)))

    pool_run(jobs, fetch, sink, cl, conn)


def do_trades(conn, cl):
    import lib
    jobs = lib.trade_jobs(conn)          # [(ticker, min_ts, max_ts)]
    done = {r[0] for r in conn.execute("SELECT ticker FROM done WHERE step LIKE 'trades:%'")}  # unused marker (keyed below)
    done = {(a, b) for a, b in conn.execute("SELECT ticker, step FROM done WHERE step LIKE 'trades:%'")}
    jobs = [j for j in jobs if (j[0], f"trades:{j[1]}") not in done]
    print("trade jobs", len(jobs), flush=True)

    def fetch(j):
        t, lo, hi = j
        hist = cl.trades_historical(lo) and cl.trades_historical(hi)
        return j, list(cl.trades(t, lo, hi, hist))

    def sink(res):
        (t, lo, hi), trs = res
        conn.executemany("INSERT OR IGNORE INTO prints VALUES (?,?,?,?,?,?)", [
            (x["trade_id"], t, float(parse_iso(x["created_time"])) + frac_s(x["created_time"]),
             float(x["yes_price_dollars"]), float(x.get("count_fp") or x.get("count") or 0), x.get("taker_side") or "")
            for x in trs])
        conn.execute("INSERT OR REPLACE INTO done VALUES (?,?,?)", (t, f"trades:{lo}", len(trs)))

    pool_run(jobs, fetch, sink, cl, conn)


if __name__ == "__main__":
    step = sys.argv[1]
    conn = connect()
    cl = KalshiClient(conn)
    cl.http.min_interval_s = 0.15          # ~6.7 req/s shared across workers (another job is downloading too)
    {"markets": do_markets, "candles": do_candles, "trades": do_trades}[step](conn, cl)
