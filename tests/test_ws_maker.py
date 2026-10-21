"""ws_maker (research log #26) on synthetic recordings only: no network, no real data."""
import gzip
import json

import pytest

from kpm.analysis import ws_maker as wm
from kpm.analysis import ws_sim as ws
from kpm.analysis import ws_sim_stats as st

G = "26OCT22AAABBB"
WIN, SP5, BK = f"KXNBAGAME-{G}-AAA", f"KXNBASPREAD-{G}-AAA5", f"KXNBAWINMARGIN-{G}-AAA1TO5"
DOC = {"date": "26OCT22", "games": {G: {
    "tickers": [WIN, SP5, BK],
    "meta": {SP5: {"floor_strike": 5.5}, BK: {"yes_sub_title": "AAA by 1 to 5"}},
    "kickoff": "1970-01-01T00:01:40Z"}}}
S = 1_000_000_000
NBA = ws.SPORTS["nba"]
D2 = wm.MVersion("M-d2-2S", 0.02, True)
D2B = wm.MVersion("M-d2-BID", 0.02, False)


class Feed:
    def __init__(self):
        self.seq, self.lines = 0, []

    def _add(self, t, typ, msg):
        self.seq += 1
        self.lines.append({"recv_ns": int(t * S), "raw": json.dumps({"type": typ, "sid": 1, "seq": self.seq, "msg": msg})})

    def book(self, t, ticker, bid, ask, bsz=100, asz=100):
        m = {"market_ticker": ticker}
        if bid is not None:
            m["yes_dollars_fp"] = [[f"{bid:.4f}", f"{bsz:.2f}"]]
        if ask is not None:
            m["no_dollars_fp"] = [[f"{1 - ask:.4f}", f"{asz:.2f}"]]
        self._add(t, "orderbook_snapshot", m)

    def trade(self, t, price, count, taker="no", ticker=BK):
        self._add(t, "trade", {"market_ticker": ticker, "yes_price_dollars": f"{price:.4f}", "count_fp": f"{count:.2f}",
                               "taker_side": taker})

    def tail(self):
        if not getattr(self, "_tailed", False):
            self._tailed = True
            self.book(self.lines[-1]["recv_ns"] / S + 400, WIN, 0.49, 0.51)


def base(thin_bid=0.15, thin_ask=0.25, bsz=100, win=(0.49, 0.51), sp=(0.29, 0.31)):
    """win mid .50, SP5.5 mid .30 -> fair .20. Primary quotes YES .18 / NO .78 (YES ask .22) at delta 2c."""
    f = Feed()
    f.book(0, WIN, *win)
    f.book(0, SP5, *sp)
    f.book(0, BK, thin_bid, thin_ask, bsz=bsz)
    return f


def go(feed, strats=(D2,), closes=None, results=None, tail=True):
    if tail:
        feed.tail()
    sim = wm.MakerSim(ws.Universe([DOC], NBA), list(strats), closes)
    sim.run(feed.lines)
    rows, dropped = wm.settle_rows(sim, results or {BK: "yes"}, NBA)
    return sim, rows


def orders(sim, vid="M-d2-2S"):
    return sim.mstate[(vid, BK)].orders


def by_kind(rows, side=None):
    return [r for r in rows if side is None or r["side"] == side]


# ---------------------------------------------------------------------------------------------- quote prices
@pytest.mark.parametrize("win_mid,yes_c,no_c", [(0.5090, 18, 77), (0.5010, 18, 77)])
def test_quote_prices_floor_and_ceil_to_the_cent(win_mid, yes_c, no_c):
    # fair .2090: bid floor(18.9)=18 (round would say 19); fair .2010: NO bid floor(77.9)=77 -> YES ask 23 = ceil(22.1)
    f = base(win=(win_mid - 0.005, win_mid + 0.005))
    sim, _ = go(f)
    o = orders(sim)
    assert o["yes"].ticks == yes_c * 100 and o["no"].ticks == no_c * 100


