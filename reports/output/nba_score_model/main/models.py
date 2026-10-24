"""Research log #59: models.  Ridge (numpy closed form), leave-one-season-out CV, stacking, the grid's TEAM points (B1, B2, G), metrics.

Team points of a grid formula: the grid (#42/#56) projects a TOTAL; this module splits it into two team scores that sum to exactly that
total (tested against gl.project_one / build_block):
  base_team = one_points(A, B, own O, own P, opp D, opp P, Lo, Lp)            (the formula's own per-team points)
  shrink on : team = Lt/2 + b * (base_team - Lt/2)                               (the total-level  Lt + b (base - Lt)  split in half)
  rest F    : none -> 0; b2b -> c_b2b * [team is on a back-to-back]; cat -> e_cat[own rest category]
              (the total-level adjustments c*nb2b and e[cat_a] + e[cat_h] are sums of exactly these per-team terms)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sm_lib as S  # noqa: E402  (also puts impl_a / #56 / grid on sys.path)
import fa_ext as fx  # noqa: E402
import fa_lib as fa  # noqa: E402
import grid_lib as gl  # noqa: E402

if str(HERE) in sys.path:
    sys.path.remove(str(HERE))
sys.path.insert(0, str(HERE))

LAMBDAS = (0.1, 1.0, 10.0, 100.0, 1000.0, 10000.0, 100000.0, 1000000.0)
B1_FLAT = 8700
B2_FLAT = 7791
WITHIN = 10.0
CANDIDATE_ORDER = ("B1", "B2", "G", "R1", "R2", "E1", "E2")       # ties on validation MAE go to the earlier id


# ------------------------------------------------------------------ ridge (standardised features, unpenalised intercept)
def standardise_fit(X):
    mu = X.mean(axis=0)
    sd = X.std(axis=0, ddof=0)
    return mu, np.where(sd > 0, sd, 1.0)


def ridge_fit(Xs, y, lam):
    """Minimises sum (y - b0 - Xs b)^2 + lam |b|^2 for standardised Xs (column means 0, so b0 = mean y)."""
    ym = float(y.mean())
    A = Xs.T @ Xs + lam * np.eye(Xs.shape[1])
    return ym, np.linalg.solve(A, Xs.T @ (y - ym))


def ridge_predict(X, mu, sd, b0, b):
    return b0 + ((np.asarray(X, float) - mu) / sd) @ b


def loso_folds(seasons_of_rows):
    """Leave-one-season-out: list of (season, test_mask).  Every season is held out exactly once."""
    s = np.asarray(seasons_of_rows)
    return [(u, s == u) for u in sorted(set(s))]


def loso_predict(X, y, seasons_of_rows, lam):
    pred = np.full(len(y), np.nan)
    for _, te in loso_folds(seasons_of_rows):
        mu, sd = standardise_fit(X[~te])
        b0, b = ridge_fit((X[~te] - mu) / sd, y[~te], lam)
        pred[te] = ridge_predict(X[te], mu, sd, b0, b)
    return pred


def cv_lambda(X, y, seasons_of_rows, lambdas=LAMBDAS):
    """Leave-one-season-out CV: pooled out-of-fold MSE per lambda; best = lowest MSE, ties -> larger lambda."""
    mse = {}
    for lam in lambdas:
        p = loso_predict(X, y, seasons_of_rows, lam)
        mse[lam] = float(np.mean((y - p) ** 2))
    best = min(mse, key=lambda L_: (mse[L_], -L_))
    return best, mse


def league_expected(F):
    """League expected points of a team-game: lg_ortg x lg_pace / 100 (season to date)."""
    return F.lg_ortg.values.astype(float) * F.lg_pace.values.astype(float) / 100.0


def fit_ridge_model(F_train, kind):
    """kind 'R1': target = pts.  kind 'R2': target = pts - league expected (added back at prediction).  lambda by LOSO CV on F_train.
    Returns (model dict, out-of-fold predictions on the original points scale)."""
    X = F_train[S.FEATS].values.astype(float)
    base = league_expected(F_train) if kind == "R2" else np.zeros(len(F_train))
    y = F_train[S.TARGET].values.astype(float) - base
    seasons = F_train.season.values
    lam, cv = cv_lambda(X, y, seasons)
    oof = loso_predict(X, y, seasons, lam) + base
    mu, sd = standardise_fit(X)
    b0, b = ridge_fit((X - mu) / sd, y, lam)
    model = {"kind": kind, "lambda": lam, "cv_mse": {str(k): v for k, v in cv.items()}, "b0": b0, "coef": b.tolist(),
             "mu": mu.tolist(), "sd": sd.tolist(), "features": list(S.FEATS), "n_rows": int(len(y)),
             "train_seasons": sorted(set(seasons))}
    return model, oof


def ridge_model_predict(model, F):
    X = F[S.FEATS].values.astype(float)
    p = ridge_predict(X, np.array(model["mu"]), np.array(model["sd"]), model["b0"], np.array(model["coef"]))
    return p + (league_expected(F) if model["kind"] == "R2" else 0.0)


# ------------------------------------------------------------------ stacking
def fit_stack(g, r1, r2, y):
    """E2: y ~ w0 + wG g + w1 r1 + w2 r2 by least squares over the training team-games (g = in-season grid points of the best G formula,
    r1 / r2 = out-of-fold ridge predictions)."""
    A = np.column_stack([np.ones(len(y)), g, r1, r2])
    w, *_ = np.linalg.lstsq(A, y, rcond=None)
    return {"w0": float(w[0]), "wG": float(w[1]), "w1": float(w[2]), "w2": float(w[3])}


def stack_predict(w, g, r1, r2):
    return w["w0"] + w["wG"] * np.asarray(g) + w["w1"] * np.asarray(r1) + w["w2"] * np.asarray(r2)


# ------------------------------------------------------------------ metrics
def game_metrics(game_id, home, y, pred):
    """Per-row arrays (two rows per game).  Total = sum of the two team predictions.  Returns the full metric dict."""
    game_id, home = np.asarray(game_id), np.asarray(home, float)
    y, pred = np.asarray(y, float), np.asarray(pred, float)
    u, inv = np.unique(game_id, return_inverse=True)
    cnt = np.bincount(inv)
    assert (cnt == 2).all(), "every game needs exactly two team rows"
    tp = np.bincount(inv, pred)
    ta = np.bincount(inv, y)
    sgn = np.where(home > 0, 1.0, -1.0)
    mp = np.bincount(inv, pred * sgn)
    ma = np.bincount(inv, y * sgn)
    e = tp - ta
    return {"n_games": int(len(u)), "total_mae": float(np.abs(e).mean()), "pct_within_10": float((np.abs(e) <= WITHIN).mean()),
            "total_rmse": float(np.sqrt((e ** 2).mean())), "bias": float(e.mean()), "team_mae": float(np.abs(pred - y).mean()),
            "margin_mae": float(np.abs(mp - ma).mean()), "winner_pct": float((np.sign(mp) == np.sign(ma)).mean()),
            "game_ids": u, "err": e, "tot_pred": tp, "tot_act": ta}


def strip(m):
    return {k: v for k, v in m.items() if k not in ("game_ids", "err", "tot_pred", "tot_act")}


# ------------------------------------------------------------------ grid
def formula_sums(P, actual, chunk=2000):
    """P (S, N) projections, actual (N,) -> per-formula sums of |e|, e^2, e, #(|e| <= 10) (e = proj - actual)."""
    nS = P.shape[0]
    out = {k: np.zeros(nS) for k in ("sabs", "ssq", "se", "w10")}
    for lo in range(0, nS, chunk):
        e = P[lo:lo + chunk] - actual
        out["sabs"][lo:lo + chunk] = np.abs(e).sum(axis=1)
        out["ssq"][lo:lo + chunk] = (e ** 2).sum(axis=1)
        out["se"][lo:lo + chunk] = e.sum(axis=1)
        out["w10"][lo:lo + chunk] = (np.abs(e) <= WITHIN).sum(axis=1)
    out["n"] = int(len(actual))
    return out


