"""kpm bot run --live: every gate, then whole live sessions through every real module.

Real: Env, ExecClient (signing, parsing), Ledger, Safety, reconcile, BotState and late_favorite. Fake: the HTTP wire
(sim_http.SimHttp), the clock and the terminal. See cli_world.py. The safety tests in test_safety.py check each gate
in isolation; here the question is whether the CLI actually runs them, in order, before anything is sent.
"""
from __future__ import annotations

from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from kpm.execution import cli

from .cli_world import FEE_73, PHRASE, World, refused
from .conftest import TEST_KEY_ID, verify_signature
from .exec_fakes import CLOSE_TS, TICKER


@pytest.fixture
def world(tmp_path, monkeypatch, ed25519_key, fake_clock):
    def make(**kw) -> World:
        return World(tmp_path, monkeypatch, ed25519_key, fake_clock, **kw)

    return make


# ============================================================================================ live: the gates
def test_live_is_refused_while_trading_is_not_enabled_in_settings(world):
    w = world(enabled=False)                                                             # the shipped default
    refused(w, w.run_bot(live=True, env="prod"), "trading_enabled")
    assert w.t.calls == []                                                               # the static gates fail before any I/O


def test_live_needs_env_prod_on_the_command_line(world):
    w = world(enabled=True)
    refused(w, w.run_bot(live=True, env=None), "env_flag")
    assert w.t.calls == []


def test_live_needs_kalshi_env_prod_and_demo_is_refused_by_design(world):
    w = world(enabled=True, env="demo")
    refused(w, w.run_bot(live=True, env="demo"), "env_var", "env_flag")
    assert w.t.calls == []


def test_live_needs_an_interactive_terminal(world):
    w = world(enabled=True, isatty=False)
    refused(w, w.run_bot(live=True, env="prod"), "tty", "confirmation")
    assert w.t.calls == []


@pytest.mark.parametrize("typed", ["", "live prod 5", "LIVE PROD 5.00", "LIVE PROD 0.25", "LIVE PROD", "yes", "LIVE PROD 50"])
def test_live_needs_the_exact_typed_phrase(world, typed):
    w = world(enabled=True, confirm=typed)
    refused(w, w.run_bot(live=True, env="prod"), "confirmation")
    assert w.t.calls == []


def test_a_prompt_that_is_interrupted_is_not_a_confirmation(world):
    def interrupted(prompt):
        raise KeyboardInterrupt

    w = world(enabled=True, confirm=interrupted)
    refused(w, w.run_bot(live=True, env="prod"), "confirmation")


def test_the_prompt_shows_the_caps_and_asks_for_the_phrase(world):
    seen = []

    def typed(prompt):
        seen.append(prompt)
        return PHRASE

    w = world(enabled=True, confirm=typed)
    assert w.run_bot(live=True, env="prod", hours=0.1) == 0, w.text
    assert seen == [f'Type "{PHRASE}" to start live trading: ']
    assert "loss cap $5.00 account-wide" in w.text and "at most 1 contract" in w.text and "300 per run" in w.text


def test_live_is_refused_with_a_kill_file(world):
    w = world(enabled=True)
    w.kill.write_text("2026-10-05T18:28:31+00:00 dashboard STOP button\n")
    refused(w, w.run_bot(live=True, env="prod"), "kill_file")
    assert w.t.calls == []


def test_live_is_refused_when_the_pc_clock_is_off(world):
    w = world(enabled=True, ahead=5.0)                                                   # the PC is 5 s ahead of Kalshi
    refused(w, w.run_bot(live=True, env="prod"), "clock_skew")
    assert "clock skew +" in w.text


def test_a_clock_within_two_seconds_passes(world):
    w = world(enabled=True, ahead=1.0)
    assert w.run_bot(live=True, env="prod", hours=0.1) == 0, w.text


def test_live_is_refused_without_a_key(world):
    w = world(enabled=True, keys=False)
    with pytest.raises(SystemExit, match="no API key configured"):
        w.run_bot(live=True, env="prod")
    assert w.t.calls == []


def test_live_is_refused_when_the_loss_cap_is_already_used_up(world):
    from .exec_fakes import seed_realized_loss
    w = world(enabled=True)
    seed_realized_loss(w.ledger(), "5.00")
    refused(w, w.run_bot(live=True, env="prod"), "loss_cap")


