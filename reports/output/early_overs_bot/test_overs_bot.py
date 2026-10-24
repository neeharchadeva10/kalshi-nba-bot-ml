"""Unit tests for overs_bot.py (IOC instant-fill version). Fake client, fake pick source, no network, no keys."""
import json
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import overs_bot as ob  # noqa: E402
from kpm.execution.client import Result, validate_order_body  # noqa: E402

ET = ob.ET
DAY = date(2026, 10, 25)
TIP = datetime(2026, 10, 25, 19, 0, tzinfo=ET)          # pick-list tip time
IN_WIN = TIP - timedelta(minutes=60)
G1 = "26OCT25AAABBB"
G2 = "26OCT25CCCDDD"
TK1 = f"KXNBATOTAL-{G1}-220"                              # strike 219.5 -> ticker 220


def tk(game, strike="219.5"):
    return f"KXNBATOTAL-{game}-{int(Decimal(strike).to_integral_value(rounding='ROUND_CEILING'))}"


def mk_row(game=G1, side="OVER", bid=45, ask=47, strike="219.5", proj=225.0, ticker=None, bet=True):
    """A compute_rows-shaped row. Taker price = the ask of the side chosen."""
    buy, limit, taker = ("YES", bid, ask) if side == "OVER" else ("NO", 100 - ask, 100 - bid)
    return {"game": game, "side": side, "buy": buy, "ticker": ticker or tk(game, strike), "strike": float(strike),
            "proj": proj, "yes_bid": bid / 100, "yes_ask": ask / 100, "limit": limit / 100,
            "taker_price": taker / 100, "bet": bet, "status": "x"}


def ping(game=G1, day=DAY, side="OVER", strike="219.5", limit_c=47):
    return f"{day.isoformat()}|{game}", {"side": side, "strike": strike, "limit_c": limit_c, "sent": "x"}


class AuthErr(Exception):
    status = 401


class FakeClient:
    """Kalshi stand-in. IOC create_order fills iff the book allows it (fill_mode 'auto'), or by force."""

    def __init__(self):
        self.created, self.cancelled, self.book = [], [], {}
        self.next_id = 0
        self.create_result = None              # Result to return instead of the default
        self.store_on_create = True            # False: the order never reaches the book (lost)
        self.create_hook = None                # callable(body, oid) run after storing, may raise
        self.order_raises = None
        self.fill_mode = "auto"                # "auto" | "never" | "always"
        self.rest_mode = False                 # True: stored order is left RESTING (to test the cancel backstop)
        self.ei = 3                            # KXNBATOTAL lives on shard 3
        self.quotes = {}                       # ticker -> (yes_bid_c, yes_ask_c)
        self.strike_override = None
        self.pos = []
        self.skew = 0.0
        self.event_ticker = True
        self.kw_seen = []
        self.orderbook_calls = 0
        self.own_pos = {}                      # ticker -> signed contracts from fills made through this client
        self.partial = None                    # e.g. "0.40": IOC fills only that much, rest cancelled
        self.size = Decimal(5)                 # contracts at the best level of both sides
        self.cash = Decimal("100")
        self.cash_raises = None
        self.orders_raises = None
        self.ignore_event_filter = False       # the API ignoring event_ticker (listing returns everything)

    # -- reads
    def server_time_skew(self):
        return self.skew

    def market(self, ticker):
        m = {"status": "active", "floor_strike": self.strike_override or f"{int(ticker.rsplit('-', 1)[1]) - 0.5}"}
        if self.event_ticker:
            m["event_ticker"] = ticker.rsplit("-", 1)[0]
        if self.ei is not None:
            m["exchange_index"] = self.ei
        return m

    def orderbook(self, ticker):
        self.orderbook_calls += 1
        b, a = self.quotes[ticker]
        return {"yes": [(Decimal(b) / 100, self.size)], "no": [(Decimal(100 - a) / 100, self.size)]}

    def shard_cash(self, ei):
        if self.cash_raises:
            raise self.cash_raises
        return self.cash

    def positions(self, **kw):
        self.kw_seen.append(("positions", dict(kw)))
        ev = None if self.ignore_event_filter else kw.get("event_ticker")
        merged = [dict(x) for x in self.pos]                 # manual positions set by the test
        for t, q in self.own_pos.items():                    # plus the bot's own fills, summed per ticker like the API
            for x in merged:
                if x.get("ticker") == t and x.get("position_fp") not in (None, "") and str(x["position_fp"]).replace(
                        ".", "", 1).lstrip("-").isdigit():
                    x["position_fp"] = str(Decimal(str(x["position_fp"])) + q)
                    break
            else:
                merged.append({"ticker": t, "position_fp": str(q)})
        return [x for x in merged if ev is None or str(x.get("ticker", ev + "-")).startswith(ev + "-")]

    def orders(self, **kw):
        self.kw_seen.append(("orders", dict(kw)))
        if self.orders_raises:
            raise self.orders_raises
        ev = None if self.ignore_event_filter else kw.get("event_ticker")
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
        if any(o.get("client_order_id") == body["client_order_id"] for o in self.book.values()):
            return Result("duplicate", 409, None, {"code": "already_exists"})
        yb, ya = self.quotes[body["ticker"]]
        p = round(Decimal(body["price"]) * 100)
        if body["side"] == "bid":                          # buy YES: fills iff yes ask <= limit
            would, side_cost = ya <= p, Decimal(ya) / 100
        else:                                              # buy NO at (1 - price): fills iff NO ask <= limit
            would, side_cost = (100 - yb) <= (100 - p), Decimal(100 - yb) / 100
        if self.fill_mode == "never":
            would = False
        elif self.fill_mode == "always":
            would = True
        cnt = body["count"]
        _own_before = dict(self.own_pos)
        if self.rest_mode:
            status, fill, rem = "resting", "0.00", cnt
        elif would and self.partial:
            status, fill, rem = "canceled", self.partial, "0.00"
        elif would:
            status, fill, rem = "executed", cnt, "0.00"
        else:
            status, fill, rem = "canceled", "0.00", "0.00"
        if fill != "0.00" and not (self.create_result and self.create_result.outcome in ("rejected", "throttled", "auth")):
            sign = 1 if body["side"] == "bid" else -1
            self.own_pos[body["ticker"]] = self.own_pos.get(body["ticker"], Decimal(0)) + sign * Decimal(fill)
        if self.store_on_create:
            self.book[oid] = {"order_id": oid, "ticker": body["ticker"], "status": status,
                              "fill_count_fp": fill, "remaining_count_fp": rem,
                              "client_order_id": body["client_order_id"], "taker_fees_dollars": "0.0044" if fill != "0.00" else "0",
                              "maker_fees_dollars": "0", "maker_fill_cost_dollars": "0",
                              "taker_fill_cost_dollars": str(side_cost) if fill != "0.00" else "0"}
        if self.create_hook:
            self.create_hook(body, oid)
        if self.create_result:
            return self.create_result
        data = {"order_id": oid, "fill_count": fill, "remaining_count": rem}
        if fill != "0.00":                                  # average_fill_price is always the YES price
            data["average_fill_price"] = str(Decimal(ya) / 100 if body["side"] == "bid" else Decimal(yb) / 100)
            data["average_fee_paid"] = "0.0044"
        return Result("ok", 201, data)

    def cancel_order(self, oid, ei):
        self.cancelled.append((oid, ei))
        if self.book[oid]["status"] == "resting":
            self.book[oid]["status"] = "canceled"
        return Result("ok", 200, {"order_id": oid, "reduced_by": "1.00"})


class FakePublic:
    """Dry-run public reader."""

    def __init__(self, quotes, strike_of=None):
        self.quotes, self.strike_of = quotes, strike_of

    def side_ask(self, ticker, outcome):
        b, a = self.quotes[ticker]
        return (a if outcome == "yes" else 100 - b), Decimal(5)

    def market(self, ticker):
        return {"status": "active", "floor_strike": self.strike_of or f"{int(ticker.rsplit('-', 1)[1]) - 0.5}"}



def one_game(key=G1, tip=TIP, tk_=None):
    return (tip, key, [{"ticker": tk_ or tk(key), "exchange_index": 3}])


def make_bot(tmp_path, rows=None, games=None, live=True, client=None, compute=None, pings="default",
             quotes=None, **kw):
    """pings: "default" = a ping for G1 (OVER 219.5 @47); dict = entries {key: info}; {} = no pings."""
    games = games if games is not None else [one_game()]
    rows = rows if rows is not None else [mk_row()]
    seen = {"fetched": None, "row_calls": 0}

    def default_compute(day, now, on_header):
        g = list(games)
        if on_header:
            on_header(0, g)                   # real compute_rows passes its own list; the bot may filter it in place
            if on_header.__name__ == "only_wanted":
                seen["row_calls"] += 1
        seen["fetched"] = [k for _, k, _ in g]
        return rows, 0, g, None

    quotes = dict(quotes) if quotes is not None else {tk(r["game"]): (45, 47) for r in rows}
    client = client if client is not None else (FakeClient() if live else None)
    if client is not None:
        client.quotes.update(quotes)
    if pings == "default":
        pings = dict([ping()])
    (tmp_path / "pings.json").write_text(json.dumps(pings))
    lines = []
    bot = ob.Bot(client=client, live=live, compute=compute or default_compute, log_path=tmp_path / "log.jsonl",
                 stop_path=tmp_path / "STOP", lock_path=tmp_path / "bot.lock", ping_path=tmp_path / "pings.json",
                 heartbeat_path=tmp_path / "hb.json", echo=lines.append, sleep=lambda s: None,
                 public=None if live else FakePublic(quotes), **kw)
    bot.lines, bot.seen = lines, seen
    return bot, client


def T(bot, t, day=DAY):
    """One tick at simulated time t (the bot's own clock follows, so send-time re-checks see the same time)."""
    bot.clock = lambda: t
    bot.tick(day, t)


