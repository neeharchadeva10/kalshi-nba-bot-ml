"""Localhost dashboard for the Kalshi bots (strategies late_favorite and pair_h2). Python standard library only.

    kpm dashboard [--port N]      then open http://127.0.0.1:8765/

- Serves one page (static/) that polls a small JSON API every dashboard.refresh_s seconds. The page shows the strategy
  of the latest run (bot_runs.strategy) and adapts its tables to it.
- Reads the bot tables (bot_runs, bot_heartbeat, bot_quotes, bot_opportunities, bot_orders, bot_events), the
  research outputs in reports/output/mechanical_edges/ and the research download progress (audit_log). The database
  is opened read-only (mode=ro) for each request and closed again; a missing file, table or column gives empty
  results. SQLite may create the -wal and -shm sidecar files next to the database when the bot is not running; the
  database content is never modified.
- The only write is POST /api/kill (the STOP button), which creates the kill-switch file (trading.kill_switch_file,
  read from config/settings.yaml on every request, never from the database). It needs the header "X-KPM-Kill: 1": a
  page on another site cannot send that header without a CORS preflight, and preflights are never approved here.
- Binds to loopback only, and answers only requests whose Host header is localhost / 127.0.0.1 / [::1] with this
  server's port, so a DNS-rebinding page cannot read the API or press STOP either. Every request is also checked
  against Fetch Metadata: a cross-site or same-site page (an <img>, a fetch, a form) gets 403 and never makes this
  server do any work; only same-origin requests, typed URLs and a followed link to "/" are answered.
- Anything that looks like a secret (key ids, key paths, signatures, tokens, headers, PEM text) is redacted from every
  JSON column before it is returned, whether the column is valid JSON or not.

Caps: the page shows the limits IN FORCE for the latest run (bot_runs.config_json) and falls back to the current
config/settings.yaml, which is re-read whenever the file changes. Values may be numeric strings ("5.00"); they are
returned as numbers. ``caps_drift`` lists the keys where the file now differs from what the run started with.

Trade status: every row of /api/opportunities carries ``trade`` (what happened to that signal: settled and won or lost,
open until its market settles, not filled, skipped and why, paper only) and every row of /api/orders carries ``money``
("real money" / "paper" / "not sent") and ``trade`` (the settlement state of the order). The reason of a skipped signal is the
one the bot logged (bot_events kind 'cap'), never a guess from its price. A fill counts as an open trade from the moment its
order shows it, although the bot writes the row's outcome a few seconds later. GET /api/performance
(?run=latest|all|<run id>, ?limit=N settled trades, default 300) returns the numbers behind the money tiles and graphs of
the page: a summary, the settled trades with running totals (real money against the paper twin of the same trades) and the
open trades. All of it is derived from the bot tables, read-only.

bot_heartbeat contract: the bot overwrites its heartbeat at least every 5 seconds in EVERY state, including waiting
for the next window; the page calls a run silent after 15 seconds without one. detail_json keys the page shows when
present (all optional; any other keys are listed as they are, with *_ts epoch values shown as local times):
  strategy, mode, status        what is running
  market_ticker (hour_ticker)   the market being watched now (KXBTC15M for late_favorite, the hour's market for pairs)
  window_end_ts                 end of the current window (epoch seconds)
  loss_used_usd                 the account-wide loss counted against the cap (positive number: realized loss + open
                                worst case); when absent the page falls back to -SUM(bot_opportunities.realized_pnl)
  max_loss_usd                  the cap in force
  balance_usd                   Kalshi cash balance in dollars (null when unknown)
  last_error                    most recent error message (shown as a warning banner)
The countdown and window times are computed from the clock (KXBTC15M windows end at :00/:15/:30/:45).
"""
from __future__ import annotations

import csv
import json
import logging
import math
import os
import re
import socket
import socketserver
import sqlite3
import time
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from kpm.core import config as _config
from kpm.core.config import PROJECT_ROOT

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
KILL_HEADER = "X-KPM-Kill"
KILL_REASON = "dashboard STOP button"
DEFAULT_KILL_FILE = "data/KILL"
RESEARCH_SUBDIR = "mechanical_edges"
# Download progress: distinct markets finished in audit_log, out of the number in each study.
PROGRESS = {
    "h1_final_minute_trades": ("analysis.crypto_final_minute", "trades", 11835),
    "h2_cross_bounds_pairs": ("analysis.crypto_cross_bounds", "pair", 2952),
}
# Limits shown on the page: read from config/settings.yaml, and overridden by what the latest run recorded in
# bot_runs.config_json (the limits actually in force). Numeric strings ("5.00") become numbers.
CAP_KEYS = ("enabled", "max_loss_usd", "max_cost_per_order_usd", "max_contracts_per_order", "max_open_exposure_usd",
            "max_orders_per_minute", "max_orders_per_session", "max_clock_skew_s", "min_seconds_to_close",
            "allowed_series")
RUN_CAP_KEYS = ("max_loss_usd", "max_cost_per_order_usd", "max_contracts_per_order", "max_orders_per_minute")
# Per-strategy settings the bots record at the top level of config_json: {block in settings.yaml: {run key: block key}}
RUN_STRATEGY_KEYS = {
    "pair_h2": ("pair", {"min_edge_usd": "min_edge_usd", "window_min": "window_min",
                         "strikes_each_side": "strikes_each_side", "poll_interval_s": "poll_interval_s",
                         "max_book_age_s": "max_book_age_s", "stop_before_close_s": "stop_before_close_s",
                         "max_live_pairs_per_hour": "max_live_pairs_per_hour", "contracts": "contracts"}),
    "late_favorite": ("late_favorite", {"contracts": "contracts", "max_attempts_per_window": "max_attempts",
                                        "poll_interval_s": "poll_interval_s", "max_book_age_s": "max_book_age_s"}),
}
UNSCOPED_MARGIN_S = 120.0          # an event with no run id belongs to a run that started up to this long after it
OTHER_RUN_FRESH_S = 60.0           # another live run whose heartbeat is younger than this is "running"
MAX_QUOTE_ROWS = 50_000
MAX_PERF_ROWS = 20_000             # opportunity rows read for the summary and graphs of one scope (newest first)
MAX_PERF_TRADES = 2_000            # settled trades one /api/performance answer lists
# What a trade is: late_favorite rows are one order held to settlement ("late favorite YES" / "late favorite NO"); the
# other strategy's rows are pairs. KXBTC15M markets close at :00/:15/:30/:45 and the hourly markets on the hour.
LATE_KIND = "late favorite"
REAL_OUTCOMES = frozenset({"filled", "both_filled", "one_leg_unwound", "one_leg_held"})   # real money was put to work
PRICE_CEILING = 0.999              # the highest price limit the bot has used (late_favorite PRICE_MAX). Only a last-resort clue for a
                                   # refused signal with no logged reason; the real bound is read from the bot's own message
MARKET_SPAN_S = {"late": 900.0, "pair": 3600.0}
OVERDUE_S = 1800.0                 # an unsettled trade this long after its market closed is "result not recorded"
FRESH_S = 600.0                    # a row with no outcome is "in progress" for this long, then "no outcome recorded"
MAX_FILE_BYTES = 5_000_000
MAX_BODY_BYTES = 4096
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".ico": "image/x-icon",
}
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
}
# Defence in depth: the bot never stores secrets in these JSON columns, but hide anything that looks like one. A KEY name
# that matches hides its whole value; a TEXT value is scrubbed for PEM blocks, "name: value" / "name=value" pairs and
# bearer tokens. Kalshi's own header names (KALSHI-ACCESS-KEY carries the key id) are covered.
REDACTED = "[redacted]"
_SECRET_WORDS = (r"secret|token|passw|passphrase|private|api[-_ ]?key|access[-_ ]?key|key[-_ ]?id|key[-_ ]?path|"
                 r"signature|authori[sz]ation|bearer|credential|cookie|kalshi-access|pem\b|headers?\b")
