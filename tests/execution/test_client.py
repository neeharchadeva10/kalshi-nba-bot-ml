"""kpm.execution.client: gates, endpoint allowlist, reads, retries, redirects, error parsing (design section 4).

Writes are in test_client_writes.py, the audit trail in test_client_audit.py, the clock probe in test_client_clock.py.
"""
from __future__ import annotations

import dataclasses
import json
import re
from decimal import Decimal

import pytest
import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from kpm.execution import auth, client as client_mod
from kpm.execution.client import (ENDPOINTS, MAX_PAGES, READ_BACKOFF_S, ApiError, AuthError, EndpointNotAllowed,
                                  ExecClient, ExecError, Listing, RedirectError, RequestsTransport, Result,
                                  TransportError, breakdown_cash, classify, endpoint_kind, parse_error, shard_status,
                                  shard_trading_active)

from .conftest import (COID, TEST_KEY_ID, TICKER, FakeClock, FakeTransport, order_body, respond, verify_signature)

D = Decimal
BOOK = f"/markets/{TICKER}/orderbook"


# ================================================================================================ construction and gates
def test_an_env_object_is_required():
    for bad in ("prod", None, object(), {"name": "prod"}):
        with pytest.raises(TypeError, match="Env"):
            ExecClient(bad)                                                      # type: ignore[arg-type]


def test_live_needs_a_loaded_key_and_must_be_a_bool(watch_env, prod_env):
    with pytest.raises(ValueError, match="API key"):
        ExecClient(watch_env, live=True)
    for bad in (1, "yes", None):
        with pytest.raises(TypeError, match="bool"):
            ExecClient(prod_env, live=bad)                                       # type: ignore[arg-type]
    assert ExecClient(prod_env, live=True).live is True
    assert ExecClient(prod_env).live is False                                    # dry-run unless asked otherwise
    assert ExecClient(watch_env).live is False


def test_the_repr_shows_the_fingerprint_never_the_key_id(prod_env):
    text = repr(ExecClient(prod_env, transport=FakeTransport()))
    assert prod_env.key_fp in text and "external-api.kalshi.com" in text and "live=False" in text
    assert TEST_KEY_ID not in text and "Ed25519" not in text


def test_close_closes_the_transport_when_it_can():
    class Closable(FakeTransport):
        closed = False

        def close(self):
            self.closed = True

    t = Closable()
    from kpm.execution.env import HOSTS, Env
    c = ExecClient(Env("prod", HOSTS["prod"]["rest"], HOSTS["prod"]["ws"]), transport=t)
    c.close()
    assert t.closed
    ExecClient(Env("prod", HOSTS["prod"]["rest"], HOSTS["prod"]["ws"]), transport=FakeTransport()).close()  # no close(): fine


def valid_amend():
    return {"ticker": TICKER, "side": "bid", "price": "0.5600", "count": "1.00", "exchange_index": 2}


WRITE_CALLS = {
    "create_order": lambda c: c.create_order(order_body()),
    "batch_create": lambda c: c.batch_create([order_body()]),
    "cancel_order": lambda c: c.cancel_order("abc123", 2),
    "batch_cancel": lambda c: c.batch_cancel([{"order_id": "abc123", "exchange_index": 2}]),
    "amend_order": lambda c: c.amend_order("abc123", valid_amend()),
    "decrease_order": lambda c: c.decrease_order("abc123", 2, reduce_by=D("1")),
    "cancel_all": lambda c: c.cancel_all(),
}


@pytest.mark.parametrize("name", list(WRITE_CALLS))
@pytest.mark.parametrize("keys", [True, False], ids=["keys-loaded", "watch-only"])
def test_every_write_raises_when_the_client_is_not_live_and_nothing_is_sent(make_client, prod_env, watch_env, name, keys):
    t = FakeTransport(forbid_writes=True)
    c = make_client(t, prod_env if keys else watch_env, live=False)
    with pytest.raises(PermissionError, match="not live"):
        WRITE_CALLS[name](c)
    assert t.calls == []                                                         # not even a read, nothing signed


@pytest.mark.parametrize("method, endpoint", [("POST", "/portfolio/events/orders"),
                                              ("DELETE", "/portfolio/events/orders"),
                                              ("DELETE", "/portfolio/events/orders/abc123"),
                                              ("POST", "/portfolio/events/orders/abc123/amend")])
def test_the_single_send_point_refuses_writes_on_a_dry_run_client_too(make_client, prod_env, method, endpoint):
    """Second line of defense (design 2): even a private call cannot get a write past a non-live client."""
    t = FakeTransport(forbid_writes=True)
    c = make_client(t, prod_env, live=False)
    with pytest.raises(PermissionError):
        c._write(method, endpoint, body={"a": 1})
    with pytest.raises(PermissionError):
        c._attempt(method, endpoint, "write", params=None, body={"a": 1}, attempt=1, client_order_id=None)
    assert t.calls == []


def test_a_live_client_does_send_writes(make_client, prod_env):
    t = FakeTransport()
    t.delete("/portfolio/events/orders", 204)
    c = make_client(t, prod_env, live=True)
    assert c.cancel_all().outcome == "ok"
    assert len(t.writes) == 1


def test_watch_only_reads_public_data_without_keys(make_client, watch_env):
    t = FakeTransport(host="external-api.kalshi.com")
    t.get("/exchange/status", fixture="exchange_status_prod.json")
    t.get("/series/KXBTC15M", fixture="series_KXBTC15M.json")
    t.get("/markets", fixture="markets_KXBTC15M_open_1430.json")
    t.get(BOOK, fixture="orderbook_KXBTC15M-26OCT051430-30_depth3.json")
    c = make_client(t, watch_env)
    assert c.exchange_status()["exchange_active"] is True
    assert c.series("KXBTC15M")["exchange_index"] == 2
    assert [m["ticker"] for m in c.open_markets("KXBTC15M")] == [TICKER]
    assert c.orderbook(TICKER, 3)["yes"][-1][0] == D("0.58")
    assert len(t.calls) == 4 and not any(call.signed for call in t.calls)


AUTH_CALLS = {
    "account_limits": lambda c: c.account_limits(),
    "balance": lambda c: c.balance(2),
    "balance_all": lambda c: c.balance(None),
    "shard_cash": lambda c: c.shard_cash(2),
    "positions": lambda c: c.positions(ticker=TICKER),
    "orders": lambda c: c.orders(ticker=TICKER),
    "order": lambda c: c.order("abc123"),
    "queue_position": lambda c: c.queue_position("abc123"),
    "fills": lambda c: c.fills(ticker=TICKER),
    "settlements": lambda c: c.settlements(ticker=TICKER),
}


