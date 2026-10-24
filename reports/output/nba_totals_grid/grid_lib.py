"""Research log #42: exhaustive NBA totals formula grid (ratings + projections).  Pure functions + Season class;
unit-tested in test_grid.py.  Imports (does not modify) #37 lib, #38 ratings2 and #39 tourney.

Grid (PREREGISTRATION.md): A combine (7) x B pace (4) x C ratings (10) x D prior k (4) x E shift (2) x F rest (3)
x G split (2) x H shrink (2) = 26,880 formulas.  The flat formula index is C-order over (A,B,C,D,E,F,G,H)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
for _p in (HERE.parent / "nba_totals_pace", HERE.parent / "nba_totals_v2", HERE.parent / "nba_totals_tourney"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))          # append: this folder's own modules win names
import lib  # noqa: E402  (#37)
import ratings2 as r2  # noqa: E402  (#38)
import tourney as tn  # noqa: E402  (#39)

# ------------------------------------------------------------------ the grid
A_OPTS = ("w1.0", "w0.75", "w0.5", "w0.25", "w0.0", "mult", "add")
A_W = {"w1.0": 1.0, "w0.75": 0.75, "w0.5": 0.5, "w0.25": 0.25, "w0.0": 0.0}
B_OPTS = ("own", "avg", "mult", "termown")
C_OPTS = ("season", "decay5", "decay10", "decay20", "last5", "last10", "last15", "kenpom", "massey", "fourfactor")
D_OPTS = (0, 10, 20, 40)
E_OPTS = ("off", "on")
F_OPTS = ("none", "b2b", "cat")
G_OPTS = ("off", "on")
H_OPTS = ("off", "on")
DIM_NAMES = ("A", "B", "C", "D", "E", "F", "G", "H")
DIM_OPTS = (A_OPTS, B_OPTS, C_OPTS, D_OPTS, E_OPTS, F_OPTS, G_OPTS, H_OPTS)
SHAPE = tuple(len(o) for o in DIM_OPTS)                  # (7,4,10,4,2,3,2,2)
N_FORMULAS = int(np.prod(SHAPE))                         # 26,880
DEFAULTS = {"A": "w0.5", "B": "avg", "C": "season", "D": 20, "E": "on", "F": "none", "G": "off", "H": "off"}
DEFAULT_IDX = tuple(DIM_OPTS[i].index(DEFAULTS[n]) for i, n in enumerate(DIM_NAMES))
HALF_LIVES = {"decay5": 5.0, "decay10": 10.0, "decay20": 20.0}
LAST_N = {"last5": 5, "last10": 10, "last15": 15}
SPLIT_C = 10.0                                            # home/away blend n_split / (n_split + 10)
MASSEY_LAMBDA = 10.0
K_FIT = 20                                                # constants are fitted at k = 20, shift on, pace avg, no split
GATE_CUTOFF = "2026-01-23"


def spec_from_flat(i):
    return tuple(int(x) for x in np.unravel_index(int(i), SHAPE))


def flat_from_spec(spec):
    return int(np.ravel_multi_index(tuple(spec), SHAPE))


def spec_names(spec):
    return {n: DIM_OPTS[j][spec[j]] for j, n in enumerate(DIM_NAMES)}


def spec_from_names(names):
    return tuple(DIM_OPTS[j].index(names[n]) for j, n in enumerate(DIM_NAMES))


def n_nondefault(spec):
    return int(sum(1 for s, d in zip(spec, DEFAULT_IDX) if s != d))


# ------------------------------------------------------------------ team-games (copy of tn.team_games_x + extras)
FF_OWN = ["efgnum", "fga", "tov", "tovden", "oreb", "orebden", "ftm"]
FF_OPP = ["o_" + c for c in FF_OWN]
MAT_COLS = ["pts", "poss", "opp_pts", "opp_poss", "pace"] + FF_OWN + FF_OPP          # 19 columns


def team_games_g(tb, season_types):
    """Copy of tourney.team_games_x (why: the four-factor rating, the home/away split and the 2024-25 fit need the
    extra columns efgnum/fga/tov/tovden/oreb/orebden/ftm for the team and for its opponent, and the venue).
    Same filtering, turnover fix, ET date, possessions, pace, row order as #39 (tested equal on the shared columns)."""
    tb = tb[tb.season_type.isin(season_types)].copy()
    tov = r2.fix_tov(tb.total_turnovers, tb.turnovers)
    tb["poss"] = lib.possessions(tb.field_goals_attempted, tb.offensive_rebounds, tov, tb.free_throws_attempted)
    tb["efgnum"] = tb.field_goals_made + 0.5 * tb.three_point_field_goals_made
    tb["fga"] = tb.field_goals_attempted
    tb["tov"] = tov
    tb["tovden"] = tb.field_goals_attempted + 0.44 * tb.free_throws_attempted + tov
    tb["oreb"] = tb.offensive_rebounds
    tb["dreb"] = tb.defensive_rebounds
    tb["ftm"] = tb.free_throws_made
    oc = ["poss", "efgnum", "fga", "tov", "tovden", "oreb", "dreb", "ftm"]
    opp = tb[["game_id", "team_abbreviation"] + oc].rename(
        columns={"team_abbreviation": "opponent_team_abbreviation", **{c: "o_" + c for c in oc}})
    m = tb.merge(opp, on=["game_id", "opponent_team_abbreviation"], how="inner")
    m["opp_poss"] = m["o_poss"]
    m["orebden"] = m["oreb"] + m["o_dreb"]            # own ORB% denominator: own OREB + opponent DREB
    m["o_orebden"] = m["o_oreb"] + m["dreb"]          # opponent ORB% denominator: opp OREB + own DREB
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
                        "tip": tip.astype("int64"), "season_type": m.season_type.values,
                        "venue": m.team_home_away.values})
    for c in FF_OWN + FF_OPP:
        out[c] = m[c].values.astype(float)
    out = out.dropna(subset=["team", "opp"]).copy()
    for c in ["pts", "opp_pts", "poss", "opp_poss"] + FF_OWN + FF_OPP:
        assert np.isfinite(out[c].values).all(), f"non-finite box-score input in {c}"
    out["pace"] = (out.poss + out.opp_poss) / 2.0
    return out.sort_values("date", kind="stable").reset_index(drop=True)


