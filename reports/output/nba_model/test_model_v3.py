"""Tests for research log #32. Run: .venv/Scripts/python.exe -m pytest reports/output/nba_model/test_model_v3.py"""
import copy
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import data_v3 as D  # noqa: E402
import features_v3 as F3  # noqa: E402
import injury_parse as IP  # noqa: E402
import model_v3 as M3  # noqa: E402


# ------------------------------------------------------------------ American odds math / de-vig
def test_implied_probability():
    assert math.isclose(D.implied_prob(100), 0.5)
    assert math.isclose(D.implied_prob(-100), 0.5)
    assert math.isclose(D.implied_prob(-150), 0.6)
    assert math.isclose(D.implied_prob(150), 0.4)
    assert math.isclose(D.implied_prob(-200), 2 / 3)
    assert math.isclose(D.implied_prob(300), 0.25)


def test_profit_per_dollar():
    assert math.isclose(D.profit_per_dollar(150, True), 1.5)
    assert math.isclose(D.profit_per_dollar(-200, True), 0.5)
    assert math.isclose(D.profit_per_dollar(-110, True), 100 / 110)
    assert D.profit_per_dollar(150, False) == -1.0 and D.profit_per_dollar(-200, False) == -1.0
    # fair price: expected profit zero at the implied probability
    for o in (-300, -110, 105, 250):
        p = D.implied_prob(o)
        assert abs(p * D.profit_per_dollar(o, True) + (1 - p) * -1.0) < 1e-12


def test_devig():
    assert math.isclose(D.devig_home(-110, -110), 0.5)
    h = D.devig_home(-150, 130)          # 0.6 and 0.43478 -> normalised
    assert math.isclose(h, 0.6 / (0.6 + 100 / 230))
    assert 0 < h < 1 and D.devig_home(-150, 130) + D.devig_home(130, -150) == pytest.approx(1.0)
    # the book's margin: raw implied probabilities sum above 1, de-vigged ones are strictly smaller
    assert D.implied_prob(-150) + D.implied_prob(130) > 1.0
    assert h < D.implied_prob(-150)


def test_odds_parsing():
    assert D.to_int_odds("+145\xa0") == 145
    assert D.to_int_odds("-125") == -125
    assert D.to_int_odds(105) == 105
    assert D.to_int_odds(None) is None and D.to_int_odds("n/a") is None


def test_ml_bets_one_per_game_and_pnl():
    rows = [{"gid": "g1", "home_win": 1.0, "stype": 2, "covered": 0.0}, {"gid": "g2", "home_win": 0.0, "stype": 3, "covered": 1.0},
            {"gid": "g3", "home_win": 1.0, "stype": 2, "covered": 0.0}]
    odds = {"g1": (-200, 170), "g2": (-200, 170), "g3": (-110, -110)}
    # g1: model 0.80 home -> home edge 0.80-0.667 = 0.133; away edge 0.2-0.370 <0 -> bet home, wins +0.5
    # g2: model 0.40 home -> home edge -0.267, away edge 0.6-0.370 = 0.23 -> bet away (+170), away wins -> +1.7
    # g3: model 0.51 home -> edge 0.51-0.5238 < 0 both -> no bet
    b = M3.ml_bets(rows, [0.80, 0.40, 0.51], odds, 0.04)
    assert [x["gid"] for x in b] == ["g1", "g2"]
    assert b[0]["side"] == 0 and math.isclose(b[0]["pnl"], 0.5) and b[0]["fav"]
    assert b[1]["side"] == 1 and math.isclose(b[1]["pnl"], 1.7) and not b[1]["fav"] and b[1]["stype"] == 3
    assert math.isclose(b[1]["pnl_both"][0], -1.0) and math.isclose(b[1]["pnl_both"][1], 1.7)
    # theta filter and tie -> home
    assert M3.ml_bets(rows, [0.80, 0.40, 0.51], odds, 0.20)[0]["gid"] == "g2"
    tie = M3.ml_bets([rows[2]], [0.5], {"g3": (-110, -110)}, -0.1)
    assert len(tie) == 1 and tie[0]["side"] == 0


