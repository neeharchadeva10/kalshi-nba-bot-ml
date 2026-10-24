# Mechanical edges: pre-registered tests

Written 2026-10-05, **before** any final-minute trade data or hourly-market data was downloaded or analysed. The rules,
buckets and pass/fail criteria below are fixed now. Nothing gets tuned after seeing results. Anything added later is
labelled *exploratory* and needs its own forward test.

**Goal.** Find Kalshi trading rules that profit from how the markets are built (settlement rules, contract
relationships, exchange programs) rather than from predicting BTC.

A strategy is called **legit** only if it passes every criterion here **and** a later forward test on markets that
close after 2026-10-05. The forward test is paper trading on public data.

## H1. Final-minute settlement averaging (KXBTC15M)

**Mechanism.**
- Settlement is the simple average of the 60 one-second CF Benchmarks BRTI prints before the close.
- During the last 60 seconds, part of that average is already fixed, so the outcome is more certain than the spot price
  alone suggests.
- If the market under-reacts, the side that is ahead (the favorite) is underpriced in the final seconds.

**Data.**
- Every Kalshi trade print in the final 90 seconds of each KXBTC15M market closing 2026-06-01 to 2026-10-04, from the
  public trades endpoint.
- Prints are aggregated per market, second, price and taker side.
- The outcome is Kalshi's settlement.

**Trade definition.**
- A print where the taker bought the side priced above 50c is a fill we could have taken as a taker at that price.
- One observation per market per bucket: the volume-weighted price of those prints.

**Buckets (fixed).**
- Seconds left: (60, 90] is the control, outside the averaging window. The test buckets are (30, 60], (15, 30],
  (5, 15] and (0, 5].
- Favorite price: [0.80, 0.90), [0.90, 0.95), [0.95, 0.98), [0.98, 0.995) and [0.995, 1.00).

**Edge per contract** = win (1/0) − price − exact Kalshi taker fee 0.07·p·(1−p).

**Pass criteria.** A test bucket (seconds left × price) is a candidate only if all of these hold:
1. The mean edge is > 0 in each period: early (June), later (Jul 1 to Aug 4) and fresh (Aug 5 to Oct 4).
2. On later + fresh combined, the lower bound of a Bonferroni-adjusted 99.75% confidence interval is > 0. This covers 20
   test buckets, uses a bootstrap clustered by day, and is computed in cents per contract.
3. At least 300 markets contribute in later + fresh, and the median printed size per market-bucket is at least 10
   contracts.
4. Mechanism check: the control bucket (60 to 90 seconds left, same price band) does not show an edge of similar size.
   If the "edge" is equally present outside the averaging window, it is not this mechanism and is not accepted.
   "Similar size" means the control's mean edge on later + fresh is at least half the test bucket's mean edge. This
   operational definition was fixed before downloading.

## H2. Cross-market bounds: hourly BTC price (KXBTCD) vs BTC 15-minute (KXBTC15M)

**Mechanism.**
- Kalshi's rules: KXBTCD "BTC above K at HH:00" and the KXBTC15M market ending at HH:00 settle on the same
  60-second BRTI average.
- With target T = the 15-minute market's opening average:
  - For K < T, "above K" can never be less likely than UP.
  - For K ≥ T, "above K" can never be more likely than UP.

**Riskless pairs.** Each pays at least $1 in every outcome:
- (a) K < T: buy KXBTCD(>K) YES and buy 15-minute DOWN.
- (b) K ≥ T: buy 15-minute UP and buy KXBTCD(>K) NO.
- Profit = 1 − cost of both legs at the asks − exact taker fees on both legs.

**Data.**
- 1-minute candles (bid and ask at each minute close) for:
  - the KXBTCD strikes nearest each hourly target (2 below, 2 above);
  - the 15-minute market ending at that hour.
- Covers the final 15 minutes, 2026-06-01 to 2026-10-04.

**Pass criteria.**
1. Violations with profit > 0 after fees occur in every period.
2. Persistence: report how many still show at the next minute close. A one-snapshot violation is not a fill.
3. A live forward check with the order-book scanner (prices *and* displayed sizes for both legs) finds them at a size
   worth trading.

Settlement edge case: if the final average lands within a cent of a strike, the pair is counted as a loss.

## H3. Exchange payments (research only)

Kalshi programs that pay traders mechanically (liquidity rewards, maker rebates, interest), taken from official sources.
No trading.

## H4. Liquidity Incentive Program capture (added 2026-10-05, before looking at any program data)

**Question.** For a small account ($25–$500), do resting limit orders in markets that pay liquidity rewards earn more in
rewards than they lose when they get filled at bad moments (adverse selection)?

**Background (from H3).**
- Kalshi pays $1–$1,000 per market per day.
- The book is sampled once a second. Each resting bid scores discount^N × size, where N is the number of ticks behind the
  reference price (the level where cumulative depth reaches 1/5 of the target size).
- Rewards are shared in proportion to score.
- Makers pay no fee on `quadratic` series. Maker fees are charged where the series has them.