def logs(tmp_path):
    p = tmp_path / "log.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def rec(bot, game=G1, day=DAY, n=1):
    return bot.orders[(day.isoformat(), game, n)]


def set_ping(tmp_path, pings):
    (tmp_path / "pings.json").write_text(json.dumps(pings))


# ------------------------------------------------------------------ the ping is the source of truth
def test_ping_is_source_of_truth_side_strike_and_max_price(tmp_path):
    # ping says UNDER 220.5 (ticker -221) max 53c; compute_rows would say OVER 219.5 -- the ping must win
    t221 = tk(G1, "220.5")
    assert t221.endswith("-221")
    bot, c = make_bot(tmp_path, pings=dict([ping(side="UNDER", strike="220.5", limit_c=53)]),
                      quotes={t221: (47, 49), TK1: (45, 47)})
    T(bot, IN_WIN)
    assert len(c.created) == 1
    b = c.created[0]
    assert b["ticker"] == t221 and b["side"] == "ask" and b["price"] == "0.4700"   # buy NO at 53c = YES ask 0.47
    assert rec(bot)["price_c"] == 53 and rec(bot)["outcome"] == "no" and rec(bot)["max_c"] == 53
    assert bot.seen["row_calls"] == 0                                              # compute_rows not used for picks
    pick = [r for r in logs(tmp_path) if r["event"] == "pick"][0]
    assert pick["source"] == "ping" and pick["side"] == "UNDER" and pick["max_c"] == 53


def test_ping_fractional_and_ceil_ticker(tmp_path):
    p = ob.parse_ping({"side": "OVER", "strike": "219.5", "limit_c": 51}, G1)[0]
    assert p["ticker"] == TK1 and p["outcome"] == "yes" and p["max_c"] == 51
    p = ob.parse_ping({"side": "UNDER", "strike": "0.5", "limit_c": 51}, G1)[0]
    assert p["ticker"].endswith("-1")
    for bad in ({"side": "SIDEWAYS", "strike": "1.5", "limit_c": 50}, {"side": "OVER", "strike": "1.5", "limit_c": 90},
                {"side": "OVER", "strike": "1.5", "limit_c": 5}, {"side": "OVER", "strike": "x", "limit_c": 50},
                {"side": "OVER", "limit_c": 50}, {"side": "OVER", "strike": "1.5", "limit_c": True},
                {"side": "OVER", "strike": "1.5", "limit_c": 50.5}):
        assert ob.parse_ping(bad, G1)[0] is None


def test_ping_strike_must_match_market_floor_strike(tmp_path):
    bot, c = make_bot(tmp_path)
    c.strike_override = "230.5"
    T(bot, IN_WIN)
    assert c.created == [] and any("strike mismatch" in ln for ln in bot.lines)
    T(bot, IN_WIN + timedelta(minutes=1))
    assert c.created == []                                 # game closed for good


def test_unusable_ping_closes_game_no_fallback(tmp_path):
    bot, c = make_bot(tmp_path, pings={ping()[0]: {"side": "OVER", "strike": "219.5", "limit_c": 99}})
    T(bot, IN_WIN)
    T(bot, TIP - timedelta(minutes=50))
    assert c.created == [] and bot.seen["row_calls"] == 0


def test_no_ping_waits_then_falls_back_at_tip_minus_50(tmp_path):
    bot, c = make_bot(tmp_path, pings={})
    T(bot, TIP - timedelta(minutes=51))
    assert c.created == [] and bot.seen["row_calls"] == 0 and any("waiting" in ln for ln in bot.lines)
    T(bot, TIP - timedelta(minutes=50))
    assert len(c.created) == 1 and bot.seen["row_calls"] == 1
    pick = [r for r in logs(tmp_path) if r["event"] == "pick"][0]
    assert pick["source"] == "fallback" and pick["max_c"] == 47            # the row's taker price (YES ask 47c)
    assert rec(bot)["price_c"] == 47


def test_fallback_under_uses_row_taker_price(tmp_path):
    bot, c = make_bot(tmp_path, pings={}, rows=[mk_row(side="UNDER", bid=45, ask=47)], quotes={TK1: (45, 47)})
    T(bot, TIP - timedelta(minutes=50))
    assert len(c.created) == 1 and rec(bot)["max_c"] == 55 and rec(bot)["outcome"] == "no"
    assert c.created[0]["side"] == "ask" and c.created[0]["price"] == "0.4500"


def test_ping_arriving_before_fallback_is_used(tmp_path):
    bot, c = make_bot(tmp_path, pings={})
    T(bot, TIP - timedelta(minutes=55))
    assert c.created == []
    set_ping(tmp_path, dict([ping(limit_c=50)]))
    T(bot, TIP - timedelta(minutes=54))
    assert len(c.created) == 1 and rec(bot)["max_c"] == 50 and bot.seen["row_calls"] == 0


def test_fallback_never_bets_a_no_bet_row(tmp_path):
    for r in (mk_row(bet=False), {**mk_row(), "side": None}, {**mk_row(), "proj": None}):
        bot, c = make_bot(tmp_path, pings={}, rows=[r])
        T(bot, TIP - timedelta(minutes=50))
        T(bot, TIP - timedelta(minutes=48))
        assert c.created == []
    bot, c = make_bot(tmp_path, pings={}, rows=[mk_row(bet=False)])
    T(bot, TIP - timedelta(minutes=50))
    assert any("NO BET" in ln for ln in bot.lines)


def test_fallback_side_buy_mismatch_skipped(tmp_path):
    r = mk_row(side="OVER")
    r["buy"] = "NO"
    bot, c = make_bot(tmp_path, pings={}, rows=[r])
    T(bot, TIP - timedelta(minutes=50))
    assert c.created == []


def test_fallback_not_started_after_tip_minus_45(tmp_path):
    bot, c = make_bot(tmp_path, pings={})
    T(bot, TIP - timedelta(minutes=44))
    assert c.created == []


# ------------------------------------------------------------------ IOC body, price rule, retries
def test_ioc_body_no_post_only_and_tif_ioc(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)
    b = c.created[0]
    assert "post_only" not in b and "expiration_time" not in b and "cancel_order_on_pause" not in b
    assert set(b) == {"ticker", "client_order_id", "side", "count", "price", "time_in_force",
                      "self_trade_prevention_type", "exchange_index"}
    assert b["time_in_force"] == "immediate_or_cancel"
    assert b["side"] == "bid" and b["price"] == "0.4700" and b["count"] == "1.00"
    assert b["self_trade_prevention_type"] == "taker_at_cross" and b["exchange_index"] == 3
    assert b["client_order_id"] == f"eob-2026-10-25-{G1}-a1"
    assert validate_order_body(b) == b


def test_limit_is_current_ask_not_max_price(tmp_path):
    bot, c = make_bot(tmp_path, pings=dict([ping(limit_c=52)]), quotes={TK1: (45, 47)})
    T(bot, IN_WIN)
    assert c.created[0]["price"] == "0.4700" and rec(bot)["price_c"] == 47 and rec(bot)["max_c"] == 52


def test_no_send_when_ask_above_max_then_retry_when_back(tmp_path):
    bot, c = make_bot(tmp_path, quotes={TK1: (46, 49)})            # ask 49 > max 47
    T(bot, IN_WIN)
    T(bot, IN_WIN + timedelta(minutes=1))
    assert c.created == [] and any("not chasing" in ln for ln in bot.lines)
    assert sum("not chasing" in ln for ln in bot.lines) == 1        # logged once, not every loop
    c.quotes[TK1] = (45, 47)                                       # the ask comes back
    T(bot, IN_WIN + timedelta(minutes=2))
    assert len(c.created) == 1 and rec(bot)["state"] == "filled"


def test_retry_continues_after_normal_window_until_tip_minus_15(tmp_path):
    bot, c = make_bot(tmp_path, quotes={TK1: (46, 49)})
    T(bot, IN_WIN)                                                 # armed, ask too high
    c.quotes[TK1] = (45, 47)
    T(bot, TIP - timedelta(minutes=20))                            # past T-45 but before T-15: still allowed
    assert len(c.created) == 1


def test_no_attempt_at_or_after_tip_minus_15(tmp_path):
    bot, c = make_bot(tmp_path, quotes={TK1: (46, 49)})
    T(bot, IN_WIN)
    c.quotes[TK1] = (45, 47)
    T(bot, TIP - timedelta(minutes=15))
    assert c.created == []


def test_unfilled_ioc_is_not_chased_and_retried_when_ask_ok(tmp_path):
    bot, c = make_bot(tmp_path)
    c.fill_mode = "never"                                          # ask looked fine, but the IOC found no fill
    T(bot, IN_WIN)
    o = rec(bot)
    assert o["state"] == "canceled" and o["filled"] == 0 and ob.order_cost(o) == 0
    c.fill_mode = "auto"
    c.quotes[TK1] = (46, 49)                                       # price moved up: no new order
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 1
    c.quotes[TK1] = (45, 47)
    T(bot, IN_WIN + timedelta(minutes=2))
    assert len(c.created) == 2 and rec(bot, n=2)["state"] == "filled"
    assert c.created[1]["price"] == "0.4700"                       # never above the max, never raised


def test_max_four_attempts_per_game(tmp_path):
    bot, c = make_bot(tmp_path)
    c.fill_mode = "never"
    for k in range(8):
        T(bot, IN_WIN + timedelta(minutes=k))
    assert len(c.created) == 4
    assert [b["client_order_id"] for b in c.created] == [f"eob-2026-10-25-{G1}-a{i}" for i in (1, 2, 3, 4)]
    assert bot.games[(DAY.isoformat(), G1)]["closed"]


