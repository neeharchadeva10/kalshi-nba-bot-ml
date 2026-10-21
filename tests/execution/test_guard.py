"""Self-tests for the shared infrastructure in conftest.py: network guard, credential isolation, FakeTransport."""
from __future__ import annotations

import os
import socket

import pytest
import requests

from kpm.core import config
from kpm.execution.client import ApiError

from .conftest import (FIXTURES_DIR, FakeClock, FakeResponse, FakeTransport, NetworkBlocked, UnmatchedRequest,
                       WriteAttempted, fixture_json, fixture_path, kalshi_date, respond)

PROD = "https://external-api.kalshi.com/trade-api/v2"


# ------------------------------------------------------------------------------------------------ network guard
def test_requests_calls_are_blocked_and_recorded(network_attempts):
    with pytest.raises(NetworkBlocked):
        requests.get(PROD + "/exchange/status", timeout=5)
    with pytest.raises(NetworkBlocked):
        requests.Session().post(PROD + "/portfolio/events/orders", data="{}")
    assert len(network_attempts) == 2 and "exchange/status" in network_attempts[0]
    network_attempts.clear()                         # the deliberate attempts must not fail this test at teardown


def test_non_loopback_sockets_are_blocked(network_attempts):
    with pytest.raises(NetworkBlocked):
        socket.create_connection(("example.com", 443), timeout=1)
    with pytest.raises(NetworkBlocked):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(NetworkBlocked):
        socket.gethostbyname("example.com")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkBlocked):
            s.connect(("93.184.216.34", 80))
        with pytest.raises(NetworkBlocked):
            s.connect_ex(("2606:2800:220:1:248:1893:25c8:1946", 80, 0, 0))
    finally:
        s.close()
    assert len(network_attempts) == 5
    network_attempts.clear()


