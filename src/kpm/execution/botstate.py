"""Bot state for the localhost dashboard (kpm.dashboard.server reads it).

Writes bot_runs, bot_heartbeat, bot_events, bot_quotes, bot_opportunities and bot_orders exactly as defined in
src/kpm/core/schema.sql:

- Times are epoch seconds (float, UTC). Prices and money are dollars.
- These dashboard tables use REAL columns, so a Decimal becomes a float only here, at the display boundary. The exact
  TEXT-decimal record of every order lives in the kpm.execution.ledger tables, not here.
- Every write is committed at once, because the dashboard opens the database read-only on every request.
- JSON columns never carry secrets. Keys that look like credentials (key ids, key paths, PEMs, passwords,
  signatures, tokens, headers) are dropped, and PEM-looking strings are replaced, before anything is written.
  Callers must still never pass secrets in values.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping

TABLES = ("bot_runs", "bot_heartbeat", "bot_events", "bot_quotes", "bot_opportunities", "bot_orders")
MODES = ("watch", "live")
STATUSES = ("waiting", "monitoring", "trading", "stopped", "error")
LEVELS = ("info", "warn", "error")
# The kinds documented in schema.sql, plus settle / smoke / stop.
EVENT_KINDS = ("start", "window", "opportunity", "order", "fill", "unwind", "cap", "kill", "error",
               "settle", "smoke", "stop")
# 'filled' is the outcome of a single-order strategy (late_favorite); the pair strategy uses both_filled / one_leg_*.
OUTCOMES = ("paper_only", "both_filled", "one_leg_unwound", "one_leg_held", "none_filled", "blocked_by_cap",
            "unknown", "filled")
# bot_heartbeat.detail_json always carries these keys (None when the bot does not know the value yet).
STANDARD_DETAIL = ("strategy", "mode", "status", "market_ticker", "window_end_ts", "loss_used_usd", "max_loss_usd",
                   "balance_usd", "kill_switch", "last_error")
ORDER_SIDES = ("yes", "no")          # the outcome traded
ORDER_ACTIONS = ("buy", "sell")

QUOTE_COLS = ("ts", "hour_ticker", "strike_ticker", "kind", "leg_a_ask", "leg_a_size", "leg_b_ask", "leg_b_size",
              "cost", "fees", "edge")
OPPORTUNITY_UPDATABLE = ("live", "outcome", "live_cost", "live_fees", "latency_ms", "payout", "realized_pnl",
                         "paper_realized_pnl")
ORDER_UPDATABLE = ("opportunity_id", "ts_sent", "ts_ack", "status", "order_id", "filled", "avg_fill_price", "fees",
                   "response")

_SECRET_KEY = re.compile(r"secret|token|passw|private|api_?key|key_?id|key_?path|pem|signature|authori[sz]ation"
                         r"|cookie|header", re.I)
_PEM = re.compile(r"-----BEGIN [A-Z ]*KEY-----")
REDACTED = "[redacted]"


def new_run_id(strategy: str = "pair_h2", now: float | None = None) -> str:
    """'pair_h2-20261005T180000Z-1a2b3c': sortable, unique, and free of anything secret."""
    stamp = datetime.fromtimestamp(time.time() if now is None else now, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{strategy}-{stamp}-{uuid.uuid4().hex[:6]}"


def _real(v: Any, name: str) -> float | None:
    """Value for a REAL column: Decimal, int, float or a decimal string. None stays None; NaN and inf are refused."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise TypeError(f"{name}: expected a number, got bool")
    if isinstance(v, str):
        v = Decimal(v.strip())
    if isinstance(v, (Decimal, int, float)):
        f = float(v)
        if not math.isfinite(f):
            raise ValueError(f"{name}: {v!r} is not finite")
        return f
    raise TypeError(f"{name}: expected a number, got {type(v).__name__}")


