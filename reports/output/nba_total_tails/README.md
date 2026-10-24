# Research #52 - far-out NBA total-line tails (sell the long shot)

Pre-registration: PREREGISTRATION.md (unchanged). Read-only, no network; all data on disk.

Files: `tails_lib.py` (rules, stats, luck test, data build), `run_discovery.py` (writes results_discovery.json,
discovery_versions.csv, records_discovery.csv, calibration_discovery.csv), `run_unseen.py` (gated: needs AUDIT_OK, refuses if
results_unseen.json exists, refuses unless the discovery luck test is PASS with a selected version), `test_total_tails.py`.

Reused from #46 (loaded from file, not copied): `fee_taker`, `EventCandles`, `decision_time`, `checkpoints_from_pbp`,
`bootstrap_lb`, BAD_WALLCLOCK window. T-1h is read from the #44 pregame table.

## Builder notes (readings taken where the pre-registration is silent)

1. Strike is chosen FIRST (mid in band, largest/smallest mid), then yes_bid >= 0.01 / yes_ask <= 0.99 / spread <= 0.10 /
   entry < 0.995 / binary result are checked on that strike only. No fallback to the next strike. Ties on mid: lower strike.
2. Missing bid or ask: the strike is not a candidate. Crossed quote (ask < bid) on the chosen strike: skip.
3. Result 'scalar' (33 markets) on the chosen strike: skip (cannot be priced as 0/1).
4. Entry = ask + 0.01 rounded to 4 dp; no 0.99 cap; skip when >= 0.995 (so over-tail yes_bid 0.01 always skips, under-tail yes_ask
   0.985 or above skips). Fee = fee_taker(entry).
5. Quotes are carry-forward: the last candle of each strike ending at or before the decision time (T-3600 for T-1h; first
   candle ending >= C+60 s for in-game, in-game table, end_ts > tip). Quote age is NOT filtered (pre-reg has no staleness rule);
   ages of chosen quotes are in results_discovery.json (tail_spread_and_age).
6. T-1h uses T from events_list (= done.T) and needs no pbp. In-game checkpoints apply #46 game guards: pbp periods present,
   bad_wallclock, stale_L0 (tip - T > 3600), in-game candles only. L0/no_L0_crossing is not used by this study so not applied.
   HT and Q3 each skip when no decision candle exists before the game's last pbp event.
7. Luck test: coin per (game, checkpoint, tail), shared across both bands; heads = rule's favourite, tails = long shot at
   its own ask + 1c + fee (over tail: YES at yes_ask; under tail: NO at 1 - yes_bid). The 12-version selection (n >= 60) is
   re-run each of 500 reps; share = reps whose best LB >= real best LB; PASS iff <= 5%. NO_ELIGIBLE if no version has n >= 60.
8. Calibration: every strike with a quote (ask >= bid) and yes/no result, at each game's three checkpoint decision candles,
   mid bucket [lo, hi) (last bucket includes 100c). Strikes in one game are correlated; descriptive only. A spread <= 10c column is
   included in addition.
9. "Any strike in band" counts use the mid-in-band rule only; "bet_eligible" counts those also passing the requirements.
10. Loss streak: bets of the selected version ordered by decision time; consecutive net < 0; plus max drawdown in dollars.
11. Unseen coin-flip (pass-bar item 3): 500 random-side copies of the selected version's bets; share with mean >= real mean.
