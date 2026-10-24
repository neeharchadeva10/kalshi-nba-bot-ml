"""REST client for the Kalshi Trade API v2 execution path (docs/execution_design.md, sections 2-4).

Deliberately separate from ``kpm.clients.http.CachedClient``: no disk cache (portfolio data must be
fresh) and no automatic resend of writes (a resent IOC that already executed would fill twice).

Rules enforced here, whatever the caller does:

* **One host.** Only the environment's allowlisted REST base from ``env.HOSTS``; https only; the
  ``requests`` session ignores proxy/netrc environment settings and never follows redirects (a 3xx is
  a hard error: requests would turn a 301 POST into a GET and drop the body).
* **Endpoint allowlist.** Any (method, path) not in :data:`ENDPOINTS` raises :class:`EndpointNotAllowed`.
  Transfers, API-key, subaccount, allocation, order-group and tier-upgrade endpoints are absent.
* **Dry-run by default.** Every write raises ``PermissionError`` unless the client was built with
  ``live=True`` (which also requires a loaded key). The transport never sees the request.
* **Wire formats.** Order bodies are checked before sending: price is a 4-dp YES-price string, count a
  2-dp contract string, side ``bid``/``ask``, explicit ``exchange_index``, a ``client_order_id``, and no
  legacy fields. Cancels always carry the order's ``exchange_index`` and never ``market_ticker``.
* **Retries.** Reads: bounded backoff 0.5, 1, 2, 4 s on 408/425/429/5xx/network errors; 401/403 stop
  at once. Writes: never resent, except a 429 (rejected before matching), which is resent with the
  same body and ``client_order_id`` at most twice, after 1 s and 2 s. Every attempt is re-signed.
* **Outcomes.** Writes return :class:`Result` with ``outcome`` in ok | rejected | duplicate | throttled |
  unknown | auth (2xx | other 4xx | 409 | 429 | timeout, connection error, 5xx, 3xx | 401, 403).
  ``unknown`` means: reconcile, never resend automatically.
* **Audit without secrets.** The ``audit`` callable gets explicit keyword fields only (env, live, ts_ms,
  method, path, params, body, client_order_id, status, response[:64 KB], latency_ms, error, attempt,
  kind, outcome). Header values, the key id, signatures and key material are never passed.
  Public market-data reads are audited only with ``audit_public_reads=True``.
* **Clock.** ``server_time_skew()`` is the median of 3 probes of the public ``/exchange/status`` endpoint:
  each probe compares the local clock (at the midpoint of the round trip) with the response's ``Date``
  header. The result is ``local - server`` in seconds (positive: this PC runs ahead). The ``Date`` header has
  1 s resolution, so the probe centres it (+0.5 s) and spaces the probes 1.1 s apart: ``/exchange/status``
  is cached for 1 s (``cache-control: max-age=1``), ``/markets`` for 15 s (so it is never used for this).

Response shapes returned by the read methods:

* ``series(t)`` / ``market(t)`` / ``order(id)``: the inner object (``{"series": {...}}`` unwrapped).
* ``open_markets``, ``orders``, ``fills``, ``settlements``, ``positions``: a :class:`Listing` (a list of
  the items across all pages; ``listing["orders"]``/``listing.raw`` also give the merged payload).
* ``orderbook(t)``: ``{"ticker", "yes": [(price, size), ...], "no": [...], "fetched_at"}`` with Decimal
  levels in ascending order (best bid LAST) and ``fetched_at`` = client clock when the response arrived.
"""
from __future__ import annotations

import json
import logging
import re
import statistics
import time
from dataclasses import dataclass, replace
from datetime import timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable, Mapping, Protocol
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

from kpm.execution import units
from kpm.execution.auth import auth_headers, sign_path
from kpm.execution.env import HOSTS, Env

log = logging.getLogger(__name__)

OUTCOMES = ("ok", "rejected", "duplicate", "throttled", "unknown", "auth")
READ_BACKOFF_S = (0.5, 1.0, 2.0, 4.0)
WRITE_429_BACKOFF_S = (1.0, 2.0)                 # at most two resends of the identical write
RETRYABLE_READ_STATUS = frozenset({408, 425, 429})
RESPONSE_AUDIT_LIMIT = 65536
DEFAULT_TIMEOUT_S = 10.0
MAX_PAGES = 50
MAX_BATCH = 10                                   # a Basic-tier Write bucket (100 tokens) fits 10 creates
USER_AGENT = "kpm-exec/0.1"
CLOCK_PROBES = 3                                 # server_time_skew(): median of 3 Date-header probes
CLOCK_PROBE_SPACING_S = 1.1                      # > the 1 s cache lifetime of /exchange/status
CLOCK_MIN_SAMPLES = 2                            # fewer usable probes than this = no answer (fail closed)
CLOCK_ENDPOINT = "/exchange/status"              # public, cache-control max-age=1 (the orderbook has no cache header)
_REPLY_HEADERS = ("date", "age", "x-kalshi-cache-hits")   # the only response headers the client keeps

_SEG = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"       # one path segment: ticker, series or order id
_SEG_RE = re.compile(_SEG)
_CLIENT_ORDER_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
_PRICE_RE = re.compile(r"0\.[0-9]{4}")           # request price: YES dollars, exactly 4 dp (units.fmt_price)
_COUNT_RE = re.compile(r"[0-9]{1,9}\.[0-9]{2}")  # request count: contracts, exactly 2 dp (units.fmt_count)
_RESERVED_ORDER_IDS = frozenset({"batched", "queue_positions"})

# (method, endpoint pattern, kind). kind: public (unsigned GET), read (signed GET), write (signed mutation).
ENDPOINTS: tuple[tuple[str, str, str], ...] = (
    ("GET", r"/exchange/status", "public"),
    ("GET", r"/exchange/user_data_timestamp", "public"),
    ("GET", r"/historical/cutoff", "public"),
    ("GET", rf"/series/{_SEG}", "public"),
    ("GET", r"/markets", "public"),
    ("GET", rf"/markets/{_SEG}", "public"),
    ("GET", rf"/markets/{_SEG}/orderbook", "public"),
    ("GET", r"/account/limits", "read"),
    ("GET", r"/portfolio/balance", "read"),
    ("GET", r"/portfolio/positions", "read"),
    ("GET", r"/portfolio/orders", "read"),
    ("GET", rf"/portfolio/orders/{_SEG}", "read"),
    ("GET", rf"/portfolio/orders/{_SEG}/queue_position", "read"),
    ("GET", r"/portfolio/fills", "read"),
    ("GET", r"/portfolio/settlements", "read"),
    ("POST", r"/portfolio/events/orders", "write"),                      # create (V2)
    ("POST", r"/portfolio/events/orders/batched", "write"),              # batch create
    ("DELETE", r"/portfolio/events/orders/batched", "write"),            # batch cancel (JSON body)
    ("DELETE", rf"/portfolio/events/orders/{_SEG}", "write"),            # cancel (?exchange_index=N)
    ("POST", rf"/portfolio/events/orders/{_SEG}/amend", "write"),
    ("POST", rf"/portfolio/events/orders/{_SEG}/decrease", "write"),
    ("DELETE", r"/portfolio/events/orders", "write"),                    # cancel ALL resting orders (204)
)
_ENDPOINT_RES = tuple((m, re.compile(p), k) for m, p, k in ENDPOINTS)

