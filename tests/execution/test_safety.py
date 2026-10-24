"""kpm.execution.safety: ceilings, live gates, and every per-order check. Offline, fake exchange, fake clock.

The rules under test are the user's decisions: one account-wide $5.00 loss cap (realized + open worst case), at most 1
contract and $1.00 of collateral per order, $3.00 of open exposure, IOC entries priced exactly at the current ask from a
fresh book, reduce_only IOC exits within 2 ticks, Thursday 02:55-05:05 ET refusal, per-shard exchange status, and every
live gate (enabled, --live, KALSHI_ENV=prod, --env prod, typed "LIVE PROD 5" at a TTY, no kill file, clock skew <= 2 s).
"""
from __future__ import annotations

import builtins
import copy
import io
import sys
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest

from kpm.execution import units
from kpm.execution.client import AuthError
from kpm.execution.safety import (ALLOWED_SERIES, CEILINGS, FLOORS, Gate, LiveGateError, Safety, SafetyConfigError,
                                  in_maintenance, kill_file_path, next_tick, parse_limits, ticks_from_touch,
                                  write_kill_file)

from .exec_fakes import (CLOSE_TS, HOURLY_TICKER, NOW, SHARD, TICKER, Clock, FakeClient, ScriptedFailure, base_market,
                         entry_spec, err_result, ex_order, ex_settlement, exit_spec, hourly_market, iso, make_book,
                         make_ledger, ok_result, place, seed_fill, seed_realized_loss)

WORST_YES_59 = D("0.59") + units.worst_fee(D(1), D("0.59"))            # 0.6071: collateral + the worst-case fee
WORST_YES_999 = D("0.999") + units.worst_fee(D(1), D("0.999"))         # 0.9992: the same at the highest allowed price
PHRASE = "LIVE PROD 5"


def utc(y, mo, d, h=0, mi=0, s=0) -> float:
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp()


class World:
    """A started, live Safety on a fake exchange, 90 s before a KXBTC15M close (a Monday), with $29.43 on shard 2."""

    def __init__(self, tmp_path, *, cfg=None, started=True, client=None, now=NOW):
        self.clock = Clock(now)
        self.ledger = make_ledger(tmp_path, self.clock)
        self.client = client or FakeClient(self.clock)
        self.kill = tmp_path / "KILL"
        config = {"enabled": True, "kill_switch_file": str(self.kill)}
        config.update(cfg or {})
        self.safety = Safety(config, self.ledger, self.client, self.clock, sleep=self.clock.sleep,
                             isatty=lambda: True)
        if started:
            self.safety.startup(live=True, env="prod", confirm=PHRASE)
            self.client.calls.clear()

    # -- what the strategies hand over
    @property
    def market(self) -> dict:
        return copy.deepcopy(self.client.markets[TICKER])

    def book(self, **kw) -> dict:
        kw.setdefault("fetched_at", self.clock.now - 0.3)
        return make_book(TICKER, **kw)

    def entry(self, outcome="yes", price=None, **over) -> dict:
        price = price or {"yes": "0.59", "no": "0.42"}[outcome]
        return entry_spec(self.market, outcome, price, **over)

    def check(self, spec=None, book=None, market=None):
        return self.safety.check_entry(spec if spec is not None else self.entry(),
                                       book if book is not None else self.book(),
                                       market if market is not None else self.market)

    def check_exit(self, spec=None, book=None, market=None):
        return self.safety.check_exit(spec if spec is not None else exit_spec(self.market),
                                      book if book is not None else self.book(),
                                      market if market is not None else self.market)

    def at(self, ts: float, close_in: float = 600.0, **market_over) -> None:
        """Move the clock to ``ts`` (UTC epoch) and give the market a close ``close_in`` seconds later."""
        self.clock.now = ts
        close = datetime.fromtimestamp(ts + close_in, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.client.markets[TICKER] = base_market(close_time=close, **market_over)
        self.safety.invalidate()

    def advance(self, seconds: float, keep_open: float = 600.0) -> None:
        """Advance the clock and keep the market open ``keep_open`` more seconds (so time-to-close never interferes)."""
        self.at(self.clock.now + seconds, keep_open)


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def refused(result, *needles):
    ok, why = result
    assert ok is False, f"expected a refusal, got ({ok!r}, {why!r})"
    for n in needles:
        assert n.lower() in why.lower(), f"{n!r} not in refusal: {why}"
    return why


def allowed(result):
    assert result == (True, ""), result


# ============================================================================================ configuration
def test_defaults_are_the_users_decisions():
    lim = parse_limits({})
    assert (lim.max_loss_usd, lim.max_cost_per_order_usd, lim.max_contracts_per_order, lim.max_open_exposure_usd) == (
        D("5.00"), D("1.00"), D("1"), D("3.00"))
    assert lim.max_orders_per_minute == 10 and lim.allowed_series == ("KXBTC15M", "KXBTCD")
    assert (lim.price_min, lim.price_max, lim.exit_max_ticks, lim.max_clock_skew_s) == (D("0.001"), D("0.999"), 2, 2.0)
    assert (FLOORS["price_min"], CEILINGS["price_max"]) == (D("0.001"), D("0.999"))      # 2026-10-06: take 99-99.9c favorites


def test_the_price_band_may_be_narrowed_but_never_widened_past_0_001_to_0_999():
    """The hard limits themselves are valid values (inclusive); a narrower band, such as the earlier 0.01..0.99, still is."""
    edge = parse_limits({"price_min": "0.001", "price_max": "0.999"})
    assert (edge.price_min, edge.price_max) == (D("0.001"), D("0.999"))
    narrow = parse_limits({"price_min": "0.01", "price_max": "0.99"})
    assert (narrow.price_min, narrow.price_max) == (D("0.01"), D("0.99"))
    for bad in ({"price_max": "1"}, {"price_max": "0.9991"}, {"price_min": "0"}, {"price_min": "0.0009"}):
        with pytest.raises(SafetyConfigError):
            parse_limits(bad)


def test_the_real_settings_file_is_valid(tmp_path):
    import yaml
    path = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))["trading"]
    lim = parse_limits(cfg)
    assert lim.max_loss_usd == D("5.0") and lim.max_cost_per_order_usd == D("1.0") and lim.max_contracts_per_order == 1
    assert lim.max_orders_per_minute == 10 and lim.kill_switch_file == "data/KILL"
    clock = Clock()
    s = Safety(cfg, make_ledger(tmp_path, clock), FakeClient(clock), clock)          # the YAML float 5.0 -> "LIVE PROD 5"
    assert s.confirmation_phrase == PHRASE and s.limits.max_open_exposure_usd == D("3.00")


@pytest.mark.parametrize("key, bad", [
    ("max_loss_usd", 5.01), ("max_loss_usd", "6"), ("max_loss_usd", 50),
    ("max_cost_per_order_usd", 1.01), ("max_contracts_per_order", 2), ("max_contracts_per_order", "1.01"),
    ("max_open_exposure_usd", 3.01), ("max_orders_per_minute", 11), ("max_orders_per_hour", 121),
    ("max_book_age_s", 5.5), ("max_clock_skew_s", 2.5), ("exit_max_ticks", 3), ("price_max", "0.9995"),
    ("status_ttl_s", 31), ("markets_ttl_s", 31), ("account_ttl_s", 31),
])
def test_a_value_above_a_ceiling_refuses_to_start(tmp_path, key, bad):
    with pytest.raises(SafetyConfigError, match="above the hard ceiling"):
        parse_limits({key: bad})
    clock = Clock()
    with pytest.raises(SafetyConfigError):
        Safety({key: bad}, make_ledger(tmp_path, clock), FakeClient(clock), clock)


@pytest.mark.parametrize("key, bad", [
    ("min_seconds_to_close", 19), ("exit_min_seconds_to_close", 1), ("price_min", "0.0005"),
    ("cash_buffer_usd", "0.04"), ("lockout_s", 59), ("settle_sweep_after_s", 10), ("settle_sweep_retry_s", 30),
])
def test_a_value_below_a_floor_refuses_to_start(key, bad):
    with pytest.raises(SafetyConfigError, match="below the hard floor"):
        parse_limits({key: bad})


@pytest.mark.parametrize("cfg", [
    {"max_loss_usd": 0}, {"max_loss_usd": -1}, {"max_loss_usd": "abc"}, {"max_loss_usd": None}, {"max_loss_usd": True},
    {"max_loss_usd": float("nan")}, {"max_orders_per_minute": 2.5}, {"max_contracts_per_order": "0.001"},
    {"price_min": "0.5", "price_max": "0.4"}, {"early_close_buffer_s": -1}, {"kill_switch_file": 5},
])
def test_malformed_configuration_refuses_to_start(cfg):
    with pytest.raises(SafetyConfigError):
        parse_limits(cfg)


def test_only_the_whitelisted_series_are_possible():
    assert ALLOWED_SERIES == ("KXBTC15M", "KXBTCD")
    assert parse_limits({"allowed_series": ["KXBTC15M"]}).allowed_series == ("KXBTC15M",)       # narrowing is fine
    for bad in (["KXNFLGAME"], ["KXBTC15M", "KXUSDJPY15M"], [], "KXBTC15M", [1]):
        with pytest.raises(SafetyConfigError):
            parse_limits({"allowed_series": bad})


def test_lowering_a_cap_is_allowed_and_yaml_floats_are_exact():
    lim = parse_limits({"max_loss_usd": 2.5, "max_cost_per_order_usd": 0.5, "max_contracts_per_order": 0.5,
                        "max_open_exposure_usd": 1})
    assert (lim.max_loss_usd, lim.max_cost_per_order_usd, lim.max_contracts_per_order, lim.max_open_exposure_usd) == (
        D("2.5"), D("0.5"), D("0.5"), D("1"))


def test_the_whole_settings_dict_is_accepted_too(tmp_path):
    clock = Clock()
    s = Safety({"trading": {"max_loss_usd": 3}, "kalshi": {}}, make_ledger(tmp_path, clock), FakeClient(clock), clock)
    assert s.limits.max_loss_usd == 3


def test_ceilings_and_floors_are_read_only():
    with pytest.raises(TypeError):
        CEILINGS["max_loss_usd"] = D("50")                       # type: ignore[index]
    with pytest.raises(TypeError):
        FLOORS["lockout_s"] = 1                                  # type: ignore[index]


def test_kill_file_path_is_relative_to_the_project_root(tmp_path):
    from kpm.core.config import PROJECT_ROOT
    assert kill_file_path({}) == Path(PROJECT_ROOT) / "data" / "KILL"
    assert kill_file_path({"kill_switch_file": "x/STOP"}) == Path(PROJECT_ROOT) / "x" / "STOP"
    assert kill_file_path({"kill_switch_file": str(tmp_path / "K")}) == tmp_path / "K"
    assert kill_file_path({}, tmp_path / "O") == tmp_path / "O"


# ============================================================================================ startup gates
def test_watch_only_start_needs_no_gates_and_never_allows_an_order(tmp_path):
    w = World(tmp_path, started=False, client=FakeClient(Clock(), live=False, has_keys=False))
    report = w.safety.startup(live=False, env=None, confirm=None)
    assert report["ok"] and report["mode"] == "watch" and w.safety.started and not w.safety.live
    refused(w.check(), "watch-only")
    refused(w.check_exit(), "watch-only")
    assert w.client.calls == []                                     # nothing was even read


def test_a_live_client_without_the_live_flag_is_refused(tmp_path):
    w = World(tmp_path, started=False)                              # FakeClient(live=True)
    with pytest.raises(LiveGateError) as e:
        w.safety.startup(live=False, env=None)
    assert [g.name for g in e.value.gates if not g.ok] == ["watch_only"] and not w.safety.started


def test_a_full_live_start_passes_every_gate_and_is_audited(tmp_path):
    w = World(tmp_path, started=False)
    report = w.safety.startup(live=True, env="prod", confirm=PHRASE, out=lambda s: None)
    assert report["ok"] and w.safety.started and w.safety.live and w.ledger.live is True
    names = [g["name"] for g in report["gates"]]
    assert names == ["trading_enabled", "live_flag", "env_var", "env_flag", "keys", "kill_file", "loss_cap",
                     "unresolved_orders", "tty", "confirmation", "clock_skew", "auth_probe", "kill_file_final"]
    assert all(g["ok"] for g in report["gates"])
    (ev,) = w.ledger._rows("SELECT * FROM audit_log WHERE action = 'startup'")
    assert ev["component"] == "exec.safety" and "LIVE PROD" not in ev["details"]
    assert w.client.calls_to("server_time_skew") and w.client.calls_to("balance") == [{"exchange_index": None}]


