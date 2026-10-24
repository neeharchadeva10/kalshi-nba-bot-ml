"""Toy tests of the independent E3 feature code (e3_check.py)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import e3_check as e3  # noqa: E402
cf = e3.cf

ET = "America/New_York"
G = pd.Timestamp("2022-11-12")
D60 = pd.Timestamp("2022-11-12 18:00", tz=ET)   # tip 19:00
D20 = pd.Timestamp("2022-11-12 18:40", tz=ET)


def box_row(season, gi, aid, name, mins, pts, played=1, team="AAA", **kw):
    day = (pd.Timestamp("2022-11-01") if season == 2023 else pd.Timestamp("2021-11-01")) + pd.Timedelta(days=gi)
    tip = (day + pd.Timedelta(hours=19)).tz_localize(ET)
    r = dict(season=season, season_type=2, game_id=f"{season}{gi:03d}", tip=tip, etdate=day, played=bool(played),
             did_not_play=not played, minutes=mins if played else np.nan, points=pts if played else np.nan,
             athlete_id=aid, athlete_display_name=name, team_abbreviation=team, rebounds=5.0, assists=3.0,
             three_point_field_goals_made=1.0, steals=1.0, blocks=0.5, field_goals_attempted=10.0,
             free_throws_attempted=5.0, turnovers=2.0, pm=4.0)
    r.update(kw)
    return r


def mk(rows, reports, moves=None, dob=None):
    pb = pd.DataFrame(rows)
    tb = pb.drop_duplicates("game_id")[["season", "game_id", "tip", "etdate", "team_abbreviation"]].copy()
    tb["team_location"], tb["team_name"], tb["team_display_name"] = "Alpha", "Aces", "Alpha Aces"
    reps = sorted([(cf.parse_pub_time(f), f, {"rows": rr, "nys": []}) for f, rr in reports], key=lambda x: x[0])
    e = object.__new__(e3.E3Calc)
    e.pb = pb
    e.calc = cf.Calc(pb, tb, {"AAA"}, reps)
    e.dob = dob or {}
    mv = moves if moves is not None else pd.DataFrame(
        columns=["athlete_id", "from_team", "to_team", "first_date_new", "season_new"])
    mv = mv.copy()
    mv["first_date_new"] = pd.to_datetime(mv.first_date_new)
    mv = mv.sort_values("first_date_new", kind="stable").reset_index(drop=True)
    e.moves_by_ath = {k: g for k, g in mv.groupby("athlete_id")}
    e.moves_to = {k: g for k, g in mv.groupby("to_team")}
    e.moves_from = {k: g for k, g in mv.groupby("from_team")}
    return e


def rr(player, status, date="11/12/2022"):
    return dict(date=date, time="07:00(ET)", matchup="BBB@AAA", team="AlphaAces", player=player, status=status)


def base():
    rows = []
    for gi in range(11):
        rows.append(box_row(2023, gi, 1, "Star Player", 30.0, 10.0 + gi, steals=2.0, blocks=1.0, rebounds=8.0,
                            assists=6.0, three_point_field_goals_made=2.0, pm=gi - 5.0))
        rows.append(box_row(2023, gi, 2, "Mid Guy", 22.0, 8.0))
        rows.append(box_row(2023, gi, 3, "Bench Guy", 11.0, 4.0))
        rows.append(box_row(2023, gi, 4, "Tiny Guy", 9.9, 2.0))
    return rows


REP = "Injury-Report_2022-11-12_05PM.pdf"


def test_all_families_one_star_out():
    e = mk(base(), [(REP, [rr("Player,Star", "Out")])], dob={1: pd.Timestamp("1990-01-01")})
    f, info = e.report_fams("AAA", "BBB", 2023, G, D60)
    assert f["pts"] == pytest.approx(15.5)               # games 1..10 -> mean(11..20)
    assert f["min"] == pytest.approx(30 / 240)
    assert f["reb"] == pytest.approx(8.0)
    assert f["ast"] == pytest.approx(6.0)
    assert f["tpm"] == pytest.approx(2.0)
    assert f["dfn"] == pytest.approx(3.0)                # steals + blocks
    assert f["usg"] == pytest.approx(10 + 0.44 * 5 + 2)
    assert f["pm"] == pytest.approx(np.mean([g - 5.0 for g in range(1, 11)]))
    assert (f["tier_star"], f["tier_starter"], f["tier_bench"]) == (pytest.approx(15.5), 0, 0)
    assert f["age_vet"] == pytest.approx(30.0) and f["age_young"] == 0   # born 1990-01-01 -> 32 on 2022-11-12
    assert f["q_pts"] == 0


def test_age_boundaries_and_unknown():
    # born 1990-11-13 -> still 31 on 2022-11-12 (not vet); born 1990-11-12 -> 32 (vet)
    assert e3.age_on(pd.Timestamp("1990-11-13"), G) == 31
    assert e3.age_on(pd.Timestamp("1990-11-12"), G) == 32
    assert e3.age_on(pd.Timestamp("1999-11-13"), G) == 22
    assert e3.age_on(pd.Timestamp("2022-01-01"), G) is None   # age 0 -> outside 18-45
    assert e3.age_on(pd.Timestamp("1960-01-01"), G) is None
    assert e3.age_on(None, G) is None
    rows = base()
    reps = [(REP, [rr("Player,Star", "Out"), rr("Guy,Mid", "Out")])]
    e = mk(rows, reps, dob={1: pd.Timestamp("1990-11-12"), 2: pd.Timestamp("1999-11-13")})
    f, _ = e.report_fams("AAA", "BBB", 2023, G, D60)
    assert f["age_vet"] == pytest.approx(30.0) and f["age_young"] == pytest.approx(22.0)
    e = mk(rows, reps, dob={})  # unknown ages: left out of AGE only
    f, info = e.report_fams("AAA", "BBB", 2023, G, D60)
    assert f["age_vet"] == 0 and f["age_young"] == 0 and info["n_age_unknown"] == 2
    assert f["pts"] == pytest.approx(15.5 + 8.0)


def test_tiers_and_rotation_threshold_10():
    reps = [(REP, [rr("Player,Star", "Out"), rr("Guy,Mid", "Out"), rr("Guy,Bench", "Out"), rr("Guy,Tiny", "Out")])]
    e = mk(base(), reps)
    f, info = e.report_fams("AAA", "BBB", 2023, G, D60)
    assert f["tier_star"] == pytest.approx(15.5) and f["tier_starter"] == pytest.approx(8.0)
    assert f["tier_bench"] == pytest.approx(4.0)         # 11 mpg bench in; 9.9 mpg out
    assert info["n_out_rot"] == 3
    # tier boundaries: exactly 28 -> star, exactly 20 -> starter
    rows = [box_row(2023, g, 1, "Star Player", 28.0, 10.0) for g in range(11)] + \
           [box_row(2023, g, 2, "Mid Guy", 20.0, 6.0) for g in range(11)]
    e = mk(rows, [(REP, [rr("Player,Star", "Out"), rr("Guy,Mid", "Out")])])
    f, _ = e.report_fams("AAA", "BBB", 2023, G, D60)
    assert f["tier_star"] == pytest.approx(10.0) and f["tier_starter"] == pytest.approx(6.0)


def test_q_family_separate_from_out():
    e = mk(base(), [(REP, [rr("Player,Star", "Questionable"), rr("Guy,Mid", "Doubtful")])])
    f, _ = e.report_fams("AAA", "BBB", 2023, G, D60)
    assert f["q_pts"] == pytest.approx(15.5) and f["q_min"] == pytest.approx(30 / 240)
    assert f["pts"] == pytest.approx(8.0)                # Doubtful counts as out; Questionable not in PTS


def test_cutoff_c60_vs_c20():
    reps = [("Injury-Report_2022-11-12_05PM.pdf", [rr("Player,Star", "Questionable")]),
            ("Injury-Report_2022-11-12_06_30PM.pdf", [rr("Player,Star", "Out")])]   # 18:30: after D60, before D20
    e = mk(base(), reps)
    f60, _ = e.report_fams("AAA", "BBB", 2023, G, D60)
    f20, _ = e.report_fams("AAA", "BBB", 2023, G, D20)
    assert f60["pts"] == 0 and f60["q_pts"] == pytest.approx(15.5)
    assert f20["pts"] == pytest.approx(15.5) and f20["q_pts"] == 0
    # inclusive at the cutoff, one minute later ignored
    f, info = e.report_fams("AAA", "BBB", 2023, G, pd.Timestamp("2022-11-12 18:30", tz=ET))
    assert f["pts"] == pytest.approx(15.5)
    f, info = e.report_fams("AAA", "BBB", 2023, G, pd.Timestamp("2022-11-12 18:29", tz=ET))
    assert f["pts"] == 0


def test_no_rows_team_is_all_zero_and_no_report():
    other = dict(date="11/12/2022", time="07:00(ET)", matchup="AAA@BBB", team="BetaBees", player="Z,Z", status="Out")
    e = mk(base(), [(REP, [other])])
    e.calc.full2abbr["betabees"] = "BBB"
    f, info = e.report_fams("AAA", "BBB", 2023, G, D60)
    assert all(f[k] == 0 for k in f) and info["report_found"]
    e = mk(base(), [])
    f, info = e.report_fams("AAA", "BBB", 2023, G, D60)
    assert all(f[k] == 0 for k in f) and not info["report_found"]


def test_window_fallback_and_no_lookahead():
    rows = [box_row(2022, g, 3, "Vet Man", 24.0, 12.0) for g in range(30)]
    rows += [box_row(2023, g, 3, "Vet Man", 5.0, 1.0) for g in range(2)]
    e = mk(rows, [(REP, [rr("Man,Vet", "Out")])])
    f, _ = e.report_fams("AAA", "BBB", 2023, G, D60)
    assert f["pts"] == pytest.approx(12.0)               # <3 this season -> last season's last 10
    # games on the target ET date and after must be ignored
    rows = base() + [box_row(2023, 11, 1, "Star Player", 30.0, 99.0), box_row(2023, 12, 1, "Star Player", 30.0, 99.0)]
    e = mk(rows, [(REP, [rr("Player,Star", "Out")])])
    f, _ = e.report_fams("AAA", "BBB", 2023, G, D60)
    assert f["pts"] == pytest.approx(15.5)


def test_plus_minus_parse():
    assert e3.parse_pm("+7") == 7 and e3.parse_pm("-14") == -14 and e3.parse_pm("0") == 0
    assert np.isnan(e3.parse_pm(None)) and np.isnan(e3.parse_pm("--"))


def test_new_family_and_no_lookahead():
    rows = []
    # last season (2022): P10 played 1000 min, P11 played 1000 min for AAA; P12 (left) 500
    for g in range(5):
        rows += [box_row(2022, g, 10, "Stay Guy", 20.0, 5.0), box_row(2022, g, 11, "Go Guy", 20.0, 5.0),
                 box_row(2022, g, 12, "Back Guy", 10.0, 2.0)]
    # this season: 11 games, new guy 99 plays 10 min in each; stay guy 30
    for g in range(11):
        rows += [box_row(2023, g, 10, "Stay Guy", 30.0, 5.0), box_row(2023, g, 99, "New Guy", 10.0, 5.0)]
    mv = pd.DataFrame([
        dict(athlete_id=99, from_team="ZZZ", to_team="AAA", first_date_new="2022-11-02", season_new=2023),
        dict(athlete_id=11, from_team="AAA", to_team="ZZZ", first_date_new="2022-07-01", season_new=2023),
        dict(athlete_id=12, from_team="AAA", to_team="ZZZ", first_date_new="2022-07-01", season_new=2023),
        dict(athlete_id=12, from_team="ZZZ", to_team="AAA", first_date_new="2022-08-01", season_new=2023),  # came back
    ])
    e = mk(rows, [], moves=mv)
    ns, gs = e.new_fams("AAA", 2023, G)
    # last 10 team games = gi 1..10; the new guy's move date 2022-11-02 is a game-day for gi=1 but the share uses
    # minutes in the window regardless once the move is visible (< G): 10*10 / (10*40)
    assert ns == pytest.approx(0.25)
    assert gs == pytest.approx(100 / (100 + 100 + 50))   # P11 gone (100 min of 250); P12 returned -> not gone
    # moves on/after the game's date are invisible
    mv2 = mv.copy()
    mv2.loc[0, "first_date_new"] = "2022-11-12"
    e = mk(rows, [], moves=mv2)
    assert e.new_fams("AAA", 2023, G)[0] == 0.0
    mv3 = mv.copy()
    mv3.loc[1, "first_date_new"] = "2022-11-12"          # P11's departure not visible yet
    e = mk(rows, [], moves=mv3)
    assert e.new_fams("AAA", 2023, G)[1] == 0.0
    # a move from an earlier season_new does not make a player "new this season"
    mv4 = mv.copy()
    mv4.loc[0, "season_new"] = 2022
    e = mk(rows, [], moves=mv4)
    assert e.new_fams("AAA", 2023, G)[0] == 0.0
    # first game of the season: no earlier game -> new_share 0
    assert e.new_fams("AAA", 2023, pd.Timestamp("2022-11-01"))[0] == 0.0


def test_new_share_uses_at_most_last_10_games():
    rows = [box_row(2023, g, 10, "Stay Guy", 30.0, 5.0) for g in range(11)]
    rows += [box_row(2023, g, 99, "New Guy", 30.0, 5.0) for g in range(5, 11)]   # only in games 5..10
    mv = pd.DataFrame([dict(athlete_id=99, from_team="ZZZ", to_team="AAA", first_date_new="2022-10-20", season_new=2023)])
    e = mk(rows, [], moves=mv)
    assert e.new_fams("AAA", 2023, G)[0] == pytest.approx(180 / (300 + 180))   # games 1..10
    assert e.new_fams("AAA", 2023, pd.Timestamp("2022-11-04"))[0] == 0.0       # games 0..2 only: new guy absent


def test_team_code_map():
    assert e3.kcode("GS") == "GSW" and e3.kcode("UTAH") == "UTA" and e3.kcode("BOS") == "BOS"
