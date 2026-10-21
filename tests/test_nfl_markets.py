import json

from kpm.collectors import nfl_markets


class FakeHttp:
    def __init__(self):
        self.calls = []

    def get(self, path, params=None, use_cache=True):
        self.calls.append((path, params))
        if path == "/series":
            return {"series": [{"ticker": "KXNFLGAME"}, {"ticker": "KXNFLSPREAD"}, {"ticker": "KXNFLANYTD"}, {"ticker": "KXNBAGAME"}]}
        assert path == "/events"
        if params["series_ticker"] == "KXNFLGAME":
            if params.get("cursor") is None:
                return {"cursor": "c2", "events": [
                    {"event_ticker": "KXNFLGAME-26OCT22AAABBB", "markets": [
                        {"ticker": "KXNFLGAME-26OCT22AAABBB-AAA", "status": "active"},
                        {"ticker": "KXNFLGAME-26OCT22AAABBB-BBB", "status": "closed"}]}]}
            return {"events": [{"event_ticker": "KXNFLGAME-26OCT25CCCDDD", "markets": [
                {"ticker": "x", "status": "active"}]}]}
        if params["series_ticker"] == "KXNFLANYTD":
            return {"events": [{"event_ticker": "KXNFLANYTD-26OCT22AAABBB-10", "markets": [
                {"ticker": "KXNFLANYTD-26OCT22AAABBB-10-P1", "status": "active"}]}]}
        return {"events": [{"event_ticker": "KXNFLSPREAD-26OCT22AAABBB", "markets": [
            {"ticker": "KXNFLSPREAD-26OCT22AAABBB-AAA3", "status": "open"}]}]}


class FakeClient:
    def __init__(self):
        self.http = FakeHttp()


def test_discover_groups_by_game_and_filters():
    doc = nfl_markets.discover(FakeClient(), "26OCT22")
    assert list(doc["games"]) == ["26OCT22AAABBB"]
    g = doc["games"]["26OCT22AAABBB"]
    assert g["series"] == ["KXNFLANYTD", "KXNFLGAME", "KXNFLSPREAD"]
    assert sorted(g["tickers"]) == ["KXNFLANYTD-26OCT22AAABBB-10-P1", "KXNFLGAME-26OCT22AAABBB-AAA",
                                    "KXNFLSPREAD-26OCT22AAABBB-AAA3"]      # sub-event -10 lands under the game


def test_run_writes_file(tmp_path):
    out = nfl_markets.run("26OCT22", client=FakeClient(), out_root=tmp_path)
    assert json.loads(out.read_text())["date"] == "26OCT22"


def test_bad_date():
    import pytest
    with pytest.raises(ValueError):
        nfl_markets.discover(FakeClient(), "2026-10-22")


def test_discover_saves_sim_meta_and_kickoff():
    class H(FakeHttp):
        def get(self, path, params=None, use_cache=True):
            r = super().get(path, params, use_cache)
            if path == "/events" and params["series_ticker"] == "KXNFLGAME" and params.get("cursor") is None:
                r["events"][0]["markets"][0].update(yes_sub_title="AAA wins", floor_strike=None,
                                                    expected_expiration_time="2026-10-22T03:15:00Z", occurrence_datetime="2026-10-22T03:15:00Z")
            if path == "/events" and params["series_ticker"] == "KXNFLSPREAD":
                r["events"][0]["markets"][0].update(floor_strike=2.5)
            return r

    class C:
        http = H()

    g = nfl_markets.discover(C(), "26OCT22")["games"]["26OCT22AAABBB"]
    assert g["kickoff"] == "2026-10-22T00:15:00Z"          # expected end - 3 h
    assert g["meta"]["KXNFLSPREAD-26OCT22AAABBB-AAA3"] == {"floor_strike": 2.5}
    assert g["meta"]["KXNFLGAME-26OCT22AAABBB-AAA"] == {"yes_sub_title": "AAA wins"}
    assert "KXNFLANYTD-26OCT22AAABBB-10-P1" not in g["meta"]


def test_kickoff_falls_back_to_occurrence_minus_3h():
    assert nfl_markets.kickoff_from([{"occurrence_datetime": "2026-10-19T03:15:00Z"}]) == "2026-10-19T00:15:00Z"
    assert nfl_markets.kickoff_from([{}]) is None


class NbaHttp:
    def __init__(self):
        self.series_asked = []

    def get(self, path, params=None, use_cache=True):
        if path == "/series":
            return {"series": [{"ticker": "KXNBAGAME"}, {"ticker": "KXNBAWINMARGIN"}, {"ticker": "KXNFLGAME"}]}
        self.series_asked.append(params["series_ticker"])
        mk = {"KXNBAGAME": {"ticker": "KXNBAGAME-26OCT20AAABBB-AAA", "status": "active",
                            "expected_expiration_time": "2026-10-21T03:00:00Z", "occurrence_datetime": "2026-10-21T02:55:00Z"},
              "KXNBAWINMARGIN": {"ticker": "KXNBAWINMARGIN-26OCT20AAABBB-AAA1TO5", "status": "active",
                                 "yes_sub_title": "AAA by 1-5", "floor_strike": 1}}[params["series_ticker"]]
        return {"events": [{"event_ticker": mk["ticker"].rsplit("-", 1)[0], "markets": [mk]}]}


def test_other_prefix_filters_series_keeps_raw_kickoff_fields_and_meta():
    h = NbaHttp()

    class C:
        http = h
    g = nfl_markets.discover(C(), "26OCT20", "KXNBA")["games"]["26OCT20AAABBB"]
    assert sorted(h.series_asked) == ["KXNBAGAME", "KXNBAWINMARGIN"]
    assert g["kickoff"] is None and g["kickoff_raw"]["expected_expiration_time"] == "2026-10-21T03:00:00Z"
    assert g["meta"]["KXNBAWINMARGIN-26OCT20AAABBB-AAA1TO5"] == {"yes_sub_title": "AAA by 1-5", "floor_strike": 1}


def test_output_path_per_sport(tmp_path):
    assert nfl_markets.out_path("26OCT22", "KXNFL", tmp_path) == tmp_path / "26OCT22" / "markets.json"
    assert nfl_markets.out_path("26OCT20", "KXNBA", tmp_path) == tmp_path / "nba" / "26OCT20" / "markets.json"
