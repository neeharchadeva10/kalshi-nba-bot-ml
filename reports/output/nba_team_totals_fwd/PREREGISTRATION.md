# Pre-registration — research log #41: the user's formula on Kalshi NBA TEAM totals, forward test (2026-27)

Written 2026-10-21 ~13:10Z (about 09:10 ET, before tonight's games) by Opus. The user chose "Forward on this season".
Changes go in "Amendments" with a date.

## Known before writing

- **Game totals failed** in #37, #38 and #39. Every formula's disagreements with Kalshi's game-total line carried
  no information (residual coefficients from -0.17 to +0.06).
- **Team totals are a different and thinner market.**
  - KXNBATEAMTOTAL: "points scored by TEAM ... Above X.5", 9 strikes per team, quadratic fee.
  - It has existed since 2026-02-10.
- **No team-total outcome or price has been looked at.**
- The 2025-26 Feb-Jun team totals are deliberately unused: they would reveal the game totals of the untouched unseen
  half.

## The model (fixed now; nothing is fitted on 2026-27 data)

- **Version F1_R2_S1 from #39,** the user's averaging formula with recent games weighted and the back-to-back
  adjustment. It tied the #39 winner (MAE 15.30).
- **Inputs:**
  - Team A points = pace_g x avg(O_A, D_B), with pace_g = (P_A + P_B) / 2.
  - The prior is the 2025-26 regular season (team_box_2026, with the turnover fix), league-shifted with
    n_lg / (n_lg + 100) to 2026-27 games before D.
  - The current season is 2026-27 games before D, weighted 0.5^(age/10). Blend k = 20.
- **Back-to-back:** each team on 0 days' rest subtracts c_team from **its own** points.
  - c_team = half of the game-level c for F1_R2 refitted on 2025-26 regular-season games (prior 2024-25), with an
    intercept, slope only.
  - Reason: a game-level back-to-back count is split evenly between the two teams.
- **sigma_team** = the standard deviation of (actual team points - projected team points) over **all 2025-26
  regular-season team-games**, from the same walk-forward machinery. This is past, already-seen data.
- **P(team A over X.5)** = 1 - Phi((X.5 - proj_A) / sigma_team).

## Data

- **Decision time:** T - 1 h, with T = KXNBAGAME occurrence - 3 h (#37 rule; fallback is the market's own occurrence
  - 3 h).
- **Quotes:** the KXNBATEAMTOTAL hourly candle ending at or before T - 1 h, from public candles downloaded after
  settlement with #37's downloader.
- **Results** are Kalshi yes/no.
- **Actual team points** come from hoopR team_box_2027 if available, otherwise from KXNBATOTAL/KXNBAGAME
  settlement-independent sources. Any source change is an amendment.
- **Window:** regular-season games with tips on ET dates 2026-10-21 through 2026-12-31. The test runs once, after
  all of them settle.
  - If fewer than 600 team-games have a valid T-1h median by then, the window extends in 2-week steps until they
    do.
  - The extension rule uses counts only, no results.

## Gates first (model-test skill), then betting, all on the forward window

1. **Accuracy:**
   - Team-points MAE <= the market team-median MAE + 0.3 points. The median is the 0.5-mid crossing,
     interpolated, as in #37.
   - **And** strike Brier <= the market's + 0.002.
2. **Residual test:** the disagreement coefficient is > 0 with both 95% CI ends > 0 (10,000 game bootstraps, seed
   20261021).
3. **Volume:** at least 20 team-games per week with a strike edge >= 3c after fees.

**If any gate fails, stop.** The betting rule is not run.

## Betting rule (only if all gates pass)

- **Per team-game:** the single best-edge strike and side among strikes with mid 15-85c, if edge >= theta.
- **Size:** 1-10 contracts by edge, the #39 formula. Taker, held to settlement.
- **theta:** {3c, 6c}. **Primary 3c.**
- **Pass bar:**
  - mean net per contract > 0;
  - bootstrap lower bound > 0 (games resampled);
  - at least 150 bets;
  - coin flip (random side on a random 15-85c strike, same counts) at most 5% of 500 copies;
  - at least $2 per week.

## Versions

1 model + 2 theta = **3 versions**.

## Amendments

(none)
