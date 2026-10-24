import csv, sys, math, statistics as st
rows = [r for r in csv.DictReader(open(sys.argv[1], encoding='utf-8')) if r['away_score']!='' and r['total_line']!='' and 2015<=int(r['season'])<=2025]
res = [int(r['total'])-float(r['total_line']) for r in rows]
n=len(res); m=st.mean(res); sd=st.pstdev(res)
sk = sum((x-m)**3 for x in res)/n/sd**3
ku = sum((x-m)**4 for x in res)/n/sd**4 - 3
med = st.median(res)
print(f"n={n} mean={m:.2f} median={med:.2f} sd={sd:.2f} skew={sk:.3f} excess_kurt={ku:.3f}")
def Phi(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
print(" d   P(resid>d) emp   normal(0,sd)  normal(mean,sd)   n_tail")
for d in [-20.5,-17.5,-14.5,-10.5,-7.5,-6.5,-3.5,-0.5,0,0.5,3.5,6.5,7.5,10.5,14.5,17.5,20.5,24.5]:
    e = sum(1 for x in res if x>d)/n
    print(f"{d:6} {e:.3f}  {1-Phi(d/sd):.3f}  {1-Phi((d-m)/sd):.3f}  {sum(1 for x in res if x>d)}")
# by line size bucket: sd of resid and over rate
import collections
b = collections.defaultdict(list)
for r in rows:
    l=float(r['total_line'])
    k = '<=40' if l<=40 else ('40.5-44' if l<=44 else ('44.5-48' if l<=48 else ('48.5-51.5' if l<=51.5 else '>=52')))
    b[k].append(int(r['total'])-l)
for k in ['<=40','40.5-44','44.5-48','48.5-51.5','>=52']:
    v=b[k]; o=sum(1 for x in v if x>0); u=sum(1 for x in v if x<0)
    print(k, len(v), f"mean_resid={st.mean(v):.2f} sd={st.pstdev(v):.2f} over%={o/(o+u):.3f}")
