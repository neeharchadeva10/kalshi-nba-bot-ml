"""pair_h2: riskless hourly KXBTCD vs KXBTC15M pairs, two IOC buys.

Pure functions (pricing, payouts, grid steps, market selection), watch mode (quotes, paper twins, settlement) and live
mode (confirm-then-send, leg risk, unwinds, unknown outcomes, loss rule, ledger settlement), then the whole live stack
with the REAL Safety and reconcile modules. Everything is offline: a fake clock whose ``sleep`` advances time and a fake
exchange (tests/execution/botfakes.py). The REAL BotState and Ledger run on in-memory SQLite.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest

from kpm.execution import pair_h2 as ph
from kpm.execution import units
from kpm.execution.botstate import BotState
from kpm.execution.client import Result, validate_order_body
from kpm.execution.ledger import Ledger

from .botfakes import (LINEAR_CENT, TAPERED, Clock, FakeExchange, FakeReconcile, FakeSafety, RecState, iso, lv,
                       micro_fee, real_stack, seed_loss, seed_trade, worse, worst_of)

HOUR = 1_791_226_800                       # 2026-10-05T19:00:00Z: the hour the pair markets close
TARGET = 85432.83                          # T: the KXBTC15M floor_strike (the average before 18:45)
STRIKES = (85200.0, 85300.0, 85500.0, 85600.0)       # two below T, two above


def m15_ticker(hour: int = HOUR) -> str:
    return f"KXBTC15M-H{hour}"


def hourly_ticker(strike: float, hour: int = HOUR) -> str:
    return f"KXBTCD-H{hour}-T{strike:g}"


def asks_book(yes_ask: str, no_ask: str, ys: str = "500", ns: str = "500") -> tuple[list, list]:
    """A book that shows these asks: YES ask = 1 - best NO bid, NO ask = 1 - best YES bid. A worse level comes first."""
    yes_bid, no_bid = f"{1 - D(no_ask):.4f}", f"{1 - D(yes_ask):.4f}"
    return (lv((worse(yes_bid), "9"), (yes_bid, ys)), lv((worse(no_bid), "9"), (no_bid, ns)))


NOTHING = asks_book("0.60", "0.55")                   # YES + NO asks add to 1.15: no pair can pay
HOURLY_CHEAP = asks_book("0.50", "0.53")              # hourly YES 0.50 (K < T leg)
M15_CHEAP = asks_book("0.57", "0.46")                 # 15m NO 0.46 (K < T leg); 15m YES 0.57
# K < T pair: hourly YES 0.50 + 15m NO 0.46 = 0.96, fees 0.0175 + 0.017388, edge 0.005112
FEES_BELOW = micro_fee("1", "0.50") + micro_fee("1", "0.46")
EDGE_BELOW = 1 - D("0.96") - FEES_BELOW


def make_m15(close: float, *, status: str = "active", target: float | None = TARGET, ei: int | None = 2) -> dict:
    m = {"ticker": m15_ticker(int(close)), "event_ticker": f"KXBTC15M-E{int(close)}", "status": status,
         "close_time": iso(close), "open_time": iso(close - 900), "strike_type": "greater_or_equal",
         "price_ranges": TAPERED, "result": "", "market_type": "binary", "can_close_early": True}
    if target is not None:
        m["floor_strike"] = target
    if ei is not None:
        m["exchange_index"] = ei
    return m


def make_hourly(close: float, strike: float, *, status: str = "active", ei: int | None = 2,
                strike_type: str = "greater") -> dict:
    m = {"ticker": hourly_ticker(strike, int(close)), "event_ticker": f"KXBTCD-E{int(close)}", "status": status,
         "close_time": iso(close), "open_time": iso(close - 3600), "floor_strike": strike, "strike_type": strike_type,
         "price_ranges": LINEAR_CENT, "result": "", "market_type": "binary", "can_close_early": True}
    if ei is not None:
        m["exchange_index"] = ei
    return m


class PairClient(FakeExchange):
    """Both series of the fake exchange: the open 15m market (closing at the next quarter) and the hourly strikes
    closing at the next hour. ``books(ticker, now) -> (yes, no) | dict`` scripts every order book."""

    def __init__(self, clock: Clock, books, *, results=None, live: bool = False, latency: float = 0.0,
                 strikes=STRIKES, target: float | None = TARGET):
        super().__init__(clock, results=results, live=live, latency=latency)
        self.books = books
        self.strikes = tuple(strikes)
        self.target = target
        self.extra_hourly: list[dict] = []           # extra / odd hourly markets to list
        self.hide_15m = False
        self.listing_15m = None                      # f(now) -> list of 15m market dicts

    def default_market(self, ticker: str) -> dict:
        if ticker.startswith("KXBTC15M-H"):
            return make_m15(int(ticker.split("-H")[1]))
        h, k = ticker.split("-H")[1].split("-T")
        return make_hourly(int(h), float(k))

    def open_markets(self, series):                # like ExecClient.open_markets: no close-time filters (OpenAPI GetMarkets)
        self._tick("open_markets", series)
        now = self.clock.now
        if series == "KXBTC15M":
            out = [] if self.hide_15m else [make_m15((int(now // 900) + 1) * 900, target=self.target)]
            if self.listing_15m is not None:
                out = self.listing_15m(now)
        else:
            hour = (int(now // 3600) + 1) * 3600
            out = [make_hourly(hour, k) for k in self.strikes] + list(self.extra_hourly)
        for m in out:
            self.known[m["ticker"]] = m
        return out

    def orderbook(self, ticker):
        self._tick("orderbook", ticker)
        got = self.books(ticker, self.clock.now)
        if isinstance(got, dict):
            return got
        yes, no = got
        return {"ticker": ticker, "yes": yes, "no": no, "fetched_at": self.clock.now}


def default_books(ticker: str, now: float):
    """Strike 85300 (below T) offers the K < T pair; everything else shows no pair."""
    if ticker == hourly_ticker(85300.0):
        return HOURLY_CHEAP
    if ticker == m15_ticker():
        return M15_CHEAP
    return NOTHING


class PairSim:
    def __init__(self, tmp_path: Path, *, live: bool = False, books=None, results=None, start_offset: float = -1000,
                 pair_cfg: dict | None = None, trading: dict | None = None, latency: float = 0.0,
                 enabled: bool = True, with_safety: bool = True, conn: sqlite3.Connection | None = None,
                 run_id: str = "run-pair", strikes=STRIKES, target: float | None = TARGET):
        self.tmp_path = tmp_path
        self.clock = Clock(HOUR + start_offset)
        self.conn = conn if conn is not None else sqlite3.connect(":memory:")
        self.state = RecState(self.conn, run_id, clock=self.clock)
        self.client = PairClient(self.clock, books or default_books, results=results, live=live, latency=latency,
                                 strikes=strikes, target=target)
        self.ledger = Ledger(self.conn, run_id, "prod", clock=self.clock, log_path=tmp_path / "exec.jsonl") if live else None
        self.client.ledger = self.ledger
        self.kill_file = tmp_path / "KILL"
        self.safety = FakeSafety(self.kill_file, allow_exit=True) if (live and with_safety) else None
        if self.safety:
            self.safety.clock = self.clock
        self.reconcile = FakeReconcile(self.clock)
        t = {"enabled": enabled, "max_loss_usd": 5.0, "max_cost_per_order_usd": 1.0, "max_contracts_per_order": 1,
             "max_orders_per_minute": 10, "kill_switch_file": str(self.kill_file),
             "pair": {"window_min": 15, "strikes_each_side": 2, "min_edge_usd": 0.001, "poll_interval_s": 1.0,
                      "contracts": 1}}
        t["pair"].update(pair_cfg or {})
        t.update(trading or {})
        self.cfg = {"trading": t}
        self.ctx = ph.PairContext(client=self.client, state=self.state, ledger=self.ledger, safety=self.safety,
                                  reconcile=self.reconcile, env_name="prod", key_fp="abcd1234")
        self.live = live
        self._bot = None

    @property
    def bot(self) -> ph.PairBot:
        if self._bot is None:
            self._bot = ph.PairBot(self.ctx, self.cfg, live=self.live, clock=self.clock, sleep=self.clock.sleep,
                                   kill_file=self.kill_file)
        return self._bot

    def run(self, hours: int | None = 1, smoke: bool = False) -> dict:
        return self.bot.run(hours=hours, smoke=smoke)

    def opps(self) -> list[dict]:
        return self.state.opportunity_rows()

    def orders(self) -> list[dict]:
        return self.state.order_rows()

    def events(self, kind: str | None = None) -> list[dict]:
        return [e for e in self.state.event_rows() if kind is None or e["kind"] == kind]

    def quotes(self) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM bot_quotes ORDER BY ts, strike_ticker")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def max_beat_gap(self) -> float:
        ts = [t for t, _ in self.state.beats]
        return max(b - a for a, b in zip(ts, ts[1:]))


# ============================================================================================ pricing: pair_legs
def test_pair_kind_is_decided_by_the_strike_against_the_target():
    assert ph.pair_kind(D("85432.83"), D("85300")) == ph.KIND_BELOW == "hourly YES + 15m DOWN"
    assert ph.pair_kind(D("85432.83"), D("85500")) == ph.KIND_ABOVE == "15m UP + hourly NO"
    assert ph.pair_kind(D("85432.83"), D("85432.83")) == ph.KIND_ABOVE          # K == T: the 15m YES + hourly NO side


def book_dict(yes_ask: str, no_ask: str, ys: str = "500", ns: str = "500") -> dict:
    yes, no = asks_book(yes_ask, no_ask, ys, ns)
    return {"yes": yes, "no": no}


def test_pair_legs_below_the_target_buys_hourly_yes_and_15m_no():
    q = ph.pair_legs("85432.83", "85300", book_dict("0.57", "0.46", "200", "300"), book_dict("0.50", "0.53", "100", "400"), 1)
    assert q["kind"] == ph.KIND_BELOW and q["contracts"] == D("1")
    a, b = q["legs"]
    assert (a["ticker_role"], a["outcome"], a["ask"], a["size"]) == ("hourly", "yes", D("0.50"), D("400"))
    # YES ask = 1 - best NO bid, so its size is the NO bid's size; the NO ask comes from the YES bid
    assert (b["ticker_role"], b["outcome"], b["ask"], b["size"]) == ("15m", "no", D("0.46"), D("200"))
    assert q["cost"] == D("0.96") and q["fees"] == FEES_BELOW
    assert q["edge"] == EDGE_BELOW == q["contracts"] - q["cost"] - q["fees"]
    assert q["target"] == D("85432.83") and q["strike"] == D("85300")


def test_pair_legs_above_the_target_buys_15m_yes_and_hourly_no():
    q = ph.pair_legs(D("85432.83"), D("85500"), book_dict("0.48", "0.55"), book_dict("0.55", "0.47"), 1)   # (asks that sum to >= 1: a real book never crosses)
    assert q["kind"] == ph.KIND_ABOVE
    a, b = q["legs"]
    assert (a["ticker_role"], a["outcome"], a["ask"]) == ("15m", "yes", D("0.48"))
    assert (b["ticker_role"], b["outcome"], b["ask"]) == ("hourly", "no", D("0.47"))
    assert q["cost"] == D("0.95") and q["fees"] == micro_fee("1", "0.48") + micro_fee("1", "0.47")


def test_pair_legs_scales_with_contracts_and_charges_fees_per_leg():
    q = ph.pair_legs(D("100"), D("90"), book_dict("0.50", "0.53"), book_dict("0.50", "0.53"), D("1"))
    q1 = ph.pair_legs(D("100"), D("90"), book_dict("0.50", "0.53"), book_dict("0.50", "0.53"), D("0.5"))
    assert q1["cost"] == q["cost"] / 2
    assert q1["fees"] == micro_fee("0.5", "0.50") + micro_fee("0.5", "0.53")      # exact per leg: hourly YES, 15m NO
    assert q1["edge"] == D("0.5") - q1["cost"] - q1["fees"]


def test_pair_legs_none_when_a_leg_has_no_ask():
    full = book_dict("0.50", "0.53")
    assert ph.pair_legs("100", "90", {"yes": [], "no": []}, full, 1) is None
    assert ph.pair_legs("100", "90", full, {"yes": full["yes"], "no": []}, 1) is None      # no NO bid: no YES ask
    # K >= T buys the 15m YES (needs its NO bids) and the hourly NO (needs its YES bids)
    assert ph.pair_legs("100", "110", {"yes": full["yes"], "no": []}, full, 1) is None
    assert ph.pair_legs("100", "110", full, {"yes": [], "no": full["no"]}, 1) is None
    assert ph.pair_legs("100", "110", {"yes": [], "no": full["no"]}, full, 1) is not None      # the 15m YES side is not needed


def test_pair_legs_uses_the_best_level_which_is_the_last_one():
    deep = {"yes": lv(("0.0100", "1"), ("0.3000", "5"), ("0.5400", "7")), "no": lv(("0.0100", "1"), ("0.2000", "5"), ("0.4300", "9"))}
    q = ph.pair_legs("100", "110", deep, deep, 1)         # K >= T: 15m YES + hourly NO
    assert [(leg["ticker_role"], leg["ask"], leg["size"]) for leg in q["legs"]] == [("15m", D("0.57"), D("9")),
                                                                                    ("hourly", D("0.46"), D("7"))]


def test_pair_legs_validation_and_injected_fees():
    with pytest.raises(ValueError):
        ph.pair_legs("100", "90", book_dict("0.5", "0.5"), book_dict("0.5", "0.5"), 0)
    with pytest.raises(TypeError):
        ph.pair_legs(100.5, "90", book_dict("0.5", "0.5"), book_dict("0.5", "0.5"), 1)      # floats are not exact
    q = ph.pair_legs("100", "90", book_dict("0.50", "0.53"), book_dict("0.50", "0.53"), 1, fee_fn=lambda c, p: D("0.01"))
    assert q["fees"] == D("0.02")


def test_pair_legs_non_ascending_book_is_refused():
    bad = {"yes": lv(("0.5000", "1"), ("0.4000", "1")), "no": lv(("0.4000", "1"))}
    with pytest.raises(ValueError, match="ascending"):
        ph.pair_legs("100", "90", bad, bad, 1)


def test_a_crossed_or_locked_book_prices_no_pair():
    """Review finding: a transitional 15m snapshot with YES bid 0.60 and NO bid 0.45 reads a NO ask of 0.40; against an
    hourly YES ask of 0.50 that was a phantom 6.5 cent edge (cost 0.90). late_favorite.read_quote and Safety refuse a
    crossed book; pair_legs priced it. A real book never crosses, so its asks never sum to less than 1 across the pair."""
    ok = book_dict("0.50", "0.53")
    crossed = {"yes": lv(("0.6000", "500")), "no": lv(("0.4500", "500"))}          # 0.60 + 0.45 = 1.05
    locked = {"yes": lv(("0.6000", "500")), "no": lv(("0.4000", "500"))}           # 0.60 + 0.40 = 1.00
    for bad in (crossed, locked):
        assert ph.is_crossed(bad)
        for strike in ("90", "110"):                                                # K < T and K >= T
            assert ph.pair_legs("100", strike, bad, ok, 1) is None                  # the 15m book is the bad one
            assert ph.pair_legs("100", strike, ok, bad, 1) is None                  # ... or the hourly book is
    assert not ph.is_crossed(ok) and not ph.is_crossed({"yes": [], "no": lv(("0.9000", "5"))}) and not ph.is_crossed({})
    # the phantom pair of the review: hourly YES ask 0.50 + 15m NO ask 0.40 (from the crossed book) = 0.90
    assert ph.outcome_ask(crossed, "no")[0] == D("0.40")                            # (what the old code priced)
    hourly = book_dict("0.50", "0.53")
    assert ph.pair_legs("100", "90", crossed, hourly, 1) is None


# ============================================================================================ payout
@pytest.mark.parametrize("kind,up,above,expected", [
    (ph.KIND_BELOW, True, True, 1),        # hourly YES wins, 15m NO loses
    (ph.KIND_BELOW, False, True, 2),       # the average landed between K and T: both pay
    (ph.KIND_BELOW, False, False, 1),
    (ph.KIND_BELOW, True, False, 0),       # impossible for K < T: the assumption failed
    (ph.KIND_ABOVE, True, True, 1),
    (ph.KIND_ABOVE, True, False, 2),       # T <= average <= K: both pay
    (ph.KIND_ABOVE, False, False, 1),
    (ph.KIND_ABOVE, False, True, 0),       # impossible for K >= T
])
def test_pair_payout(kind, up, above, expected):
    assert ph.pair_payout(kind, up, above) == D(expected)


def test_pair_payout_rejects_unknown_kinds():
    with pytest.raises(ValueError):
        ph.pair_payout("hedge", True, True)


def test_every_possible_average_pays_at_least_one_dollar_for_both_kinds():
    """The riskless claim, checked on a grid of final averages against the markets' own rules."""
    for T, K in ((100, 90), (100, 100), (100, 120), (100, 99.99)):
        kind = ph.pair_kind(D(str(T)), D(str(K)))
        for a in [x / 100 for x in range(8000, 13000)]:
            up = a >= T                 # the 15m market: YES iff the average is at least the target
            above = a > K               # the hourly market: YES iff the average is strictly above the strike
            assert ph.pair_payout(kind, up, above) >= 1, (T, K, a)