_SECRET_KEY = re.compile(rf"{_SECRET_WORDS}|^key$|^auth", re.I)
_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]*-----.*?(?:-----END [A-Z0-9 ]*-----|$)", re.S)
_SECRET_ASSIGN = re.compile(
    r"(?i)((?:secret|token|passw\w*|passphrase|private\w*|api[-_]?key\w*|access[-_]?key\w*|key[-_]?id|key[-_]?path|"
    r"signature|authori[sz]ation|credential\w*|cookie|kalshi-access-\w+)['\"]?\s*[:=]\s*)(['\"]?)"
    r"(?:bearer\s+|basic\s+)?[^'\",}\]\s]+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_INT_RE = re.compile(r"-?[0-9]+")                      # ASCII digits only: int() would also accept Arabic-Indic ones
_NUM_RE = re.compile(r"[-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?")
_warned: set[str] = set()

_cfg_stamp: tuple | None = None


def settings() -> dict:
    """config/settings.yaml, re-read whenever the file changes. kpm.core.config.settings() caches for the life of the
    process, which would leave this long-running page showing limits and a kill-switch path that no longer match the file."""
    global _cfg_stamp
    p = Path(PROJECT_ROOT) / "config" / "settings.yaml"
    try:
        st = p.stat()
        stamp: tuple | None = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    if stamp != _cfg_stamp:
        _config.settings.cache_clear()
        _cfg_stamp = stamp
    return _config.settings()


# ----------------------------------------------------------------------------------------------- helpers

def _jsonable(v: Any) -> Any:
    """Copy safe for strict JSON: NaN/inf -> None, bytes -> None, tuples -> lists."""
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (bytes, bytearray, memoryview)):
        return None
    return v


def _scrub_text(s: str) -> str:
    """Text with anything secret-looking replaced: PEM blocks, ``name: value`` / ``name=value`` pairs whose name looks
    like a secret (Python reprs and header lines included) and bearer tokens."""
    if "-----" in s:
        s = _PEM.sub(REDACTED, s)
    s = _SECRET_ASSIGN.sub(lambda m: m.group(1) + m.group(2) + REDACTED, s)
    return _BEARER.sub("Bearer " + REDACTED, s)


