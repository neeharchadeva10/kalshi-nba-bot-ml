import pytest

from kpm.analysis.lip_rewards import our_share, side_weights


def test_reference_price_and_discounted_weights():
    # target 1000 -> reference where cumulative size reaches 200: 120 @ 0.50, then 150 @ 0.49 (cum 270) -> ref 0.49
    levels = [(0.50, 120.0), (0.49, 150.0), (0.47, 100.0)]
    w, ref, depth = side_weights(levels, 1000, 0.5)
    assert ref == pytest.approx(0.49) and depth == 370
    assert w == pytest.approx(120 + 150 + 100 * 0.5 ** 2)   # 0.47 is two ticks below the reference


def test_our_share_at_best_bid_moves_the_reference_up():
    levels = [(0.50, 120.0), (0.49, 150.0)]
    # adding 100 at 0.50 -> cum 220 >= 200 at 0.50 -> ref 0.50; the 0.49 level is discounted one tick
    share = our_share(levels, 100, 1000, 0.5)
    assert share == pytest.approx(100 / (220 + 150 * 0.5))


def test_empty_side():
    assert side_weights([], 1000, 0.5)[0] == 0 and our_share([], 10, 1000, 0.5) == 0
