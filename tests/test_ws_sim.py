"""ws_sim on synthetic recordings only (no network, no real data)."""
import gzip
import json

import pytest

from kpm.analysis import ws_sim as ws
from kpm.analysis import ws_sim_stats as st

G = "26OCT22AAABBB"
SP7, SP15 = f"KXNFLSPREAD-{G}-AAA7", f"KXNFLSPREAD-{G}-AAA15"
BK = f"KXNFLWINMARGIN-{G}-AAA7TO14"          # fair = P(by > 6.5) - P(by > 14.5) = mid(SP7) - mid(SP15)
BK1 = f"KXNFLWINMARGIN-{G}-AAA1TO6"          # fair = (win - tie/2) - P(by > 6.5)
WIN, TIE = f"KXNFLGAME-{G}-AAA", f"KXNFLTIE-{G}-Y"
PROT, TWIN = f"KXNFLRECYDSPROT-{G}-P1-50", f"KXNFLRECYDS-{G}-P1-50"
DOC = {"date": "26OCT22", "games": {G: {
    "tickers": [SP7, SP15, BK, BK1, WIN, TIE, PROT, TWIN],
    "meta": {SP7: {"floor_strike": 6.5}, SP15: {"floor_strike": 14.5}, BK: {"yes_sub_title": "AAA by 7 to 14"},
             BK1: {"yes_sub_title": "AAA by 1 to 6"}},
    "kickoff": "1970-01-01T00:01:40Z"}}}
S = 1_000_000_000


class Feed:
    """Builds recorder lines on one sid with consecutive seq numbers."""

    def __init__(self):
        self.seq, self.lines = 0, []

    def _add(self, t, typ, msg, skip=0):
        self.seq += 1 + skip
        self.lines.append({"recv_ns": int(t * S), "raw": json.dumps({"type": typ, "sid": 1, "seq": self.seq, "msg": msg})})

    def book(self, t, ticker, bid, ask, bsz=100, asz=100):
        m = {"market_ticker": ticker}
        if bid is not None:
            m["yes_dollars_fp"] = [[f"{bid:.4f}", f"{bsz:.2f}"]]
        if ask is not None:
            m["no_dollars_fp"] = [[f"{1 - ask:.4f}", f"{asz:.2f}"]]
        self._add(t, "orderbook_snapshot", m)

    def delta(self, t, ticker, price, d, side, skip=0):
        self._add(t, "orderbook_delta", {"market_ticker": ticker, "price_dollars": f"{price:.4f}",
                                         "delta_fp": f"{d:.2f}", "side": side}, skip)

    def ctl(self, t, event):
        self.lines.append({"recv_ns": int(t * S), "ctl": {"event": event}})

    def tail(self):
        """A quiet last line 400 s after the end: the recording keeps going, so pending arrivals and exits are not cut off."""
        if not getattr(self, "_tailed", False):
            self._tailed = True
            self.book(self.lines[-1]["recv_ns"] / S + 400, SP15, 0.09, 0.11)

    def trade(self, t, ticker, price, count, taker="no"):
        self._add(t, "trade", {"market_ticker": ticker, "yes_price_dollars": f"{price:.4f}", "count_fp": f"{count:.2f}",
                               "taker_side": taker})


def base_feed(thin_bid=0.18, thin_ask=0.20, asz=100):
    """t=0: SP7 mid .30, SP15 mid .10 -> fair .20; the thin bucket is quoted .18/.20."""
    f = Feed()
    f.book(0, SP7, 0.29, 0.31)
    f.book(0, SP15, 0.09, 0.11)
    f.book(0, BK, thin_bid, thin_ask, asz=asz)
    return f


def run_sim(feed, versions, results=None, markouts=True, doc=DOC, tail=True):
    if tail:
        feed.tail()
    sim = ws.Sim(ws.Universe([doc]), versions, markouts=markouts)
    sim.run(feed.lines)
    sim.settle(results or {})
    return sim


T1 = ws.Version("t", "A", "T", theta=0.01, latency=1.0, exit="X1")
T2 = ws.Version("t2", "A", "T", theta=0.01, latency=1.0, exit="X2")
TB = ws.Version("b", "B", "T", theta=0.01, latency=1.0, exit="X1")
MK = ws.Version("m", "A", "M", latency=0.25, exit="X2", delta=0.01)


def stale_ask(asz=100, extra=None):
    """Thin bucket .18/.20 (fair .20); at t=10 the deep SP7 jumps (fair -> .30), so the .20 ask is stale."""
    f = base_feed(asz=asz)
    f.book(10, SP7, 0.39, 0.41)
    if extra:
        extra(f)
    return f


# ---------------------------------------------------------------------------------------------- fees and mids
@pytest.mark.parametrize("p,c,fee", [(0.5, 1, 0.0175), (0.2, 5, 0.056), (0.5, 10, 0.175), (0.01, 1, 0.0007),
                                     (0.99, 3, 0.0021), (0.33, 7, 0.1084), (0.37, 1, 0.0164)])
def test_fee_formula_and_rounding(p, c, fee):
    assert ws.taker_fee(p, c) == pytest.approx(fee, abs=1e-9)         # 0.5*10 is exactly 0.175: no round-up from float noise


@pytest.mark.parametrize("bid,ask,mid", [(0.50, 0.53, 0.515), (0.50, 0.54, None), (None, 0.03, 0.015), (None, 0.05, None),
                                         (0.50, None, None), (0.52, 0.50, None)])
def test_valid_mid_rules(bid, ask, mid):
    got = ws.valid_mid(bid, ask)
    assert got is None if mid is None else got == pytest.approx(mid)


# ---------------------------------------------------------------------------------------------- taker signals and fills
def test_stale_thin_ask_gives_a_signal_and_fills_if_the_quote_survives():
    sim = run_sim(stale_ask(asz=3), [T1], {BK: "yes"})
    assert sim.orders["t"] == 1 and sim.fills["t"] == 1
    (r,) = sim.trade_rows()
    assert (r["side"], r["count"], r["entry_px"]) == ("yes", 3, 0.20)    # min(10, 3 offered, floor($1/.20)=5)
    assert r["dec_ns"] == 10 * S and r["fill_ns"] == 11 * S
    assert r["fee_in"] == pytest.approx(0.0336)
    assert r["net"] == pytest.approx(0.8 * 3 - 0.0336)


