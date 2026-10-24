"""Independent A-RES (own implementation from the pre-registration + signed-off main/README readings 7-13)."""
import sys, json, itertools, time
import numpy as np, pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from check_lib import *

BASE = ROOT / "reports/output/nfl_totals"
OUT = Path(__file__).parent / "out"
INJ = ROOT / "data/raw/nflverse/injuries"
LAMS = (0.1, 1, 10, 100, 300, 1000, 3000, 10000, 30000)
FEATS = ["gap_pts", "gap_epa", "gap_drive", "nsp_h", "nsp_a", "proe_h", "proe_a", "qbout_h", "qbout_a",
         "hcchg_h", "hcchg_a", "firstyr_h", "firstyr_a", "rest_h", "rest_a", "short_week", "primetime",
         "div", "neutral", "week", "playoff", "ref_pen", "close"]


def pbp_extra(g):
    """per (game_id, team): neutral seconds/play, PROE; per game_id: penalty rows."""
    bad = set(pd.read_csv(BAD).game_id)
    gid = set(g.game_id)
    cols = ["game_id", "fixed_drive", "posteam", "play_type", "epa", "qb_kneel", "qb_spike", "play_id",
            "game_seconds_remaining", "half_seconds_remaining", "wp", "pass_oe", "penalty"]
    nsp, pen = [], []
    for f in sorted(PBPDIR.glob("play_by_play_*.parquet")):
        p = pd.read_parquet(f, columns=cols)
        p = p[p.game_id.isin(gid) & ~p.game_id.isin(bad)].copy()
        p["posteam"] = p.posteam.replace(TEAMMAP).replace("", np.nan)
        pen.append(p.groupby("game_id").penalty.apply(lambda s: float((s == 1).sum())).rename("pen").reset_index())
        q = play_filter(p).sort_values(["game_id", "play_id"], kind="stable").copy()
        q["pass_oe"] = q.pass_oe.fillna(0.0)
        q["neutral"] = (q.wp >= 0.2) & (q.wp <= 0.8) & (q.half_seconds_remaining > 120)
        pr = q.groupby(["game_id", "posteam"]).pass_oe.mean().rename("proe").reset_index().rename(columns={"posteam": "team"})
        nx = q.groupby(["game_id", "fixed_drive", "posteam"])[["posteam", "fixed_drive", "game_seconds_remaining", "neutral"]].shift(-1)
        pair = (q.posteam == nx.posteam) & (q.fixed_drive == nx.fixed_drive) & q.neutral & (nx.neutral == True)
        dt = q.game_seconds_remaining - nx.game_seconds_remaining
        k = q[pair & (dt >= 0)].assign(dt=dt[pair & (dt >= 0)])
        ns = k.groupby(["game_id", "posteam"]).dt.mean().rename("nsp").reset_index().rename(columns={"posteam": "team"})
        nsp.append(pr.merge(ns, on=["game_id", "team"], how="left"))
    return pd.concat(nsp, ignore_index=True), pd.concat(pen, ignore_index=True)


def ew_stat(M, team, kick, season, col, okcol, lgmap, h=8, k=4):
    cur, prev = M._rows(team, kick, season, okcol)
    lg = lgmap.get(season - 1, np.nan)
    pr = shrink(ew(prev[col].values, h), lg) if len(prev) else lg
    c = ew(cur[col].values, h) if len(cur) else np.nan
    return blend(c, len(cur), pr, k)


