"""kpm.execution.auth: request signing (docs/execution_design.md section 2, test plan case 1)."""
from __future__ import annotations

import base64
import re
import time
from types import SimpleNamespace

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from kpm.execution import auth
from kpm.execution.auth import (API_PREFIX, AUTH_HEADERS, WS_SIGN_PATH, auth_headers, key_fingerprint, key_type,
                                load_private_key, sign_path, sign_text)

from .conftest import TEST_KEY_ID, pem_bytes

PROD_BASE = "https://external-api.kalshi.com/trade-api/v2"
DEMO_BASE = "https://external-api.demo.kalshi.co/trade-api/v2"
TS = 1703123456789                                           # the design's test-vector timestamp (ms)


@pytest.fixture(scope="module")
def det_key() -> Ed25519PrivateKey:
    """A deterministic throwaway key (seed 00 01 02 ... 1f) for pinned vectors. Never used anywhere real."""
    return Ed25519PrivateKey.from_private_bytes(bytes(range(32)))


def b64(sig: bytes) -> str:
    return base64.b64encode(sig).decode("ascii")


def verify_rsa(pub, sig_b64: str, text: str, salt: int) -> None:
    pub.verify(base64.b64decode(sig_b64), text.encode(), padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=salt),
               hashes.SHA256())


# ------------------------------------------------------------------------------------------------ Ed25519
@pytest.mark.parametrize("secret, message, signature", [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb", "r",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
])
def test_ed25519_matches_the_rfc_8032_known_answers(secret, message, signature):
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret))
    assert base64.b64decode(sign_text(key, message)).hex() == signature


PINNED = [
    ("GET", f"{API_PREFIX}/portfolio/balance",
     "hkBtGNHKCfMll7XdniCzUygg/ek7JdCINuVOaGq0bDS3feQIdYVMirJSybX93bQtj8CMmr3q0RiAToeTeMOwBg=="),
    ("DELETE", f"{API_PREFIX}/portfolio/events/orders/abc123",
     "7Z+z5BFxKyCB+u5iOAmhwEhPEuP74PDUWYFtPURTVRGMRfdHr7g36+6g83zDZBAl2OK26FkH6lHQlTyhL8IiCw=="),
    ("POST", f"{API_PREFIX}/portfolio/events/orders",
     "/ro4N0xtMcM9TBedlEcPA5AL1SsWyOiIo3UPU4/aeQ/H6c6rvS4ymxWhdFIT2dLRprk+g8Dju8XU1AY4/NDkDg=="),
    ("GET", WS_SIGN_PATH,
     "GpkXLSIK8avqVF1CPnKos7fulth1qYoYZjI5qzovCmuzcKV0/qCnl8R5kVmiu2d6dLYPcYVo2r2OgcCWv1uwAw=="),
]


@pytest.mark.parametrize("method, path, expected_sig", PINNED)
def test_pinned_ed25519_vectors(det_key, method, path, expected_sig):
    h = auth_headers(det_key, TEST_KEY_ID, method, path, ts_ms=TS)
    assert h["KALSHI-ACCESS-SIGNATURE"] == expected_sig
    det_key.public_key().verify(base64.b64decode(expected_sig), f"{TS}{method}{path}".encode())


def test_the_designs_delete_vector_signs_the_path_without_the_query(det_key):
    """DELETE https://external-api.kalshi.com/trade-api/v2/portfolio/events/orders/abc123?market_ticker=X&exchange_index=2
    pre-signs as 1703123456789DELETE/trade-api/v2/portfolio/events/orders/abc123 (no host, no query)."""
    with_query = auth_headers(det_key, TEST_KEY_ID, "DELETE",
                              "/trade-api/v2/portfolio/events/orders/abc123?market_ticker=X&exchange_index=2", ts_ms=TS)
    plain = auth_headers(det_key, TEST_KEY_ID, "DELETE", "/trade-api/v2/portfolio/events/orders/abc123", ts_ms=TS)
    assert with_query == plain
    msg = b"1703123456789DELETE/trade-api/v2/portfolio/events/orders/abc123"
    det_key.public_key().verify(base64.b64decode(with_query["KALSHI-ACCESS-SIGNATURE"]), msg)


