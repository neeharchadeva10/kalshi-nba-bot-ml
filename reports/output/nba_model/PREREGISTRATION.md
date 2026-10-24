# Pre-registration — research log #29: an NBA game-winner model (strength, rest, home, key-player availability)

Written 2026-10-21 ~03:10Z by Opus, at the user's direction ("find out who is going to win the next game based on
backtesting and data with everything considered like injuries, absences of key players and trades").

**Seen before this was written:**
- KXNBAGAME 2025-26 prices and results (#24: the market's T-5min Brier was 0.1974);
- the fact that KXNBAPTS historical markets exist (ticker = game + team + player, strike ladders, results).

**No prop market data had been downloaded or analysed.** Changes go in "Amendments" with a date.

## Question

Can a model built only from information available before tip-off predict NBA winners better than Kalshi's pregame
price, by enough that buying where the model disagrees beats the ask plus the fee?

## Data (all Kalshi, public, read-only)

- **Games:** the 2025-26 regular season and playoffs, T >= 2025-10-21 (as #24's amendment), from data/nba_drift.sqlite:
  KXNBAGAME result, hourly candles and the T-1h ask.
- **Margins:** from KXNBASPREAD / KXNBAWINMARGIN results (the winning margin bucket), for strength ratings.
- **Players:** from KXNBAPTS historical markets.
  - Per game: which players had a points market (the "listed" set) and each player's team code from the ticker.
  - Points scored, approximated as the highest strike settled YES (0 if none).
- **Look-ahead rule:** every feature for game g uses only games with T before game g, plus game g's own listing,
  which is published before tip-off.

## Features (per game, home minus away where it applies)

1. **Elo:** K=20, home advantage 100, start 1500 on 2025-10-21, margin-of-victory multiplier as FiveThirtyEight
   NBA.
2. **Rolling net rating:** mean point margin over each team's last 10 games, using margins from the spread / margin
   buckets.
3. **Rest:** days since each team's previous game, capped at 4; back-to-back flag.
4. **Availability:**
   - A team's regulars = players listed in at least 5 of the team's last 10 games before g.
   - Missing points = sum of the rolling average points (last 10 games played) of regulars **not listed** for g.
   - Players listed for a team for the first time count as additions. Trades are covered automatically, because the
     team code moves with the player.
5. **Home flag** (the model learns the size).

## Model and betting rule

- **Logistic regression**, fitted on discovery games only. The Elo-only model uses feature 1 only.
- **Bet:** at T-1h, for each team, edge = model probability - Kalshi YES ask - fee(ask) at 10 contracts. Buy the
  team when edge >= theta. At most one bet per game (the larger edge). Hold to settlement.
- **Split:** discovery = the first half of games by T, unseen = the second half (which includes the playoffs). This
  is the same split date as #24.

## Versions

- theta in {2c, 4c, 6c} x model in {full, Elo-only} = **6 versions**.
- **Primary (named now):** full model, theta = 4c.

## Pass bar (primary; all must hold)

1. Mean net P&L per contract > 0 in discovery **and** unseen.
2. Unseen 95% bootstrap lower bound > 0 (resample games, 10,000 draws, seed 20261021).
3. At least 100 unseen bets.
4. **Coin flip:** at most 5% of 500 copies do as well on unseen. Each copy keeps the same games and number of bets,
   with a random side, the same price rules and the same fees. Seed 20261021.
5. At least $2 per week at 10 contracts (about 50 games per week; unseen weeks with games).
6. **Model quality check:** the model's unseen Brier score <= Kalshi's T-1h Brier on the same games, or within 0.002
   of it while the betting rules still pass. If the model is much worse than the market overall, apparent edges are
   noise.

The other 5 versions are reported. Any that clear the bar are leads for a new pre-registration only.

## Reported regardless

- Brier and log loss for model, market and an average of the two.
- Calibration buckets.
- Feature weights.
- Bets and P&L by edge size and by "key player missing" games.
- How often the availability feature disagrees with the market.

## Amendments

(none)
