"""Reconciliation, settlement and the cancel path (docs/execution_design.md 5.4-5.6).

Strategies call these with the same three leading arguments, so ``ctx.reconcile`` can simply be this module:

    reconcile(client, ledger, tickers=None)          compare the ledger with the exchange; resolve unknown outcomes
    settle(client, ledger, tickers=None)             persist settlement rows (integer cents) and the realized P&L
    cancel_path(client, ledger, reason, touched)     cancel everything resting, verify, fall back to cancel-all

Nothing in this module ever creates an order, so nothing can be resent. Reads only, except the cancels in
:func:`cancel_path`.

RECONCILE
  * Orders: ``orders(ticker)`` is matched to the ledger by client_order_id (REST fills carry no client_order_id, orders
    do). An order whose outcome was unknown (timeout, 5xx, 409, a crash after the intent) is adopted when found. When it
    is not found after the exchange's REST data (``user_data_timestamp``) has passed its send time, it is marked
    ``presumed_not_placed`` and the session is halted. Never resent.
  * Fills: ``fills(ticker)`` is split into fills of orders in this ledger (stored, deduplicated on fill_id) and foreign
    fills (a manual trade in the app: never stored, so they never touch P&L or the loss cap).
  * Checks per ticker: stored fills add up to each order's ``fill_count``; the exchange position equals the ledger
    position plus the foreign fills; an IOC order is never resting. A discrepancy is a real mismatch only when a second
    read, after the data has caught up and 5 s later, shows the same discrepancy. If the data has NOT caught up
    (``as_of_time`` of /exchange/user_data_timestamp is still behind the last order, or cannot be read) nothing is
    declared: the discrepancy is reported as ``unconfirmed``, its ticker as ``incomplete``, nobody is halted, and the next
    reconcile looks again (design 5.5 item 7: declare a mismatch only after ``as_of_time`` is past the event and two
    reads 5 s apart agree). A real mismatch calls ``ledger.halt()``, which trips Safety (kill file); an identity problem
    (another order id, ticker or side) halts at once.
  * A 401/403 on any read halts too (``authentication refused``): a bot must never loop on a rejected signature.
  * Cash is recorded in the audit log, not checked: whether resting collateral lowers ``balance_dollars`` is unknown.

SETTLE
  Settlement ``revenue`` and ``value`` are integer cents. P&L is payoff - cost - the fees on the fills, each fee once
  (the settlement's own ``fee_cost`` is the same fees summed and is never added again). A revenue that differs from what
  the local fills say is explained, or reported as a mismatch, using the foreign fills. The bots call :func:`settle`
  once per ticker; if Kalshi had not published the row yet, :func:`sweep_settlements` (run by :func:`reconcile`, and by
  Safety when a loss or exposure cap would refuse an order) records it later, so a settled position never keeps
  counting against the caps.

CANCEL PATH
  Targeted cancels carrying each order's own ``exchange_index`` (KXBTC15M and KXBTCD live on shard 2; an unrouted cancel
  would go to shard 0) -> verify with ``orders(ticker, status=resting)`` using bounded backoff for up to 30 s ->
  ``cancel_all()`` only if something is still resting, or the verification could not be done while something of ours
  could be resting -> a 60 s lockout (persisted, so a restart honours it). :class:`ExitGuard` runs it on exit, on an
  exception, on Ctrl+C / SIGBREAK and at interpreter exit.
"""
from __future__ import annotations

import atexit
import logging
import signal
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping

from kpm.execution import units
from kpm.execution.client import AuthError
from kpm.execution.ledger import D, D0, IMMEDIATE_TIFS, UNRESOLVED_STATUSES, plain

log = logging.getLogger(__name__)

COMPONENT = "exec.reconcile"
FRESH_WAIT_S = 10.0                    # wait this long for REST portfolio data to catch up with a write
FRESH_POLL_S = 1.0
LAG_RECHECK_S = 5.0                    # a mismatch counts only if a read 5 s later still shows it
PENDING_S = 15.0                       # an unknown order younger than this is not judged yet
VERIFY_TIMEOUT_S = 30.0
VERIFY_BACKOFF_S = (0.5, 1.0, 2.0, 4.0, 8.0)
LOCKOUT_S = 60.0
STALE_SETTLE_AFTER_S = 1200.0          # a ticker this ledger last traded 20 min ago has surely closed
STALE_SETTLE_RETRY_S = 120.0           # ... and a settlement that is still missing is retried at most this often
POSITION_TOLERANCE = Decimal("0.005")


