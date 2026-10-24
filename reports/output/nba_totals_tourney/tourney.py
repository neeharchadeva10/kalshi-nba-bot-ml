"""Research log #39: formula tournament. Pure functions + Engine; unit-tested in test_tourney.py.
Reuses #37 lib.py and #38 ratings2.py (imported, not modified)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
V2_DIR = HERE.parent / "nba_totals_v2"
if str(V2_DIR) not in sys.path:
    sys.path.append(str(V2_DIR))      # append: this folder's modules must win names
import ratings2 as r2  # noqa: E402  (#38; also puts #37's dir on sys.path)
import lib  # noqa: E402  (#37)

K = 20                                # blend strength, fixed by the pre-registration
FORMULAS = ("F1", "F2", "F3", "F4", "F5")
RTYPES = ("R1", "R2", "R3")
RESTS = ("S0", "S1")
HALF_LIFE = 10.0                      # R2 weight 0.5 ** (age / 10)
R3_TOL, R3_MAX = 1e-6, 100
BUCKETS = ((0, 2), (2, 4), (4, 6), (6, 8), (8, float("inf")))
VERSIONS = [f"{f}_{r}_{s}" for f in FORMULAS for r in RTYPES for s in RESTS]   # 30


# ------------------------------------------------------------------ team-games (copy of r2.team_games + extras)
def team_games_x(tb, season_types):
    """Copy of ratings2.team_games (why: it drops the opponent, game id, tip and season type, which R3, the rest
    count and the 2024-25 fit need). Same filtering, tov fix, ET date, possessions and pace as #38."""
    tb = tb[tb.season_type.isin(season_types)].copy()
    tb["poss"] = lib.possessions(tb.field_goals_attempted, tb.offensive_rebounds,
                                 r2.fix_tov(tb.total_turnovers, tb.turnovers), tb.free_throws_attempted)
    opp = tb[["game_id", "team_abbreviation", "poss"]].rename(
        columns={"team_abbreviation": "opponent_team_abbreviation", "poss": "opp_poss"})
    m = tb.merge(opp, on=["game_id", "opponent_team_abbreviation"], how="inner")
    gdt = pd.to_datetime(m.game_date_time)
    if gdt.dt.tz is None:
        gdt = gdt.dt.tz_localize("UTC")
    et = gdt.dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
    tip = ((gdt.dt.tz_convert("UTC") - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)).values
    out = pd.DataFrame({"game_id": m.game_id.values, "date": et.values,
                        "team": m.team_abbreviation.map(lib.to_kalshi).values,
                        "opp": m.opponent_team_abbreviation.map(lib.to_kalshi).values,
                        "pts": m.team_score.values.astype(float), "opp_pts": m.opponent_team_score.values.astype(float),
                        "poss": m.poss.values.astype(float), "opp_poss": m.opp_poss.values.astype(float),
                        "tip": tip.astype("int64"), "season_type": m.season_type.values})
    out = out.dropna(subset=["team", "opp"]).copy()
    for c in ("pts", "opp_pts", "poss", "opp_poss"):
        assert np.isfinite(out[c].values).all(), f"non-finite box-score input in {c}"
    out["pace"] = (out.poss + out.opp_poss) / 2.0
    return out.sort_values("date", kind="stable").reset_index(drop=True)


# ------------------------------------------------------------------ formulas
def formula_points(fid, ra, rb, Lo, Lp):
    """Points for team A and team B. ra, rb = (O, D, P) blended ratings; Lo, Lp = league ORtg and pace at D."""
    def one(own, opp):
        O, _, P = own
        _, D, Q = opp
        if fid == "F1":
            return (P + Q) / 2.0 * (O + D) / 2.0
        if fid == "F2":
            return (P * Q / Lp) * O * D / Lo
        if fid == "F3":
            return (P + Q) / 2.0 * (O + D - Lo)
        if fid == "F4":
            return (O * P + D * Q) / 2.0
        if fid == "F5":
            return O * P + D * Q - Lo * Lp
        raise ValueError(fid)
    return one(ra, rb), one(rb, ra)


def formula_total(fid, ra, rb, Lo, Lp):
    a, b = formula_points(fid, ra, rb, Lo, Lp)
    return a + b


