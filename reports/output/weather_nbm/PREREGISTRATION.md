# Pre-registration — research log #30: NOAA NBM forecast model vs Kalshi daily-high markets

Written 2026-10-21 ~04:35Z by Opus, after a feasibility study that downloaded 6 sample NBM text files and looked at
NO outcomes and NO Kalshi prices against forecasts (reports/output/weather_nbm/, research log entry).

**Known from earlier work (#5 weather calibration, FAILED):** taker buys lost in every price bucket. A long-shot bias
exists but is smaller than the spread plus the fee.

Changes go in "Amendments" with a date.

## Question

Does a probability built from NOAA's National Blend of Models (NBM) station forecast of the daily high predict
Kalshi's daily-high brackets better than Kalshi's price, by enough that taking the model's side beats the ask plus the
fee?

## Data (official, free, read-only)

- **Kalshi:** every KXHIGH* daily-high market with a CLI station in data/weather.sqlite (24 series; NYC/Chicago from
  2021, others later), with the official CLI result.
  - Prices at decision time: the yes_bid/yes_ask from the last hourly candle closing at or before the decision time.
  - Download the candles read-only if they aren't already stored.
- **NOAA NBM:** text bulletins `noaa-nbm-grib2-pds/blend.YYYYMMDD/HH/text/blend_nbstx.tHHz` (V4.0-V5.0).
  - Per station: TXN (daytime max, 12Z-00Z window) and XND (its spread) for the event day.
  - V3.2 (pre-2021) isn't used.
- **Observed highs:** from the Kalshi CLI results, plus the market's own ladder for exact degrees if needed.

## Decision times (no look-ahead: only NBM runs published before the decision time)

- **Morning (primary):** 09:00 local standard time on the event day, using the 06Z run (published by about 07:30Z).
- **Evening:** 21:00 Eastern the evening before, using the 00Z run of the event day's UTC date when published by
  then. Otherwise the 18Z run of the prior day.

## Model

- Daily high ~ Normal(mu = TXN + b_s, sigma = max(1.0, k_s * XND)), with integer rounding.
- P(bracket [a, b]) = Phi((b + 0.5 - mu) / sigma) - Phi((a - 0.5 - mu) / sigma). Open-ended brackets use one side.
- **Calibration b_s, k_s** (per station, rolling, no look-ahead):
  - Fit on that station's previous 60 event days with a known observed high, by maximum likelihood.
  - Shrink toward the pooled values from all stations' previous 60 days, with weight n/(n+30).
  - A station with fewer than 15 prior days uses the pooled values.
- **Window note:** NBM's TXN window (12Z-00Z) differs from the CLI local-standard-time day. b_s and k_s absorb the
  systematic part. Results are reported by time zone.

## Trading rule

- For each bracket market at the decision time:
  - buy YES if p - ask - fee(ask) >= theta;
  - buy NO if (1 - p) - (1 - bid) - fee(1 - bid) >= theta.
- At most one side per market. Count 10. Hold to settlement.
- Fee per contract = ceil(0.07 * 10 * P * (1 - P) / 0.0001) * 0.0001 / 10.
- A market needs a valid bid and ask at the decision time, or it's skipped and counted.

## Split

- Discovery = event dates before 2025-07-01.
- Unseen = 2025-07-01 to 2026-10-12. Most of the newer cities fall only in unseen; the rolling calibration handles
  that with no look-ahead.

## Versions

- Decision time {morning, evening} x theta {3c, 5c, 8c} = **6 versions**.
- **Primary (named now):** morning, theta = 5c.

## Pass bar (primary; all must hold)

1. Mean net P&L per contract > 0 in discovery **and** unseen.
2. Unseen 95% bootstrap lower bound > 0. Resample whole station-days, 10,000 draws, seed 20261021.
3. At least 200 unseen bets.
4. **Coin flip:** at most 5% of 500 copies do as well on unseen. Each copy has the same station-days and the same
   number of bets per station-day, with random brackets and sides among the markets with valid quotes, and the same
   price rules and fees.
5. At least **$2 per week** at count 10, over unseen weeks with bets.
6. **Model quality:** unseen bracket-level Brier of the model is at or below the market's (market p = mid), or within
   0.002 while rules 1-5 pass.

The other 5 versions are reported. Any that clear the bar are leads for a new pre-registration only. A maker variant
(resting orders at p minus delta) is a follow-up idea, not part of this test.

## Reported regardless

- Model vs market calibration (by bracket probability bucket).
- Brier and log loss.
- P&L by station, time zone, season, NBM version, YES vs NO, and price bucket (long shots vs near-money).
- How often the model disagrees by 5c or more.
- NBM bias and sigma by station over time.

## Amendments

(none)