@pytest.mark.parametrize("name", list(AUTH_CALLS))
def test_authenticated_reads_without_keys_raise_auth_error_and_send_nothing(make_client, watch_env, name):
    t = FakeTransport()
    c = make_client(t, watch_env)
    with pytest.raises(AuthError, match="needs an API key"):
        AUTH_CALLS[name](c)
    assert t.calls == []


# ================================================================================================ allowlist
ALLOWED = [
    ("GET", "/exchange/status", "public"), ("GET", "/exchange/user_data_timestamp", "public"),
    ("GET", "/historical/cutoff", "public"), ("GET", "/series/KXBTC15M", "public"), ("GET", "/markets", "public"),
    ("GET", f"/markets/{TICKER}", "public"), ("GET", BOOK, "public"),
    ("GET", "/account/limits", "read"), ("GET", "/portfolio/balance", "read"), ("GET", "/portfolio/positions", "read"),
    ("GET", "/portfolio/orders", "read"), ("GET", "/portfolio/orders/abc-123", "read"),
    ("GET", "/portfolio/orders/abc-123/queue_position", "read"), ("GET", "/portfolio/fills", "read"),
    ("GET", "/portfolio/settlements", "read"),
    ("POST", "/portfolio/events/orders", "write"), ("POST", "/portfolio/events/orders/batched", "write"),
    ("DELETE", "/portfolio/events/orders/batched", "write"), ("DELETE", "/portfolio/events/orders/abc-123", "write"),
    ("POST", "/portfolio/events/orders/abc-123/amend", "write"),
    ("POST", "/portfolio/events/orders/abc-123/decrease", "write"), ("DELETE", "/portfolio/events/orders", "write"),
]

DENIED = [
    # money movement, keys, subaccounts, allocation, tier upgrades: never reachable
    ("POST", "/portfolio/intra_exchange_instance_transfer"), ("POST", "/portfolio/subaccounts/transfer"),
    ("POST", "/portfolio/subaccounts"), ("GET", "/portfolio/subaccounts/balances"), ("GET", "/portfolio/deposits"),
    ("GET", "/portfolio/withdrawals"), ("POST", "/api_keys"), ("POST", "/api_keys/generate"), ("GET", "/api_keys"),
    ("DELETE", "/api_keys/abc"), ("POST", "/portfolio/target_balance_allocation"),
    ("POST", "/account/api_usage_level/upgrade"), ("GET", "/account/endpoint_costs"),
    ("POST", "/portfolio/order_groups/create"), ("PUT", "/portfolio/order_groups/abc/reset"),
    # the deprecated legacy order paths
    ("POST", "/portfolio/orders"), ("DELETE", "/portfolio/orders/abc"), ("POST", "/portfolio/orders/abc/amend"),
    ("POST", "/portfolio/orders/abc/decrease"), ("POST", "/portfolio/orders/batched"),
    ("DELETE", "/portfolio/orders/batched"),
    # right path, wrong method
    ("GET", "/portfolio/events/orders"), ("POST", "/markets"), ("POST", "/exchange/status"), ("DELETE", "/markets/abc"),
    ("PUT", "/portfolio/events/orders"), ("PATCH", "/portfolio/events/orders/abc"), ("GET", "/portfolio/events/orders/abc"),
    ("POST", "/portfolio/balance"), ("DELETE", "/portfolio/fills"),
    # malformed, traversal, extra segments
    ("GET", ""), ("GET", "/"), ("GET", "markets"), ("GET", "/markets/"), ("GET", "/markets/../portfolio/balance"),
    ("GET", "/markets/abc/def"), ("GET", "/markets/abc/orderbook/extra"), ("GET", "/portfolio/orders/abc/queue_position/x"),
    ("GET", "/portfolio/balance/"), ("GET", "/portfolio/balance?exchange_index=2"), ("GET", "//portfolio/balance"),
    ("GET", "/trade-api/v2/portfolio/balance"), ("GET", "/exchange/status/extra"), ("GET", "/events"),
    ("GET", "/incentive_programs"), ("GET", "/portfolio/summary/total_resting_order_value"),
    ("POST", "/portfolio/events/orders/abc/amend/extra"), ("POST", "/portfolio/events/orders/a b/decrease"),
]


@pytest.mark.parametrize("method, endpoint, kind", ALLOWED)
def test_allowlisted_endpoints_and_their_kinds(method, endpoint, kind):
    assert endpoint_kind(method, endpoint) == kind


@pytest.mark.parametrize("method, endpoint", DENIED)
def test_everything_else_is_refused(method, endpoint):
    with pytest.raises(EndpointNotAllowed):
        endpoint_kind(method, endpoint)


def test_endpoint_not_allowed_is_a_permission_error():
    assert issubclass(EndpointNotAllowed, PermissionError)


def test_the_allowlist_table_has_no_money_movement_and_only_v2_order_writes():
    writes = [(m, p) for m, p, k in ENDPOINTS if k == "write"]
    assert writes and all("/portfolio/events/orders" in p for _, p in writes)
    joined = " ".join(p for _, p, _ in ENDPOINTS)
    for word in ("transfer", "api_keys", "subaccount", "allocation", "upgrade", "order_groups", "deposit", "withdraw"):
        assert word not in joined
    assert not any(m in ("PUT", "PATCH") for m, _, _ in ENDPOINTS)


def test_a_disallowed_path_is_refused_before_anything_is_sent(make_client, prod_env):
    t = FakeTransport()
    c = make_client(t, prod_env, live=True)
    for call in (lambda: c._get("/portfolio/events/orders"), lambda: c._get("/api_keys"),
                 lambda: c._get_reply("/portfolio/subaccounts/balances"),
                 lambda: c._write("POST", "/portfolio/intra_exchange_instance_transfer", body={}),
                 lambda: c._write("GET", "/portfolio/balance"),
                 lambda: c._attempt("POST", "/portfolio/events/orders", "read", params=None, body={}, attempt=1,
                                    client_order_id=None)):
        with pytest.raises(EndpointNotAllowed):
            call()
    assert t.calls == []