def test_size_is_capped_by_one_dollar():
    (r,) = run_sim(stale_ask(), [T1], {BK: "yes"}).trade_rows()
    assert r["count"] == 5                                             # floor(1 / .20)


def test_quote_pulled_before_arrival_is_a_phantom():
    sim = run_sim(stale_ask(asz=3, extra=lambda f: f.delta(10.5, BK, 0.80, -3, "no")), [T1])
    assert sim.orders["t"] == 1 and sim.fills["t"] == 0 and not sim.trade_rows()


def test_message_exactly_at_arrival_counts_but_one_after_does_not():
    at = run_sim(stale_ask(asz=3, extra=lambda f: f.delta(11.0, BK, 0.80, -3, "no")), [T1])
    after = run_sim(stale_ask(asz=3, extra=lambda f: f.delta(11.5, BK, 0.80, -3, "no")), [T1], {BK: "yes"})
    assert at.fills["t"] == 0 and after.fills["t"] == 1


def test_no_side_signal():
    f = Feed()
    f.book(0, SP7, 0.19, 0.21)
    f.book(0, SP15, 0.09, 0.11)                                          # fair .10
    f.book(0, BK, 0.50, 0.52)
    sim = run_sim(f, [T1], {BK: "no"})
    (r,) = sim.trade_rows()
    assert (r["side"], r["count"], r["entry_px"]) == ("no", 2, 0.50)    # NO costs 1 - bid; floor(1/.5) = 2
    assert r["net"] == pytest.approx(1.0 - ws.taker_fee(0.5, 2))
    assert sim.orders["t"] == 1                                           # the YES side has no edge


def test_one_position_per_market_and_side():
    f = stale_ask()
    f.book(12, SP7, 0.38, 0.42)                                          # another top change while long: no second entry
    assert run_sim(f, [T1]).orders["t"] == 1


def test_fill_check_trade_in_window_or_big_quote():
    small = run_sim(stale_ask(asz=3), [T1], {BK: "yes"}).trade_rows()[0]
    traded = run_sim(stale_ask(asz=3, extra=lambda f: f.trade(10.5, BK, 0.20, 1, "yes")), [T1], {BK: "yes"}).trade_rows()[0]
    big = run_sim(stale_ask(asz=8), [T1], {BK: "yes"}).trade_rows()[0]
    assert (small["fill_check"], traded["fill_check"], big["fill_check"]) == (False, True, True)


# ---------------------------------------------------------------------------------------------- exits
def exit_feed(pull_bid=False):
    def extra(f):
        f.book(20, BK, 0.31, 0.33)                                       # bid - fee(bid) = .295 >= fair(.30) - .01
        if pull_bid:
            f.delta(20.5, BK, 0.31, -100, "yes")
    return stale_ask(extra=extra)


def test_x2_exit_has_the_arrival_delay():
    sim = run_sim(exit_feed(), [T2], {BK: "yes"})
    (r,) = sim.trade_rows()
    assert (r["exit_kind"], r["exit_ns"], r["exit_px"]) == ("x2", 21 * S, 0.31)
    assert r["fee_out"] == pytest.approx(ws.taker_fee(0.31, 5))
    assert r["net"] == pytest.approx(0.11 * 5 - ws.taker_fee(0.2, 5) - ws.taker_fee(0.31, 5))


def test_x2_exit_fails_if_the_bid_is_gone_at_arrival_then_holds_to_settlement():
    (r,) = run_sim(exit_feed(pull_bid=True), [T2], {BK: "no"}).trade_rows()
    assert r["exit_kind"] == "settle" and r["exit_px"] == 0.0


def test_x1_never_exits_early():
    (r,) = run_sim(exit_feed(), [T1], {BK: "yes"}).trade_rows()
    assert r["exit_kind"] == "settle"


def test_unsettled_markets_are_dropped_and_reported():
    sim = run_sim(stale_ask(), [T1], {})
    assert sim.trade_rows() == [] and sim.all_pos[0].exit_kind == "unsettled"


def test_an_unsettled_market_is_dropped_whole_including_x2_exits():
    sim = run_sim(exit_feed(), [T1, T2], {})                             # X1 held, X2 exited at t=21: both dropped
    assert sim.trade_rows() == [] and [p.exit_kind for p in sim.all_pos] == ["unsettled", "unsettled"]


def test_markouts_phase_and_kickoff():
    sim = run_sim(stale_ask(), [T1], {BK: "yes"})
    (r,) = sim.trade_rows({G: 100 * S})
    assert r["mo_thin_10"] == pytest.approx(-0.01) and r["mo_fair_10"] == pytest.approx(0.10)
    assert r["phase"] == "pregame"
    assert sim.trade_rows({G: 5 * S})[0]["phase"] == "ingame" and sim.trade_rows()[0]["phase"] == "unknown"


# ---------------------------------------------------------------------------------------------- family B
def twin_fair(updates, t):
    f = Feed()
    for ts, bid, ask in updates:
        if ts <= t:
            f.book(ts, TWIN, bid, ask)
    sim = ws.Sim(ws.Universe([DOC]), [])
    sim.run(f.lines)
    return sim.fair(sim.u.targets[PROT], int(t * S))


UPD = [(0, .49, .51), (5, .59, .61), (20, .51, .53), (35, .53, .55)]


def test_family_b_fair_is_the_trailing_30s_median_with_carry_in():
    assert twin_fair(UPD, 10) is None                                     # only 10 s of twin history
    assert twin_fair(UPD, 40) == pytest.approx(0.54)                      # median(.60 carried in, .52, .54)


def test_family_b_wide_twin_spread_voids_the_window_until_it_ages_out():
    upd = UPD + [(36, .40, .60), (50, .50, .52)]
    assert twin_fair(upd, 40) is None
    assert twin_fair(upd, 70) is None                                     # carry-in at t=40 is the wide update
    assert twin_fair(upd, 90) == pytest.approx(0.51)


