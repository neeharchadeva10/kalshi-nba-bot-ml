"""late_favorite: the rule, the order shape, the window loop, paper twins, live safety and settlement P&L.

Everything is offline: a fake clock whose ``sleep`` advances time, a fake exchange client, a fake Safety and a fake
reconcile namespace. The REAL BotState and Ledger run on in-memory SQLite, so the rows the dashboard reads and the
numbers the loss cap uses are the production code paths. The autouse guards in conftest.py block the network.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest

from kpm.execution import late_favorite as lf
from kpm.execution import units
from kpm.execution.botstate import BotState
from kpm.execution.client import Result, validate_order_body
from kpm.execution.ledger import Ledger

from .botfakes import (TAPERED, Clock, FakeExchange, FakeReconcile, FakeSafety, RecState, book, iso, lv, micro_fee,
                       real_stack, seed_loss, seed_trade, worse, worst_of)

CLOSE0 = 1_791_226_800                     # 2026-10-05T19:00:00Z, a KXBTC15M close

DEAD = book("0.5000", "0.4900")             # mid 0.505: skip
FAV_YES = book("0.9400", "0.0500")          # YES ask 0.95, mid 0.945: buy YES at 0.95 (size 900)
FAV_NO = book("0.0400", "0.9500")           # NO ask 0.96, YES ask 0.05, mid 0.045: buy NO at 0.96 (size 800)
NO_QUOTE = ([], [])                         # an empty book: no mid, so nothing to decide on (the window is polled on)


def make_market(close: float, *, status: str = "active", ei: int | None = 2, result: str = "") -> dict:
    m = {"ticker": f"KXBTC15M-{int(close)}", "event_ticker": f"KXBTC15M-E{int(close)}", "status": status,
         "close_time": iso(close), "open_time": iso(close - 900), "floor_strike": 85432.83,
         "strike_type": "greater_or_equal", "price_ranges": TAPERED, "result": result, "market_type": "binary",
         "can_close_early": True}
    if ei is not None:
        m["exchange_index"] = ei
    return m


class FakeClient(FakeExchange):
    """The KXBTC15M side of the fake exchange: one open market at a time, order books scripted by elapsed time."""

    def __init__(self, clock: Clock, books, *, results=None, live: bool = False, latency: float = 0.0):
        super().__init__(clock, results=results, live=live, latency=latency)
        self.books = books                   # f(ticker, elapsed_s) -> (yes, no) | dict | None
        self.unlisted: set[str] = set()
        self.listing = None                  # f(now) -> list of market dicts, replaces the default one-market listing

    def default_market(self, ticker: str) -> dict:
        return make_market(int(ticker.rsplit("-", 1)[1]))

    def open_markets(self, series):                # like ExecClient.open_markets: no close-time filters (OpenAPI GetMarkets)
        self._tick("open_markets", series)
        if self.listing is not None:
            out = self.listing(self.clock.now)
        else:
            base = (int(self.clock.now // 900) + 1) * 900
            out = [make_market(base)]
        for m in out:
            self.known[m["ticker"]] = m
        return [m for m in out if m["ticker"] not in self.unlisted]

    def orderbook(self, ticker):
        self._tick("orderbook", ticker)
        got = self.books(ticker, self.clock.now - (self.close_of(ticker) - 900))
        if isinstance(got, dict):
            return got
        yes, no = got
        return {"ticker": ticker, "yes": yes, "no": no, "fetched_at": self.clock.now}


# ============================================================================================ the simulation
class Sim:
    def __init__(self, tmp_path: Path, *, live: bool = False, books=None, results=None, start_offset: float = -400,
                 lf_cfg: dict | None = None, trading: dict | None = None, latency: float = 0.0,
                 enabled: bool = True, with_safety: bool = True, conn: sqlite3.Connection | None = None,
                 run_id: str = "run-test"):
        self.tmp_path = tmp_path
        self.clock = Clock(CLOSE0 + start_offset)
        self.conn = conn if conn is not None else sqlite3.connect(":memory:")      # a second run may share the database
        self.state = RecState(self.conn, run_id, clock=self.clock)
        self.client = FakeClient(self.clock, books or (lambda t, e: FAV_YES if e >= 700 else DEAD),
                                 results=results, live=live, latency=latency)
        self.ledger = Ledger(self.conn, run_id, "prod", clock=self.clock, log_path=tmp_path / "exec.jsonl") \
            if live else None
        self.client.ledger = self.ledger
        self.kill_file = tmp_path / "KILL"
        self.safety = FakeSafety(self.kill_file) if (live and with_safety) else None
        if self.safety:
            self.safety.clock = self.clock
        self.reconcile = FakeReconcile(self.clock)
        t = {"enabled": enabled, "max_loss_usd": 5.0, "max_cost_per_order_usd": 1.0, "max_contracts_per_order": 1,
             "max_orders_per_minute": 10, "kill_switch_file": str(self.kill_file), "late_favorite": dict(lf_cfg or {})}
        t.update(trading or {})
        self.cfg = {"trading": t}
        self.ctx = lf.LateContext(client=self.client, state=self.state, ledger=self.ledger, safety=self.safety,
                                  reconcile=self.reconcile, env_name="prod", key_fp="abcd1234")
        self.live = live
        self._bot = None

    @property
    def bot(self) -> lf.LateFavoriteBot:
        if self._bot is None:
            self._bot = lf.LateFavoriteBot(self.ctx, self.cfg, live=self.live, clock=self.clock, sleep=self.clock.sleep,
                                           kill_file=self.kill_file)
        return self._bot

    def run(self, hours: float | None = 0.15, smoke: bool = False) -> dict:
        return self.bot.run(hours=hours, smoke=smoke)

    # ---- readers
    def opps(self) -> list[dict]:
        return self.state.opportunity_rows()

    def orders(self) -> list[dict]:
        return self.state.order_rows()

    def events(self, kind: str | None = None) -> list[dict]:
        return [e for e in self.state.event_rows() if kind is None or e["kind"] == kind]

    def max_beat_gap(self) -> float:
        ts = [t for t, _ in self.state.beats]
        return max(b - a for a, b in zip(ts, ts[1:]))


# ============================================================================================ the rule: decide()
CLOSE = 10_000.0                        # arbitrary close for the pure tests


def b(yes_bid: str, no_bid: str, ys: str = "800", ns: str = "900", fetched: float | None = None) -> dict:
    yes, no = book(yes_bid, no_bid, ys, ns)
    out = {"yes": yes, "no": no}
    if fetched is not None:
        out["fetched_at"] = fetched
    return out


def test_rule_constants_are_the_users_decision():
    assert (lf.DECISION_START_S, lf.DECISION_END_S, lf.WINDOW_LENGTH_S) == (780.0, 840.0, 900.0)
    assert (lf.YES_ABOVE, lf.NO_BELOW) == (D("0.60"), D("0.40"))
    assert lf.STRATEGY == "late_favorite" and lf.SERIES == "KXBTC15M"
    # 780-840 s into the window is 120-60 s before the close
    assert (lf.OPENS_BEFORE_CLOSE_S, lf.ENDS_BEFORE_CLOSE_S) == (120.0, 60.0)
    assert (lf.CONTRACTS_CEILING, lf.ATTEMPTS_CEILING, lf.MAX_LOSS_CEILING, lf.COST_PER_ORDER_CEILING) == \
           (D("1"), 3, D("5.00"), D("1.00"))


def test_decide_buys_yes_when_mid_is_above_060():
    d = lf.decide(b("0.9400", "0.0500"), CLOSE - 100, CLOSE)
    assert d == {"outcome": "yes", "ask": D("0.95"), "size": D("900"), "mid": D("0.945")}
    assert set(d) == {"outcome", "ask", "size", "mid"}
    assert all(isinstance(d[k], D) for k in ("ask", "size", "mid"))


def test_decide_buys_no_when_mid_is_below_040_at_the_no_ask():
    d = lf.decide(b("0.0400", "0.9500"), CLOSE - 100, CLOSE)
    # NO ask = 1 - best YES bid = 0.96; its size is the YES bid's size
    assert d == {"outcome": "no", "ask": D("0.96"), "size": D("800"), "mid": D("0.045")}


@pytest.mark.parametrize("yes_bid,no_bid,expected", [
    ("0.6000", "0.3900", ("yes", "0.61")),       # ask 0.61, mid 0.605
    ("0.5900", "0.3900", None),                   # mid exactly 0.60: not strictly above
    ("0.5900", "0.3800", ("yes", "0.62")),       # ask 0.62, mid 0.605
    ("0.4000", "0.5900", None),                   # ask 0.41, mid 0.405
    ("0.3900", "0.5900", None),                   # ask 0.41, mid exactly 0.40: not strictly below
    ("0.3800", "0.5900", ("no", "0.62")),        # mid 0.395: NO at 1 - 0.38
    ("0.5000", "0.4900", None),
    ("0.9990", "0.0005", ("yes", "0.9995")),     # sub-cent grid
])
def test_decide_thresholds_are_strict(yes_bid, no_bid, expected):
    d = lf.decide(b(yes_bid, no_bid), CLOSE - 90, CLOSE)
    if expected is None:
        assert d is None
    else:
        assert (d["outcome"], d["ask"]) == (expected[0], D(expected[1]))


def test_decide_matches_an_independent_oracle_on_a_grid():
    def oracle(yb: D, nb: D):
        if yb + nb >= 1:
            return None
        ya = 1 - nb
        mid = (yb + ya) / 2
        if mid > D("0.6"):
            return ("yes", ya, D("900"))
        if mid < D("0.4"):
            return ("no", 1 - yb, D("800"))
        return None

    n = 0
    for i in range(1, 99):
        for j in range(1, 100 - i):
            yb, nb = D(i) / 100, D(j) / 100
            got = lf.decide(b(f"{yb:.4f}", f"{nb:.4f}"), CLOSE - 100, CLOSE)
            want = oracle(yb, nb)
            assert (None if got is None else (got["outcome"], got["ask"], got["size"])) == want, (yb, nb)
            n += 1
    assert n > 4000


@pytest.mark.parametrize("now_offset,inside", [
    (-121.0, False), (-120.001, False), (-120.0, True), (-90.0, True), (-60.0, True), (-59.999, False), (-1.0, False),
    (-899.0, False), (-300.0, False)])
def test_decide_only_inside_780_to_840_seconds(now_offset, inside):
    d = lf.decide(b("0.9400", "0.0500"), CLOSE + now_offset, CLOSE)
    assert (d is not None) is inside
    assert lf.in_decision_window(CLOSE + now_offset, CLOSE) is inside
    elapsed = lf.elapsed_in_window(CLOSE + now_offset, CLOSE)
    assert elapsed == pytest.approx(900 + now_offset)


def test_decide_reads_the_last_level_as_the_best_bid():
    # first levels are far worse than the last ones; a bot that read [0] would see yes bid 0.01 / no bid 0.01
    book_ = {"yes": lv(("0.0100", "1"), ("0.5000", "2"), ("0.9400", "11")), "no": lv(("0.0100", "1"), ("0.0300", "4"), ("0.0500", "13"))}
    d = lf.decide(book_, CLOSE - 100, CLOSE)
    assert d["outcome"] == "yes" and d["ask"] == D("0.95") and d["size"] == D("13")


def test_decide_accepts_rest_string_levels():
    book_ = {"yes": [["0.0100", "5.00"], ["0.9400", "10.00"]], "no": [["0.0200", "5.00"], ["0.0500", "20.50"]]}
    assert lf.decide(book_, CLOSE - 100, CLOSE) == {"outcome": "yes", "ask": D("0.95"), "size": D("20.50"),
                                                    "mid": D("0.945")}


@pytest.mark.parametrize("book_", [
    {"yes": [], "no": []},
    {"yes": lv(("0.9400", "5")), "no": []},
    {"yes": [], "no": lv(("0.0500", "5"))},
    {"yes": lv(("0.9400", "5")), "no": lv(("0.0600", "5"))},       # locked: 0.94 + 0.06 = 1
    {"yes": lv(("0.9500", "5")), "no": lv(("0.0700", "5"))},       # crossed
    {},
])
def test_decide_needs_a_two_sided_uncrossed_book(book_):
    assert lf.decide(book_, CLOSE - 100, CLOSE) is None


def test_decide_refuses_a_book_that_is_not_ascending():
    bad = {"yes": lv(("0.9400", "5"), ("0.5000", "5")), "no": lv(("0.0500", "5"))}
    with pytest.raises(ValueError):
        lf.decide(bad, CLOSE - 100, CLOSE)


def test_decide_freshness_when_asked():
    fresh, stale = b("0.9400", "0.0500", fetched=CLOSE - 100.5), b("0.9400", "0.0500", fetched=CLOSE - 103)
    assert lf.decide(fresh, CLOSE - 100, CLOSE, max_book_age_s=2.0) is not None
    assert lf.decide(stale, CLOSE - 100, CLOSE, max_book_age_s=2.0) is None
    assert lf.decide(stale, CLOSE - 100, CLOSE) is not None           # no limit given: not checked
    assert lf.decide(b("0.9400", "0.0500"), CLOSE - 100, CLOSE, max_book_age_s=2.0) is None   # no fetched_at: not fresh


def test_quote_math_is_exact():
    q = lf.read_quote({"yes": lv(("0.5530", "3")), "no": lv(("0.4260", "9"))})
    assert q.yes_ask == D("0.5740") and q.no_ask == D("0.4470") and q.mid == D("0.56350")


# ============================================================================================ order spec and body
def test_entry_spec_yes_is_a_bid_at_the_ask():
    s = lf.entry_spec(make_market(CLOSE0), "yes", D("0.947"), D("1"))
    assert (s["side"], s["yes_price"], s["outcome"], s["outcome_price"]) == ("bid", D("0.947"), "yes", D("0.947"))
    body = lf.order_body(s, "cid-1")
    assert body == {"ticker": f"KXBTC15M-{CLOSE0}", "client_order_id": "cid-1", "side": "bid", "price": "0.9470",
                    "count": "1.00", "time_in_force": "immediate_or_cancel",
                    "self_trade_prevention_type": "taker_at_cross", "exchange_index": 2}
    assert validate_order_body(body)


def test_entry_spec_no_is_an_ask_at_one_minus_the_no_price():
    s = lf.entry_spec(make_market(CLOSE0), "no", D("0.054"), D("1"))
    assert (s["side"], s["yes_price"]) == ("ask", D("0.946"))
    body = lf.order_body(s, "cid-2")
    assert body["side"] == "ask" and body["price"] == "0.9460" and body["count"] == "1.00"
    # the wrong-side trap: 'ask' at 0.0540 would buy NO at 0.946 (17x the collateral)
    assert (side := units.to_book("no", D("0.054"))) == ("ask", D("0.946")) and side == (s["side"], s["yes_price"])
    assert units.collateral(s["side"], s["yes_price"], s["count"]) == D("0.054")


def test_order_body_is_an_ioc_taker_without_resting_fields():
    body = lf.order_body(lf.entry_spec(make_market(CLOSE0), "yes", D("0.95"), D("1")), "c")
    assert body["time_in_force"] == "immediate_or_cancel"
    for forbidden in ("post_only", "expiration_time", "reduce_only", "cancel_order_on_pause", "action", "type", "yes_price"):
        assert forbidden not in body


def test_entry_spec_needs_the_markets_shard():
    with pytest.raises(ValueError, match="exchange_index"):
        lf.entry_spec(make_market(CLOSE0, ei=None), "yes", D("0.95"), D("1"))


# ============================================================================================ configuration
def mk_bot(tmp_path, trading=None, live=False, **kw):
    sim = Sim(tmp_path, live=live, trading=trading, **kw)
    return sim.bot


@pytest.mark.parametrize("trading,needle", [
    ({"late_favorite": {"contracts": 2}}, "contracts"),
    ({"late_favorite": {"contracts": "1.005"}}, "contracts"),
    ({"late_favorite": {"contracts": 0}}, "contracts"),
    ({"late_favorite": {"max_attempts_per_window": 4}}, "max_attempts_per_window"),
    ({"late_favorite": {"max_attempts_per_window": 0}}, "max_attempts_per_window"),
    ({"late_favorite": {"poll_interval_s": 0.05}}, "poll_interval_s"),
    ({"late_favorite": {"max_book_age_s": 30}}, "max_book_age_s"),
    ({"max_loss_usd": 5.01}, "max_loss_usd"),
    ({"max_loss_usd": 0}, "max_loss_usd"),
    ({"max_cost_per_order_usd": 1.5}, "max_cost_per_order_usd"),
    ({"max_contracts_per_order": 2}, "max_contracts_per_order"),
])
def test_configuration_above_a_ceiling_refuses_to_start(tmp_path, trading, needle):
    with pytest.raises(ValueError, match=needle):
        mk_bot(tmp_path, trading)


def test_defaults_match_the_users_decisions(tmp_path):
    bot = mk_bot(tmp_path, {"late_favorite": {}})
    assert bot.contracts == D("1") and bot.max_attempts == 3 and bot.max_loss == D("5.0")
    assert bot.poll_s == 1.0 and bot.max_book_age_s == 2.0


def test_the_short_name_max_attempts_is_accepted_and_must_agree(tmp_path):
    assert mk_bot(tmp_path, {"late_favorite": {"max_attempts": 2}}).max_attempts == 2
    assert mk_bot(tmp_path, {"late_favorite": {"max_attempts": 3, "max_attempts_per_window": 3}}).max_attempts == 3
    assert mk_bot(tmp_path, {"late_favorite": {"max_attempts": "1"}}).max_attempts == 1
    with pytest.raises(ValueError, match="disagree"):
        mk_bot(tmp_path, {"late_favorite": {"max_attempts": 2, "max_attempts_per_window": 3}})
    for bad in (4, 0, -1):
        with pytest.raises(ValueError, match="max_attempts"):
            mk_bot(tmp_path, {"late_favorite": {"max_attempts": bad}})
    with pytest.raises(ValueError, match="whole number"):
        mk_bot(tmp_path, {"late_favorite": {"max_attempts": 2.5}})


MIRROR = {"window_start_s": 780, "window_end_s": 840, "up_threshold": "0.60", "down_threshold": "0.40"}


def test_the_rule_keys_in_settings_only_repeat_the_fixed_rule(tmp_path):
    bot = mk_bot(tmp_path, {"late_favorite": dict(MIRROR)})                 # the shapes settings.yaml uses
    assert bot.max_attempts == 3
    assert mk_bot(tmp_path, {"late_favorite": {**MIRROR, "up_threshold": 0.6, "down_threshold": 0.4,
                                               "window_start_s": 780.0}}).max_attempts == 3
    assert lf.RULE_MIRROR == {"window_start_s": 780.0, "window_end_s": 840.0, "up_threshold": D("0.60"),
                              "down_threshold": D("0.40")}


@pytest.mark.parametrize("key,bad", [("window_start_s", 700), ("window_start_s", "781"), ("window_end_s", 850),
                                     ("window_end_s", 839.5), ("up_threshold", "0.65"), ("up_threshold", 0.59),
                                     ("down_threshold", "0.35"), ("down_threshold", 0.41)])
def test_a_rule_key_that_differs_from_the_fixed_rule_refuses_to_start(tmp_path, key, bad):
    with pytest.raises(ValueError, match=f"late_favorite.{key}.*fixed"):
        mk_bot(tmp_path, {"late_favorite": {**MIRROR, key: bad}})


def test_a_rule_key_that_is_not_a_number_refuses_to_start(tmp_path):
    with pytest.raises(ValueError, match="up_threshold"):
        mk_bot(tmp_path, {"late_favorite": {"up_threshold": "high"}})


def test_cfg_may_be_the_trading_block_itself(tmp_path):
    sim = Sim(tmp_path)
    bot = lf.LateFavoriteBot(sim.ctx, {"max_loss_usd": 3.5, "late_favorite": {"poll_interval_s": 0.5}}, live=False,
                             clock=sim.clock, sleep=sim.clock.sleep, kill_file=sim.kill_file)
    assert bot.max_loss == D("3.5") and bot.poll_s == 0.5


@pytest.mark.parametrize("kw,needle", [
    ({"enabled": False}, "trading.enabled"),
    ({"with_safety": False}, "Safety"),
])
def test_live_mode_gates(tmp_path, kw, needle):
    with pytest.raises(PermissionError, match=needle):
        Sim(tmp_path, live=True, **kw).bot


def test_live_needs_a_live_client_and_a_ledger_and_a_started_safety(tmp_path):
    sim = Sim(tmp_path, live=True)
    sim.client.live = False
    with pytest.raises(PermissionError, match="live=True"):
        lf.LateFavoriteBot(sim.ctx, sim.cfg, live=True, clock=sim.clock, sleep=sim.clock.sleep)
    sim.client.live = True
    sim.safety.started = False
    with pytest.raises(PermissionError, match="startup"):
        lf.LateFavoriteBot(sim.ctx, sim.cfg, live=True, clock=sim.clock, sleep=sim.clock.sleep)
    sim.safety.started = True
    sim.ctx.ledger = None
    with pytest.raises(PermissionError, match="Ledger"):
        lf.LateFavoriteBot(sim.ctx, sim.cfg, live=True, clock=sim.clock, sleep=sim.clock.sleep)


def test_a_zero_or_bad_run_length_is_refused(tmp_path):
    for bad in (0, -1, True, "2"):
        with pytest.raises(ValueError, match="hours"):
            Sim(tmp_path).bot.run(hours=bad)


# ============================================================================================ watch mode
def test_watch_mode_writes_the_paper_twin_and_settles_it(tmp_path):
    sim = Sim(tmp_path, results={f"KXBTC15M-{CLOSE0}": "yes"})
    summary = sim.run()
    run = sim.state.run_row()
    assert (run["strategy"], run["mode"], run["end_reason"]) == ("late_favorite", "watch", "finished")
    assert run["ended_ts"] is not None
    (opp,) = sim.opps()
    ticker = f"KXBTC15M-{CLOSE0}"
    assert (opp["hour_ticker"], opp["strike_ticker"], opp["kind"]) == (ticker, ticker, "late favorite YES")
    assert opp["contracts"] == 1.0 and opp["live"] == 0 and opp["outcome"] == "paper_only"
    assert opp["paper_cost"] == pytest.approx(0.95) and opp["paper_edge"] is None
    assert opp["paper_fees"] == pytest.approx(float(micro_fee("1", "0.95")))
    # detected at the first poll inside the window, not earlier
    assert opp["detected_ts"] == pytest.approx(CLOSE0 - 120)
    # the market settled YES: bought YES at 0.95 -> 1 - 0.95 - fee
    assert opp["paper_realized_pnl"] == pytest.approx(1 - 0.95 - float(micro_fee("1", "0.95")))
    assert opp["realized_pnl"] is None and opp["live_cost"] is None
    assert summary["detections"] == 1 and summary["paper_trades"] == 1 and summary["settled"] == 1
    assert summary["paper_wins"] == 1 and summary["end_reason"] == "finished"


def test_watch_mode_never_sends_anything_and_needs_no_keys(tmp_path):
    sim = Sim(tmp_path, results={f"KXBTC15M-{CLOSE0}": "yes"})
    sim.run()
    assert sim.client.names("create_order") == [] and sim.client.created == []
    assert {c[0] for c in sim.client.calls} <= {"open_markets", "market", "orderbook"}      # public reads only
    assert sim.reconcile.reconciled == [] and sim.reconcile.cancel_paths == []
    assert sim.ledger is None


def test_watch_mode_dry_run_order_row_shows_what_live_would_send(tmp_path):
    sim = Sim(tmp_path)
    sim.run()
    (o,) = sim.orders()
    assert (o["side"], o["action"], o["count"], o["tif"], o["status"], o["dry_run"]) == ("yes", "buy", 1.0, "ioc", "dry_run", 1)
    assert o["limit_price"] == pytest.approx(0.95) and o["client_order_id"].startswith("paper-")
    would = json.loads(o["response_json"])["would_send"]
    assert would["side"] == "bid" and would["price"] == "0.9500" and would["count"] == "1.00"
    assert would["time_in_force"] == "immediate_or_cancel" and would["exchange_index"] == 2
    validate_order_body(would)


def test_watch_mode_no_side_when_the_no_book_is_the_favourite(tmp_path):
    sim = Sim(tmp_path, books=lambda t, e: FAV_NO if e >= 700 else DEAD, results={f"KXBTC15M-{CLOSE0}": "no"})
    sim.run()
    (opp,) = sim.opps()
    assert opp["kind"] == "late favorite NO" and opp["paper_cost"] == pytest.approx(0.96)
    assert opp["paper_fees"] == pytest.approx(float(micro_fee("1", "0.96")))
    assert opp["paper_realized_pnl"] == pytest.approx(1 - 0.96 - float(micro_fee("1", "0.96")))
    (o,) = sim.orders()
    assert o["side"] == "no" and o["limit_price"] == pytest.approx(0.96)
    would = json.loads(o["response_json"])["would_send"]
    assert would["side"] == "ask" and would["price"] == "0.0400"        # buy NO at 0.96 = sell YES at 0.04


def test_a_losing_favourite_books_the_loss_on_the_paper_twin(tmp_path):
    sim = Sim(tmp_path, results={f"KXBTC15M-{CLOSE0}": "no"})
    sim.run()
    (opp,) = sim.opps()
    assert opp["paper_realized_pnl"] == pytest.approx(-0.95 - float(micro_fee("1", "0.95")))
    assert sim.bot.summary["paper_losses"] == 1


def test_one_paper_twin_per_window_however_long_the_rule_stays_true(tmp_path):
    sim = Sim(tmp_path)
    sim.run()
    assert len(sim.opps()) == 1 and len(sim.orders()) == 1
    assert len(sim.client.names("orderbook")) < 130          # polling stops after the paper twin is written


def test_the_rule_waits_for_the_window_even_when_the_book_already_qualifies(tmp_path):
    seen = []

    def books(t, e):
        seen.append(e)
        return FAV_YES                                       # qualifies from the first second of the market

    sim = Sim(tmp_path, books=books)
    sim.run()
    (opp,) = sim.opps()
    assert opp["detected_ts"] == pytest.approx(CLOSE0 - 120)           # elapsed 780, not earlier
    assert min(seen) == pytest.approx(780)                              # no order book was even read before 780 s


def test_the_rule_is_applied_once_to_the_first_fresh_book_and_a_neutral_book_skips_the_window(tmp_path):
    """Review finding (high): the bot polled the whole window and traded the first book that crossed a threshold. The rule
    (the user's, the module's docstring, the README and the dashboard card) decides ONCE, on the first fresh book: a YES
    mid inside 0.40-0.60 there means the window is skipped, and a favourite 7 seconds later is not traded."""
    sim = Sim(tmp_path, books=lambda t, e: FAV_YES if e >= 787 else DEAD)
    summary = sim.run()
    assert sim.opps() == [] and sim.orders() == []
    assert summary["no_signal"] == 1 and summary["detections"] == 0 and summary["paper_trades"] == 0
    (e,) = [e for e in sim.events("window") if "no trade" in e["message"]]
    assert "first fresh book" in e["message"] and "0.5050" in e["message"] and "skipped" in e["message"]
    # and it did not keep polling for the rest of the window: the decision was made at 780 s
    assert len(sim.client.names("orderbook")) == 1


def test_a_neutral_first_book_skips_a_live_window_too_and_sends_nothing(tmp_path):
    sim = live_sim(tmp_path, books=lambda t, e: FAV_YES if e >= 790 else DEAD)
    sim.run()
    assert sim.client.created == [] and sim.opps() == [] and sim.safety.entries == []
    assert len(sim.client.names("orderbook")) == 1


def test_a_book_that_is_not_a_basis_for_a_decision_does_not_count_as_the_first_one(tmp_path):
    """Stale, one-sided and crossed books carry no mid: the first FRESH TWO-SIDED book is the one that decides."""
    crossed = (lv(("0.9500", "10")), lv(("0.0700", "10")))

    def first_is_late(t, e):                       # nothing usable for 7 s, then a favourite
        return NO_QUOTE if e < 787 else FAV_YES

    sim = Sim(tmp_path, books=first_is_late)
    sim.run()
    (opp,) = sim.opps()
    assert opp["detected_ts"] == pytest.approx(CLOSE0 - 120 + 7)

    def stale_then_fresh(t, e):
        yes, no = FAV_YES
        return {"ticker": t, "yes": yes, "no": no, "fetched_at": CLOSE0 - 900 + e - (10 if e < 785 else 0)}

    (tmp_path / "b").mkdir()
    sim2 = Sim(tmp_path / "b", books=stale_then_fresh)
    sim2.run()
    assert sim2.opps()[0]["detected_ts"] == pytest.approx(CLOSE0 - 120 + 5)

    (tmp_path / "c").mkdir()
    sim3 = Sim(tmp_path / "c", books=lambda t, e: crossed if e < 783 else DEAD)    # crossed first, then a neutral book
    summary = sim3.run()
    assert sim3.opps() == [] and summary["no_signal"] == 1
    assert len(sim3.client.names("orderbook")) == 4                   # 780, 781, 782 crossed; 783 is the first usable one


def test_dead_zone_mid_skips_the_window(tmp_path):
    sim = Sim(tmp_path, books=lambda t, e: DEAD)
    summary = sim.run()
    assert sim.opps() == [] and sim.orders() == []
    assert summary["no_signal"] == 1 and summary["detections"] == 0
    (e,) = [e for e in sim.events("window") if "no trade" in e["message"]]
    assert "did not fire" in e["message"] and "0.5050" in e["message"]


def test_the_book_at_the_end_of_the_window_still_counts(tmp_path):
    sim = Sim(tmp_path, books=lambda t, e: FAV_YES if e >= 839 else NO_QUOTE)   # the first usable book is the last second's
    sim.run()
    (opp,) = sim.opps()
    assert CLOSE0 - 61 <= opp["detected_ts"] <= CLOSE0 - 60


def test_a_retry_is_made_only_while_the_rule_still_says_the_same_thing(tmp_path):
    """After the detection the rule is re-checked for a retry: the same outcome, and not a book that went neutral."""
    books = lambda t, e: FAV_YES if e < 781 else DEAD if e < 785 else FAV_YES      # noqa: E731
    sim = live_sim(tmp_path, books=books)
    sim.client.script = ["nofill", "fill"]
    sim.run()
    t0, t1 = [c_[2] for c_ in sim.client.calls if c_[0] == "create_order"]
    assert t1 - t0 == pytest.approx(5.0)               # not at 781..784 (neutral books), at the first poll that fires again
    (opp,) = sim.opps()
    assert opp["outcome"] == "filled" and opp["detected_ts"] == pytest.approx(CLOSE0 - 120)


def test_a_book_after_the_window_never_triggers(tmp_path):
    sim = Sim(tmp_path, books=lambda t, e: FAV_YES if e > 840.5 else DEAD)
    sim.run()
    assert sim.opps() == []


def test_a_decision_made_after_the_window_closed_is_refused_even_if_the_poll_started_inside(tmp_path):
    # each order book call takes 0.5 s, so the poll that starts at exactly 840.0 s decides at 840.5 s
    sim = Sim(tmp_path, latency=0.5, books=lambda t, e: FAV_YES if e >= 840.2 else DEAD)
    sim.run()
    assert sim.opps() == []
    assert max(c[2] for c in sim.client.calls if c[0] == "orderbook") == pytest.approx(CLOSE0 - 60)   # it did poll at 840


def test_stale_books_are_never_used(tmp_path):
    def books(t, e):
        return {"ticker": t, "yes": FAV_YES[0], "no": FAV_YES[1], "fetched_at": CLOSE0 - 900 + e - 10}    # 10 s old

    sim = Sim(tmp_path, books=books)
    summary = sim.run()
    assert sim.opps() == [] and summary["stale_books"] > 30
    assert any("old; not used" in e["message"] for e in sim.events("error"))
    assert len([e for e in sim.events("error") if "old; not used" in e["message"]]) <= 3      # warned, not spammed
    (e,) = [e for e in sim.events("window") if "no trade" in e["message"]]
    assert "no fresh order book" in e["message"]


def test_a_crossed_book_is_not_a_signal(tmp_path):
    crossed = (lv(("0.9500", "10")), lv(("0.0700", "10")))
    sim = Sim(tmp_path, books=lambda t, e: crossed)
    sim.run()
    assert sim.opps() == []


def test_unreadable_books_warn_and_do_not_crash(tmp_path):
    bad = {"yes": lv(("0.9400", "5"), ("0.5000", "5")), "no": lv(("0.0500", "5")), "fetched_at": 0.0}

    def books(t, e):
        bad["fetched_at"] = CLOSE0 - 900 + e
        return dict(bad)

    sim = Sim(tmp_path, books=books)
    sim.run()
    assert sim.opps() == []
    assert any("cannot read the order book" in e["message"] for e in sim.events("error"))


def test_book_read_failures_are_survived(tmp_path):
    sim = Sim(tmp_path)
    sim.client.fail["orderbook"] = ConnectionError("boom")
    sim.run()
    assert sim.opps() == []
    assert any("order book read failed" in e["message"] for e in sim.events("error"))


def test_two_consecutive_windows_each_get_their_own_paper_twin(tmp_path):
    t1, t2 = f"KXBTC15M-{CLOSE0}", f"KXBTC15M-{CLOSE0 + 900}"
    books = lambda t, e: (FAV_YES if t == t1 else FAV_NO) if e >= 700 else DEAD         # noqa: E731
    sim = Sim(tmp_path, books=books, results={t1: "yes", t2: "yes"})
    summary = sim.run(hours=0.5)
    opps = sim.opps()
    assert [(o["hour_ticker"], o["kind"]) for o in opps] == [(t1, "late favorite YES"), (t2, "late favorite NO")]
    assert opps[0]["paper_realized_pnl"] > 0 > opps[1]["paper_realized_pnl"]      # t2 settled YES: NO lost
    assert opps[0]["detected_ts"] == pytest.approx(CLOSE0 - 120)
    assert opps[1]["detected_ts"] == pytest.approx(CLOSE0 + 900 - 120)
    assert summary["windows"] == 2 and summary["settled"] == 2
    assert len(sim.orders()) == 2


def test_hours_bounds_the_run_and_unfinished_windows_are_not_started(tmp_path):
    sim = Sim(tmp_path, start_offset=-300)
    sim.run(hours=0.1)                       # deadline = CLOSE - 300 + 360: the window ending at CLOSE - 60 fits
    assert sim.bot.summary["windows"] == 1
    assert sim.clock.now < CLOSE0 + 900      # and no second window was waited for


def test_a_market_that_starts_mid_window_is_still_traded(tmp_path):
    sim = Sim(tmp_path, start_offset=-100)           # 800 s into the window
    sim.run(hours=0.1)
    (opp,) = sim.opps()
    assert CLOSE0 - 100 <= opp["detected_ts"] <= CLOSE0 - 60


def test_a_market_whose_decision_window_is_over_is_not_traded(tmp_path):
    sim = Sim(tmp_path, start_offset=-30)            # 870 s in: too late; the next market is 15 minutes away
    sim.run(hours=0.1)
    assert sim.opps() == [] and sim.bot.summary["windows"] == 0


def test_heartbeat_at_least_every_5_seconds_and_standard_keys(tmp_path):
    sim = Sim(tmp_path, results={f"KXBTC15M-{CLOSE0}": "yes"})
    sim.run()
    assert sim.max_beat_gap() <= 5.0
    assert sim.max_beat_gap() <= lf.HEARTBEAT_S + lf.TICK_S + 1e-6
    hb = sim.state.heartbeat_row()
    assert hb["status"] == "stopped"
    for k in ("strategy", "mode", "status", "market_ticker", "window_end_ts", "loss_used_usd", "max_loss_usd",
              "balance_usd", "kill_switch", "last_error"):
        assert k in hb["detail"], k
    assert hb["detail"]["strategy"] == "late_favorite" and hb["detail"]["mode"] == "watch"
    assert hb["detail"]["max_loss_usd"] == 5.0 and hb["detail"]["balance_usd"] is None
    assert hb["detail"]["kill_switch"] is False
    statuses = {s for _, s in sim.state.beats}
    assert {"waiting", "monitoring", "stopped"} <= statuses


def test_heartbeat_shows_the_window_while_trading(tmp_path):
    sim = Sim(tmp_path)
    seen = []
    orig = sim.state.heartbeat

    def spy(status, detail=None, ts=None):
        seen.append((status, dict(detail or {})))
        orig(status, detail, ts=ts)

    sim.state.heartbeat = spy
    sim.run()
    inside = [d for s, d in seen if s == "monitoring" and d.get("market_ticker")]
    assert inside, "heartbeats while the market is being watched carry its ticker"
    d = inside[-1]
    assert d["market_ticker"] == d["hour_ticker"] == f"KXBTC15M-{CLOSE0}"
    assert d["window_end_ts"] == CLOSE0 - 60 and d["market_close_ts"] == CLOSE0


def test_kill_file_present_at_start_blocks_everything(tmp_path):
    sim = Sim(tmp_path)
    sim.kill_file.write_text("stop\n")
    summary = sim.run()
    assert summary["end_reason"] == "kill switch present at start"
    assert sim.opps() == [] and sim.client.calls == []
    assert any(e["kind"] == "kill" for e in sim.events())
    assert sim.state.run_row()["end_reason"] == "kill switch present at start"


def test_kill_file_created_mid_run_stops_the_bot(tmp_path):
    sim = Sim(tmp_path, books=lambda t, e: NO_QUOTE)
    orig = sim.client.orderbook
    state = {"n": 0}

    def orderbook(ticker):
        state["n"] += 1
        if state["n"] == 5:
            sim.kill_file.write_text("kill\n")
        return orig(ticker)

    sim.client.orderbook = orderbook
    summary = sim.run()
    assert summary["end_reason"] == "kill switch"
    assert state["n"] <= 6
    assert any(e["kind"] == "kill" for e in sim.events())


def test_a_market_that_is_not_active_when_the_window_opens_is_skipped(tmp_path):
    sim = Sim(tmp_path)
    t = f"KXBTC15M-{CLOSE0}"
    sim.client.market_overrides[t] = {"status": "closed"}
    sim.run()
    assert sim.opps() == [] and sim.client.names("orderbook") == []
    assert any("skipping" in e["message"] for e in sim.events("window"))


def test_market_discovery_ignores_inactive_past_unparsable_and_duplicate_markets(tmp_path):
    sim = Sim(tmp_path)
    now = CLOSE0 - 500
    good = make_market(CLOSE0)
    sim.client.listing = lambda n: [make_market(CLOSE0 - 900), make_market(CLOSE0 + 900, status="closed"),
                                    {"ticker": "KXBTC15M-NOCLOSE", "status": "active"},
                                    {"ticker": "KXBTC15M-BAD", "status": "active", "close_time": "soon"},
                                    make_market(CLOSE0 + 1800), good]
    m, why = sim.bot._discover(now)
    assert m["ticker"] == good["ticker"] and why == ""
    # two markets closing at the same instant: refuse to guess
    dup = dict(good, ticker="KXBTC15M-OTHER")
    sim.client.listing = lambda n: [good, dup]
    m, why = sim.bot._discover(now)
    assert m is None and "refusing to guess" in why
    # discovery failure is reported, not raised
    sim.client.fail["open_markets"] = ConnectionError("down")
    m, why = sim.bot._discover(now)
    assert m is None and "discovery failed" in why


def test_the_earliest_unfinished_market_wins_even_if_the_next_is_already_listed(tmp_path):
    sim = Sim(tmp_path)
    sim.client.listing = lambda n: [make_market(CLOSE0 + 900), make_market(CLOSE0)]
    m, _ = sim.bot._discover(CLOSE0 - 500)
    assert m["ticker"] == f"KXBTC15M-{CLOSE0}"
    # once its decision window is over the next one is chosen
    m, _ = sim.bot._discover(CLOSE0 - 59)
    assert m["ticker"] == f"KXBTC15M-{CLOSE0 + 900}"
    m, _ = sim.bot._discover(CLOSE0 - 60)
    assert m["ticker"] == f"KXBTC15M-{CLOSE0}"


def test_the_normal_gap_between_windows_is_not_reported(tmp_path):
    t1, t2 = f"KXBTC15M-{CLOSE0}", f"KXBTC15M-{CLOSE0 + 900}"
    sim = Sim(tmp_path, results={t1: "yes", t2: "yes"})
    sim.run(hours=0.5)                                    # two windows: the next market opens a minute after each ends
    assert sim.bot.summary["windows"] == 2
    assert not [e for e in sim.events() if "waiting for the next" in e["message"]]


def test_a_long_wait_for_a_market_is_reported_once_and_a_failed_discovery_at_once(tmp_path):
    sim = Sim(tmp_path)
    sim.client.listing = lambda n: []
    sim.run(hours=0.1)
    msgs = [e["message"] for e in sim.events("error") if "waiting for the next KXBTC15M market" in e["message"]]
    assert len(msgs) == 1 and "no open KXBTC15M market" in msgs[0]
    broken = Sim(tmp_path / "broken" if (tmp_path / "broken").mkdir() is None else tmp_path)
    broken.client.fail["open_markets"] = ConnectionError("down")
    broken.run(hours=0.01)
    assert any("market discovery failed" in e["message"] for e in broken.events("error"))


def test_nothing_is_written_when_no_market_ever_appears(tmp_path):
    sim = Sim(tmp_path)
    sim.client.listing = lambda n: []
    summary = sim.run(hours=0.05)
    assert summary["windows"] == 0 and sim.opps() == []
    assert sim.max_beat_gap() <= 5.0


def test_a_paper_twin_with_no_result_yet_stays_pending_and_is_reported(tmp_path):
    sim = Sim(tmp_path, results={}, lf_cfg={"settle_wait_s": 60})        # the market never publishes a result
    sim.run()
    (opp,) = sim.opps()
    assert opp["paper_realized_pnl"] is None
    assert any("not settled" in e["message"] for e in sim.events("settle"))


def test_unusual_market_results_are_not_guessed(tmp_path):
    sim = Sim(tmp_path, results={f"KXBTC15M-{CLOSE0}": "void"}, lf_cfg={"settle_wait_s": 60})
    sim.run()
    assert sim.opps()[0]["paper_realized_pnl"] is None


def test_run_records_config_without_secrets(tmp_path):
    sim = Sim(tmp_path)
    sim.run()
    cfg = json.loads(sim.state.run_row()["config_json"])
    assert cfg["strategy"] == "late_favorite" and cfg["mode"] == "watch" and cfg["series"] == "KXBTC15M"
    assert cfg["rule"]["window_start_s"] == 780.0 and cfg["rule"]["buy_yes_if_mid_above"] == "0.60"
    assert cfg["max_loss_usd"] == "5.0" and cfg["contracts"] == "1"
    text = json.dumps(cfg).lower()
    assert "private" not in text and "key_id" not in text


# ============================================================================================ smoke mode
def test_smoke_does_one_discovery_and_one_decision_pass_and_exits(tmp_path):
    t = f"KXBTC15M-{CLOSE0}"
    sim = Sim(tmp_path, start_offset=-500, books=lambda tk, e: FAV_YES)
    summary = sim.run(smoke=True)
    assert summary["end_reason"] == "smoke pass done"
    assert [c[0] for c in sim.client.calls] == ["open_markets", "orderbook"]
    smoke = summary["smoke"]
    assert smoke["found"] and smoke["ticker"] == t and smoke["in_decision_window"] is False
    assert smoke["seconds_to_decision_window"] == pytest.approx(380)
    assert smoke["rule"]["outcome"] == "yes" and smoke["rule"]["ask"] == D("0.95")
    assert smoke["would_trade"] is False                 # outside the window
    assert sim.opps() == [] and sim.orders() == []
    assert sim.state.run_row()["end_reason"] == "smoke pass done"
    (e,) = sim.events("smoke")
    assert "would buy YES at 0.9500" in e["message"] and "opens in 380 s" in e["message"]
    assert sim.clock.sleeps == []                        # no waiting at all


def test_smoke_inside_the_window_says_it_would_trade(tmp_path):
    sim = Sim(tmp_path, start_offset=-100, books=lambda tk, e: FAV_NO)
    smoke = sim.run(smoke=True)["smoke"]
    assert smoke["in_decision_window"] and smoke["would_trade"] and smoke["rule"]["outcome"] == "no"
    assert sim.opps() == [] and sim.orders() == []


def test_smoke_with_no_market_or_no_book_reports_it(tmp_path):
    sim = Sim(tmp_path)
    sim.client.listing = lambda n: []
    s = sim.run(smoke=True)["smoke"]
    assert s["found"] is False and "no open KXBTC15M market" in s["reason"]
    sim2 = Sim(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path, books=lambda t, e: ([], []), start_offset=-500)
    s2 = sim2.run(smoke=True)["smoke"]
    assert s2["found"] and s2["rule"] is None and s2["would_trade"] is False


def test_smoke_is_refused_for_live(tmp_path):
    with pytest.raises(ValueError, match="smoke"):
        Sim(tmp_path, live=True).bot.run(smoke=True)


# ============================================================================================ live mode
def live_sim(tmp_path, **kw) -> Sim:
    kw.setdefault("results", {f"KXBTC15M-{CLOSE0}": "yes"})
    return Sim(tmp_path, live=True, **kw)


def coids(sim) -> list[str]:
    return [b_["client_order_id"] for b_ in sim.client.created]


def test_live_happy_path_places_one_ioc_at_the_ask_and_tracks_everything(tmp_path):
    sim = live_sim(tmp_path, latency=0.05)
    summary = sim.run()
    t = f"KXBTC15M-{CLOSE0}"
    (body,) = sim.client.created
    assert body == {"ticker": t, "client_order_id": body["client_order_id"], "side": "bid", "price": "0.9500",
                    "count": "1.00", "time_in_force": "immediate_or_cancel",
                    "self_trade_prevention_type": "taker_at_cross", "exchange_index": 2}
    # ledger: intent committed, then executed with the exchange's order id
    (row,) = sim.ledger.orders_for(t)
    assert row["status"] == "executed" and row["order_id"] == "ord-1" and row["purpose"] == "entry"
    assert (row["side"], row["price"], row["count"], row["outcome"], row["outcome_price"]) == ("bid", "0.9500", "1.00", "yes", "0.9500")
    assert row["time_in_force"] == "immediate_or_cancel" and row["reduce_only"] == 0
    # dashboard order row
    (o,) = sim.orders()
    assert (o["side"], o["action"], o["status"], o["dry_run"], o["order_id"]) == ("yes", "buy", "executed", 0, "ord-1")
    assert o["filled"] == 1.0 and o["avg_fill_price"] == pytest.approx(0.95) and o["ts_ack"] >= o["ts_sent"]
    assert o["fees"] == pytest.approx(float(micro_fee("1", "0.95")))
    # opportunity row: paper twin at detection, live fill, latency, settlement P&L
    (opp,) = sim.opps()
    fee = float(micro_fee("1", "0.95"))
    assert opp["outcome"] == "filled" and opp["live"] == 1 and opp["kind"] == "late favorite YES"
    assert opp["paper_cost"] == pytest.approx(0.95) and opp["live_cost"] == pytest.approx(0.95)
    assert opp["live_fees"] == pytest.approx(fee)
    assert opp["latency_ms"] == pytest.approx(50.0, abs=0.5)        # detection -> ack of the order (one 50 ms call)
    assert opp["payout"] == 1.0 and opp["realized_pnl"] == pytest.approx(1 - 0.95 - fee)
    assert opp["paper_realized_pnl"] == pytest.approx(1 - 0.95 - fee)
    assert summary["live_attempts"] == 1 and summary["live_fills"] == 1 and summary["outcomes"] == {"filled": 1}
    assert summary["live_pnl"] == D("1") - D("0.95") - micro_fee("1", "0.95")
    assert sim.safety.trips == [] and len(sim.safety.entries) == 1


def test_live_ledger_numbers_after_settlement(tmp_path):
    sim = live_sim(tmp_path)
    sim.run()
    t = f"KXBTC15M-{CLOSE0}"
    assert sim.ledger.settled_tickers() == {t}
    assert sim.ledger.realized_pnl() == D("1") - D("0.95") - micro_fee("1", "0.95")
    assert sim.ledger.realized_loss_total() == 0 and sim.ledger.open_worst_case() == 0
    assert sim.reconcile.settled and sim.reconcile.settled[0] == [t]
    assert not [e for e in sim.events("settle") if e["level"] == "error"]        # bot and ledger P&L agree


def test_live_loss_is_booked_and_counts_against_the_cap(tmp_path):
    sim = live_sim(tmp_path, results={f"KXBTC15M-{CLOSE0}": "no"})
    sim.run()
    (opp,) = sim.opps()
    fee = micro_fee("1", "0.95")
    assert opp["payout"] == 0.0 and opp["realized_pnl"] == pytest.approx(-float(D("0.95") + fee))
    assert sim.ledger.realized_loss_total() == D("0.95") + fee
    assert sim.bot.summary["live_losses"] == 1


def test_live_no_side_sends_an_ask_at_one_minus_the_no_price(tmp_path):
    sim = live_sim(tmp_path, books=lambda t, e: FAV_NO if e >= 700 else DEAD, results={f"KXBTC15M-{CLOSE0}": "no"})
    sim.run()
    (body,) = sim.client.created
    # buy NO at 0.96 == sell YES at 0.04: side 'ask', price '0.0400'
    assert body["side"] == "ask" and body["price"] == "0.0400" and body["count"] == "1.00"
    (row,) = sim.ledger.orders_for(f"KXBTC15M-{CLOSE0}")
    assert (row["outcome"], row["outcome_price"], row["side"], row["price"]) == ("no", "0.9600", "ask", "0.0400")
    (opp,) = sim.opps()
    assert opp["kind"] == "late favorite NO" and opp["live_cost"] == pytest.approx(0.96)
    assert opp["realized_pnl"] == pytest.approx(1 - 0.96 - float(micro_fee("1", "0.96")))
    spec = sim.safety.entries[0]
    assert (spec["outcome"], spec["outcome_price"], spec["side"], spec["yes_price"]) == ("no", D("0.96"), "ask", D("0.04"))


def test_live_intent_is_committed_before_the_send_and_matches_the_body(tmp_path):
    sim = live_sim(tmp_path)
    sim.run()                          # FakeClient.create_order asserts both inside the call
    assert len(sim.client.created) == 1


def test_live_safety_sees_a_spec_priced_exactly_at_the_ask(tmp_path):
    sim = live_sim(tmp_path)
    sim.run()
    spec = sim.safety.entries[0]
    assert spec["outcome"] == "yes" and spec["outcome_price"] == D("0.95") and spec["count"] == D("1")
    assert spec["time_in_force"] == "immediate_or_cancel" and spec["reduce_only"] is False
    assert spec["ticker"] == f"KXBTC15M-{CLOSE0}" and spec["exchange_index"] == 2


def test_an_unfilled_ioc_is_retried_on_the_next_poll_with_a_new_intent(tmp_path):
    books = lambda t, e: FAV_YES if e < 781 else book("0.9300", "0.0600") if e < 790 else FAV_YES     # noqa: E731
    sim = live_sim(tmp_path, books=books)
    sim.client.script = ["nofill", "fill"]
    sim.run()
    assert len(sim.client.created) == 2
    a, c = sim.client.created
    assert a["client_order_id"] != c["client_order_id"]
    assert a["price"] == "0.9500"
    assert c["price"] == "0.9400"                   # the retry is priced at the CURRENT ask: 1 - 0.06
    t = f"KXBTC15M-{CLOSE0}"
    rows = sim.ledger.orders_for(t)
    assert [r["status"] for r in rows] == ["canceled", "executed"] and [r["purpose"] for r in rows] == ["entry", "retry"]
    # retry happened one poll later, not instantly
    t0, t1 = [c_[2] for c_ in sim.client.calls if c_[0] == "create_order"]
    assert t1 - t0 == pytest.approx(1.0)
    (opp,) = sim.opps()
    assert opp["outcome"] == "filled"
    assert opp["paper_cost"] == pytest.approx(0.95)         # the paper twin stays at the FIRST detection
    assert opp["live_cost"] == pytest.approx(0.94)          # the live fill at the retry's ask
    assert len(sim.orders()) == 2 and sim.bot.summary["live_attempts"] == 2


def test_at_most_three_attempts_per_window(tmp_path):
    sim = live_sim(tmp_path)
    sim.client.script = ["nofill"] * 10
    sim.run()
    assert len(sim.client.created) == 3
    assert len(set(coids(sim))) == 3
    (opp,) = sim.opps()
    assert opp["outcome"] == "none_filled" and opp["live"] == 1 and opp["live_cost"] is None
    assert opp["realized_pnl"] is None
    assert sim.ledger.realized_loss_total() == 0 and sim.ledger.open_worst_case() == 0
    assert any("3 attempt" in e["message"] or "none filled" in e["message"] for e in sim.events("window"))


def test_never_more_than_one_filled_trade_per_window(tmp_path):
    sim = live_sim(tmp_path)               # the rule stays true for the whole window
    sim.run()
    assert len(sim.client.created) == 1
    assert len(sim.client.names("orderbook")) == 1        # the window stops being polled once it is traded


def test_a_partial_fill_ends_the_window(tmp_path):
    sim = live_sim(tmp_path)
    sim.client.script = ["fill:0.40"]
    sim.run()
    assert len(sim.client.created) == 1
    (o,) = sim.orders()
    assert o["status"] == "partial" and o["filled"] == pytest.approx(0.4)
    (opp,) = sim.opps()
    assert opp["outcome"] == "filled" and opp["live_cost"] == pytest.approx(0.4 * 0.95)
    assert opp["payout"] == pytest.approx(0.4)


def test_a_partial_fill_scales_the_paper_twin_to_the_contracts_that_filled(tmp_path):
    """Review finding: the dashboard divides live_cost by the row's contracts. After a partial fill (0.40 of 1 at 0.95) it
    showed 'filled at 0.3800', a -57 cent 'delta vs detection', and a paired comparison of a 0.40-contract live trade
    with a full-contract paper twin (the 'delay cost' KPI then showed a delay that never happened)."""
    sim = live_sim(tmp_path, results={f"KXBTC15M-{CLOSE0}": "no"})                  # the favourite loses
    sim.client.script = ["fill:0.40"]
    sim.run()
    (opp,) = sim.opps()
    fee = float(micro_fee("0.40", "0.95"))
    assert opp["contracts"] == pytest.approx(0.40)                                   # the trade that was really made
    assert opp["paper_cost"] == pytest.approx(0.95 * 0.40) and opp["paper_fees"] == pytest.approx(fee)
    assert opp["live_cost"] == pytest.approx(0.95 * 0.40)
    # per contract, as the dashboard shows it: the same 0.95 on both sides, so no delta
    assert opp["live_cost"] / opp["contracts"] == pytest.approx(0.95) == pytest.approx(opp["paper_cost"] / opp["contracts"])
    # both P&Ls are for 0.40 contracts, so the paired live-vs-paper difference is the order delay and nothing else
    assert opp["paper_realized_pnl"] == pytest.approx(-(0.95 * 0.40) - fee)
    assert opp["realized_pnl"] == pytest.approx(opp["paper_realized_pnl"])
    assert sim.bot.summary["paper_pnl"] == sim.bot.summary["live_pnl"] == D(str(-(0.95 * 0.40))) - micro_fee("0.40", "0.95")


def test_a_full_fill_leaves_the_paper_twin_alone(tmp_path):
    sim = live_sim(tmp_path)
    sim.run()
    (opp,) = sim.opps()
    assert opp["contracts"] == 1.0 and opp["paper_cost"] == pytest.approx(0.95)


def test_a_restart_settles_a_scaled_paper_twin_with_its_scaled_size(tmp_path):
    """The scaled numbers are what the row stores, so a later run that settles the row (the run was stopped before the
    market settled) books the paper P&L of the contracts that filled, not of the intended one."""
    t = f"KXBTC15M-{CLOSE0}"
    sim = live_sim(tmp_path, results={}, lf_cfg={"settle_wait_s": 30})                 # the market never publishes a result
    sim.client.script = ["fill:0.40"]
    sim.run()
    (opp,) = sim.opps()
    assert opp["paper_realized_pnl"] is None and opp["contracts"] == pytest.approx(0.40)
    second = Sim(tmp_path, live=True, results={t: "yes"}, start_offset=3600, conn=sim.conn, run_id="run-2",
                 books=lambda tk, e: DEAD)
    second.run(hours=0.02)
    contracts, paper_pnl = sim.conn.execute("SELECT contracts, paper_realized_pnl FROM bot_opportunities").fetchone()
    assert contracts == pytest.approx(0.40)
    assert paper_pnl == pytest.approx(0.40 - 0.95 * 0.40 - float(micro_fee("0.40", "0.95")))


def test_rejected_orders_count_as_attempts_and_are_retried_at_most_three_times(tmp_path):
    sim = live_sim(tmp_path)
    sim.client.script = ["reject", "reject", "reject", "fill"]
    sim.run()
    assert len(sim.client.created) == 3
    assert [r["status"] for r in sim.ledger.orders_for(f"KXBTC15M-{CLOSE0}")] == ["rejected"] * 3
    assert sim.opps()[0]["outcome"] == "none_filled"
    assert sim.ledger.open_worst_case() == 0          # a rejected order never counts as exposure


def test_auth_failure_trips_safety_and_stops(tmp_path):
    sim = live_sim(tmp_path)
    sim.client.script = ["auth", "fill"]
    summary = sim.run()
    assert len(sim.client.created) == 1
    assert sim.safety.halted and "authentication" in sim.safety.trips[0]
    assert "authentication" in summary["end_reason"]


def test_a_duplicate_response_is_looked_up_and_never_resent(tmp_path):
    sim = live_sim(tmp_path)
    sim.client.script = ["duplicate"]
    sim.run()
    assert len(sim.client.created) == 1
    (opp,) = sim.opps()
    assert opp["outcome"] == "filled"          # the lookup found the filled order
    assert sim.client.names("orders")


def test_unknown_outcome_is_looked_up_found_and_not_resent(tmp_path):
    sim = live_sim(tmp_path)
    sim.client.script = ["timeout_placed"]
    sim.run()
    assert len(sim.client.created) == 1
    t = f"KXBTC15M-{CLOSE0}"
    (row,) = sim.ledger.orders_for(t)
    assert row["status"] == "executed" and row["order_id"] == "ord-1"       # adopted from the exchange's order
    (opp,) = sim.opps()
    assert opp["outcome"] == "filled" and opp["live"] == 1
    assert sim.safety.trips == []
    assert any("found by lookup" in e["message"] for e in sim.events("order"))


def test_unknown_outcome_that_is_never_found_trips_safety_and_stops_without_resending(tmp_path):
    sim = live_sim(tmp_path)
    sim.client.script = ["timeout_lost", "fill"]
    summary = sim.run()
    assert len(sim.client.created) == 1                       # never resent, not even on the next poll
    assert sim.safety.halted and "unknown" in sim.safety.trips[0]
    (opp,) = sim.opps()
    assert opp["outcome"] == "unknown" and opp["realized_pnl"] is None
    (o,) = sim.orders()
    assert o["status"] == "unknown"
    # the ledger keeps it as an unresolved order, counted as fully filled for the loss cap
    (row,) = sim.ledger.orders_for(f"KXBTC15M-{CLOSE0}")
    assert row["status"] == "unknown" and sim.ledger.open_worst_case() == worst_of("0.95")
    assert "unknown" in summary["end_reason"]
    assert any("never resent" in e["message"] for e in sim.events("order"))
    assert sim.state.heartbeat_row()["detail"]["last_error"]


def test_an_exception_from_create_order_is_an_unknown_outcome_not_a_crash(tmp_path):
    sim = live_sim(tmp_path)
    sim.client.script = [ConnectionError("reset by peer"), "fill"]
    sim.run()
    assert len(sim.client.created) == 1 and sim.safety.halted
    assert sim.opps()[0]["outcome"] == "unknown"


def test_the_lookup_waits_for_the_portfolio_data_to_catch_up(tmp_path):
    sim = live_sim(tmp_path)
    sim.client.script = ["timeout_placed"]
    stale = {"n": 0}

    def user_data_timestamp():
        stale["n"] += 1
        return {"as_of_time": iso(sim.clock.now - (5 if stale["n"] < 4 else 0))}

    sim.client.user_data_timestamp = user_data_timestamp
    sim.run()
    assert stale["n"] >= 4 and sim.opps()[0]["outcome"] == "filled"


def test_a_fill_on_the_wrong_outcome_trips_safety(tmp_path):
    sim = live_sim(tmp_path)
    orig = sim.client.fills

    def wrong_side(**kw):
        fills = orig(**kw)
        for f in fills:
            f["outcome_side"] = "no"
        return fills

    sim.client.fills = wrong_side
    sim.run()
    assert sim.safety.halted and "filled on 'no'" in sim.safety.trips[0]


def test_a_fill_above_the_limit_trips_safety(tmp_path):
    sim = live_sim(tmp_path)
    orig = sim.client.fills

    def pricey(**kw):
        fills = orig(**kw)
        for f in fills:
            f["yes_price_dollars"], f["no_price_dollars"] = "0.9700", "0.0300"
        return fills

    sim.client.fills = pricey
    sim.run()
    assert sim.safety.halted and "above its" in sim.safety.trips[0]


def test_exact_fill_prices_and_fees_replace_the_limit_estimate(tmp_path):
    sim = live_sim(tmp_path)
    orig = sim.client.fills

    def better(**kw):                      # filled one tick better than the limit, with a different fee
        fills = orig(**kw)
        for f in fills:
            f["yes_price_dollars"], f["no_price_dollars"], f["fee_cost"] = "0.9490", "0.0510", "0.003400"
        return fills

    sim.client.fills = better
    sim.run()
    (o,) = sim.orders()
    assert o["avg_fill_price"] == pytest.approx(0.949) and o["fees"] == pytest.approx(0.0034)
    (opp,) = sim.opps()
    assert opp["live_cost"] == pytest.approx(0.949) and opp["live_fees"] == pytest.approx(0.0034)
    assert opp["realized_pnl"] == pytest.approx(1 - 0.949 - 0.0034)
    assert sim.ledger.fills_for(f"KXBTC15M-{CLOSE0}")[0]["yes_price"] == "0.9490"       # the ledger has the exchange's fill


def test_safety_refusal_sends_nothing_and_is_rechecked_every_few_seconds(tmp_path):
    sim = live_sim(tmp_path)
    sim.safety.verdicts = [(False, "balance too low")] * 100
    sim.run()
    assert sim.client.created == []
    n = len(sim.safety.entries)
    assert 15 <= n <= 22                                  # ~60 s window / 3 s: not every poll
    gaps = [b_ - a for a, b_ in zip(sim.safety.checks_at, sim.safety.checks_at[1:])]
    assert min(gaps) >= lf.BLOCK_RETRY_S - 1e-9
    (opp,) = sim.opps()
    assert opp["outcome"] == "blocked_by_cap" and opp["live"] == 0
    caps = sim.events("cap")
    assert len(caps) == 1 and "balance too low" in caps[0]["message"]         # one event, not 20
    assert sim.bot.summary["blocked"] == 1 and sim.ledger.orders_for(f"KXBTC15M-{CLOSE0}") == []


def test_a_refusal_that_clears_lets_the_trade_through_and_it_is_not_an_attempt(tmp_path):
    sim = live_sim(tmp_path)
    sim.safety.verdicts = [(False, "stale book"), (True, "")]
    sim.run()
    assert len(sim.client.created) == 1
    (row,) = sim.ledger.orders_for(f"KXBTC15M-{CLOSE0}")
    assert row["purpose"] == "entry"                      # the refusal was not an attempt, so this is still the entry
    t0, t1 = sim.safety.checks_at
    assert t1 - t0 == pytest.approx(lf.BLOCK_RETRY_S)
    assert sim.opps()[0]["outcome"] == "filled"


def test_safety_raising_fails_closed(tmp_path):
    sim = live_sim(tmp_path)
    sim.safety.verdicts = [RuntimeError("balance read failed")] * 100
    sim.run()
    assert sim.client.created == []
    assert any("check_entry failed" in e["message"] for e in sim.events("cap"))


@pytest.mark.parametrize("verdict", [None, "yes", 1, (None, "x"), ("ok", "x"), SimpleNamespace(), {}])
def test_unrecognised_safety_verdicts_are_refusals(tmp_path, verdict):
    sim = live_sim(tmp_path)
    sim.safety.verdicts = [verdict] * 100
    sim.run()
    assert sim.client.created == []


@pytest.mark.parametrize("verdict", [(True, ""), (True, None), [True, "fine"], True,
                                     SimpleNamespace(ok=True, reason=""), SimpleNamespace(allowed=True),
                                     {"ok": True}, {"allowed": True, "reason": "x"}])
def test_the_common_safety_verdict_shapes_that_say_yes(tmp_path, verdict):
    sim = live_sim(tmp_path)
    sim.safety.verdicts = [verdict]
    sim.run()
    assert len(sim.client.created) == 1


@pytest.mark.parametrize("verdict,reason", [(False, ""), ((False, "nope"), "nope"), (SimpleNamespace(ok=False, reason="nope"), "nope"),
                                            ({"ok": False, "reason": "nope"}, "nope")])
def test_the_common_safety_verdict_shapes_that_say_no(tmp_path, verdict, reason):
    sim = live_sim(tmp_path)
    sim.safety.verdicts = [verdict] * 100
    sim.run()
    assert sim.client.created == []
    assert reason in sim.events("cap")[0]["message"]


def test_a_halted_safety_blocks_orders(tmp_path):
    sim = live_sim(tmp_path)
    sim.safety.halted = True
    sim.run()
    assert sim.client.created == [] and sim.safety.entries == []


def test_the_safety_kill_switch_stops_the_bot(tmp_path):
    sim = live_sim(tmp_path)
    sim.safety.kill = True
    summary = sim.run()
    assert summary["end_reason"] == "kill switch present at start" and sim.client.created == []


def test_kill_file_appearing_before_the_send_prevents_the_order(tmp_path):
    sim = live_sim(tmp_path)
    orig = sim.safety.check_entry

    def check_then_kill(spec, book, market):
        res = orig(spec, book, market)
        sim.kill_file.write_text("kill\n")
        return res

    sim.safety.check_entry = check_then_kill
    summary = sim.run()
    assert sim.client.created == [] and summary["end_reason"] == "kill switch"
    assert sim.ledger.orders_for(f"KXBTC15M-{CLOSE0}") == []


# ---------------------------------------------------------------------------------------------- loss cap
def test_loss_cap_arithmetic_allows_the_last_order_that_fits(tmp_path):
    sim = live_sim(tmp_path)
    seed_loss(sim.ledger, 4)                              # 4 x 0.953325 realized
    used = sim.ledger.realized_loss_total()
    assert used == 4 * (D("0.95") + micro_fee("1", "0.95"))
    assert used + worst_of("0.95") <= D("5")
    sim.run()
    assert len(sim.client.created) == 1


def test_loss_cap_blocks_an_order_that_does_not_fit_and_stops_when_nothing_is_open(tmp_path):
    sim = live_sim(tmp_path)
    seed_loss(sim.ledger, 5)                              # 4.766625 realized: 4.766625 + 0.9535 > 5
    assert sim.ledger.realized_loss_total() + worst_of("0.95") > D("5")
    summary = sim.run()
    assert sim.client.created == [] and len(sim.safety.entries) == 1       # Safety is asked first, then the bot's own cap
    assert sim.safety.halted and "loss cap" in sim.safety.trips[0]
    assert "loss cap" in summary["end_reason"]
    (opp,) = sim.opps()
    assert opp["outcome"] == "blocked_by_cap"
    assert "loss cap" in sim.events("cap")[0]["message"]


def test_open_exposure_counts_against_the_cap_but_does_not_stop_the_bot(tmp_path):
    sim = live_sim(tmp_path)
    seed_loss(sim.ledger, 3)
    seed_loss(sim.ledger, 1, start=10, settle=False)      # a filled, unsettled position: 0.953325 open worst case
    assert sim.ledger.open_worst_case() == D("0.95") + micro_fee("1", "0.95")
    assert sim.ledger.realized_loss_total() + sim.ledger.open_worst_case() + worst_of("0.95") <= D("5")
    sim.run()                                              # 2.859975 + 0.953325 + 0.9535 = 4.7668: fits
    assert len(sim.client.created) == 1


def test_open_exposure_blocks_but_the_bot_keeps_running_while_something_is_open(tmp_path):
    sim = live_sim(tmp_path)
    seed_loss(sim.ledger, 4)
    seed_loss(sim.ledger, 1, start=10, settle=False)       # 3.8133 realized + 0.9533 open + 0.9535 > 5
    summary = sim.run(hours=0.5)
    assert sim.client.created == []
    assert not sim.safety.halted and summary["end_reason"] == "finished"      # room may free up when it settles
    assert summary["windows"] == 2 and summary["blocked"] == 2


def test_loss_cap_trips_after_a_run_of_losing_trades_end_to_end(tmp_path):
    results = {f"KXBTC15M-{CLOSE0 + 900 * i}": "no" for i in range(0, 8)}           # every favourite loses
    sim = live_sim(tmp_path, results=results, start_offset=-400)
    summary = sim.run(hours=3)
    assert len(sim.client.created) == 5                    # 5 x 0.953325 = 4.766625; the 6th would exceed 5.00
    assert sim.ledger.realized_loss_total() == 5 * (D("0.95") + micro_fee("1", "0.95"))
    assert sim.safety.halted and "loss cap" in sim.safety.trips[0]
    assert "loss cap" in summary["end_reason"]
    assert summary["windows"] == 6 and summary["live_losses"] == 5
    assert sim.bot.summary["live_pnl"] == -5 * (D("0.95") + micro_fee("1", "0.95"))
    # the stop still books the last paper twin (the 6th, never traded): the drain after a trip only reads
    assert all(o["paper_realized_pnl"] is not None for o in sim.opps()) and summary["settled"] == 6
    assert [o["outcome"] for o in sim.opps()] == ["filled"] * 5 + ["blocked_by_cap"]
    detail = sim.state.heartbeat_row()["detail"]
    assert detail["loss_used_usd"] == pytest.approx(4.766625) and detail["max_loss_usd"] == 5.0


def test_a_smaller_configured_cap_is_respected(tmp_path):
    sim = live_sim(tmp_path, trading={"max_loss_usd": 1.0})
    seed_loss(sim.ledger, 1)
    sim.run()
    assert sim.client.created == [] and sim.safety.halted           # 0.953325 + 0.9535 > 1.00


def test_safety_is_asked_first_so_it_can_sweep_stale_settlements_before_the_cap_refuses(tmp_path):
    sim = live_sim(tmp_path)
    seed_loss(sim.ledger, 5)
    sim.run()
    assert len(sim.safety.entries) == 1 and sim.client.created == []
    assert "loss cap" in sim.events("cap")[0]["message"] and "refused by safety" not in sim.events("cap")[0]["message"]


def test_a_cap_refusal_from_safety_is_explained_by_the_bots_own_check_and_stops_the_bot(tmp_path):
    sim = live_sim(tmp_path)
    seed_loss(sim.ledger, 5)
    sim.safety.verdicts = [(False, "loss cap: 4.77 used + 0.9535 worst case of this order > $5")]
    summary = sim.run()
    assert sim.safety.halted and "loss cap" in summary["end_reason"] and sim.client.created == []


def test_a_refusal_from_safety_that_is_not_the_cap_does_not_stop_the_bot(tmp_path):
    sim = live_sim(tmp_path)
    sim.safety.verdicts = [(False, "exposure cap: 3.5 open + 0.95 collateral > $3")] * 100
    summary = sim.run()
    assert not sim.safety.halted and summary["end_reason"] == "finished" and sim.client.created == []
    assert "refused by safety: exposure cap" in sim.events("cap")[0]["message"]


def test_orders_above_the_per_order_ceilings_are_refused_even_if_safety_would_allow(tmp_path):
    sim = live_sim(tmp_path)
    bot = sim.bot
    market = make_market(CLOSE0)
    spec = lf.entry_spec(market, "yes", D("0.95"), D("2"))            # 2 contracts: collateral 1.90
    ok, why, cap = bot._loss_ok(spec)
    assert not ok and not cap and "ceiling" in why
    spec = lf.entry_spec(market, "yes", D("0.999"), D("2"))           # the highest price, 2 contracts: collateral 1.998
    ok, why, cap = bot._loss_ok(spec)
    assert not ok and not cap and "ceiling" in why
    spec = lf.entry_spec(market, "yes", D("0.9995"), D("1"))          # above 0.999
    ok, why, _ = bot._loss_ok(spec)
    assert not ok and "outside 0.001..0.999" in why
    for outcome, price in (("yes", "0.9995"), ("no", "0.9995"), ("yes", "0.0005"), ("no", "0.0005")):   # both sides, both ends
        ok, why, cap = bot._loss_ok(lf.entry_spec(market, outcome, D(price), D("1")))
        assert not ok and not cap and f"price {price} is outside 0.001..0.999" in why, (outcome, price, why)
    # the bounds themselves, and the 99-99.9c favorites the user decided to take, are inside
    for outcome, price in (("yes", "0.999"), ("no", "0.999"), ("yes", "0.001"), ("no", "0.001"), ("yes", "0.995"),
                           ("no", "0.993"), ("yes", "0.99")):
        assert bot._loss_ok(lf.entry_spec(market, outcome, D(price), D("1"))) == (True, "", False), (outcome, price)
    spec = lf.entry_spec(market, "yes", D("0.95"), D("1"))
    assert bot._loss_ok(spec) == (True, "", False)


def test_the_bots_price_bounds_are_the_users_decision_and_match_safetys_hard_limits():
    """2026-10-06: take 99-99.9c favorites. The bot's own bounds are the same numbers as Safety's hard floor and ceiling, so it
    never sends what Safety would refuse nor refuses what Safety allows; the worst case of a 0.999 buy (price plus the exact
    taker fee, and the padded worst case the loss cap budgets) is still inside the $1.00 per-order ceiling."""
    from kpm.execution.safety import CEILINGS, FLOORS
    assert (lf.PRICE_MIN, lf.PRICE_MAX) == (D("0.001"), D("0.999"))
    assert (lf.PRICE_MIN, lf.PRICE_MAX) == (FLOORS["price_min"], CEILINGS["price_max"])
    assert lf.PRICE_MAX + micro_fee("1", "0.999") == D("0.999070") <= lf.COST_PER_ORDER_CEILING
    assert worst_of("0.999") == D("0.9992") <= lf.COST_PER_ORDER_CEILING == CEILINGS["max_cost_per_order_usd"]


# ---------------------------------------------------------------------------------------------- ledger memory
def test_a_market_already_traded_in_the_ledger_is_not_traded_again(tmp_path):
    sim = live_sim(tmp_path)
    t = f"KXBTC15M-{CLOSE0}"
    spec = lf.entry_spec(make_market(CLOSE0), "yes", D("0.95"), D("1"))
    spec["client_order_id"] = "earlier-run"
    spec["body"] = lf.order_body(spec, "earlier-run")
    coid = sim.ledger.new_intent(spec)
    sim.ledger.mark_result(coid, Result("ok", 201, {"order_id": "o-early", "client_order_id": coid,
                                                     "fill_count": "1.00", "remaining_count": "0.00"}))
    sim.run()
    assert sim.client.created == [] and sim.opps() == []
    assert any("already has 1 order" in e["message"] and "traded" in e["message"] for e in sim.events("window"))
    assert len(sim.ledger.orders_for(t)) == 1


def test_attempts_from_an_earlier_run_count_against_the_budget_of_three(tmp_path):
    sim = live_sim(tmp_path)
    for i in range(2):
        spec = lf.entry_spec(make_market(CLOSE0), "yes", D("0.95"), D("1"))
        spec["client_order_id"] = f"earlier-{i}"
        spec["body"] = lf.order_body(spec, f"earlier-{i}")
        coid = sim.ledger.new_intent(spec)
        sim.ledger.mark_result(coid, Result("rejected", 400, None, {"code": "invalid_order"}))
    sim.client.script = ["nofill", "nofill", "nofill"]
    sim.run()
    assert len(sim.client.created) == 1                   # 2 earlier + 1 now = 3
    assert len(sim.ledger.orders_for(f"KXBTC15M-{CLOSE0}")) == 3


def test_an_exhausted_attempt_budget_skips_the_window(tmp_path):
    sim = live_sim(tmp_path)
    for i in range(3):
        spec = lf.entry_spec(make_market(CLOSE0), "yes", D("0.95"), D("1"))
        spec["client_order_id"] = f"earlier-{i}"
        spec["body"] = lf.order_body(spec, f"earlier-{i}")
        coid = sim.ledger.new_intent(spec)
        sim.ledger.mark_result(coid, Result("rejected", 400, None, {"code": "invalid_order"}))
    sim.run()
    assert sim.client.created == [] and sim.client.names("orderbook") == []


def test_tickers_left_unsettled_by_an_earlier_run_are_settled_at_startup(tmp_path):
    sim = live_sim(tmp_path, results={f"KXBTC15M-{CLOSE0 - 900}": "no", f"KXBTC15M-{CLOSE0}": "yes"})
    old = make_market(CLOSE0 - 900)
    spec = lf.entry_spec(old, "yes", D("0.95"), D("1"))
    spec["client_order_id"] = "old-1"
    spec["body"] = lf.order_body(spec, "old-1")
    coid = sim.ledger.new_intent(spec)
    sim.ledger.mark_result(coid, Result("ok", 201, {"order_id": "o-old", "client_order_id": coid,
                                                     "fill_count": "1.00", "remaining_count": "0.00"}))
    sim.ledger.upsert_fills([{"fill_id": "f-old", "order_id": "o-old", "ticker": old["ticker"], "outcome_side": "yes",
                              "count_fp": "1.00", "yes_price_dollars": "0.9500", "no_price_dollars": "0.0500",
                              "fee_cost": "0.003325", "created_time": iso(CLOSE0 - 1000)}])
    assert sim.ledger.unsettled_tickers() == [old["ticker"]]
    sim.client.known[old["ticker"]] = old
    sim.run()
    assert sim.ledger.settled_tickers() >= {old["ticker"]}
    assert sim.reconcile.settled[0] == [old["ticker"]]          # settled before anything else happened
    (pnl,) = sim.conn.execute("SELECT pnl FROM exec_settlements WHERE ticker = ?", (old["ticker"],)).fetchone()
    assert D(pnl) == -(D("0.95") + D("0.003325"))
    # the loss cap now sees it: the old loss, offset by the win in the window this run traded
    assert sim.ledger.realized_loss_total() == (D("0.95") + D("0.003325")) - (1 - D("0.95") - micro_fee("1", "0.95"))


# ---------------------------------------------------------------------------------------------- run lifecycle
def test_cancel_path_runs_at_the_end_of_a_live_run_with_the_touched_tickers(tmp_path):
    sim = live_sim(tmp_path)
    sim.run()
    ((reason, tickers),) = sim.reconcile.cancel_paths
    assert tickers == [f"KXBTC15M-{CLOSE0}"] and reason == "finished"
    assert sim.reconcile.reconciled                       # reconciled after the order


def test_cancel_path_runs_when_the_bot_crashes(tmp_path):
    sim = live_sim(tmp_path)

    def locked(**kw):
        raise RuntimeError("database is locked")

    sim.state.opportunity = locked
    with pytest.raises(RuntimeError, match="database is locked"):
        sim.run()
    assert sim.reconcile.cancel_paths and sim.reconcile.cancel_paths[0][0].startswith("error: RuntimeError")
    run = sim.state.run_row()
    assert run["end_reason"].startswith("error: RuntimeError") and run["ended_ts"] is not None
    assert sim.state.heartbeat_row()["status"] == "stopped"
    assert any(e["level"] == "error" for e in sim.events())


def test_ctrl_c_stops_cleanly_and_runs_the_cancel_path(tmp_path):
    sim = live_sim(tmp_path, books=lambda t, e: NO_QUOTE)         # many polls: the third one is interrupted
    orig = sim.client.orderbook
    n = {"i": 0}

    def interrupt(ticker):
        n["i"] += 1
        if n["i"] == 3:
            raise KeyboardInterrupt
        return orig(ticker)

    sim.client.orderbook = interrupt
    with pytest.raises(KeyboardInterrupt):
        sim.run()
    assert sim.state.run_row()["end_reason"] == "interrupted (Ctrl+C)"
    assert sim.reconcile.cancel_paths


def test_a_failing_cancel_path_or_reconcile_does_not_hide_the_result(tmp_path):
    sim = live_sim(tmp_path)
    sim.reconcile.raise_on = {"reconcile", "settle"}

    def bad_cancel(client, ledger, reason, tickers):
        raise RuntimeError("cancel path exploded")

    sim.reconcile.cancel_path = bad_cancel
    sim.run()
    assert len(sim.client.created) == 1
    assert any("reconcile failed" in e["message"] for e in sim.events("error"))
    assert any("cancel path failed" in e["message"] for e in sim.events("error"))


def test_live_heartbeat_reports_loss_balance_and_keeps_the_5_second_rule(tmp_path):
    sim = live_sim(tmp_path)
    sim.run()
    assert sim.max_beat_gap() <= 5.0
    d = sim.state.heartbeat_row()["detail"]
    assert d["mode"] == "live" and d["balance_usd"] == 29.43 and d["loss_used_usd"] == 0.0
    assert d["kill_switch"] is False and d["max_loss_usd"] == 5.0
    assert sim.client.names("balance") and sim.client.names("balance")[0][1] == (2,)


def test_live_run_leaves_no_secrets_in_any_table(tmp_path):
    sim = live_sim(tmp_path)
    sim.run()
    blob = []
    for (name,) in sim.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for row in sim.conn.execute(f"SELECT * FROM {name}").fetchall():
            blob.append(" ".join(str(v) for v in row))
    text = "\n".join(blob).lower()
    for needle in ("private key", "begin ", "signature", "kalshi-access", "authorization"):
        assert needle not in text
    assert "abcd1234" in text                              # only the fingerprint is shown


def test_orders_are_never_sent_by_a_watch_run_even_with_a_live_client_and_keys(tmp_path):
    sim = Sim(tmp_path, live=False)
    sim.client.live = True                                 # a live-capable client, but the bot is in watch mode
    sim.run()
    assert sim.client.created == [] and len(sim.opps()) == 1


def test_the_bot_makes_at_most_a_few_reads_per_second(tmp_path):
    sim = live_sim(tmp_path)
    sim.run()
    reads = [c for c in sim.client.calls if c[0] in ("orderbook", "open_markets", "market", "orders", "fills", "balance")]
    horizon = sim.clock.now - (CLOSE0 - 400)
    assert len(reads) / horizon < 1.0                      # far below the 20 reads/s Basic-tier allowance


# ---------------------------------------------------------------------------------------------- safety warm-up, halts
def test_safety_is_warmed_before_the_window_and_while_waiting_for_a_signal(tmp_path):
    sim = live_sim(tmp_path, books=lambda t, e: NO_QUOTE)        # nothing to decide on: the whole window is polled
    sim.run()
    times = [t for t, _ in sim.safety.preflights]
    start = CLOSE0 - 120
    assert times and start - lf.WARM_LEAD_S - 1.0 <= times[0] <= start
    assert all(b_ - a >= lf.WARM_EVERY_S - 1e-9 for a, b_ in zip(times, times[1:]))
    assert times[-1] <= CLOSE0 - 60 and 13 <= len(times) <= 18


def test_safety_is_not_warmed_once_the_window_is_traded(tmp_path):
    sim = live_sim(tmp_path)
    sim.run()
    assert all(t <= CLOSE0 - 120 + 0.5 for t, _ in sim.safety.preflights)      # only the warm-up before the window
    assert len(sim.client.created) == 1


def test_a_preflight_problem_is_reported_once_and_trading_goes_on(tmp_path):
    sim = live_sim(tmp_path)
    sim.safety.preflight_problems = ["market not listed"] * 5
    sim.run()
    msgs = [e["message"] for e in sim.events("error") if "preflight" in e["message"]]
    assert len(msgs) == 1 and "market not listed" in msgs[0]
    assert len(sim.client.created) == 1


@pytest.mark.parametrize("how", ["missing", "raises"])
def test_a_safety_without_a_working_preflight_still_trades(tmp_path, how):
    sim = live_sim(tmp_path)
    if how == "missing":
        sim.safety.preflight = None
    else:
        def boom(market):
            raise RuntimeError("no network")
        sim.safety.preflight = boom
    sim.run()
    assert len(sim.client.created) == 1


def test_safety_halted_elsewhere_stops_the_bot(tmp_path):
    sim = live_sim(tmp_path, books=lambda t, e: NO_QUOTE)
    orig = sim.client.orderbook
    n = {"i": 0}

    def ob(ticker):
        n["i"] += 1
        if n["i"] == 4:
            sim.safety.halted = True                 # e.g. reconcile's ledger.halt() tripped it
        return orig(ticker)

    sim.client.orderbook = ob
    summary = sim.run()
    assert summary["end_reason"] == "safety is halted" and n["i"] <= 5 and sim.client.created == []


def test_halted_may_be_a_method(tmp_path):
    sim = live_sim(tmp_path)
    sim.safety.halted = lambda: True
    summary = sim.run()
    assert sim.client.created == [] and summary["end_reason"] == "safety is halted"


def test_reconcile_runs_after_a_fill_but_not_after_a_clean_unfilled_ioc(tmp_path):
    sim = live_sim(tmp_path)
    sim.client.script = ["nofill", "nofill", "fill"]
    sim.run()
    assert len(sim.client.created) == 3 and len(sim.reconcile.reconciled) == 1
    assert sim.reconcile.reconciled[0] == [f"KXBTC15M-{CLOSE0}"]


# ---------------------------------------------------------------------------------------------- ledger settlement lag
def test_ledger_settlement_is_retried_until_the_exchange_publishes_it(tmp_path):
    t = f"KXBTC15M-{CLOSE0}"
    sim = live_sim(tmp_path, results={t: "no"})
    sim.reconcile.ledger_lag_s = 100.0                     # GET /portfolio/settlements lags the public market result
    sim.run()
    fee = micro_fee("1", "0.95")
    assert sim.ledger.settled_tickers() == {t} and sim.ledger.realized_loss_total() == D("0.95") + fee
    assert len(sim.reconcile.settled) >= 5                 # asked again every settle_poll_s until it was there
    early, late = sim.reconcile.snapshots[0], sim.reconcile.snapshots[-1]
    assert early[1] == D("0.95") + fee and early[2] == 0   # meanwhile the trade counts as OPEN worst case
    assert late[1] == 0 or late[2] == 0                    # (the last call recorded it)
    (opp,) = sim.opps()
    assert opp["realized_pnl"] == pytest.approx(-float(D("0.95") + fee))   # the dashboard row does not wait for it
    first = json.loads(sim.events("settle")[0]["data_json"])
    assert first["ledger_recorded"] is False
    assert not [e for e in sim.events("settle") if e["level"] == "error"]  # and the cross-check passed at the end
    assert not sim.bot._ledger_todo


def skew_ledger_settlement(sim, pnl: str = "0.5000") -> None:
    """Whatever reconcile.settle records, the ledger's settlement row then says `pnl` (a disagreement to be reported)."""
    orig = sim.reconcile.settle

    def skewed(client, ledger, tickers):
        orig(client, ledger, tickers)
        ledger.conn.execute("UPDATE exec_settlements SET pnl = ?", (pnl,))
        ledger.conn.commit()

    sim.reconcile.settle = skewed


