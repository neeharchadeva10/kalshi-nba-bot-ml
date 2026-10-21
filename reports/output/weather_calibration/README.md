# Weather markets: are the prices miscalibrated? — Result

**Verdict: FAIL.** Run 2026-10-19 with `python -m kpm.cli weather-calib`, exactly as pre-registered in
[PREREGISTRATION.md](PREREGISTRATION.md) (written 2026-10-13, before any price data was downloaded).

**In dollars: $0 per week at 10 contracts.** No rule of the form "at time T, buy YES (or NO) on every contract priced
in bucket X" made money after the taker fee. Not one of the 192 cells passed the first (discovery) step, so the
validation and trade-print size steps never ran.

## What was tested

- Every settled Kalshi daily high- and low-temperature market: 49 series, 97,224 markets, Aug 2021 to 2026-10-12.
  After the W1 checks: 16,331 city-days, 96,477 markets, 1,723 dates.
- Discovery: days before 2026-06-01 (1,589 dates). Validation: 2026-06-01 to 2026-10-12 (134 dates, 19.1 weeks).
  Most low-temperature cities only started in Apr 2026, so their discovery sample is short.
- Four entry times (local time): evening before (S1, 18:00), 07:00 (S2), 12:00 (S3) and 16:00 (S4) on the day.
  At each one: buy YES at the ask or NO at 1 - bid, 10 contracts, Kalshi's quadratic taker fee, held to settlement.
- 192 cells = high/low x 4 times x YES/NO x 12 price buckets. 390,229 hypothetical trades in discovery and 219,004 in
  validation.

## Why it failed

- **Every price bucket loses when you take the price.** Average net per contract in discovery (all times pooled):

  | Price paid | 1-3c | 3-6c | 6-10c | 10-20c | 20-35c | 35-50c | 50-65c | 65-80c | 80-90c | 90-94c | 94-97c | 97-99c |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|
  | Buy YES | -1.2c | -3.2c | -4.8c | -6.6c | -7.1c | -9.3c | -14.6c | -20.7c | -27.8c | -20.2c | -26.5c | -47.2c |
  | Buy NO  | -1.0c | -2.6c | -6.0c | -8.5c | -10.5c | -9.2c | -6.5c | -6.2c | -4.8c | -3.8c | -4.1c | -2.4c |

  Across all trades: -6.3c per contract in discovery and -3.6c in validation.
- **Long shots really are overpriced, but you can't collect it.** Cheap YES contracts (1-3c) lose about 1.2c each,
  so they win less often than their price says. Taking the other side (buying NO at 97-99c) still loses 2.4c, because
  you pay the bid-ask spread plus the fee, and the spread is bigger than the bias.
- The large losses for expensive YES contracts come mostly from wide, empty books (for example bid 1c, ask 95c).
  An "ask" there is not a fair price, and a taker who pays it loses. The pre-registered rule treats every quote as
  tradable, so these trades count. Dropping wide quotes was not tested; any such filter would be a new, exploratory
  rule chosen after seeing this data.
- **Best cells (none qualified):** of the cells with at least 200 trades, only one had a positive discovery average:
  low temp, evening before, buy NO at 90-94c, +0.09c per contract (95% interval -1.6c to +1.8c). That is noise, not
  an edge. In validation only 2 cells had a positive average (best: low temp, evening before, buy YES at 65-80c,
  +0.5c per contract on 306 trades, interval -5.2c to +6.3c). Neither was a discovery candidate.

## Data-quality checks (all passed; details in dq.json)

| Check | Result |
|---|---|
| W1 one market per bracket, ranges don't overlap, exactly one YES | 399 of 16,730 events excluded (747 markets), almost all the old 2021-22 HIGHNY/HIGHCHI format, as expected. 0 events with incomplete candles. |
| W2 carry-forward rule | After a gap, the next candle opened at the previous close 99.998% of the time (bar: 99%). |
| W3 crossed quotes | 329 of 71.6M candles. |
| W4 strike range vs rules text; settlement source | 0 of 94,261 disagree. Source recorded per market: NWS for most history, The Weather Company for the last ~60 days (from about mid-Aug 2026). |
| W5 share of snapshots with a quote | 99.8% to 100% per series. |
| W6 re-check against fresh API data | The 5 best cells (no cell passed). 8 seeded entries per cell, 40 in total, re-fetched with the cache bypassed: market result, then 1-minute candles to the snapshot. **40 of 40 match** on quote, result and net. See w6_recheck.json and w6_recheck.py. |

## Exploratory (not part of the verdict)

- **Bracket-sum arbitrage** (buying YES on every bracket for under $1 after fees, or NO on every bracket for under
  n - 1): 143 moments out of 56,530 city-day snapshots, worth $28.85 in total at 10 contracts since 2022. Validation
  period: $1.52 in 19 weeks, about **$0.08 per week**, and not confirmed by fills.
- **By settlement source:** NWS and The Weather Company periods both lose (NWS: YES -6.1c, NO -4.3c per contract.
  TWC: YES -4.7c, NO -3.8c). 4 NWS validation cells look positive, but that is about what chance gives when this many
  cells are sliced. Not an edge.
- **By city** is in exploratory_by_city.csv. With 49 series x 192 cells, scattered positive cells are expected by
  chance. None were followed up.
- **Maker version: not run.** The pre-registration listed it as exploratory, but it needs trade prints, and the code
  only fetches prints for cells that pass. Resting bids pay no fee and earn the spread instead of paying it, so this
  is the obvious next weather question. It needs its own pre-registration.

## Files

| File | What |
|---|---|
| verdict.json | Verdict and counts |
| dq.json | W1-W5 details |
| cells.csv | All 192 cells x 2 periods: n, dates, mean net per contract, bootstrap percentiles |
| candidates.csv, validation.csv | Empty (no candidates) |
| w6_recheck.json, w6_recheck.py | W6 fresh-API re-check |
| exploratory_*.csv | Bracket sums, by city, by settlement source |
| collection_summary.json | What was downloaded, per series |
