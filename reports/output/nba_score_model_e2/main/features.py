"""Research log #62, step 1: injury features -> reports/output/nba_score_model_e2/injury_features.parquet (+ sample_ids.csv).

One row per (game_id, team) for the 2021-22 .. 2025-26 regular seasons (2026-27 is not built: no player_box_2027 on disk).
No outcome (points) is read here.  Definitions: FEATURES_E2.md.
Run:  .venv/Scripts/python.exe reports/output/nba_score_model_e2/main/features.py"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import e2_lib as E  # noqa: E402


def coverage(F):
    g = F.groupby("season")
    out = g.agg(team_games=("game_id", "size"), report_found=("report_found", "mean"), nys=("nys", "mean"),
                box_found=("box_found", "mean"), listed=("n_listed_own", "sum"), unmatched=("n_unmatched_own", "sum"),
                out_rot=("n_out_rot_own", "sum"), q_rot=("n_q_rot_own", "sum"), back=("n_back_own", "sum"),
                out_pts_mean=("out_pts_own", "mean"), q_pts_mean=("q_pts_own", "mean"), back_pts_mean=("back_pts_own", "mean"))
    out["unmatched_share"] = out.unmatched / out.listed.clip(lower=1)
    return out


def main():
    games = E.load_games()
    box = E.load_box()
    reports = E.Reports()
    F = E.build_injury_features(games, box, reports)
    assert F.duplicated(["game_id", "team"]).sum() == 0
    assert len(F) == len(games), (len(F), len(games))
    F.to_parquet(E.FEATURES_PARQUET, index=False)
    s = E.sample_ids(F)
    s.to_csv(E.STUDY / "sample_ids.csv", index=False)
    print(f"rows {len(F)}; sample {len(s)}; sha256 {E.sha256_file(E.FEATURES_PARQUET)}")
    print(coverage(F).to_string(float_format=lambda x: f"{x:.4f}"))


if __name__ == "__main__":
    main()
