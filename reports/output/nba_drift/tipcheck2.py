"""Amended validation (PREREGISTRATION 2026-10-20 ~19:40Z): on the same 20 games (seed 20261020), no sustained price move
before T-5 min in >= 18/20. Sustained = 5-minute absolute mid change > 0.03 at two consecutive minutes, over [T-30m, T-5m]
(the candles fetched span T-35m..T+30m; cached)."""
import json, random, sqlite3, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from kpm.clients.kalshi import KalshiClient
conn = sqlite3.connect(ROOT / "data" / "nba_drift.sqlite")
evs = [r for r in conn.execute("SELECT event_ticker, MIN(occurrence_ts), MIN(ticker) FROM markets GROUP BY event_ticker HAVING COUNT(*)=2 ORDER BY event_ticker")]
sample = random.Random(20261020).sample(evs, 20)
cl = KalshiClient()
out = []
for ev, occ, t in sample:
    tip = occ - 3 * 3600
    mids = {}
    for c in cl.candlesticks("KXNBAGAME", t, tip - 1800 - 300, tip + 1800, 1, True):
        b, a = float((c.get("yes_bid") or {}).get("close") or 0), float((c.get("yes_ask") or {}).get("close") or 0)
        if b > 0 and 0 < a < 1:
            mids[c["end_period_ts"]] = (a + b) / 2
    big = sorted(ts for ts in mids if tip - 1800 <= ts <= tip - 300 and ts - 300 in mids and abs(mids[ts] - mids[ts - 300]) > 0.03)
    sustained = any(b - a == 60 for a, b in zip(big, big[1:]))
    out.append({"event": ev, "n_big_minutes": len(big), "sustained_move_before_T-5m": sustained})
    print(out[-1])
ok = sum(1 for o in out if not o["sustained_move_before_T-5m"])
print(f"VALIDATION: {ok}/20 games with no sustained pre-game move (need >= 18) -> {'PASS' if ok >= 18 else 'FAIL'}")
Path(__file__).with_name("tipcheck2.json").write_text(json.dumps({"no_sustained_move": ok, "games": out}, indent=1))
