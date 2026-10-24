---
name: coin-flip
description: Step 5 — the coin-flip test - compare a strategy against 500 copies of itself with the same number of trades and the same exits but random entries, and report what percent of the random versions did better. Use after the unseen test passes, or whenever a result might be luck.
argument-hint: "<log # or strategy>"
---

# Step 5 — Beat a coin flip, or it doesn't trade

Idea: $ARGUMENTS

His prompt: "Test my strategy against 500 versions of itself that use the same stop-loss and the same number of
trades, but random entries. Tell me what percent of the random versions did better. Be honest, even if it's bad news."

## Kalshi version

1. **Random copies (500, fixed seed, in the log):** each one keeps the strategy's
   - number of trades per day (or per window),
   - markets universe and time-of-day pattern,
   - exit rule (hold to settlement, target or bail price),
   - size, fees and fill rules (the same honest-test engine),

   but picks entries at random: a random eligible market or minute, and a random side (YES or NO). Spell out exactly
   what is randomized. If the strategy's whole point is the price bucket, also run a variant that keeps the bucket and
   randomizes only the timing.
2. **Run on the same data as the result being checked:** discovery and unseen separately.
3. **Report:** the strategy's total net, the random distribution (median, 5th and 95th percentile), and the **percent
   of random versions that did as well or better**.
4. **Pass bar:** at most 5% of random versions do as well or better on the unseen period. Otherwise the entries
   aren't the reason it makes money. Say that plainly.
5. Note for binaries held to settlement: random entries usually lose about the spread plus fee. A strategy that only
   loses less than random still loses money, and fails anyway at step 4.

## How

There is no shared coin-flip helper yet. The first time this runs, Sonnet should build a reusable
`kpm.analysis.coinflip` (taking any strategy's entry list plus the honest-test engine) with tests, and Opus reviews it.
Running the coin-flip test doesn't add a strategy version; record the run, its seed and the result in the log entry.

PASS: go to `/build-team` (if there are 2 or more survivors) or `/paper-trade`. FAIL: record it in the log and stop.
