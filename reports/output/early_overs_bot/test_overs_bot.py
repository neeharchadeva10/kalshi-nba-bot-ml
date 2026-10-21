"""Unit tests for overs_bot.py. Fake client, fake pick source, no network, no keys."""
import json
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import overs_bot as ob  # noqa: E402
from kpm.execution.client import Result  # noqa: E402

ET = ob.ET
DAY = date(2026, 10, 25)
TIP = datetime(2026, 10, 25, 19, 0, tzinfo=ET)          # pick-list tip time
IN_WIN = TIP - timedelta(minutes=60)
G1 = "26OCT25AAABBB"


def mk_row(game=G1, side="OVER", bid=45, ask=47, strike=220.5, proj=225.0, ticker=None):
    t = ticker or f"KXNBATOTAL-{game}-{int(strike)}"
    buy, limit = ("YES", bid) if side == "OVER" else ("NO", 100 - ask)
    return {"game": game, "side": side, "buy": buy, "ticker": t, "strike": strike, "proj": proj,
            "yes_bid": bid / 100, "yes_ask": ask / 100, "limit": limit / 100, "status": "x"}


class AuthErr(Exception):
    status = 401


class FakeClient:
    def __init__(self):
        self.created, self.cancelled, self.book = [], [], {}
        self.next_id = 0
        self.create_result = None              # Result to return instead of the default
        self.store_on_create = True            # False: the order never reaches the book (lost)
        self.create_hook = None                # callable(body, oid) run after storing, may raise
        self.order_raises = None
        self.ei = 2
        self.quotes = {}                       # ticker -> (yes_bid_c, yes_ask_c)
        self.pos = []
        self.skew = 0.0
        self.calls = []
        self.event_ticker = True
        self.kw_seen = []

    # -- reads
    def server_time_skew(self):
        return self.skew

    def market(self, ticker):
        m = {"status": "active"}
        if self.event_ticker:
            m["event_ticker"] = ticker.rsplit("-", 1)[0]
        if self.ei is not None:
            m["exchange_index"] = self.ei
        return m

    def orderbook(self, ticker):
        b, a = self.quotes[ticker]
        return {"yes": [(Decimal(b) / 100, Decimal(5))], "no": [(Decimal(100 - a) / 100, Decimal(5))]}

    def positions(self, **kw):
        self.kw_seen.append(("positions", dict(kw)))
        ev = kw.get("event_ticker")
        return [x for x in self.pos if ev is None or str(x.get("ticker", ev + "-")).startswith(ev + "-")]

    def orders(self, **kw):
        self.kw_seen.append(("orders", dict(kw)))
        ev = kw.get("event_ticker")
        out = [o for o in self.book.values() if o.get("ticker") == kw.get("ticker", o.get("ticker"))
               and (ev is None or str(o.get("ticker")).startswith(ev + "-"))]
        if kw.get("status"):
            out = [o for o in out if o["status"] == kw["status"]]
        return out

    def order(self, oid):
        if self.order_raises:
            raise self.order_raises
        return dict(self.book[oid])

    # -- writes
    def create_order(self, body):
        self.created.append(body)
        self.next_id += 1
        oid = f"oid{self.next_id}"
        if self.store_on_create:
            self.book[oid] = {"order_id": oid, "ticker": body["ticker"], "status": "resting",
                              "fill_count_fp": "0.00", "remaining_count_fp": "1.00",
                              "client_order_id": body["client_order_id"], "taker_fees_dollars": "0",
                              "maker_fees_dollars": "0", "taker_fill_cost_dollars": "0",
                              "maker_fill_cost_dollars": "0"}
        if self.create_hook:
            self.create_hook(body, oid)
        if self.create_result:
            return self.create_result
        return Result("ok", 201, {"order_id": oid, "fill_count": "0.00", "remaining_count": "1.00"})

    def cancel_order(self, oid, ei):
        self.cancelled.append((oid, ei))
        if self.book[oid]["status"] == "resting":
            self.book[oid]["status"] = "canceled"
        return Result("ok", 200, {"order_id": oid, "reduced_by": "1.00"})

    def set_fill(self, oid, filled, remaining, fees="0.0044", cost="0.45"):
        o = self.book[oid]
        o.update(fill_count_fp=filled, remaining_count_fp=remaining, taker_fill_cost_dollars="0",
                 maker_fill_cost_dollars=cost, maker_fees_dollars=fees,
                 status="executed" if remaining == "0.00" else "resting")


def one_game(key=G1, tip=TIP, tk=None):
    return (tip, key, [{"ticker": tk or f"KXNBATOTAL-{key}-220", "exchange_index": 2}])