CREATE_FIELDS = frozenset({"ticker", "client_order_id", "side", "count", "price", "expiration_time",
                           "time_in_force", "post_only", "self_trade_prevention_type", "cancel_order_on_pause",
                           "reduce_only", "subaccount", "order_group_id", "exchange_index"})
CREATE_REQUIRED = frozenset({"ticker", "client_order_id", "side", "count", "price", "time_in_force",
                             "self_trade_prevention_type", "exchange_index"})
AMEND_FIELDS = frozenset({"ticker", "side", "price", "count", "client_order_id", "updated_client_order_id",
                          "expiration_time", "exchange_index"})
AMEND_REQUIRED = frozenset({"ticker", "side", "price", "count", "exchange_index"})
TIME_IN_FORCE = ("fill_or_kill", "good_till_canceled", "immediate_or_cancel")
STP_TYPES = ("taker_at_cross", "maker")

_READ_PARAMS = {
    "orders": frozenset({"ticker", "event_ticker", "min_ts", "max_ts", "status", "limit", "cursor", "exchange_index"}),
    "fills": frozenset({"ticker", "order_id", "min_ts", "max_ts", "limit", "cursor", "exchange_index"}),
    "positions": frozenset({"ticker", "event_ticker", "settlement_status", "count_filter", "limit", "cursor",
                            "exchange_index"}),
    "settlements": frozenset({"ticker", "event_ticker", "min_ts", "max_ts", "limit", "cursor"}),
}
_ENUM_PARAMS = {"status": ("resting", "canceled", "executed"),
                "settlement_status": ("unsettled", "settled", "all")}


# ============================================================================================ errors
class ExecError(RuntimeError):
    """Base class for execution-client failures. ``error`` is a parsed error dict (see :func:`parse_error`)."""

    def __init__(self, message: str, *, status: int | None = None, error: dict | None = None,
                 path: str | None = None):
        super().__init__(message)
        self.status = status
        self.error = error
        self.path = path


class AuthError(ExecError):
    """401/403, or an authenticated call without a loaded key. Halt; never loop on it."""


class RedirectError(ExecError):
    """A 3xx response. Redirects are never followed."""


class ApiError(ExecError):
    """A non-2xx read (other than auth/redirect) or a malformed 2xx body."""


class TransportError(ExecError):
    """Network failure (timeout, connection error) that outlived the read retries."""


class EndpointNotAllowed(PermissionError):
    """The (method, path) is not on the execution allowlist."""


class _SendFailed(Exception):
    """Internal: ``transport.send`` raised a network error (``.cause``). Only this is retried / reported as 'unknown'.

    The client's own refusals (``PermissionError`` when not live, :class:`EndpointNotAllowed`) are OSError subclasses
    too, so catching OSError around ``_attempt`` would mistake a safety refusal for a network failure.
    """

    def __init__(self, cause: BaseException):
        super().__init__(type(cause).__name__)
        self.cause = cause


# ============================================================================================ results
@dataclass(frozen=True)
class Result:
    """Outcome of one logical write (possibly several 429 attempts).

    ``data`` is the parsed JSON body (``None`` for 204 / non-JSON), ``error`` a :func:`parse_error` dict.
    """

    outcome: str
    status: int | None
    data: Any = None
    error: dict | None = None
    attempts: int = 1
    latency_ms: float | None = None
    client_order_id: str | None = None

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {self.outcome!r}")

    @property
    def ok(self) -> bool:
        return self.outcome == "ok"

    @property
    def order_id(self) -> str | None:
        return self.data.get("order_id") if isinstance(self.data, dict) else None


class Listing(list):
    """Items gathered across pages. ``listing["cursor"]`` / ``listing.raw`` expose the merged payload."""

    def __init__(self, items: Iterable[Any], raw: dict | None = None):
        super().__init__(items)
        self.raw: dict = raw or {}

    def __getitem__(self, key):                      # type: ignore[override]
        if isinstance(key, str):
            return self.raw[key]
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)


@dataclass(frozen=True)
class ClockSample:
    """One Date-header probe. ``skew_s`` is local - server in seconds (positive: this PC runs ahead)."""

    skew_s: float
    rtt_s: float
    server_date: str            # the Date header exactly as received (public information)
    cached: bool                # served from a cache (Age > 0 or x-kalshi-cache-hits > 0): up to ~1 s stale


@dataclass(frozen=True)
class ClockProbe:
    """Result of :meth:`ExecClient.clock_probe`: the median skew plus the samples it came from."""

    skew_s: float
    samples: tuple[ClockSample, ...]

    @property
    def spread_s(self) -> float:
        values = [s.skew_s for s in self.samples]
        return max(values) - min(values)


@dataclass(frozen=True)
class _Reply:
    """One HTTP response as the client keeps it: status, text, and only the whitelisted headers."""

    status: int
    text: str
    latency_ms: float
    headers: Mapping[str, str]
    sent_at: float              # client clock just before the send
    received_at: float          # client clock just after the response arrived


def _err(code: str | None, message: str | None, details: str | None = None, shape: str = "client") -> dict:
    return {"code": code, "message": message, "details": details, "shape": shape}


def parse_error(body: Any) -> dict:
    """Normalize any Kalshi error body to ``{code, message, details, shape}``.

    Shapes seen live or documented: ``{"error": {"code", "message", "details"}}`` (``error_object``),
    ``{"error": "too many requests"}`` (``error_string``), ``{"msg": "Parameter validation failed ..."}``
    (``msg``), the unwrapped OpenAPI ``{"code", "message"}`` (``flat``), a non-JSON body (``non_json``)
    and an empty body (``empty``). Detect 429 by status code, never by body.
    """
    if isinstance(body, (bytes, bytearray)):
        body = bytes(body).decode("utf-8", errors="replace")
    if isinstance(body, str):
        text = body.strip()
        if not text:
            return _err(None, None, None, "empty")
        try:
            data = json.loads(text)
        except ValueError:
            return _err(None, text[:500], None, "non_json")
    else:
        data = body
    if data is None:
        return _err(None, None, None, "empty")

    def s(v: Any) -> str | None:
        return None if v is None else str(v)[:500]

    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return _err(s(err.get("code")), s(err.get("message")), s(err.get("details")), "error_object")
        if isinstance(err, str):
            return _err(None, err[:500], None, "error_string")
        if "msg" in data:
            return _err(None, s(data.get("msg")), None, "msg")
        if "code" in data or "message" in data:
            return _err(s(data.get("code")), s(data.get("message")), s(data.get("details")), "flat")
    return _err(None, json.dumps(data, default=str)[:500], None, "unknown")


