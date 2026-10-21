# Pre-registration — research log #35: buy the NBA favorite late in the 4th quarter (by the game clock)

Written 2026-10-21 ~06:10Z by Opus. User choices (2026-10-21):
- entry at **6:00 and 3:00 left in Q4**;
- price bands **70-92c and 80-95c**.

Changes go in "Amendments" with a date.

## Known before writing (no outcome or price of this test has been looked at)

- **Moshrefi (arXiv 2607.14430):** Kalshi moneylines (Mar-May 2026; 57% of the 23M trades are NBA) are well
  calibrated until the last 10 minutes before market close. Then high-priced sides win more often than their price.
  - Measured to close time, which a trader can't know. **This test uses the game clock instead.**
- **Bürgi, Deng & Whelan:** makers buying at 50c or more earned +2.6% (2021 to April 2025); contracts above 70c show
  small positive post-fee returns.
- **Angelini & De Angelis (2606.07811):** Kalshi NBA live prices pass through only about 0.64 of game-state changes.
  The pass-through is lowest (0.51) in clutch time.
- **Our own record:** NFL in-game dips and jumps (#18/#19) were priced right. BTC 90-95c favorites (#7) faded.

## Data (read-only, public or approved)

- **Kalshi KXNBAGAME,** every game from 2025-04-15 (the first NBA games on Kalshi, per Angelini) to 2026-06-30.
  - Markets and results.
  - **1-minute candles** (yes_bid close, yes_ask close, volume), from scheduled start - 10 min to settlement, via the
    cached public candlestick endpoints (historical tier when settled before the cutoff).
  - Public trade prints around each entry, for fill confirmation.
- **Play-by-play:** hoopR-nba-data `nba/pbp/parquet/play_by_play_{2025,2026}.parquet` (CC BY 4.0, ESPN-derived; the
  source the user approved for #31).
  - Per event: `wallclock` (UTC), `period`, game clock, score.
  - Games are matched to Kalshi by date and teams. Unmatched games are reported and dropped.
- **Scheduled start:** Kalshi occurrence time - 3 h, falling back to expected expiration - 3 h (the #24 / nba_drift
  rule). Regular season and playoffs are both included. Preseason is excluded: it has no pbp.

## Rule

- **Decision moment E_X.** t_X = the `wallclock` of the first pbp event with period = 4 and game clock <= X:00.
  - E_X = the first 1-minute candle end time at or after t_X + 60 s, giving the market at least a minute to see that
    game state.
  - If the game never reaches Q4 with X:00 or less in the pbp, it is skipped and counted.
- **Favorite.** At E_X, the team whose YES ask (that candle's yes_ask close) is inside the band. If neither team's ask
  is in the band, there is no trade.
- **Trade.**
  - Buy 10 YES of the favorite at that ask, as a taker. One trade per game per version.
  - Fee per order: ceil(0.07 * 10 * P * (1 - P) / 0.0001) * 0.0001, or the series' fee_type if it says otherwise.
- **Exit:** hold to settlement. Net per contract = 1(win) - ask - fee/10.
- **Fill check (reported both ways, and a pass rule below).**
  - "Confirmed" means public prints on that market with taker side YES-buy, in [E_X, E_X + 60 s], at or below the ask,
    totalling 10 or more contracts.
  - The unconfirmed version assumes the displayed ask filled.

## Versions

- X {6:00, 3:00} x band {70-92c, 80-95c} = **4 versions**.
- **Primary (named now): X = 6:00, band 70-92c.**

## Split

Games ordered by scheduled start. **Discovery** = the first half. **Unseen** = the second half.

## Pass bar (primary; all must hold)

1. Mean net per contract > 0 in discovery **and** unseen.
2. Unseen 95% bootstrap lower bound > 0. Resample games, 10,000 draws, seed 20261021.
3. At least 100 unseen trades.
4. **Coin flip, price-matched and earlier in the game:** at most 5% of 500 copies do as well on unseen.
   - Each copy replaces every real trade with a random minute from the same game in periods 1-3.
   - At that minute, either team's ask must be within 1c of the real entry price. That team is bought at that ask
     plus the fee and held.
   - Seed 20261021.
   - This separates "late in the game" from the general favourite effect.
5. At least **$2 per week** at 10 contracts, over unseen weeks with games.
6. **Confirmed fills:** on unseen trades with confirmed fills, mean net > 0, with at least 50 such trades.

## Reported regardless

- A plain random-entry coin flip (any minute and side within the band).
- Calibration: late-game win rate vs price buckets, and the same for periods 1-3.
- Returns by:
  - score margin at entry;
  - price bucket;
  - home vs away;
  - regular season vs playoffs;
  - overtime games.
- Spread (ask - bid) at entry.
- The share of games skipped and why.

## Amendments

- **2026-10-21 ~06:35Z (Opus), clarifications from the build** (no results computed):
  1. **Quote at E_X.**
     - E_X = the first whole-minute boundary at or after t_X + 60 s, the same for both teams.
     - Each team's quote = the close of that market's latest 1-minute candle with end <= E_X, carried forward. It is
       valid only if that candle ended within the last 10 minutes; otherwise the game is skipped as "stale_quote".
     - The same carry-forward rule applies to coin-flip candidate minutes.
     - Confirmed-fill window: [E_X, E_X + 60 s].
  2. **Both asks in band:** skipped and counted.
  3. **Bootstrap lower bound** = 2.5th percentile; the 5th is also reported.
  4. **Coin flip:** a uniform random minute in periods 1-3, then a random qualifying team.
     - A trade with no candidate is dropped from that copy.
     - The real mean is reported over all trades and over the trades that had candidates.
  5. **Weeks** = ISO weeks of the ET tip date, over unseen games.
  6. **Wallclock sanity:** a game is valid only if:
     - t_X is within [scheduled start + 1.25 h, scheduled start + 4 h];
     - and no wallclock steps backwards by more than 5 min within Q4 before t_X.
     - Otherwise the game is excluded as "bad_wallclock".
  7. **All-Star games** are unmatched and dropped.
