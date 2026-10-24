import csv, sys, collections, math, statistics as st
rows = [r for r in csv.DictReader(open(sys.argv[1], encoding='utf-8')) if r['away_score']!='' and r['total_line']!='']
for r in rows: r['res']=int(r['total'])-float(r['total_line']); r['s']=int(r['season'])
rows.sort(key=lambda r:(r['gameday'],r['gametime']))
def summ(name,g):
    o=sum(1 for r in g if r['res']>0); u=sum(1 for r in g if r['res']<0)
    if o+u==0: print(name,'none'); return
    p=o/(o+u); print(f"{name:60s} n={len(g):4d} over%={p*100:5.1f} z={(p-.5)/math.sqrt(.25/(o+u)):+.2f} mean(tot-line)={st.mean(r['res'] for r in g):+.2f}")
first={}  # (season,team)->first coach
prevseason=collections.defaultdict(set)
for r in rows:
    for side in ('home','away'):
        k=(r['s'],r[side+'_team'])
        first.setdefault(k, r[side+'_coach'])
        prevseason[k].add(r[side+'_coach'])
interim=[]; interim_first=[]; newhc_wk1_4=[]; newhc_all=[]
seen_interim=set()
for r in rows:
    if not (2002<=r['s']<=2025) or r['game_type']!='REG': continue
    flag=False; ff=False; nh=False
    for side in ('home','away'):
        t=r[side+'_team']; c=r[side+'_coach']; k=(r['s'],t)
        if c!=first[k]:
            flag=True
            if (k,c) not in seen_interim: ff=True; seen_interim.add((k,c))
        if c not in prevseason.get((r['s']-1,t),set()) and c==first[k]: nh=True
    if flag: interim.append(r)
    if ff: interim_first.append(r)
    if nh:
        newhc_all.append(r)
        if int(r['week'])<=4: newhc_wk1_4.append(r)
for lo,hi in [(2002,2025),(2015,2025)]:
    f=lambda g:[r for r in g if lo<=r['s']<=hi]
    print(f'### {lo}-{hi}')
    summ('game involves a mid-season replacement coach', f(interim))
    summ('first game under the mid-season replacement', f(interim_first))
    summ('game involves first-year HC of that team (all weeks)', f(newhc_all))
    summ('game involves first-year HC of that team (weeks 1-4)', f(newhc_wk1_4))
