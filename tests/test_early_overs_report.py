import importlib.util
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("early_overs_report", ROOT / "scripts" / "early_overs_report.py")
R = importlib.util.module_from_spec(spec)
sys.modules["early_overs_report"] = R
spec.loader.exec_module(R)

ET = ZoneInfo("America/New_York")
N21 = date(2026, 10, 21)


def et(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=ET)


class FakeNotifier:
    class NotifyError(Exception):
        pass

    def __init__(self):
        self.logged = []

    def log_sent(self, kind, text, now, path=None):
        self.logged.append((kind, text))


class Api:
    """Fake public API: markets {ticker: (floor_strike, result)}, scores {game: (away, home)}."""

    def __init__(self, markets, scores, starts=None):
        self.markets, self.scores, self.calls = markets, scores, []
        self.starts = starts or {}

    def __call__(self, path, params=None):
        self.calls.append(path)
        if path.startswith("/markets/"):
            t = path.split("/")[-1]
            if t not in self.markets:
                raise RuntimeError("404")
            fs, res = self.markets[t]
            return {"market": {"floor_strike": fs, "result": res}}
        if path == "/milestones":
            return {"milestones": [{"type": "basketball_game", "id": "m_" + g,
                                    "related_event_tickers": ["KXNBATOTAL-" + g],
                                    "start_date": self.starts.get(g, "2026-10-22T00:00:00Z")} for g in self.scores],
                    "cursor": ""}
        if path.startswith("/live_data/basketball_game/milestone/"):
            g = path.split("/m_")[-1]
            a, h = self.scores[g]
            return {"live_data": {"details": {"away_points": a, "home_points": h, "status": "closed"}}}
        raise AssertionError(path)


def setup(tmp_path, picks):
    p = tmp_path / "n.json"
    p.write_text(json.dumps(picks))
    return dict(notified_path=p, reported_path=tmp_path / "rep.json", cache_path=tmp_path / "cache.json",
                log_path=tmp_path / "l.log")


PICKS = {
    "2026-10-21|26OCT21MINMIA": {"side": "UNDER", "strike": "241.5", "limit_c": 52, "sent": "x"},
    "2026-10-21|26OCT21CHABKN": {"side": "OVER", "strike": "219.5", "limit_c": 51, "sent": "x"},
    "2026-10-21|SCHEDULE": "2026-10-21T21:00:00+00:00",
}
MK = {"KXNBATOTAL-26OCT21MINMIA-242": (241.5, "no"), "KXNBATOTAL-26OCT21CHABKN-220": (219.5, "no")}
SC = {"26OCT21MINMIA": (131, 106), "26OCT21CHABKN": (110, 103)}
STARTS = {"26OCT21MINMIA": "2026-10-21T23:00:00Z", "26OCT21CHABKN": "2026-10-21T23:30:00Z"}


@pytest.mark.parametrize("side,res,exp", [("OVER", "yes", True), ("OVER", "no", False), ("UNDER", "no", True),
                                          ("UNDER", "yes", False), ("OVER", "", None), ("UNDER", "scalar", None)])
def test_pick_won(side, res, exp):
    assert R.pick_won(side, res) is exp


def test_ticker_from_strike():
    assert R.market_ticker("26OCT21MINMIA", "241.5") == "KXNBATOTAL-26OCT21MINMIA-242"
    assert R.market_ticker("26OCT21CHABKN", 219.5).endswith("-220")
    assert R.market_ticker("26OCT21SACLAC", "226.5").endswith("-227")


def test_nba_night():
    assert R.nba_night(et(2026, 10, 22, 1, 0)) == date(2026, 10, 21)
    assert R.nba_night(et(2026, 10, 22, 5, 59)) == date(2026, 10, 21)
    assert R.nba_night(et(2026, 10, 22, 6, 0)) == date(2026, 10, 22)
    assert R.nba_night(et(2026, 10, 21, 23, 0)) == date(2026, 10, 21)
    assert R.nba_night(datetime(2026, 10, 22, 4, 0, tzinfo=timezone.utc)) == date(2026, 10, 21)


