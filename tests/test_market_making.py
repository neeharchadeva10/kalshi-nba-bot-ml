import numpy as np
import pandas as pd
import pytest

from kpm.analysis.market_making import MMParams, fill_qty, our_quotes, simulate
from kpm.core.fees import FeeModel

P = MMParams("improve_only", 0.0, 0.0, inv_limit=150, size=100)
fee = FeeModel()


def test_quotes_improve_when_wide_only():
    assert our_quotes(0.40, 0.43, 0.415, P)[:2] == pytest.approx((0.41, 0.42))
    assert our_quotes(0.40, 0.41, 0.405, P)[:2] == (None, None)                         # 1c wide: improve_only skips
    j = MMParams("join_and_improve", 0.0, 0.0)
    assert our_quotes(0.40, 0.41, 0.405, j)[:2] == pytest.approx((0.40, 0.41))
    # guard: never bid above fair - guard
    g = MMParams("improve_only", 0.01, 0.0)
    qb, qa, _, _ = our_quotes(0.40, 0.43, 0.41, g)
    assert qb is None and qa == pytest.approx(0.42)


def test_fill_rules():
    assert fill_qty(60, 0, True, "generous", 100) == 60
    assert fill_qty(60, 0, False, "strict", 100) == 0
    assert fill_qty(60, 30, False, "strict", 100) == 30
    assert fill_qty(60, 0, True, "realistic", 100) == 60      # improved quote: first in line
    assert fill_qty(400, 0, False, "realistic", 100) == 0     # joined quote needs >= 5x size
    assert fill_qty(500, 0, False, "realistic", 100) == 100


def _game(n=5, bid=0.40, ask=0.43):
    return pd.DataFrame({"ts": np.arange(n) * 60, "bid_A": bid, "ask_A": ask, "bid_B": 1 - ask, "ask_B": 1 - bid,
                         "fair_A": (bid + ask) / 2, "mins_to_kickoff": np.arange(n)[::-1] * 1.0})


def test_simulate_buy_then_flatten():
    g = _game()
    ev = pd.DataFrame({"i": [0], "sell_le_bid": [0], "sell_lt_bid": [0], "sell_le_bid1": [100],
                       "buy_ge_ask": [0], "buy_gt_ask": [0], "buy_ge_ask1": [0],
                       "bid": [0.40], "ask": [0.43], "fair": [0.415], "mins_to_kickoff": [4.0]})
    r = simulate(ev, g, "A", 1.0, P, "realistic", fee)
    # bought 100 at 0.41 (maker fee 0.0175*100*0.41*0.59 = 0.4233 -> $0.43), flattened at bid 0.40 (taker $1.68)
    assert r["fills"] == 1 and r["end_inventory"] == 100
    assert r["pnl_flatten"] == pytest.approx(-0.41 * 100 - 0.43 + 0.40 * 100 - 1.68)
    assert r["pnl_hold"] == pytest.approx(-0.41 * 100 - 0.43 + 100 * 1.0)


def test_simulate_respects_inventory_limit():
    g = _game()
    ev = pd.DataFrame({"i": [0, 1], "sell_le_bid": [0, 0], "sell_lt_bid": [0, 0], "sell_le_bid1": [100, 100],
                       "buy_ge_ask": [0, 0], "buy_gt_ask": [0, 0], "buy_ge_ask1": [0, 0],
                       "bid": [0.40, 0.40], "ask": [0.43, 0.43], "fair": [0.415, 0.415], "mins_to_kickoff": [4.0, 3.0]})
    r = simulate(ev, g, "A", 0.0, P, "generous", fee)
    assert r["end_inventory"] == 150 and r["volume"] == 150