def test_live_is_refused_while_an_order_has_an_unknown_outcome(world):
    from .exec_fakes import base_market, entry_spec, err_result, place
    w = world(enabled=True)
    place(w.ledger(), entry_spec(base_market(), "yes", "0.59"), err_result("unknown", None, "transport_error"))
    refused(w, w.run_bot(live=True, env="prod"), "unresolved_orders")
    assert "kpm exec reconcile" in w.text


def test_live_is_refused_while_another_live_run_is_active(world):
    w = world(enabled=True)
    conn = w.connect()
    now = w.clock.time()
    conn.execute("INSERT INTO bot_runs VALUES ('other-live', 'late_favorite', 'live', ?, NULL, NULL, '{}')", (now - 600,))
    conn.execute("INSERT INTO bot_heartbeat VALUES ('other-live', ?, 'monitoring', '{}')", (now - 3,))
    conn.commit()
    conn.close()
    assert w.run_bot(live=True, env="prod") == cli.EXIT_FAIL
    assert "another live run is active" in w.text and "other-live" in w.text and w.t.calls == []
    assert w.q("SELECT COUNT(*) AS n FROM bot_runs")[0]["n"] == 1


def test_a_second_live_start_while_the_first_still_waits_at_the_prompt_is_refused(world):
    """Review finding: the guard looked for another live run's heartbeat, which only exists once its bot has started, so two
    live starts that both sat at the confirmation prompt both went live (two bots on one account, two trades per window)."""
    w = world(enabled=True)
    calls, second = [], {}

    def confirm(prompt):
        calls.append(prompt)
        if len(calls) == 1:                                   # run A is still typing: run B is started in another terminal
            before = len(w.out)
            second["rc"] = w.run_bot(live=True, env="prod")
            second["text"] = "\n".join(w.out[before:])
        return PHRASE

    w.deps.confirm = confirm
    assert w.run_bot(live=True, env="prod", hours=0.1) == 0, w.text
    assert second["rc"] == cli.EXIT_FAIL and len(calls) == 1                  # B was refused before it could even ask
    assert "another live run is active" in second["text"] and "LIVE.lock" in second["text"]
    assert "pid " in second["text"]                                           # and says who holds the lock
    assert w.q("SELECT COUNT(*) AS n FROM bot_runs")[0]["n"] == 1             # only A ever started
    assert len(w.sim.created) == 1 and len(w.t.writes) == 1                   # one order: A's


def test_the_live_lock_is_released_when_the_run_ends_however_it_ends(world):
    w = world(enabled=True)
    assert w.run_bot(live=True, env="prod", hours=0.1) == 0, w.text
    assert (w.tmp / "LIVE.lock").exists()                                     # next to the kill file
    assert w.run_bot(live=True, env="prod", hours=0.1) == 0, w.text           # a later run takes it again: nothing stale
    w.trading["enabled"] = False                                              # a start that fails a gate releases it too
    assert w.run_bot(live=True, env="prod") == cli.EXIT_FAIL
    lock = cli.LiveRunLock(w.tmp / "LIVE.lock")
    assert lock.acquire("check")[0] is True
    lock.release()


def test_a_held_live_lock_refuses_a_live_start_but_never_a_watch_run(world):
    w = world(enabled=True)
    held = cli.LiveRunLock(w.tmp / "LIVE.lock")
    assert held.acquire("another process")[0] is True
    try:
        assert w.run_bot(live=True, env="prod") == cli.EXIT_FAIL
        assert "another live run is active" in w.text and "another process" in w.text and w.t.calls == []
        assert w.q("SELECT COUNT(*) AS n FROM bot_runs")[0]["n"] == 0
        w2 = world(enabled=True, keys=False)
        assert w2.run_bot() == 0                                              # watch-only never needs the lock
    finally:
        held.release()
    assert w.run_bot(live=True, env="prod", hours=0.1) == 0, w.text            # and once it is released, a live start works


def test_the_live_lock_object_refuses_a_second_holder_and_releases_cleanly(tmp_path):
    a, b = cli.LiveRunLock(tmp_path / "d" / "LIVE.lock"), cli.LiveRunLock(tmp_path / "d" / "LIVE.lock")
    assert a.acquire("first run")[0] is True
    ok, why = b.acquire("second run")
    assert ok is False and "first run" in why and f"pid {__import__('os').getpid()}" in why
    b.release()                                                               # releasing a lock never held is harmless
    assert a.acquire("again")[0] is False                                     # not re-entrant: a second open conflicts
    a.release()
    assert b.acquire("second run")[0] is True
    b.release()
    assert cli.live_lock_path({"kill_switch_file": str(tmp_path / "x" / "KILL")}) == tmp_path / "x" / "LIVE.lock"


