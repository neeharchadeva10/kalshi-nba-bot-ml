"""Collector for research log #20 (reports/output/nfl_margin_hist/PREREGISTRATION.md): NFL win-margin buckets vs the deep
winner / tie / spread markets, 1-minute YES bid/ask candles and public trades, written to data/nfl_margin.sqlite.

Public Kalshi GET endpoints only, through KalshiClient/CachedClient (every response is cached on disk). Resumable: a
(ticker, step) pair in mm_done is never fetched twice.   Run:  python -m kpm.collectors.nfl_margin_hist
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date
from pathlib import Path

from kpm.clients.kalshi import KalshiClient
from kpm.collectors.kalshi_spread import quote_row
from kpm.core import config, db
from kpm.core.config import settings
from kpm.core.prices import parse_dollars
from kpm.core.timeutil import now_ts, parse_iso

log = logging.getLogger(__name__)

DB_NAME = "nfl_margin.sqlite"
OUT_DIR = Path("reports/output/nfl_margin_hist")
MARGIN = "KXNFLWINMARGIN"
SERIES = (MARGIN, "KXNFLGAME", "KXNFLTIE", "KXNFLSPREAD")
FIRST_DAY, LAST_DAY = date(2026, 9, 1), date(2026, 10, 19)
EXCLUDED_EVENTS = frozenset({"26OCT19WASSF"})        # the live game that motivated the test (spec)
PRE_S, POST_S = 6 * 3600, 4 * 3600 + 1800            # candle / trade window: [kickoff - 6 h, kickoff + 4 h 30 min]
_EVENT = re.compile(r"^(?P<series>[A-Z]+)-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<teams>[A-Z]+)$")
_MONTHS = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}

SCHEMA = """
CREATE TABLE IF NOT EXISTS mm_markets (
    mid INTEGER PRIMARY KEY, ticker TEXT NOT NULL UNIQUE, series TEXT NOT NULL, game TEXT NOT NULL,
    event_ticker TEXT NOT NULL, kickoff_ts INTEGER, event_date TEXT, floor_strike REAL, cap_strike REAL,
    strike_type TEXT, open_ts INTEGER, close_ts INTEGER, settled_ts INTEGER, status TEXT, result TEXT,
    expected_exp_ts INTEGER, rules_primary TEXT, rules_secondary TEXT, raw_json TEXT NOT NULL,
    q_start_ts INTEGER, q_end_ts INTEGER);