# ============================================================================================ price grid
BANDS_TAPERED = TAPERED


def test_step_price_yes_moves_up_the_yes_grid():
    assert ph.step_price("yes", D("0.55"), BANDS_TAPERED, 2) == D("0.57")
    assert ph.step_price("yes", D("0.55"), BANDS_TAPERED, 0) == D("0.55")
    assert ph.step_price("yes", D("0.95"), BANDS_TAPERED, 2) == D("0.952")                 # 0.001 steps above 0.90
    assert ph.step_price("yes", D("0.05"), BANDS_TAPERED, 2) == D("0.052")


def test_step_price_no_moves_up_the_no_price_by_stepping_the_yes_price_down():
    assert ph.step_price("no", D("0.46"), BANDS_TAPERED, 2) == D("0.48")
    assert ph.step_price("no", D("0.05"), BANDS_TAPERED, 2) == D("0.052")                  # NO 0.05 = YES 0.95: 0.001 steps


def test_step_price_band_edges_use_the_step_of_the_band_entered():
    assert ph.step_price("yes", D("0.89"), BANDS_TAPERED, 1) == D("0.90")
    assert ph.step_price("yes", D("0.90"), BANDS_TAPERED, 1) == D("0.901")                  # into the fine band
    assert ph.step_price("yes", D("0.89"), BANDS_TAPERED, 3) == D("0.902")
    assert ph.step_price("no", D("0.10"), BANDS_TAPERED, 1) == D("0.11")                   # NO 0.10 = YES 0.90: down to 0.89
    assert ph.step_price("yes", D("0.099"), BANDS_TAPERED, 2) == D("0.11")                 # 0.100, then the coarse band


def test_step_price_direction_follows_the_grid_not_the_mirror_image():
    # an asymmetric grid: 1c steps up to 0.50, 5c steps above it (the tapered grids of the series are symmetric)
    bands = [{"start": "0.0000", "end": "0.5000", "step": "0.0100"}, {"start": "0.5000", "end": "1.0000", "step": "0.0500"}]
    assert ph.step_price("yes", D("0.60"), bands, 2) == D("0.70")            # YES up through the coarse band
    assert ph.step_price("no", D("0.40"), bands, 2) == D("0.50")             # NO 0.40 = YES 0.60, down two coarse ticks: 0.50
    assert ph.step_price("yes", D("0.48"), bands, 3) == D("0.55")            # 0.49, 0.50, then the coarse step to 0.55
    assert ph.step_price("no", D("0.52"), bands, 1) == D("0.53")             # NO 0.52 = YES 0.48: down one fine tick = 0.47


def test_step_price_is_none_off_the_grid_or_at_its_end():
    assert ph.step_price("yes", D("0.555"), BANDS_TAPERED, 1) is None
    assert ph.step_price("yes", D("0.999"), BANDS_TAPERED, 2) is None
    assert ph.step_price("yes", D("0.55"), [], 1) is None
    assert ph.step_price("yes", D("0.55"), [{"start": "x"}], 1) is None
    assert ph.step_price("yes", D("0.55"), LINEAR_CENT, 2) == D("0.57")


# ============================================================================================ book helpers
def test_touch_is_the_last_level_and_refuses_unordered_sides():
    assert ph.touch(lv(("0.1000", "1"), ("0.4000", "7"))) == (D("0.4000"), D("7"))
    assert ph.touch([]) is None and ph.touch(None) is None
    assert ph.touch(lv(("0.4000", "0"))) is None                    # a zero-size top level is not a quote
    assert ph.touch([["0.2000", "3.00"], ["0.4000", "4.50"]]) == (D("0.4000"), D("4.50"))        # REST strings
    with pytest.raises(ValueError, match="ascending"):
        ph.touch(lv(("0.4000", "1"), ("0.1000", "1")))


def test_outcome_ask_reads_the_opposite_side():
    b = {"yes": lv(("0.5400", "10")), "no": lv(("0.4300", "20"))}
    assert ph.outcome_ask(b, "yes") == (D("0.57"), D("20"))
    assert ph.outcome_ask(b, "no") == (D("0.46"), D("10"))
    assert ph.outcome_ask({"yes": [], "no": b["no"]}, "no") is None
    with pytest.raises(ValueError):
        ph.outcome_ask(b, "maybe")


def test_book_age():
    assert ph.book_age({"fetched_at": 100.0}, 101.5) == 1.5
    assert ph.book_age({}, 1.0) == float("inf") and ph.book_age(None, 1.0) == float("inf")


# ============================================================================================ market selection
def test_strike_value_goes_through_the_shortest_repr():
    assert ph.strike_value({"floor_strike": 85319.11}) == D("85319.11")
    assert ph.strike_value({"floor_strike": "85319.11"}) == D("85319.11")
    assert ph.strike_value({"floor_strike": None}) is None and ph.strike_value({}) is None
    assert ph.strike_value({"floor_strike": True}) is None and ph.strike_value({"floor_strike": float("nan")}) is None
    assert ph.strike_value({"floor_strike": "x"}) is None


def test_close_ts():
    assert ph.close_ts({"close_time": "2026-10-05T19:00:00Z"}) == HOUR
    assert ph.close_ts({"close_time": "soon"}) is None and ph.close_ts({}) is None


def hm(strike: float, **kw) -> dict:
    return make_hourly(HOUR, strike, **kw)


def test_hourly_strikes_picks_the_nearest_on_each_side():
    ms = [hm(k) for k in (85000.0, 85200.0, 85300.0, 85500.0, 85600.0, 85800.0)]
    got = ph.hourly_strikes(ms, HOUR, D("85432.83"), 2)
    assert [k for k, _ in got] == [D("85200"), D("85300"), D("85500"), D("85600")]
    assert [k for k, _ in ph.hourly_strikes(ms, HOUR, D("85500"), 1)] == [D("85300"), D("85500")]     # K == T counts above
    assert ph.hourly_strikes(ms, HOUR, D("85432.83"), 0) == []


def test_hourly_strikes_ignores_other_hours_closed_markets_and_odd_types():
    ms = [hm(85300.0), make_hourly(HOUR + 3600, 85301.0), hm(85302.0, status="closed"), hm(85303.0, status="finalized"),
          hm(85304.0, strike_type="between"), {**hm(85305.0), "cap_strike": 85400}, {"ticker": "X"}, "junk",
          {**hm(85306.0), "status": "open"}, hm(85300.0)]
    got = ph.hourly_strikes(ms, HOUR, D("85432.83"), 5)
    assert sorted(k for k, _ in got) == [D("85300"), D("85306")]            # 'open' counts as active; duplicates collapse


def test_select_markets_needs_exactly_one_15m_market_with_a_target():
    ms15 = [make_m15(HOUR)]
    sel, why = ph.select_markets(ms15, [hm(k) for k in STRIKES], HOUR, 2)
    assert why == "" and sel["target"] == D("85432.83") and sel["m15"]["ticker"] == m15_ticker()
    assert [k for k, _ in sel["strikes"]] == [D("85200"), D("85300"), D("85500"), D("85600")]
    sel, why = ph.select_markets([], [hm(85300.0)], HOUR, 2)
    assert sel is None and "no open KXBTC15M market closes at this hour" in why
    sel, why = ph.select_markets([make_m15(HOUR - 900)], [hm(85300.0)], HOUR, 2)
    assert sel is None and "no open KXBTC15M" in why
    sel, why = ph.select_markets([make_m15(HOUR), {**make_m15(HOUR), "ticker": "KXBTC15M-OTHER"}], [hm(85300.0)], HOUR, 2)
    assert sel is None and "refusing to guess" in why
    sel, why = ph.select_markets([make_m15(HOUR, target=None)], [hm(85300.0)], HOUR, 2)
    assert sel is None and "target" in why and "not set" in why
    sel, why = ph.select_markets([{**make_m15(HOUR), "strike_type": "less"}], [hm(85300.0)], HOUR, 2)
    assert sel is None and "greater_or_equal" in why
    sel, why = ph.select_markets(ms15, [], HOUR, 2)
    assert sel is None and "no open KXBTCD" in why


# ============================================================================================ orders
def test_leg_spec_buys_an_outcome_through_to_book():
    s = ph.leg_spec(make_hourly(HOUR, 85300.0), "yes", D("0.50"), D("1"), purpose="pair_entry", opportunity_id=7, role="hourly")
    assert (s["side"], s["yes_price"], s["outcome"], s["outcome_price"], s["action"]) == ("bid", D("0.50"), "yes", D("0.50"), "buy")
    assert s["strategy"] == "pair_h2" and s["opportunity_id"] == 7 and s["reduce_only"] is False
    assert s["time_in_force"] == "immediate_or_cancel" and s["exchange_index"] == 2
    n = ph.leg_spec(make_m15(HOUR), "no", D("0.46"), D("1"), purpose="pair_entry", role="15m")
    assert (n["side"], n["yes_price"]) == ("ask", D("0.54"))                       # buying NO at 0.46 = selling YES at 0.54
    u = ph.leg_spec(make_m15(HOUR), "no", D("0.48"), D("1"), purpose="unwind", role="15m", reduce_only=True)
    assert u["reduce_only"] is True and u["purpose"] == "unwind"
    with pytest.raises(ValueError, match="exchange_index"):
        ph.leg_spec(make_m15(HOUR, ei=None), "yes", D("0.5"), D("1"), purpose="x")


def test_order_body_is_a_checked_ioc_createorderv2():
    s = ph.leg_spec(make_m15(HOUR), "no", D("0.46"), D("1"), purpose="pair_entry", role="15m")
    body = ph.order_body(s, "cid-1")
    assert body == {"ticker": m15_ticker(), "client_order_id": "cid-1", "side": "ask", "price": "0.5400", "count": "1.00",
                    "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross",
                    "exchange_index": 2}
    assert validate_order_body(body)
    r = ph.order_body(ph.leg_spec(make_m15(HOUR), "yes", D("0.5"), D("1"), purpose="unwind", reduce_only=True), "cid-2")
    assert r["reduce_only"] is True and validate_order_body(r)
    for forbidden in ("post_only", "expiration_time", "yes_price", "action"):
        assert forbidden not in body


# ============================================================================================ configuration
@pytest.mark.parametrize("trading,needle", [
    ({"pair": {"contracts": 2}}, "contracts"),
    ({"pair": {"contracts": "1.005"}}, "contracts"),
    ({"pair": {"contracts": 0}}, "contracts"),
    ({"pair": {"min_edge_usd": -0.01}}, "min_edge"),
    ({"pair": {"window_min": 0}}, "window_min"),
    ({"pair": {"window_min": 61}}, "window_min"),
    ({"pair": {"strikes_each_side": 0}}, "strikes_each_side"),
    ({"pair": {"strikes_each_side": 11}}, "strikes_each_side"),
    ({"pair": {"poll_interval_s": 0.05}}, "poll_interval_s"),
    ({"pair": {"max_book_age_s": 31}}, "max_book_age_s"),
    ({"pair": {"stop_before_close_s": 900}}, "stop_before_close_s"),
    ({"pair": {"max_live_pairs_per_hour": 11}}, "max_live_pairs_per_hour"),
    ({"pair": {"max_opportunities_per_strike_hour": 0}}, "max_opportunities_per_strike_hour"),
    ({"max_loss_usd": 5.01}, "max_loss_usd"),
    ({"max_loss_usd": 0}, "max_loss_usd"),
    ({"max_cost_per_order_usd": 1.5}, "max_cost_per_order_usd"),
    ({"max_contracts_per_order": 2}, "max_contracts_per_order"),
])
def test_configuration_above_a_ceiling_or_out_of_range_refuses_to_start(tmp_path, trading, needle):
    with pytest.raises(ValueError, match=needle):
        PairSim(tmp_path, trading=trading, pair_cfg=trading.get("pair")).bot


def test_defaults_match_the_users_decisions(tmp_path):
    bot = PairSim(tmp_path).bot
    assert bot.contracts == D("1") and bot.max_loss == D("5.0") and bot.min_edge == D("0.001")
    assert bot.max_live_pairs_per_hour == 1 and bot.window_s == 900.0 and bot.strikes_each_side == 2
    assert bot.poll_s == 1.0 and bot.max_book_age_s == 2.0 and bot.stop_before_close_s == 60.0


def test_the_whole_settings_dict_or_the_trading_block_both_work(tmp_path):
    sim = PairSim(tmp_path)
    bot = ph.PairBot(sim.ctx, {"max_loss_usd": 2.5, "pair": {"strikes_each_side": 3}}, live=False, clock=sim.clock,
                     sleep=sim.clock.sleep, kill_file=sim.kill_file)
    assert bot.max_loss == D("2.5") and bot.strikes_each_side == 3


@pytest.mark.parametrize("stop,ok", [(0, False), (20, False), (29.9, False), (39.9, False), (40, True), (45, True), (60, True)])
def test_the_trading_window_must_end_before_safetys_entry_cutoff(tmp_path, stop, ok):
    """Review finding: the shipped window ended 20 s before the hour, Safety refuses every entry in the last 30 s (20 + the
    10 s early-close buffer), so pairs detected in between were paper twins that live trading could never take, booked
    as 'blocked_by_cap' in live mode. With the defaults (30 + 10) the cutoff is 40 s."""
    if ok:
        assert PairSim(tmp_path, pair_cfg={"stop_before_close_s": stop}).bot.stop_before_close_s == stop
    else:
        with pytest.raises(ValueError, match=r"stop_before_close_s.*40.*paper twin"):
            PairSim(tmp_path, pair_cfg={"stop_before_close_s": stop}).bot


def test_the_cutoff_follows_the_configured_minimum_time_to_close(tmp_path):
    cfg = {"min_seconds_to_close": 20}                                              # what config/settings.yaml says: 30 s
    assert PairSim(tmp_path, trading=cfg, pair_cfg={"stop_before_close_s": 30}).bot.entry_cutoff_s == 30.0
    with pytest.raises(ValueError, match="at least 30"):
        PairSim(tmp_path, trading=cfg, pair_cfg={"stop_before_close_s": 29}).bot
    with pytest.raises(ValueError, match="at least 70"):
        PairSim(tmp_path, trading={"min_seconds_to_close": 60}, pair_cfg={"stop_before_close_s": 65}).bot


def test_the_exposure_ceiling_is_a_hard_ceiling_of_the_bot_too(tmp_path):
    assert PairSim(tmp_path, trading={"max_open_exposure_usd": "3.00"}).bot.max_open_exposure == D("3.00")
    assert PairSim(tmp_path, trading={"max_open_exposure_usd": 1.5}).bot.max_open_exposure == D("1.5")
    for bad in ("3.01", 0, -1):
        with pytest.raises(ValueError, match="max_open_exposure_usd"):
            PairSim(tmp_path, trading={"max_open_exposure_usd": bad}).bot


@pytest.mark.parametrize("kw,needle", [({"enabled": False}, "trading.enabled"), ({"with_safety": False}, "Safety")])
def test_live_mode_gates(tmp_path, kw, needle):
    with pytest.raises(PermissionError, match=needle):
        PairSim(tmp_path, live=True, **kw).bot


def test_live_needs_a_live_client_a_ledger_and_a_started_safety(tmp_path):
    sim = PairSim(tmp_path, live=True)
    sim.client.live = False
    with pytest.raises(PermissionError, match="live=True"):
        ph.PairBot(sim.ctx, sim.cfg, live=True, clock=sim.clock, sleep=sim.clock.sleep)
    sim.client.live = True
    sim.safety.started = False
    with pytest.raises(PermissionError, match="startup"):
        ph.PairBot(sim.ctx, sim.cfg, live=True, clock=sim.clock, sleep=sim.clock.sleep)
    sim.safety.started = True
    sim.ctx.ledger = None
    with pytest.raises(PermissionError, match="Ledger"):
        ph.PairBot(sim.ctx, sim.cfg, live=True, clock=sim.clock, sleep=sim.clock.sleep)


@pytest.mark.parametrize("bad", [0, -3, "x"])
def test_a_bad_run_length_is_refused(tmp_path, bad):
    with pytest.raises((ValueError, TypeError)):
        PairSim(tmp_path).bot.run(hours=bad)


