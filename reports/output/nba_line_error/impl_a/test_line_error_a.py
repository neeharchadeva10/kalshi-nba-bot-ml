"""Unit tests for research #57 implementation A.  Run:  .venv/Scripts/python.exe -m pytest reports/output/nba_line_error/impl_a/test_line_error_a.py -q
Synthetic data only (the one real-data test perturbs future box scores of a discovery season to prove f8 has no look-ahead)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import clean as C  # noqa: E402
import compare as CMP  # noqa: E402
import features as FE  # noqa: E402
import lea_lib as L  # noqa: E402
import run_select as RS  # noqa: E402
import run_test as RT  # noqa: E402
import run_validate as RV  # noqa: E402


# ------------------------------------------------------------------ statistics, formulas
def test_wilson_known_values():
    lo, hi = L.wilson(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3) and hi == pytest.approx(0.5962, abs=1e-3)
    lo, hi = L.wilson(0, 10)
    assert lo == 0.0 or lo == pytest.approx(0.0, abs=1e-12)
    assert np.isnan(L.wilson(0, 0)[0])
    assert L.BREAK_EVEN == pytest.approx(110 / 210) and round(L.BREAK_EVEN, 4) == 0.5238


def test_side_stats_pushes_and_sides():
    y = np.array([3.0, -2.0, 0.0, 5.0, -1.0, 4.0])
    side = np.array([1, -1, 1, -1, 0, 1])
    s = L.side_stats(side, y)
    # bets: over@3 win, under@-2 win, over@0 push (excluded), under@5 loss, over@4 win
    assert (s["n"], s["wins"], s["pushes"]) == (4, 3, 1)


def test_possession_formula_and_tov_rules():
    assert L.possessions(80, 10, 14, 25) == pytest.approx(0.96 * (80 - 10 + 14 + 0.44 * 25))
    assert list(L.fix_tov([15.0, np.nan, 0.0, 20.0], [16, 12, 14, 18])) == [16, 12, 14, 20]
    tb = pd.DataFrame({"season_type": [2] * 4, "total_turnovers": [28.0, 24.0, 30.0, 26.0], "turnovers": [14, 12, 15, 13]})
    d = L.tov_diagnostics(tb)
    assert d["doubled_total_turnovers"] and list(L.tov_for_box(tb)) == [14, 12, 15, 13]
    tb2 = pd.DataFrame({"season_type": [2] * 3, "total_turnovers": [14.5, 12.6, 15.7], "turnovers": [14, 12, 15]})
    assert not L.tov_diagnostics(tb2)["doubled_total_turnovers"] and list(L.tov_for_box(tb2)) == [14.5, 12.6, 15.7]


def test_real_tov_rule_per_file():
    assert L.tov_diagnostics(L.read_year(2009))["doubled_total_turnovers"]
    assert not L.tov_diagnostics(L.read_year(2014))["doubled_total_turnovers"]
    assert not L.tov_diagnostics(L.read_year(2024))["doubled_total_turnovers"]


# ------------------------------------------------------------------ matching and cleaning rules
def _cand():
    return pd.DataFrame({"game_id": [1, 2, 3, 4, 5],
                         "date": pd.to_datetime(["2020-01-10", "2020-01-12", "2020-01-13", "2020-02-01", "2020-02-02"]),
                         "home_f": ["BOS", "BOS", "BOS", "LAL", "LAL"], "away_f": ["NYK", "NYK", "NYK", "DEN", "DEN"],
                         "season_type": [2, 2, 2, 2, 3]})


def test_match_rows_window_exact_and_ambiguity():
    rows = pd.DataFrame({"date": pd.to_datetime(["2020-01-10", "2020-01-11", "2020-01-12", "2020-03-01", "2020-02-01", "2020-01-31"]),
                         "home_f": ["BOS", "BOS", "BOS", "BOS", "LAL", "LAL"], "away_f": ["NYK", "NYK", "NYK", "NYK", "DEN", "DEN"]})
    m = C.match_rows(rows, _cand())
    assert list(m.status) == ["matched", "ambiguous", "matched", "unmatched", "matched", "matched"]
    assert list(m.game_id)[:1] == [1] and m.game_id.iloc[2] == 2 and m.game_id.iloc[4] == 4 and m.game_id.iloc[5] == 4
    # 2020-01-11 is within +-1 of both game 1 (01-10) and game 2 (01-12): no exact date -> ambiguous
    # wrong home/away never matches
    r2 = pd.DataFrame({"date": pd.to_datetime(["2020-01-10"]), "home_f": ["NYK"], "away_f": ["BOS"]})
    assert C.match_rows(r2, _cand()).status.iloc[0] == "unmatched"


def test_old_odds_date_and_spread_parsing():
    assert L.old_odds_date("2008-09-1028") == pd.Timestamp("2008-10-28")
    assert L.old_odds_date("2011-12-0421") == pd.Timestamp("2012-04-21")
    assert L.old_odds_date("2019-20-1011") == pd.Timestamp("2020-10-11")      # bubble finals
    assert L.old_odds_date("2019-20-1022") == pd.Timestamp("2019-10-22")      # opening night
    assert L.old_odds_date("2019-20-0815") == pd.Timestamp("2020-08-15")
    assert L.old_odds_date("2023-10-24") == pd.Timestamp("2023-10-24")
    assert C.parse_spread("PK") == 0.0 and C.parse_spread("7.5") != "x" and C.parse_spread(3.5) == 3.5


def _tt(n_each=6, total=200.0):
    rows = []
    for k in range(n_each):
        for team in ("AAA", "BBB"):
            rows.append({"date": pd.Timestamp("2020-01-01") + pd.Timedelta(days=k), "tip": k, "team": team, "pts": total / 2, "opp_pts": total / 2})
    return C.TeamTotals(pd.DataFrame(rows))


def test_sanity_rules():
    tt = _tt()
    D = pd.Timestamp("2020-01-20")
    assert C.sanity_status(215.0, "AAA", "BBB", D, tt) is None
    assert C.sanity_status(169.5, "AAA", "BBB", D, tt)[0] == "line_out_of_range_170_275"
    assert C.sanity_status(275.5, "AAA", "BBB", D, tt)[0] == "line_out_of_range_170_275"
    assert C.sanity_status(231.0, "AAA", "BBB", D, tt)[0] == "line_far_from_team_mean_gt30"      # 200 + 31
    assert C.sanity_status(230.0, "AAA", "BBB", D, tt) is None                                     # exactly 30 passes
    # fewer than 5 earlier games: only the range check applies
    early = pd.Timestamp("2020-01-04")                                                             # 3 prior games each
    assert C.sanity_status(260.0, "AAA", "BBB", early, tt) is None
    # games on the same date do not count (strictly before)
    assert tt.mean_before("AAA", pd.Timestamp("2020-01-06"))[1] == 5
    assert tt.mean_before("AAA", pd.Timestamp("2020-01-07"))[1] == 6


def test_disputed_lines_rule():
    m = pd.DataFrame({"date": pd.to_datetime(["2015-01-01"] * 4), "Home": list("abcd"), "Away": list("wxyz"), "occ": 0,
                      "ou_x": [200.0, 200.0, 200.0, 200.0], "ou_n": [201.0, 201.1, np.nan, 200.0],
                      "spread_x": 1.0, "spread_n": 1.0, "pts_x": 1, "pts_n": 1, "home_f": "BOS", "away_f": "NYK"})
    c = C.odds_line_choice(m)
    assert list(c.disputed) == [False, True, False, False]          # exactly 1.0 apart is not disputed; 1.1 is
    assert list(c.line) == [201.0, 201.1, 200.0, 200.0]               # X_new when present, else X
    assert list(c.line_source) == ["oddsdata_new", "oddsdata_new", "oddsdata_x", "oddsdata_new"]


def test_clean_odds_season_reasons_in_order():
    gs = pd.DataFrame({"game_id": [1, 2, 3, 4, 5], "date": pd.to_datetime(["2020-01-10"] * 5),
                       "home": ["BOS", "LAL", "CHI", "MIA", "DEN"], "away": ["NYK", "DEN", "DET", "ORL", "PHX"]})
    tg = []
    for k in range(8):
        for t in ("BOS", "NYK", "LAL", "DEN", "CHI", "DET", "MIA", "ORL", "PHX"):
            tg.append({"date": pd.Timestamp("2020-01-01") + pd.Timedelta(days=k), "tip": k, "team": t, "pts": 100.0, "opp_pts": 100.0})
    tt = C.TeamTotals(pd.DataFrame(tg))
    cand = pd.DataFrame({"game_id": [1, 2, 3, 4, 5, 9], "date": pd.to_datetime(["2020-01-10"] * 5 + ["2020-04-20"]),
                         "home_f": ["BOS", "LAL", "CHI", "MIA", "DEN", "BOS"], "away_f": ["NYK", "DEN", "DET", "ORL", "PHX", "NYK"],
                         "season_type": [2, 2, 2, 2, 2, 3]})
    base = dict(occ=0, Home="h", Away="a", ou_x=200.0, ou_n=200.0, spread_x=1.0, spread_n=1.0, pts_x=0, pts_n=0)
    rows = [dict(base, date=pd.Timestamp("2020-01-10"), home_f="BOS", away_f="NYK", line=200.0, disputed=False, line_source="oddsdata_new", spread=1.0),
            dict(base, date=pd.Timestamp("2020-01-10"), home_f="LAL", away_f="DEN", line=150.0, disputed=False, line_source="oddsdata_new", spread=1.0),
            dict(base, date=pd.Timestamp("2020-01-10"), home_f="CHI", away_f="DET", line=240.0, disputed=False, line_source="oddsdata_new", spread=1.0),
            dict(base, date=pd.Timestamp("2020-01-10"), home_f="MIA", away_f="ORL", line=200.0, disputed=True, line_source="oddsdata_new", spread=1.0),
            dict(base, date=pd.Timestamp("2020-04-20"), home_f="BOS", away_f="NYK", line=200.0, disputed=False, line_source="oddsdata_new", spread=1.0),
            dict(base, date=pd.Timestamp("2020-01-10"), home_f="SAS", away_f="UTA", line=200.0, disputed=False, line_source="oddsdata_new", spread=1.0),
            dict(base, date=pd.Timestamp("2020-01-10"), home_f="DEN", away_f="PHX", line=200.0, disputed=False, line_source="oddsdata_new", spread=1.0),
            dict(base, date=pd.Timestamp("2020-01-10"), home_f="DEN", away_f="PHX", line=201.0, disputed=False, line_source="oddsdata_new", spread=1.0)]
    r, reason, detail = C.clean_odds_season("2019-20", pd.DataFrame(rows), cand, gs, tt)
    assert list(reason)[:6] == [None, "line_out_of_range_170_275", "line_far_from_team_mean_gt30", "line_disputed",
                                "not_regular_season", "unmatched"]
    assert list(reason)[6:] == ["duplicate_game", "duplicate_game"]


def test_clean_closing_inplay_and_missing():
    gs = pd.DataFrame({"game_id": [1], "date": pd.to_datetime(["2021-01-10"]), "home": ["BOS"], "away": ["NYK"]})
    tt = C.TeamTotals(pd.DataFrame({"date": [], "tip": [], "team": [], "pts": [], "opp_pts": []}).astype({"date": "datetime64[ns]"}))
    cand = pd.DataFrame({"game_id": [1, 2], "date": pd.to_datetime(["2021-01-10", "2021-01-11"]), "home_f": ["BOS", "LAL"], "away_f": ["NYK", "DEN"],
                         "season_type": [2, 2]})
    cl = pd.DataFrame({"game_id": [1, 1, 7, 2], "over_under": [220.0, 221.0, 220.0, np.nan],
                       "commence_time": ["2021-01-10T20:00:00Z"] * 4,
                       "snapshot": ["2021-01-10T19:55:00Z", "2021-01-10T20:00:00Z", "2021-01-10T19:55:00Z", "2021-01-10T19:55:00Z"],
                       "home_team_spread": 1.0})
    r, reason, _ = C.clean_closing_season("2020-21", cl, cand, gs, tt)
    assert list(reason) == [None, "snapshot_at_or_after_commence", "unmatched", "line_missing"]


# ------------------------------------------------------------------ synthetic season for look-ahead tests
def make_season(seed=0, n_teams=8, days=45, season="2010-11", start="2010-10-26", gid0=1000):
    rng = np.random.default_rng(seed)
    teams = [f"T{i}" for i in range(n_teams)]
    rows = []
    gid = gid0
    for d in range(days):
        date = pd.Timestamp(start) + pd.Timedelta(days=d)
        order = rng.permutation(n_teams)
        for k in range(0, n_teams, 2):
            if rng.random() < 0.2:
                continue
            h, a = teams[order[k]], teams[order[k + 1]]
            gid += 1
            tip = int(date.timestamp()) + 3600 * 24
            ph, pa = rng.normal(98, 4, 2)
            sh, sa = rng.integers(85, 125, 2).astype(float)
            fh, fa_ = rng.integers(70, 95, 2).astype(float)
            th, ta = rng.integers(15, 40, 2).astype(float)
            for team, opp, ven, pts, opts, fga, tpa, ps in ((h, a, "home", sh, sa, fh, th, ph), (a, h, "away", sa, sh, fa_, ta, pa)):
                rows.append({"game_id": gid, "season": season, "date": date, "tip": tip, "team": team, "opp": opp, "venue": ven,
                             "pts": pts, "opp_pts": opts, "fga": fga, "fta": 20.0, "oreb": 10.0, "tov": 13.0, "tpa": tpa,
                             "poss": ps, "opp_poss": ps, "pace": ps})
    tg = pd.DataFrame(rows)
    g = L.games_from_team_games(tg)
    clean = g.assign(line=(g.actual + rng.normal(0, 8, len(g))).round(0) + 0.5, spread=rng.normal(0, 6, len(g)).round(1))
    clean["line_source"] = "x"
    return tg, clean


def _features(tg, clean, prev=None):
    sd = FE.SeasonData(tg, prev, clean)
    return FE.season_features(sd, clean, None).set_index("game_id")


FEAT_COLS = ["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f9", "f10", "pace10_home", "pace10_away", "lg_pace", "lg_tpar", "lg_total",
             "form5_home", "form10_away", "b2b_home", "b2b_away", "f5_alt1", "f6_alt_sum"]


def _eq(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    return bool(np.all((a == b) | (np.isnan(a) & np.isnan(b))))


def test_no_lookahead_every_feature():
    tg, clean = make_season(1)
    prev_tg, _ = make_season(2, season="2009-10", start="2009-10-27", gid0=500)
    base = _features(tg, clean, prev_tg)
    dates = sorted(clean.date.unique())
    assert base.f1.notna().all() and base.f5.notna().sum() > 20 and base.f10.notna().sum() > 20
    for D in (dates[12], dates[25], dates[35]):
        tg2 = tg.copy()
        late = tg2.date >= D
        rng = np.random.default_rng(7)
        for c in ("pts", "opp_pts", "fga", "tpa", "pace", "poss", "opp_poss", "oreb", "tov"):
            tg2.loc[late, c] = tg2.loc[late, c] * rng.uniform(1.3, 2.0) + 11
        for target in clean[clean.date == D].game_id.values:
            cl2 = clean.copy()
            others = (cl2.date >= D) & (cl2.game_id != target)
            cl2.loc[others, "line"] += 13.0
            cl2.loc[others, "spread"] += 5.0
            own = cl2.game_id == target
            cl2.loc[own, "actual"] += 77.0                      # the game's own outcome must not enter its features
            cl2.loc[cl2.date >= D, "actual"] += 41.0
            pert = _features(tg2, cl2, prev_tg)
            for col in FEAT_COLS:
                assert _eq(base.loc[target, col], pert.loc[target, col]), (col, str(D), target)
            # y does use the outcome
            assert pert.loc[target, "y"] != base.loc[target, "y"]
    # games BEFORE D are unaffected as well
    D = dates[30]
    tg2 = tg.copy()
    tg2.loc[tg2.date >= D, "pts"] += 50
    pert = _features(tg2, clean, prev_tg)
    early = base.index[base.date < D]
    for col in FEAT_COLS:
        assert _eq(base.loc[early, col].values, pert.loc[early, col].values), col


def test_previous_season_only_enters_f1_fallback():
    tg, clean = make_season(3)
    prev_tg, _ = make_season(4, season="2009-10", start="2009-10-27", gid0=500)
    a = _features(tg, clean, prev_tg)
    prev2 = prev_tg.copy()
    prev2["pts"] += 20
    b = _features(tg, clean, prev2)
    early = a.f1_source == "prev_last30d"
    assert early.any() and (a.loc[early, "f1"] != b.loc[early, "f1"]).all()
    late = ~early
    assert late.any() and _eq(a.loc[late, "f1"].values, b.loc[late, "f1"].values)
    for col in ("f2", "f3", "f4", "f5", "f6", "f7", "f9", "f10"):
        assert _eq(a[col].values, b[col].values)


def test_feature_definitions_toy():
    tg, clean = make_season(5)
    f = _features(tg, clean, None)
    g = clean.set_index("game_id")
    some = f.index[40]
    r = g.loc[some]
    D = r.date
    th = tg[(tg.team == r.home) & (tg.date < D)].sort_values("date")
    ta = tg[(tg.team == r.away) & (tg.date < D)].sort_values("date")
    lg = tg[(tg.venue == "home") & (tg.date < D)]
    assert f.loc[some, "f3"] == (D - tg.date.min()).days
    assert f.loc[some, "f7"] == 0.0
    b2b = lambda t: int(len(t) > 0 and (D - t.date.max()).days == 1)       # noqa: E731
    assert f.loc[some, "f4"] == b2b(th) + b2b(ta)
    if len(th) >= 10 and len(ta) >= 10:
        want = th.pace.tail(10).mean() + ta.pace.tail(10).mean() - 2 * lg.pace.mean()
        assert f.loc[some, "f5"] == pytest.approx(want, abs=1e-9)
    # f1: league mean total of games before D (>= 50 games by then, else NaN fallback absent here)
    allg = L.games_from_team_games(tg)
    prior = allg[allg.date < D]
    if len(prior) >= 50:
        assert f.loc[some, "f1"] == pytest.approx(r.line - prior.actual.mean(), abs=1e-9)
    assert f.loc[some, "f2"] == pytest.approx(abs(r.spread))


def test_altitude_flag_and_missing_spread():
    tg, clean = make_season(8)
    clean2 = clean.copy()
    clean2["home"] = np.where(clean2.home == "T0", "DEN", clean2.home)
    clean2["away"] = np.where(clean2.away == "T0", "DEN", clean2.away)
    clean2.loc[clean2.index[5], "spread"] = np.nan
    sd = FE.SeasonData(tg.assign(team=tg.team.replace({"T0": "DEN"}), opp=tg.opp.replace({"T0": "DEN"})), None, clean2)
    f = FE.season_features(sd, clean2, None).set_index("game_id")
    assert (f.f7 == (clean2.set_index("game_id").home.isin(["DEN", "UTAH"])).astype(float)).all() and f.f7.sum() > 0
    assert np.isnan(f.f2.iloc[5])


def test_f6_f10_use_only_clean_line_games():
    tg, clean = make_season(6)
    drop = clean.sample(frac=0.3, random_state=1).game_id
    f_all = _features(tg, clean, None)
    f_drop = _features(tg, clean[~clean.game_id.isin(drop)], None)
    common = f_drop.index
    assert len(common) < len(f_all)
    # with fewer lined games, windows reach further back, so values differ somewhere but are always finite or NaN-consistent
    assert f_drop.f6.notna().sum() <= f_all.f6.reindex(common).notna().sum() + len(common)
    gid = common[60]
    r = clean.set_index("game_id").loc[gid]
    ch = clean[(clean.home == r.home) | (clean.away == r.home)]
    ch = ch[(ch.date < r.date) & ~ch.game_id.isin(drop)].sort_values("date")
    if len(ch) >= 5:
        want = (ch.actual - ch.line).tail(5).mean()
        assert f_drop.loc[gid, "form5_home"] == pytest.approx(want)


# ------------------------------------------------------------------ f8 on real data: no look-ahead, #56 reproduction
@pytest.mark.slow
def test_f8_no_lookahead_real_discovery_season():
    import fa_ext as fx
    S = "2009-10"
    D = pd.Timestamp("2010-01-15")
    base, _ = fx.project_season(S)

    def reader(year):
        tb = L.read_year_fixed(year)
        if year != 2010:
            return tb
        et = pd.to_datetime(tb.game_date_time).dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
        late = (et >= D).values
        tb = tb.copy()
        for c in ("team_score", "opponent_team_score", "field_goals_made", "field_goals_attempted", "free_throws_attempted", "offensive_rebounds"):
            tb[c] = tb[c].astype(float)
            tb.loc[late, c] = (tb.loc[late, c] * 1.5 + 3).values
        return tb

    pert, _ = fx.project_season(S, reader=reader)
    gm = L.games_from_team_games(L.team_games_from_box(L.read_year(2010), S))
    early = gm[gm.date <= D].game_id
    b = base.set_index("game_id").proj.reindex(early)
    p = pert.set_index("game_id").proj.reindex(early)
    assert len(b) > 300 and b.notna().all()
    assert np.array_equal(b.values, p.values)
    late = gm[gm.date > D + pd.Timedelta(days=20)].game_id          # the perturbation really did change later games' inputs
    bl, pl = base.set_index("game_id").proj.reindex(late), pert.set_index("game_id").proj.reindex(late)
    assert (bl.values != pl.values).mean() > 0.9


@pytest.mark.slow
def test_f8_reproduces_56_exactly_one_season():
    import fa_ext as fx
    S = "2024-25"
    w56 = fx.fa.build_world(S)
    g56 = fx.fa.games_frame(w56)
    p56 = fx.gl.project_one(w56.season, g56, w56.consts, fx.gl.spec_from_flat(7791))
    pe, _ = fx.project_season(S)
    assert np.array_equal(pe.proj.values, p56)
    assert fx.gl.flat_from_spec(fx.SPEC) == 7791


# ------------------------------------------------------------------ ridge, CV by season, decile rules
def test_season_folds_never_split_a_season():
    seasons = np.repeat([f"s{i}" for i in range(11)], 7)
    fold = L.season_folds(seasons, 5)
    for s in set(seasons):
        assert len(set(fold[seasons == s])) == 1
    assert set(fold) == {0, 1, 2, 3, 4} and np.bincount(fold[::7]).tolist() == [3, 2, 2, 2, 2]


def test_ridge_matches_closed_form_and_cv_picks_sensible_lambda():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(500, 3))
    y = 3 * X[:, 0] + rng.normal(size=500)
    mu, sd = L.standardise_fit(X)
    Xs = (X - mu) / sd
    b0, b = L.ridge_fit(Xs, y, 0.0)
    ref = np.linalg.lstsq(np.column_stack([np.ones(500), Xs]), y, rcond=None)[0]
    assert b0 == pytest.approx(ref[0]) and np.allclose(b, ref[1:])
    b0r, br = L.ridge_fit(Xs, y, 1e6)
    assert np.abs(br).max() < 1e-2
    seasons = np.repeat([f"s{i}" for i in range(10)], 50)
    lam_sig, cv = L.cv_lambda(X, y, seasons)
    assert lam_sig <= 100.0
    ynoise = rng.normal(size=500)
    lam_noise, _ = L.cv_lambda(X, ynoise, seasons)
    _, cv_noise = L.cv_lambda(X, ynoise, seasons)
    assert lam_noise >= lam_sig and cv_noise[1e5] <= cv_noise[0.1] + 0.01      # noise wants at least as much shrinkage as signal
    assert cv[1e5] > cv[1.0]                                   # ... but is much worse when there is signal
    pred = L.ridge_predict(X, mu, sd, b0, b)
    assert np.allclose(pred, b0 + Xs @ b)


def test_cv_standardises_inside_training_folds():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(300, 2))
    X[:60, 0] += 1000.0                          # one season has a wildly different level
    y = X[:, 1] + rng.normal(size=300)
    seasons = np.repeat([f"s{i}" for i in range(5)], 60)
    lam, cv = L.cv_lambda(X, y, seasons, lambdas=(1.0,))
    assert np.isfinite(cv[1.0])


def test_decile_rule_direction_and_tails():
    x = np.arange(1000, dtype=float)
    y = np.where(x <= np.quantile(x, 0.1), 4.0, np.where(x >= np.quantile(x, 0.9), -3.0, 0.5))
    cuts = L.decile_cuts(x)
    assert len(cuts) == 9 and cuts[0] == pytest.approx(np.quantile(x, 0.1))
    bot, top = L.tail_masks(x, cuts[0], cuts[-1])
    assert bot.sum() == 100 + 0 or bot.sum() in (100, 101)
    assert y[bot].mean() > 0 > y[top].mean()
    tab = L.decile_table(x, y)
    assert len(tab) == 10 and tab[0]["mean_y"] > 0 > tab[-1]["mean_y"]
    D = pd.DataFrame({f: np.random.default_rng(i).normal(size=1000) for i, f in enumerate(L.FEATS)})
    D["f3"] = x
    D["y"] = y
    D["season"] = np.repeat([f"s{i}" for i in range(10)], 100)
    res = RS.evaluate_versions(D)
    bottom, top_ = res["versions"]["U_f3_bottom"], res["versions"]["U_f3_top"]
    assert bottom["rule"]["sign"] == 1.0 and top_["rule"]["sign"] == -1.0
    assert bottom["rate"] == 1.0 and top_["rate"] == 1.0                 # follows the tail's own mean sign, y is +4 / -3 there
    # apply_rule agrees with the stats
    fz = {"rule": bottom["rule"]}
    side = L.apply_rule(fz, D)
    assert L.side_stats(side, D.y.values)["n"] == bottom["n"]


def test_continuous_tails_inclusive():
    x = np.arange(100.0)
    cuts = L.decile_cuts(x)
    bot, top = L.tail_masks(x, cuts[0], cuts[-1])
    assert bot.sum() == 10 and top.sum() == 10


# ------------------------------------------------------------------ selection / STOP / validation gates
def _noise_D(n=3300, seed=0):
    rng = np.random.default_rng(seed)
    D = pd.DataFrame({f: rng.normal(size=n) for f in L.FEATS})
    D["y"] = rng.choice([-9.0, 9.0], n)
    D["season"] = np.array([f"2008-{i % 11:02d}" for i in np.sort(np.arange(n) * 11 // n)])
    return D


def test_stop_rule_on_pure_noise_and_eligibility():
    res = RS.evaluate_versions(_noise_D())
    assert res["stop"] is True and res["selected"] is None and "no line error" in res["stop_reason"]
    assert len(res["versions"]) == 23
    small = RS.evaluate_versions(_noise_D(n=1100))                 # tails of 110 rows each -> nothing is eligible (n >= 300)
    assert small["stop"] and small["stop_reason"] == "no version has n >= 300"


def test_selection_picks_highest_lb_and_per_season_table():
    D = _noise_D()
    rng = np.random.default_rng(3)
    D["y"] = np.where(D.f2 > np.quantile(D.f2, 0.9), 6.0, D.y)            # planted: top decile of f2 always over
    D.loc[D.f2 > np.quantile(D.f2, 0.9), "y"] = np.where(rng.random((D.f2 > np.quantile(D.f2, 0.9)).sum()) < 0.9, 6.0, -6.0)
    res = RS.evaluate_versions(D)
    assert res["stop"] is False and res["selected"] == "U_f2_top"
    assert res["best_lb"] > L.BREAK_EVEN and res["versions"]["U_f2_top"]["rule"]["sign"] == 1.0
    assert set(res["per_season_selected"]) == set(D.season)


def test_validation_stop_gates():
    D = _noise_D(n=600, seed=1)
    D["y"] = 5.0
    fz = {"version": "toy", "rule": {"kind": "U", "feature": "f1", "tail": "top", "q10": -10.0, "q90": -9.0, "sign": 1.0}}
    D["f1"] = -8.5
    ok = RV.validate_core(fz, D)
    assert ok["continue"] and ok["pooled"]["n"] == 600
    few = RV.validate_core(fz, D.head(100))
    assert not few["continue"] and "n = 100" in few["stop_reason"]
    D2 = D.copy()
    D2["y"] = np.where(np.arange(600) % 2 == 0, 5.0, -5.0)                  # exactly 50% -> not above 0.5238
    bad = RV.validate_core(fz, D2)
    assert not bad["continue"] and "side rate" in bad["stop_reason"]
    D3 = D.copy()
    D3["y"] = np.where(np.arange(600) % 100 < 52, 5.0, -5.0)               # 52% < 52.38%
    assert not RV.validate_core(fz, D3)["continue"]
    D4 = D.copy()
    D4["y"] = np.where(np.arange(600) % 100 < 53, 5.0, -5.0)               # 53% > 52.38%
    assert RV.validate_core(fz, D4)["continue"]


def test_m_rule_apply_and_tau():
    rng = np.random.default_rng(0)
    F = pd.DataFrame({f: rng.normal(size=50) for f in L.FEATS})
    F.loc[3, "f4"] = np.nan
    rule = {"kind": "M", "tau": 1.0, "b0": 0.0, "coef": [2.0] + [0.0] * 9, "mu": [0.0] * 10, "sd": [1.0] * 10}
    side = L.apply_rule({"rule": rule}, F)
    pred = 2.0 * F.f1.values
    want = np.where(pred >= 1, 1.0, np.where(pred <= -1, -1.0, 0.0))
    want[3] = 0.0
    assert np.array_equal(side, want)


# ------------------------------------------------------------------ y is read only after the filter / only for requested seasons
def test_y_only_read_for_requested_seasons(tmp_path):
    rows = []
    for s, n in (("2010-11", 5), ("2015-16", 5), ("2023-24", 5)):
        for i in range(n):
            rows.append({"game_id": hash((s, i)) % 10**9, "season": s, "date": pd.Timestamp("2020-01-01"), "line": 200.0, "line_source": "closing_lines", "actual": 999.0,
                         "y": 123456.0 if s == "2023-24" else 1.0, **{f: float(i) for f in L.FEATS}})
    p = tmp_path / "f.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    RS.READ_LOG.clear()
    D = RS.load_stage(L.DISCOVERY, p)
    assert set(D.season) == {"2010-11", "2015-16"} and (D.y == 1.0).all()
    assert all(set(seasons) == set(L.DISCOVERY) for _, seasons in RS.READ_LOG)
    assert all("actual" not in cols and "line" not in cols for cols, _ in RS.READ_LOG)
    first_y = [i for i, (cols, _) in enumerate(RS.READ_LOG) if "y" in cols]
    first_x = [i for i, (cols, _) in enumerate(RS.READ_LOG) if "f1" in cols]
    assert min(first_x) < min(first_y)                                      # features are read before y
    assert 123456.0 not in D.y.values


# ------------------------------------------------------------------ refusal gates and hashes
def _study(tmp_path):
    d = tmp_path / "study"
    d.mkdir()
    return d


def test_agreement_gate(tmp_path, monkeypatch):
    d = _study(tmp_path)
    with pytest.raises(SystemExit, match="AGREEMENT.md not found"):
        L.check_agreement_pass(d)
    (d / "features_a.parquet").write_bytes(b"abc")
    h = L.sha256_file(d / "features_a.parquet")
    (d / "AGREEMENT.md").write_text(f"STATUS: FAIL\nA_SHA256: {h}\n")
    with pytest.raises(SystemExit, match="does not say STATUS: PASS"):
        L.check_agreement_pass(d)
    (d / "AGREEMENT.md").write_text("STATUS: PASS\nA_SHA256: deadbeef\n")
    with pytest.raises(SystemExit, match="changed since"):
        L.check_agreement_pass(d)
    (d / "AGREEMENT.md").write_text(f"STATUS: PASS\nA_SHA256: {h}\n")
    L.check_agreement_pass(d)
    # run_select.main refuses before touching any data when the gate fails
    monkeypatch.setattr(L, "STUDY", d)
    (d / "AGREEMENT.md").write_text("STATUS: FAIL\n")
    with pytest.raises(SystemExit, match="REFUSED"):
        RS.main()


def test_select_refuses_second_run(tmp_path, monkeypatch):
    d = _study(tmp_path)
    (d / "features_a.parquet").write_bytes(b"abc")
    h = L.sha256_file(d / "features_a.parquet")
    (d / "AGREEMENT.md").write_text(f"STATUS: PASS\nA_SHA256: {h}\n")
    (d / "frozen.json").write_text("{}")
    monkeypatch.setattr(L, "STUDY", d)
    with pytest.raises(SystemExit, match="selection runs once"):
        RS.main()


def test_frozen_hash_check(tmp_path):
    d = _study(tmp_path)
    with pytest.raises(SystemExit, match="frozen.json not found"):
        L.check_frozen_hash(d)
    blob = json.dumps({"version": "x"}).encode()
    (d / "frozen.json").write_bytes(blob)
    with pytest.raises(SystemExit, match="frozen.sha256 not found"):
        L.check_frozen_hash(d)
    (d / "frozen.sha256").write_text(L.sha256_bytes(blob) + "  frozen.json\n")
    assert L.check_frozen_hash(d)["version"] == "x"
    (d / "frozen.json").write_bytes(blob + b" ")
    with pytest.raises(SystemExit, match="hash"):
        L.check_frozen_hash(d)


def test_test_stage_gates(tmp_path):
    d = _study(tmp_path)
    base = tmp_path / "code"
    base.mkdir()
    files = ("a.py", "b.py")
    for f in files:
        (base / f).write_text(f"# {f}")
    blob = json.dumps({"version": "x"}).encode()
    (d / "frozen.json").write_bytes(blob)
    (d / "frozen.sha256").write_text(L.sha256_bytes(blob))
    gate = lambda: RT.check_gates(d, base, files)                                   # noqa: E731
    with pytest.raises(SystemExit, match="results_select"):
        gate()
    (d / "results_select.json").write_text(json.dumps({"stop": False}))
    with pytest.raises(SystemExit, match="results_validate.json not found"):
        gate()
    (d / "results_validate.json").write_text(json.dumps({"continue": False}))
    with pytest.raises(SystemExit, match="does not say continue"):
        gate()
    (d / "results_validate.json").write_text(json.dumps({"continue": True}))
    with pytest.raises(SystemExit, match="AUDIT_OK not found"):
        gate()
    (d / "AUDIT_OK").write_text("not json")
    with pytest.raises(SystemExit, match="not JSON"):
        gate()
    (d / "AUDIT_OK").write_text(json.dumps({"sha256": {"a.py": L.sha256_file(base / "a.py")}}))
    with pytest.raises(SystemExit, match="does not cover b.py"):
        gate()
    (d / "AUDIT_OK").write_text(json.dumps(L.audit_hashes(base, files)))
    assert gate()["version"] == "x"
    (base / "b.py").write_text("# changed")
    with pytest.raises(SystemExit, match="hash mismatch"):
        gate()
    (base / "b.py").write_text("# b.py")
    RT.start_test(d)
    assert (d / "test_started.json").exists()
    with pytest.raises(SystemExit, match="test_started.json exists"):
        gate()
    (d / "test_started.json").unlink()
    (d / "results_test.json").write_text("{}")
    with pytest.raises(SystemExit, match="results_test.json exists"):
        gate()
    (d / "results_validate.json").write_text(json.dumps({"continue": True}))
    (d / "results_select.json").write_text(json.dumps({"stop": True}))
    (d / "results_test.json").unlink()
    with pytest.raises(SystemExit, match="STOP"):
        gate()


def test_run_test_writes_marker_before_loading_outcomes(monkeypatch, tmp_path):
    d = _study(tmp_path)
    order = []
    monkeypatch.setattr(L, "STUDY", d)
    monkeypatch.setattr(RT, "check_gates", lambda study: {"version": "x", "rule": {}})
    monkeypatch.setattr(RT, "start_test", lambda study: order.append("marker"))
    monkeypatch.setattr(RS, "load_stage", lambda seasons, path=None: order.append("load") or (_ for _ in ()).throw(RuntimeError("stop here")))
    with pytest.raises(RuntimeError):
        RT.main()
    assert order == ["marker", "load"]


def test_audit_files_cover_dependencies_and_exist():
    for f in L.AUDIT_FILES:
        assert (HERE / f).exists(), f
    for need in ("run_test.py", "run_select.py", "run_validate.py", "features.py", "clean.py", "lea_lib.py", "fa_ext.py",
                 "../../nba_totals_grid/grid_bets.py", "../../nba_formula_accuracy/fa_lib.py"):
        assert need in L.AUDIT_FILES
    h = L.audit_hashes()["sha256"]
    assert set(h) == set(L.AUDIT_FILES) and all(len(v) == 64 for v in h.values())


# ------------------------------------------------------------------ comparison
def test_compare_frames():
    A = pd.DataFrame({"game_id": [1, 2, 3], "line": [200.0, 210.0, 220.0], "actual": [210.0, 200.0, 225.0], "y": [10.0, -10.0, 5.0],
                      **{f: [1.0, 2.0, np.nan] for f in L.FEATS}})
    B = A.copy()
    assert CMP.compare_frames(A, B)["pass"]
    B2 = A.copy()
    B2.loc[0, "f5"] += 5e-7
    assert CMP.compare_frames(A, B2)["pass"]
    B2.loc[0, "f5"] += 2e-6
    r = CMP.compare_frames(A, B2)
    assert not r["pass"] and r["mismatching_columns"] == ["f5"]
    B3 = A.copy()
    B3.loc[1, "f4"] += 1e-9                                 # counts / flags must be exact
    assert CMP.compare_frames(A, B3)["mismatching_columns"] == ["f4"]
    B4 = A.copy()
    B4.loc[2, "f9"] = 1.0                                   # NaN vs value
    assert CMP.compare_frames(A, B4)["columns"]["f9"]["nan_pattern_mismatch"] == 1
    B5 = A.iloc[:2].copy()
    r5 = CMP.compare_frames(A, B5)
    assert r5["only_in_a"] == [3] and not r5["pass"]
    B6 = A.assign(y=A.y + 1e-12)
    assert "y" in CMP.compare_frames(A, B6)["mismatching_columns"]       # y must be exact


# ------------------------------------------------------------------ Kalshi bet machinery (#42), fees and fills
def test_fee_and_main_strike_fill():
    sys.path.insert(0, str(L.ROOT / "reports" / "output" / "nba_totals_grid"))
    import grid_bets as gb
    assert gb.fee1(0.5) == pytest.approx(0.0175, abs=1e-6)
    assert gb.fee1(0.3) == pytest.approx(0.07 * 0.3 * 0.7, abs=2e-6)
    games = pd.DataFrame({"event": ["E1", "E2"]})
    s = pd.DataFrame({"event": ["E1", "E1", "E1", "E2"], "ticker": ["a", "b", "c", "d"], "strike": [220.5, 224.5, 228.5, 230.5],
                      "bid": [0.80, 0.52, 0.20, 0.45], "ask": [0.84, 0.56, 0.24, 0.49], "y": [1.0, 0.0, 0.0, 1.0]})
    bt = gb.main_strike_table(games, s)
    assert list(bt.K) == [224.5, 230.5]                                     # mid nearest 0.50
    r0 = bt.iloc[0]
    assert r0.price_over == pytest.approx(0.56) and r0.price_under == pytest.approx(0.48)
    assert r0.net_over == pytest.approx(0 - 0.56 - 0.01 - gb.fee1(0.56))     # ask + 1c + fee at the ask; y = 0 -> over loses
    assert r0.net_under == pytest.approx(1 - 0.48 - 0.01 - gb.fee1(0.48))
    sides = {101: 1.0, 102: -1.0}
    res = RT.kalshi_from_bt(bt, sides, [101, 102], boot_n=200, coin_n=20)
    assert res["n"] == 2
    expect = (r0.net_over + bt.iloc[1].net_under) / 2
    assert res["mean_net"] == pytest.approx(expect)
    res0 = RT.kalshi_from_bt(bt, {}, [101, 102], boot_n=200, coin_n=20)
    assert res0["n"] == 0                                                   # side 0 -> proj == K -> no bet


def test_verdict():
    sb = {"pass": True}
    k = {"n": 200, "mean_net": 0.02, "boot_ci95": [0.001, 0.04], "coin_flip": {"frac_copies_ge_actual": 0.04}}
    assert RT.verdict(sb, k)["EDGE"]
    assert not RT.verdict(sb, {**k, "boot_ci95": [-0.01, 0.04]})["EDGE"]
    assert not RT.verdict(sb, {**k, "n": 149})["EDGE"]
    assert not RT.verdict(sb, {**k, "coin_flip": {"frac_copies_ge_actual": 0.06}})["EDGE"]
    assert not RT.verdict({"pass": False}, k)["EDGE"]
    assert RT.side_test({"rule": {"kind": "U", "feature": "f1", "tail": "top", "q10": 0, "q90": 0.5, "sign": 1.0}},
                        pd.DataFrame({**{f: [1.0] * 400 for f in L.FEATS}, "y": [3.0] * 300 + [-3.0] * 100, "season": "2022-23"}))["pass"]


def test_discrete_features_use_min_max_tails():
    x = np.array([0, 0, 0, 0, 0, 1, 1, 1, 2, 2], float)
    assert L.is_discrete(x) and not L.is_discrete(np.arange(25.0))
    r = {"discrete": True, "xmin": 0.0, "xmax": 2.0}
    bot, top = L.tail_masks_rule(x, r)
    assert bot.sum() == 5 and top.sum() == 2 and not (bot & top).any()
    rng = np.random.default_rng(0)
    D = pd.DataFrame({f: rng.normal(size=1100) for f in L.FEATS})
    D["f7"] = (rng.random(1100) < 0.07).astype(float)
    D["f4"] = rng.integers(0, 3, 1100).astype(float)
    D["y"] = rng.choice([-9.0, 9.0], 1100)
    D["season"] = np.array([f"2008-{i % 11:02d}" for i in np.sort(np.arange(1100) * 11 // 1100)])
    res = RS.evaluate_versions(D)
    v = res["versions"]
    assert res["cuts"]["f7"]["discrete"] and not res["cuts"]["f1"]["discrete"]
    assert v["U_f7_top"]["tail_n_rows"] == int((D.f7 == 1).sum()) and v["U_f7_bottom"]["tail_n_rows"] == int((D.f7 == 0).sum())
    assert v["U_f4_top"]["tail_n_rows"] == int((D.f4 == 2).sum())
    assert v["U_f1_top"]["rule"].get("discrete") is False
    side = L.apply_rule({"rule": {**v["U_f7_top"]["rule"], "sign": 1.0}}, D)
    assert int((side != 0).sum()) == int((D.f7 == 1).sum())


def test_loso_predictions_are_out_of_fold():
    rng = np.random.default_rng(2)
    n = 400
    X = rng.normal(size=(n, 3))
    seasons = np.repeat([f"s{i}" for i in range(8)], 50)
    y = rng.normal(size=n)
    y[seasons == "s3"] += 1000.0
    pred = L.loso_predict(X, y, seasons, 1.0)
    assert np.abs(pred[seasons == "s3"].mean()) < 50          # s3's own y never enters its predictions
    assert pred[seasons == "s0"].mean() > 50                  # other seasons are pulled up by s3
    tr = seasons != "s5"
    mu, sd = L.standardise_fit(X[tr])
    b0, b = L.ridge_fit((X[tr] - mu) / sd, y[tr], 1.0)
    assert np.allclose(pred[~tr], L.ridge_predict(X[~tr], mu, sd, b0, b))
    res = RS.evaluate_versions(_noise_D())
    assert "pred_sd_oof" in res["ridge"]


def test_compare_resolution_logic():
    A = pd.DataFrame({"game_id": [1, 2, 3], "line": [1.0] * 3, "actual": [2.0] * 3, "y": [1.0] * 3, **{f: [1.0] * 3 for f in L.FEATS}})
    B = A.copy()
    B.loc[0, "f2"] = np.nan
    A.loc[0, "f2"] = 0.0
    B["f8"] = 5.0
    B.loc[1, "f5"] = 2.0
    r = CMP.compare_frames(A, B)
    assert not r["pass"] and CMP.resolved(r, A, B)[0]
    B.loc[2, "f1"] = 9.0
    assert not CMP.resolved(CMP.compare_frames(A, B), A, B)[0]