def make_bot(tmp_path, rows=None, games=None, live=True, client=None, compute=None, **kw):
    games = games if games is not None else [one_game()]
    rows = rows if rows is not None else [mk_row()]
    seen = {"fetched": None}

    def default_compute(day, now, on_header):
        g = list(games)
        if on_header:
            on_header(0, g)                   # real compute_rows passes its own list; the bot may filter it in place
        seen["fetched"] = [k for _, k, _ in g]
        return rows, 0, g, None

    client = client if client is not None else (FakeClient() if live else None)
    if client is not None and hasattr(client, "quotes"):
        for r in rows:
            client.quotes[r["ticker"]] = (round(r["yes_bid"] * 100), round(r["yes_ask"] * 100))
    lines = []
    bot = ob.Bot(client=client, live=live, compute=compute or default_compute, log_path=tmp_path / "log.jsonl",
                 stop_path=tmp_path / "STOP", lock_path=tmp_path / "bot.lock", echo=lines.append,
                 sleep=lambda s: None, **kw)
    bot.lines, bot.seen = lines, seen
    return bot, client


def T(bot, t, day=DAY):
    """One tick at simulated time t (the bot's own clock follows, so send-time re-checks see the same time)."""
    bot.clock = lambda: t
    bot.tick(day, t)


def logs(tmp_path):
    p = tmp_path / "log.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def rec(bot, game=G1, day=DAY):
    return bot.orders[(day.isoformat(), game)]


# ------------------------------------------------------------------ side / price selection
def test_over_buys_yes_at_yes_bid(tmp_path):
    bot, c = make_bot(tmp_path, rows=[mk_row(side="OVER", bid=45, ask=47)])
    T(bot, IN_WIN)
    b = c.created[0]
    assert b["side"] == "bid" and b["price"] == "0.4500" and b["count"] == "1.00"


def test_under_buys_no_at_no_bid(tmp_path):
    bot, c = make_bot(tmp_path, rows=[mk_row(side="UNDER", bid=45, ask=47)])
    T(bot, IN_WIN)
    b = c.created[0]                                   # NO bid = 100-47 = 53c -> YES-price ask at 0.47
    assert b["side"] == "ask" and b["price"] == "0.4700"
    assert rec(bot)["price_c"] == 53


def test_body_fields(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)
    b = c.created[0]
    assert b["post_only"] is True and b["time_in_force"] == "good_till_canceled"
    assert b["cancel_order_on_pause"] is True
    assert b["exchange_index"] == 2 and b["expiration_time"] == int((TIP - timedelta(minutes=15)).timestamp())
    assert b["client_order_id"] == f"eob-2026-10-25-{G1}"


def test_side_buy_mismatch_skipped(tmp_path):
    r = mk_row(side="OVER")
    r["buy"] = "NO"
    bot, c = make_bot(tmp_path, rows=[r])
    T(bot, IN_WIN)
    assert c.created == []
    r = mk_row(side="UNDER")
    r["buy"] = "MAYBE"
    bot, c = make_bot(tmp_path, rows=[r])
    T(bot, IN_WIN)
    assert c.created == []


# ------------------------------------------------------------------ caps
def order(game, price_c=50, state="open", filled=0):
    return {"game": game, "price_c": price_c, "state": state, "filled": filled, "count": 1}


NOW = IN_WIN


def caps(orders, game="G9", ticker="KXNBATOTAL-G9-1", action="buy", count=1, price=50, now=NOW):
    return ob.check_caps(orders, game, ticker, action, count, price, now)


def test_cap_ok():
    assert caps([]) == []


def test_cap_series():
    assert any("series" in v for v in caps([], ticker="KXNBAGAME-G9-1"))


def test_cap_buys_only():
    assert any("buy" in v for v in caps([], action="sell"))


def test_cap_count():
    assert any("count" in v for v in caps([], count=2))


@pytest.mark.parametrize("p", [14, 86, 0, 100])
def test_cap_price_range(p):
    assert any("price" in v for v in caps([], price=p))


@pytest.mark.parametrize("p", [15, 85])
def test_cap_price_edges_ok(p):
    assert not any("outside" in v for v in caps([], price=p))


def test_cap_one_per_game():
    assert any("game" in v for v in caps([order("G9")]))


def test_cap_14_per_night():
    assert any("orders tonight" in v for v in caps([order(f"G{i}", 15) for i in range(14)]))
    assert not any("orders tonight" in v for v in caps([order(f"G{i}", 15) for i in range(13)]))


