"""Research log #62, step 5: the TEST stage (2024-25 + 2025-26).  DO NOT RUN without the Opus leak audit.  Runs once.

REFUSES (before any outcome is loaded), in this order, unless
  1. frozen_e2.json's SHA-256 equals frozen_e2.sha256,
  2. AUDIT_OK is JSON {"sha256": {path: hash}} covering every file in e2_lib.AUDIT_FILES (this code, the injury features, E1's frozen.json
     and code, the injury inputs, the player-box / team-box files, the closing-line file) with matching hashes,
  3. AGREEMENT.md says PASS for the current injury_features.parquet,
  4. test_started.json and results_test.json do not exist.
Then: test_started.json is written FIRST; only afterwards is any test-season outcome loaded.

Pre-declared claims: paired game-bootstrap 95% CI (10,000 draws, seed 20261023) of (E2 - E1) total MAE pooled over 2024-26; "E2 is more
accurate than E1" only if the whole interval is below 0.  Reported regardless: within-10, team MAE, bias by season; the split by
key-players-out (0 / 1 / 2+ = players with >= 28 mpg over their last 10 games played, taken before the game, who did not play,
checked in the box AFTER the game - this split only); the sportsbook close as ONE reference row (never an input)."""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import e2_lib as E  # noqa: E402


def check_gates(study: Path, base: Path = HERE, files=E.AUDIT_FILES) -> dict:
    """All refusals, before anything is loaded."""
    study = Path(study)
    frozen = E.check_frozen_hash(study)
    E.check_audit_ok(study, base, files)
    E.check_agreement_pass(study)
    if (study / "test_started.json").exists():
        raise SystemExit("REFUSED: test_started.json exists; the test stage already started (runs once).")
    if (study / "results_test.json").exists():
        raise SystemExit("REFUSED: results_test.json exists; the test stage runs once.")
    return frozen


