# Pre-registration — research log #47: the favourite-longshot bias in Kalshi NBA and NFL moneylines (pregame)

Written by Opus on 2026-10-22 at about 06:55Z, before any calibration of these prices was computed for this test.
The idea is from the user's slate ("Favorite-longshot bias"). Changes go in "Amendments".

## Where the line-setter is weak (the idea)

Recreational bettors overpay for long shots and underpay for favourites in many betting markets. That is the
favourite-longshot bias. On an exchange with no bookmaker, the "line-setter" is the crowd. If the bias exists on
Kalshi game winners, buying favourites (or selling long shots, i.e. buying NO on them) wins after fees.

**Who would pay:** long-shot buyers.

## Known before writing

- **Bürgi, Deng & Whelan (2021 to April 2025, Kalshi):** buying at 50c or more earned about +2.6% for makers. Takers
  are mostly negative after fees. Contracts above 70c show small positive post-fee returns.
- **Moshrefi:** Kalshi moneylines are calibrated until the last 10 minutes before close.
- **Our BTC 15-minute 90-95c favourites (#7)** faded on later data.
- **Our #24 (when to bet)** failed.
- No calibration of these KXNBAGAME/KXNFLGAME pregame prices has been done for this question.

## Data (on disk; no download needed for the primary)

- **NBA:** KXNBAGAME hourly candles (data/nba_drift.sqlite), 2025-10 to 2026-06, with results.
- **NFL:** KXNFLGAME candles (data/kpm.sqlite price_history, about 60 s) and quotes
  (data/nfl_spread.sqlite), 2025 and 2026 seasons, with results.
- **Price:** the yes_ask close (taker) and yes_bid close of the last candle ending at or before decision time D.
  Fee 0.07·P·(1-P), rounded up to $0.0001 per 1-contract order. Slippage +1c on the ask (the candle close may be
  stale).

## Rule

At D, for each game, look at both teams' YES contracts.

- **FAV:** buy YES on the team whose ask is in the band (one per game).
- **LONG-NO:** buy NO on the team whose YES bid is in the long-shot band, at the NO ask = 1 - YES bid.

Hold to settlement. Net = 1(win) - price - 1c - fee.

## Versions (all counted)

sport {NBA, NFL} x D {T-24h, T-3h, T-1h} x side and band
{FAV 55-70, FAV 70-85, FAV 85-95, LONG-NO 5-15, LONG-NO 15-30} = 2 x 3 x 5 = **30 versions**.
T = scheduled start (Kalshi occurrence - 3 h, the repo rule).

## Split, selection and luck test

- **NBA:** discovery 2025-10-21 to 2026-01-22; unseen 2026-01-23 to 2026-06-30.
- **NFL:** discovery = 2025 regular season and playoffs; unseen = 2026 season to date (games settled by the run
  date).
- **Selection:** the highest discovery LB = mean - 1.96·sd/√n among versions with n ≥ 60.
- **Luck test:** price-matched random side, 500 reps, seed 20261022. In each rep each bet is replaced by a random
  contract (any team, YES or NO) at that sport and time whose entry price is within 2c of the real one, and the
  30-version selection is re-run.
  - FAIL if more than 5% of reps reach the real best LB.

## Pass bar (the selected version on UNSEEN; all must hold)

1. Mean net > 0 and the bootstrap 95% LB (by game, 10,000 draws, seed 20261022) > 0.
2. At least 60 unseen bets.
3. Price-matched coin flip on unseen: at most 5% of 500 copies do as well.
4. The discovery luck test passed.

## Reported regardless

- Calibration tables: win rate vs price bucket, by sport and decision time, for discovery and unseen.
- The maker (bid) version as information only: fills can't be confirmed, so it can't pass.

## Versions

30.
