import csv, sys, collections
p = sys.argv[1]
rows = list(csv.DictReader(open(p, encoding='utf-8')))
by = collections.defaultdict(lambda: collections.Counter())
for r in rows:
    s = int(r['season'])
    if s < 1999: continue
    c = by[s]
    c['n'] += 1
    if r['away_score'] != '': c['played'] += 1
    if r['total_line'] != '': c['total_line'] += 1
    if r['roof'] in ('outdoors','open'): 
        c['outdoor'] += 1
        if r['temp'] != '': c['out_temp'] += 1
        if r['wind'] != '': c['out_wind'] += 1
    if r['referee'] != '': c['ref'] += 1
    if r['home_coach'] != '': c['coach'] += 1
    if r['away_rest'] != '': c['rest'] += 1
    if r['under_odds'] != '': c['under_odds'] += 1
for s in sorted(by):
    c = by[s]
    print(s, dict(c))
