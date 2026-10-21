# Research log #31 results: NBA game-winner model v2 (official injury reports + open box scores)

**Primary verdict (full v2, theta = 4c): FAIL**

- FAIL  rule 1 mean net per contract > 0 in discovery and unseen: discovery +0.0182, unseen -0.0166
- FAIL  rule 2 unseen 95% bootstrap lower bound > 0: CI [-0.0564, +0.0242]
- PASS  rule 3 at least 100 unseen bets: n_unseen 390
- FAIL  rule 4 coin flip: at most 5% of 500 copies do as well: share 0.454
- FAIL  rule 5 at least $2/week at 10 contracts (unseen): $-3.08/wk
- FAIL  rule 6 model unseen Brier <= market Brier (or within 0.002 while rules 1-5 pass): model 0.1920 vs market 0.1803, diff +0.0117

Games 1317 (discovery 659, unseen 658), 2025-10-21T23:30:00+00:00 to 2026-06-14T00:30:00+00:00, split at 2026-01-23T01:00:00+00:00 (same split as #24 / #29). Games with a usable T-1h quote on both teams: 1313. Unseen weeks with games: 21.

In plain English: dollars are for 10 contracts per bet. A bet is made only when the model's chance for a team, minus the ask and the fee, is at least theta. The pass bar needs every rule to hold on games the model never saw.

## All 6 versions

| version | disc bets | unseen bets | disc mean net | unseen mean net | unseen 95% CI | coin-flip share | $/wk |
|---|---|---|---|---|---|---|---|
| full theta=2c | 476 | 466 | +0.0064 | -0.0195 | [-0.0574, +0.0192] | 0.504 | -4.33 |
| full theta=4c | 405 | 390 | +0.0182 | -0.0166 | [-0.0564, +0.0242] | 0.454 | -3.08 |
| full theta=6c | 330 | 309 | +0.0387 | -0.0330 | [-0.0769, +0.0118] | 0.722 | -4.85 |
| elo_only theta=2c | 479 | 431 | +0.0050 | -0.0317 | [-0.0715, +0.0079] | 0.714 | -6.50 |
| elo_only theta=4c | 399 | 339 | +0.0179 | -0.0252 | [-0.0705, +0.0194] | 0.604 | -4.07 |
| elo_only theta=6c | 336 | 265 | +0.0119 | -0.0111 | [-0.0612, +0.0382] | 0.364 | -1.40 |

Mean net is dollars per contract after the exact fee, held to settlement. $/wk = 10 contracts.

## v1 vs v2 vs the market (Brier and log loss; same games = valid T-1h quotes)

| set | n | market Brier | v1 full Brier | v2 full Brier | v2 Elo-exact-only Brier | v2 full+market avg | market log loss | v1 log loss | v2 full log loss |
|---|---|---|---|---|---|---|---|---|---|
| discovery | 659 | 0.2155 | 0.2207 | 0.2215 | 0.2245 | 0.2150 | 0.6196 | 0.6311 | 0.6328 |
| unseen | 654 | 0.1803 | 0.1931 | 0.1920 | 0.1897 | 0.1828 | 0.5350 | 0.5695 | 0.5675 |

Discovery numbers for the models are in-sample; the market has no fitting.

## Feature weights (logistic regression on discovery)

**full**: intercept (home flag, standardized scale) +0.257
| feature | weight per 1 sd | weight per raw unit | discovery sd |
|---|---|---|---|
| elo_diff | +0.398 | +0.0037 | 107.72 |
| net_diff | +0.396 | +0.0369 | 10.73 |
| rest_diff | -0.072 | -0.0846 | 0.85 |
| b2b_diff | -0.088 | -0.1826 | 0.48 |
| inj_diff | -0.052 | -0.0081 | 6.46 |

**elo_only**: intercept (home flag, standardized scale) +0.252
| feature | weight per 1 sd | weight per raw unit | discovery sd |
|---|---|---|---|
| elo_diff | +0.665 | +0.0062 | 107.72 |

## Injury-report coverage

- Games: 1317; with a qualifying report file (published >= 60 min before tip): 1317; game listed in that report: 1314 (discovery 659/659, unseen 655/658).
- One team NOTYETSUBMITTED: 0; both: 0. Games with a regular Out/Doubtful: 1067. Games with non-zero inj_diff: 1282.
- Files: {'distinct_files_used': 815, 'mb': 68.897068, 'files_with_parse_problems': 0, 'parse_problem_items': 0, 'files_with_error': 0, 'report_lag_min_mean': 82.31783092887878, 'report_lag_min_min': 63.05, 'report_lag_min_max': 119.96666666666667}

## Calibration (unseen; 10 buckets)

| bucket | full n | full mean p | full win rate | market n | market mean p | market win rate |
|---|---|---|---|---|---|---|
| 0.1-0.2 | 25 | 0.164 | 0.000 | 45 | 0.147 | 0.067 |
| 0.2-0.3 | 49 | 0.255 | 0.102 | 41 | 0.252 | 0.122 |
| 0.3-0.4 | 68 | 0.353 | 0.279 | 70 | 0.348 | 0.371 |
| 0.4-0.5 | 100 | 0.452 | 0.450 | 84 | 0.450 | 0.512 |
| 0.5-0.6 | 113 | 0.550 | 0.593 | 89 | 0.560 | 0.551 |
| 0.6-0.7 | 100 | 0.643 | 0.660 | 93 | 0.654 | 0.602 |
| 0.7-0.8 | 114 | 0.746 | 0.763 | 94 | 0.756 | 0.830 |
| 0.8-0.9 | 75 | 0.849 | 0.813 | 81 | 0.851 | 0.778 |
| 0.9-1.0 | 10 | 0.928 | 1.000 | 38 | 0.929 | 0.974 |

## Bets by edge size (full v2, theta 2c bets)

| edge | disc n | disc mean | unseen n | unseen mean |
|---|---|---|---|---|
| 0.02-0.04 | 71 | -0.0606 | 76 | -0.0346 |
| 0.04-0.06 | 75 | -0.0723 | 81 | +0.0459 |
| 0.06-0.10 | 131 | +0.0360 | 123 | -0.0258 |
| 0.10-9.00 | 199 | +0.0406 | 186 | -0.0377 |

## Bets where a regular is Out / Doubtful (full v2, theta 4c)

| group | disc n | disc mean | unseen n | unseen mean |
|---|---|---|---|---|
| regular Out/Doubtful (either team) | 308 | +0.0400 | 325 | -0.0437 |
| no regular Out/Doubtful | 97 | -0.0512 | 65 | +0.1190 |
| game listed in a qualifying report | 405 | +0.0182 | 388 | -0.0189 |

Model vs v1 vs market Brier by regular-Out games:

- discovery regular_out: n 521, v2 0.2186, v1 0.2182, market 0.2163
- discovery no_regular_out: n 138, v2 0.2325, v1 0.2302, market 0.2127
- unseen regular_out: n 543, v2 0.1845, v1 0.1863, market 0.1681
- unseen no_regular_out: n 111, v2 0.2287, v1 0.2267, market 0.2400

Sensitivity, primary bets only in games listed in a qualifying report: disc n 405, mean +0.0182; unseen n 388, mean -0.0189.

Feature stats / matching: {'listed_Questionable': 1143, 'listed_nonavailable': 12448, 'no_window_games': 3784, 'listed_Out': 10697, 'unmatched_never_in_box': 822, 'box_matched': 1317, 'listed_Available': 1570, 'listed_Probable': 439, 'listed_Doubtful': 169, 'box_matched_off_by_one_day': 1, 'unmatched_name_examples': {'thomassorber': 97, 'fredvanvleet': 88, 'damianlillard': 88, 'kyrieirving': 82, 'tyresehaliburton': 82, 'terryrozier': 80, 'tamarbates': 58, 'danteexum': 53, 'nikoladjurisic': 53, 'georgesniang': 52, 'elijohnndiaye': 33, 'hansenyang': 19, 'alextoohey': 13, 'kevonharris': 12, 'malikwilliams': 6, 'kylemangas': 4, 'julesbernard': 1, 'tyrekekey': 1}}. Quote exclusions: {'no valid bid/ask at entry': 4}. Loader exclusions: {'preseason (T before 2025-10-21)': 43, "no clean result ['scalar', 'scalar']": 3}.

## Interpretation choices

- Box scores: hoopR-nba-data player_box_2026 / team_box_2026 (ESPN-derived, CC BY 4.0). Only the 30 NBA teams are used (All-Star exhibition teams dropped). ESPN abbreviations mapped GS/NO/NY/SA/UTAH/WSH to GSW/NOP/NYK/SAS/UTA/WAS.
- Game matching Kalshi <-> box: (ticker date, home code, away code), +-1 day tolerance; unmatched games are counted. Exact margin = box final home score - away score. Winner disagreement with the Kalshi result is counted (such a margin is not used).
- Elo / rolling net margin / rest use the Kalshi game list exactly as v1 (same games, K=20, home 100, 538 multiplier, 1500 start); only the margin source changes (exact box score). A game with no matched box score updates Elo as a 1-point win and is left out of the rolling net margin (v1 rule).
- Injury report publication time = the PDF /CreationDate (stricter than the file-name stamp: hourly files stamped H were created at H:30). The game's report = the file with the latest CreationDate <= scheduled tip - 3600 s (tip = Kalshi occurrence - 3 h, drift.load_nba).
- A game is 'covered' when its matchup (AWAY@HOME, same date) appears in that report. A team shown only as NOTYETSUBMITTED, or a game absent from the report, contributes 0 (neutral); a game with no qualifying report at all has inj_diff = 0. A covered team with no listed players is a real 0.
- Player value window = the player's team's last 15 box-score games with a strictly earlier local date (fewer early in the season; 2025-26 only, no carry-over, as Elo). The player's games = the games in that window he played (minutes > 0), n = their count. Average plus-minus per minute = sum(plus_minus)/sum(minutes) over those games (pooled, minute-weighted); average minutes = sum(minutes)/n over the same games. value = pm_per_min x avg_minutes x n/(n+10)  (= mean plus-minus per game x n/(n+10)). A listed player with no games for that team in the window has n = 0, value 0.
- P(miss): Out 1.0, Doubtful 0.75, Questionable 0.5, Probable 0.1, Available 0.0. Missing value of a team = sum over its listed players of value x P(miss). Feature inj_diff = home missing value minus away missing value (as v1's miss_diff).
- Name matching: injury 'Last,First' (suffix glued to last name, e.g. PippenJr.,Scotty) and box 'First Last Jr.' are both normalised to accent-free lower-case letters of 'first+last' with the generational suffix removed, matched within the listed team's window. 'Unmatched' = listed (non-Available) players with no box-score row at all this season on any team; 'no window games' = matched name but zero games in the team's last-15 window (value 0).
- Regular (breakdown only): a player with n >= 5 and average minutes >= 20 in the window. 'Regular Out/Doubtful' = such a player listed Out or Doubtful for either team (stored as key_missing).
- Fit, standardisation (discovery mean/sd), ridge, bets, bootstrap, coin flip, pass-bar rules: model.py unchanged. Elo-exact-only model = elo_diff alone (with exact margins). Full v2 columns = elo_diff, net_diff, rest_diff, b2b_diff, inj_diff.
- Quotes: T-1h = last hourly candle closing at or before tip - 3600 s (drift.at); a game is usable when BOTH team markets have a valid bid/ask there (drift.valid_quote), as in #24. No staleness limit. The close candle is not needed.
- Market probability for Brier / log loss = home mid / (home mid + away mid) at T-1h (removes the overround).
- Model: logistic regression for P(home win), one row per game, intercept = home flag. No scikit-learn or scipy in the venv, so a numpy Newton/IRLS fit with a tiny ridge (1e-6 on the slopes, none on the intercept) is used. Features are standardized with discovery mean / std only. Fitted on ALL discovery games with a clean result (quotes not required).
- Edge for a team = model prob - YES ask - fee(ask) (10 contracts, drift.fee). Bet the team with the larger edge if edge >= theta (1e-12 tolerance); at most one bet per game; hold to settlement; net P&L per contract = result - ask - fee.
- $/week = unseen mean net x 10 x unseen bets / unseen weeks; unseen weeks = ISO weeks with at least one unseen game (as #24's amendment).
- Rule 6 'within 0.002 while the betting rules still pass' = model Brier - market Brier <= 0.002 AND rules 1-5 pass.
- Coin flip: 500 copies keeping the same bet games, random side (uniform) per game, that side's own ask and fee, seed 20261021 (fresh generator per version). Share = fraction of copies with mean net >= the strategy's.
- Bootstrap: 10,000 resamples of the unseen bets (one per game), seed 20261021, percentile 2.5 / 97.5.
- 'Key player missing' = a regular with rolling average >= 15 points is absent for either team. 'Availability disagrees with the market' = |home - away missing points| >= 8 and the team with fewer missing points is the market underdog (normalized mid < 0.5).

## Data provenance and download notes (added after the run; no analysis choice depends on them)

- Box scores: GitHub sportsdataverse/hoopR-nba-data, files nba/player_box/parquet/player_box_2026.parquet and nba/team_box/parquet/team_box_2026.parquet
  (season 2026 = 2025-26; 2025-26 regular season, play-in and playoffs, 2025-10-21 to 2026-06-13; 1326 games, 30-team games used). Last commit to the
  player_box file 2026-08-26 (307b791). License: LICENSE.md states CC BY 4.0 for the repo contents (GitHub's API reports "NOASSERTION"; a
  separate LICENSE file is the MIT template). Provenance: the repo's own README shows it is built from ESPN's NBA game feeds (hoopR-nba-raw -> hoopR-nba-data),
  i.e. ESPN-derived, not NBA.com. Files: 0.6 MB + 0.14 MB per season. Downloaded to data/raw/nba_open/.
- Injury reports: official.nba.com's 2025-26 page redirects to the 2026-27 season page, which lists only current-season links. The PDFs of the NBA's own CDN
  (ak-static.cms.nba.com/referee/injury/) are still served for 2025-26 under the NBA's file-name pattern, which was probed (HTTP 200/403 only): hourly
  names (Injury-Report_YYYY-MM-DD_05PM.pdf) until about 2025-12-20, 15-minute names (..._05_30PM.pdf) from about 2025-12-28. 815 files, 68.9 MB, in data/raw/nba_injury/
  (1702 probe requests, 0.25 s apart, 4 KB range-reads for the CreationDate, then full downloads of the chosen files). Parser: pdfplumber (pip-installed into .venv).
- Hourly-era files stamped H (e.g. 05PM) are created at H:30 and their header says H:30; using the file-name stamp would have been look-ahead of up to 30 min.
  Publication time = PDF CreationDate (stricter). Lag of the chosen report before tip: min 63 min, mean 82, max 120.
- Names: of 558 distinct listed non-Available players, 546 match a box-score row; the other 12 never appear in the 2025-26 box score even as did-not-play
  (long-term injured or two-way players such as VanVleet, Irving, Haliburton, Sorber; 4.1% of listings): value 0, correct. None looked like a spelling failure.
- 3 of 1317 games not found in the chosen report (value neutral 0): GSW@MIN 24 Jan (the report lists it under 25 Jan, i.e. it was moved), and CLE@NYK 21 and 23 May (reports at
  those times list only OKC@SAS, so those games had no listed players or were not yet posted).
