# Implementation A (research #57) - readings and notes
Files: lea_lib.py (shared), clean.py, fa_ext.py (#56 grid back to 2008-09), features.py, compare.py, run_select.py, run_validate.py,
run_test.py (never run), make_audit_hashes.py (prints only), repro_f8.py, test_line_error_a.py (36 tests).
Outputs in nba_line_error/: games_clean_a.parquet (FINAL: closing preferred incl. 2019-20 bubble, OddsData where no closing row =
"where present"), games_clean_a__strict/__fallback/__bubble/__both.parquet (variants), DATA_CHECKS.md, features_a.parquet, AGREEMENT.md.

Readings (amendment 2026-10-22 applied): f5, f9 = sum of the two deviations (sum - 2 x league); f6 = mean of two teams' last-5 means of
(actual - line) over lined games, 5 each; f5/f9/f10 need full in-season windows (10/10/10), only f1 falls back (last 30 calendar days of
last season, if < 50 games); pace = game pace (own+opp)/2 of 0.96 x (FGA - OREB + TOV + 0.44 FTA); 3PA rate = mean of per-game 3PA/FGA;
league averages = mean over games (pace) / team-games (3PA rate) before D. f4 counts teams whose latest earlier game was D-1.
f2 = |spread|, 'PK' = 0. Decile tails: bottom x <= q10, top x >= q90 (inclusive; discrete features can have tails > 10%).
Ridge: lambda grid 0.1..1e5, 5 folds by whole seasons, standardised inside each training fold; selection uses in-sample predictions.
TOV: hoopR 2007-08..2011-12 total_turnovers is doubled -> turnovers; else total_turnovers if finite and >= turnovers, else turnovers
(grid/#38 rule; B uses raw total_turnovers incl. 0 and < turnovers rows - 5+ rows differ, 2019-20/2020-21/2022-23/2024-25/2025-26).
Known hoopR defect: 2007-08 OREB/DREB columns wrong (OREB 8.4) -> 2008-09 f8 prior is distorted.
f8: #56 code (fa_lib/grid_lib), flat 7791, B index 0 = own; constants on S-1 with S-2 prior (2008-09: no prior; 2022-23 kept as #56).
Validation: 2020-21..2021-22, line_source == closing_lines only. Test: closing_lines games only.
