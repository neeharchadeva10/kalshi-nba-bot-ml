# Research log #58 - early-line study (builder README)

Pre-registration: PREREGISTRATION.md (unchanged). Files: el_lib.py (all logic), checks.py (CHECKS.md, checks.json),
run_discovery.py (results_discovery.json, discovery_versions.csv, records_discovery.csv), run_unseen.py (NEVER run by the
builder; gated), make_audit_hashes.py (prints the AUDIT_OK JSON only), test_early_line.py.

## Builder notes (ambiguities, conservative reading taken)

1. Fee is computed at the PAID price (quote + 1c slippage), as #44/#46 do; #42 computed it at the quote. Difference is < 0.1c.
2. L_e uses each strike's last 1-minute candle ending at or before t_e and <= 30 min old (inclusive). The 1-minute table holds
   candles only for minutes with activity, so a standing quote unchanged for > 30 min is treated as missing (literal reading).
   At T-8h the candle download starts exactly at T-8h, so only the boundary snapshot exists: 53% of games have L_e, with a
   median of 2 quoted strikes (crude interpolation).
3. Main strike: mid nearest 0.50, mid 15-85c, spread <= 0.10, ties to the lower strike (the 15-85c filter is not in #46's helper).
4. Markets with result 'scalar' (or no result) cannot be settled: the (game, t_e) record is skipped ("no_result").
5. T = #42's T (prep.build_games). It differs from the hoopR tip for 10 games (postponements/delays). The F56 reproduction is exactly
   0.0 on all 1,230 common games using #56's own inputs (T := hoopR tip); with #42's T one game (GSW@MIN, 2026-01-25, postponed
   from 01-24, UNSEEN half) differs by 0.0016 because the decision date is the earlier day (less information; kept as the
   conservative choice). #56 has 1,231 games; one has no Kalshi total market.
6. F56 = #56 frozen primary team_raw (flat 7791, shift 0.0), world built by fa_lib.build_world('2025-26') (constants on 2024-25).
   F42c = grid_run.load_world + fit_world_constants + project_one(winner spec) + 4.0.
7. "Excluded from BOTH tables' results" for games > 1.5 points apart at T-4h: such a game is dropped from the whole study (all
   t_e). Discovery: none were > 1.5 apart. The unseen run recomputes the exclusion on its own dates (price data only).
8. The hourly table's candles are cut off at T-1h; its T-4h line is defined with the same rules as the 1-minute one.
9. Finals: the hoopR total decides MAE; Kalshi settlement (candles.sqlite mkts.result) decides P&L. totals.sqlite expiration_value is
   empty for most historical-tier markets, so the literal "max expiration_value over NO strikes" exists for only 61 of 663 games
   (all 61 equal hoopR). Added: every strike's yes/no result agrees with the hoopR final in all 663 games. The odds-API
   file has no score columns.
10. Drift L_(T-1h) - L_e uses the T-1h line from the same 1-minute table (candle <= T-1h, <= 30 min old); the same test with #42's
    T-1h market median is in results_discovery.json under drift_alt_*.
11. MAE comparison uses the games where L_e, the T-1h median, both formulas and the final all exist (per t_e).
12. Luck test: each rep draws one 50/50 side per (game, t_e), shared by all 18 versions; selection (n >= 60, max LB) is re-run.
13. Coin flip (unseen): 500 copies, random side on the selected version's own bets; share of copies with mean >= the real mean.
14. AUDIT_OK covers el_lib.py, checks.py, run_discovery.py, run_unseen.py, results_discovery.json and the reused #44/#46/#56/#42 files
    (see el_lib.AUDIT_FILES). A new discovery run changes results_discovery.json and invalidates the audit.
