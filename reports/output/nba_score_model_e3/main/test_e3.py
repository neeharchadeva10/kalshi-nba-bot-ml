"""Unit tests for research #64 (E3).  Toy data only (no real outcomes); the 'slow' tests read files already on disk (features, box scores).
Run:  .venv/Scripts/python.exe -m pytest reports/output/nba_score_model_e3/main/test_e3.py -q"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import e3_lib as L  # noqa: E402
import compare as C  # noqa: E402
import run_select as RS  # noqa: E402
import run_test as RT  # noqa: E402

E2 = L.E2
BASE_DAY = pd.Timestamp("2023-01-01")


# ------------------------------------------------------------------ toy world
def dnum(off, base=BASE_DAY):
    return E2.day_int(base + pd.Timedelta(days=off))


# aid: (name, team for 2023 games g in range, mpg, stats dict (pts, reb, ast, tpm, dfn, usg, pm), games)
BOS_STATS = {
    1: ("Sam Star", "BOS", 30.0, dict(pts=20.0, reb=8.0, ast=5.0, tpm=2.0, dfn=3.0, usg=25.0, pm=4.0)),
    2: ("Ann Mid", "BOS", 22.0, dict(pts=10.0, reb=4.0, ast=2.0, tpm=1.0, dfn=2.0, usg=12.0, pm=-2.0)),
    3: ("Ben Bench", "BOS", 12.0, dict(pts=4.0, reb=2.0, ast=1.0, tpm=0.0, dfn=1.0, usg=6.0, pm=1.0)),
    4: ("Lou Low", "BOS", 9.9, dict(pts=3.0, reb=1.0, ast=1.0, tpm=0.0, dfn=0.0, usg=4.0, pm=0.0)),
    11: ("Nick One", "NYK", 25.0, dict(pts=12.0, reb=5.0, ast=3.0, tpm=1.0, dfn=1.0, usg=14.0, pm=1.0)),
    12: ("Nate Two", "NYK", 18.0, dict(pts=8.0, reb=3.0, ast=2.0, tpm=1.0, dfn=1.0, usg=9.0, pm=0.0)),
}


def toy_box(extra_rows=None, var_last=None):
    """BOS and NYK, season 2022-23 (file year 2023) games 0..11, plus 2022 games (yr 2022) for BOS: Sam, Gone Guy (aid 5) 20 mpg.
    aid 7 'Var Guy' (BOS) plays every game with pts = game index g (window-mean test); `var_last` overrides his pts in game 11.
    aid 6 'Mo New' plays NYK games 0-4 and BOS games 5-11 at 20 mpg."""
    rows = []

    def add(gid, yr, d, aid, name, team, mpg, st, played=True):
        rows.append({"game_id": gid, "yr": yr, "d": d, "aid": aid, "name": name, "team": team, "played": played,
                     "minutes": mpg if played else 0.0, "pts": st.get("pts", 0.0) if played else 0.0,
                     "reb": st.get("reb", 0.0), "ast": st.get("ast", 0.0), "tpm": st.get("tpm", 0.0), "dfn": st.get("dfn", 0.0),
                     "usg": st.get("usg", 0.0), "pm": st.get("pm", 0.0)})

    for g in range(12):
        for aid, (nm, team, mpg, st) in BOS_STATS.items():
            add(1000 + g, 2023, dnum(g), aid, nm, team, mpg, st)
        v = float(g) if not (var_last is not None and g == 11) else var_last
        add(1000 + g, 2023, dnum(g), 7, "Var Guy", "BOS", 20.0, dict(pts=v, reb=2 * v, ast=v, tpm=0.0, dfn=0.0, usg=v, pm=v))
        add(1000 + g, 2023, dnum(g), 6, "Mo New", "NYK" if g < 5 else "BOS", 20.0, dict(pts=5.0))
    for g in range(3):                                   # last season (file year 2022), BOS
        gid, d = 900 + g, dnum(g - 60)
        add(gid, 2022, d, 1, "Sam Star", "BOS", 30.0, BOS_STATS[1][3])
        add(gid, 2022, d, 5, "Gone Guy", "BOS", 20.0, dict(pts=7.0))
    for r in (extra_rows or []):
        rows.append(r)
    return L.Box3(pd.DataFrame(rows))


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


def reports_of(specs, g=11):
    """specs: list of (name, created offset from tip in seconds (negative = before), [(player, status)])."""
    created, content = {}, {}
    for name, off, listing in specs:
        created[name] = tip_of(g) + off
        content[name] = {"rows": [report_row(g, p, s) for p, s in listing], "nys": []}
    return E2.Reports(created=created, content=content)


AGES = L.AgeBook({1: "1990-06-01", 2: "2001-03-01", 3: None, 4: "1995-01-01", 7: "1985-01-01"})
NO_MOVES = L.Moves(pd.DataFrame({"athlete_id": [], "from_team": [], "to_team": [], "first_date_new": [], "season_new": []}))


def feats(box, reports, moves=None, ages=AGES, games=None):
    F = L.build_features(games if games is not None else toy_games(), box, reports, ages, moves or NO_MOVES)
    return F.set_index(["game_id", "team_k"])


# ------------------------------------------------------------------ window = e2_lib.player_window (identical behaviour)
def test_window3_matches_e2_player_window_on_every_day():
    box = toy_box()
    for aid in (1, 2, 5, 6, 7):
        for off in range(-70, 14):
            d = dnum(off)
            w3 = box.window3(aid, d, 2023)
            w2 = box.stats(aid, d, 2023)
            assert (w3 is None) == (w2 is None), (aid, off)
            if w3 is not None:
                assert abs(w3[0][0] - w2[0]) < 1e-12 and abs(w3[0][1] - w2[1]) < 1e-12 and w3[1] == w2[2]


# ------------------------------------------------------------------ each family's math on a toy box
def fam(listed, box=None, ages=AGES, d_off=11):
    box = box or toy_box()
    roster = {1, 2, 3, 4, 7}
    return L.report_family_features(box, "BOS", dnum(d_off), 2023, roster, {a: 2023 for a in roster}, listed, ages,
                                    BASE_DAY + pd.Timedelta(days=d_off))


def test_all_families_math():
    f = fam({"Star,Sam": "Out", "Mid,Ann": "Doubtful", "Bench,Ben": "Questionable", "Low,Lou": "Out"})
    assert f["n_out_rot"] == 2 and f["n_q_rot"] == 1                  # Lou Low (9.9 mpg) is not rotation-quality
    assert f["pts"] == 30.0                                          # PTS: 20 + 10
    assert abs(f["min"] - 52.0 / 240.0) < 1e-12                      # MIN: (30 + 22) / 240
    assert f["reb"] == 12.0 and f["ast"] == 7.0 and f["tpm"] == 3.0  # REB, AST, 3PM
    assert f["dfn"] == 5.0                                           # DEF = steals + blocks
    assert f["usg"] == 37.0 and f["pm"] == 2.0                       # USG, PM
    assert (f["tier_star"], f["tier_starter"], f["tier_bench"]) == (20.0, 10.0, 0.0)
    assert f["age_vet"] == 30.0 and f["age_young"] == 22.0           # Sam 32 (>= 32), Ann 21 (<= 23)
    assert f["q_pts"] == 4.0 and abs(f["q_min"] - 12.0 / 240.0) < 1e-12


def test_tier_boundaries():
    for mpg, want in ((28.0, "tier_star"), (27.99, "tier_starter"), (20.0, "tier_starter"), (19.99, "tier_bench"), (10.0, "tier_bench")):
        assert L.tier_of(mpg) == want


def test_rotation_threshold_is_10_mpg_inclusive():
    rows = [{"game_id": 1000 + g, "yr": 2023, "d": dnum(g), "aid": 20, "name": "Ten Flat", "team": "BOS", "played": True, "minutes": 10.0,
             "pts": 6.0, "reb": 0.0, "ast": 0.0, "tpm": 0.0, "dfn": 0.0, "usg": 0.0, "pm": 0.0} for g in range(12)]
    box = toy_box(extra_rows=rows)
    roster = {20}
    f = L.report_family_features(box, "BOS", dnum(11), 2023, roster, {20: 2023}, {"Flat,Ten": "Out"}, AGES, BASE_DAY)
    assert f["n_out_rot"] == 1 and f["pts"] == 6.0


def test_quest_not_in_out_families_and_out_not_in_q():
    f = fam({"Star,Sam": "Questionable"})
    assert f["pts"] == 0.0 and f["q_pts"] == 20.0


def test_window_uses_only_games_before_d():
    # Var Guy's pts = game index: window at day 11 = games 1..10 (last 10 strictly before) -> mean 5.5 ; game 11's own value is ignored
    a = fam({"Guy,Var": "Out"})
    box2 = toy_box(var_last=1000.0)
    b = fam({"Guy,Var": "Out"}, box=box2)
    assert a["pts"] == pytest.approx(5.5) and a == b


# ------------------------------------------------------------------ age
def test_age_computation_and_unknowns():
    assert L.age_on("1990-06-01", "2023-05-31") == 32
    assert L.age_on("1990-06-01", "2023-06-01") == 33
    assert L.age_on("2000-02-29", "2023-02-28") == 22
    book = L.AgeBook({1: "1990-06-01", 2: "2020-01-01", 3: "1950-01-01", 4: None})
    assert book.age(1, "2023-01-12") == 32
    assert book.age(2, "2023-01-12") is None          # age 3: outside 18-45 -> unknown
    assert book.age(3, "2023-01-12") is None          # age 72
    assert book.age(4, "2023-01-12") is None and book.age(99, "2023-01-12") is None
    assert book.n_out_of_range == 2


def test_unknown_age_left_out_of_age_family_only():
    f = fam({"Bench,Ben": "Out", "Star,Sam": "Out"})  # Ben has no DOB
    assert f["n_age_unknown"] == 1 and f["n_age_known"] == 1
    assert f["age_vet"] == 30.0 and f["age_young"] == 0.0         # Ben (12 mpg) is in no AGE bucket...
    assert f["pts"] == 24.0 and f["tier_bench"] == 4.0            # ...but still counts in every other family


# ------------------------------------------------------------------ report cutoffs
def test_cutoff_c60_vs_c20_and_one_second_after_ignored():
    box = toy_box()
    rep = reports_of([("early", -5 * 3600, [("Star,Sam", "Available")]),
                      ("mid", -3600 + 1, [("Star,Sam", "Out")]),             # 1 s after the T-60 cutoff, well before T-20
                      ("late", -1200 + 1, [("Star,Sam", "Out"), ("Mid,Ann", "Out")])])   # 1 s after the T-20 cutoff
    f = feats(box, rep).loc[(1011, "BOS")]
    assert f["pts_own_c60"] == 0.0                                # 'mid' ignored at T-60 (one second late); 'early' lists nobody out
    assert f["pts_own_c20"] == 20.0                               # T-20: 'mid' is the latest <= tip-20min; 'late' is 1 s after -> ignored
    rep2 = reports_of([("mid", -3600, [("Star,Sam", "Out")]), ("late", -1200, [("Star,Sam", "Out"), ("Mid,Ann", "Out")])])
    g = feats(box, rep2).loc[(1011, "BOS")]
    assert g["pts_own_c60"] == 20.0 and g["pts_own_c20"] == 30.0    # reports exactly at the cutoff are inclusive
    assert g["report_found_c60"] and g["report_found_c20"]


def test_opp_columns_are_the_other_teams_own():
    box = toy_box()
    rep = reports_of([("r", -3600, [("Star,Sam", "Out")])])
    F = feats(box, rep)
    assert F.loc[(1011, "NYK"), "pts_opp_c60"] == F.loc[(1011, "BOS"), "pts_own_c60"] == 20.0
    assert F.loc[(1011, "BOS"), "pts_opp_c60"] == 0.0


def test_team_with_no_rows_or_no_report_gets_zeros():
    box = toy_box()
    F = feats(box, reports_of([("r", -3600, [("Star,Sam", "Out")])], g=3))   # report is for another game
    r = F.loc[(1011, "BOS")]
    assert not r["report_found_c60"] and r["pts_own_c60"] == 0.0 and r["q_pts_own_c20"] == 0.0


# ------------------------------------------------------------------ NEW family: no look-ahead
def mv(aid, ft, tt, first_day, season_new=2023, base=BASE_DAY):
    return {"athlete_id": aid, "from_team": ft, "to_team": tt, "first_date_new": (base + pd.Timedelta(days=first_day)).strftime("%Y-%m-%d"),
            "season_new": season_new}


def moves_of(*ms):
    return L.Moves(pd.DataFrame(list(ms)))


def test_new_share_math_and_strict_before():
    box = toy_box()
    mo = moves_of(mv(6, "NYK", "BOS", 5))
    # BOS last 10 games before day 11 = days 1..10.  Mo played BOS days 5..10 = 6 games x 20 min.  Total BOS minutes per game:
    per_game = 30 + 22 + 12 + 9.9 + 20 + 20                      # Sam, Ann, Ben, Lou, Var Guy, Mo (Mo is in BOS from day 5)
    tot = sum((per_game if g >= 5 else per_game - 20) for g in range(1, 11))
    got = L.new_family_features(box, mo, "BOS", dnum(11), 2023)["new_share"]
    assert got == pytest.approx(6 * 20 / tot)
    # the move is dated day 5: visible for games on day 6+, NOT for the game on day 5 itself, nor before
    assert L.new_family_features(box, mo, "BOS", dnum(5), 2023)["new_share"] == 0.0
    assert L.new_family_features(box, mo, "BOS", dnum(6), 2023)["new_share"] > 0.0


def test_moves_dated_on_or_after_D_are_ignored():
    box = toy_box()
    d = dnum(11)
    base = L.new_family_features(box, NO_MOVES, "BOS", d, 2023)
    assert base == {"new_share": 0.0, "gone_share": 0.0}
    for day in (11, 12, 40):                                       # the game's own date and later: invisible
        mo = moves_of(mv(6, "NYK", "BOS", day), mv(5, "BOS", "NYK", day))
        assert L.new_family_features(box, mo, "BOS", d, 2023) == base
    mo = moves_of(mv(6, "NYK", "BOS", 10), mv(5, "BOS", "NYK", 10))        # the day before: visible
    got = L.new_family_features(box, mo, "BOS", d, 2023)
    assert got["new_share"] > 0.0 and got["gone_share"] > 0.0


def test_gone_share_math_and_return_rule():
    box = toy_box()
    mo = moves_of(mv(5, "BOS", "NYK", 3))
    # last season BOS minutes: Sam 30 x3, Gone Guy 20 x3 -> gone = 60/150
    assert L.new_family_features(box, mo, "BOS", dnum(11), 2023)["gone_share"] == pytest.approx(60 / 150)
    back = moves_of(mv(5, "BOS", "NYK", 3), mv(5, "NYK", "BOS", 7))        # he came back before D: no longer 'gone'
    assert L.new_family_features(box, back, "BOS", dnum(11), 2023)["gone_share"] == 0.0
    assert L.new_family_features(box, back, "BOS", dnum(6), 2023)["gone_share"] == pytest.approx(60 / 150)   # ... but gone before day 7
    other = moves_of(mv(5, "BOS", "NYK", 3), mv(5, "NYK", "MIA", 8))      # moved on again: still not on BOS
    assert L.new_family_features(box, other, "BOS", dnum(11), 2023)["gone_share"] == pytest.approx(60 / 150)


def test_new_family_is_cutoff_independent_and_in_both_cutoffs():
    box = toy_box()
    mo = moves_of(mv(6, "NYK", "BOS", 5))
    F = feats(box, reports_of([("r", -3600, [("Star,Sam", "Out")])]), moves=mo).loc[(1011, "BOS")]
    assert F["new_share_own_c60"] == F["new_share_own_c20"] > 0.0


# ------------------------------------------------------------------ versions, ridge, STOP, scoring
def test_version_enumeration_4095_x_2():
    v = L.enumerate_versions()
    assert len(v) == 8190 == len({x[0] for x in v})
    assert sum(1 for x in v if x[1] == "c60") == 4095 == sum(1 for x in v if x[1] == "c20")
    assert all(len(x[2]) >= 1 for x in v) and len({x[2] for x in v if x[1] == "c60"}) == 4095
    assert len(L.FAMILY_NAMES) == 12
    assert len(L.all_feature_columns()) == 2 * 34


def rng_data(n=300, k=6, seed=1):
    r = np.random.default_rng(seed)
    X = r.normal(size=(n, k)) * np.arange(1, k + 1)
    s = np.where(np.arange(n) < n // 2, "2021-22", "2022-23")
    y = X[:, 0] * 0.7 - 0.2 * X[:, 3] + r.normal(size=n)
    return X, y, s


def test_ridge_closed_form():
    X, y, _ = rng_data()
    mu, sd = L.standardise_fit(X)
    Xs = (X - mu) / sd
    lam = 7.0
    b0, b = L.ridge_fit(Xs, y, lam)
    A = Xs.T @ Xs + lam * np.eye(X.shape[1])
    assert np.allclose(A @ b, Xs.T @ (y - y.mean())) and b0 == pytest.approx(y.mean())
    # penalised optimum: gradient zero
    grad = -2 * Xs.T @ (y - b0 - Xs @ b) + 2 * lam * b
    assert np.abs(grad).max() < 1e-8


def test_subset_engine_equals_direct_fit():
    X, y, s = rng_data()
    r = np.random.default_rng(5)
    Xv = r.normal(size=(40, X.shape[1])) * np.arange(1, X.shape[1] + 1)
    eng = L.SubsetEngine(X, y, s, Xv)
    for idx in ([0], [1, 4], [0, 2, 3, 5], list(range(6))):
        lam, mse, b0, b, pv = eng.fit(idx)
        Xi = X[:, idx]
        lam_ref, mse_ref = L.cv_lambda(Xi, y, s)
        mu, sd = L.standardise_fit(Xi)
        b0r, br = L.ridge_fit((Xi - mu) / sd, y, lam_ref)
        ref = L.ridge_predict(Xv[:, idx], mu, sd, b0r, br)
        assert lam == lam_ref
        assert np.allclose(pv, ref, atol=1e-8) and np.allclose([mse[j] for j in range(5)], [mse_ref[l_] for l_ in L.LAMBDAS], rtol=1e-9)


def synth_frames(signal: bool, seed=3, n_games=60):
    r = np.random.default_rng(seed)
    cols = L.all_feature_columns()
    frames = []
    gid = 1
    for season in (list(L.TRAIN) + list(L.VALID)):
        for _ in range(n_games):
            for home in (0, 1):
                row = {"game_id": gid, "team": f"T{home}", "season": season, "home": home}
                for c in cols:
                    row[c] = r.normal()
                e1 = 110.0 + r.normal()
                row["e1"] = e1
                res = 3.0 * row["pts_own_c60"] if signal else 0.0
                row["pts"] = e1 + res
                frames.append(row)
            gid += 1
    D = pd.DataFrame(frames)
    D["r"] = D.pts - D.e1
    return D[D.season.isin(L.TRAIN)], D[D.season.isin(L.VALID)]


SMALL = [v for v in L.enumerate_versions() if v[2] in (("PTS",), ("MIN",), ("PTS", "MIN"), ("TIER", "AGE"))]


def test_stop_rule_when_e1_is_best_or_tied():
    tr, va = synth_frames(signal=False)
    rec, frozen, tab = RS.select(tr, va, versions=SMALL, log=lambda *_: None)
    assert rec["stop"] and rec["chosen"] == "E1" and frozen is None          # residual 0: every version ties E1 -> E1 wins


def test_selection_picks_signal_and_freezes():
    tr, va = synth_frames(signal=True)
    rec, frozen, tab = RS.select(tr, va, versions=SMALL, log=lambda *_: None)
    assert not rec["stop"] and "PTS" in frozen["families"] and frozen["cutoff"] == "c60"
    assert rec["E1"]["total_mae"] > rec["best_by_total"]["total_mae"]
    assert len(rec["top20"]) <= 20 and sum(rec["family_frequency_top100"].values()) > 0
    # frozen model reproduces the validation score
    pred = va.e1.values + L.predict_variant(frozen["ridge"], va)
    u, inv = np.unique(va.game_id.values, return_inverse=True)
    s = L.score_totals(inv, len(u), va.pts.values, pred)
    assert s["total_mae"] == pytest.approx(rec["best_by_total"]["total_mae"], rel=1e-9)


def test_selection_never_sees_test_seasons():
    tr, va = synth_frames(signal=True)
    bad = va.copy()
    bad["season"] = "2024-25"
    with pytest.raises(AssertionError):
        RS.select(tr, bad, versions=SMALL, log=lambda *_: None)


def test_drop_nan_games_whole_games():
    tr, va = synth_frames(signal=False)
    va = va.copy()
    va.loc[va.index[0], "e1"] = np.nan
    out, n = RS.drop_nan_games(va)
    assert n == 1 and len(out) == len(va) - 2


def test_score_totals():
    inv = np.array([0, 0, 1, 1])
    y = np.array([100.0, 110.0, 120.0, 100.0])
    p = np.array([105.0, 105.0, 100.0, 100.0])
    s = L.score_totals(inv, 2, y, p)
    assert s["total_mae"] == pytest.approx((0.0 + 20.0) / 2) and s["team_mae"] == pytest.approx((5 + 5 + 20 + 0) / 4)
    assert s["bias"] == pytest.approx(-10.0) and s["pct_within_10"] == 0.5


# ------------------------------------------------------------------ test-stage maths, gates and hashes
def toy_test_frame(better: bool, n=400, seed=2):
    r = np.random.default_rng(seed)
    rows = []
    for g in range(n):
        for h in (0, 1):
            y = 110 + r.normal(scale=10)
            e1 = y + r.normal(scale=8)
            e3 = y + r.normal(scale=4) if better else e1
            rows.append({"game_id": g, "home": h, "pts": y, "e1": e1, "e3": e3, "season": "2024-25" if g < n // 2 else "2025-26"})
    return pd.DataFrame(rows)


def test_paired_bootstrap_claims():
    D = toy_test_frame(better=True)
    out = RT.paired_blocks(D, D.e1.values, D.e3.values)
    assert out["total_mae"]["ci_upper_below_zero"] and out["team_mae"]["ci_upper_below_zero"]
    assert set(out["total_mae"]["by_season"]) == {"2024-25", "2025-26"}
    S = toy_test_frame(better=False)
    same = RT.paired_blocks(S, S.e1.values, S.e3.values)
    assert same["total_mae"]["pooled"]["diff"] == 0.0 and not same["total_mae"]["ci_upper_below_zero"]
    d_total, d_team, _, m1, m2 = RT.per_game_diffs(D, D.e1.values, D.e3.values)
    assert d_total.mean() == pytest.approx(m2["total_mae"] - m1["total_mae"]) and d_team.mean() == pytest.approx(m2["team_mae"] - m1["team_mae"])


def test_bootstrap_is_deterministic_and_uses_the_preregistered_seed():
    d = np.random.default_rng(0).normal(size=200)
    assert L.paired_boot_ci(d) == L.paired_boot_ci(d)
    assert (L.BOOT_N, L.BOOT_SEED) == (10000, 20261023)


def make_study(tmp_path, agreement=True):
    study = tmp_path / "study"
    (study / "main").mkdir(parents=True)
    feat = study / "e3_features.parquet"
    feat.write_bytes(b"features")
    (study / "AGREEMENT.md").write_text(f"STATUS: {'PASS' if agreement else 'FAIL'}\nFEATURES_SHA256: {L.sha256_file(feat)}\n")
    blob = json.dumps({"model": "x", "ridge": {}}).encode()
    (study / "frozen_e3.json").write_bytes(blob)
    (study / "frozen_e3.sha256").write_text(L.sha256_bytes(blob) + "  frozen_e3.json\n")
    base = study / "main"
    (base / "a.py").write_text("print(1)\n")
    return study, base, ("a.py",)


def test_gates_refuse_then_pass(tmp_path):
    study, base, files = make_study(tmp_path)
    with pytest.raises(SystemExit, match="AUDIT_OK not found"):
        RT.check_gates(study, base, files)
    (study / "AUDIT_OK").write_text(json.dumps({"sha256": L.audit_hashes(base, files)["sha256"]}))
    assert RT.check_gates(study, base, files)["model"] == "x"
    (base / "a.py").write_text("print(2)\n")                               # code changed after the audit
    with pytest.raises(SystemExit, match="hash mismatch"):
        RT.check_gates(study, base, files)
    (base / "a.py").write_text("print(1)\n")
    (study / "test_started.json").write_text("{}")
    with pytest.raises(SystemExit, match="test_started.json exists"):
        RT.check_gates(study, base, files)
    (study / "test_started.json").unlink()
    (study / "results_test.json").write_text("{}")
    with pytest.raises(SystemExit, match="results_test.json exists"):
        RT.check_gates(study, base, files)


def test_gate_agreement_and_frozen_hash(tmp_path):
    study, base, files = make_study(tmp_path, agreement=False)
    (study / "AUDIT_OK").write_text(json.dumps({"sha256": L.audit_hashes(base, files)["sha256"]}))
    with pytest.raises(SystemExit, match="PASS"):
        RT.check_gates(study, base, files)
    study2, base2, files2 = make_study(tmp_path / "b")
    (study2 / "frozen_e3.json").write_text("{}")                          # tampered after freezing
    with pytest.raises(SystemExit, match="hash"):
        RT.check_gates(study2, base2, files2)
    study3, base3, files3 = make_study(tmp_path / "c")
    (study3 / "AUDIT_OK").write_text(json.dumps({"sha256": {}}))
    with pytest.raises(SystemExit, match="does not cover"):
        RT.check_gates(study3, base3, files3)
    (study3 / "e3_features.parquet").write_bytes(b"changed")              # features changed after AGREEMENT
    with pytest.raises(SystemExit):
        L.check_agreement_pass(study3)


def test_selection_gate_refuses_without_pass_and_on_rerun(tmp_path):
    study, base, files = make_study(tmp_path, agreement=False)
    with pytest.raises(SystemExit, match="PASS"):
        RS.gate(study)
    study2, _, _ = make_study(tmp_path / "d")
    (study2 / "frozen_e3.json").unlink()
    (study2 / "frozen_e3.sha256").unlink()
    RS.gate(study2)
    (study2 / "results_select.json").write_text("{}")
    with pytest.raises(SystemExit, match="runs once"):
        RS.gate(study2)


def test_audit_file_list_complete_and_real_study_not_audited():
    assert len(set(L.AUDIT_FILES)) == len(L.AUDIT_FILES)
    for f in L.AUDIT_FILES:
        if f in ("../frozen_e3.json", "../AGREEMENT.md", "../e3_features.parquet", "../sample_ids.csv", "../FEATURES_E3.md"):
            continue
        assert (HERE / f).exists(), f
    assert "../../../../data/derived/player_moves.csv" in L.AUDIT_FILES and "../../nba_score_model_e2/main/e2_lib.py" in L.AUDIT_FILES
    assert any("player_core_2026" in f for f in L.AUDIT_FILES) and "../../nba_score_model/frozen.json" in L.AUDIT_FILES


# ------------------------------------------------------------------ sampling
def test_sampling_deterministic_sorted_and_seeded():
    F = pd.DataFrame({"game_id": np.arange(5000), "team": "A", "team_k": "A", "season": "2021-22",
                      "date": pd.Timestamp("2021-10-20") + pd.to_timedelta(np.arange(5000) // 10, unit="D")})
    a, b = L.sample_ids(F), L.sample_ids(F.sample(frac=1, random_state=0))
    assert len(a) == 200 and a.equals(b) and a.game_id.is_monotonic_increasing and a.game_id.is_unique
    want = np.sort(np.random.default_rng(20261025).choice(5000, 200, replace=False))
    assert (a.game_id.values == want).all()
    assert L.SEED_SAMPLE == 20261025


# ------------------------------------------------------------------ compare.py
def test_compare_frames_pass_and_fail():
    cols = C.GATING
    base = pd.DataFrame({"game_id": [1, 2], "team": ["A", "B"], **{c: [1.0, 2.0] for c in cols}})
    sample = base[["game_id", "team"]]
    assert C.compare_frames(base, base.copy(), sample)["pass"]
    bad = base.copy()
    bad[cols[3]] = [1.0, 2.0000001]
    r = C.compare_frames(base, bad, sample)
    assert not r["pass"] and r["mismatching_columns"] == [cols[3]] and r["columns"][cols[3]]["examples"]
    r = C.compare_frames(base, base.drop(columns=[cols[0]]), sample)
    assert not r["pass"] and cols[0] in r["mismatching_columns"]
    ok = base.copy()
    ok[cols[1]] = [1.0 + 1e-12, 2.0]
    assert C.compare_frames(base, ok, sample)["pass"]


# ------------------------------------------------------------------ slow: real files
@pytest.mark.slow
def test_real_features_shape_and_sample():
    F = pd.read_parquet(L.FEATURES_PARQUET)
    assert len(F) == 12306 and not F.duplicated(["game_id", "team"]).any()
    assert F[L.all_feature_columns()].notna().all().all()
    s = pd.read_csv(L.STUDY / "sample_ids.csv")
    assert len(s) == 200
    assert (L.sample_ids(F).game_id.values == s.game_id.values).all()


@pytest.mark.slow
def test_real_dob_coverage_and_sanity():
    book, info = L.load_agebook()
    assert info["n_dob"] > 1000
    on = pd.Timestamp("2025-01-01")
    ages = np.array([L.age_on(d, on) for d in book.dob.values()])
    # every current-era player is 18-45 at that date unless he retired long ago (those are never queried)
    box = L.load_box3(range(2025, 2026))
    played = [a for a in box.hist3 if a in book.dob]
    a2 = np.array([L.age_on(book.dob[a], on) for a in played])
    assert len(played) == len(box.hist3) and a2.min() >= 18 and a2.max() <= 45
    assert len(ages) == info["n_dob"]


@pytest.mark.slow
def test_real_box3_window_equals_e2_stats():
    box = L.load_box3(range(2024, 2027))
    rng = np.random.default_rng(0)
    aids = rng.choice(list(box.hist3), 40, replace=False)
    for a in aids:
        for d in (E2.day_int("2025-12-01"), E2.day_int("2026-03-01")):
            w3, w2 = box.window3(int(a), d, 2026), box.stats(int(a), d, 2026)
            assert (w3 is None) == (w2 is None)
            if w3 is not None:
                assert abs(w3[0][0] - w2[0]) < 1e-12 and abs(w3[0][1] - w2[1]) < 1e-12