def test_loopback_sockets_still_work(network_attempts):
    """asyncio's self-pipe and any local test server need loopback; only the outside world is blocked."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.create_connection(server.getsockname(), timeout=2)
    conn, _ = server.accept()
    client.sendall(b"ping")
    assert conn.recv(4) == b"ping"
    for s in (client, conn, server):
        s.close()
    assert not network_attempts


def test_default_exec_client_cannot_reach_the_network(watch_env, network_attempts):
    """Without an injected transport the client builds a real requests session; the guard stops it, loudly
    (NetworkBlocked is not an OSError, so the read-retry and 'unknown outcome' handlers cannot swallow it)."""
    from kpm.execution.client import ExecClient

    with pytest.raises(NetworkBlocked):
        ExecClient(watch_env).exchange_status()
    assert len(network_attempts) == 1                # one attempt, no retries
    network_attempts.clear()


# ------------------------------------------------------------------------------------------------ credentials
def test_kalshi_environment_variables_are_stripped_and_dotenv_is_off():
    assert not [n for n in os.environ if n.upper().startswith("KALSHI_")]
    for name in ("KALSHI_ENV", "KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PATH", "KALSHI_PROD_KEY_ID"):
        assert config.secret(name) is None           # even if the project's real .env defines them


def test_monkeypatched_variables_are_visible(monkeypatch):
    monkeypatch.setenv("KALSHI_ENV", "demo")
    assert config.secret("KALSHI_ENV") == "demo"


# ------------------------------------------------------------------------------------------------ fixtures
def test_fixture_loading_and_path_safety():
    assert fixture_json("series_KXBTC15M.json")["series"]["exchange_index"] == 2
    assert fixture_path("synthetic/balance.json").is_file()
    with pytest.raises(FileNotFoundError):
        fixture_path("nope.json")
    with pytest.raises(ValueError):
        fixture_path("../../../pyproject.toml")
    assert (FIXTURES_DIR / "MANIFEST.json").is_file()


def test_respond_builds_from_fixture_json_or_text():
    assert respond(201, fixture="synthetic/create_201_ioc_filled.json").json()["fill_count"] == "1.00"
    assert respond(json={"a": 1}).text == '{"a": 1}'
    assert respond(500, text="boom").text == "boom"
    r = respond(headers={"X-Thing": "1"})
    assert r.headers["x-thing"] == "1" and r.status_code == 200 and r.ok and r.content == b""


# ------------------------------------------------------------------------------------------------ clock
def test_fake_clock_sleep_advances_and_records():
    c = FakeClock(100.0)
    c.sleep(0.5)
    c.advance(2)
    assert c.time() == 102.5 and c() == 102.5 and c.sleeps == [0.5]


def test_kalshi_date_truncates_to_whole_seconds():
    assert kalshi_date(1_791_226_212.9) == "Mon, 05 Oct 2026 18:50:12 GMT"


# ------------------------------------------------------------------------------------------------ FakeTransport
def send(t: FakeTransport, method="GET", endpoint="/exchange/status", params=None, data=None, headers=None):
    return t.send(method, PROD + endpoint, params=params, data=data, headers=headers or {}, timeout=5.0)


def test_routes_match_on_method_endpoint_and_params():
    t = FakeTransport(host="external-api.kalshi.com")
    t.get("/portfolio/balance", {"who": "any"})
    t.get("/portfolio/balance", {"who": "shard2"}, params={"exchange_index": 2})
    t.get("/markets", {"who": "predicate"}, params=lambda p: p is not None and p.get("limit") == 5)
    assert send(t, endpoint="/portfolio/balance").json() == {"who": "any"}
    assert send(t, endpoint="/portfolio/balance", params={"exchange_index": 2}).json() == {"who": "shard2"}
    assert send(t, endpoint="/portfolio/balance", params={"exchange_index": "2"}).json() == {"who": "shard2"}
    assert send(t, endpoint="/portfolio/balance", params={"exchange_index": 0}).json() == {"who": "any"}
    assert send(t, endpoint="/markets", params={"limit": 5}).json() == {"who": "predicate"}
    with pytest.raises(UnmatchedRequest):
        send(t, endpoint="/markets", params={"limit": 6})
    with pytest.raises(UnmatchedRequest):
        send(t, method="POST", endpoint="/portfolio/balance")
    assert len(t.calls) == 7                          # every attempt is recorded, matched or not


def test_latest_route_wins_and_full_prefix_is_accepted():
    t = FakeTransport()
    t.get("/trade-api/v2/exchange/status", {"v": 1})
    t.get("/exchange/status", {"v": 2})
    assert send(t).json() == {"v": 2}


def test_reply_sequences_repeat_the_last_unless_told_not_to():
    t = FakeTransport()
    r = t.get("/exchange/status", 429, 429, {"ok": True})
    assert [send(t).status_code for _ in range(2)] == [429, 429]
    assert send(t).json() == {"ok": True} and send(t).json() == {"ok": True}
    assert len(r.calls) == 4
    once = t.get("/markets", {"x": 1}, repeat=False)
    send(t, endpoint="/markets")
    with pytest.raises(UnmatchedRequest):
        send(t, endpoint="/markets")
    assert len(once.calls) == 2


def test_reply_forms():
    t = FakeTransport()
    t.get("/a", (503, {"error": "x"}))
    t.get("/b", (200, "raw text"))
    t.get("/c", requests.ConnectionError("reset"))
    t.get("/d", requests.Timeout)
    t.get("/e", lambda call: (200, {"echo": call.endpoint}))
    t.get("/f", status=418, text="teapot", headers={"X-Y": "z"})
    assert send(t, endpoint="/a").status_code == 503
    assert send(t, endpoint="/b").text == "raw text"
    with pytest.raises(requests.ConnectionError):
        send(t, endpoint="/c")
    with pytest.raises(requests.Timeout):
        send(t, endpoint="/d")
    assert send(t, endpoint="/e").json() == {"echo": "/e"}
    f = send(t, endpoint="/f")
    assert (f.status_code, f.text, f.headers["x-y"]) == (418, "teapot", "z")
    with pytest.raises(TypeError):
        t.get("/g", object())
        send(t, endpoint="/g")


def test_calls_are_recorded_with_parsed_body_and_presign_text():
    t = FakeTransport()
    t.post("/portfolio/events/orders", 201)
    hdr = {"KALSHI-ACCESS-KEY": "k", "KALSHI-ACCESS-TIMESTAMP": "1703123456789", "KALSHI-ACCESS-SIGNATURE": "s"}
    send(t, "POST", "/portfolio/events/orders", data='{"a":1}', headers=hdr)
    (c,) = t.writes
    assert c.body == {"a": 1} and c.endpoint == "/portfolio/events/orders" and c.is_write and c.signed
    assert c.path == "/trade-api/v2/portfolio/events/orders" and c.host == "external-api.kalshi.com"
    assert c.presign_text() == "1703123456789POST/trade-api/v2/portfolio/events/orders"
    assert t.reads == [] and t.calls_to("post", "/portfolio/events/orders") == [c]
    with pytest.raises(AssertionError):
        t.assert_no_writes()


def test_forbid_writes_raises_and_still_records():
    t = FakeTransport(forbid_writes=True)
    t.post("/portfolio/events/orders", 201)
    t.get("/exchange/status", {})
    send(t)
    with pytest.raises(WriteAttempted):
        send(t, "POST", "/portfolio/events/orders", data="{}")
    with pytest.raises(WriteAttempted):
        send(t, "DELETE", "/portfolio/events/orders")
    assert len(t.writes) == 2


def test_host_and_scheme_are_pinned():
    t = FakeTransport(host="external-api.demo.kalshi.co")
    t.get("/exchange/status", {})
    with pytest.raises(UnmatchedRequest):
        send(t)                                      # prod host against a demo-pinned transport
    t2 = FakeTransport()
    t2.get("/exchange/status", {})
    with pytest.raises(UnmatchedRequest):
        t2.send("GET", "http://external-api.kalshi.com/trade-api/v2/exchange/status", params=None, data=None,
                headers={}, timeout=1.0)


def test_server_clock_adds_a_date_header_at_the_round_trip_midpoint():
    clock = FakeClock(1_791_226_200.0)
    t = FakeTransport(clock=clock, server_clock=lambda: clock.time() - 3.0, latency_s=0.4)
    t.get("/exchange/status", {})
    resp = send(t)
    assert resp.headers["Date"] == kalshi_date(1_791_226_200.2 - 3.0)      # midpoint minus the 3 s offset
    assert clock.time() == pytest.approx(1_791_226_200.4, abs=1e-6)         # abs: a bare approx is +-1800 s at epoch scale
    t.get("/other", respond(headers={"Date": "Mon, 01 Jan 2001 00:00:00 GMT"}))
    assert send(t, endpoint="/other").headers["Date"] == "Mon, 01 Jan 2001 00:00:00 GMT"      # an explicit Date wins


def test_make_client_pins_host_and_uses_the_fake_clock(make_client, fake_clock, watch_env):
    c = make_client()
    c.transport.get("/exchange/status", {"exchange_active": True})
    assert c.exchange_status() == {"exchange_active": True}
    assert c.transport.calls[0].host == "external-api.kalshi.com"
    c2 = make_client(env=watch_env)
    c2.transport.get("/exchange/status", 503, 503, 503, 503, 503)
    with pytest.raises(ApiError, match="after 5 attempts"):
        c2.exchange_status()
    assert fake_clock.sleeps == [0.5, 1.0, 2.0, 4.0]                       # backoff happened on the fake clock


def test_fake_response_is_a_response_shape():
    r = FakeResponse(200, '{"a": 1}', {"Content-Type": "application/json"})
    assert r.headers["content-type"] == "application/json" and r.json() == {"a": 1}
