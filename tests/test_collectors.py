import pytest

from kpm.collectors.kalshi_history import _candle_row


def test_candle_row_historical_format():
    c = {"end_period_ts": 60, "volume": "806.00", "open_interest": "1344672.00",
         "price": {"close": "0.2100", "mean": "0.2095"},
         "yes_ask": {"close": "0.2100", "high": "0.2100"}, "yes_bid": {"close": "0.2000", "low": "0.2000"}}
    r = _candle_row(1, c)
    assert (r["yes_bid"], r["yes_ask"], r["volume"], r["price_last"]) == pytest.approx((0.20, 0.21, 806.0, 0.21))


def test_candle_row_live_format():
    c = {"end_period_ts": 60, "volume_fp": "72.86", "open_interest_fp": "10083.41",
         "price": {"close_dollars": "0.6400", "mean_dollars": "0.6246"},
         "yes_ask": {"close_dollars": "0.6400", "high_dollars": "0.6400"},
         "yes_bid": {"close_dollars": "0.6000", "low_dollars": "0.5900"}}
    r = _candle_row(1, c)
    assert (r["yes_bid"], r["yes_ask"], r["yes_bid_low"], r["volume"]) == pytest.approx((0.60, 0.64, 0.59, 72.86))
    assert r["open_interest"] == pytest.approx(10083.41)
