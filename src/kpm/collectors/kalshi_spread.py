"""NFL winner-vs-spread study collector: KXNFLGAME + KXNFLSPREAD markets and 1-minute YES bid/ask candles for
every regular-season/playoff game of 2025 and 2026, written to data/nfl_spread.sqlite (schema: core/spread_schema.sql).

Public Kalshi GET endpoints only, through KalshiClient/CachedClient (every response is cached on disk)."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import statistics
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from importlib import resources
from pathlib import Path

from kpm.agents.matcher.rules import GameRef, find_game, parse_kalshi_event_date
from kpm.clients.kalshi import KalshiClient
from kpm.collectors.kalshi_history import _field
from kpm.core import config, db
from kpm.core.config import settings
from kpm.core.prices import parse_dollars
from kpm.core.teams import from_name, norm_code
from kpm.core.timeutil import floor_to, now_ts, parse_iso

log = logging.getLogger(__name__)

SERIES = {"KXNFLSPREAD": "spread", "KXNFLGAME": "win"}
SEASONS = (2025, 2026)
DB_NAME = "nfl_spread.sqlite"
SUMMARY_PATH = Path("reports/output/nfl_spread_arb/collection_summary.json")
_EVENT_CODE = re.compile(r"^[A-Z0-9]+-\d{2}[A-Z]{3}\d{2}([A-Z]+)$")


# ---------------------------------------------------------------- pure helpers
def team_from_ticker(ticker: str) -> str | None:
    """'KXNFLSPREAD-26OCT12BUFLAR-LAR3' -> 'LA'; 'KXNFLGAME-26OCT12BUFLAR-BUF' -> 'BUF'."""
    word = re.sub(r"\d+$", "", ticker.rsplit("-", 1)[-1])
    return norm_code(word) or from_name(word)     # full-city suffixes such as PHILADELPHIA17 / BUFFALO22


def event_code_teams(event_ticker: str) -> frozenset[str]:
    """Fallback when a market suffix is unparsable: split the event's team block into two valid codes."""
    m = _EVENT_CODE.match(event_ticker or "")
    if not m:
        return frozenset()
    blk = m.group(1)
    found = {frozenset((a, b)) for i in range(2, len(blk) - 1)
             if (a := norm_code(blk[:i])) and (b := norm_code(blk[i:])) and a != b}
    return found.pop() if len(found) == 1 else frozenset()


def bid_e4(v) -> int | None:
    """Empty bid is reported as 0 (or missing) -> NULL."""
    x = parse_dollars(v) if not isinstance(v, (int, float)) else float(v)
    return None if x is None or x <= 0 else int(round(x * 10000))


def ask_e4(v) -> int | None:
    """Empty ask is reported as 1 (or missing) -> NULL."""
    x = parse_dollars(v) if not isinstance(v, (int, float)) else float(v)
    return None if x is None or x >= 1 else int(round(x * 10000))


def quote_row(mid: int, c: dict) -> tuple:
    """(mid, ts, bid_e4, ask_e4, volume) from a historical ('close') or live ('close_dollars') candle."""
    yb, ya = c.get("yes_bid") or {}, c.get("yes_ask") or {}
    vol = parse_dollars(_field(c, "volume"))
    return (mid, int(c["end_period_ts"]), bid_e4(_field(yb, "close")), ask_e4(_field(ya, "close")), vol)


def game_window(markets: list[dict]) -> tuple[int, int] | None:
    """[floor_to_minute(min open over the game's SPREAD markets), max close over all the game's markets]."""
    opens = [m["open_ts"] for m in markets if m["kind"] == "spread" and m.get("open_ts")]
    closes = [m["close_ts"] for m in markets if m.get("close_ts")]
    if not opens or not closes:
        return None
    return floor_to(min(opens), 60), max(closes)