def test_every_client_method_stays_on_the_allowlist_and_signs_exactly_what_needs_signing(make_client, any_key, make_env):
    """A tour of every method: each request is allowlisted, public ones are unsigned, the rest verify."""
    t = FakeTransport(host="external-api.kalshi.com")
    t.get("/exchange/status", fixture="exchange_status_prod.json")
    t.get("/exchange/user_data_timestamp", fixture="user_data_timestamp.json")
    t.get("/historical/cutoff", fixture="historical_cutoff.json")
    t.get("/series/KXBTC15M", fixture="series_KXBTC15M.json")
    t.get("/markets", fixture="markets_KXBTC15M_open_1430.json")
    t.get(f"/markets/{TICKER}", fixture="market_KXBTC15M-26OCT051430-30.json")
    t.get(BOOK, fixture="orderbook_KXBTC15M-26OCT051430-30_depth3.json")
    t.get("/account/limits", fixture="synthetic/account_limits.json")
    t.get("/portfolio/balance", fixture="synthetic/balance.json")
    t.get("/portfolio/positions", fixture="synthetic/positions.json")
    t.get("/portfolio/orders", fixture="synthetic/orders.json")
    t.get("/portfolio/orders/abc123", fixture="synthetic/order_single.json")
    t.get("/portfolio/orders/abc123/queue_position", fixture="synthetic/queue_position.json")
    t.get("/portfolio/fills", fixture="synthetic/fills.json")
    t.get("/portfolio/settlements", fixture="synthetic/settlements.json")
    t.post("/portfolio/events/orders", status=201, json={"order_id": "o1", "fill_count": "1.00",
                                                         "remaining_count": "0.00", "ts_ms": 1})
    t.post("/portfolio/events/orders/batched", status=201,
           json={"orders": [{"order_id": "o1", "client_order_id": COID, "fill_count": "1.00", "remaining_count": "0.00"}]})
    t.delete("/portfolio/events/orders/batched", json={"orders": [{"order_id": "abc123", "reduced_by": "1.00"}]})
    t.delete("/portfolio/events/orders/abc123", json={"order_id": "abc123", "reduced_by": "1.00"})
    t.post("/portfolio/events/orders/abc123/amend", json={"order_id": "abc123", "ts_ms": 1})
    t.post("/portfolio/events/orders/abc123/decrease", json={"order_id": "abc123", "remaining_count": "0.50"})
    t.delete("/portfolio/events/orders", 204)
    env = make_env("prod", any_key)
    c = make_client(t, env, live=True)

    c.exchange_status(), c.user_data_timestamp(), c.historical_cutoff(), c.series("KXBTC15M")
    c.open_markets("KXBTC15M"), c.market(TICKER), c.orderbook(TICKER, 3)
    c.account_limits(), c.balance(2), c.shard_cash(2), c.positions(), c.orders(ticker=TICKER), c.order("abc123")
    c.queue_position("abc123"), c.fills(ticker=TICKER), c.settlements(ticker=TICKER)
    c.create_order(order_body()), c.batch_create([order_body()])
    c.batch_cancel([{"order_id": "abc123", "exchange_index": 2}]), c.cancel_order("abc123", 2)
    c.amend_order("abc123", valid_amend()), c.decrease_order("abc123", 2, reduce_by=D("0.5")), c.cancel_all()

    assert len(t.calls) == 23
    for call in t.calls:
        kind = endpoint_kind(call.method, call.endpoint)
        assert call.signed == (kind != "public"), (call.method, call.endpoint)
        assert call.url.startswith("https://external-api.kalshi.com/trade-api/v2/")
        assert call.path == "/trade-api/v2" + call.endpoint and "?" not in call.path
        if call.signed:
            assert call.headers["KALSHI-ACCESS-KEY"] == TEST_KEY_ID
            verify_signature(call, any_key)
        else:
            assert not {"KALSHI-ACCESS-KEY", "KALSHI-ACCESS-TIMESTAMP", "KALSHI-ACCESS-SIGNATURE"} & set(call.headers)


# ================================================================================================ public reads
def test_exchange_status_is_an_unsigned_get_of_the_documented_url(make_client, prod_env):
    t = FakeTransport(host="external-api.kalshi.com")
    t.get("/exchange/status", fixture="exchange_status_prod.json")
    c = make_client(t, prod_env)                                  # keys are loaded, but a public read is never signed
    status = c.exchange_status()
    (call,) = t.calls
    assert call.method == "GET" and call.url == "https://external-api.kalshi.com/trade-api/v2/exchange/status"
    assert call.params is None and call.data is None and not call.signed
    assert call.headers["Accept"] == "application/json" and call.timeout == c.timeout_s == 10.0
    assert [s["exchange_index"] for s in status["exchange_index_statuses"]] == [0, 1, 2, 3]


def test_the_demo_environment_talks_to_the_demo_host_only(make_client, demo_env):
    t = FakeTransport(host="external-api.demo.kalshi.co")
    t.get("/exchange/status", fixture="exchange_status_demo.json")
    t.get("/markets", fixture="markets_KXBTC15M_open_demo.json")
    c = make_client(t, demo_env)
    c.exchange_status()
    stale = c.open_markets("KXBTC15M")
    assert all(call.host == "external-api.demo.kalshi.co" for call in t.calls)
    assert any(m.get("status") == "closed" for m in stale)        # demo lists a stale closed market: callers must check


def test_shard_trading_active_needs_the_shards_own_entry_to_say_active(load_fixture):
    prod = load_fixture("exchange_status_prod.json")
    demo = load_fixture("exchange_status_demo.json")
    for ei in (0, 1, 2, 3):
        assert shard_trading_active(prod, ei) and shard_trading_active(demo, ei)
    assert shard_status(prod, 2)["description"] == "Crypto & Commodities"
    assert shard_status(prod, 9) is None and not shard_trading_active(prod, 9)            # missing shard = closed

    def with_shard2(**flags):
        s = json.loads(json.dumps(prod))
        s["exchange_index_statuses"][2].update(flags)
        return s

    assert not shard_trading_active(with_shard2(trading_active=False), 2)
    assert not shard_trading_active(with_shard2(exchange_active=False), 2)
    assert shard_trading_active(with_shard2(trading_active=False), 0)                     # other shards unaffected
    assert not shard_trading_active(with_shard2(trading_active="true"), 2)                # only a real True counts
    assert not shard_trading_active(with_shard2(trading_active=1), 2)
    broken = with_shard2()
    del broken["exchange_index_statuses"][2]["trading_active"]
    assert not shard_trading_active(broken, 2)
    top_only = {"exchange_active": True, "trading_active": True}                           # no breakdown: treat as closed
    assert not shard_trading_active(top_only, 0) and shard_status(top_only, 0) is None
    assert not shard_trading_active({"exchange_index_statuses": None}, 0)


def test_series_returns_the_series_object(make_client, watch_env):
    t = FakeTransport()
    t.get("/series/KXBTC15M", fixture="series_KXBTC15M.json")
    t.get("/series/KXUSDJPY15M", fixture="series_KXUSDJPY15M.json")
    c = make_client(t, watch_env)
    s = c.series("KXBTC15M")
    assert (s["ticker"], s["exchange_index"], s["fee_type"], s["fee_multiplier"]) == ("KXBTC15M", 2, "quadratic", 1)
    assert c.series("KXUSDJPY15M")["exchange_index"] == 0


def test_unwrapping_demands_the_wrapper_object(make_client, watch_env):
    t = FakeTransport()
    t.get("/series/KXBAD", {"ticker": "KXBAD"})                       # no {"series": ...} wrapper
    t.get("/series/KXNULL", {"series": None})
    t.get(f"/markets/{TICKER}", {"ticker": TICKER})
    c = make_client(t, watch_env)
    for call in (lambda: c.series("KXBAD"), lambda: c.series("KXNULL"), lambda: c.market(TICKER)):
        with pytest.raises(ApiError, match="has no"):
            call()


