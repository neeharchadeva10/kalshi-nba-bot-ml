# #65 main implementation

Files: rt_lib.py (all pure functions + Part B scan + evaluation/verdict), build_tables.py (writes ../main_out/), gates.py,
run_select.py (D only; NOT run), run_test.py (audit-gated, run-once; NOT run), make_audit_hashes.py, test_rt.py (22 tests).
Run order: build_tables.py, gates.py, pytest, (Opus) run_select.py, audit, run_test.py.

## Readings (ambiguities resolved literally; amendment 1 already applied)
1. Game universe = hoopR regular-season games through #42's team_games_g (drops non-NBA teams): 1080/1230/1230/1231/1231/1231.
2. F42 = frozen #42 spec via fa_lib.build_world per season, raw projection, no shift. Not defined for 2020-21/2021-22.
3. E3: frozen for 2023-24+, cross-fit (21-22 rows only, lambda 1000) for 2022-23; undefined 2021-22.
4. Lean b: all league games of the season with the model defined and ET date strictly before; b=0 if < 30; games without a line count.
5. Closing lines: snapshot < commence, 180 < line < 275, dedupe by game_id, regular-season ids only.
6. Part B: window = team's last <=10 games of THIS season strictly before g's ET date (needs >= 5); candidates = latest box row (any
   row incl. DNP) before g is for X in this season; mpg/fresh_ppg = sum over window games of rows for X / window size; fresh = minutes>0
   in the last 3 window games. Report flavor = matched (exact name among current-team players) OUT/DOUBTFUL in the latest report
   created <= tip-3600 s (72 h look-back, #62), and must also be a candidate (mpg>=M, fresh). Box flavor = candidate with no minutes>0 row in g.
   Team with <5 games, no report, or no rows => S = 0 (not NaN).
7. Game-level eligibility (luck test B, `ok_report`/`ok_box`): both teams have >=5 prior games AND (report found for both / box rows for both);
   luck-B copies also need a close line. This is my choice; "random eligible games" is not defined in the pre-registration.
8. Bootstrap lower bound = 2.5th percentile of 10,000 resampled means (as #60). Luck fraction = share of copies with mean >= actual.
9. Ties in selection: equal mean (1e-12) -> more bets -> earlier in versions_all.csv order. D window of F42 = 2023-24 only (amendment 1).
10. Kalshi bets: Part A gap vs T-1h median (`kalshi_bets` rule) and trade at the main strike with skips applied; Part B OVER at the main strike.
11. PROMISING check: each sportsbook window separately (H1 for B, H2-sb) and H2-K when >= 10 bets.
12. Rehearsal windows (D-only stand-ins) exist as rt_lib.REHEARSAL_WINDOWS; not run on real data.
