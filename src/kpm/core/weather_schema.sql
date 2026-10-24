-- Kalshi daily temperature markets calibration study (data/weather.sqlite). Separate from data/kpm.sqlite.
-- Prices are YES prices; *_c / *_o columns are dollars x 10000 as integers (c = candle close, o = candle open).
-- Times are UTC epoch seconds. `day` is the measured (local calendar) day parsed from the event ticker.

CREATE TABLE IF NOT EXISTS wx_series (
    ticker      TEXT PRIMARY KEY,
    title       TEXT,
    kind        TEXT NOT NULL,          -- 'high' | 'low'
    city        TEXT NOT NULL,          -- from the hand-written table in collectors/kalshi_weather.py
    station     TEXT,                   -- NWS CLI code parsed from the rules text, e.g. CLINYC (NULL if none)
    tz          TEXT NOT NULL,          -- IANA time zone of the city (hand-written table)
    fee_type    TEXT,
    frequency   TEXT,
    raw_json    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wx_markets (
    mid             INTEGER PRIMARY KEY,
    ticker          TEXT NOT NULL UNIQUE,
    series          TEXT NOT NULL,
    event_ticker    TEXT NOT NULL,
    kind            TEXT NOT NULL,
    city            TEXT NOT NULL,
    tz              TEXT NOT NULL,
    day             TEXT,               -- YYYY-MM-DD measured day (from event ticker, e.g. 26OCT14)
    strike_type     TEXT,               -- 'between' | 'greater' | 'less'
    floor_strike    REAL,
    cap_strike      REAL,
    strike_src      TEXT,               -- 'api' (strike fields) | 'rules' (parsed from rules text; old markets) | 'none'
    open_ts         INTEGER,
    close_ts        INTEGER,
    settled_ts      INTEGER,
    result          TEXT,               -- 'yes' | 'no' | 'scalar' (settled at a fractional value) | NULL
    settlement_value REAL,              -- dollars per YES contract
    volume          REAL,
    source          TEXT NOT NULL,      -- 'TWC' (The Weather Company) | 'NWS' (Climatological Report) | 'OTHER'
    rules_primary   TEXT,
    rules_secondary TEXT,
    data_tier       TEXT NOT NULL,      -- 'historical' | 'live' (which endpoint family served the market)
    raw_json        TEXT NOT NULL       -- market JSON without rules_*/price_ranges/early_close_condition (kept in columns)
);
CREATE INDEX IF NOT EXISTS ix_wx_markets_event ON wx_markets(event_ticker);
CREATE INDEX IF NOT EXISTS ix_wx_markets_series ON wx_markets(series, day);

-- One row per 1-minute candle Kalshi returned (sparse: only minutes where the book or trades changed; nothing is filled).
-- NULL = that side of the book was empty (bid reported as 0 / ask reported as 1).
CREATE TABLE IF NOT EXISTS wx_quotes (
    mid     INTEGER NOT NULL,
    ts      INTEGER NOT NULL,           -- candle end_period_ts (end of the minute)
    bid_c   INTEGER,
    ask_c   INTEGER,
    bid_o   INTEGER,
    ask_o   INTEGER,
    volume  REAL,
    PRIMARY KEY (mid, ts)
) WITHOUT ROWID;

-- Resumability: one row per finished (ticker, step), step = 'quotes'.
CREATE TABLE IF NOT EXISTS wx_done (
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
