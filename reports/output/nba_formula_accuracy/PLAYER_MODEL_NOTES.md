# Player model (#56 section B): builder notes

Files: player_model.py, test_player_model.py (19 tests, 2.3 s). Imports (read only): tm_lib (#48 name normalisers), grid_lib.team_games_g (#42 possessions, pace, ET dates).

## Readings chosen (ambiguities for Opus / the user)
1. **Shrinkage direction.** est = (1-w) x last-20 rate + w x current-season rate, w = n/(n+10). n = 0 gives the last-20 rate; the "last season's rate" target is only used as the target when n = 0, so it has weight 0. The opposite reading (weight n/(n+10) on the last-20 rate) was rejected: it makes the recent window matter more as the season grows.
2. **Rates** are ratios of sums (sum points / sum minutes), not means of per-game ratios.
3. **Rotation window = the team's last 10 games before the date, spanning seasons.** Absent players score 0 minutes, so players new to the team (offseason trades) are under-counted at the start of a season. Team games with no player_box rows on disk (2026-27) are dropped from the window.
4. **Minutes** = mean of the player's last 10 games played (any team, spanning seasons, OT included). A Questionable player's weight multiplies his minutes before the scale to 240, so q = 0.5 shifts his share to teammates.
5. **< 5 games played in total** (all history before the date) -> team per-minute rate = points / minutes of players over the team's last 20 games. If a team has no rotation (nothing in the window), the projection is NaN.
6. **Opponent factor** = opponent DRtg over its last 15 games (spanning seasons; sum opp_pts / sum opp_poss x 100, the #37/#38 definition) / league points per 100 possessions in the current season to date. If the season has < 60 team-game rows, last season's whole-year value is used (my threshold; the prereg gives none).
7. **Pace factor** per team = mean(own last-15 pace, opponent last-15 pace) / own last-15 pace. Pace per game = (poss + opp_poss)/2. Total = both teams' factors applied separately.
8. **History** = season types 2 and 3 (regular + playoffs); preseason and All-Star excluded.
9. **Injury input:** among reports with PDF CreationDate <= T - 3600 s (inclusive), look back 72 h for the latest one that has rows or a "not yet submitted" note for the game (date + AWAY@HOME). Only the 3,533 + 833 parsed files carry content; an unparsed newer file is skipped (counted as report_skipped_newer, 0 in every season). A team marked "not yet submitted" gets no removals. Probable/Available/unlisted = fully in.
10. **Name matching** only against that team's rotation candidates: exact normalised name, else unique last name + first initial. Listed Out/Doubtful/Questionable names that match no rotation player are counted either as `known_not_rotation` (player exists in the box scores, not in the rotation, so no effect) or `unmatched` (no such name in the box scores at all: injured rookies / two-way players who never appeared).
11. Game rows whose teams are not NBA teams (All-Star, Rising Stars) return NaN. Games before 2026-27 in team_box_2027 have no player_box on disk, so 2026-27 projections use only last-season player history (no injury reports on disk either): descriptive only.

## Coverage (regular season rows from team_box; computed with no outcomes)
| season | games projected | with a covering report at/before D | unmatched listed Out names | mean projected total (v0 / v7, level only) |
|---|---|---|---|---|
| 2022-23 | 1230 | 100.0% | 181 | 227.0 / 230.4 |
| 2023-24 | 1231 | 99.9% (1230) | 25 | 230.8 / 233.1 |
| 2024-25 | 1231 | 100.0% | 222 | 225.9 / 228.3 |
| 2025-26 | 1231 | 100.0% | 207 | 229.9 / 230.7 |
| 2026-27 (14 games) | 14 | 0% (no reports on disk) | 0 | 232.9 / 235.5 |

"With a report" = a report published at or before D listing the game. 2023-24 has 1 game without one.
