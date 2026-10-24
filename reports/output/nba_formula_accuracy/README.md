# Research #56 - NBA points formula accuracy (builder README)

Run order: `data_checks.py` (done) -> `run_select.py` (done once, wrote frozen.json + frozen.sha256) -> Opus leak audit creates
`AUDIT_OK` -> `run_confirm.py` (never run by the builder).  Tests: `pytest test_formula_accuracy.py` (26), `test_player_model.py` (19).

Files: fa_lib.py (season-generalised grid, metrics, gates), run_select.py, run_confirm.py, data_checks.py, nba_official.py (NBA.com
GET, cached; refused/timed out), repro_check.py (generalised grid vs #42's winner), player_model.py (parallel builder).

## Builder notes (ambiguities and conservative readings)
1. **2022-23 constants**: no 2020-21 on disk, so they are fitted on 2021-22 with k = 0 (no prior blend), dropping the 16 games where a team
   had no earlier game; the league-level shift in that fit uses the in-sample 2021-22 season level. Noisy (first weeks have 1-3 games).
2. **Sportsbook close**: rows whose snapshot is at/after the odds API `commence_time` are dropped (93 of 7,942; possible in-play lines).
   Relative to hoopR's scheduled tip the snapshot is a median of 0.6 min AFTER tip (commence_time is hoopR tip + 10 min for 78% of games),
   not "9 min before tip"; the line is therefore later than T-1h by roughly an hour (favours the line).
3. **LB** for the formula-side rate = Wilson 95% lower bound (pre-registration says only "LB"). Bootstrap CIs are 2.5/97.5 percentiles.
4. **Decision date / T**: hoopR scheduled tip is both `tip` and `T`; D = ET date of tip - 1h (#42's rule). Player model gets the same games.
5. **#37 baseline** = prior-season ratings only through #38's turnover fix (differs from #37's own `build_ratings` by <= 0.0097 pts on
   2025-26); **#38 baseline** = the grid default (w0.5 / avg / season / k20 / shift on), which #42 verified equal to #38 to 1e-9.
6. **Kalshi 2025-26 bets** (confirmation): regular-season games only (season_type 2) of #42's table, both its discovery and unseen halves;
   T applies to |projection - Kalshi main strike|.
7. **Player model rows with NaN projection** (no rotation) are dropped for the player forms only and counted.
8. 2026-27 has no sportsbook lines on disk and no player_box/injury files: descriptive team-family accuracy only.
9. `run_confirm.py` refuses if `player_model.py`'s SHA-256 differs from the one stored in frozen.json (player forms only).
