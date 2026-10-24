import pytest

from kpm.core import prices
from kpm.core.timeutil import et_to_utc_ts, parse_iso, to_iso


def test_parse_dollars():
    assert prices.parse_dollars("0.4200") == 0.42
    assert prices.parse_dollars(None) is None
    assert prices.parse_dollars("") is None


def test_clean_quote_drops_empty_sides():
    assert prices.clean_quote(0.0, 0.42) == (None, 0.42)
    assert prices.clean_quote(0.41, 1.0) == (0.41, None)
    assert prices.clean_quote(0.41, 0.42) == (0.41, 0.42)
    assert prices.clean_quote(0.45, 0.42) == (None, None)  # crossed -> unusable


def test_mid():
    assert prices.mid(0.40, 0.42) == pytest.approx(0.41)
    assert prices.mid(None, 0.42) is None


def test_american_odds():
    assert prices.american_to_prob(-200) == pytest.approx(2 / 3)
    assert prices.american_to_prob(+150) == pytest.approx(0.4)
    assert prices.american_to_prob(100) == pytest.approx(0.5)
    assert prices.american_to_prob(None) is None
    assert prices.american_to_prob(float("nan")) is None


def test_devig():
    a, b = prices.devig_two_way(prices.american_to_prob(-110), prices.american_to_prob(-110))
    assert a == pytest.approx(0.5) and b == pytest.approx(0.5)
    a, b = prices.devig_two_way(0.8, 0.3)
    assert a + b == pytest.approx(1.0)
    assert a == pytest.approx(0.8 / 1.1)


def test_no_side_conversions():
    assert prices.no_ask_from_yes_bid(0.40) == pytest.approx(0.60)
    assert prices.no_bid_from_yes_ask(0.42) == pytest.approx(0.58)
    assert prices.complement(0.3) == pytest.approx(0.7)


def test_parse_iso_variants():
    assert parse_iso("2025-09-05T00:20:00Z") == 1757031600
    assert parse_iso("2025-09-05 00:20:00+00") == 1757031600
    assert parse_iso("2025-09-05T00:20:00.123456Z") == 1757031600
    assert parse_iso(None) is None


def test_et_kickoff_to_utc_handles_dst():
    assert et_to_utc_ts("2025-09-04", "20:20") == 1757031600          # EDT (UTC-4)
    assert to_iso(et_to_utc_ts("2025-12-07", "13:00")) == "2025-12-07T18:00:00Z"  # EST (UTC-5)
