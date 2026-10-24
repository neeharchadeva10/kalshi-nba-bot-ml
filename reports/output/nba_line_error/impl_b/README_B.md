# Implementation B (research #57): data cleaning, y, features f1-f10, possessions

Own code (lib_b.py, build_b.py, test_line_error_b.py), written from the pre-registration text only. Nothing from impl_a,
grid_lib function bodies, fa_lib or nba_totals_pace was read or imported (only grid_lib's top constants/option table and the
docstrings, as allowed). Rebuild with `python build_b.py` (about 3 min).
Outputs: features_b.parquet (21,554 rows, 2008-09..2025-26), excluded_b.csv, counts_b.csv, agreement_b.csv,
points_mismatch_b.csv, poss_check_b.csv, team_games_b.parquet. No outcome was analysed against any feature.

## Extra columns beyond the required ones
f5_alt, f9_alt (alternative reading, below), f8_termown (f8 with B = term-own pace), f8_proj (shrunk projection),
f8_unshrunk_proj, f8_b (shrink slope used), n_games_before (hoopR league games before the date).

## Readings chosen (all flagged for Opus)

### hoopR
- Regular season = season_type 2 with both teams NBA franchise ids 1..30 (this drops All-Star / Rising Stars / Celebrity
  games that hoopR files under type 2). Date = ET date of game_date_time (equals game_date in the files I tested).
- The NBA Cup finals 2023-24 (LAL-IND) and 2024-25 (MIL-OKC) are type 2 in hoopR, so they are kept (1231 games those seasons).
- hoopR gaps vs the real schedule: 2012-13 has 1224 games (real 1229), 2013-14, 2016-17 and 2017-18 have 1229 (real 1230).
  hoopR is the truth; gaps shorten rolling windows and could flip a back-to-back flag (not corrected).
- Turnovers differ by era in the hoopR columns (checked on the data): early files have total_turnovers = 2 x turnovers and
  team_turnovers = turnovers, so I use turnovers. Later files have total_turnovers = turnovers + team_turnovers (official TOV
  incl. team turnovers), so I use total_turnovers. Where total_turnovers is null (all of 2017-18, 2 rows of 2025-26) I use
  turnovers; 2017-18 therefore omits team turnovers (about 0.6 per team-game).
- Possessions = 0.96 x (FGA - OREB + TOV + 0.44 FTA) per team; game pace = mean of the two teams' possessions. Team O, D use
  the game pace as denominator, so ORtg x pace / 100 = PPG exactly (poss_check_b.csv, max abs diff 3e-14); the own-possession variant
  is in the same file (*_own columns, also exact).

### Lines
- OddsData dates: old "X" tables are "YYYY-YY-MMDD". I take the date from the same row position of the "_new" twin
  (verified: same length and same Home/Away in every position, all 16 seasons), else parse (month >= 8 -> start year).
  This matters for the Aug-Oct 2020 bubble.
- "X" and "X_new" tables are identical in OU/Spread/Points for 2007-08..2022-23 (0 differing rows); they differ only in date
  format. For 2023-24 the two tables hold different row sets (1255 vs 1195): they are combined per game. If both exist and
  differ by more than 1.0 the game is "line disputed" (0 cases). If both agree within 1.0 the X_new value is used (1 game
  differs, by <= 1). Games in one table only use that table.
- Match OddsData row -> hoopR game by (home franchise, away franchise) and |date diff| <= 1 day; exact date preferred; two
  equally close candidates = ambiguous (excluded); match to a playoff/play-in game = not_regular_season. Team names -> franchise
  ids by a hand map. Duplicate = two rows of one table matching the same game (all excluded).
