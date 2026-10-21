"""Unit tests for research log #42 (toy data only; no real outcomes, no network)."""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grid_lib as gl  # noqa: E402
import grid_bets as gb  # noqa: E402
import grid_run as gr  # noqa: E402
from grid_lib import lib, r2, tn  # noqa: E402

TEAMS = ["AAA", "BBB", "CCC", "DDD"]
OFF = dict(efgnum=55.0, fga=100.0, tov=12.0, tovden=112.0, oreb=10.0, orebden=40.0, ftm=15.0)


def rows(date, home, away, hpts, apts, hposs=100.0, aposs=100.0, hff=None, aff=None, gid=1, stype=2):
    """Two team-game rows (home and away) of the same game, with every column team_games_g produces."""
    hff = {**OFF, **(hff or {})}
    aff = {**OFF, **(aff or {})}
    out = []
    for team, opp, ven, pts, opts, poss, oposs, f, of in (
            (home, away, "home", hpts, apts, hposs, aposs, hff, aff), (away, home, "away", apts, hpts, aposs, hposs, aff, hff)):
        r = dict(game_id=gid, date=pd.Timestamp(date), team=team, opp=opp, pts=float(pts), opp_pts=float(opts),
                 poss=float(poss), opp_poss=float(oposs), tip=0, season_type=stype, venue=ven,
                 pace=(poss + oposs) / 2.0)
        r.update(f)
        r.update({"o_" + k: v for k, v in of.items()})
        out.append(r)
    return out


def tgf(rs):
    return pd.DataFrame(rs).sort_values("date", kind="stable").reset_index(drop=True)


def toy_tg(seed, n_days, start="2025-11-01", teams=TEAMS, gid0=1):
    rng = np.random.default_rng(seed)
    rs, gid = [], gid0
    for d in range(n_days):
        date = pd.Timestamp(start) + pd.Timedelta(days=d)
        perm = rng.permutation(len(teams))
        for i in range(0, len(teams), 2):
            if rng.random() < 0.8:
                h, a = teams[perm[i]], teams[perm[i + 1]]
                ff = dict(efgnum=float(rng.integers(40, 60)), fga=float(rng.integers(80, 95)),
                          tov=float(rng.integers(8, 18)))
                ff["tovden"] = ff["fga"] + 10 + ff["tov"]
                ff["oreb"], ff["orebden"], ff["ftm"] = float(rng.integers(6, 14)), float(rng.integers(36, 46)), float(rng.integers(10, 25))
                ff2 = dict(efgnum=float(rng.integers(40, 60)), fga=float(rng.integers(80, 95)), tov=float(rng.integers(8, 18)))
                ff2["tovden"] = ff2["fga"] + 10 + ff2["tov"]
                ff2["oreb"], ff2["orebden"], ff2["ftm"] = float(rng.integers(6, 14)), float(rng.integers(36, 46)), float(rng.integers(10, 25))
                rs += rows(date, h, a, rng.integers(95, 130), rng.integers(95, 130), rng.integers(92, 106), rng.integers(92, 106),
                           ff, ff2, gid)
                gid += 1
    return tgf(rs)


@pytest.fixture(scope="module")
def toy():
    prev = toy_tg(1, 50, "2024-11-01")
    cur = toy_tg(2, 40, "2025-11-01", gid0=1000)
    coef, _ = gl.fit_four_factor(prev)
    return gl.Season(prev, cur, coef), prev, cur, coef


def toy_games(cur, days_after=0, n=12, seed=3):
    rng = np.random.default_rng(seed)
    dts = sorted(cur.date.unique())[5:]
    out = []
    for i in range(n):
        d = pd.Timestamp(dts[int(rng.integers(len(dts)))]) + pd.Timedelta(days=days_after)
        a, h = rng.choice(TEAMS, 2, replace=False)
        tip = int(pd.Timestamp(d.strftime("%Y-%m-%d") + " 19:30", tz="America/New_York").timestamp())
        out.append(dict(event=f"EV{i}", away=a, home=h, tip=tip, T=tip))
    return pd.DataFrame(out)


# ------------------------------------------------------------------ the grid itself
def test_grid_size_and_index():
    assert gl.SHAPE == (7, 4, 10, 4, 2, 3, 2, 2)
    assert gl.N_FORMULAS == 26880
    assert gl.N_FORMULAS * len(gb.THRESHOLDS) == 134400
    assert gb.THRESHOLDS == (0, 2, 4, 6, 8)
    names = {d: gl.DEFAULTS[d] for d in gl.DIM_NAMES}
    sp = gl.spec_from_names(names)
    assert sp == gl.DEFAULT_IDX and gl.n_nondefault(sp) == 0
    for i in (0, 1, 777, 26879):
        assert gl.flat_from_spec(gl.spec_from_flat(i)) == i
    assert gl.spec_from_flat(26879) == (6, 3, 9, 3, 1, 2, 1, 1)
    assert gl.n_nondefault((0, 1, 0, 2, 1, 0, 0, 0)) == 1
    nd = gb.nondefault_counts()
    assert len(nd) == 26880 and nd[gl.flat_from_spec(gl.DEFAULT_IDX)] == 0 and nd.max() == 8


# ------------------------------------------------------------------ A x B, hand-computed (every option)
O_A, D_A, P_A, O_B, D_B, P_B, LO, LP = 1.10, 1.00, 100.0, 1.00, 1.20, 96.0, 1.05, 98.0
FEAT = {k: np.array([v]) for k, v in dict(Oa=O_A, Da=D_A, Pa=P_A, Oh=O_B, Dh=D_B, Ph=P_B, Lo=LO, Lp=LP).items()}
# team A (offence 1.10, pace 100) faces defence 1.20 (pace 96); team B (offence 1.00) faces defence 1.00 (pace 100)
RATE = {"w1.0": (1.10, 1.00), "w0.75": (1.125, 1.00), "w0.5": (1.15, 1.00), "w0.25": (1.175, 1.00), "w0.0": (1.20, 1.00),
        "mult": (1.10 * 1.20 / 1.05, 1.00 * 1.00 / 1.05), "add": (1.10 + 1.20 - 1.05, 1.00 + 1.00 - 1.05)}
HAND = {}
for _a, (_ra, _rb) in RATE.items():
    HAND[(_a, "own")] = _ra * 100 + _rb * 96
    HAND[(_a, "avg")] = (_ra + _rb) * 98
    HAND[(_a, "mult")] = (_ra + _rb) * (100 * 96 / 98)
HAND[("w1.0", "termown")] = 110 + 96
HAND[("w0.75", "termown")] = (0.75 * 110 + 0.25 * 115.2) + (0.75 * 96 + 0.25 * 100)
HAND[("w0.5", "termown")] = (55 + 57.6) + (48 + 50)
HAND[("w0.25", "termown")] = (27.5 + 86.4) + (24 + 75)
HAND[("w0.0", "termown")] = 115.2 + 100
HAND[("mult", "termown")] = (110 * 115.2 + 96 * 100) / (1.05 * 98)
HAND[("add", "termown")] = (110 + 115.2 - 1.05 * 98) + (96 + 100 - 1.05 * 98)


