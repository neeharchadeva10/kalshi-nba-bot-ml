"""Research log #54: DISCOVERY run only (ET game dates 2025-10-21 .. 2026-01-22). Run ONCE.
.venv/Scripts/python.exe reports/output/nba_line_moves/run_discovery.py"""
import csv
import json
import sys

import numpy as np

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import lm as L
import pipeline as PL


def dist(vals):
    v = np.asarray([x for x in vals if x is not None], float)
    if len(v) == 0:
        return {"n": 0}
    q = np.quantile(v, [0, .05, .1, .25, .5, .75, .9, .95, 1])
    return {"n": int(len(v)), "mean": float(v.mean()), "sd": float(v.std(ddof=1)), "min": float(q[0]), "p05": float(q[1]),
            "p10": float(q[2]), "p25": float(q[3]), "median": float(q[4]), "p75": float(q[5]), "p90": float(q[6]),
            "p95": float(q[7]), "max": float(q[8]), "abs_median": float(np.median(np.abs(v))),
            "share_abs_ge_1.5": float(np.mean(np.abs(v) >= 1.5 - 1e-12)), "share_abs_ge_3.0": float(np.mean(np.abs(v) >= 3.0 - 1e-12)),
            "share_exactly_zero": float(np.mean(v == 0))}


def main():
    out_json = L.HERE / "results_discovery.json"
    recs = PL.build_records(L.DISC_FIRST, L.DISC_LAST, "discovery")
    PL.write_records_csv(recs, L.HERE / "records_discovery.csv")
    res = {"label": "discovery", "first": L.DISC_FIRST, "last": L.DISC_LAST, "n_games_in_window": len(recs)}
    res["game_counts"] = {"M_D_defined": sum(r["M_D"] is not None for r in recs),
                          "move_2h_defined": sum(r["move_2h"] is not None for r in recs),
                          "move_4h_defined": sum(r["move_4h"] is not None for r in recs),
                          "entry_available": sum(r["entry"] is not None for r in recs),
                          "skip_no_main_strike": sum(r["skip"] == "no_main_strike" for r in recs),
                          "skip_no_yes_no_result": sum(r["skip"] == "no_yes_no_result" for r in recs)}

    # ---- versions
    stats, rows, bets_by_v = {}, [], {}
    games = [r for r in recs if r["entry"] is not None]
    gidx = {r["event"]: i for i, r in enumerate(games)}
    for v in L.VERSIONS:
        bets = PL.version_bets(recs, v)
        bets_by_v[v] = bets
        nets = [b["net"] for b in bets]
        s = L.mean_sd_lb(nets) if nets else {"n": 0, "mean": None, "sd": None, "lb": None}
        s["win_rate"] = float(np.mean([n > 0 for n in nets])) if nets else None
        s["mean_net_cents"] = None if s["mean"] is None else 100 * s["mean"]
        s["lb_cents"] = None if s["lb"] is None else 100 * s["lb"]
        s["mean_entry_price"] = float(np.mean([b["price"] for b in bets])) if bets else None
        s["n_yes_side"] = int(sum(b["side"] == "YES" for b in bets))
        stats[v] = s
        rows.append({"version": L.vname(v), "w_h": v[0], "X": v[1], "direction": v[2], "n": s["n"], "win_rate": s["win_rate"],
                     "mean_net": s["mean"], "sd": s["sd"], "lb": s["lb"], "mean_net_cents": s["mean_net_cents"], "lb_cents": s["lb_cents"],
                     "eligible_n_ge_60": s["n"] >= L.MIN_N})
    with open(L.HERE / "discovery_versions.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    sel = L.select_version(stats)
    res["versions"] = {L.vname(v): s for v, s in stats.items()}
    res["selected_version"] = None if sel is None else L.vname(sel)
    res["selected_version_tuple"] = None if sel is None else list(sel)
    res["selected_stats"] = None if sel is None else stats[sel]

    # ---- luck test
    if sel is not None:
        ny = np.array([g["entry"]["YES"] for g in games])
        nn = np.array([g["entry"]["NO"] for g in games])
        sets = {}
        for v, bets in bets_by_v.items():
            m = np.zeros(len(games), bool)
            for b in bets:
                m[gidx[b["event"]]] = True
            sets[v] = m
        res["luck_test"] = L.luck_test(sets, ny, nn, stats[sel]["lb"])
        res["luck_test"]["real_best_lb"] = stats[sel]["lb"]
        res["luck_test"]["rule"] = "FAIL if more than 5% of reps reach the real best LB"
    else:
        res["luck_test"] = {"verdict": "FAIL", "note": "no version had n >= 60"}

    # ---- reported regardless
    res["move_distribution"] = {f"w{w}h": dist([r[f"move_{w}h"] for r in recs]) for w in L.WS}
    cal, drift = {}, {}
    for w in L.WS:
        pair = [(r[f"move_{w}h"], r["final_total"] - r["M_D"]) for r in recs
                if r[f"move_{w}h"] is not None and r["final_total"] is not None and r["M_D"] is not None]
        x, y = ([p[0] for p in pair], [p[1] for p in pair]) if pair else ([], [])
        cal[f"w{w}h"] = {"y": "final_total - M(D)", "x": "Move", **L.ols_slope(x, y),
                         "note": "slope 0 = line at D is unbiased given the move; slope>0 = move continues (follow); <0 = reverses (fade)"}
        dp = [(r[f"move_{w}h"], r["M_T"] - r["M_D"]) for r in recs
              if r[f"move_{w}h"] is not None and r["M_T"] is not None and r["M_D"] is not None]
        dx, dy = ([p[0] for p in dp], [p[1] for p in dp]) if dp else ([], [])
        d = {"y": "M(T) - M(D)", "x": "Move", **L.ols_slope(dx, dy)}
        d["n_move_defined"] = int(sum(r[f"move_{w}h"] is not None for r in recs))
        d["n_M_T_missing_given_move"] = d["n_move_defined"] - len(dp)
        d["by_trigger"] = {}
        for X in L.XS:
            sel_ = [(m, dd) for m, dd in dp if abs(m) >= X - 1e-12]
            if sel_:
                signed = [np.sign(m) * dd for m, dd in sel_]
                d["by_trigger"][f"|Move|>={X}"] = {"n": len(sel_), "mean_signed_drift_in_move_direction": float(np.mean(signed)),
                                                   "median_signed_drift": float(np.median(signed)),
                                                   "share_continues": float(np.mean([s > 0 for s in signed])),
                                                   "share_reverses": float(np.mean([s < 0 for s in signed])),
                                                   "share_unchanged": float(np.mean([s == 0 for s in signed])),
                                                   "mean_abs_move": float(np.mean([abs(m) for m, _ in sel_]))}
        drift[f"w{w}h"] = d
    res["calibration_final_minus_MD_on_Move"] = cal
    res["drift_D_to_T_vs_Move"] = drift
    out_json.write_text(json.dumps(res, indent=1, default=str))

    print("games", len(recs), res["game_counts"])
    print("version                    n   win%   mean_c   lb_c")
    for r in rows:
        f = lambda x, k: "-" if x is None else f"{x:{k}}"
        print(f"{r['version']:<22} {r['n']:>5} {f(None if r['win_rate'] is None else 100*r['win_rate'], '6.1f')} "
              f"{f(r['mean_net_cents'], '8.2f')} {f(r['lb_cents'], '8.2f')}")
    print("selected", res["selected_version"], "luck", res["luck_test"])
    print("calibration", json.dumps(cal, default=str))
    print("drift", json.dumps(drift, default=str))
    print("dist", json.dumps(res["move_distribution"], default=str))


if __name__ == "__main__":
    main()
