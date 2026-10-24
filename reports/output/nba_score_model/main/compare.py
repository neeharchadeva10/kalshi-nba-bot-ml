"""Research log #59, step 2: compare main features with the independent recomputation (check/features_check.parquet) on sample_ids.csv.

Reads ONLY the checker's output file (never its code).  Writes AGREEMENT.md ('STATUS: PASS' / 'STATUS: FAIL', plus FEATURES_SHA256 and
CHECK_SHA256) and agreement.json.  PASS needs: the same 300 (game_id, team) rows; identifiers (season, date, opp) identical; every feature and
the target equal within 1e-9; NaN in the same places.  Selection refuses unless AGREEMENT.md says PASS.
Run:  .venv/Scripts/python.exe reports/output/nba_score_model/main/compare.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sm_lib as S  # noqa: E402

CHECK = S.STUDY / "check" / "features_check.parquet"
MAIN = S.STUDY / "team_games_features.parquet"
IDS_CSV = S.STUDY / "sample_ids.csv"
TOL = 1e-9
NUMERIC = S.FEATS + [S.TARGET]


def compare_frames(A: pd.DataFrame, B: pd.DataFrame, sample: pd.DataFrame, tol=TOL, max_list=20) -> dict:
    """A = main rows, B = checker rows, sample = the 300 sampled (game_id, team).  Pure (unit-tested)."""
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
    for c in ("season", "opp", "date"):
        if f"{c}__b" not in m:
            cols[c] = {"skipped": "not in check file"}
            continue
        bad = (m[f"{c}__a"].astype(str).values != m[f"{c}__b"].astype(str).values)
        cols[c] = {"mismatch": int(bad.sum()), "nan_pattern_mismatch": 0, "max_abs_diff": 0.0,
                   "examples": [{"game_id": int(g), "team": t, "a": str(x), "b": str(y)} for g, t, x, y in
                                list(zip(m.game_id.values[bad], m.team.values[bad], m[f"{c}__a"].values[bad], m[f"{c}__b"].values[bad]))[:max_list]]}
    for c in NUMERIC:
        if f"{c}__b" not in m:
            cols[c] = {"missing_column_in_check": True, "mismatch": len(m)}
            continue
        x, y = m[f"{c}__a"].values.astype(float), m[f"{c}__b"].values.astype(float)
        one_nan = np.isnan(x) ^ np.isnan(y)
        d = np.where(np.isnan(x) & np.isnan(y), 0.0, np.abs(x - y))
        d = np.where(one_nan, np.inf, d)
        bad = d > tol
        cols[c] = {"mismatch": int(bad.sum()), "nan_pattern_mismatch": int(one_nan.sum()),
                   "max_abs_diff": float(np.nanmax(np.where(np.isinf(d), 1e300, d))) if len(d) else 0.0,
                   "examples": [{"game_id": int(g), "team": t, "a": float(p), "b": float(q)} for g, t, p, q in
                                list(zip(m.game_id.values[bad], m.team.values[bad], x[bad], y[bad]))[:max_list]]}
    out["columns"] = cols
    bad_cols = [k for k, v in cols.items() if v.get("mismatch", 0) > 0]
    out["mismatching_columns"] = bad_cols
    out["extra_columns_in_check"] = [c for c in B.columns if c not in S.FEATURE_COLUMNS]
    out["pass"] = bool(out["n_common"] == out["n_sample"] == out["n_b"] and not out["sample_missing_in_main"] and not bad_cols
                       and not out["only_in_check"] and not out["missing_in_check"] and out["dup_b"] == 0)
    return out


def write_md(res, path):
    L = ["# AGREEMENT - research #59, main features vs independent recomputation\n",
         f"STATUS: {'PASS' if res['pass'] else 'FAIL'}\n",
         f"FEATURES_SHA256: {res['sha_main']}", f"CHECK_SHA256: {res['sha_check']}\n",
         f"Tolerance {TOL:g} (absolute) on every feature and the target.  Sample rows {res['n_sample']}, checker rows {res['n_b']}, "
         f"matched {res['n_common']}; missing in main {res['sample_missing_in_main']}; missing in check {len(res['missing_in_check'])}; "
         f"only in check {len(res['only_in_check'])}; duplicates in check {res['dup_b']}.\n",
         "| column | mismatches | NaN-pattern mismatches | max abs diff |", "|---|---|---|---|"]
    for k, v in res["columns"].items():
        if "skipped" in v:
            L.append(f"| {k} | skipped | | |")
        elif v.get("missing_column_in_check"):
            L.append(f"| {k} | column missing in check file | | |")
        else:
            L.append(f"| {k} | {v['mismatch']} | {v['nan_pattern_mismatch']} | {v['max_abs_diff']:.3g} |")
    for k, v in res["columns"].items():
        if v.get("examples"):
            L.append(f"\n### {k}: first mismatches (game_id, team, main, check)\n")
            L += [f"- {e['game_id']} {e['team']}: main={e['a']!r} check={e['b']!r}" for e in v["examples"][:15]]
    if res["extra_columns_in_check"]:
        L.append(f"\nExtra columns in the check file (ignored): {res['extra_columns_in_check']}")
    Path(path).write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    if not CHECK.exists():
        print("check/features_check.parquet not found: comparison pending (no AGREEMENT.md written).")
        return 2
    A, B, sample = pd.read_parquet(MAIN), pd.read_parquet(CHECK), pd.read_csv(IDS_CSV)
    A["date"] = pd.to_datetime(A.date)
    res = compare_frames(A, B, sample)
    res["sha_main"], res["sha_check"] = S.sha256_file(MAIN), S.sha256_file(CHECK)
    (S.STUDY / "agreement.json").write_text(json.dumps(res, indent=1, default=str))
    write_md(res, S.STUDY / "AGREEMENT.md")
    print("STATUS:", "PASS" if res["pass"] else "FAIL", "| mismatching columns:", res["mismatching_columns"])
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
