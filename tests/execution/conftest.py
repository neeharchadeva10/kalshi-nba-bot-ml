"""Shared test infrastructure for tests/execution. Everything here is offline and credential-free.

Autouse guards (they apply to every test in this directory):

* ``_no_network``: any ``requests`` call and any socket connection to a non-loopback address raises
  :class:`NetworkBlocked`, and the test also FAILS at teardown if an attempt was made (even when the code under
  test swallowed the exception). A test that triggers the guard on purpose must ``network_attempts.clear()``.
* ``_no_credentials``: every ``KALSHI_*`` variable is removed from the environment and ``.env`` loading is
  disabled, so no test can read the real key id, the real key file or the real KALSHI_ENV.

Helpers (use them from tests with ``from .conftest import FakeTransport, FakeResponse, respond, FakeClock``, or
through the fixtures ``fake_clock``, ``make_env``, ``make_client``, ``prod_env``, ``demo_env``, ``watch_env``,
``load_fixture``):

* :class:`FakeTransport` replays recorded responses keyed by (method, endpoint, params) and records every call. It
  is the ``transport=`` argument of ``ExecClient``. ``forbid_writes=True`` makes any non-GET raise
  :class:`WriteAttempted` (the gate tests use it: "no write is sent, the transport raises if one is tried").
* :class:`FakeClock` is a manual clock whose ``sleep`` advances time and records the delays (no real sleeping).
* Fixtures live in ``tests/fixtures/kalshi_exec/``; ``synthetic/`` ones are shaped from the OpenAPI schemas.
* Throwaway Ed25519 / RSA keys are generated per session and never written anywhere but ``tmp_path``.
"""
from __future__ import annotations

import base64
import ipaddress
import json as _json
import math
import os
import socket
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from email.utils import formatdate
from pathlib import Path
from typing import Any

import pytest
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from requests.structures import CaseInsensitiveDict

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "kalshi_exec"
API_PREFIX = "/trade-api/v2"
TEST_KEY_ID = "11111111-2222-4333-8444-555555555555"        # synthetic; never a real key id
CLOCK_START = 1_791_226_200.0                                 # 2026-10-05 ~18:50 UTC
TICKER = "KXBTC15M-26OCT051430-30"                            # a real captured KXBTC15M market (exchange_index 2)
COID = "c0ffee00-0000-4000-8000-00000000000a"                 # the client_order_id of the synthetic create fixtures


# ============================================================================================ network guard
class NetworkBlocked(AssertionError):
    """A test tried to reach the network (or a non-loopback address)."""


_ATTEMPTS: list[str] = []
_LOOPBACK_NAMES = {"localhost", "ip6-localhost", ""}


def _is_loopback(host: Any) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = str(host)
    if host.lower() in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _blocked(what: str, target: Any) -> NetworkBlocked:
    msg = f"{what} -> {target!r}"
    _ATTEMPTS.append(msg)
    return NetworkBlocked(f"network access is blocked in tests/execution: {msg}")


def _install_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create_connection = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    def check_address(what: str, address: Any) -> None:
        if isinstance(address, (str, bytes)):                  # AF_UNIX path
            return
        host = address[0] if isinstance(address, tuple) and address else address
        if not _is_loopback(host):
            raise _blocked(what, address)

    def connect(self, address):                                # type: ignore[no-untyped-def]
        check_address("socket.connect", address)
        return real_connect(self, address)

    def connect_ex(self, address):                             # type: ignore[no-untyped-def]
        check_address("socket.connect_ex", address)
        return real_connect_ex(self, address)

    def create_connection(address, *args, **kwargs):           # type: ignore[no-untyped-def]
        check_address("socket.create_connection", address)
        return real_create_connection(address, *args, **kwargs)

    def getaddrinfo(host, *args, **kwargs):                    # type: ignore[no-untyped-def]
        if not _is_loopback(host):
            raise _blocked("socket.getaddrinfo", host)
        return real_getaddrinfo(host, *args, **kwargs)

    def gethostbyname(host):                                   # type: ignore[no-untyped-def]
        raise _blocked("socket.gethostbyname", host)

    def requests_request(self, method, url, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise _blocked(f"requests.Session.request {str(method).upper()}", url)

    def adapter_send(self, request, *args, **kwargs):          # type: ignore[no-untyped-def]
        raise _blocked(f"requests HTTPAdapter.send {request.method}", request.url)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "gethostbyname", gethostbyname)
    monkeypatch.setattr(socket, "gethostbyname_ex", gethostbyname)
    monkeypatch.setattr(requests.Session, "request", requests_request)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", adapter_send)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch):
    _ATTEMPTS.clear()
    _install_guard(monkeypatch)
    yield
    attempts = list(_ATTEMPTS)
    _ATTEMPTS.clear()
    assert not attempts, f"the test attempted network access: {attempts}"