def test_ed25519_signature_shape_determinism_and_tamper_detection(ed25519_key):
    text = f"{TS}GET/trade-api/v2/portfolio/balance"
    s1, s2 = sign_text(ed25519_key, text), sign_text(ed25519_key, text)
    raw = base64.b64decode(s1, validate=True)
    assert s1 == s2 and len(raw) == 64 and len(s1) == 88                          # deterministic, 64 bytes, 88 chars
    ed25519_key.public_key().verify(raw, text.encode())
    with pytest.raises(InvalidSignature):
        ed25519_key.public_key().verify(raw, (text + "x").encode())


def test_signatures_use_the_standard_base64_alphabet(rsa_key):
    for key in (rsa_key, Ed25519PrivateKey.generate()):
        for i in range(30):
            sig = sign_text(key, f"{TS + i}GET/trade-api/v2/x")
            assert re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", sig) and "-" not in sig and "_" not in sig
            base64.b64decode(sig, validate=True)


# ------------------------------------------------------------------------------------------------ RSA-PSS
def test_rsa_pss_uses_digest_length_salt(rsa_key):
    text = f"{TS}GET/trade-api/v2/portfolio/balance"
    sig = sign_text(rsa_key, text)
    assert len(base64.b64decode(sig)) == 256 and len(sig) == 344                  # RSA-2048: 256 bytes, 344 chars
    pub = rsa_key.public_key()
    verify_rsa(pub, sig, text, padding.PSS.DIGEST_LENGTH)                         # salt = 32 bytes: accepted
    verify_rsa(pub, sig, text, 32)
    for wrong_salt in (0, 20, 31, 33, 64, 222):                                   # 222 = MAX_LENGTH for RSA-2048
        with pytest.raises(InvalidSignature):
            verify_rsa(pub, sig, text, wrong_salt)                                # the salt is exactly the digest length
    with pytest.raises(InvalidSignature):
        verify_rsa(pub, sig, text + "x", 32)


def test_a_max_length_rsa_signature_is_what_kalshi_would_reject(rsa_key):
    text = f"{TS}GET/trade-api/v2/portfolio/balance"
    wrong = b64(rsa_key.sign(text.encode(), padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                                         salt_length=padding.PSS.MAX_LENGTH), hashes.SHA256()))
    with pytest.raises(InvalidSignature):
        verify_rsa(rsa_key.public_key(), wrong, text, padding.PSS.DIGEST_LENGTH)


def test_rsa_signatures_are_randomized_but_all_verify(rsa_key):
    text = f"{TS}POST/trade-api/v2/portfolio/events/orders"
    sigs = {sign_text(rsa_key, text) for _ in range(5)}
    assert len(sigs) == 5
    for s in sigs:
        verify_rsa(rsa_key.public_key(), s, text, 32)


def test_sign_text_validates_its_input(ed25519_key):
    with pytest.raises(TypeError):
        sign_text(ed25519_key, b"bytes are not text")
    with pytest.raises(TypeError):
        sign_text(ed25519_key, None)
    with pytest.raises(TypeError, match="unsupported key type"):
        sign_text(ec.generate_private_key(ec.SECP256R1()), "x")
    with pytest.raises(TypeError):
        sign_text("not a key", "x")


def test_non_ascii_text_is_signed_as_utf8(det_key):
    text = f"{TS}GET/trade-api/v2/markets/Ünï"
    sig = sign_text(det_key, text)
    det_key.public_key().verify(base64.b64decode(sig), text.encode("utf-8"))


# ------------------------------------------------------------------------------------------------ key types and loading
def test_key_type_comes_from_the_object(ed25519_key, rsa_key):
    assert key_type(ed25519_key) == "ed25519" and key_type(rsa_key) == "rsa"
    for bad in (ec.generate_private_key(ec.SECP256R1()), object(), None, "PEM"):
        with pytest.raises(TypeError):
            key_type(bad)