# ============================================================================================ reports
@dataclass
class ReconcileReport:
    """What :func:`reconcile` found. ``ok`` is False for a real mismatch, an order presumed not placed or unreadable data."""

    tickers: list[str]
    adopted: list[str] = field(default_factory=list)          # unknown-outcome orders found on the exchange
    updated: list[str] = field(default_factory=list)          # other ledger orders whose status changed
    pending: list[str] = field(default_factory=list)          # unknown orders too young to judge
    presumed_not_placed: list[str] = field(default_factory=list)
    halting: list[str] = field(default_factory=list)          # presumed-not-placed orders that were actually sent
    fills_added: int = 0
    foreign_fills: int = 0
    settled: list[str] = field(default_factory=list)          # stale tickers whose settlement was recorded on the way
    lag_cleared: list[str] = field(default_factory=list)      # discrepancies the second read no longer showed
    mismatches: list[str] = field(default_factory=list)       # real: confirmed by a second read, or identity problems
    unconfirmed: list[str] = field(default_factory=list)      # seen on both reads while the portfolio data was behind: not a mismatch (yet)
    warnings: list[str] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)       # tickers that could not be read or confirmed
    skipped: list[str] = field(default_factory=list)          # tickers with no order in this ledger
    cash: dict[str, str] = field(default_factory=dict)        # shard -> balance_dollars (recorded, not checked)
    halted: bool = False

    @property
    def ok(self) -> bool:
        return not (self.mismatches or self.halting or self.incomplete)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["ok"] = self.ok
        return d


@dataclass
class SettleReport:
    recorded: list[dict] = field(default_factory=list)        # ledger.record_settlements() rows (P&L as Decimal)
    pending: list[str] = field(default_factory=list)          # no settlement row yet
    skipped: list[str] = field(default_factory=list)          # tickers this ledger never traded
    incomplete: list[str] = field(default_factory=list)       # settlement read failed
    mismatches: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    halted: bool = False

    @property
    def ok(self) -> bool:
        return not (self.mismatches or self.incomplete)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["recorded"] = [{**r, "pnl": plain(r["pnl"]) if isinstance(r.get("pnl"), Decimal) else r.get("pnl")}
                         for r in self.recorded]
        d["ok"] = self.ok
        return d


