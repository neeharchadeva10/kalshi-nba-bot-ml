"""The audit trail of kpm.execution.client (design 5.7): one row per HTTP attempt, explicit fields, no secrets."""
from __future__ import annotations

import json
import logging
import re

import pytest
import requests

from kpm.execution.client import RESPONSE_AUDIT_LIMIT, ExecClient
from kpm.execution.env import load_env

from .conftest import COID, TICKER, FakeTransport, order_body, pem_bytes, respond

CREATE = "/portfolio/events/orders"
FIELDS = {"env", "live", "ts_ms", "method", "path", "params", "body", "client_order_id", "attempt", "kind", "status",
          "response", "latency_ms", "error", "outcome"}


def created(**over):
    d = {"order_id": "7e1c2b3a-0000-4a5b-8c9d-00000000000a", "client_order_id": COID, "fill_count": "1.00",
         "remaining_count": "0.00", "average_fill_price": "0.5600", "average_fee_paid": "0.017248", "ts_ms": 1791226212345}
    d.update(over)
    return d


@pytest.fixture
def rows():
    return []


@pytest.fixture
def audited(make_client, prod_env, rows):
    """A live client whose audit callback appends keyword-field dicts to ``rows``."""
    def audit(**fields):
        rows.append(fields)

    t = FakeTransport(host="external-api.kalshi.com")
    return make_client(t, prod_env, live=True, audit=audit)


# ================================================================================================ the fields
def test_every_row_carries_exactly_the_documented_fields(audited, rows, fake_clock):
    t = audited.transport
    t.get("/portfolio/balance", fixture="synthetic/balance_shard2.json")
    t.post(CREATE, status=201, json=created())
    audited.balance(2)
    audited.create_order(order_body())
    read, write = rows
    assert set(read) == FIELDS and set(write) == FIELDS

    assert read["env"] == "prod" and read["live"] is True and read["method"] == "GET" and read["kind"] == "read"
    assert read["path"] == "/trade-api/v2/portfolio/balance" and read["params"] == {"exchange_index": 2}
    assert read["body"] is None and read["client_order_id"] is None and read["attempt"] == 1
    assert read["status"] == 200 and read["error"] is None and read["outcome"] == "ok"
    assert json.loads(read["response"])["balance_dollars"] == "5.0000"
    assert read["ts_ms"] == int(fake_clock.now * 1000) and isinstance(read["latency_ms"], float)

    assert write["method"] == "POST" and write["kind"] == "write" and write["path"] == "/trade-api/v2/portfolio/events/orders"
    assert write["body"] == order_body() and write["client_order_id"] == COID and write["params"] is None
    assert write["status"] == 201 and write["outcome"] == "ok" and json.loads(write["response"])["order_id"]


def test_paths_are_the_signed_path_without_host_or_query(audited, rows):
    t = audited.transport
    t.delete(f"{CREATE}/abc123", json={"order_id": "abc123", "reduced_by": "1.00"})
    audited.cancel_order("abc123", 2)
    (row,) = rows
    assert row["path"] == "/trade-api/v2/portfolio/events/orders/abc123"
    assert "?" not in row["path"] and "kalshi" not in row["path"] and row["params"] == {"exchange_index": 2}


def test_every_http_attempt_gets_its_own_row_with_the_attempt_number_and_outcome(audited, rows):
    audited.transport.post(CREATE, respond(429, fixture="synthetic/error_429_flat.json"),
                           respond(429, fixture="error_429_nested.json"), respond(201, json=created()))
    res = audited.create_order(order_body())
    assert res.ok and res.attempts == 3
    assert [(r["attempt"], r["status"], r["outcome"]) for r in rows] == [(1, 429, "throttled"), (2, 429, "throttled"),
                                                                         (3, 201, "ok")]
    assert [r["error"] for r in rows] == ["http_429", "too_many_requests", None]    # parsed code, else http_<status>
    assert {r["client_order_id"] for r in rows} == {COID}
    assert len(rows) == len(audited.transport.calls)


