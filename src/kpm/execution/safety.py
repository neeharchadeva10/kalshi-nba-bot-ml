"""Safety layer for live execution (docs/execution_design.md section 5, adapted to the user's decisions).

Nothing here sends an order. Strategies call :meth:`Safety.check_entry` / :meth:`Safety.check_exit` before they create a
ledger intent; a refusal is ``(False, reason)`` and the order must not be sent. Every check fails closed: an exception,
a missing field or an unreadable response is a refusal, never a pass.

USER DECISIONS ENFORCED HERE (hard-coded ceilings in :data:`CEILINGS`; a configuration above one refuses to start)
  * ONE account-wide loss cap, at most $5.00: realized loss + open worst case + the worst case of the new order. The
    realized part is the larger of the 24 h and the all-time net realized loss, so a restart never resets the cap.
  * At most 1 contract and at most $1.00 of collateral per order, at most $3.00 of open collateral exposure.
  * Only the KXBTC15M and KXBTCD series (:data:`ALLOWED_SERIES`).
  * Only immediate_or_cancel taker orders. An entry is priced EXACTLY at the current ask of the outcome it buys, from
    an order book younger than ``max_book_age_s``. An exit is a ``reduce_only`` IOC at most 2 ticks through the touch.
  * Live gates, ALL required: ``trading.enabled`` true, the ``--live`` flag, KALSHI_ENV=prod, the ``--env prod`` flag,
    a typed confirmation ("LIVE PROD 5") at an interactive terminal, no kill file, and a clock within 2 s of Kalshi's.

CHECKS BEFORE EVERY ORDER (cheap local checks first, network reads cached briefly, the book's age measured last)
  state (started, live, not halted) -> kill file -> lockout after a cancel-all -> rate limits -> the order itself
  (IOC only, ``units.to_book`` consistency, count, price bounds 0.001-0.999 on the exact market grid, collateral) -> Thursday 02:55-05:05 ET
  maintenance -> ledger (unknown outcomes, one in-flight order per ticker and side, loss cap, exposure cap) ->
  market (listed as open in an allowed series, active, shard trading, time to close, exact price grid) -> account
  (cash on the market's own shard, position and resting orders match the ledger) -> order book (fresh, not crossed,
  priced at the touch) -> the halted flag, kill file and lockout once more, because the reads took time.

Other behavior worth knowing
  * ``trip(reason)`` halts every order (entries and exits) and writes the kill file, so every process stops and a new
    run must be started by hand. ``Ledger.halt()`` (called by reconcile on a real mismatch) calls it. A 401/403 on any
    authenticated read of a check also trips (design section 2: on 401, stop; never loop).
  * The PC clock is measured against Kalshi's at startup and again during the run (``poll_clock()``, which the bots call
    while they idle, every ``clock_recheck_s`` seconds). Every order check uses the latest measurement and refuses when
    it is outside ``max_clock_skew_s`` or older than ``2 * clock_recheck_s + 60`` s (it then measures once, inline).
  * The legs of one pair are checked TOGETHER: ``check_entry(..., batch=specs)`` counts every leg of the batch in the
    rate limits, the loss and exposure caps and the cash check, so two legs that each fit alone cannot add up past a cap.
  * When a loss or exposure cap would refuse an entry, old unsettled positions are first settled from the exchange's
    settlement rows (``reconcile.sweep_settlements``): a settled position must not keep counting against the caps.
  * ``preflight(market)`` warms the cached reads before a window opens; ``diagnostics()`` is the read-only health report
    for ``kpm exec check``; ``startup()`` runs every live gate and raises :class:`LiveGateError` if one fails.

Never logged or returned: the key id, the key path, signatures. The banner shows ``key_fp`` only.
"""
from __future__ import annotations

import inspect
import logging
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Sequence

from kpm.core.config import PROJECT_ROOT
from kpm.core.timeutil import ET
from kpm.execution import units
from kpm.execution.client import AuthError

log = logging.getLogger(__name__)

COMPONENT = "exec.safety"
D0, D1 = Decimal("0"), Decimal("1")
CENT = Decimal("0.01")
TIF_IOC = "immediate_or_cancel"
DEFAULT_KILL_FILE = "data/KILL"

ALLOWED_SERIES = ("KXBTC15M", "KXBTCD")          # code ceiling: configuration may only narrow this list

# Hard ceilings (user decisions, 2026-10-05). Raising one is a code change plus a test change, never a config edit.
CEILINGS: Mapping[str, Any] = MappingProxyType({
    "max_loss_usd": Decimal("5.00"),              # account-wide: realized + open worst case
    "max_cost_per_order_usd": Decimal("1.00"),    # collateral of one order
    "max_contracts_per_order": Decimal("1"),
    "max_open_exposure_usd": Decimal("3.00"),     # collateral at risk on unsettled positions and unfinished orders
    "max_orders_per_minute": 10,
    "max_orders_per_hour": 120,
    "max_book_age_s": 5.0,
    "max_clock_skew_s": 2.0,
    "clock_recheck_s": 900.0,                     # the PC clock is re-measured at least this often during a run
    "exit_max_ticks": 2,
    "price_max": Decimal("0.999"),               # user decision 2026-10-06: take 99-99.9c favorites (exact grid only)
    "status_ttl_s": 30.0,
    "markets_ttl_s": 30.0,
    "account_ttl_s": 30.0,
})
# Hard floors: values that may not be set lower.
FLOORS: Mapping[str, Any] = MappingProxyType({
    "min_seconds_to_close": 20.0,
    "exit_min_seconds_to_close": 2.0,
    "price_min": Decimal("0.001"),               # mirror of price_max: a NO buy at 99.9c is YES 0.001
    "cash_buffer_usd": Decimal("0.05"),
    "clock_recheck_s": 30.0,                      # (each measurement is 3 public probes, about 2.2 s)
    "lockout_s": 60.0,
    "sweep_after_s": 30.0,                        # (config keys settle_sweep_after_s / settle_sweep_retry_s)
    "sweep_retry_s": 60.0,
})

# Defaults of the time-to-close rule: an ENTRY is refused with less than min_seconds_to_close (+ early_close_buffer_s when the
# market can close early, which KXBTC15M and KXBTCD both can) left. config/settings.yaml sets min_seconds_to_close to 20.
DEFAULT_MIN_SECONDS_TO_CLOSE = 30.0
DEFAULT_EARLY_CLOSE_BUFFER_S = 10.0

MAINTENANCE_WEEKDAY = 3                           # Thursday (Monday = 0)
MAINTENANCE_START_MIN = 2 * 60 + 55               # 02:55 ET
MAINTENANCE_END_MIN = 5 * 60 + 5                  # 05:05 ET, through the end of that minute
POSITION_TOLERANCE = Decimal("0.005")


# ============================================================================================ errors and results
class SafetyError(RuntimeError):
    """Base class for safety-layer failures."""


class SafetyConfigError(SafetyError, ValueError):
    """The configuration is above a hard ceiling (or below a hard floor) or malformed: refuse to start."""


class LiveGateError(SafetyError, PermissionError):
    """A live gate failed. ``gates`` lists every gate with its verdict."""

    def __init__(self, gates: Sequence["Gate"]):
        self.gates = list(gates)
        failed = [g for g in self.gates if not g.ok]
        super().__init__("live mode refused: " + "; ".join(f"{g.name}: {g.detail}" for g in failed))


@dataclass(frozen=True)
class Gate:
    """One startup gate: its name, the verdict and a human-readable detail (never a secret)."""

    name: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


class _Refuse(Exception):
    """Internal: a check failed; the message is the refusal reason."""


def plain_str(v: Any) -> Any:
    """Decimal -> plain string (no exponent); everything else unchanged. For JSON-safe banners and messages."""
    return format(v, "f") if isinstance(v, Decimal) else v


# ============================================================================================ configuration
@dataclass(frozen=True)
class Limits:
    """The parsed ``trading`` configuration. Every value is inside the ceilings and floors."""

    max_loss_usd: Decimal
    max_cost_per_order_usd: Decimal
    max_contracts_per_order: Decimal
    max_open_exposure_usd: Decimal
    max_orders_per_minute: int
    max_orders_per_hour: int
    max_orders_per_session: int | None   # order intents of this run (this process); None = no session cap
    max_book_age_s: float
    max_clock_skew_s: float
    clock_recheck_s: float
    min_seconds_to_close: float
    early_close_buffer_s: float
    exit_min_seconds_to_close: float
    exit_max_ticks: int
    price_min: Decimal
    price_max: Decimal
    cash_buffer_usd: Decimal
    status_ttl_s: float
    markets_ttl_s: float
    account_ttl_s: float
    lockout_s: float
    sweep_after_s: float
    sweep_retry_s: float
    allowed_series: tuple[str, ...]
    kill_switch_file: str

    @property
    def clock_max_age_s(self) -> float:
        """The oldest clock-skew measurement an order check accepts: two re-check intervals and a minute."""
        return 2.0 * self.clock_recheck_s + 60.0

    @property
    def entry_cutoff_s(self) -> float:
        """Seconds before a market's close at which an ENTRY is refused: ``min_seconds_to_close`` plus the early-close
        buffer (KXBTC15M and KXBTCD both report can_close_early). A strategy's trading window must end before this."""
        return self.min_seconds_to_close + self.early_close_buffer_s

    def as_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else plain_str(v)) for k, v in self.__dict__.items()}