@pytest.mark.parametrize("fmt, header, which", [
    ("pkcs8", b"-----BEGIN PRIVATE KEY-----", "ed25519"),
    ("pkcs8", b"-----BEGIN PRIVATE KEY-----", "rsa"),            # a PKCS#8 RSA key ALSO says BEGIN PRIVATE KEY
    ("pkcs1", b"-----BEGIN RSA PRIVATE KEY-----", "rsa"),
])
def test_load_private_key_detects_the_type_from_the_parsed_key_not_the_header(tmp_path, ed25519_key, rsa_key, fmt, header,
                                                                               which):
    key = ed25519_key if which == "ed25519" else rsa_key
    pem = pem_bytes(key, fmt)
    assert pem.startswith(header)
    p = tmp_path / "k.pem"
    p.write_bytes(pem)
    loaded = load_private_key(p)
    assert key_type(loaded) == which
    assert isinstance(loaded, Ed25519PrivateKey) == (which == "ed25519")        # PKCS#8 RSA does not load as Ed25519
    text = f"{TS}GET/trade-api/v2/portfolio/balance"
    sig = sign_text(loaded, text)
    if which == "ed25519":
        key.public_key().verify(base64.b64decode(sig), text.encode())
    else:
        verify_rsa(key.public_key(), sig, text, 32)


def test_load_private_key_accepts_str_paths(tmp_path, ed25519_key):
    p = tmp_path / "k.pem"
    p.write_bytes(pem_bytes(ed25519_key))
    assert key_type(load_private_key(str(p))) == "ed25519"


def test_encrypted_pem_needs_the_right_password(tmp_path, ed25519_key):
    p = tmp_path / "enc.pem"
    p.write_bytes(pem_bytes(ed25519_key, "pkcs8", b"s3cret-pw"))
    assert key_type(load_private_key(p, "s3cret-pw")) == "ed25519"              # str password
    assert key_type(load_private_key(p, b"s3cret-pw")) == "ed25519"             # bytes password
    with pytest.raises((ValueError, TypeError)):
        load_private_key(p)                                                      # password missing
    with pytest.raises(ValueError):
        load_private_key(p, "wrong")
    with pytest.raises((ValueError, TypeError)):
        load_private_key(p, "")                                                  # an empty password counts as none


def test_non_kalshi_key_types_are_rejected(tmp_path):
    p = tmp_path / "ec.pem"
    p.write_bytes(pem_bytes(ec.generate_private_key(ec.SECP256R1())))
    with pytest.raises(ValueError, match="Ed25519 or RSA"):
        load_private_key(p)


def test_unreadable_or_garbage_keys_fail_without_echoing_the_pem(tmp_path):
    marker = "DISTINCTIVE-SECRET-MARKER-0123456789"
    garbage = tmp_path / "bad.pem"
    garbage.write_text(f"-----BEGIN PRIVATE KEY-----\n{marker}\n-----END PRIVATE KEY-----\n")
    with pytest.raises((ValueError, TypeError)) as ei:
        load_private_key(garbage)
    assert marker not in str(ei.value) and marker not in repr(ei.value)
    with pytest.raises(FileNotFoundError):
        load_private_key(tmp_path / "missing.pem")
    with pytest.raises(OSError):
        load_private_key(tmp_path)                                               # a directory
    empty = tmp_path / "empty.pem"
    empty.write_bytes(b"")
    with pytest.raises(ValueError):
        load_private_key(empty)


# ------------------------------------------------------------------------------------------------ sign_path
@pytest.mark.parametrize("base", [PROD_BASE, DEMO_BASE, "https://api.elections.kalshi.com/trade-api/v2"])
def test_sign_path_includes_the_api_prefix_on_every_host(base):
    assert sign_path(base, "/portfolio/balance") == "/trade-api/v2/portfolio/balance"
    assert sign_path(base, "/portfolio/events/orders") == "/trade-api/v2/portfolio/events/orders"
    assert sign_path(base, "/markets/KXBTC15M-26OCT051430-30/orderbook") == \
        "/trade-api/v2/markets/KXBTC15M-26OCT051430-30/orderbook"
    assert sign_path(base, "/portfolio/events/orders/abc123") == "/trade-api/v2/portfolio/events/orders/abc123"


