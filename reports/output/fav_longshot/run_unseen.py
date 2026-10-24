"""Unseen run of research log #47.  REFUSES to run unless reports/output/fav_longshot/AUDIT_OK exists (Opus audit gate)."""
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fav_longshot as fl  # noqa: E402


def check_gate(here=HERE):
    if not (here / "AUDIT_OK").exists():
        raise SystemExit("REFUSED: AUDIT_OK not present; unseen data must not be touched before the audit.")


def main():
    check_gate()
    disc = json.loads((HERE / "results_discovery.json").read_text())
    sel = disc["selected_version"]
    if not sel:
        raise SystemExit("No version selected in discovery (none with n >= 60): pass bar fails.")
    sport, dl, side, band = sel.split("|")
    lo, hi = (int(x) for x in band.split("-"))
    v = (sport, dl, side, lo, hi)
    excl = Counter()
    recs = fl.build_quotes(sport, "unseen", excl)
    bets_v = fl.bets_for_version(recs, v, excl)
    st = fl.lb_stats([b["net"] for b in bets_v])
    res = {"selected_version": sel, "unseen_stats": st, "skips_and_counts": dict(sorted(excl.items()))}
    if st["n"]:
        res["bootstrap_lb_95"] = fl.bootstrap_lb([b["net"] for b in bets_v])
        res["coin_flip"] = fl.coin_flip_unseen(recs, bets_v, v)
        res["maker_mean_info_only"] = sum(b["maker_net"] for b in bets_v) / st["n"]
        luck_ok = disc["luck_test"].get("verdict") == "PASS"
        res["pass_bar"] = {"1_mean_pos_and_lb_pos": st["mean"] > 0 and res["bootstrap_lb_95"] > 0,
                           "2_n_ge_60": st["n"] >= 60,
                           "3_coin_flip_share_le_5pct": res["coin_flip"]["share_copies_as_good"] <= 0.05,
                           "4_discovery_luck_test_passed": luck_ok}
        res["PASS"] = all(res["pass_bar"].values())
    fl.write_csv(HERE / "calibration_unseen.csv", fl.calibration_rows(recs))
    (HERE / "results_unseen.json").write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
