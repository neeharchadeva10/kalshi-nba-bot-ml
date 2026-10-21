"""READ-ONLY NBA lock watcher, FAST variant of lockwatch_night.py (one process per game via --games).

Public market data only: GET REST via the repo's KalshiClient for structure discovery, and one WebSocket with
orderbook_delta subscriptions. Commands ever sent on the socket: subscribe, unsubscribe. No order / portfolio / cancel /
amend code anywhere. Secrets are never printed or logged (keys come from kpm.execution.env.load_env).

  KALSHI_ENV=prod python reports/output/ohio_trial/lockwatch_fast.py --date 26OCT21 --games 26OCT21AAABBB --tag ohio-fast-AAABBB
Differences vs lockwatch_night: --games/--structure filter, orjson + uvloop when importable, single JSON parse per frame,
proc_us / backlog / gap_us on every 100th-delta record, proc_us mean/max + CPU% in the heartbeat.
Outputs in --out-dir (default <repo>/logs):
  lockwatch_night_<date>_<tag>.jsonl | .heartbeat | .structure.json ; create lockwatch_night_<date>_<tag>.STOP to stop.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import platform
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:                                                   # optional speedups; fall back silently
    import orjson as _orjson
except ImportError:
    _orjson = None
JSON_IMPL = "orjson" if _orjson else "json"
LOOP_IMPL = "asyncio"


def loads(raw):
    """JSON parse (orjson when importable). Raises ValueError on bad input in both implementations."""
    if _orjson is not None:
        try:
            return _orjson.loads(raw)
        except _orjson.JSONDecodeError as exc:
            raise ValueError(str(exc)) from None
    return json.loads(raw)


def dumps(obj) -> str:
    if _orjson is not None:
        return _orjson.dumps(obj).decode()
    return json.dumps(obj, separators=(",", ":"))


def install_uvloop() -> str:
    if sys.platform.startswith("linux"):
        try:
            import uvloop
            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
            return "uvloop"
        except ImportError:
            pass
    return "asyncio"


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import lockcore as lc  # noqa: E402

CHUNK = 200
HB_S = 30.0
IDLE_S = 1.0
DELAY_EVERY = 100
SNTP_EVERY_S = 600.0
STRUCT_MAX_AGE_S = 6 * 3600.0
DEAD_STATUS = ("closed", "settled", "determined", "finalized")


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def chunks(items, n=CHUNK):
    return [items[i:i + n] for i in range(0, len(items), n)]


# ------------------------------------------------------------------ structure discovery (REST, GET only)
def discover(date: str, prefix: str, workers: int = 4) -> dict:
    """All open events for `date` over every series starting with `prefix` -> {events, books, series, pulled_ts}."""
    from kpm.clients.kalshi import KalshiClient
    cl = KalshiClient()

    def get(path, **kw):
        return cl.http.get(path, kw or None, use_cache=False, persist=False)

    series = sorted(s["ticker"] for s in get("/series").get("series", []) if s["ticker"].startswith(prefix))

    def one(s):
        out, cur, err = [], None, None
        while True:
            kw = dict(series_ticker=s, status="open", limit=200, with_nested_markets=True)
            if cur:
                kw["cursor"] = cur
            try:
                r = get("/events", **kw)
            except Exception as exc:                      # one series failing must not kill discovery
                return out, f"{s}:{type(exc).__name__}"
            out += r.get("events", [])
            cur = r.get("cursor")
            if not cur:
                return out, err

    events, books, errs, used = {}, {}, [], set()
    with ThreadPoolExecutor(workers) as ex:
        for s, (evs, err) in zip(series, ex.map(one, series)):
            if err:
                errs.append(err)
            for e in evs:
                et = e["event_ticker"]
                parts = et.split("-")
                if len(parts) < 2 or not parts[1].startswith(date):
                    continue
                n0 = len(books)
                for m in e.get("markets", []):
                    if m.get("status") in DEAD_STATUS:
                        continue
                    books[m["ticker"]] = dict(strike_type=m.get("strike_type"), floor=m.get("floor_strike"),
                                              cap=m.get("cap_strike"), title=m.get("yes_sub_title") or m.get("title"))
                if len(books) > n0:
                    events[et] = dict(mutually_exclusive=e.get("mutually_exclusive"), title=e.get("title"))
                    used.add(s)
    return dict(pulled_ts=time.time(), date=date, prefix=prefix, n_series_scanned=len(series), series_used=sorted(used),
                discovery_errors=errs[:20], events=events, books=books)


def load_structure(path: Path, date: str, prefix: str):
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if doc.get("date") != date or doc.get("prefix") != prefix or time.time() - doc.get("pulled_ts", 0) > STRUCT_MAX_AGE_S:
        return None
    return doc


def filter_games(doc: dict, games) -> dict:
    """Keep only books whose game code (2nd '-' part of the ticker) is in `games`; prune events likewise."""
    keep = {g.upper() for g in games}
    books = {t: v for t, v in doc["books"].items() if lc.game_of(t).upper() in keep}
    evs = {e: v for e, v in doc.get("events", {}).items()
           if len(e.split("-")) > 1 and e.split("-")[1].upper() in keep}
    out = dict(doc)
    out["books"], out["events"] = books, evs
    out["games_filter"] = sorted(keep)
    return out


def parse_games(s):
    return [g.strip().upper() for g in s.split(",") if g.strip()] if s else []


# ------------------------------------------------------------------ SNTP (reuse reports/output/latency/measure.py)
def load_measure():
    p = ROOT / "reports" / "output" / "latency" / "measure.py"
    spec = importlib.util.spec_from_file_location("kpm_latency_measure", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ runtime
def make_fast_bookset():
    """BookSet whose feed accepts an already-parsed message ({'recv_ns', 'msg_obj'}) so each frame is parsed once."""
    from kpm.analysis import ws_books as wb

    class FastBookSet(wb.BookSet):
        def feed(self, line):
            m = line.get("msg_obj")
            if m is None:
                return super().feed(line)
            return self._feed_obj(line["recv_ns"], m)

        def _feed_obj(self, recv_ns, m):
            typ, sid, seq, body = m.get("type"), m.get("sid"), m.get("seq"), m.get("msg") or {}
            if isinstance(sid, int) and isinstance(seq, int):
                last = self.last_seq.get(sid)
                self.last_seq[sid] = seq
                if last is not None and seq != last + 1:
                    self.gaps += 1
                    for t in [t for t, s in self.sid_of.items() if s == sid]:
                        del self.sid_of[t]
                        self.dropped.append(t)
            if typ not in ("orderbook_snapshot", "orderbook_delta", "trade"):
                return
            t = body.get("market_ticker")
            if not t:
                return
            if typ == "trade":
                yield wb.Trade(recv_ns, t, float(body["yes_price_dollars"]), float(body["count_fp"]),
                               body.get("taker_side"))
            elif typ == "orderbook_snapshot":
                self.yes[t] = {wb._px(p): float(c) for p, c in body.get("yes_dollars_fp") or []}
                self.no[t] = {wb._px(p): float(c) for p, c in body.get("no_dollars_fp") or []}
                self.sid_of[t] = sid
                yield from self._emit(recv_ns, t)
            elif self.sid_of.get(t) == sid:
                side = self.yes[t] if body["side"] == "yes" else self.no[t]
                p = wb._px(body["price_dollars"])
                size = side.get(p, 0.0) + float(body["delta_fp"])
                if size > 1e-9:
                    side[p] = size
                else:
                    side.pop(p, None)
                yield from self._emit(recv_ns, t)

    return FastBookSet()


class Watcher:
    def __init__(self, doc, env, until_ts, out_dir: Path, name: str, date: str, tag: str):
        self.bs = make_fast_bookset()
        self.doc, self.tickers = doc, list(doc["books"])
        self.env, self.until_ts = env, until_ts
        self.out_dir, self.name, self.date, self.tag = out_dir, name, date, tag
        self.det = lc.Detector(doc, self.tops, self.emit)
        self.n_msgs = self.n_deltas = self.reconnects = self.seq_gaps = self.snapshots = self.errors = 0
        self.n_samples = self.n_sntp = 0
        self.log_fh = None
        self._cid = 0
        self._pending, self._subs, self._last_seq = {}, {}, {}
        self.last_hb = 0.0
        self.last_err = None
        self.last_sntp_off = None
        self.peak_valid = 0
        self._delay_pending = None
        self.prev_done_ns = None
        self.proc_sum = self.proc_n = self.proc_max = 0
        self._cpu_last = (time.perf_counter(), sum(os.times()[:2]))
        self.cpu_pct = None

    def tops(self, t):
        if t not in self.bs.sid_of:
            return None
        return self.bs.top(t)

    def write(self, obj):
        self.log_fh.write(dumps(obj) + "\n")
        self.log_fh.flush()

    def emit(self, ev, st, now):
        legs = st["legs"]
        tk = lc.leg_ticker(legs[0])
        series, game = lc.series_of(tk), lc.game_of(tk)
        common = dict(game=game, series=series, mtype=lc.market_type(series), is_prop=lc.is_prop_series(series),
                      kind=st["kind"], legs=legs)
        if ev == "lock_open":
            om = st["open_meta"] or {}
            self.write(dict(event=ev, time=iso(now), open_ns=om.get("recv_ns"), open_ts_ms=om.get("ts_ms"), **common,
                            prices=st["prices"], sizes=st["sizes"], fees=st["fees"], n=st["n"], profit=st["profit"],
                            per_contract=st["per_contract"]))
            print(f"{iso(now)} OPEN  {st['kind']} n={st['n']} profit=${st['profit']:.4f} "
                  f"{' + '.join(legs)[:100]}", flush=True)
        else:
            om, cm = st["open_meta"] or {}, st.get("close_meta") or {}
            o_ns, c_ns = om.get("recv_ns"), cm.get("recv_ns")
            life = (c_ns - o_ns) / 1e6 if o_ns and c_ns else (now - st["t_open"]) * 1000.0
            self.write(dict(event=ev, time=iso(now), open_ns=o_ns, close_ns=c_ns, lifetime_ms=round(life, 3),
                            open_ts_ms=om.get("ts_ms"), close_ts_ms=cm.get("ts_ms"), close_reason=cm.get("reason"),
                            **common, first_prices=st["first"]["prices"], first_sizes=st["first"]["sizes"],
                            first_fees=st["first"]["fees"], first_n=st["first"]["n"], first_profit=st["first"]["profit"],
                            max_profit=st["max_profit"], max_n=st["max_n"]))
            print(f"{iso(now)} CLOSE {st['kind']} lived {life:.1f}ms max_profit=${st['max_profit']:.4f} "
                  f"{' + '.join(legs)[:90]}", flush=True)

    def heartbeat(self, now):
        doc = dict(event="heartbeat", time=iso(now), date=self.date, tag=self.tag, messages=self.n_msgs,
                   valid_books=len(self.bs.sid_of), tickers=len(self.tickers), snapshots=self.snapshots,
                   reconnects=self.reconnects, seq_gaps=self.seq_gaps, errors=self.errors,
                   open_locks=len(self.det.open), locks_opened_total=self.det.n_opened, delay_samples=self.n_samples,
                   sntp_runs=self.n_sntp, last_sntp_offset_ms=self.last_sntp_off,
                   proc_us_mean_30s=(round(self.proc_sum / self.proc_n, 1) if self.proc_n else None),
                   proc_us_max_30s=self.proc_max, proc_frames_30s=self.proc_n, cpu_pct=self.cpu_pct,
                   json_impl=JSON_IMPL, loop=LOOP_IMPL)
        self.proc_sum = self.proc_n = self.proc_max = 0
        w0, c0 = self._cpu_last
        w1, c1 = time.perf_counter(), sum(os.times()[:2])
        if w1 - w0 > 1e-3:
            self.cpu_pct = round(100.0 * (c1 - c0) / (w1 - w0), 1)     # CPU% over the window since last heartbeat
        self._cpu_last = (w1, c1)
        self.write(doc)
        tmp = self.out_dir / (self.name + ".heartbeat.tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        tmp.replace(self.out_dir / (self.name + ".heartbeat"))
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

    async def handle(self, ws, raw, recv_ns, backlog):
        self.n_msgs += 1
        try:
            msg = loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(msg, dict):
            return
        mt = msg.get("type")
        body = msg.get("msg") if isinstance(msg.get("msg"), dict) else {}
        ts_ms = body.get("ts_ms")
        if mt == "orderbook_snapshot":
            self.snapshots += 1
        elif mt == "orderbook_delta":
            self.n_deltas += 1
            if self.n_deltas % DELAY_EVERY == 0 and ts_ms is not None:
                self.n_samples += 1
                self._delay_pending = (recv_ns, ts_ms, backlog)
        if mt == "subscribed":
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
        last = None
        if isinstance(sid, int) and isinstance(seq, int):
            if mt == "ok":
                pend = self._pending.pop(msg.get("id"), None)
                if pend and sid in self._subs:
                    self._subs[sid].extend(pend)
            last = self._last_seq.get(sid)
            self._last_seq[sid] = seq
            gap = last is not None and seq != last + 1 and sid in self._subs
        tops = list(self.bs.feed({"recv_ns": recv_ns, "msg_obj": msg}))
        now = time.time()
        touched = {x.ticker for x in tops}
        touched.update(self.bs.dropped)
        self.bs.dropped.clear()
        meta = {"recv_ns": recv_ns, "ts_ms": ts_ms, "reason": "gap" if gap else None}
        for t in touched:
            self.det.update(t, now, meta)
        if gap:
            self.seq_gaps += 1
            tk = self._subs.pop(sid)
            self._last_seq.pop(sid, None)
            self.write(dict(event="seq_gap", time=iso(now), sid=sid, expected=last + 1, got=seq, n_markets=len(tk)))
            await self.send(ws, "unsubscribe", {"sids": [sid]})
            await self.subscribe(ws, tk)

    def finish_frame(self, t0, t_end):
        us = (t_end - t0) // 1000
        self.proc_sum += us
        self.proc_n += 1
        if us > self.proc_max:
            self.proc_max = us
        return us

    def stop_now(self):
        return time.time() >= self.until_ts or (self.out_dir / (self.name + ".STOP")).exists()

    async def sntp_loop(self):
        try:
            mod = load_measure()
        except Exception as exc:
            self.write(dict(event="error", time=iso(time.time()), where="sntp_load", exc=type(exc).__name__))
            return
        while not self.stop_now():
            try:
                t0 = time.time_ns()
                hosts, off = await asyncio.to_thread(mod.do_ntp)
                self.n_sntp += 1
                self.last_sntp_off = round(off, 3)
                self.write(dict(event="sntp", time=iso(time.time()), recv_ns=t0, offset_ms=off, hosts=hosts))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.write(dict(event="error", time=iso(time.time()), where="sntp", exc=type(exc).__name__))
            for _ in range(int(SNTP_EVERY_S)):
                if self.stop_now():
                    return
                await asyncio.sleep(1.0)

    async def session(self):
        import websockets
        from kpm.execution.auth import WS_SIGN_PATH, auth_headers
        self._pending.clear(); self._subs.clear(); self._last_seq.clear()
        hdr = auth_headers(self.env.key, self.env.key_id, "GET", WS_SIGN_PATH)      # re-signed on every connect
        before = self.n_msgs
        try:
            conn = websockets.connect(self.env.ws_url, additional_headers=hdr)
        except TypeError:                                                           # websockets < 14
            conn = websockets.connect(self.env.ws_url, extra_headers=hdr)
        async with conn as ws:
            self.bs.feed({"recv_ns": time.time_ns(), "ctl": {"event": "connect"}})
            self._flush_dropped("connect")
            await self.subscribe(ws, self.tickers)
            while not self.stop_now():
                try:
                    raw = await asyncio.wait_for(ws.recv(), IDLE_S)
                except asyncio.TimeoutError:
                    raw = None
                if raw is not None:
                    t0 = time.perf_counter_ns()                       # recv() just returned
                    recv_ns = time.time_ns()
                    try:
                        backlog = len(ws.recv_messages.frames)       # frames queued behind this one (websockets>=14)
                    except AttributeError:
                        backlog = None
                    gap_us = None if self.prev_done_ns is None else (t0 - self.prev_done_ns) // 1000
                    self._delay_pending = None
                    await self.handle(ws, raw, recv_ns, backlog)
                    t_end = time.perf_counter_ns()
                    us = self.finish_frame(t0, t_end)
                    self.prev_done_ns = t_end
                    dp = self._delay_pending
                    if dp is not None:
                        self.write(dict(event="delay", recv_ns=dp[0], ts_ms=dp[1], proc_us=us, backlog=dp[2],
                                        gap_us=gap_us))
                self.peak_valid = max(self.peak_valid, len(self.bs.sid_of))
                now = time.time()
                if now - self.last_hb >= HB_S:
                    self.heartbeat(now)
        return self.n_msgs - before

    def _flush_dropped(self, reason):
        now = time.time()
        meta = {"recv_ns": time.time_ns(), "ts_ms": None, "reason": reason}
        for t in list(self.bs.dropped):
            self.det.update(t, now, meta)
        self.bs.dropped.clear()

    async def run(self):
        delay = 1.0
        sntp_task = asyncio.create_task(self.sntp_loop())
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
                self.bs.feed({"recv_ns": time.time_ns(), "ctl": {"event": "disconnect"}})   # books invalid after disconnect
                self._flush_dropped("disconnect")
                if self.stop_now():
                    break
                self.reconnects += 1
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
        finally:
            self.det.close_all(time.time(), {"recv_ns": time.time_ns(), "ts_ms": None, "reason": "stop"})
            self.heartbeat(time.time())
            sntp_task.cancel()
            try:
                await sntp_task
            except BaseException:
                pass


def parse_until(s: str | None, date: str) -> float:
    if not s:
        d = datetime.strptime(date, "%y%b%d").replace(tzinfo=timezone.utc) + timedelta(days=1, hours=9)
        return d.timestamp()
    m = re.fullmatch(r"\+(\d+)([sm])", s)
    if m:
        return time.time() + int(m.group(1)) * (60 if m.group(2) == "m" else 1)
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", required=True, help="event date code, e.g. 26OCT21")
    ap.add_argument("--prefix", default="KXNBA")
    ap.add_argument("--until", default=None, help="ISO time or +60s/+5m; default 09:00Z the day after --date")
    ap.add_argument("--tag", default="home", help="label, e.g. ohio-fast-PHINYK")
    ap.add_argument("--games", default=None, help="comma list of game codes, e.g. 26OCT20PHINYK (default: all)")
    ap.add_argument("--structure", default=None, help="existing .structure.json to reuse (filtered by --games)")
    ap.add_argument("--out-dir", default=str(ROOT / "logs"))
    args = ap.parse_args()
    date = args.date.upper()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"lockwatch_night_{date}_{args.tag}"

    from kpm.execution.env import load_env
    env = load_env(require_keys=True, expected="prod")

    sp = out_dir / (name + ".structure.json")
    games_f = parse_games(args.games)
    doc, src = None, "cache"
    if args.structure:
        try:
            doc = json.loads(Path(args.structure).read_text(encoding="utf-8"))
            if doc.get("date") != date or doc.get("prefix") != args.prefix:
                doc = None
            src = "file"
        except (OSError, ValueError):
            doc = None
        if doc is not None and games_f:
            doc = filter_games(doc, games_f)
            if not doc["books"]:
                doc = None                                  # file lacks these games -> discover
    if doc is None:
        doc = load_structure(sp, date, args.prefix)
        src = "cache"
        if doc is not None and games_f:
            doc = filter_games(doc, games_f)
            if not doc["books"]:
                doc = None
    if doc is None:
        src = "REST"
        doc = discover(date, args.prefix)
        if games_f:
            doc = filter_games(doc, games_f)
        sp.write_text(json.dumps(doc), encoding="utf-8")
    if not doc["books"]:
        print(f"no markets found for {date} with prefix {args.prefix}; nothing to watch", file=sys.stderr)
        return 2

    until_ts = parse_until(args.until, date)          # after discovery so +Ns counts from the start of watching
    global LOOP_IMPL
    LOOP_IMPL = install_uvloop()
    w = Watcher(doc, env, until_ts, out_dir, name, date, args.tag)
    w.log_fh = open(out_dir / (name + ".jsonl"), "a", encoding="utf-8")
    games = sorted({lc.game_of(t) for t in w.tickers})
    w.write(dict(event="start", time=iso(time.time()), date=date, tag=args.tag, prefix=args.prefix, structure=src,
                 tickers=len(w.tickers), events=len(doc["events"]), games=games, until=iso(until_ts),
                 platform=platform.platform(), python=platform.python_version(), json_impl=JSON_IMPL,
                 loop=LOOP_IMPL, games_filter=games_f))
    print(f"lockwatch_fast [{JSON_IMPL}/{LOOP_IMPL}] {date}/{args.tag}: structure from {src}; {len(games)} games, {len(doc['events'])} events, "
          f"{len(w.tickers)} tickers, {len(w.det.groups)} ladder groups, {len(w.det.me_sets)} ME sets; "
          f"until {iso(until_ts)}; stop via {out_dir / (name + '.STOP')}", flush=True)
    try:
        asyncio.run(w.run())
    except KeyboardInterrupt:
        print("interrupted")
    finally:
        w.log_fh.close()
    print(f"done: tickers={len(w.tickers)} msgs={w.n_msgs} snapshots={w.snapshots} peak_valid_books={w.peak_valid} "
          f"reconnects={w.reconnects} seq_gaps={w.seq_gaps} errors={w.errors} locks_opened={w.det.n_opened} "
          f"delay_samples={w.n_samples} sntp_runs={w.n_sntp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
