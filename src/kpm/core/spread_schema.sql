-- NFL winner vs spread arbitrage study (data/nfl_spread.sqlite). Separate from data/kpm.sqlite.
-- Prices are YES prices; *_e4 columns are dollars x 10000 as integers. Times are UTC epoch seconds.

CREATE TABLE IF NOT EXISTS sp_markets (
    mid             INTEGER PRIMARY KEY,
    ticker          TEXT NOT NULL UNIQUE,
    series          TEXT NOT NULL,          -- 'KXNFLGAME' | 'KXNFLSPREAD'
    kind            TEXT NOT NULL,          -- 'win' | 'spread'
    event_ticker    TEXT NOT NULL,
    game_id         TEXT NOT NULL,          -- games.game_id in data/kpm.sqlite
    season          INTEGER NOT NULL,
    week            INTEGER,
    game_type       TEXT,
    kickoff_ts      INTEGER NOT NULL,
    team            TEXT NOT NULL,          -- nflverse code (core.teams.norm_code) of the team YES refers to
    strike          REAL,                   -- spread: YES iff team wins by MORE than strike (floor_strike); NULL for win
    strike_type     TEXT,
    open_ts         INTEGER,
    close_ts        INTEGER,
    settled_ts      INTEGER,
    status          TEXT,
    result          TEXT,
    settlement_value REAL,                  -- dollars per YES contract when reported
    volume          REAL,
    data_tier       TEXT NOT NULL,          -- 'historical' | 'live'
    rules_primary   TEXT,
    rules_secondary TEXT,
    raw_json        TEXT NOT NULL,
    q_start_ts      INTEGER,                -- quote window requested from the candlestick endpoint
    q_end_ts        INTEGER
);
CREATE INDEX IF NOT EXISTS ix_sp_markets_game ON sp_markets(game_id);

-- One row per 1-minute candle: closing YES bid / ask of that minute. NULL = that side of the book was empty
-- (Kalshi reports an empty bid as 0 and an empty ask as 1; both are stored as NULL).
CREATE TABLE IF NOT EXISTS sp_quotes (
    mid     INTEGER NOT NULL,
    ts      INTEGER NOT NULL,               -- candle end_period_ts
    bid_e4  INTEGER,
    ask_e4  INTEGER,
    volume  REAL,
    PRIMARY KEY (mid, ts)
) WITHOUT ROWID;

-- Raw Kalshi trade prints, fetched only around candidate opportunities (for fill checks).
CREATE TABLE IF NOT EXISTS sp_trades (
    trade_id    TEXT PRIMARY KEY,
    ticker      TEXT NOT NULL,
    ts          REAL NOT NULL,              -- created_time
    yes_price   REAL NOT NULL,              -- dollars
    count       REAL NOT NULL,
    taker_side  TEXT NOT NULL               -- 'yes' | 'no'
);
CREATE INDEX IF NOT EXISTS ix_sp_trades ON sp_trades(ticker, ts);

-- Which (ticker, window) trade fetches are complete, so they are never repeated.
CREATE TABLE IF NOT EXISTS sp_trade_windows (
    ticker  TEXT NOT NULL,
    min_ts  INTEGER NOT NULL,
    max_ts  INTEGER NOT NULL,
    n       INTEGER NOT NULL,
    PRIMARY KEY (ticker, min_ts, max_ts)
);

-- Resumability: one row per finished (ticker, step), e.g. step = 'quotes'.
CREATE TABLE IF NOT EXISTS sp_done (
    ticker  TEXT NOT NULL,
    step    TEXT NOT NULL,
    ts      REAL NOT NULL,
    detail  TEXT,
    PRIMARY KEY (ticker, step)
);

-- Index of the on-disk raw API cache for requests made by this study (same shape as data/kpm.sqlite).
CREATE TABLE IF NOT EXISTS raw_responses (
    request_hash TEXT PRIMARY KEY,
    url          TEXT NOT NULL,
    params       TEXT,
    path         TEXT NOT NULL,
    http_status  INTEGER,
    fetched_ts   INTEGER NOT NULL
);
