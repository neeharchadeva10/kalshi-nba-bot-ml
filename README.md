# Kalshi vs Polymarket — NFL price-gap research

Phase 1 is a **read-only** historical study of one question: in the 2025 NFL season, how often did Kalshi's
price on game-winner markets differ from Polymarket's by more than Kalshi's fee, and when it did, did Kalshi
move toward Polymarket (and how fast)?

- Trading happens **only on Kalshi**. Polymarket is a public, read-only price reference: no wallet, no keys, no account.
- Only official, documented APIs are used: Kalshi Trade API v2 (public market data), Polymarket Gamma, CLOB and Data APIs.
- No Kalshi order, trading or portfolio endpoint is called by the research code. Its HTTP client is GET-only and refuses
  `/portfolio`, `/orders`, `/fills`, `/positions` and `/balance` paths, and nothing under `src/kpm/analysis`,
  `collectors` or `clients` imports the execution package.
- Live execution is a separate, opt-in package (`kpm.execution`, commands `kpm bot` and `kpm exec`, see
  "Live execution" below). It is watch-only unless every live gate passes.
- Every raw API response is cached gzip-compressed under `data/raw/` and never re-downloaded. 429 and 5xx
  responses are retried with exponential backoff, and `Retry-After` is honored.
- All timestamps are UTC epoch seconds. Prices are dollars in [0, 1].

## Setup

```powershell
# Python 3.11+ (3.12 used here)
py -3.12 -m venv .venv            # or: & "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
copy .env.example .env            # Phase 1 needs no credentials; leave values blank
```

Settings live in `config/settings.yaml`: season, split week, fee model, collection window, thresholds and
staleness limits. Secrets are read only from `.env`, which is gitignored and never printed or logged.

## Running each step

```powershell
.\.venv\Scripts\kpm init-db          # step 1: create SQLite schema (data/kpm.sqlite)
.\.venv\Scripts\kpm nfl              # step 4: nflverse schedule, UTC kickoffs, scores, closing lines (run first)
.\.venv\Scripts\kpm kalshi           # step 2: KXNFLGAME markets, rules, settlement, 1-min candles, trades
.\.venv\Scripts\kpm polymarket       # step 3: Gamma NFL game markets + rules, CLOB 1-min price history
.\.venv\Scripts\kpm match            # step 5a: rule-based pairing with confidence scores
.\.venv\Scripts\kpm review-export    # step 5b: write review packets -> review/review_packets/*.jsonl
#   ... AI review writes JSON results to review/review_results/*.json ...
.\.venv\Scripts\kpm review-import review\review_results   # store reviews, then gate automatically
.\.venv\Scripts\kpm gate             # step 5c: approve, or route to review/manual_review.csv
.\.venv\Scripts\kpm manual-import    # apply your yes/no decisions from the manual review CSV
.\.venv\Scripts\kpm gaps             # step 6: align series on a 1-min grid, compute after-fee edges
.\.venv\Scripts\kpm analyze          # steps 7-8: frequency, convergence, strategies, calibration, dev/holdout
.\.venv\Scripts\kpm report           # step 9: reports/output/report.md + charts
.\.venv\Scripts\kpm status           # row counts
.\.venv\Scripts\python -m pytest     # unit tests
```

Collectors are resumable. Each finished market is recorded in `audit_log`, and responses are cached, so
re-running a step skips completed work. Pass `--force` to rebuild.

## Live execution (opt-in)

Two strategies run on Kalshi's KXBTC15M (and, for the second, KXBTCD) markets. Both are **watch-only by default**: they
read public data, run the full detection, write paper trades and dashboard rows, and send nothing. Watch-only needs no
keys, only `KALSHI_ENV=prod` in `.env` (it names the environment; it is not a secret).

```powershell
.\.venv\Scripts\kpm bot run --strategy late_favorite --smoke   # one public pass (market, book, what the rule would do), then exit
.\.venv\Scripts\kpm bot run --strategy late_favorite           # watch-only, until Ctrl+C or the dashboard STOP button
.\.venv\Scripts\kpm bot run --strategy pair_h2 --hours 2       # the second strategy; --hours counts hourly windows for pairs
.\.venv\Scripts\kpm dashboard                                  # http://127.0.0.1:8765/  (localhost only; shows the strategy, P&L, STOP)
.\.venv\Scripts\kpm exec check --env prod                      # read-only health check of config, clock, shards, cash, resting orders
.\.venv\Scripts\kpm exec reconcile --env prod                  # read-only: ledger vs exchange, records settlements
.\.venv\Scripts\kpm exec kill                                  # writes data/KILL (every bot stops) and cancels what rests
```

