# Research #59 main code - builder notes

Files: `sm_lib.py` (paths, splits, feature builder, sampling, gates/hashes), `features.py`, `compare.py`, `models.py` (ridge, LOSO CV,
stacking, grid team points, metrics), `run_select.py`, `run_test.py` (NEVER run before the Opus audit), `make_audit_hashes.py` (prints only),
`test_score_model.py` (29 tests), `conftest.py`.  Definitions: `../FEATURES.md`.

Run order: `features.py` -> `compare.py` -> `run_select.py` (once) -> [Opus audit creates AUDIT_OK] -> `run_test.py` (once).

## Builder notes (ambiguities, most literal / conservative reading)

1. Team identifiers are raw hoopR codes (NJ, SEA, GS, NO ...); previous-season lookups use franchise continuity (NJ->BKN, SEA->OKC).
2. `last 10` / `last 5` with fewer games available use all available games; only a window with 0 games falls back to the S-1 value.
3. League ortg / pace "season to date" use team-game rows before D; the 50-game threshold counts games (rows / 2).
4. Rest uses ET calendar dates (gap in days - 1, cap 4, first game of S = 4); `dos` counts from the league's first regular-season ET date of S.
5. hoopR has fewer games than the NBA schedule in some seasons (2012-13: 1,224 usable of 1,230 - hoopR has 1,225 and one is the all-star game /
   non-NBA code; 2013-14, 2016-17, 2017-18: 1,229 each, one all-star game filtered).  No game was imputed.
6. Grid TEAM points: the grid projects totals only.  The split used (tested to sum exactly to the grid total, `models.GridSeason.team_points`):
   per-team `one_points`; shrink = each team shrunk toward Lt/2 with the formula's slope b; rest effects: b2b -> c_b2b for each team on a
   back-to-back, cat -> e_cat of the team's own rest category.  Totals (the headline) are identical to the grid's.  Team MAE / margin / winner %
   of grid formulas depend on this split convention (preregistration silent).
7. G enters as its validation-best formula by pooled validation TOTAL MAE (ties -> smaller flat index).  E1 and E2 use that same G* (so G*'s
   choice used validation outcomes; E2's weights and both ridges use training seasons only).  Stated here because the preregistration says
   "the best G formula" without naming the period.
8. R1 / R2 ridge: standardised features, unpenalised intercept, lambda in {0.1, 1, ..., 1e6} chosen by leave-one-season-out CV MSE on the
   training seasons (ties -> larger lambda).  Both predict team points; the total is the sum of the two team predictions.
9. E2: unconstrained least squares with intercept on training team-games, inputs = G* in-season grid points (constants from S-1, so
   walk-forward by construction), R1 and R2 leave-one-season-out out-of-fold predictions.  R1/R2 are strongly collinear, so the fitted weights
   can be large with opposite signs (w1 < 0); not constrained because the preregistration says "stacked linear blend".
10. Constants for 2022-23 follow #56 (no-prior fit on 2021-22, `legacy_2022=True` in fa_ext) so that B2 reproduces #56; this matters only at test.
11. "Within 10" is |error| <= 10 inclusive.  Bias = mean(predicted total - actual total).
12. OT flag (run_test only): play-by-play max period > 4 where the file exists (2024-25, 2025-26 only on disk); otherwise player-box minutes (a
    team's summed minutes > 252.5; 2022-23, 2023-24).  Where both exist they are cross-checked and disagreements counted in results_test.json.
    team_box has no minutes.  2026-27 games to date have no OT source here (reported as unknown; excluded from the ex-OT metric).
13. Sportsbook reference row: `fa_lib.load_closing()` (in-play rows with snapshot >= commence_time dropped, line outside 180-270 dropped),
    total MAE of the close vs the frozen model's MAE on the same games; never an input.
14. 2026-27 (14 games on disk) is evaluated descriptively with the frozen model in run_test.py; no grid / feature code is special-cased.
15. Not run / not created: `run_test.py`, `AUDIT_OK`, `test_started.json`, `results_test.json`.
