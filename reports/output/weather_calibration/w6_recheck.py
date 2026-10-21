"""W6 (weather calibration pre-registration): re-check the 5 best cells against fresh API data.

No cell passed, so the 5 best cells are taken by discovery mean net among cells with n >= 200.
For a seeded sample of entries per cell (both periods), re-fetch from the public API with the cache bypassed:
  - the market (settlement result), and
  - 1-minute candles from market open to the snapshot, re-deriving the carried quote at the snapshot.
Then compare against data/weather.sqlite and recompute net. GET only. Run from the repo root with the venv python.
"""
import json
import random
import sys
from pathlib import Path

from kpm.analysis import weather_calib as wc
from kpm.clients.kalshi import KalshiClient
from kpm.collectors.kalshi_weather import quote_row

PER_CELL = 8
OUT = Path("reports/output/weather_calibration/w6_recheck.json")

cells_df = wc.pd.read_csv(wc.OUT_DIR / "cells.csv")
disc = cells_df[(cells_df.period == "discovery") & (cells_df.n >= 200)].sort_values("mean_net", ascending=False)
best = [tuple(r) for r in disc[["kind", "snap", "side", "bucket"]].head(5).itertuples(index=False)]
print("best cells:", best)

conn = wc.open_ro()
markets, _ = wc.load_markets(conn)
valid, _ = wc.w1_filter(markets)
by_ticker = {m["ticker"]: m for ms in valid.values() for m in ms}
tier = dict(tuple(r) for r in conn.execute("SELECT ticker, data_tier FROM wx_markets").fetchall())

pool: dict[tuple, list[dict]] = {c: [] for c in best}
for e in wc._entries_for_cells(conn, valid, set(best), "0000-00-00"):
    pool[e["cell"]].append(e)
rng = random.Random(wc.SEED)
sample = [e for c in best for e in rng.sample(pool[c], min(PER_CELL, len(pool[c])))]
print({str(c): len(v) for c, v in pool.items()}, "sampled", len(sample))

client = KalshiClient(None)
client.http.min_interval_s = max(client.http.min_interval_s, 0.5)      # gentle: the LIP recorder shares the IP
rows, mism = [], 0
for e in sample:
    m = by_ticker[e["ticker"]]
    hist = tier.get(e["ticker"]) == "historical"
    mpath = f"/historical/markets/{e['ticker']}" if hist else f"/markets/{e['ticker']}"
    fresh_m = client.http.get(mpath, use_cache=False)["market"]
    cpath = (f"/historical/markets/{e['ticker']}/candlesticks" if hist
             else f"/series/{m['series']}/markets/{e['ticker']}/candlesticks")
    page = client.http.get(cpath, {"start_ts": int(m["open_ts"]), "end_ts": int(e["t"]) + 60, "period_interval": 1},
                           use_cache=False)
    qs = [quote_row(0, c) for c in page.get("candlesticks", []) if int(c["end_period_ts"]) <= e["t"]]
    fresh_q = (qs[-1][2], qs[-1][3]) if qs else None
    stored_q = wc.carried_quote(conn, m["mid"], e["t"])
    fresh_price = None
    if fresh_q:
        fresh_price = fresh_q[1] if e["side"] == "yes" else (None if fresh_q[0] is None else 10_000 - fresh_q[0])
    fresh_net = None if fresh_price is None else wc.net10_e4(e["side"], fresh_price, fresh_m.get("result"))
    ok = (fresh_m.get("result") == m["result"] and fresh_q == (stored_q[0], stored_q[1]) and fresh_net == e["net"])
    mism += not ok
    rows.append(dict(cell=list(e["cell"]), ticker=e["ticker"], snapshot_ts=e["t"], day=e["day"], tier=tier.get(e["ticker"]),
                     stored_result=m["result"], fresh_result=fresh_m.get("result"),
                     stored_quote=list(stored_q), fresh_quote=list(fresh_q) if fresh_q else None,
                     stored_net10_usd=e["net"] / 1e4, fresh_net10_usd=None if fresh_net is None else fresh_net / 1e4,
                     match=ok))
    print(("OK  " if ok else "DIFF"), e["cell"], e["ticker"], stored_q, fresh_q, m["result"], fresh_m.get("result"))

res = dict(rule="5 best cells by discovery mean net with n>=200 (no cell passed); %d seeded entries per cell" % PER_CELL,
           cells=[list(c) for c in best], entries_in_cells={"|".join(c): len(v) for c, v in pool.items()},
           checked=len(rows), mismatches=mism, rows=rows)
OUT.write_text(json.dumps(res, indent=1))
print(f"checked {len(rows)}, mismatches {mism} -> {OUT}")
sys.exit(0)
