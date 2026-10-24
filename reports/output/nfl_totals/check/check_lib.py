"""Independent implementation of #66 Part A (A-PTS, A-EPA, A-DRIVE) per Amendment 0."""
import numpy as np, pandas as pd
from pathlib import Path

ROOT = Path(r"C:\Users\Jarvis\Documents\Kalshi-PM")
GAMES = ROOT / "data/raw/nflverse/games/games.csv"
PBPDIR = ROOT / "data/raw/nflverse/pbp"
BAD = ROOT / "reports/output/nfl_totals/data/pbp_running_vs_final_implausible.csv"
TEAMMAP = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
GT = {"REG", "WC", "DIV", "CON", "SB"}
WIN = 10 / 11


def ew(vals_recent_first, h):
    """EW mean, weights 0.5**(j/h), j=0 most recent."""
    v = np.asarray(vals_recent_first, float)
    w = 0.5 ** (np.arange(len(v)) / h)
    return float((w * v).sum() / w.sum())


def ew_ratio(num, den, h):
    n = np.asarray(num, float)
    d = np.asarray(den, float)
    w = 0.5 ** (np.arange(len(n)) / h)
    return float((w * n).sum() / (w * d).sum())


def blend(cur, n, prior, k):
    if n == 0:
        return prior
    return n / (n + k) * cur + k / (n + k) * prior


def shrink(end, league):
    return end + (league - end) / 3.0


def settle(total, line, side):
    """per-1 P&L at -110. side 'O'/'U'. Returns (pnl, W/L/P)."""
    if total == line:
        return 0.0, "P"
    over = total > line
    win = over if side == "O" else (not over)
    return (WIN, "W") if win else (-1.0, "L")


def play_filter(df):
    return df[df.play_type.isin(["pass", "run"]) & (df.qb_kneel != 1) & (df.qb_spike != 1)
              & df.epa.notna() & df.posteam.notna()]


def load_games():
    g = pd.read_csv(GAMES)
    for c in ("home_team", "away_team"):
        g[c] = g[c].replace(TEAMMAP)
    g = g[g.game_type.isin(GT)].copy()
    t = g.gametime.fillna("00:00")
    k = pd.to_datetime(g.gameday + " " + t).dt.tz_localize("America/New_York", ambiguous=True, nonexistent="shift_forward")
    g["kick"] = k.dt.tz_convert("UTC")
    return g.reset_index(drop=True)


def load_pbp_stats(g):
    """per (game_id, team) EPA and drive stats."""
    bad = set(pd.read_csv(BAD).game_id)
    gid = set(g.game_id)
    cols = ["game_id", "fixed_drive", "posteam", "defteam", "play_type", "epa", "qb_kneel", "qb_spike",
            "posteam_score", "posteam_score_post", "play_id"]
    ep, dr = [], []
    for f in sorted(PBPDIR.glob("play_by_play_*.parquet")):
        p = pd.read_parquet(f, columns=cols)
        p = p[p.game_id.isin(gid) & ~p.game_id.isin(bad)].copy()
        p["posteam"] = p.posteam.replace(TEAMMAP)
        p["defteam"] = p.defteam.replace(TEAMMAP)
        q = play_filter(p)
        o = q.groupby(["game_id", "posteam"]).agg(off_epa=("epa", "mean"), plays=("epa", "size")).reset_index().rename(columns={"posteam": "team"})
        d = q.groupby(["game_id", "defteam"]).agg(def_epa=("epa", "mean")).reset_index().rename(columns={"defteam": "team"})
        ep.append(o.merge(d, on=["game_id", "team"], how="outer"))
        x = p[p.posteam.notna() & p.fixed_drive.notna()].sort_values("play_id", kind="stable")
        x = x.copy()
        x["_ft"] = x.groupby(["game_id", "fixed_drive"], sort=False).posteam.transform("first")
        keys = x[["game_id", "fixed_drive", "posteam"]].drop_duplicates(["game_id", "fixed_drive"]).set_index(["game_id", "fixed_drive"]).posteam
        xs = x[x.posteam == x._ft]
        gs = xs.groupby(["game_id", "fixed_drive"], sort=False)
        s0 = gs.posteam_score.first()      # first non-null
        s1 = gs.posteam_score_post.last()  # last non-null
        pts = (s1 - s0).clip(lower=0).reindex(keys.index).fillna(0.0)
        dd = pd.DataFrame({"team": keys, "pts": pts}).reset_index()
        dr.append(dd)
    ep = pd.concat(ep, ignore_index=True)
    dr = pd.concat(dr, ignore_index=True)
    gm = g.set_index("game_id")
    hm = dr.game_id.map(gm.home_team)
    am = dr.game_id.map(gm.away_team)
    dr["opp"] = np.where(dr.team == hm, am, hm)
    off = dr.groupby(["game_id", "team"]).agg(dr_o=("pts", "size"), pts_o=("pts", "sum")).reset_index()
    de = dr.groupby(["game_id", "opp"]).agg(dr_d=("pts", "size"), pts_d=("pts", "sum")).reset_index().rename(columns={"opp": "team"})
    dv = off.merge(de, on=["game_id", "team"], how="outer")
    return ep, dv


