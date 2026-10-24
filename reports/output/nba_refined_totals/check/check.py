"""Independent check implementation of research log #65 (nba_refined_totals).
Written from PREREGISTRATION.md (+ #62 amendments) only. Nothing is read from main/ or main_out/.
Allowed shared pieces: nba_score_model_e2/check/check_features.py (report loading, report selection, exact name
matching), e3_features.parquet c60 columns, frozen_e3.json, e1 per-team predictions, fa_lib (F42 projections).
Run:  .venv/Scripts/python.exe check.py b|flags|lean|versions|all
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
ROOT = HERE.parents[3]
STUDY = ROOT / "reports" / "output" / "nba_refined_totals"
RAW = ROOT / "data" / "raw" / "nba_open"
E3 = ROOT / "reports" / "output" / "nba_score_model_e3"
E1F = ROOT / "reports" / "output" / "nba_assumptions_audit" / "partB" / "_cache" / "e1_2021_26.parquet"
CLOSING = ROOT / "data" / "raw" / "nba_odds_samples" / "closing_lines_odds_api.parquet"
ET = "America/New_York"
WIN = 100 / 110          # +0.90909 per 1 staked


# =============================================================== pure helpers (unit tested)
def player_window_stats(prows: pd.DataFrame, xgames: list, g_tip, X: str, M: float):
    """Part B candidate table for team X before game g.
    prows: player box rows (regular season, this season only, NBA franchises only), columns
           athlete_id, team, game_id, tip, minutes, points (minutes NaN -> 0), played (bool).
    xgames: X's regular-season game_ids of this season strictly before g, in time order.
    Returns DataFrame (athlete_id, mpg, fresh_ppg, fresh) for players whose current team is X, window mpg >= M
    and fresh.  Returns empty if X has played < 5 games this season."""
    cols = ["athlete_id", "mpg", "fresh_ppg"]
    if len(xgames) < 5:
        return pd.DataFrame(columns=cols)
    prev = prows[prows.tip < g_tip]                                   # rows strictly before g (g's own rows excluded)
    prev = prev.sort_values(["tip", "game_id"], kind="stable")
    cur = prev.groupby("athlete_id").team.last()                      # most recent row (played or DNP) this season
    mine = set(cur.index[cur == X])
    win = xgames[-10:]
    last3 = xgames[-3:]
    n = len(win)
    r = prev[(prev.team == X) & prev.athlete_id.isin(mine)]           # minutes counted only while with X in X games
    w = r[r.game_id.isin(win)]
    mins = w.groupby("athlete_id").minutes.sum() / n
    pts = w.groupby("athlete_id").points.sum() / n
    fresh = set(r[r.game_id.isin(last3) & r.played].athlete_id)
    rows = []
    for a in mine:
        mp = float(mins.get(a, 0.0))
        if mp >= M and a in fresh:
            rows.append((a, mp, float(pts.get(a, 0.0))))
    return pd.DataFrame(rows, columns=cols)


def absent_box(cands: pd.DataFrame, rows_g: pd.DataFrame, X: str):
    """Box flavor: candidate is absent if he has no row with minutes > 0 for X in game g."""
    played = set(rows_g[(rows_g.team == X) & rows_g.played].athlete_id)
    return cands[~cands.athlete_id.isin(played)]


def absent_report(cands: pd.DataFrame, listed_ids: set):
    return cands[cands.athlete_id.isin(listed_ids)]


def s_value(absentees: pd.DataFrame) -> float:
    return float(absentees.fresh_ppg.sum()) if len(absentees) else 0.0


def rolling_b(dates: np.ndarray, resid: np.ndarray, target_date, min_n: int = 30):
    """b = mean(resid) over games with date STRICTLY before target_date (NaN resid ignored); 0 if < min_n such games.
    Returns (b, n)."""
    dates = np.asarray(dates, dtype="datetime64[ns]")
    m = (dates < np.datetime64(target_date, "ns")) & np.isfinite(resid)
    n = int(m.sum())
    return (float(resid[m].mean()) if n >= min_n else 0.0), n


def ridge_fit(Z: np.ndarray, y: np.ndarray, lam: float):
    """min sum (y - b0 - Z b)^2 + lam |b|^2, intercept unpenalised.  Z must already be standardised (mean 0), so
    b0 = mean(y).  Convention reproduces frozen_e3.json exactly when fitted on 2021-22 + 2022-23."""
    ybar = y.mean()
    zc = Z - Z.mean(0)
    beta = np.linalg.solve(zc.T @ zc + lam * np.eye(Z.shape[1]), zc.T @ (y - ybar))
    b0 = ybar - Z.mean(0) @ beta
    return float(b0), beta


def bet_decision(model_adj: float, line: float, G: float):
    if model_adj - line >= G:
        return "OVER"
    if line - model_adj >= G:
        return "UNDER"
    return None


def bet_pnl(side: str, total: float, line: float) -> float:
    if total == line:
        return 0.0
    over = total > line
    win = over if side == "OVER" else (not over)
    return WIN if win else -1.0


def parse_version(v: str):
    kind, model, lean, g, skips = v.split("|")
    sk = set() if skips == "none" else set(skips.split("+"))
    return kind, model, lean, int(g[1:]), sk


def scope_value(s_home: float, s_away: float, scope: str) -> float:
    return s_home + s_away if scope == "game" else max(s_home, s_away)


# =============================================================== data loading
def load_box_all():
    from importlib import util
    cf = load_cf()
    pb, tb = cf.load_box()                       # pb: 2021..2026 regular season; tb: 2022..2026 regular
    tb21 = pd.read_parquet(RAW / "team_box_2021.parquet")
    tb21 = tb21[tb21.season_type == 2].copy()
    tb21["tip"] = pd.to_datetime(tb21.game_date_time, utc=True).dt.tz_convert(ET)
    tb21["etdate"] = tb21.tip.dt.tz_localize(None).dt.normalize()
    n30 = cf.nba30(tb)
    tball = pd.concat([tb21, tb], ignore_index=True)
    return cf, pb, tball, n30


_CF = None


def load_cf():
    global _CF
    if _CF is None:
        sys.path.insert(0, str(ROOT / "reports" / "output" / "nba_score_model_e2" / "check"))
        import check_features as cf
        cf.OUT = OUT                            # keep its creation-date cache inside check/out
        _CF = cf
    return _CF


def team_game_table(tball, n30):
    t = tball[tball.team_abbreviation.isin(n30) & tball.opponent_team_abbreviation.isin(n30)]
    t = t[["season", "game_id", "team_abbreviation", "opponent_team_abbreviation", "tip", "etdate",
           "team_score", "opponent_team_score"]].copy()
    t["game_id"] = t.game_id.astype("int64")
    return t.sort_values(["season", "tip", "game_id", "team_abbreviation"]).reset_index(drop=True)


# =============================================================== 1. b_check
def run_b():
    cf, pb, tball, n30 = load_box_all()
    pb = pb[pb.team_abbreviation.isin(n30)].copy()          # All-Star / exhibition rows excluded everywhere
    pb["game_id"] = pb.game_id.astype("int64")
    pb["minutes"] = pb.minutes.fillna(0.0)
    pb["points"] = pb.points.fillna(0.0)
    pb["team"] = pb.team_abbreviation
    tg = team_game_table(tball, n30)
    reps = cf.load_reports()
    calc = cf.Calc(pb, tball[tball.season >= 2022], n30, reps)
    samp = pd.read_csv(STUDY / "sample_teamgames.csv")
    out = []
    pb_by_season = {s: g for s, g in pb.groupby("season")}
    for r in samp.itertuples():
        gid, X = int(r.game_id), r.team_abbreviation
        grow = tg[(tg.game_id == gid) & (tg.team_abbreviation == X)].iloc[0]
        season, tip, etdate, opp = int(grow.season), grow.tip, grow.etdate, grow.opponent_team_abbreviation
        xg = tg[(tg.season == season) & (tg.team_abbreviation == X) & ((tg.tip < tip))]
        xgames = list(xg.game_id)
        ps = pb_by_season[season]
        rows_g = ps[ps.game_id == gid]
        row = dict(game_id=gid, team_abbreviation=X, season_file=season, n_team_games_before=len(xgames))
        listed = None
        if season >= 2022:
            D = tip - pd.Timedelta(minutes=60)
            ri = calc.find_report({X, opp}, etdate, D)
            row["report_file"] = calc.reps[ri][1] if ri >= 0 else ""
            listed = set()
            if ri >= 0:
                names, _ = calc.report_rows(ri, etdate, X)
                for nm, st in names.items():
                    if st in ("out", "doubtful"):
                        aid = calc.match(nm, X, season, etdate)
                        if aid is not None:
                            listed.add(aid)
        for M in (20, 28):
            c = player_window_stats(ps[ps.tip < tip], xgames, tip, X, M)
            for flavor in ("report", "box"):
                if flavor == "report":
                    if listed is None:
                        ab = None
                    else:
                        ab = absent_report(c, listed)
                else:
                    ab = absent_box(c, rows_g, X)
                key = f"{flavor}_M{M}"
                if ab is None:
                    row[f"S_{key}"] = np.nan
                    row[f"ids_{key}"] = ""
                    row[f"ppg_{key}"] = ""
                    row[f"mpg_{key}"] = ""
                    continue
                ab = ab.sort_values("athlete_id")
                row[f"S_{key}"] = s_value(ab)
                row[f"ids_{key}"] = ";".join(str(int(a)) for a in ab.athlete_id)
                row[f"ppg_{key}"] = ";".join(repr(float(v)) for v in ab.fresh_ppg)
                row[f"mpg_{key}"] = ";".join(repr(float(v)) for v in ab.mpg)
        out.append(row)
    df = pd.DataFrame(out)
    first = ["game_id", "team_abbreviation", "S_report_M20", "S_report_M28", "S_box_M20", "S_box_M28"]
    df = df[first + [c for c in df.columns if c not in first]]
    df.to_csv(OUT / "b_check.csv", index=False)
    print("b_check rows", len(df))
    return df


# =============================================================== 2. flags
def run_flags():
    f = pd.read_parquet(E3 / "e3_features.parquet")
    f["game_id"] = f.game_id.astype("int64")
    cf, pb, tball, n30 = load_box_all()
    tg = team_game_table(tball, n30)
    samp = pd.read_csv(STUDY / "sample_games.csv")
    rows = []
    for r in samp.itertuples():
        gid = int(r.game_id)
        two = f[f.game_id == gid]
        assert len(two) == 2, gid
        q = bool((two.n_q_rot_c60 >= 1).any())
        new = bool((two.new_share_own_c60 >= 0.30).any())
        g2 = tg[tg.game_id == gid]
        early = False
        counts = []
        for t in g2.itertuples():
            n = int(((tg.team_abbreviation == t.team_abbreviation) & (tg.season == t.season) & (tg.etdate < t.etdate)).sum())
            counts.append(n)
            early |= n < 10
        rows.append(dict(game_id=gid, season=r.season, q_skip=q, new_skip=new, early_skip=bool(early),
                         n_q_max=int(two.n_q_rot_c60.max()), new_share_max=float(two.new_share_own_c60.max()),
                         min_prior_games=min(counts)))
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "flags_check.csv", index=False)
    print("flags rows", len(df), df[["q_skip", "new_skip", "early_skip"]].sum().to_dict())
    return df


# =============================================================== model totals per game
def e3_team_residual(f: pd.DataFrame, e1: pd.DataFrame):
    """Return DataFrame game_id, team, e1pred, resid_frozen (c60 features) and resid_cross (2022-23 cross-fit)."""
    j = json.load(open(E3 / "frozen_e3.json"))["ridge"]
    feats = j["features"]
    m = f.merge(e1[["game_id", "team", "pred", "pts"]], on=["game_id", "team"], how="inner")
    X = m[feats].values.astype(float)
    mu, sd, coef = np.array(j["mu"]), np.array(j["sd"]), np.array(j["coef"])
    m["resid_frozen"] = j["b0"] + ((X - mu) / sd) @ coef
    tr = m[m.season == "2021-22"]
    Xt = tr[feats].values.astype(float)
    mu2, sd2 = Xt.mean(0), Xt.std(0, ddof=0)
    b0, beta = ridge_fit((Xt - mu2) / sd2, (tr.pts - tr.pred).values, 1000.0)
    m["resid_cross"] = b0 + ((X - mu2) / sd2) @ beta
    return m


def model_totals(with_f42=True):
    """DataFrame per regular-season game: game_id, season, date (ET), actual, E1, E3 (NaN where undefined), F42."""
    e1 = pd.read_parquet(E1F)
    e1["game_id"] = e1.game_id.astype("int64")
    f = pd.read_parquet(E3 / "e3_features.parquet")
    f["game_id"] = f.game_id.astype("int64")
    m = e3_team_residual(f, e1)
    m["e3_team"] = np.where(m.season == "2021-22", np.nan,
                            np.where(m.season == "2022-23", m.pred + m.resid_cross, m.pred + m.resid_frozen))
    g = m.groupby("game_id").agg(season=("season", "first"), date=("date", "first"), actual=("pts", "sum"),
                                 E1=("pred", "sum"), E3=("e3_team", lambda x: x.sum(min_count=2)), n=("pred", "size"))
    assert (g.n == 2).all()
    g = g.drop(columns="n").reset_index()
    g["F42"] = np.nan
    if with_f42:
        g = add_f42(g)
    return g


def add_f42(g):
    sys.path.insert(0, str(ROOT / "reports" / "output" / "nba_formula_accuracy"))
    import fa_lib as fa
    import grid_lib as gl
    spec = tuple(json.load(open(ROOT / "reports" / "output" / "nba_totals_grid" / "winner_frozen.json"))["spec"])
    cache = OUT / "f42_cache.parquet"
    if cache.exists():
        c = pd.read_parquet(cache)
    else:
        parts = []
        for season in ("2022-23", "2023-24", "2024-25", "2025-26"):      # 2021-22 has no S-2 box file for constants
            w = fa.build_world(season)
            games = fa.games_frame(w)
            p = gl.project_one(w.season, games, w.consts, spec)
            parts.append(pd.DataFrame({"game_id": games.game_id.astype("int64").values, "F42": np.asarray(p, float)}))
        c = pd.concat(parts)
        c.to_parquet(cache)
    g = g.drop(columns="F42").merge(c, on="game_id", how="left")
    return g


def lean_series(g, model, season_label):
    s = g[(g.season == season_label)].copy()
    s = s[np.isfinite(s[model]) & np.isfinite(s.actual)]
    return s.date.values, (s.actual - s[model]).values


# =============================================================== 3. lean
def run_lean(g=None):
    g = model_totals() if g is None else g
    samp = pd.read_csv(STUDY / "sample_lean_dates.csv")
    rows = []
    for r in samp.itertuples():
        season_label = f"{r.season_file - 1}-{str(r.season_file)[2:]}"
        for model in ("F42", "E1", "E3"):
            d, res = lean_series(g, model, season_label)
            if len(d) == 0:
                continue
            b, n = rolling_b(d, res, pd.Timestamp(r.game_date))
            rows.append(dict(season_file=r.season_file, game_date=r.game_date, model=model, b=b, n=n))
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "lean_check.csv", index=False)
    print("lean rows", len(df))
    return df


# =============================================================== 4. versions (Part A discovery)
def closing_lines():
    p = pd.read_parquet(CLOSING)
    lead = (pd.to_datetime(p.commence_time, utc=True) - pd.to_datetime(p.snapshot, utc=True)).dt.total_seconds()
    p = p[lead > 0]                                          # drop snapshot >= commence
    p = p[(p.over_under > 180) & (p.over_under < 275)]       # outside (180, 275) dropped
    p = p.drop_duplicates("game_id")
    return pd.Series(p.over_under.values.astype(float), index=p.game_id.astype("int64").values)


def early_flags(tball, n30):
    tg = team_game_table(tball, n30)
    tg = tg.sort_values(["season", "team_abbreviation", "tip", "game_id"])
    tg["n_before"] = tg.groupby(["season", "team_abbreviation"]).cumcount()   # earlier games of the season, same date impossible
    e = tg.groupby("game_id").n_before.min()
    return (e < 10)


def run_versions(g=None):
    g = model_totals() if g is None else g
    cf, pb, tball, n30 = load_box_all()
    f = pd.read_parquet(E3 / "e3_features.parquet")
    fl = f.groupby("game_id").agg(q_skip=("n_q_rot_c60", lambda x: bool((x >= 1).any())),
                                   new_skip=("new_share_own_c60", lambda x: bool((x >= 0.30).any())))
    early = early_flags(tball, n30).rename("early_skip")
    line = closing_lines().rename("line")
    games = g.set_index("game_id").join(fl).join(early).join(line).reset_index()
    games = games[games.season.isin(["2022-23", "2023-24"])]
    # keep ALL league games for the lean series (g), but bets only where a line exists
    games = games.copy()
    games["date"] = pd.to_datetime(games.date)
    gg = g.copy()
    gg["date"] = pd.to_datetime(gg.date)
    games_for_lean = gg
    vs = pd.read_csv(STUDY / "sample_versions.csv").version.tolist()
    rows = []
    for v in vs:
        kind, model, lean, G, skips = parse_version(v)
        rows.append(eval_version_full(v, games, games_for_lean))
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "versions_check.csv", index=False)
    print(df.to_string())
    return df


def eval_version_full(v, games, glean):
    kind, model, lean, G, skips = parse_version(v)
    pn = []
    for s in (("2023-24",) if model == "F42" else ("2022-23", "2023-24")):     # amendment 1: F42 D-A = 2023-24 only
        d, res = lean_series(glean, model, s)
        gs = games[games.season == s].sort_values(["date", "game_id"])
        for r in gs.itertuples():
            if ("Q" in skips and r.q_skip) or ("NEW" in skips and r.new_skip) or ("EARLY" in skips and r.early_skip):
                continue
            m = getattr(r, model)
            if not (np.isfinite(m) and np.isfinite(r.line)):
                continue
            b = rolling_b(d, res, r.date)[0] if lean == "rolling" else 0.0
            side = bet_decision(m + b, r.line, G)
            if side is not None:
                pn.append(bet_pnl(side, r.actual, r.line))
    p = np.array(pn)
    return dict(version=v, bets=len(p), wins=int((p > 0).sum()), losses=int((p < 0).sum()), pushes=int((p == 0).sum()),
                mean_pnl=float(p.mean()) if len(p) else np.nan)


def run_versions_B():
    """Part B discovery (2021-22..2023-24, report flavor, sportsbook close).  Slow (~all D team-games)."""
    cf, pb, tball, n30 = load_box_all()
    pb = pb[pb.team_abbreviation.isin(n30)].copy()
    pb["game_id"] = pb.game_id.astype("int64")
    pb["minutes"] = pb.minutes.fillna(0.0)
    pb["points"] = pb.points.fillna(0.0)
    pb["team"] = pb.team_abbreviation
    tg = team_game_table(tball, n30)
    reps = cf.load_reports()
    calc = cf.Calc(pb, tball[tball.season >= 2022], n30, reps)
    line = closing_lines()
    D = tg[tg.season.isin([2022, 2023, 2024])]
    D = D[D.game_id.isin(line.index)]
    pb_by_season = {s: g for s, g in pb.groupby("season")}
    xg_by = {k: list(v.game_id) for k, v in tg.groupby(["season", "team_abbreviation"])}
    xt_by = {k: list(v.tip) for k, v in tg.groupby(["season", "team_abbreviation"])}
    S = {}
    for r in D.itertuples():
        season, X, tip = int(r.season), r.team_abbreviation, r.tip
        k = (season, X)
        n = int(np.searchsorted(np.array(xt_by[k], dtype="datetime64[ns]"), np.datetime64(tip.tz_localize(None) if tip.tzinfo else tip, "ns"), side="left"))
        xgames = xg_by[k][:n]
        ps = pb_by_season[season]
        ps = ps[ps.tip < tip]
        ri = calc.find_report({X, r.opponent_team_abbreviation}, r.etdate, tip - pd.Timedelta(minutes=60))
        listed = set()
        if ri >= 0:
            names, _ = calc.report_rows(ri, r.etdate, X)
            for nm, st in names.items():
                if st in ("out", "doubtful"):
                    a = calc.match(nm, X, season, r.etdate)
                    if a is not None:
                        listed.add(a)
        for M in (20, 28):
            cnd = player_window_stats(ps, xgames, tip, X, M)
            S[(r.game_id, X, M)] = s_value(absent_report(cnd, listed))
    games = D.groupby("game_id").agg(h=("team_abbreviation", list), actual=("team_score", "sum")).reset_index()
    vs = pd.read_csv(STUDY / "sample_versions_B.csv").version.tolist()
    rows = []
    for v in vs:
        _, Ms, Ts, scope = v.split("|")
        M, tau = int(Ms[1:]), float(Ts[1:])
        p = []
        for gm in games.itertuples():
            sv = [S[(gm.game_id, t, M)] for t in gm.h]
            if scope_value(sv[0], sv[1], scope) >= tau:
                p.append(bet_pnl("OVER", gm.actual, line[gm.game_id]))
        p = np.array(p)
        rows.append(dict(version=v, bets=len(p), wins=int((p > 0).sum()), losses=int((p < 0).sum()),
                         pushes=int((p == 0).sum()), mean_pnl=float(p.mean()) if len(p) else np.nan))
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "versions_check_B.csv", index=False)
    print(df.to_string())
    return df


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("b", "all"):
        run_b()
    if what == "B":
        run_versions_B()
    if what in ("flags", "all"):
        run_flags()
    if what in ("lean", "versions", "all"):
        g = model_totals()
        if what in ("lean", "all"):
            run_lean(g)
        if what in ("versions", "all"):
            run_versions(g)