def test_the_cross_check_reports_a_pnl_the_ledger_disagrees_with(tmp_path):
    t = f"KXBTC15M-{CLOSE0}"
    sim = live_sim(tmp_path)
    skew_ledger_settlement(sim)
    sim.run()
    (err,) = [e for e in sim.events("settle") if e["level"] == "error"]
    assert t in err["message"] and "vs ledger settlement 0.5000" in err["message"]
    assert sim.state.heartbeat_row()["detail"]["last_error"] == err["message"]     # and the dashboard keeps showing it
    (opp,) = sim.opps()
    assert opp["realized_pnl"] == pytest.approx(float(1 - D("0.95") - micro_fee("1", "0.95")))   # our own figure stands


def test_the_cross_check_stays_quiet_when_another_order_also_traded_the_market(tmp_path):
    # the ledger's settlement revenue is paid on the NET position of the ticker, so with someone else's order on the
    # same market (another strategy, an earlier run) it is not comparable with this bot's own P&L
    t = f"KXBTC15M-{CLOSE0}"
    sim = live_sim(tmp_path)
    place = sim.client.create_order

    def place_then_someone_else_trades(body):
        res = place(body)
        seed_trade(sim.ledger, 1, ticker=t, price="0.60", outcome="no")     # after ours: a window with an order is skipped
        return res

    sim.client.create_order = place_then_someone_else_trades
    skew_ledger_settlement(sim)
    sim.run()
    assert len(sim.ledger.fills_for(t)) == 2
    assert not [e for e in sim.events("settle") if e["level"] == "error"]
    assert sim.state.heartbeat_row()["detail"]["last_error"] is None
    (opp,) = sim.opps()                                    # and the bot still books exactly its own order
    assert opp["live_cost"] == pytest.approx(0.95) and opp["realized_pnl"] == pytest.approx(
        float(1 - D("0.95") - micro_fee("1", "0.95")))


