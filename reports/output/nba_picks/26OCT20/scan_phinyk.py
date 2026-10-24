"""READ-ONLY scan of every Kalshi market on 26OCT20PHINYK. GET only via kpm KalshiClient. No execution import, no writes to data/."""
import json, math, re, datetime as dt, statistics
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from kpm.clients.kalshi import KalshiClient
from kpm.analysis.ws_sim import valid_mid

OUT = Path(__file__).parent; ROOT = OUT.parents[3]
GAME = "26OCT20PHINYK"
meta = json.load(open(ROOT / "data/ws/nba/26OCT20/markets.json"))["games"][GAME]
cl = KalshiClient()
G = lambda p, **k: cl.http.get(p, k or None, use_cache=False, persist=False)

def fee(p, n):
    return math.ceil(0.07 * n * p * (1 - p) / 0.0001 - 1e-6) * 0.0001
def feeu(p, n=1): return fee(p, n) / n

pulled = dt.datetime.now(dt.timezone.utc)
mk = {}
for s in meta["series"]:
    cur = None
    while True:
        kw = dict(series_ticker=s, limit=1000)
        if cur: kw["cursor"] = cur
        r = G("/markets", **kw)
        for m in r.get("markets", []):
            if GAME in m["event_ticker"]: mk[m["ticker"]] = m
        cur = r.get("cursor")
        if not cur: break
missing = [t for t in meta["tickers"] if t not in mk]
events = sorted({m["event_ticker"] for m in mk.values()})
ev = {}
for e in events:
    try: ev[e] = G(f"/events/{e}")["event"]
    except Exception as ex: ev[e] = {"err": str(ex)[:80]}

def book(t):
    try:
        ob = G(f"/markets/{t}/orderbook")["orderbook_fp"]
    except Exception as ex:
        return dict(err=str(ex)[:80], bid=None, bsz=0, ask=None, asz=0)
    yes = sorted((float(a), float(b)) for a, b in ob.get("yes_dollars") or [])
    no = sorted((float(a), float(b)) for a, b in ob.get("no_dollars") or [])
    bid, bsz = yes[-1] if yes else (None, 0)
    nb, asz = no[-1] if no else (None, 0)
    return dict(bid=bid, bsz=bsz, ask=round(1 - nb, 4) if nb is not None else None, asz=asz)
with ThreadPoolExecutor(4) as ex:
    bks = dict(zip(mk, ex.map(book, list(mk))))
for t, b in bks.items(): b["mid"] = valid_mid(b["bid"], b["ask"], True)
priced = [t for t, b in bks.items() if b["bid"] is not None or b["ask"] is not None]

res = {"pulled_utc": pulled.isoformat(), "n_markets": len(mk), "n_priced": len(priced), "meta_missing": missing,
       "book_errors": [t for t, b in bks.items() if b.get("err")],
       "events": {e: {"mutually_exclusive": ev[e].get("mutually_exclusive"), "title": ev[e].get("title")} for e in events}}
md = [f"# PHINYK full scan\nPulled UTC {pulled:%Y-%m-%d %H:%M:%S}; markets {len(mk)}, with a quote {len(priced)}, in tickers list but not open {len(missing)}\n"]

def strike(m):
    st = m.get("strike_type")
    if st in ("greater", "greater_or_equal"): return "ge", m.get("floor_strike")
    if st in ("less", "less_or_equal"): return "le", m.get("cap_strike")
    return None, None
groups = {}
for t, m in mk.items():
    d, k = strike(m)
    if d is None or k is None: continue
    parts = t.split("-")
    sub = parts[2] if len(parts) >= 4 else re.sub(r"\d+$", "", parts[2])
    groups.setdefault((m["event_ticker"], sub, d), []).append((k, t))
