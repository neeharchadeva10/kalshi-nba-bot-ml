"""Strategy simulation on Kalshi (taker entries at the ask), Polymarket as fair value.

(a) gap_close: buy at the Kalshi ask when an after-fee gap opens; sell at the Kalshi bid when
    the gap closes (Kalshi mid reaches the Polymarket price) or at the last pre-kickoff minute.
(b) hold: same entries, held to settlement (no settlement fee).
Entries only count if Kalshi prints within +/- fill_window show >= order_size contracts traded
at or better than our entry price. One open position per game at a time (based on (a)).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from kpm.core.fees import FeeModel


@dataclass(frozen=True)
class Params:
    entry_threshold: float
    ttk_filter: str = "all"          # all | excl_last_90m
    min_volume_1h: float = 0.0
    data: str = "all"                # all | active (Polymarket price changed recently)

    def label(self) -> str:
        return (f"thr={self.entry_threshold:.2f}|ttk={self.ttk_filter}|vol1h>={self.min_volume_1h:g}"
                f"|poly={self.data}")


def fill_volume(tr: pd.DataFrame, t0: int, side: str, entry_yes_bid: float, entry_yes_ask: float,
                window_s: int) -> float:
    """Contracts printed near t0 at a price at least as good as our entry."""
    if tr.empty:
        return 0.0
    w = tr[(tr["minute_ts"] >= t0 - window_s) & (tr["minute_ts"] <= t0 + window_s)]
    if side == "yes":
        return float(w.loc[w["yes_price"] <= entry_yes_ask + 1e-9, "count"].sum())
    return float(w.loc[w["yes_price"] >= entry_yes_bid - 1e-9, "count"].sum())


def _last_valid(arr: np.ndarray, j: int) -> float:
    while j >= 0 and np.isnan(arr[j]):
        j -= 1
    return float(arr[j]) if j >= 0 else np.nan


def simulate_game(signals: pd.DataFrame, frames: dict[int, pd.DataFrame], trades: dict[int, pd.DataFrame],
                  settle: dict[int, float], fee: FeeModel, order_size: float, fill_window_s: int,
                  poly_max_staleness_s: int) -> tuple[list[dict], int]:
    """signals: candidate episode starts for ONE game (both Kalshi markets), any order."""
    out, skipped_fill = [], 0
    busy_until = -1
    for sig in signals.sort_values("t0").itertuples():
        if sig.t0 <= busy_until:
            continue
        g = frames[sig.match_id]
        i0 = sig.start_idx
        bid = g["yes_bid"].to_numpy(dtype=float)
        ask = g["yes_ask"].to_numpy(dtype=float)
        mid = g["k_mid"].to_numpy(dtype=float)
        p = np.where(g["p_age_s"].to_numpy(dtype=float) <= poly_max_staleness_s, g["p_ref"].to_numpy(dtype=float), np.nan)
        ts = g["ts"].to_numpy()
        entry = ask[i0] if sig.side == "yes" else 1.0 - bid[i0]
        fv = fill_volume(trades.get(sig.match_id, pd.DataFrame()), int(ts[i0]), sig.side, bid[i0], ask[i0], fill_window_s)
        if fv < order_size:
            skipped_fill += 1
            continue
        fee_in = fee.per_contract(entry, order_size)

        # (a) exit when the gap closes, else at the last pre-kickoff minute
        n = len(ts)
        j_exit = n - 1
        for j in range(i0 + 1, n):
            if np.isnan(mid[j]) or np.isnan(p[j]):
                continue
            if (sig.side == "yes" and mid[j] >= p[j]) or (sig.side == "no" and mid[j] <= p[j]):
                j_exit = j
                break
        exit_px = _last_valid(bid, j_exit) if sig.side == "yes" else 1.0 - _last_valid(ask, j_exit)
        fee_out = fee.per_contract(exit_px, order_size) if not np.isnan(exit_px) else np.nan
        pnl_a = exit_px - entry - fee_in - fee_out

        # (b) hold to settlement
        v = settle.get(sig.match_id)
        payoff = np.nan if v is None else (v if sig.side == "yes" else 1.0 - v)
        pnl_b = payoff - entry - fee_in

        out.append({
            "match_id": sig.match_id, "side": sig.side, "t0": int(ts[i0]), "entry": entry, "fee_in": fee_in,
            "exit_ts": int(ts[j_exit]), "exit_px": exit_px, "fee_out": fee_out, "closed_by_gap": j_exit < n - 1,
            "hold_min": (ts[j_exit] - ts[i0]) / 60.0, "pnl_gap_close": pnl_a, "pnl_hold": pnl_b,
            "edge0": sig.edge0, "fill_volume": fv,
        })
        busy_until = int(ts[j_exit])
    return out, skipped_fill


def max_drawdown(pnl: np.ndarray) -> float:
    """Largest peak-to-trough drop of cumulative PnL (same units as pnl, >= 0)."""
    if len(pnl) == 0:
        return 0.0
    c = np.concatenate([[0.0], np.cumsum(pnl)])
    return float(np.max(np.maximum.accumulate(c) - c))


def cluster_bootstrap_ci(values: np.ndarray, clusters: np.ndarray, reps: int = 2000, seed: int = 7,
                         alpha: float = 0.05) -> tuple[float, float]:
    """CI for the mean, resampling whole games (trades within a game are correlated)."""
    if len(values) == 0:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    ids = np.unique(clusters)
    groups = [values[clusters == c] for c in ids]
    sums = np.array([g.sum() for g in groups])
    cnts = np.array([len(g) for g in groups])
    means = np.empty(reps)
    for r in range(reps):
        pick = rng.integers(0, len(ids), len(ids))
        means[r] = sums[pick].sum() / cnts[pick].sum()
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def summarize(trades: pd.DataFrame, pnl_col: str, order_size: float, reps: int, min_games: int,
              min_trades: int) -> dict:
    if trades.empty:
        return {"n_trades": 0, "n_games": 0, "trust": "NO DATA"}
    t = trades.sort_values("t0")
    pnl = t[pnl_col].to_numpy(dtype=float)
    ok = ~np.isnan(pnl)
    pnl, games = pnl[ok], t["game_id"].to_numpy()[ok]
    lo, hi = cluster_bootstrap_ci(pnl, games, reps)
    n_games = len(np.unique(games))
    trust = "ok" if (n_games >= min_games and len(pnl) >= min_trades) else "SMALL SAMPLE"
    return {
        "n_trades": int(len(pnl)), "n_games": int(n_games),
        "mean_pnl_cents": 100 * float(pnl.mean()) if len(pnl) else np.nan,
        "ci95_cents": (100 * lo, 100 * hi),
        "total_pnl_usd": float(pnl.sum() * order_size),
        "win_rate": float((pnl > 0).mean()) if len(pnl) else np.nan,
        "max_drawdown_usd": max_drawdown(pnl * order_size),
        "trust": trust,
    }
