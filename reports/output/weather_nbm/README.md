# Research log #30: does NOAA's NBM forecast beat Kalshi's daily-high prices?

Pre-registration: `PREREGISTRATION.md` (written before any outcome or price was compared with a forecast). Read-only: NOAA open data over anonymous HTTPS and Kalshi's stored public candles. No orders, no portfolio calls.

## Verdict: **FAIL** (primary = morning decision, 5c edge threshold)

Plain English: a probability built from NOAA's own forecast is *worse* than Kalshi's price at 9 am local time, and taking the model's side loses money after the spread and fee. In the unseen period the primary rule bet 17,453 times (10 contracts each) and lost $5,533.07 in total, about $81.37 lost per week with bets. Betting at random loses less than the model does.

| # | Rule | Result | Number |
|---|---|---|---|
| 1 | Mean net per contract > 0 in discovery and unseen | FAIL | discovery -0.0021, unseen -0.0317 dollars |
| 2 | Unseen 95% bootstrap lower bound > 0 (10,000 draws, station-days, seed 20261021) | FAIL | 95% interval -0.0373 to -0.0260 |
| 3 | At least 200 unseen bets | pass | 17,453 |
| 4 | Coin flip: at most 5% of 500 random copies do as well | FAIL | 100.0% did as well or better (random copies average -$3,907.90, model -$5,533.07) |
| 5 | At least $2 per week at count 10 | FAIL | -$81.37 per week over 68 unseen weeks with bets |
| 6 | Unseen Brier of model <= market (or within 0.002 while 1-5 pass) | FAIL | model 0.1584 vs market mid 0.1300 (lower is better) |

## The 6 versions

Net = dollars per contract after the taker fee, holding to settlement. 'Coin flip' = share of 500 random copies (same station-days, same bets per station-day) that did as well on the unseen period (want <= 5%). $/week = unseen net dollars at 10 contracts divided by unseen weeks that had bets.

| Version | Bets disc / unseen | Mean net disc | Mean net unseen | Unseen 95% interval | Coin-flip share | $/week | All rules |
|---|---|---|---|---|---|---|---|
| morning 3c | 9,616 / 20,170 | -0.0064 | -0.0307 | -0.0360 to -0.0254 | 99.8% | -$91.06 | fail |
| morning 5c (primary) | 8,324 / 17,453 | -0.0021 | -0.0317 | -0.0373 to -0.0260 | 100.0% | -$81.37 | fail |
| morning 8c | 6,563 / 13,791 | +0.0020 | -0.0330 | -0.0395 to -0.0264 | 100.0% | -$66.88 | fail |
| evening 3c | 10,877 / 22,143 | +0.0054 | -0.0187 | -0.0235 to -0.0136 | 22.0% | -$60.74 | fail |
| evening 5c | 8,955 / 17,952 | +0.0123 | -0.0191 | -0.0247 to -0.0134 | 31.6% | -$50.52 | fail |
| evening 8c | 6,595 / 12,918 | +0.0184 | -0.0192 | -0.0261 to -0.0121 | 38.4% | -$36.48 | fail |

No version clears the bar. No leads for a follow-up pre-registration.

## Model vs market (all brackets with a valid bid and ask at the decision time)

| Decision | Period | Brackets | Brier model | Brier market (mid) | Log loss model | Log loss market | Model differs from mid by >= 5c |
|---|---|---|---|---|---|---|---|
| morning | discovery | 16,821 | 0.1697 | 0.1561 | 0.520 | 0.472 | 72% |
| morning | unseen | 29,594 | 0.1584 | 0.1300 | 0.490 | 0.400 | 70% |
| evening | discovery | 20,318 | 0.1513 | 0.1472 | 0.472 | 0.452 | 67% |
| evening | unseen | 37,009 | 0.1335 | 0.1194 | 0.424 | 0.373 | 60% |

Log loss clips probabilities to [0.001, 0.999]. The market beats the model in every row. The model is over-confident in the tails (see below), and in the evening version its high-probability calls (p >= 0.8) came true only about 37% of the time on unseen days (52 brackets), while the market's mid was well calibrated.