md.append("## Ladder groups\n")
locks = []
for (e, sub, d), L in sorted(groups.items()):
    L.sort()
    md.append(f"- {e} | {sub} | {d} | n={len(L)} strikes {[k for k,_ in L]}")
    for i in range(len(L)):
        for j in range(len(L)):
            if i == j: continue
            k1, t1 = L[i]; k2, t2 = L[j]
            if (d == "ge" and not k2 > k1) or (d == "le" and not k2 < k1): continue
            a, b = bks[t1], bks[t2]
            if a["ask"] is None or b["bid"] is None: continue
            if b["bid"] - a["ask"] > -0.03:
                sz = min(a["asz"], b["bsz"]); n = max(int(sz), 1)
                pr = n * (b["bid"] - a["ask"]) - fee(a["ask"], n) - fee(1 - b["bid"], n)
                locks.append(dict(weak=t1, strong=t2, ask_weak=a["ask"], bid_strong=b["bid"], size=sz, gross=b["bid"] - a["ask"], profit_after_fee=pr,
                                  cost=n * (a["ask"] + 1 - b["bid"]), riskless=b["bid"] > a["ask"]))
res["ladder_groups"] = {f"{e}|{s}|{d}": [k for k, _ in sorted(L)] for (e, s, d), L in groups.items()}
locks.sort(key=lambda x: -x["gross"])
res["ladder_locks"] = [l for l in locks if l["riskless"]]
res["ladder_near"] = [l for l in locks if not l["riskless"]][:15]
md.append("\n## Check 1 riskless ladder inversions (bid_strong > ask_weak)\n")
md += [f"- {json.dumps(l)}" for l in res["ladder_locks"]] or ["- none"]
md.append("\nNearest misses (within 3c)\n")
md += [f"- {l['weak']} ask {l['ask_weak']} vs {l['strong']} bid {l['bid_strong']} gross {l['gross']:.3f}" for l in res["ladder_near"]] or ["- none"]

byev = {}
for t, m in mk.items(): byev.setdefault(m["event_ticker"], []).append(t)
md.append("\n## Check 2 mutually exclusive sets\n")
res["me"] = []
fm = lambda x: "-" if x is None else f"{x:.3f}"
for e, L in sorted(byev.items()):
    me = ev[e].get("mutually_exclusive")
    exh = e.startswith("KXNBAWINMARGIN") or e.startswith("KXNBAGAME")
    if not (me or exh): continue
    bs = [bks[t] for t in L]; n = len(L)
    sb = None if any(b["bid"] is None for b in bs) else sum(b["bid"] for b in bs)
    sa = None if any(b["ask"] is None for b in bs) else sum(b["ask"] for b in bs)
    r = dict(event=e, n=n, me=me, exhaustive_assumed=exh, sum_bid=sb, sum_ask=sa)
    if sb is not None:
        c = max(int(min(b["bsz"] for b in bs)), 1)
        r["no_all_margin"] = sb - 1 - sum(feeu(1 - b["bid"], c) for b in bs); r["no_all_size"] = min(b["bsz"] for b in bs)
    if sa is not None and exh:
        c = max(int(min(b["asz"] for b in bs)), 1)
        r["yes_all_margin"] = 1 - sa - sum(feeu(b["ask"], c) for b in bs); r["yes_all_size"] = min(b["asz"] for b in bs)
    res["me"].append(r)
    md.append(f"- {e} n={n} me={me} exh={exh} sumbid={fm(sb)} sumask={fm(sa)} NOall margin={fm(r.get('no_all_margin'))} YESall margin={fm(r.get('yes_all_margin'))}")

g = GAME; teams = {t.rsplit("-", 1)[1]: v["yes_sub_title"] for t, v in meta["meta"].items() if t.startswith("KXNBAGAME-")}
A, B = list(teams)
gm = {tm: bks[f"KXNBAGAME-{g}-{tm}"]["mid"] for tm in teams}
spread = {tm: {} for tm in teams}
for t, m in mk.items():
    if t.startswith("KXNBASPREAD-"):
        spread[re.match(r"[A-Z]+", t.rsplit("-", 1)[1]).group()][m["floor_strike"]] = bks[t]["mid"]