def test_market_returns_the_market_object(make_client, watch_env):
    t = FakeTransport()
    t.get(f"/markets/{TICKER}", fixture="market_KXBTC15M-26OCT051430-30.json")
    m = make_client(t, watch_env).market(TICKER)
    assert (m["ticker"], m["status"], m["exchange_index"], m["close_time"]) == (TICKER, "active", 2, "2026-10-05T18:30:00Z")
    assert len(m["price_ranges"]) == 3 and m["can_close_early"] is True


def test_open_markets_asks_for_status_open_and_returns_a_listing(make_client, watch_env):
    t = FakeTransport()
    r = t.get("/markets", fixture="markets_KXBTC15M_open_1430.json")
    c = make_client(t, watch_env)
    markets = c.open_markets("KXBTC15M")
    assert isinstance(markets, Listing) and [m["ticker"] for m in markets] == [TICKER]
    assert r.calls[0].params == {"series_ticker": "KXBTC15M", "status": "open", "limit": 1000}
    assert markets["markets"] == list(markets) and markets["cursor"] == "" and markets.get("nope", 7) == 7
    assert markets[0]["exchange_index"] == 2 and markets[0]["status"] == "active"      # "open" filter, "active" status


def test_open_markets_never_sends_the_close_time_filters(make_client, watch_env):
    """Kalshi's OpenAPI (GetMarkets): min_close_ts / max_close_ts are compatible only with status 'closed' or no status.
    The open-markets read therefore takes no such argument, and the only request it can make is the documented one."""
    import inspect
    assert list(inspect.signature(ExecClient.open_markets).parameters) == ["self", "series_ticker"]
    t = FakeTransport()
    r = t.get("/markets", {"markets": [], "cursor": ""})
    c = make_client(t, watch_env)
    for kw in ({"min_close_ts": 1_791_226_198}, {"max_close_ts": 1_791_226_202}):
        with pytest.raises(TypeError):
            c.open_markets("KXBTC15M", **kw)
    assert r.calls == []                                                 # refused before anything was sent
    c.open_markets("KXBTC15M")
    assert r.calls[0].params == {"series_ticker": "KXBTC15M", "status": "open", "limit": 1000}


def test_open_markets_follows_the_cursor_across_pages(make_client, watch_env):
    t = FakeTransport()
    m = lambda n: {"ticker": f"T{n}"}                                  # noqa: E731
    r = t.get("/markets", {"markets": [m(1), m(2)], "cursor": "C2"}, {"markets": [m(3)], "cursor": "C3"},
              {"markets": [], "cursor": ""})
    c = make_client(t, watch_env)
    out = c.open_markets("KXBTC15M")
    assert [x["ticker"] for x in out] == ["T1", "T2", "T3"]
    assert [call.params.get("cursor") for call in r.calls] == [None, "C2", "C3"]
    assert out.raw["cursor"] == ""


def test_pagination_is_bounded(make_client, watch_env):
    t = FakeTransport()
    r = t.get("/markets", {"markets": [{"ticker": "T"}], "cursor": "NEVER-ENDS"})
    c = make_client(t, watch_env)
    with pytest.raises(ApiError, match=f"more than {MAX_PAGES} pages"):
        c.open_markets("KXBTC15M")
    assert len(r.calls) == MAX_PAGES


def test_a_listing_response_of_the_wrong_shape_is_an_api_error(make_client, watch_env):
    t = FakeTransport()
    t.get("/markets", {"markets": "oops"})
    with pytest.raises(ApiError, match="unexpected response shape"):
        make_client(t, watch_env).open_markets("KXBTC15M")
    t2 = FakeTransport()
    t2.get("/markets", [1, 2, 3])
    with pytest.raises(ApiError, match="unexpected response shape"):
        make_client(t2, watch_env).open_markets("KXBTC15M")


@pytest.mark.parametrize("bad", ["", "a b", "A/B", "../x", "a?b", "a#b", "x" * 200, "-start", ".dot", None, 5, "KX\n"])
def test_tickers_that_could_alter_the_path_are_refused_before_sending(make_client, watch_env, bad):
    t = FakeTransport()
    c = make_client(t, watch_env)
    for call in (lambda: c.series(bad), lambda: c.market(bad), lambda: c.orderbook(bad), lambda: c.open_markets(bad)):
        with pytest.raises(ValueError):
            call()
    assert t.calls == []


# ---------------------------------------------------------------------------------------------- the orderbook
def test_orderbook_levels_are_decimal_ascending_with_the_best_bid_last(make_client, watch_env, fake_clock):
    t = FakeTransport()
    r = t.get(BOOK, fixture="orderbook_KXBTC15M-26OCT051430-30_depth3.json")
    c = make_client(t, watch_env)
    fake_clock.now = 1_791_226_345.25
    book = c.orderbook(TICKER, depth=3)
    assert book == {"ticker": TICKER, "fetched_at": 1_791_226_345.25,
                    "yes": [(D("0.56"), D("1658.88")), (D("0.57"), D("2173.00")), (D("0.58"), D("2685.09"))],
                    "no": [(D("0.39"), D("1959.00")), (D("0.40"), D("6427.76")), (D("0.41"), D("5367.45"))]}
    assert all(isinstance(p, Decimal) and isinstance(s, Decimal) for side in ("yes", "no") for p, s in book[side])
    assert r.calls[0].params == {"depth": 3} and r.calls[0].endpoint == BOOK
    assert book["yes"][-1][0] == D("0.58")                                    # the best YES bid is the LAST element


def test_orderbook_without_depth_sends_no_params_and_parses_a_real_full_book(make_client, watch_env):
    t = FakeTransport()
    r = t.get(BOOK, fixture="orderbook_KXBTC15M-26OCT051430-30_full.json")
    book = make_client(t, watch_env).orderbook(TICKER)
    assert r.calls[0].params is None
    assert len(book["yes"]) > 20 and len(book["no"]) > 20
    for side in ("yes", "no"):
        prices = [p for p, _ in book[side]]
        assert prices == sorted(prices) and prices[-1] == max(prices)


def test_orderbook_fetched_at_is_the_arrival_time(make_client, watch_env, fake_clock):
    t = FakeTransport(clock=fake_clock, latency_s=0.5)
    t.get(BOOK, fixture="orderbook_KXBTC15M-26OCT051430-30_depth3.json")
    start = fake_clock.now
    book = make_client(t, watch_env).orderbook(TICKER)
    assert book["fetched_at"] == pytest.approx(start + 0.5, abs=1e-6)         # abs: a bare approx is +-1800 s at epoch scale
    assert book["fetched_at"] > start + 0.4                                    # i.e. after the round trip, not at the send


