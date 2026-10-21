"""Pure logic for the TWC-vs-NWS settlement study (no I/O)."""
from __future__ import annotations
import math, re
from datetime import date

NEG, POS = -10**6, 10**6   # open ends of an interval


def c10_to_f(tenths_c: int) -> int:
    """GHCN-Daily TMAX/TMIN (tenths of deg C) -> whole deg F. GHCN stores US values converted from whole deg F and
    rounded to 0.1 C; one whole F is 0.556 C, so rounding the inverse conversion half-up recovers the original F."""
    return math.floor(tenths_c / 10.0 * 9.0 / 5.0 + 32.0 + 0.5)


def f_to_c10(f: int) -> int:
    """whole F -> tenths C, rounded half away from zero (the forward conversion used to test the round trip)."""
    x = (f - 32) * 50.0 / 9.0
    return int(math.floor(abs(x) + 0.5)) * (1 if x >= 0 else -1)


def market_interval(strike_type: str, floor: float | None, cap: float | None) -> tuple[int, int]:
    """Whole-degree integer interval [lo, hi] for which the market pays YES.
    between: floor..cap inclusive ('between 70-71'); greater: strictly above floor ('greater than 92' -> >=93);
    less: strictly below cap ('less than 85' -> <=84)."""
    if strike_type == "between":
        return int(round(floor)), int(round(cap))
    if strike_type == "greater":
        return int(round(floor)) + 1, POS
    if strike_type == "less":
        return NEG, int(round(cap)) - 1
    raise ValueError(f"unknown strike_type {strike_type!r}")


def event_payout_interval(markets: list[dict]) -> tuple[int | None, int | None, str]:
    """From every settled market of one event [{strike_type,floor,cap,result in yes/no}] return (lo, hi, status):
    the set of whole-degree values consistent with ALL results (yes market -> value inside, no market -> outside).
    status 'ok' (contiguous, non-empty), 'inconsistent' (empty), 'noncontiguous', 'no_yes' (no market paid),
    'unresolved' (a result is not yes/no). The consistent set is intersected over [-150, 150]."""
    if any(m["result"] not in ("yes", "no") for m in markets):
        return None, None, "unresolved"
    lo_all, hi_all = -150, 150
    ok = []
    ivs = [(market_interval(m["strike_type"], m["floor"], m["cap"]), m["result"] == "yes") for m in markets]
    for v in range(lo_all, hi_all + 1):
        if all((lo <= v <= hi) == yes for (lo, hi), yes in ivs):
            ok.append(v)
    if not ok:
        return None, None, "inconsistent"
    if not any(y for _, y in ivs):
        return None, None, "no_yes"
    lo, hi = ok[0], ok[-1]
    if len(ok) != hi - lo + 1:
        return None, None, "noncontiguous"
    return (None if lo == lo_all else lo), (None if hi == hi_all else hi), "ok"


def mismatch(nws: int, lo: int | None, hi: int | None) -> int:
    """0 if the NWS value is inside the paid interval; else signed degrees by which the TWC value is above (+) or
    below (-) the NWS value, inferred as the distance to the nearest edge of the paid interval."""
    if lo is not None and nws < lo:
        return lo - nws
    if hi is not None and nws > hi:
        return hi - nws
    return 0


_MD = {m: i + 1 for i, m in enumerate(["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
                                       "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"])}


def parse_cli(text: str) -> dict | None:
    """Parse an NWS CLI (Climatological Report, Daily) text: {'date','max','min','final'}.
    Uses the first TODAY block ('...CLIMATE SUMMARY FOR OCTOBER 20 2026...'). 'final' is False when the report
    says 'VALID TODAY AS OF <time before midnight>' (partial day) and True for 'VALID AS OF/ AT MIDNIGHT' or
    reports issued for YESTERDAY. Missing values (M/MM) -> None."""
    m = re.search(r"CLIMATE SUMMARY FOR ([A-Z]+) (\d{1,2}) (\d{4})", text)
    if not m or m.group(1) not in _MD:
        return None
    d = date(int(m.group(3)), _MD[m.group(1)], int(m.group(2)))
    head = text[m.end(): m.end() + 200]
    vm = re.search(r"VALID (?:TODAY )?AS OF\s+(\d{3,4})\s*([AP]M)", head)
    final = True          # morning report for the whole previous day carries no VALID TODAY line
    if vm:
        final = False
        hhmm = int(vm.group(1)); h, mi = divmod(hhmm, 100) if hhmm >= 100 else (0, hhmm)
        final = (vm.group(2) == "AM" and h == 12) or (vm.group(2) == "PM" and h == 11 and mi >= 59)
    def grab(label):
        mm = re.search(rf"^\s*{label}\s+(-?\d+|MM|M)\b", text[m.end():], re.M)
        if not mm or mm.group(1) in ("M", "MM"):
            return None
        return int(mm.group(1))
    return {"date": d.isoformat(), "max": grab("MAXIMUM"), "min": grab("MINIMUM"), "final": final}