def market_window(win: tuple[int, int], open_ts: int | None, close_ts: int | None) -> tuple[int, int] | None:
    """Clip the game window to the market's own life."""
    s = max(win[0], floor_to(open_ts, 60)) if open_ts else win[0]
    e = min(win[1], close_ts) if close_ts else win[1]
    return (s, e) if e > s else None


# ---------------------------------------------------------------- discovery
def _eligible_games(src: sqlite3.Connection, seasons) -> list[dict]:
    cutoff = now_ts() - 6 * 3600
    q = ("SELECT * FROM games WHERE game_type != 'PRE' AND kickoff_ts < ? AND season IN (%s) ORDER BY kickoff_ts"
         % ",".join("?" * len(seasons)))
    return [dict(r) for r in src.execute(q, (cutoff, *seasons))]


def discover(client: KalshiClient, games: list[dict]) -> list[dict]:
    """Markets of both series mapped to eligible games. One event per (game, series): highest volume wins."""
    refs = [GameRef(g["game_id"], g["home_team"], g["away_team"], g["kickoff_ts"]) for g in games]
    by_id = {g["game_id"]: g for g in games}
    out: list[dict] = []
    stats = {"unmapped_events": 0, "unmapped_markets": 0, "dup_events": 0, "no_team_markets": 0}
    for series, kind in SERIES.items():
        raw: dict[str, tuple[dict, str]] = {}
        for m in client.markets(series, historical=True):
            raw[m["ticker"]] = (m, "historical")
        for m in client.markets(series, historical=False, status="settled"):
            raw.setdefault(m["ticker"], (m, "live"))
        by_event: dict[str, list[str]] = defaultdict(list)
        for t, (m, _) in raw.items():
            by_event[m["event_ticker"]].append(t)
        per_game: dict[str, list[tuple[str, list[str], float]]] = defaultdict(list)
        for ev, tickers in by_event.items():
            teams = frozenset(t for t in (team_from_ticker(x) for x in tickers) if t)
            if len(teams) != 2:
                teams = event_code_teams(ev) or teams
            game = find_game(refs, teams, parse_kalshi_event_date(ev)) if len(teams) == 2 else None
            if game is None:
                stats["unmapped_events"] += 1
                stats["unmapped_markets"] += len(tickers)
                continue
            vol = sum(float(raw[t][0].get("volume_fp") or 0) for t in tickers)
            per_game[game.game_id].append((ev, tickers, vol))
        for gid, evs in per_game.items():
            evs.sort(key=lambda x: -x[2])
            for ev, _, vol in evs[1:]:
                stats["dup_events"] += 1
                log.warning("%s: game %s has several events; dropping %s (volume %.0f), keeping %s (%.0f)",
                            series, gid, ev, vol, evs[0][0], evs[0][2])
            g = by_id[gid]
            known = {}      # custom_strike team UUID -> team, from sibling markets whose suffix parses
            for t in evs[0][1]:
                u = (raw[t][0].get("custom_strike") or {}).get("football_team")
                if u and team_from_ticker(t):
                    known[u] = team_from_ticker(t)
            for t in evs[0][1]:
                m, tier = raw[t]
                team = team_from_ticker(t) or known.get((m.get("custom_strike") or {}).get("football_team"))
                if team is None or team not in (g["home_team"], g["away_team"]):
                    stats["no_team_markets"] += 1
                    log.warning("market %s: team suffix not in game %s; skipped", t, gid)
                    continue
                out.append(_market_row(m, tier, series, kind, g, team))
    log.info("discover: %d markets kept; %s", len(out), stats)
    return out


