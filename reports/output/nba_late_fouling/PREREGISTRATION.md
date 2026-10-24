# Pre-registration — research log #51: does the live NBA total misprice the last minutes (fouling vs blowouts)?

Written by Opus on 2026-10-22 at about 19:40Z, before any late-Q4 Kalshi price was compared with an outcome. The user
asked to go deeper on the failed strategies ("use these other strategies as a base and go deeper"). This builds on
#46. Changes go in "Amendments".

## Where the line-setter is weak (the idea)

#46 found the live total priced about right at quarter breaks. The end of a game is different. In a close game the
trailing team fouls on purpose, and free throws plus quick shots add points faster than the normal pace. In a
blowout, starters sit and the pace often slows. A live total that extrapolates the clock too smoothly would be too
low in close games and too high in blowouts.

**Who would pay:** live bettors and market makers pricing the last minutes off the average scoring rate.

## Known before writing

- #46 (discovery and unseen): calibration slopes at Q1 end, halftime and Q3 end were all about 0. No Q4-late quote
  has been examined.
- OT counts toward the total if the Kalshi rules say so. The builder records the rule text from the market
  definition and uses the Kalshi result as settlement either way.

## Data (already on disk, no download)

- reports/output/nba_ingame_overreaction/candles_ingame.sqlite: 1-minute in-game KXNBATOTAL candles, every strike,
  tip to about tip + 4h10m (yes bid/ask OHLC, volume, OI).
- data/raw/nba_open/play_by_play_2026.parquet (hoopR): period, clock, home/away score, wallclock.
- Event mapping and tips: reports/output/nba_injury_speed/events_list.csv and #46 common.py (reuse its helpers and
  guards).

## Definitions

- **Checkpoint C_R:** the wallclock of the first pbp event in period 4 whose clock shows ≤ R seconds remaining in
  the quarter. R ∈ {300, 180, 120}.
- **Margin** = |home_score - away_score| on that event.
- **Decision candle:** the first in-game candle ending at or after C_R + 60 s (as #46). In-game candles only.
- **Live main strike:** the strike whose YES mid is nearest 0.50 at the decision candle, with ask - bid ≤ 0.10.
- **Guards (from #46):** skip the game if its Q1 checkpoint is outside [tip + 20 min, tip + 80 min] (bad_wallclock),
  or if C_R is outside [tip + 100 min, tip + 220 min]; skip if no pbp match, no decision candle within 5 min, or no
  qualifying strike. Each skip is counted by reason.

## Bets

- **Close game** (margin ≤ M_close): buy YES (over) at yes_ask.
- **Blowout** (margin ≥ M_blow): buy NO (under) at 1 - yes_bid.
- Taker, +1c slippage on the entry price, fee 0.07·P·(1-P) rounded up to $0.0001 (#46 common.fee_taker), 1 contract,
  held to settlement. Net = 1(win) - entry - fee. Skip if the entry after slippage is ≥ 0.99 or ≤ 0.01.

## Versions (all counted)

R {300, 180, 120} x state {close ≤ 3, close ≤ 6, blowout ≥ 15, blowout ≥ 20} = **12 versions**. One bet per game
per version.

## Split, selection and luck test

- **Discovery:** ET dates 2025-10-21 to 2026-01-22. **Unseen:** 2026-01-23 to the end of the regular season
  (2026-04-12), run once for the selected version.
- **Selection:** the highest discovery LB = mean - 1.96·sd/√n (net per contract) among versions with n ≥ 60.
- **Luck test:** a random direction (YES or NO on the same strike and candle) per game and checkpoint, shared
  across versions, 500 reps, seed 20261022, with the 12-version selection re-run. FAIL if more than 5% of reps
  reach the real best LB. No unseen run then.

## Pass bar (the selected version on UNSEEN; all must hold)

1. Mean net > 0 and the bootstrap 95% LB (by game, 10,000 draws, seed 20261022) > 0.
2. At least 60 unseen bets.
3. Coin flip: random direction at the same candles, 500 copies; at most 5% do as well.
4. The discovery luck test passed.

## Reported regardless

- Calibration by R and margin bucket (0-3, 4-6, 7-10, 11-14, 15-19, 20+): mean of final total minus the live implied
  total (the YES-mid 0.50 crossing) at the decision candle.
- OT rate by margin bucket; free throws after C_R by margin bucket (descriptive).
- Spreads, decision lag after C_R + 60 s, quote age at the decision candle; a +2c slippage sensitivity line (not used
  for selection).

## Versions

12.

## Amendments

(none)
