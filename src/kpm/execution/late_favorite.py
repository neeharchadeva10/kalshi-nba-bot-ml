"""late_favorite: the first live strategy, ported from github.com/aulekator/Polymarket-BTC-15-Minute-Trading-Bot.

THE RULE (exact; the constants below are not configurable)
  Each KXBTC15M market lives 900 s. The decision window is 780 to 840 s into it, i.e. between 120 and 60 seconds
  before ``close_time`` (both ends inclusive). The rule is applied ONCE per window, to the FIRST fresh, two-sided order
  book inside that window (a stale book, an empty side or a crossed book is not a book to decide on: the next poll is
  the first fresh one):
    YES mid > 0.60   buy 1 YES at the current YES ask
    YES mid < 0.40   buy 1 NO  at the current NO ask
    otherwise        skip the window (a later book that crosses a threshold is NOT traded: it is not the rule)
  where YES mid = (best YES bid + YES ask) / 2 and YES ask = 1 - best NO bid. The NO ask is 1 - best YES bid.
  Orders are immediate-or-cancel taker orders priced EXACTLY at the current ask and held to settlement. An unfilled
  IOC is retried on the next poll inside the window with a NEW intent that passes every check again: at most 3
  attempts per window, never more than one filled trade per window, and a retry buys the same outcome as the
  first detection (and only while the rule still says so). A refusal by Safety is not an attempt (no order exists)
  and is re-checked only every few seconds.

HONEST TRACKING
  * Paper twin: at the first detection one bot_opportunities row is written with the detection ask as its price
    (paper_cost, paper_fees; paper_edge stays NULL because a late favourite has no riskless edge). When the market
    settles, paper_realized_pnl = payout - cost - fees.
  * Live: the fill price, fees and the detection-to-ack latency go on the same row (live_cost, live_fees,
    latency_ms) and the realized P&L comes from the market result once it settles (cross-checked against the
    ledger's settlement record). bot_orders rows carry every order (dry-run rows in watch mode).
  * A partial fill (an IOC that filled 0.40 of 1) is the window's one filled trade; the paper twin is then scaled to the
    contracts that filled (same detection price), so the row's live and paper figures, and the dashboard's paired
    live-vs-paper comparison, cover the same trade.
  * bot_runs.strategy = 'late_favorite'; the heartbeat is written at least every 2 s.

MODES
  * Watch (the default) needs no keys. Public reads only: market discovery, order books and settlement results.
    It runs the full rule, writes paper twins and dashboard state, and never sends anything.
  * Live needs ctx.safety, ctx.ledger and a client built with live=True, and trading.enabled: true. Every order is
    an IOC. (side, YES price) comes only from units.to_book(). The intent is committed to the ledger before it is
    sent, and it must pass the combined loss rule and Safety.check_entry first. The commits take database time, so
    just before the send the kill file, the halted flag, the decision window and the book's age are checked once more;
    if any fails the intent is marked "not sent" and nothing leaves the process. A write whose outcome is unknown
    is looked up, never resent; if it cannot be found, Safety is tripped and the bot stops. A 401/403 on any
    authenticated read trips Safety and stops the bot.
  * smoke=True: one market discovery plus one decision pass on public data, then exit. It never trades and never
    writes opportunities or orders.

LOSS CAP
  One account-wide cap (trading.max_loss_usd, at most $5.00): realized loss (all runs) + open worst case + the
  worst case of the new order must stay within it. When no order fits and nothing is open that could free room,
  the bot trips Safety and stops. All ceilings are enforced here as well as in Safety, and a configuration above a
  ceiling refuses to start.

Interface: LateFavoriteBot(ctx, cfg, live, clock, sleep).run(hours=None, smoke=False). ctx is any object with the
attributes of LateContext. cfg is the settings 'trading' block, or the whole settings dict (its optional
``late_favorite:`` sub-block sets contracts, poll_interval_s, max_book_age_s, max_attempts_per_window (or max_attempts),
settle_poll_s and settle_wait_s; window_start_s, window_end_s, up_threshold and down_threshold only repeat the fixed
rule and refuse to start if they differ from it). hours is the wall-clock run length: no window is started that would
end after it.
"""
from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping

from kpm.core.config import PROJECT_ROOT
from kpm.execution import units
from kpm.execution.botstate import BotState
from kpm.execution.client import AuthError

log = logging.getLogger(__name__)

STRATEGY = "late_favorite"
SERIES = "KXBTC15M"
KIND_YES = "late favorite YES"
KIND_NO = "late favorite NO"

# --- the rule (user decision; fixed) -------------------------------------------------------------------------------
WINDOW_LENGTH_S = 900.0          # a KXBTC15M market lives 15 minutes
DECISION_START_S = 780.0         # seconds into the window: 120 s before close
DECISION_END_S = 840.0           # 60 s before close
YES_ABOVE = Decimal("0.60")      # mid strictly above: buy YES
NO_BELOW = Decimal("0.40")       # mid strictly below: buy NO
OPENS_BEFORE_CLOSE_S = WINDOW_LENGTH_S - DECISION_START_S     # 120
ENDS_BEFORE_CLOSE_S = WINDOW_LENGTH_S - DECISION_END_S        # 60

# The rule is not configurable. config/settings.yaml repeats it under trading.late_favorite so that a reader sees what
# runs; a different value there refuses to start instead of being ignored without a word.
RULE_MIRROR = {"window_start_s": DECISION_START_S, "window_end_s": DECISION_END_S,
               "up_threshold": YES_ABOVE, "down_threshold": NO_BELOW}

# --- ceilings (user decisions); Safety enforces them too, and a configuration above one refuses to start -------------
CONTRACTS_CEILING = Decimal("1")
ATTEMPTS_CEILING = 3
MAX_LOSS_CEILING = Decimal("5.00")
COST_PER_ORDER_CEILING = Decimal("1.00")
PRICE_MIN, PRICE_MAX = Decimal("0.001"), Decimal("0.999")   # user decision 2026-10-06: take 99-99.9c favorites

# --- operational timing --------------------------------------------------------------------------------------------
HEARTBEAT_S = 2.0                # the dashboard expects a heartbeat at least every 5 s
TICK_S = 1.0                     # longest sleep between kill-switch checks
DISCOVERY_RETRY_S = 5.0
NEXT_MARKET_WARN_S = 150.0       # no new market for this long (the normal gap after a window is up to 60 s): report it
PREPARE_S = 20.0                # re-read the market this long before the decision window opens
WARM_LEAD_S = 3.0                # Safety.preflight() this long before the window opens (its caches live 5-15 s)
WARM_EVERY_S = 4.0               # and this often while the window waits for a signal
BLOCK_RETRY_S = 3.0              # after a Safety refusal, check again no sooner than this
SETTLE_DELAY_S = 20.0            # first result check this long after the close
SETTLE_GIVE_UP_S = 6 * 3600.0
RECOVER_MIN_AGE_S = 600.0        # opportunities of earlier runs this old (their market is long closed) ...
RECOVER_MAX_AGE_S = 7 * 86400.0  # ... and at most this old are settled at startup if still pending
BALANCE_EVERY_S = 60.0
LOOKUP_TRIES = 3
LOOKUP_WAIT_S = 1.0
DATA_FRESH_WAIT_S = 10.0         # bounded wait for the portfolio data to catch up before looking an order up
ERROR_SHOW_S = 600.0             # last_error stays in the heartbeat this long
ACTIVE_STATUSES = frozenset({"active", "open"})
SETTLED_RESULTS = ("yes", "no")
# A ledger order in one of these states (or with any fill) means the window was already traded.
TRADED_STATUSES = frozenset({"intent", "unknown", "duplicate", "acked", "resting", "executed"})

D0, D1, D2 = Decimal(0), Decimal(1), Decimal(2)


# ------------------------------------------------------------------------------------------------ value helpers

def _d(v: Any) -> Decimal:
    """Exact Decimal from a Decimal, int or decimal string. Floats and bools are refused: they are not exact."""
    if isinstance(v, (bool, float)):
        raise TypeError(f"expected a Decimal, int or decimal string, got {type(v).__name__} {v!r}")
    if isinstance(v, Decimal):
        out = v
    elif isinstance(v, int):
        out = Decimal(v)
    elif isinstance(v, str):
        try:
            out = Decimal(v.strip())
        except InvalidOperation:
            raise ValueError(f"not a decimal string: {v!r}") from None
    else:
        raise TypeError(f"expected a Decimal, int or decimal string, got {type(v).__name__}")
    if not out.is_finite():
        raise ValueError(f"not a finite number: {v!r}")
    return out


def _num(v: Any) -> Decimal | None:
    """Lenient parse of an exchange field. None, '' and unparseable values give None."""
    if v is None or v == "":
        return None
    try:
        return _d(v)
    except (TypeError, ValueError):
        return None


def _cfg_dec(v: Any, name: str) -> Decimal:
    """A config number. YAML may give a float, which goes through its shortest repr (5.0 -> '5.0')."""
    if v is None or isinstance(v, bool):
        raise ValueError(f"{name} must be a number")
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError(f"{name} must be finite")
        return Decimal(repr(v))
    try:
        return _d(v)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}: {exc}") from None


def _dec_any(v: Any) -> Decimal:
    """A ledger amount: Decimal, int, decimal string or float."""
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError(f"not finite: {v!r}")
        return Decimal(repr(v))
    return _d(v)


def _iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _items(resp: Any, key: str) -> list:
    if isinstance(resp, Mapping):
        v = resp.get(key)
        return list(v) if isinstance(v, (list, tuple)) else []
    if isinstance(resp, (list, tuple)):
        return list(resp)
    return []


def _str_or_none(v: Any) -> str | None:
    return None if v is None or v == "" else str(v)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


# ------------------------------------------------------------------------------------------------ configuration

def _check_rule_mirror(lf: Mapping) -> None:
    """The rule keys in settings.yaml (window_start_s, window_end_s, up_threshold, down_threshold) must repeat the
    fixed rule exactly. They are documentation, not settings: any other value refuses to start."""
    for key, fixed in RULE_MIRROR.items():
        if lf.get(key) is None:
            continue
        got = _cfg_dec(lf[key], f"late_favorite.{key}")
        if got != Decimal(str(fixed)):
            raise ValueError(f"late_favorite.{key} is {got} but the rule is fixed at {fixed}: the user's decision is "
                             f"not configurable (change the code and its tests to change the rule)")


def _attempts_setting(lf: Mapping) -> int:
    """late_favorite.max_attempts_per_window, or its short name max_attempts (both given: they must agree)."""
    raw = [lf[k] for k in ("max_attempts_per_window", "max_attempts") if lf.get(k) is not None]
    vals = [_cfg_dec(v, "late_favorite.max_attempts_per_window") for v in raw]
    if len(set(vals)) > 1:
        raise ValueError("late_favorite.max_attempts and late_favorite.max_attempts_per_window disagree")
    if not vals:
        return ATTEMPTS_CEILING
    if vals[0] != vals[0].to_integral_value():
        raise ValueError(f"late_favorite.max_attempts_per_window must be a whole number, got {vals[0]}")
    return int(vals[0])


