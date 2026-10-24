"""Research log #64, step 3: SELECTION.  All 8,190 versions (every non-empty subset of the 12 families x report cutoff {c60, c20}) are
fitted as residual ridges on the frozen E1 over 2021-22 + 2022-23 (lambda in {1,10,100,1000,10000} by leave-one-season-out over those
two seasons, standardised on training), scored on 2023-24 by game-total MAE (primary) and team MAE.  E1 itself is a candidate; if E1
wins: STOP (no frozen_e3.json).  Else frozen_e3.json (+ sha256) holds the winner.

REFUSES unless AGREEMENT.md says STATUS: PASS for the current e3_features.parquet.  Runs once.  Only the train + validation seasons'
outcomes are ever read (E1's load_stage reads exactly the seasons requested; the test seasons are never requested).
Run:  .venv/Scripts/python.exe reports/output/nba_score_model_e3/main/run_select.py"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import e3_lib as L  # noqa: E402

RESULT_FILES = ("frozen_e3.json", "frozen_e3.sha256", "results_select.json", "STOP.md", "AUDIT_OK", "test_started.json", "results_test.json")
TOP_N = 20
FREQ_N = 100
CHOSEN_E1 = "E1"


def gate(study=L.STUDY, features_path=None):
    L.check_agreement_pass(study, features_path)
    for f in RESULT_FILES:
        if (Path(study) / f).exists():
            raise SystemExit(f"REFUSED: {f} exists; selection runs once.")


def residual_frame(rows: pd.DataFrame, feat: pd.DataFrame, e1_pred: np.ndarray) -> pd.DataFrame:
    """rows = E1 rows (game_id, team, season, home, pts ...), feat = E3 features, e1_pred aligned with rows.  Adds e1 and the residual
    target r = pts - e1.  One-to-one on (game_id, team); every row must have its features."""
    D = rows.copy()
    D["e1"] = np.asarray(e1_pred, float)
    cols = L.all_feature_columns()
    D = D.merge(feat[["game_id", "team"] + cols], on=["game_id", "team"], how="left", validate="one_to_one")
    assert D[cols].notna().all().all(), "E3 features missing for some rows"
    D["r"] = D["pts"] - D["e1"]
    return D


def drop_nan_games(D: pd.DataFrame):
    """Drop whole games with a non-finite E1 prediction on either row (a half game cannot be scored)."""
    bad = set(D.game_id[~np.isfinite(D.e1.values)])
    keep = ~D.game_id.isin(bad)
    return D[keep].reset_index(drop=True), len(bad)


def select(train: pd.DataFrame, val: pd.DataFrame, versions=None, log=print):
    """Pure given the two residual frames.  Returns (record, frozen dict or None when E1 is best -> STOP, table of all versions)."""
    versions = L.enumerate_versions() if versions is None else versions
    train, n_bad_t = drop_nan_games(train)
    val, n_bad_v = drop_nan_games(val)
    assert set(train.season) == set(L.TRAIN) and set(val.season) == set(L.VALID)
    u, inv = np.unique(val.game_id.values, return_inverse=True)
    assert (np.bincount(inv) == 2).all()
    yv, e1v = val.pts.values.astype(float), val.e1.values.astype(float)
    rtr = train.r.values.astype(float)
    n = len(versions)
    scores = {k: np.zeros(n) for k in ("total_mae", "pct_within_10", "team_mae", "bias", "lambda")}
    nfeat = np.zeros(n, int)
    engines, colpos = {}, {}
    for cut in L.CUTOFFS:
        cols = [c for f in L.FAMILY_NAMES for c in L.family_columns(f, cut)]
        colpos[cut] = {f: [cols.index(c) for c in L.family_columns(f, cut)] for f in L.FAMILY_NAMES}
        engines[cut] = (cols, L.SubsetEngine(train[cols].values.astype(float), rtr, train.season.values, val[cols].values.astype(float)))
    t0 = time.time()
    for i, (vid, cut, fams) in enumerate(versions):
        idx = [p for f in fams for p in colpos[cut][f]]
        lam, mse, b0, b, rv = engines[cut][1].fit(idx)
        s = L.score_totals(inv, len(u), yv, e1v + rv)
        for k in ("total_mae", "pct_within_10", "team_mae", "bias"):
            scores[k][i] = s[k]
        scores["lambda"][i] = lam
        nfeat[i] = len(idx)
        if i % 1000 == 0:
            log(f"version {i}/{n} {vid} total MAE {s['total_mae']:.4f} ({time.time() - t0:.0f}s)")
    e1s = L.score_totals(inv, len(u), yv, e1v)
    ids = [v[0] for v in versions]
    tab = pd.DataFrame({"version": ids, "cutoff": [v[1] for v in versions], "families": ["+".join(v[2]) for v in versions],
                        "n_features": nfeat, **scores})
    best_i = int(np.argmin(scores["total_mae"]))          # first minimum = lowest enumeration index
    e1_wins = e1s["total_mae"] <= scores["total_mae"][best_i]          # ties go to E1
    order = np.argsort(scores["total_mae"], kind="stable")
    top = tab.iloc[order[:TOP_N]].reset_index(drop=True)
    best_team_i = int(np.argmin(scores["team_mae"]))
    topfreq = tab.iloc[order[:FREQ_N]]
    freq = {f: int(sum(f in fam.split("+") for fam in topfreq.families)) for f in L.FAMILY_NAMES}
    freq_cut = {c: int((topfreq.cutoff == c).sum()) for c in L.CUTOFFS}
    n_better = int((scores["total_mae"] < e1s["total_mae"]).sum())
    rec = {"train_seasons": list(L.TRAIN), "validation_seasons": list(L.VALID), "n_versions": n, "n_train_rows": int(len(train)),
           "n_val_rows": int(len(val)), "n_dropped_games_no_e1_train": n_bad_t, "n_dropped_games_no_e1_val": n_bad_v,
           "E1": e1s, "best_by_total": tab.iloc[best_i].to_dict(), "best_by_team_mae": tab.iloc[best_team_i].to_dict(),
           "e1_team_mae": e1s["team_mae"], "top20": top.to_dict("records"), "family_frequency_top100": freq,
           "cutoff_frequency_top100": freq_cut, "chosen": CHOSEN_E1 if e1_wins else ids[best_i], "stop": bool(e1_wins),
           "n_versions_better_than_E1": n_better, "rank_of_E1_by_total_mae": n_better + 1}
    frozen = None
    if not e1_wins:
        vid, cut, fams = versions[best_i]
        cols = L.version_columns(cut, fams)
        X = train[cols].values.astype(float)
        lam, cv = L.cv_lambda(X, rtr, train.season.values)             # direct #62 closed-form path (cross-checks the engine)
        mu, sd = L.standardise_fit(X)
        b0, b = L.ridge_fit((X - mu) / sd, rtr, lam)
        direct = e1v + L.ridge_predict(val[cols].values.astype(float), mu, sd, b0, b)
        eng = engines[cut][1].fit([p for f in fams for p in colpos[cut][f]])
        assert abs(lam - scores["lambda"][best_i]) < 1e-12, "engine / direct lambda disagree"
        assert np.allclose(direct, e1v + eng[4], atol=1e-7), "engine / direct predictions disagree"
        ridge = {"variant": vid, "features": cols, "lambda": lam, "cv_mse": {str(k): v for k, v in cv.items()}, "b0": float(b0),
                 "coef": b.tolist(), "mu": mu.tolist(), "sd": sd.tolist(), "n_rows": int(len(rtr)),
                 "train_seasons": sorted(set(train.season))}
        frozen = {"model": vid, "cutoff": cut, "families": list(fams),
                  "preregistration": "reports/output/nba_score_model_e3/PREREGISTRATION.md", "ridge": ridge,
                  "split": {"train": list(L.TRAIN), "validation": list(L.VALID), "test": list(L.TEST)},
                  "validation": {"E3": {k: float(tab.iloc[best_i][k]) for k in ("total_mae", "pct_within_10", "team_mae", "bias")},
                                 "E1": e1s}}
    return rec, frozen, tab


def main():
    study = L.STUDY
    gate(study)
    t0 = time.time()
    X = L.load_e1()
    S, RS, RT = X["S"], X["RS"], X["RT"]
    frozen_e1 = S.check_frozen_hash(L.E1_STUDY)
    e1_rec = json.loads((L.E1_STUDY / "results_test.json").read_text())["blocks"]["E1"]["pooled_test"]["total_mae"]
    assert abs(e1_rec - 14.79634) < 5e-6, ("E1 stored pooled 2022-26 total MAE is not 14.79634", e1_rec)
    seasons = list(L.TRAIN) + list(L.VALID)
    assert not set(seasons) & set(L.TEST)
    feat = pd.read_parquet(L.FEATURES_PARQUET)
    rows = RS.load_stage(seasons)                      # E1 features + team points for train + validation seasons only
    assert set(rows.season) == set(seasons)
    e1 = np.full(len(rows), np.nan)
    for s in seasons:
        m = (rows.season == s).values
        e1[m] = RT.season_predictions(frozen_e1, rows[m], s, ["E1"])["E1"]
        print(f"E1 predictions {s}: {m.sum()} rows, {np.isnan(e1[m]).sum()} NaN", flush=True)
    D = residual_frame(rows, feat, e1)
    train, val = D[D.season.isin(L.TRAIN)], D[D.season.isin(L.VALID)]
    rec, frozen, tab = select(train, val)
    assert all(set(s_) <= set(seasons) for _, s_ in RS.READ_LOG), "outcomes of a non-train/validation season were requested"
    rec["runtime_s"] = time.time() - t0
    rec["e1_frozen_sha256"] = L.sha256_file(L.E1_STUDY / "frozen.json")
    rec["features_sha256"] = L.sha256_file(L.FEATURES_PARQUET)
    rec["e1_stored_pooled_2022_26_total_mae"] = e1_rec
    rec["read_log"] = [[list(c), list(s_)] for c, s_ in RS.READ_LOG]
    (study / "results_select.json").write_text(json.dumps(rec, indent=1, default=str))
    tab.to_csv(study / "validation_all_versions.csv", index=False)
    pd.DataFrame(rec["top20"]).to_csv(study / "validation_top20.csv", index=False)
    print("E1 validation:", {k: round(v, 5) for k, v in rec["E1"].items()})
    print(pd.DataFrame(rec["top20"]).to_string(float_format=lambda x: f"{x:.4f}"))
    print("best by team MAE:", rec["best_by_team_mae"])
    print("family frequency in top 100:", rec["family_frequency_top100"], rec["cutoff_frequency_top100"])
    if frozen is None:
        (study / "STOP.md").write_text("# STOP - E1 is best on the 2023-24 validation season; no E3 is frozen and the test is not run.\n\n"
                                       + json.dumps(rec["E1"], indent=1) + "\n", encoding="utf-8")
        print("STOP: E1 is best on validation. No frozen_e3.json.")
        return
    frozen["e1_frozen_sha256"], frozen["features_sha256"] = rec["e1_frozen_sha256"], rec["features_sha256"]
    blob = json.dumps(frozen, indent=1, sort_keys=True).encode()
    (study / "frozen_e3.json").write_bytes(blob)
    h = L.sha256_bytes(blob)
    (study / "frozen_e3.sha256").write_text(h + "  frozen_e3.json\n")
    print("CHOSEN:", rec["chosen"], "| frozen_e3.json SHA-256:", h, f"| {rec['runtime_s']:.0f}s")


if __name__ == "__main__":
    main()
