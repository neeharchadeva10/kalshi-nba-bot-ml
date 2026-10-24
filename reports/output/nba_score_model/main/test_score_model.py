"""Unit tests for research #59 (NBA score model).  Run:
  .venv/Scripts/python.exe -m pytest reports/output/nba_score_model/main/test_score_model.py -q
Toy data except the tests marked slow (real hoopR box scores of discovery seasons only: 2010-11, plus the written feature parquet)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sm_lib as S  # noqa: E402
import models as M  # noqa: E402
import compare as C  # noqa: E402
import run_select as rs  # noqa: E402
import run_test as rt  # noqa: E402


# ------------------------------------------------------------------ toy builders
def make_tg(games, season="2009-10"):
    """games: list of (game_id, date 'YYYY-MM-DD', home, away, home_pts, away_pts, pace).  Two rows per game, in the schema of
    sm_lib.load_all_team_games."""
    rows = []
    for gid, d, h, a, hp, ap, pace in games:
        for team, opp, venue, p, op in ((h, a, "home", hp, ap), (a, h, "away", ap, hp)):
            rows.append({"game_id": gid, "season": season, "date": pd.Timestamp(d), "tip": int(pd.Timestamp(d).value // 10 ** 9) + 3600 * 20,
                         "team": team, "opp": opp, "venue": venue, "pts": float(p), "opp_pts": float(op), "pace": float(pace)})
    tg = pd.DataFrame(rows)
    tg["fr"], tg["fr_opp"] = tg.team, tg.opp
    return tg


def prev_season_games(teams=("AAA", "BBB", "CCC")):
    g, i = [], 100
    for k in range(6):
        t = list(teams)
        a, b = t[k % 3], t[(k + 1) % 3]
        g.append((i, f"2008-11-{10 + k:02d}", a, b, 100 + k, 98 + k, 90.0 + k))
        i += 1
    return g


def toy_world():
    prev = make_tg(prev_season_games(), "2008-09")
    cur_games = [(1, "2009-11-01", "AAA", "BBB", 100, 90, 96.0),
                 (2, "2009-11-03", "BBB", "CCC", 110, 105, 100.0),
                 (3, "2009-11-04", "AAA", "CCC", 95, 99, 94.0),
                 (4, "2009-11-04", "BBB", "DDD", 101, 97, 98.0),    # DDD has no S-1 games -> NaN prev
                 (5, "2009-11-08", "AAA", "BBB", 120, 100, 102.0)]
    cur = make_tg(cur_games, "2009-10")
    return prev, cur


# ------------------------------------------------------------------ possessions / turnover guard
def test_possessions_formula():
    # 0.96 * (FGA - OREB + TOV + 0.44 FTA)
    assert S.L.possessions(80, 10, 15, 25) == pytest.approx(0.96 * (80 - 10 + 15 + 0.44 * 25))


def test_turnover_guard_rules():
    tot = np.array([20.0, np.nan, 5.0, 20.0])
    tur = np.array([18.0, 12.0, 9.0, 20.0])
    assert S.L.fix_tov(tot, tur).tolist() == [20.0, 12.0, 9.0, 20.0]          # NaN -> turnovers; total < turnovers -> turnovers
    tb = pd.DataFrame({"season_type": 2, "total_turnovers": [30.0, 40.0], "turnovers": [15.0, 20.0]})   # doubled (ratio 2.0)
    assert S.L.tov_for_box(tb).tolist() == [15.0, 20.0]
    tb2 = pd.DataFrame({"season_type": 2, "total_turnovers": [16.0, 21.0], "turnovers": [15.0, 20.0]})
    assert S.L.tov_for_box(tb2).tolist() == [16.0, 21.0]
    tb3 = pd.DataFrame({"season_type": 2, "total_turnovers": [np.nan, np.nan], "turnovers": [15.0, 20.0]})
    assert S.L.tov_for_box(tb3).tolist() == [15.0, 20.0]                      # 2017-18: whole file NaN


# ------------------------------------------------------------------ features: values, windows, fallbacks
def feat_row(F, gid, team):
    r = F[(F.game_id == gid) & (F.team == team)]
    assert len(r) == 1
    return r.iloc[0]


def test_feature_values_and_windows(monkeypatch):
    prev, cur = toy_world()
    F = S.season_features(cur, prev)
    assert list(F.columns) == S.FEATURE_COLUMNS and len(F) == 10
    # game 5 (11-08): AAA home vs BBB.  AAA before: g1 (100-90, pace 96), g3 (95-99, pace 94); BBB before: g1 (90-100), g2 (110-105, 100), g4 (101-97, 98)
    r = feat_row(F, 5, "AAA")
    assert r.ortg_x_season == pytest.approx(100 * (100 + 95) / (96 + 94))
    assert r.ortg_x_l5 == pytest.approx(r.ortg_x_season) and r.ortg_x_l10 == pytest.approx(r.ortg_x_season)   # fewer than 5 games: all of them
    assert r.drtg_y_season == pytest.approx(100 * (100 + 105 + 97) / (96 + 100 + 98))                          # BBB allowed 100, 105, 97
    assert r.pace_x_season == pytest.approx((96 + 94) / 2) and r.pace_y_season == pytest.approx((96 + 100 + 98) / 3)
    assert r.gp_x == 2 and r.gp_y == 3 and r.home == 1.0 and r.pts == 120.0
    assert r.dos == 7.0                                                         # 11-01 -> 11-08
    # prev: AAA home games in toy prev -> compute directly
    pa = prev[prev.team == "AAA"]
    assert r.ortg_x_prev == pytest.approx(100 * pa.pts.sum() / pa.pace.sum())
    assert r.pace_x_prev == pytest.approx(pa.pace.mean())
    # last-2 window check via a 1-game 'l5' vs season difference: BBB's last 10 = all 3 games
    assert r.drtg_y_l10 == pytest.approx(r.drtg_y_season)


def test_last_five_and_ten_windows():
    prev = make_tg(prev_season_games(), "2008-09")
    games = []
    for k in range(12):                           # AAA plays BBB every other day, points = 100 + k (AAA), 90 (BBB)
        games.append((10 + k, pd.Timestamp("2009-11-01").__add__(pd.Timedelta(days=2 * k)).strftime("%Y-%m-%d"), "AAA", "BBB", 100 + k, 90, 100.0))
    cur = make_tg(games, "2009-10")
    F = S.season_features(cur, prev)
    r = feat_row(F, 10 + 11, "AAA")               # 12th game: 11 before
    pts = np.array([100 + k for k in range(11)], float)
    assert r.ortg_x_season == pytest.approx(100 * pts.sum() / (100.0 * 11))
    assert r.ortg_x_l10 == pytest.approx(100 * pts[-10:].sum() / (100.0 * 10))
    assert r.ortg_x_l5 == pytest.approx(100 * pts[-5:].sum() / (100.0 * 5))
    assert r.gp_x == 11


def test_missing_window_uses_prev_and_nan_when_no_prev():
    prev, cur = toy_world()
    F = S.season_features(cur, prev)
    r = feat_row(F, 1, "AAA")                     # first game of the season: no window -> S-1 value
    assert r.gp_x == 0 and r.gp_y == 0 and r.rest_x == 4 and r.rest_y == 4 and r.b2b_x == 0.0 and r.dos == 0.0
    for c in ("season", "l10", "l5"):
        assert r[f"ortg_x_{c}"] == pytest.approx(r.ortg_x_prev) and r[f"drtg_y_{c}"] == pytest.approx(r.drtg_y_prev)
    assert r.pace_x_season == pytest.approx(r.pace_x_prev) and r.pace_y_l10 == pytest.approx(r.pace_y_prev)
    d = feat_row(F, 4, "BBB")                     # opponent DDD has no S-1 and no games yet: NaN (documented)
    assert np.isnan(d.drtg_y_prev) and np.isnan(d.drtg_y_season)


def test_league_fallback_then_season_to_date(monkeypatch):
    prev, cur = toy_world()
    F = S.season_features(cur, prev)
    pl = prev
    lg_o = 100 * pl.pts.sum() / pl.pace.sum()
    lg_p = pl.pace.mean()
    r = feat_row(F, 5, "AAA")                     # 4 games played < 50 -> S-1 full-season league values
    assert r.lg_ortg == pytest.approx(lg_o) and r.lg_pace == pytest.approx(lg_p)
    monkeypatch.setattr(S, "MIN_LG_GAMES", 3)
    F2 = S.season_features(cur, prev)
    r2 = feat_row(F2, 5, "AAA")                   # 4 games before -> season to date: rows of games 1-4
    done = cur[cur.game_id.isin([1, 2, 3, 4])]
    assert r2.lg_ortg == pytest.approx(100 * done.pts.sum() / done.pace.sum())
    assert r2.lg_pace == pytest.approx(done.pace.mean())
    r3 = feat_row(F2, 4, "BBB")                   # exactly 3 games strictly before 11-04 (game 3 is the same date -> not before): 2 games -> fallback
    assert r3.lg_ortg == pytest.approx(lg_o)


def test_rest_and_back_to_back():
    prev, cur = toy_world()
    F = S.season_features(cur, prev)
    # BBB: g1 11-01, g2 11-03 (gap 2 -> rest 1), g4 11-04 (gap 1 -> rest 0, b2b), g5 11-08 (gap 4 -> rest 3)
    assert feat_row(F, 2, "BBB").rest_x == 1 and feat_row(F, 2, "BBB").b2b_x == 0.0
    r4 = feat_row(F, 4, "BBB")
    assert r4.rest_x == 0 and r4.b2b_x == 1.0 and r4.rest_y == 4 and r4.b2b_y == 0.0       # DDD first game -> 4
    assert feat_row(F, 5, "BBB").rest_x == 3
    # cap at 4: a 12-day gap
    g = make_tg([(1, "2009-11-01", "AAA", "BBB", 100, 90, 96.0), (2, "2009-11-13", "AAA", "BBB", 100, 90, 96.0)], "2009-10")
    F2 = S.season_features(g, make_tg(prev_season_games(), "2008-09"))
    assert feat_row(F2, 2, "AAA").rest_x == 4 and feat_row(F2, 2, "AAA").b2b_x == 0.0


def test_no_look_ahead_changing_games_on_or_after_D():
    prev, cur = toy_world()
    base = S.season_features(cur, prev)
    target = (5, "AAA")                           # game 5, 11-08
    bf = feat_row(base, *target)
    cols = S.FEATS
    # change game 5 itself (same date D) and a game after D: no feature of game 5 moves
    later = make_tg([(6, "2009-11-12", "AAA", "CCC", 130, 80, 110.0)], "2009-10")
    for mod in range(3):
        c2 = cur.copy()
        if mod == 0:
            c2.loc[c2.game_id == 5, ["pts", "opp_pts", "pace"]] = [[999.0, 1.0, 150.0]] * int((c2.game_id == 5).sum())
        elif mod == 1:
            c2 = pd.concat([c2, later], ignore_index=True)
        else:                                      # same-date game (game 3 is 11-04; make a second 11-08 game)
            c2 = pd.concat([c2, make_tg([(7, "2009-11-08", "CCC", "DDD", 140, 60, 120.0)], "2009-10")], ignore_index=True)
        f2 = feat_row(S.season_features(c2, prev), *target)
        for c in cols:
            assert f2[c] == pytest.approx(bf[c], nan_ok=True), (mod, c)
    # while changing an EARLIER game does move them
    c3 = cur.copy()
    c3.loc[c3.game_id == 3, "pts"] = 150.0
    f3 = feat_row(S.season_features(c3, prev), *target)
    assert f3.ortg_x_season != pytest.approx(bf.ortg_x_season)
    # and the previous season: changing S-1 only moves *_prev (and the fallback league values), never later windows
    p2 = prev.copy()
    p2.loc[p2.team == "AAA", "pts"] += 10.0
    f4 = feat_row(S.season_features(cur, p2), *target)
    assert f4.ortg_x_season == pytest.approx(bf.ortg_x_season) and f4.ortg_x_prev != pytest.approx(bf.ortg_x_prev)


def test_target_never_used_for_same_date_features():
    prev, cur = toy_world()
    f = S.season_features(cur, prev)
    r = feat_row(f, 3, "AAA")                     # game 3 on 11-04; game 4 also 11-04 must not count for BBB/CCC either
    assert feat_row(f, 4, "BBB").gp_x == 2        # g1, g2 only
    assert r.gp_y == 1                             # CCC: g2 only


# ------------------------------------------------------------------ sampling
def test_sample_ids_determinism_and_rule():
    rng = np.random.default_rng(1)
    rows = []
    for i, s in enumerate(S.SAMPLE_SEASONS + (S.FORWARD,)):
        for j in range(60):
            rows.append((1000 * i + j, s, pd.Timestamp("2010-01-01") + pd.Timedelta(days=j), "AAA" if j % 2 else "BBB"))
    F = pd.DataFrame(rows, columns=["game_id", "season", "date", "team"])
    a = S.sample_ids(F)
    b = S.sample_ids(F.sample(frac=1.0, random_state=3))           # row order of the input does not matter
    assert a.equals(b) and len(a) == 300 and not a.duplicated(["game_id", "team"]).any()
    assert S.FORWARD not in set(a.season)
    P = F[F.season.isin(set(S.SAMPLE_SEASONS))].sort_values(["season", "date", "game_id", "team"], kind="stable").reset_index(drop=True)
    idx = np.sort(np.random.default_rng(20261023).choice(len(P), 300, replace=False))
    assert a.game_id.tolist() == P.game_id.values[idx].tolist()


# ------------------------------------------------------------------ ridge
def test_ridge_equals_closed_form_with_unpenalised_intercept():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(200, 4)) * [1, 3, 0.5, 2] + [5, -2, 0, 1]
    y = 3 + X @ np.array([1.0, -0.5, 2.0, 0.0]) + rng.normal(size=200)
    lam = 7.0
    mu, sd = M.standardise_fit(X)
    b0, b = M.ridge_fit((X - mu) / sd, y, lam)
    Z = np.column_stack([np.ones(200), (X - mu) / sd])
    P = np.diag([0.0, lam, lam, lam, lam])
    beta = np.linalg.solve(Z.T @ Z + P, Z.T @ y)                  # intercept unpenalised
    assert b0 == pytest.approx(beta[0]) and np.allclose(b, beta[1:])
    assert np.allclose(M.ridge_predict(X, mu, sd, b0, b), Z @ beta)
    b0o, bo = M.ridge_fit((X - mu) / sd, y, 1e-9)                   # lambda -> 0: OLS
    ols, *_ = np.linalg.lstsq(Z, y, rcond=None)
    assert np.allclose(np.r_[b0o, bo], ols, atol=1e-6)


def test_loso_folds_and_cv():
    seasons = np.array(["a"] * 5 + ["b"] * 4 + ["c"] * 6)
    folds = M.loso_folds(seasons)
    assert [f[0] for f in folds] == ["a", "b", "c"]
    cover = sum(f[1].astype(int) for f in folds)
    assert (cover == 1).all()                                      # every row is held out exactly once
    rng = np.random.default_rng(1)
    X = rng.normal(size=(len(seasons), 2))
    y = X @ [1.0, 2.0] + rng.normal(size=len(seasons)) * 0.1
    # a fold's prediction must not depend on its own outcomes: change y of season c, predictions of season c unchanged
    p1 = M.loso_predict(X, y, seasons, 1.0)
    y2 = y.copy()
    y2[seasons == "c"] += 100.0
    p2 = M.loso_predict(X, y2, seasons, 1.0)
    assert np.allclose(p1[seasons == "c"], p2[seasons == "c"]) and not np.allclose(p1[seasons == "a"], p2[seasons == "a"])
    lam, mse = M.cv_lambda(X, y, seasons, lambdas=(0.01, 1.0, 1e6))
    assert lam in (0.01, 1.0) and mse[1e6] > mse[0.01]


def test_r2_adds_league_expectation_back():
    rng = np.random.default_rng(2)
    n = 120
    F = pd.DataFrame(rng.normal(size=(n, len(S.FEATS))), columns=S.FEATS)
    F["lg_ortg"] = 108 + rng.normal(size=n)
    F["lg_pace"] = 92 + rng.normal(size=n)
    F["season"] = np.repeat(["s1", "s2", "s3", "s4"], n // 4)
    F["pts"] = F.lg_ortg * F.lg_pace / 100 + rng.normal(size=n)
    m1, oof1 = M.fit_ridge_model(F, "R1")
    m2, oof2 = M.fit_ridge_model(F, "R2")
    assert m2["kind"] == "R2" and len(oof2) == n
    p = M.ridge_model_predict(m2, F)
    X = F[S.FEATS].values
    manual = m2["b0"] + ((X - np.array(m2["mu"])) / np.array(m2["sd"])) @ np.array(m2["coef"]) + F.lg_ortg.values * F.lg_pace.values / 100
    assert np.allclose(p, manual)
    assert np.mean(np.abs(F.pts - p)) < 2.0 and set(m1["train_seasons"]) == {"s1", "s2", "s3", "s4"}


def test_stacking_recovers_weights():
    rng = np.random.default_rng(3)
    g, r1, r2 = rng.normal(100, 5, 500), rng.normal(100, 5, 500), rng.normal(100, 5, 500)
    y = 2.0 + 0.3 * g + 0.2 * r1 + 0.5 * r2
    w = M.fit_stack(g, r1, r2, y)
    assert (w["w0"], w["wG"], w["w1"], w["w2"]) == pytest.approx((2.0, 0.3, 0.2, 0.5), abs=1e-8)
    assert np.allclose(M.stack_predict(w, g, r1, r2), y)


# ------------------------------------------------------------------ metrics, selection helpers
def test_game_metrics_toy():
    gid = np.array([1, 1, 2, 2])
    home = np.array([1, 0, 1, 0])
    y = np.array([100.0, 90.0, 110.0, 120.0])           # totals 190, 230; margins +10, -10
    pred = np.array([105.0, 88.0, 100.0, 105.0])        # totals 193, 205; margins +17, -5
    m = M.game_metrics(gid, home, y, pred)
    assert m["total_mae"] == pytest.approx((3 + 25) / 2) and m["pct_within_10"] == 0.5 and m["bias"] == pytest.approx((3 - 25) / 2)
    assert m["team_mae"] == pytest.approx((5 + 2 + 10 + 15) / 4) and m["margin_mae"] == pytest.approx((7 + 5) / 2)
    assert m["winner_pct"] == 1.0 and m["total_rmse"] == pytest.approx(np.sqrt((9 + 625) / 2))
    with pytest.raises(AssertionError):
        M.game_metrics(np.array([1, 1, 1]), np.array([1, 0, 1]), np.ones(3), np.ones(3))


def test_within_ten_is_inclusive_and_formula_sums():
    P = np.array([[110.0, 100.0], [111.0, 100.0]])
    s = M.formula_sums(P, np.array([100.0, 100.0]))
    assert s["w10"].tolist() == [2, 1] and s["sabs"].tolist() == [10.0, 11.0] and s["se"].tolist() == [10.0, 11.0]
    pooled = M.pool([s, s])
    assert pooled["mae"].tolist() == [5.0, 5.5] and pooled["n"] == 4
    assert rs.rank_flats({"mae": np.array([2.0, 1.0, 1.0])}).tolist() == [1, 2, 0]      # ties -> smaller index


def test_choose_lowest_validation_total_mae_with_tie_order():
    t = {k: {"total_mae": v} for k, v in zip(M.CANDIDATE_ORDER, [14.0, 13.9, 13.8, 13.8, 14.1, 13.8, 13.9])}
    assert rs.choose(t) == "G"                          # G, R1, E1 tie at 13.8 -> earliest in the candidate order


# ------------------------------------------------------------------ split filters / no outcome leakage into fitting
def toy_parquet(tmp_path):
    rows = []
    for i, s in enumerate(S.TRAIN + S.VALID + S.TEST + (S.FORWARD,)):
        for j in range(4):
            rows.append({"game_id": 10 * i + j // 2, "season": s, "date": pd.Timestamp("2010-01-01") + pd.Timedelta(days=i),
                         "team": f"T{j}", "opp": f"T{j ^ 1}", **{f: float(i) for f in S.FEATS}, "pts": 100.0 + i})
    p = tmp_path / "f.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    return p


def test_load_stage_reads_only_requested_seasons_and_target_last(tmp_path):
    p = toy_parquet(tmp_path)
    rs.READ_LOG.clear()
    tr = rs.load_stage(S.TRAIN, p)
    va = rs.load_stage(S.VALID, p)
    assert set(tr.season) == set(S.TRAIN) and set(va.season) == set(S.VALID)
    seasons_read = {s for _, ss in rs.READ_LOG for s in ss}
    assert seasons_read == set(S.TRAIN) | set(S.VALID)
    assert not seasons_read & (set(S.TEST) | {S.FORWARD})
    cols_by_call = [c for c, _ in rs.READ_LOG]
    assert S.TARGET not in cols_by_call[0] and S.TARGET in cols_by_call[1]                  # features first, target afterwards
    assert not set(S.TRAIN) & set(S.VALID) and not set(S.VALID) & set(S.TEST) and max(S.TRAIN) < min(S.VALID) < min(S.TEST)


def test_select_source_never_requests_test_seasons():
    src = (HERE / "run_select.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith(("#", '"""')))
    assert "S.TEST" in code and "S.FORWARD" in code                      # only inside the no-overlap assertion
    for line in code.splitlines():
        if "load_stage(" in line and "def load_stage" not in line:
            assert "S.TEST" not in line and "S.FORWARD" not in line


