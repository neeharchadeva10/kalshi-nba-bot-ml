"""Collector for research log #29 (reports/output/nba_model/PREREGISTRATION.md).
KXNBAPTS / KXNBASPREAD / KXNBAWINMARGIN markets for 2025-26 games -> data/nba_model.sqlite.
Public GET only, through KalshiClient (cached raw responses). No candles needed.
Run:  KALSHI_ENV=prod .venv/Scripts/python.exe reports/output/nba_model/collect_props.py
"""
import json
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from kpm.clients.kalshi import KalshiClient  # noqa: E402
from kpm.core.timeutil import parse_iso  # noqa: E402

DB = Path(__file__).resolve().parents[3] / "data" / "nba_model.sqlite"
SERIES = ["KXNBAPTS", "KXNBASPREAD", "KXNBAWINMARGIN"]
D_LO, D_HI = date(2025, 10, 21), date(2026, 6, 30)
MON = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
SCHEMA = """
CREATE TABLE IF NOT EXISTS mk (ticker TEXT PRIMARY KEY, series TEXT, tier TEXT, event_ticker TEXT, game_code TEXT,
  game_date TEXT, team TEXT, player_key TEXT, player_name TEXT, title TEXT, yes_sub_title TEXT, no_sub_title TEXT,
  floor_strike REAL, result TEXT, status TEXT, close_ts INTEGER, expiration_value TEXT, raw_json TEXT);
CREATE TABLE IF NOT EXISTS parse_fail (ticker TEXT, series TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS raw_responses (request_hash TEXT PRIMARY KEY, url TEXT NOT NULL, params TEXT,
  path TEXT NOT NULL, http_status INTEGER, fetched_ts INTEGER NOT NULL);
"""
EV = re.compile(r"^(KXNBA[A-Z]+)-(\d{2})([A-Z]{3})(\d{2})([A-Z0-9]+)$")


def parse_event(ev):
    """'KXNBAPTS-26JUN13NYKSAS' -> (series, date, 'NYKSAS') or None."""
    m = EV.match(ev or "")
    if not m or m.group(3) not in MON:
        return None
    try:
        return m.group(1), date(2000 + int(m.group(2)), MON[m.group(3)], int(m.group(4))), m.group(5)
    except ValueError:
        return None


def parse_pts_ticker(ticker, game_code):
    """'KXNBAPTS-26JUN13NYKSAS-SASVWEMBANYAMA1-40' -> (team 'SAS', player_key 'VWEMBANYAMA1').
    Part 3 starts with one of the game's two team codes (AWAYHOME, 3 letters each); strict: must match."""
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


def main():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    cl = KalshiClient(conn)
    print("cutoff", cl.cutoff(), flush=True)
    for s in SERIES:
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
                team = pk = None
                if s == "KXNBAPTS":
                    pp = parse_pts_ticker(m["ticker"], gc)
                    if pp is None:
                        conn.execute("INSERT INTO parse_fail VALUES (?,?,?)", (m["ticker"], s, "team/player parse"))
                    else:
                        team, pk = pp
                title = m.get("yes_sub_title") or m.get("title")
                conn.execute("INSERT OR REPLACE INTO mk VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    m["ticker"], s, "historical" if hist else "live", m["event_ticker"], gc, d.isoformat(), team, pk,
                    name_from_title(title) if s == "KXNBAPTS" else None, m.get("title"), m.get("yes_sub_title"),
                    m.get("no_sub_title"), m.get("floor_strike"), m.get("result") or None, m.get("status"),
                    parse_iso(m.get("close_time")), m.get("expiration_value"), json.dumps(m, sort_keys=True)))
                kept += 1
            conn.commit()
            print(s, "historical" if hist else "live", "seen", seen, "in 2025-26 window", kept, flush=True)
    cl.http.flush_index(conn)
    conn.commit()
    for r in conn.execute("SELECT series, tier, count(*), sum(result IS NULL) FROM mk GROUP BY 1,2"):
        print(r)
    print("parse failures:", conn.execute("SELECT series, reason, count(*) FROM parse_fail GROUP BY 1,2").fetchall())
    print("DONE", cl.http.stats)


if __name__ == "__main__":
    main()
