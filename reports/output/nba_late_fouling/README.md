# #51 late-Q4 fouling vs blowouts (builder README)

Files: lib.py (pipeline), run_discovery.py, run_unseen.py (gated), test_late_fouling.py (21 tests).
Outputs (discovery): results_discovery.json, discovery_versions.csv, records_discovery.csv.
Reuses #46 (imported from ../nba_ingame_overreaction): fee_taker, implied_total, main_strike, EventCandles, lb, bootstrap_lb,
coverage. No network. SQLite opened read-only; per-ticker queries only; pbp read with a game_id filter and 10 columns.
run_unseen.py refuses without AUDIT_OK, if results_unseen.json exists, or unless discovery luck verdict is PASS with a
selected version and a complete download. The builder never created AUDIT_OK and never ran run_unseen.py.

## Builder notes (readings of ambiguous points)
1. C_R = first period-4 event by game_play_number (hoopR wallclock is not monotone) with clock minutes*60+seconds <= R; its
   wallclock is C_R. Margin is from that same event. Wallclock out-of-order events are used as given (window guard catches gross errors).
2. bad_wallclock is game-level (Q1 checkpoint = first period-2 event, as #46). Games with no period 2 or 4: pbp_missing_period.
3. "Within 5 min": decision candle must end <= C_R + 60 s + 300 s, else no_decision_candle. A decision candle after the game's
   last pbp event is a separate skip (decision_after_game_end, #46 rule).
4. Quotes at the decision candle are carried forward from the latest in-game candle of each strike (#46 behaviour; quote age reported).
   Only candles with end_ts > tip are used (no pre-tip candle).
5. Live main strike needs ask-bid <= 0.10 (#46 main_strike). Calibration's implied total (YES-mid 0.50 crossing, spread <= 0.15 per #46)
   can be missing; those records are still bet on but excluded from the calibration table (n_cal < n_bets_records).
6. Entry skip (price after +1c slippage >= 0.99 or <= 0.01) is counted per version as price_skips.
7. Luck test: per (game, R) a 50/50 YES/NO coin shared across all versions; real direction is replaced by the coin; the 12-version
   selection (n >= 60 after price skips, max LB) re-runs per rep. FAIL if share of reps with best LB >= real best LB is > 5%.
   If no version is eligible the verdict is FAIL.
8. Calibration final total = last pbp event's home+away (includes OT), not the Kalshi result. Settlement/net uses the Kalshi result
   of the strike in the pre-tip mkts table. The Kalshi OT rule text is not on disk (no network), so it is not recorded; the Kalshi
   result is used either way as the prereg says.
9. Free throws after C_R = free-throw attempts (type_text starting "Free Throw") strictly after the checkpoint event, whole game
   including OT. OT rate and FTs are over non-skipped records, per R x margin bucket.
10. Bucket 7-10 and 11-14 have no bets (versions use <=3, <=6, >=15, >=20); they appear in the descriptive blocks only.
11. The +2c sensitivity (plus2c_n/mean/lb in the versions table) is not used for selection.