### Calibration, morning, unseen (mean predicted probability vs how often YES happened)

| Bucket of p | n model | model p | YES freq | n market | market mid | YES freq |
|---|---|---|---|---|---|---|
| [0.0, 0.02) | 2,404 | 0.007 | 0.036 | 2,157 | 0.015 | 0.006 |
| [0.02, 0.05) | 1,707 | 0.035 | 0.071 | 5,413 | 0.032 | 0.024 |
| [0.05, 0.1) | 3,220 | 0.077 | 0.105 | 3,903 | 0.071 | 0.059 |
| [0.1, 0.2) | 8,654 | 0.152 | 0.174 | 4,369 | 0.144 | 0.128 |
| [0.2, 0.3) | 7,031 | 0.246 | 0.268 | 3,489 | 0.249 | 0.245 |
| [0.3, 0.5) | 5,210 | 0.380 | 0.384 | 6,580 | 0.396 | 0.390 |
| [0.5, 0.7) | 1,322 | 0.575 | 0.527 | 3,069 | 0.574 | 0.588 |
| [0.7, 0.8) | 25 | 0.751 | 0.640 | 331 | 0.740 | 0.761 |
| [0.8, 0.9) | 16 | 0.842 | 0.812 | 157 | 0.845 | 0.911 |
| [0.9, 0.95) | 3 | 0.919 | 1.000 | 75 | 0.924 | 0.920 |
| [0.95, 0.98) | 1 | 0.957 | 1.000 | 39 | 0.962 | 0.974 |
| [0.98, 1.0) | 1 | 0.992 | 1.000 | 12 | 0.984 | 1.000 |

The model's bottom buckets are too low (it says 0.7% and YES happens 3.6%); the market's bottom buckets are slightly too high (the usual long-shot bias), which is still smaller than the spread plus fee.

## Where the primary loses (and wins)

**By YES / NO** (primary, unseen)

| Group | Bets | Net dollars (10 contracts) | Mean net per contract |
|---|---|---|---|
| NO | 9,040 | -$3,218.57 | -0.0356 |
| YES | 8,413 | -$2,314.50 | -0.0275 |

**By entry price** (primary, unseen)

| Group | Bets | Net dollars (10 contracts) | Mean net per contract |
|---|---|---|---|
| 1-10c | 5,706 | -$1,022.37 | -0.0179 |
| 10-30c | 2,657 | -$1,428.79 | -0.0538 |
| 30-70c | 7,061 | -$2,367.93 | -0.0335 |
| 70-90c | 1,854 | -$727.72 | -0.0393 |
| 90-99c | 175 | $13.74 | +0.0079 |

**By time zone** (primary, unseen)

| Group | Bets | Net dollars (10 contracts) | Mean net per contract |
|---|---|---|---|
| America/Chicago | 6,455 | -$2,358.33 | -0.0365 |
| America/Denver | 1,228 | -$415.33 | -0.0338 |
| America/Los_Angeles | 3,555 | -$1,226.62 | -0.0345 |
| America/New_York | 5,630 | -$1,286.86 | -0.0229 |
| America/Phoenix | 585 | -$245.93 | -0.0420 |

**By season** (primary, unseen)

| Group | Bets | Net dollars (10 contracts) | Mean net per contract |
|---|---|---|---|
| DJF | 2,648 | -$762.90 | -0.0288 |
| JJA | 5,798 | -$2,040.41 | -0.0352 |
| MAM | 5,112 | -$1,370.63 | -0.0268 |
| SON | 3,895 | -$1,359.14 | -0.0349 |

**By NBM version** (primary, unseen)

| Group | Bets | Net dollars (10 contracts) | Mean net per contract |
|---|---|---|---|
| 4.3 | 8,483 | -$2,461.76 | -0.0290 |
| 5.0 | 8,970 | -$3,071.31 | -0.0342 |

**By station** (primary, unseen)

