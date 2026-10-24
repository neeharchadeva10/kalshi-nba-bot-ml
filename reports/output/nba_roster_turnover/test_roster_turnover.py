import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

import lib


# ---------- continuity math on a tiny fake roster
def test_continuity_math():
    prev = pd.DataFrame({"athlete_id": [1, 2, 3, 4, 1], "minutes": [100.0, 50.0, 30.0, 20.0, 100.0]})
    # players 1 and 3 are still around; player 1 appears in two rows (e.g. two seasons' worth of rows here)
    r = lib.continuity_for_team(prev, {1, 3, 99})
    assert r["prev_minutes"] == 300.0 and r["retained_minutes"] == 230.0
    assert r["continuity"] == pytest.approx(230 / 300)


def test_traded_player_minutes_belong_to_the_team_they_played_for():
    box = pd.DataFrame({"team_id": [1, 1, 2], "athlete_id": [7, 8, 7], "minutes": [40.0, 10.0, 60.0]})
    t1 = lib.continuity_for_team(box[box.team_id == 1][["athlete_id", "minutes"]], {7})
    t2 = lib.continuity_for_team(box[box.team_id == 2][["athlete_id", "minutes"]], set())
    assert t1["continuity"] == pytest.approx(0.8) and t2["continuity"] == 0.0


def test_nan_minutes_count_as_zero():
    prev = pd.DataFrame({"athlete_id": [1, 2], "minutes": [np.nan, 10.0]})
    assert lib.continuity_for_team(prev, {1})["continuity"] == 0.0


def _toy_season():
    # team 1 plays games 1..5; roster of game 1-3 is {1,2,3}; player 9 only debuts in game 4
    tg = pd.DataFrame({"game_id": [5, 1, 2, 3, 4], "tip": [500, 100, 200, 300, 400]})
    rows = pd.DataFrame({"game_id": [1, 1, 2, 3, 4, 5], "team_id": 1, "athlete_id": [1, 2, 3, 1, 9, 9],
                         "minutes": [10.0] * 6})
    prev = pd.DataFrame({"team_id": 1, "athlete_id": [1, 2, 9, 5], "minutes": [100.0, 100.0, 100.0, 100.0]})
    return tg.assign(team_id=1, team_abbreviation="AAA", date="2022-10-19"), rows, prev


def test_first3_exclusion_and_no_lookahead():
    tg, rows, prev = _toy_season()
    cont, first3, f3tip = lib.build_continuity(2023, prev, rows, tg)
    assert first3[1] == {1, 2, 3}                       # earliest three by tip, not by id order given
    assert f3tip[1] == 300
    # player 9 debuts in game 4 -> must NOT count (look-ahead); players 1 and 2 count
    assert cont.continuity.iloc[0] == pytest.approx(0.5)
    with pytest.raises(AssertionError):
        lib.assert_no_lookahead(300, [300, 100])        # game 3 itself is not after the first 3
    lib.assert_no_lookahead(301, [300, 100])


def test_build_games_drops_first3_of_either_team():
    cont = pd.DataFrame({"team_id": [1, 2], "continuity": [0.2, 0.9]})
    m = pd.DataFrame({"game_id": [10, 11, 12], "date": ["2022-10-20"] * 3, "tip": [250, 350, 450], "home": "A",
                      "away": "B", "home_id": 1, "away_id": 2, "OU": 200.0, "Spread": 1.0, "ML_Home": -110,
                      "ML_Away": -110, "Points": 210, "Win_Margin": 2})
    g = lib.build_games(2023, cont, {1: 300, 2: 400}, m, "2022-10-18")
    assert list(g.excluded_first3) == [True, True, False]   # 350 is after team 1's 3 games but not team 2's


# ---------- -110 settlement
def test_win_push_loss_at_minus_110():
    x = lib.nets(np.array([1, 1, -1, 0]), np.array([1, -1, -1, 1]))   # win, loss, win, push(dropped)
    assert len(x) == 3
    assert list(np.round(x, 4)) == [0.9091, -1.0, 0.9091]
    assert lib.WIN_NET == pytest.approx(0.909090909)


def test_sign_outcomes_cover_and_push():
    g = pd.DataFrame({"Win_Margin": [5, 3, -2], "Spread": [3.0, 3.0, 1.0], "Points": [220, 210, 215],
                      "OU": [215.0, 210.0, 220.0]})
    s, t = lib.sign_outcomes(g)
    assert list(s) == [1, 0, -1] and list(t) == [1, 0, -1]


