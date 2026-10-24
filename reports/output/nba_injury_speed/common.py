"""Research log #44 shared helpers (read-only study). Pure functions here are unit-tested in test_injury_speed.py."""
from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "reports" / "output" / "nba_totals_pace"))
sys.path.insert(0, str(ROOT / "reports" / "output" / "nba_model"))
ET = ZoneInfo("America/New_York")
SEED = 20261022
DISC_FIRST, DISC_LAST = "2025-10-21", "2026-01-22"      # ET game dates, inclusive
UNSEEN_FIRST = "2026-01-23"
RAW_NS = "kalshi_1m_totals"
CANDLE_BACK_S = 8 * 3600     # fetch T-8h..T (pre-reg says T-6h; 2h extra so M(P-30) exists for P near T-6h)


def et_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(ET).strftime("%Y-%m-%d")


def fee_taker(price: float, count: int = 1) -> float:
    """Kalshi taker fee for ONE order: 0.07*C*P*(1-P) rounded UP to $0.0001 (pre-registered rounding)."""
    if not 0.0 <= price <= 1.0:
        raise ValueError(price)
    raw = 0.07 * count * price * (1.0 - price)
    return round(math.ceil(raw / 0.0001 - 1e-9) * 0.0001, 4)


def implied_total(points):
    """Copy of the logic of scripts/live_scores_dashboard.py::implied_total (not imported).
    points: iterable of (strike, yes_bid, yes_ask). Strikes kept: 0 < bid <= ask < 1 and ask - bid <= 0.15.
    Returns the strike-interpolated total where the YES mid crosses 0.50, or None when there is no crossing
    (dashboard returns '< bottom strike' / '> top strike' strings there; the pre-registration says: missing)."""
    pts = []
    for s, b, a in points:
        try:
            s, b, a = float(s), float(b), float(a)
        except (TypeError, ValueError):
            continue
        if 0 < b <= a < 1 and a - b <= 0.15:
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
