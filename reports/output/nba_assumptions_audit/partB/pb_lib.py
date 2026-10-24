"""Part B (J1-J11) pure functions + walkers.  DESCRIPTIVE sensitivity analysis; changes no frozen model.
Residual convention: r = ACTUAL - prediction (r > 0: model too low).  err = pred - actual (as #59/#62 use for MAE only).
Read-only reuse of e2_lib (#62).  No network."""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_CEILING

import numpy as np
import pandas as pd

import pb_common as C

E2 = C.E2
BOOT_N = 2000
BOOT_SEED = 20261023
# Verdict thresholds in points of MAE (declared here, before the numbers are looked at)
SMALL, BIG = 0.05, 0.15


# ------------------------------------------------------------------ stats helpers
def boot_mean_ci(x, n=BOOT_N, seed=BOOT_SEED):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return {"n": 0, "mean": float("nan"), "lo": float("nan"), "hi": float("nan")}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(x), size=(n, len(x)))
    m = x[idx].mean(axis=1)
    return {"n": int(len(x)), "mean": float(x.mean()), "lo": float(np.quantile(m, 0.025)), "hi": float(np.quantile(m, 0.975))}


def boot_diff_ci(x, y, n=BOOT_N, seed=BOOT_SEED):
    """Independent-groups bootstrap of mean(x) - mean(y): (diff, lo, hi)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    x, y = x[np.isfinite(x)], y[np.isfinite(y)]
    rng = np.random.default_rng(seed)
    bx = x[rng.integers(0, len(x), size=(n, len(x)))].mean(axis=1)
    by = y[rng.integers(0, len(y), size=(n, len(y)))].mean(axis=1)
    d = bx - by
    return {"diff": float(x.mean() - y.mean()), "lo": float(np.quantile(d, 0.025)), "hi": float(np.quantile(d, 0.975)),
            "n_x": int(len(x)), "n_y": int(len(y))}


def r2_corr(x, y):
    """Single-regressor OLS with intercept: (R^2, Pearson r, slope).  NaN pairs dropped."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    k = np.isfinite(x) & np.isfinite(y)
    x, y = x[k], y[k]
    if len(x) < 3 or x.std() == 0 or y.std() == 0:
        return {"n": int(len(x)), "r2": float("nan"), "r": float("nan"), "slope": float("nan")}
    r = float(np.corrcoef(x, y)[0, 1])
    slope = float(np.cov(x, y, ddof=0)[0, 1] / x.var())
    return {"n": int(len(x)), "r2": r * r, "r": r, "slope": slope}


def r2_multi(X, y):
    """OLS R^2 with intercept for a matrix X (n,k)."""
    X, y = np.asarray(X, float), np.asarray(y, float)
    k = np.isfinite(X).all(axis=1) & np.isfinite(y)
    X, y = X[k], y[k]
    A = np.column_stack([np.ones(len(y)), X])
    b, *_ = np.linalg.lstsq(A, y, rcond=None)
    res = y - A @ b
    return {"n": int(len(y)), "r2": float(1 - (res ** 2).sum() / ((y - y.mean()) ** 2).sum())}


def verdict(delta, small=SMALL, big=BIG):
    """delta = the largest MAE swing (points) across the alternatives."""
    d = abs(delta)
    if d < small:
        return "CHOICE DOESN'T MATTER"
    if d < big:
        return "MATTERS A LITTLE"
    return "MATTERS"


# ------------------------------------------------------------------ fees (J11)
def fee_expected(count, price, rate=0.07):
    """rate * C * P * (1-P) rounded UP to $0.0001 (decimal arithmetic, no float ceil error)."""
    raw = Decimal(str(rate)) * Decimal(str(count)) * Decimal(str(price)) * (Decimal(1) - Decimal(str(price)))
    q = (raw / Decimal("0.0001")).to_integral_value(rounding=ROUND_CEILING)
    return float(q * Decimal("0.0001"))


# ------------------------------------------------------------------ rolling windows (J1, J5)
def window_bounds(hd, hy, d, year, window=10, min_cur=3):
    """Same window logic as e2_lib.player_window but returns (a, b, source) indices into the player's history arrays."""
    i = int(np.searchsorted(hd, d, side="left"))
    if i == 0:
        return None
    yy = hy[:i]
    lo = int(np.searchsorted(yy, year, side="left"))
    if i - lo >= min_cur:
        return max(lo, i - window), i, "cur"
    lo2 = int(np.searchsorted(yy, year - 1, side="left"))
    hi2 = int(np.searchsorted(yy, year - 1, side="right"))
    if hi2 > lo2:
        return max(lo2, hi2 - window), hi2, "prev"
    return None


