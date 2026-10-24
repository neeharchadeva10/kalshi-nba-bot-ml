# Research log #32 results: NBA game-winner model v3 (5 seasons, sportsbook moneylines + Kalshi)

**Data licence note (private research only):** the moneylines come from kyleskom/NBA-Machine-Learning-Sports-Betting `Data/OddsData.sqlite` (scraped from Sportsbook Review by that repo's author, no licence). The file is kept under data/raw/nba_odds_samples/ and must not be republished or redistributed. Only aggregate results are written here. The book and the time of the listed line are unknown. Box scores: hoopR-nba-data (CC BY 4.0). Injury reports: the NBA's own PDFs.

**Primary verdict (full model, theta = 4%, 2024-25 against moneylines): FAIL**

- FAIL  rule 1 mean return per $1 > 0 on 2024-25 AND mean net > 0 on the 2025-26 Kalshi confirmation: 2024-25 -0.0425, Kalshi 2025-26 -0.0242
- FAIL  rule 2 2024-25 95% bootstrap lower bound > 0: CI [-0.1588, +0.0774]
- PASS  rule 3 at least 200 bets in 2024-25: n 660
- FAIL  rule 4 coin flip: at most 5% of 500 random-side copies do as well: share 0.538
- FAIL  rule 5 at least $2/week at $10 per bet on 2024-25: $-7.79/wk
- FAIL  rule 6 2024-25 model Brier <= de-vigged moneyline Brier (or within 0.002 while rules 1-5 pass): model 0.2109 vs moneyline 0.2012, diff +0.0097

In plain English: every bet is $1 at the sportsbook's listed price (its margin included); the table also shows what that is per week at $10 a bet. A bet is placed only when the model's chance for a team beats the price's implied chance by at least theta. Model A was fitted on 2021-22 to 2023-24 and never saw 2024-25. Model B (2021-22 to 2024-25) is checked on Kalshi 2025-26 (10 contracts, ask plus fee, held to settlement).

## All 6 versions: Model A on 2024-25 against moneylines

| version | in-sample bets (21-22..23-24) | in-sample mean | 2024-25 bets | mean return per $1 | 95% CI | coin-flip share | $/wk at $10 |
|---|---|---|---|---|---|---|---|
| full theta=2% | 2643 | -0.0028 | 836 | -0.0180 | [-0.1179, +0.0860] | 0.254 | -4.17 |
| full theta=4% | 2134 | -0.0153 | 660 | -0.0425 | [-0.1588, +0.0774] | 0.538 | -7.79 |
| full theta=6% | 1654 | -0.0204 | 510 | -0.0313 | [-0.1611, +0.1077] | 0.494 | -4.44 |
| elo_only theta=2% | 2698 | -0.0335 | 863 | -0.0224 | [-0.1244, +0.0803] | 0.402 | -5.37 |
| elo_only theta=4% | 2157 | -0.0327 | 700 | -0.0396 | [-0.1521, +0.0795] | 0.560 | -7.70 |
| elo_only theta=6% | 1733 | -0.0580 | 567 | -0.0482 | [-0.1727, +0.0785] | 0.638 | -7.59 |

In-sample columns are the training seasons (they do not count). Return is per $1 staked.

## Same 6 versions: Model B on Kalshi 2025-26 (confirmation)

| version | 2025-26 Kalshi bets | mean net per contract | 95% CI | coin-flip share | $/wk at 10 contracts |
|---|---|---|---|---|---|
| full theta=2% | 833 | -0.0235 | [-0.0537, +0.0064] | 0.656 | -5.76 |
| full theta=4% | 637 | -0.0242 | [-0.0583, +0.0113] | 0.582 | -4.53 |
| full theta=6% | 478 | -0.0282 | [-0.0670, +0.0114] | 0.660 | -3.96 |
| elo_only theta=2% | 854 | -0.0269 | [-0.0557, +0.0023] | 0.676 | -6.77 |
| elo_only theta=4% | 670 | -0.0179 | [-0.0506, +0.0151] | 0.444 | -3.54 |
| elo_only theta=6% | 507 | -0.0099 | [-0.0481, +0.0299] | 0.312 | -1.48 |

Kalshi games 1317, mapped to box scores {'matched': 1317, 'matched_off_by_one_day': 1}, with a valid T-1h quote on both teams 1313; weeks 34; quote exclusions {'no valid bid/ask at entry': 4}.

## Data coverage per season

| season | box games | odds matched | report file found | game listed in report | one team not yet submitted | home win rate | mean report lead (min) | PDFs used | PDF parse errors | files with problems |
|---|---|---|---|---|---|---|---|---|---|---|
| 2021-22 | 1323 | 1323 | 1323 | 1323 | 0 | 0.548 | 82 | 708 | 0 | 0 |
| 2022-23 | 1320 | 1318 | 1320 | 1318 | 0 | 0.581 | 92 | 691 | 0 | 0 |
| 2023-24 | 1319 | 1253 | 1319 | 1318 | 0 | 0.547 | 100 | 651 | 0 | 0 |
| 2024-25 | 1321 | 1321 | 1321 | 1320 | 0 | 0.545 | 100 | 662 | 0 | 0 |
| 2025-26 | 1322 | 0 | 1322 | 1320 | 0 | 0.555 | 82 | 821 | 0 | 0 |

Odds join summary: {'2021-22': {'odds_rows': 1323, 'box_games': 1323, 'matched': 1323, 'matched_off_by_one_day': 1, 'box_games_without_odds': 0}, '2022-23': {'odds_rows': 1319, 'box_games': 1320, 'matched': 1318, 'odds_row_unmatched': 1, 'box_games_without_odds': 2}, '2023-24': {'odds_rows': 1255, 'box_games': 1319, 'matched': 1253, 'odds_row_unmatched': 2, 'box_games_without_odds': 66}, '2024-25': {'odds_rows': 1326, 'box_games': 1321, 'matched': 1321, 'odds_row_unmatched': 5, 'box_games_without_odds': 0}}

Join validation against the odds file's own Win_Margin / Points columns: {'2021-22': {'checked': 1323, 'winner_disagrees_with_odds_file_Win_Margin': 0, 'total_points_disagree': 0}, '2022-23': {'checked': 1318, 'winner_disagrees_with_odds_file_Win_Margin': 0, 'total_points_disagree': 0}, '2023-24': {'checked': 1253, 'winner_disagrees_with_odds_file_Win_Margin': 0, 'total_points_disagree': 0}, '2024-25': {'checked': 1321, 'winner_disagrees_with_odds_file_Win_Margin': 0, 'total_points_disagree': 2}}

Unmatched rows are listed in results_v3.json under odds_join.unmatched_detail.

## Brier score and log loss by season (lower is better)

Model A and B rows for a season are in-sample when that season was in the fit (flag in results_v3.json).

**2021-22** (A in-sample: True, B in-sample: True)
| set | n | Brier | log loss |
|---|---|---|---|
| moneyline devig (odds-matched games) | 1323 | 0.2097 | 0.6069 |
| model A full (odds-matched games) | 1323 | 0.2220 | 0.6349 |
| model A elo_only (odds-matched games) | 1323 | 0.2242 | 0.6399 |
| model B full (odds-matched games) | 1323 | 0.2221 | 0.6352 |
| model B elo_only (odds-matched games) | 1323 | 0.2241 | 0.6397 |
| model A full (all games) | 1323 | 0.2220 | 0.6349 |
| model A elo_only (all games) | 1323 | 0.2242 | 0.6399 |
| model B full (all games) | 1323 | 0.2221 | 0.6352 |
| model B elo_only (all games) | 1323 | 0.2241 | 0.6397 |

**2022-23** (A in-sample: True, B in-sample: True)
| set | n | Brier | log loss |
|---|---|---|---|
| moneyline devig (odds-matched games) | 1318 | 0.2173 | 0.6254 |
| model A full (odds-matched games) | 1318 | 0.2281 | 0.6483 |
| model A elo_only (odds-matched games) | 1318 | 0.2292 | 0.6507 |
| model B full (odds-matched games) | 1318 | 0.2282 | 0.6482 |
| model B elo_only (odds-matched games) | 1318 | 0.2294 | 0.6510 |
| model A full (all games) | 1320 | 0.2279 | 0.6478 |
| model A elo_only (all games) | 1320 | 0.2290 | 0.6502 |
| model B full (all games) | 1320 | 0.2279 | 0.6478 |
| model B elo_only (all games) | 1320 | 0.2292 | 0.6505 |

**2023-24** (A in-sample: True, B in-sample: True)
| set | n | Brier | log loss |
|---|---|---|---|
| moneyline devig (odds-matched games) | 1253 | 0.1980 | 0.5802 |
| model A full (odds-matched games) | 1253 | 0.2096 | 0.6064 |
| model A elo_only (odds-matched games) | 1253 | 0.2120 | 0.6108 |
| model B full (odds-matched games) | 1253 | 0.2096 | 0.6065 |
| model B elo_only (odds-matched games) | 1253 | 0.2120 | 0.6107 |
| model A full (all games) | 1319 | 0.2103 | 0.6082 |
| model A elo_only (all games) | 1319 | 0.2128 | 0.6125 |
| model B full (all games) | 1319 | 0.2104 | 0.6083 |
| model B elo_only (all games) | 1319 | 0.2127 | 0.6124 |

**2024-25** (A in-sample: False, B in-sample: True)
| set | n | Brier | log loss |
|---|---|---|---|
| moneyline devig (odds-matched games) | 1321 | 0.2012 | 0.5858 |
| model A full (odds-matched games) | 1321 | 0.2109 | 0.6083 |
| model A elo_only (odds-matched games) | 1321 | 0.2128 | 0.6129 |
| model B full (odds-matched games) | 1321 | 0.2105 | 0.6074 |
| model B elo_only (odds-matched games) | 1321 | 0.2128 | 0.6128 |
| model A full (all games) | 1321 | 0.2109 | 0.6083 |
| model A elo_only (all games) | 1321 | 0.2128 | 0.6129 |
| model B full (all games) | 1321 | 0.2105 | 0.6074 |
| model B elo_only (all games) | 1321 | 0.2128 | 0.6128 |

**2025-26** (A in-sample: False, B in-sample: False)
| set | n | Brier | log loss |
|---|---|---|---|
| model A full (all games) | 1322 | 0.2075 | 0.6021 |
| model A elo_only (all games) | 1322 | 0.2070 | 0.6015 |
| model B full (all games) | 1322 | 0.2072 | 0.6014 |
| model B elo_only (all games) | 1322 | 0.2070 | 0.6014 |

**2025-26 on games with a valid Kalshi T-1h quote**
| set | n | Brier | log loss |
|---|---|---|---|
| kalshi T-1h mid (normalized) | 1313 | 0.1980 | 0.5774 |
| model B full | 1313 | 0.2075 | 0.6019 |
| model B elo_only | 1313 | 0.2072 | 0.6020 |
| model A full | 1313 | 0.2078 | 0.6027 |
| model A elo_only | 1313 | 0.2072 | 0.6020 |

## Feature weights (logistic regression, training seasons)

**A full** (trained 2021-22, 2022-23, 2023-24, 3962 games): intercept (home flag, standardized scale) +0.264
| feature | weight per 1 sd | weight per raw unit | training sd |
|---|---|---|---|
| elo_diff | +0.717 | +0.0053 | 134.73 |
| net_diff | +0.019 | +0.0019 | 9.54 |
| rest_diff | +0.067 | +0.0760 | 0.88 |
| b2b_diff | -0.132 | -0.2730 | 0.48 |
| inj_diff | -0.028 | -0.0048 | 5.76 |

**A elo_only** (trained 2021-22, 2022-23, 2023-24, 3962 games): intercept (home flag, standardized scale) +0.262
| feature | weight per 1 sd | weight per raw unit | training sd |
|---|---|---|---|
| elo_diff | +0.708 | +0.0053 | 134.73 |

**B full** (trained 2021-22, 2022-23, 2023-24, 2024-25, 5283 games): intercept (home flag, standardized scale) +0.252
| feature | weight per 1 sd | weight per raw unit | training sd |
|---|---|---|---|
| elo_diff | +0.726 | +0.0051 | 143.10 |
| net_diff | +0.055 | +0.0055 | 10.00 |
| rest_diff | +0.067 | +0.0771 | 0.87 |
| b2b_diff | -0.138 | -0.2863 | 0.48 |
| inj_diff | -0.007 | -0.0011 | 6.13 |

**B elo_only** (trained 2021-22, 2022-23, 2023-24, 2024-25, 5283 games): intercept (home flag, standardized scale) +0.250
| feature | weight per 1 sd | weight per raw unit | training sd |
|---|---|---|---|
| elo_diff | +0.754 | +0.0053 | 143.10 |

## Calibration, 2024-25 (10 buckets)

| bucket | model n | model mean p | model win rate | moneyline n | moneyline mean p | moneyline win rate |
|---|---|---|---|---|---|---|
| 0.0-0.1 | 3 | 0.082 | 0.000 | 7 | 0.093 | 0.000 |
| 0.1-0.2 | 42 | 0.161 | 0.071 | 73 | 0.153 | 0.055 |
| 0.2-0.3 | 101 | 0.257 | 0.248 | 113 | 0.255 | 0.292 |
| 0.3-0.4 | 157 | 0.352 | 0.408 | 181 | 0.350 | 0.376 |
| 0.4-0.5 | 204 | 0.453 | 0.407 | 156 | 0.448 | 0.404 |
| 0.5-0.6 | 236 | 0.550 | 0.525 | 177 | 0.555 | 0.599 |
| 0.6-0.7 | 247 | 0.651 | 0.648 | 217 | 0.653 | 0.594 |
| 0.7-0.8 | 187 | 0.748 | 0.733 | 195 | 0.745 | 0.738 |
| 0.8-0.9 | 125 | 0.840 | 0.848 | 179 | 0.844 | 0.844 |
| 0.9-1.0 | 19 | 0.925 | 0.947 | 23 | 0.919 | 0.957 |

## Calibration, 2025-26 (10 buckets, games with a Kalshi quote)

| bucket | model B n | model mean p | model win rate | Kalshi n | Kalshi mean p | Kalshi win rate |
|---|---|---|---|---|---|---|
| 0.0-0.1 | 4 | 0.081 | 0.000 | 23 | 0.076 | 0.000 |
| 0.1-0.2 | 47 | 0.167 | 0.085 | 75 | 0.155 | 0.120 |
| 0.2-0.3 | 98 | 0.253 | 0.235 | 97 | 0.249 | 0.247 |
| 0.3-0.4 | 166 | 0.352 | 0.319 | 164 | 0.351 | 0.384 |
| 0.4-0.5 | 200 | 0.451 | 0.475 | 181 | 0.449 | 0.497 |
| 0.5-0.6 | 232 | 0.550 | 0.591 | 182 | 0.556 | 0.544 |
| 0.6-0.7 | 220 | 0.649 | 0.673 | 197 | 0.652 | 0.635 |
| 0.7-0.8 | 208 | 0.746 | 0.740 | 184 | 0.752 | 0.777 |
| 0.8-0.9 | 117 | 0.844 | 0.803 | 166 | 0.850 | 0.789 |
| 0.9-1.0 | 21 | 0.918 | 0.905 | 44 | 0.927 | 0.977 |

## 2024-25 return by edge size (full, theta 2% bets)

| edge | bets | mean return per $1 |
|---|---|---|
| 0.02-0.04 | 176 | +0.0740 |
| 0.04-0.06 | 150 | -0.0805 |
| 0.06-0.10 | 218 | -0.0250 |
| 0.10-9.00 | 292 | -0.0360 |

## 2024-25 return by bet type (full, theta 4% bets)

| group | bets | mean return per $1 |
|---|---|---|
| favourite | 122 | -0.1012 |
| underdog | 538 | -0.0292 |
| home side | 327 | -0.1127 |
| away side | 333 | +0.0265 |
| regular season | 603 | -0.0364 |
| play-in | 2 | -1.0000 |
| playoffs | 55 | -0.0746 |
| game listed in injury report | 660 | -0.0425 |

## 2025-26 Kalshi return by edge size (full, theta 2% bets)

| edge | bets | mean net per contract |
|---|---|---|
| 0.02-0.04 | 196 | -0.0214 |
| 0.04-0.06 | 159 | -0.0122 |
| 0.06-0.10 | 229 | -0.0482 |
| 0.10-9.00 | 249 | -0.0098 |

Sensitivity, v2-style injury value (no zeroing for a player's first 5 games), full theta 4%, 2024-25: 659 bets, mean -0.0539, CI [-0.1678, +0.0667] (lead only).

## Odds-timing checks

{
 "odds_2025_26_table_rows": 554,
 "joined_to_box": {
  "odds_rows": 554,
  "box_games": 1322,
  "matched": 554,
  "box_games_without_odds": 768
 },
 "overlap_with_kalshi_valid_quotes": 553,
 "mean_abs_diff_ml_vs_kalshi_prob": 0.031210442168045253,
 "corr_ml_vs_kalshi": 0.9716911594947627,
 "brier_ml_devig": 0.21493627211617988,
 "brier_kalshi": 0.21435608594338088,
 "logloss_ml_devig": 0.6183881277556059,
 "logloss_kalshi": 0.6162336127981599,
 "first_game_date": "2025-10-21",
 "last_game_date": "2026-01-07",
 "spread_vs_devig_logit": {
  "n": 5223,
  "corr": 0.8703119443333996,
  "logit_per_point_slope": 0.12333958853752854,
  "note": "spread is the home-favourite margin in the file; a closing line typically gives about 0.14-0.15 logit per point"
 }
}

Feature stats: {'listed_Out': 49495, 'listed_nonavailable': 60603, 'no_window_games': 17088, 'unmatched_never_in_box_this_season': 4014, 'listed_Probable': 2608, 'listed_Questionable': 7580, 'listed_Available': 5484, 'listed_Doubtful': 920, 'value_zeroed_first5': 5205, 'season_rollovers': 4, 'unmatched_name_examples': {'lonzoball': 168, 'nikolatopic': 106, 'jameswiseman': 104, 'danilogallinari': 102, 'jasonpreston': 98, 'thomassorber': 97, 'daronholmes': 96, 'dariosaric': 95, 'vlatkocancar': 94, 'zionwilliamson': 90, 'fredvanvleet': 88, 'damianlillard': 88, 'jamalmurray': 87, 'bensimmons': 86, 'damionlee': 86, 'kawhileonard': 84, 'chetholmgren': 84, 'ejliddell': 83, 'stevenadams': 83, 'kendricknunn': 82, 'tjwarren': 82, 'jonathanisaac': 82, 'collingillespie': 82, 'jaylenclark': 82, 'saddiqbey': 82}}

## Data provenance and download notes (added after the run; no analysis choice depends on them)

- Box scores: GitHub sportsdataverse/hoopR-nba-data, nba/team_box/parquet/team_box_{2022..2024}.parquet and nba/player_box/parquet/player_box_{2022..2024}.parquet (plain HTTPS GET from raw.githubusercontent.com, 1 s apart) added to the 2025 and 2026 files already in data/raw/nba_open/. CC BY 4.0, ESPN-derived. Games used: 1323 / 1320 / 1319 / 1321 / 1322 (the 4 All-Star games of 2025-26 and the exhibition teams are dropped).
- Moneylines: kyleskom/NBA-Machine-Learning-Sports-Betting Data/OddsData.sqlite (4.6 MB), the copy already at data/raw/nba_odds_samples/OddsData.sqlite, opened read-only. No licence; scraped from Sportsbook Review by that author; PRIVATE RESEARCH ONLY: do not republish or redistribute it (keep it under data/raw/, which is not for publication). The book and the capture time of each line are not documented. The file's 2025-26 table (554 rows, to 2026-01-07) is used only for the timing check.
- Injury reports: official PDFs of the NBA's own CDN (ak-static.cms.nba.com/referee/injury/), fetched by the file-name pattern only (HTTP GET, no site scraping, no login). Hourly names Injury-Report_YYYY-MM-DD_HHPM.pdf exist for every season (2021-22 to 2025-26); 15-minute names (_HH_MMPM) appear from 2025-12-22. 5715 probes in total (2171 downloads, the rest read from files already on disk, 0.25 s between requests, at most 3 workers at a time); 3533 distinct files (225 MB) were chosen as game reports; the other existing files probed are also kept in data/raw/nba_injury/. A missing file answers HTTP 403.
- File format changed: the 2021-22 / 2022-23 PDFs (iTextSharp, /CreationDate at the END of the file, two-word headers 'Game' 'Date', names with spaces, multi-word reasons) need the old-layout parser (injury_parse_v3.parse_old); from 2023-24 (37 files already in 2022-23) the layout is the one parsed in #31 (single-word headers, glued names, /CreationDate in the first 4 KB). The layout is detected per file from the header. Hourly files stamped H are created at H:30 in every season.
- Parse check: 3533 files parsed, 0 exceptions, 0 flagged problem items (every status word paired with a player), 0 files with zero rows. Per-season counts are in the coverage table and results_v3.json (coverage.<season>.parse). The practical check is coverage: every game but 6 is found in its report.
- Postponed games (the January 2025 Los Angeles fire and weather postponements; verified for all 5) appear twice, at the original and the rescheduled date, with different prices in the odds file; 5 odds rows on the original dates match no box game and are dropped (listed above).
## Interpretation choices

- Fit (both models): logistic regression for P(home win), one row per game, intercept = home flag, numpy IRLS with ridge 1e-6 on the slopes (model.fit_logit), features standardized with TRAINING mean/sd only. Fitted on ALL box-score games of the training seasons (odds and Kalshi quotes are not needed to fit). Model A = 2021-22..2023-24; Model B = 2021-22..2024-25.
- Moneylines: odds_2021-22_new, odds_2022-23_new, 2023-24, 2024-25 tables. ML values like '+145' with non-breaking spaces are parsed to integers. Join to box games on (local date, home team); exact date first, then +-1 day; away team must agree. Odds rows that match no game, and box games with no odds, are listed in the results (unmatched).
- Implied probability of American odds p: 100/(p+100) if p > 0 else -p/(-p+100) (raw, vig included). Edge = model P(team) - implied P(team). Bet the side with the larger edge (home on a tie) if edge >= theta (1e-12 tolerance); at most one bet per game; $1 stake; profit = p/100 (p>0) or 100/(-p) (p<0) if the team wins, else -1.
- Bootstrap: 10,000 resamples of the 2024-25 bets (games), seed 20261021, percentile 2.5/97.5 (drift.bootstrap_ci). Coin flip: 500 copies, same bet games, random side (uniform) per game at that side's own American odds, seed 20261021 (fresh generator per version); share = fraction of copies with mean return >= the strategy's.
- $/week at $10 per bet = mean return per $1 x 10 x bets / weeks, weeks = ISO weeks (UTC tip time) with at least one 2024-25 odds-matched game (weeks with games). Kalshi confirmation: mean net per contract x 10 x bets / ISO weeks of the Kalshi 2025-26 game list.
- Rule 6 Brier is computed on the same 2024-25 odds-matched games for the model and for the de-vigged moneyline (home implied / (home implied + away implied)). 'Within 0.002 while rules 1-5 pass' = model - moneyline <= 0.002 AND rules 1-5 pass.
- Kalshi confirmation (Model B): games = drift.load_nba() (#24: clean results, T >= 2025-10-21) mapped to box games on (ticker date, home, away) with +-1 day tolerance; features come from the box-score row (full 5-season Elo with carry-over); quotes, edge = model p - YES ask - fee(ask, 10 contracts), hold to settlement, exactly as #31 (model.quotes_for / make_bets); theta equals the primary version's theta. Kalshi game result is used for settlement (box winner disagreements are counted).
- Return by favourite vs underdog: favourite = the bet side's American odds < 0 (underdog > 0; even money counts as underdog). Season types: 2 regular season, 3 playoffs, 5 play-in (shown separately).
- Game universe = every hoopR box-score game of the 30 NBA teams in 2021-22..2025-26 (regular season, play-in, playoffs; no preseason, All-Star dropped), sorted by (tip, game id). Elo and rolling stats run over this complete list (not over the Kalshi list). Tip = ESPN game_date_time (scheduled start). Local date = ESPN game_date (US Eastern).
- Elo: K=20, home advantage 100, 538 multiplier, exact box-score margin for every game (v1/v2 formulas, features.elo_update). 2021-22 starts every team at 1500 (v1 value). At the first game of each later season every rating is replaced by 0.75 * (rating after the team's last game of the previous season, playoffs included) + 0.25 * 1505.
- Rolling 10-game net margin and rest (calendar days, cap 4, first game of a season = 4) are computed within a season only and reset at each season start (only Elo carries over, as the pre-registration says).
- Player value = v2 recipe (last 15 games of the player's team in the same season with a strictly earlier local date; pooled plus-minus per minute x average minutes x n/(n+10)). 'A player's first 5 games of a season get shrinkage only' is read as: a listed player who has played fewer than 5 games for any team earlier in the same season (box-score minutes > 0, strictly earlier date) gets value 0 (the prior); no previous-season data is used. Sensitivity column inj_diff_v2 keeps the v2 value (n/(n+10) shrinkage only) and is reported for the primary version.
- Injury report per game: the file with the latest PDF CreationDate <= tip - 3600 s (as v2). Matchup AWAY@HOME on the game's local date must appear in that report for the game to be 'covered'; otherwise inj_diff = 0 (neutral). Team-name map: ESPN display names without spaces (+ 'LAClippers', 'LosAngelesClippers').
- The 2021-22 / 2022-23 PDFs use an older layout (two-word headers, names with spaces); injury_parse_v3.parse_old reads it and returns the same text form ('TrentJr.,Gary'), so the v2 name normalisation is unchanged.
