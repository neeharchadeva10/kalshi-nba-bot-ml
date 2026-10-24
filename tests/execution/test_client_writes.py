"""kpm.execution.client writes: wire format, outcome classes, 429 / never-resend rules, batches, cancel routing.

Design: section 4 (Writes, error parsing, outcome classes) and test plan cases 5, 8 and 9.
"""
from __future__ import annotations

import inspect
import json
from decimal import Decimal

import pytest
import requests

from kpm.execution import auth
from kpm.execution.client import MAX_BATCH, WRITE_429_BACKOFF_S, AuthError, EndpointNotAllowed, ExecClient, Result

from .conftest import (COID, TICKER, FakeTransport, order_body, respond, verify_signature)

D = Decimal
CREATE = "/portfolio/events/orders"
BATCH = "/portfolio/events/orders/batched"
OID = "3b23c1c7-f4ef-4f0d-8b9a-9e53c61f1a0d"                 # the order id of the synthetic cancel/amend/decrease fixtures
OID2 = "a6d6010d-6d5f-40a1-a7e7-5501386bb621"


@pytest.fixture
def live(make_client, prod_env):
    """A live client (Ed25519 key, keys loaded) on a FakeTransport pinned to the production REST host."""
    return make_client(FakeTransport(host="external-api.kalshi.com"), prod_env, live=True)


def created(**over):
    d = {"order_id": "7e1c2b3a-0000-4a5b-8c9d-00000000000a", "client_order_id": COID, "fill_count": "1.00",
         "remaining_count": "0.00", "average_fill_price": "0.5600", "average_fee_paid": "0.017248", "ts_ms": 1791226212345}
    d.update(over)
    return d


def echo(call):
    """A 201 that echoes the request's client_order_id, whatever it is."""
    return respond(201, json=created(client_order_id=call.body["client_order_id"]))


def missing(field):
    body = order_body()
    del body[field]
    return body


# ================================================================================================ wire format
BAD_BODIES = {
    # side is the YES book side: an outcome ("yes"/"no") or a legacy action is never accepted
    "side yes": order_body(side="yes"), "side no": order_body(side="no"), "side buy": order_body(side="buy"),
    "side upper": order_body(side="BID"), "side None": order_body(side=None),
    # prices are 4-dp YES dollar strings strictly inside (0, 1)
    "price cents": order_body(price="56"), "price 2 dp": order_body(price="0.56"), "price 1 dp": order_body(price="0.5"),
    "price 3 dp": order_body(price="0.560"), "price 5 dp": order_body(price="0.56000"),
    "price one": order_body(price="1.0000"), "price zero": order_body(price="0.0000"),
    "price negative": order_body(price="-0.5600"), "price float": order_body(price=0.56),
    "price Decimal": order_body(price=D("0.56")), "price int": order_body(price=56), "price None": order_body(price=None),
    "price exponent": order_body(price="5.6e-1"), "price unicode digits": order_body(price="0.٥٦٠٠"),
    "price padded": order_body(price=" 0.5600"), "price empty": order_body(price=""),
    # counts are 2-dp contract strings, > 0
    "count bare": order_body(count="25"), "count 1 dp": order_body(count="0.5"), "count 3 dp": order_body(count="0.001"),
    "count zero": order_body(count="0.00"), "count negative": order_body(count="-1.00"),
    "count float": order_body(count=1.0), "count int": order_body(count=1), "count Decimal": order_body(count=D("1.00")),
    "count unicode digits": order_body(count="١.٠٠"), "count None": order_body(count=None),
    # required fields
    **{f"missing {f}": missing(f) for f in ("ticker", "client_order_id", "side", "count", "price", "time_in_force",
                                            "self_trade_prevention_type", "exchange_index")},
    # legacy / unknown fields
    "legacy type": order_body(type="limit"), "legacy action": order_body(action="buy"),
    "legacy yes_price": order_body(yes_price=56), "legacy no_price": order_body(no_price=44),
    "legacy yes_price_dollars": order_body(yes_price_dollars="0.5600"), "legacy count_fp": order_body(count_fp="1.00"),
    "legacy buy_max_cost": order_body(buy_max_cost=100), "legacy sell_position_floor": order_body(sell_position_floor=0),
    "legacy expiration_ts": order_body(expiration_ts=1791226300), "misspelt field": order_body(post_onyl=True),
    "market_ticker": order_body(market_ticker=TICKER),
    # enums and flags
    "tif GTD": order_body(time_in_force="good_till_date"), "tif Day": order_body(time_in_force="Day"),
    "tif upper": order_body(time_in_force="IMMEDIATE_OR_CANCEL"), "tif ioc": order_body(time_in_force="ioc"),
    "stp bad": order_body(self_trade_prevention_type="cancel_both"), "stp None": order_body(self_trade_prevention_type=None),
    "post_only str": order_body(post_only="true"), "post_only int": order_body(post_only=1),
    "cancel_order_on_pause str": order_body(cancel_order_on_pause="yes"), "reduce_only int": order_body(reduce_only=1),
    # routing: the market's own shard, an explicit non-negative int (never auto-routing)
    "exchange_index -1": order_body(exchange_index=-1), "exchange_index True": order_body(exchange_index=True),
    "exchange_index str": order_body(exchange_index="2"), "exchange_index float": order_body(exchange_index=2.0),
    "exchange_index None": order_body(exchange_index=None),
    # expiry is Unix SECONDS and GTC only
    "expiry in ms": order_body(time_in_force="good_till_canceled", expiration_time=1_791_226_300_000),
    "expiry float": order_body(time_in_force="good_till_canceled", expiration_time=1_791_226_300.5),
    "expiry str": order_body(time_in_force="good_till_canceled", expiration_time="1791226300"),
    "expiry on IOC": order_body(expiration_time=1_791_226_300),
    "expiry on FOK": order_body(time_in_force="fill_or_kill", expiration_time=1_791_226_300),
    "reduce_only GTC": order_body(time_in_force="good_till_canceled", reduce_only=True),
    "reduce_only FOK": order_body(time_in_force="fill_or_kill", reduce_only=True),
    "subaccount 1": order_body(subaccount=1), "subaccount True": order_body(subaccount=True),
    "order_group_id bad": order_body(order_group_id="a b"),
    # identifiers
    "ticker with slash": order_body(ticker="A/B"), "ticker empty": order_body(ticker=""), "ticker None": order_body(ticker=None),
    "coid empty": order_body(client_order_id=""), "coid too long": order_body(client_order_id="x" * 65),
    "coid spaces": order_body(client_order_id="a b"), "coid None": order_body(client_order_id=None),
    "coid int": order_body(client_order_id=7),
}


