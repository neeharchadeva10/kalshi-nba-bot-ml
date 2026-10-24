"""The interface contract the other execution modules were written against (names, argument order, return shapes).

If one of these fails, a caller elsewhere in kpm.execution (safety, ledger, reconcile, the bots, the CLI) is about to break.
"""
from __future__ import annotations

import dataclasses
import inspect
from decimal import Decimal

import pytest

from kpm.execution import auth, client, env, units

D = Decimal


def params(fn) -> list[str]:
    return list(inspect.signature(fn).parameters)


def kinds(fn) -> dict[str, str]:
    return {n: p.kind.name for n, p in inspect.signature(fn).parameters.items()}


# ------------------------------------------------------------------------------------------------ units
@pytest.mark.parametrize("fn, expected", [
    (units.dec, ["s"]), (units.fmt_price, ["p"]), (units.fmt_count, ["c"]),
    (units.to_book, ["outcome", "outcome_price"]), (units.collateral, ["side", "yes_price", "count"]),
    (units.taker_fee, ["count", "outcome_price", "multiplier"]),
    (units.worst_fee, ["count", "yes_price", "multiplier", "grid"]),
    (units.on_grid, ["price", "price_ranges"]), (units.best_bid, ["levels"]),
    (units.best_yes_ask, ["no_levels"]), (units.best_no_ask, ["yes_levels"]),
    (units.from_cents, ["v"]), (units.unix_seconds, ["ts"]), (units.parse_rfc3339, ["s"]),
])
def test_units_signatures(fn, expected):
    assert params(fn) == expected


def test_units_return_types():
    assert units.to_book("no", D("0.01")) == ("ask", D("0.99"))
    assert units.fmt_price(D("0.01")) == "0.0100" and units.fmt_count(D("25")) == "25.00"
    assert isinstance(units.taker_fee(D(1), D("0.5")), Decimal) and isinstance(units.worst_fee(D(1), D("0.5")), Decimal)
    assert isinstance(units.collateral("ask", D("0.99"), D(25)), Decimal)
    assert isinstance(units.from_cents(2500), Decimal) and isinstance(units.parse_rfc3339("2026-10-05T18:30:00Z"), float)
    assert units.best_bid([["0.1", "1"], ["0.2", "1"]]) == D("0.2") and units.best_bid([]) is None
    assert units.best_yes_ask([["0.4", "1"]]) == D("0.6") and units.best_no_ask([["0.4", "1"]]) == D("0.6")
    assert units.unix_seconds(1_791_226_200) == 1_791_226_200


# ------------------------------------------------------------------------------------------------ auth
def test_auth_surface():
    assert params(auth.load_private_key) == ["pem_path", "password"]
    assert params(auth.sign_text) == ["key", "text"]
    assert params(auth.sign_path) == ["base_url", "endpoint"]
    assert params(auth.auth_headers)[:4] == ["key", "key_id", "method", "path"]
    assert auth.WS_SIGN_PATH == "/trade-api/ws/v2"


# ------------------------------------------------------------------------------------------------ env
def test_env_surface():
    fields = [f.name for f in dataclasses.fields(env.Env)]
    assert fields[:6] == ["name", "rest_base", "ws_url", "key_id", "key", "key_fp"]          # positional order
    assert params(env.load_env)[:1] == ["require_keys"]
    assert set(env.HOSTS) == {"demo", "prod"}
    assert env.HOSTS["prod"]["rest"] == "https://external-api.kalshi.com/trade-api/v2"
    e = env.Env("prod", env.HOSTS["prod"]["rest"], env.HOSTS["prod"]["ws"])
    assert (e.key_id, e.key, e.key_fp) == (None, None, None)


# ------------------------------------------------------------------------------------------------ client
def test_exec_client_constructor():
    sig = inspect.signature(client.ExecClient.__init__)
    assert list(sig.parameters)[:2] == ["self", "env"]
    for name, default in (("live", False), ("transport", None), ("audit", None)):
        p = sig.parameters[name]
        assert p.kind.name == "KEYWORD_ONLY" and p.default == default, name


@pytest.mark.parametrize("method, expected", [
    ("exchange_status", []), ("series", ["series_ticker"]), ("open_markets", ["series_ticker"]), ("market", ["ticker"]),
    ("orderbook", ["ticker", "depth"]), ("server_time_skew", ["probes", "spacing_s"]), ("account_limits", []),
    ("balance", ["exchange_index"]), ("create_order", ["body"]), ("batch_create", ["bodies"]),
    ("cancel_order", ["order_id", "exchange_index"]), ("cancel_all", []),
])
def test_exec_client_methods(method, expected):
    fn = getattr(client.ExecClient, method)
    assert params(fn)[1:len(expected) + 1] == expected


def test_decrease_order_and_the_listing_reads_exist():
    assert params(client.ExecClient.decrease_order)[:3] == ["self", "order_id", "exchange_index"]
    for name in ("positions", "orders", "fills", "settlements", "decrease_order", "amend_order", "batch_cancel",
                 "user_data_timestamp", "historical_cutoff", "queue_position", "order", "shard_cash", "clock_probe",
                 "close"):
        assert callable(getattr(client.ExecClient, name)), name


def test_result_fields_and_outcomes():
    names = [f.name for f in dataclasses.fields(client.Result)]
    assert names[:4] == ["outcome", "status", "data", "error"]
    assert client.OUTCOMES == ("ok", "rejected", "duplicate", "throttled", "unknown", "auth")
    r = client.Result("ok", 201, {"order_id": "x"}, None)
    assert (r.outcome, r.status, r.data, r.error) == ("ok", 201, {"order_id": "x"}, None)


def test_exception_types_callers_catch():
    assert issubclass(client.AuthError, client.ExecError) and issubclass(client.ApiError, client.ExecError)
    assert issubclass(client.TransportError, client.ExecError) and issubclass(client.RedirectError, client.ExecError)
    assert issubclass(client.EndpointNotAllowed, PermissionError)                  # the not-live write error is PermissionError
    assert issubclass(client.ExecError, RuntimeError)


def test_orderbook_shape(make_client, watch_env):
    from .conftest import FakeTransport
    t = FakeTransport()
    t.get("/markets/KX-1/orderbook", {"orderbook_fp": {"yes_dollars": [["0.40", "3.00"], ["0.45", "1.00"]],
                                                       "no_dollars": [["0.50", "2.00"]]}})
    book = make_client(t, watch_env).orderbook("KX-1")
    assert set(book) == {"ticker", "yes", "no", "fetched_at"}
    assert book["yes"] == [(D("0.40"), D("3.00")), (D("0.45"), D("1.00"))] and book["no"] == [(D("0.50"), D("2.00"))]
    assert units.best_yes_ask(book["no"]) == D("0.50") and units.best_no_ask(book["yes"]) == D("0.55")


def test_server_time_skew_returns_a_float_of_seconds(make_client, watch_env, fake_clock):
    from .conftest import FakeTransport
    t = FakeTransport(clock=fake_clock, server_clock=lambda: fake_clock.time() - 1.0)
    t.get("/exchange/status", {})
    skew = make_client(t, watch_env).server_time_skew()
    assert isinstance(skew, float) and 0.4 < skew < 1.6                            # local - server: positive = PC ahead