def test_family_b_signal_is_yes_only():
    f = Feed()
    f.book(0, TWIN, 0.49, 0.51)
    f.book(40, PROT, 0.38, 0.40)
    sim = run_sim(f, [TB], {PROT: "yes"})
    (r,) = sim.trade_rows()
    assert r["side"] == "yes" and r["family"] == "B" and sim.orders["b"] == 1


# ---------------------------------------------------------------------------------------------- fair values and universe
def test_family_a_bucket_with_tie_adjusted_win_leg():
    f = Feed()
    f.book(0, WIN, 0.59, 0.61)
    f.book(0, TIE, 0.03, 0.05)
    f.book(0, SP7, 0.29, 0.31)
    sim = run_sim(f, [])
    assert sim.fair(sim.u.targets[BK1], 1) == pytest.approx(0.60 - 0.02 - 0.30)


def test_buckets_without_strike_metadata_are_skipped():
    doc = {"date": "x", "games": {G: {"tickers": list(DOC["games"][G]["tickers"])}}}
    u = ws.Universe([doc])
    assert BK not in u.targets and u.skipped["spread_without_strike"] == 2 and u.skipped["A_missing_input"] == 2


def test_plus_bucket_has_no_upper_leg():
    t = f"KXNFLWINMARGIN-{G}-AAA15PLUS"
    doc = {"date": "x", "games": {G: {"tickers": [SP15, t], "meta": {SP15: {"floor_strike": 14.5},
                                                                    t: {"yes_sub_title": "AAA by 15 or more"}}}}}
    assert ws.Universe([doc]).targets[t].legs == (("sp", SP15), None)


# ---------------------------------------------------------------------------------------------- exclusions
def gap_feed(resnap):
    f = base_feed()
    f.delta(3, SP7, 0.31, 5, "no", skip=1)                               # seq gap: every book on the sid is untrusted
    for t, sp7 in resnap:
        f.book(t, SP7, *sp7)
        f.book(t, SP15, 0.09, 0.11)
        f.book(t, BK, 0.18, 0.20)
    return f


def test_exclusion_after_seq_gap_until_the_next_snapshot_and_the_next_minute():
    assert run_sim(gap_feed([]), [T1]).orders["t"] == 0                  # books dead after the gap
    two = [(5, (0.39, 0.41))]
    assert run_sim(gap_feed(two), [T1]).orders["t"] == 0                 # snapshots restored, but inputs bad this minute
    sim = run_sim(gap_feed(two + [(65, (0.38, 0.41))]), [T1])           # next minute: the move is tradable again
    assert sim.orders["t"] == 1 and sim.heap == [] and sim.all_pos[0].dec_ns == 65 * S


def test_an_invalid_input_book_excludes_the_rest_of_its_minute():
    def feed(valid_again_at):
        f = base_feed()
        f.book(5, SP7, 0.30, 0.40)                                       # SP7 spread 10c: invalid input
        f.book(valid_again_at, SP7, 0.39, 0.41)                          # valid again, fair .30 vs the stale .20 ask
        return f
    assert run_sim(feed(10), [T1]).orders["t"] == 0                      # same minute as the invalid book
    sim = run_sim(feed(61), [T1])
    assert sim.orders["t"] == 1 and sim.all_pos[0].dec_ns == 61 * S


# ---------------------------------------------------------------------------------------------- maker
def maker_feed(extra):
    f = base_feed(0.18, 0.24)                                            # fair .20 -> quote .19, inside .18/.24
    extra(f)
    return f


def queue_extra(f):
    f.delta(0.1, BK, 0.19, 6, "yes")                                     # 6 ahead of us by the time we are live (t=.25)
    f.trade(0.2, BK, 0.19, 100)                                          # before we are live: ignored
    f.trade(1, BK, 0.19, 4)
    f.trade(1.5, BK, 0.19, 50, taker="yes")                              # a buyer, not a seller: ignored
    f.trade(1.7, BK, 0.20, 50)                                           # above our price: ignored


def test_maker_back_of_queue_fill_needs_trades_beyond_the_queue_ahead():
    assert run_sim(maker_feed(queue_extra), [MK]).fills["m"] == 0
    sim = run_sim(maker_feed(lambda f: (queue_extra(f), f.trade(2, BK, 0.19, 4))), [MK], {BK: "yes"})
    (r,) = sim.trade_rows()
    assert (r["kind"], r["count"], r["entry_px"], r["fee_in"]) == ("M", 2, 0.19, 0.0)    # 8 traded - 6 ahead
    assert r["net"] == pytest.approx(0.81 * 2)


def test_maker_cancel_takes_effect_after_the_delay():
    def moved(trade_at):
        def extra(f):
            f.book(13, SP7, 0.34, 0.36)                                  # fair .25: cancel (effective 13.25); new quote .24 not inside
            f.trade(trade_at, BK, 0.19, 3)
        return maker_feed(extra)
    assert run_sim(moved(13.1), [MK]).fills["m"] == 1                    # still resting
    assert run_sim(moved(13.3), [MK]).fills["m"] == 0                    # gone


# ---------------------------------------------------------------------------------------------- statistics
def test_split_and_not_enough_games():
    d, u = st.split_games({f"g{i}": i for i in range(7)})
    assert (d, u) == (["g0", "g1", "g2"], ["g3", "g4", "g5", "g6"])
    assert st.verdict([], set("abc"), set("de"), None)["verdict"] == "NOT ENOUGH GAMES"   # 5 games
    d, u = st.split_games({f"g{i}": i for i in range(6)})
    assert len(d) == len(u) == 3
    assert st.verdict([], set(), set("abc"), None)["verdict"] == "NOT ENOUGH GAMES"        # 3 unseen but only 3 games


def rows_for(games, per_game=40, net=0.5):
    return [{"game": g, "count": 1, "net": net, "gross": net, "fill_check": True} for g in games for _ in range(per_game)]


