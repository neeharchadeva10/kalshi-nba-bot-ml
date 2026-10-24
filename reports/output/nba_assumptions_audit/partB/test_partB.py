"""Toy checks of every Part B calculation.  Run: .venv/Scripts/python.exe -m pytest reports/output/nba_assumptions_audit/partB/test_partB.py -q"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pb_common as C  # noqa: E402
import pb_lib as L  # noqa: E402
import pb_j9_j10 as J  # noqa: E402

E2 = C.E2


# ------------------------------------------------------------------ toy league
def toy_box(star_minutes_last=35.0, star_absent_from=None, n_games=12):
    """Teams AAA, BBB play each other n_games times, one per day from 2021-11-01.  AAA: star (aid 1, 35 min, 25 pts), bench (aid 2,
    20 min, 8 pts).  BBB: aid 3 (30 min, 20 pts).  star_absent_from = index of the first game the star misses (DNP rows).  The LAST
    game has star_minutes_last."""
    rows, games = [], []
    t0 = pd.Timestamp("2021-11-01")
    for g in range(n_games):
        date = t0 + pd.Timedelta(days=g)
        d = E2.day_int(date)
        gid = 1000 + g
        last = g == n_games - 1
        absent = star_absent_from is not None and g >= star_absent_from
        sm = star_minutes_last if last else 35.0
        for aid, name, team, mins, pts in ((1, "Star Player", "AAA", sm, 25.0), (2, "Bench Guy", "AAA", 20.0, 8.0),
                                           (3, "Other Star", "BBB", 30.0, 20.0)):
            pl = not (aid == 1 and absent) and mins > 0
            rows.append(dict(game_id=gid, yr=2022, d=d, aid=aid, name=name, team=team, played=pl, minutes=mins if pl else 0.0,
                             pts=pts if pl else 0.0))
        for team, opp, venue in (("AAA", "BBB", "home"), ("BBB", "AAA", "away")):
            games.append(dict(game_id=gid, season="2021-22", date=date, tip=1.6e9 + g * 86400, team=team, opp=opp, venue=venue,
                              team_k=team, opp_k=opp))
    return E2.Box(pd.DataFrame(rows)), pd.DataFrame(games)


# ------------------------------------------------------------------ fees (J11)
def test_fee_known_values():
    assert L.fee_expected(1, 0.50) == 0.0175
    assert L.fee_expected(1, 0.24) == 0.0128                 # 0.07*.24*.76 = .012768 -> .0128 (ledger: 0.0128)
    assert L.fee_expected(1, 0.56) == 0.0173                 # .017248 -> .0173
    assert L.fee_expected(10, 0.94) == 0.0395                # .03948 -> .0395
    assert L.fee_expected(1, 0.50, 0.0175) == 0.0044         # .004375 -> .0044 (maker rate)


def test_fee_rounds_up_never_down_and_is_symmetric():
    for p in (0.01, 0.07, 0.33, 0.5):
        for c in (1, 3, 7.5):
            raw = 0.07 * c * p * (1 - p)
            f = L.fee_expected(c, p)
            assert f >= raw - 1e-12 and f - raw < 1e-4 + 1e-12
            assert L.fee_expected(c, p) == L.fee_expected(c, round(1 - p, 4))


def test_fee_exact_cent_boundary_not_bumped():
    # 0.07*1*0.5*0.5 = 0.0175 exactly: float 0.07*.25 = 0.017500000000000002; Decimal must not round that up to 0.0176
    assert L.fee_expected(1, 0.5) == 0.0175


# ------------------------------------------------------------------ stats helpers
def test_boot_ci_deterministic_and_brackets_mean():
    x = np.arange(100.0)
    a, b = L.boot_mean_ci(x), L.boot_mean_ci(x)
    assert a == b and a["lo"] < a["mean"] < a["hi"] and a["mean"] == 49.5 and a["n"] == 100


def test_boot_ci_ignores_nan_and_handles_empty():
    assert L.boot_mean_ci([1.0, np.nan, 3.0])["n"] == 2
    assert L.boot_mean_ci([])["n"] == 0


def test_r2_corr_perfect_and_none():
    x = np.arange(10.0)
    assert L.r2_corr(x, 3 * x + 1)["r2"] == pytest.approx(1.0)
    assert L.r2_corr(x, -2 * x)["r"] == pytest.approx(-1.0)
    assert np.isnan(L.r2_corr(np.ones(10), x)["r2"])


def test_r2_multi_matches_single_when_one_column():
    rng = np.random.default_rng(0)
    x = rng.normal(size=200)
    y = 2 * x + rng.normal(size=200)
    assert L.r2_multi(x[:, None], y)["r2"] == pytest.approx(L.r2_corr(x, y)["r2"])
    assert L.r2_multi(np.column_stack([x, rng.normal(size=200)]), y)["r2"] >= L.r2_corr(x, y)["r2"] - 1e-12


def test_verdict_thresholds():
    assert L.verdict(0.01) == "CHOICE DOESN'T MATTER"
    assert L.verdict(0.10) == "MATTERS A LITTLE"
    assert L.verdict(-0.30) == "MATTERS"


# ------------------------------------------------------------------ windows / look-ahead
def test_window_bounds_equals_e2_player_window():
    hd = np.array([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22], dtype=np.int64)
    hm = np.arange(13, dtype=float) + 20
    hp = np.arange(13, dtype=float)
    hy = np.array([2021] * 4 + [2022] * 9, dtype=np.int64)
    for d, year in ((23, 2022), (14, 2022), (15, 2022), (14, 2023), (5, 2022)):
        w = E2.player_window(hd, hm, hp, hy, d, year)
        b = L.window_bounds(hd, hy, d, year)
        if w is None:
            assert b is None
        else:
            a, e, src = b
            assert src == w[2] and hm[a:e].mean() == pytest.approx(w[0]) and hp[a:e].mean() == pytest.approx(w[1])


def test_window_param_changes_window_length():
    hd = np.arange(30, dtype=np.int64)
    hy = np.full(30, 2022, dtype=np.int64)
    assert L.window_bounds(hd, hy, 30, 2022, window=5)[:2] == (25, 30)
    assert L.window_bounds(hd, hy, 30, 2022, window=20)[:2] == (10, 30)


def test_team_window_mpg_counts_missed_games_as_zero():
    mins = {(1, 1): 30.0, (1, 2): 30.0}
    assert L.team_window_mpg(mins, [1, 2, 3, 4], 1) == 15.0
    assert L.team_window_mpg(mins, [], 1) is None


def test_team_last_games_min_five_and_ten():
    box, _ = toy_box()
    d = E2.day_int(pd.Timestamp("2021-11-01")) + 4          # 4 games before -> fewer than 5
    assert L.team_last_games(box, "AAA", d, 2022) is None
    d = E2.day_int(pd.Timestamp("2021-11-01")) + 5          # 5 games before
    assert len(L.team_last_games(box, "AAA", d, 2022)) == 5
    d = E2.day_int(pd.Timestamp("2021-11-01")) + 11         # 11 before -> last 10
    g = L.team_last_games(box, "AAA", d, 2022)
    assert len(g) == 10 and g[-1] == 1010


def test_build_features_with_restores_globals():
    old = (E2.ROT_MPG, E2.WINDOW)

    class Boom(Exception):
        pass

    class FakeE2:
        pass
    orig = E2.build_injury_features
    E2.build_injury_features = lambda *a, **k: (_ for _ in ()).throw(Boom())
    try:
        with pytest.raises(Boom):
            L.build_features_with(None, None, None, rot_mpg=20, window=5)
    finally:
        E2.build_injury_features = orig
    assert (E2.ROT_MPG, E2.WINDOW) == old


def test_build_features_with_actually_patches_during_call():
    seen = {}
    orig = E2.build_injury_features
    E2.build_injury_features = lambda *a, **k: seen.update(r=E2.ROT_MPG, w=E2.WINDOW) or pd.DataFrame()
    try:
        L.build_features_with(None, None, None, rot_mpg=20, window=5)
    finally:
        E2.build_injury_features = orig
    assert seen == {"r": 20.0, "w": 5}


# ------------------------------------------------------------------ J1 on a toy league
def test_j1_counts_toy_star_out_last_game():
    box, games = toy_box(star_minutes_last=0.0)               # star DNP in the last game only
    T = L.j1_counts(games[games.game_id == 1011], box)
    a = T[T.team == "AAA"].iloc[0]
    assert a["nkey_28_own10_e2"] == 1 and a["nout_28_own10_e2"] == 1
    assert a["nkey_28_team10_e2"] == 1 and a["nout_28_team10_e2"] == 1
    assert a["nkey_32_own10_active"] == 1 and a["nkey_24_own10_e2"] == 1
    b = T[T.team == "BBB"].iloc[0]                            # 30 mpg: key at 24 and 28, not 32
    assert (b["nkey_24_own10_e2"], b["nkey_28_own10_e2"], b["nkey_32_own10_e2"]) == (1, 1, 0)


def test_j1_team_window_dilutes_a_long_absent_player_own_window_does_not():
    # star misses games 4.. -> by game 12 he has missed the team's last 8 games.  Own window (last 10 PLAYED) still says 35 mpg; the
    # team window (last 10 team games, DNP=0) says 35*2/10 = 7 mpg -> not key.
    box, games = toy_box(star_minutes_last=0.0, star_absent_from=4)
    T = L.j1_counts(games[games.game_id == 1011], box)
    a = T[T.team == "AAA"].iloc[0]
    assert a["nkey_28_own10_e2"] == 1 and a["nout_28_own10_e2"] == 1
    assert a["nkey_28_team10_e2"] == 0 and a["nout_28_team10_e2"] == 0


def test_j1_no_lookahead_same_day_minutes_do_not_change_flags():
    b1, g1 = toy_box(star_minutes_last=40.0)
    b2, g2 = toy_box(star_minutes_last=5.0)                   # only the TARGET game differs
    t1 = L.j1_counts(g1[g1.game_id == 1011], b1)
    t2 = L.j1_counts(g2[g2.game_id == 1011], b2)
    for c in [c for c in t1.columns if c.startswith("nkey_")]:
        assert (t1[c].values == t2[c].values).all(), c        # key status is decided before the game


def test_j1_early_season_falls_back_to_own_window():
    box, games = toy_box()
    T = L.j1_counts(games[games.game_id == 1004], box)        # only 4 prior team games -> team10 undefined -> own10 used
    a = T[T.team == "AAA"].iloc[0]
    assert a["team_fallback_own10"] and a["nkey_28_team10_e2"] == a["nkey_28_own10_e2"] == 1


def test_key_group_and_j1_summary_groups():
    assert [L.key_group(x) for x in (0, 1, 2, 5)] == ["0", "1", "2+", "2+"]
    cols = {}
    for ro in L.ROSTERS:
        for thr in (24, 28, 32):
            for win in ("own10", "team10"):
                cols[f"nkey_{thr}_{win}_{ro}"] = [1, 1, 1, 1]
                cols[f"nout_{thr}_{win}_{ro}"] = [0, 0, 1, 1]
    # 2 games x 2 team rows: game 1 -> 0 out, game 2 -> 2 out
    G = pd.DataFrame({"game_id": [1, 1, 2, 2], **cols})
    e1 = pd.Series([10.0, -20.0], index=[1, 2])
    cl = pd.Series([4.0, -5.0], index=[1, 2])
    out, n = L.j1_summary(G, cl, e1)
    g = out["28_own10_e2"]["groups"]
    assert n == 2 and g["0"]["gap_E1_minus_close"] == 6.0 and g["2+"]["gap_E1_minus_close"] == 15.0
    assert out["28_own10_e2"]["share_of_games_in_different_group_vs_28_own10_e2"] == 0.0


def test_j1_active_roster_drops_a_departed_player():
    # aid 9 played for AAA only in last season (2021 file year): the e2 roster rule keeps him, the active-roster rule drops him.
    box, games = toy_box()
    old = pd.DataFrame([dict(game_id=1, yr=2021, d=E2.day_int(pd.Timestamp("2021-04-01")), aid=9, name="Gone Guy", team="AAA",
                             played=True, minutes=36.0, pts=20.0)])
    box = E2.Box(pd.concat([box.df, old], ignore_index=True))
    T = L.j1_counts(games[games.game_id == 1011], box)
    a = T[T.team == "AAA"].iloc[0]
    assert a["nkey_28_own10_e2"] == 2 and a["nout_28_own10_e2"] == 1       # star + the departed player (who 'did not play')
    assert a["nkey_28_own10_active"] == 1 and a["nout_28_own10_active"] == 0


# ------------------------------------------------------------------ J6
def test_classify_unmatched():
    name2 = {"rookiekid": [10], "tradedguy": [11], "dupe": [12, 13], "stale": [14]}
    rows = {10: (np.array([205]), np.array(["AAA"]), np.array([2022])),
            11: (np.array([100, 150]), np.array(["BBB", "BBB"]), np.array([2021, 2022])),
            14: (np.array([50]), np.array(["AAA"]), np.array([2021]))}
    f = lambda n: L.classify_unmatched(n, "AAA", 200, 2022, name2, rows)
    assert f("ghost") == "no_box_row_ever"
    assert f("dupe") == "ambiguous_name"
    assert f("rookiekid") == "rookie_or_new_this_season"      # first box row ever is in this season
    assert f("tradedguy") == "traded_in_or_other_team"
    assert f("stale") == "same_team_stale_or_unexplained"


# ------------------------------------------------------------------ J7
def test_status_transitions():
    t = L.status_transitions({"A": "Questionable", "B": "Out", "C": "Questionable"}, {"A": "Out", "B": "Out"})
    assert sorted(t) == [("Out", "Out"), ("Questionable", "Out"), ("Questionable", "not listed")]


# ------------------------------------------------------------------ J8
def test_league_to_date():
    tg = pd.DataFrame({"pts": [100.0, 110.0, 120.0, 130.0], "pace": [100.0, 100.0, 100.0, 100.0]})
    o, p, t = L.league_to_date(tg, 1)
    assert (o, p, t) == (105.0, 100.0, 210.0)
    o, p, t = L.league_to_date(tg, 2)
    assert o == 115.0 and t == 230.0


# ------------------------------------------------------------------ J10
def test_mids_spread_filter():
    pts = [(230.0, 0.60, 0.66), (233.0, 0.55, 0.56), (236.0, 0.30, 0.50), (239.0, 0.0, 0.2), (242.0, 0.1, 0.08)]
    assert L.mids(pts, 0.15) == [(230.0, 0.63), (233.0, 0.555)]
    assert [s for s, _ in L.mids(pts, 0.05)] == [233.0]


def test_crossing_matches_dashboard_implied_total():
    sys.path.insert(0, str(C.OUT / "nba_injury_speed"))
    import common
    pts = [(228.5, 0.70, 0.74), (231.5, 0.60, 0.62), (234.5, 0.46, 0.50), (237.5, 0.30, 0.34)]
    assert L.crossing(L.mids(pts, 0.15)) == pytest.approx(common.implied_total(pts))
    assert L.crossing([(230.0, 0.40)]) is None                # whole curve below 0.5 -> no crossing
    assert L.crossing([]) is None


def test_crossing_interpolation_value():
    assert L.crossing([(230.0, 0.60), (234.0, 0.40)]) == pytest.approx(232.0)


def test_main_strike_tie_goes_to_lower():
    assert L.main_strike([(230.0, 0.55), (233.0, 0.45), (236.0, 0.3)]) == 230.0
    assert L.main_strike([(230.0, 0.7), (233.0, 0.52), (236.0, 0.3)]) == 233.0
    assert L.main_strike([]) is None


def test_vw_mid():
    assert L.vw_mid([0.4, 0.6], [0.5, 0.7], [1, 3]) == pytest.approx((0.45 * 1 + 0.65 * 3) / 4)
    assert L.vw_mid([0.4, 0.6], [0.5, 0.7], [0, 0]) == pytest.approx(0.65)      # no volume -> last mid
    assert L.vw_mid([np.nan], [np.nan], [1]) is None


# ------------------------------------------------------------------ J9
def snap(yes, no):
    return {"recv_ns": 1_000_000_000, "m": {"type": "orderbook_snapshot", "msg": {"yes_dollars_fp": yes, "no_dollars_fp": no}}}


def dlt(ms, side, price, d):
    return {"recv_ns": ms * 1_000_000 + 5_000_000, "m": {"type": "orderbook_delta",
                                                          "msg": {"side": side, "price_dollars": price, "delta_fp": d, "ts_ms": ms}}}


def test_book_ask_from_opposite_bid():
    ob = J.OBook()
    ob.snapshot([["0.40", "10"], ["0.44", "5"]], [["0.50", "7"], ["0.55", "2"]])
    assert ob.ask("yes") == 0.45                              # 1 - best NO bid 0.55
    assert ob.ask("no") == 0.56                               # 1 - best YES bid 0.44
    ob.delta("no", "0.55", "-2")                              # level removed -> next best NO bid 0.50
    assert ob.ask("yes") == 0.50


def test_replay_strictly_before_fill_and_snapshot_required():
    msgs = [snap([["0.44", "5"]], [["0.55", "2"], ["0.50", "9"]]), dlt(2000, "no", "0.55", "-2"), dlt(3000, "no", "0.52", "4")]
    assert J.replay(msgs, 2000).ask("yes") == 0.45           # the delta AT 2000 ms is not yet applied (strictly before)
    assert J.replay(msgs, 2001).ask("yes") == 0.50
    assert J.replay(msgs, 3001).ask("yes") == 0.48
    assert J.replay([dlt(1, "no", "0.5", "1")], 10) is None  # no snapshot -> unknown, never guessed


def test_slippage_sign_and_summary():
    assert J.slippage_cents(0.52, 0.51) == 1.0
    assert J.slippage_cents(0.50, 0.51) == -1.0
    assert J.slippage_cents(0.5, None) is None
    assert J.slippage_cents(0.5, float('nan')) is None      # a missing ask must never become a number
    s = J.summarize([1.0, 0.0, -1.0, None, 2.0])
    assert s["n"] == 4 and s["mean_cents"] == 0.5 and s["share_zero"] == 0.25


# ------------------------------------------------------------------ the reproduction gate
def test_e1_reproduction_2022_26():
    D = C.load_e1()
    G = C.game_err(D)
    t = G[G.season.isin(C.SEASONS[1:])]
    assert abs(t.err.abs().mean() - C.REPRO_TEST_MAE) < 5e-6 and len(t) == 4923


def _line(f, tk, m_type, **kw):
    if m_type == "orderbook_snapshot":
        msg = {"market_ticker": tk, "yes_dollars_fp": kw["yes"], "no_dollars_fp": kw["no"]}
        return json.dumps({"recv_ns": kw["ms"] * 1_000_000, "f": f, "m": {"type": m_type, "msg": msg}})
    msg = {"market_ticker": tk, "side": kw["side"], "price_dollars": kw["price"], "delta_fp": kw["d"], "ts_ms": kw["ms"]}
    return json.dumps({"recv_ns": (kw["ms"] + 5) * 1_000_000, "f": f, "m": {"type": m_type, "msg": msg}})


def test_stream_asks_matches_replay_rule_two_sessions_and_other_tickers():
    f1, f2 = "msgs-00-111.jsonl.gz", "msgs-01-222.jsonl.gz"
    lines = [_line(f1, "T1", "orderbook_snapshot", yes=[["0.44", "5"]], no=[["0.55", "2"], ["0.50", "9"]], ms=1000),
             _line(f1, "OTHER", "orderbook_snapshot", yes=[["0.10", "1"]], no=[["0.10", "1"]], ms=1000),
             _line(f2, "T1", "orderbook_snapshot", yes=[["0.44", "5"]], no=[["0.55", "2"], ["0.50", "9"]], ms=1500),
             _line(f1, "T1", "orderbook_delta", side="no", price="0.55", d="-2", ms=2000),
             _line(f2, "T1", "orderbook_delta", side="no", price="0.55", d="-2", ms=2000),
             _line(f1, "T1", "orderbook_delta", side="no", price="0.52", d="4", ms=3000)]
    wants = [{"key": "a", "ticker": "T1", "outcome": "yes", "ms": 2000}, {"key": "b", "ticker": "T1", "outcome": "yes", "ms": 2500},
             {"key": "c", "ticker": "T1", "outcome": "no", "ms": 2500}]
    res = J.stream_asks(lines, wants)
    assert res["a"] == [0.45, 0.45]               # delta at exactly 2000 ms is not yet applied (strictly before)
    assert res["b"] == [0.50, 0.50]               # level removed by then (both sessions)
    assert res["c"] == [0.56, 0.56]               # NO buy: 1 - best YES bid 0.44
    # identical to replay() on the same messages
    msgs = [json.loads(ln) for ln in lines if '"T1"' in ln and f1 in ln]
    assert J.replay(msgs, 2500).ask("yes") == res["b"][0]


def test_stream_asks_ignores_stale_session():
    lines = [_line("msgs-00-111.jsonl.gz", "T1", "orderbook_snapshot", yes=[["0.44", "5"]], no=[["0.55", "2"]], ms=1000)]
    assert J.stream_asks(lines, [{"key": "k", "ticker": "T1", "outcome": "yes", "ms": 1000 + 700_000}]) == {}
    assert J.stream_asks(lines, [{"key": "k", "ticker": "T1", "outcome": "yes", "ms": 1000 + 5_000}])["k"] == [0.45]
