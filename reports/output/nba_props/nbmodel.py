"""Negative-binomial (NB2) regression, numpy only (no scipy/statsmodels in the repo venv).

Var(Y) = mu + alpha * mu^2, log link, r = 1/alpha.  Fit = alternate (a) IRLS for beta at fixed alpha, (b) 1-D golden-section search of
the profile log-likelihood over log(alpha).  P(Y >= N) = 1 - sum_{k<N} pmf(k) by the exact pmf recursion (no gamma function needed).
Also the Kalshi taker fee.
"""
from __future__ import annotations

import math

import numpy as np

# ------------------------------------------------------------------ Kalshi fee
FEE_MULT = 0.07
FEE_STEP = 1e-6          # docs.kalshi.com fee_rounding as recorded in memory/kalshi-exchange-mechanics.md (round UP to $0.000001)


def order_fee(price: float, count: int = 10, step: float = FEE_STEP) -> float:
    """Total taker fee in dollars: ceil_to_step(0.07 * C * P * (1 - P))."""
    if not 0.0 <= price <= 1.0:
        raise ValueError(price)
    raw = FEE_MULT * count * price * (1.0 - price)
    return round(math.ceil(raw / step - 1e-9) * step, 9)


def fee_per_contract(price: float, count: int = 10) -> float:
    return order_fee(price, count) / count


# ------------------------------------------------------------------ gammaln (vectorised, Stirling with upward shift)
_C = (1.0 / 12.0, -1.0 / 360.0, 1.0 / 1260.0, -1.0 / 1680.0, 1.0 / 1188.0)


def gammaln(x):
    x = np.asarray(x, dtype=float)
    shift = np.zeros_like(x)
    y = x.copy()
    for _ in range(12):
        m = y < 12.0
        if not m.any():
            break
        shift = shift + np.where(m, np.log(np.where(m, y, 1.0)), 0.0)
        y = np.where(m, y + 1.0, y)
    inv = 1.0 / y
    inv2 = inv * inv
    series = inv * (_C[0] + inv2 * (_C[1] + inv2 * (_C[2] + inv2 * (_C[3] + inv2 * _C[4]))))
    return (y - 0.5) * np.log(y) - y + 0.5 * math.log(2 * math.pi) + series - shift


def nb_loglik(y, mu, alpha):
    r = 1.0 / alpha
    return float(np.sum(gammaln(y + r) - gammaln(r) - gammaln(y + 1.0) + r * np.log(r / (r + mu)) + y * np.log(mu / (r + mu))))


# ------------------------------------------------------------------ pmf tail
def nb_sf(mu, alpha, n):
    """P(Y >= n) for Y ~ NB(mean mu, dispersion alpha); arrays broadcast; n integer >= 0."""
    mu = np.asarray(mu, dtype=float)
    n = np.asarray(n)
    mu, n = np.broadcast_arrays(mu, n)
    r = 1.0 / alpha
    p0 = np.exp(r * np.log(r / (r + mu)))
    cdf = np.zeros_like(mu)
    pk = p0.copy()
    kmax = int(n.max()) if n.size else 0
    ratio = mu / (r + mu)
    for k in range(kmax):
        cdf = cdf + np.where(k < n, pk, 0.0)
        pk = pk * (k + r) / (k + 1.0) * ratio
    return np.clip(1.0 - cdf, 0.0, 1.0)


# ------------------------------------------------------------------ fit
def _irls(X, y, alpha, beta0=None, ridge=1e-8, iters=60):
    n, k = X.shape
    beta = np.zeros(k) if beta0 is None else beta0.copy()
    if beta0 is None:
        beta[0] = math.log(max(float(y.mean()), 1e-3))
    for _ in range(iters):
        eta = np.clip(X @ beta, -20, 8)
        mu = np.exp(eta)
        w = mu / (1.0 + alpha * mu)
        z = eta + (y - mu) / mu
        A = X.T @ (X * w[:, None]) + ridge * np.eye(k)
        new = np.linalg.solve(A, X.T @ (w * z))
        if np.max(np.abs(new - beta)) < 1e-8:
            beta = new
            break
        beta = new
    return beta


def _golden(f, lo, hi, tol=1e-4):
    g = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c, d = b - g * (b - a), a + g * (b - a)
    fc, fd = f(c), f(d)
    while b - a > tol:
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a)
            fd = f(d)
    return (a + b) / 2


def fit_nb(X, y, outer=12):
    """X: (n,k) design WITHOUT intercept column (standardised internally with the fit sample's mean/sd).
    Returns dict(beta (with intercept, on standardised X), mean, sd, alpha, loglik)."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    mean, sd = X.mean(axis=0), X.std(axis=0)
    sd = np.where(sd < 1e-12, 1.0, sd)
    Z = np.column_stack([np.ones(len(y)), (X - mean) / sd])
    alpha = 0.2
    beta = None
    for _ in range(outer):
        beta = _irls(Z, y, alpha, beta)
        mu = np.exp(np.clip(Z @ beta, -20, 8))
        la = _golden(lambda t: -nb_loglik(y, mu, math.exp(t)), math.log(1e-4), math.log(20.0))
        new_alpha = math.exp(la)
        if abs(new_alpha - alpha) < 1e-5:
            alpha = new_alpha
            break
        alpha = new_alpha
    beta = _irls(Z, y, alpha, beta)
    mu = np.exp(np.clip(Z @ beta, -20, 8))
    return {"beta": beta, "mean": mean, "sd": sd, "alpha": alpha, "loglik": nb_loglik(y, mu, alpha)}


def predict_mu(model, X):
    X = np.asarray(X, dtype=float)
    Z = np.column_stack([np.ones(len(X)), (X - model["mean"]) / model["sd"]])
    return np.exp(np.clip(Z @ model["beta"], -20, 8))


def prob_at_least(model, X, n):
    return nb_sf(predict_mu(model, X), model["alpha"], n)