def test_coid_unique_per_attempt_and_valid(tmp_path):
    bot, c = make_bot(tmp_path)
    c.fill_mode = "never"
    for k in range(4):
        T(bot, IN_WIN + timedelta(minutes=k))
    ids = [b["client_order_id"] for b in c.created]
    assert len(set(ids)) == 4 and all(i.startswith("eob-") for i in ids)
    for b in c.created:
        validate_order_body(b)


def test_stop_after_one_fill(tmp_path):
    bot, c = make_bot(tmp_path)
    for k in range(5):
        T(bot, IN_WIN + timedelta(minutes=k))
    assert len(c.created) == 1 and rec(bot)["state"] == "filled" and rec(bot)["filled"] == Decimal("1.00")
    assert any(r["event"] == "fill" for r in logs(tmp_path))
    assert bot.games[(DAY.isoformat(), G1)]["closed"]


def test_ask_below_min_price_not_sent(tmp_path):
    bot, c = make_bot(tmp_path, pings=dict([ping(limit_c=50)]), quotes={TK1: (10, 12)})
    T(bot, IN_WIN)
    assert c.created == []


def test_under_buys_no_at_no_ask(tmp_path):
    bot, c = make_bot(tmp_path, pings=dict([ping(side="UNDER", limit_c=55)]), quotes={TK1: (45, 47)})
    T(bot, IN_WIN)                                                 # NO ask = 100 - yes bid = 55
    b = c.created[0]
    assert b["side"] == "ask" and b["price"] == "0.4500" and rec(bot)["price_c"] == 55


def test_ordering_game_outside_window_not_started(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, TIP - timedelta(minutes=76))
    T(bot, TIP - timedelta(minutes=44))
    assert c.created == []
    T(bot, TIP - timedelta(minutes=75))
    assert len(c.created) == 1


def test_fallback_fetch_limited_to_games_in_window(tmp_path):
    g2 = one_game(G2, TIP + timedelta(hours=3))
    bot, c = make_bot(tmp_path, pings={}, games=[one_game(), g2], rows=[mk_row(), mk_row(G2)])
    T(bot, TIP - timedelta(minutes=50))
    assert bot.seen["fetched"] == [G1]
    assert len(c.created) == 1


def test_missing_quote_skipped(tmp_path):
    bot, c = make_bot(tmp_path)
    c.orderbook = lambda t: {"yes": [], "no": []}
    T(bot, IN_WIN)
    assert c.created == []


# ------------------------------------------------------------------ caps (pure)
def order(game, price_c=50, state="open", filled=0):
    return {"game": game, "price_c": price_c, "state": state, "filled": filled, "count": 1}


NOW = IN_WIN


def caps(orders, game="G9", ticker="KXNBATOTAL-G9-1", action="buy", count=1, price=50, now=NOW):
    return ob.check_caps(orders, game, ticker, action, count, price, now)


def test_cap_ok():
    assert caps([]) == []


def test_cap_series_buys_count():
    assert any("series" in v for v in caps([], ticker="KXNBAGAME-G9-1"))
    assert any("buy" in v for v in caps([], action="sell"))
    assert any("count" in v for v in caps([], count=2))


@pytest.mark.parametrize("p", [14, 86, 0, 100])
def test_cap_price_range(p):
    assert any("price" in v for v in caps([], price=p))


@pytest.mark.parametrize("p", [15, 85])
def test_cap_price_edges_ok(p):
    assert not any("outside" in v for v in caps([], price=p))


def test_cap_one_filled_per_game():
    assert any("filled" in v for v in caps([order("G9", state="filled", filled=1)]))
    assert not any("filled" in v for v in caps([order("OTHER", state="filled", filled=1)]))
    assert any("filled" in v for v in caps([order("G9", state="canceled", filled=Decimal("0.4"))]))


def test_cap_four_attempts_per_game():
    three = [order("G9", 50, "canceled") for _ in range(3)]
    assert caps(three) == []
    assert any("attempts for game" in v for v in caps(three + [order("G9", 50, "canceled")]))


def test_cap_forty_attempts_per_night():
    ok = [order(f"G{i}", 15, "canceled") for i in range(39)]
    assert not any("attempts tonight" in v for v in caps(ok))
    assert any("attempts tonight" in v for v in caps(ok + [order("G40", 15, "canceled")]))


def test_taker_fee_and_cost_values():
    assert ob.est_fee(50) == Decimal("0.0176")                       # ceil(0.07*.25 /1e-4)=175 -> .0175, +1 step
    assert ob.est_fee(85) == Decimal("0.0091")                       # 0.008925 -> .0090, +1 step
    assert ob.order_cost(order("A", 50, "filled", 1)) == Decimal("0.50") + Decimal("0.0176")
    assert ob.order_cost(order("A", 50, "canceled", 0)) == 0 and ob.order_cost(order("A", 50, "failed")) == 0
    assert ob.order_cost(order("A", 50, "open")) == Decimal("0.5176")      # in-flight counts at its limit
    assert ob.order_cost(order("A", 50, "unknown")) == Decimal("0.5176")


def test_cap_seven_dollars_binds_counting_filled_in_flight_and_taker_fee():
    unit = Decimal("0.85") + ob.est_fee(85)
    assert Decimal("0.85") < unit < Decimal("0.87")
    eight = [order(f"G{i}", 85, "open" if i % 2 else "filled", 0 if i % 2 else 1) for i in range(8)]
    seven = eight[:7]
    assert sum(ob.order_cost(o) for o in eight) == 8 * unit and 8 * unit <= 7 < 9 * unit
    assert caps(seven, price=85) == []                                      # the 8th fits
    assert any("nightly cap" in v for v in caps(eight, price=85))           # the 9th is blocked
    # edge: whatever room is left after 8 x unit, an order fits iff price + taker fee <= room
    edge = eight + [order("Z", 15, "canceled")]                             # unfilled IOC costs nothing
    total = sum(ob.order_cost(o) for o in edge)
    room = Decimal("7.00") - total
    for p in (15, 16, 17, 18, 19, 20, 21):
        new = Decimal(p) / 100 + ob.est_fee(p)
        assert (not any("nightly cap" in v for v in caps(edge, game="Y", price=p))) == (new <= room)
    freed = [order(f"G{i}", 85, "canceled", 0) for i in range(8)]
    assert not any("nightly cap" in v for v in caps(freed, price=85))
    assert ob.order_cost(order("A", 50, "not_found")) > Decimal("0.5")      # unresolved: full cost stays


def test_nightly_cap_blocks_live_attempt(tmp_path):
    bot, c = make_bot(tmp_path)
    bot.orders[(DAY.isoformat(), "XX", 1)] = {**order("XX", 85, "filled", 1), "day": DAY.isoformat(), "n": 1}
    for i in range(8):
        bot.orders[(DAY.isoformat(), f"F{i}", 1)] = {**order(f"F{i}", 85, "filled", 1), "day": DAY.isoformat(), "n": 1}
    T(bot, IN_WIN)                                                  # 9 x 0.8590 = 7.73 already > 7
    assert c.created == [] and any(r["event"] == "caps" and r["ok"] is False for r in logs(tmp_path))


def test_cap_after_end_date():
    assert caps([], now=ob.HARD_END + timedelta(minutes=1))
    assert caps([], now=ob.HARD_END) == []


def test_no_orders_after_end_date_even_with_window_open(tmp_path):
    tip = ob.HARD_END.astimezone(ET) + timedelta(hours=1, minutes=30)    # 2026-11-04 01:29 ET
    now = tip - timedelta(minutes=60)
    assert now > ob.HARD_END
    d = tip.date()
    bot, c = make_bot(tmp_path, games=[one_game(tip=tip)], pings=dict([ping(day=d)]))
    T(bot, now, day=d)
    assert c.created == []
    assert any("no orders after" in ln for ln in bot.lines)


def test_end_date_rechecked_at_send_time(tmp_path):
    bot, c = make_bot(tmp_path)
    bot.clock = lambda: ob.HARD_END + timedelta(minutes=5)
    bot.tick(DAY, IN_WIN)
    assert c.created == []


def test_cap_series_violation_logged_and_skipped(tmp_path):
    bot, c = make_bot(tmp_path, pings={}, rows=[mk_row(ticker=f"KXNBAGAME-{G1}-220")])
    T(bot, TIP - timedelta(minutes=50))
    assert c.created == []


# ------------------------------------------------------------------ double exposure (manual positions)
def test_existing_position_skips_and_closes_game(tmp_path):
    bot, c = make_bot(tmp_path)
    c.pos = [{"ticker": TK1, "position_fp": "-1.00"}]
    T(bot, IN_WIN)
    assert c.created == [] and any("already exposed" in ln for ln in bot.lines)
    c.pos = []
    T(bot, IN_WIN + timedelta(minutes=1))
    assert c.created == []                                          # the game stays closed tonight


def test_manual_resting_order_skips(tmp_path):
    bot, c = make_bot(tmp_path)
    c.book["manual1"] = {"order_id": "manual1", "ticker": TK1, "status": "resting", "client_order_id": "manual"}
    T(bot, IN_WIN)
    assert c.created == []


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


def test_exposure_check_is_event_level_with_explicit_shard(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)
    pk = [kw for n, kw in c.kw_seen if n == "positions"][0]
    ok = [kw for n, kw in c.kw_seen if n == "orders" and kw.get("status") == "resting"][0]
    assert pk == {"event_ticker": f"KXNBATOTAL-{G1}", "exchange_index": 3}
    assert ok == {"event_ticker": f"KXNBATOTAL-{G1}", "status": "resting", "exchange_index": 3}


def test_exposure_checked_before_every_attempt(tmp_path):
    bot, c = make_bot(tmp_path)
    c.fill_mode = "never"
    T(bot, IN_WIN)
    c.pos = [{"ticker": TK1, "position_fp": "1.00"}]               # the user buys by hand between attempts
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 1