def test_verdict_pass_and_each_rule_can_fail():
    disc, uns = {"d1", "d2", "d3"}, {"u1", "u2", "u3"}
    rows = rows_for(disc | uns)
    v = st.verdict(rows, disc, uns, 0.0, draws=200)
    assert v["verdict"] == "PASS" and all(v["rules"].values()) and v["projected_per_week"] == pytest.approx(20 * 15)
    assert st.verdict(rows, disc, uns, 0.2, draws=200)["rules"]["4_coin_flip_le_5pct"] is False
    assert st.verdict(rows_for(disc | uns, per_game=10), disc, uns, 0.0, draws=200)["rules"]["3_unseen_trades_ge_100"] is False
    assert st.verdict(rows_for(disc, 40) + rows_for(uns, 40, net=-0.1), disc, uns, 0.0, draws=200)["verdict"] == "FAIL"


def test_bootstrap_is_deterministic_with_the_seed():
    stats = {f"g{i}": ((i * 7) % 5 - 1.3, 3.0 + i % 4) for i in range(12)}
    assert st.bootstrap_lower(stats, 500, 7) == st.bootstrap_lower(stats, 500, 7)
    assert st.bootstrap_lower(stats, 500, 7) != st.bootstrap_lower(stats, 500, 8)


def test_coin_plan_is_deterministic_and_inside_the_windows():
    slots, win = [(BK, "yes"), (SP7, "no")], {BK: (10 * S, 20 * S), SP7: (0, 5 * S)}
    a, b = st.plan_coin(slots, win, 20, 5), st.plan_coin(slots, win, 20, 5)
    assert a == b and a != st.plan_coin(slots, win, 20, 6) and len(a) == 40
    assert all(10 * S <= ns <= 20 * S for c, t, s, ns in a if t == BK)
    assert st.coin_share(0.1, [0.2, 0.1, 0.0, None]) == 0.5


def write_raw(tmp_path, feed, name="msgs-1970010100-1.jsonl.gz"):
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    feed.tail()
    with gzip.open(raw / name, "wt") as fh:
        fh.writelines(json.dumps(x) + "\n" for x in feed.lines)
    return raw


def test_coin_flip_second_pass_runs_and_is_deterministic(tmp_path):
    f = stale_ask()
    f.book(30, SP7, 0.39, 0.41)
    raw = write_raw(tmp_path, f)
    args = ([str(raw)], ws.Universe([DOC]), [(BK, "yes")], {BK: (0, 20 * S)}, {BK: "yes"})
    a = ws.coin_flip(*args, copies=15, seed=3, latency=1.0)
    assert a == ws.coin_flip(*args, copies=15, seed=3, latency=1.0) and len(a) == 15 and any(x is not None for x in a)


# ---------------------------------------------------------------------------------------------- end to end
def test_run_writes_outputs_and_reports_not_enough_games(tmp_path):
    f = stale_ask()
    raw = write_raw(tmp_path, f)
    mk = tmp_path / "markets.json"
    mk.write_text(json.dumps(DOC))
    out = tmp_path / "out"
    s = ws.run([str(raw)], [str(mk)], out, results={BK: "yes"}, fetch=False)
    assert s["verdict"]["verdict"] == "NOT ENOUGH GAMES" and s["lines"] == len(f.lines) and s["lines_out_of_order"] == 0
    assert {p.name for p in out.iterdir()} == {"summary.json", "trades.csv", "versions.csv"}
    assert len((out / "trades.csv").read_text().splitlines()) == 2
    assert json.loads((out / "summary.json").read_text())["games"][0]["segment"] in ("discovery", "unseen")


def test_lines_received_out_of_order_are_counted():
    sim = ws.Sim(ws.Universe([DOC]), [])
    sim.run(stale_ask().lines + base_feed().lines)                       # e.g. two overlapping recorder processes
    assert sim.out_of_order == 3


def test_the_unsuffixed_smoke_test_file_is_excluded(tmp_path):
    smoke = Feed()
    smoke.book(0, SP7, 0.29, 0.31)
    smoke.book(0, SP15, 0.09, 0.11)
    smoke.book(0, BK, 0.18, 0.20)
    smoke.book(5, SP7, 0.39, 0.41)                                       # a signal, but only inside the smoke test
    write_raw(tmp_path, smoke, "msgs-1970010100.jsonl.gz")
    raw = write_raw(tmp_path, base_feed(), "msgs-1970010100-1.jsonl.gz")
    mk = tmp_path / "markets.json"
    mk.write_text(json.dumps(DOC))
    s = ws.run([str(raw)], [str(mk)], tmp_path / "out", results={BK: "yes"}, fetch=False)
    assert s["lines"] == 4 and s["versions"][0]["orders"] == 0
    (skipped,) = s["raw_files_skipped_smoke"]
    assert skipped.endswith("msgs-1970010100.jsonl.gz")


def test_cli_subcommand_is_wired():
    from kpm.cli import build_parser
    a = build_parser().parse_args(["ws-sim", "--raw", "a", "b", "--markets", "m", "--versions", "all"])
    assert a.cmd == "ws-sim" and a.raw == ["a", "b"] and a.versions == "all"


def test_version_list_has_34_and_the_primary_is_unchanged():
    vs = ws.all_versions()
    assert len(vs) == 34 and len({v.vid for v in vs}) == 34
    assert sum(v.family == "A" and v.kind == "T" and v.theta == 0.03 for v in vs) == 6
    (p,) = [v for v in vs if v.vid == ws.PRIMARY]
    assert (p.family, p.kind, p.theta, p.latency, p.exit) == ("A", "T", 0.01, 1.0, "X2")


# ---------------------------------------------------------------------------------------------- 08:00Z amendment: outages
def cut_at(*extra_lines):
    """stale_ask (signal at t=10, arrival 11) with control/data lines appended after t=10."""
    f = stale_ask(asz=3)
    for fn in extra_lines:
        fn(f)
    return f


@pytest.mark.parametrize("event", ["disconnect", "stop"])
def test_disconnect_and_stop_kill_the_books_so_a_pending_arrival_does_not_fill(event):
    sim = run_sim(cut_at(lambda f: f.ctl(10.5, event)), [T1], {BK: "yes"})
    assert sim.orders["t"] == 1 and sim.fills["t"] == 0                    # arrival at 11 falls inside the outage