@pytest.mark.parametrize("label", list(BAD_BODIES))
def test_a_malformed_order_body_is_refused_before_anything_is_sent(live, label):
    with pytest.raises((ValueError, TypeError)):
        live.create_order(BAD_BODIES[label])
    assert live.transport.calls == []
    with pytest.raises((ValueError, TypeError)):
        live.batch_create([BAD_BODIES[label]])
    assert live.transport.calls == []


def test_a_non_mapping_body_is_refused(live):
    for bad in (None, [], "x", 5, [("ticker", TICKER)]):
        with pytest.raises(TypeError):
            live.create_order(bad)                                              # type: ignore[arg-type]
    assert live.transport.calls == []


@pytest.mark.parametrize("over", [
    {"price": "0.0010"}, {"price": "0.9990"}, {"price": "0.0100"}, {"count": "0.01"}, {"count": "1000.00"},
    {"count": "25.00"}, {"exchange_index": 0}, {"exchange_index": 3}, {"time_in_force": "fill_or_kill"},
    {"post_only": True, "time_in_force": "good_till_canceled", "expiration_time": 1_791_226_380,
     "cancel_order_on_pause": True},
    {"reduce_only": True}, {"side": "ask", "price": "0.9900"}, {"order_group_id": "og_123"}, {"subaccount": 0},
    {"self_trade_prevention_type": "maker"}, {"client_order_id": "my-order.1:a_b"},
    {"ticker": "KXBTCD-26OCT0515-T85249.99"},
])
def test_well_formed_order_bodies_are_accepted(live, over):
    live.transport.post(CREATE, echo)
    body = order_body(**over)
    assert live.create_order(body).outcome == "ok"
    assert live.transport.writes[0].body == body


def test_the_body_is_sent_as_compact_json_and_the_callers_dict_is_untouched(live):
    live.transport.post(CREATE, status=201, json=created())
    body = order_body()
    snapshot = json.loads(json.dumps(body))
    live.create_order(body)
    (call,) = live.transport.writes
    assert call.data == json.dumps(body, separators=(",", ":")) and call.body == snapshot
    assert body == snapshot                                                      # not mutated
    assert call.method == "POST" and call.url == "https://external-api.kalshi.com/trade-api/v2/portfolio/events/orders"
    assert call.headers["Content-Type"] == "application/json" and call.headers["Accept"] == "application/json"
    assert call.params is None and call.timeout == live.timeout_s


def test_the_price_and_count_on_the_wire_are_the_exact_strings_that_were_validated(live):
    live.transport.post(CREATE, status=201, json=created())
    live.create_order(order_body(side="ask", price="0.9900", count="1.00"))     # buy NO @ 0.01 = ask @ 0.99
    body = live.transport.writes[0].body
    assert (body["side"], body["price"], body["count"]) == ("ask", "0.9900", "1.00")
    assert D(body["price"]) == D("0.99") and isinstance(body["price"], str) and isinstance(body["count"], str)


def test_the_signature_covers_the_method_and_path_but_not_the_body(live, prod_env):
    live.transport.post(CREATE, echo)
    live.create_order(order_body(price="0.5600"))
    live.create_order(order_body(price="0.5700", client_order_id="another-order-id"))
    a, b = live.transport.writes
    assert a.data != b.data
    assert a.presign_text()[13:] == b.presign_text()[13:] == "POST/trade-api/v2/portfolio/events/orders"
    verify_signature(a, prod_env.key)
    verify_signature(b, prod_env.key)


# ================================================================================================ create: outcomes
def test_an_ioc_fill_is_ok_with_the_documented_response_fields(live):
    live.transport.post(CREATE, status=201, fixture="synthetic/create_201_ioc_filled.json")
    res = live.create_order(order_body())
    assert isinstance(res, Result) and res.ok and res.outcome == "ok" and res.status == 201 and res.error is None
    assert res.order_id == "7e1c2b3a-0000-4a5b-8c9d-00000000000a" and res.client_order_id == COID
    assert (res.data["fill_count"], res.data["remaining_count"]) == ("1.00", "0.00")
    assert res.data["average_fill_price"] == "0.5600" and res.data["average_fee_paid"] == "0.017248"
    assert res.attempts == 1 and isinstance(res.latency_ms, float) and res.latency_ms >= 0
    assert len(live.transport.calls) == 1


@pytest.mark.parametrize("fixture, fill, remaining", [("synthetic/create_201_ioc_unfilled.json", "0.00", "0.00"),
                                                      ("synthetic/create_201_resting.json", "0.00", "10.00")])
def test_unfilled_and_resting_responses_are_ok(live, fixture, fill, remaining):
    live.transport.post(CREATE, status=201, fixture=fixture)
    res = live.create_order(order_body(client_order_id=json.loads(respond(fixture=fixture).text)["client_order_id"]))
    assert res.ok and (res.data["fill_count"], res.data["remaining_count"]) == (fill, remaining)


