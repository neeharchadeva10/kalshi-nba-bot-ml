"""Research log #32 injury-report downloader for 2021-22 .. 2025-26 (read-only, public NBA CDN files, polite).
Same method as collect_injury.py (#31): file-name pattern on https://ak-static.cms.nba.com/referee/injury/ ,
publication time = PDF /CreationDate (hourly files stamped H are created at H:30), a game's report = the file with the latest
CreationDate <= scheduled tip - 3600 s. Game list and tip times come from the hoopR box scores (game_date_time).
Differences vs v2: pre-2023-24 PDFs carry /CreationDate at the END of the file, so the whole (small) file is read once per probe
and is kept on disk; hourly names are used for every day before 2025-12-01 without format probing.
Saves ONLY into data/raw/nba_injury/ (new files + _manifest_v3.json + _probe_cache_v3.json). Never executes downloaded content.
Run: .venv/Scripts/python.exe reports/output/nba_model/collect_injury_v3.py
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import data_v3 as D  # noqa: E402

ROOT = D.ROOT
OUT = ROOT / "data" / "raw" / "nba_injury"
ET = ZoneInfo("America/New_York")
BASE = "https://ak-static.cms.nba.com/referee/injury/"
DELAY = 0.25
UA = {"User-Agent": "Mozilla/5.0 (research; read-only)"}
CD = re.compile(rb"/CreationDate\(D:(\d{14})([+-]\d{2})'(\d{2})'\)")
FIFTEEN_FROM = date(2025, 12, 1)       # before this day only hourly names exist (probed, README_V3)
CACHE_F = OUT / "_probe_cache_v3.json"
cache = {}
stat = {"probes": 0, "net_get": 0, "local_read": 0, "errors": 0}


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


def creation(name):
    """Creation UTC ts of a published file, or None if it does not exist. Existing files are saved to OUT."""
    if name in cache:
        return cache[name]
    stat["probes"] += 1
    p = OUT / name
    if p.exists() and p.stat().st_size > 1000:
        stat["local_read"] += 1
        cache[name] = creation_of(p.read_bytes())
        return cache[name]
    val = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(BASE + name, headers=UA), timeout=60) as r:
                body = r.read(2_000_000)
            stat["net_get"] += 1
            val = creation_of(body)
            if body.startswith(b"%PDF"):
                p.write_bytes(body)
            time.sleep(DELAY)
            break
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                val = None
                time.sleep(DELAY)
                break
            print("unexpected http", e.code, name, flush=True)
            stat["errors"] += 1
            time.sleep(2 * (attempt + 1))
        except Exception as e:  # noqa: BLE001
            print("err", name, type(e).__name__, flush=True)
            stat["errors"] += 1
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError("repeated failure for " + name)   # do not cache a transient failure as 'missing'
    cache[name] = val
    if stat["probes"] % 200 == 0 and len(sys.argv) < 2:
        CACHE_F.write_text(json.dumps(cache))
    return val


day_fmt = {}


def fmt_for(day_et):
    if day_et < FIFTEEN_FROM:
        return "1h"
    if day_et in day_fmt:
        return day_fmt[day_et]
    f = None
    for h in (17, 18, 19):
        d = datetime(day_et.year, day_et.month, day_et.day, h, 0, tzinfo=ET)
        if creation(name15(d)) is not None:
            f = "15"
            break
        if creation(name1h(d)) is not None:
            f = "1h"
            break
    day_fmt[day_et] = f
    return f


def best_for(tipT):
    """(filename, creation ts) with the latest creation <= tipT, or (None, None)."""
    t0 = datetime.fromtimestamp(tipT, timezone.utc).astimezone(ET)
    best = (None, None)
    for step, mk, nmax in ((timedelta(minutes=15), name15, 20), (timedelta(hours=1), name1h, 12)):
        if step == timedelta(hours=1):
            t = t0.replace(minute=0, second=0, microsecond=0)
        else:
            if t0.date() < FIFTEEN_FROM:
                continue
            t = t0.replace(minute=(t0.minute // 15) * 15, second=0, microsecond=0)
        for _ in range(nmax):
            want = fmt_for(t.date())
            if want == ("15" if step == timedelta(minutes=15) else "1h"):
                c = creation(mk(t))
                if c is not None and 0 < c <= tipT:
                    if best[1] is None or c > best[1]:
                        best = (mk(t), c)
                    break
            t = (t.astimezone(timezone.utc) - step).astimezone(ET)
    return best


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if CACHE_F.exists():
        cache.update(json.loads(CACHE_F.read_text()))
    box = D.load_box_v3()
    games = sorted(box["games"].values(), key=lambda g: (g["tip"], g["gid"]))
    warm = sys.argv[1] if len(sys.argv) > 1 else None      # optional: 'rev' or 'mid' = extra worker that only pre-downloads files
    if warm == "rev":
        games = games[::-1]
    elif warm == "mid":
        games = games[len(games) // 2:] + games[:len(games) // 2]
    man, byT = {}, {}
    for i, g in enumerate(games):
        T = g["tip"] - 3600
        if T not in byT:
            byT[T] = best_for(T)
        nm, c = byT[T]
        man[g["gid"]] = {"tip": g["tip"], "season": g["season"], "file": nm, "created_utc": c}
        if i % 100 == 0:
            print(i, len(games), stat, flush=True)
    if warm:
        print("WARM DONE", stat, flush=True)
        return
    CACHE_F.write_text(json.dumps(cache))
    (OUT / "_manifest_v3.json").write_text(json.dumps({"games": man, "stat": stat}, indent=1))
    files = {v["file"] for v in man.values() if v["file"]}
    print("DONE games with a file:", sum(1 for v in man.values() if v["file"]), "/", len(man), "distinct files", len(files), stat, flush=True)


if __name__ == "__main__":
    main()
