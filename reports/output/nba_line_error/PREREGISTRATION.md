# Pre-registration — research log #57: where is the NBA total line wrong? A line-error model on clean, multi-season data

Written by Opus on 2026-10-22 at 21:18Z. The user, after #56: "keep refining until you find the edge but make sure
everything is right and the data is clean double check formulas and data". The user's focus remains the points
formula. Changes go in "Amendments".

## Why this design

#56 showed that no standalone formula beats the line. Each disagreement mostly measured the formula's own error. The
honest refinement is to model the LINE's error directly:

  y = actual final total - closing total line

from facts known before the game. Bets are placed only where the predicted error is large. Many seasons are needed to
see small biases, and they must be clean. So data checks come first, and every formula is computed twice,
independently.

## What is already known (disclosed)

- **#56:** formula-minus-line disagreements carried no information on 2022-26. The line was right 52% of the time.
- **#39** (Kalshi 2025-26 discovery): actual minus line by month: Oct +3.4, Jan -3.5.
- **Early-season check** (sportsbook, 2007-2026): early unders won in 12 of 14 seasons from 2007-08 to 2021-22, then
  reversed.
- **Literature:** early-season totals bias (Baryla et al. 2007); totals markets are close to efficient after costs.
- 2022-23 to 2025-26 outcomes vs lines have been looked at (#56, #39), so those seasons are the test only.

## Data (free / private research; cached; GET only)

- **Sportsbook totals:**
  - data/raw/nba_odds_samples/closing_lines_odds_api.parquet: consensus close, 2020-21 to 2025-26; in-play rows
    dropped.
  - OddsData.sqlite (2007-08 to 2024-25, private research only): two tables for most seasons ("X" and "X_new").
- **hoopR team box:** seasons 2007-08 to 2025-26.
  - Already on disk: 2021-22 to 2025-26.
  - Older seasons: download the hoopR/sportsdataverse release parquet files (GitHub, GET, cached to
    data/raw/nba_open/).
  - If a season is unavailable, it is dropped and reported.
- **Kalshi KXNBATOTAL T-1h** (2025-26): reports/output/nba_totals_pace, as in #42/#56.

## Data cleaning (pre-registered; run first; reported in full before any y is computed for modelling)

1. **Game matching:** each line row is matched to a hoopR game (date ±1 day, home/away team map). Unmatched or
   duplicated rows are excluded and counted per season.
2. **Final points:** OddsData points vs the hoopR final. hoopR is the truth for the final (it matched Kalshi in
   578/578). Mismatches are listed.
3. **Which line:**
   - 2020-21 onward: the closing_lines over_under where present.
   - Earlier seasons: OddsData, with the "X" and "X_new" tables compared row by row. If both exist and differ by more
     than 1.0 point, the game is "line disputed" and excluded.
   - Where closing_lines and OddsData overlap (2020-21 to 2024-25), report their agreement per season. That tells us
     which OddsData table tracks the close.
4. **Sanity:**
   - The line must be within [170, 275].
   - |line - mean of both teams' season-to-date average game totals| ≤ 30 (needs at least 5 prior games each; else
     only the range check).
   - Failures are excluded and listed.
5. Every check is reported per season in DATA_CHECKS.md: counts in, excluded by reason, counts out.

## Features (strictly before the game's ET date)

- **f1:** line minus the league average game total to date this season (or the last 30 days of last season if fewer
  than 50 games).
- **f2:** |spread| (the same source as the line; else OddsData spread). Large means blowout risk.
- **f3:** days since the season's first regular-season game.
- **f4:** the number of teams on a back-to-back (0/1/2).
- **f5:** the sum of both teams' last-10 pace minus the league average (possessions by the #42/grid_lib formula).
- **f6:** both teams' last-5 mean of (actual - line) in their own games. Does the market lag teams' recent over/under
  form?
- **f7:** a home altitude flag (DEN, UTA).
- **f8:** #56's frozen primary formula (flat 7791) minus the line, seasons 2008-09 onward, computed with its S-1
  prior. Also re-implemented independently (see below).
- **f9:** both teams' last-10 three-point attempt rate minus the league average.
- **f10:** home team's last-10 (actual - line) minus away team's. Asymmetric form.

## Formula and data double-check (pre-registered)

- **Two independent implementations:** every feature f1-f10, the possession formula and y are computed by two
  independent implementations, written by different builders without sharing code.
  - Agreement must hold to within 1e-6 (pace or ratings) or exactly (counts, y) for 100% of games.
  - Any disagreement is resolved (and the bug logged) BEFORE selection.
- **Possessions:** 0.96 x (FGA - OREB + TOV + 0.44 x FTA), averaged over both teams (the grid_lib convention). Check it
  against points per game (ORtg x pace / 100 ≈ PPG) for every team-season.

## Rules (versions)

**M: one ridge regression of y on f1-f10.**
- Standardised on discovery; λ chosen by 5-fold CV inside discovery (folds by season).
- Bet the over if the prediction ≥ τ, the under if ≤ -τ. τ ∈ {1, 2, 3} → 3 versions.