@pytest.mark.parametrize("status", [200, 201, 202])
def test_any_2xx_with_a_good_body_is_ok(live, status):
    live.transport.post(CREATE, status=status, json=created())
    assert live.create_order(order_body()).ok


def test_a_response_without_the_optional_fields_is_still_ok(live):
    live.transport.post(CREATE, status=201, json={"order_id": "x1", "fill_count": "0.00", "remaining_count": "1.00"})
    assert live.create_order(order_body()).ok


@pytest.mark.parametrize("fixture, shape, code", [
    ("synthetic/error_400_bad_request.json", "error_object", "bad_request"),
    ("synthetic/error_400_msg.json", "msg", None),
    ("synthetic/error_400_post_only_cross.json", "error_object", "invalid order"),
])
def test_a_400_is_rejected_and_is_not_resent(live, fake_clock, fixture, shape, code):
    live.transport.post(CREATE, status=400, fixture=fixture)
    res = live.create_order(order_body())
    assert res.outcome == "rejected" and res.status == 400 and not res.ok
    assert res.error["shape"] == shape and res.error["code"] == code and res.attempts == 1
    assert len(live.transport.calls) == 1 and fake_clock.sleeps == []


@pytest.mark.parametrize("status", [404, 405, 410, 422, 451])
def test_other_4xx_are_rejected_without_retry(live, status):
    live.transport.post(CREATE, status=status, json={"error": {"code": "x", "message": "m"}})
    res = live.create_order(order_body())
    assert res.outcome == "rejected" and res.status == status and len(live.transport.calls) == 1


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_halt_and_are_not_resent(live, fake_clock, status):
    live.transport.post(CREATE, status=status, fixture="synthetic/error_401.json")
    res = live.create_order(order_body())
    assert res.outcome == "auth" and res.status == status and res.error["code"] == "authentication_error"
    assert len(live.transport.calls) == 1 and fake_clock.sleeps == []


def test_a_409_is_a_duplicate_and_is_not_resent(live, fake_clock):
    live.transport.post(CREATE, status=409, fixture="synthetic/error_409_duplicate.json")
    res = live.create_order(order_body())
    assert res.outcome == "duplicate" and res.status == 409 and res.error["code"] == "order_already_exists"
    assert res.client_order_id == COID and len(live.transport.calls) == 1 and fake_clock.sleeps == []


@pytest.mark.parametrize("status", [500, 502, 503, 504, 520, 599])
def test_a_5xx_is_unknown_and_is_never_resent_even_if_the_next_reply_would_succeed(live, fake_clock, status):
    live.transport.post(CREATE, respond(status, text="boom"), respond(201, json=created()))
    res = live.create_order(order_body())
    assert res.outcome == "unknown" and res.status == status and res.attempts == 1 and not res.ok
    assert len(live.transport.calls) == 1 and fake_clock.sleeps == []           # one POST, ever
    assert res.client_order_id == COID                                           # the id the caller must reconcile on


@pytest.mark.parametrize("exc", [requests.Timeout("slow"), requests.ReadTimeout("read"), requests.ConnectionError("rst"),
                                 requests.exceptions.ChunkedEncodingError("cut"), ConnectionResetError("rst"),
                                 TimeoutError("t"), OSError("down")])
def test_a_timeout_or_connection_error_is_unknown_and_is_never_resent(live, fake_clock, exc):
    live.transport.post(CREATE, exc)
    res = live.create_order(order_body())
    assert res.outcome == "unknown" and res.status is None and res.data is None and res.attempts == 1
    assert res.error["code"] == "transport_error" and res.error["message"] == type(exc).__name__
    assert res.error["shape"] == "exception" and "never resend" in res.error["details"]
    assert len(live.transport.calls) == 1 and fake_clock.sleeps == []


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_on_a_write_is_unknown_and_never_followed_or_resent(live, fake_clock, status):
    live.transport.post(CREATE, respond(status, headers={"Location": "https://api.elections.kalshi.com/x"}),
                        respond(201, json=created()))
    res = live.create_order(order_body())
    assert res.outcome == "unknown" and res.status == status
    assert res.error["code"] == "redirect" and res.error["shape"] == "redirect"
    assert len(live.transport.calls) == 1 and live.transport.calls[0].host == "external-api.kalshi.com"


@pytest.mark.parametrize("payload, code", [
    (None, "missing_order_id"), ({}, "missing_order_id"), ([], "missing_order_id"), ({"order_id": ""}, "missing_order_id"),
    ({"order_id": 123, "fill_count": "1.00", "remaining_count": "0.00"}, "missing_order_id"),
    ({"order_id": "o", "remaining_count": "0.00"}, "bad_fill_count"),
    ({"order_id": "o", "fill_count": None, "remaining_count": "0.00"}, "bad_fill_count"),
    ({"order_id": "o", "fill_count": "abc", "remaining_count": "0.00"}, "bad_fill_count"),
    ({"order_id": "o", "fill_count": 1.0, "remaining_count": "0.00"}, "bad_fill_count"),
    ({"order_id": "o", "fill_count": "1.00"}, "bad_remaining_count"),
    ({"order_id": "o", "fill_count": "1.00", "remaining_count": "1e1"}, "bad_remaining_count"),
    ({"order_id": "o", "fill_count": "1.00", "remaining_count": "0.00", "client_order_id": "someone-else"},
     "client_order_id_mismatch"),
])
def test_a_2xx_with_an_unusable_body_is_unknown_so_it_gets_reconciled(live, payload, code):
    """The order may well exist: never treat a malformed success as a clean fill or a clean reject."""
    text = "" if payload is None else json.dumps(payload)
    live.transport.post(CREATE, status=201, text=text)
    res = live.create_order(order_body())
    assert res.outcome == "unknown" and res.status == 201 and res.error["code"] == code
    assert "reconcile" in res.error["message"] and len(live.transport.calls) == 1


