"""Research log #64, step 2: compare main E3 features with the independent recomputation on the 200 sampled team-games.

Reads ONLY the checker's output file check/e3_features_check.parquet (never its code).  Writes AGREEMENT.md ('STATUS: PASS' /
'STATUS: FAIL', FEATURES_SHA256, CHECK_SHA256) and agreement.json.  PASS needs: the same 200 (game_id, team) rows, and every one of the
68 model feature columns (17 bases x own/opp x _c60/_c20) present in the check file and equal within 1e-9 (NaN in the same places).
A missing column or any mismatch FAILS; the report lists every mismatching class with examples.  No selection is allowed after a FAIL.
Key = (game_id, team) with hoopR team codes as in sample_ids.csv; Kalshi codes in the check file are mapped to hoopR codes first.
Other shared columns (coverage) are compared and listed but do not gate.
Run:  .venv/Scripts/python.exe reports/output/nba_score_model_e3/main/compare.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import e3_lib as L  # noqa: E402

CHECK = L.STUDY / "check" / "e3_features_check.parquet"
IDS_CSV = L.STUDY / "sample_ids.csv"
TOL = 1e-9
GATING = [f"{b}_{s}_{c}" for c in L.CUTOFFS for b in L.BASES for s in ("own", "opp")]
INFO = [f"{k}_{c}" for c in L.CUTOFFS for k in ("n_listed", "n_unmatched", "n_out_rot", "n_q_rot", "n_age_known", "n_age_unknown")] + \
       [f"{k}_{c}" for c in L.CUTOFFS for k in ("report_found",)]


def normalise_check_keys(B: pd.DataFrame, sample: pd.DataFrame) -> pd.DataFrame:
    B = B.copy()
    if "team" in B and not B.team.isin(set(sample.team)).any() and B.team.isin(set(L.E2.KALSHI2HOOPR)).any():
        B["team"] = B.team.map(lambda x: L.E2.KALSHI2HOOPR.get(x, x))
    return B


def compare_frames(A: pd.DataFrame, B: pd.DataFrame, sample: pd.DataFrame, tol=TOL, max_list=20, gating=GATING, info=INFO) -> dict:
    key = ["game_id", "team"]
    out = {"n_sample": int(len(sample)), "n_b": int(len(B)), "dup_b": int(B.duplicated(key).sum())}
    a = sample[key].merge(A, on=key, how="left", indicator=True)
    out["sample_missing_in_main"] = int((a._merge != "both").sum())
    a = a.drop(columns="_merge")
    bkeys, skeys = set(map(tuple, B[key].values.tolist())), set(map(tuple, sample[key].values.tolist()))
    out["only_in_check"] = sorted(bkeys - skeys)[:max_list]
    out["missing_in_check"] = sorted(skeys - bkeys)[:max_list]
    m = a.merge(B, on=key, how="inner", suffixes=("__a", "__b"))
    out["n_common"] = int(len(m))
    cols = {}
    for c in list(gating) + [c for c in info if c in B.columns]:
        isg = c in gating
        if f"{c}__b" not in m and c not in B.columns:
            cols[c] = {"missing_column_in_check": True, "mismatch": len(m), "gating": isg}
            continue
        ca, cb = (f"{c}__a", f"{c}__b") if f"{c}__b" in m else (c, c)
        if ca not in m:
            ca = c
        x, y = m[ca].values.astype(float), m[cb].values.astype(float)
        one_nan = np.isnan(x) ^ np.isnan(y)
        d = np.where(np.isnan(x) & np.isnan(y), 0.0, np.abs(x - y))
        d = np.where(one_nan, np.inf, d)
        bad = d > tol
        cols[c] = {"mismatch": int(bad.sum()), "nan_pattern_mismatch": int(one_nan.sum()), "gating": isg,
                   "max_abs_diff": float(np.nanmax(np.where(np.isinf(d), 1e300, d))) if len(d) else 0.0,
                   "examples": [{"game_id": int(g), "team": t, "a": float(p), "b": float(q)} for g, t, p, q in
                                list(zip(m.game_id.values[bad], m.team.values[bad], x[bad], y[bad]))[:max_list]]}
    out["columns"] = cols
    out["mismatching_columns"] = [k for k, v in cols.items() if v.get("gating") and v.get("mismatch", 0) > 0]
    out["info_mismatching_columns"] = [k for k, v in cols.items() if not v.get("gating") and v.get("mismatch", 0) > 0]
    out["pass"] = bool(out["n_common"] == out["n_sample"] == out["n_b"] and not out["sample_missing_in_main"]
                       and not out["mismatching_columns"] and not out["only_in_check"] and not out["missing_in_check"]
                       and out["dup_b"] == 0)
    return out


def write_md(res, path):
    L_ = ["# AGREEMENT - research #64, main E3 features vs independent recomputation\n",
          f"STATUS: {'PASS' if res['pass'] else 'FAIL'}\n",
          f"FEATURES_SHA256: {res['sha_main']}", f"CHECK_SHA256: {res['sha_check']}\n",
          f"Tolerance {TOL:g} (absolute) on the {len(GATING)} model feature columns.  Sample rows {res['n_sample']}, checker rows "
          f"{res['n_b']}, matched {res['n_common']}; missing in main {res['sample_missing_in_main']}; missing in check "
          f"{len(res['missing_in_check'])}; only in check {len(res['only_in_check'])}; duplicates in check {res['dup_b']}.\n",
          f"Gating columns mismatching: {res['mismatching_columns'] or 'none'}\n",
          "| column | gating | mismatches | NaN-pattern mismatches | max abs diff |", "|---|---|---|---|---|"]
    for k, v in res["columns"].items():
        if v.get("missing_column_in_check"):
            L_.append(f"| {k} | {v['gating']} | column missing in check file | | |")
        else:
            L_.append(f"| {k} | {v['gating']} | {v['mismatch']} | {v['nan_pattern_mismatch']} | {v['max_abs_diff']:.3g} |")
    for k, v in res["columns"].items():
        if v.get("examples"):
            L_.append(f"\n### {k}: first mismatches (game_id, team, main, check)\n")
            L_ += [f"- {e['game_id']} {e['team']}: main={e['a']!r} check={e['b']!r}" for e in v["examples"][:15]]
    Path(path).write_text("\n".join(L_) + "\n", encoding="utf-8")


def main():
    if not CHECK.exists():
        print("check/e3_features_check.parquet not found: comparison pending (no AGREEMENT.md written).")
        return 2
    sample = pd.read_csv(IDS_CSV)
    A, B = pd.read_parquet(L.FEATURES_PARQUET), normalise_check_keys(pd.read_parquet(CHECK), sample)
    res = compare_frames(A, B, sample)
    res["sha_main"], res["sha_check"] = L.sha256_file(L.FEATURES_PARQUET), L.sha256_file(CHECK)
    (L.STUDY / "agreement.json").write_text(json.dumps(res, indent=1, default=str))
    write_md(res, L.STUDY / "AGREEMENT.md")
    print("STATUS:", "PASS" if res["pass"] else "FAIL", "| mismatching columns:", res["mismatching_columns"])
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
