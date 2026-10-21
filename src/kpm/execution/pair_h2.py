"""H2 pair bot: two taker immediate_or_cancel BUYS that together pay at least $1 in every outcome.

Markets. They are found only through open_markets(series) and the close_time / floor_strike / strike_type fields.
Tickers are never parsed.
  15m     The KXBTC15M market closing at HH:00. YES ("UP") iff the 60-second BRTI average before HH:00 is >= T, its
          floor_strike (the average before HH:45). strike_type 'greater_or_equal'.
  hourly  A KXBTCD "above K" market closing at the same HH:00. YES iff that same average is > K, its floor_strike.
          strike_type 'greater'.

Pairs. The kind strings are the ones the dashboard shows; leg_a is the first leg named.
  K <  T   buy hourly YES + buy 15m NO   "hourly YES + 15m DOWN"   pays above_won + (not up_won)
  K >= T   buy 15m YES + buy hourly NO   "15m UP + hourly NO"      pays up_won + (not above_won)
Each pair pays $1 in every outcome, and $2 when the average lands between K and T. In the research
(reports/output/mechanical_edges/h2_summary.json), 0 of 239 detected violations paid less than $1.

Pricing. Prices come only from REST order books, whose arrays are ascending, so the best bid is the LAST level.
  - The ask to buy YES is 1 - best NO bid, and the ask to buy NO is 1 - best YES bid, each with that level's size.
  - The taker fee for each leg is exact, on the OUTCOME price (units.taker_fee).
  - edge = contracts - cost - fees.
The bot acts only when edge >= min_edge_usd per pair, both displayed sizes are >= contracts, and both books are
younger than max_book_age_s.

Modes.
  - Watch (the default) needs no keys. It makes public reads only, runs full detection, writes paper-twin rows and
    keeps the dashboard state current.
  - Live needs ctx.safety, ctx.ledger and a client built with live=True. Every order is an IOC.
    - Its (side, YES price) comes from units.to_book().
    - Its intent is committed to the ledger before it is sent. Both legs go out in one batch_create call. The commits
      take database time, so just before the send the kill file, the halted flag, the time to the close and the books'
      age are checked once more; if any fails the intents are marked "not sent" and nothing leaves the process.
    - It must pass Safety first, with the two legs checked TOGETHER (``check_entry(..., batch=specs)``: the rate limits,
      the loss and exposure caps and the cash check count both legs), and the combined loss and exposure rules for the
      whole pair (realized loss + open worst case + both legs' worst case <= the account-wide cap, at most $5.00;
      open exposure + both legs' collateral <= the exposure cap, at most $3.00).
    - Confirm, then send. The detection books are a poll old, and Safety accepts only an order priced exactly at the
      current ask of a book that is fresh when its check ends. So just before sending, the bot warms Safety's caches
      (Safety.preflight), reads both books again and prices the pair again. If the pair still pays (edge, sizes, age)
      the orders are built from that second look; otherwise nothing is sent and the opportunity ends as
      'none_filled'. The paper twin always keeps the detection prices, so the dashboard's edge_change shows the cost
      of the delay.
  - Leg risk in live mode:
    - If the pair still has edge >= 0, the missing leg gets one retry.
    - Otherwise the filled leg is unwound with a reduce_only IOC priced within 2 ticks of the touch.
  - A write whose outcome is unknown is looked up, never resent. If it cannot be found, Safety is tripped and the bot
    stops.
  - At most max_live_pairs_per_hour pairs per hour, counted from the ledger so a restart does not reset it. A pair is
    one opportunity: it counts unless none of its orders (on either market) was placed, filled or left unresolved.
  - A 401/403 on any authenticated read trips Safety and stops the bot.
  - The trading window ends ``stop_before_close_s`` before the hour closes, and that must be at least Safety's entry
    cutoff (min_seconds_to_close + early_close_buffer_s), or the bot refuses to start: an edge found in the last seconds
    would be booked as a paper twin that live trading could never take.

Settlement. The paper twin and the real P&L are booked when both markets have a result. The ledger's own settlement
record (the exchange's revenue) can lag that, so reconcile.settle is asked again until it is there, and until then the
pair counts as OPEN worst case. Opportunities an earlier run left pending are settled at startup on their own rows.
After a stop the bot drains pending settlements (it only reads), except after a kill, and not when the settlement is
further away than settle_wait_s.

Interface: PairBot(ctx, cfg, live, clock, sleep).run(hours=None, smoke=False). ctx is any object with the
attributes of PairContext. cfg is the settings 'trading' block, or the whole settings dict.
"""
from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from kpm.core.config import PROJECT_ROOT
from kpm.execution import units
from kpm.execution.botstate import BotState
from kpm.execution.client import AuthError
from kpm.execution.safety import entry_cutoff_s

log = logging.getLogger(__name__)

STRATEGY = "pair_h2"
SERIES_15M = "KXBTC15M"
SERIES_HOURLY = "KXBTCD"
KIND_BELOW = "hourly YES + 15m DOWN"     # K < T
KIND_ABOVE = "15m UP + hourly NO"        # K >= T
KINDS = (KIND_BELOW, KIND_ABOVE)
ABOVE_STRIKE_TYPES = frozenset({"greater", "greater_or_equal"})   # YES when the price is high
ACTIVE_STATUSES = frozenset({"active", "open"})

# Ceilings from the user's decisions. Safety enforces them as well; the bot also refuses to exceed them on its own, and a
# configuration above one refuses to start.
CONTRACTS_CEILING = Decimal("1")
MAX_LOSS_CEILING = Decimal("5.00")
COST_PER_ORDER_CEILING = Decimal("1.00")
OPEN_EXPOSURE_CEILING = Decimal("3.00")
PRICE_MIN, PRICE_MAX = Decimal("0.01"), Decimal("0.99")
UNWIND_TICKS = 2

HEARTBEAT_S = 2.0              # the dashboard expects a heartbeat at least every 5 s
TICK_S = 1.0                   # longest sleep between kill-switch checks
DISCOVERY_RETRY_S = 5.0
SETTLE_DELAY_S = 60.0          # first result check this long after the close
SETTLE_GIVE_UP_S = 6 * 3600.0
RECOVER_MIN_AGE_S = 3600.0     # opportunities of earlier runs this old (their hour is long closed) ...
RECOVER_MAX_AGE_S = 7 * 86400.0  # ... and at most this old are settled at startup if still pending
BALANCE_EVERY_S = 60.0
LOOKUP_TRIES = 3
LOOKUP_WAIT_S = 1.0
DATA_FRESH_WAIT_S = 10.0       # bounded wait for the portfolio data to catch up before looking an order up
ERROR_SHOW_S = 600.0           # last_error stays in the heartbeat this long
MAX_ROWS_PER_STRIKE_HOUR = 5   # hard bound on opportunity rows per strike and hour
# A ledger order in one of these states (or with any fill) means a live pair was already tried on that market.
TRADED_STATUSES = frozenset({"intent", "unknown", "duplicate", "acked", "resting", "executed"})

D0, D1 = Decimal(0), Decimal(1)


# ------------------------------------------------------------------------------------------------ value helpers

def _d(v: Any) -> Decimal:
    """Exact Decimal from a Decimal, int or decimal string. Floats and bools are refused because they are not exact."""
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
    """A config number. YAML may give a float, which is converted through its shortest repr (0.001 -> '0.001')."""
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


def _cents(x: Decimal) -> str:
    return f"{x * 100:.3f}c"


def _items(resp: Any, key: str) -> list:
    if isinstance(resp, Mapping):
        v = resp.get(key)
        return list(v) if isinstance(v, (list, tuple)) else []
    if isinstance(resp, (list, tuple)):
        return list(resp)
    return []


def _str_or_none(v: Any) -> str | None:
    return None if v is None or v == "" else str(v)


# ------------------------------------------------------------------------------------------------ book math

def touch(levels: Any) -> tuple[Decimal, Decimal] | None:
    """Best bid (price, size) of one REST book side. The arrays are ascending, so the best bid is the LAST level.

    Returns None for an empty side or an unusable top level (a placeholder, price outside (0, 1), or size <= 0).
    Raises ValueError if the side is not ascending. That would mean the API changed, and the bot must not guess.
    """
    if not levels:
        return None
    prev = None
    for lv in levels:
        p = _d(lv[0])
        if prev is not None and p < prev:
            raise ValueError("order book side is not ascending (best bid must be the last level)")
        prev = p
    price, size = _d(levels[-1][0]), _d(levels[-1][1])
    if not (D0 < price < D1) or size <= 0:
        return None
    return price, size


def outcome_ask(book: Mapping, outcome: str) -> tuple[Decimal, Decimal] | None:
    """(ask, size) to BUY an outcome: YES ask = 1 - best NO bid, NO ask = 1 - best YES bid, with that level's size."""
    if outcome == "yes":
        t = touch(book.get("no") or ())
    elif outcome == "no":
        t = touch(book.get("yes") or ())
    else:
        raise ValueError(f"outcome must be 'yes' or 'no', got {outcome!r}")
    return None if t is None else (D1 - t[0], t[1])


def is_crossed(book: Mapping) -> bool:
    """True when the best YES bid plus the best NO bid is at least 1: a crossed or locked book. That is stale or
    transitional data (a real book never crosses), and no price may be taken from it. late_favorite.read_quote and
    Safety._check_book refuse such a book too, so a pair priced from it could never be sent."""
    yes, no = touch(book.get("yes") or ()), touch(book.get("no") or ())
    return yes is not None and no is not None and yes[0] + no[0] >= D1