# ============================================================================================ watch mode
def test_watch_mode_prices_every_strike_every_poll_and_writes_the_paper_twin(tmp_path):
    results = {m15_ticker(): "yes", hourly_ticker(85300.0): "yes"}
    sim = PairSim(tmp_path, results=results)
    summary = sim.run()
    run = sim.state.run_row()
    assert (run["strategy"], run["mode"], run["end_reason"]) == ("pair_h2", "watch", "finished")
    qs = sim.quotes()
    strikes = {q["strike_ticker"] for q in qs}
    assert strikes == {hourly_ticker(k) for k in STRIKES}
    polls = len(qs) // 4
    assert polls == summary["polls"] and 800 <= polls <= 850            # every second from 18:45:00 to 18:59:00
    below = [q for q in qs if q["strike_ticker"] == hourly_ticker(85300.0)]
    q0 = below[0]
    assert q0["hour_ticker"] == m15_ticker() and q0["kind"] == ph.KIND_BELOW
    assert q0["leg_a_ask"] == pytest.approx(0.50) and q0["leg_b_ask"] == pytest.approx(0.46)
    assert q0["leg_a_size"] == 500.0 and q0["cost"] == pytest.approx(0.96)
    assert q0["fees"] == pytest.approx(float(FEES_BELOW)) and q0["edge"] == pytest.approx(float(EDGE_BELOW))
    other = [q for q in qs if q["strike_ticker"] == hourly_ticker(85500.0)]
    assert other[0]["kind"] == ph.KIND_ABOVE and other[0]["edge"] < 0
    # one paper twin
    (opp,) = sim.opps()
    assert opp["hour_ticker"] == m15_ticker() and opp["strike_ticker"] == hourly_ticker(85300.0)
    assert opp["kind"] == ph.KIND_BELOW and opp["contracts"] == 1.0 and opp["live"] == 0 and opp["outcome"] == "paper_only"
    assert opp["paper_cost"] == pytest.approx(0.96) and opp["paper_fees"] == pytest.approx(float(FEES_BELOW))
    assert opp["paper_edge"] == pytest.approx(float(EDGE_BELOW))
    assert opp["detected_ts"] == pytest.approx(HOUR - 900)               # the first poll of the window
    # settled: hourly YES won (K < average) and the 15m market was YES: pays 1 + 0
    assert opp["paper_realized_pnl"] == pytest.approx(float(1 - D("0.96") - FEES_BELOW))
    assert opp["realized_pnl"] is None and opp["live_cost"] is None
    assert summary["opportunities"] == 1 and summary["settled"] == 1 and summary["outcomes"] == {"paper_only": 1}


@pytest.mark.parametrize("up,above,pays", [(True, True, 1), (False, True, 2), (False, False, 1)])
def test_the_paper_twin_is_settled_with_the_pair_payout(tmp_path, up, above, pays):
    results = {m15_ticker(): "yes" if up else "no", hourly_ticker(85300.0): "yes" if above else "no"}
    sim = PairSim(tmp_path, results=results)
    sim.run()
    (opp,) = sim.opps()
    assert opp["paper_realized_pnl"] == pytest.approx(float(pays - D("0.96") - FEES_BELOW))
    assert not [e for e in sim.events("settle") if e["level"] == "error"]


def test_a_pair_that_pays_less_than_a_dollar_is_reported_as_a_failed_assumption(tmp_path):
    sim = PairSim(tmp_path, results={m15_ticker(): "yes", hourly_ticker(85300.0): "no"})     # impossible for K < T
    sim.run()
    assert sim.opps()[0]["paper_realized_pnl"] == pytest.approx(float(0 - D("0.96") - FEES_BELOW))
    errs = [e for e in sim.events("settle") if e["level"] == "error"]
    assert errs and "pair assumption failed" in errs[0]["message"]
    assert sim.state.heartbeat_row()["detail"]["last_error"]


def test_watch_mode_dry_run_orders_show_exactly_what_live_would_send(tmp_path):
    sim = PairSim(tmp_path)
    sim.run()
    rows = sim.orders()
    assert len(rows) == 2 and all(o["dry_run"] == 1 and o["status"] == "dry_run" and o["tif"] == "ioc" for o in rows)
    by = {o["ticker"]: o for o in rows}
    h, m = by[hourly_ticker(85300.0)], by[m15_ticker()]
    assert (h["side"], h["action"], h["limit_price"]) == ("yes", "buy", pytest.approx(0.50))
    assert (m["side"], m["action"], m["limit_price"]) == ("no", "buy", pytest.approx(0.46))
    wh, wm = (json.loads(o["response_json"])["would_send"] for o in (h, m))
    assert (wh["side"], wh["price"], wh["count"]) == ("bid", "0.5000", "1.00")
    assert (wm["side"], wm["price"], wm["count"]) == ("ask", "0.5400", "1.00")        # NO 0.46 = sell YES at 0.54
    validate_order_body(wh), validate_order_body(wm)
    assert all(o["opportunity_id"] == sim.opps()[0]["id"] for o in rows)


def test_watch_mode_sends_nothing_and_reads_public_data_only(tmp_path):
    sim = PairSim(tmp_path)
    sim.run()
    assert sim.client.created == [] and sim.client.batches == []
    assert {c[0] for c in sim.client.calls} <= {"open_markets", "market", "orderbook"}
    assert sim.reconcile.reconciled == [] and sim.reconcile.cancel_paths == [] and sim.ledger is None


def test_one_paper_twin_per_strike_and_hour_however_long_the_edge_lasts(tmp_path):
    sim = PairSim(tmp_path)
    sim.run()
    assert len(sim.opps()) == 1


def test_an_edge_that_goes_away_and_returns_is_a_new_opportunity_if_the_limit_allows(tmp_path):
    def books(ticker, now):
        flicker = int(now - (HOUR - 900)) % 100 < 50           # on for 50 s, off for 50 s
        if ticker == hourly_ticker(85300.0):
            return HOURLY_CHEAP if flicker else NOTHING
        if ticker == m15_ticker():
            return M15_CHEAP
        return NOTHING

    one = PairSim(tmp_path / "a", books=books)
    one.run()
    assert len(one.opps()) == 1                                # max_opportunities_per_strike_hour = 1
    three = PairSim(tmp_path / "b", books=books, pair_cfg={"max_opportunities_per_strike_hour": 3})
    three.run()
    assert len(three.opps()) == 3
    assert [o["detected_ts"] for o in three.opps()] == pytest.approx([HOUR - 900, HOUR - 900 + 100, HOUR - 900 + 200])


def test_no_edge_means_quotes_but_no_opportunity(tmp_path):
    sim = PairSim(tmp_path, books=lambda t, now: NOTHING)
    summary = sim.run()
    assert sim.opps() == [] and sim.orders() == [] and summary["opportunities"] == 0
    assert all(q["edge"] < 0 for q in sim.quotes()) and sim.quotes()


def test_the_edge_must_clear_min_edge_per_contract(tmp_path):
    sim = PairSim(tmp_path, pair_cfg={"min_edge_usd": 0.006})          # the pair's edge is 0.005112
    sim.run()
    assert sim.opps() == [] and sim.quotes()
    sim2 = PairSim(tmp_path / "b", pair_cfg={"min_edge_usd": 0.005})
    sim2.run()
    assert len(sim2.opps()) == 1


def test_both_displayed_sizes_must_cover_the_contracts(tmp_path):
    thin = asks_book("0.50", "0.53", ys="0.50", ns="0.50")             # 0.50 contracts shown: fewer than 1
    sim = PairSim(tmp_path, books=lambda t, now: thin if t == hourly_ticker(85300.0) else M15_CHEAP)
    sim.run()
    assert sim.opps() == [] and any(q["edge"] > 0 for q in sim.quotes())      # priced, but not actionable


def test_stale_books_are_priced_for_the_chart_but_never_acted_on(tmp_path):
    def books(ticker, now):
        yes, no = (HOURLY_CHEAP if ticker == hourly_ticker(85300.0) else M15_CHEAP if ticker == m15_ticker() else NOTHING)
        return {"ticker": ticker, "yes": yes, "no": no, "fetched_at": now - 10}
    sim = PairSim(tmp_path, books=books)
    sim.run()
    assert sim.opps() == [] and sim.quotes()


def test_a_one_sided_book_is_skipped_without_a_quote(tmp_path):
    one_sided = ([], lv(("0.4000", "5")))                 # K >= T needs the hourly YES bids (for its NO ask): none
    sim = PairSim(tmp_path, books=lambda t, now: one_sided if t == hourly_ticker(85500.0) else default_books(t, now))
    sim.run()
    assert hourly_ticker(85500.0) not in {q["strike_ticker"] for q in sim.quotes()}
    assert len(sim.opps()) == 1


def test_an_unreadable_book_warns_and_does_not_stop_the_hour(tmp_path):
    bad = {"yes": lv(("0.5000", "1"), ("0.4000", "1")), "no": lv(("0.4000", "1")), "fetched_at": 0.0}

    def books(ticker, now):
        if ticker == hourly_ticker(85600.0):
            return dict(bad, fetched_at=now)
        return default_books(ticker, now)

    sim = PairSim(tmp_path, books=books)
    sim.run()
    assert len(sim.opps()) == 1 and any("cannot price" in e["message"] for e in sim.events("error"))


def test_order_book_read_failures_are_survived(tmp_path):
    sim = PairSim(tmp_path)
    orig = sim.client.orderbook
    n = {"i": 0}

    def flaky(ticker):
        n["i"] += 1
        if n["i"] % 7 == 0:
            raise ConnectionError("boom")
        return orig(ticker)

    sim.client.orderbook = flaky
    sim.run()
    assert len(sim.opps()) == 1 and any("order book read failed" in e["message"] for e in sim.events("error"))


def test_the_window_waits_for_the_15m_market_and_its_target(tmp_path):
    sim = PairSim(tmp_path, results={m15_ticker(): "yes", hourly_ticker(85300.0): "yes"})
    sim.client.hide_15m = True
    orig = sim.client.open_markets

    def open_markets(series, **kw):
        if sim.clock.now >= HOUR - 880:                        # the market opens 20 s late
            sim.client.hide_15m = False
        return orig(series, **kw)

    sim.client.open_markets = open_markets
    sim.run()
    (opp,) = sim.opps()
    assert HOUR - 880 <= opp["detected_ts"] <= HOUR - 870
    assert any("waiting for the pair markets" in e["message"] for e in sim.events("error"))


def test_no_market_in_the_window_is_reported(tmp_path):
    sim = PairSim(tmp_path)
    sim.client.hide_15m = True
    summary = sim.run()
    assert sim.opps() == [] and sim.quotes() == [] and summary["polls"] == 0
    assert any("no pair to watch" in e["message"] for e in sim.events("window"))
    assert sim.max_beat_gap() <= 5.0


def test_two_hours_are_two_windows(tmp_path):
    h2 = HOUR + 3600
    results = {m15_ticker(): "yes", hourly_ticker(85300.0): "yes", m15_ticker(h2): "no", hourly_ticker(85300.0, h2): "no"}

    def books(ticker, now):
        if ticker in (hourly_ticker(85300.0), hourly_ticker(85300.0, h2)):
            return HOURLY_CHEAP
        if ticker in (m15_ticker(), m15_ticker(h2)):
            return M15_CHEAP
        return NOTHING

    sim = PairSim(tmp_path, books=books, results=results, start_offset=-1000)
    summary = sim.run(hours=2)
    assert summary["hours"] == 2 and summary["settled"] == 2
    opps = sim.opps()
    assert [o["hour_ticker"] for o in opps] == [m15_ticker(), m15_ticker(h2)]
    assert opps[0]["detected_ts"] == pytest.approx(HOUR - 900) and opps[1]["detected_ts"] == pytest.approx(h2 - 900)
    assert sim.max_beat_gap() <= 5.0


def test_a_run_that_starts_inside_the_window_trades_the_hour_in_progress(tmp_path):
    sim = PairSim(tmp_path, start_offset=-400)
    sim.run()
    (opp,) = sim.opps()
    assert opp["detected_ts"] == pytest.approx(HOUR - 400)


def test_a_run_that_starts_too_late_for_this_hour_waits_for_the_next(tmp_path):
    sim = PairSim(tmp_path, start_offset=-30, results={})              # inside the last 60 s: this hour is over
    sim.client.hide_15m = False
    sim.bot._next_hour(sim.clock.now)
    assert sim.bot._next_hour(sim.clock.now) == HOUR + 3600
    assert sim.bot._next_hour(HOUR - 61) == HOUR and sim.bot._next_hour(HOUR - 60) == HOUR + 3600


def test_heartbeat_at_least_every_5_seconds_and_standard_keys(tmp_path):
    sim = PairSim(tmp_path, results={m15_ticker(): "yes", hourly_ticker(85300.0): "yes"})
    sim.run()
    assert sim.max_beat_gap() <= 5.0
    hb = sim.state.heartbeat_row()
    assert hb["status"] == "stopped"
    for k in ("strategy", "mode", "status", "market_ticker", "window_end_ts", "loss_used_usd", "max_loss_usd",
              "balance_usd", "kill_switch", "last_error"):
        assert k in hb["detail"], k
    assert hb["detail"]["strategy"] == "pair_h2" and hb["detail"]["mode"] == "watch"
    assert hb["detail"]["max_loss_usd"] == 5.0 and hb["detail"]["balance_usd"] is None
    assert hb["detail"]["market_ticker"] == hb["detail"]["hour_ticker"] == m15_ticker()
    assert {"waiting", "monitoring", "stopped"} <= {s for _, s in sim.state.beats}


def test_heartbeat_shows_the_pair_window_while_monitoring(tmp_path):
    sim = PairSim(tmp_path)
    seen = []
    orig = sim.state.heartbeat

    def spy(status, detail=None, ts=None):
        seen.append((status, dict(detail or {})))
        orig(status, detail, ts=ts)

    sim.state.heartbeat = spy
    sim.run()
    inside = [d for s, d in seen if s == "monitoring" and d.get("hour_ticker")]
    assert inside and inside[-1]["hour_ticker"] == m15_ticker() and inside[-1]["window_end_ts"] == HOUR - 60
    assert inside[-1]["target"] == D("85432.83") or inside[-1]["target"] == 85432.83


def test_kill_file_present_at_start_blocks_everything(tmp_path):
    sim = PairSim(tmp_path)
    sim.kill_file.write_text("stop\n")
    summary = sim.run()
    assert summary["end_reason"] == "kill switch present at start" and sim.client.calls == [] and sim.opps() == []


def test_kill_file_created_mid_window_stops_the_bot(tmp_path):
    sim = PairSim(tmp_path, books=lambda t, now: NOTHING)
    orig = sim.client.orderbook
    n = {"i": 0}

    def orderbook(ticker):
        n["i"] += 1
        if n["i"] == 12:
            sim.kill_file.write_text("kill\n")
        return orig(ticker)

    sim.client.orderbook = orderbook
    summary = sim.run()
    assert summary["end_reason"] == "kill switch" and n["i"] <= 18
    assert any(e["kind"] == "kill" for e in sim.events())


def test_run_records_config_without_secrets(tmp_path):
    sim = PairSim(tmp_path)
    sim.run()
    cfg = json.loads(sim.state.run_row()["config_json"])
    assert cfg["strategy"] == "pair_h2" and cfg["mode"] == "watch" and cfg["series"] == ["KXBTC15M", "KXBTCD"]
    assert cfg["contracts"] == "1" and cfg["min_edge_usd"] == "0.001" and cfg["max_loss_usd"] == "5.0"
    assert "private" not in json.dumps(cfg).lower() and "key_id" not in json.dumps(cfg).lower()


def test_a_paper_twin_with_no_result_stays_pending_and_is_reported(tmp_path):
    sim = PairSim(tmp_path, pair_cfg={"settle_wait_s": 300})
    sim.run()
    assert sim.opps()[0]["paper_realized_pnl"] is None
    assert any("not settled" in e["message"] for e in sim.events("settle"))


def test_a_drain_that_could_not_reach_the_settlement_is_left_for_the_next_run(tmp_path):
    sim = PairSim(tmp_path, pair_cfg={"settle_wait_s": 60})              # the hour settles 120 s after the window ends
    sim.run()
    assert sim.opps()[0]["paper_realized_pnl"] is None
    assert any("the next run settles them" in e["message"] for e in sim.events("settle"))
    assert sim.clock.now < HOUR


