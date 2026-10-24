"""kpm exec check | kill | reconcile, and the wiring of both command groups into the top-level ``kpm`` parser.

Real stack behind a scripted HTTP exchange (see cli_world.py). ``check`` and ``reconcile`` only read; ``kill`` writes the
kill file and may send cancels, never anything else.
"""
from __future__ import annotations

import os
import subprocess
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

from kpm import cli as top
from kpm.execution import cli

from .cli_world import World
from .conftest import TEST_KEY_ID, fixture_json, respond
from .exec_fakes import CLOSE_TS, TICKER, base_market, entry_spec, err_result, ok_result, place


@pytest.fixture
def world(tmp_path, monkeypatch, ed25519_key, fake_clock):
    def make(**kw) -> World:
        return World(tmp_path, monkeypatch, ed25519_key, fake_clock, **kw)

    return make


def resting_order_in_ledger(w: World, order_id: str = "sim-rest-1") -> str:
    return w.rest_an_order(order_id)


# ============================================================================================ kpm exec check
def test_check_passes_every_check_reads_only_and_shows_the_account(world):
    w = world(enabled=False)                                                             # the shipped default
    assert w.run_exec("check", env="prod") == 0, w.text
    assert "READY: every check passed" in w.text
    for name in ("config", "environment", "keys", "kill_file", "maintenance", "loss_cap", "exchange_status", "clock_skew",
                 "auth_probe", "unresolved_orders"):
        assert f"ok   {name}:" in w.text, (name, w.text)
    assert "trading.enabled in config/settings.yaml: false" in w.text
    assert "cash on all shards: $29.4300" in w.text and "shard 2: $29.4300" in w.text
    assert "open positions: 0" in w.text and "resting orders: 0" in w.text
    assert "rate limits: tier basic, read bucket 600, write bucket 100" in w.text
    assert "loss cap: $0 of $5.00 used" in w.text and "key_fp" in w.text
    assert w.t.writes == []                                                              # not one write
    assert all(c.signed for c in w.t.calls if c.endpoint.startswith(("/portfolio/", "/account/")))
    rows = w.q("SELECT kind, live FROM exec_requests")
    assert rows and {r["live"] for r in rows} == {0} and "write" not in {r["kind"] for r in rows}   # audited, dry-run flagged
    blob = w.everything()
    for secret in (TEST_KEY_ID, str(w.pem), "secret_dir", "PRIVATE KEY", "KALSHI-ACCESS"):
        assert secret not in blob, secret


def test_a_key_id_or_key_path_that_reaches_a_response_is_scrubbed_from_every_stored_row(world):
    """The ledger is built with the Env's redact_values, so even a server reply that echoes the key id (or a path that
    contains it) never reaches exec_requests, audit_log or logs/exec.jsonl."""
    w = world()
    w.t.get("/portfolio/balance", lambda call: {"balance": 2943, "balance_dollars": "29.4300",
                                                "note": f"key {TEST_KEY_ID} loaded from {w.pem}"})
    assert w.run_exec("check", env="prod") == 0, w.text
    stored = " ".join(r["response"] or "" for r in w.q("SELECT response FROM exec_requests"))
    assert "[redacted]" in stored and "note" in stored                    # the reply was stored, with the secrets blanked
    blob = w.everything()
    assert TEST_KEY_ID not in blob and str(w.pem) not in blob and "secret_dir" not in blob


def test_check_without_a_key_reads_only_public_data_and_says_what_is_missing(world):
    w = world(keys=False)
    assert w.run_exec("check") == cli.EXIT_FAIL
    assert "FAIL keys:" in w.text and "NOT READY for live trading" in w.text
    assert "no API key loaded" in w.text and "Watch-only runs do not need them" in w.text
    assert w.t.writes == [] and not any(c.signed for c in w.t.calls)


def test_check_fails_on_a_clock_that_is_off(world):
    w = world(ahead=4.0)
    assert w.run_exec("check") == cli.EXIT_FAIL
    assert "FAIL clock_skew:" in w.text and "READY: every check" not in w.text


def shard_status(trading_by_shard: dict[int, bool]) -> dict:
    """GET /exchange/status with some shards paused (the captured production reply, edited)."""
    status = fixture_json("exchange_status_prod.json")
    for e in status["exchange_index_statuses"]:
        e["trading_active"] = trading_by_shard.get(e["exchange_index"], True)
    return status


