"""#49 data access (all read-only): games from #44's events_list.csv (T = scheduled tip), KXNBATOTAL T-1h quotes from
#37's totals.sqlite, team/1H quotes from this study's candles.sqlite.  Streams per game; nothing big is held in memory."""
from __future__ import annotations

import csv
import sqlite3

import mismatch as MM

ROOT, HERE = MM.ROOT, MM.HERE
TOTALS = ROOT / "reports/output/nba_totals_pace/totals.sqlite"


def load_events(split):
    """Games of the amended window from population.csv (build_population.py); split in discovery | unseen | all."""
    rows = list(csv.DictReader(open(HERE / "population.csv")))
    if split != "all":
        rows = [r for r in rows if r["split"] == split]
    return sorted(rows, key=lambda r: (int(r["T"]), r["event"]))


def totals_conn():
    return sqlite3.connect(f"file:{TOTALS.as_posix()}?mode=ro", uri=True)


def game_total_quotes(tc, event, T):
    """KXNBATOTAL quotes at T-1h: [(ticker, strike, bid, ask, end_ts)], last candle ending at or before T-3600 per ticker.
    The totals.sqlite candle window is T-4h..T-1h, so 'at or before' is already guaranteed; re-checked here."""
    out = []
    for tk, strike, res, ev_val in tc.execute(
            "SELECT ticker, floor_strike, result, expiration_value FROM markets WHERE event_ticker=? ORDER BY floor_strike",
            (event,)):
        c = tc.execute("SELECT end_ts, bid, ask FROM candles WHERE ticker=? AND end_ts<=? ORDER BY end_ts DESC LIMIT 1",
                       (tk, T - 3600)).fetchone()
        if c is None or c[1] is None or c[2] is None:
            continue
        out.append((tk, strike, c[1], c[2], c[0]))
    return out


def game_total_median(tc, event, T):
    q = game_total_quotes(tc, event, T)
    return MM.implied_median([(s, b, a) for _, s, b, a, _ in q]), q


def markets_for(mk, event):
    """{'team': [(ticker, team, strike)], '1H': [(ticker, strike)]} for a KXNBATOTAL event, from markets.sqlite."""
    suf = MM.event_suffix(event)
    team = mk.execute("SELECT ticker, team, strike FROM markets WHERE event_ticker=? ORDER BY strike",
                      ("KXNBATEAMTOTAL-" + suf,)).fetchall()
    half = mk.execute("SELECT ticker, strike FROM markets WHERE event_ticker=? ORDER BY strike",
                      ("KXNBA1HTOTAL-" + suf,)).fetchall()
    return {"team": team, "1H": half}


def candle_db():
    db = sqlite3.connect(HERE / "candles.sqlite")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS candles (ticker TEXT, end_ts INTEGER, bid REAL, ask REAL, volume REAL,
        PRIMARY KEY (ticker,end_ts)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS done (ticker TEXT PRIMARY KEY, T INTEGER, n INTEGER, note TEXT);
    """)
    return db


def book_quotes(cdb, tickers_strikes, T):
    """[(ticker, strike)] -> [(ticker, strike, bid, ask, end_ts)] at the last candle ending <= T-3600."""
    out = []
    for tk, s in tickers_strikes:
        c = cdb.execute("SELECT end_ts, bid, ask FROM candles WHERE ticker=? AND end_ts<=? ORDER BY end_ts DESC LIMIT 1",
                        (tk, T - 3600)).fetchone()
        if c is None or c[1] is None or c[2] is None:
            continue
        out.append((tk, s, c[1], c[2], c[0]))
    return out
