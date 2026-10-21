# Pre-registration — research log #37: the user's pace x efficiency formula for NBA game totals vs Kalshi

Written 2026-10-21 ~07:00Z by Opus. **Every design choice below is the user's**, from their message and answers on
2026-10-21. Changes go in "Amendments" with a date.

## The formula (user's own, worked example BOS @ DET)

- ORtg = points per possession. DRtg = points allowed per possession. Pace = possessions per game.
- **Team A points** = average of ( ORtg_A x Pace_A , DRtg_B x Pace_A ).
- **Team B points** = average of ( ORtg_B x Pace_B , DRtg_A x Pace_B ).
- **Projected total** = A + B.
- **Example:**
  - BOS = avg(1.159 x 98.5, 1.054 x 98.5) = avg(114.16, 103.82) = 108.99.
  - DET = avg(1.124 x 103.1, 1.085 x 103.1) = avg(115.88, 111.86) = 113.87.
  - Total = 222.86.
- **Only this version is tested** (the user's choice). No shared pace, no multiplicative form, no home adjustment and
  no bias correction.

## Inputs: "last season only" (the user's choice)

- For a game in season S, every team's ORtg, DRtg and pace come from season S-1's **regular season**.
  - Source: hoopR team_box (CC BY 4.0, already in data/raw/nba_open).
  - The values stay fixed for the whole of season S.
  - There is no look-ahead: S-1 ended before S began.
- **Per team-game possessions** = FGA - OREB + total turnovers + 0.44 x FTA.
  - ORtg = sum of points / sum of own possessions.
  - DRtg = sum of opponent points / sum of opponent possessions.
  - Pace = mean over games of (own + opponent possessions) / 2.
- hoopR team codes are mapped to Kalshi codes by a tested map (e.g. NY->NYK, SA->SAS, GS->GSW, NO->NOP, UTAH->UTA,
  WSH->WAS).

## Data (read-only)

- **Kalshi KXNBATOTAL** for every 2025-26 game (Oct 2025 - Jun 2026, regular season and playoffs):
  - market definitions (strike = "total over X"), results;
  - the hourly candle (yes_bid / yes_ask close) ending at or before **T - 60 min** (the user's choice).
  - T = scheduled start (KXNBAGAME occurrence - 3 h, as #24).
- **Actual totals:** hoopR team_box scores, cross-checked against Kalshi results.
- **2026-27 games played so far** (2025-26 stats) are reported separately as a forward look. They don't count.

## Turning the projection into chances (user: "yes")

- P(total > X) = 1 - Phi((X - projected) / sigma), allowing for strike conventions (integer totals, X.5 strikes).
- sigma = the standard deviation of (actual - projected) over **discovery games only**. Nothing else is fitted.

## Market line

- Each game's market median at T-1h = the strike where the mid ((bid + ask) / 2) crosses 0.5, linearly
  interpolated between adjacent strikes with valid quotes.
- Games with no valid crossing are skipped and counted.

## Split

2025-26 games by scheduled start: **discovery** = the first half, **unseen** = the second half.

## Gates first (/model-test skill, discovery only)

1. **Accuracy:**
   - Model MAE (projected total vs actual) <= the market-median MAE, or within 0.5 points.
   - **And** strike-level Brier, model P vs market mid, on all strikes with a valid T-1h quote: model <= market,
     or within 0.002.
2. **Residual test:** outcome ~ logit(mid) + (logit(model P) - logit(mid)), on all quoted strikes. The disagreement
   coefficient must be > 0, with a 95% CI excluding 0 (bootstrap by game).
3. **Volume:** at least 20 strikes per week where |model P - mid| exceeds half the spread plus the fee.

If any gate fails, **stop**: "the formula is not better than Kalshi's line", and no betting rule is run.

## Betting rule (only if all gates pass; the user's "defaults")

- **At T-1h, for each strike priced 15-85c:**
  - YES (over) edge = P - ask - fee(ask);
  - NO (under) edge = (1 - P) - (1 - bid) - fee(1 - bid).
- **One bet per game:** the single strike-and-side with the largest edge, if that edge >= theta.
- Count 10, hold to settlement. Fee per Kalshi rounding at count 10.

## Versions

- 1 gate run, plus betting theta {3c, 6c} = **3 versions**.
- **Primary: theta = 3c.**

## Pass bar (primary; all must hold)

1. Mean net per contract > 0 in discovery and unseen.
2. Unseen 95% bootstrap lower bound > 0. Resample games, 10,000 draws, seed 20261021.
3. At least 100 unseen bets.
4. **Coin flip:** at most 5% of 500 copies do as well on unseen. Each copy uses the same games and the same count,
   with a random side on a random strike priced 15-85c, the same fees and hold. Seed 20261021.
5. At least $2 per week at 10 contracts.
6. Unseen accuracy: model MAE <= market MAE + 0.5, and Brier within 0.002 of the market's.

## Reported regardless

- The model's mean error (bias) by month, since the league scoring level can drift from last season.
- MAE and Brier by month and for the playoffs.
- Calibration.
- Bets by over/under.
- The 2026-27 forward look.
- Team-code match counts and skipped games.

## Amendments

- **2026-10-21 ~08:05Z (Opus), a note; no rule changes, and no results have been seen.**
  - **Where the example came from:** the user says the formula and its example numbers came from an Instagram reel, and
    they don't know whether it is accurate.
  - **The example vs the box scores:** none of the example numbers match a single season exactly. Box-score
    projections for BOS @ DET are 226.23 (2024-25) and 224.76 (2025-26), against the example's 222.86.
  - **Inputs stay as written:** last season's ratings, calculated from the hoopR box scores. The user said they weren't
    sure, so Opus kept the default.
  - **Refinements come later**, as new pre-registered versions tested on discovery games only.
- **2026-10-21 ~08:40Z (Opus), clarifications and fixes after the Opus leak-audit.** Written before any gate,
  MAE, Brier or P&L was computed, and the version count is unchanged at 3.
  1. **Accepted readings of the builder's choices:**
     - "Priced 15-85c" means the strike's mid is in [0.15, 0.85]. The coin flip uses the same filter.
     - Fees are rounded up to $0.000001 per order at count 10, which matches the live-verified units.taker_fee.
     - Logits are clipped to [0.01, 0.99]; Brier is unclipped.
     - The market median is the first crossing of 0.5 from the low-strike side, with no wide-quote filter.
     - The split is the hoopR-matched 2025-26 games sorted by T.
     - Strikes are X.5 and "greater", so the boundary is the strike itself.
  2. **Residual-test bootstrap:** 10,000 draws, seed 20261021, resampling games, the same as the bet bootstrap.
  3. **Scheduled time for 2025-26 games:**
     - T = the earlier of (the KXNBAGAME T, or the market's own occurrence - 3 h) and the hoopR tip time.
     - Every quote used must come from a candle ending at or before hoopR tip - 1 h; this is asserted in the code.
     - Reason: KXNBAGAME times were stale for 4 games rescheduled in late January 2026. Their quotes were all invalid,
       so nothing leaked, but they were wrongly skipped.
  4. **Outcome cross-check on every settled strike:** (hoopR total > strike) must equal (result = yes).
     - Mismatches are counted per split.
     - Any mismatch is explained before the gates are read.
  5. **Gate 2** needs both ends of the 95% CI above 0 (a code bug had checked only one). This is what the rules
     already said.
  6. **Forward look:** settled markets only. Preseason games are reported apart from the regular season.
  7. **Reported in addition:**
     - qualifying games per week (one bet per game, 15-85c);
     - a market-median sensitivity using only quotes with a spread of 10c or less;
     - candle volume on strikes bet.
  8. **Caveats:**
     - hoopR's regular season includes the NBA Cup final, which gives one team 83 games (1/83 weight).
     - Candles carry no size, so a 10-lot is assumed to fill at the best quote.
     - KXNBATOTAL's fee_type and fee_multiplier are read from the public series endpoint and recorded.
