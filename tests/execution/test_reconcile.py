"""kpm.execution.reconcile: reconcile, settle and the cancel path. Offline, fake exchange, fake clock.

Reconcile and the cancel path never create an order (the fake exchange raises if they try). Unknown outcomes are looked
up by client_order_id and never resent; a mismatch is real only when a second read agrees; a real mismatch halts Safety.
"""
from __future__ import annotations

import atexit
import json
import signal
import threading
from decimal import Decimal as D

import pytest

from kpm.execution import reconcile as rec_mod
from kpm.execution import units
from kpm.execution.client import AuthError
from kpm.execution.reconcile import (CancelReport, ExitGuard, ReconcileReport, SettleReport, cancel_path, reconcile,
                                     reconcile_all, settle)
from kpm.execution.safety import Safety

from .exec_fakes import (SHARD, TICKER, Clock, FakeClient, ScriptedFailure, base_market, entry_spec, err_result,
                         ex_fill, ex_order, ex_settlement, make_book, make_ledger, ok_result, seed_fill)

FEE = units.taker_fee(D(1), D("0.59"))                       # 0.016933
PHRASE = "LIVE PROD 5"


class Rec:
    """A ledger, a fake exchange, a Safety (registered as the ledger's halt listener) and a fake clock."""

    def __init__(self, tmp_path, *, live=True):
        self.clock = Clock()
        self.ledger = make_ledger(tmp_path, self.clock)
        self.client = FakeClient(self.clock, live=live)
        self.kill = tmp_path / "KILL"
        self.safety = Safety({"enabled": True, "kill_switch_file": str(self.kill)}, self.ledger, self.client,
                             self.clock, sleep=self.clock.sleep, isatty=lambda: True)

    def reconcile(self, tickers=None, **kw) -> ReconcileReport:
        return reconcile(self.client, self.ledger, tickers, sleep=self.clock.sleep, **kw)

    def settle(self, tickers=None, **kw) -> SettleReport:
        return settle(self.client, self.ledger, tickers, **kw)

    def cancel(self, reason="test", touched=None, **kw) -> CancelReport:
        return cancel_path(self.client, self.ledger, reason, touched, sleep=self.clock.sleep, **kw)

    # -- ledger states
    def unknown_order(self, *, age=0.0, status="unknown", sent=True, outcome="yes", price="0.59") -> str:
        """An IOC whose outcome is unknown (a timeout), created ``age`` seconds ago."""
        coid = self.ledger.new_intent(entry_spec(base_market(), outcome, price))
        if sent:
            self.ledger.mark_sent([coid], self.clock.now)
        if status == "unknown":
            self.ledger.mark_result(coid, err_result("unknown", None, "transport_error", coid))
        elif status == "duplicate":
            self.ledger.mark_result(coid, err_result("duplicate", 409, "order_already_exists", coid))
        self.clock.advance(age)
        return coid

    def resting_order(self, *, order_id="ex-r", ticker=TICKER, ledger_ei=SHARD, exchange_ei=SHARD) -> str:
        """A GTC bid resting on the exchange and known to the ledger."""
        spec = entry_spec(base_market(ticker=ticker), "yes", "0.40")
        spec["time_in_force"] = "good_till_canceled"
        spec["exchange_index"] = ledger_ei
        coid = self.ledger.new_intent(spec)
        self.ledger.mark_result(coid, ok_result(coid, order_id, fill="0.00", remaining="1.00"))
        self.client.exchange_orders.append(ex_order(coid, order_id, ticker, price="0.40", fill="0.00", remaining="1.00",
                                                    status="resting", ei=exchange_ei))
        return coid

    def on_exchange(self, coid, order_id="ex-1", *, outcome="yes", price="0.59", fee=None, position=True) -> None:
        """What the exchange holds after the order filled: the order, its fill and the position."""
        fee = fee or str(units.taker_fee(D(1), D(price)))
        self.client.exchange_orders.append(ex_order(coid, order_id, outcome=outcome, price=price, fee=fee))
        self.client.exchange_fills.append(ex_fill(f"fill-{order_id}", order_id, outcome=outcome, price=price, fee=fee))
        if position:
            self.client.exchange_positions[TICKER] = "1.00" if outcome == "yes" else "-1.00"

    def no_writes(self) -> None:
        assert self.client.calls_to("cancel_order") == [] and self.client.calls_to("cancel_all") == []


@pytest.fixture
def r(tmp_path):
    return Rec(tmp_path)


# ============================================================================================ reconcile: unknown outcomes
def test_a_timed_out_order_is_found_by_client_order_id_adopted_and_never_resent(r):
    coid = r.unknown_order()
    r.on_exchange(coid)
    rep = r.reconcile([TICKER])
    assert rep.ok and rep.adopted == [coid] and rep.fills_added == 1 and not rep.halted and rep.mismatches == []
    row = r.ledger.order(coid)
    assert (row["status"], row["order_id"], row["fill_count"], row["remaining_count"]) == ("executed", "ex-1", "1.00",
                                                                                          "0.00")
    assert r.ledger.unresolved_orders() == [] and r.ledger.position(TICKER, include_unrecorded=False) == 1
    assert r.ledger.open_worst_case() == D("0.59") + FEE
    assert rep.cash == {"2": "29.4300"} and r.client.calls_to("balance") == [{"exchange_index": SHARD}]
    r.no_writes()                                                  # (create_order would have raised AssertionError)
    assert not r.safety.halted and not r.kill.exists()


def test_the_default_tickers_are_the_ledgers_open_and_unsettled_ones(r):
    coid = r.unknown_order()
    r.on_exchange(coid)
    rep = r.reconcile()
    assert rep.tickers == [TICKER] and rep.adopted == [coid]


