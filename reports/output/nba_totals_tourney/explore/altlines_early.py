"""[EXPLORATORY - discovery half only - information, not a test] Over on lower alt lines / under on higher alt lines.
Kalshi 2025-26 discovery (first 2 weeks and all) + sportsbook 2007-08..2023-24 frequencies (no prices)."""
import sys, sqlite3
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import tourney_run as trun  # noqa: E402
import lib  # noqa: E402
v2 = trun.v2
games, strikes, info = v2.load_games()
gd = games[games.split == "discovery"].copy()
gd, sd = v2.attach_outcomes(gd, strikes, ["discovery"])
assert (gd.split == "discovery").all()
med = gd.set_index("event")["median"]; date = gd.set_index("event")["date"]
q = sd[sd.event.isin(gd.event)].dropna(subset=["mid", "y"]).copy()
q["off"] = q.strike - q.event.map(med); q["date"] = q.event.map(date)
rng = np.random.default_rng(20261021)
def summ(x, side):
    if side == "over":
        price_t, price_m, won = x.ask.values, x.bid.values, x.y.values
    else:
        price_t, price_m, won = 1 - x.bid.values, 1 - x.ask.values, 1 - x.y.values
    ft = np.array([lib.FEE.order_fee(p, 1) for p in price_t]); fm = np.array([lib.FEE.order_fee(p, 1, maker=True) for p in price_m])
    nt, nm = won - price_t - ft, won - price_m - fm
    idx = rng.integers(0, len(x), (10000, len(x)))
    ci = lambda v: np.percentile(v[idx].mean(1), [2.5, 97.5])
    return dict(n=len(x), win=won.mean(), px_ask=price_t.mean(), be_taker=(price_t + ft).mean(), net_taker=nt.mean(), ci_t=ci(nt),
                px_bid=price_m.mean(), be_maker=(price_m + fm).mean(), net_maker=nm.mean(), ci_m=ci(nm))
for label, sub in (("FIRST 2 WEEKS (Oct 21-Nov 3 2025)", q[(q.date >= "2025-10-21") & (q.date <= "2025-11-03")]), ("ALL DISCOVERY (Oct 21 2025-Jan 22 2026)", q)):
    print("\n==", label)
    print(f"{'bet':26s} {'n':>4s} {'win':>6s} {'ask':>5s} {'BE_t':>6s} {'net_t':>7s} {'CI_t':>17s} {'bid':>5s} {'BE_m':>6s} {'net_m':>7s} {'CI_m':>17s}")
    for side, offs in (("over", (0, -3, -6, -9, -12)), ("under", (0, 3, 6, 9, 12))):
        for d in offs:
            rows = []
            for ev, gg in sub.groupby("event"):
                r = gg.iloc[int(np.argmin(np.abs(gg.off.values - d)))]
                if abs(r.off - d) <= 1.5: rows.append(r)
            x = pd.DataFrame(rows)
            if len(x) < 10: continue
            s = summ(x, side)
            print(f"{side+' line'+('%+d' % d if d else ' (main)'):26s} {s['n']:4d} {s['win']:6.1%} {s['px_ask']:5.2f} {s['be_taker']:6.1%} {s['net_taker']:+7.3f} [{s['ci_t'][0]:+.3f},{s['ci_t'][1]:+.3f}] {s['px_bid']:5.2f} {s['be_maker']:6.1%} {s['net_maker']:+7.3f} [{s['ci_m'][0]:+.3f},{s['ci_m'][1]:+.3f}]")
# sportsbook frequencies (no prices): first 14 days, P(points > OU + d)
c = sqlite3.connect("file:" + str(HERE.parents[3] / "data/raw/nba_odds_samples/OddsData.sqlite") + "?mode=ro", uri=True)
ts = [r[0] for r in c.execute("select name from sqlite_master where type='table'")]
seasons = sorted({t.replace('odds_', '').replace('_new', '') for t in ts if t.startswith('odds_')} - {"2025-26"})
E = []
for s in seasons:
    t = f'odds_{s}_new' if f'odds_{s}_new' in ts else f'odds_{s}'
    d_ = pd.read_sql(f'select Date, OU, Points from "{t}"', c).dropna(); d_ = d_[d_.OU > 100]
    d_["Date"] = pd.to_datetime(d_.Date); E.append(d_[d_.Date < d_.Date.min() + pd.Timedelta(days=14)])
E = pd.concat(E); diff = E.Points - E.OU
print(f"\n== SPORTSBOOK first 14 days, 17 seasons, n={len(E)} (frequency only, no alt-line prices)")
for d in (0, -3, -6, -9, -12):
    print(f"over line{d:+d}: {(diff > d).mean():.1%}   |   under line{-d:+d}: {(diff < -d).mean():.1%}")
