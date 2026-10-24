# Weather step 1: does Kalshi's Weather Company settlement disagree with the NWS daily max/min?

Descriptive study, run 2026-10-21. Read-only. No prices, candles, bids, asks or trades were read (only market definitions: strikes, rules text, event dates, settlement results).

## Answer

Since the switch, the bracket Kalshi paid matched the official NWS value in 3,127 of 3,128 events (99.97%). One event mismatched. In the NWS era, which is the control, 12 of 13,667 events mismatched (0.09%). The two rates are not statistically different (Fisher exact p = 0.48). Mismatches are essentially never, so the idea has nothing to work with at this level.

## 1. When the settlement source switched

Taken from the rules text of every market (`rules_primary`/`rules_secondary` naming "The Weather Company" vs "National Weather Service"). No series has NWS markets after its first TWC market.

| Series | Last NWS event day | First TWC event day |
|---|---|---|
| 20 KXHIGH* series (AUS, CHI, DEN, LAX, MIA, NY, PHIL, TATL, TBOS, TDAL, TDC, THOU, TLV, TMIN, TNOLA, TOKC, TPHX, TSATX, TSEA, TSFO) | 2026-08-13 | 2026-08-14 |
| 20 KXLOW* series (TATL, TAUS, TBOS, TCHI, TDAL, TDC, TDEN, THOU, TLAX, TLV, TMIA, TMIN, TNOLA, TNYC, TOKC, TPHIL, TPHX, TSATX, TSEA, TSFO) | 2026-08-13 | 2026-08-14 |
| KXHIGHTSAN, KXLOWTSAN | none (new series) | 2026-08-19 |
| KXHIGHTEWR, KXHIGHTTTN, KXLOWTEWR, KXLOWTTTN | none | 2026-08-26 |
| KXHIGHTSDF, KXLOWTSDF | none | 2026-08-28 |

The switch is clean: every series changed on event day 2026-08-14. The TWC rules still say "Data for CLINYC ..." and point to weather.com/kalshi, "Daily Climate Report".

## 2. Method

