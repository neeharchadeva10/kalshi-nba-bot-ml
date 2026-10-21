"""kpm.execution.env: KALSHI_ENV, the allowlisted host table, per-environment keys (design section 3)."""
from __future__ import annotations

import dataclasses
import hashlib
import logging

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from kpm.core import config
from kpm.execution import env as env_mod
from kpm.execution.auth import key_type
from kpm.execution.env import ENV_NAMES, HOSTS, KEY_VARS, SECRET_ENV_NAMES, Env, load_env

from .conftest import pem_bytes

# Distinctive values so that any leak is easy to see. None of them is a real credential.
PROD_ID = "prodkey-0000-1111-2222-aaaaaaaaaaaa"
LEGACY_ID = "legacy00-0000-1111-2222-bbbbbbbbbbbb"
DEMO_ID = "demokey0-0000-1111-2222-cccccccccccc"
PW = "pw-UNIQUE-3141592653"
SECRET_DIR = "SUPERSECRETDIR"


@pytest.fixture
def pem_file(tmp_path, ed25519_key):
    d = tmp_path / SECRET_DIR
    d.mkdir()
    p = d / "prod_key.pem"
    p.write_bytes(pem_bytes(ed25519_key))
    return p


def setenv(monkeypatch, **values):
    for k, v in values.items():
        monkeypatch.setenv(k, str(v))


def exit_message(excinfo) -> str:
    return str(excinfo.value.code)


# ------------------------------------------------------------------------------------------------ the host table
def test_hosts_are_the_allowlisted_external_api_hosts():
    assert dict(HOSTS["prod"]) == {"rest": "https://external-api.kalshi.com/trade-api/v2",
                                   "ws": "wss://external-api-ws.kalshi.com/trade-api/ws/v2"}
    assert dict(HOSTS["demo"]) == {"rest": "https://external-api.demo.kalshi.co/trade-api/v2",
                                   "ws": "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"}
    assert set(HOSTS) == set(ENV_NAMES) == {"demo", "prod"}


def test_the_alternate_hosts_are_never_used():
    flat = " ".join(v for h in HOSTS.values() for v in h.values())
    for alt in ("api.elections.kalshi.com", "demo-api.kalshi.co", "demo.kalshi.com"):
        assert alt not in flat


def test_the_host_table_is_immutable():
    with pytest.raises(TypeError):
        HOSTS["prod"] = {}                                           # type: ignore[index]
    with pytest.raises(TypeError):
        HOSTS["prod"]["rest"] = "https://evil.example/trade-api/v2"  # type: ignore[index]
    with pytest.raises(TypeError):
        KEY_VARS["prod"]["key_id"] = ("X",)                          # type: ignore[index]


def test_settings_yaml_cannot_override_the_hosts(monkeypatch):
    """trading.base_url / ws_url in settings.yaml are ignored: the module reads only its own constant table."""
    monkeypatch.setattr(config, "settings", lambda *a, **k: {"trading": {"base_url": "https://evil.example/trade-api/v2",
                                                                         "ws_url": "wss://evil.example/ws"}})
    setenv(monkeypatch, KALSHI_ENV="prod")
    e = load_env(False)
    assert e.rest_base == HOSTS["prod"]["rest"] and "evil" not in e.rest_base + e.ws_url


# ------------------------------------------------------------------------------------------------ KALSHI_ENV
@pytest.mark.parametrize("require_keys", [True, False])
def test_kalshi_env_is_required_there_is_no_default(require_keys):
    with pytest.raises(SystemExit) as ei:
        load_env(require_keys)
    msg = exit_message(ei)
    assert "KALSHI_ENV is not set" in msg and "no default" in msg


def test_an_empty_kalshi_env_counts_as_not_set(monkeypatch):
    setenv(monkeypatch, KALSHI_ENV="")
    with pytest.raises(SystemExit, match="not set"):
        load_env(False)


@pytest.mark.parametrize("value", ["PROD", "Prod", "production", "Demo", "DEMO", "dev", " prod", "prod ", "demo\n",
                                   "live", "paper", "0", "true", "prod,demo", "prod;demo", "p r o d"])
def test_kalshi_env_must_be_exactly_demo_or_prod(monkeypatch, value):
    setenv(monkeypatch, KALSHI_ENV=value)
    with pytest.raises(SystemExit, match="exactly 'demo' or 'prod'"):
        load_env(False)


