"""Reproduction check: fa_ext (extended to 2008-09) vs #56's own fa_lib.build_world + grid_lib.project_one, seasons 2022-23..2025-26.
Writes repro_f8_a.json (max abs differences; must all be exactly 0.0).  No outcomes are read."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fa_ext as fx  # noqa: E402
import lea_lib as L  # noqa: E402

fa, gl = fx.fa, fx.gl


def main():
    res = {}
    for S in ("2022-23", "2023-24", "2024-25", "2025-26"):
        t0 = time.time()
        w56 = fa.build_world(S)
        g56 = fa.games_frame(w56)
        p56 = gl.project_one(w56.season, g56, w56.consts, gl.spec_from_flat(7791)) + 0.0
        pe, note = fx.project_season(S)
        m = g56[["game_id"]].merge(pe, on="game_id", how="outer", indicator=True)
        both = m[m._merge == "both"]
        a = dict(zip(g56.game_id, p56))
        d = np.abs(np.array([a[g] for g in both.game_id]) - both.proj.values)
        res[S] = {"games_56": int(len(g56)), "games_ext": int(len(pe)), "games_both": int(len(both)),
                  "max_abs_diff": float(d.max()), "consts_note": note, "seconds": round(time.time() - t0, 1)}
        print(S, res[S], flush=True)
    (L.STUDY / "repro_f8_a.json").write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