def test_a_gap_before_a_connect_is_an_outage_even_without_a_disconnect_line():
    def resnap(f):
        f.ctl(500, "connect")                                              # a new recorder process; the old one just died
        f.book(501, SP7, 0.39, 0.41)
    sim = run_sim(cut_at(resnap), [T1], {BK: "yes"})
    assert sim.orders["t"] == 1 and sim.fills["t"] == 0


def test_arrival_at_exactly_the_disconnect_time_finds_no_book_but_one_before_it_fills():
    assert run_sim(cut_at(lambda f: f.ctl(11, "disconnect")), [T1], {BK: "yes"}).fills["t"] == 0
    assert run_sim(cut_at(lambda f: f.ctl(11.1, "disconnect")), [T1], {BK: "yes"}).fills["t"] == 1


def test_pending_work_after_the_last_line_does_not_fill():
    sim = run_sim(stale_ask(asz=3), [T1], {BK: "yes"}, tail=False)         # last line t=10, arrival would be t=11
    assert sim.orders["t"] == 1 and sim.fills["t"] == 0 and sim.heap == []


def test_a_coin_flip_decision_after_the_last_line_does_not_fill():
    f = stale_ask(asz=3)
    sim = ws.Sim(ws.Universe([DOC]), [], markouts=False)
    sim.add_forced([(0, BK, "yes", int(9.5 * S))], {0: ws.Version("coin0", "A", "T", latency=1.0, exit="X2")})
    sim.run(f.lines)                                                       # decision at 9.5 s, arrival 10.5 s: after the last line
    assert sim.fills["coin0"] == 0 and sim.orders["coin0"] == 1


def test_an_exit_inside_an_outage_does_not_fill_the_position_stays_held_and_x2_resumes_later():
    def outage(f):
        f.book(20, BK, 0.31, 0.33)
        f.ctl(20.5, "disconnect")                                          # the exit decided at 20 would arrive at 21
        for tk, b, a in ((SP7, .39, .41), (SP15, .09, .11), (BK, .31, .34)):
            f.book(70, tk, b, a)                                           # next minute: books valid again
    (r,) = run_sim(stale_ask(extra=outage), [T2], {BK: "yes"}).trade_rows()
    assert (r["exit_kind"], r["exit_ns"], r["exit_px"]) == ("x2", 71 * S, 0.31)
    held = run_sim(stale_ask(extra=lambda f: (f.book(20, BK, 0.31, 0.33), f.ctl(20.5, "stop"))), [T2], {BK: "yes"})
    assert held.trade_rows()[0]["exit_kind"] == "settle"                  # never resumed: settlement still applies


# ---------------------------------------------------------------------------------------------- 08:00Z: fill check side
def test_a_trade_by_a_seller_does_not_confirm_a_yes_buy():
    sell = run_sim(stale_ask(asz=3, extra=lambda f: f.trade(10.5, BK, 0.20, 1, "no")), [T1], {BK: "yes"}).trade_rows()[0]
    dear = run_sim(stale_ask(asz=3, extra=lambda f: f.trade(10.5, BK, 0.25, 1, "yes")), [T1], {BK: "yes"}).trade_rows()[0]
    assert sell["fill_check"] is False and dear["fill_check"] is False


def test_a_no_buy_needs_a_no_taker_at_our_no_price_or_better():
    def feed(taker, price):
        f = Feed()
        f.book(0, SP7, 0.19, 0.21)
        f.book(0, SP15, 0.09, 0.11)
        f.book(0, BK, 0.50, 0.52, bsz=3)                                    # NO ask .50: our price, only 3 quoted
        f.trade(0.5, BK, price, 1, taker)
        return f
    ok = run_sim(feed("no", 0.52), [T1], {BK: "no"}).trade_rows()[0]       # NO trade at .48 <= .50
    wrong_taker = run_sim(feed("yes", 0.52), [T1], {BK: "no"}).trade_rows()[0]
    too_dear = run_sim(feed("no", 0.45), [T1], {BK: "no"}).trade_rows()[0]  # NO trade at .55 > .50
    assert (ok["fill_check"], wrong_taker["fill_check"], too_dear["fill_check"]) == (True, False, False)


# ---------------------------------------------------------------------------------------------- 08:00Z: no reuse of liquidity
def reuse_feed(change_at=None):
    """Entry at .20 (arrival 11), fair then falls so X2 exits at the .18 bid (arrival 21), then fair jumps back at 30
    while the thin quote is untouched. Optionally the ask size changes at ``change_at``."""
    f = stale_ask()
    f.book(20, SP7, 0.26, 0.28)
    f.book(30, SP7, 0.39, 0.41)
    if change_at:
        f.delta(change_at, BK, 0.80, 5, "no")
    return f


def test_the_same_recorded_quote_is_not_traded_twice_until_its_level_changes():
    sim = run_sim(reuse_feed(), [T2], {BK: "yes"})
    assert sim.fills["t2"] == 1 and sim.orders["t2"] == 1 and sim.trade_rows()[0]["exit_kind"] == "x2"
    sim = run_sim(reuse_feed(change_at=40), [T2], {BK: "yes"})
    assert sim.fills["t2"] == 2 and [p.dec_ns for p in sim.all_pos] == [10 * S, 40 * S]


# ---------------------------------------------------------------------------------------------- 08:00Z: bucket title check
def doc_with_title(title, ticker=BK):
    meta = {SP7: {"floor_strike": 6.5}, SP15: {"floor_strike": 14.5}}
    if title is not None:
        meta[ticker] = {"yes_sub_title": title}
    return {"date": "x", "games": {G: {"tickers": [SP7, SP15, ticker], "meta": meta}}}


@pytest.mark.parametrize("title,ok", [("AAA by 7 to 14", True), ("by 7 to 14", True), ("AAA by 7-14", True),
                                      ("AAA by 8 to 14", False), ("AAA by 7 to 15", False), ("AAA by 7 or more", False),
                                      ("AAA wins", False)])
