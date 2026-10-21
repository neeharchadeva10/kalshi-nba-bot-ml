"""Command line for the execution package (wired into ``kpm`` by kpm/cli.py).

    kpm exec check [--env prod]       read-only health check: config, gates, clock, shards, cash, resting orders
    kpm exec kill                     write the kill file, then run the cancel path (its cancels are the only writes)
    kpm exec reconcile [--env prod]   bring the ledger in line with the exchange and record settlements; reads only
    kpm bot run --strategy late_favorite|pair_h2 [--live] [--env prod] [--hours N] [--smoke]

``kpm bot run`` is WATCH-ONLY by default: public reads only, full detection, paper twins and dashboard state, no keys
needed, nothing sent. ``--live`` adds the live gates (all required, enforced by kpm.execution.safety):

    trading.enabled is true in config/settings.yaml, the --live flag, KALSHI_ENV=prod, the --env prod flag, no kill
    file, the PC clock within 2 s of Kalshi's (median of 3 probes), the loss cap not used up, no order with an unknown
    outcome, and the phrase "LIVE PROD 5" typed at an interactive terminal (no TTY: refused).

Only one live run at a time, because two live bots would double every trade. A live start first takes an exclusive
OS-level lock (``LIVE.lock`` next to the kill file, held until the process ends or the run finishes, so a crash never
leaves a stale one), BEFORE it asks for the confirmation: a second ``--live`` start while the first still waits at the
prompt is refused, not only one that starts after the first run has written its heartbeat. The heartbeat check stays as a
second line (and is repeated after the confirmation), and a database that cannot be read means "cannot tell": refused.
A live run's database connection waits at most 10 s for a lock (a busy database stops the run instead of stalling it).

Every dependency (settings, environment loader, database, transport, clock, prompt) is injectable through :class:`Deps`,
so the tests run this exact wiring on fakes. Output is ASCII and never shows the key id, the key path or a signature:
the only identifier printed is ``key_fp``, the first 8 hex digits of the key id's SHA-256.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

from kpm.execution import botstate, units
from kpm.execution.client import ExecClient
from kpm.execution.ledger import Ledger
from kpm.execution.safety import (LiveGateError, Safety, SafetyConfigError, kill_file_path, parse_limits,
                                  write_kill_file)

log = logging.getLogger(__name__)

STRATEGIES = ("late_favorite", "pair_h2")
ENVS = ("demo", "prod")
ACTIVE_HEARTBEAT_S = 30.0          # another live run whose heartbeat is younger than this is "running"
LIVE_LOCK_NAME = "LIVE.lock"       # next to the kill file (data/LIVE.lock)
LIVE_BUSY_TIMEOUT_MS = 10_000      # a live run's connection waits at most this long for a database lock
EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_INTERRUPTED = 0, 1, 2, 130
CLEAN_END_REASONS = ("finished", "smoke pass done")   # any other end reason of a bot run is a stop, not a normal end


# ============================================================================================ dependencies
def _print(text: str) -> None:
    """print() that never dies on a console that cannot show a character."""
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"), flush=True)


@dataclass
class Deps:
    """Everything the commands touch outside this module. The defaults are the production wiring."""

    settings: Callable[[], Mapping[str, Any]]
    load_env: Callable[..., Any]                      # kpm.execution.env.load_env(require_keys, expected=...)
    connect: Callable[[], sqlite3.Connection]         # the database, schema ensured
    transport: Any = None                             # ExecClient transport; None = real HTTPS (requests)
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], Any] = time.sleep
    isatty: Callable[[], bool] | None = None          # None = sys.stdin.isatty()
    confirm: Any = None                               # the typed confirmation (tests); None = ask with input()
    out: Callable[[str], Any] = _print
    install_signals: bool = True                      # SIGBREAK / SIGTERM -> KeyboardInterrupt during a live run
    log_path: Any = None                              # the audit mirror (logs/exec.jsonl when None); tests redirect it


def default_deps() -> Deps:
    from kpm.core import config, db
    from kpm.execution import env as env_mod

    def connect() -> sqlite3.Connection:
        conn = db.connect()
        db.init_schema(conn)
        return conn

    return Deps(settings=config.settings, load_env=env_mod.load_env, connect=connect)


# ============================================================================================ shared pieces
def _trading(deps: Deps) -> dict:
    cfg = deps.settings()
    trading = cfg.get("trading") if isinstance(cfg, Mapping) else None
    return dict(trading) if isinstance(trading, Mapping) else {}


def _gate_lines(gates: Sequence[Any]) -> list[str]:
    return [f"  {'ok  ' if g.ok else 'FAIL'} {g.name}: {g.detail}" for g in gates]


def _check_lines(checks: Sequence[Mapping[str, Any]]) -> list[str]:
    return [f"  {'ok  ' if c['ok'] else 'FAIL'} {c['name']}: {c.get('detail', '')}" for c in checks]


def _client(deps: Deps, env: Any, ledger: Ledger | None, *, live: bool) -> ExecClient:
    return ExecClient(env, live=live, audit=None if ledger is None else ledger.audit_request,
                      transport=deps.transport, clock=deps.clock, sleep=deps.sleep)


def _ledger(deps: Deps, conn: sqlite3.Connection, run_id: str, env: Any) -> Ledger:
    # redact: the storage layer scrubs the key id and key path from every row and log line, even if a caller passes one
    return Ledger(conn, run_id, env.name, clock=deps.clock, log_path=deps.log_path, redact=env.redact_values)


def _run_id(deps: Deps, prefix: str) -> str:
    return botstate.new_run_id(prefix, deps.clock())


def _other_live_run(conn: sqlite3.Connection, now: float) -> dict | None:
    """A live run (not ended) whose heartbeat is fresh, or None. Two live bots would double every trade.

    Raises ``sqlite3.Error`` when the database cannot be read: "no other run" must never be the answer to "cannot tell"."""
    row = conn.execute(
        "SELECT r.run_id, r.strategy, h.ts FROM bot_runs r JOIN bot_heartbeat h ON h.run_id = r.run_id "
        "WHERE r.mode = 'live' AND r.ended_ts IS NULL AND h.ts >= ? ORDER BY h.ts DESC LIMIT 1",
        (now - ACTIVE_HEARTBEAT_S,)).fetchone()
    return None if row is None else {"run_id": row[0], "strategy": row[1], "heartbeat_age_s": now - float(row[2])}


def live_lock_path(trading: Mapping[str, Any]) -> Path:
    """``LIVE.lock`` in the directory of the kill file (data/ by default)."""
    return kill_file_path(trading).parent / LIVE_LOCK_NAME


class LiveRunLock:
    """An exclusive lock for the whole live process, held until :meth:`release` or until the process ends.

    One byte of ``LIVE.lock`` is locked with the operating system's own mechanism (msvcrt.locking on Windows, flock
    elsewhere), so the lock goes away with the process however it ends and there is never a stale lock to clean up. A
    second opening of the file in the SAME process conflicts too (Windows locks belong to the handle, flock to the open
    file description), which is what the tests use. The file also records who holds it, for the refusal message."""

    _NOTE_AT = 16                                    # the locked byte is 0; the note starts after it so others can read it

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fh: Any = None

    def acquire(self, who: str = "") -> tuple[bool, str]:
        """``(True, "")`` when this process now holds the lock, else ``(False, who holds it / why it cannot be taken)``."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(self.path, "a+b")
        except OSError as exc:
            return False, f"cannot open {self.path} ({type(exc).__name__})"
        try:
            self._lock(fh)
        except OSError:
            holder = self._read_note()
            fh.close()
            return False, holder or "no details recorded"
        self._fh = fh
        try:
            fh.seek(self._NOTE_AT)
            fh.truncate(self._NOTE_AT)
            fh.write(f"pid {os.getpid()} {who}".strip().encode("ascii", "replace")[:200])
            fh.flush()
        except OSError:
            pass                                      # the note is a courtesy; the lock is what matters
        return True, ""

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            self._unlock(fh)
        except OSError:
            pass
        try:
            fh.close()
        except OSError:
            pass

    def _lock(self, fh: Any) -> None:
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(self, fh: Any) -> None:
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _read_note(self) -> str:
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self._NOTE_AT)
                return fh.read(200).decode("ascii", "replace").strip()
        except OSError:
            return ""