@pytest.mark.parametrize("endpoint", [
    "/trade-api/v2/portfolio/balance",           # doubled prefix ("path not found")
    "/portfolio/balance?x=1",                    # inline query: queries go in params and are never signed
    "/portfolio/balance/",                       # a trailing slash draws a 301 that changes the signed path
    "portfolio/balance",                         # no leading slash
    "",
    "/",
    "//portfolio/balance",
    "/portfolio//balance",
    "/portfolio/../balance",
    "/portfolio/./balance",
    "/portfolio/balance#frag",
    "/portfolio/ balance",
    "/portfolio/balance\n",
])
def test_sign_path_refuses_malformed_endpoints(endpoint):
    with pytest.raises(ValueError):
        sign_path(PROD_BASE, endpoint)


@pytest.mark.parametrize("base", [
    "http://external-api.kalshi.com/trade-api/v2",           # not https
    "https://external-api.kalshi.com/trade-api/v2/",         # trailing slash
    "https://external-api.kalshi.com/trade-api/v2?x=1",
    "https://external-api.kalshi.com/other/v2",
    "https://external-api.kalshi.com",
    "external-api.kalshi.com/trade-api/v2",
    "",
])
def test_sign_path_refuses_malformed_bases(base):
    with pytest.raises(ValueError):
        sign_path(base, "/portfolio/balance")


def test_sign_path_refuses_non_strings():
    for bad in (None, 5, b"/portfolio/balance"):
        with pytest.raises(ValueError):
            sign_path(PROD_BASE, bad)


# ------------------------------------------------------------------------------------------------ auth_headers
def test_auth_headers_has_exactly_the_three_kalshi_headers(det_key):
    h = auth_headers(det_key, TEST_KEY_ID, "GET", "/trade-api/v2/portfolio/balance")
    assert set(h) == set(AUTH_HEADERS) == {"KALSHI-ACCESS-KEY", "KALSHI-ACCESS-TIMESTAMP", "KALSHI-ACCESS-SIGNATURE"}
    assert h["KALSHI-ACCESS-KEY"] == TEST_KEY_ID
    assert re.fullmatch(r"[0-9]{13}", h["KALSHI-ACCESS-TIMESTAMP"])                # milliseconds: 13 digits
    now_ms = time.time() * 1000
    assert abs(int(h["KALSHI-ACCESS-TIMESTAMP"]) - now_ms) < 5000                   # fresh, in ms (not seconds)
    det_key.public_key().verify(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]),
                                (h["KALSHI-ACCESS-TIMESTAMP"] + "GET/trade-api/v2/portfolio/balance").encode())


def test_every_call_gets_a_fresh_timestamp_and_signature(monkeypatch, det_key):
    ticks = iter(range(1_791_226_200, 1_791_226_300))                  # whole seconds: x * 1000 is exact in floating point
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: float(next(ticks))))
    seen = [auth_headers(det_key, TEST_KEY_ID, "GET", "/trade-api/v2/portfolio/balance") for _ in range(5)]
    assert [h["KALSHI-ACCESS-TIMESTAMP"] for h in seen] == [str((1_791_226_200 + i) * 1000) for i in range(5)]
    assert len({h["KALSHI-ACCESS-SIGNATURE"] for h in seen}) == 5                    # every retry is re-signed


def test_the_method_is_uppercased_before_signing(det_key):
    path = "/trade-api/v2/portfolio/events/orders"
    lower = auth_headers(det_key, TEST_KEY_ID, "post", path, ts_ms=TS)
    upper = auth_headers(det_key, TEST_KEY_ID, "POST", path, ts_ms=TS)
    assert lower == upper
    assert lower["KALSHI-ACCESS-SIGNATURE"] == PINNED[2][2]