def test_a_201_with_a_non_json_body_is_unknown(live):
    live.transport.post(CREATE, status=201, text="<html>created</html>")
    res = live.create_order(order_body())
    assert res.outcome == "unknown" and res.error["code"] == "missing_order_id" and res.data is None


# ================================================================================================ 429: the only resend
@pytest.fixture
def ticking_signatures(monkeypatch):
    """Whole-second ticks for auth's clock: every signing attempt gets its own timestamp."""
    ticks = iter(range(1_791_226_200, 1_791_226_400))
    monkeypatch.setattr(auth, "time", type("T", (), {"time": staticmethod(lambda: float(next(ticks)))}))


def test_a_429_is_resent_twice_with_the_same_body_and_id_then_reported_as_throttled(live, fake_clock, ticking_signatures,
                                                                                    prod_env):
    live.transport.post(CREATE, status=429, fixture="synthetic/error_429_flat.json")
    res = live.create_order(order_body())
    calls = live.transport.writes
    assert res.outcome == "throttled" and res.status == 429 and res.attempts == 3 and len(calls) == 3
    assert fake_clock.sleeps == [1.0, 2.0] == list(WRITE_429_BACKOFF_S)          # after 1 s and 2 s, no more
    assert len({c.data for c in calls}) == 1                                      # the identical body, the same client_order_id
    assert all(c.body["client_order_id"] == COID for c in calls)
    stamps = [c.headers["KALSHI-ACCESS-TIMESTAMP"] for c in calls]
    assert len(set(stamps)) == 3                                                  # every attempt is re-signed afresh
    for c in calls:
        verify_signature(c, prod_env.key)
    assert res.error["shape"] == "error_string" and res.client_order_id == COID


@pytest.mark.parametrize("body", ["synthetic/error_429_flat.json", "error_429_nested.json", None])
def test_a_429_is_recognised_by_its_status_whatever_the_body_looks_like(live, body):
    live.transport.post(CREATE, status=429, **({"fixture": body} if body else {"text": "garbage"}))
    assert live.create_order(order_body()).outcome == "throttled"
    assert len(live.transport.calls) == 3


@pytest.mark.parametrize("replies, attempts, sleeps", [
    ([respond(429), respond(201, json=created())], 2, [1.0]),
    ([respond(429), respond(429), respond(201, json=created())], 3, [1.0, 2.0]),
])
def test_a_429_that_clears_returns_the_ok_result(live, fake_clock, replies, attempts, sleeps):
    live.transport.post(CREATE, *replies)
    res = live.create_order(order_body())
    assert res.ok and res.attempts == attempts and fake_clock.sleeps == sleeps
    assert len(live.transport.calls) == attempts


def test_a_429_followed_by_a_5xx_stops_there_and_is_unknown(live, fake_clock):
    live.transport.post(CREATE, 429, 503, respond(201, json=created()))
    res = live.create_order(order_body())
    assert res.outcome == "unknown" and res.status == 503 and res.attempts == 2
    assert len(live.transport.calls) == 2 and fake_clock.sleeps == [1.0]


def test_a_429_followed_by_a_connection_error_is_unknown_not_resent(live, fake_clock):
    live.transport.post(CREATE, 429, requests.ConnectionError("rst"), respond(201, json=created()))
    res = live.create_order(order_body())
    assert res.outcome == "unknown" and res.attempts == 2 and len(live.transport.calls) == 2


def test_a_429_followed_by_a_409_is_a_duplicate_so_the_first_attempt_was_not_lost(live):
    live.transport.post(CREATE, 429, respond(409, fixture="synthetic/error_409_duplicate.json"))
    res = live.create_order(order_body())
    assert res.outcome == "duplicate" and res.attempts == 2


# ================================================================================================ the exchange_index trap
def test_the_order_body_always_names_the_markets_shard_and_never_auto_routes(live):
    live.transport.post(CREATE, status=201, json=created())
    live.create_order(order_body(exchange_index=2))
    assert live.transport.writes[0].body["exchange_index"] == 2
    for bad in (-1, None, "2", True):
        with pytest.raises((ValueError, TypeError)):
            live.create_order(order_body(exchange_index=bad))


# ================================================================================================ batch create
def two_bodies():
    return [order_body(client_order_id="c0ffee00-0000-4000-8000-00000000000a"),
            order_body(client_order_id="c0ffee00-0000-4000-8000-00000000000b", side="ask", price="0.4100")]


def test_a_batch_where_both_orders_fill(live):
    live.transport.post(BATCH, status=201, fixture="synthetic/batch_create_201_both_filled.json")
    results = live.batch_create(two_bodies())
    (call,) = live.transport.writes
    assert call.endpoint == BATCH and call.body == {"orders": two_bodies()} and call.signed
    assert [r.outcome for r in results] == ["ok", "ok"]
    assert [r.client_order_id for r in results] == [b["client_order_id"] for b in two_bodies()]
    assert [r.order_id[-1] for r in results] == ["a", "b"] and all(r.status == 201 for r in results)
    assert results[0].data["average_fill_price"] == "0.5600"


def test_a_batch_with_one_failed_item_gives_per_item_outcomes(live):
    live.transport.post(BATCH, status=201, fixture="synthetic/batch_create_201_one_error.json")
    ok, bad = live.batch_create(two_bodies())
    assert ok.ok and not bad.ok and bad.outcome == "rejected"
    assert bad.error["code"] == "available_balance_too_low" and bad.status == 201
    assert bad.client_order_id == "c0ffee00-0000-4000-8000-00000000000b" and bad.order_id is None


