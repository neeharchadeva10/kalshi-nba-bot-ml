"""Shared helpers for NFL props study: adaptive-paced cached GET client (public endpoints only)."""
from __future__ import annotations
import gzip, hashlib, json, math, time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[3]
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KDIR = ROOT / "data" / "raw" / "kalshi_nfl_props"
NDIR = ROOT / "data" / "raw" / "nflverse"
ALLOWED_HOSTS = ("api.elections.kalshi.com", "github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com")


class Pacer:
    """AIMD: start 0.3 req/s, +0.1 per 60 s with no 429, x0.5 on 429, floor 0.2, ceiling 1.0."""
    def __init__(self, rate=0.3, floor=0.2, ceil=1.0, clock=time.monotonic):
        self.rate, self.floor, self.ceil, self.clock = rate, floor, ceil, clock
        self.last_req = -1e9
        self.last_adj = clock()

    def on_429(self):
        self.rate = max(self.floor, self.rate * 0.5)
        self.last_adj = self.clock()

    def maybe_increase(self):
        now = self.clock()
        if now - self.last_adj >= 60:
            self.rate = min(self.ceil, round(self.rate + 0.1, 10))
            self.last_adj = now

    def wait_time(self):
        self.maybe_increase()
        return max(0.0, self.last_req + 1.0 / self.rate - self.clock())

    def wait(self):
        w = self.wait_time()
        if w > 0:
            time.sleep(w)
        self.last_req = self.clock()


class KClient:
    def __init__(self):
        self.pacer = Pacer()
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "kpm-research/0.1 (read-only)"
        KDIR.mkdir(parents=True, exist_ok=True)
        self.stats = {"hits": 0, "fetches": 0, "r429": 0}

    def get(self, path, params=None):
        assert path.startswith("/") and not any(
            seg in ("portfolio", "orders", "order", "fills", "positions", "balance") for seg in path.split("/"))
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = hashlib.sha256(json.dumps([path, sorted((k, str(v)) for k, v in params.items())]).encode()).hexdigest()
        cp = KDIR / key[:2] / f"{key}.json.gz"
        if cp.exists():
            self.stats["hits"] += 1
            with gzip.open(cp, "rt", encoding="utf-8") as fh:
                return json.load(fh)
        delay = 2.0
        for _ in range(10):
            self.pacer.wait()
            try:
                r = self.s.get(KALSHI_BASE + path, params=params, timeout=60)
            except (requests.ConnectionError, requests.Timeout):
                time.sleep(delay); delay = min(delay * 2, 60); continue
            if r.status_code == 429:
                self.stats["r429"] += 1
                self.pacer.on_429()
                time.sleep(delay); delay = min(delay * 2, 60); continue
            if r.status_code in (500, 502, 503, 504):
                time.sleep(delay); delay = min(delay * 2, 60); continue
            if r.status_code == 404:
                data = {"_status": 404}
            elif not r.ok:
                raise RuntimeError(f"HTTP {r.status_code} {path}: {r.text[:200]}")
            else:
                data = r.json()
            self.stats["fetches"] += 1
            cp.parent.mkdir(parents=True, exist_ok=True)
            tmp = cp.with_suffix(".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                json.dump(data, fh)
            tmp.replace(cp)
            return data
        raise RuntimeError(f"gave up {path}")

    def paginate(self, path, key, params):
        cursor = None
        while True:
            page = self.get(path, {**params, "cursor": cursor})
            yield from page.get(key, [])
            cursor = page.get("cursor")
            if not cursor:
                return


def download_nflverse(url: str, name: str) -> Path | None:
    """GET a public nflverse release file once; cached to data/raw/nflverse/."""
    from urllib.parse import urlparse
    assert urlparse(url).hostname in ALLOWED_HOSTS
    NDIR.mkdir(parents=True, exist_ok=True)
    p = NDIR / name
    if p.exists() and p.stat().st_size > 0:
        return p
    r = requests.get(url, timeout=120, headers={"User-Agent": "kpm-research/0.1 (read-only)"})
    if r.status_code == 404:
        (NDIR / (name + ".404")).write_text("404")
        return None
    r.raise_for_status()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_bytes(r.content)
    tmp.replace(p)
    return p


def kalshi_fee(p: float, n: int = 1) -> float:
    """0.07*n*P*(1-P) rounded up to the next cent (dollars)."""
    return math.ceil(round(0.07 * n * p * (1 - p) * 100, 6)) / 100


def candles(c: KClient, series: str, ticker: str, start_ts: int, end_ts: int, hist: bool, interval_min: int = 60):
    """Hourly candles in [start_ts, end_ts]; one request (<=~8 candles). Historical tier for markets settled before cutoff."""
    path = (f"/historical/markets/{ticker}/candlesticks" if hist
            else f"/series/{series}/markets/{ticker}/candlesticks")
    d = c.get(path, {"start_ts": start_ts, "end_ts": end_ts, "period_interval": interval_min})
    return d.get("candlesticks", []) if isinstance(d, dict) else []
