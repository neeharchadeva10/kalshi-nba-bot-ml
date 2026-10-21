"""DIAGNOSTIC ONLY (not part of the pre-registered check; written after the pre-registered tip check FAILED).
Where does in-game activity actually start relative to occurrence_datetime - 3h?  Same 20 NBA games as tipcheck.py (seed
20261020; cached candles) plus 20 random NFL games from data/nfl_spread.sqlite (tip = expected_expiration_time - 3h).
Onset definitions (both on the 1-min series, minutes relative to the inferred tip):
  vol onset  = first minute m >= -30 where volume >= 5 x median(volume over [-30,-5]) for 3 consecutive minutes
  move onset = first minute where |mid_t - mid(-5)| >= 0.02 and stays >= 0.02 on the next 2 minutes
"""
import json
import random
import sqlite3
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from kpm.clients.kalshi import KalshiClient  # noqa: E402


def onsets(rows, tip):
    """rows: [(ts, mid, vol)] sorted. Returns (vol_onset_min, move_onset_min) in minutes relative to tip."""
    rel = [((t - tip) / 60.0, m, v) for t, m, v in rows]
    base = [v for r, m, v in rel if -30 <= r <= -5 and v is not None]
    med = statistics.median(base) if base else 0
    ref = [m for r, m, v in rel if -6 <= r <= -4 and m is not None]
    ref = ref[0] if ref else None
    vo = mo = None
    for i, (r, m, v) in enumerate(rel):
        if r < -4:
            continue
        if vo is None and i + 2 < len(rel) and all((rel[i + k][2] or 0) >= 5 * max(med, 1) for k in range(3)):
            vo = r
        if mo is None and ref is not None and i + 2 < len(rel) and all(
                rel[i + k][1] is not None and abs(rel[i + k][1] - ref) >= 0.02 for k in range(3)):
            mo = r
    return vo, mo


def summarize(name, vals):
    v = [x for x in vals if x is not None]
    print(f"{name}: found {len(v)}/{len(vals)}; offsets (min after inferred tip) sorted: {sorted(round(x) for x in v)}; "
          f"median {statistics.median(v) if v else None}")
    return {"found": len(v), "n": len(vals), "sorted": sorted(v), "median": statistics.median(v) if v else None}


conn = sqlite3.connect(ROOT / "data" / "nba_drift.sqlite")
evs = [r for r in conn.execute("SELECT event_ticker, MIN(occurrence_ts), MIN(ticker) FROM markets GROUP BY event_ticker "
                               "HAVING COUNT(*)=2 ORDER BY event_ticker")]
sample = random.Random(20261020).sample(evs, 20)
cl = KalshiClient()
vo_l, mo_l = [], []
for ev, occ, t in sample:
    tip = occ - 3 * 3600
    rows = []
    for c in cl.candlesticks("KXNBAGAME", t, tip - 1800 - 300, tip + 2400, 1, True):
        b, a = float((c.get("yes_bid") or {}).get("close") or 0), float((c.get("yes_ask") or {}).get("close") or 0)
        rows.append((c["end_period_ts"], (a + b) / 2 if b > 0 and 0 < a < 1 else None, float(c.get("volume") or 0)))
    vo, mo = onsets(rows, tip)
    vo_l.append(vo)
    mo_l.append(mo)
    print(ev, "vol_onset", vo, "move_onset", mo)
res = {"NBA_vol": summarize("NBA vol onset", vo_l), "NBA_move": summarize("NBA move onset", mo_l)}

n = sqlite3.connect(ROOT / "data" / "nfl_spread.sqlite")
games = n.execute("SELECT event_ticker, MIN(mid), MIN(json_extract(raw_json,'$.expected_expiration_time')) FROM sp_markets "
                  "WHERE series='KXNFLGAME' GROUP BY event_ticker").fetchall()
from datetime import datetime  # noqa: E402
vo_l, mo_l = [], []
for ev, mid_, exp in random.Random(20261020).sample(games, 20):
    tip = int(datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp()) - 3 * 3600
    rows = []
    for ts, b, a, v in n.execute("SELECT ts,bid_e4,ask_e4,volume FROM sp_quotes WHERE mid=? AND ts BETWEEN ? AND ? ORDER BY ts",
                                 (mid_, tip - 2100, tip + 2400)):
        rows.append((ts, (a + b) / 2e4 if b and a and a < 10000 else None, v or 0.0))
    vo, mo = onsets(rows, tip)
    vo_l.append(vo)
    mo_l.append(mo)
res["NFL_vol"] = summarize("NFL vol onset", vo_l)
res["NFL_move"] = summarize("NFL move onset", mo_l)
(Path(__file__).parent / "tipcheck_diag.json").write_text(json.dumps(res, indent=1))