def test_a_garbage_kalshi_env_is_not_echoed_in_full(monkeypatch):
    setenv(monkeypatch, KALSHI_ENV="x" * 300)
    with pytest.raises(SystemExit) as ei:
        load_env(False)
    assert "x" * 30 not in exit_message(ei)


def test_the_env_flag_must_equal_kalshi_env(monkeypatch):
    setenv(monkeypatch, KALSHI_ENV="prod")
    with pytest.raises(SystemExit, match="does not match"):
        load_env(False, expected="demo")
    with pytest.raises(SystemExit, match="does not match"):
        load_env(False, expected="PROD")
    assert load_env(False, expected="prod").name == "prod"
    with pytest.raises(SystemExit) as ei:
        load_env(False, expected="y" * 300)
    assert "y" * 30 not in exit_message(ei)


def test_the_real_dotenv_is_not_read_by_tests():
    """If the project's real .env defined KALSHI_ENV, load_env would succeed here. It must not: .env loading is off."""
    with pytest.raises(SystemExit, match="not set"):
        load_env(False)


# ------------------------------------------------------------------------------------------------ watch-only (no keys)
@pytest.mark.parametrize("name", ["prod", "demo"])
def test_watch_only_needs_no_keys(monkeypatch, name):
    setenv(monkeypatch, KALSHI_ENV=name)
    e = load_env(require_keys=False)
    assert (e.name, e.rest_base, e.ws_url) == (name, HOSTS[name]["rest"], HOSTS[name]["ws"])
    assert not e.has_keys and e.key_id is None and e.key is None and e.key_fp is None and e.key_error is None
    assert e.banner() == {"env": name, "rest_host": HOSTS[name]["rest"].split("/")[2],
                          "ws_host": HOSTS[name]["ws"].split("/")[2], "keys": "none", "key_fp": None,
                          "key_type": None, "key_error": None}


def test_requiring_keys_without_any_names_the_variables_to_set(monkeypatch):
    setenv(monkeypatch, KALSHI_ENV="prod")
    with pytest.raises(SystemExit) as ei:
        load_env(True)
    msg = exit_message(ei)
    for var in ("KALSHI_PROD_KEY_ID", "KALSHI_API_KEY_ID", "KALSHI_PROD_KEY_PATH", "KALSHI_PRIVATE_KEY_PATH"):
        assert var in msg
    setenv(monkeypatch, KALSHI_ENV="demo")
    with pytest.raises(SystemExit) as ei:
        load_env(True)
    assert "KALSHI_DEMO_KEY_ID" in exit_message(ei) and "KALSHI_DEMO_KEY_PATH" in exit_message(ei)


# ------------------------------------------------------------------------------------------------ loading keys
def test_prod_loads_its_own_pair(monkeypatch, pem_file):
    setenv(monkeypatch, KALSHI_ENV="prod", KALSHI_PROD_KEY_ID=PROD_ID, KALSHI_PROD_KEY_PATH=pem_file)
    e = load_env(True)
    assert e.has_keys and e.key_id == PROD_ID and key_type(e.key) == "ed25519"
    assert e.key_fp == hashlib.sha256(PROD_ID.encode()).hexdigest()[:8]
    assert e.banner()["keys"] == "loaded" and e.banner()["key_type"] == "ed25519"


def test_prod_falls_back_to_the_legacy_single_pair(monkeypatch, pem_file):
    setenv(monkeypatch, KALSHI_ENV="prod", KALSHI_API_KEY_ID=LEGACY_ID, KALSHI_PRIVATE_KEY_PATH=pem_file)
    e = load_env(True)
    assert e.key_id == LEGACY_ID and e.has_keys


def test_prod_specific_variables_win_over_the_legacy_pair(monkeypatch, pem_file, tmp_path):
    setenv(monkeypatch, KALSHI_ENV="prod", KALSHI_PROD_KEY_ID=PROD_ID, KALSHI_PROD_KEY_PATH=pem_file,
           KALSHI_API_KEY_ID=LEGACY_ID, KALSHI_PRIVATE_KEY_PATH=tmp_path / "does-not-exist.pem")
    e = load_env(True)
    assert e.key_id == PROD_ID                                       # and the legacy (missing) file was never touched


