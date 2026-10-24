# FEATURES - research #59: exact column names and definitions

File: `reports/output/nba_score_model/team_games_features.parquet`, one row per (game_id, team); the checker's file
`check/features_check.parquet` must use the same column names for the 300 rows of `sample_ids.csv`.

Population: hoopR team box, `season_type == 2` (regular season; the NBA Cup final is filed that way), both teams NBA franchises (hoopR codes
NJ, SEA, BKN, ..., the 32 codes of #57's `NBA_HOOPR`), exactly one home and one away row per game.  All-star / exhibition rows are dropped
by the franchise filter.  Season label S = "2008-09" has hoopR file year 2009; S-1 = the previous label (2008-09 uses 2007-08).
Franchise continuity for the "previous season" lookups: NJ -> BKN, SEA -> OKC (hoopR codes otherwise unchanged).

Possessions of a team-game: `poss = 0.96 * (FGA - OREB + TOV + 0.44 * FTA)`.  TOV = total_turnovers if finite and >= turnovers else
turnovers; turnovers only in the hoopR files of 2007-08..2011-12 (where total_turnovers is exactly doubled) and wherever total_turnovers is
NaN (all of 2017-18).  Game possessions `gposs` = mean(poss of the two teams).

"Before the game": rows with ET date strictly less than the game's ET date D (ET date of hoopR `game_date_time`).  Season windows use
games of S only.  `last 10` / `last 5` = the most recent min(10, n) / min(5, n) games before D (fewer than 10 games available: all of them).
If the window has 0 games the feature = that team's S-1 full-season value.

## Identifier columns
| column | meaning |
|---|---|
| game_id | hoopR/ESPN game id (int64) |
| season | "2008-09" ... "2026-27" |
| date | ET date of the game (datetime64[ns]) |
| team | X: the team whose points are the target, hoopR team_abbreviation (raw code) |
| opp | Y: the opponent, hoopR code |

## Feature columns (float64)
| column | definition |
|---|---|
| ortg_x_season | 100 * sum(X points) / sum(gposs) over X's games of S before D |
| ortg_x_l10 | same over X's last 10 games before D |
| ortg_x_l5 | same over X's last 5 games before D |
| ortg_x_prev | same over X's full S-1 regular season |
| drtg_y_season / _l10 / _l5 / _prev | 100 * sum(points Y allowed) / sum(gposs), Y's games, windows as above |
| pace_x_season / _l10 / _prev | mean gposs over X's games (season to date / last 10 / full S-1) |
| pace_y_season / _l10 / _prev | the same for Y |
| lg_ortg | league 100 * sum(points over all team-game rows before D in S) / sum(gposs of those rows); S-1 full-season value while fewer than 50 games have been played in S before D |
| lg_pace | league mean gposs per game before D in S (same 50-game fallback to the S-1 full-season mean) |
| home | 1.0 if X is the home team else 0.0 |
| rest_x, rest_y | min((D - date of the team's previous game in S) in days - 1, 4); the team's first game of S = 4 |
| b2b_x, b2b_y | 1.0 if rest == 0 else 0.0 |
| gp_x, gp_y | number of the team's games in S before D |
| dos | days from the first ET date of S's regular season (league-wide) to D |

## Target
| column | definition |
|---|---|
| pts | X's final points, OT included (hoopR team_score) |

Notes: the "Missing windows" rule is applied per column (e.g. ortg_x_l5 falls back to ortg_x_prev only when X has 0 earlier games in S).
`ortg_*_prev` / `pace_*_prev` is X's own franchise value; if a franchise had no S-1 games the value would be NaN (does not occur 2008-09+).
