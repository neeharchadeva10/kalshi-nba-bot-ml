"""Pure, importable core for the Ohio-trial lock watcher and report. No network, no I/O, no order code.

Detection math and grouping logic are the same as reports/output/nba_picks/26OCT20/lockwatch.py (refactored here,
plus per-leg sizes/fees in each lock and open/close metadata), then the pre-registration's report logic
(de-duplication, reachability, value excluding the biggest event).
"""
from __future__ import annotations

import math
import re
import statistics
from datetime import datetime

CAP = 10
PROP_SERIES = frozenset("KXNBA" + s for s in ("PTS", "REB", "AST", "3PT", "PRA", "STL", "BLK", "STOCK", "2D", "3D"))
DEDUP_WINDOW_MS = 50.0
LIFETIME_BUCKETS_MS = (0, 10, 30, 100, 300, 1000, 10000, float("inf"))


def fee(p: float, n: float) -> float:
    return math.ceil(0.07 * n * p * (1 - p) / 0.0001 - 1e-6) * 0.0001


# ------------------------------------------------------------------ ticker helpers
def series_of(ticker: str) -> str:
    return ticker.split("-")[0]


def game_of(ticker: str) -> str:
    parts = ticker.split("-")
    return parts[1] if len(parts) > 1 else ""


def night_of(game: str) -> str:
    m = re.match(r"\d{2}[A-Z]{3}\d{2}", game or "")
    return m.group(0) if m else "unknown"


def is_prop_series(series: str) -> bool:
    return series in PROP_SERIES


def leg_ticker(leg: str) -> str:
    return leg.split(" ", 1)[1] if " " in leg else leg


def market_type(series: str) -> str:
    """Coarse label for reporting: prop, or [period:]moneyline/spread/total/teamtotal/winmargin/race/ot/other."""
    if series in PROP_SERIES:
        return "prop"
    s = series[len("KXNBA"):] if series.startswith("KXNBA") else series
    period = ""
    m = re.match(r"(1H|2H|1Q|2Q|3Q|4Q)(.*)", s)
    if m:
        period, s = m.group(1) + ":", m.group(2)
    for key, label in (("TEAMTOTAL", "teamtotal"), ("WINMARGIN", "winmargin"), ("SPREAD", "spread"), ("TOTAL", "total"),
                       ("RACE", "race"), ("OT", "ot"), ("GAME", "moneyline")):
        if key in s:
            return period + label
    return period + ("winner" if s == "" else "other")


# ------------------------------------------------------------------ structure (same grouping logic as lockwatch.py)
def strike(b: dict):
    st = b.get("strike_type")
    if st in ("greater", "greater_or_equal"):
        return "ge", b.get("floor")
    if st in ("less", "less_or_equal"):
        return "le", b.get("cap")
    return None, None


def build_structure(doc: dict):
    """-> (groups {(event, sub, d): sorted [(k, ticker)]}, me_sets {event: (tickers, exhaustive)}, groups_of, sets_of)"""
    events = doc["events"]
    evkeys = sorted(events, key=len, reverse=True)
    ev_of = {}
    for t in doc["books"]:
        ev_of[t] = next(e for e in evkeys if t.startswith(e + "-"))
    groups: dict = {}
    for t, b in doc["books"].items():
        d, k = strike(b)
        if d is None or k is None:
            continue
        parts = t.split("-")
        sub = parts[2] if len(parts) >= 4 else re.sub(r"\d+$", "", parts[2])
        groups.setdefault((ev_of[t], sub, d), []).append((k, t))
    for L in groups.values():
        L.sort()
    byev: dict = {}
    for t, e in ev_of.items():
        byev.setdefault(e, []).append(t)
    me_sets = {}
    for e, L in byev.items():
        exh = e.startswith("KXNBAWINMARGIN") or e.startswith("KXNBAGAME")
        if events[e].get("mutually_exclusive") or exh:
            me_sets[e] = (sorted(L), exh)
    groups_of: dict = {}
    for key, L in groups.items():
        for _, t in L:
            groups_of.setdefault(t, []).append(key)
    sets_of: dict = {}
    for e, (L, _) in me_sets.items():
        for t in L:
            sets_of.setdefault(t, []).append(e)
    return groups, me_sets, groups_of, sets_of


