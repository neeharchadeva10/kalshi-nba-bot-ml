# Pre-registration — research log #40: early-season NBA overs on Kalshi's total line (forward test, 2026-27)

Written 2026-10-21 ~12:40Z (about 08:40 ET, before tonight's games) by Opus. The window is the user's choice
("First 2 weeks"). Changes go in "Amendments" with a date.

## Where the idea came from (known before writing)

- **#39 exploratory market check** (2025-26 discovery, already seen), Kalshi's T-1h median line by month:

  | Month | n | Overs | Actual - line |
  |---|---|---|---|
  | Oct 21-31, 2025 | 80 | 57.5% | +3.4 |
  | Nov | 218 | 48.2% | |
  | Dec | 198 | 50.0% | |
  | Jan | 164 | 40.2% | -3.5 |

- **Literature:**
  - Baryla et al. (2007) found NBA totals lines biased early in the season (the direction was not seen in the
    abstract).
  - Paine documents in-season scoring drift.
- This is **one season and 80 games**, which could be chance (about 1.4 standard errors).
- **2026-27 games seen so far:** only the 3 games on 2026-10-20, and only as an aggregate in #37's (invalid) forward
  look. No 2026-27 game from 2026-10-21 on has been played.

## Rule (1 version)

- **Games:** NBA regular-season games with KXNBATOTAL markets and a scheduled tip on **ET dates 2026-10-21 through
  2026-11-03**, 14 days.
  - The 2026-10-20 games are reported separately and never count.
  - Postponed games are dropped.
- **Decision time:** T - 1 h, where T = KXNBAGAME occurrence - 3 h, falling back to the market's own occurrence - 3 h
  (the #37 rule). If a game is moved and the quote would come after the real tip, the game is dropped.
- **Quote:** the hourly candle (yes_bid / yes_ask close) ending at or before T - 1 h, from the cached public
  candlestick endpoint (#37's downloader). It is valid if 0 < bid <= ask < 1.
- **Strike:** the strike whose mid is closest to 0.50 among strikes with mid in 15-85c. Ties go to the lower strike.
- **Bet:** buy 10 YES (over) at that ask as a taker and hold to settlement. Kalshi's result decides the outcome.
- **Fee:** 0.07 x 10 x P x (1 - P), rounded up per order to $0.000001 (#37's FeeModel). Net per contract =
  1(yes) - ask - fee / 10.

## Pass bar (all must hold)

1. Mean net per contract > 0.
2. 95% bootstrap lower bound of the mean net per contract > 0. Resample games, 10,000 draws, seed 20261021.
3. At least 80 bets.
4. **Coin flip:** at most 5% of 500 copies do as well. Each copy buys a random side (YES at ask or NO at 1 - bid) of
   the same strike in each game, with fees, held. Seed 20261021.

Realistic note: with about 100 bets near 50c, the lower bound is above 0 only if overs win about 60% or more. That
is a high bar for a small sample.

## Reported regardless

- **Win rate vs the average price** paid.
- **The same rule for each week separately.** This is information only.
- **The under side (NO at 1 - bid) at the same strike.** Kalshi's results settle both sides, so this costs nothing
  extra; it is not a version.
- **The 2026-10-20 games,** separately.
- **Skipped games and why.**

## Data and timing

- **No orders are placed.** This is a paper test from public data.
- **When it runs:** after every game in the window has settled, about 2026-11-04/05.
  - Download KXNBATOTAL definitions, results and T - 1h candles with #37's resumable downloader.
  - The nightly home-PC WebSocket recorder also captures these books, as a cross-check.
- **Versions:** 1.

## Amendments

- **2026-10-21 ~19:30Z (about 15:30 ET, before any game in the window), by Opus with the user's choice.** No
  window outcome exists yet.
  1. **New version, the UNDER (NO) side.**
     - Same games, decision time and strike.
     - Buy 10 NO at 1 - bid as a taker, hold. The fee is at the NO price.
     - It has the same pass bar (items 1-4), with its own coin flip. Versions are now **2**: over (as written) and
       under.
  2. **Why the under was added:** after #40 was written, an exploratory check of the private sportsbook file
     (data/raw/nba_odds_samples/OddsData.sqlite) gave this picture for the first 14 days of the season:
     - 2007-08 to 2023-24 (17 seasons): overs 45.5-46.1% at the main line (n about 1,700, CI 43.8-48.5%), unders
       53.2%;
     - the rest of the season: overs 49.9%.
     - Kalshi 2025-26 had overs at 55-56% (n = 103) for the same weeks, so the sources conflict.
     - Lower alt-line overs did worse in both sources at Kalshi's prices. The figures are in
       reports/output/nba_totals_tourney/explore/altlines_early.py.
  3. **The user's real $1 bets** (from 2026-10-21) are the under: a limit buy NO at the NO bid (maker), placed T-75 to
     T-45 min and cancelled if unfilled by tip - 15 min. The fill rate and the real P&L are reported next to both
     paper versions.
- **2026-10-21 ~19:50Z (about 15:50 ET, before any window game), by Opus with the user's choice ("Formula picks the
  side"; "it can either over or under").**
  1. **New version, FORMULA SIDE:** same games, decision time and strike, as a taker at the ask of the chosen side.
     - The side is OVER if the projected total > the strike, otherwise UNDER.
     - The projection is #39's **F1_R2_S1**, the user's averaging formula:
       - shared pace (P_A + P_B) / 2;
       - prior = the 2025-26 regular season (team_box_2026, turnover fix), league-shifted with n_lg / (n_lg + 100)
         to 2026-27 games before D;
       - current = 2026-27 regular-season games before D, weighted 0.5^(age/10), blended at k = 20;
       - the back-to-back adjustment c = -1.211 per team on 0 days' rest (#39's 2024-25 fit for F1_R2). Rest days
         come from KXNBAGAME event dates.
     - If no 2026-27 box scores are available, n = 0 and n_lg = 0, so the prior is used unshifted. This is
       recorded per night.
     - It has the same pass bar (items 1-4), with its own coin flip: a random side on the same strike.
  2. **Versions are now 3:** over, under and formula side.
  3. **Known before:** in the 2025-26 discovery half, side picks of this kind won 48.6-51.1% vs the median line
     (exploratory). This version has no evidence of an edge.
  4. **The user's real bets from 2026-10-21 follow this version:** 1 contract per game (the user's choice), a limit
     buy at the chosen side's bid, placed T-75 to T-45 min, cancelled if unfilled by tip - 15 min. The under-only
     real-bet plan in item 3 above is superseded before any bet was placed.
- **2026-10-21 ~20:00Z (about 16:00 ET, before any window game), by Opus: main-strike spread filter.**
  - The strike is the one whose mid is nearest 0.50 among strikes with mid 15-85c **and ask - bid <= 0.10**.
  - It applies to all 3 versions and to the live pick list, the Discord pings and the bot.
  - Reason: the #42 audit found that empty books (e.g. 0.01/0.99) can have a mid near 0.50. This was 5 of 660 in the
    2025-26 discovery half.
- **2026-10-21 ~20:35Z (about 16:35 ET, before any window game), by Opus with the user's choice ("lets do the
  search top formula ... over or under based on calculations and EV").**
  1. **New version 4, GRID WINNER:** #42's frozen winner, applied to 2026-27 (winner_frozen.json SHA-256
     9302be4e...). The formula:
     - A = w0.0 (defense only);
     - B = term-own pace, so team A points = D_B x P_B;
     - C = decay with a 5-game half-life;
     - k = 20;
     - no league shift;
     - rest categories 0 / 1 / 2 / 3+, with #42's 2024-25-fitted constants for this (A, C) pair: e = [-2.029, -0.810,
       -0.823]. Season openers count as 3+.
     - home/away split on;
     - no shrink.
  2. **2026-27 inputs:** prior = the 2025-26 regular season, current = 2026-27 regular-season games before D. Rest days
     come from KXNBAGAME event dates.
  3. **The bet:** side = sign(proj - strike), only if |proj - strike| >= 8 points.
     - Paper version: taker at ask + fee, 1 contract, hold. Same pass bar (items 1-4), with its own coin flip.
     - The formula's own P(win) = Phi(|proj - strike| / 19.235) and the EV at the limit price are shown for
       information. They are not a filter beyond the 8-point threshold.
  4. **Versions are now 4.**
  5. **Known before:** in #42 this exact strategy went 59.3% on 2025-26 discovery (n = 204, selected from 134,400)
     and **50.0% on the unseen half** (n = 170, -3.7c per contract; coin flip 52%). There is no evidence of an edge.
  6. **The user's real practice bets from 2026-10-21 follow version 4** (1 contract, limit at the chosen side's bid).
     Games under the 8-point threshold get no bet.