| Group | Bets | Net dollars (10 contracts) | Mean net per contract |
|---|---|---|---|
| CLIATL | 688 | -$113.40 | -0.0165 |
| CLIAUS | 1,221 | -$453.67 | -0.0372 |
| CLIBOS | 772 | -$199.00 | -0.0258 |
| CLIDCA | 759 | -$142.20 | -0.0187 |
| CLIDEN | 1,228 | -$415.33 | -0.0338 |
| CLIDFW | 648 | -$289.15 | -0.0446 |
| CLIEWR | 72 | -$17.79 | -0.0247 |
| CLIHOU | 601 | -$134.99 | -0.0225 |
| CLILAS | 588 | -$352.27 | -0.0599 |
| CLILAX | 1,245 | -$492.46 | -0.0396 |
| CLIMDW | 1,204 | -$504.78 | -0.0419 |
| CLIMIA | 901 | -$69.28 | -0.0077 |
| CLIMSP | 735 | -$121.80 | -0.0166 |
| CLIMSY | 647 | -$195.00 | -0.0301 |
| CLINYC | 1,168 | -$333.99 | -0.0286 |
| CLIOKC | 740 | -$333.30 | -0.0450 |
| CLIPHL | 1,139 | -$357.76 | -0.0314 |
| CLIPHX | 585 | -$245.93 | -0.0420 |
| CLISAN | 89 | -$17.80 | -0.0200 |
| CLISAT | 659 | -$325.64 | -0.0494 |
| CLISDF | 59 | -$44.60 | -0.0756 |
| CLISEA | 781 | -$210.07 | -0.0269 |
| CLISFO | 852 | -$154.01 | -0.0181 |
| CLITTN | 72 | -$8.84 | -0.0123 |

Discovery period for comparison:

**By YES / NO** (primary, discovery)

| Group | Bets | Net dollars (10 contracts) | Mean net per contract |
|---|---|---|---|
| NO | 4,946 | -$262.64 | -0.0053 |
| YES | 3,378 | $84.31 | +0.0025 |

**By entry price** (primary, discovery)

| Group | Bets | Net dollars (10 contracts) | Mean net per contract |
|---|---|---|---|
| 1-10c | 1,599 | -$61.69 | -0.0039 |
| 10-30c | 1,515 | -$405.45 | -0.0268 |
| 30-70c | 3,975 | $498.24 | +0.0125 |
| 70-90c | 1,139 | -$177.63 | -0.0156 |
| 90-99c | 96 | -$31.81 | -0.0331 |

**By NBM version** (primary, discovery)

| Group | Bets | Net dollars (10 contracts) | Mean net per contract |
|---|---|---|---|
| 4.0 | 1,156 | $209.01 | +0.0181 |
| 4.1 | 2,618 | $221.07 | +0.0084 |
| 4.2 | 4,024 | -$344.00 | -0.0085 |
| 4.3 | 526 | -$264.42 | -0.0503 |

## NBM bias and spread by station (morning run, observed high minus TXN)

| Station | Days | Mean error | Std of error | Mean NBM XND |
|---|---|---|---|---|
| CLIATL | 250 | +0.02 | 2.46 | 2.30 |
| CLIAUS | 1,174 | +0.06 | 2.58 | 2.06 |
| CLIBOS | 249 | +0.01 | 3.60 | 2.38 |
| CLIDCA | 264 | +0.28 | 3.63 | 2.37 |
| CLIDEN | 628 | -0.07 | 3.10 | 2.11 |
| CLIDFW | 243 | -0.07 | 2.36 | 2.30 |
| CLIEWR | 47 | -0.02 | 2.13 | 2.06 |
| CLIHOU | 243 | -0.28 | 1.89 | 2.19 |
| CLILAS | 271 | -0.06 | 1.53 | 1.62 |
| CLILAX | 582 | +0.17 | 2.47 | 1.63 |
| CLIMDW | 1,613 | +0.47 | 2.86 | 1.95 |
| CLIMIA | 1,170 | +0.05 | 1.53 | 1.31 |
| CLIMSP | 250 | -0.10 | 2.74 | 2.66 |
| CLIMSY | 270 | -0.18 | 2.33 | 1.86 |
| CLINYC | 1,600 | +0.27 | 2.59 | 1.82 |
| CLIOKC | 243 | -0.05 | 3.06 | 2.73 |
| CLIPHL | 626 | +0.22 | 2.72 | 1.98 |
| CLIPHX | 251 | -0.06 | 1.73 | 1.94 |
| CLISAN | 54 | +0.65 | 3.45 | 1.80 |
| CLISAT | 243 | -0.21 | 2.23 | 2.32 |
| CLISDF | 45 | +0.42 | 2.81 | 2.29 |
| CLISEA | 271 | -0.08 | 2.48 | 2.04 |
| CLISFO | 271 | -0.07 | 3.01 | 2.15 |
| CLITTN | 47 | +0.02 | 1.97 | 2.11 |

