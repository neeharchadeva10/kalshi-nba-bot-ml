"""Research log #56 selection stage.  Selection seasons 2022-23 and 2023-24, regular season ONLY: the seasons are fixed
here, the games are filtered (and no outcome column exists) before anything is scored.

  .venv/Scripts/python.exe reports/output/nba_formula_accuracy/run_select.py [--preview]

Outputs (this folder): results_select.json (everything), frozen.json + frozen.sha256 (full run) or
selection_preview.json (--preview, or automatically when player_model.py is missing: nothing is frozen).
Refuses to overwrite a frozen selection."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fa_lib as fa  # noqa: E402
import grid_lib as gl  # noqa: E402

TOP_N = 20


def season_stage(S, reader=None, closing=None, keep_P=True):
    """One selection season: world (constants from S-1 only), games (no outcomes), then outcomes/lines attached, all 26,880
    projections, per-formula sums.  Returns dict."""
    assert S in fa.SELECTION, f"{S} is not a selection season"
    world = fa.build_world(S, reader)
    games = fa.games_frame(world)                                 # filtered to the season, no outcomes
    actual = fa.actuals_frame(world).reindex(games.game_id.values).values.astype(float)
    cl = closing if closing is not None else fa.load_closing()
    line = cl.reindex(games.game_id.values).values.astype(float)
    has_line = np.isfinite(line)
    assert np.isfinite(actual).all(), "missing hoopR final totals"
    P = gl.build_projections(world.season, games, world.consts)
    assert P.shape == (gl.N_FORMULAS, len(games)) and np.isfinite(P).all()
    sums = fa.season_sums(P, actual, has_line)
    b37 = fa.baseline_37(world, games)
    out = {"season": S, "world": world, "games": games, "actual": actual, "line": line, "has_line": has_line,
           "sums": sums, "b37": b37, "default": P[fa.DEFAULT_FLAT].copy(),
           "P": P.astype(np.float32) if keep_P else None, "const_note": world.const_note}
    del P
    return out


def pooled_table(stages, flats):
    pooled = fa.pool_sums([s["sums"] for s in stages])
    rows = []
    for rk, f in enumerate(flats, 1):
        spec = gl.spec_from_flat(int(f))
        rows.append({"rank": rk, "flat": int(f), "names": {k: str(v) for k, v in gl.spec_names(spec).items()},
                     "mae": float(pooled["mae"][f]), "rmse": float(pooled["rmse"][f]), "bias": float(pooled["bias"][f]),
                     "mae_on_line_games": float(pooled["mae_line_games"][f]),
                     "mae_by_season": {s["season"]: float(s["sums"]["sabs"][f] / s["sums"]["n"]) for s in stages}})
    return pooled, rows


def baseline_rows(stages, pooled):
    actual = np.concatenate([s["actual"] for s in stages])
    line = np.concatenate([s["line"] for s in stages])
    hl = np.isfinite(line)
    b37 = np.concatenate([s["b37"] for s in stages])
    dflt = np.concatenate([s["default"] for s in stages])
    ranks = (pooled["mae"] < pooled["mae"][fa.DEFAULT_FLAT]).sum() + 1
    out = {"b37_prior_season_only": fa.mae_rmse_bias(b37, actual),
           "b37_on_line_games": fa.mae_rmse_bias(b37[hl], actual[hl]),
           "b38_default_k20": fa.mae_rmse_bias(dflt, actual),
           "b38_default_k20_on_line_games": fa.mae_rmse_bias(dflt[hl], actual[hl]),
           "b38_default_k20_rank_of_26880": int(ranks), "b38_default_flat": int(fa.DEFAULT_FLAT),
           "sportsbook_close": fa.mae_rmse_bias(line[hl], actual[hl]),
           "sportsbook_close_by_season": {s["season"]: fa.mae_rmse_bias(s["line"][s["has_line"]], s["actual"][s["has_line"]])
                                          for s in stages},
           "n_games": int(len(actual)), "n_games_with_line": int(hl.sum()),
           "line_coverage_by_season": {s["season"]: [int(s["has_line"].sum()), int(len(s["has_line"]))] for s in stages}}
    return out


def player_stage(stages, pm):
    """Pooled MAE of the player model's versions on the selection games (no outcomes passed to the model)."""
    actual = np.concatenate([s["actual"] for s in stages])
    projs = []
    for v in pm.VERSIONS:
        per = []
        for s in stages:
            g = s["games"][["game_id", "date", "T", "away", "home", "season"]].copy()
            p = np.asarray(pm.project(s["season"], g, dict(v)), float)
            assert p.shape == (len(g),), f"player_model.project returned {p.shape}"
            per.append(p)
        projs.append(np.concatenate(per))
    projs = np.array(projs)
    ok = np.isfinite(projs).all(axis=0)
    rows = []
    for i, (v, p) in enumerate(zip(pm.VERSIONS, projs)):
        m = fa.mae_rmse_bias(p[ok], actual[ok])
        rows.append({"version_index": i, "version": dict(v), **m, "n_nonfinite_games": int((~ok).sum())})
    best = int(np.argmin([r["mae"] for r in rows]))
    return rows, best, projs, ok


