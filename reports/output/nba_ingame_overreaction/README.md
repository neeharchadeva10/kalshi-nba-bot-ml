# #46 in-game total overreaction: notes
- NEXT exit falls back to HOLD when no exit quote exists (no candle, null quote, or bid <= 0). It is counted per version in
  n_fallback_hold (discovery_versions.csv). It does not affect the selected version (Q1, T10, follow, HOLD).
- Amendment 2026-10-22: bad_wallclock, in-game-only quotes, stale_L0 and the coverage gate are implemented (see PREREGISTRATION.md).
- Fee: KXNBATOTAL series fee_type = quadratic_with_maker_fees, fee_multiplier = 1 (taker 0.07*P*(1-P)).
