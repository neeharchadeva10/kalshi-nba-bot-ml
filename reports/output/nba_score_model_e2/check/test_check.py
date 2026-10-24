"""Toy-example tests of the independent E2 injury feature code (check_features.py)."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import check_features as cf  # noqa: E402

ET = "America/New_York"


def mk_box(rows):
    """rows: (season, game_idx, athlete_id, name, minutes, points, played)"""
    out = []
    for season, gi, aid, name, mins, pts, played in rows:
        day = pd.Timestamp("2022-11-01") + pd.Timedelta(days=gi) if season == 2023 else \
            pd.Timestamp("2021-11-01") + pd.Timedelta(days=gi)
        tip = (day + pd.Timedelta(hours=19)).tz_localize(ET)
        out.append(dict(season=season, season_type=2, game_id=f"{season}{gi:03d}", tip=tip, etdate=day,
                        played=bool(played), did_not_play=not played, minutes=mins if played else float("nan"),
                        points=pts if played else float("nan"), athlete_id=aid, athlete_display_name=name,
                        team_abbreviation="AAA"))
    return pd.DataFrame(out)


def mk_calc(box_rows, reports):
    pb = mk_box(box_rows)
    tb = pb.drop_duplicates("game_id")[["season", "game_id", "tip", "etdate", "team_abbreviation"]].copy()
    tb["team_location"], tb["team_name"], tb["team_display_name"] = "Alpha", "Aces", "Alpha Aces"
    reps = [(cf.parse_pub_time(f), f, {"rows": rows, "nys": nys}) for f, rows, nys in reports]
    reps.sort(key=lambda x: x[0])
    return cf.Calc(pb, tb, {"AAA"}, reps), pb


def rep_row(date, player, status):
    return dict(date=date, time="07:00(ET)", matchup="BBB@AAA", team="AlphaAces", player=player, status=status)


G = pd.Timestamp("2022-11-12")  # target game ET date (game index 11)
D_OK = pd.Timestamp("2022-11-12 18:00", tz=ET)


def base_rows():
    rows = []
    for gi in range(11):  # 11 prior games, season 2023
        rows.append((2023, gi, 1, "Star Player", 30.0, 10.0 + gi, 1))  # last 10 = gi 1..10 -> ppg 15.5
        rows.append((2023, gi, 2, "Bench Guy", 10.0, 4.0, 1))
        rows.append((2023, gi, 4, "Mid Guy Jr.", 20.0, 8.0, 1))
    return rows


def test_parse_pub_time():
    assert cf.parse_pub_time("Injury-Report_2022-11-12_06PM.pdf") == pd.Timestamp("2022-11-12 18:00", tz=ET)
    assert cf.parse_pub_time("Injury-Report_2022-11-12_06_30PM.pdf") == pd.Timestamp("2022-11-12 18:30", tz=ET)
    assert cf.parse_pub_time("Injury-Report_2022-11-12_12_00AM.pdf") == pd.Timestamp("2022-11-12 00:00", tz=ET)
    assert cf.parse_pub_time("Injury-Report_2022-11-12_12PM.pdf") == pd.Timestamp("2022-11-12 12:00", tz=ET)


def test_report_cutoff_at_D():
    d = "11/12/2022"
    reports = [
        ("Injury-Report_2022-11-12_05PM.pdf", [rep_row(d, "Player,Star", "Questionable")], []),
        ("Injury-Report_2022-11-12_06PM.pdf", [rep_row(d, "Player,Star", "Out")], []),  # exactly at D -> used
        ("Injury-Report_2022-11-12_06_30PM.pdf", [], []),  # after D -> ignored
    ]
    calc, _ = mk_calc(base_rows(), reports)
    f, info = calc.team_feats("AAA", 2023, G, D_OK)
    assert info["report_file"] == "Injury-Report_2022-11-12_06PM.pdf"
    assert f["out_pts"] == pytest.approx(15.5) and f["q_pts"] == 0
    assert f["out_min"] == pytest.approx(30 / 240)
    # one minute earlier: the 05PM report (questionable)
    f2, info2 = calc.team_feats("AAA", 2023, G, D_OK - pd.Timedelta(minutes=1))
    assert info2["report_file"] == "Injury-Report_2022-11-12_05PM.pdf"
    assert f2["q_pts"] == pytest.approx(15.5) and f2["out_pts"] == 0
    # before any report: no information
    f3, info3 = calc.team_feats("AAA", 2023, G, pd.Timestamp("2022-11-12 08:00", tz=ET))
    assert info3["report_state"] == "no_report" and f3["out_pts"] == 0


def test_nys_is_no_information():
    reports = [("Injury-Report_2022-11-12_05PM.pdf", [], [["11/12/2022", "BBB@AAA", "AlphaAces"]])]
    calc, _ = mk_calc(base_rows(), reports)
    f, info = calc.team_feats("AAA", 2023, G, D_OK)
    assert info["report_state"] == "nys" and f["out_pts"] == 0


def test_rotation_threshold_15():
    rows = []
    for gi in range(11):
        rows.append((2023, gi, 1, "Star Player", 15.0, 10.0, 1))   # exactly 15 -> in
        rows.append((2023, gi, 2, "Bench Guy", 14.9, 9.0, 1))      # below -> out
    reports = [("Injury-Report_2022-11-12_05PM.pdf",
                [rep_row("11/12/2022", "Player,Star", "Out"), rep_row("11/12/2022", "Guy,Bench", "Out")], [])]
    calc, _ = mk_calc(rows, reports)
    f, _ = calc.team_feats("AAA", 2023, G, D_OK)
    assert f["out_pts"] == pytest.approx(10.0)
    assert f["out_min"] == pytest.approx(15.0 / 240)


def test_fallback_to_last_season():
    rows = []
    for gi in range(30):  # last season (2022): 24 mpg, 12 ppg
        rows.append((2022, gi, 3, "Vet Man", 24.0, 12.0, 1))
    for gi in range(2):  # only 2 games this season -> fewer than 3 -> fallback
        rows.append((2023, gi, 3, "Vet Man", 5.0, 1.0, 1))
    reports = [("Injury-Report_2022-11-12_05PM.pdf", [rep_row("11/12/2022", "Man,Vet", "Out")], [])]
    calc, _ = mk_calc(rows, reports)
    f, info = calc.team_feats("AAA", 2023, G, D_OK)
    assert f["out_pts"] == pytest.approx(12.0) and info["used_prev_fallback"] == 1
    # with a third game this season the current-season (low) numbers are used -> below 15 mpg -> excluded
    rows.append((2023, 2, 3, "Vet Man", 5.0, 1.0, 1))
    calc, _ = mk_calc(rows, reports)
    f, info = calc.team_feats("AAA", 2023, G, D_OK)
    assert f["out_pts"] == 0 and info["used_prev_fallback"] == 0


def test_no_data_at_all_is_zero():
    reports = [("Injury-Report_2022-11-12_05PM.pdf", [rep_row("11/12/2022", "Man,Vet", "Out")], [])]
    calc, _ = mk_calc(base_rows() + [(2023, 0, 9, "Vet Man", 0, 0, 0)], reports)
    f, _ = calc.team_feats("AAA", 2023, G, D_OK)
    assert f["out_pts"] == 0


def test_returning_player_rule():
    rows = base_rows()
    # player 5: rotation-quality (28 mpg, 20 ppg) but appeared in only 4 of last 10 team games (games 7..10 -> played 4)
    for gi in range(11):
        rows.append((2023, gi, 5, "Back Man", 28.0, 20.0, 1 if gi >= 7 else 0))
    # player 6: appeared in exactly 5 of the last 10 team games (gi 6..10) -> NOT returning
    for gi in range(11):
        rows.append((2023, gi, 6, "Five Man", 28.0, 9.0, 1 if gi >= 6 else 0))
    # player 7: returning, but listed Out -> not in back_pts (it goes in out_pts)
    for gi in range(11):
        rows.append((2023, gi, 7, "Out Man", 26.0, 7.0, 1 if gi >= 8 else 0))
    reports = [("Injury-Report_2022-11-12_05PM.pdf", [rep_row("11/12/2022", "Man,Out", "Doubtful")], [])]
    calc, _ = mk_calc(rows, reports)
    f, _ = calc.team_feats("AAA", 2023, G, D_OK)
    assert f["back_pts"] == pytest.approx(20.0)
    assert f["out_pts"] == pytest.approx(7.0)  # Doubtful counts as out
    # Questionable players count in q_pts and (not being OUT/DOUBTFUL) can be back_pts too
    reports = [("Injury-Report_2022-11-12_05PM.pdf", [rep_row("11/12/2022", "Man,Back", "Questionable")], [])]
    calc, _ = mk_calc(rows, reports)
    f, _ = calc.team_feats("AAA", 2023, G, D_OK)
    assert f["q_pts"] == pytest.approx(20.0) and f["back_pts"] == pytest.approx(20.0 + 7.0)


def test_no_lookahead_same_day_and_after_ignored():
    rows = base_rows()
    # a game ON the target ET date and after it, with huge scoring: must not move the averages
    rows.append((2023, 11, 1, "Star Player", 30.0, 99.0, 1))
    rows.append((2023, 12, 1, "Star Player", 30.0, 99.0, 1))
    reports = [("Injury-Report_2022-11-12_05PM.pdf", [rep_row("11/12/2022", "Player,Star", "Out")], [])]
    calc, _ = mk_calc(rows, reports)
    f, _ = calc.team_feats("AAA", 2023, G, D_OK)
    assert f["out_pts"] == pytest.approx(15.5)
    # report rows for another ET date are ignored
    reports = [("Injury-Report_2022-11-12_05PM.pdf", [rep_row("11/13/2022", "Player,Star", "Out")], [])]
    calc, _ = mk_calc(rows, reports)
    f, info = calc.team_feats("AAA", 2023, G, D_OK)
    assert f["out_pts"] == 0 and info["report_state"] == "no_report"


def test_name_matching_examples():
    assert cf.key_report("BagleyIII,Marvin") == cf.key_hoopr("Marvin Bagley III") == "marvinbagley"
    assert cf.key_report("DukeJr.,David") == cf.key_hoopr("David Duke Jr.") == "davidduke"
    assert cf.key_report("JonesGarcia,David") == cf.key_hoopr("David Jones-Garcia") == "davidjonesgarcia"
    assert cf.key_report("Liddell,E.J.") == cf.key_hoopr("E.J. Liddell") == "ejliddell"
    assert cf.key_report("Ndiaye,EliJohn") == cf.key_hoopr("Eli John Ndiaye")
    assert cf.key_report("Porter,Michael") == "michaelporter" != cf.key_hoopr("Michael Porter Jr.") + "x"
    # Michael Porter Jr. (hoopR) vs 'Porter Jr.,Michael' in the report both reduce to michaelporter
    assert cf.key_report("PorterJr.,Michael") == cf.key_hoopr("Michael Porter Jr.")


def test_match_uses_team_pool_and_counts_unmatched():
    reports = [("Injury-Report_2022-11-12_05PM.pdf",
                [rep_row("11/12/2022", "Jr.Mid,Guy", "Out"), rep_row("11/12/2022", "Nobody,Known", "Out")], [])]
    calc, _ = mk_calc(base_rows(), reports)
    assert calc.match("Guy,Mid", "AAA", 2023, G) is not None  # 'Mid Guy Jr.' -> midguy? first=guy,last=mid
    assert calc.match("Nobody,Known", "AAA", 2023, G) is None
    assert calc.match_tier["unmatched"] == 1


def test_pdf_creation_date_parser(tmp_path):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4 /CreationDate (D:20211019183000-04'00') /ModDate (D:20211019190000-04'00')")
    assert cf.pdf_creation_utc(f) == pd.Timestamp("2021-10-19 22:30:00", tz="UTC")
    f.write_bytes(b"/CreationDate (D:20260301141503-05'00')")
    assert cf.pdf_creation_utc(f) == pd.Timestamp("2026-03-01 19:15:03", tz="UTC")
    f.write_bytes(b"nothing")
    assert cf.pdf_creation_utc(f) is None


def test_report_not_listing_game_is_skipped_and_72h_limit():
    d = "11/12/2022"
    other = dict(date=d, time="07:00(ET)", matchup="XXX@YYY", team="BetaBees", player="Z,Z", status="Out")
    reports = [
        ("Injury-Report_2022-11-12_04PM.pdf", [rep_row(d, "Player,Star", "Out")], []),
        ("Injury-Report_2022-11-12_05PM.pdf", [other], []),  # latest <= D but does not list AAA's game
    ]
    calc, _ = mk_calc(base_rows(), reports)
    calc.full2abbr["betabees"] = "BBB"
    f, info = calc.team_feats("AAA", 2023, G, D_OK, opp="CCC")
    assert info["report_file"] == "Injury-Report_2022-11-12_04PM.pdf" and f["out_pts"] == pytest.approx(15.5)
    # only report is older than 72 h before D -> no report
    reports = [("Injury-Report_2022-11-09_04PM.pdf", [rep_row(d, "Player,Star", "Out")], [])]
    calc, _ = mk_calc(base_rows(), reports)
    f, info = calc.team_feats("AAA", 2023, G, D_OK)
    assert info["report_state"] == "no_report" and f["out_pts"] == 0


def test_team_with_no_rows_all_features_zero_and_back_needs_10_games():
    d = "11/12/2022"
    rows = base_rows()
    for gi in range(11):
        rows.append((2023, gi, 5, "Back Man", 28.0, 20.0, 1 if gi >= 7 else 0))
    # the report lists the game through the opponent only: AAA has no rows -> everything 0 (incl. back_pts)
    opp_row = dict(date=d, time="07:00(ET)", matchup="AAA@BBB", team="BetaBees", player="Z,Z", status="Out")
    calc, _ = mk_calc(rows, [("Injury-Report_2022-11-12_05PM.pdf", [opp_row], [])])
    calc.full2abbr["betabees"] = "BBB"
    f, info = calc.team_feats("AAA", 2023, G, D_OK, opp="BBB")
    assert info["report_state"] == "norows" and f["back_pts"] == 0 and f["back_pts_norowsok"] == pytest.approx(20.0)
    # fewer than 10 team games this season -> back_pts 0 even with rows
    rows9 = [r for r in rows if r[1] < 9]
    calc, _ = mk_calc(rows9, [("Injury-Report_2022-11-12_05PM.pdf", [rep_row(d, "Player,Star", "Out")], [])])
    f, _ = calc.team_feats("AAA", 2023, G, D_OK)
    assert f["back_pts"] == 0 and f["back_pts_norowsok"] == 0


def test_player_team_is_most_recent_box_row():
    rows = base_rows()
    rows.append((2023, 11, 1, "Star Player", 30.0, 10.0, 1))  # same-day row ignored (strictly before)
    calc, pb = mk_calc(rows, [])
    assert 1 in calc.latest_team(2023, G)["AAA"]
    # traded: latest row for another team -> not in AAA's pool
    pb2 = pb.copy()
    extra = pb2[(pb2.athlete_id == 1) & (pb2.game_id == "2023010")].copy()
    extra["team_abbreviation"] = "ZZZ"
    extra["tip"] = extra.tip + pd.Timedelta(hours=1)  # same ET day as the last AAA game, still before G
    extra["game_id"] = "9999"
    calc.pb_sorted = pd.concat([calc.pb_sorted, extra]).sort_values(["tip", "game_id"])
    calc.latest_cache = {}
    t = calc.latest_team(2023, G)
    assert extra.etdate.iloc[0] < G
    assert 1 not in t["AAA"] and 1 in t["ZZZ"]


def test_second_amendment_name_rules():
    assert cf.key_hoopr("Nikola Jokić") == "nikolajokic" == cf.key_report("Jokic,Nikola")
    assert cf.key_hoopr("De'Aaron Fox") == cf.key_report("Fox,De'Aaron") == "deaaronfox"
    rows = base_rows() + [(2023, g, 8, "Star Playmaker", 25.0, 9.0, 1) for g in range(11)]
    calc, _ = mk_calc(rows, [])
    assert calc.match("Player,Star", "AAA", 2023, G) == 1          # exact full name
    assert calc.match("Player,S", "AAA", 2023, G) is None           # no initial matching
    assert calc.match("Player,Sta", "AAA", 2023, G) is None         # no prefix matching
    assert calc.match("Playmaker,Bob", "AAA", 2023, G) is None      # never surname alone
    rows += [(2023, g, 9, "Star Player Jr.", 25.0, 9.0, 1) for g in range(11)]
    calc, _ = mk_calc(rows, [])
    assert calc.match("Player,Star", "AAA", 2023, G) is None        # 2+ matches (suffix dropped) -> unmatched


def test_window_3_to_9_games_not_mixed_with_last_season():
    rows = [(2022, g, 3, "Vet Man", 30.0, 30.0, 1) for g in range(20)]
    rows += [(2023, g, 3, "Vet Man", 16.0, 6.0, 1) for g in range(4)]  # 4 games this season
    calc, _ = mk_calc(rows, [])
    assert calc.rates(3, 2023, G) == (16.0, 6.0, "cur")


def test_back_pts_requires_appearance_for_x_this_season():
    rows = base_rows()
    for g in range(11):  # Ghost: rows (DNP) for X every game, never played this season; star last season
        rows.append((2023, g, 5, "Ghost Man", 0, 0, 0))
        rows.append((2022, g, 5, "Ghost Man", 30.0, 25.0, 1))
    rows.append((2023, 0, 6, "Early Man", 28.0, 14.0, 1))  # played once for X this season, then absent
    for g in range(1, 11):
        rows.append((2023, g, 6, "Early Man", 0, 0, 0))
    calc, _ = mk_calc(rows, [("Injury-Report_2022-11-12_05PM.pdf", [rep_row("11/12/2022", "Man,Zed", "Questionable")], [])])
    f, _ = calc.team_feats("AAA", 2023, G, D_OK)
    assert f["back_pts"] == 0  # Ghost excluded (no appearance); Early Man has <3 games this season -> last season data none
