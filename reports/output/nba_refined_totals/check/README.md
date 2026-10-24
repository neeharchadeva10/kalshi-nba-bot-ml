# Independent check of #65 (nba_refined_totals)

Code: `check.py` (run `check.py all`, about 30 s incl. report loading; F42 cached in out/f42_cache.parquet). Tests: `test_check.py` (13).
Nothing read from main/ or main_out/. Shared pieces (allowed): the #62 checker `nba_score_model_e2/check/check_features.py`
(PDF CreationDate, report selection that lists the game, 72 h look-back, exact name matching, box loading; its cache redirected
to out/), e3_features.parquet c60 columns, frozen_e3.json, E1 per-team file, fa_lib + #42 winner_frozen spec for F42.
Outputs: out/b_check.csv (200), flags_check.csv (200), lean_check.csv (150), versions_check.csv (10).

## Readings chosen
Part B (b_check.csv)
1. Box rows: regular season, NBA 30 franchises only (All-Star/exhibition excluded), minutes NaN = 0; "played" = minutes > 0 and not did_not_play.
2. Current team = team of his latest box row (played OR DNP row) of THIS season strictly before g's tip (no fallback to last season). Rows in g itself never used.
3. X's games = X's regular-season games of this season with tip < g's tip; need >= 5; window = last 10 (fewer if < 10).
4. mpg and fresh_ppg = sum over window games of his minutes/points in rows with team == X, divided by the number of window games (10, or n if 5..9). A row for another team (he was an opponent in X's game) counts 0.
5. Fresh = a row for X with minutes > 0 in X's last 3 games before g. Candidate needs mpg >= M (inclusive).
6. Report flavor: #62 report (latest CreationDate <= tip-60 min that lists the game, 72 h look-back; team with no rows or no report = nobody listed, S = 0, not NaN). Listed OUT/DOUBTFUL names matched by the #62 exact-name rule (pool: latest-team incl. last-season fallback); a matched athlete is absent only if he is also a Part B candidate. Report flavor is NaN for 2020-21 (no reports). Games before the first report (2021-22 opening days) would be S=0.
7. Box flavor: absent = no row for X with minutes > 0 in g (includes no row at all).
8. Columns ids_/ppg_/mpg_ are ';'-joined in ascending athlete_id order; S = sum of fresh_ppg (0.0 when none).
9. 2020-21 team games come from team_box_2021 (added to the #62 loader's 2022-26); NBA30 list taken from 2022-26 as in #62.
Flags: Q = either team row n_q_rot_c60 >= 1; NEW = either team row new_share_own_c60 >= 0.30; EARLY = either team has < 10 earlier regular-season games that season (strictly earlier tip, counted from team_box).
Lean: b = mean(actual total - model total) over finite-model league regular-season games of that season (ET date strictly before the date), if n >= 30 else 0 (n reported). Actual total = sum of both teams' points in the E1 file.
 - E3: E1 + frozen residual (mu/sd/b0/coef from frozen_e3.json, per team row, own/opp columns as listed) for 2023-24+; 2022-23 cross-fit: standardise with mean/std (ddof 0) of the 2021-22 team rows per column, y = pts - E1 pred, b0 = mean(y) (intercept unpenalised), beta = (Zc'Zc + 1000 I)^-1 Zc'(y - mean y). Verified: the same convention fitted on 2021-22+2022-23 reproduces frozen_e3.json b0/coef to 1e-9 (unit test). E3 undefined in 2021-22 (NaN).
 - F42: spec of nba_totals_grid/winner_frozen.json via fa_lib.build_world(season) + gl.project_one, NO shift added; defined 2022-23..2025-26 (2021-22 needs a 2019-20 box). Not defined for 2021-22 -> no rows there (sample lean seasons are 2023..2026 only).
Versions: Part A only (all 10 sampled versions are A). Lines: odds-API consensus close, snapshot < commence, 180 < line < 275 (strict, per pre-reg wording), no duplicates exist. Games: regular season in 2022-23, 2023-24 with line, finite model total. Bets: OVER if m_adj - line >= G, UNDER if line - m_adj >= G; rolling b uses ALL league games of that season dated strictly before the game's ET date (also games without lines). Win +100/110, loss -1, push 0, push counted in `bets` and in mean. Skips remove games. No Part B version rows were evaluated (none sampled).

## Notes / suspicious
- F42 (the #42 winner, unshifted) leans very low in 2022-23: mean(actual - F42) = +15.7 (2023-24 +5.0, 2024-25 -1.5, 2025-26 +4.7). 2022-23 constants are fitted with no prior blend (fa_lib note), so this is a property of the shared F42 code, not the check; it makes the F42 "none" 2022-23 bets nearly all OVERs and the rolling fix matters a lot there.
- E1 2023-24 total MAE 14.826 and E3 bias match frozen_e3.json validation block.