def book_age(book: Mapping | None, now: float) -> float:
    try:
        return now - float(book["fetched_at"])        # type: ignore[index]
    except (KeyError, TypeError, ValueError):
        return math.inf


def pair_kind(target: Decimal, strike: Decimal) -> str:
    return KIND_BELOW if strike < target else KIND_ABOVE


def pair_legs(target: Any, strike: Any, book_15m: Mapping, book_hourly: Mapping, contracts: Any,
              fee_fn: Callable[[Decimal, Decimal], Decimal] | None = None) -> dict | None:
    """Price the riskless pair for one hourly strike.

    Returns dict(kind, legs=[leg_a, leg_b], cost, fees, edge, contracts, target, strike). Each leg is
    {ticker_role: '15m'|'hourly', outcome: 'yes'|'no', ask, size}. cost, fees and edge are totals for `contracts`
    pairs: cost = (ask_a + ask_b) * contracts, fees = exact taker fee per leg on its outcome price,
    edge = contracts - cost - fees. Returns None when either leg has no ask (an empty side) or either book is crossed
    or locked (best YES bid + best NO bid >= 1: no price may be taken from it).
    """
    fee_fn = fee_fn or units.taker_fee
    target, strike, contracts = _d(target), _d(strike), _d(contracts)
    if contracts <= 0:
        raise ValueError("contracts must be > 0")
    kind = pair_kind(target, strike)
    plan = ([("hourly", "yes", book_hourly), ("15m", "no", book_15m)] if kind == KIND_BELOW
            else [("15m", "yes", book_15m), ("hourly", "no", book_hourly)])
    legs = []
    for role, outcome, book in plan:
        if is_crossed(book):
            return None
        a = outcome_ask(book, outcome)
        if a is None:
            return None
        legs.append({"ticker_role": role, "outcome": outcome, "ask": a[0], "size": a[1]})
    cost = (legs[0]["ask"] + legs[1]["ask"]) * contracts
    fees = fee_fn(contracts, legs[0]["ask"]) + fee_fn(contracts, legs[1]["ask"])
    return {"kind": kind, "legs": legs, "cost": cost, "fees": fees, "edge": contracts - cost - fees,
            "contracts": contracts, "target": target, "strike": strike}


def pair_payout(kind: str, up_won: bool, above_won: bool) -> Decimal:
    """Dollars paid per pair (one contract per leg). up_won: the 15m market settled YES. above_won: the hourly did."""
    if kind == KIND_BELOW:       # hourly YES + 15m NO
        return Decimal(int(bool(above_won)) + int(not up_won))
    if kind == KIND_ABOVE:       # 15m YES + hourly NO
        return Decimal(int(bool(up_won)) + int(not above_won))
    raise ValueError(f"unknown pair kind {kind!r}")


# ------------------------------------------------------------------------------------------------ discovery

def close_ts(market: Mapping) -> float | None:
    s = market.get("close_time")
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        return float(units.parse_rfc3339(s))
    except (TypeError, ValueError):
        return None


def strike_value(market: Mapping, key: str = "floor_strike") -> Decimal | None:
    """floor_strike arrives as a JSON number. A float goes through its shortest repr (85319.11 -> '85319.11')."""
    v = market.get(key)
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, float):
        return Decimal(repr(v)) if math.isfinite(v) else None
    return _num(v)


def _at(market: Mapping, hour_ts: float) -> bool:
    c = close_ts(market)
    return c is not None and abs(c - hour_ts) < 0.5


def _active(market: Mapping) -> bool:
    s = market.get("status")
    return s is None or str(s).lower() in ACTIVE_STATUSES


def _above_type(market: Mapping) -> bool:
    return market.get("strike_type") in ABOVE_STRIKE_TYPES and market.get("cap_strike") is None


def hourly_strikes(markets_hourly: Iterable[Mapping], hour_ts: float, reference: Decimal,
                   n: int) -> list[tuple[Decimal, dict]]:
    """The n nearest open 'above' strikes below the reference and the n nearest at or above it, closing at hour_ts."""
    by_ticker: dict[str, tuple[Decimal, dict]] = {}
    for m in markets_hourly or ():
        if not isinstance(m, Mapping) or not m.get("ticker"):
            continue
        if not (_at(m, hour_ts) and _active(m) and _above_type(m)):
            continue
        k = strike_value(m)
        if k is not None:
            by_ticker[str(m["ticker"])] = (k, dict(m))
    ranked = sorted(by_ticker.values(), key=lambda km: (km[0], str(km[1]["ticker"])))
    below = [x for x in ranked if x[0] < reference]
    above = [x for x in ranked if x[0] >= reference]
    return (below[-n:] if n > 0 else []) + above[:max(n, 0)]


def select_markets(markets_15m: Iterable[Mapping], markets_hourly: Iterable[Mapping], hour_ts: float,
                   strikes_each_side: int) -> tuple[dict | None, str]:
    """(selection, reason). The selection is {hour_ts, m15, target, strikes: [(K, market)]}, or None with a reason."""
    c15: dict[str, dict] = {}
    for m in markets_15m or ():
        if isinstance(m, Mapping) and m.get("ticker") and _at(m, hour_ts) and _active(m):
            c15[str(m["ticker"])] = dict(m)
    if not c15:
        return None, "no open KXBTC15M market closes at this hour yet (it opens 15 minutes before the hour)"
    if len(c15) > 1:
        return None, f"{len(c15)} open KXBTC15M markets close at this hour; refusing to guess"
    m15 = next(iter(c15.values()))
    if not _above_type(m15):
        return None, (f"KXBTC15M market {m15['ticker']} has strike_type {m15.get('strike_type')!r}; "
                      "an 'up' (greater_or_equal) market is required")
    target = strike_value(m15)
    if target is None:
        return None, f"the target (floor_strike) of {m15['ticker']} is not set yet"
    strikes = hourly_strikes(markets_hourly, hour_ts, target, strikes_each_side)
    if not strikes:
        return None, "no open KXBTCD 'above' market closes at this hour"
    return {"hour_ts": hour_ts, "m15": m15, "target": target, "strikes": strikes}, ""


# ------------------------------------------------------------------------------------------------ orders

def step_price(outcome: str, outcome_price: Decimal, price_ranges: Iterable[Mapping], ticks: int) -> Decimal | None:
    """Move an outcome price `ticks` grid steps UP, which is more aggressive for a buy.

    The grid (price_ranges) is defined on YES prices. A NO price therefore moves by stepping its YES equivalent DOWN.
    Band edges use the step of the band being entered. Returns None if the price is off the grid.
    """
    try:
        bands = [(_d(b["start"]), _d(b["end"]), _d(b["step"])) for b in price_ranges]
    except (KeyError, TypeError, ValueError):
        return None
    up = outcome == "yes"
    y = outcome_price if up else D1 - outcome_price
    for _ in range(ticks):
        nxt = None
        for start, end, step in bands:
            if step <= 0:
                continue
            if up and start <= y < end and (y - start) % step == 0:
                nxt = y + step
                break
            if not up and start < y <= end and (y - start) % step == 0:
                nxt = y - step
                break
        if nxt is None:
            return None
        y = nxt
    return y if up else D1 - y


def leg_spec(market: Mapping, outcome: str, outcome_price: Decimal, count: Decimal, *, purpose: str,
             opportunity_id: int | None = None, role: str | None = None, reduce_only: bool = False) -> dict:
    """One IOC BUY of `outcome` at `outcome_price`, as the spec handed to Safety and Ledger.new_intent().

    (side, yes_price) comes only from units.to_book(). An unwind is a reduce_only buy of the opposite outcome.
    """
    ei = market.get("exchange_index")
    if isinstance(ei, bool) or not isinstance(ei, int):
        raise ValueError(f"market {market.get('ticker')} has no exchange_index")
    side, yes_price = units.to_book(outcome, outcome_price)
    return {"strategy": STRATEGY, "purpose": purpose, "role": role, "opportunity_id": opportunity_id,
            "ticker": str(market["ticker"]), "exchange_index": ei, "action": "buy", "outcome": outcome,
            "outcome_price": outcome_price, "side": side, "yes_price": yes_price, "count": count,
            "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross",
            "reduce_only": bool(reduce_only)}


def order_body(spec: Mapping, client_order_id: str) -> dict:
    """CreateOrderV2 body: YES-book side, YES price as a 4-dp dollar string, count as a 2-dp contract string."""
    body = {"ticker": spec["ticker"], "client_order_id": client_order_id, "side": spec["side"],
            "price": units.fmt_price(spec["yes_price"]), "count": units.fmt_count(spec["count"]),
            "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross",
            "exchange_index": spec["exchange_index"]}
    if spec.get("reduce_only"):
        body["reduce_only"] = True
    return body


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
class PairContext:
    """What PairBot needs. Any object with these attributes works (e.g. types.SimpleNamespace)."""
    client: Any                     # ExecClient: public reads always; authenticated reads and writes when live
    state: BotState                 # dashboard tables for this run
    ledger: Any = None              # Ledger, required when live
    safety: Any = None              # Safety, required when live; Safety.startup() must have passed already
    reconcile: Any = None           # namespace with reconcile(), settle() and cancel_path(); default kpm.execution.reconcile
    env_name: str | None = None
    key_fp: str | None = None


# ------------------------------------------------------------------------------------------------ the bot

