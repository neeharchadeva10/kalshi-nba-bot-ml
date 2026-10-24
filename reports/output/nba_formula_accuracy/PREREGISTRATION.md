# Pre-registration — research log #56: the best NBA points formula, judged on real final scores, then against the lines

Written by Opus on 2026-10-22 at 20:28Z. The user's words: "turn your focus to the points formula and find the best
formula, check all data, make sure the numbers are right and compare it with the real data in the past and see if
you are close and then check the lines".

The user's choices (2026-10-22):
- all 26,880 team formulas, ranked by accuracy;
- a new player-based formula;
- no line blend;
- the live 2-week test continues unchanged.

Changes go in "Amendments".

## What is already known (disclosed)

- #37-#42 (2025-26 Kalshi): every box-score formula had a mean absolute error (MAE) of 15.3-15.7 points vs the T-1h
  Kalshi line's 14.67 on discovery. #39's residual coefficients were all negative: the disagreements carried no
  information.
- **Opus check today (2026-10-22, all 2025-26 games):**
  - The live #42 winner is biased -4.0 points (-8.3 in October). Its MAE is 15.5 vs the line's 14.5.
  - When it differs from the line by 4+ points, it says "under" 78% of the time, and the final lands on its side 51%.
- **Data check today:** hoopR final totals match Kalshi's end-of-game values in 575/575 games.
  - The earlier "mismatches" came from YES strikes that settle early, at the moment they are crossed.
- Seasons 2025-26 and earlier have been looked at in parts by #37-#42 and the early-season check. That is why
  selection here uses 2022-23 and 2023-24 only.

## Data (on disk, free; cached)

- **hoopR** team_box and player_box, seasons 2021-22 to 2025-26 (files *_2022 ... *_2026), plus 2026-27 to date.
  - player_box has DNP rows; those are after the fact and are never used as inputs.
- **Official NBA injury reports**, data/raw/nba_injury (PDFs from 2021-10-19), parsed by
  reports/output/nba_model/injury_parse_v3.py. Input = the latest report published at or before T - 60 min.
- **Lines:**
  - **Sportsbook consensus closing total:** data/raw/nba_odds_samples/closing_lines_odds_api.parquet (over_under,
    about 11 books, snapshot a median of about 9 min before tip). This is LATER than our T-1h decision, which favours
    the line. Private research use.
  - **Kalshi T-1h implied median:** 2025-26, reports/output/nba_totals_pace; 2026-27 to date where available.
- **Actual total** = the hoopR final (both teams, OT included).

## Data checks (run first; reported regardless; any failure is fixed or excluded BEFORE selection)

1. Final totals:
   - hoopR vs Kalshi end-of-game values (NO-settled strikes), 2025-26;
   - hoopR vs closing_lines / OddsData final points where present, all seasons. Mismatches are listed.
2. Team totals = the sum of player points per game in player_box, all seasons.
3. Possessions and ratings:
   - the formula code's season ORtg, DRtg and pace per team reproduce points per game (ORtg x pace / 100 ≈ PPG, as #39
     did for 2024-25), every season;
   - cross-check against NBA.com official season team stats (GET, cached, outside 17:30-21:30 ET). If NBA.com
     refuses, this is reported as "not checked" (no scraping workarounds).
4. Injury reports: parse coverage per season (% of games with a report at or before T - 60 min); unmatched player
   names.
5. Lines: coverage per season, totals outside 180-270, duplicates, game matching to hoopR.
6. The live pipeline: tonight's projections (scripts/early_overs_picks.py via nba_totals_grid/tonight.py)
   recomputed independently from the raw inputs for at least 2 games; the 2026-27 box scores in use; the prior season
   used.

## Formulas

### A. Team formulas (26,880)

- #42's full grid (reports/output/nba_totals_grid/grid_lib.py: A..H).
- Generalised so that season S uses prior = S-1 regular season and current = S games strictly before the game's ET
  date.
- Every constant (rest/back-to-back effects, four-factor coefficients, shrink) is fitted on S-1 only.
  - For S = 2022-23, the constants come from 2021-22 with no prior-season blend: no 2020-21 on disk. The builder notes
    this.
- The user's original formula (#37) and #38's version are reported as named baselines.

### B. Player-based formula (8 versions)

