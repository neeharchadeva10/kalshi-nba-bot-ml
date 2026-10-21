import importlib.util
import sys
from datetime import date, datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("early_overs_notify", ROOT / "scripts" / "early_overs_notify.py")
N = importlib.util.module_from_spec(spec)
sys.modules["early_overs_notify"] = N
spec.loader.exec_module(N)

ET = ZoneInfo("America/New_York")
GOOD = "https://discord.com/api/webhooks/123/SECRETTOKEN"


@pytest.fixture(autouse=True)
def _no_real_snapshot(monkeypatch):
    """The DraftKings snapshot hook must never run for real (live Odds API call, real key, real data files)."""
    import requests
    monkeypatch.setenv("ODDS_API_KEY", "")
    monkeypatch.setattr(N, "DK_RUNNER", lambda: None)
    monkeypatch.setattr(N, "ORDER_MODE", "maker")   # the tests below were written for limit-at-the-bid wording
    monkeypatch.setattr(N, "SEND_SCHEDULE", True)   # ...and with schedule / cancel-check messages on
    monkeypatch.setattr(N, "SEND_CANCEL", True)

    def boom(*a, **k):
        raise AssertionError("network call attempted in a test")
    monkeypatch.setattr(requests, "get", boom)


def test_load_webhook_ok_and_comments(tmp_path):
    p = tmp_path / "w.txt"
    p.write_text("# c\n\n" + GOOD + "\n")
    assert N.load_webhook(p, env={}) == GOOD


@pytest.mark.parametrize("content", ["PASTE_WEBHOOK_URL_HERE\n", "", "http://evil.com/x\n", "https://discord.com/other\n"])
def test_load_webhook_bad(tmp_path, content):
    p = tmp_path / "w.txt"
    p.write_text(content)
    with pytest.raises(N.NotifyError):
        N.load_webhook(p, env={})


def test_env_fallback(tmp_path):
    assert N.load_webhook(tmp_path / "none.txt", env={"DISCORD_WEBHOOK_URL": GOOD}) == GOOD


def test_bad_url_not_in_error(tmp_path):
    bad = "https://evil.example/SECRETTOKEN"
    p = tmp_path / "w.txt"
    p.write_text(bad)
    with pytest.raises(N.NotifyError) as e:
        N.load_webhook(p, env={})
    assert "SECRETTOKEN" not in str(e.value)


def test_post_failure_redacts_url():
    import requests
    with mock.patch.object(N.requests, "post", side_effect=requests.ConnectionError("fail " + GOOD)), \
            mock.patch.object(N.time, "sleep"):
        with pytest.raises(N.NotifyError) as e:
            N.post_discord(GOOD, "x")
    assert "SECRETTOKEN" not in str(e.value) and "SECRETTOKEN" not in repr(e.value.__cause__)
    assert e.value.__suppress_context__


def test_post_429_retry_then_ok():
    r429 = mock.Mock(status_code=429)
    r429.json.return_value = {"retry_after": 0.5}
    ok = mock.Mock(status_code=204)
    with mock.patch.object(N.requests, "post", side_effect=[r429, ok]) as m, mock.patch.object(N.time, "sleep") as s:
        N.post_discord(GOOD, "hi")
    assert m.call_count == 2 and s.call_args[0][0] == 0.5
    assert m.call_args.kwargs["timeout"] == 10 and m.call_args.kwargs["json"] == {"content": "hi"}


def test_post_http_error_redacted():
    with mock.patch.object(N.requests, "post", return_value=mock.Mock(status_code=404)):
        with pytest.raises(N.NotifyError) as e:
            N.post_discord(GOOD, "x")
    assert "SECRETTOKEN" not in str(e.value)


def test_buckets():
    assert N.bucket_for(0)[2] == 51.5
    assert N.bucket_for(-1.99)[2] == 51.5
    assert N.bucket_for(2.0)[2] == 47.9
    assert N.bucket_for(5.9)[2] == 44.3
    assert N.bucket_for(-6.5)[2] == 53.0
    assert N.bucket_for(8)[2] == 46.7 and N.bucket_for(30)[2] == 46.7


def test_breakeven():
    assert round(N.breakeven_pct(48), 1) == 48.4


ROW = {"game": "26OCT21ATLORL", "side": "UNDER", "ticker": "KXNBATOTAL-26OCT21ATLORL-234", "strike": 233.5,
       "proj": 232.1, "limit": 0.48, "status": "PLACE NOW (limit buy NO @ 0.48)"}
TIP = datetime(2026, 10, 21, 19, 0, tzinfo=ET)


HOWTO_UNDER = (
    "How to place it (Kalshi app or site):\n"
    '1) Search "ATL ORL total" (e.g. "ATL ORL total") and open Total points.\n'
    '2) Find the line "Over 233.5". Tap NO (= under).\n'
    "3) Switch the order type to LIMIT. Price: 48c. Quantity/shares: 1.\n"
    "4) Review and submit. It will wait in line until someone sells to you.\n"
    "5) At 6:45 PM, if it hasn't filled, cancel it (open your resting orders / open orders and cancel). "
    "Don't raise the price.\n"
    "Screens can differ a bit - the key parts are: NO/YES, LIMIT, the price, 1 contract.")