# ------------------------------------------------------------------ gates and hashes
def make_study(tmp_path, with_frozen=True):
    d = tmp_path / "study"
    d.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    files = ("a.py", "b.py")
    for f in files:
        (base / f).write_text(f"# {f}\n")
    if with_frozen:
        blob = json.dumps({"model": "B1"}).encode()
        (d / "frozen.json").write_bytes(blob)
        (d / "frozen.sha256").write_text(S.sha256_bytes(blob) + "  frozen.json\n")
    (d / "team_games_features.parquet").write_bytes(b"features v1")
    (d / "AGREEMENT.md").write_text("STATUS: PASS\nFEATURES_SHA256: " + S.sha256_bytes(b"features v1") + "\n")
    return d, base, files


def test_run_test_gates(tmp_path):
    d, base, files = make_study(tmp_path, with_frozen=False)
    with pytest.raises(SystemExit, match="frozen.json not found"):
        rt.check_gates(d, base, files)
    (tmp_path / "x").mkdir()
    d, base, files = make_study(tmp_path / "x", with_frozen=True)
    with pytest.raises(SystemExit, match="AUDIT_OK not found"):
        rt.check_gates(d, base, files)
    (d / "AUDIT_OK").write_text("not json")
    with pytest.raises(SystemExit, match="not JSON"):
        rt.check_gates(d, base, files)
    good = {"sha256": {f: S.sha256_file(base / f) for f in files}}
    (d / "AUDIT_OK").write_text(json.dumps({"sha256": {"a.py": good["sha256"]["a.py"]}}))
    with pytest.raises(SystemExit, match="does not cover"):
        rt.check_gates(d, base, files)
    bad = {"sha256": dict(good["sha256"], **{"b.py": "0" * 64})}
    (d / "AUDIT_OK").write_text(json.dumps(bad))
    with pytest.raises(SystemExit, match="hash mismatch"):
        rt.check_gates(d, base, files)
    (d / "AUDIT_OK").write_text(json.dumps(good))
    assert rt.check_gates(d, base, files) == {"model": "B1"}
    (d / "team_games_features.parquet").write_bytes(b"features v2")                    # features rebuilt after the agreement
    with pytest.raises(SystemExit, match="changed since AGREEMENT"):
        rt.check_gates(d, base, files)
    (d / "team_games_features.parquet").write_bytes(b"features v1")
    (d / "AGREEMENT.md").write_text("STATUS: FAIL\n")
    with pytest.raises(SystemExit, match="does not say STATUS: PASS"):
        rt.check_gates(d, base, files)
    (d / "AGREEMENT.md").write_text("STATUS: PASS\nFEATURES_SHA256: " + S.sha256_bytes(b"features v1") + "\n")
    (d / S.FORWARD_SNAPSHOT).write_bytes(b"x")
    with pytest.raises(SystemExit, match="snapshot exists"):
        rt.check_gates(d, base, files)
    (d / S.FORWARD_SNAPSHOT).unlink()
    (d / "frozen.json").write_text(json.dumps({"model": "G"}))                       # frozen.json edited after freezing
    with pytest.raises(SystemExit, match="frozen.json hash"):
        rt.check_gates(d, base, files)
    (d / "frozen.json").write_text(json.dumps({"model": "B1"}))
    (d / "test_started.json").write_text("{}")
    with pytest.raises(SystemExit, match="already started"):
        rt.check_gates(d, base, files)
    (d / "test_started.json").unlink()
    (d / "results_test.json").write_text("{}")
    with pytest.raises(SystemExit, match="results_test.json exists"):
        rt.check_gates(d, base, files)


