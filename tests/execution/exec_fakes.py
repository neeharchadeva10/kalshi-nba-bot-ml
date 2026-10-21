"""Shared fakes for test_ledger, test_safety and test_reconcile. Offline: no network, no real sleeping.

* :class:`Clock` is a manual clock; ``clock.sleep`` advances it, so bounded waits finish instantly.
* :class:`FakeClient` is a small simulated exchange: markets, order books, orders, fills, positions, settlements, cash
  per shard, the data lag of the REST portfolio endpoints, and the cancel endpoints (with misrouting, 404s, auth
  failures and unknown outcomes). Every call is recorded in ``client.calls``. ``create_order`` and the other order
  writes raise AssertionError: the modules under test must never send an order.
* ``script`` lets a test script successive replies of one read (a lagging read, then the caught-up read).
* Builders for the exchange's wire shapes (``ex_order``, ``ex_fill``, ``ex_settlement``) and for strategy specs
  (``entry_spec``, ``exit_spec``), shaped exactly like the ones the bots hand to Safety and Ledger.new_intent().
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from kpm.core import db
from kpm.execution import units
from kpm.execution.client import Result
from kpm.execution.ledger import Ledger

D = Decimal
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "kalshi_exec"

CLOSE_ISO = "2026-10-05T18:30:00Z"              # a Monday
CLOSE_TS = 1_791_225_000.0
NOW = CLOSE_TS - 90.0                           # 90 s before the close: inside late_favorite's decision window
TICKER = "KXBTC15M-26OCT051430-30"
HOURLY_TICKER = "KXBTCD-26OCT0515-T85249.99"
SHARD = 2                                       # KXBTC15M and KXBTCD live on exchange_index 2


def fx(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Clock:
    """Manual clock: ``clock()`` reads it, ``clock.sleep(s)`` advances it and records ``s``."""

    def __init__(self, now: float = NOW):
        self.now = float(now)
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    time = __call__

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))
        self.now += float(seconds)


# ============================================================================================ markets and books
def base_market(**over: Any) -> dict:
    """The real KXBTC15M market capture (tapered_deci_cent grid, shard 2, closes 18:30:00Z)."""
    m = copy.deepcopy(fx("market_KXBTC15M-26OCT051430-30.json")["market"])
    m.update(over)
    return m


def hourly_market(**over: Any) -> dict:
    """A KXBTCD hourly 'above' market closing at the same time (linear_cent grid, shard 2)."""
    m = base_market(ticker=HOURLY_TICKER, event_ticker="KXBTCD-26OCT0515", strike_type="greater",
                    price_level_structure="linear_cent",
                    price_ranges=[{"end": "1.0000", "start": "0.0000", "step": "0.0100"}], floor_strike=85249.99)
    m.update(over)
    return m


def make_book(ticker: str = TICKER, *, yes: Any = (("0.56", "10"), ("0.57", "20"), ("0.58", "30")),
              no: Any = (("0.39", "5"), ("0.40", "15"), ("0.41", "25")), fetched_at: float | None = None) -> dict:
    """A parsed order book as ExecClient.orderbook returns it: ascending bids, best LAST, Decimal pairs.

    Defaults: best YES bid 0.58 and best NO bid 0.41, so the YES ask is 0.59 (size 25) and the NO ask is 0.42 (size 30).
    """
    return {"ticker": ticker, "yes": [(D(p), D(s)) for p, s in yes], "no": [(D(p), D(s)) for p, s in no],
            "fetched_at": NOW - 0.3 if fetched_at is None else fetched_at}


# ============================================================================================ specs
def entry_spec(market: dict | None = None, outcome: str = "yes", price: str | Decimal = "0.59",
               count: str | Decimal = "1", *, purpose: str = "entry", reduce_only: bool = False, **over: Any) -> dict:
    """An IOC buy of ``outcome`` at ``price`` (the outcome's own price), shaped like late_favorite.entry_spec."""
    market = market or base_market()
    price, count = D(str(price)), D(str(count))
    side, yes_price = units.to_book(outcome, price)
    spec = {"strategy": "late_favorite", "purpose": purpose, "role": None, "opportunity_id": 1,
            "ticker": market["ticker"], "exchange_index": market["exchange_index"], "action": "buy", "outcome": outcome,
            "outcome_price": price, "side": side, "yes_price": yes_price, "count": count,
            "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross",
            "reduce_only": reduce_only}
    spec.update(over)
    return spec


def exit_spec(market: dict | None = None, outcome: str = "no", price: str | Decimal = "0.42",
              count: str | Decimal = "1", **over: Any) -> dict:
    """A reduce_only IOC buy of the opposite outcome (an unwind), shaped like pair_h2.leg_spec(purpose='unwind')."""
    return entry_spec(market, outcome, price, count, purpose="unwind", reduce_only=True, **over)


# ============================================================================================ ledger helpers
def make_ledger(tmp_path: Path, clock: Clock, *, run_id: str = "run-1", env: str = "prod", name: str = "ledger.sqlite",
                **kw: Any) -> Ledger:
    conn = db.connect(tmp_path / name)
    db.init_schema(conn)
    return Ledger(conn, run_id, env, clock=clock, log_path=tmp_path / "exec.jsonl", **kw)


def ok_result(coid: str, order_id: str, *, fill: str = "1.00", remaining: str = "0.00", avg_price: str | None = None,
              avg_fee: str | None = None, status: int = 201) -> Result:
    data: dict[str, Any] = {"order_id": order_id, "client_order_id": coid, "fill_count": fill,
                            "remaining_count": remaining, "ts_ms": 1791224910123}
    if avg_price is not None:
        data["average_fill_price"] = avg_price
    if avg_fee is not None:
        data["average_fee_paid"] = avg_fee
    return Result("ok", status, data, None, 1, 12.0, coid)


def err_result(outcome: str, status: int | None = None, code: str = "x", coid: str | None = None) -> Result:
    err = {"code": code, "message": f"{outcome} for the test", "details": None, "shape": "error_object"}
    return Result(outcome, status, None, err, 1, 5.0, coid)


def place(ledger: Ledger, spec: dict, result: Result | None = None, *, order_id: str | None = None,
          fill: str = "1.00", remaining: str = "0.00") -> str:
    """new_intent + mark_result in the order the bots do it. Returns the client_order_id."""
    coid = ledger.new_intent(spec)
    if result is None:
        result = ok_result(coid, order_id or f"ord-{coid[:8]}", fill=fill, remaining=remaining)
    ledger.mark_result(coid, result)
    return coid


def seed_fill(ledger: Ledger, *, ticker: str = TICKER, outcome: str = "yes", price: str = "0.59",
              order_id: str = "ord-1", fill_id: str = "fill-1", fee: str | None = None, count: str = "1") -> str:
    """An executed IOC order plus its fetched fill, as left behind by an earlier trade. Returns the client_order_id."""
    coid = place(ledger, entry_spec(base_market(ticker=ticker), outcome, price, count), order_id=order_id,
                 fill=f"{D(count):.2f}")
    fee = fee if fee is not None else str(units.taker_fee(D(count), D(price)))
    ledger.upsert_fills([ex_fill(fill_id, order_id, ticker, outcome=outcome, price=price, count=f"{D(count):.2f}",
                                 fee=fee)])
    return coid


def seed_realized_loss(ledger: Ledger, amount: str, ticker: str = "KXBTC15M-OLD-1") -> None:
    """Record a settled loss of exactly ``amount`` dollars (a settlement row with no fills, revenue 0, no fee)."""
    ledger.record_settlements([ex_settlement(ticker, result="no", yes_cost=amount, revenue=0, fee="0.0000")])


# ============================================================================================ exchange wire shapes
def ex_order(coid: str, order_id: str, ticker: str = TICKER, *, outcome: str = "yes", price: str = "0.59",
             count: str = "1.00", fill: str = "1.00", remaining: str = "0.00", status: str = "executed",
             ei: int = SHARD, fee: str = "0.016933", **over: Any) -> dict:
    """A GET /portfolio/orders Order object (outcome price in, YES/NO dollars out)."""
    side, yes_price = units.to_book(outcome, D(price))
    o = {"order_id": order_id, "client_order_id": coid, "ticker": ticker, "outcome_side": outcome, "book_side": side,
         "type": "limit", "status": status, "yes_price_dollars": f"{yes_price:.4f}",
         "no_price_dollars": f"{D(1) - yes_price:.4f}", "fill_count_fp": fill, "remaining_count_fp": remaining,
         "initial_count_fp": count, "taker_fees_dollars": fee if D(fill) > 0 else "0.000000",
         "maker_fees_dollars": "0.000000", "expiration_time": None, "created_time": iso(NOW),
         "last_update_time": iso(NOW), "self_trade_prevention_type": "taker_at_cross", "order_group_id": None,
         "cancel_order_on_pause": False, "subaccount_number": 0, "exchange_index": ei}
    o.update(over)
    return o


def ex_fill(fill_id: str, order_id: str, ticker: str = TICKER, *, outcome: str = "yes", price: str = "0.59",
            count: str = "1.00", fee: str = "0.016933", ei: int = SHARD, is_taker: bool = True, **over: Any) -> dict:
    """A GET /portfolio/fills row. ``price`` is the price of the outcome bought."""
    side, yes_price = units.to_book(outcome, D(price))
    f = {"fill_id": fill_id, "trade_id": fill_id, "order_id": order_id, "ticker": ticker, "market_ticker": ticker,
         "exchange_index": ei, "outcome_side": outcome, "book_side": side, "count_fp": count,
         "yes_price_dollars": f"{yes_price:.4f}", "no_price_dollars": f"{D(1) - yes_price:.4f}",
         "is_taker": is_taker, "fee_cost": fee, "created_time": iso(NOW + 1.0), "subaccount_number": 0,
         "ts": int(NOW)}
    f.update(over)
    return f


def ex_settlement(ticker: str = TICKER, *, result: str = "yes", yes_count: str = "1.00", yes_cost: str = "0.5900",
                  no_count: str = "0.00", no_cost: str = "0.0000", revenue: int = 100, fee: str = "0.016933",
                  settled_at: float = CLOSE_TS + 30.0) -> dict:
    """A GET /portfolio/settlements row: ``revenue`` and ``value`` are integer CENTS."""
    return {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0], "exchange_index": SHARD,
            "market_result": result, "yes_count_fp": yes_count, "yes_total_cost_dollars": yes_cost,
            "no_count_fp": no_count, "no_total_cost_dollars": no_cost, "revenue": revenue, "value": 100,
            "fee_cost": fee, "settled_time": iso(settled_at)}


