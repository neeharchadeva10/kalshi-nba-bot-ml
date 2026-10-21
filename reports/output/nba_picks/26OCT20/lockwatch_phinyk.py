"""READ-ONLY real-time lock watcher for 26OCT20PHINYK. Public WebSocket market data only (orderbook_delta).
No order / portfolio / cancel code anywhere. Commands ever sent: subscribe, unsubscribe. Secrets are never printed or logged.
Run from the repo root:  KALSHI_ENV=prod .venv/Scripts/python.exe reports/output/nba_picks/26OCT20/lockwatch.py [--until ISO]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
LOGS = ROOT / "logs"
NAME = "lockwatch_phinyk"
CAP = 10
CHUNK = 200
HB_S = 30.0
IDLE_S = 1.0


def fee(p: float, n: float) -> float:
    return math.ceil(0.07 * n * p * (1 - p) / 0.0001 - 1e-6) * 0.0001


# ------------------------------------------------------------------ structure (same grouping logic as bosdet.py)
def strike(b: dict):
    st = b.get("strike_type")
    if st in ("greater", "greater_or_equal"):
        return "ge", b.get("floor")
    if st in ("less", "less_or_equal"):
        return "le", b.get("cap")
    return None, None


def build_structure(doc: dict):
    """-> (groups {(event, sub, d): sorted [(k, ticker)]}, me_sets {event: (tickers, exhaustive)}, groups_of {ticker: [keys]},
    sets_of {ticker: [events]})"""
    events = doc["events"]
    evkeys = sorted(events, key=len, reverse=True)
    ev_of = {}
    for t in doc["books"]:
        ev_of[t] = next(e for e in evkeys if t.startswith(e + "-"))
    groups: dict = {}
    for t, b in doc["books"].items():
        d, k = strike(b)
        if d is None or k is None:
            continue
        parts = t.split("-")
        sub = parts[2] if len(parts) >= 4 else re.sub(r"\d+$", "", parts[2])
        groups.setdefault((ev_of[t], sub, d), []).append((k, t))
    for L in groups.values():
        L.sort()
    byev: dict = {}
    for t, e in ev_of.items():
        byev.setdefault(e, []).append(t)
    me_sets = {}
    for e, L in byev.items():
        exh = e.startswith("KXNBAWINMARGIN") or e.startswith("KXNBAGAME")
        if events[e].get("mutually_exclusive") or exh:
            me_sets[e] = (sorted(L), exh)
    groups_of: dict = {}
    for key, L in groups.items():
        for _, t in L:
            groups_of.setdefault(t, []).append(key)
    sets_of: dict = {}
    for e, (L, _) in me_sets.items():
        for t in L:
            sets_of.setdefault(t, []).append(e)
    return groups, me_sets, groups_of, sets_of


# ------------------------------------------------------------------ pure detection math (tops: (bid, bsz, ask, asz))
def ladder_lock(weak: str, strong: str, tw, ts):
    """Buy YES weak at ask, buy NO strong at 1 - bid(strong). tw/ts are (bid,bsz,ask,asz) or None. -> dict or None"""
    if not tw or not ts or tw[2] is None or ts[0] is None:
        return None
    ask, asz, bid, bsz = tw[2], tw[3], ts[0], ts[1]
    n = math.floor(min(asz, bsz, CAP) + 1e-9)
    if n < 1:
        return None
    profit = n * (bid - ask) - fee(ask, n) - fee(1 - bid, n)
    if profit <= 0:
        return None
    return dict(kind="ladder", legs=[f"YES {weak}", f"NO {strong}"], prices=[ask, round(1 - bid, 4)], n=n,
                profit=round(profit, 4), per_contract=round(profit / n, 4))


def me_locks(event: str, tickers: list, tops: dict, exhaustive: bool):
    """-> list of lock dicts (buy-all-NO, and buy-all-YES if exhaustive). tops[t] = (bid,bsz,ask,asz) or None if invalid."""
    out = []
    ts = [tops.get(t) for t in tickers]
    if any(x is None for x in ts):
        return out
    if all(x[0] is not None for x in ts):
        n = math.floor(min(min(x[1] for x in ts), CAP) + 1e-9)
        if n >= 1:
            sb = sum(x[0] for x in ts)
            profit = n * (sb - 1) - sum(fee(1 - x[0], n) for x in ts)
            if profit > 0:
                out.append(dict(kind="me_no", legs=[f"NO {t}" for t in tickers], prices=[round(1 - x[0], 4) for x in ts], n=n,
                                profit=round(profit, 4), per_contract=round(profit / n, 4)))
    if exhaustive and all(x[2] is not None for x in ts):
        n = math.floor(min(min(x[3] for x in ts), CAP) + 1e-9)
        if n >= 1:
            sa = sum(x[2] for x in ts)
            profit = n * (1 - sa) - sum(fee(x[2], n) for x in ts)
            if profit > 0:
                out.append(dict(kind="me_yes", legs=[f"YES {t}" for t in tickers], prices=[x[2] for x in ts], n=n,
                                profit=round(profit, 4), per_contract=round(profit / n, 4)))
    return out


class Detector:
    """Keeps open-lock state. tops_fn(t) -> (bid,bsz,ask,asz) if the book is valid else None."""

    def __init__(self, doc: dict, tops_fn, emit):
        self.groups, self.me_sets, self.groups_of, self.sets_of = build_structure(doc)
        self.tops_fn, self.emit = tops_fn, emit
        self.open: dict = {}                       # key -> state
        self.n_opened = 0

    def _ladder_keys(self, t: str):
        """Qualifying ladder locks involving ticker t -> {key: (lock, tickers)}"""
        found, scope = {}, set()
        for gk in self.groups_of.get(t, []):
            d = gk[2]
            L = self.groups[gk]
            k_t = next(k for k, x in L if x == t)
            for k, x in L:
                if x == t:
                    continue
                for (k1, t1), (k2, t2) in (((k_t, t), (k, x)), ((k, x), (k_t, t))):
                    if (d == "ge" and not k2 > k1) or (d == "le" and not k2 < k1):
                        continue
                    key = ("L", t1, t2)
                    scope.add(key)
                    lk = ladder_lock(t1, t2, self.tops_fn(t1), self.tops_fn(t2))
                    if lk:
                        found[key] = (lk, (t1, t2))
        return found, scope

    def _me_keys(self, t: str):
        found, scope = {}, set()
        for e in self.sets_of.get(t, []):
            L, exh = self.me_sets[e]
            scope |= {("ME", e, "me_no"), ("ME", e, "me_yes")}
            tops = {x: self.tops_fn(x) for x in L}
            for lk in me_locks(e, L, tops, exh):
                found[("ME", e, lk["kind"])] = (lk, tuple(L))
        return found, scope

    def update(self, t: str, now: float):
        f1, s1 = self._ladder_keys(t)
        f2, s2 = self._me_keys(t)
        found, scope = {**f1, **f2}, s1 | s2
        for key, (lk, tks) in found.items():
            st = self.open.get(key)
            if st is None:
                self.open[key] = dict(lk, t_open=now, max_profit=lk["profit"], max_n=lk["n"], tickers=tks, key=key,
                                      first=lk)
                self.n_opened += 1
                self.emit("lock_open", self.open[key], now)
            else:
                if lk["profit"] > st["max_profit"]:
                    st["max_profit"] = lk["profit"]
                st["max_n"] = max(st["max_n"], lk["n"])
        for key in [k for k in self.open if k in scope and k not in found]:
            st = self.open.pop(key)
            self.emit("lock_close", st, now)

    def close_all(self, now: float):
        for key in list(self.open):
            self.emit("lock_close", self.open.pop(key), now)


# ------------------------------------------------------------------ runtime
def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def chunks(items, n=CHUNK):
    return [items[i:i + n] for i in range(0, len(items), n)]


class Watcher:
    def __init__(self, doc, env, until_ts):
        from kpm.analysis.ws_books import BookSet
        self.bs = BookSet()
        self.tickers = list(doc["books"])
        self.env, self.until_ts = env, until_ts
        self.det = Detector(doc, self.tops, self.emit)
        self.n_msgs = self.reconnects = self.seq_gaps = self.snapshots = self.errors = 0
        self.log_fh = None
        self._cid = 0
        self._pending, self._subs, self._last_seq = {}, {}, {}
        self.last_hb = 0.0
        self.last_err = None

    def tops(self, t):
        if t not in self.bs.sid_of:
            return None
        return self.bs.top(t)

    def write(self, obj):
        self.log_fh.write(json.dumps(obj, separators=(",", ":")) + "\n")
        self.log_fh.flush()

    def emit(self, ev, st, now):
        if ev == "lock_open":
            self.write(dict(event=ev, time=iso(now), kind=st["kind"], legs=st["legs"], prices=st["prices"], n=st["n"],
                            profit=st["profit"], per_contract=st["per_contract"]))
            print(f"{iso(now)} OPEN  {st['kind']} n={st['n']} profit=${st['profit']:.4f} ({st['per_contract']:.4f}/ct) {' + '.join(st['legs'])[:110]}", flush=True)
        else:
            dur = now - st["t_open"]
            self.write(dict(event=ev, time=iso(now), t_open=iso(st["t_open"]), duration_s=round(dur, 3), kind=st["kind"],
                            legs=st["legs"], first_prices=st["first"]["prices"], first_n=st["first"]["n"],
                            max_profit=st["max_profit"], max_n=st["max_n"]))
            print(f"{iso(now)} CLOSE {st['kind']} lived {dur:.3f}s max_profit=${st['max_profit']:.4f} {' + '.join(st['legs'])[:90]}", flush=True)

    def heartbeat(self, now):
        doc = dict(event="heartbeat", time=iso(now), messages=self.n_msgs, valid_books=len(self.bs.sid_of),
                   tickers=len(self.tickers), snapshots=self.snapshots, reconnects=self.reconnects, seq_gaps=self.seq_gaps,
                   errors=self.errors, open_locks=len(self.det.open), locks_opened_total=self.det.n_opened,
                   open_lock_legs=[" + ".join(s["legs"]) for s in self.det.open.values()][:10])
        self.write(doc)
        tmp = LOGS / (NAME + ".heartbeat.tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        tmp.replace(LOGS / (NAME + ".heartbeat"))
        self.last_hb = now

    # ---- send: only subscribe / unsubscribe
    async def send(self, ws, cmd, params):
        assert cmd in ("subscribe", "unsubscribe")
        self._cid += 1
        await ws.send(json.dumps({"id": self._cid, "cmd": cmd, "params": params}))
        return self._cid

    async def subscribe(self, ws, tickers):
        for part in chunks(tickers):
            cid = await self.send(ws, "subscribe", {"channels": ["orderbook_delta"], "market_tickers": part})
            self._pending[cid] = part


    async def handle(self, ws, raw):
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        self.n_msgs += 1
        recv_ns = time.time_ns()
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        mt = msg.get("type")
        if mt == "orderbook_snapshot":
            self.snapshots += 1
        if mt == "subscribed":
            body = msg.get("msg") or {}
            pend = self._pending.pop(msg.get("id"), None)
            if pend and isinstance(body.get("sid"), int):
                self._subs[body["sid"]] = list(pend)
                self._last_seq.pop(body["sid"], None)
        elif mt == "error":
            self._pending.pop(msg.get("id"), None)
            self.errors += 1
            self.last_err = json.dumps(msg.get("msg"))[:200]
            self.write(dict(event="error", time=iso(time.time()), detail=self.last_err))
        sid, seq = msg.get("sid"), msg.get("seq")
        gap = False
        if isinstance(sid, int) and isinstance(seq, int):
            if mt == "ok":
                pend = self._pending.pop(msg.get("id"), None)
                if pend and sid in self._subs:
                    self._subs[sid].extend(pend)
            last = self._last_seq.get(sid)
            self._last_seq[sid] = seq
            gap = last is not None and seq != last + 1 and sid in self._subs
        tops = list(self.bs.feed({"recv_ns": recv_ns, "raw": raw}))
        now = time.time()
        touched = {x.ticker for x in tops}
        touched.update(self.bs.dropped)
        self.bs.dropped.clear()
        for t in touched:
            self.det.update(t, now)
        if gap:
            self.seq_gaps += 1
            tk = self._subs.pop(sid)
            self._last_seq.pop(sid, None)
            self.write(dict(event="seq_gap", time=iso(now), sid=sid, expected=last + 1, got=seq, n_markets=len(tk)))
            await self.send(ws, "unsubscribe", {"sids": [sid]})
            await self.subscribe(ws, tk)

    def stop_now(self):
        return time.time() >= self.until_ts or (LOGS / (NAME + ".STOP")).exists()

    async def session(self):
        import websockets
        from kpm.execution.auth import WS_SIGN_PATH, auth_headers
        self._pending.clear(); self._subs.clear(); self._last_seq.clear()
        hdr = auth_headers(self.env.key, self.env.key_id, "GET", WS_SIGN_PATH)
        before = self.n_msgs
        async with websockets.connect(self.env.ws_url, additional_headers=hdr) as ws:
            self.bs.feed({"recv_ns": time.time_ns(), "ctl": {"event": "connect"}})
            for t in list(self.bs.dropped):
                self.det.update(t, time.time())
            self.bs.dropped.clear()
            await self.subscribe(ws, self.tickers)
            while not self.stop_now():
                try:
                    raw = await asyncio.wait_for(ws.recv(), IDLE_S)
                except asyncio.TimeoutError:
                    raw = None
                if raw is not None:
                    await self.handle(ws, raw)
                now = time.time()
                if now - self.last_hb >= HB_S:
                    self.heartbeat(now)
        return self.n_msgs - before

    async def run(self):
        delay = 1.0
        try:
            while not self.stop_now():
                try:
                    got = await self.session()
                    if got:
                        delay = 1.0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.errors += 1
                    self.write(dict(event="error", time=iso(time.time()), where="session", exc=type(exc).__name__))
                # book invalid after any disconnect
                self.bs.feed({"recv_ns": time.time_ns(), "ctl": {"event": "disconnect"}})
                now = time.time()
                for t in list(self.bs.dropped):
                    self.det.update(t, now)
                self.bs.dropped.clear()
                if self.stop_now():
                    break
                self.reconnects += 1
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
        finally:
            self.det.close_all(time.time())
            self.heartbeat(time.time())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default="2026-10-21T00:00:00Z")
    args = ap.parse_args()
    from kpm.execution.env import load_env
    env = load_env(require_keys=True, expected="prod")
    until = datetime.fromisoformat(args.until.replace("Z", "+00:00"))
    until_ts = (until if until.tzinfo else until.replace(tzinfo=timezone.utc)).timestamp()
    doc = json.loads((HERE / "phinyk.json").read_text(encoding="utf-8"))
    LOGS.mkdir(exist_ok=True)
    w = Watcher(doc, env, until_ts)
    w.log_fh = open(LOGS / (NAME + ".jsonl"), "a", encoding="utf-8")
    print(f"lockwatch: {len(w.tickers)} tickers, {len(w.det.groups)} ladder groups, {len(w.det.me_sets)} ME sets; "
          f"until {iso(until_ts)}; stop via logs/{NAME}.STOP", flush=True)
    try:
        asyncio.run(w.run())
    except KeyboardInterrupt:
        print("interrupted")
    finally:
        w.log_fh.close()
    print(f"done: msgs={w.n_msgs} snapshots={w.snapshots} valid={len(w.bs.sid_of)} reconnects={w.reconnects} "
          f"seq_gaps={w.seq_gaps} errors={w.errors} locks_opened={w.det.n_opened}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
