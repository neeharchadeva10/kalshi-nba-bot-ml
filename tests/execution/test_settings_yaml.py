"""config/settings.yaml: the trading block is exactly what the user decided, and every key in it is read by something.

The file is parsed directly (yaml.safe_load), never through kpm.core.config, so no .env is touched. Nothing here sends a
request: the bots are only constructed.
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from kpm.execution import late_favorite as lf
from kpm.execution import pair_h2
from kpm.execution.botstate import BotState
from kpm.execution.safety import CEILINGS, FLOORS, Safety, parse_limits

SETTINGS = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"

# Every key the trading block may hold. A new key must be added here on purpose, together with whatever reads it.
KNOWN_TRADING_KEYS = {
    "enabled", "max_loss_usd", "max_cost_per_order_usd", "max_contracts_per_order", "max_open_exposure_usd",
    "max_orders_per_minute", "max_orders_per_session", "max_clock_skew_s", "allowed_series", "price_min", "price_max",
    "min_seconds_to_close", "kill_switch_file", "late_favorite", "pair"}


@pytest.fixture(scope="module")
def trading() -> dict:
    return yaml.safe_load(SETTINGS.read_text(encoding="utf-8"))["trading"]


def test_the_enabled_switch_is_a_real_boolean_and_the_block_has_no_hosts(trading):
    # The file ships with enabled: false (live trading is opt-in). The user flips it to go live, so the suite only requires
    # a real boolean: the string "false" would be truthy to a careless reader, and Safety needs exactly True.
    assert isinstance(trading["enabled"], bool)
    text = yaml.safe_dump(trading)
    for host_part in ("kalshi.com", "kalshi.co", "http://", "https://", "wss://", "base_url", "ws_url"):
        assert host_part not in text                                # hosts live in kpm.execution.env, not here


def test_every_trading_key_is_known(trading):
    assert set(trading) == KNOWN_TRADING_KEYS


def test_the_limits_are_the_users_decisions(trading):
    lim = parse_limits(trading)
    assert lim.max_loss_usd == D("5.00") and lim.max_loss_usd == CEILINGS["max_loss_usd"]       # ONE account-wide cap
    assert lim.max_cost_per_order_usd == D("1.00") and lim.max_contracts_per_order == D("1")
    assert lim.max_open_exposure_usd == D("3.00")
    assert lim.max_orders_per_minute == 10 and lim.max_orders_per_session == 300
    assert lim.max_clock_skew_s == 2.0
    assert lim.allowed_series == ("KXBTC15M", "KXBTCD")
    assert (lim.price_min, lim.price_max) == (D("0.001"), D("0.999"))      # 2026-10-06: take 99-99.9c favorites
    assert (lim.price_min, lim.price_max) == (FLOORS["price_min"], CEILINGS["price_max"])   # the file uses the whole allowed band
    assert lim.min_seconds_to_close == 20 and lim.min_seconds_to_close == FLOORS["min_seconds_to_close"]
    assert lim.kill_switch_file == "data/KILL"


def test_money_and_counts_are_strings_so_yaml_never_makes_floats(trading):
    for key in ("max_loss_usd", "max_cost_per_order_usd", "max_contracts_per_order", "max_open_exposure_usd",
                "price_min", "price_max"):
        assert isinstance(trading[key], str), key
    assert isinstance(trading["late_favorite"]["up_threshold"], str) and isinstance(trading["pair"]["min_edge_usd"], str)


def test_the_typed_confirmation_names_the_cap(trading):
    class Quiet:
        live = False
        env = SimpleNamespace(name="prod")

    s = Safety(trading, ledger=None, client=Quiet())
    assert s.confirmation_phrase == "LIVE PROD 5"


def test_the_late_favorite_block_builds_the_bot_the_user_described(trading):
    ctx = SimpleNamespace(client=SimpleNamespace(live=False), state=BotState(sqlite3.connect(":memory:"), "r"),
                          ledger=None, safety=None, reconcile=None, env_name="prod", key_fp=None)
    bot = lf.LateFavoriteBot(ctx, {"trading": trading}, live=False)
    assert bot.contracts == D("1") and bot.max_attempts == 3
    assert (bot.poll_s, bot.max_book_age_s) == (1.0, 2.0)
    assert bot.max_loss == D("5.00")
    # the four rule lines repeat the fixed rule: the bot would have refused to start otherwise
    block = trading["late_favorite"]
    assert (block["window_start_s"], block["window_end_s"]) == (lf.DECISION_START_S, lf.DECISION_END_S)
    assert (D(block["up_threshold"]), D(block["down_threshold"])) == (lf.YES_ABOVE, lf.NO_BELOW)


def test_the_pair_block_builds_the_second_bot(trading):
    ctx = SimpleNamespace(client=SimpleNamespace(live=False), state=BotState(sqlite3.connect(":memory:"), "r"),
                          ledger=None, safety=None, reconcile=None, env_name="prod", key_fp=None)
    bot = pair_h2.PairBot(ctx, {"trading": trading}, live=False)
    assert bot.contracts == D("1") and bot.min_edge == D("0.001")
    assert (bot.window_s, bot.strikes_each_side, bot.poll_s, bot.max_book_age_s) == (900.0, 2, 1.0, 2.0)
    assert (bot.stop_before_close_s, bot.max_live_pairs_per_hour) == (45.0, 2)
    # the window ends before Safety's entry cutoff (min_seconds_to_close 20 + the early-close buffer 10), so every pair the
    # bot detects can also be traded live (review finding: it used to stop at 20 s, 10 s after Safety stops accepting)
    assert bot.stop_before_close_s >= bot.entry_cutoff_s == parse_limits(trading).entry_cutoff_s == 30.0


def test_a_settings_value_past_a_ceiling_refuses_to_start(trading):
    for key, bad in (("max_loss_usd", "5.01"), ("max_cost_per_order_usd", "1.01"), ("max_contracts_per_order", "2"),
                     ("max_open_exposure_usd", "3.01"), ("max_orders_per_minute", 12), ("max_clock_skew_s", 2.5)):
        with pytest.raises(ValueError, match="ceiling"):
            parse_limits({**trading, key: bad})
