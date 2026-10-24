# FEATURES_E3 - research #64: exact column names and definitions

File: `e3_features.parquet`, one row per (game_id, team), regular season (hoopR season_type 2) 2021-22 .. 2025-26, 12,306 rows.
Written by `main/features.py` (library `main/e3_lib.py`, which imports #62's `e2_lib.py` read-only). No outcome (points) is read.
Inputs: E1 team-game list (E1's cleaning, via e2_lib.load_games), player_box_2021..2026, official injury reports (report-set union +
PDF CreationDate, exactly #62), hoopR player_core_2021..2026 (date of birth), data/derived/player_moves.csv.

## Keys
game_id, season, date (ET), team / opp (hoopR codes, the key used by E1's parquet and sample_ids.csv), team_k / opp_k (Kalshi codes),
tip (UTC s), D60 = tip - 3600, D20 = tip - 1200, box_found.

## Column pattern
`{base}_{own|opp}_{c60|c20}`: 17 bases x 2 sides x 2 cutoffs = 68 model columns. `_own` = the row's team, `_opp` = the other team.
c60 = latest report with CreationDate <= tip - 60 min; c20 = <= tip - 20 min (inclusive; a report one second later is ignored; look-back
72 h; a report counts if it has rows or "not yet submitted" notes for the game). NEW-family columns do not depend on the cutoff (the c60
and c20 columns are equal).

## Shared rules (from #62, unchanged except the rotation threshold)
- History = regular-season box rows with minutes > 0 and not DNP, ET date STRICTLY before the game date.
- Player window: >= 3 games played this season -> means over his last <= 10 games played this season; else his last <= 10 games played last
  season; else none. All stat means use the same window rows.
- **Rotation-quality for E3 = window mpg >= 10** (E2 used 15). Counted players are listed OUT/DOUBTFUL (families 1-10) or QUESTIONABLE (Q).
- Players = athletes whose most recent regular-season box row before the game is for the team and is from this or last season.
- Name match = exact normalised "first last" among those players (zero or 2+ matches = unmatched, contributes 0). A player listed twice keeps
  the most severe status. A team with no rows in the chosen report, "not yet submitted", or no report: all report-based families are 0.

## Families (Out/Doubtful rotation-quality players O; Questionable rotation-quality players Q)
| # | Family | columns (base names) | definition |
|---|---|---|---|
| 1 | PTS | pts | sum over O of window points per game |
| 2 | MIN | min | sum over O of window mpg / 240 |
| 3 | REB | reb | sum over O of window rebounds per game |
| 4 | AST | ast | sum over O of window assists per game (box column `assists`) |
| 5 | 3PM | tpm | sum over O of window three-pointers made per game |
| 6 | DEF | dfn | sum over O of window (steals + blocks) per game |
| 7 | USG | usg | sum over O of window (FGA + 0.44 FTA + turnovers) per game |
| 8 | PM | pm | sum over O of window plus-minus per game (string "+7"/"-14" parsed to a number) |
| 9 | TIER | tier_star, tier_starter, tier_bench | sum over O of window PPG split by window mpg: star >= 28; starter 20 <= mpg < 28; bench < 20 |
| 10 | AGE | age_vet, age_young | sum over O of window mpg for age >= 32 (vet) and for age <= 23 (young) |
| 11 | Q | q_pts, q_min | sum over Q of window PPG, and of window mpg / 240 |
| 12 | NEW | new_share, gone_share | see below |

Age = completed years on the game's ET date from the hoopR player_core date of birth (all six files, later overrides earlier, 0 conflicts).
Unknown (no DOB) or outside 18-45 (sanity) = age unknown: the player is left out of AGE only (counted in `n_age_unknown_*`).

NEW family (moves visible only if `first_date_new` is STRICTLY BEFORE the game's ET date):
- `new_share` = (minutes played for the team by athletes who moved TO the team with `season_new` = this season, in the team's last <= 10
  regular-season games of this season before the game) / (all minutes played for the team in those games); 0 if the team has no earlier
  game this season. (Fewer than 10 earlier games: the games available are used.)
- `gone_share` = (last-season regular-season minutes for the team of athletes who have a visible move FROM the team and whose latest
  visible move did not bring them back to it) / (all the team's last-season regular-season minutes); 0 if no last-season minutes.

## Coverage columns (not model inputs), per cutoff suffix _c60/_c20
report_found, report_name, report_created, nys, report_skipped_newer, n_listed (Out/Doubtful/Questionable names listed),
n_unmatched, unmatched_names ('Name|Status;...'), n_out_rot, n_q_rot, n_age_known, n_age_unknown (the last two over the Out/Doubtful
rotation-quality players).

## sample_ids.csv
200 (game_id, team, team_k, season, date) rows: all regular-season team-games sorted by (season, date, game_id, team),
`numpy.random.default_rng(20261025).choice(len, 200, replace=False)`, sorted. `team` = hoopR code.

## Versions
Ridge on the residual target r = actual team points - E1 team points. Version = (cutoff, non-empty subset of the 12 families); 4,095 x 2
= 8,190. Column order inside a version: families in table order, within a family base by base, own then opp.
