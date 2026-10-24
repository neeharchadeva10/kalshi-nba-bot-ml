"""Price and probability conversions."""
from __future__ import annotations

import math


def parse_dollars(v) -> float | None:
    """Kalshi fixed-point dollar strings ('0.4200') -> float. None/'' -> None."""
    if v is None or v == "":
        return None
    return float(v)


def clean_quote(bid: float | None, ask: float | None) -> tuple[float | None, float | None]:
    """Treat an empty book side as missing: bid <= 0 or ask >= 1 means no quote."""
    b = bid if bid is not None and 0.0 < bid < 1.0 else None
    a = ask if ask is not None and 0.0 < ask < 1.0 else None
    if b is not None and a is not None and b > a:
        return None, None
    return b, a


def mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def american_to_prob(odds: float | None) -> float | None:
    """Implied probability (with vig) from American odds."""
    if odds is None or (isinstance(odds, float) and math.isnan(odds)) or odds == 0:
        return None
    if odds < 0:
        return -odds / (-odds + 100.0)
    return 100.0 / (odds + 100.0)


def devig_two_way(p_a: float | None, p_b: float | None) -> tuple[float | None, float | None]:
    """Proportional de-vig of a two-outcome market."""
    if p_a is None or p_b is None:
        return None, None
    s = p_a + p_b
    if s <= 0:
        return None, None
    return p_a / s, p_b / s


def complement(p: float | None) -> float | None:
    return None if p is None else 1.0 - p


def no_ask_from_yes_bid(yes_bid: float | None) -> float | None:
    """Buying NO at its ask is equivalent to selling YES at the YES bid."""
    return None if yes_bid is None else 1.0 - yes_bid


def no_bid_from_yes_ask(yes_ask: float | None) -> float | None:
    return None if yes_ask is None else 1.0 - yes_ask
