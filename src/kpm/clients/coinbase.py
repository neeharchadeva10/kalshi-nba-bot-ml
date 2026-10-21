"""Coinbase Exchange public market data (no account): https://docs.cdp.coinbase.com/exchange
GET /products/{product}/candles?granularity=60&start&end  -> [time, low, high, open, close, volume], max 300 per call.
Coinbase `time` is the bar START; we store bar END times to line up with Kalshi candles.
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from kpm.clients.http import CachedClient
from kpm.core.config import settings
from kpm.core.timeutil import to_iso


class CoinbaseClient:
    def __init__(self, conn: sqlite3.Connection | None = None):
        cfg = settings()["crypto"]
        self.http = CachedClient(cfg["coinbase_url"], "coinbase", 0.15, conn=conn)

    def candles(self, product: str, start_ts: int, end_ts: int, granularity: int = 60) -> pd.DataFrame:
        rows: dict[int, tuple] = {}
        step = 300 * granularity
        s = start_ts
        while s < end_ts:
            e = min(s + step, end_ts)
            data = self.http.get(f"/products/{product}/candles",
                                 {"granularity": granularity, "start": to_iso(s), "end": to_iso(e)})
            for t, low, high, open_, close, vol in data:
                rows[int(t) + granularity] = (open_, high, low, close, vol)
            s = e
        df = pd.DataFrame.from_dict(rows, orient="index", columns=["open", "high", "low", "close", "volume"])
        return df.rename_axis("ts").reset_index().sort_values("ts").reset_index(drop=True)