def test_audit_files_cover_frozen_and_code_and_start_marker(tmp_path):
    for need in ("../team_games_features.parquet", "../../../../data/raw/nba_open/team_box_2022.parquet",
                 "../../../../data/raw/nba_open/team_box_2026.parquet", "../../../../data/raw/nba_open/play_by_play_2025.parquet",
                 "../../../../data/raw/nba_open/play_by_play_2026.parquet", "../../../../data/raw/nba_open/player_box_2023.parquet",
                 "../../../../data/raw/nba_open/player_box_2024.parquet",
                 "../../../../data/raw/nba_odds_samples/closing_lines_odds_api.parquet", "../frozen.json", "run_test.py", "models.py", "sm_lib.py", "features.py", "../../nba_totals_grid/grid_lib.py",
                 "../../nba_formula_accuracy/fa_lib.py", "../../nba_line_error/impl_a/fa_ext.py", "../../nba_line_error/impl_a/lea_lib.py"):
        assert need in S.AUDIT_FILES
    rt.start_test(tmp_path)
    assert json.loads((tmp_path / "test_started.json").read_text())["started_utc"]
    assert not (S.STUDY / "AUDIT_OK").exists() and not (S.STUDY / "results_test.json").exists() and not (S.STUDY / "test_started.json").exists()


