# Pre-registration — research log #55: sell 10-15c in-game NBA total long shots (unseen-only test)

Written by Opus on 2026-10-22 at 20:17Z. The user chose "Test it once on unseen" (2026-10-22). No unseen-period
price or outcome has been looked at for this rule. Changes go in "Amendments".

## Where it came from (a post-hoc look, disclosed)

#52's discovery calibration table (2025-10-21 to 2026-01-22, pooled over all strikes at T-1h, HT and Q3 end, with
correlated strikes within a game) showed:
- strikes with YES mid 10-15c hit 6.2% (mean mid 12.2c);
- strikes with YES mid 85-90c hit 90.7% (mean mid 87.3c).

That table was a "reported regardless" block, not a test. This rule was written after seeing it, so **discovery
numbers for this rule are in-sample and do not count**. Only the unseen run decides.

Looks so far in this family: #52's 12 versions plus its 8-bucket calibration table.

**Who would pay:** in-game buyers of mid-priced long shots (10-15c) on the totals ladder.

**Other looks at the unseen period:** #46's unseen run (Q1/HT/Q3 main-strike calibration) used the same in-game
candles. It looked only at the strike nearest 50c, never at the tails.

## Data (on disk, no download)

#52's sources and code (reports/output/nba_total_tails/tails_lib.py):
- reports/output/nba_ingame_overreaction/candles_ingame.sqlite;
- the hoopR pbp checkpoints;
- the #46 guards: bad_wallclock, stale_L0, in-game candles only.

## Rule (one version)

- **Checkpoints:** HT (first pbp event of Q3) and Q3 end (first pbp event of Q4). The decision candle is the first
  in-game candle ending at or after C + 60 s. T-1h is excluded: the pregame ladder has no strikes in band.
- **Over tail:**
  - Among strikes with YES mid in [0.10, 0.15), take the one with mid nearest 0.125 (ties: the lower strike).
  - Buy NO at 1 - yes_bid.
- **Under tail:**
  - Among strikes with YES mid in (0.85, 0.90], take the one with mid nearest 0.875 (ties: the higher strike).
  - Buy YES at yes_ask.
- The chosen strike needs ask - bid ≤ 0.10, yes_bid ≥ 0.01 and yes_ask ≤ 0.99. There is no fallback to another
  strike (as #52).
- **Costs:** taker, +1c slippage on the entry, fee 0.07·P·(1-P) rounded up to $0.0001 (#46 fee_taker), 1 contract,
  held to settlement. Scalar results are skipped and counted.
- **Pooled:** all bets (2 checkpoints x 2 tails, at most 4 per game) form ONE strategy.

## Test

- **Unseen:** ET dates 2026-01-23 to 2026-04-12, run ONCE, after an Opus leak audit creates AUDIT_OK.
- **Discovery** (2025-10-21 to 2026-01-22) is computed in the same run for reference only and labelled in-sample.

## Pass bar (unseen; all must hold)

1. Mean net per contract > 0, and the bootstrap 95% LB > 0 (resampling GAMES, 10,000 draws, seed 20261022).
2. At least 100 unseen bets (several per game; more than #52's 60 because the bets are correlated).
3. **Coin flip:** at the same strikes and candles, a random side (the rule's side, or the long shot at its ask + 1c +
   fee), 500 copies, seed 20261022. At most 5% reach the real mean.

There is no discovery luck test, because there is no selection among versions. The disclosure above stands in for
it.

## Reported regardless

- n, win rate, mean entry, mean net, LB by checkpoint and tail (descriptive; not used to pass).
- The unseen calibration table in the same 8 buckets as #52.
- Skips by reason; spreads; quote ages; worst losing streak and max drawdown.

## Versions

1 (family: #52 12 + this 1).

## Amendments

(none)