@pytest.fixture
def network_attempts() -> list[str]:
    """The guard's record of blocked attempts. Clear it after a test that triggers the guard deliberately."""
    return _ATTEMPTS


@pytest.fixture(autouse=True)
def _no_credentials(monkeypatch: pytest.MonkeyPatch):
    """No test may see the real KALSHI_* variables or read the real .env."""
    for name in [n for n in os.environ if n.upper().startswith("KALSHI_")]:
        monkeypatch.delenv(name, raising=False)
    from kpm.core import config
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: False)
    yield


# ============================================================================================ fixtures on disk
def fixture_path(name: str) -> Path:
    p = (FIXTURES_DIR / name).resolve()
    if FIXTURES_DIR.resolve() not in p.parents:
        raise ValueError(f"fixture {name!r} is outside {FIXTURES_DIR}")
    if not p.is_file():
        raise FileNotFoundError(f"no fixture {name!r} in {FIXTURES_DIR}")
    return p


def fixture_text(name: str) -> str:
    return fixture_path(name).read_text(encoding="utf-8")


def fixture_json(name: str) -> Any:
    return _json.loads(fixture_text(name))


@pytest.fixture
def load_fixture() -> Callable[[str], Any]:
    """``load_fixture("synthetic/balance.json")`` -> parsed JSON from tests/fixtures/kalshi_exec/."""
    return fixture_json


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


# ============================================================================================ clock
class FakeClock:
    """A manual clock. ``clock.time`` / ``clock()`` read it; ``clock.sleep(s)`` advances it and records ``s``."""

    def __init__(self, start: float = CLOCK_START):
        self.now = float(start)
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    __call__ = time

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))
        self.now += float(seconds)


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


def kalshi_date(ts: float) -> str:
    """An HTTP ``Date`` header for Unix time ``ts`` (1 s resolution, truncated like a real server)."""
    return formatdate(math.floor(ts), usegmt=True)


# ============================================================================================ fake transport
class UnmatchedRequest(AssertionError):
    """The client sent a request that no route of the FakeTransport covers."""


class WriteAttempted(AssertionError):
    """A write (non-GET) reached a transport that forbids writes."""


class _Unset:
    def __repr__(self) -> str:
        return "<unset>"


_UNSET = _Unset()


class FakeResponse:
    """The slice of ``requests.Response`` that ExecClient reads: status_code, text, headers."""

    def __init__(self, status_code: int = 200, text: str = "", headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.text = text
        self.headers = CaseInsensitiveDict(headers or {})

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self) -> Any:
        return _json.loads(self.text)

    def __repr__(self) -> str:
        return f"FakeResponse({self.status_code}, {self.text[:60]!r})"


def respond(status: int = 200, *, json: Any = _UNSET, text: str | None = None, fixture: str | None = None,
            headers: dict[str, str] | None = None) -> FakeResponse:
    """Build a response from a fixture file, a JSON value or raw text (``respond(201, fixture="synthetic/x.json")``)."""
    if fixture is not None:
        body = fixture_text(fixture)
    elif json is not _UNSET:
        body = _json.dumps(json)
    else:
        body = text or ""
    return FakeResponse(status, body, headers)