def test_check_is_ready_while_only_another_shard_is_paused(world):
    """Review finding: `kpm exec check` said NOT READY (exit 1) when ANY shard (Combos, Tennis ...) was paused, though a live
    run on shard 2 (where KXBTC15M and KXBTCD trade) would have passed every gate."""
    w = world()
    w.t.get("/exchange/status", lambda call: respond(200, json=shard_status({1: False, 3: False})))
    assert w.run_exec("check") == 0, w.text
    assert "READY: every check passed" in w.text and "ok   exchange_status:" in w.text
    assert "shard 2 (KXBTC15M, KXBTCD) trading" in w.text and "other shard(s) not trading, not used here: 1, 3" in w.text


def test_check_fails_when_the_shard_the_bots_trade_on_is_paused(world):
    w = world()
    w.t.get("/exchange/status", lambda call: respond(200, json=shard_status({2: False})))
    assert w.run_exec("check") == cli.EXIT_FAIL
    assert "FAIL exchange_status:" in w.text and "shard 2 (KXBTC15M, KXBTCD) not trading" in w.text
    assert "NOT READY for live trading" in w.text and w.t.writes == []


def test_check_fails_when_the_shard_of_a_series_cannot_be_read(world):
    w = world()
    w.t.get("/series/KXBTCD", 503)
    w.t.get("/markets", {"markets": [], "cursor": ""}, params=lambda p: bool(p) and p.get("series_ticker") == "KXBTCD")
    assert w.run_exec("check") == cli.EXIT_FAIL
    assert "FAIL exchange_status:" in w.text and "cannot read the exchange_index" in w.text


def test_check_fails_while_a_kill_file_exists_or_an_order_is_unknown(world):
    w = world()
    w.kill.write_text("2026-10-05T18:28:31+00:00 dashboard STOP button\n")
    place(w.ledger(), entry_spec(base_market(), "yes", "0.59"), err_result("unknown", None, "transport_error"))
    assert w.run_exec("check") == cli.EXIT_FAIL
    assert "FAIL kill_file:" in w.text and "FAIL unresolved_orders:" in w.text and "unknown outcomes 1" in w.text


def test_check_reports_a_resting_order(world):
    w = world()
    resting_order_in_ledger(w)
    assert w.run_exec("check") == 0, w.text
    assert "resting orders: 1  <- cancel them (kpm exec kill) before live trading" in w.text


def test_check_refuses_a_config_past_a_ceiling_and_a_wrong_environment(world, monkeypatch):
    w = world(trading={"max_loss_usd": "6.00"})
    assert w.run_exec("check") == cli.EXIT_USAGE and "max_loss_usd" in w.text and w.t.calls == []
    w.trading["max_loss_usd"] = "5.00"
    with pytest.raises(SystemExit, match="does not match KALSHI_ENV"):
        w.run_exec("check", env="demo")
    assert w.t.calls == []


# ============================================================================================ kpm exec kill
def test_kill_writes_the_kill_file_and_sends_nothing_when_nothing_rests(world):
    w = world(enabled=True)
    assert w.run_exec("kill") == 0, w.text
    text = w.kill.read_text(encoding="utf-8")
    assert "kpm exec kill" in text and text[:4] == "2026"
    assert "kill file written:" in w.text and "cancel path: 0 order(s) found, 0 cancelled" in w.text
    assert w.t.writes == []                                                              # nothing rests: not a single DELETE


def test_kill_cancels_a_resting_order_with_its_own_shard_and_verifies(world):
    w = world(enabled=True)
    coid = resting_order_in_ledger(w)
    assert w.run_exec("kill") == 0, w.text
    assert w.sim.cancels == [("sim-rest-1", {"exchange_index": 2})]                      # routed to the order's shard
    (delete,) = w.t.writes
    assert (delete.method, delete.endpoint, delete.params, delete.body) == (
        "DELETE", "/portfolio/events/orders/sim-rest-1", {"exchange_index": 2}, None)
    assert delete.signed and delete.presign_text().endswith("DELETE/trade-api/v2/portfolio/events/orders/sim-rest-1")
    assert w.sim.cancel_all_calls == 0
    assert "1 order(s) found, 1 cancelled, verified True, still resting 0, cancel-all used False" in w.text
    assert w.q("SELECT status FROM exec_orders WHERE client_order_id = ?", coid)[0]["status"] == "canceled"


def test_kill_without_a_key_still_writes_the_file(world):
    w = world(keys=False)
    assert w.run_exec("kill") == 0
    assert w.kill.exists() and "cancel path not run: no API key is loaded" in w.text
    assert w.t.calls == []