def _guarded(deps: Deps, fn: Callable[[Any, Deps], int], args: Any) -> int:
    """Run a command; turn the expected refusals into a message and an exit code, never a traceback."""
    try:
        return fn(args, deps)
    except SafetyConfigError as exc:
        deps.out(f"refusing to start: {exc}")
        return EXIT_USAGE
    except LiveGateError as exc:
        deps.out("LIVE MODE REFUSED. Nothing was sent.")
        for line in _gate_lines(exc.gates):
            deps.out(line)
        return EXIT_FAIL
    except PermissionError as exc:
        deps.out(f"refused: {exc}")
        return EXIT_FAIL
    except ValueError as exc:                          # a strategy setting past its ceiling, a bad argument ...
        deps.out(f"refusing to start: {exc}")
        return EXIT_USAGE
    except KeyboardInterrupt:
        deps.out("interrupted (Ctrl+C)")
        return EXIT_INTERRUPTED


# ============================================================================================ kpm exec ...
def run_exec(args: Any, deps: Deps | None = None) -> int:
    """Dispatch ``kpm exec <check|kill|reconcile>``. Returns the process exit code."""
    if args.exec_cmd == "record":                      # read-only market-data recorder; no ledger, no client
        from kpm.execution import ws_recorder
        return ws_recorder.cli_main(args)
    deps = deps or default_deps()
    handlers = {"check": _exec_check, "kill": _exec_kill, "reconcile": _exec_reconcile}
    return _guarded(deps, handlers[args.exec_cmd], args)