def test_bucket_range_in_the_ticker_must_match_the_title(title, ok):
    u = ws.Universe([doc_with_title(title)])
    assert (BK in u.targets) is ok
    assert u.skipped["A_bucket_title_mismatch"] == (0 if ok else 1)


def test_bucket_without_a_title_is_skipped_and_counted_and_plus_titles_parse():
    u = ws.Universe([doc_with_title(None)])
    assert BK not in u.targets and u.skipped["A_bucket_no_title"] == 1
    plus = f"KXNFLWINMARGIN-{G}-AAA15PLUS"
    assert plus in ws.Universe([doc_with_title("AAA by 15 or more", plus)]).targets
    assert plus not in ws.Universe([doc_with_title("AAA by 15 to 20", plus)]).targets
    assert BK in ws.Universe([doc_with_title("76ers by 7 to 14")]).targets     # digits in a team name are not the range


# ---------------------------------------------------------------------------------------------- 08:00Z: Family B twins
def test_a_one_sided_twin_book_is_not_given_the_long_shot_rule():
    assert ws.valid_mid(None, 0.03) == pytest.approx(0.015)                    # Family A inputs keep it
    assert ws.valid_mid(None, 0.03, longshot=False) is None
    f = Feed()
    f.book(0, TWIN, None, 0.03)
    f.book(40, TWIN, None, 0.03)
    sim = ws.Sim(ws.Universe([DOC]), [])
    sim.run(f.lines)
    assert sim.fair(sim.u.targets[PROT], 40 * S) is None


# ---------------------------------------------------------------------------------------------- 08:00Z: verdict extras
def test_lines_out_of_order_make_the_verdict_invalid():
    disc, uns = {"d1", "d2", "d3"}, {"u1", "u2", "u3"}
    rows = rows_for(disc | uns)
    assert st.verdict(rows, disc, uns, 0.0, draws=200)["verdict"] == "PASS"
    v = st.verdict(rows, disc, uns, 0.0, draws=200, lines_out_of_order=2)
    assert v["verdict"] == "INVALID: lines out of order" and v["lines_out_of_order"] == 2


def test_run_is_invalid_when_recorder_files_overlap_in_time(tmp_path):
    raw = write_raw(tmp_path, stale_ask(), "msgs-1970010100-1.jsonl.gz")
    write_raw(tmp_path, base_feed(), "msgs-1970010100-2.jsonl.gz")             # restarts at t=0 after t=10 was read
    mk = tmp_path / "markets.json"
    mk.write_text(json.dumps(DOC))
    s = ws.run([str(raw)], [str(mk)], tmp_path / "out", results={BK: "yes"}, fetch=False)
    assert s["lines_out_of_order"] > 0 and s["verdict"]["verdict"] == "INVALID: lines out of order"


def test_unseen_lower_bound_by_first_k_games():
    disc = {f"d{i}" for i in range(4)}
    order = [f"u{i}" for i in range(6)]
    nets = [0.6, -0.3, 0.5, 0.1, -0.2, 0.4]
    rows = rows_for(disc) + [{"game": g, "count": 1, "net": n, "gross": n, "fill_check": True}
                             for g, n in zip(order, nets) for _ in range(20)]
    v = st.verdict(rows, disc, set(order), 0.0, draws=300, seed=5, unseen_order=order)
    by_k = v["unseen_lower_bound_by_first_k"]
    assert sorted(by_k) == [3, 4, 5, 6]
    per = st.per_game([r for r in rows if r["game"] in order])
    assert by_k[3] == st.bootstrap_lower({g: per[g] for g in order[:3]}, 300, 5)
    assert by_k[6] == v["bootstrap_lower_95"] and by_k[3] != by_k[6]
    assert "minimum" in v["unseen_ci_note"]


