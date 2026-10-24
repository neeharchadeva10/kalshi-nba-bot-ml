# #66 NFL totals: weather data (built 2026-10-23)

Weather and schedule only. No score, total or line column is ever read (`lib.load_games` reads a fixed safe column list and
asserts none of the outcome columns is present; tested).

## Files

| File | What |
|---|---|
| `stadiums.csv` | 67 rows, one per games.csv stadium_id used 1999-2026 (plus a name override for one mislabelled row). roof_category open-air 48 / dome 12 / retractable 7 |
| `coord_check.csv` | gross-error check of every coordinate against the Open-Meteo geocoding API (all 67 within 30 km of the city centre) |
| `wind_forecasts.parquet` | 1,700 open-air games 2018-2026 (columns as specified, plus game_type, week, roof, fc_wind_d2_mph, hf_temp_f, era5_temp_f) |
| `game_universe.csv` | every 2018-2026 game with in/out of the open-air set and the reason |
| `stadium_grid.csv` | model grid cell and elevation Open-Meteo used per stadium |
| `WEATHER_CHECK.md` | coverage, correlations, unit sanity, missing values (weather vs weather only) |
| `fetch_archive.py`, `lib.py`, `build_stadiums.py`, `verify_coords.py`, `make_weather_check.py` | code. Rerunning `fetch_archive.py` costs no requests (all raw responses cached in `cache/`) |
| `test_weather.py` | 21 tests |
| `../../../../scripts/nfl_wind_forecast.py` + `_task.ps1` | forward fetcher (not scheduled) |

Rebuild: `build_stadiums.py`, `fetch_archive.py`, `make_weather_check.py`. 608 HTTP requests for the full archive (weighted cost
well under the free-tier 10,000/day), paced at <= 400 weighted calls per minute, with retry/backoff.

## Open-air definition (Amendment 0 item 6)

A game is in the set iff stadiums.csv roof_category == `open-air` AND games.csv roof is `outdoors` or `open`.
2018-2026: 2,499 games, 1,700 in the set; 796 excluded because the stadium is dome/retractable, 3 because games.csv
roof is not outdoors/open at an open-air stadium (the 2026 Melbourne, Paris and Munich games are tagged `dome` in games.csv although the
venues are physically open-air; Madrid has an empty roof).

## Issue-time guarantee

Requirement: the forecast used for B2 must have been issued at least 24 h before kickoff.