def test_the_websocket_handshake_signs_get_plus_the_ws_path(det_key):
    assert WS_SIGN_PATH == "/trade-api/ws/v2"
    h = auth_headers(det_key, TEST_KEY_ID, "GET", WS_SIGN_PATH, ts_ms=TS)
    det_key.public_key().verify(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]), b"1703123456789GET/trade-api/ws/v2")
    assert h["KALSHI-ACCESS-SIGNATURE"] == PINNED[3][2]


def test_a_timestamp_in_seconds_is_refused_because_it_would_fail_the_signature(det_key):
    with pytest.raises(ValueError, match="milliseconds"):
        auth_headers(det_key, TEST_KEY_ID, "GET", "/trade-api/v2/portfolio/balance", ts_ms=1_703_123_456)
    with pytest.raises(ValueError):
        auth_headers(det_key, TEST_KEY_ID, "GET", "/trade-api/v2/portfolio/balance", ts_ms=17_031_234_567_890)


@pytest.mark.parametrize("method", ["", "get ", "HEAD", "OPTIONS", "TRACE", "FETCH", None])
def test_unsupported_methods_are_refused(det_key, method):
    with pytest.raises(ValueError):
        auth_headers(det_key, TEST_KEY_ID, method, "/trade-api/v2/portfolio/balance")


@pytest.mark.parametrize("path", ["/portfolio/balance", "portfolio/balance", "", "/api/v2/portfolio/balance",
                                  "https://external-api.kalshi.com/trade-api/v2/portfolio/balance"])
def test_paths_must_start_at_the_api_root(det_key, path):
    with pytest.raises(ValueError):
        auth_headers(det_key, TEST_KEY_ID, "GET", path)


@pytest.mark.parametrize("key_id", ["", "   ", None, 5])
def test_a_key_id_is_required(det_key, key_id):
    with pytest.raises(ValueError):
        auth_headers(det_key, key_id, "GET", "/trade-api/v2/portfolio/balance")


def test_auth_headers_with_an_rsa_key_verify_with_salt_32(rsa_key):
    h = auth_headers(rsa_key, TEST_KEY_ID, "DELETE", "/trade-api/v2/portfolio/events/orders/abc?exchange_index=2")
    text = h["KALSHI-ACCESS-TIMESTAMP"] + "DELETE/trade-api/v2/portfolio/events/orders/abc"
    verify_rsa(rsa_key.public_key(), h["KALSHI-ACCESS-SIGNATURE"], text, 32)


# ------------------------------------------------------------------------------------------------ secrets
def test_key_fingerprint_is_the_first_8_hex_of_sha256_and_leaks_nothing():
    fp = key_fingerprint(TEST_KEY_ID)
    assert fp == "cf4c4732" and re.fullmatch(r"[0-9a-f]{8}", fp)
    assert key_fingerprint(TEST_KEY_ID) == fp and key_fingerprint(TEST_KEY_ID + "x") != fp
    assert TEST_KEY_ID not in fp
    for bad in ("", None, 5):
        with pytest.raises(ValueError):
            key_fingerprint(bad)


def test_module_surface_and_constants():
    assert API_PREFIX == "/trade-api/v2"
    for name in ("load_private_key", "sign_text", "sign_path", "auth_headers", "WS_SIGN_PATH"):
        assert hasattr(auth, name)
    # the module never prints or logs: nothing that could carry a key id, a path or a signature
    import inspect
    src = inspect.getsource(auth)
    assert not re.search(r"\bprint\(", src) and "import logging" not in src and not re.search(r"\blog\.", src)


def test_rsa_key_size_is_not_assumed():
    """A 1024-bit key signs with a 128-byte signature; nothing hard-codes 256 (kept small so the test is fast)."""
    small = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    text = f"{TS}GET/trade-api/v2/portfolio/balance"
    sig = sign_text(small, text)
    assert len(base64.b64decode(sig)) == 128
    verify_rsa(small.public_key(), sig, text, 32)
