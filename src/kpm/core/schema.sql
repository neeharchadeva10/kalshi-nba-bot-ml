-- KPM schema. All *_ts columns are integer UTC epoch seconds. Prices are dollars in [0, 1].
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS games (               -- nflverse reference
  game_id        TEXT PRIMARY KEY,
  season         INTEGER NOT NULL,
  week           INTEGER NOT NULL,
  game_type      TEXT NOT NULL,                  -- REG, WC, DIV, CON, SB
  home_team      TEXT NOT NULL,
  away_team      TEXT NOT NULL,
  kickoff_ts     INTEGER NOT NULL,
  home_score     INTEGER,
  away_score     INTEGER,
  spread_line    REAL,
  total_line     REAL,
  home_moneyline INTEGER,
  away_moneyline INTEGER,
  home_fair_prob REAL,                           -- de-vigged closing moneyline
  split          TEXT,                           -- dev | holdout
  fetched_ts     INTEGER NOT NULL
);

-- One row per tradable contract that pays $1 if a specific outcome happens.
-- Kalshi: one per market ticker. Polymarket: one per outcome token (2 per market).
CREATE TABLE IF NOT EXISTS markets (
  market_id          INTEGER PRIMARY KEY,
  platform           TEXT NOT NULL CHECK (platform IN ('kalshi','polymarket','polymarket_us')),
  contract_key       TEXT NOT NULL,              -- Kalshi ticker | Polymarket token_id | Polymarket US slug:side
  platform_market_id TEXT NOT NULL,              -- Kalshi ticker | Polymarket condition_id
  event_key          TEXT,                       -- Kalshi event_ticker | Polymarket event slug
  series_key         TEXT,
  title              TEXT,
  subtitle           TEXT,
  description        TEXT,
  rules_primary      TEXT,
  rules_secondary    TEXT,
  outcome_label      TEXT,
  team_abbr          TEXT,                       -- normalized nflverse code; YES = this team wins
  game_id            TEXT REFERENCES games(game_id),
  open_ts            INTEGER,
  close_ts           INTEGER,
  settled_ts         INTEGER,
  status             TEXT,
  result             TEXT,                       -- yes | no | split | void | NULL
  settlement_value   REAL,
  data_tier          TEXT,                       -- live | historical
  price_resolution_s INTEGER,                    -- measured median spacing of stored prices
  price_source       TEXT,                       -- which series was stored for analysis
  raw_ref            TEXT,
  meta_json          TEXT,                       -- venue-specific extras (kickoff hints, market type)
  fetched_ts         INTEGER NOT NULL,
  UNIQUE (platform, contract_key)
);
CREATE INDEX IF NOT EXISTS ix_markets_game ON markets(game_id);

-- Bars / snapshots. Phase 1 writes 1-minute Kalshi candles and Polymarket points.
-- Phase 2 live monitors write here too (source='kalshi_ws', 'poly_ws', interval_s=0).
CREATE TABLE IF NOT EXISTS price_history (
  market_id     INTEGER NOT NULL REFERENCES markets(market_id),
  ts            INTEGER NOT NULL,                -- bar END time
  interval_s    INTEGER NOT NULL,
  source        TEXT NOT NULL,                   -- kalshi_candle | poly_prices_history | poly_trades_1m
  yes_bid       REAL,
  yes_ask       REAL,
  yes_bid_low   REAL,
  yes_ask_high  REAL,
  price_last    REAL,
  price_mean    REAL,
  volume        REAL,
  open_interest REAL,
  PRIMARY KEY (market_id, ts, interval_s, source)
) WITHOUT ROWID;

-- Individual prints (live system; also small historical pulls).
CREATE TABLE IF NOT EXISTS trades (
  trade_id   TEXT PRIMARY KEY,
  market_id  INTEGER NOT NULL REFERENCES markets(market_id),
  ts         INTEGER NOT NULL,
  yes_price  REAL NOT NULL,
  no_price   REAL,
  count      REAL NOT NULL,
  taker_side TEXT
);
CREATE INDEX IF NOT EXISTS ix_trades_mkt_ts ON trades(market_id, ts);

