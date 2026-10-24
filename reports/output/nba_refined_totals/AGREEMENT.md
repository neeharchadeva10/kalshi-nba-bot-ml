# #65 agreement: main/ vs the independent check/ (Opus, 2026-10-23)

The comparison script is agreement.py; its output is agreement_stdout.txt. The comparison was run after amendment 1
and after two fixes to main.

## Fixes to main before the final comparison

1. **Current team by tip time.** Game 401585384: Schroder had played for BKN earlier the same day. The fix changed 9
   box-flavor M20 team-games and 4 M28 team-games out of 14,466.
2. **2020-21 report flavor is NaN** (that season has no injury reports).

## Results

| Check | Result |
|---|---|
| Part B features: 200 team-games, S_report / S_box at M20 and M28, plus the absentee id lists | 200/200 exact (max diff 3.6e-15) |
| Skip flags Q / NEW / EARLY: 200 games | 200/200 |
| Lean fix b: 50 dates × 3 models | 150/150 (max diff 1.1e-14; n identical) |
| Discovery versions: 10 Part A + 4 Part B | 14/14 identical (n, W, L, P, mean P&L) |

**Shared dependency (disclosed):** both sides compute F42 through fa_lib with the #42 winner spec, so F42 itself is not
independently checked. F42 is gated separately against #42/#56 (diff 0.0).

**Verdict: AGREEMENT PASS.** Selection may run.
