"""Adaptive pacing (AIMD) + a GET-only cached client for research log #49.
Rate starts 0.4 req/s; +0.1 req/s per 60 s with no 429; x0.5 on any 429; floor 0.2, ceiling 1.2. Retry-After honoured.
Cache layout is identical to kpm.clients.http.CachedClient (gz json keyed by sha256(url, sorted params))."""
from __future__ import annotations

import gzip
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

START, STEP, PERIOD, FLOOR, CEIL = 0.4, 0.1, 60.0, 0.2, 1.2


class Pacer:
    """Pure pacing logic; `clock` and `sleep` are injectable so tests can use a fake clock."""

    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self.rate = START
        self.next_ok = clock()
        self.anchor = clock()          # last time the rate changed (or the last 429)
        self.n429 = 0

    def _grow(self, now):
        k = int((now - self.anchor) // PERIOD)
        if k > 0:
            self.rate = min(CEIL, self.rate + STEP * k)
            self.anchor += k * PERIOD

    def wait(self):
        """Block until the next request may be sent; returns the seconds slept."""
        now = self.clock()
        self._grow(now)
        d = max(0.0, self.next_ok - now)
        if d > 0:
            self.sleep(d)
        now = self.clock()
        self._grow(now)
        self.next_ok = now + 1.0 / self.rate
        return d

    def on_429(self, retry_after=None):
        now = self.clock()
        self.n429 += 1
        self.rate = max(FLOOR, self.rate * 0.5)
        self.anchor = now
        pause = float(retry_after) if retry_after not in (None, "") else 0.0
        self.next_ok = max(self.next_ok, now + max(pause, 1.0 / self.rate))
        return pause


class PacedClient:
    FORBIDDEN = {"portfolio", "orders", "order", "fills", "positions", "balance"}

    def __init__(self, base_url, cache_dir, pacer=None, max_retries=8, timeout=60.0, log=None):
        import requests
        self.base = base_url.rstrip("/")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.pacer = pacer or Pacer()
        self.max_retries, self.timeout = max_retries, timeout
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "kpm-research/0.1 (read-only)"
        self.stats = {"hits": 0, "fetches": 0, "r429": 0, "retries": 0, "404": 0}
        self.log = log or (lambda m: None)

    def _path(self, key):
        return self.cache_dir / key[:2] / f"{key}.json.gz"

    def get(self, path, params=None):
        from kpm.clients.http import CachedClient, HttpError
        if self.FORBIDDEN & {x.lower() for x in path.split("?")[0].split("/")}:
            raise PermissionError(path)
        url = f"{self.base}{path}"
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = CachedClient.request_key(url, params)
        cp = self._path(key)
        if cp.exists():
            self.stats["hits"] += 1
            with gzip.open(cp, "rt", encoding="utf-8") as fh:
                return json.load(fh)
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            self.pacer.wait()
            try:
                r = self.s.get(url, params=params, timeout=self.timeout)
            except Exception as e:  # noqa: BLE001
                if attempt == self.max_retries:
                    raise
                self.stats["retries"] += 1
                self.log(f"network error {type(e).__name__}; sleep {delay}s")
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            if r.status_code == 429:
                self.stats["r429"] += 1
                pause = self.pacer.on_429(r.headers.get("Retry-After"))
                self.log(f"429 -> rate {self.pacer.rate:.2f}/s retry-after={pause}")
                if attempt == self.max_retries:
                    raise HttpError(429, url, r.text)
                time.sleep(max(pause, 1.0))
                continue
            if r.status_code in (408, 425, 500, 502, 503, 504) and attempt < self.max_retries:
                self.stats["retries"] += 1
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            if r.status_code == 404:
                self.stats["404"] += 1
                data = {"_http_404": True, "candlesticks": []}
            elif not r.ok:
                raise HttpError(r.status_code, url, r.text)
            else:
                data = r.json()
            self.stats["fetches"] += 1
            cp.parent.mkdir(parents=True, exist_ok=True)
            tmp = cp.with_suffix(".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                json.dump(data, fh)
            tmp.replace(cp)
            return data
        raise RuntimeError("unreachable")
