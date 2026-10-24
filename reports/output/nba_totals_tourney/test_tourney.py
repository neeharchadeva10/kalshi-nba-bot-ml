"""Unit tests for research log #39 (toy data only; no real outcomes)."""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tourney as tn  # noqa: E402
import tourney_run as trun  # noqa: E402
import lib  # noqa: E402


def game(date, a, b, pa, pb, poss_a=100.0, poss_b=100.0):
    pace = (poss_a + poss_b) / 2
    return [dict(date=pd.Timestamp(date), team=a, opp=b, pts=pa, opp_pts=pb, poss=poss_a, opp_poss=poss_b, pace=pace),
            dict(date=pd.Timestamp(date), team=b, opp=a, pts=pb, opp_pts=pa, poss=poss_b, opp_poss=poss_a, pace=pace)]


def tg(games):
    return pd.DataFrame([r for g in games for r in g])


PREV = tg([game("2024-11-01", "AAA", "BBB", 110, 100), game("2024-11-02", "AAA", "CCC", 105, 110),
           game("2024-11-03", "BBB", "CCC", 100, 108), game("2024-11-04", "BBB", "AAA", 101, 112)])


# ---------------------------------------------------------------- F1-F5 hand-computed
RA, RB, LO, LP = (1.10, 1.00, 100.0), (1.00, 1.20, 96.0), 1.05, 98.0


def test_formulas_hand_computed():
    # F1: pace 98; A = 98*(1.10+1.20)/2 = 112.7 ; B = 98*(1.00+1.00)/2 = 98
    assert tn.formula_points("F1", RA, RB, LO, LP) == pytest.approx((112.7, 98.0))
    # F2: pace_g = 100*96/98 ; A = pg*1.10*1.20/1.05 ; B = pg*1.00*1.00/1.05
    pg = 100 * 96 / 98
    assert tn.formula_points("F2", RA, RB, LO, LP) == pytest.approx((pg * 1.32 / 1.05, pg * 1.0 / 1.05))
    # F3: pace 98; A = 98*(1.10+1.20-1.05) = 122.5 ; B = 98*(1.00+1.00-1.05) = 93.1
    assert tn.formula_points("F3", RA, RB, LO, LP) == pytest.approx((122.5, 93.1))
    # F4: A = (1.10*100 + 1.20*96)/2 = 112.6 ; B = (1.00*96 + 1.00*100)/2 = 98
    assert tn.formula_points("F4", RA, RB, LO, LP) == pytest.approx((112.6, 98.0))
    # F5: A = 110 + 115.2 - 1.05*98 = 122.3 ; B = 96 + 100 - 102.9 = 93.1
    assert tn.formula_points("F5", RA, RB, LO, LP) == pytest.approx((122.3, 93.1))
    assert tn.formula_total("F5", RA, RB, LO, LP) == pytest.approx(215.4)


def test_formulas_symmetric_in_teams():
    for f in tn.FORMULAS:
        a, b = tn.formula_points(f, RA, RB, LO, LP)
        b2, a2 = tn.formula_points(f, RB, RA, LO, LP)
        assert (a, b) == pytest.approx((a2, b2))


def test_league_average_teams_give_league_total_for_all_formulas():
    avg = (LO, LO, LP)
    for f in tn.FORMULAS:
        assert tn.formula_total(f, avg, avg, LO, LP) == pytest.approx(2 * LO * LP)


# ---------------------------------------------------------------- engine toys
def toy_engine(cur):
    return tn.Engine(PREV, cur)


CUR = tg([game("2025-10-01", "AAA", "BBB", 100, 90), game("2025-10-02", "AAA", "CCC", 110, 95),
          game("2025-10-03", "AAA", "BBB", 120, 100), game("2025-10-03", "CCC", "BBB", 99, 101)])


def prior_of(eng, team, D):
    r_o, r_p, _ = eng.R1.league(np.datetime64(D))
    O, Dd, P = eng.prev[team]
    return O * r_o, Dd * r_o, P * r_p


