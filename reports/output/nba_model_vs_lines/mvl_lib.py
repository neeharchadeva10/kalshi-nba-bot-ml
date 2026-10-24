"""Research log #60 library (pure numpy/pandas; no data loading).  See PREREGISTRATION.md.

A: side rate of E1 vs the sportsbook close.   B: Kalshi bets at the main strike.   C: blend MAE vs close MAE."""
from __future__ import annotations

import numpy as np

SEED = 20261023
N_BOOT = 10000
N_COIN = 500
BREAK_EVEN = 110 / 210
MIN_N = 150
GAPS = (4, 6, 8)
FIXED_W = (0.1, 0.2, 0.3)
W_GRID = tuple(round(i * 0.05, 2) for i in range(0, 21))     # 0.00 .. 1.00 step 0.05
PRIMARY_G = 4
PRIMARY_W = 0.2


def wilson(wins, n, z=1.96):
    if n == 0:
        return float("nan"), float("nan")
    p = wins / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return float(c - h), float(c + h)


def side_table(e1, line, actual, g):
    """Side = over if e1 > line, under if e1 < line; gap >= g; pushes (actual == line) excluded."""
    e1, line, actual = (np.asarray(x, float) for x in (e1, line, actual))
    d = e1 - line
    m = (np.abs(d) >= g) & (d != 0)
    push = m & (actual == line)
    m = m & ~push
    win = np.where(d > 0, actual > line, actual < line)
    n, w = int(m.sum()), int((win & m).sum())
    lo, hi = wilson(w, n)
    return {"g": g, "n": n, "wins": w, "rate": (w / n if n else float("nan")), "lb": lo, "ub": hi,
            "pushes": int(push.sum()), "pass": bool(n >= MIN_N and np.isfinite(lo) and lo > BREAK_EVEN)}


def blend(e1, line, w):
    return w * np.asarray(e1, float) + (1.0 - w) * np.asarray(line, float)


def mae(pred, actual):
    return float(np.abs(np.asarray(pred, float) - np.asarray(actual, float)).mean())


def fit_w(e1_val, line_val, act_val, grid=W_GRID):
    """MAE-minimising w on `grid`, using ONLY the validation arrays passed in.  Ties -> the smaller w."""
    maes = [mae(blend(e1_val, line_val, w), act_val) for w in grid]
    j = int(np.argmin(maes))                      # argmin returns the first (smallest w) minimum
    return float(grid[j]), {float(w): m for w, m in zip(grid, maes)}


def boot_ci(d, n_boot=N_BOOT, seed=SEED, chunk=500):
    """Game bootstrap of mean(d): (mean, 2.5 pct, 97.5 pct).  Deterministic given the seed."""
    d = np.asarray(d, float)
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot)
    for lo in range(0, n_boot, chunk):
        k = min(chunk, n_boot - lo)
        means[lo:lo + k] = d[rng.integers(0, len(d), size=(k, len(d)))].mean(axis=1)
    return float(d.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def blend_vs_close(e1, line, actual, w):
    """Blend MAE, close MAE, paired bootstrap of (|blend err| - |close err|); pass iff the CI hi < 0."""
    b = blend(e1, line, w)
    d = np.abs(b - actual) - np.abs(np.asarray(line, float) - actual)
    m, lo, hi = boot_ci(d)
    return {"w": w, "n": int(len(d)), "blend_mae": mae(b, actual), "close_mae": mae(line, actual), "diff": m,
            "ci95": [lo, hi], "pass": bool(hi < 0)}


# ------------------------------------------------------------------ B: Kalshi bets
def kalshi_bets(e1, med, bt, g):
    """Per game (aligned arrays/DataFrame): if |e1 - median| >= g, buy E1's side (over if e1 > median) at the main strike.
    bt: grid_bets.main_strike_table columns valid, K, net_over/under, paid_over/under, win_over/under.
    Returns a DataFrame of the bet games with idx (position in bt), side, paid, fee-inclusive net, win."""
    e1, med = np.asarray(e1, float), np.asarray(med, float)
    d = e1 - med
    ok = bt.valid.values & np.isfinite(d) & (np.abs(np.nan_to_num(d)) >= g) & (np.nan_to_num(d) != 0)
    idx = np.flatnonzero(ok)
    over = d[idx] > 0
    pick = lambda a, b: np.where(over, bt[a].values[idx], bt[b].values[idx])      # noqa: E731
    return {"idx": idx, "over": over, "gap": d[idx], "e1": e1[idx], "net": pick("net_over", "net_under").astype(float),
            "paid": pick("paid_over", "paid_under").astype(float), "win": pick("win_over", "win_under").astype(float),
            "K": bt.K.values[idx].astype(float)}


def coin_flip(bets, bt, n=N_COIN, seed=SEED):
    """Same bet games and strikes, random side per game, same costs; 500 copies.  Share of copies with mean net >= actual."""
    idx = bets["idx"]
    no, nu = bt.net_over.values[idx].astype(float), bt.net_under.values[idx].astype(float)
    rng = np.random.default_rng(seed)
    side = rng.integers(0, 2, (n, len(idx))).astype(bool)
    means = np.where(side, no, nu).mean(axis=1)
    actual = float(bets["net"].mean())
    return {"actual_mean": actual, "copies_mean": float(means.mean()), "frac_copies_ge_actual": float((means >= actual).mean()),
            "copies": n}


def b_summary(bets, bt, g):
    n = int(len(bets["idx"]))
    if n < 2:
        return {"g": g, "n": n, "pass": False}
    mean, lo, hi = boot_ci(bets["net"])
    coin = coin_flip(bets, bt)
    out = {"g": g, "n": n, "win_rate": float(bets["win"].mean()), "avg_paid": float(bets["paid"].mean()), "mean_net": mean,
           "boot_ci95": [lo, hi], "coin_flip": coin, "over_share": float(bets["over"].mean())}
    out["e1_on_other_side_of_strike"] = int(np.sum(np.where(bets["over"], bets["e1"] < bets["K"], bets["e1"] > bets["K"])))
    out["pass"] = bool(n >= MIN_N and mean > 0 and lo > 0 and coin["frac_copies_ge_actual"] <= 0.05)
    return out


def overall(a_primary, b_primary, c_primary, c_fit):
    return {"E1_gives_a_betting_edge": bool(a_primary and b_primary),
            "E1_improves_on_line_accuracy": bool(c_primary or c_fit),
            "accuracy_claim_chances": 2}


def repro_ok(recomputed, reference, tol=1e-9):
    return bool(abs(float(recomputed) - float(reference)) <= tol)