def team_window_mpg(mins, gids, aid):
    """Mean minutes of `aid` over the team's games in `gids` (a game he did not play counts 0)."""
    if not gids:
        return None
    return float(sum(mins.get((aid, g), 0.0) for g in gids)) / len(gids)


def team_last_games(box, team, d, year, n=10, min_games=5):
    """The team's last n games of THIS season strictly before d, or None when it has played fewer than min_games."""
    t = box.team_games.get(team)
    if t is None:
        return None
    td, tgid, ty = t
    i = int(np.searchsorted(td, d, side="left"))
    g = tgid[:i][ty[:i] == year]
    if len(g) < min_games:
        return None
    return [int(x) for x in g[-n:]]


def key_group(n):
    return "2+" if n >= 2 else str(int(n))


# ------------------------------------------------------------------ the shared roster walk (replicates build_injury_features' state logic)
def walk_team_games(games: pd.DataFrame, box, visit):
    """Calls visit(row, d, team, year, roster_list, last_yr) for every team-game, in date order, BEFORE that day's box rows update the
    state (strictly-before rule).  roster = athletes whose most recent box row is for `team` and from this or last season."""
    games = games.copy()
    games["d"] = [E2.day_int(x) for x in games.date]
    by_date = {d: g for d, g in games.groupby("d", sort=True)}
    brows = {d: g for d, g in box.df.groupby("d", sort=True)}
    last_team, last_yr, roster = {}, {}, {}
    for d in sorted(set(by_date) | set(brows)):
        if d in by_date:
            for r in by_date[d].itertuples(index=False):
                year = E2.year_of(r.season)
                ro = [a for a in roster.get(r.team_k, ()) if last_yr.get(a, 0) >= year - 1]
                visit(r, d, r.team_k, year, sorted(ro), last_yr)
        if d in brows:
            b = brows[d]
            for aid, team, yr in zip(b.aid.values, b.team.values, b.yr.values):
                aid = int(aid)
                old = last_team.get(aid)
                if old != team:
                    if old is not None:
                        roster[old].discard(aid)
                    roster.setdefault(team, set()).add(aid)
                    last_team[aid] = team
                last_yr[aid] = int(yr)


THRESHOLDS = (24.0, 28.0, 32.0)
WINDOWS = ("own10", "team10")
ROSTERS = ("e2", "active")


def j1_counts(games, box):
    """Per team-game: key players (mpg >= thr) and key players who did not play, for 3 thresholds x 2 windows x 2 roster rules.
    own10 = E2's window (player's own last 10 games played; last season's if < 3 games this season).
    team10 = mean minutes over the TEAM's last 10 games of this season (DNP = 0 min), needs >= 5 team games this season; with fewer
    the own10 value is used (so early-season rows equal own10).
    Roster rules.  e2 = #62's: most recent box row is for the team and from this or last season (keeps players who left the league
    after last season for the whole year).  active = a box row (played or DNP) for the team in its last 10 games of this season; when the
    team has played < 5 games this season the e2 rule is used."""
    mins = {}
    df = box.df
    pl = df[df.played]
    for a, g, m in zip(pl.aid.values, pl.game_id.values, pl.minutes.values):
        mins[(int(a), int(g))] = float(m)
    rows_in = {}
    for t, gid, a in zip(df.team.values, df.game_id.values, df.aid.values):
        rows_in.setdefault((t, int(gid)), set()).add(int(a))
    recs = []

    def visit(r, d, team, year, roster, last_yr):
        tg = team_last_games(box, team, d, year)
        played = box.played_in.get((team, int(r.game_id)), ())
        row = {"game_id": int(r.game_id), "team": r.team, "season": r.season, "team_fallback_own10": tg is None}
        active = None if tg is None else set().union(*[rows_in.get((team, g), set()) for g in tg])
        for ro in ROSTERS:
            ros = roster if (ro == "e2" or active is None) else [a for a in roster if a in active]
            mp = {}
            for a in ros:
                w = box.stats(a, d, year)
                if w is None:
                    continue
                own = w[0]
                tw = team_window_mpg(mins, tg, a) if tg is not None else own
                mp[a] = (own, tw)
            for thr in THRESHOLDS:
                for wi, win in enumerate(WINDOWS):
                    keys = [a for a, v in mp.items() if v[wi] >= thr]
                    row[f"nkey_{int(thr)}_{win}_{ro}"] = len(keys)
                    row[f"nout_{int(thr)}_{win}_{ro}"] = sum(1 for a in keys if a not in played)
        recs.append(row)

    walk_team_games(games, box, visit)
    return pd.DataFrame(recs)


