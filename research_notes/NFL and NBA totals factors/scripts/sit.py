import csv, sys, collections, math, statistics as st
rows = [r for r in csv.DictReader(open(sys.argv[1], encoding='utf-8')) if r['away_score']!='' and r['total_line']!='']
for r in rows:
    r['res'] = int(r['total'])-float(r['total_line']); r['s']=int(r['season'])
def summ(name, g):
    o=sum(1 for r in g if r['res']>0); u=sum(1 for r in g if r['res']<0)
    if o+u==0: print(name, 0); return
    p=o/(o+u); se=math.sqrt(.25/(o+u))
    print(f"{name:45s} n={len(g):5d} over%={p*100:5.1f} z={(p-.5)/se:+.2f} mean(tot-line)={st.mean(r['res'] for r in g):+.2f}")
for lo,hi in [(2015,2025),(2015,2019),(2020,2025)]:
    print(f"######## {lo}-{hi}")
    E=[r for r in rows if lo<=r['s']<=hi]
    summ('ALL', E)
    REG=[r for r in E if r['game_type']=='REG']
    summ('playoffs', [r for r in E if r['game_type']!='REG'])
    out=[r for r in E if r['roof'] in ('outdoors','open') and r['wind']!='']
    for a,b in [(0,9),(10,14),(15,19),(20,99),(15,99)]:
        summ(f'outdoor wind {a}-{b}', [r for r in out if a<=float(r['wind'])<=b])
    ot=[r for r in E if r['roof'] in ('outdoors','open') and r['temp']!='']
    summ('temp <=32F', [r for r in ot if float(r['temp'])<=32])
    summ('temp >=85F', [r for r in ot if float(r['temp'])>=85])
    summ('dome/closed', [r for r in E if r['roof'] in ('dome','closed')])
    summ('divisional (REG)', [r for r in REG if r['div_game']=='1'])
    summ('non-divisional (REG)', [r for r in REG if r['div_game']=='0'])
    summ('Thursday (REG, excl wk1)', [r for r in REG if r['weekday']=='Thursday' and r['week']!='1'])
    summ('Monday (REG)', [r for r in REG if r['weekday']=='Monday'])
    summ('Sunday night >=19:00 (REG)', [r for r in REG if r['weekday']=='Sunday' and r['gametime']>='19:00'])
    pt=[r for r in REG if r['weekday'] in ('Thursday','Monday') or (r['weekday']=='Sunday' and r['gametime']>='19:00')]
    summ('ALL primetime (Thu/Mon/SNF, REG)', pt)
    summ('Sunday day (REG)', [r for r in REG if r['weekday']=='Sunday' and r['gametime']<'19:00'])
    summ('neutral site REG (intl mostly)', [r for r in REG if r['location']=='Neutral'])
    summ('both teams off bye (rest>=13 both)', [r for r in REG if int(r['away_rest'])>=13 and int(r['home_rest'])>=13])
    summ('one team off bye', [r for r in REG if (int(r['away_rest'])>=13) != (int(r['home_rest'])>=13)])
    summ('either team rest<=5 (short wk, not TNF?)', [r for r in REG if min(int(r['away_rest']),int(r['home_rest']))<=5])
    summ('weeks 1-4', [r for r in REG if int(r['week'])<=4])
    summ('weeks 15+ (REG)', [r for r in REG if int(r['week'])>=15])
    summ('line <=40', [r for r in E if float(r['total_line'])<=40])
    summ('line >=50', [r for r in E if float(r['total_line'])>=50])
