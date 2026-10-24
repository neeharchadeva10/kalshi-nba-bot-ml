# Pre-registration — research log #53: resting (maker) orders on the near-miss ideas #48 and #49

Written by Opus on 2026-10-22 at about 19:40Z. The user chose "Resting orders on near-misses" when asking to go
deeper. #48 and #49 failed as taker strategies; their maker-execution versions have not been run. Changes go in
"Amendments".

## What is already known (disclosed, because it shapes this test)

- #48 discovery (taker at T-1h): the best eligible version, PTS 28+ min rotation, was -1.2c per bet. Luck test 19.4%.
  Only 2 of 24 versions reached n 60.
- #49 discovery (taker at T-1h): the best version was -0.25c. Luck test 45.2%. Team and 1H spreads were about
  14-16c.
- Both unseen halves are still unopened. Opus has seen both discovery results, so discovery here is NOT clean.
  Selection on it is labelled exploratory, and only the unseen run counts.

## Where the line-setter is weak (the idea)

Both ideas lost by roughly the cost of crossing the spread plus the taker fee. A resting buy at the bid pays no
spread and a quarter of the fee. The risk is adverse selection: resting orders fill most when the price is about to
move against them. The fill rule below is strict, and the outcomes carry the adverse selection.

## Data

- **#48 family:** the shared props cache, data/raw/kalshi_props_hourly/<date>/<ticker>.json.gz (hourly candles T-4h
  to T-1h, with yes bid/ask OHLC and trade price OHLC). Unseen is already downloaded.
- **#49 family:**
  - team and 1H totals from the raw hourly cache data/raw/kalshi_teamtotal_1h_hourly;
  - the game total from reports/output/nba_totals_pace/totals.sqlite (the raw JSON column).
  - The builder must confirm that trade price OHLC exists in these raw responses. If not, the #49 family is dropped
    by amendment before any result.
  - #49's unseen half is NOT downloaded. Download it (same scope and window as #49) only if a #49-family version is
    selected and passes the luck test.

## Execution rule (both families)

- **Post time E0:** the end of the earliest candle in the market's file. It must be ≤ T - 2h, so at least one later
  candle exists; otherwise skip (no_post_window).
- **Signal at E0:**
  - Uses only information at or before E0. #48: the last injury report published at or before E0. #49: the medians
    from candles at E0, with k = 0.5113 (frozen by #49).
  - Otherwise the signal, beneficiaries and strike choice are exactly #48's / #49's rules, evaluated at E0 instead of
    T-1h.
- **Limit price:**
  - YES buy: the yes_bid close at E0.
  - NO buy: 1 - the yes_ask close at E0.
  - It must be within 15-85c.
- **Fill (strict trade-through):**
  - The order fills only if a later candle ending at or before T-1h shows a trade strictly through the limit.
  - YES buy: price.low < limit. NO buy: price.high > 1 - limit.
  - A touch at the limit does not count (queue position unknown). Otherwise it is cancelled at T-1h with no bet.
- **Costs:**
  - The maker fee is 0.0175·P·(1-P) rounded up to $0.0001 if the series' fee_type is quadratic_with_maker_fees,
    otherwise 0. If fee_type is unknown, the fee is charged.
  - No slippage is added to a maker fill: it fills at the limit or not at all.
  - A +1c sensitivity line is reported, but not used for selection.
- 1 contract (#49 "other follows / team": 1 per leg), held to settlement. Non-binary "fair price" settlements are
  valued at their settlement value and counted.

## Versions (all counted)

#48's 24 (stat x M_star x beneficiaries x timing) + #49's 16 (pair x direction x |D|) = **40 versions**.

## Split, selection and luck test

- **#48 family:** discovery 2025-11-18 to 2026-01-22; unseen 2026-01-23 to the end of the regular season.
- **#49 family:** #49's amended split (discovery = the first half of the Feb-Jun 2026 games).
- **Selection:** the highest discovery LB = mean - 1.96·sd/√n among all 40 versions with n ≥ 60 FILLED bets.
- **Luck test (500 reps, seed 20261022, selection re-run over all 40):**
  - #48 family: the price-matched random over bet from #48 (another active player, same stat and night, limit
    within 2c), under the same post and fill rule;
  - #49 family: a random side per game under the same post and fill rule.
  - FAIL if more than 5% of reps reach the real best LB.

## Pass bar (the selected version on UNSEEN; all must hold)

1. Mean net > 0 and the bootstrap 95% LB (by game, 10,000 draws, seed 20261022) > 0.
2. At least 60 unseen filled bets.
3. The coin flip under the same fill rule: at most 5% of 500 copies do as well.
4. The discovery luck test passed.

## Reported regardless

- Fill rate by version; filled vs unfilled outcomes (adverse selection: the win rate of signals that did NOT fill).
- Maker fee_type per series; the +1c sensitivity.

## Versions

40.

## Amendments

(none)
