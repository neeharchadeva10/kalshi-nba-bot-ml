"""Synthetic-data tests for the win-margin historical backtest (kpm.analysis.margin_hist, kpm.collectors.nfl_margin_hist)."""
from __future__ import annotations

import sqlite3
from datetime import date

import numpy as np
import pandas as pd
import pytest

from kpm.analysis import margin_hist as mh
from kpm.analysis.ws_sim import taker_fee
from kpm.collectors import nfl_margin_hist as col

G = "26OCT04AAABBB"
B16 = f"KXNFLWINMARGIN-{G}-AAA1TO6"
WIN, TIE, SP7 = f"KXNFLGAME-{G}-AAA", f"KXNFLTIE-{G}-TIE", f"KXNFLSPREAD-{G}-AAA7"
T = 12
START = 1_000_000


def const(b, a, n=T):
    return np.full(n, b), np.full(n, a)


def make_game(target=(0.15, 0.17), win=(0.59, 0.61), tie=(0.01, 0.03), sp7=(0.39, 0.41), result="yes", period="unseen",
              trades=None, t=T):
    """Fair of AAA 1-to-6 = (win mid 0.60 - tie mid 0.02 / 2) - spread-6.5 mid 0.40 = 0.19."""
    bid, ask = {}, {}
    for tk, (b, a) in {B16: target, WIN: win, TIE: tie, SP7: sp7}.items():
        bid[tk], ask[tk] = const(b, a, t)
    known = {tk: np.ones(t, dtype=bool) for tk in bid}
    grid = START + 60 * (np.arange(t) + 1)
    strikes = {SP7: {"floor_strike": 6.5}}
    uni = mh.build_universe(G, list(bid), strikes)
    return mh.Game(G, START + 10 * 60, "2026-10-04", grid, bid, ask, known, {B16: result}, trades or {}, "quadratic",
                   uni, period)


def sides_of(g):
    return {s.side: s for s in mh.build_sides(g)}


# ------------------------------------------------------------------------------------------------ fair mapping
def test_fair_mapping_and_tie_adjustment():
    g = make_game()
    s = sides_of(g)["yes"]
    assert s.fair[3] == pytest.approx(0.19)
    assert len(g.universe.targets) == 1            # 7TO14 / 15PLUS skipped: spread 14.5 not listed
    assert mh.bucket_type(B16) == "1-6"
    assert mh.bucket_type("KXNFLWINMARGIN-X-AAA15PLUS") == "15+"
    assert mh.bucket_type("KXNFLWINMARGIN-X-AAA7TO14") == "7-14"


def test_fair_15plus_uses_single_leg():
    sp15 = f"KXNFLSPREAD-{G}-AAA15"
    tk = f"KXNFLWINMARGIN-{G}-AAA15PLUS"
    g = make_game()
    for t_, (b, a) in {sp15: (0.09, 0.11), tk: (0.05, 0.07)}.items():
        g.bid[t_], g.ask[t_] = const(b, a)
        g.known[t_] = np.ones(T, dtype=bool)
    g.result[tk] = "no"
    g.universe = mh.build_universe(G, list(g.bid), {SP7: {"floor_strike": 6.5}, sp15: {"floor_strike": 14.5}})
    by = {s.ticker: s for s in mh.build_sides(g) if s.side == "yes"}
    assert by[tk].fair[0] == pytest.approx(0.10)
    assert by[B16].fair[0] == pytest.approx(0.19 - 0.0 + 0.0)    # unchanged: needs only win/tie and 6.5


def test_invalid_inputs_give_no_fair():
    g = make_game(sp7=(0.30, 0.41))                # 11c wide spread input
    assert np.isnan(sides_of(g)["yes"].fair).all()
    g = make_game(win=(0.20, 0.22), sp7=(0.69, 0.71))   # P(win)-tie/2 < P(by>6.5): fair < 0 before clamping
    assert np.isnan(sides_of(g)["yes"].fair).all()
    g = make_game(tie=(np.nan, 0.03))              # long shot, no bid, ask <= 3c -> ask / 2 counts
    assert sides_of(g)["yes"].fair[0] == pytest.approx(0.60 - 0.0075 - 0.40)
    g = make_game(tie=(np.nan, 0.10))              # no bid and ask > 3c -> unusable
    assert np.isnan(sides_of(g)["yes"].fair).all()


