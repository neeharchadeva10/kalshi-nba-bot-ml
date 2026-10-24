import csv, sys, collections, math, statistics as st, random
rows = [r for r in csv.DictReader(open(sys.argv[1], encoding='utf-8')) if r['away_score']!='' and r['total_line']!='' and r['referee']!='']
for r in rows:
    r['res'] = int(r['total'])-float(r['total_line']); r['s']=int(r['season'])
E=[r for r in rows if 2015<=r['s']<=2025]
by=collections.defaultdict(list)
for r in E: by[r['referee']].append(r)
refs=[k for k,v in by.items() if len(v)>=80]
print("refs with >=80 games 2015-2025:", len(refs))
# chi-square heterogeneity of over rate
tot_o=sum(1 for r in E if r['res']>0); tot_u=sum(1 for r in E if r['res']<0); p=tot_o/(tot_o+tot_u)
chi=0; df=0
lst=[]
for k in refs:
    o=sum(1 for r in by[k] if r['res']>0); u=sum(1 for r in by[k] if r['res']<0); n=o+u
    chi += (o-n*p)**2/(n*p*(1-p)); df+=1
    lst.append((o/n, n, k, st.mean(r['res'] for r in by[k])))
print(f"chi2={chi:.1f} df={df-1}  (expected ~df if no ref effect)")
lst.sort()
for x in lst[:5]+lst[-5:]: print(f"  {x[2]:22s} n={x[1]} over%={x[0]*100:.1f} mean_res={x[3]:+.2f}")
# persistence: ref over% in season-pairs: odd seasons vs even seasons
def rate(g):
    o=sum(1 for r in g if r['res']>0); u=sum(1 for r in g if r['res']<0); return (o/(o+u) if o+u else None), o+u
def mres(g): return st.mean(r['res'] for r in g) if g else None
xs=[];ys=[];xm=[];ym=[]
for k in refs:
    a=[r for r in by[k] if r['s']%2==1]; b=[r for r in by[k] if r['s']%2==0]
    if len(a)>=30 and len(b)>=30:
        xs.append(rate(a)[0]); ys.append(rate(b)[0]); xm.append(mres(a)); ym.append(mres(b))
def corr(x,y):
    mx,my=st.mean(x),st.mean(y); return sum((a-mx)*(b-my) for a,b in zip(x,y))/math.sqrt(sum((a-mx)**2 for a in x)*sum((b-my)**2 for b in y))
print(f"odd vs even seasons: n_refs={len(xs)} corr(over%)={corr(xs,ys):+.3f} corr(mean resid)={corr(xm,ym):+.3f}")
# prior-season-rate predicts next season? for each ref-season with >=10 games previous season
pairs=[]
for k,g in by.items():
    ss=collections.defaultdict(list)
    for r in g: ss[r['s']].append(r)
    for s in ss:
        if s-1 in ss and len(ss[s-1])>=12 and len(ss[s])>=12:
            pairs.append((mres(ss[s-1]), mres(ss[s]), rate(ss[s-1])[0], rate(ss[s])[0]))
print(f"season-to-season ref pairs n={len(pairs)} corr(mean resid)={corr([a[0] for a in pairs],[a[1] for a in pairs]):+.3f} corr(over%)={corr([a[2] for a in pairs],[a[3] for a in pairs]):+.3f}")
# simple rule: bet over if ref's prior-season over% >=60, under if <=40
w=l=0
for k,g in by.items():
    ss=collections.defaultdict(list)
    for r in g: ss[r['s']].append(r)
    for s in ss:
        if s-1 in ss and len(ss[s-1])>=12:
            pr=rate(ss[s-1])[0]
            if pr is None: continue
            side = 1 if pr>=0.6 else (-1 if pr<=0.4 else 0)
            if side==0: continue
            for r in ss[s]:
                if r['res']==0: continue
                if (r['res']>0)==(side==1): w+=1
                else: l+=1
print(f"rule prior-season ref over%>=60 -> over, <=40 -> under: {w}-{l} = {w/(w+l)*100:.1f}%")
