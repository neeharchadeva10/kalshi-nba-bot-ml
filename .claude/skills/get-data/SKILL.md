---
name: get-data
description: Step 2 of the funnel — get real historical Kalshi data (and other approved free sources) for a series or idea, cached to disk, so it can be backtested. Use when an idea needs data that isn't in data/ yet.
argument-hint: "<series ticker or idea, e.g. KXBTC15M or 'NBA game winners'>"
---

# Step 2 — Get real data

Target: $ARGUMENTS

His version bought 16 years of 1-minute NASDAQ data. Here the data is free: Kalshi's public API has settled markets,
1-minute candles (bid/ask) and every trade print.

1. **Check what we already have.** Run `.venv\Scripts\python.exe -m kpm.cli status` and look in `data/` (for example
   `data/weather.sqlite`, `data/lip_forward.sqlite`, the crypto and NFL databases). Reuse before downloading.
2. **Find the series.** Use the public `/series` and `/markets` endpoints through `kpm.clients.kalshi.KalshiClient`.
   Markets settled before the historical cutoff are only on `/historical/*`. The client handles this.
3. **Pick the minimum data that answers the question:**
   - Settled markets with result, open/close times and strike: always.
   - 1-minute candles (closing bid and ask): for any entry-price rule.
   - Trade prints (`/markets/trades`, `/historical/trades`): needed to prove fills (step 3).
   - Order books: only available live, so record them going forward (see `/paper-trade`).
4. **Rules for the collector:**
   - GET only, through `kpm.clients.http.CachedClient` (every raw response is cached, rate-limited, with 429 backoff).
   - Resumable: a `done` table, so a crash or reboot loses nothing.
   - Estimate the request count and the time at about 2.5 requests per second before starting, and tell the user.
   - Long downloads run detached:
     `Start-Process -WindowStyle Hidden .venv\Scripts\python.exe -ArgumentList '-m','kpm.cli','<cmd>' -RedirectStandardOutput logs\<cmd>.out -RedirectStandardError logs\<cmd>.err`
   - Free RAM is tight (about 1.5 GB), so stream to SQLite. Don't hold everything in memory.
5. **Other sources** (official APIs only, no scraping): Polymarket Gamma/CLOB/Data/US (read-only reference), Coinbase
   public candles, NOAA (api.weather.gov, NCEI, NOAA Open Data on AWS). Anything else (Iowa Mesonet, Open-Meteo, any
   paid feed such as Databento) needs the user's OK first.
6. A new collector is code: Sonnet writes it, Opus reviews it, and it gets tests in `tests/`.
7. Finish with: what was downloaded (series, markets, date range), any gaps, and where it lives.
