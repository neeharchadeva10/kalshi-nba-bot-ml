# Kalshi vs Polymarket: NFL game-winner price gaps, 2025 season

*Phase 1 historical study, built only from public read-only data: Kalshi market data, Polymarket's Gamma and
CLOB APIs, and nflverse. The appendices are regenerated from the database by `kpm report`. This summary was
written after reading those results.*

## Bottom line

**Don't build the live system as designed.** In the 2025 season, Kalshi's NFL game-winner prices did not drift
away from Polymarket's by more than Kalshi's fee at any time when Kalshi had real trading volume.

1. **No tradable gaps on game day.** In the final 24 hours before kickoff there was **not one after-fee gap
   above 2¢** across 279 games, and the largest after-fee edge in that window was **1.8¢**. Those 24 hours
   hold **82% of Kalshi's volume** for the final pre-game week (51% in the last 3 hours alone).
2. **The gaps that existed came early in the week, with thin books.** 99% of gaps above 2¢ began **more than
   4 days before kickoff**, and 77% within the first day after Polymarket listed the game. The biggest ones
   came from Polymarket's price sitting near 50¢ while its new order book was still empty.
3. **When a gap opened, Polymarket moved to Kalshi.** Within 15 minutes Polymarket had moved toward Kalshi in
   56–57% of gaps, and Kalshi had moved toward Polymarket in 7–8%. That is the opposite of the premise.
4. **Every strategy lost money after fees.** All 32 parameter sets lost money on the early weeks (best:
   −2.0¢ per contract). The sets chosen there lost again on the later weeks: **−3.5¢** per contract for "sell
   when the gap closes" and **−7.6¢** for "hold to settlement".
5. **Polymarket is a good fair price near kickoff, but no better than Kalshi.** At kickoff, Polymarket, Kalshi
   and the sportsbook closing line were equally well calibrated. Kalshi was slightly better than Polymarket
   at every horizon tested.

## What the data shows

### 1. How often did a gap beat Kalshi's fee?

On a 1-minute grid, *edge* means how much cheaper Kalshi's YES (or NO) ask was than the Polymarket price,
after Kalshi's taker fee on a 100-contract order.

| Final 7 days before kickoff | Early weeks (1–9): 135 games | Later weeks + playoffs: 144 games |
|---|---|---|
| Share of minutes with edge > 1¢ | 0.83% | 0.92% |
| Share of minutes with edge > 2¢ | 0.64% | 0.58% |
| Share of minutes with edge > 5¢ | 0.40% | 0.32% |
| Gap episodes > 2¢ (games with at least one) | 695 (68 games) | 683 (82 games) |
| Median episode length | 5 min | 4 min |
| Episodes > 2¢ starting in the final 24 h | **0** | **0** |
| Episodes > 1¢ starting in the final 24 h | 5 (in at most 3 games) | 8 (in at most 4 games) |

**When:** gaps bunch up on the Sunday afternoon a week before the game, which is Polymarket's first day of
trading for that game. 80% of gaps for Sunday games started between 1pm and midnight ET that Sunday, while the
previous week's games were being played. Even for Thursday games, most gaps started on the Sunday before.
From about 4 days before kickoff onward, the two prices sit about half a cent apart on average and after-fee
gaps all but disappear. Gaps above 2¢ appear in 0.004% of minutes between 1 and 4 days out, and never in the
final day.

**Liquidity:** when a gap opened, the Kalshi market had typically traded about 100 contracts in the previous
hour. In the final three hours before kickoff the median was 50,000–180,000 contracts an hour.

**Why the biggest gaps aren't real opportunities:** 154 gaps opened at 20¢ or more.
- **Empty-book midpoints:** 61% started with Polymarket's published price at 45–55¢ while Kalshi traded well
  away from 50¢ (outside 35–65¢), and 99% started within a day of Polymarket listing the game. Polymarket's
  history publishes a midpoint-style price, and the midpoint of an empty book is about 50¢. Example: LA–CAR
  (Nov 24), where Polymarket sat near 0.50 for about 22 hours while Kalshi traded the Rams at 0.84–0.87.
