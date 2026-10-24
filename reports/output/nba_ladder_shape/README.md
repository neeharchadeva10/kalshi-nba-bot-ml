# Research log #61 - shape of the pregame NBA total ladder

Pre-registration: PREREGISTRATION.md. Files: ls_common.py (constants, reused #44 implied_total / #46 fee_taker, hashes),
ls_pipeline.py, checks.py -> CHECKS.md, run_discovery.py (done), run_unseen.py (gated, NOT run), make_audit_hashes.py (prints only),
test_ladder_shape.py. No network. Unseen runs only after Opus creates AUDIT_OK.

## Builder notes (ambiguities, most literal / conservative reading taken)

1. Ladder = per strike, last 1-minute candle ending <= D = T - 60 min (T from events_list.csv, equal to candles.sqlite done.T for
   all events), age <= 30 min, bid <= ask, ask - bid <= 0.15. bid = 0 and ask = 1 quotes are allowed into the ladder/fit/calibration
   (but #44's implied_total drops bid = 0 / ask = 1 strikes when computing M).
2. Band/tail: distance d = K - M (high) or M - K (low), half-open [lo, hi). Nearest to the centre (8 / 13); a tie takes the lower strike.
   The nearest strike is picked first; if its spread > 0.10 the game has no bet in that band x tail (no fallback to another strike).
3. "Skip an entry >= 0.99": tested on the entry price INCLUDING the +1c slippage (ask + 0.01 >= 0.99 is skipped); more skipping.
   Skips are per version (buy and sell entries can differ). n of a version counts bets actually placed.
4. Sell-tail = buy the opposite side taker (high tail: NO at 1 - yes_bid; low tail: YES at yes_ask), same slippage and fee.
5. Hourly cross-check: hourly candle = latest ending in [D-30min, D] (for T on the half hour the hourly grid ends at D-30min); the
   1-minute quote is compared as of that same hourly end_ts. The exclusion list uses quotes only (no outcomes) and is applied to
   discovery and unseen alike. checks.py itself covers discovery dates only (it also reads discovery finals/results).
6. Strike-result consistency is reported only (no exclusion): 2 of 7283 strikes disagree (event KXNBATOTAL-25NOV17OKCNOP, hoopR final 229
   vs Kalshi YES at 229.5 and 232.5 - likely a hoopR final error); bets use Kalshi's result as instructed. 22 strikes (3 events
   without hoopR match) have no final, so they are in Part B but not in the z measurement.
7. Normal fit: Levenberg-Marquardt from 3 sigma starts (6/18/40), sigma bounded [0.5, 200]; mids strictly inside (0.02, 0.98); unweighted.
   Part A calibration uses every ladder strike of games that have an M, d bucketed half-open [lo, hi); d outside +-15 is dropped.
8. Luck test: one coin per game x band x tail candidate; buy-versions use the buy side on heads, sell-versions the opposite;
   versions with < 60 bets in a rep are ineligible; FAIL if share of reps with best LB >= real best LB is > 5%.
   Verdict also "NO_ELIGIBLE_VERSION" if nothing has n >= 60 (unseen refuses on anything but PASS).
9. Selection is pinned by results_discovery.json (hash-checked in AUDIT_OK), not by constants in code.
10. required_paths() adds events_list.csv, the hoopR play-by-play parquet, results_discovery.json and the #44/#46 common.py files
    to the hashed set (beyond "code + two sqlite").
11. Bootstrap resamples games with replacement (one bet per game per version); coin flip = 500 copies, random side per candidate at the
    same strikes, share with mean >= real mean.
