"""Tests for player_model.py (research log #56 section B).  Synthetic data only: no outcomes, no lines.
Run: .venv/Scripts/python.exe -m pytest reports/output/nba_formula_accuracy/test_player_model.py -q"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import player_model as pm  # noqa: E402

BASE = pd.Timestamp("2024-01-01")
ET = "America/New_York"

# 8 players per team: (aid, name, minutes, ppm)
ATL = [(1, "Alpha One", 34, 0.8), (2, "Bravo Two", 32, 0.7), (3, "Charlie Three", 30, 0.6), (4, "Delta Four", 28, 0.5),
       (5, "Echo Five", 26, 0.5), (6, "Foxtrot Six", 24, 0.4), (7, "Golf Seven", 22, 0.4), (8, "Hotel Eight", 20, 0.3)]
BOS = [(11, "Ida Eleven", 35, 0.7), (12, "Jack Twelve", 33, 0.6), (13, "Kilo Thirteen", 31, 0.6), (14, "Lima Fourteen", 29, 0.5),
       (15, "Mike Fifteen", 27, 0.5), (16, "Nora Sixteen", 25, 0.4), (17, "Oscar Seventeen", 21, 0.4), (18, "Papa Eighteen", 19, 0.3)]
SCALE_ATL = 240.0 / sum(p[2] for p in ATL)


def date_of(i):
    return BASE + pd.Timedelta(days=i)


def build(n_games=40, atl=ATL, bos=BOS, mod=None, atl_fga=85, bos_fga=95, atl_pts=100, bos_pts=110, year=2024, start=0):
    """n_games games ATL vs BOS, one per day from BASE+start.  mod(game_index, team, spec) -> spec or None (None = DNP row)."""
    pbr, tbr = [], []
    for i in range(n_games):
        gid = 1000 + start + i
        gdt = pd.Timestamp(f"{date_of(start + i).date()} 19:00", tz=ET)
        away_atl = i % 2 == 0
        for team, opp, specs, home_away, fga, sc, osc in (("ATL", "BOS", atl, "away" if away_atl else "home", atl_fga, atl_pts, bos_pts),
                                                         ("BOS", "ATL", bos, "home" if away_atl else "away", bos_fga, bos_pts, atl_pts)):
            for spec in specs:
                sp = mod(i, team, spec) if mod else spec
                aid, name = spec[0], spec[1]
                if sp is None:
                    pbr.append(dict(game_id=gid, season=year, season_type=2, game_date_time=gdt, athlete_id=aid,
                                    athlete_display_name=name, minutes=np.nan, points=np.nan, did_not_play=True,
                                    team_abbreviation=team))
                else:
                    pbr.append(dict(game_id=gid, season=year, season_type=2, game_date_time=gdt, athlete_id=aid,
                                    athlete_display_name=name, minutes=float(sp[2]), points=float(sp[2] * sp[3]),
                                    did_not_play=False, team_abbreviation=team))
            tbr.append(dict(game_id=gid, season=year, season_type=2, game_date_time=gdt, team_abbreviation=team,
                            opponent_team_abbreviation=opp, team_home_away=home_away, field_goals_attempted=float(fga),
                            offensive_rebounds=10.0, defensive_rebounds=30.0, total_turnovers=13.0, turnovers=13.0,
                            free_throws_attempted=20.0, field_goals_made=35.0, three_point_field_goals_made=10.0,
                            free_throws_made=15.0, team_score=sc, opponent_team_score=osc))
    return pd.DataFrame(pbr), pd.DataFrame(tbr)


def tables(**kw):
    pb, tb = build(**kw)
    return pm.Tables(pb, tb, kw.get("year", 2024))


def games_df(day, away="ATL", home="BOS", hour=19):
    d = date_of(day)
    T = int(pd.Timestamp(f"{d.date()} {hour}:00", tz=ET).timestamp())
    return pd.DataFrame({"game_id": [9999], "date": [d.normalize()], "T": [T], "away": [away], "home": [home], "season": ["2023-24"]})


def no_reports():
    return pm.Reports(created={}, content={})


def fake_reports(created_ts, rows, name="rep1.pdf", matchup="ATL@BOS", day=40, nys=None):
    mdy = date_of(day).strftime("%m/%d/%Y")
    content = {name: {"rows": [dict(date=mdy, time="07:00(ET)", matchup=matchup, team=t, player=p, status=s) for t, p, s in rows],
                      "nys": nys or []}}
    return pm.Reports(created={name: created_ts}, content=content)


def run(tabs, reps=None, day=40, **kw):
    return pm.project_all("2023-24", games_df(day, **kw), tables=tabs, reports=reps or no_reports())


def atl_raw(ppm_override=None, drop=(), weights=None):
    """hand-computed ATL raw points: minutes scaled to 240 over the available weighted players."""
    pl = [p for p in ATL if p[0] not in drop]
    w = {p[0]: (weights or {}).get(p[0], 1.0) for p in pl}
    eff = {p[0]: p[2] * w[p[0]] for p in pl}
    s = 240.0 / sum(eff.values())
    return sum(((ppm_override or {}).get(p[0], p[3])) * eff[p[0]] * s for p in pl)


# ------------------------------------------------------------------ pure helpers
def test_versions_interface():
    assert len(pm.VERSIONS) == 8
    assert len({(v["q"], v["opp"], v["pace"]) for v in pm.VERSIONS}) == 8
    assert pm.VERSIONS[0] == {"q": 0.5, "opp": False, "pace": False}
    assert pm.VERSIONS[-1] == {"q": 1.0, "opp": True, "pace": True}
    assert {v["q"] for v in pm.VERSIONS} == {0.5, 1.0}
    assert all(set(v) == {"q", "opp", "pace"} for v in pm.VERSIONS)


def test_shrink_formula():
    # n = 40 -> w = 0.8 on the season rate
    assert pm.shrink_rate(0.8, 0.6, 40) == pytest.approx(0.2 * 0.8 + 0.8 * 0.6)
    assert pm.shrink_rate(0.8, 0.6, 10) == pytest.approx(0.5 * 0.8 + 0.5 * 0.6)
    assert pm.shrink_rate(0.8, None, 0, prior_rate=0.3) == pytest.approx(0.8)       # n=0 -> weight 0 on the target
    assert pm.shrink_rate(0.7, 0.7, 25) == pytest.approx(0.7)


def test_scale_minutes_sums_to_240():
    m = pm.scale_minutes([30, 20, 10])
    assert m.sum() == pytest.approx(240.0)
    assert m[0] / m[1] == pytest.approx(1.5)
    assert pm.scale_minutes([]).size == 0
    assert pm.team_raw_points([30, 30], [1, 1], [0.5, 1.0]) == pytest.approx(0.5 * 120 + 1.0 * 120)
    assert np.isnan(pm.team_raw_points([], [], []))


def test_status_weight():
    assert pm.status_weight("Out", 0.5) == 0 and pm.status_weight("Doubtful", 1.0) == 0
    assert pm.status_weight("Questionable", 0.5) == 0.5 and pm.status_weight("Questionable", 1.0) == 1.0
    assert pm.status_weight("Probable", 0.5) == 1.0 and pm.status_weight(None, 0.5) == 1.0 and pm.status_weight("Available", 0.5) == 1.0


# ------------------------------------------------------------------ rotation / rates / baseline
def test_baseline_no_injuries_matches_hand_computation():
    out = run(tables())
    # no opp / pace: raw sum of both teams
    bos = sum(p[3] * p[2] * 240.0 / sum(x[2] for x in BOS) for p in BOS)
    assert out[0][0] == pytest.approx(atl_raw() + bos)
    assert out[0][0] == pytest.approx(out[4][0])                      # q irrelevant without injuries


def test_rotation_threshold_and_dnp_counts_zero():
    def mod(i, team, spec):
        if team == "ATL" and spec[0] == 8:                            # 20 mpg player misses 6 of last 10 games -> 8 mpg
            return None if i >= 34 and i < 40 and i % 1 == 0 else spec
        return spec
    tabs = tables(mod=mod)
    rot = tabs.rotation("ATL", pm.day_int(date_of(40)))
    assert 8 not in rot                                               # 4 games x 20 / 10 = 8 mpg < 10
    assert rot[1] == pytest.approx(34)

    def mod2(i, team, spec):
        if team == "ATL" and spec[0] == 8:
            return None if 36 <= i < 40 else spec                     # misses 4 of last 10 -> 12 mpg: stays
        return spec
    rot2 = tables(mod=mod2).rotation("ATL", pm.day_int(date_of(40)))
    assert rot2[8] == pytest.approx(12.0)


def test_min_ppm_below_5_games_uses_team_average():
    def mod(i, team, spec):
        if team == "ATL" and spec[0] == 1:
            return spec if i >= 37 else None                          # only 3 games played in total
        return spec
    tabs = tables(mod=mod)
    d = pm.day_int(date_of(40))
    inp = tabs.player_inputs(1, d, 2024)
    assert inp["n_total"] == 3
    # window mpg for player 1 = 3*34/10 = 10.2 -> rotation; ppm falls back to the team rate
    rot = tabs.rotation("ATL", d)
    assert 1 in rot
    tr = tabs.team_rate("ATL", d)
    # team rate: player points / player minutes, last 20 team games (player 1 played in none of the first 17 of them)
    pts = sum((p[2] * p[3]) for p in ATL if p[0] != 1) * 17 + sum(p[2] * p[3] for p in ATL) * 3
    mn = sum(p[2] for p in ATL if p[0] != 1) * 17 + sum(p[2] for p in ATL) * 3
    assert tr == pytest.approx(pts / mn)
    got = run(tabs)[0][0]
    mean_min = {p[0]: (p[2]) for p in ATL}
    mean_min[1] = 34.0
    eff = {a: m for a, m in mean_min.items() if a in rot}
    s = 240.0 / sum(eff.values())
    exp_atl = sum((tr if a == 1 else next(p[3] for p in ATL if p[0] == a)) * m * s for a, m in eff.items())
    bos = sum(p[3] * p[2] * 240.0 / sum(x[2] for x in BOS) for p in BOS)
    assert got == pytest.approx(exp_atl + bos)


def test_shrinkage_end_to_end():
    def mod(i, team, spec):
        if team == "ATL" and spec[0] == 1:
            return (1, spec[1], 34, 0.4 if i < 20 else 0.8)           # last 20 at 0.8, season 0.6
        return spec
    out = run(tables(mod=mod))[0][0]
    exp = atl_raw(ppm_override={1: 0.2 * 0.8 + 0.8 * 0.6})
    bos = sum(p[3] * p[2] * 240.0 / sum(x[2] for x in BOS) for p in BOS)
    assert out == pytest.approx(exp + bos)


def test_last_20_spans_seasons_and_season_rate_is_current_only():
    # previous-season file rows (year 2023) for 30 games, then 5 current-season games
    pb1, tb1 = build(n_games=30, year=2023, start=-60, mod=lambda i, t, s: (s[0], s[1], s[2], 0.2) if s[0] == 1 else s)
    pb2, tb2 = build(n_games=5, year=2024, start=0, mod=lambda i, t, s: (s[0], s[1], s[2], 1.0) if s[0] == 1 else s)
    tabs = pm.Tables(pd.concat([pb1, pb2], ignore_index=True), pd.concat([tb1, tb2], ignore_index=True), 2024)
    inp = tabs.player_inputs(1, pm.day_int(date_of(5)), 2024)
    assert inp["n_cur"] == 5 and inp["n_total"] == 35
    assert inp["last20_rate"] == pytest.approx((15 * 0.2 + 5 * 1.0) / 20)
    assert inp["cur_rate"] == pytest.approx(1.0)
    assert inp["prior_rate"] == pytest.approx(0.2)


# ------------------------------------------------------------------ look-ahead
def test_no_lookahead_future_and_same_day_games_ignored():
    base = tables(n_games=40)
    ref = run(base)
    # the target game's own box score (day 40) and later games with crazy stats must not change the result
    crazy = lambda i, t, s: (s[0], s[1], 40, 5.0)                                              # noqa: E731
    pb_x, tb_x = build(n_games=3, start=40, mod=crazy, atl_fga=150, bos_fga=60, atl_pts=170, bos_pts=60)
    pb0, tb0 = build(n_games=40)
    tabs2 = pm.Tables(pd.concat([pb0, pb_x], ignore_index=True), pd.concat([tb0, tb_x], ignore_index=True), 2024)
    out2 = run(tabs2)
    for i in range(8):
        assert out2[i][0] == ref[i][0], i
    # control: the crazy games DO change the projection for a LATER game (so the guard above is meaningful)
    out3 = run(tabs2, day=43)
    assert abs(out3[0][0] - ref[0][0]) > 1.0


def test_injury_cutoff_at_D():
    tabs = tables()
    g = games_df(40)
    D = float(g["T"].iloc[0]) - 3600.0
    rows = [("AtlantaHawks", "One,Alpha", "Out")]
    base = pm.project_all("2023-24", g, tables=tabs, reports=no_reports())[0][0]
    on_time = pm.project_all("2023-24", g, tables=tabs, reports=fake_reports(D, rows))[0][0]
    exact_plus = pm.project_all("2023-24", g, tables=tabs, reports=fake_reports(D + 60.0, rows))[0][0]
    just_after = pm.project_all("2023-24", g, tables=tabs, reports=fake_reports(D + 1.0, rows))[0][0]
    assert on_time != pytest.approx(base)                              # report exactly at D counts
    assert exact_plus == base and just_after == base                   # created after D: ignored
    st = pm.LAST_STATS["2023-24"]
    assert st["with_report"] == 0


def test_latest_report_wins():
    tabs = tables()
    g = games_df(40)
    D = float(g["T"].iloc[0]) - 3600.0
    mdy = date_of(40).strftime("%m/%d/%Y")
    mk = lambda st: {"rows": [dict(date=mdy, time="", matchup="ATL@BOS", team="AtlantaHawks", player="One,Alpha", status=st)], "nys": []}  # noqa: E731
    reps = pm.Reports(created={"a.pdf": D - 7200, "b.pdf": D - 600}, content={"a.pdf": mk("Out"), "b.pdf": mk("Available")})
    out = pm.project_all("2023-24", g, tables=tabs, reports=reps)[0][0]
    assert out == pm.project_all("2023-24", g, tables=tabs, reports=no_reports())[0][0]       # b (Available) is the latest


# ------------------------------------------------------------------ injuries
def test_out_doubtful_removed_questionable_weighted():
    tabs = tables()
    g = games_df(40)
    D = float(g["T"].iloc[0]) - 3600.0
    bos = sum(p[3] * p[2] * 240.0 / sum(x[2] for x in BOS) for p in BOS)
    for status in ("Out", "Doubtful"):
        out = pm.project_all("2023-24", g, tables=tabs, reports=fake_reports(D - 100, [("AtlantaHawks", "One,Alpha", status)]))
        assert out[0][0] == pytest.approx(atl_raw(drop=(1,)) + bos)
        assert out[4][0] == pytest.approx(atl_raw(drop=(1,)) + bos)
    out = pm.project_all("2023-24", g, tables=tabs, reports=fake_reports(D - 100, [("AtlantaHawks", "One,Alpha", "Questionable")]))
    assert out[0][0] == pytest.approx(atl_raw(weights={1: 0.5}) + bos)       # q = 0.5
    assert out[4][0] == pytest.approx(atl_raw() + bos)                       # q = 1.0 -> same as healthy
    assert out[0][0] != pytest.approx(out[4][0])
    # Probable / Available leave the player in
    out = pm.project_all("2023-24", g, tables=tabs, reports=fake_reports(D - 100, [("AtlantaHawks", "One,Alpha", "Probable")]))
    assert out[0][0] == pytest.approx(atl_raw() + bos)
    # BOS player listed on the ATL line must not be removed from ATL (team column respected)
    out = pm.project_all("2023-24", g, tables=tabs, reports=fake_reports(D - 100, [("BostonCeltics", "One,Alpha", "Out")]))
    assert out[0][0] == pytest.approx(atl_raw() + bos)
    st = pm.LAST_STATS["2023-24"]
    assert st["with_report"] == 1


def test_unmatched_name_counted_and_ignored():
    tabs = tables()
    g = games_df(40)
    D = float(g["T"].iloc[0]) - 3600.0
    out = pm.project_all("2023-24", g, tables=tabs, reports=fake_reports(D - 100, [("AtlantaHawks", "Nobody,Zed", "Out")]))
    st = pm.LAST_STATS["2023-24"]
    assert st["unmatched"] == 1 and st["matched_rotation"] == 0
    assert out[0][0] == run(tabs)[0][0]


def test_team_not_yet_submitted_means_no_removal():
    tabs = tables()
    g = games_df(40)
    D = float(g["T"].iloc[0]) - 3600.0
    mdy = date_of(40).strftime("%m/%d/%Y")
    reps = fake_reports(D - 100, [("AtlantaHawks", "One,Alpha", "Out")], nys=[[mdy, "ATL@BOS", "AtlantaHawks"]])
    out = pm.project_all("2023-24", g, tables=tabs, reports=reps)
    assert out[0][0] == run(tabs)[0][0]


# ------------------------------------------------------------------ opponent and pace factors
def test_opponent_and_pace_factors():
    tabs = tables(n_games=40)
    d = pm.day_int(date_of(40))
    out = run(tabs)
    # independent hand computation of the building blocks
    poss_atl = 85 - 10 + 13 + 0.44 * 20
    poss_bos = 95 - 10 + 13 + 0.44 * 20
    drtg_atl = 100.0 * 110 / poss_bos                       # ATL allowed 110 over BOS's possessions (opp_poss)
    drtg_bos = 100.0 * 100 / poss_atl
    lg = 100.0 * (100 + 110) / (poss_atl + poss_bos)
    pace_atl = pace_bos = (poss_atl + poss_bos) / 2.0
    atl = atl_raw()
    bos = sum(p[3] * p[2] * 240.0 / sum(x[2] for x in BOS) for p in BOS)
    assert tabs.league_avg(d, 2024) == pytest.approx(lg)
    # opp only: ATL x (BOS allowed / league), BOS x (ATL allowed / league)
    assert out[2][0] == pytest.approx(atl * drtg_bos / lg + bos * drtg_atl / lg)
    # pace only: both teams' own pace equals the game's expected pace -> factor 1
    assert out[1][0] == pytest.approx(atl + bos)
    assert out[3][0] == pytest.approx(out[2][0])


def test_pace_factor_when_teams_differ():
    # ATL's history is slow (pace 86.8, vs CHI), BOS's is fast (pace 106.8, vs MIA) -> expected pace = the mean
    def relabel(df, mapping, cols):
        df = df.copy()
        for c in cols:
            df[c] = df[c].replace(mapping)
        return df
    pa, ta = build(n_games=40, atl_fga=75, bos_fga=75)
    pa = relabel(pa, {"BOS": "CHI"}, ["team_abbreviation"])
    ta = relabel(ta, {"BOS": "CHI"}, ["team_abbreviation", "opponent_team_abbreviation"])
    pb_, tb_ = build(n_games=40, atl_fga=95, bos_fga=95)
    pb_ = relabel(pb_, {"ATL": "MIA"}, ["team_abbreviation"])
    tb_ = relabel(tb_, {"ATL": "MIA"}, ["team_abbreviation", "opponent_team_abbreviation"])
    tb_["game_id"] += 5000
    pb_["game_id"] += 5000
    tabs = pm.Tables(pd.concat([pa, pb_], ignore_index=True), pd.concat([ta, tb_], ignore_index=True), 2024)
    d = pm.day_int(date_of(40))
    pa15, pb15 = tabs.recent("ATL", d)[1], tabs.recent("BOS", d)[1]
    assert pa15 == pytest.approx(75 - 10 + 13 + 8.8) and pb15 == pytest.approx(95 - 10 + 13 + 8.8)
    out = pm.project_all("2023-24", games_df(40), tables=tabs, reports=no_reports())
    exp = (pa15 + pb15) / 2.0
    sideA = atl_raw()
    sideB = sum(p[3] * p[2] * 240.0 / sum(x[2] for x in BOS) for p in BOS)
    assert out[0][0] == pytest.approx(sideA + sideB)
    assert out[1][0] == pytest.approx(sideA * exp / pa15 + sideB * exp / pb15)      # pace on, opp off


# ------------------------------------------------------------------ determinism / shape
def test_deterministic_and_shapes():
    tabs = tables()
    g = pd.concat([games_df(40), games_df(41, away="BOS", home="ATL"), games_df(42, away="XXX", home="ATL")], ignore_index=True)
    a = pm.project_all("2023-24", g, tables=tabs, reports=no_reports())
    b = pm.project_all("2023-24", g, tables=tabs, reports=no_reports())
    assert sorted(a) == list(range(8))
    for i in range(8):
        assert a[i].shape == (3,) and a[i].dtype == float
        assert np.array_equal(a[i], b[i], equal_nan=True)
    assert np.isnan(a[0][2])                                               # unmapped team -> NaN, not a crash
    assert np.isfinite(a[0][:2]).all()


def test_project_matches_project_all_and_caches(monkeypatch):
    tabs = tables()
    g = games_df(40)
    pm._TABLES["2023-24"] = tabs
    pm._REPORTS["r"] = no_reports()
    try:
        allv = pm.project_all("2023-24", g)
        for i, v in enumerate(pm.VERSIONS):
            p = pm.project("2023-24", g, v)
            assert isinstance(p, np.ndarray) and p.shape == (1,)
            assert p[0] == allv[i][0]
    finally:
        pm._TABLES.pop("2023-24", None)
        pm._REPORTS.pop("r", None)
        pm._LAST_CALL.clear()