def test_cap_seven_dollars_binds_counting_resting_filled_and_fee():
    unit = Decimal("0.85") + ob.est_fee(85)
    assert Decimal("0.85") < unit < Decimal("0.87")
    # 8 x unit ~ 6.87 <= 7.00 (so 8 fit) but 9 x unit ~ 7.73 > 7.00 (the 9th must be blocked)
    eight = [order(f"G{i}", 85, "open" if i % 2 else "filled") for i in range(8)]
    seven = eight[:7]
    assert sum(ob.order_cost(o) for o in eight) == 8 * unit and 8 * unit <= 7 < 9 * unit
    assert caps(seven, price=85) == []                                      # the 8th fits
    assert any("nightly cap" in v for v in caps(eight, price=85))           # the 9th is blocked
    # the fee decides it: without fees 8 x 0.85 + 0.15 + fee(15) is below 7.00 only by the fee margin
    edge = eight + [order("Z", 15)]
    total = sum(ob.order_cost(o) for o in edge)
    exact_room = Decimal("7.00") - total
    new_cost = Decimal("0.15") + ob.est_fee(15)
    assert (caps(edge, game="Y", price=15) == []) == (new_cost <= exact_room)
    nine = [order(f"G{i}", 85, "filled") for i in range(8)] + [order("H", 85, "open")]
    assert any("nightly cap" in v for v in caps(nine, price=15))            # 9 x unit alone is already over 7
    # a cancelled, unfilled order frees its money; a partly filled one keeps the filled part
    freed = [order(f"G{i}", 85, "canceled", 0) for i in range(8)]
    assert not any("nightly cap" in v for v in caps(freed, price=85))
    assert ob.order_cost(order("A", 50, "canceled", Decimal("1"))) > Decimal("0.5")
    assert ob.order_cost(order("A", 50, "not_found")) > Decimal("0.5")      # unresolved: full cost stays


def test_cap_after_end_date():
    assert caps([], now=ob.HARD_END + timedelta(minutes=1))
    assert caps([], now=ob.HARD_END) == []


def test_no_orders_after_end_date_even_with_window_open(tmp_path):
    tip = ob.HARD_END.astimezone(ET) + timedelta(hours=1, minutes=30)    # 2026-11-04 01:29 ET
    now = tip - timedelta(minutes=60)                                # 00:29 ET: window open, after the end date
    assert now > ob.HARD_END
    bot, c = make_bot(tmp_path, games=[one_game(tip=tip)])
    T(bot, now, day=tip.date())
    assert c.created == []
    assert any("no orders after" in ln for ln in bot.lines)


def test_cap_violation_logged_and_skipped(tmp_path):
    bot, c = make_bot(tmp_path, rows=[mk_row(ticker=f"KXNBAGAME-{G1}-220")])
    T(bot, IN_WIN)
    assert c.created == []
    assert any(r["event"] == "caps" and r["ok"] is False for r in logs(tmp_path))


# ------------------------------------------------------------------ windows, post-only / no-cross
def test_outside_window_no_order(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, TIP - timedelta(minutes=76))
    T(bot, TIP - timedelta(minutes=44))
    assert c.created == []
    T(bot, TIP - timedelta(minutes=75))
    assert len(c.created) == 1


def test_missing_quote_skipped(tmp_path):
    r = mk_row()
    r["side"] = None
    bot, c = make_bot(tmp_path, rows=[r])
    T(bot, IN_WIN)
    assert c.created == []


def test_no_cross_guard_skips_zero_spread(tmp_path):
    bot, c = make_bot(tmp_path, rows=[mk_row(bid=47, ask=47)])
    T(bot, IN_WIN)
    assert c.created == []
    bot, c = make_bot(tmp_path, rows=[mk_row(bid=46, ask=47)])
    T(bot, IN_WIN)
    assert len(c.created) == 1


def test_orderbook_fetch_limited_to_games_in_window(tmp_path):
    g2 = one_game("26OCT25CCCDDD", TIP + timedelta(hours=3))
    bot, c = make_bot(tmp_path, games=[one_game(), g2], rows=[mk_row(), mk_row("26OCT25CCCDDD")])
    T(bot, IN_WIN)
    assert bot.seen["fetched"] == [G1]                  # only the in-window game reaches compute_rows' book fetches
    assert len(c.created) == 1


# ------------------------------------------------------------------ stale checks right before sending
def test_stale_quote_skipped_no_chasing(tmp_path):
    bot, c = make_bot(tmp_path)
    c.quotes[f"KXNBATOTAL-{G1}-220"] = (46, 48)          # best YES bid moved from 45 to 46 after the pick
    c.quotes[f"KXNBATOTAL-{G1}-220"] = (46, 48)
    r = mk_row()
    c.quotes[r["ticker"]] = (46, 48)
    T(bot, IN_WIN)
    assert c.created == []
    assert any("stale quote" in ln for ln in bot.lines)


def test_window_rechecked_at_send_time(tmp_path):
    bot, c = make_bot(tmp_path)
    bot.clock = lambda: TIP - timedelta(minutes=44)      # the window closed while the bot was preparing
    bot.tick(DAY, IN_WIN)
    assert c.created == []
    assert any("window closed" in ln for ln in bot.lines)


def test_end_date_rechecked_at_send_time(tmp_path):
    bot, c = make_bot(tmp_path)
    bot.clock = lambda: ob.HARD_END + timedelta(minutes=5)
    bot.tick(DAY, IN_WIN)
    assert c.created == []


# ------------------------------------------------------------------ double exposure
def test_existing_position_skips(tmp_path):
    bot, c = make_bot(tmp_path)
    c.pos = [{"ticker": f"KXNBATOTAL-{G1}-220", "position_fp": "-1.00"}]
    T(bot, IN_WIN)
    assert c.created == []
    assert any("already exposed" in ln for ln in bot.lines)