**U: univariate decile rules,** for each of f1-f10.
- On discovery, compute the mean y in the feature's bottom and top deciles (cut points from discovery).
- Rule: in a tail decile, bet the sign of that tail's discovery mean y.
- 10 features x 2 tails = 20 versions.

**Total: 23 versions.**

## Split

- **Discovery:** 2008-09 to 2018-19, regular season (11 seasons; 2007-08 serves only as the prior for f8).
- **Validation:** 2019-20 to 2021-22, regular season. Bubble and no-fan seasons included, flagged.
- **Test** (run once, after an Opus leak audit creates AUDIT_OK with code hashes):
  - 2022-23 to 2025-26 regular season vs the sportsbook close;
  - plus Kalshi 2025-26 (T-1h main strike, taker ask + 1c slippage + fee, 1 contract, held; #42's machinery).

## Selection and pass bar

1. **Selection (discovery only):** for each version, the side rate vs the line with pushes excluded, and its Wilson 95%
   LB. Eligible: n ≥ 300. Pick the highest LB.
   - If no version's discovery LB exceeds 0.5238 (= 110/210), STOP: "no line error found", no validation or test run.
2. **Validation:** the selected version must have a side rate > 0.5238 with n ≥ 150. Otherwise STOP (test not run).
3. **Test (all must hold):**
   - side-rate Wilson LB > 0.5238 pooled over 2022-26 vs the close, n ≥ 300;
   - Kalshi 2025-26 mean net > 0 and game-bootstrap LB > 0 (10,000 draws, seed 20261022), n ≥ 150;
   - coin flip: random sides on the same games, 500 copies, seed 20261022. At most 5% reach the real Kalshi mean.

## Reported regardless

- The data-cleaning tables.
- The two-implementation agreement report.
- The discovery decile table of mean y for every feature.
- Per-season side rates of the selected version (discovery and validation).

## Versions

23.

## Amendments

(none)

- **2026-10-22 21:42Z (Opus), line provenance and feature readings.** Written after implementation B's data report and
  BEFORE any y-based analysis, model or selection.
  1. **Finding:** OddsData totals agree with the odds-API consensus close in 2022-23..2024-25 (mean |diff| 0.4-0.7), but
     NOT in 2019-20..2021-22 (mean |diff| 3.2-3.4; 26-30% within 1 point). OddsData's line timing is therefore
     unknown, and possibly opening lines, for at least some seasons. It cannot be checked for 2008-09..2018-19.
  2. **Discovery** stays 2008-09..2018-19 on OddsData lines, now labelled "sportsbook line, timing unknown".
  3. **Validation becomes 2020-21..2021-22 on the odds-API consensus close only.** 2019-20 is dropped, because its line
     source is uncertain. Validation n ≥ 150 is unchanged.
  4. **Test unchanged:** 2022-23..2025-26 vs the close, plus Kalshi 2025-26. A bias that exists only against
     non-closing lines will fail validation or test. That is the intended protection.
  5. **Feature readings (both implementations must use these):**
     - f5 and f9 = the sum of the two teams' deviations from the league average (sum - 2 x league).
     - f6 = the mean of the two teams' last-5 means of (actual - line); each team needs 5 lined games, else NaN.
     - f5, f9 and f10 need full windows inside the current season, else NaN. f1 alone may fall back to last season.
     - f8 uses #56's frozen primary exactly as frozen.json names it: B = "own" pace (index 0), not "term-own".
     - When OddsData X and X_new agree within 1.0, use X_new.
     - 2017-18 has no team-turnover column, so team turnovers are omitted there (possessions slightly low; noted).
     - The NBA Cup final counts as regular season (as hoopR files it).
  6. Versions are unchanged (23).

- **2026-10-22 21:46Z (Opus), agreement resolution and two rule fixes.** Still before any y-based analysis or selection.
  1. **Agreement A vs B:**
     - The game set (21,554), line, actual, y, f1, f3, f4, f6, f7, f9 and f10 agree exactly.
     - **f2:** 1 game with spread "PK". It is 0 (A is right; B's NaN is a parsing gap).
     - **f5:** hoopR has total_turnovers = 0 or below the player turnovers in some rows, and exactly 2x turnovers in
       2007-08..2011-12. A's rule is correct: use total_turnovers if it is finite and ≥ turnovers, else turnovers;
       use turnovers in 2007-12. With B's raw rule, A reproduces B exactly in every season, so the difference is
       fully explained.
     - **f8:** A runs #56's frozen code path. The #56 leak audit verified it by an independent hand-coded
       recomputation (≤ 1e-13). B's from-spec re-implementation read several steps differently. **Definition: A.**
       B's divergence is recorded as a spec-documentation gap, not a data error.
     - Resolution accepted. The agreement gate is satisfied with A's features.
  2. **Discrete features** (fewer than 20 distinct values: f4, f7): the decile tails become the games at the
     feature's minimum value and the games at its maximum value. The "≤ q10 / ≥ q90" cut is used for continuous
     features only.
  3. **Ridge M discovery statistics** use leave-one-season-out predictions (out of fold), not in-sample. λ is still
     chosen by season-fold CV.
  4. **Known hoopR defect:** 2007-08 OREB/DREB levels are not real. That affects only the 2008-09 f8 prior. Noted, not
     fixable.