def _money(v: Any, name: str) -> Decimal:
    """A configuration number as an exact Decimal. YAML floats go through their shortest repr (5.0 -> '5.0')."""
    if v is None or isinstance(v, bool):
        raise SafetyConfigError(f"trading.{name} must be a number")
    try:
        if isinstance(v, float):
            if not math.isfinite(v):
                raise ValueError("not finite")
            return Decimal(repr(v))
        if isinstance(v, Decimal):
            out = v
        elif isinstance(v, int):
            out = Decimal(v)
        elif isinstance(v, str):
            out = Decimal(v.strip())
        else:
            raise TypeError(type(v).__name__)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise SafetyConfigError(f"trading.{name} must be a number, got {str(v)[:40]!r} ({exc})") from None
    if not out.is_finite():
        raise SafetyConfigError(f"trading.{name} must be finite")
    return out


def _whole(v: Any, name: str) -> int:
    d = _money(v, name)
    if d != d.to_integral_value():
        raise SafetyConfigError(f"trading.{name} must be a whole number, got {d}")
    return int(d)


def parse_limits(cfg: Mapping[str, Any] | None) -> Limits:
    """Validate the ``trading`` block against the hard ceilings and floors; raise :class:`SafetyConfigError`.

    Missing keys take the user's decisions as defaults, so a minimal block is valid. ``base_url``, ``ws_url``,
    ``late_favorite`` and ``pair`` are ignored here (hosts come from kpm.execution.env, strategies read their own
    sub-blocks). ``max_orders_per_session`` is optional: the order intents one run may create (None = no session cap).
    """
    cfg = dict(cfg or {})

    def pick(name: str, default: Any, conv: Callable[[Any, str], Any]) -> Any:
        return conv(cfg.get(name, default), name)

    vals: dict[str, Any] = {
        "max_loss_usd": pick("max_loss_usd", CEILINGS["max_loss_usd"], _money),
        "max_cost_per_order_usd": pick("max_cost_per_order_usd", CEILINGS["max_cost_per_order_usd"], _money),
        "max_contracts_per_order": pick("max_contracts_per_order", CEILINGS["max_contracts_per_order"], _money),
        "max_open_exposure_usd": pick("max_open_exposure_usd", CEILINGS["max_open_exposure_usd"], _money),
        "max_orders_per_minute": pick("max_orders_per_minute", CEILINGS["max_orders_per_minute"], _whole),
        "max_orders_per_hour": pick("max_orders_per_hour", 60, _whole),
        "max_orders_per_session": None if cfg.get("max_orders_per_session") is None
        else _whole(cfg["max_orders_per_session"], "max_orders_per_session"),
        "max_book_age_s": float(pick("max_book_age_s", 2.0, _money)),
        "max_clock_skew_s": float(pick("max_clock_skew_s", CEILINGS["max_clock_skew_s"], _money)),
        "clock_recheck_s": float(pick("clock_recheck_s", 600.0, _money)),
        "min_seconds_to_close": float(pick("min_seconds_to_close", DEFAULT_MIN_SECONDS_TO_CLOSE, _money)),
        "early_close_buffer_s": float(pick("early_close_buffer_s", DEFAULT_EARLY_CLOSE_BUFFER_S, _money)),
        "exit_min_seconds_to_close": float(pick("exit_min_seconds_to_close", 5.0, _money)),
        "exit_max_ticks": pick("exit_max_ticks", CEILINGS["exit_max_ticks"], _whole),
        "price_min": pick("price_min", FLOORS["price_min"], _money),
        "price_max": pick("price_max", CEILINGS["price_max"], _money),
        "cash_buffer_usd": pick("cash_buffer_usd", FLOORS["cash_buffer_usd"], _money),
        "status_ttl_s": float(pick("status_ttl_s", 5.0, _money)),
        "markets_ttl_s": float(pick("markets_ttl_s", 15.0, _money)),
        "account_ttl_s": float(pick("account_ttl_s", 10.0, _money)),
        "lockout_s": float(pick("lockout_s", FLOORS["lockout_s"], _money)),
        "sweep_after_s": float(pick("settle_sweep_after_s", 120.0, _money)),
        "sweep_retry_s": float(pick("settle_sweep_retry_s", 120.0, _money)),
    }
    problems: list[str] = []
    for key, ceiling in CEILINGS.items():
        if key in vals and vals[key] > ceiling:
            problems.append(f"{key}={plain_str(vals[key])} is above the hard ceiling {plain_str(ceiling)}")
    for key, floor in FLOORS.items():
        if key in vals and vals[key] < floor:
            problems.append(f"{key}={plain_str(vals[key])} is below the hard floor {plain_str(floor)}")
    for key in ("max_loss_usd", "max_cost_per_order_usd", "max_contracts_per_order", "max_open_exposure_usd",
                "max_orders_per_minute", "max_orders_per_hour", "max_book_age_s", "max_clock_skew_s",
                "clock_recheck_s", "exit_max_ticks", "status_ttl_s", "markets_ttl_s", "account_ttl_s"):
        if vals[key] <= 0:
            problems.append(f"{key} must be > 0")
    for key in ("early_close_buffer_s",):
        if vals[key] < 0:
            problems.append(f"{key} must be >= 0")
    if vals["max_orders_per_session"] is not None and vals["max_orders_per_session"] <= 0:
        problems.append("max_orders_per_session must be > 0 (or left out for no session cap)")
    if vals["max_contracts_per_order"] != vals["max_contracts_per_order"].quantize(CENT):
        problems.append("max_contracts_per_order must be a multiple of 0.01")
    if not (D0 < vals["price_min"] < vals["price_max"] < D1):
        problems.append("price_min and price_max must satisfy 0 < price_min < price_max < 1")

    series = cfg.get("allowed_series", list(ALLOWED_SERIES))
    if isinstance(series, str) or not isinstance(series, (list, tuple)) or not series \
            or not all(isinstance(s, str) for s in series):
        problems.append("allowed_series must be a non-empty list of series tickers")
        series = list(ALLOWED_SERIES)
    outside = sorted(set(series) - set(ALLOWED_SERIES))
    if outside:
        problems.append(f"allowed_series {outside} is outside the code whitelist {list(ALLOWED_SERIES)}")
    kill = cfg.get("kill_switch_file") or DEFAULT_KILL_FILE
    if not isinstance(kill, (str, Path)):
        problems.append("kill_switch_file must be a path")
        kill = DEFAULT_KILL_FILE
    if problems:
        raise SafetyConfigError("refusing to start: " + "; ".join(problems))
    return Limits(allowed_series=tuple(dict.fromkeys(series)), kill_switch_file=str(kill), **vals)


def entry_cutoff_s(cfg: Mapping[str, Any] | None) -> float:
    """Seconds before a market's close at which Safety stops accepting ENTRIES, for the ``trading`` block ``cfg``:
    ``min_seconds_to_close`` plus ``early_close_buffer_s``. A strategy's trading window must end before this, or the
    detections it makes in the last seconds could never be traded (they would only be paper twins)."""
    cfg = dict(cfg or {})
    return float(_money(cfg.get("min_seconds_to_close", DEFAULT_MIN_SECONDS_TO_CLOSE), "min_seconds_to_close")
                 + _money(cfg.get("early_close_buffer_s", DEFAULT_EARLY_CLOSE_BUFFER_S), "early_close_buffer_s"))


def kill_file_path(cfg: Mapping[str, Any] | None = None, override: str | Path | None = None) -> Path:
    """Absolute path of the kill-switch file (same rule as the dashboard: relative paths are under the project root)."""
    p = Path(str(override or (cfg or {}).get("kill_switch_file") or DEFAULT_KILL_FILE))
    return p if p.is_absolute() else Path(PROJECT_ROOT) / p