- **One-minute spikes:** others were single-minute jumps that snapped straight back. Example: CLE–BAL
  (Sep 7), Polymarket 0.175 → 0.56 → 0.175 with Kalshi unchanged.

### 2. After a gap opened, who moved?

Gaps above 2¢, all minutes:

| | Kalshi moved toward Polymarket | Polymarket moved toward Kalshi | Both | Neither |
|---|---|---|---|---|
| Early weeks, after 5 / 15 / 60 min | 7% / 8% / 9% | 45% / 56% / 53% | 8% / 13% / 25% | 40% / 24% / 13% |
| Later weeks, after 5 / 15 / 60 min | 4% / 7% / 10% | 53% / 57% / 59% | 7% / 12% / 23% | 35% / 25% / 9% |

- **Size of the moves:** within an hour, Kalshi's mid moved 0.2–1.2¢ toward Polymarket on average, while
  Polymarket moved 2.7–8.7¢ toward Kalshi. Every gap had closed by kickoff, mostly through Polymarket's
  price.
- **Robustness:** the pattern holds by time before kickoff, Kalshi spread, Kalshi volume and favorite vs
  underdog (Appendix C). Kalshi is the anchor and Polymarket the follower.
- **One nuance:** for the roughly 300 gaps that opened at least a day after Polymarket listed the game, Kalshi
  did more of the moving over the following hours or days. In the later weeks (about 210 such gaps), by
  kickoff Kalshi had moved
  toward Polymarket in 30% of them and Polymarket toward Kalshi in 42%. Average moves were 5.2¢ for Kalshi and
  3.8¢ for Polymarket. In the first 15 minutes Polymarket still did most of the moving (47% vs 12%).
  Section 3 shows these gaps weren't tradable either.

### 3. Could you have made money on Kalshi?

How the simulation works:
- **Entry:** buy at the Kalshi ask, paying the taker fee.
- **Fill check:** a trade counts only if Kalshi's own prints showed at least 100 contracts traded at that
  price or better within ±1 minute.
- **Parameters:** chosen on the early weeks only (from 32 combinations), then applied unchanged to the later
  weeks.

| Strategy (parameters chosen on early weeks) | Early weeks | Later weeks |
|---|---|---|
| **Sell when the gap closes** (edge > 1¢, Polymarket active, Kalshi ≥ 1,000 contracts in prior hour) | 61 trades in 32 games: **−4.7¢**/contract (95% CI −5.4 to −3.9), 2% winners | 37 trades in 24 games: **−3.5¢** (CI −4.6 to −2.2), 5% winners |
| **Hold to settlement** (edge > 2¢, all minutes) | 59 trades in 39 games: **−2.8¢** (CI −15.5 to +10.2), 41% winners | 47 trades in 35 games: **−7.6¢** (CI −20.0 to +7.3), 38% winners; worst drawdown $511 at 100 contracts |

- **Sell when the gap closes:** the gap usually closed because Polymarket moved, so the trade just paid
  Kalshi's spread plus two fees.
- **Hold to settlement:** amounts to a bet on the game minus the fee. The wide confidence intervals are
  game-outcome noise, not an edge.
- **Not fillable anyway:** 72% (early) and 83% (later) of 2¢ gap openings had too little Kalshi volume
  nearby to fill 100 contracts.
- **No sub-group worked:** splitting by time before kickoff, Kalshi spread, Kalshi volume or favorite vs
  underdog gives nothing positive in both halves. The one exception has 3 trades in total.
- **Post-hoc check** (defined after seeing the data, so outside the clean protocol): using only gaps that
  opened at least a day after Polymarket listed the game changes nothing.
  - Sell when closed: −4.2¢ and −3.3¢.
  - Hold: +4.9¢ on 13 trades, then −10.4¢ on 23 trades.

### 4. Is Polymarket a trustworthy fair price?

Brier score for the home-team probability vs the result (lower is better). 278 games, with the one tie
excluded.

