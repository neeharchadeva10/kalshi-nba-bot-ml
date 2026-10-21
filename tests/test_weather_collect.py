import json
import sqlite3
from importlib import resources
from zoneinfo import ZoneInfo

import pytest

from kpm.collectors import kalshi_weather as kw


def test_event_day():
    assert kw.parse_event_day("KXHIGHNY-26OCT14") == "2026-10-14"
    assert kw.parse_event_day("KXLOWTCHI-26FEB27") == "2026-02-27"
    assert kw.parse_event_day("KXHIGHHOU-25JAN28") == "2025-01-28"
    assert kw.parse_event_day("KXCITIESWEATHER-25MAR04(CHI)(DEN)(PHIL)") == "2025-03-04"
    assert kw.parse_event_day("KXHIGHNY-26XYZ14") is None
    assert kw.parse_event_day("KXHIGHNY-26FEB30") is None
    assert kw.parse_event_day("KXHIGHNY") is None


def test_source_both_wordings():
    twc = ("If the maximum temperature recorded at New York City (CLINYC) for Oct 14, 2026, is greater than 85° "
           "fahrenheit according to The Weather Company, then the market resolves to Yes.")
    nws = ("If the highest temperature recorded in Central Park, New York for August 12, 2026 as reported by the "
           "National Weather Service's Climatological Report (Daily), is greater than 90Â°, then the market "
           "resolves to Yes.")
    assert kw.parse_source(twc) == "TWC"
    assert kw.parse_source(nws) == "NWS"
    assert kw.parse_source("something else entirely", "see the exchange") == "OTHER"
    assert kw.parse_source(None, None) == "OTHER"
    assert kw.parse_source("no source named", "as reported by the Weather Company") == "TWC"
    assert kw.parse_station(twc) == "CLINYC"
    assert kw.parse_station(nws) is None


def test_strikes():
    assert kw.parse_strikes({"strike_type": "between", "floor_strike": 84, "cap_strike": 85}) == ("between", 84.0, 85.0, "api")
    assert kw.parse_strikes({"strike_type": "greater", "floor_strike": 85}) == ("greater", 85.0, None, "api")
    assert kw.parse_strikes({"strike_type": "less", "cap_strike": 78}) == ("less", None, 78.0, "api")
    # old markets: no strike fields, parse the rules text (mis-encoded degree sign tolerated)
    old = {"ticker": "X"}
    assert kw.parse_strikes({**old, "rules_primary": "... is between 70-71�, then the market resolves to Yes."})         == ("between", 70.0, 71.0, "rules")
    assert kw.parse_strikes({**old, "rules_primary": "... is strictly greater than 87Â°, then ..."})         == ("greater", 87.0, None, "rules")
    assert kw.parse_strikes({**old, "rules_primary": "... is below 78°, then ..."}) == ("less", None, 78.0, "rules")
    assert kw.parse_strikes({**old, "rules_primary": "nothing"}) == (None, None, None, "none")
    with pytest.raises(ValueError):
        kw.parse_strikes({"ticker": "X", "strike_type": "between", "floor_strike": 84})
    with pytest.raises(ValueError):
        kw.parse_strikes({"ticker": "X", "strike_type": "less"})


def test_quote_row_null_for_empty_sides():
    hist = {"end_period_ts": 1000, "volume": "2.00",
            "yes_bid": {"open": "0.0000", "close": "0.0400"}, "yes_ask": {"open": "0.0600", "close": "1.0000"}}
    assert kw.quote_row(7, hist) == (7, 1000, 400, None, None, 600, 2.0)
    live = {"end_period_ts": 1060, "volume_fp": "0.00",
            "yes_bid": {"open_dollars": "0.0400", "close_dollars": "0.0000"},
            "yes_ask": {"open_dollars": "1.0000", "close_dollars": "0.0500"}}
    assert kw.quote_row(7, live) == (7, 1060, None, 500, 400, None, 0.0)
    assert kw.quote_row(7, {"end_period_ts": 5}) == (7, 5, None, None, None, None, None)