# ------------------------------------------------------------------ four factors
def ff_rates(sums7):
    """sums7 (..., 7) in FF_OWN order -> (..., 4): eFG%, TOV%, ORB%, FT/FGA from summed numerators/denominators
    (so a multi-game rating is rate-weighted: each factor is a ratio of sums)."""
    s = np.asarray(sums7, float)
    return np.stack([s[..., 0] / s[..., 1], s[..., 2] / s[..., 3], s[..., 4] / s[..., 5], s[..., 6] / s[..., 1]], axis=-1)


def fit_four_factor(tg):
    """OLS of team-game ORtg (pts / poss) on [1, eFG%, TOV%, ORB%, FT/FGA] over all rows of tg (row-level rates).
    Returns (coef (5,), r2)."""
    X = np.column_stack([np.ones(len(tg)), ff_rates(tg[FF_OWN].values)])
    y = (tg.pts / tg.poss).values
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = y - X @ coef
    return coef, float(1 - (res ** 2).sum() / ((y - y.mean()) ** 2).sum())


def ff_apply(coef, sums7):
    """Fitted points per possession from summed factors (own factors -> O; opponent's factors -> D)."""
    return float(coef[0] + ff_rates(sums7) @ np.asarray(coef[1:], float))


# ------------------------------------------------------------------ Massey ridge
def massey_fit(tg, lam=MASSEY_LAMBDA):
    """Ridge (amendment 2026-10-21 ~20:00Z: UNWEIGHTED rows, so lam = 10 means 10 games of shrinkage):
    y = pts / poss of every team-game row = mu + o_team + dd_opp  (dd = what the opponent ALLOWS above mu, so D = mu + dd is on the allowed scale; this is the
    pre-registration's 'mu + o - d' with d = -dd, an identical fit).  Penalty lam x (sum o^2 + sum dd^2); mu is not
    penalised.  Returns (mu, {team: (o, dd)}).  Teams are those in tg."""
    teams = sorted(set(tg.team) | set(tg.opp))
    ix = {t: i for i, t in enumerate(teams)}
    nt, n = len(teams), len(tg)
    X = np.zeros((n, 1 + 2 * nt))
    X[:, 0] = 1.0
    r = np.arange(n)
    X[r, 1 + tg.team.map(ix).values] = 1.0
    X[r, 1 + nt + tg.opp.map(ix).values] = 1.0
    w = np.ones(n)
    y = (tg.pts / tg.poss).values.astype(float)
    A = X.T @ (X * w[:, None])
    A[1:, 1:] += lam * np.eye(2 * nt)
    beta = np.linalg.solve(A, X.T @ (w * y))
    return float(beta[0]), {t: (float(beta[1 + i]), float(beta[1 + nt + i])) for t, i in ix.items()}