def test_existing_resting_order_from_any_source_skips(tmp_path):
    bot, c = make_bot(tmp_path)
    c.book["manual1"] = {"order_id": "manual1", "ticker": f"KXNBATOTAL-{G1}-220", "status": "resting",
                         "client_order_id": "manual"}
    T(bot, IN_WIN)
    assert c.created == []


def test_missing_exchange_index_live_skips_never_zero(tmp_path):
    bot, c = make_bot(tmp_path)
    c.ei = None
    T(bot, IN_WIN)
    assert c.created == []
    assert any("exchange_index" in ln for ln in bot.lines)


# ------------------------------------------------------------------ one order per game / restart
def test_one_order_per_game(tmp_path):
    bot, c = make_bot(tmp_path)
    for k in range(3):
        T(bot, IN_WIN + timedelta(minutes=k))
    assert len(c.created) == 1


def test_restart_reconciles_and_cancels_orphan(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)
    assert len(c.created) == 1
    bot2, _ = make_bot(tmp_path, client=c)              # a new process: same log, same exchange state
    bot2.reconcile_from_log()
    T(bot2, IN_WIN + timedelta(minutes=3))
    assert len(c.created) == 1                          # not re-placed
    assert rec(bot2)["order_id"] == "oid1"
    T(bot2, TIP - timedelta(minutes=15))
    assert c.cancelled == [("oid1", 2)]                 # the second process cancels the first one's order