def test_batch_results_follow_the_request_order_not_the_response_order(live):
    items = json.loads(respond(fixture="synthetic/batch_create_201_both_filled.json").text)["orders"]
    live.transport.post(BATCH, status=201, json={"orders": list(reversed(items))})
    results = live.batch_create(two_bodies())
    assert [r.client_order_id[-1] for r in results] == ["a", "b"] and [r.order_id[-1] for r in results] == ["a", "b"]


@pytest.mark.parametrize("code, outcome", [
    ("order_already_exists", "duplicate"), ("duplicate_client_order_id", "duplicate"), ("already_exists", "duplicate"),
    ("internal_error", "unknown"), ("service_unavailable", "unknown"), ("timeout", "unknown"), ("unknown", "unknown"),
    ("invalid_order", "rejected"), ("available_balance_too_low", "rejected"), ("not_found", "rejected"), (None, "rejected"),
])
def test_per_item_error_codes_map_to_outcome_classes(live, code, outcome):
    items = json.loads(respond(fixture="synthetic/batch_create_201_both_filled.json").text)["orders"]
    items[1] = {"order_id": None, "client_order_id": items[1]["client_order_id"], "error": {"code": code, "message": "m"}}
    live.transport.post(BATCH, status=201, json={"orders": items})
    assert live.batch_create(two_bodies())[1].outcome == outcome


@pytest.mark.parametrize("status, outcome, calls", [(400, "rejected", 1), (401, "auth", 1), (409, "duplicate", 1),
                                                    (500, "unknown", 1), (429, "throttled", 3)])
def test_a_whole_batch_failure_gives_every_item_the_same_outcome(live, fake_clock, status, outcome, calls):
    live.transport.post(BATCH, status=status, json={"error": {"code": "x", "message": "m"}})
    results = live.batch_create(two_bodies())
    assert [r.outcome for r in results] == [outcome, outcome] and len(live.transport.calls) == calls
    assert [r.client_order_id for r in results] == [b["client_order_id"] for b in two_bodies()]   # still per order


def test_a_batch_that_times_out_is_unknown_for_every_item_and_not_resent(live):
    live.transport.post(BATCH, requests.Timeout("slow"))
    results = live.batch_create(two_bodies())
    assert [r.outcome for r in results] == ["unknown", "unknown"] and len(live.transport.calls) == 1
    assert [r.client_order_id[-1] for r in results] == ["a", "b"]


@pytest.mark.parametrize("payload, code", [
    ({"orders": "x"}, "batch_response_shape"), ({"nope": []}, "batch_response_shape"), ([], "batch_response_shape"),
    ({"orders": []}, "batch_response_count"),
])
def test_a_batch_response_of_the_wrong_shape_or_size_is_unknown_for_every_item(live, payload, code):
    live.transport.post(BATCH, status=201, json=payload)
    results = live.batch_create(two_bodies())
    assert [r.outcome for r in results] == ["unknown", "unknown"] and {r.error["code"] for r in results} == {code}


def test_an_item_for_an_unknown_client_order_id_is_not_matched_by_position(live):
    items = json.loads(respond(fixture="synthetic/batch_create_201_both_filled.json").text)["orders"]
    items[1]["client_order_id"] = "someone-elses-order"
    live.transport.post(BATCH, status=201, json={"orders": items})
    ok, lost = live.batch_create(two_bodies())
    assert ok.ok and lost.outcome == "unknown" and lost.error["code"] == "batch_item_missing"


def test_an_item_without_a_client_order_id_is_matched_by_position(live):
    items = json.loads(respond(fixture="synthetic/batch_create_201_both_filled.json").text)["orders"]
    for it in items:
        del it["client_order_id"]
    live.transport.post(BATCH, status=201, json={"orders": items})
    assert [r.outcome for r in live.batch_create(two_bodies())] == ["ok", "ok"]


def test_a_batch_item_with_a_bad_body_and_no_error_is_unknown(live):
    items = json.loads(respond(fixture="synthetic/batch_create_201_both_filled.json").text)["orders"]
    items[0]["fill_count"] = None
    live.transport.post(BATCH, status=201, json={"orders": items})
    first, second = live.batch_create(two_bodies())
    assert first.outcome == "unknown" and first.error["code"] == "bad_fill_count" and second.ok


def test_batch_create_validates_its_input_before_sending(live):
    live.transport.post(BATCH, status=201, json={"orders": []})
    with pytest.raises(ValueError, match="at least one"):
        live.batch_create([])
    with pytest.raises(ValueError, match=f"at most {MAX_BATCH}"):
        live.batch_create([order_body(client_order_id=f"id-{i}") for i in range(MAX_BATCH + 1)])
    with pytest.raises(ValueError, match="unique"):
        live.batch_create([order_body(), order_body()])
    with pytest.raises(ValueError):
        live.batch_create([order_body(client_order_id="id-1"), order_body(client_order_id="id-2", price="56")])
    assert live.transport.calls == []


def test_a_full_batch_of_ten_is_allowed(live):
    bodies = [order_body(client_order_id=f"id-{i}") for i in range(MAX_BATCH)]
    live.transport.post(BATCH, status=201, json={"orders": [created(client_order_id=b["client_order_id"],
                                                                   order_id=f"o-{i}") for i, b in enumerate(bodies)]})
    assert all(r.ok for r in live.batch_create(bodies)) and len(live.transport.writes[0].body["orders"]) == MAX_BATCH


