# Handoff — 2026-10-19 06:30Z (02:30 ET, Oct 19)

Read this first when you pick the project back up. The memory files in
`~/.claude/projects/C--Users-Jarvis-Documents-Kalshi-PM/memory/` hold the long-term rules and findings. This file
holds the current state.

## Where things stand

- **Every edge tested so far has lost.** The newest result: **weather calibration FAILED (2026-10-19), $0/week at 10
  contracts.**
  - 0 of 192 pre-registered cells qualified. Taker buys lose in every price bucket.
  - The long-shot bias is real, but smaller than spread plus fee.
  - All data-quality checks passed, including W6 (40 of 40 match fresh API data).
  - Details: `reports/output/weather_calibration/README.md`.
- Earlier failures: NFL winner-vs-spread arb, market making, BTC 15-minute, bot v5, the scanner and Polymarket US
  cross-venue. See the `kalshi-edge-findings` memory.
- **One test is running: the LIP forward test.** It's a 14-day paper test of collecting Kalshi Liquidity Incentive
  Program rewards with 10-contract resting bids.
  - **It has a gap.** Run 1 lasted from 2026-10-13 21:41Z to 2026-10-14 19:16Z (about 21.5 h). Then the PC crashed
    (Kernel-Power 41, unclean shutdown at about 15:05 ET) and Windows Update rebooted it three times. Nothing
    restarted the recorder.
  - Restarted on **2026-10-19 06:15Z**. It resumed 50 tracked (program, q) rows on 27 markets. The run-1 log is
    backed up at `logs/lip_collect_run1_2026-10-13.log`.
  - **Decision for the user:** count only the new run (14 days ends about **2026-11-02 06:15Z**), or include the
    first 21.5 h too. Either way the gap must be reported in the write-up.

## WAS @ SF live night (2026-10-19 evening, ET)

- The user asked to trade the MNF game. Claude found no structural lock.
- The user approved live bets on thin win-margin buckets and protected props priced below their deep-market fair
  value, and later an automatic trader under exact allow rules.
