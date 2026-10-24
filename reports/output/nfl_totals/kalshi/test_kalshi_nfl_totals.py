"""Unit tests for download_kalshi_nfl_totals.py (no network)."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import download_kalshi_nfl_totals as d  # noqa: E402


def games_df():
    rows = [
        # game_id, season, week, gameday, gametime, away, home
        ("2026_06_ARI_LA", 2026, 6, "2026-10-18", "16:05", "ARI", "LA", 20, 24, 0),
        ("2026_06_HOU_JAX", 2026, 6, "2026-10-18", "09:30", "HOU", "JAX", 10, 17, 0),
        ("2026_07_NE_CHI", 2026, 7, "2026-10-22", "20:15", "NE", "CHI", 27, 30, 1),
        ("2026_10_WAS_SF", 2026, 10, "2026-11-08", "13:00", "WAS", "SF", 14, 21, 0),
        ("2026_01_LAC_KC", 2026, 1, "2026-09-13", "", "LAC", "KC", 14, 21, 0),
    ]
    g = pd.DataFrame(rows, columns=["game_id", "season", "week", "gameday", "gametime", "away_team", "home_team",
                                    "away_score", "home_score", "overtime"])
    g["game_type"] = "REG"
    g["kickoff_utc"] = [d.kickoff_utc(a, b) for a, b in zip(g["gameday"], g["gametime"])]
    return g


NFL = {"ARI", "LA", "HOU", "JAX", "NE", "CHI", "WAS", "SF", "LAC", "KC"}


def test_team_map_aliases():
    tmap, bad = d.build_team_map({"JAC", "LAR", "WSH", "WAS", "LA", "KC"}, NFL)
    assert tmap == {"JAC": "JAX", "LAR": "LA", "WSH": "WAS", "WAS": "WAS", "LA": "LA", "KC": "KC"}
    assert bad == []
    _, bad = d.build_team_map({"XYZ"}, NFL)
    assert bad == ["XYZ"]


def test_parse_event_and_split():
    pe = d.parse_event("KXNFLTOTAL-26OCT22NECHI")
    assert pe["gameday"] == "2026-10-22" and pe["block"] == "NECHI" and pe["game_key"] == "26OCT22NECHI"
    assert d.parse_event("garbage") is None
    valid = {"LAC", "KC", "LA", "NE", "CHI", "JAC", "HOU"}
    assert d.split_block("LACKC", valid) == [("LAC", "KC")]       # LA|CKC is not a valid reading
    assert d.split_block("HOUJAC", valid) == [("HOU", "JAC")]


def test_kickoff_dst():
    # 2026-11-01 is the end of DST: Oct 31 13:00 ET = EDT (UTC-4), Nov 8 13:00 ET = EST (UTC-5)
    assert d.kickoff_utc("2026-10-18", "16:05") == int(datetime(2026, 10, 18, 20, 5, tzinfo=timezone.utc).timestamp())
    assert d.kickoff_utc("2026-11-08", "13:00") == int(datetime(2026, 11, 8, 18, 0, tzinfo=timezone.utc).timestamp())
    assert d.kickoff_utc("2026-03-08", "13:00") == int(datetime(2026, 3, 8, 17, 0, tzinfo=timezone.utc).timestamp())  # EDT starts
    assert d.kickoff_utc("2026-09-13", "") is None and d.kickoff_utc("2026-09-13", float("nan")) is None


def test_match_event():
    g = games_df()
    tmap, _ = d.build_team_map({"ARI", "LAR", "HOU", "JAC", "NE", "CHI", "WAS", "SF", "LAC", "KC"}, NFL)
    valid = set(tmap)
    r = d.match_event("KXNFLTOTAL-26OCT18ARILAR", g, valid, tmap)
    assert r["status"] == "matched" and r["game_id"] == "2026_06_ARI_LA" and r["order_ok"] and r["method"] == "exact_date"
    r = d.match_event("KXNFLTOTAL-26OCT18HOUJAC", g, valid, tmap)
    assert r["game_id"] == "2026_06_HOU_JAX"
    assert d.match_event("KXNFLTOTAL-26OCT22NECHI", g, valid, tmap)["kickoff_utc"] == int(
        datetime(2026, 10, 23, 0, 15, tzinfo=timezone.utc).timestamp())
    # reversed order is still matched but flagged
    r = d.match_event("KXNFLTOTAL-26OCT22CHINE", g, valid, tmap)
    assert r["status"] == "matched" and r["order_ok"] is False
    # one day off the ticker date -> fallback method recorded
    r = d.match_event("KXNFLTOTAL-26NOV09WASSF", g, valid, tmap)
    assert r["status"] == "matched" and r["method"] == "date-1"
    # unmatched: preseason, wrong teams, missing kickoff time
    assert d.match_event("KXNFLTOTAL-26AUG22DALARI", g, valid | {"DAL"}, {**tmap, "DAL": "DAL"})["status"] == "no_nflverse_game"
    assert d.match_event("junk", g, valid, tmap)["status"] == "unparsed"
    assert d.match_event("KXNFLTOTAL-26SEP13LACKC", g, valid, tmap)["kickoff_utc"] is None


def test_bracket_check_synthetic():
    strikes = [(40.5, "yes"), (43.5, "yes"), (46.5, "no"), (49.5, "no")]
    assert d.bracket_check(strikes, 45)["bracket_ok"] is True        # 43.5 < 45 <= 46.5
    assert d.bracket_check(strikes, 46)["bracket_ok"] is True
    assert d.bracket_check(strikes, 43)["bracket_ok"] is False       # YES at 43.5 would be wrong
    assert d.bracket_check(strikes, 47)["bracket_ok"] is False       # NO at 46.5 would be wrong
    assert d.bracket_check([(40.5, "yes"), (43.5, "no"), (46.5, "yes")], 45)["monotonic"] is False
    assert d.bracket_check([(40.5, "yes")], 44)["bracket_ok"] is True    # one-sided
    assert d.bracket_check(strikes, None)["bracket_ok"] is None


def test_exp_value_rule():
    rows = [(40.5, "yes", "42"), (50.5, "no", "45"), (44.5, "no", "44"), (30.5, "yes", ""), (30.5, "yes", "Yes")]
    bad = d.exp_value_flags(rows, 44)
    assert sorted(b["why"] for b in bad) == ["empty", "no: != total", "non-numeric"]
    assert d.exp_value_flags([(40.5, "yes", "39")], 44)[0]["why"] == "yes: not in (floor, total]"
    assert d.exp_value_flags([(40.5, "yes", "45")], 44)[0]["why"] == "yes: not in (floor, total]"


def test_candle_windows():
    ko = 1_000_000_000
    w = d.candle_windows(ko - 20 * 86400, ko)
    assert w["1h"] == (ko - 10 * 86400, ko + 900)            # cap at kickoff - 10 days
    assert w["1m"] == (ko - 6 * 3600, ko + 900)
    w = d.candle_windows(ko - 3600, ko)
    assert w["1h"] == (ko - 3600, ko + 900)
    w = d.candle_windows(ko + 7200, ko)                      # opened after kickoff + 15 min
    assert w["1h"] is None and w["1m"] is None


def test_flatten_candle_live_and_hist_names():
    live = {"end_period_ts": 5, "volume_fp": "3.00", "open_interest_fp": "9.00",
            "yes_bid": {"close_dollars": "0.4000"}, "yes_ask": {"close_dollars": "0.4500"}, "price": {}}
    r = d.flatten_candle(live)
    assert r["yes_bid_close_f"] == 0.40 and r["yes_ask_close_f"] == 0.45 and r["volume_f"] == 3.0
    assert r["yes_bid_close_dollars"] == "0.4000"           # raw string kept
    hist = {"end_period_ts": 5, "volume": 3, "open_interest": 9, "yes_bid": {"close": 0.4}, "yes_ask": {"close": 0.45}}
    assert d.flatten_candle(hist)["yes_ask_close_f"] == 0.45


def test_two_sided():
    assert d.two_sided(0.4, 0.45) and not d.two_sided(0.0, 0.45) and not d.two_sided(0.4, 1.0)
    assert not d.two_sided(None, 0.4) and not d.two_sided(float("nan"), 0.4)


class FakeSession:
    def __init__(self):
        self.calls = 0

    def get(self, *a, **k):
        self.calls += 1
        return "resp"


class FakeHttp:
    def __init__(self):
        self._session = FakeSession()


class FakeClient:
    def __init__(self):
        self.http = FakeHttp()


def test_request_cap():
    c = FakeClient()
    b = d.RequestBudget(3)
    b.install(c)
    for _ in range(3):
        assert c.http._session.get("u") == "resp"
    with pytest.raises(d.BudgetExhausted):
        c.http._session.get("u")
    assert c.http._session.calls == 3 and b.n == 3
    # no cap
    c2 = FakeClient()
    d.RequestBudget(None).install(c2)
    for _ in range(100):
        c2.http._session.get("u")
    assert c2.http._session.calls == 100


def test_wait_until_mock_time():
    clock = {"t": 1000.0}
    slept = []

    def sleep(s):
        slept.append(s)
        clock["t"] += s

    iso = datetime.fromtimestamp(1000 + 150, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total = d.wait_until(iso, now_fn=lambda: clock["t"], sleep_fn=sleep, chunk_s=60)
    assert slept == [60, 60, 30] and total == 150
    assert d.wait_until(iso, now_fn=lambda: clock["t"], sleep_fn=sleep) == 0      # already past: no sleep
    assert d.wait_until(None, sleep_fn=lambda s: pytest.fail("slept")) == 0


def test_done_table_resume(tmp_path):
    d.mark_done(tmp_path, {"ticker": "A", "kind": "1h", "status": "ok"})
    d.mark_done(tmp_path, {"ticker": "A", "kind": "1m", "status": "empty_window"})
    done = d.load_done(tmp_path)
    assert set(done) == {"A|1h", "A|1m"}


class StubClient:
    """KalshiClient stand-in: candlesticks() returns a canned list and counts calls."""

    def __init__(self, fail_first=0):
        self.calls, self.fail = [], fail_first

    def candlesticks(self, s, t, a, b, i, h):
        self.calls.append((t, i, h))
        if self.fail > 0:
            self.fail -= 1
            raise RuntimeError("network down")
        return [{"end_period_ts": a + 3600, "yes_bid": {"close_dollars": "0.4000"}, "yes_ask": {"close_dollars": "0.4500"}}]


def test_stage_candles_resume_and_retry(tmp_path):
    ko = 1_900_000_000
    mk = pd.DataFrame([{"ticker": "T1", "series": "KXNFLTOTAL", "tier": "live", "event_ticker": "E1",
                        "open_time": datetime.fromtimestamp(ko - 86400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
                       {"ticker": "T2", "series": "KXNFLTOTAL", "tier": "live", "event_ticker": "E2",
                        "open_time": "2026-01-01T00:00:00Z"}])
    ev = pd.DataFrame([{"event_ticker": "E1", "status": "matched", "kickoff_utc": ko},
                       {"event_ticker": "E2", "status": "no_nflverse_game", "kickoff_utc": None}])
    now = ko + 86400
    c = StubClient(fail_first=1)
    st = d.stage_candles(c, tmp_path, mk, ev, now, passes=2, pass_sleep_s=0, sleep_fn=lambda s: None)
    assert st["fetched"] == 2 and st["no_kickoff"] == 1 and st["still_failed"] == 0
    n = len(c.calls)
    c2 = StubClient()
    st2 = d.stage_candles(c2, tmp_path, mk, ev, now, passes=1, pass_sleep_s=0, sleep_fn=lambda s: None)
    assert c2.calls == [] and st2["already_done"] == 2          # resumable: nothing refetched
    assert d.build_candle_parquets(tmp_path) == {"1h": 1, "1m": 1}
    # game not yet played -> nothing attempted
    (tmp_path / "x").mkdir()
    c3 = StubClient()
    st3 = d.stage_candles(c3, tmp_path / "x", mk.iloc[:1], ev, ko - 10, passes=1, pass_sleep_s=0)
    assert c3.calls == [] and st3["not_yet_played"] == 1


def test_lookahead_guard_windows_end_before_post_kick():
    """Candle windows never extend past kickoff+15 min (the stated end), starts never after the end."""
    for open_off in (-30 * 86400, -3 * 86400, -600, 0):
        ko = 1_800_000_000
        w = d.candle_windows(ko + open_off, ko)
        for k in ("1h", "1m"):
            if w[k]:
                assert w[k][1] == ko + 900 and w[k][0] < w[k][1]