@pytest.mark.parametrize("A", gl.A_OPTS)
@pytest.mark.parametrize("B", gl.B_OPTS)
def test_A_B_hand_computed(A, B):
    assert gl.total_proj(A, B, FEAT)[0] == pytest.approx(HAND[(A, B)], abs=1e-9)


def test_A_B_spot_values_literal():
    assert HAND[("w0.75", "own")] == pytest.approx(208.5)       # 112.5 + 96
    assert HAND[("w0.5", "avg")] == pytest.approx(210.7)        # 98 x 2.15
    assert HAND[("w0.5", "termown")] == pytest.approx(210.6)    # 112.6 + 98
    assert HAND[("add", "termown")] == pytest.approx(215.4)


def test_pdf_analogues_of_39_formulas():
    """F1..F5 of #39 are the grid cells (A, B) listed in grid_run.F39."""
    ra, rb = (O_A, D_A, P_A), (O_B, D_B, P_B)
    for fid, (A, B) in gr.F39.items():
        assert gl.total_proj(A, B, FEAT)[0] == pytest.approx(tn.formula_total(fid, ra, rb, LO, LP), abs=1e-9)


# ------------------------------------------------------------------ D: prior weight k
def test_blend_k_options():
    prior, cur = np.array([[1.0, 1.0, 100.0]]), np.array([[1.2, 1.2, 100.0]])
    n = np.array([10.0])
    assert gl.blend_v(prior, cur, n, 0)[0, 0] == pytest.approx(1.2)
    assert gl.blend_v(prior, cur, n, 10)[0, 0] == pytest.approx(1.1)
    assert gl.blend_v(prior, cur, n, 20)[0, 0] == pytest.approx((20 * 1.0 + 10 * 1.2) / 30)
    assert gl.blend_v(prior, cur, n, 40)[0, 0] == pytest.approx(1.04)
    for k in gl.D_OPTS:      # n = 0 -> prior, also at k = 0
        assert gl.blend_v(prior, cur, np.array([0.0]), k)[0, 0] == 1.0
    assert gl.blend_v(prior, cur, n, 20)[0, 0] == pytest.approx(r2.blend(1.0, 1.2, 10, 20))


# ------------------------------------------------------------------ hand-built CurTable: E shift, G split
def mk_tab(n_a=10.0, n_sa=0.0, n_h=0.0, n_sh=0.0, ro=1.02, rp=0.98):
    pr_a, pr_h = [1.0, 1.1, 100.0], [1.2, 1.0, 98.0]
    return gl.CurTable(1.1, 99.0, prior_a=[pr_a], prior_h=[pr_h], r_o=[ro], r_p=[rp],
                       cur_a=[[1.2, 1.3, 101.0]], n_a=[n_a], cur_sa=[[1.4, 1.5, 103.0]], n_sa=[n_sa],
                       cur_h=[[1.0, 0.9, 99.0]], n_h=[n_h], cur_sh=[[0.8, 0.7, 95.0]], n_sh=[n_sh])


def test_shift_E_off_and_on():
    t = mk_tab(n_a=0.0)               # no current games: pure prior, so shift is visible directly
    on = gl.features(t, 20, True, False)
    off = gl.features(t, 20, False, False)
    assert on["Oa"][0] == pytest.approx(1.0 * 1.02) and on["Da"][0] == pytest.approx(1.1 * 1.02)
    assert on["Pa"][0] == pytest.approx(100.0 * 0.98) and on["Ph"][0] == pytest.approx(98.0 * 0.98)
    assert on["Lo"][0] == pytest.approx(1.1 * 1.02) and on["Lp"][0] == pytest.approx(99.0 * 0.98)
    assert off["Oa"][0] == 1.0 and off["Pa"][0] == 100.0 and off["Lo"][0] == 1.1 and off["Lp"][0] == 99.0


def test_shrunk_ratio_hand():
    assert r2.shrunk_ratio(1.10, 1.00, 100) == pytest.approx(1.0 + 0.10 * 100 / 200)
    assert r2.shrunk_ratio(1.10, 1.00, 0) == 1.0


def test_split_G_blend():
    t = mk_tab(n_a=10.0, n_sa=10.0)
    base = gl.features(t, 20, False, False)
    sp = gl.features(t, 20, False, True)
    o_all = (20 * 1.0 + 10 * 1.2) / 30                  # overall blended offence
    o_split = (20 * 1.0 + 10 * 1.4) / 30                # venue-only blended with the prior at k
    assert base["Oa"][0] == pytest.approx(o_all)
    w = 10 / (10 + 10)                                  # n_split / (n_split + 10) = 0.5
    assert sp["Oa"][0] == pytest.approx(w * o_split + (1 - w) * o_all)
    # n_split = 0 -> overall unchanged; n_split = 30 -> w = 0.75
    t0 = mk_tab(n_a=10.0, n_sa=0.0)
    assert gl.features(t0, 20, False, True)["Oa"][0] == pytest.approx(o_all)
    t30 = mk_tab(n_a=10.0, n_sa=30.0)
    o30 = (20 * 1.0 + 30 * 1.4) / 50
    assert gl.features(t30, 20, False, True)["Oa"][0] == pytest.approx(0.75 * o30 + 0.25 * o_all)
    # the home team uses its home-only rating
    th = mk_tab(n_a=0.0, n_h=10.0, n_sh=10.0)
    o_h_all, o_h_sp = (20 * 1.2 + 10 * 1.0) / 30, (20 * 1.2 + 10 * 0.8) / 30
    assert gl.features(th, 20, False, True)["Oh"][0] == pytest.approx(0.5 * o_h_sp + 0.5 * o_h_all)


# ------------------------------------------------------------------ H shrink and F rest inside build_block (hand)
def test_shrink_H_and_rest_F_in_block():
    t = mk_tab(n_a=0.0)               # pure priors, shift off below
    rest = gl.Rest([0], [1])          # away on a back-to-back, home with one day of rest
    ci = 0
    e_cat = np.zeros((7, 10, 3))
    c_b2b = np.zeros((7, 10))
    b = np.zeros((7, 10))
    a = gl.A_OPTS.index("w0.5")
    c_b2b[a, ci] = -1.2
    e_cat[a, ci] = [-1.8, -0.7, -0.6]
    b[a, ci] = 0.5
    cs = gl.Consts(c_b2b, e_cat, b)
    f = gl.features(t, 20, False, False)
    base = gl.total_proj("w0.5", "avg", f)[0]
    Lt = 2 * 1.1 * 99.0
    blk = gl.build_block(t, rest, cs, ci, 20, False, False, [a], [gl.B_OPTS.index("avg")])[0, 0]      # (3, 2, 1)
    assert blk[0, 0, 0] == pytest.approx(base)                                           # F none, H off
    assert blk[1, 0, 0] == pytest.approx(base - 1.2 * 1)                                 # b2b: one team on a back-to-back
    assert blk[2, 0, 0] == pytest.approx(base + (-1.8) + (-0.7))                         # cat: away cat 0, home cat 1
    assert blk[0, 1, 0] == pytest.approx(Lt + 0.5 * (base - Lt))                         # H on
    assert blk[1, 1, 0] == pytest.approx(Lt + 0.5 * (base - Lt) - 1.2)                   # rest added after the shrink, unscaled
    assert blk[2, 1, 0] == pytest.approx(Lt + 0.5 * (base - Lt) - 2.5)