# ============================================================================================ the fake exchange client
class ScriptedFailure(Exception):
    """Default exception FakeClient raises when a read is scripted to fail."""


class FakeClient:
    """A simulated ExecClient: the reads and cancels that Safety and reconcile use. See the module docstring."""

    def __init__(self, clock: Clock, *, live: bool = True, env_name: str = "prod", has_keys: bool = True):
        self.clock = clock
        self.live = live
        self.has_keys = has_keys
        self.env = SimpleNamespace(name=env_name, rest_host="external-api.kalshi.com", key_fp="deadbeef",
                                   has_keys=has_keys)
        self.status: dict = fx("exchange_status_prod.json")
        self.markets: dict[str, dict] = {TICKER: base_market()}
        self.series_of: dict[str, str] = {TICKER: "KXBTC15M", HOURLY_TICKER: "KXBTCD"}
        self.balances: dict[int | None, str] = {None: "29.4300", 0: "0.0000", SHARD: "29.4300"}
        self.exchange_orders: list[dict] = []
        self.exchange_fills: list[dict] = []
        self.exchange_positions: dict[str, str] = {}
        self.settlement_rows: list[dict] = []
        self.skew: Any = 0.5
        self.data_lag_s = 0.0
        self.latency_s = 0.0                       # seconds the fake clock advances on every call
        self.calls: list[tuple[str, dict]] = []
        self.script: dict[str, list] = {}
        self.fail: dict[str, BaseException] = {}
        self.cancel_mode = "ok"                    # ok | 404 | gone | other | auth | permission
        self.cancel_removes = True                 # a successful cancel really removes the resting order
        self.cancel_all_removes = True
        self.cancel_all_mode = "ok"                # ok | auth
        self.cancelled: list[tuple[str, int]] = []
        self.cancel_all_calls = 0

    # ---- plumbing
    def _record(self, name: str, **kw: Any) -> None:
        self.calls.append((name, kw))
        if self.latency_s:
            self.clock.advance(self.latency_s)                  # every call takes this long (slow network)

    def calls_to(self, name: str) -> list[dict]:
        return [kw for n, kw in self.calls if n == name]

    def _scripted(self, name: str, default: Any) -> Any:
        """Next scripted reply for ``name`` (the last one repeats); an exception reply is raised."""
        if name in self.fail:
            raise self.fail[name]
        queue = self.script.get(name)
        if not queue:
            return default
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, BaseException):
            raise reply
        return copy.deepcopy(reply)

    # ---- public reads
    def exchange_status(self) -> dict:
        self._record("exchange_status")
        return self._scripted("exchange_status", copy.deepcopy(self.status))

    def open_markets(self, series_ticker: str) -> list[dict]:
        """GET /markets?series_ticker=&status=open. Like ExecClient.open_markets it takes NO close-time filter: Kalshi's
        OpenAPI allows min_close_ts / max_close_ts only with status closed or no status, so a caller that passes one gets a
        TypeError here (and would get a documented-incompatible request on the wire)."""
        self._record("open_markets", series=series_ticker)
        if "open_markets" in self.fail:
            raise self.fail["open_markets"]
        return [copy.deepcopy(m) for t, m in self.markets.items() if self.series_of.get(t) == series_ticker]

    def series(self, series_ticker: str) -> dict:
        """GET /series/{t}: the series object; both allowed series trade on shard 2."""
        self._record("series", series=series_ticker)
        if "series" in self.fail:
            raise self.fail["series"]
        return self._scripted("series", {"ticker": series_ticker, "exchange_index": SHARD, "fee_type": "quadratic",
                                         "fee_multiplier": 1})

    def market(self, ticker: str) -> dict:
        self._record("market", ticker=ticker)
        return copy.deepcopy(self.markets[ticker])

    def user_data_timestamp(self) -> dict:
        self._record("user_data_timestamp")
        return self._scripted("user_data_timestamp", {"as_of_time": iso(self.clock.now - self.data_lag_s)})

    def server_time_skew(self, *a: Any, **kw: Any) -> Any:
        self._record("server_time_skew")
        if "server_time_skew" in self.fail:
            raise self.fail["server_time_skew"]
        return self.skew

    # ---- authenticated reads
    def balance(self, exchange_index: int | None) -> dict:
        self._record("balance", exchange_index=exchange_index)
        if "balance" in self.fail:
            raise self.fail["balance"]
        dollars = self.balances[exchange_index]
        cents = int((D(dollars) * 100).to_integral_value(rounding="ROUND_DOWN"))
        return self._scripted("balance", {"balance": cents, "balance_dollars": dollars, "portfolio_value": cents,
                                          "balance_breakdown": [{"exchange_index": 0, "balance": self.balances[0]},
                                                                {"exchange_index": SHARD,
                                                                 "balance": self.balances[SHARD]}]})

    def positions(self, **kw: Any) -> list[dict]:
        self._record("positions", **kw)
        base = [{"ticker": t, "exchange_index": SHARD, "position_fp": v}
                for t, v in self.exchange_positions.items() if kw.get("ticker") in (None, t)]
        return self._scripted("positions", base)

    def orders(self, **kw: Any) -> list[dict]:
        self._record("orders", **kw)
        base = [copy.deepcopy(o) for o in self.exchange_orders
                if kw.get("ticker") in (None, o["ticker"]) and kw.get("status") in (None, o["status"])]
        return self._scripted("orders", base)

    def fills(self, **kw: Any) -> list[dict]:
        self._record("fills", **kw)
        base = [copy.deepcopy(f) for f in self.exchange_fills
                if kw.get("ticker") in (None, f["ticker"]) and kw.get("order_id") in (None, f["order_id"])]
        return self._scripted("fills", base)

    def settlements(self, **kw: Any) -> list[dict]:
        self._record("settlements", **kw)
        base = [copy.deepcopy(s) for s in self.settlement_rows if kw.get("ticker") in (None, s["ticker"])]
        return self._scripted("settlements", base)

    # ---- the only writes the modules under test may send: cancels
    def cancel_order(self, order_id: str, exchange_index: int) -> Result:
        self._record("cancel_order", order_id=order_id, exchange_index=exchange_index)
        if not self.live:
            raise PermissionError("cancel_order: this ExecClient is not live (dry-run); writes are disabled")
        mode = self.cancel_mode
        if mode == "permission":
            raise PermissionError("cancel_order: writes are disabled")
        order = next((o for o in self.exchange_orders if o["order_id"] == order_id), None)
        if mode == "auth":
            return err_result("auth", 401, "authentication_error")
        if mode == "gone" and order is not None:                       # it filled or expired a moment ago
            order["status"], order["remaining_count_fp"] = "canceled", "0.00"
            return err_result("rejected", 404, "not_found")
        if mode == "404" or order is None or order["exchange_index"] != exchange_index:
            return err_result("rejected", 404, "not_found")            # a misrouted cancel looks like this
        if mode == "other":
            return err_result("unknown", 200, "order_id_mismatch")     # the response described another order
        self.cancelled.append((order_id, exchange_index))
        if self.cancel_removes:
            order["status"] = "canceled"
            order["remaining_count_fp"] = "0.00"
        return Result("ok", 200, {"order_id": order_id, "client_order_id": order["client_order_id"],
                                  "reduced_by": "1.00", "ts_ms": 1791224911000}, None, 1, 8.0)

    def cancel_all(self) -> Result:
        self._record("cancel_all")
        self.cancel_all_calls += 1
        if not self.live:
            raise PermissionError("cancel_all: this ExecClient is not live (dry-run); writes are disabled")
        if self.cancel_all_mode == "auth":
            return err_result("auth", 401, "authentication_error")
        if self.cancel_all_removes:
            for o in self.exchange_orders:
                if o["status"] == "resting":
                    o["status"] = "canceled"
        return Result("ok", 204, None, None, 1, 8.0)

    # ---- order writes: the code under test must never send an order
    def _forbidden(self, name: str) -> Callable[..., Any]:
        def fn(*a: Any, **kw: Any) -> Any:
            raise AssertionError(f"{name} was called: reconcile, safety and the cancel path never create or amend orders")
        return fn

    def __getattr__(self, name: str) -> Any:
        if name in ("create_order", "batch_create", "amend_order", "decrease_order", "batch_cancel"):
            return self._forbidden(name)
        raise AttributeError(name)