def test_carry_forward_rules():
    rows = [(START + 120, 0.10, 0.12), (START + 300, None, 0.20), (START + 360, 0.5, 0.4), (START + 371, 0.1, 0.2)]
    bid, ask, known, off = mh.carry_forward(START, 10, rows, END := START + 60 * 8)
    assert not known[0] and np.isnan(bid[0])                 # before the first candle
    assert bid[1] == 0.10 and bid[3] == 0.10                 # carried
    assert known[4] and np.isnan(bid[4]) and ask[4] == 0.20  # empty bid carried as empty, row stays known
    assert not known[5] and np.isnan(ask[5])                 # crossed snapshot (bid > ask) is unknown
    assert off == 1                                          # the off-grid candle
    assert not known[7] and np.isnan(ask[7])                 # nothing at or after the end (grid[7] == END)
    assert not known[6]                                      # the crossed row is carried until the next candle


# ------------------------------------------------------------------------------------------------ fees
def test_carry_forward_baseline_candle():
    bid, ask, known, off = mh.carry_forward(START, 5, [(START, 0.3, 0.4), (START + 180, 0.5, 0.6)], None)
    assert off == 0 and known[0] and bid[0] == 0.3 and bid[1] == 0.3 and bid[2] == 0.5
    bid, ask, known, off = mh.carry_forward(START, 5, [(START, 0.3, 0.4), (START + 60, 0.5, 0.6)], None)
    assert bid[0] == 0.5                                     # a real minute-0 candle wins over the baseline
    assert mh.carry_forward(START, 5, [(START - 60, 0.3, 0.4)], None)[3] == 1


def test_fees_exact():
    assert taker_fee(0.17, 5) == 0.0494
    assert mh.maker_fee(0.17, 5, "quadratic") == 0.0
    assert mh.maker_fee(0.17, 5, "quadratic_with_maker_fees") == round(0.25 * 0.0494, 4)
    assert mh.fee_unit(0.17) == taker_fee(0.17, 1)
    assert mh.order_count(0.17) == 5 and mh.order_count(0.07) == 10 and mh.order_count(0.60) == 1


# ------------------------------------------------------------------------------------------------ signal and entry
V_T2_X1 = mh.Version("T-th2-X1", "T", theta=0.02, exit="X1")
V_T1_X1 = mh.Version("T-th1-X1", "T", theta=0.01, exit="X1")
V_T1_X2 = mh.Version("T-th1-X2", "T", theta=0.01, exit="X2")


def test_signal_threshold_and_settlement_pnl():
    s = sides_of(make_game())["yes"]
    assert s.ev[0] == pytest.approx(0.19 - 0.17 - 0.0099)
    assert mh.run_taker_side(s, V_T2_X1) == []               # 1.01c < 2c
    tr = mh.run_taker_side(s, V_T1_X1)
    assert len(tr) == 1                                      # one position per market and side
    r = tr[0]
    assert (r["signal_ts"], r["entry_ts"]) == (START + 60, START + 120)    # signal minute 0, entry minute 1
    assert r["price"] == 0.17 and r["count"] == 5
    assert r["fee_in"] == 0.0494 and r["exit_kind"] == "settle"
    assert r["pnl"] == pytest.approx(5 * 1.0 - 5 * 0.17 - 0.0494)          # result = yes
    lose = mh.run_taker_side(sides_of(make_game(result="no"))["yes"], V_T1_X1)[0]
    assert lose["pnl"] == pytest.approx(-5 * 0.17 - 0.0494)
    assert lose["phase"] == "pregame"


def test_entry_only_if_price_survives():
    g = make_game()
    g.ask[B16][1] = 0.18                                     # ask moves away at the entry minute
    g.ask[B16][2:] = 0.18
    g.bid[B16][1:] = 0.16
    s = sides_of(g)["yes"]
    assert s.ev[0] > 0.01 > s.ev[1] - 1e-12 or s.ev[1] < 0.01
    tr = mh.run_taker_side(s, V_T1_X1)
    assert tr == []                                          # signal at 0 not filled; later minutes have EV < theta
    g = make_game()
    g.ask[B16][1:] = 0.16                                    # price improves: fill at the better t+1 price
    s = sides_of(g)["yes"]
    r = mh.run_taker_side(s, V_T1_X1)[0]
    assert r["price"] == 0.16 and r["count"] == 6 and r["entry_ts"] == START + 120