def test_an_unknown_order_that_is_not_found_yet_stays_pending_until_it_is_old_enough(r):
    coid = r.unknown_order(age=5)
    rep = r.reconcile([TICKER])
    assert rep.pending == [coid] and rep.presumed_not_placed == [] and rep.ok and not rep.halted
    assert r.ledger.order(coid)["status"] == "unknown"             # not judged: nothing resolves it by itself
    assert r.ledger.open_worst_case() > 0                          # and it still counts in full toward the cap


def test_an_unknown_order_that_stays_missing_is_presumed_not_placed_and_halts_but_is_never_resent(r):
    coid = r.unknown_order(age=20)
    rep = r.reconcile([TICKER])
    assert rep.presumed_not_placed == [coid] and rep.halting == [coid] and rep.halted and not rep.ok
    assert r.ledger.order(coid)["status"] == "presumed_not_placed" and r.ledger.open_worst_case() == 0
    assert r.safety.halted and r.kill.exists() and "not found on the exchange" in r.safety.halt_reason
    r.no_writes()
    last = r.ledger.last_halt()
    assert last and "not found" in last["reason"]


def test_the_wait_for_fresh_data_is_bounded_to_ten_seconds(r):
    coid = r.unknown_order(age=20)
    r.client.data_lag_s = 60                                       # the exchange's portfolio data is a minute behind
    rep = r.reconcile([TICKER])
    assert r.clock.sleeps.count(1.0) == 10 and sum(r.clock.sleeps) == 10     # polled for 10 s, then went on
    assert rep.presumed_not_placed == [coid] and r.client.calls_to("user_data_timestamp")


def test_fresh_data_means_no_waiting_at_all(r):
    coid = r.unknown_order(age=20)
    r.reconcile([TICKER])
    assert r.clock.sleeps == []
    assert r.ledger.order(coid)["status"] == "presumed_not_placed"


def test_an_intent_that_was_never_sent_is_presumed_not_placed_without_a_halt(r):
    coid = r.unknown_order(status="intent", sent=False, age=30)    # a crash between the commit and the send
    rep = r.reconcile([TICKER])
    assert rep.presumed_not_placed == [coid] and rep.halting == [] and rep.ok and not rep.halted
    assert any("never sent" in w for w in rep.warnings) and not r.safety.halted
    assert r.ledger.order(coid)["status"] == "presumed_not_placed"


def test_a_409_whose_order_is_not_listed_is_a_mismatch(r):
    r.unknown_order(status="duplicate", age=20)
    rep = r.reconcile([TICKER])
    assert not rep.ok and rep.halted and any("client_order_id exists" in m for m in rep.mismatches)


def test_an_order_presumed_not_placed_that_shows_up_later_is_a_halting_mismatch(r):
    coid = r.unknown_order(age=20)
    r.reconcile([TICKER])
    r.kill.unlink()
    r.safety.halted = False
    r.on_exchange(coid)                                            # it was placed after all
    rep = r.reconcile([TICKER])
    assert not rep.ok and any("presumed not placed" in m for m in rep.mismatches) and rep.halted
    assert r.ledger.order(coid)["status"] == "executed" and r.ledger.position(TICKER) == 1


# ============================================================================================ reconcile: lag and mismatches
def test_a_stale_first_read_is_not_a_mismatch_when_the_second_read_agrees(r):
    coid = r.unknown_order()
    r.on_exchange(coid)
    stale = {"ticker": TICKER, "position_fp": "0.00"}
    fresh = {"ticker": TICKER, "position_fp": "1.00"}
    r.client.script["positions"] = [[stale], [fresh]]               # the exchange's position lags, then catches up
    rep = r.reconcile([TICKER])
    assert rep.ok and rep.mismatches == [] and not rep.halted and not r.safety.halted
    assert len(rep.lag_cleared) == 1 and "position on" in rep.lag_cleared[0]
    assert len(r.client.calls_to("positions")) == 2 and 5.0 in r.clock.sleeps    # re-read 5 s later


def test_two_reads_five_seconds_apart_that_agree_are_a_real_mismatch_and_halt_the_session(r):
    coid = r.unknown_order()
    r.on_exchange(coid)
    r.client.exchange_positions[TICKER] = "0.00"                    # the exchange says flat; our fills say long
    rep = r.reconcile([TICKER])
    assert not rep.ok and rep.halted and len(rep.mismatches) == 1
    assert "position on" in rep.mismatches[0] and "exchange 0" in rep.mismatches[0]
    assert r.safety.halted and r.kill.exists() and "reconcile mismatch" in r.safety.halt_reason
    assert len(r.client.calls_to("positions")) == 2 and r.clock.sleeps.count(5.0) == 1
    r.no_writes()


def test_a_discrepancy_that_only_shows_up_on_the_second_read_is_a_warning_not_yet_a_mismatch(r):
    coid = r.unknown_order()
    r.on_exchange(coid)
    r.client.script["positions"] = [[{"ticker": TICKER, "position_fp": "0.00"}],
                                    [{"ticker": TICKER, "position_fp": "5.00"}]]
    rep = r.reconcile([TICKER])
    assert rep.mismatches == [] and rep.ok and any("not yet confirmed" in w for w in rep.warnings)


def test_a_wait_for_fresh_data_that_times_out_leaves_the_discrepancy_unconfirmed_not_a_mismatch(r):
    """Review finding: with the portfolio data lagging past the 10 s wait, two reads 5 s apart that both showed the lag were
    declared a real mismatch: Safety tripped and data/KILL was written although nothing was inconsistent. Design 5.5 item 7
    says to declare a mismatch only after as_of_time is past the event AND two reads 5 s apart agree."""
    coid = r.unknown_order()
    r.on_exchange(coid)
    r.client.data_lag_s = 60
    r.client.exchange_positions[TICKER] = "0.00"
    rep = r.reconcile([TICKER])
    assert rep.mismatches == [] and not rep.halted and not r.safety.halted and not r.kill.exists()
    assert len(rep.unconfirmed) == 1 and "position on" in rep.unconfirmed[0]
    assert rep.incomplete == [TICKER] and not rep.ok                                  # reported, so it is not forgotten
    assert any("not confirmed fresh" in w for w in rep.warnings)
    assert any("not declared a mismatch" in w for w in rep.warnings)
    assert r.clock.sleeps.count(1.0) == 10 and r.clock.sleeps[-1] == 5.0
    assert r.ledger.last_halt() is None


