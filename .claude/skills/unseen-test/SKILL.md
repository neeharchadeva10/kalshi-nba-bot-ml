---
name: unseen-test
description: Step 4 — out-of-sample test - freeze the rules built on early data, then run them once on later data the strategy never saw, and show both side by side. Use after a strategy passes the honest test.
argument-hint: "<log # or strategy>"
---

# Step 4 — Test on data it has never seen

Idea: $ARGUMENTS

His prompt: "Build the strategy using only 2010-2020. Don't look at anything after 2020. Then test it on 2021-2026,
data it has never seen, and show me both results side by side."

Kalshi history is shorter (most series start in 2023-2026), so split by date:

1. **The split is fixed in the log entry before any test.** Default: everything before a cutoff date is discovery,
   and everything after is unseen. Keep at least 30 distinct dates and 100 trades in the unseen part. If the data is
   too short for that, say so; the final unseen test becomes `/paper-trade`.
2. **Tweaking is allowed only on discovery data.** Filters, thresholds, volatility or trend conditions are all fine
   there. Each tweak is a new version in the log. Once a version is picked, freeze its rules: copy the exact final
   rules into the log entry with the date.
3. **Run the frozen rules once on the unseen period.** No changes after seeing it. If the user wants to change
   something after seeing unseen results, that is a new version. It goes back to discovery, and its unseen test now
   needs data that hasn't been seen yet, which means later dates or paper trading.
4. **Pass bar:** average net per trade above 0 after fees in **both** periods. On the unseen period, the 95%
   lower bound must also be above 0, using a bootstrap over whole dates (markets on the same day move together).
   Use at least 10,000 resamples.
5. **Show side by side:** trades, win rate, net per trade, dollars per week at the real size, worst streak. If
   discovery looks great and unseen is flat, call it luck or overfitting in plain words.
6. Remind the user how many versions were tried to get here (from the log).

PASS: go to `/coin-flip`. FAIL: record it in the log and stop.