@dataclass(frozen=True)
class Call:
    """One request as the transport received it (headers include the signature, exactly like the real server)."""

    index: int
    method: str
    url: str
    endpoint: str                       # relative to the REST base: "/portfolio/balance"
    path: str                           # the signed path: "/trade-api/v2/portfolio/balance"
    host: str
    params: dict[str, Any] | None
    data: str | None
    body: Any                           # json.loads(data) or None
    headers: dict[str, str]
    timeout: float | None

    @property
    def is_write(self) -> bool:
        return self.method != "GET"

    @property
    def signed(self) -> bool:
        return "KALSHI-ACCESS-KEY" in self.headers

    def presign_text(self) -> str:
        """What the client signed: timestamp + METHOD + path (no query string, no body)."""
        return self.headers["KALSHI-ACCESS-TIMESTAMP"] + self.method + self.path


def _params_match(spec: Any, actual: dict[str, Any] | None) -> bool:
    if spec is None:
        return True
    if callable(spec):
        return bool(spec(actual))
    want = {str(k): str(v) for k, v in spec.items()}
    have = {str(k): str(v) for k, v in (actual or {}).items()}
    return want == have


@dataclass
class Route:
    method: str
    endpoint: str
    params: Any
    replies: list[Any]
    repeat: bool = True
    calls: list[Call] = field(default_factory=list)

    def next_reply(self, call: Call) -> Any:
        n = len(self.calls)
        self.calls.append(call)
        if n < len(self.replies):
            reply = self.replies[n]
        elif self.repeat:
            reply = self.replies[-1]
        else:
            raise UnmatchedRequest(f"{self.method} {self.endpoint}: all {len(self.replies)} scripted replies were used")
        return reply(call) if callable(reply) and not isinstance(reply, (BaseException, type)) else reply


def _as_exception(reply: Any) -> BaseException | None:
    if isinstance(reply, BaseException):
        return reply
    if isinstance(reply, type) and issubclass(reply, BaseException):
        return reply("scripted transport failure")
    return None


def _as_response(reply: Any) -> FakeResponse:
    if isinstance(reply, FakeResponse):
        return reply
    if isinstance(reply, bool):
        raise TypeError("a bool is not a reply")
    if isinstance(reply, int):
        return FakeResponse(reply)
    if isinstance(reply, tuple) and len(reply) == 2 and isinstance(reply[0], int):
        status, body = reply
        if isinstance(body, str):
            return FakeResponse(status, body)
        return FakeResponse(status, "" if body is None else _json.dumps(body))
    if isinstance(reply, (dict, list)):
        return FakeResponse(200, _json.dumps(reply))
    if isinstance(reply, str):
        return FakeResponse(200, reply)
    raise TypeError(f"cannot turn {reply!r} into a response")