def test_r2_weights_and_effective_n():
    eng = toy_engine(CUR)
    D = np.datetime64("2025-10-04")
    snap = eng.snapshot(D, "R2")
    w = np.array([0.5 ** 0.2, 0.5 ** 0.1, 1.0])               # ages 2, 1, 0 (most recent = 3rd)
    pts = np.array([100.0, 110.0, 120.0])
    cur_o = (w * pts).sum() / (w * 100.0).sum()
    n_eff = w.sum()
    po = prior_of(eng, "AAA", D)
    assert snap["AAA"][0] == pytest.approx((20 * po[0] + n_eff * cur_o) / (20 + n_eff))
    assert snap["AAA"][2] == pytest.approx((20 * po[2] + n_eff * 100.0) / (20 + n_eff))   # pace 100 all games
    # R1 for the same team uses plain sums (n = 3)
    s1 = eng.snapshot(D, "R1")
    assert s1["AAA"][0] == pytest.approx((20 * po[0] + 3 * (330 / 300)) / 23)


def test_r1_equals_38_class_projection():
    eng = toy_engine(CUR)
    D = np.datetime64("2025-10-04")
    Lo, Lp, _, _ = eng.league_level(D)
    raw, _ = eng.project_raw("AAA", "BBB", D)
    assert raw["F1_R1"] == pytest.approx(eng.R1.project("AAA", "BBB", D, 20))


def test_same_date_exclusion_r1_r2_r3():
    D = np.datetime64("2025-10-03")                           # AAA, BBB, CCC play ON this date
    eng = toy_engine(CUR)
    cur_wo = CUR[CUR.date < pd.Timestamp(D)].reset_index(drop=True)
    cur_bad = CUR.copy()
    cur_bad.loc[cur_bad.date == pd.Timestamp(D), ["pts", "opp_pts"]] = 999.0   # nonsense on date D itself
    eng_bad, eng_wo = toy_engine(cur_bad), toy_engine(cur_wo)
    for r in tn.RTYPES:
        a, b, c = eng.snapshot(D, r), eng_bad.snapshot(D, r), eng_wo.snapshot(D, r)
        for t in ("AAA", "BBB", "CCC"):
            assert a[t] == pytest.approx(c[t]) and b[t] == pytest.approx(c[t])
    assert eng.league_level(D) == pytest.approx(eng_wo.league_level(D))
    assert eng.b2b("AAA", D) and not eng.b2b("BBB", np.datetime64("2025-10-01"))


def test_future_games_do_not_change_earlier_snapshots():
    D = np.datetime64("2025-10-02")
    later = pd.concat([CUR, tg([game("2025-11-01", "AAA", "CCC", 150, 150)])], ignore_index=True)
    for r in tn.RTYPES:
        a, b = toy_engine(CUR).snapshot(D, r), toy_engine(later).snapshot(D, r)
        for t in ("AAA", "BBB", "CCC"):
            assert a[t] == pytest.approx(b[t])


# ---------------------------------------------------------------- R3
def test_r3_two_team_hand_answer():
    t = tg([game("2025-10-01", "AAA", "BBB", 110, 100)])
    adj, rounds = tn.kenpom_adjust(t)
    # L = 1.05; raw O=(1.1,1.0), D=(1.0,1.1); round 1: O_A=1.1*1.05/1.1=1.05, O_B=1.0*1.05/1.0=1.05,
    # D_A=1.0*1.05/1.05=1.0, D_B=1.1*1.05/1.05=1.1 ; round 2: no change -> converged at round 2
    assert adj["AAA"] == pytest.approx((1.05, 1.0)) and adj["BBB"] == pytest.approx((1.05, 1.1))
    assert rounds == 2
    # one round only: stops at the cap
    adj1, r1 = tn.kenpom_adjust(t, max_rounds=1)
    assert r1 == 1 and adj1["AAA"] == pytest.approx((1.05, 1.0))