def test_the_reviewers_scenario_a_fill_reported_by_the_reply_while_the_portfolio_data_lags_is_not_a_mismatch(r):
    """The create-order reply says filled; /portfolio/fills, /orders and /positions show nothing for 60 s."""
    coid = r.ledger.new_intent(entry_spec(base_market(), "yes", "0.59"))
    r.ledger.mark_sent([coid], r.clock.now)
    r.ledger.mark_result(coid, ok_result(coid, "ex-1", fill="1.00"))                  # the reply: filled 1.00
    r.client.data_lag_s = 60
    rep = r.reconcile([TICKER])
    assert [d for d in rep.unconfirmed if "fills stored 0 != fill_count 1" in d]
    assert not rep.halted and not r.safety.halted and not r.kill.exists()


def test_the_data_catching_up_during_the_recheck_makes_the_two_reads_decisive_again(r):
    coid = r.unknown_order()
    r.on_exchange(coid)
    r.client.data_lag_s = 12                                       # 10 s of waiting is not enough, 10 + 5 s is
    r.client.exchange_positions[TICKER] = "0.00"
    rep = r.reconcile([TICKER])
    assert len(rep.mismatches) == 1 and rep.unconfirmed == [] and rep.halted and r.safety.halted and r.kill.exists()


def test_an_unreadable_user_data_timestamp_leaves_it_unconfirmed_too(r):
    coid = r.unknown_order()
    r.on_exchange(coid)
    r.client.fail["user_data_timestamp"] = ScriptedFailure("503")
    r.client.exchange_positions[TICKER] = "0.00"
    rep = r.reconcile([TICKER])
    assert rep.mismatches == [] and len(rep.unconfirmed) == 1 and not rep.halted and not r.safety.halted


def test_an_unconfirmed_discrepancy_is_looked_at_again_by_the_next_reconcile(r):
    coid = r.unknown_order()
    r.on_exchange(coid)
    r.client.data_lag_s = 60
    r.client.exchange_positions[TICKER] = "0.00"
    assert r.reconcile([TICKER]).unconfirmed
    r.client.data_lag_s = 0                                         # the data has caught up, and the exchange is right
    r.client.exchange_positions[TICKER] = "1.00"
    rep = r.reconcile([TICKER])
    assert rep.ok and rep.unconfirmed == [] and not r.safety.halted
    r.client.exchange_positions[TICKER] = "0.00"                    # ... and when it is still wrong with FRESH data: real
    rep = r.reconcile([TICKER])
    assert len(rep.mismatches) == 1 and rep.halted and r.safety.halted


def test_fills_that_do_not_add_up_to_the_orders_fill_count_are_a_mismatch(r):
    coid = r.unknown_order()
    r.client.exchange_orders.append(ex_order(coid, "ex-1", fill="1.00"))
    r.client.exchange_fills.append(ex_fill("f1", "ex-1", count="0.40"))
    r.client.exchange_positions[TICKER] = "1.00"
    rep = r.reconcile([TICKER])
    assert not rep.ok and rep.halted
    assert any("fills stored 0.40 != fill_count 1.00" in m for m in rep.mismatches)
    assert any(m.startswith("position on") for m in rep.mismatches)


def test_a_wrong_ticker_or_side_on_the_exchange_halts_at_once(r):
    coid = r.unknown_order()
    r.client.script["orders"] = [[ex_order(coid, "ex-1", ticker="KXBTC15M-OTHER", fill="0.00", remaining="0.00",
                                           status="canceled", book_side="ask")]]
    rep = r.reconcile([TICKER])
    assert not rep.ok and rep.halted
    joined = " | ".join(rep.mismatches)
    assert "ticker" in joined and "side" in joined
    assert r.client.calls_to("positions") != [] and len(r.client.calls_to("orders")) >= 1


def test_an_ioc_order_that_is_resting_is_an_anomaly(r):
    coid = r.unknown_order()
    r.client.exchange_orders.append(ex_order(coid, "ex-1", status="resting", fill="0.00", remaining="1.00"))
    rep = r.reconcile([TICKER])
    assert not rep.ok and any("is resting on the exchange" in m for m in rep.mismatches)


def test_a_maker_fill_on_an_ioc_order_is_only_a_warning(r):
    coid = r.unknown_order()
    r.client.exchange_orders.append(ex_order(coid, "ex-1"))
    r.client.exchange_fills.append(ex_fill("f1", "ex-1", is_taker=False))
    r.client.exchange_positions[TICKER] = "1.00"
    rep = r.reconcile([TICKER])
    assert rep.ok and any("is_taker=false" in w for w in rep.warnings)


def test_halting_can_be_switched_off(r):
    coid = r.unknown_order()
    r.on_exchange(coid)
    r.client.exchange_positions[TICKER] = "0.00"
    rep = r.reconcile([TICKER], halt=False)
    assert not rep.ok and rep.mismatches and not rep.halted and not r.safety.halted


# ============================================================================================ reconcile: manual trades
def test_a_manual_trade_on_the_same_ticker_is_never_stored_and_never_counts(r):
    coid = r.unknown_order()
    r.on_exchange(coid)
    r.client.exchange_orders.append(ex_order("manual-1", "m-1", price="0.55", count="2.00", fill="2.00"))
    r.client.exchange_fills.append(ex_fill("f-manual", "m-1", price="0.55", count="2.00", fee="0.034650"))
    r.client.exchange_positions[TICKER] = "3.00"                    # ours +1 and the manual +2
    rep = r.reconcile([TICKER])
    assert rep.ok and rep.foreign_fills == 1 and rep.mismatches == [] and not rep.halted
    assert [f["fill_id"] for f in r.ledger.fills_for(TICKER)] == ["fill-ex-1"]
    assert r.ledger.order("manual-1") is None
    assert r.ledger.open_worst_case() == D("0.59") + FEE            # the manual trade is outside the bot's cap