def _exec_check(args: Any, deps: Deps) -> int:
    """Read-only: the config is inside every ceiling, the environment, key, clock, shards and cash are as live trading
    needs them, nothing unknown is open. Authenticated reads only when a key is loaded. Sends no write."""
    trading = _trading(deps)
    parse_limits(trading)                              # SafetyConfigError: a value past a ceiling
    env = deps.load_env(False, expected=args.env)
    conn = deps.connect()
    client = None
    try:
        out = deps.out
        ledger = _ledger(deps, conn, _run_id(deps, "exec-check"), env)
        client = _client(deps, env, ledger, live=False)
        safety = Safety(trading, ledger, client, deps.clock, sleep=deps.sleep, isatty=deps.isatty)
        out("kpm exec check (read-only: no order, cancel or transfer is sent)")
        for line in safety.banner():
            out("  " + line)
        out(f"trading.enabled in config/settings.yaml: {'true' if safety.enabled else 'false'} "
            "(live trading also needs --live, --env prod and the typed confirmation)")
        report = safety.diagnostics(args.env)
        out("checks:")
        for line in _check_lines(report["checks"]):
            out(line)
        if env.has_keys:
            for line in _account_lines(client):
                out(line)
        else:
            out("account: no API key loaded, so cash, positions and orders were not read (watch-only needs none)")
        s = ledger.summary()
        out(f"ledger: realized loss ${s['realized_loss_total']} (24 h ${s['realized_loss_24h']}), open worst case "
            f"${s['open_worst_case']}, open orders {s['open_orders']}, unknown outcomes {s['unresolved_orders']}")
        used = report["status"].get("loss_used_usd")
        out(f"loss cap: {'unknown' if used is None else '$' + str(used)} of ${report['status']['max_loss_usd']} used")
        if report["ok"]:
            out("READY: every check passed. Live trading still needs --live, --env prod and the typed confirmation.")
            return EXIT_OK
        failed = [c["name"] for c in report["checks"] if not c["ok"]]
        out(f"NOT READY for live trading: {len(failed)} check(s) failed ({', '.join(failed)}). "
            "Watch-only runs do not need them.")
        return EXIT_FAIL
    finally:
        if client is not None:
            client.close()
        conn.close()


