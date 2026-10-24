# #66 independent checker (check/)

Code: check_lib.py (features, settle), run_check.py (outputs), test_check.py (pytest). No code shared with main/.
Outcomes computed for D (2006-2014) only. Run: `.venv\Scripts\python.exe run_check.py` (~4.5 min).

## Readings
1. Missing gametime (1999 only) -> kickoff 00:00 ET of gameday. Kickoff converted to UTC; 365 days = exactly 365*24h.
2. L window uses every game in games.csv (game_type in REG/WC/DIV/CON/SB) with both scores present, whether or not total_line exists; value = total points / (2 * games); needs >= 50 games.
3. Lprev (A-PTS prior) = mean points per team-game of season S-1 over games with scores. If a team has no S-1 games, prior = Lprev.
4. "Previous games in the season" use the games.csv `season` field (playoffs included, same season). A team's S-1 history = all its games with season == S-1.
5. EPA/drive history ("j") counts only that team's games with valid pbp features (pbp game present, not one of the 39 bad ids); n = number of such games this season.
6. EPA league mean for the prior = mean over S-1 team-games of the per-team-game stat (unweighted by plays) for off_epa, def_epa, plays. EW is over per-game means (not play-weighted).
7. A team-game is EPA-valid if off_epa, def_epa and plays are all non-null (both sides of ball present).
8. Drive (Amendment 3): group (game_id, fixed_drive) with posteam and fixed_drive non-null, ordered by play_id. Drive plays = those whose posteam equals the first play's posteam. Points = max(0, last non-null posteam_score_post - first non-null posteam_score among those plays); if either missing, 0 and the drive still counts. Defensive drives faced = the other team's drives (opponent from games.csv).
9. Drive prior league values: PPD = sum(points)/sum(drives) over S-1 team-games; DPG = mean offensive drives per S-1 team-game. Shrink 1/3 as for points. Blend with k = 4, h = 8.
10. A-DRIVE team points = mean(DPG_h, DPG_a) * (PPD_off_team + PPD_def_opp)/2.
11. A-EPA OLS: per h, on D games (total_line + both scores) where both teams' features and L are non-NaN; rows = home and away team-games (6 coefficients incl. intercept), numpy lstsq. Target = team points. Prediction = sum of the two team fits. Games with NaN features are not bet.
12. Bets: OVER if pred - close >= G, UNDER if close - pred >= G; result by total = home+away vs close; push on equality (P&L 0, counted in pushes, in the n_bets denominator of mean P&L). Win = +10/11, loss = -1.
13. Team codes in the sample CSVs are mapped (SD->LAC etc.) before lookup; output keeps the sample's code.
14. EPA-feature columns in check_epa_teamgames.csv are the BLENDED (post-prior) values going into the game; DPG/PPD_* the A-DRIVE h=8,k=4 blended values.
15. pbp rows are used only for games in games.csv with the allowed game types; pbp game_id is matched to games.csv game_id directly.

## M-1 additions (check_ares.py, check_b2.py) - further Readings
16. A-RES neutral seconds per play: "consecutive" = consecutive among EPA-filter plays (play_id order) of the same posteam and fixed_drive; both snaps need wp in [0.2,0.8] (inclusive) and half_seconds_remaining > 120; wp NaN -> not neutral; diff >= 0 kept. Team-game value = mean of kept pairs, NaN -> skipped in EW (n counts valid games only). PROE = mean pass_oe of EPA-filter plays, null -> 0. EW h8 k4; prior shrinks toward the S-1 unweighted mean of the team-game values.
17. gap_epa uses the h8 A-EPA OLS fitted on D team-games (as in version tests); gap_drive = A-DRIVE (Amendment 3) - close; gap_pts = A-PTS h8 k4 - close.
18. QB-out: latest row (by date_modified) of that player among rows with same (season, week, team) as the game and ET date of date_modified strictly before gameday; Out/Doubtful -> 1; NaN status -> 0. Starter = home/away_qb_id of the team's previous scored game (any season). Rows without date_modified never used.
19. Coach change: in the team's schedule order (all games.csv rows), coach differs from the previous game in the same season; flag if index distance from the latest such change in the season <= 4. First-year = no earlier-SEASON game with same team and coach.
20. Referee: per-game count of pbp rows with penalty == 1 over pbp-valid games; ref mean over his games in S-2..S-1 if >= 10 games, else mean of all games of those seasons.
21. Ridge: z-score with statistics from all D complete cases (not recomputed per fold); in folds, intercept = train mean after centring train features; unpenalised intercept; ties in LOSO MSE -> smaller lambda; D complete cases only.
22. Primetime uses gametime string >= "19:00".
23. B2 join: open_air = stadiums.csv roof_category == open-air AND games.csv roof in {outdoors, open}; forecast_ok = fc_wind_mph present AND fc_variant == previous_day1 AND fc_issue_guarantee_h >= 24 (identical to the parquet's forecast_ok on the 100 games). Games absent from the parquet get NaN forecast and forecast_ok False. b2: 10 <= fc < 20; b3: fc >= 20.
