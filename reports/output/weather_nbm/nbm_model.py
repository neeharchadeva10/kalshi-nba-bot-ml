"""NBM -> bracket probability model for pre-registration #30 (pure functions, unit-tested in test_model.py)."""
from __future__ import annotations

import bisect
import math
from datetime import timedelta

import numpy as np
from scipy.optimize import minimize
from scipy.special import ndtr

WINDOW = 60          # previous event days used per station
SHRINK_K = 30        # weight n / (n + 30)
MIN_STATION_DAYS = 15
SIGMA_FLOOR = 1.0
B_MAX = 15.0         # search bounds (interpretation, documented in README)
K_MIN, K_MAX = 0.05, 10.0
LL_FLOOR = 1e-12


# ------------------------------------------------------------------ probability math
def sigma_of(k, xnd):
    return np.maximum(SIGMA_FLOOR, k * np.asarray(xnd, dtype=float))


def bracket_prob(a, b, mu, sigma):
    """P(integer high in [a, b]) = Phi((b+0.5-mu)/s) - Phi((a-0.5-mu)/s); a may be -inf, b may be +inf."""
    hi = ndtr((b + 0.5 - mu) / sigma) if np.isfinite(b) else 1.0
    lo = ndtr((a - 0.5 - mu) / sigma) if np.isfinite(a) else 0.0
    return hi - lo


def market_interval(stype, floor, cap):
    """Integer-degree interval a Kalshi high-temp market pays on. between [fl, cap]; greater F = {F+1..inf};
    less C = {-inf..C-1}."""
    if stype == "between":
        return float(floor), float(cap)
    if stype == "greater":
        return float(floor) + 1.0, math.inf
    if stype == "less":
        return -math.inf, float(cap) - 1.0
    raise ValueError(stype)


# ------------------------------------------------------------------ fee (taker, quadratic), integer e4 dollars
def order_fee_e4(price_e4, contracts=10):
    """ceil(0.07*C*P*(1-P) to $0.0001) for one order, in e4 dollars (exact integer maths)."""
    num = 7 * contracts * price_e4 * (10_000 - price_e4)
    return -(-num // 1_000_000)


def fee_per_contract_e4(price_e4):
    """ceil(0.07*10*P*(1-P)/0.0001)*0.0001/10, in e4 dollars per contract (may be fractional)."""
    return order_fee_e4(price_e4, 10) / 10.0


# ------------------------------------------------------------------ calibration
def _nll(params, txn, xnd, y):
    b, k = params
    mu = txn + b
    s = sigma_of(k, xnd)
    p = ndtr((y + 0.5 - mu) / s) - ndtr((y - 0.5 - mu) / s)
    return -float(np.sum(np.log(np.maximum(p, LL_FLOOR))))


def fit_bk(txn, xnd, y):
    """Maximum-likelihood (b, k) for Normal(TXN + b, max(1, k*XND)) with integer rounding. Bounded Nelder-Mead
    on (b, log k) with box clipping. Returns (b, k)."""
    txn = np.asarray(txn, float); xnd = np.asarray(xnd, float); y = np.asarray(y, float)

    def f(u):
        b = min(max(u[0], -B_MAX), B_MAX)
        k = math.exp(min(max(u[1], math.log(K_MIN)), math.log(K_MAX)))
        pen = 1e-3 * (max(0.0, abs(u[0]) - B_MAX) ** 2 + max(0.0, abs(u[1]) - 3) ** 2)
        return _nll((b, k), txn, xnd, y) + pen

    best = None
    for k0 in (1.0, 0.4, 2.0):          # a few starts: sigma floor makes the surface flat in places
        r = minimize(f, [float(np.mean(y - txn)), math.log(k0)], method="Nelder-Mead",
                     options=dict(xatol=1e-3, fatol=1e-4, maxiter=400))
        if best is None or r.fun < best.fun:
            best = r
    b = min(max(best.x[0], -B_MAX), B_MAX)
    k = math.exp(min(max(best.x[1], math.log(K_MIN)), math.log(K_MAX)))
    return b, k


def select_history(days, entries, day, dec_ts, n=WINDOW):
    """No-look-ahead history for one station.
    days: sorted list of event-day strings; entries: parallel list of dict(day, settled_ts, y, txn, xnd) (y/txn/xnd
    may be None when the observed high or the forecast is missing).  Returns the most recent <= n entries with
    day < `day`, settled_ts <= dec_ts (outcome public at the decision instant), and y, txn, xnd all present."""
    i = bisect.bisect_left(days, day)         # strictly earlier days only
    out = []
    j = i - 1
    while j >= 0 and len(out) < n:
        e = entries[j]
        if e["y"] is not None and e["txn"] is not None and e["xnd"] is not None and e["settled_ts"] <= dec_ts:
            out.append(e)
        j -= 1
    return out


def calibrate(station_hist, pool_hist, fit=fit_bk, pooled=None):
    """Rolling calibration with shrinkage. Returns dict(b, k, mode, n, n_pool) or None if no model is possible.
    - pooled: < MIN_STATION_DAYS pooled observations -> None.
    - station has < 15 prior days -> pooled values.
    - else weight w = n/(n+30) on the station's own fit, 1-w on the pooled fit."""
    if len(pool_hist) < MIN_STATION_DAYS:
        return None
    if pooled is None:
        pooled = fit([e["txn"] for e in pool_hist], [e["xnd"] for e in pool_hist], [e["y"] for e in pool_hist])
    pb, pk = pooled
    n = len(station_hist)
    if n < MIN_STATION_DAYS:
        return dict(b=pb, k=pk, mode="pooled", n=n, n_pool=len(pool_hist), b_station=None, k_station=None)
    sb, sk = fit([e["txn"] for e in station_hist], [e["xnd"] for e in station_hist], [e["y"] for e in station_hist])
    w = n / (n + SHRINK_K)
    return dict(b=w * sb + (1 - w) * pb, k=w * sk + (1 - w) * pk, mode="shrunk", n=n, n_pool=len(pool_hist),
                b_station=sb, k_station=sk)
