"""Environment selection: one allowlisted REST/WS host per environment (docs/execution_design.md, section 3).

* ``KALSHI_ENV`` is required and must be exactly ``demo`` or ``prod``. There is no default.
* Hosts are a constant table in code. ``config/settings.yaml`` cannot override them
  (``trading.base_url`` / ``trading.ws_url`` are ignored by the execution module).
* Credentials are per environment and only the selected environment's pair is read:
    - prod: ``KALSHI_PROD_KEY_ID`` (falls back to ``KALSHI_API_KEY_ID``),
            ``KALSHI_PROD_KEY_PATH`` (falls back to ``KALSHI_PRIVATE_KEY_PATH``),
            optional ``KALSHI_PROD_KEY_PASSWORD``;
    - demo: ``KALSHI_DEMO_KEY_ID``, ``KALSHI_DEMO_KEY_PATH``, optional ``KALSHI_DEMO_KEY_PASSWORD``.
* Watch-only mode (``require_keys=False``) works with no keys at all: public reads only.
* Secrets are never printed, logged or stored. Messages name the environment VARIABLE, never its
  value; the only identifier shown is ``key_fp = sha256(key_id)[:8]``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlparse

from cryptography.exceptions import UnsupportedAlgorithm

from kpm.core import config
from kpm.execution.auth import key_fingerprint, key_type, load_private_key

ENV_NAMES = ("demo", "prod")

HOSTS = MappingProxyType({
    "demo": MappingProxyType({"rest": "https://external-api.demo.kalshi.co/trade-api/v2",
                              "ws": "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"}),
    "prod": MappingProxyType({"rest": "https://external-api.kalshi.com/trade-api/v2",
                              "ws": "wss://external-api-ws.kalshi.com/trade-api/ws/v2"}),
})

# Lookup order per credential (first non-empty variable wins). The legacy single pair is a prod-only fallback.
KEY_VARS = MappingProxyType({
    "demo": MappingProxyType({"key_id": ("KALSHI_DEMO_KEY_ID",),
                              "key_path": ("KALSHI_DEMO_KEY_PATH",),
                              "password": ("KALSHI_DEMO_KEY_PASSWORD",)}),
    "prod": MappingProxyType({"key_id": ("KALSHI_PROD_KEY_ID", "KALSHI_API_KEY_ID"),
                              "key_path": ("KALSHI_PROD_KEY_PATH", "KALSHI_PRIVATE_KEY_PATH"),
                              "password": ("KALSHI_PROD_KEY_PASSWORD",)}),
})
SECRET_ENV_NAMES = frozenset(v for env in KEY_VARS.values() for names in env.values() for v in names)
# Keep the shared registry of secret variable names complete (it is only ever extended, never printed).
config.SECRET_KEYS.update(SECRET_ENV_NAMES)

_PLAUSIBLE_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,127}")


@dataclass(frozen=True)
class Env:
    """The selected environment. ``key_id`` and ``key`` are excluded from ``repr`` so they never leak."""

    name: str
    rest_base: str
    ws_url: str
    key_id: str | None = field(default=None, repr=False)
    key: Any = field(default=None, repr=False, compare=False)
    key_fp: str | None = None
    key_error: str | None = None          # why no key is loaded in watch-only mode (never contains a secret)
    key_path: str | None = field(default=None, repr=False, compare=False)   # as named in .env; only for redact_values

    def __post_init__(self) -> None:
        if self.name not in HOSTS:
            raise ValueError(f"environment must be one of {ENV_NAMES}, got {self.name!r}")
        hosts = HOSTS[self.name]
        if self.rest_base != hosts["rest"] or self.ws_url != hosts["ws"]:
            raise ValueError(f"{self.name}: hosts must come from the HOSTS table; refusing a non-allowlisted host")
        if (self.key_id is None) != (self.key is None):
            raise ValueError("key_id and key must be given together")
        if self.key is not None:
            key_type(self.key)                                    # Ed25519 or RSA only
            fp = key_fingerprint(self.key_id)
            if self.key_fp is None:
                object.__setattr__(self, "key_fp", fp)
            elif self.key_fp != fp:
                raise ValueError("key_fp does not match key_id")
        elif self.key_fp is not None:
            raise ValueError("key_fp given without a key")

    @property
    def has_keys(self) -> bool:
        return self.key is not None

    @property
    def redact_values(self) -> tuple[str, ...]:
        """The key id and the key path, for ``Ledger(redact=...)``: the storage layer scrubs them from every row and log
        line even if a caller passes one by mistake. This is the only way another module gets at these values; it is
        never printed, logged or stored itself."""
        return tuple(v for v in (self.key_id, self.key_path) if v)

    @property
    def rest_host(self) -> str:
        return urlparse(self.rest_base).netloc

    @property
    def ws_host(self) -> str:
        return urlparse(self.ws_url).netloc

    def banner(self) -> dict[str, Any]:
        """Safe-to-print summary (no key id, no path, no key material)."""
        return {"env": self.name, "rest_host": self.rest_host, "ws_host": self.ws_host,
                "keys": "loaded" if self.has_keys else "none", "key_fp": self.key_fp,
                "key_type": key_type(self.key) if self.has_keys else None, "key_error": self.key_error}


def _getenv(name: str) -> str | None:
    """Environment / .env lookup (``config.secret`` loads .env without overriding real variables)."""
    return config.secret(name)


def _first(names: tuple[str, ...]) -> tuple[str | None, str | None]:
    for n in names:
        v = _getenv(n)
        if v:
            return v, n
    return None, None


def _or(names: tuple[str, ...]) -> str:
    return " (or ".join(names) + ")" * (len(names) - 1)


def _resolve_key_path(raw: str) -> Path:
    """The key file named by the environment: ``~`` is expanded; a relative path is tried against the working
    directory, then the project root (the PEM belongs outside the repo, so absolute is the norm)."""
    try:
        p = Path(raw).expanduser()
    except RuntimeError:                                   # no home directory to expand '~' against
        p = Path(raw)
    if not p.is_absolute():
        for base in (Path.cwd(), config.PROJECT_ROOT):
            candidate = base / p
            if candidate.is_file():
                return candidate
    return p


def load_env(require_keys: bool, *, expected: str | None = None) -> Env:
    """Read ``KALSHI_ENV`` and that environment's key pair.

    ``require_keys=True`` (live or authenticated use): any missing or unloadable credential raises
    ``SystemExit`` with a message that names the variable to fix. ``require_keys=False`` (watch-only):
    no keys is fine; a broken key configuration is reported in ``Env.key_error`` instead of exiting.
    ``expected`` is the ``--env`` command-line value; it must equal ``KALSHI_ENV`` exactly.
    """
    name = _getenv("KALSHI_ENV")
    if name is None:
        raise SystemExit("KALSHI_ENV is not set. Set it to exactly 'demo' or 'prod' (there is no default). "
                         "Watch-only mode on real markets uses KALSHI_ENV=prod and needs no keys.")
    if name not in HOSTS:
        raise SystemExit(f"KALSHI_ENV must be exactly 'demo' or 'prod', got {name[:20]!r}.")
    if expected is not None and expected != name:
        raise SystemExit(f"--env {str(expected)[:20]!r} does not match KALSHI_ENV={name!r}; refusing to start.")
    hosts = HOSTS[name]
    names = KEY_VARS[name]
    key_id, _id_var = _first(names["key_id"])
    key_path, path_var = _first(names["key_path"])
    password, _ = _first(names["password"])

    def fail(msg: str) -> Env:
        if require_keys:
            raise SystemExit(msg)
        return Env(name, hosts["rest"], hosts["ws"], key_error=msg)

    if key_id is None and key_path is None:
        if require_keys:
            raise SystemExit(f"{name}: no API key configured. Set {_or(names['key_id'])} and "
                             f"{_or(names['key_path'])} in .env (the PEM file belongs outside the repo).")
        return Env(name, hosts["rest"], hosts["ws"])
    if key_id is None:
        return fail(f"{name}: the key path is set but the key id is missing; set {_or(names['key_id'])}.")
    if key_path is None:
        return fail(f"{name}: the key id is set but the key path is missing; set {_or(names['key_path'])}.")
    if not _PLAUSIBLE_KEY_ID.fullmatch(key_id):
        return fail(f"{name}: the value of {_id_var} does not look like a Kalshi API key id (a UUID); "
                    "check that the key id and the key path are not swapped.")
    try:
        key = load_private_key(_resolve_key_path(key_path), password)
    except FileNotFoundError:
        return fail(f"{name}: the private key file named by {path_var} does not exist.")
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        return fail(f"{name}: could not load the private key named by {path_var} ({type(exc).__name__}): "
                    "wrong or missing password, not a PEM file, or not an Ed25519/RSA key.")
    except OSError as exc:
        return fail(f"{name}: cannot read the private key file named by {path_var} ({type(exc).__name__}).")
    return Env(name, hosts["rest"], hosts["ws"], key_id=key_id, key=key, key_path=key_path)
