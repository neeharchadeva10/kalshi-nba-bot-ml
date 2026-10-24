# #66 NFL totals: main implementation

Files: nfl_lib.py (loaders, EW/prior/blend, L, pbp aggregation, OLS/ridge, fees/P&L, bootstrap, luck tests, audit hashes),
part_a.py (56 versions, bets, evaluation, verdict), part_b.py (B1-B5, hindsight split, verdict), part_c.py (Kalshi ladder, T-60 picker,
implied median, fair model, 20 Part C versions, verdict), build_features.py (writes ../main_out/), gates.py, run_select.py
(Part A selection on D only; NOT run), run_test.py (guarded; NOT run), make_audit_hashes.py, test_nfl.py (46 tests).

Run order: build_features.py, gates.py, pytest, (Opus) agreement check, run_select.py, audit -> AUDIT_OK + test_started.json, run_test.py.
make_audit_hashes.py fails until the Kalshi parquet files and results_select.json exist (it lists what is missing).

Guards: run_test.py refuses unless ../AUDIT_OK (SHA-256 of every file in nfl_lib.AUDIT_FILES) AND ../test_started.json exist, the
hashes match, results_select.json exists and results_test.json does not. Outcomes enter only through nfl_lib.attach_outcomes, which
refuses seasons outside the caller's allowed set (run_select passes D only). game_table.parquet stores no outcome.

## Readings needing Opus sign-off (the most literal reading was implemented; none was chosen silently)

Data / history
1. 1999 games have no gametime (259 rows, all in W): kickoff falls back to 13:00 ET. Only affects warm-up ordering.
2. n in n/(n+k) counts this season's previous games WITH the statistic valid. For pbp stats the 39 excluded games therefore do not
   count toward n and are skipped in the EW. For points, n = all previous scored games.
3. "That season's league mean of the same statistic" (A-EPA/A-DRIVE/A-RES priors) is read as season S-1 (the season whose end values are
   pulled), the ratio sum(num)/sum(den) over all valid team-games of S-1. (Season S would be look-ahead.)
4. pbp rows with posteam == '' (seen in 1999) are treated as null.

A-DRIVE
5. A drive is (game_id, fixed_drive) but ~2% of those groups contain a second posteam (the extra point after a defensive touchdown is
   logged with the scoring defence as posteam). The drive's team = posteam of its FIRST play, and only that team's plays count, so the
   points are that team's posteam_score_post(last own play) - posteam_score(first play), floored at 0. A defensive TD is therefore 0 points
   for the drive.
6. PPD and DPG: EW of ratio = sum(w*points)/sum(w*drives); DPG = sum(w*drives)/sum(w); the prior pulls toward the S-1 league ratio.
   Prediction-vs-close on all games has mean about -2.2 (A-DRIVE under-predicts because defensive/special-teams scores are not drive
   points). This is the registered model; the bias will make A-DRIVE bet mostly UNDER.

A-RES feature details (not fixed by Amendment 0)
7. Neutral seconds per play: EPA-filter plays (pass/run, no kneel/spike, epa and posteam non-null), consecutive plays of the same offence
   in the same fixed_drive, seconds = game_seconds_remaining(earlier) - game_seconds_remaining(later), kept if BOTH snaps are neutral
   (wp in [0.2, 0.8] and half_seconds_remaining > 120) and the difference is >= 0; team-game value = mean of the kept pairs (NaN if none:
   skipped in the EW). EW with h = 8, k = 4. Early seasons (1999-2000) have bad clock data; D history is from 2001 on.
8. PROE: mean of pass_oe over the EPA-filter offensive plays with null -> 0 (pass_oe exists from 2006; it is absent only in 1999-2005, which are all 0).
   EW h = 8, k = 4.
9. QB-out flag: starting QB id (games.csv home/away_qb_id) of the team's previous PLAYED game (any season); flag 1 if that gsis_id has
   report_status Out or Doubtful in the LATEST row, among rows with the same (season, week, team) whose date_modified (UTC, converted to
   an ET calendar date) is STRICTLY BEFORE the game's ET gameday. Latest = largest date_modified (a later row with status Probable or
   blank overrides an earlier Out). Columns used: season, week, team (canonical), gsis_id, report_status, date_modified. Files with no
   usable date_modified: all of 2025 and 2026, and 2009 (17 dated rows of 4,821): those rows are never used, so the flag is 0 in 2009 and
   in 2025-26 (it cannot be shown to precede the game). This is a structural inconsistency between D (2010-14 flagged ~3%) and the
   2025-26 holdout (always 0); the ridge fit sees only D.
10. HC change = the game is the coach-change game or within 4 games after it (games since change <= 4), change = head coach differs
    from the team's previous game in the same season (schedule order, so also defined for unplayed games). First-year HC = no game with
    this team and this coach in an earlier SEASON (first year with the team, not first year as head coach anywhere). Both are per team
    (home and away columns).
