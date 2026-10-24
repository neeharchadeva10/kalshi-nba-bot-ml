"""Unit tests for the pure math in drift.py. Run: .venv/Scripts/python.exe -m pytest reports/output/nba_drift/test_drift.py"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import drift as d  # noqa: E402


def test_fee_known_values():
    # 0.07*10*0.5*0.5 = 0.175 -> ceil to 0.0001 = 0.1750 -> per contract 0.0175
    assert math.isclose(d.fee(0.5), 0.0175, abs_tol=1e-12)
    # P=0.65: 0.07*10*0.65*0.35 = 0.15925 -> 0.1593 -> 0.01593
    assert math.isclose(d.fee(0.65), 0.01593, abs_tol=1e-12)
    # P=0.01: 0.07*10*0.01*0.99=0.00693 -> 0.0070 -> 0.0007 ; extremes cost ~0
    assert math.isclose(d.fee(0.01), 0.0007, abs_tol=1e-12)
    assert d.fee(0.0) == 0.0 and d.fee(1.0) == 0.0


def test_fee_no_float_overshoot():
    # exact multiples of 0.0001 must not be bumped up by float noise
    for p in (0.5, 0.2, 0.8, 0.3, 0.7):
        raw = 0.07 * 10 * p * (1 - p)
        assert d.fee(p) * 10 <= raw + 0.0001 + 1e-12
        assert d.fee(p) * 10 >= raw - 1e-12


def test_clv_and_pnls():
    assert math.isclose(d.clv_net(0.60, 0.55), 0.05 - d.fee(0.55))
    assert math.isclose(d.clv_net(0.50, 0.55), -0.05 - d.fee(0.55))
    assert math.isclose(d.settle_pnl(1.0, 0.55), 0.45 - d.fee(0.55))
    assert math.isclose(d.settle_pnl(0.0, 0.55), -0.55 - d.fee(0.55))
    assert math.isclose(d.exit_pnl(0.58, 0.55), 0.03 - d.fee(0.55) - d.fee(0.58))


def test_elo_update_and_prob():
    assert math.isclose(d.elo_prob_home(1500, 1500), 1 / (1 + 10 ** (-100 / 400)))
    p = d.elo_prob_home(1500, 1500)
    h, a = d.elo_update(1500, 1500, True)
    assert math.isclose(h, 1500 + 20 * (1 - p)) and math.isclose(a, 1500 - 20 * (1 - p))
    h, a = d.elo_update(1500, 1500, False)
    assert math.isclose(h, 1500 - 20 * p) and math.isclose(a, 1500 + 20 * p)
    assert math.isclose(h + a, 3000)  # zero-sum


def test_split_index():
    assert d.split_index(10) == 5 and d.split_index(11) == 6 and d.split_index(1) == 1 and d.split_index(0) == 0


def test_last_at_or_before():
    ts = [10, 20, 30]
    assert d.last_at_or_before(ts, 5) is None
    assert d.last_at_or_before(ts, 10) == 0       # equal counts ("at or before")
    assert d.last_at_or_before(ts, 29) == 1
    assert d.last_at_or_before(ts, 30) == 2
    assert d.last_at_or_before(ts, 99) == 2
    assert d.last_at_or_before([], 5) is None


def test_at_returns_age():
    r = d.at([100, 160], [0.4, 0.5], [0.5, 0.6], 200)
    assert r == (0.5, 0.6, 160, 40)
    assert d.at([100], [0.4], [0.5], 99) is None


def test_valid_quote():
    assert d.valid_quote(0.4, 0.5)
    assert not d.valid_quote(0.0, 0.5) and not d.valid_quote(0.4, 1.0)
    assert not d.valid_quote(None, 0.5) and not d.valid_quote(0.6, 0.5)
    assert not d.valid_quote(float("nan"), 0.5)


def test_bootstrap_reproducible_and_contains_mean():
    x = np.array([0.1, -0.05, 0.02, 0.03, -0.01] * 40)
    lo, hi = d.bootstrap_ci(x, draws=2000)
    assert (lo, hi) == d.bootstrap_ci(x, draws=2000)
    assert lo < x.mean() < hi
