"""Research log #59, step 3: SELECTION on the validation seasons (2019-20 .. 2021-22), fitting on 2008-09 .. 2018-19 only.

REFUSES unless AGREEMENT.md says STATUS: PASS for the current team_games_features.parquet.  Runs once.
Order:
  1. features of train + validation rows are read WITHOUT the target; the target (pts) is then read for exactly train + validation seasons
     (test / forward seasons are never requested - READ_LOG records every read, tests check it);
  2. the grid (26,880 formulas, team points sum to the grid's totals) is scored on the VALIDATION seasons: the lowest pooled total MAE is
     the G family's entry G*;  G* is then evaluated in-season on the TRAIN seasons only to build E2's stacking inputs;
  3. R1 / R2 ridge (lambda by leave-one-season-out CV inside train), E2 weights fitted on train out-of-fold predictions;
  4. all 7 candidates are scored on validation; the lowest pooled total MAE is frozen (frozen.json + frozen.sha256).
Run:  .venv/Scripts/python.exe reports/output/nba_score_model/main/run_select.py"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sm_lib as S  # noqa: E402
import models as M  # noqa: E402

FEATURES = S.STUDY / "team_games_features.parquet"
READ_LOG: list = []
TOP_N = 20


def read_columns(seasons, columns, path=None):
    cols = list(columns)
    READ_LOG.append((tuple(cols), tuple(seasons)))
    return pq.read_table(path or FEATURES, columns=cols, filters=[("season", "in", list(seasons))]).to_pandas()


def load_stage(seasons, path=None):
    """Features first (no target), then the target for the same seasons, merged on (game_id, team)."""
    X = read_columns(seasons, S.IDS + S.FEATS, path)
    y = read_columns(seasons, ["game_id", "team", "season", S.TARGET], path)[["game_id", "team", S.TARGET]]
    D = X.merge(y, on=["game_id", "team"], how="left", validate="one_to_one")
    assert D[S.TARGET].notna().all()
    return D.sort_values(["season", "date", "game_id", "team"], kind="stable").reset_index(drop=True)


def gate(study=S.STUDY, features_path=None):
    S.check_agreement_pass(study, features_path)
    for f in ("frozen.json", "frozen.sha256", "results_select.json", "AUDIT_OK", "test_started.json", "results_test.json"):
        if (Path(study) / f).exists():
            raise SystemExit(f"REFUSED: {f} exists; selection runs once.")


def rank_flats(pooled):
    """Flat indices by pooled total MAE ascending; ties -> smaller flat index."""
    return np.argsort(pooled["mae"], kind="stable")


def choose(table):
    """table: {id: metrics}; lowest total_mae wins, ties -> CANDIDATE_ORDER."""
    return min(table, key=lambda k: (table[k]["total_mae"], M.CANDIDATE_ORDER.index(k)))


def grid_rank_on_validation(val_rows):
    """Scores all 26,880 formulas on the validation seasons (one season at a time, only sums kept)."""
    parts, seasons = [], {}
    for s in S.VALID:
        t0 = time.time()
        gs = M.GridSeason(s)
        rows = val_rows[val_rows.season == s]
        assert set(gs.games.game_id) == set(rows.game_id), f"{s}: grid games differ from feature games"
        act_rows = rows.groupby("game_id")[S.TARGET].sum()
        actual = act_rows.reindex(gs.games.game_id.values).values.astype(float)
        assert np.allclose(actual, gs.actual_totals()), f"{s}: hoopR totals differ between feature rows and the grid world"
        P = gs.all_totals()
        assert P.shape[0] == 26880 and np.isfinite(P).all()
        parts.append(M.formula_sums(P, actual))
        del P
        seasons[s] = gs
        print(f"  grid {s}: {len(actual)} games, {time.time() - t0:.0f}s", flush=True)
    return M.pool(parts), seasons


def team_rows_for(gs, flat, rows):
    spec = M.gl.spec_from_flat(int(flat))
    a, h = gs.team_points(spec)
    return M.map_to_rows(gs.games, a, h, rows)


def evaluate(rows, pred):
    return M.game_metrics(rows.game_id.values, rows.home.values, rows[S.TARGET].values, pred)


def select(train, val, log=print):
    """Pure given the two frames (rows with features + pts).  Returns (record, frozen dict)."""
    log("validation grid (26,880 formulas) ...")
    pooled, vgs = grid_rank_on_validation(val)
    order = rank_flats(pooled)
    gstar = int(order[0])
    gspec, gnames = M.spec_info(gstar)
    log(f"G* = flat {gstar} {gnames} pooled validation total MAE {pooled['mae'][gstar]:.4f}")
    # G* on the training seasons (in-season grid points; constants from S-1; no training outcomes enter the grid)
    g_train = np.full(len(train), np.nan)
    for s in S.TRAIN:
        gs = M.GridSeason(s)
        m = (train.season == s).values
        assert set(gs.games.game_id) == set(train.game_id[m]), f"{s}: grid games differ from feature games"
        g_train[m] = team_rows_for(gs, gstar, train[m])
    assert np.isfinite(g_train).all()
    r1, oof1 = M.fit_ridge_model(train, "R1")
    r2, oof2 = M.fit_ridge_model(train, "R2")
    ytr = train[S.TARGET].values.astype(float)
    stack = M.fit_stack(g_train, oof1, oof2, ytr)
    log(f"R1 lambda {r1['lambda']}, R2 lambda {r2['lambda']}, stack {stack}")
    # validation predictions
    preds = {k: np.full(len(val), np.nan) for k in M.CANDIDATE_ORDER}
    for s in S.VALID:
        m = (val.season == s).values
        rows = val[m]
        gs = vgs[s]
        preds["B1"][m] = team_rows_for(gs, M.B1_FLAT, rows)
        preds["B2"][m] = team_rows_for(gs, M.B2_FLAT, rows)
        preds["G"][m] = team_rows_for(gs, gstar, rows)
    preds["R1"] = M.ridge_model_predict(r1, val)
    preds["R2"] = M.ridge_model_predict(r2, val)
    preds["E1"] = 0.5 * (preds["G"] + preds["R2"])
    preds["E2"] = M.stack_predict(stack, preds["G"], preds["R1"], preds["R2"])
    table, by_season = {}, {}
    for k in M.CANDIDATE_ORDER:
        assert np.isfinite(preds[k]).all(), k
        table[k] = M.strip(evaluate(val, preds[k]))
        by_season[k] = {s: M.strip(evaluate(val[val.season == s], preds[k][(val.season == s).values]))["total_mae"] for s in S.VALID}
    # G top 20 (total MAE from the grid's own totals; team MAE from the split team points)
    top = []
    for rk, f in enumerate(order[:TOP_N], 1):
        pr = np.full(len(val), np.nan)
        for s in S.VALID:
            m = (val.season == s).values
            pr[m] = team_rows_for(vgs[s], int(f), val[m])
        met = M.strip(evaluate(val, pr))
        assert abs(met["total_mae"] - pooled["mae"][f]) < 1e-9, "team-point split does not reproduce the grid total MAE"
        sp, nm = M.spec_info(int(f))
        top.append({"rank": rk, "flat": int(f), "spec": sp, "names": nm, **met})
    chosen = choose(table)
    rec = {"validation_seasons": list(S.VALID), "train_seasons": list(S.TRAIN), "n_train_rows": int(len(train)),
           "n_val_rows": int(len(val)), "g_star": {"flat": gstar, "spec": gspec, "names": gnames},
           "candidates": table, "candidates_by_season_total_mae": by_season, "g_top20": top, "chosen": chosen,
           "ridge": {"R1": {k: v for k, v in r1.items() if k != "coef"}, "R2": {k: v for k, v in r2.items() if k != "coef"}},
           "stack": stack,
           "grid_all_formulas_summary": {"best_mae": float(pooled["mae"][gstar]), "median_mae": float(np.median(pooled["mae"])),
                                         "worst_mae": float(pooled["mae"].max()),
                                         "b1_rank": int((pooled["mae"] < pooled["mae"][M.B1_FLAT]).sum() + 1),
                                         "b2_rank": int((pooled["mae"] < pooled["mae"][M.B2_FLAT]).sum() + 1)}}
    b1s, b2s = M.spec_info(M.B1_FLAT), M.spec_info(M.B2_FLAT)
    frozen = {"model": chosen, "reference_model": "B1", "preregistration": "reports/output/nba_score_model/PREREGISTRATION.md",
              "split": {"train": list(S.TRAIN), "validation": list(S.VALID), "test": list(S.TEST), "forward": S.FORWARD},
              "grid": {"g_star_flat": gstar, "g_star_spec": gspec, "g_star_names": gnames, "b1_flat": M.B1_FLAT, "b1_spec": b1s[0],
                       "b2_flat": M.B2_FLAT, "b2_spec": b2s[0], "legacy_2022": True,
                       "rule": "every season S refits every constant on S-1 only (fa_ext.build_world_ext)"},
              "ridge": {"R1": r1, "R2": r2}, "stack": stack, "features": list(S.FEATS),
              "validation": {k: {kk: table[k][kk] for kk in ("total_mae", "pct_within_10", "team_mae", "bias")} for k in table}}
    return rec, frozen, pooled


def fmt_table(table):
    rows = [{"id": k, **{kk: v[kk] for kk in ("total_mae", "pct_within_10", "total_rmse", "bias", "team_mae", "margin_mae", "winner_pct")}}
            for k, v in table.items()]
    return pd.DataFrame(rows)


def main():
    study = S.STUDY
    gate(study)
    t0 = time.time()
    train = load_stage(S.TRAIN)
    val = load_stage(S.VALID)
    assert set(train.season) == set(S.TRAIN) and set(val.season) == set(S.VALID)
    assert not (set(train.season) | set(val.season)) & (set(S.TEST) | {S.FORWARD})
    rec, frozen, pooled = select(train, val)
    rec["runtime_s"] = time.time() - t0
    rec["read_log"] = [[list(c), list(s)] for c, s in READ_LOG]
    (study / "results_select.json").write_text(json.dumps(rec, indent=1, default=str))
    tab = fmt_table(rec["candidates"])
    tab.to_csv(study / "validation_table.csv", index=False)
    top = pd.DataFrame([{k: r[k] for k in ("rank", "flat", "total_mae", "pct_within_10", "team_mae", "bias")} | r["names"] for r in rec["g_top20"]])
    top.to_csv(study / "validation_g_top20.csv", index=False)
    print(tab.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print(top.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    blob = json.dumps(frozen, indent=1, sort_keys=True).encode()
    (study / "frozen.json").write_bytes(blob)
    h = S.sha256_bytes(blob)
    (study / "frozen.sha256").write_text(h + "  frozen.json\n")
    print("CHOSEN:", rec["chosen"], "| frozen.json SHA-256:", h, f"| {rec['runtime_s']:.0f}s")


if __name__ == "__main__":
    main()