def test_read_retries_are_audited_attempt_by_attempt(audited, rows):
    audited.transport.get("/portfolio/balance", 503, 429, respond(fixture="synthetic/balance.json"))
    audited.balance(None)
    assert [(r["attempt"], r["status"]) for r in rows] == [(1, 503), (2, 429), (3, 200)]


@pytest.mark.parametrize("status, outcome", [(400, "rejected"), (401, "auth"), (403, "auth"), (404, "rejected"),
                                             (409, "duplicate"), (500, "unknown"), (302, "unknown")])
def test_the_audited_outcome_is_the_write_outcome_class(audited, rows, status, outcome):
    audited.transport.post(CREATE, status=status, json={"error": {"code": "some_code", "message": "m"}})
    audited.create_order(order_body())
    (row,) = rows
    assert row["status"] == status and row["outcome"] == outcome and row["error"] == "some_code"


def test_a_transport_failure_is_audited_with_the_exception_class_only(audited, rows):
    audited.transport.post(CREATE, requests.ConnectionError("HTTPSConnectionPool(host='external-api.kalshi.com'): reset"))
    audited.create_order(order_body())
    (row,) = rows
    assert row["status"] is None and row["response"] is None and row["error"] == "ConnectionError"
    assert row["outcome"] == "unknown" and row["client_order_id"] == COID and row["kind"] == "write"
    assert "HTTPSConnectionPool" not in json.dumps(row, default=str)                # the message text is not kept


def test_the_response_is_cut_at_64_kb(audited, rows):
    big = json.dumps({"orders": [], "pad": "x" * 200_000, "cursor": ""})
    audited.transport.get("/portfolio/orders", status=200, text=big)
    audited.orders(ticker=TICKER)
    (row,) = rows
    assert len(row["response"]) == RESPONSE_AUDIT_LIMIT == 65536 and row["response"] == big[:65536]


def test_a_dry_run_client_marks_its_rows_not_live(make_client, prod_env, rows):
    t = FakeTransport(host="external-api.kalshi.com")
    t.get("/portfolio/balance", fixture="synthetic/balance.json")
    c = make_client(t, prod_env, live=False, audit=lambda **f: rows.append(f))
    c.balance(None)
    assert [r["live"] for r in rows] == [False]


def test_public_reads_are_audited_only_when_asked(make_client, watch_env, rows):
    def build(flag):
        t = FakeTransport()
        t.get("/exchange/status", fixture="exchange_status_prod.json")
        return make_client(t, watch_env, audit=lambda **f: rows.append(f), audit_public_reads=flag)

    build(False).exchange_status()
    assert rows == []
    build(True).exchange_status()
    assert [(r["kind"], r["live"], r["method"], r["status"]) for r in rows] == [("public", False, "GET", 200)]
    assert set(rows[0]) == FIELDS


def test_without_an_audit_callback_nothing_breaks(make_client, prod_env):
    t = FakeTransport()
    t.get("/portfolio/balance", fixture="synthetic/balance.json")
    assert make_client(t, prod_env, audit=None).balance(None)["balance"] == 2500


def test_an_audit_failure_never_changes_a_write_result(make_client, prod_env, caplog):
    def broken(**fields):
        raise RuntimeError("disk full")

    t = FakeTransport()
    t.post(CREATE, status=201, json=created())
    c = make_client(t, prod_env, live=True, audit=broken)
    caplog.set_level(logging.DEBUG)
    res = c.create_order(order_body())
    assert res.ok and c.audit_failures == 1 and len(t.calls) == 1                    # sent once, result intact
    assert "execution audit callback failed" in caplog.text


