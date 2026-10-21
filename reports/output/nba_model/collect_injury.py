"""Research log #31 injury-report downloader (read-only, public files).
The NBA publishes injury-report PDFs at https://ak-static.cms.nba.com/referee/injury/ ; file names are US Eastern
wall-clock stamps. Observed naming (probed, see README_V2): hourly 'Injury-Report_YYYY-MM-DD_05PM.pdf' until about
2025-12-20, then 15-minute 'Injury-Report_YYYY-MM-DD_05_30PM.pdf'. The PDF metadata /CreationDate is the real
publication time (hourly files are created ~30 min after their stamp), so PUBLICATION time = CreationDate, and a
game's report = the file with the latest CreationDate <= scheduled tip - 3600 s (stricter than the file-name stamp).
Saves ONLY into data/raw/nba_injury/. Parses nothing; never executes downloaded content. 0.25 s between requests.
Run: .venv/Scripts/python.exe reports/output/nba_model/collect_injury.py
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import features as F  # noqa: E402

ROOT = HERE.parents[2]
OUT = ROOT / "data" / "raw" / "nba_injury"
ET = ZoneInfo("America/New_York")
BASE = "https://ak-static.cms.nba.com/referee/injury/"
DELAY = 0.25
UA = {"User-Agent": "Mozilla/5.0 (research; read-only)"}
CD = re.compile(rb"/CreationDate\(D:(\d{14})([+-]\d{2})'(\d{2})'\)")
cache = {}      # filename -> creation utc ts or None
nprobe = 0


def name15(d):
    return "Injury-Report_" + d.strftime("%Y-%m-%d_%I_%M%p") + ".pdf"


def name1h(d):
    return "Injury-Report_" + d.strftime("%Y-%m-%d_%I%p") + ".pdf"


def creation(name):
    """Creation UTC ts of a published file (reads only the first 4 KB), or None if it does not exist."""
    global nprobe
    if name in cache:
        return cache[name]
    nprobe += 1
    req = urllib.request.Request(BASE + name, headers=UA)
    val = None
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            head = r.read(4096)
        m = CD.search(head)
        if head.startswith(b"%PDF") and m:
            dt = datetime.strptime(m.group(1).decode(), "%Y%m%d%H%M%S")
            sign = 1 if m.group(2)[:1] == b"+" else -1
            off = timedelta(hours=int(m.group(2)[1:]), minutes=int(m.group(3))) * sign
            val = (dt - off).replace(tzinfo=timezone.utc).timestamp()
        else:
            val = -1.0   # exists but no creation date: treated as unusable
    except urllib.error.HTTPError as e:
        if e.code not in (403, 404):
            print("unexpected http", e.code, name)
    except Exception as e:  # noqa: BLE001
        print("err", name, type(e).__name__)
    time.sleep(DELAY)
    cache[name] = val
    return val


day_fmt = {}


def fmt_for(day_et):
    """'15' or '1h' or None for an ET date, decided by probing one evening file."""
    if day_et in day_fmt:
        return day_fmt[day_et]
    f = None
    for h in (17, 18):
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
    for step, mk, nmax in ((timedelta(minutes=15), name15, 20), (timedelta(hours=1), name1h, 6)):
        if step == timedelta(hours=1):
            t = t0.replace(minute=0, second=0, microsecond=0)
        else:
            t = t0.replace(minute=(t0.minute // 15) * 15, second=0, microsecond=0)
        for _ in range(nmax):
            d = t
            want = fmt_for(d.date())
            if want == ("15" if step == timedelta(minutes=15) else "1h"):
                c = creation(mk(d))
                if c is not None and 0 < c <= tipT:
                    if best[1] is None or c > best[1]:
                        best = (mk(d), c)
                    break
            t = (t.astimezone(timezone.utc) - step).astimezone(ET)
    return best


def download(name):
    p = OUT / name
    if p.exists() and p.stat().st_size > 1000:
        return
    req = urllib.request.Request(BASE + name, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    assert data.startswith(b"%PDF")
    p.write_bytes(data)
    time.sleep(DELAY)


def main():
    games, _, _ = F.drift.load_nba()
    games = sorted(games, key=lambda g: (g.tip, g.gid))
    OUT.mkdir(parents=True, exist_ok=True)
    man, byT = {}, {}
    for i, g in enumerate(games):
        T = g.tip - 3600
        if T not in byT:
            byT[T] = best_for(T)
        nm, c = byT[T]
        man[g.gid] = {"tip": g.tip, "file": nm, "created_utc": c}
        if i % 100 == 0:
            print(i, len(games), "probes", nprobe, flush=True)
    files = sorted({v["file"] for v in man.values() if v["file"]})
    print("distinct files to download:", len(files), flush=True)
    for k, nm in enumerate(files):
        download(nm)
        if k % 100 == 0:
            print("dl", k, flush=True)
    (OUT / "_manifest.json").write_text(json.dumps({"games": man, "probes": nprobe, "day_format": {str(k): v for k, v in day_fmt.items()}}, indent=1))
    print("games with a file:", sum(1 for v in man.values() if v["file"]), "/", len(man), "probes", nprobe)


if __name__ == "__main__":
    main()