def pool(parts):
    n = sum(p["n"] for p in parts)
    agg = {k: sum(p[k] for p in parts) for k in ("sabs", "ssq", "se", "w10")}
    return {"n": n, "mae": agg["sabs"] / n, "rmse": np.sqrt(agg["ssq"] / n), "bias": agg["se"] / n, "within10": agg["w10"] / n}


class GridSeason:
    """One season of the grid: world (constants from S-1), games without outcomes, cached rating tables."""

    def __init__(self, season, legacy_2022=True, reader=None):
        self.season = season
        self.world = fx.build_world_ext(season, reader=reader, legacy_2022=legacy_2022)
        self.games = fa.games_frame(self.world)
        self.dates = gl.decision_dates(self.games)
        self.away, self.home = self.games.away.values, self.games.home.values
        self._tabs = {}
        self._rest = None

    def tab(self, ci):
        if ci not in self._tabs:
            self._tabs[ci] = gl.CurTable.build(self.world.season, self.dates, self.away, self.home, gl.C_OPTS[ci])
        return self._tabs[ci]

    def rest(self):
        if self._rest is None:
            self._rest = gl.rest_info(self.world.season, self.dates, self.away, self.home)
        return self._rest

    def actual_totals(self):
        """Outcomes: call only for seasons whose outcomes may be used (validation / test stage)."""
        return fa.actuals_frame(self.world).reindex(self.games.game_id.values).values.astype(float)

    def all_totals(self):
        return gl.build_projections(self.world.season, self.games, self.world.consts)

    def team_points(self, spec):
        """(away_points, home_points) of one formula, summing to gl.project_one's total."""
        a, b, c, d, e, f, g, h = spec
        ft = gl.features(self.tab(c), gl.D_OPTS[d], bool(e), bool(g))
        A, B = gl.A_OPTS[a], gl.B_OPTS[b]
        pa = gl.one_points(A, B, ft["Oa"], ft["Pa"], ft["Dh"], ft["Ph"], ft["Lo"], ft["Lp"])
        ph = gl.one_points(A, B, ft["Oh"], ft["Ph"], ft["Da"], ft["Pa"], ft["Lo"], ft["Lp"])
        rest, cs = self.rest(), self.world.consts
        if f == 1:
            adj_a, adj_h = cs.c_b2b[a, c] * (rest.cat_a == 0), cs.c_b2b[a, c] * (rest.cat_h == 0)
        elif f == 2:
            eff = gl.cat_effects(cs.e_cat[a, c])
            adj_a, adj_h = eff[rest.cat_a], eff[rest.cat_h]
        else:
            adj_a = adj_h = 0.0
        if h:
            half = ft["Lo"] * ft["Lp"]                  # = Lt / 2
            pa = half + cs.b[a, c] * (pa - half)
            ph = half + cs.b[a, c] * (ph - half)
        return np.asarray(pa + adj_a, float), np.asarray(ph + adj_h, float)

    def total(self, spec):
        return gl.project_one(self.world.season, self.games, self.world.consts, spec)