def _enabled_false(w):
    w.safety.enabled = False


def _demo_env(w):
    w.client.env.name = "demo"


def _keys_missing(w):
    w.client.env.has_keys = False


def _client_not_live(w):
    w.client.live = False


def _kill_present(w):
    w.kill.write_text("x")


def _loss_used_up(w):
    seed_realized_loss(w.ledger, "5.00")


def _unknown_order(w):
    place(w.ledger, entry_spec(base_market(ticker="KXBTC15M-OLD-1")), err_result("unknown"))


STATIC_GATES = [("trading_enabled", _enabled_false, "enabled"), ("env_var", _demo_env, "prod"),
                ("keys", _keys_missing, "key"), ("keys", _client_not_live, "live=True"),
                ("kill_file", _kill_present, "kill file"), ("loss_cap", _loss_used_up, "used up"),
                ("unresolved_orders", _unknown_order, "kpm exec reconcile")]


@pytest.mark.parametrize("gate, break_it, needle", STATIC_GATES,
                         ids=["enabled-false", "env-not-prod", "no-keys", "client-not-live", "kill-file", "loss-cap",
                              "unknown-order"])
def test_each_static_gate_blocks_a_live_start_and_nothing_is_sent_or_asked(tmp_path, gate, break_it, needle):
    w = World(tmp_path, started=False)
    break_it(w)
    asked = []
    with pytest.raises(LiveGateError) as e:
        w.safety.startup(live=True, env="prod", confirm=lambda prompt: asked.append(prompt) or PHRASE)
    failed = {g.name: g for g in e.value.gates if not g.ok}
    assert gate in failed and needle.lower() in failed[gate].detail.lower()
    assert asked == [], "the user must not be asked to confirm while a gate has already failed"
    assert not w.safety.started and not w.safety.live
    refused(w.check(), "startup")
    assert w.client.calls_to("cancel_order") == [] and w.client.calls_to("cancel_all") == []


@pytest.mark.parametrize("flag", [None, "demo", "PROD", "prod ", True])
def test_the_env_flag_must_be_exactly_prod(tmp_path, flag):
    w = World(tmp_path, started=False)
    with pytest.raises(LiveGateError, match="env_flag"):
        w.safety.startup(live=True, env=flag, confirm=PHRASE)
    assert not w.safety.started


@pytest.mark.parametrize("typed", ["", "live prod 5", "LIVE PROD 4", "LIVE PROD", "LIVE PROD 5.0", "LIVE PROD  5",
                                   "YES", True, 5, None])
def test_only_the_exact_typed_phrase_confirms(tmp_path, monkeypatch, typed):
    w = World(tmp_path, started=False)
    if typed is None:                                               # no text given: it prompts with input()
        monkeypatch.setattr(builtins, "input", lambda prompt="": "nope")
    with pytest.raises(LiveGateError, match="confirmation"):
        w.safety.startup(live=True, env="prod", confirm=typed, out=lambda s: None)
    assert not w.safety.started


def test_the_phrase_follows_the_cap_and_typed_whitespace_is_trimmed(tmp_path):
    w = World(tmp_path, started=False, cfg={"max_loss_usd": 2.5})
    assert w.safety.confirmation_phrase == "LIVE PROD 2.5"
    with pytest.raises(LiveGateError):
        w.safety.startup(live=True, env="prod", confirm=PHRASE)
    w.safety.startup(live=True, env="prod", confirm="  LIVE PROD 2.5\n")
    assert w.safety.started
    assert World(tmp_path / "x", started=False).safety.confirmation_phrase == "LIVE PROD 5"


def test_no_tty_refuses_even_with_the_right_phrase(tmp_path, monkeypatch):
    w = World(tmp_path, started=False)
    with pytest.raises(LiveGateError) as e:
        w.safety.startup(live=True, env="prod", confirm=PHRASE, isatty=lambda: False)
    assert "tty" in {g.name for g in e.value.gates if not g.ok}
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))              # the real default test: a non-interactive stdin
    w2 = World(tmp_path / "b", started=False)
    w2.safety._isatty = None
    with pytest.raises(LiveGateError, match="tty"):
        w2.safety.startup(live=True, env="prod", confirm=PHRASE)


def test_the_prompt_shows_the_caps_and_never_the_key(tmp_path, monkeypatch):
    w = World(tmp_path, started=False)
    shown, asked = [], []
    w.safety.startup(live=True, env="prod", confirm=lambda p: asked.append(p) or PHRASE, out=shown.append)
    text = "\n".join(shown)
    assert '"LIVE PROD 5"' in asked[0]
    assert "loss cap $5" in text and "1 contract" in text and "$1.00 collateral" in text and "KXBTC15M" in text
    scrubbed = text
    for p in {str(tmp_path), str(tmp_path.resolve())}:           # macOS temp dirs live under /private/var
        scrubbed = scrubbed.replace(p, "")
    assert "key_fp deadbeef" in text and "PRIVATE" not in scrubbed.upper()
    w2 = World(tmp_path / "b", started=False)
    seen = []
    monkeypatch.setattr(builtins, "input", lambda prompt="": seen.append(prompt) or PHRASE)
    w2.safety.startup(live=True, env="prod", confirm=None, out=lambda s: None)       # prompts with input()
    assert len(seen) == 1 and w2.safety.started
    w3 = World(tmp_path / "c", started=False)
    w3.safety.startup(live=True, env="prod", confirm=lambda: PHRASE)                  # a zero-argument callable works
    assert w3.safety.started


def test_eof_at_the_prompt_is_a_refusal(tmp_path, monkeypatch):
    w = World(tmp_path, started=False)

    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr(builtins, "input", eof)
    with pytest.raises(LiveGateError, match="confirmation"):
        w.safety.startup(live=True, env="prod", confirm=None, out=lambda s: None)


@pytest.mark.parametrize("skew, ok", [(0.0, True), (0.5, True), (-1.9, True), (2.0, True), (-2.0, True),
                                      (2.01, False), (-2.01, False), (30, False), (float("nan"), False),
                                      (float("inf"), False), (None, False), ("1.0", False), (True, False)])
def test_clock_skew_must_be_within_two_seconds(tmp_path, skew, ok):
    w = World(tmp_path, started=False)
    w.client.skew = skew
    if ok:
        w.safety.startup(live=True, env="prod", confirm=PHRASE)
        assert w.safety.started
    else:
        with pytest.raises(LiveGateError, match="clock_skew"):
            w.safety.startup(live=True, env="prod", confirm=PHRASE)
        assert not w.safety.started


def test_a_skew_probe_that_fails_or_does_not_exist_refuses(tmp_path):
    w = World(tmp_path, started=False)
    w.client.fail["server_time_skew"] = ScriptedFailure("no Date header")
    with pytest.raises(LiveGateError, match="clock skew probe failed"):
        w.safety.startup(live=True, env="prod", confirm=PHRASE)
    w2 = World(tmp_path / "b", started=False)
    w2.client.server_time_skew = None                                  # an old client without the method
    with pytest.raises(LiveGateError, match="no server_time_skew"):
        w2.safety.startup(live=True, env="prod", confirm=PHRASE)


@pytest.mark.parametrize("reply", [{"skew_s": 0.4}, SimpleNamespace(skew_s=-0.4)], ids=["mapping", "object"])
def test_skew_may_be_reported_as_a_mapping_or_object(tmp_path, reply):
    w = World(tmp_path, started=False)
    w.client.skew = reply
    w.safety.startup(live=True, env="prod", confirm=PHRASE)
    assert w.safety.started


def test_a_start_with_old_unrecorded_settlements_sweeps_them_before_declaring_the_cap_used_up(tmp_path):
    w = World(tmp_path, started=False)
    for i in range(9):                                                    # 9 x ~0.61 worst case = 5.46 > 5.00 "used"
        seed_fill(w.ledger, ticker=f"KXBTC15M-OLD-{i}", order_id=f"o{i}", fill_id=f"f{i}")
    w.at(NOW + 300)
    for i in range(9):
        w.client.settlement_rows.append(ex_settlement(f"KXBTC15M-OLD-{i}", result="yes", revenue=100,
                                                      fee=str(units.taker_fee(D(1), D("0.59")))))
    w.safety.startup(live=True, env="prod", confirm=PHRASE)                # they were wins that were never recorded
    assert w.safety.started and len(w.ledger.settled_tickers()) == 9


def test_a_settlement_mismatch_found_by_the_start_gate_is_reported_but_trips_nothing(tmp_path):
    w = World(tmp_path, started=False)
    for i in range(9):
        seed_fill(w.ledger, ticker=f"KXBTC15M-OLD-{i}", order_id=f"o{i}", fill_id=f"f{i}")
    w.at(NOW + 300)
    for i in range(9):                                                    # we hold YES and it won, yet revenue is 0
        w.client.settlement_rows.append(ex_settlement(f"KXBTC15M-OLD-{i}", result="yes", revenue=0,
                                                      fee=str(units.taker_fee(D(1), D("0.59")))))
    d = w.safety.diagnostics("prod")                                      # a check command: no kill file, no halt
    assert not w.safety.halted and not w.kill.exists()
    assert any(r["details"] for r in w.ledger._rows("SELECT details FROM audit_log WHERE action = 'settle'"))


def test_a_401_on_the_probe_says_the_key_and_environment_do_not_match(tmp_path):
    w = World(tmp_path, started=False)
    w.client.fail["balance"] = AuthError("GET /portfolio/balance: HTTP 401", status=401)
    with pytest.raises(LiveGateError, match="authentication refused"):
        w.safety.startup(live=True, env="prod", confirm=PHRASE)
    w2 = World(tmp_path / "b", started=False)
    w2.client.script["balance"] = [{"balance": 5}]                      # no balance_dollars
    with pytest.raises(LiveGateError, match="auth_probe"):
        w2.safety.startup(live=True, env="prod", confirm=PHRASE)


def test_the_kill_file_is_checked_again_after_the_prompt(tmp_path):
    w = World(tmp_path, started=False)

    def type_then_kill(prompt):
        w.kill.write_text("stop")
        return PHRASE

    with pytest.raises(LiveGateError, match="kill_file_final"):
        w.safety.startup(live=True, env="prod", confirm=type_then_kill)


def test_diagnostics_reports_without_confirmation_or_orders(tmp_path):
    w = World(tmp_path, started=False)
    d = w.safety.diagnostics("prod")
    assert d["ok"] is True
    names = {c["name"] for c in d["checks"]}
    assert {"config", "environment", "keys", "kill_file", "maintenance", "loss_cap", "exchange_status", "clock_skew",
            "auth_probe", "unresolved_orders"} <= names
    assert not w.safety.started and w.client.calls_to("cancel_all") == []
    w.kill.write_text("x")
    w.client.skew = 9
    bad = {c["name"] for c in w.safety.diagnostics("demo")["checks"] if not c["ok"]}
    assert bad == {"environment", "kill_file", "clock_skew"}


# ============================================================================================ entries: the happy path
def test_a_valid_yes_entry_passes(w):
    allowed(w.check())
    assert w.safety.last_refusal is None and w.safety.refusals == 0


def test_a_valid_no_entry_passes(w):
    allowed(w.check(w.entry("no", "0.42")))


def test_the_check_order_and_the_number_of_network_reads_are_minimal(w):
    allowed(w.check())
    assert [n for n, _ in w.client.calls] == ["open_markets", "exchange_status", "balance", "positions", "orders"]
    assert w.client.calls_to("balance") == [{"exchange_index": SHARD}]        # the market's own shard, never the total
    assert w.client.calls_to("open_markets")[0]["series"] == "KXBTC15M"       # the event hint tries the right series first
    assert w.client.calls_to("orders") == [{"ticker": TICKER, "status": "resting"}]


def test_the_next_check_uses_the_cache(w):
    allowed(w.check())
    w.client.calls.clear()
    allowed(w.check())
    assert w.client.calls == []


