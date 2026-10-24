"""Independent checker for research #59 features. Written from PREREGISTRATION.md only."""
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
RAW = ROOT / "data" / "raw" / "nba_open"
OUT = Path(__file__).resolve().parent
TURNOVERS_ONLY_FILE_YEARS = range(2008, 2013)   # seasons 2007-08..2011-12
SEED = 20261023
CAP_REST = 4
MIN_LEAGUE_GAMES = 50


def team_turnovers(turn, total, file_year):
    """Turnover guard: total_turnovers if finite and >= turnovers else turnovers;
    plain turnovers only in 2007-08..2011-12."""
    turn = np.asarray(turn, float)
    total = np.asarray(total, float)
    if file_year in TURNOVERS_ONLY_FILE_YEARS:
        return turn.copy()
    ok = np.isfinite(total) & (total >= turn)
    return np.where(ok, total, turn)


def load_clean(years=range(2008, 2028)):
    """Regular-season NBA team-games with columns used downstream."""
    parts = []
    for y in years:
        d = pd.read_parquet(RAW / f"team_box_{y}.parquet")
        d = d[d.season_type == 2].copy()
        d["tov"] = team_turnovers(d.turnovers, d.total_turnovers, y)
        d["file_year"] = y
        parts.append(d)
    d = pd.concat(parts, ignore_index=True)
    # drop all-star / exhibition: any game with a "team" that is not one of the real NBA clubs.
    n = d.groupby(["season", "team_abbreviation"]).size()
    real = set(n[n >= 10].index.get_level_values(1))
    ok = d.team_abbreviation.isin(real) & d.opponent_team_abbreviation.isin(real)
    d = d[ok]
    tg = pd.DataFrame({
        "game_id": d.game_id.astype("int64"), "team_id": d.team_id.astype("int64"),
        "team": d.team_abbreviation.astype(str), "opp_id": d.opponent_team_id.astype("int64"),
        "season": d.season.astype(int), "date": pd.to_datetime(d.game_date.astype(str)),
        "home": (d.team_home_away == "home").astype(int),
        "pts": d.team_score.astype(float), "opp_pts": d.opponent_team_score.astype(float),
        "fga": d.field_goals_attempted.astype(float), "oreb": d.offensive_rebounds.astype(float),
        "tov": d.tov.astype(float), "fta": d.free_throws_attempted.astype(float)})
    return tg.reset_index(drop=True)


def add_poss(tg):
    tg = tg.copy()
    tg["poss_team"] = 0.96 * (tg.fga - tg.oreb + tg.tov + 0.44 * tg.fta)
    tg["gposs"] = tg.groupby("game_id").poss_team.transform("mean")
    return tg


def _ortg(h):  # points scored per 100 game possessions
    return 100.0 * h.pts.sum() / h.gposs.sum() if len(h) else np.nan


def _drtg(h):
    return 100.0 * h.opp_pts.sum() / h.gposs.sum() if len(h) else np.nan


def _pace(h):
    return h.gposs.mean() if len(h) else np.nan


def _or(v, fb):
    return fb if (v is None or not np.isfinite(v)) else v