def test_an_empty_or_one_sided_book_is_returned_as_is(make_client, watch_env):
    t = FakeTransport()
    t.get(BOOK, fixture="orderbook_demo_one_sided.json")
    t.get("/markets/NO-SUCH-TICKER/orderbook", {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}})
    t.get("/markets/NULLS/orderbook", {"orderbook_fp": {"yes_dollars": None}})
    c = make_client(t, watch_env)
    one = c.orderbook(TICKER)
    assert one["no"] == [] and one["yes"] == [(D("0.01"), D("1.00"))]
    nothing = c.orderbook("NO-SUCH-TICKER")                              # a nonexistent ticker: 200 and empty arrays
    assert (nothing["yes"], nothing["no"]) == ([], [])
    nulls = c.orderbook("NULLS")
    assert (nulls["yes"], nulls["no"]) == ([], [])


@pytest.mark.parametrize("payload, why", [
    ({"orderbook": {"yes_dollars": []}}, "no orderbook_fp"),
    ({"orderbook_fp": []}, "orderbook_fp is not an object"),
    ([], "not an object"),
    ({"orderbook_fp": {"yes_dollars": [["0.58", "1"], ["0.57", "1"]]}}, "descending: would price off the worst level"),
    ({"orderbook_fp": {"yes_dollars": [["0.57", "1"], ["0.57", "2"]]}}, "duplicate level"),
    ({"orderbook_fp": {"no_dollars": [["0.0000", "0.00"]]}}, "placeholder price 0"),
    ({"orderbook_fp": {"no_dollars": [["1.0000", "0.00"]]}}, "placeholder price 1"),
    ({"orderbook_fp": {"yes_dollars": [["0.5", "-3"]]}}, "negative size"),
    ({"orderbook_fp": {"yes_dollars": [[0.5, "3"]]}}, "float price"),
    ({"orderbook_fp": {"yes_dollars": [["0.5"]]}}, "short level"),
    ({"orderbook_fp": {"yes_dollars": ["0.5"]}}, "level is not a pair"),
])
def test_an_unusable_orderbook_is_an_api_error_never_a_guess(make_client, watch_env, payload, why):
    t = FakeTransport()
    t.get(BOOK, payload)
    with pytest.raises(ApiError):
        make_client(t, watch_env).orderbook(TICKER)


@pytest.mark.parametrize("depth", [0, -1, 101, True, "3", 3.0, 2.5])
def test_orderbook_depth_is_validated(make_client, watch_env, depth):
    t = FakeTransport()
    c = make_client(t, watch_env)
    with pytest.raises(ValueError, match="depth"):
        c.orderbook(TICKER, depth=depth)
    assert t.calls == []


def test_user_data_timestamp_and_historical_cutoff(make_client, watch_env):
    t = FakeTransport()
    t.get("/exchange/user_data_timestamp", fixture="user_data_timestamp.json")
    t.get("/historical/cutoff", fixture="historical_cutoff.json")
    c = make_client(t, watch_env)
    assert c.user_data_timestamp() == {"as_of_time": "2026-10-05T18:00:09.923815Z"}
    assert c.historical_cutoff()["orders_updated_ts"] == "2026-09-21T00:00:00Z"


# ================================================================================================ authenticated reads
def test_a_signed_read_carries_the_three_headers_and_a_valid_signature(make_client, make_env, any_key):
    t = FakeTransport()
    t.get("/portfolio/balance", fixture="synthetic/balance.json")
    c = make_client(t, make_env("prod", any_key))
    c.balance(2)
    (call,) = t.calls
    assert call.signed and call.params == {"exchange_index": 2}
    assert call.headers["KALSHI-ACCESS-KEY"] == TEST_KEY_ID
    assert re.fullmatch(r"[0-9]{13}", call.headers["KALSHI-ACCESS-TIMESTAMP"])
    assert call.presign_text().endswith("GET/trade-api/v2/portfolio/balance")           # the query is not signed
    verify_signature(call, any_key)
    with pytest.raises(InvalidSignature):                                               # it is THIS key's signature
        verify_signature(call, Ed25519PrivateKey.generate())


def test_balance_is_scoped_to_one_shard_and_never_the_integer_total(make_client, prod_env):
    t = FakeTransport()                                              # the latest matching route wins: generic first
    t.get("/portfolio/balance", fixture="synthetic/balance.json", params=None)
    t.get("/portfolio/balance", fixture="synthetic/balance_shard2.json", params={"exchange_index": 2})
    t.get("/portfolio/balance", {"balance": 500, "balance_breakdown": []}, params={"exchange_index": 7})
    c = make_client(t, prod_env)
    shard = c.balance(2)
    assert shard["balance_dollars"] == "5.0000" and t.calls[0].params == {"exchange_index": 2}
    assert c.balance(None)["balance"] == 2500 and t.calls[1].params is None            # all shards: the caller asked for it
    assert c.shard_cash(2) == D("5.0000") and isinstance(c.shard_cash(2), Decimal)
    with pytest.raises(ApiError, match="balance_dollars"):
        c.shard_cash(7)                                                                 # only the integer cents: refused
    for bad in (True, -1, "2", 2.0):
        with pytest.raises(ValueError, match="exchange_index"):
            c.balance(bad)
        with pytest.raises(ValueError, match="exchange_index"):
            c.shard_cash(bad)


def test_breakdown_cash_reads_the_dollar_string_of_the_right_shard(load_fixture):
    b = load_fixture("synthetic/balance.json")
    assert breakdown_cash(b, 0) == D("20.0000") and breakdown_cash(b, 2) == D("5.0000")
    assert breakdown_cash(b, 1) is None and breakdown_cash({}, 2) is None and breakdown_cash({"balance_breakdown": None}, 2) is None


def test_account_limits(make_client, prod_env):
    t = FakeTransport()
    t.get("/account/limits", fixture="synthetic/account_limits.json")
    lim = make_client(t, prod_env).account_limits()
    assert lim["usage_tier"] == "basic" and lim["write"]["bucket_capacity"] == 100


def test_orders_merge_pages_and_keep_the_merged_payload(make_client, prod_env):
    t = FakeTransport()
    r = t.get("/portfolio/orders", respond(fixture="synthetic/orders_page1.json"),
              respond(fixture="synthetic/orders_page2.json"))
    c = make_client(t, prod_env)
    orders = c.orders(ticker=TICKER, status="resting", limit=1)
    assert [o["order_id"][-1] for o in orders] == ["a", "b"]
    assert [call.params for call in r.calls] == [{"ticker": TICKER, "status": "resting", "limit": 1},
                                                  {"ticker": TICKER, "status": "resting", "limit": 1,
                                                   "cursor": "CURSOR-PAGE-2"}]
    assert orders["orders"] == list(orders) and orders["cursor"] == ""


