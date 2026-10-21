"""After a gap opens, which venue moves toward the other?

At episode start t0 the raw gap is g0 = p_ref - k_mid (positive for YES-side gaps, negative
for NO-side). With s = sign(g0), at t0 + h:
  kalshi_move = s * (k_mid(t0+h) - k_mid(t0))   > 0 means Kalshi moved toward Polymarket
  poly_move   = -s * (p_ref(t0+h) - p_ref(t0))  > 0 means Polymarket moved toward Kalshi
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def classify(kalshi_move: float, poly_move: float, tol: float) -> str:
    if np.isnan(kalshi_move) or np.isnan(poly_move):
        return "no_data"
    k, p = kalshi_move >= tol, poly_move >= tol
    if k and p:
        return "both"
    if k:
        return "kalshi_toward_poly"
    if p:
        return "poly_toward_kalshi"
    return "neither"


def measure(g: pd.DataFrame, ep: pd.DataFrame, horizons_min: list[int], tol: float,
            poly_max_staleness_s: int, grid_s: int = 60) -> pd.DataFrame:
    """g: one match's gap rows (sorted, index 0..n-1). ep: that match's episodes."""
    if ep.empty:
        return ep
    k_mid = g["k_mid"].to_numpy(dtype=float)
    p = g["p_ref"].to_numpy(dtype=float)
    p_age = g["p_age_s"].to_numpy(dtype=float)
    p = np.where(p_age <= poly_max_staleness_s, p, np.nan)
    last = len(g) - 1
    ts = g["ts"].to_numpy()
    out = ep.copy()
    i0 = out["start_idx"].to_numpy()
    g0 = p[i0] - k_mid[i0]
    s = np.sign(g0)
    out["gap0"] = g0
    targets = {f"{h}m": i0 + h * 60 // grid_s for h in horizons_min}
    targets["kickoff"] = np.full_like(i0, last)
    for name, idx in targets.items():
        valid = idx <= last
        j = np.minimum(idx, last)
        # grid gaps would make index offsets wrong; require exact elapsed time for fixed horizons
        if name != "kickoff":
            valid &= (ts[j] - ts[i0]) == (idx - i0) * grid_s
        km = np.where(valid, s * (k_mid[j] - k_mid[i0]), np.nan)
        pm = np.where(valid, -s * (p[j] - p[i0]), np.nan)
        out[f"kalshi_move_{name}"] = km
        out[f"poly_move_{name}"] = pm
        with np.errstate(divide="ignore", invalid="ignore"):
            out[f"closed_frac_{name}"] = np.where(valid, (km + pm) / np.abs(g0), np.nan)
        out[f"class_{name}"] = [classify(a, b, tol) if v else "beyond_kickoff"
                                for a, b, v in zip(km, pm, valid)]
    return out
