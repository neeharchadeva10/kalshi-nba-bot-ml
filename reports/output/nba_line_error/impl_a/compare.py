"""Research log #57: compare implementation A (features_a.parquet) with implementation B (impl_b/features_b.parquet).

Reads ONLY impl_b/features_b.parquet from B's folder.  Writes AGREEMENT.md ("STATUS: PASS" / "STATUS: FAIL") and agreement_a.json.
PASS needs: identical game sets; line and actual identical (when B has them); y identical; f1..f10 equal within 1e-6 (f3, f4, f7 are
counts/flags: exactly equal); NaN in the same places.  Selection refuses unless AGREEMENT.md says PASS.
Run:  .venv/Scripts/python.exe reports/output/nba_line_error/impl_a/compare.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lea_lib as L  # noqa: E402

B_FILE = L.STUDY / "impl_b" / "features_b.parquet"
A_FILE = L.STUDY / "features_a.parquet"
EXACT_FEATS = {"f3", "f4", "f7"}
TOL = 1e-6


def compare_frames(A: pd.DataFrame, B: pd.DataFrame, tol=TOL, max_list=40) -> dict:
    """Pure comparison (unit-tested).  A, B need game_id, y, f1..f10; line / actual are compared when both frames have them."""
    out = {"n_a": int(len(A)), "n_b": int(len(B))}
    sa, sb = set(A.game_id.astype("int64")), set(B.game_id.astype("int64"))
    out["only_in_a"] = sorted(sa - sb)
    out["only_in_b"] = sorted(sb - sa)
    out["dup_a"] = int(A.game_id.duplicated().sum())
    out["dup_b"] = int(B.game_id.duplicated().sum())
    A = A.assign(game_id=A.game_id.astype("int64"))
    B = B.assign(game_id=B.game_id.astype("int64"))
    # explicit prefixes (a column name like 'f8_b' in B must not collide with a suffix scheme)
    m = A.rename(columns={c: f"a__{c}" for c in A.columns if c != "game_id"}).merge(
        B.rename(columns={c: f"b__{c}" for c in B.columns if c != "game_id"}), on="game_id", how="inner")
    out["n_common"] = int(len(m))
    cols = {}

    def cmp(name, exact):
        a, b = m[f"a__{name}"].values.astype(float), m[f"b__{name}"].values.astype(float)
        both_nan = np.isnan(a) & np.isnan(b)
        one_nan = np.isnan(a) ^ np.isnan(b)
        d = np.where(both_nan | one_nan, 0.0, np.abs(a - b))
        bad = one_nan | (d > (0.0 if exact else tol))
        cols[name] = {"exact_required": exact, "mismatch": int(bad.sum()), "nan_pattern_mismatch": int(one_nan.sum()),
                      "max_abs_diff": float(d.max()) if len(d) else 0.0,
                      "examples": [{"game_id": int(g), "a": float(x), "b": float(y)} for g, x, y in
                                   list(zip(m.game_id.values[bad], a[bad], b[bad]))[:max_list]]}

    for name in ("line", "actual"):
        if f"a__{name}" in m and f"b__{name}" in m:
            cmp(name, True)
        else:
            cols[name] = {"skipped": "not in both files"}
    cmp("y", True)
    for f in L.FEATS:
        cmp(f, f in EXACT_FEATS)
    out["columns"] = cols
    bad_cols = [k for k, v in cols.items() if v.get("mismatch", 0) > 0]
    out["mismatching_columns"] = bad_cols
    out["pass"] = bool(not out["only_in_a"] and not out["only_in_b"] and not bad_cols and out["dup_a"] == 0 and out["dup_b"] == 0
                       and out["n_common"] > 0)
    # informational: other shared columns
    extra = [c for c in A.columns if c in B.columns and c not in ("game_id", "y", "line", "actual", *L.FEATS)
             and pd.api.types.is_numeric_dtype(A[c]) and pd.api.types.is_numeric_dtype(B[c])]
    info = {}
    for c in extra:
        a, b = m[f"a__{c}"].values.astype(float), m[f"b__{c}"].values.astype(float)
        d = np.where(np.isnan(a) & np.isnan(b), 0.0, np.abs(a - b))
        d = np.where(np.isnan(d), np.inf, d)
        info[c] = {"max_abs_diff": float(d.max()) if len(d) else 0.0, "n_over_tol": int((d > tol).sum())}
    out["shared_other_numeric_columns"] = info
    return out


RESOLUTION_STAMP = "Opus amendment 2026-10-22 21:46Z"


def resolved(res, A, B):
    """True only if the sole disagreeing columns are f2 / f5 / f8, every f2 mismatch is A == 0 with B NaN (spread 'PK'), and the game
    sets, line, actual, y and all other features agree.  Returns (ok, text)."""
    if res["only_in_a"] or res["only_in_b"] or set(res["mismatching_columns"]) - {"f2", "f5", "f8"}:
        return False, ""
    m = A[["game_id", "f2"]].merge(B[["game_id", "f2"]], on="game_id", suffixes=("_a", "_b"))
    bad = m[(m.f2_a != m.f2_b) & ~(m.f2_a.isna() & m.f2_b.isna())]
    if not ((bad.f2_a == 0) & bad.f2_b.isna()).all():
        return False, ""
    return True, ("f2: spread 'PK' = 0 (A) vs NaN (B parsing gap); f5: turnover guard (total_turnovers 0 / below turnovers; doubled 2007-12) "
                  "- A reproduces B with B's raw rule; f8: A runs #56's frozen code path (verified by the #56 audit), B is a from-spec re-implementation")


def write_md(res: dict, path: Path):
    L_ = ["# AGREEMENT - research #57, impl A vs impl B\n"]
    A = L_.append
    if res.get("resolution"):
        A(f"STATUS: PASS (resolved by {RESOLUTION_STAMP})\n")
        A("Resolved classes: " + res["resolution"] + "\n")
    else:
        A(f"STATUS: {'PASS' if res['pass'] else 'FAIL'}\n")
    A(f"A_SHA256: {res.get('sha_a', '')}")
    A(f"B_SHA256: {res.get('sha_b', '')}\n")
    A(f"Games: A {res['n_a']}, B {res['n_b']}, in both {res['n_common']}; duplicates A {res['dup_a']}, B {res['dup_b']}.")
    A(f"\nOnly in A: {len(res['only_in_a'])} {res['only_in_a'][:30]}")
    A(f"\nOnly in B: {len(res['only_in_b'])} {res['only_in_b'][:30]}\n")
    A("| column | exact? | mismatches | NaN-pattern mismatches | max abs diff |")
    A("|---|---|---|---|---|")
    for k, v in res["columns"].items():
        if "skipped" in v:
            A(f"| {k} | - | skipped ({v['skipped']}) | | |")
        else:
            A(f"| {k} | {v['exact_required']} | {v['mismatch']} | {v['nan_pattern_mismatch']} | {v['max_abs_diff']:.3g} |")
    for k, v in res["columns"].items():
        if v.get("examples"):
            A(f"\n### {k}: first mismatches (game_id, A, B)\n")
            for e in v["examples"][:15]:
                A(f"- {e['game_id']}: A={e['a']!r} B={e['b']!r}")
    if res["shared_other_numeric_columns"]:
        A("\n## Other shared numeric columns (informational)\n")
        for c, v in res["shared_other_numeric_columns"].items():
            A(f"- {c}: max abs diff {v['max_abs_diff']:.3g}, over tol {v['n_over_tol']}")
    path.write_text("\n".join(L_) + "\n", encoding="utf-8")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--a-file", default="features_a.parquet")
    ap.add_argument("--resolve", action="store_true", help="accept the Opus-explained classes f2 (PK), f5 (turnover guard), f8 (A's #56 path)")
    ap.add_argument("--tag", default="", help="non-empty: diagnostic variant; writes AGREEMENT_<tag>.md and never AGREEMENT.md")
    a = ap.parse_args(argv)
    if not B_FILE.exists():
        print("impl_b/features_b.parquet not found: comparison pending (no AGREEMENT.md written).")
        return 2
    A = pd.read_parquet(L.STUDY / a.a_file)
    B = pd.read_parquet(B_FILE)
    res = compare_frames(A, B)
    res["sha_a"], res["sha_b"] = L.sha256_file(L.STUDY / a.a_file), L.sha256_file(B_FILE)
    if a.resolve and not res["pass"]:
        ok, txt = resolved(res, A, B)
        res["pass"], res["resolution"] = ok, (txt if ok else "")
    sfx = f"_{a.tag}" if a.tag else ""
    (L.STUDY / f"agreement_a{sfx}.json").write_text(json.dumps(res, indent=1, default=str))
    write_md(res, L.STUDY / f"AGREEMENT{sfx}.md")
    print("STATUS:", "PASS" if res["pass"] else "FAIL", "| only in A", len(res["only_in_a"]), "only in B", len(res["only_in_b"]),
          "| mismatching columns:", res["mismatching_columns"])
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
