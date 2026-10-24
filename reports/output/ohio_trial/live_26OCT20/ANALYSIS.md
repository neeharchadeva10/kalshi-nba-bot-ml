# Live lock bot, night of 2026-10-20 (PHINYK + OKCSAS), post-game analysis

Written 2026-10-21 ~04:50Z by Opus. Sources:
- events.jsonl: every attempt/start/stop line from the server's bot logs.
- account.json: the user's Kalshi fills, positions, settlements and shard cash, read-only (GET /portfolio/*).

The bot logs and Kalshi's records agree on every fill.

## Bottom line

|                     | Shard 0 (moneyline) | Shard 3 (everything else) | Total    |
|---------------------|---------------------|---------------------------|----------|
| Cash at start       | $8.6969             | $9.0000                   | $17.6969 |
| Cash now            | $8.8668             | $10.6527                  | $19.5195 |
| Change              | +$0.1699            | +$1.6527                  | **+$1.8226** |

- One position is still open: **1 YES "Julian Champagnie 4+ rebounds" bought at 4c**.
  - Its cost is already taken out of the cash above.
  - Kalshi hasn't settled the SGA, Fox or Champagnie rebound markets yet. Expected around 07:30Z; the latest
    expiration is 2026-10-23 01:30Z.
  - The market shows 2+ rebounds at 99c and 4+ at 0-1c, so he finished with 2 or 3. **Expect it to settle NO** and
    the night to end at **+$1.82**.
- Per game:
  - PHINYK: -$0.0171 (moneyline pair +$0.1699; all other legs -$0.1870).
  - OKCSAS: +$1.8397.

## What happened

- **29 attempts** (PHINYK 14, OKCSAS 15): **0 complete locks**, 14 partial, 15 with nothing filled.
- **17 contracts filled:**
  - 13 cheap long-shot legs (1-7c);
  - 2 legs at 98c (4th-quarter winner set);
  - 2 moneyline legs (56c and 24c).
- **Speed:**
  - Decision about 1-5 ms after the 01:45Z fix (26 ms and 127 ms before it; one 32 ms outlier at 04:18).
  - Order round trips 7-50 ms, median about 10 ms.
  - The missing legs were missed anyway.
- **OKCSAS stopped at 04:24:07Z** on the 15-attempts-per-game cap. Both processes exit by themselves at 09:00Z.

### Where the profit came from

1. **Two long-shot legs won, +$1.93.**
   - KXNBA3QTOTAL OKCSAS 63+ (YES at 5c) and Holmgren 6+ rebounds (YES at 2c).
   - The other 11 long shots lost. With Champagnie, 11 of 13 lost.
   - At the prices paid, 13 legs should win about 0.36 times. Two or more wins has about a **4.8% chance** if the
     prices were fair. That is most likely luck, and 13 legs can't show otherwise.
2. **Moneyline pair, +$0.17.**
   - The 01:42 attempt bought NO NYK at 56c. The 01:48 attempt bought YES NYK at 24c.
   - Holding both is a real lock (80c in, $1 out). It only happened because NYK's price fell 20c between the two
     attempts. A move the other way would have left a plain 56c bet.
3. **4th-quarter winner, +$0.04.**
   - NO PHI and NO TIE filled at 98c each; NO NYK (2c) didn't.
   - The bot had **$1.96 at risk to make 4c**. It paid off because NYK won the 4th.
   - This was the biggest risk of the night.
4. **Everything else, about -$0.28** (11 losing long shots plus fees).

### Why no lock completed

- In every partial, the leg that filled was the **cheap side** or a side the market was about to agree with. The leg
  that vanished was the one that made it a lock.
- The "lock" was one quote that was still there plus one that was already gone, or about to be.
- Our 10 ms round trip from Ohio didn't change that. The quotes we needed were pulled or taken before or as our
  order arrived.

## Lead to test, not to trade

- In an inverted ladder, one of the two prices is stale. Tonight both winning long shots were the stale-cheap side of
  an inversion (for example, Holmgren 6+ at 2c while the 8+ market bid 4c).
- **Question:** does the cheap leg of a "fake lock" win more often than its price says?
- The read-only lockwatch_night logs (#27) hold every lock seen, across all games and nights, with leg prices and
  results.
- This needs a pre-registration before anyone looks at outcomes.

## Fixes discussed but not built (awaiting the user)

- **Skip locks whose cheap leg is under 10c.**
  - Tonight this would have skipped everything except the two moneyline attempts: +$0.17 instead of +$1.82.
  - It cuts variance, not a proven loss.
- **Complete the missing leg up to break-even.**
- **Net offsetting fills in the at-risk ledger.** The ledger still counts $0.84 on the NYK pair.
- **Cap any single leg's price**, so a 98c leg can't put about $1 at risk for 2c.