def test_a_manual_no_trade_is_signed_against_the_position(r):
    coid = r.unknown_order()
    r.on_exchange(coid)
    r.client.exchange_fills.append(ex_fill("f-manual", "m-1", outcome="no", price="0.40", count="3.00"))
    r.client.exchange_positions[TICKER] = "-2.00"                    # +1 (ours) - 3 (manual NO)
    assert r.reconcile([TICKER]).ok


def test_tickers_with_no_order_in_this_ledger_are_skipped(r):
    rep = r.reconcile(["KXBTC15M-NOT-OURS"])
    assert rep.skipped == ["KXBTC15M-NOT-OURS"] and rep.ok and r.client.calls == []


# ============================================================================================ reconcile: settled markets
def test_a_ticker_already_settled_in_the_ledger_skips_the_position_check(r):
    seed_fill(r.ledger)
    r.ledger.record_settlements([ex_settlement(result="yes", revenue=100)])
    r.client.exchange_positions.clear()                              # a settled position leaves the unsettled listing
    rep = r.reconcile([TICKER])
    assert rep.ok and r.client.calls_to("positions") == []


def test_a_position_that_vanished_because_the_market_settled_is_not_a_mismatch(r):
    seed_fill(r.ledger)
    r.client.settlement_rows.append(ex_settlement(result="yes", revenue=100))
    r.client.exchange_orders.append(ex_order(r.ledger.orders_for(TICKER)[0]["client_order_id"], "ord-1"))
    r.client.exchange_fills.append(ex_fill("fill-1", "ord-1"))
    rep = r.reconcile([TICKER])
    assert rep.ok and any("settled on the exchange" in w for w in rep.warnings) and not rep.halted


def test_a_vanished_position_with_no_settlement_is_a_mismatch(r):
    seed_fill(r.ledger)
    rep = r.reconcile([TICKER])
    assert not rep.ok and any("position on" in m for m in rep.mismatches)


# ============================================================================================ reconcile: failures
def test_a_401_while_reconciling_halts(r):
    coid = r.unknown_order()
    r.on_exchange(coid)
    r.client.fail["orders"] = AuthError("GET /portfolio/orders: HTTP 401", status=401)
    rep = r.reconcile([TICKER])
    assert not rep.ok and rep.incomplete == [TICKER] and rep.halted and r.safety.halted
    assert "authentication" in r.safety.halt_reason


def test_an_unreadable_ticker_is_incomplete_not_a_mismatch(r):
    coid = r.unknown_order()
    r.on_exchange(coid)
    r.client.fail["fills"] = ScriptedFailure("503")
    rep = r.reconcile([TICKER])
    assert not rep.ok and rep.incomplete == [TICKER] and rep.mismatches == [] and not rep.halted
    assert any("ScriptedFailure" in w for w in rep.warnings)


def test_a_401_while_settling_halts_instead_of_asking_again(r):
    """Review finding: reconcile() halted on a 401 but settle() filed it under 'incomplete', so a revoked key left the bot
    asking for settlements for ever."""
    seed_fill(r.ledger)
    r.client.fail["settlements"] = AuthError("GET /portfolio/settlements: HTTP 401", status=401)
    rep = r.settle([TICKER])
    assert rep.incomplete == [TICKER] and rep.halted and not rep.ok
    assert r.safety.halted and "authentication refused while settling" in r.safety.halt_reason and r.kill.exists()
    assert any("authentication refused" in w for w in rep.warnings)


def test_a_401_on_the_fills_read_of_a_settlement_halts_too(r):
    seed_fill(r.ledger)
    r.client.settlement_rows.append(ex_settlement(result="yes", revenue=100))
    r.client.fail["fills"] = AuthError("GET /portfolio/fills: HTTP 403", status=403)
    rep = r.settle([TICKER])
    assert rep.halted and r.safety.halted and rep.recorded == []


def test_a_401_while_settling_does_not_trip_when_halting_is_switched_off(r):
    """The startup gate and `kpm exec check` call settle(halt=False): a check command must not trip anything."""
    seed_fill(r.ledger)
    r.client.fail["settlements"] = AuthError("GET /portfolio/settlements: HTTP 401", status=401)
    rep = r.settle([TICKER], halt=False)
    assert rep.incomplete == [TICKER] and not rep.halted and not r.safety.halted and not r.kill.exists()


def test_a_401_does_not_stop_settle_from_being_asked_about_other_tickers_first(r):
    seed_fill(r.ledger, ticker="KXBTC15M-OLD-1", order_id="o1", fill_id="f1")
    seed_fill(r.ledger, ticker="KXBTC15M-OLD-2", order_id="o2", fill_id="f2")
    r.client.fail["settlements"] = AuthError("GET /portfolio/settlements: HTTP 401", status=401)
    rep = r.settle(["KXBTC15M-OLD-1", "KXBTC15M-OLD-2"])
    assert len(r.client.calls_to("settlements")) == 1                                  # it stopped at the first 401


def test_a_failing_cash_read_is_a_warning_only(r):
    coid = r.unknown_order()
    r.on_exchange(coid)
    r.client.fail["balance"] = ScriptedFailure("boom")
    rep = r.reconcile([TICKER])
    assert rep.ok and rep.cash == {} and any("cash on shard 2" in w for w in rep.warnings)


def test_cash_recording_can_be_switched_off(r):
    coid = r.unknown_order()
    r.on_exchange(coid)
    r.reconcile([TICKER], record_cash=False)
    assert r.client.calls_to("balance") == []