# ------------------------------------------------------------------ odds <-> box join on a toy example
def toy_box_games(specs):
    g = {}
    for gid, dt, home, away, season in specs:
        g[gid] = {"gid": gid, "date": dt, "season": season, "stype": 2, "tip": 0.0, "home": home, "away": away, "hs": 100, "as_": 90,
                  "players": defaultdict(dict)}
    return {"games": g, "by_key": {(v["date"], v["home"], v["away"]): k for k, v in g.items()}, "all_names": defaultdict(set)}


def orow(dt, home, away, mh=-150, ma=130):
    return {"date": dt, "home_name": home, "away_name": away, "home": D.NAME2CODE.get(home), "away": D.NAME2CODE.get(away),
            "ml_h": mh, "ml_a": ma, "spread": 0.0, "ou": 0.0}


def test_join_toy():
    box = toy_box_games([("a", "2022-01-10", "LAC", "BOS", 2022), ("b", "2022-01-11", "CHA", "NYK", 2022),
                         ("c", "2022-01-12", "LAL", "MIA", 2022), ("d", "2022-01-20", "DEN", "UTA", 2022),
                         ("e", "2022-01-25", "PHX", "DAL", 2022)])
    odds = [orow("2022-01-10", "Los Angeles Clippers", "Boston Celtics"),      # naming: Los Angeles Clippers -> LAC, exact date
            orow("2022-01-11", "Charlotte Bobcats", "New York Knicks"),         # old name in the 2021-22 file
            orow("2022-01-13", "Los Angeles Lakers", "Miami Heat"),             # shifted one day late -> +-1 day tolerance (-1)
            orow("2022-01-20", "Denver Nuggets", "Phoenix Suns"),               # away team disagrees -> not joined
            orow("2022-02-01", "Boston Celtics", "Miami Heat"),                 # no such game -> unmatched
            orow("2022-01-25", "Phoenix Suns", "Dallas Mavericks", None, 130),  # missing moneyline -> dropped
            orow("2022-01-26", "Mystery Team", "Dallas Mavericks")]             # unknown team name
    m, rep, summ = D.join_odds(box, odds, 2022)
    assert set(m) == {"a", "b", "c"}
    assert m["c"]["day_offset"] == -1 and m["a"]["day_offset"] == 0
    assert D.NAME2CODE["LA Clippers"] == D.NAME2CODE["Los Angeles Clippers"] == "LAC"
    assert len(rep["away_mismatch"]) == 1 and len(rep["odds_row_unmatched"]) == 1
    assert len(rep["missing_or_bad_moneyline"]) == 1 and len(rep["unknown_team_name"]) == 1
    assert sorted(x[1] for x in rep["box_games_without_odds"]) == ["DEN", "PHX"]
    assert summ["matched"] == 3 and summ["box_games"] == 5


def test_join_does_not_reuse_a_game_and_respects_season():
    box = toy_box_games([("a", "2022-01-10", "LAC", "BOS", 2022), ("z", "2023-01-10", "LAC", "BOS", 2023)])
    odds = [orow("2022-01-10", "LA Clippers", "Boston Celtics"), orow("2022-01-10", "LA Clippers", "Boston Celtics")]
    m, rep, _ = D.join_odds(box, odds, 2022)
    assert list(m) == ["a"] and len(rep["odds_row_unmatched"]) == 1     # the second row cannot reuse game 'a'; 2023 game not eligible


# ------------------------------------------------------------------ Elo carry-over and no look-ahead across seasons
def mk_game(gid, season, day, home, away, hs, as_, players=None, hour=19):
    d = date(2021 if season == 2022 else 2022, 11, 1) + timedelta(days=day)
    if season == 2023:
        d = date(2022, 11, 1) + timedelta(days=day)
    tip = datetime(d.year, d.month, d.day, hour, 0, tzinfo=timezone.utc).timestamp()
    return {"gid": gid, "date": d.isoformat(), "season": season, "stype": 2, "tip": tip, "home": home, "away": away, "hs": hs, "as_": as_,
            "players": players or defaultdict(dict)}


def toy_box(games, names=None):
    gd = {g["gid"]: g for g in games}
    return {"games": gd, "by_key": {}, "all_names": names or defaultdict(set)}