def test_a_ledger_that_never_publishes_is_left_for_a_later_run_without_stalling_forever(tmp_path):
    t = f"KXBTC15M-{CLOSE0}"
    sim = live_sim(tmp_path, results={t: "yes"}, lf_cfg={"settle_wait_s": 120})
    sim.reconcile.ledger_lag_s = 10 ** 9
    sim.run()
    assert sim.ledger.settled_tickers() == set() and sim.ledger.open_worst_case() > 0
    assert any("not recorded by the ledger" in e["message"] for e in sim.events("settle"))
    assert sim.clock.now < CLOSE0 + 300                    # waited settle_wait_s, not forever
    # a later run (same database, the exchange has published by now) settles it at startup
    sim2 = Sim(tmp_path, live=True, results={t: "yes"}, start_offset=3600, conn=sim.conn, run_id="run-2",
               books=lambda tk, e: DEAD)
    sim2.run(hours=0.02)
    assert sim2.ledger.settled_tickers() == {t}
    assert sim2.ledger.open_worst_case() == 0
    assert sim2.ledger.realized_pnl() == 1 - D("0.95") - micro_fee("1", "0.95")
    assert sim2.reconcile.settled and sim2.reconcile.settled[0] == [t]
    assert any("left over from an earlier run" in e["message"] for e in sim2.events("settle"))