For each team and game, decided at T - 60 min:
1. **Rotation:** players with 10+ mpg over their last 10 team games.
   - Remove players listed Out or Doubtful on the latest report at or before T - 60 min.
   - Questionable players count at weight q, q ∈ {0.5, 1.0}.
2. **Points per minute:** each player's last 20 games played (spanning seasons), shrunk toward his current-season
   rate with weight n/(n+10). Fewer than 5 games → the team's per-minute average.
3. **Minutes:** the last-10 mean, scaled so the team's expected minutes sum to 240 (OT ignored).
4. **Team points (raw)** = Σ ppm_i x min_i.
5. **Options:**
   - **opponent adjustment {off, on}:** x (the opponent's points allowed per 100 possessions over its last 15
     games / the league average to date);
   - **pace adjustment {off, on}:** x (the game's expected pace / the team's own recent pace), where the expected pace
     is the mean of both teams' last-15 pace.
6. **Total** = the sum of both teams.

Versions: q {0.5, 1.0} x opponent {off, on} x pace {off, on} = 8.

## Selection (seasons 2022-23 and 2023-24, regular season; only these outcomes are used to choose)

1. **Accuracy:** the best team formula and the best player formula, each by the lowest pooled MAE. RMSE and bias are
   reported.
2. **Lean correction:** for each chosen formula, shift = mean(actual - projection) on the selection seasons; the
   corrected formula = projection + shift. Both the raw and the corrected forms go forward.
3. **Disagreement threshold vs the sportsbook close:**
   - For each of the 4 chosen forms (team or player x raw or corrected), pick T ∈ {2, 4, 6, 8} by the highest
     lower bound of the "final on the formula's side of the line" rate, with n ≥ 150 (pushes excluded).
   - The single best form-and-threshold pair is the primary.
4. Everything chosen is written to frozen.json with a SHA-256 hash logged in docs/RESEARCH_LOG.md BEFORE the
   confirmation stage runs.

## Confirmation (seasons 2024-25 and 2025-26 regular season, plus 2026-27 to date as descriptive; run once, after an
Opus leak audit creates AUDIT_OK)

Reported for all 4 frozen forms; the primary decides:
- MAE and bias vs the actual total, beside the sportsbook-close MAE and the Kalshi T-1h MAE (2025-26).
- A paired game-bootstrap 95% CI of (formula MAE - line MAE) (10,000 draws, seed 20261022).
- The rate of finals on the formula's side, at the frozen T, vs the sportsbook close; n; 95% CI.
- **Kalshi 2025-26:** the frozen rule's bets at the T-1h main strike, as #42 did: taker ask + 1c slippage + fee,
  1 contract, held. n, mean net, game-bootstrap LB.

## What counts as an edge (all must hold for the primary)

1. Formula-side rate vs the sportsbook close at the frozen T: the 95% LB is above 52.38% (the break-even at -110),
   pooled over 2024-25 and 2025-26.
2. **Kalshi 2025-26:** mean net > 0 and game-bootstrap LB > 0 after fees and slippage.
3. At least 150 bets in each of 1 and 2.

Anything short of that is reported as "no edge vs the lines", with the accuracy results still given.

## Versions

26,880 (team) + 8 (player) + 2 corrections x 2 families + 4 forms x 4 thresholds = **26,908**.

## Amendments

(none)

- **21:02Z 2026-10-22 (Opus), bug fix after the leak audit. No confirmation-season outcome has been computed.**
  - **B1:** player_model.py converted team codes that were already Kalshi codes. Every game with GSW/NOP/NYK/SAS/UTA/WAS
    got NaN (903 of 2,461 selection games), so the player-family selection ran on a non-random 63% subset.
  - **Fix:** accept Kalshi codes. Add a real-data test (at most 2 NaN games per season) and a GSW/NYK toy test.
  - **Then:**
    - Archive the first freeze to selection_v1_buggy/ (not deleted).
    - Re-run run_select.py once on the same selection seasons. Its rules are unchanged.
    - Re-freeze, with the new hash logged.
  - **Also from the audit:**
    - Break-even becomes exactly 110/210.
    - run_confirm writes a confirm_started marker before loading any outcome, and refuses if one exists.
    - AUDIT_OK will hold the SHA-256 of every code file it depends on (fa_lib, run_confirm, player_model, grid_lib,
      grid_bets), checked at run time.
  - **Unchanged:** formulas, versions, thresholds, seasons and the edge criteria.
