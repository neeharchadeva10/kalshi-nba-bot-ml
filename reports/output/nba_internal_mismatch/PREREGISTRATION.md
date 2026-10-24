# Pre-registration — research log #49: Kalshi's own NBA total markets disagree with each other

Written by Opus on 2026-10-22 at about 07:25Z, before any team-total or first-half-total price was downloaded or
compared. The idea is from the user's slate ("Kalshi internal mismatch"). Changes go in "Amendments".

## Where the line-setter is weak (the idea)

Kalshi prices the game total (KXNBATOTAL), each team's total (KXNBATEAMTOTAL) and the first-half total (KXNBA1HTOTAL)
as separate order books, with different traders and liquidity.

- The sum of the two team-total medians should roughly equal the game-total median.
- The first-half median should be roughly a stable fraction of the game median.

When they disagree, at least one is off. The thinner book is the likelier culprit.

**Who would pay:** whoever is resting stale orders in the less-watched book.

## Known before writing

- Nothing on these three series together. #41 is a forward test of a formula on team totals (2026-27); it doesn't
  overlap.

## Data (free; official)

- **KXNBATOTAL:** T-1h candles already cached (reports/output/nba_totals_pace/totals.sqlite).
- **KXNBATEAMTOTAL and KXNBA1HTOTAL:** definitions and hourly candles T-4h to T-1h, from the historical market
  candlestick endpoint, raw responses cached.
  - Download scope: for each game, only strikes within ±8 points of the expected value (team: half the game-total
    median at T-1h; 1H: 0.5 x the game-total median). Both are known at decision time.

## Definitions

- **Medians** (at T-1h, last hourly candle at or before; books with spread ≤ 0.15): implied 0.50 crossings
  M_game, M_home, M_away and M_1H, as in #44.
- **Team mismatch:** D_team = (M_home + M_away) - M_game.
- **Half mismatch:** D_1H = M_1H - k·M_game, where k = the median of M_1H / M_game over DISCOVERY games. k is fitted
  once on discovery and frozen.
- **Bets** (taker at the ask + 1c + fee, rounded up per order, 1 contract per leg, held to settlement):
  - **"game follows":** bet the game total toward the other market. If D_team > 0, buy YES on the game total's main
    strike; if < 0, NO. Same for D_1H.
  - **"other follows":** bet the other market toward the game total. Team: 1 contract on EACH team total, on the side
    that reduces |D|. 1H: the 1H main strike.
  - The main strike is the mid nearest 0.50 within 15-85c, with spread ≤ 0.10.

## Versions (all counted)

pair {team, 1H} x direction {game follows, other follows} x threshold |D| ≥ {1, 2, 3, 4} points = **16 versions**.
One bet set per game per version.

## Split, selection and luck test

- **Discovery:** ET dates 2025-10-21 to 2026-01-22. **Unseen:** 2026-01-23 to the end of the regular season,
  run once for the selected version.
- **Selection:** the highest discovery LB = mean - 1.96·sd/√n (net per contract) among versions with n ≥ 60.
- **Luck test:** shared random side per game, 500 reps, seed 20261022, with the 16-version selection re-run.
  - FAIL if more than 5% of reps reach the real best LB.

## Pass bar (the selected version on UNSEEN; all must hold)

1. Mean net > 0 and the bootstrap 95% LB (by game, 10,000 draws, seed 20261022) > 0.
2. At least 60 unseen bets.
3. Coin flip on unseen (random side, same markets and times): at most 5% of 500 copies do as well.
4. The discovery luck test passed.

## Reported regardless

- The distributions of D_team and D_1H, and how often each market alone ended closer to the final.
- Liquidity (spread) of each book at T-1h.

## Versions

16.

## Amendments

- **2026-10-22 06:35Z (Opus), new split forced by the data.** No team-total or 1H price or outcome had been analysed.
  - The builder found that KXNBATEAMTOTAL and KXNBA1HTOTAL have **no markets before February 2026**. They have 529
    and 523 events, Feb to Jun 2026, matching #41's note that team totals start 2026-02-10. So the original
    discovery window (2025-10-21 to 2026-01-22) is empty.
  - Seen before this amendment: two unseen-period price probes, and aggregate yes/no counts of the listing, which
    were not analysed by game or version.
  - **New split:**
    - All games with KXNBATOTAL plus at least one of the two other series, ET dates 2026-02-01 to 2026-06-30, regular
      season and playoffs.
    - Ordered by scheduled start. **Discovery = the first half of games; unseen = the second half**, run once.
  - **Everything else is unchanged:** k is fitted on the new discovery half only and frozen, plus the 16 versions,
    n ≥ 60 for selection, the luck test, the pass bar and the costs.
  - Also reported: discovery and unseen by regular season vs playoffs.