class PairBot:
    def __init__(self, ctx: Any, cfg: Mapping | None, live: bool = False, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Any] = time.sleep, *, fee_fn: Callable[[Decimal, Decimal], Decimal] | None = None,
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
        pair = trading.get("pair") or {}
        self.trading_cfg = dict(trading)
        self.contracts = _cfg_dec(pair.get("contracts", 1), "pair.contracts")
        if not (D0 < self.contracts <= CONTRACTS_CEILING) or self.contracts != self.contracts.quantize(Decimal("0.01")):
            raise ValueError(f"pair.contracts must be a multiple of 0.01 in (0, {CONTRACTS_CEILING}], got {self.contracts}")
        self.min_edge = _cfg_dec(pair.get("min_edge_usd", "0.001"), "pair.min_edge_usd")
        if self.min_edge < 0:
            raise ValueError("pair.min_edge_usd must be >= 0")
        self.window_s = float(pair.get("window_min", 15)) * 60.0
        self.strikes_each_side = int(pair.get("strikes_each_side", 2))
        self.poll_s = float(pair.get("poll_interval_s", 1.0))
        self.max_book_age_s = float(pair.get("max_book_age_s", 2.0))
        self.stop_before_close_s = float(pair.get("stop_before_close_s", 60.0))
        self.max_live_pairs_per_hour = int(pair.get("max_live_pairs_per_hour", 1))
        self.max_opps_per_strike_hour = int(pair.get("max_opportunities_per_strike_hour", 1))
        self.settle_poll_s = float(pair.get("settle_poll_s", 30.0))
        self.settle_wait_s = float(pair.get("settle_wait_s", 900.0))
        for name, ok in (("pair.window_min", 0 < self.window_s <= 3600),
                         ("pair.strikes_each_side", 1 <= self.strikes_each_side <= 10),
                         ("pair.poll_interval_s", 0.2 <= self.poll_s <= 60),
                         ("pair.max_book_age_s", 0 < self.max_book_age_s <= 30),
                         ("pair.stop_before_close_s", 0 <= self.stop_before_close_s < self.window_s),
                         ("pair.max_live_pairs_per_hour", 0 <= self.max_live_pairs_per_hour <= 10),
                         ("pair.max_opportunities_per_strike_hour", 1 <= self.max_opps_per_strike_hour <= 5),
                         ("pair.settle_poll_s", self.settle_poll_s > 0), ("pair.settle_wait_s", self.settle_wait_s >= 0)):
            if not ok:
                raise ValueError(f"{name} is out of range")
        self.max_loss = _cfg_dec(trading.get("max_loss_usd", MAX_LOSS_CEILING), "trading.max_loss_usd")
        if not (D0 < self.max_loss <= MAX_LOSS_CEILING):          # a configuration above a ceiling refuses to start
            raise ValueError(f"trading.max_loss_usd must be in (0, {MAX_LOSS_CEILING}], got {self.max_loss}")
        self.max_open_exposure = _cfg_dec(trading.get("max_open_exposure_usd", OPEN_EXPOSURE_CEILING),
                                          "trading.max_open_exposure_usd")
        if not (D0 < self.max_open_exposure <= OPEN_EXPOSURE_CEILING):
            raise ValueError(f"trading.max_open_exposure_usd must be in (0, {OPEN_EXPOSURE_CEILING}], "
                             f"got {self.max_open_exposure}")
        # Safety refuses an entry this close to the close (min_seconds_to_close + the early-close buffer): the trading
        # window must end before that, or what it detects in its last seconds can only ever be a paper twin.
        self.entry_cutoff_s = entry_cutoff_s(trading)
        if self.stop_before_close_s < self.entry_cutoff_s:
            raise ValueError(f"pair.stop_before_close_s is {self.stop_before_close_s:g} but Safety refuses every entry in "
                             f"the last {self.entry_cutoff_s:g} s before the close (min_seconds_to_close + "
                             f"early_close_buffer_s): a pair found later could only be a paper twin that live trading can "
                             f"never take. Set it to at least {self.entry_cutoff_s:g}.")
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

        self.summary: dict[str, Any] = {"hours": 0, "polls": 0, "quotes": 0, "opportunities": 0, "live_attempts": 0,
                                        "blocked": 0, "gone": 0, "outcomes": {}, "settled": 0, "recovered": 0,
                                        "end_reason": None}
        self.hour_ts: float | None = None
        self.hour_ticker: str | None = None
        self.target: Decimal | None = None
        self.window_start_ts: float | None = None
        self.window_end_ts: float | None = None
        self.live_pairs_hour = 0
        self.balance_usd: Decimal | None = None
        self.last_error: str | None = None
        self._last_error_ts = -math.inf
        self._exchange_index: int | None = None
        self._strikes: dict[str, dict] = {}
        self._pending: list[dict] = []
        self._touched: set[str] = set()
        self._ledger_todo: dict[str, float] = {}     # live tickers whose settlement the ledger has not recorded yet
        self._ledger_next = 0.0
        self._live_pnl: dict[int, tuple[Decimal, list[str], set[str]]] = {}   # opp id -> (our P&L, tickers, our order ids)
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
    def run(self, hours: int | None = None, smoke: bool = False) -> dict:
        """Trade (or watch) `hours` hourly windows, or forever when hours is None. smoke=True: one public pass, exit."""
        if smoke and self.live:
            raise ValueError("smoke mode reads public data only; run it without live")
        if hours is not None and int(hours) < 1:
            raise ValueError("hours must be >= 1")
        mode = "live" if self.live else "watch"
        self.state.start(STRATEGY, mode, self._run_config(smoke), ts=self.clock())
        self._event("info", "start", f"pair bot started in {mode} mode" + (" (smoke pass)" if smoke else ""),
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
                done = 0
                self._recover_unsettled()
                while not self._stop and (hours is None or done < int(hours)):
                    self._run_hour(self._next_hour(self.clock()))
                    done += 1
                    self.summary["hours"] = done
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
            log.exception("pair bot: could not record the end of run %s", self.state.run_id)
        self._heartbeat_safe("stopped")
        self._event_safe("info", "stop", f"pair bot stopped: {reason}", {k: v for k, v in self.summary.items()
                                                                        if k != "smoke"})

    def _next_hour(self, now: float) -> int:
        h = int(math.floor(now / 3600.0)) * 3600 + 3600
        if now >= h - self.stop_before_close_s:
            h += 3600
        return h

    def _sleep_until(self, t: float, cap: float = TICK_S) -> None:
        """Sleep until ``t``, but never more than ``cap`` seconds in one call. A wall clock that steps backwards while a
        poll is in progress puts ``t`` far ahead; one long sleep would leave the kill file and the heartbeat unattended
        for that long. The callers loop, so a capped sleep only means the next iteration comes sooner."""
        dt = t - self.clock()
        if dt > 0:
            self.sleep(min(dt, cap))

    # -------------------------------------------------------------------------------------------- one hour
    def _run_hour(self, hour: int) -> None:
        self.hour_ts, self.hour_ticker, self.target = hour, None, None
        self.window_start_ts = hour - self.window_s
        self.window_end_ts = hour - self.stop_before_close_s
        self.live_pairs_hour = 0
        self._strikes = {}
        start, end = self.window_start_ts, self.window_end_ts

        while not self._stop and self.clock() < start:
            self._idle("waiting")
            if self._stop:
                return
            self._sleep_until(min(start, self.clock() + TICK_S))

        sel, why, next_try = None, "", self.clock()
        while not self._stop and self.clock() < end:
            if self.clock() >= next_try:
                sel, why = self._discover(hour)
                if sel:
                    break
                self._warn(f"discover:{hour}", f"waiting for the pair markets of the hour closing {_iso(hour)}: {why}",
                           every_s=300)
                next_try = self.clock() + DISCOVERY_RETRY_S
            self._idle("monitoring")
            self._sleep_until(min(end, next_try, self.clock() + TICK_S))
        if self._stop:
            return
        if sel is None:
            self._event("warn", "window", f"no pair to watch for the hour closing {_iso(hour)}: {why}")
            return

        self._enter_hour(sel)
        self._event("info", "window",
                    f"watching {self.hour_ticker} (target {self.target}) against {len(sel['strikes'])} KXBTCD strikes "
                    f"until {_iso(end)}",
                    {"hour_ticker": self.hour_ticker, "target": self.target, "hour_close_ts": hour,
                     "window_end_ts": end, "strikes": [{"ticker": m["ticker"], "strike": k} for k, m in sel["strikes"]]})
        polls0, opps0 = self.summary["polls"], self.summary["opportunities"]
        while not self._stop:
            t0 = self.clock()
            if t0 >= end or self._check_kill() or self._check_halted():
                break
            self._poll(sel)
            if self._stop:
                break
            self._refresh_balance()
            self._refresh_clock()
            self._heartbeat("monitoring")
            self._sleep_until(min(end, t0 + self.poll_s), cap=max(self.poll_s, TICK_S))
        self._event("info", "window", f"window closed for {self.hour_ticker}: {self.summary['polls'] - polls0} polls, "
                    f"{self.summary['opportunities'] - opps0} opportunities")

    def _enter_hour(self, sel: dict) -> None:
        self.hour_ticker = str(sel["m15"]["ticker"])
        self.target = sel["target"]
        ei = sel["m15"].get("exchange_index")
        if isinstance(ei, int) and not isinstance(ei, bool):
            self._exchange_index = ei
        self.live_pairs_hour = self._ledger_pairs([self.hour_ticker] + [str(m["ticker"]) for _k, m in sel["strikes"]])

    def _ledger_pairs(self, tickers: Iterable[str]) -> int:
        """Live pairs already tried this hour, from the ledger: a restart within the hour must not reset
        max_live_pairs_per_hour. If the ledger cannot be read the budget is treated as used.

        A pair is one opportunity, whose legs' orders share a group id and sit on two markets (the 15m market and one
        KXBTCD strike). It counts unless none of its orders was placed, filled or left unresolved, which is exactly the
        rule of the in-memory counter (every outcome but none_filled counts). Looking at the 15m leg alone missed a pair
        whose hourly leg filled and whose 15m leg never did (then unwound or held): a restart in the same hour would
        have sent another one."""
        fn = getattr(self.ledger, "orders_for", None)
        if not self.live or fn is None:
            return 0
        pairs: set[str] = set()
        try:
            for ticker in dict.fromkeys(tickers):
                for r in fn(ticker):
                    if r.get("purpose") == "pair_entry" and (
                            r.get("status") in TRADED_STATUSES or (_num(r.get("fill_count")) or D0) > 0):
                        pairs.add(str(r.get("group_id") or r.get("client_order_id")))
        except Exception as exc:
            self._warn("ledger", f"ledger read failed ({exc}); no live pair this hour")
            return self.max_live_pairs_per_hour
        return len(pairs)

    def _discover(self, hour: float) -> tuple[dict | None, str]:
        try:
            markets_15m = self.client.open_markets(SERIES_15M)
            markets_hourly = self.client.open_markets(SERIES_HOURLY)
        except Exception as exc:
            return None, f"market discovery failed: {type(exc).__name__}: {exc}"
        return select_markets(markets_15m, markets_hourly, hour, self.strikes_each_side)

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

    # -------------------------------------------------------------------------------------------- pricing
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

    def _poll(self, sel: dict, record: bool = True) -> list[dict]:
        """One pricing pass. It fetches the 15m book once and each strike's book, and writes bot_quotes.

        When `record` is set, it acts on each new opportunity as soon as that opportunity is detected.
        Returns the opportunities found.
        """
        ts = self.clock()
        m15 = sel["m15"]
        book15 = self._book(m15["ticker"])
        rows: list[dict] = []
        found: list[dict] = []
        if book15 is not None:
            for strike, mh in sel["strikes"]:
                st = self._strikes.setdefault(mh["ticker"], {"active": False, "used": 0, "rows": 0})
                bookh = self._book(mh["ticker"])
                q = None
                if bookh is not None:
                    try:
                        q = pair_legs(sel["target"], strike, book15, bookh, self.contracts, self.fee_fn)
                    except (ArithmeticError, TypeError, ValueError) as exc:
                        self._warn(f"price:{mh['ticker']}", f"cannot price {mh['ticker']}: {exc}")
                if q is None:
                    st["active"] = False
                    continue
                a, b = q["legs"]
                rows.append({"ts": ts, "hour_ticker": m15["ticker"], "strike_ticker": mh["ticker"], "kind": q["kind"],
                             "leg_a_ask": a["ask"], "leg_a_size": a["size"], "leg_b_ask": b["ask"],
                             "leg_b_size": b["size"], "cost": q["cost"], "fees": q["fees"], "edge": q["edge"]})
                now = self.clock()
                ok = (q["edge"] >= self.min_edge * self.contracts
                      and all(leg["size"] >= self.contracts for leg in q["legs"])
                      and book_age(book15, now) <= self.max_book_age_s
                      and book_age(bookh, now) <= self.max_book_age_s)
                new = (ok and not st["active"] and st["used"] < self.max_opps_per_strike_hour
                       and st["rows"] < MAX_ROWS_PER_STRIKE_HOUR)
                st["active"] = ok
                if not new:
                    continue
                st["used"] += 1
                st["rows"] += 1
                found.append({"detected_ts": now, "strike_ticker": mh["ticker"], "strike": strike, "kind": q["kind"],
                              "cost": q["cost"], "fees": q["fees"], "edge": q["edge"],
                              "legs": [dict(leg) for leg in q["legs"]]})
                if record:
                    attempted = self._opportunity(q, sel, mh, book15, bookh, now)
                    if attempted or self._stop:
                        break          # our own orders changed the books; price again on the next poll
        if rows:
            self.state.quotes(rows)
        self.summary["polls"] += 1
        self.summary["quotes"] += len(rows)
        return found

    # -------------------------------------------------------------------------------------------- opportunities
    def _opportunity(self, q: dict, sel: dict, mh: dict, book15: Mapping, bookh: Mapping, detected: float) -> bool:
        """Write the paper twin, then trade it if live. Returns True when live orders were sent."""
        m15 = sel["m15"]
        opp_id = self.state.opportunity(detected_ts=detected, hour_ticker=m15["ticker"], strike_ticker=mh["ticker"],
                                        kind=q["kind"], contracts=self.contracts, paper_cost=q["cost"],
                                        paper_fees=q["fees"], paper_edge=q["edge"], live=False,
                                        outcome=None if self.live else "paper_only")
        self.summary["opportunities"] += 1
        self._event("info", "opportunity",
                    f"{q['kind']}: edge {_cents(q['edge'])} on {mh['ticker']} vs {m15['ticker']}",
                    {"opportunity_id": opp_id, "target": q["target"], "strike": q["strike"], "cost": q["cost"],
                     "fees": q["fees"], "edge": q["edge"], "legs": q["legs"],
                     "book_age_s": {"15m": book_age(book15, detected), "hourly": book_age(bookh, detected)}})
        item = {"opp_id": opp_id, "hour_ts": sel["hour_ts"], "hour_ticker": str(m15["ticker"]),
                "strike_ticker": str(mh["ticker"]), "kind": q["kind"], "contracts": self.contracts,
                "paper_cost": q["cost"], "paper_fees": q["fees"], "live": False, "resolved": True, "records": [],
                "next_check": 0.0}
        self._pending.append(item)
        markets = {"15m": m15, "hourly": mh}
        if not self.live:
            self._paper_orders(opp_id, q, markets, detected)
            self._count("paper_only")
            return False
        return self._try_live(opp_id, item, q, markets, {"15m": book15, "hourly": bookh}, detected)

    def _paper_orders(self, opp_id: int, q: dict, markets: Mapping, ts: float) -> None:
        """Dry-run bot_orders rows that show exactly what a live run would have sent."""
        for leg in q["legs"]:
            m = markets[leg["ticker_role"]]
            coid = "paper-" + uuid.uuid4().hex
            try:
                body = order_body(leg_spec(m, leg["outcome"], leg["ask"], self.contracts, purpose="paper",
                                           opportunity_id=opp_id, role=leg["ticker_role"]), coid)
            except (KeyError, TypeError, ValueError):
                body = None
            self.state.order(client_order_id=coid, ticker=m["ticker"], side=leg["outcome"], action="buy",
                             count=self.contracts, limit_price=leg["ask"], tif="ioc", status="dry_run",
                             opportunity_id=opp_id, ts_sent=ts, dry_run=True,
                             response={"dry_run": True, "would_send": body})

    def _entry_block_reason(self) -> str | None:
        if self._stop:
            return "the bot is stopping"
        if self._check_kill():
            return "kill switch"
        if self._safety_halted():
            return "safety is halted"
        if self.live_pairs_hour >= self.max_live_pairs_per_hour:
            return f"max_live_pairs_per_hour ({self.max_live_pairs_per_hour}) reached for this hour"
        return None

    def _blocked(self, opp_id: int, reason: str) -> bool:
        self.state.update_opportunity(opp_id, outcome="blocked_by_cap")
        self.summary["blocked"] += 1
        self._count("blocked_by_cap")
        self._event("warn", "cap", f"live pair not sent: {reason}", {"opportunity_id": opp_id})
        return False

    def _gone(self, opp_id: int, item: dict, why: str) -> bool:
        """The opportunity did not survive the fresh look just before the orders: nothing was sent."""
        self.state.update_opportunity(opp_id, outcome="none_filled")
        st = self._strikes[item["strike_ticker"]]
        st["used"] -= 1                       # nothing was sent: if the edge is still there on the next poll it is a
        st["active"] = False                  # new look (bounded by MAX_ROWS_PER_STRIKE_HOUR rows per strike and hour)
        self.summary["gone"] += 1
        self._count("none_filled")
        self._event("info", "opportunity", f"opportunity gone before the orders could be sent: {why}",
                    {"opportunity_id": opp_id})
        return False

    def _halt_if_capped(self) -> None:
        """A pair does not fit under the cap. If nothing is open that could free room, no pair ever will: stop."""
        try:
            open_worst = _dec_any(self.ledger.open_worst_case())
        except Exception:
            return
        if open_worst <= 0 and not any(i["live"] for i in self._pending):
            self._trip(f"the {self.max_loss} loss cap leaves no room for another pair")

    def _warm_safety(self, market: Mapping) -> None:
        """Let Safety refresh its cached reads for a market (listing, shard status, cash, position, resting orders), so
        its checks are fast and the books they are handed are still fresh when they end. Optional."""
        fn = getattr(self.safety, "preflight", None)
        if fn is None:
            return
        try:
            problem = fn(market)
        except Exception as exc:
            problem = f"preflight failed: {type(exc).__name__}: {exc}"
        if problem:
            self._warn(f"warm:{market.get('ticker')}:{problem}", f"safety preflight for {market.get('ticker')}: {problem}",
                       every_s=120)

    def _fresh_pricing(self, q: dict, markets: Mapping) -> tuple[dict | None, dict | None, str]:
        """Warm Safety, re-read both order books and price the pair again.

        Safety accepts an order only when it is priced EXACTLY at the current ask of a book that is fresh when the check
        ends, and the detection books are already a poll old. So the orders are built from this second look, and only
        if the pair still pays: edge >= min_edge, both displayed sizes >= contracts, both books fresh.
        Returns (pricing, books, "") or (None, None, why).
        """
        for role in ("15m", "hourly"):
            self._warm_safety(markets[role])
        books = {role: self._book(markets[role]["ticker"]) for role in ("15m", "hourly")}
        if any(b is None for b in books.values()):
            return None, None, "an order book could not be read"
        try:
            q2 = pair_legs(q["target"], q["strike"], books["15m"], books["hourly"], self.contracts, self.fee_fn)
        except (ArithmeticError, TypeError, ValueError) as exc:
            return None, None, f"cannot price the pair again: {exc}"
        now = self.clock()
        if q2 is None:
            return None, None, "a leg has no ask any more"
        if q2["edge"] < self.min_edge * self.contracts:
            return None, None, f"edge is now {q2['edge']} (min {self.min_edge * self.contracts})"
        if any(leg["size"] < self.contracts for leg in q2["legs"]):
            return None, None, "a leg shows fewer contracts than needed"
        if any(book_age(b, now) > self.max_book_age_s for b in books.values()):
            return None, None, "an order book is not fresh"
        return q2, books, ""

    def _try_live(self, opp_id: int, item: dict, q: dict, markets: Mapping, books: Mapping, detected: float) -> bool:
        reason = self._entry_block_reason()
        if reason:
            return self._blocked(opp_id, reason)
        left = self.hour_ts - self.clock()
        if left < self.entry_cutoff_s:               # Safety would refuse it: not a cap, simply too late, so say that
            return self._gone(opp_id, item, f"only {left:.0f} s to the close, less than the {self.entry_cutoff_s:.0f} s "
                                            "Safety needs for an entry")
        q2, books2, why = self._fresh_pricing(q, markets)
        if q2 is None:
            return self._gone(opp_id, item, why)
        q, books = q2, books2
        specs = []
        for leg in q["legs"]:
            role = leg["ticker_role"]
            try:
                specs.append(leg_spec(markets[role], leg["outcome"], leg["ask"], self.contracts, purpose="pair_entry",
                                      opportunity_id=opp_id, role=role))
            except (KeyError, TypeError, ValueError) as exc:
                return self._blocked(opp_id, f"cannot build the {role} order: {exc}")
        # Safety first, leg by leg but with the WHOLE pair in view (batch=specs): neither leg is in the ledger yet when the
        # other is checked, so each leg's rate, exposure, loss and cash checks add up both legs. When a cap is about to
        # refuse, Safety records the settlement of old positions the ledger has not seen yet, which can make room. Then
        # this bot's own rule for the whole pair: the backstop when Safety says yes, and the explanation when it says no
        # (a cap that leaves no room stops the bot).
        refusal = None
        for leg, spec in zip(q["legs"], specs):
            role = leg["ticker_role"]
            ok, why = self._safety_check("check_entry", spec, books[role], markets[role], batch=specs)
            if not ok:
                refusal = f"{role} {leg['outcome'].upper()} leg refused by safety: {why}"
                break
        loss_ok, loss_why = self._pair_loss_ok(specs)
        if not loss_ok:
            if loss_why.startswith("combined loss rule"):
                self._blocked(opp_id, loss_why)
                self._halt_if_capped()
                return False
            return self._blocked(opp_id, loss_why)
        if refusal:
            return self._blocked(opp_id, refusal)
        if self._check_kill():
            return self._blocked(opp_id, "kill switch")

        sent = [self._intent(s) for s in specs]                    # committed before anything is sent
        ts_sent = self.clock()
        for spec, (coid, body) in zip(specs, sent):
            self._order_row(coid, spec, opp_id, ts_sent, body)
            self._touched.add(spec["ticker"])
        self.state.update_opportunity(opp_id, live=True)
        item["live"] = True
        coids = [coid for coid, _ in sent]
        marked = self._mark_sent(coids, ts_sent)
        # Those commits can take long (a locked database waits up to its busy timeout): look again, now, at what made the
        # pair allowed. Anything that no longer holds means the committed intents are NOT sent.
        stop, why_not = self._unsafe_to_send(books, marked)
        if why_not:
            return self._abort_unsent(opp_id, item, specs, sent, stop, why_not)
        try:
            results = self._match_results(coids, self.client.batch_create([body for _, body in sent]))
        except Exception as exc:     # the batch may or may not have reached the exchange: unknown, never resent
            results = [_Outcome("unknown", error=f"{type(exc).__name__}: {exc}") for _ in sent]
        ts_ack = self.clock()
        latency_ms = (ts_ack - detected) * 1000.0
        sends = [self._finish_send(spec, coid, body, res, ts_ack, ts_sent)
                 for spec, (coid, body), res in zip(specs, sent, results)]

        records: list[dict] = []
        item["records"] = records
        outcome = self._resolve_legs(opp_id, specs, sends, markets, records)
        item["resolved"] = outcome != "unknown"
        self._reconcile(sorted({s["ticker"] for s in specs}))
        for r in records:
            self._refine(r)
        cost = sum((r["count"] * r["price"] for r in records), D0)
        fees = sum((r["fee"] for r in records), D0)
        self.state.update_opportunity(opp_id, outcome=outcome, live_cost=cost, live_fees=fees, latency_ms=latency_ms)
        if outcome == "none_filled":
            self._strikes[item["strike_ticker"]]["used"] -= 1     # nothing happened: the strike may be retried later
        else:
            self.live_pairs_hour += 1
        self.summary["live_attempts"] += 1
        self._count(outcome)
        level = {"both_filled": "info", "none_filled": "info", "one_leg_unwound": "warn"}.get(outcome, "error")
        msg = (f"live pair {outcome.replace('_', ' ')} on {item['strike_ticker']}: cost ${cost}, fees ${fees}, "
               f"{latency_ms:.0f} ms from detection to both acks")
        self._event(level, "unwind" if outcome.startswith("one_leg") else "fill", msg,
                    {"opportunity_id": opp_id, "outcome": outcome, "records": records, "latency_ms": latency_ms})
        if level == "error":
            self._set_error(msg)
        if self._check_halted():
            return True
        self._check_loss_cap()
        return True

    def _mark_sent(self, coids: list[str], ts: float) -> bool:
        """Stamp the intents' send time in the ledger. False if that failed: orders the ledger cannot date are not sent."""
        fn = getattr(self.ledger, "mark_sent", None)
        if fn is None:
            return True
        try:
            fn(coids, ts)
            return True
        except Exception:
            log.exception("pair bot: ledger.mark_sent failed")
            return False

    def _unsafe_to_send(self, books: Mapping, marked: bool) -> tuple[bool, str | None]:
        """The cheap checks that must still hold at the moment of the send: ``(stop, reason)`` for the first that does not,
        ``(False, None)`` when all do. ``stop`` is True for a reason that ends the run (kill file, halted Safety, a ledger
        that cannot date the send), False for a pair that is merely too late (the close is near, a book went stale)."""
        if not marked:
            return True, "the ledger could not stamp the send time (database busy?)"
        if self._stop:
            return True, "the bot is stopping"
        if self._check_kill():
            return True, "kill switch"
        if self._check_halted():
            return True, "safety is halted"
        now = self.clock()
        left = self.hour_ts - now
        if left < self.entry_cutoff_s:
            return False, f"only {left:.0f} s to the close, less than the {self.entry_cutoff_s:.0f} s Safety needs for an entry"
        for role, b in books.items():
            age = book_age(b, now)
            if age > self.max_book_age_s:
                return False, f"the {role} order book is {age:.1f} s old (max {self.max_book_age_s:g} s)"
        return False, None

    def _abort_unsent(self, opp_id: int, item: dict, specs: list[dict], sent: list[tuple[str, dict]], stop: bool,
                      why: str) -> bool:
        """Committed intents that must not go out. Say so everywhere, so the ledger does not keep them as orders that may
        have been placed (they are final and count as no exposure) and the dashboard does not show them as live orders.
        Returns False: nothing was sent."""
        for spec, (coid, body) in zip(specs, sent):
            try:
                self.ledger.mark_presumed_not_placed(coid, f"not sent: {why}")
            except Exception:
                log.exception("pair bot: could not mark %s as not sent", coid)
                self._set_error(f"intent {coid} was committed but not sent ({why}) and could not be marked")
            try:
                self.state.order(client_order_id=coid, ticker=spec["ticker"], side=spec["outcome"], action="buy",
                                 count=spec["count"], limit_price=spec["outcome_price"], tif="ioc", status="not_sent",
                                 opportunity_id=opp_id, dry_run=True, response={"request": body, "not_sent": why})
            except Exception:
                log.exception("pair bot: could not record that %s was not sent", coid)
        item["live"] = False
        try:
            self.state.update_opportunity(opp_id, live=False)
        except Exception:
            log.exception("pair bot: could not clear the live flag of opportunity %s", opp_id)
        note = f"{why} (checked again after the intents were committed)"
        return self._blocked(opp_id, note) if stop else self._gone(opp_id, item, note)

    # -------------------------------------------------------------------------------------------- leg risk
    def _resolve_legs(self, opp_id: int, specs: list[dict], sends: list[dict], markets: Mapping,
                      records: list[dict]) -> str:
        for spec, s in zip(specs, sends):
            if s["fill"]:
                records.append(self._record(spec, s))
        fills = [s["fill"] for s in sends]
        if any(f is None for f in fills):
            self._trip("an order outcome is unknown and the order was not found; nothing is resent")
            return "unknown"
        fa, fb = fills
        if fa == fb:
            return "both_filled" if fa > 0 else "none_filled"
        i_long = 0 if fa > fb else 1
        long_spec, short_spec = specs[i_long], specs[1 - i_long]
        qty = fills[i_long] - fills[1 - i_long]
        got = self._retry_missing(opp_id, short_spec, markets[short_spec["role"]], long_spec["outcome_price"], qty,
                                  records)
        if got is None:
            self._trip("the retry order's outcome is unknown and the order was not found; nothing is resent")
            return "unknown"
        rest = qty - got
        if rest <= 0:
            return "both_filled"
        unwound = self._unwind(opp_id, long_spec, markets[long_spec["role"]], rest, records)
        if unwound is None:
            self._trip("the unwind order's outcome is unknown and the order was not found; nothing is resent")
            return "unknown"
        return "one_leg_unwound" if unwound >= rest else "one_leg_held"

    def _retry_missing(self, opp_id: int, spec: dict, market: Mapping, long_price: Decimal, qty: Decimal,
                       records: list[dict]) -> Decimal | None:
        """One retry for the missing leg at its current ask, only while the pair still has edge >= 0."""
        role, outcome, ticker = spec["role"], spec["outcome"], spec["ticker"]
        book = self._book(ticker)
        why, ask, edge = None, None, None
        try:
            crossed = book is not None and is_crossed(book)
            a = None if crossed or book is None else outcome_ask(book, outcome)
            if crossed:
                why = "the book is crossed or locked"
        except (TypeError, ValueError) as exc:
            a, why = None, f"unreadable book: {exc}"
        if a is None:
            why = why or "no ask"
        else:
            ask, size = a
            edge = qty - (long_price + ask) * qty - self.fee_fn(qty, long_price) - self.fee_fn(qty, ask)
            if edge < 0:
                why = f"pair edge now {edge} < 0"
            elif size < qty:
                why = f"only {size} displayed at {ask}"
            elif book_age(book, self.clock()) > self.max_book_age_s:
                why = "book too old"
            elif self._safety_halted() or self._kill_active():
                why = "safety halted or kill switch"
        data = {"opportunity_id": opp_id, "ticker": ticker, "outcome": outcome, "qty": qty, "ask": ask, "edge": edge}
        if why:
            self._event("warn", "unwind", f"no retry for the missing {role} {outcome.upper()} leg: {why}", data)
            return D0
        rspec = leg_spec(market, outcome, ask, qty, purpose="pair_retry", opportunity_id=opp_id, role=role)
        ok, reason = self._safety_check("check_entry", rspec, book, market)
        if not ok:
            self._event("warn", "cap", f"retry of the {role} leg refused by safety: {reason}", data)
            return D0
        s = self._send_one(rspec, opp_id)
        if s["fill"]:
            records.append(self._record(rspec, s))
        self._event("info", "order", f"retry of the {role} {outcome.upper()} leg filled {s['fill']}", data)
        return s["fill"]

    def _unwind(self, opp_id: int, long_spec: dict, market: Mapping, qty: Decimal,
                records: list[dict]) -> Decimal | None:
        """Sell the unpaired leg: a reduce_only IOC buy of the opposite outcome, at most 2 ticks through the touch."""
        held = long_spec["outcome"]
        other = "no" if held == "yes" else "yes"
        ticker = long_spec["ticker"]
        book = self._book(ticker)
        try:
            crossed = book is not None and is_crossed(book)
            a = None if crossed or book is None else outcome_ask(book, other)
        except (TypeError, ValueError):
            crossed, a = False, None
        if a is None:
            self._event("error", "unwind", f"cannot unwind {held.upper()} on {ticker}: "
                        + ("the book is crossed or locked" if crossed else "no usable bid"), {"qty": qty})
            return D0
        ask = a[0]
        stepped = step_price(other, ask, market.get("price_ranges") or (), UNWIND_TICKS)
        limit = ask if stepped is None else max(ask, min(stepped, PRICE_MAX))
        uspec = leg_spec(market, other, limit, qty, purpose="unwind", opportunity_id=opp_id, role=long_spec["role"],
                         reduce_only=True)
        ok, reason = self._safety_check("check_exit", uspec, book, market)
        data = {"opportunity_id": opp_id, "ticker": ticker, "held": held, "qty": qty, "touch": ask, "limit": limit}
        if not ok:
            self._event("error", "unwind", f"unwind of {held.upper()} on {ticker} refused by safety: {reason}", data)
            return D0
        s = self._send_one(uspec, opp_id)
        if s["fill"]:
            records.append(self._record(uspec, s))
        self._event("warn", "unwind", f"unwind of {held.upper()} on {ticker} filled {s['fill']} of {qty}", data)
        return s["fill"]

    # -------------------------------------------------------------------------------------------- sending
    def _safety_check(self, name: str, spec: dict, book: Mapping | None, market: Mapping, **kw: Any) -> tuple[bool, str]:
        fn = getattr(self.safety, name, None)
        if fn is None:
            return False, f"Safety has no {name}()"
        try:
            return _verdict(fn(spec, book, market, **kw))
        except Exception as exc:      # fail closed
            return False, f"{name} failed: {type(exc).__name__}: {exc}"

    def _pair_loss_ok(self, specs: list[dict]) -> tuple[bool, str]:
        """Combined rules for the whole pair, the bot's own backstop to Safety: realized loss + open worst case + both
        legs' worst case <= the loss cap, and open exposure + both legs' collateral <= the exposure cap."""
        try:
            for s in specs:
                if units.collateral(s["side"], s["yes_price"], s["count"]) > COST_PER_ORDER_CEILING:
                    return False, f"collateral of the {s['role']} leg exceeds the {COST_PER_ORDER_CEILING} per-order ceiling"
            used = self._realized_loss() + _dec_any(self.ledger.open_worst_case())
            collateral = sum((units.collateral(s["side"], s["yes_price"], s["count"]) for s in specs), D0)
            worst = collateral + sum((units.worst_fee(s["count"], s["yes_price"]) for s in specs), D0)
            exposure = _dec_any(self.ledger.open_exposure())
        except Exception as exc:
            return False, f"loss check failed: {type(exc).__name__}: {exc}"
        if used + worst > self.max_loss:
            return False, f"combined loss rule: {used} used + {worst} pair worst case > {self.max_loss} cap"
        if exposure + collateral > self.max_open_exposure:
            return False, (f"combined exposure rule: {exposure} open + {collateral} pair collateral > "
                           f"{self.max_open_exposure} cap")
        return True, ""

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

    def _display(self, spec: Mapping) -> tuple[str, str, Decimal]:
        """(side, action, price) for bot_orders. An unwind shows as a sale of the held outcome."""
        if spec.get("purpose") == "unwind":
            return ("no" if spec["outcome"] == "yes" else "yes"), "sell", D1 - spec["outcome_price"]
        return spec["outcome"], "buy", spec["outcome_price"]

    def _order_row(self, coid: str, spec: dict, opp_id: int, ts_sent: float, body: Mapping) -> None:
        side, action, price = self._display(spec)
        self.state.order(client_order_id=coid, ticker=spec["ticker"], side=side, action=action, count=spec["count"],
                         limit_price=price, tif="ioc", status="sent", opportunity_id=opp_id, ts_sent=ts_sent,
                         dry_run=False, response={"request": body})

    def _send_one(self, spec: dict, opp_id: int) -> dict:
        coid, body = self._intent(spec)
        ts_sent = self.clock()
        self._order_row(coid, spec, opp_id, ts_sent, body)
        self._touched.add(spec["ticker"])
        self._mark_sent([coid], ts_sent)
        try:
            res = self.client.create_order(body)
        except Exception as exc:      # unknown outcome: looked up, never resent
            res = _Outcome("unknown", error=f"{type(exc).__name__}: {exc}")
        return self._finish_send(spec, coid, body, res, self.clock(), ts_sent)

    @staticmethod
    def _match_results(coids: list[str], raw: Any) -> list[Any]:
        raw = list(raw or [])
        by_coid = {}
        for r in raw:
            d = getattr(r, "data", None)
            if isinstance(d, Mapping) and d.get("client_order_id") in coids:
                by_coid[d["client_order_id"]] = r
        out = []
        for i, c in enumerate(coids):
            if c in by_coid:
                out.append(by_coid[c])
                continue
            r = raw[i] if i < len(raw) else None
            d = getattr(r, "data", None) if r is not None else None
            if r is not None and not (isinstance(d, Mapping) and d.get("client_order_id") not in (None, "", c)):
                out.append(r)
            else:
                out.append(_Outcome("unknown", error="no result for this order in the batch response"))
        return out

    def _finish_send(self, spec: dict, coid: str, body: Mapping, res: Any, ts_ack: float, ts_sent: float) -> dict:
        try:
            self.ledger.mark_result(coid, res)
        except Exception as exc:
            self._set_error(f"ledger.mark_result failed for {coid}: {exc}")
            self._event_safe("error", "error", f"ledger.mark_result failed for {coid}: {type(exc).__name__}: {exc}")
        fill, order_id = self._resolve_fill(spec, coid, res, ts_sent)
        fee = self._fee_from(res, fill, spec["outcome_price"]) if fill else None
        side, action, price = self._display(spec)
        self.state.update_order(coid, ts_ack=ts_ack, status=_order_status(getattr(res, "outcome", None), fill,
                                                                           spec["count"]),
                                order_id=order_id, filled=fill, avg_fill_price=price if fill else None, fees=fee,
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
        """One filled order in outcome terms (every order is a buy of an outcome). price is the limit until refined."""
        return {"coid": s["coid"], "order_id": s["order_id"], "ticker": spec["ticker"], "role": spec["role"],
                "outcome": spec["outcome"], "count": s["fill"], "price": spec["outcome_price"],
                "limit": spec["outcome_price"], "fee": s["fee"] if s["fee"] is not None else D0,
                "purpose": spec["purpose"], "exact": False}

    def _refine(self, r: dict) -> None:
        """Replace the limit-price estimate with the exchange's fills when their counts add up. A fill on the wrong
        outcome, or above its limit, is an anomaly that stops the bot."""
        fills_fn = getattr(self.client, "fills", None)
        if fills_fn is None or not r.get("order_id") or r.get("exact"):
            return
        try:
            resp = fills_fn(order_id=r["order_id"])
        except AuthError as exc:
            self._auth_stop(exc, "the fills read")
            return
        except Exception as exc:
            self._warn(f"fills:{r['order_id']}", f"fills read failed for {r['order_id']}: {type(exc).__name__}: {exc}")
            return
        items = [f for f in _items(resp, "fills") if isinstance(f, Mapping) and f.get("order_id") in (None, r["order_id"])]
        if not items:
            return
        total = value = fee = D0
        fee_known, ok = True, True
        for f in items:
            c = _num(f.get("count_fp", f.get("count")))
            if c is None:
                ok = False
                break
            if f.get("outcome_side") != r["outcome"]:
                self._trip(f"order {r['coid']} filled on {f.get('outcome_side')!r}, not the {r['outcome']!r} it bought")
                return
            p = _num(f.get("yes_price_dollars")) if r["outcome"] == "yes" else _num(f.get("no_price_dollars"))
            if p is None and r["outcome"] == "no":
                y = _num(f.get("yes_price_dollars"))
                p = None if y is None else D1 - y
            if p is None:
                ok = False
                break
            if p > r["limit"] + Decimal("0.0001"):
                self._trip(f"order {r['coid']} filled at {p}, above its {r['limit']} limit")
                return
            fc = _num(f.get("fee_cost"))
            fee_known = fee_known and fc is not None
            total, value, fee = total + c, value + c * p, fee + (fc or D0)
        if not ok or total != r["count"]:
            return
        r["price"], r["exact"] = value / total, True
        if fee_known:
            r["fee"] = fee
        price = D1 - r["price"] if r["purpose"] == "unwind" else r["price"]
        self.state.update_order(r["coid"], avg_fill_price=price, fees=r["fee"])
        upsert = getattr(self.ledger, "upsert_fills", None)
        if upsert is not None:
            try:
                upsert(items)
            except Exception as exc:
                self._warn(f"upsert:{r['order_id']}", f"ledger could not record the fills: {exc}")

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
                log.exception("pair bot: Safety.trip failed")
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

    def _check_loss_cap(self) -> None:
        """Realized loss at the cap: trip and stop. (Room for the next pair is checked before each pair.)"""
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
                ei = (self.client.series(SERIES_15M) or {}).get("exchange_index")
                if isinstance(ei, int) and not isinstance(ei, bool):
                    self._exchange_index = ei
            if self._exchange_index is None:
                return
            b = self.client.balance(self._exchange_index) or {}
            usd = _num(b.get("balance_dollars"))
            if usd is None and isinstance(b.get("balance"), int):
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

    # -------------------------------------------------------------------------------------------- settlement
    def _market_result(self, ticker: str, cache: dict) -> str | None:
        if ticker in cache:
            return cache[ticker]
        try:
            m = self.client.market(ticker)
        except Exception as exc:
            self._warn(f"result:{ticker}", f"market read failed for {ticker}: {type(exc).__name__}: {exc}")
            m = None
        if isinstance(m, Mapping) and isinstance(m.get("market"), Mapping):
            m = m["market"]
        r = str(m.get("result") or "").lower() if isinstance(m, Mapping) else ""
        cache[ticker] = r if r in ("yes", "no") else None
        return cache[ticker]

    def _settle_pending(self) -> None:
        if not self._pending:
            return
        now = self.clock()
        cache: dict[str, str | None] = {}
        for item in list(self._pending):
            if now < item["hour_ts"] + SETTLE_DELAY_S or now < item["next_check"]:
                continue
            item["next_check"] = now + self.settle_poll_s
            r15 = self._market_result(item["hour_ticker"], cache)
            rh = self._market_result(item["strike_ticker"], cache)
            if r15 is None or rh is None:
                if now > item["hour_ts"] + SETTLE_GIVE_UP_S:
                    self._pending.remove(item)
                    self._event("warn", "settle", f"no result for {item['strike_ticker']} / {item['hour_ticker']} "
                                "after 6 h; P&L stays empty", {"opportunity_id": item["opp_id"]})
                continue
            self._settle_one(item, {item["hour_ticker"]: r15, item["strike_ticker"]: rh})
            self._pending.remove(item)

    def _settle_one(self, item: dict, results: Mapping[str, str]) -> None:
        """Book the paper twin's and (live) the real P&L of one finished pair. ``item["run_id"]`` is set for a row an
        earlier run left pending: it is settled on its own row and kept out of this run's counts."""
        recovered = item.get("run_id") is not None
        up_won = results[item["hour_ticker"]] == "yes"
        above_won = results[item["strike_ticker"]] == "yes"
        per_pair = pair_payout(item["kind"], up_won, above_won)
        paper_pnl = per_pair * item["contracts"] - item["paper_cost"] - item["paper_fees"]
        fields: dict[str, Any] = {"paper_realized_pnl": paper_pnl}
        data: dict[str, Any] = {"opportunity_id": item["opp_id"], "results": dict(results), "pair_payout": per_pair,
                                "paper_realized_pnl": paper_pnl}
        if item["live"]:
            for r in item["records"]:
                self._refine(r)                   # the fills are surely visible by now: exact prices and fees
            if item["resolved"]:
                records = item["records"]
                payout = sum((r["count"] for r in records if results.get(r["ticker"]) == r["outcome"]), D0)
                cost = sum((r["count"] * r["price"] for r in records), D0)
                fees = sum((r["fee"] for r in records), D0)
                fields.update(payout=payout, realized_pnl=payout - cost - fees, live_cost=cost, live_fees=fees)
                data.update(payout=payout, realized_pnl=payout - cost - fees, live_cost=cost, live_fees=fees)
                self._live_pnl[item["opp_id"]] = (payout - cost - fees, sorted({r["ticker"] for r in records}),
                                                  {r["order_id"] for r in records if r.get("order_id")})
            queued = [t for t in (item["hour_ticker"], item["strike_ticker"]) if self._queue_ledger(t)]
            if queued:
                self._settle_ledger_pending(force=True)
                data["ledger_recorded"] = not any(t in self._ledger_todo for t in queued)
        self.state.update_opportunity(item["opp_id"], run_id=item.get("run_id"), **fields)
        self.summary["recovered" if recovered else "settled"] += 1
        if per_pair < 1:
            msg = f"pair on {item['strike_ticker']} paid ${per_pair} (< $1): the pair assumption failed"
            self._set_error(msg)
            self._event("error", "settle", msg, data)
        else:
            self._event("info", "settle", f"settled {item['strike_ticker']}: pair paid ${per_pair}, paper P&L "
                        f"${paper_pnl}", data)
        self._check_loss_cap()

    # ---- the ledger's own settlement record (the exchange's revenue); it can lag the public market result
    def _ledger_expects_settlement(self, ticker: str) -> bool:
        """True when the ledger holds a fill (or an order that reported one) on the ticker, so the exchange will publish
        a settlement for it. An order that never filled has none: waiting for one would only stall the shutdown."""
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
        until the ledger records it the pair counts as OPEN worst case, not as realized."""
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
            elif now - self._ledger_todo[t] > SETTLE_GIVE_UP_S:
                del self._ledger_todo[t]
                self._event("warn", "settle", f"the ledger has no settlement record for {t} after 6 h; "
                            "run kpm exec reconcile", {"ticker": t})
        for opp_id, (pnl, tickers_, order_ids) in list(self._live_pnl.items()):
            if all(t not in self._ledger_todo for t in tickers_):
                del self._live_pnl[opp_id]
                self._cross_check(opp_id, pnl, tickers_, order_ids)

    def _cross_check(self, opp_id: int, pnl: Decimal, tickers: list[str], order_ids: set[str] | None = None) -> None:
        """Compare our P&L of one pair with the ledger's settlement records of its tickers (the exchange's revenue).

        The ledger's figure covers every fill on a ticker, so it is only comparable when all of them are ours."""
        conn, env = getattr(self.ledger, "conn", None), getattr(self.ledger, "env", None)
        if conn is None or env is None or not tickers:
            return
        try:
            if order_ids is not None and any({f["order_id"] for f in self.ledger.fills_for(t)} - order_ids
                                             for t in tickers):
                return                       # another order (another strategy) also traded one of these markets
            rows = conn.execute("SELECT ticker, pnl, check_status FROM exec_settlements WHERE env = ? AND ticker IN (%s)"
                                % ", ".join("?" * len(tickers)), (env, *tickers)).fetchall()
        except Exception:
            return
        if len(rows) != len(tickers):
            return                                   # not all recorded (a leg that never filled has no settlement)
        lp = sum((_num(r[1]) or D0 for r in rows), D0)
        bad = [r[2] for r in rows if r[2] not in (None, "ok")]
        if abs(lp - pnl) > Decimal("0.0001") or bad:
            msg = f"P&L of opportunity {opp_id}: bot {pnl} vs ledger settlements {lp} ({', '.join(map(str, bad)) or 'ok'})"
            self._set_error(msg)
            self._event("error", "settle", msg, {"opportunity_id": opp_id, "bot_pnl": pnl, "ledger_pnl": lp,
                                                 "tickers": tickers})

    def _kill_requested(self) -> bool:
        """A kill file this run did not write itself (a Safety trip writes one)."""
        return self._kill_active() and not self._tripped

    def _recover_unsettled(self) -> None:
        """Opportunities an earlier run left pending (it was stopped before their hour settled) are settled now, on their
        own rows, so the paper twin and live P&L columns are never left empty for good."""
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
        if r["kind"] not in KINDS or None in (r["paper_cost"], r["paper_fees"], r["contracts"]):
            return None
        live = bool(r["live"])
        records: list[dict] = []
        if live:
            for o in self.state.live_orders_of(r["run_id"], r["id"]):
                if not (o["filled"] and o["filled"] > 0 and o["avg_fill_price"] is not None):
                    continue
                price = Decimal(repr(o["avg_fill_price"]))
                outcome = o["side"]
                if o["action"] == "sell":            # an unwind: shown as a sale of the held outcome, bought the other
                    outcome, price = ("no" if outcome == "yes" else "yes"), D1 - price
                records.append({"coid": o["client_order_id"], "order_id": o["order_id"], "ticker": o["ticker"],
                                "role": None, "outcome": outcome, "count": Decimal(repr(o["filled"])), "price": price,
                                "limit": price, "fee": Decimal(repr(o["fees"] or 0.0)),
                                "purpose": "unwind" if o["action"] == "sell" else "pair_entry", "exact": True})
        return {"opp_id": r["id"], "run_id": r["run_id"], "hour_ts": float(r["detected_ts"]),
                "hour_ticker": r["hour_ticker"], "strike_ticker": r["strike_ticker"], "kind": r["kind"],
                "contracts": Decimal(repr(r["contracts"])), "paper_cost": Decimal(repr(r["paper_cost"])),
                "paper_fees": Decimal(repr(r["paper_fees"])), "live": live, "resolved": r["outcome"] != "unknown",
                "records": records, "next_check": 0.0}

    def _drain_settlements(self) -> None:
        """Wait (bounded) for the last pairs to settle so their P&L is booked. Not after a kill: the user asked for a
        stop. A safety trip still drains: it only reads."""
        if not (self._pending or self._ledger_todo):
            return
        now = self.clock()
        if self._pending and not self._ledger_todo and \
                min(i["hour_ts"] + SETTLE_DELAY_S for i in self._pending) - now > self.settle_wait_s:
            self._event("info", "settle", f"{len(self._pending)} opportunity(ies) settle more than "
                        f"{self.settle_wait_s:.0f} s from now; the next run settles them")
            return
        deadline = now + self.settle_wait_s
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
            self._event("warn", "settle", f"{len(self._pending)} opportunities not settled and {len(self._ledger_todo)} "
                        f"ticker(s) not recorded by the ledger within {self.settle_wait_s:.0f} s; "
                        "their P&L stays empty")

    # -------------------------------------------------------------------------------------------- smoke
    def _smoke(self) -> dict:
        """One discovery and one pricing pass for the hour closing next, from public data only. Never trades."""
        now = self.clock()
        hour = int(math.floor(now / 3600.0)) * 3600 + 3600
        self.hour_ts = hour
        self.window_start_ts, self.window_end_ts = hour - self.window_s, hour - self.stop_before_close_s
        out: dict[str, Any] = {"hour_close_ts": hour, "hour_close": _iso(hour)}
        sel, why = self._discover(hour)
        if sel:
            self._enter_hour(sel)
            found = self._poll(sel, record=False)
            out.update(found=True, hour_ticker=self.hour_ticker, target=self.target,
                       strikes=[{"ticker": m["ticker"], "strike": k} for k, m in sel["strikes"]],
                       quotes_written=self.summary["quotes"], would_be_opportunities=found)
            msg = (f"smoke pass: priced {self.summary['quotes']} strikes against {self.hour_ticker} "
                   f"(target {self.target}); {len(found)} would-be opportunities")
        else:
            out.update(found=False, reason=why, proxy=self._smoke_proxy(hour, now))
            msg = f"smoke pass: no pair to price for the hour closing {_iso(hour)}: {why}"
        self._event("info", "smoke", msg, out)
        return out

    def _smoke_proxy(self, hour: int, now: float) -> dict:
        """Outside the window: show the nearest hourly strikes around the open 15m market's target. No pair is priced."""
        try:
            m15s = self.client.open_markets(SERIES_15M)
            hourly = self.client.open_markets(SERIES_HOURLY)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        current = sorted((m for m in m15s or () if isinstance(m, Mapping) and _active(m)
                          and (close_ts(m) or 0) > now and strike_value(m) is not None), key=lambda m: close_ts(m) or 0)
        out: dict[str, Any] = {"note": "reference only: a pair needs the KXBTC15M market that closes on the hour",
                               "reference_15m": current[0]["ticker"] if current else None,
                               "reference_target": strike_value(current[0]) if current else None,
                               "hourly_strikes": []}
        if not current:
            return out
        for k, m in hourly_strikes(hourly, hour, strike_value(current[0]), self.strikes_each_side):
            book = self._book(m["ticker"])
            row: dict[str, Any] = {"ticker": m["ticker"], "strike": k}
            for outcome in ("yes", "no"):
                try:
                    a = outcome_ask(book, outcome) if book is not None else None
                except (TypeError, ValueError):
                    a = None
                row[f"{outcome}_ask"], row[f"{outcome}_size"] = (a if a else (None, None))
            out["hourly_strikes"].append(row)
        return out

    # -------------------------------------------------------------------------------------------- state helpers
    def _run_config(self, smoke: bool) -> dict:
        t = self.trading_cfg
        return {"strategy": STRATEGY, "mode": "live" if self.live else "watch", "smoke": bool(smoke),
                "env": getattr(self.ctx, "env_name", None), "key_fp": getattr(self.ctx, "key_fp", None),
                "series": [SERIES_15M, SERIES_HOURLY], "contracts": self.contracts, "min_edge_usd": self.min_edge,
                "window_min": self.window_s / 60.0, "strikes_each_side": self.strikes_each_side,
                "poll_interval_s": self.poll_s, "max_book_age_s": self.max_book_age_s,
                "stop_before_close_s": self.stop_before_close_s, "max_live_pairs_per_hour": self.max_live_pairs_per_hour,
                "max_opportunities_per_strike_hour": self.max_opps_per_strike_hour, "max_loss_usd": self.max_loss,
                "max_cost_per_order_usd": t.get("max_cost_per_order_usd"),
                "max_contracts_per_order": t.get("max_contracts_per_order"),
                "max_orders_per_minute": t.get("max_orders_per_minute"), "kill_switch_file": str(self.kill_path)}

    def _heartbeat(self, status: str, force: bool = False) -> None:
        now = self.clock()
        if not force and status == self._hb_status and 0 <= now - self._hb_ts < HEARTBEAT_S:
            return                       # (a clock that stepped backwards must not silence the heartbeat: 0 <= ...)
        detail: dict[str, Any] = {
            "mode": "live" if self.live else "watch", "status": status, "hour_ticker": self.hour_ticker,
            "target": self.target, "hour_close_ts": self.hour_ts, "window_start_ts": self.window_start_ts,
            "window_end_ts": self.window_end_ts, "loss_used_usd": self._loss_used(), "max_loss_usd": self.max_loss,
            "balance_usd": self.balance_usd, "kill_switch": self._kill_active(),
            "live_pairs_this_hour": self.live_pairs_hour, "opportunities": self.summary["opportunities"],
            "polls": self.summary["polls"], "clock_skew_s": self._clock_skew()}
        if self.last_error and (status in ("stopped", "error") or now - self._last_error_ts < ERROR_SHOW_S):
            detail["last_error"] = self.last_error           # a stopped run keeps showing why it stopped
        self.state.heartbeat(status, detail, ts=now)
        self._hb_status, self._hb_ts = status, now

    def _heartbeat_safe(self, status: str) -> None:
        try:
            self._heartbeat(status, force=True)
        except Exception:
            log.exception("pair bot: heartbeat failed")

    def _event(self, level: str, kind: str, message: str, data: Any = None) -> None:
        getattr(log, {"warn": "warning"}.get(level, level))("pair bot %s: %s", kind, message)
        self.state.event(level, kind, message, data, ts=self.clock())

    def _event_safe(self, level: str, kind: str, message: str, data: Any = None) -> None:
        try:
            self._event(level, kind, message, data)
        except Exception:
            log.exception("pair bot: could not record event %r", message)

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