def test_missing_exchange_index_live_skips_never_zero(tmp_path):
    bot, c = make_bot(tmp_path)
    c.ei = None
    T(bot, IN_WIN)
    assert c.created == [] and any("exchange_index" in ln for ln in bot.lines)


def test_missing_event_ticker_skips(tmp_path):
    bot, c = make_bot(tmp_path)
    c.event_ticker = False
    T(bot, IN_WIN)
    assert c.created == [] and any("event_ticker" in ln for ln in bot.lines)


def test_exposure_read_error_fails_closed(tmp_path):
    bot, c = make_bot(tmp_path)

    def bad(**kw):
        raise RuntimeError("down")
    c.positions = bad
    T(bot, IN_WIN)
    assert c.created == []


# ------------------------------------------------------------------ heartbeat
def test_heartbeat_written_atomically_and_rate_limited(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)
    hb = json.loads((tmp_path / "hb.json").read_text())
    assert set(hb) == {"ts_utc", "pid", "live", "placing", "night"}
    assert hb["live"] is True and hb["placing"] is True and hb["night"] == "2026-10-25" and isinstance(hb["pid"], int)
    assert datetime.fromisoformat(hb["ts_utc"]) == IN_WIN.astimezone(timezone.utc)
    assert not (tmp_path / "hb.json.tmp").exists()
    T(bot, IN_WIN + timedelta(seconds=30))
    assert json.loads((tmp_path / "hb.json").read_text())["ts_utc"] == hb["ts_utc"]      # <60 s: not rewritten
    T(bot, IN_WIN + timedelta(seconds=61))
    assert json.loads((tmp_path / "hb.json").read_text())["ts_utc"] != hb["ts_utc"]


def test_heartbeat_live_false_in_dry_run_and_live_false_on_exit(tmp_path):
    bot, _ = make_bot(tmp_path, live=False)
    T(bot, IN_WIN)
    assert json.loads((tmp_path / "hb.json").read_text())["live"] is False
    bot, c = make_bot(tmp_path)
    (tmp_path / "STOP").write_text("x")
    bot.clock = lambda: IN_WIN
    bot.run(DAY)
    assert json.loads((tmp_path / "hb.json").read_text())["live"] is False           # bot gone: not "auto-placed"


def test_dry_run_does_not_clobber_fresh_live_heartbeat(tmp_path):
    live = {"ts_utc": IN_WIN.astimezone(timezone.utc).isoformat(), "pid": 999999999, "live": True, "night": "2026-10-25"}
    (tmp_path / "hb.json").write_text(json.dumps(live))
    bot, _ = make_bot(tmp_path, live=False)
    T(bot, IN_WIN)
    assert json.loads((tmp_path / "hb.json").read_text()) == live


# ------------------------------------------------------------------ restart / one game
def test_restart_restores_attempts_and_does_not_replace_a_filled_game(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)
    assert len(c.created) == 1
    bot2, _ = make_bot(tmp_path, client=c)              # a new process: same log, same exchange state
    bot2.reconcile_from_log()
    T(bot2, IN_WIN + timedelta(minutes=3))
    assert len(c.created) == 1 and rec(bot2)["order_id"] == "oid1" and rec(bot2)["state"] == "filled"


def test_restart_keeps_attempt_count_and_pick(tmp_path):
    bot, c = make_bot(tmp_path)
    c.fill_mode = "never"
    T(bot, IN_WIN)
    T(bot, IN_WIN + timedelta(minutes=1))
    bot2, _ = make_bot(tmp_path, client=c, pings={})   # ping now gone; restored pick still applies
    bot2.reconcile_from_log()
    c.fill_mode = "never"
    for k in range(2, 8):
        T(bot2, IN_WIN + timedelta(minutes=k))
    assert len(c.created) == 4                          # 2 + 2 more, never more than 4 in total
    assert c.created[2]["client_order_id"].endswith("-a3")


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
    assert len(c.created) == 1                          # unresolved attempt blocks a new one; found by coid -> filled
    assert rec(bot2)["state"] == "filled"


def test_dry_run_records_do_not_block_live(tmp_path):
    bot, _ = make_bot(tmp_path, live=False)
    T(bot, IN_WIN)
    bot2, c2 = make_bot(tmp_path)
    bot2.reconcile_from_log()
    T(bot2, IN_WIN)
    assert len(c2.created) == 1


# ------------------------------------------------------------------ create outcomes
def test_409_duplicate_treated_as_unknown_resolved_by_coid(tmp_path):
    bot, c = make_bot(tmp_path)
    c.create_result = Result("duplicate", 409, None, {"code": "already_exists"})
    T(bot, IN_WIN)
    o = rec(bot)
    assert o["order_id"] == "oid1" and o["state"] == "filled"        # resolved by client_order_id
    assert ob.order_cost(o) > Decimal("0.47")


def test_409_unresolvable_stays_unknown_counts_and_blocks_more_attempts(tmp_path):
    bot, c = make_bot(tmp_path)
    c.store_on_create = False
    c.create_result = Result("duplicate", 409, None, {"code": "already_exists"})
    T(bot, IN_WIN)
    o = rec(bot)
    assert o["state"] == "unknown" and ob.order_cost(o) > Decimal("0.47")
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 1                                       # no second IOC while one is unresolved


def test_unknown_outcome_resolved(tmp_path):
    bot, c = make_bot(tmp_path)
    c.create_result = Result("unknown", None, None, {"code": "transport_error"})
    T(bot, IN_WIN)
    assert rec(bot)["state"] == "filled" and rec(bot)["order_id"] == "oid1"


def test_throttled_and_rejected_treated_as_failed_and_may_retry(tmp_path):
    bot, c = make_bot(tmp_path)
    c.store_on_create = False
    c.create_result = Result("throttled", 429, None, {"code": "rate"})
    T(bot, IN_WIN)
    assert rec(bot)["state"] == "failed" and ob.order_cost(rec(bot)) == 0
    c.create_result = None
    c.store_on_create = True
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 2                                       # failed = definitely not placed; next loop retries
    bot, c = make_bot(tmp_path)
    c.store_on_create = False
    c.create_result = Result("rejected", 400, None, {"code": "invalid_order"})
    T(bot, IN_WIN)
    assert rec(bot)["state"] == "failed"


def test_response_says_filled_is_trusted_and_confirmed(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)
    assert rec(bot)["state"] == "filled"
    fills = [r for r in logs(tmp_path) if r["event"] == "fill"]
    assert fills and fills[0]["attempt"] == 1


# ------------------------------------------------------------------ stalled / lost orders (backstop)
def test_lost_order_without_id_stops_blocking_after_misses(tmp_path):
    bot, c = make_bot(tmp_path)
    c.store_on_create = False
    c.create_result = Result("unknown", None, None, {"code": "transport_error"})
    T(bot, IN_WIN)
    o = rec(bot)
    assert o["state"] == "unknown" and o["order_id"] is None
    assert not bot.day_done(DAY, TIP + timedelta(hours=1))
    t = TIP - timedelta(minutes=15)
    T(bot, t)
    assert o["cancel_tries"] >= 1
    for k in range(4):
        T(bot, t + timedelta(minutes=11 + k))
    assert o["state"] == "not_found" and ob.order_cost(o) > Decimal("0.47")
    assert bot.day_done(DAY, TIP + timedelta(hours=1))


def test_cancel_tries_exhaust_on_no_id_path(tmp_path):
    bot, c = make_bot(tmp_path)
    c.store_on_create = False
    c.create_result = Result("unknown", None, None, {"code": "transport_error"})
    T(bot, IN_WIN)
    for k in range(12):
        T(bot, TIP - timedelta(minutes=15, seconds=-k))
    assert rec(bot)["cancel_tries"] == ob.MAX_CANCEL_TRIES and rec(bot)["state"] == "unknown"
    assert bot.day_done(DAY, TIP + timedelta(hours=1))


def test_order_get_raises_falls_back_to_listing(tmp_path):
    bot, c = make_bot(tmp_path)
    c.rest_mode = True                                               # a (misbehaving) IOC left resting
    T(bot, IN_WIN)
    assert rec(bot)["state"] == "open"
    c.order_raises = RuntimeError("single-order GET broken on this shard")
    c.book["oid1"].update(status="executed", fill_count_fp="1.00", remaining_count_fp="0.00")
    T(bot, IN_WIN + timedelta(minutes=1))
    assert rec(bot)["state"] == "filled" and any(r["event"] == "fill" for r in logs(tmp_path))


def test_id_never_found_becomes_not_found(tmp_path):
    bot, c = make_bot(tmp_path)
    c.rest_mode = True
    T(bot, IN_WIN)
    del c.book["oid1"]
    c.order_raises = KeyError("oid1")
    t = TIP - timedelta(minutes=15)
    for k in range(5):
        T(bot, t + timedelta(minutes=11 + k))
    assert rec(bot)["state"] == "not_found"


def test_apply_unusable_view_keeps_state(tmp_path):
    bot, c = make_bot(tmp_path)
    c.rest_mode = True
    T(bot, IN_WIN)
    o = rec(bot)
    bot._apply(o, {"state": "unknown", "filled": Decimal(0), "remaining": Decimal(0), "fees": Decimal(0),
                   "avg_cost": None, "api_status": None}, "t")
    assert o["state"] == "open"


def test_backstop_cancels_a_resting_order_at_tip_minus_15(tmp_path):
    bot, c = make_bot(tmp_path)
    c.rest_mode = True
    T(bot, IN_WIN)
    assert rec(bot)["state"] == "open" and len(c.created) == 1       # no second order while one is open
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 1
    T(bot, TIP - timedelta(minutes=16))
    assert c.cancelled == []
    T(bot, TIP - timedelta(minutes=15))
    assert c.cancelled == [("oid1", 3)] and rec(bot)["state"] == "canceled"
    T(bot, TIP - timedelta(minutes=14))
    assert len(c.cancelled) == 1 and len(c.created) == 1


