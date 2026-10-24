# NFL winner vs spread arbitrage: pre-registered test

Written 2026-10-13, **before** any KXNFLSPREAD price or trade history was downloaded or analysed. The relations, fill
rules and pass/fail criteria below are fixed now. Nothing gets tuned after seeing results. Anything added later is
labelled *exploratory*.

**Question.** On Kalshi, do the game-winner markets (KXNFLGAME) and the spread markets (KXNFLSPREAD) of the same NFL
game ever disagree badly enough that a two-leg position pays at least $1 in every outcome yet costs less than $1
including fees, and could those prices actually have been traded?

## Facts checked before writing (public API, 2026-10-13)

- KXNFLSPREAD and KXNFLGAME both have fee_type `quadratic_with_maker_fees`, multiplier 1. Taker fee per order =
  0.07 x C x P x (1 - P), rounded up to $0.000001 (Kalshi's sub-cent accounting).
- Spread market rule: "If <team> wins by more than K points ... resolves to Yes." K is `floor_strike` (1.5, 2.5, ...).
  About 25 spread markets per game, both teams, opened about 6 days before kickoff.
- Winner market rule: YES if the team wins; **a tie resolves to $0.50 for each team**. A tie is not a win by more than
  K, so every spread market resolves NO on a tie.
- History: 302 KXNFLSPREAD events (7,444 markets) for the 2025 season on the historical endpoints; 2026 season on the
  live endpoints. 1-minute candlesticks carry the closing YES bid and YES ask of every minute.

## Relations (every pair below pays at least $1 in every outcome, ties included)

W(T) = "team T wins", S(T,K) = "team T wins by more than K", for the two teams A and B of one game.

| Code | Position | Riskless because | Guaranteed edge per pair |
|---|---|---|---|
| R1 | buy W(T) YES + buy S(T,K) NO | S(T,K) implies W(T) | bid S(T,K) - ask W(T) |
| R2 | buy S(T,K1) YES + buy S(T,K2) NO, K1 < K2 | S(T,K2) implies S(T,K1) | bid S(T,K2) - ask S(T,K1) |
| R3 | buy NO on both of two outcomes that cannot both happen: (S(A,K), S(B,J)), (S(A,K), W(B)), (S(B,J), W(A)), (W(A), W(B)) | at most one pays YES (a tie pays W at most $0.50 each) | bid X + bid Y - 1 |
| R4 | buy W(A) YES + buy W(B) YES | one of them pays $1, or $0.50 + $0.50 on a tie | 1 - ask W(A) - ask W(B) |

Net guaranteed edge = the column above minus the taker fee on both legs at order size C, per pair.
Pairs of three or more legs are out of scope. Extra payouts in some outcomes (for example $2 in R1 when T wins by K
or less) are fair-value bonuses and are not counted.

## Data and snapshots

- Games: every 2025 regular-season and playoff game, and every 2026 regular-season game played by 2026-10-12, from the
  `games` table (nflverse). Preseason excluded.
- Quotes: for every KXNFLSPREAD and KXNFLGAME market of those games, the 1-minute candle closing YES bid and YES ask from
  the spread markets' open until market close.
- A snapshot is one candle end time t. A pair is evaluated at t only if **both** legs have a candle ending exactly at t
  with the needed side present (no forward fill). If fewer than 95% of minutes have candles, stop and decide before
  going on.
- Pre-game = t before the nflverse kickoff; in-game = kickoff to market close. **Pre-game is the primary result.**

## Opportunities, fills and duration

- An **episode** is a run of consecutive snapshots (60 s apart) where one pair's net edge at C = 1 is above 0.
- Episodes that share a market and overlap in time are merged into one **opportunity** (one mispriced quote can't be
  traded several times). Each opportunity is valued by its best pair at its first snapshot t0.
- **Fill check (primary, strict):** Kalshi trade prints in (t0, t0 + 60 s] on each leg, with the taker on our side,
  at a price as good as ours or better. Buy-YES leg: taker_side = yes and yes_price <= our ask. Buy-NO leg:
  taker_side = no and yes_price >= the bid we sell into. Fillable size = the smaller leg's qualifying volume.
- **Fill check (secondary, loose):** any print within t0 +/- 60 s at as-good prices (the rule used in the 2025 edge hunt).
- Duration = number of consecutive snapshots in the opportunity.
- Order sizes reported: 1, 10 and 100 contracts. 10 is the user's size.

## Pass criteria (both must hold, judged separately for each season)

**Manual (by hand):** pre-game opportunities that (a) net at least 1c per pair after taker fees at 10 contracts,
(b) are fillable for at least 10 contracts on the strict rule, and (c) last at least 2 snapshots, add up to at least
**$2.00 of guaranteed profit per NFL week** on average, in **both** 2025 and 2026.

**Bot:** the same with (c) relaxed to 1 snapshot and size up to 100 contracts, at least **$5.00 per week** in both seasons.

Otherwise the verdict is FAIL for that use. In-game results are reported but are not part of the verdict.

## Data-quality checks (must pass before results are trusted)

- D1: every market maps to one game and one team; spread strike = `floor_strike`, consistent with the ticker suffix.
- D2: every settled spread result agrees with the nflverse final margin (YES exactly when margin > K), and every winner
  result agrees with the final score (tie -> $0.50). Any mismatch is listed and explained.
- D3: candle coverage per market (share of minutes present between open and close) and share of null bids/asks.
- D4: no snapshot with bid > ask inside one market.
- D5: rules text of every market matches the two rule patterns above; any market with different wording is listed and
  excluded.
- D6: the 10 largest opportunities are re-checked by hand against the raw cached API responses.

## Amendment 1 (2026-10-13, before any opportunity was computed)

**Finding from the smoke run.** Kalshi returns a 1-minute candle only for minutes in which the market's book or trades
changed. Median share of minutes with a candle: about 4% for spread markets and 59% for winner markets. The original
"no forward fill, stop below 95% coverage" rule assumed a candle every minute, so it cannot be applied as written.

**Evidence that a missing minute means "nothing changed".**
- 74% of spread candles (389,165 of 525,899) have zero volume, so candles are emitted on order-book changes, not only
  on trades.
- On 8 randomly chosen 2026 spread markets (16,439 candle pairs), every candle that follows a gap opened at exactly the
  previous candle's closing bid and ask: 8,036 of 8,036 (100.0%). Across consecutive minutes the figure is 99.7%, so the
  open field is a real measurement, not a copy of the previous close.

**Change.**
- Each market's quote is carried forward from its last candle close until its next candle or its close_ts. Snapshots
  are every minute end from that market's first candle to its close. Minutes before a market's first candle have no
  known quote and are excluded.
- D3 is redefined: report the share of snapshot minutes with a carried quote, the median and maximum carry age, and the
  re-check of open-after-gap = previous close on the full dataset (expected ~100%; below 99% means stop and decide).
- Everything else is unchanged: relations, fill rules, sizes and pass criteria.

**Known bias of the strict fill rule under this change.** A mispriced quote that rests for many minutes with no trades
fails the strict check even though it may have been fillable. Such cases are reported separately as "stood N minutes,
unconfirmed size" and do not count toward the verdict.
