"""Research log #48 DISCOVERY run only (game dates 2025-11-18 .. 2026-01-22): 24 versions, LB selection (n >= 60), price-matched
luck test (500 reps, seed 20261022), props-over base-rate table.  Refuses to run on the full discovery set until the download
of every discovery market has finished (discovery_download_complete.flag) unless --partial (debug; writes *_partial files).
Never touches dates after 2026-01-22.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analysis as A  # noqa: E402
import tm_lib as L  # noqa: E402

FLAG = L.HERE / "discovery_download_complete.flag"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--partial", action="store_true")
    a = ap.parse_args()
    if not FLAG.exists() and not a.partial:
        sys.exit("discovery download not complete (no flag); use --partial to debug on what is cached")
    suffix = "_partial" if a.partial else ""
    t0 = time.time()
    box = L.Box()
    reports = L.Reports()
    sel = [r for r in L.read_selection() if r["game_date"] <= L.DISC_HI]          # discovery rows ONLY
    assert all(L.DISC_LO <= r["game_date"] <= L.DISC_HI for r in sel)
    settle = A.load_settlements([r["ticker"] for r in sel])
    out = A.collect(box, reports, sel, L.DISC_LO, L.DISC_HI, settle)
    stats = A.version_stats(out["bets"])
    chosen = L.select_version(stats)
    # ---- luck test
    real = {}
    vkeys = {}
    for v in L.VERSIONS:
        vkeys[v] = [b["key"] for b in out["bets"][v]]
        for b in out["bets"][v]:
            real[b["key"]] = dict(night=b["night"], stat=b["stat"], ask=b["ask"], player=b["aid"], net=b["net"])
    cand, n_fb, n_empty = L.luck_candidates(real, out["pool"])
    pool_nets = [p["net"] for p in out["pool"]]
    luck = L.luck_test(vkeys, {k: r["net"] for k, r in real.items()}, cand, pool_nets, reps=500, seed=L.SEED)
    rep_lbs = luck.pop("rep_best_lbs")
    luck.update(n_distinct_real_bets=len(real), pool_size=len(out["pool"]), n_bets_nearest_price_fallback=n_fb,
                n_bets_no_other_player=n_empty, reps=500, seed=L.SEED,
                rep_best_lb_quantiles={q: float(__import__("numpy").percentile([x for x in rep_lbs if x is not None], q))
                                       for q in (5, 50, 95, 99)} if any(x is not None for x in rep_lbs) else None)
    luck["verdict"] = ("NOT RUN (no version with n >= 60)" if luck["share"] is None else
                       ("FAIL (share > 5%)" if luck["share"] > 0.05 else "PASS (share <= 5%)"))
    base = A.base_rate_table(out["base"])
    # ---- write
    vrows = []
    for v in L.VERSIONS:
        s = stats[v]
        vrows.append(dict(stat=v[0], M_star=v[1], beneficiaries=v[2], timing=v[3], n=s["n"], n_games=s["n_games"],
                          win_pct=s["win_pct"], mean_net=s["mean"], sd=s["sd"], lb=s["lb"], mean_ask=s["mean_ask"],
                          n_nonbinary=s["n_nonbinary"], mean_actual_minus_strike=s["mean_actual_minus_strike"],
                          n_actual_missing=s["n_actual_missing"], eligible_n60=s["n"] >= L.MIN_N, selected=(v == chosen),
                          skips=json.dumps(dict(out["vskips"][v]), sort_keys=True)))
    with (L.HERE / f"discovery_versions{suffix}.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(vrows[0]))
        w.writeheader()
        w.writerows(vrows)
    with (L.HERE / f"discovery_bets{suffix}.csv").open("w", newline="") as fh:
        cols = ["stat", "M_star", "beneficiaries", "timing", "game", "team", "ticker", "strike", "bid", "ask", "settle", "nonbinary",
                "fee", "net", "actual", "late", "stars"]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for v in L.VERSIONS:
            for b in out["bets"][v]:
                w.writerow(dict(stat=v[0], M_star=v[1], beneficiaries=v[2], timing=v[3], game=f"{b['game'][0]} {b['game'][1]}",
                                team=b["team"], ticker=b["key"], strike=b["strike"], bid=b["bid"], ask=b["ask"], settle=b["settle"],
                                nonbinary=b["nonbinary"], fee=b["fee"], net=b["net"], actual=b["actual"], late=b["late"],
                                stars=b["stars"]))
    best_any = max((v for v in L.VERSIONS if stats[v]["lb"] is not None), key=lambda v: stats[v]["lb"], default=None)
    res = dict(
        partial=a.partial, runtime_s=round(time.time() - t0), discovery_range=[L.DISC_LO, L.DISC_HI],
        selected_version=dict(zip(["stat", "M_star", "beneficiaries", "timing"], chosen)) if chosen else None,
        selected_stats=stats[chosen] if chosen else None,
        best_lb_any_n=(dict(version=best_any, **stats[best_any]) if best_any else None),
        versions_with_n_ge_60=sum(1 for v in L.VERSIONS if stats[v]["n"] >= L.MIN_N),
        luck_test=luck, counts=dict(out["counts"]), reports_without_parse=reports.missing,
        events_total=len(out["events"]), base_rate=base,
        versions={"|".join(map(str, v)): dict(stats[v], skips=dict(out["vskips"][v])) for v in L.VERSIONS})
    (L.HERE / f"results_discovery{suffix}.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("partial", "selected_version", "selected_stats", "best_lb_any_n", "versions_with_n_ge_60",
                                           "luck_test", "counts", "reports_without_parse", "runtime_s")}, indent=1, default=str))


if __name__ == "__main__":
    main()
