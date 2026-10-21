"""A scripted Kalshi behind :class:`FakeTransport` (not a test module).

botfakes.FakeExchange fakes the ExecClient object; this fakes the HTTP wire underneath it, so the REAL ExecClient (its
allowlist, parameter whitelist, signing, response parsing) runs together with the real Env, Ledger, Safety, reconcile,
BotState and strategies. A wrong parameter name, a wrong return shape or a drifted interface between those modules fails
here and nowhere else.

The exchange keeps one consistent state: markets and their books, orders, fills, positions and settlements agree with
each other, and every create_order the bots send is recorded in ``created`` (the exact JSON body). Anything not routed
(a DELETE, an unknown endpoint) makes the transport raise UnmatchedRequest, so a bot that cancels or cancels-all when it
should not fails loudly.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Callable

import requests

from kpm.execution import units

from .conftest import FakeClock, FakeTransport, respond
from .exec_fakes import CLOSE_TS, SHARD, TICKER, base_market, ex_fill, ex_order, ex_settlement, iso

CASH = "29.4300"


def zulu(ts: float) -> str:
    """An RFC3339 time as the markets endpoint writes it: 2026-10-05T18:30:00Z."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def levels(best: str, size: str = "800.00", worse_by: str = "0.0200") -> list[list[str]]:
    """Ascending REST levels as strings, best bid LAST (a worse level first proves the reader takes the last one)."""
    worse = D(best) - D(worse_by)
    if worse <= 0:                                           # near $0 there is no room for a level 2 cents lower
        worse = D(best) / 2
    return [[f"{worse:.4f}", "5.00"], [f"{D(best):.4f}", size]]


