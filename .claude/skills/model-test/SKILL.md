---
name: model-test
description: Step 3m — test a prediction model against the market BEFORE testing any betting rule. The model's probabilities must be at least as accurate as the market price at the decision time (Brier/log loss, calibration), and its disagreements with the market must actually predict outcomes. Use for any model that outputs win probabilities (NBA, NFL, weather, props).
argument-hint: "<log # or model>"
---

# Step 3m — Is the model better than the market?

Model: $ARGUMENTS. It must already be in `docs/RESEARCH_LOG.md`, pre-registered. If it isn't, run `/log-idea` first.

**Why this step exists:** the market price is already a probability. A betting rule can only make money if the
model knows something the price doesn't, by more than the spread plus the fee. Models #29, #31 and #32 (NBA) and
#30 (weather) all failed because the model was less accurate than the price. This step catches that early and
cheaply, before any betting-rule versions are spent.

## Gates (on the discovery period only; unseen stays untouched until all three pass)

1. **Accuracy vs the market.**
   - Compute the model's Brier score and log loss next to the market's, on the same events at the same decision time.
   - Market probability = the mid of bid and ask at that moment; for sportsbook odds, de-vigged.
   - Pass if the model's Brier is at or below the market's, or within 0.002.
   - Otherwise **stop**: record "model worse than market" in the log, and don't run betting rules.
2. **Do the disagreements carry information? (residual test)**
   - Fit outcome ~ logit(market p) + (logit(model p) - logit(market p)) by logistic regression.
   - The disagreement term's coefficient must be > 0 with a 95% CI (bootstrap by game or date) that excludes 0.
   - If the CI includes 0, the model adds nothing the price doesn't already know. **Stop.**
3. **Size of the information vs the cost.**
   - Estimate how often the model and the market differ by more than the round-trip cost (half the spread plus the
     fee at that price), and the average gap in those cases.
   - Under about 20 such events per week of data means the idea can't reach the $2/week bar. Say so and stop.

Report calibration buckets for model and market side by side, plus a model + market blend (average of logits) as
a reference.

## Then

Only after all three gates pass:
- go to `/honest-test` with the pre-registered betting rule;
- then `/unseen-test` and `/coin-flip`, as usual.

Every gate run counts as a version in the log entry.

## Build and review

- The `backtest-builder` agent (Sonnet) writes the code: no look-ahead, features timestamped before the decision.
- The `leak-auditor` agent (Opus) reviews it before any gate result is read.
- Data questions go to the `data-scout` agent (Haiku).