**Method.**
1. **Static economics.** List the active liquidity programs from the public endpoint. Snapshot each market's public
   order book and compute the existing score using Kalshi's formula.
   - Estimate the daily reward for resting q = 10, 50 and 100 contracts at the reference price on both sides.
   - Express it per dollar of collateral.
   - Shortlist the programs paying at least 0.5% of collateral per day before fills.
2. **Forward paper test (at least 2 weeks).** For the shortlist:
   - Snapshot the books every 30 minutes and log public trades.
   - Simulate a resting order at the back of the queue at the reference price. Fills require traded volume at our price
     to exceed the queue ahead of us.
   - Mark each fill to the mid price 1 hour and 24 hours later, and to settlement if it settles.
   - Net = rewards − adverse selection − maker fees (where charged).

**Pass criteria.**
- Simulated net > 0 per dollar of collateral in each week of the forward test.
- It must hold with adverse selection doubled and a reward share halved, which accounts for other liquidity joining.
- It must hold across at least 5 different markets.

**Caveat.** Collecting rewards requires placing real orders. That is outside this read-only phase, so it is the user's
decision.

## Rules for all tests

- Exact Kalshi taker fees (sub-cent), as charged by the fills API.
- Read-only: public market data only; no order, portfolio or account endpoints.
- Report every bucket, pass or fail, not just the winners.

## H4 step 2 amendment: forward paper test details (2026-10-13, before any step-2 data was collected)

The user approved the forward test on 2026-10-13. The H4 pass criteria above are unchanged; this section fixes the
details they left open. Kalshi's LIP help article (modified 2026-09-25) was re-read the same day: one snapshot per
second; reference price = first level where cumulative size reaches 1/5 of the target; "Kalshi scores every resting
order that helps reach the Target Size on its side" (ties inside a price level are not defined); a snapshot is excluded
unless both sides reach the target; reward = score share x period reward x (non-excluded / total snapshots), rounded
down to the cent; programs paying under $1.00 to an account pay nothing; the program ends 2027-01-01.

**Collection (public GET only, at most 6 requests/second in total, at least 14 days).**
- Every 15 minutes: list active liquidity programs, then fetch order books for program markets not yet tracked,
  highest period reward first, within the rate budget. Store a per-market summary (best bids, depth, qualifying
  weighted size per side), not every full book.
- Selection rule (uses only information available at that moment): rank programs by the estimated reward for the
  whole program at q = 10 under rule C below, and track the top 25 whose estimate is at least $1. Same for q = 50.
  Tracking stops when the program ends or the market falls out of the top 25 at a rescan.
- Tracked markets: full order book every 60 seconds, public trades every 60 seconds, and the market result after
  it settles. Raw responses kept gzip-compressed.

**Simulated bot.**
- Rests q contracts as a YES bid and q as a NO bid at each side's best bid, from the first snapshot after selection.
- Each snapshot: if the best bid on a side moved above our price, or our price is now below the reference price,
  cancel and re-place at the current best bid at the back of the queue.
- Queue: at placement, the queue ahead is the size resting at our price in that snapshot. Taker volume at our price
  uses up the queue ahead first, then fills us. A taker trade at a worse price than ours fills all our remaining size.
  Cancellations ahead of us are ignored (back-of-queue). Variant "front": queue ahead = 0, which maximises fills.
- After a fill on a side the bot stops quoting that side in that market and holds the position to settlement. A
  position not settled by the end of the test is marked to the last mid price.
- Maker fees: 0 on series with fee_type `quadratic`, 0.0175 x C x P x (1 - P) per fill on `quadratic_with_maker_fees`.
- Rewards per snapshot minute and side: our score = q x discount^(ticks below the reference price) (1 at or above it).
  - Rule C (primary): only orders inside the first Target Size of cumulative depth from the best price qualify, with
    our order last in its price level. If our order is outside, it scores 0.
  - Rule L (lenient, reported only): every resting order qualifies.
  - Share = our score / (all qualifying scores including ours). A minute where either side's total depth is below
    the target is excluded.
  - Program reward = period reward x sum over the program's minutes of (YES share + NO share) / 2 / (number of
    minutes in the program), with minutes we were not resting counted as 0. Rounded down to the cent; 0 if under $1.

**Verdict (primary: q = 10, rule C, back-of-queue).**
- Net = rewards + fill P&L (settlement payout - price paid - maker fees), per dollar of average collateral, where
  collateral = q x (our YES price + our NO price) while resting.
- PASS needs all of: net > 0 in each of the two weeks; net > 0 under stress (rewards x 0.5 and losing fill P&L x 2);
  positive net in at least 5 different markets with no single market supplying more than half of the total.
- Also reported: q = 50, rule L, the front-of-queue variant, and a $30 budget (at most 3 markets resting at once, best
  ranked first) in dollars per week.

*Clarification (2026-10-13, before the 14-day collection started; only a 4-minute smoke test had been run, with no
fills or results looked at):* "the estimated reward for the whole program" in the selection rule is computed for the
part of the program still to run at the moment of selection (period reward x shares x remaining fraction), because
that is what a bot joining at that moment could earn. Selection is re-run every 2 minutes and at each 15-minute
refresh; tracked markets are re-scored from their 60-second books.
