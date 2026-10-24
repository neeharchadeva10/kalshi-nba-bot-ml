"""Step A: market DEFINITIONS + settlement results only (no prices) for the TWC study.
Reads data/weather.sqlite read-only (columns: no quotes table touched) and fetches live-tier settled markets via
the repo's cached public GET client. Output: cache/market_defs.json.gz (whitelisted fields only)."""
import gzip, json, sqlite3, sys
from pathlib import Path
sys.path.insert(0, "src")
from kpm.clients.kalshi import KalshiClient
from kpm.collectors.kalshi_weather import parse_source, parse_strikes, parse_event_day, parse_station

OUT = Path("reports/output/weather_twc/cache/market_defs.json.gz")
KEEP = ("ticker", "event_ticker", "series", "strike_type", "floor_strike", "cap_strike", "result",
        "settlement_value", "settled_ts", "rules_primary", "rules_secondary", "close_time", "status", "origin")

def main():
    c = sqlite3.connect("file:data/weather.sqlite?mode=ro", uri=True)
    rows = {}
    q = ("select ticker,event_ticker,series,strike_type,floor_strike,cap_strike,result,settlement_value,settled_ts,"
         "rules_primary,rules_secondary,data_tier from wx_markets")
    for r in c.execute(q):
        d = dict(zip(("ticker", "event_ticker", "series", "strike_type", "floor_strike", "cap_strike", "result",
                      "settlement_value", "settled_ts", "rules_primary", "rules_secondary", "origin"), r))
        d["origin"] = "db_" + d["origin"]
        rows[d["ticker"]] = d
    series = [r[0] for r in c.execute("select ticker from wx_series")]
    kc = KalshiClient()
    n_live = n_new = 0
    for s in series:
        for m in kc.markets(s, historical=False, status="settled"):
            n_live += 1
            st, fl, cp, _ = parse_strikes(m)
            d = {"ticker": m["ticker"], "event_ticker": m["event_ticker"], "series": s, "strike_type": st,
                 "floor_strike": fl, "cap_strike": cp, "result": m.get("result") or None,
                 "settlement_value": m.get("settlement_value_dollars"), "settled_ts": m.get("settlement_ts"),
                 "rules_primary": m.get("rules_primary"), "rules_secondary": m.get("rules_secondary"),
                 "origin": "live_fetch"}
            if m["ticker"] not in rows:
                n_new += 1
                rows[m["ticker"]] = d
    for d in rows.values():
        d["day"] = parse_event_day(d["event_ticker"])
        d["source"] = parse_source(d["rules_primary"], d["rules_secondary"])
        d["station"] = parse_station(d["rules_primary"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        json.dump(list(rows.values()), f)
    print("db+live markets", len(rows), "live fetched", n_live, "new beyond DB", n_new)

if __name__ == "__main__":
    main()
