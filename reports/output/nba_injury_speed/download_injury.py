"""Research log #44: probe the NBA injury-report slots on every 2025-26 regular-season game day (ET), 11:00 AM ET to the last tip:
hourly names before the 15-minute era, 15-minute names in it.  Public GET of ak-static.cms.nba.com/referee/injury/<file name>
only; cached files in data/raw/nba_injury/ are reused (never re-downloaded); 0.25 s between requests, 2 workers.
Each hit -> /CreationDate (UTC).  Writes reports_manifest.json (name -> created_utc) + injury_download_report.json.
Resumable: probe results (including 'missing') are cached in injury_probe_cache.json.
Run: .venv/Scripts/python.exe reports/output/nba_injury_speed/download_injury.py [extra_dates.txt]
"""
import csv
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import common as C
from common import ET, HERE, ROOT

OUT = ROOT / "data" / "raw" / "nba_injury"
BASE = "https://ak-static.cms.nba.com/referee/injury/"
UA = {"User-Agent": "Mozilla/5.0 (research; read-only)"}
CD = re.compile(rb"/CreationDate\(D:(\d{14})([+-]\d{2})'(\d{2})'\)")
CACHE_F = HERE / "injury_probe_cache.json"
V3_CACHE = ROOT / "reports" / "output" / "nba_model" / "_probe_cache_v3.json"   # read only (old probe results)
_lock = threading.Lock()
_last = [0.0]
stat = {"probes": 0, "net_get": 0, "local_read": 0, "cache_hit": 0, "http_missing": 0, "errors": 0}
cache = {}


def name15(d):
    return "Injury-Report_" + d.strftime("%Y-%m-%d_%I_%M%p") + ".pdf"


def name1h(d):
    return "Injury-Report_" + d.strftime("%Y-%m-%d_%I%p") + ".pdf"


def creation_of(body: bytes):
    m = CD.search(body)
    if not (body.startswith(b"%PDF") and m):
        return -1.0
    dt = datetime.strptime(m.group(1).decode(), "%Y%m%d%H%M%S")
    sign = 1 if m.group(2)[:1] == b"+" else -1
    off = timedelta(hours=int(m.group(2)[1:]), minutes=int(m.group(3))) * sign
    return (dt - off).replace(tzinfo=timezone.utc).timestamp()


def probe(name):
    """creation UTC ts of the published file, None if it does not exist (HTTP 403/404), -1 if unreadable."""
    with _lock:
        if name in cache:
            stat["cache_hit"] += 1
            return cache[name]
        stat["probes"] += 1
    p = OUT / name
    if p.exists() and p.stat().st_size > 1000:
        val = creation_of(p.read_bytes())
        with _lock:
            stat["local_read"] += 1
            cache[name] = val
        return val
    val = None
    for attempt in range(4):
        with _lock:
            wait = _last[0] + 0.25 - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.monotonic()
        try:
            with urllib.request.urlopen(urllib.request.Request(BASE + name, headers=UA), timeout=60) as r:
                body = r.read(3_000_000)
            val = creation_of(body)
            if body.startswith(b"%PDF"):
                p.write_bytes(body)
            with _lock:
                stat["net_get"] += 1
            break
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                with _lock:
                    stat["http_missing"] += 1
                val = None
                break
            with _lock:
                stat["errors"] += 1
            time.sleep(2 * (attempt + 1))
        except Exception:   # noqa: BLE001
            with _lock:
                stat["errors"] += 1
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError("repeated failure for " + name)     # transient failure is NOT cached as missing
    with _lock:
        cache[name] = val
    return val