def _account_lines(client: ExecClient) -> list[str]:
    """Cash, positions, resting orders and the rate-limit tier, from authenticated GETs. Each read may fail alone."""
    lines: list[str] = []

    def attempt(label: str, fn: Callable[[], list[str]]) -> None:
        try:
            lines.extend(fn())
        except Exception as exc:
            lines.append(f"{label}: could not be read ({type(exc).__name__})")

    def cash() -> list[str]:
        data = client.balance(None)
        out = [f"cash on all shards: ${data['balance_dollars']}"]
        for row in data.get("balance_breakdown") or ():
            if isinstance(row, Mapping):
                out.append(f"  shard {row.get('exchange_index')}: ${row.get('balance')}")
        return out

    def positions() -> list[str]:
        held = [p for p in client.positions() if isinstance(p, Mapping) and units.dec(p.get("position_fp") or "0") != 0]
        return [f"open positions: {len(held)}"] + [f"  {p.get('ticker')}: {p.get('position_fp')}" for p in held[:5]]

    def resting() -> list[str]:
        rows = [o for o in client.orders(status="resting") if isinstance(o, Mapping)]
        return [f"resting orders: {len(rows)}" + ("  <- cancel them (kpm exec kill) before live trading" if rows else "")]

    def limits() -> list[str]:
        d = client.account_limits()
        return [f"rate limits: tier {d.get('usage_tier')}, read bucket {(d.get('read') or {}).get('bucket_capacity')}, "
                f"write bucket {(d.get('write') or {}).get('bucket_capacity')}"]

    attempt("cash", cash)
    attempt("positions", positions)
    attempt("resting orders", resting)
    attempt("rate limits", limits)
    return lines


def _exec_kill(args: Any, deps: Deps) -> int:
    """Write the kill file (every bot sees it within a second and stops), then try to cancel whatever is resting."""
    out = deps.out
    trading = _trading(deps)
    path = write_kill_file(kill_file_path(trading), "kpm exec kill")
    out(f"kill file written: {path}")
    out("every running bot stops sending orders at its next check (at least once a second) and cancels what rests. "
        "To trade again: delete that file, then start a new run.")
    try:
        env = deps.load_env(False)
    except SystemExit as exc:
        out(f"cancel path not run: {exc.code}")
        return EXIT_OK
    if not env.has_keys:
        out("cancel path not run: no API key is loaded (the kill file still stops every running bot).")
        return EXIT_OK
    from kpm.execution import reconcile
    conn = deps.connect()
    client = None
    try:
        ledger = _ledger(deps, conn, _run_id(deps, "exec-kill"), env)
        client = _client(deps, env, ledger, live=True)         # live only so that cancels can be sent; nothing else is
        rep = reconcile.cancel_path(client, ledger, "kpm exec kill", sleep=deps.sleep, clock=deps.clock)
        out(f"cancel path: {len(rep.targets)} order(s) found, {len(rep.cancelled)} cancelled, verified "
            f"{rep.verified}, still resting {len(rep.remaining)}, cancel-all used {rep.cancel_all_used}")
        for err in rep.errors:
            out(f"  note: {err}")
        return EXIT_OK if rep.ok else EXIT_FAIL
    finally:
        if client is not None:
            client.close()
        conn.close()


def _exec_reconcile(args: Any, deps: Deps) -> int:
    """Reconcile every open or unsettled ticker, then record the settlements that are out. Reads only. A real mismatch
    trips Safety, which writes the kill file (a new run needs it deleted by hand)."""
    from kpm.execution import reconcile
    trading = _trading(deps)
    parse_limits(trading)
    env = deps.load_env(True, expected=args.env)
    conn = deps.connect()
    client = None
    try:
        ledger = _ledger(deps, conn, _run_id(deps, "exec-reconcile"), env)
        client = _client(deps, env, ledger, live=False)
        safety = Safety(trading, ledger, client, deps.clock, sleep=deps.sleep, isatty=deps.isatty)  # halt listener
        deps.out("kpm exec reconcile (reads only). Stop any running bot first: two processes would reconcile at once.")
        result = reconcile.reconcile_all(client, ledger, clock=deps.clock, sleep=deps.sleep)
        deps.out(json.dumps(result, indent=2, sort_keys=True, default=str))
        if safety.halted:
            deps.out(f"HALTED: {safety.halt_reason}. The kill file {safety.kill_path} was written; delete it after "
                     "you have looked into it.")
        deps.out("reconcile: clean" if result["ok"] else "reconcile: PROBLEMS FOUND (see above)")
        return EXIT_OK if result["ok"] else EXIT_FAIL
    finally:
        if client is not None:
            client.close()
        conn.close()


