# early_overs_bot

Small, capped live bot that places the user's nightly NBA totals picks automatically as INSTANT FILLS (research log
#40, formula-side version). Not started by anyone but the user.

## Desktop buttons (one click)

Run once to create them: `powershell -ExecutionPolicy Bypass -File scripts\make_bot_shortcuts.ps1` (puts "START Kalshi
bot" and "STOP Kalshi bot" on the Desktop).

- START (`scripts/start_overs_bot.ps1`): if the heartbeat is fresh and live and its pid really is overs_bot.py it just says
  "Bot already running (pid N)". Otherwise it deletes a leftover STOP file and opens a new window titled "Kalshi overs bot
  (LIVE)" running the live command below with KALSHI_ENV=prod. Leave that window open; Ctrl+C in it also stops the bot.
- STOP (`scripts/stop_overs_bot.ps1`): creates the STOP file and waits up to 45 s for the bot to exit by itself (it
  cancels anything open first). If it is still live it kills only the pid in the heartbeat, and only after checking that
  pid's command line contains overs_bot.py. A hard kill cannot cancel orders, so check the Kalshi app for open orders
  (instant-fill orders normally leave none).
- Both log to logs/early_overs_bot_launcher.log.

## Daily start (live), plain command alternative

From %USERPROFILE%\Documents\Kalshi-PM, PowerShell:

    $env:KALSHI_ENV="prod"; .venv/Scripts/python.exe reports/output/early_overs_bot/overs_bot.py --live

- Start it before the first window opens: about 75 minutes before the first tip of the night (the first game's
  tip time on the pick list minus 75 min). Anything not started by tip-45 min is skipped.
- Stop it: create the file `reports/output/early_overs_bot/STOP` (the bot cancels anything still open and exits;
  delete the file before the next start), or press Ctrl+C in its window.
- One start covers several days. `_run_days` loops over every ET date from the start day to `--until` (default
  2026-11-03): after each night's last game passes tip-15 it sleeps until 11:00 ET the next day and continues, with
  the clock re-checked each day. So you do not have to restart it daily as long as the PC stays on, awake, and the
  window stays open. If the PC sleeps, the window is closed or the process dies, start it again the same way (a restart
  rebuilds tonight's attempts from the log, so nothing is placed twice). Restarting daily is fine and also safe; the
  lock allows only one copy at a time.

Other commands:

- Dry run (sends nothing; reads public data only): `.venv/Scripts/python.exe reports/output/early_overs_bot/overs_bot.py --dry-run --once`
- Dry run showing the first game's order even outside its window: add `--force-first`
- Tests: `.venv/Scripts/python.exe -m pytest reports/output/early_overs_bot/test_overs_bot.py -q`

## What it does

Pick source of truth = the Discord ping. The notifier (scripts/early_overs_notify.py) records each ping in
logs/early_overs_notified.json (key `<ET date>|<game>` -> side, strike, limit_c = the ask of the chosen side at ping time).
For each game, from tip-75 to tip-45 min:

1. If a ping exists for that night and game, the bot buys EXACTLY that: same side, same strike (ticker
   `KXNBATOTAL-<game>-<ceil(strike)>`, checked against the market's `floor_strike`; a mismatch skips the game), max price
   = limit_c.
2. If there is no ping yet it waits and re-checks every loop.
3. If there is still no ping at tip-50 min it computes the pick itself with `compute_rows` and uses that row's taker price
   as the max price. A row with `bet == False` (or no side) is never bet.
4. The source used (`ping` or `fallback`) is logged on the `pick` line and in every `order_attempt`/`would_send` line.

Instant fill: once a game's pick is fixed, every loop (60 s) until tip-15 min it reads the side's current ask. Only if the
ask is <= max price (and 15-85c) it sends ONE immediate-or-cancel buy of 1 contract with limit = that ask (no post_only,
exactly the live-proven approve.py / lockbot body: no post_only, no expiration_time, no cancel_order_on_pause). If the IOC does not fill it does not
chase: it tries again on a later loop only when the ask is back at or below the max price. Max 4 IOC attempts per game,
40 per night. The game is done as soon as 1 contract is filled; a partial fill (fractional contracts) is not done, so a
top-up IOC for the remainder is allowed within the same caps. It also needs the best ask level to hold at least the
contracts wanted ("thin ask" otherwise) and the shard cash to cover limit + fee + $0.10 ("low cash" otherwise; a failed
cash read skips the send).

Safety (all kept): event-level exposure check before every attempt (any position or resting order in that game's
KXNBATOTAL event that the bot did not make itself, e.g. one placed by hand, skips the game for the night. The bot's own
filled contracts on a ticker are subtracted first, so a top-up after a partial fill works; a position bigger than our
fills, on the opposite side, or on another strike still counts as foreign. A position view lagging behind our fills is not
treated as foreign); single-instance lock
(logs/early_overs_bot.lock); `--live` needs KALSHI_ENV exactly `prod`; PC clock within 2 s of the exchange; first 401/403
cancels and halts; STOP file; hard end date 2026-11-03 23:59 ET; the expiration backstop (an IOC should never rest, but
anything still open at tip-15 is cancelled, and anything open at exit is cancelled); an order that cannot be found is
marked not_found after tip-15 + 10 min and its full cost stays counted.

Hard caps (constants in overs_bot.py): series KXNBATOTAL, buys only, count 1, price 15-85c, at most 1 contract filled per
game, 4 IOC attempts per game, 40 per night, $7.00 per night. The spend counts filled cost + taker fee
(0.07 x P x (1-P) rounded up to $0.0001, plus one $0.0001 step) and any in-flight order at its limit.
`client_order_id` = `eob-<date>-<game>-a<n>` (unique per attempt; the `eob-` prefix keeps the trade ledger tagging fills
as early_overs_bot).

## Heartbeat

At most every 60 s (also while sleeping between days) the bot writes logs/early_overs_bot.heartbeat atomically (temp file,
then replace): `{"ts_utc", "pid", "live", "placing", "night"}`. `placing` is true only when live AND the clock check passed AND the
nightly caps (attempts, $7) are not exhausted AND the end date has not passed AND the bot is not halted/stopped; it is
written immediately when it changes. The notifier should key its "bot is placing" line off `placing == true`. A dry run writes `live: false, placing: false` and never overwrites a fresh live-bot heartbeat; a live bot writes
`live: false, placing: false` when it exits.

## Log

logs/early_overs_bot.jsonl (dry-run lines have `"dry": true` and are ignored when a live run restarts). Events: `pick`,
`wait_ping`, `no_send` (ask above max, logged once per change), `caps`, `order_attempt`, `order_sent`, `fill`,
`order_state`, `game_closed`. A restart rebuilds tonight's attempts and picks from this log, so attempt numbers continue
and a filled game is not bet again.

## Notes and limits

- Games tipping before about 18:15 ET get no Discord ping unless the notifier window is widened (being widened to
  11:00-23:00). Such a game uses the ping once the notifier sends it; if none exists at tip-50 the bot falls back to its
  own `compute_rows` pick. A half-written notified file never triggers a fallback on that loop (it retries next loop).
- If a schedule reload (every 30 min) finds an armed game's tip moved EARLIER, its cancel time moves with it and the game
  is closed if that time has already passed. A later tip is logged but the earlier cancel time is kept.
- Order lookups use the ticker listing first; the single-order GET is only a fallback and a 403/404 from it never halts.
  401/403 from the listing, positions or create still halt the bot.
- The exposure listing is also filtered locally by event, so an API that ignores `event_ticker` cannot block (or hide)
  anything.
- The heartbeat `placing` is also false while the last shard-cash read is below 0.85 + fee + $0.10 (cannot afford a
  max-price order) or after 3 consecutive cash/exposure read failures; it is rewritten at the end of every tick so cap
  exhaustion shows at once.