def test_howto_over_and_length():
    t = N.format_pick({**ROW, "side": "OVER", "proj": 238.0}, TIP)
    assert 'Tap YES (= over).' in t and "Tap NO" not in t and "Price: 48c" in t
    assert len(t) < 1900 and len(N.format_pick(ROW, TIP)) < 1900


def test_cancel_message_and_timing_dedupe_only_pinged():
    info = N.pick_info(ROW)
    assert N.format_cancel("26OCT21ATLORL", TIP, info) == (
        'Cancel check - ATL @ ORL (tip 7:00 PM): if your 1 share NO @ 48c on "Over 233.5" hasn\'t filled, cancel it now. '
        "If it filled, you're done - it settles after the game.")
    assert "YES @" in N.format_cancel("26OCT21ATLORL", TIP, {**info, "side": "OVER"})
    tips = {"26OCT21ATLORL": TIP}
    day = date(2026, 10, 21)
    state = {N.pick_key(day, "26OCT21ATLORL"): {"sent": "x", **info}, N.schedule_key(day): "x"}
    at = lambda h, m: datetime(2026, 10, 21, h, m, tzinfo=ET)
    assert N.plan_messages([], tips, day, at(18, 44), state) == []
    m = N.plan_messages([], tips, day, at(18, 45), state)
    assert [k for _, k, _ in m] == ["cancel"] and m[0][0] == "2026-10-21|26OCT21ATLORL|CANCEL"
    assert N.plan_messages([], tips, day, at(19, 0), state) == []  # at/after tip
    state[m[0][0]] = "x"
    assert N.plan_messages([], tips, day, at(18, 50), state) == []  # deduped
    # game never pinged -> no cancel
    s2 = {N.schedule_key(day): "x"}
    assert N.plan_messages([], tips, day, at(18, 50), s2) == []


def test_format_pick_under():
    t = N.format_pick(ROW, TIP)
    assert t == (
        "NBA totals pick - ATL @ ORL (tip 7:00 PM ET)\n"
        "ORDER: BUY NO (UNDER 233.5) x 1 share @ 48c LIMIT (wait in line at the bid)\n"
        "Bet: UNDER 233.5 -> buy NO on 'Over 233.5' (KXNBATOTAL-26OCT21ATLORL-234)\n"
        "Shares: 1 contract (= 1 share) at 48c limit -> cost ~$0.48 + ~$0.004 fee; pays $1.00 if it wins\n"
        "Limit price: 48c x 1 contract (cost ~$0.48 + fee)\n"
        "Cancel if unfilled by 6:45 PM ET. Don't chase.\n"
        "Formula 232.1 vs line 233.5 (1.4 under). Last season similar picks won 52% (95% CI 45-58%); "
        "need 48.4% to break even.\n"
        "Research pick, not financial advice - you place the bet yourself.\n\n" + HOWTO_UNDER)


def test_format_pick_over():
    r = {**ROW, "side": "OVER", "proj": 238.0, "strike": 233.5}
    t = N.format_pick(r, TIP)
    assert "Bet: OVER 233.5 -> buy YES" in t and "(4.5 over)" in t and "44%" in t


def test_format_schedule():
    t = N.format_schedule(date(2026, 10, 21), [("26OCT21ATLORL", TIP)])
    assert "cancel check 15 min before tip." in t
    assert "ATL @ ORL - tip 7:00 PM ET; place 5:45 PM-6:15 PM; cancel by 6:45 PM" in t


def test_plan_dedupe_and_schedule_once():
    tips = {"26OCT21ATLORL": TIP}
    day = date(2026, 10, 21)
    now = datetime(2026, 10, 21, 18, 0, tzinfo=ET)
    state = {}
    m = N.plan_messages([ROW], tips, day, now, state)
    assert [k for _, k, _ in m] == ["schedule", "pick"]
    for sk, _, _ in m:
        state[sk] = "x"
    assert N.plan_messages([ROW], tips, day, now, state) == []
    # next day: new keys
    assert len(N.plan_messages([ROW], tips, date(2026, 10, 22), now, state)) == 2
    # before 17:00: no schedule
    early = datetime(2026, 10, 21, 16, 0, tzinfo=ET)
    assert N.plan_messages([], tips, day, early, {}) == []
    # not PLACE NOW -> no pick
    assert N.plan_messages([{**ROW, "status": "too early (in 5m)"}], tips, day, early, {}) == []


def test_state_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    assert N.load_state(p) == {}
    N.save_state({"a": 1}, p)
    assert N.load_state(p) == {"a": 1}


