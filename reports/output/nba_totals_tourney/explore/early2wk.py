"""[EXPLORATORY - discovery half only - information, not a test] Kalshi 2025-26 first two weeks (ET Oct 21 - Nov 3):
YES at the strike with mid nearest 0.50 (mid 15-85c), taker at ask and maker at bid (fill assumed), 1 contract basis."""
import sys
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import tourney_run as trun  # noqa: E402
import lib  # noqa: E402
v2 = trun.v2
games, strikes, info = v2.load_games()
gd = games[games.split == "discovery"].copy()
gd, sd = v2.attach_outcomes(gd, strikes, ["discovery"])
assert (gd.split == "discovery").all()
gd = gd[(gd.date >= "2025-10-21") & (gd.date <= "2025-11-03")]
q = sd[sd.event.isin(gd.event)].dropna(subset=["mid", "y"])
q = q[(q.mid >= 0.15 - 1e-9) & (q.mid <= 0.85 + 1e-9)]
rows = []
for ev, gg in q.groupby("event"):
    gg = gg.sort_values("strike"); r = gg.iloc[int(np.argmin(np.abs(gg.mid.values - 0.5)))]
    y = float(r.y)
    tk = y - r.ask - lib.FEE.order_fee(r.ask, 1)
    mk = y - r.bid - lib.FEE.order_fee(r.bid, 1, maker=True)
    rows.append((ev, y, r.ask, r.bid, tk, mk))
a = np.array([[x[1], x[2], x[3], x[4], x[5]] for x in rows]); n = len(a)
rng = np.random.default_rng(20261021); idx = rng.integers(0, n, (10000, n))
def ci(v): b = v[idx].mean(1); return np.percentile(b, [2.5, 97.5])
w = a[:, 0].mean(); se = np.sqrt(w * (1 - w) / n)
print(f"n={n} games; overs won {int(a[:,0].sum())} = {w:.1%}; 95% CI for win rate {ci(a[:,0])[0]:.1%} to {ci(a[:,0])[1]:.1%}")
print(f"avg ask {a[:,1].mean():.3f}, avg bid {a[:,2].mean():.3f}")
for name, col in (("taker @ask", 3), ("maker @bid (fill assumed)", 4)):
    lo, hi = ci(a[:, col]); print(f"{name}: net/contract {a[:,col].mean():+.4f}, 95% CI [{lo:+.4f}, {hi:+.4f}]")
be_t = (a[:,1] + [lib.FEE.order_fee(p, 1) for p in a[:,1]]).mean(); be_m = (a[:,2] + [lib.FEE.order_fee(p, 1, maker=True) for p in a[:,2]]).mean()
print(f"break-even win rate: taker {be_t:.1%}, maker {be_m:.1%}")