def j1_summary(G, close_err_by_game, e1_err_by_game):
    """G = team-level counts -> per game (sum of both teams) -> groups 0/1/2+; E1 and close MAE and their gap per group.
    e1_err_by_game / close_err_by_game: Series game_id -> (pred - actual).  Gap = MAE(E1) - MAE(close) on games that have a close.
    Variant key = '<thr>_<window>_<roster>'; the reference for 'different group' is 28_own10_e2 (= #62's definition)."""
    cols = [c for c in G.columns if c.startswith("nkey_") or c.startswith("nout_")]
    per_game = G.groupby("game_id")[cols].sum()
    out = {}
    both = e1_err_by_game.index.intersection(close_err_by_game.index).intersection(per_game.index)
    e1 = np.abs(e1_err_by_game.loc[both].values)
    cl = np.abs(close_err_by_game.loc[both].values)
    for ro in ROSTERS:
        for thr in THRESHOLDS:
            for win in WINDOWS:
                k = f"{int(thr)}_{win}_{ro}"
                grp = np.array([key_group(x) for x in per_game.loc[both, f"nout_{k}"].values])
                res = {"mean_key_players_per_team_game": float(G[f"nkey_{k}"].mean()),
                       "mean_key_out_per_team_game": float(G[f"nout_{k}"].mean()), "groups": {}}
                for g in ("0", "1", "2+"):
                    s = grp == g
                    if s.sum() == 0:
                        continue
                    ci = boot_mean_ci(e1[s] - cl[s])
                    res["groups"][g] = {"n_games": int(s.sum()), "share": float(s.mean()), "E1_mae": float(e1[s].mean()),
                                        "close_mae": float(cl[s].mean()), "gap_E1_minus_close": ci["mean"],
                                        "gap_lo": ci["lo"], "gap_hi": ci["hi"]}
                res["_grp"] = grp
                out[k] = res
    base = out["28_own10_e2"]["_grp"]
    for k, res in out.items():
        res["share_of_games_in_different_group_vs_28_own10_e2"] = float((res["_grp"] != base).mean())
        del res["_grp"]
    return out, len(both)


# ------------------------------------------------------------------ J2-J4: E2-style residual ridge under alternative definitions
def build_features_with(games, box, reports, rot_mpg=15.0, window=10):
    """e2_lib.build_injury_features with ROT_MPG / WINDOW patched for the duration of the call (the module file is not modified)."""
    old = (E2.ROT_MPG, E2.WINDOW)
    E2.ROT_MPG, E2.WINDOW = float(rot_mpg), int(window)
    try:
        return E2.build_injury_features(games, box, reports)
    finally:
        E2.ROT_MPG, E2.WINDOW = old


def add_q_variants(F):
    """J4 helper columns: w05 = out_pts + 0.5 q_pts (Questionable at half weight); all1 = out_pts + q_pts (Questionable = Out)."""
    F = F.copy()
    for side in ("own", "opp"):
        F[f"w05_{side}"] = F[f"out_pts_{side}"] + 0.5 * F[f"q_pts_{side}"]
        F[f"all1_{side}"] = F[f"out_pts_{side}"] + F[f"q_pts_{side}"]
    return F


Q_VARIANTS = {"separate": "B", "half_weight": "W05", "ignored": "A", "counted_as_out": "ALL1"}


def _register_variants():
    E2.VARIANTS.setdefault("W05", ["w05"])
    E2.VARIANTS.setdefault("ALL1", ["all1"])


def fit_eval(train, val, variant):
    """Fit the ridge residual layer on `train` (needs columns r, e1, season + features) and score `val` -> game metrics dict."""
    _register_variants()
    m = E2.fit_variant(train, train.r.values.astype(float), variant)
    pred = val.e1.values + E2.predict_variant(m, val)
    met = E2.game_metrics(val.game_id.values, val.home.values, val.pts.values, pred)
    return m, met, pred


def paired_diff(met_alt, met_base):
    d = np.abs(met_alt["err"]) - np.abs(met_base["err"])
    return E2.paired_boot_ci(d, n_boot=BOOT_N, seed=BOOT_SEED)


# ------------------------------------------------------------------ J5/J6 helpers
def pm_history(box, years=range(2021, 2027)):
    """aid -> (d, plus_minus, yr) of games PLAYED (same filter/order as Box.hist); plus_minus parsed from '+3' strings."""
    parts = []
    for y in years:
        pb = pd.read_parquet(C.ROOT / "data" / "raw" / "nba_open" / f"player_box_{y}.parquet",
                             columns=["game_id", "athlete_id", "plus_minus", "season_type"])
        pb = pb[(pb.season_type == 2) & pb.athlete_id.notna()]
        pm = pd.to_numeric(pb.plus_minus.astype(str).str.replace("+", "", regex=False), errors="coerce")
        parts.append(pd.DataFrame({"game_id": pb.game_id.astype("int64").values, "aid": pb.athlete_id.astype("int64").values,
                                   "pm": pm.values}))
    pmdf = pd.concat(parts).drop_duplicates(["game_id", "aid"], keep="first")
    df = box.df.merge(pmdf, on=["game_id", "aid"], how="left")
    pl = df[df.played]
    return {int(a): (g.d.values.astype(np.int64), g.pm.values.astype(float), g.yr.values.astype(np.int64))
            for a, g in pl.groupby("aid", sort=False)}