class Features:
    def __init__(self, tg):
        self.tg = add_poss(tg).sort_values(["date", "game_id"]).reset_index(drop=True)
        self.by_team = {k: g for k, g in self.tg.groupby(["team_id", "season"])}
        self.by_season = {k: g for k, g in self.tg.groupby("season")}
        self.first_date = self.tg.groupby("season").date.min().to_dict()

    def _hist(self, team, S, D):
        g = self.by_team.get((team, S))
        if g is None:
            return self.tg.iloc[0:0]
        return g[g.date < D]

    def _full(self, team, S):
        g = self.by_team.get((team, S - 1))
        return self.tg.iloc[0:0] if g is None else g

    def _league(self, S, D):
        sg = self.by_season.get(S)
        h = sg[sg.date < D] if sg is not None else self.tg.iloc[0:0]
        if h.game_id.nunique() >= MIN_LEAGUE_GAMES:
            return _ortg(h), _pace(h.drop_duplicates("game_id"))
        p = self.by_season.get(S - 1)
        if p is None:
            return np.nan, np.nan
        return _ortg(p), _pace(p.drop_duplicates("game_id"))

    def _rest(self, team, S, D):
        h = self._hist(team, S, D)
        if not len(h):
            return CAP_REST, 0
        r = min((D - h.date.max()).days - 1, CAP_REST)
        return r, int(r == 0)

    def _side(self, team, S, D, out, tag, role):
        h = self._hist(team, S, D)
        pv = self._full(team, S)
        f = _ortg if role == "off" else _drtg
        name = "ortg" if role == "off" else "drtg"
        prev = f(pv)
        out[f"{name}_{tag}_prev"] = prev
        out[f"{name}_{tag}_s2d"] = _or(f(h), prev)
        out[f"{name}_{tag}_l10"] = _or(f(h.tail(10)), prev)
        out[f"{name}_{tag}_l5"] = _or(f(h.tail(5)), prev)

    def _pace(self, team, S, D, out, tag):
        h = self._hist(team, S, D)
        prev = _pace(self._full(team, S))
        out[f"pace_{tag}_prev"] = prev
        out[f"pace_{tag}_s2d"] = _or(_pace(h), prev)
        out[f"pace_{tag}_l10"] = _or(_pace(h.tail(10)), prev)

    def row(self, game_id, team_id):
        r = self.tg[(self.tg.game_id == game_id) & (self.tg.team_id == team_id)]
        assert len(r) == 1
        r = r.iloc[0]
        S, D, X, Y = int(r.season), r.date, int(r.team_id), int(r.opp_id)
        opp = self.tg[(self.tg.game_id == game_id) & (self.tg.team_id == Y)].iloc[0].team
        o = {"game_id": int(game_id), "season": S, "date": D, "team": r.team, "opp": opp}
        self._side(X, S, D, o, "X", "off")
        self._side(Y, S, D, o, "Y", "def")
        self._pace(X, S, D, o, "X")
        self._pace(Y, S, D, o, "Y")
        o["league_ortg"], o["league_pace"] = self._league(S, D)
        o["home"] = int(r.home)
        o["rest_X"], o["b2b_X"] = self._rest(X, S, D)
        o["rest_Y"], o["b2b_Y"] = self._rest(Y, S, D)
        o["gp_X"] = len(self._hist(X, S, D))
        o["gp_Y"] = len(self._hist(Y, S, D))
        o["dos"] = (D - self.first_date[S]).days
        o["target"] = float(r.pts)
        return o


def sample_keys(tg, first=2009, last=2026):
    rows = tg[(tg.season >= first) & (tg.season <= last)]
    rows = rows.sort_values(["season", "date", "game_id", "team"], kind="mergesort").reset_index(drop=True)
    rng = np.random.default_rng(SEED)
    idx = np.sort(rng.choice(len(rows), 300, replace=False))
    return rows.iloc[idx][["game_id", "team_id", "team"]], len(rows)


def to_feature_names(df):
    """Rename to the main builder's FEATURES.md column names (names only)."""
    df = df.rename(columns={c: c.replace("_X", "_x").replace("_Y", "_y").replace("_s2d", "_season")
                            for c in df.columns})
    df = df.rename(columns={"league_ortg": "lg_ortg", "league_pace": "lg_pace", "target": "pts"})
    df["season"] = df.season.map(lambda S: f"{S - 1}-{str(S)[2:]}")
    num = [c for c in df.columns if c not in ("game_id", "season", "date", "team", "opp")]
    df[num] = df[num].astype("float64")
    df["date"] = df["date"].astype("datetime64[ns]")
    return df


if __name__ == "__main__":
    tg = load_clean()
    keys, n = sample_keys(tg)
    F = Features(tg)
    df = pd.DataFrame([F.row(g, t) for g, t in zip(keys.game_id, keys.team_id)])
    df = to_feature_names(df)
    df.to_parquet(OUT / "features_check.parquet", index=False)
    print("population", n, "rows", len(df), "nan cells", int(df.isna().sum().sum()))
    print(df.isna().sum()[df.isna().sum() > 0])
    print(df.season.value_counts().sort_index().to_dict())