def toy_games():
    g = []
    t = [("BOS", "NYK"), ("NYK", "BOS"), ("BOS", "NYK")]
    for k, (h, a) in enumerate(t):
        g.append(mk_game(f"s1_{k}", 2022, 2 * k, h, a, 110 + 3 * k, 100))
    for k, (h, a) in enumerate(t):
        g.append(mk_game(f"s2_{k}", 2023, 2 * k, h, a, 105, 99 + 5 * k))
    return g


def test_season_carry_formula():
    assert F3.season_carry(1605.0) == pytest.approx(0.75 * 1605 + 0.25 * 1505)
    assert F3.season_carry(1505.0) == pytest.approx(1505.0)
    assert F3.season_carry(1400.0) == pytest.approx(1426.25)


def test_elo_carries_over_with_shrinkage():
    box = toy_box(toy_games())
    rows, st = F3.build_features_v3(box, {}, {})
    by = {r["gid"]: r for r in rows}
    # end-of-season-1 ratings = the first season-2 game's ratings are the carry of the ratings after season 1's last game
    # reproduce season 1 with the same Elo update
    import features as F
    elo = {"BOS": 1500.0, "NYK": 1500.0}
    for k in range(3):
        g = box["games"][f"s1_{k}"]
        elo[g["home"]], elo[g["away"]] = F.elo_update(elo[g["home"]], elo[g["away"]], g["hs"] > g["as_"], abs(g["hs"] - g["as_"]))
    assert by["s1_0"]["elo_h"] == 1500.0                                   # first season starts at 1500
    assert by["s2_0"]["elo_h"] == pytest.approx(F3.season_carry(elo["BOS"]))
    assert by["s2_0"]["elo_a"] == pytest.approx(F3.season_carry(elo["NYK"]))
    assert abs(by["s2_0"]["elo_h"] - 1505) < abs(elo["BOS"] - 1505)          # pulled toward 1505
    assert st["season_rollovers"] == 1
    # within-season stats reset: first game of season 2 has no recent margin and full rest cap
    assert by["s2_0"]["net_h"] == 0.0 and by["s2_0"]["rest_h"] == 4.0 and by["s2_0"]["b2b_h"] == 0.0


def feature_keys(r):
    return {k: v for k, v in r.items() if k not in ("margin", "home_win")}


def test_no_lookahead_across_seasons():
    base = toy_games()
    rows0, _ = F3.build_features_v3(toy_box(copy.deepcopy(base)), {}, {})
    by0 = {r["gid"]: r for r in rows0}
    # (1) changing ANY season-2 result must not change season-1 rows, nor season-2 rows with an earlier/equal tip
    mod = copy.deepcopy(base)
    for g in mod:
        if g["gid"] == "s2_1":
            g["hs"], g["as_"] = 60, 130
    by1 = {r["gid"]: r for r in F3.build_features_v3(toy_box(mod), {}, {})[0]}
    for gid in ("s1_0", "s1_1", "s1_2", "s2_0", "s2_1"):
        assert feature_keys(by1[gid]) == feature_keys(by0[gid]), gid       # s2_1's own features use only earlier games
    assert by1["s2_2"]["elo_h"] != by0["s2_2"]["elo_h"]                    # a LATER game does see the changed result
    # (2) changing the last season-1 game DOES change the season-2 start (carry), and never season-1 rows before it
    mod = copy.deepcopy(base)
    for g in mod:
        if g["gid"] == "s1_2":
            g["hs"], g["as_"] = 90, 120
    by2 = {r["gid"]: r for r in F3.build_features_v3(toy_box(mod), {}, {})[0]}
    for gid in ("s1_0", "s1_1", "s1_2"):
        assert feature_keys(by2[gid]) == feature_keys(by0[gid])
    assert by2["s2_0"]["elo_h"] != by0["s2_0"]["elo_h"]
    # (3) same-tip games are computed from the state before that tip
    two = [mk_game("x1", 2022, 0, "BOS", "NYK", 100, 90), mk_game("x2", 2022, 0, "LAL", "MIA", 100, 90),
           mk_game("x3", 2022, 2, "BOS", "LAL", 100, 90)]
    byx = {r["gid"]: r for r in F3.build_features_v3(toy_box(two), {}, {})[0]}
    assert byx["x1"]["elo_h"] == 1500.0 and byx["x2"]["elo_h"] == 1500.0 and byx["x3"]["elo_h"] > 1500.0
    assert byx["x3"]["elo_a"] > 1500.0                                      # LAL won x2 as home


