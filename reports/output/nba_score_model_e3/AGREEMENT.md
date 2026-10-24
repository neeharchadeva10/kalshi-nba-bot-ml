# AGREEMENT - research #64, main E3 features vs independent recomputation

STATUS: PASS

FEATURES_SHA256: 9ad0520718ac27e545c5b06b43b3854b1bf1db0febb3a17dc08634655791a272
CHECK_SHA256: 2390807f94a2281f4b9b7b71c552d69a68ad25db7fd949df49b1cfdc45ebdee1

Tolerance 1e-09 (absolute) on the 68 model feature columns.  Sample rows 200, checker rows 200, matched 200; missing in main 0; missing in check 0; only in check 0; duplicates in check 0.

Gating columns mismatching: none

| column | gating | mismatches | NaN-pattern mismatches | max abs diff |
|---|---|---|---|---|
| pts_own_c60 | True | 0 | 0 | 2.84e-14 |
| pts_opp_c60 | True | 0 | 0 | 1.42e-14 |
| min_own_c60 | True | 0 | 0 | 2.22e-16 |
| min_opp_c60 | True | 0 | 0 | 2.22e-16 |
| reb_own_c60 | True | 0 | 0 | 7.11e-15 |
| reb_opp_c60 | True | 0 | 0 | 7.11e-15 |
| ast_own_c60 | True | 0 | 0 | 3.55e-15 |
| ast_opp_c60 | True | 0 | 0 | 3.55e-15 |
| tpm_own_c60 | True | 0 | 0 | 1.78e-15 |
| tpm_opp_c60 | True | 0 | 0 | 1.78e-15 |
| dfn_own_c60 | True | 0 | 0 | 1.78e-15 |
| dfn_opp_c60 | True | 0 | 0 | 1.78e-15 |
| usg_own_c60 | True | 0 | 0 | 1.42e-14 |
| usg_opp_c60 | True | 0 | 0 | 1.42e-14 |
| pm_own_c60 | True | 0 | 0 | 7.11e-15 |
| pm_opp_c60 | True | 0 | 0 | 7.11e-15 |
| tier_star_own_c60 | True | 0 | 0 | 1.42e-14 |
| tier_star_opp_c60 | True | 0 | 0 | 1.42e-14 |
| tier_starter_own_c60 | True | 0 | 0 | 1.42e-14 |
| tier_starter_opp_c60 | True | 0 | 0 | 7.11e-15 |
| tier_bench_own_c60 | True | 0 | 0 | 3.55e-15 |
| tier_bench_opp_c60 | True | 0 | 0 | 1.78e-15 |
| age_vet_own_c60 | True | 0 | 0 | 1.42e-14 |
| age_vet_opp_c60 | True | 0 | 0 | 0 |
| age_young_own_c60 | True | 0 | 0 | 1.42e-14 |
| age_young_opp_c60 | True | 0 | 0 | 1.42e-14 |
| q_pts_own_c60 | True | 0 | 0 | 7.11e-15 |
| q_pts_opp_c60 | True | 0 | 0 | 1.42e-14 |
| q_min_own_c60 | True | 0 | 0 | 0 |
| q_min_opp_c60 | True | 0 | 0 | 1.11e-16 |
| new_share_own_c60 | True | 0 | 0 | 0 |
| new_share_opp_c60 | True | 0 | 0 | 0 |
| gone_share_own_c60 | True | 0 | 0 | 0 |
| gone_share_opp_c60 | True | 0 | 0 | 0 |
| pts_own_c20 | True | 0 | 0 | 2.84e-14 |
| pts_opp_c20 | True | 0 | 0 | 1.42e-14 |
| min_own_c20 | True | 0 | 0 | 2.22e-16 |
| min_opp_c20 | True | 0 | 0 | 2.22e-16 |
| reb_own_c20 | True | 0 | 0 | 7.11e-15 |
| reb_opp_c20 | True | 0 | 0 | 7.11e-15 |
| ast_own_c20 | True | 0 | 0 | 3.55e-15 |
| ast_opp_c20 | True | 0 | 0 | 3.55e-15 |
| tpm_own_c20 | True | 0 | 0 | 1.78e-15 |
| tpm_opp_c20 | True | 0 | 0 | 1.78e-15 |
| dfn_own_c20 | True | 0 | 0 | 3.55e-15 |
| dfn_opp_c20 | True | 0 | 0 | 1.78e-15 |
| usg_own_c20 | True | 0 | 0 | 2.84e-14 |
| usg_opp_c20 | True | 0 | 0 | 1.42e-14 |
| pm_own_c20 | True | 0 | 0 | 7.11e-15 |
| pm_opp_c20 | True | 0 | 0 | 7.11e-15 |
| tier_star_own_c20 | True | 0 | 0 | 1.42e-14 |
| tier_star_opp_c20 | True | 0 | 0 | 1.42e-14 |
| tier_starter_own_c20 | True | 0 | 0 | 1.42e-14 |
| tier_starter_opp_c20 | True | 0 | 0 | 7.11e-15 |
| tier_bench_own_c20 | True | 0 | 0 | 3.55e-15 |
| tier_bench_opp_c20 | True | 0 | 0 | 1.78e-15 |
| age_vet_own_c20 | True | 0 | 0 | 1.42e-14 |
| age_vet_opp_c20 | True | 0 | 0 | 0 |
| age_young_own_c20 | True | 0 | 0 | 1.42e-14 |
| age_young_opp_c20 | True | 0 | 0 | 1.42e-14 |
| q_pts_own_c20 | True | 0 | 0 | 7.11e-15 |
| q_pts_opp_c20 | True | 0 | 0 | 3.55e-15 |
| q_min_own_c20 | True | 0 | 0 | 0 |
| q_min_opp_c20 | True | 0 | 0 | 5.55e-17 |
| new_share_own_c20 | True | 0 | 0 | 0 |
| new_share_opp_c20 | True | 0 | 0 | 0 |
| gone_share_own_c20 | True | 0 | 0 | 0 |
| gone_share_opp_c20 | True | 0 | 0 | 0 |
