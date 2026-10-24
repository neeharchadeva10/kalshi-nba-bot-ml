# Pre-registration — research log #65: the refined totals strategy (rosters, injuries, key/role players considered)

Written by Opus on 2026-10-23 at 05:50Z, before any data for this test was examined. Changes go in "Amendments".

**The user:** "refine the strategy again with all these considered".
- They chose "Both, one honest test":
  - Part A: rebuild the bot's pick rule with the roster/injury-aware model, a lean fix and skip rules.
  - Part B: test "star sits → bet the over".
- Then "Paper-track now, switch Nov 4": a refined rule that holds up is paper-tracked next to the live #40 v5. The
  live rule stays unchanged through 2026-11-03 (the user's decision of 2026-10-22). At the ~Nov 4 review, the user
  decides whether anything replaces it.
- **Claude never changes or starts the live bot** for this.

## Known before writing (disclosed)

- **#60:** when E1 disagrees with the sportsbook close (2022-26), it is right about 50% of the time at every gap size.
  On Kalshi 2025-26 it lost about 5c per bet. So Part A is expected to FAIL unless the lean fix or the skips change
  that.
- **#62/#64:** injury and roster features improve each TEAM's predicted score, but not the game TOTAL.
- **#56:** the live #42 formula leans -4.0 points in 2025-26 (+1.5 in 2024-25).
- **Descriptive note (2026-10-23 03:56Z, the Part B lead):** on 2021-22..2025-26 sportsbook closes, books cut the
  total about 0.87 per 10 PPG of fresh absence, while real scoring fell about 0.39 (about 2.8 se).
  - This was found on ALL seasons that have injury reports, so 2021-26 is not clean for Part B.
  - **2020-21 was NOT used** for that question. Its lines were used only by earlier, unrelated formula studies.
- **Expected size if the lead is real:** about +1 point per game for 20 PPG out, which is about +2% win rate. That is
  roughly break-even against Kalshi's cost (about 52.75c per 50c contract with fee and 1c slippage). Bigger absences
  would give more.

## Data (free, cached, GET only; no order or portfolio endpoints)

**Lines and finals**
- **Sportsbook consensus close:** data/raw/nba_odds_samples/closing_lines_odds_api.parquet.
  - Drop snapshot ≥ commence and over_under outside (180, 275). Regular season only, matched to hoopR game_id.
- **Finals:** hoopR team_box (verified against Kalshi 578/578).
- **Kalshi 2025-26:** #42's reports/output/nba_totals_grid/grid_bets.py main-strike table at T-1h, unchanged:
  - main strike = YES mid nearest 0.50;
  - entry = taker ask + 1c slippage + fee 0.07·P·(1−P) rounded up;
  - 1 contract, held to settlement.
  - The Kalshi "line" for the gap rule is the T-1h implied median, as in #60's kalshi_bets.

**Injury, roster and player data**
- **Official injury reports:** #62's report set and fixed rules:
  - the union of the two parsed sets, with CreationDate as the publish time;
  - the latest report ≤ T-60 that lists the game;
  - exact-name matching among current-team players;
  - All-Star rows excluded.
- **E3 decision-time features:** reports/output/nba_score_model_e3/e3_features.parquet (c60 columns), used for the
  skip flags.
- **Player box:** hoopR player_box 2021..2026 (files *_2021 = 2020-21 … *_2026 = 2025-26).

## Models (per game, predicted TOTAL)

| Model | Definition |
|---|---|
| F42 | The live formula: the frozen #42 search winner, computed for each season by #56's fa_lib season-generalised grid, unchanged |
| E1 | The frozen #59 model (frozen.json d9866fc5…): the home prediction plus the away prediction |
| E3 | E1 plus the frozen #64 residual (frozen_e3.json 7dca7ef4…) for 2023-24 onward. For 2022-23 it is CROSS-FITTED: the same 18 features with λ = 1000, ridge and standardisation fitted on 2021-22 only, because the frozen fit used 2022-23 |

**Lean fix (L):**
- `none`; or
- `rolling`: model + b, where b = mean(actual total − model total) over all league regular-season games of the same
  season with an ET date strictly before the game's ET date. It is used only once there are ≥ 30 such games; before
  that, b = 0.

## Part A — the refined pick rule (bet the model's side)

- **Bet rule:** bet OVER if model_adj − line ≥ G, UNDER if line − model_adj ≥ G, else no bet.

**Skip rules (any subset of 3):**

| Skip | Rule |
|---|---|
| Q | Either team has ≥ 1 rotation-quality player listed QUESTIONABLE at T-60 (n_q_rot_c60 ≥ 1, E3's ≥ 10 mpg definition) |
| NEW | Either team's share of last-10-game minutes played by players new to it this season is ≥ 0.30 (new_share_*_c60) |
| EARLY | Either team has played < 10 regular-season games this season before the game |

- **Grid:** 3 models × 2 lean fixes × G ∈ {2, 4, 6, 8} × 8 skip subsets = **192 versions**.
- The live rule (#40 v5 = F42 / none / G 4 / no skip) is one of them, and is reported in every window.

## Part B — "star sits → bet the over"

For team X in game g:
1. **Current team:** the player's most recent regular-season box row of this season before g is for X.
2. **Windows:** X's last 10 regular-season games before g, needing ≥ 5 team games this season.
   - His minutes per TEAM game (DNP or absent = 0) ≥ M.
   - fresh_ppg = his points per team game over the same window (DNP = 0).
3. **Fresh:** he had minutes > 0 in at least one of X's last 3 games before g.
4. **Absent tonight:** this depends on the flavor.
   - **report flavor** (decision-time; used for selection, H2-Kalshi and forward): listed OUT or DOUBTFUL for X in the
     T-60 report (#62 rules).
   - **box flavor** (after the fact): no box row with minutes > 0 in g. Used for H1 2020-21, which has no official
     reports, and as a cross-check.

- S_X = Σ fresh_ppg over X's absentees.
- **Scope:**
  - `game` means S_home + S_away ≥ τ;
  - `team` means max(S_home, S_away) ≥ τ.
- **Bet:** OVER at the line.
- **Grid:** M ∈ {20, 28} × τ ∈ {10, 15, 20, 30} × scope ∈ {game, team} = **16 versions**.

## P&L (always including costs)

- **Sportsbook:** flat −110.
  - A win pays +0.90909 per 1 staked, a loss costs −1.
  - A push (integer line) is a bet with P&L 0. Break-even is 52.38% of decided bets.
- **Kalshi:** the net per contract from the #42 machinery: the 1/0 payout minus (ask + 1c) minus the fee.

## Windows

| Window | Seasons | Use |
|---|---|---|
| D-A | 2022-23, 2023-24 (sportsbook close) | Part A selection |
| D-B | 2021-22, 2022-23, 2023-24 (sportsbook close, report flavor) | Part B selection |
| H1 | 2020-21 (sportsbook close, box flavor) | Part B only. Unseen for this question |
| H2-sb | 2024-25, 2025-26 (sportsbook close) | Both parts. SECOND LOOK, disclosed (#60 and the descriptive lead used these closes) |
| H2-K | Kalshi 2025-26 at T-1h | Both parts. Real prices and costs |

## Selection (D only)

- **Part A:** the highest mean P&L per bet among versions with ≥ 150 bets in D-A.
- **Part B:** the highest mean P&L per bet (report flavor) among versions with ≥ 80 bets in D-B.
- **Ties:** more bets wins.
- **STOP rule:** if a part's chosen version has D mean P&L ≤ 0, that part STOPS. No holdout is run for it.
- **Reported:** every version's D row (CSV), the top 20, and the live rule's row.

## Verdicts (pre-declared, per part, on the chosen version, run ONCE)

**PASS** needs all three:
1. **Sportsbook holdouts:** pooled mean P&L per bet > 0, with the game-bootstrap 95% lower bound > 0 (10,000 draws,
   seed 20261023). The pool is H2-sb for A, and H1 + H2-sb for B.
2. **Kalshi:** H2-K mean net per contract > 0 with ≥ 30 bets.
3. **Luck test:** < 5% of 500 random copies do as well on the pooled sportsbook holdout (seed 20261024).
   - Part A copies: the same games with random sides.
   - Part B copies: the same number of OVER bets per season, on random eligible games of that season.

**PROMISING:** not a PASS, but every holdout point estimate > 0 after costs, and the luck test < 10%.
- The holdouts are H1 (B) and H2-sb, each separately, and H2-K when it has ≥ 10 bets.

**FAIL:** anything else.

**What follows:**
- PASS or PROMISING versions get free forward paper tracking from the next game night, next to the live #40 v5. It
  goes to the dashboard and log only, never Discord.
- PROMISING is labelled as weak evidence.
- Real money changes only by the user's decision at the ~Nov 4 review. The Hybrid policy's 4+ weeks / 100+ bets
  verdict rule still applies before any size increase.

## Double checks (before selection)

1. **Independent implementation** (a check/ folder sharing no code with main/):
   - B's absentee features, both flavors, on 200 random team-games (seed 20261026);
   - the lean-fix b on 50 random (season, date) pairs;
   - the Q/NEW/EARLY flags on 200 random games;
   - bet counts and mean P&L in D for 10 random versions.
   They must agree exactly, or the difference is explained and amended before selection.
2. **Reproduction gates:**
   - F42 reproduces #56's repro_check (#42 on 2025-26).
   - E1's 2022-26 total MAE is 14.79634 (4,923 games).
   - E3 frozen's 2024-26 total MAE is 14.903 (#64).
   - The H2-K machinery fed E1 at G 4 reproduces #60's stored net_g4 per game. These are already-published numbers,
     compared silently with pass/fail only.
3. **No-look-ahead unit tests:**
   - reports after T-60 are ignored;
   - game g's own box rows are excluded from every window;
   - the lean fix uses strictly earlier dates;
   - the current team uses rows before g only.
4. **Multi-source:**
   - the share of box-flavor absentees in D listed OUT/DOUBTFUL at T-60, and at the last report before tip;
   - Kalshi T-1h median vs close on 2025-26 (median |diff|).
5. **Rehearsal:** the holdout code paths run on D first.

## Order

1. Build main/ and check/.
2. Reproduction gates and agreement.
3. Selection on D.
4. Rehearsal.
5. Opus leak audit: AUDIT_OK with SHA-256 of code and inputs.
6. Started marker.
7. Holdouts run once.
8. Log the results, even if bad.

## Versions

192 (A) + 16 (B) = **208**.

## Amendments

(none)

- **2026-10-23 05:58Z (Opus), amendment 1, before selection.** No holdout outcome has been seen.
  - **Prompted by:** the independent checker reports that F42 (#56's fa_lib generalisation of the #42 winner) is
    mean(actual − F42) = +15.7 in 2022-23, versus +5.0 / −1.5 / +4.7 in 2023-24 / 2024-25 / 2025-26.
  - **Cause (structural):** fa_lib fits the 2022-23 constants on 2021-22 with NO prior-season blend, because 2020-21
    was not on disk when #56 ran. The live formula always has prior seasons, so F42 in 2022-23 does not behave like the
    rule the bot uses.
  - **Disclosed:** before this amendment, Opus saw the checker's discovery-period (2022-24) results for 10 sampled
    Part A versions. All but two were negative; the best were +0.023 on 98 bets and +0.006 on 999 bets. The amendment is
    structural, not outcome-driven, but it is a degree of freedom used after seeing discovery numbers.
  1. **F42's discovery window D-A is 2023-24 only.** E1 and E3 keep 2022-23 + 2023-24. All other rules are unchanged,
     including the ≥ 150-bet floor (which now applies to F42 on one season).
  2. **Readings fixed for both implementations** (the checker's README readings 1-9):
     - Current team = the latest box row of this season strictly before g's tip, a DNP row included, with no
       last-season fallback.
     - mpg and fresh_ppg = his minutes / points in rows for X over the window games, divided by the number of window
       games.
     - Report flavor: a team with no rows, or a game with no report, gives S = 0. A matched OUT/DOUBTFUL name counts
       only if he is also a Part B candidate.
     - Lines use 180 < line < 275.
     - The rolling lean fix averages ALL league games of the season dated before the game, including games without a
       line.
  3. **Extra agreement check:** the 4 Part B versions in sample_versions_B.csv (seed 20261027) are also compared on
     discovery bets and mean P&L.
