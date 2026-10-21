---
name: paper-trade
description: Step 8 — forward-test ("the dealer") - a program that watches live Kalshi markets, records every trade the strategy would take on brand-new days, settles them, and never places an order. Use after a strategy or team passes the backtest steps.
argument-hint: "<log # or strategy>"
---

# Step 8 — Paper trade on brand-new days

His prompt: "Build a program that runs every weekday from 9 a.m. to 4 p.m. It should watch the market every minute
and record every trade my strategies would take today. Send each entry and exit to my Discord. Don't place any real
orders." He calls his forward tester "the dealer". Brand-new days are the one test you can't accidentally cheat on.

Strategy: $ARGUMENTS

## Build

1. **Use what exists if it fits:** `kpm bot run --strategy late_favorite|pair_h2` is already watch-only without
   `--live` (paper trades, no keys, nothing sent). `kpm lip-collect` is the pattern for a detached forward recorder:
   heartbeat file, resumable, raw responses kept.
2. **For a new strategy,** Sonnet writes a watch-only recorder and Opus reviews it:
   - public GET data only, no auth, nothing that can place orders;
   - it runs on the strategy's schedule (many Kalshi series, like BTC 15m, run around the clock; set the hours in
     the log entry);
   - every minute, decide with only the data available then;
   - record each would-be entry with the quote, displayed size, the timestamp and the reason;
   - record exits and settlement results;
   - store the following prints so fills can be confirmed later, the same way as in `/honest-test`;
   - write a heartbeat file, survive restarts, and log every gap.
3. **Run it detached:** `Start-Process -WindowStyle Hidden .venv\Scripts\python.exe -ArgumentList ...`. It doesn't
   survive a reboot (the PC crashed on 2026-10-14 and the LIP recorder was down for 4.5 days), so check it every
   session.
4. **Alerts:** the default is the log file and the local dashboard (`kpm dashboard`, 127.0.0.1:8765). Discord alerts,
   like his, need the user's OK and a webhook URL in `.env` (never printed). Sending a message is an outbound write
   to a third party, so ask first.

## Judge

- The length and pass bar go in the log before starting. The default is at least 14 days and at least 100 would-be
  trades.
- At the end: the paper results next to the backtest's unseen-period numbers. Net per trade after fees, dollars per
  week at the real size, fill-confirmed share, and every gap.
- PASS: go to `/right-size`. FAIL: record it in the log.