def test_rest_categories():
    dates = np.array(["2025-11-01", "2025-11-03", "2025-11-04", "2025-11-08"], dtype="datetime64[D]")
    D = lambda s: np.datetime64(s, "D")   # noqa: E731
    assert gl.rest_cat(dates, D("2025-11-01")) == 3          # first game of the season
    assert gl.rest_cat(dates, D("2025-11-02")) == 0          # played yesterday
    assert gl.rest_cat(dates, D("2025-11-03")) == 1          # one day of rest (played 11-01)
    assert gl.rest_cat(dates, D("2025-11-04")) == 0
    assert gl.rest_cat(dates, D("2025-11-06")) == 1          # last game 11-04: gap 2 -> 1 day of rest
    assert gl.rest_cat(dates, D("2025-11-07")) == 2          # gap 3 -> 2 days
    assert gl.rest_cat(dates, D("2025-11-08")) == 3          # gap 4 -> 3 days
    assert gl.rest_cat(dates, D("2025-11-20")) == 3          # long rest
    # agrees with #39's back-to-back flag
    for s in ("2025-11-01", "2025-11-02", "2025-11-04", "2025-11-08", "2025-11-09"):
        assert (gl.rest_cat(dates, D(s)) == 0) == tn.b2b_flag(dates, D(s))
    r = gl.Rest([0, 3, 1], [0, 0, 2])
    assert r.nb2b.tolist() == [2.0, 1.0, 0.0]


def test_fit_cat_recovers_exact_effects():
    rng = np.random.default_rng(0)
    ca, ch = rng.integers(0, 4, 400), rng.integers(0, 4, 400)
    e = np.array([-1.8, -0.7, -0.6, 0.0])
    resid = 0.9 + e[ca] + e[ch]                      # intercept 0.9 is fitted and NOT applied
    got, a0, cnt = gl.fit_cat(resid, ca, ch)
    assert got == pytest.approx([-1.8, -0.7, -0.6], abs=1e-9) and a0 == pytest.approx(0.9, abs=1e-9)
    assert cnt.sum() == 800


def test_fit_shrink_recovers_exact_slope():
    proj = np.linspace(210, 250, 50)
    Lt = 231.0
    actual = 3.0 + Lt + 0.4 * (proj - Lt)
    b, a0 = gl.fit_shrink(proj, actual, Lt)
    assert b == pytest.approx(0.4, abs=1e-12)
    assert a0 == pytest.approx(3.0, abs=1e-9)          # intercept fitted, never applied


def test_fit_c_is_39s_slope_with_intercept():
    x = np.array([0, 1, 2, 0, 1, 2, 0, 2.0])
    c, a, n, nb = tn.fit_c(5.0 - 1.2 * x, x)
    assert c == pytest.approx(-1.2) and a == pytest.approx(5.0)


# ------------------------------------------------------------------ C: rating types (hand-computed)
def season_with_twenty(extra=None):
    """AAA plays BBB 20 times; AAA scores 100 + j on 100 possessions in game j (dates 2025-11-01 + j); BBB scores 100."""
    prev = tgf(sum([rows(f"2024-11-{j + 1:02d}", "AAA", "BBB", 100, 100, gid=j + 1) for j in range(5)], []))
    rs = []
    for j in range(20):
        home, away = ("AAA", "BBB") if j % 2 == 0 else ("BBB", "AAA")
        hp, ap = (100 + j, 100) if j % 2 == 0 else (100, 100 + j)
        rs += rows(pd.Timestamp("2025-11-01") + pd.Timedelta(days=j), home, away, hp, ap, gid=100 + j)
    rs += extra or []
    coef = np.array([0.0, 1.0, 0.0, 0.0, 0.0])
    return gl.Season(prev, tgf(rs), coef)


D_AFTER = np.datetime64("2025-11-21", "D")           # after all 20 games


def test_rating_season_decay_last_hand():
    s = season_with_twenty()
    pts = np.array([100.0 + j for j in range(20)])
    r = s.cur_rating("season", "AAA", D_AFTER)
    assert r[0] == pytest.approx(pts.sum() / 2000) and r[1] == pytest.approx(1.0) and r[2] == pytest.approx(100.0)
    assert r[3] == 20
    for ct, hl in (("decay5", 5), ("decay10", 10), ("decay20", 20)):
        w = [0.5 ** ((19 - j) / hl) for j in range(20)]               # most recent game has age 0
        r = s.cur_rating(ct, "AAA", D_AFTER)
        assert r[0] == pytest.approx(sum(wi * p for wi, p in zip(w, pts)) / (100 * sum(w)))
        assert r[3] == pytest.approx(sum(w))                            # n_eff = sum of weights
    assert gl.HALF_LIVES == {"decay5": 5.0, "decay10": 10.0, "decay20": 20.0}
    for ct, N, want in (("last5", 5, 1.17), ("last10", 10, 1.145), ("last15", 15, 1.12)):
        r = s.cur_rating(ct, "AAA", D_AFTER)
        assert r[0] == pytest.approx(want) and r[3] == N                # mean of the last N games' points / 100
    # fewer games than N: all games count, n_eff = games played
    r = s.cur_rating("last15", "AAA", np.datetime64("2025-11-04", "D"))
    assert r[3] == 3 and r[0] == pytest.approx((100 + 101 + 102) / 300)


def test_decay_weights_are_halving():
    w = 0.5 ** (np.arange(3, -1, -1) / 10)
    assert w[-1] == 1.0 and w[-2] == pytest.approx(0.5 ** 0.1) and w[0] == pytest.approx(0.5 ** 0.3)
    # 10 games ago has weight exactly 1/2 at half-life 10
    assert 0.5 ** (10 / 10) == 0.5


def test_kenpom_equal_league_and_matches_39():
    rs = []
    for j in range(6):
        rs += rows(pd.Timestamp("2025-11-01") + pd.Timedelta(days=j), TEAMS[j % 2], TEAMS[2 + j % 2], 110, 110, gid=j)
    prev = tgf(sum([rows("2024-11-01", "AAA", "CCC", 110, 110, gid=900), rows("2024-11-02", "BBB", "DDD", 110, 110, gid=901)], []))
    s = gl.Season(prev, tgf(rs), np.zeros(5))
    r = s.cur_rating("kenpom", "AAA", np.datetime64("2025-11-10", "D"))
    assert r[0] == pytest.approx(1.1) and r[1] == pytest.approx(1.1) and r[2] == pytest.approx(100.0)
    # on real-looking toy data: identical to tn.kenpom_adjust of the slice
    s2 = season_with_twenty()
    D = np.datetime64("2025-11-10", "D")
    sl = s2.cur[s2.cur.date < pd.Timestamp(D)]
    res, _ = tn.kenpom_adjust(sl)
    r = s2.cur_rating("kenpom", "AAA", D)
    assert (r[0], r[1]) == pytest.approx(res["AAA"]) and r[3] == 9


