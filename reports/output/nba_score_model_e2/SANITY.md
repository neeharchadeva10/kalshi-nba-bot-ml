# SANITY - research #62: do the injury reports agree with the box scores?

Box scores are used here only AFTER the fact, to check the reports.  'Rotation-quality' = >= 15 mpg over the last 10 games played before the game; 'did not play' = box DNP or absent from the box.  Expected share for OUT-listed players: about 99%.

## OUT-listed rotation players who did not play

| season | status | n | n_played | share_did_not_play |
|---|---|---|---|---|
| 2021-22 | Out | 5867 | 0 | 1.0000 |
| 2022-23 | Out | 4140 | 1 | 0.9998 |
| 2023-24 | Out | 4416 | 1 | 0.9998 |
| 2024-25 | Out | 5353 | 0 | 1.0000 |
| 2025-26 | Out | 6465 | 0 | 1.0000 |


## Other statuses (context)

| season | status | n | n_played | share_did_not_play |
|---|---|---|---|---|
| 2021-22 | Doubtful | 111 | 3 | 0.9730 |
| 2021-22 | Questionable | 1028 | 597 | 0.4193 |
| 2022-23 | Doubtful | 145 | 3 | 0.9793 |
| 2022-23 | Questionable | 1114 | 602 | 0.4596 |
| 2023-24 | Doubtful | 174 | 2 | 0.9885 |
| 2023-24 | Questionable | 1285 | 777 | 0.3953 |
| 2024-25 | Doubtful | 171 | 5 | 0.9708 |
| 2024-25 | Questionable | 1356 | 712 | 0.4749 |
| 2025-26 | Doubtful | 124 | 0 | 1.0000 |
| 2025-26 | Questionable | 865 | 540 | 0.3757 |


## Mismatches (listed Out, rotation-quality, but played): 2 rows, all in sanity_mismatches.csv.  By season: 2022-23 1, 2023-24 1

First 40 (sorted by date):

| season | date | game_id | team | athlete_id | name | status | prior10_mpg |
|---|---|---|---|---|---|---|---|
| 2022-23 | 2023-02-25 | 401469062 | MEM | 4433137 | Ziaire Williams | Out | 16.70 |
| 2023-24 | 2024-04-02 | 401585721 | PHI | 3059318 | Joel Embiid | Out | 33.80 |


## Injury-report coverage per season (team-game rows)

| season | team_games | report_found | nys | box_found | listed | unmatched | out_rot | q_rot | back | unmatched_share |
|---|---|---|---|---|---|---|---|---|---|---|
| 2021-22 | 2460 | 1.0000 | 0.0000 | 1.0000 | 11211 | 706 | 5978 | 1028 | 3023 | 0.0630 |
| 2022-23 | 2460 | 1.0000 | 0.0000 | 1.0000 | 9999 | 968 | 4285 | 1114 | 1712 | 0.0968 |
| 2023-24 | 2462 | 0.9992 | 0.0000 | 1.0000 | 11242 | 955 | 4590 | 1285 | 1612 | 0.0849 |
| 2024-25 | 2462 | 1.0000 | 0.0000 | 1.0000 | 12282 | 1322 | 5524 | 1356 | 2025 | 0.1076 |
| 2025-26 | 2462 | 1.0000 | 0.0000 | 1.0000 | 11813 | 1278 | 6589 | 865 | 2034 | 0.1082 |


report_found = a report published at or before D = tip - 60 min covered the game (72 h look-back); nys = the team was 'not yet submitted' (no information); unmatched = listed Out/Doubtful/Questionable names with no unique match among the team's players (they contribute 0).

## Unmatched-name diagnostic (not used by any feature)

| season | unmatched | league_unique_name | league_unique_rotation |
|---|---|---|---|
| 2021-22 | 706 | 703 | 230 |
| 2022-23 | 968 | 951 | 363 |
| 2023-24 | 955 | 930 | 273 |
| 2024-25 | 1322 | 1275 | 373 |
| 2025-26 | 1278 | 1072 | 339 |


league_unique_name = the unmatched name has exactly one league-wide normalised-name match in the box files; league_unique_rotation = that player was rotation-quality before the game (so a feature value was missed).  Mostly newly-acquired or long-term-injured players with no box row for the team.
