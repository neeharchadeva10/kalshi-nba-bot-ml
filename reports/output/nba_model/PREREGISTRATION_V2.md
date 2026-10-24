# Pre-registration — research log #31: NBA game-winner model v2 (official injury reports + open box scores)

Written 2026-10-21 ~04:20Z by Opus, after the user approved downloading the NBA's official injury reports and using
open GitHub NBA data. **None of that data has been downloaded or looked at yet.**

#29's results are known, and v2 is designed around its two stated data weaknesses:
- estimated margins (44%);
- player availability from Kalshi prop listings.

The betting rule, split, versions and pass bar are copied from #29 so the comparison is fair. Changes go in
"Amendments" with a date.

## Data (free, official or openly licensed; read-only)

- **Official NBA injury reports**, 2025-26 (the NBA's published report files).
  - For each game, use the **latest report published at least 60 minutes before scheduled tip**.
  - Status per listed player: Out, Doubtful, Questionable, Probable, Available.
- **Open box-score dataset from GitHub** covering 2025-26 (license and source checked and recorded):
  - exact final scores (these replace the estimated margins);
  - per-player minutes, points and plus-minus per game.
  - If no open dataset covers 2025-26, stop and amend before modelling.
- **Kalshi:** the same KXNBAGAME prices, results and T-1h ask as #29 (data/nba_drift.sqlite), with the same games and
  the same split.

## Features (no look-ahead: only games with T before game g, plus the injury report for g)

1. Elo with **exact** margins: K=20, home advantage 100, 538 margin-of-victory multiplier, 1500 at 2025-10-21.
2. Rolling 10-game net margin (exact).
3. Rest (cap 4) and back-to-back.
4. **Injury-adjusted strength:**
   - Player value = the player's average plus-minus per minute x average minutes over his team's last 15 games, or
     the player's games if fewer. Shrink toward 0 with weight n/(n+10), where n is games played.
   - Missing value for the team = the sum over listed players of value x P(miss): Out 1.0, Doubtful 0.75,
     Questionable 0.5, Probable 0.1, Available 0.
   - The feature is home minus away.
5. Home flag (intercept).

## Model, bets, split, versions, pass bar

These are identical to #29 (reports/output/nba_model/PREREGISTRATION.md):
- logistic regression fitted on discovery only;
- edge = model probability - T-1h ask - fee at 10 contracts, at most one bet per game, held to settlement;
- the same discovery/unseen split;
- versions: theta {2, 4, 6c} x {full v2, Elo-exact only} = **6 versions**;
- **Primary: full v2, theta 4c**;
- pass bar rules 1-6, including model Brier <= market Brier (within 0.002 while rules 1-5 pass).
- Seeds 20261021.

## Reported regardless

- Everything #29 reports.
- v1 vs v2 vs market Brier on the same games.
- Injury-report coverage: games with a report at least 60 min before tip.
- P&L in games where a regular is Out or Doubtful.

## Amendments

(none)