def test_make_audit_hashes_only_prints(tmp_path, capsys):
    src = (HERE / "make_audit_hashes.py").read_text()
    assert "write_text" not in src and "open(" not in src and "print(" in src
    h = S.audit_hashes(HERE, ("sm_lib.py",))
    assert h["sha256"]["sm_lib.py"] == S.sha256_file(HERE / "sm_lib.py")


def test_select_gates(tmp_path):
    d = tmp_path
    fp = d / "team_games_features.parquet"
    fp.write_bytes(b"features")
    with pytest.raises(SystemExit, match="AGREEMENT.md not found"):
        rs.gate(d, fp)
    (d / "AGREEMENT.md").write_text("STATUS: FAIL\nFEATURES_SHA256: x\n")
    with pytest.raises(SystemExit, match="does not say STATUS: PASS"):
        rs.gate(d, fp)
    (d / "AGREEMENT.md").write_text("STATUS: PASS\nFEATURES_SHA256: " + "0" * 64 + "\n")
    with pytest.raises(SystemExit, match="changed since AGREEMENT"):
        rs.gate(d, fp)
    (d / "AGREEMENT.md").write_text("STATUS: PASS\nFEATURES_SHA256: " + S.sha256_file(fp) + "\n")
    rs.gate(d, fp)                                                                    # all clear
    for f in ("frozen.json", "AUDIT_OK", "results_test.json", "results_select.json", "test_started.json"):
        (d / f).write_text("{}")
        with pytest.raises(SystemExit, match="selection runs once"):
            rs.gate(d, fp)
        (d / f).unlink()


