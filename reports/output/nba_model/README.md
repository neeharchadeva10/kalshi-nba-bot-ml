# Research log #29 results: an NBA game-winner model (strength, rest, home, key-player availability)

**Primary verdict (full model, theta = 4c): FAIL**

- FAIL  rule 1 mean net per contract > 0 in discovery and unseen: discovery +0.0207, unseen -0.0272
- FAIL  rule 2 unseen 95% bootstrap lower bound > 0: CI [-0.0687, +0.0144]
- PASS  rule 3 at least 100 unseen bets: n_unseen 386
- FAIL  rule 4 coin flip: at most 5% of 500 copies do as well: share 0.696
- FAIL  rule 5 at least $2/week at 10 contracts (unseen): $-5.00/wk
- FAIL  rule 6 model unseen Brier <= market Brier (or within 0.002 while rules 1-5 pass): model 0.1931 vs market 0.1803, diff +0.0129

Games 1317 (discovery 659, unseen 658), 2025-10-21T23:30:00+00:00 to 2026-06-14T00:30:00+00:00, split at 2026-01-23T01:00:00+00:00 (same split as #24). Games with a usable T-1h quote on both teams: 1313. Unseen weeks with games: 21.

In plain English: dollars are for 10 contracts per bet. A bet is made only when the model's chance for a team, minus the ask and the fee, is at least theta. The pass bar needs every rule to hold on games the model never saw.

## All 6 versions

| version | disc bets | unseen bets | disc mean net | unseen mean net | unseen 95% CI | coin-flip share | $/wk |
|---|---|---|---|---|---|---|---|
| full theta=2c | 474 | 476 | +0.0145 | -0.0140 | [-0.0505, +0.0245] | 0.400 | -3.17 |
| full theta=4c | 400 | 386 | +0.0207 | -0.0272 | [-0.0687, +0.0144] | 0.696 | -5.00 |
| full theta=6c | 317 | 301 | +0.0139 | -0.0363 | [-0.0816, +0.0099] | 0.748 | -5.20 |
| elo_only theta=2c | 470 | 420 | +0.0126 | -0.0420 | [-0.0812, -0.0010] | 0.866 | -8.39 |
| elo_only theta=4c | 389 | 332 | +0.0090 | -0.0380 | [-0.0822, +0.0068] | 0.760 | -6.00 |
| elo_only theta=6c | 325 | 248 | +0.0140 | -0.0052 | [-0.0563, +0.0474] | 0.256 | -0.62 |

Mean net is dollars per contract after the exact fee, held to settlement. $/wk = 10 contracts.

## Model against the market (Brier and log loss; lower is better; same games = valid T-1h quotes)

| set | n | market Brier | full Brier | Elo-only Brier | full+market avg Brier | market log loss | full log loss | Elo-only log loss | avg log loss |
|---|---|---|---|---|---|---|---|---|---|
| discovery | 659 | 0.2155 | 0.2207 | 0.2247 | 0.2150 | 0.6196 | 0.6311 | 0.6401 | 0.6184 |
| unseen | 654 | 0.1803 | 0.1931 | 0.1895 | 0.1831 | 0.5350 | 0.5695 | 0.5624 | 0.5452 |

Discovery numbers for the model are in-sample (it was fitted there); the market has no fitting.

## Feature weights (logistic regression on discovery)

**full**: intercept (home flag, standardized scale) +0.257
| feature | weight per 1 sd | weight per raw unit | discovery sd |
|---|---|---|---|
| elo_diff | +0.311 | +0.0030 | 105.16 |
| net_diff | +0.464 | +0.0493 | 9.40 |
| rest_diff | -0.064 | -0.0751 | 0.85 |
| b2b_diff | -0.080 | -0.1663 | 0.48 |
| miss_diff | -0.063 | -0.0050 | 12.63 |

**elo_only**: intercept (home flag, standardized scale) +0.251
| feature | weight per 1 sd | weight per raw unit | discovery sd |
|---|---|---|---|
| elo_diff | +0.661 | +0.0063 | 105.16 |

## Calibration (unseen; 10 buckets)

| bucket | full n | full mean p | full win rate | market n | market mean p | market win rate |
|---|---|---|---|---|---|---|
| 0.0-0.1 | 2 | 0.090 | 0.000 | 19 | 0.074 | 0.000 |
| 0.1-0.2 | 29 | 0.155 | 0.000 | 45 | 0.147 | 0.067 |
| 0.2-0.3 | 54 | 0.255 | 0.130 | 41 | 0.252 | 0.122 |
| 0.3-0.4 | 68 | 0.353 | 0.338 | 70 | 0.348 | 0.371 |
| 0.4-0.5 | 106 | 0.453 | 0.462 | 84 | 0.450 | 0.512 |
| 0.5-0.6 | 106 | 0.559 | 0.604 | 89 | 0.560 | 0.551 |
| 0.6-0.7 | 89 | 0.646 | 0.652 | 93 | 0.654 | 0.602 |
| 0.7-0.8 | 104 | 0.750 | 0.750 | 94 | 0.756 | 0.830 |
| 0.8-0.9 | 77 | 0.853 | 0.831 | 81 | 0.851 | 0.778 |
| 0.9-1.0 | 19 | 0.923 | 0.895 | 38 | 0.929 | 0.974 |

## Bets by edge size (full model, theta 2c bets, held to settlement)

| edge | disc n | disc mean | unseen n | unseen mean |
|---|---|---|---|---|
| 0.02-0.04 | 74 | -0.0190 | 90 | +0.0427 |
| 0.04-0.06 | 83 | +0.0467 | 85 | +0.0048 |
| 0.06-0.10 | 142 | -0.0172 | 118 | -0.0569 |
| 0.10-9.00 | 175 | +0.0392 | 183 | -0.0230 |

## Bets by key-player-missing (full model, theta 4c)

| group | disc n | disc mean | unseen n | unseen mean |
|---|---|---|---|---|
| key player missing | 85 | +0.0112 | 125 | -0.0180 |
| no key player missing | 315 | +0.0233 | 261 | -0.0317 |
| both teams' listings observed | 178 | +0.0379 | 332 | -0.0233 |

Model vs market Brier by key-player-missing games:

- discovery key_missing: n 156, model 0.2488, market 0.2473
- discovery not_key_missing: n 503, model 0.2120, market 0.2057
- unseen key_missing: n 197, model 0.2038, market 0.1902
- unseen not_key_missing: n 457, model 0.1885, market 0.1760

## Availability vs the market

Games with a missing-points gap of at least 8: 460; availability points to the market underdog in 241 (+0.524). When they disagree the availability side won +0.349 and the market side won +0.651 (shares).

Sensitivity, primary bets only in games where both teams' PTS listings exist: disc n 178, mean +0.0379; unseen n 332, mean -0.0233.

Listing coverage: {'both_teams_observed': 898, 'one_team': 190, 'none': 229}. Margin sources: {'margin_ladder_bracket_estimate': 579, 'margin_ladder_exact': 8, 'margin_missing_or_rejected': 3, 'margin_exp_value': 727}. Quote exclusions: {'no valid bid/ask at entry': 4}. Loader exclusions: {'preseason (T before 2025-10-21)': 43, "no clean result ['scalar', 'scalar']": 3}.

## Interpretation choices

- Games: exactly drift.load_nba() (#24): clean KXNBAGAME results, T >= 2025-10-21, sorted by (tip, gid); split = first ceil(N/2) games discovery, the rest unseen (identical to #24).
- Margin: the exact final winning margin. KXNBASPREAD ladders are SPARSE (not every k.5 strike exists), so the ladder only brackets the margin ('over 3.5' YES = at least 4; the lowest NO strike above it caps it). The exact integer margin is the market's expiration_value (same on both teams' markets), used when it lies inside the ladder bounds; outside the bounds it is rejected and counted. With no expiration_value, the ladder is used only if its bounds coincide; otherwise (expiration_value is the text 'Yes'/'No' on about 590 events) the midpoint of the ladder bounds (the lower bound when no upper strike exists) is used as an ESTIMATE and counted as ladder_bracket_estimate. The winner comes from the KXNBAGAME result when no ladder YES exists. KXNBAWINMARGIN has no 2025-26 markets, so spreads are the only source. A game with no usable margin updates Elo as a 1-point win (smallest possible) and is left out of rolling net margin.
- Elo: K=20, home advantage 100, all teams start 1500 on 2025-10-21 (no carry-over), 538 NBA multiplier ((|margin|+3)^0.8) / (7.5 + 0.006 * winner_elo_diff), winner_elo_diff = winner's Elo (+100 if home) minus the loser's Elo (+100 if home). Feature = pre-game home Elo minus away Elo (the 100 home edge is left to the intercept).
- Rolling net margin: mean margin (from that team's side) over its last 10 games before g (fewer if fewer exist; 0 if none). Feature = home minus away.
- Rest: calendar days (ticker local game date) since the team's previous game, capped at 4; 4 for a team's first game. Back-to-back = rest 1. Features = home minus away rest and home minus away back-to-back flag.
- Home flag: every row is from the home team's point of view (target = home win), so the home flag is the intercept; the model learns its size there.
- Listing: a team-game is 'observed' when at least one KXNBAPTS market with that team's code exists for the game. The 'last 10 games' windows for regulars use the team's last 10 OBSERVED games (an unobserved game says nothing about who was listed). Unobserved current team-game -> missing points 0 (neutral). Sensitivity: the full model restricted to games observed for both teams is reported.
- Regulars: players (key = ticker part 3 minus the team code, e.g. VWEMBANYAMA1; a jersey-number change looks like a new player) listed for the team in >= 5 of its last 10 observed games.
- Points scored = floor_strike + 0.5 of the highest strike settled YES (0 if none). A game counts as 'played' for a player when at least one of his markets settled yes/no; if every market settled scalar (inactive, void) he did not play and the game is skipped. Rolling average = mean points over his last 10 played games (any team) before g.
- Missing points for a team = sum of rolling averages of its regulars not listed for it in g. A regular who is listed for the OTHER team in g (traded) is not counted as missing. Feature = home minus away.
- Key player missing (breakdown only): some regular with rolling average >= 15 points is not listed, for either team.
- Quotes: T-1h = last hourly candle closing at or before tip - 3600 s (drift.at); a game is usable when BOTH team markets have a valid bid/ask there (drift.valid_quote), as in #24. No staleness limit. The close candle is not needed.
- Market probability for Brier / log loss = home mid / (home mid + away mid) at T-1h (removes the overround).
- Model: logistic regression for P(home win), one row per game, intercept = home flag. No scikit-learn or scipy in the venv, so a numpy Newton/IRLS fit with a tiny ridge (1e-6 on the slopes, none on the intercept) is used. Features are standardized with discovery mean / std only. Fitted on ALL discovery games with a clean result (quotes not required).
- Edge for a team = model prob - YES ask - fee(ask) (10 contracts, drift.fee). Bet the team with the larger edge if edge >= theta (1e-12 tolerance); at most one bet per game; hold to settlement; net P&L per contract = result - ask - fee.
- $/week = unseen mean net x 10 x unseen bets / unseen weeks; unseen weeks = ISO weeks with at least one unseen game (as #24's amendment).
- Rule 6 'within 0.002 while the betting rules still pass' = model Brier - market Brier <= 0.002 AND rules 1-5 pass.
- Coin flip: 500 copies keeping the same bet games, random side (uniform) per game, that side's own ask and fee, seed 20261021 (fresh generator per version). Share = fraction of copies with mean net >= the strategy's.
- Bootstrap: 10,000 resamples of the unseen bets (one per game), seed 20261021, percentile 2.5 / 97.5.
- 'Key player missing' = a regular with rolling average >= 15 points is absent for either team. 'Availability disagrees with the market' = |home - away missing points| >= 8 and the team with fewer missing points is the market underdog (normalized mid < 0.5).