def form_block(name, family, proj, shift, actual, line):
    """Games with a non-finite projection (player model: no rotation) are dropped for that form and counted."""
    fin = np.isfinite(proj)
    proj, actual, line = proj[fin], actual[fin], line[fin]
    hl = np.isfinite(line)
    pr = proj + shift
    tab, best = fa.select_threshold(pr[hl], line[hl], actual[hl])
    return {"family": family, "shift": float(shift), "games_dropped_nonfinite": int((~fin).sum()),
            "accuracy_all_games": fa.mae_rmse_bias(pr, actual),
            "threshold_table": tab, "best": best, "T": (best["T"] if best else None)}


def core_select(reader=None, closing=None, pm=None, thresholds_only=False):
    t0 = time.time()
    stages = [season_stage(S, reader, closing) for S in fa.SELECTION]
    pooled, _ = pooled_table(stages, [0])
    order = fa.rank_formulas(pooled)
    _, top_rows = pooled_table(stages, order[:TOP_N])
    base = baseline_rows(stages, pooled)
    actual = np.concatenate([s["actual"] for s in stages])
    line = np.concatenate([s["line"] for s in stages])
    best_flat = int(order[0])
    # chosen team formula: recompute with project_one (float64) and check it against the grid row
    spec = gl.spec_from_flat(best_flat)
    team_proj = np.concatenate([gl.project_one(s["world"].season, s["games"], s["world"].consts, spec) for s in stages])
    grid_row = np.concatenate([s["P"][best_flat] for s in stages]).astype(float)
    assert np.abs(team_proj - grid_row).max() < 1e-3, "project_one and build_projections disagree"
    forms = {}
    shift_t = fa.lean_shift(team_proj, actual)
    forms["team_raw"] = form_block("team_raw", "team", team_proj, 0.0, actual, line)
    forms["team_corr"] = form_block("team_corr", "team", team_proj, shift_t, actual, line)
    player = None
    if pm is not None:
        prow, pbest, pprojs, ok = player_stage(stages, pm)
        pp = pprojs[pbest]
        shift_p = fa.lean_shift(pp[ok], actual[ok])
        forms["player_raw"] = form_block("player_raw", "player", pp, 0.0, actual, line)
        forms["player_corr"] = form_block("player_corr", "player", pp, shift_p, actual, line)
        player = {"version_index": pbest, "version": dict(pm.VERSIONS[pbest]), "shift": shift_p, "all_versions": prow}
    primary = fa.choose_primary(forms)
    res = {"selection_seasons": list(fa.SELECTION), "constants_notes": {s["season"]: s["const_note"] for s in stages},
           "n_games": int(len(actual)), "baselines": base,
           "team": {"flat": best_flat, "spec": [int(x) for x in spec],
                    "names": {k: str(v) for k, v in gl.spec_names(spec).items()},
                    "selection_accuracy": fa.mae_rmse_bias(team_proj, actual), "shift": shift_t,
                    "constants_pair_last_season": stages[-1]["world"].consts.pair(spec[0], spec[2])},
           "team_top20": top_rows, "player": player, "forms": forms, "primary": primary,
           "n_versions_note": "26,880 team + 8 player + 2x2 corrections + 4 forms x 4 thresholds",
           "runtime_s": time.time() - t0}
    return res


