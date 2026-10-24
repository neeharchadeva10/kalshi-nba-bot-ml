"""Research log #44: 1-minute candles for every 2025-26 regular-season KXNBATOTAL market, T-8h..T (public GET, cached under
data/raw/kalshi_1m_totals/, resumable).  Polite: shared 0.2 s interval (5 req/s), exponential backoff on 429 (CachedClient).
Writes events_list.csv (event, T, tip ...) and candles.sqlite.
Run: .venv/Scripts/python.exe reports/output/nba_injury_speed/download_candles.py
"""
import csv
import sqlite3
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import common as C
from common import HERE, ROOT, CANDLE_BACK_S

from kpm.clients.http import CachedClient, HttpError
from kpm.core.config import settings

SEASON_END_REG = "2026-04-13"     # last regular-season ET date (used only for events hoopR cannot place)


def build_events():
    import lib
    import prep
    totals = sqlite3.connect(f"file:{(ROOT / 'reports/output/nba_totals_pace/totals.sqlite').as_posix()}?mode=ro", uri=True)
    mk = totals.execute("SELECT ticker, event_ticker, own_occ_ts, result, floor_strike, strike_type FROM markets").fetchall()
    d = sqlite3.connect(f"file:{(ROOT / 'data/nba_drift.sqlite').as_posix()}?mode=ro", uri=True)
    start = {et.split("-", 1)[1]: occ - 3 * 3600 for et, occ in d.execute(
        "SELECT event_ticker, MIN(occurrence_ts) FROM markets GROUP BY event_ticker")}
    hoopr = prep.hoopr_games(2026)
    byev = {}
    for t, et, own, res, fs, st in mk:
        byev.setdefault(et, {"own": own, "markets": []})["markets"].append((t, res, fs, st))
    rows, skipped = [], []
    for et, v in sorted(byev.items()):
        pe = lib.parse_event(et)
        if pe is None:
            skipped.append((et, "unparsed"))
            continue
        date, away, home = pe
        base = start.get(et.split("-", 1)[1])
        src = "drift"
        if base is None and v["own"] is not None:
            base, src = v["own"] - 3 * 3600, "own"
        if base is None:
            skipped.append((et, "no_T"))
            continue
        if base >= prep.SEASON_SPLIT_TS:
            continue                                     # 2026-27
        hm = prep.match_hoopr(hoopr, date, away, home)
        tip, stype, gid = None, None, None
        T = base
        if hm is not None:
            tip, stype, gid = int(hm.tip), int(hm.season_type), int(hm.game_id)
            T, tag = prep.schedule_T(base, tip)
            if tag:
                src += "->hoopr_tip"
        regular = (stype == 2) if stype is not None else (C.et_date(T) <= SEASON_END_REG)
        if not regular:
            skipped.append((et, f"not_regular_season(type={stype})"))
            continue
        rows.append({"event": et, "game_date_et": C.et_date(T), "away": away, "home": home, "T": T, "T_src": src,
                     "hoopr_tip": tip, "hoopr_gid": gid, "season_type": stype, "n_markets": len(v["markets"])})
    return rows, skipped, byev


def main():
    rows, skipped, byev = build_events()
    with open(HERE / "events_list.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("events", len(rows), "skipped", len(skipped), skipped[:5], flush=True)
    conn = sqlite3.connect(HERE / "candles.sqlite")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS candles (ticker TEXT, end_ts INTEGER, bid REAL, ask REAL, bid_open REAL, ask_open REAL,
       bid_low REAL, bid_high REAL, ask_low REAL, ask_high REAL, volume REAL, oi REAL, PRIMARY KEY (ticker,end_ts)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS done (ticker TEXT PRIMARY KEY, event TEXT, T INTEGER, n INTEGER, note TEXT);
    CREATE TABLE IF NOT EXISTS mkts (ticker TEXT PRIMARY KEY, event TEXT, strike REAL, strike_type TEXT, result TEXT);
    """)
    jobs = []
    done = {r[0] for r in conn.execute("SELECT ticker FROM done WHERE note IN ('ok','http_404')")}
    for r in rows:
        for t, res, fs, st in byev[r["event"]]["markets"]:
            conn.execute("INSERT OR REPLACE INTO mkts VALUES (?,?,?,?,?)", (t, r["event"], fs, st, res))
            if t not in done:
                jobs.append((t, r["event"], r["T"]))
    conn.commit()
    print("markets", sum(len(byev[r["event"]]["markets"]) for r in rows), "jobs", len(jobs), flush=True)
    cl = CachedClient(settings()["kalshi"]["base_url"], C.RAW_NS, 0.2)

    def px(c, side, k):
        v = (c.get(side) or {}).get(k)
        return float(v) if v not in (None, "") else None

    def fetch(j):
        t, et, T = j
        try:
            p = cl.get(f"/historical/markets/{t}/candlesticks",
                       {"start_ts": T - CANDLE_BACK_S, "end_ts": T, "period_interval": 1})
        except HttpError as e:
            return j, None, f"http_{e.status}" if e.status == 404 else f"error_{e.status}"
        except Exception as e:   # noqa: BLE001
            return j, None, "error_" + type(e).__name__
        return j, [c for c in p.get("candlesticks", []) if c["end_period_ts"] <= T], "ok"

    t0, k = time.time(), 0
    it = iter(jobs)
    with ThreadPoolExecutor(max_workers=3) as pool:
        infl = set()
        while True:
            while len(infl) < 6 and (j := next(it, None)) is not None:
                infl.add(pool.submit(fetch, j))
            if not infl:
                break
            fin, infl = wait(infl, return_when=FIRST_COMPLETED)
            for fu in fin:
                j, cs, note = fu.result()
                if cs is not None:
                    for c in cs:
                        conn.execute("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                            j[0], int(c["end_period_ts"]), px(c, "yes_bid", "close"), px(c, "yes_ask", "close"),
                            px(c, "yes_bid", "open"), px(c, "yes_ask", "open"), px(c, "yes_bid", "low"),
                            px(c, "yes_bid", "high"), px(c, "yes_ask", "low"), px(c, "yes_ask", "high"),
                            float(c["volume"]) if c.get("volume") not in (None, "") else None,
                            float(c["open_interest"]) if c.get("open_interest") not in (None, "") else None))
                conn.execute("INSERT OR REPLACE INTO done VALUES (?,?,?,?,?)", (j[0], j[1], j[2], len(cs) if cs else 0, note))
                k += 1
                if k % 200 == 0:
                    conn.commit()
                    el = time.time() - t0
                    print(f"{k}/{len(jobs)} {el:.0f}s eta {el / k * (len(jobs) - k):.0f}s stats {cl.stats}", flush=True)
    conn.commit()
    print("DONE", k, cl.stats, flush=True)


if __name__ == "__main__":
    main()