# ------------------------------------------------------------------ compare
def test_compare_frames_pass_and_fail():
    rng = np.random.default_rng(0)
    A = pd.DataFrame({"game_id": [1, 1, 2, 2], "team": ["A", "B", "C", "D"], "season": "2009-10", "opp": ["B", "A", "D", "C"],
                      "date": pd.Timestamp("2010-01-01"), **{c: rng.normal(size=4) for c in C.NUMERIC}})
    sample = A[["game_id", "team"]].iloc[:3]
    B = A.iloc[:3].copy()
    assert C.compare_frames(A, B, sample)["pass"]
    B2 = B.copy()
    B2.loc[B2.index[1], "ortg_x_l5"] += 1e-8
    r = C.compare_frames(A, B2, sample)
    assert not r["pass"] and r["mismatching_columns"] == ["ortg_x_l5"]
    B3 = B.copy()
    B3.loc[B3.index[0], "dos"] = np.nan
    r3 = C.compare_frames(A, B3, sample)
    assert not r3["pass"] and r3["columns"]["dos"]["nan_pattern_mismatch"] == 1
    B4 = B.iloc[:2]
    assert not C.compare_frames(A, B4, sample)["pass"]
    B5 = B.copy()
    B5.loc[B5.index[0], "ortg_x_l5"] += 5e-10
    assert C.compare_frames(A, B5, sample)["pass"]                                      # within 1e-9


