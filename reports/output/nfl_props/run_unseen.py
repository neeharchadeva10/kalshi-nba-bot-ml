"""Unseen run (once).  REFUSES unless reports/output/nfl_props/AUDIT_OK exists (Opus audit)."""
import sys, json
from pathlib import Path
H = Path(__file__).parent
if not (H / "AUDIT_OK").exists():
    raise SystemExit("REFUSED: reports/output/nfl_props/AUDIT_OK is missing. Opus must audit the discovery code first.")
sys.path.insert(0, str(H))
import numpy as np
import build_data, run_discovery as R, nfl_lib as L

if (H / "unseen_result.json").exists():
    raise SystemExit("unseen already run once; refusing to run again.")
disc = json.loads((H / "discovery_result.json").read_text())
best = disc["selected"]
if best is None:
    raise SystemExit("no version was selected in discovery")
build_data.main("unseen")
d = R.load_bets("unseen"); x = R.real_bets(d)[best]
st = L.version_stats(x.game.tolist(), x.net.tolist())
rng = np.random.default_rng(L.SEED); pools = R.pools_of(d)
real = [dict(paid=r.paid, week=r.week, stat=r.stat, player=r.pid) for r in x.itertuples()]
means = []
for _ in range(500):
    nets = [L.draw_matched(rng, [p for p in pools[(b["stat"], b["week"])] if p["player"] != b["player"]], b["paid"])["net"]
            for b in real if [p for p in pools.get((b["stat"], b["week"]), []) if p["player"] != b["player"]]]
    means.append(np.mean(nets))
coin = float((np.array(means) >= st["mean"]).mean())
passed = st["mean"] > 0 and st["lb"] > 0 and st["n"] >= 40 and coin <= 0.05 and disc["luck_test"]["verdict"] == "PASS"
out = dict(version=best, stats=st, coin_flip_share=coin, luck_test_discovery=disc["luck_test"]["verdict"], PASS=bool(passed))
(H / "unseen_result.json").write_text(json.dumps(out, indent=1, default=str)); print(out)
