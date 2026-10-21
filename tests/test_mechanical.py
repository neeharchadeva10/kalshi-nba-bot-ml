import numpy as np
import pandas as pd
import pytest

from kpm.analysis.crypto_cross_bounds import hourly_event, nearest_strikes, pair_profits
from kpm.analysis.crypto_final_minute import observations
from kpm.core.fees import FeeModel

fee = FeeModel(rounding="none")


def test_hourly_event_matches_the_15_minute_close():
    assert hourly_event("KXBTC15M-26OCT051400-00") == "KXBTCD-26OCT0514"
    assert hourly_event("KXBTC15M-26JUN022200-00") == "KXBTCD-26JUN0222"


def test_nearest_strikes_two_each_side():
    ms = [{"floor_strike": k} for k in (84799.99, 84899.99, 84999.99, 85099.99, 85199.99, 85299.99)]
    got = [m["floor_strike"] for m in nearest_strikes(ms, 85050.0)]
    assert got == [84899.99, 84999.99, 85099.99, 85199.99]


def quotes(bid, ask):
    return pd.DataFrame({"ts": [60, 120], "yes_bid": bid, "yes_ask": ask})


def test_pair_below_target_buys_hourly_yes_and_15m_down():
    # strike below target: hourly YES ask 0.55, 15m YES bid 0.50 -> DOWN costs 0.50; total 1.05 -> no violation
    d = pair_profits(quotes([0.40, 0.40], [0.42, 0.42]), quotes([0.53, 0.53], [0.55, 0.55]), 85000, 84900, fee)
    assert d["kind"].iloc[0] == "hourly YES + 15m DOWN"
    assert d["cost"].iloc[0] == pytest.approx(0.55 + (1 - 0.40))
    # hourly YES (more likely than UP) priced below UP: ask 0.40 while 15m YES bid 0.45 -> cost 0.40 + 0.55 = 0.95
    d2 = pair_profits(quotes([0.45, 0.45], [0.47, 0.47]), quotes([0.38, 0.38], [0.40, 0.40]), 85000, 84900, fee)
    exp = 1 - 0.95 - 0.07 * 0.40 * 0.60 - 0.07 * 0.55 * 0.45
    assert d2["profit"].iloc[0] == pytest.approx(exp)


def test_pair_above_target_buys_15m_up_and_hourly_no():
    # strike at/above target: hourly YES bid 0.60 while 15m UP ask 0.35 -> cost 0.35 + 0.40 = 0.75
    d = pair_profits(quotes([0.33, 0.33], [0.35, 0.35]), quotes([0.60, 0.60], [0.62, 0.62]), 85000, 85000, fee)
    assert d["kind"].iloc[0] == "15m UP + hourly NO"
    assert d["cost"].iloc[0] == pytest.approx(0.75)
    assert d["profit"].iloc[0] > 0


def test_final_minute_observations_use_favorite_taker_buys():
    tf = pd.DataFrame({
        "market_id": [1, 1, 1, 1],
        "sec_left": [10, 10, 10, 70],
        "yes_price": [0.96, 0.97, 0.96, 0.92],
        "taker_side": ["yes", "yes", "no", "yes"],   # the 'no' print sold YES: not a favorite buy
        "count": [10.0, 30.0, 50.0, 20.0],
        "n_trades": [1, 1, 1, 1]})
    mk = pd.DataFrame({"market_id": [1], "settlement_value": [1.0], "split": ["later"], "day": [1]})
    obs = observations(tf, mk, fee)
    row = obs[obs["sec_b"] == "5-15s"].iloc[0]
    assert row["price_b"] == "95-98c" and row["size"] == 40
    assert row["vwap"] == pytest.approx((0.96 * 10 + 0.97 * 30) / 40)
    assert row["edge"] == pytest.approx(1 - row["vwap"] - 0.07 * row["vwap"] * (1 - row["vwap"]))
    assert set(obs["sec_b"]) == {"5-15s", "60-90s (control)"}


def test_no_side_favorite_is_mapped_to_no_price():
    tf = pd.DataFrame({"market_id": [2], "sec_left": [3], "yes_price": [0.04], "taker_side": ["no"],
                       "count": [25.0], "n_trades": [1]})
    mk = pd.DataFrame({"market_id": [2], "settlement_value": [1.0], "split": ["fresh"], "day": [1]})
    row = observations(tf, mk, fee).iloc[0]
    assert row["vwap"] == pytest.approx(0.96) and not row["fav_yes"] and row["win"] == 0.0