def test_the_cache_does_not_grow_without_bound_over_a_long_run(w):
    for i in range(600):                                                    # a new ticker every 15 minutes for days
        w.safety._put(("listed", f"KXBTC15M-T{i}"), w.clock.now + i * 900.0, {"ticker": f"T{i}"})
        w.clock.now += 900.0
    assert len(w.safety._cache) <= 260


def test_preflight_warms_the_caches(w):
    assert w.safety.preflight(w.market) is None
    w.client.calls.clear()
    allowed(w.check())
    assert w.client.calls == []
    assert w.safety.preflight({"ticker": TICKER}) == "market has no ticker or exchange_index"


def test_caches_expire(w):
    allowed(w.check())
    w.clock.advance(16)                                                      # past the 15 s markets TTL and 10 s account TTL
    w.client.calls.clear()
    allowed(w.check())
    assert {n for n, _ in w.client.calls} == {"open_markets", "exchange_status", "balance", "positions", "orders"}


def test_new_ledger_activity_on_the_ticker_refreshes_the_exchange_view(w):
    allowed(w.check())
    w.client.calls.clear()
    place(w.ledger, w.entry(), err_result("rejected", 400))                  # an attempt on this ticker (not placed)
    w.clock.advance(1)
    allowed(w.check())
    assert [n for n, _ in w.client.calls] == ["positions", "orders"]


def test_a_sub_cent_price_is_allowed_where_the_grid_has_sub_cent_ticks(w):
    book = w.book(yes=(("0.940", "5"),), no=(("0.054", "9"),))               # YES ask 0.946: band [0.9, 1.0] step 0.001
    allowed(w.check(w.entry("yes", "0.946"), book))
    book = w.book(yes=(("0.056", "5"),), no=(("0.940", "9"),))               # NO ask 0.944 -> YES price 0.056
    allowed(w.check(w.entry("no", "0.944"), book))


def test_a_yes_favorite_at_99_3_cents_passes_on_the_tapered_grid(w):
    """User decision 2026-10-06: take 99-99.9c favorites. 0.993 is a tick of KXBTC15M's 0.001 band [0.9, 1.0], inside the
    0.001..0.999 bounds, and its $0.993 collateral is under the $1.00 per-order ceiling."""
    assert w.market["price_level_structure"] == "tapered_deci_cent" and units.on_grid(D("0.993"), w.market["price_ranges"])
    spec = w.entry("yes", "0.993")
    assert (spec["side"], spec["yes_price"]) == ("bid", D("0.993"))
    allowed(w.check(spec, w.book(yes=(("0.990", "5"),), no=(("0.007", "9"),))))           # YES ask = 1 - best NO bid 0.007
    assert w.safety.last_refusal is None and w.safety.refusals == 0


def test_a_no_favorite_at_99_3_cents_passes_as_an_ask_at_the_yes_price_0_007(w):
    """Buying NO at 0.993 is selling YES at 1 - 0.993: side 'ask', price '0.0070' (never 'ask' at 0.993, which would buy NO
    at 0.007). units.to_book is the only conversion, and Safety passes exactly that order."""
    side, yes_price = units.to_book("no", D("0.993"))
    assert (side, units.fmt_price(yes_price)) == ("ask", "0.0070")
    spec = w.entry("no", "0.993")
    assert (spec["side"], spec["yes_price"]) == ("ask", D("0.007"))
    book = w.book(yes=(("0.007", "5"),), no=(("0.990", "9"),))                         # NO ask = 1 - best YES bid 0.007
    allowed(w.check(spec, book))
    raw = {"ticker": TICKER, "exchange_index": SHARD, "side": "ask", "price": "0.0070", "count": "1.00",
           "time_in_force": "immediate_or_cancel"}                                       # the request body's own form
    allowed(w.check(raw, book))
    raw["price"] = "0.9930"                                                               # the wrong side: NO at 0.007
    refused(w.check(raw, book), "not the current NO ask 0.993")
    spec["side"], spec["yes_price"] = "bid", D("0.993")                                   # YES at 0.993 labelled as the NO order
    refused(w.check(spec, book), "does not match units.to_book")


def test_a_raw_side_and_price_spec_is_accepted_when_it_is_consistent(w):
    spec = {"ticker": TICKER, "exchange_index": SHARD, "side": "ask", "price": "0.5800", "count": "1.00",
            "time_in_force": "immediate_or_cancel"}                           # buys NO at 0.42
    allowed(w.check(spec))


def test_a_spec_with_a_body_is_read_from_both_places(w):
    spec = w.entry()
    spec["body"] = {"ticker": TICKER, "client_order_id": "c-1", "side": "bid", "price": "0.5900", "count": "1.00",
                    "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross",
                    "exchange_index": SHARD}
    allowed(w.check(spec))
    spec["body"]["count"] = "2.00"
    refused(w.check(spec), "disagree on count")


# ============================================================================================ entries: the order itself
def test_buying_no_with_the_yes_book_side_at_the_no_price_is_refused(w):
    """'ask' at 0.01 buys NO at 0.99: 99x the collateral. Only units.to_book may convert."""
    spec = w.entry("no", "0.42")
    spec["side"], spec["yes_price"] = "bid", D("0.42")                        # the wrong-side order
    refused(w.check(spec), "does not match units.to_book")
    spec = w.entry("no", "0.01")
    spec["side"], spec["yes_price"] = "ask", D("0.01")
    refused(w.check(spec), "units.to_book")


def test_a_raw_wrong_side_order_cannot_pass_the_book_check_either(w):
    spec = {"ticker": TICKER, "exchange_index": SHARD, "side": "ask", "price": "0.4200", "count": "1.00",
            "time_in_force": "immediate_or_cancel"}                           # that is NO at 0.58, but the NO ask is 0.42
    refused(w.check(spec), "not the current NO ask")


@pytest.mark.parametrize("patch, needle", [
    ({"count": D("2")}, "per-order limit"), ({"count": D("1.01")}, "per-order limit"),
    ({"count": D("0.001")}, "steps of 0.01"), ({"count": D("0")}, "steps of 0.01"), ({"count": D("-1")}, "steps of 0.01"),
    ({"count": 1.0}, "float"), ({"count": "abc"}, "not a decimal"), ({"count": True}, "bool"),
    ({"outcome_price": 0.59}, "float"), ({"yes_price": 0.59}, "float"),
    ({"time_in_force": "good_till_canceled"}, "only immediate_or_cancel"),
    ({"time_in_force": "fill_or_kill"}, "only immediate_or_cancel"),
    ({"time_in_force": None}, "only immediate_or_cancel"),
    ({"post_only": True}, "post_only"), ({"expiration_time": 1791225100}, "expiration_time"),
    ({"reduce_only": True}, "must not be reduce_only"), ({"reduce_only": "yes"}, "bool"),
    ({"exchange_index": None}, "exchange_index"), ({"exchange_index": True}, "exchange_index"),
    ({"ticker": ""}, "no ticker"), ({"outcome": "maybe"}, "yes' or 'no"), ({"side": "yes"}, "does not match"),
    ({"self_trade_prevention_type": "cancel_both"}, "self_trade_prevention_type"),
])
def test_malformed_or_forbidden_specs_are_refused(w, patch, needle):
    spec = w.entry()
    spec.update(patch)
    refused(w.check(spec), needle)


def test_outcome_without_a_price_is_refused(w):
    spec = w.entry()
    spec["outcome_price"] = None
    refused(w.check(spec), "together")
    bare = {"ticker": TICKER, "exchange_index": SHARD, "time_in_force": "immediate_or_cancel", "count": "1"}
    refused(w.safety.check_entry(bare, w.book(), w.market), "side")


def test_prices_outside_0_001_to_0_999_are_refused_even_on_a_finer_grid(w):
    """The user's bounds are 0.001-0.999 (YES price). Every tick of both real grids lies inside them (the finest step is
    0.001), so they only bite on a grid finer than Kalshi's today: on a 0.0001 grid 0.9995 and 0.0005 ARE ticks, and are still
    refused, by the bounds and not by the grid. The bounds themselves pass on that grid."""
    fine = [{"start": "0.0000", "end": "1.0000", "step": "0.0001"}]
    w.client.markets[TICKER] = base_market(price_ranges=fine)
    w.safety.invalidate()
    for price in ("0.9995", "0.0005", "0.9999", "0.0001"):
        assert units.on_grid(D(price), fine)
    refused(w.check(w.entry("yes", "0.9995"), w.book(yes=(("0.990", "5"),), no=(("0.0005", "9"),))), "outside 0.001..0.999")
    refused(w.check(w.entry("yes", "0.0005"), w.book(yes=(("0.0004", "5"),), no=(("0.9995", "9"),))), "outside 0.001..0.999")
    refused(w.check(w.entry("no", "0.9995"), w.book(yes=(("0.0005", "5"),), no=(("0.990", "9"),))), "outside 0.001..0.999")  # YES 0.0005
    refused(w.check(w.entry("no", "0.0005"), w.book(yes=(("0.9995", "5"),), no=(("0.0004", "9"),))), "outside 0.001..0.999")  # YES 0.9995
    refused(w.check(w.entry("yes", "0.9999"), w.book(yes=(("0.990", "5"),), no=(("0.0001", "9"),))), "outside 0.001..0.999")
    assert w.safety.refusals == 5
    allowed(w.check(w.entry("yes", "0.999"), w.book(yes=(("0.990", "5"),), no=(("0.001", "9"),))))      # the top bound itself
    allowed(w.check(w.entry("no", "0.999"), w.book(yes=(("0.001", "5"),), no=(("0.990", "9"),))))       # YES 0.001: the bottom bound
    allowed(w.check(w.entry("yes", "0.001"), w.book(yes=(("0.0005", "5"),), no=(("0.999", "9"),))))     # (a long shot is still inside)


def test_a_price_of_99_95_cents_is_refused_on_the_tapered_grid(w):
    """0.9995 is past the highest tick of KXBTC15M's grid (0.999) and past the 0.999 bound; the bound meets it first. 0.9985
    lies inside the bounds but between two ticks (0.998 and 0.999), so the grid itself refuses it, never snapping it."""
    ranges = w.market["price_ranges"]
    assert not units.on_grid(D("0.9995"), ranges) and not units.on_grid(D("0.9985"), ranges)
    assert units.on_grid(D("0.998"), ranges) and units.on_grid(D("0.999"), ranges)
    refused(w.check(w.entry("yes", "0.9995"), w.book(yes=(("0.990", "5"),), no=(("0.0005", "9"),))), "outside 0.001..0.999")
    refused(w.check(w.entry("yes", "0.9985"), w.book(yes=(("0.990", "5"),), no=(("0.0015", "9"),))), "price grid")
    refused(w.check(w.entry("no", "0.9995"), w.book(yes=(("0.0005", "5"),), no=(("0.990", "9"),))), "outside 0.001..0.999")
    refused(w.check(w.entry("no", "0.9985"), w.book(yes=(("0.0015", "5"),), no=(("0.990", "9"),))), "price grid")   # YES 0.0015
    assert w.safety.refusals == 4


def test_a_sub_cent_price_at_the_extremes_is_refused_on_a_whole_cent_grid(w):
    """0.001 ticks near 0 and 1 are a feature of KXBTC15M's tapered grid. A KXBTCD-style whole-cent market has no tick at 0.993
    (nor at 0.007, the YES price of a NO buy at 0.993): the grid refuses it, it is never snapped to 0.99 or 1.00."""
    market = hourly_market()
    w.client.markets[HOURLY_TICKER] = market
    assert not units.on_grid(D("0.993"), market["price_ranges"]) and units.on_grid(D("0.99"), market["price_ranges"])

    def check(outcome, price, yes, no):
        book = make_book(HOURLY_TICKER, yes=yes, no=no, fetched_at=w.clock.now - 0.2)
        return w.safety.check_entry(entry_spec(market, outcome, price), book, market)

    refused(check("yes", "0.993", (("0.990", "5"),), (("0.007", "9"),)), "price grid")      # YES ask 0.993
    refused(check("no", "0.993", (("0.007", "5"),), (("0.990", "9"),)), "price grid")       # NO ask 0.993 = YES price 0.007
    allowed(check("yes", "0.99", (("0.980", "5"),), (("0.010", "9"),)))                     # the whole cent beside it passes
    allowed(check("no", "0.99", (("0.010", "5"),), (("0.980", "9"),)))                      # NO 0.99 = YES price 0.01