All stations: 10,905 days, mean error +0.13 F, std 2.59 F. NBM's own XND averages about 2 F, so the calibrated sigma (k near 1.2-1.4) is wider than NBM states. Per station and year, including the calibrated b and k, are in `results.json` (`nbm.morning.by_station_year`). Station biases are small (largest: Midway +0.47, San Diego +0.65 on 54 days), so calibration adds little.

## Data and coverage

- Kalshi: 11,827 station-day events (67,806 bracket markets) from 24 CLI stations, 2021-08-06 to 2026-10-12; 11,432 usable after the exactly-one-YES / no-overlap / complete-candles checks. Excluded: no_yes 173, overlapping_ranges 308, multiple_yes 138, scalar_result 4.
- Observed highs come from the market's own `expiration_value` field (the official CLI integer high; it was cross-checked against which bracket settled YES, 1 mismatch). 461 events have no value; they are traded but not used in calibration.
- NBM: 4,078 files read, 2 not in the bucket (2022-01-05 18Z, 2026-06-17 00Z), 0 failures, 88.2 GB read in 30.0 minutes on the Ohio server (5 threads, nice 10). Versions 4.0 to 5.0. 24 of 24 stations present in every file. Output: `data/nbm_station_max.csv` (97,872 rows), `data/nbm_files.csv` (per-file log with S3 LastModified).
- Morning: model built for 11,367 station-days; 66,789 markets, 46,415 with a valid bid and ask (69%); 20,373 had a candle but a missing bid or ask (skipped and counted); 0 had no candle yet. Skipped days: 46 (06Z file published after the decision time) and 18 (pool < 15 days, first weeks of 2021).
- Evening: 11,394 station-days; 66,951 markets, 57,327 valid (86%); 9,449 missing a side; 175 had no candle yet and 144 were not open. Run used: 00Z of event day 7,669 times, 18Z of the day before 3,744 times (in summer the 00Z run is usually published after 21:00 ET).
- Prices: data/weather.sqlite already holds 1-minute candles (bid/ask closes) for every market, so no new Kalshi downloads were needed. The quote is the last candle at or before the decision instant (median age under an hour).

## How each ambiguous rule was read (stricter reading when unclear)

