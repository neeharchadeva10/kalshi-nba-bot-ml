# Pre-registration — research log #59: predict NBA final scores as accurately as possible (own stats only)

Written by Opus on 2026-10-23 at 00:08Z.

## The user's goal

- "our new goal now is to accuratly predict the end score of the game not it goes under or over".
- "dont worry about the lines right now we need to get as close as possible to preidicting the total score of a game".

## The user's choices (2026-10-22/23)

- Predict each team's score; the game total is their sum. **The total is the headline target.**
- Own stats only. No betting line is ever an input.
- Score by:
  - average miss in points (MAE);
  - % of games whose total lands within 10 points.
- No fees and no betting: this is accuracy only.

Changes go in "Amendments".

## Known before writing (disclosed)

- #56 and today's descriptive table: team formulas miss the 2024-26 totals by 15.1-15.7 points on average.
- The user's fixed formula #38 is the most consistent (15.1 / 15.4).
- In 2025-26 OT games (4%) averaged +25 vs any pregame number, and the biggest misses are shooting-luck nights that every
  predictor misses. A total MAE around 14 is likely near the floor for any pregame prediction.
- 2022-26 accuracy of some grid formulas has been looked at (#56). That is why model choice uses 2019-22 and the test
  is 2022-26 plus forward games.

## Data (on disk; no network)

- **hoopR team box, seasons 2007-08 to 2025-26,** plus 2026-27 to date (data/raw/nba_open/team_box_YYYY.parquet).
- The cleaning rules and turnover guard follow #57 (amended):
  - total_turnovers if it is finite and ≥ turnovers, else turnovers;
  - turnovers only in 2007-08..2011-12;
  - all-star and exhibition games dropped;
  - the NBA Cup final counts as regular season.
- Final points: hoopR. Verified against Kalshi settlement (578/578) and OddsData (16 OddsData typos).

## Features for team X vs opponent Y in game g on ET date D, season S

All features use only games strictly before D. Regular season only.

- **Possessions per team-game:** poss = 0.96 x (FGA - OREB + TOV + 0.44 x FTA). Game possessions = the mean of both teams.
- **Offense:** ortg_X over windows {season to date, last 10, last 5} in S = 100 x Σ points scored / Σ game
  possessions. Plus ortg_X_prev = the full S-1 regular season.
- **Defense:** drtg_Y over the same windows and prev = 100 x Σ points allowed / Σ game possessions.
- **Pace:** pace_X and pace_Y over {season to date, last 10} and prev = the mean game possessions.
- **League:** league ortg and league pace, season to date (S-1 full-season values while fewer than 50 games have been
  played in S).
- **Context:**
  - home (1/0);
  - rest_X and rest_Y = days since the previous game, minus 1, capped at 4 (the first game = 4);
  - b2b_X and b2b_Y (rest = 0);
  - gp_X and gp_Y = games played in S before D;
  - dos = days since S's first regular-season game.
- **Missing windows** (0 games): use the S-1 full-season value.
- **Target:** X's final points, OT included. Total = pred_X + pred_Y.

## Candidate models (all counted)

| Id | Model | Versions |
|---|---|---|
| B1 | #38, the user's fixed formula (grid default flat 8700), team points | 1 |
| B2 | #56 frozen primary (flat 7791) | 1 |
| G | The full #42/#56 grid: every one of the 26,880 formulas, as team points | 26,880 |
| R1 | Ridge regression of X's points on all features; trained on the training seasons; λ by leave-one-season-out CV inside training | 1 |
| R2 | Ridge as R1, but the target is X's points minus the league expected points (league ortg x league pace / 100, to date), added back after | 1 |
| E1 | The plain average of the best G formula and R2 | 1 |
| E2 | A stacked linear blend of the best G formula, R1 and R2, weights fitted on the training seasons | 1 |

The grid formulas for seasons before 2022-23 use #57's fa_ext extension (prior = S-1, constants fitted on S-1).

**Total: 26,886 versions.**

## Split

- **Training (fitting R1, R2, E2):** 2008-09 to 2018-19.
- **Validation (choose one model):** 2019-20 to 2021-22. The lowest pooled total MAE wins. The G family enters as its
  validation-best formula.
- **Test (run once, frozen, after an Opus leak audit):** 2022-23 to 2025-26. Then forward, 2026-27 games as they are
  played.

## Reported for the frozen model on test (by season and pooled)

- Total MAE, % of totals within 10 (headline), RMSE, bias.
- Team-score MAE, margin MAE, winner %.
- MAE excluding OT games.
- The 20 biggest misses.
- The same metrics for B1 (the user's formula) as the reference.
- **One reference row only** (not used anywhere): the sportsbook close's total MAE on the same games.

## Double checks (the user: "double check formulas and data", "multiple sources")

1. **Independent recomputation:** 300 random team-games (seed 20261023), across all seasons, recomputed by separate code
   written without sharing the main code. Every feature must agree to 1e-9; differences are resolved before
   selection.
2. **No look-ahead tests:** changing any game dated on or after D changes no feature for g.
3. **Final points:** hoopR vs Kalshi settlement (2025-26) and vs OddsData points (all seasons). Already done; re-run
   and reported.

## Versions

26,886.

## Amendments

(none)

- **2026-10-23 00:37Z (Opus), after the pre-test leak audit (no leak found; one integrity blocker). Written BEFORE any
  test-season accuracy is computed.**
  1. **Inputs hashed:** AUDIT_OK must also hash:
     - team_games_features.parquet (and run_test calls the AGREEMENT check);
     - raw team_box 2022..2026, play_by_play 2025/2026 and player_box 2023/2024;
     - closing_lines_odds_api.parquet.
     The live team_box_2027 is not hashed.
  2. **2022-23 constants:**
     - **Primary** stays as registered (legacy_2022 = True: 2022-23 constants fitted on 2021-22 without a prior, as #56).
     - **Pre-declared sensitivity row:** E1 and B1 with legacy_2022 = False (k 20 fit with prior 2020-21), 2022-23 and
       pooled. Labelled "sensitivity, not primary".
  3. **Forward 2026-27 block:**
     - It runs on a snapshot copy of team_box_2027 taken before the start marker.
     - Any error in it is recorded and is non-fatal, so it cannot burn the one-shot test.
  4. **Comparison claim:** "E1 is more accurate than B1 (the user's formula)" is made only if the paired game-bootstrap
     95% CI of (E1 total MAE - B1 total MAE), pooled over 2022-26, lies entirely below 0 (10,000 draws, seed
     20261023). The same CI is reported vs G* and per season.
  5. **Finals cross-check** (prereg double check 3), written to FINALS_CHECK.md before the test, data agreement only:
     hoopR finals vs Kalshi end-of-game values (2025-26) and vs OddsData points (2022-25).
  6. No change to the model, the seasons or the metrics.