**Going live** (`kpm bot run --strategy late_favorite --live --env prod`) needs ALL of these, checked at start and again
before every order (the clock gate is a measurement kept fresh during the run, see 3); any one failing sends nothing:

1. `trading.enabled: true` in `config/settings.yaml` (it ships as `false`), and the `--live` flag.
2. `KALSHI_ENV=prod` in `.env` and `--env prod` on the command line, with the production key pair in `.env`
   (`KALSHI_PROD_KEY_ID` / `KALSHI_PROD_KEY_PATH`, or the older `KALSHI_API_KEY_ID` / `KALSHI_PRIVATE_KEY_PATH`).
   The key file lives outside the repo; the key id, its path and signatures are never printed, logged or stored (only
   the 8-hex-digit fingerprint `key_fp` is shown).
3. No `data/KILL` file, the PC clock within 2 s of Kalshi's (median of 3 probes; measured at start and again every 10
   minutes while the bot runs, and an order is refused while the latest reading is off by more or is over 21 minutes old),
   the loss cap not used up, and no order with an unknown outcome (`kpm exec reconcile` resolves those).
4. The phrase `LIVE PROD 5` typed at an interactive terminal (no terminal: refused).

Only one live run at a time is allowed: a live start first takes an exclusive lock on `data/LIVE.lock` (released when the
process ends, so a crash leaves nothing to clean up), before it asks for the phrase. Hard limits live in code
(`kpm.execution.safety`; settings above a ceiling refuse to start): ONE account-wide loss cap of $5.00 (realized loss plus
open worst case), at most 1 contract and $1.00 of collateral per order, at most $3.00 open, IOC taker orders only, priced
exactly at the current ask of a fresh book. The two legs of a pair are checked together against these caps and the rate
limits. A 401/403 on any authenticated read stops the bot (kill file written, exit code 1); it never retries a rejected key.

- **late_favorite**: in the last 120 to 60 seconds before each KXBTC15M close, take the first fresh two-sided book and
  decide once: buy 1 YES at the ask if the YES mid is above 0.60, or 1 NO at the ask if it is below 0.40, otherwise skip the
  window (a later book that crosses a threshold is not traded); up to 3 immediate-or-cancel attempts, one filled trade per
  window, held to settlement. The rule is fixed in code. Historical expectation (11,237 trades,
  Jun to Oct 2026): it wins about 92.9% of the time and still loses about 1.2 cents per trade, so the $5 cap is expected to
  trip within days. Every trade is tracked twice, a paper twin at the detection price and the live fill, so the cost of
  order delay shows on the dashboard.
- **pair_h2**: the riskless hourly KXBTCD strike vs KXBTC15M pair (two IOC buys that together pay at least $1). Its trading
  window ends 45 s before the hour, before Safety stops accepting entries (30 s), so every pair it detects can be traded.

Everything sent to Kalshi is audited without secrets in `exec_requests`, every order intent is committed before it is
sent, and a write with an unknown outcome is looked up, never resent.

## Matching and review (step 5)

1. **Rules** (`src/kpm/agents/matcher/rules.py`) pair each Kalshi team contract with the Polymarket outcome
   token for the same team. The checks are: same two teams, ET game date, Polymarket `gameStartTime` vs
   nflverse kickoff, plausible Kalshi expiry, and binary/moneyline market types. The weighted score runs from 0 to 1.
2. **AI review**. Each pair's full titles, descriptions and resolution rules go into a review packet. The
   reviewer returns `{same_event, settlement_differences, confidence, reason}`, validated by
   `models.ReviewResult`.
   - Phase 1: the review was done by Claude Code inside the working session, with no API calls.
     `SessionBackend` exports packets and `review-import` stores the results.
   - Live system: `ClaudeAPIBackend` calls the Claude API with structured output (`claude-opus-5-5`). It
     refuses to run unless `matcher.allow_paid_api: true`. It's a paid service, so approve the cost first.
     Install the SDK with `pip install -e ".[claude]"`.