# ------------------------------------------------------------------ Season: per-team series + ratings
class Series:
    __slots__ = ("dates", "mat", "opp")

    def __init__(self, dates, mat, opp):
        self.dates, self.mat, self.opp = dates, mat, opp


def _dint(D):
    return int(np.datetime64(D, "D").astype("int64"))


class Season:
    """prev_tg: previous regular season team-games; cur_tg: this season's team-games (any order); ff_coef: the
    2024-25 four-factor fit.  Every query for ET date D uses only rows with date < D (searchsorted 'left')."""

    def __init__(self, prev_tg, cur_tg, ff_coef):
        self.ff_coef = np.asarray(ff_coef, float)
        self.prev, self.Lo_prev, self.Lp_prev = r2.prior_from_tg(prev_tg)
        cur_tg = cur_tg.sort_values("date", kind="stable").reset_index(drop=True)
        self.cur = cur_tg
        self.cur_dates = cur_tg.date.values.astype("datetime64[D]")
        self.R1 = r2.Ratings(self.prev, self.Lo_prev, self.Lp_prev, cur_tg)     # league level + shrunk ratios (#38)
        self.series = {}
        for t, g in cur_tg.groupby("team", sort=False):
            dates = g.date.values.astype("datetime64[D]")
            mat = g[MAT_COLS].values.astype(float)
            opp = g.opp.values
            ven = g.venue.values
            d = {"all": Series(dates, mat, opp)}
            for v in ("home", "away"):
                msk = ven == v
                d[v] = Series(dates[msk], mat[msk], opp[msk])
            self.series[t] = d
        self._kp, self._ms = {}, {}
        self.r3_rounds = []
        # priors (unshifted): {family: {team: (O, D, P)}}
        kp, rounds, ch = tn.kenpom_adjust(prev_tg, return_change=True)
        self.r3_rounds.append(("prior", rounds, ch))
        mu, ms = massey_fit(prev_tg)
        ff = {}
        for t, g in prev_tg.groupby("team"):
            s = g[FF_OWN + FF_OPP].sum().values
            ff[t] = (ff_apply(self.ff_coef, s[:7]), ff_apply(self.ff_coef, s[7:]))
        self.priors = {
            "plain": dict(self.prev),
            "kenpom": {t: (kp[t][0], kp[t][1], self.prev[t][2]) for t in self.prev},
            "massey": {t: (mu + ms[t][0], mu + ms[t][1], self.prev[t][2]) for t in self.prev},
            "fourfactor": {t: (ff[t][0], ff[t][1], self.prev[t][2]) for t in self.prev}}

    # -- per-D league fits (cached)
    def _kenpom(self, D):
        key = _dint(D)
        if key not in self._kp:
            i = int(np.searchsorted(self.cur_dates, np.datetime64(D, "D"), side="left"))
            if i == 0:
                self._kp[key] = None
            else:
                sl = self.cur.iloc[:i]
                res, rounds, ch = tn.kenpom_adjust(sl, return_change=True)
                self.r3_rounds.append((str(np.datetime64(D, "D")), rounds, ch))
                self._kp[key] = (res, float(sl.pts.sum() / sl.poss.sum()))
        return self._kp[key]

    def _massey(self, D):
        key = _dint(D)
        if key not in self._ms:
            i = int(np.searchsorted(self.cur_dates, np.datetime64(D, "D"), side="left"))
            self._ms[key] = None if i == 0 else massey_fit(self.cur.iloc[:i])
        return self._ms[key]

    def league_ratios(self, D):
        """(r_o, r_p, n_lg) as #38 (shift on)."""
        return self.R1.league(np.datetime64(D, "D"))

    def cur_rating(self, ctype, team, D, venue="all"):
        """This season's rating of `team` from rows before D, restricted to venue ('all'|'home'|'away'):
        (O, D, P, n_eff) or None if no such rows."""
        ser = self.series.get(team, {}).get(venue)
        if ser is None:
            return None
        D = np.datetime64(D, "D")
        n = int(np.searchsorted(ser.dates, D, side="left"))
        if n == 0:
            return None
        if ctype == "kenpom":
            fit = self._kenpom(D)
            res, L = fit
            if venue == "all":
                Oa, Da = res[team]
                return (Oa, Da, ser.mat[:n, 4].sum() / n, float(n))
            a = ser.mat[:n]
            o = ser.opp[:n]
            dadj = np.array([res[x][1] for x in o])
            oadj = np.array([res[x][0] for x in o])
            return ((a[:, 0] * L / dadj).sum() / a[:, 1].sum(), (a[:, 2] * L / oadj).sum() / a[:, 3].sum(),
                    a[:, 4].sum() / n, float(n))
        if ctype == "massey":
            mu, par = self._massey(D)
            if venue == "all":
                return (mu + par[team][0], mu + par[team][1], ser.mat[:n, 4].sum() / n, float(n))
            a = ser.mat[:n]
            o = ser.opp[:n]
            o_opp = np.array([par[x][0] for x in o])
            dd_opp = np.array([par[x][1] for x in o])
            # one ridge coordinate update of this team's parameter using only its games at this venue
            o_t = (a[:, 0] / a[:, 1] - mu - dd_opp).sum() / (n + MASSEY_LAMBDA)
            dd_t = (a[:, 2] / a[:, 3] - mu - o_opp).sum() / (n + MASSEY_LAMBDA)
            return (mu + o_t, mu + dd_t, a[:, 4].sum() / n, float(n))
        if ctype == "fourfactor":
            s = ser.mat[:n].sum(axis=0)
            return (ff_apply(self.ff_coef, s[5:12]), ff_apply(self.ff_coef, s[12:19]), s[4] / n, float(n))
        lo = 0
        if ctype == "season":
            w = np.ones(n)
        elif ctype in HALF_LIVES:
            w = 0.5 ** (np.arange(n - 1, -1, -1) / HALF_LIVES[ctype])        # age 0 = most recent game
        elif ctype in LAST_N:
            lo = max(0, n - LAST_N[ctype])
            w = np.ones(n - lo)
        else:
            raise ValueError(ctype)
        s = (ser.mat[lo:n, :5] * w[:, None]).sum(axis=0)
        W = float(w.sum())
        return (s[0] / s[1], s[2] / s[3], s[4] / W, W)

    def rest_cat(self, team, D):
        return rest_cat(self.series[team]["all"].dates if team in self.series else np.array([], "datetime64[D]"), D)