def test_kill_without_kalshi_env_still_writes_the_file(world, monkeypatch):
    w = world(keys=False)
    monkeypatch.delenv("KALSHI_ENV")
    assert w.run_exec("kill") == 0
    assert w.kill.exists() and "cancel path not run: KALSHI_ENV is not set" in w.text


def test_kill_works_even_when_the_config_is_past_a_ceiling(world):
    w = world(keys=False, trading={"max_loss_usd": "50"})                                 # a broken config must not block STOP
    assert w.run_exec("kill") == 0 and w.kill.exists()


def test_kill_twice_appends_and_deleting_the_file_is_the_only_way_back(world):
    w = world(keys=False)
    w.run_exec("kill")
    w.run_exec("kill")
    assert len(w.kill.read_text(encoding="utf-8").splitlines()) == 2


# ============================================================================================ kpm exec reconcile
def lost_reply_order(w: World, *, reached_exchange: bool) -> str:
    """An IOC order whose reply was lost (unknown outcome in the ledger); the exchange may or may not have it."""
    led = w.ledger("old-run")
    coid = led.new_intent(entry_spec(base_market(), "yes", "0.73"))
    led.mark_sent([coid])
    led.mark_result(coid, err_result("unknown", None, "transport_error", coid))
    if reached_exchange:
        w.sim.place(led.request_body(coid), D(1))
    return coid


def test_reconcile_adopts_an_order_whose_reply_was_lost_and_sends_nothing(world):
    w = world(enabled=True)
    coid = lost_reply_order(w, reached_exchange=True)
    assert w.run_exec("reconcile", env="prod") == 0, w.text
    assert "reconcile: clean" in w.text and coid[:8] in w.text and w.t.writes == []
    (o,) = w.q("SELECT * FROM exec_orders WHERE client_order_id = ?", coid)
    assert o["status"] == "executed" and o["fill_count"] == "1.00"
    assert w.q("SELECT COUNT(*) AS n FROM exec_fills")[0]["n"] == 1
    assert not w.kill.exists()


def test_reconcile_with_nothing_to_do_is_clean(world):
    w = world()
    assert w.run_exec("reconcile") == 0, w.text
    assert "reconcile: clean" in w.text and w.t.writes == []


def test_reconcile_halts_when_an_order_that_was_sent_is_not_on_the_exchange(world):
    w = world(enabled=True)
    lost_reply_order(w, reached_exchange=False)
    w.clock.advance(20)                                                                  # old enough to be judged
    assert w.run_exec("reconcile", env="prod") == cli.EXIT_FAIL
    assert "reconcile: PROBLEMS FOUND" in w.text and "HALTED:" in w.text
    assert w.kill.exists() and "safety trip" in w.kill.read_text(encoding="utf-8")       # every process stops
    assert w.t.writes == []                                                              # and still nothing was resent


def test_reconcile_needs_a_key(world):
    w = world(keys=False)
    with pytest.raises(SystemExit, match="no API key configured"):
        w.run_exec("reconcile")
    assert w.t.calls == []


# ============================================================================================ the top-level parser
def test_kpm_exec_and_bot_are_wired_into_the_top_level_commands(world, monkeypatch):
    w = world(keys=False)
    monkeypatch.setattr(cli, "default_deps", lambda: w.deps)
    assert top.main(["bot", "run", "--strategy", "late_favorite", "--hours", "0.1"]) == 0
    assert w.q("SELECT strategy, mode FROM bot_runs") == [{"strategy": "late_favorite", "mode": "watch"}]
    assert top.main(["bot", "run", "--strategy", "late_favorite", "--smoke"]) == 0
    assert top.main(["exec", "check"]) == cli.EXIT_FAIL                                  # no key: not ready for live
    assert top.main(["exec", "kill"]) == 0 and w.kill.exists()
    assert w.t.writes == []


def test_a_live_start_from_the_top_level_parser_is_refused_without_the_gates(world, monkeypatch):
    w = world(enabled=False)
    monkeypatch.setattr(cli, "default_deps", lambda: w.deps)
    assert top.main(["bot", "run", "--strategy", "late_favorite", "--live", "--env", "prod"]) == cli.EXIT_FAIL
    assert "LIVE MODE REFUSED" in w.text and w.t.writes == []


