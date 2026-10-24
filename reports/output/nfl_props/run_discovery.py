"""Discovery analysis: 9 versions, selection (highest bootstrap LB among n >= 40), price-matched luck test (500 reps, seed 20261022).
Reads bets_discovery.csv written by build_data.py.  Never touches unseen."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np, pandas as pd
import nfl_lib as L

H = Path(__file__).parent


def load_bets(split: str) -> pd.DataFrame:
    d = pd.read_csv(H / f"bets_{split}.csv")
    d = d[d.has_main & d.net.notna()].copy()
    d["game"] = d.event.str.split("-").str[1]
    d["win"] = d.result.eq("yes")
    return d


def real_bets(d: pd.DataFrame) -> dict:
    out = {}
    for stat, shock in L.VERSIONS:
        x = d[(d.stat == stat) & d.tags.fillna("").map(lambda t: shock in t.split("|"))]
        out[f"{stat}_{shock}"] = x
    return out


def pools_of(d: pd.DataFrame) -> dict:
    pools = {}
    for r in d.itertuples():
        pools.setdefault((r.stat, r.week), []).append(dict(paid=r.paid, net=r.net, game=r.game, player=r.pid))
    return pools


def main():
    d = load_bets("discovery")
    rb = real_bets(d)
    stats = {}
    for v, x in rb.items():
        stats[v] = L.version_stats(x.game.tolist(), x.net.tolist()) if len(x) else dict(n=0, games=0, mean=float("nan"), lb=float("nan"))
        stats[v]["win_rate"] = float(x.win.mean()) if len(x) else None
        stats[v]["avg_price_paid"] = float(x.paid.mean()) if len(x) else None
    best, elig = L.select_version(stats)
    print("version stats (discovery):")
    for v in sorted(stats):
        s = stats[v]; print(f"  {v:12s} n={s['n']:4d} games={s['games']:3d} mean={s['mean']:+.4f} LB={s['lb']:+.4f} win={s['win_rate']} avgpaid={s['avg_price_paid']}")
    print("eligible (n>=40):", elig, " selected:", best)
    res = dict(versions=stats, eligible=elig, selected=best, n_bets_table=len(d), pool_size=len(d))
    if best is not None:
        real = {v: [dict(paid=r.paid, week=r.week, stat=r.stat, game=r.game, player=r.pid) for r in rb[v].itertuples()] for v in elig}
        lt = L.luck_test(real, pools_of(d), stats[best]["lb"], elig)
        res["luck_test"] = dict(real_best_lb=stats[best]["lb"], share_reps_ge_real=lt["share_ge_real"], n_reps=lt["n_reps"],
                                n_valid=lt["n_valid"], verdict=lt["verdict"], tolerance_c=3, boot_draws_per_rep=1000,
                                median_rep_best_lb=float(np.nanmedian(lt["best_lb_per_rep"])),
                                p95_rep_best_lb=float(np.nanpercentile(lt["best_lb_per_rep"], 95)))
        # price-matched coin flip for the selected version (informational in discovery)
        rng = np.random.default_rng(L.SEED); pools = pools_of(d); means = []
        for _ in range(500):
            nets = []
            for b in real[best]:
                pool = [p for p in pools[(b["stat"], b["week"])] if p["player"] != b["player"]]
                if pool: nets.append(L.draw_matched(rng, pool, b["paid"])["net"])
            means.append(np.mean(nets))
        res["coin_flip_selected"] = dict(share_copies_mean_ge_real=float((np.array(means) >= stats[best]["mean"]).mean()),
                                         copy_mean_median=float(np.median(means)))
        print("luck test:", json.dumps(res["luck_test"], indent=1)); print("coin flip:", res["coin_flip_selected"])
    (H / "discovery_result.json").write_text(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
