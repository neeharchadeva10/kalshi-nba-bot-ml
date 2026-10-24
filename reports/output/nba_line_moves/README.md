# Research #54: pregame NBA total line moves (follow vs fade)

Pre-registration: PREREGISTRATION.md (unchanged). Data: ../nba_injury_speed/candles.sqlite (read-only), events_list.csv.

Files: lm.py (rules, stats, gates), pipeline.py (per-event streaming), run_discovery.py (run once), run_unseen.py (gated; never run by the builder),
test_line_moves.py (18 tests), outputs results_discovery.json, discovery_versions.csv, records_discovery.csv.

Run: `.venv/Scripts/python.exe reports/output/nba_line_moves/run_discovery.py`; tests: `-m pytest reports/output/nba_line_moves`.

## Builder notes (ambiguities, more conservative reading taken)

1. **Staleness for the main strike.** The pre-reg states the 10-minute staleness limit for M(t). I applied it to the main-strike
   quote at D as well (a quote older than 10 min is not tradable). Candle `bid`/`ask` are the candle-close quotes.
2. **Candle "ending <= t".** Uses `end_ts <= t`. D = T - 3600 exactly; D - w = D - 2h or D - 4h.
3. **Slippage.** +1c on the buy price (YES: ask + 0.01; NO: (1 - bid) + 0.01); fee on the slipped price; hold to settlement from `mkts.result`.
   Games whose main-strike market has a result other than yes/no ("scalar") are skipped (1 discovery game).
4. **Entry is shared by all versions.** One entry per game (main strike at D); both sides' nets are stored, so follow/fade and the luck test use
   the same strike and candle.
5. **Luck test.** Per rep, one random YES/NO per game over every game with an entry; the same draws for all 8 versions; selection re-run per rep
   (n >= 60, highest normal LB = mean - 1.96 sd/sqrt(n)); share of reps whose best LB >= the real best LB. Reps with no eligible version count as -inf.
   Follow and fade share one trigger set, so the random version has the same n as the real one.
6. **Final total (calibration)** = sum of the two team scores in hoopr team_box_2026 for `hoopr_gid` (discovery games only).
   M(T) (drift) = M at t = T (last candle <= T, <= 10 min old).
7. **Calibration/drift use all games with the needed values**, not only triggered ones. Drift block also reports the signed drift in the move direction for |Move| >= X.
8. Bootstrap/coin flip (unseen only, pass bar) copied from #44 (10,000 draws by game, 500 copies, seed 20261022).
9. run_unseen.py refuses unless AUDIT_OK exists, results_unseen.json is absent, and discovery `luck_test.verdict == PASS` with a selected version.
   The builder did not create AUDIT_OK and did not run run_unseen.py.
