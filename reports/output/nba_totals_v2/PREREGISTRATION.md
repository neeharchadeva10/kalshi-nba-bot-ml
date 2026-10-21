# Pre-registration — research log #38: the user's totals formula, fixed (shared pace, this season's stats, league level)

Written 2026-10-21 ~09:20Z by Opus. The design choices are the user's, from their answers on 2026-10-21. Changes go
in "Amendments" with a date.

## Known before writing

- **#37** (reports/output/nba_totals_pace/) failed at the model gates on discovery.
  - MAE: 15.81 vs the market median's 14.67. Strike Brier: 0.2265 vs 0.2102.
  - Residual coefficient 0.055, CI [-0.19, 0.30].
  - It ran 4.3 points low. By month: Oct +7.3, Nov +5.4, Dec +5.9, Jan -0.5.
  - Removing the bias in-sample only reaches 15.47.
  - corr(actual, formula) = 0.14 vs 0.34 for the market.
- **Discovery outcomes have therefore been seen.** **The unseen half (661 games from 2026-01-23 02:00Z) has not been
  loaded**, and it is the only honest test of this version.
- **The user chose** (2026-10-21):
  - "Fix the formula only" (no injuries), at 1 hour before tip;
  - shared pace = the average of the two teams;
  - the blend speed picked on the first half;
  - "Yes, shift them" to this season's league scoring level.

## The model (everything not listed is as in #37)

For a 2025-26 game on ET date D, each team X's ratings use only box scores from **ET dates strictly before D**.
hoopR team_box_2026 is used, regular season, play-in and playoffs; preseason and All-Star games are excluded.

1. **Last season's ratings** O_prev, D_prev, P_prev: exactly as #37, from the 2024-25 regular season.
2. **League shift** (the user's "yes"):
   - **League levels.**
     - Lo_prev and Lp_prev are the 2024-25 league ORtg (sum of points / sum of possessions) and the mean pace.
     - Lo_cur and Lp_cur are the same from 2025-26 games before D, over n_lg team-games.
   - **Shrunk ratios.**
     - r_o = 1 + (Lo_cur / Lo_prev - 1) x n_lg / (n_lg + 100).
     - r_p = 1 + (Lp_cur / Lp_prev - 1) x n_lg / (n_lg + 100).
     - With n_lg = 0, r = 1.
     - The constant 100 team-games is fixed and not tuned.
   - **Prior ratings:** O_prior = O_prev x r_o, D_prior = D_prev x r_o, P_prior = P_prev x r_p.
3. **This season's ratings,** from the team's n games before D:
   - O_cur = sum of points / sum of own possessions;
   - D_cur = sum of opponent points / sum of opponent possessions;
   - P_cur = the mean of (own + opponent possessions) / 2.
4. **Blend** with strength k (last season counts as k games):
   - O = (k x O_prior + n x O_cur) / (k + n), and likewise for D and P.
   - If n = 0, the prior is used.
5. **Shared pace:** Pace_game = (P_A + P_B) / 2.
6. **Team A points** = avg(O_A x Pace_game, D_B x Pace_game). The same for B. Total = A + B. The user's averaging
   step is unchanged.

## Choosing k (on discovery only)

- k is chosen from {0, 5, 10, 20, 40}, a 5-model grid. The chosen k has the **lowest discovery MAE** (projection vs
  actual). A tie goes to the larger k.
- All 5 discovery MAEs are reported.
- sigma = the standard deviation of (actual - projected) over discovery games at the chosen k.

## Data

- No new downloads. Market data comes from the #37 database, read-only:
  - reports/output/nba_totals_pace/totals.sqlite;
  - the T-1h candles under #37 amendment 2's T rule;
  - the same 660 / 661 split.
- Actual totals come from hoopR (the ladder check passed with 0 mismatches).
- **No 2026-27 forward look:** #37 found KXNBATOTAL expiration_value is a per-strike running total, not the final.

## Gates and betting

- **Gates** are identical to #37, on discovery at the chosen k:
  - MAE <= market + 0.5 **and** strike Brier <= market + 0.002;
  - residual coefficient > 0 with both CI ends > 0 (10,000 game bootstraps, seed 20261021);
  - volume.
- **If any gate fails, stop.** The unseen half is not loaded.
- **Betting rule, only if all gates pass:**
  - identical to #37 (one bet per game, mid 15-85c, count 10, hold, taker fees);
  - theta {3c, 6c}, **primary 3c**;
  - the unseen half is run once.
- **Pass bar** (primary): identical to #37 items 1-6. This includes the coin flip (500 copies) and unseen accuracy
  (MAE <= market + 0.5 and Brier within 0.002).

## Versions

5 model grid + 2 betting theta = **7 versions**.

## Reported regardless

- Bias and MAE by month; calibration.
- Bets by over/under.
- The unblended pieces at the chosen k (prior only, current only), for discovery only.
- n (games before D) at the time of each discovery game.

## Amendments

- **2026-10-21 ~10:00Z (Opus), after the Opus leak-audit, with the user's answer.** Written before any MAE, Brier,
  sigma or P&L was computed. The version count is unchanged at 7.
  1. **Turnovers for possessions** (user: "Both seasons"):
     - tov = total_turnovers if it is finite and >= turnovers, else turnovers.
     - This applies to the 2024-25 prior and the 2025-26 games.
     - It fixes:
       - CHI on 2025-12-01, where total_turnovers is NaN; this had turned every projection after that date into NaN;
       - LAC and CHI on 2026-01-20, GS on 2025-01-05 and DEN on 2024-12-19, where total_turnovers is 0.
     - The code asserts that every box-score input and every projection is finite. Smoke prints the NaN count at each k.
  2. **Decision date:** D = the ET date of min(hoopR tip, T - 1 h). Only 26JAN24GSWMIN (unseen) changes: league
     games from the evening of Jan 24, after its quote, no longer count.
  3. **Actual totals** come from hoopR only, with no expiration_value fallback. Missing actuals are counted before
     anything is filled, and none are filled.
  4. **Reporting restored as in #37:**
     - qualifying games per week;
     - market median from quotes with a spread of 10c or less;
     - blend Brier;
     - spread stats;
     - per-game n_away / n_home in games.csv.
  5. **Hygiene:**
     - the v2 run module is loaded by path in its tests (a name clash with #37);
     - the gate stage builds ratings from box scores dated before 2026-01-23 ET only;
     - the look-ahead test asserts the same-date row is excluded.
  6. **#37 note:** #37's 2024-25 prior had the 2 zero-turnover rows (about 1/82 weight each). The effect is far too
     small to change #37's verdict, which missed the accuracy bar by 1.1 points. #37 is not re-run.