# ------------------------------------------------------------------ R3: KenPom-style adjustment
def kenpom_adjust(tg, tol=R3_TOL, max_rounds=R3_MAX, return_change=False):
    """Opponent adjustment over team-games `tg` (needs team, opp, pts, opp_pts, poss, opp_poss).
    adj_O_a = sum_g [pts/poss x L / adj_D_opp] x poss / sum poss ;  adj_D_a = sum_g [opp_pts/opp_poss x L / adj_O_opp]
    x opp_poss / sum opp_poss  (L = league ORtg of tg). Start from raw ORtg/DRtg. Sequential (Gauss-Seidel) update:
    O from the old D, then D from the NEW O (a simultaneous update has a -1 eigenvalue and never converges).
    Stops when the max change of any rating < tol, or after max_rounds.  Returns ({team: (adj_O, adj_D)}, rounds)."""
    teams = sorted(set(tg.team) | set(tg.opp))
    ix = {t: i for i, t in enumerate(teams)}
    t = tg.team.map(ix).values
    o = tg.opp.map(ix).values
    nt = len(teams)
    pts, opts = tg.pts.values.astype(float), tg.opp_pts.values.astype(float)
    pos, opos = tg.poss.values.astype(float), tg.opp_poss.values.astype(float)
    L = pts.sum() / pos.sum()
    sp = np.bincount(t, pos, nt)
    sop = np.bincount(t, opos, nt)
    O = np.bincount(t, pts, nt) / sp
    D = np.bincount(t, opts, nt) / sop
    xo, xd = pts / pos, opts / opos
    rounds, change = 0, float("nan")
    for rounds in range(1, max_rounds + 1):
        O_new = np.bincount(t, xo * L / D[o] * pos, nt) / sp
        D_new = np.bincount(t, xd * L / O_new[o] * opos, nt) / sop
        change = max(np.max(np.abs(O_new - O)), np.max(np.abs(D_new - D)))
        O, D = O_new, D_new
        if change < tol:
            break
    res = {tm: (O[i], D[i]) for tm, i in ix.items()}
    return (res, rounds, change) if return_change else (res, rounds)


# ------------------------------------------------------------------ rest
def b2b_flag(dates, D):
    """dates: sorted datetime64[D] array of a team's game dates. True if the team's latest game before D was on D-1.
    The team's first game of the season (no earlier game) is NOT a back-to-back."""
    i = int(np.searchsorted(dates, np.datetime64(D, "D"), side="left"))
    return bool(i > 0 and dates[i - 1] == np.datetime64(D, "D") - np.timedelta64(1, "D"))


def fit_c(resid, x):
    """OLS WITH intercept (amendment 2026-10-21, 1): resid = a + c x. Only c is applied. Returns (c, a, n, n_b2b)."""
    resid, x = np.asarray(resid, float), np.asarray(x, float)
    sxx = float(((x - x.mean()) ** 2).sum())
    assert sxx > 0, "rest regressor has no variation"
    c = float(((x - x.mean()) * (resid - resid.mean())).sum() / sxx)
    return c, float(resid.mean() - c * x.mean()), int(len(x)), int(x.sum())