3. **Gate**. A pair is approved only if the rule score is ≥ 0.9, the AI says `same_event: yes`, and the AI
   confidence is ≥ 0.9. Everything else goes to `review/manual_review.csv` and is excluded until you set
   `approve` to `yes` and run `kpm manual-import`. Your decisions are stored as `human` reviews and always
   override the AI.
4. Every decision (scores, reviews, status changes) is written to the `audit_log` table and to `logs/matcher.jsonl`.

## Layout

```
config/settings.yaml        all tunables
src/kpm/core/               config, db + schema.sql, fees, prices, teams, time, audit
src/kpm/clients/            cached GET client, Kalshi, Polymarket, nflverse  (Phase 2 adds WebSocket clients)
src/kpm/collectors/         historical collection steps
src/kpm/agents/matcher/     matcher agent (rules, prompt, backends, pipeline)  (Phase 2/3: news, analyst, orchestrator)
src/kpm/analysis/           align, gaps, episodes, convergence, strategies, calibration, run_all
src/kpm/reports/            charts + report builder;  reports/narrative.md holds the written conclusions
src/kpm/execution/          live execution: units, auth, env, client, ledger, safety, reconcile, bots, CLI (see above)
src/kpm/dashboard/          localhost monitor (stdlib server + static page) and its STOP button
tests/                      fees, prices/time, gaps/episodes/convergence/strategy math, matching
data/                       raw cache + SQLite (gitignored)
review/                     manual_review.csv, review_results/*.json (the AI review record)
```

## Database (SQLite, `data/kpm.sqlite`)

| Table | Contents |
|---|---|
| `games` | nflverse schedule: UTC kickoff, scores, closing spread/total/moneylines, de-vigged home probability, dev/holdout split |
| `markets` | one row per contract that pays $1 on one team (Kalshi ticker or Polymarket token), with full rules text and settlement |
| `price_history` | 1-min Kalshi candles (bid/ask/last/volume/OI) and Polymarket price points; Phase 2 live snapshots go here too |
| `trades` / `trades_1m` | individual prints (live use) / historical Kalshi prints aggregated per minute and price, used for fill checks |
| `market_matches` | Kalshi↔Polymarket pairs, rule score and features, status |
| `match_reviews` | append-only AI and human reviews (JSON) |
| `analysis_runs`, `gaps` | config snapshot per run + per-minute after-fee edges |
| `audit_log` | every collector, matcher and analysis decision |
| `raw_responses` | index of the on-disk API cache |
| `bot_runs`, `bot_heartbeat`, `bot_events`, `bot_quotes`, `bot_opportunities`, `bot_orders` | what the bots show the dashboard: one row per run (`strategy`, `mode` watch or live), paper twins next to live fills |
| `exec_requests`, `exec_orders`, `exec_fills`, `exec_settlements` | the execution ledger: every request, order intent, fill and settlement, prices as exact text decimals |

## Fee model

`kpm.core.fees.FeeModel`: taker fee = `ceil_to_cent(0.07 × C × P × (1−P))`, maker = 25% of taker, no
settlement fee. The multiplier, maker fraction, rounding mode (`cent_ceil`, `micro_ceil` or `none`) and
simulated order size are all configurable. The KXNFLGAME series reports `fee_type: quadratic_with_maker_fees`
and `fee_multiplier: 1`. Kalshi's fee-rounding docs now describe sub-cent accounting, so check the current
schedule before the live phase.

## Later phases (structure is in place; the live-execution part of phase 3 is built, see above)

- **Phase 2**: live read-only monitoring via Kalshi and Polymarket WebSockets writing to `price_history`, a
  paper-trading log with CLV, alerts with manual approval, and a news agent (injuries/inactives) that can
  pause a game.
- **Phase 3**: Kalshi demo trading, then small live trades. Hard risk limits live in plain code: max order
  size, max daily loss, kill switch, cancel-all on crash or disconnect. An analyst agent and an orchestrator
  coordinate the agents. Agents can pause trading or flag issues, but can never place trades or override
  risk limits.