def test_the_report_goes_to_the_audit_log_as_json(r):
    coid = r.unknown_order()
    r.on_exchange(coid)
    rep = r.reconcile([TICKER])
    json.dumps(rep.as_dict())
    (row,) = r.ledger._rows("SELECT component, entity_id, details FROM audit_log WHERE action = 'reconcile'")
    assert row["component"] == "exec.reconcile" and row["entity_id"] == "prod"
    assert json.loads(row["details"])["adopted"] == [coid] and json.loads(row["details"])["ok"] is True


# ============================================================================================ stale settlements
OLD = "KXBTC15M-26OCT051400-00"


def stale_position(r, *, age=1500.0):
    """A filled position on an earlier market whose settlement the bot never got to record."""
    seed_fill(r.ledger, ticker=OLD, order_id="o-old", fill_id="f-old")
    r.clock.advance(age)
    r.client.exchange_fills.append(ex_fill("f-old", "o-old", OLD))


def test_a_reconcile_also_records_the_settlement_of_an_old_ticker_the_bot_never_settled(r):
    """The bots call settle() once; if Kalshi had not published the row yet, the position would count toward the
    $3.00 exposure cap forever and eventually block every entry."""
    stale_position(r)
    assert r.ledger.open_exposure() == D("0.59")
    r.client.settlement_rows.append(ex_settlement(OLD, result="yes", revenue=100, fee=str(FEE)))
    coid = r.unknown_order()
    r.on_exchange(coid)
    rep = r.reconcile([TICKER])
    assert rep.ok and rep.settled == [OLD] and r.ledger.settled_tickers() == {OLD}
    assert r.ledger.open_exposure() == D("0.59")          # only the new position is open now ...
    assert r.ledger.realized_pnl() == D("0.393067")        # ... and the old one is realized, not carried
    assert r.client.calls_to("settlements") == [{"ticker": OLD}]


def test_a_ticker_traded_recently_is_not_swept(r):
    stale_position(r, age=600.0)                            # 10 minutes: its market may still be open
    r.client.settlement_rows.append(ex_settlement(OLD, result="yes", revenue=100, fee=str(FEE)))
    coid = r.unknown_order()
    r.on_exchange(coid)
    rep = r.reconcile([TICKER])
    assert rep.settled == [] and r.client.calls_to("settlements") == [] and r.ledger.settled_tickers() == set()


def test_a_settlement_that_is_still_missing_is_retried_at_most_every_two_minutes(r):
    stale_position(r)
    coid = r.unknown_order()
    r.on_exchange(coid)
    r.reconcile([TICKER])
    r.reconcile([TICKER])
    assert len(r.client.calls_to("settlements")) == 1       # throttled
    r.clock.advance(121)
    r.reconcile([TICKER])
    assert len(r.client.calls_to("settlements")) == 2
    r.client.settlement_rows.append(ex_settlement(OLD, result="yes", revenue=100, fee=str(FEE)))
    r.clock.advance(121)
    assert r.reconcile([TICKER]).settled == [OLD]


def test_the_sweep_can_be_switched_off_and_never_touches_the_tickers_being_reconciled(r):
    stale_position(r)
    r.client.settlement_rows.append(ex_settlement(OLD, result="yes", revenue=100, fee=str(FEE)))
    coid = r.unknown_order()
    r.on_exchange(coid)
    assert r.reconcile([TICKER], settle_stale=False).settled == [] and r.ledger.settled_tickers() == set()
    r.client.exchange_orders.append(ex_order("c-old", "o-old", OLD))
    assert r.reconcile([OLD, TICKER]).settled == []         # OLD is being reconciled itself: settle() is the caller's job


def test_a_mismatch_found_while_sweeping_halts_once(r):
    stale_position(r)
    r.client.settlement_rows.append(ex_settlement(OLD, result="yes", revenue=0, fee=str(FEE)))   # we hold YES: wrong
    coid = r.unknown_order()
    r.on_exchange(coid)
    rep = r.reconcile([TICKER])
    assert not rep.ok and rep.halted and r.safety.halted and any("mismatch: revenue 0" in m for m in rep.mismatches)
    assert len(r.ledger._rows("SELECT * FROM audit_log WHERE action = 'halt'")) == 1


# ============================================================================================ settle
def test_settlement_is_recorded_in_cents_with_the_fee_counted_once(r):
    seed_fill(r.ledger)
    r.client.settlement_rows.append(ex_settlement(result="yes", revenue=100, fee=str(FEE)))
    rep = r.settle([TICKER])
    (rec,) = rep.recorded
    assert rep.ok and rec["revenue_cents"] == 100 and rec["revenue"] == "1.00" and rec["check"] == "ok"
    assert rec["pnl"] == D("1") - D("0.59") - FEE == D("0.393067")
    assert r.ledger.settled_tickers() == {TICKER} and r.ledger.open_worst_case() == 0
    assert r.ledger.realized_pnl() == D("0.393067") and rep.warnings == []
    json.dumps(rep.as_dict())


def test_a_losing_settlement_counts_toward_the_realized_loss(r):
    seed_fill(r.ledger)
    r.client.settlement_rows.append(ex_settlement(result="no", revenue=0, fee=str(FEE)))
    r.settle([TICKER])
    assert r.ledger.realized_loss_total() == D("0.59") + FEE == r.ledger.realized_loss_24h()


def test_settling_twice_changes_nothing(r):
    seed_fill(r.ledger)
    r.client.settlement_rows.append(ex_settlement(result="yes", revenue=100, fee=str(FEE)))
    r.settle([TICKER])
    r.settle([TICKER])
    assert len(r.ledger._rows("SELECT * FROM exec_settlements")) == 1
    assert r.ledger.realized_pnl() == D("0.393067")


