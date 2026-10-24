import numpy as np
import pytest

from kpm.analysis.reverse import arb_edge, kalshi_fair, poly_edge
from kpm.collectors.polymarket_us_history import side_quotes
from kpm.core.fees import FeeModel

poly_fee = FeeModel(taker_multiplier=0.0695, maker_fraction=0.0, rounding="cent_half_even")
kalshi_fee = FeeModel()


def test_polymarket_us_fee_banker_rounding():
    # 0.0695 * 100 * 0.25 = 1.7375 -> 1.74 (matches the published "$1.74 per 100 contracts at $0.50")
    assert poly_fee.order_fee(0.50, 100) == pytest.approx(1.74)
    # 0.0695 * 1 * 0.5 * 0.5 = 0.017375 -> 0.02
    assert poly_fee.order_fee(0.50, 1) == pytest.approx(0.02)
    # exact half cent rounds to even: 0.0695 * 10 * 0.9 * 0.1 = 0.06255 -> 0.06 (not a half); craft a half:
    half = FeeModel(taker_multiplier=1.0, rounding="cent_half_even")
    assert half.order_fee(0.5, 0.1) == pytest.approx(0.02)   # 0.025 -> 0.02 (round half to even)
    assert half.order_fee(0.5, 0.14) == pytest.approx(0.04)  # 0.035 -> 0.04
    vec = poly_fee.per_contract_array(np.array([0.5, 0.2]), 100)
    assert vec[0] == pytest.approx(0.0174) and vec[1] == pytest.approx(poly_fee.per_contract(0.2, 100))


def test_side_quotes_from_display_prices():
    q = side_quotes(0.523, 0.489)  # longPrice ~ long ask; shortPrice ~ 1 - long bid
    assert q["long"] == pytest.approx((0.523, 0.511))   # (ask, bid)
    assert q["short"] == pytest.approx((0.489, 0.477))
    assert side_quotes(None, 0.4) == {"long": (None, None), "short": (None, None)}


def test_kalshi_fair_uses_both_books():
    assert kalshi_fair(0.60, 0.42)[()] == pytest.approx(0.59)          # (0.60 + 0.58) / 2
    assert kalshi_fair(np.nan, 0.42)[()] == pytest.approx(0.58)
    assert kalshi_fair(0.60, np.nan)[()] == pytest.approx(0.60)


def test_poly_edge_after_fee():
    e = poly_edge(0.60, 0.55, poly_fee, 100)
    assert float(e) == pytest.approx(0.60 - 0.55 - poly_fee.per_contract(0.55, 100))


def test_arb_edge_picks_cheaper_kalshi_leg():
    # Buy T on Polymarket US at 0.40; Kalshi T bid 0.47 -> NO on T costs 0.53; Kalshi O ask 0.55.
    edge, leg = arb_edge(0.40, 0.47, 0.55, poly_fee, kalshi_fee, 100)
    cost_no = 0.53 + kalshi_fee.per_contract(0.53, 100)
    expected = 1 - 0.40 - poly_fee.per_contract(0.40, 100) - cost_no
    assert float(edge) == pytest.approx(expected) and leg[()] == "no_t"
    # Make the other team's YES cheaper than NO-on-T
    edge2, leg2 = arb_edge(0.40, 0.40, 0.52, poly_fee, kalshi_fee, 100)
    assert leg2[()] == "yes_o"
    assert float(edge2) == pytest.approx(1 - 0.40 - poly_fee.per_contract(0.40, 100) - 0.52
                                         - kalshi_fee.per_contract(0.52, 100))


def test_arb_edge_missing_quotes():
    edge, leg = arb_edge(0.40, np.nan, np.nan, poly_fee, kalshi_fee, 100)
    assert np.isnan(edge) and leg[()] is None
    edge, leg = arb_edge(0.40, np.nan, 0.55, poly_fee, kalshi_fee, 100)
    assert leg[()] == "yes_o" and not np.isnan(edge)