CREATE INDEX IF NOT EXISTS ix_mm_markets_game ON mm_markets(game);
CREATE TABLE IF NOT EXISTS mm_quotes (mid INTEGER NOT NULL, ts INTEGER NOT NULL, bid_e4 INTEGER, ask_e4 INTEGER,
    volume REAL, PRIMARY KEY (mid, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS mm_trades (trade_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, ts REAL NOT NULL,
    yes_price REAL NOT NULL, count REAL NOT NULL, taker_side TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_mm_trades ON mm_trades(ticker, ts);
CREATE TABLE IF NOT EXISTS mm_done (ticker TEXT NOT NULL, step TEXT NOT NULL, ts REAL NOT NULL, detail TEXT,
    PRIMARY KEY (ticker, step));
CREATE TABLE IF NOT EXISTS mm_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS raw_responses (request_hash TEXT PRIMARY KEY, url TEXT NOT NULL, params TEXT,
    path TEXT NOT NULL, http_status INTEGER, fetched_ts INTEGER NOT NULL);
"""


def db_path() -> Path:
    return config.path("db_path").parent / DB_NAME


def parse_event(event_ticker: str) -> tuple[str, date] | None:
    """'KXNFLWINMARGIN-26OCT19WASSF' -> ('26OCT19WASSF', date(2026, 10, 19)); None when it does not parse."""
    m = _EVENT.match(event_ticker or "")
    if not m or m["mon"] not in _MONTHS:
        return None
    try:
        return m["yy"] + m["mon"] + m["dd"] + m["teams"], date(2000 + int(m["yy"]), _MONTHS[m["mon"]], int(m["dd"]))
    except ValueError:
        return None


def in_scope(event_ticker: str) -> bool:
    p = parse_event(event_ticker)
    return p is not None and FIRST_DAY <= p[1] <= LAST_DAY and p[0] not in EXCLUDED_EVENTS


def market_row(m: dict, series: str, game: str, kickoff: int | None, event_date: date) -> dict:
    return {
        "ticker": m["ticker"], "series": series, "game": game, "event_ticker": m["event_ticker"],
        "kickoff_ts": kickoff, "event_date": event_date.isoformat(),
        "floor_strike": float(m["floor_strike"]) if m.get("floor_strike") is not None else None,
        "cap_strike": float(m["cap_strike"]) if m.get("cap_strike") is not None else None,
        "strike_type": m.get("strike_type"), "open_ts": parse_iso(m.get("open_time")),
        "close_ts": parse_iso(m.get("close_time")), "settled_ts": parse_iso(m.get("settlement_ts")),
        "status": m.get("status"), "result": m.get("result") or None,
        "expected_exp_ts": parse_iso(m.get("expected_expiration_time")),
        "rules_primary": m.get("rules_primary"), "rules_secondary": m.get("rules_secondary"),
        "raw_json": json.dumps(m, sort_keys=True),
        "q_start_ts": None, "q_end_ts": None,
    }


def kickoff_of(markets: list[dict]) -> int | None:
    """Kickoff = KXNFLGAME expected_expiration_time - 3 h (same rule as #22)."""
    ts = [m["expected_exp_ts"] for m in markets if m["series"] == "KXNFLGAME" and m["expected_exp_ts"]]
    return min(ts) - 3 * 3600 if ts else None


def discover(client: KalshiClient) -> tuple[list[dict], dict]:
    """All markets of the in-scope games. Every game's events are queried by event ticker (live tier)."""
    stats = {"events_listed": 0, "events_in_scope": 0, "historical_tier_events": 0, "missing": []}
    events = {}
    for m in client.markets(MARGIN, historical=False):
        events.setdefault(m["event_ticker"], []).append(m)
    stats["events_listed"] = len(events)
    rows: list[dict] = []
    for ev in sorted(events):
        if not in_scope(ev):
            continue
        game, d = parse_event(ev)
        stats["events_in_scope"] += 1
        by_series: dict[str, list[dict]] = {MARGIN: events[ev]}
        for s in SERIES[1:]:
            by_series[s] = list(client.markets(s, historical=False, event_ticker=f"{s}-{game}"))
            if not by_series[s]:
                stats["missing"].append(f"{s}-{game}")
        game_rows = [market_row(m, s, game, None, d) for s, ms in by_series.items() for m in ms]
        ko = kickoff_of(game_rows)
        for r in game_rows:
            r["kickoff_ts"] = ko
            if ko is not None:
                r["q_start_ts"], r["q_end_ts"] = ko - PRE_S, ko + POST_S
        rows.extend(game_rows)
    log.info("discover: %d markets, %s", len(rows), {k: v for k, v in stats.items() if k != "missing"})
    return rows, stats


def fetch_trades(conn: sqlite3.Connection, client: KalshiClient, rows: list[dict]) -> int:
    """Public trade prints for the win-margin markets over each market's candle window."""
    done = {r[0] for r in conn.execute("SELECT ticker FROM mm_done WHERE step='trades'")}
    n = 0
    for r in rows:
        if r["series"] != MARGIN or r["q_start_ts"] is None or r["ticker"] in done:
            continue
        lo, hi = r["q_start_ts"], r["q_end_ts"]
        hist = client.trades_historical(lo) and client.trades_historical(hi)
        out = []
        for t in client.trades(r["ticker"], lo, hi, hist):
            out.append((t["trade_id"], r["ticker"], float(parse_iso(t["created_time"])) + _frac(t["created_time"]),
                        float(parse_dollars(t["yes_price_dollars"])),
                        float(parse_dollars(t.get("count_fp")) or t.get("count") or 0), t.get("taker_side") or ""))
        conn.executemany("INSERT OR IGNORE INTO mm_trades (trade_id, ticker, ts, yes_price, count, taker_side) "
                         "VALUES (?,?,?,?,?,?)", out)
        conn.execute("INSERT OR REPLACE INTO mm_done (ticker, step, ts, detail) VALUES (?,?,?,?)",
                     (r["ticker"], "trades", now_ts(), json.dumps({"n": len(out)})))
        client.http.flush_index(conn)
        conn.commit()
        n += 1
    return n


def _frac(iso: str) -> float:
    """Fractional seconds of an ISO timestamp (parse_iso truncates to whole seconds)."""
    m = re.search(r"\.(\d+)", iso or "")
    return float("0." + m.group(1)) if m else 0.0


def run(limit_games: int | None = None) -> dict:
    cfg = settings()
    conn = db.connect(db_path())
    conn.executescript(SCHEMA)
    conn.commit()
    try:
        client = KalshiClient(conn)
        client.cutoff()
        fee_types = {s: client.series(s).get("fee_type") for s in SERIES}
        conn.execute("INSERT OR REPLACE INTO mm_meta VALUES ('fee_types', ?)", (json.dumps(fee_types),))
        rows, stats = discover(client)
        if limit_games:
            keep = sorted({r["game"] for r in rows})[:limit_games]
            rows = [r for r in rows if r["game"] in keep]
        for r in rows:
            db.upsert(conn, "mm_markets", r, ["ticker"])
        conn.commit()
        mids = {t: m for t, m in conn.execute("SELECT ticker, mid FROM mm_markets")}
        done = {r[0] for r in conn.execute("SELECT ticker FROM mm_done WHERE step='quotes'")}
        todo = [r for r in rows if r["q_start_ts"] is not None and r["ticker"] not in done]
        log.info("%d markets, %d need candles", len(rows), len(todo))

        def fetch(r: dict):
            return r, client.candlesticks(r["series"], r["ticker"], int(r["q_start_ts"]), int(r["q_end_ts"]),
                                          cfg["kalshi"]["candle_interval_min"], False)

        workers = cfg["kalshi"].get("workers", 6)
        queue = iter(todo)
        i = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            inflight = set()
            while True:
                while len(inflight) < 3 * workers and (j := next(queue, None)) is not None:
                    inflight.add(pool.submit(fetch, j))
                if not inflight:
                    break
                finished, inflight = wait(inflight, return_when=FIRST_COMPLETED)
                for fut in finished:
                    r, candles = fut.result()
                    mid = mids[r["ticker"]]
                    conn.executemany("INSERT OR REPLACE INTO mm_quotes (mid, ts, bid_e4, ask_e4, volume) VALUES (?,?,?,?,?)",
                                     [quote_row(mid, c) for c in candles])
                    conn.execute("INSERT OR REPLACE INTO mm_done (ticker, step, ts, detail) VALUES (?,?,?,?)",
                                 (r["ticker"], "quotes", now_ts(), json.dumps({"n": len(candles)})))
                    client.http.flush_index(conn)
                    conn.commit()
                    i += 1
                    if i % 100 == 0 or i == len(todo):
                        log.info("candles %d/%d markets (hits=%d fetches=%d retries=%d)", i, len(todo),
                                 client.http.stats["hits"], client.http.stats["fetches"], client.http.stats["retries"])
        n_tr = fetch_trades(conn, client, rows)
        log.info("trade windows fetched this run: %d", n_tr)
        summary = coverage(conn, stats, fee_types)
    finally:
        conn.close()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "collection_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return summary


def coverage(conn: sqlite3.Connection, stats: dict, fee_types: dict) -> dict:
    out: dict = {"generated_ts": now_ts(), "fee_types": fee_types, "discover": stats}
    out["markets_by_series"] = {s: n for s, n in conn.execute("SELECT series, COUNT(*) FROM mm_markets GROUP BY series")}
    out["games"] = conn.execute("SELECT COUNT(DISTINCT game) FROM mm_markets").fetchone()[0]
    out["quote_rows"] = conn.execute("SELECT COUNT(*) FROM mm_quotes").fetchone()[0]
    out["trade_rows"] = conn.execute("SELECT COUNT(*) FROM mm_trades").fetchone()[0]
    out["markets_without_quotes"] = [t for (t,) in conn.execute(
        "SELECT ticker FROM mm_markets WHERE mid NOT IN (SELECT DISTINCT mid FROM mm_quotes)")][:50]
    out["status_counts"] = {f"{s}/{st}": n for s, st, n in conn.execute(
        "SELECT series, status, COUNT(*) FROM mm_markets GROUP BY series, status")}
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit-games", type=int, default=None, help="smoke run on the first N games")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(a.limit_games)
