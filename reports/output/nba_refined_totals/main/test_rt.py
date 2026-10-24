"""Unit tests for research #65 (main).  Toy data only; no real outcomes.
Run:  .venv/Scripts/python.exe -m pytest reports/output/nba_refined_totals/main/test_rt.py -q"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rt_lib as R  # noqa: E402

E2 = R.E2
BASE = pd.Timestamp("2023-01-01")


# ------------------------------------------------------------------ versions
def test_versions_match_csv_exactly():
    ids = R.load_version_ids()
    assert ids == R.all_versions()
    assert len(ids) == 208 and len(set(ids)) == 208


def test_parse_version():
    v = R.parse_version("A|E3|rolling|G4|Q+NEW")
    assert (v["part"], v["model"], v["lean"], v["G"], v["skips"]) == ("A", "E3", "rolling", 4, ("Q", "NEW"))
    assert R.parse_version("A|F42|none|G2|none")["skips"] == ()
    b = R.parse_version("B|M28|T20|game")
    assert (b["M"], b["tau"], b["scope"]) == (28, 20, "game")
    with pytest.raises(Exception):
        R.parse_version("A|E9|none|G2|none")


def test_d_seasons_amendment_1():
    assert R.d_seasons("A|F42|none|G4|none") == ("2023-24",)
    assert R.d_seasons("A|E1|none|G4|none") == ("2022-23", "2023-24")
    assert R.d_seasons("B|M20|T10|game") == ("2021-22", "2022-23", "2023-24")


# ------------------------------------------------------------------ P&L
def test_sb_pnl_with_pushes():
    act = np.array([230.0, 230.0, 230.0, 230.0])
    line = np.array([225.5, 235.5, 230.0, 230.0])
    over = R.sb_pnl(np.array([True, True, True, False]), act, line)
    assert over[0] == pytest.approx(10 / 11) and over[1] == -1.0 and over[2] == 0.0 and over[3] == 0.0
    under = R.sb_pnl(np.array([False, False]), np.array([230.0, 230.0]), np.array([225.5, 235.5]))
    assert under[0] == -1.0 and under[1] == pytest.approx(10 / 11)
    s = R.summarize_sb(over)
    assert (s["n"], s["wins"], s["losses"], s["pushes"]) == (4, 1, 1, 2)
    assert s["mean"] == pytest.approx((10 / 11 - 1) / 4)


def test_kalshi_fee_and_net():
    # 0.07 * P * (1-P) rounded UP to $0.000001
    assert R.kalshi_fee1(0.5) == pytest.approx(0.0175)
    f = R.kalshi_fee1(0.53)
    exact = 0.07 * 0.53 * 0.47
    assert exact <= f + 1e-12 and f - exact < 1e-6 + 1e-12
    assert R.kalshi_net(1.0, 0.50, 0.0175) == pytest.approx(1 - 0.50 - 0.01 - 0.0175)
    assert R.kalshi_net(0.0, 0.50, 0.0175) == pytest.approx(-0.5275)


# ------------------------------------------------------------------ lean fix
def lean_toy():
    rows = []
    gid = 0
    for day in range(40):                                   # 2 games per day, season "S1", model total 200, actual 200 + day%5
        for _ in range(2):
            rows.append({"game_id": gid, "season": "S1", "et_date": BASE + pd.Timedelta(days=day), "actual_total": 200.0 + day % 5, "m": 200.0})
            gid += 1
    rows.append({"game_id": gid, "season": "S2", "et_date": BASE, "actual_total": 999.0, "m": 0.0})      # other season never leaks
    return pd.DataFrame(rows)


def test_lean_strictly_earlier_dates_and_min_games():
    g = lean_toy()
    t = R.lean_table(g, "m", "X").set_index(["season", "et_date"])
    for day in (0, 5, 14, 15, 16, 39):
        d = BASE + pd.Timedelta(days=day)
        b_ref, n_ref = R.lean_brute(g, "m", "S1", d)
        assert t.loc[("S1", d), "n_games_before"] == n_ref == 2 * day
        assert t.loc[("S1", d), "b"] == pytest.approx(b_ref)
    assert t.loc[("S1", BASE + pd.Timedelta(days=14)), "b"] == 0.0           # 28 games < 30 -> 0
    assert t.loc[("S1", BASE + pd.Timedelta(days=15)), "n_games_before"] == 30   # exactly 30 -> used
    assert t.loc[("S1", BASE + pd.Timedelta(days=15)), "b"] == pytest.approx(np.mean([x % 5 for x in range(15) for _ in range(2)]))
    assert t.loc[("S2", BASE), "b"] == 0.0


def test_lean_same_day_actuals_do_not_leak():
    g = lean_toy()
    g2 = g.copy()
    g2.loc[(g2.season == "S1") & (g2.et_date == BASE + pd.Timedelta(days=20)), "actual_total"] = 1e6
    a = R.lean_table(g, "m", "X").set_index(["season", "et_date"]).b
    b = R.lean_table(g2, "m", "X").set_index(["season", "et_date"]).b
    d20 = ("S1", BASE + pd.Timedelta(days=20))
    assert a.loc[d20] == b.loc[d20]                            # day-20 outcomes don't change day-20's b
    assert a.loc[("S1", BASE + pd.Timedelta(days=21))] != b.loc[("S1", BASE + pd.Timedelta(days=21))]


def test_lean_ignores_games_without_model_but_not_without_line():
    g = lean_toy()
    g["close_line"] = np.nan                                    # no lines at all: b still computed from actuals
    g.loc[g.game_id < 10, "m"] = np.nan                         # model undefined for 10 games -> excluded
    t = R.lean_table(g, "m", "X").set_index(["season", "et_date"])
    assert t.loc[("S1", BASE + pd.Timedelta(days=39)), "n_games_before"] == 78 - 10


# ------------------------------------------------------------------ Part A decisions and skips
def toy_ev():
    n = 6
    return pd.DataFrame({
        "game_id": np.arange(n), "season": ["2023-24"] * n,
        "f42": [210.0, 230.0, 220.0, 225.0, np.nan, 218.0], "e1": [210.0] * n, "e3": [210.0] * n,
        "b_f42": [2.0] * n, "b_e1": [0.0] * n, "b_e3": [0.0] * n,
        "close_line": [220.0, 220.0, 220.0, 220.0, 220.0, np.nan],
        "actual_total": [200.0, 240.0, 220.0, 230.0, 240.0, 240.0],
        "q_skip": [False, False, False, True, False, False], "new_skip": [False] * n, "early_skip": [False] * n,
        "k_median": [220.0] * n, "k_valid": [True] * n,
        "k_net_over": [0.4] * n, "k_net_under": [-0.6] * n, "k_win_over": [1.0] * n, "k_win_under": [0.0] * n})


def test_part_a_bets_rule_skips_pushes_nan():
    ev = toy_ev()
    b = R.part_a_sb(ev, R.parse_version("A|F42|none|G4|none"))
    # 210 (under, -10), 230 (over, +10), 220 (no bet), 225 (gap 5 over, q not skipped here), nan, no line
    assert b.game_id.tolist() == [0, 1, 3]
    assert b.over.tolist() == [False, True, True]
    assert b.pnl.tolist() == pytest.approx([10 / 11, 10 / 11, 10 / 11])
    bq = R.part_a_sb(ev, R.parse_version("A|F42|none|G4|Q"))
    assert bq.game_id.tolist() == [0, 1]
    br = R.part_a_sb(ev, R.parse_version("A|F42|rolling|G8|none"))      # adj = tot + 2: 212 (gap 8 under), 232 (over)
    assert br.game_id.tolist() == [0, 1]
    bp = R.part_a_sb(ev, R.parse_version("A|F42|none|G2|none"))         # game 2 gap 0: none; game 5 has no line
    assert 2 not in bp.game_id.tolist() and 5 not in bp.game_id.tolist()
    # push: line 220 vs actual 220 would pay 0 -- add a bet game that pushes
    ev.loc[2, ["f42", "actual_total"]] = [230.0, 220.0]
    bp2 = R.part_a_sb(ev, R.parse_version("A|F42|none|G4|none"))
    assert bp2.set_index("game_id").pnl.loc[2] == 0.0


def test_part_a_kalshi_uses_median_gap_and_main_strike_net():
    ev = toy_ev()
    k = R.part_a_kalshi(ev, R.parse_version("A|F42|none|G4|none"))
    assert k.game_id.tolist() == [0, 1, 3]
    assert k.net.tolist() == [-0.6, 0.4, 0.4]
    ev.loc[1, "k_valid"] = False
    assert R.part_a_kalshi(ev, R.parse_version("A|F42|none|G4|none")).game_id.tolist() == [0, 3]


def test_flags_either_team():
    e3 = pd.DataFrame({"game_id": [1, 1, 2, 2, 3, 3], "team_k": list("ABCDEF"), "n_q_rot_c60": [0, 1, 0, 0, 0, 0],
                       "new_share_own_c60": [0.0, 0.0, 0.29, 0.30, 0.1, 0.1]})
    d = lambda i: BASE + pd.Timedelta(days=i)           # noqa: E731
    rows = []
    # team A plays 12 games before game 1's date; game 3's team E has played only 9
    for i in range(12):
        rows.append((100 + i, "A", "S", d(i)))
    rows += [(1, "A", "S", d(12)), (1, "B", "S", d(12)), (2, "C", "S", d(12)), (2, "D", "S", d(12))]
    for i in range(12):
        rows += [(200 + i, "B", "S", d(i)), (300 + i, "C", "S", d(i)), (400 + i, "D", "S", d(i))]
    for i in range(9):
        rows.append((500 + i, "E", "S", d(i)))
    for i in range(12):
        rows.append((600 + i, "F", "S", d(i)))
    rows += [(3, "E", "S", d(12)), (3, "F", "S", d(12))]
    tg = pd.DataFrame(rows, columns=["game_id", "team", "season", "date"])
    f = R.compute_flags(e3, tg, pd.DataFrame({"game_id": [1, 2, 3], "home": "x", "away": "y"})).set_index("game_id")
    assert f.q_skip.tolist() == [True, False, False]
    assert f.new_skip.tolist() == [False, True, False]
    assert f.early_skip.tolist() == [False, False, True]


# ------------------------------------------------------------------ Part B (look-ahead guards on a toy world)
def dn(i):
    return E2.day_int(BASE + pd.Timedelta(days=i))


def toy_world(report_created_offset=-4000, with_late_report=False):
    """Team ATL (home) vs BOS (away); season 2022-23 (file year 2023); games 0..11 one per day, target game = 11.
    id1 Star (ATL) 30 min / 20 pts in games 0..10, ABSENT in game 11; id2 Sub (ATL) 10 min; id3 BigBang (ATL) plays ONLY game 11 (60 min);
    id7 (ATL games 0..5 at 40 min, then BOS); id8 Gone Guy (ATL games 0..9 at 30 min, BOS from game 10).  BOS: id11 25 min."""
    rows = []

    def add(g, aid, name, team, mins, pts):
        rows.append({"game_id": 9000 + g, "yr": 2023, "d": dn(g), "aid": aid, "name": name, "team": team, "played": mins > 0,
                     "minutes": float(mins), "pts": float(pts)})

    for g in range(12):
        if g <= 10:
            add(g, 1, "Sam Star", "ATL", 30, 20)
        else:
            add(g, 1, "Sam Star", "ATL", 0, 0)           # DNP row in game 11 (still a row, still current-team evidence)
        add(g, 2, "Sue Sub", "ATL", 10, 4)
        add(g, 11, "Bob Bos", "BOS", 25, 12)
        if g == 11:
            add(g, 3, "Big Bang", "ATL", 60, 50)
        if g <= 5:
            add(g, 7, "Seven Seven", "ATL", 40, 10)
        else:
            add(g, 7, "Seven Seven", "BOS", 40, 10)
        if g <= 9:
            add(g, 8, "Gone Guy", "ATL", 30, 15)
        else:
            add(g, 8, "Gone Guy", "BOS", 30, 15)
    box = pd.DataFrame(rows)
    tip = 1_700_000_000
    games = pd.DataFrame({"game_id": [9000 + g for g in range(12)], "season": "2022-23",
                          "date": [BASE + pd.Timedelta(days=g) for g in range(12)],
                          "tip": [tip + 86400 * g for g in range(12)], "away": "BOS", "home": "ATL"})
    tip11 = tip + 86400 * 11
    mdy = (BASE + pd.Timedelta(days=11)).strftime("%m/%d/%Y")
    row = {"date": mdy, "matchup": "BOS@ATL", "team": "AtlantaHawks", "player": "Star,Sam", "status": "Out"}
    created = {"early": tip11 + report_created_offset}
    content = {"early": {"rows": [row], "nys": []}}
    if with_late_report:
        created["late"] = tip11 - 3000                  # created AFTER T-60 (tip - 3600): must be ignored
        content["late"] = {"rows": [dict(row, player="Star,Sam", status="Out")], "nys": []}
    return games, box, E2.Reports(created=created, content=content)


def part_b_row(F, gid, team):
    return F[(F.game_id == gid) & (F.team == team)].iloc[0]


def test_part_b_basic_both_flavors():
    games, box, rep = toy_world()
    F = R.build_part_b(games, box, rep)
    r = part_b_row(F, 9011, "ATL")
    assert r.defined and r.n_team_games_before == 11
    # Star: window = ATL games 1..10 (last 10 before game 11): 30 mpg, 20 fresh ppg, fresh; absent in box (no minutes) and listed Out
    assert r.S_report_M28 == pytest.approx(20.0) and r.S_box_M28 == pytest.approx(20.0)
    j = json.loads(r.abs_box_M28)
    assert j == [{"athlete_id": 1, "fresh_ppg": 20.0, "mpg_per_team_game": 30.0}]
    assert r.S_box_M20 == pytest.approx(20.0)           # Sub (10 mpg) never qualifies; BigBang/other-team players are not candidates
    assert part_b_row(F, 9011, "BOS").S_box_M20 == 0.0


def test_part_b_report_after_t60_ignored():
    games, box, rep = toy_world(report_created_offset=-3000)        # created 3000 s before tip: after T-60
    # give the season a report for ANOTHER game (game 9010) so the season has reports: a game without one then gives S = 0
    tip10 = int(games.tip.iloc[10])
    mdy10 = (BASE + pd.Timedelta(days=10)).strftime("%m/%d/%Y")
    rep.created.append(tip10 - 5000)
    rep.names.append("g10")
    rep.content["g10"] = {"rows": [{"date": mdy10, "matchup": "BOS@ATL", "team": "BostonCeltics", "player": "Bos,Bob", "status": "Out"}], "nys": []}
    rep.created, rep.names = zip(*sorted(zip(rep.created, rep.names)))
    rep.created, rep.names = list(rep.created), list(rep.names)
    F = R.build_part_b(games, box, rep)
    r = part_b_row(F, 9011, "ATL")
    assert not r.report_found and r.S_report_M28 == 0.0 and r.S_report_M20 == 0.0   # no report -> S = 0, not NaN
    assert r.S_box_M28 == pytest.approx(20.0)                                       # box flavor unaffected
    games, box, rep = toy_world(report_created_offset=-4000, with_late_report=True)
    r2 = part_b_row(R.build_part_b(games, box, rep), 9011, "ATL")
    assert r2.report_name == "early" and r2.S_report_M28 == pytest.approx(20.0)     # the later report is never used
    # boundary: created exactly at T-60 is allowed (<=)
    games, box, rep = toy_world(report_created_offset=-3600)
    assert part_b_row(R.build_part_b(games, box, rep), 9011, "ATL").report_found


def test_part_b_own_game_rows_excluded_from_windows():
    games, box, rep = toy_world()
    F = R.build_part_b(games, box, rep)
    j = json.loads(part_b_row(F, 9011, "ATL").abs_box_M28)
    # if game 11's own row (0 min) leaked into the window the window would be games 2..11 and mpg would be 27, ppg 18
    assert j[0]["mpg_per_team_game"] == 30.0 and j[0]["fresh_ppg"] == 20.0
    # BigBang plays only game 11 (60 min): he must not be a candidate in game 11 and must not change anyone's numbers
    assert 3 not in [x["athlete_id"] for x in json.loads(part_b_row(F, 9011, "ATL").abs_box_M20)]
    box2 = box.copy()
    box2.loc[(box2.aid == 3), ["minutes", "pts"]] = [1.0, 99.0]
    F2 = R.build_part_b(games, box2, rep)
    assert part_b_row(F2, 9011, "ATL").abs_box_M28 == part_b_row(F, 9011, "ATL").abs_box_M28


def test_part_b_current_team_uses_rows_before_g_only():
    games, box, rep = toy_world()
    F = R.build_part_b(games, box, rep)
    ids = [x["athlete_id"] for x in json.loads(part_b_row(F, 9011, "ATL").abs_box_M20)]
    # id8 (ATL until game 9, BOS rows in game 10) and id7 (BOS since game 6) are NOT ATL current-team players -> not ATL absentees
    assert 7 not in ids and 8 not in ids and 1 in ids
    # a game-11-only row cannot make someone "current": mutate id8's game-11 row to ATL, still not counted (rows before g only)
    box2 = box.copy()
    box2 = pd.concat([box2, pd.DataFrame([{"game_id": 9011, "yr": 2023, "d": dn(11), "aid": 8, "name": "Gone Guy", "team": "ATL", "played": False,
                                           "minutes": 0.0, "pts": 0.0}])], ignore_index=True)
    ids2 = [x["athlete_id"] for x in json.loads(part_b_row(R.build_part_b(games, box2, rep), 9011, "ATL").abs_box_M20)]
    assert 8 not in ids2
    # but at game 10 (before the trade is visible) id8 IS an ATL candidate who played -> not absent; check he is current at game 10
    r10 = part_b_row(R.build_part_b(games, box, rep), 9010, "ATL")
    assert r10.defined


def test_part_b_min_team_games_and_window_divisor():
    games, box, rep = toy_world()
    F = R.build_part_b(games, box, rep)
    assert not part_b_row(F, 9003, "ATL").defined and part_b_row(F, 9003, "ATL").S_box_M28 == 0.0   # 3 prior games < 5
    r5 = part_b_row(F, 9005, "ATL")
    assert r5.defined and r5.n_team_games_before == 5
    # window of 5 games: divisor is 5 (not 10)
    stats = {("T", g): {1: (30.0, 20.0)} for g in range(5)}
    c = R.window_candidates(stats, "T", list(range(5)), {1})
    assert c[1] == (30.0, 20.0, True)
    stats2 = {("T", g): {1: (30.0, 20.0)} for g in range(0, 10, 2)}      # present in 5 of 10 games -> halves
    c2 = R.window_candidates(stats2, "T", list(range(10)), {1})
    assert c2[1][0] == 15.0 and c2[1][1] == 10.0
    assert not R.window_candidates({("T", 0): {1: (30.0, 20.0)}}, "T", list(range(10)), {1})[1][2]     # not in last 3 -> not fresh
    assert 1 not in R.window_candidates(stats, "T", list(range(5)), set())                               # not current-team


# ------------------------------------------------------------------ bootstrap and luck tests
def test_bootstrap_and_luck_reproducible():
    x = np.random.default_rng(1).normal(0.02, 1.0, 300)
    assert R.boot_ci(x) == R.boot_ci(x)
    m, lo, hi = R.boot_ci(x)
    assert lo < m < hi
    po, pu = R.sb_pnl(np.ones(300, bool), 220 + x * 10, np.full(300, 220.0)), R.sb_pnl(np.zeros(300, bool), 220 + x * 10, np.full(300, 220.0))
    a = R.luck_random_sides(po, pu, 0.05)
    assert a == R.luck_random_sides(po, pu, 0.05) and a["copies"] == 500
    assert 0.0 <= a["frac_copies_ge_actual"] <= 1.0
    elig = {"s1": np.random.default_rng(2).normal(0, 1, 200), "s2": np.random.default_rng(3).normal(0, 1, 120)}
    b = R.luck_random_games(elig, {"s1": 40, "s2": 30}, 0.5)
    assert b == R.luck_random_games(elig, {"s1": 40, "s2": 30}, 0.5) and b["n_bets"] == 70
    assert R.luck_random_games(elig, {"s1": 40, "s2": 30}, -9.0)["frac_copies_ge_actual"] == 1.0
    assert R.luck_random_games(elig, {"s1": 40, "s2": 30}, 9.0)["frac_copies_ge_actual"] == 0.0


def test_choose_best_ties_and_floor():
    rows = pd.DataFrame({"version": list("abcd"), "n": [200, 300, 100, 300], "mean_pnl": [0.05, 0.05, 0.9, 0.04]})
    assert R.choose_best(rows, 150).version == "b"            # tie on mean -> more bets; c below the floor
    assert R.choose_best(rows, 1000) is None


def test_verdict_logic():
    base = {"pooled_sb": {"mean": 0.03, "boot_lo": 0.01}, "kalshi": {"n": 40, "mean_net": 0.02}, "luck": {"frac_copies_ge_actual": 0.02},
            "per_window_sb": {"H2-sb": {"mean": 0.03}}}
    assert R.verdict(base)["verdict"] == "PASS"
    weak = dict(base, pooled_sb={"mean": 0.03, "boot_lo": -0.01}, luck={"frac_copies_ge_actual": 0.08})
    assert R.verdict(weak)["verdict"] == "PROMISING"
    assert R.verdict(dict(weak, kalshi={"n": 40, "mean_net": -0.01}))["verdict"] == "FAIL"
    assert R.verdict(dict(weak, luck={"frac_copies_ge_actual": 0.2}))["verdict"] == "FAIL"


# ------------------------------------------------------------------ toy end to end
def test_toy_end_to_end_part_b_and_a():
    games, box, rep = toy_world()
    F = R.build_part_b(games, box, rep)
    preds = pd.DataFrame({"game_id": [9011], "season": ["2022-23"], "et_date": [BASE + pd.Timedelta(days=11)], "home": ["ATL"], "away": ["BOS"],
                          "actual_total": [230.0], "close_line": [225.0], "f42": [235.0], "e1": [235.0], "e3": [235.0],
                          "b_f42": [0.0], "b_e1": [0.0], "b_e3": [0.0], "k_median": [np.nan], "k_valid": [False],
                          "k_net_over": [np.nan], "k_net_under": [np.nan], "k_win_over": [np.nan], "k_win_under": [np.nan]})
    flags = pd.DataFrame({"game_id": [9011], "q_skip": [False], "new_skip": [False], "early_skip": [False], "flags_defined": [True]})
    ev = R.make_eval_frame(preds, flags, F)
    assert bool(ev.ok_report.iloc[0]) and bool(ev.ok_box.iloc[0])
    b = R.part_b_sb(ev, R.parse_version("B|M28|T20|game"), "report")
    assert b.pnl.tolist() == pytest.approx([10 / 11]) and b.over.all()
    assert len(R.part_b_sb(ev, R.parse_version("B|M28|T30|team"), "report")) == 0
    a = R.part_a_sb(ev, R.parse_version("A|F42|none|G8|none"))
    assert a.over.tolist() == [True] and a.pnl.tolist() == pytest.approx([10 / 11])    # 235 vs 225 -> over; 230 > 225 wins
    wins = {"sb": [("w", ("2022-23",), "report")], "k": ()}
    res = R.evaluate_version(ev, "B|M28|T20|game", wins)
    assert res["pooled_sb"]["n"] == 1 and res["luck"]["n_bets"] == 1
    res = R.evaluate_version(ev, "A|F42|none|G8|none", wins)
    assert res["pooled_sb"]["n"] == 1 and res["kalshi"]["n"] == 0


def test_run_test_refuses_without_audit(tmp_path):
    import run_test as RT
    with pytest.raises(SystemExit):
        RT.check_gates(tmp_path)                                # no AUDIT_OK
    (tmp_path / "AUDIT_OK").write_text(json.dumps({"sha256": {}}))
    with pytest.raises(SystemExit):
        RT.check_gates(tmp_path)                                # AUDIT_OK does not cover the files
    (tmp_path / "AUDIT_OK").write_text(json.dumps(R.audit_hashes(files=R.OWN_AUDIT[:1])))
    with pytest.raises(SystemExit):
        RT.check_gates(tmp_path, files=R.OWN_AUDIT[:1])         # no results_select.json
    (tmp_path / "results_select.json").write_text("{}")
    (tmp_path / "test_started.json").write_text("{}")
    with pytest.raises(SystemExit):
        RT.check_gates(tmp_path, files=R.OWN_AUDIT[:1])         # already started
    (tmp_path / "test_started.json").unlink()
    assert RT.check_gates(tmp_path, files=R.OWN_AUDIT[:1]) == {}
    (tmp_path / "AUDIT_OK").write_text(json.dumps({"sha256": {"rt_lib.py": "0" * 64}}))
    with pytest.raises(SystemExit):
        RT.check_gates(tmp_path, files=R.OWN_AUDIT[:1])         # hash mismatch


def test_load_tables_restricts_seasons_first(tmp_path):
    ev = R.load_tables(seasons=R.D_B)
    assert set(ev.season) == set(R.D_B)


def test_part_b_same_day_trade_uses_tip_time():
    """Player is ATL's absentee-candidate, but earlier the SAME DAY (earlier tip) he played for BOS: current team is BOS at the later tip."""
    games, box, rep = toy_world()
    g11_tip = int(games.tip.iloc[11])
    extra = pd.DataFrame([{"game_id": 7777, "yr": 2023, "d": dn(11), "aid": 1, "name": "Sam Star", "team": "BOS", "played": True,
                           "minutes": 20.0, "pts": 8.0},
                          {"game_id": 7777, "yr": 2023, "d": dn(11), "aid": 12, "name": "Zed Zed", "team": "BOS", "played": True,
                           "minutes": 20.0, "pts": 8.0},
                          {"game_id": 7777, "yr": 2023, "d": dn(11), "aid": 13, "name": "Yan Yan", "team": "DAL", "played": True,
                           "minutes": 20.0, "pts": 8.0}])
    box2 = pd.concat([box, extra], ignore_index=True)
    earlier = pd.DataFrame({"game_id": [7777], "season": "2022-23", "date": [BASE + pd.Timedelta(days=11)], "tip": [g11_tip - 20000],
                            "away": "DAL", "home": "BOS"})
    G = pd.concat([games, earlier], ignore_index=True)
    r = part_b_row(R.build_part_b(G, box2, rep), 9011, "ATL")
    assert r.S_box_M20 == 0.0 and r.S_report_M20 == 0.0            # Star is already a BOS player by tip time
    # a LATER same-day tip must not be visible
    later = earlier.assign(tip=[g11_tip + 20000])
    G2 = pd.concat([games, later], ignore_index=True)
    r2 = part_b_row(R.build_part_b(G2, box2, rep), 9011, "ATL")
    assert r2.S_box_M20 == pytest.approx(20.0)


def test_part_b_report_flavor_nan_when_season_has_no_reports():
    games, box, rep = toy_world()
    F = R.build_part_b(games, box, E2.Reports(created={}, content={}))
    assert F.S_report_M20.isna().all() and (F.S_box_M20 >= 0).all()
    F2 = R.build_part_b(games, box, rep)                       # season has reports (even if a game lacks one) -> 0, not NaN
    assert F2.S_report_M20.notna().all()