def test_r3_identical_teams_give_league_level():
    t = tg([game("2025-10-01", "AAA", "BBB", 110, 110), game("2025-10-02", "BBB", "CCC", 110, 110),
            game("2025-10-03", "AAA", "CCC", 110, 110)])
    adj, _ = tn.kenpom_adjust(t)
    for o, d in adj.values():
        assert o == pytest.approx(1.10, abs=1e-9) and d == pytest.approx(1.10, abs=1e-9)


def test_r3_three_team_consistent_league_reproduces_rates():
    # exactly rank-1 data: pts rate x_ij = L a_i b_j, mean over the 6 ordered pairs = L  =>  truth is O_i=L a_i s^-1,
    # D_j = L b_j s (any scale s); the converged ratings must reproduce every x_ij = O_i D_j / L.
    a = [1.1, 1.0, 0.9]
    b1, b2 = 1.1, 0.9
    b3 = (6 - 1.9 * b1 - 2.0 * b2) / 2.1
    b = [b1, b2, b3]
    L, names = 1.05, ["AAA", "BBB", "CCC"]
    games = []
    d = 0
    for i in range(3):
        for j in range(3):
            if i < j:
                d += 1
                games.append(game(f"2025-10-{d:02d}", names[i], names[j], 100 * L * a[i] * b[j], 100 * L * a[j] * b[i]))
    adj, rounds = tn.kenpom_adjust(tg(games))
    assert rounds < 100
    for i in range(3):
        for j in range(3):
            if i != j:
                assert adj[names[i]][0] * adj[names[j]][1] / L == pytest.approx(L * a[i] * b[j], abs=1e-4)


def test_r3_is_a_fixed_point_of_the_update():
    t = tg([game("2025-10-01", "AAA", "BBB", 112, 101), game("2025-10-02", "BBB", "CCC", 99, 108),
            game("2025-10-03", "AAA", "CCC", 120, 100), game("2025-10-04", "CCC", "BBB", 104, 97, 98, 102)])
    adj, _ = tn.kenpom_adjust(t)
    L = t.pts.sum() / t.poss.sum()
    for team in adj:
        g = t[t.team == team]
        o = sum(r.pts / r.poss * L / adj[r.opp][1] * r.poss for r in g.itertuples()) / g.poss.sum()
        assert o == pytest.approx(adj[team][0], abs=1e-5)


def test_r3_engine_uses_adjusted_ratings_and_unadjusted_pace():
    eng = toy_engine(CUR)
    D = np.datetime64("2025-10-04")
    snap = eng.snapshot(D, "R3")
    adj, _ = tn.kenpom_adjust(CUR[CUR.date < pd.Timestamp(D)])
    po = (eng.prev_adj["AAA"][0] * eng.R1.league(D)[0], eng.prev_adj["AAA"][1] * eng.R1.league(D)[0])
    assert snap["AAA"][0] == pytest.approx((20 * po[0] + 3 * adj["AAA"][0]) / 23)
    assert snap["AAA"][1] == pytest.approx((20 * po[1] + 3 * adj["AAA"][1]) / 23)
    assert snap["AAA"][2] == pytest.approx(eng.snapshot(D, "R1")["AAA"][2])         # pace untouched


# ---------------------------------------------------------------- rest
def test_rest_day_counting():
    dates = np.array(["2025-10-20", "2025-10-21", "2025-10-24"], dtype="datetime64[D]")
    f = lambda s: tn.b2b_flag(dates, np.datetime64(s))                              # noqa: E731
    assert f("2025-10-20") is False          # first game of the season: not a back-to-back
    assert f("2025-10-21") is True           # played yesterday
    assert f("2025-10-22") is True           # 10-21 game is yesterday
    assert f("2025-10-23") is False          # last game 2 days ago
    assert f("2025-10-24") is False          # the game ON D does not count; previous is 3 days back
    assert f("2025-10-25") is True