@dataclass
class CancelReport:
    reason: str
    tickers: list[str]
    targets: list[dict] = field(default_factory=list)         # {order_id, exchange_index, ticker, client_order_id, source}
    cancelled: list[str] = field(default_factory=list)        # order ids whose cancel returned ok
    failed: list[dict] = field(default_factory=list)
    unlocated: list[str] = field(default_factory=list)        # resting-capable ledger orders with no order id
    verified: bool = False                                    # the last verification pass read every ticker cleanly
    leftover: list[str] = field(default_factory=list)         # order ids still resting after the targeted cancels
    remaining: list[str] = field(default_factory=list)        # order ids still resting at the very end
    cancel_all_used: bool = False
    cancel_all_outcome: str | None = None
    lockout_until: float | None = None
    not_live: bool = False
    auth_failed: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when a clean read at the end showed nothing resting."""
        return self.verified and not self.remaining and not self.not_live and not self.auth_failed

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["ok"] = self.ok
        return d


# ============================================================================================ helpers
def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _items(resp: Any, key: str) -> list[Mapping]:
    """Items of a Listing (a list) or of a raw payload ``{key: [...]}``; non-mappings are dropped."""
    if isinstance(resp, Mapping):
        resp = resp.get(key)
    return [x for x in (resp or ()) if isinstance(x, Mapping)]


def _short(coid: Any) -> str:
    return str(coid)[:8]


def _signed(fill: Mapping[str, Any]) -> Decimal:
    """+count for a YES fill, -count for a NO fill (the position sign convention)."""
    side = fill.get("outcome_side") or {"bid": "yes", "ask": "no"}.get(str(fill.get("book_side")))
    count = D(fill["count_fp"] if fill.get("count_fp") is not None else fill.get("count"))
    return count if side == "yes" else -count


class _Ctx:
    """Shared plumbing for one call: clock, sleep and the tuning knobs."""

    def __init__(self, client: Any, ledger: Any, clock: Callable[[], float] | None,
                 sleep: Callable[[float], Any], **knobs: float):
        self.client = client
        self.ledger = ledger
        self.now = clock or getattr(ledger, "clock", None) or time.time
        self.sleep = sleep
        self.fresh_wait_s = knobs.get("fresh_wait_s", FRESH_WAIT_S)
        self.lag_recheck_s = knobs.get("lag_recheck_s", LAG_RECHECK_S)
        self.pending_s = knobs.get("pending_s", PENDING_S)


def _wait_fresh(ctx: _Ctx, since_ts: float | None) -> bool:
    """Bounded wait until the REST portfolio data (user_data_timestamp.as_of_time) has passed ``since_ts``.

    True when it has; False when it has not within ``fresh_wait_s`` or the timestamp cannot be read.
    """
    fn = getattr(ctx.client, "user_data_timestamp", None)
    if fn is None or since_ts is None:
        return True
    deadline = ctx.now() + ctx.fresh_wait_s
    while True:
        try:
            as_of = units.parse_rfc3339(fn()["as_of_time"])
        except Exception:
            return False
        if as_of >= since_ts:
            return True
        if ctx.now() >= deadline:
            return False
        ctx.sleep(FRESH_POLL_S)


def _fresh_now(ctx: _Ctx, since_ts: float | None) -> bool:
    """One read, no waiting: has the REST portfolio data (``as_of_time``) passed ``since_ts``? An unreadable timestamp
    is False (the data cannot be called fresh)."""
    fn = getattr(ctx.client, "user_data_timestamp", None)
    if fn is None or since_ts is None:
        return True
    try:
        return units.parse_rfc3339(fn()["as_of_time"]) >= since_ts
    except Exception:
        return False


def _event(ledger: Any, action: str, details: Mapping[str, Any]) -> None:
    fn = getattr(ledger, "log_event", None)
    if fn is None:
        return
    try:
        fn(action, details, component=COMPONENT, entity_type="env", entity_id=getattr(ledger, "env", None))
    except Exception:
        log.exception("could not record %s", action)


def _halt(ledger: Any, reason: str, details: Mapping[str, Any] | None = None) -> bool:
    fn = getattr(ledger, "halt", None)
    if fn is None:
        return False
    try:
        fn(reason, details)
        return True
    except Exception:
        log.exception("ledger.halt failed")
        return False


# ============================================================================================ reconcile
def reconcile(client: Any, ledger: Any, tickers: Iterable[str] | None = None, *,
              clock: Callable[[], float] | None = None, sleep: Callable[[float], Any] = time.sleep,
              fresh_wait_s: float = FRESH_WAIT_S, lag_recheck_s: float = LAG_RECHECK_S, pending_s: float = PENDING_S,
              record_cash: bool = True, halt: bool = True, settle_stale: bool = True,
              stale_after_s: float = STALE_SETTLE_AFTER_S, stale_retry_s: float = STALE_SETTLE_RETRY_S
              ) -> ReconcileReport:
    """Bring the ledger in line with the exchange for ``tickers`` (default: every ticker with an open order or an
    unsettled position) and report what could not be reconciled. Reads only. See the module docstring.

    ``settle_stale``: also try to record the settlement of other tickers this ledger last traded more than
    ``stale_after_s`` ago (at most one try per ticker per ``stale_retry_s``). The bots call :func:`settle` once per
    ticker; if Kalshi had not published the row yet, this keeps the loss and exposure caps from carrying a position
    that already settled.
    """
    ctx = _Ctx(client, ledger, clock, sleep, fresh_wait_s=fresh_wait_s, lag_recheck_s=lag_recheck_s,
               pending_s=pending_s)
    wanted = sorted({str(t) for t in (tickers if tickers is not None else ledger.tickers_to_reconcile())})
    rep = ReconcileReport(tickers=wanted)
    auth_failed = False
    for t in wanted:
        if not ledger.orders_for(t):
            rep.skipped.append(t)
            continue
        try:
            _reconcile_ticker(ctx, t, rep)
        except AuthError as exc:
            auth_failed = True
            rep.incomplete.append(t)
            rep.warnings.append(f"{t}: authentication refused ({exc})")
        except Exception as exc:
            rep.incomplete.append(t)
            rep.warnings.append(f"{t}: could not be read ({type(exc).__name__}: {exc})")
            log.warning("reconcile of %s failed: %s", t, exc)
    if record_cash:
        _record_cash(ctx, [t for t in wanted if t not in rep.skipped], rep)
    if settle_stale:
        _sweep_settlements(ctx, set(wanted), rep, halt, stale_after_s, stale_retry_s)
    reasons = []
    if rep.mismatches:
        reasons.append("reconcile mismatch: " + "; ".join(rep.mismatches[:3]))
    if auth_failed:
        reasons.append("authentication refused while reconciling")
    if rep.halting:
        reasons.append(f"order(s) {', '.join(_short(c) for c in rep.halting)} not found on the exchange after a send")
    if reasons and halt and not rep.halted:                # (a stale-settlement mismatch has already halted)
        rep.halted = _halt(ledger, "; ".join(reasons)[:480], {"tickers": wanted})
    _event(ledger, "reconcile", rep.as_dict())
    return rep


@dataclass
class SweepResult:
    """What :func:`sweep_settlements` did: tickers tried, tickers whose settlement was recorded, and any problems."""

    tried: list[str] = field(default_factory=list)
    settled: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)
    halted: bool = False


def sweep_settlements(client: Any, ledger: Any, *, clock: Callable[[], float] | None = None, halt: bool = True,
                      stale_after_s: float = STALE_SETTLE_AFTER_S, retry_s: float = STALE_SETTLE_RETRY_S,
                      skip: Iterable[str] = ()) -> SweepResult:
    """Record the settlement of every unsettled ticker this ledger last traded more than ``stale_after_s`` ago.

    A ticker is tried at most once per ``retry_s`` (so a settlement Kalshi never publishes costs one read per
    interval, not one per call). Never raises. Safety calls this when a loss or exposure cap would refuse an order,
    because a position that already settled must not keep counting against the caps.
    """
    out = SweepResult()
    now = float((clock or getattr(ledger, "clock", None) or time.time)())
    tried = getattr(ledger, "settle_attempts", None)
    if tried is None:
        return out
    skip = set(skip)
    try:
        candidates = ledger.unsettled_tickers()
    except Exception:
        return out
    for t in candidates:
        last = ledger.last_activity_ts(t)
        if t in skip or last is None or now - last < stale_after_s or now - tried.get(t, -1e18) < retry_s:
            continue
        tried[t] = now
        out.tried.append(t)
        try:
            srep = settle(client, ledger, [t], halt=halt)
        except Exception as exc:
            out.warnings.append(f"{t}: stale settlement check failed ({type(exc).__name__})")
            continue
        if srep.recorded:
            out.settled.append(t)
        out.warnings.extend(f"{t}: {w}" for w in srep.warnings)
        out.mismatches.extend(srep.mismatches)
        out.halted = out.halted or srep.halted
    return out


def _sweep_settlements(ctx: _Ctx, skip: set[str], rep: ReconcileReport, halt: bool, stale_after_s: float,
                       stale_retry_s: float) -> None:
    res = sweep_settlements(ctx.client, ctx.ledger, clock=ctx.now, halt=halt, stale_after_s=stale_after_s,
                            retry_s=stale_retry_s, skip=skip)
    rep.settled.extend(res.settled)
    rep.warnings.extend(w for w in res.warnings if w not in rep.warnings)
    rep.mismatches.extend(m for m in res.mismatches if m not in rep.mismatches)
    rep.halted = rep.halted or res.halted


def _record_cash(ctx: _Ctx, tickers: list[str], rep: ReconcileReport) -> None:
    shards = {o["exchange_index"] for t in tickers for o in ctx.ledger.orders_for(t) if _is_int(o["exchange_index"])}
    if not shards or not getattr(ctx.client, "has_keys", True):
        return
    for ei in sorted(shards):
        try:
            rep.cash[str(ei)] = str(units.dec(ctx.client.balance(ei)["balance_dollars"]))
        except Exception as exc:
            rep.warnings.append(f"cash on shard {ei} could not be read ({type(exc).__name__})")


def _reconcile_ticker(ctx: _Ctx, t: str, rep: ReconcileReport) -> None:
    first = _pass(ctx, t, rep, first=True)
    if not first:
        return
    since = ctx.ledger.last_activity_ts(t)
    fresh = _wait_fresh(ctx, since)
    if not fresh:
        rep.warnings.append(f"{t}: portfolio data not confirmed fresh (as_of_time is behind the last order or unreadable)")
    ctx.sleep(ctx.lag_recheck_s)
    if not fresh:
        fresh = _fresh_now(ctx, since)                         # it may have caught up while we waited 5 s
    second = _pass(ctx, t, rep, first=False)
    for d in first:
        if d in second:
            if fresh:                                          # as_of_time is past the event and two reads agree
                if d not in rep.mismatches:
                    rep.mismatches.append(d)
            else:                                              # the data lags: this may be the lag itself, not a mismatch
                if d not in rep.unconfirmed:
                    rep.unconfirmed.append(d)
                    rep.warnings.append(f"{d} (on both reads, but the portfolio data is not confirmed fresh: not declared a "
                                        "mismatch, no halt; the next reconcile looks again)")
                if t not in rep.incomplete:
                    rep.incomplete.append(t)
        else:
            rep.lag_cleared.append(d)
    for d in second:
        if d not in first and d not in rep.warnings:
            rep.warnings.append(f"{d} (seen once; not yet confirmed)")


def _pass(ctx: _Ctx, t: str, rep: ReconcileReport, first: bool) -> list[str]:
    """One read-and-compare pass over ``t``. Returns the discrepancies (lag candidates); identity problems go straight
    to ``rep.mismatches``."""
    client, ledger = ctx.client, ctx.ledger
    _sync_orders(ctx, t, rep)
    unresolved = ledger.unresolved_orders([t])
    for row in unresolved:
        _judge_unresolved(ctx, t, row, rep, first)

    fills = _items(client.fills(ticker=t), "fills")
    ours, foreign = ledger.split_fills(fills)
    rep.fills_added += ledger.upsert_fills(ours)
    rep.foreign_fills = max(rep.foreign_fills, len({str(f.get("fill_id") or f.get("trade_id")) for f in foreign}))
    foreign_net = sum((_signed(f) for f in foreign), D0)
    ordered = {o["order_id"]: o for o in ledger.orders_for(t) if o["order_id"]}
    for f in ours:
        o = ordered.get(f.get("order_id"))
        if o is not None and f.get("is_taker") is False and o["time_in_force"] in IMMEDIATE_TIFS:
            msg = f"fill {str(f.get('fill_id'))[:8]} of IOC order {_short(o['client_order_id'])} reports is_taker=false"
            if msg not in rep.warnings:
                rep.warnings.append(msg)

    disc: list[str] = []
    for o in ledger.orders_for(t):
        if not o["order_id"]:
            continue
        if o["fill_count"] not in (None, ""):
            recorded = sum((D(f["count"]) for f in ledger.fills_for(order_id=o["order_id"])), D0)
            reported = D(o["fill_count"])
            if recorded != reported:
                disc.append(f"order {_short(o['client_order_id'])}: fills stored {plain(recorded)} != "
                            f"fill_count {plain(reported)}")
        if o["time_in_force"] in IMMEDIATE_TIFS and o["status"] == "resting":
            disc.append(f"IOC order {_short(o['client_order_id'])} is resting on the exchange")
    disc.extend(_position_discrepancy(ctx, t, foreign_net, rep))
    return disc


def _sync_orders(ctx: _Ctx, t: str, rep: ReconcileReport) -> None:
    ledger = ctx.ledger
    for o in _items(ctx.client.orders(ticker=t), "orders"):
        info = ledger.apply_exchange_order(o)
        if info is None:
            continue                                           # not ours (a manual order)
        coid = info["client_order_id"]
        if info["previous"] in UNRESOLVED_STATUSES and info["status"] not in UNRESOLVED_STATUSES:
            if coid not in rep.adopted:
                rep.adopted.append(coid)
        elif info["previous"] != info["status"] and coid not in rep.updated:
            rep.updated.append(coid)
        for p in info["problems"]:
            msg = f"order {_short(coid)}: {p}"
            if msg not in rep.mismatches:
                rep.mismatches.append(msg)


def _judge_unresolved(ctx: _Ctx, t: str, row: Mapping[str, Any], rep: ReconcileReport, first: bool) -> None:
    """An order whose outcome is unknown and that the exchange's order list does not show (yet)."""
    ledger = ctx.ledger
    coid = row["client_order_id"]
    sent = row["sent_ts"] if row["sent_ts"] is not None else row["created_ts"]
    if ctx.now() - sent < ctx.pending_s:
        if coid not in rep.pending:
            rep.pending.append(coid)
        return
    if not first:
        return
    _wait_fresh(ctx, sent)
    _sync_orders(ctx, t, rep)                                  # one more read after the data caught up
    current = ledger.order(coid)
    if current is None or current["status"] not in UNRESOLVED_STATUSES:
        return
    if current["status"] == "duplicate":
        msg = f"order {_short(coid)}: the exchange said the client_order_id exists, but no such order is listed"
        if msg not in rep.mismatches:
            rep.mismatches.append(msg)
        return
    why = f"not found on the exchange after {ctx.now() - sent:.0f} s and a fresh read; presumed not placed (never resent)"
    ledger.mark_presumed_not_placed(coid, why)
    rep.presumed_not_placed.append(coid)
    if current["status"] != "intent" or current["sent_ts"] is not None:
        rep.halting.append(coid)                               # it was sent: its absence is a halting event
    else:
        rep.warnings.append(f"order {_short(coid)} was committed but never sent (crash before the send)")


