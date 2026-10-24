"""#53 DISCOVERY run (exploratory: Opus has already seen the #48/#49 discovery taker results).  Run ONCE.
#48 family dates 2025-11-18..2026-01-22 ; #49 family = #49's discovery half (population.csv).  The split filters are applied before any
candle or outcome is read.  Writes results_discovery.json, discovery_versions.csv, records_discovery.csv.
Never touches unseen outcomes.  No network."""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402

import nm_lib as N  # noqa: E402
import fam48 as F48  # noqa: E402
import fam49 as F49  # noqa: E402
import nm_pipeline as P  # noqa: E402

K_FILE = F49.K_FILE


def main():
    if N.RESULTS_DISC.exists() and "--force" not in sys.argv:
        sys.exit("results_discovery.json exists: discovery is run ONCE (use --force only for a documented re-run).")
    t0 = time.time()
    fee_types = N.load_fee_types()
    k = json.loads(K_FILE.read_text())["k"]
    # ---------------- #49 family
    games49, skips49 = F49.build_games("discovery", k, fee_types)
    # ---------------- #48 family
    box = F48.L.Box()
    reports = F48.L.Reports()
    sel = N.filter_48_rows(F48.L.read_selection())                 # discovery rows ONLY, before any candle/outcome is read
    assert all(N.DISC_LO <= r["game_date"] <= N.DISC_HI for r in sel)
    settle = F48.load_settlements([r["ticker"] for r in sel])
    res48 = F48.collect(box, reports, sel, N.DISC_LO, N.DISC_HI, settle, fee_types)
    units = P.build_units(res48, games49)
    stats = P.version_table(units)
    chosen = N.select_version(stats, P.ORDER, N.MIN_N)
    luck = P.luck_test(res48, games49, units)
    rep = luck.pop("rep_best_lbs")
    good = [x for x in rep if x is not None and x > -1e9]
    luck["rep_best_lb_quantiles"] = {str(q): float(np.percentile(good, q)) for q in (5, 50, 95, 99)} if good else None
    luck["verdict"] = ("NOT RUN (no version with n >= 60 filled bets)" if luck["share"] is None else
                       ("FAIL (share > 5%)" if luck["share"] > 0.05 else "PASS (share <= 5%)"))
    if luck["share"] is not None:
        luck["verdict"] = "PASS" if luck["share"] <= 0.05 else "FAIL"
    # ---------------- write
    cols = ["version", "family", "signals", "fills", "fill_rate", "n_units", "n_filled", "win_pct_filled", "mean_net", "sd", "lb",
            "mean_net_plus1c", "unfilled_signals", "unfilled_win_pct", "unfilled_mean_win_value", "mean_win_value_filled",
            "eligible_n60", "selected"]
    with open(HERE / "discovery_versions.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for v in P.ORDER:
            s = stats[v]
            w.writerow([P.vlabel(v), v[0], s["signals"], s["fills"], s["fill_rate"], s["n_units"], s["n"], s["win_pct"], s["mean"],
                        s["sd"], s["lb"], s["mean_net_plus1c"], s["unfilled_signals"], s["unfilled_win_pct"],
                        s["unfilled_mean_win_value"], s["mean_win_value_filled"], s["n"] >= N.MIN_N, v == chosen])
    with open(HERE / "records_discovery.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["version", "unit_id", "game", "filled", "net", "net_plus1c", "limit", "signals", "fills",
                    "filled_win_values", "unfilled_win_values"])
        for v in P.ORDER:
            for u in units[v]:
                w.writerow([P.vlabel(v), u["id"], u["game"] if isinstance(u["game"], str) else " ".join(u["game"]), u["filled"],
                            u["net"], u["net1c"], u["limit"], u["signals"], u["fills"],
                            ";".join(map(str, u["fw"])), ";".join(map(str, u["uw"]))])
    series = sorted({N.series_of(r["ticker"]) for r in sel} | {"KXNBATOTAL", "KXNBATEAMTOTAL", "KXNBA1HTOTAL"})
    res = dict(
        runtime_s=round(time.time() - t0), note="EXPLORATORY: discovery was not clean (Opus had seen the #48/#49 taker discovery results).",
        k_49=k, fee_type_by_series={s: fee_types.get(s, "unknown (not in cached series definitions) -> maker fee charged") for s in series},
        n_games_49_discovery=len(games49), game_skips_49=skips49,
        n_48_signals_posted=sum(len(res48["bets"][v]) for v in F48.VERSIONS),
        selected_version=(P.version_dict(chosen) if chosen else None), selected_stats=(stats[chosen] if chosen else None),
        versions_with_n_ge_60=sum(1 for v in P.ORDER if stats[v]["n"] >= N.MIN_N), luck_test=luck,
        counts_48=dict(res48["counts"]), pool_size_48=len(res48["pool"]),
        vskips_48={P.vlabel(("48",) + v): dict(res48["vskips"][v]) for v in F48.VERSIONS},
        versions={P.vlabel(v): stats[v] for v in P.ORDER})
    N.RESULTS_DISC.write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({x: res[x] for x in ("runtime_s", "fee_type_by_series", "n_games_49_discovery", "game_skips_49",
                                           "selected_version", "selected_stats", "versions_with_n_ge_60", "luck_test",
                                           "counts_48")}, indent=1, default=str))


if __name__ == "__main__":
    main()
