---
name: send
description: User-only. Places ONE live Kalshi order from a ticket Claude recommended, through reports/output/picks/approve.py and its hard limits. The user typing /send <ticket-id> is their approval for that one ticket only.
argument-hint: "<ticket-id>"
disable-model-invocation: true
allowed-tools: Bash(KALSHI_ENV=prod .venv/Scripts/python.exe reports/output/picks/approve.py:*)
---

# /send — the user approves one ticket

Ticket: $ARGUMENTS

The user typed this command themselves. That is their approval to send **this one ticket**, once. It does not
approve any other ticket, a retry, or anything later.

1. **No ticket id given:**
   - List the pending tickets. These are files in `reports/output/picks/tickets/*.json` that are under 30 minutes
     old and have no `send_attempt` or `order_result` line for their id in `logs/picks_orders.jsonl`.
   - For each, show its id, market, side, count, max price, max cost and reason.
   - Tell the user to type `/send <ticket-id>`. **Send nothing.**
2. **Check the id:**
   - It must match `^[A-Za-z0-9-]+$`, and `reports/output/picks/tickets/<id>.json` must exist.
   - If not, stop and say why.
3. **Send it.** Run exactly this, once:
   `KALSHI_ENV=prod .venv/Scripts/python.exe reports/output/picks/approve.py <id> --send`
   - approve.py does its own live pre-flight and enforces every limit: moneyline and spread series only, buys only,
     IOC, at most 10 contracts, at most $3.00 per ticket and in total, 30-minute expiry, one shot.
   - Never edit those limits or the ticket to get an order through.
4. **Report** in plain English:
   - whether it filled, how many contracts, the average price, and the cost including the fee;
   - the position afterwards, or the exact abort reason.
5. **If it aborted** (the price moved, the ticket expired, and so on): **don't retry.**
   - Re-check the market.
   - If a bet still makes sense, write a fresh ticket and show it.
   - The user decides with a new `/send`.

Never run `approve.py ... --send` outside a `/send` the user typed.