def test_each_credential_is_looked_up_independently(monkeypatch, pem_file):
    setenv(monkeypatch, KALSHI_ENV="prod", KALSHI_PROD_KEY_ID=PROD_ID, KALSHI_PRIVATE_KEY_PATH=pem_file)
    assert load_env(True).key_id == PROD_ID


def test_demo_loads_only_the_demo_pair(monkeypatch, pem_file):
    setenv(monkeypatch, KALSHI_ENV="demo", KALSHI_DEMO_KEY_ID=DEMO_ID, KALSHI_DEMO_KEY_PATH=pem_file,
           KALSHI_PROD_KEY_ID=PROD_ID, KALSHI_API_KEY_ID=LEGACY_ID)
    e = load_env(True)
    assert e.name == "demo" and e.key_id == DEMO_ID and e.rest_base == HOSTS["demo"]["rest"]


@pytest.mark.parametrize("prod_vars", [
    {"KALSHI_PROD_KEY_ID": PROD_ID, "KALSHI_PROD_KEY_PATH": "p"},
    {"KALSHI_API_KEY_ID": LEGACY_ID, "KALSHI_PRIVATE_KEY_PATH": "p"},
])
def test_demo_never_uses_production_or_legacy_credentials(monkeypatch, pem_file, prod_vars):
    prod_vars = {k: (pem_file if v == "p" else v) for k, v in prod_vars.items()}
    setenv(monkeypatch, KALSHI_ENV="demo", **prod_vars)
    assert not load_env(False).has_keys                              # watch-only on demo: no keys, no error
    with pytest.raises(SystemExit, match="KALSHI_DEMO_KEY_ID"):
        load_env(True)


def test_only_the_selected_environments_key_file_is_opened(monkeypatch, pem_file, tmp_path):
    opened = []
    real = env_mod.load_private_key

    def spy(path, password=None):
        opened.append(str(path))
        return real(path, password)

    monkeypatch.setattr(env_mod, "load_private_key", spy)
    setenv(monkeypatch, KALSHI_ENV="prod", KALSHI_PROD_KEY_ID=PROD_ID, KALSHI_PROD_KEY_PATH=pem_file,
           KALSHI_DEMO_KEY_ID=DEMO_ID, KALSHI_DEMO_KEY_PATH=tmp_path / "demo-never-opened.pem")
    load_env(True)
    assert opened == [str(pem_file)]


@pytest.mark.parametrize("kind", ["ed25519-pkcs8", "rsa-pkcs1", "rsa-pkcs8"])
def test_ed25519_and_rsa_keys_both_load(monkeypatch, tmp_path, ed25519_key, rsa_key, kind):
    key, fmt = {"ed25519-pkcs8": (ed25519_key, "pkcs8"), "rsa-pkcs1": (rsa_key, "pkcs1"),
                "rsa-pkcs8": (rsa_key, "pkcs8")}[kind]
    p = tmp_path / "k.pem"
    p.write_bytes(pem_bytes(key, fmt))
    setenv(monkeypatch, KALSHI_ENV="prod", KALSHI_PROD_KEY_ID=PROD_ID, KALSHI_PROD_KEY_PATH=p)
    assert key_type(load_env(True).key) == kind.split("-")[0]


def test_an_encrypted_key_uses_the_password_variable(monkeypatch, tmp_path, ed25519_key):
    p = tmp_path / "enc.pem"
    p.write_bytes(pem_bytes(ed25519_key, "pkcs8", PW.encode()))
    setenv(monkeypatch, KALSHI_ENV="prod", KALSHI_PROD_KEY_ID=PROD_ID, KALSHI_PROD_KEY_PATH=p,
           KALSHI_PROD_KEY_PASSWORD=PW)
    assert load_env(True).has_keys
    setenv(monkeypatch, KALSHI_PROD_KEY_PASSWORD="wrong")
    with pytest.raises(SystemExit):
        load_env(True)