def test_price_helpers():
    assert wm.bid_cents(0.2090, 0.02) == 18 and wm.bid_cents(0.20, 0.02) == 18        # float noise does not drop a cent
    assert wm.no_bid_cents(0.2010, 0.02) == 77 and 100 - wm.no_bid_cents(0.2010, 0.02) == 23
    assert 100 - wm.no_bid_cents(0.20, 0.02) == 22


# ---------------------------------------------------------------------------------------------- no crossing
def test_a_quote_that_would_cross_the_opposite_best_is_not_placed():
    sim, _ = go(base(thin_bid=0.10, thin_ask=0.17))                  # our YES bid .18 would be >= the .17 ask
    assert orders(sim)["yes"] is None and orders(sim)["no"] is not None
    sim, _ = go(base(thin_bid=0.23, thin_ask=0.30))                  # our YES ask .22 would be <= the .23 bid
    assert orders(sim)["no"] is None and orders(sim)["yes"] is not None


# ---------------------------------------------------------------------------------------------- latency
def test_order_goes_live_after_one_second():
    early = base()
    early.trade(0.5, 0.18, 10)                                        # before L: not live, no fill
    sim, rows = go(early)
    assert not rows
    late = base()
    late.trade(1.5, 0.18, 10)
    sim, rows = go(late)
    assert [(r["side"], r["count"], r["kind"]) for r in rows] == [("yes", 10, "trade")]


# ---------------------------------------------------------------------------------------------- queue and trade fills
def test_back_of_queue_then_trade_fill_after_the_queue_is_consumed():
    f = base(thin_bid=0.18, bsz=30)                                  # 30 ahead of us at .18
    f.trade(1.5, 0.18, 20)
    sim, rows = go(f)
    assert not rows                                                  # 20 < 30 ahead
    f = base(thin_bid=0.18, bsz=30)
    f.trade(1.5, 0.18, 20)
    f.trade(2.0, 0.18, 15)                                           # 35 traded: 30 consume the queue, 5 fill us
    sim, rows = go(f)
    assert [(r["count"], r["kind"], r["price"]) for r in rows] == [(5, "trade", 0.18)]


def test_trade_through_our_price_on_our_side_fills_and_the_other_side_does_not():
    f = base()
    f.trade(1.5, 0.17, 10, taker="no")                               # a sell below our .18 bid
    assert [(r["side"], r["count"]) for r in go(f)[1]] == [("yes", 10)]
    f = base()
    f.trade(1.5, 0.18, 10, taker="yes")                              # a buy at our bid price: wrong side
    f.trade(1.6, 0.17, 10, taker="yes")
    assert not go(f, strats=(D2B,))[1]
    f = base()
    f.trade(1.5, 0.22, 10, taker="yes")                              # a buy lifting our YES ask (NO bid .78) fills the NO side
    assert [(r["side"], r["price"]) for r in go(f)[1]] == [("no", 0.78)]
    f = base()
    f.trade(1.5, 0.22, 10, taker="no")                               # a sell at .22: wrong side for our ask
    f.trade(1.6, 0.21, 10, taker="yes")                              # a buy below our ask: does not reach it
    assert not go(f)[1]


# ---------------------------------------------------------------------------------------------- cross fills
def test_cross_fill_when_the_opposite_best_moves_through_our_price():
    f = base()
    f.book(5, BK, 0.15, 0.17)                                        # ask goes through our .18 bid
    sim, rows = go(f)
    assert [(r["side"], r["count"], r["price"], r["kind"]) for r in rows] == [("yes", 10, 0.18, "cross")]
    f = base()
    f.book(5, BK, 0.15, 0.19)                                        # not through
    assert not go(f)[1]
    f = base()
    f.book(5, BK, 0.22, 0.30)                                        # bid reaches our YES ask .22: NO side crossed
    assert [(r["side"], r["count"], r["price"], r["kind"]) for r in go(f)[1]] == [("no", 10, 0.78, "cross")]


def test_cross_fill_needs_the_order_to_be_live():
    f = base()
    f.book(0.5, BK, 0.15, 0.17)                                      # moves through before go-live: dropped, not filled
    sim, rows = go(f)
    assert not rows and orders(sim)["yes"].dead and not orders(sim)["yes"].live