@pytest.mark.parametrize("outcome, book", [
    ("yes", dict(yes=(("0.990", "5"),), no=(("0.001", "9"),))),         # YES ask 0.999: a bid at the YES price 0.999
    ("no", dict(yes=(("0.001", "5"),), no=(("0.990", "9"),))),          # NO ask 0.999: an ask at the YES price 0.001
])
def test_the_worst_case_of_a_99_9_cent_buy_stays_inside_the_one_dollar_per_order_ceiling(w, outcome, book):
    """0.999 is the highest price the bounds allow. Its collateral plus the exact taker fee (0.07 x 0.999 x 0.001, rounded
    up to $0.000001) and even Safety's padded worst case (the fee rounded up to $0.0001, plus one step) stay under the
    $1.00 per-order ceiling, so one trade never risks more than the dollar the user accepted. Safety budgets exactly that
    worst case: the loss cap's boundary sits at $5.00 - 0.9992 (compare test_the_loss_cap_boundary_is_exact)."""
    price, ceiling = D("0.999"), CEILINGS["max_cost_per_order_usd"]
    fee = units.taker_fee(D(1), price)
    assert fee == D("0.000070") and price + fee == D("0.999070") and price + fee <= ceiling == D("1.00")
    assert WORST_YES_999 == D("0.9992") and WORST_YES_999 <= ceiling
    spec = w.entry(outcome, "0.999")
    assert units.collateral(spec["side"], spec["yes_price"], spec["count"]) == price
    allowed(w.check(spec, w.book(**book)))
    seed_realized_loss(w.ledger, str(D("5") - WORST_YES_999))                     # 4.0008: used + worst == 5.0000
    allowed(w.check(spec, w.book(**book)))
    seed_realized_loss(w.ledger, "0.0001", "KXBTC15M-OLD-2")                      # one more 0.0001: 5.0001
    refused(w.check(spec, w.book(**book)), "loss cap:", "> $5")


def test_a_price_off_the_markets_grid_is_refused_never_snapped(w):
    book = w.book(yes=(("0.570", "5"),), no=(("0.575", "9"),))                # YES ask 0.425: not a 0.01 tick in [0.1, 0.9]
    refused(w.check(w.entry("yes", "0.425"), book), "grid")
    market = hourly_market()                                                  # linear_cent: whole cents only
    w.client.markets[HOURLY_TICKER] = market
    book = make_book(HOURLY_TICKER, yes=(("0.440", "5"),), no=(("0.445", "9"),), fetched_at=w.clock.now - 0.2)
    refused(w.safety.check_entry(entry_spec(market, "yes", "0.555"), book, market), "grid")


def test_the_hourly_market_passes_on_whole_cents(w):
    market = hourly_market()
    w.client.markets[HOURLY_TICKER] = market
    book = make_book(HOURLY_TICKER, yes=(("0.440", "5"),), no=(("0.450", "9"),), fetched_at=w.clock.now - 0.2)
    allowed(w.safety.check_entry(entry_spec(market, "yes", "0.55"), book, market))
    assert w.client.calls_to("open_markets")[0]["series"] == "KXBTCD"           # its event ticker names the series first


def test_collateral_per_order_is_capped(tmp_path):
    w = World(tmp_path, cfg={"max_cost_per_order_usd": 0.5})
    refused(w.check(), "collateral")
    allowed(w.check(w.entry("no", "0.42")))                                   # 0.42 collateral fits under 0.50


def test_a_fractional_contract_cap_is_enforced(tmp_path):
    w = World(tmp_path, cfg={"max_contracts_per_order": 0.5})
    refused(w.check(), "per-order limit")
    allowed(w.check(w.entry(count="0.5")))


# ============================================================================================ entries: the order book
def test_slow_cold_reads_age_the_book_so_the_first_attempt_is_refused_and_the_next_one_passes(w):
    """The book's age is measured after the network reads, so what it costs to warm the caches counts against it."""
    w.client.latency_s = 0.6                                        # five cold reads: 3 s
    refused(w.check(), "stale")
    w.client.latency_s = 0.0
    allowed(w.check())                                              # caches are warm; a fresh book passes at once


def test_a_stale_book_is_refused_and_the_age_limit_is_inclusive(w):
    refused(w.check(book=w.book(fetched_at=w.clock.now - 2.5)), "stale")
    allowed(w.check(book=w.book(fetched_at=w.clock.now - 2.0)))
    refused(w.check(book=w.book(fetched_at=w.clock.now - 2.001)), "stale")


def test_book_problems_are_refused(w):
    refused(w.safety.check_entry(w.entry(), None, w.market), "no order book")
    book = w.book()
    del book["fetched_at"]
    refused(w.check(book=book), "fetched_at")
    refused(w.check(book=w.book(fetched_at=w.clock.now + 30)), "future")
    refused(w.check(book=make_book(HOURLY_TICKER, fetched_at=w.clock.now - 0.1)), "order book is for")
    refused(w.check(book=w.book(no=(("0.50", "5"), ("0.49", "5")))), "unusable order book")   # descending: best unknown


def test_a_crossed_or_locked_book_never_prices_an_order(w):
    refused(w.check(w.entry("yes", "0.59"), w.book(yes=(("0.60", "5"),), no=(("0.41", "5"),))), "crossed or locked")
    refused(w.check(w.entry("yes", "0.59"), w.book(yes=(("0.59", "5"),), no=(("0.41", "5"),))), "crossed or locked")
    refused(w.check(w.entry("no", "0.41"), w.book(yes=(("0.59", "5"),), no=(("0.41", "5"),))), "crossed or locked")
    allowed(w.check(w.entry("yes", "0.60"), w.book(yes=(("0.58", "5"),), no=(("0.40", "5"),))))   # a 2 c spread is normal


def test_an_empty_side_means_there_is_no_ask(w):
    refused(w.check(book=w.book(no=())), "no NO bids")                         # YES ask = 1 - best NO bid
    refused(w.check(w.entry("no", "0.42"), w.book(yes=())), "no YES bids")
    refused(w.check(book={"ticker": TICKER, "fetched_at": w.clock.now}), "no NO bids")


def test_zero_size_placeholders_are_not_quotes(w):
    book = w.book(no=(("0.40", "5"), ("0.41", "0")))                           # the 0.41 bid has no size: best is 0.40
    refused(w.check(w.entry("yes", "0.59"), book), "not the current YES ask 0.60")
    allowed(w.check(w.entry("yes", "0.60"), book))


@pytest.mark.parametrize("price", ["0.58", "0.60", "0.61", "0.01"])
def test_an_entry_must_be_priced_exactly_at_the_current_ask(w, price):
    refused(w.check(w.entry("yes", price)), "not the current YES ask 0.59", "exactly at the ask")


def test_the_ask_comes_from_the_opposite_side_of_the_book(w):
    """YES ask = 1 - best NO bid (the LAST element), NO ask = 1 - best YES bid. The first element is the worst level."""
    allowed(w.check(w.entry("yes", "0.59")))                                   # 1 - 0.41, not 1 - 0.39
    refused(w.check(w.entry("yes", "0.61")), "0.59")
    allowed(w.check(w.entry("no", "0.42")))                                    # 1 - 0.58, not 1 - 0.56
    refused(w.check(w.entry("no", "0.44")), "0.42")


# ============================================================================================ entries: market, shard, time
def test_a_market_outside_the_allowed_series_is_refused(w):
    w.client.series_of[TICKER] = "KXNFLGAME"
    refused(w.check(), "not in the open markets of the allowed series")


def test_the_event_ticker_is_only_a_hint_never_trusted(w):
    w.client.series_of[TICKER] = "KXNFLGAME"                                   # the market claims KXBTC15M but is not listed
    w.client.calls.clear()
    refused(w.check(), "allowed series")
    assert [c["series"] for c in w.client.calls_to("open_markets")] == ["KXBTC15M", "KXBTCD"]
    w.safety.invalidate()
    w.client.series_of[TICKER] = "KXBTCD"                                      # listed under the other allowed series
    allowed(w.check())


def test_a_ticker_that_is_not_listed_as_open_is_refused(w):
    spec, market = w.entry(), w.market
    w.client.markets.clear()
    refused(w.check(spec, market=market), "not in the open markets")


@pytest.mark.parametrize("status", ["closed", "determined", "finalized", "initialized", "inactive", "open", None])
def test_only_an_active_market_is_tradable(w, status):
    w.client.markets[TICKER] = base_market(status=status)
    refused(w.check(market=base_market(status=status)), "status")


def test_a_stale_market_object_is_refused(w):
    # the listing is matched by ticker (no close-time filter is sent: OpenAPI allows those only with status closed), so a
    # market object with another close_time or grid is caught by comparing it with the fresh listing
    refused(w.check(market=base_market(close_time="2026-10-05T18:45:00Z")), "stale", "close_time")
    refused(w.check(market=base_market(close_time="2026-10-05T18:30:01Z")), "stale")     # one second off is already stale
    allowed(w.check(market=base_market(close_time="2026-10-05T18:30:00.000Z")))          # the same instant, other format
    refused(w.check(market=base_market(price_ranges=[{"start": "0", "end": "1", "step": "0.01"}])), "stale")
    refused(w.check(market=base_market(ticker=HOURLY_TICKER)), "not 'KXBTC15M")


def test_the_orders_shard_must_be_the_markets_shard(w):
    refused(w.check(w.entry(exchange_index=0)), "exchange_index 0")
    refused(w.check(market=base_market(exchange_index=0)), "exchange_index")
    w.client.markets[TICKER] = base_market(exchange_index=0)                    # the fresh listing says another shard
    refused(w.check(market=base_market()), "exchange_index")


def test_per_shard_exchange_status_decides_not_the_top_level_flags(w):
    w.client.status["trading_active"] = False                                   # shard 0's flags (top level)
    w.client.status["exchange_index_statuses"][0]["trading_active"] = False
    w.safety.invalidate()
    allowed(w.check())                                                          # shard 2 is trading
    w.client.status["exchange_index_statuses"][2]["trading_active"] = False
    w.safety.invalidate()
    refused(w.check(), "shard 2", "trading_active")
    w.client.status["exchange_index_statuses"][2].update(trading_active=True, exchange_active=False)
    w.safety.invalidate()
    refused(w.check(), "shard 2")
    del w.client.status["exchange_index_statuses"][2]                           # no entry for the shard: closed
    w.safety.invalidate()
    refused(w.check(), "missing")
    w.client.status.pop("exchange_index_statuses")
    w.safety.invalidate()
    refused(w.check(), "shard 2")


def test_the_status_read_is_cached_briefly_then_refreshed(w):
    allowed(w.check())
    w.client.status["exchange_index_statuses"][2]["trading_active"] = False
    w.clock.advance(3)
    allowed(w.check())                                                          # still inside the 5 s TTL
    w.clock.advance(3)
    refused(w.check(), "shard 2")


def test_time_to_close_with_the_early_close_buffer(w):
    w.at(CLOSE_TS - 41, close_in=41)                                            # can_close_early: 30 s + 10 s buffer
    allowed(w.check())
    w.at(CLOSE_TS - 39, close_in=39)
    refused(w.check(), "closes in 39 s", "40 s required")
    w.at(CLOSE_TS - 31, close_in=31, can_close_early=False)                     # no early close: 30 s
    allowed(w.check())
    w.at(CLOSE_TS - 29, close_in=29, can_close_early=False)
    refused(w.check(), "closes in 29 s", "30 s required")
    w.at(CLOSE_TS + 5, close_in=-5, can_close_early=False)                      # after the close
    refused(w.check(), "closes in -5 s")


def test_the_late_favorite_window_is_still_tradable(w):
    """The decision window ends 60 s before close: that must pass the 30 s + 10 s early-close buffer."""
    w.at(CLOSE_TS - 60, close_in=60)
    allowed(w.check())


