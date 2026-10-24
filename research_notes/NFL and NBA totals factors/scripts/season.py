import csv, sys, statistics as st, math
rows=[r for r in csv.DictReader(open(sys.argv[1],encoding='utf-8')) if r['away_score']!='' and r['total_line']!='' and r['game_type']=='REG']
def ov(g):
    o=sum(1 for r in g if int(r['total'])>float(r['total_line'])); u=sum(1 for r in g if int(r['total'])<float(r['total_line'])); return o/(o+u) if o+u else float('nan')
xs=[];ys=[]
print("season  over%_all  over%_wk1-6  over%_wk7+  mean(tot-line)_wk1-6  wk7+  avg_line")
for s in range(2006,2026):
    g=[r for r in rows if int(r['season'])==s]
    a=[r for r in g if int(r['week'])<=6]; b=[r for r in g if int(r['week'])>6]
    ma=st.mean(int(r['total'])-float(r['total_line']) for r in a); mb=st.mean(int(r['total'])-float(r['total_line']) for r in b)
    xs.append(ma); ys.append(mb)
    print(f"{s}   {ov(g)*100:5.1f}    {ov(a)*100:5.1f}     {ov(b)*100:5.1f}     {ma:+.2f}     {mb:+.2f}   {st.mean(float(r['total_line']) for r in g):.1f}")
mx,my=st.mean(xs),st.mean(ys)
c=sum((a-mx)*(b-my) for a,b in zip(xs,ys))/math.sqrt(sum((a-mx)**2 for a in xs)*sum((b-my)**2 for b in ys))
print(f"corr(season early mean resid, later mean resid) over 2006-2025 = {c:+.2f} (n={len(xs)})")