def build():
    t0 = time.time()
    M = Model()
    g = M.g
    ex, pen = pbp_extra(g)
    tg = M.tg.merge(ex, on=["game_id", "team"], how="left")
    tg["ok_nsp"] = tg.nsp.notna()
    tg["ok_proe"] = tg.proe.notna()
    tg = tg.sort_values("kick", kind="stable").reset_index(drop=True)
    M.tg = tg
    M.by_team = {t: d for t, d in tg.groupby("team")}
    lg_nsp = tg[tg.ok_nsp].groupby("season").nsp.mean().to_dict()
    lg_proe = tg[tg.ok_proe].groupby("season").proe.mean().to_dict()
    pen = pen.merge(g[["game_id", "season", "referee"]], on="game_id")
    print("pbp extras", round(time.time() - t0, 1), flush=True)

    # coach flags per team-game (all games in games.csv, schedule order)
    gs = g.sort_values("kick", kind="stable")
    rows = []
    for side in ("home", "away"):
        r = gs[["game_id", "season", "kick", f"{side}_team", f"{side}_coach", f"{side}_qb_id"]].copy()
        r.columns = ["game_id", "season", "kick", "team", "coach", "qb"]
        r["side"] = side
        rows.append(r)
    cg = pd.concat(rows).sort_values("kick", kind="stable")
    hc = {}
    for team, d in cg.groupby("team"):
        d = d.reset_index(drop=True)
        last_change_idx = {}
        seen = set()  # (season, coach) of earlier seasons
        per_season_first = {}
        for i, r in d.iterrows():
            same = d[(d.season == r.season) & (d.index < i)]
            changed = len(same) > 0 and same.iloc[-1].coach != r.coach
            if changed:
                last_change_idx[r.season] = i
            lc = last_change_idx.get(r.season)
            flag = lc is not None and (i - lc) <= 4
            early = d[(d.season < r.season) & (d.coach == r.coach)]
            hc[(r.game_id, team)] = (int(flag), int(len(early) == 0))

    # previous played game starter per team
    played = cg.merge(g[["game_id", "home_score", "away_score"]], on="game_id")
    played = played[played.home_score.notna() & played.away_score.notna()]
    prev_qb = {t: d for t, d in played.groupby("team")}

    # injuries
    inj = []
    for f in sorted(INJ.glob("injuries_*.parquet")):
        d = pd.read_parquet(f)
        if "date_modified" not in d.columns:
            d["date_modified"] = pd.NaT
        d = d[["season", "team", "week", "gsis_id", "report_status", "date_modified"]].copy()
        d["date_modified"] = pd.to_datetime(d.date_modified, utc=True)
        inj.append(d)
    inj = pd.concat(inj, ignore_index=True)
    inj["team"] = inj.team.replace(TEAMMAP)
    inj = inj[inj.date_modified.notna()].copy()
    inj["ed"] = inj.date_modified.dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
    inj = inj.sort_values("date_modified", kind="stable")
    injg = {k: d for k, d in inj.groupby(["season", "week", "team"])}

    def qbout(team, gr):
        d = prev_qb.get(team)
        if d is None:
            return 0
        d = d[d.kick < gr.kick]
        if not len(d):
            return 0
        q = d.iloc[-1].qb
        if not isinstance(q, str):
            return 0
        x = injg.get((gr.season, gr.week, team))
        if x is None:
            return 0
        gd = pd.Timestamp(gr.gameday)
        x = x[(x.gsis_id == q) & (x.ed < gd)]
        if not len(x):
            return 0
        return int(x.iloc[-1].report_status in ("Out", "Doubtful"))

    # referee penalties
    pen_by = {s: d for s, d in pen.groupby("season")}

    def refpen(gr):
        ds = [pen_by[s] for s in (gr.season - 2, gr.season - 1) if s in pen_by]
        if not ds:
            return np.nan
        d = pd.concat(ds)
        if isinstance(gr.referee, str):
            m = d[d.referee == gr.referee]
            if len(m) >= 10:
                return m.pen.mean()
        return d.pen.mean()

    # EPA OLS on D (needs D outcomes: seasons 2006-2014 only)
    D = g[(g.season >= 2006) & (g.season <= 2014) & g.total_line.notna() & g.home_score.notna() & g.away_score.notna()]
    sg = pd.read_csv(BASE / "sample_games.csv").game_id
    need = sorted(set(D.game_id) | set(sg))
    beta = None
    Xs, ys = [], []
    XX = {}
    for i in need:
        XX[i] = M.epa_X(i, 8)
    for i in D.game_id:
        xh, xa = XX[i]
        if np.isnan(xh).any() or np.isnan(xa).any():
            continue
        r = M.gk.loc[i]
        Xs += [xh, xa]; ys += [r.home_score, r.away_score]
    beta, *_ = np.linalg.lstsq(np.array(Xs), np.array(ys), rcond=None)
    print("epa fit", len(ys), round(time.time() - t0, 1), flush=True)

    recs = []
    for i in need:
        gr = M.gk.loc[i]
        H, A, kick, s = M.game(i)
        close = gr.total_line
        pts = M.apts(i, 8, 4)["pred"]
        xh, xa = XX[i]
        epa = float(xh @ beta + xa @ beta)
        adr = M.adrive(i)
        gt = str(gr.gametime)
        r = dict(game_id=i, season=s, gap_pts=pts - close, gap_epa=epa - close, gap_drive=adr - close, close=close)
        for tag, tm in (("h", H), ("a", A)):
            r[f"nsp_{tag}"] = ew_stat(M, tm, kick, s, "nsp", "ok_nsp", lg_nsp)
            r[f"proe_{tag}"] = ew_stat(M, tm, kick, s, "proe", "ok_proe", lg_proe)
            r[f"qbout_{tag}"] = qbout(tm, gr)
            r[f"hcchg_{tag}"], r[f"firstyr_{tag}"] = hc[(i, tm)]
        r["rest_h"] = gr.home_rest; r["rest_a"] = gr.away_rest
        r["short_week"] = float(min(gr.home_rest, gr.away_rest) <= 5)
        r["primetime"] = float(gt >= "19:00")
        r["div"] = gr.div_game; r["neutral"] = float(gr.location == "Neutral")
        r["week"] = gr.week; r["playoff"] = float(gr.game_type != "REG")
        r["ref_pen"] = refpen(gr)
        if i in set(D.game_id):
            r["y"] = (gr.home_score + gr.away_score) - close
        recs.append(r)
    F = pd.DataFrame(recs)
    print("features", round(time.time() - t0, 1), flush=True)
    return F, set(D.game_id), list(sg)