# ============================================================================================ entries: Thursday maintenance
@pytest.mark.parametrize("when, blocked", [
    (utc(2026, 10, 8, 6, 54, 59), False),        # Thu 02:54:59 EDT
    (utc(2026, 10, 8, 6, 55, 0), True),          # Thu 02:55:00 EDT
    (utc(2026, 10, 8, 7, 0, 0), True),           # Thu 03:00 EDT: Kalshi's pause begins
    (utc(2026, 10, 8, 8, 59, 59), True),         # Thu 04:59:59 EDT
    (utc(2026, 10, 8, 9, 5, 59), True),          # Thu 05:05:59 EDT: the 05:05 minute is still refused
    (utc(2026, 10, 8, 9, 6, 0), False),          # Thu 05:06:00 EDT
    (utc(2026, 10, 7, 7, 0, 0), False),          # the same time on a Wednesday
    (utc(2026, 10, 9, 7, 0, 0), False),          # ... and on a Friday
    (utc(2026, 11, 5, 8, 0, 0), True),           # Thu 03:00 EST (DST ended Nov 1): 08:00 UTC
    (utc(2026, 11, 5, 7, 0, 0), False),          # Thu 02:00 EST: 07:00 UTC is NOT inside the window in winter
    (utc(2026, 11, 5, 10, 6, 0), False),         # Thu 05:06 EST
    (utc(2026, 3, 12, 7, 0, 0), True),           # Thu 03:00 EDT after the spring change
])
def test_thursday_maintenance_window_in_eastern_time(w, when, blocked):
    assert in_maintenance(when) is blocked
    w.at(when)
    if blocked:
        refused(w.check(), "maintenance")
    else:
        allowed(w.check())


def test_maintenance_refuses_exits_too(w):
    seed_fill(w.ledger)
    w.at(utc(2026, 10, 8, 7, 0, 0))
    refused(w.check_exit(), "maintenance")


# ============================================================================================ entries: rate limits, kill, lockout
def test_the_per_minute_rate_limit(w):
    for _ in range(10):
        place(w.ledger, w.entry(), err_result("rejected", 400))                 # every ATTEMPT counts, placed or not
    refused(w.check(), "rate limit", "10 orders in the last minute")
    w.advance(61)
    allowed(w.check())


def test_nine_orders_a_minute_is_still_allowed(w):
    for _ in range(9):
        place(w.ledger, w.entry(), err_result("rejected", 400))
    allowed(w.check())


def test_the_hourly_rate_limit(tmp_path):
    w = World(tmp_path, cfg={"max_orders_per_hour": 5})
    for _ in range(5):
        place(w.ledger, w.entry(), err_result("rejected", 400))
        w.advance(70)
    refused(w.check(), "last hour")
    w.advance(3600)
    allowed(w.check())


def test_there_is_no_session_cap_unless_configured():
    assert parse_limits({}).max_orders_per_session is None
    assert parse_limits({"max_orders_per_session": None}).max_orders_per_session is None
    assert parse_limits({"max_orders_per_session": 300}).max_orders_per_session == 300
    assert parse_limits({"max_orders_per_session": "300"}).max_orders_per_session == 300
    assert parse_limits({"max_orders_per_session": 300}).as_dict()["max_orders_per_session"] == 300


@pytest.mark.parametrize("bad", [0, -1, 2.5, "abc", True, float("nan")])
def test_a_malformed_session_cap_refuses_to_start(bad):
    with pytest.raises(SafetyConfigError):
        parse_limits({"max_orders_per_session": bad})


def test_the_session_cap_counts_this_runs_orders_only_and_is_not_a_rate(tmp_path):
    w = World(tmp_path, cfg={"max_orders_per_session": 3})
    other = make_ledger(w.ledger._log_path.parent, w.clock, run_id="run-2")      # another process, same database
    for _ in range(4):
        place(other, w.entry(), err_result("rejected", 400))
    allowed(w.check())                                                          # another run's orders are not ours
    for _ in range(2):
        place(w.ledger, w.entry(), err_result("rejected", 400))                 # every ATTEMPT counts, placed or not
        w.advance(70)
    allowed(w.check())                                                          # two of three used
    place(w.ledger, w.entry(), err_result("rejected", 400))
    w.advance(70)
    refused(w.check(), "session order cap", "3 orders", "max 3")
    w.advance(7200)
    refused(w.check(), "session order cap")                                     # a cap on the run, not a rate


def test_the_session_cap_never_blocks_an_exit(tmp_path):
    w = World(tmp_path, cfg={"max_orders_per_session": 1})
    seed_fill(w.ledger)                                                         # a position to unwind (1 order of this run)
    w.advance(70)
    refused(w.check(), "session order cap")
    allowed(w.check_exit())                                                     # exits only reduce risk


def test_the_session_cap_is_in_the_banner(tmp_path):
    capped = World(tmp_path, cfg={"max_orders_per_session": 300})
    assert any("300 per run" in line for line in capped.safety.banner())
    (tmp_path / "b").mkdir()
    assert not any("per run" in line for line in World(tmp_path / "b").safety.banner())


def test_the_rate_limit_counts_every_run_in_the_environment(w):
    other = make_ledger(w.ledger._log_path.parent, w.clock, run_id="run-2")      # another process, same database
    for _ in range(10):
        place(other, w.entry(), err_result("rejected", 400))
    refused(w.check(), "rate limit")


def test_the_kill_file_blocks_every_order_and_removing_it_resumes_checks(w):
    allowed(w.check())
    w.kill.write_text("2026-10-05T18:28:31+00:00 dashboard STOP button\n")
    assert w.safety.kill_active()
    refused(w.check(), "kill switch")
    refused(w.check_exit(), "kill switch")
    w.kill.unlink()
    assert not w.safety.kill_active()
    allowed(w.check())


def test_an_unreadable_kill_path_counts_as_active(tmp_path, monkeypatch):
    w = World(tmp_path)

    def boom(self):
        raise PermissionError("denied")

    monkeypatch.setattr(type(w.safety.kill_path), "exists", boom)
    assert w.safety.kill_active() is True


def test_a_lockout_after_cancel_all_blocks_orders_for_60_seconds(w):
    w.ledger.set_lockout(w.clock.now + 60, "cancel_all after: kill")
    refused(w.check(), "lockout", "60 s")
    refused(w.check_exit(), "lockout")
    w.advance(59)
    refused(w.check(), "lockout")
    w.advance(2)
    allowed(w.check())


def test_trip_halts_orders_writes_the_kill_file_and_is_idempotent(w):
    w.safety.trip("order outcome unknown")
    assert w.safety.halted and w.safety.halt_reason == "order outcome unknown" and w.safety.kill_active()
    assert "safety trip: order outcome unknown" in w.kill.read_text()
    refused(w.check(), "halted", "order outcome unknown")
    refused(w.check_exit(), "halted")
    w.safety.trip("again")
    assert w.safety.halt_reason == "order outcome unknown" and w.kill.read_text().count("\n") == 1
    assert len(w.ledger._rows("SELECT details FROM audit_log WHERE action = 'trip'")) == 2


def test_trip_never_raises_even_if_the_kill_file_cannot_be_written(tmp_path):
    (tmp_path / "file.txt").write_text("a file, not a directory")
    w = World(tmp_path, cfg={"kill_switch_file": str(tmp_path / "file.txt" / "KILL")})
    w.safety.trip("x")
    assert w.safety.halted


def test_a_ledger_halt_trips_safety(w):
    w.ledger.halt("reconcile mismatch: position on X")
    assert w.safety.halted and w.safety.kill_active()
    refused(w.check(), "halted", "reconcile mismatch")


def test_write_kill_file_appends_a_timestamped_line(tmp_path):
    p = write_kill_file(tmp_path / "d" / "KILL", "first\nsecond")
    write_kill_file(p, "third")
    lines = p.read_text().splitlines()
    assert len(lines) == 2 and lines[0].endswith("first second") and lines[1].endswith("third")


# ============================================================================================ entries: ledger rules
def test_an_earlier_unknown_outcome_blocks_every_new_entry(w):
    place(w.ledger, entry_spec(base_market(ticker="KXBTC15M-OLD-1")), err_result("unknown"))
    refused(w.check(), "unknown outcome", "reconcile")


def test_an_open_order_on_the_same_ticker_and_side_blocks(w):
    resting = w.entry()
    resting["time_in_force"] = "good_till_canceled"
    coid = w.ledger.new_intent(resting)
    w.ledger.mark_result(coid, ok_result(coid, "ord-r", fill="0.00", remaining="1.00"))     # a resting GTC order
    refused(w.check(), "still open")


def test_the_loss_cap_boundary_is_exact(w):
    """realized + open worst case + the new order's worst case <= $5.00, and not one cent more."""
    seed_realized_loss(w.ledger, str(D("5") - WORST_YES_59))                     # 4.3929: used + worst == 5.0000
    allowed(w.check())
    seed_realized_loss(w.ledger, "0.0001", "KXBTC15M-OLD-2")                     # one more 0.0001: 5.0001
    refused(w.check(), "loss cap:", "> $5")


def test_the_cap_counts_open_positions_at_their_worst_case(w):
    seed_fill(w.ledger, ticker="KXBTC15M-OLD-1", order_id="o1", fill_id="f1")           # open: 0.59 + its exact fee
    open_worst = D("0.59") + units.taker_fee(D(1), D("0.59"))
    assert w.ledger.open_worst_case() == open_worst
    seed_realized_loss(w.ledger, str(D("5") - WORST_YES_59 - open_worst), "KXBTC15M-OLD-7")   # (another ticker)
    allowed(w.check())
    seed_realized_loss(w.ledger, "0.0001", "KXBTC15M-OLD-8")
    refused(w.check(), "loss cap")
    assert w.ledger.open_worst_case() == open_worst                              # the position is still unsettled


def test_an_old_loss_still_counts_because_the_cap_is_a_total_and_profits_offset_it(w):
    w.ledger.record_settlements([ex_settlement("KXBTC15M-OLD-1", result="no", yes_cost="4.70", revenue=0, fee="0.0000",
                                               settled_at=w.clock.now - 3 * 86400)])
    assert w.ledger.realized_loss_24h() == 0 and w.ledger.realized_loss_total() == D("4.70")
    refused(w.check(), "loss cap")                                               # 4.70 + 0.6071 > 5.00
    w.ledger.record_settlements([ex_settlement("KXBTC15M-OLD-2", result="yes", yes_cost="1.0000", revenue=300,
                                               fee="0.0000")])                   # +2.00 net
    assert w.ledger.realized_loss_total() == D("2.70")
    allowed(w.check())


def test_recent_losses_count_in_full_even_when_an_old_profit_offsets_them_in_the_total(w):
    """The realized figure is the LARGER of the 24 h and the all-time net loss, so neither window can hide a loss."""
    w.ledger.record_settlements([ex_settlement("KXBTC15M-OLD-1", result="yes", yes_cost="1.0000", revenue=400,
                                               fee="0.0000", settled_at=w.clock.now - 3 * 86400)])      # +3.00, old
    w.ledger.record_settlements([ex_settlement("KXBTC15M-OLD-2", result="no", yes_cost="4.50", revenue=0,
                                               fee="0.0000", settled_at=w.clock.now - 3600)])           # -4.50, recent
    assert w.ledger.realized_loss_total() == D("1.50") and w.ledger.realized_loss_24h() == D("4.50")
    assert w.safety.realized_loss() == D("4.50")
    refused(w.check(), "loss cap")                                               # 4.50 + 0.6071 > 5.00 although total is 1.50


def test_a_spent_cap_trips_safety(w):
    seed_realized_loss(w.ledger, "5.00")
    refused(w.check(), "loss cap reached")
    assert w.safety.halted and w.kill.exists()
    assert "realized loss" in w.safety.halt_reason


def test_the_exposure_cap(w):
    for i in range(4):                                                           # 4 x 0.59 = 2.36 open collateral
        seed_fill(w.ledger, ticker=f"KXBTC15M-OLD-{i}", order_id=f"o{i}", fill_id=f"f{i}")
    allowed(w.check())                                                           # 2.36 + 0.59 = 2.95 <= 3.00
    seed_fill(w.ledger, ticker="KXBTC15M-OLD-9", order_id="o9", fill_id="f9")    # 2.95 open
    refused(w.check(), "exposure cap")                                           # 2.95 + 0.59 > 3.00


