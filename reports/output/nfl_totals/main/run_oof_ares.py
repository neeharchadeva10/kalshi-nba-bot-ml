"""Research log #66, Amendment 5: D-ONLY out-of-fold A-RES diagnostic.  For each D season s (2006-2014) the ridge is refitted on the other
8 D seasons (lambda re-chosen by leave-one-season-out inside them, z-scoring from those training rows, complete cases) and season s is
predicted.  Writes ../oof_ares_D.csv (D results for g in {0.5, 1, 1.5, 2}).  Outcomes: D seasons only (attach_outcomes refuses others)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nfl_lib as N  # noqa: E402
import part_a as A  # noqa: E402
from build_features import RES_FEATURES  # noqa: E402


def oof_predictions(fd: pd.DataFrame, lambdas=N.RIDGE_LAMBDAS) -> pd.Series:
    """fd: D rows with f_<feature> columns and `total`.  Returns out-of-fold predicted residuals indexed like fd (NaN for incomplete rows)."""
    X = fd[[f"f_{c}" for c in RES_FEATURES]].values.astype(float)
    y = (fd.total - fd.close).values.astype(float)
    ok = np.isfinite(X).all(axis=1) & np.isfinite(y)
    seasons = fd.season.values
    pred = np.full(len(fd), np.nan)
    for s in sorted(set(seasons)):
        tr, te = ok & (seasons != s), ok & (seasons == s)
        lam, _ = N.loso_lambda(X[tr], y[tr], seasons[tr], lambdas)
        pred[te] = N.ridge_predict(N.ridge_fit(X[tr], y[tr], lam), X[te])
    return pd.Series(pred, index=fd.index)


def main():
    g = N.load_games()
    gt = pd.read_parquet(N.MAIN_OUT / "game_table.parquet")
    gt = gt[gt.window == "D"]
    assert set(gt.season.unique()) <= set(N.D_SEASONS)
    fd = N.attach_outcomes(gt, g, N.D_SEASONS)
    fd["ares_pred"] = oof_predictions(fd)
    rows = [A.version_row(fd, f"A-RES|g{x:g}") for x in N.GRES_GRID]
    out = pd.DataFrame(rows).rename(columns={"version": "version_oof"})
    out.to_csv(N.STUDY / "oof_ares_D.csv", index=False)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