def test_a_live_run_that_registers_while_we_wait_at_the_prompt_is_caught_by_the_second_look(world):
    """The lock is the main defence; the heartbeat check is repeated after the confirmation as a second line."""
    w = world(enabled=True)

    def confirm(prompt):
        conn = w.connect()
        now = w.clock.time()
        conn.execute("INSERT INTO bot_runs VALUES ('other-live', 'late_favorite', 'live', ?, NULL, NULL, '{}')", (now - 5,))
        conn.execute("INSERT INTO bot_heartbeat VALUES ('other-live', ?, 'monitoring', '{}')", (now - 1,))
        conn.commit()
        conn.close()
        return PHRASE

    w.deps.confirm = confirm
    assert w.run_bot(live=True, env="prod") == cli.EXIT_FAIL
    assert "another live run is active" in w.text and "other-live" in w.text
    assert w.sim.created == [] and w.t.writes == []
    assert w.q("SELECT COUNT(*) AS n FROM bot_runs")[0]["n"] == 1             # ours never started


def test_a_database_that_cannot_be_read_is_not_taken_to_mean_no_other_live_run(world):
    """Review finding: any sqlite error in the guard meant 'no other run': it failed open."""
    import sqlite3

    class Flaky:
        def __init__(self, conn):
            self._c = conn

        def execute(self, sql, *a):
            if "FROM bot_runs r JOIN bot_heartbeat" in sql:
                raise sqlite3.OperationalError("database is locked")
            return self._c.execute(sql, *a)

        def __getattr__(self, name):
            return getattr(self._c, name)

    w = world(enabled=True)
    w.deps.connect = lambda: Flaky(w.connect())
    assert w.run_bot(live=True, env="prod") == cli.EXIT_FAIL
    assert "cannot check whether another live run is active" in w.text and "database is locked" in w.text
    assert w.t.calls == [] and w.sim.created == []
    assert w.q("SELECT COUNT(*) AS n FROM bot_runs")[0]["n"] == 0


def test_a_live_runs_connection_waits_at_most_ten_seconds_for_a_database_lock(world, monkeypatch):
    """Review finding: the connection could wait up to 300 s for a lock between the last check and the send."""
    from kpm.execution.botstate import BotState
    seen = {}
    real_start = BotState.start

    def spy(self, strategy, mode, *a, **k):
        seen[mode] = self.conn.execute("PRAGMA busy_timeout").fetchone()[0]
        return real_start(self, strategy, mode, *a, **k)

    monkeypatch.setattr(BotState, "start", spy)
    w = world(enabled=True)
    assert w.run_bot(live=True, env="prod", hours=0.1) == 0, w.text
    assert seen["live"] == cli.LIVE_BUSY_TIMEOUT_MS == 10_000
    assert w.run_bot() == 0
    assert seen["watch"] == 300_000                                           # a watch run keeps the library default


def test_a_401_after_the_startup_probe_stops_the_live_run_and_the_exit_code_says_so(world):
    """The reviewer's reproduction on the real wire: the startup balance probe works, then every signed shard-scoped read
    answers 401 (a revoked key, or a clock past Kalshi's tolerance). The run used to go on for ever with last_error empty
    and exit code 0; it must stop, say why, and fail."""
    import json
    w = world(enabled=True)
    w.t.get("/portfolio/balance", (401, {"error": {"code": "authentication_error", "message": "bad signature"}}),
            params={"exchange_index": 2})
    rc = w.run_bot(live=True, env="prod", hours=0.5)
    assert rc == cli.EXIT_FAIL and w.sim.created == [] and w.t.writes == []
    assert w.kill.exists() and "authentication refused" in w.kill.read_text(encoding="utf-8")
    assert w.q("SELECT end_reason FROM bot_runs")[0]["end_reason"] not in cli.CLEAN_END_REASONS
    hb = json.loads(w.q("SELECT detail_json FROM bot_heartbeat")[0]["detail_json"])
    assert "authentication refused" in hb["last_error"] and hb["status"] == "stopped"
    shard_reads = [c for c in w.t.calls if c.endpoint == "/portfolio/balance" and c.params == {"exchange_index": 2}]
    assert len(shard_reads) <= 2                                              # it stopped at the first 401, it did not loop
    assert "run ended:" in w.text


