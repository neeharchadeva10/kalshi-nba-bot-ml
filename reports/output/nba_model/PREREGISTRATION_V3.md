# Pre-registration — research log #32: NBA game-winner model v3 (5 seasons, sportsbook moneylines + Kalshi)

Written 2026-10-21 ~05:30Z by Opus.

**User decision:** use kyleskom/NBA-Machine-Learning-Sports-Betting `Data/OddsData.sqlite` for moneylines.
- It has **no license**, and its odds were scraped from Sportsbook Review by that repo's author.
- **Private research use only:** no redistribution, and the data isn't to be republished.
- The book and timing of its line are unknown.

**Known before writing:**
- #29 and #31 results (one season, model worse than Kalshi).
- The column names and per-season row counts of the odds file.
- hoopR box-score file coverage.
- That NBA injury-report PDFs exist from 2018-19.

**Not done yet:** no odds have been compared with outcomes or with any model. Changes go in "Amendments" with a date.

## Data

- **Box scores:** hoopR-nba-data team_box / player_box 2022-2026 (seasons 2021-22 to 2025-26). Exact scores, minutes,
  points and plus-minus.
- **Injury reports:** official NBA PDFs. For each game, the latest report created at least 60 min before scheduled tip,
  parsed as in #31.
- **Moneylines:** OddsData.sqlite (ML_Home, ML_Away).
  - 2021-22 and 2022-23 use the `_new` tables (clean dates). Later seasons use the plain tables.
  - Join to box scores on date + home team. Unmatched rows are reported and dropped.
- **Kalshi:** 2025-26 KXNBAGAME T-1h ask (data/nba_drift.sqlite), as in #29/#31.

## Features

As #31 v2, with no look-ahead:
- Elo with exact margins. The season carry-over is rating = 0.75 * end-of-season + 0.25 * 1505 (538 style).
- Rolling 10-game net margin.
- Rest and back-to-back.
- Injury-adjusted missing value. Player value uses games within the same season; a player's first 5 games of a season
  get shrinkage only.
- Home flag.

## Fit and tests

- **Model A** (for the 2024-25 test): logistic regression fitted on 2021-22 + 2022-23 + 2023-24.
- **Model B** (for the 2025-26 Kalshi test): refitted on 2021-22 through 2024-25.
- **Primary test (unseen), 2024-25 against the sportsbook moneylines:**
  - Implied probability of a price p: 100/(p+100) if p > 0, else -p/(-p+100).
  - Edge = model p - implied p.
  - Bet the side with the larger edge when edge >= theta. At most one bet per game.
  - $1 stake at the listed American odds (the book's margin is already in the price). Profit = odds payout if the
    team wins, else -1.
- **Secondary (confirmation), 2025-26 against Kalshi:** Model B, the same rule as #31 (T-1h ask + fee at 10
  contracts), theta matching the primary.

## Versions

- theta in {2%, 4%, 6%} x {full, Elo-only} = **6 versions**.
- **Primary (named now):** full, theta = 4%, 2024-25 against the moneylines.

## Pass bar (primary; all must hold)

1. Mean return per $1 bet > 0 on 2024-25 **and** mean net > 0 on the 2025-26 Kalshi confirmation.
2. 2024-25 95% bootstrap lower bound > 0 (resample games, 10,000 draws, seed 20261021).
3. At least 200 bets in 2024-25.
4. **Coin flip:** at most 5% of 500 random-side copies do as well on 2024-25. Same games, same count, same odds rule.
   Seed 20261021.
5. At least $2 per week projected at $10 per bet on 2024-25 (weeks with games).
6. **Model quality:** 2024-25 model Brier <= the de-vigged moneyline Brier (home implied / (home + away implied)),
   or within 0.002 while rules 1-5 pass. 2025-26 model Brier vs Kalshi is also reported.

Training-season (in-sample) results are reported but don't count. The other 5 versions are leads only.

## Reported regardless

- Brier and log loss by season for model, de-vigged moneyline and Kalshi.
- Calibration.
- Feature weights.
- Return by edge bucket, by favourite vs underdog, and by regular season vs playoffs.
- Join and coverage counts.
- Injury-report coverage by season.

## Amendments

(none)
