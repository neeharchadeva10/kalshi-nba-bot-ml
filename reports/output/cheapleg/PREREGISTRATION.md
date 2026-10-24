# Pre-registration — research log #33: does the cheap leg of a "fake lock" win more often than its price?

Written 2026-10-21 ~05:00Z by Opus, at the user's direction ("yes test the cheap leg lead"). Changes go in
"Amendments" with a date.

## Where the idea came from (known before writing)

- **The live lock bot, night of 26OCT20** (reports/output/ohio_trial/live_26OCT20/ANALYSIS.md):
  - 29 attempts, 0 complete locks.
  - The cheap side filled 13 times (1-7c). The expensive side filled 3 times.
  - **2 of the 13 cheap fills won** (+$1.93). With fair prices, 2 or more wins has about a 4.8% chance.
- **Nothing else has been looked at.** No other lock's leg outcomes have been examined, including:
  - the 26OCT20 recordings;
  - the lockwatch_night logs;
  - any later night.
- **Prior evidence against:** cheap contracts usually win *less* often than their price (#5 weather long-shot bias).
  The bot's fills may also be adversely selected: the side still there may be the one nobody wanted.

## Question

When the live bot's lock math fires, the lock is usually fake: one leg vanishes. Does buying **one** leg (no lock)
and holding to settlement make money after Kalshi's fee? The main candidate is the cheapest leg.

## Data

- **Order books:** Kalshi WebSocket order-book recordings, read-only, recorder on the home PC.
  - Folders: data/ws/nba/<date>/raw, plus supplement folders such as data/ws/nba/26OCT21b. A ticker appears in only
    one folder per night.
  - Books are rebuilt as in ws_sim / ws_books: snapshots plus deltas.
  - A sequence gap or outage makes a ticker's book invalid until its next snapshot.
  - Recording time (local receipt) is the clock. The feed delay shifts every event equally, so "t + L" in recording
    time is the exchange state L after the event.
- **Results:** each market's result from the public GET /markets/{ticker}, cached.
  - Markets still unsettled 7 days after their game are excluded and counted.
- **Coverage limit:** only tickers listed when that night's recorder(s) started.
  - Markets listed later (for example, second-half lines) are missing. *PROT series are not recorded.
  - Coverage is reported.
- **Source night:** 26OCT20 (BOSDET, PHINYK, OKCSAS) is **reported separately and never counts toward the pass
  bar.**
- **Test games:** every NBA game recorded from 26OCT21 on.
  - Ordered by scheduled start: Kalshi's game time minus 3 h, as in #24's amendment.
  - **Discovery** = the first half of the test games; **unseen** = the rest.
  - The test runs once **at least 30 test games (15 or more unseen) have settled**.

## Signal (the live bot's own lock math, reports/output/ohio_trial/lockcore.py)

- On every book update, the Detector checks the affected locks at **count 1**:
  - **ladder:** YES weak strike at its ask + NO strong strike at 1 - its bid;
  - **me_no:** NO on every market of a mutually exclusive set;
  - **me_yes:** YES on every market of an exhaustive set.
- **A lock event:** a lock's first appearance (lock_open) with profit **>= $0.01 after fees**, which is the live bot's
  minimum.
  - Every leg's book must be valid at that moment.
  - lock_grow events are ignored.
- **De-duplication:**
  - lockcore.dedupe uses a 50 ms window.
  - Then a 60 s refire guard per (leg ticker, side): a leg already bought in the last 60 s is not bought again.
- **Window:** all recorded time. Pre-game (before scheduled start) and in-game are reported separately.

## Which single leg is bought (versions)

- **cheapest:** the lock leg with the lowest price. Ties go to the alphabetically first ticker, then YES before NO.
- **stale:** among the legs other than the trigger, the one whose best price changed longest ago.
  - The trigger is the ticker whose update opened the lock.
- **trigger:** the leg whose update opened the lock.

## Fill and P&L

- **Fill:**
  - An IOC buy of 1 contract at the lock price for that leg, arriving at **t + L**. t is the recording time of the
    message that opened the lock.
  - It fills if that side's best price at arrival (YES ask, or 1 - YES bid for NO) is at or below the lock price, and
    at that better price.
  - Otherwise there is no trade, and it is counted as a miss.
- **L = 10 ms** (the Ohio server's measured reaction, about 9.2 ms) or **40 ms** (home).
- **Fee:** ceil(0.07 * p * (1 - p) / 0.0001) * 0.0001 per contract at count 1.
- **Exit:** hold to settlement. P&L = payout (1 or 0) - price - fee.

## Versions

- Leg {cheapest, stale, trigger} x L {10 ms, 40 ms} = **6 versions**.
- **Primary (named now): cheapest, L = 10 ms.** The other 5 are leads only.

## Pass bar (primary; all must hold)

1. Mean net P&L per fill > 0 in discovery **and** unseen.
2. Unseen 95% bootstrap lower bound > 0. Resample whole games, 10,000 draws, seed 20261021.
3. At least **200 unseen fills**.
4. **Price-matched coin flip:** at most 5% of 500 copies do as well on unseen.
   - Each copy replaces every real fill with a random (time, market, side) from the same game and phase.
   - The random pick must have a valid book and a best price for that side within 1c of the real fill price.
   - It buys at that price plus the fee and holds to settlement. Seed 20261021.
   - This asks whether "being the cheap leg of a lock" adds anything beyond "being a contract at that price".
5. At least **$2 per week** at count 1, projected at 50 NBA games per week.

## Reported regardless

- Fill rate (fills / lock events) per version.
- Win rate vs mean price and mean fee, by price bucket: 1-2c, 3-5c, 6-10c, 11-25c, 26-50c, over 50c.
- By market type (prop, total, spread, quarter/half, ME set), by lock kind, by phase, and fills per game.
- The source night (26OCT20) on its own: all 6 versions, plus whether the live bot's 13 cheap fills are reproduced.
- Coverage: recorded tickers vs all listed, and the number of unsettled exclusions.
- If the server's lockwatch_night logs exist for a test night, the overlap with its lock_open events.

## Amendments

- **2026-10-21 ~06:15Z (Opus), clarifications from the build.** Written after the source-night run. None of these
  changes a rule.
  1. **Scheduled start:** occurrence time - 3 h, falling back to expected expiration - 3 h. This is what the
     #24/nba_drift code actually does; in the 26OCT20 files expected expiration = occurrence + 3 h.
  2. **Refire guard:** "bought" means a filled order. Misses don't block a later attempt. The 60 s runs from the
     earlier event's time.
  3. **Detector universe:** only recorded tickers, as with the live bot's discovery. ME sets with unrecorded members
     can't lock.
  4. **At most 3 legs per lock**, mirroring the live bot's max_legs. The source night had no larger lock events, so it
     is unaffected.
  5. **Coin-flip candidates** may be either side, weighted by true sample counts. Unsettled candidates are dropped.
  6. **Gate:** a game counts once all its fills are settled, or once 7 days have passed since its start.
- **2026-10-21 ~06:40Z (Opus, with the user's answers), after the Opus leak-audit and before any test-night outcome
  was looked at:**
  7. **Run timing (user: "wait for 200 buys"):**
     - A count-only check reads no results, settlements or P&L. After each recorded night it counts the primary's
       simulated fills in the would-be unseen half (games from 26OCT21 on, split by scheduled start).
     - The test runs **once**, on all test nights recorded through the first night where that count is >= 200 and
       every included game meets the gate (amendment 6, applied literally: all primary fills settled, or 7 days or
       more since the game).
     - The gate is checked from metadata **before** any statistic is computed or printed.
  8. **Primary latency is now L = 40 ms** (user's choice). Reason: on the 26OCT20 source night, the 10 ms replay
     filled 7 cheapest legs that the live bot really missed, while 40 ms matched the live bot 22 of 23 times.
     - 10 ms stays a reported version; the version count is unchanged at 6.
     - **New primary: cheapest leg, L = 40 ms.**
     - The live-vs-replay 2x2 is reported for every test night the live bot ran.
  9. **Cross-connection locks** (user's choice): lock events whose legs come from different recorder connections
     (folders) are **excluded from the primary** and reported separately. This affects 26OCT21 (three folders). From
     26OCT22 one recorder per night is used.
  10. **\*PROT series** are recorded from 26OCT21 (folder 26OCT21c, the user's choice) and are part of the universe.
      Their market_type label is "prop" (lockcore's mapping wrongly gives "ot").
  11. **De-dup representative:** the **earliest** lock in each 50 ms chain, as the live bot fires on the first
      lock_open. The most-profitable-lock variant is reported.
  12. **Fills need depth:** cumulative size of at least 1 contract at or better than the limit at arrival.

