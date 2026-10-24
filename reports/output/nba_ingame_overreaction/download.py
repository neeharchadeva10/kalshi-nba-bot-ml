"""Research log #46: tip..tip+5h 1-minute candles for every 2025-26 regular-season KXNBATOTAL market (public GET, cached under
data/raw/kalshi_1m_totals_ingame/, resumable). Single thread, 0.67 s interval (<=1.5 req/s), exponential backoff on 429
(CachedClient). Discovery dates first, then unseen. Progress in download_report.json.
Run: .venv/Scripts/python.exe <this file> [discovery|all]"""
import json
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C
from kpm.clients.http import CachedClient, HttpError
from kpm.core.config import settings

INTERVAL = 0.67


def px(c, side, k):
    v = (c.get(side) or {}).get(k)
    return float(v) if v not in (None, "") else None


def main(scope="all"):
    ev = pd.read_csv(C.INJ / "events_list.csv")
    ev = ev[ev.hoopr_tip.notna()].sort_values(["game_date_et", "event"])
    if scope == "discovery":
        ev = ev[ev.game_date_et <= C.DISC_LAST]
    inj = sqlite3.connect(f"file:{(C.INJ / 'candles.sqlite').as_posix()}?mode=ro", uri=True)
    mk = pd.read_sql("SELECT ticker, event, strike, result FROM mkts", inj)
    inj.close()
    byev = {e: g.ticker.tolist() for e, g in mk.groupby("event")}
    conn = sqlite3.connect(C.HERE / "candles_ingame.sqlite")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS candles (ticker TEXT, end_ts INTEGER, bid REAL, ask REAL, bid_open REAL, ask_open REAL,
       bid_low REAL, bid_high REAL, ask_low REAL, ask_high REAL, volume REAL, oi REAL, PRIMARY KEY (ticker,end_ts)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS done (ticker TEXT PRIMARY KEY, event TEXT, tip INTEGER, n INTEGER, note TEXT);""")
    done = {r[0] for r in conn.execute("SELECT ticker FROM done WHERE note IN ('ok','http_404')")}
    jobs = []
    for r in ev.itertuples():
        for t in byev.get(r.event, []):
            if t not in done:
                jobs.append((t, r.event, int(r.hoopr_tip), r.game_date_et))
    cl = CachedClient(settings()["kalshi"]["base_url"], C.RAW_NS, INTERVAL)
    t0, k, notes = time.time(), 0, {}
    print("jobs", len(jobs), flush=True)
    for t, e, tip, d in jobs:
        try:
            p = cl.get(f"/historical/markets/{t}/candlesticks",
                       {"start_ts": tip, "end_ts": tip + C.FETCH_SPAN_S, "period_interval": 1})
            cs, note = p.get("candlesticks", []), "ok"
        except HttpError as ex:
            cs, note = [], ("http_404" if ex.status == 404 else f"error_{ex.status}")
        except Exception as ex:  # noqa: BLE001
            cs, note = [], "error_" + type(ex).__name__
        for c in cs:
            conn.execute("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                t, int(c["end_period_ts"]), px(c, "yes_bid", "close"), px(c, "yes_ask", "close"), px(c, "yes_bid", "open"),
                px(c, "yes_ask", "open"), px(c, "yes_bid", "low"), px(c, "yes_bid", "high"), px(c, "yes_ask", "low"),
                px(c, "yes_ask", "high"), float(c["volume"]) if c.get("volume") not in (None, "") else None,
                float(c["open_interest"]) if c.get("open_interest") not in (None, "") else None))
        conn.execute("INSERT OR REPLACE INTO done VALUES (?,?,?,?,?)", (t, e, tip, len(cs), note))
        notes[note] = notes.get(note, 0) + 1
        k += 1
        if k % 100 == 0:
            conn.commit()
            el = time.time() - t0
            rep = {"scope": scope, "requested_this_run": k, "jobs_this_run": len(jobs), "notes": notes,
                   "elapsed_s": round(el), "client_stats": cl.stats, "last_date": d}
            (C.HERE / "download_report.json").write_text(json.dumps(rep, indent=1))
            print(k, len(jobs), round(el), notes, cl.stats, flush=True)
    conn.commit()
    el = time.time() - t0
    tot = dict(conn.execute("SELECT note, count(*) FROM done GROUP BY note").fetchall())
    (C.HERE / "download_report.json").write_text(json.dumps({
        "scope": scope, "complete": True, "requested_this_run": k, "notes_this_run": notes,
        "done_table_totals": tot, "elapsed_s": round(el), "client_stats": cl.stats}, indent=1))
    print("DONE", k, tot, flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all")
