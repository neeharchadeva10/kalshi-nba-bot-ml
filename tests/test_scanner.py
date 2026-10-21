import pandas as pd
import pytest

from kpm.core.fees import FeeModel
from kpm.scanner.structural import check_ladder, check_mutually_exclusive, is_exhaustive_strike_set

fee = FeeModel(rounding="none")


def ladder(bids, asks, strikes, kind="greater"):
    return pd.DataFrame({"ticker": [f"T{k}" for k in strikes], "strike_type": kind,
                         "floor": strikes if kind == "greater" else None, "cap": strikes if kind == "less" else None,
                         "bid": bids, "ask": asks, "bid_size": 500.0, "ask_size": 500.0, "mutually_exclusive": False})


def test_greater_ladder_violation_found_and_priced():
    # "above 100" ask 0.40 but "above 110" bid 0.50 -> buy >100, sell >110: worst case $1 back for 0.40 + 0.50
    ev = ladder([0.38, 0.50], [0.40, 0.52], [100, 110])
    hits = check_ladder(ev, fee, "greater")
    assert len(hits) == 1
    expected = 0.50 - 0.40 - fee.per_contract(0.40, 100) - fee.per_contract(0.50, 100)
    assert hits[0]["profit_per_set"] == pytest.approx(expected)
    assert hits[0]["legs"][0]["ticker"] == "T100" and hits[0]["legs"][1]["ticker"] == "T110"


def test_consistent_ladder_has_no_hits():
    ev = ladder([0.60, 0.40, 0.20], [0.62, 0.42, 0.22], [100, 110, 120])
    assert check_ladder(ev, fee, "greater") == []


def test_separate_underlyings_are_not_one_ladder():
    # rain markets for different cities in one event: not comparable, even with "violating" prices
    ev = pd.DataFrame({"ticker": ["KXRAIN-26OCT04-SFO", "KXRAIN-26OCT04-LV", "KXRAIN-26OCT04-PHX"],
                       "strike_type": "greater", "floor": [0.0, 0.0, 0.0], "cap": None,
                       "bid": [0.01, 0.99, 0.50], "ask": [0.02, 1.00, 0.52], "bid_size": 500.0, "ask_size": 500.0,
                       "mutually_exclusive": False})
    assert check_ladder(ev, fee, "greater") == []
    # two players' receptions ladders in one event: only compare within a player
    ev2 = pd.DataFrame({"ticker": ["E-PLAYERA3", "E-PLAYERA5", "E-PLAYERB4"], "strike_type": "greater",
                        "floor": [3.0, 5.0, 4.0], "cap": None, "bid": [0.60, 0.30, 0.90], "ask": [0.62, 0.32, 0.92],
                        "bid_size": 500.0, "ask_size": 500.0, "mutually_exclusive": False})
    assert check_ladder(ev2, fee, "greater") == []
    # real Kalshi prop format: player in the second-to-last segment, bare strike number last
    ev3 = pd.DataFrame({"ticker": ["KXNFLREC-X-NOJJOHNSON83-2", "KXNFLREC-X-NOAKAMARA41-5"],
                        "title": ["Juwan Johnson: 2+", "Alvin Kamara: 5+"], "custom_strike": ['{"p": "a"}', '{"p": "b"}'],
                        "strike_type": "greater", "floor": [1.5, 4.5], "cap": None, "bid": [0.10, 0.60],
                        "ask": [0.12, 0.62], "bid_size": 500.0, "ask_size": 500.0, "mutually_exclusive": False})
    assert check_ladder(ev3, fee, "greater") == []
    # ...while one player's own ladder is still checked
    ev4 = ev3.assign(ticker=["KXNFLREC-X-NOJJOHNSON83-2", "KXNFLREC-X-NOJJOHNSON83-5"],
                     title=["Juwan Johnson: 2+", "Juwan Johnson: 5+"], custom_strike='{"p": "a"}')
    assert len(check_ladder(ev4, fee, "greater")) == 1


def test_read_only_guard_allows_orderbook_but_not_orders():
    from kpm.clients.http import CachedClient
    cc = CachedClient("https://example.invalid", "test_guard", max_retries=0)
    for path in ("/portfolio/orders", "/portfolio/balance", "/orders", "/portfolio/fills"):
        with pytest.raises(PermissionError):
            cc.get(path)
    # /markets/X/orderbook passes the guard (it fails later only because the host is invalid)
    with pytest.raises(Exception) as exc:
        cc.get("/markets/KXBTCD-X/orderbook", use_cache=False)
    assert not isinstance(exc.value, PermissionError)


def test_less_ladder_violation():
    # "below 100" bid 0.55 but "below 110" ask 0.45 -> buy <110, sell <100
    ev = ladder([0.55, 0.43], [0.57, 0.45], [100, 110], kind="less")
    hits = check_ladder(ev, fee, "less")
    assert len(hits) == 1 and hits[0]["legs"][0]["ticker"] == "T110"


def test_exact_buckets_mislabeled_as_tails_are_ignored():
    # KXSTARSHIPSPACE-26 style: "exactly 4" / "exactly 5" carry strike_type 'less' with floor == cap
    ev = ladder([0.48, 0.32], [0.51, 0.38], [4.0, 5.0], kind="less").assign(floor=[4.0, 5.0])
    assert check_ladder(ev, fee, "less") == []


def me_event(bids, asks, types=None, floors=None, caps=None):
    n = len(bids)
    return pd.DataFrame({"ticker": [f"M{i}" for i in range(n)], "bid": bids, "ask": asks, "bid_size": 100.0,
                         "ask_size": 100.0, "mutually_exclusive": True,
                         "strike_type": types or ["custom"] * n, "floor": floors or [None] * n,
                         "cap": caps or [None] * n})


def test_sell_all_when_bids_exceed_one():
    ev = me_event([0.40, 0.35, 0.30], [0.42, 0.37, 0.32])   # bids sum 1.05
    hits = [h for h in check_mutually_exclusive(ev, fee) if h["kind"] == "sell_all"]
    assert len(hits) == 1
    expected = 0.05 - sum(fee.per_contract(1 - b, 100) for b in (0.40, 0.35, 0.30))
    assert hits[0]["profit_per_set"] == pytest.approx(expected)


def test_buy_all_flags_exhaustiveness():
    ev = me_event([0.28, 0.28, 0.28], [0.30, 0.30, 0.30],
                  types=["less", "between", "greater"], floors=[None, 100.0, 109.99], caps=[100.0, 109.99, None])
    hits = [h for h in check_mutually_exclusive(ev, fee) if h["kind"] == "buy_all"]
    assert len(hits) == 1 and hits[0]["details"]["exhaustive"] == "verified"
    assert is_exhaustive_strike_set(ev)
    gap = me_event([0.28] * 3, [0.30] * 3, types=["less", "between", "greater"],
                   floors=[None, 105.0, 109.99], caps=[100.0, 109.99, None])
    assert not is_exhaustive_strike_set(gap)