def test_only_a_gated_live_run_and_kill_build_a_client_that_can_write(world, monkeypatch):
    """The one parameter that matters most: which commands construct ExecClient(live=True)."""
    built = []
    real = cli.ExecClient

    def recorder(env, **kw):
        built.append((kw.get("live"), kw.get("audit") is not None))
        return real(env, **kw)

    monkeypatch.setattr(cli, "ExecClient", recorder)
    w = world(enabled=True)
    assert w.run_bot() == 0                                               # watch-only
    assert built == [(False, False)]                                       # no write possible, and no ledger at all
    built.clear()
    w.run_exec("check", env="prod")
    w.run_exec("reconcile", env="prod")
    assert built == [(False, True), (False, True)]                         # reads only (audited into the ledger)
    built.clear()
    w.deps.confirm = "wrong phrase"
    w.run_bot(live=True, env="prod")
    assert built == [(True, True)]                                         # built live, but the gates refuse before any use
    assert w.t.writes == []
    built.clear()
    w.clock.now = CLOSE_TS - 130                                           # back before the window the watch run used up
    w.deps.confirm = "LIVE PROD 5"
    w.run_bot(live=True, env="prod", hours=0.1)
    assert built == [(True, True)] and len(w.t.writes) == 1
    built.clear()
    w.run_exec("kill")
    assert built == [(True, True)]                                         # cancels only, after the kill file is written
    assert len(w.t.writes) == 1                                            # (still just the one order: nothing rested)


@pytest.mark.parametrize("argv", [["exec", "check"], ["bot", "run", "--strategy", "late_favorite", "--smoke"]])
def test_a_missing_trading_extra_gives_an_install_hint_not_a_traceback(monkeypatch, capsys, argv):
    import kpm.execution
    monkeypatch.delattr(kpm.execution, "cli")                               # (an import of the package attribute first)
    monkeypatch.setitem(sys.modules, "kpm.execution.cli", None)             # what a failed import leaves behind
    assert top.main(argv) == 2
    err = capsys.readouterr().err
    assert "needs the trading extras" in err and 'pip install -e ".[trading]"' in err


def test_the_parser_accepts_the_documented_forms_and_rejects_the_rest():
    p = top.build_parser()
    a = p.parse_args(["bot", "run", "--strategy", "pair_h2", "--live", "--env", "prod", "--hours", "2", "--smoke"])
    assert (a.cmd, a.bot_cmd, a.strategy, a.live, a.env, a.hours, a.smoke) == ("bot", "run", "pair_h2", True, "prod", 2.0, True)
    a = p.parse_args(["bot", "run", "--strategy", "late_favorite"])
    assert (a.live, a.env, a.hours, a.smoke) == (False, None, None, False)                # watch-only is the default
    assert p.parse_args(["exec", "check", "--env", "prod"]).exec_cmd == "check"
    assert p.parse_args(["exec", "kill"]).exec_cmd == "kill"
    assert p.parse_args(["exec", "reconcile", "--env", "demo"]).env == "demo"
    for bad in (["bot"], ["bot", "run"], ["bot", "run", "--strategy", "nope"], ["exec"], ["exec", "place"],
                ["exec", "check", "--env", "staging"], ["bot", "run", "--strategy", "late_favorite", "--hours", "x"]):
        with pytest.raises(SystemExit) as e:
            p.parse_args(bad)
        assert e.value.code == 2, bad


def test_every_existing_command_is_still_there():
    p = top.build_parser()
    commands = ["init-db", "nfl", "kalshi", "polymarket", "polymarket-us", "match", "review-export", "review-api", "gate",
                "manual-import", "gaps", "analyze", "report", "reverse", "edge-hunt", "crypto-collect", "crypto-backtest",
                "crypto-trends", "crypto-htf", "crypto-breakout", "crypto-bot-rules", "scan", "final-minute",
                "cross-bounds", "dashboard", "lip-scan", "crypto-maker", "market-making", "status"]
    for c in commands:
        assert p.parse_args([c]).cmd == c, c
    assert p.parse_args(["review-import", "some/dir"]).path == "some/dir"
    assert p.parse_args(["dashboard", "--port", "9000"]).port == 9000
    assert p.parse_args(["scan", "--repeat", "3", "--every", "10"]).repeat == 3


def test_research_commands_do_not_import_the_execution_package():
    """kpm.cli imports kpm.execution only when exec or bot is dispatched; building the parser imports nothing heavy."""
    code = ("import sys; import kpm.cli as c; c.build_parser(); "
            "print(any(m.startswith('kpm.execution') for m in sys.modules))")
    src = str(Path(top.__file__).resolve().parents[1])
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                         env={"PYTHONPATH": src, "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")})
    assert out.stdout.strip() == "False", out.stderr