def test_massey_toy_known_answer():
    # one game, UNWEIGHTED rows, lambda = 10: y_A = 1.2, y_B = 1.0.  By symmetry o_A = dd_B = x, o_B = dd_A = -x,
    # mu = 1.1; minimise 2 (0.1 - 2x)^2 + 20 (2 x^2)  ->  x = 1/120.  So O_A = D_B = 1.1 + 1/120, O_B = D_A = 1.1 - 1/120.
    tg = tgf(rows("2025-11-01", "AAA", "BBB", 120, 100, 100, 100))
    mu, par = gl.massey_fit(tg)
    assert mu == pytest.approx(1.1, abs=1e-9)
    assert mu + par["AAA"][0] == pytest.approx(1.1 + 1 / 120, abs=1e-9) and mu + par["BBB"][1] == pytest.approx(1.1 + 1 / 120, abs=1e-9)
    assert mu + par["BBB"][0] == pytest.approx(1.1 - 1 / 120, abs=1e-9) and mu + par["AAA"][1] == pytest.approx(1.1 - 1 / 120, abs=1e-9)
    # one-step venue update with n games: o_t = sum(ppp - mu - dd_opp) / (n + 10)
    s1 = gl.Season(tgf(rows("2024-11-01", "AAA", "BBB", 100, 100, gid=9)), tg, np.zeros(5))
    r = s1.cur_rating("massey", "AAA", np.datetime64("2025-11-02", "D"), "home")
    assert r[0] == pytest.approx(1.1 + (1.2 - 1.1 - par["BBB"][1]) / 11 )
    # symmetric game: both teams score 1.1 -> every o and dd is 0 (ridge) and O = D = mu = 1.1
    tg = tgf(rows("2025-11-01", "AAA", "BBB", 110, 110, 100, 100))
    mu, par = gl.massey_fit(tg)
    assert mu == pytest.approx(1.1) and all(abs(v) < 1e-9 for t in par.values() for v in t)


def test_massey_normal_equations_hold_and_lambda_is_10():
    assert gl.MASSEY_LAMBDA == 10.0
    tg = toy_tg(5, 12)
    mu, par = gl.massey_fit(tg)
    teams = sorted(par)
    # gradient of  sum (y - mu - o_t - dd_u)^2 + 10 (sum o^2 + sum dd^2)  is zero
    res = (tg.pts / tg.poss).values - mu - tg.team.map(lambda t: par[t][0]).values - tg.opp.map(lambda t: par[t][1]).values
    w = np.ones(len(tg))          # unweighted rows
    assert (w * res).sum() == pytest.approx(0, abs=1e-8)
    for t in teams:
        assert (w * res * (tg.team.values == t)).sum() - 10 * par[t][0] == pytest.approx(0, abs=1e-8)
        assert (w * res * (tg.opp.values == t)).sum() - 10 * par[t][1] == pytest.approx(0, abs=1e-8)


def test_four_factor_fit_recovers_known_coefficients():
    rng = np.random.default_rng(4)
    true = np.array([0.20, 0.90, -0.50, 0.10, 0.15])
    rs = []
    for i in range(40):
        fga = 80.0 + rng.integers(0, 15)
        f = dict(efgnum=float(rng.integers(38, 60)), fga=fga, tov=float(rng.integers(8, 18)), oreb=float(rng.integers(6, 14)),
                 orebden=float(rng.integers(36, 46)), ftm=float(rng.integers(8, 26)))
        f["tovden"] = fga + 8 + f["tov"]
        efg, tov, orb, ftr = f["efgnum"] / fga, f["tov"] / f["tovden"], f["oreb"] / f["orebden"], f["ftm"] / fga
        ortg = true[0] + true[1] * efg + true[2] * tov + true[3] * orb + true[4] * ftr
        rs += rows("2025-11-01", "AAA", "BBB", 100 * ortg, 100, 100, 100, hff=f, gid=i)
    tg = tgf(rs)
    coef, r2_ = gl.fit_four_factor(tg[tg.team == "AAA"])
    assert coef == pytest.approx(true, abs=1e-9) and r2_ == pytest.approx(1.0)
    assert gl.ff_rates([60, 100, 15, 120, 10, 40, 20]) == pytest.approx([0.6, 0.125, 0.25, 0.2])
    assert gl.ff_apply(true, [60, 100, 15, 120, 10, 40, 20]) == pytest.approx(0.2 + 0.9 * 0.6 - 0.5 * 0.125 + 0.1 * 0.25 + 0.15 * 0.2)


def test_four_factor_rating_is_rate_weighted():
    # game 1: eFG num 50 / fga 100; game 2: 60 / 50.  Aggregated eFG = 110/150, NOT the mean of 0.5 and 1.2
    g1 = rows("2025-11-01", "AAA", "BBB", 100, 100, hff=dict(efgnum=50.0, fga=100.0), gid=1)
    g2 = rows("2025-11-02", "AAA", "BBB", 100, 100, hff=dict(efgnum=60.0, fga=50.0), gid=2)
    prev = tgf(rows("2024-11-01", "AAA", "BBB", 100, 100, gid=9))
    s = gl.Season(prev, tgf(g1 + g2), np.array([0.0, 1.0, 0.0, 0.0, 0.0]))     # O = eFG%
    r = s.cur_rating("fourfactor", "AAA", np.datetime64("2025-11-03", "D"))
    assert r[0] == pytest.approx(110 / 150) and r[2] == pytest.approx(100.0) and r[3] == 2
    # defence = the same fit on the factors ALLOWED (BBB's, here the default OFF: 55/100)
    assert r[1] == pytest.approx(0.55)