def ply(minutes, pm):
    return (minutes, pm, 10.0)


def test_injury_value_same_season_and_first_five_games():
    games = []
    # last season: Star played 10 games for BOS with strong plus-minus; this season he has played only 4 before the test game
    for k in range(10):
        games.append(mk_game(f"o{k}", 2022, k, "BOS", "NYK", 100, 90, {"BOS": {"jaysonstar": ply(30, 10), "bob": ply(20, 1)}}))
    for k in range(4):
        games.append(mk_game(f"n{k}", 2023, k, "BOS", "NYK", 100, 90, {"BOS": {"jaysonstar": ply(30, 10), "bob": ply(20, 1)}}))
    games.append(mk_game("t4", 2023, 4, "BOS", "NYK", 100, 90, {"BOS": {"jaysonstar": ply(30, 10)}}))
    games.append(mk_game("t5", 2023, 5, "BOS", "NYK", 100, 90, {"BOS": {"jaysonstar": ply(30, 10)}}))
    names = defaultdict(set)
    names["jaysonstar"] = {(2022, "BOS"), (2023, "BOS")}
    box = toy_box(games, names)

    def rep(day):
        d = date(2022, 11, 1) + timedelta(days=day)
        return {"rows": [{"date": d.strftime("%m/%d/%Y"), "time": "07:00(ET)", "matchup": "NYK@BOS", "team": "BostonCeltics",
                          "player": "Star,Jayson", "status": "Out"}], "nys": [], "created_utc": 0, "file": "x", "header": None}

    reports = {"t4": rep(4), "t5": rep(5)}
    rows, st = F3.build_features_v3(box, reports, {"BostonCeltics": "BOS", "NewYorkKnicks": "NYK"})
    by = {r["gid"]: r for r in rows}
    # t4: he has played 4 games this season (< 5): shrinkage only -> value 0 although last season's 10 strong games exist
    assert by["t4"]["covered"] == 1.0 and by["t4"]["inj_diff"] == 0.0
    assert by["t4"]["inj_diff_v2"] > 0 and st["value_zeroed_first5"] >= 1
    # t5: he has now played 5 games this season -> v2 recipe: window = 5 games (n=5), pm/min=1/3, avg 30 -> 10 * 5/15; Out -> p=1
    assert by["t5"]["inj_diff"] == pytest.approx(10.0 * 5 / 15)
    # last season's games never enter the window: with only previous-season games the value would be 0 (n=0)
    assert by["t5"]["inj_diff"] == by["t5"]["inj_diff_v2"]


def test_injury_report_only_same_matchup_and_date():
    games = [mk_game("a", 2022, 0, "BOS", "NYK", 100, 90), mk_game("b", 2022, 3, "BOS", "NYK", 100, 90)]
    rep_other_day = {"rows": [{"date": "11/09/2021", "time": "", "matchup": "NYK@BOS", "team": "BostonCeltics", "player": "X,Y", "status": "Out"}],
                     "nys": [], "created_utc": 0, "file": "x", "header": None}
    rows, _ = F3.build_features_v3(toy_box(games), {"b": rep_other_day}, {"BostonCeltics": "BOS"})
    by = {r["gid"]: r for r in rows}
    assert by["b"]["report_found"] == 1.0 and by["b"]["covered"] == 0.0 and by["b"]["inj_diff"] == 0.0


def test_injury_parse_name_forms_old_layout():
    # the old-layout parser glues words, so these forms must normalise like the new layout
    assert IP.norm_injury_name("TrentJr.,Gary") == IP.norm_box_name("Gary Trent Jr.") == "garytrent"
    assert IP.norm_injury_name("BagleyIII,Marvin") == IP.norm_box_name("Marvin Bagley III") == "marvinbagley"