def test_restart_after_crash_between_attempt_and_reply(tmp_path):
    bot, c = make_bot(tmp_path)

    def boom(body, oid):
        raise RuntimeError("boom")
    c.create_hook = boom
    T(bot, IN_WIN)
    assert len(c.created) == 1 and rec(bot)["state"] == "unknown"
    c.create_hook = None
    bot2, _ = make_bot(tmp_path, client=c)
    bot2.reconcile_from_log()
    T(bot2, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 1                          # the attempt record alone blocks a re-place
    T(bot2, TIP - timedelta(minutes=15))                # and the order is found by client_order_id and cancelled
    assert c.cancelled == [("oid1", 2)]


def test_dry_run_records_do_not_block_live(tmp_path):
    bot, _ = make_bot(tmp_path, live=False)
    T(bot, IN_WIN)
    bot2, c2 = make_bot(tmp_path)
    bot2.reconcile_from_log()
    T(bot2, IN_WIN)
    assert len(c2.created) == 1


# ------------------------------------------------------------------ create outcomes
def test_409_duplicate_treated_as_unknown_cost_counted_and_cancelled(tmp_path):
    bot, c = make_bot(tmp_path)
    c.create_result = Result("duplicate", 409, None, {"code": "already_exists"})
    T(bot, IN_WIN)
    o = rec(bot)
    assert o["order_id"] == "oid1" and o["state"] == "open"        # resolved by client_order_id
    assert ob.order_cost(o) > Decimal("0.45")
    T(bot, TIP - timedelta(minutes=15))
    assert c.cancelled == [("oid1", 2)]


def test_409_unresolvable_stays_unknown_and_counts(tmp_path):
    bot, c = make_bot(tmp_path)
    c.store_on_create = False
    c.create_result = Result("duplicate", 409, None, {"code": "already_exists"})
    T(bot, IN_WIN)
    o = rec(bot)
    assert o["state"] == "unknown" and ob.order_cost(o) > Decimal("0.45")
    assert len(ob.check_caps([o], "X", "KXNBATOTAL-X-1", "buy", 1, 50, IN_WIN)) == 0   # still room, but counted


def test_unknown_outcome_resolved_and_cancelled(tmp_path):
    bot, c = make_bot(tmp_path)
    c.create_result = Result("unknown", None, None, {"code": "transport_error"})
    T(bot, IN_WIN)
    assert rec(bot)["state"] == "open" and rec(bot)["order_id"] == "oid1"
    T(bot, TIP - timedelta(minutes=15))
    assert c.cancelled == [("oid1", 2)]


def test_throttled_treated_as_failed(tmp_path):
    bot, c = make_bot(tmp_path)
    c.store_on_create = False
    c.create_result = Result("throttled", 429, None, {"code": "rate"})
    T(bot, IN_WIN)
    assert rec(bot)["state"] == "failed" and ob.order_cost(rec(bot)) == 0
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 1                          # no retry of the same game


def test_rejected_treated_as_failed(tmp_path):
    bot, c = make_bot(tmp_path)
    c.store_on_create = False
    c.create_result = Result("rejected", 400, None, {"code": "invalid_order"})
    T(bot, IN_WIN)
    assert rec(bot)["state"] == "failed"


# ------------------------------------------------------------------ M2 stall, M4 fallback
def test_lost_order_without_id_stops_blocking_after_misses(tmp_path):
    bot, c = make_bot(tmp_path)
    c.store_on_create = False
    c.create_result = Result("unknown", None, None, {"code": "transport_error"})
    T(bot, IN_WIN)
    o = rec(bot)
    assert o["state"] == "unknown" and o["order_id"] is None
    assert not bot.day_done(DAY, TIP + timedelta(hours=1))          # blocked while unresolved and tries remain
    t = TIP - timedelta(minutes=15)
    T(bot, t)
    assert o["cancel_tries"] >= 1                                    # no-id path counts tries
    for k in range(4):                                               # after cancel_at + 10 min, clean listings w/o it
        T(bot, t + timedelta(minutes=11 + k))
    assert o["state"] == "not_found"
    assert ob.order_cost(o) > Decimal("0.45")                        # full cost stays counted
    assert bot.day_done(DAY, TIP + timedelta(hours=1))               # and it no longer blocks the day


def test_cancel_tries_exhaust_on_no_id_path(tmp_path):
    bot, c = make_bot(tmp_path)
    c.store_on_create = False
    c.create_result = Result("unknown", None, None, {"code": "transport_error"})
    T(bot, IN_WIN)
    for k in range(12):                                              # before cancel_at + 10 min: never not_found
        T(bot, TIP - timedelta(minutes=15, seconds=-k))
    assert rec(bot)["cancel_tries"] == ob.MAX_CANCEL_TRIES and rec(bot)["state"] == "unknown"
    assert bot.day_done(DAY, TIP + timedelta(hours=1))


def test_order_get_raises_falls_back_to_listing(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)
    c.order_raises = RuntimeError("single-order GET broken on this shard")
    c.set_fill("oid1", "1.00", "0.00")
    T(bot, IN_WIN + timedelta(minutes=1))
    assert rec(bot)["state"] == "filled"
    assert any(r["event"] == "fill" for r in logs(tmp_path))


def test_order_get_raises_cancel_still_works_via_listing(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)
    c.order_raises = RuntimeError("broken")
    T(bot, TIP - timedelta(minutes=15))
    assert c.cancelled == [("oid1", 2)] and rec(bot)["state"] == "canceled"


def test_id_never_found_becomes_not_found(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)
    del c.book["oid1"]                                               # the exchange never lists it again
    c.order_raises = KeyError("oid1")
    t = TIP - timedelta(minutes=15)
    for k in range(5):
        T(bot, t + timedelta(minutes=11 + k))
    assert rec(bot)["state"] == "not_found"


def test_apply_unusable_view_keeps_state(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)
    o = rec(bot)
    bot._apply(o, {"state": "unknown", "filled": Decimal(0), "remaining": Decimal(0), "fees": Decimal(0),
                   "avg_cost": None, "api_status": None}, "t")
    assert o["state"] == "open"


# ------------------------------------------------------------------ cancel at tip-15
def test_cancel_unfilled_at_tip_minus_15(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)
    T(bot, TIP - timedelta(minutes=16))
    assert c.cancelled == []
    T(bot, TIP - timedelta(minutes=15))
    assert c.cancelled == [("oid1", 2)]
    assert rec(bot)["state"] == "canceled"
    T(bot, TIP - timedelta(minutes=14))
    assert len(c.cancelled) == 1 and len(c.created) == 1       # never re-placed


def test_cancel_partly_filled(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)
    c.set_fill("oid1", "0.40", "0.60")
    T(bot, TIP - timedelta(minutes=15))
    assert c.cancelled == [("oid1", 2)]
    o = rec(bot)
    assert o["state"] == "canceled" and o["filled"] == Decimal("0.40")


def test_no_cancel_for_filled(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)
    c.set_fill("oid1", "1.00", "0.00")
    T(bot, IN_WIN + timedelta(minutes=5))
    T(bot, TIP - timedelta(minutes=15))
    assert c.cancelled == []
    assert "fill" in [r["event"] for r in logs(tmp_path)]


# ------------------------------------------------------------------ STOP / Ctrl+C / halt / finally
def two_games():
    return [one_game(G1), one_game("26OCT25CCCDDD")]


def test_stop_file_cancels_all_and_exits(tmp_path):
    bot, c = make_bot(tmp_path, rows=[mk_row(G1), mk_row("26OCT25CCCDDD")], games=two_games())
    T(bot, IN_WIN)
    assert len(c.created) == 2
    (tmp_path / "STOP").write_text("x")
    bot.run(DAY)
    assert len(c.cancelled) == 2 and bot.stopped and len(c.created) == 2


def test_ctrl_c_cancels_all(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)

    def boom(day, now):
        raise KeyboardInterrupt
    bot.tick = boom
    bot.run(DAY)
    assert c.cancelled == [("oid1", 2)] and bot.stopped


def test_ctrl_c_during_create_cancels_the_order(tmp_path):
    bot, c = make_bot(tmp_path)

    def interrupt(body, oid):
        raise KeyboardInterrupt
    c.create_hook = interrupt                            # order is on the exchange when Ctrl+C lands
    bot.clock = lambda: IN_WIN
    bot.run(DAY)
    assert len(c.created) == 1 and c.cancelled == [("oid1", 2)]


def test_finally_cancels_on_unexpected_exit(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)

    def die(first_day, once):
        raise SystemExit("unexpected")
    bot._run_days = die
    with pytest.raises(SystemExit):
        bot.run(DAY)
    assert c.cancelled == [("oid1", 2)]


def test_first_auth_error_halts_after_cancelling(tmp_path):
    bot, c = make_bot(tmp_path, rows=[mk_row(G1), mk_row("26OCT25CCCDDD")], games=two_games())
    T(bot, IN_WIN)
    assert len(c.created) == 2
    c.order_raises = AuthErr("401")
    bot.clock = lambda: IN_WIN + timedelta(minutes=1)
    bot.run(DAY)
    assert bot.halt_reason and bot.stopped and len(c.cancelled) == 2


def test_auth_outcome_on_create_halts(tmp_path):
    bot, c = make_bot(tmp_path)
    c.store_on_create = False
    c.create_result = Result("auth", 401, None, {"code": "x"})
    bot.clock = lambda: IN_WIN
    bot.run(DAY)
    assert bot.halt_reason and rec(bot)["state"] == "failed" and len(c.created) == 1


# ------------------------------------------------------------------ dry run / live gating / lock
def test_dry_run_sends_nothing(tmp_path):
    class Tripwire:
        def __getattr__(self, n):
            raise AssertionError(f"dry run touched client.{n}")
    bot, _ = make_bot(tmp_path, live=False)
    bot.client = Tripwire()
    T(bot, IN_WIN)
    assert any("DRY RUN, NOT SENT" in ln for ln in bot.lines)
    T(bot, TIP - timedelta(minutes=15))               # dry cancel path touches nothing either


def test_force_first_dry_only(tmp_path):
    bot, _ = make_bot(tmp_path, live=False, force_first=True)
    T(bot, TIP - timedelta(hours=5))
    assert any("DRY RUN, NOT SENT" in ln for ln in bot.lines)
    with pytest.raises(ValueError):
        ob.Bot(client=FakeClient(), live=True, force_first=True)


def _tripwires(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("live machinery touched")
    monkeypatch.setattr("kpm.execution.env.load_env", boom)
    monkeypatch.setattr("kpm.execution.client.ExecClient", boom)
    monkeypatch.setattr(ob, "Bot", boom)


def test_live_refuses_without_prod_env():
    for val in (None, "demo", "PROD", ""):
        with pytest.raises(SystemExit):
            ob.require_prod_env(lambda name, v=val: v)
    ob.require_prod_env(lambda name: "prod")


def test_main_live_refuses_without_prod(monkeypatch):
    _tripwires(monkeypatch)
    monkeypatch.setattr("kpm.core.config.secret", lambda n: None)
    with pytest.raises(SystemExit):
        ob.main(["--live"])


def test_main_live_refuses_demo(monkeypatch):
    _tripwires(monkeypatch)
    monkeypatch.setattr("kpm.core.config.secret", lambda n: "demo")
    with pytest.raises(SystemExit):
        ob.main(["--live"])


def test_main_live_once_refused_before_anything(monkeypatch):
    _tripwires(monkeypatch)
    monkeypatch.setattr("kpm.core.config.secret", lambda n: "prod")
    with pytest.raises(SystemExit):
        ob.main(["--live", "--once"])
    with pytest.raises(SystemExit):
        ob.main(["--live", "--force-first"])


def test_bot_run_live_once_refused(tmp_path):
    bot, c = make_bot(tmp_path)
    with pytest.raises(ValueError):
        bot.run(DAY, once=True)
    assert c.created == []


def test_two_instances_second_refused(tmp_path):
    bot1, c1 = make_bot(tmp_path)
    bot2, c2 = make_bot(tmp_path)
    held = ob.acquire_lock(bot1.lock_path)               # what bot1.run() holds for its whole life
    try:
        with pytest.raises(SystemExit):
            bot2.run(DAY)
        assert c2.created == []
        with pytest.raises(SystemExit):
            ob.acquire_lock(bot1.lock_path)
    finally:
        ob.release_lock(held)
    again = ob.acquire_lock(bot1.lock_path)              # released: free again
    ob.release_lock(again)


def test_lock_held_during_run_and_released_after(tmp_path):
    bot, c = make_bot(tmp_path)
    (tmp_path / "STOP").write_text("x")
    seen = {}
    orig = bot._run_days

    def spy(first_day, once):
        try:
            ob.acquire_lock(bot.lock_path)
            seen["free"] = True
        except SystemExit:
            seen["free"] = False
        return orig(first_day, once)
    bot._run_days = spy
    bot.clock = lambda: IN_WIN
    bot.run(DAY)
    assert seen["free"] is False
    ob.release_lock(ob.acquire_lock(bot.lock_path))      # free after run()


def test_clock_skew_gate(tmp_path):
    bot, c = make_bot(tmp_path)
    c.skew = 3.5
    bot.clock = lambda: IN_WIN
    with pytest.raises(SystemExit):
        bot.run(DAY)
    assert c.created == []
    bot, c = make_bot(tmp_path)
    c.skew = -1.9
    (tmp_path / "STOP").write_text("x")
    bot.clock = lambda: IN_WIN
    bot.run(DAY)                                         # within 2 s: starts (then stops on STOP)


def test_console_lines_ascii(tmp_path):
    bot, _ = make_bot(tmp_path)
    T(bot, IN_WIN)
    assert bot.lines and all(ln.isascii() for ln in bot.lines)


# ------------------------------------------------------------------ multi-day run, DST, schedule reload
def test_day_start_is_11_et_and_dst_safe():
    assert ob.day_start(date(2026, 10, 31)) == datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)   # EDT
    assert ob.day_start(date(2026, 11, 1)) == datetime(2026, 11, 1, 16, 0, tzinfo=timezone.utc)      # EST after the change
    assert (ob.day_start(date(2026, 11, 2)) - ob.day_start(date(2026, 11, 1))) == timedelta(hours=24)
    assert (ob.day_start(date(2026, 11, 1)) - ob.day_start(date(2026, 10, 31))) == timedelta(hours=25)


def test_multi_day_run_places_and_cancels_each_day(tmp_path):
    d1, d2 = date(2026, 10, 25), date(2026, 10, 26)
    tips = {d1: datetime(2026, 10, 25, 19, 0, tzinfo=ET), d2: datetime(2026, 10, 26, 19, 30, tzinfo=ET)}
    sim = {"t": ob.day_start(d1)}

    def compute(day, now, on_header):
        key = f"{day:%y%b%d}".upper() + "AAABBB"
        games = [(tips[day], key, [{"ticker": f"KXNBATOTAL-{key}-220", "exchange_index": 2}])]
        if on_header:
            on_header(0, games)
        return [mk_row(key)], 0, games, None

    c = FakeClient()
    bot, _ = make_bot(tmp_path, client=c, compute=compute, until=d2)
    for key in ("26OCT25AAABBB", "26OCT26AAABBB"):
        c.quotes[f"KXNBATOTAL-{key}-220"] = (45, 47)
    bot.clock = lambda: sim["t"]
    bot._sleep = lambda s: sim.__setitem__("t", sim["t"] + timedelta(seconds=s))
    bot.run(d1)
    days = {o["day"] for o in bot.orders.values()}
    assert days == {"2026-10-25", "2026-10-26"}
    assert len(c.created) == 2 and len(c.cancelled) == 2           # each night: one placed, one cancelled unfilled
    ev = [r["event"] for r in logs(tmp_path)]
    assert ev.count("day_done") == 2 and ev[-1] == "exit"
    assert sim["t"] >= ob.day_start(d2)


def test_schedule_reloaded_every_30_minutes(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, TIP - timedelta(hours=6))
    first = bot.sched[DAY.isoformat()]["loaded_at"]
    T(bot, TIP - timedelta(hours=6) + timedelta(minutes=29))
    assert bot.sched[DAY.isoformat()]["loaded_at"] == first
    T(bot, TIP - timedelta(hours=6) + timedelta(minutes=31))
    assert bot.sched[DAY.isoformat()]["loaded_at"] > first


# ------------------------------------------------------------------ parsing
def test_parse_order_obj():
    p = ob.parse_order_obj({"status": "canceled", "fill_count_fp": "0.00", "remaining_count_fp": "0.00"})
    assert p["state"] == "canceled"
    p = ob.parse_order_obj({"status": "executed", "fill_count_fp": "1.00", "remaining_count_fp": "0.00",
                            "maker_fill_cost_dollars": "0.45", "maker_fees_dollars": "0.0044"})
    assert p["state"] == "filled" and p["avg_cost"] == Decimal("0.45") and p["fees"] == Decimal("0.0044")


def test_decision_uses_current_time_each_placement(tmp_path):
    seen = []
    bot, c = make_bot(tmp_path)
    inner = bot._compute

    def spy(day, now, hdr):
        seen.append(now)
        return inner(day, now, hdr)
    bot._compute = spy
    T(bot, IN_WIN)
    assert seen[-1] == IN_WIN


# ------------------------------------------------------------------ event-level exposure, explicit shard
def test_exposure_check_is_event_level_with_explicit_shard(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)
    pk = [kw for n, kw in c.kw_seen if n == "positions"][0]
    ok = [kw for n, kw in c.kw_seen if n == "orders" and kw.get("status") == "resting"][0]
    assert pk == {"event_ticker": f"KXNBATOTAL-{G1}", "exchange_index": 2}
    assert ok == {"event_ticker": f"KXNBATOTAL-{G1}", "status": "resting", "exchange_index": 2}


def test_manual_order_on_other_strike_of_same_event_blocks(tmp_path):
    bot, c = make_bot(tmp_path)
    c.book["manual1"] = {"order_id": "manual1", "ticker": f"KXNBATOTAL-{G1}-230", "status": "resting",
                         "client_order_id": "manual"}
    T(bot, IN_WIN)
    assert c.created == []
    assert any("already exposed" in ln for ln in bot.lines)


def test_position_on_other_strike_of_same_event_blocks(tmp_path):
    bot, c = make_bot(tmp_path)
    c.pos = [{"ticker": f"KXNBATOTAL-{G1}-230", "position_fp": "2.00"}]
    T(bot, IN_WIN)
    assert c.created == []


def test_zero_position_and_other_event_do_not_block(tmp_path):
    bot, c = make_bot(tmp_path)
    c.pos = [{"ticker": f"KXNBATOTAL-{G1}-230", "position_fp": "0.00"},
             {"ticker": "KXNBATOTAL-26OCT25ZZZYYY-200", "position_fp": "5.00"}]
    T(bot, IN_WIN)
    assert len(c.created) == 1


@pytest.mark.parametrize("bad", [{"ticker": f"KXNBATOTAL-{G1}-230"},
                                 {"ticker": f"KXNBATOTAL-{G1}-230", "position_fp": "abc"},
                                 {"ticker": f"KXNBATOTAL-{G1}-230", "position_fp": None},
                                 {"position_fp": "0.00"}])
def test_malformed_position_counts_as_exposed(tmp_path, bad):
    bot, c = make_bot(tmp_path)
    c.pos = [bad]
    T(bot, IN_WIN)
    assert c.created == []


def test_missing_event_ticker_skips(tmp_path):
    bot, c = make_bot(tmp_path)
    c.event_ticker = False
    T(bot, IN_WIN)
    assert c.created == []
    assert any("event_ticker" in ln for ln in bot.lines)


def test_exposure_read_error_fails_closed(tmp_path):
    bot, c = make_bot(tmp_path)

    def bad(**kw):
        raise RuntimeError("down")
    c.positions = bad
    T(bot, IN_WIN)
    assert c.created == []


def test_listing_fallback_passes_exchange_index(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)
    c.order_raises = RuntimeError("x")
    c.kw_seen.clear()
    T(bot, IN_WIN + timedelta(minutes=1))
    assert ("orders", {"ticker": f"KXNBATOTAL-{G1}-220", "exchange_index": 2}) in c.kw_seen


# ------------------------------------------------------------------ failing schedule reload; per-day clock check
def test_reload_failure_still_cancels_on_time_and_keeps_schedule(tmp_path):
    calls = {"n": 0}
    base = make_bot(tmp_path)[0]._compute

    def flaky(day, now, hdr):
        calls["n"] += 1
        if now >= TIP - timedelta(minutes=20) and hdr is not None and hdr.__name__ == "hdr":
            raise RuntimeError("markets endpoint down")
        return base(day, now, hdr)
    bot, c = make_bot(tmp_path, compute=flaky)
    T(bot, IN_WIN)
    assert len(c.created) == 1
    T(bot, TIP - timedelta(minutes=15))                  # the reload (>30 min old) raises; the cancel must still happen
    assert c.cancelled == [("oid1", 2)]
    assert bot.sched[DAY.isoformat()]["games"]            # old schedule kept
    assert any("schedule reload" in ln for ln in bot.lines)


def test_clock_failure_at_day_start_skips_placing_but_cancels(tmp_path):
    d1, d2 = date(2026, 10, 25), date(2026, 10, 26)
    tips = {d1: datetime(2026, 10, 25, 19, 0, tzinfo=ET), d2: datetime(2026, 10, 26, 19, 30, tzinfo=ET)}
    sim = {"t": ob.day_start(d1)}

    def compute(day, now, on_header):
        key = f"{day:%y%b%d}".upper() + "AAABBB"
        games = [(tips[day], key, [{"ticker": f"KXNBATOTAL-{key}-220", "exchange_index": 2}])]
        if on_header:
            on_header(0, games)
        return [mk_row(key)], 0, games, None

    c = FakeClient()
    bot, _ = make_bot(tmp_path, client=c, compute=compute, until=d2)
    for key in ("26OCT25AAABBB", "26OCT26AAABBB"):
        c.quotes[f"KXNBATOTAL-{key}-220"] = (45, 47)
    skews = iter([0.0, 5.0])                              # start ok, day-2 start fails
    c.server_time_skew = lambda: next(skews)
    bot.clock = lambda: sim["t"]
    bot._sleep = lambda s: sim.__setitem__("t", sim["t"] + timedelta(seconds=s))
    bot.run(d1)
    assert len(c.created) == 1 and len(c.cancelled) == 1   # day 1 placed and cancelled; day 2 no placing
    assert {o["day"] for o in bot.orders.values()} == {"2026-10-25"}