# ================================================================================================ cancel: routing
def test_cancel_order_signature_requires_the_exchange_index_and_has_no_market_ticker():
    sig = inspect.signature(ExecClient.cancel_order)
    assert list(sig.parameters) == ["self", "order_id", "exchange_index"]
    assert all(p.default is inspect.Parameter.empty for n, p in sig.parameters.items() if n != "self")
    with pytest.raises(TypeError):
        ExecClient.cancel_order(None, OID)                                      # type: ignore[call-arg]


def test_a_cancel_carries_the_orders_own_exchange_index_in_the_query(live, prod_env):
    live.transport.delete(f"{CREATE}/{OID}", fixture="synthetic/cancel_200.json")
    res = live.cancel_order(OID, 2)
    (call,) = live.transport.writes
    assert res.ok and res.data["reduced_by"] == "10.00" and res.order_id == OID
    assert call.method == "DELETE" and call.endpoint == f"{CREATE}/{OID}" and call.params == {"exchange_index": 2}
    assert call.data is None and call.body is None and "Content-Type" not in call.headers
    assert call.path == f"/trade-api/v2/portfolio/events/orders/{OID}" and "?" not in call.path   # the query is not signed
    assert call.presign_text().endswith(f"DELETE/trade-api/v2/portfolio/events/orders/{OID}")
    verify_signature(call, prod_env.key)


def test_every_shard_of_a_real_order_listing_is_cancelled_on_its_own_shard(live, load_fixture):
    """Orders carry their own exchange_index (KXBTC15M/KXBTCD: 2, KXUSDJPY15M: 0); the cancel must echo it."""
    orders = load_fixture("synthetic/orders.json")["orders"]
    assert [o["exchange_index"] for o in orders] == [2, 2, 0]
    for o in orders:
        live.transport.delete(f"{CREATE}/{o['order_id']}", json={"order_id": o["order_id"], "reduced_by": "1.00"})
        assert live.cancel_order(o["order_id"], o["exchange_index"]).ok
    assert [c.params for c in live.transport.writes] == [{"exchange_index": 2}, {"exchange_index": 2},
                                                          {"exchange_index": 0}]
    for c in live.transport.writes:
        assert "market_ticker" not in (c.params or {}) and "market_ticker" not in c.url


def test_exchange_index_zero_is_sent_explicitly_and_never_together_with_market_ticker(live):
    live.transport.delete(f"{CREATE}/{OID}", fixture="synthetic/cancel_200.json")
    live.cancel_order(OID, 0)
    (call,) = live.transport.writes
    assert call.params == {"exchange_index": 0} and set(call.params) == {"exchange_index"}


@pytest.mark.parametrize("bad", [-1, True, False, "2", 2.0, None, 2.5])
def test_a_cancel_without_a_valid_shard_is_refused_before_sending(live, bad):
    with pytest.raises(ValueError, match="exchange_index"):
        live.cancel_order(OID, bad)
    with pytest.raises(ValueError, match="exchange_index"):
        live.batch_cancel([{"order_id": OID, "exchange_index": bad}])
    with pytest.raises(ValueError, match="exchange_index"):
        live.decrease_order(OID, bad, reduce_by=D("1"))
    assert live.transport.calls == []


@pytest.mark.parametrize("bad", ["", "batched", "queue_positions", "a/b", "a b", "../x", "a?exchange_index=0", None, 5])
def test_a_cancel_for_something_that_is_not_an_order_id_is_refused(live, bad):
    for call in (lambda: live.cancel_order(bad, 2), lambda: live.decrease_order(bad, 2, reduce_by=D("1")),
                 lambda: live.amend_order(bad, {"ticker": TICKER, "side": "bid", "price": "0.5600", "count": "1.00",
                                                "exchange_index": 2})):
        with pytest.raises(ValueError):
            call()
    assert live.transport.calls == []


def test_a_cancel_response_that_describes_another_order_is_unknown(live):
    """Known issue (2026-05-21): the response can describe a different order. Check order_id, then reconcile."""
    live.transport.delete(f"{CREATE}/{OID}", fixture="synthetic/cancel_200_other_order.json")
    res = live.cancel_order(OID, 2)
    assert res.outcome == "unknown" and res.status == 200 and res.error["code"] == "order_id_mismatch"
    assert res.data["order_id"] == OID2 and len(live.transport.calls) == 1


@pytest.mark.parametrize("payload, code", [
    (None, "missing_order_id"), ({}, "missing_order_id"), ({"reduced_by": "1.00"}, "missing_order_id"),
    ({"order_id": OID}, "bad_reduced_by"), ({"order_id": OID, "reduced_by": None}, "bad_reduced_by"),
    ({"order_id": OID, "reduced_by": 1.0}, "bad_reduced_by"), ({"order_id": OID, "reduced_by": "one"}, "bad_reduced_by"),
])
def test_a_cancel_response_without_a_usable_echo_is_unknown(live, payload, code):
    live.transport.delete(f"{CREATE}/{OID}", status=200, text="" if payload is None else json.dumps(payload))
    res = live.cancel_order(OID, 2)
    assert res.outcome == "unknown" and res.error["code"] == code


def test_a_404_on_cancel_is_rejected_but_never_proof_the_order_is_gone(live, fake_clock):
    """Cancelling on the wrong shard (or after close) also 404s: the caller must reconcile with orders(ticker)."""
    live.transport.delete(f"{CREATE}/{OID}", status=404, fixture="error_404_not_found.json")
    res = live.cancel_order(OID, 0)
    assert res.outcome == "rejected" and res.status == 404 and res.error["code"] == "not_found"
    assert len(live.transport.calls) == 1 and fake_clock.sleeps == []


