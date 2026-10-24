# Research #53: resting (maker) orders on the #48 and #49 near-misses

Implements PREREGISTRATION.md literally. Discovery only so far; `run_unseen.py` has never been run.

## Files
- `nm_lib.py` fee, fill rule, E0 rule, splits, selection, bootstrap, luck candidates, unseen gates.
- `fam48.py` #48 family (reuses `nba_teammate_out/tm_lib.py` and `analysis.report_sets`, unchanged).
- `fam49.py` #49 family (reuses `nba_internal_mismatch/mismatch.py`, `population.csv`, `k_frozen.json`).
- `nm_pipeline.py` 40 versions, stats over FILLED bets, joint luck test.
- `run_discovery.py` (run once) -> `results_discovery.json`, `discovery_versions.csv`, `records_discovery.csv`.
- `run_unseen.py` gated (AUDIT_OK, no prior results_unseen.json, discovery luck PASS, #49 unseen download for a #49 selection).
- `test_maker_nearmiss.py` 33 tests.

## Data check (#49 OHLC)
Trade price OHLC (`price.low/high`) is present in the raw team/1H gz cache and in the `raw` JSON column of `totals.sqlite`. The #49 family was therefore built and run.
Only 4034 of 6984 discovery team/1H tickers have a cached file; the rest were never downloaded or returned nothing.

## Builder notes (ambiguities, each resolved with the more conservative or more literal reading)
1. **E0 granularity.** "Earliest candle in the market's file" is per market. Used:
   - #48: per (beneficiary, stat) = the earlier of its 2 strike files' first candle end; a strike without a candle ending exactly at E0 is unavailable (no_candle).
   - #49: per game = the earliest first-candle end over all the game's markets; markets without a candle at exactly E0 give no quote.
2. **#48 info cutoff.** Star/late/active/beneficiary logic is re-evaluated with the report cutoff = that bet's E0 (not T-1h); `late` = first Out report in (E0-3h, E0].
3. **fee_type.** Only KXNBATOTAL's series definition is cached (`quadratic_with_maker_fees`). KXNBAPTS/REB/AST/TEAMTOTAL/1HTOTAL are unknown, so the maker fee is charged.
4. **k.** Read from `k_frozen.json` (0.51128727...; the pre-registration's 0.5113 is its rounding).
5. **#49 unit.** One unit per game per version. Team "other follows" posts 2 legs; unit net = mean over FILLED legs. n and LB use filled units (games), not legs. Legs posted/filled are in the `signals`/`fills` columns.
6. **#49 limit range.** The 15-85c rule is applied to the limit of the side being bought; a leg outside it is not posted (not counted as unfilled).
7. **#48 limit.** YES at yes_bid close at E0; the strike is chosen exactly as #48 (nearest 0.50 mid, 15-85c mid, spread <= 10c), then the bid must be in 15-85c.
8. **Luck test.** One rng stream. Per rep: #48 draws one pool order per distinct real bet (the pool order has its own fill outcome, and a replacement that does not fill drops out of n); #49 draws one side per game, shared by all 16 #49 versions. Selection re-run over all 40 each rep. If no version has n>=60 filled the test is NOT RUN.
9. **Pool (#48).** Every selected market with a valid quote at its own E0, limit in 15-85c, player not listed Out/Doubtful at E0.
10. **+1c sensitivity** = price paid limit+1c, fee recomputed on that price.
11. **Non-binary** settlements: valued at settlement value (none appear in the discovery records, 831 order outcomes all 0 or 1; #49 games with a non yes/no result are dropped for that combo).
12. **Unfilled win rate**: share of unfilled posted orders whose bought side settled at exactly 1.