def ridge_fit(Z, y, lam):
    zm = Z.mean(0)
    Zc = Z - zm
    b = np.linalg.solve(Zc.T @ Zc + lam * np.eye(Z.shape[1]), Zc.T @ (y - y.mean()))
    return y.mean() - zm @ b, b


def main():
    F, Dids, sg = build()
    F.drop(columns=[c for c in ["y"] if c in F.columns]).to_csv(OUT / "check_ares_features_all.csv", index=False)
    Fd = F[F.game_id.isin(Dids)].dropna(subset=FEATS + ["y"])
    mu = Fd[FEATS].mean().values.copy()
    sd = Fd[FEATS].std(ddof=0).values.copy()
    sd[sd == 0] = 1.0
    Z = (Fd[FEATS].values - mu) / sd
    y = Fd.y.values
    seas = Fd.season.values
    mse = {}
    for lam in LAMS:
        sse = 0.0
        for s in np.unique(seas):
            tr = seas != s
            a, b = ridge_fit(Z[tr], y[tr], lam)
            sse += ((y[~tr] - (a + Z[~tr] @ b)) ** 2).sum()
        mse[lam] = sse / len(y)
    lam = min(LAMS, key=lambda l: (mse[l], l))
    a, b = ridge_fit(Z, y, lam)
    Fs = F.set_index("game_id").loc[sg]
    zs = (Fs[FEATS].values.astype(float) - mu) / sd
    pred = a + zs @ b
    pred[np.isnan(Fs[FEATS].values.astype(float)).any(1)] = np.nan
    pd.DataFrame({"game_id": sg, "ares_pred": pred, "lambda": lam}).to_csv(OUT / "check_ares_predictions.csv", index=False)
    json.dump({"lambda": lam, "n_D_complete": int(len(y)), "intercept": float(a), "feats": FEATS,
               "coef_z": dict(zip(FEATS, map(float, b))), "mu": dict(zip(FEATS, map(float, mu))),
               "sd": dict(zip(FEATS, map(float, sd))), "loso_mse": {str(k): float(v) for k, v in mse.items()}},
              open(OUT / "check_ares_coefs.json", "w"), indent=1)
    print("lambda", lam, "n", len(y), "sample NaN preds", int(np.isnan(pred).sum()))


if __name__ == "__main__":
    main()
