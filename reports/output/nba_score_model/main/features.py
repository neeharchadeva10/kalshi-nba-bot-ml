"""Research log #59, step 1: team_games_features.parquet (one row per (game_id, team), 2008-09 .. 2026-27 regular season) and
sample_ids.csv (300 team-games, seed 20261023).  Definitions: ../FEATURES.md.  Every feature uses only games dated strictly before the
game's ET date; a missing window (0 games) falls back to the S-1 full-season value.

Run:  .venv/Scripts/python.exe reports/output/nba_score_model/main/features.py"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sm_lib as S  # noqa: E402


def main():
    tg = S.load_all_team_games()
    F = S.build_features(tg)
    F.to_parquet(S.STUDY / "team_games_features.parquet", index=False)
    ids = S.sample_ids(F)
    ids.to_csv(S.STUDY / "sample_ids.csv", index=False)
    print("rows", len(F), "games", F.game_id.nunique(), "seasons", F.season.nunique())
    print(F.groupby("season").size().to_string())
    nan = F[S.FEATS].isna().sum()
    print("NaN per feature (non-zero only):", nan[nan > 0].to_dict())
    print("sample_ids:", len(ids), "seasons covered:", ids.season.nunique())
    print("parquet sha256:", S.sha256_file(S.STUDY / "team_games_features.parquet"))


if __name__ == "__main__":
    main()