def test_dry_run_sends_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(N, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(N, "LOG_PATH", tmp_path / "l.log")
    fake = mock.Mock()
    fake.ET = ET
    fake.compute_rows.return_value = ([ROW], 6, [(TIP, "26OCT21ATLORL", [])], None)
    monkeypatch.setattr(N, "_load_picks", lambda: fake)
    with mock.patch.object(N.requests, "post") as post:
        assert N.main(["--dry-run", "--force-schedule"]) == 0
    post.assert_not_called()
    assert not (tmp_path / "s.json").exists() and not (tmp_path / "l.log").exists()
    out = capsys.readouterr().out
    assert "Tonight's schedule" in out


def test_send_updates_state_and_log_without_url(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(N, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(N, "LOG_PATH", tmp_path / "l.log")
    monkeypatch.setattr(N, "load_webhook", lambda: GOOD)
    fake = mock.Mock()
    fake.ET = ET
    fake.compute_rows.return_value = ([ROW], 6, [(TIP, "26OCT21ATLORL", [])], None)
    monkeypatch.setattr(N, "_load_picks", lambda: fake)
    day = datetime.now(ET).date()
    row = {**ROW}
    tip = datetime.combine(day, TIP.timetz())
    fake.compute_rows.return_value = ([row], 6, [(tip, "26OCT21ATLORL", [])], None)
    with mock.patch.object(N.requests, "post", return_value=mock.Mock(status_code=204)) as post:
        N.main([])
        N.main([])  # second run: deduped
    sent = [c.kwargs["json"]["content"] for c in post.call_args_list]
    assert sum("NBA totals pick" in s for s in sent) == 1
    assert "SECRETTOKEN" not in capsys.readouterr().out
    assert "SECRETTOKEN" not in (tmp_path / "l.log").read_text()
    assert "SECRETTOKEN" not in (tmp_path / "s.json").read_text()


def test_test_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(N, "LOG_PATH", tmp_path / "l.log")
    monkeypatch.setattr(N, "load_webhook", lambda: GOOD)
    with mock.patch.object(N.requests, "post", return_value=mock.Mock(status_code=204)) as post:
        assert N.main(["--test"]) == 0
    assert post.call_args.kwargs["json"] == {"content": "Kalshi-PM notifier connected"}


def test_taker_mode_uses_ask_price_fee_and_wording(monkeypatch):
    """ORDER_MODE = 'taker' (user, 2026-10-21): price = the side's ask, taker fee 0.07*p*(1-p), fill-now wording."""
    monkeypatch.setattr(N, "ORDER_MODE", "taker")
    row = {"game": "26OCT21MINMIA", "side": "UNDER", "ticker": "KXNBATOTAL-26OCT21MINMIA-240", "strike": 239.5,
           "limit": 0.47, "taker_price": 0.48, "proj": 232.7, "rule": "grid_winner", "p_formula": 0.64,
           "ev_c": 16.4, "grid_t": 4.0}
    tip = datetime(2026, 10, 21, 19, 30, tzinfo=ZoneInfo("America/New_York"))
    msg = N.format_pick(row, tip)
    assert "Price: 48c" in msg and "at 48c limit" in msg and "should fill right away" in msg
    assert "~$0.017 fee" in msg                                   # 0.07*0.48*0.52 = 0.01747
    assert abs(N.breakeven_pct(48) - (48 + 7 * 0.48 * 0.52)) < 1e-9
    assert f"EV at 48c = {(0.64 - 0.48 - 0.07 * 0.48 * 0.52) * 100:+.1f}c" in msg
    assert N.pick_info(row)["limit_c"] == 48 and len(msg) < 1900


def test_order_and_prices_lines():
    r = {**ROW, "yes_bid": 0.50, "yes_ask": 0.52}
    t = N.format_pick(r, TIP)
    assert "ORDER: BUY NO (UNDER 233.5) x 1 share @ 48c LIMIT" in t
    assert "YES (over) bid 50c / ask 52c; NO (under) bid 48c / ask 50c" in t


def test_picks_only_when_schedule_and_cancel_off(monkeypatch):
    """User 2026-10-21: shared channel - only pick pings (no schedule, no cancel checks)."""
    monkeypatch.setattr(N, "SEND_SCHEDULE", False)
    monkeypatch.setattr(N, "SEND_CANCEL", False)
    day = date(2026, 10, 21)
    tips = {"26OCT21ATLORL": TIP}
    row = {**ROW, "status": "PLACE NOW (limit buy NO @ 0.48)"}
    at = lambda h, m: datetime(2026, 10, 21, h, m, tzinfo=ET)
    m = N.plan_messages([row], tips, day, at(17, 50), {})
    assert [k for _, k, _ in m] == ["pick"]
    state = {N.pick_key(day, "26OCT21ATLORL"): {"sent": "x", **N.pick_info(ROW)}}
    assert N.plan_messages([], tips, day, at(18, 50), state) == []          # no cancel check
    assert N.plan_messages([], tips, day, at(17, 1), {}, force_schedule=True) == []  # no schedule