# ------------------------------------------------------------------ detection math (tops: (bid, bsz, ask, asz))
def ladder_lock(weak: str, strong: str, tw, ts):
    """Buy YES weak at ask, buy NO strong at 1 - bid(strong). -> dict or None"""
    if not tw or not ts or tw[2] is None or ts[0] is None:
        return None
    ask, asz, bid, bsz = tw[2], tw[3], ts[0], ts[1]
    n = math.floor(min(asz, bsz, CAP) + 1e-9)
    if n < 1:
        return None
    fees = [fee(ask, n), fee(1 - bid, n)]
    profit = n * (bid - ask) - sum(fees)
    if profit <= 0:
        return None
    return dict(kind="ladder", legs=[f"YES {weak}", f"NO {strong}"], prices=[ask, round(1 - bid, 4)], sizes=[asz, bsz],
                fees=[round(f, 4) for f in fees], n=n, profit=round(profit, 4), per_contract=round(profit / n, 4))


def me_locks(event: str, tickers: list, tops: dict, exhaustive: bool):
    out = []
    ts = [tops.get(t) for t in tickers]
    if any(x is None for x in ts):
        return out
    if all(x[0] is not None for x in ts):
        n = math.floor(min(min(x[1] for x in ts), CAP) + 1e-9)
        if n >= 1:
            sb = sum(x[0] for x in ts)
            fees = [fee(1 - x[0], n) for x in ts]
            profit = n * (sb - 1) - sum(fees)
            if profit > 0:
                out.append(dict(kind="me_no", legs=[f"NO {t}" for t in tickers], prices=[round(1 - x[0], 4) for x in ts],
                                sizes=[x[1] for x in ts], fees=[round(f, 4) for f in fees], n=n,
                                profit=round(profit, 4), per_contract=round(profit / n, 4)))
    if exhaustive and all(x[2] is not None for x in ts):
        n = math.floor(min(min(x[3] for x in ts), CAP) + 1e-9)
        if n >= 1:
            sa = sum(x[2] for x in ts)
            fees = [fee(x[2], n) for x in ts]
            profit = n * (1 - sa) - sum(fees)
            if profit > 0:
                out.append(dict(kind="me_yes", legs=[f"YES {t}" for t in tickers], prices=[x[2] for x in ts],
                                sizes=[x[3] for x in ts], fees=[round(f, 4) for f in fees], n=n,
                                profit=round(profit, 4), per_contract=round(profit / n, 4)))
    return out


class Detector:
    """Open-lock state machine. tops_fn(t) -> (bid,bsz,ask,asz) if the book is valid else None.
    update(t, now, meta): meta (any dict, e.g. recv_ns/ts_ms of the triggering message) is stored on open as
    st['open_meta'] and on close as st['close_meta']; emit(ev, st, now) is called for lock_open / lock_close."""

    def __init__(self, doc: dict, tops_fn, emit):
        self.groups, self.me_sets, self.groups_of, self.sets_of = build_structure(doc)
        self.tops_fn, self.emit = tops_fn, emit
        self.open: dict = {}
        self.n_opened = 0

    def _ladder_keys(self, t: str):
        found, scope = {}, set()
        for gk in self.groups_of.get(t, []):
            d = gk[2]
            L = self.groups[gk]
            k_t = next(k for k, x in L if x == t)
            for k, x in L:
                if x == t:
                    continue
                for (k1, t1), (k2, t2) in (((k_t, t), (k, x)), ((k, x), (k_t, t))):
                    if (d == "ge" and not k2 > k1) or (d == "le" and not k2 < k1):
                        continue
                    key = ("L", t1, t2)
                    scope.add(key)
                    lk = ladder_lock(t1, t2, self.tops_fn(t1), self.tops_fn(t2))
                    if lk:
                        found[key] = (lk, (t1, t2))
        return found, scope

    def _me_keys(self, t: str):
        found, scope = {}, set()
        for e in self.sets_of.get(t, []):
            L, exh = self.me_sets[e]
            scope |= {("ME", e, "me_no"), ("ME", e, "me_yes")}
            tops = {x: self.tops_fn(x) for x in L}
            for lk in me_locks(e, L, tops, exh):
                found[("ME", e, lk["kind"])] = (lk, tuple(L))
        return found, scope

    def update(self, t: str, now: float, meta: dict | None = None):
        f1, s1 = self._ladder_keys(t)
        f2, s2 = self._me_keys(t)
        found, scope = {**f1, **f2}, s1 | s2
        for key, (lk, tks) in found.items():
            st = self.open.get(key)
            if st is None:
                self.open[key] = dict(lk, t_open=now, max_profit=lk["profit"], max_n=lk["n"], tickers=tks, key=key,
                                      first=lk, open_meta=meta)
                self.n_opened += 1
                self.emit("lock_open", self.open[key], now)
            else:
                if lk["profit"] > st["max_profit"]:
                    st["max_profit"] = lk["profit"]
                st["max_n"] = max(st["max_n"], lk["n"])
        for key in [k for k in self.open if k in scope and k not in found]:
            st = self.open.pop(key)
            st["close_meta"] = meta
            self.emit("lock_close", st, now)

    def close_all(self, now: float, meta: dict | None = None):
        for key in list(self.open):
            st = self.open.pop(key)
            st["close_meta"] = meta
            self.emit("lock_close", st, now)