# ------------------------------------------------------------------------------------------------ broken key setups
def broken_cases(tmp_path, ed25519_key):
    d = tmp_path / SECRET_DIR
    d.mkdir(exist_ok=True)
    garbage = d / "garbage.pem"
    garbage.write_text("-----BEGIN PRIVATE KEY-----\nDISTINCTIVE-PEM-BODY\n-----END PRIVATE KEY-----\n")
    ec_key = d / "ec.pem"
    ec_key.write_bytes(pem_bytes(ec.generate_private_key(ec.SECP256R1())))
    enc = d / "enc.pem"
    enc.write_bytes(pem_bytes(ed25519_key, "pkcs8", PW.encode()))
    return {
        "missing file": (PROD_ID, d / "missing.pem", None),
        "garbage pem": (PROD_ID, garbage, None),
        "ec key": (PROD_ID, ec_key, None),
        "encrypted without password": (PROD_ID, enc, None),
        "encrypted wrong password": (PROD_ID, enc, "wrong-pw-UNIQUE"),
        "directory": (PROD_ID, d, None),
        "implausible id": ("short", None, None),
        "id and path swapped": ("PATHSWAP/" + str(d / "x.pem"), None, None),
        "key id missing": (OMIT, garbage, None),                   # the path is set, the id is not
        "key path missing": (PROD_ID, OMIT, None),                 # the id is set, the path is not
    }


CASES = ["missing file", "garbage pem", "ec key", "encrypted without password", "encrypted wrong password", "directory",
         "implausible id", "id and path swapped", "key id missing", "key path missing"]
OMIT = object()                                                    # the variable is not set at all


@pytest.mark.parametrize("case", CASES)
def test_broken_key_setups_fail_closed_when_keys_are_required_and_degrade_in_watch_only(
        monkeypatch, tmp_path, ed25519_key, pem_file, caplog, capsys, case):
    key_id, path, password = broken_cases(tmp_path, ed25519_key)[case]
    vars_ = {"KALSHI_ENV": "prod"}
    if key_id is not OMIT:
        vars_["KALSHI_PROD_KEY_ID"] = key_id
    if path is not OMIT:
        vars_["KALSHI_PROD_KEY_PATH"] = path if path is not None else pem_file
    if password:
        vars_["KALSHI_PROD_KEY_PASSWORD"] = password
    key_id, path = (None if key_id is OMIT else key_id), (None if path is OMIT else path)
    setenv(monkeypatch, **vars_)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(SystemExit) as ei:
        load_env(True)
    required_msg = exit_message(ei)
    watch = load_env(False)
    assert not watch.has_keys and watch.key is None and watch.key_id is None and watch.key_error
    assert watch.key_error == required_msg                          # the same explanation, no exit in watch-only
    assert watch.banner()["key_error"] == required_msg and watch.banner()["keys"] == "none"
    secrets = [str(s) for s in (key_id, path, pem_file, SECRET_DIR, "DISTINCTIVE-PEM-BODY", PW, "wrong-pw-UNIQUE")
               if s is not None]
    out = capsys.readouterr()
    for text in (required_msg, repr(watch), str(watch.banner()), caplog.text, out.out, out.err):
        for secret in secrets:
            assert secret not in text, f"{secret!r} leaked in {text[:120]!r}"
    assert "KALSHI_PROD_KEY" in required_msg                          # it names the VARIABLE to fix


def test_a_path_without_an_id_and_an_id_without_a_path_are_explained(monkeypatch, pem_file):
    setenv(monkeypatch, KALSHI_ENV="prod", KALSHI_PROD_KEY_PATH=pem_file)
    with pytest.raises(SystemExit, match="key id is missing"):
        load_env(True)
    assert "key id is missing" in load_env(False).key_error
    monkeypatch.delenv("KALSHI_PROD_KEY_PATH")
    setenv(monkeypatch, KALSHI_PROD_KEY_ID=PROD_ID)
    with pytest.raises(SystemExit, match="key path is missing"):
        load_env(True)
    assert "key path is missing" in load_env(False).key_error


def test_loading_a_good_key_prints_and_logs_nothing(monkeypatch, pem_file, caplog, capsys):
    setenv(monkeypatch, KALSHI_ENV="prod", KALSHI_PROD_KEY_ID=PROD_ID, KALSHI_PROD_KEY_PATH=pem_file)
    caplog.set_level(logging.DEBUG)
    e = load_env(True)
    out = capsys.readouterr()
    assert caplog.text == "" and out.out == "" and out.err == ""
    for text in (repr(e), str(e), str(e.banner())):
        assert PROD_ID not in text and SECRET_DIR not in text and "PRIVATE KEY" not in text
        assert str(pem_file) not in text


