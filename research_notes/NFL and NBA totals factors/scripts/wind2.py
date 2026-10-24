import csv, sys, collections, statistics as st
rows=[r for r in csv.DictReader(open(sys.argv[1],encoding='utf-8')) if r['away_score']!='' and r['total_line']!='' and r['roof'] in ('outdoors','open') and r['wind']!='' and 2015<=int(r['season'])<=2025]
g=[r for r in rows if 10<=float(r['wind'])<=19]
def rec(x):
    o=sum(1 for r in x if int(r['total'])>float(r['total_line'])); u=sum(1 for r in x if int(r['total'])<float(r['total_line'])); return o,u
print("by season (wind 10-19): over-under")
for s in range(2015,2026):
    o,u=rec([r for r in g if int(r['season'])==s]); print(s, f"{o}-{u}", f"under%={u/(o+u)*100:.0f}" if o+u else '')
print("by home team (>=15 games):")
by=collections.defaultdict(list)
for r in g: by[r['home_team']].append(r)
tot_o,tot_u=rec(g)
for t,x in sorted(by.items(), key=lambda kv:-len(kv[1])):
    if len(x)>=15:
        o,u=rec(x); print(t,len(x),f"{o}-{u}")
# leave-top-3-teams-out
top=[t for t,_ in sorted(by.items(), key=lambda kv:-len(kv[1]))[:3]]
o,u=rec([r for r in g if r['home_team'] not in top]); print('excluding',top, f"{o}-{u} under%={u/(o+u)*100:.1f}")
# by closing-line bucket within windy
for lo,hi in [(0,41),(41.5,45),(45.5,99)]:
    o,u=rec([r for r in g if lo<=float(r['total_line'])<=hi]); print('line',lo,hi,f"{o}-{u} under%={u/(o+u)*100:.1f}")
# does the market shade windy totals at all? compare closing line windy vs calm for same home team (crude)