def clean_json(v: Any, decimals: str = "str") -> Any:
    """JSON-safe copy: secret-looking keys dropped, Decimal -> str (exact) or float, NaN/inf -> None."""
    if isinstance(v, Mapping):
        return {str(k): clean_json(x, decimals) for k, x in v.items() if not _SECRET_KEY.search(str(k))}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [clean_json(x, decimals) for x in v]
    if isinstance(v, Decimal):
        if not v.is_finite():
            return None
        return float(v) if decimals == "float" else str(v)
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, str):
        return REDACTED if _PEM.search(v) else v
    if v is None or isinstance(v, (bool, int)):
        return v
    if isinstance(v, (bytes, bytearray, memoryview)):
        return None
    return clean_json(str(v), decimals)


def _dumps(v: Any, decimals: str = "str") -> str | None:
    if v is None:
        return None
    return json.dumps(clean_json(v, decimals), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _text(v: Any, name: str, *, required: bool = True, max_len: int = 4000) -> str | None:
    if v is None:
        if required:
            raise ValueError(f"{name} is required")
        return None
    s = str(v)
    if required and not s.strip():
        raise ValueError(f"{name} is required")
    return s[:max_len]


class BotState:
    """Writer for the dashboard's bot tables, for one run (run_id). Owns no connection lifecycle."""

    def __init__(self, conn: sqlite3.Connection, run_id: str, clock: Callable[[], float] = time.time) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        self.conn = conn
        self.run_id = run_id
        self.clock = clock
        self._strategy: str | None = None
        self._mode: str | None = None
        self._ensure_tables()

    # ------------------------------------------------------------------------------------------------ internals
    def _ensure_tables(self) -> None:
        have = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not set(TABLES) <= have:
            from kpm.core import db       # schema.sql is the single definition of these tables
            db.init_schema(self.conn)

    def _ts(self, ts: float | None) -> float:
        return float(self.clock() if ts is None else ts)

    def _write(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        owned = not self.conn.in_transaction       # never roll back work another writer left open on this connection
        try:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur
        except BaseException:
            if owned and self.conn.in_transaction:
                self.conn.rollback()
            raise

    # ------------------------------------------------------------------------------------------------ bot_runs
    def start(self, strategy: str, mode: str, config: Mapping | None = None, ts: float | None = None) -> None:
        """Insert this run's bot_runs row. config_json holds caps and settings in force, never secrets."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        name = _text(strategy, "strategy", max_len=64)
        self._write("INSERT INTO bot_runs (run_id, strategy, mode, started_ts, ended_ts, end_reason, config_json) "
                    "VALUES (?, ?, ?, ?, NULL, NULL, ?)",
                    (self.run_id, name, mode, self._ts(ts), _dumps(config or {})))
        self._strategy, self._mode = name, mode

    def end(self, reason: str, ts: float | None = None) -> None:
        """Mark the run ended. A second call keeps the first end time and reason."""
        self._write("UPDATE bot_runs SET ended_ts = ?, end_reason = ? WHERE run_id = ? AND ended_ts IS NULL",
                    (self._ts(ts), _text(reason, "reason", max_len=500), self.run_id))

    # ------------------------------------------------------------------------------------------------ heartbeat
    def _run_identity(self) -> tuple[str | None, str | None]:
        """(strategy, mode) of this run, from start() or, for a BotState that did not start it, from bot_runs."""
        if self._strategy is None:
            row = self.conn.execute("SELECT strategy, mode FROM bot_runs WHERE run_id = ?", (self.run_id,)).fetchone()
            if row is not None:
                self._strategy, self._mode = row[0], row[1]
        return self._strategy, self._mode

    def standard_detail(self, status: str, detail: Mapping | None = None) -> dict:
        """The heartbeat detail with every STANDARD_DETAIL key present (None when unknown) and the caller's extras.

        The status argument is authoritative. A bot that only knows the pair strategy's ``hour_ticker`` still gets a
        ``market_ticker`` (and the reverse), so the dashboard's tile and any reader of the contract keys both work.
        """
        d = dict(detail or {})
        strategy, mode = self._run_identity()
        d["status"] = status
        d.setdefault("strategy", strategy)
        d.setdefault("mode", mode)
        if d.get("market_ticker") in (None, ""):
            d["market_ticker"] = d.get("hour_ticker")
        if d.get("hour_ticker") in (None, "") and d.get("market_ticker"):
            d["hour_ticker"] = d["market_ticker"]
        for k in STANDARD_DETAIL:
            d.setdefault(k, None)
        return d

    def heartbeat(self, status: str, detail: Mapping | None = None, ts: float | None = None) -> None:
        """Overwrite this run's single heartbeat row. Decimals in detail become numbers for the page.

        detail_json always has the STANDARD_DETAIL keys (see :meth:`standard_detail`); extra keys are kept."""
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
        self._write("INSERT INTO bot_heartbeat (run_id, ts, status, detail_json) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT (run_id) DO UPDATE SET ts = excluded.ts, status = excluded.status, "
                    "detail_json = excluded.detail_json",
                    (self.run_id, self._ts(ts), status, _dumps(self.standard_detail(status, detail), decimals="float")))

    # ------------------------------------------------------------------------------------------------ events
    def event(self, level: str, kind: str, message: str, data: Any = None, ts: float | None = None) -> int:
        level = "warn" if level == "warning" else level
        if level not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}, got {level!r}")
        if kind not in EVENT_KINDS:
            raise ValueError(f"kind must be one of {EVENT_KINDS}, got {kind!r}")
        cur = self._write("INSERT INTO bot_events (run_id, ts, level, kind, message, data_json) VALUES (?, ?, ?, ?, ?, ?)",
                          (self.run_id, self._ts(ts), level, kind, _text(message, "message"), _dumps(data)))
        return int(cur.lastrowid)

    # ------------------------------------------------------------------------------------------------ quotes
    def quotes(self, rows: Iterable[Mapping]) -> int:
        """Per-poll pair pricing (one row per strike), written in one transaction. Same (ts, strike) replaces."""
        params = []
        for r in rows:
            missing = [c for c in QUOTE_COLS if c not in r]
            if missing:
                raise ValueError(f"quote row is missing {missing}")
            params.append((self.run_id, _real(r["ts"], "ts"), _text(r["hour_ticker"], "hour_ticker"),
                           _text(r["strike_ticker"], "strike_ticker"), _text(r["kind"], "kind"),
                           *(_real(r[c], c) for c in QUOTE_COLS[4:])))
        if not params:
            return 0
        owned = not self.conn.in_transaction
        try:
            self.conn.executemany(f"INSERT OR REPLACE INTO bot_quotes (run_id, {', '.join(QUOTE_COLS)}) "
                                  f"VALUES ({', '.join('?' * (len(QUOTE_COLS) + 1))})", params)
            self.conn.commit()
        except BaseException:
            if owned and self.conn.in_transaction:
                self.conn.rollback()
            raise
        return len(params)

    def quote(self, **row: Any) -> None:
        self.quotes([row])

    # ------------------------------------------------------------------------------------------------ opportunities
    def opportunity(self, *, detected_ts: float, hour_ticker: str, strike_ticker: str, kind: str, contracts: Any,
                    paper_cost: Any, paper_fees: Any, paper_edge: Any, live: bool = False,
                    outcome: str | None = None) -> int:
        """Insert one opportunity (the paper twin at detection prices). Returns its id."""
        if outcome is not None and outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")
        cur = self._write(
            "INSERT INTO bot_opportunities (run_id, detected_ts, hour_ticker, strike_ticker, kind, contracts, "
            "paper_cost, paper_fees, paper_edge, live, outcome) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, _real(detected_ts, "detected_ts"), _text(hour_ticker, "hour_ticker"),
             _text(strike_ticker, "strike_ticker"), _text(kind, "kind"), _real(contracts, "contracts"),
             _real(paper_cost, "paper_cost"), _real(paper_fees, "paper_fees"), _real(paper_edge, "paper_edge"),
             1 if live else 0, outcome))
        return int(cur.lastrowid)

    def update_opportunity(self, opportunity_id: int, *, run_id: str | None = None, **fields: Any) -> None:
        """Update columns of one opportunity. ``run_id`` (default: this run) lets a new run settle a row an earlier
        run left pending (its paper twin and realized P&L are only known after the market settles)."""
        bad = sorted(set(fields) - set(OPPORTUNITY_UPDATABLE))
        if bad:
            raise ValueError(f"cannot update bot_opportunities columns {bad}")
        if not fields:
            return
        vals = []
        for k, v in fields.items():
            if k == "live":
                vals.append(1 if v else 0)
            elif k == "outcome":
                if v is not None and v not in OUTCOMES:
                    raise ValueError(f"outcome must be one of {OUTCOMES}, got {v!r}")
                vals.append(v)
            else:
                vals.append(_real(v, k))
        self._write(f"UPDATE bot_opportunities SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ? AND run_id = ?",
                    (*vals, int(opportunity_id), self.run_id if run_id is None else run_id))

    def rescale_opportunity(self, opportunity_id: int, *, contracts: Any, paper_cost: Any, paper_fees: Any,
                            run_id: str | None = None) -> None:
        """Scale a row's paper twin to the contracts that actually filled (a live partial fill).

        ``update_opportunity`` deliberately cannot touch the paper twin, which keeps the prices of the detection. A
        partial fill is the one case that changes it, and only its SIZE: the same detection price on fewer contracts,
        so ``contracts``, ``paper_cost`` and ``paper_fees`` describe the trade that was really made and the row's live
        and paper figures (and the dashboard's paired comparison and per-contract prices) are about the same trade."""
        n = _real(contracts, "contracts")
        if n is None or n <= 0:
            raise ValueError("contracts must be > 0")
        self._write("UPDATE bot_opportunities SET contracts = ?, paper_cost = ?, paper_fees = ? "
                    "WHERE id = ? AND run_id = ?",
                    (n, _real(paper_cost, "paper_cost"), _real(paper_fees, "paper_fees"), int(opportunity_id),
                     self.run_id if run_id is None else run_id))

    # ------------------------------------------------------------------------------------------------ orders
    def order(self, *, client_order_id: str, ticker: str, side: str, action: str, count: Any, limit_price: Any,
              tif: str | None = None, status: str | None = None, opportunity_id: int | None = None,
              ts_sent: float | None = None, ts_ack: float | None = None, order_id: str | None = None,
              filled: Any = None, avg_fill_price: Any = None, fees: Any = None, dry_run: bool = True,
              response: Any = None) -> None:
        """Insert or replace one bot_orders row. side is the outcome (yes/no) and limit_price its price in dollars;
        response_json holds the request body and the exchange response, never request headers."""
        if side not in ORDER_SIDES:
            raise ValueError(f"side must be one of {ORDER_SIDES} (the outcome), got {side!r}")
        if action not in ORDER_ACTIONS:
            raise ValueError(f"action must be one of {ORDER_ACTIONS}, got {action!r}")
        self._write(
            "INSERT INTO bot_orders (client_order_id, run_id, opportunity_id, ts_sent, ts_ack, ticker, side, action, "
            "count, limit_price, tif, status, order_id, filled, avg_fill_price, fees, dry_run, response_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (client_order_id) DO UPDATE SET opportunity_id = excluded.opportunity_id, "
            "ts_sent = excluded.ts_sent, ts_ack = excluded.ts_ack, ticker = excluded.ticker, side = excluded.side, "
            "action = excluded.action, count = excluded.count, limit_price = excluded.limit_price, tif = excluded.tif, "
            "status = excluded.status, order_id = excluded.order_id, filled = excluded.filled, "
            "avg_fill_price = excluded.avg_fill_price, fees = excluded.fees, dry_run = excluded.dry_run, "
            "response_json = excluded.response_json",
            (_text(client_order_id, "client_order_id", max_len=128), self.run_id, opportunity_id,
             _real(ts_sent, "ts_sent"), _real(ts_ack, "ts_ack"), _text(ticker, "ticker"), side, action,
             _real(count, "count"), _real(limit_price, "limit_price"), _text(tif, "tif", required=False),
             _text(status, "status", required=False), _text(order_id, "order_id", required=False),
             _real(filled, "filled"), _real(avg_fill_price, "avg_fill_price"), _real(fees, "fees"),
             1 if dry_run else 0, _dumps(response)))

    def update_order(self, client_order_id: str, **fields: Any) -> None:
        bad = sorted(set(fields) - set(ORDER_UPDATABLE))
        if bad:
            raise ValueError(f"cannot update bot_orders columns {bad}")
        if not fields:
            return
        cols, vals = [], []
        for k, v in fields.items():
            if k == "response":
                cols.append("response_json")
                vals.append(_dumps(v))
            elif k in ("status", "order_id"):
                cols.append(k)
                vals.append(_text(v, k, required=False))
            elif k == "opportunity_id":
                cols.append(k)
                vals.append(None if v is None else int(v))
            else:
                cols.append(k)
                vals.append(_real(v, k))
        self._write(f"UPDATE bot_orders SET {', '.join(f'{c} = ?' for c in cols)} WHERE client_order_id = ?",
                    (*vals, client_order_id))

    # ------------------------------------------------------------------------------------------------ reads
    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = self.conn.execute(sql, params)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def run_row(self) -> dict | None:
        rows = self._rows("SELECT * FROM bot_runs WHERE run_id = ?", (self.run_id,))
        return rows[0] if rows else None

    def heartbeat_row(self) -> dict | None:
        """The heartbeat row with detail_json parsed into ``detail``."""
        rows = self._rows("SELECT * FROM bot_heartbeat WHERE run_id = ?", (self.run_id,))
        if not rows:
            return None
        row = rows[0]
        row["detail"] = json.loads(row.pop("detail_json") or "{}")
        return row

    def opportunity_row(self, opportunity_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM bot_opportunities WHERE id = ? AND run_id = ?", (int(opportunity_id), self.run_id))
        return rows[0] if rows else None

    def opportunity_rows(self) -> list[dict]:
        return self._rows("SELECT * FROM bot_opportunities WHERE run_id = ? ORDER BY id", (self.run_id,))

    def unsettled_opportunities(self, strategy: str, *, detected_between: tuple[float, float]) -> list[dict]:
        """Opportunities of ``strategy`` from OTHER runs whose paper P&L is still empty (the run ended, or was stopped,
        before the market settled), detected within ``detected_between`` (oldest, newest). Oldest first."""
        lo, hi = detected_between
        return self._rows(
            "SELECT o.* FROM bot_opportunities o JOIN bot_runs r ON r.run_id = o.run_id "
            "WHERE r.strategy = ? AND o.run_id != ? AND o.paper_realized_pnl IS NULL "
            "AND o.detected_ts >= ? AND o.detected_ts <= ? ORDER BY o.detected_ts, o.id",
            (strategy, self.run_id, float(lo), float(hi)))

    def live_orders_of(self, run_id: str, opportunity_id: int) -> list[dict]:
        """The real (not dry-run) bot_orders rows of one opportunity."""
        return self._rows("SELECT * FROM bot_orders WHERE run_id = ? AND opportunity_id = ? AND dry_run = 0 "
                          "ORDER BY ts_sent, rowid", (run_id, int(opportunity_id)))

    def order_rows(self) -> list[dict]:
        return self._rows("SELECT * FROM bot_orders WHERE run_id = ? ORDER BY rowid", (self.run_id,))

    def event_rows(self) -> list[dict]:
        return self._rows("SELECT * FROM bot_events WHERE run_id = ? ORDER BY id", (self.run_id,))