| Time before kickoff | Polymarket | Kalshi mid | Sportsbook closing line |
|---|---|---|---|
| 24 hours | 0.2137 | 0.2131 | – |
| 3 hours | 0.2140 | 0.2136 | – |
| Kickoff | 0.2127 | 0.2124 | 0.2124 |

Near kickoff all three are well calibrated and practically identical, which is why gaps disappear then. Early
in the week, when gaps do exist, Polymarket's price is the less reliable of the two.

## Data limitations

- **Polymarket time resolution was 1 minute for all 570 outcome tokens.** It was never coarser, so the Data
  API trades fallback wasn't needed. But this series has limits:
  - It is **one midpoint-style price per minute, with no bid, ask, depth or volume**, so we can't tell whether
    the price was tradable. The two outcomes' prices are exact complements.
  - It prints a point every minute even when nothing changed. The appendices therefore also show a
    "Polymarket active" view (price changed within 30 minutes). Both views lead to the same conclusions.
  - Gap speed below 1 minute can't be measured. The 5-minute results rest on five 1-minute steps, and very
    brief dislocations on either venue would be invisible.
- **Kalshi data:** 1-minute candles (bid/ask at the close of each minute, trade prices, volume) plus every
  trade (5.2M). Quotes are carried forward through quiet minutes. 561 of 564 markets have a median spacing of
  60 seconds.
- **Window: the final 7 days before kickoff.** Polymarket listed 86% of games earlier than that (median 7.6
  days ahead; a quarter 13–14 days ahead), so the earliest part of each game's overlap is not included. Those
  are the thinnest, most gap-prone hours on both venues. Including them would raise the raw gap counts but
  shouldn't change the trading conclusion. Extending to 14 days is a config change plus about an hour of
  free data collection.
- **Fill realism:** uses Kalshi prints at our price or better within ±1 minute. It doesn't model queue
  position or our own market impact.
- **Kickoff time:** pre-game data is cut at nflverse's scheduled kickoff. Polymarket's own start time was
  5 hours off in 3 games and 15 minutes off in 1.

## Caveats

- **One season, 279 games.** Strategy results rest on 37–82 trades each and are flagged where below 30 games or
  50 trades. The conclusion doesn't hinge on any one number, though:
  - gaps were rare and confined to thin early-week books;
  - they closed mainly through Polymarket;
  - every parameter set lost money.
- **Excluded games:** six playoff and late-season games are left out.
  - Both conference championships and the Super Bowl had no usable Kalshi `KXNFLGAME` market.
  - Flexed week-18 CAR–TB and SEA–SF and the divisional SF–SEA game await your approval in
    `review/manual_review.csv`. The AI review found them the same event; they were flagged only because
    Kalshi's ticker date differs from the game date.
- **Settlement rules differ in rare cases:**
  - Kalshi pays a "fair price" on cancellation, where Polymarket resolves 50-50.
  - Postponement windows differ.
  - Polymarket's text is silent on ties for 354 pairs, yet its one 2025 tie (GB–DAL) settled 50/50, like
    Kalshi.
  - None of these affected a 2025 pre-game price.
- **Fees:** modelled as 0.07 × P × (1−P) per contract, rounded up to the cent per 100-contract order. Kalshi's
  docs now describe sub-cent fee accounting; exact rounding moves results by at most a few tenths of a cent.
- **Markets change.** Kalshi's per-game pre-game volume varied 2–4x across regular-season weeks and was far
  higher in the playoffs, and 2026 liquidity could differ again. Nothing here points to an edge that would appear with more volume.
  Volume is exactly what made the gaps disappear.

## Recommendation

**Not worth building the live Polymarket-reference trading system for Kalshi NFL game winners.** The edge
it targets didn't exist in the hours when Kalshi has liquidity. Where gaps did appear, Kalshi was the better
price, and trading Kalshi toward Polymarket lost money after fees in every variant tested.

The collectors, database schema and matcher agent work and are reusable. If you want to keep exploring, two
questions this codebase could test cheaply with public data are:

1. Whether Kalshi lags sportsbook closing lines on game day.
2. Whether Kalshi is the follower in thinner markets (spreads, totals, other leagues).

Neither is built.