# ================================================================================================ no secrets, anywhere
@pytest.mark.parametrize("password", [None, "pw-UNIQUE-2718281828"])
def test_no_secret_reaches_the_audit_the_results_the_logs_or_the_console(
        monkeypatch, tmp_path, any_key, make_client, caplog, capsys, password):
    """Design 5.7 leak test: run a fake session, then search everything for the key id, the signatures, the PEM
    text and the PEM path. The environment is built by load_env from a real PEM file, like production."""
    key_id = "leakcheck-7f3a9c10-aaaa-4bbb-8ccc-0123456789ab"
    pem_dir = tmp_path / "SecretKeyDir"
    pem_dir.mkdir()
    pem = pem_dir / "kalshi_prod.pem"
    pem.write_bytes(pem_bytes(any_key, "pkcs8", password.encode() if password else None))
    monkeypatch.setenv("KALSHI_ENV", "prod")
    monkeypatch.setenv("KALSHI_PROD_KEY_ID", key_id)
    monkeypatch.setenv("KALSHI_PROD_KEY_PATH", str(pem))
    if password:
        monkeypatch.setenv("KALSHI_PROD_KEY_PASSWORD", password)
    caplog.set_level(logging.DEBUG)
    env = load_env(True)

    rows: list[dict] = []
    t = FakeTransport(host="external-api.kalshi.com")

    def orders(call):
        coid = call.body["client_order_id"]
        return {"ok-1": respond(201, json=created(client_order_id=coid)),
                "auth-1": respond(401, fixture="synthetic/error_401.json"),
                "throttle-1": respond(429, fixture="synthetic/error_429_flat.json"),
                "boom-1": respond(500, text="internal error"),
                "timeout-1": requests.Timeout("slow"),
                "dup-1": respond(409, fixture="synthetic/error_409_duplicate.json")}[coid]

    t.post(CREATE, orders)
    t.get("/portfolio/balance", fixture="synthetic/balance_shard2.json")
    t.get("/portfolio/orders", fixture="synthetic/orders.json")
    t.get("/exchange/status", fixture="exchange_status_prod.json")
    t.delete(f"{CREATE}/abc123", status=404, fixture="error_404_not_found.json")
    t.delete(CREATE, 204)
    c = make_client(t, env, live=True, audit=lambda **f: rows.append(f), audit_public_reads=True)

    visible = [repr(c), repr(env), str(env.banner())]
    c.exchange_status(), c.balance(2), c.orders(ticker=TICKER)
    for coid in ("ok-1", "auth-1", "throttle-1", "boom-1", "timeout-1", "dup-1"):
        visible.append(repr(c.create_order(order_body(client_order_id=coid))))
    visible.append(repr(c.cancel_order("abc123", 2)))
    visible.append(repr(c.cancel_all()))
    try:
        c.balance(7)
    except Exception as exc:                                                        # an unmatched route: whatever happens
        visible.append(repr(exc))
    out = capsys.readouterr()

    signatures = [call.headers["KALSHI-ACCESS-SIGNATURE"] for call in t.calls if call.signed]
    assert len(signatures) >= 10 and all(len(s) >= 80 for s in signatures)
    pem_text = pem.read_text()
    secrets = {"key id": key_id, "private key marker": "PRIVATE KEY", "pem path": str(pem),
               "pem path (json)": json.dumps(str(pem))[1:-1], "pem dir": "SecretKeyDir", "pem file": "kalshi_prod.pem"}
    secrets.update({f"signature {i}": s for i, s in enumerate(signatures)})
    secrets.update({f"pem line {i}": line for i, line in enumerate(pem_text.splitlines())
                    if line and "-----" not in line and len(line) > 20})
    if password:
        secrets["password"] = password
    haystacks = {"audit rows (json)": json.dumps(rows, default=str), "audit rows (repr)": repr(rows),
                 "visible reprs": "\n".join(visible), "log": caplog.text, "stdout": out.out, "stderr": out.err}
    assert len(rows) == len([call for call in t.calls])                             # every request has a row
    for hname, hay in haystacks.items():
        for sname, secret in secrets.items():
            assert secret not in hay, f"{sname} leaked into {hname}"
    # the only identifier that may appear anywhere is the fingerprint
    assert env.key_fp in repr(c) and key_id not in repr(c)


def test_the_audit_rows_never_contain_header_names_or_values(audited, rows):
    audited.transport.post(CREATE, status=201, json=created())
    audited.create_order(order_body())
    text = json.dumps(rows, default=str).lower()
    for needle in ("kalshi-access", "authorization", "signature", "x-api", "cookie", "user-agent", "accept"):
        assert needle not in text
    assert not re.search(r"[A-Za-z0-9+/]{80,}={0,2}", json.dumps(rows, default=str))     # no signature-sized blobs