def rest_cat(dates, D):
    """Rest category of a team on ET date D from its sorted game dates: 0 = played D-1 (back-to-back), 1 = one day
    of rest, 2 = two days, 3 = three or more days (or no earlier game this season)."""
    i = int(np.searchsorted(dates, np.datetime64(D, "D"), side="left"))
    if i == 0:
        return 3
    gap = int((np.datetime64(D, "D") - dates[i - 1]).astype("int64"))      # >= 1
    return int(min(gap - 1, 3))


# ------------------------------------------------------------------ per-game tables
def decision_dates(games):
    return np.array([r2.decision_date(int(t), int(T)) for t, T in zip(games["tip"].values, games["T"].values)],
                    dtype="datetime64[D]")


def blend_v(prior, cur, n, k):
    """Vector #38 blend: (k prior + n cur) / (k + n); n = 0 -> prior.  prior, cur (N,3); n (N,)."""
    n = np.asarray(n, float)[:, None]
    den = np.where(n > 0, k + n, 1.0)
    return np.where(n > 0, (k * prior + n * cur) / den, prior)


class CurTable:
    """Everything that does not depend on (k, shift, split-on/off) for one rating type over a list of games:
    unshifted priors, shrunk ratios, this season's ratings overall and at the venue (n_eff = 0 -> no rating)."""

    FIELDS = ("prior_a", "prior_h", "r_o", "r_p", "cur_a", "n_a", "cur_sa", "n_sa", "cur_h", "n_h", "cur_sh", "n_sh")

    def __init__(self, Lo_prev, Lp_prev, **arrays):
        self.Lo_prev, self.Lp_prev = Lo_prev, Lp_prev
        for f in self.FIELDS:
            setattr(self, f, np.asarray(arrays[f], float))

    @classmethod
    def build(cls, season, dates, away, home, ctype):
        fam = {"kenpom": "kenpom", "massey": "massey", "fourfactor": "fourfactor"}.get(ctype, "plain")
        pri = season.priors[fam]
        N = len(dates)
        a = {f: np.zeros((N, 3)) if f.startswith(("prior", "cur")) else np.zeros(N) for f in cls.FIELDS}
        for j in range(N):
            D, ta, th = dates[j], away[j], home[j]
            r_o, r_p, _ = season.league_ratios(D)
            a["r_o"][j], a["r_p"][j] = r_o, r_p
            a["prior_a"][j], a["prior_h"][j] = pri[ta], pri[th]
            for tag, team, ven in (("a", ta, "away"), ("h", th, "home")):
                for suffix, v in (("", "all"), ("s", ven)):
                    cr = season.cur_rating(ctype, team, D, v)
                    if cr is not None:
                        a[f"cur_{suffix}{tag}"][j] = cr[:3]
                        a[f"n_{suffix}{tag}"][j] = cr[3]
        return cls(season.Lo_prev, season.Lp_prev, **a)