def start_test(study: Path):
    (Path(study) / "test_started.json").write_text(
        json.dumps({"started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}))


def key_out_counts(D: pd.DataFrame, box: E.Box) -> pd.Series:
    """game_id -> number of key players (>= 28 mpg prior-10, both teams) who did not play the game (box DNP or absent).  After-the-fact
    box: used for the reported split only, never as an input."""
    cnt = {}
    for r in D.itertuples(index=False):
        n = 0
        for tok in (r.key_aids_own or "").split(";"):
            if tok and int(tok) not in box.played_in.get((r.team_k, int(r.game_id)), ()):
                n += 1
        cnt[int(r.game_id)] = cnt.get(int(r.game_id), 0) + n
    return pd.Series(cnt)


def paired_block(D, p1, p2):
    """Paired game bootstrap of (total MAE of p2 - total MAE of p1) (p2 = E2, p1 = E1), pooled and per season."""
    m1 = E.game_metrics(D.game_id.values, D.home.values, D.pts.values, p1)
    m2 = E.game_metrics(D.game_id.values, D.home.values, D.pts.values, p2)
    d = np.abs(m2["err"]) - np.abs(m1["err"])
    season = pd.Series(D.season.values, index=D.game_id.values)
    season = season[~season.index.duplicated()].reindex(m1["game_ids"]).values
    out = {"pooled": dict(zip(("diff", "lo", "hi"), E.paired_boot_ci(d)), n_games=int(len(d))), "by_season": {}}
    for s in sorted(set(season)):
        k = season == s
        out["by_season"][s] = dict(zip(("diff", "lo", "hi"), E.paired_boot_ci(d[k])), n_games=int(k.sum()))
    out["ci_upper_below_zero"] = bool(out["pooled"]["hi"] < 0)
    return out


def split_by_key_out(D, p1, p2, nkey):
    m1 = E.game_metrics(D.game_id.values, D.home.values, D.pts.values, p1)
    m2 = E.game_metrics(D.game_id.values, D.home.values, D.pts.values, p2)
    k = nkey.reindex(m1["game_ids"]).fillna(0).astype(int).values
    grp = np.where(k >= 2, "2+", k.astype(str))
    out = {}
    for g in ("0", "1", "2+"):
        s = grp == g
        if s.any():
            e1, e2 = m1["err"][s], m2["err"][s]
            out[g] = {"n_games": int(s.sum()), "E1_total_mae": float(np.abs(e1).mean()), "E2_total_mae": float(np.abs(e2).mean()),
                      "diff_E2_minus_E1": float(np.abs(e2).mean() - np.abs(e1).mean()),
                      "E1_bias": float(e1.mean()), "E2_bias": float(e2.mean())}
    return out


def main():
    study = E.STUDY
    frozen = check_gates(study)
    start_test(study)                                    # marker before any outcome
    X = E.load_e1()
    S, M, RS, RT = X["S"], X["M"], X["RS"], X["RT"]
    frozen_e1 = S.check_frozen_hash(E.E1_STUDY)
    seasons = list(E.TEST)
    inj = pd.read_parquet(E.FEATURES_PARQUET)
    rows = RS.load_stage(seasons)                        # test outcomes (pts) are loaded only now
    e1 = np.full(len(rows), np.nan)
    for s in seasons:
        m = (rows.season == s).values
        e1[m] = RT.season_predictions(frozen_e1, rows[m], s, ["E1"])["E1"]
    D = rows.copy()
    D["e1"] = e1
    D = D.merge(inj[["game_id", "team", "team_k", "key_aids_own"] + E.FEAT_COLS], on=["game_id", "team"], how="left", validate="one_to_one")
    assert D[E.FEAT_COLS].notna().all().all()
    D["e2"] = D.e1 + E.predict_variant(frozen["ridge"], D)
    ok = np.isfinite(D.e1.values) & np.isfinite(D.e2.values)
    n_drop = int((~ok).sum())
    D = D[ok].reset_index(drop=True)
    res = {"model": frozen["model"], "n_rows_dropped_nan": n_drop, "metrics": {}, "by_season": {}}
    for name, col in (("E1", "e1"), ("E2", "e2")):
        res["metrics"][name] = E.strip(E.game_metrics(D.game_id.values, D.home.values, D.pts.values, D[col].values))
        for s in seasons:
            d = D[D.season == s]
            res["by_season"].setdefault(name, {})[s] = E.strip(E.game_metrics(d.game_id.values, d.home.values, d.pts.values, d[col].values))
    res["paired_bootstrap_E2_minus_E1"] = paired_block(D, D.e1.values, D.e2.values)
    res["draws"], res["seed"] = E.BOOT_N, E.BOOT_SEED
    res["E2_more_accurate_than_E1"] = res["paired_bootstrap_E2_minus_E1"]["ci_upper_below_zero"]
    box = E.load_box(range(2025, 2027))
    nkey = key_out_counts(D, box)
    res["split_by_key_players_out"] = split_by_key_out(D, D.e1.values, D.e2.values, nkey)
    close = M.fa.load_closing()
    met = E.game_metrics(D.game_id.values, D.home.values, D.pts.values, D.e2.values)
    met1 = E.game_metrics(D.game_id.values, D.home.values, D.pts.values, D.e1.values)
    line = pd.Series(met["game_ids"]).map(close).values.astype(float)
    has = np.isfinite(line)
    res["sportsbook_close_reference"] = {
        "n_games_with_close": int(has.sum()),
        "close_total_mae": float(np.abs(line[has] - met["tot_act"][has]).mean()) if has.any() else None,
        "E1_total_mae_same_games": float(np.abs(met1["err"][has]).mean()) if has.any() else None,
        "E2_total_mae_same_games": float(np.abs(met["err"][has]).mean()) if has.any() else None}
    (study / "results_test.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res["metrics"][k] for k in res["metrics"]}, indent=1))
    print(json.dumps(res["paired_bootstrap_E2_minus_E1"], indent=1))
    print(json.dumps(res["split_by_key_players_out"], indent=1))
    print(json.dumps(res["sportsbook_close_reference"], indent=1))
    print("E2_more_accurate_than_E1:", res["E2_more_accurate_than_E1"])


if __name__ == "__main__":
    main()