# ------------------------------------------------------------------ prediction from frozen / OT flags
def test_predict_frozen_all_candidates():
    rows = pd.DataFrame({f: np.zeros(3) for f in S.FEATS})
    rows["lg_ortg"], rows["lg_pace"] = 100.0, 100.0
    r = {"kind": "R1", "b0": 100.0, "coef": [0.0] * len(S.FEATS), "mu": [0.0] * len(S.FEATS), "sd": [1.0] * len(S.FEATS)}
    frozen = {"grid": {"b1_flat": 1, "b2_flat": 2, "g_star_flat": 3}, "ridge": {"R1": r, "R2": dict(r, kind="R2", b0=2.0)},
              "stack": {"w0": 1.0, "wG": 0.5, "w1": 0.25, "w2": 0.25}}
    gp = {1: np.array([90.0] * 3), 2: np.array([91.0] * 3), 3: np.array([110.0] * 3)}
    P = lambda m: rt.predict_frozen(frozen, m, rows, gp)  # noqa: E731
    assert (P("B1") == 90).all() and (P("B2") == 91).all() and (P("G") == 110).all()
    assert (P("R1") == 100).all() and (P("R2") == 2.0 + 100.0).all()                     # R2 adds lg_ortg * lg_pace / 100 back
    assert (P("E1") == 0.5 * (110 + 102)).all()
    assert (P("E2") == 1.0 + 55 + 25 + 25.5).all()
    assert rt.needed_flats(frozen, ["B1"]) == [1] and rt.needed_flats(frozen, ["R1", "R2"]) == [] and rt.needed_flats(frozen, ["E2", "B1"]) == [1, 3]