def test_fit_c_with_intercept():
    # known line resid = 2.0 - 1.5 x exactly -> a = 2.0, c = -1.5
    x = np.array([0, 1, 2, 1, 0, 2])
    c, a, n, nb = tn.fit_c(2.0 - 1.5 * x, x)
    assert c == pytest.approx(-1.5) and a == pytest.approx(2.0) and n == 6 and nb == 6
    # a constant shift moves a only, not c (intercept absorbs it)
    c2, a2, _, _ = tn.fit_c(2.0 - 1.5 * x + 5, x)
    assert c2 == pytest.approx(-1.5) and a2 == pytest.approx(7.0)
    # hand OLS: x=[0,1,2,1], r=[1,-2,-3,-1]: xbar=1, rbar=-1.25, Sxx=2, Sxr=-4 -> c=-2, a=rbar-c*xbar=0.75
    c3, a3, _, _ = tn.fit_c(np.array([1.0, -2.0, -3.0, -1.0]), np.array([0, 1, 2, 1]))
    assert c3 == pytest.approx(-2.0) and a3 == pytest.approx(0.75)
    with pytest.raises(AssertionError):
        tn.fit_c(np.array([1.0, 2.0]), np.array([1, 1]))


def test_fit_all_c_matches_manual_ols():
    eng = toy_engine(CUR)
    g = pd.DataFrame({"away": ["AAA", "BBB", "AAA"], "home": ["BBB", "CCC", "CCC"],
                      "tip": [1759615200 + 86400 * i for i in (0, 1, 2)], "actual": [215.0, 200.0, 230.0]})
    g["T"] = g.tip
    c, info = tn.fit_all_c(eng, g)
    raw = eng.project_table(g)
    res = g.actual.values - raw["F3_R2"].values
    x = raw.nb2b.values.astype(float)
    assert c[("F3", "R2")] == pytest.approx(((x - x.mean()) * (res - res.mean())).sum() / ((x - x.mean()) ** 2).sum())
    assert info[("F3", "R2")][2] == 3 and len(c) == 15


def test_with_rest_applies_c_times_count():
    raw = pd.DataFrame({f"{f}_{r}": [200.0, 210.0] for f in tn.FORMULAS for r in tn.RTYPES})
    raw["nb2b"] = [2, 0]
    c = {(f, r): -0.5 for f in tn.FORMULAS for r in tn.RTYPES}
    out = tn.with_rest(raw, c)
    assert list(out.columns) == tn.VERSIONS and len(tn.VERSIONS) == 30
    assert out["F1_R1_S1"].tolist() == [199.0, 210.0] and out["F1_R1_S0"].tolist() == [200.0, 210.0]


# ---------------------------------------------------------------- winner, buckets, sizes
def test_winner_tie_order():
    base = {v: 0.25 for v in tn.VERSIONS}
    assert tn.pick_winner(base) == "F1_R1_S0"
    b = dict(base)
    b["F4_R3_S0"] = b["F2_R2_S1"] = b["F3_R1_S1"] = b["F1_R1_S1"] = b["F5_R1_S0"] = 0.20
    assert tn.pick_winner(b) == "F5_R1_S0"                  # R1 < R2 < R3, then S0 < S1 before F
    del b["F5_R1_S0"]
    b["F5_R1_S0"] = 0.21
    assert tn.pick_winner(b) == "F1_R1_S1"                  # same R and S: F1 first
    b["F4_R3_S0"] = 0.19
    assert tn.pick_winner(b) == "F4_R3_S0"                  # lowest Brier wins outright


def test_side_won_buckets():
    proj = [230, 230, 236, 220, 240, 226]
    med = [228, 228, 228, 228, 228, 228]
    act = [231, 220, 240, 215, 220, 228]
    t = tn.side_won_table(proj, med, act)
    # gaps 2,2,8,8,12,2 ; sides won (proj-med)*(act-med)>0: y, n, y, y(220<228 & 215<228), n, tie(act==med: dropped)
    assert t["[2,4)"] == {"n": 2, "share": 0.5}
    assert t["[8,inf)"]["n"] == 3 and t["[8,inf)"]["share"] == pytest.approx(2 / 3)
    assert t["[0,2)"]["n"] == 0 and t["[0,2)"]["share"] is None