def test_window_boundaries():
    assert not R.in_window(date(2026, 10, 20))
    assert R.in_window(date(2026, 10, 21)) and R.in_window(date(2026, 11, 3))
    assert not R.in_window(date(2026, 11, 4))


def test_final_window():
    assert not R.in_final_window(et(2026, 10, 22, 1, 44))
    assert R.in_final_window(et(2026, 10, 22, 1, 45))
    assert R.in_final_window(et(2026, 10, 22, 5, 59))
    assert not R.in_final_window(et(2026, 10, 22, 6, 0))
    assert not R.in_final_window(et(2026, 10, 21, 23, 15))


def test_parse_picks_ignores_schedule():
    assert [p[1] for p in R.parse_picks(PICKS)] == ["26OCT21CHABKN", "26OCT21MINMIA"]


def test_message_format_ordering_and_no_dollar(tmp_path):
    api = Api(MK, SC, STARTS)
    kw = setup(tmp_path, PICKS)
    sent = []
    st, text = R.run(N21, et(2026, 10, 22, 0, 30), api, lambda u, t: sent.append(t), False,
                     webhook_loader=lambda: "u", notifier=FakeNotifier(), **kw)
    assert st == "sent" and sent == [text]
    assert text.splitlines() == [
        "NBA totals — Wed Oct 21 results",
        "✅ MIN @ MIA · UNDER 241.5 · final 237 (131–106)",
        "❌ CHA @ BKN · OVER 219.5 · final 213 (110–103)",
        "Tonight: 1–1",
        "2-week record (Oct 21–Nov 3): 1–1"]
    assert "$" not in text and len(text) < 1900
    assert all(c.startswith(("/markets/", "/milestones", "/live_data/")) for c in api.calls)


def test_pending_waits_then_forced_in_final_window(tmp_path):
    mk = dict(MK)
    mk["KXNBATOTAL-26OCT21CHABKN-220"] = (219.5, "")
    api = Api(mk, SC, STARTS)
    kw = setup(tmp_path, PICKS)
    sent = []
    args = dict(webhook_loader=lambda: "u", notifier=FakeNotifier(), **kw)
    st, _ = R.run(N21, et(2026, 10, 22, 0, 30), api, lambda u, t: sent.append(t), False, **args)
    assert st == "waiting" and sent == [] and not (tmp_path / "rep.json").exists()
    st, text = R.run(N21, et(2026, 10, 22, 1, 45), api, lambda u, t: sent.append(t), False, **args)
    assert st == "sent" and len(sent) == 1
    lines = text.splitlines()
    assert "⏳ CHA @ BKN · OVER 219.5 · pending" in lines
    assert "Tonight: 1–0" in lines and lines[-1] == "(pending results will count in the next report)"
    assert "$" not in text


def test_send_once(tmp_path):
    api = Api(MK, SC, STARTS)
    kw = setup(tmp_path, PICKS)
    sent = []
    args = dict(webhook_loader=lambda: "u", notifier=FakeNotifier(), **kw)
    assert R.run(N21, et(2026, 10, 22, 0, 30), api, lambda u, t: sent.append(t), False, **args)[0] == "sent"
    assert "2026-10-21" in json.loads((tmp_path / "rep.json").read_text())
    assert R.run(N21, et(2026, 10, 22, 0, 45), api, lambda u, t: sent.append(t), False, **args)[0] == "already-sent"
    assert len(sent) == 1
    assert args["notifier"].logged[0][0] == "report"


def test_dry_run_sends_and_writes_nothing(tmp_path):
    api = Api(MK, SC, STARTS)
    kw = setup(tmp_path, PICKS)
    sent = []
    st, text = R.run(N21, et(2026, 10, 22, 0, 30), api, lambda u, t: sent.append(t), True,
                     notifier=FakeNotifier(), **kw)
    assert st == "dry-run" and sent == [] and not (tmp_path / "rep.json").exists() and "Tonight" in text