def test_no_cancel_for_filled(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)
    T(bot, TIP - timedelta(minutes=15))
    assert c.cancelled == []


# ------------------------------------------------------------------ STOP / Ctrl+C / halt / finally
def two_games():
    return [one_game(G1), one_game(G2)]


def test_stop_file_cancels_all_and_exits(tmp_path):
    bot, c = make_bot(tmp_path, rows=[mk_row(G1), mk_row(G2)], games=two_games(),
                      pings=dict([ping(G1), ping(G2)]))
    c.rest_mode = True
    T(bot, IN_WIN)
    assert len(c.created) == 2
    (tmp_path / "STOP").write_text("x")
    bot.run(DAY)
    assert len(c.cancelled) == 2 and bot.stopped and len(c.created) == 2


def test_stop_file_blocks_new_orders(tmp_path):
    bot, c = make_bot(tmp_path)
    (tmp_path / "STOP").write_text("x")
    T(bot, IN_WIN)
    assert c.created == []


def test_ctrl_c_cancels_all(tmp_path):
    bot, c = make_bot(tmp_path)
    c.rest_mode = True
    T(bot, IN_WIN)

    def boom(day, now):
        raise KeyboardInterrupt
    bot.tick = boom
    bot.run(DAY)
    assert c.cancelled == [("oid1", 3)] and bot.stopped


def test_ctrl_c_during_create_cancels_the_order(tmp_path):
    bot, c = make_bot(tmp_path)
    c.rest_mode = True

    def interrupt(body, oid):
        raise KeyboardInterrupt
    c.create_hook = interrupt
    bot.clock = lambda: IN_WIN
    bot.run(DAY)
    assert len(c.created) == 1 and c.cancelled == [("oid1", 3)]


def test_finally_cancels_on_unexpected_exit(tmp_path):
    bot, c = make_bot(tmp_path)
    c.rest_mode = True
    T(bot, IN_WIN)

    def die(first_day, once):
        raise SystemExit("unexpected")
    bot._run_days = die
    with pytest.raises(SystemExit):
        bot.run(DAY)
    assert c.cancelled == [("oid1", 3)]


def test_first_auth_error_halts_after_cancelling(tmp_path):
    bot, c = make_bot(tmp_path, rows=[mk_row(G1), mk_row(G2)], games=two_games(),
                      pings=dict([ping(G1), ping(G2)]))
    c.rest_mode = True
    T(bot, IN_WIN)
    assert len(c.created) == 2
    c.orders_raises = AuthErr("401")                       # listing is the primary lookup now
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
class ReadOnlyClient:
    """Any write endpoint (and any authenticated read) trips the test."""

    def __getattr__(self, n):
        raise AssertionError(f"dry run touched client.{n}")


def test_dry_run_never_calls_a_write_endpoint_and_logs_what_it_would_send(tmp_path):
    bot, _ = make_bot(tmp_path, live=False)
    bot.client = ReadOnlyClient()
    T(bot, IN_WIN)
    assert any("DRY RUN, NOT SENT" in ln for ln in bot.lines)
    w = [r for r in logs(tmp_path) if r["event"] == "would_send"][0]
    assert w["body"]["time_in_force"] == "immediate_or_cancel" and "post_only" not in w["body"]
    assert w["source"] == "ping" and w["dry"] is True
    T(bot, TIP - timedelta(minutes=15))                   # dry cancel path touches nothing either
    bot.cancel_all_open("x")
    assert not (tmp_path / "bot.lock").exists()


def test_dry_run_respects_max_price_and_fallback(tmp_path):
    bot, _ = make_bot(tmp_path, live=False, quotes={TK1: (46, 49)})
    T(bot, IN_WIN)
    assert not any("DRY RUN, NOT SENT" in ln for ln in bot.lines)
    bot, _ = make_bot(tmp_path, live=False, pings={})
    T(bot, TIP - timedelta(minutes=50))
    assert any("DRY RUN, NOT SENT" in ln and "source fallback" in ln for ln in bot.lines)


def test_dry_run_full_run_loop_end_to_end(tmp_path):
    sim = {"t": IN_WIN - timedelta(minutes=10)}
    bot, _ = make_bot(tmp_path, live=False, until=DAY)
    bot.client = ReadOnlyClient()
    bot.clock = lambda: sim["t"]
    bot._sleep = lambda s: sim.__setitem__("t", sim["t"] + timedelta(seconds=s))
    bot.run(DAY)
    assert sum(1 for ln in bot.lines if "DRY RUN, NOT SENT" in ln) == 1
    assert [r["event"] for r in logs(tmp_path)][-1] == "exit"


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


def test_main_live_refuses_without_prod_and_demo(monkeypatch):
    _tripwires(monkeypatch)
    for v in (None, "demo"):
        monkeypatch.setattr("kpm.core.config.secret", lambda n, v=v: v)
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
    held = ob.acquire_lock(bot1.lock_path)
    try:
        with pytest.raises(SystemExit):
            bot2.run(DAY)
        assert c2.created == []
        with pytest.raises(SystemExit):
            ob.acquire_lock(bot1.lock_path)
    finally:
        ob.release_lock(held)
    ob.release_lock(ob.acquire_lock(bot1.lock_path))


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
    ob.release_lock(ob.acquire_lock(bot.lock_path))


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
    bot.run(DAY)


def test_console_lines_ascii(tmp_path):
    bot, _ = make_bot(tmp_path)
    T(bot, IN_WIN)
    assert bot.lines and all(ln.isascii() for ln in bot.lines)


# ------------------------------------------------------------------ multi-day run, DST, schedule reload
def test_day_start_is_11_et_and_dst_safe():
    assert ob.day_start(date(2026, 10, 31)) == datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)
    assert ob.day_start(date(2026, 11, 1)) == datetime(2026, 11, 1, 16, 0, tzinfo=timezone.utc)
    assert (ob.day_start(date(2026, 11, 2)) - ob.day_start(date(2026, 11, 1))) == timedelta(hours=24)
    assert (ob.day_start(date(2026, 11, 1)) - ob.day_start(date(2026, 10, 31))) == timedelta(hours=25)


def _two_day_setup(tmp_path):
    d1, d2 = date(2026, 10, 25), date(2026, 10, 26)
    tips = {d1: datetime(2026, 10, 25, 19, 0, tzinfo=ET), d2: datetime(2026, 10, 26, 19, 30, tzinfo=ET)}
    keys = {d1: "26OCT25AAABBB", d2: "26OCT26AAABBB"}

    def compute(day, now, on_header):
        key = keys[day]
        games = [(tips[day], key, [{"ticker": tk(key), "exchange_index": 3}])]
        if on_header:
            on_header(0, games)
        return [mk_row(key)], 0, games, None

    c = FakeClient()
    pings = dict([ping(keys[d1], d1), ping(keys[d2], d2)])
    quotes = {tk(keys[d1]): (45, 47), tk(keys[d2]): (45, 47)}
    bot, _ = make_bot(tmp_path, client=c, compute=compute, until=d2, pings=pings, quotes=quotes)
    return bot, c, d1, d2


def test_multi_day_run_places_each_night(tmp_path):
    bot, c, d1, d2 = _two_day_setup(tmp_path)
    sim = {"t": ob.day_start(d1)}
    bot.clock = lambda: sim["t"]
    bot._sleep = lambda s: sim.__setitem__("t", sim["t"] + timedelta(seconds=s))
    bot.run(d1)
    assert {o["day"] for o in bot.orders.values()} == {"2026-10-25", "2026-10-26"}
    assert len(c.created) == 2 and c.cancelled == []                     # one fill per night; nothing to cancel
    ev = [r["event"] for r in logs(tmp_path)]
    assert ev.count("day_done") == 2 and ev[-1] == "exit"
    assert sim["t"] >= ob.day_start(d2)
    hb = json.loads((tmp_path / "hb.json").read_text())
    assert hb["live"] is False                                           # final heartbeat on exit


def test_clock_failure_at_day_start_skips_placing(tmp_path):
    bot, c, d1, d2 = _two_day_setup(tmp_path)
    sim = {"t": ob.day_start(d1)}
    skews = iter([0.0, 5.0])
    c.server_time_skew = lambda: next(skews)
    bot.clock = lambda: sim["t"]
    bot._sleep = lambda s: sim.__setitem__("t", sim["t"] + timedelta(seconds=s))
    bot.run(d1)
    assert len(c.created) == 1 and {o["day"] for o in bot.orders.values()} == {"2026-10-25"}


def test_schedule_reloaded_every_30_minutes(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, TIP - timedelta(hours=6))
    first = bot.sched[DAY.isoformat()]["loaded_at"]
    T(bot, TIP - timedelta(hours=6) + timedelta(minutes=29))
    assert bot.sched[DAY.isoformat()]["loaded_at"] == first
    T(bot, TIP - timedelta(hours=6) + timedelta(minutes=31))
    assert bot.sched[DAY.isoformat()]["loaded_at"] > first


def test_reload_failure_still_cancels_on_time_and_keeps_schedule(tmp_path):
    base = make_bot(tmp_path)[0]._compute

    def flaky(day, now, hdr):
        if now >= TIP - timedelta(minutes=20) and hdr is not None and hdr.__name__ == "hdr":
            raise RuntimeError("markets endpoint down")
        return base(day, now, hdr)
    bot, c = make_bot(tmp_path, compute=flaky)
    c.rest_mode = True
    T(bot, IN_WIN)
    assert len(c.created) == 1
    T(bot, TIP - timedelta(minutes=15))
    assert c.cancelled == [("oid1", 3)]
    assert bot.sched[DAY.isoformat()]["games"]
    assert any("schedule reload" in ln for ln in bot.lines)


