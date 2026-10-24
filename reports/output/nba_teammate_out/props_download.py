"""SHARED props price download (research log #48; #36 reuses it).  Public GET only:
  GET https://api.elections.kalshi.com/trade-api/v2/historical/markets/{ticker}/candlesticks
      ?start_ts=T-4h&end_ts=T-1h&period_interval=60
for the markets listed in selection.csv (the 2 strikes nearest each player's pre-game last-10 mean; see build_selection.py).

* Every raw response is cached at data/raw/kalshi_props_hourly/<game_date>/<ticker>.json.gz and never re-downloaded
  (resumable; a killed job just continues).  404s are cached too (status 404).
* Adaptive pacing (pacing.AIMD): start 0.5 req/s, +0.1 req/s per 60 s without a 429, x0.5 on a 429, floor 0.2, ceiling 1.5,
  Retry-After honoured.
* Queue order: group 0 (discovery dates 2025-11-18..2026-01-22), then 1 (unseen regular season), then 2 (play-in/playoffs).
* Progress -> download_progress.log every 3 minutes.  Create the file STOP in this folder to stop gracefully.
* Writes discovery_download_complete.flag when every group-0 market is cached.
No auth, no keys, no order/portfolio endpoints.  Nothing here reads outcomes.

Resume:  .venv/Scripts/python.exe reports/output/nba_teammate_out/props_download.py
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import threading
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tm_lib as L  # noqa: E402
from pacing import AIMD  # noqa: E402

BASE = "https://api.elections.kalshi.com/trade-api/v2"
PROGRESS = L.HERE / "download_progress.log"
STATE = L.HERE / "download_state.json"
STOP = L.HERE / "STOP"
FLAG = L.HERE / "discovery_download_complete.flag"
WORKERS = 3
LOG_EVERY_S = 180


def path_for(ticker):
    p = f"/historical/markets/{ticker}/candlesticks"
    assert p.startswith("/historical/markets/") and p.endswith("/candlesticks")      # public market-data path only
    return p


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    print(line, flush=True)
    with PROGRESS.open("a") as fh:
        fh.write(line + "\n")


def write_cache(r, status, resp, params):
    p = L.candle_path(r["ticker"], r["game_date"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    rec = dict(ticker=r["ticker"], game_date=r["game_date"], path=path_for(r["ticker"]), params=params, status=status,
               fetched_ts=int(time.time()), response=resp)
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(rec, fh)
    tmp.replace(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-group", type=int, default=2)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--hours", type=float, default=None, help="stop after this many hours")
    a = ap.parse_args()
    sel = [r for r in L.read_selection() if r["group"] <= a.max_group]
    total = {g: sum(1 for r in sel if r["group"] == g) for g in (0, 1, 2)}
    todo = [r for r in sel if not L.candle_path(r["ticker"], r["game_date"]).exists()]
    if a.limit:
        todo = todo[:a.limit]
    st = json.loads(STATE.read_text()) if STATE.exists() else dict(n_429=0, n_requests=0, n_fail=0, n_404=0, runs=0)
    st["runs"] += 1
    log(f"START selected={sum(total.values())} by_group={total} cached_already={sum(total.values()) - len(todo)} "
        f"todo={len(todo)} (cumulative before this run: {st['n_requests']} requests, {st['n_429']} 429s)")
    pacer = AIMD()
    lock = threading.Lock()
    it = iter(todo)
    cnt = dict(done=0, fail=0, n404=0, req=0, n429=0)
    t_start = time.time()
    deadline = t_start + a.hours * 3600 if a.hours else None
    session = requests.Session()
    session.headers["User-Agent"] = "kpm-research/0.1 (read-only)"

    def next_item():
        with lock:
            if STOP.exists() or (deadline and time.time() > deadline):
                return None
            return next(it, None)

    def fetch(r):
        params = {"start_ts": r["tip"] - 4 * 3600, "end_ts": r["tip"] - 3600, "period_interval": 60}
        backoff = 5.0
        for attempt in range(40):          # 429s do not consume the budget for long: pacing slows down instead
            pacer.acquire()
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
            time.sleep(backoff)           # 5xx etc: retry, never cached
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
    last_log, last_done, last_t = time.time(), 0, time.time()
    while any(t.is_alive() for t in ths):
        time.sleep(5)
        now = time.time()
        if now - last_log >= LOG_EVERY_S:
            with lock:
                d, f4, n429, req = cnt["done"], cnt["fail"], cnt["n429"], cnt["req"]
            inst = (d - last_done) / (now - last_t)
            avg = d / (now - t_start)
            rem = len(todo) - d - f4
            eta = rem / avg / 3600 if avg > 0 else float("inf")
            g0 = sum(1 for r in sel if r["group"] == 0 and L.candle_path(r["ticker"], r["game_date"]).exists())
            log(f"progress run_done={d}/{len(todo)} total_cached={sum(total.values()) - rem}/{sum(total.values())} "
                f"discovery_cached={g0}/{total[0]} run_rate={avg:.2f}/s last_interval={inst:.2f}/s target_rate={pacer.rate:.2f}/s "
                f"429s_this_run={n429} requests={req} fail={f4} 404={cnt['n404']} ETA_all={eta:.1f}h "
                f"(rate min/max {pacer.min_rate:.2f}/{pacer.max_rate:.2f})")
            last_log, last_done, last_t = now, d, now
            if g0 == total[0] and not FLAG.exists():
                FLAG.write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
    g0 = sum(1 for r in sel if r["group"] == 0 and L.candle_path(r["ticker"], r["game_date"]).exists())
    if g0 == total[0] and not FLAG.exists():
        FLAG.write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
    st["n_429"] += cnt["n429"]
    st["n_requests"] += cnt["req"]
    st["n_fail"] += cnt["fail"]
    st["n_404"] += cnt["n404"]
    STATE.write_text(json.dumps(st))
    log(f"END run: done={cnt['done']} fail={cnt['fail']} 404={cnt['n404']} requests={cnt['req']} 429s={cnt['n429']} "
        f"discovery_cached={g0}/{total[0]} cumulative={st}")


if __name__ == "__main__":
    main()