- **Stations and series:** the 24 series whose `wx_series.station` is a CLI code (KXHIGHNY, KXHIGHCHI, ...). The 2024-25 KXHIGHHOU series has no CLI code and is excluded. Event dates 2021-08-06 to 2026-10-12 (no market existed in 2021-01 to 2021-07).
- **Morning decision:** 09:00 local STANDARD time on the event day (so 14:00Z for Eastern in every season, 16:00Z for Phoenix). The 06Z run is used only if its S3 LastModified is at or before that instant; otherwise the day is skipped.
- **Evening decision:** 21:00 America/New_York clock time (DST-aware) on the prior evening. 00Z run of the event day's UTC date if its LastModified <= that instant, else the 18Z run of the prior day if its LastModified <= it, else the day is skipped. This applies to all stations.
- **Quote:** 'last hourly candle closing at or before the decision time' is implemented as the last stored 1-minute candle at or before the decision instant (at the top of the hour this equals the hourly close). Valid = bid and ask both present, bid <= ask, market open. A bid of 0 (no bidders) is a missing bid; this is common for far-tail brackets.
- **Model:** TXN for the 12Z-00Z window ending 00Z of day D+1 (UTC date D). sigma = max(1, k*XND); a day with XND missing is skipped (none occurred). Brackets: between [floor, cap]; 'greater F' = F+1 and up; 'less C' = C-1 and below.
- **Calibration history:** a prior day counts only if (a) it is an earlier event day of that station, (b) its market had settled (Kalshi settled timestamp) at or before the decision instant, (c) its observed high and a forecast of the same decision type exist. So the most recent day is often not yet usable. 'Previous 60 event days' = the 60 most recent qualifying days. Pooled values = maximum-likelihood fit on the union of every station's 60 qualifying days (needs >= 15, else no model that day). Station n < 15 uses pooled values; otherwise weight n/(n+30) on the station's fit.
- **Fit details (not specified):** exact-degree likelihood Phi((y+.5-mu)/s) - Phi((y-.5-mu)/s); Nelder-Mead from three starting points; b limited to +-15 F and k to 0.05-10 (no calibration ever touched a limit: b ranged -1.5 to +2.0 F, k 0.4 to 2.4; sigma is floored at 1).
- **Trading:** buy YES if p - ask - fee >= theta (fee per contract as in the pre-registration, order fee rounded up to $0.0001); buy NO if bid - p - fee(1-bid) >= theta; the larger edge if both (cannot happen with bid <= ask). 10 contracts, hold to settlement; net uses the settled result.
- **Split:** discovery = event day < 2025-07-01; unseen = 2025-07-01 to 2026-10-12.
- **Bootstrap:** clusters are all station-days with at least one valid quote in the period (so days without bets count as zero), 10,000 multinomial resamples, seed 20261021. The 'lower bound' used for rule 2 is the 2.5th percentile (two-sided 95% interval), the stricter reading; the 5th percentile is also in results.json.
- **Coin flip:** 500 copies per version and period (seed 20261021). Same station-days and same number of bets per station-day; markets drawn at random without replacement from that day's valid-quote markets, side random; same price (ask for YES, 1-bid for NO) and fee. 'As well' = total net on the unseen period >= the model's.
- **$/week:** unseen net dollars at 10 contracts / number of ISO weeks (by event date) with at least one bet.
- **Brier / log loss:** bracket level on all valid-quote brackets of that decision type (market p = (bid+ask)/2). Rule 6 uses the morning numbers for the primary.

## Things worth knowing

- The market is better informed than a 06Z forecast at 9 am local time: temperatures have been rising for hours. The morning model is the worst of the six (unseen -3.2c per contract); the evening model is about as good as random picks (coin-flip share 22-38%) but still loses 1.9c per contract.
- Discovery looked near break-even (morning 5c: -0.2c; evening 5c: +1.2c per contract) and unseen got worse (-3.2c and -1.9c). The market changed: the average bid-ask spread on valid-quote brackets was about 11c in discovery and about 3c in the unseen period, so the market is both tighter and sharper later. The discovery period is also mostly NYC, Chicago, Austin, Miami, Denver, Philadelphia and LAX; most other cities exist only in the unseen period.
- The model bets on a large share of brackets (about 59% of valid-quote brackets in the unseen morning version at 5c) because it disagrees with the market by 5c or more about 70% of the time. That is noise from over-confident tails, not hidden edge.
- Late-published NBM files were found and skipped by the LastModified rule (8 of 1,891 06Z files were published 7+ hours late; 7 morning days dropped).
- Look-ahead checks: the build asserts every used run has LastModified <= the decision instant, and the calibration only uses days settled before the decision (see tests).

## Files

`PREREGISTRATION.md`, `pull_nbm.py` (server bulk pull), `nbm_text.py` (parser), `nbm_runsel.py` (decision times and run choice), `nbm_model.py` (probability math, fee, calibration), `flatten_nbm.py`, `build_rows.py` (model rows and quotes), `analyze.py` (backtest, bootstrap, coin flip), `make_readme.py`, `test_model.py` (15 tests), `results.json`, `build_summary.json`, `rows_morning.csv.gz`, `rows_evening.csv.gz`, `bets_primary.csv.gz`, `days.txt`. Data: `data/nbm_station_max.csv`, `data/nbm_files.csv`, `data/nbm_raw.jsonl`.
