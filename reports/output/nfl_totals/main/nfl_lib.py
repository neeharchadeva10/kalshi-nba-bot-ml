"""Research log #66 (NFL game totals) -- MAIN library.  Pure functions first, loaders/builders after.

Implements reports/output/nfl_totals/PREREGISTRATION.md (incl. Amendment 0) literally.  Read-only on every input; no network,
no kpm.execution, no keys.  Readings of ambiguous points are listed in main/README.md ("Readings needing Opus sign-off").

Contamination rule: nothing in this file prints or saves an outcome statistic.  Outcomes (final totals) enter only through
`attach_outcomes(frame, seasons)`, which refuses seasons outside the caller's allowed set.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent                                   # reports/output/nfl_totals
OUT = STUDY.parent                                    # reports/output
ROOT = STUDY.parents[2]                               # repo root
MAIN_OUT = STUDY / "main_out"
NFLV = ROOT / "data" / "raw" / "nflverse"
GAMES_CSV = NFLV / "games" / "games.csv"
PBP_DIR = NFLV / "pbp"
INJ_DIR = NFLV / "injuries"
IMPLAUSIBLE_CSV = STUDY / "data" / "pbp_running_vs_final_implausible.csv"
VERSIONS_A_CSV = STUDY / "versions_A.csv"
WEATHER_DIR = STUDY / "weather"
KALSHI_DIR = STUDY / "kalshi"

# ------------------------------------------------------------------ constants (pre-registered)
BOOT_N, BOOT_SEED = 10000, 20261023
LUCK_N, LUCK_SEED_A = 500, 20261024            # the pre-registration gives one luck seed (20261024); every part uses it
LUCK_SEED = LUCK_SEED_A
WIN_PAY = 10.0 / 11.0                          # -110 flat: win +0.909090.., loss -1, push 0
BREAK_EVEN = 110.0 / 210.0                     # 52.38% of decided bets
TEAM_MAP = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
GAME_TYPES = ("REG", "WC", "DIV", "CON", "SB")
FALLBACK_GAMETIME = "13:00"                    # only 1999 games lack a gametime (warm-up window W); documented reading
PRIOR_SHRINK = 1.0 / 3.0
L_WINDOW_DAYS, L_MIN_GAMES = 365, 50
HS = (4, 8, 16)
KS = (2, 4, 8)
EPA_K, DRIVE_H, DRIVE_K, RES_H, RES_K = 4, 8, 4, 8, 4
SLIP = 0.01
D_SEASONS = tuple(range(2006, 2015))
W_SEASONS = tuple(range(1999, 2006))
H1_SEASONS = tuple(range(2015, 2023))
H2_SEASONS = (2023, 2024, 2025)
H3_SEASONS = (2026,)
MIN_BETS_A, MIN_BETS_C = 200, 100
G_GRID = (1.5, 3.0, 4.5, 6.0)
GRES_GRID = (0.5, 1.0, 1.5, 2.0)
HK_SPLIT_DAY = "2026-01-01"
MAX_SPREAD = 0.10


def window_of(season: int) -> str:
    s = int(season)
    if s in W_SEASONS:
        return "W"
    if s in D_SEASONS:
        return "D"
    if s in H1_SEASONS:
        return "H1"
    if s in H2_SEASONS:
        return "H2"
    if s in H3_SEASONS:
        return "H3"
    return "?"


def canon(t):
    return TEAM_MAP.get(t, t)


# ------------------------------------------------------------------ games
def load_games(path=GAMES_CSV) -> pd.DataFrame:
    """games.csv with canonical team codes and kickoff (gameday + gametime read as America/New_York, converted to UTC).
    Adds: home, away, kick_s (UTC epoch seconds, int), et_date (the gameday string), window, scored."""
    g = pd.read_csv(path)
    g["home"] = g.home_team.map(canon)
    g["away"] = g.away_team.map(canon)
    gt = g.gametime.fillna(FALLBACK_GAMETIME)
    ko = pd.to_datetime(g.gameday + " " + gt).dt.tz_localize("America/New_York", ambiguous=False, nonexistent="shift_forward")
    ko = ko.dt.tz_convert("UTC")
    g["kick_s"] = ko.dt.as_unit("s").astype("int64")
    g["et_date"] = g.gameday
    g["window"] = [window_of(s) for s in g.season]
    g["scored"] = g.home_score.notna() & g.away_score.notna()
    return g.sort_values(["kick_s", "game_id"], kind="stable").reset_index(drop=True)


def universe(g: pd.DataFrame) -> pd.DataFrame:
    """Games with a closing total and game_type in REG/WC/DIV/CON/SB (Amendment 0.1)."""
    return g[g.total_line.notna() & g.game_type.isin(GAME_TYPES)].copy()


def team_game_table(g: pd.DataFrame) -> pd.DataFrame:
    """Long table: one row per (game, team) for ALL rows of g (scored or not).  pts/pts_allowed are NaN if unscored."""
    def side(prefix, opp_prefix, is_home):
        return pd.DataFrame({
            "game_id": g.game_id.values, "season": g.season.values, "game_type": g.game_type.values, "week": g.week.values,
            "kick_s": g.kick_s.values, "team": g[("home" if is_home else "away")].values,
            "opp": g[("away" if is_home else "home")].values, "is_home": int(is_home),
            "pts": g[f"{prefix}_score"].values, "pts_allowed": g[f"{opp_prefix}_score"].values,
            "qb_id": g[f"{prefix}_qb_id"].values, "coach": g[f"{prefix}_coach"].values, "rest": g[f"{prefix}_rest"].values,
            "scored": g.scored.values})
    tg = pd.concat([side("home", "away", True), side("away", "home", False)], ignore_index=True)
    return tg.sort_values(["team", "kick_s", "game_id"], kind="stable").reset_index(drop=True)


def attach_outcomes(frame: pd.DataFrame, games: pd.DataFrame, allowed_seasons) -> pd.DataFrame:
    """Add `total` (final home+away) to `frame` (needs game_id, season).  Refuses any season outside `allowed_seasons`."""
    allowed = set(int(s) for s in allowed_seasons)
    bad = set(int(s) for s in frame.season.unique()) - allowed
    if bad:
        raise RuntimeError(f"attach_outcomes refused: seasons {sorted(bad)} are outside the allowed set {sorted(allowed)}")
    tot = (games.set_index("game_id").home_score + games.set_index("game_id").away_score)
    out = frame.copy()
    out["total"] = out.game_id.map(tot).values
    return out


# ------------------------------------------------------------------ exponentially weighted ratings (Amendment 0.2 / 0.3)
def ew_ratio(num: np.ndarray, den: np.ndarray, h: float) -> float:
    """sum(w*num)/sum(w*den) with w = 0.5^(j/h), j = 0 for the LAST element (arrays are chronological, oldest first)."""
    num, den = np.asarray(num, float), np.asarray(den, float)
    if len(num) == 0:
        return float("nan")
    j = np.arange(len(num) - 1, -1, -1)
    w = 0.5 ** (j / h)
    sd = float((w * den).sum())
    return float((w * num).sum() / sd) if sd > 0 else float("nan")


def blend_prior(cur, n, prior, k):
    """O = n/(n+k)*cur + k/(n+k)*prior (n = games played this season with the stat; n = 0 -> prior)."""
    cur, n, prior = (np.asarray(x, float) for x in (cur, n, prior))
    with np.errstate(invalid="ignore", divide="ignore"):
        b = (n * np.nan_to_num(cur) + k * prior) / (n + k)
    return np.where(n > 0, b, prior)


def prior_from_end(end, lprev, shrink=PRIOR_SHRINK):
    """prior = end + (Lprev - end)/3; with no previous-season games (end NaN) prior = Lprev."""
    end = np.asarray(end, float)
    lprev = np.asarray(lprev, float)
    return np.where(np.isnan(end), lprev, end + (lprev - end) * shrink)


def ew_components(hist: pd.DataFrame, query: pd.DataFrame, specs: dict, h: float) -> dict:
    """For every query row (team, season, kick_s) and every spec name -> (num_col, den_col or None):
        cur   EW ratio over the team's games of the query's season with kick_s < query kick_s (NaN if none)
        n     number of those games with the stat valid
        prior pull of the team's season S-1 EW ratio 1/3 of the way to the league ratio of S-1 (league ratio of S-1 if the team has none)
    hist: DataFrame [team, season, kick_s, <cols>], one row per team-game that has been played; rows where the stat is NaN are skipped
    for that stat.  Strictly earlier games only: hist rows with kick_s >= the query's kick_s are never used."""
    league = {}
    for name, (nc, dc) in specs.items():
        v = hist[nc].notna() & (hist[dc].notna() if dc else True)
        hv = hist[v]
        num = hv.groupby("season")[nc].sum()
        den = hv.groupby("season")[dc].sum() if dc else hv.groupby("season")[nc].size().astype(float)
        league[name] = (num / den).to_dict()
    hs = hist.sort_values(["team", "kick_s"], kind="stable")
    by_team = {}
    for team, d in hs.groupby("team", sort=False):
        arr = {"kick": d.kick_s.values.astype("int64"), "season": d.season.values.astype("int64")}
        for name, (nc, dc) in specs.items():
            num = d[nc].values.astype(float)
            den = d[dc].values.astype(float) if dc else np.ones(len(d))
            arr[name] = (num, den, ~(np.isnan(num) | np.isnan(den)))
        by_team[team] = arr
    nq = len(query)
    res = {name: {"cur": np.full(nq, np.nan), "n": np.zeros(nq), "prior": np.full(nq, np.nan)} for name in specs}
    qt, qs, qk = query.team.values, query.season.values.astype("int64"), query.kick_s.values.astype("int64")
    end_cache = {}
    for i in range(nq):
        a = by_team.get(qt[i])
        if a is None:
            for name in specs:
                res[name]["prior"][i] = league[name].get(qs[i] - 1, np.nan)
            continue
        e = int(np.searchsorted(a["kick"], qk[i], side="left"))        # rows [0, e) are strictly earlier
        seas = a["season"][:e]
        cur_idx = np.nonzero(seas == qs[i])[0]
        for name in specs:
            num, den, ok = a[name]
            ci = cur_idx[ok[cur_idx]]
            if len(ci):
                res[name]["cur"][i] = ew_ratio(num[ci], den[ci], h)
                res[name]["n"][i] = len(ci)
            key = (qt[i], qs[i] - 1, name)
            if key not in end_cache:
                pi = np.nonzero((a["season"] == qs[i] - 1) & ok)[0]
                end_cache[key] = ew_ratio(num[pi], den[pi], h) if len(pi) else float("nan")
            lprev = league[name].get(qs[i] - 1, np.nan)
            res[name]["prior"][i] = float(prior_from_end(end_cache[key], lprev))
    return res