def features(tab, k, shift, split):
    """Blended (O, D, P) of the away and home team and league levels, per game.
    shift: prior x r_o (O, D), x r_p (P); league Lo = Lo_prev x r_o, Lp = Lp_prev x r_p (off -> r = 1).
    split: blend the team's venue-only rating (itself blended with the prior at k) with its overall blended rating at
    w = n_split / (n_split + 10)."""
    ro = tab.r_o if shift else np.ones_like(tab.r_o)
    rp = tab.r_p if shift else np.ones_like(tab.r_p)
    sc = np.stack([ro, ro, rp], axis=1)
    out = {"Lo": tab.Lo_prev * ro, "Lp": tab.Lp_prev * rp}
    for tag in ("a", "h"):
        prior = getattr(tab, f"prior_{tag}") * sc
        r = blend_v(prior, getattr(tab, f"cur_{tag}"), getattr(tab, f"n_{tag}"), k)
        if split:
            ns = getattr(tab, f"n_s{tag}")
            rs = blend_v(prior, getattr(tab, f"cur_s{tag}"), ns, k)
            w = (ns / (ns + SPLIT_C))[:, None]
            r = w * rs + (1.0 - w) * r
        out["Oa" if tag == "a" else "Oh"], out["Da" if tag == "a" else "Dh"], out["Pa" if tag == "a" else "Ph"] = \
            r[:, 0], r[:, 1], r[:, 2]
    return out