def _position_discrepancy(ctx: _Ctx, t: str, foreign_net: Decimal, rep: ReconcileReport) -> list[str]:
    ledger, client = ctx.ledger, ctx.client
    if t in ledger.settled_tickers():
        return []                                              # a settled position leaves the unsettled listing
    ex_pos = D0
    for row in _items(client.positions(ticker=t), "market_positions"):
        if row.get("ticker") == t:
            ex_pos += units.dec(row.get("position_fp") or "0")
    ours = ledger.position(t, include_unrecorded=False)
    expected = ours + foreign_net
    if abs(ex_pos - expected) <= POSITION_TOLERANCE:
        return []
    if ex_pos == 0:
        try:
            settled = _items(client.settlements(ticker=t), "settlements")
        except Exception:
            settled = []
        if any(s.get("ticker") == t for s in settled):
            note = f"{t}: settled on the exchange; run settle() to record it"
            if note not in rep.warnings:
                rep.warnings.append(note)
            return []
    return [f"position on {t}: exchange {plain(ex_pos)} != ledger {plain(ours)} + foreign {plain(foreign_net)}"]


# ============================================================================================ settle
def settle(client: Any, ledger: Any, tickers: Iterable[str] | None = None, *, halt: bool = True) -> SettleReport:
    """Persist the settlement rows of ``tickers`` (default: every unsettled ticker of the ledger).

    Only tickers this ledger traded are recorded. A ticker whose settlement is not published yet is ``pending``
    (call again later). Idempotent: a recorded settlement is simply updated.
    """
    rep = SettleReport()
    wanted = sorted({str(t) for t in (tickers if tickers is not None else ledger.unsettled_tickers())})
    auth_failed = False
    for t in wanted:
        if not ledger.orders_for(t) and not ledger.fills_for(ticker=t):
            rep.skipped.append(t)
            continue
        try:
            rows = [r for r in _items(client.settlements(ticker=t), "settlements") if r.get("ticker") == t]
        except AuthError as exc:                   # a rejected signature: stop, do not go on asking
            auth_failed = True
            rep.incomplete.append(t)
            rep.warnings.append(f"{t}: authentication refused ({exc})")
            break
        except Exception as exc:
            rep.incomplete.append(t)
            rep.warnings.append(f"{t}: settlements could not be read ({type(exc).__name__}: {exc})")
            continue
        if not rows:
            rep.pending.append(t)
            continue
        foreign: list[Mapping[str, Any]] | None = []
        try:                                       # our fills first, so the P&L comes from them and not the settlement row
            ours, foreign = ledger.split_fills(_items(client.fills(ticker=t), "fills"))
            ledger.upsert_fills(ours)
        except AuthError as exc:
            auth_failed = True
            rep.incomplete.append(t)
            rep.warnings.append(f"{t}: authentication refused ({exc})")
            break
        except Exception as exc:
            foreign = None
            rep.warnings.append(f"{t}: fills could not be read ({type(exc).__name__}); P&L may come from the settlement row")
        for res in ledger.record_settlements(rows):
            rep.recorded.append(res)
            if res.get("fee_note"):
                rep.warnings.append(f"{t}: {res['fee_note']}")
            if res["check"] != "ok":
                _explain_gap(ledger, t, res, foreign, rep)
    reasons = []
    if rep.mismatches:
        reasons.append("settlement mismatch: " + "; ".join(rep.mismatches[:3])[:380])
    if auth_failed:
        reasons.append("authentication refused while settling")
    if reasons and halt:
        rep.halted = _halt(ledger, "; ".join(reasons)[:440], {"tickers": wanted})
    _event(ledger, "settle", rep.as_dict())
    return rep


