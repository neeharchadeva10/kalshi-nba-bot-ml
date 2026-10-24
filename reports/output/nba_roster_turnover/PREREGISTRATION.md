# Pre-registration — research log #45: early-season lines for high roster-turnover NBA teams

Written by Opus on 2026-10-22 at about 06:40Z, before any line or outcome was split by roster continuity. Asked for
by the user ("trades ... think of yourself as DraftKings and counter that"). Changes go in "Amendments".

## Where the line-setter is weak (the idea)

A book's opening early-season numbers lean on last season's team ratings plus guessed offseason adjustments. A team
that turned over much of its roster (trades, free agency) has the least reliable prior. If books under-adjust, its
early lines are biased in a consistent direction. If they adjust but noisily, the lines are only less accurate, and
that can't be bet.

**Who would pay:** bettors and books still anchored to last season's version of the team.

## Known before writing

- Early-season under (all teams) was strong from 2007 to 2022 and reversed from 2022-23 on (exploratory, logged
  2026-10-22).
- Box-score formulas never beat Kalshi's T-1h total (#37-#42).
- Nothing has been computed by roster continuity.

## Data (free, on disk)

- **Rosters and minutes:** hoopR player_box 2022-2026 (data/raw/nba_open/player_box_{2022..2026}.parquet;
  season label = end year). This gives seasons 2021-22 to 2025-26. Continuity needs the previous season, so
  **test seasons are 2022-23 to 2025-26.**
- **Lines:** data/raw/nba_odds_samples/OddsData.sqlite (Spread, OU, ML_Home/ML_Away, Points, Win_Margin). These are
  probably closing lines. Private research only, never redistributed.
- **Kalshi 2025-26:** T-1h KXNBATOTAL main line and prices (reports/output/nba_totals_pace/totals.sqlite), as a
  real-price cross-check for totals.

## Definitions

- **Continuity of team T in season s.**
  - Take T's total minutes in season s-1 (regular season).
  - Continuity = the share of those minutes played by players who also play for T in any of T's first 3 games of
    season s.
  - Players traded during s-1 count for the team they played the minutes for.
  - The builder checks that the first-3-games rule uses only games before the bet's date, and drops the team's first
    3 games from the bet set, because their rosters define continuity.
- **Low-continuity team:** continuity in the bottom 20% (version) or bottom 33% (version) of the 30 teams that
  season.
- **Early window:** games with ET date within the first 14 days (version) or 30 days (version) of the season.

## Bets (sportsbook pricing at -110 on both sides; break-even 52.38%; one bet per game per version)

- **Spread, fade:** bet AGAINST the low-continuity team, i.e. on its opponent's spread. **Spread, back:** bet FOR the
  low-continuity team.
- **Total, under / total, over:** applies to games involving at least one low-continuity team.
- **Win** = covers or goes over/under the closing line. A push is refunded and excluded. Net per $1 risked:
  +0.9091 for a win, -1 for a loss.
  - This is the -110 cost. No better price is assumed. Closing lines are used as the price available (a slight
    optimism, disclosed).
- **Kalshi cross-check** (totals versions only, 2025-26): the same side on Kalshi's T-1h main strike, as a taker at
  the ask plus fee (0.07·P·(1-P), rounded up per order), 1 contract.

## Versions (all pre-registered; every one is counted)

market {spread, total} x direction {2 per market} x threshold {20%, 33%} x window {14, 30 days} x involvement
{the team in the game (spread: the low team; total: at least one low team), only games where the OPPONENT is
NOT low-continuity} = 2 x 2 x 2 x 2 x 2 = **32 versions**.

## Split, selection and luck test

- **Discovery:** 2022-23 and 2023-24. **Unseen:** 2024-25 and 2025-26, run once for the selected version.
- **Selection:** the version with the highest discovery lower bound LB = mean - 1.96·sd/√n (net per $1), among
  versions with n ≥ 40.
- **Luck test on discovery:** shared random sides, 500 reps, seed 20261022. In each rep every game gets one random
  side and the same 32-version selection is re-run.
  - Report the share of reps whose best LB is at least the real best.
  - If it is more than 5%, the winner is "indistinguishable from luck" and the test FAILS, without opening the
    unseen half. (This is the #42 discipline.)

## Pass bar (the selected version on UNSEEN; all must hold)

1. Mean net > 0 and the bootstrap 95% LB (by game, 10,000 draws, seed 20261022) > 0.
2. At least 40 unseen bets.
3. Coin flip: random sides on the same unseen games, 500 copies; at most 5% do as well.
4. The discovery luck test (above) was passed.
5. For a totals version: the Kalshi 2025-26 cross-check mean net is not below -2c per contract. Information if
   n < 20.

## Reported regardless

- The accuracy story: mean absolute error of the line vs continuity tercile, by season.
- The direction of the bias: mean (actual - line) by tercile.
- Continuity values per team-season.

## Versions

32.
