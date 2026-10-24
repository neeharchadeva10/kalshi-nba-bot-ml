"""Research log #65: reproduction gates (pre-registration "Double checks" 2) and the discovery-only multi-source checks (4).

Silent: prints PASS/FAIL and max abs differences only (no outcome-based statistic).  Writes main_out/gates.json.
Run:  .venv/Scripts/python.exe reports/output/nba_refined_totals/main/gates.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rt_lib as R  # noqa: E402

R._grid_path()
import fa_lib as fa  # noqa: E402
import grid_lib as gl  # noqa: E402

E1_MAE_REF, E1_N_REF = 14.79634, 4923
E3_MAE_REF_3DP = 14.903
TOL = 1e-9


def gate_f42(preds):
    import repro_check
    res = repro_check.run()                                     # fa.build_world('2025-26') vs #42's own grid_run world, every game
    stored = json.loads((R.FA_DIR / "repro_check.json").read_text())
    d1 = abs(res["max_abs_diff_projection"] - stored["max_abs_diff_projection"])
    # my table's 2025-26 F42 column vs #42's own projections (grid_run.load_world + fit_world_constants)
    import grid_run as gr
    spec = tuple(json.loads((R.GRID_DIR / "winner_frozen.json").read_text())["spec"])
    mine = fa.build_world("2025-26")
    g = fa.games_frame(mine)
    w = gr.load_world(None)
    p42 = gl.project_one(w.season, g, gr.fit_world_constants(w), spec)
    col = preds.set_index("game_id").f42.reindex(g.game_id.astype("int64").values).values
    d2 = float(np.nanmax(np.abs(col - p42)))
    ok = bool(res["max_abs_diff_projection"] <= TOL and d1 <= TOL and d2 <= TOL and np.isfinite(col).all())
    return {"gate": "F42 reproduces #56 repro_check (#42 on 2025-26)", "pass": ok, "max_abs_diff": max(d1, d2, res["max_abs_diff_projection"]),
            "n_games": int(len(g))}


def gate_e1(preds):
    p = preds[preds.season.isin(["2022-23", "2023-24", "2024-25", "2025-26"])]
    mae = float(np.abs(p.e1 - p.actual_total).mean())
    ok = bool(len(p) == E1_N_REF and abs(round(mae, 5) - E1_MAE_REF) < 1e-12 and p.e1.notna().all())
    return {"gate": "E1 2022-26 total MAE = 14.79634 on 4,923 games", "pass": ok, "max_abs_diff": abs(mae - E1_MAE_REF), "n_games": int(len(p))}


def gate_e3(preds):
    p = preds[preds.season.isin(R.H2_SB)]
    mae = float(np.abs(p.e3 - p.actual_total).mean())
    ref = json.loads((R.E3_STUDY / "results_test.json").read_text())["metrics"]["E3"]["total_mae"]
    ok = bool(round(mae, 3) == E3_MAE_REF_3DP and abs(mae - ref) < 1e-9 and p.e3.notna().all())
    return {"gate": "frozen E3 2024-26 total MAE = 14.903 (results_test.json)", "pass": ok, "max_abs_diff": abs(mae - ref), "n_games": int(len(p))}


def gate_kalshi(ev):
    ver = R.parse_version("A|E1|none|G4|none")
    mine = R.part_a_kalshi(ev, ver).set_index("game_id").net
    rec = pd.read_csv(R.OUT / "nba_model_vs_lines" / "records.csv")
    rec = rec[rec.season == "2025-26"]
    ref = rec[rec.bet_g4 == True].set_index("game_id").net_g4            # noqa: E712
    same_set = set(mine.index) == set(ref.index)
    j = pd.concat([mine, ref], axis=1, keys=["mine", "ref"]).dropna()
    diff = float(np.abs(j.mine - j.ref).max()) if len(j) else float("nan")
    e1_diff = float(np.abs(ev.set_index("game_id").e1.reindex(rec.game_id.values).values - rec.e1.values).max())
    ok = bool(same_set and diff <= TOL and len(ref) > 0)
    return {"gate": "H2-K machinery fed E1 at G=4 reproduces #60 records.csv net_g4 per game", "pass": ok, "max_abs_diff": diff,
            "same_bet_set": bool(same_set), "n_bets": int(len(ref)), "max_abs_diff_e1_vs_records": e1_diff}


def multi_source(ev_all, bfeat):
    """Discovery seasons only (2021-22..2023-24): share of box-flavor absentees listed OUT/DOUBTFUL at T-60 and at the last report before tip."""
    b = bfeat[bfeat.season.isin(R.D_B)]
    out = {}
    for M in R.B_MS:
        n = int(b[f"boxabs_n_M{M}"].sum())
        out[f"M{M}"] = {"n_box_absentees": n, "share_listed_out_doubtful_t60": float(b[f"boxabs_listed_t60_M{M}"].sum() / n),
                        "share_listed_out_doubtful_last_before_tip": float(b[f"boxabs_listed_tip_M{M}"].sum() / n)}
    k = ev_all[ev_all.season == "2025-26"]
    m = k.k_median.notna() & k.close_line.notna()
    out["kalshi_T-1h_median_vs_close_2025_26"] = {"n": int(m.sum()), "median_abs_line_diff": float(np.median(np.abs(k.k_median[m] - k.close_line[m])))}
    return out


def main():
    preds = pd.read_parquet(R.MAIN_OUT / "preds.parquet")
    ev = R.load_tables()
    bfeat = pd.read_parquet(R.MAIN_OUT / "b_features.parquet")
    gates = [gate_f42(preds), gate_e1(preds), gate_e3(preds), gate_kalshi(ev)]
    for g in gates:
        print(("PASS" if g["pass"] else "FAIL"), g["gate"], "| max abs diff:", g["max_abs_diff"], "|", {k: v for k, v in g.items() if k not in ("gate", "pass", "max_abs_diff")})
    ms = multi_source(ev, bfeat)
    print("multi-source (discovery seasons only):", json.dumps(ms, indent=1))
    (R.MAIN_OUT / "gates.json").write_text(json.dumps({"gates": gates, "multi_source": ms}, indent=1, default=str))
    if not all(g["pass"] for g in gates):
        sys.exit(1)


if __name__ == "__main__":
    main()