def _explain_gap(ledger: Any, t: str, res: Mapping[str, Any], foreign: list[Mapping[str, Any]] | None,
                 rep: SettleReport) -> None:
    """The exchange's revenue differs from what our fills say: is it a manual trade on the same ticker?"""
    result = res.get("market_result")
    revenue = res.get("revenue_cents")
    if foreign:
        total = ledger.ticker_agg(t).net + sum((_signed(f) for f in foreign), D0)
        held = total if result == "yes" else -total
        expected = max(D0, held) * 100
        if result in ("yes", "no") and revenue is not None and expected == Decimal(revenue):
            ledger.update_settlement_check(t, "ok: foreign fills (a manual trade on this ticker) explain the revenue")
            rep.warnings.append(f"{t}: revenue includes a manual trade on this ticker; P&L counts only the bot's fills")
            return
    rep.mismatches.append(f"{t}: {res['check']}")


# ============================================================================================ cancel path
def cancel_path(client: Any, ledger: Any, reason: str, touched: Iterable[str] | None = None, *,
                clock: Callable[[], float] | None = None, sleep: Callable[[float], Any] = time.sleep,
                verify_timeout_s: float = VERIFY_TIMEOUT_S, lockout_s: float = LOCKOUT_S,
                backoff: tuple[float, ...] = VERIFY_BACKOFF_S) -> CancelReport:
    """Cancel everything of ours that could be resting, verify, and fall back to cancel-all if needed.

    Runs for every kind of stop (kill file, cap breach, mismatch, exception, Ctrl+C, normal exit). Each step is guarded:
    one failure never stops the next. Returns a :class:`CancelReport`; ``ok`` means a read showed nothing resting.
    """
    now = clock or getattr(ledger, "clock", None) or time.time
    open_rows = ledger.open_orders()
    tickers = sorted({str(t) for t in (touched or ())} | set(ledger.touched_tickers(run_only=True))
                     | {r["ticker"] for r in open_rows})
    rep = CancelReport(reason=str(reason)[:300], tickers=tickers)
    if getattr(client, "live", True) is False:
        rep.not_live = True
        rep.errors.append("the client is not live (dry-run): it never sent an order and cannot cancel one")
        _event(ledger, "cancel_path", rep.as_dict())
        return rep

    targets: dict[str, dict] = {}
    for r in open_rows:
        if r["order_id"]:
            targets[r["order_id"]] = {"order_id": r["order_id"], "exchange_index": r["exchange_index"],
                                      "ticker": r["ticker"], "client_order_id": r["client_order_id"], "source": "ledger"}
        elif r["time_in_force"] == "good_till_canceled":
            rep.unlocated.append(r["client_order_id"])         # may rest, but cannot be cancelled without an id
    ledger_ei = {r["ticker"]: r["exchange_index"] for r in open_rows if _is_int(r["exchange_index"])}
    for t in tickers:
        try:
            resting = _items(client.orders(ticker=t, status="resting"), "orders")
        except Exception as exc:
            rep.errors.append(f"{t}: resting orders could not be read ({type(exc).__name__})")
            continue
        for o in resting:
            oid = o.get("order_id")
            if not oid:
                continue
            ledger.apply_exchange_order(o)
            ei = o.get("exchange_index") if _is_int(o.get("exchange_index")) else ledger_ei.get(t)
            if oid in targets:
                if _is_int(o.get("exchange_index")) or not _is_int(targets[oid]["exchange_index"]):
                    targets[oid]["exchange_index"] = ei          # the exchange's own shard beats our row's
                continue
            targets[oid] = {"order_id": oid, "exchange_index": ei, "ticker": t,
                            "client_order_id": o.get("client_order_id"), "source": "exchange"}
    rep.unlocated = [c for c in rep.unlocated if not (ledger.order(c) or {}).get("order_id")]   # found ones are targets
    rep.targets = list(targets.values())

    for tg in rep.targets:
        if not _is_int(tg["exchange_index"]):
            rep.failed.append({"order_id": tg["order_id"], "outcome": "no_exchange_index",
                               "detail": "the shard is unknown, so the cancel cannot be routed"})
            continue
        try:
            res = client.cancel_order(tg["order_id"], tg["exchange_index"])
        except PermissionError as exc:
            rep.not_live = True
            rep.errors.append(f"cancel refused: {exc}")
            break
        except Exception as exc:
            rep.failed.append({"order_id": tg["order_id"], "outcome": "error", "detail": type(exc).__name__})
            continue
        if res.outcome == "ok":
            rep.cancelled.append(tg["order_id"])
        else:
            rep.failed.append({"order_id": tg["order_id"], "outcome": res.outcome, "status": res.status,
                               "detail": (res.error or {}).get("code") if isinstance(res.error, Mapping) else None})
            if res.outcome == "auth":
                rep.auth_failed = True
                break
    if rep.not_live or rep.auth_failed:
        _finish_cancel(client, ledger, rep)
        return rep

    leftover, verified = _verify_clear(client, ledger, tickers, now, sleep, verify_timeout_s, backoff)
    rep.leftover, rep.remaining, rep.verified = leftover, leftover, verified
    could_rest = bool(rep.targets or rep.unlocated)           # something of ours may still be resting
    if leftover or (not verified and could_rest):
        rep.cancel_all_used = True
        log.warning("cancel path (%s): falling back to cancel_all (leftover=%s, verified=%s)", reason, leftover, verified)
        try:
            res = client.cancel_all()
            rep.cancel_all_outcome = res.outcome
            if res.outcome == "auth":
                rep.auth_failed = True
        except PermissionError as exc:
            rep.not_live = True
            rep.errors.append(f"cancel_all refused: {exc}")
        except Exception as exc:
            rep.cancel_all_outcome = f"error: {type(exc).__name__}"
        until = float(now()) + float(lockout_s)
        rep.lockout_until = until
        try:
            ledger.set_lockout(until, f"cancel_all after: {reason}"[:300])
        except Exception:
            log.exception("could not persist the lockout")
        leftover2, verified2 = _verify_clear(client, ledger, tickers, now, sleep, verify_timeout_s / 2.0, backoff)
        rep.remaining, rep.verified = leftover2, verified2
        if leftover2 or not verified2:
            rep.errors.append("orders may still be resting after cancel_all")
            _halt(ledger, "orders may still be resting after cancel_all", {"tickers": tickers})
    _finish_cancel(client, ledger, rep)
    return rep


