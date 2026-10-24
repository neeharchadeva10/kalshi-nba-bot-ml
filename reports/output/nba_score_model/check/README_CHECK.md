# Independent check, research #59 (written from PREREGISTRATION.md; names aligned to FEATURES.md afterwards)

Files: check_features.py (code), test_check.py (8 tests, pass), features_check.parquet (300 rows, FEATURES.md names).
Run: `.venv/Scripts/python.exe reports/output/nba_score_model/check/check_features.py`.

## Readings chosen
- Population: team_box_2009..2026 (seasons 2008-09..2025-26), season_type 2 only. Histories use 2008..2026 files.
  43,146 team-games. Sort (season, date, game_id, team abbreviation); rng default_rng(20261023).choice(n,300,False); sorted.
- All-star/exhibition drop: a game is dropped if either side is not a real club. Real club = abbreviation with >= 10
  season_type-2 rows in that season. Removes EAST, WEST, USA, WORLD, LEB, GIA, DUR, STE, CAN, KEN, CHK, SHQ, STARS, STRIPES.
  NBA Cup final kept (it is season_type 2 in hoopR).
- Date = hoopR game_date (equals the ET date of game_date_time in every row checked). Windows use date < D.
- Turnovers: file years 2008..2012 use `turnovers` (total_turnovers is exactly 2x there); otherwise total_turnovers if
  finite and >= turnovers, else turnovers (all of file 2018 has NaN total_turnovers, so turnovers).
- Team poss = 0.96*(FGA-OREB+TOV+0.44*FTA); game poss = mean of the two teams.
- ortg = 100*sum pts / sum game poss over the team's games in the window; drtg uses points allowed; pace = mean game poss.
- last 10 / last 5 = most recent min(n,10)/min(n,5) games of S before D; 0 games in the window -> S-1 full-season value
  of the same quantity (per column).
- Previous season = file season-1 for the same team_id (so NJ->BKN and SEA->OKC continue; abbreviations can differ).
- League ortg = 100*sum pts over all team-game rows before D / sum game poss of those rows; league pace = mean game poss
  per game. Fewer than 50 distinct league games played in S before D -> S-1 full-season values of the same.
- rest = min((D - last game date in S) - 1, 4); first game of S = 4; b2b = rest == 0. Previous game must be in S
  (no carry-over across seasons).
- gp = games of the team in S before D. dos = days from the league's first regular-season date in S to D.
- Target = team_score.

## Ambiguities in the prereg
1. "last 10" when fewer than 10 games exist (partial window vs fallback): I use the partial window.
2. Whether previous-season "full" values and rest cross season boundaries (I used S only for rest, S-1 for prev).
3. "50 games" in the league rule: games (not team-games), strictly before D.
4. Franchise continuity (NJ->BKN, SEA->OKC) is not stated; I used team_id.
5. dos is from the league's first date, not the team's.
6. Which games are "exhibition" is not defined; my rule is the 10-row rule above.
7. hoopR data holes: 2012-13 file has 1224 clean games (expected ~1229), 2013-14 1229 ok; not repaired.
