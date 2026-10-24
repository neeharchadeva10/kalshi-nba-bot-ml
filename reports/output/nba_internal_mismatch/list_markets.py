"""#49 step 1: list KXNBATEAMTOTAL + KXNBA1HTOTAL historical-tier markets (public GET, cached) -> markets.sqlite."""
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pacing import PacedClient, ROOT  # noqa: E402
from kpm.core.timeutil import parse_iso  # noqa: E402

BASE = "https://api.elections.kalshi.com/trade-api/v2"
NS = ROOT / "data" / "raw" / "kalshi_teamtotal_1h_hourly"


def main():
    cl = PacedClient(BASE, NS, log=lambda m: print(m, flush=True))
    cut = cl.get("/historical/cutoff") if False else None   # cutoff must not be cached; query directly below
    import requests
    cut = requests.get(BASE + "/historical/cutoff", headers={"User-Agent": "kpm-research/0.1 (read-only)"}).json()
    print("cutoff", cut, flush=True)
    db = sqlite3.connect(HERE / "markets.sqlite")
    db.execute("""CREATE TABLE IF NOT EXISTS markets (ticker TEXT PRIMARY KEY, series TEXT, event_ticker TEXT, team TEXT,
        strike REAL, strike_type TEXT, result TEXT, expiration_value TEXT, open_ts INTEGER, close_ts INTEGER,
        occ_ts INTEGER, status TEXT, volume REAL)""")
    for series in ("KXNBATEAMTOTAL", "KXNBA1HTOTAL"):
        cursor, n = None, 0
        while True:
            p = cl.get("/historical/markets", {"series_ticker": series, "limit": 1000, "cursor": cursor})
            for m in p.get("markets", []):
                tk = m["ticker"]
                team = None
                if series == "KXNBATEAMTOTAL":
                    suf = tk.rsplit("-", 1)[1]
                    team = suf.rstrip("0123456789")
                v = m.get("volume_fp")
                db.execute("INSERT OR REPLACE INTO markets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    tk, series, m["event_ticker"], team, m.get("floor_strike"), m.get("strike_type"),
                    m.get("result") or None, m.get("expiration_value"), parse_iso(m.get("open_time")),
                    parse_iso(m.get("close_time")), parse_iso(m.get("occurrence_datetime")), m.get("status"),
                    float(v) if v not in (None, "") else None))
                n += 1
            cursor = p.get("cursor")
            print(series, n, flush=True)
            if not cursor:
                break
        db.commit()
    print(cl.stats)


if __name__ == "__main__":
    main()
