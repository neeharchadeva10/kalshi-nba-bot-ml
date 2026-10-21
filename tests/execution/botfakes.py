"""Shared fakes for the strategy tests (test_late_favorite.py, test_pair_h2.py). Not a test module.

* :class:`Clock`          manual clock whose ``sleep`` advances it (no real waiting)
* :class:`FakeExchange`   the slice of ExecClient the bots use, backed by a small consistent exchange: orders, fills,
                          positions and settlements agree with each other, so the REAL kpm.execution.reconcile has
                          something true to compare. Subclasses add ``open_markets`` and ``orderbook``.
* :class:`FakeSafety`     stands in for kpm.execution.safety.Safety (``check_entry(spec, book, market) -> (ok, reason)``)
* :class:`FakeReconcile`  reconcile / settle / cancel_path as the bots call them
* :func:`real_stack`      swaps the fakes for the real Safety and reconcile modules
* :class:`RecState`       BotState that remembers every heartbeat time

The REAL BotState and Ledger run on in-memory SQLite in the tests, so the rows the dashboard reads and the numbers the
loss cap uses are the production code paths. The autouse guards in conftest.py block the network.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal as D
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from kpm.execution import reconcile as real_reconcile
from kpm.execution import units
from kpm.execution.botstate import BotState
from kpm.execution.client import Result, validate_order_body
from kpm.execution.safety import Safety

TAPERED = [{"start": "0.0000", "end": "0.1000", "step": "0.0010"},
           {"start": "0.1000", "end": "0.9000", "step": "0.0100"},
           {"start": "0.9000", "end": "1.0000", "step": "0.0010"}]
LINEAR_CENT = [{"start": "0.0000", "end": "1.0000", "step": "0.0100"}]


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def micro_fee(count: str, price: str) -> D:
    """Independent re-implementation of the quadratic taker fee, rounded UP to $0.000001."""
    return (D("0.07") * D(count) * D(price) * (1 - D(price))).quantize(D("0.000001"), rounding=ROUND_CEILING)


def worst_of(price: str, count: str = "1") -> D:
    """Collateral + worst-case fee of `count` contracts bought at `price` (fee rounded up to $0.0001, plus one step)."""
    fee = (D("0.07") * D(count) * D(price) * (1 - D(price))).quantize(D("0.0001"), rounding=ROUND_CEILING) + D("0.0001")
    return D(price) * D(count) + fee


def lv(*pairs: tuple[str, str]) -> list[tuple[D, D]]:
    """Ascending REST levels as ExecClient.orderbook returns them: [(price, size), ...] (Decimals)."""
    return [(D(p), D(s)) for p, s in pairs]


def worse(price: str) -> str:
    """A strictly lower, positive price on the 4-dp grid (half the price)."""
    return f"{(D(price) / 2).quantize(D('0.0001')):.4f}"


def book(yes_bid: str, no_bid: str, ys: str = "800", ns: str = "900") -> tuple[list, list]:
    """A book whose BEST bids are last: a worse level first proves the bot reads the last element."""
    return (lv((worse(yes_bid), "7"), (yes_bid, ys)), lv((worse(no_bid), "3"), (no_bid, ns)))


class Clock:
    """Manual clock: ``sleep`` advances it (no real waiting)."""

    def __init__(self, start: float):
        self.now = float(start)
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))
        self.now += float(seconds)


class FakeExchange:
    """A consistent fake exchange behind the ExecClient surface the bots call. Every call is recorded in ``calls`` as
    ``(name, args, time)`` and advances the clock by ``latency``.

    Scripting (consumed in order, ``"fill"`` when empty): ``script`` for every order, ``script_by_ticker[ticker]`` first
    for that ticker. A step is ``"fill"``, ``"fill:0.40"``, ``"nofill"``, ``"timeout_placed"`` (the response is lost but
    the order filled), ``"timeout_lost"`` (the order never arrived), ``"reject"``, ``"auth"``, ``"duplicate"``, an
    exception instance (raised) or a callable ``f(body) -> Result``. ``batch_script`` does the same for a whole
    ``batch_create`` call (``"timeout_placed"`` / ``"timeout_lost"`` / ``"raise_placed"`` / ``"raise_lost"``).
    """

    def __init__(self, clock: Clock, *, results: dict | None = None, live: bool = False, latency: float = 0.0):
        self.clock = clock
        self.results = dict(results or {})   # ticker -> "yes" | "no"
        self.live = live
        self.latency = latency
        self.calls: list[tuple] = []
        self.known: dict[str, dict] = {}
        self.market_overrides: dict[str, dict] = {}   # ticker -> fields replacing the market object
        self.settle_lag = 5.0                # the market result is public this long after the close
        self.settlement_lag_s: float | None = 0.0    # GET /portfolio/settlements publishes this long after the close
        self.fills_after: float | None = None        # fills() returns nothing before this time
        self.script: list = []
        self.script_by_ticker: dict[str, list] = {}
        self.batch_script: list = []
        self.created: list[dict] = []
        self.batches: list[list[dict]] = []
        self.orders_db: dict[str, dict] = {}
        self.fills_db: dict[str, list] = {}
        self.fail: dict[str, BaseException] = {}
        self.ledger = None                   # set by the Sim: lets create_order assert the intent is already committed
        self.n_orders = 0

    # ---------------------------------------------------------------- helpers
    def _tick(self, name: str, *args) -> None:
        self.calls.append((name, args, self.clock.now))
        self.clock.now += self.latency
        if name in self.fail:
            raise self.fail[name]

    def names(self, name: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == name]

    def default_market(self, ticker: str) -> dict:
        raise KeyError(f"unknown market {ticker}")

    def close_of(self, ticker: str) -> float:
        m = self.known.get(ticker) or self.default_market(ticker)
        return units.parse_rfc3339(m["close_time"])

    # ---------------------------------------------------------------- public reads
    def market(self, ticker):
        self._tick("market", ticker)
        m = dict(self.known.setdefault(ticker, self.default_market(ticker)))
        if ticker in self.results and self.clock.now >= self.close_of(ticker) + self.settle_lag:
            m.update(status="finalized", result=self.results[ticker])
        m.update(self.market_overrides.get(ticker, {}))
        return m

    def series(self, series_ticker):
        self._tick("series", series_ticker)
        return {"ticker": series_ticker, "exchange_index": 2, "fee_type": "quadratic", "fee_multiplier": 1}

    skew_s: object = 0.0                     # what the PC clock is off by; tests change it to drift the clock
    skew_probes = 0

    def server_time_skew(self, *a, **kw):
        """The PC clock vs Kalshi's, as the real Safety asks it at startup and again during a run. Not recorded in
        ``calls`` (it is a clock probe, not an exchange call) and it never advances the clock."""
        self.skew_probes += 1
        if isinstance(self.skew_s, BaseException):
            raise self.skew_s
        return self.skew_s

    def user_data_timestamp(self):
        self._tick("user_data_timestamp")
        return {"as_of_time": iso(self.clock.now + 5)}

    def exchange_status(self):
        self._tick("exchange_status")
        return {"exchange_active": True, "trading_active": True,
                "exchange_index_statuses": [{"exchange_index": i, "exchange_active": True, "trading_active": True}
                                            for i in (0, 1, 2, 3)]}

    # ---------------------------------------------------------------- authenticated reads
    def balance(self, exchange_index):
        self._tick("balance", exchange_index)
        return {"balance": 2943, "balance_dollars": "29.4300"}

    def orders(self, **kw):
        self._tick("orders", kw)
        rows = [o for o in self.orders_db.values() if o["ticker"] == kw.get("ticker")]
        if kw.get("status"):
            rows = [o for o in rows if o["status"] == kw["status"]]
        return [dict(o) for o in rows]

    def fills(self, **kw):
        self._tick("fills", kw)
        if self.fills_after is not None and self.clock.now < self.fills_after:
            return []                                              # REST portfolio data lags the write
        out = []
        for oid, fills in self.fills_db.items():
            for f in fills:
                if kw.get("order_id") in (None, oid) and kw.get("ticker") in (None, f["ticker"]):
                    out.append(dict(f))
        return out

    def _net(self, ticker: str) -> D:
        net = D("0")
        for fills in self.fills_db.values():
            for f in fills:
                if f["ticker"] == ticker:
                    net += D(f["count_fp"]) if f["outcome_side"] == "yes" else -D(f["count_fp"])
        return net

    def _published(self, ticker: str) -> bool:
        """The exchange has settled the ticker at close + settle_lag."""
        return ticker in self.results and self.clock.now >= self.close_of(ticker) + self.settle_lag

    def positions(self, **kw):
        self._tick("positions", kw)
        t = kw.get("ticker")
        net = self._net(t)
        if net == 0 or self._published(t):
            return []                                              # a settled position leaves the unsettled listing
        return [{"ticker": t, "exchange_index": 2, "position_fp": f"{net:.2f}"}]

    def settlements(self, **kw):
        self._tick("settlements", kw)
        t = kw.get("ticker")
        if not self._published(t) or self.settlement_lag_s is None or not any(
                f["ticker"] == t for fl in self.fills_db.values() for f in fl):
            return []
        if self.clock.now < self.close_of(t) + self.settlement_lag_s:
            return []
        res = self.results[t]
        yes = no = cost_y = cost_n = fee = D("0")
        for fl in self.fills_db.values():
            for f in fl:
                if f["ticker"] != t:
                    continue
                c = D(f["count_fp"])
                fee += D(f["fee_cost"])
                if f["outcome_side"] == "yes":
                    yes, cost_y = yes + c, cost_y + c * D(f["yes_price_dollars"])
                else:
                    no, cost_n = no + c, cost_n + c * D(f["no_price_dollars"])
        net = yes - no                      # Kalshi nets a YES+NO pair into $1 of cash when it is created, so the
        revenue = int(max(D("0"), net if res == "yes" else -net) * 100)    # settlement pays the NET position only
        return [{"ticker": t, "event_ticker": "E", "exchange_index": 2, "market_result": res,
                 "yes_count_fp": f"{yes:.2f}", "yes_total_cost_dollars": f"{cost_y:.6f}",
                 "no_count_fp": f"{no:.2f}", "no_total_cost_dollars": f"{cost_n:.6f}", "revenue": revenue,
                 "value": None, "fee_cost": f"{fee:f}", "settled_time": iso(self.clock.now)}]

    def cancel_order(self, order_id, exchange_index):
        self._tick("cancel_order", order_id, exchange_index)
        raise AssertionError("nothing rests: an IOC bot has nothing to cancel")

    def cancel_all(self):
        self._tick("cancel_all")
        raise AssertionError("cancel_all must not be needed")

    # ---------------------------------------------------------------- writes
    def _place(self, body: dict, fill: D) -> dict:
        """The exchange accepts the order and fills `fill` contracts of it (an IOC: nothing ever rests)."""
        self.n_orders += 1
        oid = f"ord-{self.n_orders}"
        price = D(body["price"])
        outcome = "yes" if body["side"] == "bid" else "no"
        outcome_price = price if outcome == "yes" else 1 - price
        self.orders_db[body["client_order_id"]] = {
            "order_id": oid, "client_order_id": body["client_order_id"], "ticker": body["ticker"],
            "status": "executed" if fill > 0 else "canceled", "fill_count_fp": f"{fill:.2f}",
            "remaining_count_fp": "0.00", "book_side": body["side"], "exchange_index": body["exchange_index"]}
        if fill > 0:
            self.fills_db[oid] = [{
                "fill_id": f"fill-{self.n_orders}", "order_id": oid, "ticker": body["ticker"],
                "exchange_index": body["exchange_index"], "outcome_side": outcome, "book_side": body["side"],
                "count_fp": f"{fill:.2f}", "yes_price_dollars": f"{price:.4f}", "no_price_dollars": f"{1 - price:.4f}",
                "is_taker": True, "fee_cost": f"{micro_fee(str(fill), str(outcome_price)):f}",
                "created_time": iso(self.clock.now)}]
        return {"order_id": oid, "client_order_id": body["client_order_id"], "fill_count": f"{fill:.2f}",
                "remaining_count": "0.00", "average_fill_price": f"{price:.4f}",
                "average_fee_paid": f"{micro_fee('1', str(outcome_price)):f}" if fill > 0 else None,
                "ts_ms": int(self.clock.now * 1000)}

    def _next_step(self, body: dict):
        queue = self.script_by_ticker.get(body["ticker"])
        if queue:
            return queue.pop(0)
        return self.script.pop(0) if self.script else "fill"

    def _check_sent(self, body: dict) -> None:
        validate_order_body(body)                                    # the real wire-format checks
        if self.ledger is not None:                                  # intent committed BEFORE the send
            row = self.ledger.order(body["client_order_id"])
            assert row is not None and row["status"] == "intent", "the intent must be committed before the send"
            assert json.loads(row["body_json"]) == body, "the stored body is exactly what is sent"

    def _result_for(self, body: dict, step) -> Result:
        coid = body["client_order_id"]
        if callable(step):
            return step(body)
        kind, _, arg = step.partition(":")
        if kind == "fill":
            return Result("ok", 201, self._place(body, D(arg or "1")), None, 1, 5.0, coid)
        if kind == "nofill":
            return Result("ok", 201, self._place(body, D("0")), None, 1, 5.0, coid)
        if kind == "timeout_placed":                    # response lost, but the order reached the exchange and filled
            self._place(body, D(arg or "1"))
            return Result("unknown", None, None, {"code": "transport_error"}, 1, 10000.0, coid)
        if kind == "timeout_lost":                      # response lost and the order never arrived
            return Result("unknown", None, None, {"code": "transport_error"}, 1, 10000.0, coid)
        if kind == "reject":
            return Result("rejected", 400, {"error": {"code": "invalid_order"}}, {"code": "invalid_order"}, 1, 5.0, coid)
        if kind == "auth":
            return Result("auth", 401, {"error": {"code": "unauthorized"}}, {"code": "unauthorized"}, 1, 5.0, coid)
        if kind == "duplicate":
            self._place(body, D("1"))
            return Result("duplicate", 409, {"error": {"code": "conflict"}}, {"code": "conflict"}, 1, 5.0, coid)
        raise AssertionError(f"unknown script step {step!r}")

    def create_order(self, body):
        self._tick("create_order", body)
        if not self.live:
            raise PermissionError("create_order: this client is not live")
        self._check_sent(body)
        self.created.append(dict(body))
        step = self._next_step(body)
        if isinstance(step, BaseException):
            raise step
        return self._result_for(body, step)

    def batch_create(self, bodies):
        self._tick("batch_create", bodies)
        if not self.live:
            raise PermissionError("batch_create: this client is not live")
        bodies = [dict(b) for b in bodies]
        for b in bodies:
            self._check_sent(b)
        self.batches.append(bodies)
        self.created.extend(dict(b) for b in bodies)
        mode = self.batch_script.pop(0) if self.batch_script else None
        if mode in ("raise_placed", "timeout_placed"):
            for b in bodies:
                self._place(b, D("1"))
        if mode in ("raise_placed", "raise_lost"):
            raise ConnectionError("connection reset by peer")
        if mode in ("timeout_placed", "timeout_lost"):
            return [Result("unknown", None, None, {"code": "transport_error"}, 1, 10000.0, b["client_order_id"])
                    for b in bodies]
        out = []
        for b in bodies:
            step = self._next_step(b)
            if isinstance(step, BaseException):
                raise step
            out.append(self._result_for(b, step))
        return out


class FakeSafety:
    """Stands in for kpm.execution.safety.Safety: ``check_entry(spec, book, market) -> (ok, reason)``.

    A trip writes the kill file, like the real one. ``verdicts`` / ``exit_verdicts`` are consumed in order ((True, "")
    when empty); an exception in the list is raised. A strategy that never sells leaves ``allow_exit`` False.
    """

    def __init__(self, kill_file: Path | None = None, *, allow_exit: bool = False):
        self.started = True
        self.halted = False
        self.kill = False
        self.kill_file = kill_file
        self.allow_exit = allow_exit
        self.trips: list[str] = []
        self.entries: list[dict] = []
        self.batches: list[list[dict]] = []        # the ``batch=`` each entry check was given (None for a single order)
        self.exits: list[dict] = []
        self.verdicts: list = []
        self.exit_verdicts: list = []
        self.checks_at: list[float] = []
        self.preflights: list[tuple[float, str]] = []
        self.preflight_problems: list = []
        self.clock_polls: list[float] = []         # when the bot asked Safety to keep the PC-clock check alive
        self.clock_problems: list = []             # consumed by poll_clock(): None = the clock is fine
        self.clock: Clock | None = None

    def preflight(self, market):
        self.preflights.append((self.clock.now if self.clock else 0.0, market["ticker"]))
        return self.preflight_problems.pop(0) if self.preflight_problems else None

    def poll_clock(self, *, force=False):
        self.clock_polls.append(self.clock.now if self.clock else 0.0)
        return self.clock_problems.pop(0) if self.clock_problems else None

    def clock_skew(self):
        return 0.1

    def check_entry(self, spec, book, market, *, batch=None):
        self.entries.append(dict(spec))
        self.batches.append(None if batch is None else [dict(s) for s in batch])
        self.checks_at.append(self.clock.now if self.clock else 0.0)
        v = self.verdicts.pop(0) if self.verdicts else (True, "")
        if isinstance(v, BaseException):
            raise v
        return v

    def check_exit(self, spec, book, market):
        if not self.allow_exit:
            raise AssertionError("this strategy holds to settlement and never sells")
        self.exits.append(dict(spec))
        v = self.exit_verdicts.pop(0) if self.exit_verdicts else (True, "")
        if isinstance(v, BaseException):
            raise v
        return v

    def kill_active(self):
        return self.kill

    def trip(self, reason):
        self.halted = True
        self.trips.append(reason)
        if self.kill_file is not None:
            self.kill_file.write_text(f"safety trip: {reason}\n")


class FakeReconcile:
    """reconcile / settle / cancel_path as the bots call them: (client, ledger, tickers) and (client, ledger, reason, tickers)."""

    def __init__(self, clock: Clock | None = None):
        self.clock = clock
        self.reconciled: list[list[str]] = []
        self.settled: list[list[str]] = []
        self.cancel_paths: list[tuple] = []
        self.raise_on: set[str] = set()
        self.ledger_lag_s = 0.0              # the exchange publishes a settlement this long after the close
        self.snapshots: list[tuple] = []     # (time, open worst case, realized loss) at every settle() call

    def reconcile(self, client, ledger, tickers):
        if "reconcile" in self.raise_on:
            raise RuntimeError("reconcile exploded")
        self.reconciled.append(list(tickers))

    def settle(self, client, ledger, tickers):
        if "settle" in self.raise_on:
            raise RuntimeError("settle exploded")
        self.settled.append(list(tickers))
        self.snapshots.append((self.clock.now, ledger.open_worst_case(), ledger.realized_loss_total()))
        rows = []
        for t in tickers:
            m = client.market(t) or {}                             # works for the fake exchange and the real ExecClient
            res = m.get("result")
            agg = ledger.ticker_agg(t)
            if res not in ("yes", "no") or not agg.n:
                continue
            if self.clock.now < units.parse_rfc3339(m["close_time"]) + self.ledger_lag_s:
                continue                                           # not published yet: stays pending
            payoff = agg.payoff(res)
            rows.append({"ticker": t, "event_ticker": "E", "exchange_index": 2, "market_result": res,
                         "yes_count_fp": f"{agg.yes:.2f}", "yes_total_cost_dollars": "0.00",
                         "no_count_fp": f"{agg.no:.2f}", "no_total_cost_dollars": "0.00",
                         "revenue": int(payoff * 100), "value": None, "fee_cost": f"{agg.fees:f}",
                         "settled_time": iso(self.clock.now)})
        if rows:
            ledger.record_settlements(rows)

    def cancel_path(self, client, ledger, reason, tickers):
        self.cancel_paths.append((reason, list(tickers)))


class RecState(BotState):
    """BotState that remembers every heartbeat time (the table keeps only the last one)."""

    def __init__(self, *a: Any, **k: Any):
        super().__init__(*a, **k)
        self.beats: list[tuple[float, str]] = []

    def heartbeat(self, status, detail=None, ts=None):
        self.beats.append((self._ts(ts), status))
        super().heartbeat(status, detail, ts=ts)


def real_stack(sim: Any, *, safety: bool = True, reconcile: bool = True) -> Any:
    """Swap the fakes for kpm.execution.safety.Safety and kpm.execution.reconcile on a Sim (it needs ``cfg``, ``ledger``,
    ``client``, ``clock``, ``kill_file``, ``ctx`` and ``safety`` / ``reconcile`` attributes)."""
    if safety:
        sf = Safety(sim.cfg["trading"], sim.ledger, sim.client, sim.clock, sleep=sim.clock.sleep,
                    kill_file=sim.kill_file)
        sf.started, sf.live = True, True          # the startup gates are Safety's own tests (clock skew, typed confirmation)
        sf.poll_clock(force=True)                 # ... including the first clock measurement that startup() would have taken
        sim.safety = sim.ctx.safety = sf
    if reconcile:
        sim.reconcile = sim.ctx.reconcile = SimpleNamespace(
            reconcile=partial(real_reconcile.reconcile, clock=sim.clock, sleep=sim.clock.sleep),
            settle=real_reconcile.settle,
            cancel_path=partial(real_reconcile.cancel_path, clock=sim.clock, sleep=sim.clock.sleep))
    return sim


def seed_trade(ledger: Any, i: int, *, ticker: str | None = None, price: str = "0.95", outcome: str = "yes",
               settle: str | None = None, base_close: int = 1_791_140_400) -> str:
    """One earlier trade in the ledger: 1 contract of `outcome` bought at `price` on its own market, filled. When
    `settle` is 'yes' or 'no' the market result is recorded too (a settled win or loss)."""
    t = ticker or f"KXBTC15M-SEED{base_close + i * 900}"
    yes_price = D(price) if outcome == "yes" else 1 - D(price)
    side = "bid" if outcome == "yes" else "ask"
    coid = f"seed-{i}-{t}"[:60]
    body = {"ticker": t, "side": side, "price": f"{yes_price:.4f}", "count": "1.00",
            "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross", "exchange_index": 2,
            "client_order_id": coid}
    spec = {"client_order_id": coid, "body": body, "outcome": outcome, "outcome_price": D(price), "purpose": "entry",
            "ticker": t}
    ledger.new_intent(spec)
    ledger.mark_result(coid, Result("ok", 201, {"order_id": f"seed-ord-{i}", "client_order_id": coid,
                                                "fill_count": "1.00", "remaining_count": "0.00"}))
    fee = micro_fee("1", price)
    ledger.upsert_fills([{"fill_id": f"seed-fill-{i}", "order_id": f"seed-ord-{i}", "ticker": t,
                          "outcome_side": outcome, "count_fp": "1.00", "yes_price_dollars": f"{yes_price:.4f}",
                          "no_price_dollars": f"{1 - yes_price:.4f}", "fee_cost": f"{fee:f}",
                          "created_time": iso(base_close - 100)}])
    if settle in ("yes", "no"):
        won = settle == outcome
        ledger.record_settlements([{"ticker": t, "market_result": settle, "revenue": 100 if won else 0,
                                    "yes_count_fp": "1.00" if outcome == "yes" else "0.00",
                                    "yes_total_cost_dollars": price if outcome == "yes" else "0.00",
                                    "no_count_fp": "1.00" if outcome == "no" else "0.00",
                                    "no_total_cost_dollars": price if outcome == "no" else "0.00",
                                    "fee_cost": f"{fee:f}", "settled_time": iso(base_close)}])
    return t


def seed_loss(ledger: Any, n: int, *, start: int = 1, price: str = "0.95", settle: bool = True) -> None:
    """n earlier trades that bought 1 YES at `price`; settled as losses when `settle` (open positions otherwise)."""
    for i in range(start, start + n):
        seed_trade(ledger, i, price=price, settle="no" if settle else None)