def test_no_side():
    g = make_game(target=(0.30, 0.32), result="no")
    s = sides_of(g)["no"]
    assert s.price[0] == pytest.approx(0.70) and s.own_bid[0] == pytest.approx(0.68)
    assert s.ev[0] == pytest.approx(0.30 - 0.19 - mh.fee_unit(0.70))
    r = mh.run_taker_side(s, mh.Version("T-th2-X1", "T", theta=0.02, exit="X1"))[0]
    assert r["side"] == "no" and r["price"] == 0.70 and r["count"] == 1
    assert r["pnl"] == pytest.approx(1.0 - 0.70 - taker_fee(0.70, 1))        # NO wins when the bucket result is no
    g_yes = make_game(target=(0.30, 0.32), result="yes")
    assert mh.run_taker_side(sides_of(g_yes)["no"], mh.Version("x", "T", theta=0.02, exit="X1"))[0]["pnl"] < 0


def test_x2_exit_next_minute_and_hold_if_never():
    g = make_game()
    g.bid[B16][4:] = 0.19                                    # bid reaches fair at minute 4: 0.19 - fee >= 0.19 - 0.01
    g.ask[B16][4:] = 0.21
    s = sides_of(g)["yes"]
    r = mh.run_taker_side(s, V_T1_X2)[0]
    assert r["entry_ts"] == START + 120
    # trigger at t'=4 (bid 0.19 - fee 0.0112 -> 0.1788 >= 0.18? no) -> check the exact rule below
    pb = 0.19
    trig = pb - mh.fee_unit(pb) >= 0.19 - 0.01 - 1e-9
    assert r["exit_kind"] == ("x2" if trig else "settle")
    g = make_game()
    g.bid[B16][4:] = 0.20                                    # 0.20 - fee(0.20)=0.1888 >= 0.18: triggers at 4, sells at 5
    g.ask[B16][4:] = 0.22
    r = mh.run_taker_side(sides_of(g)["yes"], V_T1_X2)[0]
    assert r["exit_kind"] == "x2" and r["exit_ts"] == START + 60 * 6 and r["exit_px"] == 0.20
    assert r["fee_out"] == taker_fee(0.20, 5)
    assert r["pnl"] == pytest.approx(5 * 0.20 - taker_fee(0.20, 5) - 5 * 0.17 - 0.0494)
    # the bid disappears at the sell minute -> keep checking; later minute 7 still bids -> exit there
    g = make_game()
    g.bid[B16][4:] = 0.20
    g.ask[B16][4:] = 0.22
    g.bid[B16][5] = 0.19                                     # lower bid at t'+1 than at t': not sold at 5
    r = mh.run_taker_side(sides_of(g)["yes"], V_T1_X2)[0]
    assert r["exit_kind"] == "x2" and r["exit_ts"] == START + 60 * 8     # t'=4 not held, t'=5 no trigger, t'=6 triggers, sold at index 7
    # never triggers: held to settlement
    r = mh.run_taker_side(sides_of(make_game())["yes"], V_T1_X2)[0]
    assert r["exit_kind"] == "settle"


def test_markouts_and_fill_check():
    trades = {B16: (np.array([START + 100.0, START + 119.5]), np.array([0.17, 0.18]), np.array([1.0, 1.0]))}
    g = make_game(trades=trades)
    r = mh.run_taker_side(sides_of(g)["yes"], V_T1_X1)[0]
    assert r["fill_check"] is False or r["fill_check"] is True
    # entry minute is (START+60, START+120]: the print at 0.17 (<= entry price) at START+100 counts
    assert r["fill_check"] is True
    g = make_game(trades={B16: (np.array([START + 100.0]), np.array([0.18]), np.array([1.0]))})
    assert mh.run_taker_side(sides_of(g)["yes"], V_T1_X1)[0]["fill_check"] is False
    g = make_game(trades={B16: (np.array([START + 130.0]), np.array([0.17]), np.array([1.0]))})   # next minute
    assert mh.run_taker_side(sides_of(g)["yes"], V_T1_X1)[0]["fill_check"] is False
    assert r["mo1"] == pytest.approx(0.16 - 0.17) and r["mo5"] == pytest.approx(0.16 - 0.17)


