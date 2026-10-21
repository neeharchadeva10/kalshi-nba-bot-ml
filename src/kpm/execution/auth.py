"""Kalshi request signing (docs/execution_design.md, section 2; docs.kalshi.com api_keys + quick starts).

Pre-sign text = timestamp_ms + METHOD + path, where path includes ``/trade-api/v2`` and excludes the
host and the query string. The JSON body is not signed. Ed25519 keys sign the text directly (64-byte,
deterministic signatures); RSA keys use RSA-PSS with SHA-256, MGF1(SHA-256) and salt length = digest
length (32 bytes, NOT ``MAX_LENGTH``). Signatures are standard base64. The key type is read from the
parsed key object, never from the PEM header (PKCS#8 RSA keys also say ``BEGIN PRIVATE KEY``).

Secrets: nothing here logs or returns the key id, the PEM path or the PEM contents; use
:func:`key_fingerprint` (sha256(key_id)[:8]) wherever an identifier is needed in logs or banners.
"""
from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

API_PREFIX = "/trade-api/v2"
WS_SIGN_PATH = "/trade-api/ws/v2"                 # WebSocket handshake: sign ts + "GET" + this path
HEADER_KEY = "KALSHI-ACCESS-KEY"
HEADER_TIMESTAMP = "KALSHI-ACCESS-TIMESTAMP"
HEADER_SIGNATURE = "KALSHI-ACCESS-SIGNATURE"
AUTH_HEADERS = (HEADER_KEY, HEADER_TIMESTAMP, HEADER_SIGNATURE)
METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")


def load_private_key(pem_path: str | Path, password: str | bytes | None = None):
    """Load an Ed25519 or RSA private key from a PEM file (optionally encrypted).

    The type check uses the parsed object: an EC/DSA key raises ``ValueError``. Exceptions never
    contain the PEM contents; callers must not echo the path (see ``kpm.execution.env``).
    """
    if isinstance(password, str):
        password = password.encode("utf-8")
    key = serialization.load_pem_private_key(Path(pem_path).read_bytes(), password=password or None)
    if not isinstance(key, (Ed25519PrivateKey, rsa.RSAPrivateKey)):
        raise ValueError("Kalshi API keys are Ed25519 or RSA; this PEM holds another key type")
    return key


def key_type(key: Any) -> str:
    """'ed25519' or 'rsa' (from the object, not the PEM header)."""
    if isinstance(key, Ed25519PrivateKey):
        return "ed25519"
    if isinstance(key, rsa.RSAPrivateKey):
        return "rsa"
    raise TypeError(f"unsupported key type {type(key).__name__}; Kalshi keys are Ed25519 or RSA")


def sign_text(key: Any, text: str) -> str:
    """Sign UTF-8 ``text`` with the key's own algorithm and return standard base64."""
    if not isinstance(text, str):
        raise TypeError("pre-sign text must be a str")
    msg = text.encode("utf-8")
    kind = key_type(key)
    if kind == "ed25519":
        sig = key.sign(msg)                                                # 64 bytes, deterministic
    else:
        sig = key.sign(msg,
                       padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                   salt_length=padding.PSS.DIGEST_LENGTH),  # 32 bytes; NOT MAX_LENGTH
                       hashes.SHA256())                                     # 256 bytes for RSA-2048, randomized
    return base64.b64encode(sig).decode("ascii")                           # standard alphabet, not URL-safe


def sign_path(base_url: str, endpoint: str) -> str:
    """``'/portfolio/balance'`` -> ``'/trade-api/v2/portfolio/balance'`` (the path that gets signed).

    The endpoint is relative to the REST base and must start with '/'. A doubled ``/trade-api``
    prefix, an inline query string (queries go in ``params`` and are never signed), a fragment, a
    trailing slash (it draws a 301 that changes the signed path) or a ``.``/``..`` segment is refused.
    """
    if not isinstance(endpoint, str) or not endpoint.startswith("/"):
        raise ValueError(f"endpoint must start with '/': {endpoint!r}")
    if (endpoint.startswith("/trade-api/") or "?" in endpoint or "#" in endpoint or endpoint.endswith("/")
            or "//" in endpoint or any(seg in (".", "..") for seg in endpoint.split("/"))
            or any(ch.isspace() for ch in endpoint)):
        raise ValueError(f"bad endpoint {endpoint!r}")
    base = urlparse(base_url)
    if base.scheme != "https" or not base.netloc or base.query or base.fragment \
            or not base.path.startswith("/trade-api/") or base.path.endswith("/"):
        raise ValueError("base_url must look like https://<host>/trade-api/v2 (no trailing slash, no query)")
    return urlparse(base_url + endpoint).path


def auth_headers(key: Any, key_id: str, method: str, path: str, *, ts_ms: int | None = None) -> dict[str, str]:
    """The three ``KALSHI-ACCESS-*`` headers for one request attempt.

    ``path`` is the full signed path (``/trade-api/v2/...`` or :data:`WS_SIGN_PATH`); anything after
    '?' is dropped before signing. The timestamp is milliseconds (13 digits) and is taken fresh on
    every call unless ``ts_ms`` is given (tests only), so every retry is re-signed.
    """
    if not isinstance(key_id, str) or not key_id.strip():
        raise ValueError("an API key id is required to sign a request")
    method_u = str(method).upper()
    if method_u not in METHODS:
        raise ValueError(f"unsupported HTTP method {method!r}")
    signed_path = str(path).split("?", 1)[0]
    if not signed_path.startswith("/trade-api/"):
        raise ValueError("sign the full path from the API root (it must start with /trade-api/)")
    ts = str(int(time.time() * 1000) if ts_ms is None else int(ts_ms))
    if len(ts) != 13:
        raise ValueError("KALSHI-ACCESS-TIMESTAMP must be Unix milliseconds (13 digits)")
    return {HEADER_KEY: key_id,
            HEADER_TIMESTAMP: ts,
            HEADER_SIGNATURE: sign_text(key, ts + method_u + signed_path)}


def key_fingerprint(key_id: str) -> str:
    """sha256(key_id)[:8]: the only form of the key id that may appear in logs, banners and the DB."""
    if not isinstance(key_id, str) or not key_id:
        raise ValueError("key id must be a non-empty string")
    return hashlib.sha256(key_id.encode("utf-8")).hexdigest()[:8]
