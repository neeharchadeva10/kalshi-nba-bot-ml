"""#36 JOB 1: KXNBA3PT hourly-candle download (public GET only), same method as #48's props_download.py:
  GET https://api.elections.kalshi.com/trade-api/v2/historical/markets/{ticker}/candlesticks
      ?start_ts=T-4h&end_ts=T-1h&period_interval=60
for the markets in threes_selection.csv.  Raw responses cached at data/raw/kalshi_props_hourly_3pt/<game_date>/<ticker>.json.gz
(404s cached too).  Resumable.  AIMD pacing (pacing.py): start 0.5 req/s, ceiling 1.5, x0.5 on 429, floor 0.2.

BOT WINDOW: NO HTTP request is made between 17:30 and 21:30 America/New_York (the early-overs bot places orders then).
Every request is gated by wait_if_paused() immediately before the GET (also after any pacing sleep).

Writes threes_download_complete.flag when every selected market has a final state (ok or 404).  Create STOP_3PT to stop.
No auth, no keys, no order/portfolio endpoints.  Nothing here reads outcomes.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import sys
import threading
import time
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "nba_teammate_out"))
sys.path.insert(0, str(HERE))
from pacing import AIMD  # noqa: E402

ET = ZoneInfo("America/New_York")
PAUSE_START = dtime(17, 30)
PAUSE_END = dtime(21, 30)
BASE = "https://api.elections.kalshi.com/trade-api/v2"
SELECTION_CSV = HERE / "threes_selection.csv"
CANDLE_DIR = HERE.parents[2] / "data" / "raw" / "kalshi_props_hourly_3pt"
PROGRESS = HERE / "threes_download_progress.log"
STATE = HERE / "threes_download_state.json"
STOP = HERE / "STOP_3PT"
FLAG = HERE / "threes_download_complete.flag"
WORKERS = 3
LOG_EVERY_S = 180
CEIL = 1.5


def pause_seconds(now_ts: float) -> float:
    """Seconds to wait before a request may be sent at epoch time now_ts: 0 outside [17:30, 21:30) ET, else time to 21:30 ET."""
    now = datetime.fromtimestamp(now_ts, ET)
    t = now.time()
    if PAUSE_START <= t < PAUSE_END:
        end = datetime.combine(now.date(), PAUSE_END, tzinfo=ET)
        return max((end - now).total_seconds(), 0.0)
    return 0.0


_pause_logged = [0.0]


def wait_if_paused(clock=time.time, sleep=time.sleep, logger=None):
    """Block until outside the bot window.  Returns the total seconds slept."""
    slept = 0.0
    while True:
        s = pause_seconds(clock())
        if s <= 0:
            return slept
        if logger and clock() - _pause_logged[0] > 600:
            _pause_logged[0] = clock()
            logger(f"paused for bot window (17:30-21:30 ET); resuming in {s / 60:.0f} min")
        d = min(s + 1.0, 60.0)
        sleep(d)
        slept += d


def path_for(ticker):
    p = f"/historical/markets/{ticker}/candlesticks"
    assert p.startswith("/historical/markets/") and p.endswith("/candlesticks")      # public market-data path only
    return p


def candle_path(ticker, game_date):
    return CANDLE_DIR / game_date / f"{ticker}.json.gz"


def read_selection(path=SELECTION_CSV):
    with Path(path).open() as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["group"] = int(r["group"])
        r["tip"] = int(r["tip"])
    return rows


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    print(line, flush=True)
    with PROGRESS.open("a") as fh:
        fh.write(line + "\n")


def write_cache(r, status, resp, params):
    p = candle_path(r["ticker"], r["game_date"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    rec = dict(ticker=r["ticker"], game_date=r["game_date"], path=path_for(r["ticker"]), params=params, status=status,
               fetched_ts=int(time.time()), response=resp)
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(rec, fh)
    tmp.replace(p)


def main():
    import requests
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    sel = read_selection()
    todo = [r for r in sel if not candle_path(r["ticker"], r["game_date"]).exists()]
    if a.limit:
        todo = todo[:a.limit]
    st = json.loads(STATE.read_text()) if STATE.exists() else dict(n_429=0, n_requests=0, n_fail=0, n_404=0, runs=0)
    st["runs"] += 1
    log(f"START selected={len(sel)} cached_already={len(sel) - len(todo)} todo={len(todo)} pid={os.getpid()}")
    pacer = AIMD(ceil=CEIL)
    lock = threading.Lock()
    it = iter(todo)
    cnt = dict(done=0, fail=0, n404=0, req=0, n429=0)
    session = requests.Session()
    session.headers["User-Agent"] = "kpm-research/0.1 (read-only)"

    def next_item():
        with lock:
            if STOP.exists():
                return None
            return next(it, None)

    def fetch(r):
        params = {"start_ts": r["tip"] - 4 * 3600, "end_ts": r["tip"] - 3600, "period_interval": 60}
        backoff = 5.0
        for attempt in range(40):
            wait_if_paused(logger=log)
            pacer.acquire()
            wait_if_paused(logger=log)             # pacing may have slept into the window
            try:
                resp = session.get(BASE + path_for(r["ticker"]), params=params, timeout=45)
            except (requests.ConnectionError, requests.Timeout):
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue
            with lock:
                cnt["req"] += 1
            if resp.status_code == 429:
                ra = resp.headers.get("Retry-After")
                try:
                    ra = float(ra) if ra is not None else None
                except ValueError:
                    ra = None
                pacer.on_429(ra)
                with lock:
                    cnt["n429"] += 1
                continue
            if resp.status_code == 200:
                pacer.on_ok()
                write_cache(r, 200, resp.json(), params)
                return "ok"
            if resp.status_code == 404:
                pacer.on_ok()
                write_cache(r, 404, None, params)
                return "404"
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)
        return "fail"

    def worker():
        while True:
            r = next_item()
            if r is None:
                return
            out = fetch(r)
            with lock:
                if out == "fail":
                    cnt["fail"] += 1
                else:
                    cnt["done"] += 1
                    if out == "404":
                        cnt["n404"] += 1

    ths = [threading.Thread(target=worker, daemon=True) for _ in range(WORKERS)]
    for t in ths:
        t.start()
    last_log = time.time()
    while any(t.is_alive() for t in ths):
        time.sleep(5)
        now = time.time()
        if now - last_log >= LOG_EVERY_S:
            with lock:
                d, f4, n429, req = cnt["done"], cnt["fail"], cnt["n429"], cnt["req"]
            cached = sum(1 for r in sel if candle_path(r["ticker"], r["game_date"]).exists())
            log(f"progress run_done={d}/{len(todo)} total_cached={cached}/{len(sel)} target_rate={pacer.rate:.2f}/s "
                f"429s_this_run={n429} requests={req} fail={f4} 404={cnt['n404']} paused_now={pause_seconds(now) > 0}")
            last_log = now
    cached = sum(1 for r in sel if candle_path(r["ticker"], r["game_date"]).exists())
    if cached == len(sel) and not FLAG.exists():
        FLAG.write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
    st["n_429"] += cnt["n429"]
    st["n_requests"] += cnt["req"]
    st["n_fail"] += cnt["fail"]
    st["n_404"] += cnt["n404"]
    STATE.write_text(json.dumps(st))
    log(f"END run: done={cnt['done']} fail={cnt['fail']} 404={cnt['n404']} requests={cnt['req']} 429s={cnt['n429']} "
        f"cached={cached}/{len(sel)} flag={'yes' if FLAG.exists() else 'no'}")


if __name__ == "__main__":
    main()
