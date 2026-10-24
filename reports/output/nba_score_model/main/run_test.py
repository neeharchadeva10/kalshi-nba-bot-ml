"""Research log #59, step 4: the TEST stage (2022-23 .. 2025-26, plus 2026-27 to date, descriptive).  DO NOT RUN without the Opus leak audit.

REFUSES (before any outcome is loaded), in this order, unless
  1. frozen.json's SHA-256 equals frozen.sha256,
  2. AUDIT_OK is JSON {"sha256": {path: hash}} covering every file in sm_lib.AUDIT_FILES (this code, #56/#57/grid code, frozen.json,
     the features parquet, the raw box / play-by-play / player-box / closing-line inputs; NOT the live team_box_2027) with matching hashes,
  3. AGREEMENT.md says PASS for the current features parquet,
  4. test_started.json, results_test.json and the forward snapshot do not exist.
Then: forward snapshot copy -> test_started.json -> only afterwards any test-season outcome is loaded.  The test runs once.
Amendment 2026-10-23 00:37Z: sensitivity_legacy_false (E1, B1; not primary), paired bootstrap CIs, non-fatal forward block on the snapshot.

Reported for the frozen model and for B1 (reference): total MAE, % of totals within 10, RMSE, bias, team-score MAE, margin MAE, winner %,
MAE excluding OT games, the 20 biggest misses; by season and pooled.  ONE reference row: the sportsbook close's total MAE on the same games
(never an input).  OT flag: play-by-play max period > 4 (files exist for 2024-25, 2025-26), else player-box minutes (team minutes > 252.5);
where both exist they are cross-checked and the disagreements counted."""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sm_lib as S  # noqa: E402
import models as M  # noqa: E402
import run_select as rs  # noqa: E402

OT_MINUTES = 252.5            # a team's player minutes: 240 regulation, +25 per overtime


def check_gates(study: Path, base: Path = HERE, files=S.AUDIT_FILES) -> dict:
    """All refusals, before anything is loaded.  Order: frozen hash -> AUDIT_OK (incl. input hashes) -> agreement -> run-once checks."""
    study = Path(study)
    frozen = S.check_frozen_hash(study)
    S.check_audit_ok(study, base, files)
    S.check_agreement_pass(study)
    if (study / "test_started.json").exists():
        raise SystemExit("REFUSED: test_started.json exists; the test stage already started (runs once).")
    if (study / "results_test.json").exists():
        raise SystemExit("REFUSED: results_test.json exists; the test stage runs once.")
    if (study / S.FORWARD_SNAPSHOT).exists():
        raise SystemExit("REFUSED: the forward snapshot exists; the test stage already started (runs once).")
    return frozen


