"""Forward "paper tape": one run = one snapshot of OPEN markets for the NFL/NBA totals/spread/winner series.

Public, unauthenticated GET /markets only (no keys, no order/portfolio endpoints, no kpm.execution).
Every run writes, under data/paper_tape/<ET date>/ :
  raw-<UTC HHMMSS>.json.gz   the raw API pages exactly as returned
  snap.jsonl.gz              append-only (one gzip member appended per run): a compact row per open market,
                             plus a {"type": "gap", ...} line when the previous snapshot is > 15 min old
and updates logs/paper_tape.heartbeat (overwritten JSON line) and appends to logs/paper_tape.log.
Prices are kept as the strings the API returned plus a parsed float (<name>_f).
Run time budget: < 2 min (throttle >= 0.25 s between requests).
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
ET = ZoneInfo("America/New_York")
BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ["KXNFLTOTAL", "KXNFLTEAMTOTAL", "KXNFL1HTOTAL", "KXNFL2HTOTAL", "KXNFLSPREAD", "KXNFLGAME",
          "KXNBATOTAL", "KXNBATEAMTOTAL", "KXNBA1HTOTAL", "KXNBASPREAD", "KXNBAGAME"]
THROTTLE_S = 0.25
GAP_S = 15 * 60
DEADLINE_S = 100          # stop starting new requests after this (hard budget is 2 min)
MAX_PAGES = 20
RETRIES = 3

log = logging.getLogger("paper_tape")

# fields kept as the API string + a parsed float
STR_FIELDS = {
    "yes_bid": "yes_bid_dollars", "yes_ask": "yes_ask_dollars",
    "yes_bid_size": "yes_bid_size_fp", "yes_ask_size": "yes_ask_size_fp",
    "last_price": "last_price_dollars", "volume": "volume_fp", "open_interest": "open_interest_fp",
    "liquidity": "liquidity_dollars", "volume_24h": "volume_24h_fp",
}


def _f(v):
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def compact_row(m: dict, series: str, ts_utc: str) -> dict:
    row = {"type": "mkt", "ts_utc": ts_utc, "series": series, "ticker": m.get("ticker"),
           "event_ticker": m.get("event_ticker"), "floor_strike": m.get("floor_strike"),
           "cap_strike": m.get("cap_strike"), "strike_type": m.get("strike_type")}
    for short, key in STR_FIELDS.items():
        # fall back to the legacy cent-integer names if the API ever returns them instead
        raw = m.get(key)
        if raw is None and short in ("yes_bid", "yes_ask", "last_price"):
            raw = m.get(short)
        if raw is None and short in ("volume", "open_interest"):
            raw = m.get(short)
        if raw is not None:
            row[short] = raw
            row[short + "_f"] = _f(raw)
    row.update(status=m.get("status"), close_time=m.get("close_time"),
               expected_expiration_time=m.get("expected_expiration_time"))
    return row


class Snapshotter:
    def __init__(self, root: Path = ROOT, session=None, now_fn=None, sleep_fn=time.sleep, series=None,
                 base_url: str = BASE_URL, mono_fn=time.monotonic):
        self.root = Path(root)
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = "kpm-research/0.1 (read-only paper tape)"
        for h in list(self.session.headers):                   # never send credentials
            if h.lower() in ("authorization",) or h.lower().startswith("kalshi-access"):
                del self.session.headers[h]
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.sleep_fn, self.mono_fn = sleep_fn, mono_fn
        self.series = list(series or SERIES)
        self.base_url = base_url.rstrip("/")
        self._last_req = 0.0

    # ------------------------------------------------------------------ paths
    def et_date(self, now: datetime) -> str:
        return now.astimezone(ET).strftime("%Y-%m-%d")

    def day_dir(self, now: datetime) -> Path:
        return self.root / "data" / "paper_tape" / self.et_date(now)

    @property
    def heartbeat_path(self) -> Path:
        return self.root / "logs" / "paper_tape.heartbeat"

    # ------------------------------------------------------------------ network
    def _get(self, params: dict) -> dict:
        url = f"{self.base_url}/markets"                         # the only endpoint this tool ever calls
        delay = 1.0
        for attempt in range(RETRIES + 1):
            wait = self._last_req + THROTTLE_S - self.mono_fn()
            if wait > 0:
                self.sleep_fn(wait)
            self._last_req = self.mono_fn()
            try:
                r = self.session.get(url, params=params, timeout=20)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == RETRIES:
                    raise
                log.warning("network error %s; retry in %.0fs", exc.__class__.__name__, delay)
                self.sleep_fn(delay)
                delay *= 2
                continue
            if r.status_code in (408, 425, 429, 500, 502, 503, 504) and attempt < RETRIES:
                log.warning("HTTP %s; retry in %.0fs", r.status_code, delay)
                self.sleep_fn(delay)
                delay *= 2
                continue
            if r.status_code == 404:
                return {"markets": [], "_http": 404}
            r.raise_for_status()
            return r.json()
        raise RuntimeError("unreachable")

    def fetch_series(self, s: str) -> list[dict]:
        pages, cursor = [], None
        for _ in range(MAX_PAGES):
            params = {"series_ticker": s, "status": "open", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            page = self._get(params)
            pages.append(page)
            cursor = page.get("cursor")
            if not cursor or not page.get("markets"):
                break
        return pages

    # ------------------------------------------------------------------ state
    def previous_ok_ts(self) -> datetime | None:
        try:
            hb = json.loads(self.heartbeat_path.read_text(encoding="utf-8"))
            v = hb.get("last_ok_ts_utc")
            return datetime.fromisoformat(v) if v else None
        except (OSError, ValueError):
            return None

    def write_heartbeat(self, hb: dict) -> None:
        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.heartbeat_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(hb), encoding="utf-8")
        tmp.replace(self.heartbeat_path)

    # ------------------------------------------------------------------ run
    def run(self) -> dict:
        t0 = self.mono_fn()
        now = self.now_fn()
        ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        prev = self.previous_ok_ts()
        lines, raw, counts, errors = [], {}, {}, {}
        if prev is not None and (now - prev).total_seconds() > GAP_S:
            gap = {"type": "gap", "ts_utc": ts, "prev_ok_ts_utc": prev.strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "gap_s": int((now - prev).total_seconds())}
            lines.append(gap)
            log.warning("GAP: previous snapshot %s is %d s old", gap["prev_ok_ts_utc"], gap["gap_s"])
        for s in self.series:
            if self.mono_fn() - t0 > DEADLINE_S:
                errors[s] = "skipped: run deadline reached"
                continue
            try:
                pages = self.fetch_series(s)
            except Exception as exc:                  # one series failing must not lose the others
                errors[s] = f"{exc.__class__.__name__}: {exc}"[:300]
                log.error("%s failed: %s", s, errors[s])
                continue
            ms = [m for p in pages for m in p.get("markets", [])]
            if not ms:
                counts[s] = 0
                log.info("%s: no open markets (skipped)", s)
                continue
            raw[s] = pages
            counts[s] = len(ms)
            lines += [compact_row(m, s, ts) for m in ms]
            log.info("%s: %d open markets", s, len(ms))
        d = self.day_dir(now)
        d.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%H%M%S")
        if raw:
            rawp = d / f"raw-{stamp}.json.gz"
            tmp = rawp.with_suffix(".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                json.dump({"ts_utc": ts, "series": raw}, fh)
            tmp.replace(rawp)
        if lines:
            blob = gzip.compress(("\n".join(json.dumps(x) for x in lines) + "\n").encode("utf-8"))
            with open(d / "snap.jsonl.gz", "ab") as fh:        # one gzip member per run, appended in a single write
                fh.write(blob)
        ok = bool(counts)                 # at least one series answered (even with 0 open markets)
        hb = {"ts_utc": ts, "n_markets": counts, "errors": errors,
              "last_ok_ts_utc": now.isoformat() if ok else (prev.isoformat() if prev else None),
              "elapsed_s": round(self.mono_fn() - t0, 1)}
        self.write_heartbeat(hb)
        log.info("snapshot done: %s errors=%s elapsed=%.1fs", counts, errors, hb["elapsed_s"])
        return hb


def read_snap(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:        # reads all appended members
        return [json.loads(x) for x in fh if x.strip()]


def setup_logging(root: Path) -> None:
    (root / "logs").mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(root / "logs" / "paper_tape.log", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.handlers[:] = [fh, sh]
    log.setLevel(logging.INFO)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--series", default=",".join(SERIES))
    a = ap.parse_args(argv)
    setup_logging(Path(a.root))
    hb = Snapshotter(Path(a.root), series=a.series.split(",")).run()
    return 0 if any(hb["n_markets"].values()) or not hb["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