def test_a_caller_supplied_cursor_returns_exactly_that_page(make_client, prod_env):
    t = FakeTransport()
    r = t.get("/portfolio/orders", respond(fixture="synthetic/orders_page1.json"))
    out = make_client(t, prod_env).orders(cursor="MINE")
    assert len(out) == 1 and len(r.calls) == 1 and out["cursor"] == "CURSOR-PAGE-2"


def test_none_parameters_are_dropped_from_the_query(make_client, prod_env):
    t = FakeTransport()
    r = t.get("/portfolio/orders", {"orders": [], "cursor": ""})
    make_client(t, prod_env).orders(ticker=TICKER, status=None, min_ts=None, exchange_index=None)
    assert r.calls[0].params == {"ticker": TICKER}


def test_positions_listing_exposes_market_and_event_positions(make_client, prod_env):
    t = FakeTransport()
    t.get("/portfolio/positions", fixture="synthetic/positions.json")
    pos = make_client(t, prod_env).positions(ticker=TICKER, settlement_status="all", count_filter="position")
    assert [p["position_fp"] for p in pos] == ["1.00", "-1.00"]                         # signed: + YES, - NO
    assert [e["event_ticker"] for e in pos["event_positions"]] == ["KXBTCD-26OCT0515", "KXBTC15M-26OCT051500"]
    assert t.calls[0].params == {"ticker": TICKER, "settlement_status": "all", "count_filter": "position"}


def test_fills_and_settlements_pass_through_with_integer_cents_untouched(make_client, prod_env):
    t = FakeTransport()
    t.get("/portfolio/fills", fixture="synthetic/fills.json")
    t.get("/portfolio/settlements", fixture="synthetic/settlements.json")
    c = make_client(t, prod_env)
    fills = c.fills(ticker=TICKER, order_id="7e1c2b3a-0000-4a5b-8c9d-00000000000a", min_ts=1_791_226_000)
    assert [f["fill_id"][-1] for f in fills] == ["a", "b"] and fills[1]["outcome_side"] == "no"
    assert t.calls[0].params == {"ticker": TICKER, "order_id": "7e1c2b3a-0000-4a5b-8c9d-00000000000a",
                                 "min_ts": 1_791_226_000}
    settled = c.settlements(ticker=TICKER)
    assert [s["revenue"] for s in settled] == [100, 0] and isinstance(settled[0]["revenue"], int)


def test_order_and_queue_position(make_client, prod_env):
    t = FakeTransport()
    t.get("/portfolio/orders/7e1c2b3a", fixture="synthetic/order_single.json")
    t.get("/portfolio/orders/7e1c2b3a/queue_position", fixture="synthetic/queue_position.json")
    c = make_client(t, prod_env)
    assert c.order("7e1c2b3a")["status"] == "executed" and c.order("7e1c2b3a")["exchange_index"] == 2
    assert c.queue_position("7e1c2b3a") == {"queue_position_fp": "0.00"}


@pytest.mark.parametrize("bad", ["", "batched", "queue_positions", "a/b", "a b", "../x", None, 5, "a?x=1"])
def test_order_ids_that_are_not_order_ids_are_refused(make_client, prod_env, bad):
    t = FakeTransport()
    c = make_client(t, prod_env)
    with pytest.raises(ValueError):
        c.order(bad)
    with pytest.raises(ValueError):
        c.queue_position(bad)
    assert t.calls == []


@pytest.mark.parametrize("method, kw", [
    ("orders", {"subaccount": 1}), ("orders", {"client_order_id": "x"}), ("orders", {"foo": 1}),
    ("fills", {"status": "resting"}), ("fills", {"subaccount": 0}), ("positions", {"min_ts": 1_791_226_000}),
    ("settlements", {"exchange_index": 2}), ("settlements", {"status": "settled"}),
])
def test_unsupported_query_parameters_are_refused_not_silently_dropped(make_client, prod_env, method, kw):
    t = FakeTransport()
    with pytest.raises(TypeError, match="unsupported parameters"):
        getattr(make_client(t, prod_env), method)(**kw)
    assert t.calls == []


@pytest.mark.parametrize("method, kw", [
    ("orders", {"limit": 0}), ("orders", {"limit": 1001}), ("orders", {"limit": True}), ("orders", {"limit": "5"}),
    ("orders", {"min_ts": 1_791_226_000_000}), ("fills", {"max_ts": 1_791_226_000_000}), ("orders", {"min_ts": 5}),
    ("orders", {"status": "open"}), ("orders", {"status": "RESTING"}), ("positions", {"settlement_status": "x"}),
    ("orders", {"exchange_index": -1}), ("fills", {"exchange_index": True}), ("fills", {"exchange_index": "2"}),
    ("orders", {"ticker": "a b"}), ("orders", {"event_ticker": "../x"}), ("fills", {"order_id": "a/b"}),
    ("orders", {"ticker": ""}),
])
def test_read_parameters_are_validated_before_sending(make_client, prod_env, method, kw):
    t = FakeTransport()
    with pytest.raises(ValueError):
        getattr(make_client(t, prod_env), method)(**kw)
    assert t.calls == []


def test_valid_read_parameter_edges_are_accepted(make_client, prod_env):
    t = FakeTransport()
    t.get("/portfolio/orders", {"orders": [], "cursor": ""})
    c = make_client(t, prod_env)
    c.orders(limit=1), c.orders(limit=1000), c.orders(status="canceled"), c.orders(status="executed")
    c.orders(exchange_index=0), c.orders(min_ts=1_000_000_000, max_ts=9_999_999_999)
    assert len(t.calls) == 6


# ================================================================================================ retries and errors (reads)
@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_stop_at_once_for_signed_and_public_reads(make_client, prod_env, fake_clock, status):
    t = FakeTransport()
    t.get("/portfolio/balance", status=status,
          text='{"error": {"code": "authentication_error", "message": "invalid signature"}}')
    c = make_client(t, prod_env)
    with pytest.raises(AuthError) as ei:
        c.balance(2)
    assert ei.value.status == status and ei.value.error["code"] == "authentication_error"
    assert len(t.calls) == 1 and fake_clock.sleeps == []                              # never loop on a 401
    assert isinstance(ei.value, ExecError)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 408, 425, 599])
def test_retryable_statuses_use_the_bounded_backoff_then_give_up(make_client, prod_env, fake_clock, status):
    t = FakeTransport()
    t.get("/portfolio/balance", status=status,
          text='{"error": {"code": "too_many_requests", "message": "too many requests"}}')
    c = make_client(t, prod_env)
    with pytest.raises(ApiError) as ei:
        c.balance(2)
    assert len(t.calls) == 5 and fake_clock.sleeps == [0.5, 1.0, 2.0, 4.0] == list(READ_BACKOFF_S)
    assert ei.value.status == status and "after 5 attempts" in str(ei.value)
    assert ei.value.error["code"] == "too_many_requests"


