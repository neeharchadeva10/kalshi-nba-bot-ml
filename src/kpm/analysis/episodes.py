"""Gap episodes: maximal runs of consecutive grid minutes with after-fee edge > threshold."""
from __future__ import annotations

import numpy as np
import pandas as pd

SIDES = ("yes", "no")


def find_episodes(ts: np.ndarray, edge: np.ndarray, threshold: float, grid_s: int = 60) -> list[tuple[int, int]]:
    """Return (start_idx, end_idx) inclusive for runs where edge > threshold on a contiguous grid.

    A NaN edge (stale/missing data) or a gap in the grid ends a run.
    """
    above = np.nan_to_num(np.asarray(edge, dtype=float), nan=-np.inf) > threshold
    n = len(above)
    if n == 0:
        return []
    ts = np.asarray(ts)
    brk = np.ones(n, dtype=bool)                       # brk[i]: grid not contiguous between i-1 and i
    brk[1:] = (ts[1:] - ts[:-1]) != grid_s
    prev_above = np.r_[False, above[:-1]]
    next_above = np.r_[above[1:], False]
    next_brk = np.r_[brk[1:], True]
    starts = np.flatnonzero(above & (brk | ~prev_above))
    ends = np.flatnonzero(above & (next_brk | ~next_above))
    return list(zip(starts.tolist(), ends.tolist()))


def episodes_for_match(g: pd.DataFrame, thresholds: list[float], grid_s: int = 60) -> pd.DataFrame:
    """g: one match's gap rows sorted by ts. Returns one row per (threshold, side, episode)."""
    rows = []
    ts = g["ts"].to_numpy()
    for side in SIDES:
        edge = g[f"edge_{side}"].to_numpy(dtype=float)
        for thr in thresholds:
            for s, e in find_episodes(ts, edge, thr, grid_s):
                rows.append({
                    "match_id": int(g["match_id"].iloc[0]), "side": side, "threshold": thr,
                    "start_idx": s, "end_idx": e, "t0": int(ts[s]), "t_end": int(ts[e]),
                    "duration_min": (e - s + 1) * grid_s / 60.0,
                    "edge0": float(edge[s]), "edge_max": float(np.nanmax(edge[s:e + 1])),
                    "ends_at_kickoff": e == len(ts) - 1,
                })
    return pd.DataFrame(rows)
