# Pre-registration — research log #43: bet Kalshi's NBA total toward DraftKings' line when they differ by 2+ points (forward)

Written 2026-10-21 ~20:20Z (about 16:20 ET) by Opus. The user's question was "are there any differences between
draftkings lines and kalshi lines for points total". User choices: **a free Odds API key**, and **a gap of 2+ points**.
Changes go in "Amendments" with a date.

## Known before writing (exploratory, already-seen 2025-26 data)

- **Data:** sportsbook OU from the private file (odds_2025-26; the book and timestamp are unknown, probably a close)
  vs Kalshi's T-1h median, 553 games.
- **The gap:** mean +0.26, MAE 1.72. The lines differ by 1+ points in 49% of games, 2+ in 29%, 3+ in 18%.
- **Accuracy is equal:** MAE vs actual is 14.99 (book) and 14.98 (Kalshi).
- **The book's side** of Kalshi's line won:

  | Gap | n | Book's side won | 95% CI |
  |---|---|---|---|
  | 1+ | 271 | 52.0% | 46-58% |
  | 2+ | 159 | 54.7% | 47-62.5% |
  | 3+ | 102 | 55.9% | 46-65.5% |

- **This is probably flattered:** the book line is likely later than T-1h.
- The script is reports/output/nba_totals_tourney/explore/book_vs_kalshi.py.

## Data (forward only, from the first night the snapshotter runs)

- **DraftKings:** The Odds API v4, the official service, with the user's free key (500 requests/month).
  - Request: GET /v4/sports/basketball_nba/odds?regions=us&markets=totals&bookmakers=draftkings&oddsFormat=american.
  - It is called **once each time a game's window opens** (T-75 min). All games are recorded per call, and the raw
    JSON is cached.
  - The DraftKings main total = its posted totals point (Over/Under) with prices.
- **Kalshi:** at the same moment, read from public orderbooks:
  - the main strike (mid nearest 0.50 among mid 15-85c with spread <= 10c, ties to the lower strike);
  - the median line (0.5-mid crossing, interpolated, as in #37).

## Rule

- **Gap** = DraftKings total - Kalshi median line, both read at the same snapshot. Bet only if |gap| >= 2.0.
- **Side:**
  - if the gap is positive (DraftKings higher), buy YES (over) on Kalshi's main strike;
  - if negative, buy NO (under).
- **Primary P&L: taker.** Buy 1 contract at the side's ask at the snapshot, plus 1c slippage, plus the taker fee at
  count 1. Hold to settlement.
- **Reported, not counted:**
  - maker at the bid, if a fill is confirmed by public trade prints at or below the bid before tip - 15 min;
  - the user's real fills, if any.

## Window and pass bar

- **Window:** runs until **150 qualifying games** or **2027-01-31**, whichever comes first. It is evaluated once.
  The stop rule uses counts only, never results.
- **Pass bar (all must hold):**
  1. Mean net per contract > 0.
  2. 95% bootstrap lower bound > 0. Resample games, 10,000 draws, seed 20261021.
  3. At least 100 bets.
  4. **Coin flip:** at most 5% of 500 copies do as well. Each copy buys a random side on the same strike in the same
     games, with the same costs. Seed 20261021.
  5. At least $2 per week at 10 contracts.
- **Reported:**
  - win rate vs price;
  - results by gap size (2-3 and 3+);
  - over vs under;
  - how often DraftKings' line moved toward or away from Kalshi before tip (if later snapshots exist);
  - API requests used.

## Versions

**1.**

## Amendments

(none)
