import numpy as np
import pytest

from kpm.analysis.crypto_bot_rules import BotRules, simulate_market
from kpm.core.fees import FeeModel

fee = FeeModel()   # Kalshi taker fee rounded up to the cent, as for 1-contract orders
R = BotRules("t", 0.75, 0.85, 8, 0.40, 0.10)
TAU = np.arange(14, -1, -1, dtype=float)   # minute closes from 14 to 0 minutes left


def book(up_ask, spread=0.01):
    a = np.array(up_ask, float)
    return a - spread, a   # (yes_bid, yes_ask)


def run(bid, ask, y, rules=R, lo=None, hi=None, intraminute=False):
    return simulate_market(TAU, bid, ask, bid if lo is None else lo, ask if hi is None else hi, y, rules, fee,
                           intraminute=intraminute)


def test_take_profit_on_up_side():
    # in range only from 7 minutes left; ask reaches entry + 10c four minutes later
    bid, ask = book([0.60] * 7 + [0.80, 0.84, 0.88, 0.91, 0.93, 0.95, 0.97, 0.99])
    t = run(bid, ask, 1.0)
    assert t["side"] == "UP" and t["tau_entry"] == 7 and t["entry"] == pytest.approx(0.80)
    assert t["reason"] == "take_profit" and t["exit"] == pytest.approx(0.90)   # sold at the bid
    assert t["pnl"] == pytest.approx(0.90 - 0.80 - 0.02 - 0.01)               # fees: 2c in, 1c out


def test_stop_loss_on_down_side():
    yes_bid = np.array([0.50] * 7 + [0.20, 0.40, 0.65, 0.70, 0.80, 0.90, 0.95, 0.98])
    yes_ask = yes_bid + 0.01
    t = run(yes_bid, yes_ask, 1.0)
    assert t["side"] == "DOWN" and t["entry"] == pytest.approx(0.80)          # NO ask = 1 - YES bid
    assert t["reason"] == "stop" and t["exit"] == pytest.approx(0.34)         # NO bid = 1 - YES ask
    assert t["pnl"] == pytest.approx(0.34 - 0.02 - 0.80 - 0.02)


def test_no_entry_outside_window_or_range():
    bid, ask = book([0.60] * 4 + [0.80] + [0.95] * 10)   # in range only with 10 minutes left
    assert run(bid, ask, 1.0) is None


def test_wide_spread_blocks_exit_then_settles():
    yes_ask = np.array([0.60] * 7 + [0.80, 0.35, 0.36, 0.37, 0.36, 0.35, 0.34, 0.30])
    yes_bid = np.where(np.arange(15) > 7, yes_ask - 0.15, yes_ask - 0.01)    # 15c spread after entry
    t = run(yes_bid, yes_ask, 0.0)
    assert t["reason"] == "settled_loss" and t["pnl"] == pytest.approx(-0.80 - 0.02)


def test_hold_winners_to_settlement():
    bid, ask = book([0.60] * 7 + [0.80, 0.84, 0.88, 0.91, 0.93, 0.95, 0.97, 0.99])
    t = run(bid, ask, 1.0, rules=BotRules("hold", 0.75, 0.85, 8, 0.40, 1.0))
    assert t["reason"] == "settled_win" and t["pnl"] == pytest.approx(1.0 - 0.80 - 0.02)


def test_delta_filter_blocks_entries_near_the_opening_price():
    bid, ask = book([0.60] * 7 + [0.80, 0.84, 0.88, 0.91, 0.93, 0.95, 0.97, 0.99])
    spot = np.full(15, 100_010.0)
    spot[7] = 100_005.0                      # only $5 from the opening price when the ask first enters the range
    r = BotRules("delta", 0.75, 0.85, 8, 0.40, 0.10, delta=10.0)
    t = simulate_market(TAU, bid, ask, bid, ask, 1.0, r, fee, spot=spot, strike=100_000.0)
    assert t["tau_entry"] == 6 and t["entry"] == pytest.approx(0.84)
    assert simulate_market(TAU, bid, ask, bid, ask, 1.0, r, fee) is None   # no BTC prices: the filter never passes


def test_intraminute_touch_counts_but_not_before_entry():
    bid, ask = book([0.60] * 7 + [0.80, 0.84, 0.85, 0.86, 0.87, 0.88, 0.88, 0.88])
    hi = ask.copy()
    hi[7] = 0.95          # spike inside the entry minute happened before the fill: ignored
    hi[9] = 0.91          # spike two minutes later: take-profit at the limit (target - 2c)
    t = run(bid, ask, 0.0, hi=hi, intraminute=True)
    assert t["reason"] == "take_profit" and t["exit"] == pytest.approx(0.88)
    assert run(bid, ask, 0.0)["reason"] == "settled_loss"   # closes alone never reach the target
