"""Research log #64, step 1: E3 features -> reports/output/nba_score_model_e3/e3_features.parquet (+ sample_ids.csv).

One row per (game_id, team), 2021-22 .. 2025-26 regular seasons, all 12 families (own + opp) for both report cutoffs (_c60, _c20).
No outcome (points) is read here.  Definitions: FEATURES_E3.md.
Run:  .venv/Scripts/python.exe reports/output/nba_score_model_e3/main/features.py"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import e3_lib as L  # noqa: E402


def coverage(F):
    rows = {}
    for cut in L.CUTOFFS:
        g = F.groupby("season")
        rows[cut] = g.agg(team_games=("game_id", "size"), report_found=(f"report_found_{cut}", "mean"), nys=(f"nys_{cut}", "mean"),
                          listed=(f"n_listed_{cut}", "sum"), unmatched=(f"n_unmatched_{cut}", "sum"),
                          out_rot=(f"n_out_rot_{cut}", "sum"), q_rot=(f"n_q_rot_{cut}", "sum"),
                          age_known=(f"n_age_known_{cut}", "sum"), age_unknown=(f"n_age_unknown_{cut}", "sum"))
        rows[cut]["unmatched_share"] = rows[cut].unmatched / rows[cut].listed.clip(lower=1)
        rows[cut]["age_known_share"] = rows[cut].age_known / (rows[cut].age_known + rows[cut].age_unknown).clip(lower=1)
    return rows


def main():
    games = L.E2.load_games()
    box = L.load_box3()
    ages, info = L.load_agebook()
    moves = L.load_moves()
    reports = L.E2.Reports()
    F = L.build_features(games, box, reports, ages, moves)
    assert F.duplicated(["game_id", "team"]).sum() == 0
    assert len(F) == len(games), (len(F), len(games))
    cols = L.all_feature_columns()
    assert F[cols].notna().all().all(), "NaN in a model feature"
    F.to_parquet(L.FEATURES_PARQUET, index=False)
    s = L.sample_ids(F)
    s.to_csv(L.STUDY / "sample_ids.csv", index=False)
    print(f"rows {len(F)}; sample {len(s)}; sha256 {L.sha256_file(L.FEATURES_PARQUET)}; dob {info}; age out-of-range lookups {ages.n_out_of_range}")
    # DOB coverage over every athlete who played in the box and sanity of ages at 2026-01-01 for current players
    aids = set(box.hist3)
    known = sum(1 for a in aids if a in ages.dob)
    print(f"athletes in box (played) {len(aids)}; with DOB {known} ({known / len(aids):.4f})")
    for cut, c in coverage(F).items():
        print(f"--- cutoff {cut}")
        print(c.to_string(float_format=lambda x: f"{x:.4f}"))
    allc = coverage(F)
    for cut, c in allc.items():
        k, u = c.age_known.sum(), c.age_unknown.sum()
        print(f"{cut}: ages known among missing rotation players {k}/{k + u} = {k / max(k + u, 1):.4f}; "
              f"report found {F[f'report_found_{cut}'].mean():.4f}")


if __name__ == "__main__":
    main()
