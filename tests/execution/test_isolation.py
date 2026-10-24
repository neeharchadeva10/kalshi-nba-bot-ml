"""Isolation (design test-plan case 13): research code never imports kpm.execution, the read-only research client keeps
refusing portfolio paths, and the execution modules keep their security posture (no redirects, no http, no research code)."""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import requests

from kpm.clients import http as research_http
from kpm.core import config

from .conftest import NetworkBlocked

SRC = Path(__file__).resolve().parents[2] / "src" / "kpm"
EXEC = SRC / "execution"
OWNED = ("__init__", "units", "auth", "env", "client")                    # the modules this test directory covers first
RESEARCH_PACKAGES = ("analysis", "collectors", "clients", "core", "scanner", "reports", "agents")


def imported_modules(path: Path) -> set[str]:
    """Absolute dotted names a file imports, with relative imports resolved against its package."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = ["kpm"] + list(path.relative_to(SRC).parts[:-1])
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            found.add(mod)
            found.update(f"{mod}.{a.name}" for a in node.names)
    return found


def py_files(package: str) -> list[Path]:
    return sorted((SRC / package).rglob("*.py"))


@pytest.mark.parametrize("package", RESEARCH_PACKAGES)
def test_research_code_never_imports_the_execution_package(package):
    offenders = []
    for f in py_files(package):
        text = f.read_text(encoding="utf-8")
        mods = imported_modules(f)
        if any(m == "kpm.execution" or m.startswith("kpm.execution.") for m in mods) or re.search(r"kpm\.execution", text):
            offenders.append(str(f.relative_to(SRC)))
    assert offenders == []


def test_the_research_packages_exist_so_the_scan_is_not_vacuous():
    assert sum(len(py_files(p)) for p in RESEARCH_PACKAGES) > 30
    assert (SRC / "clients" / "http.py").is_file() and (SRC / "execution" / "client.py").is_file()


@pytest.mark.parametrize("module", OWNED)
def test_the_execution_core_never_reuses_the_research_clients(module):
    """The execution client is deliberately separate from CachedClient (permanent disk cache, automatic retries)."""
    mods = imported_modules(EXEC / f"{module}.py")
    for forbidden in ("kpm.clients", "kpm.collectors", "kpm.analysis", "kpm.scanner", "kpm.reports"):
        assert not any(m == forbidden or m.startswith(forbidden + ".") for m in mods), (module, forbidden)


def run_python(code: str) -> str:
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                         env={**os.environ, "PYTHONPATH": str(SRC.parent)})
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_importing_the_package_imports_no_submodule():
    code = ("import sys, kpm.execution\n"
            "print(sorted(m for m in sys.modules if m.startswith('kpm.execution.')))")
    assert run_python(code) == "[]"


def test_units_and_auth_pull_in_no_network_library():
    code = ("import sys\n"
            "import kpm.execution.units\n"
            "a = sorted(m for m in ('requests', 'cryptography', 'urllib3', 'websockets') if m in sys.modules)\n"
            "import kpm.execution.auth\n"
            "b = sorted(m for m in ('requests', 'urllib3', 'websockets') if m in sys.modules)\n"
            "print(a, b)")
    assert run_python(code) == "[] []"


# ------------------------------------------------------------------------------------------------ CachedClient
@pytest.fixture
def cached_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "path", lambda key: tmp_path)
    return research_http.CachedClient("https://external-api.kalshi.com/trade-api/v2", "isolation-test")


@pytest.mark.parametrize("path", [
    "/portfolio/balance", "/portfolio/positions", "/portfolio/orders", "/portfolio/fills", "/portfolio/settlements",
    "/portfolio/events/orders", "/portfolio/events/orders/abc", "/portfolio/orders/abc/queue_position",
    "/trade-api/v2/portfolio/balance", "https://external-api.kalshi.com/trade-api/v2/portfolio/balance?x=1",
    "/PORTFOLIO/Balance", "/markets/orders", "/markets/KX/fills", "/positions", "/balance", "/order",
])
def test_cached_client_still_refuses_portfolio_and_trading_paths(cached_client, path, network_attempts):
    with pytest.raises(PermissionError, match="Refusing non-market-data path"):
        cached_client.get(path)
    assert not network_attempts                                                  # refused before any request


def test_cached_client_still_reads_public_market_data_paths(cached_client, network_attempts):
    """The guard is per path segment: public data such as the orderbook is not caught by it (it reaches the
    network layer, which the test guard blocks)."""
    with pytest.raises(NetworkBlocked):                                          # not PermissionError("Refusing ...")
        cached_client.get("/markets/KXBTC15M-26OCT051430-30/orderbook", use_cache=False)
    assert network_attempts                                                      # it did try to fetch
    network_attempts.clear()


def test_cached_client_is_get_only():
    for name in ("post", "put", "delete", "patch", "request", "send"):
        assert not hasattr(research_http.CachedClient, name)
    assert {"portfolio", "orders", "fills", "positions", "balance"} <= research_http.FORBIDDEN_SEGMENTS


def test_the_guard_is_on_by_default():
    import inspect
    assert inspect.signature(research_http.CachedClient.__init__).parameters["forbid_trading_paths"].default is True


# ------------------------------------------------------------------------------------------------ security posture
SOURCES = {name: (EXEC / f"{name}.py").read_text(encoding="utf-8") for name in OWNED}


@pytest.mark.parametrize("name", OWNED)
def test_no_insecure_request_settings_in_the_execution_core(name):
    src = SOURCES[name]
    assert "verify=False" not in src and "verify = False" not in src
    assert "allow_redirects=True" not in src and "allow_redirects = True" not in src
    assert not re.search(r"""["']http://""", src), "plain-http URLs must not appear in the execution core"
    assert "disable_warnings" not in src and not re.search(r"^\s*(import|from)\s+urllib3", src, re.M)
    assert not re.search(r"\brequests\.(get|post|put|patch|delete|head|request)\(", src)    # only via the one session


def test_only_the_transport_class_touches_the_requests_session():
    src = SOURCES["client"]
    assert src.count("allow_redirects=False") == 1 and "trust_env = False" in src and "max_redirects = 0" in src
    assert len(re.findall(r"\._session\.request\(", src)) == 1


def test_secrets_are_read_in_exactly_one_module():
    """Only env.py may read the key id / key path from the environment (design 2: secrets)."""
    for name in OWNED:
        src = SOURCES[name]
        reads_env = bool(re.search(r"os\.environ|os\.getenv|config\.secret|dotenv|read_bytes\(|open\(", src))
        assert reads_env == (name in ("env", "auth")), name                       # auth only reads the PEM it is handed
    assert "config.secret" in SOURCES["env"] and "config.secret" not in SOURCES["auth"]


def test_no_module_prints_or_logs_secrets_by_construction():
    for name in ("units", "auth", "env"):
        assert not re.search(r"\bprint\(", SOURCES[name]) and "logging" not in SOURCES[name], name
    client = SOURCES["client"]
    assert not re.search(r"\bprint\(", client)
    for m in re.finditer(r"log\.\w+\(([^)]*)\)", client):                          # the client logs only the audit failure
        assert "key" not in m.group(1).lower() and "sign" not in m.group(1).lower()


def test_requests_is_not_imported_by_units_auth_or_env():
    for name in ("units", "auth", "env"):
        assert "import requests" not in SOURCES[name]
    assert requests.__name__ == "requests"