def test_mapping_missing_fails_loudly():
    s = [{"ticker": "KXHIGHTZZZZ", "title": "Daily high temp Atlantis", "frequency": "daily"},
         {"ticker": "KXLOWTYYYY", "title": "Daily low temp Mu", "frequency": "daily"},
         {"ticker": "KXHIGHNY", "title": "Highest temperature in NYC", "frequency": "daily"}]
    with pytest.raises(kw.WeatherMappingError) as e:
        kw.select_series(s)
    assert "KXHIGHTZZZZ" in str(e.value) and "KXLOWTYYYY" in str(e.value) and "KXHIGHNY" not in str(e.value)


def test_scope_selection():
    s = [{"ticker": "KXHIGHNY", "title": "Highest temperature in NYC", "frequency": "daily", "fee_type": "quadratic"},
         {"ticker": "KXLOWTPHX", "title": "Low Temperature Phoenix", "frequency": "daily"},
         {"ticker": "KXHIGHNYD", "title": "Hourly Directional NYC Temperature", "frequency": "hourly"},
         {"ticker": "KXRAINNYC", "title": "NYC rain", "frequency": "daily"},
         {"ticker": "KXHIGHUS", "title": "High temp in United States", "frequency": "daily"},
         {"ticker": "KXMINNYC", "title": "Min NYC temp", "frequency": "monthly"}]
    inc, exc = kw.select_series(s)
    assert {(x["ticker"], x["kind"], x["tz"]) for x in inc} == {
        ("KXHIGHNY", "high", "America/New_York"), ("KXLOWTPHX", "low", "America/Phoenix")}
    assert {x["ticker"] for x in exc} == {"KXHIGHNYD", "KXRAINNYC", "KXHIGHUS", "KXMINNYC"}


def test_mapping_table_is_consistent():
    for t, key in kw.SERIES_CITY.items():
        assert key in kw.CITIES, t
    for name, tz in kw.CITIES.values():
        ZoneInfo(tz)


def test_market_row_and_schema_roundtrip():
    m = {"ticker": "KXHIGHNY-26OCT14-B84.5", "event_ticker": "KXHIGHNY-26OCT14", "strike_type": "between",
         "floor_strike": 84, "cap_strike": 85, "open_time": "2026-10-13T14:00:00Z",
         "close_time": "2026-10-15T05:00:00Z", "settlement_ts": "2026-10-15T11:00:00.5Z", "result": "yes",
         "volume_fp": "123.50", "rules_primary": "... according to The Weather Company, then ...",
         "rules_secondary": "x"}
    series = {"ticker": "KXHIGHNY", "kind": "high", "city": "New York City (Central Park)", "tz": "America/New_York"}
    row = kw.market_row(m, "live", series)
    assert row["day"] == "2026-10-14" and row["source"] == "TWC" and row["volume"] == 123.5
    assert "rules_primary" not in json.loads(row["raw_json"])
    conn = sqlite3.connect(":memory:")
    conn.executescript(resources.files("kpm.core").joinpath("weather_schema.sql").read_text(encoding="utf-8"))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(raw_responses)")]
    assert cols == ["request_hash", "url", "params", "path", "http_status", "fetched_ts"]
    from kpm.core import db
    db.upsert(conn, "wx_markets", row, ["ticker"])
    mid = conn.execute("SELECT mid FROM wx_markets").fetchone()[0]
    conn.executemany("INSERT OR REPLACE INTO wx_quotes VALUES (?,?,?,?,?,?,?)",
                     [kw.quote_row(mid, {"end_period_ts": 60, "yes_bid": {"open": "0", "close": "0.1"},
                                         "yes_ask": {"open": "1", "close": "1"}, "volume": "1"})])
    assert conn.execute("SELECT bid_c, ask_c, bid_o, ask_o FROM wx_quotes").fetchone() == (1000, None, None, None)