# ---------- version grid
def test_grid_is_32_and_unique():
    grid = lib.version_grid()
    assert len(grid) == 32 and len({lib.vname(v) for v in grid}) == 32


# ---------- selection
def test_lb_selection():
    df = pd.DataFrame({"version": ["a", "b", "c", "d"], "n": [100, 39, 200, 100], "lb": [-0.1, 0.5, -0.05, -0.05]})
    assert lib.select_best(df).version == "c"               # b excluded (n < 40); c ties d -> first in grid order
    assert lib.select_best(df.assign(n=10)) is None
    assert lib.stats(np.array([lib.WIN_NET, -1.0] * 50))["lb"] == pytest.approx(
        np.mean([lib.WIN_NET, -1.0] * 50) - 1.96 * np.std([lib.WIN_NET, -1.0] * 50, ddof=1) / 10)


def _fake_games(n=400, seed=1):
    rng = np.random.default_rng(seed)
    low = rng.random((n, 2)) < 0.3
    return pd.DataFrame({
        "low20_home": low[:, 0], "low20_away": low[:, 1], "low33_home": low[:, 0], "low33_away": low[:, 1],
        "excluded_first3": False, "day_idx": rng.integers(0, 40, n), "Win_Margin": rng.integers(-20, 20, n),
        "Spread": rng.integers(-10, 10, n) + 0.5, "Points": rng.integers(200, 240, n),
        "OU": rng.integers(205, 235, n) + 0.5})


def test_version_sets_and_sides():
    g = _fake_games()
    v = {"market": "spread", "dir": "fade", "thr": 20, "win": 14, "inv": "low_in_game"}
    idx, side = lib.version_sides(g, v)
    lh, la = g.low20_home.values[idx], g.low20_away.values[idx]
    assert (lh ^ la).all() and (g.day_idx.values[idx] < 14).all()
    assert (side == np.where(lh, -1, 1)).all()              # fade the low team: low home -> away covers (-1)
    vb = dict(v, dir="back")
    assert (lib.version_sides(g, vb)[1] == -side).all()
    vt = {"market": "total", "dir": "under", "thr": 20, "win": 30, "inv": "low_in_game"}
    idx2, side2 = lib.version_sides(g, vt)
    assert (g.low20_home.values[idx2] | g.low20_away.values[idx2]).all() and (side2 == -1).all()
    idx3, _ = lib.version_sides(g, dict(vt, inv="opp_not_low"))
    assert (g.low20_home.values[idx3] ^ g.low20_away.values[idx3]).all()


def test_rank_flags_and_terciles():
    c = {i: i / 30 for i in range(30)}
    assert sum(lib.rank_flags(c, 0.20).values()) == 6 and sum(lib.rank_flags(c, 0.33).values()) == 10
    t = lib.tercile_of(c)
    assert [list(t.values()).count(k) for k in (0, 1, 2)] == [10, 10, 10] and t[0] == 0 and t[29] == 2


# ---------- luck test
def test_luck_test_deterministic_and_matches_rerun():
    g = _fake_games()
    b1, r1 = lib.luck_test(g, reps=50, seed=lib.SEED)
    b2, r2 = lib.luck_test(g, reps=50, seed=lib.SEED)
    assert np.array_equal(b1, b2) and r1 == r2
    b3, _ = lib.luck_test(g, reps=50, seed=lib.SEED + 1)
    assert not np.array_equal(b1, b3)
    assert 0.0 <= lib.luck_share(b1, r1) <= 1.0


def test_luck_vectorised_matches_direct_computation():
    g = _fake_games(300)
    grid = lib.version_grid()
    rng = np.random.default_rng(lib.SEED)
    signs = rng.integers(0, 2, size=(3, len(g))) * 2 - 1
    best, _ = lib.luck_test(g, reps=3, seed=lib.SEED, grid=grid)
    s_ref, t_ref = lib.sign_outcomes(g)
    for rep in range(3):
        lbs = []
        for v in grid:
            idx, _s = lib.version_sides(g, v)
            ref = (s_ref if v["market"] == "spread" else t_ref)[idx]
            st = lib.stats(lib.nets(ref, signs[rep][idx]))
            if st["n"] >= lib.MIN_N:
                lbs.append(st["lb"])
        assert max(lbs) == pytest.approx(best[rep])