def _redact(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: REDACTED if isinstance(k, str) and _SECRET_KEY.search(k) else _redact(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        if len(v) == 2 and isinstance(v[0], str) and _SECRET_KEY.search(v[0]):      # a header pair ["KALSHI-ACCESS-KEY", "..."]
            return [v[0], REDACTED]
        return [_redact(x) for x in v]
    if isinstance(v, str):
        return _scrub_text(v)
    return v


def _parse_json(text: Any) -> Any:
    """Parsed and redacted JSON column. Text that is not valid JSON (a Python repr, say) is scrubbed and cut, not dropped."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return _redact(json.loads(text))
    except ValueError:
        return _scrub_text(text)[:2000]


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _as_number(v: Any) -> Any:
    """YAML and bot configs may hold money and counts as strings ("5.00", "1"): numbers for the page. Anything that is
    not a finite number is returned unchanged (booleans, lists, None, words)."""
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, str):
        s = v.strip()
        if _INT_RE.fullmatch(s):
            return int(s)
        if _NUM_RE.fullmatch(s):
            f = float(s)
            return f if math.isfinite(f) else v
    return v


def _same_path(a: Any, b: Any) -> bool:
    try:
        pa, pb = Path(str(a)), Path(str(b))
        pa = pa if pa.is_absolute() else Path(PROJECT_ROOT) / pa
        pb = pb if pb.is_absolute() else Path(PROJECT_ROOT) / pb
        return os.path.normcase(os.path.abspath(pa)) == os.path.normcase(os.path.abspath(pb))
    except (OSError, ValueError, TypeError):
        return False


def _arg(q: dict[str, list[str]], name: str, default: str | None = None) -> str | None:
    v = q.get(name)
    return v[0] if v else default


def _arg_num(q: dict[str, list[str]], name: str, default: float | None, lo: float, hi: float) -> float | None:
    try:
        v = float(_arg(q, name) or "")
    except ValueError:
        return default
    return min(hi, max(lo, v)) if math.isfinite(v) else default


def kill_switch_path(cfg: dict) -> Path:
    p = Path(str((cfg.get("trading") or {}).get("kill_switch_file") or DEFAULT_KILL_FILE))
    return p if p.is_absolute() else Path(PROJECT_ROOT) / p


def _is_loopback(host: str) -> bool:
    return host.strip().strip("[]").lower() in LOOPBACK_HOSTS


def host_allowed(value: str | None, port: int) -> bool:
    """True for a Host header (or Origin netloc) naming loopback with this server's port."""
    if not value:
        return False
    v = value.strip().lower()
    if v.startswith("["):                       # [::1]:8765
        end = v.find("]")
        if end < 0:
            return False
        name, rest = v[1:end], v[end + 1:]
        if rest and not rest.startswith(":"):
            return False
        port_s = rest[1:]
    else:
        name, _, port_s = v.partition(":")
        if ":" in port_s:                        # bare IPv6 without brackets is not a valid Host
            return False
    if name not in LOOPBACK_HOSTS:
        return False
    if not port_s:
        return port == 80
    return port_s.isascii() and port_s.isdigit() and int(port_s) == port


# ----------------------------------------------------------------------------------------------- database

def _open_db(db_path: str | os.PathLike | None) -> sqlite3.Connection | None:
    """Read-only connection, or None when the database file is missing (it is never created here)."""
    if not db_path:
        return None
    p = Path(db_path)
    if not p.is_file():
        return None
    try:
        conn = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        log.warning("dashboard: cannot open %s read-only: %s", p, exc)
        return None
    conn.row_factory = sqlite3.Row
    return conn


def _query(conn: sqlite3.Connection | None, sql: str, params: tuple = ()) -> list[dict]:
    if conn is None:
        return []
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.DatabaseError as exc:   # table not created yet, older schema, locked or unreadable file
        msg = str(exc)
        if "no such table" not in msg and msg not in _warned:
            _warned.add(msg)
            log.warning("dashboard query failed (%s): %s", msg, " ".join(sql.split())[:160])
        return []


def _latest_run(conn: sqlite3.Connection | None) -> dict | None:
    rows = _query(conn, "SELECT * FROM bot_runs ORDER BY started_ts DESC, rowid DESC LIMIT 1")
    return rows[0] if rows else None


def _run_filter(conn: sqlite3.Connection | None, q: dict) -> tuple[str | None, bool]:
    """(run_id, all_runs): ?run=all -> (None, True); default latest run; ?run=<id> for one run."""
    sel = (_arg(q, "run", "latest") or "latest").strip()
    if sel == "all":
        return None, True
    if sel != "latest":
        return sel, False
    run = _latest_run(conn)
    return (run["run_id"] if run else None), False


PAIRED = "live = 1 AND realized_pnl IS NOT NULL AND paper_realized_pnl IS NOT NULL"     # live trade with both P&Ls known
AWAITING = ("live = 1 AND realized_pnl IS NULL AND "
            "outcome IN ('both_filled', 'filled', 'one_leg_unwound', 'one_leg_held')")  # filled, market not settled yet


def _stats(conn: sqlite3.Connection | None, run_id: str | None) -> dict:
    """Counts and P&L of bot_opportunities (and the live orders sent) for one run, or for every run.

    realized_pnl and paper_realized_pnl cover different sets of rows (a paper twin has no live trade, an unwind has no
    twin until its hour settles), so they must not be compared with each other. The ``paired_*`` figures cover only
    live trades whose realized AND paper P&L are both known: the honest comparison. ``pending_n`` counts live trades
    still waiting for their market to settle. avg_edge_change (the cost of order delay, in dollars per pair) uses only
    both-legs-filled rows whose live fees are known."""
    where, params = ("WHERE run_id = ?", (run_id,)) if run_id is not None else ("", ())
    out = {"opportunities": 0, "last_ts": None, "live_attempts": 0, "both_filled": 0, "filled": 0, "unwound": 0,
           "avg_latency_ms": None, "realized_pnl": None, "paper_realized_pnl": None, "avg_edge_change": None,
           "paired_n": 0, "paired_realized": None, "paired_paper": None, "pending_n": 0, "live_orders": 0}
    o = _query(conn, f"""
        SELECT COUNT(*) AS opportunities,
               MAX(detected_ts) AS last_ts,
               COALESCE(SUM(live), 0) AS live_attempts,
               COALESCE(SUM(outcome = 'both_filled'), 0) AS both_filled,
               COALESCE(SUM(outcome IN ('both_filled', 'filled')), 0) AS filled,
               COALESCE(SUM(outcome = 'one_leg_unwound'), 0) AS unwound,
               AVG(latency_ms) AS avg_latency_ms,
               SUM(realized_pnl) AS realized_pnl,
               SUM(paper_realized_pnl) AS paper_realized_pnl,
               AVG(CASE WHEN outcome = 'both_filled' AND live_cost IS NOT NULL AND live_fees IS NOT NULL
                             AND paper_cost IS NOT NULL
                        THEN (paper_cost - live_cost) + (COALESCE(paper_fees, 0) - live_fees) END)
                   AS avg_edge_change,
               COALESCE(SUM(CASE WHEN {PAIRED} THEN 1 END), 0) AS paired_n,
               SUM(CASE WHEN {PAIRED} THEN realized_pnl END) AS paired_realized,
               SUM(CASE WHEN {PAIRED} THEN paper_realized_pnl END) AS paired_paper,
               COALESCE(SUM(CASE WHEN {AWAITING} THEN 1 END), 0) AS pending_n
        FROM bot_opportunities {where}""", params)
    if o:
        out.update(o[0])
    n = _query(conn, f"SELECT COUNT(*) AS live_orders FROM bot_orders {where} {'AND' if where else 'WHERE'} dry_run = 0",
               params)
    if n:
        out["live_orders"] = n[0]["live_orders"]
    return out


def _unscoped_window(conn: sqlite3.Connection | None, run_id: str) -> tuple[float, float] | None:
    """(from, to) epoch seconds within which a row with no run id is shown with ``run_id``: from shortly before the
    run started (a start-up error is logged before its run row exists) until it ended (or for ever while it runs).
    None when there is no such run."""
    run = _query(conn, "SELECT started_ts, ended_ts FROM bot_runs WHERE run_id = ?", (run_id,))
    start = _num(run[0].get("started_ts")) if run else None
    if start is None:
        return None
    end = _num(run[0].get("ended_ts"))
    return start - UNSCOPED_MARGIN_S, end if end is not None else 1e18


def _list(conn: sqlite3.Connection | None, q: dict, table: str, order: str, default_limit: int, max_limit: int,
          decorate: Callable[[dict], dict] | None = None, include_unscoped: bool = False) -> dict:
    """Newest-first rows of one bot table for the latest run (default), ?run=all, or ?run=<id>.

    ``include_unscoped`` (tables with a ``ts`` column): rows with no run id are shown too, but only those logged around
    the selected run's lifetime, so a start-up error from days ago does not follow every later run."""
    limit = int(_arg_num(q, "limit", default_limit, 1, max_limit) or default_limit)
    run_id, all_runs = _run_filter(conn, q)
    if all_runs:
        where, params = "", ()
    elif run_id is None:
        if not include_unscoped:
            return {"run_id": None, "all_runs": False, "rows": []}
        where, params = "WHERE run_id IS NULL", ()
    else:
        where, params = "WHERE run_id = ?", (run_id,)
        win = _unscoped_window(conn, run_id) if include_unscoped else None
        if win is not None:
            where = "WHERE run_id = ? OR (run_id IS NULL AND ts >= ? AND ts <= ?)"
            params = (run_id, win[0], win[1])
    rows = _query(conn, f"SELECT * FROM {table} {where} ORDER BY {order} LIMIT ?", (*params, limit))
    if decorate:
        rows = [decorate(r) for r in rows]
    return {"run_id": run_id, "all_runs": all_runs, "rows": rows}


def _decorate_opportunity(r: dict) -> dict:
    """Adds live_edge (same basis as paper_edge) and edge_change = live_edge - paper_edge (the cost of delay).

    Both need the live fees. A fill whose fees are not recorded yet gets ``fees_pending`` instead of a number: counting
    a missing fee as $0 would overstate the live edge by the whole fee and turn the delay cost positive."""
    pe, pc, pf = _num(r.get("paper_edge")), _num(r.get("paper_cost")), _num(r.get("paper_fees"))
    lc, lf = _num(r.get("live_cost")), _num(r.get("live_fees"))
    live_edge = None
    r["fees_pending"] = False
    if lc is not None and r.get("outcome") in (None, "both_filled"):
        if lf is None:
            r["fees_pending"] = True
        elif pe is not None and pc is not None:
            live_edge = pe + (pc - lc) + ((pf or 0.0) - lf)
        else:
            live_edge = 1.0 - lc - lf
    r["live_edge"] = live_edge
    r["edge_change"] = live_edge - pe if live_edge is not None and pe is not None else None
    return r


def _decorate_order(r: dict) -> dict:
    r["response"] = _parse_json(r.pop("response_json", None))
    return r


def _decorate_event(r: dict) -> dict:
    r["data"] = _parse_json(r.pop("data_json", None))
    if isinstance(r.get("message"), str):
        r["message"] = _scrub_text(r["message"])
    return r


# ----------------------------------------------------------------------------------------------- trade status

def _money(v: float, signed: bool = False) -> str:
    """Dollars to the cent for a label: $0.25, +$0.25, −$0.74 (a real minus sign). A value that rounds to zero has no sign."""
    c = Decimal(repr(abs(float(v)))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    body = f"${c:,.2f}"
    if c == 0:
        return body
    return ("−" if v < 0 else "+" if signed else "") + body


def _hm(ts: float) -> str:
    """HH:MM in this machine's time zone, which is the browser's: the dashboard only serves localhost."""
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M")
    except (OverflowError, OSError, ValueError):
        return "?"


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _is_late(r: dict) -> bool:
    return str(r.get("kind") or "").startswith(LATE_KIND)


def _market_close_ts(r: dict) -> float | None:
    """When the row's market closes and settles: the next quarter hour after the detection for a late_favorite signal (the
    bot decides 60-120 s before the close), the next full hour for a pair. None without a detection time."""
    ts = _num(r.get("detected_ts"))
    if ts is None:
        return None
    span = MARKET_SPAN_S["late" if _is_late(r) else "pair"]
    return math.floor(ts / span) * span + span


def _side(r: dict) -> str | None:
    """YES or NO for a late_favorite row (the outcome it buys); None for a pair."""
    if not _is_late(r):
        return None
    word = str(r.get("kind") or "")[len(LATE_KIND):].strip().upper()
    return word if word in ("YES", "NO") else None


def _per_contract(total: Any, r: dict) -> float | None:
    t, n = _num(total), _num(r.get("contracts"))
    return t / n if t is not None and n else None


def _won(r: dict, pnl: float, paper: bool = False) -> bool:
    """Did the trade win? A late_favorite contract pays $1 or nothing, so it won when it paid out; a pair always pays $1 in
    total, so a pair won when it made money. ``paper`` judges the paper twin, whose payout is not stored (it is its P&L plus
    what it cost)."""
    if _is_late(r):
        if paper:
            cost = _num(r.get("paper_cost"))
            if cost is not None:
                n = _num(r.get("contracts")) or 1.0
                return pnl + cost + (_num(r.get("paper_fees")) or 0.0) > 0.5 * n
        else:
            payout = _num(r.get("payout"))
            if payout is not None:
                return payout > 0
    return pnl > 0


_PRICE_OUTSIDE = re.compile(r"price\s+([0-9]*\.?[0-9]+)\s+is outside\s+([0-9]*\.?[0-9]+)\s*\.\.\s*([0-9]*\.?[0-9]+)", re.I)
_SKIP_WORDS = (("loss cap", "loss cap reached"), ("kill switch", "kill switch was on"), ("halt", "safety halt"),
               ("refused by safety", "refused by the safety check"), ("per-order ceiling", "above the per-order limit"),
               ("stopping", "bot was stopping"))


def _cents(price: float) -> str:
    """A price bound in cents, as short as it can be: 0.99 -> 99c, 0.999 -> 99.9c, 0.01 -> 1c, 0.001 -> 0.1c."""
    return f"{round(price * 100, 4):g}c"


def _skip_label(reason: str | None, ask: float | None = None) -> str:
    """The real reason a signal was not traded, in a few words. The bot logs the raw reason of each refused order (bot_events
    kind 'cap') and that reason always wins: the bot checks the stop, kill switch and halt before the price, and the price
    bound itself has changed (99c, then 99.9c), so the row's own price says nothing certain about why an order was refused. A
    price message names its bound, which is what the label quotes. Only when no reason was logged is the detection price used,
    and then only above every limit the bot has had (PRICE_CEILING), where it is surely the price."""
    text = (reason or "").strip()
    if text:
        m = _PRICE_OUTSIDE.search(text)
        if m:
            price, lo, hi = float(m.group(1)), float(m.group(2)), float(m.group(3))
            if price > hi:
                return f"price above {_cents(hi)}"
            return f"price below {_cents(lo)}" if price < lo else "price outside the bot's limits"
        low = text.lower()
        for needle, label in _SKIP_WORDS:
            if needle in low:
                return label
        return _clip(text, 70)
    if ask is not None and ask > PRICE_CEILING + 1e-9:
        return "price above the bot's limit"
    return "blocked before sending"


_PRICE_SKIP = re.compile(r"price (?:above|below|outside) ")


def _is_price_skip(label: str) -> bool:
    """A skip label that is the bot's price limit doing its job (shown without the warning tone)."""
    return _PRICE_SKIP.match(label) is not None


def _stale(r: dict, now: float) -> bool:
    """A row detected more than FRESH_S ago: whatever the bot was going to write about it, it would have written by now."""
    detected = _num(r.get("detected_ts"))
    return detected is not None and now - detected > FRESH_S


def _skip_reasons(conn: sqlite3.Connection | None, rows: Any) -> dict[int, str]:
    """{opportunity id: raw reason the bot gave} for the refused signals (outcome blocked_by_cap) among ``rows``, from
    bot_events (kind 'cap': "live order not sent: <reason>", with the opportunity id in its data). Only events logged after the
    oldest refused signal are read, and the newest event of a signal wins. The text is scrubbed of anything secret-like."""
    wanted = {r["id"]: _num(r.get("detected_ts")) for r in rows if r.get("outcome") == "blocked_by_cap" and isinstance(r.get("id"), int)}
    if conn is None or not wanted:
        return {}
    times = [t for t in wanted.values() if t is not None]
    since = min(times) - 60.0 if times else 0.0                # a refusal is logged right after its signal was detected
    out: dict[int, str] = {}
    for ev in _query(conn, "SELECT message, data_json FROM bot_events WHERE kind = 'cap' AND ts >= ? ORDER BY ts DESC LIMIT 5000",
                     (since,)):
        try:
            data = json.loads(ev.get("data_json") or "null")
        except ValueError:
            continue
        oid = data.get("opportunity_id") if isinstance(data, dict) else None
        if isinstance(oid, int) and oid in wanted and oid not in out and isinstance(ev.get("message"), str):
            msg = _scrub_text(ev["message"])
            out[oid] = msg.split("live order not sent:", 1)[-1].strip()
    return out


def trade_status(r: dict, now: float, reason: str | None = None, filled: bool = False) -> dict:
    """What happened to one bot_opportunities row, in plain words (``label``), with the numbers behind it.

    state    settled | open | overdue | unwound | not_filled | skipped | paper_only | in_progress | no_outcome | unknown
    result   "won" / "lost" once a real trade settled (a late_favorite contract won when it paid out), else None
    real     real money was put to work (an order filled); ``settled``: and its P&L is booked
    label    "Settled · WON +$0.25" · "Settled · LOST −$0.74" · "Open · settles at 15:00" · "Not filled" ·
             "Skipped · price above 99c" (the real reason, not just "blocked") · "Paper only"
    ``close_ts`` is when the market closes, ``paper_label`` the paper twin's P&L ("paper +$0.24") and ``detail`` one more
    line of explanation. ``reason`` is the raw reason the bot gave for a refused signal, when it is known. ``filled`` says that a
    real order of this row has filled (see _unbooked_fills): a late_favorite row that has no outcome yet but a filled order is an
    open trade already, because the bot writes the outcome only some seconds after the fill."""
    outcome = r.get("outcome")
    live = bool(r.get("live"))
    pnl, paper = _num(r.get("realized_pnl")), _num(r.get("paper_realized_pnl"))
    close = _market_close_ts(r)
    real = outcome in REAL_OUTCOMES or bool(filled and outcome is None and live and _is_late(r) and not _stale(r, now))
    hm = _hm(close) if close is not None else None
    paper_text = f"paper {_money(paper, True)}" if paper is not None else None

    def make(state: str, label: str, tone: str, detail: str | None = None, result: str | None = None) -> dict:
        return {"state": state, "label": label, "tone": tone, "result": result, "real": real,
                "settled": real and pnl is not None, "pnl": pnl if real else None, "paper_pnl": paper,
                "paper_label": paper_text, "close_ts": close, "side": _side(r), "detail": detail}

    def waiting(prefix: str, tone: str) -> dict:      # a filled trade whose market has not settled (or not been booked)
        if close is None or hm is None:
            return make("open", prefix + "waiting for settlement", tone)
        if now >= close + OVERDUE_S:
            return make("overdue", f"Overdue · no result recorded since {hm}", "warn",
                        "the market closed a while ago but the bot has not booked its result; see the Events table")
        if now >= close:
            return make("open", f"{prefix}market closed {hm}, waiting for the result", tone)
        return make("open", f"{prefix}settles at {hm}", tone)

    if real:
        if outcome == "one_leg_unwound":
            return make("unwound", "Closed · one leg unwound" + (f" {_money(pnl, True)}" if pnl is not None else ""), "serious",
                        "one leg filled and was sold back", "won" if pnl is not None and pnl > 0 else "lost")
        if pnl is None:
            return waiting("Open · one leg held, " if outcome == "one_leg_held" else "Open · ",
                           "crit" if outcome == "one_leg_held" else "info")
        won = _won(r, pnl)
        return make("settled", f"Settled · {'WON' if won else 'LOST'} {_money(pnl, True)}", "good" if won else "crit",
                    "the side won, but fees were larger than the profit" if won and pnl < 0 else None,
                    "won" if won else "lost")
    if outcome == "none_filled":
        return make("not_filled", "Not filled", "",
                    "the order was sent and nothing filled" if live else "no order could be sent (thin book)")
    if outcome == "blocked_by_cap":
        why = _skip_label(reason, _per_contract(r.get("paper_cost"), r))
        return make("skipped", "Skipped · " + why, "" if _is_price_skip(why) else "warn",
                    _clip(reason, 200) if reason else None)
    if outcome == "unknown":
        return make("unknown", "Unknown · order result not confirmed", "crit",
                    "the bot could not confirm what happened to the order; check Kalshi")
    if outcome == "paper_only":
        if paper is not None:
            twin = f"paper twin {'WON' if _won(r, paper, paper=True) else 'LOST'} {_money(paper, True)}"
        elif close is not None and now >= close:
            twin = f"paper twin: market closed {hm}, waiting for the result"
        else:
            twin = f"paper twin settles at {hm}" if hm else "paper twin not settled yet"
        return make("paper_only", "Paper only", "", twin)
    if outcome is None:
        if _stale(r, now):
            return make("no_outcome", "No outcome recorded", "warn", "the bot never wrote what happened to this signal")
        return make("in_progress", "In progress", "info",
                    "order sent, waiting for the result" if live else "the bot is deciding or placing the order")
    return make("other", str(outcome).replace("_", " "), "")


def order_status(o: dict, trade: dict | None, now: float) -> dict:
    """The state shown next to an order. A real order that filled shares the state of its trade, one that did not fill is
    "Not filled", a paper order shows its paper twin, and an order the bot decided not to send says so. A fill whose trade the
    bot is still writing (its row is fresh and has no outcome yet) is "booking", an information, not the warning that is kept
    for a fill that no trade accounts for."""
    real = o.get("dry_run") in (0, False)
    status = str(o.get("status") or "")
    filled = _num(o.get("filled")) or 0.0

    def make(state: str, label: str, tone: str, detail: str | None = None) -> dict:
        return {"state": state, "label": label, "tone": tone, "result": None, "real": real, "settled": False, "pnl": None,
                "paper_pnl": None, "paper_label": None, "close_ts": None, "side": None, "detail": detail}

    if real:
        if filled > 0:
            if trade and trade["real"]:
                return dict(trade)
            if trade and trade["state"] == "in_progress":
                return make("booking", "Filled · the bot is recording the trade", "info",
                            "the order filled; the bot writes the trade a few seconds later")
            # real money filled, but there is no trade row that says so: never show that as "Not filled" or "Paper only"
            return make("filled", "Filled", "warn", "the order filled, but no filled trade is linked to it: check Kalshi"
                        if trade else "no trade row is linked to this order")
        if status in ("canceled", "rejected", "throttled", "auth_error"):
            return make("not_filled", "Not filled", "", "order " + status.replace("_", " "))
        if status == "unknown":
            return make("unknown", "Unknown · order result not confirmed", "crit",
                        "the bot could not confirm what happened to the order; check Kalshi")
        return make("in_flight", "Sent · waiting for the exchange", "info")
    if status == "not_sent":
        return make("not_sent", "Not sent", "", "the bot decided against sending it at the last check")
    return dict(trade) if trade else make("paper_only", "Paper only", "")


def _safe(fn: Callable[..., dict], *args: Any) -> dict:
    """A row with odd data must never turn the whole answer into an error."""
    try:
        return fn(*args)
    except (TypeError, ValueError, ArithmeticError, KeyError, AttributeError):
        return {"state": "other", "label": "Status unavailable", "tone": "", "result": None, "real": False, "settled": False,
                "pnl": None, "paper_pnl": None, "paper_label": None, "close_ts": None, "side": None, "detail": None}


def _unbooked_fills(conn: sqlite3.Connection | None, rows: Any, now: float) -> set[int]:
    """Ids of the late_favorite rows among ``rows`` whose real order has filled although the row has no outcome yet.

    The bot sets live=1 on the row before it sends, writes the fill on the order at the exchange's answer, and writes the row's
    outcome only when its decision window closes, a few seconds later (it reconciles with the exchange first). For those seconds
    the order is the only record of the trade, and without this look the page would call a filled order a mystery. A row detected
    more than FRESH_S ago is left out: a fill the bot never booked is a warning, not a trade about to be booked."""
    ids = sorted({r["id"] for r in rows if isinstance(r.get("id"), int) and r.get("live") and r.get("outcome") is None
                  and _is_late(r) and not _stale(r, now)})
    out: set[int] = set()
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        for o in _query(conn, "SELECT DISTINCT opportunity_id FROM bot_orders WHERE dry_run = 0 AND filled > 0 "
                              f"AND opportunity_id IN ({','.join('?' * len(chunk))})", tuple(chunk)):
            out.add(o["opportunity_id"])
    return out


def _attach_trades(conn: sqlite3.Connection | None, rows: list[dict], now: float) -> None:
    reasons = _skip_reasons(conn, rows)
    unbooked = _unbooked_fills(conn, rows, now)
    for r in rows:
        r["trade"] = _safe(trade_status, r, now, reasons.get(r.get("id")), r.get("id") in unbooked)


def _money_tag(o: dict) -> str:
    """The words for the Money column of the orders table: real money, paper, or not sent. Never "LIVE", which a settled trade
    would still wear long after it was over."""
    if o.get("dry_run") in (0, False):
        return "real money"
    return "not sent" if str(o.get("status") or "") == "not_sent" else "paper"


def _attach_order_trades(conn: sqlite3.Connection | None, rows: list[dict], now: float) -> None:
    ids = sorted({r["opportunity_id"] for r in rows if isinstance(r.get("opportunity_id"), int)})
    opps: dict[int, dict] = {}
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        for o in _query(conn, f"SELECT * FROM bot_opportunities WHERE id IN ({','.join('?' * len(chunk))})", tuple(chunk)):
            opps[o["id"]] = o
    reasons = _skip_reasons(conn, opps.values())
    unbooked = _unbooked_fills(conn, opps.values(), now)
    for r in rows:
        opp = opps.get(r.get("opportunity_id"))
        trade = _safe(trade_status, opp, now, reasons.get(opp["id"]), opp["id"] in unbooked) if opp else None
        r["money"] = _money_tag(r)
        r["trade"] = _safe(order_status, r, trade, now)


# ----------------------------------------------------------------------------------------------- files

def _read_json(path: Path) -> Any:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _cell(v: str | None) -> Any:
    if v is None or not v.strip():
        return None
    s = v.strip()
    if s in ("True", "true"):
        return True
    if s in ("False", "false"):
        return False
    if _INT_RE.fullmatch(s):
        return int(s)
    try:
        f = float(s)
    except ValueError:
        return s
    return f if math.isfinite(f) else None


def _read_csv(path: Path, max_rows: int = 2000) -> list[dict]:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return []
        with open(path, newline="", encoding="utf-8") as fh:
            out = []
            for i, row in enumerate(csv.DictReader(fh)):
                if i >= max_rows:
                    break
                out.append({k: _cell(v) for k, v in row.items() if isinstance(k, str)})
            return out
    except (OSError, csv.Error, UnicodeDecodeError):
        return []


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


# ----------------------------------------------------------------------------------------------- API

def _caps(trading: dict, run: dict | None) -> tuple[dict, str, list[dict]]:
    """(caps, source, drift). ``caps`` are the limits to show: settings.yaml, overridden by what the latest run recorded
    in bot_runs.config_json (the limits IN FORCE for that run), with numeric strings turned into numbers. ``source`` is
    "run" when the run supplied any of them, else "config". ``drift`` lists {key, run, config} where the file now
    differs from what the run started with (a limit edited after the start has no effect on the running bot)."""
    caps: dict[str, Any] = {k: _as_number(trading.get(k)) for k in CAP_KEYS}
    for block in ("pair", "late_favorite"):
        v = trading.get(block)
        caps[block] = {k: _as_number(x) for k, x in v.items()} if isinstance(v, dict) else {}
    drift: list[dict] = []
    source = "config"
    rc = run.get("config") if run else None
    if not isinstance(rc, dict):
        return caps, source, drift

    def take(target: dict, run_key: str, key: str) -> None:
        nonlocal source
        rv = _as_number(rc.get(run_key))
        if rv is None or isinstance(rv, (dict, list)):
            return
        if target.get(key) != rv and target.get(key) is not None:
            drift.append({"key": key, "run": rv, "config": target.get(key)})
        target[key] = rv
        source = "run"

    for k in RUN_CAP_KEYS:
        take(caps, k, k)
    block, mapping = RUN_STRATEGY_KEYS.get(str(run.get("strategy")), (None, {}))
    if block:
        for run_key, key in mapping.items():
            take(caps[block], run_key, key)
        rule = rc.get("rule")
        if block == "late_favorite" and isinstance(rule, dict):          # the fixed rule, as the bot recorded it
            for run_key, key in (("window_start_s", "window_start_s"), ("window_end_s", "window_end_s"),
                                 ("buy_yes_if_mid_above", "up_threshold"), ("buy_no_if_mid_below", "down_threshold")):
                rv = _as_number(rule.get(run_key))
                if rv is not None and not isinstance(rv, (dict, list)):
                    caps[block][key] = rv
    here = kill_switch_path({"trading": trading})
    kf = rc.get("kill_switch_file")
    if isinstance(kf, str) and kf and not _same_path(kf, here):
        drift.append({"key": "kill_switch_file", "run": kf, "config": str(here)})
    return caps, source, drift


def _other_live_runs(conn: sqlite3.Connection | None, run_id: str, now: float) -> list[dict]:
    """Live runs other than the latest one that have not ended and are still beating: a second bot trading at the same
    time (the headline badge only describes the newest run)."""
    rows = _query(conn, "SELECT r.run_id, r.strategy, r.started_ts, h.ts AS heartbeat_ts FROM bot_runs r "
                        "LEFT JOIN bot_heartbeat h ON h.run_id = r.run_id "
                        "WHERE r.mode = 'live' AND r.ended_ts IS NULL AND r.run_id != ?", (run_id,))
    out = []
    for r in rows:
        ts = _num(r.get("heartbeat_ts"))
        if ts is not None and 0 <= now - ts <= OTHER_RUN_FRESH_S:
            out.append({"run_id": r["run_id"], "strategy": r["strategy"], "heartbeat_age_s": now - ts})
    return out


def api_status(conn: sqlite3.Connection | None, cfg: dict, q: dict, now: float) -> dict:
    run = _latest_run(conn)
    heartbeat = latest_quote = None
    other_live: list[dict] = []
    if run:
        run["config"] = _parse_json(run.pop("config_json", None))
        if isinstance(run.get("end_reason"), str):
            run["end_reason"] = _scrub_text(run["end_reason"])
        hb = _query(conn, "SELECT * FROM bot_heartbeat WHERE run_id = ?", (run["run_id"],))
        if hb:
            heartbeat = hb[0]
            heartbeat["detail"] = _parse_json(heartbeat.pop("detail_json", None))
            ts = _num(heartbeat.get("ts"))
            heartbeat["age_s"] = now - ts if ts is not None else None
        lq = _query(conn, "SELECT ts, hour_ticker FROM bot_quotes WHERE run_id = ? ORDER BY ts DESC LIMIT 1",
                    (run["run_id"],))
        latest_quote = lq[0] if lq else None
        other_live = _other_live_runs(conn, run["run_id"], now)
    trading = cfg.get("trading") or {}
    caps, caps_source, caps_drift = _caps(trading, run)
    refresh = _num((cfg.get("dashboard") or {}).get("refresh_s")) or 2.0
    return {
        "server_time": now,
        "db_available": conn is not None,
        "run": run,
        "strategy": run.get("strategy") if run else None,
        "heartbeat": heartbeat,
        "latest_quote": latest_quote,
        "other_live_runs": other_live,
        "caps": caps,
        "caps_source": caps_source,
        "caps_drift": caps_drift,
        "kill_switch_active": kill_switch_path(cfg).exists(),
        "kill_switch_file": str(trading.get("kill_switch_file") or DEFAULT_KILL_FILE),
        "kill_switch_path": str(kill_switch_path(cfg)),
        "stats": {"run": _stats(conn, run["run_id"]) if run else None, "all": _stats(conn, None)},
        "dashboard": {"refresh_s": min(60.0, max(0.5, refresh))},
    }


def api_quotes(conn: sqlite3.Connection | None, cfg: dict, q: dict, now: float) -> dict:
    """Pair pricing per poll for the live chart, oldest first. ?minutes=30 window; ?since=<ts> for increments."""
    minutes = _arg_num(q, "minutes", 30.0, 1.0, 240.0) or 30.0
    lo = now - minutes * 60
    since = _arg_num(q, "since", None, -1e12, 1e12)
    if since is not None:
        lo = max(lo, since)
    run_id, all_runs = _run_filter(conn, q)
    base = {"run_id": run_id, "all_runs": all_runs, "minutes": minutes, "from_ts": lo, "server_time": now}
    if not all_runs and run_id is None:
        return {**base, "rows": []}
    if all_runs:
        rows = _query(conn, "SELECT * FROM bot_quotes WHERE ts >= ? ORDER BY ts DESC LIMIT ?", (lo, MAX_QUOTE_ROWS))
    else:
        rows = _query(conn, "SELECT * FROM bot_quotes WHERE run_id = ? AND ts >= ? ORDER BY ts DESC LIMIT ?",
                      (run_id, lo, MAX_QUOTE_ROWS))
        for r in rows:
            r.pop("run_id", None)
    rows.sort(key=lambda r: (_num(r.get("ts")) or 0.0, str(r.get("strike_ticker"))))
    return {**base, "rows": rows}


def api_opportunities(conn: sqlite3.Connection | None, cfg: dict, q: dict, now: float) -> dict:
    out = _list(conn, q, "bot_opportunities", "detected_ts DESC, id DESC", 100, 1000, _decorate_opportunity)
    _attach_trades(conn, out["rows"], now)
    return out


def api_orders(conn: sqlite3.Connection | None, cfg: dict, q: dict, now: float) -> dict:
    out = _list(conn, q, "bot_orders", "COALESCE(ts_sent, ts_ack, 0) DESC, rowid DESC", 100, 1000, _decorate_order)
    _attach_order_trades(conn, out["rows"], now)
    return out


def _rounded(v: Any) -> Any:
    """Floats to 6 decimals, so sums of cents do not show as 0.5027630000000001."""
    if isinstance(v, float):
        return round(v, 6)
    if isinstance(v, dict):
        return {k: _rounded(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_rounded(x) for x in v]
    return v


def _summary_template() -> dict:
    return {"signals": 0, "settled": 0, "open": 0, "overdue": 0, "wins": 0, "losses": 0, "win_rate": None, "pnl": None,
            "paper_pnl": None, "paper_n": 0, "delay_cost": None, "avg_pnl": None, "best": None, "worst": None, "fees": None,
            "fees_pending": 0, "avg_latency_ms": None, "avg_fill_vs_paper_cents": None, "skipped": 0, "skipped_reasons": {},
            "not_filled": 0, "paper_only": 0, "unknown": 0, "first_ts": None, "last_ts": None}


def _trade_item(r: dict, t: dict, paper_view: bool) -> dict:
    """One settled trade for the graphs. In the paper view (a watch-only scope) the paper twin is the trade."""
    pnl, paper = t["paper_pnl"] if paper_view else t["pnl"], t["paper_pnl"]
    entry = _per_contract(r.get("paper_cost") if paper_view else r.get("live_cost"), r)
    won = _won(r, pnl, paper=True) if paper_view else (t["result"] == "won")
    return {"id": r.get("id"), "run_id": r.get("run_id"), "ts": _num(r.get("detected_ts")), "close_ts": t["close_ts"],
            "ticker": r.get("hour_ticker"), "side": t["side"], "contracts": _num(r.get("contracts")), "entry": entry,
            "paper_entry": _per_contract(r.get("paper_cost"), r), "fees": _num(r.get("paper_fees") if paper_view else r.get("live_fees")),
            "pnl": pnl, "paper_pnl": paper, "payout": _num(r.get("payout")), "result": "won" if won else "lost",
            "latency_ms": None if paper_view else _num(r.get("latency_ms"))}


def api_performance(conn: sqlite3.Connection | None, cfg: dict, q: dict, now: float) -> dict:
    """The numbers behind the page's money tiles and graphs, for the latest run (default), ?run=all or ?run=<id>.

    ``view`` is "real" when the scope has real orders (a live run): the trades are the filled orders whose market settled,
    each with the P&L the exchange paid and the P&L of its paper twin (the same trade at the detection price). A scope with
    no real orders (watch-only) has view "paper": the trades are the paper twins that settled. ``trades`` is oldest first
    with running totals (``cum``, ``cum_paper``); when more than ?limit settled trades exist the oldest are left out and
    ``base`` holds the running totals before the first one listed. ``open`` lists trades whose market has not settled, among them
    a fill the bot has not written the outcome of yet (see _unbooked_fills). Wins and losses follow the contract (a late_favorite
    contract that paid out won). In the summary ``delay_cost`` is the real P&L minus the paper P&L of the same settled trades, so
    a POSITIVE value means the real fills did better than their paper twins; ``fees``, ``avg_latency_ms`` and
    ``avg_fill_vs_paper_cents`` cover every real fill, open ones included, and everything else only settled trades. Empty tables
    give an empty answer."""
    run_id, all_runs = _run_filter(conn, q)
    cap = int(_arg_num(q, "limit", 300, 1, MAX_PERF_TRADES) or 300)
    out: dict[str, Any] = {"run_id": run_id, "all_runs": all_runs, "server_time": now, "view": "real", "strategy": None,
                           "mode": None, "since": None, "truncated": False, "summary": _summary_template(),
                           "base": {"pnl": 0.0, "paper_pnl": 0.0}, "trades": [], "open": []}
    if conn is None or (not all_runs and run_id is None):
        return out
    runs = _query(conn, "SELECT run_id, strategy, mode, started_ts FROM bot_runs" + ("" if all_runs else " WHERE run_id = ?"),
                  () if all_runs else (run_id,))
    where, params = ("", ()) if all_runs else ("WHERE run_id = ?", (run_id,))
    rows = _query(conn, f"SELECT * FROM bot_opportunities {where} ORDER BY detected_ts DESC, id DESC LIMIT ?",
                  (*params, MAX_PERF_ROWS + 1))
    if len(rows) > MAX_PERF_ROWS:
        out["truncated"], rows = True, rows[:MAX_PERF_ROWS]
    rows.reverse()                                                   # oldest first
    modes = {str(r.get("mode")) for r in runs}
    strategies = {str(r.get("strategy")) for r in runs}
    out["mode"] = modes.pop() if len(modes) == 1 else ("mixed" if modes else None)
    out["strategy"] = strategies.pop() if len(strategies) == 1 else None
    live_scope = any(str(r.get("mode")) == "live" for r in runs) or any(r.get("live") for r in rows)
    paper_view = not live_scope and bool(runs or rows)          # nothing at all yet reads as the (empty) real view
    out["view"] = "paper" if paper_view else "real"
    starts = [_num(r.get("started_ts")) for r in runs if not live_scope or str(r.get("mode")) == "live"]
    out["since"] = min((s for s in starts if s is not None), default=None)

    reasons = _skip_reasons(conn, rows)
    unbooked = _unbooked_fills(conn, rows, now)                      # filled a moment ago, the bot has not written the outcome yet
    s = _summary_template()
    s["signals"] = len(rows)
    settled: list[tuple[dict, dict]] = []
    opens: list[tuple[dict, dict]] = []
    fees, lat, diffs = [], [], []
    for r in rows:
        t = _safe(trade_status, r, now, reasons.get(r.get("id")), r.get("id") in unbooked)
        ts = _num(r.get("detected_ts"))
        if ts is not None:
            s["first_ts"] = ts if s["first_ts"] is None else min(s["first_ts"], ts)
            s["last_ts"] = ts if s["last_ts"] is None else max(s["last_ts"], ts)
        state = t["state"]
        if t["real"]:
            lf = _num(r.get("live_fees"))
            if lf is None and _num(r.get("live_cost")) is not None:
                s["fees_pending"] += 1
            elif lf is not None:
                fees.append(lf)
            if _num(r.get("latency_ms")) is not None:
                lat.append(float(r["latency_ms"]))
            lc, pc = _per_contract(r.get("live_cost"), r), _per_contract(r.get("paper_cost"), r)
            if lc is not None and pc is not None:
                diffs.append((lc - pc) * 100.0)
            (settled if t["settled"] else opens).append((r, t))
        elif state == "skipped":
            s["skipped"] += 1
            why = t["label"].split(" · ", 1)[-1]
            s["skipped_reasons"][why] = s["skipped_reasons"].get(why, 0) + 1
        elif state == "not_filled":
            s["not_filled"] += 1
        elif state == "unknown":
            s["unknown"] += 1
        elif state == "paper_only":
            s["paper_only"] += 1
            if paper_view:
                (settled if t["paper_pnl"] is not None else opens).append((r, t))
    key = lambda rt: (rt[1]["close_ts"] or _num(rt[0].get("detected_ts")) or 0.0, rt[0].get("id") or 0)   # noqa: E731
    settled.sort(key=key)
    opens.sort(key=key)

    items, cum, cum_p = [], 0.0, 0.0
    for r, t in settled:
        it = _trade_item(r, t, paper_view)
        cum += it["pnl"]
        cum_p += it["paper_pnl"] if it["paper_pnl"] is not None else 0.0
        it["cum"], it["cum_paper"] = cum, cum_p
        items.append(it)
    if len(items) > cap:
        cut = items[len(items) - cap - 1]
        out["base"] = {"pnl": cut["cum"], "paper_pnl": cut["cum_paper"]}
        out["truncated"] = True
        items = items[-cap:]
    out["trades"] = items
    out["open"] = [{"id": r.get("id"), "ts": _num(r.get("detected_ts")), "close_ts": t["close_ts"], "ticker": r.get("hour_ticker"),
                    "side": t["side"], "contracts": _num(r.get("contracts")), "state": t["state"], "label": t["label"],
                    "entry": _per_contract(r.get("paper_cost") if paper_view else r.get("live_cost"), r)} for r, t in opens]

    # summary over EVERY settled trade (not only the listed ones)
    allp = [(_trade_item(r, t, paper_view)) for r, t in settled] if len(items) < len(settled) else items
    n = len(allp)
    s["settled"], s["open"] = n, len(opens)
    s["overdue"] = sum(1 for _, t in opens if t["state"] == "overdue")
    s["wins"] = sum(1 for it in allp if it["result"] == "won")
    s["losses"] = n - s["wins"]
    if n:
        s["win_rate"] = s["wins"] / n
        s["pnl"] = sum(it["pnl"] for it in allp)
        s["avg_pnl"] = s["pnl"] / n
        best, worst = max(allp, key=lambda it: it["pnl"]), min(allp, key=lambda it: it["pnl"])
        s["best"] = {k: best[k] for k in ("id", "pnl", "ticker", "side", "close_ts")}
        s["worst"] = {k: worst[k] for k in ("id", "pnl", "ticker", "side", "close_ts")}
        known = [it for it in allp if it["paper_pnl"] is not None]
        s["paper_n"] = len(known)
        if known:
            s["paper_pnl"] = sum(it["paper_pnl"] for it in known)
            s["delay_cost"] = sum(it["pnl"] for it in known) - s["paper_pnl"] if not paper_view else None   # real - paper: > 0 is a gain
    if not paper_view:
        s["fees"] = sum(fees) if fees or s["fees_pending"] == 0 else None
        s["avg_latency_ms"] = sum(lat) / len(lat) if lat else None
        s["avg_fill_vs_paper_cents"] = sum(diffs) / len(diffs) if diffs else None
    out["summary"] = s
    return _rounded(out)


def api_events(conn: sqlite3.Connection | None, cfg: dict, q: dict, now: float) -> dict:
    # Events without a run (e.g. a start-up error before the run row exists) are shown with the latest run.
    return _list(conn, q, "bot_events", "ts DESC, id DESC", 200, 2000, _decorate_event, include_unscoped=True)


def api_research(conn: sqlite3.Connection | None, cfg: dict, q: dict, now: float) -> dict:
    d = Path((cfg.get("paths") or {}).get("report_dir") or Path(PROJECT_ROOT) / "reports" / "output") / RESEARCH_SUBDIR
    files = {"h1_summary": "h1_summary.json", "h1_verdict": "h1_final_minute_verdict.csv",
             "h2_summary": "h2_summary.json", "h2_by_split": "h2_cross_bounds_summary.csv",
             "h4_summary": "h4_lip_summary.json"}
    h2 = _read_json(d / files["h2_summary"])
    by_split = _read_csv(d / files["h2_by_split"])
    if not by_split and isinstance(h2, dict) and isinstance(h2.get("by_split"), list):
        by_split = h2["by_split"]
    progress = {}
    for key, (component, action, total) in PROGRESS.items():
        r = _query(conn, "SELECT COUNT(DISTINCT entity_id) AS n FROM audit_log WHERE component = ? AND action = ?",
                   (component, action))
        progress[key] = {"done": r[0]["n"] if r else None, "total": total, "component": component, "action": action}
    return {
        "h1_summary": _read_json(d / files["h1_summary"]),
        "h1_verdict": _read_csv(d / files["h1_verdict"]),
        "h2_summary": h2,
        "h2_by_split": by_split,
        "h4_summary": _read_json(d / files["h4_summary"]),
        "updated": {k: _mtime(d / f) for k, f in files.items()},
        "progress": progress,
        "server_time": now,
    }


ROUTES: dict[str, Callable[[sqlite3.Connection | None, dict, dict, float], dict]] = {
    "/api/status": api_status,
    "/api/quotes": api_quotes,
    "/api/opportunities": api_opportunities,
    "/api/orders": api_orders,
    "/api/performance": api_performance,
    "/api/events": api_events,
    "/api/research": api_research,
}


def create_kill_file(cfg: dict, reason: str = KILL_REASON) -> Path:
    """Create (or append to) the kill-switch file with a UTC timestamp and the reason."""
    path = kill_switch_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{stamp} {reason}\n")
    return path


# ----------------------------------------------------------------------------------------------- HTTP

class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "kpm-dashboard"
    sys_version = ""
    timeout = 30

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_OPTIONS(self) -> None:  # noqa: N802  CORS preflights are never approved
        self._dispatch("OPTIONS")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    # -- routing
    def _dispatch(self, method: str) -> None:
        try:
            port = self.server.server_address[1]
            if not host_allowed(self.headers.get("Host"), port):
                self._json({"ok": False, "error": "forbidden host"}, 403)
                return
            if not self.path.startswith("/") or self.path.startswith("//"):
                self._json({"ok": False, "error": "bad request target"}, 400)
                return
            path, _, query = self.path.partition("?")
            if not self._fetch_site_ok(method, path):
                self._json({"ok": False, "error": "cross-site request refused"}, 403)
                return
            if path == "/api/kill":
                if method != "POST":
                    self._json({"ok": False, "error": "use POST"}, 405, {"Allow": "POST"})
                else:
                    self._kill(port)
                return
            if method != "GET":
                self._json({"ok": False, "error": "method not allowed"}, 405, {"Allow": "GET"})
                return
            if path.startswith("/api/"):
                self._api(path, query)
            elif path == "/":
                self._static("index.html")
            elif path.startswith("/static/"):
                self._static(unquote(path[len("/static/"):]))
            else:
                self._json({"ok": False, "error": "not found"}, 404)
        except ConnectionError:   # the browser went away mid-response (reload, closed tab): nothing to answer
            return
        except Exception:   # never leak a traceback to the page; log it locally
            log.exception("dashboard request failed: %s %s", method, self.path)
            try:
                self._json({"ok": False, "error": "internal error"}, 500)
            except Exception:
                pass

    def _fetch_site_ok(self, method: str, path: str) -> bool:
        """Fetch Metadata resource isolation. A browser tells a request's origin in Sec-Fetch-Site: any page we did not
        serve (a cross-site or same-site <img>, fetch or form) must not make this server run queries for it, even though
        the browser would hide the answer. Allowed: same-origin, "none" (a typed URL or bookmark), no header at all (curl,
        Python), and one cross-site case: the user following a link to the page itself (a top-level navigation to "/")."""
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site in ("", "same-origin", "none"):
            return True
        mode = (self.headers.get("Sec-Fetch-Mode") or "").strip().lower()
        dest = (self.headers.get("Sec-Fetch-Dest") or "").strip().lower()
        return method == "GET" and path == "/" and mode == "navigate" and dest in ("", "document")

    def _api(self, path: str, query: str) -> None:
        fn = ROUTES.get(path)
        if fn is None:
            self._json({"ok": False, "error": "not found"}, 404)
            return
        cfg = settings()
        conn = _open_db((cfg.get("paths") or {}).get("db_path"))
        try:
            body = fn(conn, cfg, parse_qs(query), time.time())
        finally:
            if conn is not None:
                conn.close()
        self._json(body)

    def _kill(self, port: int) -> None:
        if (self.headers.get(KILL_HEADER) or "").strip() != "1":
            self._json({"ok": False, "error": f"missing {KILL_HEADER}: 1 header"}, 403)
            return
        origin = self.headers.get("Origin")
        if origin is not None:
            o = urlsplit(origin)
            if o.scheme != "http" or not host_allowed(o.netloc, port):
                self._json({"ok": False, "error": "cross-origin request refused"}, 403)
                return
        if (self.headers.get("Sec-Fetch-Site") or "same-origin").lower() not in ("same-origin", "none"):
            self._json({"ok": False, "error": "cross-site request refused"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._json({"ok": False, "error": "bad Content-Length"}, 400)
            return
        if length > MAX_BODY_BYTES:
            self._json({"ok": False, "error": "body too large"}, 413)
            return
        if length > 0:
            self.rfile.read(length)   # nothing is expected in the body; drain it
        cfg = settings()
        path = create_kill_file(cfg)
        log.warning("kill switch created from the dashboard STOP button: %s", path)
        self._json({"ok": True, "kill_switch_active": True, "kill_switch_path": str(path),
                    "kill_switch_file": str((cfg.get("trading") or {}).get("kill_switch_file") or DEFAULT_KILL_FILE)})

    def _static(self, name: str) -> None:
        # Only exact names of regular files directly inside static/ are served: no listing, no traversal.
        files = {p.name: p for p in STATIC_DIR.iterdir() if p.is_file() and not p.name.startswith(".")} \
            if STATIC_DIR.is_dir() else {}
        p = files.get(name)
        if p is None:
            self._json({"ok": False, "error": "not found"}, 404)
            return
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(p.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    # -- responses
    def _security_headers(self) -> None:
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)

    def _json(self, body: Any, status: int = 200, extra: dict[str, str] | None = None) -> None:
        data = json.dumps(_jsonable(body), separators=(",", ":"), allow_nan=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    # -- logging: quiet for the 2-second polling, loud for errors and refusals
    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        try:
            status = int(code)
        except (TypeError, ValueError):
            status = 0
        if status >= 400:
            log.warning('dashboard %s "%s" %s', self.address_string(), self.requestline, status)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (stdlib signature)
        log.info("dashboard %s %s", self.address_string(), format % args)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR would let a second process bind the same port; use exclusive binding instead.
    allow_reuse_address = os.name != "nt"

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, handler)

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)   # skips HTTPServer's getfqdn(), which can stall on Windows
        self.server_name, self.server_port = str(self.server_address[0]), int(self.server_address[1])


def make_server(host: str | None = None, port: int | None = None) -> DashboardServer:
    """Build (but do not start) the server. Refuses any address other than loopback."""
    d = settings().get("dashboard") or {}
    host = str(host if host is not None else d.get("host", "127.0.0.1")).strip()
    if not _is_loopback(host):
        raise ValueError(f"the dashboard only binds to localhost (127.0.0.1, localhost or ::1), not {host!r}")
    port = int(port if port is not None else d.get("port", 8765))
    if not 0 <= port <= 65535:
        raise ValueError(f"invalid port {port}")
    name = host.strip("[]").lower()
    bind = "127.0.0.1" if name == "localhost" else name
    return DashboardServer((bind, port), DashboardHandler)


def serve(host: str | None = None, port: int | None = None, open_browser: bool = False) -> None:
    """Run until Ctrl+C. Defaults come from settings()["dashboard"]; open_browser opens the page once started."""
    srv = make_server(host, port)
    h, p = srv.server_address[0], srv.server_address[1]
    url = f"http://{'[' + h + ']' if ':' in h else h}:{p}/"
    print(f"KPM dashboard running at {url}  (localhost only; Ctrl+C to stop)", flush=True)
    log.info("dashboard listening on %s", url)
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
