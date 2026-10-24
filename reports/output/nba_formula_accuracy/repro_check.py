"""Research log #56: does the season-generalised team grid reproduce #42's frozen winner on 2025-26?
Compares fa_lib.build_world('2025-26') + project_one against #42's own grid_run.load_world(None) +
fit_world_constants + project_one for every 2025-26 regular-season game (hoopR games, T = tip).  Also compares the
constants and the #37 baseline against lib.build_ratings.  Uses 2025-26 box scores only as INPUT to this identity check
(no outcomes are read: the check compares projections with projections)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fa_lib as fa  # noqa: E402
import grid_lib as gl  # noqa: E402


def run(season="2025-26", spec=None, limit=None):
    import grid_run as gr
    fz = json.loads((fa.GRID / "winner_frozen.json").read_text())
    spec = tuple(spec or fz["spec"])
    t0 = time.time()
    mine = fa.build_world(season)
    games = fa.games_frame(mine)
    if limit:
        games = games.iloc[:limit].reset_index(drop=True)
    p_mine = gl.project_one(mine.season, games, mine.consts, spec)
    w = gr.load_world(None)
    c42 = gr.fit_world_constants(w)
    p42 = gl.project_one(w.season, games, c42, spec)
    out = {"season": season, "spec": list(spec), "n_games": int(len(games)),
           "max_abs_diff_projection": float(np.abs(p_mine - p42).max()),
           "max_abs_diff_consts": {
               "c_b2b": float(np.abs(mine.consts.c_b2b - c42.c_b2b).max()),
               "e_cat": float(np.abs(mine.consts.e_cat - c42.e_cat).max()),
               "b": float(np.abs(mine.consts.b - c42.b).max())},
           "ff_coef_max_diff": float(np.abs(mine.ff_coef - w.ff_coef).max()),
           "frozen_constants_pair_diff": float(abs(mine.consts.pair(spec[0], spec[2])["b"] - fz["constants_pair"]["b"])),
           "runtime_s": time.time() - t0}
    # #37 baseline vs #37's own build_ratings (prior season = 2024-25 file 2025)
    import pandas as pd
    rt = fa.lib.build_ratings(pd.read_parquet(fa.BOX / "team_box_2025.parquet"))
    b37 = fa.baseline_37(mine, games)
    ref = np.array([fa.lib.project_total(rt[a], rt[h])[2] for a, h in zip(games.away, games.home)])
    out["baseline37_max_diff_vs_lib_build_ratings"] = float(np.nanmax(np.abs(b37 - ref)))
    out["baseline37_nan_in_lib_build_ratings"] = int(np.isnan(ref).sum())
    return out


if __name__ == "__main__":
    res = run(limit=int(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(json.dumps(res, indent=1))
    if len(sys.argv) == 1:
        (HERE / "repro_check.json").write_text(json.dumps(res, indent=1))