# ---------------------------------------------------------------------------------------------- inventory
def test_inventory_cap_cuts_quote_size_and_fills():
    f = base()
    f.trade(1.5, 0.18, 4)                                            # 4 filled: net +4
    f.book(5, SP5, 0.30, 0.32)                                       # fair moves 1c -> requote
    sim, rows = go(f)
    assert sum(r["count"] for r in rows) == 4
    assert orders(sim)["yes"].size == 6 and orders(sim)["no"].size == 10     # room +6 on the bid side; the NO side may take 10
    f = base()
    f.trade(1.5, 0.18, 100)
    sim, rows = go(f)
    assert sum(r["count"] for r in rows) == 10                       # never more than +10 net
    assert wm.inventory_stats(sim, "M-d2-2S")["max_abs_net"] == 10


# ---------------------------------------------------------------------------------------------- cancels
def test_cancel_after_l_when_fair_becomes_invalid():
    def feed(t):
        f = base()
        f.book(5, SP5, 0.20, 0.40)                                   # a 20c-wide input: fair invalid
        f.trade(t, 0.18, 10)
        return f
    assert not go(feed(6.5))[1]                                      # cancel effective at 6 s
    assert [r["count"] for r in go(feed(5.5))[1]] == [10]            # still resting until the cancel takes effect


def test_cancel_in_the_last_60s_before_close_and_no_new_quotes():
    close = {BK: 200 * S}                                            # cut at 140 s, effective 141 s
    f = base()
    f.trade(140.5, 0.18, 10)
    assert [r["count"] for r in go(f, closes=close)[1]] == [10]
    f = base()
    f.trade(150, 0.18, 10)
    assert not go(f, closes=close)[1]
    f = base()
    f.book(145, SP5, 0.30, 0.32)                                     # fair moves inside the cut window: nothing is placed
    sim, _ = go(f, closes=close)
    assert sim.placed["M-d2-2S"] == 2                                # only the first YES+NO pair
    f = base()
    f.book(145, SP5, 0.30, 0.32)
    sim, _ = go(f)                                                   # without a close time the requote happens
    assert sim.placed["M-d2-2S"] == 4


def test_close_times_prefer_meta_then_expected_end_then_kickoff_plus_3h():
    doc = {"games": {G: {"tickers": [WIN, BK], "meta": {BK: {"close_time": "1970-01-01T00:10:00Z"}}, "kickoff": None,
                         "kickoff_raw": {"expected_expiration_time": "1970-01-01T01:00:00Z"}}}}
    c = wm.close_times([doc])
    assert c[BK] == 600 * S and c[WIN] == 3600 * S
    c = wm.close_times([{"games": {G: {"tickers": [BK], "kickoff": "1970-01-01T00:00:00Z"}}}])
    assert c[BK] == 3 * 3600 * S
    assert wm.close_times([{"games": {G: {"tickers": [BK]}}}]) == {}


# ---------------------------------------------------------------------------------------------- settlement
@pytest.mark.parametrize("result", ["yes", "no"])
def test_a_yes_fill_plus_a_no_fill_pays_one_dollar_per_pair(result):
    f = base()
    f.trade(1.5, 0.18, 10, taker="no")
    f.trade(3.0, 0.22, 10, taker="yes")
    sim, rows = go(f, results={BK: result})
    assert sorted(r["side"] for r in rows) == ["no", "yes"]
    assert sum(r["net"] for r in rows) == pytest.approx((1 - 0.18 - 0.78) * 10)
    assert wm.completed_pairs(rows) == 10
    v = wm.version_row("M-d2-2S", rows, sim, {G}, set(), 1, NBA)
    assert v["pairs_completed"] == 10 and v["by_bucket"]["1TO5"]["contracts"] == 20


def test_unsettled_market_is_dropped_whole():
    f = base()
    f.trade(1.5, 0.18, 10)
    sim, _ = go(f)
    rows, dropped = wm.settle_rows(sim, {}, NBA)
    assert not rows and dropped["M-d2-2S"] == 10