def league_level(sc: pd.DataFrame, q_kick_s) -> np.ndarray:
    """L for each query kickoff: mean points per team-game over the scored games with kick_s in [q - 365 days, q).  NaN if < 50 games.
    sc: scored games with kick_s, home_score, away_score."""
    sc = sc.sort_values("kick_s", kind="stable")
    k = sc.kick_s.values.astype("int64")
    cum = np.concatenate([[0.0], np.cumsum((sc.home_score.values + sc.away_score.values).astype(float))])
    q = np.asarray(q_kick_s, dtype="int64")
    lo = np.searchsorted(k, q - L_WINDOW_DAYS * 86400, side="left")
    hi = np.searchsorted(k, q, side="left")
    cnt = hi - lo
    with np.errstate(invalid="ignore", divide="ignore"):
        L = (cum[hi] - cum[lo]) / (2.0 * cnt)
    return np.where(cnt >= L_MIN_GAMES, L, np.nan)


def team_queries(gq: pd.DataFrame) -> pd.DataFrame:
    """Two rows per game (home then away) with game_id, side, team, season, kick_s -- the query rows for ew_components."""
    h = pd.DataFrame({"game_id": gq.game_id.values, "side": "home", "team": gq.home.values, "season": gq.season.values, "kick_s": gq.kick_s.values})
    a = pd.DataFrame({"game_id": gq.game_id.values, "side": "away", "team": gq.away.values, "season": gq.season.values, "kick_s": gq.kick_s.values})
    return pd.concat([h, a], ignore_index=True)


