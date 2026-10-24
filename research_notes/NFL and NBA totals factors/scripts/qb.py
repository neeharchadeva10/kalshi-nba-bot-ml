import csv, sys, collections, math, statistics as st
rows = [r for r in csv.DictReader(open(sys.argv[1], encoding='utf-8')) if r['away_score']!='' and r['total_line']!='']
for r in rows: r['res']=int(r['total'])-float(r['total_line']); r['s']=int(r['season'])
# primary QB per team-season (most starts) -- NOTE uses full-season info (hindsight) only for labeling
cnt=collections.defaultdict(collections.Counter)
for r in rows:
    if r['game_type']!='REG': continue
    cnt[(r['s'],r['home_team'])][r['home_qb_id']]+=1
    cnt[(r['s'],r['away_team'])][r['away_qb_id']]+=1
prim={k:v.most_common(1)[0][0] for k,v in cnt.items()}
# also previous-start-based label (no hindsight): QB differs from team's QB in its previous game this season
last={}
def summ(name,g):
    o=sum(1 for r in g if r['res']>0); u=sum(1 for r in g if r['res']<0)
    p=o/(o+u); print(f"{name:55s} n={len(g):4d} over%={p*100:5.1f} z={(p-.5)/math.sqrt(.25/(o+u)):+.2f} mean(tot-line)={st.mean(r['res'] for r in g):+.2f}")
for lo,hi in [(2015,2025),(2020,2025)]:
    g=[r for r in rows if lo<=r['s']<=hi and r['game_type']=='REG']
    g.sort(key=lambda r:(r['gameday'],r['gametime']))
    last={}; change=[]
    nb=collections.defaultdict(list)
    for r in g:
        b=0; ch=0
        for side in ('home','away'):
            t=r[side+'_team']; q=r[side+'_qb_id']
            if q!=prim[(r['s'],t)]: b+=1
            if (r['s'],t) in last and last[(r['s'],t)]!=q: ch+=1
            last[(r['s'],t)]=q
        nb[b].append(r)
        if ch: change.append(r)
    print(f"#### {lo}-{hi}")
    for b in (0,1,2): summ(f'non-primary starters in game = {b} (hindsight label)', nb[b])
    summ('a team starts a different QB than its previous game', change)