def test_a_cancel_after_the_close_is_a_rejection_not_an_unknown(live):
    live.transport.delete(f"{CREATE}/{OID}", status=400, json={"error": {"code": "market_inactive", "message": "MARKET_INACTIVE"}})
    res = live.cancel_order(OID, 2)
    assert res.outcome == "rejected" and res.error["message"] == "MARKET_INACTIVE"


def test_a_cancel_that_times_out_is_unknown_and_not_resent(live):
    live.transport.delete(f"{CREATE}/{OID}", requests.Timeout("slow"))
    res = live.cancel_order(OID, 2)
    assert res.outcome == "unknown" and len(live.transport.calls) == 1


# ================================================================================================ batch cancel
def test_batch_cancel_sends_each_orders_shard_and_maps_items(live):
    live.transport.delete(f"{CREATE}/batched", fixture="synthetic/batch_cancel_200.json")
    items = [{"order_id": OID, "exchange_index": 2}, {"order_id": OID2, "exchange_index": 0, "ignored": "x"}]
    results = live.batch_cancel(items)
    (call,) = live.transport.writes
    assert call.method == "DELETE" and call.endpoint == f"{CREATE}/batched" and call.params is None
    assert call.body == {"orders": [{"order_id": OID, "exchange_index": 2}, {"order_id": OID2, "exchange_index": 0}]}
    assert call.headers["Content-Type"] == "application/json"                     # a DELETE with a JSON body
    assert [r.outcome for r in results] == ["ok", "ok"] and [r.data["reduced_by"] for r in results] == ["10.00", "5.00"]
    assert "market_ticker" not in call.data


def test_batch_cancel_with_one_error_item(live):
    live.transport.delete(f"{CREATE}/batched", fixture="synthetic/batch_cancel_200_one_error.json")
    ok, bad = live.batch_cancel([{"order_id": OID, "exchange_index": 2}, {"order_id": OID2, "exchange_index": 2}])
    assert ok.ok and bad.outcome == "rejected" and bad.error["code"] == "not_found"


@pytest.mark.parametrize("status, outcome", [(400, "rejected"), (401, "auth"), (500, "unknown")])
def test_batch_cancel_whole_failure(live, status, outcome):
    live.transport.delete(f"{CREATE}/batched", status=status, json={"error": {"code": "x", "message": "m"}})
    results = live.batch_cancel([{"order_id": OID, "exchange_index": 2}, {"order_id": OID2, "exchange_index": 2}])
    assert [r.outcome for r in results] == [outcome, outcome]


def test_batch_cancel_items_missing_from_the_response_are_unknown(live):
    live.transport.delete(f"{CREATE}/batched", status=200, json={"orders": [
        {"order_id": OID, "reduced_by": "1.00"}, {"order_id": "someone-else", "reduced_by": "1.00"}]})
    first, second = live.batch_cancel([{"order_id": OID, "exchange_index": 2}, {"order_id": OID2, "exchange_index": 2}])
    assert first.ok and second.outcome == "unknown" and second.error["code"] == "batch_item_missing"
    live.transport.delete(f"{CREATE}/batched", status=200, json={"orders": []})
    assert {r.error["code"] for r in live.batch_cancel([{"order_id": OID, "exchange_index": 2}])} == {"batch_response_count"}


def test_batch_cancel_validation(live):
    with pytest.raises(ValueError, match="at least one"):
        live.batch_cancel([])
    with pytest.raises(ValueError, match="at most 50"):
        live.batch_cancel([{"order_id": f"o{i}", "exchange_index": 2} for i in range(51)])
    with pytest.raises(KeyError):
        live.batch_cancel([{"order_id": OID}])                                   # the shard is mandatory
    with pytest.raises(ValueError):
        live.batch_cancel([{"order_id": "batched", "exchange_index": 2}])
    assert live.transport.calls == []
    live.transport.delete(f"{CREATE}/batched", status=200,
                          json={"orders": [{"order_id": f"o{i}", "reduced_by": "1.00"} for i in range(50)]})
    assert len(live.batch_cancel([{"order_id": f"o{i}", "exchange_index": 2} for i in range(50)])) == 50


# ================================================================================================ amend and decrease
def amend_body(**over):
    body = {"ticker": TICKER, "side": "bid", "price": "0.5700", "count": "2.00", "exchange_index": 2}
    body.update(over)
    return body


def test_amend_posts_the_new_total_and_accepts_a_response_without_remaining_count(live, prod_env):
    live.transport.post(f"{CREATE}/{OID}/amend", status=200, fixture="synthetic/amend_200_no_remaining.json")
    res = live.amend_order(OID, amend_body(client_order_id=COID, expiration_time=1_791_226_500))
    (call,) = live.transport.writes
    assert res.ok and res.order_id == OID
    assert call.endpoint == f"{CREATE}/{OID}/amend" and call.params is None
    assert call.body == amend_body(client_order_id=COID, expiration_time=1_791_226_500)
    verify_signature(call, prod_env.key)
    assert res.client_order_id == COID


@pytest.mark.parametrize("over", [
    {"side": "yes"}, {"price": "0.57"}, {"price": 0.57}, {"count": "2"}, {"count": 2}, {"exchange_index": -1},
    {"exchange_index": None}, {"expiration_time": 1_791_226_500_000}, {"expiration_time": "soon"}, {"post_only": True},
    {"market_ticker": TICKER}, {"yes_price": 57}, {"client_order_id": ""}, {"updated_client_order_id": "a b"},
    {"ticker": "A/B"},
])
def test_a_malformed_amend_is_refused_before_sending(live, over):
    with pytest.raises((ValueError, TypeError)):
        live.amend_order(OID, amend_body(**over))
    assert live.transport.calls == []