def classify(status: int | None, *, exc: BaseException | None = None) -> str:
    """Map an HTTP status (or a transport exception) to a write outcome class."""
    if exc is not None or status is None:
        return "unknown"
    if 200 <= status < 300:
        return "ok"
    if status in (401, 403):
        return "auth"
    if status == 409:
        return "duplicate"
    if status == 429:
        return "throttled"
    if 400 <= status < 500:
        return "rejected"
    return "unknown"                  # 1xx, 3xx (never followed), 5xx: the write may or may not have happened


def _item_outcome(err: dict) -> str:
    """Outcome for a per-item error inside a 2xx batch response."""
    code = (err.get("code") or "").lower()
    if "already_exists" in code or "duplicate" in code:
        return "duplicate"
    if any(k in code for k in ("internal", "unavailable", "timeout", "unknown")):
        return "unknown"
    return "rejected"


# ============================================================================================ allowlist & validation
def endpoint_kind(method: str, endpoint: str) -> str:
    """'public' | 'read' | 'write' for an allowlisted (method, endpoint); otherwise :class:`EndpointNotAllowed`."""
    m = str(method).upper()
    for em, rx, kind in _ENDPOINT_RES:
        if em == m and rx.fullmatch(endpoint or ""):
            return kind
    raise EndpointNotAllowed(f"{m} {endpoint!r} is not on the execution endpoint allowlist")