# ------------------------------------------------------------------ A x B: points and totals
def one_points(A, B, O, P, Dopp, Popp, Lo, Lp):
    """Points of the team with offence O and pace P against an opponent with defence Dopp and pace Popp."""
    if B == "termown":
        if A in A_W:
            w = A_W[A]
            return w * O * P + (1.0 - w) * Dopp * Popp
        if A == "mult":
            return (O * P) * (Dopp * Popp) / (Lo * Lp)
        if A == "add":
            return O * P + Dopp * Popp - Lo * Lp
        raise ValueError(A)
    pace = {"own": P, "avg": (P + Popp) / 2.0, "mult": P * Popp / Lp}[B]
    if A in A_W:
        rate = A_W[A] * O + (1.0 - A_W[A]) * Dopp
    elif A == "mult":
        rate = O * Dopp / Lo
    elif A == "add":
        rate = O + Dopp - Lo
    else:
        raise ValueError(A)
    return rate * pace


def total_proj(A, B, f):
    """Projected total (away + home points) from a features() dict."""
    pa = one_points(A, B, f["Oa"], f["Pa"], f["Dh"], f["Ph"], f["Lo"], f["Lp"])
    ph = one_points(A, B, f["Oh"], f["Ph"], f["Da"], f["Pa"], f["Lo"], f["Lp"])
    return pa + ph


# ------------------------------------------------------------------ rest and constants
class Rest:
    def __init__(self, cat_a, cat_h):
        self.cat_a, self.cat_h = np.asarray(cat_a, int), np.asarray(cat_h, int)
        self.nb2b = (self.cat_a == 0).astype(float) + (self.cat_h == 0).astype(float)


def rest_info(season, dates, away, home):
    return Rest([season.rest_cat(t, D) for t, D in zip(away, dates)], [season.rest_cat(t, D) for t, D in zip(home, dates)])


def cat_effects(e):
    """(3,) fitted effects of categories 0/1/2 -> lookup of length 4 (category 3+ is the reference, effect 0)."""
    return np.concatenate([np.asarray(e, float), [0.0]])


class Consts:
    """Constants per (A, C) pair, fitted on the 2024-25 walk-forward (prior 2023-24; k=20, shift on, pace avg, no split):
    c_b2b (slope per back-to-back team), e_cat (effects of rest-day categories 0/1/2; 3+ = reference), b (shrink slope).
    The intercepts of the fits are kept for the printout only; they are never applied."""

    def __init__(self, c_b2b, e_cat, b, extra=None):
        self.c_b2b, self.e_cat, self.b = np.asarray(c_b2b, float), np.asarray(e_cat, float), np.asarray(b, float)
        self.extra = extra or {}

    def to_json(self):
        return {"c_b2b": self.c_b2b.tolist(), "e_cat": self.e_cat.tolist(), "b": self.b.tolist()}

    def pair(self, a, c):
        return {"c_b2b": float(self.c_b2b[a, c]), "e_cat": [float(x) for x in self.e_cat[a, c]], "b": float(self.b[a, c])}


def fit_cat(resid, cat_a, cat_h):
    """OLS with intercept: resid = a + e0 n0 + e1 n1 + e2 n2, n_j = number of the 2 teams in category j (3+ is the
    reference).  Returns (e (3,), a, n_per_category (4,))."""
    resid = np.asarray(resid, float)
    cats = np.stack([np.asarray(cat_a), np.asarray(cat_h)])
    cnt = np.stack([(cats == j).sum(axis=0) for j in range(4)], axis=1).astype(float)      # (N, 4)
    X = np.column_stack([np.ones(len(resid)), cnt[:, :3]])
    assert np.linalg.matrix_rank(X) == 4, "rest-category regressors are collinear"
    beta, *_ = np.linalg.lstsq(X, resid, rcond=None)
    return beta[1:], float(beta[0]), cnt.sum(axis=0)


def fit_shrink(proj, actual, Lt):
    """OLS with intercept of (actual - Lt) on (proj - Lt); only the slope b is applied.  Returns (b, a)."""
    x = np.asarray(proj, float) - Lt
    y = np.asarray(actual, float) - Lt
    xc = x - x.mean()
    assert float((xc ** 2).sum()) > 0
    b = float((xc * (y - y.mean())).sum() / (xc ** 2).sum())
    return b, float(y.mean() - b * x.mean())


