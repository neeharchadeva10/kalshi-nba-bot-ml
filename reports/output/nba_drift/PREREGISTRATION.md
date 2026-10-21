# Pre-registration — research log #24: does the pre-game price drift predictably? (when to bet)

Written 2026-10-20 ~18:30Z by Opus, **before any historical NBA price was downloaded or looked at**. The NFL data
(data/nfl_spread.sqlite) was used before for #17/#18/#19, but never for open-to-close drift. Today's only
observation was 26OCT20BOSDET: the opening lines had DET slightly favoured, and BOS firmed to about 53% by an hour
before tip. That observation prompted the question and isn't part of any result. Changes go in "Amendments" with a
date.

## Question

Can we predict the direction a Kalshi game-winner price moves between an early time and tip-off, by enough that
buying early beats the closing price after the spread and the exact fee? In other words, is there a better time
than "right before the game" to bet, and on which side?

## Data

- **NBA (primary):** every KXNBAGAME market of the 2025-26 season (regular season and playoffs) that Kalshi lists as
  settled, pulled read-only from the public API.
  - Each team's own market (YES = that team wins).
  - Hourly candlesticks from listing to tip-off, plus 1-minute candlesticks for the last 20 minutes before tip-off
    (yes_bid / yes_ask closes).
  - Results from the market result.
- **NFL (secondary):** KXNFLGAME in data/nfl_spread.sqlite (2025 and 2026 weeks 1-5). Same rules; separate verdict.
- **Tip-off** = event occurrence_datetime - 3 h (seen on 26OCT20BOSDET and 26OCT21; NFL: KXNFLGAME
  expected_expiration_time - 3 h).
  - Before any strategy is run, verify the rule on 20 random games: the mid's 5-minute absolute change must jump
    within ±15 min of the inferred tip-off on at least 18 of 20.
  - If it fails, stop and amend here before going on.
- **Exclusions:** games with no bid or no ask in a team's market at the entry or close time (counted and reported);
  postponed or voided games.

## Measurements (per game, per team market)

- **Entry** at E in {T-24h, T-6h, T-1h}: the last hourly candle closing at or before E (no look-ahead).
  - Entry price = yes_ask close. Entry mid = (bid + ask) / 2.
- **Close:** the last 1-minute candle closing at or before T-5 min. Close mid = (bid + ask) / 2.
- **Fee per contract at 10 contracts:** ceil(0.07 * 10 * P * (1-P) / 0.0001) * 0.0001 / 10.
- **Primary metric, CLV net:** close mid - entry ask - fee(entry ask). This is the closing-line value of a taker buy
  at E.
- **Secondary metrics:**
  - Hold to settlement: result - entry ask - fee.
  - Exit at close: close bid - entry ask - fee(entry) - fee(close bid).

## Strategies (which team to buy at E); every one is a version

- **V1 favourite:** the team whose entry mid > 0.5.
- **V2 underdog:** the team whose entry mid < 0.5.
- **V3 home team.**
- **V4 away team.** Home/away comes from the event title "X at Y" and is checked against the ticker.
- **V5 momentum:** the team whose mid rose over the 24 h before E. Skipped when that history is missing.
- **V6 reversal:** the team whose mid fell over the 24 h before E.
- **V7 Elo edge:** the team whose Elo probability exceeds its entry mid by at least 0.03.
  - Elo is built only from results of earlier games in the same data, with K = 20, home advantage 100 points, and
    1500 at season start.
  - The first 150 games (NBA) or first 2 weeks (NFL) are warm-up and not traded.

7 strategies x 3 entry times = **21 versions per sport, 42 in total.**

**Primary (named now):** NBA, V1 favourite, E = T-6h, CLV net.

## Split and pass bar (primary; all must hold)

- **Split by tip-off date.** Discovery = the first half of games, unseen = the second half, which includes the
  playoffs. Same for NFL.

1. Mean CLV net > 0 in discovery **and** unseen.
2. Unseen 95% bootstrap lower bound > 0. Resample whole games, 10,000 draws, seed 20261020.
3. At least 200 unseen trades.
4. **Coin flip:** at most 5% of 500 random-side copies do as well on unseen. Each copy uses the same games and entry
   time, buys a uniformly random team, and is scored the same way. Seed 20261020.
5. Unseen mean hold-to-settlement P&L per contract >= 0, as a sanity check against a mis-measured close.
6. At least **$2 per week** at 10 contracts: unseen mean CLV net x 10 x the unseen trades per week.

The other 41 versions are reported. Any that clear the bar are leads for a new pre-registration, never passes.

## Reported regardless

- Average absolute drift from E to close by entry time.
- Spread at each entry time.
- Calibration of the close mid against results (buckets, Brier) and of the entry mid.
- How often V1's favourite at E is still the favourite at close.
- NBA vs NFL.

## Amendments

**2026-10-20 ~19:40Z — tip-off definition and data scope.**
- **Before this amendment:**
  - The NBA download was complete and the tip-off check had failed (1 of 20).
  - **No NBA strategy, drift or calibration number had been computed or seen.**
  - The NFL secondary results had been seen: all 21 versions fail. They don't bear on the timing question below.
- **What the check showed** (reports/output/nba_drift/tipcheck*.json):
  - Kalshi's time minus 3 h is the **scheduled** start.
  - Real NBA play begins 12-15 min later (median volume onset +12 min, price onset +15 min), the usual delay
    between the listed time and the actual tip.
  - The largest 5-minute move comes 17-30 min after the scheduled start, so "jump within ±15 min" was the wrong
    check.
  - None of the 20 games showed a price move before the scheduled start.
- **Amended definitions:**
  - **T = scheduled start** = occurrence_datetime - 3 h, or expected_expiration_time - 3 h where occurrence_datetime
    is missing. The two are equal on all 82 events that have both, and only 82 of 1,363 events have
    occurrence_datetime.
  - Entry times and the T-5 min close are measured from T, so the close is pre-game.
  - **Validation (replaces the ±15 min check):** on the same 20 games, no sustained price move before T-5 min in at
    least 18 of 20. The diagnostic already shows 20 of 20.
- **Season scope:** "regular season and playoffs" means games with T on or after 2025-10-21, the 2025-26 opening
  night. Earlier games are preseason and are dropped. Elo starts at 1500 on 2025-10-21, and the warm-up is the first
  150 of these games.
- **Weeks for rule 6:** weeks with at least one game, so off-season weeks aren't counted (this applies to NFL too).
- The primary, the 42 versions, the split rule and the pass bar are **unchanged**.