def test_a_crashed_live_run_with_a_stale_heartbeat_does_not_block_a_new_one(world):
    w = world(enabled=True)
    conn = w.connect()
    now = w.clock.time()
    conn.execute("INSERT INTO bot_runs VALUES ('crashed', 'late_favorite', 'live', ?, NULL, NULL, '{}')", (now - 3600,))
    conn.execute("INSERT INTO bot_heartbeat VALUES ('crashed', ?, 'monitoring', '{}')", (now - 120,))
    conn.commit()
    conn.close()
    assert w.run_bot(live=True, env="prod", hours=0.1) == 0, w.text


def test_a_watch_run_is_never_blocked_by_a_live_one(world):
    w = world(enabled=True, keys=False)
    conn = w.connect()
    now = w.clock.time()
    conn.execute("INSERT INTO bot_runs VALUES ('other-live', 'late_favorite', 'live', ?, NULL, NULL, '{}')", (now - 600,))
    conn.execute("INSERT INTO bot_heartbeat VALUES ('other-live', ?, 'monitoring', '{}')", (now - 3,))
    conn.commit()
    conn.close()
    assert w.run_bot() == 0


# ============================================================================================ live: a whole session
def test_a_full_live_session_through_every_real_module(world, ed25519_key):
    w = world(enabled=True)
    assert w.run_bot(live=True, env="prod", hours=0.1) == 0, w.text

    # the wire: exactly one write, the order, shaped and signed as the design says, and never a cancel
    (post,) = w.t.writes
    assert (post.method, post.endpoint) == ("POST", "/portfolio/events/orders")
    coid = post.body["client_order_id"]
    assert post.body == {"ticker": TICKER, "client_order_id": coid, "side": "bid", "price": "0.7300", "count": "1.00",
                         "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross",
                         "exchange_index": 2}
    assert post.signed and post.headers["KALSHI-ACCESS-KEY"] == TEST_KEY_ID
    verify_signature(post, ed25519_key)
    assert post.presign_text().endswith("POST/trade-api/v2/portfolio/events/orders")
    assert not [c for c in w.t.calls if c.method == "DELETE"]
    assert all(c.signed for c in w.t.calls if c.endpoint.startswith("/portfolio/"))     # portfolio reads are signed ...
    assert not any(c.signed for c in w.t.calls if not c.endpoint.startswith("/portfolio/"))   # ... market data is not

    # the ledger: intent, fill, settlement (integer cents), each fee once
    (o,) = w.q("SELECT * FROM exec_orders")
    assert (o["side"], o["price"], o["count"], o["outcome"], o["time_in_force"]) == ("bid", "0.7300", "1.00", "yes",
                                                                                      "immediate_or_cancel")
    assert (o["status"], o["fill_count"], o["exchange_index"], o["purpose"]) == ("executed", "1.00", 2, "entry")
    (f,) = w.q("SELECT * FROM exec_fills")
    assert (f["outcome_side"], f["count"], f["yes_price"], f["no_price"]) == ("yes", "1.00", "0.7300", "0.2700")
    assert D(f["fee_cost"]) == FEE_73
    (s,) = w.q("SELECT * FROM exec_settlements")
    assert (s["market_result"], s["revenue_cents"], s["check_status"]) == ("yes", 100, "ok")
    assert D(s["pnl"]) == 1 - D("0.73") - FEE_73

    # the dashboard rows: the paper twin at the detection price and the live fill side by side
    (run,) = w.q("SELECT * FROM bot_runs")
    assert (run["strategy"], run["mode"], run["end_reason"]) == ("late_favorite", "live", "finished")
    (opp,) = w.q("SELECT * FROM bot_opportunities")
    assert (opp["kind"], opp["outcome"], opp["live"]) == ("late favorite YES", "filled", 1)
    assert opp["paper_cost"] == pytest.approx(0.73) and opp["live_cost"] == pytest.approx(0.73)
    assert opp["live_fees"] == pytest.approx(float(FEE_73)) and opp["latency_ms"] is not None
    pnl = float(1 - D("0.73") - FEE_73)
    assert opp["realized_pnl"] == pytest.approx(pnl) and opp["paper_realized_pnl"] == pytest.approx(pnl)
    (bo,) = w.q("SELECT * FROM bot_orders")
    assert (bo["dry_run"], bo["status"], bo["filled"], bo["client_order_id"]) == (0, "executed", 1.0, coid)
    assert w.q("SELECT status FROM bot_heartbeat")[0]["status"] == "stopped"
    assert any(r["action"] == "startup" for r in w.q("SELECT action FROM audit_log"))

    # the money numbers the cap uses are consistent: a settled win leaves nothing open and no loss
    led = w.ledger("reader")
    assert led.realized_loss_total() == 0 and led.open_worst_case() == 0 and led.realized_pnl() == D(str(pnl)).quantize(
        D("0.000001")) and led.unresolved_orders() == [] and led.open_orders() == []

    # nothing secret anywhere: not the key id, not the key path, not a signature, not a header name
    blob = w.everything()
    secrets = [TEST_KEY_ID, str(w.pem), "secret_dir", "PRIVATE KEY", "KALSHI-ACCESS"]
    secrets += [c.headers["KALSHI-ACCESS-SIGNATURE"] for c in w.t.calls if c.signed]
    for secret in secrets:
        assert secret not in blob, secret
    assert "key_fp" in w.text


