# Independent check of E2 injury features (research #62, double check 1)

Code: `check_features.py` (run: `.venv/Scripts/python.exe .../check/check_features.py`, about 20 s). Tests: `test_check.py` (10, pass).
Output: `injury_features_check.parquet` (200 rows, sample = `sample_ids.csv`, all 200 found in my own team-game list of 12,306).
Written from PREREGISTRATION.md plus the user's briefing only; FEATURES_E2.md read only for column names (suffix `_own` / `_opp`).
No outcomes (points of the target game, errors) are read or used. No shared code with main/.

## Readings (every choice) - REVISED to the 2026-10-23 amendment (PRIMARY columns)
1. **Report time** = the PDF /CreationDate, read from the raw bytes of data/raw/nba_injury/<name>.pdf by regex
   (`D:YYYYMMDDHHMMSS+hh'mm'`, converted to UTC; no PDF library). All 3533 parsed reports have one (0 skipped). Cached in
   creation_cache.json. File-name time is no longer used.
2. **Report used** = the latest report with CreationDate <= D (D = hoopR `game_date_time` - 60 min; equality counts) that LISTS THE
   GAME, looking back at most 72 h; reports not listing it are skipped. "Lists the game" = the report has a row or a
   "not yet submitted" entry with the game's ET date (MM/DD/YYYY) for either team of the game (team full name mapped to hoopR code);
   the matchup string itself is not compared. Within the report, X with no rows, or a nys entry, or no report found: all four
   features 0 (the amendment's literal "a team with no rows has features 0", so back_pts too; `back_pts_*_norowsok` holds
   the alternative where back_pts is still computed). Sample: own side 198 rows / 2 norows, opp side 197 / 3 norows; every game
   had a report; none nys.
   Status: Out and Doubtful = out; Questionable = q; Probable/Available ignored; a player listed twice keeps the most severe.
3. **History** = regular season (season_type 2) player_box rows, minutes > 0 and not did_not_play (0 minutes = not played), ET date
   strictly before the game's ET date. Window: >= 3 games this season: last min(10, n) games this season (all teams); else the last
   10 games played last season (2021-22 uses player_box_2021); else none. Rotation-quality = window mpg >= 15.0.
   `*_prevfull` columns = alternative (full last-season average).
4. **A player's team** = team of his most recent regular-season box row (played or DNP row) strictly before the ET date, this
   season else last season. X's players = those whose latest team is X (a just-traded player with no row yet for X is unmatched).
5. **Name matching** (report "Last,First", spaces removed, suffix glued, e.g. `BagleyIII,Marvin`, `DukeJr.,David`):
   lower-case alphanumerics, strip suffix (jr, sr, ii, iii, iv) from report last name / hoopR last token, key = first+last. Tier 1:
   unique exact key among X's players. Tier 2: unique (last name, first name prefix-compatible) among X's players. Else unmatched,
   contributes 0 (the earlier league-wide tier is dropped). Unmatched listings in the sample (own + opp sides): 2021-22 27,
   2022-23 34, 2023-24 21, 2024-25 43, 2025-26 58 (most are players with no box row in the current/previous season).
6. **back_pts** = current season only, 0 until X has played 10 games this season: the 10 most recent regular-season games of X before
   the ET date; candidates = X's players (reading 4) with a box row for X in those games, rotation-quality, not listed Out/Doubtful,
   playing in fewer than 5 of those 10 games; value = window ppg. Max in the sample 45.7.
7. out_min = sum window mpg of matched rotation-quality Out/Doubtful players / 240.
8. Sample: `sample_ids.csv` (200 rows, hoopR codes).

Differences vs the alternatives on the 200 rows (own / opp): fallback full-season average changes out_pts on 53 / 59 rows,
back_pts on 14 / 16, q_pts on 1 / 1; back_pts with no-rows computed (`_norowsok`) differs on 2 / 1.
Fallback used (>= 1 listed rotation player from last season): 56 own / 61 opp rows. Listed Out/Doubtful rotation players used
(own side): 427; Questionable: 91.

## Columns
game_id, team, opp, season ('2021-22'), date, tip, D, then for suffix own/opp: out_pts, q_pts, back_pts, out_min (primary), the
`_prevfull` copies, `back_pts_*_norowsok`, report_name, report_state (rows / norows / nys / no_report), nys, n_unmatched, n_listed_out,
n_listed_q, used_prev_fallback. Run summary: `run_stats.json`.

## Tests (test_check.py, 14 pass)
PDF CreationDate parser; a report not listing the game is skipped; 72 h look-back limit; no rows = all features 0; back_pts 0 before 10 games; player team = most recent box row. Report cutoff at D (inclusive, one minute earlier changes report, before any report = no info); nys = no information; rotation
threshold 15.0 vs 14.9; fallback to last season when < 3 games this season (and not when 3); returning-player rule (4 of 10 in,
5 of 10 out, listed-Out excluded, Questionable can be both q and back); same-day / later games and other-date report rows
ignored; name matching examples (BagleyIII, DukeJr., JonesGarcia, E.J., EliJohn, PorterJr.); unmatched counting.

## SECOND AMENDMENT (2026-10-23 02:51Z) - current PRIMARY columns
- Report set = union of injury_parsed_v3.json (3533) and nba_teammate_out/injury_parsed_tm.jsonl (833), deduplicated by PDF name:
  4366 reports; CreationDate from PDF bytes; none lacked one.
- Names: accents stripped (NFKD plus o-slash, d-stroke, l-stroke), lowercase, apostrophes/periods/hyphens removed, suffixes jr/sr/ii/iii/iv
  dropped (also when glued to the report last name), "Last,First" -> first+last; compared with spaces removed (the report PDFs have
  no spaces, e.g. JonesGarcia, EliJohn). EXACT match among players whose latest box-row team is X; 0 or 2+ matches = unmatched.
  No initial / prefix / surname matching, no league-wide tier.
- Window: current-season games with minutes > 0 before D, last min(10,n) if n >= 3 (never mixed); n < 3 -> last 10 games played last
  season; plain means; rotation >= 15 mpg.
- back_pts: candidates = every player with minutes > 0 for X at least once this season before D (NOT restricted to X's latest team
  and not restricted to the last-10 window rows); appeared in < 5 of X's last 10 team games; rotation-quality; not listed Out/Doubtful;
  0 until X has 10 games this season; 0 when X has no rows in the chosen report.
  Consequence: players traded away from X still count, so back_pts can be large (own max 117.2 on the sample).
- Sample results unchanged in counts: unmatched per season (own+opp) 27, 34, 21, 43, 58; listed Out/Doubtful used 427 own / 426 opp;
  Questionable 91 / 89; report state own rows 198 / norows 2, opp 197 / 3. 17 tests pass.

- Clarification applied: back_pts candidates must also have X as latest-box-row team before D. New own-side back_pts max printed in report.
