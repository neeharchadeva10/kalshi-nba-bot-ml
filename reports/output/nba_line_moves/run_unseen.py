"""Research log #54: UNSEEN run (ET game dates 2026-01-23 .. 2026-04-12), ONCE, for the version selected in discovery.
REFUSES unless: AUDIT_OK exists (created by the Opus audit, never by the builder), results_unseen.json does not exist, and the
discovery luck test is PASS."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lm as L


def main():
    ok, why = L.unseen_gate()
    if not ok:
        print("REFUSING:", why)
        sys.exit(2)
    import numpy as np
    import pipeline as PL
    disc = json.loads((L.HERE / "results_discovery.json").read_text())
    ver = tuple(disc["selected_version_tuple"])
    recs = PL.build_records(L.UNSEEN_FIRST, L.UNSEEN_LAST, "unseen", with_extras=False)
    bets = PL.version_bets(recs, ver)
    res = {"label": "unseen", "version": L.vname(ver), "n_games_in_window": len(recs), "n_bets": len(bets)}
    if bets:
        nets = [b["net"] for b in bets]
        bt = L.boot_lb(nets, [b["event"] for b in bets])
        cf = L.coin_flip([b["net_yes"] for b in bets], [b["net_no"] for b in bets], float(np.mean(nets)))
        res.update(boot=bt, coin_flip=cf, win_rate=float(np.mean([n > 0 for n in nets])))
        bar = {"1_mean_gt0_and_boot_lb_gt0": bool(bt["mean"] > 0 and bt["lb_2.5"] > 0), "2_n_ge_60": len(bets) >= 60,
               "3_coin_flip_le_5pct": cf["share_as_good"] <= 0.05, "4_discovery_luck_pass": disc["luck_test"]["verdict"] == "PASS"}
        res["pass_bar"], res["pass_bar_all"] = bar, all(bar.values())
    (L.HERE / "results_unseen.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