-- Historical prints aggregated per minute and price level (keeps the DB small;
-- raw prints remain in the gzip response cache). Used for fill-realism checks.
CREATE TABLE IF NOT EXISTS trades_1m (
  market_id  INTEGER NOT NULL REFERENCES markets(market_id),
  minute_ts  INTEGER NOT NULL,                   -- minute END time, aligns with candles
  yes_price  REAL NOT NULL,
  taker_side TEXT NOT NULL,
  count      REAL NOT NULL,
  n_trades   INTEGER NOT NULL,
  PRIMARY KEY (market_id, minute_ts, yes_price, taker_side)
) WITHOUT ROWID;

-- Prints in the last seconds before a market's close, aggregated per second left (final-minute settlement test).
CREATE TABLE IF NOT EXISTS trades_final (
  market_id  INTEGER NOT NULL REFERENCES markets(market_id),
  sec_left   INTEGER NOT NULL,                   -- ceil(close - print time), seconds
  yes_price  REAL NOT NULL,
  taker_side TEXT NOT NULL,
  count      REAL NOT NULL,
  n_trades   INTEGER NOT NULL,
  PRIMARY KEY (market_id, sec_left, yes_price, taker_side)
) WITHOUT ROWID;

-- Live/watch-only bot state, shared with the localhost dashboard. Times are epoch seconds (float, UTC).
CREATE TABLE IF NOT EXISTS bot_runs (
  run_id      TEXT PRIMARY KEY,
  strategy    TEXT NOT NULL,                   -- 'late_favorite' | 'pair_h2'
  mode        TEXT NOT NULL,                   -- 'watch' (paper only) | 'live'
  started_ts  REAL NOT NULL,
  ended_ts    REAL,
  end_reason  TEXT,
  config_json TEXT                             -- caps and strategy settings in force (never secrets)
);
CREATE TABLE IF NOT EXISTS bot_heartbeat (     -- one row per run, overwritten every loop
  run_id      TEXT PRIMARY KEY,
  ts          REAL NOT NULL,
  status      TEXT NOT NULL,                   -- waiting | monitoring | trading | stopped | error
  detail_json TEXT                             -- current hour, window end, loss used, balance, last error...
);
CREATE TABLE IF NOT EXISTS bot_events (
  id        INTEGER PRIMARY KEY,
  run_id    TEXT,
  ts        REAL NOT NULL,
  level     TEXT NOT NULL,                     -- info | warn | error
  kind      TEXT NOT NULL,                     -- start | window | opportunity | order | fill | unwind | cap | kill | error
  message   TEXT NOT NULL,
  data_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_bot_events_ts ON bot_events(ts);
CREATE TABLE IF NOT EXISTS bot_quotes (        -- per-poll pair pricing, for the live chart
  run_id        TEXT NOT NULL,
  ts            REAL NOT NULL,
  hour_ticker   TEXT NOT NULL,                 -- KXBTC15M market ending on the hour
  strike_ticker TEXT NOT NULL,                 -- KXBTCD strike
  kind          TEXT NOT NULL,                 -- 'hourly YES + 15m DOWN' | '15m UP + hourly NO'
  leg_a_ask     REAL, leg_a_size REAL,
  leg_b_ask     REAL, leg_b_size REAL,
  cost          REAL, fees REAL, edge REAL,    -- edge = 1 - cost - fees (dollars per pair)
  PRIMARY KEY (run_id, ts, strike_ticker)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS bot_opportunities (
  id                 INTEGER PRIMARY KEY,
  run_id             TEXT NOT NULL,
  detected_ts        REAL NOT NULL,
  hour_ticker        TEXT NOT NULL,
  strike_ticker      TEXT NOT NULL,
  kind               TEXT NOT NULL,
  contracts          REAL NOT NULL,
  paper_cost         REAL, paper_fees REAL, paper_edge REAL,   -- at detection prices (the paper twin)
  live               INTEGER NOT NULL DEFAULT 0,
  outcome            TEXT,                     -- paper_only | both_filled | one_leg_unwound | one_leg_held | none_filled | blocked_by_cap | unknown | filled (late_favorite)
  live_cost          REAL, live_fees REAL,
  latency_ms         REAL,                     -- detection -> last leg acknowledged
  payout             REAL,                     -- filled after settlement
  realized_pnl       REAL,
  paper_realized_pnl REAL
);
CREATE TABLE IF NOT EXISTS bot_orders (
  client_order_id TEXT PRIMARY KEY,
  run_id          TEXT NOT NULL,
  opportunity_id  INTEGER,
  ts_sent         REAL, ts_ack REAL,
  ticker          TEXT NOT NULL,
  side            TEXT NOT NULL,                -- yes | no
  action          TEXT NOT NULL,                -- buy | sell
  count           REAL NOT NULL,
  limit_price     REAL NOT NULL,                -- dollars
  tif             TEXT,
  status          TEXT,
  order_id        TEXT,
  filled          REAL, avg_fill_price REAL, fees REAL,
  dry_run         INTEGER NOT NULL DEFAULT 1,
  response_json   TEXT                          -- exchange response (never request headers)
);

CREATE TABLE IF NOT EXISTS market_matches (
  match_id         INTEGER PRIMARY KEY,
  game_id          TEXT REFERENCES games(game_id),
  kalshi_market_id INTEGER NOT NULL REFERENCES markets(market_id),
  poly_market_id   INTEGER NOT NULL REFERENCES markets(market_id),
  same_outcome     INTEGER NOT NULL,             -- 1 = both YES pay when the same team wins
  rule_score       REAL NOT NULL,
  rule_features    TEXT,                         -- JSON
  status           TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|manual_review|rejected
  matcher_version  TEXT NOT NULL,
  created_ts       INTEGER NOT NULL,
  updated_ts       INTEGER NOT NULL,
  UNIQUE (kalshi_market_id, poly_market_id)
);

CREATE TABLE IF NOT EXISTS match_reviews (       -- AI and human reviews, append-only
  review_id              INTEGER PRIMARY KEY,
  match_id               INTEGER NOT NULL REFERENCES market_matches(match_id),
  reviewer               TEXT NOT NULL,          -- claude-code-session | claude-api:<model> | human
  same_event             INTEGER,
  settlement_differences TEXT,                   -- JSON list
  confidence             REAL,
  reason                 TEXT,
  raw_json               TEXT NOT NULL,
  prompt_hash            TEXT,
  created_ts             INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_runs (
  run_id       INTEGER PRIMARY KEY,
  created_ts   INTEGER NOT NULL,
  config_json  TEXT NOT NULL,
  code_version TEXT
);

CREATE TABLE IF NOT EXISTS gaps (
  run_id          INTEGER NOT NULL REFERENCES analysis_runs(run_id),
  match_id        INTEGER NOT NULL REFERENCES market_matches(match_id),
  ts              INTEGER NOT NULL,
  k_yes_bid       REAL,
  k_yes_ask       REAL,
  k_mid           REAL,
  k_age_s         INTEGER,
  p_ref           REAL,
  p_age_s         INTEGER,
  p_unchanged_s   INTEGER,                       -- seconds since the Polymarket price last changed
  fee_yes         REAL,
  fee_no          REAL,
  edge_yes        REAL,
  edge_no         REAL,
  mins_to_kickoff REAL,
  ttk_bucket      TEXT,
  k_volume_1h     REAL,
  k_spread        REAL,
  is_favorite     INTEGER,
  PRIMARY KEY (run_id, match_id, ts)
) WITHOUT ROWID;

-- External reference prices (e.g. Coinbase BTC-USD 1-minute bars) used as fair-value inputs.
CREATE TABLE IF NOT EXISTS reference_prices (
  source  TEXT NOT NULL,                         -- coinbase
  symbol  TEXT NOT NULL,                         -- BTC-USD
  ts      INTEGER NOT NULL,                      -- bar END time (aligns with Kalshi candles)
  open    REAL, high REAL, low REAL, close REAL, volume REAL,
  PRIMARY KEY (source, symbol, ts)
) WITHOUT ROWID;

-- Structural (no-prediction) mispricings found by the live scanner, one row per opportunity per scan.
CREATE TABLE IF NOT EXISTS scan_opportunities (
  id             INTEGER PRIMARY KEY,
  scan_ts        INTEGER NOT NULL,
  kind           TEXT NOT NULL,                  -- ladder_greater | ladder_less | sell_all | buy_all
  event_key      TEXT,
  series_key     TEXT,
  legs           TEXT,                           -- JSON list of {ticker, action, price, size}
  profit_per_set REAL,                           -- dollars per set after taker fees (worst case)
  max_sets       REAL,                           -- limited by the thinnest leg's displayed size
  profit_usd     REAL,
  verified       INTEGER,                        -- 1 = re-checked against live order books
  details        TEXT
);
CREATE INDEX IF NOT EXISTS ix_scan_ts ON scan_opportunities(scan_ts);

CREATE TABLE IF NOT EXISTS audit_log (
  id          INTEGER PRIMARY KEY,
  ts          INTEGER NOT NULL,
  component   TEXT NOT NULL,
  entity_type TEXT,
  entity_id   TEXT,
  action      TEXT NOT NULL,
  details     TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_done ON audit_log(component, action, entity_id);

CREATE TABLE IF NOT EXISTS raw_responses (       -- index of the on-disk response cache
  request_hash TEXT PRIMARY KEY,
  url          TEXT NOT NULL,
  params       TEXT,
  path         TEXT NOT NULL,
  http_status  INTEGER,
  fetched_ts   INTEGER NOT NULL
);

-- Execution ledger (kpm.execution.ledger). Prices, counts and money are TEXT decimal strings, never REAL; times are
-- epoch seconds (REAL) unless the name ends in _ms. Every row carries env (demo | prod) and nothing is ever compared
-- across environments. Request headers, key ids, signatures and key material are never stored.
CREATE TABLE IF NOT EXISTS exec_requests (       -- every request/response the execution client made (audit)
  id              INTEGER PRIMARY KEY,
  run_id          TEXT NOT NULL,
  env             TEXT NOT NULL,
  live            INTEGER NOT NULL DEFAULT 0,      -- 0 = dry-run client
  ts              REAL NOT NULL,
  ts_ms           INTEGER,                         -- client clock when the request was sent
  method          TEXT NOT NULL,
  path            TEXT NOT NULL,                   -- signed path (/trade-api/v2/...): no host, no query
  kind            TEXT,                            -- public | read | write
  attempt         INTEGER,
  params_json     TEXT,
  body_json       TEXT,
  client_order_id TEXT,                            -- comma-separated for a batch
  status          INTEGER,                         -- HTTP status; NULL when the request never got a response
  outcome         TEXT,                            -- ok | rejected | duplicate | throttled | unknown | auth
  response        TEXT,                            -- cut at 64 KB
  latency_ms      REAL,
  error           TEXT,                            -- error code or exception class
  extra_json      TEXT
);
CREATE INDEX IF NOT EXISTS ix_exec_requests_coid ON exec_requests(client_order_id);
CREATE TABLE IF NOT EXISTS exec_orders (         -- one row per order intent, committed BEFORE the request is sent
  client_order_id TEXT PRIMARY KEY,
  run_id          TEXT NOT NULL,
  env             TEXT NOT NULL,
  created_ts      REAL NOT NULL,
  updated_ts      REAL NOT NULL,
  ticker          TEXT NOT NULL,
  exchange_index  INTEGER,                         -- the market's shard; every cancel carries it
  side            TEXT NOT NULL,                   -- bid | ask (YES book side, from units.to_book)
  price           TEXT NOT NULL,                   -- YES price, 4 dp
  count           TEXT NOT NULL,                   -- contracts, 2 dp
  outcome         TEXT,                            -- yes | no: the outcome bought
  outcome_price   TEXT,
  time_in_force   TEXT NOT NULL,
  reduce_only     INTEGER NOT NULL DEFAULT 0,
  purpose         TEXT,                            -- entry | retry | exit ...
  group_id        TEXT,                            -- e.g. the bot_opportunities id of the pair
  status          TEXT NOT NULL,                   -- intent | acked | resting | executed | canceled | rejected |
                                                   -- duplicate | throttled | unknown | presumed_not_placed | auth_error
  order_id        TEXT,
  fill_count      TEXT,
  remaining_count TEXT,
  avg_fill_price  TEXT,
  avg_fee_paid    TEXT,                            -- per contract (create response)
  taker_fees      TEXT,                            -- order totals (GET /portfolio/orders)
  maker_fees      TEXT,
  ts_ms           INTEGER,                         -- matching-engine time of the write response
  sent_ts         REAL,
  result_ts       REAL,
  error           TEXT,
  body_json       TEXT NOT NULL                    -- the exact request body (never headers)
);
CREATE INDEX IF NOT EXISTS ix_exec_orders_order ON exec_orders(order_id);
CREATE INDEX IF NOT EXISTS ix_exec_orders_ticker ON exec_orders(env, ticker);
CREATE TABLE IF NOT EXISTS exec_fills (          -- deduplicated on fill_id; joined to orders by order_id
  fill_id         TEXT PRIMARY KEY,
  env             TEXT NOT NULL,
  run_id          TEXT,
  order_id        TEXT,
  client_order_id TEXT,
  ticker          TEXT NOT NULL,
  exchange_index  INTEGER,
  outcome_side    TEXT NOT NULL,                   -- yes | no (+count / -count on the position)
  book_side       TEXT,                            -- bid | ask
  count           TEXT NOT NULL,
  yes_price       TEXT NOT NULL,
  no_price        TEXT NOT NULL,                   -- 1 - yes_price when the source omits it (WS fills)
  is_taker        INTEGER,
  fee_cost        TEXT NOT NULL,                   -- this fill's fee in dollars: the only fee counted in P&L
  created_ts      REAL,
  source          TEXT,                            -- rest | ws
  raw_json        TEXT
);
CREATE INDEX IF NOT EXISTS ix_exec_fills_ticker ON exec_fills(env, ticker);
CREATE INDEX IF NOT EXISTS ix_exec_fills_order ON exec_fills(order_id);
CREATE TABLE IF NOT EXISTS exec_settlements (    -- GET /portfolio/settlements rows, persisted (no historical copy)
  env             TEXT NOT NULL,
  ticker          TEXT NOT NULL,
  event_ticker    TEXT,
  exchange_index  INTEGER,
  market_result   TEXT,                            -- yes | no | scalar
  yes_count       TEXT,
  yes_total_cost  TEXT,
  no_count        TEXT,
  no_total_cost   TEXT,
  revenue_cents   INTEGER,                         -- integer CENTS as sent by the API
  revenue         TEXT,                            -- dollars = revenue_cents / 100
  value_cents     INTEGER,                         -- payout of one YES contract in cents (nullable)
  fee_cost        TEXT,                            -- sum of the trade fees already on the fills: never added again
  settled_ts      REAL,
  run_id          TEXT,
  pnl             TEXT,                            -- realized P&L in dollars when recorded
  pnl_basis       TEXT,                            -- fills | settlement
  check_status    TEXT,                            -- ok | mismatch: ...
  raw_json        TEXT,
  recorded_ts     REAL NOT NULL,
  PRIMARY KEY (env, ticker)
);