def start_test(study: Path):
    (Path(study) / "test_started.json").write_text(
        json.dumps({"started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}))


# ------------------------------------------------------------------ OT flags
def ot_from_pbp(pbp: pd.DataFrame) -> pd.Series:
    """game_id -> True if any play has period_number > 4."""
    mx = pbp.groupby("game_id").period_number.max()
    return (mx > 4)


def ot_from_player_box(pb: pd.DataFrame) -> pd.Series:
    """game_id -> True if some team's summed player minutes exceed OT_MINUTES."""
    m = pd.to_numeric(pb.minutes, errors="coerce").fillna(0.0)
    t = pb.assign(_m=m).groupby(["game_id", "team_id"])._m.sum()
    return t.groupby(level=0).max() > OT_MINUTES


def ot_flags(game_ids, seasons) -> tuple[pd.Series, dict]:
    """OT flag per game_id (NaN where neither source covers the game) and a source/cross-check report."""
    ids = pd.Index(sorted(set(int(g) for g in game_ids)))
    pbp_flag, pb_flag = pd.Series(dtype=bool), pd.Series(dtype=bool)
    for s in seasons:
        y = S.year_of(s)
        p = S.BOX / f"play_by_play_{y}.parquet"
        if p.exists():
            df = pd.read_parquet(p, columns=["game_id", "period_number"])
            pbp_flag = pd.concat([pbp_flag, ot_from_pbp(df)])
        q = S.BOX / f"player_box_{y}.parquet"
        if q.exists():
            df = pd.read_parquet(q, columns=["game_id", "team_id", "minutes"])
            pb_flag = pd.concat([pb_flag, ot_from_player_box(df)])
    pbp_flag = pbp_flag[~pbp_flag.index.duplicated()].reindex(ids)
    pb_flag = pb_flag[~pb_flag.index.duplicated()].reindex(ids)
    both = pbp_flag.notna() & pb_flag.notna()
    flag = pbp_flag.where(pbp_flag.notna(), pb_flag)
    rep = {"n_games": int(len(ids)), "from_pbp": int(pbp_flag.notna().sum()), "from_player_box_only": int((pbp_flag.isna() & pb_flag.notna()).sum()),
           "no_source": int(flag.isna().sum()), "cross_checked": int(both.sum()),
           "disagree": int((pbp_flag[both].astype(bool) != pb_flag[both].astype(bool)).sum()),
           "n_ot": int(flag.fillna(False).astype(bool).sum())}
    return flag, rep


# ------------------------------------------------------------------ prediction
def needed_flats(frozen, models):
    g = frozen["grid"]
    out = set()
    for m in models:
        if m == "B1":
            out.add(g["b1_flat"])
        elif m == "B2":
            out.add(g["b2_flat"])
        elif m in ("G", "E1", "E2"):
            out.add(g["g_star_flat"])
    return sorted(out)


def predict_frozen(frozen, model, rows, grid_points):
    """Team points for `rows` under a frozen candidate; grid_points = {flat: array aligned with rows}."""
    g = frozen["grid"]
    if model == "B1":
        return np.asarray(grid_points[g["b1_flat"]], float)
    if model == "B2":
        return np.asarray(grid_points[g["b2_flat"]], float)
    gp = grid_points.get(g["g_star_flat"]) if model in ("G", "E1", "E2") else None
    if model == "G":
        return np.asarray(gp, float)
    r1 = M.ridge_model_predict(frozen["ridge"]["R1"], rows) if model in ("R1", "E2") else None
    r2 = M.ridge_model_predict(frozen["ridge"]["R2"], rows) if model in ("R2", "E1", "E2") else None
    if model == "R1":
        return r1
    if model == "R2":
        return r2
    if model == "E1":
        return 0.5 * (np.asarray(gp, float) + r2)
    if model == "E2":
        return M.stack_predict(frozen["stack"], gp, r1, r2)
    raise ValueError(model)


def season_predictions(frozen, rows, season, models, legacy_2022=None, reader=None):
    flats = needed_flats(frozen, models)
    pts = {}
    if flats:
        leg = frozen["grid"].get("legacy_2022", True) if legacy_2022 is None else legacy_2022
        gs = M.GridSeason(season, legacy_2022=leg, reader=reader)
        for f in flats:
            pts[f] = rs.team_rows_for(gs, f, rows)
    return {m: predict_frozen(frozen, m, rows, pts) for m in models}


def paired_block(rows, pa, pb):
    """Paired game-bootstrap CI of (total MAE of A - total MAE of B), pooled and per season.  rows/pa/pb aligned team rows."""
    ma = M.game_metrics(rows.game_id.values, rows.home.values, rows[S.TARGET].values, pa)
    mb = M.game_metrics(rows.game_id.values, rows.home.values, rows[S.TARGET].values, pb)
    d = np.abs(ma["err"]) - np.abs(mb["err"])
    season = pd.Series(rows.season.values, index=rows.game_id.values)
    season = season[~season.index.duplicated()].reindex(ma["game_ids"]).values
    out = {"pooled": dict(zip(("diff", "lo", "hi"), M.paired_boot_ci(d)), n_games=int(len(d))), "by_season": {}}
    for s_ in sorted(set(season)):
        m = season == s_
        out["by_season"][s_] = dict(zip(("diff", "lo", "hi"), M.paired_boot_ci(d[m])), n_games=int(m.sum()))
    out["ci_upper_below_zero"] = bool(out["pooled"]["hi"] < 0)
    return out


def biggest_misses(rows, pred, ot, n=20):
    met = M.game_metrics(rows.game_id.values, rows.home.values, rows[S.TARGET].values, pred)
    g = pd.DataFrame({"game_id": met["game_ids"], "pred_total": met["tot_pred"], "actual_total": met["tot_act"], "error": met["err"]})
    meta = rows[rows.home > 0][["game_id", "date", "team", "opp", "season"]].rename(columns={"team": "home_team", "opp": "away_team"})
    g = g.merge(meta, on="game_id", how="left")
    g["ot"] = g.game_id.map(ot).astype(object)
    return g.reindex(g.error.abs().sort_values(ascending=False, kind="stable").index).head(n).to_dict("records")


def metrics_block(rows, pred, ot):
    met = M.game_metrics(rows.game_id.values, rows.home.values, rows[S.TARGET].values, pred)
    out = M.strip(met)
    flag = pd.Series(met["game_ids"]).map(ot)
    known = flag.notna().values
    noot = known & ~flag.fillna(True).astype(bool).values
    e = np.abs(met["err"])
    out["mae_excl_ot"] = float(e[noot].mean()) if noot.any() else None
    out["n_excl_ot"] = int(noot.sum())
    out["n_ot"] = int((known & ~noot).sum())
    out["n_ot_unknown"] = int((~known).sum())
    out["mae_ot_only"] = float(e[known & ~noot].mean()) if (known & ~noot).any() else None
    return out


def forward_block(frozen, snapshot, models):
    """2026-27 to date on the snapshot copy (descriptive).  Errors are returned, never raised."""
    try:
        tg = pd.concat([S.L.team_games_from_box(S.L.read_year(2026), "2025-26"), S.snapshot_team_games(snapshot)], ignore_index=True)
        tg["fr"], tg["fr_opp"] = tg.team.map(S.L.FRANCHISE), tg.opp.map(S.L.FRANCHISE)
        F = S.build_features(tg, [S.FORWARD])
        if not len(F):
            return {"n_games": 0}
        pr = season_predictions(frozen, F, S.FORWARD, models, reader=S.reader_with_snapshot(snapshot))
        out = {"n_games": int(F.game_id.nunique()), "note": "descriptive; snapshot of team_box_2027; no OT source"}
        for k in models:
            out[k] = metrics_block(F, pr[k], pd.Series(dtype=object))
        return out
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def main():
    study = S.STUDY
    frozen = check_gates(study)
    snapshot = S.make_forward_snapshot(study)          # copy of the live file BEFORE the marker
    start_test(study)                                   # marker before any outcome
    model = frozen["model"]
    models = sorted(set([model, "B1", "E1", "G"]), key=M.CANDIDATE_ORDER.index)
    seasons = list(S.TEST)
    D = rs.load_stage(seasons)                           # outcomes (pts) are loaded only now
    ot, ot_rep = ot_flags(D.game_id.unique(), seasons)
    preds = {m: np.full(len(D), np.nan) for m in models}
    for s in seasons:
        m_ = (D.season == s).values
        pr = season_predictions(frozen, D[m_], s, models)
        for k in models:
            preds[k][m_] = pr[k]
    res = {"model": model, "reference": "B1", "ot_report": ot_rep, "blocks": {}, "by_season": {}, "worst20": {}}
    for k in models:
        ok = np.isfinite(preds[k])
        res.setdefault("nan_predictions", {})[k] = int((~ok).sum())
        res["blocks"][k] = {"pooled_test": metrics_block(D[ok], preds[k][ok], ot)}
        for s in seasons:
            m_ = (D.season == s).values & ok
            if m_.any():
                res["by_season"].setdefault(k, {})[s] = metrics_block(D[m_], preds[k][m_], ot)
        res["worst20"][k] = biggest_misses(D[ok], preds[k][ok], ot)
    res["paired_bootstrap"] = {"E1_vs_B1": paired_block(D, preds["E1"], preds["B1"]), "E1_vs_Gstar": paired_block(D, preds["E1"], preds["G"]),
                               "draws": M.BOOT_N, "seed": M.BOOT_SEED}
    res["E1_more_accurate_than_B1"] = res["paired_bootstrap"]["E1_vs_B1"]["ci_upper_below_zero"]
    # sensitivity, not primary: 2022-23 constants with the prior-2020-21 fit (legacy_2022 = False); E1 and B1; pooled 2022-26 with the swap
    try:
        sens = {"label": "sensitivity, not primary", "legacy_2022": False}
        m22 = (D.season == "2022-23").values
        alt = {k: preds[k].copy() for k in ("E1", "B1")}
        pr = season_predictions(frozen, D[m22], "2022-23", ["E1", "B1"], legacy_2022=False)
        for k in alt:
            alt[k][m22] = pr[k]
        for k in alt:
            sens[k] = {"pooled_2022_26_with_swap": metrics_block(D, alt[k], ot), "2022-23": metrics_block(D[m22], alt[k][m22], ot)}
        sens["paired_E1_vs_B1"] = paired_block(D, alt["E1"], alt["B1"])
        res["sensitivity_legacy_false"] = sens
    except Exception as e:  # noqa: BLE001
        res["sensitivity_legacy_false"] = {"error": f"{type(e).__name__}: {e}"}
    # ONE reference row: the sportsbook close on the same games (never an input)
    import fa_lib as fa
    close = fa.load_closing()
    met = M.game_metrics(D.game_id.values, D.home.values, D[S.TARGET].values, preds[model])
    line = pd.Series(met["game_ids"]).map(close).values.astype(float)
    has = np.isfinite(line)
    res["sportsbook_close_reference"] = {
        "n_games_with_close": int(has.sum()),
        "close_total_mae": float(np.abs(line[has] - met["tot_act"][has]).mean()) if has.any() else None,
        "frozen_model_mae_same_games": float(np.abs(met["err"][has]).mean()) if has.any() else None}
    res["forward_2026_27"] = forward_block(frozen, snapshot, [model] if model == "B1" else [model, "B1"])
    (study / "results_test.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res["blocks"][k]["pooled_test"] for k in models}, indent=1, default=str))
    print(json.dumps(res["sportsbook_close_reference"], indent=1))
    print("E1_more_accurate_than_B1:", res["E1_more_accurate_than_B1"])


if __name__ == "__main__":
    main()