md.append(f"\n## Check 3 margin buckets (game mids {gm})\n")
res["buckets"] = []
for tm in teams:
    def pover(k, tm=tm):
        if abs(k - 0.5) < 1e-9: return gm[tm]
        return spread[tm].get(k)
    for name, a, bnd, sfx in [("1-5", .5, 5.5, "1TO5"), ("6-10", 5.5, 10.5, "6TO10"), ("11-20", 10.5, 20.5, "11TO20"), ("21+", 20.5, None, "21PLUS")]:
        tk = f"KXNBAWINMARGIN-{g}-{tm}{sfx}"
        if tk not in bks: continue
        b = bks[tk]; p1 = pover(a); p2 = 0.0 if bnd is None else pover(bnd)
        row = dict(ticker=tk, title=mk[tk].get("yes_sub_title"), bid=b["bid"], bsz=b["bsz"], ask=b["ask"], asz=b["asz"])
        if p1 is None or p2 is None: row["skip"] = "invalid input leg"
        else:
            fair = p1 - p2; row["fair"] = fair
            if b["ask"] is not None:
                row["ev_yes_1"] = fair - b["ask"] - feeu(b["ask"], 1)
                row["ev_yes_sz"] = fair - b["ask"] - feeu(b["ask"], max(int(b["asz"]), 1))
            if b["bid"] is not None:
                row["ev_no_1"] = b["bid"] - fair - feeu(1 - b["bid"], 1)
                row["ev_no_sz"] = b["bid"] - fair - feeu(1 - b["bid"], max(int(b["bsz"]), 1))
        res["buckets"].append(row); md.append(f"- {json.dumps(row)}")

def interp(pts, y=.5):
    pts = sorted(pts)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if y0 >= y >= y1 and y0 != y1: return x0 + (y0 - y) / (y0 - y1) * (x1 - x0)
md.append("\n## Check 4 totals\n")
try:
    tot = sorted((m["floor_strike"], bks[t]["mid"]) for t, m in mk.items() if t.startswith("KXNBATOTAL-") and bks[t]["mid"] is not None)
    medT = interp(tot); q25 = interp(tot, .75); q75 = interp(tot, .25)
    sdT = (q75 - q25) / 1.349
    pts = [(.5, gm[A])] if gm[A] is not None else []
    pts += [(k, v) for k, v in spread[A].items() if v is not None] + [(-k, 1 - v) for k, v in spread[B].items() if v is not None]
    medM = interp(pts); m25 = interp(pts, .75); m75 = interp(pts, .25)
    sdM = (m75 - m25) / 1.349
    md.append(f"total ladder mids {tot}\nmedian total {medT}, sd {sdT}; margin({A}) median {medM}, sd {sdM}")
    res["c4"] = dict(total_median=medT, sd_total=sdT, margin_median=medM, sd_margin=sdM, flags=[], teamtotal={})
    nd = statistics.NormalDist()
    for t, m in sorted(mk.items()):
        if t.startswith("KXNBATEAMTOTAL-") and bks[t]["mid"] is not None and m.get("floor_strike") is not None:
            tm = re.match(r"[A-Z]+", t.rsplit("-", 1)[1]).group()
            sgn = 1 if tm == A else -1
            mu = (medT + sgn * medM) / 2; sd = math.sqrt((sdT ** 2 + sdM ** 2) / 4)
            model = 1 - nd.cdf((m["floor_strike"] - mu) / sd)
            diff = bks[t]["mid"] - model
            res["c4"]["teamtotal"][t] = dict(mid=bks[t]["mid"], model=model, diff=diff, bid=bks[t]["bid"], ask=bks[t]["ask"], title=m.get("yes_sub_title"))
            if abs(diff) > .05: res["c4"]["flags"].append(t)
            md.append(f"- {t} {m.get('yes_sub_title')} mid {bks[t]['mid']:.3f} model {model:.3f} diff {diff:+.3f}{' FLAG' if abs(diff)>.05 else ''}")
except Exception as ex:
    md.append(f"check 4 error {ex!r}"); res["c4_error"] = repr(ex)

res["books"] = {t: {**bks[t], "title": mk[t].get("yes_sub_title"), "strike_type": mk[t].get("strike_type"), "floor": mk[t].get("floor_strike"), "cap": mk[t].get("cap_strike")} for t in mk}
json.dump(res, open(OUT / "phinyk.json", "w"), indent=1)
open(OUT / "phinyk.md", "w").write("\n".join(md))
print("\n".join(md))
