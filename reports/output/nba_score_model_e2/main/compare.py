"""Research log #62, step 3: compare main injury features with the independent recomputation on the 200 sampled team-games.

Reads ONLY the checker's output file check/injury_features_check.parquet (never its code).  Writes AGREEMENT.md ('STATUS: PASS' /
'STATUS: FAIL', FEATURES_SHA256, CHECK_SHA256) and agreement.json.  PASS needs: the same 200 (game_id, team) rows, and the 8 model
features equal within 1e-9 (NaN in the same places).  Selection and the test refuse unless AGREEMENT.md says PASS for the current
injury_features.parquet.  Key = (game_id, team) with hoopR team codes as in sample_ids.csv; if the check file uses Kalshi codes they
are mapped to hoopR codes first.  Other shared columns (coverage, ids) are compared and listed but do not gate.
Run:  .venv/Scripts/python.exe reports/output/nba_score_model_e2/main/compare.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import e2_lib as E  # noqa: E402

CHECK = E.STUDY / "check" / "injury_features_check.parquet"
IDS_CSV = E.STUDY / "sample_ids.csv"
TOL = 1e-9
GATING = E.FEAT_COLS
INFO = ["n_listed_own", "n_unmatched_own", "n_out_rot_own", "n_q_rot_own", "n_back_own"]


def normalise_check_keys(B: pd.DataFrame, sample: pd.DataFrame) -> pd.DataFrame:
    B = B.copy()
    if "team" in B and not B.team.isin(set(sample.team)).any() and B.team.isin(set(E.KALSHI2HOOPR)).any():
        B["team"] = B.team.map(lambda x: E.KALSHI2HOOPR.get(x, x))
    return B


def compare_frames(A: pd.DataFrame, B: pd.DataFrame, sample: pd.DataFrame, tol=TOL, max_list=20) -> dict:
    """A = main rows, B = checker rows, sample = the sampled (game_id, team).  Pure (unit-tested)."""
    key = ["game_id", "team"]
    out = {"n_sample": int(len(sample)), "n_b": int(len(B)), "dup_b": int(B.duplicated(key).sum())}
    a = sample[key].merge(A, on=key, how="left", indicator=True)
    out["sample_missing_in_main"] = int((a._merge != "both").sum())
    a = a.drop(columns="_merge")
    bkeys = set(map(tuple, B[key].values.tolist()))
    skeys = set(map(tuple, sample[key].values.tolist()))
    out["only_in_check"] = sorted(bkeys - skeys)[:max_list]
    out["missing_in_check"] = sorted(skeys - bkeys)[:max_list]
    m = a.merge(B, on=key, how="inner", suffixes=("__a", "__b"))
    out["n_common"] = int(len(m))
    cols = {}
    for c in GATING + [c for c in INFO if c in B.columns]:
        if f"{c}__b" not in m:
            cols[c] = {"missing_column_in_check": True, "mismatch": len(m), "gating": c in GATING}
            continue
        x, y = m[f"{c}__a"].values.astype(float), m[f"{c}__b"].values.astype(float)
        one_nan = np.isnan(x) ^ np.isnan(y)
        d = np.where(np.isnan(x) & np.isnan(y), 0.0, np.abs(x - y))
        d = np.where(one_nan, np.inf, d)
        bad = d > tol
        cols[c] = {"mismatch": int(bad.sum()), "nan_pattern_mismatch": int(one_nan.sum()), "gating": c in GATING,
                   "max_abs_diff": float(np.nanmax(np.where(np.isinf(d), 1e300, d))) if len(d) else 0.0,
                   "examples": [{"game_id": int(g), "team": t, "a": float(p), "b": float(q)} for g, t, p, q in
                                list(zip(m.game_id.values[bad], m.team.values[bad], x[bad], y[bad]))[:max_list]]}
    out["columns"] = cols
    bad_cols = [k for k, v in cols.items() if v.get("gating") and v.get("mismatch", 0) > 0]
    out["mismatching_columns"] = bad_cols
    out["info_mismatching_columns"] = [k for k, v in cols.items() if not v.get("gating") and v.get("mismatch", 0) > 0]
    out["pass"] = bool(out["n_common"] == out["n_sample"] == out["n_b"] and not out["sample_missing_in_main"] and not bad_cols
                       and not out["only_in_check"] and not out["missing_in_check"] and out["dup_b"] == 0)
    return out


def write_md(res, path):
    L = ["# AGREEMENT - research #62, main injury features vs independent recomputation\n",
         f"STATUS: {'PASS' if res['pass'] else 'FAIL'}\n",
         f"FEATURES_SHA256: {res['sha_main']}", f"CHECK_SHA256: {res['sha_check']}\n",
         f"Tolerance {TOL:g} (absolute) on the 8 model features.  Sample rows {res['n_sample']}, checker rows {res['n_b']}, "
         f"matched {res['n_common']}; missing in main {res['sample_missing_in_main']}; missing in check {len(res['missing_in_check'])}; "
         f"only in check {len(res['only_in_check'])}; duplicates in check {res['dup_b']}.\n",
         "| column | gating | mismatches | NaN-pattern mismatches | max abs diff |", "|---|---|---|---|---|"]
    for k, v in res["columns"].items():
        if v.get("missing_column_in_check"):
            L.append(f"| {k} | {v['gating']} | column missing in check file | | |")
        else:
            L.append(f"| {k} | {v['gating']} | {v['mismatch']} | {v['nan_pattern_mismatch']} | {v['max_abs_diff']:.3g} |")
    for k, v in res["columns"].items():
        if v.get("examples"):
            L.append(f"\n### {k}: first mismatches (game_id, team, main, check)\n")
            L += [f"- {e['game_id']} {e['team']}: main={e['a']!r} check={e['b']!r}" for e in v["examples"][:15]]
    Path(path).write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    if not CHECK.exists():
        print("check/injury_features_check.parquet not found: comparison pending (no AGREEMENT.md written).")
        return 2
    sample = pd.read_csv(IDS_CSV)
    A, B = pd.read_parquet(E.FEATURES_PARQUET), normalise_check_keys(pd.read_parquet(CHECK), sample)
    res = compare_frames(A, B, sample)
    res["sha_main"], res["sha_check"] = E.sha256_file(E.FEATURES_PARQUET), E.sha256_file(CHECK)
    (E.STUDY / "agreement.json").write_text(json.dumps(res, indent=1, default=str))
    write_md(res, E.STUDY / "AGREEMENT.md")
    print("STATUS:", "PASS" if res["pass"] else "FAIL", "| mismatching columns:", res["mismatching_columns"])
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