# ============================================================================================ smoke
def test_smoke_does_one_discovery_and_one_pricing_pass_and_exits(tmp_path):
    sim = PairSim(tmp_path, start_offset=-300)                          # 18:55: the pair markets are open
    summary = sim.run(smoke=True)
    assert summary["end_reason"] == "smoke pass done"
    names = [c[0] for c in sim.client.calls]
    assert names[:2] == ["open_markets", "open_markets"] and names.count("orderbook") == 5     # 15m + 4 strikes
    s = summary["smoke"]
    assert s["found"] and s["hour_ticker"] == m15_ticker() and len(s["strikes"]) == 4
    assert [w["strike_ticker"] for w in s["would_be_opportunities"]] == [hourly_ticker(85300.0)]
    assert sim.opps() == [] and sim.orders() == [] and len(sim.quotes()) == 4
    assert sim.clock.sleeps == []


def test_smoke_outside_the_window_shows_the_nearest_strikes_as_reference(tmp_path):
    sim = PairSim(tmp_path, start_offset=-3000)                          # 18:10: the 15m market for the hour is not open yet
    s = sim.run(smoke=True)["smoke"]
    assert s["found"] is False and "no open KXBTC15M market closes at this hour" in s["reason"]
    proxy = s["proxy"]
    assert proxy["reference_15m"] is not None and proxy["note"].startswith("reference only")
    assert [row["strike"] for row in proxy["hourly_strikes"]] == [D("85200"), D("85300"), D("85500"), D("85600")]
    assert proxy["hourly_strikes"][1]["yes_ask"] == D("0.50")
    assert sim.opps() == [] and sim.quotes() == []


def test_smoke_is_refused_for_live(tmp_path):
    with pytest.raises(ValueError, match="smoke"):
        PairSim(tmp_path, live=True).bot.run(smoke=True)


# ============================================================================================ live mode
def live_pair(tmp_path, **kw) -> PairSim:
    kw.setdefault("results", {m15_ticker(): "yes", hourly_ticker(85300.0): "yes"})
    return PairSim(tmp_path, live=True, **kw)


def two_cheap_strikes(ticker: str, now: float):
    if ticker in (hourly_ticker(85200.0), hourly_ticker(85300.0)):
        return HOURLY_CHEAP
    if ticker == m15_ticker():
        return M15_CHEAP
    return NOTHING


def test_live_both_legs_fill_in_one_batch_and_the_pair_is_tracked_end_to_end(tmp_path):
    sim = live_pair(tmp_path, latency=0.05)
    summary = sim.run()
    (batch,) = sim.client.batches
    assert [b["ticker"] for b in batch] == [hourly_ticker(85300.0), m15_ticker()]      # leg a, leg b
    h, m = batch
    assert (h["side"], h["price"], h["count"], h["time_in_force"]) == ("bid", "0.5000", "1.00", "immediate_or_cancel")
    assert (m["side"], m["price"], m["count"]) == ("ask", "0.5400", "1.00")           # NO 0.46 = YES 0.54 on the ask side
    assert h["exchange_index"] == m["exchange_index"] == 2 and "reduce_only" not in h
    assert sim.client.names("create_order") == []                                      # both legs went in one batch
    (opp,) = sim.opps()
    for t, outcome, price in ((hourly_ticker(85300.0), "yes", "0.5000"), (m15_ticker(), "no", "0.4600")):
        (row,) = sim.ledger.orders_for(t)
        assert row["status"] == "executed" and row["purpose"] == "pair_entry" and row["group_id"] == str(opp["id"])
        assert (row["outcome"], row["outcome_price"], row["time_in_force"]) == (outcome, price, "immediate_or_cancel")
    rows = {o["ticker"]: o for o in sim.orders()}
    assert all(o["status"] == "executed" and o["dry_run"] == 0 and o["filled"] == 1.0 for o in rows.values())
    assert rows[hourly_ticker(85300.0)]["side"] == "yes" and rows[m15_ticker()]["side"] == "no"
    assert rows[m15_ticker()]["avg_fill_price"] == pytest.approx(0.46)
    assert opp["outcome"] == "both_filled" and opp["live"] == 1
    assert opp["live_cost"] == pytest.approx(0.96) and opp["live_fees"] == pytest.approx(float(FEES_BELOW))
    assert opp["latency_ms"] > 100                                                    # detection -> both acks
    assert opp["payout"] == 1.0 and opp["realized_pnl"] == pytest.approx(float(EDGE_BELOW))
    assert opp["paper_realized_pnl"] == pytest.approx(float(EDGE_BELOW))
    assert sim.ledger.realized_pnl() == EDGE_BELOW and sim.ledger.open_worst_case() == 0
    assert sim.ledger.settled_tickers() == {hourly_ticker(85300.0), m15_ticker()} and not sim.bot._ledger_todo
    assert not [e for e in sim.events() if e["level"] == "error"], [e["message"] for e in sim.events("error")]
    assert summary["live_attempts"] == 1 and summary["outcomes"] == {"both_filled": 1} and not sim.safety.trips
    assert len(sim.safety.entries) == 2 and [e["role"] for e in sim.safety.entries] == ["hourly", "15m"]


def test_live_a_pair_that_lands_between_the_strike_and_the_target_pays_two_dollars(tmp_path):
    sim = live_pair(tmp_path, results={m15_ticker(): "no", hourly_ticker(85300.0): "yes"})
    sim.run()
    (opp,) = sim.opps()
    assert opp["payout"] == 2.0 and opp["realized_pnl"] == pytest.approx(float(2 - D("0.96") - FEES_BELOW))
    assert sim.ledger.realized_pnl() == 2 - D("0.96") - FEES_BELOW


def test_live_the_k_ge_t_pair_buys_15m_yes_and_hourly_no(tmp_path):
    def books(ticker, now):
        if ticker == hourly_ticker(85500.0):
            return asks_book("0.60", "0.47")                       # hourly NO 0.47
        if ticker == m15_ticker():
            return asks_book("0.48", "0.55")                       # 15m YES 0.48
        return NOTHING
    sim = live_pair(tmp_path, books=books, results={m15_ticker(): "yes", hourly_ticker(85500.0): "no"})
    sim.run()
    (batch,) = sim.client.batches
    assert [b["ticker"] for b in batch] == [m15_ticker(), hourly_ticker(85500.0)]
    assert (batch[0]["side"], batch[0]["price"]) == ("bid", "0.4800")                  # 15m YES at its ask
    assert (batch[1]["side"], batch[1]["price"]) == ("ask", "0.5300")                  # hourly NO 0.47 = YES 0.53
    (opp,) = sim.opps()
    assert opp["kind"] == ph.KIND_ABOVE and opp["outcome"] == "both_filled"
    assert opp["payout"] == 2.0                                                        # both won: T <= average <= K


def test_live_orders_are_priced_exactly_at_the_asks_with_to_book(tmp_path):
    sim = live_pair(tmp_path)
    sim.run()
    spec_h, spec_m = sim.safety.entries
    assert (spec_h["outcome"], spec_h["outcome_price"], spec_h["side"], spec_h["yes_price"]) == ("yes", D("0.50"), "bid", D("0.50"))
    assert (spec_m["outcome"], spec_m["outcome_price"], spec_m["side"], spec_m["yes_price"]) == ("no", D("0.46"), "ask", D("0.54"))
    for spec in (spec_h, spec_m):
        assert spec["time_in_force"] == "immediate_or_cancel" and spec["reduce_only"] is False and spec["purpose"] == "pair_entry"
        assert (spec["side"], spec["yes_price"]) == units.to_book(spec["outcome"], spec["outcome_price"])


def test_live_the_intents_are_committed_before_the_batch_is_sent(tmp_path):
    sim = live_pair(tmp_path)
    sim.run()                                  # FakeExchange.batch_create asserts it for every order, inside the call
    assert len(sim.client.created) == 2


def test_live_only_max_live_pairs_per_hour_pairs_are_sent(tmp_path):
    sim = live_pair(tmp_path, books=two_cheap_strikes)
    summary = sim.run()
    opps = sim.opps()
    assert [o["outcome"] for o in opps] == ["both_filled", "blocked_by_cap"]
    assert len(sim.client.batches) == 1 and summary["blocked"] == 1
    assert any("max_live_pairs_per_hour" in e["message"] for e in sim.events("cap"))


def test_live_two_pairs_in_an_hour_when_the_limit_says_so(tmp_path):
    sim = live_pair(tmp_path, books=two_cheap_strikes, pair_cfg={"max_live_pairs_per_hour": 2},
                    results={m15_ticker(): "yes", hourly_ticker(85200.0): "yes", hourly_ticker(85300.0): "yes"})
    sim.run()
    assert [o["outcome"] for o in sim.opps()] == ["both_filled", "both_filled"] and len(sim.client.batches) == 2
    assert sim.ledger.realized_pnl() == 2 * EDGE_BELOW


def test_live_a_restart_in_the_same_hour_keeps_the_pair_count(tmp_path):
    first = live_pair(tmp_path)
    first.run()
    assert len(first.client.batches) == 1
    second = PairSim(tmp_path, live=True, conn=first.conn, run_id="run-2", start_offset=-1000,
                     results={m15_ticker(): "yes", hourly_ticker(85300.0): "yes"})
    second.run()
    assert second.client.batches == [] and [o["outcome"] for o in second.opps()] == ["blocked_by_cap"]
    assert second.bot.live_pairs_hour == 1


def test_live_an_unreadable_ledger_means_no_live_pair_this_hour(tmp_path):
    sim = live_pair(tmp_path)

    def broken(ticker):
        raise sqlite3.OperationalError("database is locked")

    sim.ledger.orders_for = broken
    sim.run()
    assert sim.client.batches == [] and sim.opps()[0]["outcome"] == "blocked_by_cap"


# ---------------------------------------------------------------------------------------------- the fresh look
def flip_books(calls: dict, *, hourly_second=NOTHING, m15_second=None):
    """Detection sees the cheap pair; the SECOND look at 85300 (the confirm step) sees `hourly_second`, and the second
    look at the 15m market sees `m15_second`. Everything after that is cheap again."""
    def books(ticker, now):
        if ticker == hourly_ticker(85300.0):
            calls["h"] = calls.get("h", 0) + 1
            return hourly_second if calls["h"] == 2 else HOURLY_CHEAP
        if ticker == m15_ticker():
            calls["m"] = calls.get("m", 0) + 1
            return m15_second if (calls["m"] == 2 and m15_second is not None) else M15_CHEAP
        return NOTHING
    return books


def test_live_the_pair_is_priced_again_with_fresh_books_before_anything_is_sent(tmp_path):
    calls: dict = {}
    sim = live_pair(tmp_path, books=flip_books(calls), pair_cfg={"max_live_pairs_per_hour": 1})
    summary = sim.run()
    first = sim.opps()[0]
    assert first["outcome"] == "none_filled" and first["live"] == 0 and first["live_cost"] is None
    assert summary["gone"] == 1
    assert any("opportunity gone before the orders could be sent" in e["message"] and "edge is now" in e["message"]
               for e in sim.events("opportunity"))
    assert first["paper_cost"] == pytest.approx(0.96)                       # the paper twin keeps the detection prices
    # nothing was checked or sent for it: the only orders belong to the later, confirmed opportunity
    assert len(sim.safety.entries) == 2 and len(sim.client.batches) == 1


def test_live_a_vanished_edge_can_be_tried_again_when_it_comes_back(tmp_path):
    calls: dict = {}
    sim = live_pair(tmp_path, books=flip_books(calls))
    sim.run()
    opps = sim.opps()
    assert opps[0]["outcome"] == "none_filled" and opps[1]["outcome"] == "both_filled"    # re-detected, confirmed, sent
    assert len(sim.client.batches) == 1 and opps[1]["live"] == 1
    assert opps[1]["detected_ts"] > opps[0]["detected_ts"]


def test_live_the_orders_use_the_fresh_asks_and_the_paper_twin_keeps_the_detection_prices(tmp_path):
    calls: dict = {}
    sim = live_pair(tmp_path, books=flip_books(calls, hourly_second=HOURLY_CHEAP, m15_second=asks_book("0.57", "0.45")))
    sim.run()
    (batch,) = sim.client.batches
    assert (batch[1]["side"], batch[1]["price"]) == ("ask", "0.5500")                  # 15m NO now 0.45 = YES 0.55
    opp = sim.opps()[0]
    assert opp["paper_cost"] == pytest.approx(0.96) and opp["live_cost"] == pytest.approx(0.95)
    assert opp["live_fees"] == pytest.approx(float(micro_fee("1", "0.50") + micro_fee("1", "0.45")))
    assert opp["outcome"] == "both_filled"


@pytest.mark.parametrize("second,why", [
    (asks_book("0.50", "0.53", ys="0.50", ns="0.50"), "fewer contracts"),
    ((lv(("0.4000", "5")), []), "no ask"),
])
def test_live_the_fresh_look_also_checks_sizes_and_missing_sides(tmp_path, second, why):
    calls: dict = {}
    sim = live_pair(tmp_path, books=flip_books(calls, hourly_second=second))
    sim.run()
    assert sim.opps()[0]["outcome"] == "none_filled"
    assert any(why in e["message"] for e in sim.events("opportunity"))


def test_live_the_fresh_look_fails_when_a_book_cannot_be_read(tmp_path):
    sim = live_pair(tmp_path)
    orig = sim.client.orderbook
    state = {"n": 0}

    def flaky(ticker):
        state["n"] += 1
        if state["n"] == 5:          # poll: 15m, 85200, 85300 (the detection), then the fresh look: 15m, 85300 <- fails
            raise ConnectionError("boom")
        return orig(ticker)

    sim.client.orderbook = flaky
    sim.run()
    first = sim.opps()[0]
    assert first["outcome"] == "none_filled" and first["live"] == 0
    assert any("an order book could not be read" in e["message"] for e in sim.events("opportunity"))


def test_live_safety_is_warmed_for_both_markets_before_the_look(tmp_path):
    sim = live_pair(tmp_path)
    sim.run()
    assert [t for _, t in sim.safety.preflights[:2]] == [m15_ticker(), hourly_ticker(85300.0)]


@pytest.mark.parametrize("how", ["missing", "raises"])
def test_live_a_safety_without_a_working_preflight_still_trades(tmp_path, how):
    sim = live_pair(tmp_path)
    if how == "missing":
        sim.safety.preflight = None
    else:
        def boom(market):
            raise RuntimeError("no network")
        sim.safety.preflight = boom
    sim.run()
    assert len(sim.client.batches) == 1


# ---------------------------------------------------------------------------------------------- refusals and caps
def test_live_a_refused_leg_sends_nothing(tmp_path):
    sim = live_pair(tmp_path)
    sim.safety.verdicts = [(False, "balance too low")]
    sim.run()
    assert sim.client.created == [] and sim.opps()[0]["outcome"] == "blocked_by_cap"
    assert sim.ledger.orders_for(m15_ticker()) == [] and sim.ledger.orders_for(hourly_ticker(85300.0)) == []
    caps = sim.events("cap")
    assert "hourly YES leg refused by safety: balance too low" in caps[0]["message"]


def test_live_the_second_legs_refusal_also_sends_nothing(tmp_path):
    sim = live_pair(tmp_path)
    sim.safety.verdicts = [(True, ""), (False, "stale book")]
    sim.run()
    assert sim.client.created == [] and sim.ledger.open_orders() == []
    assert "15m NO leg refused by safety: stale book" in sim.events("cap")[0]["message"]


def test_live_safety_raising_fails_closed(tmp_path):
    sim = live_pair(tmp_path)
    sim.safety.verdicts = [RuntimeError("balance read failed")] * 5
    sim.run()
    assert sim.client.created == [] and any("check_entry failed" in e["message"] for e in sim.events("cap"))


@pytest.mark.parametrize("verdict", [None, "yes", 1, (None, "x"), ("ok", "x"), SimpleNamespace(), {}])
def test_live_unrecognised_safety_verdicts_are_refusals(tmp_path, verdict):
    sim = live_pair(tmp_path)
    sim.safety.verdicts = [verdict] * 5
    sim.run()
    assert sim.client.created == []


@pytest.mark.parametrize("verdict", [(True, ""), [True, "fine"], True, SimpleNamespace(ok=True, reason=""), {"ok": True}])
def test_live_the_common_safety_verdict_shapes_that_say_yes(tmp_path, verdict):
    sim = live_pair(tmp_path)
    sim.safety.verdicts = [verdict, verdict]
    sim.run()
    assert len(sim.client.batches) == 1


def test_live_combined_loss_rule_counts_both_legs(tmp_path):
    fits = live_pair(tmp_path / "fits")
    seed_loss(fits.ledger, 4)                                   # 3.8133 realized + 0.9951 worst case of the pair = 4.81
    fits.run()
    assert len(fits.client.batches) == 1
    blocked = live_pair(tmp_path / "blocked")
    seed_loss(blocked.ledger, 5)                                # 4.7666 + 0.9951 > 5.00
    blocked.run()
    assert blocked.client.batches == [] and blocked.opps()[0]["outcome"] == "blocked_by_cap"
    assert "combined loss rule" in blocked.events("cap")[0]["message"]
    assert len(blocked.safety.entries) == 2                     # Safety was asked leg by leg first (each leg fits alone)