# ------------------------------------------------------------------------------------------------ maker
def test_maker_strict_trade_through():
    v = mh.Version("M-d1", "M", exit="X1", delta=0.01)
    # fair 0.19, delta 1c -> quote 0.18, strictly inside bid 0.15 / ask 0.17? no -> make the spread wider
    base = dict(target=(0.15, 0.22))
    t0 = START + 60                                          # decision at minute 0 (grid[0] = START + 60)
    def run(prices, times, counts=None):
        tr = {B16: (np.array(times, float), np.array(prices, float), np.array(counts or [10.0] * len(prices)))}
        return mh.run_maker_side(sides_of(make_game(trades=tr, **base))["yes"], v)
    r = run([0.17], [t0 + 30])                               # print below 0.18 within 60 s: fills at 0.18
    assert len(r) >= 1 and r[0]["price"] == 0.18 and r[0]["fee_in"] == 0.0 and r[0]["signal_ts"] == t0
    assert r[0]["count"] == 5 and r[0]["entry_ts"] == t0 + 60
    assert run([0.18], [t0 + 30]) == []                      # trade AT our price is not a fill (strict)
    r = run([0.17], [t0 + 61])                               # outside the minute-0 order's 60 s: filled by the minute-1 order
    assert r[0]["signal_ts"] == t0 + 60 and r[0]["entry_ts"] == t0 + 120
    assert run([0.17], [t0 - 1]) == []                       # before the order was placed
    r = run([0.17], [t0 + 30], [2.0])                        # only 2 contracts printed below us -> partial fill of 2
    assert r[0]["count"] == 2
    r = run([0.17], [t0 + 30], [0.5])
    assert r == []
    # quote must sit strictly inside the spread: ask 0.18 -> q 0.18 not inside
    tr = {B16: (np.array([t0 + 30.0]), np.array([0.10]), np.array([10.0]))}
    assert mh.run_maker_side(sides_of(make_game(target=(0.15, 0.18), trades=tr))["yes"], v) == []
    # no NO-side maker quotes
    assert mh.run_maker_side(sides_of(make_game(trades=tr, **base))["no"], v) == []


# ------------------------------------------------------------------------------------------------ exclusions and split
def test_scope_exclusions():
    assert col.in_scope("KXNFLWINMARGIN-26SEP27TENNYG")
    assert col.in_scope("KXNFLWINMARGIN-26OCT19PHINYG") is True
    assert not col.in_scope("KXNFLWINMARGIN-26OCT19WASSF")             # the live game
    assert not col.in_scope("KXNFLWINMARGIN-26FEB08SEANE")             # Super Bowl, Feb 2026
    assert not col.in_scope("KXNFLWINMARGIN-26OCT20ABCDEF")
    assert not col.in_scope("KXNFLWINMARGIN-26AUG30ABCDEF")
    assert col.parse_event("KXNFLWINMARGIN-26OCT19WASSF") == ("26OCT19WASSF", date(2026, 10, 19))


def _build_db(path, games):
    conn = sqlite3.connect(path)
    conn.executescript(col.SCHEMA)
    mid = 0

    def add(game, ev_date, ko, series, ticker, result, floor=None, close_off=3 * 3600, bid=0.5, ask=0.52):
        nonlocal mid
        mid += 1
        conn.execute("INSERT INTO mm_markets (mid, ticker, series, game, event_ticker, kickoff_ts, event_date, floor_strike, "
                     "close_ts, result, raw_json, q_start_ts, q_end_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (mid, ticker, series, game, f"{series}-{game}", ko, ev_date, floor, ko + close_off, result, "{}",
                      ko - mh.PRE_S, ko + mh.POST_S))
        conn.execute("INSERT INTO mm_quotes VALUES (?,?,?,?,?)", (mid, ko - mh.PRE_S + 60, int(bid * 1e4), int(ask * 1e4), 0))
    for game, ev_date, ko, unsettled in games:
        for k, suf in enumerate(["TIE", "AAA1TO6", "AAA7TO14", "AAA15PLUS", "BBB1TO6", "BBB7TO14", "BBB15PLUS"]):
            add(game, ev_date, ko, "KXNFLWINMARGIN", f"KXNFLWINMARGIN-{game}-{suf}",
                None if (unsettled and k == 3) else "no")
        for tm in ("AAA", "BBB"):
            add(game, ev_date, ko, "KXNFLGAME", f"KXNFLGAME-{game}-{tm}", "yes")
            add(game, ev_date, ko, "KXNFLSPREAD", f"KXNFLSPREAD-{game}-{tm}7", "no", 6.5, bid=0.3, ask=0.32)
            add(game, ev_date, ko, "KXNFLSPREAD", f"KXNFLSPREAD-{game}-{tm}15", "no", 14.5, bid=0.1, ask=0.12)
        add(game, ev_date, ko, "KXNFLTIE", f"KXNFLTIE-{game}-TIE", "no", bid=0.01, ask=0.03)
    conn.execute("INSERT INTO mm_meta VALUES ('fee_types', '{\"KXNFLWINMARGIN\": \"quadratic\"}')")
    conn.commit()
    conn.close()


