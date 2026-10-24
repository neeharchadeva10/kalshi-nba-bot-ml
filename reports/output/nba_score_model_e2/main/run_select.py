"""Research log #62, step 4: SELECTION.  Fit ridge A / B / C on the residual of the frozen E1 over 2021-22 + 2022-23 (lambda by
leave-one-season-out over those two seasons); choose on 2023-24 by total MAE among A, B, C and E1 itself.  If E1 is best: STOP (no E2,
no frozen_e2.json).  Else freeze frozen_e2.json (+ sha256).

REFUSES unless AGREEMENT.md says STATUS: PASS for the current injury_features.parquet.  Runs once (refuses if any result file exists).
Only the train + validation seasons' outcomes are ever read (E1's load_stage reads them for exactly the seasons requested; the test
seasons are never requested).
Run:  .venv/Scripts/python.exe reports/output/nba_score_model_e2/main/run_select.py"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import e2_lib as E  # noqa: E402

RESULT_FILES = ("frozen_e2.json", "frozen_e2.sha256", "results_select.json", "STOP.md", "AUDIT_OK", "test_started.json", "results_test.json")


def gate(study=E.STUDY, features_path=None):
    E.check_agreement_pass(study, features_path)
    for f in RESULT_FILES:
        if (Path(study) / f).exists():
            raise SystemExit(f"REFUSED: {f} exists; selection runs once.")


def residual_frame(rows: pd.DataFrame, inj: pd.DataFrame, e1_pred: np.ndarray) -> pd.DataFrame:
    """rows = E1 rows (game_id, team, season, home, pts ...), inj = injury features, e1_pred aligned with rows.  Adds e1 and the
    residual target r = pts - e1.  One-to-one on (game_id, team); every row must have its injury features."""
    D = rows.copy()
    D["e1"] = np.asarray(e1_pred, float)
    cols = ["game_id", "team"] + E.FEAT_COLS
    D = D.merge(inj[cols], on=["game_id", "team"], how="left", validate="one_to_one")
    assert D[E.FEAT_COLS].notna().all().all(), "injury features missing for some rows"
    D["r"] = D["pts"] - D["e1"]
    return D


def evaluate(D, pred):
    return E.game_metrics(D.game_id.values, D.home.values, D.pts.values, pred)


def select(train: pd.DataFrame, val: pd.DataFrame, log=print):
    """Pure given the two residual frames.  Returns (record, frozen dict or None when E1 is best -> STOP)."""
    ok_t, ok_v = np.isfinite(train.e1.values), np.isfinite(val.e1.values)
    train, val = train[ok_t].reset_index(drop=True), val[ok_v].reset_index(drop=True)
    assert set(train.season) == set(E.TRAIN) and set(val.season) == set(E.VALID)
    models, preds = {}, {"E1": val.e1.values.astype(float)}
    for v in ("A", "B", "C"):
        models[v] = E.fit_variant(train, train.r.values.astype(float), v)
        preds[v] = val.e1.values + E.predict_variant(models[v], val)
        log(f"variant {v}: lambda {models[v]['lambda']}")
    table = {k: E.strip(evaluate(val, preds[k])) for k in E.CANDIDATE_ORDER}
    chosen = E.choose(table)
    rec = {"train_seasons": list(E.TRAIN), "validation_seasons": list(E.VALID), "n_train_rows": int(len(train)),
           "n_val_rows": int(len(val)), "n_dropped_no_e1_train": int((~ok_t).sum()), "n_dropped_no_e1_val": int((~ok_v).sum()),
           "candidates": table, "chosen": chosen, "stop": chosen == "E1",
           "models": {v: {k: m[k] for k in ("variant", "features", "lambda", "cv_mse", "b0", "coef", "mu", "sd", "n_rows")}
                      for v, m in models.items()}}
    frozen = None
    if chosen != "E1":
        frozen = {"model": chosen, "preregistration": "reports/output/nba_score_model_e2/PREREGISTRATION.md", "ridge": models[chosen],
                  "split": {"train": list(E.TRAIN), "validation": list(E.VALID), "test": list(E.TEST)},
                  "validation": {k: table[k] for k in table}}
    return rec, frozen


def fmt_table(table):
    return pd.DataFrame([{"id": k, **{kk: v[kk] for kk in ("total_mae", "pct_within_10", "team_mae", "bias")}} for k, v in table.items()])


def coef_table(rec):
    rows = []
    for v, m in rec["models"].items():
        rows.append({"variant": v, "lambda": m["lambda"], "b0": m["b0"], **{f"coef[{f}]": c for f, c in zip(m["features"], m["coef"])}})
    return pd.DataFrame(rows)


def main():
    study = E.STUDY
    gate(study)
    t0 = time.time()
    X = E.load_e1()
    S, RS, RT = X["S"], X["RS"], X["RT"]
    frozen_e1 = S.check_frozen_hash(E.E1_STUDY)
    seasons = list(E.TRAIN) + list(E.VALID)
    assert not set(seasons) & set(E.TEST)
    inj = pd.read_parquet(E.FEATURES_PARQUET)
    rows = RS.load_stage(seasons)                      # E1 features + team points (target) for train + validation seasons only
    assert set(rows.season) == set(seasons)
    e1 = np.full(len(rows), np.nan)
    for s in seasons:
        m = (rows.season == s).values
        e1[m] = RT.season_predictions(frozen_e1, rows[m], s, ["E1"])["E1"]
        print(f"E1 predictions {s}: {m.sum()} rows, {np.isnan(e1[m]).sum()} NaN", flush=True)
    D = residual_frame(rows, inj, e1)
    train, val = D[D.season.isin(E.TRAIN)], D[D.season.isin(E.VALID)]
    rec, frozen = select(train, val)
    rec["runtime_s"] = time.time() - t0
    rec["e1_frozen_sha256"] = E.sha256_file(E.E1_STUDY / "frozen.json")
    rec["features_sha256"] = E.sha256_file(E.FEATURES_PARQUET)
    rec["read_log"] = [[list(c), list(s_)] for c, s_ in RS.READ_LOG]
    assert all(set(s_) <= set(seasons) for _, s_ in RS.READ_LOG), "outcomes of a non-train/validation season were requested"
    (study / "results_select.json").write_text(json.dumps(rec, indent=1, default=str))
    tab = fmt_table(rec["candidates"])
    tab.to_csv(study / "validation_table.csv", index=False)
    coef_table(rec).to_csv(study / "validation_coefficients.csv", index=False)
    print(tab.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print(coef_table(rec).to_string(index=False))
    if frozen is None:
        (study / "STOP.md").write_text("# STOP - E1 is best on the 2023-24 validation season; no E2 is frozen and the test is not run.\n\n"
                                       + tab.to_string(index=False, float_format=lambda x: f"{x:.4f}") + "\n", encoding="utf-8")
        print("STOP: E1 is best on validation. No frozen_e2.json.")
        return
    frozen["e1_frozen_sha256"], frozen["features_sha256"] = rec["e1_frozen_sha256"], rec["features_sha256"]
    blob = json.dumps(frozen, indent=1, sort_keys=True).encode()
    (study / "frozen_e2.json").write_bytes(blob)
    h = E.sha256_bytes(blob)
    (study / "frozen_e2.sha256").write_text(h + "  frozen_e2.json\n")
    print("CHOSEN:", rec["chosen"], "| frozen_e2.json SHA-256:", h, f"| {rec['runtime_s']:.0f}s")


if __name__ == "__main__":
    main()