# ------------------------------------------------------------------ walk-forward: same-date and later rows never count
@pytest.mark.parametrize("ct", gl.C_OPTS)
def test_walk_forward_excludes_same_date_and_later(ct):
    base_rows = []
    for j in range(8):
        base_rows += rows(pd.Timestamp("2025-11-01") + pd.Timedelta(days=j), TEAMS[j % 2], TEAMS[2 + (j // 2) % 2], 100 + 3 * j,
                          101 + 2 * j, gid=j)
    prev = toy_tg(7, 30, "2024-11-01")
    coef, _ = gl.fit_four_factor(prev)
    D = pd.Timestamp("2025-11-09")
    leak = rows(D, "AAA", "CCC", 150, 60, gid=500) + rows(D, "BBB", "DDD", 70, 160, gid=501) + \
        rows(D + pd.Timedelta(days=2), "AAA", "DDD", 170, 90, gid=502)
    s0 = gl.Season(prev, tgf(base_rows), coef)
    s1 = gl.Season(prev, tgf(base_rows + leak), coef)
    Dd = np.datetime64(D, "D")
    for team in TEAMS:
        for ven in ("all", "home", "away"):
            a, b = s0.cur_rating(ct, team, Dd, ven), s1.cur_rating(ct, team, Dd, ven)
            assert (a is None) == (b is None)
            if a is not None:
                assert np.allclose(a, b, atol=1e-12, rtol=0), (ct, team, ven)
        assert s0.rest_cat(team, Dd) == s1.rest_cat(team, Dd)
    assert s0.league_ratios(Dd) == s1.league_ratios(Dd)
    # ...and the row on the day before D does count
    s2 = gl.Season(prev, tgf(base_rows + rows(D - pd.Timedelta(days=1), "AAA", "CCC", 150, 60, gid=600)), coef)
    assert not np.allclose(s2.cur_rating(ct, "AAA", Dd), s0.cur_rating(ct, "AAA", Dd))


def test_decision_date_is_et_date_of_min_tip_T_minus_1h():
    tip = int(pd.Timestamp("2025-11-05 19:30", tz="America/New_York").timestamp())
    g = pd.DataFrame({"tip": [tip, tip], "T": [tip, tip - 7200 * 12]})
    d = gl.decision_dates(g)
    assert d[0] == np.datetime64("2025-11-05")
    assert d[1] == np.datetime64("2025-11-04")      # T = tip - 24h, so T - 1h = 18:30 ET the day before
    late = int(pd.Timestamp("2025-11-06 00:30", tz="America/New_York").timestamp())
    assert gl.decision_dates(pd.DataFrame({"tip": [late], "T": [late]}))[0] == np.datetime64("2025-11-05")   # tip - 1h = 23:30


# ------------------------------------------------------------------ the full tensor
def test_tensor_matches_project_one_and_is_finite(toy):
    s, prev, cur, coef = toy
    g = toy_games(cur, 0, 10)
    consts = gl.Consts(np.random.default_rng(1).normal(-1, .1, (7, 10)), np.random.default_rng(2).normal(-.5, .2, (7, 10, 3)),
                       np.random.default_rng(3).normal(1, .1, (7, 10)))
    P = gl.build_projections(s, g, consts)
    assert P.shape == (26880, 10) and np.isfinite(P).all()
    rng = np.random.default_rng(9)
    for fl in list(rng.integers(0, 26880, 40)) + [0, 26879, gl.flat_from_spec(gl.DEFAULT_IDX)]:
        sp = gl.spec_from_flat(fl)
        assert gl.project_one(s, g, consts, sp) == pytest.approx(P[fl], abs=1e-9), sp


def test_tensor_cell_matches_from_scratch_formula(toy):
    """User's base formula recomputed from raw rows with plain loops (independent of CurTable/features)."""
    s, prev, cur, coef = toy
    g = toy_games(cur, 0, 6)
    consts = gl.Consts(np.zeros((7, 10)), np.zeros((7, 10, 3)), np.ones((7, 10)))
    P = gl.build_projections(s, g, consts)
    for i, r in enumerate(g.itertuples()):
        D = r2.decision_date(r.tip, r.T)
        def rating(team):
            sub = cur[(cur.team == team) & (cur.date < pd.Timestamp(D))]
            op, dp, pp = s.prev[team]
            r_o, r_p, _ = s.league_ratios(D)
            n = len(sub)
            pr_ = (op * r_o, dp * r_o, pp * r_p)
            if n == 0:
                return pr_
            cu = (sub.pts.sum() / sub.poss.sum(), sub.opp_pts.sum() / sub.opp_poss.sum(), sub.pace.mean())
            return tuple((20 * pr_[j] + n * cu[j]) / (20 + n) for j in range(3))
        ra, rb = rating(r.away), rating(r.home)
        pace = (ra[2] + rb[2]) / 2
        want = pace * (ra[0] + rb[1]) / 2 + pace * (rb[0] + ra[1]) / 2
        assert P[gl.flat_from_spec(gl.DEFAULT_IDX), i] == pytest.approx(want, abs=1e-9)


def test_fit_constants_runs_and_is_finite(toy):
    s, prev, cur, coef = toy
    games24 = tn.fit_games_2425(cur.assign(tip=lambda d: ((d.date - pd.Timestamp("1970-01-01")).dt.total_seconds() + 23 * 3600).astype("int64"),
                                           season_type=2))
    games24 = games24.assign(actual=230.0 + np.random.default_rng(0).normal(0, 12, len(games24)))
    c = gl.fit_constants(s, games24)
    assert c.c_b2b.shape == (7, 10) and c.e_cat.shape == (7, 10, 3) and c.b.shape == (7, 10)
    assert np.isfinite(c.c_b2b).all() and np.isfinite(c.e_cat).all() and np.isfinite(c.b).all()
    assert c.pair(2, 0)["b"] == c.b[2, 0]


# ------------------------------------------------------------------ team-games copy equals #39's on shared columns
def raw_box():
    base = dict(season_type=2, team_home_away="home", field_goals_made=40, three_point_field_goals_made=12,
                field_goals_attempted=88, free_throws_made=18, free_throws_attempted=24, offensive_rebounds=10,
                defensive_rebounds=33, turnovers=12, total_turnovers=14, team_score=110, opponent_team_score=104)
    r = []
    for gid, h, a, dt in ((1, "BOS", "NY", "2025-11-01 19:30:00-04:00"), (2, "GS", "NY", "2025-11-02 22:00:00-05:00")):
        for team, opp, ven in ((h, a, "home"), (a, h, "away")):
            x = dict(base, game_id=gid, team_abbreviation=team, opponent_team_abbreviation=opp, team_home_away=ven,
                     game_date_time=pd.Timestamp(dt))
            if ven == "away":
                x.update(team_score=104, opponent_team_score=110, total_turnovers=np.nan, turnovers=15)   # NaN -> tov fix
            r.append(x)
    df = pd.DataFrame(r)
    df["game_date_time"] = pd.to_datetime(df.game_date_time, utc=True).dt.tz_convert("America/New_York")
    return df


def test_team_games_g_equals_39_on_shared_columns_and_adds_ff():
    tb = raw_box()
    a, b = gl.team_games_g(tb, (2,)), tn.team_games_x(tb, (2,))
    for c in b.columns:
        assert np.array_equal(a[c].values, b[c].values), c
    r = a[(a.team == "BOS") & (a.game_id == 1)].iloc[0]
    assert r.efgnum == 40 + 0.5 * 12 and r.fga == 88 and r.tov == 14                 # own
    assert r.tovden == pytest.approx(88 + 0.44 * 24 + 14) and r.orebden == 10 + 33   # oreb + opp dreb
    assert r.ftm == 18 and r.o_tov == 15 and r.venue == "home"                       # opponent's NaN total_turnovers -> turnovers
    assert r.o_orebden == 10 + 33


# ------------------------------------------------------------------ strikes, costs, the bet rule
def strikes_df(rows_):
    d = pd.DataFrame(rows_, columns=["event", "ticker", "strike", "bid", "ask", "y"])
    d["mid"] = (d.bid + d.ask) / 2
    d["strike_type"] = "greater"
    return d


def test_main_strike_nearest_half_ties_lower_and_range():
    g = pd.DataFrame({"event": ["E1", "E2", "E3", "E4"]})
    s = strikes_df([
        ("E1", "a", 220.5, 0.58, 0.62, 1), ("E1", "b", 225.5, 0.44, 0.48, 0), ("E1", "c", 230.5, 0.20, 0.24, 0),   # mids .60 .46 .22
        ("E2", "a", 220.5, 0.53, 0.57, 1), ("E2", "b", 225.5, 0.43, 0.47, 0),                                       # .55 vs .45: tie -> lower
        ("E3", "a", 220.5, 0.90, 0.94, 1), ("E3", "b", 225.5, 0.10, 0.14, 0),                                       # both outside 15-85
        ("E4", "a", 220.5, np.nan, 0.50, 1), ("E4", "b", 225.5, 0.80, 0.90, 1)])                                    # no valid quote / mid .85
    bt = gb.main_strike_table(g, s)
    assert bt.ticker.tolist()[:2] == ["b", "a"]
    assert bt.has_strike.tolist() == [True, True, False, True] and bt.ticker[3] == "b"      # mid exactly .85 is allowed
    assert bt.K[1] == 220.5


def test_bet_costs_and_net_exact():
    g = pd.DataFrame({"event": ["E1"]})
    s = strikes_df([("E1", "a", 225.5, 0.40, 0.44, 1)])
    bt = gb.main_strike_table(g, s)
    r = bt.iloc[0]
    fee_o, fee_u = lib.fee_pc(0.44, 1), lib.fee_pc(1 - 0.40, 1)
    assert fee_o == pytest.approx(math.ceil(0.07 * 0.44 * 0.56 * 1e6 - 1e-9) / 1e6)     # micro_ceil, at the ASK
    assert r.fee_over == fee_o and r.fee_under == fee_u
    assert r.paid_over == pytest.approx(0.45) and r.paid_under == pytest.approx(0.61)     # ask + 1c; NO ask = 1 - bid
    assert r.net_over == pytest.approx(1 - 0.44 - 0.01 - fee_o)                          # y = yes: OVER wins
    assert r.net_under == pytest.approx(0 - 0.60 - 0.01 - fee_u)
    # y = no
    bt2 = gb.main_strike_table(g, strikes_df([("E1", "a", 225.5, 0.40, 0.44, 0)]))
    assert bt2.net_over[0] == pytest.approx(-0.44 - 0.01 - fee_o) and bt2.net_under[0] == pytest.approx(1 - 0.60 - 0.01 - fee_u)


def test_bets_for_side_threshold_and_equal_projection():
    g = pd.DataFrame({"event": [f"E{i}" for i in range(5)]})
    s = strikes_df([(f"E{i}", "a", 225.5, 0.48, 0.52, 1 if i % 2 == 0 else 0) for i in range(5)])
    bt = gb.main_strike_table(g, s)
    proj = np.array([225.5 + 3.0, 225.5 - 3.0, 225.5 + 1.0, 225.5, 225.5 - 8.0])
    for T, want in ((0, 4), (2, 3), (4, 1), (6, 1), (8, 1)):
        assert len(gb.bets_for(proj, bt, T)) == want                      # proj == strike never bets
    b = gb.bets_for(proj, bt, 2)
    assert b.side.tolist() == ["over", "under", "under"] and b.event.tolist() == ["E0", "E1", "E4"]
    f = lib.fee_pc(0.52, 1)
    assert b.net.iloc[0] == pytest.approx(1 - 0.52 - 0.01 - f)            # E0 y=1, OVER wins
    assert b.net.iloc[1] == pytest.approx(1 - 0.52 - 0.01 - f)            # E1 y=0, UNDER (NO ask .52) wins
    assert b.net.iloc[2] == pytest.approx(0 - 0.52 - 0.01 - f)            # E4 y=0 (i=4 -> even -> y=1), UNDER loses


def test_strategy_stats_match_naive_loop_and_lb():
    rng = np.random.default_rng(11)
    N, S = 400, 30
    rows_ = []
    for i in range(N):
        bid = float(rng.integers(30, 60)) / 100
        rows_.append((f"E{i}", "a", 220.5 + rng.integers(0, 6), bid, bid + 0.04, int(rng.integers(0, 2))))
    g = pd.DataFrame({"event": [r[0] for r in rows_]})
    bt = gb.main_strike_table(g, strikes_df(rows_))
    ba, v = gb.bet_arrays(bt)
    P = bt.K.values + rng.normal(0, 5, (S, N))
    st = gb.strategy_stats(P, ba, chunk=7)
    fin = gb.finalize_stats(st)
    for ti, T in enumerate(gb.THRESHOLDS):
        for s_ in (0, 5, 29):
            nets, paid, wins = [], [], 0
            for i in range(N):
                d = P[s_, i] - bt.K[i]
                if d == 0 or abs(d) < T:
                    continue
                over = d > 0
                p = bt.ask[i] if over else 1 - bt.bid[i]
                fee = lib.fee_pc(p, 1)
                win = (bt.y[i] == 1) if over else (bt.y[i] == 0)
                nets.append(float(win) - p - 0.01 - fee)
                paid.append(p + 0.01)
                wins += int(win)
            n = len(nets)
            assert fin["n"][ti, s_] == n and fin["wins"][ti, s_] == wins
            assert fin["mean"][ti, s_] == pytest.approx(np.mean(nets), abs=1e-12)
            assert fin["sd"][ti, s_] == pytest.approx(np.std(nets, ddof=1), abs=1e-10)
            assert fin["avg_paid"][ti, s_] == pytest.approx(np.mean(paid))
            assert fin["total"][ti, s_] == pytest.approx(np.sum(nets), abs=1e-10)
            assert fin["lb"][ti, s_] == pytest.approx(np.mean(nets) - 1.96 * np.std(nets, ddof=1) / math.sqrt(n), abs=1e-10)


def test_lower_bound_formula():
    assert gb.lower_bound(0.02, 0.5, 400) == pytest.approx(0.02 - 1.96 * 0.5 / 20)


# ------------------------------------------------------------------ winner selection
def test_pick_winner_max_lb_and_tie_order():
    nT, S = 5, 6
    lb = np.full((nT, S), -0.1)
    n = np.full((nT, S), 200.0)
    nondef = np.array([3, 1, 2, 0, 1, 4])
    lb[2, 1] = 0.05
    lb[3, 4] = 0.05 + 1e-12              # tied within tolerance, same nondefault count (1) -> smaller T wins (T index 2 < 3)
    assert gb.pick_winner(lb, n, nondef) == (2, 1)
    lb[1, 3] = 0.05                      # tied, fewer non-default parts (0) wins
    assert gb.pick_winner(lb, n, nondef) == (1, 3)
    lb[0, 5] = 0.2                       # higher LB but not eligible (n < 150)
    n[0, 5] = 149
    assert gb.pick_winner(lb, n, nondef) == (1, 3)
    n[0, 5] = 150                        # eligible at exactly 150 -> now the unique max
    assert gb.pick_winner(lb, n, nondef) == (0, 5)
    lb[:, :] = -0.1
    lb[2, 0] = lb[0, 0] = 0.01           # same formula, two thresholds tied -> smaller threshold
    n[:] = 200
    assert gb.pick_winner(lb, n, nondef) == (0, 0)
    assert gb.pick_winner(lb, np.zeros_like(n), nondef) is None


def test_luck_check_reproducible_and_structure():
    rng = np.random.default_rng(1)
    N = 300
    g = pd.DataFrame({"event": [f"E{i}" for i in range(N)]})
    s = strikes_df([(f"E{i}", "a", 225.5, 0.48, 0.52, int(rng.integers(0, 2))) for i in range(N)])
    ba, _ = gb.bet_arrays(gb.main_strike_table(g, s))
    P = 225.5 + rng.normal(0, 5, (50, N))
    a = gb.luck_check(P, ba, np.random.default_rng(gb.SEED), n_strat=100, min_n=100)
    b = gb.luck_check(P, ba, np.random.default_rng(gb.SEED), n_strat=100, min_n=100)
    assert np.array_equal(a["lb"], b["lb"]) and a["drawn"] == 100 and a["eligible"] == len(a["lb"]) > 0
    assert (a["n"] >= 100).all() and (a["n"] <= N).all()
    full = gb.luck_full(P, ba, np.random.default_rng(5), min_n=100)
    assert len(full) <= 50 * 5 and np.isfinite(full).all()


# ------------------------------------------------------------------ coin flip and pass bar
def test_coin_flip_and_pass_bar():
    g = pd.DataFrame({"event": [f"E{i}" for i in range(200)]})
    s = strikes_df([(f"E{i}", "a", 225.5, 0.48, 0.52, 1) for i in range(200)])        # every game goes OVER
    bt = gb.main_strike_table(g, s)
    bets = gb.bets_for(np.full(200, 230.0), bt, 0)                                     # always bets OVER: all win
    cf = gb.coin_flip(bets, bt, n=500, seed=gb.SEED)
    assert cf["copies"] == 500 and cf["frac_copies_ge_actual"] == 0.0 and cf["n_games"] == 200
    bar, st = gb.pass_bar(bets, weeks=20.0, coin=cf)
    assert all(bar.values()) and st["n"] == 200
    assert st["usd_per_week_at_10"] == pytest.approx(bets.net.sum() * 10 / 20.0)
    bets2 = bets.head(50)
    bar2, _ = gb.pass_bar(bets2, weeks=20.0, coin=cf)
    assert bar2["3_bets_ge_100"] is False
    bar3, _ = gb.pass_bar(bets, weeks=20.0, coin={"frac_copies_ge_actual": 0.06})
    assert bar3["4_coin_flip_le_5pct"] is False
    bar4, _ = gb.pass_bar(bets, weeks=10 ** 6, coin=cf)
    assert bar4["5_usd_per_week_ge_2"] is False


# ------------------------------------------------------------------ discovery / final plumbing (mocks)
def mock_world(n_disc=150, n_unseen=40, seed=0):
    rng = np.random.default_rng(seed)
    rows_g, rows_s = [], []
    for i in range(n_disc + n_unseen):
        split = "discovery" if i < n_disc else "unseen"
        day = "2025-11-%02d" % (1 + i % 28) if split == "discovery" else "2026-02-%02d" % (1 + i % 28)
        tip = int(pd.Timestamp(day + " 19:30", tz="America/New_York").timestamp())
        a, h = rng.choice(TEAMS, 2, replace=False)
        actual = 230 + rng.normal(0, 15)
        ev = f"EV{i}"
        rows_g.append(dict(event=ev, split=split, tip=tip, T=tip, away=a, home=h, median=230.5, actual=actual))
        for k, (dk, mid) in enumerate(((-5, 0.65), (0, 0.5), (5, 0.35))):
            K = 230.5 + dk
            rows_s.append(dict(event=ev, ticker=f"{ev}-{k}", strike=K, bid=mid - 0.02, ask=mid + 0.02, mid=mid, strike_type="greater",
                               y=float(actual > K), status="finalized"))
    return pd.DataFrame(rows_g), pd.DataFrame(rows_s)


class Recorder:
    def __init__(self, games_all):
        self.calls, self.games_all = [], games_all

    def __call__(self, games, strikes, which):
        self.calls.append(list(which))
        g = games[games.split.isin(which)].copy()
        s = strikes[strikes.event.isin(g.event)].copy()
        return g, s


def test_core_discovery_never_touches_unseen_and_freezes_winner(tmp_path):
    games, strikes = mock_world()
    seen = {}
    rng = np.random.default_rng(1)
    consts = gl.Consts(np.zeros((7, 10)), np.zeros((7, 10, 3)), np.ones((7, 10)))

    def build(gd):
        seen["splits"] = set(gd.split)
        seen["n"] = len(gd)
        return 230.5 + rng.normal(0, 6, (gl.N_FORMULAS, len(gd))), consts
    rec = Recorder(games)
    out = gr.core_discovery(games, strikes, {}, build, rec, tmp_path, n_boot=20, min_n=20, n_luck=30)
    assert seen["splits"] == {"discovery"} and seen["n"] == 150
    assert rec.calls == [["discovery"]]                      # the unseen split is never requested
    fz = json.loads((tmp_path / "winner_frozen.json").read_text())
    assert fz["flat"] == out["winner"]["flat"] and fz["T"] == out["winner"]["T"]
    assert tuple(fz["spec"]) == gl.spec_from_flat(fz["flat"])
    assert out["n_strategies"] == 134400 and out["games"]["valid_settled"] == 150
    # the winner is the max-LB eligible strategy
    df = pd.read_csv(tmp_path / "all_strategies.csv")
    el = df[df.eligible]
    assert out["winner"]["stats"]["lb"] == pytest.approx(el.lb.max(), abs=1e-9)
    assert len(df) == 134400 and (tmp_path / "leaderboard_top50.csv").exists()
    # a second discovery run refuses to overwrite the frozen winner
    with pytest.raises(RuntimeError):
        gr.core_discovery(games, strikes, {}, build, Recorder(games), tmp_path, n_boot=20, min_n=20, n_luck=30)


def test_discovery_entry_uses_gate_cutoff(monkeypatch):
    seen = {}

    class Stop(Exception):
        pass

    monkeypatch.setattr(gr.v2, "load_games", lambda: (pd.DataFrame(), pd.DataFrame(), {}))

    def fake_world(before=None, raw=False):
        seen["before"] = before
        raise Stop
    monkeypatch.setattr(gr, "load_world", fake_world)
    with pytest.raises(Stop):
        gr.discovery()
    assert seen["before"] == "2026-01-23"


def test_read_box_drops_rows_on_or_after_cutoff(monkeypatch):
    tb = raw_box()
    tb.loc[2:, "game_date_time"] = pd.Timestamp("2026-01-23 19:30:00-05:00")
    monkeypatch.setattr(pd, "read_parquet", lambda *_a, **_k: tb.copy())
    out = gr.read_box(2026, "2026-01-23")
    assert len(out) == 2 and (pd.to_datetime(out.game_date_time) < pd.Timestamp("2026-01-23", tz="America/New_York")).all()
    assert len(gr.read_box(2026, None)) == 4


def test_final_refuses_without_frozen_winner(tmp_path, monkeypatch):
    games, strikes = mock_world()
    called = []
    with pytest.raises(FileNotFoundError):
        gr.core_final(games, strikes, lambda: called.append("world"), lambda *a: called.append("attach"),
                      tmp_path / "winner_frozen.json", tmp_path / "final_results.json")
    assert called == []                                    # nothing loaded, nothing attached
    monkeypatch.setattr(gr, "FROZEN", tmp_path / "nope.json")
    monkeypatch.setattr(gr.v2, "load_games", lambda: called.append("load_games"))
    with pytest.raises(FileNotFoundError):
        gr.final()
    assert called == []


def test_final_evaluates_exactly_the_frozen_winner(tmp_path, monkeypatch, toy):
    season, prev, cur, coef = toy
    games, strikes = mock_world(n_disc=10, n_unseen=60)
    consts = gl.Consts(np.random.default_rng(1).normal(-1, .1, (7, 10)), np.random.default_rng(2).normal(-.5, .2, (7, 10, 3)),
                       np.random.default_rng(3).normal(1, .1, (7, 10)))
    spec = gl.spec_from_names(dict(A="w0.75", B="own", C="decay10", D=10, E="off", F="cat", G="on", H="on"))
    fl = gl.flat_from_spec(spec)
    fz = {"spec": list(spec), "flat": fl, "T": 2, "names": {k: str(x) for k, x in gl.spec_names(spec).items()},
          "discovery_stats": {"lb": 0.0}, "constants_pair": consts.pair(spec[0], spec[2])}
    (tmp_path / "winner_frozen.json").write_text(json.dumps(fz))
    # unseen games in the mock are dated 2026-02; the toy season ends 2025-12, which is fine (walk-forward on all toy rows)
    used = []
    real = gl.project_one
    monkeypatch.setattr(gl, "project_one", lambda s, g, c, sp: (used.append((tuple(sp), len(g))), real(s, g, c, sp))[1])
    monkeypatch.setattr(gl, "build_projections", lambda *a, **k: pytest.fail("the full grid must not be built in final"))
    rec = Recorder(games)
    out = gr.core_final(games, strikes, lambda: (season, consts), rec, tmp_path / "winner_frozen.json",
                        tmp_path / "final_results.json", out_dir=tmp_path)
    assert rec.calls == [["unseen"]] and used == [(spec, 60)]
    # recompute the bets by hand with the same single-formula projection
    gu = games[games.split == "unseen"]
    proj = real(season, gu, consts, spec)
    bt = gb.main_strike_table(gu, strikes[strikes.event.isin(gu.event)])
    want = gb.bets_for(proj, bt, 2)
    assert out["bets"]["n"] == len(want) and out["bets"]["mean"] == pytest.approx(want.net.mean())
    assert set(out["pass_bar"]) == {"1_mean_net_pos", "2_boot_lo_gt0", "3_bets_ge_100", "4_coin_flip_le_5pct",
                                    "5_usd_per_week_ge_2"}
    # one test only
    with pytest.raises(RuntimeError):
        gr.core_final(games, strikes, lambda: (season, consts), Recorder(games), tmp_path / "winner_frozen.json",
                      tmp_path / "final_results.json", out_dir=tmp_path)


def test_final_aborts_if_constants_drift(tmp_path, toy):
    season, prev, cur, coef = toy
    games, strikes = mock_world(n_disc=10, n_unseen=20)
    c1 = gl.Consts(np.zeros((7, 10)), np.zeros((7, 10, 3)), np.ones((7, 10)))
    c2 = gl.Consts(np.zeros((7, 10)) + 1e-3, np.zeros((7, 10, 3)), np.ones((7, 10)))
    spec = gl.DEFAULT_IDX
    fz = {"spec": list(spec), "flat": gl.flat_from_spec(spec), "T": 0, "names": {k: str(x) for k, x in gl.spec_names(spec).items()},
          "discovery_stats": {}, "constants_pair": c1.pair(spec[0], spec[2])}
    (tmp_path / "f.json").write_text(json.dumps(fz))
    with pytest.raises(AssertionError):
        gr.core_final(games, strikes, lambda: (season, c2), Recorder(games), tmp_path / "f.json", tmp_path / "r.json")


def test_main_strike_spread_filter_never_picks_empty_book():
    g = pd.DataFrame({"event": ["E1", "E2"]})
    s = strikes_df([("E1", "empty", 225.5, 0.01, 0.99, 1), ("E1", "ok", 230.5, 0.30, 0.38, 0),
                    ("E2", "empty", 225.5, 0.01, 0.99, 1), ("E2", "wide", 230.5, 0.40, 0.51, 1)])
    bt = gb.main_strike_table(g, s)
    assert bt.ticker[0] == "ok" and not bt.has_strike[1]          # 11c spread excluded; 10c allowed
    s2 = strikes_df([("E1", "ten", 225.5, 0.40, 0.50, 1)])
    assert gb.main_strike_table(g.head(1), s2).ticker[0] == "ten"


def test_luck_shared_matches_naive_and_is_fast():
    import time
    rng = np.random.default_rng(2)
    N = 220
    g = pd.DataFrame({"event": [f"E{i}" for i in range(N)]})
    s = strikes_df([(f"E{i}", "a", 225.5, 0.48, 0.52, int(rng.integers(0, 2))) for i in range(N)])
    ba, _ = gb.bet_arrays(gb.main_strike_table(g, s))
    P = 225.5 + rng.normal(0, 5, (40, N))
    got = gb.luck_shared(P, ba, np.random.default_rng(gb.SEED), reps=6, min_n=100, chunk=7)
    side = np.random.default_rng(gb.SEED).integers(0, 2, (N, 6)).astype(bool)
    for r in range(6):
        net = np.where(side[:, r], ba["net_over"], ba["net_under"])
        best = -np.inf
        for f in range(40):
            for T in gb.THRESHOLDS:
                d = P[f] - ba["K"]
                m = (np.abs(d) >= T) & (d != 0)
                if m.sum() >= 100:
                    x = net[m]
                    best = max(best, x.mean() - 1.96 * x.std(ddof=1) / math.sqrt(len(x)))
        assert got[r] == pytest.approx(best, abs=1e-10)
    P2 = 225.5 + rng.normal(0, 5, (26880, 660))                 # fake outcomes only: timing of the full-size null
    N2 = 660
    ba2 = {k: rng.random(N2) for k in ba}
    ba2["K"] = np.full(N2, 225.5)
    t = time.time()
    gb.luck_shared(P2, ba2, np.random.default_rng(1), reps=200, min_n=150)
    print("luck_shared 200 reps full size: %.1fs" % (time.time() - t))
