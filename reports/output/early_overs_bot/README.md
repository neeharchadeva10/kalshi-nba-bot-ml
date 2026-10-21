# early_overs_bot

Small, capped live bot for the nightly NBA totals bets (research log #40, formula-side version). Not started by
anyone but the user.

Commands (from C:\Users\Jarvis\Documents\Kalshi-PM):

- Dry run (sends nothing): `.venv/Scripts/python.exe reports/output/early_overs_bot/overs_bot.py --dry-run --once`
- Dry run showing the first game's order even outside its window: add `--force-first`
- LIVE: `KALSHI_ENV=prod .venv/Scripts/python.exe reports/output/early_overs_bot/overs_bot.py --live`
  (PowerShell: `$env:KALSHI_ENV="prod"; .venv/Scripts/python.exe reports/output/early_overs_bot/overs_bot.py --live`)
- STOP: create the file `reports/output/early_overs_bot/STOP` (cancels the bot's open orders, exits). Delete it before
  the next start. Ctrl+C does the same.
- Tests: `.venv/Scripts/python.exe -m pytest reports/output/early_overs_bot/test_overs_bot.py -q`

Behaviour: every 60 s (each ET day's loop starts 11:00 ET; the schedule is reloaded every 30 min), for each game in
its window (tip-75 to tip-45 min, "tip" = the pick list's tip time): recompute the pick, then re-check just before
sending that the window and the end date still hold, that the chosen side's fresh best bid still equals the planned
price (no chasing), that the market is active with a valid exchange_index, and that there is no position and no
resting order on that ticker from any source. Then place ONE post-only GTC limit buy of 1 contract at that bid
(cancel_order_on_pause, expiry set to tip-15 as a backstop). At tip-15 cancel anything unfilled or partly filled.
Never re-places, chases or modifies.

Safety: a lock file (logs/early_overs_bot.lock) allows only one live instance; `--live` is refused with `--once`; the
PC clock must be within 2 s of the exchange; the first 401/403 cancels open orders and halts; any exit with open orders
cancels them. An order that cannot be found for 3 listings after cancel_at + 10 min is marked not_found (its full cost
stays counted toward the cap) and is reported in the log.

Hard caps (constants in overs_bot.py): series KXNBATOTAL, buys only, count 1, price 15-85c, 1 order per game per ET date,
14 orders per night, $7.00 per night (price x count + estimated fee, filled and resting), no orders after
2026-11-03 23:59 ET.

Log: logs/early_overs_bot.jsonl (dry-run lines have "dry": true and are ignored when a live run restarts). A restart
rebuilds tonight's orders from that log, so a game is not placed twice. The client_order_id is fixed per game and date,
but Kalshi only dedupes it among live and recent orders, so the log (not the exchange) is the real guard.