# ------------------------------------------------------------------ parsing
def test_parse_order_obj():
    p = ob.parse_order_obj({"status": "canceled", "fill_count_fp": "0.00", "remaining_count_fp": "0.00"})
    assert p["state"] == "canceled"
    p = ob.parse_order_obj({"status": "executed", "fill_count_fp": "1.00", "remaining_count_fp": "0.00",
                            "taker_fill_cost_dollars": "0.47", "taker_fees_dollars": "0.0044"})
    assert p["state"] == "filled" and p["avg_cost"] == Decimal("0.47") and p["fees"] == Decimal("0.0044")


def test_decision_uses_current_time_for_fallback(tmp_path):
    seen = []
    bot, c = make_bot(tmp_path, pings={})
    inner = bot._compute

    def spy(day, now, hdr):
        seen.append(now)
        return inner(day, now, hdr)
    bot._compute = spy
    t = TIP - timedelta(minutes=50)
    T(bot, t)
    assert seen[-1] == t


def test_listing_fallback_passes_exchange_index(tmp_path):
    bot, c = make_bot(tmp_path)
    c.rest_mode = True
    T(bot, IN_WIN)
    c.order_raises = RuntimeError("x")
    c.kw_seen.clear()
    T(bot, IN_WIN + timedelta(minutes=1))
    assert ("orders", {"ticker": TK1, "exchange_index": 3}) in c.kw_seen


# ====================================================================== audit round: fixes #1 - #12
class Forbidden(Exception):
    status = 403


def hb_of(tmp_path):
    return json.loads((tmp_path / "hb.json").read_text())


# ---- #1 heartbeat honesty
def test_heartbeat_placing_false_when_clock_check_failed(tmp_path):
    bot, c = make_bot(tmp_path)
    bot.clock_ok = False
    T(bot, IN_WIN)
    hb = hb_of(tmp_path)
    assert hb["live"] is True and hb["placing"] is False and c.created == []


def test_heartbeat_placing_flips_immediately_when_state_changes(tmp_path):
    bot, c = make_bot(tmp_path)
    T(bot, TIP - timedelta(hours=6))
    assert hb_of(tmp_path)["placing"] is True
    bot.clock_ok = False
    T(bot, TIP - timedelta(hours=6) + timedelta(seconds=10))          # < 60 s later, but the state changed
    assert hb_of(tmp_path)["placing"] is False


def test_heartbeat_placing_false_when_caps_exhausted(tmp_path):
    bot, c = make_bot(tmp_path)
    for i in range(9):
        bot.orders[(DAY.isoformat(), f"F{i}", 1)] = {**order(f"F{i}", 85, "filled", 1), "day": DAY.isoformat(), "n": 1}
    T(bot, TIP - timedelta(hours=6))
    assert hb_of(tmp_path)["placing"] is False                         # 9 x 0.859 > $7
    bot2, _ = make_bot(tmp_path)
    for i in range(40):
        bot2.orders[(DAY.isoformat(), f"A{i}", 1)] = {**order(f"A{i}", 15, "canceled"), "day": DAY.isoformat(), "n": 1}
    T(bot2, TIP - timedelta(hours=6))
    assert hb_of(tmp_path)["placing"] is False


def test_heartbeat_placing_false_after_end_date_dry_run_and_exit(tmp_path):
    bot, _ = make_bot(tmp_path)
    assert bot.placing_now(DAY, ob.HARD_END + timedelta(minutes=1)) is False
    assert bot.placing_now(DAY, IN_WIN) is True
    bot, _ = make_bot(tmp_path, live=False)
    T(bot, IN_WIN)
    assert hb_of(tmp_path)["placing"] is False
    bot, c = make_bot(tmp_path)
    (tmp_path / "STOP").write_text("x")
    bot.clock = lambda: IN_WIN
    bot.run(DAY)
    hb = hb_of(tmp_path)
    assert hb["live"] is False and hb["placing"] is False


# ---- #2 re-check right before the POST
def _hook_after_attempt_log(bot, action):
    orig = bot.log

    def log(event, line=None, **kw):
        orig(event, line, **kw)
        if event == "order_attempt":
            action()
    bot.log = log


def test_clock_passing_cancel_at_between_attempt_log_and_post_does_not_post(tmp_path):
    bot, c = make_bot(tmp_path)
    _hook_after_attempt_log(bot, lambda: setattr(bot, "clock", lambda: TIP - timedelta(minutes=14)))
    T(bot, IN_WIN)
    assert c.created == []
    o = rec(bot)
    assert o["state"] == "failed" and ob.order_cost(o) == 0
    ev = [r for r in logs(tmp_path) if r["event"] == "order_state" and r["state"] == "failed"]
    assert ev and ev[0]["reason"] == "late/stop before send"


def test_stop_file_between_attempt_log_and_post_does_not_post(tmp_path):
    bot, c = make_bot(tmp_path)
    _hook_after_attempt_log(bot, lambda: (tmp_path / "STOP").write_text("x"))
    T(bot, IN_WIN)
    assert c.created == [] and rec(bot)["state"] == "failed"


def test_end_date_between_attempt_log_and_post_does_not_post(tmp_path):
    bot, c = make_bot(tmp_path)
    _hook_after_attempt_log(bot, lambda: setattr(bot, "clock", lambda: ob.HARD_END + timedelta(minutes=1)))
    T(bot, IN_WIN)
    assert c.created == []


# ---- #4 fills only go up
def _open_order(tmp_path):
    bot, c = make_bot(tmp_path)
    c.rest_mode = True
    T(bot, IN_WIN)
    return bot, c, rec(bot)


def test_apply_fills_never_decrease_and_filled_state_sticks(tmp_path):
    bot, c, o = _open_order(tmp_path)

    def mk(st, f):
        return {"state": st, "filled": Decimal(f), "remaining": Decimal(0), "fees": Decimal(0),
                "avg_cost": None, "api_status": "x"}
    bot._apply(o, mk("canceled", "0.40"), "t")
    assert o["filled"] == Decimal("0.40") and o["state"] == "canceled"
    bot._apply(o, mk("canceled", "0"), "t")                              # a sparse / stale view
    assert o["filled"] == Decimal("0.40")
    bot._apply(o, mk("filled", "1.00"), "t")
    bot._apply(o, mk("canceled", "0"), "t")
    assert o["state"] == "filled" and o["filled"] == Decimal("1.00")


def test_apply_with_missing_fill_fields_executed_means_done(tmp_path):
    p = ob.parse_order_obj({"status": "executed"})                        # no fill fields at all
    assert p["state"] == "filled" and p["filled"] == 0
    bot, c, o = _open_order(tmp_path)
    bot._apply(o, p, "t")
    assert o["state"] == "filled" and ob.game_filled([o]) == Decimal(1)
    assert any("filled" in v for v in caps([o], game=G1))               # caps count it as 1 contract
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 1 and bot.games[(DAY.isoformat(), G1)]["closed"]
    assert ob.parse_order_obj({"status": "canceled"})["state"] == "canceled"    # canceled with no fill fields


# ---- #5 resting IOC
def test_create_response_with_remaining_is_open_counted_and_cancelled(tmp_path):
    bot, c = make_bot(tmp_path)
    c.rest_mode = True
    T(bot, IN_WIN)
    o = rec(bot)
    assert o["state"] == "open" and ob.order_cost(o) > Decimal("0.47")
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 1                                          # no second order on top of an open one
    T(bot, TIP - timedelta(minutes=15))
    assert c.cancelled == [("oid1", 3)]


def test_create_response_with_remaining_while_both_lookups_fail_stays_open(tmp_path):
    bot, c = make_bot(tmp_path)
    c.rest_mode = True
    c.order_raises = RuntimeError("single GET down")
    orig_orders, orig_create, state = c.orders, c.create_order, {"after_create": False}

    def orders(**kw):
        if state["after_create"]:
            raise RuntimeError("listing down")
        return orig_orders(**kw)

    def create(body):
        r = orig_create(body)
        state["after_create"] = True
        return r
    c.orders, c.create_order = orders, create
    T(bot, IN_WIN)
    o = rec(bot)
    assert o["state"] == "open" and o["order_id"] == "oid1"             # response said remaining > 0, nothing contradicts
    assert ob.order_cost(o) > Decimal("0.47")
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 1
    T(bot, TIP - timedelta(minutes=15))
    assert c.cancelled == [("oid1", 3)]                                  # cancel by id still attempted


# ---- #7 thin ask, partial fills, top-up
def test_thin_ask_skips_then_sends_when_size_is_there(tmp_path):
    bot, c = make_bot(tmp_path)
    c.size = Decimal("0.5")
    T(bot, IN_WIN)
    assert c.created == [] and any("thin ask" in ln for ln in bot.lines)
    c.size = Decimal(1)
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 1


def test_partial_fill_is_not_done_and_tops_up_the_remainder(tmp_path):
    bot, c = make_bot(tmp_path)
    c.partial = "0.40"
    T(bot, IN_WIN)
    assert rec(bot)["filled"] == Decimal("0.40") and rec(bot)["state"] == "canceled"
    assert not bot.games[(DAY.isoformat(), G1)]["closed"]
    c.partial = None
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 2 and c.created[1]["count"] == "0.60"
    assert c.created[1]["client_order_id"].endswith("-a2")
    assert rec(bot, n=2)["state"] == "filled"
    assert ob.game_filled(bot._game_orders(DAY.isoformat(), G1)) == Decimal("1.00")
    T(bot, IN_WIN + timedelta(minutes=2))
    assert len(c.created) == 2 and bot.games[(DAY.isoformat(), G1)]["closed"]