def test_live_a_cap_that_leaves_no_room_for_a_pair_stops_the_bot(tmp_path):
    sim = live_pair(tmp_path)
    seed_loss(sim.ledger, 5)                                    # nothing open, realized 4.7666: no pair ever fits again
    summary = sim.run()
    assert sim.safety.halted and "no room for another pair" in sim.safety.trips[0]
    assert sim.client.created == [] and "no room" in summary["end_reason"]
    assert sim.opps()[0]["outcome"] == "blocked_by_cap"


def test_live_a_cap_refusal_from_safety_is_explained_by_the_bots_own_rule(tmp_path):
    sim = live_pair(tmp_path)
    seed_loss(sim.ledger, 5)
    sim.safety.verdicts = [(False, "loss cap: 4.77 used + 0.50 worst case of this order > $5")]
    summary = sim.run()
    assert sim.safety.halted and "no room" in summary["end_reason"] and sim.client.created == []


def test_live_a_refusal_that_is_not_the_cap_does_not_stop_the_bot(tmp_path):
    sim = live_pair(tmp_path)
    sim.safety.verdicts = [(False, "exposure cap: 3.5 open + 0.5 collateral > $3")]
    summary = sim.run()
    assert not sim.safety.halted and summary["end_reason"] == "finished" and sim.client.created == []


def test_live_open_positions_count_in_the_loss_rule(tmp_path):
    sim = live_pair(tmp_path)
    seed_loss(sim.ledger, 4)
    seed_loss(sim.ledger, 1, start=10, settle=False)            # an open worst case of 0.9533
    summary = sim.run()
    assert sim.client.batches == [] and "combined loss rule" in sim.events("cap")[0]["message"]
    assert not sim.safety.halted and summary["end_reason"] == "finished"      # room may free up when it settles


def test_live_the_per_order_collateral_ceiling_applies_to_each_leg(tmp_path):
    bot = live_pair(tmp_path).bot
    fits = ph.leg_spec(make_hourly(HOUR, 85300.0), "yes", D("0.50"), D("2"), purpose="pair_entry", role="hourly")
    assert bot._pair_loss_ok([fits])[0]                         # 2 contracts at 0.50: exactly 1.00 of collateral
    big = ph.leg_spec(make_hourly(HOUR, 85300.0), "yes", D("0.60"), D("2"), purpose="pair_entry", role="hourly")
    ok, why = bot._pair_loss_ok([big])
    assert not ok and "per-order ceiling" in why


def test_live_the_pairs_realized_loss_at_the_cap_trips_safety(tmp_path):
    sim = live_pair(tmp_path)
    state = {"loss": D("0")}
    sim.ledger.realized_loss_total = lambda: state["loss"]
    sim.ledger.realized_loss_24h = lambda: state["loss"]
    orig = sim.client.batch_create

    def batch(bodies):
        out = orig(bodies)
        state["loss"] = D("5.01")
        return out

    sim.client.batch_create = batch
    summary = sim.run()
    assert sim.safety.halted and "reached the 5.0 cap" in sim.safety.trips[0] and "reached" in summary["end_reason"]


# ---------------------------------------------------------------------------------------------- leg risk
def test_live_no_fill_at_all_is_none_filled_and_the_strike_may_come_back(tmp_path):
    sim = live_pair(tmp_path)
    sim.client.script = ["nofill", "nofill"]
    sim.run()
    (opp,) = sim.opps()
    assert opp["outcome"] == "none_filled" and opp["live"] == 1 and opp["live_cost"] == 0.0
    assert sim.ledger.open_worst_case() == 0 and sim.ledger.realized_loss_total() == 0
    assert [r["status"] for r in sim.ledger.orders_for(m15_ticker())] == ["canceled"]
    assert sim.bot.live_pairs_hour == 0 and sim.bot._strikes[hourly_ticker(85300.0)]["used"] == 0
    assert sim.reconcile.settled == [] and sim.clock.now < HOUR + 400            # nothing to wait for at the end


def test_live_one_leg_filled_the_missing_leg_is_retried_once_while_the_pair_still_pays(tmp_path):
    sim = live_pair(tmp_path)
    sim.client.script_by_ticker[m15_ticker()] = ["nofill", "fill"]
    sim.run()
    (opp,) = sim.opps()
    assert opp["outcome"] == "both_filled" and opp["live_cost"] == pytest.approx(0.96)
    t15 = sim.ledger.orders_for(m15_ticker())
    assert [(r["purpose"], r["status"]) for r in t15] == [("pair_entry", "canceled"), ("pair_retry", "executed")]
    assert len(sim.client.created) == 3 and len(sim.client.batches) == 1       # the retry is a single order
    assert sim.client.created[-1]["ticker"] == m15_ticker() and sim.safety.exits == []
    assert any("retry of the 15m NO leg filled 1" in e["message"] for e in sim.events("order"))
    assert opp["realized_pnl"] == pytest.approx(float(EDGE_BELOW))


def test_live_a_failed_retry_unwinds_with_a_reduce_only_ioc_within_two_ticks(tmp_path):
    sim = live_pair(tmp_path)
    sim.client.script_by_ticker[m15_ticker()] = ["nofill", "nofill"]
    sim.run()
    (opp,) = sim.opps()
    assert opp["outcome"] == "one_leg_unwound"
    unwind = sim.client.created[-1]
    # the YES held on the hourly market is sold by buying NO at the touch (0.53) plus two ticks (0.55)
    assert unwind["ticker"] == hourly_ticker(85300.0) and unwind["reduce_only"] is True
    assert (unwind["side"], unwind["price"], unwind["count"]) == ("ask", "0.4500", "1.00")
    assert unwind["time_in_force"] == "immediate_or_cancel"
    (exit_spec,) = sim.safety.exits
    assert exit_spec["purpose"] == "unwind" and exit_spec["reduce_only"] is True and exit_spec["outcome"] == "no"
    assert exit_spec["outcome_price"] == D("0.55") and exit_spec["role"] == "hourly"
    rows = sim.ledger.orders_for(hourly_ticker(85300.0))
    assert [(r["purpose"], r["status"], r["reduce_only"]) for r in rows] == [("pair_entry", "executed", 0), ("unwind", "executed", 1)]
    # YES at 0.50 plus NO at 0.55 pays exactly 1.00: a small loss, and the dashboard row says so
    fees = micro_fee("1", "0.50") + micro_fee("1", "0.55")
    assert opp["live_cost"] == pytest.approx(1.05) and opp["live_fees"] == pytest.approx(float(fees))
    assert opp["payout"] == 1.0 and opp["realized_pnl"] == pytest.approx(float(1 - D("1.05") - fees))
    assert sim.ledger.realized_pnl() == 1 - D("1.05") - fees
    assert any(e["kind"] == "unwind" and e["level"] == "warn" for e in sim.events())
    assert sim.bot.live_pairs_hour == 1


def test_live_the_unwind_row_shows_as_a_sale_of_the_held_outcome(tmp_path):
    sim = live_pair(tmp_path)
    sim.client.script_by_ticker[m15_ticker()] = ["nofill", "nofill"]
    sim.run()
    rows = [o for o in sim.orders() if o["ticker"] == hourly_ticker(85300.0)]
    entry, unwind = rows
    assert (entry["side"], entry["action"], entry["limit_price"]) == ("yes", "buy", pytest.approx(0.50))
    assert (unwind["side"], unwind["action"], unwind["limit_price"]) == ("yes", "sell", pytest.approx(0.45))
    assert unwind["avg_fill_price"] == pytest.approx(0.45) and unwind["filled"] == 1.0


def test_live_no_retry_when_the_pair_no_longer_pays_and_the_unwind_goes_straight_out(tmp_path):
    calls = {"m": 0}

    def books(ticker, now):
        if ticker == m15_ticker():
            calls["m"] += 1
            return M15_CHEAP if calls["m"] <= 2 else asks_book("0.57", "0.51")      # after the fill: NO ask 0.51
        return default_books(ticker, now)

    sim = live_pair(tmp_path, books=books)
    sim.client.script_by_ticker[m15_ticker()] = ["nofill"]
    sim.run()
    (opp,) = sim.opps()
    assert opp["outcome"] == "one_leg_unwound"
    assert [b["ticker"] for b in sim.client.created] == [hourly_ticker(85300.0), m15_ticker(), hourly_ticker(85300.0)]
    assert any("no retry for the missing 15m NO leg: pair edge now" in e["message"] for e in sim.events("unwind"))


def test_live_an_unfilled_unwind_leaves_the_position_held_and_says_so_loudly(tmp_path):
    sim = live_pair(tmp_path, results={}, pair_cfg={"settle_wait_s": 60})           # the market never settles in this test
    sim.client.script_by_ticker[m15_ticker()] = ["nofill", "nofill"]
    sim.client.script_by_ticker[hourly_ticker(85300.0)] = ["fill", "nofill"]            # the unwind does not fill
    sim.run()
    (opp,) = sim.opps()
    assert opp["outcome"] == "one_leg_held" and opp["live_cost"] == pytest.approx(0.50)
    assert any(e["level"] == "error" and e["kind"] == "unwind" for e in sim.events())
    assert sim.state.heartbeat_row()["detail"]["last_error"]
    assert sim.ledger.open_worst_case() == worst_of("0.50") - D("0.0176") + micro_fee("1", "0.50")   # the held YES: its cost + fee
    assert sim.bot.live_pairs_hour == 1


def test_live_a_held_position_that_settles_is_booked_like_any_other(tmp_path):
    sim = live_pair(tmp_path)                                                             # hourly YES wins
    sim.client.script_by_ticker[m15_ticker()] = ["nofill", "nofill"]
    sim.client.script_by_ticker[hourly_ticker(85300.0)] = ["fill", "nofill"]
    sim.run()
    (opp,) = sim.opps()
    assert opp["outcome"] == "one_leg_held" and opp["payout"] == 1.0
    assert opp["realized_pnl"] == pytest.approx(float(1 - D("0.50") - micro_fee("1", "0.50")))
    assert sim.ledger.open_worst_case() == 0 and sim.ledger.realized_pnl() == 1 - D("0.50") - micro_fee("1", "0.50")


def test_live_an_unwind_refused_by_safety_leaves_the_position_held(tmp_path):
    sim = live_pair(tmp_path)
    sim.client.script_by_ticker[m15_ticker()] = ["nofill", "nofill"]
    sim.safety.exit_verdicts = [(False, "price is not within 2 ticks")]
    sim.run()
    assert sim.opps()[0]["outcome"] == "one_leg_held"
    assert [b["ticker"] for b in sim.client.created].count(hourly_ticker(85300.0)) == 1       # no unwind was sent
    assert any("unwind of YES on" in e["message"] and "refused by safety" in e["message"] for e in sim.events("unwind"))


def test_live_an_unwind_with_no_bid_cannot_be_priced(tmp_path):
    state = {"empty": False}

    def books(ticker, now):
        if state["empty"] and ticker == hourly_ticker(85300.0):
            return ([], lv(("0.5000", "5")))                    # nobody bids YES: no NO ask to buy
        return default_books(ticker, now)

    sim = live_pair(tmp_path, books=books)
    sim.client.script_by_ticker[m15_ticker()] = ["nofill", "nofill"]
    orig = sim.client.batch_create

    def batch(bodies):
        out = orig(bodies)
        state["empty"] = True
        return out

    sim.client.batch_create = batch
    sim.run()
    assert sim.opps()[0]["outcome"] == "one_leg_held"
    assert any("no usable bid" in e["message"] for e in sim.events("unwind"))


def test_live_a_partial_fill_on_one_leg_retries_and_unwinds_only_the_difference(tmp_path):
    sim = live_pair(tmp_path)
    sim.client.script_by_ticker[m15_ticker()] = ["fill:0.40", "nofill"]                 # 0.40 of the 15m leg fills
    sim.run()
    (opp,) = sim.opps()
    assert opp["outcome"] == "one_leg_unwound"
    created = sim.client.created
    assert [(b["ticker"], b["count"]) for b in created] == [
        (hourly_ticker(85300.0), "1.00"), (m15_ticker(), "1.00"),            # the pair
        (m15_ticker(), "0.60"),                                              # the retry for the missing 0.60
        (hourly_ticker(85300.0), "0.60")]                                    # the unwind of the 0.60 still held
    assert created[-1]["reduce_only"] is True


def test_live_the_retry_is_one_attempt_only(tmp_path):
    sim = live_pair(tmp_path)
    sim.client.script_by_ticker[m15_ticker()] = ["nofill", "nofill", "fill"]
    sim.run()
    assert len([b for b in sim.client.created if b["ticker"] == m15_ticker()]) == 2       # entry + ONE retry


def test_live_a_retry_refused_by_safety_goes_straight_to_the_unwind(tmp_path):
    sim = live_pair(tmp_path)
    sim.client.script_by_ticker[m15_ticker()] = ["nofill"]
    sim.safety.verdicts = [(True, ""), (True, ""), (False, "cash")]                      # the third check_entry: the retry
    sim.run()
    assert sim.opps()[0]["outcome"] == "one_leg_unwound"
    assert any("retry of the 15m leg refused by safety: cash" in e["message"] for e in sim.events("cap"))


def test_live_a_rejected_leg_is_treated_like_an_unfilled_one(tmp_path):
    sim = live_pair(tmp_path)
    sim.client.script_by_ticker[m15_ticker()] = ["reject", "fill"]
    sim.run()
    assert sim.opps()[0]["outcome"] == "both_filled"
    assert [r["status"] for r in sim.ledger.orders_for(m15_ticker())] == ["rejected", "executed"]


# ---------------------------------------------------------------------------------------------- unknown outcomes
@pytest.mark.parametrize("mode", ["timeout_placed", "raise_placed"])
def test_live_unknown_batch_outcome_found_by_lookup_is_not_resent(tmp_path, mode):
    sim = live_pair(tmp_path)
    sim.client.batch_script = [mode]
    sim.run()
    assert len(sim.client.created) == 2 and len(sim.client.batches) == 1            # never resent
    (opp,) = sim.opps()
    assert opp["outcome"] == "both_filled" and not sim.safety.halted
    for t in (hourly_ticker(85300.0), m15_ticker()):
        (row,) = sim.ledger.orders_for(t)
        assert row["status"] == "executed" and row["order_id"]
    assert sum("found by lookup" in e["message"] for e in sim.events("order")) == 2


@pytest.mark.parametrize("mode", ["timeout_lost", "raise_lost"])
def test_live_unknown_batch_outcome_never_found_trips_safety_and_stops_without_resending(tmp_path, mode):
    sim = live_pair(tmp_path)
    sim.client.batch_script = [mode]
    summary = sim.run()
    assert len(sim.client.created) == 2 and len(sim.client.batches) == 1
    assert sim.safety.halted and "unknown" in sim.safety.trips[0] and "unknown" in summary["end_reason"]
    (opp,) = sim.opps()
    assert opp["outcome"] == "unknown" and opp["realized_pnl"] is None
    assert {o["status"] for o in sim.orders()} == {"unknown"}
    assert sim.ledger.unresolved_orders() and sim.ledger.open_worst_case() > 0       # counted as fully filled until resolved
    assert sim.state.heartbeat_row()["detail"]["last_error"]
    assert sim.reconcile.settled == [] and sim.clock.now < HOUR + 600                # no stalled shutdown


def test_live_one_leg_unknown_and_the_other_filled_halts_without_acting(tmp_path):
    sim = live_pair(tmp_path)

    def lost(body):
        return Result("unknown", None, None, {"code": "transport_error"}, 1, 10000.0, body["client_order_id"])

    sim.client.script_by_ticker[m15_ticker()] = [lost]
    summary = sim.run()
    assert [b["ticker"] for b in sim.client.created] == [hourly_ticker(85300.0), m15_ticker()]      # no retry, no unwind
    assert sim.safety.halted and sim.opps()[0]["outcome"] == "unknown" and sim.safety.exits == []
    assert "unknown" in summary["end_reason"]


def test_live_an_auth_failure_stops_everything(tmp_path):
    sim = live_pair(tmp_path)
    sim.client.script = ["auth", "auth"]
    summary = sim.run()
    assert len(sim.client.created) == 2 and sim.safety.halted and "authentication" in sim.safety.trips[0]
    assert "authentication" in summary["end_reason"]


def test_live_a_fill_on_the_wrong_outcome_trips_safety(tmp_path):
    sim = live_pair(tmp_path)
    orig = sim.client.fills

    def wrong(**kw):
        out = orig(**kw)
        for f in out:
            f["outcome_side"] = "no" if f["outcome_side"] == "yes" else "yes"
        return out

    sim.client.fills = wrong
    sim.run()
    assert sim.safety.halted and "filled on" in sim.safety.trips[0]