# ------------------------------------------------------------------------------------------------ key path resolution
def test_a_tilde_in_the_key_path_is_expanded(monkeypatch, tmp_path, ed25519_key):
    home = tmp_path / "home"
    (home / "keys").mkdir(parents=True)
    (home / "keys" / "k.pem").write_bytes(pem_bytes(ed25519_key))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    setenv(monkeypatch, KALSHI_ENV="prod", KALSHI_PROD_KEY_ID=PROD_ID, KALSHI_PROD_KEY_PATH="~/keys/k.pem")
    assert load_env(True).has_keys


def test_a_relative_key_path_is_tried_against_the_cwd_then_the_project_root(monkeypatch, tmp_path, ed25519_key):
    cwd, root, elsewhere = tmp_path / "cwd", tmp_path / "root", tmp_path / "elsewhere"
    for d in (cwd, root, elsewhere):
        d.mkdir()
    (cwd / "in_cwd.pem").write_bytes(pem_bytes(ed25519_key))
    (root / "in_root.pem").write_bytes(pem_bytes(ed25519_key))
    monkeypatch.setattr(config, "PROJECT_ROOT", root)
    monkeypatch.chdir(cwd)
    setenv(monkeypatch, KALSHI_ENV="prod", KALSHI_PROD_KEY_ID=PROD_ID, KALSHI_PROD_KEY_PATH="in_cwd.pem")
    assert load_env(True).has_keys
    setenv(monkeypatch, KALSHI_PROD_KEY_PATH="in_root.pem")
    assert load_env(True).has_keys
    setenv(monkeypatch, KALSHI_PROD_KEY_PATH="nowhere.pem")
    with pytest.raises(SystemExit, match="does not exist"):
        load_env(True)


# ------------------------------------------------------------------------------------------------ the Env object
def mk(name="prod", **kw):
    return Env(name, HOSTS[name]["rest"], HOSTS[name]["ws"], **kw)


def test_env_validates_its_name_and_hosts(ed25519_key):
    assert mk().name == "prod" and mk("demo").rest_host == "external-api.demo.kalshi.co"
    with pytest.raises(ValueError, match="demo or prod|one of"):
        Env("staging", "https://external-api.kalshi.com/trade-api/v2", "wss://external-api-ws.kalshi.com/trade-api/ws/v2")
    bad_hosts = [
        ("https://api.elections.kalshi.com/trade-api/v2", HOSTS["prod"]["ws"]),
        ("https://external-api.demo.kalshi.co/trade-api/v2", HOSTS["prod"]["ws"]),     # demo host on a prod Env
        ("http://external-api.kalshi.com/trade-api/v2", HOSTS["prod"]["ws"]),
        ("https://external-api.kalshi.com/trade-api/v2/", HOSTS["prod"]["ws"]),
        ("https://evil.example/trade-api/v2", HOSTS["prod"]["ws"]),
        (HOSTS["prod"]["rest"], "wss://api.elections.kalshi.com/trade-api/ws/v2"),
        (HOSTS["prod"]["rest"], HOSTS["demo"]["ws"]),
    ]
    for rest, ws in bad_hosts:
        with pytest.raises(ValueError, match="allowlisted|HOSTS"):
            Env("prod", rest, ws)


def test_env_requires_key_and_key_id_together_and_a_consistent_fingerprint(ed25519_key):
    with pytest.raises(ValueError, match="together"):
        mk(key_id=PROD_ID)
    with pytest.raises(ValueError, match="together"):
        mk(key=ed25519_key)
    with pytest.raises(ValueError, match="without a key"):
        mk(key_fp="deadbeef")
    with pytest.raises(ValueError, match="does not match"):
        mk(key_id=PROD_ID, key=ed25519_key, key_fp="deadbeef")
    with pytest.raises(TypeError):
        mk(key_id=PROD_ID, key=ec.generate_private_key(ec.SECP256R1()))               # only Ed25519 / RSA
    e = mk(key_id=PROD_ID, key=ed25519_key)
    assert e.key_fp == hashlib.sha256(PROD_ID.encode()).hexdigest()[:8]
    assert mk(key_id=PROD_ID, key=ed25519_key, key_fp=e.key_fp).has_keys