def _verify_clear(client: Any, ledger: Any, tickers: list[str], now: Callable[[], float],
                  sleep: Callable[[float], Any], timeout_s: float, backoff: tuple[float, ...]) -> tuple[list[str], bool]:
    """Read ``orders(ticker, status=resting)`` for every ticker with bounded backoff until nothing rests.

    Returns (order ids still resting after the last read, True if every read of the last pass succeeded).
    """
    deadline = float(now()) + timeout_s
    attempt = 0
    max_attempts = len(backoff) + 8                    # a hard bound even if the clock never advances
    while True:
        left: list[str] = []
        clean = True
        for t in tickers:
            try:
                for o in _items(client.orders(ticker=t, status="resting"), "orders"):
                    ledger.apply_exchange_order(o)
                    left.append(str(o.get("order_id")))
            except Exception:
                clean = False
        if clean and not left:
            return [], True
        if float(now()) >= deadline or attempt >= max_attempts:
            return left, clean
        sleep(backoff[min(attempt, len(backoff) - 1)])
        attempt += 1


def _finish_cancel(client: Any, ledger: Any, rep: CancelReport) -> None:
    """Bring ledger statuses up to date (best effort) and write the report to the audit log."""
    for t in rep.tickers:
        try:
            for o in _items(client.orders(ticker=t), "orders"):
                ledger.apply_exchange_order(o)
        except Exception:
            pass
    _event(ledger, "cancel_path", rep.as_dict())
    level = logging.INFO if rep.ok else logging.WARNING
    log.log(level, "cancel path (%s): %d target(s), %d cancelled, verified=%s, cancel_all=%s", rep.reason,
            len(rep.targets), len(rep.cancelled), rep.verified, rep.cancel_all_used)