- **2026-10-21 ~20:50Z (about 16:50 ET, before any window game), by Opus with the user's choice ("lets do 4+ so we
  can test all forward from now on").**
  1. **New version 5:** the same as version 4 (the #42 frozen winner formula, applied to 2026-27), but it bets when
     |proj - strike| >= **4** points. The paper version is a taker at ask + fee, 1 contract, with the same pass bar
     (items 1-4) and its own coin flip.
  2. **Version 4 (8+) stays tracked.** Every game's projection is logged, so both thresholds come from the same
     snapshots. Versions are now **5**.
  3. **Known before:** on 2025-26 discovery, this formula at 4+ went 50.2% (n = 424, -3.4c per contract after costs).
     The 4+ threshold was never run on the unseen half.
  4. **The user's real practice bets follow version 5 from tonight:** 1 contract, limit at the chosen side's bid.
- **2026-10-21 ~21:05Z (about 17:05 ET, before any window game), by Opus with the user's choice ("lets do the instant
  fill"):** the user's real practice bets are a **limit order at the chosen side's ask** (an immediate taker fill,
  taker fee 0.07 x P x (1 - P)), 1 contract, placed when each game's window opens.
  - This now matches the paper version-5 execution exactly.
  - If it doesn't fill because the price moved up, cancel it; don't pay more. No version change.