def test_a_window_without_fills_is_not_queued_for_ledger_settlement(tmp_path):
    sim = live_sim(tmp_path)
    sim.client.script = ["nofill"] * 3
    sim.run()
    assert sim.reconcile.settled == [] and not sim.bot._ledger_todo
    assert sim.clock.now < CLOSE0 + 140                    # the drain ended at the paper twin's settlement
    assert sim.opps()[0]["paper_realized_pnl"] is not None


def test_an_unknown_order_without_fills_does_not_stall_the_shutdown(tmp_path):
    sim = live_sim(tmp_path)
    sim.client.script = ["timeout_lost"]
    sim.run()
    assert sim.reconcile.settled == [] and sim.clock.now < CLOSE0 + 140
    assert sim.state.heartbeat_row()["detail"]["last_error"]       # a stopped run keeps showing why it stopped


def test_exact_fills_are_read_at_settlement_when_they_were_not_visible_after_the_order(tmp_path):
    sim = live_sim(tmp_path)
    sim.client.fills_after = CLOSE0 - 30                  # REST fills lag: nothing visible when the order is placed
    orig = sim.client.fills

    def better(**kw):
        out = orig(**kw)
        for f in out:
            f["yes_price_dollars"], f["no_price_dollars"], f["fee_cost"] = "0.9490", "0.0510", "0.003400"
        return out

    sim.client.fills = better
    sim.run()
    (opp,) = sim.opps()
    assert opp["live_cost"] == pytest.approx(0.949) and opp["live_fees"] == pytest.approx(0.0034)
    assert opp["realized_pnl"] == pytest.approx(1 - 0.949 - 0.0034)
    (o,) = sim.orders()
    assert o["avg_fill_price"] == pytest.approx(0.949) and o["fees"] == pytest.approx(0.0034)
    assert sim.ledger.fills_for(f"KXBTC15M-{CLOSE0}")[0]["yes_price"] == "0.9490"
    assert not [e for e in sim.events("settle") if e["level"] == "error"]


