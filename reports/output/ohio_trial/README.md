# Ohio trial: read-only all-games NBA lock watcher

Read-only. Public REST GETs plus one WebSocket (`subscribe` / `unsubscribe` on `orderbook_delta` only). No order,
portfolio, cancel or amend code. See `PREREGISTRATION.md` for the decision rule.

Files:
- `lockcore.py`: detection math, grouping, de-duplication and report logic (pure, no network).
- `lockwatch_night.py`: the watcher (one process, all games of a date).
- `lock_report.py`: pre-registration report over one or more watcher logs.
- `test_lockcore.py`: pytest, no network.

## Environment variable names (values never printed or logged)

The key loader is `kpm.execution.env.load_env` (reads the process environment, then `<repo>/.env`):
- `KALSHI_ENV` = `prod` (required, no default)
- `KALSHI_PROD_KEY_ID` (fallback name `KALSHI_API_KEY_ID`)
- `KALSHI_PROD_KEY_PATH` (fallback name `KALSHI_PRIVATE_KEY_PATH`): path to the PEM file, keep it outside the repo
- `KALSHI_PROD_KEY_PASSWORD`: only if the PEM is encrypted

On the server use the separate revocable key. Put these in `<repo>/.env` (copy `.env.example`; `chmod 600`) or export them.
Keys are needed only for the WebSocket handshake; the REST structure discovery is unauthenticated.

## Run at home (Windows)

    cd C:\Users\Jarvis\Documents\Kalshi-PM
    $env:KALSHI_ENV="prod"
    .venv\Scripts\python.exe reports\output\ohio_trial\lockwatch_night.py --date 26OCT21 --tag home

## Run on Ubuntu 24.04 (AWS us-east-2)

    sudo apt update && sudo apt install -y python3-venv python3-pip git
    git clone <repo> Kalshi-PM && cd Kalshi-PM
    python3 -m venv .venv && . .venv/bin/activate
    pip install -e ".[trading]"        # core deps plus cryptography and websockets
    cp .env.example .env && chmod 600 .env   # fill the KALSHI_* names above; PEM path outside the repo
    mkdir -p logs
    nohup python reports/output/ohio_trial/lockwatch_night.py --date 26OCT21 --tag ohio > logs/night_26OCT21_ohio.out 2>&1 &

Requires Python 3.11+ (the repo's `requires-python`; Ubuntu 24.04 ships 3.12). Open outbound 443 (REST, WSS) and UDP 123
(SNTP; if blocked the offset records show errors and delay stats stay uncorrected). Keep the system clock synced
(chrony or systemd-timesyncd, default on Ubuntu).

## Options

    --date 26OCT21        event date code (required)
    --prefix KXNBA        series prefix to discover
    --tag home|ohio       label in the output file names
    --until ISO|+60s|+5m  default 09:00Z the day after --date
    --out-dir logs        default <repo>/logs

Start it a few hours before tip-off or earlier; structure discovery takes 1 to 2 minutes (about 300 series, rate-limited)
and is cached 6 h in `<out-dir>/lockwatch_night_<date>_<tag>.structure.json`.

## Outputs (in `--out-dir`)

- `lockwatch_night_<date>_<tag>.jsonl`: records `start`, `lock_open`, `lock_close`, `delay` (every 100th delta:
  `recv_ns`, `ts_ms`), `sntp` (at start and every 10 min: `offset_ms` = true minus local), `heartbeat`, `seq_gap`, `error`.
  Lock records carry game, series, `mtype`, `is_prop`, kind, legs, prices, sizes, per-leg fees, n, profit after fees,
  `open_ns`/`close_ns` (`time.time_ns()` at receive), `open_ts_ms`/`close_ts_ms` (exchange `ts_ms` of the opening and
  closing message), `lifetime_ms`.
- `lockwatch_night_<date>_<tag>.heartbeat`: rewritten every 30 s.
- Stop early: create `lockwatch_night_<date>_<tag>.STOP` (or Ctrl-C).

## Report and tests

    python reports/output/ohio_trial/lock_report.py --logs logs/lockwatch_night_26OCT21_ohio.jsonl [more files] --reaction-ms 8
    python -m pytest -q reports/output/ohio_trial/test_lockcore.py

The report de-duplicates (locks sharing any leg and opening within 50 ms are one event, valued at its most profitable
lock), keeps events whose lock lived at least R + 2 ms, and prints value per night with and without props, value
excluding the biggest event, breakdowns by market type and lifetime bucket, and SNTP-corrected feed delay
(median / p90 / p99). It writes `lock_report_<first log stem>.json` next to the first log. Nights are the date codes in
the logs; pass all nights' logs in one call for the trial-wide mean and the excluding-biggest-event figure.
Reachability is judged on the event's most profitable lock (conservative: a longer-lived sibling lock is not substituted).
