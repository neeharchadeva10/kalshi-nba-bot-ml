# WEATHER_CHECK (weather vs weather only; no betting outcomes touched)

Generated from wind_forecasts.parquet (1700 open-air games, seasons 2018-2026).

## Coverage by season (non-null counts of open-air games)

| season | games | forecast_ok | fc_wind | fc_gust | fc_temp | fc_precip | fc_wind_d2 | hf_wind | era5_wind | obs_wind | obs_temp |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2018 | 198 | 0 | 0 | 0 | 0 | 0 | 0 | 198 | 198 | 197 | 197 |
| 2019 | 201 | 0 | 0 | 0 | 0 | 0 | 0 | 201 | 201 | 196 | 196 |
| 2020 | 176 | 0 | 0 | 0 | 0 | 0 | 0 | 176 | 176 | 176 | 176 |
| 2021 | 191 | 0 | 0 | 0 | 189 | 0 | 0 | 191 | 191 | 191 | 191 |
| 2022 | 188 | 0 | 0 | 0 | 185 | 0 | 0 | 188 | 188 | 107 | 107 |
| 2023 | 188 | 5 | 5 | 5 | 161 | 5 | 5 | 188 | 188 | 158 | 158 |
| 2024 | 187 | 187 | 187 | 187 | 187 | 187 | 187 | 187 | 187 | 182 | 182 |
| 2025 | 193 | 193 | 193 | 193 | 193 | 193 | 193 | 193 | 193 | 189 | 189 |
| 2026 | 178 | 63 | 63 | 63 | 63 | 63 | 63 | 63 | 54 | 44 | 44 |

## Game universe 2018-2026

- all games: 2499; in the open-air set: 1700
- excluded: stadium not open-air=796, games.csv roof not outdoors/open=3

- open-air games with kickoff still in the future (no weather yet): 114

## Wind: correlation and mean difference (mph; a - b)

| pair | n | corr | mean_diff | mae | rmse |
|---|---|---|---|---|---|
| fc (prev day1) vs ERA5 (a - b) | 439 | 0.778 | 0.68 | 2.21 | 2.86 |
| fc (prev day1) vs games.csv obs (a - b) | 420 | 0.759 | 0.26 | 2.17 | 2.93 |
| fc day2 vs ERA5 (a - b) | 439 | 0.778 | 0.77 | 2.16 | 2.87 |
| HF short-lead vs ERA5 (a - b) | 1576 | 0.822 | -0.46 | 1.86 | 2.54 |
| HF short-lead vs games.csv obs (a - b) | 1440 | 0.753 | -1.08 | 2.52 | 3.44 |
| ERA5 vs games.csv obs (a - b) | 1440 | 0.732 | -0.61 | 2.49 | 3.45 |
| fc day1 vs HF short-lead (a - b) | 448 | 0.781 | 1.01 | 2.12 | 2.92 |

### Same comparisons by period

| seasons | pair | n | corr | mean_diff | mae | rmse |
|---|---|---|---|---|---|---|
| 2024-2026 | ERA5 vs obs | 415 | 0.77 | -0.42 | 2.08 | 2.87 |
| 2024-2026 | HF vs obs | 415 | 0.771 | -0.76 | 2.22 | 2.92 |
| 2024-2026 | fc day1 vs obs | 415 | 0.76 | 0.27 | 2.16 | 2.92 |
| 2018-2023 | ERA5 vs obs | 1025 | 0.723 | -0.68 | 2.66 | 3.66 |
| 2018-2023 | HF vs obs | 1025 | 0.751 | -1.21 | 2.64 | 3.62 |
| 2018-2023 | fc day1 vs obs | 5 | 0.641 | -0.28 | 2.56 | 3.05 |

## Temperature: correlation and mean difference

| pair | n | corr | mean_diff | mae | rmse |
|---|---|---|---|---|---|
| fc day1 temp vs ERA5 temp (a - b, F) | 969 | 0.985 | -0.33 | 2.38 | 3.1 |
| fc day1 temp vs games.csv temp (a - b, F) | 845 | 0.98 | -0.02 | 2.69 | 3.66 |
| ERA5 temp vs games.csv temp (a - b, F) | 1440 | 0.986 | 0.43 | 2.07 | 2.92 |
| HF temp vs games.csv temp (a - b, F) | 1440 | 0.988 | 0.1 | 1.87 | 2.71 |

