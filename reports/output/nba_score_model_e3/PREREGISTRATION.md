# Pre-registration — research log #64: E3 = E1 + everything missing players do (all feature combinations)

Written by Opus on 2026-10-23 at 04:13Z. The user: "redo the model the most recent one with the new information on key
players, test all combinations of key players, points time played everything a key player does even assists and
rebounds and name and age. and then lets see what we get". Accuracy only, own data only (no betting lines as inputs).
Changes go in "Amendments".

## Known (disclosed)

- **E2 (#62)** used out/questionable players' PPG.
  - Team-score MAE improved (9.44 → 9.35), but the total did not (14.89 vs 14.90) on 2024-26.
- **#63 audit:**
  - E1 runs -2.4 in teams' first 10 games (roster turnover);
  - 21% of team-games get a new Out/Doubtful listing after the T-60 report;
  - role-player absences move margins more per point than stars.
- **The 2024-26 seasons have now been used as E2's test.** Reusing them as E3's test is a SECOND look and is weaker
  evidence (disclosed). The final word is forward 2026-27 games through the live tracker.

## Player names are NOT inputs

Learning names memorises who used to matter and fails when rosters change. Identity is represented by what the player
does (the families below) and by his role tier and age.

## Data (free; cached; GET only)

- **Frozen E1** (frozen.json d9866fc5...), per-team predictions.
- **Injury reports and windows:** official reports, read with #62's fixed rules (report-set union, CreationDate,
  exact-name matching among current-team players, current-season windows with the last-season fallback, All-Star rows
  excluded, current-team filter).
- **player_box** 2021..2026: minutes, points, rebounds, assists, steals, blocks, turnovers, FGA, FTA, 3PM, plus_minus.
- **Age:** date of birth from hoopR player_core (or the rosters files). Unknown ages are counted and left out of the AGE
  family only.
- **Player moves** (data/derived/player_moves.csv) for the NEW family.

## Feature families (each computed for the team AND its opponent)

**Who counts:** players listed OUT/DOUBTFUL who are rotation-quality (≥ 10 mpg over their window). QUESTIONABLE
players have their own family.

| # | Family | Definition |
|---|---|---|
| 1 | PTS | Σ points per game |
| 2 | MIN | Σ minutes per game / 240 |
| 3 | REB | Σ rebounds per game |
| 4 | AST | Σ assists per game |
| 5 | 3PM | Σ three-pointers made per game |
| 6 | DEF | Σ (steals + blocks) per game |
| 7 | USG | Σ (FGA + 0.44·FTA + TOV) per game |
| 8 | PM | Σ plus-minus per game |
| 9 | TIER | Σ PPG split by star (≥ 28 mpg) / starter (20-28) / bench (< 20). 3 columns per side |
| 10 | AGE | Σ mpg of missing veterans (age ≥ 32) and Σ mpg of missing young players (age ≤ 23). 2 columns per side |
| 11 | Q | PTS and MIN for QUESTIONABLE rotation players. 2 columns per side |
| 12 | NEW | Share of the team's last-10-game minutes played by players new to the team this season (moves), and the share of the team's last-season minutes held by players no longer on it. 2 columns per side |

## Model and versions

- A residual ridge on the frozen E1 (target = actual team pts - E1 team pts), as in #62. λ is chosen by
  leave-one-season-out over the training seasons, per version.
- **Versions:**
  - every non-empty subset of the 12 families: 4,095;
  - x report cutoff {T-60, T-20}. T-20 means the latest report published ≤ 20 min before tip, which the bot could
    use only by betting about 20 min before tip.
  - **= 8,190 versions.**

## Split and selection

- **Training:** 2021-22 + 2022-23. **Validation:** 2023-24.
- **Choose by the lowest validation GAME-TOTAL MAE**, with E1 itself as a candidate. **If E1 wins, STOP.**
- **Also reported:** the best by team-score MAE, the top 20 versions, and how often each family appears in the top 100.
- **Test (once, after an Opus audit with code and input hashes plus a start marker):** 2024-25 + 2025-26, second use,
  disclosed.

## Claims (pre-declared)

- **"E3 is more accurate than E1 on game totals"** only if the paired game-bootstrap 95% CI of (E3 - E1) total MAE,
  pooled over 2024-26, is entirely below 0 (10,000 draws, seed 20261023).
- **The same claim for team-score MAE**, reported separately.
- **Reported regardless:** within 10 points, bias by season; the closing-line reference row.

## Double checks

1. 200 random team-games (seed 20261025): all family columns recomputed by independent code. Exact agreement, or
   explained and amended before selection.
2. No-look-ahead tests (report cutoff, windows, moves strictly before D).
3. Age coverage, and a sanity check on DOB (age 18-45).

## Versions

8,190.

## Amendments

(none)