# ---------------------------------------------------------------------------------------------- naive
def test_naive_strategy_quotes_around_the_thin_mid():
    sim, _ = go(base(thin_bid=0.10, thin_ask=0.20), strats=wm.strategies([D2]))
    assert orders(sim)["yes"].ticks == 1800                           # deep fair .20
    n = orders(sim, "M-d2-2S-N")                                      # thin mid .15 -> 13c bid, NO 83c
    assert n["yes"].ticks == 1300 and n["no"].ticks == 8300
    f = base(thin_bid=0.10, thin_ask=None)                            # no ask: the naive version skips
    sim, _ = go(f, strats=wm.strategies([D2]))
    assert orders(sim, "M-d2-2S-N") == {"yes": None, "no": None} and orders(sim)["yes"] is not None


# ---------------------------------------------------------------------------------------------- verdict
def vrows(games, count=10, net=1.0, vid="M-d2-2S", mo=0.0):
    return [{"version": vid, "game": g, "count": count, "net": net, "gross": net, "mo_fair_60": mo} for g in games]


def games(n):
    return [f"g{i:02d}" for i in range(n)]


def call(prim, naive, g, **kw):
    d, u = set(g[:len(g) // 2]), set(g[len(g) // 2:])
    return wm.verdict(prim, naive, d, u, draws=300, games_per_week=50, min_games=20, min_unseen=10, **kw), d, u


def test_verdict_passes_and_each_rule_can_fail():
    g = games(30)
    v, _, _ = call(vrows(g), vrows(g, net=0.5, vid="n"), g)
    assert v["verdict"] == "PASS" and all(v["rules"].values()) and v["primary_minus_naive_ci95"] is not None
    assert v["projected_per_week"] == pytest.approx(50.0)
    bad = {
        "1_positive_discovery_and_unseen": vrows(g[:15], net=-1.0) + vrows(g[15:]),
        "2_unseen_bootstrap_lower_gt_0": vrows(g[:15]) + [r | {"net": (3.0 if i % 2 else -2.5)} for i, r in enumerate(vrows(g[15:]))],
        "3_unseen_filled_contracts_ge_100": vrows(g[:15]) + vrows(g[15:], count=6),
        "5_weekly_ge_2": vrows(g[:15]) + vrows(g[15:], net=0.001),
        "6_fair_markout_60s_ge_minus_2c": vrows(g, mo=-0.03),
    }
    for rule, prim in bad.items():
        out, _, _ = call(prim, vrows(g, net=-5.0, vid="n"), g)
        assert out["verdict"] == "FAIL" and out["rules"][rule] is False, rule
    out, _, _ = call(vrows(g), vrows(g, net=2.0, vid="n"), g)              # naive does better: information check fails
    assert out["rules"]["4_primary_minus_naive_gt_0"] is False and out["verdict"] == "FAIL"
    out, _, _ = call(vrows(g), [], g)                                      # naive with no fills counts as 0
    assert out["rules"]["4_primary_minus_naive_gt_0"] is True
    out, _, _ = call(vrows(g[:15]) + vrows(g[15:], mo=-0.05)[:], vrows(g, net=0.5, vid="n"), g)
    assert out["rules"]["6_fair_markout_60s_ge_minus_2c"] is False         # unseen alone is too negative


def test_unseen_games_without_fills_count_as_zero():
    g = games(30)
    prim = vrows(g[:15]) + vrows(g[15:20])                                 # only 5 of 15 unseen games have fills
    v, _, _ = call(prim, [], g)
    assert v["rules"]["2_unseen_bootstrap_lower_gt_0"] is True or v["bootstrap_lower_95"] == 0.1
    # per contract the empty games add nothing, but a draw made only of empty games gives 0, so the bound cannot exceed the mean
    assert v["bootstrap_lower_95"] <= v["unseen"]["net_per_contract"] + 1e-12


def test_verdict_gating_and_invalid_propagation():
    g = games(19)
    v, d, u = call(vrows(g), vrows(g, vid="n"), g)
    assert v["verdict"] == "NOT ENOUGH GAMES"
    g = games(30)
    d, u = set(g[:25]), set(g[25:28])                                      # 28 games but only 3 unseen (< 10)
    assert wm.verdict(vrows(g), [], d, u, draws=50, games_per_week=50, min_games=20, min_unseen=10)["verdict"] == "NOT ENOUGH GAMES"
    g6 = games(6)                                                          # the NFL gate is 6 games / 3 unseen
    out = wm.verdict(vrows(g6), [], set(g6[:3]), set(g6[3:]), draws=50, games_per_week=15, min_games=6, min_unseen=3)
    assert out["verdict"] != "NOT ENOUGH GAMES"
    v, _, _ = call(vrows(g), [], g, lines_out_of_order=3)
    assert v["verdict"] == "INVALID: lines out of order"
    v, _, _ = call(vrows(g), [], g, tickers_in_two_streams=1)
    assert v["verdict"] == "INVALID: a ticker appears in two raw dirs"
    v, _, _ = call(vrows(games(5)), [], games(5), lines_out_of_order=1)   # INVALID wins over NOT ENOUGH GAMES
    assert v["verdict"].startswith("INVALID")


def test_bootstrap_diff_is_paired_and_deterministic():
    g = games(10)
    a = {x: (1.0, 10.0) for x in g}
    b = {x: (0.5, 10.0) for x in g}
    lo, hi = wm.bootstrap_diff(a, b, g, 200, 7)
    assert lo == pytest.approx(0.05) and hi == pytest.approx(0.05)
    assert wm.bootstrap_diff(a, b, g, 200, 7) == (lo, hi)


# ---------------------------------------------------------------------------------------------- versions
@pytest.mark.parametrize("sport", ["nba", "nfl"])
def test_six_versions_per_sport_and_the_primary_id(sport):
    vs = wm.versions_for(ws.SPORTS[sport])
    ids = [v.vid for v in vs]
    assert len(ids) == 6 == len(set(ids)) and wm.PRIMARY == "M-d2-2S" and wm.PRIMARY in ids
    assert set(ids) == {f"M-d{d}-{k}" for d in (1, 2, 3) for k in ("2S", "BID")}
    assert len(wm.strategies(vs)) == 12 and sum(s.naive for s in wm.strategies(vs)) == 6
    assert {v.two_sided for v in vs if v.vid.endswith("BID")} == {False}


# ---------------------------------------------------------------------------------------------- end to end
def test_run_writes_outputs(tmp_path):
    f = base()
    f.trade(1.5, 0.18, 10)
    f.tail()
    raw = tmp_path / "raw"
    raw.mkdir()
    with gzip.open(raw / "msgs-1970010100-1.jsonl.gz", "wt") as fh:
        fh.writelines(json.dumps(x) + "\n" for x in f.lines)
    mk = tmp_path / "markets.json"
    mk.write_text(json.dumps(DOC))
    s = wm.run([str(raw)], [str(mk)], tmp_path / "out", results={BK: "yes"}, fetch=False, sport="nba")
    assert s["verdict"]["verdict"] == "NOT ENOUGH GAMES" and s["lines_out_of_order"] == 0
    assert {v["version"] for v in s["versions"]} == {"M-d2-2S", "M-d2-2S-N"}
    assert s["versions"][0]["contracts"] == 10 and s["versions"][0]["trade_fill_share"] == 1.0
    assert (tmp_path / "out" / "summary.json").exists() and (tmp_path / "out" / "trades.csv").exists()
    s = wm.run([str(raw)], [str(mk)], tmp_path / "out2", results={BK: "yes"}, fetch=False, sport="nba", versions="all")
    assert len(s["versions"]) == 12


def test_cli_registers_ws_maker():
    from kpm.cli import build_parser
    a = build_parser().parse_args(["ws-maker", "--sport", "nba", "--raw", "x", "--markets", "y", "--no-fetch"])
    assert a.cmd == "ws-maker" and a.no_fetch and a.versions == "primary"