def test_a_settlement_that_is_not_published_yet_is_pending(r):
    seed_fill(r.ledger)
    rep = r.settle([TICKER])
    assert rep.pending == [TICKER] and rep.recorded == [] and rep.ok
    assert r.ledger.settled_tickers() == set() and r.client.calls_to("fills") == []


def test_only_tickers_this_ledger_traded_are_recorded(r):
    r.client.settlement_rows.append(ex_settlement("KXBTC15M-NOT-OURS", result="yes", revenue=100))
    rep = r.settle(["KXBTC15M-NOT-OURS"])
    assert rep.skipped == ["KXBTC15M-NOT-OURS"] and r.client.calls_to("settlements") == []
    assert r.ledger.settled_tickers() == set()


def test_the_default_is_every_unsettled_ticker(r):
    seed_fill(r.ledger)
    r.client.settlement_rows.append(ex_settlement(result="yes", revenue=100, fee=str(FEE)))
    assert r.settle().recorded and r.ledger.unsettled_tickers() == []


def test_a_settlement_fee_that_differs_from_the_fill_fees_is_noted_and_not_added(r):
    seed_fill(r.ledger)
    r.client.settlement_rows.append(ex_settlement(result="yes", revenue=100, fee="0.050000"))
    rep = r.settle([TICKER])
    assert any("fee_cost" in w for w in rep.warnings) and rep.recorded[0]["pnl"] == D("0.393067")


def test_fills_are_synced_before_the_pnl_so_it_comes_from_the_fills(r):
    coid = r.unknown_order()
    r.on_exchange(coid)
    r.reconcile([TICKER], record_cash=False)
    r.ledger._write(lambda: r.ledger.conn.execute("DELETE FROM exec_fills"))      # (pretend they were never fetched)
    r.client.settlement_rows.append(ex_settlement(result="yes", revenue=100, fee=str(FEE)))
    rep = r.settle([TICKER])
    assert rep.recorded[0]["basis"] == "fills" and len(r.ledger.fills_for(TICKER)) == 1


def test_a_manual_trade_on_the_same_ticker_explains_a_larger_revenue(r):
    seed_fill(r.ledger, order_id="ord-1", fill_id="fill-1")
    r.client.exchange_fills += [ex_fill("fill-1", "ord-1"), ex_fill("f-manual", "m-1", price="0.55", count="2.00")]
    r.client.settlement_rows.append(ex_settlement(result="yes", yes_count="3.00", yes_cost="1.6900", revenue=300,
                                                  fee=str(FEE)))
    rep = r.settle([TICKER])
    assert rep.ok and rep.mismatches == [] and not rep.halted
    assert any("manual trade" in w for w in rep.warnings)
    assert rep.recorded[0]["pnl"] == D("0.393067")                                # the bot's own fills only
    check = r.ledger._rows("SELECT check_status FROM exec_settlements")[0]["check_status"]
    assert check.startswith("ok: foreign fills")


def test_a_real_revenue_mismatch_halts(r):
    seed_fill(r.ledger)
    r.client.settlement_rows.append(ex_settlement(result="yes", revenue=0, fee=str(FEE)))
    rep = r.settle([TICKER])
    assert not rep.ok and rep.halted and r.safety.halted
    assert rep.mismatches and "mismatch: revenue 0 cents" in rep.mismatches[0]
    quiet = Rec(r.kill.parent / "q")
    seed_fill(quiet.ledger)
    quiet.client.settlement_rows.append(ex_settlement(result="yes", revenue=0, fee=str(FEE)))
    assert not quiet.settle([TICKER], halt=False).halted and not quiet.safety.halted


def test_an_unreadable_settlement_is_incomplete(r):
    seed_fill(r.ledger)
    r.client.fail["settlements"] = ScriptedFailure("503")
    rep = r.settle([TICKER])
    assert rep.incomplete == [TICKER] and not rep.ok and r.ledger.settled_tickers() == set()


def test_reconcile_all_reconciles_then_settles_everything_open(r):
    coid = r.unknown_order()
    r.on_exchange(coid)
    r.client.settlement_rows.append(ex_settlement(result="yes", revenue=100, fee=str(FEE)))
    out = reconcile_all(r.client, r.ledger, sleep=r.clock.sleep)
    assert out["ok"] and out["reconcile"]["adopted"] == [coid] and out["settle"]["recorded"]
    assert out["ledger"]["open_worst_case"] == "0" and r.ledger.settled_tickers() == {TICKER}
    json.dumps(out)


# ============================================================================================ cancel path
def test_a_targeted_cancel_carries_the_orders_own_exchange_index_then_verifies(r):
    coid = r.resting_order()
    rep = r.cancel("kill file", [TICKER])
    assert rep.ok and rep.verified and rep.cancelled == ["ex-r"] and not rep.cancel_all_used
    assert r.client.cancelled == [("ex-r", SHARD)]                         # shard 2, never the default shard 0
    assert r.client.cancel_all_calls == 0 and r.ledger.lockout_until() == 0 and rep.lockout_until is None
    assert {"ticker": TICKER, "status": "resting"} in r.client.calls_to("orders")
    assert r.ledger.order(coid)["status"] == "canceled"                    # the ledger follows the exchange
    json.dumps(rep.as_dict())


def test_the_exchanges_shard_beats_a_wrong_shard_in_our_row(r):
    r.resting_order(ledger_ei=0, exchange_ei=SHARD)
    rep = r.cancel("x", [TICKER])
    assert r.client.cancelled == [("ex-r", SHARD)] and rep.ok


def test_a_misrouted_cancel_would_fail_so_a_shard_we_cannot_learn_falls_back_to_cancel_all(r):
    coid = r.resting_order()
    r.ledger._update_order(coid, {"exchange_index": None})
    r.client.exchange_orders[0].pop("exchange_index")
    rep = r.cancel("x", [TICKER])
    assert rep.failed and rep.failed[0]["outcome"] == "no_exchange_index" and r.client.cancelled == []
    assert rep.cancel_all_used and r.client.cancel_all_calls == 1 and rep.ok


