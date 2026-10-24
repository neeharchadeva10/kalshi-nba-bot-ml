"""Research log #51: UNSEEN run (ET dates 2026-01-23 .. 2026-04-12), ONCE, for the version selected on discovery.
REFUSES unless AUDIT_OK exists (created by the Opus audit, never by the builder), refuses if results_unseen.json exists,
and refuses unless results_discovery.json has luck-test verdict PASS with a selected version and a complete download."""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib as L


def refusal(here=L.HERE):
    """(exit_code, message) when the run must be refused, else None. Pure w.r.t. `here`."""
    if not (here / "AUDIT_OK").exists():
        return 2, "REFUSING: AUDIT_OK does not exist. The unseen half waits for the Opus audit."
    if (here / "results_unseen.json").exists():
        return 3, "REFUSING: results_unseen.json already exists; the unseen half is run once."
    dj = here / "results_discovery.json"
    if not dj.exists():
        return 4, "REFUSING: results_discovery.json missing."
    disc = json.loads(dj.read_text())
    if disc.get("partial_download"):
        return 4, "REFUSING: results_discovery.json was made from a partial download."
    if disc.get("luck_test", {}).get("verdict") != "PASS" or not disc.get("selected"):
        return 4, "REFUSING: discovery luck test did not PASS (or no eligible version): no unseen run."
    return None


def main():
    r = refusal()
    if r:
        print(r[1])
        sys.exit(r[0])
    disc = json.loads((L.HERE / "results_discovery.json").read_text())
    sel = disc["selected"]
    v = (int(sel["R"]), sel["state"], int(sel["M"]))
    assert L.vname(v) == sel["version"]
    cov = L.C46.coverage(L.UNSEEN_FIRST, L.UNSEEN_LAST)
    if cov["not_downloaded"] > 0:
        print("REFUSING: unseen download incomplete:", json.dumps(cov))
        sys.exit(6)
    recs, games, skips = L.build_records(L.UNSEEN_FIRST, L.UNSEEN_LAST)
    assert all(L.UNSEEN_FIRST <= x["date"] <= L.UNSEEN_LAST for x in recs.values()), "record outside the unseen window"
    bets, ps = L.version_bets(recs, v)
    nets = np.array([b[1] for b in bets])
    byg = {}
    for g, n in bets:
        byg.setdefault(g, []).append(n)
    lb = L.bootstrap_lb(byg)
    mean = float(nets.mean()) if len(nets) else None
    share = L.coin_flip_share(recs, v, mean)
    bar = {"1_mean_pos_and_boot_lb_pos": bool(mean is not None and lb is not None and mean > 0 and lb > 0),
           "2_n_ge_60": len(nets) >= 60,
           "3_coin_flip_share_le_5pct": bool(share is not None and share <= 0.05),
           "4_discovery_luck_passed": True}
    rows = L.all_version_stats(recs)
    L.records_frame(recs).to_csv(L.HERE / "records_unseen.csv", index=False)
    L.bets_frame(recs).to_csv(L.HERE / "bets_unseen.csv", index=False)
    import pandas as pd
    pd.DataFrame(rows).to_csv(L.HERE / "unseen_versions.csv", index=False)
    res = {"selected": sel, "n": len(nets), "mean": mean, "bootstrap_lb95": lb, "price_skips": ps,
           "coin_flip_share_ge_real": share, "pass_bar": bar, "PASS": all(bar.values()), "games_processed": len(games),
           "coverage": cov, "game_level_skips": {k: len(x) for k, x in skips.items()},
           "game_level_skip_events": {k: x[:30] for k, x in skips.items()}, "versions_for_reference": rows,
           "calibration_by_R_margin": L.calibration_and_descriptives(recs), **L.diag_blocks(recs)}
    (L.HERE / "results_unseen.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
