# Independent check of E3 features (research #64, double check 1)

Code: `e3_check.py` (about 107 s; imports the #62 checker `nba_score_model_e2/check/check_features.py` for box/report loading,
CreationDate, report selection and exact name matching, with its OUT redirected to this folder). Tests: `test_check.py` (11 pass).
Output: `e3_features_check.parquet`, 200 rows = `../sample_ids.csv` (all found), keys game_id, season, date, team, opp, team_k, opp_k,
tip (UTC s), D60, D20; 17 bases x own/opp x c60/c20 = 68 model columns `{base}_{own|opp}_{c60|c20}`; coverage columns
`report_found / n_unmatched / n_out_rot / n_q_rot / n_age_known / n_age_unknown` with the same `_{side}_{cutoff}` suffix.
Read only: PREREGISTRATION (#64, #62 + amendments), FEATURES_E3.md, sample_ids.csv. Not read: main/, e3_features.parquet. No outcomes used.

## Readings
1. Reports, CreationDate, report choice (latest <= D inclusive that lists the game, 72 h look-back), team without rows / nys / no
   report = all report families 0, status severity, exact name matching, current team = latest box row (this or last season),
   All-Star rows dropped: as in #62 checker. Cutoffs D60 = tip-60 min, D20 = tip-20 min (tip = hoopR game_date_time).
2. Window: >=3 games played this season -> last <=10 this season; else last <=10 last season; else none. All stats from the same rows.
   Rotation = window mpg >= 10.0 (>=10 inclusive). Out/Doubtful -> families 1-10; Questionable -> Q only (q_pts, q_min=mpg/240).
3. Per game stats are plain means: USG = FGA + 0.44 FTA + TOV; DEF = STL + BLK; PM = plus_minus string parsed to float ("+7"->7).
   No NaN plus_minus among played rows was met in the sample (a NaN would propagate as NaN; none seen).
4. TIER by window mpg: star >= 28, starter 20 <= mpg < 28, bench < 20 (PPG summed). min = sum mpg/240.
5. AGE: DOB = first 10 chars of player_core date_of_birth ("1984-12-30T08:00Z" -> 1984-12-30), all six files, later overrides earlier
   (0 conflicts among 1,117 athletes). Age = completed years on the game ET date. Unknown or outside 18-45 = unknown, left out of AGE
   only. vet = age >= 32 adds window mpg; young = age <= 23 adds window mpg (not /240). Sample: 0 unknown ages among Out/Doubtful
   rotation players.
6. NEW (cutoff independent): moves are visible if first_date_new < game ET date. Moves file uses Kalshi codes, box uses hoopR codes;
   map GS->GSW, NO->NOP, NY->NYK, SA->SAS, UTAH->UTA, WSH->WAS.
   - new_share = minutes of athletes with a visible move TO the team with season_new = this season, in the team's last <=10 earlier
     games this season / all minutes in those games (0 if none). The mover's minutes are counted in the whole window even in games
     before the move date (the move date is the first game; only visibility is date-limited).
   - gone_share = last-season team minutes of athletes with any visible move FROM the team whose latest visible move (by
     first_date_new, stable file order) does not go to the team / all last-season team minutes.
7. Sample results (200 rows, 400 team-sides per cutoff): reports found for every game; unmatched listings 165 (c60) / 164 (c20);
   Out/Doubtful rotation players used 1012 (c60) / 1044 (c20); Questionable 177 / 99. c60 != c20 for the PTS family on 18 own / 9 opp
   rows; Q on 35 / 19; NEW columns identical across cutoffs. Largest sums: PTS 137 (9-player outages in PHI 2025-03-21 and CLE 2021-12-26
   COVID period, plausible).

## Ambiguities (decide / cross-check with main)
- gone_share "latest visible move" tie on equal dates (file order used); "any visible move from the team" is not limited to season_old.
- new_share denominator counts all minutes of rows with played=True (minutes>0) for the team; the denominator is also
  approx 240 x games.
- Q columns: the Q player's mpg uses the same >=10 threshold (Q-family rotation filter assumed).
- hoopR-vs-Kalshi team code map above was written by me (moves.csv uses Kalshi codes).
- A Questionable player is not subtracted anywhere else; a player listed Out and Questionable keeps Out.
