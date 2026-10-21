"""Research log #37 downloader (read-only, cached). KXNBATOTAL definitions + results for every market, and per market
ONE hourly candle: the last candle ending at or before T-60min (request window T-4h..T-1h, filtered client-side).
T = scheduled start = KXNBAGAME occurrence - 3h (data/nba_drift.sqlite, read-only); for games not in that table
(2026-27) T = the KXNBATOTAL market's own occurrence_datetime (or expected_expiration_time) - 3h, same rule collect.py used.
Resumable: re-run to continue.  Run: .venv/Scripts/python.exe reports/output/nba_totals_pace/download.py
"""
import json
import sqlite3
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kpm.clients.kalshi import KalshiClient  # noqa: E402
from kpm.core.timeutil import parse_iso  # noqa: E402

HERE = Path(__file__).resolve().parent
DB = HERE / "totals.sqlite"
DRIFT = ROOT / "data" / "nba_drift.sqlite"
SERIES = "KXNBATOTAL"
SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (ticker TEXT PRIMARY KEY, event_ticker TEXT, tier TEXT, status TEXT, result TEXT,
  strike_type TEXT, floor_strike REAL, expiration_value TEXT, open_ts INTEGER, close_ts INTEGER, own_occ_ts INTEGER,
  settled_ts INTEGER, volume REAL, title TEXT, rules_primary TEXT);
