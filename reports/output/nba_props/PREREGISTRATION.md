# Pre-registration — research log #36: NBA player-prop model vs Kalshi (points, rebounds, assists, threes)

Written 2026-10-21 ~06:10Z by Opus. User choices (2026-10-21):
- "thinner markets";
- decision **1 hour before tip**;
- **15-85c primary, all prices as a version**.

The model must first pass the `/model-test` gates (.claude/skills/model-test/SKILL.md) on discovery data, before any
betting rule is run. Changes go in "Amendments" with a date.

## Known before writing (no prop price or outcome comparison has been looked at)

- **Research survey** (reports/Beatable NBA and weather markets.md): no rigorous after-cost study of NBA props exists.
  The "props are soft" claims are anecdotal.
- **Our pregame winner models #29/#31/#32 failed.** Kalshi's moneyline was more accurate than every one, and injuries
  added almost nothing there.
- **#29 data:** KXNBAPTS 2025-26 markets, tickers and results (no prices) were downloaded. Players were listed for
  about 1,096 games.
- **Bürgi et al.:** contracts at 10c or less lose more than 60% after fees, hence the 15-85c primary.

## Data (read-only)

- **Kalshi** KXNBAPTS, KXNBAREB, KXNBAAST, KXNBA3PT, every 2025-26 market (Nov 2025 - Jun 2026):
  - definitions (player, team, strike = N+), results;
  - **hourly candles** (yes_bid / yes_ask close) through the candle ending at or before **T - 60 min**;
  - public cached GETs.
  - T = scheduled start (occurrence - 3 h, as #24).
  - Markets settled at a "fair price" (player inactive) are excluded and counted.
- **hoopR-nba-data player_box 2022-2026** (already in data/raw/nba_open): minutes and the four stats per game.
- **Official NBA injury reports**, parsed in #32 (reports/output/nba_model/injury_parsed_v3.json). For each game, the
  latest report published **at or before T - 60 min**.
- **Player matching:** Kalshi ticker player key and name to hoopR athlete, by name + team. Unmatched players are
  reported and dropped.

## Model (per player, game and stat; only information before the decision)

Features:
1. **Per-minute rate** over the player's last 20 games played (any season), shrunk toward his current-season rate
   with weight n/(n+10).
2. **Projected minutes:** mean minutes over his last 10 games played.
3. **Teammates out:** sum of the rolling per-game averages of the same stat for rotation teammates (10+ mpg over
   their last 10) listed Out or Doubtful on the decision-time report.
4. **Own status:** Questionable or Probable flag. Players listed Out are not bet.
5. **Opponent allowed:** opponent's stat allowed per game over its last 15 games, relative to the league average to
   date.
6. **Home, rest days** (capped at 4) **and back-to-back.**

**Distribution:**
- A negative-binomial regression (log link) for the stat count, fitted on discovery games only, with one dispersion
  per stat.
- P(stat >= N) comes from the fitted NB.
- The Elo-style "team" features of #29-#32 are not used.

## Gates (/model-test, discovery only; two runs, 15-85c and all prices)

1. Model Brier at or below the Kalshi T-1h **mid** Brier on the same markets, or within 0.002.
2. **Residual test:** outcome ~ logit(mid) + (logit(model) - logit(mid)). The disagreement coefficient must be > 0,
   with a 95% CI (bootstrap by game) excluding 0.
3. At least 20 markets per week where |model - mid| exceeds half the spread plus the fee.

If any gate fails, **stop**: the result is "model not better than market", and no betting rule is run.

## Betting rule (only if all gates pass)

- **Buy YES** if p - ask - fee(ask) >= theta. **Buy NO** if (1 - p) - (1 - bid) - fee(1 - bid) >= theta.
- One side per market, count 10, hold to settlement. Fee at count 10 per contract, per Kalshi rounding.
- **Price filter** on the price paid: 15-85c (primary) or all prices.

## Split

Games by scheduled start: **discovery** = the first half, **unseen** = the second half.

## Versions

- Gate runs: 2 (15-85c, all).
- Betting: theta {3c, 6c} x price range {15-85c, all} = 4.
- **6 versions in total.** **Primary: theta = 3c, 15-85c.**

## Pass bar (primary betting rule; all must hold)

1. Mean net per contract > 0 in discovery and unseen.
2. Unseen bootstrap lower bound > 0. Resample games, 10,000 draws, seed 20261021.
3. At least 200 unseen bets.
4. **Coin flip:** at most 5% of 500 copies do as well on unseen. Each copy uses the same markets and count, a random
   side, the same price filter and the same fees. Seed 20261021.
5. At least $2 per week at 10 contracts.
6. Unseen model Brier at or below the market's, or within 0.002.

## Reported regardless

- By stat type, by strike distance from the player's mean, by price bucket, and by teammates-out games.
- Calibration of model vs market.
- Spread at T-1h.
- Match and coverage counts.
- Exclusions: inactive, fair-price settlements, no quote.

## Amendments

(none)