def map_to_rows(games, away_pts, home_pts, rows):
    """Grid team points for the feature rows (game_id, home flag): NaN for rows whose game is not in the grid's games frame."""
    pa = pd.Series(away_pts, index=games.game_id.values)
    ph = pd.Series(home_pts, index=games.game_id.values)
    gid = rows.game_id.values
    is_home = rows.home.values > 0
    out = np.where(is_home, ph.reindex(gid).values, pa.reindex(gid).values)
    return out.astype(float)


def spec_info(flat):
    spec = gl.spec_from_flat(int(flat))
    return [int(x) for x in spec], {k: str(v) for k, v in gl.spec_names(spec).items()}


# ------------------------------------------------------------------ paired bootstrap
BOOT_N = 10000
BOOT_SEED = 20261023


def paired_boot_ci(d, n_boot=BOOT_N, seed=BOOT_SEED, chunk=500):
    """Game bootstrap of mean(d) (d = per-game |error A| - |error B|): (point, lo, hi) = mean, 2.5 and 97.5 percentiles."""
    d = np.asarray(d, float)
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot)
    for lo in range(0, n_boot, chunk):
        k = min(chunk, n_boot - lo)
        means[lo:lo + k] = d[rng.integers(0, len(d), size=(k, len(d)))].mean(axis=1)
    return float(d.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))