def test_size_formula():
    for th in (0.03, 0.06):
        assert tn.size_contracts(th, th) == 1
        assert tn.size_contracts(0.10, th) == 10
        assert tn.size_contracts(0.25, th) == 10
        assert tn.size_contracts(th - 1e-13, th) == 1       # float noise at the threshold never gives 0
    assert tn.size_contracts(0.065, 0.03) == 5              # 1 + floor(9*0.035/0.07 = 4.5)
    assert tn.size_contracts(0.08, 0.06) == 5               # 1 + floor(9*0.02/0.04 = 4.5)
    assert tn.size_contracts(0.0999, 0.03) == 9             # 1 + floor(8.987)
    assert tn.size_contracts(0.0301, 0.03) == 1


# ---------------------------------------------------------------- fast residual test == lib.residual_test
def _toy_strikes(n_games=40, seed=3):
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n_games):
        for k, (bid, ask) in enumerate([(0.74, 0.76), (0.50, 0.52), (0.28, 0.30)]):
            mid = (bid + ask) / 2
            rows.append(dict(event=f"E{g}", ticker=f"E{g}-{k}", strike=210.5 + 3 * k, strike_type="greater", bid=bid,
                             ask=ask, mid=mid, y=float(rng.random() < mid + 0.05 * (0.9 - 0.3 * k - mid)),
                             p=float(np.clip(mid + rng.normal(0, 0.1), 0.05, 0.95)), cvol=5.0))
    return pd.DataFrame(rows)


def test_fast_residual_test_matches_loop():
    s = _toy_strikes()
    a = lib.residual_test(s.mid, s.p, s.y, s.event, n_boot=60)
    b = tn.residual_test_fast(s.mid, s.p, s.y, s.event, n_boot=60, chunk=25)
    assert b == pytest.approx(a, abs=1e-5)


# ---------------------------------------------------------------- sized bets
def test_fee_recomputed_at_actual_count():
    s = _toy_strikes(10)
    s["p"] = [0.9 if k.endswith("-2") else 0.1 for k in s.ticker]       # large edges
    g = pd.DataFrame({"event": sorted(s.event.unique())})
    b = trun.run_bets_sized(g, s, 0.03)
    assert len(b) > 0
    for r in b.itertuples():
        win = (r.yes_result == 1) if r.side == "over" else (r.yes_result == 0)
        assert r.net == pytest.approx((1.0 if win else 0.0) - r.price - lib.fee_pc(r.price, r.count))
        assert r.dollars == pytest.approx(r.count * r.net)
        assert r.count == tn.size_contracts(r.edge, 0.03)
    assert b["count"].max() == 10


def test_contract_weighted_bootstrap_matches_ratio():
    d, c = np.array([1.0, -2.0, 3.0]), np.array([1, 2, 3])
    lo, hi = tn.wmean_boot_ci(d, c, n=2000)
    assert lo <= d.sum() / c.sum() + 1.0 and hi >= lo
    # all-equal per-contract bets: any resample gives the same ratio
    lo, hi = tn.wmean_boot_ci(np.array([0.5, 1.0, 1.5]), np.array([1, 2, 3]), n=500)
    assert lo == pytest.approx(0.5) and hi == pytest.approx(0.5)