def test_a_live_run_that_does_not_fill_retries_up_to_three_times_then_leaves_the_window(world):
    w = world(enabled=True)
    w.sim.script = ["nofill", "nofill", "nofill", "nofill"]                              # the 4th must never be sent
    assert w.run_bot(live=True, env="prod", hours=0.1) == 0, w.text
    assert len(w.sim.created) == 3                                                       # three attempts, each a new intent
    assert len({b["client_order_id"] for b in w.sim.created}) == 3
    assert {b["price"] for b in w.sim.created} == {"0.7300"}
    (opp,) = w.q("SELECT * FROM bot_opportunities")
    assert opp["outcome"] == "none_filled" and opp["realized_pnl"] is None
    assert [r["status"] for r in w.q("SELECT status FROM exec_orders ORDER BY created_ts")] == ["canceled"] * 3
    assert w.q("SELECT COUNT(*) AS n FROM exec_settlements")[0]["n"] == 0


def test_the_third_attempt_can_fill_and_a_window_never_fills_twice(world):
    w = world(enabled=True)
    w.sim.script = ["nofill", "nofill", "fill", "fill"]
    assert w.run_bot(live=True, env="prod", hours=0.1) == 0, w.text
    assert len(w.sim.created) == 3 and len(w.sim.fills) == 1
    assert w.q("SELECT outcome FROM bot_opportunities")[0]["outcome"] == "filled"


def test_a_lost_response_is_looked_up_and_never_resent(world):
    w = world(enabled=True)
    w.sim.script = ["timeout_placed"]                                                    # the order filled; the reply was lost
    assert w.run_bot(live=True, env="prod", hours=0.1) == 0, w.text
    assert len(w.sim.created) == 1                                                       # found by client_order_id, not resent
    (o,) = w.q("SELECT * FROM exec_orders")
    assert o["status"] == "executed" and o["fill_count"] == "1.00"
    assert w.q("SELECT outcome FROM bot_opportunities")[0]["outcome"] == "filled"
    assert not w.kill.exists()


def test_an_order_that_is_lost_and_not_found_trips_safety_and_stops(world):
    w = world(enabled=True)
    w.sim.script = ["timeout_lost"]
    assert w.run_bot(live=True, env="prod", hours=0.1) == cli.EXIT_FAIL
    assert len(w.sim.created) == 1                                                       # never resent
    assert w.kill.exists() and "safety trip" in w.kill.read_text()                       # every process stops
    assert w.q("SELECT outcome FROM bot_opportunities")[0]["outcome"] == "unknown"
    assert "run ended:" in w.text


def test_the_loss_cap_stops_the_bot_before_it_sends_an_order_that_could_break_it(world):
    from .exec_fakes import seed_realized_loss
    w = world(enabled=True)
    seed_realized_loss(w.ledger(), "4.80")                                               # $0.20 of the $5 cap left
    rc = w.run_bot(live=True, env="prod", hours=0.1)
    assert w.sim.created == []                                                           # a 0.73 + fee order does not fit
    assert rc == cli.EXIT_FAIL
    assert w.kill.exists()                                                               # the bot tripped Safety: auto-stop


def test_a_kill_file_created_during_the_run_stops_it_without_an_order(world):
    w = world(enabled=True)
    real_sleep = w.deps.sleep

    def sleeper(s):
        real_sleep(s)
        if w.clock.now >= CLOSE_TS - 125:                                                # the STOP button, just before the window
            w.kill.write_text("2026-10-05T18:28:31+00:00 dashboard STOP button\n")

    w.deps.sleep = sleeper
    assert w.run_bot(live=True, env="prod", hours=0.1) == cli.EXIT_FAIL
    assert w.sim.created == []
    assert w.q("SELECT end_reason FROM bot_runs")[0]["end_reason"] == "kill switch"