class Model:
    def __init__(self):
        g = load_games()
        self.g = g
        ep, dv = load_pbp_stats(g)
        rows = []
        for side, opp, pf, pa in (("home_team", "away_team", "home_score", "away_score"),
                                  ("away_team", "home_team", "away_score", "home_score")):
            r = g[["game_id", "season", "kick", side, opp, pf, pa]].copy()
            r.columns = ["game_id", "season", "kick", "team", "opp", "pf", "pa"]
            rows.append(r)
        tg = pd.concat(rows, ignore_index=True)
        tg = tg.merge(ep, on=["game_id", "team"], how="left").merge(dv, on=["game_id", "team"], how="left")
        tg["ok_epa"] = tg.off_epa.notna() & tg.def_epa.notna() & tg.plays.notna()
        tg["ok_drv"] = tg.dr_o.notna() & tg.dr_d.notna() & (tg.dr_o > 0) & (tg.dr_d > 0)
        tg["scored"] = tg.pf.notna() & tg.pa.notna()
        tg = tg.sort_values("kick", kind="stable").reset_index(drop=True)
        self.tg = tg
        self.by_team = {t: d for t, d in tg.groupby("team")}
        sc = g[g.home_score.notna() & g.away_score.notna()].sort_values("kick")
        self.sc_kick = sc.kick.dt.tz_convert("UTC").dt.tz_localize(None).values
        self.sc_cum = np.concatenate([[0.0], np.cumsum((sc.home_score + sc.away_score).values)])
        self.gk = g.set_index("game_id")
        s = tg[tg.scored]
        self.Lprev = s.groupby("season").pf.mean().to_dict()
        e = tg[tg.ok_epa]
        self.lg_epa = {c: e.groupby("season")[c].mean().to_dict() for c in ("off_epa", "def_epa", "plays")}
        d = tg[tg.ok_drv]
        self.lg_ppd = (d.groupby("season").pts_o.sum() / d.groupby("season").dr_o.sum()).to_dict()
        self.lg_dpg = d.groupby("season").dr_o.mean().to_dict()

    def L(self, kick):
        u = kick.tz_convert("UTC").tz_localize(None)
        lo = np.searchsorted(self.sc_kick, np.datetime64(u - pd.Timedelta(days=365)), side="left")
        hi = np.searchsorted(self.sc_kick, np.datetime64(u), side="left")
        n = hi - lo
        if n < 50:
            return np.nan
        return (self.sc_cum[hi] - self.sc_cum[lo]) / (2 * n)

    def _rows(self, team, kick, season, okcol):
        d = self.by_team[team]
        valid = d[okcol]
        cur = d[(d.kick < kick) & (d.season == season) & valid].sort_values("kick", ascending=False)
        prev = d[(d.season == season - 1) & valid].sort_values("kick", ascending=False)
        return cur, prev

    def pts_rating(self, team, kick, season, h, k):
        cur, prev = self._rows(team, kick, season, "scored")
        Lp = self.Lprev.get(season - 1, np.nan)
        out = []
        for col in ("pf", "pa"):
            pr = shrink(ew(prev[col].values, h), Lp) if len(prev) else Lp
            c = ew(cur[col].values, h) if len(cur) else np.nan
            out.append(blend(c, len(cur), pr, k))
        return out  # O, D

    def epa_feat(self, team, kick, season, h, k=4):
        cur, prev = self._rows(team, kick, season, "ok_epa")
        res = {}
        for col in ("off_epa", "def_epa", "plays"):
            lg = self.lg_epa[col].get(season - 1, np.nan)
            pr = shrink(ew(prev[col].values, h), lg) if len(prev) else lg
            c = ew(cur[col].values, h) if len(cur) else np.nan
            res[col] = blend(c, len(cur), pr, k)
        return res

    def drive_feat(self, team, kick, season, h=8, k=4):
        cur, prev = self._rows(team, kick, season, "ok_drv")
        lgp = self.lg_ppd.get(season - 1, np.nan)
        lgd = self.lg_dpg.get(season - 1, np.nan)
        res = {}
        for name, (nu, de) in {"PPD_off": ("pts_o", "dr_o"), "PPD_def": ("pts_d", "dr_d")}.items():
            pr = shrink(ew_ratio(prev[nu].values, prev[de].values, h), lgp) if len(prev) else lgp
            c = ew_ratio(cur[nu].values, cur[de].values, h) if len(cur) else np.nan
            res[name] = blend(c, len(cur), pr, k)
        pr = shrink(ew(prev["dr_o"].values, h), lgd) if len(prev) else lgd
        c = ew(cur["dr_o"].values, h) if len(cur) else np.nan
        res["DPG"] = blend(c, len(cur), pr, k)
        return res

    def game(self, gid):
        r = self.gk.loc[gid]
        return r.home_team, r.away_team, r.kick, int(r.season)

    def apts(self, gid, h, k):
        H, A, kick, s = self.game(gid)
        L = self.L(kick)
        Oh, Dh = self.pts_rating(H, kick, s, h, k)
        Oa, Da = self.pts_rating(A, kick, s, h, k)
        return dict(O_home=Oh, D_home=Dh, O_away=Oa, D_away=Da, L=L, pred=Oh + Da + Oa + Dh - 2 * L)

    def epa_X(self, gid, h):
        """feature rows (home, away) for the OLS: [1, oEPA_team, dEPA_opp, plays_team, plays_opp, L]."""
        H, A, kick, s = self.game(gid)
        fh = self.epa_feat(H, kick, s, h)
        fa = self.epa_feat(A, kick, s, h)
        L = self.L(kick)
        xh = [1.0, fh["off_epa"], fa["def_epa"], fh["plays"], fa["plays"], L]
        xa = [1.0, fa["off_epa"], fh["def_epa"], fa["plays"], fh["plays"], L]
        return np.array(xh), np.array(xa)

    def adrive(self, gid, h=8, k=4):
        H, A, kick, s = self.game(gid)
        fh = self.drive_feat(H, kick, s, h, k)
        fa = self.drive_feat(A, kick, s, h, k)
        e = (fh["DPG"] + fa["DPG"]) / 2
        return e * (fh["PPD_off"] + fa["PPD_def"]) / 2 + e * (fa["PPD_off"] + fh["PPD_def"]) / 2