**What the docs say** (https://open-meteo.com/en/docs/previous-runs-api, read 2026-10-23):
- "_previous_day0 is the current model run (equivalent to the live Forecast API). _previous_day1 is the value that was predicted
  24 hours before valid time, _previous_day2 48 hours before, and so on up to day 7."
- "Requesting temperature_2m_previous_day1 returns the value predicted 24 hours before valid time"
- "Most models are archived from January 2024. GFS 2 m temperature extends back to March 2021."
- "For workflows that need the complete output of a specific model run (initialisation datetime, ...) use the Single Runs API
  instead, which stores each run independently and is queryable by its UTC initialisation time via the &run= parameter."

The docs do not say whether "24 hours before valid time" means the run INITIALISATION time is at or before valid - 24 h, or
something fuzzier. So I tested it against the Single Runs API (https://single-runs-api.open-meteo.com/v1/forecast?run=...), which
returns a named run:

- Boston (Gillette), 4 valid hours on 2026-10-21/22, default model selection (best_match), same as the archive uses.
  - `wind_speed_10m_previous_day1` equalled the HRRR run initialised **25, 26, 28 and 27 h** before the valid hour (value
    10.9, 14.5, 10.3, 12.5 mph). All four match exactly.
  - `_previous_day2` equalled the GFS run initialised **49, 50, 52 and 51 h** before (11.6, 10.7, 8.1, 11.8). All four match.
- Pure-model checks (gfs_global and icon_global, 2 valid hours each): `_previous_day1` = run 24 h (valid 18Z) or 27 h (valid 09Z)
  before; `_previous_day2` = run 48 h or 51 h before. Never a run closer than N x 24 h.
- Reading: `previous_dayN` = the latest model run initialised at least N x 24 h before the valid hour.

**Conclusion.** `previous_day1` guarantees model initialisation >= 24 h before the valid hour. The valid hour is the
kickoff hour floored (<= kickoff), so initialisation is also >= 24 h before kickoff. `fc_variant = previous_day1`,
`fc_issue_guarantee_h = 24` (set only where the value exists; `lib.variant_guarantee_h`, `lib.guarantee_ok`, tested).
`previous_day2` (guarantee 48 h) is also stored as `fc_wind_d2_mph` as the stricter alternative; it is highly correlated (0.78 with ERA5, same as day 1).

**Caveats (for Opus to decide on):**
1. The guarantee is on model **initialisation** time. A run becomes publicly available a few hours after initialisation
   (GFS about 4 h, HRRR about 1-2 h), so a day1 forecast was publicly *visible* about 20-23 h before kickoff in the worst
   case. "Issued" is taken as initialisation time, which is how the docs define the lead. If "visible >= 24 h" is wanted, use
   `fc_wind_d2_mph` (>= 48 h init, so >= about 43 h visible).
2. Nothing here is a look-ahead problem: the bet decision is at T-60 min, far after either time.
3. The default `best_match` model mix is chosen by Open-Meteo per location and can change over time (US: HRRR for day 1, GFS
   for day 2; elsewhere ICON/ECMWF). It is the same mix the forward fetcher uses (default), so forward and archive are consistent.
   The model per row is not exposed by the API.

## Availability by period (this changes the pre-registration)

Probed on Gillette (also London, Mexico City); confirmed by the full archive (WEATHER_CHECK.md coverage table):

| Variable | Previous Runs `_previous_day1` available from |
|---|---|
| wind_speed_10m, wind_gusts_10m, precipitation | **2024-01-19** (US sites); London 2024-02-04 |
| temperature_2m | 2021-03-24 |
| `_previous_day2` wind | 2024-01-20 |

So **forecast_ok is False for every game before 2024-01-19**: all of seasons 2018-2022, 2023 regular season. Only 5 playoff games
of the 2023 season (Jan 2024) have a day1 forecast. B2/B3 can be evaluated on the 2024 and 2025 seasons (380 games) and 2026 to date
(63 games), not "about 2021+" as the pre-registration says. There is no ">= 24 h-issued" forecast for earlier years from Open-Meteo.
Games before that have only the short-lead Historical Forecast (`hf_wind_mph`) and ERA5, which do not satisfy B2.

## Other notes

- All requests use `timezone=UTC`, `wind_speed_unit=mph`, `temperature_unit=fahrenheit`, `precipitation_unit=mm`. The response timezone is
  asserted to be GMT. Values are at the hour starting at the kickoff hour (instantaneous wind/temperature; precipitation is the
  sum of the preceding hour).
- ERA5 request uses `models=era5`; Open-Meteo's ERA5 lags about 5 days, so games in the last 7 days have `era5_wind_mph` empty.
- Previous-Runs/Historical-Forecast end at yesterday (UTC). One game (2026_07_NE_CHI, 2026-10-23 00:15Z) has no archive value yet; rerun
  `fetch_archive.py` later to fill it.
- games.csv `wind`/`temp` are missing for many games: 81 of 2022 (weeks 1-8), 30 of 2023, and recent 2026 games not yet filled. B1 can't use those.
- Stadium coordinates are from the builder's knowledge with each row's Wikipedia page as the source of record. They were **not**
  machine-checked against Wikipedia (rule: no service other than Open-Meteo). `verify_coords.py` checked each against the
  Open-Meteo geocoder (city centre within 30 km, all pass), and WEATHER_CHECK.md compares ERA5 with games.csv temperature per stadium.
  For wind the model grid is 0.1 degrees or coarser, so an error of 1-2 km does not change a value. The pre-registration gate
  "within 2 km of each stadium's listed coordinates" therefore still needs an independent check by Opus against its own list.
- games.csv reuses stadium_id JAX00 for a 2026 Tottenham Hotspur Stadium game; `lib.NAME_OVERRIDES` maps it to LON02.
- roof categories are one per stadium_id (no id changes roof type over time in this data). games.csv per-game roof still gates B2.
- Forward tape: `data/paper_tape/nfl_wind/<UTC date>.jsonl` (+ raw gz). First run on 2026-10-23 22:25Z wrote 8 games. B2 forward must take the
  latest line per game with `hours_before_kickoff >= 24`.
