---
name: strategy-ideas
description: Step 3b — list 10 simple, well-known prediction-market / Kalshi strategies, say in one sentence who loses money to each, mark which were already tested here, and queue the untested ones for honest backtests. Use when the user has no strategy or asks for new ideas.
argument-hint: "[market family, e.g. crypto, sports, weather, politics]"
---

# Step 3b — 10 known strategies

His prompt: "Give me 10 simple, well-known day trading strategies that have been published before. For each one,
explain in one sentence who is losing money to it. Then backtest all 10 with the honest rules."

Focus: $ARGUMENTS (all Kalshi markets if empty).

1. List 10 published or well-known ideas that fit Kalshi binary contracts. Mix types: favorite/long-shot bias,
   late-window momentum, mean reversion after spikes, cross-market riskless pairs (bracket sums, ladders, related
   series), cross-venue gaps, news-lag, maker-only versions of taker ideas, settlement-rule quirks, liquidity rewards,
   forecast-vs-price (weather/NOAA, sports models).
2. For each one, one line each: the rule; **who is losing money to it** (if nobody, it isn't an edge); and the data
   needed.
3. Mark each against `docs/RESEARCH_LOG.md` and the `kalshi-edge-findings` memory: **already tested** (with result and
   log #), **partly tested** (what's missing, for example "maker version untested"), or **new**.
4. Be honest about the odds. Everything tested here so far has lost to fees and spread. Rank the new ones by how
   believable the "who loses" answer is and how cheap the data is.
5. Ask the user which to run. For each one chosen, go to `/log-idea` (one entry each, before any data is looked at),
   then `/honest-test`.