- Payout interval per event: every settled market of the event gives a constraint (YES means the whole-degree value lies inside the market's range, NO means outside). The consistent set of whole-degree values over all markets of the event is the paid interval. Conventions: `between` floor..cap inclusive; `greater` floor means value >= floor+1; `less` cap means value <= cap-1. An event is excluded if the set is empty (inconsistent), not contiguous, or no market paid YES. In practice no event was inconsistent or non-contiguous.
- Official NWS value: NCEI GHCN-Daily `by_station` files (TMAX/TMIN, tenths of a degree C) for the CLI site (24 stations, mapping in `fetch_noaa.py`). Values are converted back to whole F with `round(C*9/5+32)`. The round trip F to tenths-C to F was tested exactly for every whole F from -80 to 139, since 1 F = 0.56 C is much bigger than the 0.1 C step. Records with a quality flag were dropped. 13,667 NWS-era and 3,033 TWC-era events use GHCN.
- GHCN lags: last day available is 2026-10-17. For 2026-10-18 and 2026-10-19 (95 TWC events) the value comes from the NWS CLI product (api.weather.gov), the final morning report only.
- NWS CLI cross-check: api.weather.gov keeps only about 7 days of CLI products per site. Where both exist (233 station-days, max and min), GHCN and the final CLI agreed on all 233.
- Mismatch = NWS value outside the paid interval. Size = distance from the NWS value to the nearest edge of the paid interval.

## 3. Results

| Era | Events | Mismatches | Rate | 95% interval (Wilson) |
|---|---|---|---|---|
| NWS era (control, 2021-09 to 2026-08-13) | 13,667 | 12 | 0.088% | 0.050% to 0.153% |
| NWS era without the Houston Jul 2026 cluster | 13,662 | 7 | 0.051% | 0.025% to 0.106% |
| TWC era (2026-08-14 to 2026-10-19) | 3,128 | 1 | 0.032% | 0.006% to 0.181% |

Control by year: 2021 0/154, 2022 0/661, 2023 0/1,194, 2024 1/1,548, 2025 2/2,684, 2026 9/7,426. The control is about zero, so the pipeline is validated. High vs low: see results.json (`by_source_kind`).

TWC era by city: 23 of 24 stations had 0 mismatches (n = 106 to 134 each). Only Miami (CLIMIA) has one, 1 of 134. Full table in results.json (`by_station`).

### The TWC-era mismatch

| Event | NWS (GHCN) | Kalshi paid | TWC vs NWS |
|---|---|---|---|
| KXHIGHMIA-26AUG29 | 85 F | 90-91 F | TWC higher by 5 F |

The GHCN value for that day comes from source flag `1` (NWS CF6 form), not from the CLI. A 5 F gap is large enough that a data problem on either side is plausible. The CLI for that day is no longer on api.weather.gov (about 7 days kept), so it was not cross-checked. I did not resolve it.

### NWS-era mismatches (control), all 12

Size and direction: +1 x4, +2 x1, +3 x3, +4 x1, -1 x2, -3 x1 (positive = Kalshi paid a higher bracket than GHCN's value).

| Group | Events |
|---|---|
| KXHIGHTHOU 2026-07-02 to 07-06 (5 consecutive days) | GHCN 1 to 4 F below the paid bracket |
| KXLOWTSEA 2026-07-22 and 07-28 | +1 |
| KXLOWTBOS 2026-05-26 (-3), KXLOWTMIN 2026-05-12 (+3) | |
| KXHIGHMIA 2024-11-27 (+3), KXHIGHMIA 2025-01-20 (-1), KXHIGHNY 2025-02-22 (-1) | source flag `W` |

9 of the 12 come from GHCN source flag `1` (the CF6 form, available from 2025-01-19). CF6 is a monthly form that can differ from the final CLI, so the control mismatches are probably CF6-vs-CLI differences or data errors in GHCN, not Kalshi settling against something other than the NWS. This was not verified, because the old CLIs are not retrievable.

### Patterns

- Edge, temperature, date: with one mismatch there is nothing to cluster. For the TWC era, among matched events the NWS value sat on the low edge of the paid bracket 1,290 times, the high edge 1,287 times and in a tail 550 times, so there is no one-sided edge effect. The one mismatch is not on an edge (5 F away).
- C/F conversion artifacts: none seen. The one TWC mismatch is at 85 F; nothing else mismatches at any whole-F value in the TWC era.
- By month in the TWC era: Aug 1/778, Sep 0/1,440, Oct 0/910.
- Direction: the single TWC case is TWC higher. One case says nothing about direction.
- Station and date clusters in the control: Houston Hobby July 2026 (5 days) is the only cluster.

## 4. Exclusions (events)

| Reason | Events |
|---|---|
| Non-US or no CLI station series (London, Paris, Tokyo, etc., plus old 2021-22 formats without a station) | 70 (NWS era) |
| Old 2021-22 KXHIGHNY/KXHIGHCHI format with 1-2 markets per event, none YES (ladder incomplete) | 148 |
| Settled at a fractional value ("scalar") | 8 (2026-01 to 2026-07, all NWS era; KXHIGHTDC x3, KXLOWTMIA x3, KXHIGHMIA, KXLOWTATL) |
| Source not NWS or TWC (2021 formats) | 44 |
| No NOAA value found | 1 NWS era, 2 TWC era |
| Inconsistent or non-contiguous ladders | 0 |
| Mixed source inside one event, station conflict between series and rules text | 0 |

17,068 events total, 16,795 analysed. No scalar or voided events in the TWC era.

## 5. Caveats

- GHCN is not the CLI. From 2025-01 its values come from the CF6 form (flag `1`); before that mostly from WBAN/ASOS daily summaries (flag `W`). Only the last 7 days could be checked against the actual CLI (233 of 233 agreed).
- The TWC era is short (67 days, fall season). Summer thunderstorms, extreme heat/cold or sensor outages may behave differently.
- The Wilson interval for the TWC era runs up to 0.18%, so a true mismatch rate of a few tenths of a percent is not excluded.
- Settlement here means the market result stored by Kalshi's public API. Any correction after settlement is not modelled.
- Not checked: whether Kalshi's settlement time vs data updates matters for trading (needs prices, deliberately not looked at).
- Rules say TWC "may be subject to rounding and conversion differences from the final reported value". The data show no sign of this in whole-degree terms.

## Files

| File | What |
|---|---|
| `fetch_defs.py` | Market definitions and results (DB read-only plus cached live GETs); whitelisted fields only |
| `fetch_noaa.py` | GHCN-Daily and NWS CLI downloads (cached) |
| `analyze.py` | Interval derivation, comparison, summaries; writes `results.json`, `events.csv` |
| `extra_stats.py` | Wilson intervals and Fisher test added to `results.json` |
| `twc_core.py`, `test_twc_core.py` | Interval, conversion, mismatch and CLI-parsing logic and 9 tests (all pass) |
| `events.csv` | One row per analysed event: interval, NWS value, source, mismatch |
| `cache/` | Raw responses (about 50 MB): market defs, GHCN files, CLI products, station list and readme |