# ---------------------------------------------------------------- control flow: unseen never loaded on gate failure
def _toy_world(n=60):
    rng = np.random.default_rng(1)
    games = pd.DataFrame({"event": [f"E{i}" for i in range(n)], "split": ["discovery"] * (n // 2) + ["unseen"] * (n // 2),
                          "T": np.arange(n) * 86400.0 + 1.76e9, "median": 225.0 + rng.normal(0, 3, n),
                          "month": "2025-11", "stage": "regular", "hoopr_id": np.arange(n), "tip": 0})
    rows = []
    for i in range(n):
        for k, (bid, ask) in enumerate([(0.74, 0.76), (0.50, 0.52), (0.28, 0.30)]):
            rows.append(dict(event=f"E{i}", ticker=f"E{i}-{k}", strike=219.5 + 3 * k, strike_type="greater", bid=bid,
                             ask=ask, mid=(bid + ask) / 2, cvol=5.0))
    return games, pd.DataFrame(rows)


def _attach_factory(calls):
    def attach(games, strikes, which):
        calls.append(tuple(which))
        g = games[games.split.isin(which)].copy()
        rng = np.random.default_rng(7)
        g["actual"] = 225.0 + rng.normal(0, 14, len(g))
        s = strikes[strikes.event.isin(g.event)].copy()
        tot = g.set_index("event").actual
        s["y"] = (s.event.map(tot) > s.strike).astype(float)
        return g, s
    return attach


def test_unseen_never_loaded_when_a_gate_fails():
    games, strikes = _toy_world()
    calls, full_calls = [], []

    def bad(g):                                         # terrible projections: gate 1 must fail
        return pd.DataFrame({v: np.full(len(g), 150.0) for v in tn.VERSIONS}, index=g.index)

    def full():
        full_calls.append(1)
        return bad
    out = trun.core_run(games, strikes, {}, bad, full, attach=_attach_factory(calls), n_boot=20, write=False)
    assert out["all_gates_pass"] is False and "unseen" in out["verdict"]
    assert calls == [("discovery",)] and full_calls == []
    assert len(out["leaderboard_discovery"]) == 30


def test_unseen_loaded_only_after_all_gates_pass(monkeypatch):
    games, strikes = _toy_world()
    calls, full_calls = [], []

    def good(g):
        return pd.DataFrame({v: g["median"].values + (0.5 if v.startswith("F2") else 0.0) for v in tn.VERSIONS}, index=g.index)

    def full():
        full_calls.append(1)
        return good
    monkeypatch.setattr(trun.lib, "gate2_pass", lambda *a: True)
    monkeypatch.setattr(trun.pr, "gate3_count", lambda q, w: (500, 500.0))
    real = trun.pr.accuracy
    monkeypatch.setattr(trun.pr, "accuracy", lambda g, s: {**real(g, s), "model_mae": 1.0, "market_mae": 1.0,
                                                         "model_brier": 0.2, "market_brier": 0.2})
    out = trun.core_run(games, strikes, {}, good, full, attach=_attach_factory(calls), n_boot=20, write=False)
    assert out["all_gates_pass"] is True
    assert calls == [("discovery",), ("unseen",)] and full_calls == [1]
    assert "pass_bar_primary" in out and set(out["versions"]) == {"theta_0.03", "theta_0.06"}


def test_cent_fee_sensitivity_and_near_dup():
    b = pd.DataFrame({"count": [10], "price": [0.5], "side": ["over"], "yes_result": [1], "dollars": [4.0]})
    r = trun.cent_fee_sensitivity(b)
    # fee 0.07*10*.25 = 0.175 -> $0.18 ; net = 10*(1-0.5) - 0.18 = 4.82
    assert r["total_net_usd"] == pytest.approx(4.82)
    P = pd.DataFrame({v: np.arange(5.0) + (1.0 if v.startswith("F4") else 0.0) for v in tn.VERSIONS})
    nd = trun.near_dup_stats(P)
    assert nd["F4-F1_R1"]["sd"] == pytest.approx(0.0) and nd["F4-F1_R1"]["max_abs"] == pytest.approx(1.0)


def test_discovery_date_guard():
    games, strikes = _toy_world()
    games["tip"] = 1769212800 + 86400 * 5       # 2026-01-28: after the cutoff
    games["T"] = games["tip"] + 100.0

    def proj(g):
        return pd.DataFrame({v: g["median"].values for v in tn.VERSIONS}, index=g.index)
    with pytest.raises(AssertionError):
        trun.core_run(games, strikes, {}, proj, attach=_attach_factory([]), n_boot=5, write=False)
