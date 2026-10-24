"""Research log #64, step 4: the TEST stage (2024-25 + 2025-26; the second look at these seasons, disclosed).
DO NOT RUN without the Opus leak audit.  Runs once.

REFUSES (before any outcome is loaded), in this order, unless
  1. frozen_e3.json's SHA-256 equals frozen_e3.sha256,
  2. AUDIT_OK is JSON {"sha256": {path: hash}} covering every file in e3_lib.AUDIT_FILES (this code, the E3 features, moves, player_core,
     E1's frozen.json and code, the injury inputs, the player-box / team-box files, the closing-line file) with matching hashes,
  3. AGREEMENT.md says PASS for the current e3_features.parquet,
  4. test_started.json and results_test.json do not exist.
Then: test_started.json is written FIRST; only afterwards is any test-season outcome loaded.

Pre-declared claims: paired game-bootstrap 95% CI (10,000 draws, seed 20261023) of (E3 - E1), pooled over 2024-26, for (a) game-total MAE
and (b) team-score MAE.  "E3 is more accurate than E1 on <metric>" only if the whole interval is below 0.  Reported regardless: within-10,
bias by season, and the sportsbook close as ONE reference row (never an input)."""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import e3_lib as L  # noqa: E402


def check_gates(study: Path, base: Path = HERE, files=L.AUDIT_FILES) -> dict:
    """All refusals, before anything is loaded."""
    study = Path(study)
    frozen = L.check_frozen_hash(study)
    L.check_audit_ok(study, base, files)
    L.check_agreement_pass(study)
    if (study / "test_started.json").exists():
        raise SystemExit("REFUSED: test_started.json exists; the test stage already started (runs once).")
    if (study / "results_test.json").exists():
        raise SystemExit("REFUSED: results_test.json exists; the test stage runs once.")
    return frozen


def start_test(study: Path):
    (Path(study) / "test_started.json").write_text(
        json.dumps({"started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}))


def per_game_diffs(D, p1, p2):
    """Per game: (E3 - E1) of the game-total absolute error and of the mean team absolute error.  p1 = E1, p2 = E3 (aligned team rows)."""
    m1 = L.game_metrics(D.game_id.values, D.home.values, D.pts.values, p1)
    m2 = L.game_metrics(D.game_id.values, D.home.values, D.pts.values, p2)
    d_total = np.abs(m2["err"]) - np.abs(m1["err"])
    u, inv = np.unique(D.game_id.values, return_inverse=True)
    assert (u == m1["game_ids"]).all()
    y = D.pts.values.astype(float)
    d_team = np.bincount(inv, np.abs(np.asarray(p2, float) - y) - np.abs(np.asarray(p1, float) - y)) / 2.0
    season = pd.Series(D.season.values, index=D.game_id.values)
    season = season[~season.index.duplicated()].reindex(u).values
    return d_total, d_team, season, m1, m2


def paired_blocks(D, p1, p2):
    d_total, d_team, season, _, _ = per_game_diffs(D, p1, p2)
    out = {}
    for name, d in (("total_mae", d_total), ("team_mae", d_team)):
        blk = {"pooled": dict(zip(("diff", "lo", "hi"), L.paired_boot_ci(d)), n_games=int(len(d))), "by_season": {}}
        for s in sorted(set(season)):
            k = season == s
            blk["by_season"][s] = dict(zip(("diff", "lo", "hi"), L.paired_boot_ci(d[k])), n_games=int(k.sum()))
        blk["ci_upper_below_zero"] = bool(blk["pooled"]["hi"] < 0)
        out[name] = blk
    return out


def main():
    study = L.STUDY
    frozen = check_gates(study)
    start_test(study)                                    # marker before any outcome
    X = L.load_e1()
    S, M, RS, RT = X["S"], X["M"], X["RS"], X["RT"]
    frozen_e1 = S.check_frozen_hash(L.E1_STUDY)
    seasons = list(L.TEST)
    feat = pd.read_parquet(L.FEATURES_PARQUET)
    cols = frozen["ridge"]["features"]
    rows = RS.load_stage(seasons)                        # test outcomes (pts) are loaded only now
    e1 = np.full(len(rows), np.nan)
    for s in seasons:
        m = (rows.season == s).values
        e1[m] = RT.season_predictions(frozen_e1, rows[m], s, ["E1"])["E1"]
    D = rows.copy()
    D["e1"] = e1
    D = D.merge(feat[["game_id", "team"] + cols], on=["game_id", "team"], how="left", validate="one_to_one")
    assert D[cols].notna().all().all()
    D["e3"] = D.e1 + L.predict_variant(frozen["ridge"], D)
    ok = np.isfinite(D.e1.values) & np.isfinite(D.e3.values)
    bad_games = set(D.game_id[~ok])
    D = D[~D.game_id.isin(bad_games)].reset_index(drop=True)          # whole games only
    res = {"model": frozen["model"], "n_games_dropped_nan": len(bad_games), "metrics": {}, "by_season": {}}
    for name, col in (("E1", "e1"), ("E3", "e3")):
        res["metrics"][name] = L.strip(L.game_metrics(D.game_id.values, D.home.values, D.pts.values, D[col].values))
        for s in seasons:
            d = D[D.season == s]
            res["by_season"].setdefault(name, {})[s] = L.strip(L.game_metrics(d.game_id.values, d.home.values, d.pts.values, d[col].values))
    res["paired_bootstrap_E3_minus_E1"] = paired_blocks(D, D.e1.values, D.e3.values)
    res["draws"], res["seed"] = L.BOOT_N, L.BOOT_SEED
    res["E3_more_accurate_than_E1_total"] = res["paired_bootstrap_E3_minus_E1"]["total_mae"]["ci_upper_below_zero"]
    res["E3_more_accurate_than_E1_team"] = res["paired_bootstrap_E3_minus_E1"]["team_mae"]["ci_upper_below_zero"]
    close = M.fa.load_closing()
    m3 = L.game_metrics(D.game_id.values, D.home.values, D.pts.values, D.e3.values)
    m1 = L.game_metrics(D.game_id.values, D.home.values, D.pts.values, D.e1.values)
    line = pd.Series(m3["game_ids"]).map(close).values.astype(float)
    has = np.isfinite(line)
    res["sportsbook_close_reference"] = {
        "n_games_with_close": int(has.sum()),
        "close_total_mae": float(np.abs(line[has] - m3["tot_act"][has]).mean()) if has.any() else None,
        "E1_total_mae_same_games": float(np.abs(m1["err"][has]).mean()) if has.any() else None,
        "E3_total_mae_same_games": float(np.abs(m3["err"][has]).mean()) if has.any() else None}
    (study / "results_test.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res["metrics"], indent=1))
    print(json.dumps(res["paired_bootstrap_E3_minus_E1"], indent=1))
    print(json.dumps(res["sportsbook_close_reference"], indent=1))
    print("E3 more accurate than E1 (total):", res["E3_more_accurate_than_E1_total"], "(team):", res["E3_more_accurate_than_E1_team"])


if __name__ == "__main__":
    main()