def test_a_kill_stops_without_waiting_for_the_last_settlement(tmp_path):
    sim = Sim(tmp_path, results={f"KXBTC15M-{CLOSE0}": "yes"})
    orig = sim.client.open_markets

    def open_markets(series):
        if sim.clock.now >= CLOSE0 - 30:                   # the user presses STOP after the window, before settlement
            sim.kill_file.write_text("stop\n")
        return orig(series)

    sim.client.open_markets = open_markets
    summary = sim.run()
    assert summary["end_reason"] == "kill switch"
    assert sim.opps()[0]["paper_realized_pnl"] is None     # not settled: the user asked for a stop
    assert sim.clock.now < CLOSE0


def test_a_safety_trip_writes_the_kill_file_but_the_drain_still_books_the_last_paper_twin(tmp_path):
    sim = live_sim(tmp_path)
    seed_loss(sim.ledger, 5)
    sim.run()                                              # the cap trips at the window; the kill file now exists
    assert sim.kill_file.exists() and sim.safety.halted and sim.client.created == []
    assert sim.opps()[0]["paper_realized_pnl"] is not None             # the drain ran: it only reads


# ---------------------------------------------------------------------------------------------- recovery of earlier runs
def seed_run(conn, run_id: str, *, strategy: str = "late_favorite", mode: str = "watch", started: float = CLOSE0 - 90_000):
    st = BotState(conn, run_id)
    st.start(strategy, mode, {}, ts=started)
    return st