def out_player_values(box, pmh, aid, d, year):
    """(ppg, mpg, pm_per_game) of a player at day d with E2's window; NaN pm when no pm values in the window."""
    h = box.hist.get(aid)
    if h is None:
        return None
    b = window_bounds(h[0], h[3], d, year)
    if b is None:
        return None
    a, e, _ = b
    ppg, mpg = float(h[2][a:e].mean()), float(h[1][a:e].mean())
    pd_, pm_, py_ = pmh[aid]
    pm_win = pm_[a:e]                                  # same ordering/filter as hist -> same indices
    pm = float(np.nanmean(pm_win)) if np.isfinite(pm_win).any() else float("nan")
    return ppg, mpg, pm


def classify_unmatched(norm, team, d, year, name2aids, aid_rows):
    """Type of an unmatched listed name at game day d."""
    aids = name2aids.get(norm, [])
    if len(aids) == 0:
        return "no_box_row_ever"
    if len(aids) > 1:
        return "ambiguous_name"
    da, ta, ya = aid_rows[aids[0]]
    if ya[0] >= year:
        return "rookie_or_new_this_season"
    i = int(np.searchsorted(da, d, side="left"))
    if i == 0:
        return "rookie_or_new_this_season"
    if ta[i - 1] != team:
        return "traded_in_or_other_team"
    return "same_team_stale_or_unexplained"


# ------------------------------------------------------------------ J7
def status_transitions(listed60, listedT):
    """listed60/listedT: {raw name: status}.  Returns list of (status60, statusT or 'not listed')."""
    return [(s, listedT.get(p, "not listed")) for p, s in listed60.items()]


# ------------------------------------------------------------------ J8
def league_to_date(tg: pd.DataFrame, n_games: int):
    """tg rows of one season sorted by (date, tip, game_id); league ortg = 100 sum pts / sum pace, pace = mean pace over the first
    n_games games (2 rows per game).  Returns (ortg, pace, implied game total = 2 ortg pace / 100)."""
    rows = tg.iloc[: 2 * n_games]
    o = 100.0 * rows.pts.sum() / rows.pace.sum()
    p = rows.pace.mean()
    return float(o), float(p), float(2 * o * p / 100.0)


# ------------------------------------------------------------------ J10 line variants
def mids(points, max_spread):
    """points: iterable of (strike, bid, ask) -> sorted [(strike, mid)] with 0 < bid <= ask < 1 and ask - bid <= max_spread."""
    out = []
    for s, b, a in points:
        try:
            s, b, a = float(s), float(b), float(a)
        except (TypeError, ValueError):
            continue
        if np.isfinite([s, b, a]).all() and 0 < b <= a < 1 and a - b <= max_spread + 1e-12:
            out.append((s, (a + b) / 2))
    return sorted(out)


def crossing(pm):
    """pm = sorted [(strike, mid)] -> strike-interpolated total where the mid crosses 0.50, or None (no crossing)."""
    if not pm or pm[0][1] < 0.5 or pm[-1][1] > 0.5:
        return None
    for (s1, m1), (s2, m2) in zip(pm, pm[1:]):
        if m1 >= 0.5 >= m2:
            return s1 if m1 == m2 else s1 + (m1 - 0.5) / (m1 - m2) * (s2 - s1)
    return pm[-1][0] if pm[-1][1] == 0.5 else None


def main_strike(pm):
    """The strike whose mid is closest to 0.50 (ties -> the lower strike)."""
    if not pm:
        return None
    return min(pm, key=lambda t: (round(abs(t[1] - 0.5), 9), t[0]))[0]


def vw_mid(bids, asks, vols):
    """Volume-weighted mean of the candle mids; falls back to the last mid when total volume is 0."""
    bids, asks, vols = (np.asarray(x, float) for x in (bids, asks, vols))
    mid = (bids + asks) / 2
    k = np.isfinite(mid)
    if not k.any():
        return None
    w = np.where(k, np.nan_to_num(vols, nan=0.0), 0.0)
    if w.sum() <= 0:
        return float(mid[k][-1])
    return float((np.where(k, mid, 0.0) * w).sum() / w.sum())