def test_ot_detection_toy():
    pbp = pd.DataFrame({"game_id": [1, 1, 2, 2, 3], "period_number": [1, 4, 1, 5, 6]})
    assert rt.ot_from_pbp(pbp).to_dict() == {1: False, 2: True, 3: True}
    pb = pd.DataFrame({"game_id": [1] * 4 + [2] * 4, "team_id": [10, 10, 11, 11] * 2,
                       "minutes": ["120", "120", "240", None, "145", "120", "120", "145"]})
    # game 1: team 10 = 240, team 11 = 240 -> no OT; game 2: team 10 = 265 (OT), team 11 = 265
    assert rt.ot_from_player_box(pb).to_dict() == {1: False, 2: True}


def test_biggest_misses_and_metrics_excl_ot():
    rows = pd.DataFrame({"game_id": [1, 1, 2, 2, 3, 3], "home": [1.0, 0.0] * 3, "team": list("ABCDEF"), "opp": list("BADCFE"),
                         "season": "2022-23", "date": pd.Timestamp("2023-01-01"), "pts": [100.0, 100.0, 120.0, 120.0, 110.0, 100.0]})
    pred = np.array([100.0, 100.0, 100.0, 100.0, 105.0, 105.0])
    ot = pd.Series({1: False, 2: True, 3: False})
    top = rt.biggest_misses(rows, pred, ot, n=2)
    assert top[0]["game_id"] == 2 and top[0]["error"] == -40.0 and top[0]["ot"] is True
    m = rt.metrics_block(rows, pred, ot)
    assert m["mae_excl_ot"] == pytest.approx(0.0)                                  # games 1 and 3 are exact; game 2 (OT) is off by 40
    assert m["n_ot"] == 1 and m["n_excl_ot"] == 2 and m["n_ot_unknown"] == 0 and m["mae_ot_only"] == 40.0