def write_kill_file(path: str | Path, reason: str) -> Path:
    """Create (or append to) the kill-switch file: ``<UTC timestamp> <reason>``. Deleting it does not resume a run."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = " ".join(str(reason).split())[:500]
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(f"{stamp} {line}\n")
    return p


# ============================================================================================ price grid helpers
def _bands(price_ranges: Iterable[Mapping[str, Any]] | None) -> list[tuple[Decimal, Decimal, Decimal]]:
    out = []
    for b in price_ranges or ():
        out.append((units.dec(b["start"]), units.dec(b["end"]), units.dec(b["step"])))
    return out


def next_tick(bands: Sequence[tuple[Decimal, Decimal, Decimal]], price: Decimal, up: bool) -> Decimal | None:
    """The next grid price above (``up``) or below ``price``; None if ``price`` is off the grid or at its end.

    A band edge uses the step of the band being entered: from 0.9000 upward the step is that of [0.9, 1.0].
    """
    for start, end, step in bands:
        if step <= 0:
            continue
        nxt = None
        if up and start <= price < end and (price - start) % step == 0:
            nxt = price + step
        elif not up and start < price <= end and (price - start) % step == 0:
            nxt = price - step
        if nxt is not None:
            return nxt if D0 < nxt < D1 else None                # $0 and $1 are not tradable prices
    return None


def ticks_from_touch(bands: Sequence[tuple[Decimal, Decimal, Decimal]], touch: Decimal, target: Decimal, up: bool,
                     max_ticks: int) -> int | None:
    """Grid steps from ``touch`` to ``target`` walking up (higher YES price) or down; None if more than ``max_ticks``."""
    cur = touch
    for n in range(max_ticks + 1):
        if cur == target:
            return n
        nxt = next_tick(bands, cur, up)
        if nxt is None:
            return None
        cur = nxt
    return None


def in_maintenance(ts: float) -> bool:
    """True from Thursday 02:55 through 05:05 ET (Kalshi's trading pause is 03:00-05:00; DST follows America/New_York)."""
    dt = datetime.fromtimestamp(ts, tz=ET)
    if dt.weekday() != MAINTENANCE_WEEKDAY:
        return False
    minute = dt.hour * 60 + dt.minute
    return MAINTENANCE_START_MIN <= minute <= MAINTENANCE_END_MIN


def _shard_active(status: Any, exchange_index: int) -> bool:
    """True only if the shard's own entry exists and says exchange_active and trading_active (missing = closed)."""
    if not isinstance(status, Mapping):
        return False
    for e in status.get("exchange_index_statuses") or ():
        if isinstance(e, Mapping) and e.get("exchange_index") == exchange_index:
            return e.get("exchange_active") is True and e.get("trading_active") is True
    return False


def _close_ts(market: Mapping[str, Any]) -> float | None:
    s = market.get("close_time")
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        return float(units.parse_rfc3339(s))
    except (TypeError, ValueError):
        return None


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _dec(v: Any, what: str) -> Decimal:
    """Exact Decimal from a Decimal, int or decimal string; floats and bools are refused."""
    if isinstance(v, bool) or v is None:
        raise _Refuse(f"{what} is missing or a bool")
    if isinstance(v, float):
        raise _Refuse(f"{what} is a float; prices and counts must be Decimals or decimal strings")
    try:
        if isinstance(v, int):
            return Decimal(v)
        return units.dec(v.strip() if isinstance(v, str) else v)
    except (TypeError, ValueError):
        raise _Refuse(f"{what} is not a decimal ({str(v)[:30]!r})") from None


@dataclass(frozen=True)
class _Order:
    """A spec reduced to what the checks need. (side, yes_price) always agrees with units.to_book(outcome, price)."""

    kind: str
    ticker: str
    exchange_index: int
    outcome: str
    outcome_price: Decimal
    side: str
    yes_price: Decimal
    count: Decimal
    collateral: Decimal
    worst: Decimal                  # collateral + the worst-case fee


# ============================================================================================ Safety
class Safety:
    """Gates and per-order checks. One instance per process; see the module docstring for the rules.

    ``cfg_trading`` is the ``trading`` block of settings.yaml (or the whole settings dict). ``clock`` is epoch seconds
    and must be the same clock as the ledger's and the client's. ``kill_file`` overrides the configured path (tests).
    Attributes strategies read: ``started`` (startup() passed), ``live``, ``halted``, ``halt_reason``, ``limits``.
    """

    def __init__(self, cfg_trading: Mapping[str, Any] | None, ledger: Any, client: Any,
                 clock: Callable[[], float] = time.time, *, sleep: Callable[[float], Any] = time.sleep,
                 isatty: Callable[[], bool] | None = None, kill_file: str | Path | None = None) -> None:
        cfg = dict(cfg_trading or {})
        if isinstance(cfg.get("trading"), Mapping):
            cfg = dict(cfg["trading"])
        self.cfg = cfg
        self.limits = parse_limits(cfg)                      # raises SafetyConfigError above a ceiling
        self.enabled = cfg.get("enabled") is True
        self.ledger = ledger
        self.client = client
        self.clock = clock
        self.sleep = sleep
        self._isatty = isatty
        self.kill_path = kill_file_path(cfg, kill_file)
        self.started = False
        self.live = False
        self.halted = False
        self.halt_reason: str | None = None
        self.last_refusal: str | None = None
        self.refusals = 0
        self._cache: dict[tuple, tuple[float, Any]] = {}
        # the latest clock-skew measurement: skew (local - Kalshi, s), when it was taken, the last probe error and when
        # the next measurement is due (startup gate and poll_clock() write it; every order check reads it)
        self._clock_state: dict[str, Any] = {"skew": None, "at": None, "error": None, "next_try": 0.0}
        add = getattr(ledger, "add_halt_listener", None)
        if add is not None:
            add(self.trip)                                   # reconcile's real mismatches halt this session

    def __repr__(self) -> str:
        return f"Safety(started={self.started}, live={self.live}, halted={self.halted})"

    # ------------------------------------------------------------------------------------------ state
    @property
    def max_loss_usd(self) -> Decimal:
        return self.limits.max_loss_usd

    @property
    def confirmation_phrase(self) -> str:
        """What the user types to go live: ``LIVE PROD 5`` for a $5 cap."""
        cap = self.limits.max_loss_usd.normalize()
        return f"LIVE PROD {format(cap, 'f')}"

    def kill_active(self) -> bool:
        """True if the kill file exists. An unreadable path counts as active (fail closed)."""
        try:
            return self.kill_path.exists()
        except OSError:
            return True

    def lockout_until(self) -> float:
        fn = getattr(self.ledger, "lockout_until", None)
        return float(fn()) if fn is not None else 0.0

    def _risk(self) -> tuple[Decimal, Decimal, Decimal]:
        """(realized loss for the cap, open worst case, open exposure).

        The realized figure is the larger of the 24 h and the all-time net loss (never reset by a restart). One ledger
        pass (``risk_snapshot``) when the ledger offers it; the separate methods otherwise."""
        snap = getattr(self.ledger, "risk_snapshot", None)
        if snap is not None:
            s = snap()
            return (max(D0, s["realized_loss_24h"], s["realized_loss_total"]), s["open_worst_case"],
                    s["open_exposure"])
        vals = [D0]
        for name in ("realized_loss_24h", "realized_loss_total"):
            fn = getattr(self.ledger, name, None)
            if fn is not None:
                v = fn()
                vals.append(v if isinstance(v, Decimal) else _money(v, name))
        return max(vals), self.ledger.open_worst_case(), self.ledger.open_exposure()

    def realized_loss(self) -> Decimal:
        """Net realized loss for the cap: the larger of the 24 h and the all-time figure (never reset by a restart)."""
        return self._risk()[0]

    def loss_used(self) -> Decimal:
        """What the combined loss rule has already used: realized loss + the open worst case."""
        realized, worst, _exposure = self._risk()
        return realized + worst

    def trip(self, reason: str) -> None:
        """Halt this session: no more orders (entries or exits), and write the kill file so every process stops.

        Cancels still work: the caller runs reconcile.cancel_path. Idempotent; never raises.
        """
        first = not self.halted
        self.halted = True
        if first:
            self.halt_reason = str(reason)[:500]
        try:
            if first:
                write_kill_file(self.kill_path, f"safety trip: {reason}")
        except OSError:
            log.exception("could not write the kill file %s", self.kill_path)
        log_event = getattr(self.ledger, "log_event", None)
        if log_event is not None:
            log_event("trip", {"reason": str(reason)[:500], "first": first}, component=COMPONENT, entity_type="env",
                      entity_id=getattr(self.ledger, "env", None))
        log.error("safety tripped: %s", reason)

    def status(self) -> dict:
        """Numbers for heartbeats (never raises; a field is None when it cannot be read)."""
        def safe(fn: Callable[[], Any]) -> Any:
            try:
                v = fn()
                return plain_str(v) if isinstance(v, Decimal) else v
            except Exception:
                return None

        return {"started": self.started, "live": self.live, "halted": self.halted, "halt_reason": self.halt_reason,
                "kill_switch": self.kill_active(), "max_loss_usd": plain_str(self.limits.max_loss_usd),
                "loss_used_usd": safe(self.loss_used),
                "open_exposure_usd": safe(lambda: self.ledger.open_exposure()),
                "lockout_until": safe(self.lockout_until), "last_refusal": self.last_refusal,
                "refusals": self.refusals, "clock_skew_s": self.clock_skew()}

    # ------------------------------------------------------------------------------------------ the PC clock
    def clock_skew(self) -> float | None:
        """The latest measured skew in seconds (this PC minus Kalshi), or None before the first measurement."""
        return self._clock_state["skew"]

    def _measure_clock(self) -> tuple[float | None, str | None]:
        """One measurement: the median of 3 public probes (about 2.2 s). Returns ``(skew, None)`` or ``(None, reason)``
        and records the result; a failed probe keeps the previous reading (which then ages out)."""
        st = self._clock_state
        fn = getattr(self.client, "server_time_skew", None)
        skew: float | None = None
        err: str | None = None
        if fn is None:
            err = "the client has no server_time_skew()"
        else:
            try:
                skew = _skew_seconds(fn())
            except Exception as exc:
                err = f"clock skew probe failed ({type(exc).__name__})"
        now = self.clock()
        retry = min(60.0, self.limits.clock_recheck_s)         # after a failure or a bad reading, look again soon
        if skew is None:
            st["error"], st["next_try"] = err, now + retry
            return None, err
        st.update(skew=skew, at=now, error=None)
        st["next_try"] = now + (self.limits.clock_recheck_s if abs(skew) <= self.limits.max_clock_skew_s else retry)
        return skew, None

    def _clock_problem(self) -> str | None:
        """Why the latest measurement does not allow trading, or None. Reads the cached measurement only."""
        st = self._clock_state
        skew, at = st["skew"], st["at"]
        if skew is None or at is None:
            return st["error"] or "the PC clock has not been checked against Kalshi's yet"
        limit = self.limits.max_clock_skew_s
        if abs(skew) > limit:
            return f"clock skew {skew:+.2f} s vs Kalshi is over the {limit:g} s limit"
        age = self.clock() - at
        if age < -1.0:
            return "the PC clock moved backwards since the last skew measurement"
        if age > self.limits.clock_max_age_s:
            why = f" (last probe: {st['error']})" if st["error"] else ""
            return (f"the clock skew was last measured {age:.0f} s ago, more than the "
                    f"{self.limits.clock_max_age_s:.0f} s allowed{why}")
        return None

    def poll_clock(self, *, force: bool = False) -> str | None:
        """Keep the clock check alive during a run: measure the skew when the last reading is older than
        ``clock_recheck_s`` (or ``force``). Returns None while the clock is within ``max_clock_skew_s`` of Kalshi's, else
        the reason no order will pass. Network (3 public probes, about 2.2 s) only when a measurement is due. Never
        raises. The bots call this while they idle, so the order checks find a fresh reading and never probe inline."""
        try:
            st = self._clock_state
            now = self.clock()
            if force or now >= st["next_try"] or (st["at"] is not None and now < st["at"] - 1.0):
                self._measure_clock()
            return self._clock_problem()
        except Exception as exc:
            return f"clock check failed: {type(exc).__name__}"

    def _check_clock(self) -> None:
        """Per-order clock gate. Uses the cached measurement; measures once here (inline, before the book's age is taken)
        when there is none, it is older than ``clock_max_age_s``, the clock moved backwards, or a bad reading is due for
        a second look."""
        st = self._clock_state
        now = self.clock()
        stale = st["at"] is None or now < st["at"] - 1.0 or now - st["at"] > self.limits.clock_max_age_s
        if stale or (self._clock_problem() is not None and now >= st["next_try"]):
            self._measure_clock()
        problem = self._clock_problem()
        if problem is not None:
            raise _Refuse(problem)

    def banner(self) -> list[str]:
        """Lines for the console and the audit log. No key id, no key path, no signatures: ``key_fp`` only."""
        env = getattr(self.client, "env", None)
        lim = self.limits
        mode = "LIVE" if self.live else "WATCH-ONLY"
        lines = [f"mode {mode}",
                 f"environment {getattr(env, 'name', '?')}  host {getattr(env, 'rest_host', '?')}  "
                 f"key_fp {getattr(env, 'key_fp', None)}",
                 f"loss cap ${plain_str(lim.max_loss_usd)} account-wide (realized + open worst case)",
                 f"per order: at most {plain_str(lim.max_contracts_per_order)} contract, "
                 f"${plain_str(lim.max_cost_per_order_usd)} collateral; open exposure at most "
                 f"${plain_str(lim.max_open_exposure_usd)}",
                 f"orders: IOC only, at most {lim.max_orders_per_minute}/min and {lim.max_orders_per_hour}/h"
                 + ("" if lim.max_orders_per_session is None else f", {lim.max_orders_per_session} per run"),
                 f"series {', '.join(lim.allowed_series)}",
                 f"kill file {self.kill_path}"]
        return lines

    # ------------------------------------------------------------------------------------------ startup gates
    def run_gates(self, live: bool, env: str | None, confirm: Any = None, *, isatty: Callable[[], bool] | None = None,
                  out: Callable[[str], Any] | None = None) -> list[Gate]:
        """Evaluate the live gates without raising. Watch-only (``live`` false) needs none beyond a non-live client.

        Order: static gates; then (only if they all pass) the interactive confirmation; then the network gates (clock
        skew, authenticated probe), so the skew reading is fresh when trading starts; finally the kill file again.
        """
        client_live = getattr(self.client, "live", None)
        if not live:
            ok = client_live is not True
            return [Gate("watch_only", ok, "watch-only: no order can be sent" if ok else
                         "the client was built with live=True but --live was not given; refusing a live client")]
        env_obj = getattr(self.client, "env", None)
        env_name = getattr(env_obj, "name", None)
        gates = [
            Gate("trading_enabled", self.enabled, "trading.enabled is true" if self.enabled else
                 "trading.enabled is not true in config/settings.yaml"),
            Gate("live_flag", live is True, "--live given" if live is True else "--live is required"),
            Gate("env_var", env_name == "prod", "KALSHI_ENV=prod" if env_name == "prod" else
                 f"KALSHI_ENV must be exactly 'prod' for live trading, got {str(env_name)[:20]!r}"),
            Gate("env_flag", env == "prod", "--env prod given" if env == "prod" else
                 "--env prod is required on the command line" + ("" if isinstance(env, (str, type(None))) else
                                                                  " (pass the flag's string value)")),
            Gate("keys", bool(getattr(env_obj, "has_keys", False)) and client_live is True,
                 "API key loaded and the client is live" if getattr(env_obj, "has_keys", False) and client_live is True
                 else "live trading needs the environment's API key and a client built with live=True"),
            Gate("kill_file", not self.kill_active(), "no kill file" if not self.kill_active() else
                 f"kill file present ({self.kill_path}); delete it and start a new run"),
            self._loss_gate(),
            self._unresolved_gate(),
        ]
        if not all(g.ok for g in gates):
            gates.append(Gate("confirmation", False, "not asked: an earlier gate failed"))
            return gates
        gates.extend(self._confirmation_gates(confirm, isatty or self._isatty, out))
        if not all(g.ok for g in gates):
            return gates
        gates.append(self._skew_gate())
        gates.append(self._auth_gate())
        gates.append(Gate("kill_file_final", not self.kill_active(), "no kill file" if not self.kill_active() else
                          "the kill file appeared during startup"))
        return gates

    def _loss_gate(self) -> Gate:
        try:
            used = self.loss_used()
            cap = self.limits.max_loss_usd
            if used >= cap and self._sweep_stale_settlements(halt=False):   # (maybe unrecorded settlements)
                used = self.loss_used()
        except Exception as exc:
            return Gate("loss_cap", False, f"cannot read the ledger: {type(exc).__name__}")
        if used >= cap:
            return Gate("loss_cap", False, f"the ${plain_str(cap)} loss cap is used up (${plain_str(used)})")
        return Gate("loss_cap", True, f"${plain_str(used)} of ${plain_str(cap)} used")

    def _unresolved_gate(self) -> Gate:
        try:
            unresolved = self.ledger.unresolved_orders()
        except Exception as exc:
            return Gate("unresolved_orders", False, f"cannot read the ledger: {type(exc).__name__}")
        if unresolved:
            return Gate("unresolved_orders", False, f"{len(unresolved)} order(s) have an unknown outcome: run "
                        "'kpm exec reconcile' before starting")
        return Gate("unresolved_orders", True, "no order with an unknown outcome")

    def _confirmation_gates(self, confirm: Any, isatty: Callable[[], bool] | None,
                            out: Callable[[str], Any] | None) -> list[Gate]:
        phrase = self.confirmation_phrase
        tty_fn = isatty or (lambda: bool(getattr(sys.stdin, "isatty", lambda: False)()))
        try:
            has_tty = bool(tty_fn())
        except Exception:
            has_tty = False
        tty = Gate("tty", has_tty, "interactive terminal" if has_tty else
                   "no interactive terminal (TTY): the confirmation must be typed by a person")
        if not has_tty:
            return [tty, Gate("confirmation", False, "not asked: no TTY")]
        emit = out if out is not None else print
        prompt = f'Type "{phrase}" to start live trading: '
        typed: Any
        if isinstance(confirm, str):
            typed = confirm
        elif callable(confirm) or confirm is None:
            for line in self.banner():
                emit(line)
            reader = confirm if callable(confirm) else input
            try:
                takes_prompt = len(inspect.signature(reader).parameters) > 0
            except (TypeError, ValueError):
                takes_prompt = True
            try:
                typed = reader(prompt) if takes_prompt else reader()
            except (EOFError, KeyboardInterrupt):
                typed = None
        else:
            typed = None                                     # a bool or anything else is never a confirmation
        ok = isinstance(typed, str) and typed.strip() == phrase
        return [tty, Gate("confirmation", ok, "typed confirmation accepted" if ok else
                          f'the confirmation must be exactly "{phrase}"')]

    def _skew_gate(self) -> Gate:
        skew, err = self._measure_clock()                 # (also what every later order check reads)
        if skew is None:
            return Gate("clock_skew", False, err or "clock skew could not be measured")
        limit = self.limits.max_clock_skew_s
        ok = abs(skew) <= limit
        return Gate("clock_skew", ok, f"clock skew {skew:+.2f} s vs Kalshi (limit {limit:g} s)")

    def _auth_gate(self) -> Gate:
        try:
            data = self.client.balance(None)
            units.dec(data["balance_dollars"])
        except Exception as exc:
            name = type(exc).__name__
            hint = "authentication refused: the key and KALSHI_ENV do not match" if name == "AuthError" else \
                f"authenticated balance read failed ({name})"
            return Gate("auth_probe", False, hint)
        return Gate("auth_probe", True, "authenticated GET /portfolio/balance works")

    def startup(self, live: bool, env: str | None, confirm: Any = None, *, isatty: Callable[[], bool] | None = None,
                out: Callable[[str], Any] | None = None) -> dict:
        """Run every gate; set ``started``. Raises :class:`LiveGateError` (and stays not started) if any gate fails.

        ``live``: the ``--live`` flag. ``env``: the ``--env`` flag's string value (``"prod"``; None if not given).
        ``confirm``: the text the user typed, or a callable that prompts for it (it gets the prompt text when it takes
        an argument), or None to prompt with ``input()``. Without a TTY a live start is always refused.
        """
        self.started = False
        gates = self.run_gates(live, env, confirm, isatty=isatty, out=out)
        report = {"mode": "live" if live else "watch", "ok": all(g.ok for g in gates),
                  "gates": [g.as_dict() for g in gates], "limits": self.limits.as_dict()}
        self._audit("startup", report)
        if not report["ok"]:
            raise LiveGateError(gates)
        self.live = bool(live)
        self.started = True
        if hasattr(self.ledger, "live"):
            self.ledger.live = self.live
        return report

    def diagnostics(self, env: str | None = None) -> dict:
        """Read-only health report for ``kpm exec check``: no confirmation, no ``--live``, no orders.

        Every check runs independently and a failure is data, not an exception.
        """
        checks: list[Gate] = []

        def run(name: str, fn: Callable[[], Gate]) -> None:
            try:
                checks.append(fn())
            except Exception as exc:
                checks.append(Gate(name, False, f"{type(exc).__name__}"))

        env_obj = getattr(self.client, "env", None)
        env_name = getattr(env_obj, "name", None)
        checks.append(Gate("config", True, "inside every hard ceiling"))
        checks.append(Gate("environment", env_name is not None and (env is None or env == env_name),
                           f"KALSHI_ENV={env_name}" + ("" if env is None or env == env_name else
                                                       f" does not match --env {str(env)[:20]!r}")))
        checks.append(Gate("keys", bool(getattr(env_obj, "has_keys", False)),
                           f"loaded, key_fp {getattr(env_obj, 'key_fp', None)}" if getattr(env_obj, "has_keys", False)
                           else "no API key loaded: authenticated checks are skipped"))
        checks.append(Gate("kill_file", not self.kill_active(), "absent" if not self.kill_active() else
                           f"present ({self.kill_path})"))
        checks.append(Gate("maintenance", not in_maintenance(self.clock()), "outside Thursday 02:55-05:05 ET"
                           if not in_maintenance(self.clock()) else "inside the Thursday maintenance window"))
        checks.append(self._loss_gate())

        def shards() -> Gate:
            """The shard(s) the allowed series trade on must be trading (the same rule as the per-order check). Another
            shard that is paused (Combos, Tennis ...) is not a problem for these bots: it is only reported."""
            status = self.client.exchange_status()
            try:
                wanted = self._series_shards()
            except _Refuse as why:
                return Gate("exchange_status", False, str(why))
            by_shard: dict[int, list[str]] = {}
            for series, ei in wanted.items():
                by_shard.setdefault(ei, []).append(series)
            names = {ei: f"shard {ei} ({', '.join(ss)})" for ei, ss in sorted(by_shard.items())}
            bad = [n for ei, n in names.items() if not _shard_active(status, ei)]
            others = [str(e.get("exchange_index")) for e in (status.get("exchange_index_statuses") or ())
                      if isinstance(e, Mapping) and e.get("exchange_index") not in by_shard
                      and not (e.get("exchange_active") is True and e.get("trading_active") is True)]
            detail = (f"{', '.join(bad)} not trading" if bad else f"{', '.join(names.values())} trading")
            if others:
                detail += f"; other shard(s) not trading, not used here: {', '.join(others)}"
            return Gate("exchange_status", not bad, detail)

        run("exchange_status", shards)
        run("clock_skew", self._skew_gate)
        if getattr(env_obj, "has_keys", False):
            run("auth_probe", self._auth_gate)
        unresolved = []
        try:
            unresolved = self.ledger.unresolved_orders()
        except Exception:
            pass
        checks.append(Gate("unresolved_orders", not unresolved, "none" if not unresolved else
                           f"{len(unresolved)} order(s) with an unknown outcome: run kpm exec reconcile"))
        return {"ok": all(g.ok for g in checks), "checks": [g.as_dict() for g in checks], "status": self.status()}

    def _series_shards(self) -> dict[str, int]:
        """``{series: exchange_index}`` for every allowed series: from GET /series/{t} (a small public read), else from the
        series' open markets. Raises :class:`_Refuse` when a series' shard cannot be read (fail closed)."""
        out: dict[str, int] = {}
        for series in self.limits.allowed_series:
            ei: Any = None
            fn = getattr(self.client, "series", None)
            if fn is not None:
                try:
                    ei = (fn(series) or {}).get("exchange_index")
                except Exception:
                    ei = None
            if not _is_int(ei):
                ei = None
                try:
                    for m in self.client.open_markets(series) or ():
                        if isinstance(m, Mapping) and _is_int(m.get("exchange_index")):
                            ei = m["exchange_index"]
                            break
                except Exception:
                    ei = None
            if ei is None:
                raise _Refuse(f"cannot read the exchange_index (shard) of series {series}")
            out[series] = ei
        return out

    # ------------------------------------------------------------------------------------------ the per-order checks
    def check_entry(self, spec: Mapping[str, Any], book: Mapping[str, Any] | None,
                    market: Mapping[str, Any] | None, *,
                    batch: Sequence[Mapping[str, Any]] | None = None) -> tuple[bool, str]:
        """May this entry order be sent? ``(True, "")`` or ``(False, reason)``. Never raises.

        An entry is an IOC buy of one outcome priced EXACTLY at that outcome's current ask in ``book`` (a parsed
        ExecClient.orderbook, fresh), on a ``market`` that is open in an allowed series.

        ``batch``: the specs of EVERY order that will be sent together with this one (this one included, e.g. both legs
        of a pair). The rate limits, the loss cap, the exposure cap and the cash check then count all of them, so legs
        that each fit alone cannot add up past a cap. Each leg is still checked with its own book and market.
        """
        return self._check("entry", spec, book, market, batch)

    def check_exit(self, spec: Mapping[str, Any], book: Mapping[str, Any] | None,
                   market: Mapping[str, Any] | None) -> tuple[bool, str]:
        """May this exit order be sent? A ``reduce_only`` IOC buy of the opposite outcome of a position we hold, priced
        at most ``exit_max_ticks`` (2) through the touch. Exits skip the loss and exposure caps: they only reduce risk."""
        return self._check("exit", spec, book, market)

    def preflight(self, market: Mapping[str, Any]) -> str | None:
        """Warm the caches (market listing, shard status, cash, position and resting orders) for ``market`` before a
        window opens, so the first check_entry has no network latency. Returns a problem string or None. Optional.

        Also reports (from the cached measurement, with no probe) a PC clock that is off or has not been re-measured."""
        try:
            self._require_live_state()
            ticker, ei = market.get("ticker"), market.get("exchange_index")
            if not isinstance(ticker, str) or not _is_int(ei):
                return "market has no ticker or exchange_index"
            problem = self._clock_problem()
            if problem is not None:
                return problem
            self._check_market("entry", ticker, ei, market, None)
            self._cash(ei)
            self._exchange_view(ticker)
        except _Refuse as r:
            return str(r)
        except AuthError as exc:
            self._auth_stop(exc)
            return f"authentication refused (HTTP 401/403): {exc}; safety halted"
        except Exception as exc:
            return f"preflight failed: {type(exc).__name__}: {exc}"
        return None

    def _auth_stop(self, exc: Exception) -> None:
        """A 401/403 on an authenticated read: the key, KALSHI_ENV or the PC clock is wrong. Stop (kill file) instead of
        retrying forever (design section 2: on 401, stop; don't loop)."""
        self.trip(f"authentication refused (HTTP 401/403) on a safety read: {exc}")

    def _check(self, kind: str, spec: Any, book: Any, market: Any,
               batch: Sequence[Mapping[str, Any]] | None = None) -> tuple[bool, str]:
        try:
            reason = self._refusal(kind, spec, book, market, batch)
        except _Refuse as r:
            reason = str(r)
        except AuthError as exc:
            self._auth_stop(exc)
            reason = f"authentication refused (HTTP 401/403): {exc}; safety halted"
        except Exception as exc:                              # fail closed on anything unexpected
            log.exception("safety check %s failed", kind)
            reason = f"safety check error: {type(exc).__name__}: {exc}"
        if reason:
            self.refusals += 1
            self.last_refusal = reason
            self._audit(f"{kind}_refused", {"reason": reason, "ticker": _safe_ticker(spec)})
            return False, reason
        return True, ""

    def _refusal(self, kind: str, spec: Any, book: Any, market: Any,
                 batch: Sequence[Mapping[str, Any]] | None = None) -> str | None:
        self._require_live_state()
        order = self._parse_order(kind, spec)
        group = self._parse_batch(kind, spec, order, batch)
        self._check_rate(kind, len(group))
        self._check_hours()
        self._check_clock()
        self._check_ledger(kind, order, group)
        listed = self._check_market(kind, order.ticker, order.exchange_index, market, order)
        if kind == "entry":
            self._check_account(order, group)
        self._check_book(kind, order, book, listed)
        self._require_live_state()                # the network reads took time: halted / kill file / lockout, once more
        return None

    def _parse_batch(self, kind: str, spec: Any, order: _Order, batch: Sequence[Mapping[str, Any]] | None) -> list[_Order]:
        """Every order sent together with ``order`` (itself included). A single order is a batch of one."""
        if batch is None:
            return [order]
        if kind != "entry":
            raise _Refuse("only entries are checked as a batch")
        if isinstance(batch, (str, bytes)) or not isinstance(batch, Sequence) or not batch:
            raise _Refuse("batch must be a non-empty list of order specs")
        return [self._parse_order(kind, s) for s in batch if s is not spec] + [order]

    # ---- state, kill file, lockout
    def _require_live_state(self) -> None:
        if not self.started:
            raise _Refuse("Safety.startup() has not passed")
        if not self.live:
            raise _Refuse("watch-only run: no order may be sent")
        if self.halted:
            raise _Refuse(f"safety is halted: {self.halt_reason}")
        if self.kill_active():
            raise _Refuse(f"kill switch: {self.kill_path} exists")
        until = self.lockout_until()
        now = self.clock()
        if until > now:
            raise _Refuse(f"lockout after a cancel-all: no orders for another {until - now:.0f} s")

    # ---- the order itself
    def _parse_order(self, kind: str, spec: Any) -> _Order:
        if not isinstance(spec, Mapping):
            raise _Refuse("order spec is not a mapping")
        body = spec.get("body") if isinstance(spec.get("body"), Mapping) else {}

        def pick(*names: str) -> Any:
            found: list[Any] = []
            for src in (spec, body):
                for n in names:
                    if src.get(n) is not None:
                        found.append(src[n])
            if len(found) > 1 and not _all_equal(found):
                raise _Refuse(f"the spec and its body disagree on {names[0]}")
            return found[0] if found else None

        ticker = pick("ticker")
        if not isinstance(ticker, str) or not ticker:
            raise _Refuse("order has no ticker")
        ei = pick("exchange_index")
        if not _is_int(ei) or ei < 0:
            raise _Refuse("order has no exchange_index (the market's shard)")
        tif = pick("time_in_force")
        if tif != TIF_IOC:
            raise _Refuse(f"only immediate_or_cancel orders are allowed, got time_in_force={tif!r}")
        if pick("post_only"):
            raise _Refuse("post_only is not allowed on a taker IOC order")
        if pick("expiration_time") is not None:
            raise _Refuse("expiration_time is only valid on resting (GTC) orders, which are not allowed")
        reduce_only = pick("reduce_only")
        if reduce_only is None:
            reduce_only = False
        if not isinstance(reduce_only, bool):
            raise _Refuse("reduce_only must be a bool")
        if kind == "entry" and reduce_only:
            raise _Refuse("an entry must not be reduce_only")
        if kind == "exit" and not reduce_only:
            raise _Refuse("an exit must be reduce_only")
        stp = pick("self_trade_prevention_type")
        if stp not in (None, "taker_at_cross", "maker"):
            raise _Refuse(f"unknown self_trade_prevention_type {stp!r}")

        outcome, oprice = pick("outcome"), pick("outcome_price")
        side, yes_raw = pick("side"), pick("yes_price", "price")
        if (outcome is None) != (oprice is None):
            raise _Refuse("outcome and outcome_price must be given together")
        yes_price = _dec(yes_raw, "price") if yes_raw is not None else None
        if outcome is not None:
            if outcome not in units.OUTCOMES:
                raise _Refuse(f"outcome must be 'yes' or 'no', got {outcome!r}")
            op = _dec(oprice, "outcome_price")
            try:
                exp_side, exp_yes = units.to_book(outcome, op)
            except (TypeError, ValueError) as exc:
                raise _Refuse(f"bad outcome price: {exc}") from None
            if side is not None and side != exp_side:
                raise _Refuse(f"side {side!r} does not match units.to_book({outcome}, {op}) = {exp_side!r}")
            if yes_price is not None and yes_price != exp_yes:
                raise _Refuse(f"YES price {yes_price} does not match units.to_book({outcome}, {op}) = {exp_yes}")
            side, yes_price = exp_side, exp_yes
        else:
            if side not in units.BOOK_SIDES or yes_price is None:
                raise _Refuse("the spec needs outcome + outcome_price, or side ('bid'|'ask') + the YES price")
            try:
                outcome, op = units.from_book(side, yes_price)
            except (TypeError, ValueError) as exc:
                raise _Refuse(f"bad YES price: {exc}") from None
        count = _dec(pick("count"), "count")
        if count <= 0 or count != count.quantize(CENT):
            raise _Refuse(f"count {count} must be > 0 in steps of 0.01")
        lim = self.limits
        if count > lim.max_contracts_per_order:
            raise _Refuse(f"{count} contracts exceeds the per-order limit of {plain_str(lim.max_contracts_per_order)}")
        if not (lim.price_min <= yes_price <= lim.price_max):
            raise _Refuse(f"YES price {yes_price} is outside {plain_str(lim.price_min)}..{plain_str(lim.price_max)}")
        collateral = units.collateral(side, yes_price, count)
        if collateral > lim.max_cost_per_order_usd:
            raise _Refuse(f"collateral {plain_str(collateral)} exceeds the per-order limit "
                          f"${plain_str(lim.max_cost_per_order_usd)}")
        worst = collateral + units.worst_fee(count, yes_price)
        return _Order(kind, ticker, ei, outcome, op, side, yes_price, count, collateral, worst)

    # ---- rate limits and hours
    def _check_rate(self, kind: str = "entry", n: int = 1) -> None:
        """The order counts include the ``n`` orders about to be created (a pair is two), so a batch cannot step over a limit."""
        count = getattr(self.ledger, "count_orders_since", None)
        if count is None:
            raise _Refuse("the ledger cannot count orders: rate limits cannot be enforced")
        more = "" if n == 1 else f" and {n} more in this batch"
        now = self.clock()
        n_min = count(now - 60.0, run_only=False)
        if n_min + n > self.limits.max_orders_per_minute:
            raise _Refuse(f"rate limit: {n_min} orders in the last minute{more} (max {self.limits.max_orders_per_minute})")
        n_hour = count(now - 3600.0, run_only=False)
        if n_hour + n > self.limits.max_orders_per_hour:
            raise _Refuse(f"rate limit: {n_hour} orders in the last hour{more} (max {self.limits.max_orders_per_hour})")
        cap = self.limits.max_orders_per_session
        if cap is not None and kind == "entry":               # an exit only reduces risk: the session cap never blocks it
            n_run = count(None, run_only=True)
            if n_run + n > cap:
                raise _Refuse(f"session order cap: {n_run} orders sent in this run{more} (max {cap}); start a new run")

    def _check_hours(self) -> None:
        if in_maintenance(self.clock()):
            raise _Refuse("Thursday maintenance window (02:55-05:05 ET): no orders")

    # ---- ledger-based rules
    def _check_ledger(self, kind: str, order: _Order, group: Sequence[_Order] | None = None) -> None:
        led = self.ledger
        unresolved = led.unresolved_orders()
        if kind == "entry":
            if unresolved:
                ids = ", ".join(str(o["client_order_id"])[:8] for o in unresolved[:3])
                raise _Refuse(f"{len(unresolved)} earlier order(s) have an unknown outcome ({ids}): "
                              "reconcile before sending anything new")
            if led.has_inflight(order.ticker, order.side):
                raise _Refuse(f"an order on {order.ticker} ({order.side}) is still open")
            reason = self._cap_violation(order, group)
            if reason is not None and self._sweep_stale_settlements():
                reason = self._cap_violation(order, group)        # settled positions no longer count: look again
            if reason is not None:
                if reason.startswith("loss cap reached"):
                    self.trip(f"realized loss reached the ${plain_str(self.limits.max_loss_usd)} cap")
                raise _Refuse(reason)
            return
        same = [o for o in unresolved if o["ticker"] == order.ticker]
        if same:
            raise _Refuse(f"an earlier order on {order.ticker} has an unknown outcome: reconcile before exiting")
        pos = led.position(order.ticker, include_unrecorded=True)
        reduces = (order.outcome == "no" and pos > 0) or (order.outcome == "yes" and pos < 0)
        if not reduces:
            raise _Refuse(f"an exit buying {order.outcome.upper()} would not reduce the ledger position {pos} "
                          f"on {order.ticker}")
        if order.count > abs(pos):
            raise _Refuse(f"exit of {order.count} exceeds the {abs(pos)} contracts held on {order.ticker}")

    def _cap_violation(self, order: _Order, group: Sequence[_Order] | None = None) -> str | None:
        """The loss-cap or exposure-cap reason this entry would be refused for, or None. Reads only the ledger.

        ``group``: every order sent together with this one (a pair's two legs). Their worst cases and their collateral
        are added up before they are compared with the caps, because neither leg is in the ledger yet when the other is
        checked."""
        lim = self.limits
        orders = list(group) if group else [order]
        worst = sum((o.worst for o in orders), D0)
        collateral = sum((o.collateral for o in orders), D0)
        what = "this order" if len(orders) == 1 else f"these {len(orders)} orders"
        realized, open_worst, exposure = self._risk()
        if realized >= lim.max_loss_usd:
            return f"loss cap reached: realized loss {plain_str(realized)}"
        used = realized + open_worst
        if used + worst > lim.max_loss_usd:
            return (f"loss cap: {plain_str(used)} used + {plain_str(worst)} worst case of {what} "
                    f"> ${plain_str(lim.max_loss_usd)}")
        if exposure + collateral > lim.max_open_exposure_usd:
            return (f"exposure cap: {plain_str(exposure)} open + {plain_str(collateral)} collateral"
                    f"{'' if len(orders) == 1 else f' of {what}'} > ${plain_str(lim.max_open_exposure_usd)}")
        return None

    def _sweep_stale_settlements(self, halt: bool = True) -> bool:
        """A cap is about to refuse an order: first record the settlement of old positions the bots never got to
        settle (Kalshi had not published the row when they looked). True if anything new was recorded.

        ``halt``: a settlement that disagrees with our fills halts the session. The startup gate and the read-only
        ``diagnostics()`` pass False: a check command must not trip anything; the mismatch stays in the audit log."""
        try:
            from kpm.execution import reconcile
            res = reconcile.sweep_settlements(self.client, self.ledger, clock=self.clock, halt=halt,
                                              stale_after_s=self.limits.sweep_after_s,
                                              retry_s=self.limits.sweep_retry_s)
        except Exception:
            log.exception("stale settlement sweep failed")
            return False
        if res.settled:
            self._audit("stale_settlements", {"settled": res.settled, "mismatches": res.mismatches})
        return bool(res.settled)

    # ---- market, shard and grid
    def _cached(self, key: tuple, ttl: float, fetch: Callable[[], Any]) -> Any:
        now = self.clock()
        hit = self._cache.get(key)
        if hit is not None and 0 <= now - hit[0] <= ttl:
            return hit[1]
        value = fetch()
        self._put(key, now, value)
        return value

    def _put(self, key: tuple, now: float, value: Any) -> None:
        """Store a cached read; a long run (one new ticker every 15 minutes) never lets the cache grow without bound."""
        self._cache[key] = (now, value)
        if len(self._cache) > 256:
            for k in [k for k, (t, _v) in self._cache.items() if not 0 <= now - t <= 600]:
                del self._cache[k]

    def invalidate(self, *prefix: Any) -> None:
        """Drop cached reads whose key starts with ``prefix`` (all of them when called with no arguments)."""
        for k in [k for k in self._cache if k[: len(prefix)] == prefix]:
            del self._cache[k]

    def _listed_market(self, ticker: str, market: Mapping[str, Any]) -> Mapping[str, Any]:
        """The fresh open-markets entry for ``ticker`` in an allowed series (never parsed from the ticker).

        The listing is ``GET /markets?series_ticker=&status=open`` and nothing else: Kalshi's OpenAPI allows the close-time
        filters only with status closed (or no status), so they are not sent here. The ticker is matched in the listing
        (an open series holds one or two KXBTC15M markets and a few hundred KXBTCD strikes, one page), and a stale
        ``close_time`` on the caller's market object is caught afterwards by comparing it with the listing's."""
        def fetch() -> Mapping[str, Any]:
            hint = str(market.get("event_ticker") or "")
            order = sorted(self.limits.allowed_series, key=lambda s: 0 if hint.startswith(s + "-") else 1)
            for series in order:
                for m in self.client.open_markets(series) or ():
                    if isinstance(m, Mapping) and m.get("ticker") == ticker:
                        return dict(m)
            raise _Refuse(f"{ticker} is not in the open markets of the allowed series "
                          f"({', '.join(self.limits.allowed_series)}): wrong ticker or a market that is no longer open")

        return self._cached(("listed", ticker), self.limits.markets_ttl_s, fetch)

    def _check_market(self, kind: str, ticker: str, exchange_index: int, market: Any,
                      order: _Order | None) -> Mapping[str, Any]:
        if not isinstance(market, Mapping):
            raise _Refuse("no market object")
        if market.get("ticker") != ticker:
            raise _Refuse(f"market object is for {market.get('ticker')!r}, not {ticker!r}")
        if market.get("exchange_index") != exchange_index or not _is_int(market.get("exchange_index")):
            raise _Refuse(f"order exchange_index {exchange_index} does not match the market's {market.get('exchange_index')!r}")
        listed = self._listed_market(ticker, market)
        if listed.get("exchange_index") != exchange_index:
            raise _Refuse("the fresh market listing has another exchange_index: the market object is stale")
        listed_close, given_close = _close_ts(listed), _close_ts(market)       # (compared as times, not as strings)
        if listed_close is None or given_close is None or abs(listed_close - given_close) >= 0.5 \
                or _norm_ranges(listed.get("price_ranges")) != _norm_ranges(market.get("price_ranges")):
            raise _Refuse("the market object differs from the fresh listing (close_time or price_ranges): it is stale")
        if listed.get("status") != "active" or market.get("status") not in (None, "active"):
            raise _Refuse(f"market status is {listed.get('status')!r}, not 'active'")
        close = _close_ts(listed)
        if close is None:
            raise _Refuse("market has no usable close_time")
        remaining = close - self.clock()
        need = self.limits.min_seconds_to_close if kind == "entry" else self.limits.exit_min_seconds_to_close
        if kind == "entry" and listed.get("can_close_early") is True:
            need += self.limits.early_close_buffer_s
        if remaining < need:
            raise _Refuse(f"{ticker} closes in {remaining:.0f} s, less than the {need:.0f} s required")
        status = self._cached(("status",), self.limits.status_ttl_s, self.client.exchange_status)
        if not _shard_active(status, exchange_index):
            raise _Refuse(f"exchange shard {exchange_index} is not trading_active (or is missing from /exchange/status)")
        if order is not None:
            try:
                on_grid = units.on_grid(order.yes_price, listed.get("price_ranges") or ())
            except (TypeError, ValueError, KeyError, InvalidOperation):
                on_grid = False
            if not on_grid:
                raise _Refuse(f"YES price {order.yes_price} is not on the market's price grid")
        return listed

    # ---- cash, position and resting orders
    def _cash(self, exchange_index: int) -> tuple[Decimal, float]:
        """(cash on this shard in dollars, time the read started). Never the all-shard total."""
        def fetch() -> tuple[Decimal, float]:
            t0 = self.clock()
            data = self.client.balance(exchange_index)
            try:
                return units.dec(data["balance_dollars"]), t0
            except (KeyError, TypeError, ValueError):
                raise _Refuse(f"no usable balance_dollars for shard {exchange_index}") from None

        return self._cached(("cash", exchange_index), self.limits.account_ttl_s, fetch)

    def _exchange_view(self, ticker: str) -> tuple[Decimal, list[dict]]:
        """(exchange position_fp on ``ticker``, resting orders on it); cached until the ledger sees new activity."""
        version = getattr(self.ledger, "last_activity_ts", lambda t: None)(ticker)
        key = ("view", ticker)
        now = self.clock()
        hit = self._cache.get(key)
        if hit is not None and 0 <= now - hit[0] <= self.limits.account_ttl_s and hit[1][0] == version:
            return hit[1][1]
        pos = D0
        for row in self.client.positions(ticker=ticker) or ():
            if isinstance(row, Mapping) and row.get("ticker") == ticker:
                pos += units.dec(row.get("position_fp") or "0")
        resting = [dict(o) for o in (self.client.orders(ticker=ticker, status="resting") or ())
                   if isinstance(o, Mapping)]
        value = (pos, resting)
        self._put(key, now, (version, value))
        return value

    def _check_account(self, order: _Order, group: Sequence[_Order] | None = None) -> None:
        cash, read_ts = self._cash(order.exchange_index)
        pending = self.ledger.worst_of_orders_since(read_ts)
        # the other legs of the batch are not in the ledger yet: the shard must cover all of its legs at once
        same_shard = [o for o in (group or [order]) if o.exchange_index == order.exchange_index]
        need = sum((o.worst for o in same_shard), D0) + self.limits.cash_buffer_usd
        if cash - pending < need:
            what = "this order needs" if len(same_shard) == 1 else f"these {len(same_shard)} orders need"
            raise _Refuse(f"cash on shard {order.exchange_index} is {plain_str(cash)} (less {plain_str(pending)} for "
                          f"orders sent since the reading); {what} {plain_str(need)}")
        pos, resting = self._exchange_view(order.ticker)
        ours = self.ledger.position(order.ticker, include_unrecorded=True)
        if abs(pos - ours) > POSITION_TOLERANCE:
            raise _Refuse(f"position on {order.ticker}: exchange {plain_str(pos)} != ledger {plain_str(ours)} "
                          "(a manual trade, a missed fill or reconcile lag)")
        if resting:
            ids = ", ".join(str(o.get("order_id"))[:8] for o in resting[:3])
            raise _Refuse(f"{len(resting)} resting order(s) on {order.ticker} ({ids}): they could trade against this one")

    # ---- the order book (checked last, so its age is measured as close to the send as possible)
    def _check_book(self, kind: str, order: _Order, book: Any, listed: Mapping[str, Any]) -> None:
        if not isinstance(book, Mapping):
            raise _Refuse("no order book")
        if book.get("ticker") not in (None, order.ticker):
            raise _Refuse(f"the order book is for {book.get('ticker')!r}, not {order.ticker!r}")
        try:
            age = self.clock() - float(book["fetched_at"])
        except (KeyError, TypeError, ValueError):
            raise _Refuse("the order book has no fetched_at: freshness cannot be checked") from None
        if age < -1.0:
            raise _Refuse(f"the order book's timestamp is {-age:.1f} s in the future")
        if age > self.limits.max_book_age_s:
            raise _Refuse(f"order book is {age:.1f} s old (stale; max {self.limits.max_book_age_s:g} s)")
        try:
            yes_best, no_best = units.best_bid(book.get("yes")), units.best_bid(book.get("no"))
            ask = units.outcome_ask(book, order.outcome)
        except (TypeError, ValueError) as exc:
            raise _Refuse(f"unusable order book: {exc}") from None
        if yes_best is not None and no_best is not None and yes_best + no_best >= D1:
            raise _Refuse(f"the order book is crossed or locked (best YES bid {yes_best} + best NO bid {no_best} >= 1): "
                          "stale or transitional data cannot price an order")
        if ask is None:
            other = "NO" if order.outcome == "yes" else "YES"
            raise _Refuse(f"the book has no {other} bids, so there is no {order.outcome.upper()} ask to price against")
        touch, _size = ask
        if kind == "entry":
            if order.outcome_price != touch:
                raise _Refuse(f"price {order.outcome_price} is not the current {order.outcome.upper()} ask {touch}: "
                              "entries are priced exactly at the ask")
            return
        try:
            bands = _bands(listed.get("price_ranges"))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise _Refuse("the market's price_ranges are unusable") from None
        _, touch_yes = units.to_book(order.outcome, touch)
        n = ticks_from_touch(bands, touch_yes, order.yes_price, up=(order.side == "bid"),
                             max_ticks=self.limits.exit_max_ticks)
        if n is None:
            raise _Refuse(f"exit price {order.outcome_price} is not within {self.limits.exit_max_ticks} ticks "
                          f"through the {order.outcome.upper()} touch {touch}")

    # ------------------------------------------------------------------------------------------ audit
    def _audit(self, action: str, details: Mapping[str, Any]) -> None:
        log_event = getattr(self.ledger, "log_event", None)
        if log_event is None:
            return
        try:
            log_event(action, details, component=COMPONENT, entity_type="env",
                      entity_id=getattr(self.ledger, "env", None))
        except Exception:
            log.exception("could not audit %s", action)


# ============================================================================================ small helpers
def _skew_seconds(v: Any) -> float:
    """Seconds of skew from ExecClient.server_time_skew() (a float) or a mapping/object that carries it."""
    if isinstance(v, Mapping):
        for k in ("skew_s", "skew", "skew_seconds"):
            if k in v:
                v = v[k]
                break
    elif hasattr(v, "skew_s"):
        v = v.skew_s
    if isinstance(v, bool) or not isinstance(v, (int, float, Decimal)):
        raise TypeError("skew is not a number")
    f = float(v)
    if not math.isfinite(f):
        raise ValueError("skew is not finite")
    return f


def _norm_ranges(ranges: Any) -> tuple:
    out = []
    for b in ranges or ():
        try:
            out.append((units.dec(b["start"]), units.dec(b["end"]), units.dec(b["step"])))
        except (KeyError, TypeError, ValueError):
            out.append(("bad", repr(b)))
    return tuple(out)


def _all_equal(values: Sequence[Any]) -> bool:
    """True if every value is equal, comparing numbers numerically (Decimal('0.56') == '0.5600')."""
    def norm(x: Any) -> Any:
        if isinstance(x, (Decimal, int)) and not isinstance(x, bool):
            return Decimal(x)
        if isinstance(x, str):
            try:
                return Decimal(x)
            except InvalidOperation:
                return x
        return x

    first = norm(values[0])
    return all(norm(v) == first for v in values[1:])


def _safe_ticker(spec: Any) -> str | None:
    try:
        t = spec.get("ticker") if isinstance(spec, Mapping) else None
        return str(t)[:80] if t else None
    except Exception:
        return None
