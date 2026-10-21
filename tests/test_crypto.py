import numpy as np
import pytest

from kpm.analysis.crypto15m import fair_up, norm_cdf, settle_variance, split_of

C = {"early_end": "2026-07-01T00:00:00Z", "later_end": "2026-08-05T00:00:00Z"}


def test_norm_cdf():
    assert float(norm_cdf(0.0)) == pytest.approx(0.5)
    assert float(norm_cdf(1.96)) == pytest.approx(0.975, abs=1e-3)


def test_settle_variance_accounts_for_final_minute_average():
    assert float(settle_variance(0.001, 5)) == pytest.approx(0.001 ** 2 * (4 + 1 / 3))
    assert float(settle_variance(0.001, 1)) == pytest.approx(0.001 ** 2 / 3)
    assert float(settle_variance(0.001, 5, basis_var=1e-8)) == pytest.approx(0.001 ** 2 * (4 + 1 / 3) + 1e-8)


def test_fair_up_basic_properties():
    assert float(fair_up(100_000, 100_000, 0.001, 10)) == pytest.approx(0.5)
    up, down = 100_000 * np.exp(0.001), 100_000 * np.exp(-0.001)  # symmetric in log space
    above = float(fair_up(up, 100_000, 0.001, 10))
    below = float(fair_up(down, 100_000, 0.001, 10))
    assert above > 0.5 > below and above == pytest.approx(1 - below, abs=1e-9)
    # less time left -> more certain
    assert float(fair_up(up, 100_000, 0.001, 2)) > above
    # a positive basis (BRTI above Coinbase) raises the probability
    assert float(fair_up(100_000, 100_000, 0.001, 10, basis_mean=1e-4)) > 0.5


def test_favorite_side_prices():
    from kpm.analysis.crypto_trends import side_prices
    fav_yes, fav_mid, fav_ask, dog_ask = side_prices([0.90, 0.20], [0.92, 0.22])
    assert list(fav_yes) == [True, False]
    assert fav_mid == pytest.approx([0.91, 0.79])
    assert fav_ask == pytest.approx([0.92, 0.80])   # NO ask = 1 - YES bid
    assert dog_ask == pytest.approx([0.10, 0.22])


def test_swing_structure_trend():
    from kpm.analysis.crypto_htf import swing_trend_states
    up = np.array([1, 2, 3, 2, 1, 2, 4, 3, 2, 3, 5, 4, 3], float)   # highs 3,4,5 / lows 1,2 -> HH + HL
    s = swing_trend_states(up, n=1)
    assert s[8] == 0 and s[9] == 1          # trend only known once the second higher low is confirmed (bar 9)
    down = 10 - up                            # mirror image: lower highs and lower lows
    assert swing_trend_states(down, n=1)[9] == -1
    flat = np.array([1, 2, 1, 2, 1, 2, 1, 2, 1, 2], float)           # equal highs/lows -> no trend
    assert (swing_trend_states(flat, n=1) == 0).all()


def test_breakout_levels_and_breaks():
    import pandas as pd
    from kpm.analysis.crypto_breakout import breaks, swing_levels
    # 1-minute closes on a 1-minute "chart" (bar_minutes=1) so each minute is a bar: swing high 5 at t=2,
    # confirmed at t=4 (n=2); price crosses above 5 at t=6 -> break up there, not before confirmation.
    ts = np.arange(0, 9) * 60
    closes = pd.Series([1, 3, 5, 4, 3, 4, 6, 7, 6], index=ts, dtype=float)
    lv = swing_levels(closes, bar_minutes=1, n=2)
    assert np.isnan(lv["H"].iloc[3]) and lv["H"].iloc[4] == 5
    b = breaks(closes, lv)
    assert list(b) == [0, 0, 0, 0, 0, 0, 1, 0, 0]


def test_split_boundaries():
    assert split_of(1782863999, C) == "early"      # 2026-06-30 23:59:59Z
    assert split_of(1782864000, C) == "later"      # 2026-07-01 00:00:00Z
    assert split_of(1785888000, C) == "fresh"      # 2026-08-05 00:00:00Z