# ------------------------------------------------------------------ report logic (pre-registration)
def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))] if v else None


def lifetime_bucket(ms: float) -> str:
    b = LIFETIME_BUCKETS_MS
    for lo, hi in zip(b, b[1:]):
        if lo <= ms < hi:
            return f"{lo:g}-{hi:g}ms" if hi != float("inf") else f">={lo:g}ms"
    return "n/a"


def _iso_ns(s):
    try:
        return int(round(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1e6)) * 1000
    except (ValueError, AttributeError):
        return None


def normalize_close(rec: dict):
    """A lock_close log record -> normalized lock dict, tolerant of old records (no ns/ts_ms/game/is_prop)."""
    legs = rec.get("legs") or []
    if not legs:
        return None
    tk = leg_ticker(legs[0])
    series = rec.get("series") or series_of(tk)
    game = rec.get("game") or game_of(tk)
    open_ns = rec.get("open_ns")
    if open_ns is None:
        open_ns = _iso_ns(rec.get("t_open"))
    if rec.get("lifetime_ms") is not None:
        life = float(rec["lifetime_ms"])
    elif rec.get("duration_s") is not None:
        life = float(rec["duration_s"]) * 1000.0
    elif rec.get("close_ns") and open_ns:
        life = (rec["close_ns"] - open_ns) / 1e6
    else:
        return None
    if open_ns is None:
        return None
    profit = rec.get("max_profit", rec.get("profit"))
    if profit is None:
        return None
    is_prop = rec["is_prop"] if "is_prop" in rec else is_prop_series(series)
    return dict(game=game, night=night_of(game), series=series, kind=rec.get("kind"), legs=list(legs), open_ns=int(open_ns),
                life_ms=life, profit=float(profit), is_prop=bool(is_prop), mtype=market_type(series))


def dedupe(locks: list, window_ms: float = DEDUP_WINDOW_MS) -> list:
    """Locks sharing any leg and opening within window_ms of each other form one event (connected components).
    Event value = its most profitable lock (that lock's lifetime is the event's). -> list of event dicts."""
    n = len(locks)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_leg: dict = {}
    for i, lk in enumerate(locks):
        for leg in lk["legs"]:
            by_leg.setdefault(leg, []).append((lk["open_ns"], i))
    win = window_ms * 1e6
    for L in by_leg.values():
        L.sort()
        for (a, i), (b, j) in zip(L, L[1:]):          # consecutive links suffice for connectivity within the window
            if b - a <= win:
                parent[find(i)] = find(j)
    comp: dict = {}
    for i in range(n):
        comp.setdefault(find(i), []).append(i)
    events = []
    for idxs in comp.values():
        best = max((locks[i] for i in idxs), key=lambda x: x["profit"])
        events.append(dict(best, n_locks=len(idxs), open_ns=min(locks[i]["open_ns"] for i in idxs)))
    events.sort(key=lambda e: e["open_ns"])
    return events


def reachable(events: list, reaction_ms: float, margin_ms: float = 2.0) -> list:
    thr = reaction_ms + margin_ms
    return [e for e in events if e["life_ms"] >= thr]


def value_by_night(events: list, nights=None) -> dict:
    out = {n: 0.0 for n in (nights or [])}
    for e in events:
        out[e["night"]] = out.get(e["night"], 0.0) + e["profit"]
    return out


def mean_value_per_night(events: list, nights, drop_biggest: bool = False) -> float:
    nights = set(nights) | {e["night"] for e in events}
    if not nights:
        return 0.0
    tot = sum(e["profit"] for e in events)
    if drop_biggest and events:
        tot -= max(e["profit"] for e in events)
    return tot / len(nights)


def delay_stats(samples: list, sntp: list) -> dict:
    """samples: [(recv_ns, ts_ms)], sntp: [(recv_ns, offset_ms)] where offset_ms = true - local. Corrected delay (ms) =
    recv_ms + offset - ts_ms using the latest SNTP offset at or before the sample (first one for earlier samples)."""
    if not samples:
        return {"n": 0}
    sn = sorted(sntp)
    d = []
    for r, ts in samples:
        off = 0.0
        if sn:
            off = sn[0][1]
            for t, o in sn:
                if t <= r:
                    off = o
                else:
                    break
        d.append(r / 1e6 + off - ts)
    return {"n": len(d), "offset_corrected": bool(sn), "median_ms": round(statistics.median(d), 2),
            "p90_ms": round(pct(d, 90), 2), "p99_ms": round(pct(d, 99), 2), "min_ms": round(min(d), 2),
            "max_ms": round(max(d), 2)}
