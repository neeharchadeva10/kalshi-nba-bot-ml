"""#49 UNSEEN run (once, for the version selected on discovery, with k frozen on discovery).
REFUSES without AUDIT_OK (written by Opus after the audit).  Pass bar (PREREGISTRATION.md):
1 mean net > 0 and bootstrap 95% LB (by game, 10,000 draws, seed 20261022) > 0 ; 2 >= 60 unseen bets ;
3 coin flip (random side, same markets/times): <= 5% of 500 copies do as well ; 4 the discovery luck test passed."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

if not (HERE / "AUDIT_OK").exists():
    sys.exit("REFUSED: reports/output/nba_internal_mismatch/AUDIT_OK does not exist (Opus audit pending). Unseen not run.")

import numpy as np  # noqa: E402
import mismatch as MM  # noqa: E402
import pipeline as P  # noqa: E402


def main():
    if not (HERE / "k_frozen.json").exists() or not (HERE / "results_discovery.json").exists():
        sys.exit("REFUSED: no frozen k / discovery selection.")
    disc = json.loads((HERE / "results_discovery.json").read_text())
    if not disc.get("selected_version"):
        sys.exit("REFUSED: discovery selected no version.")
    k = json.loads((HERE / "k_frozen.json").read_text())["k"]
    sel = tuple(int(x) if x.isdigit() else x for x in disc["selected_version"].replace(">=", "").split("|"))
    games, skips = P.build_games("unseen")
    P.set_d1h(games, k)
    stats, vlist, arr, skipc = P.version_table(games)
    j = vlist.index(sel)
    inc = arr["incl"][:, j]
    x = arr["real"][inc, j]
    n = len(x)
    res = {"version": disc["selected_version"], "k": k, "n_bets": n, "game_skips": skips, "version_skips": skipc}
    if n:
        res["mean_net"] = float(x.mean())
        res["boot_lb"] = MM.boot_mean_lb(x)
        rng = np.random.default_rng(MM.SEED)
        ny, nn = arr["ny"][inc, j], arr["nn"][inc, j]
        copies = [float(np.where(rng.integers(0, 2, n).astype(bool), ny, nn).mean()) for _ in range(500)]
        res["coin_flip_share_as_good"] = float(np.mean(np.array(copies) >= x.mean() - 1e-15))
        res["pass"] = {"1_mean_and_LB_gt_0": bool(x.mean() > 0 and res["boot_lb"] > 0), "2_n_ge_60": n >= 60,
                       "3_coin_flip_le_5pct": res["coin_flip_share_as_good"] <= 0.05,
                       "4_discovery_luck_passed": disc["luck_test"]["verdict"] == "PASS"}
        res["PASS"] = all(res["pass"].values())
    P.dump(HERE / "results_unseen.json", res)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
