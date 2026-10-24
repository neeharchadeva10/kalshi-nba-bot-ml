# AGREEMENT - research #62, main injury features vs independent recomputation

STATUS: PASS

FEATURES_SHA256: 0ea03ad0ffd45ff2f003e79989f428afaef9eb5d00cd87ede9e759e0291b613a
CHECK_SHA256: 002856bb4836d54f2ec9e3b4395e6289d3eff55d972e108b722f19113e77cde1

Tolerance 1e-09 (absolute) on the 8 model features.  Sample rows 200, checker rows 200, matched 200; missing in main 0; missing in check 0; only in check 0; duplicates in check 0.

| column | gating | mismatches | NaN-pattern mismatches | max abs diff |
|---|---|---|---|---|
| out_pts_own | True | 0 | 0 | 1.42e-14 |
| q_pts_own | True | 0 | 0 | 7.11e-15 |
| back_pts_own | True | 0 | 0 | 3.55e-15 |
| out_min_own | True | 0 | 0 | 1.11e-16 |
| out_pts_opp | True | 0 | 0 | 1.42e-14 |
| q_pts_opp | True | 0 | 0 | 3.55e-15 |
| back_pts_opp | True | 0 | 0 | 1.42e-14 |
| out_min_opp | True | 0 | 0 | 2.22e-16 |
| n_unmatched_own | False | 0 | 0 | 0 |