class FakeTransport:
    """Replays scripted responses and records every call. The ``transport=`` argument of ``ExecClient``.

    ``route(method, endpoint, *replies, params=None, ...)`` registers replies for a request. ``endpoint`` is
    relative to the REST base (``"/markets/KX-1/orderbook"``); ``params=None`` matches any query, a dict must
    match exactly (values compared as strings), a callable receives the params. The LATEST matching route wins.
    Each call to a route consumes the next reply; the last one repeats unless ``repeat=False``. A reply is:

    * a :class:`FakeResponse` / ``respond(...)``, an ``int`` status, ``(status, body)``, a dict/list (200 JSON)
      or a str (200 text);
    * an exception instance or class: raised from ``send`` (``requests.ConnectionError(...)``, ``requests.Timeout``);
    * a callable ``f(call)`` returning any of the above.

    ``route(..., status=201, fixture="synthetic/x.json")`` / ``json=...`` / ``text=...`` is shorthand for one reply.

    Optional realism for clock tests: ``server_clock`` (a callable returning the server's Unix time) adds a ``Date``
    header to every reply that lacks one; ``clock`` + ``latency_s`` make each call advance that FakeClock by the
    round-trip time (the Date is taken at the midpoint).
    """

    def __init__(self, *, forbid_writes: bool = False, host: str | None = None, clock: FakeClock | None = None,
                 server_clock: Callable[[], float] | None = None, latency_s: float = 0.0):
        self.forbid_writes = forbid_writes
        self.host = host
        self.clock = clock
        self.server_clock = server_clock
        self.latency_s = latency_s
        self.calls: list[Call] = []
        self.routes: list[Route] = []

    # -------------------------------------------------------------------------- scripting
    def route(self, method: str, endpoint: str, *replies: Any, params: Any = None, status: int = 200,
              json: Any = _UNSET, text: str | None = None, fixture: str | None = None,
              headers: dict[str, str] | None = None, repeat: bool = True) -> Route:
        scripted = list(replies)
        if fixture is not None or json is not _UNSET or text is not None:
            scripted.append(respond(status, json=json, text=text, fixture=fixture, headers=headers))
        if not scripted:
            scripted.append(respond(status, headers=headers))
        if endpoint.startswith(API_PREFIX):
            endpoint = endpoint[len(API_PREFIX):]
        r = Route(method.upper(), endpoint, params, scripted, repeat)
        self.routes.append(r)
        return r

    def get(self, endpoint: str, *replies: Any, **kw: Any) -> Route:
        return self.route("GET", endpoint, *replies, **kw)

    def post(self, endpoint: str, *replies: Any, **kw: Any) -> Route:
        return self.route("POST", endpoint, *replies, **kw)

    def delete(self, endpoint: str, *replies: Any, **kw: Any) -> Route:
        return self.route("DELETE", endpoint, *replies, **kw)

    # -------------------------------------------------------------------------- inspection
    @property
    def writes(self) -> list[Call]:
        return [c for c in self.calls if c.is_write]

    @property
    def reads(self) -> list[Call]:
        return [c for c in self.calls if not c.is_write]

    def calls_to(self, method: str, endpoint: str) -> list[Call]:
        return [c for c in self.calls if c.method == method.upper() and c.endpoint == endpoint]

    def assert_no_writes(self) -> None:
        assert not self.writes, f"unexpected writes: {[(c.method, c.endpoint) for c in self.writes]}"

    # -------------------------------------------------------------------------- the Transport protocol
    def send(self, method: str, url: str, *, params: dict | None, data: str | None,
             headers: dict[str, str], timeout: float) -> FakeResponse:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path
        endpoint = path[len(API_PREFIX):] if path.startswith(API_PREFIX) else path
        try:
            body = _json.loads(data) if data else None
        except ValueError:
            body = data
        call = Call(len(self.calls), method.upper(), url, endpoint, path, parsed.netloc,
                    dict(params) if params else None, data, body, dict(headers), timeout)
        self.calls.append(call)
        if self.host is not None and parsed.netloc != self.host:
            raise UnmatchedRequest(f"request to host {parsed.netloc!r}, expected {self.host!r}")
        if parsed.scheme != "https":
            raise UnmatchedRequest(f"request over {parsed.scheme!r}, expected https")
        if self.forbid_writes and call.is_write:
            raise WriteAttempted(f"a write reached the transport: {call.method} {call.endpoint}")
        route = next((r for r in reversed(self.routes)
                      if r.method == call.method and r.endpoint == endpoint and _params_match(r.params, call.params)),
                     None)
        if route is None:
            raise UnmatchedRequest(f"no route for {call.method} {call.endpoint} params={call.params}")
        reply = route.next_reply(call)
        if self.clock is not None and self.latency_s:
            self.clock.advance(self.latency_s / 2)
        exc = _as_exception(reply)
        if exc is not None:
            if self.clock is not None and self.latency_s:
                self.clock.advance(self.latency_s / 2)
            raise exc
        resp = _as_response(reply)
        if self.server_clock is not None and "Date" not in resp.headers:
            resp.headers["Date"] = kalshi_date(self.server_clock())
        if self.clock is not None and self.latency_s:
            self.clock.advance(self.latency_s / 2)
        return resp