CREATE TABLE IF NOT EXISTS candles (ticker TEXT, end_ts INTEGER, bid REAL, ask REAL, volume REAL, raw TEXT,
  PRIMARY KEY (ticker, end_ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS done (ticker TEXT PRIMARY KEY, n INTEGER, note TEXT, T INTEGER);
CREATE TABLE IF NOT EXISTS raw_responses (request_hash TEXT PRIMARY KEY, url TEXT NOT NULL, params TEXT,
  path TEXT NOT NULL, http_status INTEGER, fetched_ts INTEGER NOT NULL);
"""


def fl(v):
    return float(v) if v not in (None, "") else None


def candle_quote(c):
    """(bid_close, ask_close, volume) from a Kalshi candle dict; price fields are dollar strings."""
    def px(side):
        d = c.get(side) or {}
        for k in ("close_dollars", "close"):
            if d.get(k) not in (None, ""):
                return float(d[k])
        return None
    v = c.get("volume_fp", c.get("volume"))
    return px("yes_bid"), px("yes_ask"), fl(v)


def main():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    cl = KalshiClient(conn)
    print("cutoff", cl.cutoff(), flush=True)
    for tier, hist in (("historical", True), ("live", False)):
        n = 0
        for m in cl.markets(SERIES, historical=hist):
            occ = parse_iso(m.get("occurrence_datetime")) or parse_iso(m.get("expected_expiration_time"))
            conn.execute("INSERT OR REPLACE INTO markets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                m["ticker"], m["event_ticker"], tier, m.get("status"), m.get("result") or None, m.get("strike_type"),
                fl(m.get("floor_strike")), m.get("expiration_value"), parse_iso(m.get("open_time")),
                parse_iso(m.get("close_time")), occ, parse_iso(m.get("settlement_ts")),
                fl(m.get("volume_fp")), m.get("title"), m.get("rules_primary")))
            n += 1
        conn.commit()
        print(tier, "markets listed:", n, flush=True)
    cl.http.flush_index(conn)
    d = sqlite3.connect(f"file:{DRIFT.as_posix()}?mode=ro", uri=True)
    start = {et.split("-", 1)[1]: occ - 3 * 3600 for et, occ in d.execute(
        "SELECT event_ticker, MIN(occurrence_ts) FROM markets GROUP BY event_ticker")}
    # Amendment 2.3: for 2025-26 games T = min(base T, hoopR tip)
    import lib  # noqa: E402
    import prep  # noqa: E402
    hoopr = prep.hoopr_games(2026)
    try:
        conn.execute("ALTER TABLE done ADD COLUMN T INTEGER")
    except sqlite3.OperationalError:
        pass
    done = {r[0]: (r[1], r[2], r[3]) for r in conn.execute("SELECT ticker, n, note, T FROM done")}
    rows = conn.execute("SELECT ticker, event_ticker, tier, open_ts, own_occ_ts FROM markets").fetchall()
    now = time.time()
    jobs, refetch, not_played, changed = [], 0, 0, []
    for t, et, tier, op, own in rows:
        pe = lib.parse_event(et)
        base = start.get(et.split("-", 1)[1])
        if base is None and own is not None:
            base = own - 3 * 3600
        if base is None:
            conn.execute("INSERT OR REPLACE INTO done VALUES (?,?,?,?)", (t, 0, "no_start", None))
            continue
        T = base
        if base < prep.SEASON_SPLIT_TS and pe:
            hm = prep.match_hoopr(hoopr, *pe)
            if hm is not None:
                T, _ = prep.schedule_T(base, int(hm.tip))
        prev = done.get(t)
        redo = False
        if prev is not None:
            n, note, Tprev = prev
            Tprev = base if Tprev is None else Tprev   # rows written before this fix used the base T
            if Tprev != T:
                redo = True
                changed.append(t)
            elif tier == "live" and n == 0 and note == "ok":
                redo = True            # may have been fetched before the game was played
            if not redo:
                continue
            refetch += 1
        if op is not None and op > T - 3600:
            conn.execute("INSERT OR REPLACE INTO done VALUES (?,?,?,?)", (t, 0, "opened_after_T-60m", T))
            continue
        if T - 3600 > now:             # candle not yet closed: do not fetch, do not mark done
            not_played += 1
            conn.execute("DELETE FROM done WHERE ticker=?", (t,))
            continue
        jobs.append((t, tier == "historical", T, prev is not None))
    conn.commit()
    print("candle jobs", len(jobs), "of which refetch", refetch, "T-changed tickers", len(changed),
          "not yet played (left undone)", not_played, flush=True)
    print("T-changed:", sorted({c.rsplit('-', 1)[0] for c in changed}), flush=True)

    def fetch(j):
        t, hist, T, redo = j
        path = (f"/historical/markets/{t}/candlesticks" if hist
                else f"/series/{SERIES}/markets/{t}/candlesticks")
        if redo:   # bypass any cached (possibly pre-game, empty) response
            page = cl.http.get(path, {"start_ts": T - 4 * 3600, "end_ts": T - 3600, "period_interval": 60},
                               use_cache=False)
            cs = sorted(page.get("candlesticks", []), key=lambda c: c["end_period_ts"])
        else:
            cs = cl.candlesticks(SERIES, t, T - 4 * 3600, T - 3600, 60, hist)
        return j, [c for c in cs if c["end_period_ts"] <= T - 3600]

    t0 = time.time()
    it = iter(jobs)
    k = 0
    with ThreadPoolExecutor(max_workers=20) as pool:
        infl = set()
        while True:
            while len(infl) < 40 and (j := next(it, None)) is not None:
                infl.add(pool.submit(fetch, j))
            if not infl:
                break
            fin, infl = wait(infl, return_when=FIRST_COMPLETED)
            for fu in fin:
                j, cs = fu.result()
                conn.execute("DELETE FROM candles WHERE ticker=?", (j[0],)) if j[3] else None
                for c in cs:
                    b, a, v = candle_quote(c)
                    conn.execute("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?)",
                                 (j[0], int(c["end_period_ts"]), b, a, v, json.dumps(c, sort_keys=True)))
                conn.execute("INSERT OR REPLACE INTO done VALUES (?,?,?,?)", (j[0], len(cs), "ok", j[2]))
                k += 1
                if k % 250 == 0:
                    cl.http.flush_index(conn)
                    conn.commit()
                    el = time.time() - t0
                    print(f"{k}/{len(jobs)} elapsed {el:.0f}s eta {el / k * (len(jobs) - k):.0f}s", flush=True)
    cl.http.flush_index(conn)
    conn.commit()
    print("DONE", k, cl.http.stats, flush=True)


if __name__ == "__main__":
    main()
