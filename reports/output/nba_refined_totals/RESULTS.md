# #65 results: the refined totals strategy (2026-10-23)

**Both parts FAIL.** The holdout was run once, after the agreement check and the Opus audit. The numbers come from
results_test.json.

| | Discovery (chosen, best of N) | Unseen / holdout seasons, sportsbook -110 | Kalshi 2025-26, fee + 1c | Luck test | Verdict |
|---|---|---|---|---|---|
| **A: E3, gap ≥ 8, skip a team's first 10 games** | +9.1c (274 bets, best of 192) | 2024-26: 111-122-1, **-9.0c** | 87 bets, **-0.8c** | 80% | FAIL |
| **B: one team missing 30+ PPG of fresh absences → OVER** | +8.1c (312, best of 16) | 2020-21: 68-48-1, +11.8c; 2024-26: 90-96-1, **-7.6c** | 111 bets, **-5.8c** | 30% | FAIL |
| **Live rule #40 v5 (reference)** | +4.7c (2023-24) | 2024-26: 742-743-13 (50.0%), **-4.6c** | 730 bets, **-2.5c** | 50% | (would FAIL) |

**In plain words**
- **The refined pick rule's discovery gain was luck from trying many versions.** On new seasons it lost.
- **"Scorer sits → bet the over" worked in older seasons and has faded every year since.** In the last two seasons it
  lost, and it lost on Kalshi.
- **The live bot's rule is a coin flip:** exactly 50% on 1,498 recent games. Fees make it slightly negative.

Nothing is paper-tracked, per the pre-registration. Disclosures: PREREGISTRATION.md (amendment 1), AGREEMENT.md, and
the research log entries #65.
