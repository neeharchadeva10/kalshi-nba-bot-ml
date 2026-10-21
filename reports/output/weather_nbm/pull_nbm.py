"""Bulk pull of NBM NBS station daily-max forecasts (pre-registration #30). Read-only, anonymous HTTPS.

Usage: python pull_nbm.py DAYS_FILE OUT_JSONL [THREADS]
DAYS_FILE: one YYYY-MM-DD event date per line.  For each day D fetch 06Z of D, 00Z of D and, only if the 00Z file is
missing or was published after 21:00 ET on D-1, 18Z of D-1.  Output: one JSON line per file with S3 Last-Modified,
bytes read, NBM version, issue time and per-station (txn, xnd, fhr, valid_end) for target_date D.  Resumable.
"""
import io, json, sys, time, threading, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import nbm_text as nt
import nbm_runsel as rs

STATIONS = ["KATL", "KAUS", "KBOS", "KDCA", "KDEN", "KDFW", "KEWR", "KHOU", "KLAS", "KLAX", "KMDW", "KMIA",
            "KMSP", "KMSY", "KNYC", "KOKC", "KPHL", "KPHX", "KSAN", "KSAT", "KSDF", "KSEA", "KSFO", "KTTN"]
lock = threading.Lock()
stats = dict(bytes=0, files=0, fail=0)


class Counter(io.RawIOBase):
    def __init__(self, r):
        super().__init__(); self.r = r; self.n = 0
    def readable(self): return True
    def readinto(self, buf):
        b = self.r.read(len(buf)); buf[:len(b)] = b; self.n += len(b); return len(b)


def fetch(day_dir, cycle, target):
    """Return dict(record) or dict(missing=True)."""
    url = nt.url(day_dir, cycle)
    last_err = None
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                lm = parsedate_to_datetime(r.headers["Last-Modified"]).astimezone(timezone.utc)
                c = Counter(r)
                fh = io.TextIOWrapper(io.BufferedReader(c), encoding="ascii", errors="replace")
                blocks = {s: nt.parse_block(b) for s, b in nt.iter_blocks(fh, STATIONS)}
                nb = c.n
            rows = {}
            ver = None
            issued = None
            for s, b in blocks.items():
                ver, issued = b["version"], b["issued"]
                for m in b["maxes"]:
                    if m["target_date"] == target:
                        rows[s] = dict(txn=m["txn"], xnd=m["xnd"], fhr=m["fhr"], valid_end=m["valid_end_utc"].isoformat())
            with lock:
                stats["bytes"] += nb; stats["files"] += 1
            return dict(day_dir=day_dir, cycle=cycle, target=target, last_modified=lm.isoformat(), bytes=nb,
                        version=ver, issued=issued.isoformat() if issued else None, rows=rows,
                        n_blocks=len(blocks))
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return dict(day_dir=day_dir, cycle=cycle, target=target, missing=True, http=e.code)
            last_err = repr(e)
        except Exception as e:
            last_err = repr(e)
        time.sleep(2 ** attempt)
    with lock:
        stats["fail"] += 1
    return dict(day_dir=day_dir, cycle=cycle, target=target, failed=last_err)


def do_day(day, out, done):
    d = date.fromisoformat(day)
    prev = (d - timedelta(days=1)).strftime("%Y%m%d")
    dd = d.strftime("%Y%m%d")
    recs = []
    for dir_, cyc in ((dd, "06"), (dd, "00")):
        if (dir_, cyc, day) not in done:
            recs.append(fetch(dir_, cyc, day))
    r00 = next((r for r in recs if r["cycle"] == "00" and r["day_dir"] == dd), None)
    if r00 is None:
        r00 = done.get((dd, "00", day))
    need18 = True
    if r00 and not r00.get("missing") and not r00.get("failed"):
        lm = datetime.fromisoformat(r00["last_modified"])
        need18 = rs.choose_evening(day, lm, None) is None
    if need18 and (prev, "18", day) not in done:
        recs.append(fetch(prev, "18", day))
    with lock:
        for r in recs:
            out.write(json.dumps(r) + "\n")
        out.flush()


def main():
    days = [l.strip() for l in open(sys.argv[1]) if l.strip()]
    outp = sys.argv[2]
    nthreads = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    done = {}
    try:
        for l in open(outp):
            r = json.loads(l)
            if not r.get("failed"):
                done[(r["day_dir"], r["cycle"], r["target"])] = r
    except FileNotFoundError:
        pass
    out = open(outp, "a")
    t0 = time.time()
    n = 0
    with ThreadPoolExecutor(nthreads) as ex:
        futs = [ex.submit(do_day, d, out, done) for d in days]
        for f in futs:
            f.result(); n += 1
            if n % 50 == 0:
                print(f"{n}/{len(days)} days  files={stats['files']} bytes={stats['bytes']} fail={stats['fail']} "
                      f"{time.time()-t0:.0f}s", flush=True)
    print("DONE", stats, f"{time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
