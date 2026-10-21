"""READ-ONLY scan of NBA 26OCT20 on Kalshi. GET only via kpm CachedClient (persist=False). No execution import."""
import json, math, re, datetime as dt
from pathlib import Path
from kpm.clients.kalshi import KalshiClient
from kpm.analysis.ws_sim import valid_mid

OUT = Path(__file__).parent
ROOT = OUT.parents[3]
meta = json.load(open(ROOT / "data/ws/nba/26OCT20/markets.json"))
cl = KalshiClient()
G = lambda p, **k: cl.http.get(p, k or None, use_cache=False, persist=False)

def fee(p, n=10):
    return math.ceil(0.07 * n * p * (1 - p) / 0.0001 - 1e-6) * 0.0001 / n

def book(t):
    ob = G(f"/markets/{t}/orderbook")["orderbook_fp"]
    yes = [(float(a), float(b)) for a, b in ob.get("yes_dollars") or []]
    no = [(float(a), float(b)) for a, b in ob.get("no_dollars") or []]
    yes.sort(); no.sort()
    bid, bsz = (yes[-1] if yes else (None, 0))
    nb, asz = (no[-1] if no else (None, 0))
    ask = round(1 - nb, 4) if nb is not None else None
    return dict(bid=bid, bsz=bsz, ask=ask, asz=asz)

def mid_of(b, longshot=True):
    return valid_mid(b["bid"], b["ask"], longshot)

BUCKETS = [("1-5", 0.5, 5.5), ("6-10", 5.5, 10.5), ("11-20", 10.5, 20.5), ("21+", 20.5, None)]
pulled = dt.datetime.now(dt.timezone.utc)
res = {"pulled_utc": pulled.isoformat(), "games": {}}
md = [f"# NBA 26OCT20 read-only scan\nPulled UTC: {pulled:%Y-%m-%d %H:%M:%S}\n"]