def seed_opp(st: BotState, close: int, kind: str, *, detected: float | None = None, live: bool = False,
             cost: float = 0.95, fees: float = 0.003325, outcome: str | None = None) -> int:
    t = f"KXBTC15M-{close}"
    return st.opportunity(detected_ts=close - 100 if detected is None else detected, hour_ticker=t, strike_ticker=t,
                          kind=kind, contracts=1, paper_cost=cost, paper_fees=fees, paper_edge=None, live=live,
                          outcome=outcome)


def test_paper_twins_an_earlier_run_left_pending_are_settled_at_startup(tmp_path):
    day = CLOSE0 - 86_400
    sim = Sim(tmp_path, results={f"KXBTC15M-{day}": "yes", f"KXBTC15M-{day + 900}": "yes", f"KXBTC15M-{CLOSE0}": "yes"})
    old = seed_run(sim.conn, "run-old")
    a = seed_opp(old, day, lf.KIND_YES, outcome="paper_only")                          # won
    b = seed_opp(old, day + 900, lf.KIND_NO, cost=0.96, fees=0.0027, outcome="paper_only")   # lost: the market said YES
    done = seed_opp(old, day + 1800, lf.KIND_YES, outcome="paper_only")
    old.update_opportunity(done, paper_realized_pnl=0.5)                                # already settled: left alone
    young = seed_opp(old, CLOSE0 - 400, lf.KIND_YES, detected=CLOSE0 - 450 + 300, outcome="paper_only")   # < 10 minutes old
    weird = seed_opp(old, day + 2700, "pair kind", outcome="paper_only")                # not one of ours to read
    other = seed_run(sim.conn, "run-pair", strategy="pair_h2")
    pair = seed_opp(other, day + 3600, lf.KIND_YES, outcome="paper_only")               # another strategy's run
    sim.run()
    cur = sim.conn.execute("SELECT * FROM bot_opportunities")
    names = [d[0] for d in cur.description]
    rows = {r[0]: dict(zip(names, r)) for r in cur.fetchall()}
    assert rows[a]["paper_realized_pnl"] == pytest.approx(1 - 0.95 - 0.003325)
    assert rows[b]["paper_realized_pnl"] == pytest.approx(-0.96 - 0.0027)
    assert rows[done]["paper_realized_pnl"] == 0.5
    assert rows[young]["paper_realized_pnl"] is None and rows[weird]["paper_realized_pnl"] is None
    assert rows[pair]["paper_realized_pnl"] is None
    assert sim.bot.summary["recovered"] == 2
    assert sim.bot.summary["settled"] == 1                  # this run's own window, counted separately
    assert sim.bot.summary["paper_pnl"] == 1 - D("0.95") - micro_fee("1", "0.95")      # recovered rows stay out of it
    assert any("left over from an earlier run" in e["message"] for e in sim.events("settle"))


def test_a_pending_row_whose_market_has_no_result_is_left_for_the_next_run(tmp_path):
    day = CLOSE0 - 86_400
    sim = Sim(tmp_path)
    old = seed_run(sim.conn, "run-old")
    a = seed_opp(old, day, lf.KIND_YES, outcome="paper_only")
    sim.run()
    assert old.opportunity_row(a)["paper_realized_pnl"] is None


def test_live_trades_an_earlier_run_left_pending_get_their_realized_pnl(tmp_path):
    day = CLOSE0 - 86_400
    sim = live_sim(tmp_path, results={f"KXBTC15M-{day}": "no", f"KXBTC15M-{CLOSE0}": "yes"})
    old = seed_run(sim.conn, "run-old", mode="live")
    a = seed_opp(old, day, lf.KIND_YES, live=True, outcome="filled")
    old.order(client_order_id="o-old-1", ticker=f"KXBTC15M-{day}", side="yes", action="buy", count=1, limit_price=0.95,
              tif="ioc", status="executed", opportunity_id=a, ts_sent=day - 100, order_id="e-1", filled=1.0,
              avg_fill_price=0.949, fees=0.0034, dry_run=False)
    unknown = seed_opp(old, day + 900, lf.KIND_YES, live=True, outcome="unknown")
    sim.run()
    row = old.opportunity_row(a)
    assert row["payout"] == 0.0 and row["realized_pnl"] == pytest.approx(-0.949 - 0.0034)
    assert row["live_cost"] == pytest.approx(0.949) and row["live_fees"] == pytest.approx(0.0034)
    assert row["paper_realized_pnl"] == pytest.approx(-0.95 - 0.003325)
    assert old.opportunity_row(unknown)["realized_pnl"] is None       # an unknown outcome has no P&L to book
    assert sim.bot.summary["live_pnl"] == 1 - D("0.95") - micro_fee("1", "0.95")   # only this run's trade


def test_realized_loss_at_the_cap_after_an_order_trips_safety(tmp_path):
    """The second line of defence: the loss booked after a trade (a fee above the estimate, say) reaches the cap."""
    sim = live_sim(tmp_path)
    state = {"loss": D("0")}
    sim.ledger.realized_loss_total = lambda: state["loss"]
    sim.ledger.realized_loss_24h = lambda: state["loss"]
    orig = sim.client.create_order

    def create(body):
        res = orig(body)
        state["loss"] = D("5.01")
        return res

    sim.client.create_order = create
    summary = sim.run()
    assert len(sim.client.created) == 1 and sim.safety.halted
    assert "reached the 5.0 cap" in sim.safety.trips[0] and "reached" in summary["end_reason"]