def test_a_read_that_recovers_returns_normally(make_client, prod_env, fake_clock):
    t = FakeTransport()
    t.get("/portfolio/balance", 429, 503, respond(fixture="synthetic/balance.json"))
    assert make_client(t, prod_env).balance(None)["balance"] == 2500
    assert len(t.calls) == 3 and fake_clock.sleeps == [0.5, 1.0]


def test_every_retry_is_signed_afresh(make_client, make_env, any_key, monkeypatch):
    ticks = iter(range(1_791_226_200, 1_791_226_300))
    monkeypatch.setattr(auth, "time", type("T", (), {"time": staticmethod(lambda: float(next(ticks)))}))
    t = FakeTransport()
    t.get("/portfolio/balance", 503, 503, 503, respond(fixture="synthetic/balance.json"))
    make_client(t, make_env("prod", any_key)).balance(None)
    stamps = [c.headers["KALSHI-ACCESS-TIMESTAMP"] for c in t.calls]
    assert stamps == [str((1_791_226_200 + i) * 1000) for i in range(4)]
    assert len({c.headers["KALSHI-ACCESS-SIGNATURE"] for c in t.calls}) == 4
    for call in t.calls:
        verify_signature(call, any_key)


@pytest.mark.parametrize("exc", [requests.ConnectionError("reset"), requests.Timeout("slow"), requests.ReadTimeout("r"),
                                 requests.exceptions.SSLError("tls"), ConnectionResetError("rst"), TimeoutError("t"),
                                 OSError("net down")])
def test_network_errors_are_retried_then_become_transport_errors(make_client, watch_env, fake_clock, exc):
    t = FakeTransport()
    t.get("/exchange/status", exc)
    with pytest.raises(TransportError) as ei:
        make_client(t, watch_env).exchange_status()
    assert len(t.calls) == 5 and fake_clock.sleeps == [0.5, 1.0, 2.0, 4.0]
    assert type(exc).__name__ in str(ei.value) and ei.value.__cause__ is exc


def test_a_network_error_that_clears_is_invisible(make_client, watch_env, fake_clock):
    t = FakeTransport()
    t.get("/exchange/status", requests.ConnectionError("blip"), respond(fixture="exchange_status_prod.json"))
    assert make_client(t, watch_env).exchange_status()["exchange_active"] is True
    assert len(t.calls) == 2 and fake_clock.sleeps == [0.5]


@pytest.mark.parametrize("status, fixture, code", [(404, "error_404_not_found.json", "not_found"),
                                                   (400, "synthetic/error_400_bad_request.json", "bad_request"),
                                                   (400, "synthetic/error_400_msg.json", None),
                                                   (422, "synthetic/error_400_bad_request.json", "bad_request")])
def test_client_errors_are_not_retried_and_carry_the_parsed_error(make_client, watch_env, fake_clock, status, fixture, code):
    t = FakeTransport()
    t.get(f"/markets/{TICKER}", status=status, text=respond(fixture=fixture).text)
    with pytest.raises(ApiError) as ei:
        make_client(t, watch_env).market(TICKER)
    assert len(t.calls) == 1 and fake_clock.sleeps == []
    assert ei.value.status == status and ei.value.error["code"] == code and ei.value.path.endswith(f"/markets/{TICKER}")


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirects_are_never_followed(make_client, make_env, ed25519_key, fake_clock, status):
    t = FakeTransport()
    t.get("/portfolio/orders", status=status, text="",
          headers={"Location": "https://api.elections.kalshi.com/trade-api/v2/portfolio/orders/"})
    c = make_client(t, make_env("prod", ed25519_key))
    with pytest.raises(RedirectError) as ei:
        c.orders(ticker=TICKER)
    assert ei.value.status == status and len(t.calls) == 1 and fake_clock.sleeps == []     # one request, no retry, no follow
    assert t.calls[0].host == "external-api.kalshi.com"


def test_a_non_json_success_body_is_an_api_error_and_is_not_retried(make_client, watch_env, fake_clock):
    t = FakeTransport()
    t.get("/exchange/status", text="<html>maintenance</html>")
    with pytest.raises(ApiError, match="non-JSON"):
        make_client(t, watch_env).exchange_status()
    assert len(t.calls) == 1 and fake_clock.sleeps == []
    t2 = FakeTransport()
    t2.get("/exchange/status", text="")
    with pytest.raises(ApiError, match="non-JSON"):
        make_client(t2, watch_env).exchange_status()


def test_unexpected_exceptions_from_the_transport_are_not_swallowed(make_client, watch_env, prod_env):
    """Only network errors are retried / downgraded; a bug (or the network guard) must surface."""
    class Boom(RuntimeError):
        pass

    t = FakeTransport()
    t.get("/exchange/status", Boom("bug"))
    with pytest.raises(Boom):
        make_client(t, watch_env).exchange_status()
    assert len(t.calls) == 1
    tw = FakeTransport()
    tw.post("/portfolio/events/orders", Boom("bug"))
    with pytest.raises(Boom):
        make_client(tw, prod_env, live=True).create_order(order_body())
    assert len(tw.calls) == 1


# ================================================================================================ the production transport
def real_response(body: bytes = b'{"ok": true}', status: int = 200, encoding: str | None = None) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = body                                                      # type: ignore[attr-defined]
    r.encoding = encoding
    return r


def test_the_production_transport_never_lets_requests_sniff_a_charset(monkeypatch):
    """JSON is UTF-8 by definition: an unknown encoding is set to utf-8 (no chardet guessing); a stated one is kept."""
    monkeypatch.setattr(requests.Session, "request", lambda self, method, url, **kw: real_response("é".encode()))
    resp = RequestsTransport().send("GET", "https://external-api.kalshi.com/trade-api/v2/x", params=None, data=None,
                                    headers={}, timeout=1.0)
    assert resp.encoding == "utf-8" and resp.text == "é"
    monkeypatch.setattr(requests.Session, "request",
                        lambda self, method, url, **kw: real_response(b"x", encoding="iso-8859-1"))
    kept = RequestsTransport().send("GET", "https://external-api.kalshi.com/trade-api/v2/x", params=None, data=None,
                                    headers={}, timeout=1.0)
    assert kept.encoding == "iso-8859-1"


