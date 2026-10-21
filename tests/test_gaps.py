import numpy as np
import pandas as pd
import pytest

from kpm.analysis.convergence import classify, measure
from kpm.analysis.episodes import find_episodes
from kpm.analysis.gaps import compute_edges, ttk_bucket
from kpm.analysis.strategies import cluster_bootstrap_ci, fill_volume, max_drawdown
from kpm.core.fees import FeeModel

fee = FeeModel()


def test_edge_yes_and_no():
    # Poly fair 0.55; Kalshi 0.50 / 0.52. 100 contracts.
    e = compute_edges(0.55, 0.50, 0.52, fee, 100)
    fee_yes = fee.per_contract(0.52, 100)          # ceil(1.7472) = 1.75 / 100
    assert float(e["fee_yes"]) == pytest.approx(fee_yes)
    assert float(e["edge_yes"]) == pytest.approx(0.55 - 0.52 - fee_yes)
    fee_no = fee.per_contract(0.50, 100)           # NO ask = 1 - 0.50
    assert float(e["edge_no"]) == pytest.approx(0.45 - 0.50 - fee_no)
    assert float(e["edge_no"]) < 0


def test_edge_no_positive_when_poly_below_bid():
    e = compute_edges(0.40, 0.45, 0.47, fee, 100)
    assert float(e["edge_no"]) == pytest.approx(0.60 - 0.55 - fee.per_contract(0.55, 100))
    assert float(e["edge_yes"]) < 0


def test_edges_vectorized_with_missing():
    e = compute_edges([0.55, np.nan], [0.50, 0.50], [0.52, np.nan], fee, 100)
    assert not np.isnan(e["edge_yes"][0])
    assert np.isnan(e["edge_yes"][1]) and np.isnan(e["edge_no"][1])


def test_ttk_buckets():
    b = {">24h": [86400, 1e12], "24h-3h": [10800, 86400], "3h-90m": [5400, 10800], "last 90m": [0, 5400]}
    out = ttk_bucket([90000, 86400, 10799, 5400, 60], b)
    assert list(out) == [">24h", ">24h", "3h-90m", "3h-90m", "last 90m"]


def test_find_episodes_breaks_on_nan_and_grid_gaps():
    ts = np.array([0, 60, 120, 180, 240, 360, 420])
    edge = np.array([0.02, 0.03, np.nan, 0.02, 0.02, 0.02, 0.0])
    assert find_episodes(ts, edge, 0.01) == [(0, 1), (3, 4), (5, 5)]


def test_find_episodes_threshold_is_strict():
    ts = np.arange(0, 300, 60)
    assert find_episodes(ts, np.array([0.01, 0.01, 0.011, 0.0, 0.02]), 0.01) == [(2, 2), (4, 4)]


def test_classify():
    assert classify(0.01, 0.0, 0.005) == "kalshi_toward_poly"
    assert classify(0.0, 0.01, 0.005) == "poly_toward_kalshi"
    assert classify(0.01, 0.01, 0.005) == "both"
    assert classify(-0.01, 0.0, 0.005) == "neither"
    assert classify(np.nan, 0.0, 0.005) == "no_data"


def test_convergence_measure_kalshi_moves_up():
    n = 20
    g = pd.DataFrame({"ts": np.arange(n) * 60, "k_mid": np.r_[[0.50] * 5, [0.54] * 15],
                      "p_ref": [0.55] * n, "p_age_s": [0] * n})
    ep = pd.DataFrame({"start_idx": [0]})
    out = measure(g, ep, [5, 60], 0.005, 600)
    assert out["gap0"].iloc[0] == pytest.approx(0.05)
    assert out["kalshi_move_5m"].iloc[0] == pytest.approx(0.04)
    assert out["poly_move_5m"].iloc[0] == pytest.approx(0.0)
    assert out["class_5m"].iloc[0] == "kalshi_toward_poly"
    assert out["class_60m"].iloc[0] == "beyond_kickoff"
    assert out["closed_frac_kickoff"].iloc[0] == pytest.approx(0.8)


def test_convergence_no_side_sign():
    # Poly below Kalshi (NO-side gap); Poly rises to Kalshi -> poly moved toward Kalshi.
    g = pd.DataFrame({"ts": [0, 60], "k_mid": [0.50, 0.50], "p_ref": [0.45, 0.50], "p_age_s": [0, 0]})
    out = measure(g, pd.DataFrame({"start_idx": [0]}), [1], 0.005, 600)
    assert out["class_1m"].iloc[0] == "poly_toward_kalshi"


def test_fill_volume():
    tr = pd.DataFrame({"minute_ts": [60, 120, 600], "yes_price": [0.52, 0.53, 0.52], "count": [80, 50, 999]})
    assert fill_volume(tr, 120, "yes", 0.50, 0.52, 60) == 80        # only prints at <= ask, within window
    assert fill_volume(tr, 120, "no", 0.52, 0.54, 60) == 130        # NO fills need yes_price >= yes bid


def test_max_drawdown():
    assert max_drawdown(np.array([1, -2, 3, -4, 1])) == pytest.approx(4)
    assert max_drawdown(np.array([1, 2, 3])) == 0
    assert max_drawdown(np.array([-1, -1])) == pytest.approx(2)


def test_cluster_bootstrap_ci_contains_mean():
    v = np.array([0.01, 0.02, -0.01, 0.03, 0.0, 0.02])
    lo, hi = cluster_bootstrap_ci(v, np.array([1, 1, 2, 2, 3, 3]), reps=500)
    assert lo <= v.mean() <= hi
