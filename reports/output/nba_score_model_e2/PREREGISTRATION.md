# Pre-registration — research log #62: E2 = the #59 score model plus who is out tonight (official injury reports)

Written by Opus on 2026-10-23 at 02:32Z. The user: "okay lets try e2". Accuracy only, own data only (no betting lines
as inputs). Changes go in "Amendments".

## Why (the diagnosis, 2022-26, already-seen data, descriptive)

E1 is about 0.5 points per game less accurate than the closing line even when every key player plays, and about 0.7
when 1-2 key players (28+ mpg) sit. In big E1-vs-line disagreements with a key player out, E1 was too high 89% of the
time: it doesn't know who is missing. E2 adds that knowledge.

**Expected gain:** about 0.2 points per game, at most.

## Data (free; cached; GET only)

- **The frozen E1:** reports/output/nba_score_model/frozen.json (d9866fc5...). Its per-team predictions come from the
  #59 code, unchanged.
- **Official NBA injury reports:** data/raw/nba_injury (PDFs from 2021-10-19), parsed in
  reports/output/nba_model/injury_parsed_v3.json. Input for a game = the latest report published at or before
  D = T - 60 min (T = hoopR scheduled tip). "Not yet submitted" = no information.
- **hoopR player_box** 2021-22..2025-26 (files *_2022.._2026), plus player_box_2021 (2020-21, downloaded from the hoopR
  GitHub raw release, GET, cached) for early-2021-22 fallbacks only.
- **Name matching:** report name + team → hoopR athlete. Unmatched names are counted per season and contribute 0.

## Features per team X in game g (strictly before g's ET date; injuries as of D)

"Rotation-quality" = averaged ≥ 15 minutes over his last 10 games played (current season; if fewer than 3 this season,
last season's mpg).

| Feature | Definition |
|---|---|
| out_pts_X | Σ over X's rotation-quality players listed OUT or DOUBTFUL of their points per game over their last 10 games played (if fewer than 3 this season, last season's PPG) |
| q_pts_X | The same for players listed QUESTIONABLE |
| back_pts_X | Σ over X's rotation-quality players NOT listed OUT/DOUBTFUL who appeared in fewer than 5 of X's last 10 team games (returning from absence), of their PPG over their last 10 games played |
| out_min_X | Σ mpg of X's OUT/DOUBTFUL rotation-quality players / 240 |

The same four features are also computed for the opponent Y.

## Model

- Residual layer on the frozen E1. Target r = actual X points - E1 predicted X points.
- Ridge regression (intercept + features), λ chosen by leave-one-season-out CV inside training.
- E2_X = E1_X + r̂; E2 total = E2_X + E2_Y.

| Variant | Features (own + opponent) | Count |
|---|---|---|
| A | out_pts | 2 |
| B | out_pts, q_pts | 4 |
| C | out_pts, q_pts, back_pts, out_min | 8 |

**3 versions.**

## Split

- **Training:** 2021-22 + 2022-23 regular season.
- **Validation:** 2023-24. Choose among A, B, C and E1 itself by total MAE. **If E1 is best, STOP** (no E2).
- **Test (once, after an Opus leak audit with code and input hashes and a start marker):** 2024-25 + 2025-26 regular
  season.
  - **Disclosed:** E1's own accuracy and lines on these seasons have been seen (#59, #60, the diagnosis). E2 has not.
- **Forward:** if E2 is frozen, it is added to the live tracker (locked before tip) next to E1 and B1. Forward 2026-27
  games are the final word.

## Claims (pre-declared)

- **"E2 is more accurate than E1"** only if the paired game-bootstrap 95% CI of (E2 total MAE - E1 total MAE), pooled
  over 2024-26, lies entirely below 0 (10,000 draws, seed 20261023).
- **Reported regardless:**
  - E2 vs the sportsbook close (reference only; never an input);
  - the split by key-players-out (0 / 1 / 2+);
  - within-10 rate, team MAE and bias, by season.

## Double checks

