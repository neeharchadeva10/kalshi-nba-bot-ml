"""Prototype parser/streamer for NBM NBS text bulletins (NOAA Open Data, noaa-nbm-grib2-pds).

NBS block layout (verified on V3.2 .. V4.3): header line "<ID> NBM Vx.y NBS GUIDANCE m/dd/yyyy HHMM UTC",
then rows "<space+LABEL(3)+space> then 3-char right-aligned fields (data starts col 6)".  UTC row = hour of each column, FHR row = forecast hour.
TXN at a 00 UTC column = daytime max (12h ending 00Z); at a 12 UTC column = night min.
XND = standard deviation of TXN (deg F, integer).
Run: python -I nbm_text.py   (runs unit tests on samples/)
"""
import io
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

BUCKET = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"
HDR = re.compile(r"^\s*(\S+)\s+NBM V(\d+\.\d+)\s+NBS GUIDANCE\s+(\d+)/(\d+)/(\d{4})\s+(\d{2})(\d{2}) UTC")


def url(day, cycle, prod="nbstx"):
    """day='YYYYMMDD', cycle='06' -> object URL."""
    return f"{BUCKET}/blend.{day}/{cycle}/text/blend_{prod}.t{cycle}z"


def _fields(line):
    body = line[5:]
    return [body[i:i + 3].strip() for i in range(0, len(body), 3)]


def parse_block(lines):
    """lines: list of str for ONE station block. Returns dict with station, version, issued (aware UTC dt),
    and 'maxes': list of dict(valid_end_utc, local_date_utc_basis, fhr, txn, xnd) for daytime max columns."""
    m = HDR.match(lines[0])
    if not m:
        raise ValueError("bad header: %r" % lines[0])
    stn, ver, mo, dy, yr, hh, mm = m.groups()
    issued = datetime(int(yr), int(mo), int(dy), int(hh), int(mm), tzinfo=timezone.utc)
    rows = {}
    for ln in lines[1:]:
        if len(ln) >= 4 and ln[:4].strip() in ("UTC", "FHR", "TXN", "XND") and ln[:4].strip() not in rows:
            rows[ln[:4].strip()] = _fields(ln)
    out = []
    for i, (u, f) in enumerate(zip(rows["UTC"], rows["FHR"])):
        t = rows["TXN"][i] if i < len(rows["TXN"]) else ""
        if u == "00" and t not in ("", "-99"):
            x = rows["XND"][i] if i < len(rows["XND"]) else ""
            valid_end = issued.replace(minute=0) + timedelta(hours=int(f))
            out.append(dict(valid_end_utc=valid_end,
                            target_date=(valid_end - timedelta(hours=12)).date().isoformat(),  # UTC day the 12Z-00Z window starts
                            fhr=int(f), txn=int(t), xnd=int(x) if x not in ("", "-99") else None))
    return dict(station=stn, version=ver, issued=issued, maxes=out)


def iter_blocks(fh, wanted):
    """Stream a text file object; yield (station, [lines]) for wanted ids; stops early once past max(wanted)
    in ASCII order (file is sorted by id). Discards everything else."""
    wanted = set(wanted)
    last = max(wanted)
    cur, buf = None, []
    for ln in fh:
        ln = ln.rstrip("\n")
        m = HDR.match(ln)
        if m:
            if cur in wanted:
                yield cur, buf
            cur, buf = m.group(1), [ln]
            if cur > last and not cur[0].isdigit():
                return
        elif cur in wanted:
            buf.append(ln)
    if cur in wanted:
        yield cur, buf


def stream_station_blocks(day, cycle, stations, prod="nbstx"):
    """Download (streamed, never stored whole) and return {station: parsed block}."""
    with urllib.request.urlopen(url(day, cycle, prod), timeout=120) as r:
        fh = io.TextIOWrapper(r, encoding="ascii", errors="replace")
        return {s: parse_block(b) for s, b in iter_blocks(fh, stations)}


def _test():
    import glob, os
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")
    files = sorted(glob.glob(os.path.join(d, "*.txt")))
    assert files, "no samples"
    for f in files:
        with open(f, encoding="ascii", errors="replace") as fh:
            res = {s: parse_block(b) for s, b in iter_blocks(fh, ["KMIA", "KNYC", "KMDW", "KAUS"])}
        assert res, f
        for s, r in res.items():
            assert r["maxes"], (f, s)
            assert all(30 <= m["txn"] <= 125 for m in r["maxes"]), (f, s)
            assert all(m["xnd"] is None or 0 <= m["xnd"] <= 20 for m in r["maxes"])
        s0 = res[sorted(res)[0]]
        print(os.path.basename(f), "V" + s0["version"], s0["issued"].isoformat(), sorted(res))
        for s in sorted(res):
            m = res[s]["maxes"][0]
            print("   ", s, "first max: valid_end", m["valid_end_utc"].isoformat(), "day", m["target_date"], "TXN", m["txn"], "XND", m["xnd"])
    print("OK", len(files), "files")


if __name__ == "__main__":
    _test()
