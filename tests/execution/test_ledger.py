"""kpm.execution.ledger: intents before sends, unknown outcomes, fills, settlement cents, loss accounting, audit.

Offline. A real Ledger runs on a throwaway SQLite file; the exchange is replaced by the shapes in exec_fakes.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal as D

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from kpm.core import db
from kpm.execution import units
from kpm.execution.client import ExecClient
from kpm.execution.env import HOSTS, Env
from kpm.execution.ledger import (EXEC_TABLES, FINAL_STATUSES, NOT_PLACED_STATUSES, OPEN_STATUSES,
                                  UNRESOLVED_STATUSES, Ledger, build_body, plain)

from .exec_fakes import (CLOSE_TS, HOURLY_TICKER, NOW, SHARD, TICKER, Clock, base_market, entry_spec, err_result,
                         ex_fill, ex_order, ex_settlement, hourly_market, iso, make_ledger, ok_result, place)

FEE_YES_59 = units.taker_fee(D(1), D("0.59"))          # 0.016933
FEE_NO_42 = units.taker_fee(D(1), D("0.42"))           # 0.017052
WORST_YES_59 = D("0.59") + units.worst_fee(D(1), D("0.59"))


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def led(tmp_path, clock):
    return make_ledger(tmp_path, clock)


def filled_order(led, *, outcome="yes", price="0.59", order_id="ord-1", fill_id="fill-1", ticker=TICKER,
                 fee=None, market=None):
    """An executed IOC order with its fill recorded. Returns the client_order_id."""
    spec = entry_spec(market or base_market(), outcome, price)
    coid = place(led, spec, order_id=order_id)
    fee = fee if fee is not None else str(units.taker_fee(D(1), D(price)))
    led.upsert_fills([ex_fill(fill_id, order_id, ticker, outcome=outcome, price=price, fee=fee)])
    return coid


# ============================================================================================ schema
def test_exec_tables_exist_and_schema_init_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "s.sqlite")
    db.init_schema(conn)
    db.init_schema(conn)                                           # CREATE IF NOT EXISTS: safe to repeat
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert set(EXEC_TABLES) <= tables and {"bot_runs", "bot_orders", "audit_log"} <= tables

    def cols(t):
        return {r[1]: r[2] for r in conn.execute(f"PRAGMA table_info({t})")}

    orders, fills, setl = cols("exec_orders"), cols("exec_fills"), cols("exec_settlements")
    for c in ("price", "count", "outcome_price", "fill_count", "remaining_count", "avg_fill_price", "avg_fee_paid",
              "taker_fees", "maker_fees"):
        assert orders[c] == "TEXT", c                                # exact decimals, never REAL
    for c in ("count", "yes_price", "no_price", "fee_cost"):
        assert fills[c] == "TEXT", c
    assert setl["revenue_cents"] == "INTEGER" and setl["revenue"] == "TEXT" and setl["fee_cost"] == "TEXT"
    assert "env" in orders and "env" in fills and "env" in setl      # every row carries its environment


def test_ledger_creates_missing_tables_on_an_empty_database(tmp_path, clock):
    conn = sqlite3.connect(":memory:")
    Ledger(conn, "run-1", "prod", clock=clock, log_path=tmp_path / "x.jsonl")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert set(EXEC_TABLES) <= tables


def test_ledger_rejects_empty_run_or_env(tmp_path, clock):
    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError):
        Ledger(conn, "", "prod", clock=clock, log_path=tmp_path / "x.jsonl")
    with pytest.raises(ValueError):
        Ledger(conn, "run", " ", clock=clock, log_path=tmp_path / "x.jsonl")


# ============================================================================================ intent before send
def test_intent_is_committed_before_the_send(tmp_path, clock, led):
    coid = led.new_intent(entry_spec())
    other = sqlite3.connect(tmp_path / "ledger.sqlite")              # a second connection sees only COMMITTED rows
    row = other.execute("SELECT status, body_json, run_id, env FROM exec_orders WHERE client_order_id = ?",
                        (coid,)).fetchone()
    other.close()
    assert row is not None, "the intent row must be committed before anything is sent"
    assert row[0] == "intent" and row[2] == "run-1" and row[3] == "prod"
    assert json.loads(row[1]) == led.request_body(coid)
    assert not led.conn.in_transaction


def test_the_stored_body_is_exactly_what_gets_sent(led):
    spec = entry_spec()
    coid = led.new_intent(spec)
    body = led.request_body(coid)
    assert body == {"ticker": TICKER, "client_order_id": coid, "side": "bid", "price": "0.5900", "count": "1.00",
                    "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross",
                    "exchange_index": SHARD, "reduce_only": False}   # explicit flags in the spec are kept as given
    assert spec["client_order_id"] == coid                            # the spec is updated with the id
    row = led.order(coid)
    assert (row["price"], row["count"], row["outcome"], row["outcome_price"]) == ("0.5900", "1.00", "yes", "0.59")
    assert row["status"] == "intent" and row["reduce_only"] == 0 and row["exchange_index"] == SHARD


def test_buying_no_is_stored_as_an_ask_at_one_minus_the_price(led):
    """The 99x trap: 'buy NO at 0.01' is side ask at YES price 0.99, never ask at 0.01."""
    spec = entry_spec(outcome="no", price="0.01")
    coid = led.new_intent(spec)
    body = led.request_body(coid)
    assert (body["side"], body["price"]) == ("ask", "0.9900")
    row = led.order(coid)
    assert (row["outcome"], row["outcome_price"]) == ("no", "0.01")
    assert units.collateral(row["side"], D(row["price"]), D(row["count"])) == D("0.01")


@pytest.mark.parametrize("override, message", [
    ({"side": "bid"}, "do not match"),                                    # NO order tagged as a YES bid
    ({"side": "ask", "yes_price": D("0.01"), "price": D("0.01")}, "do not match"),     # the 99x wrong-side order
    ({"yes_price": D("0.5")}, "do not match"),
])
def test_wrong_side_or_price_can_never_be_stored(led, override, message):
    spec = entry_spec(outcome="no", price="0.42")
    spec.update(override)
    with pytest.raises(ValueError, match=message):
        led.new_intent(spec)
    assert led.orders_for(TICKER) == []                                   # nothing was written


@pytest.mark.parametrize("override, error", [
    ({"outcome": None}, ValueError),                                       # outcome and price must come together
    ({"outcome_price": D("0")}, ValueError),
    ({"outcome_price": D("1")}, ValueError),
    ({"outcome_price": 0.59}, TypeError),                                  # floats are refused
    ({"count": D("0")}, ValueError),
    ({"count": D("0.001")}, ValueError),
    ({"exchange_index": None}, ValueError),                                # every order carries its shard
    ({"exchange_index": True}, ValueError),
    ({"time_in_force": "day"}, ValueError),
    ({"reduce_only": True, "time_in_force": "good_till_canceled"}, ValueError),
])
def test_malformed_specs_are_refused(led, override, error):
    spec = entry_spec()
    spec.update(override)
    if override.get("outcome") is None and "outcome" in override:
        spec.pop("side"), spec.pop("yes_price")
    with pytest.raises(error):
        led.new_intent(spec)
    assert led.orders_for(TICKER) == []


def test_count_25_and_0_25_stay_distinct():
    a = build_body(entry_spec(count="25"), "c-1")
    b = build_body(entry_spec(count="0.25"), "c-1")
    assert (a["count"], b["count"]) == ("25.00", "0.25")


def test_a_client_order_id_is_never_reused(led):
    coid = led.new_intent(entry_spec())
    with pytest.raises(ValueError, match="already in the ledger"):
        led.new_intent(entry_spec(client_order_id=coid))
    assert len(led.orders_for(TICKER)) == 1
    assert led.new_intent(entry_spec()) != coid                           # a retry is a NEW intent


def test_the_spec_shape_the_bots_send_is_accepted(led):
    """pair_h2 / late_favorite put a body dict, Decimals and strategy metadata in the spec."""
    spec = entry_spec(base_market(), "yes", "0.59", opportunity_id=77)
    spec["client_order_id"] = "11111111-1111-4111-8111-111111111111"
    spec["body"] = {"ticker": TICKER, "client_order_id": spec["client_order_id"], "side": "bid", "price": "0.5900",
                    "count": "1.00", "time_in_force": "immediate_or_cancel",
                    "self_trade_prevention_type": "taker_at_cross", "exchange_index": SHARD}
    coid = led.new_intent(spec)
    assert coid == "11111111-1111-4111-8111-111111111111"
    row = led.order(coid)
    assert row["group_id"] == "77" and row["purpose"] == "entry"
    assert led.request_body(coid)["price"] == "0.5900"


def test_unwind_specs_store_reduce_only(led):
    spec = entry_spec(outcome="no", price="0.42", purpose="unwind", reduce_only=True)
    coid = led.new_intent(spec)
    assert led.request_body(coid)["reduce_only"] is True
    assert led.order(coid)["reduce_only"] == 1 and led.order(coid)["purpose"] == "unwind"


# ============================================================================================ results
def test_ioc_fully_filled_is_executed(led):
    spec = entry_spec()
    coid = led.new_intent(spec)
    status = led.mark_result(coid, ok_result(coid, "ord-1", fill="1.00", avg_price="0.5900", avg_fee="0.016933"))
    row = led.order(coid)
    assert status == "executed" == row["status"]
    assert (row["order_id"], row["fill_count"], row["remaining_count"]) == ("ord-1", "1.00", "0.00")
    assert (row["avg_fill_price"], row["avg_fee_paid"], row["ts_ms"]) == ("0.5900", "0.016933", 1791224910123)
    assert row["sent_ts"] is not None and row["result_ts"] is not None and row["error"] is None


def test_ioc_unfilled_is_canceled_and_a_partial_fill_keeps_its_count(led):
    c1 = led.new_intent(entry_spec())
    assert led.mark_result(c1, ok_result(c1, "o1", fill="0.00")) == "canceled"
    c2 = led.new_intent(entry_spec())
    assert led.mark_result(c2, ok_result(c2, "o2", fill="0.40")) == "canceled"
    assert led.order(c2)["fill_count"] == "0.40"


def test_gtc_with_remaining_count_is_resting(led):
    spec = entry_spec()
    spec["time_in_force"] = "good_till_canceled"
    coid = led.new_intent(spec)
    assert led.mark_result(coid, ok_result(coid, "o1", fill="0.00", remaining="1.00")) == "resting"
    assert coid in {o["client_order_id"] for o in led.open_orders()}


@pytest.mark.parametrize("result, status", [
    (err_result("rejected", 400, "invalid_order"), "rejected"),
    (err_result("throttled", 429), "throttled"),
    (err_result("auth", 401), "auth_error"),
    (err_result("duplicate", 409, "order_already_exists"), "duplicate"),
    (err_result("unknown", None, "transport_error"), "unknown"),
])
def test_every_client_outcome_maps_to_a_status(led, result, status):
    coid = led.new_intent(entry_spec())
    assert led.mark_result(coid, result) == status
    row = led.order(coid)
    assert row["status"] == status and row["error"]                       # the reason is kept
    assert (status in UNRESOLVED_STATUSES) == (row["client_order_id"] in {o["client_order_id"]
                                                                         for o in led.unresolved_orders()})


def test_unknown_and_duplicate_stay_open_until_reconcile(led):
    """Nothing in the ledger resolves an unknown outcome by itself, and nothing here resends."""
    c1 = led.new_intent(entry_spec())
    led.mark_result(c1, err_result("unknown"))
    c2 = led.new_intent(entry_spec())
    led.mark_result(c2, err_result("duplicate", 409))
    assert {o["client_order_id"] for o in led.unresolved_orders()} == {c1, c2}
    assert {o["client_order_id"] for o in led.open_orders()} == {c1, c2}
    assert led.has_inflight(TICKER, "bid")


@pytest.mark.parametrize("data, why", [
    ({"order_id": "o1", "client_order_id": "someone-else", "fill_count": "1.00", "remaining_count": "0.00"},
     "client_order_id"),
    ({"client_order_id": None, "fill_count": "1.00", "remaining_count": "0.00"}, "order_id"),
    ({"order_id": "o1", "fill_count": "one", "remaining_count": "0.00"}, "decimal"),
])
def test_an_ok_response_that_does_not_describe_the_request_is_unknown(led, data, why):
    from kpm.execution.client import Result
    coid = led.new_intent(entry_spec())
    status = led.mark_result(coid, Result("ok", 201, data, None, 1, 5.0, None))
    assert status == "unknown"
    assert why in led.order(coid)["error"]


def test_a_result_for_another_order_is_not_applied(led):
    coid = led.new_intent(entry_spec())
    other = led.new_intent(entry_spec())
    assert led.mark_result(coid, ok_result(other, "o9")) == "unknown"
    assert led.order(other)["status"] == "intent"


def test_marking_a_final_order_again_is_ignored(led):
    coid = led.new_intent(entry_spec())
    led.mark_result(coid, ok_result(coid, "o1"))
    assert led.mark_result(coid, err_result("rejected", 400)) == "executed"
    assert led.order(coid)["status"] == "executed"


def test_mark_result_for_an_unknown_id_raises(led):
    with pytest.raises(KeyError):
        led.mark_result("nope", ok_result("nope", "o1"))


def test_mark_sent_stamps_the_send_time_once(led, clock):
    coid = led.new_intent(entry_spec())
    led.mark_sent([coid], clock.now + 5)
    led.mark_sent([coid], clock.now + 99)
    assert led.order(coid)["sent_ts"] == clock.now + 5


# ============================================================================================ exchange orders
def test_an_unknown_order_found_on_the_exchange_is_adopted(led):
    coid = led.new_intent(entry_spec())
    led.mark_result(coid, err_result("unknown"))
    info = led.apply_exchange_order(ex_order(coid, "ex-1"))
    assert (info["previous"], info["status"], info["problems"]) == ("unknown", "executed", [])
    row = led.order(coid)
    assert (row["status"], row["order_id"], row["fill_count"], row["remaining_count"]) == ("executed", "ex-1", "1.00",
                                                                                          "0.00")
    assert row["taker_fees"] == "0.016933" and row["maker_fees"] == "0.000000"
    assert led.unresolved_orders() == []


def test_foreign_orders_are_ignored(led):
    assert led.apply_exchange_order(ex_order("manual-1", "ex-77")) is None


def test_identity_problems_are_reported(led):
    coid = place(led, entry_spec(), order_id="ex-1")
    info = led.apply_exchange_order(ex_order(coid, "ex-OTHER", ticker="KXBTC15M-OTHER", book_side="ask"))
    joined = " | ".join(info["problems"])
    assert "order_id" in joined and "ticker" in joined and "side" in joined
    assert led.order(coid)["error"]


def test_an_order_presumed_not_placed_that_exists_is_a_problem(led):
    coid = led.new_intent(entry_spec())
    led.mark_result(coid, err_result("unknown"))
    led.mark_presumed_not_placed(coid, "not found")
    assert led.order(coid)["status"] == "presumed_not_placed" and "presumed_not_placed" in NOT_PLACED_STATUSES
    info = led.apply_exchange_order(ex_order(coid, "ex-1"))
    assert any("presumed not placed" in p for p in info["problems"])
    assert led.order(coid)["status"] == "executed"                         # the exchange wins


def test_orders_are_matched_by_exchange_id_when_the_client_id_is_missing(led):
    coid = place(led, entry_spec(), order_id="ex-1")
    info = led.apply_exchange_order(ex_order("", "ex-1", fill="1.00"))
    assert info["client_order_id"] == coid


# ============================================================================================ fills and positions
def test_fills_are_deduplicated_on_fill_id_and_stored_exactly(led):
    place(led, entry_spec(), order_id="ord-1")
    f = ex_fill("fill-1", "ord-1", fee="0.016933")
    assert led.upsert_fills([f]) == 1
    assert led.upsert_fills([f, f]) == 0
    (row,) = led.fills_for(TICKER)
    assert (row["count"], row["yes_price"], row["no_price"], row["fee_cost"]) == ("1.00", "0.5900", "0.4100",
                                                                              "0.016933")
    assert (row["outcome_side"], row["book_side"], row["is_taker"], row["source"]) == ("yes", "bid", 1, "rest")
    assert row["run_id"] == "run-1" and row["order_id"] == "ord-1"


def test_a_ws_no_fill_without_a_no_price_uses_one_minus_the_yes_price(led):
    coid = place(led, entry_spec(outcome="no", price="0.42"), order_id="ord-9")
    ws = {"trade_id": "ws-1", "order_id": "ord-9", "client_order_id": coid, "market_ticker": TICKER,
          "exchange_index": 2, "is_taker": True, "side": "no", "yes_price_dollars": "0.5800", "count_fp": "1.00",
          "fee_cost": "0.017052", "ts_ms": 1791224910000, "outcome_side": "no", "book_side": "ask"}
    assert led.upsert_fills([ws]) == 1
    (row,) = led.fills_for(TICKER)
    assert (row["fill_id"], row["no_price"], row["source"]) == ("ws-1", "0.4200", "ws")
    assert led.position(TICKER) == D("-1")                                  # NO counts against the position


def test_fills_of_orders_not_in_this_ledger_are_never_stored(led):
    """A manual trade in the app must not reach P&L or the loss cap."""
    place(led, entry_spec(), order_id="ord-1")
    mine, manual = ex_fill("f-mine", "ord-1"), ex_fill("f-manual", "someone-elses-order", count="5.00")
    assert led.split_fills([mine, manual]) == ([mine], [manual])
    assert led.upsert_fills([mine, manual]) == 1
    assert [f["fill_id"] for f in led.fills_for(TICKER)] == ["f-mine"]
    assert [f["fill_id"] for f in led.last_foreign] == ["f-manual"]
    with pytest.raises(ValueError, match="belong to no order"):
        led.upsert_fills([manual], foreign="error")
    assert led.upsert_fills([manual], foreign="store") == 1
    with pytest.raises(ValueError):
        led.upsert_fills([mine], foreign="maybe")


def test_upsert_fills_is_all_or_nothing(led):
    place(led, entry_spec(), order_id="ord-1")
    bad = ex_fill("f-bad", "ord-1", count="0.00")
    with pytest.raises(ValueError):
        led.upsert_fills([ex_fill("f-ok", "ord-1"), bad])
    assert led.fills_for(TICKER) == []


@pytest.mark.parametrize("patch", [{"count_fp": "-1.00"}, {"yes_price_dollars": "1.5000"}, {"fee_cost": "-0.01"},
                                   {"outcome_side": "maybe", "book_side": None}])
def test_unusable_fills_are_refused(led, patch):
    place(led, entry_spec(), order_id="ord-1")
    with pytest.raises(ValueError):
        led.upsert_fills([ex_fill("f1", "ord-1", **patch)])


def test_position_is_signed_and_counts_fills_not_yet_fetched(led):
    coid = place(led, entry_spec(), order_id="ord-1")                      # response says 1.00 filled
    assert led.position(TICKER, include_unrecorded=True) == D("1")
    assert led.position(TICKER, include_unrecorded=False) == D("0")        # compare the exchange with THIS one
    led.upsert_fills([ex_fill("f1", "ord-1")])
    assert led.position(TICKER, include_unrecorded=True) == D("1")         # the fetched fill replaces the estimate
    assert led.position(TICKER, include_unrecorded=False) == D("1")
    place(led, entry_spec(outcome="no", price="0.42"), order_id="ord-2")
    led.upsert_fills([ex_fill("f2", "ord-2", outcome="no", price="0.42", fee="0.017052")])
    assert led.position(TICKER) == D("0")                                  # YES + NO net to flat
    assert led.positions()[TICKER] == D("0")


# ============================================================================================ settlement and P&L
def test_a_winning_yes_settles_in_cents_and_the_fee_is_counted_once(led):
    filled_order(led)
    rows = led.record_settlements([ex_settlement(result="yes", revenue=100, fee="0.016933")])
    (r,) = rows
    assert r["revenue_cents"] == 100 and r["revenue"] == "1.00" and r["check"] == "ok" and r["fee_note"] is None
    assert r["pnl"] == D("1") - D("0.59") - FEE_YES_59 == D("0.393067")     # fee once, not 0.033866
    assert led.realized_pnl() == D("0.393067") and led.realized_loss_total() == 0
    (s,) = led._rows("SELECT * FROM exec_settlements")
    assert (s["revenue_cents"], s["revenue"], s["value_cents"], s["pnl"], s["pnl_basis"]) == (100, "1.00", 100,
                                                                                            "0.393067", "fills")


def test_a_losing_no_position_loses_its_cost_and_fees(led):
    filled_order(led, outcome="no", price="0.42", fee="0.017052")
    led.record_settlements([ex_settlement(result="yes", yes_count="0.00", yes_cost="0.0000", no_count="1.00",
                                          no_cost="0.4200", revenue=0, fee="0.017052")])
    assert led.realized_pnl() == D("-0.437052") and led.realized_loss_total() == D("0.437052")


def test_revenue_must_be_integer_cents(led):
    filled_order(led)
    for bad in (1.0, 100.5, True):
        with pytest.raises(TypeError):
            led.record_settlements([ex_settlement(revenue=bad)])
    assert led.settled_tickers() == set()
    led.record_settlements([ex_settlement(revenue="100")])                  # a numeric string is accepted
    assert led._rows("SELECT revenue_cents FROM exec_settlements")[0]["revenue_cents"] == 100


def test_a_revenue_that_disagrees_with_the_fills_is_flagged(led):
    filled_order(led)
    (r,) = led.record_settlements([ex_settlement(result="yes", revenue=0)])
    assert r["check"].startswith("mismatch")
    assert led._rows("SELECT check_status FROM exec_settlements")[0]["check_status"].startswith("mismatch")


def test_a_settlement_fee_that_differs_from_the_fill_fees_is_noted_but_not_added(led):
    filled_order(led)
    (r,) = led.record_settlements([ex_settlement(result="yes", revenue=100, fee="0.020000")])
    assert "fee_cost" in r["fee_note"] and r["pnl"] == D("0.393067")        # the fill fees are the ones counted


def test_without_local_fills_the_settlement_row_alone_gives_the_pnl(led):
    (r,) = led.record_settlements([ex_settlement(result="yes", yes_cost="0.5900", revenue=100, fee="0.016933")])
    assert r["basis"] == "settlement" and r["pnl"] == D("1") - D("0.59") - D("0.016933")


def test_a_hedged_pair_pays_one_dollar_either_way(led):
    filled_order(led, outcome="yes", price="0.59", order_id="o1", fill_id="f1")
    filled_order(led, outcome="no", price="0.42", order_id="o2", fill_id="f2", fee="0.017052")
    cost, fees = D("1.01"), FEE_YES_59 + FEE_NO_42
    for result in ("yes", "no"):
        clone = led.record_settlements([ex_settlement(result=result, yes_count="1.00", no_count="1.00", revenue=0,
                                                      fee=str(fees))])
        assert clone[0]["pnl"] == D("1") - cost - fees


def test_recording_a_settlement_twice_updates_the_same_row(led):
    filled_order(led)
    led.record_settlements([ex_settlement(revenue=100)])
    led.record_settlements([ex_settlement(revenue=100)])
    assert len(led._rows("SELECT * FROM exec_settlements")) == 1
    assert led.realized_pnl() == D("0.393067")


def test_settlement_rows_need_a_ticker(led):
    with pytest.raises(ValueError):
        led.record_settlements([{"revenue": 0}])


# ============================================================================================ loss accounting
def test_realized_loss_24h_rolls_but_the_total_never_resets(led, clock):
    filled_order(led)
    led.record_settlements([ex_settlement(result="no", revenue=0, settled_at=clock.now - 25 * 3600)])
    loss = D("0.59") + FEE_YES_59
    assert led.realized_loss_total() == loss
    assert led.realized_loss_24h() == 0                                      # 25 h old: outside the window
    clock.advance(-3 * 3600)
    assert led.realized_loss_24h() == loss                                   # 22 h old: inside


def test_the_total_is_net_so_profits_offset_losses(led):
    filled_order(led, order_id="o1", fill_id="f1")
    other = TICKER.replace("-30", "-45")
    filled_order(led, order_id="o2", fill_id="f2", ticker=other, market=base_market(ticker=other))
    led.record_settlements([ex_settlement(TICKER, result="yes", revenue=100),
                            ex_settlement(other, result="no", revenue=0)])
    net = (D("1") - D("0.59") - FEE_YES_59) + (-D("0.59") - FEE_YES_59)
    assert led.realized_pnl() == net and led.realized_loss_total() == -net and net < 0
    led.record_settlements([ex_settlement(other, result="yes", revenue=100)])
    assert led.realized_loss_total() == 0


def test_an_unknown_order_counts_in_full_toward_the_open_worst_case(led):
    coid = led.new_intent(entry_spec())
    assert led.open_worst_case() == WORST_YES_59                              # intent: it may be sent already
    led.mark_result(coid, err_result("unknown"))
    assert led.open_worst_case() == WORST_YES_59 and led.open_exposure() == D("0.59")
    led.mark_presumed_not_placed(coid, "not found")
    assert led.open_worst_case() == 0                                         # never placed: no exposure


@pytest.mark.parametrize("outcome", ["rejected", "throttled", "auth"])
def test_orders_that_were_never_placed_do_not_count(led, outcome):
    coid = led.new_intent(entry_spec())
    led.mark_result(coid, err_result(outcome, 400))
    assert led.open_worst_case() == 0 and led.open_exposure() == 0


def test_a_fill_the_order_reported_but_not_fetched_counts_once_fetched_it_is_exact(led):
    place(led, entry_spec(), order_id="ord-1")                                # executed, fill not fetched yet
    assert led.open_worst_case() == WORST_YES_59
    led.upsert_fills([ex_fill("f1", "ord-1", fee=str(FEE_YES_59))])
    assert led.open_worst_case() == D("0.59") + FEE_YES_59                    # exact fee, no estimate, not doubled
    assert led.open_exposure() == D("0.59")


def test_an_unresolved_order_whose_fill_was_fetched_is_not_counted_twice(led):
    coid = led.new_intent(entry_spec())
    led.mark_result(coid, err_result("unknown"))
    led.apply_exchange_order(ex_order(coid, "ex-1", status="resting", fill="0.00", remaining="1.00"))  # id known
    led._update_order(coid, {"status": "unknown"})                            # (still unresolved in the ledger)
    led.upsert_fills([ex_fill("f1", "ex-1", fee=str(FEE_YES_59))])
    assert led.open_worst_case() == D("0.59") + FEE_YES_59                    # not 2 x the position


def test_the_risk_snapshot_equals_the_separate_calls_in_a_mixed_book(led, clock):
    """One pass over the fills must give exactly what four separate calls give: settled wins and losses inside and
    outside the 24 h window, an open position, a hedged pair, an unknown order and a rejected one."""
    filled_order(led, order_id="o1", fill_id="f1", ticker="KXBTC15M-A")
    filled_order(led, order_id="o2", fill_id="f2", ticker="KXBTC15M-B")
    filled_order(led, order_id="o3", fill_id="f3", ticker="KXBTC15M-C")
    led.record_settlements([ex_settlement("KXBTC15M-A", result="yes", revenue=100, settled_at=clock.now - 3600),
                            ex_settlement("KXBTC15M-B", result="no", revenue=0, settled_at=clock.now - 3 * 86400)])
    filled_order(led, outcome="no", price="0.42", order_id="o4", fill_id="f4", ticker="KXBTC15M-C", fee="0.017052")
    led.mark_result(led.new_intent(entry_spec(base_market(ticker="KXBTC15M-D"))), err_result("unknown"))
    led.mark_result(led.new_intent(entry_spec(base_market(ticker="KXBTC15M-E"))), err_result("rejected", 400))
    snap = led.risk_snapshot()
    assert snap == {"realized_loss_24h": led.realized_loss_24h(), "realized_loss_total": led.realized_loss_total(),
                    "open_worst_case": led.open_worst_case(), "open_exposure": led.open_exposure()}
    assert snap["realized_loss_24h"] == 0 and snap["realized_loss_total"] > 0 and snap["open_worst_case"] > 0
    assert led.risk_snapshot() == snap                                           # reading changes nothing


def test_random_books_match_an_independent_model_of_the_loss_and_exposure(tmp_path):
    """300 seeded trades: yes/no, odd prices, partial sizes, hedges on one ticker, settled and unsettled, unknown and
    rejected orders. The expected numbers are computed here from scratch (cost, fees, payout, $1 per YES+NO pair)."""
    import random
    rng = random.Random(20261005)
    clock = Clock()
    led = make_ledger(tmp_path, clock)
    book = {}                                                  # ticker -> [yes, no, cost, fees]
    unknown = {}                                               # ticker -> [worst case, collateral] of unknown orders
    for i in range(300):
        t = f"KXBTC15M-R{rng.randrange(40)}"
        outcome = rng.choice(["yes", "no"])
        price = D(rng.randrange(3, 98)) / 100
        count = rng.choice([D("1"), D("0.5"), D("0.25")])
        spec = entry_spec(base_market(ticker=t), outcome, price, count)
        kind = rng.random()
        if kind < 0.08:                                         # an unknown outcome: counts in full until resolved
            led.mark_result(led.new_intent(spec), err_result("unknown"))
            side, yes_price = units.to_book(outcome, price)
            collateral = units.collateral(side, yes_price, count)
            row = unknown.setdefault(t, [D(0), D(0)])
            row[0] += collateral + units.worst_fee(count, yes_price)
            row[1] += collateral
        elif kind < 0.16:                                       # rejected: never placed
            led.mark_result(led.new_intent(spec), err_result("rejected", 400))
        else:                                                   # filled in full and fetched
            oid = f"o{i}"
            place(led, spec, order_id=oid, fill=f"{count:.2f}")
            fee = units.taker_fee(count, price)
            led.upsert_fills([ex_fill(f"f{i}", oid, t, outcome=outcome, price=str(price), count=f"{count:.2f}",
                                      fee=str(fee))])
            row = book.setdefault(t, [D(0), D(0), D(0), D(0)])
            row[0 if outcome == "yes" else 1] += count
            row[2] += count * price
            row[3] += fee
    settled = {}
    for t in sorted(book):
        if rng.random() < 0.5:
            result = rng.choice(["yes", "no"])
            yes, no, cost, fees = book[t]
            settled[t] = (D(0) + (yes if result == "yes" else no)) - cost - fees
            led.record_settlements([ex_settlement(t, result=result, yes_count=f"{yes:.2f}", no_count=f"{no:.2f}",
                                                  revenue=int((yes if result == "yes" else no) * 100), fee=str(fees),
                                                  settled_at=clock.now - rng.randrange(0, 2 * 86400))])
    net_total = sum(settled.values(), D(0))
    open_worst = open_exposure = D(0)
    for t, (yes, no, cost, fees) in book.items():
        if t not in settled:
            netted = min(yes, no)
            open_worst += max(D(0), cost + fees - netted)
            open_exposure += max(D(0), cost - netted)
    for t, (worst, collateral) in unknown.items():              # an unknown order on a settled market no longer counts
        if t not in settled:
            open_worst += worst
            open_exposure += collateral
    snap = led.risk_snapshot()
    assert snap["realized_loss_total"] == max(D(0), -net_total)
    assert snap["open_worst_case"] == open_worst and snap["open_exposure"] == open_exposure
    since = clock.now - 86400
    day = sum((p for t, p in settled.items()
               if led._rows("SELECT settled_ts FROM exec_settlements WHERE ticker = ?", (t,))[0]["settled_ts"] >= since),
              D(0))
    assert snap["realized_loss_24h"] == max(D(0), -day)
    assert snap == {"realized_loss_24h": led.realized_loss_24h(), "realized_loss_total": led.realized_loss_total(),
                    "open_worst_case": led.open_worst_case(), "open_exposure": led.open_exposure()}


def test_a_hedged_position_nets_to_its_fees(led):
    filled_order(led, outcome="yes", price="0.59", order_id="o1", fill_id="f1")
    filled_order(led, outcome="no", price="0.42", order_id="o2", fill_id="f2", fee="0.017052")
    assert led.open_worst_case() == D("1.01") + FEE_YES_59 + FEE_NO_42 - 1
    assert led.open_exposure() == D("0.01")


def test_a_settled_position_leaves_the_open_worst_case(led):
    filled_order(led)
    assert led.open_worst_case() == D("0.59") + FEE_YES_59
    led.record_settlements([ex_settlement(result="no", revenue=0)])
    assert led.open_worst_case() == 0 and led.open_exposure() == 0
    assert led.realized_loss_24h() == D("0.59") + FEE_YES_59


def test_unsettled_and_reconcile_tickers(led):
    coid = led.new_intent(entry_spec())
    led.mark_result(coid, err_result("unknown"))
    assert led.tickers_to_reconcile() == [TICKER] and led.unsettled_tickers() == [TICKER]
    led.apply_exchange_order(ex_order(coid, "ex-1"))
    led.upsert_fills([ex_fill("f1", "ex-1")])
    led.record_settlements([ex_settlement(result="yes", revenue=100)])
    assert led.tickers_to_reconcile() == [] and led.unsettled_tickers() == []


def test_pnl_for_orders_gives_the_legs_of_one_pair(led):
    c1 = filled_order(led, outcome="yes", price="0.59", order_id="o1", fill_id="f1")
    hourly = hourly_market()
    c2 = filled_order(led, outcome="no", price="0.40", order_id="o2", fill_id="f2", ticker=HOURLY_TICKER,
                      market=hourly)
    r = led.pnl_for_orders([c1, c2], {TICKER: "yes", HOURLY_TICKER: "yes"})
    assert r["cost"] == D("0.99") and r["payout"] == D("1")
    assert r["pnl"] == D("1") - D("0.99") - r["fees"]
    assert led.pnl_for_orders([c1, c2], {TICKER: "yes"})["pnl"] is None       # a result is still missing


# ============================================================================================ counters and scoping
def test_counters(led, clock):
    led.new_intent(entry_spec())
    clock.advance(30)
    led.new_intent(entry_spec())
    assert led.count_orders_since(clock.now - 60) == 2 and led.count_orders_since(clock.now - 10) == 1
    assert led.count_orders_since(None) == 2 and led.touched_tickers() == [TICKER]
    assert led.find_intent(TICKER, "bid", "0.5900", "1.00", clock.now - 60)
    assert led.worst_of_orders_since(clock.now - 10) == WORST_YES_59
    assert led.last_activity_ts(TICKER) == clock.now and led.last_activity_ts("OTHER") is None


def test_everything_is_scoped_to_the_environment(tmp_path, clock):
    conn = db.connect(tmp_path / "shared.sqlite")
    db.init_schema(conn)
    prod = Ledger(conn, "run-p", "prod", clock=clock, log_path=tmp_path / "p.jsonl")
    demo = Ledger(conn, "run-d", "demo", clock=clock, log_path=tmp_path / "d.jsonl")
    filled_order(demo)
    demo.record_settlements([ex_settlement(result="no", revenue=0)])
    assert demo.realized_loss_total() > 0 and demo.open_orders() == []
    assert prod.realized_loss_total() == 0 and prod.realized_loss_24h() == 0 and prod.open_worst_case() == 0
    assert prod.count_orders_since(None, run_only=False) == 0 and prod.settled_tickers() == set()
    assert prod.orders_for(TICKER) == [] and prod.fills_for(TICKER) == []
    coid = prod.new_intent(entry_spec())
    assert demo.order(coid) is None                                            # one env never sees the other's order


# ============================================================================================ halts and lockout
def test_halt_notifies_listeners_and_is_recorded(led):
    seen = []
    led.add_halt_listener(seen.append)
    led.add_halt_listener(seen.append)                                          # registering twice is harmless
    led.add_halt_listener(lambda r: 1 / 0)                                      # a failing listener cannot block the rest
    led.halt("mismatch on X", {"tickers": ["X"]})
    assert seen == ["mismatch on X"]
    last = led.last_halt()
    assert last["reason"] == "mismatch on X" and last["ts"] == int(led.clock())
    assert Ledger(led.conn, "run-2", "prod", clock=led.clock, log_path=led._log_path).last_halt()["reason"] == \
        "mismatch on X"                                                         # persisted: a restart sees it
    assert Ledger(led.conn, "run-2", "demo", clock=led.clock, log_path=led._log_path).last_halt() is None


def test_a_lockout_survives_a_restart(led, clock):
    assert led.lockout_until() == 0
    led.set_lockout(clock.now + 60, "cancel_all")
    assert led.lockout_until() == clock.now + 60
    fresh = Ledger(led.conn, "run-2", "prod", clock=clock, log_path=led._log_path)
    assert fresh.lockout_until() == clock.now + 60
    assert Ledger(led.conn, "run-2", "demo", clock=clock, log_path=led._log_path).lockout_until() == 0


def test_summary_is_plain_text_numbers(led):
    filled_order(led)
    s = led.summary()
    assert s["open_worst_case"] == plain(D("0.59") + FEE_YES_59) and s["open_orders"] == 0
    json.dumps(s)


# ============================================================================================ audit without secrets
KEY_ID = "99999999-aaaa-4bbb-8ccc-dddddddddddd"                                  # synthetic: not a real key id


class CaptureTransport:
    """A transport that records exactly what ExecClient sends (headers included) and answers from a table."""

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, method, url, *, params, data, headers, timeout):
        from types import SimpleNamespace
        self.sent.append({"method": method, "url": url, "params": params, "data": data, "headers": dict(headers)})
        if method == "POST":
            body = json.loads(data)
            return SimpleNamespace(status_code=201, headers={}, text=json.dumps(
                {"order_id": "ord-1", "client_order_id": body["client_order_id"], "fill_count": "1.00",
                 "remaining_count": "0.00", "average_fill_price": "0.5900", "average_fee_paid": "0.016933",
                 "ts_ms": 1791224910123}))
        return SimpleNamespace(status_code=200, headers={}, text=json.dumps(
            {"balance": 2943, "balance_dollars": "29.4300", "balance_breakdown": []}))


def dump_everything(conn, jsonl_path) -> str:
    parts = []
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        for row in conn.execute(f"SELECT * FROM {name}"):
            parts.append(" ".join(str(x) for x in tuple(row)))
    if jsonl_path.exists():
        parts.append(jsonl_path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_a_real_client_session_leaves_no_secret_in_the_db_or_the_logs(tmp_path, clock):
    key = Ed25519PrivateKey.generate()
    pem_path = tmp_path / "secret-key.pem"
    pem_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    led = make_ledger(tmp_path, clock, redact=[KEY_ID, str(pem_path)])
    led.live = True
    env = Env("prod", HOSTS["prod"]["rest"], HOSTS["prod"]["ws"], key_id=KEY_ID, key=key)
    transport = CaptureTransport()
    client = ExecClient(env, live=True, transport=transport, audit=led.audit_request, clock=clock, sleep=clock.sleep)

    client.balance(SHARD)
    coid = led.new_intent(entry_spec())
    result = client.create_order(led.request_body(coid))
    assert led.mark_result(coid, result) == "executed"

    rows = led._rows("SELECT * FROM exec_requests ORDER BY id")
    assert [(r["method"], r["path"], r["kind"], r["status"], r["live"]) for r in rows] == [
        ("GET", "/trade-api/v2/portfolio/balance", "read", 200, 1),
        ("POST", "/trade-api/v2/portfolio/events/orders", "write", 201, 1)]
    assert rows[1]["client_order_id"] == coid and json.loads(rows[1]["body_json"])["price"] == "0.5900"

    secrets = [KEY_ID, str(pem_path), "PRIVATE KEY"]
    for call in transport.sent:                                                  # what really went over the wire
        assert call["headers"]["KALSHI-ACCESS-KEY"] == KEY_ID
        secrets.append(call["headers"]["KALSHI-ACCESS-SIGNATURE"])
    dump = dump_everything(led.conn, tmp_path / "exec.jsonl")
    for s in secrets:
        assert s not in dump, f"a secret reached the ledger or the log: {s[:12]}..."
    assert "KALSHI-ACCESS" not in dump                                           # no header names either


def test_hostile_audit_fields_are_dropped_or_redacted(tmp_path, clock):
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    pem_path = tmp_path / "k.pem"
    led = make_ledger(tmp_path, clock, redact=[KEY_ID, str(pem_path)])
    led.audit_request(method="get", path=f"https://host.example/trade-api/v2/portfolio/balance?token=abc",
                      headers={"KALSHI-ACCESS-KEY": KEY_ID, "KALSHI-ACCESS-SIGNATURE": "c2lnbmF0dXJl"},
                      key_id=KEY_ID, private_key_path=str(pem_path), password="hunter2",
                      body={"ticker": TICKER, "nested": {"api_key": "zzz-secret", "keep": 1}, "passphrase": "pw"},
                      params={"cursor": "c1", "access_token": "tok-123"},
                      response=f"oops {pem} and {KEY_ID}", error=f"could not read {pem_path}", extra_note="ok",
                      status=500, latency_ms=3.0, outcome="unknown", kind="read", attempt=1)
    (row,) = led._rows("SELECT * FROM exec_requests")
    assert row["path"] == "/trade-api/v2/portfolio/balance"                      # no host, no query string
    assert row["method"] == "GET" and row["status"] == 500
    dump = dump_everything(led.conn, tmp_path / "exec.jsonl")
    for s in (KEY_ID, str(pem_path), "PRIVATE KEY", "hunter2", "zzz-secret", "c2lnbmF0dXJl", "tok-123", '"pw"', "abc"):
        assert s not in dump, s
    extra = json.loads(row["extra_json"])
    assert extra["dropped_fields"] >= 4 and extra["extra_note"] == "ok"
    assert json.loads(row["body_json"])["nested"]["keep"] == 1 and json.loads(row["body_json"])["ticker"] == TICKER


def test_a_dry_run_request_is_flagged_and_audit_never_raises(tmp_path, clock, monkeypatch):
    led = make_ledger(tmp_path, clock)
    led.audit_request(env="prod", live=False, method="GET", path="/trade-api/v2/exchange/status", kind="public",
                      status=200, response='{"ok": true}')
    assert led._rows("SELECT live, kind, response FROM exec_requests")[0] == {"live": 0, "kind": "public",
                                                                              "response": '{"ok": true}'}

    def boom(*a, **k):
        raise sqlite3.OperationalError("disk is full")

    monkeypatch.setattr(led, "_insert", boom)
    assert led.audit_request(method="POST", path="/x") is None                   # an audit failure never reaches the order path
    assert led.audit_errors == 1


def test_responses_are_cut_at_64_kb_and_the_file_log_gets_a_shorter_copy(tmp_path, clock):
    led = make_ledger(tmp_path, clock)
    led.audit_request(method="GET", path="/trade-api/v2/markets", response="x" * 70_000, status=200)
    assert len(led._rows("SELECT response FROM exec_requests")[0]["response"]) == 65_536
    line = json.loads((tmp_path / "exec.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert line["component"] == "exec.ledger" and len(line["response"]) == 4000


def test_every_request_attempt_gets_a_row(tmp_path, clock):
    led = make_ledger(tmp_path, clock)
    for attempt in (1, 2, 3):
        led.audit_request(method="POST", path="/trade-api/v2/portfolio/events/orders", kind="write", attempt=attempt,
                          status=429, outcome="throttled", client_order_id="c-1")
    assert [r["attempt"] for r in led._rows("SELECT attempt FROM exec_requests ORDER BY id")] == [1, 2, 3]


def test_status_sets_are_consistent():
    assert not (OPEN_STATUSES & FINAL_STATUSES)
    assert UNRESOLVED_STATUSES <= OPEN_STATUSES and NOT_PLACED_STATUSES <= FINAL_STATUSES


def test_close_time_constant_is_a_monday_90s_ahead():
    assert CLOSE_TS - NOW == 90 and iso(CLOSE_TS).startswith("2026-10-05T18:30:00")
