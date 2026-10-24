"""Research log #61: UNSEEN run (ET dates 2026-01-23..2026-04-12), ONCE, for the version selected on discovery.
REFUSES unless: AUDIT_OK exists as JSON {"sha256": {relative path: hash}} matching every file in ls_common.required_paths()
(code + the two sqlite inputs + events list + play-by-play + results_discovery.json); neither unseen_started.json nor
results_unseen.json exists; and the discovery luck test PASSED with an eligible selected version.
unseen_started.json is written BEFORE any unseen outcome is loaded. AUDIT_OK is created by the Opus audit, never by the builder."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ls_common as C
import ls_pipeline as P


def check_audit(here: Path, root: Path, paths=None):
    """None when AUDIT_OK is valid, else (code, message)."""
    f = here / "AUDIT_OK"
    if not f.exists():
        return 2, "REFUSING: AUDIT_OK does not exist. The unseen half waits for the Opus audit."
    try:
        want = json.loads(f.read_text())["sha256"]
        assert isinstance(want, dict)
    except Exception:
        return 4, 'REFUSING: AUDIT_OK is not JSON of the form {"sha256": {path: hash}}.'
    for p in (paths if paths is not None else C.required_paths(root)):
        if not p.exists():
            return 4, f"REFUSING: required input missing: {p}"
        key = C.rel(p, root)
        if want.get(key) != C.sha256_file(p):
            return 4, f"REFUSING: hash mismatch or missing in AUDIT_OK for {key}"
    return None


def refusal(here: Path = C.HERE, root: Path = C.ROOT, paths=None):
    """(exit_code, message) when the run must be refused, else None. Pure w.r.t. its arguments (testable)."""
    r = check_audit(here, root, paths)
    if r:
        return r
    if (here / "unseen_started.json").exists() or (here / "results_unseen.json").exists():
        return 3, "REFUSING: unseen_started.json or results_unseen.json exists; the unseen half is run once."
    dj = here / "results_discovery.json"
    if not dj.exists():
        return 5, "REFUSING: results_discovery.json missing."
    disc = json.loads(dj.read_text())
    if disc.get("luck_test", {}).get("verdict") != "PASS" or not disc.get("selected"):
        return 5, "REFUSING: discovery luck test did not pass (or no eligible version): no unseen run."
    return None


def main():
    r = refusal()
    if r:
        print(r[1])
        sys.exit(r[0])
    disc = json.loads((C.HERE / "results_discovery.json").read_text())
    sel = disc["selected"]
    band, tail, direction = sel["band"], sel["tail"], sel["dir"]
    hashes = {C.rel(p): C.sha256_file(p) for p in C.required_paths()}
    (C.HERE / "unseen_started.json").write_text(json.dumps(
        {"started_utc": datetime.now(timezone.utc).isoformat(), "selected": sel, "sha256": hashes}, indent=1, default=str))
    # ---- only now are unseen outcomes loaded
    ev = P.select_events(C.UNSEEN_FIRST, C.UNSEEN_LAST)
    assert ev.game_date_et.min() >= C.UNSEEN_FIRST and ev.game_date_et.max() <= C.UNSEEN_LAST
    excl = P.excluded_games(P.quote_check_rows(ev))
    games, cands, skips = P.build_events(ev, exclude=excl)
    finals = P.load_finals([g["gid"] for g in games if g["gid"] is not None])
    fits, summ, cal = P.part_a(games, finals)
    bets = P.version_bets(cands, band, tail, direction)
    nets = np.array([b[1] for b in bets])
    byg = {}
    for g, n, _ in bets:
        byg.setdefault(g, []).append(n)
    lb = P.bootstrap_lb(byg)
    mean = float(nets.mean()) if len(nets) else None
    cf = P.coin_flip(cands, band, tail, direction, mean)
    bar = {"1_mean_pos_and_boot_lb_pos": bool(mean is not None and lb is not None and mean > 0 and lb > 0),
           "2_n_ge_60": len(nets) >= C.MIN_N,
           "3_coin_flip_share_le_5pct": bool(cf["share_ge_real"] is not None and cf["share_ge_real"] <= 0.05),
           "4_discovery_luck_passed": True}
    pd.DataFrame(fits).to_csv(C.HERE / "partA_games_unseen.csv", index=False)
    pd.DataFrame(cal).to_csv(C.HERE / "calibration_unseen.csv", index=False)
    P.candidates_frame(cands).to_csv(C.HERE / "records_unseen.csv", index=False)
    res = {"selected": sel, "n": len(nets), "mean": mean, "win": float((nets > 0).mean()) if len(nets) else None,
           "avg_paid": float(np.mean([b[2] for b in bets])) if bets else None, "bootstrap_lb95": lb, "coin_flip": cf,
           "pass_bar": bar, "PASS": all(bar.values()), "games_processed": len(games), "quote_check_excluded_games": excl,
           "game_level_skips": {k: len(v) for k, v in skips.items()},
           "part_A": {"summary": summ, "calibration": cal},
           "all_versions_unseen_for_information": P.all_version_stats(cands)}
    (C.HERE / "results_unseen.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