def test_audit_files_exist_and_exclude_live_file():
    assert not any("team_box_2027" in f for f in S.AUDIT_FILES)
    for f in S.AUDIT_FILES:
        assert (HERE / f).exists(), f


def test_gate_order_in_source():
    src = (HERE / "run_test.py").read_text()
    body = src[src.index("def main"):]
    order = [body.index(x) for x in ("check_gates(study)", "make_forward_snapshot(study)", "start_test(study)", "rs.load_stage(")]
    assert order == sorted(order)
    gate = src[src.index("def check_gates"):src.index("def start_test")]
    assert gate.index("check_frozen_hash") < gate.index("check_audit_ok") < gate.index("check_agreement_pass") < gate.index("test_started.json")


def test_forward_snapshot_copy_and_reader(tmp_path, monkeypatch):
    src = tmp_path / "box"
    src.mkdir()
    pd.DataFrame({"a": [1, 2]}).to_parquet(src / "team_box_2027.parquet")
    monkeypatch.setattr(S, "BOX", src)
    dst = S.make_forward_snapshot(tmp_path)
    assert dst.name == S.FORWARD_SNAPSHOT and S.sha256_file(dst) == S.sha256_file(src / "team_box_2027.parquet")


def test_forward_block_error_is_recorded_not_raised(tmp_path):
    out = rt.forward_block({"grid": {}}, tmp_path / "missing.parquet", ["B1"])
    assert "error" in out


def test_paired_bootstrap():
    d = np.r_[np.full(50, -1.0), np.full(50, -3.0)]
    pt, lo, hi = M.paired_boot_ci(d, n_boot=500)
    assert pt == -2.0 and lo <= pt <= hi and hi < 0
    assert M.paired_boot_ci(d, n_boot=500) == (pt, lo, hi)                              # fixed seed
    assert M.BOOT_N == 10000 and M.BOOT_SEED == 20261023
    rows = pd.DataFrame({"game_id": np.repeat(np.arange(40), 2), "home": [1.0, 0.0] * 40, "season": "2022-23", "pts": 110.0})
    better, worse = np.full(80, 110.0), np.full(80, 100.0)
    blk = rt.paired_block(rows, better, worse)
    assert blk["pooled"]["diff"] == -20.0 and blk["ci_upper_below_zero"] is True and blk["pooled"]["n_games"] == 40
    assert rt.paired_block(rows, worse, better)["ci_upper_below_zero"] is False


# ------------------------------------------------------------------ slow: real discovery-season data
@pytest.mark.slow
def test_grid_team_points_sum_to_grid_total_real_2010_11():
    gs = M.GridSeason("2010-11")
    for flat in (M.B1_FLAT, M.B2_FLAT, 12345, 20001, 25288, 3, 26879):
        spec = M.gl.spec_from_flat(flat)
        a, h = gs.team_points(spec)
        assert np.abs(a + h - gs.total(spec)).max() < 1e-9
    P = gs.all_totals()
    a, h = gs.team_points(M.gl.spec_from_flat(25288))
    assert np.abs(P[25288] - (a + h)).max() < 1e-9
    assert M.B1_FLAT == M.fa.DEFAULT_FLAT and M.B2_FLAT == M.fx.FLAT


@pytest.mark.slow
def test_real_features_parquet_sanity():
    p = S.STUDY / "team_games_features.parquet"
    if not p.exists():
        pytest.skip("features.py has not been run")
    F = pd.read_parquet(p)
    assert (F.groupby("game_id").size() == 2).all() and not F.duplicated(["game_id", "team"]).any()
    assert F[S.FEATS + [S.TARGET]].notna().all().all()
    assert set(F.season) == set(S.ALL_SEASONS)
    first = F[F.gp_x == 0]
    assert (first.ortg_x_season == first.ortg_x_prev).all() and (first.rest_x == 4).all()
    assert ((F.b2b_x == 1) == (F.rest_x == 0)).all() and F.rest_x.between(0, 4).all()
    assert F.groupby("season").dos.min().eq(0).all()
    ids = pd.read_csv(S.STUDY / "sample_ids.csv")
    assert len(ids) == 300 and set(ids.season) <= set(S.SAMPLE_SEASONS)
    # 2010-11: features' dates equal the grid's decision dates for every game
    gs = M.GridSeason("2010-11")
    gd = pd.Series(gs.dates.astype("datetime64[ns]"), index=gs.games.game_id.values)
    f = F[(F.season == "2010-11") & (F.home == 1)].set_index("game_id").date
    assert (gd.reindex(f.index).values == f.values).all()
    # grid covers exactly the games of the feature table (2010-11 and 2025-26)
    for s in ("2010-11",):
        assert set(M.GridSeason(s).games.game_id) == set(F[F.season == s].game_id)
