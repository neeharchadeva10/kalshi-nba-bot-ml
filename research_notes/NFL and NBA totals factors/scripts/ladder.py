import csv, sys, math, json, urllib.request, statistics as st
rows=[r for r in csv.DictReader(open(sys.argv[1],encoding='utf-8')) if r['away_score']!='' and r['total_line']!='' and 2015<=int(r['season'])<=2025]
res=[int(r['total'])-float(r['total_line']) for r in rows]
sd=st.pstdev(res)
def Phi(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def emp(d):  # P(resid > d) with linear interpolation on half-points
    return sum(1 for x in res if x>d)/len(res)
url="https://api.elections.kalshi.com/trade-api/v2/events?series_ticker=KXNFLTOTAL&limit=12&with_nested_markets=true&status=open"
d=json.load(urllib.request.urlopen(url))
for e in d['events']:
    ms=[]
    for m in e['markets']:
        try:
            b=float(m['yes_bid_dollars']); a=float(m['yes_ask_dollars'])
        except Exception: continue
        if a-b>0.03 or a<=0 : continue
        k=float(m['floor_strike']) if m.get('floor_strike') is not None else None
        if k is None: continue
        ms.append((k,(a+b)/2,a-b))
    if len(ms)<8: continue
    ms.sort()
    # market median: interpolate where mid crosses 0.5
    L=None
    for (k1,p1,_),(k2,p2,_) in zip(ms,ms[1:]):
        if p1>=0.5>=p2: L=k1+(p1-0.5)/(p1-p2)*(k2-k1); break
    if L is None: continue
    # our 'line' estimate: empirical median of resid is -0.5, so line ~= market median + 0.5
    line=L+0.5
    print(f"{e['event_ticker']}  market median {L:.1f} -> assumed closing-type line {line:.1f}")
    print("   strike  kalshi_mid  empirical  normal   spread")
    for k,p,sp in ms:
        print(f"   {k:5.1f}   {p:.3f}     {emp(k-line):.3f}    {1-Phi((k-line)/sd):.3f}   {sp:.2f}")
