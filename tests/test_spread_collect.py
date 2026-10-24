import pytest

from kpm.collectors import kalshi_spread as ks


@pytest.mark.parametrize("ticker,team", [
    ("KXNFLSPREAD-26OCT12BUFLAR-LAR3", "LA"),
    ("KXNFLSPREAD-26OCT12BUFLAR-BUF12", "BUF"),
    ("KXNFLSPREAD-25SEP07GBKC-GB1", "GB"),
    ("KXNFLSPREAD-25SEP07SFKC-KC10", "KC"),
    ("KXNFLSPREAD-25SEP07SFKC-SF22", "SF"),
    ("KXNFLSPREAD-25SEP07JACHOU-JAC4", "JAX"),
    ("KXNFLGAME-26OCT12BUFLAR-BUF", "BUF"),
    ("KXNFLGAME-26OCT12BUFLAR-LAR", "LA"),
    ("KXNFLGAME-26OCT12BUFLAR-TIE", None),
    ("KXNFLSPREAD-25NOV16DETPHI-PHILADELPHIA17", "PHI"),
    ("KXNFLSPREAD-25SEP28NOBUF-BUFFALO22", "BUF"),
])
def test_team_from_ticker(ticker, team):
    assert ks.team_from_ticker(ticker) == team


def test_event_code_teams():
    assert ks.event_code_teams("KXNFLSPREAD-26OCT12BUFLAR") == frozenset({"BUF", "LA"})
    assert ks.event_code_teams("KXNFLGAME-25SEP07GBKC") == frozenset({"GB", "KC"})
    assert ks.event_code_teams("garbage") == frozenset()


def test_empty_sides_become_null():
    assert ks.bid_e4("0.0000") is None and ks.bid_e4(None) is None and ks.bid_e4(0) is None
    assert ks.ask_e4("1.0000") is None and ks.ask_e4(None) is None and ks.ask_e4(1) is None
    assert ks.bid_e4("0.4200") == 4200 and ks.ask_e4("0.4300") == 4300
    assert ks.bid_e4("0.0100") == 100 and ks.ask_e4("0.9900") == 9900


def test_quote_row_historical_and_live_formats():
    hist = {"end_period_ts": 1765926420, "volume": "2.00",
            "yes_bid": {"close": "0.0100"}, "yes_ask": {"close": "0.9900"}}
    live = {"end_period_ts": 1765926480, "volume_fp": "3.00",
            "yes_bid": {"close_dollars": "0.0000"}, "yes_ask": {"close_dollars": "1.0000"}}
    assert ks.quote_row(7, hist) == (7, 1765926420, 100, 9900, 2.0)
    assert ks.quote_row(7, live) == (7, 1765926480, None, None, 3.0)
    assert ks.quote_row(7, {"end_period_ts": 60})[2:] == (None, None, None)


def test_game_window_and_market_window():
    ms = [{"kind": "spread", "open_ts": 1000, "close_ts": 9000},
          {"kind": "spread", "open_ts": 1130, "close_ts": 9500},
          {"kind": "win", "open_ts": 100, "close_ts": 12000}]
    win = ks.game_window(ms)
    assert win == (960, 12000)          # floor of the earliest SPREAD open; latest close of any market
    assert ks.market_window(win, 100, 12000) == (960, 12000)       # winner opened earlier: clipped to window start
    assert ks.market_window(win, 1130, 9500) == (1080, 9500)
    assert ks.market_window(win, 20000, 21000) is None
    assert ks.game_window([{"kind": "win", "open_ts": 1, "close_ts": 2}]) is None