def test_the_production_transport_is_https_only_and_never_follows_redirects(monkeypatch):
    seen = {}

    def fake_request(self, method, url, **kw):
        seen.update(method=method, url=url, **kw)
        return real_response()

    monkeypatch.setattr(requests.Session, "request", fake_request)
    t = RequestsTransport()
    s = t._session
    assert s.trust_env is False and s.max_redirects == 0
    assert list(s.adapters) == ["https://"]                              # no http:// adapter at all
    assert s.adapters["https://"].max_retries.total == 0                 # urllib3 never retries behind our back
    assert s.headers["User-Agent"] == "kpm-exec/0.1"
    resp = t.send("POST", "https://external-api.kalshi.com/trade-api/v2/portfolio/events/orders", params={"a": 1},
                  data='{"x":1}', headers={"H": "v"}, timeout=7.5)
    assert resp.json() == {"ok": True}
    assert seen == {"method": "POST", "url": "https://external-api.kalshi.com/trade-api/v2/portfolio/events/orders",
                    "params": {"a": 1}, "data": '{"x":1}', "headers": {"H": "v"}, "timeout": 7.5,
                    "allow_redirects": False}


def test_the_production_transport_cannot_use_plain_http(monkeypatch):
    t = RequestsTransport()
    s = t._session
    with pytest.raises(requests.exceptions.InvalidSchema):
        s.get_adapter("http://external-api.kalshi.com/trade-api/v2/exchange/status")
    assert s.get_adapter("https://external-api.kalshi.com/x") is s.adapters["https://"]


def test_the_default_transport_is_the_requests_transport(watch_env):
    assert isinstance(ExecClient(watch_env)._transport, RequestsTransport)


def test_the_client_refuses_to_send_outside_the_environments_host(make_client, prod_env, monkeypatch):
    """Even if the endpoint table were tampered with, the URL host must stay the env's allowlisted host."""
    t = FakeTransport()
    c = make_client(t, prod_env)
    monkeypatch.setattr(c, "_host", "api.elections.kalshi.com")             # simulate drift between env and client
    with pytest.raises(EndpointNotAllowed, match="outside the allowlisted host"):
        c.exchange_status()
    assert t.calls == []


# ================================================================================================ error parsing
@pytest.mark.parametrize("body, expected", [
    ('{"error": {"code": "not_found", "message": "not found"}}',
     {"code": "not_found", "message": "not found", "details": None, "shape": "error_object"}),
    ('{"error": {"code": "bad_request", "message": "bad request", "details": "invalid status filter"}}',
     {"code": "bad_request", "message": "bad request", "details": "invalid status filter", "shape": "error_object"}),
    ('{"error": {"code": "too_many_requests", "message": "too many requests"}}',
     {"code": "too_many_requests", "message": "too many requests", "details": None, "shape": "error_object"}),
    ('{"error": "too many requests"}',
     {"code": None, "message": "too many requests", "details": None, "shape": "error_string"}),
    ('{"msg": "Parameter validation failed for GetMarkets: limit"}',
     {"code": None, "message": "Parameter validation failed for GetMarkets: limit", "details": None, "shape": "msg"}),
    ('{"code": "invalid_order", "message": "bad", "details": "d"}',
     {"code": "invalid_order", "message": "bad", "details": "d", "shape": "flat"}),
    ("<html>502 Bad Gateway</html>", {"code": None, "message": "<html>502 Bad Gateway</html>", "details": None,
                                       "shape": "non_json"}),
    ("", {"code": None, "message": None, "details": None, "shape": "empty"}),
    ("   \n", {"code": None, "message": None, "details": None, "shape": "empty"}),
    (None, {"code": None, "message": None, "details": None, "shape": "empty"}),
    ("null", {"code": None, "message": None, "details": None, "shape": "empty"}),
])
def test_parse_error_handles_every_documented_and_observed_shape(body, expected):
    assert parse_error(body) == expected


def test_parse_error_accepts_bytes_and_already_parsed_json():
    assert parse_error(b'{"error": {"code": "x", "message": "m"}}')["code"] == "x"
    assert parse_error({"error": {"code": "y"}})["code"] == "y"
    assert parse_error(b"\xff\xfe not utf8")["shape"] == "non_json"
    assert parse_error([1, 2])["shape"] == "unknown" and parse_error({"other": 1})["shape"] == "unknown"
    assert parse_error({"error": {"code": 400, "message": ["a"]}})["code"] == "400"


def test_parse_error_on_the_real_captured_bodies(load_fixture):
    assert parse_error(json.dumps(load_fixture("error_404_not_found.json")))["code"] == "not_found"
    nested = parse_error(json.dumps(load_fixture("error_429_nested.json")))
    flat = parse_error(json.dumps(load_fixture("synthetic/error_429_flat.json")))
    assert nested["message"] == flat["message"] == "too many requests"                     # same message, two shapes
    assert parse_error(json.dumps(load_fixture("synthetic/error_400_post_only_cross.json"))) == {
        "code": "invalid order", "message": "invalid order", "details": "post only cross", "shape": "error_object"}
    assert parse_error(json.dumps(load_fixture("synthetic/error_409_duplicate.json")))["code"] == "order_already_exists"


def test_parse_error_truncates_long_text():
    big = "x" * 5000
    assert len(parse_error(big)["message"]) == 500
    assert len(parse_error(json.dumps({"error": {"code": "c", "message": big}}))["message"]) == 500


@pytest.mark.parametrize("status, outcome", [
    (200, "ok"), (201, "ok"), (202, "ok"), (204, "ok"), (299, "ok"),
    (400, "rejected"), (404, "rejected"), (405, "rejected"), (422, "rejected"), (451, "rejected"),
    (401, "auth"), (403, "auth"), (409, "duplicate"), (429, "throttled"),
    (500, "unknown"), (502, "unknown"), (503, "unknown"), (504, "unknown"), (599, "unknown"),
    (301, "unknown"), (302, "unknown"), (307, "unknown"), (308, "unknown"), (100, "unknown"), (199, "unknown"),
    (None, "unknown"),
])
def test_classify_table(status, outcome):
    assert classify(status) == outcome


def test_a_transport_exception_is_always_unknown():
    for exc in (requests.Timeout("t"), ConnectionError("c"), RuntimeError("r")):
        assert classify(None, exc=exc) == "unknown" and classify(200, exc=exc) == "unknown"


def test_result_validates_its_outcome_and_exposes_ok_and_order_id():
    for o in ("ok", "rejected", "duplicate", "throttled", "unknown", "auth"):
        assert Result(o, 200).outcome == o
    with pytest.raises(ValueError):
        Result("maybe", 200)
    r = Result("ok", 201, {"order_id": "abc", "x": 1})
    assert r.ok and r.order_id == "abc" and not Result("unknown", None).ok and Result("ok", 204, None).order_id is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.outcome = "rejected"                                                           # type: ignore[misc]
    assert client_mod.OUTCOMES == ("ok", "rejected", "duplicate", "throttled", "unknown", "auth")


def test_clock_and_sleep_are_injectable_and_default_to_real_time():
    from kpm.execution.env import HOSTS, Env
    c = ExecClient(Env("prod", HOSTS["prod"]["rest"], HOSTS["prod"]["ws"]), transport=FakeTransport())
    import time
    assert c._clock is time.time and c._sleep is time.sleep
    assert isinstance(FakeClock(1.0).time(), float)