def to_wide(q: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Pivot per-team-row arrays (name -> array aligned with q) to one row per game with <name>_home / <name>_away."""
    base = q[["game_id", "side"]].copy()
    for n, v in cols.items():
        base[n] = np.asarray(v, float)
    hm = base[base.side == "home"].drop(columns="side").set_index("game_id").add_suffix("_home")
    aw = base[base.side == "away"].drop(columns="side").set_index("game_id").add_suffix("_away")
    return hm.join(aw)


PTS_SPECS = {"O": ("pts", None), "D": ("pts_allowed", None)}


def apts_table(gq: pd.DataFrame, hist_pts: pd.DataFrame, sc: pd.DataFrame, hs=HS, ks=KS) -> pd.DataFrame:
    """A-PTS predictions for the games gq: one row per game, columns game_id, L, apts_h{h}_k{k}.  hist_pts = scored team-games
    [team, season, kick_s, pts, pts_allowed]; sc = scored games (for L).  Prediction = O_home + D_away + O_away + D_home - 2L."""
    q = team_queries(gq)
    L = league_level(sc, gq.kick_s.values)
    out = pd.DataFrame({"game_id": gq.game_id.values, "L": L})
    for h in hs:
        comp = ew_components(hist_pts, q, PTS_SPECS, h)
        for k in ks:
            O = blend_prior(comp["O"]["cur"], comp["O"]["n"], comp["O"]["prior"], k)
            D = blend_prior(comp["D"]["cur"], comp["D"]["n"], comp["D"]["prior"], k)
            w = to_wide(q, {"O": O, "D": D}).reindex(gq.game_id.values)
            out[f"apts_h{h}_k{k}"] = (w.O_home.values + w.D_away.values + w.O_away.values + w.D_home.values - 2 * L)
    return out


# ------------------------------------------------------------------ pbp aggregation (Amendment 0.3 / 0.4 + the A-RES stats)
PBP_COLS = ["game_id", "play_id", "play_type", "qb_kneel", "qb_spike", "epa", "posteam", "defteam", "fixed_drive", "posteam_score",
            "posteam_score_post", "wp", "half_seconds_remaining", "game_seconds_remaining", "pass_oe", "penalty", "home_team", "away_team"]
NEUTRAL_WP = (0.2, 0.8)
NEUTRAL_MIN_HALF_SECS = 120


def implausible_ids(path=IMPLAUSIBLE_CSV) -> set:
    return set(pd.read_csv(path).game_id.astype(str))


def epa_play_mask(p: pd.DataFrame) -> pd.Series:
    """Amendment 0.3 play filter: play_type pass/run, no kneel, no spike, epa not null, posteam not null."""
    return (p.play_type.isin(["pass", "run"]) & (p.qb_kneel != 1) & (p.qb_spike != 1) & p.epa.notna() & p.posteam.notna())


def drive_table(p: pd.DataFrame) -> pd.DataFrame:
    """Drives = (game_id, fixed_drive) with posteam not null (Amendment 0.4).  The drive's team is the posteam of its FIRST play; only
    that team's plays count (the extra point after a defensive touchdown carries the other team as posteam).  Points =
    posteam_score_post of the last play - posteam_score of the first play, floored at 0.  p must be sorted by (game_id, play_id)."""
    d = p[p.posteam.notna() & p.fixed_drive.notna()]
    first_team = d.groupby(["game_id", "fixed_drive"], sort=False).posteam.transform("first")
    d = d[d.posteam == first_team]
    agg = d.groupby(["game_id", "fixed_drive"], sort=False).agg(team=("posteam", "first"), s_first=("posteam_score", "first"),
                                                              s_last=("posteam_score_post", "last")).reset_index()
    agg["pts"] = (agg.s_last - agg.s_first).clip(lower=0)
    return agg


def neutral_pairs(e: pd.DataFrame) -> pd.DataFrame:
    """e = EPA-filter plays sorted by (game_id, play_id).  For consecutive plays of the same offense in the same drive, the seconds
    between the two snaps (game clock of the earlier snap minus that of the later one), kept only if BOTH snaps are 'neutral'
    (wp in [0.2, 0.8] and half_seconds_remaining > 120) and the difference is >= 0.  Returns game_id, posteam, secs."""
    e = e[["game_id", "fixed_drive", "posteam", "wp", "half_seconds_remaining", "game_seconds_remaining"]].copy()
    e["neutral"] = (e.wp >= NEUTRAL_WP[0]) & (e.wp <= NEUTRAL_WP[1]) & (e.half_seconds_remaining > NEUTRAL_MIN_HALF_SECS)
    grp = e.groupby(["game_id", "fixed_drive", "posteam"], sort=False)
    e["prev_gsr"] = grp.game_seconds_remaining.shift(1)
    e["prev_neutral"] = grp.neutral.shift(1).fillna(False).astype(bool)
    e["secs"] = e.prev_gsr - e.game_seconds_remaining
    k = e.neutral & e.prev_neutral & e.secs.notna() & (e.secs >= 0)
    return e.loc[k, ["game_id", "posteam", "secs"]]


def aggregate_pbp_season(p: pd.DataFrame, exclude_ids) -> tuple:
    """-> (team-game table, game table).  Team-game columns: game_id, team, off_epa, def_epa, plays, proe, ns_secs, ns_n, off_drives,
    off_pts, def_drives, def_pts.  Game table: game_id, pen (penalty == 1 rows), pbp_home, pbp_away.  Excluded ids are dropped first."""
    p = p[~p.game_id.isin(set(exclude_ids))].copy()
    if p.empty:
        return pd.DataFrame(), pd.DataFrame()
    for c in ("posteam", "defteam", "home_team", "away_team"):
        p[c] = p[c].where(p[c] != "").replace(TEAM_MAP)          # '' (seen in 1999 pbp) is treated as null
    p = p.sort_values(["game_id", "play_id"], kind="stable").reset_index(drop=True)
    gm = p.groupby("game_id").agg(pen=("penalty", lambda s: float((s == 1).sum())), pbp_home=("home_team", "first"),
                                 pbp_away=("away_team", "first")).reset_index()
    e = p[epa_play_mask(p)].copy()
    e["pass_oe0"] = e.pass_oe.fillna(0.0)
    off = e.groupby(["game_id", "posteam"]).agg(off_epa=("epa", "mean"), plays=("epa", "size"), proe=("pass_oe0", "mean")).reset_index()
    off = off.rename(columns={"posteam": "team"})
    de = e.groupby(["game_id", "defteam"]).agg(def_epa=("epa", "mean")).reset_index().rename(columns={"defteam": "team"})
    ns = neutral_pairs(e).groupby(["game_id", "posteam"]).agg(ns_secs=("secs", "mean"), ns_n=("secs", "size")).reset_index()
    ns = ns.rename(columns={"posteam": "team"})
    dr = drive_table(p).merge(gm[["game_id", "pbp_home", "pbp_away"]], on="game_id", how="left")
    dr["opp"] = np.where(dr.team == dr.pbp_home, dr.pbp_away, dr.pbp_home)
    od = dr.groupby(["game_id", "team"]).agg(off_drives=("pts", "size"), off_pts=("pts", "sum")).reset_index()
    dd = dr.groupby(["game_id", "opp"]).agg(def_drives=("pts", "size"), def_pts=("pts", "sum")).reset_index().rename(columns={"opp": "team"})
    tg = off.merge(de, on=["game_id", "team"], how="outer")
    for t in (ns, od, dd):
        tg = tg.merge(t, on=["game_id", "team"], how="outer")
    return tg, gm


def load_pbp_aggregates(seasons=range(1999, 2027), pbp_dir=PBP_DIR, exclude=None):
    """-> (pbp team-game table, pbp game table) for the given seasons (reads only the PBP_COLS columns)."""
    exclude = implausible_ids() if exclude is None else exclude
    tgs, gms = [], []
    for s in seasons:
        f = Path(pbp_dir) / f"play_by_play_{s}.parquet"
        if not f.exists():
            continue
        t, m = aggregate_pbp_season(pd.read_parquet(f, columns=PBP_COLS), exclude)
        tgs.append(t)
        gms.append(m)
    return pd.concat(tgs, ignore_index=True), pd.concat(gms, ignore_index=True)


# ------------------------------------------------------------------ A-EPA / A-DRIVE / A-RES stat blocks
EPA_SPECS = {"off_epa": ("off_epa", None), "def_epa": ("def_epa", None), "plays": ("plays", None)}
DRIVE_SPECS = {"dpg": ("off_drives", None), "ppd_off": ("off_pts", "off_drives"), "ppd_def": ("def_pts", "def_drives")}
RES_SPECS = {"ns": ("ns_secs", None), "proe": ("proe", None)}


def pbp_history(pbp_tg: pd.DataFrame, tg: pd.DataFrame) -> pd.DataFrame:
    """pbp team-game stats joined to (team, season, kick_s) of the PLAYED games (history table for the pbp stats)."""
    base = tg[tg.scored][["game_id", "team", "season", "kick_s"]]
    return base.merge(pbp_tg, on=["game_id", "team"], how="inner")


def epa_features(gq: pd.DataFrame, hist: pd.DataFrame, hs=HS, k=EPA_K) -> pd.DataFrame:
    """Blended EW stats per team-game going INTO the game, for each h: columns game_id, team, side, h{h}_off_epa, h{h}_def_epa, h{h}_plays."""
    q = team_queries(gq)
    out = q[["game_id", "side", "team"]].copy()
    for h in hs:
        comp = ew_components(hist, q, EPA_SPECS, h)
        for name in EPA_SPECS:
            out[f"h{h}_{name}"] = blend_prior(comp[name]["cur"], comp[name]["n"], comp[name]["prior"], k)
    return out


def drive_features(gq: pd.DataFrame, hist: pd.DataFrame, h=DRIVE_H, k=DRIVE_K) -> pd.DataFrame:
    q = team_queries(gq)
    comp = ew_components(hist, q, DRIVE_SPECS, h)
    return to_wide(q, {n: blend_prior(comp[n]["cur"], comp[n]["n"], comp[n]["prior"], k) for n in DRIVE_SPECS}).reset_index()


def adrive_prediction(df: pd.DataFrame) -> np.ndarray:
    """Team points = mean(DPG_home, DPG_away) * (PPD_off_team + PPD_def_opp)/2; prediction = sum of both teams."""
    ed = (df.dpg_home.values + df.dpg_away.values) / 2.0
    return ed * ((df.ppd_off_home.values + df.ppd_def_away.values) / 2.0 + (df.ppd_off_away.values + df.ppd_def_home.values) / 2.0)


def res_stat_features(gq: pd.DataFrame, hist: pd.DataFrame, h=RES_H, k=RES_K) -> pd.DataFrame:
    q = team_queries(gq)
    comp = ew_components(hist, q, RES_SPECS, h)
    return to_wide(q, {n: blend_prior(comp[n]["cur"], comp[n]["n"], comp[n]["prior"], k) for n in RES_SPECS}).reset_index()


# ------------------------------------------------------------------ OLS / ridge (fitted on D rows only)
def ols_fit(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Least squares with intercept; returns [c0, c1, ...]."""
    A = np.column_stack([np.ones(len(X)), X])
    return np.linalg.lstsq(A, y, rcond=None)[0]


def ols_predict(coef: np.ndarray, X: np.ndarray) -> np.ndarray:
    return coef[0] + np.asarray(X, float) @ coef[1:]


def epa_team_design(wide: pd.DataFrame, h: int, L: np.ndarray, side: str) -> np.ndarray:
    """Design for one side's team points: [oEPA_team, dEPA_opp, plays_team, plays_opp, L].  wide has h{h}_<stat>_home/_away."""
    me, opp = ("home", "away") if side == "home" else ("away", "home")
    return np.column_stack([wide[f"h{h}_off_epa_{me}"].values, wide[f"h{h}_def_epa_{opp}"].values, wide[f"h{h}_plays_{me}"].values,
                            wide[f"h{h}_plays_{opp}"].values, np.asarray(L, float)])


def ridge_fit(X: np.ndarray, y: np.ndarray, lam: float) -> dict:
    """Ridge on z-scored features (stats from X), unpenalised intercept.  Returns mean, std, coef (on z), intercept."""
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd = np.where(sd > 0, sd, 1.0)
    Z = (X - mu) / sd
    ym = float(y.mean())
    beta = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - ym))
    return {"mu": mu, "sd": sd, "beta": beta, "intercept": ym, "lambda": float(lam)}