def fit_constants(season24, games24):
    """games24: away, home, tip, T, actual (tn.fit_games_2425).  Returns Consts."""
    dates = decision_dates(games24)
    away, home = games24.away.values, games24.home.values
    actual = games24.actual.values.astype(float)
    rest = rest_info(season24, dates, away, home)
    nA, nC = len(A_OPTS), len(C_OPTS)
    c_b2b, e_cat, b = np.zeros((nA, nC)), np.zeros((nA, nC, 3)), np.zeros((nA, nC))
    ex = {"a_b2b": np.zeros((nA, nC)), "a_cat": np.zeros((nA, nC)), "a_shrink": np.zeros((nA, nC))}
    for ci, ct in enumerate(C_OPTS):
        f = features(CurTable.build(season24, dates, away, home, ct), K_FIT, True, False)
        Lt = 2.0 * f["Lo"] * f["Lp"]
        for ai, A in enumerate(A_OPTS):
            base = total_proj(A, "avg", f)
            assert np.isfinite(base).all()
            c, a0, _, _ = tn.fit_c(actual - base, rest.nb2b)
            c_b2b[ai, ci], ex["a_b2b"][ai, ci] = c, a0
            e, a1, _ = fit_cat(actual - base, rest.cat_a, rest.cat_h)
            e_cat[ai, ci], ex["a_cat"][ai, ci] = e, a1
            b[ai, ci], ex["a_shrink"][ai, ci] = fit_shrink(base, actual, Lt)
    return Consts(c_b2b, e_cat, b, ex)


# ------------------------------------------------------------------ projections
def build_block(tab, rest, consts, ci, k, shift, split, a_list, b_list):
    """Projections for one (C, k, shift, split) cell: array (len(a_list), len(b_list), 3 [F], 2 [H], N)."""
    f = features(tab, k, shift, split)
    Lt = 2.0 * f["Lo"] * f["Lp"]
    N = len(Lt)
    out = np.empty((len(a_list), len(b_list), 3, 2, N))
    for ia, a in enumerate(a_list):
        A = A_OPTS[a]
        e = cat_effects(consts.e_cat[a, ci])
        adj = (np.zeros(N), consts.c_b2b[a, ci] * rest.nb2b, e[rest.cat_a] + e[rest.cat_h])
        for ib, bb in enumerate(b_list):
            base = total_proj(A, B_OPTS[bb], f)
            for fi in range(3):
                out[ia, ib, fi, 0] = base + adj[fi]
                # amendment: shrink the unadjusted projection, then add the rest adjustment unscaled
                out[ia, ib, fi, 1] = Lt + consts.b[a, ci] * (base - Lt) + adj[fi]
    return out


def make_tables(season, games, ctypes=C_OPTS):
    dates = decision_dates(games)
    away, home = games.away.values, games.home.values
    tabs = {ct: CurTable.build(season, dates, away, home, ct) for ct in ctypes}
    return tabs, rest_info(season, dates, away, home), dates


def build_projections(season, games, consts, progress=None):
    """All 26,880 formulas for `games`: array (26880, N), flat index = C-order over (A,B,C,D,E,F,G,H)."""
    tabs, rest, _ = make_tables(season, games)
    N = len(games)
    P = np.empty(SHAPE + (N,))
    for ci in range(len(C_OPTS)):
        for di, k in enumerate(D_OPTS):
            for ei in range(2):
                for gi in range(2):
                    P[:, :, ci, di, ei, :, gi, :, :] = build_block(
                        tabs[C_OPTS[ci]], rest, consts, ci, k, bool(ei), bool(gi), range(len(A_OPTS)), range(len(B_OPTS)))
        if progress:
            progress(ci)
    return P.reshape(N_FORMULAS, N)


def project_one(season, games, consts, spec):
    """Projections (N,) of ONE formula (spec = index tuple); computes only what that formula needs."""
    a, b, c, d, e, f, g, h = spec
    dates = decision_dates(games)
    tab = CurTable.build(season, dates, games.away.values, games.home.values, C_OPTS[c])
    rest = rest_info(season, dates, games.away.values, games.home.values)
    blk = build_block(tab, rest, consts, c, D_OPTS[d], bool(e), bool(g), [a], [b])
    return blk[0, 0, f, h]
