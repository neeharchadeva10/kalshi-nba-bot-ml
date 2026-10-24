# Research #55: in-game NBA total tails 10-15c / 85-90c (one pooled rule)

Pre-registration: PREREGISTRATION.md. Files: tails1015_lib.py (rule; reuses #52 tails_lib), run_discovery.py (in-sample,
reference only; run once), run_unseen.py (refuses without AUDIT_OK or if results_unseen.json exists; NOT run),
test_tails_1015.py (21 tests).

## Builder notes (ambiguities, conservative readings)
- Band edges use 1e-9 tolerance on mids rounded to 6 dp: over [0.10, 0.15), under (0.85, 0.90].
- "Nearest to center" ties compared after rounding |mid - center| to 6 dp; over ties -> lower strike, under -> higher.
- Requirements (spread <= 0.10, yes_bid >= 0.01, yes_ask <= 0.99) are applied to BOTH tails and to the chosen strike
  only; added crossed-quote and non-binary-result skips. #52's "entry >= 0.995" skip is not used (cannot trigger).
- Skip reason precedence: crossed, spread, bid, ask, scalar.
- Only HT and Q3 records enter the rule; build_records also builds T-1h records, which are ignored (but T-1h game-level
  skip counts appear in game_level_skips).
- Calibration table = #52's 8 buckets over all strikes at HT + Q3 only (not T-1h as in #52).
- Coin flip uses #52's coin_flip_share (one random side per bet, 500 copies, seed 20261022).
- Bootstrap: #46 bootstrap_lb over games (all bets of a game grouped), 95% LB = 2.5% quantile.
- Loaded #52's lib by file path as module "tails52_lib"; sys.path restored afterwards (test guards it).
- Discovery numbers are in-sample and do not count.