def test_a_hedged_position_counts_only_its_net_exposure(w):
    seed_fill(w.ledger, ticker="KXBTC15M-OLD-1", order_id="o1", fill_id="f1", outcome="yes", price="0.59")
    seed_fill(w.ledger, ticker="KXBTC15M-OLD-1", order_id="o2", fill_id="f2", outcome="no", price="0.40")
    assert w.ledger.open_exposure() == 0                                         # 0.99 paid for a $1 payout
    fees = units.taker_fee(D(1), D("0.59")) + units.taker_fee(D(1), D("0.40"))
    assert w.ledger.open_worst_case() == D("0.99") + fees - 1
    allowed(w.check())


# ============================================================================================ entries: stale settlements
def stale_unsettled(w, n, *, age=200.0, price="0.59"):
    """n filled positions on earlier markets whose settlement was never recorded (the bot looked too early)."""
    for i in range(n):
        seed_fill(w.ledger, ticker=f"KXBTC15M-OLD-{i}", order_id=f"o{i}", fill_id=f"f{i}", price=price)
    w.advance(age)


def publish_settlements(w, n, *, result="yes", revenue=100, price="0.59"):
    for i in range(n):
        w.client.settlement_rows.append(ex_settlement(f"KXBTC15M-OLD-{i}", result=result, revenue=revenue,
                                                      yes_cost=price, fee=str(units.taker_fee(D(1), D(price)))))


def test_a_cap_that_would_refuse_first_settles_old_positions_the_bots_never_recorded(w):
    """The bots call settle() once. If Kalshi had not published the row yet, the position would block entries forever."""
    stale_unsettled(w, 5)                                                       # 5 x 0.59 = 2.95 open: 2.95 + 0.59 > 3.00
    publish_settlements(w, 5)
    allowed(w.check())
    assert w.ledger.settled_tickers() == {f"KXBTC15M-OLD-{i}" for i in range(5)} and w.ledger.open_exposure() == 0
    assert len(w.client.calls_to("settlements")) == 5
    assert w.ledger._rows("SELECT details FROM audit_log WHERE action = 'stale_settlements'")


def test_the_sweep_runs_only_when_a_cap_would_refuse(w):
    stale_unsettled(w, 2)
    publish_settlements(w, 2)
    allowed(w.check())
    assert w.client.calls_to("settlements") == [] and w.ledger.settled_tickers() == set()


def test_a_settlement_that_is_still_missing_keeps_the_cap_and_is_retried_at_most_every_two_minutes(w):
    stale_unsettled(w, 5)
    refused(w.check(), "exposure cap")
    assert len(w.client.calls_to("settlements")) == 5
    refused(w.check(), "exposure cap")
    assert len(w.client.calls_to("settlements")) == 5                           # throttled
    w.advance(121)
    refused(w.check(), "exposure cap")
    assert len(w.client.calls_to("settlements")) == 10
    publish_settlements(w, 5)
    w.advance(121)
    allowed(w.check())


def test_sweeping_cannot_be_used_to_dodge_the_loss_cap(w):
    stale_unsettled(w, 8, price="0.60")                                         # 8 x ~0.617 open worst case
    publish_settlements(w, 8, result="no", revenue=0, price="0.60")             # all lost: realized 4.94 after settling
    refused(w.check(), "loss cap:")
    assert w.ledger.realized_loss_total() > D("4.9") and not w.safety.halted


def test_a_settlement_that_does_not_match_our_fills_halts_during_the_sweep(w):
    stale_unsettled(w, 5)
    publish_settlements(w, 5, result="yes", revenue=0)                          # we hold YES and it won, yet revenue 0
    ok, why = w.check()
    assert ok is False and w.safety.halted and w.kill.exists()


def test_a_failing_settlement_read_does_not_break_the_check(w):
    stale_unsettled(w, 5)
    w.client.fail["settlements"] = ScriptedFailure("503")
    refused(w.check(), "exposure cap")


def test_a_trip_during_the_checks_is_never_a_pass(w):
    w.client.balances[SHARD] = "29.43"
    allowed(w.check())
    original = w.safety._check_book

    def trip_then_check(*a, **k):
        w.safety.trip("a reconcile mismatch arrived while the checks were running")
        return original(*a, **k)

    w.safety._check_book = trip_then_check
    refused(w.check(), "halted")


# ============================================================================================ entries: the account on the exchange
def test_low_cash_on_the_target_shard_refuses_even_if_the_total_looks_large(w):
    w.client.balances[SHARD] = "0.40"                                            # all-shard total is still $29.43
    refused(w.check(), "cash on shard 2", "0.40")
    assert w.client.calls_to("balance") == [{"exchange_index": SHARD}]
    w.client.balances[SHARD] = "0.65"                                            # needs 0.6071 + $0.05 = 0.6571
    w.safety.invalidate()
    refused(w.check(), "needs 0.6571")
    w.client.balances[SHARD] = "0.66"
    w.safety.invalidate()
    allowed(w.check())


def test_orders_sent_since_the_cash_reading_are_deducted_from_it(w):
    w.client.balances[SHARD] = "0.70"
    allowed(w.check())                                                           # needs 0.6571, has 0.70
    w.clock.advance(1)
    place(w.ledger, entry_spec(base_market(ticker="KXBTC15M-OLD-1")), order_id="o1")     # sent after the reading
    refused(w.check(), "cash on shard 2", "for orders sent since")


def test_a_position_the_ledger_does_not_know_refuses(w):
    w.client.exchange_positions[TICKER] = "1.00"                                 # a manual trade, or a missed fill
    refused(w.check(), "position on", "exchange 1.00 != ledger 0")


def test_a_position_that_matches_the_ledger_is_fine(w):
    seed_fill(w.ledger, ticker=TICKER, order_id="o1", fill_id="f1")
    w.client.exchange_positions[TICKER] = "1.00"
    allowed(w.check())


def test_a_resting_order_on_the_ticker_refuses(w):
    w.client.exchange_orders.append(ex_order("manual-1", "m-1", status="resting", fill="0.00", remaining="1.00"))
    refused(w.check(), "resting order")


# ============================================================================================ fail closed
@pytest.mark.parametrize("name", ["exchange_status", "open_markets", "balance", "positions", "orders"])
def test_a_failing_read_is_a_refusal_not_an_exception(w, name):
    w.client.fail[name] = ScriptedFailure("boom")
    ok, why = w.check()
    assert ok is False and "ScriptedFailure" in why
    w.client.fail.clear()
    allowed(w.check())                                                           # nothing was cached from the failure


def test_a_failing_ledger_is_a_refusal(w, monkeypatch):
    monkeypatch.setattr(w.ledger, "risk_snapshot", lambda: 1 / 0)
    ok, why = w.check()
    assert ok is False and "ZeroDivisionError" in why