def _market_row(m: dict, tier: str, series: str, kind: str, g: dict, team: str) -> dict:
    return {
        "ticker": m["ticker"], "series": series, "kind": kind, "event_ticker": m["event_ticker"],
        "game_id": g["game_id"], "season": g["season"], "week": g["week"], "game_type": g["game_type"],
        "kickoff_ts": g["kickoff_ts"], "team": team,
        "strike": float(m["floor_strike"]) if kind == "spread" and m.get("floor_strike") is not None else None,
        "strike_type": m.get("strike_type"),
        "open_ts": parse_iso(m.get("open_time")), "close_ts": parse_iso(m.get("close_time")),
        "settled_ts": parse_iso(m.get("settlement_ts")), "status": m.get("status"),
        "result": m.get("result") or None,
        "settlement_value": parse_dollars(m.get("settlement_value_dollars")),
        "volume": parse_dollars(m.get("volume_fp")), "data_tier": tier,
        "rules_primary": m.get("rules_primary"), "rules_secondary": m.get("rules_secondary"),
        "raw_json": json.dumps(m, sort_keys=True),
    }


# ---------------------------------------------------------------- run
def spread_db_path() -> Path:
    return config.path("db_path").parent / DB_NAME


def _open_spread_db() -> sqlite3.Connection:
    conn = db.connect(spread_db_path())
    conn.executescript(resources.files("kpm.core").joinpath("spread_schema.sql").read_text(encoding="utf-8"))
    conn.commit()
    return conn