class SimHttp:
    """One or more markets, their order books, and a consistent account, served through a FakeTransport."""

    def __init__(self, t: FakeTransport, clock: FakeClock, *, close_ts: float = CLOSE_TS, ticker: str = TICKER,
                 book: tuple[str, str] | None = ("0.72", "0.27"), result: str = "yes", settle_lag: float = 30.0,
                 cash: str = CASH):
        self.t, self.clock = t, clock
        self.settle_lag = settle_lag
        self.cash = cash
        self.markets: dict[str, dict] = {}
        self.series: dict[str, str] = {}
        self.books: dict[str, tuple[str, str] | None] = {}
        self.results: dict[str, str] = {}
        self.book_fn: Callable[[str, float], tuple[str, str] | None] | None = None
        self.script: list[Any] = []                  # per create_order: fill | nofill | reject | timeout_placed | timeout_lost
        self.created: list[dict] = []                # the exact JSON bodies of every create_order received
        self.orders: dict[str, dict] = {}            # client_order_id -> Order object
        self.fills: list[dict] = []
        self.cancels: list[tuple[str, dict]] = []    # (order_id, query params) of every DELETE /portfolio/events/orders/{id}
        self.cancel_all_calls = 0
        self.lag_s = 0.0                             # REST portfolio data lags a write by this long (user_data_timestamp)
        self._visible: dict[str, float] = {}         # client_order_id / fill_id -> when the REST portfolio data shows it
        # a FRESH response object per call: FakeTransport stamps the Date header once per object, and a reused object would
        # keep an old time (a real server sends the current time every time)
        t.get("/exchange/status", lambda call: respond(200, fixture="exchange_status_prod.json"))
        t.get("/exchange/user_data_timestamp", lambda call: {"as_of_time": iso(self.clock.now - self.lag_s)})
        t.get("/series/KXBTC15M", lambda call: respond(200, fixture="series_KXBTC15M.json"))
        t.get("/series/KXBTCD", lambda call: {"series": {"ticker": "KXBTCD", "exchange_index": SHARD,
                                                         "fee_type": "quadratic", "fee_multiplier": 1}})
        t.get("/markets", self.on_markets)
        t.get("/account/limits", fixture="synthetic/account_limits.json")
        t.get("/portfolio/balance", self.on_balance)
        t.get("/portfolio/positions", self.on_positions)
        t.get("/portfolio/orders", self.on_orders)
        t.get("/portfolio/fills", self.on_fills)
        t.get("/portfolio/settlements", self.on_settlements)
        t.post("/portfolio/events/orders", self.on_create)
        t.post("/portfolio/events/orders/batched", self.on_batch)
        self.batches: list[list[dict]] = []          # the bodies of every batch_create received
        self.add_market(base_market(ticker=ticker, close_time=zulu(close_ts), open_time=zulu(close_ts - 900),
                                    event_ticker=ticker.rsplit("-", 1)[0], status="active", result=""),
                        "KXBTC15M", book=book, result=result)

    # ------------------------------------------------------------------ markets and books
    def add_market(self, market: dict, series: str, *, book: tuple[str, str] | None, result: str = "yes") -> None:
        tk = market["ticker"]
        self.markets[tk], self.series[tk], self.books[tk], self.results[tk] = market, series, book, result
        self.t.get(f"/markets/{tk}", lambda call, tk=tk: {"market": self.market_object(tk)})
        self.t.get(f"/markets/{tk}/orderbook", lambda call, tk=tk: self.on_book(tk))

    def close_of(self, ticker: str) -> float:
        return units.parse_rfc3339(self.markets[ticker]["close_time"])

    def market_object(self, ticker: str) -> dict:
        m = copy.deepcopy(self.markets[ticker])
        if self.clock.now >= self.close_of(ticker) + 5.0:
            m.update(status="finalized", result=self.results[ticker])
        return m

    def on_markets(self, call) -> dict:
        """Only markets that are open now are listed (opened at open_time, not yet closed), as Kalshi lists them.

        Kalshi's OpenAPI (GetMarkets) allows min_close_ts / max_close_ts only with status closed or no status. A request
        that sends them with status=open is a request the server is free to reject (or to answer with nothing), so this
        stand-in fails the test instead of quietly honouring the filters."""
        params = call.params or {}
        wanted = params.get("series_ticker")
        assert not ({"min_close_ts", "max_close_ts"} & set(params) and params.get("status") == "open"), (
            f"GetMarkets: close-time filters are not documented as compatible with status=open: {params}")
        rows = []
        for tk in self.markets:
            opened = units.parse_rfc3339(self.markets[tk]["open_time"]) if self.markets[tk].get("open_time") else float("-inf")
            if self.series[tk] == wanted and opened <= self.clock.now < self.close_of(tk):
                rows.append(self.market_object(tk))
        return {"markets": rows, "cursor": ""}

    def add_following_markets(self, n: int, *, books: list[tuple[str, str] | None] | None = None,
                              results: list[str] | None = None) -> list[str]:
        """``n`` more KXBTC15M markets, each closing 900 s after the previous one (the first market's tickers are
        ``<first>`` and the new ones ``<prefix>-w1`` ... ). Returns their tickers."""
        first = next(iter(self.markets))
        close0 = self.close_of(first)
        tickers = []
        for k in range(1, n + 1):
            tk = f"{first.rsplit('-', 1)[0]}-w{k}"
            m = base_market(ticker=tk, close_time=zulu(close0 + 900 * k), open_time=zulu(close0 + 900 * (k - 1)),
                            event_ticker=tk, status="active", result="")
            self.add_market(m, "KXBTC15M", book=(books[k - 1] if books else ("0.50", "0.49")),
                            result=(results[k - 1] if results else "yes"))
            tickers.append(tk)
        return tickers

    def on_book(self, ticker: str) -> dict:
        pair = self.book_fn(ticker, self.clock.now) if self.book_fn else self.books[ticker]
        yes, no = ([], []) if pair is None else (levels(pair[0]), levels(pair[1], "900.00", "0.0200"))
        return {"orderbook_fp": {"yes_dollars": yes, "no_dollars": no}}

    def published(self, ticker: str) -> bool:
        return self.clock.now >= self.close_of(ticker) + self.settle_lag

    # ------------------------------------------------------------------ the account
    def on_balance(self, call) -> dict:
        return {"balance": int(D(self.cash) * 100), "balance_dollars": self.cash, "portfolio_value": int(D(self.cash) * 100),
                "balance_breakdown": [{"exchange_index": 0, "balance": "0.0000"}, {"exchange_index": SHARD,
                                                                                    "balance": self.cash}]}

    def seen(self, key: str) -> bool:
        """Has the REST portfolio data caught up with this order or fill yet? (lag_s after it happened)"""
        return self._visible.get(key, float("-inf")) <= self.clock.now

    def net(self, ticker: str) -> D:
        total = D(0)
        for f in self.fills:
            if f["ticker"] == ticker and self.seen(f["fill_id"]):
                total += D(f["count_fp"]) if f["outcome_side"] == "yes" else -D(f["count_fp"])
        return total

    def on_positions(self, call) -> dict:
        tk = (call.params or {}).get("ticker")
        net = self.net(tk) if tk else D(0)
        rows = [] if net == 0 or self.published(tk) else [{"ticker": tk, "exchange_index": SHARD, "position_fp": f"{net:.2f}"}]
        return {"market_positions": rows, "event_positions": [], "cursor": ""}

    def on_orders(self, call) -> dict:
        p = call.params or {}
        rows = [copy.deepcopy(o) for o in self.orders.values() if self.seen(o["client_order_id"])
                and p.get("ticker") in (None, o["ticker"]) and p.get("status") in (None, o["status"])]
        return {"orders": rows, "cursor": ""}

    def on_fills(self, call) -> dict:
        p = call.params or {}
        rows = [copy.deepcopy(f) for f in self.fills if self.seen(f["fill_id"])
                and p.get("ticker") in (None, f["ticker"]) and p.get("order_id") in (None, f["order_id"])]
        return {"fills": rows, "cursor": ""}

    def on_settlements(self, call) -> dict:
        tk = (call.params or {}).get("ticker")
        mine = [f for f in self.fills if f["ticker"] == tk and self.seen(f["fill_id"])]
        if tk not in self.markets or not mine or not self.published(tk):
            return {"settlements": [], "cursor": ""}
        res = self.results[tk]
        yes = sum((D(f["count_fp"]) for f in mine if f["outcome_side"] == "yes"), D(0))
        no = sum((D(f["count_fp"]) for f in mine if f["outcome_side"] == "no"), D(0))
        cost_y = sum((D(f["count_fp"]) * D(f["yes_price_dollars"]) for f in mine if f["outcome_side"] == "yes"), D(0))
        cost_n = sum((D(f["count_fp"]) * D(f["no_price_dollars"]) for f in mine if f["outcome_side"] == "no"), D(0))
        fee = sum((D(f["fee_cost"]) for f in mine), D(0))
        net = yes - no                                  # the exchange nets a YES+NO pair: the settlement pays the net
        revenue = int(max(D(0), net if res == "yes" else -net) * 100)
        return {"settlements": [ex_settlement(tk, result=res, yes_count=f"{yes:.2f}", yes_cost=f"{cost_y:.4f}",
                                              no_count=f"{no:.2f}", no_cost=f"{cost_n:.4f}", revenue=revenue,
                                              fee=f"{fee:f}", settled_at=self.close_of(tk) + self.settle_lag)],
                "cursor": ""}

    # ------------------------------------------------------------------ resting orders and the cancel endpoints
    def add_resting(self, coid: str, *, order_id: str = "sim-rest-1", ticker: str = TICKER, outcome: str = "yes",
                    price: str = "0.40", count: str = "1.00") -> dict:
        """An order that rests on the exchange (a GTC bid, say), with its DELETE route."""
        order = ex_order(coid, order_id, ticker, outcome=outcome, price=price, count=count, fill="0.00",
                         remaining=count, status="resting")
        self.orders[coid] = order
        self.t.delete(f"/portfolio/events/orders/{order_id}", lambda call: self.on_cancel(coid, call))
        return order

    def on_cancel(self, coid: str, call):
        self.cancels.append((self.orders[coid]["order_id"], dict(call.params or {})))
        o = self.orders[coid]
        o.update(status="canceled", remaining_count_fp="0.00")
        return {"order_id": o["order_id"], "client_order_id": coid, "reduced_by": o["initial_count_fp"],
                "ts_ms": int(self.clock.now * 1000)}

    def enable_cancel_all(self) -> None:
        self.t.delete("/portfolio/events/orders", lambda call: self.on_cancel_all())

    def on_cancel_all(self):
        self.cancel_all_calls += 1
        for o in self.orders.values():
            if o["status"] == "resting":
                o.update(status="canceled", remaining_count_fp="0.00")
        return 204

    # ------------------------------------------------------------------ the only write the bots send
    def place(self, body: dict, fill: D) -> dict:
        """The exchange accepts the order and fills ``fill`` contracts of it (an IOC: nothing ever rests)."""
        n = len(self.orders) + 1
        oid = f"sim-order-{n}"
        count, price = D(body["count"]), D(body["price"])
        outcome = "yes" if body["side"] == "bid" else "no"
        outcome_price = price if outcome == "yes" else 1 - price
        fee = units.taker_fee(fill, outcome_price) if fill > 0 else D(0)
        order = ex_order(body["client_order_id"], oid, body["ticker"], outcome=outcome, price=f"{outcome_price:.4f}",
                         count=f"{count:.2f}", fill=f"{fill:.2f}", remaining="0.00",
                         status="executed" if fill > 0 else "canceled", fee=f"{fee:f}")
        self.orders[body["client_order_id"]] = order
        self._visible[body["client_order_id"]] = self.clock.now + self.lag_s
        if fill > 0:
            self.fills.append(ex_fill(f"sim-fill-{n}", oid, body["ticker"], outcome=outcome, price=f"{outcome_price:.4f}",
                                      count=f"{fill:.2f}", fee=f"{fee:f}"))
            self._visible[f"sim-fill-{n}"] = self.clock.now + self.lag_s
        return {"order_id": oid, "client_order_id": body["client_order_id"], "fill_count": f"{fill:.2f}",
                "remaining_count": "0.00", "average_fill_price": f"{price:.4f}" if fill > 0 else None,
                "average_fee_paid": f"{units.taker_fee(D(1), outcome_price):f}" if fill > 0 else None,
                "ts_ms": int(self.clock.now * 1000)}

    def on_create(self, call):
        body = call.body
        self.created.append(copy.deepcopy(body))
        step = self.script.pop(0) if self.script else "fill"
        count = D(body["count"])
        if step == "fill":
            return respond(201, json=self.place(body, count))
        if step == "nofill":
            return respond(201, json=self.place(body, D(0)))
        if step == "reject":
            return respond(400, json={"error": {"code": "invalid_order", "message": "scripted rejection"}})
        if step == "timeout_placed":                    # the response is lost but the order reached the exchange
            self.place(body, count)
            return requests.Timeout("scripted timeout")
        if step == "timeout_lost":                      # the response is lost and the order never arrived
            return requests.Timeout("scripted timeout")
        raise AssertionError(f"unknown script step {step!r}")

    def on_batch(self, call):
        """POST /portfolio/events/orders/batched: one script step per order (fill | nofill | reject), or a step for the
        whole batch (timeout_placed | timeout_lost)."""
        bodies = call.body["orders"]
        self.batches.append(copy.deepcopy(bodies))
        self.created.extend(copy.deepcopy(bodies))
        whole = self.script[0] if self.script and self.script[0] in ("timeout_placed", "timeout_lost") else None
        if whole:
            self.script.pop(0)
            if whole == "timeout_placed":
                for b in bodies:
                    self.place(b, D(b["count"]))
            return requests.Timeout("scripted timeout")
        items = []
        for b in bodies:
            step = self.script.pop(0) if self.script else "fill"
            if step == "reject":
                items.append({"order_id": None, "client_order_id": b["client_order_id"], "fill_count": None,
                              "remaining_count": None, "average_fill_price": None, "average_fee_paid": None,
                              "ts_ms": None, "error": {"code": "invalid_order", "message": "scripted rejection",
                                                       "details": ""}})
                continue
            item = self.place(b, D(b["count"]) if step == "fill" else D(0))
            item["error"] = None
            items.append(item)
        return respond(201, json={"orders": items})