# ------------------------------------------------------------------------------------------------ the rule

@dataclass(frozen=True)
class Quote:
    """The top of a REST order book. REST arrays are ascending, so the best bid of a side is its LAST level.

    YES ask = 1 - best NO bid (and its size is the NO bid's size); NO ask = 1 - best YES bid.
    """
    yes_bid: Decimal
    yes_bid_size: Decimal
    no_bid: Decimal
    no_bid_size: Decimal

    @property
    def yes_ask(self) -> Decimal:
        return D1 - self.no_bid

    @property
    def no_ask(self) -> Decimal:
        return D1 - self.yes_bid

    @property
    def mid(self) -> Decimal:
        """YES mid, exact: (YES bid + YES ask) / 2."""
        return (self.yes_bid + self.yes_ask) / D2


def book_age(book: Mapping | None, now: float) -> float:
    try:
        return float(now) - float(book["fetched_at"])        # type: ignore[index]
    except (KeyError, TypeError, ValueError):
        return math.inf


def read_quote(book: Mapping) -> Quote | None:
    """Best bid and size of each side of a parsed book ``{"yes": [...], "no": [...]}`` (levels ascending).

    Levels may be (Decimal, Decimal) pairs, as ExecClient.orderbook returns, or REST ``[price, size]`` strings.
    None when a side is empty (no mid) or the book is crossed or locked (best YES bid + best NO bid >= 1: stale or
    transitional data that no rule may act on). A side that is not strictly ascending raises ValueError: the API
    changed, and the bot must not guess which level is the best.
    """
    yes = units.best_level(book.get("yes"))
    no = units.best_level(book.get("no"))
    if yes is None or no is None:
        return None
    if yes[0] + no[0] >= D1:
        return None
    return Quote(yes_bid=yes[0], yes_bid_size=yes[1], no_bid=no[0], no_bid_size=no[1])


def rule(quote: Quote) -> dict | None:
    """The book part of the rule, without the time gate: ``{outcome, ask, size, mid}`` or None (skip)."""
    mid = quote.mid
    if mid > YES_ABOVE:
        return {"outcome": "yes", "ask": quote.yes_ask, "size": quote.no_bid_size, "mid": mid}
    if mid < NO_BELOW:
        return {"outcome": "no", "ask": quote.no_ask, "size": quote.yes_bid_size, "mid": mid}
    return None


def elapsed_in_window(now: float, close: float) -> float:
    """Seconds since the market's 900 s window opened."""
    return float(now) - (float(close) - WINDOW_LENGTH_S)


def in_decision_window(now: float, close: float) -> bool:
    return DECISION_START_S <= elapsed_in_window(now, close) <= DECISION_END_S


def decide(book: Mapping, now: float, close_ts: float, *, max_book_age_s: float | None = None) -> dict | None:
    """The late-favourite rule on one order book.

    Returns None (skip) or ``{"outcome": "yes"|"no", "ask": Decimal, "size": Decimal, "mid": Decimal}`` where ask is
    the price of ONE contract of that outcome and size the contracts displayed at that ask. Only inside the
    decision window (780-840 s into the 900 s window, both ends inclusive); a book older than ``max_book_age_s``
    (when given) is not fresh and gives None.
    """
    if not in_decision_window(now, close_ts):
        return None
    if max_book_age_s is not None and book_age(book, now) > max_book_age_s:
        return None
    quote = read_quote(book)
    return None if quote is None else rule(quote)


# ------------------------------------------------------------------------------------------------ markets

def close_ts(market: Mapping) -> float | None:
    s = market.get("close_time")
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        return float(units.parse_rfc3339(s))
    except (TypeError, ValueError):
        return None


def _active(market: Mapping) -> bool:
    s = market.get("status")
    return s is None or str(s).lower() in ACTIVE_STATUSES


def _unwrap_market(m: Any) -> Mapping | None:
    if isinstance(m, Mapping) and isinstance(m.get("market"), Mapping):
        m = m["market"]
    return m if isinstance(m, Mapping) else None


# ------------------------------------------------------------------------------------------------ orders

def entry_spec(market: Mapping, outcome: str, outcome_price: Decimal, count: Decimal, *, purpose: str = "entry",
               opportunity_id: int | None = None) -> dict:
    """One IOC BUY of ``outcome`` at ``outcome_price``: the spec handed to Safety and Ledger.new_intent().

    (side, yes_price) comes only from units.to_book(): buying NO at 0.054 is ('ask', 0.946), which is a sale of
    YES into the best YES bid.
    """
    ei = market.get("exchange_index")
    if not _is_int(ei) or ei < 0:
        raise ValueError(f"market {market.get('ticker')} has no exchange_index")
    side, yes_price = units.to_book(outcome, outcome_price)
    return {"strategy": STRATEGY, "purpose": purpose, "role": None, "opportunity_id": opportunity_id,
            "ticker": str(market["ticker"]), "exchange_index": ei, "action": "buy", "outcome": outcome,
            "outcome_price": outcome_price, "side": side, "yes_price": yes_price, "count": count,
            "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross",
            "reduce_only": False}


def order_body(spec: Mapping, client_order_id: str) -> dict:
    """CreateOrderV2 body: YES-book side, YES price as a 4-dp dollar string, count as a 2-dp contract string."""
    return {"ticker": spec["ticker"], "client_order_id": client_order_id, "side": spec["side"],
            "price": units.fmt_price(spec["yes_price"]), "count": units.fmt_count(spec["count"]),
            "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross",
            "exchange_index": spec["exchange_index"]}


def _order_status(outcome: str | None, fill: Decimal | None, count: Decimal) -> str:
    if fill is None:
        return "unknown"
    if outcome in ("rejected", "throttled"):
        return outcome
    if outcome == "auth":
        return "auth_error"
    if fill >= count:
        return "executed"
    return "partial" if fill > 0 else "canceled"      # an IOC remainder is cancelled, never resting


def _verdict(res: Any) -> tuple[bool, str]:
    """Normalise a Safety verdict to (ok, reason). Anything that does not clearly say yes is a no (fail closed)."""
    if isinstance(res, bool):
        return res, ""
    if isinstance(res, (tuple, list)) and res:
        return res[0] is True, (str(res[1]) if len(res) > 1 and res[1] else "")
    if isinstance(res, Mapping):
        for k in ("ok", "allowed", "passed", "approved"):
            if k in res:
                return res[k] is True, str(res.get("reason") or res.get("why") or "")
    for k in ("ok", "allowed", "passed", "approved"):
        if hasattr(res, k):
            return getattr(res, k) is True, str(getattr(res, "reason", "") or getattr(res, "why", "") or "")
    return False, f"unrecognised safety verdict {type(res).__name__}"


@dataclass
class _Outcome:
    """Result-shaped stand-in for a write that raised instead of returning a Result. Its outcome is unknown."""
    outcome: str
    status: int | None = None
    data: Any = None
    error: Any = None


@dataclass
class LateContext:
    """What LateFavoriteBot needs. Any object with these attributes works (e.g. types.SimpleNamespace)."""
    client: Any                     # ExecClient: public reads always; authenticated reads and writes when live
    state: BotState                 # dashboard tables for this run
    ledger: Any = None              # Ledger, required when live
    safety: Any = None              # Safety, required when live; Safety.startup() must have passed already
    reconcile: Any = None           # namespace with reconcile(), settle() and cancel_path(); default kpm.execution.reconcile
    env_name: str | None = None
    key_fp: str | None = None


@dataclass
class _Window:
    """One KXBTC15M market from discovery to its last decision poll."""
    market: dict
    ticker: str
    close_ts: float
    start_ts: float                  # first decision instant (close - 120 s)
    end_ts: float                    # last decision instant (close - 60 s)
    outcome: str | None = None       # locked at the first detection
    opp_id: int | None = None
    detected_ts: float | None = None
    detect_ask: Decimal | None = None
    detect_mid: Decimal | None = None
    prior_attempts: int = 0          # orders an earlier run already sent for this market (from the ledger)
    attempts: int = 0                # intents this run created (each counts against the budget of 3, sent or not)
    sent: int = 0                    # of those, the orders that really left this process
    skipped: bool = False            # the first fresh book did not fire the rule: the window is skipped
    skip_elapsed_s: float | None = None
    filled: Decimal = D0
    records: list = field(default_factory=list)      # filled orders, shared with the pending settlement item
    unknown: bool = False
    blocked: str | None = None
    next_try: float = 0.0
    done: bool = False
    polls: int = 0
    fresh_polls: int = 0
    last_quote: Quote | None = None
    last_ack_ts: float | None = None
    next_warm: float = 0.0
    block_reasons: set = field(default_factory=set)


# ------------------------------------------------------------------------------------------------ the bot

