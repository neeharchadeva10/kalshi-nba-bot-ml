"""NBA lock BOT: lockwatch_fast detection (lockcore, unchanged) + a guarded taker order executor.

  DRY (default; never builds an order client, logs "would_send"):
    KALSHI_ENV=prod python reports/output/ohio_trial/lockbot.py --date 26OCT21 --games 26OCT21AAABBB --tag AAABBB
  LIVE (real money; one process per game, all share the night ledger):
    KALSHI_ENV=prod python reports/output/ohio_trial/lockbot.py --date 26OCT21 --games 26OCT21AAABBB --tag AAABBB --live
  Stop: touch <out-dir>/lockbot_<date>_<tag>.STOP

Needs lockwatch_fast.py and lockcore.py next to this file. An eligible lock = every leg bought IOC at its detected taker
price, all legs sent concurrently; nothing is ever sold or cancelled (held to settlement).
The ONLY order-sending code path is OrderSender.post(), reachable only from Bot.attempt() when args.live is true.
Secrets are never printed or logged.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lockwatch_fast as lf  # noqa: E402
import lockcore as lc  # noqa: E402

# ------------------------------------------------------------------ LIVE-MODE LIMITS (defaults; override by CLI flag only)
COUNT = 1                      # contracts per leg
MIN_PROFIT = Decimal("0.01")   # dollars per set after exact taker fees on every leg
GAME_CAP = Decimal("5.00")     # at-risk dollars per game
NIGHT_CAP = Decimal("15.00")   # at-risk dollars per night, shared across processes via the ledger file
MAX_ATTEMPTS_PER_GAME = 15
MAX_ORDER_ERRORS = 3
MAX_LEGS = 3
REFIRE_S = 2.0
WORKERS = 8
KEEPALIVE_S = 15.0
ORDER_ENDPOINT = "/portfolio/events/orders"        # CreateOrderV2, same as ExecClient.create_order
D4 = Decimal("0.0001")


def D(x):
    return Decimal(str(x)) if x not in (None, "") else None


def fee(count, p) -> Decimal:
    """Taker fee = ceil(0.07*C*P*(1-P)/0.0001)*0.0001 at the price P paid for the side bought (same as approve.py)."""
    p = D(p)
    raw = Decimal("0.07") * Decimal(count) * p * (1 - p) / D4
    return Decimal(math.ceil(raw)) * D4


# ------------------------------------------------------------------ pure order / risk math
def build_body(ticker: str, side: str, p_side, count, exchange_index: int, coid: str | None = None) -> dict:
    """YES buy = bid at the YES price p; NO buy = ask at the YES price 1 - p_no (identical to approve.py / wassf_trade.py)."""
    from kpm.execution.client import validate_order_body
    p = D(p_side)
    return validate_order_body({
        "ticker": ticker, "client_order_id": coid or str(uuid.uuid4()), "side": "bid" if side == "yes" else "ask",
        "count": f"{Decimal(count):.2f}", "price": f"{(p if side == 'yes' else 1 - p):.4f}",
        "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross",
        "exchange_index": exchange_index})


def complete_payout(kind: str, n_legs: int, count) -> Decimal:
    """Payout of the full lock: ladder/me_yes pay >= 1 per set, me_no pays >= N-1 per set."""
    return Decimal(count) * (n_legs - 1 if kind == "me_no" else 1)


def guaranteed_payout(kind: str, fills: list) -> Decimal:
    """Worst-case payout of what was actually filled (fills: contracts per leg, 0 if unfilled)."""
    f = [Decimal(x) for x in fills]
    if not f:
        return Decimal(0)
    if kind == "me_no":                       # N NOs on a mutually exclusive set: worst case the biggest leg is the YES
        return sum(f) - max(f)
    return min(f)                             # ladder (YES weak + NO strong) and me_yes: every leg must be held


def make_plan(lock: dict, count, min_profit, max_legs) -> dict | None:
    """lock = a lockcore open-lock state. -> plan dict, or None if not eligible (reason in plan['skip'])."""
    legs = []
    for lg, p in zip(lock["legs"], lock["prices"]):
        side, tk = lg.split(" ", 1)
        ps = D(round(p, 4))
        legs.append(dict(ticker=tk, side=side.lower(), p_side=ps, fee=fee(count, ps), cost=Decimal(count) * ps))
    reserve = sum(l["cost"] + l["fee"] for l in legs)
    profit = complete_payout(lock["kind"], len(legs), count) - reserve
    plan = dict(kind=lock["kind"], count=count, legs=legs, reserve=reserve, exp_profit=profit)
    if len(legs) > max_legs:
        plan["skip"] = f"legs {len(legs)} > max_legs {max_legs}"
    elif any(not (0 < l["p_side"] < 1) for l in legs):
        plan["skip"] = "price outside (0,1)"
    elif profit < Decimal(str(min_profit)):
        plan["skip"] = f"exact profit {profit:.4f} < min_profit {min_profit}"
    return plan


def parse_leg(leg: dict, status, data, exc=None) -> dict:
    """Parse a create-order reply exactly as approve.py: fill_count, average_fill_price (YES price), average_fee_paid
    (per contract). `unknown` = the order may have filled (transport error, 5xx, 3xx, malformed 2xx)."""
    out = dict(ticker=leg["ticker"], side=leg["side"], status=status, ok=False, order_id=None, fill=Decimal(0),
               avg_price=None, fee_each=Decimal(0), unknown=False, error=None)
    if exc is not None or status is None:
        out.update(unknown=True, error=type(exc).__name__ if exc is not None else "no_status")
        return out
    if 200 <= status < 300:
        if not isinstance(data, dict) or not isinstance(data.get("order_id"), str) or not data["order_id"]:
            out.update(unknown=True, error="missing_order_id")
            return out
        try:
            filled = D(data.get("fill_count")) or Decimal(0)
            yp = D(data.get("average_fill_price"))
            fe = D(data.get("average_fee_paid")) or Decimal(0)
        except Exception:
            out.update(unknown=True, error="bad_fields", order_id=data["order_id"])
            return out
        out.update(ok=True, order_id=data["order_id"], fill=filled, fee_each=fe,
                   avg_price=None if yp is None else (yp if leg["side"] == "yes" else 1 - yp))
        return out
    out["error"] = f"http_{status}"
    out["unknown"] = status >= 500 or 300 <= status < 400          # 4xx (incl. 429) = definitely not placed
    return out


def settle_math(plan: dict, results: list) -> dict:
    """At-risk of one attempt = filled cost + fees - guaranteed payout (>= 0), plus the full cost of any leg whose
    outcome is unknown (conservative: assume it filled and pays nothing)."""
    cost = fees = Decimal(0)
    fills = []
    unknown_risk = Decimal(0)
    for leg, r in zip(plan["legs"], results):
        if r["unknown"]:
            unknown_risk += leg["cost"] + leg["fee"]
            fills.append(Decimal(0))
            continue
        f = r["fill"]
        fills.append(f)
        if f > 0:
            px = r["avg_price"] if r["avg_price"] is not None else leg["p_side"]
            fe = r["fee_each"] * f if r["avg_price"] is not None else fee(f, leg["p_side"])
            cost += f * px
            fees += fe
    guaranteed = guaranteed_payout(plan["kind"], fills)
    at_risk = max(Decimal(0), cost + fees - guaranteed) + unknown_risk
    n_full = sum(1 for f in fills if f >= Decimal(plan["count"]))
    if unknown_risk > 0:
        status = "unknown"
    elif n_full == len(fills):
        status = "complete"
    elif any(f > 0 for f in fills):
        status = "partial"
    else:
        status = "none"
    return dict(status=status, cost=cost, fees=fees, guaranteed=guaranteed, at_risk=at_risk, unknown_risk=unknown_risk,
                fills=fills)


# ------------------------------------------------------------------ shared night ledger (atomic file, cross-process)
class Ledger:
    """JSON file {"settled": {game: at_risk}, "reserved": {rid: {"game", "amt"}}}. Every read-modify-write holds a
    lock file; the write is temp file + os.replace. Reservations count against the caps until settled (a crashed
    process leaves its reservation in place = conservative)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lockp = Path(str(path) + ".lock")

    @contextmanager
    def _lock(self):
        t0 = time.monotonic()
        while True:
            try:
                fd = os.open(self.lockp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                try:
                    if time.time() - self.lockp.stat().st_mtime > 5.0:        # stale lock from a dead process
                        self.lockp.unlink()
                        continue
                except OSError:
                    pass
                if time.monotonic() - t0 > 3.0:
                    raise TimeoutError("ledger lock busy")
                time.sleep(0.002)
        try:
            yield
        finally:
            try:
                self.lockp.unlink()
            except OSError:
                pass

    def _read(self) -> dict:
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
        doc.setdefault("settled", {})
        doc.setdefault("reserved", {})
        return doc

    def _write(self, doc: dict) -> None:
        tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def totals(doc: dict, game: str) -> tuple:
        gs = sum((D(v) for g, v in doc["settled"].items() if g == game), Decimal(0))
        gr = sum((D(r["amt"]) for r in doc["reserved"].values() if r["game"] == game), Decimal(0))
        ns = sum((D(v) for v in doc["settled"].values()), Decimal(0))
        nr = sum((D(r["amt"]) for r in doc["reserved"].values()), Decimal(0))
        return gs + gr, ns + nr                                  # (game at-risk incl. reserved, night at-risk incl. reserved)

    def reserve(self, rid: str, game: str, amt, game_cap, night_cap) -> tuple:
        """-> (ok, reason, game_total, night_total). On ok the reservation is recorded."""
        amt = D(amt)
        with self._lock():
            doc = self._read()
            g, n = self.totals(doc, game)
            if g + amt > D(game_cap):
                return False, "game_cap", g, n
            if n + amt > D(night_cap):
                return False, "night_cap", g, n
            doc["reserved"][rid] = dict(game=game, amt=str(amt))
            self._write(doc)
            return True, None, g + amt, n + amt

    def settle(self, rid: str, game: str, at_risk) -> tuple:
        with self._lock():
            doc = self._read()
            doc["reserved"].pop(rid, None)
            doc["settled"][game] = str(D(doc["settled"].get(game, "0")) + D(at_risk))
            self._write(doc)
            return self.totals(doc, game)

    def snapshot(self, game: str) -> tuple:
        with self._lock():
            return self.totals(self._read(), game)


# ------------------------------------------------------------------ public market info + order sender
def fetch_exchange_index(env, ticker: str, _tls=threading.local()):
    """GET /markets/{ticker} (public, unsigned) -> exchange_index, or None. Read-only."""
    from kpm.execution.client import RequestsTransport, USER_AGENT
    tr = getattr(_tls, "tr", None)
    if tr is None:
        tr = _tls.tr = RequestsTransport()
    for attempt in range(5):
        try:
            r = tr.send("GET", f"{env.rest_base}/markets/{ticker}", params=None, data=None,
                        headers={"Accept": "application/json", "User-Agent": USER_AGENT}, timeout=10.0)
            if r.status_code == 429:
                time.sleep(0.5 * (attempt + 1))
                continue
            if r.status_code == 200:
                ei = (r.json().get("market") or {}).get("exchange_index")
                if isinstance(ei, int) and not isinstance(ei, bool):
                    return ei
        except Exception:
            pass
    return None


class OrderSender:
    """Persistent keep-alive sender: one RequestsTransport (requests.Session) per worker thread, warmed at startup.
    Signing/headers/host/path as ExecClient.create_order. Constructed ONLY in live mode."""

    def __init__(self, env, workers: int = WORKERS):
        from kpm.execution.auth import auth_headers, sign_path
        from kpm.execution.client import RequestsTransport, USER_AGENT
        self.env, self.workers = env, workers
        self._auth, self._ua, self._tr_cls = auth_headers, USER_AGENT, RequestsTransport
        self.path = sign_path(env.rest_base, ORDER_ENDPOINT)
        self.url = env.rest_base + ORDER_ENDPOINT
        self.tls = threading.local()
        self.pool = lf.ThreadPoolExecutor(workers, initializer=self._init_thread)

    def _init_thread(self):
        self.tls.tr = self._tr_cls()

    def warm(self, timeout: float = 10.0) -> int:
        """Open/refresh one connection per worker (public GET /exchange/status, unsigned). -> workers warmed."""
        bar = threading.Barrier(self.workers, timeout=timeout)

        def one():
            try:
                bar.wait()
                r = self.tls.tr.send("GET", f"{self.env.rest_base}/exchange/status", params=None, data=None,
                                     headers={"Accept": "application/json", "User-Agent": self._ua}, timeout=10.0)
                return r.status_code < 500          # any HTTP reply (incl. a 429 to the burst) = connection is open
            except Exception:
                return False
        return sum(1 for f in [self.pool.submit(one) for _ in range(self.workers)] if f.result())

    def post(self, leg: dict, body: dict) -> dict:
        """THE order-sending code path. Blocking; run in a worker thread."""
        headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": self._ua}
        headers.update(self._auth(self.env.key, self.env.key_id, "POST", self.path))      # fresh timestamp per send
        data = lf.dumps(body)
        send_ns = time.time_ns()
        try:
            resp = self.tls.tr.send("POST", self.url, params=None, data=data, headers=headers, timeout=10.0)
            resp_ns = time.time_ns()
            try:
                js = resp.json()
            except ValueError:
                js = None
            res = parse_leg(leg, int(resp.status_code), js)
            res["raw"] = (resp.text or "")[:400] if not res["ok"] else None
        except Exception as exc:                        # timeout / connection: the order may have been sent
            resp_ns = time.time_ns()
            res = parse_leg(leg, None, None, exc)
        res.update(send_ns=send_ns, response_ns=resp_ns)
        return res


# ------------------------------------------------------------------ the bot
class GrowDetector(lc.Detector):
    """lockcore.Detector + a 'lock_grow' event carrying the CURRENT lock (prices/sizes/profit) whenever an already-open
    lock's profit rises, so a lock that opened below min-profit and then grew can still be traded (2026-10-21)."""

    def update(self, t: str, now: float, meta: dict | None = None):
        f1, s1 = self._ladder_keys(t)
        f2, s2 = self._me_keys(t)
        found, scope = {**f1, **f2}, s1 | s2
        for key, (lk, tks) in found.items():
            st = self.open.get(key)
            if st is None:
                self.open[key] = dict(lk, t_open=now, max_profit=lk["profit"], max_n=lk["n"], tickers=tks, key=key,
                                      first=lk, open_meta=meta)
                self.n_opened += 1
                self.emit("lock_open", self.open[key], now)
            else:
                grew = lk["profit"] > st["max_profit"]
                if grew:
                    st["max_profit"] = lk["profit"]
                st["max_n"] = max(st["max_n"], lk["n"])
                if grew:
                    self.emit("lock_grow", dict(lk, key=key, tickers=tks, open_meta=meta), now)
        for key in [k for k in self.open if k in scope and k not in found]:
            st = self.open.pop(key)
            st["close_meta"] = meta
            self.emit("lock_close", st, now)


class Bot(lf.Watcher):
    def __init__(self, doc, env, until_ts, out_dir: Path, name: str, date: str, tag: str, args, ledger: Ledger):
        super().__init__(doc, env, until_ts, out_dir, name, date, tag)
        self.det = GrowDetector(doc, self.tops, self.emit)        # also fires when an open lock grows past min-profit
        self.args, self.ledger = args, ledger
        self.live = bool(args.live)
        self.count = int(args.count)
        self.game_cap, self.night_cap = D(args.game_cap), D(args.night_cap)
        self.game = sorted({lc.game_of(t) for t in self.tickers})[0] if self.tickers else ""
        self.games = sorted({lc.game_of(t) for t in self.tickers})
        self.ei: dict = {}
        self.sender: OrderSender | None = None
        self.inflight: set = set()
        self.last_fire: dict = {}                     # lock key / ticker -> monotonic of last fire
        self.tasks: set = set()
        self.halted = None
        self.attempts = self.fills = self.n_complete = self.n_partial = self.n_none = self.n_unknown = 0
        self.order_errors = 0
        self.eligible = self.skipped = 0
        self.game_risk = self.night_risk = Decimal(0)

    # ---- trigger
    def emit(self, ev, st, now):
        if ev != "lock_grow":                     # the base watcher logs only lock_open / lock_close
            super().emit(ev, st, now)
        if ev not in ("lock_open", "lock_grow"):  # lock_grow: an open lock's profit rose (current prices in st)
            return
        t_dec = time.time_ns()
        try:
            self.consider(st, t_dec)
        except Exception as exc:                                   # never let trading code break the feed
            self.errors += 1
            self.write(dict(event="bot_error", time=lf.iso(time.time()), where="consider", exc=type(exc).__name__,
                            msg=str(exc)[:200]))

    def halt(self, reason):
        if self.halted is None:
            self.halted = reason
            self.write(dict(event="stop", time=lf.iso(time.time()), reason=reason, attempts=self.attempts,
                            order_errors=self.order_errors))
            print(f"{lf.iso(time.time())} TRADING HALTED: {reason}", flush=True)

    def consider(self, st, t_dec_ns):
        key = repr(st["key"])
        om = st.get("open_meta") or {}
        plan = make_plan(st, self.count, self.args.min_profit, self.args.max_legs)
        if plan is None:
            return
        if "skip" in plan:
            self.skipped += 1
            if not plan["skip"].startswith("exact profit"):
                self.write(dict(event="skip", time=lf.iso(time.time()), key=key, reason=plan["skip"]))
            return
        self.eligible += 1
        self.write(dict(event="eligible", time=lf.iso(time.time()), key=key, kind=plan["kind"], legs=st["legs"],
                        prices=st["prices"], sizes=st["sizes"], n=st["n"], exp_profit=str(plan["exp_profit"]),
                        reserve=str(plan["reserve"]), trigger_recv_ns=om.get("recv_ns"), trigger_ts_ms=om.get("ts_ms")))
        if self.halted or self.stop_now():
            return
        mono = time.monotonic()
        tks = [l["ticker"] for l in plan["legs"]]
        if key in self.inflight or any(t in self.inflight for t in tks):
            return self.write(dict(event="skip", time=lf.iso(time.time()), key=key, reason="in_flight"))
        if any(mono - self.last_fire.get(k, -1e9) < self.args.refire_s for k in [key] + tks):
            return self.write(dict(event="skip", time=lf.iso(time.time()), key=key, reason="refire_guard"))
        if self.attempts >= self.args.max_attempts_per_game:
            return self.halt("max_attempts_per_game")
        rid = uuid.uuid4().hex
        ok, why, g, n = self.ledger.reserve(rid, self.game, plan["reserve"], self.game_cap, self.night_cap)
        if not ok:
            self.write(dict(event="cap_block", time=lf.iso(time.time()), key=key, cap=why, reserve=str(plan["reserve"]),
                            game_at_risk=str(g), night_at_risk=str(n)))
            return self.halt(f"{why}_would_be_exceeded")
        self.game_risk, self.night_risk = g, n
        self.attempts += 1
        for k in [key] + tks:
            self.last_fire[k] = mono
        self.inflight.update([key] + tks)
        # 2026-10-21: send NOW, from the detection call itself. Scheduling the send as an asyncio task let it wait
        # behind the frame backlog in bursts (26-127 ms seen live); the sender threads go out immediately instead.
        pre, prep_exc = None, None
        try:
            eis = [self.ei.get(l["ticker"]) for l in plan["legs"]]
            if any(e is None for e in eis):
                raise RuntimeError("exchange_index not cached")
            bodies = [build_body(l["ticker"], l["side"], l["p_side"], plan["count"], e) for l, e in zip(plan["legs"], eis)]
            futs = ([self.sender.pool.submit(self.sender.post, l, b) for l, b in zip(plan["legs"], bodies)]
                    if self.live else None)
            t_ready = time.time_ns()
            decision_us = (t_ready - om["recv_ns"]) // 1000 if om.get("recv_ns") else (t_ready - t_dec_ns) // 1000
            pre = (bodies, futs, decision_us)
        except Exception as exc:
            prep_exc = exc
        t = asyncio.get_running_loop().create_task(self.attempt(plan, key, rid, st, om, t_dec_ns, pre, prep_exc))
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    # ---- execution
    async def get_ei(self, loop, ticker):
        if ticker not in self.ei:
            ei = await loop.run_in_executor(None, fetch_exchange_index, self.env, ticker)
            if ei is not None:
                self.ei[ticker] = ei
        return self.ei.get(ticker)

    async def attempt(self, plan, key, rid, st, om, t_dec_ns, pre=None, prep_exc=None):
        loop = asyncio.get_running_loop()
        bodies = []
        legs = plan["legs"]
        try:
            if prep_exc is not None:
                raise prep_exc
            bodies, futs, decision_us = pre
            if self.live:                                   # orders already sent from consider(); just collect
                res = list(await asyncio.gather(*[asyncio.wrap_future(f) for f in futs]))
            else:                                           # DRY: nothing is ever sent
                self.write(dict(event="would_send", time=lf.iso(time.time()), key=key, kind=plan["kind"], bodies=bodies,
                                decision_latency_us=decision_us, exp_profit=str(plan["exp_profit"]),
                                trigger_recv_ns=om.get("recv_ns"), trigger_ts_ms=om.get("ts_ms")))
                res = [dict(ticker=l["ticker"], side=l["side"], ok=True, fill=Decimal(plan["count"]), avg_price=l["p_side"],
                            fee_each=l["fee"] / Decimal(plan["count"]), unknown=False, simulated=True, order_id=None,
                            send_ns=None, response_ns=None) for l in legs]
        except Exception as exc:
            self.errors += 1
            self.write(dict(event="attempt_error", time=lf.iso(time.time()), key=key, exc=type(exc).__name__,
                            msg=str(exc)[:200]))
            res = [dict(ticker=l["ticker"], side=l["side"], ok=False, fill=Decimal(0), avg_price=None,
                        fee_each=Decimal(0), unknown=False, error="attempt_exception",
                        order_id=None, send_ns=None, response_ns=None) for l in legs]
            decision_us = None
        sm = settle_math(plan, res)
        self.order_errors += sum(1 for r in res if not r["ok"])
        g, n = self.ledger.settle(rid, self.game, sm["at_risk"])
        self.game_risk, self.night_risk = g, n
        self.fills += int(sum(sm["fills"]))
        attr = {"complete": "n_complete", "partial": "n_partial", "none": "n_none", "unknown": "n_unknown"}[sm["status"]]
        setattr(self, attr, getattr(self, attr) + 1)
        self.write(dict(event="attempt", time=lf.iso(time.time()), key=key, mode="live" if self.live else "dry",
                        kind=plan["kind"], decision_latency_us=decision_us, bodies=bodies,
                        legs=[dict(ticker=r["ticker"], side=r["side"], ok=r["ok"], order_id=r["order_id"],
                                   fill_count=str(r["fill"]), avg_price=None if r["avg_price"] is None else str(r["avg_price"]),
                                   fee_each=str(r["fee_each"]), unknown=r["unknown"], error=r.get("error"),
                                   raw=r.get("raw"), send_ns=r.get("send_ns"), response_ns=r.get("response_ns"))
                              for r in res],
                        summary=sm["status"], realized_cost=str(sm["cost"] + sm["fees"]), guaranteed_payout=str(sm["guaranteed"]),
                        at_risk=str(sm["at_risk"]), game_at_risk=str(g), night_at_risk=str(n)))
        print(f"{lf.iso(time.time())} ATTEMPT {'LIVE' if self.live else 'dry'} {plan['kind']} {sm['status']} "
              f"at_risk=${sm['at_risk']:.4f} night=${n:.4f}", flush=True)
        self.inflight.difference_update([key] + [l["ticker"] for l in legs])
        if self.live and self.order_errors >= self.args.max_order_errors:
            self.halt(f"order_errors>={self.args.max_order_errors}")
        if sm["status"] == "unknown":
            self.halt("unknown_order_outcome_needs_reconcile")

    # ---- heartbeat / lifecycle
    def heartbeat(self, now):
        super().heartbeat(now)
        try:
            g, n = self.ledger.snapshot(self.game)
        except Exception:
            g, n = self.game_risk, self.night_risk
        bot = dict(mode="live" if self.live else "dry", attempts=self.attempts, fills=self.fills, complete=self.n_complete,
                   partial=self.n_partial, none=self.n_none, unknown=self.n_unknown, order_errors=self.order_errors,
                   eligible=self.eligible, halted=self.halted, game_at_risk=str(g), night_at_risk=str(n),
                   game_cap=str(self.game_cap), night_cap=str(self.night_cap))
        self.write(dict(event="bot_heartbeat", time=lf.iso(now), **bot))
        hp = self.out_dir / (self.name + ".heartbeat")
        try:
            doc = json.loads(hp.read_text(encoding="utf-8"))
            doc.update(bot)
            tmp = self.out_dir / (self.name + ".heartbeat.tmp")
            tmp.write_text(json.dumps(doc), encoding="utf-8")
            tmp.replace(hp)
        except (OSError, ValueError):
            pass

    async def keepalive(self):
        while not self.stop_now():
            for _ in range(int(KEEPALIVE_S)):
                if self.stop_now():
                    return
                await asyncio.sleep(1.0)
            if not self.inflight and self.sender is not None:
                try:
                    await asyncio.get_running_loop().run_in_executor(None, self.sender.warm)
                except Exception:
                    pass

    async def run(self):
        ka = asyncio.create_task(self.keepalive()) if self.live else None
        try:
            await super().run()
        finally:
            if ka:
                ka.cancel()
            if self.tasks:
                await asyncio.gather(*list(self.tasks), return_exceptions=True)


# ------------------------------------------------------------------ main
def startup_checks(env, tickers, args, ledger_path):
    """Live preflight (read-only). -> (sender, ei cache, cash by shard). SystemExit on any failure."""
    from kpm.execution.client import ExecClient
    ro = ExecClient(env, live=False)                       # read-only: balance reads only
    shards = sorted({e for e in ei_cache_values(tickers) if e is not None})
    if not shards:
        raise SystemExit("no exchange_index could be fetched; refusing to start live")
    cash = {s: ro.shard_cash(s) for s in shards}
    # user 2026-10-20: "$5 for each game" -> a shard is tradable only if its cash covers one game's cap; markets on
    # unfunded shards are dropped (each shard has its own balance: KXNBAGAME is shard 0, the rest of KXNBA shard 3).
    # A night cap above cash is allowed: cash is then the real ceiling.
    funded = {s for s, c in cash.items() if c is not None and c >= D(args.game_cap)}
    if not funded:
        raise SystemExit(f"no shard has cash >= game-cap {args.game_cap} (cash by shard: "
                         f"{ {s: str(c) for s, c in cash.items()} }); refusing to start live")
    dropped = {s: str(c) for s, c in cash.items() if s not in funded}
    if dropped:
        print(f"WARNING: shard(s) {dropped} below game-cap {args.game_cap}: their markets are not traded", flush=True)
    short = {s: str(c) for s, c in cash.items() if s in funded and c < D(args.night_cap)}
    if short:
        print(f"WARNING: balance below night-cap {args.night_cap} on shard(s) {short}; cash is the real ceiling",
              flush=True)
    return cash, funded


_EI: dict = {}


def ei_cache_values(tickers):
    return [_EI.get(t) for t in tickers]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", required=True)
    ap.add_argument("--prefix", default="KXNBA")
    ap.add_argument("--until", default=None)
    ap.add_argument("--tag", default="home")
    ap.add_argument("--games", default=None)
    ap.add_argument("--structure", default=None)
    ap.add_argument("--out-dir", default=str(lf.ROOT / "logs"))
    ap.add_argument("--live", action="store_true", help="REAL MONEY. Without it: dry, nothing is ever sent.")
    ap.add_argument("--count", type=int, default=COUNT)
    ap.add_argument("--min-profit", type=Decimal, default=MIN_PROFIT)
    ap.add_argument("--game-cap", type=Decimal, default=GAME_CAP)
    ap.add_argument("--night-cap", type=Decimal, default=NIGHT_CAP)
    ap.add_argument("--max-attempts-per-game", type=int, default=MAX_ATTEMPTS_PER_GAME)
    ap.add_argument("--max-order-errors", type=int, default=MAX_ORDER_ERRORS)
    ap.add_argument("--max-legs", type=int, default=MAX_LEGS)
    ap.add_argument("--refire-s", type=float, default=REFIRE_S)
    args = ap.parse_args()
    if args.count < 1 or args.game_cap <= 0 or args.night_cap <= 0:
        raise SystemExit("count/caps must be positive")
    date = args.date.upper()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"lockbot_{date}_{args.tag}"
    games_f = lf.parse_games(args.games)
    if args.live and len(games_f) != 1:
        raise SystemExit("--live requires exactly one game in --games (one process per game)")

    from kpm.execution.env import load_env
    env = load_env(require_keys=True, expected="prod")
    import kpm.execution.client  # noqa: F401  (load now: the first trade must not pay the import, 26 ms seen live)
    if env.name != "prod":
        raise SystemExit("KALSHI_ENV must be prod")

    sp = out_dir / (f"lockwatch_night_{date}_{args.tag}.structure.json")
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
            doc = lf.filter_games(doc, games_f)
            doc = doc if doc["books"] else None
    if doc is None:
        doc = lf.load_structure(sp, date, args.prefix)
        src = "cache"
        if doc is not None and games_f:
            doc = lf.filter_games(doc, games_f)
            doc = doc if doc["books"] else None
    if doc is None:
        src = "REST"
        doc = lf.discover(date, args.prefix)
        if games_f:
            doc = lf.filter_games(doc, games_f)
        sp.write_text(json.dumps(doc), encoding="utf-8")
    if not doc["books"]:
        print(f"no markets found for {date}; nothing to do", file=sys.stderr)
        return 2

    tickers = list(doc["books"])
    with lf.ThreadPoolExecutor(6) as ex:                  # prefetch exchange_index (public GETs), cached for the run
        for t, e in zip(tickers, ex.map(lambda t: fetch_exchange_index(env, t), tickers)):
            if e is not None:
                _EI[t] = e
    ledger_path = out_dir / (f"lockbot_{date}_ledger.json" if args.live else f"lockbot_{date}_ledger_dry.json")
    sender, cash = None, None
    if args.live:
        cash, funded = startup_checks(env, tickers, args, ledger_path)
        keep = {t for t in tickers if _EI.get(t) in funded}
        doc = dict(doc, books={t: b for t, b in doc["books"].items() if t in keep})
        tickers = list(doc["books"])
        print(f"trading {len(tickers)} markets on funded shard(s) {sorted(funded)}", flush=True)
        sender = OrderSender(env)
        warmed = 0
        for i in range(4):                                 # retry: a cold start can hit a burst limit
            warmed = sender.warm()
            if warmed == sender.workers:
                break
            time.sleep(1.5 * (i + 1))
        if warmed < sender.workers:
            raise SystemExit(f"only {warmed}/{sender.workers} connections warmed after retries; refusing to start live")

    until_ts = lf.parse_until(args.until, date)
    lf.LOOP_IMPL = lf.install_uvloop()
    bot = Bot(doc, env, until_ts, out_dir, name, date, args.tag, args, Ledger(ledger_path))
    bot.ei, bot.sender = _EI, sender
    bot.log_fh = open(out_dir / (name + ".jsonl"), "a", encoding="utf-8")
    games = sorted({lc.game_of(t) for t in tickers})
    limits = dict(count=args.count, min_profit=str(args.min_profit), game_cap=str(args.game_cap),
                  night_cap=str(args.night_cap), max_attempts_per_game=args.max_attempts_per_game,
                  max_order_errors=args.max_order_errors, max_legs=args.max_legs, refire_s=args.refire_s)
    mode = "LIVE" if args.live else "DRY"
    bot.write(dict(event="start", time=lf.iso(time.time()), mode=mode, limits=limits, games=games, tag=args.tag, date=date,
                   structure=src, tickers=len(tickers), ei_cached=len(_EI), ledger=str(ledger_path),
                   cash_by_shard=None if cash is None else {str(k): str(v) for k, v in cash.items()},
                   until=lf.iso(until_ts), platform=platform.platform(), python=platform.python_version(),
                   json_impl=lf.JSON_IMPL, loop=lf.LOOP_IMPL))
    print(f"lockbot {mode} [{lf.JSON_IMPL}/{lf.LOOP_IMPL}] {date}/{args.tag} games={games} limits={limits} "
          f"tickers={len(tickers)} ei_cached={len(_EI)} cash={cash and {k: str(v) for k, v in cash.items()}} "
          f"until {lf.iso(until_ts)}; stop via {out_dir / (name + '.STOP')}", flush=True)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("interrupted")
    finally:
        bot.log_fh.close()
    print(f"done: mode={mode} msgs={bot.n_msgs} locks_opened={bot.det.n_opened} eligible={bot.eligible} "
          f"attempts={bot.attempts} complete={bot.n_complete} partial={bot.n_partial} none={bot.n_none} "
          f"unknown={bot.n_unknown} order_errors={bot.order_errors} halted={bot.halted} "
          f"game_at_risk={bot.game_risk} night_at_risk={bot.night_risk}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