## Unit sanity (games.csv wind)

- games.csv wind: n=1440, mean 8.24, median 8.0, p95 17.0, max 44
- ERA5 10 m wind (mph, requested wind_speed_unit=mph): mean 7.59; mean ratio ERA5/obs (obs>0) = 1.04, median 0.90
- If games.csv were km/h or knots the ERA5/obs ratio would be about 0.62 or 1.15 relative to a true mph match; the mean-difference table above is the evidence (ERA5 10 m is a grid-cell mean and usually reads a little different from a stadium anemometer).
- games.csv wind is a whole number; ERA5/forecast values have 0.1 resolution.

## Per-stadium ERA5 vs games.csv (outliers would flag a wrong coordinate)

| stadium_id | n | wind_corr | wind_bias_era5_minus_obs | temp_bias_era5_minus_obs |
|---|---|---|---|---|
| BAL00 | 69.0 | 0.71 | 2.13 | -1.1 |
| BOS00 | 66.0 | 0.66 | 0.66 | 0.4 |
| BUF00 | 73.0 | 0.9 | -1.24 | 0.3 |
| CAR00 | 62.0 | 0.85 | -1.27 | 0.6 |
| CHI98 | 67.0 | 0.66 | 1.48 | 0.4 |
| CIN00 | 66.0 | 0.79 | -0.7 | 1.2 |
| CLE00 | 64.0 | 0.84 | -1.44 | 0.2 |
| DEN00 | 65.0 | 0.2 | -1.94 | 0.5 |
| GNB00 | 67.0 | 0.75 | -0.92 | 0.5 |
| JAX00 | 58.0 | 0.66 | -1.39 | 2.0 |
| KAN00 | 70.0 | 0.84 | -1.11 | 0.9 |
| LAX97 | 13.0 | 0.74 | 0.22 | -3.0 |
| LAX99 | 16.0 | 0.55 | 2.49 | 1.8 |
| LON00 | 6.0 | 0.63 | 2.48 | -4.6 |
| LON02 | 7.0 | 0.91 | -0.0 | -1.1 |
| MEX00 | 1.0 |  | -5.9 | -3.5 |
| MIA00 | 63.0 | 0.79 | -1.7 | -1.8 |
| NAS00 | 66.0 | 0.77 | -1.42 | 1.4 |
| NYC01 | 127.0 | 0.72 | 0.09 | -0.0 |
| OAK00 | 14.0 | 0.75 | -2.66 | 1.0 |
| PHI00 | 68.0 | 0.56 | -2.98 | 0.5 |
| PIT00 | 66.0 | 0.81 | -0.67 | 1.1 |
| RIO00 | 1.0 |  | -1.7 | -1.0 |
| SEA00 | 65.0 | 0.76 | 0.09 | 1.3 |
| SFO01 | 69.0 | 0.68 | -1.37 | 2.2 |
| TAM00 | 68.0 | 0.83 | 0.14 | -0.9 |
| WAS00 | 63.0 | 0.79 | -0.06 | 0.4 |

## Missing values (open-air games, all seasons)

```
{'fc_wind_mph': 1252, 'fc_gust_mph': 1252, 'fc_temp_f': 722, 'fc_precip_mm': 1252, 'hf_wind_mph': 115, 'era5_wind_mph': 124, 'obs_wind_mph': 260, 'obs_temp_f': 260}
```

### Missing within 2024-2026, by column

```
{'fc_wind_mph': 115, 'fc_gust_mph': 115, 'fc_temp_f': 115, 'fc_precip_mm': 115, 'hf_wind_mph': 115, 'era5_wind_mph': 124, 'obs_wind_mph': 143, 'obs_temp_f': 143}
```

### 2024-2026 games without a day1 forecast (kickoff past)

1 games

| game_id | stadium_id | kickoff_utc |
|---|---|---|
| 2026_07_NE_CHI | CHI98 | 2026-10-23 00:15:00+00:00 |
