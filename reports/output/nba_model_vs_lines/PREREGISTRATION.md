# Pre-registration — research log #60: does the #59 score model (E1) beat the lines when they disagree, or help when blended?

Written by Opus on 2026-10-23 at 01:03Z. The user chose "Test #1 + #2 now". Changes go in "Amendments".

## Disclosure

- **E1 was frozen** (frozen.json d9866fc5...) before any 2022-26 game was used. It never used a betting line as an
  input.
- **2022-26 outcomes have been seen:**
  - E1's accuracy (#59 test: MAE 14.80);
  - one reference row: the sportsbook close's MAE 14.28 on the same games, which suggests the line is more accurate;
  - #56's formula-side rates vs the close.
- **E1's own side rate vs any line has NOT been computed.** No rule below is chosen from data: every threshold and
  weight is fixed here.

## Data

- **E1 predictions,** 2022-23..2025-26 regular season, recomputed with the #59 code. They must reproduce #59's test
  pooled MAE of 14.796 exactly. Plus 2019-22 predictions (validation seasons) for part C's fitted weight only.
- **Sportsbook consensus close:** data/raw/nba_odds_samples/closing_lines_odds_api.parquet, in-play rows dropped.
- **Kalshi 2025-26:** the T-1h implied median and the main strike, via #42's machinery
  (reports/output/nba_totals_grid/grid_bets.py: taker ask + 1c slippage + fee, 1 contract, held).
- **Multi-source check:** Kalshi T-1h median vs the close (median |diff|), and the finals (hoopR, already verified
  578/578).

## A. Disagreement vs the sportsbook close (2022-26, pooled)

- Side = over if E1_total > close, under if E1_total < close. Gap g = |E1_total - close|.
- Side rate = the share of games, pushes excluded, where the final lands on E1's side; Wilson 95% LB.
- g ∈ {4, 6, 8} → 3 versions. **Primary g = 4.**
- **Pass:** LB > 110/210 (≈ 52.38%) with n ≥ 150.

## B. Kalshi 2025-26 bets with fees

- At T-1h, if |E1_total - Kalshi median| ≥ g, buy E1's side at the main strike (taker ask + 1c + fee, 1 contract,
  held).
- g ∈ {4, 6, 8} → 3 versions. **Primary g = 4.**
- **Pass (all):**
  - mean net > 0;
  - game-bootstrap 95% LB > 0 (10,000 draws, seed 20261023);
  - n ≥ 150;
  - coin flip (random side, same strikes, 500 copies, seed 20261023) ≤ 5%.

## C. Blend accuracy (2022-26, pooled)

- blend = w x E1_total + (1 - w) x close.
- **Fixed weights:** w ∈ {0.1, 0.2, 0.3}. **Primary w = 0.2.**
- **Fitted weight:** w_fit = the MAE-minimising w on a 0.05 grid over the 2020-21 + 2021-22 validation games that have
  a close. Fitted on validation only, then applied to 2022-26.
- 4 versions.
- **"The blend beats the line"** only if the paired game-bootstrap 95% CI of (blend MAE - close MAE) lies entirely
  below 0 (10,000 draws, seed 20261023).

## Overall

- **"E1 gives a betting edge"** requires both A-primary and B-primary to pass.
- **"E1 improves on the line's accuracy"** requires C-primary or C-fitted to pass. With two chances, the claim is
  reported with that count.
- Everything is reported regardless, by season too.

## Versions

10.

## Amendments

(none)
