# Weather markets: are the prices miscalibrated? (pre-registered test)

Written 2026-10-13, **before** any weather price or trade history was downloaded or analysed. Only market metadata
(series list, open/close times, strike structure, rules text) was looked at. The rules, buckets and pass bars below
are fixed now. Anything added later is labelled *exploratory*.

**Question.** In Kalshi's daily temperature markets, do contracts at a given price win more or less often than the
price says, by enough that a simple rule ("at time T, buy YES (or NO) on every contract priced in bucket X") makes
money after taker fees at 10 contracts? The classic suspect is the long-shot bias: cheap contracts winning less often
than their price.

## Facts checked before writing (public API, 2026-10-13)

- Each city-day is one event with about 6 mutually exclusive markets: 2-degree brackets ("between 84-85") plus a
  low tail ("less than 78") and a high tail ("greater than 85"). Exactly one should settle YES.
- A market opens about 10 am ET the day before the measured day and closes at the end of the measured climate day
  (e.g. 05:00 UTC for New York during daylight time). It settles the next day.
- Settlement source changed over time: the Feb 2026 rules use the NWS Daily Climatological Report; the Oct 2026 rules
  use "The Weather Company". Both periods are included; results are also reported separately by source.
- Weather series have fee_type `quadratic`: takers pay 0.07 x C x P x (1 - P), makers pay nothing.
- Settled markets before 2026-08-14 are on the historical endpoints, later ones on the live endpoints.

## Data

- Universe: every settled market of every daily high-temperature and low-temperature series (category "Climate and
  Weather", daily frequency), all cities. Hourly, rain and snow series are out of scope.
- Quotes: 1-minute candles (closing YES bid and YES ask) from open to close. A market's quote is carried forward from
  its last candle until its next candle or its close (the rule validated in the NFL study, re-checked here as W2).
- Outcome: Kalshi's settlement result.
- City time zones come from a hand-checked table (series -> city -> IANA time zone).

## Trades tested

Snapshot times, in the city's local time, for measured day D:
- S1: D-1 at 18:00 (evening before)
- S2: D at 07:00
- S3: D at 12:00
- S4: D at 16:00

At each snapshot every open market with a quote gives up to two hypothetical taker trades of 10 contracts:
- Buy YES at the ask (needs an ask).
- Buy NO at 1 - bid (needs a bid).

Entry-price buckets (price paid per contract): 1-3c, 3-6c, 6-10c, 10-20c, 20-35c, 35-50c, 50-65c, 65-80c, 80-90c,
90-94c, 94-97c, 97-99c (lower edge exclusive except the first).

Net per contract = payout (1 or 0) - price - taker fee per contract, with the fee for one 10-contract order rounded up
to $0.0001, as on the user's real fills.

A **cell** is one combination of market kind (high or low temperature) x snapshot x side (YES or NO) x bucket.
That is 2 x 4 x 2 x 12 = 192 cells.

## Statistics

- Uncertainty: bootstrap resampling of whole calendar dates (all cities and markets of a date together, because
  weather and brackets are correlated), 10,000 resamples, 95% intervals.
- Discovery period: measured days before 2026-06-01. Validation period: 2026-06-01 to the latest settled day.

## Pass criteria

1. **Discovery.** A cell is a candidate if its mean net per contract is above 0 with the 95% interval's lower bound
   above 0, with at least 200 trades from at least 30 different dates.
2. **Validation.** A candidate passes if, in the validation period alone, its mean net is above 0 with the one-sided
   95% lower bound above 0 (same bootstrap) and at least 100 trades.
3. **Size.** A passing cell must be worth at least **$2.00 per week** at 10 contracts in the validation period,
   counting only entries confirmed by trade prints: within 10 minutes either side of the snapshot, prints on that
   market with the taker on our side at our price or better, at least 10 contracts in total.

The verdict is PASS if at least one cell meets all three. Results for every cell are reported, pass or fail.

## Data-quality checks (must pass before results are trusted)

- W1: every market maps to one series, city, date and strike range; within each event the ranges do not overlap
  and exactly one market settled YES. Events that fail are listed and excluded.
- W2: carry-forward check: a candle after a gap opens at exactly the previous close in at least 99% of cases
  (below 99% means stop and decide).
- W3: no crossed quote (bid above ask).
- W4: the strike range in the rules text matches floor_strike/cap_strike; the settlement source (NWS or The Weather
  Company) is recorded per market.
- W5: share of snapshots with a quote, per series.
- W6: the cells that pass (or the 5 best cells if none pass) are re-checked against fresh API data.

## Also reported (exploratory, not part of the verdict)

- A maker version: the same rule as a resting bid at the snapshot (no fee), counted only when later prints show a
  taker trading into that price.
- Bracket-sum checks: moments when buying YES on every market of an event costs under $1 after fees, or buying NO
  on every market costs under (number of markets - 1) after fees.
- Results by city and by settlement source.