# ============================================================================================ keys and environments
def pem_bytes(key: Any, fmt: str = "pkcs8", password: bytes | None = None) -> bytes:
    """Serialize a throwaway private key: ``pkcs8`` (BEGIN PRIVATE KEY), ``pkcs1`` (BEGIN RSA PRIVATE KEY, RSA only)."""
    encryption: Any = serialization.NoEncryption()
    if password is not None:
        encryption = serialization.BestAvailableEncryption(password)
    layout = {"pkcs8": serialization.PrivateFormat.PKCS8,
              "pkcs1": serialization.PrivateFormat.TraditionalOpenSSL}[fmt]
    return key.private_bytes(serialization.Encoding.PEM, layout, encryption)


@pytest.fixture(scope="session")
def ed25519_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture(scope="session")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(params=["ed25519", "rsa"])
def any_key(request: pytest.FixtureRequest, ed25519_key: Ed25519PrivateKey, rsa_key: rsa.RSAPrivateKey) -> Any:
    """Runs the test once with an Ed25519 key and once with an RSA key."""
    return ed25519_key if request.param == "ed25519" else rsa_key


def verify_signature(call: Call, key: Any) -> None:
    """Raise ``InvalidSignature`` unless the call's KALSHI-ACCESS-SIGNATURE verifies, with the PUBLIC half of ``key``,
    over timestamp + METHOD + signed path (the design's pre-sign text; Ed25519 direct, RSA-PSS with a 32-byte salt)."""
    sig = base64.b64decode(call.headers["KALSHI-ACCESS-SIGNATURE"], validate=True)
    msg = call.presign_text().encode("utf-8")
    pub = key.public_key()
    if isinstance(key, Ed25519PrivateKey):
        pub.verify(sig, msg)
    else:
        pub.verify(sig, msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())


def order_body(**override: Any) -> dict[str, Any]:
    """A valid CreateOrderV2 body: an IOC taker buy of 1 YES at 0.56 on a KXBTC15M market (shard 2)."""
    body: dict[str, Any] = {"ticker": TICKER, "client_order_id": COID, "side": "bid", "count": "1.00",
                            "price": "0.5600", "time_in_force": "immediate_or_cancel",
                            "self_trade_prevention_type": "taker_at_cross", "exchange_index": 2}
    body.update(override)
    return body


@pytest.fixture
def make_env() -> Callable[..., Any]:
    """``make_env("prod", key)`` -> an Env on the allowlisted hosts with the synthetic key id (``key=None``: no keys)."""
    from kpm.execution.env import HOSTS, Env

    def factory(name: str = "prod", key: Any = None, key_id: str = TEST_KEY_ID) -> Any:
        hosts = HOSTS[name]
        if key is None:
            return Env(name, hosts["rest"], hosts["ws"])
        return Env(name, hosts["rest"], hosts["ws"], key_id=key_id, key=key)

    return factory


@pytest.fixture
def prod_env(make_env, ed25519_key):
    return make_env("prod", ed25519_key)


@pytest.fixture
def demo_env(make_env, ed25519_key):
    return make_env("demo", ed25519_key)


@pytest.fixture
def watch_env(make_env):
    """Production hosts, no keys: watch-only mode."""
    return make_env("prod", None)


@pytest.fixture
def make_client(fake_clock, prod_env) -> Callable[..., Any]:
    """``make_client(transport=None, env=None, live=False, audit=None, **kw)`` -> ExecClient on the FakeClock.

    The default transport is a FakeTransport pinned to the env's REST host; read it back from ``client.transport``.
    Retries and backoff use ``fake_clock.sleep`` (recorded in ``fake_clock.sleeps``), so tests never really wait.
    """
    from kpm.execution.client import ExecClient

    def factory(transport: Any = None, env: Any = None, *, live: bool = False, audit: Any = None, **kw: Any):
        env = env if env is not None else prod_env
        if transport is None:
            transport = FakeTransport(host=env.rest_host)
        kw.setdefault("clock", fake_clock.time)
        kw.setdefault("sleep", fake_clock.sleep)
        client = ExecClient(env, live=live, transport=transport, audit=audit, **kw)
        client.transport = transport                       # type: ignore[attr-defined]
        return client

    return factory
