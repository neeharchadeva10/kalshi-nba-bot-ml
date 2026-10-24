"""Step 1 feasibility probe: 3 settled games, in-game 1-min candles tip..tip+5h (public GET, cached)."""
import sys, json
from pathlib import Path; sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nba_injury_speed"))
import common as C
import pandas as pd
from kpm.clients.http import CachedClient
from kpm.core.config import settings
ev = pd.read_csv(C.HERE / "events_list.csv")
cl = CachedClient(settings()["kalshi"]["base_url"], C.RAW_NS_INGAME if hasattr(C, "RAW_NS_INGAME") else "kalshi_1m_totals_ingame", 0.67)
out = []
for e in ["KXNBATOTAL-25OCT22BKNCHA", "KXNBATOTAL-25DEC05LACMEM", "KXNBATOTAL-26JAN04MILSAC"]:
    r = ev[ev.event == e].iloc[0]
    tip = int(r.hoopr_tip)
    for k in (230, 234):
        t = f"{e}-{k}"
        try:
            p = cl.get(f"/historical/markets/{t}/candlesticks", {"start_ts": tip, "end_ts": tip + 5 * 3600, "period_interval": 1})
        except Exception as ex:
            out.append((t, "err", str(ex)[:100])); continue
        cs = p.get("candlesticks", [])
        out.append((t, len(cs), cs[0]["end_period_ts"] - tip if cs else None, cs[-1]["end_period_ts"] - tip if cs else None))
        if cs: print(json.dumps(cs[len(cs)//2])[:400])
print(out)
