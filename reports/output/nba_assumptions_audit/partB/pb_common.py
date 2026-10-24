"""Part B (judgment calls J1-J11) shared loaders.  DESCRIPTIVE only; read-only reuse of #59 (E1) and #62 (E2) code; no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
AUDIT = HERE.parent
OUT = AUDIT.parent
ROOT = OUT.parents[1]
CACHE = HERE / "_cache"
CACHE.mkdir(exist_ok=True)
for p in (OUT / "nba_score_model_e2" / "main",):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
import e2_lib as E2  # noqa: E402  (module-level constants are patched only inside this process; the file is never modified)

STUDY59 = OUT / "nba_score_model"
REPRO_TEST_MAE = 14.79634
SEASONS = ("2021-22", "2022-23", "2023-24", "2024-25", "2025-26")


def load_e1(seasons=SEASONS, use_cache=True):
    """Team-row table with E1 prediction in column pred (frozen #59 pipeline); cached to partB/_cache."""
    f = CACHE / "e1_2021_26.parquet"
    if use_cache and f.exists():
        D = pd.read_parquet(f)
        if set(seasons) <= set(D.season):
            return D[D.season.isin(seasons)].reset_index(drop=True)
    X = E2.load_e1()
    S, RS, RT = X["S"], X["RS"], X["RT"]
    frozen = json.loads((STUDY59 / "frozen.json").read_text())
    D = RS.load_stage(list(seasons))
    pred = np.full(len(D), np.nan)
    for s in seasons:
        m = (D.season == s).values
        pred[m] = RT.season_predictions(frozen, D[m], s, ["E1"])["E1"]
    D["pred"] = pred
    D.to_parquet(f)
    return D


def game_err(D, col="pred"):
    """-> DataFrame per game: game_id, season, tot_pred, tot_act, err = pred - actual."""
    m = E2.game_metrics(D.game_id.values, D.home.values, D.pts.values, D[col].values)
    season = D.drop_duplicates("game_id").set_index("game_id").season
    out = pd.DataFrame({"game_id": m["game_ids"], "tot_pred": m["tot_pred"], "tot_act": m["tot_act"], "err": m["err"]})
    out["season"] = out.game_id.map(season).values
    return out
