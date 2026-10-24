"""Unit tests for research #62 (E2).  Toy data only (no real outcomes); the 'slow' tests read files already on disk.
Run:  .venv/Scripts/python.exe -m pytest reports/output/nba_score_model_e2/main/test_e2.py -q"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import e2_lib as E  # noqa: E402
import compare as C  # noqa: E402
import run_select as RS  # noqa: E402
import run_test as RT  # noqa: E402

BASE_DAY = pd.Timestamp("2023-01-01")


# ------------------------------------------------------------------ toy world
def toy_box(extra=None, absent=None):
    """BOS (Kalshi) and NYK, season 2022-23 (file year 2023), games on day 0..11.  Players:
    1 'Sam Star' 30 mpg 20 ppg, plays every game;  2 'Lou Low' 14.9 mpg;  3 'Ray Edge' 15.0 mpg;
    4 'Rita Return' 20 mpg 10 ppg, plays only games 0-4;  5 'Ben Back' 20 mpg, plays games 0-5;  6 'Zed Rest' 10 mpg.
    NYK: 11 'Nick One' 25 mpg 12 ppg, 12 'Nate Two' 18 mpg 8 ppg."""
    spec = {1: ("Sam Star", "BOS", 30.0, 20.0, range(12)), 2: ("Lou Low", "BOS", 14.9, 5.0, range(12)),
            3: ("Ray Edge", "BOS", 15.0, 6.0, range(12)), 4: ("Rita Return", "BOS", 20.0, 10.0, range(0, 5)),
            5: ("Ben Back", "BOS", 20.0, 9.0, range(0, 6)), 6: ("Zed Rest", "BOS", 10.0, 2.0, range(12)),
            11: ("Nick One", "NYK", 25.0, 12.0, range(12)), 12: ("Nate Two", "NYK", 18.0, 8.0, range(12))}
    rows = []
    for aid, (nm, team, mpg, ppg, games) in spec.items():
        for g in range(12):
            played = g in games and not (absent and (aid, g) in absent)
            rows.append({"game_id": 1000 + g, "yr": 2023, "d": E.day_int(BASE_DAY + pd.Timedelta(days=g)), "aid": aid, "name": nm,
                         "team": team, "played": played, "minutes": mpg if played else 0.0, "pts": ppg if played else 0.0})
    for r in (extra or []):
        rows.append(r)
    return E.Box(pd.DataFrame(rows))


def toy_games(n=12):
    recs = []
    for g in range(n):
        date = BASE_DAY + pd.Timedelta(days=g)
        tip = int(date.tz_localize("UTC").timestamp()) + 23 * 3600
        for team, tk, opp, ok, venue in (("BOS", "BOS", "NY", "NYK", "away"), ("NY", "NYK", "BOS", "BOS", "home")):
            recs.append({"game_id": 1000 + g, "season": "2022-23", "date": date, "tip": tip, "team": team, "opp": opp, "venue": venue,
                         "team_k": tk, "opp_k": ok})
    return pd.DataFrame(recs)


def tip_of(g):
    return int((BASE_DAY + pd.Timedelta(days=g)).tz_localize("UTC").timestamp()) + 23 * 3600


def report_row(g, player, status, team="BostonCeltics"):
    return {"date": (BASE_DAY + pd.Timedelta(days=g)).strftime("%m/%d/%Y"), "time": "07:00(ET)", "matchup": "BOS@NYK", "team": team,
            "player": player, "status": status}


def toy_reports(g=11, created_off=0, rows=None, nys=None):
    rows = rows if rows is not None else [report_row(g, "Star,Sam", "Out"), report_row(g, "Rita,Return", "Available")]
    created = {"R_late": tip_of(g) - 3600 + created_off, "R_early": tip_of(g) - 5 * 3600}
    content = {"R_late": {"rows": rows, "nys": nys or []},
               "R_early": {"rows": [report_row(g, "Star,Sam", "Available")], "nys": []}}
    return E.Reports(created=created, content=content)


def feats(box, reports, games=None):
    F = E.build_injury_features(games if games is not None else toy_games(), box, reports)
    return F.set_index(["game_id", "team_k"])


# ------------------------------------------------------------------ report cutoff
def test_report_at_D_used_one_second_after_ignored():
    box = toy_box()
    f = feats(box, toy_reports(created_off=0)).loc[(1011, "BOS")]
    assert f.report_name == "R_late" and f.out_pts_own == 20.0
    f = feats(box, toy_reports(created_off=1)).loc[(1011, "BOS")]       # published 1 s after D -> ignored, the earlier report is used
    assert f.report_name == "R_early" and f.out_pts_own == 0.0
    f = feats(box, toy_reports(created_off=-1)).loc[(1011, "BOS")]
    assert f.report_name == "R_late"


def test_report_lookback_72h_and_no_report_gives_zero():
    box = toy_box()
    rep = E.Reports(created={"R": tip_of(11) - 3600 - 73 * 3600}, content={"R": {"rows": [report_row(11, "Star,Sam", "Out")], "nys": []}})
    f = feats(box, rep).loc[(1011, "BOS")]
    assert not f.report_found and f.out_pts_own == 0.0 and f.back_pts_own == 0.0


def test_not_yet_submitted_means_no_information():
    box = toy_box()
    rep = toy_reports(rows=[report_row(11, "One,Nick", "Out", "NewYorkKnicks")], nys=[[(BASE_DAY + pd.Timedelta(days=11)).strftime("%m/%d/%Y"), "BOS@NYK", "BostonCeltics"]])
    F = feats(box, rep)
    b, n = F.loc[(1011, "BOS")], F.loc[(1011, "NYK")]
    assert b.nys and b.out_pts_own == 0.0 and b.back_pts_own == 0.0          # BOS not submitted: all zero, incl. the returning rule
    assert (not n.nys) and n.out_pts_own == 12.0                              # NYK submitted: Nick One Out
    assert b.out_pts_opp == 12.0 and n.out_pts_opp == 0.0                     # opponent columns mirror


def test_team_with_no_rows_in_report_gets_all_zero():
    rep = toy_reports(rows=[report_row(11, "One,Nick", "Out", "NewYorkKnicks")])      # BOS has no rows in the covering report
    b = feats(toy_box(), rep).loc[(1011, "BOS")]
    assert b.report_found and b.out_pts_own == 0.0 and b.back_pts_own == 0.0 and b.out_pts_opp == 12.0


def test_player_team_is_most_recent_row_this_or_last_season():
    roster, last_yr = {1}, {1: 2021}                                  # last row two seasons ago -> not on the team
    f = E.team_features(toy_box(), "BOS", E.day_int(BASE_DAY + pd.Timedelta(days=11)), 2023, roster, last_yr, {"Star,Sam": "Out"}, True, None)
    assert f["out_pts"] == 0.0 and f["n_unmatched"] == 1
    f = E.team_features(toy_box(), "BOS", E.day_int(BASE_DAY + pd.Timedelta(days=11)), 2023, {1}, {1: 2022}, {"Star,Sam": "Out"}, True, None)
    assert f["out_pts"] == 20.0


def test_name_matching_exact_full_name_only():
    names = {1: "Giannis Antetokounmpo", 2: "Thanasis Antetokounmpo", 3: "De'Aaron Fox", 4: "Scotty Pippen Jr.", 5: "Shai Gilgeous-Alexander",
             6: "Nikola Jokić", 7: "Kevin Porter Jr.", 8: "Kevin Porter"}
    cn = {}
    for a, n in names.items():
        cn.setdefault(E.norm_display(n), []).append(a)
    assert E.match_name("Antetokounmpo,Giannis", cn) == 1 and E.match_name("Antetokounmpo,Thanasis", cn) == 2
    assert E.match_name("Antetokounmpo,Alex", cn) is None                   # shared surname, no first-name match -> unmatched
    assert E.match_name("Antetokounmpo,G.", cn) is None                      # never partial
    assert E.match_name("Fox,De'Aaron", cn) == 3 and E.match_name("Fox,DeAaron", cn) == 3
    assert E.match_name("PippenJr.,Scotty", cn) == 4 and E.match_name("Pippen Jr.,Scotty", cn) == 4
    assert E.match_name("Gilgeous-Alexander,Shai", cn) == 5 and E.match_name("Jokic,Nikola", cn) == 6
    assert E.match_name("Porter Jr.,Kevin", cn) is None                      # 'Kevin Porter Jr.' and 'Kevin Porter' collide -> 2 matches
    assert E.match_name("Nobody,Known", cn) is None


def test_window_current_season_never_mixes_seasons():
    # 5 games this season -> those 5 (not padded with last season)
    hd = np.array([1, 2, 3, 4, 5, 6, 7]); hm = np.array([30., 30, 10, 10, 10, 20, 20]); hp = np.array([9., 9, 1, 1, 1, 2, 2])
    hy = np.array([2022, 2022, 2023, 2023, 2023, 2023, 2023])
    mpg, ppg, src = E.player_window(hd, hm, hp, hy, 8, 2023)
    assert src == "cur" and mpg == pytest.approx(14.0) and ppg == pytest.approx(1.4)
    assert E.player_window(hd, hm, hp, hy, 5, 2023)[2] == "prev"             # 2 games this season before day 5 -> last season's games


def test_back_pts_requires_a_game_played_for_team_this_season():
    box = toy_box()
    rep = toy_reports(rows=[report_row(11, "Star,Sam", "Available")])
    played_cur = {a for a in (1, 2, 3, 5, 6)}                                 # Rita (4) has not played for BOS this season in this call
    f = E.team_features(box, "BOS", E.day_int(BASE_DAY + pd.Timedelta(days=11)), 2023, {1, 2, 3, 4, 5, 6}, {a: 2023 for a in range(1, 7)},
                        {"Star,Sam": "Available"}, True, [1001 + i for i in range(10)], played_cur)
    assert f["back_pts"] == 0.0
    f = E.team_features(box, "BOS", E.day_int(BASE_DAY + pd.Timedelta(days=11)), 2023, {1, 2, 3, 4, 5, 6}, {a: 2023 for a in range(1, 7)},
                        {"Star,Sam": "Available"}, True, [1001 + i for i in range(10)], played_cur | {4})
    assert f["back_pts"] == 10.0


def test_traded_away_player_never_counts_in_back_pts():
    box = toy_box()
    extra = pd.DataFrame([{"game_id": 1010, "yr": 2023, "d": E.day_int(BASE_DAY + pd.Timedelta(days=10)), "aid": 4, "name": "Rita Return",
                           "team": "NYK", "played": False, "minutes": 0.0, "pts": 0.0}])      # Rita's latest row is now for NYK
    g = feats(E.Box(pd.concat([box.df, extra], ignore_index=True)), toy_reports(rows=[report_row(11, "Star,Sam", "Available")]))
    assert g.loc[(1011, "BOS")].back_pts_own == 0.0


# ------------------------------------------------------------------ no look-ahead
def test_player_window_strictly_before_date():
    hd = np.array([1, 2, 3, 4, 5]); hm = np.array([10., 20, 30, 40, 50]); hp = np.array([1., 2, 3, 4, 5]); hy = np.full(5, 2023)
    assert E.player_window(hd, hm, hp, hy, 4, 2023) == (20.0, 2.0, "cur")      # games on days 1-3 only: day 4 itself is excluded
    assert E.player_window(hd, hm, hp, hy, 5, 2023)[0] == 25.0
    assert E.player_window(hd, hm, hp, hy, 1, 2023) is None


def test_future_box_and_future_reports_do_not_change_features():
    base = feats(toy_box(), toy_reports()).loc[(1011, "BOS")]
    # change the star's box on the game day itself and everything after: features of game 1011 must not move
    extra = [{"game_id": 1011, "yr": 2023, "d": E.day_int(BASE_DAY + pd.Timedelta(days=11)), "aid": 1, "name": "Sam Star", "team": "BOS",
              "played": True, "minutes": 48.0, "pts": 60.0}]
    box = toy_box()
    df = box.df[~((box.df.aid == 1) & (box.df.game_id == 1011))]
    df = pd.concat([df, pd.DataFrame(extra)], ignore_index=True)
    f2 = feats(E.Box(df), toy_reports()).loc[(1011, "BOS")]
    for c in E.FEAT_COLS:
        assert f2[c] == base[c], c
    # a later report (after D) listing someone else Out does not matter
    rep = toy_reports()
    rep.created.append(tip_of(11) + 10); rep.names.append("R_after")
    rep.content["R_after"] = {"rows": [report_row(11, "Star,Sam", "Available"), report_row(11, "Edge,Ray", "Out")], "nys": []}
    f3 = feats(toy_box(), rep).loc[(1011, "BOS")]
    assert f3.out_pts_own == 20.0 and f3.report_name == "R_late"


def test_same_day_game_not_in_history():
    # two games on the same ET date: the second must not see the first game's minutes
    box = toy_box()
    assert box.stats(1, E.day_int(BASE_DAY + pd.Timedelta(days=11)), 2023)[0] == 30.0
    extra = pd.DataFrame([{"game_id": 2000, "yr": 2023, "d": E.day_int(BASE_DAY + pd.Timedelta(days=11)), "aid": 1, "name": "Sam Star",
                           "team": "BOS", "played": True, "minutes": 1.0, "pts": 0.0}])
    b2 = E.Box(pd.concat([box.df, extra], ignore_index=True))
    assert b2.stats(1, E.day_int(BASE_DAY + pd.Timedelta(days=11)), 2023)[0] == 30.0


# ------------------------------------------------------------------ fallbacks / rotation threshold
def test_rotation_threshold_is_15_inclusive():
    box = toy_box()
    rows = [report_row(11, "Low,Lou", "Out"), report_row(11, "Edge,Ray", "Out")]
    f = feats(box, toy_reports(rows=rows)).loc[(1011, "BOS")]
    assert f.out_pts_own == 6.0 and f.n_out_rot_own == 1                    # Ray Edge (15.0) counts, Lou Low (14.9) does not
    assert abs(f.out_min_own - 15.0 / 240.0) < 1e-12


def test_fallback_to_last_season_when_fewer_than_3_games():
    hd = np.array([-300, -299, -298, 1, 2]); hm = np.array([20., 22, 24, 5, 7]); hp = np.array([8., 9, 10, 1, 1])
    hy = np.array([2022, 2022, 2022, 2023, 2023])
    assert E.player_window(hd, hm, hp, hy, 3, 2023) == (22.0, 9.0, "prev")   # 2 games this season -> last season's window
    hd2, hm2, hp2, hy2 = np.append(hd, 2), np.append(hm, 9.), np.append(hp, 2.), np.append(hy, 2023)
    assert E.player_window(hd2, hm2, hp2, hy2, 3, 2023)[2] == "cur"           # 3 games this season -> current season
    assert E.player_window(hd2[3:], hm2[3:], hp2[3:], hy2[3:], 3, 2023)[0] == pytest.approx((5 + 7 + 9) / 3)
    assert E.player_window(hd[3:], hm[3:], hp[3:], hy[3:], 3, 2023) is None  # 2 games and no last season -> no value
    # window is the last 10 games played only
    n = 15
    assert E.player_window(np.arange(n), np.arange(n, dtype=float), np.ones(n), np.full(n, 2023), n, 2023)[0] == pytest.approx(np.mean(np.arange(5, 15)))


def test_unmatched_name_contributes_zero_and_is_counted():
    f = feats(toy_box(), toy_reports(rows=[report_row(11, "Nobody,Known", "Out"), report_row(11, "Star,Sam", "Questionable")])).loc[(1011, "BOS")]
    assert f.out_pts_own == 0.0 and f.q_pts_own == 20.0 and f.n_unmatched_own == 1 and "Nobody,Known|Out" in f.unmatched_names_own


def test_doubtful_counts_as_out_and_most_severe_status_wins():
    rows = [report_row(11, "Star,Sam", "Questionable"), report_row(11, "Star,Sam", "Doubtful")]
    f = feats(toy_box(), toy_reports(rows=rows)).loc[(1011, "BOS")]
    assert f.out_pts_own == 20.0 and f.q_pts_own == 0.0


# ------------------------------------------------------------------ returning-player rule
def test_returning_player_rule():
    box = toy_box()
    rep = toy_reports(rows=[report_row(11, "Star,Sam", "Available")])
    f = feats(box, rep).loc[(1011, "BOS")]
    # window = games 1..10.  Rita played 1-4 = 4 (<5) -> returning (10 ppg).  Ben played 1-5 = 5 -> not returning.
    assert f.back_pts_own == 10.0 and f.n_back_own == 1
    # listed Out/Doubtful players are excluded from back_pts
    rep = toy_reports(rows=[report_row(11, "Return,Rita", "Out")])
    f = feats(box, rep).loc[(1011, "BOS")]
    assert f.back_pts_own == 0.0 and f.out_pts_own == 10.0
    rep = toy_reports(rows=[report_row(11, "Return,Rita", "Questionable")])
    assert feats(box, rep).loc[(1011, "BOS")].back_pts_own == 10.0           # questionable players are NOT excluded


def test_returning_rule_needs_10_team_games_this_season():
    f = feats(toy_box(), toy_reports(g=9, rows=[report_row(9, "Star,Sam", "Available")]), games=toy_games(10)).loc[(1009, "BOS")]
    assert f.back_pts_own == 0.0                                              # only 9 earlier games


# ------------------------------------------------------------------ team codes
def test_team_code_mapping_six_teams():
    for h, k in (("GS", "GSW"), ("NY", "NYK"), ("SA", "SAS"), ("UTAH", "UTA"), ("WSH", "WAS"), ("NO", "NOP")):
        assert E.to_kalshi(h) == k and E.KALSHI2HOOPR[k] == h
    assert len(set(E.HOOPR2KALSHI.values())) == 30 and set(E.TEAMNAME2CODE.values()) == set(E.HOOPR2KALSHI.values())
    assert E.to_kalshi("DUR") is None                                          # All-Star team: not an NBA franchise


def test_six_teams_end_to_end_report_match():
    """Players of GS / NY / SA / UTAH / WSH / NO (hoopR codes in the box) are matched to a report that uses Kalshi names."""
    rows, recs = [], []
    codes = {"GS": ("GSW", "GoldenStateWarriors"), "NY": ("NYK", "NewYorkKnicks"), "SA": ("SAS", "SanAntonioSpurs"),
             "UTAH": ("UTA", "UtahJazz"), "WSH": ("WAS", "WashingtonWizards"), "NO": ("NOP", "NewOrleansPelicans")}
    aid = 100
    for h, (k, nm) in codes.items():
        aid += 1
        for g in range(5):
            rows.append({"game_id": 3000 + g * 10 + aid, "yr": 2023, "d": E.day_int(BASE_DAY + pd.Timedelta(days=g)), "aid": aid,
                         "name": f"Pat {h.title()}x", "team": E.to_kalshi(h), "played": True, "minutes": 30.0, "pts": 15.0})
    df = pd.DataFrame(rows)
    box = E.Box(df)
    for h, (k, nm) in codes.items():
        a = int(df[df.team == k].aid.iloc[0])
        roster = {a}
        last_yr = {a: 2023}
        listed = {f"{h.title()}x,Pat": "Out"}
        f = E.team_features(box, k, E.day_int(BASE_DAY + pd.Timedelta(days=9)), 2023, roster, last_yr, listed, True, None)
        assert f["out_pts"] == 15.0, (h, f)
    assert E.TEAMNAME2CODE["GoldenStateWarriors"] == "GSW"


# ------------------------------------------------------------------ ridge
def test_ridge_closed_form_matches_normal_equations_and_augmented_lstsq():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(200, 3)); y = X @ [1.0, -2.0, 0.5] + 3 + rng.normal(size=200) * 0.1
    mu, sd = E.standardise_fit(X); Xs = (X - mu) / sd
    lam = 10.0
    b0, b = E.ridge_fit(Xs, y, lam)
    assert b0 == pytest.approx(y.mean())
    assert np.allclose(b, np.linalg.inv(Xs.T @ Xs + lam * np.eye(3)) @ Xs.T @ (y - y.mean()))
    A = np.vstack([np.column_stack([np.ones(200), Xs]), np.column_stack([np.zeros(3), np.sqrt(lam) * np.eye(3)])])
    w = np.linalg.lstsq(A, np.concatenate([y, np.zeros(3)]), rcond=None)[0]
    assert np.allclose(w[0], b0) and np.allclose(w[1:], b)                       # intercept unpenalised (Xs is centred)
    assert np.allclose(E.ridge_predict(X, mu, sd, b0, b), b0 + Xs @ b)
    b_big = E.ridge_fit(Xs, y, 1e12)[1]
    assert np.abs(b_big).max() < 1e-6                                              # huge lambda -> coefficients vanish


def test_loso_folds_and_lambda_selection():
    seasons = np.array(["a"] * 5 + ["b"] * 5)
    folds = E.loso_folds(seasons)
    assert [s for s, _ in folds] == ["a", "b"] and all(m.sum() == 5 for _, m in folds)
    assert not (folds[0][1] & folds[1][1]).any()
    rng = np.random.default_rng(2)
    X = rng.normal(size=(100, 2)); y = rng.normal(size=100)                          # pure noise -> the largest lambda wins
    seas = np.array(["a"] * 50 + ["b"] * 50)
    lam, mse = E.cv_lambda(X, y, seas)
    assert lam == max(E.LAMBDAS) or mse[lam] == min(mse.values())
    # fold prediction uses ONLY the other season: changing the held-out season's y must not move its own prediction
    p1 = E.loso_predict(X, y, seas, 10.0); y2 = y.copy(); y2[:50] += 100
    p2 = E.loso_predict(X, y2, seas, 10.0)
    assert np.allclose(p1[:50], p2[:50]) and not np.allclose(p1[50:], p2[50:])


# ------------------------------------------------------------------ selection / STOP rule
def toy_frames(signal: bool, seed=3):
    rng = np.random.default_rng(seed)
    parts = []
    for s, n in (("2021-22", 200), ("2022-23", 200), ("2023-24", 200)):
        a, b = rng.gamma(2, 4, n), rng.gamma(2, 4, n)
        rows = []
        for i in range(n):
            gid = hash((s, i)) % 10 ** 7 + 1
            for home, (o, p) in ((1, (a[i], b[i])), (0, (b[i], a[i]))):
                rows.append({"game_id": gid, "team": f"{s}_{i}_{home}", "season": s, "home": home, "pts": 100.0 + rng.normal(),
                             "out_pts_own": o if signal else 0.0, "out_pts_opp": p if signal else 0.0,
                             **{c: 0.0 for c in E.FEAT_COLS if c not in ("out_pts_own", "out_pts_opp")}})
        parts.append(pd.DataFrame(rows))
    D = pd.concat(parts, ignore_index=True)
    if signal:
        r = -1.5 * D.out_pts_own - 0.5 * D.out_pts_opp + rng.normal(size=len(D)) * 0.3
    else:
        r = pd.Series(0.0, index=D.index)
    D["e1"] = D.pts - r
    D["r"] = D.pts - D.e1
    return D


def test_stop_rule_when_e1_is_best():
    D = toy_frames(signal=False)
    rec, frozen = RS.select(D[D.season.isin(E.TRAIN)], D[D.season.isin(E.VALID)], log=lambda *_: None)
    assert rec["chosen"] == "E1" and rec["stop"] and frozen is None             # variants equal E1 exactly -> tie -> E1 (conservative)
    assert E.choose({"E1": {"total_mae": 1.0}, "A": {"total_mae": 1.0}, "B": {"total_mae": 1.0}, "C": {"total_mae": 1.0}}) == "E1"
    assert E.choose({"E1": {"total_mae": 1.0}, "A": {"total_mae": 0.9}, "B": {"total_mae": 0.9}, "C": {"total_mae": 1.2}}) == "A"


def test_selection_picks_a_variant_when_features_carry_signal():
    D = toy_frames(signal=True)
    rec, frozen = RS.select(D[D.season.isin(E.TRAIN)], D[D.season.isin(E.VALID)], log=lambda *_: None)
    assert rec["chosen"] != "E1" and frozen is not None and frozen["model"] == rec["chosen"]
    assert rec["candidates"][rec["chosen"]]["total_mae"] < rec["candidates"]["E1"]["total_mae"]
    assert frozen["ridge"]["train_seasons"] == list(E.TRAIN)


def test_residual_frame_merges_one_to_one_and_target_definition():
    rows = pd.DataFrame({"game_id": [1, 1], "team": ["A", "B"], "season": "2021-22", "home": [0, 1], "pts": [100.0, 110.0]})
    inj = pd.DataFrame({"game_id": [1, 1], "team": ["A", "B"], **{c: [1.0, 2.0] for c in E.FEAT_COLS}})
    D = RS.residual_frame(rows, inj, np.array([98.0, 112.0]))
    assert list(D.r) == [2.0, -2.0]
    with pytest.raises(AssertionError):
        RS.residual_frame(rows, inj.iloc[:1], np.array([98.0, 112.0]))


# ------------------------------------------------------------------ gates / hashes
def make_study(tmp_path, files=("a.py",), agreement="PASS", frozen=True, audit=True):
    study = tmp_path / "study"; base = tmp_path / "main"; base.mkdir(parents=True); study.mkdir()
    for f in files:
        (base / f).write_text("x = 1\n")
    (study / "injury_features.parquet").write_bytes(b"features")
    if frozen:
        blob = json.dumps({"model": "A", "ridge": {}}).encode()
        (study / "frozen_e2.json").write_bytes(blob)
        (study / "frozen_e2.sha256").write_text(E.sha256_bytes(blob) + "  frozen_e2.json\n")
    (study / "AGREEMENT.md").write_text(f"STATUS: {agreement}\nFEATURES_SHA256: {E.sha256_file(study / 'injury_features.parquet')}\n")
    if audit:
        (study / "AUDIT_OK").write_text(json.dumps(E.audit_hashes(base, files)))
    return study, base, files


def test_run_test_refusal_gates(tmp_path):
    study, base, files = make_study(tmp_path)
    assert RT.check_gates(study, base, files)["model"] == "A"                    # everything in order -> passes
    (study / "AUDIT_OK").unlink()
    with pytest.raises(SystemExit, match="AUDIT_OK not found"):
        RT.check_gates(study, base, files)
    (study / "AUDIT_OK").write_text("not json")
    with pytest.raises(SystemExit, match="not JSON"):
        RT.check_gates(study, base, files)
    (study / "AUDIT_OK").write_text(json.dumps({"sha256": {}}))
    with pytest.raises(SystemExit, match="does not cover"):
        RT.check_gates(study, base, files)
    (study / "AUDIT_OK").write_text(json.dumps(E.audit_hashes(base, files)))
    (base / "a.py").write_text("x = 2\n")                                        # code changed after the audit
    with pytest.raises(SystemExit, match="hash mismatch"):
        RT.check_gates(study, base, files)


def test_run_test_refuses_without_frozen_bad_frozen_hash_agreement_and_second_run(tmp_path):
    study, base, files = make_study(tmp_path, frozen=False)
    with pytest.raises(SystemExit, match="frozen_e2.json not found"):
        RT.check_gates(study, base, files)
    study, base, files = make_study(tmp_path / "b")
    (study / "frozen_e2.json").write_text("{}")
    with pytest.raises(SystemExit, match="hash"):
        RT.check_gates(study, base, files)
    study, base, files = make_study(tmp_path / "c", agreement="FAIL")
    with pytest.raises(SystemExit, match="PASS"):
        RT.check_gates(study, base, files)
    study, base, files = make_study(tmp_path / "d")
    (study / "injury_features.parquet").write_bytes(b"features changed")           # parquet changed after AGREEMENT
    with pytest.raises(SystemExit):
        RT.check_gates(study, base, files)
    study, base, files = make_study(tmp_path / "e")
    RT.start_test(study)
    with pytest.raises(SystemExit, match="test_started.json exists"):
        RT.check_gates(study, base, files)
    study, base, files = make_study(tmp_path / "f")
    (study / "results_test.json").write_text("{}")
    with pytest.raises(SystemExit, match="results_test.json exists"):
        RT.check_gates(study, base, files)


def test_run_select_gate(tmp_path):
    study, base, files = make_study(tmp_path, agreement="PASS", frozen=False, audit=False)
    RS.gate(study)
    (study / "results_select.json").write_text("{}")
    with pytest.raises(SystemExit, match="selection runs once"):
        RS.gate(study)
    study2, _, _ = make_study(tmp_path / "x", agreement="FAIL", frozen=False, audit=False)
    with pytest.raises(SystemExit, match="PASS"):
        RS.gate(study2)
    study3 = tmp_path / "none"; study3.mkdir()
    with pytest.raises(SystemExit, match="AGREEMENT.md not found"):
        RS.gate(study3)


def test_audit_file_list_covers_what_the_test_needs():
    need = ["e2_lib.py", "run_test.py", "run_select.py", "features.py", "compare.py", "../frozen_e2.json", "../injury_features.parquet",
            "../../nba_score_model/frozen.json", "../../nba_model/injury_parsed_v3.json", "../../nba_teammate_out/injury_parsed_tm.jsonl",
            "../../../../data/raw/nba_injury/_probe_cache_v3.json", "../../../../data/raw/nba_open/player_box_2021.parquet",
            "../../../../data/raw/nba_open/player_box_2026.parquet", "../../../../data/raw/nba_odds_samples/closing_lines_odds_api.parquet"]
    for f in need:
        assert f in E.AUDIT_FILES, f
    assert len(set(E.AUDIT_FILES)) == len(E.AUDIT_FILES)


# ------------------------------------------------------------------ sampling, compare, bootstrap, splits
def test_sampling_is_deterministic_sorted_and_200():
    F = pd.DataFrame({"game_id": np.repeat(np.arange(3000), 2), "season": "2021-22",
                      "date": pd.Timestamp("2022-01-01") + pd.to_timedelta(np.repeat(np.arange(3000), 2) // 10, "D"),
                      "team": np.tile(["A", "B"], 3000)}).sample(frac=1, random_state=0)
    s1, s2 = E.sample_ids(F), E.sample_ids(F.sample(frac=1, random_state=5))
    assert len(s1) == 200 and s1.equals(s2) and not s1.duplicated(["game_id", "team"]).any()
    assert list(s1.game_id) == sorted(s1.game_id)
    P = F.sort_values(["season", "date", "game_id", "team"]).reset_index(drop=True)
    assert list(s1.game_id) == list(P.game_id.values[np.sort(np.random.default_rng(20261024).choice(6000, 200, replace=False))])


def test_compare_frames_pass_and_fail():
    A = pd.DataFrame({"game_id": [1, 2], "team": ["A", "B"], **{c: [1.0, 2.0] for c in E.FEAT_COLS}})
    sample = A[["game_id", "team"]]
    assert C.compare_frames(A, A.copy(), sample)["pass"]
    B = A.copy(); B.loc[0, "q_pts_own"] += 1e-6
    r = C.compare_frames(A, B, sample)
    assert not r["pass"] and r["mismatching_columns"] == ["q_pts_own"]
    B = A.copy(); B.loc[0, "q_pts_own"] += 1e-12
    assert C.compare_frames(A, B, sample)["pass"]
    assert not C.compare_frames(A, A.iloc[:1], sample)["pass"]                  # missing row
    B = A.copy(); B.loc[0, "out_min_opp"] = np.nan
    assert not C.compare_frames(A, B, sample)["pass"]                           # NaN pattern
    assert not C.compare_frames(A, A.drop(columns=["back_pts_own"]), sample)["pass"]


def test_paired_bootstrap_deterministic_and_sign():
    d = np.random.default_rng(0).normal(-0.5, 1.0, 400)
    a, b = E.paired_boot_ci(d, n_boot=2000), E.paired_boot_ci(d, n_boot=2000)
    assert a == b and a[1] < a[0] < a[2] and a[2] < 0
    assert E.BOOT_N == 10000 and E.BOOT_SEED == 20261023


def test_split_by_key_players_out_and_paired_block():
    gids = np.repeat(np.arange(1, 7), 2)
    D = pd.DataFrame({"game_id": gids, "team": np.tile(["a", "b"], 6), "home": np.tile([0, 1], 6), "season": "2024-25",
                      "pts": 100.0})
    p1, p2 = np.full(12, 105.0), np.full(12, 102.0)
    nkey = pd.Series({1: 0, 2: 0, 3: 1, 4: 1, 5: 2, 6: 3})
    out = RT.split_by_key_out(D, p1, p2, nkey)
    assert out["0"]["n_games"] == 2 and out["1"]["n_games"] == 2 and out["2+"]["n_games"] == 2
    assert out["0"]["E1_total_mae"] == 10.0 and out["0"]["E2_total_mae"] == 4.0 and out["0"]["diff_E2_minus_E1"] == -6.0
    pb = RT.paired_block(D, p1, p2)
    assert pb["pooled"]["diff"] == -6.0 and pb["ci_upper_below_zero"]


def test_key_out_counts_after_the_fact_box():
    box = toy_box(absent={(1, 11)})
    D = pd.DataFrame({"game_id": [1011, 1011], "team_k": ["BOS", "NYK"], "key_aids_own": ["1", "11;12"]})
    assert RT.key_out_counts(D, box).loc[1011] == 1                              # Sam Star did not play game 11


def test_metrics_hand_example():
    m = E.game_metrics([1, 1, 2, 2], [0, 1, 0, 1], [100, 110, 90, 95], [101, 112, 80, 99])
    assert m["total_mae"] == pytest.approx((3 + 6) / 2) and m["team_mae"] == pytest.approx((1 + 2 + 10 + 4) / 4)
    assert m["bias"] == pytest.approx((3 - 6) / 2) and m["pct_within_10"] == 1.0


# ------------------------------------------------------------------ real files (slow)
@pytest.mark.slow
def test_load_e1_does_not_clobber_this_folders_modules():
    import features as F2
    X = E.load_e1()
    assert Path(sys.modules["features"].__file__).parent == HERE and Path(sys.modules["run_test"].__file__).parent == HERE
    assert Path(sys.modules["compare"].__file__).parent == HERE and sys.modules["features"] is F2
    assert Path(X["RT"].__file__).parent == E.E1_MAIN and sys.path[0] == str(HERE)


@pytest.mark.slow
def test_real_feature_file_integrity():
    if not E.FEATURES_PARQUET.exists():
        pytest.skip("features not built")
    F = pd.read_parquet(E.FEATURES_PARQUET)
    assert len(F) == 12306 and not F.duplicated(["game_id", "team"]).any() and set(F.season) == set(E.SEASONS)
    assert F[E.FEAT_COLS].notna().all().all() and (F[E.FEAT_COLS] >= 0).all().all()
    for h in ("GS", "NY", "SA", "UTAH", "WSH", "NO"):
        sub = F[F.team == h]
        assert len(sub) > 400 and sub.team_k.iloc[0] == E.to_kalshi(h)
        assert sub.report_found.mean() > 0.99 and sub.out_pts_own.sum() > 0           # reports matched for the six code-mismatch teams
    # every game has both rows and the opponent columns mirror the opponent's own columns
    g = F.sort_values(["game_id", "team"]).reset_index(drop=True)
    a, b = g.iloc[0::2].reset_index(drop=True), g.iloc[1::2].reset_index(drop=True)
    assert (a.game_id == b.game_id).all() and np.allclose(a.out_pts_own, b.out_pts_opp) and np.allclose(b.back_pts_own, a.back_pts_opp)
    assert (F.D == F.tip - 3600).all() and (F.report_created.dropna() <= F.D[F.report_created.notna()]).all()
