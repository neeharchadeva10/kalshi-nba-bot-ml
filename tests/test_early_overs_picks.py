import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import early_overs_picks as p  # noqa: E402


def q(strike, bid, ask):
    return {"strike": strike, "bid": bid, "ask": ask, "ticker": f"T{strike}"}


def test_yes_ask_from_no_bid():
    ob = {"orderbook_fp": {"yes_dollars": [["0.4000", "5"], ["0.4500", "2"]],
                           "no_dollars": [["0.3000", "9"], ["0.5200", "1"]]}}
    assert p.best_bid_ask_cents(ob) == (45, 48)  # ask = 1 - 0.52
    assert p.best_bid_ask_cents({"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}) == (None, None)


def test_strike_nearest_half_and_band():
    qs = [q(220, 80, 84), q(225, 60, 62), q(230, 44, 48), q(235, 20, 24)]
    assert p.select_strike(qs)["strike"] == 230
    assert p.select_strike([q(1, 10, 12), q(2, 88, 90)]) is None
    assert p.select_strike([q(1, 14, 16)])["strike"] == 1  # mid 15 inclusive
    assert p.select_strike([q(1, 0, 50), q(2, 50, 50)])["strike"] == 2  # invalid bid 0 skipped


def test_spread_cap_amendment():
    assert p.select_strike([q(1, 1, 99)]) is None  # 0.01/0.99 never picked
    assert p.select_strike([q(1, 40, 51)]) is None  # 11c spread
    assert p.select_strike([q(1, 40, 50)])["strike"] == 1  # 10c spread allowed
    # tighter strike farther from 50 beats a wide strike nearer 50
    assert p.select_strike([q(1, 44, 58), q(2, 35, 40)])["strike"] == 2


def test_tie_goes_lower():
    qs = [q(231, 50, 54), q(230, 46, 50), q(229, 40, 44)]  # mids 52 and 48 tie around 50
    assert p.select_strike(qs)["strike"] == 230
    assert p.select_strike(list(reversed(qs)))["strike"] == 230


def test_contracts_formula():
    assert p.contracts_for(50) == 2
    assert p.contracts_for(33) == 3
    assert p.contracts_for(34) == 2
    assert p.contracts_for(85) == 1
    assert p.contracts_for(99) == 1


def test_fee():
    cost, fee = p.cost_with_fee(50, 2)  # 0.07*2*.25 = 0.035
    assert abs(fee - 0.035) < 1e-12 and abs(cost - 1.035) < 1e-12
    assert abs(p.cost_with_fee(33, 3)[1] - 0.046431) < 1e-12  # 0.07*3*.33*.67
    assert abs(p.cost_with_fee(47, 1)[1] - 0.017437) < 1e-12  # 0.07*.47*.53
    # rounds UP to the micro-dollar: 0.07*1*.31*.69 = 0.07*3*.41*.59 = 0.050799
    assert abs(p.cost_with_fee(41, 3)[1] - 0.050799) < 1e-12


def test_maker_fee_and_cost():
    # maker 0.0175*2*.5*.5 = 0.00875 ; taker 0.035
    cost, fee = p.cost_with_fee(50, 2, maker=True)
    assert abs(fee - 0.00875) < 1e-12 and abs(cost - 1.00875) < 1e-12
    assert abs(p.cost_with_fee(50, 2)[1] - 0.035) < 1e-12
    # 0.0175*3*.41*.59 = 0.01269975 -> 0.0127 (micro ceil: 0.012700)
    assert abs(p.cost_with_fee(41, 3, maker=True)[1] - 0.0127) < 1e-12


def test_status_windows():
    tip = datetime(2026, 10, 22, 0, 0, tzinfo=timezone.utc)  # 20:00 ET

    def at(x):
        return tip + timedelta(minutes=x)

    assert p.status_for(at(-100), tip, 48) == "too early (in 25m)"
    assert p.status_for(at(-75), tip, 48) == "PLACE NOW (limit buy NO @ 0.48)"
    assert p.status_for(at(-45), tip, 48).startswith("PLACE NOW")
    assert p.status_for(at(-44), tip, 48) == "working - cancel if unfilled by 19:45"
    assert p.status_for(at(-15), tip, 48).startswith("working")
    assert p.status_for(at(-14), tip, 48) == "past window"


def test_test_window():
    def z(s):
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)

    assert not p.in_test_window(z("2026-10-21T03:00:00"))  # Oct 20 ET evening
    assert p.in_test_window(z("2026-10-22T00:00:00"))      # Oct 21 8pm ET
    assert p.in_test_window(z("2026-11-04T02:00:00"))      # Nov 3 ET
    assert not p.in_test_window(z("2026-11-04T06:00:00"))  # Nov 4 ET


def test_no_side_conversion():
    # yes 45/48 -> no bid = 100-48 = 52, no ask = 100-45 = 55
    assert p.best_no_cents(45, 48) == (52, 55)
    ob = {"orderbook_fp": {"yes_dollars": [["0.4500", "2"]], "no_dollars": [["0.5200", "1"]]}}
    bid, ask = p.best_bid_ask_cents(ob)
    assert p.best_no_cents(bid, ask)[0] == 52  # equals the orderbook's best no price


def test_no_side_contracts_and_fees():
    assert p.contracts_for(52) == 1 and p.contracts_for(48) == 2 and p.contracts_for(33) == 3
    cost, fee = p.cost_with_fee(48, 2, maker=True)  # 0.0175*2*.48*.52 = 0.008736
    assert abs(fee - 0.008736) < 1e-12 and abs(cost - 0.968736) < 1e-12
    assert p.cost_with_fee(49, 2)[1] > fee  # taker fee larger
