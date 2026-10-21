"""GET-only HTTP client with a permanent on-disk cache, rate limiting and backoff.

Every successful response is stored gzip-compressed under data/raw/<namespace>/ keyed by
a hash of (url, sorted params), so re-runs never re-download. Only GET is implemented on
purpose: Phase 1 must not touch any order/trading/portfolio endpoint.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import random
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import requests

from kpm.core import config
from kpm.core.timeutil import now_ts

log = logging.getLogger(__name__)

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
# Whole path segments that belong to account/trading APIs. Matched per segment so that public market data such
# as /markets/{ticker}/orderbook stays allowed while /portfolio/orders etc. are refused.
FORBIDDEN_SEGMENTS = {"portfolio", "orders", "order", "fills", "positions", "balance"}


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        super().__init__(f"HTTP {status} for {url}: {body[:300]}")
        self.status = status


class CachedClient:
    def __init__(self, base_url: str, namespace: str, min_interval_s: float = 0.1,
                 max_retries: int = 8, timeout_s: float = 60.0,
                 conn: sqlite3.Connection | None = None, forbid_trading_paths: bool = True):
        self.base_url = base_url.rstrip("/")
        self.namespace = namespace
        self.min_interval_s = min_interval_s
        self.max_retries = max_retries
        self.timeout_s = timeout_s
        self.conn = conn
        self.forbid = forbid_trading_paths
        self.cache_dir = config.path("raw_cache_dir") / namespace
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "kpm-research/0.1 (read-only)"
        self._lock = threading.Lock()
        self._owner = threading.get_ident()
        self._pending: list[tuple] = []
        self._last = 0.0
        self.stats = {"hits": 0, "fetches": 0, "retries": 0}

    @staticmethod
    def request_key(url: str, params: dict | None) -> str:
        items = sorted((k, str(v)) for k, v in (params or {}).items() if v is not None)
        return hashlib.sha256(json.dumps([url, items]).encode()).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json.gz"

    def _throttle(self) -> None:
        with self._lock:
            wait = self._last + self.min_interval_s - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def get(self, path: str, params: dict | None = None, use_cache: bool = True, persist: bool = True) -> Any:
        """persist=False: never write the response to the on-disk cache (long-running collectors keep their own raw log)."""
        if self.forbid and FORBIDDEN_SEGMENTS & {seg.lower() for seg in path.split("?")[0].split("/")}:
            raise PermissionError(f"Refusing non-market-data path in read-only phase: {path}")
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = self.request_key(url, params)
        cp = self._cache_path(key)
        if use_cache and cp.exists():
            self.stats["hits"] += 1
            with gzip.open(cp, "rt", encoding="utf-8") as fh:
                return json.load(fh)

        delay = 1.0
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                r = self._session.get(url, params=params, timeout=self.timeout_s)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == self.max_retries:
                    raise
                log.warning("network error %s on %s; retry in %.1fs", exc.__class__.__name__, url, delay)
                self.stats["retries"] += 1
                time.sleep(delay + random.uniform(0, delay / 2))
                delay = min(delay * 2, 120)
                continue
            if r.status_code in RETRY_STATUS and attempt < self.max_retries:
                ra = r.headers.get("Retry-After")
                wait = float(ra) if ra and ra.replace(".", "", 1).isdigit() else delay
                log.warning("HTTP %s on %s; retry in %.1fs", r.status_code, url, wait)
                self.stats["retries"] += 1
                if r.status_code == 429:  # back off globally so other threads don't keep hammering
                    with self._lock:
                        self._last = max(self._last, time.monotonic() + wait)
                time.sleep(wait + random.uniform(0, 0.5))
                delay = min(delay * 2, 120)
                continue
            if not r.ok:
                raise HttpError(r.status_code, url, r.text)
            data = r.json()
            self.stats["fetches"] += 1
            if not persist:
                return data
            cp.parent.mkdir(parents=True, exist_ok=True)
            tmp = cp.with_suffix(".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                json.dump(data, fh)
            tmp.replace(cp)
            with self._lock:
                self._pending.append((key, url, json.dumps(params, sort_keys=True), str(cp), r.status_code, now_ts()))
            if self.conn is not None and threading.get_ident() == self._owner:
                self.flush_index()
            return data
        raise RuntimeError("unreachable")

    def flush_index(self, conn: sqlite3.Connection | None = None) -> None:
        """Write pending cache-index rows. Call from the thread that owns the connection."""
        conn = conn or self.conn
        if conn is None:
            return
        with self._lock:
            rows, self._pending = self._pending, []
        conn.executemany("INSERT OR REPLACE INTO raw_responses (request_hash, url, params, path, http_status, "
                         "fetched_ts) VALUES (?,?,?,?,?,?)", rows)