def frozen_from(res):
    keep = {"selection_seasons": res["selection_seasons"], "team": {k: res["team"][k] for k in ("flat", "spec", "names", "shift")},
            "player": None if res["player"] is None else {k: res["player"][k] for k in ("version_index", "version", "shift")},
            "forms": {k: {"family": v["family"], "shift": v["shift"], "T": v["T"],
                          "selection_best": v["best"]} for k, v in res["forms"].items()},
            "player_model_sha256": res.get("player_model_sha256"),
            "primary": res["primary"], "preregistration": "reports/output/nba_formula_accuracy/PREREGISTRATION.md",
            "constants_rule": "each confirmation season S refits every constant on S-1 only (build_world)",
            "threshold_rule": "disagreement vs the sportsbook consensus close; pushes excluded; Wilson 95% LB"}
    return keep


def main(argv):
    preview = "--preview" in argv
    pm = fa.load_player_model()
    if pm is None:
        print("player_model.py not found: running the TEAM family only; nothing will be frozen (selection_preview.json).")
        preview = True
    fa.check_select_gates(HERE, preview)
    res = core_select(pm=pm)
    res["preview"] = preview
    res["player_model_sha256"] = fa.sha256_file(HERE / "player_model.py") if pm is not None else None
    res["line_rows_used"] = int(len(fa.load_closing()))
    (HERE / ("selection_preview.json" if preview else "results_select.json")).write_text(
        json.dumps(res, indent=1, default=str))
    if not preview:
        fz = HERE / "frozen.json"
        fz.write_text(json.dumps(frozen_from(res), indent=1, sort_keys=True))
        h = fa.sha256_file(fz)
        (HERE / "frozen.sha256").write_text(h + "  frozen.json\n")
        print("frozen.json SHA-256:", h)
    print_report(res)
    return res


def print_report(res):
    b = res["baselines"]
    print(f"selection games {res['n_games']} (with a sportsbook close: {b['n_games_with_line']}); runtime {res['runtime_s']:.0f}s")
    print("constants:", json.dumps(res["constants_notes"]))
    print("sportsbook close MAE/RMSE/bias:", {k: round(v, 3) for k, v in b["sportsbook_close"].items()})
    print("#37 prior-only:", {k: round(v, 3) for k, v in b["b37_prior_season_only"].items()})
    print("#38 default k=20:", {k: round(v, 3) for k, v in b["b38_default_k20"].items()}, "rank", b["b38_default_k20_rank_of_26880"])
    print("TOP 20 team formulas by pooled selection MAE (MAE on line-games in brackets):")
    for r in res["team_top20"]:
        n = r["names"]
        print(f"{r['rank']:3d} flat {r['flat']:5d} {n['A']:5s} {n['B']:7s} {n['C']:10s} k{n['D']:>2s} sh-{n['E']:3s} {n['F']:4s} "
              f"split-{n['G']:3s} shr-{n['H']:3s} MAE {r['mae']:.3f} [{r['mae_on_line_games']:.3f}] RMSE {r['rmse']:.3f} bias {r['bias']:+.3f}")
    if res["player"]:
        for r in res["player"]["all_versions"]:
            print("player version", r["version_index"], r["version"], f"MAE {r['mae']:.3f} RMSE {r['rmse']:.3f} bias {r['bias']:+.3f}")
    for k, f in res["forms"].items():
        print(k, "shift", round(f["shift"], 3), "MAE", round(f["accuracy_all_games"]["mae"], 3))
        for t in f["threshold_table"]:
            print("    T", t["T"], "n", t["n"], "rate", None if t["n"] == 0 else round(t["rate"], 4), "LB", None if t["n"] == 0 else round(t["lb"], 4))
        print("    best:", None if f["best"] is None else (f["best"]["T"], round(f["best"]["lb"], 4)))
    print("primary:", res["primary"])


if __name__ == "__main__":
    main(sys.argv[1:])
