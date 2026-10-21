---
name: right-size
description: Step 9 — position sizing. Simulate the strategy thousands of times at different sizes under the account's real limits ($5 loss cap, $1 per trade, ~$29 balance, book depth) and pick the size that survives most often, with a coin-flip version at every size. Use before any live trading.
argument-hint: "<log # or strategy>"
---

# Step 9 — Right size

His prompt: "Simulate my strategy on this prop firm's exact rules, thousands of times, starting on every day in
history. Test different sizes and show me which size passes most often, fastest and cheapest. Include a coin-flip
version at every size." He blew two accounts at 10 micros, then passed at 5 micros, because at 10 the edge lost to
the account rules.

Strategy: $ARGUMENTS

## The "account rules" here

- Live micro-account: about $29. **$5 account-wide loss cap** (auto-stop), **$1 max per trade**, only the series
  allowed by the live exception (see the `read-only-research-rules` memory). $0.88 of the $5 cap is already used.
- Kalshi ties up the full price per contract until it settles.
- Fees round per order, so 1-contract orders pay more per contract than 10-contract orders.
- Book depth: an order bigger than the displayed size at the price won't fill there.

## Simulate

1. Use the strategy's trade list from the unseen period plus paper trading (not discovery).
2. Sizes: 1, 2, 3, 5 and 10 contracts, plus the largest that fits in $1 per trade.
3. For each size: start on every day in the history, replay forward with a day-block bootstrap (at least 2,000 runs
   per start), and recompute fees and fills at that size, counting a trade as unfilled when its size is over the
   displayed size.
4. **Report per size:**
   - chance of hitting the $5 cap before reaching +$5,
   - median days to +$5,
   - expected dollars per week,
   - worst run.

   Run the same for the coin-flip version at the same size.
5. Pick the size with the best odds of not hitting the cap where the strategy still clearly beats its coin flip. Say
   plainly if no size works.
6. Log the result (`/log-idea`). Next: `/go-live`, which only the user can start.