- Closing lines: drop snapshot >= commence_time (93 rows); matched directly by game_id (file has no team names) with a +-1 day
  check of the ET commence date. If a closing row is dropped or has a null OU, the game falls back to OddsData if available
  (the prereg's "where present"). Closing is preferred whenever present.
- line_source: closing_odds_api | oddsdata_X+Xnew | oddsdata_X | oddsdata_Xnew. spread = abs(home_team_spread) from the closing
  row, else abs(OddsData Spread).
- Sanity: (1) range [170, 275] on the chosen line; (2) |line - (avg total of home team's earlier games this season + away's)/2|
  <= 30 when both teams have >= 5 earlier games this season (0 failures).
- OddsData Points vs hoopR final: listed in points_mismatch_b.csv (32 rows = 16 games, both tables); hoopR is used; not excluded.

### Features (only games dated strictly before the game's ET date; windows are NOT carried across seasons)
- f1 = line - mean total of all hoopR regular-season games before D this season. If fewer than 50 such games, the mean total
  of last season's games in its last 30 calendar days (last game date - 29 days .. last game date) replaces it.
- f2 = |spread| (one NaN: OddsData spread null, game 401071623).
- f3 = D - date of the season's first regular-season game. f4 = how many of the two teams played on D-1 (any hoopR regular game).
- f5 = (home last-10 pace + away last-10 pace) - 2 x league mean pace to date (reading: sum of the two deviations).
  f5_alt = sum - 1 x league (literal wording). Last-10 = the team's last 10 games before D in the same season; fewer than 10 -> NaN
  (2,812 rows, also f9 and f10). League mean = mean of game paces before D this season.
- f9 = same as f5 with each team's 3PA/FGA per game (mean of per-game rates, not ratio of sums); league = mean team-game rate; f9_alt likewise.
- f6 = average of (home last-5 mean error, away last-5 mean error), error = actual - line, using only the team's earlier games
  in the clean line table, same season; needs 5 such games each (else NaN, 1,425 rows).
- f10 = home last-10 mean error - away last-10 mean error (needs 10 each).
- f7 = 1 if the home team id is DEN (7) or UTAH (26).
- y = hoopR actual total (both teams, OT included) - line.

### f8 (frozen.json flat 7791)
Spec indices (A=2 w0.5, B=0, C=1 decay5, D=0 k0, E=1 shift on, F=0 none, G=1 split on, H=1 shrink on).
- DISCREPANCY: the brief says B = term-own pace, but frozen.json names B "own", and grid_lib's option table is
  B_OPTS = ("own","avg","mult","termown") with index 0 = own; flat 7791 recomputes with B index 0. I implemented "own"
  as f8 (team A points = (0.5 O_A + 0.5 D_B) x P_A) and provide term-own as f8_termown (0.5 O_A P_A + 0.5 D_B P_B). Opus decides.
- Team-game O = pts / game pace, D = opp pts / game pace, P = game pace. decay5 = weighted mean of the team's earlier games this
  season, weight 0.5^(age/5), age = games ago (most recent 0).
- Prior (S-1 regular season) = the team's decay5 rating over S-1 as of its last game (reading: same family as C), used only when the team has
  0 games yet (k = 0).
- Shift on: r_o = 1 + (Lo_cur/Lo_prev - 1) x n_lg/(n_lg+100), same for pace; n_lg = number of league GAMES before D (reading; could be team-games).
  Prior O, D x r_o and P x r_p. Lo = Lo_prev x r_o, Lp = Lp_prev x r_p.
- Split on: venue rating = decay5 over the team's earlier games at that venue this season (prior if none), blended with the overall
  rating at n_v/(n_v+10), n_v = count of venue games.
- Shrink on: proj' = Lt + b (proj - Lt), Lt = 2 Lo Lp. b = OLS slope of (actual - Lt) on (proj - Lt) over ALL S-1 regular-season
  games, projections walk-forward on S-1 with prior S-2 (k=20, shift on, pace avg, no split, A and C as above).
  For S = 2008-09, S-1 = 2007-08 has no prior (2006-07 not on disk): that fit uses no prior, ratings from games to date only,
  Lt = 2 x league mean to date, games where a team has no earlier game skipped (reading).
- Fitted b by season runs 0.98..1.63 (mean 1.38). Above 1 is unusual for a "shrink"; plausible because averaging O_A and D_B at
  w=0.5 halves the team spreads, but I could not verify against #56's own constants.
- F none: no rest effect. team_raw has shift 0.0, so no lean correction. f8 = proj' - line. No NaN in f8.

## Data findings worth knowing
- OddsData vs the odds-API close (agreement_b.csv): 2022-23..2024-25 agree closely (mean |diff| 0.4-0.7, within 1 point 89-95%);
  2019-20..2021-22 do NOT (exact match 5%, within 1 point 26-30%, mean |diff| 3.2-3.4, a few gross errors up to 241 points in OddsData);
  2025-26 mean |diff| 1.4. So the older OddsData totals (2008-09..2018-19 cannot be checked) may be opening or other-time lines, not the close.
  OddsData |spread| also differs from the closing spread in 2019-22.
- Gross OddsData errors caught by the range rule: lines 1.0, 19.5, 20.5, 47, 51, 160 (x5, Apr 2012), 1105, 1111, 1955.5 (13 games).
- 6 games in 2025-26 have no line (OddsData 2025-26 ends in Jan 2026, closing file lacks them).

## Per-season counts
Rows in = source rows (oddsdata rows counted per table, X and X_new both, so a game can count twice; closing rows incl. playoffs).
Excluded columns are rows (or games for line_out_of_range / no_line_available / line_disputed). Out = rows in features_b.parquet.

```
 season  hoopr_regular_games  odds_rows_in  closing_rows_in  x_odds_not_regular_season  clean_out  x_odds_ambiguous  x_line_out_of_range  x_odds_unmatched  x_closing_not_regular_season  x_closing_snapshot_not_before_commence  x_no_line_available
2008-09                 1230          2630                0                        170       1230                 0                    0                 0                             0                                       0                    0
2009-10                 1230          2624                0                        162       1230                 2                    0                 0                             0                                       0                    0
2010-11                 1230          2622                0                        162       1227                 0                    3                 0                             0                                       0                    0
2011-12                  990          2148                0                        168        985                 0                    5                 0                             0                                       0                    0
2012-13                 1224          2628                0                        170       1223                 0                    1                10                             0                                       0                    0
2013-14                 1229          2638                0                        178       1229                 0                    0                 2                             0                                       0                    0
2014-15                 1230          2622                0                        162       1229                 0                    1                 0                             0                                       0                    0
2015-16                 1230          2632                0                        172       1230                 0                    0                 0                             0                                       0                    0
2016-17                 1229          2618                0                        152       1228                 6                    1                 2                             0                                       0                    0
2017-18                 1229          2624                0                        164       1229                 0                    0                 2                             0                                       0                    0
2018-19                 1230          2624                0                        164       1230                 0                    0                 0                             0                                       0                    0
2019-20                 1059          2286              172                        168       1057                 0                    2                 0                            83                                       2                    0
2020-21                 1080          2342             1155                        170       1080                 0                    0                12                            78                                      21                    0
2021-22                 1230          2646             1322                        186       1230                 0                    0                 0                            93                                       4                    0
2022-23                 1230          2638             1319                        180       1230                 0                    0                 2                            90                                      30                    0
2023-24                 1231          2450             1319                         72       1231                 0                    0                 2                            87                                       9                    0
2024-25                 1231          1326             1321                         90       1231                 0                    0                 5                            88                                      14                    0
2025-26                 1231           554             1322                          0       1225                 0                    0                 0                            91                                      13                    6
```

Totals by reason (all rows incl. 2007-08):
```
reason
oddsdata_not_regular_season             2862
closing_not_regular_season               610
closing_snapshot_not_before_commence      93
oddsdata_unmatched                        37
line_out_of_range                         13
closing_unmatched_game_id                 12
oddsdata_ambiguous                         8
no_line_available                          6
```
