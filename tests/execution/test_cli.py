"""kpm bot run (watch-only) and the argument and config checks, on the real stack behind a scripted HTTP exchange.

See cli_world.py for what is real and what is fake. The live gates and live sessions are in test_cli_live.py, the
``kpm exec`` commands in test_cli_exec.py.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest
import yaml

from kpm.execution import cli, units

from .cli_world import FEE_73, SETTINGS, World
from .conftest import TEST_KEY_ID
from .exec_fakes import CLOSE_TS, TICKER


@pytest.fixture
def world(tmp_path, monkeypatch, ed25519_key, fake_clock):
    def make(**kw) -> World:
        return World(tmp_path, monkeypatch, ed25519_key, fake_clock, **kw)

    return make


# ============================================================================================ watch-only
def test_watch_only_needs_no_keys_sends_nothing_and_writes_the_paper_twin(world):
    w = world(keys=False)
    assert w.run_bot() == 0
    assert w.t.writes == [] and not any(c.signed for c in w.t.calls)                    # public reads only
    (run,) = w.q("SELECT * FROM bot_runs")
    assert (run["strategy"], run["mode"], run["end_reason"]) == ("late_favorite", "watch", "finished")
    assert run["ended_ts"] is not None
    (opp,) = w.q("SELECT * FROM bot_opportunities")
    assert opp["kind"] == "late favorite YES" and opp["outcome"] == "paper_only" and opp["live"] == 0
    assert opp["paper_cost"] == pytest.approx(0.73) and opp["paper_fees"] == pytest.approx(float(FEE_73))
    assert opp["paper_realized_pnl"] == pytest.approx(float(1 - D("0.73") - FEE_73))    # YES won: the twin settled
    (order,) = w.q("SELECT * FROM bot_orders")
    assert order["dry_run"] == 1 and order["status"] == "dry_run" and order["side"] == "yes"
    assert order["limit_price"] == pytest.approx(0.73)
    for table in ("exec_orders", "exec_requests", "exec_fills", "exec_settlements"):
        assert w.q(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] == 0                   # no ledger in watch-only
    assert "WATCH-ONLY" in w.text and TEST_KEY_ID not in w.everything()


def test_watch_only_with_keys_in_env_still_makes_public_reads_only(world):
    w = world(keys=True)
    assert w.run_bot() == 0
    assert w.t.writes == [] and not any(c.signed for c in w.t.calls)
    assert w.q("SELECT mode FROM bot_runs")[0]["mode"] == "watch"


def test_watch_only_with_a_broken_key_setup_still_runs_and_says_why(world, monkeypatch):
    w = world(keys=False)
    monkeypatch.setenv("KALSHI_PROD_KEY_ID", "11111111-2222-4333-8444-555555555555")
    monkeypatch.setenv("KALSHI_PROD_KEY_PATH", str(w.tmp / "no_such_key.pem"))
    assert w.run_bot() == 0
    assert "note: prod: the private key file named by KALSHI_PROD_KEY_PATH does not exist. Watch-only does not use the key." in w.text
    assert "no_such_key" not in w.text and w.t.writes == [] and not any(c.signed for c in w.t.calls)


def test_watch_only_is_the_default_and_a_no_signal_book_skips_the_window(world):
    w = world(keys=False, book=("0.50", "0.49"))                                         # mid 0.505: no favorite
    assert w.run_bot() == 0
    assert w.q("SELECT COUNT(*) AS n FROM bot_opportunities")[0]["n"] == 0
    assert w.q("SELECT COUNT(*) AS n FROM bot_orders")[0]["n"] == 0


def test_a_no_favorite_buys_no_at_the_no_ask(world):
    w = world(keys=False, book=("0.05", "0.94"), result="no")                           # YES mid 0.055: buy NO at 0.95
    assert w.run_bot() == 0
    (opp,) = w.q("SELECT * FROM bot_opportunities")
    assert opp["kind"] == "late favorite NO" and opp["paper_cost"] == pytest.approx(0.95)
    assert opp["paper_realized_pnl"] == pytest.approx(float(1 - D("0.95") - units.taker_fee(D(1), D("0.95"))))
    (order,) = w.q("SELECT * FROM bot_orders")
    assert order["side"] == "no" and order["limit_price"] == pytest.approx(0.95)


def test_smoke_is_one_public_pass_and_writes_no_opportunity_or_order(world):
    w = world(keys=False)
    assert w.run_bot(smoke=True, hours=None) == 0
    assert w.q("SELECT COUNT(*) AS n FROM bot_opportunities")[0]["n"] == 0
    assert w.q("SELECT COUNT(*) AS n FROM bot_orders")[0]["n"] == 0
    assert w.q("SELECT end_reason FROM bot_runs")[0]["end_reason"] == "smoke pass done"
    assert TICKER in w.text and "smoke pass done" in w.text and w.t.writes == []
    paths = {c.endpoint for c in w.t.calls}
    assert paths <= {"/markets", f"/markets/{TICKER}/orderbook", "/exchange/status", f"/markets/{TICKER}"}


def test_pair_h2_smoke_runs_watch_only_too(world):
    from .exec_fakes import hourly_market
    from .sim_http import zulu
    w = world(keys=False, start_offset=-300.0)
    w.sim.add_market(hourly_market(ticker="KXBTCD-26OCT0514-T85249.99", close_time=zulu(CLOSE_TS)), "KXBTCD",
                     book=("0.40", "0.58"))
    assert w.run_bot(strategy="pair_h2", smoke=True, hours=None) == 0
    assert w.q("SELECT strategy, mode FROM bot_runs")[0] == {"strategy": "pair_h2", "mode": "watch"}
    assert w.t.writes == []


def test_a_kill_file_stops_even_a_watch_run_before_it_reads_anything(world):
    w = world(keys=False)
    w.kill.write_text("2026-10-05T18:28:31+00:00 dashboard STOP button\n")
    assert w.run_bot() == cli.EXIT_FAIL                                                  # not a clean end
    assert "kill switch present at start" in w.text
    assert w.t.calls == []


# ============================================================================================ run length and bot choice
def test_pair_h2_takes_whole_hourly_windows_rounded_up():
    class Ctx:
        client = state = ledger = safety = None
    env = type("E", (), {"name": "prod", "key_fp": None})()
    import sqlite3
    from kpm.execution.botstate import BotState
    state = BotState(sqlite3.connect(":memory:"), "r")
    trading = yaml.safe_load(SETTINGS.read_text(encoding="utf-8"))["trading"]
    deps = cli.Deps(settings=lambda: {}, load_env=None, connect=None)
    client = type("C", (), {"live": False})()
    for given, expected in ((0.2, 1), (1.0, 1), (1.01, 2), (2.5, 3), (None, None)):
        _, hours = cli._make_bot("pair_h2", client, state, None, None, env, trading, False, deps, given)
        assert hours == expected and (hours is None or isinstance(hours, int))
    _, hours = cli._make_bot("late_favorite", client, state, None, None, env, trading, False, deps, 0.25)
    assert hours == 0.25                                                                 # late_favorite: wall-clock hours


def test_an_unknown_strategy_is_refused():
    deps = cli.Deps(settings=lambda: {}, load_env=None, connect=None)
    with pytest.raises(ValueError, match="unknown strategy"):
        cli._make_bot("nope", None, None, None, None, None, {}, False, deps, None)


# ============================================================================================ argument and config errors
@pytest.mark.parametrize("kw,needle", [
    ({"smoke": True, "live": True}, "--smoke"),
    ({"hours": 0}, "--hours"), ({"hours": -1.0}, "--hours"), ({"hours": float("nan")}, "--hours"),
    ({"hours": float("inf")}, "--hours"), ({"smoke": True, "hours": 1.0}, "--hours does not apply"),
])
def test_bad_arguments_refuse_to_start(world, kw, needle):
    w = world(keys=False)
    assert w.run_bot(**kw) == cli.EXIT_USAGE
    assert needle in w.text and w.t.calls == []
    assert not w.db_path.exists()                                                        # refused before anything was opened


@pytest.mark.parametrize("trading,needle", [
    ({"max_loss_usd": "5.01"}, "max_loss_usd"), ({"max_cost_per_order_usd": "1.50"}, "max_cost_per_order_usd"),
    ({"max_contracts_per_order": "2"}, "max_contracts_per_order"), ({"max_orders_per_minute": 12}, "max_orders_per_minute"),
    ({"max_open_exposure_usd": "3.50"}, "max_open_exposure_usd"), ({"max_clock_skew_s": 5}, "max_clock_skew_s"),
    ({"allowed_series": ["KXBTC15M", "KXNFLGAME"]}, "allowed_series"),
])
def test_a_config_value_past_a_ceiling_refuses_to_start_even_in_watch_only(world, trading, needle):
    w = world(keys=False, trading=trading)
    assert w.run_bot() == cli.EXIT_USAGE
    assert needle in w.text and w.t.calls == []


@pytest.mark.parametrize("lf,needle", [
    ({"up_threshold": "0.65"}, "up_threshold"), ({"window_start_s": 700}, "window_start_s"),
    ({"contracts": "2"}, "contracts"), ({"max_attempts": 4}, "max_attempts"),
])
def test_a_strategy_setting_that_breaks_the_rule_or_a_ceiling_refuses_to_start(world, lf, needle):
    base = yaml.safe_load(SETTINGS.read_text(encoding="utf-8"))["trading"]["late_favorite"]
    w = world(keys=False, trading={"late_favorite": {**base, **lf}})
    assert w.run_bot() == cli.EXIT_USAGE
    assert needle in w.text and w.t.calls == []


def test_a_missing_or_wrong_environment_stops_with_the_reason(world, monkeypatch):
    w = world(keys=False)
    monkeypatch.delenv("KALSHI_ENV")
    with pytest.raises(SystemExit, match="KALSHI_ENV is not set"):
        w.run_bot()
    monkeypatch.setenv("KALSHI_ENV", "prod")
    with pytest.raises(SystemExit, match="does not match KALSHI_ENV"):
        w.run_bot(env="demo")
    assert w.t.calls == []