def test_a_retry_buys_the_same_outcome_as_the_first_detection(tmp_path):
    # the window detects YES, the IOC does not fill, and one poll later the book looks like a NO favourite
    sim = live_sim(tmp_path, books=lambda t, e: FAV_YES if e < 781 else FAV_NO)
    sim.client.script = ["nofill", "fill"]
    sim.run()
    assert len(sim.client.created) == 1 and sim.client.created[0]["side"] == "bid"      # never a NO order
    assert any("locked to yes" in e["message"] for e in sim.events("error"))
    (opp,) = sim.opps()
    assert opp["kind"] == "late favorite YES" and opp["outcome"] == "none_filled"


# ============================================================================================ the real Safety and reconcile
def test_full_stack_a_winning_trade_end_to_end(tmp_path):
    t = f"KXBTC15M-{CLOSE0}"
    sim = real_stack(live_sim(tmp_path, with_safety=False))
    summary = sim.run()
    assert len(sim.client.created) == 1 and summary["end_reason"] == "finished"
    (row,) = sim.ledger.orders_for(t)
    assert row["status"] == "executed" and row["purpose"] == "entry"
    fee = micro_fee("1", "0.95")
    assert sim.ledger.settled_tickers() == {t}
    assert sim.ledger.realized_pnl() == 1 - D("0.95") - fee            # recorded by the real settle()
    assert sim.ledger.open_worst_case() == 0 and sim.ledger.realized_loss_total() == 0
    (opp,) = sim.opps()
    assert opp["outcome"] == "filled" and opp["realized_pnl"] == pytest.approx(float(1 - D("0.95") - fee))
    assert not [e for e in sim.events() if e["level"] == "error"], [e["message"] for e in sim.events("error")]
    assert not sim.safety.halted and not sim.kill_file.exists()
    # the real cancel path ran at the end and found nothing resting
    audit = [r[0] for r in sim.conn.execute("SELECT action FROM audit_log WHERE component = 'exec.reconcile'")]
    assert "cancel_path" in audit and "reconcile" in audit and "settle" in audit
    assert [c[0] for c in sim.client.calls if c[0] in ("cancel_order", "cancel_all")] == []


def test_full_stack_the_no_side_passes_the_real_safety_checks(tmp_path):
    sim = real_stack(live_sim(tmp_path, with_safety=False, books=lambda t, e: FAV_NO if e >= 700 else DEAD,
                              results={f"KXBTC15M-{CLOSE0}": "no"}))
    sim.run()
    (body,) = sim.client.created
    assert body["side"] == "ask" and body["price"] == "0.0400"
    assert sim.opps()[0]["realized_pnl"] == pytest.approx(1 - 0.96 - float(micro_fee("1", "0.96")))
    assert not [e for e in sim.events() if e["level"] == "error"]


def test_full_stack_an_unfilled_ioc_is_retried_through_the_real_safety(tmp_path):
    sim = real_stack(live_sim(tmp_path, with_safety=False))
    sim.client.script = ["nofill", "fill"]
    sim.run()
    assert len(sim.client.created) == 2 and sim.opps()[0]["outcome"] == "filled"
    assert [r["status"] for r in sim.ledger.orders_for(f"KXBTC15M-{CLOSE0}")] == ["canceled", "executed"]
    assert not sim.events("cap")                                  # Safety never refused the retry (no 'still open' order)


def test_full_stack_a_losing_trade_updates_the_cap_through_the_real_ledger_path(tmp_path):
    sim = real_stack(live_sim(tmp_path, with_safety=False, results={f"KXBTC15M-{CLOSE0}": "no"}))
    sim.run()
    assert sim.ledger.realized_loss_total() == D("0.95") + micro_fee("1", "0.95")
    assert sim.safety.loss_used() == D("0.95") + micro_fee("1", "0.95")
    assert sim.opps()[0]["realized_pnl"] == pytest.approx(-float(D("0.95") + micro_fee("1", "0.95")))


def test_full_stack_settlement_lag_is_handled_by_the_real_settle(tmp_path):
    t = f"KXBTC15M-{CLOSE0}"
    sim = real_stack(live_sim(tmp_path, with_safety=False, results={t: "no"}))
    sim.client.settlement_lag_s = 100.0                          # GET /portfolio/settlements lags the market result
    sim.run()
    assert sim.ledger.settled_tickers() == {t} and not sim.bot._ledger_todo
    assert sim.ledger.realized_loss_total() == D("0.95") + micro_fee("1", "0.95")
    assert len(sim.client.names("settlements")) >= 5            # asked again until the exchange published it
    assert not [e for e in sim.events() if e["level"] == "error"]


def test_full_stack_a_warm_safety_makes_the_first_check_fresh_enough(tmp_path):
    # every call takes 0.5 s; Safety's first check needs ~5 reads (listing, status, cash, position, orders)
    warm = real_stack(live_sim(tmp_path / "w", with_safety=False, latency=0.5), reconcile=False)
    warm.reconcile = FakeReconcile(warm.clock)
    warm.ctx.reconcile = warm.reconcile
    warm.run()
    assert len(warm.client.created) == 1 and not warm.events("cap")          # warmed by preflight(): no refusal
    assert warm.ledger.orders_for(f"KXBTC15M-{CLOSE0}")[0]["purpose"] == "entry"


def test_full_stack_without_preflight_the_first_check_is_refused_as_stale_then_passes(tmp_path):
    cold = real_stack(live_sim(tmp_path / "c", with_safety=False, latency=0.5), reconcile=False)
    cold.reconcile = FakeReconcile(cold.clock)
    cold.ctx.reconcile = cold.reconcile
    cold.safety.preflight = None                                              # no warm-up
    cold.run()
    caps = cold.events("cap")
    assert caps and "old" in caps[0]["message"] and "stale" in caps[0]["message"]
    assert len(cold.client.created) == 1                                      # the next poll got through (caches warm)
    assert cold.ledger.orders_for(f"KXBTC15M-{CLOSE0}")[0]["purpose"] == "entry"   # a refusal is not an attempt


def test_full_stack_real_safety_halted_blocks_the_order(tmp_path):
    sim = real_stack(live_sim(tmp_path, with_safety=False))
    sim.safety.trip("test: stop everything")
    summary = sim.run()
    assert sim.client.created == [] and sim.kill_file.exists()
    assert summary["end_reason"] in ("kill switch present at start", "safety is halted")


def test_full_stack_an_unknown_outcome_is_found_by_the_real_reconcile_and_not_resent(tmp_path):
    t = f"KXBTC15M-{CLOSE0}"
    sim = real_stack(live_sim(tmp_path, with_safety=False))
    sim.client.script = ["timeout_placed", "fill"]
    sim.run()
    assert len(sim.client.created) == 1
    (row,) = sim.ledger.orders_for(t)
    assert row["status"] == "executed" and row["order_id"] == "ord-1"
    assert sim.opps()[0]["outcome"] == "filled" and not sim.safety.halted
    assert not [e for e in sim.events() if e["level"] == "error"], [e["message"] for e in sim.events("error")]


def test_full_stack_an_unknown_outcome_that_never_appears_halts_the_real_safety(tmp_path):
    sim = real_stack(live_sim(tmp_path, with_safety=False))
    sim.client.script = ["timeout_lost", "fill"]
    summary = sim.run()
    assert len(sim.client.created) == 1 and sim.safety.halted and sim.kill_file.exists()
    assert "unknown" in summary["end_reason"] and sim.opps()[0]["outcome"] == "unknown"
    # the real Safety now refuses everything, including a new run: an unresolved order is on the books
    ok, why = sim.safety.check_entry(lf.entry_spec(make_market(CLOSE0 + 900), "yes", D("0.95"), D("1")), {}, {})
    assert ok is False


# ============================================================================================ review: the last look before a send
def test_a_kill_file_that_appears_after_the_intent_was_committed_prevents_the_send(tmp_path):
    """Review finding: four SQLite commits (each able to wait for a lock) sit between the last kill/Safety check and the
    send, and nothing looked again. Here STOP is pressed while the intent is being committed: nothing may be sent."""
    t = f"KXBTC15M-{CLOSE0}"
    sim = live_sim(tmp_path)
    orig = sim.ledger.new_intent

    def commit_then_stop(spec):
        coid = orig(spec)                                  # committed ...
        sim.kill_file.write_text("stop\n")                 # ... and the user presses STOP before the order goes out
        return coid

    sim.ledger.new_intent = commit_then_stop
    summary = sim.run()
    assert sim.client.created == [] and summary["end_reason"] == "kill switch"
    (row,) = sim.ledger.orders_for(t)
    assert row["status"] == "presumed_not_placed" and "not sent: kill switch" in row["error"]
    assert sim.ledger.open_worst_case() == 0 and sim.ledger.unresolved_orders() == []     # no phantom exposure, nothing to reconcile
    (o,) = sim.orders()
    assert (o["status"], o["dry_run"], o["ts_sent"]) == ("not_sent", 1, None)              # not shown as a real order
    (opp,) = sim.opps()
    assert opp["live"] == 0 and opp["outcome"] == "blocked_by_cap"
    assert summary["live_attempts"] == 0 and any("kill switch" in e["message"] for e in sim.events("cap"))


def test_a_commit_that_stalls_past_the_window_or_the_books_age_is_not_sent_but_the_next_poll_may_be(tmp_path):
    """The reviewer's stall: the commits wait for a lock and the order would leave 5 s after its book was read (or after
    the window closed). Nothing stale is sent; with time left, the next poll prices a fresh book and may send."""
    t = f"KXBTC15M-{CLOSE0}"
    sim = live_sim(tmp_path)
    orig = sim.ledger.mark_sent
    state = {"n": 0}

    def slow_mark_sent(ids, ts=None):
        state["n"] += 1
        if state["n"] == 1:
            sim.clock.now += 5.0                           # the database was locked for 5 s
        return orig(ids, ts)

    sim.ledger.mark_sent = slow_mark_sent
    summary = sim.run()
    assert len(sim.client.created) == 1                    # only the second attempt, from a fresh book, was sent
    first, second = sim.ledger.orders_for(t)
    assert first["status"] == "presumed_not_placed" and "order book is 5.0 s old" in first["error"]
    assert second["status"] == "executed" and second["purpose"] == "retry"
    assert [o["status"] for o in sim.orders()] == ["not_sent", "executed"]
    (opp,) = sim.opps()
    assert opp["outcome"] == "filled" and opp["live"] == 1 and summary["live_attempts"] == 1


def test_a_commit_that_stalls_until_the_window_is_over_sends_nothing(tmp_path):
    t = f"KXBTC15M-{CLOSE0}"
    sim = live_sim(tmp_path)
    orig = sim.ledger.mark_sent

    def slow_mark_sent(ids, ts=None):
        sim.clock.now += 70.0                              # a 70 s lock wait: 25 s before the close
        return orig(ids, ts)

    sim.ledger.mark_sent = slow_mark_sent
    summary = sim.run()
    assert sim.client.created == [] and summary["live_attempts"] == 0
    (row,) = sim.ledger.orders_for(t)
    assert row["status"] == "presumed_not_placed" and "decision window closed" in row["error"]
    assert sim.opps()[0]["outcome"] == "blocked_by_cap" and sim.opps()[0]["live"] == 0
    assert any("decision window closed" in e["message"] for e in sim.events("cap"))