def ridge_predict(fit: dict, X: np.ndarray) -> np.ndarray:
    return fit["intercept"] + ((np.asarray(X, float) - fit["mu"]) / fit["sd"]) @ fit["beta"]


RIDGE_LAMBDAS = (0.1, 1.0, 10.0, 100.0, 300.0, 1000.0, 3000.0, 10000.0, 30000.0)


def loso_lambda(X: np.ndarray, y: np.ndarray, seasons: np.ndarray, lambdas=RIDGE_LAMBDAS) -> tuple:
    """Leave-one-season-out over the seasons present (D only).  Returns (best lambda by pooled held-out MSE, {lambda: mse})."""
    mse = {}
    for lam in lambdas:
        err = []
        for s in np.unique(seasons):
            tr, te = seasons != s, seasons == s
            f = ridge_fit(X[tr], y[tr], lam)
            err.append((ridge_predict(f, X[te]) - y[te]) ** 2)
        mse[float(lam)] = float(np.concatenate(err).mean())
    best = min(mse, key=lambda l: (mse[l], l))
    return best, mse


# ------------------------------------------------------------------ A-RES: schedule / coach / injury / referee features
def coach_flags(tg: pd.DataFrame, near: int = 4) -> pd.DataFrame:
    """Per team-game (all rows of tg, played or not): hc_change = this game is within `near` games of a head-coach change that happened
    this season (the change game itself is distance 0); hc_first = the coach has no earlier-SEASON game with this team in the table."""
    out = []
    for team, d in tg.groupby("team", sort=False):
        d = d.sort_values(["kick_s", "game_id"], kind="stable")
        seen_before = {}                                  # season -> set of coaches seen in earlier seasons
        coaches_by_season = {}
        for s, ds in d.groupby("season"):
            coaches_by_season[int(s)] = set(ds.coach.dropna())
        cum, seasons_sorted = set(), sorted(coaches_by_season)
        for s in seasons_sorted:
            seen_before[s] = set(cum)
            cum |= coaches_by_season[s]
        for s, ds in d.groupby("season", sort=True):
            c = ds.coach.values
            since = np.full(len(c), np.nan)
            last = -1
            for i in range(len(c)):
                if i > 0 and c[i] != c[i - 1]:
                    last = i
                since[i] = (i - last) if last >= 0 else np.nan
            out.append(pd.DataFrame({
                "game_id": ds.game_id.values, "team": team,
                "hc_change": ((since <= near) & ~np.isnan(since)).astype(int),
                "hc_first": [int(x not in seen_before[int(s)]) for x in c]}))
    return pd.concat(out, ignore_index=True)