def test_summary_carries_the_maker_caveat_and_the_cli_help_says_unsettled_is_dropped_whole(tmp_path, capsys):
    raw = write_raw(tmp_path, stale_ask())
    mk = tmp_path / "markets.json"
    mk.write_text(json.dumps(DOC))
    s = ws.run([str(raw)], [str(mk)], tmp_path / "out", results={BK: "yes"}, fetch=False)
    assert all(w in s["maker_caveat"] for w in ("exploratory", "can never pass", "optimistic", "go-live"))
    from kpm.cli import build_parser
    with pytest.raises(SystemExit):
        build_parser().parse_args(["ws-sim", "--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert "dropped whole" in help_text and "X2 exits" in help_text


# ---------------------------------------------------------------------------------------------- NBA (#23)
NG = "26OCT20LALGSW"
WINNER = {"LAL": f"KXNBAGAME-{NG}-LAL", "GSW": f"KXNBAGAME-{NG}-GSW"}
SPR = {(t, k): f"KXNBASPREAD-{NG}-{t}{k}" for t in ("LAL", "GSW") for k in (5, 10, 20)}
NBA_MID = {"LAL": (.60, {5: .45, 10: .25, 20: .05}), "GSW": (.40, {5: .30, 10: .15, 20: .03})}
BUCKETS = [("1TO5", "by 1 to 5"), ("6TO10", "by 6 to 10"), ("11TO20", "by 11 to 20"), ("21PLUS", "by 21 or more")]
NB = {(t, b): f"KXNBAWINMARGIN-{NG}-{t}{b}" for t in ("LAL", "GSW") for b, _ in BUCKETS}


def nba_doc(raw_kick="1970-01-01T00:01:40Z", game=NG):
    meta = {v: {"floor_strike": k + .5} for (t, k), v in SPR.items()}
    meta.update({NB[t, b]: {"yes_sub_title": f"{t} {title}"} for t in ("LAL", "GSW") for b, title in BUCKETS})
    tickers = [*WINNER.values(), *SPR.values(), *NB.values(), f"KXNBATIE-{NG}-Y",
               f"KXNBAPTSPROT-{NG}-P1-20", f"KXNBAPTS-{NG}-P1-20"]
    return {"date": "26OCT20", "games": {game: {"tickers": tickers, "meta": meta, "kickoff": None,
                                                 "kickoff_raw": {"occurrence_datetime": raw_kick}}}}


def nba_feed(thin=None):
    """Deep inputs at the mids above (+-1c); ``thin`` = {bucket ticker: (bid, ask)}."""
    f = Feed()
    for t, (w, sp) in NBA_MID.items():
        f.book(0, WINNER[t], w - .01, w + .01)
        for k, m in sp.items():
            f.book(0, SPR[t, k], m - .01, m + .01)
    for tk, (b, a) in (thin or {}).items():
        f.book(0, tk, b, a)
    return f


def test_nba_bucket_fairs_match_the_known_margin_distribution_and_sum_to_one():
    sim = ws.Sim(ws.Universe([nba_doc()], ws.SPORTS["nba"]), [])
    sim.run(nba_feed().lines)
    want = {("LAL", "1TO5"): .15, ("LAL", "6TO10"): .20, ("LAL", "11TO20"): .20, ("LAL", "21PLUS"): .05,
            ("GSW", "1TO5"): .10, ("GSW", "6TO10"): .15, ("GSW", "11TO20"): .12, ("GSW", "21PLUS"): .03}
    got = {k: sim.fair(sim.u.targets[NB[k]], 1) for k in want}
    assert got == pytest.approx(want, abs=1e-9)
    assert sum(got.values()) == pytest.approx(1.0, abs=1e-9)            # P(LAL wins) + P(GSW wins): no tie bucket
    assert len(sim.u.targets) == 8 and not sim.u.skipped              # no tie market, no Family B


def test_nba_has_no_tie_leg_no_family_b_and_the_nfl_mode_finds_nothing():
    u = ws.Universe([nba_doc()], ws.SPORTS["nba"])
    assert u.targets[NB["LAL", "1TO5"]].legs[0] == ("win", WINNER["LAL"], None)
    assert all(t.family == "A" for t in u.targets.values())                # the PROT ticker in the doc is not a target
    assert not [t for t in ws.Universe([nba_doc()]).targets.values() if t.family == "A"]


def test_nba_versions_are_16_with_the_documented_primary():
    vs = ws.all_versions(ws.SPORTS["nba"])
    assert len(vs) == 16 and len({v.vid for v in vs}) == 16
    assert sum(v.kind == "T" for v in vs) == 12 and sum(v.kind == "M" for v in vs) == 4
    assert {v.theta for v in vs if v.kind == "T"} == {0.01, 0.02} and {v.family for v in vs} == {"A"}
    assert {v.latency for v in vs if v.kind == "M"} == {0.25, 1.0} and {v.latency for v in vs if v.kind == "T"} == {0.25, 1.0, 5.0}
    (p,) = [v for v in vs if v.vid == ws.PRIMARY]
    assert (p.theta, p.latency, p.exit, p.kind) == (0.01, 1.0, "X2", "T")
    assert len(ws.all_versions()) == 34                                  # NFL unchanged


def test_nba_games_are_ordered_by_kickoff_raw_and_gated_at_20_games_10_unseen():
    u = ws.Universe([nba_doc("2026-10-21T02:00:00Z", "26OCT21AAABBB"), nba_doc("2026-10-20T23:00:00Z", "26OCT20CCCDDD")],
                    ws.SPORTS["nba"])
    assert u.kickoff["26OCT21AAABBB"] > u.kickoff["26OCT20CCCDDD"] > 0
    sp = ws.SPORTS["nba"]
    assert (sp.min_games, sp.min_unseen, sp.games_per_week) == (20, 10, 50)
    kw = dict(min_games=sp.min_games, min_unseen=sp.min_unseen)
    assert st.verdict([], {f"d{i}" for i in range(9)}, {f"u{i}" for i in range(10)}, None, **kw)["verdict"] == "NOT ENOUGH GAMES"
    assert st.verdict([], {f"d{i}" for i in range(11)}, {f"u{i}" for i in range(9)}, None, **kw)["verdict"] == "NOT ENOUGH GAMES"
    rows = rows_for([f"g{i}" for i in range(20)])
    v = st.verdict(rows, {f"g{i}" for i in range(10)}, {f"g{i}" for i in range(10, 20)}, 0.0, draws=200,
                   games_per_week=sp.games_per_week, **kw)
    assert v["verdict"] == "PASS" and v["projected_per_week"] == pytest.approx(20 * 50)


def test_nba_bucket_titles_are_cross_checked():
    doc = nba_doc()
    doc["games"][NG]["meta"][NB["LAL", "21PLUS"]]["yes_sub_title"] = "LAL by 20 or more"
    u = ws.Universe([doc], ws.SPORTS["nba"])
    assert NB["LAL", "21PLUS"] not in u.targets and u.skipped["A_bucket_title_mismatch"] == 1 and len(u.targets) == 7


def test_nba_end_to_end_yes_on_a_cheap_bucket_and_no_on_a_rich_one(tmp_path):
    import csv
    f = nba_feed({NB["LAL", "1TO5"]: (.08, .10), NB["LAL", "6TO10"]: (.35, .37)})     # fairs .15 and .20
    raw = write_raw(tmp_path, f)
    mk = tmp_path / "markets.json"
    mk.write_text(json.dumps(nba_doc()))
    out = tmp_path / "out"
    results = {NB["LAL", "1TO5"]: "yes", NB["LAL", "6TO10"]: "no"}
    s = ws.run([str(raw)], [str(mk)], out, results=results, fetch=False, sport="nba", versions="all")
    assert s["sport"] == "nba" and s["verdict"]["verdict"] == "NOT ENOUGH GAMES" and s["targets"] == {"A": 8}
    assert len(s["versions"]) == 16
    rows = list(csv.DictReader(open(out / "trades.csv")))
    by = {r["ticker"]: r for r in rows if r["version"] == ws.PRIMARY}
    cheap, rich = by[NB["LAL", "1TO5"]], by[NB["LAL", "6TO10"]]
    assert (cheap["side"], float(cheap["entry_px"]), cheap["family"]) == ("yes", 0.10, "A")
    assert (rich["side"], float(rich["entry_px"])) == ("no", 0.65)
    assert float(cheap["net"]) > 0 and float(rich["net"]) > 0 and cheap["phase"] == "pregame"
    assert all(float(r["fee_in"]) == 0.0 for r in rows if r["kind"] == "M")          # maker fee 0 on KXNBAWINMARGIN


def test_cli_sport_flag_defaults_to_nfl_and_accepts_nba():
    from kpm.cli import build_parser
    base = ["ws-sim", "--raw", "a", "--markets", "m"]
    assert build_parser().parse_args(base).sport == "nfl"
    a = build_parser().parse_args(base + ["--sport", "nba"])
    assert a.sport == "nba" and a.out is None


# ---------------------------------------------------------------------------------------------- one stream per raw dir
G2 = "26OCT22CCCDDD"
DOC2 = json.loads(json.dumps(DOC).replace(G, G2))
BK_2 = BK.replace(G, G2)


def lines_of(feed, game=G, tail=True):
    if tail:
        feed.tail()
    return [{**ln, "raw": ln["raw"].replace(G, game)} if "raw" in ln else ln for ln in feed.lines]


def write_dir(tmp_path, name, lines, fname="msgs-1970010100-1.jsonl.gz"):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    with gzip.open(d / fname, "wt") as fh:
        fh.writelines(json.dumps(x) + "\n" for x in lines)
    return str(d)


def run_dirs(dirs, docs=(DOC, DOC2), versions=(T1,)):
    sim = ws.Sim(ws.Universe(list(docs)), list(versions))
    sim.run(ws._stream(dirs))
    sim.settle({BK: "yes", BK_2: "yes"})
    return sim


def test_two_overlapping_dirs_with_the_same_sids_have_no_gaps_no_disorder_and_both_trade(tmp_path):
    a = write_dir(tmp_path, "a", lines_of(stale_ask(asz=3)))
    b = write_dir(tmp_path, "b", lines_of(stale_ask(asz=3), G2))              # both start at sid 1, seq 1, at the same times
    sim = run_dirs([a, b])
    assert sim.out_of_order == 0 and all(bs.gaps == 0 for bs in sim.sets.values()) and len(sim.sets) == 2
    assert sim.fills["t"] == 2 and {p.target.game for p in sim.all_pos} == {G, G2}
    mk = tmp_path / "m1.json"
    mk2 = tmp_path / "m2.json"
    mk.write_text(json.dumps(DOC))
    mk2.write_text(json.dumps(DOC2))
    s = ws.run([a, b], [str(mk), str(mk2)], tmp_path / "out", results={BK: "yes", BK_2: "yes"}, fetch=False)
    assert s["verdict"]["verdict"] == "NOT ENOUGH GAMES" and s["lines_out_of_order"] == 0


def test_a_disconnect_in_one_dir_does_not_stop_a_fill_in_the_other(tmp_path):
    fa = stale_ask(asz=3)
    fa.ctl(10.5, "disconnect")
    a = write_dir(tmp_path, "a", lines_of(fa))
    b = write_dir(tmp_path, "b", lines_of(stale_ask(asz=3), G2))
    sim = run_dirs([a, b])
    assert [p.target.game for p in sim.all_pos] == [G2] and sim.orders["t"] == 2


def test_a_dir_that_ends_kills_its_own_books_while_the_other_dir_goes_on(tmp_path):
    a = write_dir(tmp_path, "a", lines_of(stale_ask(asz=3), tail=False))       # last line t=10: arrival at 11 is after it
    b = write_dir(tmp_path, "b", lines_of(stale_ask(asz=3), G2))               # keeps running to t=410
    sim = run_dirs([a, b])
    assert [p.target.game for p in sim.all_pos] == [G2] and sim.orders["t"] == 2


def test_the_same_ticker_in_two_dirs_makes_the_verdict_invalid(tmp_path):
    a = write_dir(tmp_path, "a", lines_of(stale_ask(asz=3)))
    b = write_dir(tmp_path, "b", lines_of(stale_ask(asz=3)))
    mk = tmp_path / "m.json"
    mk.write_text(json.dumps(DOC))
    s = ws.run([a, b], [str(mk)], tmp_path / "out", results={BK: "yes"}, fetch=False)
    assert s["verdict"]["verdict"] == "INVALID: a ticker appears in two raw dirs" and BK in s["tickers_in_two_streams"]


def test_overlapping_processes_inside_one_dir_still_count_but_overlap_between_dirs_does_not(tmp_path):
    a = write_dir(tmp_path, "a", lines_of(stale_ask(asz=3)), "msgs-1970010100-1.jsonl.gz")
    write_dir(tmp_path, "a", lines_of(base_feed(), tail=False), "msgs-1970010100-2.jsonl.gz")   # restarts at t=0
    b = write_dir(tmp_path, "b", lines_of(stale_ask(asz=3), G2))
    sim = run_dirs([a, b])
    assert sim.out_of_order == 3                                                   # only dir a's second process


def test_stream_silences_count_gaps_over_60_s(tmp_path):
    f = base_feed()
    f.book(120, SP15, 0.09, 0.11)                                                  # 120 s after the last line
    f.book(150, SP15, 0.09, 0.11)                                                  # a 30 s gap is not counted
    a = write_dir(tmp_path, "a", lines_of(f, tail=False))
    mk = tmp_path / "m.json"
    mk.write_text(json.dumps(DOC))
    s = ws.run([a], [str(mk)], tmp_path / "out", results={}, fetch=False)
    assert s["stream_silences"][a] == {"gaps_over_60s": 1, "seconds": 120.0}


def test_coin_flip_reads_the_same_multi_stream_path(tmp_path):
    a = write_dir(tmp_path, "a", lines_of(stale_ask()))
    b = write_dir(tmp_path, "b", lines_of(stale_ask(), G2))
    args = ([a, b], ws.Universe([DOC, DOC2]), [(BK, "yes"), (BK_2, "yes")], {BK: (0, 20 * S), BK_2: (0, 20 * S)},
            {BK: "yes", BK_2: "yes"})
    out = ws.coin_flip(*args, copies=8, seed=3, latency=1.0)
    assert out == ws.coin_flip(*args, copies=8, seed=3, latency=1.0) and any(x is not None for x in out)