def _open_games_ro() -> sqlite3.Connection:
    p = config.path("db_path").resolve().as_posix()
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def run(limit: int | None = None, force: bool = False, season: int | None = None) -> None:
    cfg = settings()
    seasons = (season,) if season else SEASONS
    src = _open_games_ro()
    try:
        games = _eligible_games(src, seasons)
    finally:
        src.close()
    log.info("%d eligible games (seasons %s)", len(games), seasons)
    if limit:  # evenly spaced sample so a smoke run touches both seasons / data tiers
        games = [games[int(i * (len(games) - 1) / max(limit - 1, 1))] for i in range(min(limit, len(games)))]

    conn = _open_spread_db()
    try:
        client = KalshiClient(conn)
        client.cutoff()
        rows = discover(client, games)
        by_game: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_game[r["game_id"]].append(r)
        jobs = []
        for gid, ms in by_game.items():
            win = game_window(ms)
            for r in ms:
                mw = market_window(win, r["open_ts"], r["close_ts"]) if win else None
                r["q_start_ts"], r["q_end_ts"] = mw if mw else (None, None)
                db.upsert(conn, "sp_markets", r, ["ticker"])
                if mw is None:
                    continue
                if client.is_historical(r["settled_ts"]) != (r["data_tier"] == "historical"):
                    log.warning("%s: tier %s disagrees with cutoff on settled_ts", r["ticker"], r["data_tier"])
                jobs.append({"ticker": r["ticker"], "series": r["series"], "tier": r["data_tier"],
                             "start": mw[0], "end": mw[1]})
        mids = {t: m for t, m in conn.execute("SELECT ticker, mid FROM sp_markets")}
        done = {r[0] for r in conn.execute("SELECT ticker FROM sp_done WHERE step='quotes'")}
        client.http.flush_index(conn)
        conn.commit()
        todo = [j for j in jobs if force or j["ticker"] not in done]
        log.info("%d markets (%d games); %d need quotes", len(jobs), len(by_game), len(todo))

        def fetch(job: dict) -> tuple[dict, list[dict]]:
            return job, client.candlesticks(job["series"], job["ticker"], job["start"], job["end"],
                                            cfg["kalshi"]["candle_interval_min"], job["tier"] == "historical")

        workers = cfg["kalshi"].get("workers", 6)
        queue = iter(todo)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            inflight = set()        # bounded: completed futures (and their candle lists) are dropped at once
            i = 0
            while True:
                while len(inflight) < 3 * workers and (j := next(queue, None)) is not None:
                    inflight.add(pool.submit(fetch, j))
                if not inflight:
                    break
                finished, inflight = wait(inflight, return_when=FIRST_COMPLETED)
                for fut in finished:
                    job, candles = fut.result()
                    mid = mids[job["ticker"]]
                    qrows = [quote_row(mid, c) for c in candles]
                    conn.executemany("INSERT OR REPLACE INTO sp_quotes (mid, ts, bid_e4, ask_e4, volume) "
                                     "VALUES (?,?,?,?,?)", qrows)
                    conn.execute("INSERT OR REPLACE INTO sp_done (ticker, step, ts, detail) VALUES (?,?,?,?)",
                                 (job["ticker"], "quotes", now_ts(), json.dumps({"n": len(qrows)})))
                    client.http.flush_index(conn)
                    conn.commit()
                    i += 1
                    del fut, candles, qrows
                    if i % 50 == 0 or i == len(todo):
                        log.info("quotes %d/%d markets (cache hits=%d fetches=%d retries=%d)", i, len(todo),
                                 client.http.stats["hits"], client.http.stats["fetches"], client.http.stats["retries"])
        summary = coverage_summary(conn, [g["game_id"] for g in games], games)
    finally:
        conn.close()
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def coverage_summary(conn: sqlite3.Connection, game_ids: list[str], games: list[dict]) -> dict:
    """Coverage of the collected data. `games` is the eligible set this run was asked for."""
    out: dict = {"generated_ts": now_ts(), "eligible_games": len(games)}
    series_by_game: dict[str, set[str]] = defaultdict(set)
    for gid, s, season in conn.execute("SELECT game_id, series, season FROM sp_markets"):
        series_by_game[gid].add(s)
    season_of = {g["game_id"]: g["season"] for g in games}
    per_season: dict = {}
    for gid, season in season_of.items():
        d = per_season.setdefault(str(season), {"eligible": 0, "both_series": 0, "missing_spread": 0,
                                                "missing_win": 0, "missing_both": 0})
        d["eligible"] += 1
        have = series_by_game.get(gid, set())
        d["both_series"] += have == set(SERIES)
        d["missing_spread"] += "KXNFLSPREAD" not in have and "KXNFLGAME" in have
        d["missing_win"] += "KXNFLGAME" not in have and "KXNFLSPREAD" in have
        d["missing_both"] += not have
    out["games_by_season"] = per_season
    out["games_missing_a_series"] = sum(d["eligible"] - d["both_series"] for d in per_season.values())
    out["markets_by_kind"] = {k: n for k, n in conn.execute("SELECT kind, COUNT(*) FROM sp_markets GROUP BY kind")}
    out["quote_rows"] = conn.execute("SELECT COUNT(*) FROM sp_quotes").fetchone()[0]
    cov: dict[str, list[float]] = defaultdict(list)
    for kind, qs, qe, n in conn.execute(
            "SELECT m.kind, m.q_start_ts, m.q_end_ts, "
            "(SELECT COUNT(*) FROM sp_quotes q WHERE q.mid=m.mid AND q.ts > m.q_start_ts AND q.ts <= m.q_end_ts) "
            "FROM sp_markets m WHERE m.q_start_ts IS NOT NULL AND m.mid IN (SELECT mid FROM sp_quotes GROUP BY mid)"):
        exp = (qe - qs) / 60
        if exp > 0:
            cov[kind].append(min(1.0, n / exp))
    out["coverage_by_kind"] = {k: {"markets": len(v), "median": statistics.median(v), "p05": _pct(v, 0.05),
                                   "min": min(v)} for k, v in cov.items()}
    nulls = {}
    for kind, n, nb, na in conn.execute(
            "SELECT m.kind, COUNT(*), SUM(q.bid_e4 IS NULL), SUM(q.ask_e4 IS NULL) "
            "FROM sp_quotes q JOIN sp_markets m USING(mid) GROUP BY m.kind"):
        nulls[kind] = {"rows": n, "null_bid_share": nb / n, "null_ask_share": na / n}
    out["null_shares_by_kind"] = nulls
    out["markets_without_quotes"] = conn.execute(
        "SELECT COUNT(*) FROM sp_markets WHERE mid NOT IN (SELECT mid FROM sp_quotes GROUP BY mid)").fetchone()[0]
    return out
