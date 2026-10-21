"""The whole stack for one CLI test (not a test module): environment variables, a scripted exchange, a database file, the
CLI's Deps and its output. Shared by test_cli.py (watch-only, arguments), test_cli_live.py (gates, sessions) and
test_cli_exec.py (kpm exec ...).

Real: Env (load_env from environment variables and a throwaway PEM), ExecClient (allowlist, signing, parsing), Ledger,
Safety (every live gate), reconcile, BotState and both strategies. Fake: the HTTP wire (sim_http.SimHttp on a
FakeTransport), the clock (FakeClock: sleep advances it) and the terminal. The network guard in conftest.py blocks
everything else, and the transport refuses any host but the environment's allowlisted one.
"""
from __future__ import annotations

import argparse
import sqlite3
from decimal import Decimal as D
from pathlib import Path
from urllib.parse import urlparse

import yaml

from kpm.core import db
from kpm.execution import cli, units
from kpm.execution import env as env_mod
from kpm.execution.env import HOSTS

from .conftest import TEST_KEY_ID, FakeTransport, pem_bytes
from .exec_fakes import CLOSE_TS, TICKER
from .sim_http import SimHttp

SETTINGS = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
PHRASE = "LIVE PROD 5"
FEE_73 = units.taker_fee(D(1), D("0.73"))             # 0.07 * 0.73 * 0.27 rounded up to $0.000001


class World:
    def __init__(self, tmp_path: Path, monkeypatch, key, clock, *, keys: bool = True, env: str = "prod",
                 enabled: bool = False, trading: dict | None = None, start_offset: float = -130.0,
                 book: tuple[str, str] | None = ("0.72", "0.27"), result: str = "yes", isatty: bool = True,
                 confirm=PHRASE, ahead: float = 0.0, close_ts: float = CLOSE_TS, ticker: str = TICKER):
        self.tmp, self.clock, self.key = tmp_path, clock, key
        self.close_ts = close_ts
        clock.now = close_ts + start_offset
        self.kill = tmp_path / "KILL"
        cfg = dict(yaml.safe_load(SETTINGS.read_text(encoding="utf-8"))["trading"])    # the real file, plus overrides
        cfg.update(enabled=enabled, kill_switch_file=str(self.kill))
        cfg.update(trading or {})
        self.trading = cfg
        monkeypatch.setenv("KALSHI_ENV", env)
        self.pem = tmp_path / "secret_dir" / "key.pem"
        if keys:
            self.pem.parent.mkdir()
            self.pem.write_bytes(pem_bytes(key))
            prefix = "KALSHI_PROD" if env == "prod" else "KALSHI_DEMO"
            monkeypatch.setenv(f"{prefix}_KEY_ID", TEST_KEY_ID)
            monkeypatch.setenv(f"{prefix}_KEY_PATH", str(self.pem))
        self.t = FakeTransport(host=urlparse(HOSTS[env]["rest"]).netloc, server_clock=lambda: clock.now - ahead)
        self.sim = SimHttp(self.t, clock, close_ts=close_ts, ticker=ticker, book=book, result=result)
        self.out: list[str] = []
        self.db_path = tmp_path / "cli.sqlite"
        self.deps = cli.Deps(settings=lambda: {"trading": self.trading}, load_env=env_mod.load_env,
                             connect=self.connect, transport=self.t, clock=clock.time, sleep=clock.sleep,
                             isatty=lambda: isatty, confirm=confirm, out=self.out.append, install_signals=False,
                             log_path=tmp_path / "exec.jsonl")

    def connect(self) -> sqlite3.Connection:
        conn = db.connect(self.db_path)
        db.init_schema(conn)
        return conn

    # ---- commands
    def bot_args(self, **kw) -> argparse.Namespace:
        d = dict(cmd="bot", bot_cmd="run", strategy="late_favorite", live=False, env=None, hours=0.1, smoke=False)
        d.update(kw)
        return argparse.Namespace(**d)

    def run_bot(self, **kw) -> int:
        return cli.run_bot(self.bot_args(**kw), self.deps)

    def exec_args(self, name: str, **kw) -> argparse.Namespace:
        return argparse.Namespace(cmd="exec", exec_cmd=name, **({"env": None} | kw))

    def run_exec(self, name: str, **kw) -> int:
        return cli.run_exec(self.exec_args(name, **kw), self.deps)

    # ---- reading back
    def q(self, sql: str, *params) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()

    @property
    def text(self) -> str:
        return "\n".join(self.out)

    def everything(self) -> str:
        """Every table row, the audit mirror and the console output as one string: for 'no secret anywhere' checks."""
        parts = [self.text]
        if self.db_path.exists():
            conn = sqlite3.connect(self.db_path)
            try:
                for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
                    if name in ("markets", "price_history", "trades", "trades_1m", "trades_final"):
                        continue
                    parts.extend(" ".join(str(x) for x in row) for row in conn.execute(f"SELECT * FROM {name}"))
            finally:
                conn.close()
        for log in (self.tmp / "exec.jsonl", self.tmp / "seed.jsonl"):
            if log.exists():
                parts.append(log.read_text(encoding="utf-8"))
        return "\n".join(parts)

    # ---- seeding what an earlier run left behind
    def ledger(self, run_id: str = "earlier-run"):
        from kpm.execution.ledger import Ledger
        return Ledger(self.connect(), run_id, "prod", clock=self.clock.time, log_path=self.tmp / "seed.jsonl")

    def rest_an_order(self, order_id: str = "sim-rest-1") -> str:
        """A GTC bid that rests on the exchange and that an earlier run recorded: what the cancel path must find."""
        from .exec_fakes import base_market, entry_spec, ok_result
        spec = entry_spec(base_market(), "yes", "0.40")
        spec["time_in_force"] = "good_till_canceled"
        led = self.ledger("old-run")
        coid = led.new_intent(spec)
        led.mark_result(coid, ok_result(coid, order_id, fill="0.00", remaining="1.00"))
        self.sim.add_resting(coid, order_id=order_id)
        return coid


def refused(w: World, rc: int, *gate_names: str) -> None:
    """The live start was refused: exit 1, the failed gates are named, and not one write left the process."""
    assert rc == cli.EXIT_FAIL, w.text
    assert "LIVE MODE REFUSED. Nothing was sent." in w.text
    for g in gate_names:
        assert f"FAIL {g}:" in w.text, (g, w.text)
    assert w.t.writes == [] and not w.sim.created
    assert w.q("SELECT COUNT(*) AS n FROM bot_runs")[0]["n"] == 0                        # no run row: it never started
    assert w.q("SELECT COUNT(*) AS n FROM exec_orders WHERE run_id != 'earlier-run'")[0]["n"] == 0   # (seeded rows aside)
