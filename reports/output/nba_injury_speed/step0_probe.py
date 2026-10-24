"""Step 0 feasibility probe: 1-minute candles for settled 2025-26 KXNBATOTAL markets (GET only, cached)."""
import json, sqlite3, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from kpm.clients.http import CachedClient
from kpm.core.config import settings
HERE = Path(__file__).resolve().parent
cfg = settings()["kalshi"]
cl = CachedClient(cfg["base_url"], "kalshi_1m_totals", 0.2)
db = sqlite3.connect(f"file:{(ROOT/'reports/output/nba_totals_pace/totals.sqlite').as_posix()}?mode=ro", uri=True)
rows = db.execute("select ticker,event_ticker,tier,own_occ_ts,open_ts from markets where tier='historical' "
                  "and own_occ_ts<1781000000 and own_occ_ts>1761000000 order by own_occ_ts").fetchall()
picks = [rows[i] for i in (50, len(rows)//4, len(rows)//2, 3*len(rows)//4, len(rows)-20)]
res = []
for t, et, tier, occ, op in picks:
    T = occ - 3*3600
    r = {"ticker": t, "event": et, "T_base": T}
    for name, path in (("hist_market", f"/historical/markets/{t}/candlesticks"),
                       ("series_market", f"/series/KXNBATOTAL/markets/{t}/candlesticks"),
                       ("event_level", f"/series/KXNBATOTAL/events/{et}/candlesticks")):
        try:
            p = cl.get(path, {"start_ts": T-6*3600, "end_ts": T, "period_interval": 1})
            cs = p.get("candlesticks") or p.get("market_candlesticks") or []
            r[name] = {"keys": list(p.keys()), "n": len(cs), "first_item_type": type(cs[0]).__name__ if cs else None}
            if cs and name != "event_level":
                r[name]["sample"] = cs[len(cs)//2]
            if name == "event_level":
                r[name]["raw_head"] = json.dumps(p)[:600]
        except Exception as e:
            r[name] = {"error": str(e)[:200]}
    res.append(r)
(HERE / "step0_probe_raw.json").write_text(json.dumps(res, indent=1))
for r in res:
    print(r["ticker"], {k: (v.get("n"), v.get("error")) for k, v in r.items() if isinstance(v, dict)})