# ------------------------------------------------------------------ the engine
class Engine:
    def __init__(self, prev_tg, cur_tg, k=K):
        self.k = k
        self.prev, self.Lo_prev, self.Lp_prev = r2.prior_from_tg(prev_tg)
        cur_tg = cur_tg.sort_values("date", kind="stable").reset_index(drop=True)
        self.cur = cur_tg
        self.R1 = r2.Ratings(self.prev, self.Lo_prev, self.Lp_prev, cur_tg)    # #38's class, used as is
        self.r3_rounds = []
        adj, rounds, ch = kenpom_adjust(prev_tg, return_change=True)
        self.r3_rounds.append(("prior", rounds, ch))
        self.prev_adj = adj
        self.dates = cur_tg.date.values.astype("datetime64[D]")
        self.team = {}
        for t, g in cur_tg.groupby("team"):
            g = g.sort_values("date", kind="stable")
            self.team[t] = {"dates": g.date.values.astype("datetime64[D]"),
                            "a": g[["pts", "poss", "opp_pts", "opp_poss", "pace"]].values.astype(float)}
        self._snap = {}

    # -- league level at D (shifted as #38): Lo_prev * r_o, Lp_prev * r_p
    def league_level(self, D):
        r_o, r_p, n_lg = self.R1.league(D)
        return self.Lo_prev * r_o, self.Lp_prev * r_p, r_o, r_p

    def snapshot(self, D, rtype):
        """{team: (O, D, P)} blended at k, for every team, using only games with ET date < D."""
        D = np.datetime64(D, "D")
        key = (rtype, int(D.astype("int64")))
        if key in self._snap:
            return self._snap[key]
        r_o, r_p, _ = self.R1.league(D)
        out = {}
        if rtype == "R3":
            i = int(np.searchsorted(self.dates, D, side="left"))
            adj = None
            if i > 0:
                sl = self.cur.iloc[:i]
                adj, rounds, ch = kenpom_adjust(sl, return_change=True)
                self.r3_rounds.append((str(D), rounds, ch))
        for t, (Op, Dp, Pp) in self.prev.items():
            if rtype == "R3":
                Oa, Da = self.prev_adj[t]
                prior = (Oa * r_o, Da * r_o, Pp * r_p)
            else:
                prior = (Op * r_o, Dp * r_o, Pp * r_p)
            n_eff, cur = 0, None
            if t in self.team:
                dts, a = self.team[t]["dates"], self.team[t]["a"]
                n = int(np.searchsorted(dts, D, side="left"))
                if n:
                    if rtype == "R1":
                        s = a[:n].sum(axis=0)
                        n_eff, cur = n, (s[0] / s[1], s[2] / s[3], s[4] / n)
                    elif rtype == "R2":
                        w = 0.5 ** (np.arange(n - 1, -1, -1) / HALF_LIFE)     # age 0 = most recent game
                        s = (a[:n] * w[:, None]).sum(axis=0)
                        n_eff, cur = float(w.sum()), (s[0] / s[1], s[2] / s[3], s[4] / w.sum())
                    else:   # R3: adjusted O, D; pace unadjusted
                        Oa, Da = adj[t]
                        n_eff, cur = n, (Oa, Da, a[:n, 4].sum() / n)
            out[t] = tuple(r2.blend(prior[j], (cur[j] if cur else None), n_eff, self.k) for j in range(3))
        self._snap[key] = out
        return out

    def b2b(self, team, D):
        return b2b_flag(self.team[team]["dates"], D) if team in self.team else False

    def project_raw(self, away, home, D):
        """({'F1_R1': total, ...} 15 values, number of teams on a back-to-back)."""
        Lo, Lp, _, _ = self.league_level(D)
        out = {}
        for r in RTYPES:
            snap = self.snapshot(D, r)
            for f in FORMULAS:
                out[f"{f}_{r}"] = formula_total(f, snap[away], snap[home], Lo, Lp)
        return out, int(self.b2b(away, D)) + int(self.b2b(home, D))

    def project_table(self, games, c=None):
        """games needs away, home, tip, T (decision date D = ET date of min(tip, T-1h)).
        Returns (DataFrame of 15 raw columns F?_R? + 'nb2b', and if c is given the 30-version DataFrame)."""
        rows, nb = [], []
        for r in games.itertuples():
            D = r2.decision_date(r.tip, r.T)
            p, n = self.project_raw(r.away, r.home, D)
            rows.append(p)
            nb.append(n)
        raw = pd.DataFrame(rows, index=games.index)
        raw["nb2b"] = nb
        return raw if c is None else with_rest(raw, c)


def with_rest(raw, c):
    """30-version table from raw (15 cols + nb2b) and c = {(f, r): value}. S0 = raw; S1 = raw + c x nb2b."""
    out = {}
    for f in FORMULAS:
        for r in RTYPES:
            out[f"{f}_{r}_S0"] = raw[f"{f}_{r}"].values
            out[f"{f}_{r}_S1"] = raw[f"{f}_{r}"].values + c[(f, r)] * raw.nb2b.values
    return pd.DataFrame(out, index=raw.index)


# ------------------------------------------------------------------ 2024-25 fit of c
def fit_games_2425(tg_cur):
    """One row per regular-season (season_type 2) game of the 2024-25 box: away/home (the alphabetically first team is
    'away', irrelevant: every formula is symmetric), actual total, tip, T = tip. D = ET date of tip - 1h."""
    g = tg_cur[(tg_cur.season_type == 2) & (tg_cur.team < tg_cur.opp)]
    return pd.DataFrame({"away": g.team.values, "home": g.opp.values, "tip": g.tip.values, "T": g.tip.values,
                         "actual": (g.pts + g.opp_pts).values}, index=g.index).reset_index(drop=True)


def fit_all_c(engine24, games24):
    """c per (formula, rtype): OLS with intercept of (actual - raw projection) on nb2b over games24.
    Returns ({(f, r): c}, info {(f, r): (c, a, n_games, n_b2b_total)}). Only c is applied downstream."""
    raw = engine24.project_table(games24)
    c, info = {}, {}
    for f in FORMULAS:
        for r in RTYPES:
            res = games24.actual.values - raw[f"{f}_{r}"].values
            c[(f, r)], a, n, nb = fit_c(res, raw.nb2b.values)
            info[(f, r)] = (c[(f, r)], a, n, nb)
    return c, info