11. Rest days = games.csv home_rest / away_rest; short week = min(rest) <= 5; primetime = gametime >= 19:00 ET on any weekday (including
    Thursday); divisional = div_game; neutral = location == Neutral; week = week number; playoff = game_type != REG.
12. Referee penalties: mean number of pbp rows with penalty == 1 per game over that referee's games (games.csv `referee`) in seasons S-2
    and S-1 (pbp-valid games); if he has < 10 such games (or no referee) the mean of all pbp-valid games of those two seasons.
13. Ridge: features z-scored with D-row statistics, unpenalised intercept, lambda grid (0.1, 1, 10, 100, 300, 1000, 3000, 10000, 30000)
    chosen by pooled leave-one-season-out MSE inside D (9 folds); complete-case rows only; chosen lambda and coefficients in
    ../main_out/ares_ridge.json. A-RES uses the sportsbook close as a feature on every window, including HK.

Kalshi / ladder
14. Main-strike picker follows the task text and #42's code: the strike with YES mid nearest 0.50 AMONG two-sided strikes with spread <=
    10c (ties -> lower strike). The pre-registration sentence ("a ladder whose main strike has spread > 10c is skipped") would skip a
    ladder whose nearest-to-0.50 strike is wide instead of moving to the next one. Gate: identical to #42's grid_bets.main_strike_table
    on synthetic ladders, including the wide-spread and tie cases.
15. A quote needs yes_bid_close and yes_ask_close present, bid >= 1c, ask <= 99c, ask >= bid (0.00/0.99 are the empty-book placeholders
    visible in the smoke candles). The quote is the LAST candle in the window (1-minute: [decision-60min, decision]; hourly: [decision-3h,
    decision], end_ts <= decision); if that last candle is not two-sided the strike has no quote (no fall back to an older candle).
16. Implied median M: the first consecutive pair of two-sided strikes (any spread) with mid_i >= 0.5 >= mid_{i+1}; no crossing -> game
    skipped.
17. Part A on HK: the gap is taken against M (the Kalshi line) and the model's side is bought at the main strike (OVER = YES at ask,
    UNDER = NO at 1 - yes bid), ask + 1c + fee. A-RES uses only the sign of its predicted residual. Settlement from nflverse finals
    (total > strike); the Kalshi bracket gate (gates.py) checks the Kalshi results against the same finals after the download.
18. Fair model: f_b is the plain Gaussian KDE (bandwidth 2) of total - close in the band; "smoothed frequency" in w_t is the same
    continuous KDE of the totals evaluated at integer t (w_t = empirical frequency / that, clipped [0.4, 2.0], w = 1 where the smoothed
    value is ~0). Band thresholds applied to M (and to the close when fitting): < 41, 41-44.99, 45-48.99, >= 49. fair_YES(s) = sum over
    t >= floor(s)+1.
19. Part C: one side per strike = the side with the larger edge; C-LS requires a two-sided quote (needs a yes bid for the NO price).
    Luck copies draw the same number of bets per game from that game's two-sided strikes at that tau, with random sides. Bootstrap is by
    game (several bets per game). Part C selection on HK-sel happens inside run_test.py (it is a holdout window).

Part B / verdicts
20. Forecast "10-19 mph" = 10 <= fc < 20; B3 = fc >= 20; B1 observed wind is integer in games.csv. Open-air = roof in {outdoors, open}
    AND stadiums.csv roof_category == "open-air" (join on stadium_id). B4/B5/B1 apply to games with a closing total; B4/B5 REG only.
21. Part B clean sportsbook sets: B1/B4/B5 = D + H3 (W, H1, H2 reported separately), B2/B3 = H3 only (2021-25 second look reported).
    Luck test pools the clean seasons; the "random games of that season" pool is ALL games of the season with a total_line and final
    (not restricted to open-air games). HK requirement (>= 30 bets) is applied to B2 and B4 only. PROMISING needs every clean point
    estimate > 0 where D always counts and H3 / HK count only with >= 10 bets, plus pooled clean mean > 0 and luck < 10%.
22. B2 hindsight label: computed on all B2 bets (forecast_ok) of the forecast era 2021-26 (and also reported for H3 alone); label =
    win rate (decided) > 52.38% where observed >= forecast but not where observed < forecast.
23. Part A luck test uses the pooled H1+H2 bets (the set criterion 1 is computed on). One luck seed (20261024) is used for all parts.

## Status of inputs
kalshi/{markets,events,candles_1h,candles_1m}.parquet do not exist yet; the Kalshi code was exercised only on synthetic data and on the
column schema of kalshi/smoke/. weather/wind_forecasts.parquet exists but is still being built by another agent (1,700 rows when last
read); b_frame uses only game_id, fc_wind_mph, forecast_ok, obs_wind_mph and stadiums.csv stadium_id, roof_category.
