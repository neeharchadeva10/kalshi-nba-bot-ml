import csv, sys, collections, math, statistics as st
rows = [r for r in csv.DictReader(open(sys.argv[1], encoding='utf-8')) if r['away_score']!='' and r['total_line']!='']
def era(lo, hi, reg_only=False):
    return [r for r in rows if lo <= int(r['season']) <= hi and (not reg_only or r['game_type']=='REG')]
for lo, hi in [(2015,2025),(2020,2025)]:
    g = era(lo, hi)
    tot = [int(r['total']) for r in g]
    line = [float(r['total_line']) for r in g]
    resid = [t-l for t,l in zip(tot,line)]
    n=len(g)
    print(f"== {lo}-{hi} n={n} mean total={st.mean(tot):.2f} sd={st.pstdev(tot):.2f} mean line={st.mean(line):.2f} mean resid={st.mean(resid):.2f} sd resid={st.pstdev(resid):.2f}")
    ov = sum(1 for x in resid if x>0); un=sum(1 for x in resid if x<0); pu=n-ov-un
    print(f"   over {ov} under {un} push {pu} over%={ov/(ov+un):.3f}")
    # frequency of each total vs smoothed (normal on resid conditional on line)
    c = collections.Counter(tot)
    sd = st.pstdev(resid)
    # expected count of exact total k under normal approx: sum over games of P(k-0.5<X<k+0.5), X~N(line, sd)
    def Phi(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
    exp = collections.Counter()
    for l in line:
        for k in range(20, 80):
            exp[k] += Phi((k+0.5-l)/sd)-Phi((k-0.5-l)/sd)
    print("   k  obs%  normal%  ratio")
    for k in range(30, 62):
        o = c[k]/n*100; e = exp[k]/n*100
        print(f"   {k}  {o:.2f}  {e:.2f}  {o/e:.2f}")