def test_luck_test_detects_a_planted_edge():
    g = _fake_games(1500, seed=5)
    # plant: every game with exactly one low-20 team and day<14 goes UNDER (total well below the line)
    one = (g.low20_home ^ g.low20_away) & (g.day_idx < 14)
    g["Points"] = g.Points.astype(float); g.loc[one, "Points"] = g.loc[one, "OU"] - 8
    best, real = lib.luck_test(g, reps=100, seed=lib.SEED)
    assert lib.luck_share(best, real) == 0.0 and real > 0.5


# ---------- Kalshi fee rounding
def test_kalshi_fee_rounding():
    # 0.07 * 0.5 * 0.5 = 0.0175 per contract; one contract rounds UP to a micro-dollar
    assert lib.kalshi_fee(0.5, 1) == pytest.approx(0.0175)
    assert lib.kalshi_fee(0.33, 1) == pytest.approx(0.015477)      # 0.07*0.33*0.67 = 0.015477 exactly
    assert lib.kalshi_fee(0.123, 1) == pytest.approx(0.007551)     # raw 0.0075507 rounded UP
    assert lib.kalshi_fee(0.123, 1) >= 0.07 * 0.123 * 0.877


def test_kalshi_net_settlement():
    # over: buy yes at ask 0.60, wins -> 1 - 0.60 - fee
    fee = lib.kalshi_fee(0.60, 1)
    assert lib.kalshi_net("over", 0.55, 0.60, True) == pytest.approx(0.40 - fee)
    # under: buy no at 1 - bid = 0.45; loses if yes
    fee2 = lib.kalshi_fee(0.45, 1)
    assert lib.kalshi_net("under", 0.55, 0.60, True) == pytest.approx(-0.45 - fee2)


def test_main_strike_closest_to_half():
    s = pd.DataFrame({"strike": [220.5, 225.5, 230.5], "bid": [0.8, 0.5, 0.2], "ask": [0.84, 0.54, 0.24],
                      "mid": [0.82, 0.52, 0.22]})
    assert lib.main_strike(s).strike == 225.5


# ---------- boot / coin flip
def test_bootstrap_and_coin_flip_deterministic():
    x = np.array([lib.WIN_NET] * 60 + [-1.0] * 40)
    assert lib.boot_lb(x) == lib.boot_lb(x)
    g = _fake_games()
    v = lib.version_grid()[16]
    assert lib.coin_flip(g, v, 0.0, reps=100) == lib.coin_flip(g, v, 0.0, reps=100)


# ---------- unseen guard
def test_run_unseen_refuses_without_audit_ok():
    assert not (lib.HERE / "AUDIT_OK").exists()
    r = subprocess.run([sys.executable, str(lib.HERE / "run_unseen.py")], capture_output=True, text=True,
                       cwd=str(lib.HERE))
    assert r.returncode != 0 and "REFUSED" in (r.stderr + r.stdout)


# ---------- odds parsing / matching
def test_match_odds_exact_then_shifted_and_name_map():
    assert lib.NAME_TO_ABBR["LA Clippers"] == lib.NAME_TO_ABBR["Los Angeles Clippers"] == "LAC"
    tg = pd.DataFrame({"game_id": [1, 1, 2, 2], "date": ["2023-01-05", "2023-01-05", "2023-01-07", "2023-01-07"],
                       "tip": [10, 10, 20, 20], "team_abbreviation": ["BOS", "NY", "BOS", "NY"],
                       "team_home_away": ["home", "away", "away", "home"], "team_id": [2, 18, 2, 18],
                       "opponent_team_id": [18, 2, 18, 2]})
    odds = pd.DataFrame({"Date": ["2023-01-05", "2023-01-06", "2023-02-01"], "home": ["BOS", "NY", "BOS"],
                         "away": ["NY", "BOS", "NY"], "OU": 200.0, "Spread": 1.0, "ML_Home": -110, "ML_Away": -110,
                         "Points": 1, "Win_Margin": 1})
    m, exact, shifted, un = lib.match_odds_to_hoopr(odds, tg)
    assert (exact, shifted, len(un), len(m)) == (1, 1, 1, 2)
