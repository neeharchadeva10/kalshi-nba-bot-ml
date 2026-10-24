"""Exact unit conversions for the Kalshi Trade API v2 (docs/execution_design.md, section 1).

The traps this module exists to stop (each one is a 100x or wrong-side order):

* Prices are YES-price DOLLAR strings with 4 dp: ``"0.0100"`` is 1 cent. Never integer cents.
* ``side`` is the YES book side. ``"bid"`` buys YES; ``"ask"`` sells YES, which is buying NO at
  1 - price. :func:`to_book` is the ONLY conversion from an outcome price to ``(side, yes_price)``:
  buying NO at 0.01 is ``("ask", 0.99)``; ``"ask"`` at 0.01 would buy NO at 0.99 (99x the collateral).
* Collateral depends on the side: a bid locks price x count, an ask locks (1 - price) x count.
* ``count`` is in contracts with 0.01 steps: ``"25"`` and ``"0.25"`` differ by 100x.
* Money fields mix units: integer cents (balance, settlement revenue), centicents (transfers, LIP).
* Request times are Unix SECONDS; the signature timestamp is milliseconds.
* REST orderbook arrays are ascending, so the best bid is the LAST element.
  Best YES ask = 1 - best NO bid; best NO ask = 1 - best YES bid.

Floats are refused everywhere: every function takes ``Decimal`` (or the API's decimal strings).
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

D0 = Decimal("0")
D1 = Decimal("1")
CENT = Decimal("0.01")
CENTICENT = Decimal("0.0001")
MICRO = Decimal("0.000001")
TAKER_RATE = Decimal("0.07")          # quadratic taker fee: 0.07 x C x P x (1 - P), confirmed by a 2022 filing

OUTCOMES = ("yes", "no")
BOOK_SIDES = ("bid", "ask")

_DECIMAL_STR = re.compile(r"-?[0-9]+(\.[0-9]+)?")             # ASCII digits only: Decimal() also accepts e.g. Arabic-Indic ones
_RFC3339 = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.(?P<frac>[0-9]{1,9}))?"
    r"(?P<tz>[Zz]|[+-][0-9]{2}:[0-9]{2})")

Level = tuple[Decimal, Decimal]


# --------------------------------------------------------------------------------------------- parsing
def dec(s: Any) -> Decimal:
    """Decimal from an API decimal STRING (``*_dollars``, ``*_fp``, ``fee_cost``, price, count).

    Floats, ints and bools are refused (``TypeError``): a float has already lost exactness and an
    int is usually a cents field passed where dollars were expected. An existing finite ``Decimal``
    is returned unchanged. Malformed strings (``"NaN"``, ``"1e3"``, ``""``) raise ``ValueError``.
    """
    if isinstance(s, Decimal):
        if not s.is_finite():
            raise ValueError(f"non-finite decimal {s!r}")
        return s
    if not isinstance(s, str):
        raise TypeError(f"expected a decimal string, got {type(s).__name__}")
    if not _DECIMAL_STR.fullmatch(s):
        raise ValueError(f"not a plain decimal string: {s[:40]!r}")
    return Decimal(s)


def _require_decimal(x: Any, what: str) -> Decimal:
    if isinstance(x, bool) or not isinstance(x, Decimal):
        raise TypeError(f"{what} must be a Decimal, got {type(x).__name__}")
    if not x.is_finite():
        raise ValueError(f"{what} is not finite: {x!r}")
    return x


def _on_step(x: Decimal, step: Decimal) -> bool:
    try:
        return x == x.quantize(step)
    except InvalidOperation:                     # too many digits for the context: certainly not a valid value
        return False


# --------------------------------------------------------------------------------------------- request formatting
def fmt_price(p: Decimal) -> str:
    """Request ``price``: the YES price in dollars with exactly 4 dp, strictly between 0 and 1.

    ``fmt_price(Decimal("0.01")) == "0.0100"``. Rejects 0, 1, 50 (cents!), more than 4 dp, and floats.
    """
    _require_decimal(p, "price")
    if not (D0 < p < D1) or not _on_step(p, CENTICENT):
        raise ValueError(f"bad price {p}: must be a YES price in dollars, 0 < p < 1, at most 4 dp")
    return f"{p:.4f}"


def fmt_count(c: Decimal) -> str:
    """Request ``count``: contracts with exactly 2 dp, > 0, in 0.01 steps (``"25.00"`` != ``"0.25"``)."""
    _require_decimal(c, "count")
    if c <= 0 or not _on_step(c, CENT):
        raise ValueError(f"bad count {c}: contracts must be > 0 in steps of 0.01")
    return f"{c:.2f}"


# --------------------------------------------------------------------------------------------- direction
def to_book(outcome: str, outcome_price: Decimal) -> tuple[str, Decimal]:
    """The ONLY place an outcome price becomes ``(side, YES price)``.

    Buying YES at p is ``("bid", p)``. Buying NO at p is ``("ask", 1 - p)``: buy NO @ 0.01 -> ``("ask", 0.99)``.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be 'yes' or 'no', got {outcome!r}")
    _require_decimal(outcome_price, "outcome price")
    if not (D0 < outcome_price < D1):
        raise ValueError(f"outcome price {outcome_price} must be strictly between 0 and 1")
    if outcome == "yes":
        return "bid", outcome_price
    return "ask", D1 - outcome_price


