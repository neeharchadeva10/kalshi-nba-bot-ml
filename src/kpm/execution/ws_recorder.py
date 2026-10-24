"""READ-ONLY Kalshi WebSocket order-book recorder (market data only; no orders, no account commands).

Writes every received message raw to ``<root>/<date>/raw/msgs-YYYYMMDDHH-<process start epoch>.jsonl.gz`` as
``{"recv_ns": ..., "raw": "<text>"}`` plus our own control events as ``{"recv_ns": ..., "ctl": {...}}``.
Book reconstruction is done offline by kpm.analysis.ws_books; the two share only these files.

Safety: the only commands ever sent are in ``ALLOWED_CMDS`` on ``ALLOWED_CHANNELS`` (checked in :meth:`Recorder.send_cmd`
right before every send). The handshake is signed with kpm.execution.auth for every (re)connect. Key material is never
printed, logged or written; error text is scrubbed of the key id and key path.

Message formats (docs.kalshi.com): subscribe ``{"id":1,"cmd":"subscribe","params":{"channels":["orderbook_delta"],
"market_tickers":[...]}}``; reply ``{"id":1,"type":"subscribed","msg":{"channel":...,"sid":N}}``; every data message has
``sid`` and ``seq`` (consecutive per sid); error ``{"id":..,"type":"error","msg":{"code":..,"msg":..}}``.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import time
import zlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from kpm.execution.auth import WS_SIGN_PATH, auth_headers

log = logging.getLogger(__name__)

ALLOWED_CMDS = frozenset({"subscribe", "unsubscribe", "update_subscription"})
ALLOWED_CHANNELS = frozenset({"orderbook_delta", "trade"})
CHUNK = 200
FLUSH_S = 5.0
HEARTBEAT_S = 10.0
BACKOFF_MAX_S = 30.0
IDLE_REPLY_S = 1.0          # recv timeout: how often the loop wakes to check STOP / --until / flush


class CommandNotAllowed(Exception):
    """Something other than subscribe / unsubscribe / update_subscription (or an unknown channel) was about to be sent."""


# ------------------------------------------------------------------------------------------------ file writer
class HourlyWriter:
    """Appends JSON lines to gzip files rolled per UTC hour. ``flush`` makes the data readable after a crash."""

    def __init__(self, raw_dir: Path):
        self.dir = Path(raw_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._hour: str | None = None
        self._fh = None
        self._start = int(time.time())               # one file per process and hour: a crash never truncates a later run

    def write(self, recv_ns: int, obj: dict) -> None:
        hour = datetime.fromtimestamp(recv_ns / 1e9, timezone.utc).strftime("%Y%m%d%H")
        if hour != self._hour:
            self.close()
            self._fh = gzip.open(self.dir / f"msgs-{hour}-{self._start}.jsonl.gz", "ab")
            self._hour = hour
        self._fh.write((json.dumps({"recv_ns": recv_ns, **obj}, separators=(",", ":")) + "\n").encode("utf-8"))

    def flush(self) -> None:
        if self._fh is not None:
            self._fh.flush(zlib.Z_SYNC_FLUSH)
            self._fh.fileobj.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
            self._hour = None


# ------------------------------------------------------------------------------------------------ helpers
def load_tickers(markets_path: str | Path, games: list[str] | None = None) -> tuple[str, list[str]]:
    """(date code, tickers) from the discovery file, optionally only the named game suffixes."""
    doc = json.loads(Path(markets_path).read_text(encoding="utf-8"))
    chosen = doc["games"]
    if games:
        missing = [g for g in games if g not in chosen]
        if missing:
            raise SystemExit(f"unknown game(s) {missing}; the file has {sorted(chosen)}")
        chosen = {g: chosen[g] for g in games}
    tickers: list[str] = []
    for g in chosen.values():
        tickers.extend(t for t in g["tickers"] if t not in tickers)
    if not tickers:
        raise SystemExit("no tickers selected")
    return doc["date"], tickers


def chunks(items: list[str], n: int = CHUNK) -> list[list[str]]:
    return [items[i:i + n] for i in range(0, len(items), n)]


def parse_until(text: str) -> float:
    """ISO time to epoch seconds; a time without a zone is taken as UTC."""
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


# ------------------------------------------------------------------------------------------------ recorder
class Recorder:
    """One recording run. Everything external (connect, signing, clocks, sleeping) is injectable for tests."""

    def __init__(self, *, url: str, tickers: list[str], writer: HourlyWriter, sign: Callable[[], dict[str, str]],
                 log_dir: Path, connect: Callable[..., Any] | None = None, trades: bool = True,
                 until_ts: float | None = None, clock: Callable[[], float] = time.time,
                 ns_clock: Callable[[], int] = time.time_ns, sleep: Callable[[float], Any] = asyncio.sleep,
                 scrub: tuple[str, ...] = (), tag: str | None = None):
        self.tag = tag
        self.url, self.tickers, self.writer, self.sign = url, list(tickers), writer, sign
        self.log_dir = Path(log_dir)
        self.trades, self.until_ts = trades, until_ts
        self.clock, self.ns_clock, self.sleep, self.scrub = clock, ns_clock, sleep, tuple(s for s in scrub if s)
        if connect is None:
            import websockets
            connect = websockets.connect
        self.connect = connect
        self.stop = False
        self.counts: Counter = Counter()
        self.n_msgs = 0
        self.reconnects = 0
        self.seq_gaps = 0
        self.last_msg_ts: float | None = None
        self._cmd_id = 0
        self._pending: dict[int, tuple[str, list[str]]] = {}      # command id -> (channel, tickers)
        self._subs: dict[int, tuple[str, list[str]]] = {}         # sid -> (channel, tickers)
        self._last_seq: dict[int, int] = {}
        self._last_flush = 0.0
        self._last_hb = 0.0

    # ---- sending: the allowlist is enforced here and nowhere else sends
    async def send_cmd(self, ws, cmd: dict) -> int:
        name = cmd.get("cmd")
        if name not in ALLOWED_CMDS:
            raise CommandNotAllowed(f"refusing to send command {str(name)[:40]!r}")
        chans = (cmd.get("params") or {}).get("channels", [])
        if not set(chans) <= ALLOWED_CHANNELS:
            raise CommandNotAllowed("refusing to send a command on a non-market-data channel")
        self._cmd_id += 1
        cmd = {**cmd, "id": self._cmd_id}
        await ws.send(json.dumps(cmd))
        return self._cmd_id

    async def subscribe(self, ws, channel: str, tickers: list[str]) -> None:
        cid = await self.send_cmd(ws, {"cmd": "subscribe", "params": {"channels": [channel], "market_tickers": tickers}})
        self._pending[cid] = (channel, tickers)

    async def subscribe_all(self, ws) -> None:
        channels = ["orderbook_delta"] + (["trade"] if self.trades else [])
        for ch in channels:
            for part in chunks(self.tickers):
                await self.subscribe(ws, ch, part)

    # ---- control events / counters
    def ctl(self, event: str, **kw: Any) -> None:
        self.writer.write(self.ns_clock(), {"ctl": {"event": event, **kw}})

    def _clean(self, text: str) -> str:
        for s in self.scrub:
            text = text.replace(s, "<redacted>")
        return text[:300]

    # ---- one received message
    async def handle(self, ws, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        self.writer.write(self.ns_clock(), {"raw": raw})
        self.n_msgs += 1
        self.last_msg_ts = self.clock()
        try:
            msg = json.loads(raw)
            if not isinstance(msg, dict):
                raise ValueError
            mtype = str(msg.get("type", "?"))
        except ValueError:
            msg, mtype = {}, "unparsed"
        self.counts[mtype] += 1
        if mtype == "subscribed":                       # first subscribe on a channel: opens the channel's sid
            body = msg.get("msg") or {}
            pend = self._pending.pop(msg.get("id"), None)
            sid = body.get("sid")
            if pend and isinstance(sid, int):
                self._subs[sid] = (pend[0], list(pend[1]))
                self._last_seq.pop(sid, None)
                self.ctl("subscribed", channel=pend[0], sid=sid, n_markets=len(pend[1]))
        elif mtype == "error":
            self._pending.pop(msg.get("id"), None)
            self.ctl("error", detail=self._clean(json.dumps(msg.get("msg"))))
        elif isinstance(msg.get("sid"), int) and isinstance(msg.get("seq"), int):
            if mtype == "ok":                           # further subscribes on a channel add markets to the same sid
                pend = self._pending.pop(msg.get("id"), None)
                if pend and msg["sid"] in self._subs:
                    self._subs[msg["sid"]][1].extend(pend[1])
            await self._check_seq(ws, msg["sid"], msg["seq"])   # seq counts snapshots, deltas, trades and ok replies

    async def _check_seq(self, ws, sid: int, seq: int) -> None:
        last = self._last_seq.get(sid)
        self._last_seq[sid] = seq
        if last is None or seq == last + 1 or sid not in self._subs:
            return
        channel, tickers = self._subs[sid]
        self.seq_gaps += 1
        self.ctl("seq_gap", sid=sid, channel=channel, expected=last + 1, got=seq, n_markets=len(tickers))
        if channel != "orderbook_delta":
            return                                   # trade prints have no book to repair
        await self.send_cmd(ws, {"cmd": "unsubscribe", "params": {"sids": [sid]}})
        self._subs.pop(sid, None)
        self._last_seq.pop(sid, None)
        for part in chunks(tickers):                 # a fresh subscription starts with fresh snapshots
            await self.subscribe(ws, channel, part)
        self.ctl("resubscribe", old_sid=sid, n_markets=len(tickers))

    # ---- stop / periodic work
    def should_stop(self) -> bool:
        if self.stop or (self.until_ts is not None and self.clock() >= self.until_ts):
            return True
        return (self.log_dir / self.log_name("STOP")).exists()

    def periodic(self) -> None:
        now = self.clock()
        if now - self._last_flush >= FLUSH_S:
            self.writer.flush()
            self._last_flush = now
        if now - self._last_hb >= HEARTBEAT_S:
            self.write_heartbeat()
            self._last_hb = now

    def log_name(self, ext: str) -> str:
        """ws_recorder.<ext> for an untagged (old-style) recorder, ws_recorder-<tag>.<ext> otherwise."""
        return f"ws_recorder.{ext}" if not self.tag else f"ws_recorder-{self.tag}.{ext}"

    def write_heartbeat(self) -> None:
        now = self.clock()
        doc = {"time": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
               "messages": self.n_msgs, "by_type": dict(self.counts), "reconnects": self.reconnects,
               "seq_gaps": self.seq_gaps,
               "last_message_age_s": None if self.last_msg_ts is None else round(now - self.last_msg_ts, 1)}
        self.log_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.log_dir / (self.log_name("heartbeat") + ".tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        os.replace(tmp, self.log_dir / self.log_name("heartbeat"))

    # ---- main loop
    async def session(self) -> int:
        """One connection (freshly signed). Returns the number of messages received."""
        before = self.n_msgs
        self._pending.clear()
        self._subs.clear()
        self._last_seq.clear()
        async with self.connect(self.url, additional_headers=self.sign()) as ws:
            self.ctl("connect", n_tickers=len(self.tickers))
            await self.subscribe_all(ws)
            while not self.should_stop():
                try:
                    raw = await asyncio.wait_for(ws.recv(), IDLE_REPLY_S)
                except asyncio.TimeoutError:
                    self.periodic()
                    continue
                await self.handle(ws, raw)
                self.periodic()
        return self.n_msgs - before

    async def run(self) -> None:
        delay = 1.0
        try:
            while not self.should_stop():
                try:
                    got = await self.session()
                    if self.should_stop():
                        break
                    self.ctl("disconnect", reason="closed")
                    if got:
                        delay = 1.0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:                                   # any failure: back off, re-sign, resubscribe
                    self.ctl("error", where="session", exc=type(exc).__name__, detail=self._clean(str(exc)))
                    self.ctl("disconnect", reason=type(exc).__name__)
                    if self.should_stop():
                        break
                self.reconnects += 1
                self.writer.flush()
                await self.sleep(delay)
                delay = min(delay * 2, BACKOFF_MAX_S)
        finally:
            self.ctl("stop")
            self.write_heartbeat()
            self.writer.close()


# ------------------------------------------------------------------------------------------------ CLI entry
def recorder_tag(markets_path: str | Path) -> str:
    """The markets file's parent folder relative to data/ws, '/' -> '-': 26OCT22, nba-26OCT20."""
    parent = Path(markets_path).resolve().parent
    try:
        rel = parent.relative_to(Path("data/ws").resolve())
    except ValueError:
        rel = Path(parent.name)
    return "-".join(rel.parts)


def cli_main(args: Any) -> int:
    from kpm.execution.env import load_env
    env = load_env(require_keys=True, expected="prod")          # exits with a message that names the variable to fix
    date, tickers = load_tickers(args.markets, args.games)
    root = Path(args.markets).resolve().parent          # raw/ goes next to the markets file (data/ws/<date> for NFL)
    tag = getattr(args, "tag", None) or recorder_tag(args.markets)
    rec = Recorder(url=env.ws_url, tickers=tickers, writer=HourlyWriter(root / "raw"),
                   sign=lambda: auth_headers(env.key, env.key_id, "GET", WS_SIGN_PATH), log_dir=Path("logs"), tag=tag,
                   trades=args.trades, until_ts=parse_until(args.until) if args.until else None,
                   scrub=env.redact_values)
    print(f"recording {len(tickers)} markets (trades={'on' if args.trades else 'off'}) to {root / 'raw'}; "
          f"stop with Ctrl+C or by creating logs/ws_recorder-{tag}.STOP")
    try:
        asyncio.run(rec.run())
    except KeyboardInterrupt:
        print("interrupted; files closed")
    print(f"done: {rec.n_msgs} messages, {rec.reconnects} reconnects, {rec.seq_gaps} seq gaps")
    return 0
