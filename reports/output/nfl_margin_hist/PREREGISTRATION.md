# Pre-registration — research log #20 (historical, minute-level): NFL win-margin buckets vs the deep markets

Written 2026-10-20 ~05:15Z by Opus, **before any historical win-margin price was looked at**. So far we know only
that 651 settled KXNFLWINMARGIN markets exist (2026 Sep-Oct, about 93 games) plus 7 from Feb 2026. That comes from a
count query; no prices were read. The live game WAS @ SF (26OCT19WASSF) motivated this test and is excluded.
Amendments go at the bottom, dated, without editing this text.

## Question

At one-minute resolution, do win-margin buckets trade away from the fair value implied by the deep winner, tie and
spread markets? Does taking those prices make money after exact fees? This is the slow cousin of #22: it can only
see gaps that last a minute or more, which the WAS @ SF data says some do (recurring gaps of up to about 2 minutes).

## Data

- **Games:** every 2026 regular-season NFL game with KXNFLWINMARGIN markets, kickoff from 2026-09-01 to 2026-10-19,
  **excluding 26OCT19WASSF**. The Feb 2026 Super Bowl is excluded (different season and context).
- **Split by kickoff date:**
  - **Discovery** = kickoff before 2026-10-01.
  - **Unseen** = kickoff on or after 2026-10-01.
  - The test runs only if unseen has at least 25 games.
- **Markets per game:** the 7 KXNFLWINMARGIN buckets, both KXNFLGAME sides, KXNFLTIE, and every KXNFLSPREAD strike.
- **Candles:** 1-minute candlesticks (yes bid/ask close) from the public API, cached raw.
  - Grid from kickoff - 6 h to kickoff + 4 h 30 min, carrying the last candle forward (as in the spread study's
    Amendment 1).
  - Kickoff = KXNFLGAME expected_expiration_time - 3 h, the same rule as #22.
- **Trades:** public trade prints for the win-margin markets in the same window, at second resolution, used for the
  fill check.
- **Settlement:** each market's public result. Games with any unsettled bucket are dropped and reported.

## Fair value (same as #22 Family A)

- For bucket "team by a to b": fair = P(team by > a-0.5) - P(team by > b+0.5).
- P(team wins) = KXNFLGAME mid - TIE mid / 2. "15 or more" = P(by > 14.5). Spread strikes come from floor_strike.
- Input validity at minute t:
  - Every input has both sides and a spread of 3c or less.
  - A long shot with no bid and an ask of 3c or less counts as ask/2.
  - A fair below 0 or above 1 before clamping is invalid.
- The TIE bucket is not traded.

## Strategy

**Taker:**
- At minute t (close values), signal when EV = fair - ask - fee(ask) >= theta. The NO side mirrors it:
  EV = bid - fair - fee(1-bid), buying NO at 1 - bid.
- Entry at minute t+1. Fill only if the t+1 close still offers the signal price or better, at the t+1 price.
- Size: count = min(10, floor($1 / price)).
- One open position per market and side.

**Exits:**
- **X1:** hold to settlement.
- **X2:** the first minute t' after entry with bid - fee(bid) >= fair(t') - 1c (NO side mirrored). Sell at minute
  t'+1 if that close still bids at the t' price or better; otherwise keep checking. Hold to settlement if it never
  triggers.

**Maker (exploratory):**
- At minute t, rest a YES bid at floor((fair - delta) * 100) / 100 when it sits strictly inside the spread.
- Filled only if a public trade prints **strictly below** our price within the next 60 s. This is conservative, since
  queue size is unknown.
- Maker fee 0 (quadratic series, re-checked at run time). Exit X2.

**Fees:** the taker fee per order is ceil(0.07 * C * P * (1-P), to $0.0001).

## Versions

- Taker: theta in {1c, 2c, 3c} x exits {X1, X2} = 6.
- Maker: delta in {1c, 2c} = 2.
- **Total 8 versions.**
- **Primary:** taker, theta = 2c, X2.

## Pass bar (primary; all must hold)

1. Mean net per contract > 0 in discovery **and** unseen.
2. Unseen 95% bootstrap lower bound > 0 (whole games, 10,000 draws, seed 20261020).
3. At least 100 unseen trades.
4. **Coin flip:** at most 5% of 500 random copies do as well on unseen. Each copy keeps the same buckets, sides and
   number of entries per game, with uniformly random signal minutes inside the same window, and the same fill and
   exit rules. Seed 20261020.
5. At least $2 per week projected at real size (about 15 games per week).
6. At least 50% of primary entries have a public trade at the entry price or better within the entry minute.

Results are reported for every version, pregame vs in-game, by bucket type, and with markouts at +1, +5 and +15 min.
A non-primary pass only counts as a lead for a new pre-registration.

## Amendments

(none)
