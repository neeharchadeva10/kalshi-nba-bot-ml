"""J9 helper: pull the raw order-book messages (snapshot / delta / trade) for the ledger's NBA tickers out of the read-only ws recorder
files (data/ws/nba/*/raw, opened read-only) into partB/_cache/ws_nba_msgs.jsonl.  Skips truncated (still-being-written) gz files safely."""
import glob, gzip, json, os, re, sys
from pathlib import Path
import pandas as pd
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
L = pd.read_csv(ROOT / "logs" / "trade_ledger.csv")
L = L[L.ticker.str.startswith("KXNBA") & L.fill_time_utc.between("2026-10-20", "2026-10-23")]
tickers = sorted(set(L.ticker))
print(len(tickers), "tickers", flush=True)
pat = re.compile("|".join(re.escape(t) for t in tickers))
out = open(HERE / "_cache" / "ws_nba_msgs.jsonl", "w")
n = 0
for f in sorted(glob.glob(str(ROOT / "data" / "ws" / "nba" / "26OCT2*" / "raw" / "*.jsonl.gz"))):
    try:
        for l in gzip.open(f, "rt"):
            if pat.search(l):
                r = json.loads(l)
                m = json.loads(r["raw"])
                if m.get("type") in ("orderbook_snapshot", "orderbook_delta", "trade"):
                    out.write(json.dumps({"recv_ns": r["recv_ns"], "f": os.path.basename(f), "m": m}) + "\n")
                    n += 1
    except EOFError:
        print("truncated", f, flush=True)
    print(os.path.basename(f), n, flush=True)
out.close()
print("done", n)
