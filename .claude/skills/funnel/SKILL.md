---
name: funnel
description: Show where every trading idea stands in the 10-step "zero to AI trading bot" funnel (honest test, unseen data, coin flip, team, paper trade, right size, live) and what to run next. Use when the user asks what's been tested, what to do next, or to run an idea through the whole process.
argument-hint: "[idea name or #, optional]"
---

# The funnel

This project follows the 10-step workflow from "Claude Code Trading Bot: Full Step-by-Step Tutorial" (Steven Ace
Trades), adapted from NASDAQ futures to Kalshi. Each step has its own skill:

| Step | Skill | What it does |
|---|---|---|
| 1 Set up | — | Done: VS Code/terminal, Claude Code, Python venv (`.venv`), the `kpm` CLI |
| 2 Get data | `/get-data` | Download and cache real Kalshi history (free public API) |
| 3a Your rules | `/brain-dump` | Interview the user about how they trade; turn it into exact rules |
| 3b Known ideas | `/strategy-ideas` | 10 well-known prediction-market strategies and who loses money to each |
| 3 Honest test | `/honest-test` | Backtest with real fees, real fills only, no peeking at the future |
| 4 Unseen data | `/unseen-test` | Build on early data, test once on later data it never saw |
| 5 Coin flip | `/coin-flip` | Beat 500 random-entry copies of itself, or it's out |
| 6 Log everything | `/log-idea` | Append-only research log with a version count |
| 7 Build a team | `/build-team` | Combine the survivors into one portfolio |
| 8 Paper trade | `/paper-trade` | Forward-test on brand-new days with no real orders |
| 9 Right size | `/right-size` | Pick the size that best survives the $5 loss cap |
| 10 Go live | `/go-live` | Gate check only. The user starts live trading themselves |

**The five rules:** real data and a backtester that can't cheat; test on data it has never seen; beat a coin flip;
fake money before real money; size small and let the bot do the clicking.

## What to do

1. Read `docs/RESEARCH_LOG.md` and the `kalshi-edge-findings` memory.
2. If `$ARGUMENTS` names an idea, show that idea's row: every step passed, the step it is on, the version count, and
   the exact next skill to run. Otherwise show a short table of all ideas with their furthest step and result, plus
   the total versions tested.
3. Recommend one next action. Order matters: never skip a step. An idea that fails a step stops there unless the user
   logs a new version (which adds to the count).
4. Keep it plain English with dollars, at the user's real size (default 10 contracts; the live account is about $29
   with a $5 account-wide loss cap).

## Project guardrails (apply to every step)

- Research is read-only and GET-only (see the `read-only-research-rules` memory). No paid data or services without
  asking first.
- Pre-register: write the idea and its pass bar in the log **before** looking at the data.
- Model roles: Opus orchestrates and checks, Sonnet writes code, Haiku scouts. Never Fable.
- Launch long jobs detached with `.venv\Scripts\python.exe`. They don't survive a reboot.
