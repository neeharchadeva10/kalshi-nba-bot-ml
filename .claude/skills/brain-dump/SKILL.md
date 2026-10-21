---
name: brain-dump
description: Step 3a — interview the user about how they actually trade (markets, timing, entries, exits, skip days, sizing) and turn it into exact, testable rules. Use when the user wants to test their own strategy or says "here's how I trade".
argument-hint: "[strategy nickname, optional]"
---

# Step 3a — Tell Claude everything you know about trading

His tip was to talk into a mic for an hour and feed Claude your trading journal. Do the same here.

**Voice tip for the user:** on Windows, press **Win + H** in the Claude Code prompt to dictate for free. (He used
Wispr Flow, which is a separate app.)

## Interview

Ask one topic at a time and keep each question short. The user types fast and informally. Topics:

1. Which markets (for example KXBTC15M up/down, NFL winners mid-game, weather, politics) and why those.
2. When: time of day, minutes into the window, days skipped (CPI, FOMC, Fed speakers, big games).
3. Entry: exactly what has to be true. Chart timeframes (the user reads 5m/15m/1h swing highs and lows), price range
   paid, YES or NO.
4. Exit: hold to settlement, sell at a target, or bail at a price. Kalshi has no stop orders, so a "stop" means a
   resting sell or a bot watching.
5. Size: contracts or dollars per trade, and the most trades per day.
6. What a bad day looks like, and what made the best trades work.
7. **Journal:** offer to read the user's own Kalshi fills, positions and settlements as the journal. This is GET only,
   through `kpm.execution` ExecClient with `live=False`; the user approved it on 2026-10-13. Never place, change or
   cancel anything.

## Output

1. Write `docs/strategies/<nickname>.md`:
   - the rules in plain English,
   - the same rules as exact, codeable conditions,
   - a list of anything still vague, with the default you chose,
   - which data each rule needs.
2. Check the `kalshi-edge-findings` memory and `docs/RESEARCH_LOG.md`. If something close has been tested, say so with
   the result (for example, the user's higher-timeframe swing style was tested and lost about 4-5c per trade).
3. Hand off to `/log-idea` to log it before any backtest, then `/honest-test`.