def slots(day, last_tip_et, era15):
    """Slot start datetimes (ET) from 11:00 to the last tip."""
    t = datetime(day.year, day.month, day.day, 11, 0, tzinfo=ET)
    step = timedelta(minutes=15) if era15 else timedelta(hours=1)
    out = []
    while t <= last_tip_et:
        out.append(t)
        t = (t.astimezone(timezone.utc) + step).astimezone(ET)
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if CACHE_F.exists():
        cache.update(json.loads(CACHE_F.read_text()))
    elif V3_CACHE.exists():
        cache.update(json.loads(V3_CACHE.read_text()))        # known results (None = missing) from the #32 probes
    # game days and last tip per day
    days = defaultdict(int)
    with open(HERE / "events_list.csv") as fh:
        for r in csv.DictReader(fh):
            days[r["game_date_et"]] = max(days[r["game_date_et"]], int(r["T"]))
    if len(sys.argv) > 1:      # optional extra dates: lines "YYYY-MM-DD HH:MM" (ET last tip)
        for ln in open(sys.argv[1]):
            if ln.strip():
                d, hm = ln.split()
                dt = datetime.strptime(d + " " + hm, "%Y-%m-%d %H:%M").replace(tzinfo=ET)
                days[d] = max(days[d], int(dt.timestamp()))
    # 15-minute era start: probe 15-minute names (18:00, 18:15, 18:30, 18:45 ET) on each day 2025-12-01 .. 2026-01-05
    era_start, era_probe = None, {}
    d0 = date(2025, 12, 1)
    while d0 <= date(2026, 1, 5):
        hit = False
        for hh, mm in ((18, 0), (18, 15), (18, 30), (18, 45), (19, 15)):
            if probe(name15(datetime(d0.year, d0.month, d0.day, hh, mm, tzinfo=ET))) not in (None, -1.0):
                hit = True
                break
        era_probe[d0.isoformat()] = hit
        if hit and era_start is None:
            era_start = d0
        d0 += timedelta(days=1)
    print("15-minute era starts", era_start, flush=True)
    jobs, per_day = [], {}
    for day_s in sorted(days):
        day = date.fromisoformat(day_s)
        era15 = era_start is not None and day >= era_start
        last_tip_et = datetime.fromtimestamp(days[day_s], timezone.utc).astimezone(ET)
        sl = slots(day, last_tip_et, era15)
        per_day[day_s] = {"era15": era15, "slots": len(sl)}
        jobs += [name15(s) if era15 else name1h(s) for s in sl]
    print("days", len(per_day), "slots to probe", len(jobs), flush=True)

    def run(n):
        try:
            return n, probe(n)
        except Exception as e:   # noqa: BLE001
            return n, "ERR " + type(e).__name__

    res, fails, t0 = {}, [], time.time()
    with ThreadPoolExecutor(max_workers=2) as pool:
        for k, (n, v) in enumerate(pool.map(run, jobs)):
            if isinstance(v, str):
                fails.append(n)
            else:
                res[n] = v
            if k % 500 == 0:
                CACHE_F.write_text(json.dumps(cache))
                print(k, len(jobs), f"{time.time() - t0:.0f}s", stat, flush=True)
    CACHE_F.write_text(json.dumps(cache))
    man = {n: v for n, v in res.items() if v not in (None, -1.0)}
    bad = [n for n, v in res.items() if v == -1.0]
    (HERE / "reports_manifest.json").write_text(json.dumps(man, indent=0, sort_keys=True))
    byday_hit = defaultdict(int)
    for n in man:
        byday_hit[n.split("_")[1]] += 1
    rep = {"era15_start_probed": str(era_start), "era_probe_by_day": era_probe, "days": len(per_day), "slots_probed": len(jobs),
           "reports_found": len(man), "slots_missing": len(res) - len(man) - len(bad), "unreadable_pdf": bad,
           "probe_failures_unresolved": fails, "stat": stat, "days_with_zero_reports": [d for d in per_day if byday_hit[d] == 0],
           "reports_per_day_min_med_max": [min(byday_hit.values()), sorted(byday_hit.values())[len(byday_hit) // 2], max(byday_hit.values())]
           if byday_hit else None}
    (HERE / "injury_download_report.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps({k: v for k, v in rep.items() if k != "era_probe_by_day"}, indent=1)[:3000])


if __name__ == "__main__":
    main()