def load_engines(box_dir, before=None):
    """(engine_for_2025_26, engine_for_2024_25_fit, games24). `before` truncates the 2025-26 box scores (gate stage)."""
    box = Path(box_dir)
    rd = lambda y: pd.read_parquet(box / f"team_box_{y}.parquet")        # noqa: E731
    cur24 = team_games_x(rd(2025), r2.CUR_SEASON_TYPES)
    assert cur24.date.max() < pd.Timestamp("2025-07-01"), "2024-25 fit data must predate 2025-07-01"
    eng24 = Engine(team_games_x(rd(2024), (2,)), cur24)
    games24 = fit_games_2425(cur24)
    prev25 = team_games_x(rd(2025), (2,))
    cur26 = team_games_x(rd(2026), r2.CUR_SEASON_TYPES)
    if before is not None:
        cur26 = cur26[cur26.date < pd.Timestamp(before)].reset_index(drop=True)
        assert cur26.date.max() < pd.Timestamp(before), "gate-stage box scores must predate the cutoff"
    return Engine(prev25, cur26), eng24, games24


# ------------------------------------------------------------------ leaderboard pieces
def side_won_table(proj, median, actual):
    """Share of games where the actual landed on the formula's side of the market median, by |proj - median| bucket.
    Games with proj == median or actual == median have no side and are dropped."""
    proj, median, actual = (np.asarray(a, float) for a in (proj, median, actual))
    ok = np.isfinite(proj) & np.isfinite(median) & np.isfinite(actual)
    d = np.abs(proj - median)
    prod = (proj - median) * (actual - median)
    out = {}
    for lo, hi in BUCKETS:
        m = ok & (d >= lo) & (d < hi) & (prod != 0)
        out[f"[{lo},{hi})"] = {"n": int(m.sum()), "share": float((prod[m] > 0).mean()) if m.any() else None}
    return out


def pick_winner(briers):
    """briers: {version: discovery Brier}. Lowest Brier; ties go to R1<R2<R3, then S0<S1, then F1..F5."""
    def key(v):
        f, r, s = v.split("_")
        return (briers[v], RTYPES.index(r), RESTS.index(s), FORMULAS.index(f))
    return min(briers, key=key)


def size_contracts(edge, theta):
    """min(10, 1 + floor(9 x (edge - theta)/(0.10 - theta))), at least 1. 1e-9 absorbs float noise (edge == 0.10)."""
    return int(max(1, min(10, 1 + math.floor(9.0 * (edge - theta) / (0.10 - theta) + 1e-9))))


# ------------------------------------------------------------------ fast residual test (same maths as lib.residual_test)
def residual_test_fast(mid, pmodel, y, game_ids, n_boot=10000, seed=lib.SEED, chunk=500):
    """Same model and RNG stream as lib.residual_test (one rng.integers(0, ng, ng) per draw), but the 10,000
    bootstrap logistic fits are batched: each draw is a weighted fit (weight = times the game was picked), warm
    started at the full-sample fit, Newton steps run for all draws in a chunk at once. Estimates equal the loop's to
    solver tolerance (tested against lib.residual_test)."""
    mid, pmodel, y = (np.asarray(a, float) for a in (mid, pmodel, y))
    game_ids = np.asarray(game_ids)
    lm, lp = lib.logit(mid), lib.logit(pmodel)
    X = np.column_stack([np.ones(len(y)), lm, lp - lm])
    b0 = lib.logistic_fit(X, y)
    games, rowgame = np.unique(game_ids, return_inverse=True)
    ng = len(games)
    rng = np.random.default_rng(seed)
    C = np.empty((n_boot, ng), dtype=np.float64)
    for i in range(n_boot):
        C[i] = np.bincount(rng.integers(0, ng, size=ng), minlength=ng)
    XX = np.einsum("ni,nj->nij", X, X).reshape(len(y), 9)
    out = np.empty(n_boot)
    for s in range(0, n_boot, chunk):
        W = C[s:s + chunk][:, rowgame]
        B = W.shape[0]
        b = np.tile(b0, (B, 1))
        for _ in range(50):
            p = 1.0 / (1.0 + np.exp(-(b @ X.T)))
            w = np.clip(p * (1 - p), 1e-9, None)
            g = ((y - p) * W) @ X
            H = ((W * w) @ XX).reshape(B, 3, 3) + 1e-8 * np.eye(3)
            step = np.linalg.solve(H, g[..., None])[..., 0]
            b = b + step
            if np.max(np.abs(step)) < 1e-9:
                break
        out[s:s + B] = b[:, 2]
    return float(b0[2]), float(np.quantile(out, 0.025)), float(np.quantile(out, 0.975))


# ------------------------------------------------------------------ sized betting (contract-weighted)
def wmean_boot_ci(dollars, counts, n=10000, seed=lib.SEED, alpha=0.05):
    """Bootstrap (resample games = bets) of sum(dollars)/sum(contracts)."""
    d, c = np.asarray(dollars, float), np.asarray(counts, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(n, len(d)))
    m = d[idx].sum(axis=1) / c[idx].sum(axis=1)
    return float(np.quantile(m, alpha / 2)), float(np.quantile(m, 1 - alpha / 2))