class LateFavoriteBot:
    def __init__(self, ctx: Any, cfg: Mapping | None, live: bool = False, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Any] = time.sleep, *,
                 fee_fn: Callable[[Decimal, Decimal], Decimal] | None = None,
                 kill_file: str | Path | None = None) -> None:
        self.ctx = ctx
        self.client = ctx.client
        self.state: BotState = ctx.state
        self.ledger = getattr(ctx, "ledger", None)
        self.safety = getattr(ctx, "safety", None)
        self.reconciler = getattr(ctx, "reconcile", None)
        self.live = bool(live)
        self.clock, self.sleep = clock, sleep
        self.fee_fn = fee_fn or units.taker_fee

        cfg = cfg or {}
        trading = cfg["trading"] if isinstance(cfg.get("trading"), Mapping) else cfg
        lf = trading.get("late_favorite") or {}
        self.trading_cfg = dict(trading)
        _check_rule_mirror(lf)
        self.contracts = _cfg_dec(lf.get("contracts", 1), "late_favorite.contracts")
        if not (D0 < self.contracts <= CONTRACTS_CEILING) or self.contracts != self.contracts.quantize(Decimal("0.01")):
            raise ValueError(f"late_favorite.contracts must be a multiple of 0.01 in (0, {CONTRACTS_CEILING}], "
                             f"got {self.contracts}")
        self.max_attempts = _attempts_setting(lf)
        self.poll_s = float(lf.get("poll_interval_s", 1.0))
        self.max_book_age_s = float(lf.get("max_book_age_s", 2.0))
        self.settle_poll_s = float(lf.get("settle_poll_s", 15.0))
        self.settle_wait_s = float(lf.get("settle_wait_s", 600.0))
        for name, ok in (("late_favorite.max_attempts_per_window", 1 <= self.max_attempts <= ATTEMPTS_CEILING),
                         ("late_favorite.poll_interval_s", 0.2 <= self.poll_s <= 5.0),
                         ("late_favorite.max_book_age_s", 0 < self.max_book_age_s <= 10.0),
                         ("late_favorite.settle_poll_s", self.settle_poll_s > 0),
                         ("late_favorite.settle_wait_s", self.settle_wait_s >= 0)):
            if not ok:
                raise ValueError(f"{name} is out of range")
        self.max_loss = _cfg_dec(trading.get("max_loss_usd", MAX_LOSS_CEILING), "trading.max_loss_usd")
        if not (D0 < self.max_loss <= MAX_LOSS_CEILING):
            raise ValueError(f"trading.max_loss_usd must be in (0, {MAX_LOSS_CEILING}], got {self.max_loss}")
        if trading.get("max_cost_per_order_usd") is not None:
            cap = _cfg_dec(trading["max_cost_per_order_usd"], "trading.max_cost_per_order_usd")
            if not (D0 < cap <= COST_PER_ORDER_CEILING):
                raise ValueError(f"trading.max_cost_per_order_usd must be in (0, {COST_PER_ORDER_CEILING}], got {cap}")
        if trading.get("max_contracts_per_order") is not None:
            cap = _cfg_dec(trading["max_contracts_per_order"], "trading.max_contracts_per_order")
            if not (D0 < cap <= CONTRACTS_CEILING):
                raise ValueError(f"trading.max_contracts_per_order must be in (0, {CONTRACTS_CEILING}], got {cap}")
        kf = Path(str(kill_file or trading.get("kill_switch_file") or "data/KILL"))
        self.kill_path = kf if kf.is_absolute() else Path(PROJECT_ROOT) / kf

        if self.live:
            self._check_live_gates(trading)
            if self.reconciler is None:
                from kpm.execution import reconcile as _reconcile
                self.reconciler = _reconcile

        self.summary: dict[str, Any] = {
            "windows": 0, "polls": 0, "stale_books": 0, "detections": 0, "paper_trades": 0, "live_attempts": 0,
            "live_fills": 0, "blocked": 0, "no_signal": 0, "settled": 0, "recovered": 0, "paper_wins": 0,
            "paper_losses": 0,
            "live_wins": 0, "live_losses": 0, "paper_pnl": D0, "live_pnl": D0, "outcomes": {}, "end_reason": None}
        self.window: _Window | None = None
        self.balance_usd: Decimal | None = None
        self.last_error: str | None = None
        self._last_error_ts = -math.inf
        self._exchange_index: int | None = None
        self._last_close_ts = -math.inf          # markets closing at or before this have been handled
        self._pending: list[dict] = []
        self._touched: set[str] = set()
        self._ledger_todo: dict[str, float] = {}     # live tickers whose settlement the ledger has not recorded yet
        self._ledger_next = 0.0
        self._live_pnl: dict[str, tuple[Decimal, set[str]]] = {}     # ticker -> (our P&L, our order ids), cross-checked
        self._tripped = False                        # this run tripped Safety (which writes the kill file itself)
        self._stop = False
        self._stop_reason: str | None = None
        self._hb_status: str | None = None
        self._hb_ts = -math.inf
        self._balance_ts = -math.inf
        self._warned: dict[str, float] = {}

    # -------------------------------------------------------------------------------------------- gates
    def _check_live_gates(self, trading: Mapping) -> None:
        problems = []
        if self.safety is None:
            problems.append("no Safety object (ctx.safety)")
        if self.ledger is None:
            problems.append("no Ledger (ctx.ledger)")
        if trading.get("enabled") is not True:
            problems.append("trading.enabled is not true in config/settings.yaml")
        if getattr(self.client, "live", None) is not True:
            problems.append("the client was not built with live=True")
        if self.safety is not None and getattr(self.safety, "started", True) is False:
            problems.append("Safety.startup() has not passed")
        if problems:
            raise PermissionError("live mode refused: " + "; ".join(problems))

    # -------------------------------------------------------------------------------------------- run
    def run(self, hours: float | None = None, smoke: bool = False) -> dict:
        """Trade (or watch) windows for ``hours`` of wall-clock time, or until stopped when hours is None.

        smoke=True: one market discovery and one decision pass on public data, then exit; it never trades.
        """
        if smoke and self.live:
            raise ValueError("smoke mode reads public data only; run it without live")
        if hours is not None and not (isinstance(hours, (int, float)) and not isinstance(hours, bool) and hours > 0):
            raise ValueError("hours must be a number > 0")
        mode = "live" if self.live else "watch"
        self.state.start(STRATEGY, mode, self._run_config(smoke), ts=self.clock())
        self._event("info", "start", f"late_favorite started in {mode} mode" + (" (smoke pass)" if smoke else ""),
                    self._run_config(smoke))
        self._heartbeat("waiting", force=True)
        reason = "finished"
        try:
            if smoke:
                self.summary["smoke"] = self._smoke()
                reason = "smoke pass done"
            elif self._kill_active():
                reason = "kill switch present at start"
                self._event("warn", "kill", f"{reason} ({self.kill_path}); delete the file and start again")
            else:
                deadline = None if hours is None else self.clock() + float(hours) * 3600.0
                self._startup_settle()
                self._recover_unsettled()
                while not self._stop:
                    w = self._next_window(deadline)
                    if w is None:
                        break
                    self._run_window(w)
                if self._stop_reason != "kill switch":
                    self._drain_settlements()
                reason = self._stop_reason or "finished"
        except KeyboardInterrupt:
            reason = "interrupted (Ctrl+C)"
            self._event_safe("warn", "stop", "stopped by Ctrl+C")
            raise
        except BaseException as exc:
            reason = f"error: {type(exc).__name__}: {exc}"[:300]
            self._set_error(reason)
            self._event_safe("error", "error", reason)
            self._heartbeat_safe("error")
            raise
        finally:
            self.summary["end_reason"] = reason
            self._finish(reason)
        return self.summary

    def _finish(self, reason: str) -> None:
        if self.live:
            self._cancel_path(reason)
        try:
            self.state.end(reason, ts=self.clock())
        except Exception:
            log.exception("late_favorite: could not record the end of run %s", self.state.run_id)
        self._heartbeat_safe("stopped")
        self._event_safe("info", "stop", f"late_favorite stopped: {reason}",
                         {k: v for k, v in self.summary.items() if k != "smoke"})

    def _sleep_until(self, t: float, cap: float = TICK_S) -> None:
        """Sleep until ``t``, but never more than ``cap`` seconds in one call. A wall clock that steps backwards while a
        poll is in progress puts ``t`` far ahead; one long sleep would leave the kill file and the heartbeat unattended
        for that long. The callers loop, so a capped sleep only means the next iteration comes sooner."""
        dt = t - self.clock()
        if dt > 0:
            self.sleep(min(dt, cap))

    # -------------------------------------------------------------------------------------------- windows
    def _discover(self, now: float, *, upcoming: bool = True) -> tuple[dict | None, str]:
        """The open KXBTC15M market with the earliest close that has not been handled yet.

        ``upcoming``: its decision window must not be over yet; otherwise (smoke) it only has to be open. Tickers
        are never parsed: close_time decides."""
        try:
            markets = self.client.open_markets(SERIES)
        except Exception as exc:
            return None, f"market discovery failed: {type(exc).__name__}: {exc}"
        cands: dict[float, list[dict]] = {}
        for m in markets or ():
            if not isinstance(m, Mapping) or not m.get("ticker") or not _active(m):
                continue
            c = close_ts(m)
            if c is None or c <= self._last_close_ts:
                continue
            if (now > c - ENDS_BEFORE_CLOSE_S) if upcoming else (now >= c):
                continue                                        # its decision window (or the market) is over
            if c - now > WINDOW_LENGTH_S + 60:                  # not this cycle's market
                continue
            cands.setdefault(c, []).append(dict(m))
        if not cands:
            return None, "no open KXBTC15M market with a decision window still ahead"
        c = min(cands)
        if len(cands[c]) > 1:
            return None, f"{len(cands[c])} open KXBTC15M markets close at {_iso(c)}; refusing to guess"
        return cands[c][0], ""

    def _next_window(self, deadline: float | None) -> _Window | None:
        """Wait (heartbeating) until a market with a decision window still ahead is open. None: the run is over."""
        next_try = waiting_since = self.clock()
        while not self._stop:
            now = self.clock()
            if deadline is not None and now >= deadline:
                return None
            if now >= next_try:
                m, why = self._discover(now)
                c = None if m is None else close_ts(m)
                if m is not None and c is not None:
                    if deadline is not None and c - ENDS_BEFORE_CLOSE_S > deadline:
                        return None                             # it would end after the requested run length
                    if _is_int(m.get("exchange_index")):
                        self._exchange_index = m["exchange_index"]          # the balance read needs the shard
                    return _Window(market=m, ticker=str(m["ticker"]), close_ts=c, start_ts=c - OPENS_BEFORE_CLOSE_S,
                                   end_ts=c - ENDS_BEFORE_CLOSE_S)
                # The next market opens the moment the last one closes, up to a minute after its decision window ends:
                # that gap is normal. Only a failed read, or a wait much longer than the gap, is worth an event.
                if why.startswith("market discovery failed") or now - waiting_since > NEXT_MARKET_WARN_S:
                    self._warn("discover", f"waiting for the next KXBTC15M market: {why}", every_s=300)
                next_try = now + DISCOVERY_RETRY_S
            self._idle("waiting")
            if self._stop:
                break
            self._sleep_until(min(next_try, self.clock() + TICK_S))
        return None

    def _run_window(self, w: _Window) -> None:
        self.window = w
        self._last_close_ts = w.close_ts
        self.summary["windows"] += 1
        if _is_int(w.market.get("exchange_index")):
            self._exchange_index = w.market["exchange_index"]
        try:
            self._window_loop(w)
        finally:
            self.window = None

    def _window_loop(self, w: _Window) -> None:
        prepared = False
        while not self._stop and self.clock() < w.start_ts:
            if not prepared and self.clock() >= w.start_ts - PREPARE_S:
                prepared = True
                if not self._prepare(w):
                    return
            if prepared and self.clock() >= w.start_ts - WARM_LEAD_S:
                self._warm_safety(w)
            self._idle("monitoring" if prepared else "waiting")
            if self._stop:
                return
            self._sleep_until(min(w.start_ts, self.clock() + TICK_S))
        if self._stop:
            return
        if not prepared and not self._prepare(w):
            return
        if self.live and not self._ledger_allows(w):
            return
        self._event("info", "window", f"decision window open for {w.ticker}: {_iso(w.start_ts)} to {_iso(w.end_ts)}, "
                    f"close {_iso(w.close_ts)}",
                    {"ticker": w.ticker, "close_ts": w.close_ts, "window_start_ts": w.start_ts,
                     "window_end_ts": w.end_ts, "floor_strike": w.market.get("floor_strike")})
        while not self._stop and not w.done:
            t0 = self.clock()
            if t0 > w.end_ts or self._check_kill() or self._check_halted():
                break
            book = self._book(w.ticker)
            if book is not None:
                self._on_book(w, book)
            self.summary["polls"] += 1
            w.polls += 1
            if not w.done:
                self._warm_safety(w)
            self._refresh_balance()
            self._heartbeat("trading" if w.attempts and not w.done else "monitoring")
            if self._stop or w.done or self.clock() >= w.end_ts:
                break
            self._sleep_until(min(w.end_ts, t0 + self.poll_s), cap=max(self.poll_s, TICK_S))
        self._close_window(w)

    def _warm_safety(self, w: _Window) -> None:
        """Live: let Safety refresh its cached reads (market listing, shard status, cash, position) just before and
        during the window, so the first check_entry is fast and the book it is handed is still fresh when the check
        ends. Optional: a Safety without preflight() is simply not warmed."""
        if not self.live or w.attempts or w.done:
            return
        fn = getattr(self.safety, "preflight", None)
        now = self.clock()
        if fn is None or now < w.next_warm:
            return
        w.next_warm = now + WARM_EVERY_S
        try:
            problem = fn(w.market)
        except Exception as exc:
            problem = f"preflight failed: {type(exc).__name__}: {exc}"
        if problem:
            self._warn(f"warm:{w.ticker}:{problem}", f"safety preflight for {w.ticker}: {problem}", every_s=120)

    def _prepare(self, w: _Window) -> bool:
        """Re-read the market just before its decision window: it must still be active and close when it said."""
        try:
            m = _unwrap_market(self.client.market(w.ticker))
        except Exception as exc:
            self._warn(f"prepare:{w.ticker}", f"market read failed for {w.ticker}: {type(exc).__name__}: {exc}")
            return True                     # keep the discovery data; the order book decides whether we can trade
        if m is None:
            return True
        c = close_ts(m)
        if not _active(m) or (c is not None and abs(c - w.close_ts) > 0.5):
            self._event("warn", "window",
                        f"skipping {w.ticker}: status {m.get('status')!r}, close_time {m.get('close_time')!r}")
            return False
        w.market = {**w.market, **dict(m)}
        return True

    def _ledger_allows(self, w: _Window) -> bool:
        """Live: the ledger remembers orders from earlier runs. A market that already has a filled or unresolved
        order is not traded again, and earlier attempts count against this window's budget of 3."""
        fn = getattr(self.ledger, "orders_for", None)
        if fn is None:
            return True
        try:
            rows = list(fn(w.ticker))
        except Exception as exc:
            self._event("warn", "window", f"skipping {w.ticker}: the ledger could not be read ({exc})")
            return False
        traded = any(r.get("status") in TRADED_STATUSES or (_num(r.get("fill_count")) or D0) > 0 for r in rows)
        if traded or len(rows) >= self.max_attempts:
            self._event("warn", "window", f"{w.ticker} already has {len(rows)} order(s) in the ledger "
                        f"({'traded' if traded else 'attempt budget used'}); not trading it again")
            return False
        w.prior_attempts = len(rows)
        return True

    def _book(self, ticker: str) -> Mapping | None:
        try:
            book = self.client.orderbook(ticker)
        except Exception as exc:
            self._warn(f"book:{ticker}", f"order book read failed for {ticker}: {type(exc).__name__}: {exc}")
            return None
        if not isinstance(book, Mapping):
            self._warn(f"book:{ticker}", f"order book for {ticker} is not a mapping")
            return None
        return book

    def _on_book(self, w: _Window, book: Mapping) -> None:
        now = self.clock()
        age = book_age(book, now)
        if age > self.max_book_age_s:
            self.summary["stale_books"] += 1
            self._warn(f"stale:{w.ticker}", f"order book for {w.ticker} is {age:.1f} s old; not used", every_s=30)
            return
        w.fresh_polls += 1
        try:
            w.last_quote = read_quote(book)
            d = decide(book, now, w.close_ts)
        except (ArithmeticError, TypeError, ValueError) as exc:
            self._warn(f"parse:{w.ticker}", f"cannot read the order book of {w.ticker}: {exc}")
            return
        if d is None:
            if w.outcome is None and w.last_quote is not None and in_decision_window(now, w.close_ts):
                # The rule is applied ONCE, to the first fresh two-sided book inside the window, and this book does not
                # fire it (the YES mid is 0.40-0.60): the window is skipped. A later book that crosses a threshold is
                # not the rule and is not traded. (A stale, one-sided or crossed book never reaches here: it is no
                # basis for a decision, so the next fresh one is the first.)
                w.skipped = w.done = True        # (_close_window reports it)
                w.skip_elapsed_s = elapsed_in_window(now, w.close_ts)
            return
        if w.outcome is None:
            self._detect(w, d, book, now)
        elif d["outcome"] != w.outcome:
            self._warn(f"flip:{w.ticker}", f"the rule now says {d['outcome']} but this window is locked to "
                       f"{w.outcome}; ignoring", every_s=30)
            return
        if not self.live or w.done:
            return
        if d["size"] < self.contracts:
            self._warn(f"thin:{w.ticker}", f"only {d['size']} contracts displayed at {d['ask']}; need {self.contracts}",
                       every_s=30)
            return
        if w.filled > 0 or w.unknown or w.prior_attempts + w.attempts >= self.max_attempts or now < w.next_try:
            return
        self._attempt(w, d, book, now)

    # -------------------------------------------------------------------------------------------- detection
    def _detect(self, w: _Window, d: dict, book: Mapping, now: float) -> None:
        """The first book of the window that fires the rule: write the paper twin (live or not)."""
        w.outcome, w.detected_ts, w.detect_ask, w.detect_mid = d["outcome"], now, d["ask"], d["mid"]
        w.paper_cost = d["ask"] * self.contracts
        w.paper_fees = self.fee_fn(self.contracts, d["ask"])
        kind = KIND_YES if d["outcome"] == "yes" else KIND_NO
        w.opp_id = self.state.opportunity(
            detected_ts=now, hour_ticker=w.ticker, strike_ticker=w.ticker, kind=kind, contracts=self.contracts,
            paper_cost=w.paper_cost, paper_fees=w.paper_fees, paper_edge=None, live=False,
            outcome=None if self.live else "paper_only")
        self.summary["detections"] += 1
        self._event("info", "opportunity",
                    f"{kind}: mid {d['mid']:.4f}, buy {d['outcome'].upper()} at {d['ask']:.4f} on {w.ticker}",
                    {"opportunity_id": w.opp_id, "ticker": w.ticker, "outcome": d["outcome"], "ask": d["ask"],
                     "mid": d["mid"], "size": d["size"], "elapsed_s": elapsed_in_window(now, w.close_ts),
                     "book_age_s": book_age(book, now), "paper_cost": w.paper_cost, "paper_fees": w.paper_fees})
        self._pending.append({"opp_id": w.opp_id, "ticker": w.ticker, "close_ts": w.close_ts, "outcome": d["outcome"],
                              "contracts": self.contracts, "paper_cost": w.paper_cost, "paper_fees": w.paper_fees,
                              "live": False, "resolved": True, "records": w.records, "next_check": 0.0})
        if not self.live:
            self._paper_order(w, d, now)
            self.summary["paper_trades"] += 1
            self._count("paper_only")
            w.done = True            # watch mode: one paper twin per window, nothing else to do

    def _paper_order(self, w: _Window, d: dict, ts: float) -> None:
        """A dry-run bot_orders row showing exactly what a live run would have sent."""
        coid = "paper-" + uuid.uuid4().hex
        try:
            body = order_body(entry_spec(w.market, d["outcome"], d["ask"], self.contracts, purpose="paper",
                                         opportunity_id=w.opp_id), coid)
        except (KeyError, TypeError, ValueError):
            body = None
        self.state.order(client_order_id=coid, ticker=w.ticker, side=d["outcome"], action="buy", count=self.contracts,
                         limit_price=d["ask"], tif="ioc", status="dry_run", opportunity_id=w.opp_id, ts_sent=ts,
                         dry_run=True, response={"dry_run": True, "would_send": body})

    # -------------------------------------------------------------------------------------------- live attempt
    def _entry_block_reason(self) -> str | None:
        if self._stop:
            return "the bot is stopping"
        if self._check_kill():
            return "kill switch"
        if self._safety_halted():
            return "safety is halted"
        return None

    def _blocked(self, w: _Window, reason: str, *, cap: bool = False, retry_s: float = BLOCK_RETRY_S) -> None:
        w.blocked = reason
        w.next_try = self.clock() + retry_s
        if reason not in w.block_reasons:
            w.block_reasons.add(reason)
            self.summary["blocked"] += 1
            self._event("warn", "cap", f"live order not sent: {reason}", {"opportunity_id": w.opp_id,
                                                                             "ticker": w.ticker})
        if cap:
            self._halt_if_capped()

    def _attempt(self, w: _Window, d: dict, book: Mapping, decided_ts: float) -> None:
        reason = self._entry_block_reason()
        if reason:
            return self._blocked(w, reason, retry_s=0.0)
        try:
            spec = entry_spec(w.market, d["outcome"], d["ask"], self.contracts, opportunity_id=w.opp_id,
                              purpose="entry" if w.attempts == 0 and w.prior_attempts == 0 else "retry")
        except (KeyError, TypeError, ValueError) as exc:
            return self._blocked(w, f"cannot build the order: {exc}")
        # Safety first: when a cap is about to refuse the order it first records the settlement of old positions the
        # ledger has not seen yet, which can make room. Then this bot's own cap check: it is the backstop when Safety
        # says yes, and the explanation when Safety says no (a cap that leaves no room stops the bot).
        ok, why = self._safety_check("check_entry", spec, book, w.market)
        loss_ok, loss_why, is_cap = self._loss_ok(spec)
        if not loss_ok:
            return self._blocked(w, loss_why, cap=is_cap)
        if not ok:
            return self._blocked(w, f"refused by safety: {why}")
        if self._check_kill():
            return self._blocked(w, "kill switch", retry_s=0.0)

        coid, body = self._intent(spec)                       # committed before anything is sent
        self._touched.add(w.ticker)
        w.attempts += 1
        w.blocked = None
        ts_sent = self.clock()
        self._order_row(coid, spec, w.opp_id, ts_sent, body)
        self.state.update_opportunity(w.opp_id, live=True)
        for item in self._pending:
            if item["opp_id"] == w.opp_id:
                item["live"] = True
        marked = self._mark_sent(coid, ts_sent)
        # Those four commits can take long (a locked database waits up to its busy timeout): look again, now, at what
        # made the order allowed. Anything that no longer holds means the committed intent is NOT sent.
        why_not = self._unsafe_to_send(w, book, marked)
        if why_not:
            return self._abort_unsent(w, spec, coid, body, why_not)
        w.sent += 1
        self.summary["live_attempts"] += 1
        try:
            res = self.client.create_order(body)
        except Exception as exc:     # the order may or may not have reached the exchange: unknown, never resent
            res = _Outcome("unknown", error=f"{type(exc).__name__}: {exc}")
        ts_ack = self.clock()
        w.last_ack_ts = ts_ack
        sent = self._finish_send(spec, coid, body, res, ts_ack, ts_sent)
        fill = sent["fill"]
        self._event("info", "order",
                    f"attempt {w.prior_attempts + w.attempts}/{self.max_attempts}: IOC buy {d['outcome'].upper()} at "
                    f"{d['ask']:.4f} on {w.ticker}: {'outcome unknown' if fill is None else f'{fill} filled'} "
                    f"({(ts_ack - decided_ts) * 1000:.0f} ms from the decision)",
                    {"opportunity_id": w.opp_id, "client_order_id": coid, "attempt": w.prior_attempts + w.attempts,
                     "outcome": getattr(res, "outcome", None), "fill": fill})
        if fill is None:
            w.unknown = True
            w.done = True
            self._trip("an order outcome is unknown and the order was not found; nothing is resent")
        elif fill > 0:
            w.records.append(self._record(spec, sent))
            w.filled += fill
            w.done = True                              # never more than one filled trade per window
            self.summary["live_fills"] += 1
            if fill < self.contracts:
                self._scale_twin(w, fill)
        if fill is None or fill > 0 or not self._ledger_final(coid):
            self._reconcile([w.ticker])           # fills to record, or an order the ledger still sees as open
        for rec in w.records:
            self._refine(rec)
        if self._check_halted():
            return
        self._check_loss_cap()

    def _mark_sent(self, coid: str, ts: float) -> bool:
        """Stamp the intent's send time in the ledger. False if that failed: an order the ledger cannot date is not sent."""
        mark = getattr(self.ledger, "mark_sent", None)
        if mark is None:
            return True
        try:
            mark([coid], ts)
            return True
        except Exception:
            log.exception("late_favorite: ledger.mark_sent failed for %s", coid)
            return False

    def _unsafe_to_send(self, w: _Window, book: Mapping, marked: bool) -> str | None:
        """The cheap checks that must still hold at the moment of the send, or the reason one does not: the send time
        was stamped, the bot is not stopping, no kill file, Safety not halted, the decision window is not over and the
        book the order was priced from is still fresh."""
        if not marked:
            return "the ledger could not stamp the send time (database busy?)"
        if self._stop:
            return "the bot is stopping"
        if self._check_kill():
            return "kill switch"
        if self._check_halted():
            return "safety is halted"
        now = self.clock()
        if now > w.end_ts + self.max_book_age_s:
            return f"the decision window closed {now - w.end_ts:.1f} s ago"
        age = book_age(book, now)
        if age > self.max_book_age_s:
            return f"the order book is {age:.1f} s old (max {self.max_book_age_s:g} s)"
        return None

    def _abort_unsent(self, w: _Window, spec: Mapping, coid: str, body: Mapping, why: str) -> None:
        """A committed intent that must not go out. Say so everywhere, so the ledger does not keep it as an order that may
        have been placed (it is final, and counts as no exposure) and the dashboard does not show it as a live order."""
        try:
            self.ledger.mark_presumed_not_placed(coid, f"not sent: {why}")
        except Exception:
            log.exception("late_favorite: could not mark %s as not sent", coid)
            self._set_error(f"intent {coid} was committed but not sent ({why}) and could not be marked")
        try:
            self.state.order(client_order_id=coid, ticker=spec["ticker"], side=spec["outcome"], action="buy",
                             count=spec["count"], limit_price=spec["outcome_price"], tif="ioc", status="not_sent",
                             opportunity_id=w.opp_id, dry_run=True, response={"request": body, "not_sent": why})
            if not w.sent:                                   # nothing of this window has left the process
                self.state.update_opportunity(w.opp_id, live=False)
                for item in self._pending:
                    if item["opp_id"] == w.opp_id:
                        item["live"] = False
        except Exception:
            log.exception("late_favorite: could not record that %s was not sent", coid)
        self._blocked(w, f"{why} (checked again after the intent was committed)")

    def _scale_twin(self, w: _Window, filled: Decimal) -> None:
        """A partial fill is the window's one filled trade. Scale the paper twin to the contracts that filled (same
        detection price, fewer contracts), so the row's live and paper figures, and the dashboard's paired live-vs-paper
        comparison and its per-contract prices, describe the same trade."""
        cost, fees = w.detect_ask * filled, self.fee_fn(filled, w.detect_ask)
        w.paper_cost, w.paper_fees = cost, fees
        for item in self._pending:
            if item["opp_id"] == w.opp_id:
                item.update(contracts=filled, paper_cost=cost, paper_fees=fees)
        try:
            self.state.rescale_opportunity(w.opp_id, contracts=filled, paper_cost=cost, paper_fees=fees)
        except Exception as exc:
            self._warn(f"twin:{w.ticker}", f"could not scale the paper twin of {w.ticker} to {filled}: {exc}")

    def _ledger_final(self, coid: str) -> bool:
        """True when the ledger already shows the order as finished without a fill (an IOC the exchange cancelled)."""
        fn = getattr(self.ledger, "order", None)
        try:
            row = fn(coid) if fn is not None else None
        except Exception:
            return False
        return bool(row) and row.get("status") in ("canceled", "rejected", "throttled")

    # -------------------------------------------------------------------------------------------- sending
    def _safety_check(self, name: str, spec: dict, book: Mapping | None, market: Mapping) -> tuple[bool, str]:
        fn = getattr(self.safety, name, None)
        if fn is None:
            return False, f"Safety has no {name}()"
        try:
            return _verdict(fn(spec, book, market))
        except Exception as exc:      # fail closed
            return False, f"{name} failed: {type(exc).__name__}: {exc}"

    def _realized_loss(self) -> Decimal:
        """Realized loss for the account-wide cap: the larger of the all-time and the 24 h figure, as Safety counts
        it, so a restart never resets the cap."""
        vals = []
        for name in ("realized_loss_total", "realized_loss_24h"):
            fn = getattr(self.ledger, name, None)
            if fn is not None:
                vals.append(_dec_any(fn()))
        if not vals:
            raise AttributeError("the ledger has no realized_loss_total() or realized_loss_24h()")
        return max(vals)

    def _loss_ok(self, spec: dict) -> tuple[bool, str, bool]:
        """Per-order ceilings plus the combined loss rule: realized loss + open worst case + this order's worst
        case <= the cap. Returns (ok, reason, is_cap)."""
        try:
            collateral = units.collateral(spec["side"], spec["yes_price"], spec["count"])
            worst = collateral + units.worst_fee(spec["count"], spec["yes_price"])
            if collateral > COST_PER_ORDER_CEILING:
                return False, f"collateral {collateral} exceeds the {COST_PER_ORDER_CEILING} per-order ceiling", False
            if spec["count"] > CONTRACTS_CEILING:
                return False, f"{spec['count']} contracts exceeds the {CONTRACTS_CEILING} per-order ceiling", False
            if not (PRICE_MIN <= spec["outcome_price"] <= PRICE_MAX):
                return False, f"price {spec['outcome_price']} is outside {PRICE_MIN}..{PRICE_MAX}", False
            used = self._realized_loss() + _dec_any(self.ledger.open_worst_case())
        except Exception as exc:
            return False, f"loss check failed: {type(exc).__name__}: {exc}", False
        if used + worst > self.max_loss:
            return False, f"loss cap: {used} used + {worst} worst case of this order > {self.max_loss} cap", True
        return True, "", False

    def _halt_if_capped(self) -> None:
        """An order does not fit under the cap. If nothing is open that could free room, no order ever will: stop."""
        try:
            open_worst = _dec_any(self.ledger.open_worst_case())
        except Exception:
            return
        if open_worst <= 0 and not any(i["live"] for i in self._pending):
            self._trip(f"the {self.max_loss} loss cap leaves no room for another order")

    def _intent(self, spec: dict) -> tuple[str, dict]:
        coid0 = str(uuid.uuid4())
        spec["client_order_id"] = coid0
        spec["body"] = order_body(spec, coid0)
        coid = self.ledger.new_intent(spec)
        if not coid:
            raise RuntimeError("Ledger.new_intent returned no client_order_id")
        coid = str(coid)
        if coid != coid0:
            spec["client_order_id"], spec["body"] = coid, order_body(spec, coid)
        return coid, spec["body"]

    def _order_row(self, coid: str, spec: Mapping, opp_id: int | None, ts_sent: float, body: Mapping) -> None:
        self.state.order(client_order_id=coid, ticker=spec["ticker"], side=spec["outcome"], action="buy",
                         count=spec["count"], limit_price=spec["outcome_price"], tif="ioc", status="sent",
                         opportunity_id=opp_id, ts_sent=ts_sent, dry_run=False, response={"request": body})

    def _finish_send(self, spec: dict, coid: str, body: Mapping, res: Any, ts_ack: float, ts_sent: float) -> dict:
        try:
            self.ledger.mark_result(coid, res)
        except Exception as exc:
            self._set_error(f"ledger.mark_result failed for {coid}: {exc}")
            self._event_safe("error", "error", f"ledger.mark_result failed for {coid}: {type(exc).__name__}: {exc}")
        fill, order_id = self._resolve_fill(spec, coid, res, ts_sent)
        fee = self._fee_from(res, fill, spec["outcome_price"]) if fill else None
        self.state.update_order(coid, ts_ack=ts_ack, status=_order_status(getattr(res, "outcome", None), fill,
                                                                          spec["count"]),
                                order_id=order_id, filled=fill, avg_fill_price=spec["outcome_price"] if fill else None,
                                fees=fee,
                                response={"request": body, "outcome": getattr(res, "outcome", None),
                                          "http_status": getattr(res, "status", None),
                                          "data": getattr(res, "data", None), "error": getattr(res, "error", None)})
        return {"coid": coid, "fill": fill, "order_id": order_id, "fee": fee}

    def _resolve_fill(self, spec: dict, coid: str, res: Any, ts_sent: float) -> tuple[Decimal | None, str | None]:
        """Contracts filled, or None when the outcome stays unknown. Unknown outcomes are looked up, never resent."""
        oc = getattr(res, "outcome", None)
        data = getattr(res, "data", None)
        data = data if isinstance(data, Mapping) else {}
        if oc == "ok":
            echoed = data.get("client_order_id")
            fill = _num(data.get("fill_count"))
            if echoed not in (None, "", coid):
                self._event("error", "order", f"the response for {coid} names client_order_id {echoed}; looking it up")
            elif fill is not None:
                return self._checked_fill(spec, coid, fill), _str_or_none(data.get("order_id"))
        elif oc in ("rejected", "throttled"):
            return D0, None
        elif oc == "auth":
            self._trip("authentication refused (HTTP 401/403): check the key and KALSHI_ENV")
            return D0, None
        return self._lookup_fill(spec, coid, ts_sent)

    def _checked_fill(self, spec: dict, coid: str, fill: Decimal) -> Decimal:
        if fill < 0 or fill > spec["count"]:
            self._trip(f"order {coid} reports {fill} filled of {spec['count']}")
        return fill

    def _wait_portfolio_fresh(self, since_ts: float) -> None:
        """Bounded wait until the REST portfolio data has caught up with the time the order was sent."""
        fn = getattr(self.client, "user_data_timestamp", None)
        if fn is None:
            return
        deadline = self.clock() + DATA_FRESH_WAIT_S
        while True:
            try:
                as_of = units.parse_rfc3339(fn()["as_of_time"])
            except Exception:
                return
            if as_of >= since_ts or self.clock() >= deadline:
                return
            self._heartbeat("trading")
            self.sleep(1.0)

    def _lookup_fill(self, spec: dict, coid: str, ts_sent: float) -> tuple[Decimal | None, str | None]:
        ticker = spec["ticker"]
        self._wait_portfolio_fresh(ts_sent)
        for attempt in range(LOOKUP_TRIES):
            self._heartbeat("trading", force=True)           # the dashboard must not see a silent bot while we look
            if attempt:
                self.sleep(LOOKUP_WAIT_S)
            self._reconcile([ticker])
            try:
                resp = self.client.orders(ticker=ticker)
            except AuthError as exc:
                self._auth_stop(exc, "the orders lookup")
                continue
            except Exception as exc:
                self._warn(f"lookup:{coid}", f"orders lookup failed for {ticker}: {type(exc).__name__}: {exc}")
                continue
            for o in _items(resp, "orders"):
                if isinstance(o, Mapping) and o.get("client_order_id") == coid:
                    fill = _num(o.get("fill_count_fp", o.get("fill_count")))
                    if fill is None:
                        break
                    adopt = getattr(self.ledger, "apply_exchange_order", None)
                    if adopt is not None:
                        try:
                            adopt(o)
                        except Exception as exc:
                            self._warn(f"adopt:{coid}", f"ledger could not adopt order {coid}: {exc}")
                    self._event("info", "order", f"order {coid} found by lookup: {fill} filled",
                                {"order_id": o.get("order_id"), "status": o.get("status")})
                    return self._checked_fill(spec, coid, fill), _str_or_none(o.get("order_id"))
        self._event("error", "order", f"order {coid} on {ticker}: outcome unknown and not found; it is never resent")
        return None, None

    def _fee_from(self, res: Any, fill: Decimal, price: Decimal) -> Decimal:
        data = getattr(res, "data", None)
        if isinstance(data, Mapping):
            per = _num(data.get("average_fee_paid"))       # dollars PER CONTRACT
            if per is not None:
                return per * fill
        return self.fee_fn(fill, price)

    def _record(self, spec: Mapping, s: Mapping) -> dict:
        """One filled order, in outcome terms. price is the limit until refined from the exchange's fills."""
        return {"coid": s["coid"], "order_id": s["order_id"], "ticker": spec["ticker"], "outcome": spec["outcome"],
                "count": s["fill"], "price": spec["outcome_price"], "limit": spec["outcome_price"],
                "fee": s["fee"] if s["fee"] is not None else D0, "exact": False}

    def _refine(self, rec: dict) -> None:
        """Replace the limit-price estimate with the exchange's fills when their counts add up. A fill on the wrong
        outcome, or above the limit, is an anomaly that stops the bot."""
        fills_fn = getattr(self.client, "fills", None)
        if fills_fn is None or not rec.get("order_id") or rec.get("exact"):
            return
        try:
            resp = fills_fn(order_id=rec["order_id"])
        except AuthError as exc:
            self._auth_stop(exc, "the fills read")
            return
        except Exception as exc:
            self._warn(f"fills:{rec['order_id']}", f"fills read failed for {rec['order_id']}: {type(exc).__name__}: {exc}")
            return
        items = [f for f in _items(resp, "fills")
                 if isinstance(f, Mapping) and f.get("order_id") in (None, rec["order_id"])]
        if not items:
            return
        total = value = fee = D0
        fee_known, ok = True, True
        for f in items:
            c = _num(f.get("count_fp", f.get("count")))
            if c is None:
                ok = False
                break
            if f.get("outcome_side") != rec["outcome"]:
                self._trip(f"order {rec['coid']} filled on {f.get('outcome_side')!r}, not the {rec['outcome']!r} it bought")
                return
            p = _num(f.get("yes_price_dollars")) if rec["outcome"] == "yes" else _num(f.get("no_price_dollars"))
            if p is None and rec["outcome"] == "no":
                y = _num(f.get("yes_price_dollars"))
                p = None if y is None else D1 - y
            if p is None:
                ok = False
                break
            if p > rec["limit"] + Decimal("0.0001"):
                self._trip(f"order {rec['coid']} filled at {p}, above its {rec['limit']} limit")
                return
            fc = _num(f.get("fee_cost"))
            fee_known = fee_known and fc is not None
            total, value, fee = total + c, value + c * p, fee + (fc or D0)
        if not ok or total != rec["count"]:
            return
        rec["price"], rec["exact"] = value / total, True
        if fee_known:
            rec["fee"] = fee
        self.state.update_order(rec["coid"], avg_fill_price=rec["price"], fees=rec["fee"])
        upsert = getattr(self.ledger, "upsert_fills", None)
        if upsert is not None:
            try:
                upsert(items)
            except Exception as exc:
                self._warn(f"upsert:{rec['order_id']}", f"ledger could not record the fills: {exc}")

    # -------------------------------------------------------------------------------------------- window end
    def _close_window(self, w: _Window) -> None:
        """Final state of the window's opportunity row and one summary event."""
        if w.outcome is None:
            self.summary["no_signal"] += 1
            if w.fresh_polls == 0:
                why = "no fresh order book"
            elif w.skipped and w.last_quote is not None:
                why = (f"the rule did not fire on the first fresh book (YES mid {w.last_quote.mid:.4f}, "
                       f"{w.skip_elapsed_s:.0f} s into the window), so the window is skipped")
            elif w.last_quote is None:
                why = "no two-sided order book"
            else:
                why = f"the rule did not fire (last mid {w.last_quote.mid:.4f})"
            self._event("info", "window", f"no trade on {w.ticker}: {why}",
                        {"ticker": w.ticker, "polls": w.polls, "fresh_polls": w.fresh_polls, "skipped": w.skipped,
                         "last_mid": None if w.last_quote is None else w.last_quote.mid})
            return
        if not self.live:
            self._event("info", "window", f"paper twin on {w.ticker}: {w.outcome.upper()} at {w.detect_ask:.4f} "
                        f"(mid {w.detect_mid:.4f})", {"ticker": w.ticker, "opportunity_id": w.opp_id})
            return
        if w.unknown:
            outcome = "unknown"
        elif w.filled > 0:
            outcome = "filled"
        elif w.sent > 0:
            outcome = "none_filled"
        elif w.blocked:
            outcome = "blocked_by_cap"          # a cap, Safety, the kill switch, a halt or a stale send refused every order
        else:
            outcome = "none_filled"             # the rule fired but no order could be sent (a thin book)
        fields: dict[str, Any] = {"outcome": outcome}
        if w.records:
            fields["live_cost"] = sum((r["count"] * r["price"] for r in w.records), D0)
            fields["live_fees"] = sum((r["fee"] for r in w.records), D0)
        if w.last_ack_ts is not None and w.detected_ts is not None and w.sent:
            fields["latency_ms"] = (w.last_ack_ts - w.detected_ts) * 1000.0     # detection -> last ack
        self.state.update_opportunity(w.opp_id, **fields)
        for item in self._pending:
            if item["opp_id"] == w.opp_id:
                item["resolved"] = outcome != "unknown"
        self._count(outcome)
        level = {"filled": "info", "none_filled": "info", "blocked_by_cap": "warn"}.get(outcome, "error")
        self._event(level, "fill" if outcome == "filled" else "window",
                    f"live trade on {w.ticker}: {outcome.replace('_', ' ')} after {w.prior_attempts + w.attempts} attempt(s)",
                    {"opportunity_id": w.opp_id, "outcome": outcome, "attempts": w.attempts, "filled": w.filled,
                     "blocked": w.blocked, **{k: v for k, v in fields.items() if k != "outcome"}})
        if level == "error":
            self._set_error(f"live trade on {w.ticker}: {outcome}")

    # -------------------------------------------------------------------------------------------- safety helpers
    def _reconcile(self, tickers: list[str]) -> None:
        fn = getattr(self.reconciler, "reconcile", None)
        if fn is None or not self.live:
            return
        try:
            rep = fn(self.client, self.ledger, tickers)
        except Exception as exc:
            self._warn(f"reconcile:{','.join(tickers)}", f"reconcile failed: {type(exc).__name__}: {exc}")
            return
        unconfirmed = list(getattr(rep, "unconfirmed", None) or ())
        if unconfirmed:       # the portfolio data lags: not a mismatch and no halt, but not something to hide either
            self._warn(f"unconfirmed:{','.join(tickers)}",
                       f"reconcile of {', '.join(tickers)}: {unconfirmed[0]} could not be confirmed because the exchange's "
                       "portfolio data is behind; it is not a mismatch (nothing halted), and settlement checks it again",
                       every_s=300)

    def _cancel_path(self, reason: str) -> None:
        fn = getattr(self.reconciler, "cancel_path", None)
        if fn is None:
            return
        try:
            fn(self.client, self.ledger, reason, sorted(self._touched))
        except Exception as exc:
            self._set_error(f"cancel path failed: {exc}")
            self._event_safe("error", "error", f"cancel path failed: {type(exc).__name__}: {exc}")

    def _safety_halted(self) -> bool:
        """Safety.halted (an attribute; a callable is tolerated). Unreadable counts as halted."""
        if self.safety is None:
            return False
        v = getattr(self.safety, "halted", False)
        try:
            return bool(v() if callable(v) else v)
        except Exception:
            return True

    def _check_halted(self) -> bool:
        """Stop the bot when Safety halted (a trip from here, from the ledger's halt listener, from reconcile, or from a
        401 on one of Safety's own reads). The reason Safety gives is shown as the run's last error."""
        if not self._safety_halted():
            return False
        if self.last_error is None:
            why = getattr(self.safety, "halt_reason", None)
            self._set_error(f"safety halted: {why}" if isinstance(why, str) and why else "safety halted")
        self._halt("safety is halted")
        return True

    def _trip(self, reason: str) -> None:
        """Emergency stop: Safety is tripped (no order can pass again) and this bot stops."""
        self._tripped = True
        self._set_error(reason)
        fn = getattr(self.safety, "trip", None)
        if fn is not None:
            try:
                fn(reason)
            except Exception:
                log.exception("late_favorite: Safety.trip failed")
        self._event_safe("error", "error", f"safety tripped: {reason}")
        self._halt(reason)

    def _halt(self, reason: str) -> None:
        if not self._stop:
            self._stop, self._stop_reason = True, reason
            self._event_safe("warn", "stop", f"stopping: {reason}")

    def _kill_active(self) -> bool:
        try:
            if self.kill_path.exists():
                return True
        except OSError:
            return True
        fn = getattr(self.safety, "kill_active", None) if self.safety is not None else None
        if fn is not None:
            try:
                return bool(fn())
            except Exception:
                return True
        return False

    def _check_kill(self) -> bool:
        if not self._kill_active():
            return False
        if not self._stop:
            if self.last_error is None and self._safety_halted():
                # the kill file is Safety's own trip (it writes one): say why, not only that the file exists
                why = getattr(self.safety, "halt_reason", None)
                self._set_error(f"safety halted: {why}" if isinstance(why, str) and why else "safety halted")
            self._event_safe("warn", "kill", f"kill switch active ({self.kill_path}): no new orders, stopping")
            self._halt("kill switch")
        return True

    def _check_loss_cap(self) -> None:
        """Realized loss at the cap: trip and stop. (Room for one more order is checked before each order.)"""
        if not self.live or self.ledger is None:
            return
        try:
            realized = self._realized_loss()
        except Exception:
            return
        if realized >= self.max_loss:
            self._trip(f"realized loss {realized} reached the {self.max_loss} cap")

    def _loss_used(self) -> Decimal | None:
        if self.ledger is None:
            return None
        try:
            return self._realized_loss() + _dec_any(self.ledger.open_worst_case())
        except Exception:
            return None

    def _refresh_balance(self) -> None:
        if not self.live or 0 <= self.clock() - self._balance_ts < BALANCE_EVERY_S:
            return
        self._balance_ts = self.clock()
        try:
            if self._exchange_index is None:
                return
            b = self.client.balance(self._exchange_index) or {}
            usd = _num(b.get("balance_dollars"))
            if usd is None and _is_int(b.get("balance")):
                usd = units.from_cents(b["balance"])
            if usd is not None:
                self.balance_usd = usd
        except AuthError as exc:
            self._auth_stop(exc, "the balance read")
        except Exception as exc:
            self._warn("balance", f"balance read failed: {type(exc).__name__}: {exc}", every_s=600)

    def _auth_stop(self, exc: Exception, what: str) -> None:
        """A 401/403 on an authenticated read: the key, KALSHI_ENV or the PC clock is wrong. Stop; never loop on it."""
        self._trip(f"authentication refused (HTTP 401/403) on {what}: {exc}")

    def _refresh_clock(self) -> None:
        """Live: keep Safety's PC-clock check alive. It re-measures the skew against Kalshi's when the last reading is
        due (every 10 minutes), so an order check finds a fresh reading; a clock that has drifted refuses every order."""
        fn = getattr(self.safety, "poll_clock", None) if self.live else None
        if fn is None:
            return
        try:
            problem = fn()
        except Exception as exc:
            problem = f"clock check failed: {type(exc).__name__}"
        if problem:
            self._set_error(f"PC clock: {problem}")
            self._warn("clock", f"PC clock: {problem}; no order is sent until it is within tolerance", every_s=300)

    def _clock_skew(self) -> float | None:
        fn = getattr(self.safety, "clock_skew", None) if self.live else None
        try:
            return None if fn is None else fn()
        except Exception:
            return None

    # -------------------------------------------------------------------------------------------- idle housekeeping
    def _idle(self, status: str) -> None:
        self._check_kill()
        if self.live:
            self._check_halted()
        if self._stop:
            return
        self._settle_pending()
        self._settle_ledger_pending()
        self._refresh_balance()
        self._refresh_clock()
        self._heartbeat(status)

    # -------------------------------------------------------------------------------------------- settlement
    def _market_result(self, ticker: str) -> str | None:
        try:
            m = _unwrap_market(self.client.market(ticker))
        except Exception as exc:
            self._warn(f"result:{ticker}", f"market read failed for {ticker}: {type(exc).__name__}: {exc}")
            return None
        r = str((m or {}).get("result") or "").lower()
        return r if r in SETTLED_RESULTS else None

    def _settle_pending(self) -> None:
        if not self._pending:
            return
        now = self.clock()
        for item in list(self._pending):
            if now < item["close_ts"] + SETTLE_DELAY_S or now < item["next_check"]:
                continue
            item["next_check"] = now + self.settle_poll_s
            res = self._market_result(item["ticker"])
            if res is None:
                if now > item["close_ts"] + SETTLE_GIVE_UP_S:
                    self._pending.remove(item)
                    self._event("warn", "settle", f"no result for {item['ticker']} after 6 h; P&L stays empty",
                                {"opportunity_id": item["opp_id"]})
                continue
            self._settle_one(item, res)
            self._pending.remove(item)

    def _settle_one(self, item: dict, result: str) -> None:
        """Book the paper twin's and (live) the real P&L of one finished market. ``item["run_id"]`` is set for a
        row an earlier run left pending: it is settled on its own row and kept out of this run's totals."""
        recovered = item.get("run_id") is not None
        won = result == item["outcome"]
        payout_per = D1 if won else D0
        paper_pnl = payout_per * item["contracts"] - item["paper_cost"] - item["paper_fees"]
        fields: dict[str, Any] = {"paper_realized_pnl": paper_pnl}
        data: dict[str, Any] = {"opportunity_id": item["opp_id"], "ticker": item["ticker"], "result": result,
                                "bought": item["outcome"], "won": won, "paper_realized_pnl": paper_pnl}
        if recovered:
            data["from_run"] = item["run_id"]
            self.summary["recovered"] += 1
        else:
            self.summary["settled"] += 1
            self.summary["paper_pnl"] += paper_pnl
            self.summary["paper_wins" if won else "paper_losses"] += 1
        live_pnl = None
        if item["live"]:
            for rec in item["records"]:
                self._refine(rec)                 # the fills are surely visible by now: exact prices and fees
            if item["resolved"] and item["records"]:
                recs = item["records"]
                filled = sum((r["count"] for r in recs), D0)
                cost = sum((r["count"] * r["price"] for r in recs), D0)
                fees = sum((r["fee"] for r in recs), D0)
                payout = filled * payout_per
                live_pnl = payout - cost - fees
                fields.update(payout=payout, realized_pnl=live_pnl, live_cost=cost, live_fees=fees)
                data.update(payout=payout, realized_pnl=live_pnl, live_cost=cost, live_fees=fees)
                if not recovered:
                    self.summary["live_pnl"] += live_pnl
                    self.summary["live_wins" if won else "live_losses"] += 1
                self._live_pnl[item["ticker"]] = (live_pnl, {r["order_id"] for r in recs if r.get("order_id")})
            if self._queue_ledger(item["ticker"]):
                self._settle_ledger_pending(force=True)
                data["ledger_recorded"] = item["ticker"] not in self._ledger_todo
        self.state.update_opportunity(item["opp_id"], run_id=item.get("run_id"), **fields)
        msg = (f"settled {item['ticker']} {result.upper()}: bought {item['outcome'].upper()} "
               f"({'won' if won else 'lost'}), paper P&L ${paper_pnl:.4f}"
               + (f", live P&L ${live_pnl:.4f}" if live_pnl is not None else "")
               + (" (left over from an earlier run)" if recovered else ""))
        self._event("info", "settle", msg, data)
        self._check_loss_cap()

    # ---- the ledger's own settlement record (the exchange's revenue); it can lag the public market result
    def _ledger_expects_settlement(self, ticker: str) -> bool:
        """True when the ledger holds a fill (or an order that reported one) on the ticker, so the exchange will publish
        a settlement for it. An order that never filled (cancelled IOC, rejected, never found) has none: waiting for
        one would only stall the shutdown."""
        try:
            if self.ledger.ticker_agg(ticker).n:
                return True
            return any((_num(o.get("fill_count")) or D0) > 0 for o in self.ledger.orders_for(ticker))
        except Exception:
            return True                      # cannot tell: let reconcile.settle decide

    def _queue_ledger(self, ticker: str) -> bool:
        """Queue a ticker for reconcile.settle until the ledger records it. False when there is nothing to settle."""
        if not self._ledger_expects_settlement(ticker):
            return False
        self._ledger_todo.setdefault(ticker, self.clock())
        return True

    def _ledger_settled_tickers(self) -> set[str] | None:
        fn = getattr(self.ledger, "settled_tickers", None)
        if fn is None:
            return None
        try:
            return set(fn())
        except Exception:
            return None

    def _settle_ledger_pending(self, force: bool = False) -> None:
        """Ask reconcile.settle to record the exchange's settlement of every queued ticker, and keep asking (every
        settle_poll_s) until the ledger has it: GET /portfolio/settlements can lag the public market result, and
        until the ledger records it the trade counts as OPEN worst case, not as realized."""
        if not self._ledger_todo:
            return
        now = self.clock()
        if not force and now < self._ledger_next:
            return
        self._ledger_next = now + self.settle_poll_s
        tickers = sorted(self._ledger_todo)
        fn = getattr(self.reconciler, "settle", None)
        if fn is not None:
            try:
                fn(self.client, self.ledger, tickers)
            except Exception as exc:
                self._warn(f"settle:{','.join(tickers)}", f"ledger settlement failed: {type(exc).__name__}: {exc}")
        settled = self._ledger_settled_tickers() if fn is not None else None
        for t in tickers:
            if settled is None or t in settled:
                del self._ledger_todo[t]
                mine = self._live_pnl.pop(t, None)
                if mine is not None:
                    self._cross_check(t, *mine)
            elif now - self._ledger_todo[t] > SETTLE_GIVE_UP_S:
                del self._ledger_todo[t]
                self._event("warn", "settle", f"the ledger has no settlement record for {t} after 6 h; "
                            "run kpm exec reconcile", {"ticker": t})

    def _cross_check(self, ticker: str, pnl: Decimal, order_ids: set[str] | None = None) -> None:
        """Compare our P&L with the ledger's settlement record (the exchange's revenue); report when they differ.

        The ledger's figure covers every fill on the ticker, so it is only comparable when all of them are ours."""
        conn, env = getattr(self.ledger, "conn", None), getattr(self.ledger, "env", None)
        if conn is None or env is None:
            return
        try:
            if order_ids is not None and {f["order_id"] for f in self.ledger.fills_for(ticker)} - order_ids:
                return                       # another order (another strategy) also traded this market
            row = conn.execute("SELECT pnl, pnl_basis, check_status FROM exec_settlements WHERE env = ? AND ticker = ?",
                               (env, ticker)).fetchone()
        except Exception:
            return
        if row is None:
            return
        lp = _num(row[0])
        if (lp is not None and abs(lp - pnl) > Decimal("0.0001")) or row[2] not in (None, "ok"):
            msg = f"P&L of {ticker}: bot {pnl} vs ledger settlement {lp} ({row[2]})"
            self._set_error(msg)
            self._event("error", "settle", msg, {"ticker": ticker, "bot_pnl": pnl, "ledger_pnl": lp,
                                                 "basis": row[1], "check": row[2]})

    def _startup_settle(self) -> None:
        """Live: tickers an earlier run left unsettled in the ledger are settled now, so the loss cap is exact."""
        if not self.live:
            return
        fn = getattr(self.ledger, "unsettled_tickers", None)
        if fn is None:
            return
        try:
            tickers = list(fn())
        except Exception as exc:
            self._warn("startup", f"ledger.unsettled_tickers failed: {exc}")
            return
        done = [t for t in tickers if self._market_result(t) is not None]
        if done:
            self._reconcile(done)                        # resolves unknown orders and records the fills first
            queued = [t for t in done if self._queue_ledger(t)]
            if queued:
                self._settle_ledger_pending(force=True)
            self._event("info", "settle", f"settling {len(queued)} of {len(done)} finished ticker(s) left over from "
                        "an earlier run", {"tickers": done, "queued": queued})

    def _recover_unsettled(self) -> None:
        """Opportunities an earlier run left pending (it was stopped before their market settled) are settled now,
        on their own rows, so the paper twin and live P&L columns are never left empty for good."""
        now = self.clock()
        try:
            rows = self.state.unsettled_opportunities(
                STRATEGY, detected_between=(now - RECOVER_MAX_AGE_S, now - RECOVER_MIN_AGE_S))
        except Exception as exc:
            self._warn("recover", f"could not look for unsettled opportunities: {type(exc).__name__}: {exc}")
            return
        n = 0
        for r in rows:
            try:
                item = self._item_from_row(r)
            except (ArithmeticError, TypeError, ValueError):
                item = None
            if item is not None:
                self._pending.append(item)
                n += 1
        if n:
            self._event("info", "settle", f"{n} opportunity(ies) from earlier runs are still unsettled; "
                        "checking their markets")

    def _item_from_row(self, r: Mapping) -> dict | None:
        outcome = {KIND_YES: "yes", KIND_NO: "no"}.get(r["kind"])
        if outcome is None or r["paper_cost"] is None or r["paper_fees"] is None or r["contracts"] is None:
            return None
        live = bool(r["live"])
        records: list[dict] = []
        if live:
            for o in self.state.live_orders_of(r["run_id"], r["id"]):
                if o["filled"] and o["filled"] > 0 and o["avg_fill_price"] is not None:
                    price = Decimal(repr(o["avg_fill_price"]))
                    records.append({"coid": o["client_order_id"], "order_id": o["order_id"], "ticker": r["hour_ticker"],
                                    "outcome": outcome, "count": Decimal(repr(o["filled"])), "price": price,
                                    "limit": price, "fee": Decimal(repr(o["fees"] or 0.0)), "exact": True})
        return {"opp_id": r["id"], "run_id": r["run_id"], "ticker": r["hour_ticker"], "close_ts": float(r["detected_ts"]),
                "outcome": outcome, "contracts": Decimal(repr(r["contracts"])),
                "paper_cost": Decimal(repr(r["paper_cost"])), "paper_fees": Decimal(repr(r["paper_fees"])),
                "live": live, "resolved": r["outcome"] != "unknown", "records": records, "next_check": 0.0}

    def _kill_requested(self) -> bool:
        """A kill file this run did not write itself (a Safety trip writes one)."""
        return self._kill_active() and not self._tripped

    def _drain_settlements(self) -> None:
        """Wait (bounded) for the last trades to settle so their P&L is booked. Not after a kill: the user asked for a
        stop. A safety trip still drains: it only reads."""
        if not (self._pending or self._ledger_todo):
            return
        deadline = self.clock() + self.settle_wait_s
        while (self._pending or self._ledger_todo) and self.clock() < deadline:
            if self._kill_requested():
                break
            self._settle_pending()
            self._settle_ledger_pending()
            if not (self._pending or self._ledger_todo):
                break
            self._heartbeat("waiting")
            self._sleep_until(min(deadline, self.clock() + TICK_S))
        if self._pending or self._ledger_todo:
            self._event("warn", "settle", f"{len(self._pending)} trade(s) not settled and {len(self._ledger_todo)} "
                        f"ticker(s) not recorded by the ledger within {self.settle_wait_s:.0f} s; a later run finishes them")

    # -------------------------------------------------------------------------------------------- smoke
    def _smoke(self) -> dict:
        """One discovery and one decision pass on public data. Never trades; writes no opportunities or orders."""
        now = self.clock()
        out: dict[str, Any] = {"checked_at": _iso(now)}
        m, why = self._discover(now, upcoming=False)
        c = None if m is None else close_ts(m)
        if m is None or c is None:
            out.update(found=False, reason=why)
            self._event("info", "smoke", f"smoke pass: no open KXBTC15M market: {why}", out)
            return out
        elapsed = elapsed_in_window(now, c)
        out.update(found=True, ticker=m["ticker"], close_ts=c, close=_iso(c), elapsed_s=elapsed,
                   in_decision_window=in_decision_window(now, c),
                   seconds_to_decision_window=max(0.0, DECISION_START_S - elapsed),
                   exchange_index=m.get("exchange_index"), floor_strike=m.get("floor_strike"))
        out.update(book=None, rule=None, would_trade=False)
        book = self._book(str(m["ticker"]))
        if book is not None:
            try:
                q = read_quote(book)
            except (ArithmeticError, TypeError, ValueError) as exc:
                q = None
                out["book_error"] = str(exc)
            if q is not None:
                d = rule(q)
                out.update(book={"yes_bid": q.yes_bid, "yes_ask": q.yes_ask, "no_bid": q.no_bid, "no_ask": q.no_ask,
                                 "mid": q.mid, "book_age_s": book_age(book, self.clock())},
                           rule=d, would_trade=bool(d) and out["in_decision_window"])
        d = out["rule"]
        verdict = (f"the rule would buy {d['outcome'].upper()} at {d['ask']:.4f} (mid {d['mid']:.4f})" if d
                   else "the rule would skip this book")
        if out["in_decision_window"]:
            timing = "inside the decision window"
        elif elapsed < DECISION_START_S:
            timing = f"the decision window opens in {out['seconds_to_decision_window']:.0f} s"
        else:
            timing = "the decision window is over"
        self._event("info", "smoke", f"smoke pass on {m['ticker']}: {verdict}; {timing}", out)
        return out

    # -------------------------------------------------------------------------------------------- state helpers
    def _run_config(self, smoke: bool) -> dict:
        t = self.trading_cfg
        return {"strategy": STRATEGY, "mode": "live" if self.live else "watch", "smoke": bool(smoke),
                "env": getattr(self.ctx, "env_name", None), "key_fp": getattr(self.ctx, "key_fp", None),
                "series": SERIES,
                "rule": {"window_start_s": DECISION_START_S, "window_end_s": DECISION_END_S,
                         "window_length_s": WINDOW_LENGTH_S, "buy_yes_if_mid_above": YES_ABOVE,
                         "buy_no_if_mid_below": NO_BELOW, "order": "IOC at the current ask, held to settlement"},
                "contracts": self.contracts, "max_attempts_per_window": self.max_attempts,
                "poll_interval_s": self.poll_s, "max_book_age_s": self.max_book_age_s,
                "max_loss_usd": self.max_loss, "max_cost_per_order_usd": t.get("max_cost_per_order_usd"),
                "max_contracts_per_order": t.get("max_contracts_per_order"),
                "max_orders_per_minute": t.get("max_orders_per_minute"), "kill_switch_file": str(self.kill_path)}

    def _heartbeat(self, status: str, force: bool = False) -> None:
        now = self.clock()
        if not force and status == self._hb_status and 0 <= now - self._hb_ts < HEARTBEAT_S:
            return                       # (a clock that stepped backwards must not silence the heartbeat: 0 <= ...)
        w = self.window
        detail: dict[str, Any] = {
            "strategy": STRATEGY, "mode": "live" if self.live else "watch", "status": status,
            "market_ticker": None if w is None else w.ticker, "hour_ticker": None if w is None else w.ticker,
            "market_close_ts": None if w is None else w.close_ts,
            "window_start_ts": None if w is None else w.start_ts, "window_end_ts": None if w is None else w.end_ts,
            "loss_used_usd": self._loss_used(), "max_loss_usd": self.max_loss, "balance_usd": self.balance_usd,
            "kill_switch": self._kill_active(), "attempts_this_window": 0 if w is None else w.attempts,
            "windows": self.summary["windows"], "detections": self.summary["detections"],
            "live_fills": self.summary["live_fills"], "polls": self.summary["polls"],
            "clock_skew_s": self._clock_skew()}
        if self.last_error and (status in ("stopped", "error") or now - self._last_error_ts < ERROR_SHOW_S):
            detail["last_error"] = self.last_error           # a stopped run keeps showing why it stopped
        self.state.heartbeat(status, detail, ts=now)
        self._hb_status, self._hb_ts = status, now

    def _heartbeat_safe(self, status: str) -> None:
        try:
            self._heartbeat(status, force=True)
        except Exception:
            log.exception("late_favorite: heartbeat failed")

    def _event(self, level: str, kind: str, message: str, data: Any = None) -> None:
        getattr(log, {"warn": "warning"}.get(level, level))("late_favorite %s: %s", kind, message)
        self.state.event(level, kind, message, data, ts=self.clock())

    def _event_safe(self, level: str, kind: str, message: str, data: Any = None) -> None:
        try:
            self._event(level, kind, message, data)
        except Exception:
            log.exception("late_favorite: could not record event %r", message)

    def _warn(self, key: str, message: str, data: Any = None, every_s: float = 60.0) -> None:
        now = self.clock()
        if 0 <= now - self._warned.get(key, -math.inf) < every_s:
            return
        self._warned[key] = now
        self._event("warn", "error", message, data)

    def _set_error(self, message: str) -> None:
        self.last_error, self._last_error_ts = message[:400], self.clock()

    def _count(self, outcome: str) -> None:
        self.summary["outcomes"][outcome] = self.summary["outcomes"].get(outcome, 0) + 1