def test_live_a_fill_above_the_limit_trips_safety(tmp_path):
    sim = live_pair(tmp_path)
    orig = sim.client.fills

    def pricey(**kw):
        out = orig(**kw)
        for f in out:
            f["yes_price_dollars"], f["no_price_dollars"] = "0.9700", "0.0300"
        return out

    sim.client.fills = pricey
    sim.run()
    assert sim.safety.halted and "above its" in sim.safety.trips[0]


def test_live_exact_fill_prices_and_fees_replace_the_limit_estimates(tmp_path):
    sim = live_pair(tmp_path)
    orig = sim.client.fills

    def better(**kw):                           # each leg fills one tick better than its limit, with a different fee
        out = orig(**kw)
        for f in out:
            yes = D(f["yes_price_dollars"])
            yes = yes - D("0.0010") if f["outcome_side"] == "yes" else yes + D("0.0010")
            f["yes_price_dollars"], f["no_price_dollars"] = f"{yes:.4f}", f"{1 - yes:.4f}"
            f["fee_cost"] = "0.010000"
        return out

    sim.client.fills = better
    sim.run()
    (opp,) = sim.opps()
    assert opp["live_cost"] == pytest.approx(0.499 + 0.459) and opp["live_fees"] == pytest.approx(0.02)
    assert opp["realized_pnl"] == pytest.approx(1 - 0.499 - 0.459 - 0.02)
    assert {round(o["avg_fill_price"], 3) for o in sim.orders()} == {0.499, 0.459}
    assert not [e for e in sim.events("settle") if e["level"] == "error"]


# ---------------------------------------------------------------------------------------------- settlement and the ledger
def test_live_ledger_settlement_is_retried_until_the_exchange_publishes_it(tmp_path):
    sim = live_pair(tmp_path)
    sim.reconcile.ledger_lag_s = 200.0
    sim.run()
    assert sim.ledger.settled_tickers() == {hourly_ticker(85300.0), m15_ticker()} and not sim.bot._ledger_todo
    assert len(sim.reconcile.settled) >= 3
    early = sim.reconcile.snapshots[0]
    assert early[1] > 0 and early[2] == 0                       # the pair counted as OPEN worst case meanwhile
    (opp,) = sim.opps()
    assert opp["realized_pnl"] == pytest.approx(float(EDGE_BELOW))
    assert json.loads(sim.events("settle")[0]["data_json"])["ledger_recorded"] is False
    assert not [e for e in sim.events("settle") if e["level"] == "error"]


def test_live_the_cross_check_flags_a_pnl_the_ledger_disagrees_with(tmp_path):
    sim = live_pair(tmp_path)
    orig = sim.reconcile.settle

    def skewed(client, ledger, tickers):
        orig(client, ledger, tickers)
        ledger.conn.execute("UPDATE exec_settlements SET pnl = '0.5000'")        # the ledger now says something else
        ledger.conn.commit()

    sim.reconcile.settle = skewed
    sim.run()
    errs = [e for e in sim.events("settle") if e["level"] == "error"]
    assert errs and "ledger settlements" in errs[0]["message"]


def test_live_the_cross_check_stays_quiet_when_another_order_also_traded_one_of_the_markets(tmp_path):
    # the ledger's settlement revenue is paid on the NET position of a ticker, so with someone else's order on the same
    # market (another strategy, an earlier run) it is not comparable with this pair's own P&L
    sim = live_pair(tmp_path)
    send = sim.client.batch_create

    def send_then_someone_else_trades(bodies):
        out = send(bodies)
        seed_trade(sim.ledger, 1, ticker=m15_ticker(), price="0.60", outcome="no")
        return out

    sim.client.batch_create = send_then_someone_else_trades
    orig = sim.reconcile.settle

    def skewed(client, ledger, tickers):
        orig(client, ledger, tickers)
        ledger.conn.execute("UPDATE exec_settlements SET pnl = '0.5000'")
        ledger.conn.commit()

    sim.reconcile.settle = skewed
    sim.run()
    assert len(sim.ledger.fills_for(m15_ticker())) == 2
    assert not [e for e in sim.events("settle") if e["level"] == "error"]
    assert sim.state.heartbeat_row()["detail"]["last_error"] is None
    (opp,) = sim.opps()                                         # and the bot still books exactly its own two orders
    assert opp["realized_pnl"] == pytest.approx(float(EDGE_BELOW))


def test_live_a_pair_that_never_filled_is_not_queued_for_ledger_settlement(tmp_path):
    sim = live_pair(tmp_path)
    sim.client.script = ["nofill", "nofill"]
    sim.run()
    assert sim.bot._ledger_todo == {} and sim.reconcile.settled == []


# ---------------------------------------------------------------------------------------------- lifecycle
def test_live_cancel_path_runs_at_the_end_with_the_touched_tickers(tmp_path):
    sim = live_pair(tmp_path)
    sim.run()
    ((reason, tickers),) = sim.reconcile.cancel_paths
    assert reason == "finished" and tickers == sorted([hourly_ticker(85300.0), m15_ticker()])
    assert sim.reconcile.reconciled and sorted(sim.reconcile.reconciled[0]) == tickers


def test_live_kill_file_appearing_before_the_send_prevents_the_orders(tmp_path):
    sim = live_pair(tmp_path)
    orig = sim.safety.check_entry
    n = {"i": 0}

    def check_then_kill(spec, book, market, **kw):
        res = orig(spec, book, market, **kw)
        n["i"] += 1
        if n["i"] == 2:
            sim.kill_file.write_text("kill\n")
        return res

    sim.safety.check_entry = check_then_kill
    summary = sim.run()
    assert sim.client.created == [] and summary["end_reason"] == "kill switch" and sim.ledger.open_orders() == []


def test_live_a_halted_safety_blocks_orders_and_stops_the_run(tmp_path):
    sim = live_pair(tmp_path)
    sim.safety.halted = True
    summary = sim.run()
    assert sim.client.created == [] and summary["end_reason"] == "safety is halted"


def test_live_halted_may_be_a_method(tmp_path):
    sim = live_pair(tmp_path)
    sim.safety.halted = lambda: True
    summary = sim.run()
    assert sim.client.created == [] and summary["end_reason"] == "safety is halted"


def test_live_a_safety_trip_stops_the_run_and_the_drain_still_books_the_paper_twin(tmp_path):
    def late_pair(ticker, now):                  # the pair only shows up 200 s before the close
        return default_books(ticker, now) if now >= HOUR - 200 else NOTHING

    sim = live_pair(tmp_path, books=late_pair)
    sim.client.batch_script = ["timeout_lost"]
    sim.run()
    assert sim.kill_file.exists() and sim.safety.halted
    assert sim.opps()[0]["paper_realized_pnl"] is not None            # the drain after a trip only reads


def test_live_a_trip_early_in_the_window_does_not_wait_for_a_settlement_it_cannot_reach(tmp_path):
    sim = live_pair(tmp_path)
    sim.client.batch_script = ["timeout_lost"]
    sim.run()                                                         # trips at 18:45; the hour settles at 19:01
    assert sim.safety.halted and sim.clock.now < HOUR - 800            # it did not sit out the drain
    assert any("the next run settles them" in e["message"] for e in sim.events("settle"))
    assert sim.opps()[0]["paper_realized_pnl"] is None
    # the next run, hours later, settles that row on its own row
    second = PairSim(tmp_path / "again" if (tmp_path / "again").mkdir() is None else tmp_path, conn=sim.conn,
                     run_id="run-2", start_offset=7200, books=lambda t, now: NOTHING,
                     results={m15_ticker(): "yes", hourly_ticker(85300.0): "yes"})
    second.run(hours=1)
    row = sim.state.opportunity_row(sim.opps()[0]["id"])
    assert row["paper_realized_pnl"] == pytest.approx(float(EDGE_BELOW)) and second.bot.summary["recovered"] == 1


def test_live_crash_runs_the_cancel_path_and_records_the_error(tmp_path):
    sim = live_pair(tmp_path)

    def locked(**kw):
        raise RuntimeError("database is locked")

    sim.state.opportunity = locked
    with pytest.raises(RuntimeError, match="database is locked"):
        sim.run()
    assert sim.reconcile.cancel_paths and sim.reconcile.cancel_paths[0][0].startswith("error: RuntimeError")
    run = sim.state.run_row()
    assert run["end_reason"].startswith("error: RuntimeError") and run["ended_ts"] is not None
    assert sim.state.heartbeat_row()["status"] == "stopped" and sim.state.heartbeat_row()["detail"]["last_error"]


def test_live_ctrl_c_stops_cleanly_and_runs_the_cancel_path(tmp_path):
    sim = live_pair(tmp_path, books=lambda t, now: NOTHING)
    orig = sim.client.orderbook
    n = {"i": 0}

    def interrupt(ticker):
        n["i"] += 1
        if n["i"] == 12:
            raise KeyboardInterrupt
        return orig(ticker)

    sim.client.orderbook = interrupt
    with pytest.raises(KeyboardInterrupt):
        sim.run()
    assert sim.state.run_row()["end_reason"] == "interrupted (Ctrl+C)" and sim.reconcile.cancel_paths


def test_live_a_failing_reconcile_or_cancel_path_does_not_hide_the_result(tmp_path):
    sim = live_pair(tmp_path)
    sim.reconcile.raise_on = {"reconcile", "settle"}

    def bad_cancel(client, ledger, reason, tickers):
        raise RuntimeError("cancel path exploded")

    sim.reconcile.cancel_path = bad_cancel
    sim.run()
    assert len(sim.client.batches) == 1
    assert any("reconcile failed" in e["message"] for e in sim.events("error"))
    assert any("cancel path failed" in e["message"] for e in sim.events("error"))


def test_live_heartbeat_reports_loss_balance_and_keeps_the_5_second_rule(tmp_path):
    sim = live_pair(tmp_path)
    sim.run()
    assert sim.max_beat_gap() <= 5.0
    d = sim.state.heartbeat_row()["detail"]
    assert d["mode"] == "live" and d["balance_usd"] == 29.43 and d["loss_used_usd"] == 0.0
    assert d["kill_switch"] is False and d["max_loss_usd"] == 5.0


def test_live_a_silent_lookup_still_heartbeats(tmp_path):
    sim = live_pair(tmp_path)
    sim.client.batch_script = ["timeout_lost"]
    sim.run()
    assert sim.max_beat_gap() <= 5.0


def test_live_run_leaves_no_secrets_in_any_table(tmp_path):
    sim = live_pair(tmp_path)
    sim.run()
    blob = []
    for (name,) in sim.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for row in sim.conn.execute(f"SELECT * FROM {name}").fetchall():
            blob.append(" ".join(str(v) for v in row))
    text = "\n".join(blob).lower()
    for needle in ("private key", "begin ", "signature", "kalshi-access", "authorization"):
        assert needle not in text
    assert "abcd1234" in text


def test_live_a_watch_run_with_a_live_capable_client_still_sends_nothing(tmp_path):
    sim = PairSim(tmp_path, live=False)
    sim.client.live = True
    sim.run()
    assert sim.client.created == [] and len(sim.opps()) == 1


# ---------------------------------------------------------------------------------------------- review: the pair as a whole
def test_live_safety_is_asked_about_both_legs_together(tmp_path):
    """Review finding: each leg was checked alone, so the caps never saw the pair. Both checks now carry both specs."""
    sim = live_pair(tmp_path)
    sim.run()
    assert len(sim.safety.entries) == 2 and len(sim.safety.batches) == 2
    for batch in sim.safety.batches:
        assert [s["role"] for s in batch] == ["hourly", "15m"]                        # the whole pair, in both calls
    assert sim.safety.batches[0] == sim.safety.batches[1]


def test_live_the_bots_own_exposure_rule_backs_safety_up(tmp_path):
    """Open exposure 2.50 plus the pair's collateral 0.96 is 3.46 > 3.00, even if Safety were to say yes to both legs."""
    sim = live_pair(tmp_path)
    seed_loss(sim.ledger, 5, price="0.50", settle=False)                               # five open positions: 2.50
    assert sim.ledger.open_exposure() == D("2.50")
    sim.run()
    assert sim.client.created == [] and sim.opps()[0]["outcome"] == "blocked_by_cap"
    assert "combined exposure rule" in sim.events("cap")[0]["message"] and not sim.safety.halted
    assert sim.ledger.open_exposure() == D("2.50")                                      # nothing was added


def test_live_a_pair_that_fits_the_exposure_rule_goes_out(tmp_path):
    sim = live_pair(tmp_path)
    seed_loss(sim.ledger, 4, price="0.50", settle=False)                               # 2.00 open + 0.96 <= 3.00
    sim.run()
    assert len(sim.client.batches) == 1 and sim.opps()[0]["outcome"] == "both_filled"


def test_live_a_restart_keeps_the_pair_count_when_the_15m_leg_never_filled(tmp_path):
    """Review finding: the count read from the ledger looked at the 15m ticker's rows only, so a pair whose HOURLY leg
    filled and whose 15m leg did not (then unwound) was forgotten, and a restart in the same hour sent another pair."""
    first = live_pair(tmp_path)
    first.client.script_by_ticker[m15_ticker()] = ["nofill", "nofill"]                 # entry and retry: the 15m leg never fills
    first.run()
    assert first.opps()[0]["outcome"] == "one_leg_unwound" and first.bot.live_pairs_hour == 1
    assert {r["status"] for r in first.ledger.orders_for(m15_ticker())} == {"canceled"}   # nothing on the 15m ticker traded
    second = PairSim(tmp_path, live=True, conn=first.conn, run_id="run-2", start_offset=-1000,
                     results={m15_ticker(): "yes", hourly_ticker(85300.0): "yes"})
    second.run()
    assert second.client.batches == [] and [o["outcome"] for o in second.opps()] == ["blocked_by_cap"]
    assert second.bot.live_pairs_hour == 1


def test_live_a_restart_keeps_the_pair_count_for_a_held_leg_too(tmp_path):
    first = live_pair(tmp_path)
    first.client.script_by_ticker[m15_ticker()] = ["nofill", "nofill"]
    first.client.script_by_ticker[hourly_ticker(85300.0)] = ["fill", "nofill"]         # the unwind does not fill either
    first.run()
    assert first.opps()[0]["outcome"] == "one_leg_held"
    second = PairSim(tmp_path, live=True, conn=first.conn, run_id="run-2", start_offset=-1000, results={})
    second.run()
    assert second.client.batches == [] and second.bot.live_pairs_hour == 1


def test_live_a_restart_does_not_count_a_pair_where_nothing_filled(tmp_path):
    """The in-memory rule counts every outcome but none_filled, and so does the count read back from the ledger."""
    first = live_pair(tmp_path)
    first.client.script = ["nofill", "nofill"]
    first.run()
    assert first.opps()[0]["outcome"] == "none_filled" and first.bot.live_pairs_hour == 0
    second = PairSim(tmp_path, live=True, conn=first.conn, run_id="run-2", start_offset=-1000,
                     results={m15_ticker(): "yes", hourly_ticker(85300.0): "yes"})
    second.run()
    assert len(second.client.batches) == 1 and second.opps()[0]["outcome"] == "both_filled"


def test_live_the_count_counts_pairs_not_orders(tmp_path):
    """A pair is one opportunity however many orders it needed (entry, retry, unwind share its group id)."""
    first = live_pair(tmp_path, pair_cfg={"max_live_pairs_per_hour": 2}, books=two_cheap_strikes,
                      results={m15_ticker(): "yes", hourly_ticker(85200.0): "yes", hourly_ticker(85300.0): "yes"})
    first.client.script_by_ticker[m15_ticker()] = ["nofill", "fill"]                    # a retry in the first pair
    first.run()
    assert [o["outcome"] for o in first.opps()] == ["both_filled", "both_filled"]
    assert len(first.ledger.orders_for(m15_ticker())) == 3                              # entry, retry, entry of pair 2
    assert first.bot._ledger_pairs([m15_ticker(), hourly_ticker(85200.0), hourly_ticker(85300.0)]) == 2


