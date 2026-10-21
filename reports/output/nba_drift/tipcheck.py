"""Tip-off rule check for research log #24 (PREREGISTRATION.md, Data section). Data validation only, no strategy.

20 random NBA games (seed 20261020). For each: 1-minute candles over [tip-30m, tip+30m] (public GET, via KalshiClient),
mid series of one team market, 5-minute absolute change |mid_t - mid_{t-5}|. Operationalisation (the pre-registration does
not define 'jump'): the minute at which that 5-minute change is largest over [tip-30m, tip+30m] must lie within +-15 min of
the inferred tip (tip = occurrence_datetime - 3h). Pass if >= 18 of 20.
Secondary descriptive: mean |5-min change| in [tip, tip+15m] vs [tip-30m, tip-15m].
"""
import json
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from kpm.clients.kalshi import KalshiClient  # noqa: E402

conn = sqlite3.connect(ROOT / "data" / "nba_drift.sqlite")
evs = [r for r in conn.execute("SELECT event_ticker, MIN(occurrence_ts), MIN(ticker) FROM markets GROUP BY event_ticker "
                               "HAVING COUNT(*)=2 ORDER BY event_ticker")]
rnd = random.Random(20261020)
sample = rnd.sample(evs, 20)
cl = KalshiClient()
out = []
for ev, occ, ticker in sample:
    tip = occ - 3 * 3600
    cs = cl.candlesticks("KXNBAGAME", ticker, tip - 1800 - 300, tip + 1800, 1, True)
    mids = {}
    for c in cs:
        b, a = float((c.get("yes_bid") or {}).get("close") or 0), float((c.get("yes_ask") or {}).get("close") or 0)
        if b > 0 and 0 < a < 1:
            mids[c["end_period_ts"]] = (a + b) / 2
    ch = {}
    for t, m in mids.items():
        if tip - 1800 <= t <= tip + 1800 and (t - 300) in mids:
            ch[t] = abs(m - mids[t - 300])
    if not ch:
        out.append({"event": ev, "ticker": ticker, "ok": False, "reason": "no usable 5-min changes", "n_candles": len(cs)})
        continue
    mx = max(ch.values())
    argmax = [t for t, v in ch.items() if v == mx]
    best = min(argmax, key=lambda t: abs(t - tip))
    off = (best - tip) / 60.0
    before = [v for t, v in ch.items() if t < tip - 900]
    after = [v for t, v in ch.items() if tip <= t <= tip + 900]
    out.append({"event": ev, "ticker": ticker, "ok": mx > 0 and abs(off) <= 15 and len(argmax) < len(ch),
                "peak_offset_min": off, "peak_change": mx, "n_changes": len(ch), "n_ties_at_peak": len(argmax),
                "mean_chg_tip-30..-15": sum(before) / len(before) if before else None,
                "mean_chg_tip..+15": sum(after) / len(after) if after else None})
n_ok = sum(1 for o in out if o["ok"])
cl.http.flush_index  # noqa: B018  (no index connection here; responses are cached on disk)
for o in out:
    print(json.dumps(o))
print(f"TIP RULE CHECK: {n_ok}/20 pass (need >= 18) -> {'PASS' if n_ok >= 18 else 'FAIL'}")
(Path(__file__).parent / "tipcheck.json").write_text(json.dumps({"pass_count": n_ok, "games": out}, indent=1))
