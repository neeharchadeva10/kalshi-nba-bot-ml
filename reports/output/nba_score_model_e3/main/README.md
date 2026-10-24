# E3 (research #64) - builder notes

Pre-registration: ../PREREGISTRATION.md. Definitions: ../FEATURES_E3.md.

Order: `features.py` -> (checker writes check/e3_features_check.parquet) -> `compare.py` (AGREEMENT.md) -> `run_select.py` (only on PASS)
-> `run_test.py` (only after the Opus audit; NEVER run by the builder; `make_audit_hashes.py` only prints). Tests: `test_e3.py`.

## Readings and ambiguities (the most literal / conservative reading was taken)
1. Rotation threshold for "who counts" is 10 mpg (the E3 pre-registration), not 15 as in E2. Windows and all other #62 rules are unchanged.
2. TIER boundaries: star >= 28 mpg, starter 20 <= mpg < 28, bench < 20 (all players counted have >= 10).
3. AGE: "Sigma mpg of missing veterans / young players" = raw window mpg (not divided by 240). Age on the game date in completed years.
   Missing DOB or age outside 18-45 -> unknown -> left out of AGE only.
4. Q family: MIN is window mpg / 240 (same scaling as the MIN family).
5. NEW family: moves are visible only when first_date_new < the game's ET date (strictly before). Consequence: an offseason signing's first
   game is not visible to himself on opening night, so opening-night NEW values are 0 for offseason moves. "New to the team this season" =
   a move to the team with season_new = this season-year. "Last-10-game minutes" = the team's last <= 10 games of this season (fewer if the
   team has played fewer; 0 if none). "Players no longer on it" = a visible move away whose latest visible move does not return to the team;
   players who left without a recorded destination (waived, retired) are NOT visible in the moves file and are therefore missed.
6. The two report cutoffs share all non-report families (NEW), so NEW-only versions are identical for c60 and c20 (still counted: 8,190).
7. Ridge: lambda grid {1,10,100,1000,10000}; LOSO over the two training seasons; pooled out-of-fold MSE; ties -> larger lambda. The subset
   engine (Gram blocks + eigendecomposition) is mathematically the same as #62's closed form; tests compare them and run_select re-fits the
   winner with the direct path and asserts agreement before freezing.
8. Ties in validation total MAE go to E1, then to the earlier version in enumeration order (c60 before c20; ascending family bitmask,
   bit 0 = PTS ... bit 11 = NEW).
9. E1 reproduction gate: the "2022-26 total MAE 14.79634" needs the test seasons' outcomes, so run_select does NOT recompute it; it asserts the
   stored value in nba_score_model/results_test.json (14.79634...) and E1's frozen.json hash. E1 predictions are recomputed for 2021-24 only.
10. Test stage: team-MAE claim uses per-game mean of the two teams' |error| differences (E3 - E1), same bootstrap seed 20261023, 10,000 draws.
    Games with a NaN E1 prediction are dropped as whole games.
11. plus_minus is a string ("+7"); unparsable values would be 0 (none occur among played rows). DNP rows have no stats.