- **Result: about -$2.54.** The account-wide $5 loss cap has about $3.42 used.
- Every step is in `docs/RESEARCH_LOG.md` (#18-#21). Fills are in `logs/wassf_orders.jsonl`. Scripts:
  `reports/output/nfl_dip/wassf_*.py`.
- Not a valid test. Next: test #20/#21 offline through the funnel, using the recorded `logs/wassf_watch.csv`,
  `logs/wassf_signals.csv` and past games.
- **Cleanup for the user:** remove the 4 allow rules added that night with `/permissions` (wassf_sell, wassf_trade,
  wassf_auto, tail).
- The read-only dashboard `wassf_dashboard.py` (127.0.0.1:8766) was stopped 2026-10-20 ~07:40Z by Claude Code
  because memory was low. Restart it only if the user asks. The price
  watcher `wassf_watch.py` stops itself at settlement or 04:45Z.
- Post-game analysis is done (`reports/output/nfl_dip/postgame.*`, summary in the research log under #21).
- **Next: research log #22, the WebSocket recorder.** It is built and reviewed (`kpm nfl-markets --date 26OCT22`, then
  `KALSHI_ENV=prod .venv/Scripts/python.exe -m kpm exec record --markets data/ws/26OCT22/markets.json --until <ISO>`;
  stop with `logs/ws_recorder.STOP`; heartbeat `logs/ws_recorder.heartbeat`).
  - It is read-only and uses the user's key only to sign the handshake (approved 2026-10-20).
  - Plan: record NE@CHI Thu 2026-10-22, about 22:00Z to 04:30Z, re-running discovery first. Then the Sunday games.
  - Write the #22 test rules in the research log BEFORE looking at the recordings.
  - **Done 2026-10-20:**
    - Rules pre-registered: #22 NFL and #23 NBA, with amendments up to 08:05Z.
    - The simulator `kpm ws-sim --sport nfl|nba` is built, reviewed and fixed on synthetic data only. 150 targeted
      tests pass.
  - **Test gates:**
    - #22 runs once 6+ NFL games are recorded (3+ unseen), at least 48 h after the last one ends.
    - #23 runs at 20+ NBA games (10+ unseen).
  - Pass every raw folder with `--raw`, and its markets.json with `--markets`.
  - Waiting on the user: start it by hand Thursday, or add a Windows scheduled task (needs their OK).

## Running right now (detached, independent of Claude Code)

| What | Real PID | Started | Logs |
|---|---|---|---|
| LIP recorder (`kpm lip-collect`) | 3824 (launcher stub 9476) | 2026-10-19 06:15Z | `logs/lip_collect.log`, `logs/lip_collect.heartbeat` |
| WS order-book recorder (`kpm exec record`, NE@CHI 26OCT22, 1,069 markets, until 2026-10-23T05:00Z) | detached | restarted 2026-10-20 04:58Z (old code: stop file `logs/ws_recorder.STOP`) | `logs/ws_recorder.heartbeat`, `logs/ws_recorder.out/.err`, data `data/ws/26OCT22/raw/` |
| WS recorder, NBA 26OCT20 (3 games, 1,437 markets, until 2026-10-21T09:00Z) | detached | 2026-10-20 05:05Z | `logs/ws_recorder-nba-26OCT20.heartbeat` (stop: `logs/ws_recorder-nba-26OCT20.STOP`), data `data/ws/nba/26OCT20/raw/` |
| WS recorder, NBA 26OCT21 (11 games, 3,321 markets, until 2026-10-22T09:00Z) | 8672 (launcher) | 2026-10-20 17:46Z | `logs/ws_recorder-nba-26OCT21.heartbeat` (stop: `logs/ws_recorder-nba-26OCT21.STOP`), data `data/ws/nba/26OCT21/raw/` |
| WS recorder, NBA 26OCT21 **supplement** for #33 (the quarter/half/prop tickers listed after the first recorder started, 1,830 markets, *PROT excluded, until 2026-10-22T09:00Z) | 6424 | 2026-10-21 04:51Z | `logs/ws_recorder-nba-26OCT21b.heartbeat` (stop: `logs/ws_recorder-nba-26OCT21b.STOP`), data `data/ws/nba/26OCT21b/raw/` |
| Lock watcher, BOS@DET 26OCT20 (read-only, no order code; 564 markets, until 2026-10-21T00:00Z) | 21428 (launcher 14680) | 2026-10-20 17:58Z | `logs/lockwatch_bosdet.jsonl`, `.heartbeat` (stop: `logs/lockwatch_bosdet.STOP`) |

- **Restarting a WS recorder into the same folder** (e.g. NE@CHI Thursday with fresh discovery):
  - First drop its STOP file.
  - Wait until its process has exited: the heartbeat stops updating and the process is gone.
  - Then start the new one.
  - Two processes writing one raw folder at the same time make `kpm ws-sim` count lines out of order, and the
    pre-registered test is then INVALID. Different folders may overlap in time.
- **Collectors do not survive a reboot or crash.** After any restart of the PC, check that PID 3824 (or its
  replacement) is alive.
- Keep the PC plugged in. It sleeps after 3 minutes on battery.
- Free RAM is low, about 1.5 GB of 13.8 GB. Don't start a background watcher unless the user asks.

### Quick status checks (PowerShell, from the repo root)

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*lip-collect*' } | Select-Object ProcessId, CommandLine
Get-Content logs\lip_collect.heartbeat                 # errors should be 0 and iso should be recent
```

### If the recorder died

**Use the venv python.** The system `python` has no `kpm` installed, and that launch fails silently in a hidden window.

```powershell
Start-Process -WindowStyle Hidden .venv\Scripts\python.exe -ArgumentList '-m','kpm','lip-collect'
```

It resumes its tracked markets from `data/lip_forward.sqlite` and appends to the same log. Note every gap for the
write-up.

## Next steps, in order

1. **Tell the user** the weather verdict, and ask how to treat the LIP gap.
2. **After 14 days of LIP data:** build `kpm lip-sim`, the simulator plus verdict exactly as in the "H4 step 2
   amendment" in `reports/output/mechanical_edges/PREREGISTRATION.md`. The primary result is q=10, rule C, back of
   queue. It must clear these bars:
   - net per $ of collateral above 0 in each week,
   - still above 0 under stress (rewards ×0.5, losing fills ×2),
   - at least 5 positive markets, with no single market more than 50% of the total.

   Sonnet writes the code and Opus reviews it. The scoring helpers already exist in `src/kpm/analysis/lip_forward.py`.
3. **Possible next weather tests.** Each needs its own pre-registration before any data is looked at:
   - Maker version of the calibration test: resting bids, no fee, counted only when trade prints show a fill.
     The data is already in `data/weather.sqlite`; trade prints come from the public API.
   - #1 (NOAA): late-day "the high already happened" lock-ins, using NWS observations.
   - #3 (NOAA): NBM forecasts against the market price.

## Done this session (2026-10-19)

- **Funnel skills added** (from the video's 10-step workflow, adapted to Kalshi): `.claude/skills/` has `/funnel`,
  `/get-data`, `/brain-dump`, `/strategy-ideas`, `/honest-test`, `/unseen-test`, `/coin-flip`, `/log-idea`,
  `/build-team`, `/paper-trade`, `/right-size` and `/go-live` (user-only; it never starts live trading).
  The append-only research log is `docs/RESEARCH_LOG.md`, backfilled with ideas #1-17. **Log every new idea
  there before testing it.**

- Weather download finished 2026-10-14 03:57Z (97,224 markets). `weather-calib` ran in 123 s. W6 re-check, README,
  memories updated.
- The `/watch` video skill was set up with the local engine (captions only, balanced detail). `yt-dlp`, Deno and the
  yt-dlp FFmpeg build were installed via winget. The user declined Gemini.
- Video watched: "Claude Code Trading Bot: Full Step-by-Step Tutorial" (Steven Ace Trades, bNevUiQe858). It covers a
  10-step NASDAQ-futures bot workflow and suggests no Kalshi rule to test. Its "coin-flip test" (compare against
  random entries with the same exits) is a check we don't run yet.

## 2026-10-20 afternoon (BOS @ DET NBA opener)

- **Result: no bet placed.**
  - The user asked for picks. Every source (Kalshi, Polymarket, DraftKings) agreed within about 1c on winner, spread
    and total.
  - A Pistons ticket (6 @ 48c) was written. Once DraftKings' current line was found (BOS -122 / DET +102), it was no
    longer recommended.
- **Live-order path the user approved:** `reports/output/picks/approve.py <ticket>`. It's a dry run unless `--send`.
  - Limits: moneyline/spread series, buys only, IOC, at most 10 contracts, $3 per ticket and $3 total, 30-min expiry,
    one shot.
  - The user approves with the user-only `/send <ticket>` skill (.claude/skills/send), or with `!` plus `--send`.
  - Claude never sends on its own.
- **Research:**
  - #24, when to bet (NBA 1,317 and NFL 360 games): FAIL, all 42 versions.
  - #25, in-game locks: 14 in the first 25 min, all lasting 0.02 s or less. Full-game tally pending.
- **Tomorrow:** the NBA 26OCT21 recorder is already running (11 games).

## Recording schedule for the maker test (#26) and #23 (NBA gate: 20 games, 10 unseen)

- **26OCT20:** 3 games (BOSDET, PHINYK, OKCSAS), recording, with margin buckets.
- **26OCT21:** 11 games, recording, with margin buckets.
- **From 26OCT22 on, NBA recording is automatic.** Windows scheduled task **"KalshiPM NBA recorder"** (created
  2026-10-21 at the user's request) runs `scripts/nightly_nba_record.ps1` daily at 16:00 ET.
  - It lists that ET date's KXNBA markets (all series, including *PROT) into `data/ws/nba/<DATE>/markets.json`. Any
    old list is backed up as `.bak-<time>`.
  - It starts **one** read-only recorder (tag `nba-<DATE>`) until 09:00Z the next day.
  - It does nothing if that night's recorder is already running or there are no games.
  - Log: `logs/nightly_record_<DATE>.log`. Delete with `schtasks /Delete /TN "KalshiPM NBA recorder" /F`.
  - The PC must be on, awake and logged in at 16:00 ET.
  - Check each morning that the log says "started" and that the heartbeat `logs/ws_recorder-nba-<DATE>.heartbeat`
    is fresh.
- **26OCT22:** 2 games (CLEPHI, DENOKC). The task re-lists markets at 16:00 ET, by which time WINMARGIN should be
  listed. The **NFL 26OCT22 restart (#22) is still manual**: STOP file, wait for exit, `kpm nfl-markets --date
  26OCT22`, restart.
- **#33 (cheap-leg) gate:** run `cheapleg.py --count-only` after each night. The test runs once, when the unseen half
  has 200 or more primary (40 ms) fills (amendment 7).

## Ohio trial (#27): what to run each NBA night

- **Live lock bots (user-started):** `lockbot.py --live`, one per game on the server.
  - Stop: `touch ~/kpm/logs/lockbot_<date>_<TAG>.STOP`.
  - Dashboard (read-only, local): `bot_dashboard.py --date <date>` at http://127.0.0.1:8767.
  - **The user's allow rules (2026-10-21)**, for the two read-only monitor scripts:
    `Bash(bash reports/output/ohio_trial/watch_trades.sh:*)` and `Bash(bash reports/output/ohio_trial/bot_status.sh:*)`.
    **Don't edit those scripts without telling the user first**, since the rules trust their contents.
  - Shards: KXNBAGAME is on exchange 0 and every other KXNBA series is on exchange 3. API orders need cash on that
    shard (`reports/output/picks/shard_transfer.py`, run by the user).

- **Server:** EC2 `kalshi-ohio`, i-094e9277b9ca0d405, us-east-2c.
  - Address: 18.223.21.99.
  - SSH: `C:\Windows\System32\OpenSSH\ssh.exe -i ~/.ssh/kalshi-ohio.pem ubuntu@18.223.21.99`.
  - Code is in `~/kpm` (venv `.venv`), the env file is `~/kpm/.env`, and the key is `~/kpm/secrets/kalshi.key`.
  - It uses the user's existing Kalshi key (their choice, 2026-10-20).
  - **Cost about $16/month. Terminate it when the trial ends.**
- **Nightly automatic start (cron, 2026-10-20):** `30 21 * * * /home/ubuntu/kpm/run_night.sh`.
  - At 21:30Z (5:30 PM ET) it starts the read-only all-games watcher for the US-Eastern date, unless one is
    already running for that date.
  - Weekend games tipping before 5:30 PM ET are not covered from tip-off.
  - Disk at setup: 12 GB free; logs about 6 MB per night so far.
- **Server logs:** `~/kpm/logs/lockwatch_night_<date>_ohio.*`. Stop with `touch` on the matching `.STOP` file.
- **Budget alarm:** "Kalshi trial 20 dollar cap", $20/month, created 2026-10-20 ~21:00Z. It alerts the user's
  email at 85% and 100% of actual spend, and on the 100% forecast.

- **Watcher:** `reports/output/ohio_trial/lockwatch_night.py --date <26OCTdd> --tag home`, started detached at home
  about 3 h before the first tip so props are listed. Run the same with `--tag ohio` on the server once it's up.
- **Report:** `lock_report.py --logs <both jsonl> --reaction-ms <server R>`.
- **Waiting on the user:**
  - AWS setup (docs/OHIO_SERVER_SETUP.md);
  - whether locks may include props (default: non-prop only), decided before the first server night.

## Waiting on the user

- How to count the LIP gap (see above).
- Approval to add the **Gemini Predictions API**, the backup venue for LIP-style or cross-venue tests.
- Any non-NOAA weather source (Iowa Mesonet, Open-Meteo, The Weather Company) needs explicit approval.
- Whether to **commit to git**. Nothing is committed yet; the whole repo is untracked. Commit only when asked.

## Loose ends from earlier

- **BTC live micro-bot** (`src/kpm/execution`, run with `kpm bot run`): it crashed with a disk I/O error after hitting its 300-order cap.
  $0.88 of the $5 account-wide loss cap is used. Don't restart it. Live mode needs the user to type `LIVE PROD 5`
  themselves.
- **Clock:** fixed 2026-10-20 19:53Z. The Windows Time service had been stopped; the user set it to Automatic,
  started it and resynced (source time.windows.com, poll every 1024 s). If the clock drifts again, check
  `w32tm /query /status`.

## Rules that must not be broken (full text in the `read-only-research-rules` memory)

- Research is read-only and GET-only. Never call Kalshi order, portfolio, fills, positions or balance endpoints;
  `clients/http.py` blocks them. The one exception is the user's own fills and positions, read through ExecClient
  with live=False.
- Use official APIs only: Kalshi, Polymarket (Gamma/CLOB/Data/US), Coinbase public candles and NOAA.
  No scraping, no VPNs.
- Never print secrets from `.env`. Ask before any paid service, including the Claude API.
- Cache every raw response to disk. Pre-register before looking at data. Report in plain English with dollars.
- Model roles: Opus orchestrates and checks, Sonnet writes code, Haiku scouts. Never Fable.

## Key files

| Area | Files |
|---|---|
| Weather study (done, FAIL) | `reports/output/weather_calibration/README.md`, `PREREGISTRATION.md`, `w6_recheck.py`, `src/kpm/analysis/weather_calib.py`, `data/weather.sqlite` |
| LIP test | `reports/output/mechanical_edges/PREREGISTRATION.md` (H4 step 2 amendment), `src/kpm/collectors/lip_forward.py`, `src/kpm/analysis/lip_forward.py`, `src/kpm/analysis/lip_rewards.py`, `tests/test_lip_forward.py`, `data/lip_forward.sqlite`, raw data in `data/raw/lip_forward/` |
| NFL study (done, FAIL) | `reports/output/nfl_spread_arb/README.md` |
| Tests | `python -m pytest`: 2781 passed at last run |

## Resuming

```powershell
cd C:\Users\Jarvis\Documents\Kalshi-PM
claude --continue        # or start fresh and say: "read docs/HANDOFF.md"
```