def _segment(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _SEG_RE.fullmatch(value):
        raise ValueError(f"bad {what}: {str(value)[:60]!r}")
    return value


def _order_id(value: Any) -> str:
    oid = _segment(value, "order id")
    if oid in _RESERVED_ORDER_IDS:
        raise ValueError(f"{oid!r} is not an order id")
    return oid


def _exchange_index(value: Any) -> int:
    """An explicit shard index >= 0 (never -1/auto-routing, never a bool or a string)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"exchange_index must be an int >= 0 read from the market/order object, got {value!r}")
    return value


def _check_price(value: Any, what: str = "price") -> None:
    if not isinstance(value, str) or not _PRICE_RE.fullmatch(value) or not units.D0 < Decimal(value) < units.D1:
        raise ValueError(f"{what} must be a 4-dp YES-price dollar string from units.fmt_price, got {value!r}")


def _check_count(value: Any, what: str = "count") -> None:
    if not isinstance(value, str) or not _COUNT_RE.fullmatch(value) or Decimal(value) <= 0:
        raise ValueError(f"{what} must be a 2-dp contract string from units.fmt_count, got {value!r}")


def _check_client_order_id(value: Any, what: str = "client_order_id") -> None:
    if not isinstance(value, str) or not _CLIENT_ORDER_ID_RE.fullmatch(value):
        raise ValueError(f"{what} must be a short id such as str(uuid4()), got {str(value)[:80]!r}")


def _check_bool(body: Mapping, name: str) -> None:
    if name in body and not isinstance(body[name], bool):
        raise ValueError(f"{name} must be a bool")


def _check_expiration(body: Mapping) -> None:
    if "expiration_time" in body:
        units.unix_seconds(body["expiration_time"])          # Unix SECONDS; ms values are refused


def validate_order_body(body: Mapping[str, Any]) -> dict:
    """Check a CreateOrderV2 body's wire format and return a plain copy. Raises ``ValueError``/``TypeError``.

    Format only; caps, grid and freshness are the safety layer's job.
    """
    if not isinstance(body, Mapping):
        raise TypeError("order body must be a mapping")
    keys = set(body)
    unknown = keys - CREATE_FIELDS
    if unknown:
        raise ValueError(f"fields not in CreateOrderV2 (legacy or misspelled): {sorted(unknown)}")
    missing = CREATE_REQUIRED - keys
    if missing:
        raise ValueError(f"order body is missing {sorted(missing)}")
    _segment(body["ticker"], "ticker")
    _check_client_order_id(body["client_order_id"])
    if body["side"] not in units.BOOK_SIDES:
        raise ValueError("side must be 'bid' (buy YES) or 'ask' (sell YES = buy NO); use units.to_book()")
    _check_price(body["price"])
    _check_count(body["count"])
    if body["time_in_force"] not in TIME_IN_FORCE:
        raise ValueError(f"time_in_force must be one of {TIME_IN_FORCE}")
    if body["self_trade_prevention_type"] not in STP_TYPES:
        raise ValueError(f"self_trade_prevention_type must be one of {STP_TYPES}")
    _exchange_index(body["exchange_index"])
    for name in ("post_only", "cancel_order_on_pause", "reduce_only"):
        _check_bool(body, name)
    _check_expiration(body)
    if "expiration_time" in body and body["time_in_force"] != "good_till_canceled":
        raise ValueError("expiration_time is only valid with good_till_canceled")
    if body.get("reduce_only") and body["time_in_force"] != "immediate_or_cancel":
        raise ValueError("reduce_only is only accepted with immediate_or_cancel")
    if "subaccount" in body and (isinstance(body["subaccount"], bool) or body["subaccount"] != 0):
        raise ValueError("only the primary account (subaccount 0) is supported")
    if "order_group_id" in body:
        _segment(body["order_group_id"], "order_group_id")
    return dict(body)


def validate_amend_body(body: Mapping[str, Any]) -> dict:
    """Check an AmendOrderV2 body (``count`` is the new TOTAL: filled + desired resting)."""
    if not isinstance(body, Mapping):
        raise TypeError("amend body must be a mapping")
    keys = set(body)
    if keys - AMEND_FIELDS:
        raise ValueError(f"fields not in AmendOrderV2: {sorted(keys - AMEND_FIELDS)}")
    if AMEND_REQUIRED - keys:
        raise ValueError(f"amend body is missing {sorted(AMEND_REQUIRED - keys)}")
    _segment(body["ticker"], "ticker")
    if body["side"] not in units.BOOK_SIDES:
        raise ValueError("side must be 'bid' or 'ask'")
    _check_price(body["price"])
    _check_count(body["count"])
    _exchange_index(body["exchange_index"])
    for name in ("client_order_id", "updated_client_order_id"):
        if name in body:
            _check_client_order_id(body[name], name)
    if "expiration_time" in body and body["expiration_time"] != 0:      # 0 clears the expiry
        _check_expiration(body)
    return dict(body)


def _count_param(value: Any, what: str, *, allow_zero: bool = False) -> str:
    if isinstance(value, Decimal):
        if allow_zero and value == 0:
            return "0.00"
        return units.fmt_count(value)
    if isinstance(value, str) and _COUNT_RE.fullmatch(value) and (allow_zero or Decimal(value) > 0):
        return value
    raise ValueError(f"{what} must be a Decimal or a 2-dp contract string, got {value!r}")


def _clean_params(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not params:
        return None
    out: dict[str, Any] = {}
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, bool):
            out[k] = "true" if v else "false"
        elif isinstance(v, (int, str)):
            out[k] = v
        elif isinstance(v, Decimal):
            out[k] = str(v)
        else:
            raise TypeError(f"query parameter {k} has unsupported type {type(v).__name__}")
    return out or None


def _check_read_params(kind: str, kw: Mapping[str, Any]) -> dict[str, Any]:
    allowed = _READ_PARAMS[kind]
    unknown = set(kw) - allowed
    if unknown:
        raise TypeError(f"{kind}() got unsupported parameters {sorted(unknown)} (allowed: {sorted(allowed)})")
    out = {k: v for k, v in kw.items() if v is not None}
    for k in ("min_ts", "max_ts"):
        if k in out:
            units.unix_seconds(out[k])
    if "limit" in out and (isinstance(out["limit"], bool) or not isinstance(out["limit"], int)
                           or not 1 <= out["limit"] <= 1000):
        raise ValueError("limit must be an int in 1..1000")
    if "exchange_index" in out:
        _exchange_index(out["exchange_index"])
    for k, allowed_values in _ENUM_PARAMS.items():
        if k in out and out[k] not in allowed_values:
            raise ValueError(f"{k} must be one of {allowed_values}")
    for k in ("ticker", "event_ticker", "order_id"):
        if k in out:
            _segment(out[k], k)
    return out


def _json_or_none(text: str) -> Any:
    if not text or not text.strip():
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


# ============================================================================================ helpers for callers
def shard_status(status: Mapping[str, Any], exchange_index: int) -> dict | None:
    """The ``exchange_index_statuses`` entry for one shard (the top-level flags describe shard 0 only)."""
    for entry in status.get("exchange_index_statuses") or ():
        if isinstance(entry, dict) and entry.get("exchange_index") == exchange_index:
            return entry
    return None


def shard_trading_active(status: Mapping[str, Any], exchange_index: int) -> bool:
    """True only if the shard's entry exists and says exchange_active and trading_active (missing = closed)."""
    entry = shard_status(status, exchange_index)
    return bool(entry) and entry.get("exchange_active") is True and entry.get("trading_active") is True


def breakdown_cash(balance: Mapping[str, Any], exchange_index: int) -> Decimal | None:
    """Cash on one shard from ``balance_breakdown`` (dollar string), or None when it is not listed."""
    for entry in balance.get("balance_breakdown") or ():
        if isinstance(entry, dict) and entry.get("exchange_index") == exchange_index:
            return units.dec(entry["balance"])
    return None


# ============================================================================================ transport
class Transport(Protocol):
    def send(self, method: str, url: str, *, params: dict | None, data: str | None,
             headers: dict[str, str], timeout: float) -> Any:
        """Perform one HTTP request; return an object with ``status_code``, ``text`` and ``headers``.

        ``headers`` (the response headers, any mapping) is only read for ``Date``, ``Age`` and
        ``x-kalshi-cache-hits`` by the clock probe; a transport without it just cannot be used for that.
        """


class RequestsTransport:
    """The production transport: one ``requests`` session, https only, no redirects, no urllib3 retries."""

    def __init__(self) -> None:
        s = requests.Session()
        s.trust_env = False                       # no proxy / netrc / CA overrides from the environment
        s.max_redirects = 0
        s.adapters.clear()
        s.mount("https://", HTTPAdapter(max_retries=0))
        s.headers["User-Agent"] = USER_AGENT
        self._session = s

    def send(self, method: str, url: str, *, params: dict | None, data: str | None,
             headers: dict[str, str], timeout: float) -> requests.Response:
        resp = self._session.request(method, url, params=params, data=data, headers=headers,
                                     timeout=timeout, allow_redirects=False)
        if resp.encoding is None:                 # JSON is UTF-8 (RFC 8259); never let requests sniff a charset
            resp.encoding = "utf-8"
        return resp

    def close(self) -> None:
        self._session.close()


def _reply_text(resp: Any) -> str:
    """The response body as text; an undecodable body counts as empty (a write then reads as 'unknown')."""
    try:
        return resp.text or ""
    except Exception:                                    # e.g. LookupError for an unknown charset
        return ""


def _reply_headers(resp: Any) -> dict[str, str]:
    """Only the whitelisted response headers, lower-cased. Nothing else is ever kept or audited."""
    raw = getattr(resp, "headers", None)
    if not raw:
        return {}
    try:
        lowered = {str(k).lower(): str(v) for k, v in raw.items()}
    except Exception:
        return {}
    return {k: lowered[k] for k in _REPLY_HEADERS if k in lowered}


def _int_or_zero(value: Any) -> int:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _clock_sample(reply: _Reply) -> ClockSample | None:
    """local - server for one reply, or None when it carries no usable ``Date`` header.

    ``Date`` has 1 s resolution (the true time lies in [Date, Date + 1)), so it is centred (+0.5 s); an ``Age``
    header (seconds spent in a cache) is added. The local reference is the midpoint of the round trip.
    """
    date = reply.headers.get("date")
    if not date:
        return None
    try:
        dt = parsedate_to_datetime(date)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:                                 # RFC 9110 dates are always GMT
        dt = dt.replace(tzinfo=timezone.utc)
    age = _int_or_zero(reply.headers.get("age"))
    hits = _int_or_zero(reply.headers.get("x-kalshi-cache-hits"))
    server = dt.timestamp() + 0.5 + age
    mid = (reply.sent_at + reply.received_at) / 2.0
    return ClockSample(skew_s=mid - server, rtt_s=reply.received_at - reply.sent_at, server_date=date[:64],
                       cached=age > 0 or hits > 0)


# ============================================================================================ client
class ExecClient:
    """Execution client bound to one :class:`~kpm.execution.env.Env` (one host, one key)."""

    def __init__(self, env: Env, *, live: bool = False, transport: Transport | None = None,
                 audit: Callable[..., Any] | None = None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep, timeout_s: float = DEFAULT_TIMEOUT_S,
                 audit_public_reads: bool = False):
        if not isinstance(env, Env):
            raise TypeError("env must be a kpm.execution.env.Env (see load_env)")
        if env.rest_base != HOSTS[env.name]["rest"]:
            raise ValueError("refusing a REST base that is not the environment's allowlisted host")
        if not isinstance(live, bool):
            raise TypeError("live must be a bool")
        if live and not env.has_keys:
            raise ValueError("live mode needs the environment's API key (load_env(require_keys=True))")
        parsed = urlparse(env.rest_base)
        if parsed.scheme != "https":
            raise ValueError("the REST base must be https")
        self.env = env
        self.live = live
        self.timeout_s = float(timeout_s)
        self._host = parsed.netloc
        self._transport = transport if transport is not None else RequestsTransport()
        self._audit = audit
        self._audit_public = bool(audit_public_reads)
        self._clock = clock
        self._sleep = sleep
        self.audit_failures = 0

    def __repr__(self) -> str:
        return f"ExecClient(env={self.env.name!r}, host={self._host!r}, live={self.live}, key_fp={self.env.key_fp!r})"

    @property
    def has_keys(self) -> bool:
        return self.env.has_keys

    def close(self) -> None:
        """Release the transport's connections (a no-op for transports without ``close``)."""
        closer = getattr(self._transport, "close", None)
        if callable(closer):
            closer()

    # ------------------------------------------------------------------ plumbing
    def _require_live(self, what: str) -> None:
        if not self.live:
            raise PermissionError(f"{what}: this ExecClient is not live (dry-run); writes are disabled")

    def _record(self, **fields: Any) -> None:
        if self._audit is None:
            return
        if fields.get("kind") == "public" and not self._audit_public:
            return
        try:
            self._audit(**fields)
        except Exception:                                   # an audit failure must not hide a write's result
            self.audit_failures += 1
            log.exception("execution audit callback failed for %s %s", fields.get("method"), fields.get("path"))

    def _attempt(self, method: str, endpoint: str, kind: str, *, params: dict | None, body: Any,
                 attempt: int, client_order_id: str | None) -> _Reply:
        """Send one signed (or public) request and audit it.

        Validation errors (allowlist, not-live, no key) propagate as they are. A network error from the transport is
        audited and raised as :class:`_SendFailed`; any other exception from the transport propagates unchanged.
        """
        if endpoint_kind(method, endpoint) != kind:                  # defense in depth: re-check the allowlist
            raise EndpointNotAllowed(f"{method} {endpoint!r} is not a {kind} endpoint")
        if str(method).upper() != "GET" and not self.live:           # second line of defense: the one send point
            raise PermissionError(f"{method} {endpoint}: a write was attempted on a client that is not live")
        path = sign_path(self.env.rest_base, endpoint)
        url = self.env.rest_base + endpoint
        u = urlparse(url)
        if u.scheme != "https" or u.netloc != self._host:
            raise EndpointNotAllowed("refusing a request outside the allowlisted host")
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":"))
            headers["Content-Type"] = "application/json"
        if kind != "public":
            if not self.env.has_keys:
                raise AuthError(f"{method} {path} needs an API key; none is loaded (watch-only)", path=path)
            headers.update(auth_headers(self.env.key, self.env.key_id, method, path))
        sent_at = float(self._clock())
        t0 = time.monotonic()
        base = dict(env=self.env.name, live=self.live, ts_ms=int(sent_at * 1000), method=method, path=path,
                    params=dict(params) if params else None, body=body, client_order_id=client_order_id,
                    attempt=attempt, kind=kind)
        try:
            resp = self._transport.send(method, url, params=params, data=data, headers=headers,
                                        timeout=self.timeout_s)
        except (requests.RequestException, OSError) as exc:
            self._record(**base, status=None, response=None,
                         latency_ms=round((time.monotonic() - t0) * 1000, 3),
                         error=type(exc).__name__, outcome="unknown")
            raise _SendFailed(exc) from exc
        received_at = float(self._clock())
        latency_ms = round((time.monotonic() - t0) * 1000, 3)
        status = int(resp.status_code)
        text = _reply_text(resp)
        self._record(**base, status=status, response=text[:RESPONSE_AUDIT_LIMIT], latency_ms=latency_ms,
                     error=None if 200 <= status < 300 else (parse_error(text).get("code") or f"http_{status}"),
                     outcome=classify(status))
        return _Reply(status, text, latency_ms, _reply_headers(resp), sent_at, received_at)

    def _get_reply(self, endpoint: str, params: Mapping[str, Any] | None = None) -> _Reply:
        """GET with the read retry policy; returns the first 2xx reply, raises :class:`ExecError` otherwise."""
        kind = endpoint_kind("GET", endpoint)
        if kind == "write":
            raise EndpointNotAllowed("GET on a write endpoint")
        clean = _clean_params(params)
        path = sign_path(self.env.rest_base, endpoint)
        attempt = 0
        while True:
            attempt += 1
            retry_left = attempt <= len(READ_BACKOFF_S)
            try:
                reply = self._attempt("GET", endpoint, kind, params=clean, body=None,
                                      attempt=attempt, client_order_id=None)
            except _SendFailed as failed:
                exc = failed.cause
                if retry_left:
                    self._sleep(READ_BACKOFF_S[attempt - 1])
                    continue
                raise TransportError(f"GET {path}: {type(exc).__name__} after {attempt} attempts",
                                     path=path) from exc
            status, text = reply.status, reply.text
            if 200 <= status < 300:
                return reply
            err = parse_error(text)
            if 300 <= status < 400:
                raise RedirectError(f"GET {path}: HTTP {status} redirect not followed", status=status,
                                    error=err, path=path)
            if status in (401, 403):
                raise AuthError(f"GET {path}: HTTP {status} {err.get('code') or ''}".rstrip(), status=status,
                                error=err, path=path)
            if status in RETRYABLE_READ_STATUS or status >= 500:
                if retry_left:
                    self._sleep(READ_BACKOFF_S[attempt - 1])
                    continue
                raise ApiError(f"GET {path}: HTTP {status} after {attempt} attempts", status=status,
                               error=err, path=path)
            raise ApiError(f"GET {path}: HTTP {status} {err.get('code') or ''}".rstrip(), status=status,
                           error=err, path=path)

    def _get_json(self, endpoint: str, params: Mapping[str, Any] | None = None) -> tuple[Any, _Reply]:
        """GET and parse the JSON body (a 2xx with a non-JSON body is an :class:`ApiError`, not retried)."""
        reply = self._get_reply(endpoint, params)
        try:
            return json.loads(reply.text), reply
        except ValueError:
            path = sign_path(self.env.rest_base, endpoint)
            raise ApiError(f"GET {path}: HTTP {reply.status} with a non-JSON body", status=reply.status,
                           error=parse_error(reply.text), path=path) from None

    def _get(self, endpoint: str, params: Mapping[str, Any] | None = None) -> Any:
        return self._get_json(endpoint, params)[0]

    def _paginate(self, endpoint: str, params: Mapping[str, Any], items_key: str,
                  extra_keys: tuple[str, ...] = ()) -> Listing:
        q = dict(params)
        caller_cursor = "cursor" in q
        items: list[Any] = []
        extras: dict[str, list[Any]] = {k: [] for k in extra_keys}
        for _ in range(MAX_PAGES):
            data = self._get(endpoint, q)
            if not isinstance(data, dict) or not isinstance(data.get(items_key, []), (list, type(None))):
                raise ApiError(f"GET {endpoint}: unexpected response shape", path=endpoint)
            items.extend(data.get(items_key) or [])
            for k in extra_keys:
                extras[k].extend(data.get(k) or [])
            cursor = data.get("cursor")
            if caller_cursor or not cursor:
                raw = {items_key: items, **extras, "cursor": cursor or ""}
                return Listing(items, raw)
            q["cursor"] = cursor
        raise ApiError(f"GET {endpoint}: more than {MAX_PAGES} pages; refusing to continue", path=endpoint)

    @staticmethod
    def _unwrap(data: Any, key: str, path: str) -> dict:
        inner = data.get(key) if isinstance(data, dict) else None
        if not isinstance(inner, dict):
            raise ApiError(f"GET {path}: response has no '{key}' object", path=path)
        return inner

    # ------------------------------------------------------------------ public reads (unsigned)
    def exchange_status(self) -> dict:
        """GET /exchange/status. Use :func:`shard_trading_active` for the market's own shard."""
        return self._get("/exchange/status")

    def user_data_timestamp(self) -> dict:
        """GET /exchange/user_data_timestamp: ``as_of_time`` = how fresh REST portfolio data is."""
        return self._get("/exchange/user_data_timestamp")

    def historical_cutoff(self) -> dict:
        """GET /historical/cutoff: older orders/fills only exist under /historical/."""
        return self._get("/historical/cutoff")

    def series(self, series_ticker: str) -> dict:
        """GET /series/{t} -> the series object (exchange_index, fee_type, fee_multiplier, ...)."""
        t = _segment(series_ticker, "series ticker")
        return self._unwrap(self._get(f"/series/{t}"), "series", f"/series/{t}")

    def open_markets(self, series_ticker: str) -> Listing:
        """GET /markets?series_ticker=&status=open (all pages) -> market objects.

        Server-cached for up to ~15 s, so never price orders from these quotes. Market objects report
        ``status == "active"``; a just-closed or stale market can still appear, so callers must check
        ``status`` and ``close_time`` themselves.

        Only these three parameters are ever sent. Kalshi's OpenAPI (GetMarkets) allows ``min_close_ts`` and
        ``max_close_ts`` only with status ``closed`` or no status, so this method takes no close-time filter: a caller
        that wants one market matches its ticker in the listing.
        """
        params: dict[str, Any] = {"series_ticker": _segment(series_ticker, "series ticker"), "status": "open",
                                  "limit": 1000}
        return self._paginate("/markets", params, "markets")

    def market(self, ticker: str) -> dict:
        """GET /markets/{t} -> the market object (status, close_time, exchange_index, price_ranges, ...)."""
        t = _segment(ticker, "ticker")
        return self._unwrap(self._get(f"/markets/{t}"), "market", f"/markets/{t}")

    def orderbook(self, ticker: str, depth: int | None = None) -> dict:
        """GET /markets/{t}/orderbook (never cached) -> parsed bids, ascending, best LAST.

        A nonexistent ticker returns HTTP 200 with empty arrays, so an empty book proves nothing.
        """
        t = _segment(ticker, "ticker")
        params = None
        if depth is not None:
            if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 100:
                raise ValueError("depth must be an int in 1..100 (omit it for the full book)")
            params = {"depth": depth}
        data, reply = self._get_json(f"/markets/{t}/orderbook", params)
        fetched_at = reply.received_at                       # client clock when the response arrived
        ob = data.get("orderbook_fp") if isinstance(data, dict) else None
        if not isinstance(ob, dict):
            raise ApiError(f"GET /markets/{t}/orderbook: no orderbook_fp in the response", path=t)
        try:
            yes = units.parse_levels(ob.get("yes_dollars") or [])
            no = units.parse_levels(ob.get("no_dollars") or [])
        except (TypeError, ValueError) as exc:
            raise ApiError(f"GET /markets/{t}/orderbook: unusable levels ({exc})", path=t) from None
        return {"ticker": t, "yes": yes, "no": no, "fetched_at": fetched_at}

    def clock_probe(self, probes: int = CLOCK_PROBES, spacing_s: float = CLOCK_PROBE_SPACING_S) -> ClockProbe:
        """Probe the exchange clock: ``probes`` (default 3) public GETs of ``/exchange/status``, ``spacing_s`` apart.

        Each probe compares the local clock (midpoint of the round trip) with the response's ``Date`` header, centred
        on its 1 s resolution; the result is the median of the usable probes, as ``local - server`` seconds (positive:
        this PC runs ahead). A probe whose response has no usable ``Date`` header, and a probe that fails after the
        read retries, is dropped; with fewer than 2 usable probes there is no answer and :class:`ApiError` is raised
        (a caller gating live trading on the clock must treat that as a failed gate).
        """
        if isinstance(probes, bool) or not isinstance(probes, int) or not 1 <= probes <= 9:
            raise ValueError("probes must be an int in 1..9")
        if isinstance(spacing_s, bool) or not isinstance(spacing_s, (int, float)) or not 0 <= spacing_s <= 30:
            raise ValueError("spacing_s must be a number in 0..30")
        samples: list[ClockSample] = []
        problems: list[str] = []
        for i in range(probes):
            if i:
                self._sleep(float(spacing_s))
            try:
                reply = self._get_reply(CLOCK_ENDPOINT)
            except ExecError as exc:
                problems.append(type(exc).__name__)
                continue
            sample = _clock_sample(reply)
            if sample is None:
                problems.append("no usable Date header")
            else:
                samples.append(sample)
        if len(samples) < min(CLOCK_MIN_SAMPLES, probes):
            raise ApiError(f"clock skew unavailable: {len(samples)} of {probes} probes usable "
                           f"({', '.join(sorted(set(problems))) or 'no probes'})", path=CLOCK_ENDPOINT)
        return ClockProbe(float(statistics.median(s.skew_s for s in samples)), tuple(samples))

    def server_time_skew(self, probes: int = CLOCK_PROBES, spacing_s: float = CLOCK_PROBE_SPACING_S) -> float:
        """Median of 3 Date-header probes: ``local clock - Kalshi clock`` in seconds (see :meth:`clock_probe`).

        Positive means this PC runs ahead of the exchange. The live gate wants ``abs(skew) <= 2.0``. Public
        endpoint, so it works in watch-only mode without keys. Raises :class:`ApiError` when it cannot tell.
        """
        return self.clock_probe(probes, spacing_s).skew_s

    # ------------------------------------------------------------------ authenticated reads (signed)
    def account_limits(self) -> dict:
        """GET /account/limits: usage_tier, read/write {refill_rate, bucket_capacity}, grants."""
        return self._get("/account/limits")

    def balance(self, exchange_index: int | None) -> dict:
        """GET /portfolio/balance?exchange_index=N.

        Use ``balance_dollars`` (exact dollar string) of a shard-scoped call; the integer ``balance``
        is cents, truncated, and covers every shard when ``exchange_index`` is None.
        """
        params = None if exchange_index is None else {"exchange_index": _exchange_index(exchange_index)}
        return self._get("/portfolio/balance", params)

    def shard_cash(self, exchange_index: int) -> Decimal:
        """Exact cash on one shard: ``balance_dollars`` of a call scoped with ``?exchange_index``."""
        data = self.balance(_exchange_index(exchange_index))
        try:
            return units.dec(data["balance_dollars"])
        except (KeyError, TypeError, ValueError):
            raise ApiError("GET /portfolio/balance: no usable balance_dollars", path="/portfolio/balance") from None

    def positions(self, **kw: Any) -> Listing:
        """GET /portfolio/positions (all pages) -> market positions; ``["event_positions"]`` also available.

        ``position_fp`` is signed: + YES contracts, - NO contracts. Primary account only.
        """
        params = _check_read_params("positions", kw)
        return self._paginate("/portfolio/positions", params, "market_positions", ("event_positions",))

    def orders(self, **kw: Any) -> Listing:
        """GET /portfolio/orders (all pages). There is no client_order_id filter: query by ticker, match locally."""
        return self._paginate("/portfolio/orders", _check_read_params("orders", kw), "orders")

    def order(self, order_id: str) -> dict:
        """GET /portfolio/orders/{id} -> the Order object (no shard parameter; prefer orders(ticker=...))."""
        oid = _order_id(order_id)
        return self._unwrap(self._get(f"/portfolio/orders/{oid}"), "order", f"/portfolio/orders/{oid}")

    def queue_position(self, order_id: str) -> dict:
        """GET /portfolio/orders/{id}/queue_position -> {queue_position_fp}."""
        return self._get(f"/portfolio/orders/{_order_id(order_id)}/queue_position")

    def fills(self, **kw: Any) -> Listing:
        """GET /portfolio/fills (all pages). No client_order_id: join on order_id, dedupe on fill_id."""
        return self._paginate("/portfolio/fills", _check_read_params("fills", kw), "fills")

    def settlements(self, **kw: Any) -> Listing:
        """GET /portfolio/settlements (all pages). ``revenue``/``value`` are integer CENTS; persist locally."""
        return self._paginate("/portfolio/settlements", _check_read_params("settlements", kw), "settlements")

    # ------------------------------------------------------------------ writes (live only)
    def _write(self, method: str, endpoint: str, *, params: dict | None = None, body: Any = None,
               client_order_id: str | None = None,
               check: Callable[[Any], str | None] | None = None) -> Result:
        if endpoint_kind(method, endpoint) != "write":
            raise EndpointNotAllowed(f"{method} {endpoint} is not a write endpoint")
        if not self.env.has_keys:
            raise AuthError("writes need an API key; none is loaded")
        clean = _clean_params(params)
        attempts = 0
        t0 = time.monotonic()
        while True:
            attempts += 1
            try:
                reply = self._attempt(method, endpoint, "write", params=clean, body=body,
                                      attempt=attempts, client_order_id=client_order_id)
            except _SendFailed as failed:                       # timeout, connection error...: it may have been sent
                return Result("unknown", None, None,
                              _err("transport_error", type(failed.cause).__name__,
                                   "outcome unknown: reconcile, never resend", "exception"),
                              attempts, round((time.monotonic() - t0) * 1000, 3), client_order_id)
            status, text = reply.status, reply.text
            outcome = classify(status)
            if outcome == "throttled" and attempts <= len(WRITE_429_BACKOFF_S):
                self._sleep(WRITE_429_BACKOFF_S[attempts - 1])        # same body, same client_order_id
                continue
            latency = round((time.monotonic() - t0) * 1000, 3)
            data = _json_or_none(text)
            if outcome == "ok":
                problem = check(data) if check is not None else None
                if problem:
                    return Result("unknown", status, data,
                                  _err(problem, "unexpected write response; reconcile before acting"),
                                  attempts, latency, client_order_id)
                return Result("ok", status, data, None, attempts, latency, client_order_id)
            err = parse_error(text)
            if 300 <= status < 400:
                err = _err("redirect", f"HTTP {status} not followed", err.get("message"), "redirect")
            return Result(outcome, status, data, err, attempts, latency, client_order_id)

    def create_order(self, body: Mapping[str, Any]) -> Result:
        """POST /portfolio/events/orders (CreateOrderV2). ``body`` must carry a client_order_id that the
        ledger committed before this call. 201 -> ok with {order_id, fill_count, remaining_count, ts_ms}."""
        self._require_live("create_order")
        clean = validate_order_body(body)
        coid = clean["client_order_id"]
        return self._write("POST", "/portfolio/events/orders", body=clean, client_order_id=coid,
                           check=lambda d: _check_create_item(d, coid))

    def batch_create(self, bodies: Iterable[Mapping[str, Any]]) -> list[Result]:
        """POST /portfolio/events/orders/batched -> one Result per order, in request order.

        Each item is checked for its own ``error``. A whole-batch failure gives every item the same outcome.
        """
        self._require_live("batch_create")
        orders = [validate_order_body(b) for b in bodies]
        if not orders:
            raise ValueError("batch_create needs at least one order")
        if len(orders) > MAX_BATCH:
            raise ValueError(f"at most {MAX_BATCH} orders per batch")
        coids = [o["client_order_id"] for o in orders]
        if len(set(coids)) != len(coids):
            raise ValueError("client_order_ids in a batch must be unique")
        res = self._write("POST", "/portfolio/events/orders/batched", body={"orders": orders},
                          client_order_id=None, check=lambda d: _check_batch(d, len(orders)))
        if res.outcome != "ok":
            return [replace(res, client_order_id=c) for c in coids]
        items = res.data["orders"]
        by_coid = {it.get("client_order_id"): it for it in items
                   if isinstance(it, dict) and it.get("client_order_id")}
        out: list[Result] = []
        for i, coid in enumerate(coids):
            item = by_coid.get(coid)
            if item is None and isinstance(items[i], dict) and not items[i].get("client_order_id"):
                item = items[i]                                   # positional fallback only for an id-less item
            if item is None:
                out.append(Result("unknown", res.status, None,
                                  _err("batch_item_missing", "no response item for this client_order_id"),
                                  res.attempts, res.latency_ms, coid))
                continue
            if item.get("error"):
                perr = parse_error({"error": item["error"]})
                out.append(Result(_item_outcome(perr), res.status, item, perr, res.attempts, res.latency_ms, coid))
                continue
            problem = _check_create_item(item, coid)
            if problem:
                out.append(Result("unknown", res.status, item, _err(problem, "unexpected batch item; reconcile"),
                                  res.attempts, res.latency_ms, coid))
            else:
                out.append(Result("ok", res.status, item, None, res.attempts, res.latency_ms, coid))
        return out

    def cancel_order(self, order_id: str, exchange_index: int) -> Result:
        """DELETE /portfolio/events/orders/{id}?exchange_index=N (the order's own shard; never market_ticker).

        Without an index the cancel would go to shard 0 and miss a shard-2 (KXBTC15M/KXBTCD) order. A 404
        does not prove the order is gone, and a response that names another order is ``unknown``:
        reconcile with ``orders(ticker=...)`` in both cases.
        """
        self._require_live("cancel_order")
        oid = _order_id(order_id)
        ei = _exchange_index(exchange_index)
        return self._write("DELETE", f"/portfolio/events/orders/{oid}", params={"exchange_index": ei},
                           check=lambda d: _check_echo(d, oid, "reduced_by"))

    def batch_cancel(self, items: Iterable[Mapping[str, Any]]) -> list[Result]:
        """DELETE /portfolio/events/orders/batched with {orders: [{order_id, exchange_index}]}."""
        self._require_live("batch_cancel")
        orders = [{"order_id": _order_id(it["order_id"]), "exchange_index": _exchange_index(it["exchange_index"])}
                  for it in items]
        if not orders:
            raise ValueError("batch_cancel needs at least one order")
        if len(orders) > 50:
            raise ValueError("at most 50 cancels per batch")
        res = self._write("DELETE", "/portfolio/events/orders/batched", body={"orders": orders},
                          check=lambda d: _check_batch(d, len(orders)))
        if res.outcome != "ok":
            return [res for _ in orders]
        by_oid = {it.get("order_id"): it for it in res.data["orders"] if isinstance(it, dict)}
        out: list[Result] = []
        for o in orders:
            item = by_oid.get(o["order_id"])
            if item is None:
                out.append(Result("unknown", res.status, None,
                                  _err("batch_item_missing", "no response item for this order_id"),
                                  res.attempts, res.latency_ms))
            elif item.get("error"):
                perr = parse_error({"error": item["error"]})
                out.append(Result(_item_outcome(perr), res.status, item, perr, res.attempts, res.latency_ms))
            else:
                out.append(Result("ok", res.status, item, None, res.attempts, res.latency_ms))
        return out

    def amend_order(self, order_id: str, body: Mapping[str, Any]) -> Result:
        """POST /portfolio/events/orders/{id}/amend. Amend has no post_only and can fill immediately."""
        self._require_live("amend_order")
        oid = _order_id(order_id)
        clean = validate_amend_body(body)
        return self._write("POST", f"/portfolio/events/orders/{oid}/amend", body=clean,
                           client_order_id=clean.get("client_order_id"),
                           check=lambda d: _check_echo(d, oid, None))

    def decrease_order(self, order_id: str, exchange_index: int, *, reduce_by: Decimal | str | None = None,
                       reduce_to: Decimal | str | None = None) -> Result:
        """POST /portfolio/events/orders/{id}/decrease with exactly one of reduce_by / reduce_to."""
        self._require_live("decrease_order")
        oid = _order_id(order_id)
        ei = _exchange_index(exchange_index)
        if (reduce_by is None) == (reduce_to is None):
            raise ValueError("pass exactly one of reduce_by or reduce_to")
        body: dict[str, Any] = {"exchange_index": ei}
        if reduce_by is not None:
            body["reduce_by"] = _count_param(reduce_by, "reduce_by")
        else:
            body["reduce_to"] = _count_param(reduce_to, "reduce_to", allow_zero=True)
        return self._write("POST", f"/portfolio/events/orders/{oid}/decrease", body=body,
                           check=lambda d: _check_echo(d, oid, "remaining_count"))

    def cancel_all(self) -> Result:
        """DELETE /portfolio/events/orders -> 204. Cancels EVERY resting order on the account, on all shards,
        including manual ones; orders placed in the next minute may also be cancelled. Last-resort kill only."""
        self._require_live("cancel_all")
        return self._write("DELETE", "/portfolio/events/orders")


# ============================================================================================ response checks
def _check_create_item(d: Any, client_order_id: str) -> str | None:
    if not isinstance(d, dict) or not isinstance(d.get("order_id"), str) or not d["order_id"]:
        return "missing_order_id"
    for k in ("fill_count", "remaining_count"):
        try:
            units.dec(d.get(k))
        except (TypeError, ValueError):
            return f"bad_{k}"
    echoed = d.get("client_order_id")
    if echoed and echoed != client_order_id:
        return "client_order_id_mismatch"
    return None


def _check_echo(d: Any, order_id: str, count_key: str | None) -> str | None:
    """Cancel/amend/decrease responses must describe the requested order (known issue 2026-05-21)."""
    if not isinstance(d, dict) or not d.get("order_id"):
        return "missing_order_id"
    if d["order_id"] != order_id:
        return "order_id_mismatch"
    if count_key is not None:
        try:
            units.dec(d.get(count_key))
        except (TypeError, ValueError):
            return f"bad_{count_key}"
    return None


def _check_batch(d: Any, n: int) -> str | None:
    if not isinstance(d, dict) or not isinstance(d.get("orders"), list):
        return "batch_response_shape"
    if len(d["orders"]) != n:
        return "batch_response_count"
    return None