# ============================================================================================ exit guard
class ExitGuard:
    """Runs :func:`cancel_path` exactly once when a run ends, however it ends.

    Use as a context manager around the whole run (normal exit, an exception and Ctrl+C all run it, and the original
    exception is never swallowed). ``register_atexit()`` adds interpreter exit; ``install_signal_handlers()`` turns
    SIGBREAK (Windows Ctrl+Break) and SIGTERM into KeyboardInterrupt so the same path runs.

    ``touched`` is an iterable of tickers or a callable returning one, read when the guard fires.
    """

    def __init__(self, client: Any, ledger: Any, *, touched: Iterable[str] | Callable[[], Iterable[str]] = (),
                 **kwargs: Any):
        self.client = client
        self.ledger = ledger
        self.touched = touched
        self.kwargs = kwargs
        self.report: CancelReport | None = None
        self._done = False
        self._old_handlers: dict[int, Any] = {}

    def run(self, reason: str) -> CancelReport | None:
        if self._done:
            return self.report
        self._done = True
        try:
            tickers = self.touched() if callable(self.touched) else self.touched
            self.report = cancel_path(self.client, self.ledger, reason, list(tickers or ()), **self.kwargs)
        except Exception:
            log.exception("the cancel path itself failed (%s)", reason)
        return self.report

    def __enter__(self) -> "ExitGuard":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            why = "finished"
        elif issubclass(exc_type, KeyboardInterrupt):
            why = "interrupted (Ctrl+C)"
        else:
            why = f"error: {exc_type.__name__}"
        self.run(why)
        self.restore_signal_handlers()
        return False

    def register_atexit(self) -> None:
        atexit.register(self.run, "interpreter exit")

    def unregister_atexit(self) -> None:
        atexit.unregister(self.run)

    def install_signal_handlers(self) -> list[int]:
        """Best effort (main thread only): SIGBREAK / SIGTERM raise KeyboardInterrupt. Returns the signals installed."""
        def handler(signum: int, frame: Any) -> None:
            raise KeyboardInterrupt(f"signal {signum}")

        installed = []
        for name in ("SIGBREAK", "SIGTERM"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                self._old_handlers[int(sig)] = signal.signal(sig, handler)
                installed.append(int(sig))
            except (ValueError, OSError):
                continue
        return installed

    def restore_signal_handlers(self) -> None:
        for sig, old in list(self._old_handlers.items()):
            try:
                signal.signal(sig, old)
            except (ValueError, OSError):
                pass
        self._old_handlers.clear()


# ============================================================================================ one-shot for the CLI
def reconcile_all(client: Any, ledger: Any, **kwargs: Any) -> dict:
    """``kpm exec reconcile``: reconcile every open or unsettled ticker, then record the settlements that are out."""
    rec = reconcile(client, ledger, None, **kwargs)
    keep = {k: v for k, v in kwargs.items() if k in ("halt",)}
    stl = settle(client, ledger, None, **keep)
    return {"reconcile": rec.as_dict(), "settle": stl.as_dict(), "ledger": ledger.summary(),
            "ok": rec.ok and stl.ok}
