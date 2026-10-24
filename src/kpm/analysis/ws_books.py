"""Rebuild YES order books from the raw WebSocket recordings (data/ws/<date>/raw/msgs-*.jsonl.gz).

Input lines: ``{"recv_ns", "raw"}`` (a Kalshi message as received) or ``{"recv_ns", "ctl"}`` (recorder control events).
Documented formats (docs.kalshi.com, prices in dollars as strings):

* ``orderbook_snapshot``: ``{"type","sid","seq","msg":{"market_ticker","yes_dollars_fp":[["0.5500","100.00"],..],
  "no_dollars_fp":[..]}}`` (a side key is absent when empty). Levels are resting BIDS: YES bids at YES prices, NO bids at
  NO-leg prices, so the best YES ask is ``1 - best NO bid``.
* ``orderbook_delta``: ``{"type","sid","seq","msg":{"market_ticker","price_dollars","delta_fp","side":"yes"|"no"}}``.
* ``trade``: ``{"type","sid","seq","msg":{"market_ticker","yes_price_dollars","count_fp","taker_side",..}}``.

Rules: a snapshot replaces the market's book. A delta is applied only if its sid is the sid of the market's last snapshot
and no seq gap was seen on that sid since; otherwise it is dropped until the next snapshot. A ``connect``, ``disconnect``
or ``stop`` control event invalidates every book (sids restart on a new connection; nothing is recorded in an outage).
"""
from __future__ import annotations

import gzip
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple


OUTAGE_EVENTS = ("connect", "disconnect", "stop")


class Top(NamedTuple):
    recv_ns: int
    ticker: str
    best_yes_bid: float | None
    bid_size: float | None
    best_yes_ask: float | None
    ask_size: float | None


class Trade(NamedTuple):
    recv_ns: int
    ticker: str
    yes_price: float
    count: float
    taker_side: str | None


def raw_files(raw_dir: str | Path) -> list[Path]:
    """msgs-<YYYYMMDDHH>[-<process start epoch>].jsonl.gz, ordered by hour, then start epoch (old unsuffixed names first)."""
    def key(p: Path):
        parts = p.name[len("msgs-"):-len(".jsonl.gz")].split("-")
        return parts[0], int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return sorted(Path(raw_dir).glob("msgs-*.jsonl.gz"), key=key)


def read_lines(paths: Iterable[str | Path]) -> Iterator[dict]:
    for p in paths:
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            try:
                for line in fh:
                    if line.strip():
                        yield json.loads(line)
            except (EOFError, OSError):               # a crash can leave a truncated final gzip member
                continue


def _px(s) -> int:
    return int(round(float(s) * 10000))               # price in 1/10000 dollar


class BookSet:
    """All markets' books plus the validity bookkeeping. Feed it lines; it returns events."""

    def __init__(self) -> None:
        self.yes: dict[str, dict[int, float]] = defaultdict(dict)
        self.no: dict[str, dict[int, float]] = defaultdict(dict)
        self.sid_of: dict[str, int] = {}              # market -> sid of its last snapshot (valid books only)
        self.last_seq: dict[int, int] = {}
        self.last_top: dict[str, tuple] = {}
        self.gaps = 0
        self.dropped: list[str] = []                  # markets whose book just became untrusted (consumers drain this)

    def top(self, t: str):
        y, n = self.yes[t], self.no[t]
        by = max(y) if y else None
        bn = max(n) if n else None
        return (None if by is None else by / 10000, None if by is None else y[by],
                None if bn is None else (10000 - bn) / 10000, None if bn is None else n[bn])

    def _emit(self, recv_ns: int, t: str) -> Iterator[Top]:
        cur = self.top(t)
        if self.last_top.get(t) != cur:
            self.last_top[t] = cur
            yield Top(recv_ns, t, *cur)

    def feed(self, line: dict) -> Iterator[Top | Trade]:
        recv_ns = line["recv_ns"]
        ctl = line.get("ctl")
        if ctl is not None:
            if ctl.get("event") in OUTAGE_EVENTS:
                self.dropped.extend(self.sid_of)
                self.sid_of.clear()
                self.last_seq.clear()
            return
        try:
            m = json.loads(line["raw"])
        except (ValueError, KeyError):
            return
        if not isinstance(m, dict):
            return
        typ, sid, seq, body = m.get("type"), m.get("sid"), m.get("seq"), m.get("msg") or {}
        if isinstance(sid, int) and isinstance(seq, int):
            last = self.last_seq.get(sid)
            self.last_seq[sid] = seq
            if last is not None and seq != last + 1:
                self.gaps += 1
                for t in [t for t, s in self.sid_of.items() if s == sid]:
                    del self.sid_of[t]
                    self.dropped.append(t)                # book untrusted until its next snapshot
        if typ not in ("orderbook_snapshot", "orderbook_delta", "trade"):
            return                                    # 'ok' replies also consume seq numbers, so they were counted above
        t = body.get("market_ticker")
        if not t:
            return
        if typ == "trade":
            yield Trade(recv_ns, t, float(body["yes_price_dollars"]), float(body["count_fp"]), body.get("taker_side"))
        elif typ == "orderbook_snapshot":
            self.yes[t] = {_px(p): float(c) for p, c in body.get("yes_dollars_fp") or []}
            self.no[t] = {_px(p): float(c) for p, c in body.get("no_dollars_fp") or []}
            self.sid_of[t] = sid
            yield from self._emit(recv_ns, t)
        elif self.sid_of.get(t) == sid:               # delta on a trusted book
            side = self.yes[t] if body["side"] == "yes" else self.no[t]
            p = _px(body["price_dollars"])
            size = side.get(p, 0.0) + float(body["delta_fp"])
            if size > 1e-9:
                side[p] = size
            else:
                side.pop(p, None)
            yield from self._emit(recv_ns, t)


def iter_events(raw_dir: str | Path) -> Iterator[Top | Trade]:
    """Top-of-book changes and trades for a whole recording, in file order."""
    books = BookSet()
    for line in read_lines(raw_files(raw_dir)):
        yield from books.feed(line)


def summarize(raw_dir: str | Path) -> dict:
    """Counts by message type, markets with a snapshot, seq gaps (from the recorder's control events and our own check)."""
    types: dict[str, int] = defaultdict(int)
    snap: set[str] = set()
    ctl_gaps = 0
    for line in read_lines(raw_files(raw_dir)):
        if "ctl" in line:
            types["ctl:" + str(line["ctl"].get("event"))] += 1
            ctl_gaps += line["ctl"].get("event") == "seq_gap"
            continue
        try:
            m = json.loads(line["raw"])
        except ValueError:
            types["unparsed"] += 1
            continue
        types[str(m.get("type"))] += 1
        if m.get("type") == "orderbook_snapshot":
            snap.add(m["msg"]["market_ticker"])
    return {"by_type": dict(types), "markets_with_snapshot": len(snap), "seq_gaps_recorded": ctl_gaps}
