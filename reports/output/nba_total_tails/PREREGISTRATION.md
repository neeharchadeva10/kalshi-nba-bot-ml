# Pre-registration — research log #52: are far-out NBA total lines (long shots) overpriced on Kalshi?

Written by Opus on 2026-10-22 at about 19:40Z, before any far-strike price was compared with an outcome. The user
asked to go deeper on the failed strategies. This builds on #47 (favourite-longshot on moneylines, FAIL).

## Where the line-setter is weak (the idea)

Long shots are often overpriced because buyers like cheap lottery tickets. #47 found no such bias on moneylines.
The totals ladder has its own long shots: strikes the total will probably not reach (cheap YES) or will almost
surely reach (cheap NO). Before the game Kalshi lists only about 11 strikes spanning about 30 points, so most real
long shots appear during the game, as the ladder drifts away from the live total.

**Bet:** sell the long shot, i.e. buy the likely side at 90c-99c.

**Who would pay:** live and pregame buyers of cheap far-out tickets.

## Known before writing

- #47: no favourite-longshot edge on NBA/NFL moneylines after costs (30 versions, luck test 83%).
- Weather #2 (2026-10-19): a long-shot bias existed but was smaller than spread plus fee.
- Bürgi et al.: Kalshi contracts at 10c or less lose more than 60% after fees on average, across categories.
- No totals-ladder tail price has been compared with an outcome here.

## Data (already on disk, no download)

- **T-1h, pregame:** reports/output/nba_injury_speed/candles.sqlite (1-minute, every listed strike, T-8h to T).
  Quote = the last candle ending at or before T - 60 min, where T = done.T (the scheduled start).
- **In-game:** reports/output/nba_ingame_overreaction/candles_ingame.sqlite plus the hoopR pbp checkpoints and the
  guards of #46 (bad_wallclock, stale_L0, in-game candles only).
  - **HT:** first pbp event of Q3. **Q3 end:** first pbp event of Q4.
  - The decision candle is the first candle ending at or after C + 60 s.

## Bets (per game, checkpoint and tail: at most one bet)

- **Over tail** (the total probably won't reach the strike): among strikes with YES mid in [0.01, X], take the one
  with the LARGEST YES mid. Buy NO at 1 - yes_bid. Requires yes_bid ≥ 0.01.
- **Under tail** (the total will almost surely reach it): among strikes with YES mid in [1 - X, 0.99], take the one
  with the SMALLEST YES mid. Buy YES at yes_ask. Requires yes_ask ≤ 0.99.
- Every chosen strike needs ask - bid ≤ 0.10.
- **Costs:** taker, +1c slippage on the entry, fee 0.07·P·(1-P) rounded up to $0.0001, 1 contract, held to
  settlement. Skip if the entry after slippage is ≥ 0.995.

## Versions (all counted)

checkpoint {T-1h, HT, Q3 end} x band X {0.05, 0.10} x tail {over, under} = **12 versions**.

## Split, selection and luck test

- **Discovery:** ET dates 2025-10-21 to 2026-01-22. **Unseen:** 2026-01-23 to 2026-04-12, run once for the selected
  version.
- **Selection:** the highest discovery LB = mean - 1.96·sd/√n among versions with n ≥ 60.
- **Luck test:** at the same strike and candle, a random side (the favourite as in the rule, or the long shot at its
  ask + 1c + fee), shared per game, checkpoint and tail, 500 reps, seed 20261022, with the 12-version selection
  re-run. FAIL if more than 5% of reps reach the real best LB.

## Pass bar (the selected version on UNSEEN; all must hold)

1. Mean net > 0 and the bootstrap 95% LB (by game, 10,000 draws, seed 20261022) > 0.
2. At least 60 unseen bets.
3. Coin flip (random side at the same strike and candle, 500 copies): at most 5% do as well.
4. The discovery luck test passed.

## Reported regardless

- Calibration table, discovery only: YES mid bucket (0-2, 2-5, 5-10, 10-15, 85-90, 90-95, 95-98, 98-100c) vs the
  realised YES rate, pooled over all strikes and checkpoints (descriptive; not a version).
- Counts per checkpoint of games with any strike in each band; spreads in the tails.
- How often the tail loses: worst-case loss streak of the selected version in discovery.

## Versions

12.

## Amendments

(none)
