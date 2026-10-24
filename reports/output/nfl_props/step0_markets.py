"""Step 0a: enumerate settled NFL prop markets (historical + live tiers), cached; write compact index."""
import sys, json, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import lib
OUT = Path(__file__).parent / "markets_index.jsonl"
SERIES = ['KXNFLRSHYDS', 'KXNFLRECYDS', 'KXNFLREC', 'KXNFLPASSYDS']
c = lib.KClient()
keep = ['ticker', 'event_ticker', 'status', 'result', 'open_time', 'close_time', 'expiration_time',
        'occurrence_datetime', 'expected_expiration_time', 'floor_strike', 'strike_type', 'title', 'yes_sub_title',
        'settlement_ts', 'settlement_value', 'settlement_value_dollars', 'volume', 'volume_fp', 'custom_strike',
        'last_price_dollars', 'yes_bid_dollars', 'yes_ask_dollars', 'previous_price_dollars']
n = 0
with open(OUT, "w") as fh:
    for s in SERIES:
        for tier, path, extra in [('hist', '/historical/markets', {}), ('live', '/markets', {})]:
            cnt = 0
            for m in c.paginate(path, 'markets', {'series_ticker': s, 'limit': 1000, **extra}):
                d = {k: m.get(k) for k in keep if k in m}
                d['series'] = s; d['tier'] = tier
                fh.write(json.dumps(d) + "\n"); cnt += 1
            print(s, tier, cnt, flush=True)
print(c.stats, c.pacer.rate)