def test_ctrl_c_runs_the_cancel_path_once_and_leaves_no_write_but_the_order(world):
    w = world(enabled=True)
    real_sleep = w.deps.sleep

    def sleeper(s):
        real_sleep(s)
        if w.sim.created and w.clock.now > CLOSE_TS - 50:
            raise KeyboardInterrupt

    w.deps.sleep = sleeper
    assert w.run_bot(live=True, env="prod", hours=0.1) == cli.EXIT_INTERRUPTED
    assert "interrupted (Ctrl+C)" in w.text
    assert len(w.sim.created) == 1 and len(w.t.writes) == 1                              # nothing rested, so nothing to cancel
    assert w.q("SELECT end_reason FROM bot_runs")[0]["end_reason"] == "interrupted (Ctrl+C)"
    assert any(r["action"] == "cancel_path" for r in w.q("SELECT action FROM audit_log"))


def test_the_exit_guard_cancels_what_rests_even_when_the_bot_dies_before_its_own_cleanup(world, monkeypatch):
    from kpm.execution.botstate import BotState

    def boom(self, *a, **k):
        raise RuntimeError("the database is locked")

    w = world(enabled=True)
    w.rest_an_order()                                                                    # left over from an earlier run
    monkeypatch.setattr(BotState, "start", boom)                                         # dies before bot.run() has a try/finally
    assert w.run_bot(live=True, env="prod", hours=0.1) == cli.EXIT_FAIL
    assert "the run ended with an error" in w.text and "the database is locked" in w.text
    assert w.sim.cancels == [("sim-rest-1", {"exchange_index": 2})]                      # the guard's cancel path found and cancelled it
    assert w.sim.created == []


@pytest.fixture
def our_signal_handlers():
    """Replaces SIGTERM / SIGBREAK handlers with a sentinel for the test and puts the originals back afterwards."""
    import signal
    names = [n for n in ("SIGTERM", "SIGBREAK") if hasattr(signal, n)]

    def sentinel(signum, frame):
        pass

    originals = {n: signal.signal(getattr(signal, n), sentinel) for n in names}
    try:
        yield SimpleNamespace(names=names, sentinel=sentinel)
    finally:
        for n, h in originals.items():
            signal.signal(getattr(signal, n), h)


def test_the_exit_guard_signal_handlers_are_installed_for_the_run_and_restored(world, our_signal_handlers):
    import signal
    w = world(enabled=True)
    w.deps.install_signals = True
    seen = {}
    real_sleep = w.deps.sleep

    def sleeper(s):
        real_sleep(s)
        if w.sim.created:                                    # inside the bot run (startup's clock probes sleep earlier)
            seen.setdefault("during", {n: signal.getsignal(getattr(signal, n)) for n in our_signal_handlers.names})

    w.deps.sleep = sleeper
    assert w.run_bot(live=True, env="prod", hours=0.1) == 0, w.text
    sentinel = our_signal_handlers.sentinel
    assert all(h is not sentinel for h in seen["during"].values())                       # the guard's own handler ran the show
    assert all(signal.getsignal(getattr(signal, n)) is sentinel for n in our_signal_handlers.names)   # and ours is back


def test_a_termination_signal_ends_a_live_run_like_ctrl_c_and_cancels(world, our_signal_handlers):
    import signal
    w = world(enabled=True)
    w.deps.install_signals = True
    real_sleep = w.deps.sleep
    name = our_signal_handlers.names[0]

    def sleeper(s):
        real_sleep(s)
        if w.sim.created:
            signal.getsignal(getattr(signal, name))(getattr(signal, name), None)         # what the OS does on SIGTERM / Ctrl+Break

    w.deps.sleep = sleeper
    assert w.run_bot(live=True, env="prod", hours=0.1) == cli.EXIT_INTERRUPTED
    assert "interrupted (Ctrl+C)" in w.text and len(w.sim.created) == 1
    assert w.q("SELECT end_reason FROM bot_runs")[0]["end_reason"] == "interrupted (Ctrl+C)"
    assert any(r["action"] == "cancel_path" for r in w.q("SELECT action FROM audit_log"))
