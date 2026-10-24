# Pre-registration — research log #39: NBA totals formula tournament (every published box-score method) vs Kalshi

Written 2026-10-21 ~10:50Z by Opus, at the user's direction:
- "do all the calculation necessary... google it find it create it... test all possibilities";
- "the ones we are more confident we can add more".

User choices: **only the best formula gets the one unseen test**, and **bet size scales 1-10 contracts by edge**.
Changes go in "Amendments" with a date.

## Known before writing (discovery outcomes have been seen)

- **#37 and #38 failed the model gates on the discovery half** (660 games, Oct 21 2025 - Jan 22 2026).
  - Market median MAE 14.67, strike Brier 0.2102.
  - #37: 15.81 / 0.2265, residual coefficient +0.055 [-0.19, 0.30].
  - #38 (k = 20): 15.37 / 0.2219, residual coefficient -0.105 [-0.41, 0.19].
  - Exploratory, discovery only: when |formula - line| >= 8, the actual landed on the formula's side 49% (#37) and
    46% (#38) of the time. The best weight on (formula - line) was about 0.
- **Literature** (research_notes/NBA totals formulas/formulas_and_evidence.md):
  - No published NBA totals MAE exists for box-score formulas.
  - One modern (AI-assisted, unverified closes) source: a ratings-only model has RMSE 21.46 vs the line's 20.72. The
    slope of (result - line) on (model - line) is 0.02, and the model went 48.1% when it disagreed by 2+ points.
  - Old studies:
    - totals roughly fair (overs 49.87%, 2000-08);
    - one found an early-season totals bias (Baryla et al. 2007), direction not seen.
- **The unseen half (661 games from 2026-01-23 02:00Z) has never been loaded.** It is the only honest test.

## Shared machinery (from #38, audited)

- **Data:**
  - #37's T-1h quotes and split (reports/output/nba_totals_pace/totals.sqlite, read-only);
  - hoopR actual totals only;
  - #38's turnover fix (amendment 1) and D = ET date of min(tip, T - 1 h).
- **Walk-forward:** ratings for a game on D use only box scores with ET date < D. The prior is the previous season,
  regular season only, league-shifted exactly as #38 (n_lg / (n_lg + 100)).
- **Blend strength:** k = 20 (chosen on discovery in #38), for every version.
- **Gate stage:** box scores dated before 2026-01-23 only.

## The grid: 5 formulas x 3 rating types x 2 rest settings = 30 model versions

For each team, (O, D, P) are its blended per-possession offense, defense and pace. L_o and L_p are the league ORtg and
pace at D (the league-shifted level, as #38).

**Formulas**

| ID | Name | Team A points | Notes |
|---|---|---|---|
| F1 | Your average | pace_g x avg(O_A, D_B) | pace_g = (P_A + P_B) / 2. This is #38's formula. |
| F2 | Multiplicative (KenPom / log5) | pace_g x O_A x D_B / L_o | pace_g = P_A x P_B / L_p |
| F3 | Additive | pace_g x (O_A + D_B - L_o) | pace_g = (P_A + P_B) / 2 |
| F4 | Points-per-game average | (O_A x P_A + D_B x P_B) / 2 | Each team's own pace, as in #37 |
| F5 | Points-per-game additive | O_A x P_A + D_B x P_B - L_o x L_p | |

The total is A + B for every formula.

**Rating types**

- **R1, season blend:** exactly #38.
  - Current season: sum of points / sum of possessions over the team's games before D.
  - Blended with the shifted prior at k = 20.
- **R2, recent games weighted:** as R1, but each current-season game gets weight 0.5^(age/10).
  - age = 0 for the team's most recent game. Applied to points, possessions and pace.
  - The effective n = the sum of the weights, used in the k = 20 blend.
- **R3, adjusted for opponents** (KenPom-style iteration).
  - Over the team's games before D:
    - adj_O_a = sum over games of [pts_a / poss_a x L_o / adj_D_opp] x poss_a / sum of poss_a;
    - adj_D_a = the same with opponent points and adj_O_opp.
  - Iterate from the raw values until the maximum change is < 1e-6, with at most 100 rounds.
  - Pace is unadjusted.
  - The prior is the same adjustment over the whole previous regular season, league-shifted. The blend is at k = 20.

**Rest setting**

- **S0:** no rest adjustment.
- **S1:** total += c x (the number of teams playing on 0 days' rest, i.e. a back-to-back).
  - The day count is the ET date gap to the team's previous game; a team's first game is not a back-to-back.
  - c is fitted per (formula, rating type) by OLS of (actual - projection) on that count, over the **2024-25
    season**. That season's projections use the same machinery with a 2023-24 prior (team_box_2024).
  - No discovery data is used to fit c.

## Probabilities and choosing the winner (discovery only)

- **Per version:**
  - sigma = the standard deviation of (actual - projected) over discovery games;
  - P(over X) = 1 - Phi((X - proj) / sigma), as #37.
- **Leaderboard** (all 30 versions, all reported): discovery MAE, strike Brier, bias, the residual coefficient with
  its CI, and the "formula's side won" share by |formula - line| bucket.
- **Winner:** the version with the **lowest discovery strike Brier**. Ties go to fewer moving parts, in the order
  R1 < R2 < R3, then S0 < S1, then F1 ... F5.
- **Gates on the winner only,** identical to #37 and #38:
  - MAE <= market + 0.5 **and** Brier <= market + 0.002;
  - residual coefficient > 0 with both CI ends > 0 (10,000 game bootstraps, seed 20261021);
  - volume.
- **If the winner fails any gate, stop.** The unseen half is not loaded.

## Betting rule (winner only, only if all gates pass)

- **At T-1h:**
  - for each strike with mid in 15-85c, YES edge = P - ask - fee/contract and NO edge = (1 - P) - (1 - bid) -
    fee/contract;
  - one bet per game, on the largest edge, if edge >= theta.
- **Size (the user's choice):**
  - contracts = min(10, 1 + floor(9 x (edge - theta) / (0.10 - theta))), giving 1 contract at edge = theta and
    10 at edge >= 10c;
  - the fee is Kalshi's taker fee at that count, rounded up per order to $0.000001;
  - edge is computed with the fee at count 10, then the fee is recomputed at the actual count for P&L.
- **Exit:** hold to settlement.
- **theta:** {3c, 6c}. **Primary 3c.**

## Pass bar (primary; all must hold)

1. Mean net per contract (contract-weighted) > 0 in discovery and unseen.
2. Unseen 95% bootstrap lower bound of the mean net per contract > 0. Resample games, 10,000 draws, seed 20261021.
3. At least 100 unseen bets.
4. **Coin flip:** at most 5% of 500 copies make as many total dollars on unseen.
   - Each copy uses the same games and the same contract count per bet.
   - The side is random, on a random strike with mid 15-85c.
   - Same fees, held to settlement. Seed 20261021.
5. At least $2 per week, over unseen weeks.
6. Unseen accuracy for the winner: MAE <= market + 0.5 and Brier within 0.002.

## Versions

30 model + 2 betting = **32 versions**.

The size grows by up to 10 contracts per bet. At prices up to 85c that is up to $8.50 per bet, so this is a research
size, above the user's live $1-per-bet caps.

## Amendments

- **2026-10-21 ~11:45Z (Opus), after the Opus leak-audit, with the user's answer.** Written before any 2025-26
  MAE, Brier, sigma, residual or P&L was computed. The version count is unchanged at 32.
  1. **Rest constant** (the user's choice, "Use the clean measure"):
     - Fit (actual - projection) = a + c x n_b2b by OLS with an intercept, on 2024-25 regular-season games.
     - Only the slope c is applied; the intercept a (2024-25 bias) is not.
     - The audit measured c from -1.14 to -1.26 per back-to-back team across the 15 (formula, rating type) pairs.
     - The earlier no-intercept fit is replaced.
     - Caveat: the 2024-25 fit uses k = 20, which #38 chose on discovery. This is minor contamination of a constant.
  2. **R3 details:**
     - **Update order:** Gauss-Seidel, O updated first from the old D, then D from the new O. A simultaneous update
       oscillates. The order fixes the O/D split while teams form 2-team clusters in the first days, which moves F1_R3
       by up to 1.7 points vs R1 in week 1.
     - **Weights:** adj_D is weighted by opponent possessions.
     - **League level:** inside R3 the iteration uses the raw current-season league ORtg before D, not the shifted
       L_o. The prior uses the previous full season. The shift is applied via the blend at k = 20 with n = game
       count. The gap to the shifted level is <= 0.3% after day 2.
     - **Iteration cap:** dates that hit the 100-round cap are listed in results.json. They are 2025-10-27 (distance
       2e-4 from the converged value) and 2024-10-26/27 in the rest fit.
  3. **The 2024-25 rest fit** uses regular-season games only, with decision date = ET date of tip - 1 h (no Kalshi
     T exists). Away/home are assigned alphabetically; every formula and the back-to-back count are symmetric.
  4. **Tolerances:** ties use exact equality. The size formula has a 1e-9 tolerance and a floor of 1 contract.
     Side-won buckets drop exact ties and report n.
  5. **Wording fix:** F4 uses each term's own team pace as in the table, (O_A x P_A + D_B x P_B) / 2. It is not
     "as in #37" (#37 used A's pace for both of A's terms).
  6. **Reported in addition:**
     - The near-duplicate structure: F4 ~ F1 (sd 0.03) and F5 ~ F2. The grid has about 3 truly different formulas.
     - The winner's discovery gates are biased upward by the selection (winner's curse); only the unseen test is
       clean.
     - A cent-rounding fee sensitivity (information only).
     - Caveat: fills of up to 10 contracts are assumed at candle quotes with no depth data.
  7. **Guards:**
     - the gate-stage box scores are dated before 2026-01-23;
     - the 2024-25 fit dates are before 2025-07-01;
     - the maximum discovery D is before the cutoff;
     - fit_c has a nonzero regressor.
