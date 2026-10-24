# Pre-registration — research log #54: do pregame NBA total line moves keep going (or reverse)?

Written by Opus on 2026-10-22 at about 19:40Z, before any pregame line move was compared with an outcome. The user
asked to go deeper on the failed strategies. This builds on #44.

## Where the line-setter is weak (the idea)

#44 found that the total barely moves when the official injury report comes out, so the news is priced earlier.
If the market under-reacts when news arrives, a line that moved in the hours before tip should keep going in the
same direction (follow). If it over-reacts, it should come back (fade).

**Who would pay:** whoever posted or took the stale side of the move.

## Known before writing

- #24: pregame moneyline drift on Kalshi was unpredictable by momentum, favourite, home or Elo. All 42 versions
  failed; the closing price was the best calibrated.
- #44 (discovery): line moves around injury-report times had a median of -0.04 points.
- No pregame total move has been compared with an outcome here.

## Data (already on disk, no download)

- reports/output/nba_injury_speed/candles.sqlite: 1-minute pregame KXNBATOTAL candles, every listed strike, T-8h to
  T. T = done.T (the scheduled start).
- **Implied total M(t):** the YES-mid 0.50 crossing across the ladder at time t, as #44.
  - Each strike uses its last candle ending at or before t, no older than 10 min.
  - Books need spread ≤ 0.15. If there is no crossing, M(t) is missing.

## Rule

- **Decision time D:** T - 60 min. Move = M(D) - M(D - w). Skip if either is missing.
- **Trigger:** |Move| ≥ X points.
- **FOLLOW:** move up → buy YES on the main strike at D; move down → buy NO. **FADE:** the opposite.
- **Main strike at D:** YES mid nearest 0.50, within 15-85c, ask - bid ≤ 0.10.
- **Costs:** taker, +1c slippage, fee 0.07·P·(1-P) rounded up to $0.0001, 1 contract, held to settlement.

## Versions (all counted)

w {2h, 4h} x X {1.5, 3.0} x direction {follow, fade} = **8 versions**. One bet per game per version.

## Split, selection and luck test

- **Discovery:** ET dates 2025-10-21 to 2026-01-22. **Unseen:** 2026-01-23 to 2026-04-12, run once for the selected
  version.
- **Selection:** the highest discovery LB = mean - 1.96·sd/√n among versions with n ≥ 60.
- **Luck test:** a random direction per game (same strike and candle), shared across versions, 500 reps, seed
  20261022, with the 8-version selection re-run. FAIL if more than 5% of reps reach the real best LB.

## Pass bar (the selected version on UNSEEN; all must hold)

1. Mean net > 0 and the bootstrap 95% LB (by game, 10,000 draws, seed 20261022) > 0.
2. At least 60 unseen bets.
3. Coin flip (random direction, same candles, 500 copies): at most 5% do as well.
4. The discovery luck test passed.

## Reported regardless

- The distribution of moves for w = 2h and 4h (discovery).
- Calibration: the final total minus M(D) regressed on Move (slope and se), by w. Discovery only.
- How far the line moves from D to T (D-to-close drift) vs Move: descriptive continuation.

## Versions

8.

## Amendments

(none)
