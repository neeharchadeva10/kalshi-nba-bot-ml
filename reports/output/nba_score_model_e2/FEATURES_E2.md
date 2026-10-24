# FEATURES_E2 - research #62: exact column names and definitions

File: `injury_features.parquet`, one row per (game_id, team), regular season (hoopR season_type 2) 2021-22 .. 2025-26, 12,306 rows
(2026-27 is NOT built: there is no player_box_2027 on disk).  Written by `main/features.py` (library `main/e2_lib.py`).
No points / outcome column is read.  Reads: E1 team-game list (E1's cleaning), player_box_2021..2026, probe cache of report creation
times, injury_parsed_v3.json (+ #48's local parse injury_parsed_tm.jsonl for 2025-11..2026-06).

## Keys and identifiers
| column | meaning |
|---|---|
| game_id | hoopR / ESPN game id (int) |
| season | '2021-22' ... '2025-26' |
| date | ET calendar date of the game (the E1 convention) |
| team, opp | hoopR codes (GS, NY, SA, UTAH, WSH, NO ...) - **the key used by E1's parquet and by sample_ids.csv** |
| team_k, opp_k | Kalshi codes (GSW, NYK, SAS, UTA, WAS, NOP ...) = the codes used by the injury reports |
| tip, D | tip = hoopR scheduled start (UTC epoch s); D = tip - 3600 |

## The 8 model features (own team X, opponent Y)
`out_pts_own out_pts_opp q_pts_own q_pts_opp back_pts_own back_pts_opp out_min_own out_min_opp`

Definitions (X = the row's team; the _opp column is the same quantity for the other team of the game):
1. **History** = regular-season (season_type 2) box rows with minutes > 0 and not did_not_play ("games played"), ET date STRICTLY
   BEFORE the game's ET date, across all teams of that player.  This season = the file year of the game's season.
2. **Player window** (current season: the last <= 10 games played, 3-9 used as is, seasons never mixed).  If he has played >= 3 games this season: mpg and ppg = means of minutes and points over his last 10 games
   played (fewer if he has fewer than 10) this season.  Else, if he played last season: the same means over his last 10 games played
   LAST season (reading of "last season's mpg/PPG": the same 10-game window, drawn from last season).  Else no value (not
   rotation-quality).  The 2020-21 file (player_box_2021) is used only for this last-season fallback of 2021-22.
3. **Rotation-quality** = window mpg >= 15.0.
4. **X's players** = athletes whose most recent regular-season box row (played or not) before the game's date is for X, and that
   row is from this season or last season (amendment 2026-10-23 02:45Z).
5. **Report** = the latest report with creation time <= D (inclusive; one second later is ignored) that has rows or
   "not yet submitted" notes for this game (matchup AWAY@HOME in Kalshi codes, report date = ET date), looking back at most 72 h.
   Reports whose parsed content is missing are skipped.  Creation time = PDF /CreationDate (probe cache, positive values only).
6. **Listing.**  A team with no rows in the chosen report has no listed injuries: all four features 0 (incl. back_pts).  Per team, each name keeps its most severe status (Out > Doubtful > Questionable; Probable / Available are ignored).
   If the team is "not yet submitted" in that report, or no report covers the game: no information -> all four features 0.
7. **Name match** (second readings amendment 2026-10-23 02:51Z): normalise lower-case, strip accents / apostrophes / periods / hyphens,
   drop suffixes jr sr ii iii iv; report 'Last,First' -> 'first last'; EXACT match to the normalised display name among X's players
   (spaces removed before comparing, so 'Van Vleet' = 'VanVleet'); zero or 2+ matches = unmatched (counted, contributes 0); never a
   surname-only or initial match.
8. out_pts = sum of window ppg of matched rotation-quality players listed Out or Doubtful.
   q_pts = the same for Questionable.  out_min = sum of window mpg of the same Out/Doubtful players / 240.
9. back_pts = sum of window ppg of X's players who played (minutes > 0) for X at least once this season before the game, are
   rotation-quality, are NOT listed Out/Doubtful, and played in fewer than 5 of X's last 10 regular-season games of this season.  If X
   has played fewer than 10 games this season, or the team has no rows in the chosen report: back_pts = 0.

## Coverage / auxiliary columns (not model inputs)
report_found (bool), report_name, report_created (UTC epoch s), nys (team "not yet submitted"), report_skipped_newer (newer reports
at/before D that did not cover the game), box_found (the game has player-box rows for the team), n_listed_own (Out/Doubtful/
Questionable names listed), n_unmatched_own + unmatched_names_own ('Name|Status;...'), n_out_rot_own, n_q_rot_own, n_back_own (counts
of players behind out_pts / q_pts / back_pts), key_aids_own (athlete ids of X's players with window mpg >= 28, pre-game; used only
by the key-players-out split in run_test, which checks them against the box AFTER the game), listed_rot_own ('aid:Status:mpg;...' of
matched rotation-quality listed players; used by sanity.py).

## sample_ids.csv
200 (game_id, team[, team_k, season, date]) rows: all regular-season team-games 2021-22..2025-26 sorted by (season, date, game_id,
team) with `numpy.random.default_rng(20261024).choice(len, 200, replace=False)`, sorted.  `team` = hoopR code.

## Variants (ridge on the residual target r = actual team points - E1 team points)
A: out_pts (own, opp) = 2 features.  B: + q_pts = 4.  C: + back_pts + out_min = 8.