class NoSnapshot:
    """A ledger without risk_snapshot(): Safety must fall back to the separate methods and get the same numbers."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        if name == "risk_snapshot":
            raise AttributeError(name)
        return getattr(self._inner, name)


def test_the_loss_rule_gives_the_same_numbers_without_the_one_pass_snapshot(tmp_path):
    w = World(tmp_path)
    seed_fill(w.ledger, ticker="KXBTC15M-OLD-1", order_id="o1", fill_id="f1")
    seed_realized_loss(w.ledger, "2.50", "KXBTC15M-OLD-2")
    plain_ledger = Safety({"enabled": True, "kill_switch_file": str(w.kill)}, NoSnapshot(w.ledger), w.client, w.clock)
    assert plain_ledger.realized_loss() == w.safety.realized_loss() == D("2.50")
    assert plain_ledger.loss_used() == w.safety.loss_used() == D("2.50") + D("0.59") + units.taker_fee(D(1), D("0.59"))
    assert plain_ledger._risk() == w.safety._risk()


@pytest.mark.parametrize("garbage", [None, "x", 1, [], {}, {"ticker": TICKER}])
def test_garbage_never_raises_and_never_passes(w, garbage):
    assert w.safety.check_entry(garbage, w.book(), w.market)[0] is False
    assert w.safety.check_exit(garbage, w.book(), w.market)[0] is False
    assert w.safety.check_entry(w.entry(), garbage, w.market)[0] is False
    assert w.safety.check_entry(w.entry(), w.book(), garbage)[0] is False


def test_an_unstarted_safety_allows_nothing(tmp_path):
    w = World(tmp_path, started=False)
    refused(w.check(), "startup() has not passed")
    refused(w.check_exit(), "startup() has not passed")
    assert w.client.calls == []


def test_refusals_are_counted_and_audited(w):
    w.check(w.entry("yes", "0.61"))
    assert w.safety.refusals == 1 and "not the current YES ask" in w.safety.last_refusal
    (row,) = w.ledger._rows("SELECT details FROM audit_log WHERE action = 'entry_refused'")
    assert TICKER in row["details"]
    st = w.safety.status()
    assert st["started"] and st["live"] and not st["halted"] and st["loss_used_usd"] == "0" and st["refusals"] == 1


def test_the_banner_shows_the_caps_and_no_key_material(w):
    text = "\n".join(w.safety.banner())
    assert "mode LIVE" in text and "$5" in text and "KXBTC15M, KXBTCD" in text and "deadbeef" in text
    assert str(w.kill) in text


# ============================================================================================ exits
def hold_yes(w, **kw):
    seed_fill(w.ledger, ticker=TICKER, outcome="yes", price="0.59", order_id="o-hold", fill_id="f-hold", **kw)


def hold_no(w):
    seed_fill(w.ledger, ticker=TICKER, outcome="no", price="0.42", order_id="o-hold", fill_id="f-hold")


def test_an_exit_at_the_touch_passes_without_the_entry_checks(w):
    hold_yes(w)
    allowed(w.check_exit())                                                      # buy NO at the NO ask 0.42, reduce_only
    assert [n for n, _ in w.client.calls] == ["open_markets", "exchange_status"]  # no cash, position or resting reads


@pytest.mark.parametrize("price, ok", [("0.42", True), ("0.43", True), ("0.44", True), ("0.45", False),
                                       ("0.41", False), ("0.99", False)])
def test_an_exit_may_be_at_most_two_ticks_through_the_touch(w, price, ok):
    hold_yes(w)
    result = w.check_exit(exit_spec(w.market, "no", price))
    allowed(result) if ok else refused(result, "within 2 ticks")


def test_exit_ticks_for_a_yes_buy_go_up_and_cross_the_band_edge_at_0_90(w):
    hold_no(w)
    book = w.book(yes=(("0.050", "5"),), no=(("0.100", "5"),))                    # YES ask = 0.900
    for price, ok in (("0.900", True), ("0.901", True), ("0.902", True), ("0.903", False), ("0.91", False)):
        result = w.check_exit(exit_spec(w.market, "yes", price), book)
        allowed(result) if ok else refused(result, "within 2 ticks")


def test_exit_ticks_for_a_no_buy_use_the_grid_of_the_band_being_entered(w):
    """A NO price p is a YES price 1 - p, so the NO grid mirrors the YES grid: from NO 0.100 the next tick is 0.110."""
    hold_yes(w)
    book = w.book(yes=(("0.900", "5"),), no=(("0.040", "5"),))                    # NO ask = 0.100, YES touch 0.900
    for price, ok in (("0.100", True), ("0.110", True), ("0.120", True), ("0.130", False)):
        result = w.check_exit(exit_spec(w.market, "no", price), book)
        allowed(result) if ok else refused(result, "within 2 ticks")
    refused(w.check_exit(exit_spec(w.market, "no", "0.101"), book), "grid")        # 0.101 is not a tick (YES 0.899)
    book = w.book(yes=(("0.100", "5"),), no=(("0.040", "5"),))                    # NO ask = 0.900, YES touch 0.100
    for price, ok in (("0.900", True), ("0.901", True), ("0.902", True), ("0.903", False)):   # YES 0.100, 0.099, 0.098
        result = w.check_exit(exit_spec(w.market, "no", price), book)
        allowed(result) if ok else refused(result, "within 2 ticks")


@pytest.mark.parametrize("outcome", ["yes", "no"])
def test_every_exit_limit_pair_h2_computes_is_accepted_and_one_more_tick_is_not(tmp_path, outcome):
    """pair_h2 prices an unwind with its own step_price(); if Safety counted ticks differently, unwinds at a band edge
    would be refused and a one-legged position would be left open. Compare them over the tapered grid."""
    pair_h2 = pytest.importorskip("kpm.execution.pair_h2")
    step_price = getattr(pair_h2, "step_price", None)
    if step_price is None:
        pytest.skip("pair_h2.step_price is not available")
    w = World(tmp_path)
    if outcome == "no":
        hold_yes(w)                                    # holding YES: the exit buys NO
    else:
        hold_no(w)                                     # holding NO: the exit buys YES
    ranges = base_market()["price_ranges"]
    asks = [D(x) for x in ("0.020", "0.098", "0.099", "0.100", "0.101", "0.102", "0.300", "0.890", "0.898", "0.899",
                           "0.900", "0.901", "0.902", "0.950", "0.980")]
    checked = 0
    for ask in asks:
        if outcome == "yes":                                          # YES ask = 1 - best NO bid
            book = w.book(yes=(("0.001", "5"),), no=((str(D(1) - ask), "5"),))
        else:                                                         # NO ask = 1 - best YES bid
            book = w.book(yes=((str(D(1) - ask), "5"),), no=(("0.001", "5"),))
        limit = step_price(outcome, ask, ranges, 2)
        beyond = step_price(outcome, ask, ranges, 3)
        if limit is None or limit > D("0.99") or ask > D("0.99"):
            continue                                                  # pair_h2 clamps to 0.99 / has no tick: not an order
        checked += 1
        allowed(w.check_exit(exit_spec(w.market, outcome, limit), book))
        allowed(w.check_exit(exit_spec(w.market, outcome, ask), book))
        if beyond is not None and beyond <= D("0.99") and beyond != limit:
            refused(w.check_exit(exit_spec(w.market, outcome, beyond), book), "within 2 ticks")
    assert checked >= 10


def test_an_exit_must_be_reduce_only_and_must_reduce_something(w):
    hold_yes(w)
    spec = exit_spec(w.market)
    spec["reduce_only"] = False
    refused(w.check_exit(spec), "must be reduce_only")
    refused(w.check_exit(exit_spec(w.market, "yes", "0.59")), "would not reduce")        # more YES while long YES
    refused(w.check_exit(exit_spec(w.market, "no", "0.42", count="2")), "per-order limit")


def test_an_exit_needs_a_position_to_reduce(w):
    refused(w.check_exit(), "would not reduce the ledger position 0")
    hold_yes(w, count="0.50")
    refused(w.check_exit(), "exceeds the 0.50 contracts held")
    allowed(w.check_exit(exit_spec(w.market, "no", "0.42", count="0.5")))


def test_an_exit_is_blocked_by_an_unknown_outcome_on_its_own_ticker_only(w):
    hold_yes(w)
    place(w.ledger, entry_spec(base_market(ticker="KXBTC15M-OLD-1")), err_result("unknown"))
    allowed(w.check_exit())
    place(w.ledger, exit_spec(w.market), err_result("unknown"))
    refused(w.check_exit(), "unknown outcome")


def test_exits_still_work_when_the_loss_cap_is_used_up(w):
    """An exit only reduces risk, so the caps that stop entries must not trap a one-legged position."""
    hold_yes(w)
    seed_realized_loss(w.ledger, "4.90")
    refused(w.check(), "loss cap")
    allowed(w.check_exit())


def test_exits_have_a_shorter_time_to_close_requirement(w):
    hold_yes(w)
    w.at(CLOSE_TS - 6, close_in=6)
    allowed(w.check_exit())
    refused(w.check(), "closes in 6 s")
    w.at(CLOSE_TS - 4, close_in=4)
    refused(w.check_exit(), "5 s required")


def test_exits_need_a_fresh_book_and_a_tradable_market(w):
    hold_yes(w)
    refused(w.check_exit(book=w.book(fetched_at=w.clock.now - 3)), "stale")
    refused(w.check_exit(book=w.book(yes=())), "no YES bids")
    w.client.status["exchange_index_statuses"][2]["trading_active"] = False
    w.safety.invalidate()
    refused(w.check_exit(), "shard 2")


def test_exits_obey_the_per_order_ceilings_and_price_bounds(w):
    hold_yes(w)
    refused(w.check_exit(exit_spec(w.market, "no", "0.42", count="1.5")), "per-order limit")
    book = w.book(yes=(("0.9995", "5"),), no=(("0.0003", "5"),))                  # NO ask 0.0005: YES price 0.9995, above the 0.999 bound
    refused(w.check_exit(exit_spec(w.market, "no", "0.0005"), book), "outside 0.001..0.999")
    book = w.book(yes=(("0.001", "5"),), no=(("0.900", "5"),))                    # NO ask 0.999: YES price 0.001, the bound itself
    allowed(w.check_exit(exit_spec(w.market, "no", "0.999"), book))


def test_a_yes_exit_below_the_price_floor_is_refused_and_the_floor_itself_passes(w):
    """The bounds are symmetric: an exit buying YES at 0.0005 is below the 0.001 floor; 0.001 is a tick of the grid and in."""
    hold_no(w)
    book = w.book(yes=(("0.0003", "5"),), no=(("0.9995", "5"),))                  # YES ask 0.0005
    refused(w.check_exit(exit_spec(w.market, "yes", "0.0005"), book), "outside 0.001..0.999")
    book = w.book(yes=(("0.0005", "5"),), no=(("0.999", "5"),))                   # YES ask 0.001
    allowed(w.check_exit(exit_spec(w.market, "yes", "0.001"), book))


def test_a_halted_session_blocks_exits(w):
    hold_yes(w)
    w.safety.trip("x")
    refused(w.check_exit(), "halted")


# ============================================================================================ review: the legs of a pair
def test_the_fake_safety_the_strategy_tests_use_has_the_real_interface():
    """The strategy tests run the bots against botfakes.FakeSafety. If it drifts from the real Safety (as it would have
    without ``batch``), those tests stop testing what production calls. Same parameter names, method by method."""
    import inspect

    from .botfakes import FakeSafety

    for name in ("check_entry", "check_exit", "preflight", "poll_clock", "clock_skew", "kill_active", "trip"):
        real = list(inspect.signature(getattr(Safety, name)).parameters)
        fake = list(inspect.signature(getattr(FakeSafety, name)).parameters)
        assert real == fake, (name, real, fake)


def pair_legs(w):
    """Two entries that go out together (a pair): YES on the 15m market at 0.59 and NO on the hourly market at 0.56, each
    with its own book and market. Returns (specs, books, markets)."""
    hourly = hourly_market()
    w.client.markets[HOURLY_TICKER] = hourly
    h_book = make_book(HOURLY_TICKER, yes=(("0.440", "5"),), no=(("0.450", "9"),), fetched_at=w.clock.now - 0.2)
    return [w.entry("yes", "0.59"), entry_spec(hourly, "no", "0.56")], [w.book(), h_book], [w.market, hourly]


def check_pair(w, specs, books, markets, *, batch=True):
    """Both legs through check_entry, the way pair_h2 asks (``batch=specs`` for every leg), or one by one."""
    return [w.safety.check_entry(s, b, m, batch=specs if batch else None) for s, b, m in zip(specs, books, markets)]


def test_each_leg_of_a_pair_fits_the_exposure_cap_alone_but_the_pair_does_not(w):
    """Review finding: the $3.00 open-exposure ceiling was checked one leg at a time, so with 2.20 open two legs of 0.59
    and 0.56 (2.79 and 2.76 each) passed and left 3.35 open. The batch counts both legs."""
    for i in range(4):
        seed_fill(w.ledger, ticker=f"KXBTC15M-OLD-{i}", order_id=f"o{i}", fill_id=f"f{i}", price="0.55")
    assert w.ledger.open_exposure() == D("2.20")
    specs, books, markets = pair_legs(w)
    for verdict in check_pair(w, specs, books, markets, batch=False):
        allowed(verdict)                                                           # alone: 2.79 and 2.76 <= 3.00
    for verdict in check_pair(w, specs, books, markets):
        refused(verdict, "exposure cap", "these 2 orders", "2.2", "open + 1.15 collateral")
    assert not w.safety.halted                                                     # a refusal, not a trip: room may free up


def test_a_pair_that_fits_the_exposure_cap_together_passes(w):
    seed_fill(w.ledger, ticker="KXBTC15M-OLD-1", order_id="o1", fill_id="f1", price="0.55")      # 0.55 open: 0.55 + 1.15 <= 3
    specs, books, markets = pair_legs(w)
    assert check_pair(w, specs, books, markets) == [(True, ""), (True, "")]


def test_each_leg_of_a_pair_fits_the_loss_cap_alone_but_the_pair_does_not(w):
    seed_realized_loss(w.ledger, "4.00")
    specs, books, markets = pair_legs(w)
    for verdict in check_pair(w, specs, books, markets, batch=False):
        allowed(verdict)                                                           # 4.00 + 0.61 and 4.00 + 0.58 <= 5.00
    for verdict in check_pair(w, specs, books, markets):
        refused(verdict, "loss cap:", "these 2 orders", "> $5")


def test_the_pairs_orders_count_in_the_per_minute_rate_limit(w):
    for _ in range(9):
        place(w.ledger, w.entry(), err_result("rejected", 400))
    specs, books, markets = pair_legs(w)
    for verdict in check_pair(w, specs, books, markets, batch=False):
        allowed(verdict)                                                           # 9 < 10 for either leg alone
    for verdict in check_pair(w, specs, books, markets):
        refused(verdict, "rate limit", "9 orders in the last minute", "2 more in this batch", "max 10")


def test_the_pairs_orders_count_in_the_hourly_rate_limit(tmp_path):
    w = World(tmp_path, cfg={"max_orders_per_hour": 5})
    for _ in range(4):
        place(w.ledger, w.entry(), err_result("rejected", 400))
        w.advance(70)
    specs, books, markets = pair_legs(w)
    allowed(w.safety.check_entry(specs[0], books[0], markets[0]))
    refused(w.safety.check_entry(specs[0], books[0], markets[0], batch=specs), "last hour", "2 more in this batch")


def test_the_pairs_orders_count_in_the_session_cap(tmp_path):
    w = World(tmp_path, cfg={"max_orders_per_session": 3})
    for _ in range(2):
        place(w.ledger, w.entry(), err_result("rejected", 400))
        w.advance(70)
    specs, books, markets = pair_legs(w)
    allowed(w.safety.check_entry(specs[0], books[0], markets[0]))                 # 2 + 1 <= 3
    refused(w.safety.check_entry(specs[0], books[0], markets[0], batch=specs), "session order cap", "2 more in this batch")


def test_the_shard_must_cover_both_legs_of_a_pair_at_once(w):
    w.client.balances[SHARD] = "1.20"                                              # each leg needs about 0.66, both about 1.24
    specs, books, markets = pair_legs(w)
    for verdict in check_pair(w, specs, books, markets, batch=False):
        allowed(verdict)
    for verdict in check_pair(w, specs, books, markets):
        refused(verdict, "cash on shard 2", "these 2 orders need")
    w.client.balances[SHARD] = "1.30"
    w.safety.invalidate()
    assert check_pair(w, specs, books, markets) == [(True, ""), (True, "")]


def test_a_batch_of_one_is_exactly_a_single_order_check(w):
    spec = w.entry()
    assert w.safety.check_entry(spec, w.book(), w.market, batch=[spec]) == (True, "")
    seed_realized_loss(w.ledger, str(D("5") - WORST_YES_59 + D("0.0001")))
    why = refused(w.safety.check_entry(spec, w.book(), w.market, batch=[spec]), "loss cap:", "this order")
    assert "these" not in why                                                       # the single-order wording is unchanged


def test_every_leg_of_the_batch_is_still_checked_on_its_own_book_and_market(w):
    specs, books, markets = pair_legs(w)
    stale = dict(books[1], fetched_at=w.clock.now - 3.0)                           # the hourly book is too old
    ok1, ok2 = [w.safety.check_entry(s, b, m, batch=specs) for s, b, m in zip(specs, [books[0], stale], markets)]
    assert ok1 == (True, "")
    refused(ok2, "stale")
    other = dict(specs[1], count=D("2"))                                           # a malformed sibling refuses the leg too
    refused(w.safety.check_entry(specs[0], books[0], markets[0], batch=[specs[0], other]), "per-order limit")


@pytest.mark.parametrize("bad", [[], (), "x", 7, {}])
def test_a_batch_must_be_a_non_empty_list_of_specs(w, bad):
    refused(w.safety.check_entry(w.entry(), w.book(), w.market, batch=bad), "batch must be a non-empty list")


# ============================================================================================ review: a 401 on a read halts
@pytest.mark.parametrize("name", ["balance", "positions", "orders"])
def test_a_401_on_a_safety_read_trips_instead_of_refusing_forever(w, name):
    """Review finding: AuthError was turned into an ordinary refusal, so a revoked key or a clock past Kalshi's tolerance
    made the bot retry a rejected signed read every few seconds for ever, with no error and exit code 0."""
    w.client.fail[name] = AuthError(f"GET /portfolio/{name}: HTTP 401", status=401)
    ok, why = w.check()
    assert ok is False and "authentication refused" in why and "safety halted" in why
    assert w.safety.halted and w.kill.exists() and "authentication refused" in w.safety.halt_reason
    assert "safety trip: authentication refused" in w.kill.read_text()
    w.client.calls.clear()
    refused(w.check(), "halted")                                                    # from now on nothing is even read
    refused(w.check_exit(), "halted")
    assert w.client.calls == []


def test_a_401_during_preflight_trips_too(w):
    w.client.fail["balance"] = AuthError("GET /portfolio/balance: HTTP 403", status=403)
    problem = w.safety.preflight(w.market)
    assert problem and "authentication refused" in problem and w.safety.halted and w.kill.exists()


def test_other_read_failures_do_not_trip(w):
    for name in ("balance", "positions", "orders"):
        w.client.fail[name] = ScriptedFailure("503")
        w.safety.invalidate()
        assert w.check()[0] is False and not w.safety.halted and not w.kill.exists()
        w.client.fail.clear()


# ============================================================================================ review: the clock is re-measured
def test_the_clock_is_measured_at_startup_and_order_checks_do_not_probe(w):
    assert w.safety.clock_skew() == 0.5 and w.safety.status()["clock_skew_s"] == 0.5
    assert w.safety.poll_clock() is None and w.client.calls_to("server_time_skew") == []        # not due yet
    allowed(w.check())
    assert w.client.calls_to("server_time_skew") == []                              # an order check reads the cached value


def test_poll_clock_measures_when_due_and_reports_a_clock_that_drifted(w):
    """Review finding: the skew gate ran only at startup, so a PC clock that drifted during a days-long run was never seen."""
    w.client.skew = 20.0
    w.advance(601)                                                                  # the next measurement is due at 600 s
    problem = w.safety.poll_clock()
    assert problem and "+20.00" in problem and "2 s limit" in problem
    assert len(w.client.calls_to("server_time_skew")) == 1 and w.safety.clock_skew() == 20.0
    refused(w.check(), "clock skew +20.00 s")                                       # no order passes while it is off
    refused(w.check_exit(), "clock skew +20.00 s")
    assert not w.safety.halted                                                      # a refusal, not a trip: it can recover
    w.client.skew = 0.3                                                             # NTP corrected it
    w.advance(30)
    assert w.safety.poll_clock() is not None                                        # a bad reading is re-checked after 60 s ...
    w.advance(31)
    assert w.safety.poll_clock() is None and w.safety.clock_skew() == 0.3
    allowed(w.check())


def test_poll_clock_with_force_measures_at_once(w):
    w.client.skew = -3.5
    assert "-3.50" in w.safety.poll_clock(force=True)
    w.client.skew = 0.0
    assert w.safety.poll_clock() is not None and w.safety.poll_clock(force=True) is None


def test_a_stale_measurement_is_replaced_before_an_order_is_checked(w):
    """If nobody polled the clock (a bot that idles less), the order check measures once, inline, and obeys the result."""
    w.client.skew = 30.0
    w.advance(1300)                                                                 # older than 2 x 600 + 60 s
    refused(w.check(), "clock skew +30.00 s")
    assert len(w.client.calls_to("server_time_skew")) == 1


def test_a_stale_but_fine_measurement_is_renewed_and_the_order_goes_through(w):
    w.advance(1300)
    allowed(w.check())
    assert len(w.client.calls_to("server_time_skew")) == 1 and w.safety.clock_skew() == 0.5


def test_failing_probes_end_in_a_refusal_once_the_last_good_reading_is_too_old(w):
    w.client.fail["server_time_skew"] = ScriptedFailure("no Date header")
    w.advance(601)
    assert w.safety.poll_clock() is None                                            # the 601 s old good reading still stands
    allowed(w.check())
    w.advance(700)                                                                  # 1301 s: past 2 x 600 + 60
    problem = w.safety.poll_clock()
    assert problem and "last measured" in problem and "clock skew probe failed" in problem
    refused(w.check(), "last measured")


def test_a_pc_clock_that_moves_backwards_is_measured_again(w):
    w.client.skew = 12.0
    w.clock.now -= 30.0                                                             # Windows stepped the clock back
    problem = w.safety.poll_clock()
    assert problem and "+12.00" in problem and len(w.client.calls_to("server_time_skew")) == 1
    w.client.skew = 0.0
    w.clock.now += 61
    assert w.safety.poll_clock() is None


def test_an_order_check_with_no_measurement_measures_first(tmp_path):
    w = World(tmp_path, started=False)
    w.safety.started = w.safety.live = True                                         # (startup() would have measured)
    allowed(w.check())
    assert len(w.client.calls_to("server_time_skew")) == 1
    w2 = World(tmp_path / "b", started=False)
    w2.safety.started = w2.safety.live = True
    w2.client.fail["server_time_skew"] = ScriptedFailure("down")
    refused(w2.check(), "clock skew probe failed")


def test_preflight_reports_a_clock_problem_without_probing(w):
    w.client.skew = 9.0
    w.advance(601)
    w.safety.poll_clock()
    w.client.calls.clear()
    assert "clock skew +9.00" in w.safety.preflight(w.market)
    assert w.client.calls == []


def test_the_clock_recheck_interval_is_bounded_by_a_ceiling_and_a_floor():
    lim = parse_limits({})
    assert lim.clock_recheck_s == 600.0 and lim.clock_max_age_s == 1260.0
    assert parse_limits({"clock_recheck_s": 900}).clock_max_age_s == 1860.0
    with pytest.raises(SafetyConfigError, match="above the hard ceiling"):
        parse_limits({"clock_recheck_s": 901})
    with pytest.raises(SafetyConfigError, match="below the hard floor"):
        parse_limits({"clock_recheck_s": 29})
    assert CEILINGS["clock_recheck_s"] == 900.0 and FLOORS["clock_recheck_s"] == 30.0


def test_the_entry_cutoff_is_the_minimum_time_to_close_plus_the_early_close_buffer():
    assert parse_limits({"min_seconds_to_close": 20}).entry_cutoff_s == 30.0         # what config/settings.yaml says
    assert parse_limits({}).entry_cutoff_s == 40.0


# ============================================================================================ review: the listing and the shards
def test_the_fresh_listing_is_read_with_the_series_and_nothing_else(w):
    """Review finding: Safety asked for status=open together with min_close_ts / max_close_ts, a combination Kalshi's
    OpenAPI lists as incompatible (those filters go with status closed or no status)."""
    seen = []
    real = w.client.open_markets

    def spy(*args, **kwargs):
        seen.append((args, kwargs))
        return real(*args, **kwargs)

    w.client.open_markets = spy
    allowed(w.check())
    assert seen == [(("KXBTC15M",), {})]                                            # no close-time filter, no other argument


def test_a_listing_with_other_markets_still_finds_the_ticker(w):
    """Without the close-time filter the listing holds every open market of the series: the ticker is matched in it."""
    for i in range(30):
        w.client.markets[f"KXBTC15M-OTHER{i}"] = base_market(ticker=f"KXBTC15M-OTHER{i}", close_time="2026-10-05T19:30:00Z")
        w.client.series_of[f"KXBTC15M-OTHER{i}"] = "KXBTC15M"
    allowed(w.check())
    refused(w.check(market=base_market(close_time="2026-10-05T19:30:00Z")), "stale")      # same ticker, another close


def test_diagnostics_only_needs_the_shard_the_bots_trade_on(tmp_path):
    """Review finding: kpm exec check failed 'exchange_status' when ANY shard (Combos, Tennis ...) was paused."""
    w = World(tmp_path, started=False)
    for i in (0, 1, 3):
        w.client.status["exchange_index_statuses"][i]["trading_active"] = False
    d = w.safety.diagnostics("prod")
    check = {c["name"]: c for c in d["checks"]}["exchange_status"]
    assert check["ok"] and d["ok"]
    assert "shard 2 (KXBTC15M, KXBTCD) trading" in check["detail"]
    assert "other shard(s) not trading, not used here: 0, 1, 3" in check["detail"]
    w.client.status["exchange_index_statuses"][2]["trading_active"] = False
    d = w.safety.diagnostics("prod")
    check = {c["name"]: c for c in d["checks"]}["exchange_status"]
    assert not check["ok"] and not d["ok"] and "shard 2 (KXBTC15M, KXBTCD) not trading" in check["detail"]
    w.client.status["exchange_index_statuses"][2].update(trading_active=True, exchange_active=False)
    assert not {c["name"]: c for c in w.safety.diagnostics("prod")["checks"]}["exchange_status"]["ok"]


def test_diagnostics_reads_the_shard_from_the_series_and_falls_back_to_the_open_markets(tmp_path):
    w = World(tmp_path, started=False)
    w.client.script["series"] = [{"ticker": "KXBTC15M", "exchange_index": 3}]       # the series object says shard 3
    w.client.status["exchange_index_statuses"][3]["trading_active"] = False
    check = {c["name"]: c for c in w.safety.diagnostics("prod")["checks"]}["exchange_status"]
    assert not check["ok"] and "shard 3" in check["detail"]
    w.client.script.clear()
    w.client.markets[HOURLY_TICKER] = hourly_market()                               # (KXBTCD has an open market too)
    w.client.fail["series"] = ScriptedFailure("503")                                # no series read: the markets name the shard
    check = {c["name"]: c for c in w.safety.diagnostics("prod")["checks"]}["exchange_status"]
    assert check["ok"] and "shard 2" in check["detail"] and w.client.calls_to("open_markets")
    w.client.fail["open_markets"] = ScriptedFailure("503")                          # neither: fail closed
    check = {c["name"]: c for c in w.safety.diagnostics("prod")["checks"]}["exchange_status"]
    assert not check["ok"] and "cannot read the exchange_index" in check["detail"]


# ============================================================================================ grid helpers
BANDS = [(D("0.0000"), D("0.1000"), D("0.0010")), (D("0.1000"), D("0.9000"), D("0.0100")),
         (D("0.9000"), D("1.0000"), D("0.0010"))]


@pytest.mark.parametrize("price, up, expected", [
    ("0.5900", True, "0.6000"), ("0.5900", False, "0.5800"),
    ("0.8900", True, "0.9000"), ("0.9000", True, "0.9010"), ("0.9000", False, "0.8900"),     # edge: the band entered
    ("0.1000", True, "0.1100"), ("0.1000", False, "0.0990"), ("0.0100", False, "0.0090"),
    ("0.9990", True, None), ("0.0010", False, None), ("0.0000", False, None), ("0.5950", True, None),   # ends / off grid
])
def test_next_tick_walks_the_grid_and_uses_the_band_being_entered(price, up, expected):
    got = next_tick(BANDS, D(price), up)
    assert got == (None if expected is None else D(expected))


def test_ticks_from_touch():
    assert ticks_from_touch(BANDS, D("0.58"), D("0.58"), False, 2) == 0
    assert ticks_from_touch(BANDS, D("0.58"), D("0.56"), False, 2) == 2
    assert ticks_from_touch(BANDS, D("0.58"), D("0.55"), False, 2) is None
    assert ticks_from_touch(BANDS, D("0.89"), D("0.901"), True, 2) == 2
    assert ticks_from_touch(BANDS, D("0.58"), D("0.59"), False, 2) is None                   # wrong direction


def test_gate_is_a_plain_record():
    g = Gate("x", True, "fine")
    assert g.as_dict() == {"name": "x", "ok": True, "detail": "fine"}
    assert iso(CLOSE_TS).startswith("2026-10-05T18:30:00") and CLOSE_TS - NOW == 90
