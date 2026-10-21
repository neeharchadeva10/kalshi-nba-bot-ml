"""Safety, reconcile and the cancel path driven through the REAL ExecClient (scripted transport, no network).

The other test files use a fake exchange object, which accepts any arguments. Here every read and write goes through
ExecClient's own validation (allowlist, parameter whitelist, signing, response parsing) so a wrong parameter name or a
wrong return shape in kpm.execution.safety / reconcile fails loudly. The Ledger is the audit sink, as in production.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal as D

import pytest

from kpm.core import db
from kpm.execution import units
from kpm.execution.ledger import Ledger
from kpm.execution.reconcile import cancel_path, reconcile, settle
from kpm.execution.safety import Safety

from .conftest import TEST_KEY_ID, FakeTransport, fixture_json
from .exec_fakes import (SHARD, TICKER, base_market, entry_spec, err_result, ex_fill, ex_order, ex_settlement, iso,
                         ok_result)

PHRASE = "LIVE PROD 5"
BALANCE = {"balance": 2943, "balance_dollars": "29.4300", "portfolio_value": 2943,
           "balance_breakdown": [{"exchange_index": 0, "balance": "0.0000"}, {"exchange_index": 2, "balance": "29.4300"}]}


class Stack:
    """A Ledger, a live ExecClient on a scripted transport, and a Safety, all on one fake clock."""

    def __init__(self, tmp_path, make_client, prod_env, fake_clock, *, close_in=90.0):
        self.clock = fake_clock
        self.kill = tmp_path / "KILL"
        conn = db.connect(tmp_path / "stack.sqlite")
        db.init_schema(conn)
        self.ledger = Ledger(conn, "run-int", "prod", clock=fake_clock.time, log_path=tmp_path / "exec.jsonl",
                             redact=[TEST_KEY_ID])
        self.t = FakeTransport(host=prod_env.rest_host, server_clock=fake_clock.time)    # replies carry a Date header
        self.client = make_client(self.t, prod_env, live=True, audit=self.ledger.audit_request)
        self.safety = Safety({"enabled": True, "kill_switch_file": str(self.kill)}, self.ledger, self.client,
                             fake_clock.time, sleep=fake_clock.sleep, isatty=lambda: True)
        close = datetime.fromtimestamp(fake_clock.now + close_in, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.market = base_market(close_time=close)
        t = self.t
        t.get("/exchange/status", fixture="exchange_status_prod.json")
        t.get("/exchange/user_data_timestamp", lambda call: {"as_of_time": iso(fake_clock.now)})
        t.get("/series/KXBTC15M", fixture="series_KXBTC15M.json")
        t.get("/series/KXBTCD", {"series": {"ticker": "KXBTCD", "exchange_index": 2}})
        # GetMarkets with EXACTLY the documented parameters (series_ticker, status=open, limit): a request that adds the
        # close-time filters (OpenAPI: only with status closed or no status) matches no route and fails the test
        t.get("/markets", {"markets": [self.market], "cursor": ""},
              params={"series_ticker": "KXBTC15M", "status": "open", "limit": 1000})
        t.get("/markets", {"markets": [], "cursor": ""},
              params={"series_ticker": "KXBTCD", "status": "open", "limit": 1000})
        t.get(f"/markets/{TICKER}", {"market": self.market})
        t.get(f"/markets/{TICKER}/orderbook", fixture="orderbook_KXBTC15M-26OCT051430-30_depth3.json")
        t.get("/portfolio/balance", BALANCE)
        t.get("/portfolio/positions", {"market_positions": [], "event_positions": [], "cursor": ""})
        t.get("/portfolio/orders", {"orders": [], "cursor": ""})
        t.get("/portfolio/fills", {"fills": [], "cursor": ""})
        t.get("/portfolio/settlements", {"settlements": [], "cursor": ""})

    def calls(self, endpoint, method="GET"):
        return self.t.calls_to(method, endpoint)


@pytest.fixture
def stack(tmp_path, make_client, prod_env, fake_clock):
    return Stack(tmp_path, make_client, prod_env, fake_clock)


def dump_everything(ledger, tmp_path) -> str:
    parts = []
    for (name,) in ledger.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        for row in ledger.conn.execute(f"SELECT * FROM {name}"):
            parts.append(" ".join(str(x) for x in tuple(row)))
    log = tmp_path / "exec.jsonl"
    return "\n".join(parts) + (log.read_text(encoding="utf-8") if log.exists() else "")


# ============================================================================================ safety
def test_a_live_start_and_an_entry_check_work_through_the_real_client(stack):
    rep = stack.safety.startup(live=True, env="prod", confirm=PHRASE)             # real clock probe + authenticated probe
    assert rep["ok"] and all(g["ok"] for g in rep["gates"])
    book = stack.client.orderbook(TICKER)                                         # a captured book, parsed by the client
    market = stack.client.market(TICKER)
    assert book["yes"][-1][0] == D("0.58") and book["no"][-1][0] == D("0.41")     # best bid last
    ok, why = stack.safety.check_entry(entry_spec(market, "yes", "0.59"), book, market)
    assert (ok, why) == (True, "")
    ok, why = stack.safety.check_entry(entry_spec(market, "no", "0.42"), book, market)
    assert (ok, why) == (True, "")
    ok, why = stack.safety.check_entry(entry_spec(market, "yes", "0.60"), book, market)
    assert ok is False and "not the current YES ask 0.59" in why

    assert stack.t.writes == []
    listing = stack.calls("/markets")[0]
    # exactly the documented parameters: Kalshi's OpenAPI allows the close-time filters only with status closed / no status
    assert listing.params == {"series_ticker": "KXBTC15M", "status": "open", "limit": 1000} and not listing.signed
    balance = stack.calls("/portfolio/balance")
    assert [c.params for c in balance] == [None, {"exchange_index": SHARD}]        # the probe, then the market's own shard
    assert all(c.signed for c in balance)
    assert [c.params for c in stack.calls("/portfolio/positions")] == [{"ticker": TICKER}]
    assert [c.params for c in stack.calls("/portfolio/orders")] == [{"ticker": TICKER, "status": "resting"}]
    assert len(stack.calls("/exchange/status")) >= 4                              # 3 clock probes + the shard status


def test_a_clock_that_is_off_blocks_the_start_through_the_real_probe(tmp_path, make_client, prod_env, fake_clock):
    stack = Stack(tmp_path, make_client, prod_env, fake_clock)
    stack.t.server_clock = lambda: fake_clock.now - 30                            # the PC is 30 s ahead of Kalshi
    with pytest.raises(PermissionError, match="clock_skew"):
        stack.safety.startup(live=True, env="prod", confirm=PHRASE)
    assert not stack.safety.started and stack.t.writes == []


def test_a_401_from_the_balance_probe_blocks_the_start(stack):
    stack.t.get("/portfolio/balance", 401)
    with pytest.raises(PermissionError, match="auth_probe"):
        stack.safety.startup(live=True, env="prod", confirm=PHRASE)


def test_diagnostics_through_the_real_client(stack):
    d = stack.safety.diagnostics("prod")
    assert d["ok"] is True and {c["name"] for c in d["checks"]} >= {"exchange_status", "clock_skew", "auth_probe"}
    assert stack.t.writes == []


def test_a_shard_that_is_not_trading_blocks_an_entry_through_the_real_status_read(stack):
    stack.safety.startup(live=True, env="prod", confirm=PHRASE)
    status = fixture_json("exchange_status_prod.json")
    status["exchange_index_statuses"][2]["trading_active"] = False
    stack.t.get("/exchange/status", status)
    stack.safety.invalidate()
    book, market = stack.client.orderbook(TICKER), stack.client.market(TICKER)
    ok, why = stack.safety.check_entry(entry_spec(market, "yes", "0.59"), book, market)
    assert ok is False and "shard 2" in why


# ============================================================================================ reconcile
def test_reconcile_adopts_a_timed_out_order_through_the_real_client(stack):
    spec = entry_spec(stack.market, "yes", "0.59")
    coid = stack.ledger.new_intent(spec)
    stack.ledger.mark_sent([coid])
    stack.ledger.mark_result(coid, err_result("unknown", None, "transport_error", coid))
    stack.t.get("/portfolio/orders", {"orders": [ex_order(coid, "ex-1")], "cursor": ""})
    stack.t.get("/portfolio/fills", {"fills": [ex_fill("f1", "ex-1")], "cursor": ""})
    stack.t.get("/portfolio/positions", {"market_positions": [{"ticker": TICKER, "exchange_index": 2,
                                                                "position_fp": "1.00"}], "cursor": ""})
    rep = reconcile(stack.client, stack.ledger, [TICKER], sleep=stack.clock.sleep)
    assert rep.ok and rep.adopted == [coid] and rep.fills_added == 1 and rep.cash == {"2": "29.4300"}
    assert stack.ledger.order(coid)["status"] == "executed" and stack.ledger.position(TICKER) == 1
    assert [c.params for c in stack.calls("/portfolio/orders")] == [{"ticker": TICKER}]
    assert [c.params for c in stack.calls("/portfolio/fills")] == [{"ticker": TICKER}]
    assert [c.params for c in stack.calls("/portfolio/positions")] == [{"ticker": TICKER}]
    assert [c.params for c in stack.calls("/portfolio/balance")] == [{"exchange_index": SHARD}]
    assert stack.t.writes == []                                                    # a lookup never resends


def test_a_missing_order_is_presumed_not_placed_and_halts_through_the_real_client(stack):
    coid = stack.ledger.new_intent(entry_spec(stack.market, "yes", "0.59"))
    stack.ledger.mark_sent([coid])
    stack.ledger.mark_result(coid, err_result("unknown", None, "transport_error", coid))
    stack.clock.advance(20)
    rep = reconcile(stack.client, stack.ledger, [TICKER], sleep=stack.clock.sleep)
    assert rep.presumed_not_placed == [coid] and rep.halted and stack.safety.halted and stack.kill.exists()
    assert stack.t.writes == []


def test_settle_through_the_real_client_records_integer_cents_and_the_fee_once(stack):
    spec = entry_spec(stack.market, "yes", "0.59")
    coid = stack.ledger.new_intent(spec)
    stack.ledger.mark_result(coid, ok_result(coid, "ex-1"))
    fee = str(units.taker_fee(D(1), D("0.59")))
    stack.t.get("/portfolio/fills", {"fills": [ex_fill("f1", "ex-1", fee=fee)], "cursor": ""})
    stack.t.get("/portfolio/settlements", {"settlements": [ex_settlement(TICKER, result="yes", revenue=100, fee=fee)],
                                           "cursor": ""})
    rep = settle(stack.client, stack.ledger, [TICKER])
    assert rep.ok and rep.recorded[0]["revenue_cents"] == 100 and rep.recorded[0]["pnl"] == D("0.393067")
    assert [c.params for c in stack.calls("/portfolio/settlements")] == [{"ticker": TICKER}]
    assert stack.ledger.realized_pnl() == D("0.393067") and stack.ledger.open_worst_case() == 0


# ============================================================================================ cancel path
def resting(stack, order_id="ex-r"):
    spec = entry_spec(stack.market, "yes", "0.40")
    spec["time_in_force"] = "good_till_canceled"
    coid = stack.ledger.new_intent(spec)
    stack.ledger.mark_result(coid, ok_result(coid, order_id, fill="0.00", remaining="1.00"))
    return coid, ex_order(coid, order_id, price="0.40", fill="0.00", remaining="1.00", status="resting")


def test_the_cancel_is_routed_by_the_orders_own_shard_and_verified_through_the_real_client(stack):
    coid, order = resting(stack)
    gone = {**order, "status": "canceled", "remaining_count_fp": "0.00"}
    stack.t.get("/portfolio/orders", {"orders": [order], "cursor": ""},       # 1: discover what rests
                {"orders": [], "cursor": ""},                                  # 2: verify (status=resting): nothing left
                {"orders": [gone], "cursor": ""})                              # 3: bring the ledger up to date
    stack.t.delete("/portfolio/events/orders/ex-r", {"order_id": "ex-r", "client_order_id": coid,
                                                      "reduced_by": "1.00", "ts_ms": 1791226200123})
    rep = cancel_path(stack.client, stack.ledger, "kill file", [TICKER], sleep=stack.clock.sleep)
    assert rep.ok and rep.cancelled == ["ex-r"] and not rep.cancel_all_used
    (delete,) = stack.t.writes
    assert (delete.method, delete.endpoint, delete.params) == ("DELETE", "/portfolio/events/orders/ex-r",
                                                               {"exchange_index": SHARD})
    assert delete.presign_text().endswith("DELETE/trade-api/v2/portfolio/events/orders/ex-r")   # no query is signed
    assert delete.signed and delete.body is None
    assert [c.params for c in stack.calls("/portfolio/orders")] == [{"ticker": TICKER, "status": "resting"}] * 2 + [
        {"ticker": TICKER}]
    assert stack.ledger.order(coid)["status"] == "canceled"


def test_cancel_all_is_the_last_resort_and_a_lockout_follows(stack):
    coid, order = resting(stack)
    stack.t.get("/portfolio/orders", *([{"orders": [order], "cursor": ""}] * 9), {"orders": [], "cursor": ""})
    stack.t.delete("/portfolio/events/orders/ex-r", {"order_id": "ex-r", "client_order_id": coid,
                                                      "reduced_by": "1.00", "ts_ms": 1})
    stack.t.delete("/portfolio/events/orders", 204)
    rep = cancel_path(stack.client, stack.ledger, "loss cap", [TICKER], sleep=stack.clock.sleep)
    assert rep.cancel_all_used and rep.cancel_all_outcome == "ok" and rep.ok
    assert [(c.method, c.endpoint) for c in stack.t.writes] == [("DELETE", "/portfolio/events/orders/ex-r"),
                                                                 ("DELETE", "/portfolio/events/orders")]
    assert stack.ledger.lockout_until() == rep.lockout_until and rep.lockout_until > stack.clock.now


def test_a_cancel_reply_that_names_another_order_is_not_trusted_by_the_real_client(stack):
    coid, order = resting(stack)
    stack.t.get("/portfolio/orders", *([{"orders": [order], "cursor": ""}] * 9), {"orders": [], "cursor": ""})
    stack.t.delete("/portfolio/events/orders/ex-r", {"order_id": "someone-else", "reduced_by": "5.00", "ts_ms": 1})
    stack.t.delete("/portfolio/events/orders", 204)
    rep = cancel_path(stack.client, stack.ledger, "x", [TICKER], sleep=stack.clock.sleep)
    assert rep.failed and rep.failed[0]["outcome"] == "unknown" and rep.cancel_all_used and rep.ok


# ============================================================================================ audit
def test_every_authenticated_request_is_audited_and_no_secret_is_stored(stack, tmp_path, prod_env):
    stack.safety.startup(live=True, env="prod", confirm=PHRASE)
    book, market = stack.client.orderbook(TICKER), stack.client.market(TICKER)
    stack.safety.check_entry(entry_spec(market, "yes", "0.59"), book, market)
    coid, order = resting(stack)
    stack.t.get("/portfolio/orders", {"orders": [order], "cursor": ""}, {"orders": [], "cursor": ""})
    stack.t.delete("/portfolio/events/orders/ex-r", {"order_id": "ex-r", "client_order_id": coid, "reduced_by": "1.00",
                                                      "ts_ms": 1})
    cancel_path(stack.client, stack.ledger, "x", [TICKER], sleep=stack.clock.sleep)

    rows = stack.ledger._rows("SELECT method, path, kind, live FROM exec_requests")
    assert {r["kind"] for r in rows} == {"read", "write"} and all(r["live"] == 1 for r in rows)   # public reads: not audited
    assert ("DELETE", "/trade-api/v2/portfolio/events/orders/ex-r", "write", 1) in {
        (r["method"], r["path"], r["kind"], r["live"]) for r in rows}
    secrets = [TEST_KEY_ID, "PRIVATE KEY"] + [c.headers["KALSHI-ACCESS-SIGNATURE"] for c in stack.t.calls if c.signed]
    dump = dump_everything(stack.ledger, tmp_path)
    for s in secrets:
        assert s not in dump
    assert "KALSHI-ACCESS" not in dump


def test_the_fake_transport_really_signed_the_requests(stack):
    stack.client.balance(SHARD)
    (call,) = stack.calls("/portfolio/balance")
    assert call.signed and call.headers["KALSHI-ACCESS-KEY"] == TEST_KEY_ID and call.presign_text().endswith(
        "GET/trade-api/v2/portfolio/balance")