# ---------------------------------------------------------------------------------------------- review: crossed books, the window
def test_a_crossed_15m_book_makes_no_phantom_opportunity_and_does_not_use_up_the_strike(tmp_path):
    crossed15 = (lv(("0.6000", "500")), lv(("0.4500", "500")))                          # NO ask 0.40: a 6.5 cent 'edge'

    def books(ticker, now):
        if ticker == m15_ticker() and now < HOUR - 800:
            return crossed15                                                            # a transitional snapshot at the start
        return default_books(ticker, now)

    sim = PairSim(tmp_path, books=books)
    sim.run()
    (opp,) = sim.opps()                                                                 # only the real one, once the book is sane
    assert opp["detected_ts"] >= HOUR - 800 and opp["paper_cost"] == pytest.approx(0.96)
    assert [q for q in sim.quotes() if q["ts"] < HOUR - 800 and q["strike_ticker"] == hourly_ticker(85300.0)] == []
    (tmp_path / "live").mkdir()
    live = live_pair(tmp_path / "live", books=books)
    live.run()
    assert [o["outcome"] for o in live.opps()] == ["both_filled"] and not live.events("cap")
    assert len(live.client.batches) == 1


def test_full_stack_a_crossed_book_would_have_used_up_the_strike_with_a_refusal(tmp_path):
    """The reviewer's failure: the phantom pair was refused by the real Safety ('crossed or locked') and, with one
    opportunity per strike and hour, the strike got no second look when a real edge appeared."""
    crossed15 = (lv(("0.6000", "500")), lv(("0.4500", "500")))

    def books(ticker, now):
        return crossed15 if ticker == m15_ticker() and now < HOUR - 800 else default_books(ticker, now)

    sim = real_pair(tmp_path, books=books)
    sim.run()
    assert [o["outcome"] for o in sim.opps()] == ["both_filled"] and not sim.events("cap")
    assert sim.opps()[0]["detected_ts"] >= HOUR - 800


def test_a_pair_that_appears_after_the_window_ends_is_not_a_paper_twin(tmp_path):
    """With the window ending 45 s before the close, an edge that first shows 35 s before it is neither paper-traded nor
    'blocked by a cap': the bot is simply not trading any more (it is past what Safety would accept)."""
    sim = PairSim(tmp_path, pair_cfg={"stop_before_close_s": 45},
                  books=lambda t, now: default_books(t, now) if now >= HOUR - 35 else NOTHING)
    sim.run()
    assert sim.opps() == [] and sim.orders() == []


def test_full_stack_every_pair_the_window_detects_can_also_be_traded_live(tmp_path):
    """The window's last second is still before Safety's entry cutoff: a pair that first shows 46 s before the close (the
    last poll of a 45 s window) is sent, not refused for 'closes in 46 s, less than ...'."""
    sim = real_pair(tmp_path, pair_cfg={"stop_before_close_s": 45}, trading={"min_seconds_to_close": 20},
                    books=lambda t, now: default_books(t, now) if now >= HOUR - 46.5 else NOTHING)
    sim.run()
    assert [o["outcome"] for o in sim.opps()] == ["both_filled"] and not sim.events("cap")
    assert sim.opps()[0]["detected_ts"] >= HOUR - 46.5 and len(sim.client.batches) == 1


def test_live_a_pair_that_slips_past_the_cutoff_while_it_is_being_looked_at_is_gone_not_blocked(tmp_path):
    jumped = {"done": False}

    def books(ticker, now):
        return default_books(ticker, now) if now >= HOUR - 50 else NOTHING

    sim = live_pair(tmp_path, books=books, pair_cfg={"stop_before_close_s": 41}, trading={"min_seconds_to_close": 30})
    orig = sim.client.orderbook

    def slow(ticker):
        if sim.clock.now >= HOUR - 50 and not jumped["done"]:
            jumped["done"] = True
            sim.clock.now += 20.0                                                       # a slow network: now 30 s to the close
        return orig(ticker)

    sim.client.orderbook = slow
    sim.run()
    (opp,) = sim.opps()
    assert opp["outcome"] == "none_filled" and sim.client.created == [] and not sim.events("cap")
    assert any("less than the 40 s Safety needs" in e["message"] for e in sim.events("opportunity"))
    assert sim.bot._strikes[hourly_ticker(85300.0)]["used"] == 0                        # nothing was sent: not used up


# ---------------------------------------------------------------------------------------------- review: the last look before a send
def test_live_a_kill_file_that_appears_after_the_intents_were_committed_prevents_the_batch(tmp_path):
    sim = live_pair(tmp_path)
    orig = sim.ledger.new_intent
    n = {"i": 0}

    def commit_then_stop(spec):
        coid = orig(spec)
        n["i"] += 1
        if n["i"] == 2:
            sim.kill_file.write_text("stop\n")                                          # STOP, with both intents committed
        return coid

    sim.ledger.new_intent = commit_then_stop
    summary = sim.run()
    assert sim.client.created == [] and sim.client.batches == [] and summary["end_reason"] == "kill switch"
    rows = sim.ledger.orders_for(m15_ticker()) + sim.ledger.orders_for(hourly_ticker(85300.0))
    assert len(rows) == 2 and {r["status"] for r in rows} == {"presumed_not_placed"}
    assert all("not sent: kill switch" in r["error"] for r in rows)
    assert sim.ledger.open_worst_case() == 0 and sim.ledger.unresolved_orders() == []
    assert {(o["status"], o["dry_run"]) for o in sim.orders() if o["status"] == "not_sent"} == {("not_sent", 1)}
    (opp,) = sim.opps()
    assert opp["live"] == 0 and opp["outcome"] == "blocked_by_cap" and summary["live_attempts"] == 0


def test_live_a_stalled_commit_sends_nothing_stale_but_the_next_look_may(tmp_path):
    """The reviewer's stall: the commits wait for a lock, so the batch would leave 5 s after its books were read."""
    sim = live_pair(tmp_path)
    orig = sim.ledger.mark_sent
    state = {"n": 0}

    def slow_mark_sent(ids, ts=None):
        state["n"] += 1
        if state["n"] == 1:
            sim.clock.now += 5.0
        return orig(ids, ts)

    sim.ledger.mark_sent = slow_mark_sent
    summary = sim.run()
    assert len(sim.client.batches) == 1                                                 # only the fresh look was sent
    assert [o["outcome"] for o in sim.opps()] == ["none_filled", "both_filled"]
    stale = [r for t in (hourly_ticker(85300.0), m15_ticker()) for r in sim.ledger.orders_for(t)
             if r["status"] == "presumed_not_placed"]
    assert len(stale) == 2 and all("order book is 5.0 s old" in r["error"] for r in stale)
    assert any("checked again after the intents were committed" in e["message"] for e in sim.events("opportunity"))
    assert summary["live_attempts"] == 1


def test_live_intents_whose_send_time_could_not_be_stamped_are_not_sent(tmp_path):
    sim = live_pair(tmp_path)
    orig = sim.ledger.mark_sent

    def failing(ids, ts=None):
        raise sqlite3.OperationalError("database is locked")

    sim.ledger.mark_sent = failing
    sim.run()
    assert sim.client.created == [] and sim.opps()[0]["outcome"] == "blocked_by_cap"
    rows = sim.ledger.orders_for(m15_ticker()) + sim.ledger.orders_for(hourly_ticker(85300.0))
    assert {r["status"] for r in rows} == {"presumed_not_placed"} and all("could not stamp" in r["error"] for r in rows)


def test_live_safety_halting_while_the_intents_are_committed_prevents_the_batch(tmp_path):
    sim = live_pair(tmp_path)
    orig = sim.ledger.new_intent
    n = {"i": 0}

    def commit_then_halt(spec):
        coid = orig(spec)
        n["i"] += 1
        if n["i"] == 2:
            sim.safety.halted = True
        return coid

    sim.ledger.new_intent = commit_then_halt
    summary = sim.run()
    assert sim.client.batches == [] and summary["end_reason"] == "safety is halted"
    assert sim.state.heartbeat_row()["detail"]["last_error"]


# ---------------------------------------------------------------------------------------------- review: a 401 stops the bot
def test_live_a_401_on_the_balance_read_trips_safety_and_stops_the_bot(tmp_path):
    from kpm.execution.client import AuthError
    sim = live_pair(tmp_path)
    sim.client.fail["balance"] = AuthError("GET /trade-api/v2/portfolio/balance: HTTP 401", status=401)
    summary = sim.run()
    assert sim.safety.halted and "authentication refused" in sim.safety.trips[0]
    assert "authentication refused" in summary["end_reason"] and sim.client.created == []
    assert "authentication refused" in sim.state.heartbeat_row()["detail"]["last_error"]
    assert len(sim.client.names("balance")) == 1


def test_full_stack_a_401_on_one_of_safetys_reads_stops_the_pair_bot(tmp_path):
    from kpm.execution.client import AuthError
    sim = real_pair(tmp_path)
    sim.client.fail["positions"] = AuthError("GET /trade-api/v2/portfolio/positions: HTTP 401", status=401)
    summary = sim.run(hours=2)
    assert sim.client.created == [] and sim.safety.halted and sim.kill_file.exists()
    assert summary["end_reason"] in ("safety is halted", "kill switch") and summary["hours"] == 1       # not the next hour
    assert "authentication refused" in sim.state.heartbeat_row()["detail"]["last_error"]
    assert len(sim.client.names("positions")) == 1


# ---------------------------------------------------------------------------------------------- review: the PC clock
def test_live_the_bot_keeps_the_clock_check_alive_while_it_waits_and_while_it_polls(tmp_path):
    sim = live_pair(tmp_path)
    sim.run()
    assert len(sim.safety.clock_polls) > 100 and sim.safety.clock_polls[0] < HOUR - 900
    assert sim.state.heartbeat_row()["detail"]["clock_skew_s"] == pytest.approx(0.1)


def test_full_stack_a_pc_clock_that_drifted_sends_no_pair_and_is_not_a_trip(tmp_path):
    sim = real_pair(tmp_path, trading={"clock_recheck_s": 30})
    sim.client.skew_s = 25.0
    sim.run()
    assert sim.client.created == [] and not sim.safety.halted and not sim.kill_file.exists()
    (opp,) = sim.opps()
    assert opp["outcome"] == "blocked_by_cap" and opp["paper_cost"] == pytest.approx(0.96)     # the paper twin is still written
    assert any("clock skew +25.00" in e["message"] for e in sim.events("cap"))
    assert any("PC clock" in e["message"] for e in sim.events("error"))


# ---------------------------------------------------------------------------------------------- review: a wall clock that steps back
def test_a_wall_clock_that_steps_backwards_never_causes_one_long_sleep(tmp_path):
    sim = PairSim(tmp_path, books=lambda t, now: NOTHING)
    orig = sim.client.orderbook
    n = {"i": 0}

    def book(ticker):
        n["i"] += 1
        if n["i"] == 7:
            sim.clock.now -= 300.0
            sim.kill_file.write_text("stop\n")
        return orig(ticker)

    sim.client.orderbook = book
    summary = sim.run()
    assert summary["end_reason"] == "kill switch"
    assert max(sim.clock.sleeps) <= ph.TICK_S + 1e-9
    assert n["i"] <= 12                                                                  # STOP was noticed within a poll or two


def test_the_heartbeat_keeps_beating_after_the_wall_clock_steps_back(tmp_path):
    sim = PairSim(tmp_path, books=lambda t, now: NOTHING)
    orig = sim.client.orderbook
    n = {"i": 0, "stepped_at": None}

    def book(ticker):
        n["i"] += 1
        if n["i"] == 7:
            sim.clock.now -= 300.0
            n["stepped_at"] = sim.clock.now
        return orig(ticker)

    sim.client.orderbook = book
    sim.run()
    t0 = n["stepped_at"]
    assert len([t for t, s in sim.state.beats if t0 <= t <= t0 + 10.0 and s == "monitoring"]) >= 4


# ============================================================================================ the real Safety and reconcile
def real_pair(tmp_path, **kw) -> PairSim:
    return real_stack(live_pair(tmp_path, with_safety=False, **kw))


def test_full_stack_a_pair_end_to_end_with_the_real_safety_and_reconcile(tmp_path):
    sim = real_pair(tmp_path)
    summary = sim.run()
    assert summary["end_reason"] == "finished" and len(sim.client.batches) == 1
    (opp,) = sim.opps()
    assert opp["outcome"] == "both_filled" and opp["realized_pnl"] == pytest.approx(float(EDGE_BELOW))
    assert sim.ledger.realized_pnl() == EDGE_BELOW and sim.ledger.open_worst_case() == 0
    assert not [e for e in sim.events() if e["level"] == "error"], [e["message"] for e in sim.events("error")]
    assert not sim.safety.halted and not sim.kill_file.exists()
    audit = [r[0] for r in sim.conn.execute("SELECT action FROM audit_log WHERE component = 'exec.reconcile'")]
    assert {"reconcile", "settle", "cancel_path"} <= set(audit)


def test_full_stack_the_k_ge_t_pair_passes_the_real_checks_too(tmp_path):
    def books(ticker, now):
        if ticker == hourly_ticker(85500.0):
            return asks_book("0.60", "0.47")
        if ticker == m15_ticker():
            return asks_book("0.48", "0.55")
        return NOTHING
    sim = real_pair(tmp_path, books=books, results={m15_ticker(): "yes", hourly_ticker(85500.0): "yes"})
    sim.run()
    assert sim.opps()[0]["outcome"] == "both_filled" and not [e for e in sim.events() if e["level"] == "error"]


def test_full_stack_a_missing_leg_is_retried_through_the_real_safety(tmp_path):
    sim = real_pair(tmp_path)
    sim.client.script_by_ticker[m15_ticker()] = ["nofill", "fill"]
    sim.run()
    assert sim.opps()[0]["outcome"] == "both_filled" and not sim.events("cap")
    assert [r["purpose"] for r in sim.ledger.orders_for(m15_ticker())] == ["pair_entry", "pair_retry"]


def test_full_stack_the_real_safety_accepts_the_reduce_only_unwind(tmp_path):
    sim = real_pair(tmp_path)
    sim.client.script_by_ticker[m15_ticker()] = ["nofill", "nofill"]
    sim.run()
    (opp,) = sim.opps()
    assert opp["outcome"] == "one_leg_unwound", [e["message"] for e in sim.events("unwind")]
    assert sim.client.created[-1]["reduce_only"] is True and not sim.safety.halted
    fees = micro_fee("1", "0.50") + micro_fee("1", "0.55")
    assert sim.ledger.realized_pnl() == 1 - D("1.05") - fees          # YES 0.50 + NO 0.55 settled through the real settle()


def test_full_stack_unknown_batch_outcome_resolved_by_the_real_reconcile(tmp_path):
    sim = real_pair(tmp_path)
    sim.client.batch_script = ["timeout_placed"]
    sim.run()
    assert len(sim.client.batches) == 1 and sim.opps()[0]["outcome"] == "both_filled" and not sim.safety.halted
    assert not [e for e in sim.events() if e["level"] == "error"], [e["message"] for e in sim.events("error")]


def test_full_stack_unknown_batch_outcome_never_found_halts_the_real_safety(tmp_path):
    sim = real_pair(tmp_path)
    sim.client.batch_script = ["raise_lost"]
    sim.run()
    assert len(sim.client.batches) == 1 and sim.safety.halted and sim.kill_file.exists()
    spec = ph.leg_spec(make_hourly(HOUR + 3600, 85300.0), "yes", D("0.5"), D("1"), purpose="pair_entry")
    assert sim.safety.check_entry(spec, {}, {})[0] is False


def test_full_stack_settlement_lag_is_handled_by_the_real_settle(tmp_path):
    sim = real_pair(tmp_path)
    sim.client.settlement_lag_s = 150.0
    sim.run()
    assert sim.ledger.settled_tickers() == {hourly_ticker(85300.0), m15_ticker()} and not sim.bot._ledger_todo
    assert sim.ledger.realized_pnl() == EDGE_BELOW
    assert not [e for e in sim.events() if e["level"] == "error"]


def test_full_stack_warm_caches_make_the_second_look_fast_enough_for_the_real_freshness_rule(tmp_path):
    # every call takes 0.4 s; the real Safety re-reads the market listing, status, cash, position and resting orders
    sim = real_pair(tmp_path, latency=0.4)
    sim.run()
    assert len(sim.client.batches) == 1 and not sim.events("cap"), [e["message"] for e in sim.events("cap")]


def test_full_stack_a_halted_real_safety_blocks_the_pair(tmp_path):
    sim = real_pair(tmp_path)
    sim.safety.trip("test: stop everything")
    summary = sim.run()
    assert sim.client.created == [] and sim.kill_file.exists()
    assert summary["end_reason"] in ("kill switch present at start", "safety is halted")


