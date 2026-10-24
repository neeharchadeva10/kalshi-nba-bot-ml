# Notes by topic

The neat, topic-organized index of everything we've done. **Update this file as topics change.**

The full, dated record is docs/RESEARCH_LOG.md (append-only, by date and idea number). Study folders are under
reports/output/.

Last updated: 2026-10-23.

---

## 1. Predicting NBA final scores (current focus)

**Goal (the user):** "get as close as possible to predicting the total score of a game", using our own stats, not
betting lines.

**Best model so far: "E1" (#59).** It averages two predictions:
- a tuned version of the user's own averaging formula: recent games count more, last season blends in early, and it
  adjusts to league scoring;
- a statistical model that learned from 11 seasons (2008-19): each team's offence, defence, pace, rest, home court,
  back-to-backs.

**How good it is,** tested once on 4,923 games it had never seen (2022-26):

| | New model E1 | The user's formula (#38) | Sportsbook line (reference only) |
|---|---|---|---|
| Average points off on the total | **14.8** | 15.1 | 14.3 |
| Total within 10 points | 40.9% | 40.1% | — |
| Picks the winner | 65.6% | 64.8% | — |

- E1 is better than the user's formula in **every** season (0.2-0.5 points per game). That isn't luck: the likely
  range never crosses zero.
- **No model can get much below about 14 points off.** Overtime games (about 23 off) and wild shooting nights can't be
  seen before tip.
- **Files:** reports/output/nba_score_model/ (frozen.json = the locked model, results_test.json = the results).
- **Against the betting line (#60):** no edge.
  - When the model disagrees with the line, it's right about 50% of the time (2,000 games).
  - On Kalshi it would have lost about 5 cents per bet.
  - Mixing the model into the line never helps.
  - The line already knows everything the model knows. Use the model for predicting scores, not for betting against
    the line.
- **Live tracking:** every game's prediction is locked before tip and scored after the final, on the dashboard's
  SCORE MODEL panel (logs/score_model/).
- **E2, injury-aware (#62):** knowing who's out made each TEAM's predicted score better (9.44 → 9.35 points off), but
  the GAME TOTAL didn't improve at all (14.89 vs 14.90).
  - A missing scorer mostly shifts points between the two teams; it barely changes the total.
  - So injuries aren't what separates us from the line on totals.
- **E3, everything a missing player does (#64):** points, minutes, rebounds, assists, 3s, steals/blocks, usage,
  plus-minus, role tier, age, questionable and new players, with all 8,190 combinations tested.
  - Again: better at each TEAM's score (9.44 → 9.35), no better on the game TOTAL (14.90 vs 14.89).
  - The best mix was assists + 3-pointers + role tier + age + questionable players.
- **Assumptions audit (#63):**
  - **Overtime** (about 5% of games, +21 points) is the one big blind spot; it is unpredictable.
  - **Small issues:** early season, bad teams, some arenas, year-to-year scoring level. Each is worth at most about 0.03
    points; together about 0.1.
  - **Judgment calls** like rotation cutoffs, form windows and how questionable players are treated barely matter. The
    "key player" definition matters only for how we slice results.
  - **Late news:** 1 in 5 teams gets a new Out or Doubtful name in the last hour before tip, which the line sees and our
    1-hour-before snapshot doesn't.
  - **Real fills** paid exactly the listed price, so our 1c slippage assumption is on the safe side.
  - **OT history:** 59 / 60 / 54 OT games in 2023-24 / 2024-25 / 2025-26 (about 4.7%).

## 2. The points formula vs betting lines (#37-#42, #56-#58)

- The user's reel formula, its fixed version, 26,880 combinations, a player/injury version, a "where is the line
  wrong" model over 18 seasons, and earlier Kalshi lines were all tested with fees.
- **Result:** nothing beats the betting line. The line is about 0.5-1.5 points more accurate than any formula, and when
  a formula disagrees with it, the line is right slightly more often.
- **The live bot's formula** leans low some seasons and high in others (-4.7 in 2025-26, +1.5 in 2024-25).
- **#61, the shape of Kalshi's line ladder:** Kalshi also gets the SPREAD right. It expects totals to vary by about ±18.6
  points, and they really do. The far-out lines are priced fairly, and all 8 tail bets lost after fees.
- **Folders:** nba_totals_pace, nba_totals_tourney, nba_totals_grid, nba_formula_accuracy, nba_line_error,
  nba_early_line.

## 3. Live test, bot and dashboard

- **The 2-week live test** (Oct 21 - Nov 3): 1 contract per game, the bot places it, Discord gets the pick and a
  nightly report.
  - Night 1: 4-3.
  - Night 2: 2-0. CLE@PHI under (final 220 vs 237.5) and DEN@OKC under (final 180 vs 236.5, a freak low game).
  - **After 2 nights: 6-3, +$1.37 after fees.**
    - All 9 games went under the line: unders 6-0, overs 0-3.
    - The price we got was no better than the market's (average -0.5c), so so far this is low-scoring games plus the
      formula's lean toward unders, not a proven edge.
- **Refining the strategy (#65): both refinements FAILED on new seasons.** reports/output/nba_refined_totals/RESULTS.md.
  - **A** (the injury/roster-aware model, lean fixed, skip rules) was +9c on old seasons, then **-9c** on 2024-26 and
    -0.8c on Kalshi.
  - **B** ("scorer sits → bet the over") was +12c on 2020-21, then **-7.6c** on 2024-26 and -5.8c on Kalshi. It has
    faded every season.
  - **The live bot's rule on 2024-26 is exactly 50% on 1,498 games**, about -2.5c per Kalshi bet after fees. The 6-3
    start is luck.
  - Nothing is paper-tracked.
- **Bot:** desktop START / STOP buttons. The user starts it; Claude never does. Code: reports/output/early_overs_bot/.
- **Dashboard:** http://127.0.0.1:8768. It syncs with Kalshi every 30 s (read-only) and switches to each new night at
  6 AM ET.
  - Start: `.venv\Scripts\python.exe scripts\live_scores_dashboard.py`.
- **Every real trade:** logs/trade_ledger.csv and logs/bet_journal.csv.

## 4. Every other strategy tested (all failed after fees)

| Area | Ideas (log numbers) | Why they failed |
|---|---|---|
| NBA in-game | #46 early swings, #51 late-game fouling, #52/#55 far-out lines, #35 late favourites | Live lines are fair; late books too wide |
| NBA pregame | #24 timing, #44 injury speed, #45 roster turnover, #47 favourite/long shot, #54 line moves | News is priced before it's public; moves don't continue |
| NBA props | #48 star out, #53 resting orders | Props adjust; resting orders rarely fill (and the good ones don't) |
| NBA cross-market | #49 team / 1st-half totals vs game total | Thin books, 14-16c spreads |
| NFL | #1, #18-#20, #24, #47, #50 | Priced right |
| Weather / crypto / market making | weather #2, BTC 15m, MM | Edge smaller than costs |

**Still open:** the Kalshi rewards program (LIP). It is paper-tested, with the verdict about Nov 2.

## 5. Data and checks

- **Box scores:** hoopR (free, ESPN-based), 2008-2027. Final scores match Kalshi's settlements 578/578.
- **Sportsbook lines:** odds-API consensus close (2020-26) and OddsData (2007-25, private).
  - OddsData has typos.
  - Its line timing is unclear before 2022.
- **Injury reports:** official PDFs from Oct 2021.
- **NBA.com** blocked our requests, so that cross-check wasn't done.
- **Every study:**
  - rules are written down first;
  - every version is counted;
  - the code is checked by a second, independent version and a leak audit;
  - the final test is run once on unseen games.

## 5b. Rosters, trades and player value

- **Current rosters:** docs/ROSTERS_CURRENT.md, refreshed daily at 9 AM ET, from the free hoopR data. The roster file,
  game sheets and box scores agree.
- **Every player move since 2008:** docs/PLAYER_MOVES.md (in-season and offseason, with dates). Big recent trades were
  checked against the news.
- **Key trades, last 3 seasons plus the 2026 offseason,** with news links: docs/TRADES_LAST_3_SEASONS.md.
- **24/7 trade watcher:** checks the RealGM/ESPN/CBS news feeds every 10 minutes and shows moves in the dashboard's
  TRADE WIRE panel. Each move is confirmed by the next day's roster data.
- **Bench upgrades and roster changes this season:** docs/ROSTER_CHANGES_2026_27.md (who came in and out of each
  team, with last season's production).
- **24/7 injury watcher:** reads the official NBA injury report (every 15 min on game days) plus news headlines every 10
  minutes. The dashboard's INJURY WIRE panel shows new listings, status changes, LATE changes near tip, and flags
  your bot's games.
- **How the books value a missing star:** about 1 point of SPREAD per 10 points-per-game missing, which is right. They
  cut the TOTAL about 0.9 per 10 PPG while reality is about 0.4. That is a possible lead, not proven.

## 6. Ground rules

- Research only reads data. Real orders are placed only by the user's bot or by the user.
- Fees **and** slippage are always included when money is involved.
- Results are reported truthfully, even when bad.
- Claude is not a licensed financial advisor.