def test_what_still_rests_after_the_targeted_cancels_triggers_cancel_all_and_a_60_second_lockout(r):
    r.resting_order()
    r.client.cancel_removes = False                  # the cancel says ok, but the order keeps resting
    t0 = r.clock.now
    rep = r.cancel("loss cap", [TICKER])
    assert rep.cancel_all_used and r.client.cancel_all_calls == 1 and rep.cancel_all_outcome == "ok"
    assert rep.leftover == ["ex-r"] and rep.remaining == [] and rep.ok
    assert r.clock.sleeps == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0, 8.0]           # bounded backoff for 30 s, then the fallback
    assert rep.lockout_until == t0 + 31.5 + 60 and r.ledger.lockout_until() == rep.lockout_until
    r.safety.startup(live=True, env="prod", confirm=PHRASE)
    ok, why = r.safety.check_entry(entry_spec(), make_book(TICKER, fetched_at=r.clock.now - 0.3), base_market())
    assert ok is False and "lockout" in why                                # new orders are blocked for 60 s


def test_a_lockout_survives_a_restart_of_the_process(r, tmp_path):
    r.resting_order()
    r.client.cancel_removes = False
    rep = r.cancel("x", [TICKER])
    again = make_ledger(tmp_path, r.clock)                                 # a fresh Ledger on the same database file
    assert again.lockout_until() == rep.lockout_until


def test_if_cancel_all_cannot_clear_it_either_the_session_halts(r):
    r.resting_order()
    r.client.cancel_removes = False
    r.client.cancel_all_removes = False
    rep = r.cancel("x", [TICKER])
    assert not rep.ok and rep.remaining == ["ex-r"] and rep.cancel_all_used
    assert r.safety.halted and "still be resting after cancel_all" in r.safety.halt_reason
    assert r.clock.sleeps[:7] == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0, 8.0] and sum(r.clock.sleeps) < 60


def test_cancel_all_is_not_used_when_the_targeted_cancels_work(r):
    r.resting_order(order_id="a")
    r.resting_order(order_id="b")
    rep = r.cancel("normal exit", [TICKER])
    assert sorted(rep.cancelled) == ["a", "b"] and r.client.cancel_all_calls == 0 and rep.ok


def test_an_order_that_is_already_gone_is_not_an_emergency(r):
    r.resting_order()
    r.client.cancel_mode = "gone"                    # 404: it filled or expired a moment ago
    rep = r.cancel("x", [TICKER])
    assert rep.ok and not rep.cancel_all_used and rep.failed[0]["outcome"] == "rejected" and rep.failed[0]["status"] == 404


def test_a_cancel_response_for_another_order_is_not_trusted(r):
    r.resting_order()
    r.client.cancel_mode = "other"                   # the 2026-05-21 issue: the reply describes a different order
    rep = r.cancel("x", [TICKER])
    assert rep.failed[0]["outcome"] == "unknown" and rep.cancel_all_used and rep.ok


def test_a_manual_order_resting_on_a_touched_ticker_is_cancelled_too(r):
    r.client.exchange_orders.append(ex_order("manual-1", "m-1", status="resting", fill="0.00", remaining="1.00"))
    seed_fill(r.ledger)                                                      # the ticker was touched by this ledger
    rep = r.cancel("x", [TICKER])
    assert [(t["order_id"], t["source"]) for t in rep.targets] == [("m-1", "exchange")]
    assert r.client.cancelled == [("m-1", SHARD)] and rep.ok


def test_a_normal_exit_with_only_ioc_orders_cancels_nothing_and_never_cancels_all(r):
    seed_fill(r.ledger)
    rep = r.cancel("finished", [TICKER])
    assert rep.ok and rep.targets == [] and r.client.cancel_all_calls == 0 and r.client.cancelled == []


def test_unreadable_orders_without_anything_that_could_rest_do_not_trigger_cancel_all(r):
    seed_fill(r.ledger)                                                      # an executed IOC: it cannot be resting
    r.client.fail["orders"] = ScriptedFailure("network down")
    rep = r.cancel("finished", [TICKER])
    assert not rep.ok and not rep.verified and not rep.cancel_all_used and r.client.cancel_all_calls == 0
    assert any("could not be read" in e for e in rep.errors)


def test_unreadable_orders_while_a_gtc_order_may_rest_trigger_cancel_all(r):
    r.resting_order()
    r.client.fail["orders"] = ScriptedFailure("network down")
    rep = r.cancel("kill", [TICKER])
    assert r.client.cancelled == [("ex-r", SHARD)]                          # the ledger's order is still cancelled by id
    assert rep.cancel_all_used and r.client.cancel_all_calls == 1 and not rep.ok and r.safety.halted


def test_a_gtc_order_without_an_id_that_is_not_on_the_exchange_is_not_an_emergency(r):
    spec = entry_spec()
    spec["time_in_force"] = "good_till_canceled"
    coid = r.ledger.new_intent(spec)
    r.ledger.mark_result(coid, err_result("unknown", None, "transport_error", coid))
    rep = r.cancel("x", [TICKER])
    assert rep.unlocated == [coid] and rep.ok and not rep.cancel_all_used


def test_an_unresolved_gtc_order_found_resting_is_adopted_and_cancelled(r):
    spec = entry_spec()
    spec["time_in_force"] = "good_till_canceled"
    coid = r.ledger.new_intent(spec)
    r.ledger.mark_result(coid, err_result("unknown", None, "transport_error", coid))
    r.client.exchange_orders.append(ex_order(coid, "ex-9", status="resting", fill="0.00", remaining="1.00"))
    rep = r.cancel("x", [TICKER])
    assert rep.unlocated == [] and r.client.cancelled == [("ex-9", SHARD)] and rep.ok
    assert r.ledger.order(coid)["order_id"] == "ex-9" and r.ledger.order(coid)["status"] == "canceled"


