import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
from check_lib import ew, ew_ratio, blend, shrink, settle, play_filter, WIN


def test_ew_hand():
    # h=1: weights 1, .5 ; recent-first [10, 4] -> (10 + 2)/1.5 = 8
    assert abs(ew([10, 4], 1) - 8.0) < 1e-12
    # h=2: weights 1, 2**-.5, .5
    w = np.array([1, 2 ** -0.5, 0.5])
    assert abs(ew([3, 6, 9], 2) - (w * [3, 6, 9]).sum() / w.sum()) < 1e-12
    assert ew([7], 8) == 7


def test_ew_ratio():
    # h=1: weights 1,.5 ; (1*6+.5*0)/(1*3+.5*3)=6/4.5
    assert abs(ew_ratio([6, 0], [3, 3], 1) - 6 / 4.5) < 1e-12


def test_prior_and_blend():
    assert abs(shrink(30.0, 21.0) - 27.0) < 1e-12      # 30 + (21-30)/3
    assert abs(blend(20.0, 4, 26.0, 4) - 23.0) < 1e-12  # 4/8*20 + 4/8*26
    assert blend(np.nan, 0, 24.0, 4) == 24.0           # n=0 -> prior only
    assert abs(blend(10.0, 2, 22.0, 2) - 16.0) < 1e-12


def test_push_rule():
    assert settle(44, 44.0, "O") == (0.0, "P")
    assert settle(44, 44.0, "U") == (0.0, "P")
    assert settle(45, 44.5, "O") == (WIN, "W")
    assert settle(45, 44.5, "U") == (-1.0, "L")
    assert settle(40, 44.5, "U")[1] == "W"
    assert abs(WIN - 0.909090909) < 1e-8


def test_play_filter():
    df = pd.DataFrame({
        "play_type": ["pass", "run", "pass", "run", "punt", "pass", "run", "pass"],
        "qb_kneel": [0, 0, 0, 1, 0, 0, 0, 0],
        "qb_spike": [0, 0, 1, 0, 0, 0, 0, 0],
        "epa": [0.1, 0.2, 0.3, 0.4, 0.5, np.nan, 0.7, 0.8],
        "posteam": ["A", "A", "A", "A", "A", "A", None, "B"]})
    out = play_filter(df)
    assert list(out.epa) == [0.1, 0.2, 0.8]


def test_no_lookahead_toy():
    # EW history must exclude the current game: a toy team table
    from check_lib import Model
    m = Model.__new__(Model)
    tg = pd.DataFrame({"team": ["X"] * 3, "season": [2020] * 3,
                       "kick": pd.to_datetime(["2020-09-13", "2020-09-20", "2020-09-27"], utc=True),
                       "pf": [10., 20., 99.], "pa": [1., 2., 3.], "scored": True})
    m.by_team = {"X": tg}
    m.Lprev = {2019: 22.0}
    O, D = m.pts_rating("X", pd.Timestamp("2020-09-27", tz="UTC"), 2020, 1, 1)
    # history = [20, 10] recent-first, h=1 -> (20+5)/1.5 ; prior=22 (no 2019 games); n=2,k=1
    cur = 25 / 1.5
    assert abs(O - (2 / 3 * cur + 1 / 3 * 22.0)) < 1e-9


def test_ridge_hand():
    from check_ares import ridge_fit
    Z = np.array([[-1.0], [0.0], [1.0]]); y = np.array([1.0, 2.0, 6.0])
    # y-ybar=[-2,-1,3]; Zc.T@(y-ybar)=5; Zc.T@Zc=2; lam=2 -> b=1.25; ybar=3
    a, b = ridge_fit(Z, y, 2.0)
    assert abs(b[0] - 1.25) < 1e-12 and abs(a - 3.0) < 1e-12
    a, b = ridge_fit(Z, y, 0.0)
    assert abs(b[0] - 2.5) < 1e-12


def test_b2_bounds():
    # 10 <= wind < 20 for B2, >= 20 for B3
    for v, b2, b3 in ((9.99, 0, 0), (10.0, 1, 0), (19.99, 1, 0), (20.0, 0, 1)):
        assert (10 <= v < 20) == bool(b2) and (v >= 20) == bool(b3)