def test_full_stack_the_real_safety_checks_the_two_legs_together_against_the_exposure_cap(tmp_path):
    """Review finding, with the reviewer's numbers: 2.50 of unsettled positions open, a pair with legs at 0.50 and 0.46.
    Each leg alone fits (2.50 + 0.50 = 3.00, 2.50 + 0.46 = 2.96) but together they leave 3.46 open against a cap of 3.00;
    the batch used to go out. Here the bot's own exposure backstop is taken out, so it is SAFETY that refuses."""
    sim = real_pair(tmp_path)
    seed_loss(sim.ledger, 5, price="0.50", settle=False)
    sim.bot._pair_loss_ok = lambda specs: (True, "")
    sim.run()
    assert sim.client.created == [] and sim.client.batches == []
    assert sim.opps()[0]["outcome"] == "blocked_by_cap"
    why = sim.events("cap")[0]["message"]
    assert "refused by safety" in why and "exposure cap" in why and "these 2 orders" in why and "2.5" in why
    assert sim.ledger.open_exposure() == D("2.50") and not sim.safety.halted           # nothing was added; not a trip


def test_full_stack_the_pairs_two_orders_count_in_the_session_cap(tmp_path):
    sim = real_pair(tmp_path, trading={"max_orders_per_session": 3})
    seed_loss(sim.ledger, 2)                                                            # two earlier orders of this run
    sim.run()
    assert sim.client.batches == [] and sim.opps()[0]["outcome"] == "blocked_by_cap"
    why = sim.events("cap")[0]["message"]
    assert "session order cap" in why and "2 more in this batch" in why and not sim.safety.halted
    (tmp_path / "ok").mkdir()
    ok = real_pair(tmp_path / "ok", trading={"max_orders_per_session": 4})
    seed_loss(ok.ledger, 2)
    ok.run()
    assert len(ok.client.batches) == 1                                                  # 2 + 2 <= 4: room for the pair


# ============================================================================================ recovery of earlier runs
def seed_run(conn, run_id: str, *, mode: str = "watch", strategy: str = "pair_h2") -> BotState:
    st = BotState(conn, run_id)
    st.start(strategy, mode, {}, ts=HOUR - 90_000)
    return st


def seed_pair(st: BotState, hour: int, strike: float, kind: str, *, live: bool = False, detected: float | None = None,
              cost: float = 0.96, fees: float = 0.034888, outcome: str | None = "paper_only") -> int:
    return st.opportunity(detected_ts=hour - 800 if detected is None else detected, hour_ticker=m15_ticker(hour),
                          strike_ticker=hourly_ticker(strike, hour), kind=kind, contracts=1, paper_cost=cost,
                          paper_fees=fees, paper_edge=0.005, live=live, outcome=outcome)


def test_paper_twins_an_earlier_run_left_pending_are_settled_at_startup(tmp_path):
    day = HOUR - 86_400
    sim = PairSim(tmp_path, results={m15_ticker(day): "yes", hourly_ticker(85300.0, day): "yes",
                                     m15_ticker(day - 3600): "no", hourly_ticker(85300.0, day - 3600): "yes",
                                     m15_ticker(): "yes", hourly_ticker(85300.0): "yes"})
    old = seed_run(sim.conn, "run-old")
    a = seed_pair(old, day, 85300.0, ph.KIND_BELOW)                                    # pays 1 + 0
    b = seed_pair(old, day - 3600, 85300.0, ph.KIND_BELOW)                             # pays 1 + 1 (between K and T)
    done = seed_pair(old, day - 7200, 85300.0, ph.KIND_BELOW)
    old.update_opportunity(done, paper_realized_pnl=0.5)                               # already settled: left alone
    young = seed_pair(old, HOUR - 3600 * 0, 85300.0, ph.KIND_BELOW, detected=HOUR - 1000 - 600 + 300)   # under an hour old
    weird = seed_pair(old, day - 10_800, 85300.0, "some other kind")
    other = seed_run(sim.conn, "run-late", strategy="late_favorite")
    wrong = seed_pair(other, day - 14_400, 85300.0, ph.KIND_BELOW)                     # another strategy's run
    sim.run()
    cur = sim.conn.execute("SELECT * FROM bot_opportunities")
    names = [d[0] for d in cur.description]
    rows = {r[0]: dict(zip(names, r)) for r in cur.fetchall()}
    assert rows[a]["paper_realized_pnl"] == pytest.approx(1 - 0.96 - 0.034888)
    assert rows[b]["paper_realized_pnl"] == pytest.approx(2 - 0.96 - 0.034888)
    assert rows[done]["paper_realized_pnl"] == 0.5
    assert rows[young]["paper_realized_pnl"] is None and rows[weird]["paper_realized_pnl"] is None
    assert rows[wrong]["paper_realized_pnl"] is None
    assert sim.bot.summary["recovered"] == 2 and sim.bot.summary["settled"] == 1
    assert any("left over from an earlier run" in e["message"] or "from earlier runs" in e["message"] for e in sim.events("settle"))


def test_live_pairs_an_earlier_run_left_pending_get_their_realized_pnl_including_unwinds(tmp_path):
    day = HOUR - 86_400
    sim = live_pair(tmp_path, results={m15_ticker(day): "yes", hourly_ticker(85300.0, day): "yes",
                                       m15_ticker(day - 3600): "yes", hourly_ticker(85300.0, day - 3600): "yes",
                                       m15_ticker(day - 7200): "yes", hourly_ticker(85300.0, day - 7200): "yes",
                                       m15_ticker(): "yes", hourly_ticker(85300.0): "yes"})
    old = seed_run(sim.conn, "run-old", mode="live")
    both = seed_pair(old, day, 85300.0, ph.KIND_BELOW, live=True, outcome="both_filled")
    old.order(client_order_id="o1", ticker=hourly_ticker(85300.0, day), side="yes", action="buy", count=1, limit_price=0.5,
              tif="ioc", status="executed", opportunity_id=both, ts_sent=day - 800, filled=1.0, avg_fill_price=0.5,
              fees=0.0175, dry_run=False)
    old.order(client_order_id="o2", ticker=m15_ticker(day), side="no", action="buy", count=1, limit_price=0.46, tif="ioc",
              status="executed", opportunity_id=both, ts_sent=day - 800, filled=1.0, avg_fill_price=0.46,
              fees=0.017388, dry_run=False)
    unwound = seed_pair(old, day - 3600, 85300.0, ph.KIND_BELOW, live=True, outcome="one_leg_unwound")
    old.order(client_order_id="o3", ticker=hourly_ticker(85300.0, day - 3600), side="yes", action="buy", count=1,
              limit_price=0.5, tif="ioc", status="executed", opportunity_id=unwound, ts_sent=day - 4400, filled=1.0,
              avg_fill_price=0.5, fees=0.0175, dry_run=False)
    old.order(client_order_id="o4", ticker=hourly_ticker(85300.0, day - 3600), side="yes", action="sell", count=1,
              limit_price=0.45, tif="ioc", status="executed", opportunity_id=unwound, ts_sent=day - 4390, filled=1.0,
              avg_fill_price=0.45, fees=0.017325, dry_run=False)                       # the unwind: bought NO at 0.55
    unknown = seed_pair(old, day - 7200, 85300.0, ph.KIND_BELOW, live=True, outcome="unknown")
    sim.run()
    r1, r2, r3 = (old.opportunity_row(i) for i in (both, unwound, unknown))
    assert r1["payout"] == 1.0 and r1["realized_pnl"] == pytest.approx(1 - 0.96 - 0.0175 - 0.017388)
    assert r1["live_cost"] == pytest.approx(0.96) and r1["paper_realized_pnl"] == pytest.approx(1 - 0.96 - 0.034888)
    assert r2["payout"] == 1.0 and r2["realized_pnl"] == pytest.approx(1 - 0.50 - 0.55 - 0.0175 - 0.017325)
    assert r2["live_cost"] == pytest.approx(1.05)
    assert r3["realized_pnl"] is None and r3["paper_realized_pnl"] is not None        # an unknown outcome has no P&L to book


# ============================================================================================ the REAL ExecClient over a fake transport
from .conftest import FakeTransport, TEST_KEY_ID, respond                       # noqa: E402


def book_json(book: tuple[list, list]) -> dict:
    yes, no = book
    return {"orderbook_fp": {"yes_dollars": [[f"{p:.4f}", f"{s:.2f}"] for p, s in yes],
                             "no_dollars": [[f"{p:.4f}", f"{s:.2f}"] for p, s in no]}}


def pair_transport(host: str, clock, *, results: dict, forbid_writes: bool = False) -> FakeTransport:
    t = FakeTransport(host=host, clock=clock, forbid_writes=forbid_writes)
    hourly = [make_hourly(HOUR, k) for k in STRIKES]
    t.get("/markets", {"markets": [make_m15(HOUR)], "cursor": ""},
          params=lambda p: bool(p) and p.get("series_ticker") == "KXBTC15M")
    t.get("/markets", {"markets": hourly, "cursor": ""}, params=lambda p: bool(p) and p.get("series_ticker") == "KXBTCD")
    for k in STRIKES:
        book = HOURLY_CHEAP if k == 85300.0 else NOTHING
        t.get(f"/markets/{hourly_ticker(k)}/orderbook", respond(200, json=book_json(book)))
        t.get(f"/markets/{hourly_ticker(k)}", {"market": {**make_hourly(HOUR, k), "status": "finalized",
                                                          "result": results.get(hourly_ticker(k), "")}})
    t.get(f"/markets/{m15_ticker()}/orderbook", respond(200, json=book_json(M15_CHEAP)))
    t.get(f"/markets/{m15_ticker()}", {"market": {**make_m15(HOUR), "status": "finalized",
                                                  "result": results.get(m15_ticker(), "")}})
    return t


def test_real_client_pair_watch_mode_reads_public_data_only(tmp_path, make_client, watch_env, fake_clock):
    results = {m15_ticker(): "yes", hourly_ticker(85300.0): "yes"}
    t = pair_transport(watch_env.rest_host, fake_clock, results=results, forbid_writes=True)
    client = make_client(t, watch_env)
    fake_clock.now = HOUR - 1000
    state = RecState(sqlite3.connect(":memory:"), "run-pair-real", clock=fake_clock.time)
    cfg = {"trading": {"kill_switch_file": str(tmp_path / "KILL"),
                       "pair": {"window_min": 15, "strikes_each_side": 2, "min_edge_usd": 0.001}}}
    bot = ph.PairBot(ph.PairContext(client=client, state=state), cfg, live=False, clock=fake_clock.time,
                     sleep=fake_clock.sleep, kill_file=tmp_path / "KILL")
    summary = bot.run(hours=1)
    assert summary["end_reason"] == "finished" and summary["opportunities"] == 1 and summary["settled"] == 1
    (opp,) = state.opportunity_rows()
    assert opp["kind"] == ph.KIND_BELOW and opp["strike_ticker"] == hourly_ticker(85300.0)
    assert opp["paper_edge"] == pytest.approx(float(EDGE_BELOW)) and opp["paper_realized_pnl"] == pytest.approx(float(EDGE_BELOW))
    assert t.calls and all(c.method == "GET" and not c.signed and c.host == watch_env.rest_host for c in t.calls)
    t.assert_no_writes()


def test_real_client_pair_live_sends_one_signed_batch_of_two_ioc_buys(tmp_path, make_client, prod_env, fake_clock):
    results = {m15_ticker(): "yes", hourly_ticker(85300.0): "yes"}
    t = pair_transport(prod_env.rest_host, fake_clock, results=results)
    conn = sqlite3.connect(":memory:")
    ledger = Ledger(conn, "run-pair-real", "prod", clock=fake_clock.time, log_path=tmp_path / "exec.jsonl",
                    redact=[TEST_KEY_ID])
    sent: dict[str, dict] = {}

    def batch(call):
        items = []
        for i, body in enumerate(call.body["orders"]):
            oid = f"ord-batch-{len(sent) + 1}"
            sent[oid] = dict(body)
            outcome_price = D(body["price"]) if body["side"] == "bid" else 1 - D(body["price"])
            items.append({"order_id": oid, "client_order_id": body["client_order_id"], "fill_count": "1.00",
                          "remaining_count": "0.00", "average_fill_price": body["price"],
                          "average_fee_paid": f"{micro_fee('1', str(outcome_price)):f}",
                          "ts_ms": int(fake_clock.now * 1000), "error": None})
        return respond(201, json={"orders": items})

    def fills(call):
        oid = (call.params or {}).get("order_id")
        body = sent.get(oid)
        if body is None:
            return respond(200, json={"fills": [], "cursor": ""})
        yes = D(body["price"])
        return respond(200, json={"fills": [{
            "fill_id": f"fill-{oid}", "order_id": oid, "ticker": body["ticker"], "exchange_index": 2,
            "outcome_side": "yes" if body["side"] == "bid" else "no", "book_side": body["side"], "count_fp": "1.00",
            "yes_price_dollars": f"{yes:.4f}", "no_price_dollars": f"{1 - yes:.4f}", "is_taker": True,
            "fee_cost": f"{micro_fee('1', str(yes if body['side'] == 'bid' else 1 - yes)):f}",
            "created_time": iso(fake_clock.now)}], "cursor": ""})

    t.post("/portfolio/events/orders/batched", batch)
    t.get("/portfolio/fills", fills)
    t.get("/portfolio/balance", {"balance": 2943, "balance_dollars": "29.4300"})
    client = make_client(t, prod_env, live=True, audit=ledger.audit_request)
    ledger.live = True
    safety = FakeSafety(tmp_path / "KILL", allow_exit=True)
    safety.clock = fake_clock
    reconcile = FakeReconcile(fake_clock)
    state = RecState(conn, "run-pair-real", clock=fake_clock.time)
    fake_clock.now = HOUR - 1000
    cfg = {"trading": {"enabled": True, "max_loss_usd": 5.0, "max_cost_per_order_usd": 1.0, "max_contracts_per_order": 1,
                       "kill_switch_file": str(tmp_path / "KILL"),
                       "pair": {"window_min": 15, "strikes_each_side": 2, "min_edge_usd": 0.001}}}
    ctx = ph.PairContext(client=client, state=state, ledger=ledger, safety=safety, reconcile=reconcile,
                         env_name="prod", key_fp="abcd1234")
    summary = ph.PairBot(ctx, cfg, live=True, clock=fake_clock.time, sleep=fake_clock.sleep,
                         kill_file=tmp_path / "KILL").run(hours=1)
    assert summary["end_reason"] == "finished" and summary["outcomes"] == {"both_filled": 1}
    (post,) = t.writes
    assert (post.method, post.endpoint) == ("POST", "/portfolio/events/orders/batched") and post.signed
    orders = post.body["orders"]
    assert [(o["ticker"], o["side"], o["price"]) for o in orders] == [
        (hourly_ticker(85300.0), "bid", "0.5000"), (m15_ticker(), "ask", "0.5400")]
    assert all(o["time_in_force"] == "immediate_or_cancel" and o["count"] == "1.00" and o["exchange_index"] == 2 for o in orders)
    assert len({o["client_order_id"] for o in orders}) == 2
    (opp,) = state.opportunity_rows()
    assert opp["outcome"] == "both_filled" and opp["realized_pnl"] == pytest.approx(float(EDGE_BELOW))
    assert ledger.realized_pnl() == EDGE_BELOW and ledger.open_worst_case() == 0
    text = "\n".join(" ".join(str(v) for v in row) for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall() for row in conn.execute(f"SELECT * FROM {name}"))
    for needle in (TEST_KEY_ID, "KALSHI-ACCESS", "PRIVATE KEY", "Authorization"):
        assert needle not in text, needle


# ============================================================================================ the production database and the dashboard
def test_a_watch_run_on_the_production_connection_is_readable_by_the_dashboard_api(tmp_path):
    from kpm.core import db
    from kpm.dashboard import server

    path = tmp_path / "kpm.sqlite"
    conn = db.connect(path)
    sim = PairSim(tmp_path, conn=conn, results={m15_ticker(): "yes", hourly_ticker(85300.0): "yes"})
    sim.run()
    conn.commit()
    ro = server._open_db(path)
    cfg = {"trading": {"max_loss_usd": 5.0, "kill_switch_file": str(tmp_path / "KILL"), "pair": {"window_min": 15}},
           "dashboard": {"refresh_s": 2}}
    now = sim.clock.now
    status = server.api_status(ro, cfg, {}, now)
    assert status["run"]["strategy"] == "pair_h2" and status["run"]["mode"] == "watch"
    assert status["heartbeat"]["detail"]["hour_ticker"] == status["heartbeat"]["detail"]["market_ticker"] == m15_ticker()
    quotes = server.api_quotes(ro, cfg, {"minutes": ["60"]}, HOUR - 800)
    assert {q["strike_ticker"] for q in quotes["rows"]} == {hourly_ticker(k) for k in STRIKES} and quotes["rows"]
    (opp,) = server.api_opportunities(ro, cfg, {}, now)["rows"]
    assert opp["kind"] == ph.KIND_BELOW and opp["outcome"] == "paper_only" and opp["paper_edge"] == pytest.approx(float(EDGE_BELOW))
    assert len(server.api_orders(ro, cfg, {}, now)["rows"]) == 2
    ro.close()
    conn.close()
