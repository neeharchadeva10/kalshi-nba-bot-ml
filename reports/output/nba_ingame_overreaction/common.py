"""Research log #46 helpers (read-only). Pure functions unit-tested in test_ingame.py.
Fees/implied_total copied from #44's common.py (itself a copy of the dashboard logic); not importing those files."""
from __future__ import annotations

import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
INJ = ROOT / "reports" / "output" / "nba_injury_speed"
RAW_NS = "kalshi_1m_totals_ingame"
SEED = 20261022
DISC_FIRST, DISC_LAST = "2025-10-21", "2026-01-22"
UNSEEN_FIRST, UNSEEN_LAST = "2026-01-23", "2026-04-12"   # last regular-season ET date in events_list
FETCH_SPAN_S = 5 * 3600          # tip .. tip+5h (settlement of every NBA game is well inside this)
CHECKPOINTS = ("Q1", "HT", "Q3")  # end of Q1 (first event of Q2), halftime (first of Q3), end of Q3 (first of Q4)
CP_PERIOD = {"Q1": 2, "HT": 3, "Q3": 4}
THRESHOLDS = (4, 6, 8, 10)
DIRECTIONS = ("fade", "follow")
EXITS = ("hold", "next")
ENTRY_SLIP = 0.01
EXIT_SLIP = 0.01
DECISION_LAG_S = 60
MAIN_SPREAD = 0.10
LINE_SPREAD = 0.15
MIN_N = 60
BAD_WALLCLOCK_MIN = (20, 80)


def fee_taker(price: float, count: int = 1) -> float:
    """Kalshi taker fee for ONE order: 0.07*C*P*(1-P) rounded UP to $0.0001."""
    if not 0.0 <= price <= 1.0:
        raise ValueError(price)
    raw = 0.07 * count * price * (1.0 - price)
    return round(math.ceil(raw / 0.0001 - 1e-9) * 0.0001, 4)


def implied_total(points):
    """points: iterable of (strike, yes_bid, yes_ask). Keep 0<bid<=ask<1, spread<=0.15. Interpolated strike where YES mid
    crosses 0.50; None when no crossing (all mids on one side of 0.5)."""
    pts = []
    for s, b, a in points:
        try:
            s, b, a = float(s), float(b), float(a)
        except (TypeError, ValueError):
            continue
        if 0 < b <= a < 1 and a - b <= LINE_SPREAD + 1e-9:
            pts.append((s, (a + b) / 2))
    pts.sort()
    if not pts:
        return None
    if pts[0][1] < 0.5 or pts[-1][1] > 0.5:
        return None
    for (s1, m1), (s2, m2) in zip(pts, pts[1:]):
        if m1 >= 0.5 >= m2:
            if m1 == m2:
                return s1
            return s1 + (m1 - 0.5) / (m1 - m2) * (s2 - s1)
    return pts[-1][0] if pts[-1][1] == 0.5 else None


def main_strike(points):
    """Live main strike: YES mid nearest 0.50 among strikes with 0<bid<=ask<1 and spread<=0.10.
    Ties broken by the lower strike. Returns (strike, bid, ask) or None."""
    best = None
    for s, b, a in points:
        if b is None or a is None:
            continue
        if 0 < b <= a < 1 and a - b <= MAIN_SPREAD + 1e-9:
            key = (abs((a + b) / 2 - 0.5), s)
            if best is None or key < best[0]:
                best = (key, (s, b, a))
    return None if best is None else best[1]


def side_for(direction: str, line_c: float, line_0: float) -> str:
    """FADE: bet back toward L0 (L_C > L0 -> NO, else YES). FOLLOW: opposite."""
    fade = "no" if line_c > line_0 else "yes"
    if direction == "fade":
        return fade
    return "yes" if fade == "no" else "no"


def side_quotes(side, yes_bid, yes_ask):
    """(ask, bid) of the chosen side. NO ask = 1 - yes_bid; NO bid = 1 - yes_ask."""
    if side == "yes":
        return yes_ask, yes_bid
    return 1.0 - yes_bid, 1.0 - yes_ask


def net_hold(side, result, ask):
    """One contract bought at ask+1c slippage, held to settlement. result: 'yes'/'no' of the YES strike market."""
    p = min(ask + ENTRY_SLIP, 0.99)
    win = 1.0 if result == side else 0.0
    return win - p - fee_taker(p)


def net_next(ask, bid_exit):
    """Buy at ask+1c, sell at bid-1c at the next checkpoint; taker fee on both orders."""
    p = min(ask + ENTRY_SLIP, 0.99)
    q = max(bid_exit - EXIT_SLIP, 0.0)
    return q - p - fee_taker(p) - fee_taker(q)


def lb(vals):
    import statistics
    n = len(vals)
    if n < 2:
        return float("nan")
    return statistics.fmean(vals) - 1.96 * statistics.stdev(vals) / math.sqrt(n)


def grid():
    return [(cp, T, d, ex) for cp in CHECKPOINTS for T in THRESHOLDS for d in DIRECTIONS for ex in EXITS]


SELECTED = ("Q1", 10, "follow", "hold")   # frozen on discovery (research log #46 amendment 2026-10-22)


def running_downloaders():
    """Command lines of running python processes that look like the #44 or #46 candle downloaders."""
    import subprocess
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | ForEach-Object { $_.CommandLine }"],
                         capture_output=True, text=True).stdout
    return [l.strip() for l in out.splitlines() if (("nba_injury_speed" in l and "download_candles.py" in l) or ("nba_ingame_overreaction" in l and "download.py" in l))]


def coverage(first, last, events_csv=None):
    """Download-state coverage of every market of events with a hoopr_tip and ET date in [first, last], in BOTH the #44
    pre-tip table and the in-game table. Final states: ok / http_404."""
    import sqlite3
    from collections import Counter
    import pandas as pd
    ev = pd.read_csv(events_csv or (INJ / "events_list.csv"))
    ev = ev[(ev.game_date_et >= first) & (ev.game_date_et <= last) & ev.hoopr_tip.notna()]
    inj = sqlite3.connect(f"file:{(INJ / 'candles.sqlite').as_posix()}?mode=ro", uri=True)
    tk = {t for (t,) in inj.execute("SELECT ticker FROM mkts WHERE event IN (%s)" % ",".join("?" * len(ev)), list(ev.event))}
    pre = dict(inj.execute("SELECT ticker, note FROM done").fetchall())
    inj.close()
    ing = sqlite3.connect(f"file:{(HERE / 'candles_ingame.sqlite').as_posix()}?mode=ro", uri=True)
    done = dict(ing.execute("SELECT ticker, note FROM done").fetchall())
    ing.close()
    fin = ("ok", "http_404")
    return {"markets": len(tk),
            "pre_tip_not_final": sum(pre.get(t) not in fin for t in tk),
            "ingame_not_final": sum(done.get(t) not in fin for t in tk),
            "pre_tip_notes": dict(Counter(pre.get(t, "missing") for t in tk)),
            "ingame_notes": dict(Counter(done.get(t, "missing") for t in tk)),
            "not_downloaded": sum(pre.get(t) not in fin or done.get(t) not in fin for t in tk)}