def test_load_split_and_dropped_games(tmp_path):
    p = tmp_path / "m.sqlite"
    _build_db(p, [("26SEP27AAABBB", "2026-09-27", 1_790_000_000, False),
                  ("26OCT01AAABBB", "2026-10-01", 1_790_500_000, False),
                  ("26OCT04AAABBB", "2026-10-04", 1_790_700_000, True)])
    games, cov = mh.load_games(p)
    assert [g.period for g in games] == ["discovery", "unseen"]
    assert list(cov["dropped"]) == ["26OCT04AAABBB"] and "unsettled" in cov["dropped"]["26OCT04AAABBB"]
    assert len(games[0].grid) == 630 and games[0].grid[0] == games[0].kickoff - mh.PRE_S + 60
    assert len(games[0].universe.targets) == 6
    assert games[0].known[games[0].universe.targets and f"KXNFLGAME-26SEP27AAABBB-AAA"][0]


def test_unseen_minimum_games_rule():
    ok = {"net_per_contract_c": 1.0, "trades": 200, "usd_per_week": 5, "fill_check_share": 0.9, "boot_lo_c": 0.5}
    res = {"discovery": ok, "unseen": ok}
    v = mh.verdict(res, {"share_as_good": 0.01}, 24)
    assert v["run"] is False and v["pass"] is False
    v = mh.verdict(res, {"share_as_good": 0.01}, 25)
    assert v["run"] and v["pass"]
    v = mh.verdict(res, {"share_as_good": 0.06}, 30)
    assert not v["pass"] and not v["lines"]["4 coin flip: <= 5% of copies as good on unseen"]
    bad = dict(ok, boot_lo_c=-0.1)
    assert not mh.verdict({"discovery": ok, "unseen": bad}, {"share_as_good": 0.0}, 30)["pass"]


# ------------------------------------------------------------------------------------------------ randomness
def test_bootstrap_deterministic():
    pnl = np.array([1.0, -0.5, 2.0, 0.0, 0.3])
    cnt = np.array([10.0, 5.0, 10.0, 0.0, 3.0])
    a = mh._bootstrap(pnl, cnt)
    assert a == mh._bootstrap(pnl, cnt) and a[0] < a[1]
    assert mh._bootstrap(np.array([]), np.array([])) == (None, None)
    assert a != mh._bootstrap(pnl, cnt, seed=1)


def test_coin_flip_deterministic_and_in_range():
    g = make_game()
    sides = mh.build_sides(g)
    real = pd.DataFrame([{"game": G, "ticker": B16, "side": "yes"}])
    v = mh.Version("T-th2-X2", "T", theta=0.02, exit="X2")
    a = mh.coin_flip([g], {G: sides}, real, v, 5.0, copies=50)
    assert a == mh.coin_flip([g], {G: sides}, real, v, 5.0, copies=50)
    assert 0.0 <= a["share_as_good"] <= 1.0 and a["copies"] == 50
    # constant quotes: every copy that enters has identical pnl; a real mean far above it is never matched
    assert mh.coin_flip([g], {G: sides}, real, v, 1e6, copies=20)["share_as_good"] == 0.0
    assert mh.coin_flip([g], {G: sides}, real, v, -1e6, copies=20)["share_as_good"] >= 0.9     # copies whose random minute has no t+1 do not enter


def test_versions_and_primary():
    vs = mh.all_versions()
    assert len(vs) == 8 and mh.PRIMARY in {v.vid for v in vs}
    prim = next(v for v in vs if v.vid == mh.PRIMARY)
    assert prim.theta == 0.02 and prim.exit == "X2" and prim.kind == "T"
    assert mh.SEED == 20261020 and mh.BOOT_DRAWS == 10_000 and mh.COIN_COPIES == 500


def test_summarize_and_breakdown():
    g = make_game()
    tr = pd.DataFrame(mh.run_game(g, [V_T1_X1]))
    s = mh.summarize(tr, [G, "other"])
    assert s["trades"] == 1 and s["contracts"] == 5 and s["games"] == 2
    assert s["net_per_contract_c"] == pytest.approx((5 - 0.85 - 0.0494) / 5 * 100)
    assert s["usd_per_week"] == pytest.approx(s["total_usd"] / 2 * 15)
    assert mh.summarize(tr.iloc[0:0], [G])["trades"] == 0
    assert mh.breakdown(tr, "bucket")["1-6"]["trades"] == 1
