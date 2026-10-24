# Pre-registration — research log #27: can a server next to Kalshi reach the resolution locks? (read-only trial)

Written 2026-10-20 ~20:05Z by Opus, after the user approved the trial ("yes lets do it"). **No measurement from the
server exists yet.**

What was known when this was written:
- From home: reaction is about 30 ms typical (feed 19.6 ms median plus about 9 ms send).
- BOSDET produced 30 locks, all lasting under 30 ms. 15 lasted 10 ms or more, worth $10.53 at shown sizes, or about
  $7.50 de-duplicated, mostly one player-prop rebound threshold. PHINYK and OKCSAS had 0 so far.

This trial is **read-only**: no orders. It decides only whether a trading bot is worth building. It can't show
whether locks fill, since only real orders can show that.

## Setup

- One AWS EC2 instance in **us-east-2 (Ohio)**, which hosts Kalshi's WebSocket and trading servers. Run by the user's
  AWS account, with a separate, revocable Kalshi API key for the server.
- On the server, run the read-only all-games lock watcher (`lockwatch_night.py`) for every NBA game night and the
  latency measurement (`reports/output/latency/measure.py`).
- The same watcher runs at home on the same nights, for comparison.

## Measurements

- **Server reaction time R:** the server's WebSocket feed delay (exchange ts_ms to receive, corrected by an SNTP
  offset) plus half the REST round trip to Kalshi from the server.
  - Report the median and p90 per night, then the median across nights.
- **Locks:** the same detection math and fees as lockwatch.py, at count up to 10.
  - **De-duplication:** locks that share any leg and open within 50 ms of each other form one event; the event's
    value is its single most profitable lock.
  - **Reachable** = the lock lasted at least R_median + 2 ms, as observed by the server.
- **Value per game night** = the sum of reachable de-duplicated event values, after fees.

## Decision rule (all must hold, over at least 5 NBA game nights with at least 25 games)

1. Median server reaction R <= 10 ms.
2. Mean reachable de-duplicated value >= **$3.00 per game night**.
3. The value isn't one event: excluding the single biggest event of the whole trial, the mean is still >= $2.00 per
   night.

**Scope:**
- The rule is applied to **all non-prop locks**, plus **prop locks only if the user says before the first server
  night that the bot may trade props.** Both versions are reported either way.
- Decided 2026-10-20 ~20:10Z: the user hasn't answered yet, so the default scope is non-prop. A user decision on
  props, made before the first server night, replaces this line through a dated amendment. **Superseded by the
  21:20Z amendment: props included.**

**If it passes:** build the live lock bot (micro size, $3 loss cap, post-trial pre-registration, the user's go-ahead
past Claude Code's safety check). **If it fails:** shut the server down.

## Reported regardless

- Server vs home: feed delay distribution, and the same locks seen by both with their lifetimes from each vantage.
- Lock counts and value by market type (props, race, quarter, total ladders, spread ladders) and by lifetime bucket.
- Server cost to date.

## Amendments

**2026-10-20 ~21:20Z — scope decided by the user: "props yes".**
- The decision rule applies to **all locks, player props included**. Non-prop results are still reported.
- Timing:
  - Night 1 (26OCT20) watchers started at 20:52Z, before this decision.
  - No lock result from the night-1 logs had been looked at when the user decided. Only feed-delay and health
    statistics had been viewed.
  - The user had seen the pre-trial BOSDET per-game numbers (most value in one prop), which were known when this
    pre-registration was written.
- So the decision counts as made before the trial's lock data. Night 1 is included.
