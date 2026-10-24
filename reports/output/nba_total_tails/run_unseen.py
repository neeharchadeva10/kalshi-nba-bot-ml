"""Research log #52: UNSEEN run (ET 2026-01-23..2026-04-12), ONCE, for the version selected on discovery.
REFUSES unless AUDIT_OK exists (created by the Opus audit, never by the builder), refuses if results_unseen.json exists, and
refuses unless the discovery luck test is PASS with a selected version."""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tails_lib as L


def refusal(here=L.HERE):
    """Return (exit_code, message) when the run must be refused, else None. Pure w.r.t. `here`."""
    if not (here / "AUDIT_OK").exists():
        return 2, "REFUSING: AUDIT_OK does not exist. The unseen half waits for the Opus audit."
    if (here / "results_unseen.json").exists():
        return 3, "REFUSING: results_unseen.json already exists; the unseen half is run once."
    p = here / "results_discovery.json"
    if not p.exists():
        return 4, "REFUSING: results_discovery.json missing."
    disc = json.loads(p.read_text())
    if disc.get("luck_test", {}).get("verdict") != "PASS" or not disc.get("selected"):
        return 4, "REFUSING: discovery luck test is not PASS (or no eligible version): no unseen run."
    return None


def main():
    r = refusal()
    if r:
        print(r[1])
        sys.exit(r[0])
    import pandas as pd
    disc = json.loads((L.HERE / "results_discovery.json").read_text())
    sel = disc["selected"]
    key = (sel["cp"], float(sel["band"]), sel["tail"])
    recs, games, skips = L.build_records(L.UNSEEN_FIRST, L.UNSEEN_LAST)
    assert all(L.UNSEEN_FIRST <= x["date"] <= L.UNSEEN_LAST for x in recs), "record outside the unseen window"
    rows = [b for x in recs for b in L.bet_rows(x)]
    pd.DataFrame(rows).to_csv(L.HERE / "records_unseen.csv", index=False)
    bets = [x for x in rows if x["skip"] is None and (x["cp"], x["band"], x["tail"]) == key]
    nets = np.array([b["net_fav"] for b in bets])
    byg = {}
    for b in bets:
        byg.setdefault(b["game"], []).append(b["net_fav"])
    lbv = L.IGP.bootstrap_lb(byg, draws=10_000, seed=L.SEED)
    mean = float(nets.mean()) if len(nets) else None
    share = L.coin_flip_share(bets, 500, L.SEED)
    bar = {"1_mean_pos_and_boot_lb_pos": bool(mean is not None and lbv is not None and mean > 0 and lbv > 0),
           "2_n_ge_60": len(nets) >= 60,
           "3_coin_flip_share_le_5pct": bool(share is not None and share <= 0.05),
           "4_discovery_luck_passed": True}
    res = {"selected": sel, "n": len(nets), "mean": mean, "bootstrap_lb95": lbv, "coin_flip_share_ge_real": share,
           "pass_bar": bar, "PASS": all(bar.values()), "versions": L.version_stats(rows),
           "game_level_skips": {k: len(v) for k, v in skips.items()},
           "calibration": L.calibration(recs), "band_counts": L.band_counts(recs)}
    (L.HERE / "results_unseen.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
