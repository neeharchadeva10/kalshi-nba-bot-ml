"""Execution ledger: the local record of every request, order intent, fill and settlement
(docs/execution_design.md 5.4-5.7).

Tables (appended to src/kpm/core/schema.sql): exec_requests, exec_orders, exec_fills, exec_settlements. Prices, counts
and money are TEXT decimal strings; times are epoch seconds. Every row carries ``env`` and every read is scoped to the
ledger's environment, so demo activity never counts against production caps.

What the ledger guarantees:

* **Intent before send.** :meth:`Ledger.new_intent` inserts and COMMITS the full request body (status ``intent``)
  before the caller sends it, so a crash right after the send still leaves a row that reconciliation matches by
  ``client_order_id``. A client_order_id is never reused for a new intent.
* **Unknown outcomes stay unknown.** :meth:`Ledger.mark_result` records ``unknown`` for a timeout, a 5xx or a response
  that does not describe the request. Only reconciliation (``orders(ticker=...)`` matched by client_order_id) resolves
  it. Nothing in this module resends anything.
* **Audit without secrets.** :meth:`Ledger.audit_request` (the ``audit=`` callable of ExecClient) builds each row
  from an explicit field list, drops header/credential-looking keys at any depth, replaces PEM text and any value
  passed in ``redact=``, cuts responses at 64 KB, mirrors the row to logs/exec.jsonl and never raises into the
  order path.
* **Money.** Settlement ``revenue``/``value`` are integer cents (``units.from_cents``). Realized P&L counts each
  fill's ``fee_cost`` once and never adds the settlement's ``fee_cost`` (the same fees, summed) on top.
* **Loss and exposure for the safety layer.** :meth:`realized_loss_total` / :meth:`realized_loss_24h` are the net
  realized loss (>= 0) over settled tickers; :meth:`open_worst_case` is what can still be lost: unsettled positions
  net of $1 per YES+NO pair, fills an order reported but that are not fetched yet, and every order whose outcome is
  not final (counted as if it filled completely at its limit, plus the worst-case fee).
* **Only our own fills.** :meth:`upsert_fills` stores a fill only if it belongs to an order in this ledger (matched by
  order_id, or client_order_id for WebSocket fills). A manual trade in the app is "foreign": it never reaches P&L,
  positions or the loss cap (:meth:`split_fills` separates them for reconcile).
* **Halts.** :meth:`halt` records a real inconsistency in audit_log and calls the registered listeners; Safety
  registers ``Safety.trip``, so reconcile can stop the session without holding a Safety object.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse

from kpm.execution import units

log = logging.getLogger(__name__)

D0, D1 = Decimal("0"), Decimal("1")
CENT = Decimal("0.01")
DAY_S = 86400.0
COMPONENT = "exec.ledger"
EXEC_TABLES = ("exec_requests", "exec_orders", "exec_fills", "exec_settlements")
RESPONSE_MAX_CHARS = 65536          # exec_requests.response
JSONL_RESPONSE_CHARS = 4000         # logs/exec.jsonl keeps a shorter copy
TEXT_MAX_CHARS = 4000

# Order statuses. Exchange statuses are resting | canceled | executed; the rest are local.
FINAL_STATUSES = frozenset({"executed", "canceled", "rejected", "throttled", "presumed_not_placed", "auth_error"})
UNRESOLVED_STATUSES = frozenset({"intent", "unknown", "duplicate"})      # outcome not known: reconcile, never resend
OPEN_STATUSES = UNRESOLVED_STATUSES | {"acked", "resting"}
NOT_PLACED_STATUSES = frozenset({"rejected", "throttled", "presumed_not_placed", "auth_error"})
RESULT_STATUS = {"rejected": "rejected", "throttled": "throttled", "auth": "auth_error",
                 "duplicate": "duplicate", "unknown": "unknown"}
EXCHANGE_STATUS = {"resting": "resting", "canceled": "canceled", "executed": "executed"}
IMMEDIATE_TIFS = frozenset({"immediate_or_cancel", "fill_or_kill"})
TIME_IN_FORCE = ("fill_or_kill", "good_till_canceled", "immediate_or_cancel")
DEFAULT_STP = "taker_at_cross"

# Request-body fields of CreateOrderV2 the ledger stores and returns (anything else in a spec is metadata).
BODY_KEYS = ("ticker", "client_order_id", "side", "count", "price", "time_in_force", "self_trade_prevention_type",
             "exchange_index", "expiration_time", "post_only", "cancel_order_on_pause", "reduce_only",
             "order_group_id", "subaccount")

_SECRET_KEY = re.compile(r"secret|token|passw|passphrase|(?:^|_)pwd(?:$|_)|private|api_?key|key_?id|key_?path|pem"
                         r"|signature|authori[sz]ation|cookie|header|access", re.I)
_PEM_BLOCK = re.compile(r"-----BEGIN [A-Z0-9 ]*-----.*?(?:-----END [A-Z0-9 ]*-----|$)", re.S)
_CLIENT_ORDER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
_INT_TEXT = re.compile(r"-?\d+")
REDACTED = "[redacted]"


# ============================================================================================ helpers
def D(x: Any) -> Decimal:
    """Exact Decimal from an API decimal string, an int or a Decimal. Floats, bools and None are refused."""
    if isinstance(x, bool) or x is None:
        raise TypeError(f"expected a decimal string, got {x!r}")
    if isinstance(x, int):
        return Decimal(x)
    if isinstance(x, str):
        return units.dec(x.strip())
    return units.dec(x)                     # Decimal passes through (finite only); float raises TypeError


def _d0(x: Any) -> Decimal:
    return D0 if x is None or x == "" else D(x)


def plain(d: Decimal) -> str:
    """Decimal as a plain string (never '1E-7'): what the TEXT columns store and units.dec() reads back."""
    return format(d, "f")


def _dec_text(x: Any) -> str | None:
    return None if x is None or x == "" else plain(D(x))


def _cents(v: Any) -> int | None:
    """Integer cents as the API sends them (Settlement revenue/value). Floats are refused."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise TypeError("expected integer cents, got bool")
    if isinstance(v, int):
        return v
    if isinstance(v, str) and _INT_TEXT.fullmatch(v.strip()):
        return int(v.strip())
    raise TypeError(f"expected integer cents, got {v!r}")