def test_amend_requires_every_mandatory_field_and_a_mapping(live):
    for field in ("ticker", "side", "price", "count", "exchange_index"):
        body = amend_body()
        del body[field]
        with pytest.raises(ValueError, match="missing"):
            live.amend_order(OID, body)
    with pytest.raises(TypeError):
        live.amend_order(OID, [])                                                # type: ignore[arg-type]
    assert live.transport.calls == []


def test_amend_may_clear_the_expiry_with_zero(live):
    live.transport.post(f"{CREATE}/{OID}/amend", status=200, json={"order_id": OID, "ts_ms": 1})
    assert live.amend_order(OID, amend_body(expiration_time=0)).ok
    assert live.transport.writes[0].body["expiration_time"] == 0


def test_amend_response_for_another_order_is_unknown(live):
    live.transport.post(f"{CREATE}/{OID}/amend", status=200, json={"order_id": OID2, "ts_ms": 1})
    res = live.amend_order(OID, amend_body())
    assert res.outcome == "unknown" and res.error["code"] == "order_id_mismatch"


@pytest.mark.parametrize("status, outcome", [(400, "rejected"), (404, "rejected"), (409, "duplicate"), (500, "unknown"),
                                             (401, "auth")])
def test_amend_outcome_classes(live, status, outcome):
    live.transport.post(f"{CREATE}/{OID}/amend", status=status, json={"error": {"code": "x", "message": "m"}})
    assert live.amend_order(OID, amend_body()).outcome == outcome


def test_decrease_by_and_to(live, prod_env):
    live.transport.post(f"{CREATE}/{OID}/decrease", status=200, fixture="synthetic/decrease_200.json")
    res = live.decrease_order(OID, 2, reduce_by=D("2"))
    assert res.ok and res.data["remaining_count"] == "8.00"
    live.decrease_order(OID, 2, reduce_to="1.50")
    live.decrease_order(OID, 2, reduce_to=D("0"))
    by, to, zero = live.transport.writes
    assert by.body == {"exchange_index": 2, "reduce_by": "2.00"}
    assert to.body == {"exchange_index": 2, "reduce_to": "1.50"}
    assert zero.body == {"exchange_index": 2, "reduce_to": "0.00"}              # reduce_to 0 cancels the rest
    assert by.endpoint == f"{CREATE}/{OID}/decrease" and by.params is None
    verify_signature(by, prod_env.key)


@pytest.mark.parametrize("kw", [{}, {"reduce_by": D("1"), "reduce_to": D("1")}, {"reduce_by": "1"}, {"reduce_by": 1},
                                {"reduce_by": 1.0}, {"reduce_by": D("0")}, {"reduce_by": "0.00"}, {"reduce_by": D("-1")},
                                {"reduce_by": D("0.001")}, {"reduce_to": "1"}, {"reduce_to": 0}, {"reduce_to": D("-0.01")}])
def test_decrease_needs_exactly_one_valid_count(live, kw):
    with pytest.raises((ValueError, TypeError)):
        live.decrease_order(OID, 2, **kw)
    assert live.transport.calls == []


def test_decrease_response_must_echo_the_order_and_the_remaining_count(live):
    live.transport.post(f"{CREATE}/{OID}/decrease", status=200, json={"order_id": OID2, "remaining_count": "1.00"})
    assert live.decrease_order(OID, 2, reduce_by=D("1")).error["code"] == "order_id_mismatch"
    live.transport.post(f"{CREATE}/{OID}/decrease", status=200, json={"order_id": OID})
    assert live.decrease_order(OID, 2, reduce_by=D("1")).error["code"] == "bad_remaining_count"


# ================================================================================================ cancel all
def test_cancel_all_is_a_bare_signed_delete_returning_204(live, prod_env):
    live.transport.delete(CREATE, 204)
    res = live.cancel_all()
    (call,) = live.transport.writes
    assert res.ok and res.status == 204 and res.data is None
    assert call.method == "DELETE" and call.endpoint == CREATE and call.params is None and call.data is None
    assert call.path == "/trade-api/v2/portfolio/events/orders" and "Content-Type" not in call.headers
    verify_signature(call, prod_env.key)


def test_cancel_all_with_a_json_body_is_also_ok(live):
    live.transport.delete(CREATE, status=200, json={"cancelled": 3})
    res = live.cancel_all()
    assert res.ok and res.data == {"cancelled": 3}


@pytest.mark.parametrize("status, outcome, calls", [(401, "auth", 1), (403, "auth", 1), (500, "unknown", 1),
                                                    (503, "unknown", 1), (404, "rejected", 1), (429, "throttled", 3)])
def test_cancel_all_failures(live, status, outcome, calls):
    live.transport.delete(CREATE, status=status, json={"error": {"code": "x", "message": "m"}})
    res = live.cancel_all()
    assert res.outcome == outcome and len(live.transport.calls) == calls


def test_cancel_all_that_times_out_is_unknown(live):
    live.transport.delete(CREATE, requests.ConnectionError("rst"))
    assert live.cancel_all().outcome == "unknown"


# ================================================================================================ keys and gates, again
def test_writes_need_a_loaded_key_even_if_someone_forces_the_internal_path(make_client, watch_env):
    c = make_client(FakeTransport(forbid_writes=True), watch_env)
    with pytest.raises((AuthError, PermissionError)):
        c._write("POST", CREATE, body={})
    assert c.transport.calls == []


def test_the_write_endpoints_are_the_v2_event_paths_only(live):
    for call in (lambda: live._write("POST", "/portfolio/orders", body={}),
                 lambda: live._write("DELETE", "/portfolio/orders/abc"),
                 lambda: live._write("POST", "/portfolio/intra_exchange_instance_transfer", body={}),
                 lambda: live._write("POST", "/api_keys/generate", body={})):
        with pytest.raises(EndpointNotAllowed):
            call()
    assert live.transport.calls == []