def test_no_picks_message(tmp_path):
    kw = setup(tmp_path, {"2026-10-21|SCHEDULE": "x"})
    sent = []
    st, text = R.run(N21, et(2026, 10, 22, 0, 30), Api({}, {}), lambda u, t: sent.append(t), False,
                     webhook_loader=lambda: "u", notifier=FakeNotifier(), **kw)
    assert st == "sent"
    assert text == "NBA totals — Wed Oct 21: no picks tonight (no game was 4+ points off the line)."
    assert "$" not in text


def test_two_week_record_counts_other_nights_and_window(tmp_path):
    picks = dict(PICKS)
    picks["2026-10-22|26OCT22AAABBB"] = {"side": "OVER", "strike": "200.5", "limit_c": 50, "sent": "x"}
    picks["2026-10-20|26OCT20XXXYYY"] = {"side": "OVER", "strike": "200.5", "limit_c": 50, "sent": "x"}
    mk = dict(MK)
    mk["KXNBATOTAL-26OCT22AAABBB-201"] = (200.5, "yes")
    mk["KXNBATOTAL-26OCT20XXXYYY-201"] = (200.5, "yes")
    api = Api(mk, {**SC, "26OCT22AAABBB": (100, 110)}, STARTS)
    kw = setup(tmp_path, picks)
    st, text = R.run(date(2026, 10, 22), et(2026, 10, 23, 0, 30), api, lambda u, t: None, True,
                     notifier=FakeNotifier(), **kw)
    # Oct 22 tonight 1-0; 2-week = Oct 21 (1-1) + Oct 22 (1-0) = 2-1; the Oct 20 pick is outside the window
    assert "Tonight: 1–0" in text and "2-week record (Oct 21–Nov 3): 2–1" in text
    assert not any("26OCT20" in c for c in api.calls)


def test_cache_prevents_refetch(tmp_path):
    api = Api(MK, SC, STARTS)
    kw = setup(tmp_path, PICKS)
    R.run(N21, et(2026, 10, 22, 0, 30), api, lambda u, t: None, True, notifier=FakeNotifier(), **kw)
    cached = json.loads((tmp_path / "cache.json").read_text())
    assert cached["2026-10-21|26OCT21MINMIA"]["result"] == "no"
    api2 = Api({}, {})
    st, text = R.run(N21, et(2026, 10, 22, 0, 40), api2, lambda u, t: None, True, notifier=FakeNotifier(), **kw)
    assert api2.calls == [] and "final 237" in text


def test_strike_mismatch_is_pending(tmp_path, capsys):
    mk = {"KXNBATOTAL-26OCT21MINMIA-242": (240.5, "no"), "KXNBATOTAL-26OCT21CHABKN-220": (219.5, "no")}
    kw = setup(tmp_path, PICKS)
    st, text = R.run(N21, et(2026, 10, 22, 0, 30), Api(mk, SC, STARTS), lambda u, t: None, True,
                     notifier=FakeNotifier(), **kw)
    assert "⏳ MIN @ MIA" in text and "floor_strike" in capsys.readouterr().err
    assert "26OCT21MINMIA" not in json.loads((tmp_path / "cache.json").read_text())


def test_unsettled_not_cached(tmp_path):
    api = Api({"KXNBATOTAL-26OCT21MINMIA-242": (241.5, ""), "KXNBATOTAL-26OCT21CHABKN-220": (219.5, "")}, SC)
    kw = setup(tmp_path, PICKS)
    R.run(N21, et(2026, 10, 22, 0, 30), api, lambda u, t: None, True, notifier=FakeNotifier(), **kw)
    assert json.loads((tmp_path / "cache.json").read_text()) == {}