def test_env_is_frozen_and_never_prints_its_secrets(ed25519_key):
    e = mk(key_id=PROD_ID, key=ed25519_key)
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.name = "demo"                                                                # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.key_id = "other"                                                             # type: ignore[misc]
    for text in (repr(e), str(e), str(e.banner()), f"{e}", format(e), repr([e]), repr({"env": e})):
        assert PROD_ID not in text and "Ed25519" not in text and "PRIVATE" not in text
    assert set(e.banner()) == {"env", "rest_host", "ws_host", "keys", "key_fp", "key_type", "key_error"}
    assert e.banner()["key_fp"] == e.key_fp and e.rest_host == "external-api.kalshi.com"
    assert e.ws_host == "external-api-ws.kalshi.com"


def test_secret_variable_names_are_registered_with_the_shared_config():
    expected = {"KALSHI_DEMO_KEY_ID", "KALSHI_DEMO_KEY_PATH", "KALSHI_DEMO_KEY_PASSWORD", "KALSHI_PROD_KEY_ID",
                "KALSHI_PROD_KEY_PATH", "KALSHI_PROD_KEY_PASSWORD", "KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PATH"}
    assert expected <= SECRET_ENV_NAMES and expected <= config.SECRET_KEYS
    assert "KALSHI_ENV" not in SECRET_ENV_NAMES                    # the environment NAME is not a secret


def test_the_shared_registry_itself_lists_every_secret_name():
    """kpm.core.config.SECRET_KEYS must be complete on its own: importing kpm.execution.env must not be what fills it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_fresh_kpm_config", config.__file__)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    assert SECRET_ENV_NAMES <= fresh.SECRET_KEYS
    assert "ANTHROPIC_API_KEY" in fresh.SECRET_KEYS and "KALSHI_ENV" not in fresh.SECRET_KEYS


def test_env_example_names_every_variable_and_holds_no_value():
    """.env.example is what a new user copies: it must list every variable env.py reads, with no default environment and
    no value filled in (a real key id or path committed there would be a leak)."""
    text = (config.PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assigned = {}
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            assigned[k.strip()] = v.strip()
    assert assigned["KALSHI_ENV"] == ""                                      # no default: the user must choose
    assert all(v == "" for v in assigned.values())
    for name in SECRET_ENV_NAMES | {"KALSHI_ENV"}:
        assert name in text, name


def test_redact_values_are_the_key_id_and_the_key_path_and_never_show_in_a_repr(monkeypatch, pem_file):
    setenv(monkeypatch, KALSHI_ENV="prod", KALSHI_PROD_KEY_ID=PROD_ID, KALSHI_PROD_KEY_PATH=pem_file)
    e = load_env(True)
    assert e.redact_values == (PROD_ID, str(pem_file))
    for text in (repr(e), str(e), str(e.banner())):
        assert PROD_ID not in text and str(pem_file) not in text and SECRET_DIR not in text


def test_redact_values_are_empty_without_a_key(monkeypatch):
    setenv(monkeypatch, KALSHI_ENV="prod")
    assert load_env(False).redact_values == ()
    assert mk().redact_values == ()


def test_the_ledger_scrubs_the_values_the_env_offers_for_redaction(monkeypatch, pem_file, tmp_path):
    from kpm.core import db
    from kpm.execution.ledger import Ledger
    setenv(monkeypatch, KALSHI_ENV="prod", KALSHI_PROD_KEY_ID=PROD_ID, KALSHI_PROD_KEY_PATH=pem_file)
    e = load_env(True)
    conn = db.connect(tmp_path / "r.sqlite")
    db.init_schema(conn)
    led = Ledger(conn, "run-r", "prod", log_path=tmp_path / "exec.jsonl", redact=e.redact_values)
    led.audit_request(method="GET", path="/portfolio/balance", status=200, error=f"failed for {PROD_ID} at {pem_file}",
                      response='{"note": "' + PROD_ID + '"}')
    dump = " ".join(str(tuple(r)) for r in conn.execute("SELECT * FROM exec_requests"))
    dump += (tmp_path / "exec.jsonl").read_text(encoding="utf-8")
    assert PROD_ID not in dump and SECRET_DIR not in dump and "[redacted]" in dump
