---
name: honest-test
description: Step 3 — build or run a backtest that can't cheat - exact Kalshi fees, fills only at prices that actually traded, no future information, results at the user's real size. Use for any backtest of a trading rule.
argument-hint: "<log # or strategy file>"
---

# Step 3 — The honest test

Idea: $ARGUMENTS. It must already be in `docs/RESEARCH_LOG.md`. If it isn't, run `/log-idea` first.

His prompt, adapted: "You are my backtesting engineer. Build a backtester for my strategy using the 1-minute data.
Non-negotiable rules."

## Non-negotiable rules

1. **Exact Kalshi fees on every trade.** Taker = 0.07 x C x P x (1 - P) per order with Kalshi's fee rounding
   (https://docs.kalshi.com/getting_started/fee_rounding). Makers pay 0 on `quadratic` series and
   0.0175 x C x P x (1 - P) on `quadratic_with_maker_fees`. Read `fee_type` from the series. Don't use the
   `cent_ceil` setting in config/settings.yaml for small orders; it overstates fees on 1-10 contracts.
2. **Only fill at prices the market actually traded at.**
   - Takers pay the ask (YES) or 1 - bid (NO) from the quote at that minute. No mid-price fills.
   - Confirm with trade prints: within a short window, prints with the taker on our side at our price or better,
     with at least our size in total. Report results both with and without this check.
   - Makers fill only when later prints trade through our price (back of the queue).
3. **Never use information from the future.** Each decision uses only data timestamped before it. Watch for: candle
   close vs open, the settlement value, end-of-day lists, and filters chosen with hindsight.
4. **Slippage:** if our size is more than the displayed size at the price, walk the book or count the trade as
   unfilled.
5. **If a result looks too good, treat it as a bug** and show why it isn't one before reporting it. His first winner
   was 74% fake from missing costs.
6. **Real size:** report per trade and in dollars per week at the user's size (default 10 contracts, $1 max per trade
   for the live micro-account).

## How

- Reuse what exists: `kpm.analysis.*` (for example `weather_calib.py`, `spread_arb.py` and `crypto_bot_rules.py`
  show the pattern), `kpm.core.prices`, the cached clients.
- New backtest code: Sonnet writes it with unit tests (fees, fill logic, no-lookahead on a toy example); Opus reviews
  it before any result is read.
- Use only the discovery period here. Unseen data is reserved for `/unseen-test`.
- Every variant run (filter, threshold, bucket, time) is one more version. Append it to the log entry.

## Report

Trades, win rate, average net per trade after fees, dollars per week at the real size, worst losing streak, and the
same numbers with fills confirmed. Then: PASS (go to `/unseen-test`) or FAIL (stop and record it in the log).
