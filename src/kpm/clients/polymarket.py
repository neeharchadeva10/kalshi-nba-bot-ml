"""Polymarket public read-only APIs. No wallet, keys or account.

- Gamma API (market metadata, rules): https://gamma-api.polymarket.com
- CLOB API  (price history):          GET /prices-history?market=<token>&startTs&endTs&fidelity
- Data API  (public trades; fallback when prices-history is coarse): GET /trades?market=<conditionId>
"""
from __future__ import annotations

import sqlite3
from typing import Iterator

from kpm.clients.http import CachedClient
from kpm.core.config import settings

# Data API trade rows include user identifiers; we never store these.
_TRADE_KEEP = ("asset", "conditionId", "side", "size", "price", "timestamp", "outcome", "outcomeIndex",
               "transactionHash")


class PolymarketClient:
    def __init__(self, conn: sqlite3.Connection | None = None):
        cfg = settings()["polymarket"]
        self.cfg = cfg
        iv = cfg["min_request_interval_s"]
        self.gamma = CachedClient(cfg["gamma_url"], "polymarket_gamma", iv, conn=conn)
        self.clob = CachedClient(cfg["clob_url"], "polymarket_clob", iv, conn=conn)
        self.data = CachedClient(cfg["data_api_url"], "polymarket_data", iv, conn=conn)

    def series_events(self, series_id: int, closed: bool = True, page: int = 100) -> Iterator[dict]:
        offset = 0
        while True:
            batch = self.gamma.get("/events", {"series_id": series_id, "closed": str(closed).lower(),
                                               "limit": page, "offset": offset, "order": "id", "ascending": "true"})
            if not batch:
                return
            yield from batch
            if len(batch) < page:
                return
            offset += page

    def event_by_slug(self, slug: str) -> dict | None:
        res = self.gamma.get("/events", {"slug": slug})
        return res[0] if res else None

    def prices_history(self, token_id: str, start_ts: int, end_ts: int, fidelity_min: int,
                       chunk_s: int) -> list[dict]:
        out: dict[int, float] = {}
        s = start_ts
        while s < end_ts:
            e = min(s + chunk_s, end_ts)
            page = self.clob.get("/prices-history", {"market": token_id, "startTs": s, "endTs": e,
                                                     "fidelity": fidelity_min})
            for p in page.get("history", []):
                out[int(p["t"])] = float(p["p"])
            s = e
        return [{"t": t, "p": out[t]} for t in sorted(out)]

    def trades(self, condition_id: str, page: int = 500, max_offset: int = 100_000) -> Iterator[dict]:
        """Public trades, newest first. The Data API caps how deep offset paging goes."""
        offset = 0
        while offset <= max_offset:
            try:
                batch = self.data.get("/trades", {"market": condition_id, "limit": page, "offset": offset,
                                                  "takerOnly": "false"})
            except Exception:
                return
            if not batch:
                return
            for t in batch:
                yield {k: t.get(k) for k in _TRADE_KEEP}
            if len(batch) < page:
                return
            offset += page
