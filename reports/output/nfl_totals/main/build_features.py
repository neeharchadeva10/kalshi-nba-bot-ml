"""Research log #66: build per-game predictions and features into ../main_out/ (reads nflverse caches only; no network).

Outputs (no outcome of any game is stored in them):
  game_table.parquet       one row per game (total_line present): metadata, close, L, 9 A-PTS, 3 A-EPA, A-DRIVE, A-RES prediction + features
  epa_teamfeatures.parquet blended EW off_epa / def_epa / plays per team going INTO each game, h in {4, 8, 16}, k = 4
  pbp_teamgame.parquet     per team-game pbp aggregates (39 inconsistent games excluded)
  epa_ols.json             A-EPA OLS coefficients per h (fitted on D team-games only)
  ares_ridge.json          A-RES ridge (lambda by leave-one-season-out inside D, coefficients; fitted on D rows only)
  fair_model.npz           Part C fair model (fitted on 1999-2024 finals; not evaluated)
  agree_*.csv              agreement outputs for the independent checker (predictions / team features / D-only version stats)
Outcomes are touched only to FIT (OLS and ridge on D rows; the fair model on 1999-2024 finals) and to compute D-window version
statistics for agree_versions_D.csv; nothing from H1/H2/H3/HK is compared with finals or lines.
Run:  .venv/Scripts/python.exe reports/output/nfl_totals/main/build_features.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nfl_lib as N  # noqa: E402
import part_a as A  # noqa: E402
import part_c as C  # noqa: E402

RES_FEATURES = ["gap_pts", "gap_epa", "gap_drive", "ns_home", "ns_away", "proe_home", "proe_away", "qb_out_home", "qb_out_away",
                "hc_change_home", "hc_change_away", "hc_first_home", "hc_first_away", "rest_home", "rest_away", "short_week",
                "primetime", "div_game", "neutral", "week", "playoff", "ref_pen", "close"]


def wide_epa(epa_f: pd.DataFrame) -> pd.DataFrame:
    """long (game_id, side, ...) -> one row per game with <col>_home / <col>_away."""
    cols = [c for c in epa_f.columns if c.startswith("h")]
    h = epa_f[epa_f.side == "home"].set_index("game_id")[cols].add_suffix("_home")
    a = epa_f[epa_f.side == "away"].set_index("game_id")[cols].add_suffix("_away")
    return h.join(a)


def fit_epa_models(U, tg, wide, L_by_game, d_game_ids) -> tuple:
    """OLS per h on D team-games (points of the team ~ [1, oEPA_team, dEPA_opp, plays_team, plays_opp, L]).  Returns (preds dict, coefs)."""
    pts = tg.set_index(["game_id", "team"]).pts
    coefs, preds = {}, {}
    for h in N.HS:
        ids = wide.index
        Lg = L_by_game.reindex(ids).values
        Xh = N.epa_team_design(wide, h, Lg, "home")
        Xa = N.epa_team_design(wide, h, Lg, "away")
        gm = U.set_index("game_id").reindex(ids)
        in_d = np.isin(ids, d_game_ids)
        yh = np.array([pts.get((g, t), np.nan) for g, t in zip(ids, gm.home.values)])
        ya = np.array([pts.get((g, t), np.nan) for g, t in zip(ids, gm.away.values)])
        X = np.vstack([Xh[in_d], Xa[in_d]])
        y = np.concatenate([yh[in_d], ya[in_d]])
        ok = np.isfinite(X).all(axis=1) & np.isfinite(y)
        coef = N.ols_fit(X[ok], y[ok])
        coefs[h] = {"names": ["c0", "oEPA_team", "dEPA_opp", "plays_team", "plays_opp", "L"], "coef": [float(c) for c in coef],
                    "n_team_games": int(ok.sum()), "seasons": list(N.D_SEASONS)}
        preds[h] = pd.Series(N.ols_predict(coef, Xh) + N.ols_predict(coef, Xa), index=ids)
    return preds, coefs


def build(verbose=True):
    t0 = time.time()
    log = (lambda *a: print(*a, flush=True)) if verbose else (lambda *a: None)
    N.MAIN_OUT.mkdir(exist_ok=True)
    g = N.load_games()
    U = N.universe(g).reset_index(drop=True)
    tg = N.team_game_table(g)
    sc = g[g.scored]
    hist_pts = tg[tg.scored][["team", "season", "kick_s", "pts", "pts_allowed"]]
    log("games", len(g), "universe", len(U), f"{time.time() - t0:.0f}s")

    # A-PTS (9 models)
    ap = N.apts_table(U, hist_pts, sc)
    log("apts done", f"{time.time() - t0:.0f}s")

    # pbp aggregates and the pbp-based features
    pbp_tg, pbp_gm = N.load_pbp_aggregates()
    pbp_tg.to_parquet(N.MAIN_OUT / "pbp_teamgame.parquet", index=False)
    hist_pbp = N.pbp_history(pbp_tg, tg)
    log("pbp aggregated", len(pbp_tg), "team-games", f"{time.time() - t0:.0f}s")
    epa_f = N.epa_features(U, hist_pbp)
    epa_f.to_parquet(N.MAIN_OUT / "epa_teamfeatures.parquet", index=False)
    wide = wide_epa(epa_f)
    L_by_game = ap.set_index("game_id").L
    d_ids = U[U.window == "D"].game_id.values
    epa_pred, epa_coefs = fit_epa_models(U, tg, wide, L_by_game, d_ids)
    (N.MAIN_OUT / "epa_ols.json").write_text(json.dumps(epa_coefs, indent=1))
    log("epa done", f"{time.time() - t0:.0f}s")

    dr = N.drive_features(U, hist_pbp)
    dr["adrive"] = N.adrive_prediction(dr)
    rs = N.res_stat_features(U, hist_pbp)

    gt = U[["game_id", "season", "week", "game_type", "kick_s", "et_date", "home", "away", "window", "roof", "wind", "stadium_id",
            "weekday", "gametime"]].copy()
    gt["close"] = U.total_line.values
    gt = gt.merge(ap, on="game_id", how="left")
    for h in N.HS:
        gt[f"aepa_h{h}"] = gt.game_id.map(epa_pred[h]).values
    gt = gt.merge(dr[["game_id", "adrive"]], on="game_id", how="left")

    # A-RES features
    q = N.team_queries(U).merge(U[["game_id", "week", "et_date"]], on="game_id", how="left")
    inj = N.load_injuries()
    qb = N.qb_out_flags(tg, q, inj)
    qbw = q[["game_id", "team", "side"]].merge(qb, on=["game_id", "team"], how="left")
    qbh = qbw[qbw.side == "home"].set_index("game_id").qb_out
    qba = qbw[qbw.side == "away"].set_index("game_id").qb_out
    cf = N.coach_flags(tg)
    cfh = U[["game_id", "home"]].merge(cf, left_on=["game_id", "home"], right_on=["game_id", "team"], how="left").set_index("game_id")
    cfa = U[["game_id", "away"]].merge(cf, left_on=["game_id", "away"], right_on=["game_id", "team"], how="left").set_index("game_id")
    refp = N.referee_penalty_feature(g, pbp_gm).drop_duplicates("game_id").set_index("game_id").ref_pen
    f = pd.DataFrame({"game_id": U.game_id.values})
    ix = f.game_id.values
    f["gap_pts"] = gt["apts_h8_k4"].values - gt.close.values
    f["gap_epa"] = gt["aepa_h8"].values - gt.close.values
    f["gap_drive"] = gt["adrive"].values - gt.close.values
    rsi = rs.set_index("game_id").reindex(ix)
    f["ns_home"], f["ns_away"] = rsi.ns_home.values, rsi.ns_away.values
    f["proe_home"], f["proe_away"] = rsi.proe_home.values, rsi.proe_away.values
    f["qb_out_home"], f["qb_out_away"] = qbh.reindex(ix).values, qba.reindex(ix).values
    f["hc_change_home"], f["hc_change_away"] = cfh.reindex(ix).hc_change.values, cfa.reindex(ix).hc_change.values
    f["hc_first_home"], f["hc_first_away"] = cfh.reindex(ix).hc_first.values, cfa.reindex(ix).hc_first.values
    f["rest_home"], f["rest_away"] = U.home_rest.values.astype(float), U.away_rest.values.astype(float)
    f["short_week"] = (np.minimum(U.home_rest.values, U.away_rest.values) <= 5).astype(float)
    f["primetime"] = (U.gametime.fillna("").astype(str).values >= "19:00").astype(float)
    f["div_game"] = U.div_game.values.astype(float)
    f["neutral"] = (U.location == "Neutral").values.astype(float)
    f["week"] = U.week.values.astype(float)
    f["playoff"] = (U.game_type != "REG").values.astype(float)
    f["ref_pen"] = refp.reindex(ix).values
    f["close"] = gt.close.values
    X = f[RES_FEATURES].values.astype(float)
    ok_all = np.isfinite(X).all(axis=1)

    # ridge on D rows only (target total - close: D outcomes, fitting only)
    dmask = (gt.window == "D").values
    tot_d = N.attach_outcomes(gt[dmask][["game_id", "season"]], g, N.D_SEASONS).total.values
    y_d = tot_d - gt.close.values[dmask]
    fit_rows = ok_all[dmask] & np.isfinite(y_d)
    Xd, yd, sd_ = X[dmask][fit_rows], y_d[fit_rows], gt.season.values[dmask][fit_rows]
    assert set(np.unique(sd_)) <= set(N.D_SEASONS)
    lam, mse = N.loso_lambda(Xd, yd, sd_)
    rf = N.ridge_fit(Xd, yd, lam)
    res_pred = np.full(len(X), np.nan)
    res_pred[ok_all] = N.ridge_predict(rf, X[ok_all])
    gt["ares_pred"] = res_pred
    (N.MAIN_OUT / "ares_ridge.json").write_text(json.dumps({
        "features": RES_FEATURES, "lambda": lam, "loso_mse_by_lambda": mse, "n_rows": int(fit_rows.sum()), "seasons": list(N.D_SEASONS),
        "mu": rf["mu"].tolist(), "sd": rf["sd"].tolist(), "beta_on_z": rf["beta"].tolist(), "intercept": rf["intercept"]}, indent=1))
    for c in RES_FEATURES:
        gt[f"f_{c}"] = f[c].values
    gt.to_parquet(N.MAIN_OUT / "game_table.parquet", index=False)
    log("game_table", gt.shape, "ridge lambda", lam, f"{time.time() - t0:.0f}s")

    # fair model (finals 1999-2024; fit only)
    fs = g[g.total_line.notna() & g.scored & g.game_type.isin(N.GAME_TYPES) & (g.season <= 2024)]
    model = C.fit_fair(fs.total_line.values, (fs.home_score + fs.away_score).values)
    C.save_fair(model, N.MAIN_OUT / "fair_model.npz")

    # agreement outputs
    sg = pd.read_csv(N.STUDY / "sample_games.csv")
    pc = [f"apts_h{h}_k{k}" for h in N.HS for k in N.KS]
    ag = sg.merge(gt[["game_id"] + pc], on="game_id", how="left")
    ag.to_csv(N.MAIN_OUT / "agree_apts_predictions.csv", index=False, float_format="%.10f")
    st = pd.read_csv(N.STUDY / "sample_teamgames.csv")
    st["team_c"] = st.team.map(N.canon)                      # sample may use game_id-style codes (OAK, SD, STL)
    ec = st.merge(epa_f.drop(columns="side").rename(columns={"team": "team_c"}), on=["game_id", "team_c"], how="left").drop(columns="team_c")
    cols = [c for c in ec.columns if c not in ("game_id", "team")]
    ec = ec[["game_id", "team"] + sorted(cols)]
    ec.to_csv(N.MAIN_OUT / "agree_epa_teamgames.csv", index=False, float_format="%.10f")
    sv = list(pd.read_csv(N.STUDY / "sample_versions_A.csv").version)
    dframe = N.attach_outcomes(gt[gt.window == "D"], g, N.D_SEASONS)
    rows = [A.version_row(dframe, v) for v in sv]
    pd.DataFrame(rows).rename(columns={"n": "n_bets", "wins": "wins", "losses": "losses", "pushes": "pushes", "mean_pnl": "mean_pnl"})[
        ["version", "n_bets", "wins", "losses", "pushes", "mean_pnl"]].to_csv(N.MAIN_OUT / "agree_versions_D.csv", index=False, float_format="%.10f")
    log("agreement files: games", len(ag), "teamgames", len(ec), "versions", len(rows), f"{time.time() - t0:.0f}s")
    return gt


if __name__ == "__main__":
    build()