def _int_or_none(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and _INT_TEXT.fullmatch(v.strip()):
        return int(v.strip())
    return None


def _num_or_none(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ts(s: Any) -> float | None:
    return units.parse_rfc3339(s) if isinstance(s, str) and s.strip() else None


def _short(v: Any, n: int = 200) -> str | None:
    return None if v is None else str(v)[:n]


def scrub(v: Any) -> Any:
    """JSON-safe copy: credential-looking keys (headers, key ids, signatures, passwords, PEMs...) are replaced at any
    depth, PEM blocks inside strings are replaced, Decimals become exact strings and bytes are dropped."""
    if isinstance(v, Mapping):
        return {str(k): (REDACTED if _SECRET_KEY.search(str(k)) else scrub(x)) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [scrub(x) for x in v]
    if isinstance(v, Decimal):
        return plain(v) if v.is_finite() else None
    if isinstance(v, (bytes, bytearray, memoryview)):
        return None
    if isinstance(v, str):
        return _PEM_BLOCK.sub(REDACTED, v)
    if v is None or isinstance(v, (bool, int, float)):
        return v
    return _PEM_BLOCK.sub(REDACTED, str(v))


def _clean_path(p: Any) -> str:
    """The signed path only: no scheme/host, no query string."""
    s = str(p or "")
    if "://" in s:
        s = urlparse(s).path
    return s.split("?", 1)[0][:500]


def _body_coids(body: Any) -> str | None:
    if not isinstance(body, Mapping):
        return None
    if body.get("client_order_id"):
        return str(body["client_order_id"])[:128]
    orders = body.get("orders")
    if isinstance(orders, list):
        ids = [str(o["client_order_id"]) for o in orders if isinstance(o, Mapping) and o.get("client_order_id")]
        return ",".join(ids)[:2000] or None
    return None


def _err_text(error: Any, status: Any, outcome: Any) -> str:
    if isinstance(error, Mapping):
        parts = [str(error.get(k)) for k in ("code", "message", "details") if error.get(k)]
        msg = " | ".join(parts)
    elif isinstance(error, BaseException):
        msg = type(error).__name__
    else:
        msg = "" if error is None else str(error)
    return f"{outcome or 'error'} (HTTP {status}) {msg}".strip()[:TEXT_MAX_CHARS]


def build_body(spec: Mapping[str, Any], client_order_id: str) -> dict:
    """The exact CreateOrderV2 body for an order spec.

    ``spec`` holds the request fields (or a ``body`` dict of them) plus optional metadata (``outcome``,
    ``outcome_price``, ``purpose``, ``group_id``). When ``outcome``/``outcome_price`` are given, ``side``/``price``
    must equal ``units.to_book(outcome, outcome_price)`` (they are filled in from it when absent): a wrong-side order
    can never be stored. price/count are formatted by units.fmt_price/fmt_count.
    """
    src = spec["body"] if isinstance(spec.get("body"), Mapping) else spec
    body = {k: src[k] for k in BODY_KEYS if src.get(k) is not None}
    outcome = spec.get("outcome", src.get("outcome"))
    oprice = spec.get("outcome_price", src.get("outcome_price"))
    if outcome is not None or oprice is not None:
        if outcome is None or oprice is None:
            raise ValueError("outcome and outcome_price must be given together")
        side, yes_price = units.to_book(str(outcome), D(oprice))
        # every YES-price field the spec carries ('price' in the body, 'yes_price' in the strategies' specs) must agree
        given = [x for x in (body.get("price"), spec.get("yes_price"), src.get("yes_price")) if x is not None]
        if body.get("side", side) != side or any(D(x) != yes_price for x in given):
            raise ValueError(f"side/price {body.get('side')}/{given} do not match "
                             f"units.to_book({outcome}, {oprice}) = {side}/{yes_price}")
        body["side"], body["price"] = side, yes_price
    missing = [k for k in ("ticker", "side", "price", "count", "time_in_force", "exchange_index") if k not in body]
    if missing:
        raise ValueError(f"order spec is missing {missing}")
    if body["side"] not in ("bid", "ask"):
        raise ValueError(f"side must be 'bid' or 'ask' (YES book side), got {body['side']!r}")
    if body["time_in_force"] not in TIME_IN_FORCE:
        raise ValueError(f"time_in_force must be one of {TIME_IN_FORCE}")
    ei = body["exchange_index"]
    if isinstance(ei, bool) or not isinstance(ei, int) or ei < 0:
        raise ValueError(f"exchange_index must be the market's shard (int >= 0), got {ei!r}")
    body["price"] = units.fmt_price(D(body["price"]))
    body["count"] = units.fmt_count(D(body["count"]))
    body.setdefault("self_trade_prevention_type", DEFAULT_STP)
    for flag in ("post_only", "cancel_order_on_pause", "reduce_only"):
        if flag in body and not isinstance(body[flag], bool):
            raise ValueError(f"{flag} must be a bool")
    if body.get("reduce_only") and body["time_in_force"] != "immediate_or_cancel":
        raise ValueError("reduce_only is only accepted with immediate_or_cancel")
    if not _CLIENT_ORDER_ID.fullmatch(client_order_id):
        raise ValueError(f"client_order_id {client_order_id[:80]!r} is not a short id such as str(uuid4())")
    body["client_order_id"] = client_order_id
    return body


@dataclass
class TickerAgg:
    """Fills on one ticker: contracts bought per outcome, money paid and fees (all from fills, fees counted once)."""

    yes: Decimal = D0
    no: Decimal = D0
    cost: Decimal = D0
    fees: Decimal = D0
    n: int = 0

    def add(self, outcome_side: str, count: Decimal, yes_price: Decimal, no_price: Decimal, fee: Decimal) -> None:
        if outcome_side == "yes":
            self.yes += count
            self.cost += count * yes_price
        elif outcome_side == "no":
            self.no += count
            self.cost += count * no_price
        else:
            raise ValueError(f"fill outcome_side must be yes|no, got {outcome_side!r}")
        self.fees += fee
        self.n += 1

    @property
    def net(self) -> Decimal:
        """Signed position: + YES contracts, - NO contracts (Kalshi nets a YES+NO pair into $1 of cash)."""
        return self.yes - self.no

    @property
    def netted(self) -> Decimal:
        """YES+NO pairs: each is worth exactly $1 whatever the result."""
        return min(self.yes, self.no)

    def payoff(self, result: str | None, value_cents: int | None = None) -> Decimal:
        if result == "yes":
            return self.yes
        if result == "no":
            return self.no
        if value_cents is not None:                     # scalar: value = payout of one YES contract
            v = units.from_cents(int(value_cents))
            return self.yes * v + self.no * (D1 - v)
        return self.netted                              # unknown result: only the netted pairs are certain


# ============================================================================================ ledger
class Ledger:
    """Ledger for one run (``run_id``) in one environment (``env_name``: demo | prod). Owns no connection lifecycle.

    ``clock`` gives epoch seconds; ``log_path`` overrides logs/exec.jsonl; ``redact`` lists values (key id, key path)
    that must never appear in a stored row even if a caller passes them by mistake.
    """

    def __init__(self, conn: sqlite3.Connection, run_id: str, env_name: str, *,
                 clock: Callable[[], float] = time.time, log_path: str | Path | None = None,
                 redact: Iterable[str] = ()) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        if not isinstance(env_name, str) or not env_name.strip():
            raise ValueError("env_name must be a non-empty string")
        self.conn = conn
        self.run_id = run_id
        self.env = env_name
        self.clock = clock
        self.live = False                  # the caller sets this once live sending is enabled; stamps audit rows
        self.audit_errors = 0
        self._log_path = Path(log_path) if log_path is not None else None
        self._log_disabled = False
        vals: set[str] = set()
        for v in redact:
            if v and len(str(v)) >= 4:
                vals.add(str(v))
                vals.add(json.dumps(str(v))[1:-1])          # the JSON-escaped form (Windows paths)
        self._redact_values = tuple(sorted(vals, key=len, reverse=True))
        self._lockout = 0.0
        self._halt_listeners: list[Callable[[str], Any]] = []
        self.last_foreign: list[dict] = []          # fills the last upsert_fills() skipped because no ledger order owns them
        self.settle_attempts: dict[str, float] = {}  # ticker -> when reconcile last tried to settle it (rate limit)
        self._ensure_tables()

    @property
    def env_name(self) -> str:
        return self.env

    # ------------------------------------------------------------------------------------------ internals
    def _ensure_tables(self) -> None:
        have = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not set(EXEC_TABLES) | {"audit_log"} <= have:
            from kpm.core import db              # schema.sql is the single definition of these tables
            db.init_schema(self.conn)

    def _rows(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        cur = self.conn.execute(sql, tuple(params))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def _write(self, fn: Callable[[], Any]) -> Any:
        """Run statements and commit at once (crash safety); roll back only a transaction this call opened."""
        owned = not self.conn.in_transaction
        try:
            out = fn()
            self.conn.commit()
            return out
        except BaseException:
            if owned and self.conn.in_transaction:
                self.conn.rollback()
            raise

    def _insert(self, table: str, row: Mapping[str, Any]) -> sqlite3.Cursor:
        cols = list(row)
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
        return self._write(lambda: self.conn.execute(sql, [row[c] for c in cols]))

    def _update_order(self, client_order_id: str, fields: Mapping[str, Any]) -> None:
        cols = list(fields)
        sql = f"UPDATE exec_orders SET {', '.join(f'{c} = ?' for c in cols)} WHERE client_order_id = ? AND env = ?"
        self._write(lambda: self.conn.execute(sql, [fields[c] for c in cols] + [client_order_id, self.env]))

    def _redact_text(self, s: Any) -> str | None:
        if s is None:
            return None
        out = _PEM_BLOCK.sub(REDACTED, str(s))
        for v in self._redact_values:
            out = out.replace(v, REDACTED)
        return out

    def _json(self, v: Any) -> str | None:
        if v is None:
            return None
        return self._redact_text(json.dumps(scrub(v), sort_keys=True, separators=(",", ":"), default=str))

    def _jsonl_path(self) -> Path | None:
        if self._log_disabled:
            return None
        if self._log_path is None:
            try:
                from kpm.core import config
                self._log_path = config.path("logs_dir") / "exec.jsonl"
            except Exception:                                      # no settings: keep the DB audit only
                self._log_disabled = True
                log.warning("logs_dir is not configured; the exec.jsonl mirror is disabled")
                return None
        return self._log_path

    def _jsonl(self, record: Mapping[str, Any]) -> None:
        path = self._jsonl_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            line = self._redact_text(json.dumps(scrub(record), sort_keys=True, default=str))
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            self.audit_errors += 1
            log.exception("could not append to %s", path)

    # ------------------------------------------------------------------------------------------ audit
    def audit_request(self, **fields: Any) -> None:
        """One exec_requests row per HTTP attempt (pass as ``ExecClient(audit=ledger.audit_request)``).

        Known fields: env, live, ts_ms, method, path, kind, attempt, params, body, client_order_id, status, outcome,
        response, latency_ms, error. Credential-looking keys (headers, key ids, signatures...) are dropped, anything
        else goes to extra_json after scrubbing. Never raises: an audit failure must not change a write's outcome.
        """
        try:
            self._audit_request(dict(fields))
        except Exception:
            self.audit_errors += 1
            log.exception("could not write an exec_requests audit row")

    def _audit_request(self, f: dict) -> None:
        dropped = [k for k in list(f) if _SECRET_KEY.search(str(k))]
        for k in dropped:
            f.pop(k)                                     # never stored, not even redacted
        body, params = f.pop("body", None), f.pop("params", None)
        coid = f.pop("client_order_id", None) or _body_coids(body)
        response = f.pop("response", None)
        if response is not None and not isinstance(response, str):
            response = json.dumps(scrub(response), default=str)
        response = self._redact_text(response)
        error = f.pop("error", None)
        if isinstance(error, BaseException):
            error = type(error).__name__
        elif isinstance(error, (Mapping, list)):
            error = json.dumps(scrub(error), default=str)
        live = bool(f.pop("live", self.live))
        row = {
            "run_id": self.run_id,
            "env": str(f.pop("env", None) or self.env)[:20],
            "live": 1 if live else 0,
            "ts": float(self.clock()),
            "ts_ms": _int_or_none(f.pop("ts_ms", None)),
            "method": str(f.pop("method", "") or "").upper()[:10],
            "path": _clean_path(f.pop("path", "")),
            "kind": _short(f.pop("kind", None), 20),
            "attempt": _int_or_none(f.pop("attempt", None)),
            "params_json": self._json(params),
            "body_json": self._json(body),
            "client_order_id": _short(coid, 2000),
            "status": _int_or_none(f.pop("status", None)),
            "outcome": _short(f.pop("outcome", None), 20),
            "response": None if response is None else response[:RESPONSE_MAX_CHARS],
            "latency_ms": _num_or_none(f.pop("latency_ms", None)),
            "error": None if error is None else self._redact_text(error)[:TEXT_MAX_CHARS],
            "extra_json": None,
        }
        if dropped:
            f["dropped_fields"] = len(dropped)               # (a name without 'secret': scrub() would redact the count)
        if f:
            row["extra_json"] = self._json(f)
        self._insert("exec_requests", row)
        rec = {k: v for k, v in row.items() if k not in ("params_json", "body_json", "extra_json", "response")}
        rec.update(component=COMPONENT, action="request",
                   params=json.loads(row["params_json"]) if row["params_json"] else None,
                   body=json.loads(row["body_json"]) if row["body_json"] else None,
                   response=None if response is None else response[:JSONL_RESPONSE_CHARS])
        self._jsonl(rec)

    def log_event(self, action: str, details: Mapping[str, Any] | None = None, *, component: str = COMPONENT,
                  entity_type: str | None = None, entity_id: Any = None) -> None:
        """audit_log row (same table as kpm.core.audit) mirrored to logs/exec.jsonl. Never raises."""
        try:
            payload = self._json(details or {}) or "{}"
            ts = int(self.clock())
            eid = None if entity_id is None else str(entity_id)
            self._write(lambda: self.conn.execute(
                "INSERT INTO audit_log (ts, component, entity_type, entity_id, action, details) VALUES (?,?,?,?,?,?)",
                (ts, component, entity_type, eid, action, payload)))
            self._jsonl({"ts": ts, "component": component, "run_id": self.run_id, "env": self.env, "action": action,
                         "entity_type": entity_type, "entity_id": eid, "details": json.loads(payload)})
        except Exception:
            self.audit_errors += 1
            log.exception("could not write ledger event %s", action)

    # ------------------------------------------------------------------------------------------ intents and results
    def new_intent(self, spec: Mapping[str, Any]) -> str:
        """Insert and COMMIT an order intent before it is sent; return its client_order_id.

        The id is ``spec["client_order_id"]`` if given (it must be new) or a fresh uuid4. The stored body is
        :func:`build_body` of the spec; fetch it with :meth:`request_body` (a dict spec also gets its
        ``client_order_id`` set, and so does a ``spec["body"]`` dict). Metadata: ``purpose`` (entry | retry | exit),
        ``group_id`` / ``opportunity_id``.
        """
        coid = str(spec.get("client_order_id") or uuid.uuid4())
        if self._rows("SELECT 1 FROM exec_orders WHERE client_order_id = ?", (coid,)):
            raise ValueError(f"client_order_id {coid} is already in the ledger: a new order needs a new intent "
                             "(an unknown outcome is resolved by reconcile, never by resending)")
        body = build_body(spec, coid)
        if spec.get("outcome") is not None or (isinstance(spec.get("body"), Mapping)
                                               and spec["body"].get("outcome") is not None):
            src = spec if spec.get("outcome") is not None else spec["body"]
            outcome, outcome_price = str(src["outcome"]), D(src["outcome_price"])
        else:
            outcome, outcome_price = units.from_book(body["side"], D(body["price"]))
        now = float(self.clock())
        row = {
            "client_order_id": coid, "run_id": self.run_id, "env": self.env, "created_ts": now, "updated_ts": now,
            "ticker": body["ticker"], "exchange_index": body["exchange_index"], "side": body["side"],
            "price": body["price"], "count": body["count"], "outcome": outcome, "outcome_price": plain(outcome_price),
            "time_in_force": body["time_in_force"], "reduce_only": 1 if body.get("reduce_only") else 0,
            "purpose": _short(spec.get("purpose"), 40),
            "group_id": _short(spec.get("group_id", spec.get("opportunity_id")), 80),
            "status": "intent", "body_json": json.dumps(body, sort_keys=True, separators=(",", ":")),
        }
        self._insert("exec_orders", row)                 # committed: the send may happen now
        if isinstance(spec, dict):
            spec["client_order_id"] = coid
            if isinstance(spec.get("body"), dict):
                spec["body"]["client_order_id"] = coid
        self._jsonl({"ts": now, "component": COMPONENT, "action": "intent", "run_id": self.run_id, "env": self.env,
                     "client_order_id": coid, "body": body, "purpose": row["purpose"], "group_id": row["group_id"]})
        return coid

    def request_body(self, client_order_id: str) -> dict:
        """The exact body stored by :meth:`new_intent` (send this, unchanged)."""
        row = self.order(client_order_id)
        if row is None:
            raise KeyError(f"unknown client_order_id {client_order_id}")
        return json.loads(row["body_json"])

    def mark_sent(self, client_order_ids: Iterable[str], ts: float | None = None) -> None:
        """Optional: stamp the send time (reconcile measures its grace period from it; else from created_ts)."""
        t = float(self.clock() if ts is None else ts)
        ids = list(client_order_ids)
        self._write(lambda: self.conn.executemany(
            "UPDATE exec_orders SET sent_ts = ?, updated_ts = ? WHERE client_order_id = ? AND env = ? "
            "AND sent_ts IS NULL", [(t, t, c, self.env) for c in ids]))

    def mark_result(self, client_order_id: str, result: Any) -> str:
        """Record a write Result (outcome ok | rejected | duplicate | throttled | unknown | auth); return the status.

        ok: the response must echo this client_order_id (or none) and carry an order_id and parseable counts, else the
        status is ``unknown``. An immediate_or_cancel order is final at once: ``executed`` when fully filled,
        ``canceled`` otherwise (remaining_count is the final value). Other outcomes map to rejected | duplicate |
        throttled | unknown | auth_error. duplicate and unknown stay open until reconcile resolves them.
        """
        row = self.order(client_order_id)
        if row is None:
            raise KeyError(f"unknown client_order_id {client_order_id}")
        if row["status"] not in UNRESOLVED_STATUSES | {"acked"}:
            log.warning("mark_result for %s ignored: status is already %s", client_order_id, row["status"])
            return row["status"]
        outcome = getattr(result, "outcome", None)
        data = getattr(result, "data", None)
        data = data if isinstance(data, Mapping) else {}
        now = float(self.clock())
        upd: dict[str, Any] = {"updated_ts": now, "result_ts": now}
        if row["sent_ts"] is None:
            upd["sent_ts"] = now
        problem = None
        res_coid = getattr(result, "client_order_id", None)
        if res_coid not in (None, client_order_id):
            problem = f"result belongs to client_order_id {res_coid}"
        fill = remaining = D0
        if outcome == "ok" and problem is None:
            echoed, order_id = data.get("client_order_id"), data.get("order_id")
            if echoed not in (None, "", client_order_id):
                problem = f"response names client_order_id {echoed}"
            elif not isinstance(order_id, str) or not order_id:
                problem = "response has no order_id"
            else:
                try:
                    fill, remaining = D(data.get("fill_count")), D(data.get("remaining_count"))
                except (TypeError, ValueError):
                    problem = "response fill_count/remaining_count are not decimal strings"
        if problem is not None:
            status = "unknown"
            upd["error"] = f"unexpected response, reconcile: {problem}"[:TEXT_MAX_CHARS]
        elif outcome == "ok":
            count = D(row["count"])
            if row["time_in_force"] in IMMEDIATE_TIFS:
                status = ("executed" if fill >= count else "canceled") if remaining == 0 else "acked"
            else:
                status = "resting" if remaining > 0 else "executed"
            upd.update(order_id=data["order_id"], fill_count=plain(fill), remaining_count=plain(remaining),
                       avg_fill_price=_dec_text(data.get("average_fill_price")),
                       avg_fee_paid=_dec_text(data.get("average_fee_paid")), ts_ms=_int_or_none(data.get("ts_ms")),
                       error=None)
        else:
            status = RESULT_STATUS.get(str(outcome), "unknown")
            upd["error"] = _err_text(getattr(result, "error", None), getattr(result, "status", None), outcome)
        upd["status"] = status
        self._update_order(client_order_id, upd)
        self._jsonl({"ts": now, "component": COMPONENT, "action": "result", "run_id": self.run_id, "env": self.env,
                     "client_order_id": client_order_id, "outcome": outcome, "status": status,
                     "http_status": getattr(result, "status", None), "data": data, "error": upd.get("error")})
        return status

    def apply_exchange_order(self, order: Mapping[str, Any]) -> dict | None:
        """Update (or adopt) a ledger order from a GET /portfolio/orders Order object; None if it is not ours.

        Matched by client_order_id, else by order_id. The exchange's status, counts and fees win. Returns
        ``{client_order_id, previous, status, problems}``; problems (other order_id/ticker/side, or an order that was
        presumed not placed) are real mismatches for reconcile.
        """
        coid = order.get("client_order_id") or None
        oid = order.get("order_id") or None
        row = self.order(coid) if coid else None
        if row is None and oid:
            row = self.order_by_exchange_id(oid)
        if row is None:
            return None
        problems = []
        if row["order_id"] and oid and row["order_id"] != oid:
            problems.append(f"exchange order_id {oid} != ledger order_id {row['order_id']}")
        if order.get("ticker") and order["ticker"] != row["ticker"]:
            problems.append(f"exchange ticker {order['ticker']} != ledger ticker {row['ticker']}")
        if order.get("book_side") and order["book_side"] != row["side"]:
            problems.append(f"exchange book_side {order['book_side']} != ledger side {row['side']}")
        if row["status"] == "presumed_not_placed":
            problems.append("an order presumed not placed exists on the exchange")
        status = EXCHANGE_STATUS.get(str(order.get("status")), row["status"])
        upd: dict[str, Any] = {"updated_ts": float(self.clock()), "status": status}
        if not row["order_id"] and oid:
            upd["order_id"] = oid
        for col, key in (("fill_count", "fill_count_fp"), ("remaining_count", "remaining_count_fp"),
                         ("taker_fees", "taker_fees_dollars"), ("maker_fees", "maker_fees_dollars")):
            if order.get(key) not in (None, ""):
                upd[col] = plain(D(order[key]))
        ei = order.get("exchange_index")
        if row["exchange_index"] is None and isinstance(ei, int) and not isinstance(ei, bool):
            upd["exchange_index"] = ei
        if problems:
            upd["error"] = "; ".join(problems)[:TEXT_MAX_CHARS]
        self._update_order(row["client_order_id"], upd)
        return {"client_order_id": row["client_order_id"], "previous": row["status"], "status": status,
                "problems": problems}

    def mark_presumed_not_placed(self, client_order_id: str, reason: str) -> None:
        """Final verdict for an unknown outcome that reconcile could not find. It is never resent."""
        self._update_order(client_order_id, {"status": "presumed_not_placed", "updated_ts": float(self.clock()),
                                             "error": str(reason)[:TEXT_MAX_CHARS]})
        self.log_event("presumed_not_placed", {"client_order_id": client_order_id, "reason": reason},
                       entity_type="order", entity_id=client_order_id)

    # ------------------------------------------------------------------------------------------ order queries
    def order(self, client_order_id: str) -> dict | None:
        rows = self._rows("SELECT * FROM exec_orders WHERE client_order_id = ? AND env = ?",
                          (client_order_id, self.env))
        return rows[0] if rows else None

    def order_by_exchange_id(self, order_id: str) -> dict | None:
        rows = self._rows("SELECT * FROM exec_orders WHERE order_id = ? AND env = ?", (order_id, self.env))
        return rows[0] if rows else None

    def orders_for(self, ticker: str) -> list[dict]:
        return self._rows("SELECT * FROM exec_orders WHERE env = ? AND ticker = ? ORDER BY created_ts",
                          (self.env, ticker))

    def _status_in(self, statuses: Iterable[str], extra: str = "", params: tuple = ()) -> list[dict]:
        st = sorted(statuses)
        return self._rows(f"SELECT * FROM exec_orders WHERE env = ? AND status IN ({', '.join('?' * len(st))}){extra}"
                          " ORDER BY created_ts", (self.env, *st, *params))

    def open_orders(self) -> list[dict]:
        """Orders (all runs in this env) that are not final: intent, unknown, duplicate, acked, resting."""
        return self._status_in(OPEN_STATUSES)

    def unresolved_orders(self, tickers: Iterable[str] | None = None) -> list[dict]:
        """Orders whose outcome is unknown (intent, unknown, duplicate): reconcile them, never resend."""
        rows = self._status_in(UNRESOLVED_STATUSES)
        if tickers is not None:
            want = set(tickers)
            rows = [r for r in rows if r["ticker"] in want]
        return rows

    def has_inflight(self, ticker: str, side: str) -> bool:
        return bool(self._status_in(OPEN_STATUSES, " AND ticker = ? AND side = ?", (ticker, side)))

    def find_intent(self, ticker: str, side: str, price: str, count: str, since_ts: float) -> bool:
        """True if this run created an intent with these terms at or after ``since_ts``."""
        return bool(self._rows(
            "SELECT 1 FROM exec_orders WHERE env = ? AND run_id = ? AND ticker = ? AND side = ? AND price = ? "
            "AND count = ? AND created_ts >= ? LIMIT 1", (self.env, self.run_id, ticker, side, price, count, since_ts)))

    def count_orders_since(self, since_ts: float | None = None, run_only: bool = True) -> int:
        """Order intents created at or after ``since_ts`` (every attempt counts, rejected ones too)."""
        sql = "SELECT COUNT(*) AS n FROM exec_orders WHERE env = ?"
        params: list[Any] = [self.env]
        if run_only:
            sql += " AND run_id = ?"
            params.append(self.run_id)
        if since_ts is not None:
            sql += " AND created_ts >= ?"
            params.append(since_ts)
        return int(self._rows(sql, params)[0]["n"])

    def touched_tickers(self, run_only: bool = True) -> list[str]:
        sql = "SELECT DISTINCT ticker FROM exec_orders WHERE env = ?" + (" AND run_id = ?" if run_only else "")
        params = (self.env, self.run_id) if run_only else (self.env,)
        return sorted(r["ticker"] for r in self._rows(sql, params))

    def worst_of_orders_since(self, since_ts: float) -> Decimal:
        """Worst case (collateral + worst fee, full count) of orders created at or after ``since_ts`` that may have
        been placed: what a cash reading taken at ``since_ts`` does not reflect yet."""
        total = D0
        for o in self._rows("SELECT * FROM exec_orders WHERE env = ? AND created_ts >= ?", (self.env, since_ts)):
            if o["status"] in NOT_PLACED_STATUSES:
                continue
            p, c = D(o["price"]), D(o["count"])
            total += units.collateral(o["side"], p, c) + units.worst_fee(c, p)
        return total

    # ------------------------------------------------------------------------------------------ fills and positions
    def _owner_of(self, f: Mapping[str, Any]) -> dict | None:
        """The ledger order a fill belongs to: by exchange order_id (REST), else by client_order_id (WS). None = foreign."""
        order_id = f.get("order_id")
        owner = self.order_by_exchange_id(order_id) if order_id else None
        if owner is None and f.get("client_order_id"):
            owner = self.order(str(f["client_order_id"]))
        return owner

    def split_fills(self, fills: Iterable[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
        """(ours, foreign): which fills belong to orders in this ledger and which do not (a manual trade in the app).

        Foreign fills must never reach exec_fills: P&L, positions and the loss cap are computed from that table.
        """
        ours: list[Mapping[str, Any]] = []
        foreign: list[Mapping[str, Any]] = []
        for f in fills:
            (ours if self._owner_of(f) is not None else foreign).append(f)
        return ours, foreign

    def _fill_row(self, f: Mapping[str, Any]) -> dict:
        fill_id = f.get("fill_id") or f.get("trade_id")
        ticker = f.get("ticker") or f.get("market_ticker")
        side = f.get("outcome_side") or {"bid": "yes", "ask": "no"}.get(str(f.get("book_side")))
        if not fill_id or not ticker or side not in ("yes", "no"):
            raise ValueError(f"unusable fill (fill_id={fill_id!r}, ticker={ticker!r}, outcome_side={side!r})")
        count = D(f["count_fp"] if f.get("count_fp") is not None else f.get("count"))
        yes = D(f["yes_price_dollars"])
        no = D(f["no_price_dollars"]) if f.get("no_price_dollars") not in (None, "") else D1 - yes   # WS: no NO price
        fee = D(f.get("fee_cost") or "0")
        if count <= 0 or not (D0 <= yes <= D1) or not (D0 <= no <= D1) or fee < 0:
            raise ValueError(f"fill {fill_id}: count/price/fee out of range")
        order_id = f.get("order_id")
        owner = self._owner_of(f)
        if owner is not None and not order_id:
            order_id = owner["order_id"]
        created = _ts(f.get("created_time"))
        if created is None and _int_or_none(f.get("ts_ms")) is not None:
            created = _int_or_none(f.get("ts_ms")) / 1000.0
        return {
            "fill_id": str(fill_id), "env": self.env, "run_id": owner["run_id"] if owner else None,
            "order_id": order_id, "client_order_id": f.get("client_order_id") or (owner or {}).get("client_order_id"),
            "ticker": ticker, "exchange_index": _int_or_none(f.get("exchange_index")), "outcome_side": side,
            "book_side": f.get("book_side"), "count": plain(count), "yes_price": plain(yes), "no_price": plain(no),
            "is_taker": None if f.get("is_taker") is None else (1 if f.get("is_taker") else 0),
            "fee_cost": plain(fee), "created_ts": created,
            "source": "rest" if f.get("created_time") or f.get("fill_id") else "ws",
            "raw_json": self._json(f),
        }

    def upsert_fills(self, fills: Iterable[Mapping[str, Any]], *, foreign: str = "skip") -> int:
        """Insert REST or WS fills, deduplicated on fill_id (= trade_id). Returns how many were new.

        Only fills of orders in this ledger are stored (matched by order_id, or client_order_id for WS fills). A fill
        nobody here owns is a manual trade in the app: ``foreign="skip"`` (default) leaves it out and lists it in
        ``self.last_foreign``; ``"store"`` keeps it anyway (it then counts in P&L, so only for tests and tools);
        ``"error"`` raises ValueError.

        All-or-nothing: an unusable fill raises ValueError and nothing is written.
        """
        if foreign not in ("skip", "store", "error"):
            raise ValueError("foreign must be 'skip', 'store' or 'error'")
        items = list(fills)
        self.last_foreign = []
        if foreign != "store":
            ours, other = self.split_fills(items)
            if other and foreign == "error":
                raise ValueError(f"{len(other)} fill(s) belong to no order in this ledger")
            self.last_foreign = [dict(f) for f in other]
            items = ours
        rows = [self._fill_row(f) for f in items]
        if not rows:
            return 0
        cols = list(rows[0])
        sql = (f"INSERT INTO exec_fills ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
               "ON CONFLICT (fill_id) DO NOTHING")

        def write() -> int:
            n = 0
            for r in rows:
                n += self.conn.execute(sql, [r[c] for c in cols]).rowcount
            return n

        return self._write(write)

    def fills_for(self, ticker: str | None = None, order_id: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM exec_fills WHERE env = ?", [self.env]
        if ticker is not None:
            sql += " AND ticker = ?"
            params.append(ticker)
        if order_id is not None:
            sql += " AND order_id = ?"
            params.append(order_id)
        return self._rows(sql + " ORDER BY created_ts, fill_id", params)

    def _aggs(self) -> dict[str, TickerAgg]:
        out: dict[str, TickerAgg] = {}
        for r in self._rows("SELECT ticker, outcome_side, count, yes_price, no_price, fee_cost FROM exec_fills "
                            "WHERE env = ?", (self.env,)):
            out.setdefault(r["ticker"], TickerAgg()).add(r["outcome_side"], D(r["count"]), D(r["yes_price"]),
                                                         D(r["no_price"]), D(r["fee_cost"]))
        return out

    def ticker_agg(self, ticker: str) -> TickerAgg:
        return self._aggs().get(ticker) or TickerAgg()

    def recorded_by_order(self) -> dict[str, Decimal]:
        """Sum of fetched fill counts per order_id."""
        out: dict[str, Decimal] = {}
        for r in self._rows("SELECT order_id, count FROM exec_fills WHERE env = ? AND order_id IS NOT NULL",
                            (self.env,)):
            out[r["order_id"]] = out.get(r["order_id"], D0) + D(r["count"])
        return out

    def _unrecorded(self, o: Mapping[str, Any], recorded: Mapping[str, Decimal]) -> Decimal:
        """Contracts an order reported filled whose fills are not fetched yet."""
        if not o["order_id"] or not o["fill_count"]:
            return D0
        return max(D0, D(o["fill_count"]) - recorded.get(o["order_id"], D0))

    def position(self, ticker: str, include_unrecorded: bool = True) -> Decimal:
        """Signed position (+YES / -NO) from fetched fills, plus (by default) fills an order reported but that are not
        fetched yet. Compare with positions(ticker).position_fp using ``include_unrecorded=False``."""
        net = self.ticker_agg(ticker).net
        if include_unrecorded:
            recorded = self.recorded_by_order()
            for o in self.orders_for(ticker):
                extra = self._unrecorded(o, recorded)
                net += extra if o["side"] == "bid" else -extra
        return net

    def positions(self, include_unrecorded: bool = True) -> dict[str, Decimal]:
        tickers = set(self._aggs()) | {r["ticker"] for r in self._rows(
            "SELECT DISTINCT ticker FROM exec_orders WHERE env = ?", (self.env,))}
        return {t: self.position(t, include_unrecorded) for t in sorted(tickers)}

    # ------------------------------------------------------------------------------------------ settlements and P&L
    def settled_tickers(self) -> set[str]:
        return {r["ticker"] for r in self._rows("SELECT ticker FROM exec_settlements WHERE env = ?", (self.env,))}

    def unsettled_tickers(self) -> list[str]:
        """Tickers with fills, reported fills or open orders and no settlement record yet."""
        settled = self.settled_tickers()
        tickers = set(self._aggs())
        for o in self._rows("SELECT ticker, status, fill_count FROM exec_orders WHERE env = ?", (self.env,)):
            if o["status"] in OPEN_STATUSES or (o["fill_count"] and D(o["fill_count"]) > 0):
                tickers.add(o["ticker"])
        return sorted(tickers - settled)

    def tickers_to_reconcile(self) -> list[str]:
        return sorted({o["ticker"] for o in self.open_orders()} | set(self.unsettled_tickers()))

    def _pnl(self, s: Mapping[str, Any], agg: TickerAgg | None) -> tuple[Decimal, str]:
        """Realized P&L of a settled ticker: payoff - cost - fill fees (fees once), from the fills when we have them,
        else from the settlement record alone (revenue - total costs - its fee_cost)."""
        if agg is not None and agg.n:
            return agg.payoff(s.get("market_result"), s.get("value_cents")) - agg.cost - agg.fees, "fills"
        rev = units.from_cents(int(s["revenue_cents"])) if s.get("revenue_cents") is not None else D0
        return rev - _d0(s.get("yes_total_cost")) - _d0(s.get("no_total_cost")) - _d0(s.get("fee_cost")), "settlement"

    def record_settlements(self, settlements: Iterable[Mapping[str, Any]]) -> list[dict]:
        """Persist GET /portfolio/settlements rows (one per env and ticker) and return the computed P&L per row.

        ``revenue``/``value`` are integer cents. ``check`` is ``ok`` or ``mismatch: ...`` when the revenue differs from
        what the local fills say the net position should receive.
        """
        aggs = self._aggs()
        now = float(self.clock())
        rows, out = [], []
        for s in settlements:
            ticker = s.get("ticker")
            if not ticker:
                raise ValueError("settlement row without a ticker")
            revenue_cents, value_cents = _cents(s.get("revenue")), _cents(s.get("value"))
            result = s.get("market_result")
            row = {
                "env": self.env, "ticker": ticker, "event_ticker": s.get("event_ticker"),
                "exchange_index": _int_or_none(s.get("exchange_index")), "market_result": result,
                "yes_count": _dec_text(s.get("yes_count_fp")), "yes_total_cost": _dec_text(s.get("yes_total_cost_dollars")),
                "no_count": _dec_text(s.get("no_count_fp")), "no_total_cost": _dec_text(s.get("no_total_cost_dollars")),
                "revenue_cents": revenue_cents,
                "revenue": None if revenue_cents is None else plain(units.from_cents(revenue_cents)),
                "value_cents": value_cents, "fee_cost": _dec_text(s.get("fee_cost")),
                "settled_ts": _ts(s.get("settled_time")), "run_id": self.run_id, "raw_json": self._json(s),
                "recorded_ts": now,
            }
            agg = aggs.get(ticker)
            pnl, basis = self._pnl(row, agg)
            check, fee_note = "ok", None
            if agg is not None and agg.n:
                if result in ("yes", "no") and revenue_cents is not None:
                    held = agg.net if result == "yes" else -agg.net
                    expected = max(D0, held) * 100
                    if expected != Decimal(revenue_cents):
                        check = (f"mismatch: revenue {revenue_cents} cents but the local fills hold "
                                 f"{plain(max(D0, held))} winning contracts ({plain(expected)} cents)")
                if row["fee_cost"] is not None and D(row["fee_cost"]) != agg.fees:
                    fee_note = (f"settlement fee_cost {row['fee_cost']} != sum of fill fees {plain(agg.fees)}; "
                                "the fill fees are the ones counted")
            row.update(pnl=plain(pnl), pnl_basis=basis, check_status=check)
            rows.append(row)
            out.append({"ticker": ticker, "market_result": result, "revenue_cents": revenue_cents,
                        "revenue": row["revenue"], "pnl": pnl, "basis": basis, "check": check, "fee_note": fee_note})
        if rows:
            from kpm.core import db

            def write() -> None:
                for r in rows:
                    db.upsert(self.conn, "exec_settlements", r, ("env", "ticker"))

            self._write(write)
        return out

    def realized_pnl(self, since_ts: float | None = None) -> Decimal:
        """Net realized P&L (dollars) of settled tickers in this env, optionally only those settled since ``since_ts``.
        Recomputed from the fills each time, so a late-fetched fill is never missed."""
        aggs = self._aggs()
        total = D0
        for s in self._rows("SELECT * FROM exec_settlements WHERE env = ?", (self.env,)):
            ts = s["settled_ts"] if s["settled_ts"] is not None else s["recorded_ts"]
            if since_ts is not None and ts < since_ts:
                continue
            total += self._pnl(s, aggs.get(s["ticker"]))[0]
        return total

    def realized_loss(self, since_ts: float | None = None) -> Decimal:
        """Net realized loss as a positive number (0 when net realized P&L is >= 0)."""
        return max(D0, -self.realized_pnl(since_ts))

    def realized_loss_24h(self) -> Decimal:
        return self.realized_loss(float(self.clock()) - DAY_S)

    def realized_loss_total(self) -> Decimal:
        """All runs in this env: the user's cap is a TOTAL loss cap, so a restart never resets it."""
        return self.realized_loss(None)

    def risk_snapshot(self) -> dict[str, Decimal]:
        """The four numbers the loss and exposure rules need, from ONE read of the fills and settlements (each of the
        separate methods re-reads all of them): ``realized_loss_24h``, ``realized_loss_total``, ``open_worst_case``,
        ``open_exposure``. Equal to calling those methods one by one."""
        aggs = self._aggs()
        since = float(self.clock()) - DAY_S
        total = day = D0
        for s in self._rows("SELECT * FROM exec_settlements WHERE env = ?", (self.env,)):
            pnl = self._pnl(s, aggs.get(s["ticker"]))[0]
            total += pnl
            ts = s["settled_ts"] if s["settled_ts"] is not None else s["recorded_ts"]
            if ts >= since:
                day += pnl
        worst, exposure = self._open(aggs)
        return {"realized_loss_24h": max(D0, -day), "realized_loss_total": max(D0, -total),
                "open_worst_case": worst, "open_exposure": exposure}

    def _open(self, aggs: Mapping[str, TickerAgg] | None = None) -> tuple[Decimal, Decimal]:
        """(worst case, collateral exposure) of everything not settled yet."""
        settled = self.settled_tickers()
        worst = exposure = D0
        for t, a in (self._aggs() if aggs is None else aggs).items():
            if t in settled:
                continue
            worst += max(D0, a.cost + a.fees - a.netted)
            exposure += max(D0, a.cost - a.netted)
        recorded = self.recorded_by_order()
        for o in self._rows("SELECT * FROM exec_orders WHERE env = ?", (self.env,)):
            if o["ticker"] in settled or o["status"] in NOT_PLACED_STATUSES:
                continue
            count = D(o["count"])
            if o["status"] in UNRESOLVED_STATUSES:
                # it may have filled completely; fills already fetched for it are counted above, not twice
                qty = max(D0, count - recorded.get(o["order_id"], D0)) if o["order_id"] else count
            else:
                resting = D(o["remaining_count"]) if o["status"] in ("acked", "resting") and o["remaining_count"] \
                    else D0
                qty = min(count, self._unrecorded(o, recorded) + resting)
            if qty > 0:
                p = D(o["price"])
                c = units.collateral(o["side"], p, qty)
                worst += c + units.worst_fee(qty, p)
                exposure += c
        return worst, exposure

    def open_worst_case(self) -> Decimal:
        """What can still be lost on unsettled tickers and unfinished orders (dollars, >= 0, fees included)."""
        return self._open()[0]

    def open_exposure(self) -> Decimal:
        """Collateral at risk on unsettled tickers and unfinished orders (dollars, fees excluded)."""
        return self._open()[1]

    def pnl_for_orders(self, client_order_ids: Iterable[str], market_results: Mapping[str, str | None]) -> dict:
        """Cost, fees, payout and P&L of the fills of some orders (e.g. the legs of one pair).

        ``market_results`` maps ticker -> 'yes' | 'no' (public market result); payout/pnl are None while any traded
        ticker has no result yet.
        """
        ids = list(client_order_ids)
        if not ids:
            return {"cost": D0, "fees": D0, "payout": D0, "pnl": D0, "filled": {}}
        rows = self._rows(
            "SELECT f.* FROM exec_fills f JOIN exec_orders o ON o.order_id = f.order_id AND o.env = f.env "
            f"WHERE f.env = ? AND o.client_order_id IN ({', '.join('?' * len(ids))})", (self.env, *ids))
        per: dict[str, TickerAgg] = {}
        for r in rows:
            per.setdefault(r["ticker"], TickerAgg()).add(r["outcome_side"], D(r["count"]), D(r["yes_price"]),
                                                         D(r["no_price"]), D(r["fee_cost"]))
        cost = sum((a.cost for a in per.values()), D0)
        fees = sum((a.fees for a in per.values()), D0)
        payout: Decimal | None = D0
        for t, a in per.items():
            res = market_results.get(t)
            if res not in ("yes", "no"):
                payout = None
                break
            payout += a.payoff(res)
        return {"cost": cost, "fees": fees, "payout": payout,
                "pnl": None if payout is None else payout - cost - fees,
                "filled": {t: a.net for t, a in per.items()}}

    def update_settlement_check(self, ticker: str, check_status: str) -> None:
        """Replace the check_status of a recorded settlement (reconcile does this when foreign fills explain a gap)."""
        self._write(lambda: self.conn.execute(
            "UPDATE exec_settlements SET check_status = ? WHERE env = ? AND ticker = ?",
            (str(check_status)[:TEXT_MAX_CHARS], self.env, ticker)))

    def last_activity_ts(self, ticker: str) -> float | None:
        """Latest time this ledger sent, got a result for, or created an order on ``ticker`` (None if it has none).

        Reconcile waits until the exchange's REST data (user_data_timestamp) has passed this before it trusts a read.
        """
        best: float | None = None
        for o in self.orders_for(ticker):
            for k in ("sent_ts", "result_ts", "created_ts"):
                if o[k] is not None and (best is None or o[k] > best):
                    best = float(o[k])
        return best

    # ------------------------------------------------------------------------------------------ halts, lockout, summary
    def add_halt_listener(self, fn: Callable[[str], Any]) -> None:
        """Register ``fn(reason)`` to run when :meth:`halt` is called (Safety registers ``Safety.trip``)."""
        if fn not in self._halt_listeners:
            self._halt_listeners.append(fn)

    def halt(self, reason: str, details: Mapping[str, Any] | None = None) -> None:
        """A real inconsistency was found (a mismatch that two reads agree on, an unknown order that cannot be found):
        record it in audit_log and tell every listener, so the session stops placing orders. Never raises."""
        self.log_event("halt", {"reason": str(reason)[:TEXT_MAX_CHARS], **dict(details or {})}, entity_type="env",
                       entity_id=self.env)
        for fn in list(self._halt_listeners):
            try:
                fn(str(reason))
            except Exception:
                log.exception("a halt listener failed")

    def last_halt(self) -> dict | None:
        """The most recent halt recorded for this environment: ``{ts, reason}`` or None."""
        rows = self._rows("SELECT ts, details FROM audit_log WHERE component = ? AND action = 'halt' AND entity_id = ? "
                          "ORDER BY id DESC LIMIT 1", (COMPONENT, self.env))
        if not rows:
            return None
        try:
            reason = json.loads(rows[0]["details"]).get("reason")
        except (TypeError, ValueError, AttributeError):
            reason = None
        return {"ts": rows[0]["ts"], "reason": reason}

    def set_lockout(self, until_ts: float, reason: str) -> None:
        """Block new orders until ``until_ts`` (after a cancel-all). Persisted in audit_log, so a restart sees it."""
        self._lockout = max(self._lockout, float(until_ts))
        self.log_event("lockout", {"until": float(until_ts), "reason": reason}, entity_type="env",
                       entity_id=self.env)

    def lockout_until(self) -> float:
        persisted = 0.0
        for r in self._rows("SELECT details FROM audit_log WHERE component = ? AND action = 'lockout' "
                            "AND entity_id = ? ORDER BY id DESC LIMIT 20", (COMPONENT, self.env)):
            try:
                persisted = max(persisted, float(json.loads(r["details"]).get("until", 0.0)))
            except (TypeError, ValueError, AttributeError):
                continue
        return max(self._lockout, persisted)

    def summary(self) -> dict:
        """Numbers for heartbeats and banners (decimal strings)."""
        worst, exposure = self._open()
        return {"env": self.env, "realized_pnl_total": plain(self.realized_pnl()),
                "realized_loss_total": plain(self.realized_loss_total()),
                "realized_loss_24h": plain(self.realized_loss_24h()),
                "open_worst_case": plain(worst), "open_exposure": plain(exposure),
                "open_orders": len(self.open_orders()), "unresolved_orders": len(self.unresolved_orders())}