for g, gd in meta["games"].items():
    m = gd["meta"]
    abbr = g[len("26OCT20"):]
    teams = {}
    for t, v in m.items():
        if t.startswith("KXNBAGAME-"):
            teams[t.rsplit("-", 1)[1]] = v["yes_sub_title"]
    A, B = list(teams)  # first-listed order
    GR = {"teams": teams, "times": gd["kickoff_raw"], "game": {}, "spread": {}, "buckets": [], "problems": []}
    for tm in teams:
        GR["game"][tm] = book(f"KXNBAGAME-{g}-{tm}")
        GR["game"][tm]["mid"] = mid_of(GR["game"][tm])
    spread = {tm: {} for tm in teams}
    for t, v in m.items():
        if t.startswith("KXNBASPREAD-"):
            tm = re.match(r"[A-Z]+", t.rsplit("-", 1)[1]).group()
            k = v["floor_strike"]
            b = book(t); b["mid"] = mid_of(b)
            spread[tm][k] = b
    GR["spread"] = {tm: {str(k): spread[tm][k] for k in sorted(spread[tm])} for tm in teams}
    # implied median margin for team A: P(A margin > x)
    pts = []
    gA, gB = GR["game"][A]["mid"], GR["game"][B]["mid"]
    if gA is not None: pts.append((0.5, gA))
    if gB is not None: pts.append((-0.5, 1 - gB))
    for k, b in spread[A].items():
        if b["mid"] is not None: pts.append((k, b["mid"]))
    for k, b in spread[B].items():
        if b["mid"] is not None: pts.append((-k, 1 - b["mid"]))
    pts.sort()
    med = None
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if y0 >= 0.5 >= y1 and y0 != y1:
            med = x0 + (y0 - 0.5) / (y0 - y1) * (x1 - x0); break
    GR["median_margin_A"] = med
    GR["median_note"] = f"positive = {teams[A]} wins by that many (interpolated across valid spread mids)"
    # buckets
    for tm in teams:
        gmid = GR["game"][tm]["mid"]
        def pover(k):
            if abs(k - 0.5) < 1e-9: return gmid
            b = spread[tm].get(k)
            return b["mid"] if b else None
        def pinterp(k):
            pp = sorted([(0.5, gmid)] + [(kk, b["mid"]) for kk, b in spread[tm].items() if b["mid"] is not None])
            pp = [(x, y) for x, y in pp if y is not None]
            for (x0, y0), (x1, y1) in zip(pp, pp[1:]):
                if x0 <= k <= x1: return y0 + (y1 - y0) * (k - x0) / (x1 - x0)
            return None
        for name, a, bnd in BUCKETS:
            tk = f"KXNBAWINMARGIN-{g}-{tm}" + {"1-5": "1TO5", "6-10": "6TO10", "11-20": "11TO20", "21+": "21PLUS"}[name]
            row = {"team": tm, "bucket": name, "ticker": tk}
            if tk not in m: row["skip"] = "ticker not in markets.json"; GR["buckets"].append(row); continue
            b = book(tk); row.update(b)
            hi = 1.0 if False else None
            p1 = pover(a); p2 = 0.0 if bnd is None else pover(bnd)
            if p1 is None or p2 is None:
                row["skip"] = f"invalid/missing input leg (P(by>{a if p1 is None else bnd}))"
            else:
                fair = p1 - p2; row["fair"] = fair
                if b["ask"] is not None:
                    row["ev_yes"] = fair - b["ask"] - fee(b["ask"]); row["size_yes"] = b["asz"]
                if b["bid"] is not None:
                    row["ev_no"] = b["bid"] - fair - fee(1 - b["bid"]); row["size_no"] = b["bsz"]
                row["flag"] = [s for s, e, z in (("BUY YES", row.get("ev_yes"), row.get("size_yes", 0)),
                               ("BUY NO", row.get("ev_no"), row.get("size_no", 0))) if e is not None and e >= 0.02 and z >= 1]
            q1 = pinterp(a); q2 = 0.0 if bnd is None else pinterp(bnd)
            if q1 is not None and q2 is not None:
                row["fair_interp"] = q1 - q2
                if "fair" not in row and row.get("ask") is not None:
                    row["ev_yes_interp"] = row["fair_interp"] - row["ask"] - fee(row["ask"])
                if "fair" not in row and row.get("bid") is not None:
                    row["ev_no_interp"] = row["bid"] - row["fair_interp"] - fee(1 - row["bid"])
            GR["buckets"].append(row)
    fi = [r["fair"] if "fair" in r else r.get("fair_interp") for r in GR["buckets"]]
    GR["fair_sum_with_interp"] = sum(fi) if all(x is not None for x in fi) else None
    fs = [r["fair"] for r in GR["buckets"] if "fair" in r]
    GR["fair_sum"] = sum(fs) if len(fs) == 8 else None
    GR["fair_n"] = len(fs)
    # other series
    other = {}
    for s in gd["series"]:
        try:
            ev = f"{s}-{g}"
            n = 0; cur = None
            while True:
                r = G("/markets", event_ticker=ev, limit=1000, cursor=cur)
                n += len(r.get("markets", [])); cur = r.get("cursor")
                if not cur: break
            other[s] = n
        except Exception as e:
            other[s] = f"err {e}"[:60]
    GR["series_market_counts"] = other
    res["games"][g] = GR
    # md
    md.append(f"\n## {g}: {teams[A]} vs {teams[B]}\nevent times: {GR['times']}\n")
    md.append("Win: " + "; ".join(f"{teams[t]} bid {GR['game'][t]['bid']} ({GR['game'][t]['bsz']:.0f}) ask {GR['game'][t]['ask']} ({GR['game'][t]['asz']:.0f}) mid {GR['game'][t]['mid']}" for t in teams))
    md.append(f"\nSpread-implied median margin: {med if med is None else round(med,2)} ({GR['median_note']})\n")
    md.append("\nSpread ladder (strike: bid/ask/mid)\n")
    for tm in teams:
        md.append(f"- {tm}: " + ", ".join(f"{k}:{b['bid']}/{b['ask']}/{None if b['mid'] is None else round(b['mid'],3)}" for k, b in sorted(spread[tm].items())))
    md.append("\n| team | bucket | bid (sz) | ask (sz) | fair | EV yes | EV no | flag |\n|---|---|---|---|---|---|---|---|")
    f = lambda x: "-" if x is None else f"{x:.3f}"
    for r in GR["buckets"]:
        md.append(f"| {r['team']} | {r['bucket']} | {r.get('bid')} ({r.get('bsz',0):.0f}) | {r.get('ask')} ({r.get('asz',0):.0f}) | {f(r.get('fair'))} | {f(r.get('ev_yes'))} | {f(r.get('ev_no'))} | interp fair {f(r.get('fair_interp'))} (EVy {f(r.get('ev_yes_interp'))}, EVn {f(r.get('ev_no_interp'))}) | {r.get('skip') or ','.join(r.get('flag', []))} |")
    md.append(f"\nFair sum ({GR['fair_n']}/8 strict buckets): {GR['fair_sum']}; with interpolated fill-in: {GR['fair_sum_with_interp']}\n\nOther series (market counts): {other}\n")

json.dump(res, open(OUT / "scan.json", "w"), indent=1)
open(OUT / "scan.md", "w").write("\n".join(md))
print("\n".join(md))
