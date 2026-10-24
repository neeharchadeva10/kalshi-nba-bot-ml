# Kalshi NFL totals download (research log #66) - read-only, data only

Script: `download_kalshi_nfl_totals.py` (tests: `test_kalshi_nfl_totals.py`).
Public GETs only through `kpm.clients.kalshi.KalshiClient` / `CachedClient` (trading-path guard on, all responses
cached under `data/raw/kalshi`). No auth, no `kpm.execution`. No betting-outcome statistics are computed.

## Run
Smoke test (<= 50 requests, writes to `smoke/`):
    .venv\Scripts\python.exe reports\output\nfl_totals\kalshi\download_kalshi_nfl_totals.py --smoke
Full run (waits for the time given, then downloads; safe to re-run, it resumes from `done.jsonl` + the HTTP cache):
    .venv\Scripts\python.exe reports\output\nfl_totals\kalshi\download_kalshi_nfl_totals.py --not-before-utc 2026-10-24T02:00:00Z
Options: `--max-requests N` (hard cap on real HTTP requests, cache hits free; exits code 3 when hit),
`--stages series,markets,candles,check`, `--series ...`, `--passes N` (retry passes for failed candle fetches).
Rerun after more games have been played to pick up their candles (games whose kickoff+15min+1h is still in the
future are skipped as `not_yet_played`).

## Outputs (in this folder)
series_meta.json, markets.parquet, events.parquet (event -> nflverse game), team_map.json, candles_1h.parquet,
candles_1m.parquet, coverage.parquet, done.jsonl (done-table), CHECK.md, check.json, raw/ (all raw pages / candles).
Candle parquet columns: ticker, end_ts, tier, raw flattened API fields as returned (e.g. `yes_bid_close_dollars`
strings) plus parsed floats `*_f` (yes_bid/yes_ask/price open|high|low|close|mean|previous, volume_f, open_interest_f).
Candles are sparse: only periods the API returned.

## Rules implemented literally
- Hourly candles: [max(market open, kickoff-10d), kickoff+15min]. 1-minute candles: [kickoff-6h, kickoff+15min].
  Candles ending after kickoff (up to +15 min) are therefore IN-PLAY data: analysis must cut at kickoff itself.
- Markets opened after kickoff+15min (Kalshi creates new strikes in-game, e.g. KXNFLTOTAL-26OCT22NECHI-76 opened
  ~2h after kickoff) get an empty window and no candles (recorded as `empty_window` in done.jsonl).
- Tier: live vs `/historical` is taken from the listing the market came from (cutoff 2026-08-24 for settled markets);
  on a 404 the other endpoint is tried.
- Kickoff = nflverse gameday + gametime read as America/New_York (zoneinfo, DST-safe).
- Settlement bracket: result YES means total > floor_strike. For each settled KXNFLTOTAL game the highest YES strike
  must be < nflverse home+away score (which includes overtime) and the lowest NO strike must be >= it.

## Team-code map (Kalshi event-ticker code -> nflverse), derived from the data
Identical for 30 codes. Differences: JAC -> JAX, LAR -> LA (Kalshi uses BOTH LA and LAR for the Rams: e.g.
26JAN04ARILA vs 26AUG22NOLAR). WAS appears as WAS (WSH, STL, SD, OAK are defined as aliases but were not observed). Full map as observed: team_map.json.

## Matching
Kalshi event `KXNFLTOTAL-26OCT22NECHI`: date = ET gameday, block = <away><home>. The block is split into two valid
codes and matched to the nflverse game with the same ET date and the same unordered team pair (then date +-1 day as
fallback, recorded in `method`). Away/home order mismatches are listed in CHECK.md. Unmatched events (the August
preseason games are not in nflverse) get no candles.

## Known facts from the smoke test
- Series fees: KXNFLTOTAL = quadratic_with_maker_fees, multiplier 1 (differs from the other three, which are
  plain `quadratic`, multiplier 1). The 0.07 coefficient is the repo's config, not in the series metadata.
- Series metadata has no overtime statement for full-game totals (2H series explicitly includes OT, 1H does not).
  The bracket check against nflverse totals (which include OT) agrees for the overtime games checked.
- `expiration_value` is NOT always the final total: markets that close early on YES carry the running total at
  that moment; many are empty, one Super Bowl event has 'Yes'/'No' strings. CHECK.md reports these separately.
