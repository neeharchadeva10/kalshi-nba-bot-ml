import pandas as pd

from kpm.analysis.crypto_maker import maker_fill

TR = pd.DataFrame({
    "minute_ts": [120, 180, 180, 240],
    "yes_price": [0.92, 0.91, 0.93, 0.08],
    "taker_side": ["no", "no", "yes", "yes"],
    "count": [300, 250, 1000, 600],
})


def test_yes_favorite_bid_fills_from_takers_selling_yes():
    # bid 0.92 placed at t0=60: sells at <= 0.92 after t0 -> 300 + 250 = 550 eligible, 250 strictly through
    assert maker_fill(TR, 60, 300, True, 0.92, "generous", 100) == 100
    assert maker_fill(TR, 60, 300, True, 0.92, "realistic", 100) == 100   # 550 >= 5x100
    assert maker_fill(TR, 60, 300, True, 0.92, "strict", 100) == 100      # 250 through at 0.91
    assert maker_fill(TR, 60, 300, True, 0.90, "generous", 100) == 0       # nobody sold at <= 0.90


def test_orders_only_see_prints_after_placement_and_before_cancel():
    assert maker_fill(TR, 180, 300, True, 0.92, "generous", 100) == 0      # sells were at or before t0
    assert maker_fill(TR, 60, 150, True, 0.92, "realistic", 100) == 0      # only 300 before cancel < 500


def test_no_favorite_bid_fills_from_takers_buying_yes():
    # NO favorite bid 0.92 <=> filled by YES buys at >= 0.08: 1000 at 0.93 (through) + 600 at 0.08 (at level)
    assert maker_fill(TR, 60, 300, False, 0.92, "generous", 100) == 100
    assert maker_fill(TR, 60, 300, False, 0.92, "strict", 100) == 100
    assert maker_fill(TR, 200, 300, False, 0.92, "strict", 100) == 0       # only the print at exactly 0.08 remains