def load_injuries(inj_dir=INJ_DIR, seasons=range(2009, 2027)) -> pd.DataFrame:
    """Injury reports, canonical team codes.  date_modified (UTC) -> `dm_et_date` (ET calendar date, string); NaT stays NaN.
    2009 (17 rows) and 2025-26 (all rows) have NO date_modified: those rows can never be shown to precede a game date."""
    frames = []
    for s in seasons:
        f = Path(inj_dir) / f"injuries_{s}.parquet"
        if not f.exists():
            continue
        d = pd.read_parquet(f)
        if "date_modified" not in d.columns:
            d["date_modified"] = pd.NaT
        d = d[["season", "week", "team", "gsis_id", "report_status", "date_modified"]].copy()
        frames.append(d)
    inj = pd.concat(frames, ignore_index=True)
    inj["team"] = inj.team.replace(TEAM_MAP)
    dm = pd.to_datetime(inj.date_modified, utc=True)
    inj["dm_et_date"] = dm.dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
    inj.loc[dm.isna(), "dm_et_date"] = np.nan
    inj["dm_s"] = (dm - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()
    inj["season"] = inj.season.astype(int)
    inj["week"] = inj.week.astype(int)
    return inj


def qb_out_flags(tg: pd.DataFrame, queries: pd.DataFrame, inj: pd.DataFrame) -> pd.DataFrame:
    """QB-out flag per (game_id, team) of `queries` (game_id, team, season, week, kick_s, et_date): the starting QB (games.csv qb id) of
    the team's PREVIOUS PLAYED game (any season) is listed report_status Out or Doubtful in the LATEST dated report of that
    (season, week, team) whose ET date is strictly before the game's ET date.  Undated reports are never used.  0 if no report/QB."""
    played = tg[tg.scored].sort_values(["team", "kick_s"], kind="stable")
    prev_qb = {}
    for team, d in played.groupby("team", sort=False):
        prev_qb[team] = (d.kick_s.values.astype("int64"), d.qb_id.values)
    qbs = []
    for t, k in zip(queries.team.values, queries.kick_s.values.astype("int64")):
        a = prev_qb.get(t)
        if a is None:
            qbs.append(None)
            continue
        i = int(np.searchsorted(a[0], k, side="left")) - 1
        qbs.append(a[1][i] if i >= 0 else None)
    q = queries.copy()
    q["qb"] = qbs
    q = q[q.qb.notna()]
    m = q.merge(inj[inj.dm_et_date.notna()], left_on=["season", "week", "team", "qb"], right_on=["season", "week", "team", "gsis_id"], how="inner")
    m = m[m.dm_et_date < m.et_date]
    m = m.sort_values(["game_id", "team", "dm_s"], kind="stable").groupby(["game_id", "team"]).tail(1)
    m["qb_out"] = m.report_status.isin(["Out", "Doubtful"]).astype(int)
    res = queries[["game_id", "team"]].merge(m[["game_id", "team", "qb_out"]], on=["game_id", "team"], how="left")
    res["qb_out"] = res.qb_out.fillna(0).astype(int)
    return res


def referee_penalty_feature(g: pd.DataFrame, pbp_gm: pd.DataFrame, min_games: int = 10) -> pd.DataFrame:
    """Per game: the referee's mean penalties per game (pbp penalty == 1 plays) over his games in seasons S-2 and S-1; the league mean of
    those two seasons' games if he has fewer than `min_games` such games (or no referee)."""
    x = g[["game_id", "season", "referee"]].merge(pbp_gm[["game_id", "pen"]], on="game_id", how="left")
    done = x[x.pen.notna()]
    out = []
    for s, gs in x.groupby("season"):
        pool = done[done.season.isin([s - 2, s - 1])]
        league = pool.pen.mean() if len(pool) else np.nan
        stats = pool.groupby("referee").pen.agg(["mean", "size"])
        for gid, ref in zip(gs.game_id.values, gs.referee.values):
            if isinstance(ref, str) and ref in stats.index and stats.loc[ref, "size"] >= min_games:
                out.append((gid, float(stats.loc[ref, "mean"])))
            else:
                out.append((gid, float(league) if not np.isnan(league) else np.nan))
    return pd.DataFrame(out, columns=["game_id", "ref_pen"])


# ------------------------------------------------------------------ P&L
def sb_pnl(side_over, actual, line) -> np.ndarray:
    """-110 flat.  side_over True = OVER.  win +10/11, loss -1, push (actual == line) 0."""
    actual, line = np.asarray(actual, float), np.asarray(line, float)
    over = np.where(actual > line, WIN_PAY, np.where(actual < line, -1.0, 0.0))
    under = np.where(actual < line, WIN_PAY, np.where(actual > line, -1.0, 0.0))
    return np.where(np.asarray(side_over, bool), over, under)


def summarize_sb(pnl) -> dict:
    pnl = np.asarray(pnl, float)
    n = int(len(pnl))
    wins, losses = int((pnl > 0).sum()), int((pnl < 0).sum())
    return {"n": n, "mean": (float(pnl.mean()) if n else float("nan")), "wins": wins, "losses": losses, "pushes": n - wins - losses,
            "win_rate_decided": (wins / (wins + losses) if wins + losses else float("nan")), "total": float(pnl.sum())}


def kalshi_fee(price: float) -> float:
    """0.07 * P * (1 - P) rounded UP to $0.0001 (P = the all-in price paid, ask + 1c)."""
    x = 0.07 * price * (1.0 - price)
    return math.ceil(round(x * 1e4, 6)) / 1e4


def kalshi_net(ask: float, win) -> float:
    """Per contract: payout (1/0) - P - fee, where P = ask + 1c slippage (capped at 1.0) and fee = kalshi_fee(P)."""
    p = min(ask + SLIP, 1.0)
    return float(win) - p - kalshi_fee(p)


def kalshi_net_arr(ask, win) -> np.ndarray:
    ask, win = np.asarray(ask, float), np.asarray(win, float)
    return np.array([kalshi_net(a, w) if np.isfinite(a) and np.isfinite(w) else np.nan for a, w in zip(ask, win)])


# ------------------------------------------------------------------ bootstrap / luck tests
def boot_ci(x, n_boot=BOOT_N, seed=BOOT_SEED, chunk=500):
    """Game bootstrap of the mean (each bet = one game): (mean, 2.5th, 97.5th percentile).  Deterministic given the seed."""
    x = np.asarray(x, float)
    if len(x) == 0:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot)
    for lo in range(0, n_boot, chunk):
        k = min(chunk, n_boot - lo)
        means[lo:lo + k] = x[rng.integers(0, len(x), size=(k, len(x)))].mean(axis=1)
    return float(x.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def boot_ci_by_game(x, game_ids, n_boot=BOOT_N, seed=BOOT_SEED):
    """Bootstrap resampling GAMES (for Part C, where one game has several bets); statistic = mean over all bets of the drawn games."""
    x = np.asarray(x, float)
    gid = np.asarray(game_ids)
    if len(x) == 0:
        return float("nan"), float("nan"), float("nan")
    codes, uniq = pd.factorize(gid)
    ng = len(uniq)
    sums = np.bincount(codes, weights=x, minlength=ng)
    cnts = np.bincount(codes, minlength=ng).astype(float)
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot)
    for lo in range(0, n_boot, 500):
        k = min(500, n_boot - lo)
        idx = rng.integers(0, ng, size=(k, ng))
        means[lo:lo + k] = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    return float(x.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def luck_random_sides(pnl_over, pnl_under, actual_mean, n=LUCK_N, seed=LUCK_SEED) -> dict:
    """Part A luck test: the SAME bet games, a fair coin per game for the side, same costs; n copies.  frac = share of copies whose mean
    P&L per bet is >= the actual."""
    pnl_over, pnl_under = np.asarray(pnl_over, float), np.asarray(pnl_under, float)
    if len(pnl_over) == 0:
        return {"copies": n, "frac_copies_ge_actual": float("nan"), "copies_mean": float("nan"), "actual_mean": actual_mean, "n_bets": 0}
    rng = np.random.default_rng(seed)
    side = rng.integers(0, 2, (n, len(pnl_over))).astype(bool)
    means = np.where(side, pnl_over, pnl_under).mean(axis=1)
    return {"copies": n, "frac_copies_ge_actual": float((means >= actual_mean - 1e-12).mean()), "copies_mean": float(means.mean()),
            "actual_mean": float(actual_mean), "n_bets": int(len(pnl_over))}


def luck_random_games(under_pnl_by_season: dict, n_by_season: dict, actual_mean, n=LUCK_N, seed=LUCK_SEED) -> dict:
    """Part B luck test: per season the same number of UNDER bets as the real rule has, on random games of that season (without
    replacement); pooled mean P&L per bet of each copy vs the actual.  under_pnl_by_season: season -> UNDER P&L of every game."""
    rng = np.random.default_rng(seed)
    tot = np.zeros(n)
    nb = 0
    for s in sorted(n_by_season):
        k = int(n_by_season[s])
        if k == 0:
            continue
        pool = np.asarray(under_pnl_by_season[s], float)
        assert k <= len(pool), f"{s}: {k} real bets but only {len(pool)} games"
        keys = rng.random((n, len(pool)))
        idx = np.argpartition(keys, k - 1, axis=1)[:, :k] if k < len(pool) else np.tile(np.arange(len(pool)), (n, 1))
        tot += pool[idx].sum(axis=1)
        nb += k
    if nb == 0:
        return {"copies": n, "frac_copies_ge_actual": float("nan"), "copies_mean": float("nan"), "actual_mean": actual_mean, "n_bets": 0}
    means = tot / nb
    return {"copies": n, "frac_copies_ge_actual": float((means >= actual_mean - 1e-12).mean()), "copies_mean": float(means.mean()),
            "actual_mean": float(actual_mean), "n_bets": int(nb)}


def luck_side_preserving(over_pool: dict, under_pool: dict, n_over: dict, n_under: dict, actual_mean, n=LUCK_N, seed=LUCK_SEED) -> dict:
    """Side-preserving luck test (Amendment 6, M-2): per season the SAME number of OVER and UNDER bets as the real version, on random
    games of that season (OVER and UNDER draws independent, each without replacement).  over_pool[s] / under_pool[s] = the OVER / UNDER
    P&L of every game of season s.  frac = share of copies whose pooled mean P&L per bet is >= the actual."""
    rng = np.random.default_rng(seed)
    tot, nb = np.zeros(n), 0
    for s in sorted(set(n_over) | set(n_under)):
        for pool, k in ((over_pool, int(n_over.get(s, 0))), (under_pool, int(n_under.get(s, 0)))):
            if k == 0:
                continue
            p = np.asarray(pool[s], float)
            assert k <= len(p), f"{s}: {k} bets but only {len(p)} games"
            keys = rng.random((n, len(p)))
            idx = np.argpartition(keys, k - 1, axis=1)[:, :k] if k < len(p) else np.tile(np.arange(len(p)), (n, 1))
            tot += p[idx].sum(axis=1)
            nb += k
    if nb == 0:
        return {"copies": n, "frac_copies_ge_actual": float("nan"), "copies_mean": float("nan"), "actual_mean": actual_mean, "n_bets": 0}
    means = tot / nb
    return {"copies": n, "frac_copies_ge_actual": float((means >= actual_mean - 1e-12).mean()), "copies_mean": float(means.mean()),
            "actual_mean": float(actual_mean), "n_bets": int(nb)}


ERA_BIAS_LABEL = "not separable from era over/under bias"


def side_controls(games: pd.DataFrame, bets: pd.DataFrame, actual_mean) -> dict:
    """Reporting controls (labels only).  games: every game of the evaluated windows (season, total, close); bets: the version's bets
    (season, over, pnl).  Returns the side mix, always-OVER / always-UNDER summaries on all those games, and the side-preserving luck test."""
    g = games[np.isfinite(games.total.values.astype(float)) & np.isfinite(games.close.values.astype(float))]
    ov = sb_pnl(np.ones(len(g), bool), g.total.values, g.close.values)
    un = sb_pnl(np.zeros(len(g), bool), g.total.values, g.close.values)
    over_pool = {s: ov[(g.season == s).values] for s in g.season.unique()}
    under_pool = {s: un[(g.season == s).values] for s in g.season.unique()}
    no = bets[bets.over].groupby("season").size().to_dict()
    nu = bets[~bets.over].groupby("season").size().to_dict()
    luck = luck_side_preserving(over_pool, under_pool, no, nu, actual_mean) if len(bets) else luck_random_sides([], [], float("nan"))
    return {"side_mix": {"n_over": int(bets.over.sum()), "n_under": int((~bets.over).sum())}, "always_over": summarize_sb(ov),
            "always_under": summarize_sb(un), "luck_side_preserving": luck}


def luck_random_strikes(per_game: list, k_by_game: list, actual_mean, n=LUCK_N, seed=LUCK_SEED) -> dict:
    """Part C luck test.  per_game[i] = (net_yes, net_no) arrays over game i's eligible strikes (net per contract of buying YES / NO at the
    real prices); k_by_game[i] = the number of bets the real version has in game i.  Each copy draws k_i random strikes without
    replacement per game, each with a random side; pooled mean net per contract vs actual."""
    rng = np.random.default_rng(seed)
    tot, nb = np.zeros(n), 0
    for (ny, nn), k in zip(per_game, k_by_game):
        k = int(k)
        if k == 0:
            continue
        ny, nn = np.asarray(ny, float), np.asarray(nn, float)
        m = len(ny)
        k = min(k, m)
        keys = rng.random((n, m))
        idx = np.argpartition(keys, k - 1, axis=1)[:, :k] if k < m else np.tile(np.arange(m), (n, 1))
        side = rng.integers(0, 2, (n, k)).astype(bool)
        tot += np.where(side, ny[idx], nn[idx]).sum(axis=1)
        nb += k
    if nb == 0:
        return {"copies": n, "frac_copies_ge_actual": float("nan"), "copies_mean": float("nan"), "actual_mean": actual_mean, "n_bets": 0}
    means = tot / nb
    return {"copies": n, "frac_copies_ge_actual": float((means >= actual_mean - 1e-12).mean()), "copies_mean": float(means.mean()),
            "actual_mean": float(actual_mean), "n_bets": int(nb)}


def choose_best(rows: pd.DataFrame, min_bets: int):
    """rows: version, n, mean_pnl (enumeration order).  Highest mean per bet among versions with n >= min_bets; ties (1e-12) -> more bets,
    then earlier enumeration order.  Returns the chosen row (Series) or None."""
    el = rows[(rows.n >= min_bets) & rows.mean_pnl.notna()].copy()
    if el.empty:
        return None
    el["_o"] = np.arange(len(el))
    el["_m"] = el.mean_pnl.round(12)
    return el.sort_values(["_m", "n", "_o"], ascending=[False, False, True], kind="stable").iloc[0].drop(["_o", "_m"])


# ------------------------------------------------------------------ audit hashes
def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


OWN_AUDIT = ("nfl_lib.py", "part_a.py", "part_b.py", "part_c.py", "build_features.py", "gates.py", "run_select.py", "run_test.py",
             "make_audit_hashes.py", "../PREREGISTRATION.md", "../versions_A.csv",
             "../main_out/game_table.parquet", "../main_out/epa_teamfeatures.parquet", "../main_out/epa_ols.json",
             "../main_out/ares_ridge.json", "../main_out/fair_model.npz", "../main_out/gates.json", "../results_select.json")
INPUT_AUDIT = ("../../../../data/raw/nflverse/games/games.csv", "../../../../data/raw/nflverse/pbp/manifest.json",
               "../../../../data/raw/nflverse/injuries/manifest.json", "../data/pbp_running_vs_final_implausible.csv",
               "../kalshi/markets.parquet", "../kalshi/events.parquet", "../kalshi/candles_1h.parquet", "../kalshi/candles_1m.parquet",
               "../weather/wind_forecasts.parquet", "../weather/stadiums.csv")
AUDIT_FILES = OWN_AUDIT + INPUT_AUDIT


def audit_hashes(base=HERE, files=AUDIT_FILES) -> dict:
    missing = [f for f in files if not (Path(base) / f).exists()]
    if missing:
        raise FileNotFoundError(f"audit input files missing: {missing}")
    return {"sha256": {f: sha256_file(Path(base) / f) for f in files}}


def check_audit_ok(study=STUDY, base=HERE, files=AUDIT_FILES):
    """Refuse unless AUDIT_OK (JSON with a 'sha256' map covering every audited file with matching hashes) AND test_started.json exist,
    and results_test.json does not (the holdout stage runs once).  Nothing outcome-related is loaded before this passes."""
    study = Path(study)
    p = study / "AUDIT_OK"
    if not p.exists():
        raise SystemExit("REFUSED: AUDIT_OK not found (only the Opus leak audit creates it).")
    if not (study / "test_started.json").exists():
        raise SystemExit("REFUSED: test_started.json not found (the Opus marker that starts the holdout stage).")
    if (study / "results_test.json").exists():
        raise SystemExit("REFUSED: results_test.json exists; the holdout stage runs once.")
    try:
        got = json.loads(p.read_text())["sha256"]
    except Exception:  # noqa: BLE001
        raise SystemExit("REFUSED: AUDIT_OK is not JSON with a 'sha256' map.")
    for f in files:
        if f not in got:
            raise SystemExit(f"REFUSED: AUDIT_OK does not cover {f}.")
        fp = Path(base) / f
        if not fp.exists() or sha256_file(fp) != got[f]:
            raise SystemExit(f"REFUSED: {f} changed or is missing since the audit (hash mismatch).")