def from_book(side: str, yes_price: Decimal) -> tuple[str, Decimal]:
    """Inverse of :func:`to_book` for display and ledgers: ``("ask", 0.99) -> ("no", 0.01)``."""
    if side not in BOOK_SIDES:
        raise ValueError(f"side must be 'bid' or 'ask', got {side!r}")
    _require_decimal(yes_price, "YES price")
    if not (D0 < yes_price < D1):
        raise ValueError(f"YES price {yes_price} must be strictly between 0 and 1")
    return ("yes", yes_price) if side == "bid" else ("no", D1 - yes_price)


def collateral(side: str, yes_price: Decimal, count: Decimal) -> Decimal:
    """Max loss of a fill from flat, before fees: a bid locks p x count, an ask (= NO buy) (1 - p) x count."""
    if side not in BOOK_SIDES:
        raise ValueError(f"side must be 'bid' or 'ask', got {side!r}")
    _require_decimal(yes_price, "YES price")
    _require_decimal(count, "count")
    if not (D0 < yes_price < D1):
        raise ValueError(f"YES price {yes_price} must be strictly between 0 and 1")
    if count < 0:
        raise ValueError(f"count {count} must be >= 0")
    return (yes_price if side == "bid" else D1 - yes_price) * count


# --------------------------------------------------------------------------------------------- fees
def _fee_raw(count: Decimal, price: Decimal, multiplier: Decimal) -> Decimal:
    _require_decimal(count, "count")
    _require_decimal(price, "price")
    _require_decimal(multiplier, "fee multiplier")
    if count <= 0:
        raise ValueError(f"count {count} must be > 0")
    if not (D0 < price < D1):
        raise ValueError(f"price {price} must be strictly between 0 and 1")
    if multiplier < 0:
        raise ValueError("fee multiplier must be >= 0")
    return TAKER_RATE * multiplier * count * price * (D1 - price)


def taker_fee(count: Decimal, outcome_price: Decimal, multiplier: Decimal = D1) -> Decimal:
    """Exact Kalshi trade fee for a taker fill: 0.07 x mult x C x P x (1 - P), rounded UP to $0.000001.

    P x (1 - P) is symmetric, so the outcome price and the YES price give the same fee. This is the
    'trade fee'; a direct member may also pay a rounding fee below $0.0001 (see :func:`worst_fee`).
    """
    return _fee_raw(count, outcome_price, multiplier).quantize(MICRO, rounding=ROUND_CEILING)


def worst_fee(count: Decimal, yes_price: Decimal, multiplier: Decimal = D1, grid: Decimal = CENTICENT) -> Decimal:
    """Upper bound of the net fee for budgeting: the taker fee rounded up to the balance grid, plus one step.

    The step covers the rounding fee that puts the balance back on the grid ($0.0001 for direct
    members; pass ``grid=CENT`` for a $0.01 grid).
    """
    _require_decimal(grid, "grid")
    if grid <= 0:
        raise ValueError("grid must be > 0")
    return _fee_raw(count, yes_price, multiplier).quantize(grid, rounding=ROUND_CEILING) + grid


# --------------------------------------------------------------------------------------------- price grid
def on_grid(price: Decimal, price_ranges: Iterable[Mapping[str, Any]]) -> bool:
    """Exact membership of the market's ``price_ranges`` tick grid ([{start, end, step}] dollar strings).

    Never snaps. $0 and $1 are invalid prices and return False. Bounds such as 0.01 <= p <= 0.99 are
    the safety layer's job: 0.0050 is on a tapered_deci_cent grid.
    """
    _require_decimal(price, "price")
    if not (D0 < price < D1):
        return False
    for band in price_ranges or ():
        start, end, step = dec(band["start"]), dec(band["end"]), dec(band["step"])
        if step <= 0:
            continue
        if start <= price <= end and (price - start) % step == 0:
            return True
    return False