def test_partial_fills_respect_four_attempts_and_never_exceed_one_contract(tmp_path):
    bot, c = make_bot(tmp_path)
    c.partial = "0.10"
    for k in range(10):
        T(bot, IN_WIN + timedelta(minutes=k))
    assert len(c.created) == 4
    assert ob.game_filled(bot._game_orders(DAY.isoformat(), G1)) <= 1
    assert caps([order("G9", 50, "canceled", Decimal("0.5"))], count=Decimal("0.5")) == []
    assert any("filled" in v for v in caps([order("G9", 50, "canceled", Decimal("0.6"))], count=Decimal("0.5")))
    assert ob.order_cost(order("A", 50, "canceled", Decimal("0.4"))) == Decimal("0.4") * Decimal("0.5") + \
        ob.est_fee(50, Decimal("0.4"))


def test_restart_restores_partial_fill_and_tops_up(tmp_path):
    bot, c = make_bot(tmp_path)
    c.partial = "0.40"
    T(bot, IN_WIN)
    c.partial = None
    bot2, _ = make_bot(tmp_path, client=c)
    bot2.reconcile_from_log()
    T(bot2, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 2 and c.created[1]["count"] == "0.60"


# ---- #6 truncated notified.json
def test_truncated_notified_file_at_fallback_time_arms_nothing_then_recovers(tmp_path):
    bot, c = make_bot(tmp_path, pings={})
    (tmp_path / "pings.json").write_text('{"2026-10-25|26OCT25AAA')       # half-written
    T(bot, TIP - timedelta(minutes=50))
    T(bot, TIP - timedelta(minutes=49))
    assert c.created == [] and bot.seen["row_calls"] == 0
    assert bot.games == {} and any("unreadable" in ln for ln in bot.lines)
    set_ping(tmp_path, dict([ping(limit_c=50)]))                         # file completes with the ping
    T(bot, TIP - timedelta(minutes=48))
    assert len(c.created) == 1 and rec(bot)["max_c"] == 50 and bot.seen["row_calls"] == 0


def test_notified_file_absent_or_without_the_key_falls_back(tmp_path):
    bot, c = make_bot(tmp_path, pings={})
    (tmp_path / "pings.json").unlink()
    T(bot, TIP - timedelta(minutes=50))
    assert len(c.created) == 1
    bot, c = make_bot(tmp_path, pings={ping(G2)[0]: ping(G2)[1]})
    T(bot, TIP - timedelta(minutes=50))
    assert len(c.created) == 1 and bot.seen["row_calls"] == 1
    bot, c = make_bot(tmp_path, pings={})
    (tmp_path / "pings.json").write_text('["not", "a", "dict"]')
    T(bot, TIP - timedelta(minutes=50))
    assert c.created == []


# ---- #8 order lookup order, 403/404 never halts
def test_listing_is_used_before_single_order_get(tmp_path):
    bot, c, o = _open_order(tmp_path)
    c.order_raises = AssertionError("single GET must not be needed")
    c.book["oid1"].update(status="executed", fill_count_fp="1.00", remaining_count_fp="0.00")
    T(bot, IN_WIN + timedelta(minutes=1))
    assert o["state"] == "filled"


def test_single_order_get_403_and_404_do_not_halt(tmp_path):
    for exc in (Forbidden("403"), KeyError("404")):
        bot, c = make_bot(tmp_path)
        c.rest_mode = True
        c.store_on_create = False                                        # the listing never shows it
        T(bot, IN_WIN)
        assert rec(bot)["state"] == "open"
        c.order_raises = exc
        for k in range(3):
            T(bot, IN_WIN + timedelta(minutes=1 + k))
        assert bot.halt_reason is None
        assert any(r["event"] == "error" and "single GET" in r["where"] for r in logs(tmp_path))
        assert len(c.created) == 1


def test_real_auth_error_on_listing_positions_or_create_still_halts(tmp_path):
    bot, c = make_bot(tmp_path)
    c.rest_mode = True
    T(bot, IN_WIN)
    c.orders_raises = AuthErr("401")
    bot.clock = lambda: IN_WIN + timedelta(minutes=1)
    bot.run(DAY)
    assert bot.halt_reason
    bot, c = make_bot(tmp_path)

    def bad(**kw):
        raise AuthErr("401")
    c.positions = bad
    T(bot, IN_WIN)
    assert bot.halt_reason and c.created == []


# ---- #9 fallback errors do not stop armed games
def test_compute_rows_raising_in_fallback_does_not_stop_an_armed_game(tmp_path):
    g2 = one_game(G2, TIP + timedelta(minutes=10))
    base = make_bot(tmp_path, games=[one_game(), g2], rows=[mk_row(), mk_row(G2)])[0]._compute

    def flaky(day, now, hdr):
        if hdr is not None and hdr.__name__ == "only_wanted":
            raise RuntimeError("books endpoint down")
        return base(day, now, hdr)
    bot, c = make_bot(tmp_path, games=[one_game(), g2], rows=[mk_row(), mk_row(G2)], compute=flaky,
                      quotes={TK1: (46, 49), tk(G2): (45, 47)})
    T(bot, IN_WIN)                                                       # G1 armed from its ping, ask too high
    assert c.created == []
    c.quotes[TK1] = (45, 47)
    T(bot, TIP - timedelta(minutes=40))                                  # G2's fallback raises; G1 must still go
    assert len(c.created) == 1 and c.created[0]["ticker"] == TK1
    assert any(r["event"] == "error" and "fallback compute_rows" in r["where"] for r in logs(tmp_path))
    assert (DAY.isoformat(), G2) not in bot.games                        # retried next loop


# ---- #10 cash
def test_low_cash_skips_and_cash_error_fails_closed(tmp_path):
    bot, c = make_bot(tmp_path)
    need = Decimal("0.47") + ob.est_fee(47) + ob.CASH_BUFFER
    c.cash = need - Decimal("0.0001")
    T(bot, IN_WIN)
    assert c.created == [] and any("low cash" in ln for ln in bot.lines)
    c.cash = need
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 1
    bot, c = make_bot(tmp_path)
    c.cash_raises = RuntimeError("balance down")
    T(bot, IN_WIN)
    assert c.created == []
    bot, c = make_bot(tmp_path)
    c.cash_raises = Forbidden("403")                                     # auth error on the cash read halts
    T(bot, IN_WIN)
    assert c.created == [] and bot.halt_reason


# ---- #11 tip moves
def test_tip_moved_earlier_recomputes_cancel_and_closes_when_past(tmp_path):
    tips = {"t": TIP}

    def compute(day, now, hdr):
        g = [(tips["t"], G1, [{"ticker": TK1, "exchange_index": 3}])]
        if hdr:
            hdr(0, g)
        return [mk_row()], 0, g, None
    bot, c = make_bot(tmp_path, compute=compute, quotes={TK1: (46, 49)})
    T(bot, IN_WIN)                                                        # armed, ask too high
    gm = bot.games[(DAY.isoformat(), G1)]
    assert gm["cancel_at"] == TIP - timedelta(minutes=15)
    tips["t"] = TIP - timedelta(minutes=20)                               # moved 20 min earlier -> cancel at TIP-35
    c.quotes[TK1] = (45, 47)
    T(bot, IN_WIN + timedelta(minutes=31))                                # reload due; now = TIP-29 >= new cancel_at
    assert gm["tip"] == TIP - timedelta(minutes=20) and gm["cancel_at"] == TIP - timedelta(minutes=35)
    assert gm["closed"] and c.created == []


def test_tip_moved_earlier_but_still_before_new_cancel_keeps_game_and_shortens_orders(tmp_path):
    tips = {"t": TIP}

    def compute(day, now, hdr):
        g = [(tips["t"], G1, [{"ticker": TK1, "exchange_index": 3}])]
        if hdr:
            hdr(0, g)
        return [mk_row()], 0, g, None
    bot, c = make_bot(tmp_path, compute=compute)
    c.rest_mode = True
    T(bot, TIP - timedelta(minutes=70))                                   # open order outstanding
    tips["t"] = TIP - timedelta(minutes=5)
    T(bot, TIP - timedelta(minutes=70) + timedelta(minutes=31))           # now = TIP-39; new cancel = TIP-20
    assert rec(bot)["cancel_at"] == TIP - timedelta(minutes=20) and bot.games[(DAY.isoformat(), G1)]["closed"] is None
    T(bot, TIP - timedelta(minutes=20))
    assert c.cancelled == [("oid1", 3)]


# ---- #12 exposure listing where the API ignores event_ticker
def test_exposure_listing_ignoring_event_ticker_is_filtered_locally(tmp_path):
    bot, c = make_bot(tmp_path)
    c.ignore_event_filter = True
    c.book["other"] = {"order_id": "other", "ticker": "KXNBATOTAL-26OCT25ZZZYYY-200", "status": "resting",
                       "client_order_id": "manual"}
    c.pos = [{"ticker": "KXNBATOTAL-26OCT25ZZZYYY-200", "position_fp": "3.00"}]
    T(bot, IN_WIN)
    assert len(c.created) == 1                                           # other games' orders/positions don't block
    bot, c = make_bot(tmp_path)
    c.ignore_event_filter = True
    c.book["mine"] = {"order_id": "mine", "ticker": f"KXNBATOTAL-{G1}-230", "status": "resting",
                      "client_order_id": "manual"}
    T(bot, IN_WIN)
    assert c.created == []                                               # same event still blocks
    bot, c = make_bot(tmp_path)
    c.ignore_event_filter = True
    c.book["done"] = {"order_id": "done", "ticker": f"KXNBATOTAL-{G1}-230", "status": "canceled",
                      "client_order_id": "manual"}
    T(bot, IN_WIN)
    assert len(c.created) == 1                                           # a non-resting order is not exposure


# ---- fake realism: NO buy fill price, fee fields
def test_no_buy_average_fill_price_is_yes_price_and_fee_is_read(tmp_path):
    bot, c = make_bot(tmp_path, pings=dict([ping(side="UNDER", limit_c=55)]), quotes={TK1: (45, 47)})
    T(bot, IN_WIN)
    fill = [r for r in logs(tmp_path) if r["event"] == "fill"][0]
    assert fill["avg_cost"] == "0.5500"                                  # 1 - YES price 0.45
    assert Decimal(fill["fees"]) == Decimal("0.0044")
    sent = [r for r in logs(tmp_path) if r["event"] == "order_sent"][0]
    assert sent["data"]["average_fill_price"] == "0.45"                  # the exchange reports the YES price


# ---- 409 after a lost log (coid reuse)
def test_409_after_lost_log_resolves_to_the_existing_order(tmp_path):
    bot, c = make_bot(tmp_path)
    coid = f"eob-2026-10-25-{G1}-a1"
    c.book["old1"] = {"order_id": "old1", "ticker": TK1, "status": "executed", "fill_count_fp": "1.00",
                      "remaining_count_fp": "0.00", "client_order_id": coid, "taker_fees_dollars": "0.0044",
                      "maker_fees_dollars": "0", "taker_fill_cost_dollars": "0.47", "maker_fill_cost_dollars": "0"}
    T(bot, IN_WIN)                                                       # fresh process, no log: sends a1 again
    assert len(c.created) == 1
    o = rec(bot)
    assert o["order_id"] == "old1" and o["state"] == "filled"
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 1                                           # no further order


# ---- restart carrying unknown orders from a previous night
def test_restart_with_unknown_order_from_previous_night(tmp_path):
    prev = DAY - timedelta(days=1)
    ptip = TIP - timedelta(days=1)
    c = FakeClient()
    pg = "26OCT24AAABBB"
    bot1, _ = make_bot(tmp_path, client=c, games=[one_game(pg, ptip)], rows=[mk_row(pg)],
                       pings=dict([ping(pg, prev)]), quotes={tk(pg): (45, 47)})
    c.store_on_create = False
    c.create_result = Result("unknown", None, None, {"code": "transport_error"})
    T(bot1, ptip - timedelta(minutes=60), day=prev)
    assert bot1.orders[(prev.isoformat(), pg, 1)]["state"] == "unknown"
    c.create_result, c.store_on_create = None, True
    bot2, _ = make_bot(tmp_path, client=c)                               # restarted the next night
    bot2.reconcile_from_log()
    old = bot2.orders[(prev.isoformat(), pg, 1)]
    assert old["state"] == "unknown" and old["order_id"] is None
    T(bot2, IN_WIN)                                                      # tonight still places normally
    assert len(c.created) == 2 and rec(bot2)["state"] == "filled"
    assert bot2._tonight(DAY.isoformat()) == [rec(bot2)]                 # last night's order is not tonight's spend
    for k in range(4):                                                   # and it is chased down (not_found) eventually
        T(bot2, TIP + timedelta(hours=2, minutes=k))
    assert old["state"] == "not_found" and bot2.halt_reason is None


# ---- multi-day run across the DST change with real ET tip times
def test_multi_day_run_across_dst_change_oct31_to_nov2(tmp_path):
    days = [date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2)]
    keys = {days[0]: "26OCT31AAABBB", days[1]: "26NOV01AAABBB", days[2]: "26NOV02AAABBB"}
    tips = {d: datetime(d.year, d.month, d.day, 19, 0, tzinfo=ET) for d in days}

    def compute(day, now, on_header):
        key = keys[day]
        games = [(tips[day], key, [{"ticker": tk(key), "exchange_index": 3}])]
        if on_header:
            on_header(0, games)
        return [mk_row(key)], 0, games, None
    c = FakeClient()
    pings = dict(ping(keys[d], d) for d in days)
    quotes = {tk(keys[d]): (45, 47) for d in days}
    bot, _ = make_bot(tmp_path, client=c, compute=compute, until=days[-1], pings=pings, quotes=quotes)
    sim = {"t": ob.day_start(days[0])}
    bot.clock = lambda: sim["t"]
    bot._sleep = lambda s: sim.__setitem__("t", sim["t"] + timedelta(seconds=s))
    bot.run(days[0])
    assert len(c.created) == 3 and c.cancelled == []
    assert [b["client_order_id"] for b in c.created] == [f"eob-{d.isoformat()}-{keys[d]}-a1" for d in days]
    cancel_utc = {o["day"]: o["cancel_at"].astimezone(timezone.utc) for o in bot.orders.values()}
    assert cancel_utc["2026-10-31"].hour == 22 and cancel_utc["2026-10-31"].minute == 45     # 18:45 EDT
    assert cancel_utc["2026-11-01"].hour == 23 and cancel_utc["2026-11-01"].minute == 45     # 18:45 EST
    assert cancel_utc["2026-11-02"].hour == 23 and cancel_utc["2026-11-02"].minute == 45
    ev = [r["event"] for r in logs(tmp_path)]
    assert ev.count("day_done") == 3 and ev[-1] == "exit"


