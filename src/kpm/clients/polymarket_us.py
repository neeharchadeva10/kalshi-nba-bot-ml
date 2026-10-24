"""Polymarket US public market data (read-only, no account): https://docs.polymarket.us

- GET /v1/markets                       market listing (closed/date filters)
- GET /v1/market/slug/{slug}            market detail incl. marketSides (teams, long/short)
- GET /v1/price-history                 1-min book-derived display prices, custom ranges <= 24 h:
                                        longPrice  ~ best ask of the long side
                                        shortPrice ~ 1 - best bid of the long side (= ask of the short side)
- GET /v1/markets/{slug}/settlement     settlement of the long side (1, 0, ...)
"""
from __future__ import annotations

import sqlite3
from typing import Iterator

from kpm.clients.http import CachedClient, HttpError
from kpm.core.config import settings

DAY = 86400


class PolymarketUSClient:
    def __init__(self, conn: sqlite3.Connection | None = None):
        cfg = settings()["polymarket_us"]
        self.http = CachedClient(cfg["gateway_url"], "polymarket_us", cfg["min_request_interval_s"], conn=conn)

    def markets(self, start_min: str, start_max: str, closed: bool = True, page: int = 200) -> Iterator[dict]:
        offset = 0
        while True:
            res = self.http.get("/v1/markets", {"closed": str(closed).lower(), "limit": page, "offset": offset,
                                                "startDateMin": start_min, "startDateMax": start_max})
            batch = res.get("markets", []) if isinstance(res, dict) else []
            yield from batch
            if len(batch) < page:
                return
            offset += page

    def market(self, slug: str) -> dict:
        return self.http.get(f"/v1/market/slug/{slug}")["market"]

    def settlement(self, slug: str):
        """Settlement of the long side, or None when the exchange has no settlement record."""
        try:
            return self.http.get(f"/v1/markets/{slug}/settlement").get("settlement")
        except HttpError as exc:
            if exc.status == 404:
                return None
            raise

    def price_history(self, slug: str, start_ts: int, end_ts: int) -> list[dict]:
        out: dict[int, dict] = {}
        s = start_ts
        while s < end_ts:
            e = min(s + DAY, end_ts)
            res = self.http.get("/v1/price-history", {"symbol": slug, "timestamp.startTimestamp": s,
                                                      "timestamp.endTimestamp": e, "fidelity": 1})
            for p in res.get("history", []) or []:
                out[int(p["timestamp"])] = p
            s = e
        return [out[t] for t in sorted(out)]
