# Pre-registration — research log #61: is the SHAPE of Kalshi's pregame NBA total ladder right (too narrow or too wide)?

Written by Opus on 2026-10-23 at 02:09Z. The user chose this idea ("2") after reasoning that a model which only gets
the middle right can't beat the line. Changes go in "Amendments".

## Idea

Each game's KXNBATOTAL ladder (about 11 strikes, about 3 points apart) implies a whole distribution of final totals,
not just a median.
- If the market thinks totals are more predictable than they are (too narrow), strikes far from the median are priced
  too cheap. Buying the tails wins.
- If it thinks they are less predictable (too wide), the tails are too expensive. Selling them wins.

This needs no forecast of the middle.

**Who would pay:** traders who price far strikes off an assumed spread rather than the true about 18-point spread of
NBA totals.

## Known before writing (disclosed)

- **The realised spread of finals around the Kalshi T-1h median,** 2025-26 (from #56/#59 work): MAE about 14.6, which
  implies an SD of about 18.
- **#52:** at T-1h, strikes priced ≤ 10c almost never exist (the ladder spans about ±15 points).
- **#52's pooled calibration table** (T-1h + HT + Q3 strikes) showed 10-15c strikes hitting 6.2% and 85-90c hitting
  90.7%. Mostly in-game; pregame-only tails were not separated.
- **#55** (in-game 10-15c tails, sell): FAIL on unseen.
- No pregame 10-35c tail strategy has been tested.

## Data (on disk, no network)

- **Primary:** reports/output/nba_injury_speed/candles.sqlite. 1-minute KXNBATOTAL candles for every listed strike,
  T-8h to T, 2025-26. Results are in `mkts.result`. T = done.T (the scheduled start).
- **Cross-check source:** reports/output/nba_totals_pace/totals.sqlite, hourly. The T-1h quotes from both tables are
  compared per strike (median |mid difference|, share within 1c). Strikes differing by more than 3c are listed;
  their games are excluded from both.

## Decision-time ladder (D = T - 60 min)

- **Each strike:** the last 1-minute candle ending at or before D, no older than 30 min, ask - bid ≤ 0.15.
- **Market median M:** the YES-mid 0.50 crossing (#44's implied_total).
- **Implied normal:** fit μ and σ by least squares of 1 - Φ((K - μ)/σ) to the YES mids of strikes with mids in
  (0.02, 0.98). At least 4 strikes are needed, else no fit.

## A. Measurement (reported regardless; discovery shown first, unseen only after the test)

- The median implied σ_mkt.
- The SD of z = (final - μ)/σ_mkt: SD > 1 means too narrow, SD < 1 too wide.
- **Calibration by distance:** per strike, d = K - M in points, bucketed {-15..-12, -12..-9, -9..-6, -6..-3, -3..0,
  0..3, 3..6, 6..9, 9..12, 12..15}. Mean YES mid vs the realised YES rate, n strikes and n games.

## B. Betting rule (versions)

- For each game: band b ∈ {[6, 10), [10, 16)} points from M; tail ∈ {high: K ≥ M + b, low: K ≤ M - b}.
- Take the one strike in the band nearest the band's centre (8 or 13 points). It must have spread ≤ 0.10.
- **Direction:**
  - **buy-tail:** buy the cheap side. High tail: YES at yes_ask. Low tail: NO at 1 - yes_bid.
  - **sell-tail:** the opposite side.
- **Costs:** taker, +1c slippage on the entry, fee 0.07·P·(1-P) rounded up to $0.0001, 1 contract, held to
  settlement. Skip an entry ≥ 0.99.

band {2} x tail {2} x direction {2} = **8 versions**. At most one bet per game per version.

## Split, selection and luck test

- **Discovery:** ET dates 2025-10-21..2026-01-22. **Unseen:** 2026-01-23..2026-04-12 (regular season), run once
  after an Opus audit (AUDIT_OK with code and input hashes, a start marker).
- **Selection:** the highest discovery LB = mean - 1.96·sd/√n among versions with n ≥ 60.
- **Luck test:** at the same strike and candle, a random direction per game x band x tail, 500 reps, seed 20261023,
  with the 8-version selection re-run. FAIL if more than 5% of reps reach the real best LB.

## Pass bar (the selected version on UNSEEN; all must hold)

1. Mean net > 0 and the game-bootstrap 95% LB > 0 (10,000 draws, seed 20261023).
2. n ≥ 60.
3. Coin flip (random direction at the same strikes, 500 copies) ≤ 5%.
4. The discovery luck test passed.

A pass would then go to a forward paper test on 2026-27 games before any money.

## Versions

8.

## Amendments

(none)