# --------------------------------------------------------------------------------------------- orderbook
def parse_levels(levels: Iterable[Sequence[Any]] | None) -> list[Level]:
    """REST ``[[price_dollars, count_fp], ...]`` (or already-parsed Decimal pairs) -> ``[(price, size), ...]``.

    Validates what the rest of the module relies on: prices strictly inside (0, 1), sizes >= 0, and
    prices strictly ASCENDING (the live REST order; the API reference's 'best to worst' is wrong).
    Zero-size levels are dropped. Raises ``ValueError`` on anything else.
    """
    out: list[Level] = []
    prev: Decimal | None = None
    for lvl in levels or ():
        if not isinstance(lvl, (list, tuple)) or len(lvl) != 2:
            raise ValueError(f"orderbook level must be [price, size], got {lvl!r}")
        price, size = dec(lvl[0]), dec(lvl[1])
        if not (D0 < price < D1):
            raise ValueError(f"orderbook price {price} outside (0, 1)")
        if size < 0:
            raise ValueError(f"negative orderbook size {size}")
        if prev is not None and price <= prev:
            raise ValueError("orderbook levels are not strictly ascending; refusing to guess the best level")
        prev = price
        if size > 0:
            out.append((price, size))
    return out


def best_level(levels: Iterable[Sequence[Any]] | None) -> Level | None:
    """Best (highest) bid level ``(price, size)``: the LAST element of an ascending REST array. None if empty."""
    parsed = parse_levels(levels)
    return parsed[-1] if parsed else None


def best_bid(levels: Iterable[Sequence[Any]] | None) -> Decimal | None:
    """Best bid price: the LAST element of the ascending REST array (``[0]`` is the worst level shown)."""
    lvl = best_level(levels)
    return None if lvl is None else lvl[0]


def best_yes_ask(no_levels: Iterable[Sequence[Any]] | None) -> Decimal | None:
    """Best YES ask = 1 - best NO bid. None when the NO side is empty (never a tradable price)."""
    b = best_bid(no_levels)
    return None if b is None else D1 - b


def best_no_ask(yes_levels: Iterable[Sequence[Any]] | None) -> Decimal | None:
    """Best NO ask = 1 - best YES bid. None when the YES side is empty."""
    b = best_bid(yes_levels)
    return None if b is None else D1 - b


def outcome_ask(book: Mapping[str, Any], outcome: str) -> Level | None:
    """``(ask price, displayed size)`` to BUY ``outcome`` from a parsed book ``{"yes": [...], "no": [...]}``.

    Buying YES lifts the best NO bid (ask = 1 - NO bid); buying NO lifts the best YES bid (ask = 1 - YES bid).
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be 'yes' or 'no', got {outcome!r}")
    lvl = best_level(book.get("no" if outcome == "yes" else "yes"))
    return None if lvl is None else (D1 - lvl[0], lvl[1])


def outcome_bid(book: Mapping[str, Any], outcome: str) -> Level | None:
    """``(bid price, displayed size)`` to SELL ``outcome``: the best bid on that outcome's own side."""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be 'yes' or 'no', got {outcome!r}")
    return best_level(book.get(outcome))


# --------------------------------------------------------------------------------------------- money and time units
def from_cents(v: int) -> Decimal:
    """Integer CENTS (GetBalance ``balance``/``portfolio_value``, Settlement ``revenue``/``value``) -> dollars."""
    if isinstance(v, bool) or not isinstance(v, int):
        raise TypeError(f"expected integer cents, got {type(v).__name__}")
    return Decimal(v) * CENT


def from_centicents(v: int) -> Decimal:
    """Integer CENTICENTS (transfer ``amount``, LIP ``period_reward``) -> dollars: 200000 -> 20."""
    if isinstance(v, bool) or not isinstance(v, int):
        raise TypeError(f"expected integer centicents, got {type(v).__name__}")
    return Decimal(v) * CENTICENT


def to_centicents(usd: Decimal) -> int:
    """Dollars -> integer centicents ($1 = 10000). Not used by the order path; refuses sub-$0.0001 amounts."""
    _require_decimal(usd, "amount")
    n = usd / CENTICENT
    if n != n.to_integral_value():
        raise ValueError(f"{usd} is not a multiple of $0.0001")
    return int(n)


def unix_seconds(ts: int) -> int:
    """Guard for ``expiration_time`` / ``min_ts`` / ``max_ts``: Unix SECONDS (a millisecond value is refused)."""
    if isinstance(ts, bool) or not isinstance(ts, int):
        raise TypeError(f"expected integer Unix seconds, got {type(ts).__name__}")
    if not (1_000_000_000 <= ts < 10_000_000_000):
        raise ValueError(f"{ts} is not Unix seconds")
    return ts


def parse_rfc3339(s: str) -> float:
    """RFC3339 timestamp with 0-9 fractional digits and a ``Z``/offset suffix -> Unix seconds (float).

    A timestamp without a zone is refused: ``datetime.timestamp()`` would silently use local time.
    """
    if not isinstance(s, str):
        raise TypeError(f"expected an RFC3339 string, got {type(s).__name__}")
    m = _RFC3339.fullmatch(s.strip())
    if not m:
        raise ValueError(f"not an RFC3339 timestamp with a zone: {s[:40]!r}")
    text = s.strip()
    frac = m.group("frac")
    if frac and len(frac) > 6:                              # datetime keeps microseconds only
        text = text.replace("." + frac, "." + frac[:6], 1)
    if m.group("tz") in ("Z", "z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).timestamp()