# ====================================================================== round 3: N1 (own fills) and N3 (placing)
def test_partial_fill_then_top_up_succeeds_with_the_bots_own_position_visible(tmp_path):
    bot, c = make_bot(tmp_path)
    c.partial = "0.40"
    T(bot, IN_WIN)
    assert c.positions(event_ticker=f"KXNBATOTAL-{G1}") == [{"ticker": TK1, "position_fp": "0.40"}]
    c.partial = None
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 2 and c.created[1]["count"] == "0.60"       # not closed as "placed by hand"
    assert rec(bot, n=2)["state"] == "filled"
    assert c.positions(event_ticker=f"KXNBATOTAL-{G1}") == [{"ticker": TK1, "position_fp": "1.00"}]


def test_top_up_for_under_uses_negative_own_position(tmp_path):
    bot, c = make_bot(tmp_path, pings=dict([ping(side="UNDER", limit_c=55)]))
    c.partial = "0.40"
    T(bot, IN_WIN)
    assert c.positions(event_ticker=f"KXNBATOTAL-{G1}")[0]["position_fp"] == "-0.40"
    c.partial = None
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 2


def test_lagging_position_view_does_not_block_a_top_up(tmp_path):
    bot, c = make_bot(tmp_path)
    c.partial = "0.40"
    T(bot, IN_WIN)
    c.own_pos.clear()                                                    # API has not shown our fill yet
    c.partial = None
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 2


def test_manual_position_same_ticker_on_top_of_bot_fill_is_detected(tmp_path):
    bot, c = make_bot(tmp_path)
    c.partial = "0.40"
    T(bot, IN_WIN)
    c.partial = None
    c.pos = [{"ticker": TK1, "position_fp": "1.00"}]                     # user also bought 1 by hand: API shows 1.40
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 1 and bot.games[(DAY.isoformat(), G1)]["closed"]
    assert any("already exposed" in ln for ln in bot.lines)


def test_manual_position_on_other_strike_detected_after_bot_fill(tmp_path):
    bot, c = make_bot(tmp_path)
    c.partial = "0.40"
    T(bot, IN_WIN)
    c.partial = None
    c.pos = [{"ticker": f"KXNBATOTAL-{G1}-230", "position_fp": "1.00"}]
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 1 and bot.games[(DAY.isoformat(), G1)]["closed"]


def test_opposite_side_position_on_same_ticker_is_detected(tmp_path):
    bot, c = make_bot(tmp_path)
    c.partial = "0.40"
    T(bot, IN_WIN)
    c.partial = None
    c.own_pos.clear()
    c.pos = [{"ticker": TK1, "position_fp": "-2.00"}]                    # NO position while we hold YES
    T(bot, IN_WIN + timedelta(minutes=1))
    assert len(c.created) == 1


def test_manual_position_before_first_attempt_still_blocks(tmp_path):
    bot, c = make_bot(tmp_path)
    c.pos = [{"ticker": TK1, "position_fp": "0.50"}]
    T(bot, IN_WIN)
    assert c.created == []


def test_placing_false_when_last_cash_read_is_below_max_order(tmp_path):
    bot, c = make_bot(tmp_path)
    c.cash = Decimal("0.90")                                              # enough for a 47c order, not for 85c+fee+0.10
    T(bot, IN_WIN)
    assert len(c.created) == 1
    assert hb_of(tmp_path)["placing"] is False
    bot, c = make_bot(tmp_path)
    T(bot, IN_WIN)
    assert hb_of(tmp_path)["placing"] is True


def test_placing_false_after_three_consecutive_read_failures_and_recovers(tmp_path):
    bot, c = make_bot(tmp_path)
    c.cash_raises = RuntimeError("balance down")
    for k in range(2):
        T(bot, IN_WIN + timedelta(minutes=k))
    assert hb_of(tmp_path)["placing"] is True
    T(bot, IN_WIN + timedelta(minutes=2))
    assert hb_of(tmp_path)["placing"] is False and c.created == []
    c.cash_raises = None
    T(bot, IN_WIN + timedelta(minutes=3))
    assert hb_of(tmp_path)["placing"] is True                             # a good read resets the counter
    bot, c = make_bot(tmp_path)

    def bad(**kw):
        raise RuntimeError("down")
    c.positions = bad
    for k in range(3):
        T(bot, IN_WIN + timedelta(minutes=k))
    assert hb_of(tmp_path)["placing"] is False                            # exposure read failures count too


def test_heartbeat_written_at_end_of_tick_reflects_cap_exhaustion_mid_tick(tmp_path):
    bot, c = make_bot(tmp_path)
    for i in range(7):
        bot.orders[(DAY.isoformat(), f"F{i}", 1)] = {**order(f"F{i}", 85, "filled", 1), "day": DAY.isoformat(), "n": 1}
    bot.orders[(DAY.isoformat(), "FX", 1)] = {**order("FX", 35, "filled", 1), "day": DAY.isoformat(), "n": 1}
    assert bot.placing_now(DAY, IN_WIN) is True                           # room for one more minimum order
    T(bot, IN_WIN)                                                        # this tick spends ~$0.49
    assert len(c.created) == 1
    assert hb_of(tmp_path)["placing"] is False                            # same tick's closing heartbeat says so