1. 200 random team-games' injury features recomputed by independent code (no shared code), agreeing exactly.
2. No look-ahead tests: reports after D ignored; games on or after the ET date ignored.
3. **Report sanity, multi-source:** the share of OUT-listed rotation players who did not play that game (box DNP or
   absent). Expected about 99%. Mismatches listed.

## Versions

3.

## Amendments

(none)

- **2026-10-23 02:45Z (Opus), readings fixed before any outcome is analysed** (after the independent checker's report).
  Both implementations must use these:
  1. **Report time** = the PDF CreationDate (the actual publish time). The #59 audit found it 0-45 min AFTER the file-name
     time, never earlier, so this is the no-look-ahead choice.
  2. **Which report:** the latest report with CreationDate ≤ D that lists the GAME (matchup). Within it, a team with no
     rows has no listed injuries, so its features are 0. A report that doesn't list the game is skipped (look back at
     most 72 h).
  3. **Fallback** for fewer than 3 games this season: last season's last 10 games played (not the full season).
  4. **back_pts** uses the current season only. It is 0 until the team has played 10 games this season. This avoids
     counting traded-in players as "returning" at season start.
  5. **A player's team** = the team of his most recent regular-season box row before D (this season, else last
     season).
  6. **History** is regular season only. A 0-minute row counts as not played.
  7. Versions, seasons and claims are unchanged.

- **2026-10-23 02:51Z (Opus), second readings amendment.** The agreement check FAILED on 3 explained classes. Still before
  any outcome is analysed. Both implementations must use these exact rules:
  1. **Report set:** the union of reports/output/nba_model/injury_parsed_v3.json and
     reports/output/nba_teammate_out/injury_parsed_tm.jsonl (covers 2025-11..2026-06 reports v3 lacks), deduplicated
     by PDF name. CreationDate comes from the PDF bytes.
  2. **Name matching:**
     - Normalise: lowercase; strip accents, apostrophes, periods and hyphens; drop suffixes jr/sr/ii/iii/iv; collapse
       spaces.
     - The report's "Last, First" becomes "first last". Match it EXACTLY to the normalised full display name of players
       whose team (rule 5 of the first amendment) is X.
     - No exact match, or 2+ matches → unmatched (contributes 0). Never match on surname alone.
  3. **Windows:**
     - "Last 10 games played" = his most recent ≤ 10 regular-season games with minutes > 0 in the CURRENT season,
       before D. If he has 3-9 such games, use those (seasons are not mixed).
     - If he has < 3, use his last 10 games played last season.
     - PPG = the plain mean of points over the window; mpg = the mean of minutes over the same window.
     - Rotation-quality = mpg ≥ 15 over that window.
  4. **back_pts** counts only players who:
     - played for X at least once this season (a box row with minutes > 0 for X before D);
     - appeared in fewer than 5 of X's last 10 team games;
     - are rotation-quality;
     - are not listed OUT or DOUBTFUL.
     Players with no appearance for X this season are excluded, because their availability is unknown.
  5. **Unchanged:** a team with no rows in the chosen report → 0 for all four features; versions, seasons and claims.

- **2026-10-23 02:53Z (Opus), clarification of rule 4 (back_pts) before any outcome:** candidates must also have X as their
  current team: the team of their most recent regular-season box row before D is X. A player traded away from X, or
  whose latest row is for another team, never counts for X. All other rules are unchanged.
- **Same clarification for out_pts, q_pts and out_min:** a listed player counts for X only if his current team (same
  rule) is X. This is already the matching rule (2): players are matched among those whose team is X.

- **2026-10-23 02:57Z (Opus), All-Star rule before any outcome:** box rows whose team is not one of the 30 NBA franchises
  (All-Star / Rising Stars / exhibition teams such as WORLD, USA, CHK, LEB, GIA, DUR, STE, CAN, KEN, SHQ, STARS, STRIPES,
  EAST, WEST) are excluded everywhere:
  - from "current team" (latest box row);
  - from every player window;
  - from team-game counts.
  This matches #59, which dropped them.
