import importlib.util
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest
import requests

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("dk_snapshot", Path(__file__).parent / "dk_snapshot.py")
D = importlib.util.module_from_spec(spec)
sys.modules["dk_snapshot"] = D
spec.loader.exec_module(D)
spec2 = importlib.util.spec_from_file_location("early_overs_notify", ROOT / "scripts" / "early_overs_notify.py")
N = importlib.util.module_from_spec(spec2)
sys.modules["early_overs_notify"] = N
spec2.loader.exec_module(N)

KEY = "abcd1234SECRETKEYzzzz9876"


@pytest.fixture(autouse=True)
def _no_network_no_real_key(tmp_path, monkeypatch):
    """No test may reach the network, the real key file, or the real data/log files."""
    kp = tmp_path / "placeholder_key.txt"
    kp.write_text("PASTE_ODDS_API_KEY_HERE" + chr(10))
    monkeypatch.setattr(D, "KEY_PATH", kp)
    monkeypatch.setattr(D, "OUT_PATH", tmp_path / "out" / "snapshots.jsonl")
    monkeypatch.setattr(D, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(D, "LOG_PATH", tmp_path / "dk.log")
    monkeypatch.setenv("ODDS_API_KEY", "")

    def boom(*a, **k):
        raise AssertionError("network call attempted in a test")
    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(requests.Session, "request", boom)
    monkeypatch.setattr(N, "DK_RUNNER", lambda: None)  # notifier never loads/runs the real snapshotter


def sel(quotes):  # same rule as early_overs_picks.select_strike
    return _picks().select_strike(quotes)


def _picks():
    return D._load_picks()


def event(away="Atlanta Hawks", home="Orlando Magic", commence="2026-10-22T00:00:00Z", pt=228.5, op=-110, up=-110):
    return {"id": "x", "away_team": away, "home_team": home, "commence_time": commence,
            "bookmakers": [{"key": "draftkings", "markets": [{"key": "totals", "outcomes": [
                {"name": "Over", "price": op, "point": pt}, {"name": "Under", "price": up, "point": pt}]}]}]}


# ---- key loading / redaction
def test_load_key_skips_comments(tmp_path):
    p = tmp_path / "k.txt"
    p.write_text("# c\n\n" + KEY + "\n")
    assert D.load_key(p, env={}) == KEY


@pytest.mark.parametrize("content", ["PASTE_ODDS_API_KEY_HERE\n", "# only comment\n", ""])
def test_placeholder_refused(tmp_path, content):
    p = tmp_path / "k.txt"
    p.write_text(content)
    with pytest.raises(D.SnapshotError):
        D.load_key(p, env={})
    assert not D.key_is_set(p, env={})


def test_env_fallback(tmp_path):
    assert D.load_key(tmp_path / "none.txt", env={"ODDS_API_KEY": KEY}) == KEY


def test_redact():
    s = D.redact(f"GET https://x/odds?regions=us&apiKey={KEY}&z=1 failed {KEY}", KEY)
    assert KEY not in s and "apiKey=<redacted>" in s and "&z=1" in s


def test_fetch_exception_hides_key():
    sess = mock.Mock()
    sess.get.side_effect = requests.ConnectionError(f"boom https://h/?apiKey={KEY}")
    with pytest.raises(D.SnapshotError) as ei:
        D.fetch_odds(KEY, session=sess)
    assert KEY not in str(ei.value) and KEY not in repr(ei.value)
    assert ei.value.__cause__ is None and ei.value.__context__ is not None  # suppressed (from None)
    assert ei.value.__suppress_context__


def test_fetch_http_error_and_headers():
    sess = mock.Mock()
    sess.get.return_value = mock.Mock(status_code=401, headers={})
    with pytest.raises(D.SnapshotError) as ei:
        D.fetch_odds(KEY, session=sess)
    assert KEY not in str(ei.value)
    sess.get.return_value = mock.Mock(status_code=200, headers={"x-requests-remaining": "499", "x-requests-used": "1"},
                                      json=lambda: [event()])
    data, hdr = D.fetch_odds(KEY, session=sess)
    assert hdr == {"x-requests-remaining": "499", "x-requests-used": "1"} and len(data) == 1
    params = sess.get.call_args.kwargs["params"]
    assert params["bookmakers"] == "draftkings" and params["markets"] == "totals" and params["regions"] == "us"


def test_saved_raw_and_log_have_no_key(tmp_path):
    now = datetime(2026, 10, 21, 22, 0, tzinfo=timezone.utc)
    p = D.save_raw([event()], now, KEY, tmp_path)
    assert p.name == "20261021T220000Z.json" and KEY not in p.read_text()
    lp = tmp_path / "l.log"
    D.log(f"failed apiKey={KEY} and {KEY}", KEY, lp)
    assert KEY not in lp.read_text()


# ---- mapping / extraction
def test_team_map_clippers():
    assert D.team_code("LA Clippers") == "LAC" and D.team_code("Los Angeles Clippers") == "LAC"
    assert D.team_code("Nope") is None


def test_kalshi_key_mapping_et_date():
    # 00:00Z Oct 22 = 8pm ET Oct 21
    assert D.kalshi_key_for(event(), ["26OCT21ATLORL", "26OCT22ATLORL"]) == "26OCT21ATLORL"
    assert D.kalshi_key_for(event(away="LA Clippers", home="Los Angeles Lakers"), ["26OCT21LACLAL"]) == "26OCT21LACLAL"
    assert D.kalshi_key_for(event(), ["26OCT21ORLATL"]) is None  # home/away swapped
    assert D.kalshi_key_for(event(away="Unknown FC"), ["26OCT21ATLORL"]) is None


def test_dk_totals():
    assert D.dk_totals(event(pt=227.5, op=-105, up=-115)) == {"point": 227.5, "over_price": -105, "under_price": -115}
    e = event()
    e["bookmakers"][0]["key"] = "fanduel"
    assert D.dk_totals(e) is None
    assert D.dk_totals({"bookmakers": []}) is None


# ---- lines
def q(strike, bid, ask):
    return {"strike": strike, "ticker": f"T-{strike}", "bid": bid, "ask": ask}


QUOTES = [q(222.5, 80, 84), q(225.5, 66, 70), q(228.5, 52, 56), q(231.5, 38, 42), q(234.5, 20, 24), q(237.5, 5, 9)]


def test_main_strike_and_median():
    main, med = D.kalshi_lines(QUOTES, sel)
    assert main["strike"] == 228.5  # mid .54 closest to .50
    # mids: 228.5 -> .54, 231.5 -> .40: crossing at 228.5 + (.04/.14)*3
    assert med == pytest.approx(228.5 + 0.04 / 0.14 * 3)
    assert D.market_median([(1, 0.3), (2, 0.2)]) is None  # never at/above .5


def test_main_strike_tie_lower_and_spread_filter():
    m, _ = D.kalshi_lines([q(200.5, 48, 52), q(203.5, 48, 52)], sel)
    assert m["strike"] == 200.5
    m, _ = D.kalshi_lines([q(200.5, 40, 60), q(203.5, 30, 38)], sel)  # first has 20c spread
    assert m["strike"] == 203.5


def test_gap_side_logic():
    main = q(228.5, 52, 56)
    d = D.decide(231.0, 228.5 + 0.04 / 0.14 * 3 - 0.0, main)  # median ~229.36 -> gap 1.64, no bet
    assert not d["would_bet"] and d["side"] == "YES"
    d = D.decide(231.5, 229.0, main)
    assert d["gap"] == 2.5 and d["would_bet"] and d["side"] == "YES" and (d["side_bid"], d["side_ask"]) == (52, 56)
    d = D.decide(226.5, 229.0, main)
    assert d["would_bet"] and d["side"] == "NO" and (d["side_bid"], d["side_ask"]) == (44, 48)
    d = D.decide(231.0, 229.0, main)  # exactly 2.0 qualifies
    assert d["would_bet"]
    assert D.decide(None, 229.0, main)["would_bet"] is False and D.decide(230.0, None, main)["gap"] is None


def test_build_row_no_look_ahead_fields():
    now = datetime(2026, 10, 21, 22, 0, tzinfo=timezone.utc)
    tip = now + timedelta(minutes=70)
    r = D.build_row(now, date(2026, 10, 21), "26OCT21ATLORL", tip, "x", D.dk_totals(event(pt=232.5)), QUOTES, sel)
    assert r["minutes_to_tip"] == 70.0 and r["in_window"] and r["would_bet"] and r["side"] == "YES"
    assert r["kalshi_main_ticker"] == "T-228.5"
    assert not any(k in r for k in ("outcome", "actual", "result", "won"))


# ---- one call per run, tipped games skipped, notifier unaffected
class FakePicks:
    select_strike = staticmethod(lambda qs: sel(qs))


def test_run_snapshot_one_call_and_rows(tmp_path):
    kp = tmp_path / "k.txt"
    kp.write_text(KEY + "\n")
    now = datetime(2026, 10, 21, 22, 0, tzinfo=timezone.utc)
    tip_future, tip_past = now + timedelta(minutes=60), now - timedelta(minutes=5)
    games = [(tip_future, "26OCT21ATLORL", ["m"]), (tip_past, "26OCT21BOSNYK", ["m"])]
    events = [event(), event("Boston Celtics", "New York Knicks")]
    fetch = mock.Mock(return_value=(events, {"x-requests-remaining": "10", "x-requests-used": "1"}))
    out = tmp_path / "s.jsonl"
    with mock.patch.object(D, "kalshi_games", return_value=games), \
            mock.patch.object(D, "read_quotes", return_value=QUOTES), \
            mock.patch.object(D, "save_raw") as sr, mock.patch.object(D, "log"):
        rows, hdr = D.run_snapshot(now, date(2026, 10, 21), out_path=out, key_path=kp, fetch=fetch, picks=FakePicks)
    assert fetch.call_count == 1 and sr.call_count == 1
    assert [r["game"] for r in rows] == ["26OCT21ATLORL"]
    lines = out.read_text().splitlines()
    assert len(lines) == 1 and KEY not in lines[0] and json.loads(lines[0])["dk_point"] == 228.5
    assert hdr["x-requests-remaining"] == "10"


def test_dry_run_makes_no_call(tmp_path, capsys):
    fetch = mock.Mock()
    with mock.patch.object(D, "latest_sample", return_value=None):
        rows, _ = D.run_snapshot(dry_run=True, fetch=fetch, out_path=tmp_path / "s.jsonl")
    assert rows == [] and fetch.call_count == 0 and not (tmp_path / "s.jsonl").exists()
    assert "apiKey=<redacted>" in capsys.readouterr().out


def test_safe_snapshot_never_raises_or_leaks(tmp_path):
    lp = tmp_path / "l.log"
    with mock.patch.object(D, "key_is_set", return_value=True), mock.patch.object(D, "load_key", return_value=KEY), \
            mock.patch.object(D, "run_snapshot", side_effect=RuntimeError(f"bad apiKey={KEY}")), \
            mock.patch.object(D, "LOG_PATH", lp):
        D.safe_snapshot()
    assert "failed" in lp.read_text() and KEY not in lp.read_text()


def test_safe_snapshot_no_key_is_noop():
    with mock.patch.object(D, "key_is_set", return_value=False), mock.patch.object(D, "run_snapshot") as rs:
        D.safe_snapshot()
    assert rs.call_count == 0


def test_notifier_unaffected_if_snapshot_raises(monkeypatch):
    def bad():
        raise RuntimeError("x")
    monkeypatch.setattr(N, "DK_RUNNER", bad)
    t = N.start_dk_snapshot()
    t.join(10)
    assert not t.is_alive()
    monkeypatch.setattr(N, "DK_RUNNER", None)
    with mock.patch.object(importlib.util, "spec_from_file_location", side_effect=RuntimeError("x")):
        assert N.start_dk_snapshot() is None  # loader failure swallowed


def test_safe_snapshot_with_placeholder_makes_no_call():
    with mock.patch.object(D, "fetch_odds") as f:
        D.safe_snapshot()
    assert f.call_count == 0


def test_notifier_main_pings_sent_and_one_snapshot_per_run(tmp_path):
    ET = N.ZoneInfo("America/New_York") if hasattr(N, "ZoneInfo") else None
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
    now = datetime(2026, 10, 21, 18, 0, tzinfo=ET)
    msgs = [("d|k1", "pick", "a"), ("d|k2", "pick", "b")]
    fake_picks = mock.Mock(ET=ET)
    fake_picks.compute_rows.return_value = ([], 0, [], None)
    calls = []

    class T:
        def join(self, timeout=None):
            calls.append("join")

    with mock.patch.object(N, "_load_picks", return_value=fake_picks), \
            mock.patch.object(N, "load_webhook", return_value="https://discord.com/api/webhooks/1/x"), \
            mock.patch.object(N, "plan_messages", return_value=msgs), mock.patch.object(N, "load_state", return_value={}), \
            mock.patch.object(N, "save_state"), mock.patch.object(N, "log_sent"), \
            mock.patch.object(N, "pick_info", return_value={}), \
            mock.patch.object(N, "post_discord", side_effect=lambda u, t: calls.append("ping")), \
            mock.patch.object(N, "start_dk_snapshot", side_effect=lambda: calls.append("snap") or T()) as ss:
        fake_picks.compute_rows.return_value = ([{"game": "k1"}, {"game": "k2"}], 0, [], None)
        rc = N.main([])
    assert rc == 0 and ss.call_count == 1
    assert calls == ["snap", "ping", "ping", "join"]  # one snapshot, started before pings, joined after them
    # snapshot failure to start does not stop pings
    calls.clear()
    with mock.patch.object(N, "_load_picks", return_value=fake_picks), \
            mock.patch.object(N, "load_webhook", return_value="https://discord.com/api/webhooks/1/x"), \
            mock.patch.object(N, "plan_messages", return_value=msgs), mock.patch.object(N, "load_state", return_value={}), \
            mock.patch.object(N, "save_state"), mock.patch.object(N, "log_sent"), mock.patch.object(N, "pick_info", return_value={}), \
            mock.patch.object(N, "post_discord", side_effect=lambda u, t: calls.append("ping")), \
            mock.patch.object(N, "start_dk_snapshot", return_value=None):
        assert N.main([]) == 0
    assert calls == ["ping", "ping"]