# ============================================================================================ kpm bot run
def run_bot(args: Any, deps: Deps | None = None) -> int:
    """Dispatch ``kpm bot run``. Returns the process exit code."""
    deps = deps or default_deps()
    return _guarded(deps, _bot_run, args)


def _bot_run(args: Any, deps: Deps) -> int:
    live, smoke, out = bool(args.live), bool(args.smoke), deps.out
    if smoke and live:
        out("refusing to start: --smoke reads public data only and never trades; drop --live")
        return EXIT_USAGE
    hours = args.hours
    if hours is not None:
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not math.isfinite(hours) or hours <= 0:
            out("refusing to start: --hours must be a number greater than 0")
            return EXIT_USAGE
        if smoke:
            out("refusing to start: --smoke is one pass; --hours does not apply")
            return EXIT_USAGE
    trading = _trading(deps)
    parse_limits(trading)                               # SafetyConfigError: refuse to start above a ceiling
    env = deps.load_env(live, expected=args.env)        # live needs the key; watch-only does not (SystemExit says why)

    conn = deps.connect()
    client = None
    lock = None
    try:
        run_id = _run_id(deps, args.strategy)
        ledger = safety = None
        if live:
            # An exclusive claim for the whole live process, taken BEFORE the prompt: the run's own bot_runs row and heartbeat
            # only exist once the bot starts, so two starts that both sit at the prompt would otherwise both go live.
            lock = LiveRunLock(live_lock_path(trading))
            held, why = lock.acquire(f"{args.strategy} {run_id}")
            if not held:
                out(f"refusing to start live: another live run is active or the live lock cannot be taken ({lock.path}: "
                    f"{why}). Two live bots would double every trade; stop the other one first (kpm exec kill, or its "
                    "dashboard STOP button).")
                return EXIT_FAIL
            if _live_run_elsewhere(conn, deps, out):
                return EXIT_FAIL
            conn.execute(f"PRAGMA busy_timeout = {LIVE_BUSY_TIMEOUT_MS}")      # a locked database stops the run, not stalls it
            ledger = _ledger(deps, conn, run_id, env)
        client = _client(deps, env, ledger, live=live)
        if live:
            safety = Safety(trading, ledger, client, deps.clock, sleep=deps.sleep, isatty=deps.isatty)
            safety.startup(True, args.env, deps.confirm, isatty=deps.isatty, out=out)   # LiveGateError: nothing sent
            if _live_run_elsewhere(conn, deps, out):                  # one more look: the prompt and the probes took a while
                return EXIT_FAIL
            out("LIVE: every gate passed. Real orders will be sent.")
            for line in safety.banner():
                out("  " + line)
        else:
            out(f"WATCH-ONLY ({env.name}): public reads only, paper trades only, nothing is sent"
                + (" (smoke pass)" if smoke else ""))
            if getattr(env, "key_error", None):                   # a broken key setup never stops a watch-only run
                out(f"note: {env.key_error} Watch-only does not use the key.")
        state = botstate.BotState(conn, run_id, clock=deps.clock)
        bot, run_hours = _make_bot(args.strategy, client, state, ledger, safety, env, trading, live, deps, hours)
        out(f"run {run_id}: {args.strategy}" + _length_text(args.strategy, run_hours, smoke))
        try:
            summary = _run_guarded(bot, client, ledger, live, smoke, run_hours, deps)
        except KeyboardInterrupt:
            raise
        except Exception as exc:                        # the bot has stopped (its finally ran the cancel path)
            log.exception("bot run failed")
            out(f"the run ended with an error: {type(exc).__name__}: {exc}")
            return EXIT_FAIL
    finally:
        if client is not None:
            client.close()
        conn.close()
        if lock is not None:
            lock.release()
    reason = summary.get("end_reason")
    out(json.dumps(summary, indent=2, sort_keys=True, default=str))
    out(f"run ended: {reason}")
    return EXIT_OK if reason in CLEAN_END_REASONS else EXIT_FAIL