def test_an_intent_whose_send_time_could_not_be_stamped_is_not_sent(tmp_path):
    t = f"KXBTC15M-{CLOSE0}"
    sim = live_sim(tmp_path)
    orig = sim.ledger.mark_sent
    state = {"n": 0}

    def failing_mark_sent(ids, ts=None):
        state["n"] += 1
        if state["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return orig(ids, ts)

    sim.ledger.mark_sent = failing_mark_sent
    sim.run()
    first, second = sim.ledger.orders_for(t)
    assert first["status"] == "presumed_not_placed" and "could not stamp the send time" in first["error"]
    assert len(sim.client.created) == 1 and second["status"] == "executed"
    assert sim.client.created[0]["client_order_id"] == second["client_order_id"]       # the unstamped intent was never sent


def test_safety_halting_while_the_intent_is_committed_prevents_the_send(tmp_path):
    sim = live_sim(tmp_path)
    orig = sim.ledger.new_intent

    def commit_then_halt(spec):
        coid = orig(spec)
        sim.safety.halted = True                           # e.g. a reconcile mismatch arrived on another thread
        return coid

    sim.ledger.new_intent = commit_then_halt
    summary = sim.run()
    assert sim.client.created == [] and summary["end_reason"] == "safety is halted"
    assert sim.state.heartbeat_row()["detail"]["last_error"]                  # the run says why it stopped


def test_the_send_still_goes_out_when_nothing_changed_between_the_checks_and_the_send(tmp_path):
    sim = live_sim(tmp_path, latency=0.3)                  # slow calls, but the book is read right before the checks
    sim.run()
    assert len(sim.client.created) == 1 and sim.opps()[0]["outcome"] == "filled"


# ============================================================================================ review: a 401 stops the bot
def test_a_401_on_the_balance_read_trips_safety_and_stops_the_bot(tmp_path):
    from kpm.execution.client import AuthError
    sim = live_sim(tmp_path)
    sim.client.fail["balance"] = AuthError("GET /trade-api/v2/portfolio/balance: HTTP 401", status=401)
    summary = sim.run()
    assert sim.safety.halted and "authentication refused" in sim.safety.trips[0] and "balance" in sim.safety.trips[0]
    assert "authentication refused" in summary["end_reason"] and sim.client.created == []
    assert "authentication refused" in sim.state.heartbeat_row()["detail"]["last_error"]
    assert len(sim.client.names("balance")) == 1                              # no loop: it stopped at the first 401


def test_a_401_on_the_fills_read_trips_safety_too(tmp_path):
    from kpm.execution.client import AuthError
    sim = live_sim(tmp_path)

    def refuse(**kw):
        raise AuthError("GET /trade-api/v2/portfolio/fills: HTTP 403", status=403)

    sim.client.fills = refuse
    sim.run()
    assert sim.safety.halted and "authentication refused" in sim.safety.trips[0] and "fills" in sim.safety.trips[0]


def test_full_stack_a_401_on_one_of_safetys_reads_stops_the_run_instead_of_refusing_for_ever(tmp_path):
    """Review finding: a revoked key (or a clock past Kalshi's tolerance) made every signed read answer 401. Safety turned
    that into an ordinary refusal, the bot retried every few seconds in every window, last_error stayed empty and the run
    ended 'finished' with exit code 0."""
    from kpm.execution.client import AuthError
    sim = real_stack(live_sim(tmp_path, with_safety=False))
    sim.client.fail["positions"] = AuthError("GET /trade-api/v2/portfolio/positions: HTTP 401", status=401)
    summary = sim.run(hours=0.5)
    assert sim.client.created == [] and sim.safety.halted and sim.kill_file.exists()
    assert summary["end_reason"] in ("safety is halted", "kill switch")        # (Safety's trip writes the kill file)
    assert "authentication refused" in sim.state.heartbeat_row()["detail"]["last_error"]
    assert "safety trip: authentication refused" in sim.kill_file.read_text()
    assert len(sim.client.names("positions")) == 1                           # one 401, then nothing: it does not keep asking
    assert summary["windows"] == 1                                            # and it did not go on to the next window


# ============================================================================================ review: the PC clock during a run
def test_the_bot_keeps_the_clock_check_alive_while_it_idles(tmp_path):
    sim = live_sim(tmp_path)
    sim.run()
    polls = sim.safety.clock_polls
    assert len(polls) > 50 and polls[0] < CLOSE0 - 120                         # polled all through the wait for the window
    assert sim.state.heartbeat_row()["detail"]["clock_skew_s"] == pytest.approx(0.1)


def test_a_clock_problem_is_reported_in_the_heartbeat_and_warned_about_once(tmp_path):
    sim = live_sim(tmp_path)
    sim.safety.clock_problems = ["clock skew +25.00 s vs Kalshi is over the 2 s limit"] * 1000
    sim.run()
    errs = [e["message"] for e in sim.events("error") if "PC clock" in e["message"]]
    assert 1 <= len(errs) <= 3 and "+25.00" in errs[0]                         # re-warned every 5 minutes, not every second
    assert "PC clock" in sim.state.heartbeat_row()["detail"]["last_error"]


def test_full_stack_a_pc_clock_that_drifted_stops_every_order_until_it_is_right_again(tmp_path):
    """Review finding: the clock gate ran once, at startup. Here the clock is 25 s off by the time the window opens and is
    resynced with 40 s of it left: no order goes out in between (nothing trips: it recovers by itself), and the order goes
    out as soon as the next measurement says the clock is right."""
    sim = real_stack(live_sim(tmp_path, with_safety=False, trading={"clock_recheck_s": 30}))
    sim.client.skew_s = 25.0                                                   # drifted since the startup measurement
    orig = sim.client.orderbook

    def book(ticker):
        if sim.clock.now >= CLOSE0 - 100:
            sim.client.skew_s = 0.2                                            # Windows resynced the clock
        return orig(ticker)

    sim.client.orderbook = book
    summary = sim.run()
    assert len(sim.client.created) == 1 and not sim.safety.halted and not sim.kill_file.exists()
    (sent_at,) = [c[2] for c in sim.client.calls if c[0] == "create_order"]
    assert CLOSE0 - 100 <= sent_at <= CLOSE0 - 60                              # only after the clock was right again
    assert any("clock skew +25.00" in e["message"] for e in sim.events("cap"))  # the refusals said why
    assert any("PC clock" in e["message"] for e in sim.events("error"))
    assert sim.opps()[0]["outcome"] == "filled" and summary["end_reason"] == "finished"


def test_full_stack_a_pc_clock_that_stays_wrong_sends_nothing_and_is_not_a_trip(tmp_path):
    sim = real_stack(live_sim(tmp_path, with_safety=False, trading={"clock_recheck_s": 30}))
    sim.client.skew_s = 25.0
    sim.run()
    assert sim.client.created == [] and not sim.safety.halted
    (opp,) = sim.opps()
    assert opp["outcome"] == "blocked_by_cap" and opp["paper_cost"] == pytest.approx(0.95)        # the paper twin is still written
    assert any("clock skew +25.00" in e["message"] for e in sim.events("cap"))


# ============================================================================================ review: a wall clock that steps back
def test_a_wall_clock_that_steps_backwards_never_causes_one_long_sleep(tmp_path):
    """Review finding: the poll's sleep was computed from a wall-clock t0, so a backward step during a poll became ONE
    sleep as long as the step: no kill-file check and no heartbeat for that long (the run looked silent)."""
    sim = Sim(tmp_path, books=lambda t, e: NO_QUOTE)
    orig = sim.client.orderbook
    n = {"i": 0}

    def book(ticker):
        n["i"] += 1
        if n["i"] == 3:
            sim.clock.now -= 300.0                                              # Windows resynced the clock: 5 min back
            sim.kill_file.write_text("stop\n")                                  # and the user presses STOP right then
        return orig(ticker)

    sim.client.orderbook = book
    summary = sim.run()
    assert summary["end_reason"] == "kill switch"
    assert max(sim.clock.sleeps) <= lf.TICK_S + 1e-9                           # never one 300 s sleep with the kill file unwatched
    assert n["i"] <= 4                                                          # and STOP was noticed at the very next poll


def test_the_heartbeat_keeps_beating_after_the_wall_clock_steps_back(tmp_path):
    sim = Sim(tmp_path, books=lambda t, e: NO_QUOTE)
    orig = sim.client.orderbook
    n = {"i": 0, "stepped_at": None}

    def book(ticker):
        n["i"] += 1
        if n["i"] == 3:
            sim.clock.now -= 300.0
            n["stepped_at"] = sim.clock.now
        return orig(ticker)

    sim.client.orderbook = book
    sim.run()
    t0 = n["stepped_at"]
    beats = [t for t, s in sim.state.beats if t0 <= t <= t0 + 10.0 and s == "monitoring"]
    assert len(beats) >= 4                                                      # one every 2 s, not silence until the clock catches up


# ============================================================================================ the REAL ExecClient over a fake transport
# The bots only ever see the ExecClient surface. These tests run late_favorite on the real client, with the transport
# replaying the PUBLIC captures from 2026-10-05 (real KXBTC15M market listing, a real order book with sub-cent levels), so
# the response parsing, the endpoint allowlist, signing and the wire format of the order are the production code paths.
from .conftest import FakeTransport, TEST_KEY_ID, fixture_json, respond           # noqa: E402

REAL_TICKER = "KXBTC15M-26OCT051430-30"
REAL_CLOSE = 1_791_225_000                       # 2026-10-05T18:30:00Z, the close of the captured market
REAL_ASK = D("0.947")                            # captured book at ~18:25: best YES bid 0.946, best NO bid 0.053


def real_transport(host: str, clock, *, result: str | None = "yes", forbid_writes: bool = False) -> FakeTransport:
    t = FakeTransport(host=host, clock=clock, forbid_writes=forbid_writes)
    t.get("/markets", fixture="markets_KXBTC15M_open_1430.json",
          params=lambda p: bool(p) and p.get("series_ticker") == "KXBTC15M" and p.get("status") == "open")
    market = fixture_json("market_KXBTC15M-26OCT051430-30.json")
    settled = {"market": {**market["market"], "status": "finalized", "result": result or ""}}
    # first read: the prepare step before the window; every later read happens after the close
    t.get(f"/markets/{REAL_TICKER}", respond(200, json=market), respond(200, json=settled))
    t.get(f"/markets/{REAL_TICKER}/orderbook", fixture="orderbook_KXBTC15M-26OCT051430-30_full_subcent.json")
    return t


def real_bot(tmp_path, client, state, *, live: bool, ledger=None, safety=None, reconcile=None, clock=None):
    cfg = {"trading": {"enabled": True, "max_loss_usd": 5.0, "max_cost_per_order_usd": 1.0, "max_contracts_per_order": 1,
                       "kill_switch_file": str(tmp_path / "KILL")}}
    ctx = lf.LateContext(client=client, state=state, ledger=ledger, safety=safety, reconcile=reconcile,
                         env_name="prod", key_fp="abcd1234")
    return lf.LateFavoriteBot(ctx, cfg, live=live, clock=clock.time, sleep=clock.sleep, kill_file=tmp_path / "KILL")


def test_real_client_watch_mode_reads_public_data_only_and_needs_no_keys(tmp_path, make_client, watch_env, fake_clock):
    t = real_transport(watch_env.rest_host, fake_clock, forbid_writes=True)
    client = make_client(t, watch_env)                                   # no keys, not live
    fake_clock.now = REAL_CLOSE - 210
    conn = sqlite3.connect(":memory:")
    state = RecState(conn, "run-real", clock=fake_clock.time)
    summary = real_bot(tmp_path, client, state, live=False, clock=fake_clock).run(hours=0.1)
    assert summary["end_reason"] == "finished" and summary["detections"] == 1 and summary["settled"] == 1
    (opp,) = state.opportunity_rows()
    assert (opp["hour_ticker"], opp["kind"], opp["outcome"]) == (REAL_TICKER, "late favorite YES", "paper_only")
    assert opp["paper_cost"] == pytest.approx(float(REAL_ASK))                       # 1 - 0.053, from the sub-cent book
    assert opp["paper_fees"] == pytest.approx(float(micro_fee("1", "0.947")))
    assert opp["detected_ts"] == pytest.approx(REAL_CLOSE - 120, abs=1.0)
    assert opp["paper_realized_pnl"] == pytest.approx(float(1 - REAL_ASK - micro_fee("1", "0.947")))   # the market said YES
    (o,) = state.order_rows()
    would = json.loads(o["response_json"])["would_send"]
    assert would == {"ticker": REAL_TICKER, "client_order_id": would["client_order_id"], "side": "bid", "price": "0.9470",
                     "count": "1.00", "time_in_force": "immediate_or_cancel",
                     "self_trade_prevention_type": "taker_at_cross", "exchange_index": 2}
    # the transport saw only unsigned public GETs on the allowlisted host
    assert t.calls and all(c.method == "GET" and not c.signed and c.host == watch_env.rest_host for c in t.calls)
    t.assert_no_writes()
    assert {c.endpoint for c in t.calls} == {"/markets", f"/markets/{REAL_TICKER}", f"/markets/{REAL_TICKER}/orderbook"}


def test_real_client_smoke_pass_on_the_recorded_market(tmp_path, make_client, watch_env, fake_clock):
    t = real_transport(watch_env.rest_host, fake_clock, forbid_writes=True)
    client = make_client(t, watch_env)
    fake_clock.now = REAL_CLOSE - 100                                    # inside the decision window
    state = RecState(sqlite3.connect(":memory:"), "run-smoke", clock=fake_clock.time)
    summary = real_bot(tmp_path, client, state, live=False, clock=fake_clock).run(smoke=True)
    s = summary["smoke"]
    assert s["found"] and s["ticker"] == REAL_TICKER and s["in_decision_window"] and s["would_trade"]
    assert s["rule"]["outcome"] == "yes" and s["rule"]["ask"] == REAL_ASK and s["rule"]["size"] == D("924.83")
    assert s["book"]["yes_bid"] == D("0.946") and s["book"]["mid"] == D("0.9465")
    assert [c.endpoint for c in t.calls] == ["/markets", f"/markets/{REAL_TICKER}/orderbook"]
    assert state.opportunity_rows() == [] and state.order_rows() == []


def live_real(tmp_path, make_client, prod_env, fake_clock, *, result="yes"):
    """A live bot on the real client: real Ledger (also the client's audit sink), fake Safety and reconcile."""
    t = real_transport(prod_env.rest_host, fake_clock, result=result)
    conn = sqlite3.connect(":memory:")
    ledger = Ledger(conn, "run-real", "prod", clock=fake_clock.time, log_path=tmp_path / "exec.jsonl",
                    redact=[TEST_KEY_ID])
    created: dict[str, dict] = {}

    def create(call):
        body = dict(call.body)                      # a copy: the recorded call must stay exactly what was sent
        created[body["client_order_id"]] = body
        oid = f"ord-real-{len(created)}"
        yes = D(body["price"])
        created[body["client_order_id"]]["_oid"] = oid
        return respond(201, json={"order_id": oid, "client_order_id": body["client_order_id"], "fill_count": "1.00",
                                  "remaining_count": "0.00", "average_fill_price": body["price"],
                                  "average_fee_paid": f"{micro_fee('1', str(yes)):f}", "ts_ms": int(fake_clock.now * 1000)})

    def fills(call):
        oid = (call.params or {}).get("order_id")
        for body in created.values():
            if body["_oid"] == oid:
                yes = D(body["price"])
                return respond(200, json={"fills": [{
                    "fill_id": f"fill-{oid}", "order_id": oid, "ticker": body["ticker"], "exchange_index": 2,
                    "outcome_side": "yes" if body["side"] == "bid" else "no", "book_side": body["side"],
                    "count_fp": "1.00", "yes_price_dollars": f"{yes:.4f}", "no_price_dollars": f"{1 - yes:.4f}",
                    "is_taker": True, "fee_cost": f"{micro_fee('1', str(yes)):f}",
                    "created_time": iso(fake_clock.now)}], "cursor": ""})
        return respond(200, json={"fills": [], "cursor": ""})

    t.post("/portfolio/events/orders", create)
    t.get("/portfolio/fills", fills)
    t.get("/portfolio/balance", fixture="synthetic/balance_shard2.json")
    client = make_client(t, prod_env, live=True, audit=ledger.audit_request)
    ledger.live = True
    safety = FakeSafety(tmp_path / "KILL")
    safety.clock = fake_clock
    reconcile = FakeReconcile(fake_clock)
    state = RecState(conn, "run-real", clock=fake_clock.time)
    fake_clock.now = REAL_CLOSE - 210
    bot = real_bot(tmp_path, client, state, live=True, ledger=ledger, safety=safety, reconcile=reconcile, clock=fake_clock)
    return SimpleNamespace(t=t, conn=conn, ledger=ledger, bot=bot, state=state, safety=safety, reconcile=reconcile,
                           created=created)


def test_real_client_live_signs_and_sends_one_ioc_priced_at_the_ask(tmp_path, make_client, prod_env, fake_clock):
    s = live_real(tmp_path, make_client, prod_env, fake_clock)
    summary = s.bot.run(hours=0.1)
    assert summary["end_reason"] == "finished" and summary["live_fills"] == 1
    (post,) = s.t.writes
    assert (post.method, post.endpoint, post.host) == ("POST", "/portfolio/events/orders", prod_env.rest_host)
    assert post.signed and post.presign_text().endswith("POST/trade-api/v2/portfolio/events/orders")     # signed, no query
    body = {k: v for k, v in post.body.items()}
    assert body == {"ticker": REAL_TICKER, "client_order_id": body["client_order_id"], "side": "bid", "price": "0.9470",
                    "count": "1.00", "time_in_force": "immediate_or_cancel",
                    "self_trade_prevention_type": "taker_at_cross", "exchange_index": 2}
    assert [c for c in s.t.calls if not c.signed and c.is_write] == []
    assert all(c.signed for c in s.t.calls if c.endpoint.startswith("/portfolio"))        # authenticated reads are signed
    assert all(not c.signed for c in s.t.calls if c.endpoint.startswith("/markets"))       # public ones are not
    (row,) = s.ledger.orders_for(REAL_TICKER)
    assert row["status"] == "executed" and row["order_id"] == "ord-real-1" and row["price"] == "0.9470"
    (opp,) = s.state.opportunity_rows()
    assert opp["outcome"] == "filled" and opp["live_cost"] == pytest.approx(0.947)
    assert opp["realized_pnl"] == pytest.approx(float(1 - REAL_ASK - micro_fee("1", "0.947")))
    assert s.ledger.realized_pnl() == 1 - REAL_ASK - micro_fee("1", "0.947")


def test_real_client_live_losing_trade_updates_the_ledger_cap(tmp_path, make_client, prod_env, fake_clock):
    s = live_real(tmp_path, make_client, prod_env, fake_clock, result="no")
    s.bot.run(hours=0.1)
    assert s.ledger.realized_loss_total() == REAL_ASK + micro_fee("1", "0.947")
    assert s.state.opportunity_rows()[0]["payout"] == 0.0


def test_real_client_live_run_leaves_no_secrets_in_any_table_or_log(tmp_path, make_client, prod_env, fake_clock):
    s = live_real(tmp_path, make_client, prod_env, fake_clock)
    s.bot.run(hours=0.1)
    blob = []
    for (name,) in s.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for row in s.conn.execute(f"SELECT * FROM {name}").fetchall():
            blob.append(" ".join(str(v) for v in row))
    text = "\n".join(blob) + (tmp_path / "exec.jsonl").read_text(encoding="utf-8")
    assert s.conn.execute("SELECT COUNT(*) FROM exec_requests").fetchone()[0] >= 3        # the audit sink saw the requests
    for needle in (TEST_KEY_ID, "KALSHI-ACCESS", "PRIVATE KEY", "BEGIN ", "Authorization"):
        assert needle not in text, needle
    # no stored signature either: a base64 Ed25519 signature is 88 characters
    import re
    assert not re.search(r"[A-Za-z0-9+/]{86}==", text)


def test_real_client_a_write_in_watch_mode_is_impossible(tmp_path, make_client, watch_env, fake_clock):
    t = real_transport(watch_env.rest_host, fake_clock, forbid_writes=True)
    client = make_client(t, watch_env)
    with pytest.raises(PermissionError):
        client.create_order({"ticker": "X"})                  # the client itself refuses: not live
    assert t.writes == []


# ============================================================================================ the production database and the dashboard
def test_a_live_run_on_the_production_connection_is_readable_by_the_dashboard_api(tmp_path):
    from kpm.core import db
    from kpm.dashboard import server

    path = tmp_path / "kpm.sqlite"
    conn = db.connect(path)                                  # file, WAL, sqlite3.Row rows: what the CLI will hand the bots
    sim = live_sim(tmp_path, conn=conn)
    sim.run()
    conn.commit()
    ro = server._open_db(path)                               # the dashboard's own read-only connection
    cfg = {"trading": {"max_loss_usd": 5.0, "kill_switch_file": str(tmp_path / "KILL")}, "dashboard": {"refresh_s": 2}}
    now = sim.clock.now
    status = server.api_status(ro, cfg, {}, now)
    assert status["run"]["strategy"] == "late_favorite" and status["run"]["mode"] == "live"
    assert status["run"]["ended_ts"] is not None and status["run"]["end_reason"] == "finished"
    hb = status["heartbeat"]
    assert hb["status"] == "stopped" and hb["detail"]["strategy"] == "late_favorite" and hb["detail"]["max_loss_usd"] == 5.0
    assert hb["detail"]["balance_usd"] == 29.43 and hb["detail"]["loss_used_usd"] == 0.0
    assert status["stats"]["run"]["opportunities"] == 1 and status["stats"]["run"]["live_orders"] == 1
    (opp,) = server.api_opportunities(ro, cfg, {}, now)["rows"]
    assert opp["kind"] == "late favorite YES" and opp["outcome"] == "filled" and opp["paper_edge"] is None
    assert opp["live_edge"] is None and opp["realized_pnl"] == pytest.approx(float(1 - D("0.95") - micro_fee("1", "0.95")))
    (order,) = server.api_orders(ro, cfg, {}, now)["rows"]
    assert order["status"] == "executed" and order["dry_run"] == 0 and order["response"]["request"]["price"] == "0.9500"
    kinds = {e["kind"] for e in server.api_events(ro, cfg, {}, now)["rows"]}
    assert {"start", "opportunity", "order", "fill", "settle", "stop"} <= kinds
    ro.close()
    conn.close()
