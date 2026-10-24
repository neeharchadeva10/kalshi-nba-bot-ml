"""Research log #46: UNSEEN run (ET dates 2026-01-23 .. 2026-04-12), ONCE, for the version selected on discovery.
REFUSES to run unless AUDIT_OK exists (created by the Opus audit, never by the builder), the discovery download was complete
and its luck test passed, every unseen market has a final download state in BOTH candle tables, and no downloader is running.
The selected version (Q1, |move| >= 10, follow, hold) is pinned in code (amendment 2026-10-22)."""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C


def refusal(here=C.HERE):
    """Return (exit_code, message) when the run must be refused, else None. Pure w.r.t. `here` for testing the AUDIT_OK gate."""
    if not (here / "AUDIT_OK").exists():
        return 2, "REFUSING: AUDIT_OK does not exist. The unseen half waits for the Opus audit."
    if (here / "results_unseen.json").exists():
        return 3, "REFUSING: results_unseen.json already exists; the unseen half is run once."
    return None


def main():
    r = refusal()
    if r:
        print(r[1])
        sys.exit(r[0])
    disc = json.loads((C.HERE / "results_discovery.json").read_text())
    if disc.get("partial_download"):
        print("REFUSING: results_discovery.json was made from a partial download.")
        sys.exit(4)
    if disc["luck_test"]["verdict"] != "PASS" or not disc.get("selected"):
        print("REFUSING: discovery luck test did not pass (or no eligible version): no unseen run.")
        sys.exit(4)
    sel = disc["selected"]
    assert (sel["cp"], sel["T"], sel["dir"], sel["exit"]) == C.SELECTED, "discovery selection differs from the pinned version"
    run = C.running_downloaders()
    if run:
        print("REFUSING: a candle downloader is still running:", run)
        sys.exit(5)
    cov = C.coverage(C.UNSEEN_FIRST, C.UNSEEN_LAST)
    if cov["not_downloaded"] > 0:
        print("REFUSING: unseen download incomplete:", json.dumps(cov))
        sys.exit(6)
    import pipeline as P
    cp, T, d, ex = C.SELECTED
    recs, games, skips = P.build_records(C.UNSEEN_FIRST, C.UNSEEN_LAST)
    assert all(C.UNSEEN_FIRST <= v["date"] <= C.UNSEEN_LAST for v in recs.values()), "record outside the unseen window"
    bets = P.version_bets(recs, cp, T, d, ex)
    nets = np.array([b[1] for b in bets])
    byg = {}
    for g, n, _ in bets:
        byg.setdefault(g, []).append(n)
    lb = P.bootstrap_lb(byg)
    mean = float(nets.mean()) if len(nets) else None
    keys = [k for k, r_ in recs.items() if k[1] == cp and not r_["skip"] and abs(r_["move"]) >= T]
    rng2 = np.random.default_rng(C.SEED)
    cf = []
    for _ in range(500):
        tot = [P.bet_net(recs[k], C.side_for("fade" if rng2.random() < 0.5 else "follow", recs[k]["LC"], recs[k]["L0"]), ex)[0]
               for k in keys]
        cf.append(float(np.mean(tot)) if tot else float("nan"))
    share = float((np.array(cf) >= mean).mean()) if mean is not None and keys else None
    bar = {"1_mean_pos_and_boot_lb_pos": bool(mean is not None and lb is not None and mean > 0 and lb > 0),
           "2_n_ge_60": len(nets) >= 60,
           "3_coin_flip_share_le_5pct": bool(share is not None and share <= 0.05),
           "4_discovery_luck_passed": True}
    import pandas as pd
    keep = ["game", "date", "cp", "skip", "C_ts", "D", "lag", "age_s", "L0", "LC", "move", "final", "strike", "ticker",
            "yes_bid", "yes_ask", "spread", "result", "exit_t", "xbid", "xask"]
    pd.DataFrame([{k: v.get(k) for k in keep} for v in recs.values()]).to_csv(C.HERE / "records_unseen.csv", index=False)
    res = {"selected": sel, "n": len(nets), "mean": mean, "bootstrap_lb95": lb, "coin_flip_share_ge_real": share,
           "pass_bar": bar, "PASS": all(bar.values()), "games_processed": len(games), "coverage": cov,
           "game_level_skips": {k: len(v) for k, v in skips.items()},
           "game_level_skip_events": {k: v[:30] for k, v in skips.items()},
           "calibration": P.calibration(recs), **P.diag_blocks(recs)}
    (C.HERE / "results_unseen.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