def test_a_client_that_is_not_live_cannot_cancel_and_says_so(tmp_path):
    r = Rec(tmp_path, live=False)
    r.resting_order()
    rep = r.cancel("x", [TICKER])
    assert rep.not_live and not rep.ok and r.client.calls == []


def test_a_cancel_refused_for_lack_of_write_permission_is_reported_not_raised(r):
    r.resting_order()
    r.client.cancel_mode = "permission"
    rep = r.cancel("x", [TICKER])
    assert rep.not_live and not rep.ok and r.client.cancel_all_calls == 0


def test_a_401_on_cancel_stops_and_does_not_try_cancel_all(r):
    r.resting_order(order_id="a")
    r.resting_order(order_id="b")
    r.client.cancel_mode = "auth"
    rep = r.cancel("x", [TICKER])
    assert rep.auth_failed and not rep.ok and r.client.cancel_all_calls == 0
    assert len(r.client.calls_to("cancel_order")) == 1                      # no hammering after a 401


def test_the_cancel_path_with_nothing_to_do_still_verifies(r):
    rep = r.cancel("finished", [TICKER])
    assert rep.ok and rep.verified and rep.cancelled == [] and r.client.cancel_all_calls == 0


def test_tickers_come_from_the_caller_and_from_the_ledger(r):
    other = "KXBTC15M-26OCT051445-45"
    r.resting_order(ticker=other)
    seed_fill(r.ledger, ticker=TICKER)
    rep = r.cancel("x", None)
    assert rep.tickers == sorted([TICKER, other]) and r.client.cancelled == [("ex-r", SHARD)]


# ============================================================================================ exit guard
def test_the_guard_runs_the_cancel_path_on_normal_exit(r):
    r.resting_order()
    with ExitGuard(r.client, r.ledger, touched=[TICKER], sleep=r.clock.sleep) as g:
        assert g.report is None
    assert g.report.reason == "finished" and r.client.cancelled == [("ex-r", SHARD)]


def test_the_guard_runs_on_an_exception_and_does_not_swallow_it(r):
    r.resting_order()
    with pytest.raises(ValueError, match="boom"):
        with ExitGuard(r.client, r.ledger, touched=[TICKER], sleep=r.clock.sleep) as g:
            raise ValueError("boom")
    assert g.report.reason == "error: ValueError" and r.client.cancelled == [("ex-r", SHARD)]


def test_the_guard_runs_on_ctrl_c(r):
    r.resting_order()
    with pytest.raises(KeyboardInterrupt):
        with ExitGuard(r.client, r.ledger, touched=[TICKER], sleep=r.clock.sleep) as g:
            raise KeyboardInterrupt
    assert g.report.reason == "interrupted (Ctrl+C)" and r.client.cancelled == [("ex-r", SHARD)]


def test_the_guard_runs_once_and_reads_the_tickers_when_it_fires(r):
    touched = set()
    g = ExitGuard(r.client, r.ledger, touched=lambda: touched, sleep=r.clock.sleep)
    touched.add(TICKER)                                                    # the bot touched it after the guard was built
    r.resting_order()
    first = g.run("a")
    assert g.run("b") is first and first.reason == "a" and r.client.cancelled == [("ex-r", SHARD)]


def test_a_failing_cancel_path_never_masks_the_original_error(r, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("cancel path exploded")

    monkeypatch.setattr(rec_mod, "cancel_path", boom)
    with pytest.raises(ValueError, match="original"):
        with ExitGuard(r.client, r.ledger, touched=[TICKER]):
            raise ValueError("original")


def test_the_guard_can_register_for_interpreter_exit(r, monkeypatch):
    seen = []
    monkeypatch.setattr(atexit, "register", lambda fn, *a: seen.append((fn, a)))
    monkeypatch.setattr(atexit, "unregister", lambda fn: seen.append(("unregistered", fn)))
    r.resting_order()
    g = ExitGuard(r.client, r.ledger, touched=[TICKER], sleep=r.clock.sleep)
    g.register_atexit()
    (fn, args), = seen
    fn(*args)                                                              # what the interpreter does at exit
    assert g.report.reason == "interpreter exit" and r.client.cancelled == [("ex-r", SHARD)]
    g.unregister_atexit()
    assert seen[-1][0] == "unregistered"


def test_signal_handlers_turn_ctrl_break_into_keyboard_interrupt_and_are_restored(r):
    if threading.current_thread() is not threading.main_thread():
        pytest.skip("signal handlers can only be installed from the main thread")
    sig = getattr(signal, "SIGBREAK", signal.SIGTERM)
    before = signal.getsignal(sig)
    g = ExitGuard(r.client, r.ledger, touched=[TICKER])
    installed = g.install_signal_handlers()
    try:
        assert int(sig) in installed
        with pytest.raises(KeyboardInterrupt):
            signal.getsignal(sig)(int(sig), None)
    finally:
        g.restore_signal_handlers()
    assert signal.getsignal(sig) == before


# ============================================================================================ nothing here ever sends an order
def test_the_fake_exchange_really_refuses_order_writes(r):
    for name in ("create_order", "batch_create", "amend_order", "decrease_order"):
        with pytest.raises(AssertionError, match="never create or amend orders"):
            getattr(r.client, name)({})


def test_neither_reconcile_nor_the_cancel_path_ever_calls_an_order_write(r):
    coid = r.unknown_order(age=20)
    r.on_exchange(coid)
    r.resting_order()
    r.client.cancel_removes = False
    r.reconcile([TICKER])
    r.settle([TICKER])
    r.cancel("x", [TICKER])
    names = {n for n, _ in r.client.calls}
    assert names.isdisjoint({"create_order", "batch_create", "amend_order", "decrease_order"})
