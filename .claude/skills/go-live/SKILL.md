---
name: go-live
description: Step 10 — pre-flight gate for live trading. Checks the strategy passed every funnel step, checks the account and safety gates read-only, and tells the user the exact command to start it themselves. Claude never starts live trading.
argument-hint: "<log # or strategy>"
disable-model-invocation: true
---

# Step 10 — Go live (the user presses the button)

His prompt: "Connect my account through its API and place the trades from the dealer for me. Every order must have
its stop-loss attached on the broker side. Start in test mode with no real orders and wait for me to say go. Turn
yourself on every weekday, stop trading for the day if I lose X dollars, give me a kill button on my desktop that
closes everything, and text me every time you enter, exit or have a problem."

Most of that already exists in `kpm.execution`. **Claude never runs `--live` and never types the confirmation.**

Strategy: $ARGUMENTS

## 1. Gate: did it earn this?

From `docs/RESEARCH_LOG.md`, show the dated PASS lines for steps 3 (honest), 4 (unseen), 5 (coin flip), 8 (paper)
and 9 (size). Also show the version count. If any step is missing or failed, stop and say which. (The H2 pair and
late_favorite micro-tests were the user's explicit exceptions on 2026-10-05, made knowing the backtests; that
doesn't carry over to new strategies.)

## 2. Safety checks (read-only)

- `.venv\Scripts\python.exe -m kpm exec check --env prod`: config, clock, cash, resting orders, loss cap used.
  This sends no writes.
- The strategy must exist as a `kpm bot run --strategy <name>` choice. A new strategy needs code in `kpm.execution`:
  Sonnet writes it, Opus reviews it, and tests pass, with every order going through `kpm.execution.safety`.
- Run it watch-only first: `kpm bot run --strategy <name> --env prod` (no `--live`) for at least one full session,
  and compare its decisions with the paper-trade recorder.
- Limits, from the `read-only-research-rules` memory: $5 account-wide loss cap with auto-stop, $1 max per trade,
  1 contract per leg, allowed series only. Changing any of these is the user's decision.
- Kalshi has no broker-side stop orders. The worst case per trade is the price paid, which is why the $1-per-trade
  cap matters. A "stop" means the bot sells, which needs the bot to be running.
- Kill button: `.venv\Scripts\python.exe -m kpm exec kill` writes the kill file (every bot stops within a second) and
  cancels anything resting. The dashboard at 127.0.0.1:8765 has a STOP button. Offer to make a desktop shortcut that
  runs `kpm exec kill`.
- Alerts: Discord or text needs the user's OK and a webhook in `.env`. The default is the dashboard and the logs.
- Clock: if `exec check` flags drift, the user runs `w32tm /resync` in an admin terminal.

## 3. Hand over

Tell the user the exact command to run **themselves** in their own terminal:

```powershell
.venv\Scripts\python.exe -m kpm bot run --strategy <name> --live --env prod
```

It also needs `trading.enabled: true` in `config/settings.yaml` and `KALSHI_ENV=prod`, and the user types
`LIVE PROD 5` at the prompt. Claude does not edit `trading.enabled` unless asked, and never types the phrase.
Log the go-live date in the research log.
