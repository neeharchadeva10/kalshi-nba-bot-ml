---
name: log-idea
description: Step 6 — log everything. Append a new idea (rules, date, data split, pass bar) to the append-only research log BEFORE it is tested, add versions as variants are tried, and record results. Use whenever a new trading idea, variant or result comes up.
argument-hint: "<idea, or '#<n> result: ...', or '#<n> version: ...'>"
---

# Step 6 — Log everything

His prompt: "Create a research log. Before you test any idea, write it down with the rules and the date. Count every
version you test. Never edit or delete anything in it. Remind me how many versions we tested whenever we find a
winner, so I don't fool myself."

The log is `docs/RESEARCH_LOG.md`. Input: $ARGUMENTS

## Rules

- **Append only.** Never change or delete existing text. Corrections and results are new dated lines under the
  entry, or a new entry that points to the old one.
- **New idea:** append a `### #<next n> — <name> — <today>` block using the template at the bottom of the file:
  - idea, who loses money to it, exact rules,
  - data and the discovery/unseen split,
  - the pass bar, written before any data is looked at,
  - versions = 1, status = logged.

  For a full study, also write `reports/output/<study>/PREREGISTRATION.md`, the way the weather and NFL studies did.
- **New version** (any filter, threshold, time, bucket or parameter tried): add a dated line under the entry with what
  changed, and increase its version count. Update the running total at the top. This is the one place a number above
  the "New entries" line may change.
- **Result:** add a dated line with the step, PASS or FAIL, the key numbers in dollars, and the report path. Update the
  status line by adding a new dated status line, not by editing the old one.
- **Whenever anything passes a step,** say in the reply: "This passed after N versions of this idea (M total across
  the project)." The more versions, the more likely a pass is luck.
- Before logging, check whether the idea or something close is already in the log or the `kalshi-edge-findings`
  memory, and say so.
- Also update the `kalshi-edge-findings` memory when an idea finishes, pass or fail.
