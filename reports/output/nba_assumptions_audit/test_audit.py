"""Tests for the #63 assumptions audit: reproduction of #59's E1 MAEs, bootstrap determinism, time-zone / road-trip / rest logic on a toy
schedule, roster-continuity math on a toy example, possession-formula weights."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_lib as A  # noqa: E402


@pytest.fixture(scope="module")
def D():
    return A.load_e1()


def test_reproduction(D):
    rep = A.reproduction(D)
    assert abs(rep["test_total_mae"] - 14.79634) < 5e-6, rep
    assert abs(rep["val_total_mae"] - 14.836) < 5e-4, rep
    assert rep["test_n_games"] == 4923 and rep["val_n_games"] == 3369
    assert rep["ok"]


def test_game_table_sign_convention(D):
    G = A.game_table(D)
    assert len(G) == 3369 + 4923
    # r = actual - E1; #59's bias (E1 - actual) over the test seasons is +0.8120 -> mean residual -0.8120
    t = G[G.season.isin(A.TEST_SEASONS)]
    assert abs(t.r_tot.mean() + 0.8119510403644707) < 1e-9
    assert np.allclose(G.r_tot, G.r_h + G.r_a)


def test_bootstrap_deterministic_and_correct():
    rng = np.random.default_rng(1)
    v = rng.normal(2.0, 5.0, 300)
    a = A.boot_ci(v)
    b = A.boot_ci(v)
    assert all(np.array_equal(x, y) for x, y in zip(a, b))
    mean, lo, hi, se = (float(x[0]) for x in a)
    assert mean == pytest.approx(v.mean())
    assert lo < mean < hi
    assert se == pytest.approx(5.0 / np.sqrt(300), rel=0.15)
    # a different seed changes the interval; same seed with other rows stacked does not change the first row's resamples
    c = A.boot_ci(v, seed=1)
    assert not np.array_equal(a[1], c[1])
    two = A.boot_ci(np.vstack([v, v * 0 + 1]))
    assert two[1][0] == a[1][0]


def toy_sched():
    # team LAL (Pacific): H 1/1 vs GS, A 1/3 @NY (east), A 1/4 @BOS, A 1/5 @DEN? (rest), H 1/6 vs GS, A 1/7 @UTAH ; season 2022-23
    rows = [(1, "2022-23", "2023-01-01", "LAL", "GS", True), (2, "2022-23", "2023-01-03", "LAL", "NY", False),
            (3, "2022-23", "2023-01-04", "LAL", "BOS", False), (4, "2022-23", "2023-01-05", "LAL", "DEN", False),
            (5, "2022-23", "2023-01-09", "LAL", "GS", True), (6, "2022-23", "2023-01-10", "LAL", "UTAH", False)]
    d = pd.DataFrame(rows, columns=["game_id", "season", "date", "team", "opp", "home"])
    d["date"] = pd.to_datetime(d.date)
    d["pts"], d["opp_pts"] = 100, 90
    return d


def test_road_trip_and_timezone_logic():
    s = A.add_schedule_vars(toy_sched())
    s = s.set_index("game_id")
    assert list(s.trip_game) == [0, 1, 2, 3, 0, 1]
    # venue zones: LAL home -8, NY -5, BOS -5, DEN -7, home -8, UTAH -7
    assert list(s.loc_tz) == [-8, -5, -5, -7, -8, -7]
    sh = s.tz_shift
    assert np.isnan(sh.loc[1])                       # first game: no previous
    assert sh.loc[2] == 3                            # Pacific -> NY: +3 = moved east 3 zones
    assert sh.loc[3] == 0 and sh.loc[4] == -2        # BOS->DEN: moved west 2
    assert sh.loc[5] == -1 and sh.loc[6] == 1        # DEN->LAL home: -1 ; home -> UTAH: +1
    # rest flags: 3-in-4 : games 2,3 (1/1,1/3,1/4 -> 1/4 - 1/1 = 3 days) ; 4-in-6: 1/5 - 1/1 = 4 days <= 5 with 3 previous
    assert bool(s.three_in_four.loc[3]) and not bool(s.three_in_four.loc[2]) and not bool(s.three_in_four.loc[5])
    assert bool(s.four_in_six.loc[4]) and not bool(s.four_in_six.loc[3]) and not bool(s.four_in_six.loc[6])


def test_phoenix_mountain_and_neutral_sites():
    assert A.TZ["PHX"] == -7 and A.TZ["DEN"] == -7 and A.TZ["UTAH"] == -7
    d = pd.DataFrame({"game_id": [A.CUP_FINALS.copy().pop(), 5], "season": ["2023-24", "2023-24"],
                      "date": pd.to_datetime(["2023-12-09", "2023-12-10"]), "team": ["LAL", "LAL"], "opp": ["IND", "GS"],
                      "home": [True, True]})
    tz, bub = A.location_tz(d)
    assert tz[0] == A.CUP_TZ and tz[1] == -8 and not bub.any()
    b = pd.DataFrame({"game_id": [9], "season": ["2019-20"], "date": pd.to_datetime(["2020-08-01"]), "team": ["LAL"], "opp": ["DEN"], "home": [True]})
    tz, bub = A.location_tz(b)
    assert bub[0] and tz[0] == A.BUBBLE_TZ


def test_roster_continuity_math():
    prev = {"A": 100.0, "B": 100.0, "C": 200.0}
    assert A.continuity(prev, {"A", "C"}) == pytest.approx(0.75)
    assert A.continuity(prev, set()) == 0.0
    assert A.continuity(prev, {"A", "B", "C", "Z"}) == 1.0
    # per game: players known only from strictly earlier dates
    pb_prev = pd.DataFrame({"athlete_id": ["A", "B", "C"], "minutes": [100.0, 100.0, 200.0]})
    pb_cur = pd.DataFrame({"athlete_id": ["A", "Z", "C", "A", "B"], "game_id": [1, 1, 2, 2, 3],
                           "date": pd.to_datetime(["2022-10-20", "2022-10-20", "2022-10-22", "2022-10-22", "2022-10-24"]),
                           "minutes": [30.0, 20.0, 25.0, 0.0, 10.0]})
    ser = A.continuity_by_game(pb_prev, pb_cur)
    assert np.isnan(ser.loc[1])                                   # first game: no earlier roster
    assert ser.loc[2] == pytest.approx(100 / 400)                 # roster {A, Z} -> A only
    assert ser.loc[3] == pytest.approx(300 / 400)                 # roster {A, Z, C}; A with 0 minutes in game 2 doesn't matter (already in); B not yet


def test_possession_weights():
    assert A.poss_w(100, 10, 15, 20, 0.44) == pytest.approx(0.96 * (100 - 10 + 15 + 8.8))
    assert A.poss_w(100, 10, 15, 20, 0.40) < A.poss_w(100, 10, 15, 20, 0.475)


def test_slicer_points_recoverable():
    n = 400
    rng = np.random.default_rng(3)
    G = pd.DataFrame({"r_tot": rng.normal(0, 18, n), "r_margin": rng.normal(0, 12, n), "r_h": rng.normal(0, 9, n), "r_a": rng.normal(0, 9, n)})
    G.loc[:99, "r_tot"] += 10                                    # a planted +10 bias in the first quarter
    T = pd.DataFrame({"r": rng.normal(0, 9, 2 * n)})
    sl = A.Slicer(G, T)
    row = sl.game("X", "planted", np.arange(n) < 100)
    assert row["n"] == 100 and row["share"] == 0.25
    assert row["points_recoverable_proxy"] == pytest.approx(abs(row["mean_resid"]) * 0.25)
    assert row["sig_95"] and row["ci_lo"] > 0
    row2 = sl.game("Y", "null", np.arange(n) >= 100)
    assert row2["mean_resid"] < row["mean_resid"]