def _live_run_elsewhere(conn: sqlite3.Connection, deps: Deps, out: Callable[[str], Any]) -> bool:
    """True (after saying why) when another live run is beating, or when that cannot be told: a locked or unreadable
    database is "cannot tell", never "no other run"."""
    try:
        other = _other_live_run(conn, deps.clock())
    except sqlite3.Error as exc:
        out(f"refusing to start live: cannot check whether another live run is active ({type(exc).__name__}: {exc}). "
            "Two live bots would double every trade.")
        return True
    if other is None:
        return False
    out(f"refusing to start live: another live run is active ({other['strategy']} {other['run_id']}, "
        f"heartbeat {other['heartbeat_age_s']:.0f} s ago). Two live bots would double every trade; stop "
        "it first (kpm exec kill, or its dashboard STOP button).")
    return True


def _length_text(strategy: str, run_hours: float | int | None, smoke: bool) -> str:
    if smoke:
        return " (one public pass)"
    unit = "hour(s)" if strategy == "late_favorite" else "hourly window(s)"
    length = "until stopped" if run_hours is None else f"for {run_hours:g} {unit}"
    return f", {length}; Ctrl+C or the dashboard STOP button ends it"


def _make_bot(strategy: str, client: ExecClient, state: Any, ledger: Any, safety: Any, env: Any, trading: Mapping,
              live: bool, deps: Deps, hours: float | None) -> tuple[Any, float | int | None]:
    """The strategy object and the run length it takes: late_favorite wants wall-clock hours (any number > 0), pair_h2
    whole hourly windows (rounded up, at least 1)."""
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; choose one of {', '.join(STRATEGIES)}")
    from kpm.execution import reconcile as rec
    # The bots call reconcile(client, ledger, tickers), settle(client, ledger, tickers) and cancel_path(client, ledger,
    # reason, tickers). The module's own waits (a 5 s lag recheck, a 30 s verification) must use the same clock and sleep
    # as everything else in this run, so they are bound here instead of defaulting to time.sleep.
    namespace = SimpleNamespace(reconcile=partial(rec.reconcile, clock=deps.clock, sleep=deps.sleep), settle=rec.settle,
                                cancel_path=partial(rec.cancel_path, clock=deps.clock, sleep=deps.sleep))
    kw = dict(client=client, state=state, ledger=ledger, safety=safety, reconcile=namespace, env_name=env.name,
              key_fp=env.key_fp)
    if strategy == "late_favorite":
        from kpm.execution.late_favorite import LateContext, LateFavoriteBot
        return LateFavoriteBot(LateContext(**kw), trading, live=live, clock=deps.clock, sleep=deps.sleep), hours
    from kpm.execution.pair_h2 import PairBot, PairContext
    windows = None if hours is None else max(1, math.ceil(hours))
    return PairBot(PairContext(**kw), trading, live=live, clock=deps.clock, sleep=deps.sleep), windows


def _run_guarded(bot: Any, client: ExecClient, ledger: Any, live: bool, smoke: bool, run_hours: float | int | None,
                 deps: Deps) -> dict:
    """bot.run() inside the exit guard (live): the cancel path runs on a normal end, an exception, Ctrl+C, SIGBREAK,
    SIGTERM and at interpreter exit, exactly once."""
    if not live:
        return bot.run(hours=run_hours, smoke=smoke)
    from kpm.execution.reconcile import ExitGuard
    guard = ExitGuard(client, ledger, sleep=deps.sleep, clock=deps.clock)
    if deps.install_signals:
        guard.install_signal_handlers()
    guard.register_atexit()
    try:
        with guard:
            summary = dict(bot.run(hours=run_hours, smoke=False))
    finally:
        guard.unregister_atexit()
        guard.restore_signal_handlers()
    rep = guard.report
    if rep is not None:
        summary["exit_guard_cancel_path"] = {"ok": rep.ok, "orders_found": len(rep.targets),
                                             "cancelled": len(rep.cancelled), "still_resting": len(rep.remaining),
                                             "cancel_all_used": rep.cancel_all_used}
    return summary
