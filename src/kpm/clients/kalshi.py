"""Kalshi read-only market data client (live + historical tiers).

Docs: https://docs.kalshi.com  -- market data endpoints used here need no auth.
Markets settled before GET /historical/cutoff -> market_settled_ts live in the
historical tier and must be read from /historical/* endpoints.
"""
from __future__ import annotations

import sqlite3
from typing import Iterator

from kpm.clients.http import CachedClient
from kpm.core.config import settings
from kpm.core.timeutil import parse_iso


class KalshiClient:
    def __init__(self, conn: sqlite3.Connection | None = None):
        cfg = settings()["kalshi"]
        self.cfg = cfg
        self.http = CachedClient(cfg["base_url"], "kalshi", cfg["min_request_interval_s"], conn=conn)
        self._cutoff: dict | None = None

    # The cutoff moves over time, so it is never served from cache.
    def cutoff(self) -> dict:
        if self._cutoff is None:
            self._cutoff = self.http.get("/historical/cutoff", use_cache=False)
        return self._cutoff

    def is_historical(self, settled_ts: int | None) -> bool:
        cut = parse_iso(self.cutoff()["market_settled_ts"])
        return settled_ts is not None and settled_ts < cut

    def trades_historical(self, ts: int) -> bool:
        return ts < parse_iso(self.cutoff()["trades_created_ts"])

    def series(self, ticker: str) -> dict:
        return self.http.get(f"/series/{ticker}")["series"]

    def _paginate(self, path: str, key: str, params: dict, use_cache: bool = True) -> Iterator[dict]:
        cursor = None
        while True:
            page = self.http.get(path, {**params, "cursor": cursor}, use_cache=use_cache)
            yield from page.get(key, [])
            cursor = page.get("cursor")
            if not cursor:
                return

    def markets(self, series_ticker: str, historical: bool, **filters) -> Iterator[dict]:
        path = "/historical/markets" if historical else "/markets"
        # Live listings change; only cache historical (immutable) pages.
        yield from self._paginate(path, "markets", {"series_ticker": series_ticker, "limit": 1000, **filters},
                                  use_cache=historical)

    def candlesticks(self, series_ticker: str, ticker: str, start_ts: int, end_ts: int,
                     interval_min: int, historical: bool) -> list[dict]:
        """Fetch candles in chunks that respect the per-request candle limit."""
        step = self.cfg["max_candles_per_request"] * interval_min * 60 - interval_min * 60
        path = (f"/historical/markets/{ticker}/candlesticks" if historical
                else f"/series/{series_ticker}/markets/{ticker}/candlesticks")
        out: dict[int, dict] = {}
        s = start_ts
        while s < end_ts:
            e = min(s + step, end_ts)
            page = self.http.get(path, {"start_ts": s, "end_ts": e, "period_interval": interval_min})
            for c in page.get("candlesticks", []):
                out[c["end_period_ts"]] = c
            s = e
        return [out[k] for k in sorted(out)]

    def trades(self, ticker: str, min_ts: int, max_ts: int, historical: bool) -> Iterator[dict]:
        path = "/historical/trades" if historical else "/markets/trades"
        yield from self._paginate(path, "trades", {"ticker": ticker, "min_ts": min_ts, "max_ts": max_ts,
                                                   "limit": 1000})
