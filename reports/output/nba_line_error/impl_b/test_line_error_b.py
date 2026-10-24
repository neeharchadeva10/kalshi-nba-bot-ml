"""Tests for implementation B (synthetic / toy data only; no real outcomes are inspected)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_b as L  # noqa: E402


# ---------------------------------------------------------------- synthetic league
def synth_games(seed=0, seasons=("2008-09", "2009-10", "2010-11"), n_days=50, n_teams=6, start_year=2008):
    rng = np.random.default_rng(seed)
    rows, gid = [], 1000
    for si, s in enumerate(seasons):
        d0 = pd.Timestamp(year=start_year + si, month=10, day=25)
        for day in range(n_days):
            D = d0 + pd.Timedelta(days=day)
            if rng.random() < 0.15:
                continue
            teams = rng.permutation(np.arange(1, n_teams + 1))
            for j in range(0, n_teams, 2):
                h, a = int(teams[j]), int(teams[j + 1])
                gid += 1
                pace = rng.normal(95, 4)
                hp, ap = rng.normal(108, 9), rng.normal(105, 9)
                hp, ap = round(hp), round(ap)
                rows.append(dict(game_id=gid, season=s, season_type=2, date=D, home_id=h, away_id=a,
                                 home=f"T{h}", away=f"T{a}", home_pts=float(hp), away_pts=float(ap), actual=float(hp + ap),
                                 pace=float(pace), home_tpar=float(rng.uniform(.3, .4)), away_tpar=float(rng.uniform(.3, .4))))
    g = pd.DataFrame(rows).sort_values(["date", "game_id"]).reset_index(drop=True)
    return g


def synth_clean(g, seed=1):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"game_id": g.game_id, "line": np.round(rng.normal(213, 6, len(g)) * 2) / 2,
                         "line_source": "x", "spread": np.round(rng.uniform(0, 12, len(g)) * 2) / 2})


FEATS = ["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f9", "f10", "f5_alt", "f9_alt"]


def test_no_lookahead_features():
    g = synth_games()
    c = synth_clean(g)
    base = L.compute_features(g, c).set_index("game_id")
    # pick a "future" game in the middle of season 2 and corrupt EVERYTHING about it
    s2 = g[g.season == "2009-10"]
    victim = s2.iloc[len(s2) // 2]
    g2, c2 = g.copy(), c.copy()
    m = g2.game_id == victim.game_id
    g2.loc[m, ["home_pts", "away_pts", "actual", "pace", "home_tpar", "away_tpar"]] = [300.0, 10.0, 310.0, 150.0, 0.9, 0.9]
    c2.loc[c2.game_id == victim.game_id, ["line", "spread"]] = [250.0, 40.0]
    alt = L.compute_features(g2, c2).set_index("game_id")
    before = g[g.date <= victim.date].game_id  # same date included: only dates < D may be used
    same_day_or_before = [x for x in before if x != victim.game_id]
    pd.testing.assert_frame_equal(base.loc[same_day_or_before, FEATS], alt.loc[same_day_or_before, FEATS])
    after = g[g.date > victim.date].game_id
    assert not np.allclose(base.loc[after, FEATS].fillna(0).values, alt.loc[after, FEATS].fillna(0).values)  # sanity: it does matter later


def test_no_lookahead_all_games_dropped_after():
    """Features for a season-2 game do not change if everything after its date is deleted."""
    g = synth_games(seed=3)
    c = synth_clean(g)
    base = L.compute_features(g, c).set_index("game_id")
    cut = g[g.season == "2009-10"].iloc[40]
    g_cut = g[g.date < cut.date + pd.Timedelta(days=1)].copy()   # keep games up to and including the cut date
    c_cut = c[c.game_id.isin(g_cut.game_id)]
    part = L.compute_features(g_cut, c_cut).set_index("game_id")
    # the last-30-days-of-previous-season logic uses only seasons earlier than the game's own, so unaffected
    ids = [x for x in part.index if x in base.index]
    pd.testing.assert_frame_equal(base.loc[ids, FEATS], part.loc[ids, FEATS])


def test_f8_no_lookahead():
    g = synth_games(seed=5)
    sg = {s: g[g.season == s].reset_index(drop=True) for s in sorted(g.season.unique())}
    stg = {s: L.team_game_table(sg[s]) for s in sg}
    S, S1, S2 = "2010-11", "2009-10", "2008-09"
    base, _, b0 = L.f8_for_season(sg, stg, S, S1, S2)
    victim = sg[S].iloc[len(sg[S]) // 2]
    sg2 = dict(sg)
    gS = sg[S].copy()
    gS.loc[gS.game_id == victim.game_id, ["home_pts", "away_pts", "actual", "pace"]] = [400.0, 5.0, 405.0, 180.0]
    sg2[S] = gS
    stg2 = dict(stg)
    stg2[S] = L.team_game_table(gS)
    alt, _, b1 = L.f8_for_season(sg2, stg2, S, S1, S2)
    assert b0 == b1                                   # b is fitted on S-1 only
    idx = (sg[S].date <= victim.date).values & (sg[S].game_id != victim.game_id).values
    np.testing.assert_allclose(base[idx], alt[idx], rtol=0, atol=1e-12)
    assert not np.allclose(base[~idx], alt[~idx], equal_nan=True)


def test_f8_shrink_constant_uses_only_previous_season():
    g = synth_games(seed=7)
    sg = {s: g[g.season == s].reset_index(drop=True) for s in sorted(g.season.unique())}
    stg = {s: L.team_game_table(sg[s]) for s in sg}
    _, _, b0 = L.f8_for_season(sg, stg, "2010-11", "2009-10", "2008-09")
    sg2 = dict(sg); stg2 = dict(stg)
    sg2["2010-11"] = sg["2010-11"].assign(home_pts=sg["2010-11"].home_pts + 50)
    stg2["2010-11"] = L.team_game_table(sg2["2010-11"])
    _, _, b1 = L.f8_for_season(sg2, stg2, "2010-11", "2009-10", "2008-09")
    assert b0 == b1


# ---------------------------------------------------------------- formulas on hand-computed examples
def test_possession_formula_hand():
    # 0.96 * (80 - 10 + 12 + 0.44*20) = 0.96 * 90.8 = 87.168
    assert L.possessions(80, 10, 12, 20) == pytest.approx(87.168, abs=1e-12)


def test_game_pace_is_mean_of_both_teams():
    tg = pd.DataFrame([
        dict(game_id=1, season="2008-09", season_type=2, date=pd.Timestamp("2008-11-01"), team_id=1, opp_id=2, abbr="A", opp_abbr="B",
             is_home=True, pts=100., opp_pts=90., fga=80., oreb=10., tov=12., fta=20., tpa=20., poss_own=87.168),
        dict(game_id=1, season="2008-09", season_type=2, date=pd.Timestamp("2008-11-01"), team_id=2, opp_id=1, abbr="B", opp_abbr="A",
             is_home=False, pts=90., opp_pts=100., fga=70., oreb=8., tov=10., fta=10., tpa=28., poss_own=L.possessions(70, 8, 10, 10))])
    g = L.build_games(tg)
    own_b = 0.96 * (70 - 8 + 10 + 4.4)
    assert g.pace.iloc[0] == pytest.approx((87.168 + own_b) / 2)
    assert g.actual.iloc[0] == 190
    assert g.home_tpar.iloc[0] == pytest.approx(0.25) and g.away_tpar.iloc[0] == pytest.approx(0.4)


def test_tov_series_eras():
    early = pd.DataFrame({"turnovers": [14, 10], "team_turnovers": [14, 10], "total_turnovers": [28, 20]})
    late = pd.DataFrame({"turnovers": [13, 10], "team_turnovers": [1, 0], "total_turnovers": [14, 10]})
    nulls = pd.DataFrame({"turnovers": [13, 10], "team_turnovers": [np.nan] * 2, "total_turnovers": [np.nan] * 2})
    assert L._tov_series(early).tolist() == [14, 10]
    assert L._tov_series(late).tolist() == [14, 10]
    assert L._tov_series(nulls).tolist() == [13, 10]


def test_decay_mean_and_split_hand():
    v = np.array([[1.0], [3.0]])
    w_old = 0.5 ** (1 / 5)
    assert L.decay_mean(v)[0] == pytest.approx((w_old * 1 + 3) / (w_old + 1))
    assert L.decay_mean(np.array([[2.0]]))[0] == 2.0


def test_project_total_modes_hand():
    a, h = (0.5, 0.6, 90.0), (0.55, 0.45, 100.0)
    # own: away (0.5*0.5+0.5*0.45)*90 = 42.75 ; home (0.5*0.55+0.5*0.6)*100 = 57.5
    assert L.project_total(a, h, 0.5, "own") == pytest.approx(42.75 + 57.5)
    # avg pace 95
    assert L.project_total(a, h, 0.5, "avg") == pytest.approx((0.475 + 0.575) * 95)
    # term-own: 0.5*0.5*90 + 0.5*0.45*100 + 0.5*0.55*100 + 0.5*0.6*90
    assert L.project_total(a, h, 0.5, "termown") == pytest.approx(22.5 + 22.5 + 27.5 + 27.0)


def test_shrink_slope_recovers_known_slope():
    lt = np.full(50, 200.0)
    proj = 200 + np.linspace(-10, 10, 50)
    actual = 200 + 0.4 * (proj - 200) + 3.0
    assert L.fit_shrink_slope(proj, lt, actual) == pytest.approx(0.4)


def test_rating_prior_used_only_when_no_games_k0():
    g = synth_games(seed=9)
    sg = {s: g[g.season == s].reset_index(drop=True) for s in sorted(g.season.unique())}
    stg = {s: L.team_game_table(sg[s]) for s in sg}
    sr = L.SeasonRatings(stg["2009-10"], sg["2009-10"], stg["2008-09"])
    first = sg["2009-10"].date.min()
    t = int(sg["2009-10"].iloc[0].home_id)
    r0 = sr.team_rating(t, first, 0, 1.0, 1.0, None)       # no games yet -> prior
    pr = L.decay_mean(stg["2008-09"][stg["2008-09"].team == t].sort_values(["date", "game_id"])[["O", "D", "P"]].values)
    np.testing.assert_allclose(r0, pr)
    later = first + pd.Timedelta(days=20)
    cur = stg["2009-10"][(stg["2009-10"].team == t) & (stg["2009-10"].date < later)].sort_values(["date", "game_id"])
    np.testing.assert_allclose(sr.team_rating(t, later, 0, 1.0, 1.0, None), L.decay_mean(cur[["O", "D", "P"]].values))


# ---------------------------------------------------------------- simple features by hand
def tiny_league():
    # 4 teams, 2 days; dates chosen so back-to-back / days-since-first are known
    rows = [
        (1, "2008-09", "2008-10-28", 1, 2, 100, 100),
        (2, "2008-09", "2008-10-28", 3, 4, 110, 100),
        (3, "2008-09", "2008-10-29", 1, 3, 100, 120),   # team 1 and 3 both played the previous day
        (4, "2008-09", "2008-10-31", 7, 4, 90, 90),     # fake ids ok for features
    ]
    d = []
    for gid, s, dt, h, a, hp, ap in rows:
        d.append(dict(game_id=gid, season=s, season_type=2, date=pd.Timestamp(dt), home_id=h, away_id=a, home=f"T{h}", away=f"T{a}",
                      home_pts=float(hp), away_pts=float(ap), actual=float(hp + ap), pace=95.0, home_tpar=.3, away_tpar=.35))
    return pd.DataFrame(d)


def test_f3_f4_f7_hand():
    g = tiny_league()
    c = pd.DataFrame({"game_id": [1, 2, 3, 4], "line": [200.0, 210.0, 225.0, 180.0], "line_source": "x", "spread": [1.0, 2.0, 3.0, 4.0]})
    f = L.compute_features(g, c).set_index("game_id")
    assert f.loc[1, "f3"] == 0 and f.loc[3, "f3"] == 1 and f.loc[4, "f3"] == 3
    assert f.loc[1, "f4"] == 0
    assert f.loc[3, "f4"] == 2                          # both teams played on 10-28
    assert f.loc[4, "f4"] == 0
    assert f.loc[4, "f7"] == 1.0 and f.loc[3, "f7"] == 0.0     # home id 7 = DEN
    assert f.loc[3, "f2"] == 3.0


def test_f1_uses_prior_season_last30_when_under_50_games():
    g = synth_games(seed=2, n_days=12)
    # make the last 30 days of 2008-09 hold a known mean total: all those games total 200
    m = g.season == "2008-09"
    g.loc[m, ["home_pts", "away_pts", "actual"]] = [100.0, 100.0, 200.0]
    c = synth_clean(g)
    f = L.compute_features(g, c).merge(c, on="game_id").merge(g[["game_id", "season"]], on="game_id")
    row = f[f.season == "2009-10"].iloc[0]
    assert row.f1 == pytest.approx(row.line - 200.0)


def test_f1_uses_season_to_date_after_50_games():
    g = synth_games(seed=4, n_days=40)
    c = synth_clean(g)
    f = L.compute_features(g, c).set_index("game_id")
    r = g[g.season == "2009-10"].sort_values(["date", "game_id"])
    # find a game with >= 50 strictly-earlier games this season
    for rr in r.itertuples():
        prior = r[r.date < rr.date]
        if len(prior) >= 50:
            expect = c.set_index("game_id").loc[rr.game_id, "line"] - prior.actual.mean()
            assert f.loc[rr.game_id, "f1"] == pytest.approx(expect)
            break
    else:
        pytest.fail("no game with 50 prior games")


def test_f5_f9_f6_f10_windows_hand():
    # one team (id 1) plays home vs rotating opponent every day; check last-10 uses only 10 earlier games, full window needed
    rows = []
    for i in range(14):
        rows.append(dict(game_id=i + 1, season="2008-09", season_type=2, date=pd.Timestamp("2008-11-01") + pd.Timedelta(days=i),
                         home_id=1, away_id=2 + (i % 2), home="A", away="B", home_pts=100.0 + i, away_pts=100.0, actual=200.0 + i,
                         pace=90.0 + i, home_tpar=0.30, away_tpar=0.40))
    g = pd.DataFrame(rows)
    c = pd.DataFrame({"game_id": g.game_id, "line": 200.0, "line_source": "x", "spread": 1.0})
    f = L.compute_features(g, c).set_index("game_id")
    assert np.isnan(f.loc[10, "f5"])                    # only 9 earlier games for team 1
    # game 12 (index 11): team 1 last 10 = games 2..11 paces 91..100 -> mean 95.5 ; team 2 or 3 has fewer than 10 -> NaN
    assert np.isnan(f.loc[12, "f5"])
    # f10 / f6 need err history: team 1 err = actual-line = i
    # game 12: team 1 last 5 errs = games 7..11 -> i=6..10 mean 8 ; away team (id 2 + 11%2 = 3) has 5 earlier games? games 2,4,..: i odd -> id3
    # id 3 games: i=1,3,5,7,9 (5 earlier before i=11) errs 1,3,5,7,9 mean 5 ; so f6 = (8+5)/2
    assert f.loc[12, "f6"] == pytest.approx((8.0 + 5.0) / 2)


# ---------------------------------------------------------------- matching and cleaning
def toy_world():
    g = synth_games(seed=11, seasons=("2020-21",), n_days=40, n_teams=6, start_year=2020)
    return g


def test_parse_old_date():
    assert L.parse_old_date("2009-10-1027") == pd.Timestamp("2009-10-27")
    assert L.parse_old_date("2009-10-0617") == pd.Timestamp("2010-06-17")
    assert L.parse_old_date("2022-23-0101") == pd.Timestamp("2023-01-01")


NAMES = ["Atlanta Hawks", "Boston Celtics", "New Orleans Pelicans", "Chicago Bulls", "Cleveland Cavaliers", "Dallas Mavericks"]


def odds_rows(g, n=None, shift_days=0):
    rows = []
    for r in g.itertuples():
        rows.append(dict(table="odds_2020-21_new", season_tab="2020-21", kind="Xnew", date_how="iso",
                         date=r.date + pd.Timedelta(days=shift_days), home_name=NAMES[r.home_id - 1], away_name=NAMES[r.away_id - 1],
                         OU=r.actual, Spread=-3.0, Points=r.actual, row=len(rows)))
    o = pd.DataFrame(rows)
    o["home_id"] = o.home_name.map(L.NAME_TO_ID).map({1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6})
    o["away_id"] = o.away_name.map(L.NAME_TO_ID).map({1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6})
    return o


def test_matching_pm1_day_unmatched_notregular_ambiguous():
    g = toy_world()
    g_all = g.copy()
    o = odds_rows(g.iloc[:10], shift_days=1)           # rows dated one day late -> still match
    m = L.match_oddsdata(o, g_all)
    assert (m.match_status == "ok").all() and (m.game_id.values == g.game_id.iloc[:10].values).all()
    o2 = odds_rows(g.iloc[:3], shift_days=200)         # far off -> unmatched
    assert (L.match_oddsdata(o2, g_all).match_status == "unmatched").all()
    g_po = g_all.copy(); g_po.loc[g_po.index[0], "season_type"] = 3
    o3 = odds_rows(g.iloc[:1])
    assert L.match_oddsdata(o3, g_po).match_status.iloc[0] == "not_regular_season"
    # two games of the same home/away pair within the +-1 day window and neither on the row's date -> ambiguous
    x = g.iloc[:1].copy(); y = g.iloc[:1].copy()
    x["date"] = pd.Timestamp("2020-12-01"); y["date"] = pd.Timestamp("2020-12-03"); y["game_id"] = 99999
    ga = pd.concat([x, y])
    orow = odds_rows(x, 0); orow["date"] = pd.Timestamp("2020-12-02")
    assert L.match_oddsdata(orow, ga).match_status.iloc[0] == "ambiguous"
    # exact date preferred over +-1
    z = pd.concat([x, y.assign(date=pd.Timestamp("2020-12-02"))])
    orow["date"] = pd.Timestamp("2020-12-02")
    mm = L.match_oddsdata(orow, z)
    assert mm.match_status.iloc[0] == "ok" and mm.game_id.iloc[0] == 99999


def clean_setup():
    g = synth_games(seed=13, seasons=("2020-21",), n_days=60, n_teams=6, start_year=2020)
    g["actual"] = 215.0
    g["home_pts"] = 108.0; g["away_pts"] = 107.0
    return g


def test_cleaning_rules():
    g = clean_setup()
    o = odds_rows(g)
    o["OU"] = 215.0
    late = g.sort_values("date").iloc[-6:].game_id.tolist()      # late games have >= 5 prior games for both teams
    ids = {k: late[i] for i, k in enumerate(["range", "far", "dup", "disp", "ok", "snap"])}
    o.loc[g.game_id.values == ids["range"], "OU"] = 280.0
    o.loc[g.game_id.values == ids["far"], "OU"] = 250.0         # |250 - 215| = 35 > 30, in range
    # duplicate row in the same table
    dup = o[g.game_id.values == ids["dup"]]
    o = pd.concat([o, dup], ignore_index=True)
    # disputed: X says 215, Xnew says 218
    xrow = o[(g.game_id.reindex(range(len(o))).values == ids["disp"])] if False else None
    base = L.match_oddsdata(o, g)
    dsp = base[base.game_id == ids["disp"]].copy()
    dsp["kind"], dsp["table"], dsp["OU"] = "X", "odds_2020-21", 215.0
    base.loc[base.game_id == ids["disp"], "OU"] = 218.0
    base = pd.concat([base, dsp], ignore_index=True)
    # an agreeing pair (diff 0.5) for 'ok'
    okr = base[base.game_id == ids["ok"]].copy()
    okr["kind"], okr["table"], okr["OU"] = "X", "odds_2020-21", 214.5
    base = pd.concat([base, okr], ignore_index=True)
    # closing: bad snapshot for 'snap' (falls back to OddsData), good closing for the first game
    first = g.sort_values("date").iloc[0]
    close = pd.DataFrame([
        dict(game_id=first.game_id, game_spread=3.0, home_team_spread=-3.0, over_under=222.5, commence_time=f"{first.date:%Y-%m-%d}T23:00:00Z",
             snapshot=f"{first.date:%Y-%m-%d}T22:50:00Z"),
        dict(game_id=ids["snap"], game_spread=3.0, home_team_spread=-3.0, over_under=200.0, commence_time="2021-01-30T23:00:00Z",
             snapshot="2021-01-30T23:00:00Z"),
        dict(game_id=555555, game_spread=3.0, home_team_spread=-3.0, over_under=200.0, commence_time="2021-01-30T23:00:00Z",
             snapshot="2021-01-30T22:00:00Z")])
    # make commence dates plausible for the 'snap' game: use its real date
    sn = g[g.game_id == ids["snap"]].iloc[0]
    close.loc[1, ["commence_time", "snapshot"]] = [f"{sn.date:%Y-%m-%d}T23:00:00Z", f"{sn.date:%Y-%m-%d}T23:00:00Z"]
    clean, excl, info = L.clean_lines(g, g, base, close)
    ex = pd.DataFrame(excl)
    reasons = ex.groupby("reason").size().to_dict()
    assert reasons["line_out_of_range"] == 1
    assert reasons["line_far_from_team_means"] == 1
    assert reasons["line_disputed"] == 1
    assert reasons["oddsdata_duplicate"] == 2
    assert reasons["closing_snapshot_not_before_commence"] == 1
    assert reasons["closing_unmatched_game_id"] == 1
    cl = clean.set_index("game_id")
    assert cl.loc[first.game_id, "line"] == 222.5 and cl.loc[first.game_id, "line_source"] == "closing_odds_api"
    assert cl.loc[ids["snap"], "line"] == 215.0 and cl.loc[ids["snap"], "line_source"].startswith("oddsdata")   # fallback
    assert cl.loc[ids["ok"], "line"] == 215.0                      # X_new used when both agree within 1.0
    for k in ("range", "far", "dup", "disp"):
        assert ids[k] not in cl.index
    assert cl.loc[first.game_id, "spread"] == 3.0


def test_no_line_game_excluded():
    g = clean_setup()
    o = odds_rows(g.iloc[1:])
    o["OU"] = 215.0
    m = L.match_oddsdata(o, g)
    clean, excl, info = L.clean_lines(g, g, m, pd.DataFrame(columns=["game_id", "game_spread", "home_team_spread", "over_under", "commence_time", "snapshot"]))
    assert any(e["reason"] == "no_line_available" and e["key"] == str(g.game_id.iloc[0]) for e in excl)
