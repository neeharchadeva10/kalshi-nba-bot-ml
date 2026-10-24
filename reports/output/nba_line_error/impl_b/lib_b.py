"""Implementation B of research #57: data cleaning, y and features f1-f10 (own code, written from PREREGISTRATION.md).

Everything here is deterministic and uses only data dated strictly before a game's ET date D (rows with date < D).
No network. No analysis of y.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
RAW_BOX = ROOT / "data" / "raw" / "nba_open"
ODDS_DB = ROOT / "data" / "raw" / "nba_odds_samples" / "OddsData.sqlite"
CLOSE_PQ = ROOT / "data" / "raw" / "nba_odds_samples" / "closing_lines_odds_api.parquet"
OUT = Path(__file__).resolve().parent

NBA_IDS = set(range(1, 31))          # hoopR/ESPN franchise ids 1..30 (All-Star / exhibition teams have other ids)
DENVER, UTAH = 7, 26                 # altitude flag teams (f7)

NAME_TO_ID = {
    "Atlanta Hawks": 1, "Boston Celtics": 2, "New Orleans Pelicans": 3, "Chicago Bulls": 4, "Cleveland Cavaliers": 5,
    "Dallas Mavericks": 6, "Denver Nuggets": 7, "Detroit Pistons": 8, "Golden State Warriors": 9,
    "Houston Rockets": 10, "Indiana Pacers": 11, "LA Clippers": 12, "Los Angeles Clippers": 12,
    "Los Angeles Lakers": 13, "Miami Heat": 14, "Milwaukee Bucks": 15, "Minnesota Timberwolves": 16,
    "Brooklyn Nets": 17, "New Jersey Nets": 17, "New York Knicks": 18, "Orlando Magic": 19,
    "Philadelphia 76ers": 20, "Phoenix Suns": 21, "Portland Trail Blazers": 22, "Sacramento Kings": 23,
    "San Antonio Spurs": 24, "Oklahoma City Thunder": 25, "Seattle SuperSonics": 25, "Utah Jazz": 26,
    "Washington Wizards": 27, "Toronto Raptors": 28, "Memphis Grizzlies": 29, "Charlotte Bobcats": 30,
    "Charlotte Hornets": 30,
}


def season_label(file_year: int) -> str:
    return f"{file_year - 1}-{str(file_year)[2:]}"


# --------------------------------------------------------------------------------------------- hoopR
def _tov_series(d: pd.DataFrame) -> pd.Series:
    """Turnovers per team-game, one file (season) at a time.

    hoopR 'turnovers' / 'team_turnovers' / 'total_turnovers' differ by era (checked on the data):
      * early seasons: team_turnovers == turnovers and total_turnovers == 2 * turnovers  -> use 'turnovers'
      * later seasons: total_turnovers = turnovers + team_turnovers (official TOV incl. team turnovers) -> use total
      * where total_turnovers is missing (all of 2017-18, 2 rows of 2025-26) -> use 'turnovers'
    """
    tov = pd.to_numeric(d["turnovers"], errors="coerce")
    tot = pd.to_numeric(d["total_turnovers"], errors="coerce")
    doubled = (tot == 2 * tov).mean() > 0.9
    if doubled:
        return tov
    return tot.where(tot.notna(), tov)


def load_hoopr() -> pd.DataFrame:
    """Team-game rows (2 per game) for all seasons 2007-08..2025-26, all season types, NBA franchises only."""
    parts = []
    for y in range(2008, 2027):
        d = pd.read_parquet(RAW_BOX / f"team_box_{y}.parquet")
        d["tov"] = _tov_series(d)
        d["file_year"] = y
        parts.append(d)
    d = pd.concat(parts, ignore_index=True)
    out = pd.DataFrame({
        "game_id": d["game_id"].astype("int64"),
        "season": d["file_year"].map(season_label),
        "season_type": d["season_type"].astype(int),
        "date": pd.to_datetime(d["game_date_time"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")),
        "team_id": d["team_id"].astype(int),
        "opp_id": d["opponent_team_id"].astype(int),
        "abbr": d["team_abbreviation"],
        "opp_abbr": d["opponent_team_abbreviation"],
        "is_home": (d["team_home_away"] == "home"),
        "pts": d["team_score"].astype(float),
        "opp_pts": d["opponent_team_score"].astype(float),
        "fga": d["field_goals_attempted"].astype(float),
        "oreb": d["offensive_rebounds"].astype(float),
        "tov": d["tov"].astype(float),
        "fta": d["free_throws_attempted"].astype(float),
        "tpa": d["three_point_field_goals_attempted"].astype(float),
    })
    out = out[out.team_id.isin(NBA_IDS) & out.opp_id.isin(NBA_IDS)].copy()
    out["poss_own"] = possessions(out["fga"], out["oreb"], out["tov"], out["fta"])
    return out.reset_index(drop=True)


def possessions(fga, oreb, tov, fta):
    """0.96 x (FGA - OREB + TOV + 0.44 x FTA) for one team-game."""
    return 0.96 * (fga - oreb + tov + 0.44 * fta)


def build_games(tg: pd.DataFrame) -> pd.DataFrame:
    """One row per game from the team-game rows: home/away ids, abbreviations, scores, total, game possessions
    (mean of both teams' possessions = pace), regular-season flag."""
    h = tg[tg.is_home].set_index("game_id")
    a = tg[~tg.is_home].set_index("game_id")
    g = pd.DataFrame(index=h.index.intersection(a.index))
    h, a = h.loc[g.index], a.loc[g.index]
    g["season"] = h["season"]
    g["season_type"] = h["season_type"]
    g["date"] = h["date"]
    g["home_id"], g["away_id"] = h["team_id"], a["team_id"]
    g["home"], g["away"] = h["abbr"], a["abbr"]
    g["home_pts"], g["away_pts"] = h["pts"], a["pts"]
    g["actual"] = h["pts"] + a["pts"]
    g["poss_home"], g["poss_away"] = h["poss_own"], a["poss_own"]
    g["pace"] = (h["poss_own"] + a["poss_own"]) / 2.0
    g["home_tpar"] = h["tpa"] / h["fga"]
    g["away_tpar"] = a["tpa"] / a["fga"]
    g = g.reset_index().sort_values(["date", "game_id"]).reset_index(drop=True)
    return g


def regular(g: pd.DataFrame) -> pd.DataFrame:
    return g[g.season_type == 2].reset_index(drop=True)


# --------------------------------------------------------------------------------------------- odds data
def parse_old_date(s: str) -> pd.Timestamp:
    """Old 'X' tables store 'YYYY-YY-MMDD' (season label + month/day). Month >= 8 -> season start year, else +1.
    (Wrong only for the Aug-Oct 2020 bubble, which the aligned '_new' table fixes; see load_oddsdata.)"""
    season, mmdd = s[:7], s[8:]
    y0 = int(season[:4])
    m, d = int(mmdd[:2]), int(mmdd[2:])
    return pd.Timestamp(year=y0 if m >= 8 else y0 + 1, month=m, day=d)


def load_oddsdata() -> pd.DataFrame:
    """All OddsData tables, long. Columns: table, season_tab, kind ('X' or 'Xnew'), date (ET), home_id, away_id, OU,
    Spread, Points, home/away names. Old-format dates of an 'X' table are replaced by the same-position date of
    its '_new' twin when the two tables have the same length and the same Home/Away in every position (verified), else
    parsed with parse_old_date."""
    con = sqlite3.connect(ODDS_DB)
    names = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    tabs = {n: pd.read_sql(f'select * from "{n}"', con) for n in names}
    con.close()
    rows = []
    for n, d in tabs.items():
        d = d.copy()
        base = n[5:] if n.startswith("odds_") else n
        is_new = base.endswith("_new")
        season_tab = base[:-4] if is_new else base
        iso = d["Date"].astype(str).str.fullmatch(r"\d{4}-\d{2}-\d{2}")
        if iso.all():
            dates = pd.to_datetime(d["Date"])
            how = "iso"
        else:
            twin = tabs.get(f"odds_{season_tab}_new")
            if (twin is not None and len(twin) == len(d) and (twin.Home.values == d.Home.values).all()
                    and (twin.Away.values == d.Away.values).all()):
                dates = pd.to_datetime(twin["Date"])
                how = "aligned_new"
            else:
                dates = d["Date"].astype(str).map(parse_old_date)
                how = "parsed"
        x = pd.DataFrame({
            "table": n, "season_tab": season_tab, "kind": "Xnew" if is_new else "X", "date_how": how,
            "date": dates.values, "home_name": d["Home"].values, "away_name": d["Away"].values,
            "OU": pd.to_numeric(d["OU"], errors="coerce").values,
            "Spread": pd.to_numeric(d["Spread"], errors="coerce").values,
            "Points": pd.to_numeric(d["Points"], errors="coerce").values,
        })
        x["row"] = np.arange(len(x))
        rows.append(x)
    o = pd.concat(rows, ignore_index=True)
    o["home_id"] = o["home_name"].map(NAME_TO_ID)
    o["away_id"] = o["away_name"].map(NAME_TO_ID)
    return o


def match_oddsdata(o: pd.DataFrame, games_all: pd.DataFrame) -> pd.DataFrame:
    """Match each OddsData row to a hoopR game: same home id and away id, game date within +-1 day of the row date.
    Exact-date candidate preferred. Adds game_id, match_status in {ok, unmatched, ambiguous, not_regular_season,
    bad_team_name}."""
    key = games_all[["game_id", "date", "home_id", "away_id", "season_type"]]
    by = {}
    for r in key.itertuples(index=False):
        by.setdefault((r.home_id, r.away_id), []).append((r.date, r.game_id, r.season_type))
    gid, status = [], []
    for r in o.itertuples(index=False):
        if pd.isna(r.home_id) or pd.isna(r.away_id):
            gid.append(np.nan); status.append("bad_team_name"); continue
        cands = [(d, g, st) for d, g, st in by.get((int(r.home_id), int(r.away_id)), [])
                 if abs((d - r.date).days) <= 1]
        if not cands:
            gid.append(np.nan); status.append("unmatched"); continue
        exact = [c for c in cands if c[0] == r.date]
        use = exact if exact else cands
        if len(use) > 1:
            gid.append(np.nan); status.append("ambiguous"); continue
        d, g, st = use[0]
        gid.append(g)
        status.append("ok" if st == 2 else "not_regular_season")
    o = o.copy()
    o["game_id"] = gid
    o["match_status"] = status
    return o


# --------------------------------------------------------------------------------------------- cleaning
def clean_lines(g_reg: pd.DataFrame, g_all: pd.DataFrame, o: pd.DataFrame, close_raw: pd.DataFrame):
    """Pre-registered cleaning (steps 1-4). Returns (clean DataFrame one row per kept game, excluded list of dicts,
    info dict with per-season counts and agreement tables)."""
    excl = []
    gi = g_all.set_index("game_id")

    # ---------------- closing lines (2020-21..): drop snapshot >= commence, match by game_id (+- 1 day sanity)
    c = close_raw.copy()
    c["snap"] = pd.to_datetime(c["snapshot"], utc=True)
    c["comm"] = pd.to_datetime(c["commence_time"], utc=True)
    c["comm_et_date"] = pd.to_datetime(c["comm"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d"))
    c_rows_in = len(c)
    c["season"] = c["game_id"].map(gi["season"])
    bad_snap = c["snap"] >= c["comm"]
    for r in c[bad_snap].itertuples():
        excl.append(dict(source="closing", key=str(r.game_id), season=r.season, reason="closing_snapshot_not_before_commence",
                         detail="fallback to OddsData if present"))
    c = c[~bad_snap]
    dup = c.duplicated("game_id", keep=False)
    for r in c[dup].itertuples():
        excl.append(dict(source="closing", key=str(r.game_id), season=r.season, reason="closing_duplicate_game_id", detail=""))
    c = c[~dup]
    ok_rows = []
    for r in c.itertuples():
        if r.game_id not in gi.index:
            excl.append(dict(source="closing", key=str(r.game_id), season="", reason="closing_unmatched_game_id", detail=""))
            continue
        gr = gi.loc[r.game_id]
        if gr["season_type"] != 2:
            excl.append(dict(source="closing", key=str(r.game_id), season=gr["season"], reason="closing_not_regular_season", detail=""))
            continue
        if abs((gr["date"] - r.comm_et_date).days) > 1:
            excl.append(dict(source="closing", key=str(r.game_id), season=gr["season"], reason="closing_date_mismatch", detail=""))
            continue
        if pd.isna(r.over_under):
            excl.append(dict(source="closing", key=str(r.game_id), season=gr["season"], reason="closing_ou_null",
                             detail="fallback to OddsData if present"))
            continue
        ok_rows.append(dict(game_id=r.game_id, close_line=float(r.over_under), close_spread=abs(float(r.home_team_spread))))
    close = pd.DataFrame(ok_rows, columns=["game_id", "close_line", "close_spread"]).set_index("game_id")

    # ---------------- OddsData rows
    o = o.copy()
    o["season"] = o["game_id"].map(gi["season"]).fillna(o["season_tab"])
    for r in o[o.match_status != "ok"].itertuples():
        excl.append(dict(source=f"oddsdata:{r.table}", key=f"{r.date:%Y-%m-%d}|{r.home_name}|{r.away_name}",
                         season=r.season, reason=f"oddsdata_{r.match_status}", detail=f"row {r.row}"))
    om = o[o.match_status == "ok"].copy()
    dupk = om.duplicated(["table", "game_id"], keep=False)
    for r in om[dupk].itertuples():
        excl.append(dict(source=f"oddsdata:{r.table}", key=f"{r.date:%Y-%m-%d}|{r.home_name}|{r.away_name}",
                         season=r.season, reason="oddsdata_duplicate", detail=f"row {r.row}"))
    om = om[~dupk]
    om = om[om.OU.notna()]
    # points mismatch vs hoopR (listed, not excluded)
    om["hoopr_total"] = om["game_id"].map(gi["actual"])
    pm = om[om.Points.notna() & (om.Points != om.hoopr_total)][["table", "game_id", "date", "home_name", "away_name", "Points", "hoopr_total"]]
    # per game: combine X and Xnew
    odds_game = {}
    for gid_, grp in om.groupby("game_id"):
        vals = grp.sort_values("kind")  # 'X' < 'Xnew'
        if len(grp) == 1:
            r = grp.iloc[0]
            odds_game[gid_] = dict(ou=r.OU, spread=abs(r.Spread) if pd.notna(r.Spread) else np.nan, tabs=r.kind, disputed=False,
                                   x=r.OU if r.kind == "X" else np.nan, xn=r.OU if r.kind == "Xnew" else np.nan)
        else:
            x = grp[grp.kind == "X"]
            xn = grp[grp.kind == "Xnew"]
            if len(x) == 1 and len(xn) == 1:
                ox, on = x.iloc[0].OU, xn.iloc[0].OU
                disputed = abs(ox - on) > 1.0
                r = xn.iloc[0]       # reading: when both agree within 1.0 the X_new value is used
                odds_game[gid_] = dict(ou=r.OU, spread=abs(r.Spread) if pd.notna(r.Spread) else np.nan, tabs="X+Xnew",
                                       disputed=disputed, x=ox, xn=on)
            else:  # more than one row of the same kind for a game cannot happen after dedup; guard anyway
                odds_game[gid_] = dict(ou=np.nan, spread=np.nan, tabs="odd", disputed=True, x=np.nan, xn=np.nan)

    # ---------------- decide a line for each hoopR regular-season game
    pre = {}
    kept = []
    for r in g_reg.itertuples():
        gid_ = r.game_id
        if gid_ in close.index:
            line, src, spr = close.at[gid_, "close_line"], "closing_odds_api", close.at[gid_, "close_spread"]
        elif gid_ in odds_game:
            og = odds_game[gid_]
            if og["disputed"]:
                excl.append(dict(source="game", key=str(gid_), season=r.season, reason="line_disputed",
                                 detail=f"X={og['x']} Xnew={og['xn']}"))
                continue
            line, src, spr = og["ou"], f"oddsdata_{og['tabs']}", og["spread"]
        else:
            excl.append(dict(source="game", key=str(gid_), season=r.season, reason="no_line_available", detail=""))
            continue
        if not (170.0 <= line <= 275.0):
            excl.append(dict(source="game", key=str(gid_), season=r.season, reason="line_out_of_range", detail=f"line={line}"))
            continue
        kept.append(dict(game_id=gid_, line=float(line), line_source=src, spread=float(spr) if pd.notna(spr) else np.nan))
    k = pd.DataFrame(kept)

    # sanity: |line - mean of both teams' season-to-date avg totals| <= 30 (needs >=5 prior games each)
    team_hist = team_total_history(g_reg)
    keep_idx = []
    for i, r in enumerate(k.itertuples()):
        gr = gi.loc[r.game_id]
        ma, na = team_hist.avg_before(gr["away_id"], gr["season"], gr["date"])
        mh, nh = team_hist.avg_before(gr["home_id"], gr["season"], gr["date"])
        if na >= 5 and nh >= 5:
            ref = (ma + mh) / 2.0
            if abs(r.line - ref) > 30.0:
                excl.append(dict(source="game", key=str(r.game_id), season=gr["season"], reason="line_far_from_team_means",
                                 detail=f"line={r.line} ref={ref:.2f}"))
                continue
        keep_idx.append(i)
    k = k.iloc[keep_idx].reset_index(drop=True)

    # agreement closing vs OddsData
    agree = []
    for gid_, og in odds_game.items():
        if gid_ in close.index and not og["disputed"]:
            agree.append(dict(game_id=gid_, season=gi.at[gid_, "season"], close=close.at[gid_, "close_line"],
                              ou=og["ou"], x=og["x"], xn=og["xn"], sp_odds=og["spread"], sp_close=close.at[gid_, "close_spread"]))
    agree = pd.DataFrame(agree)
    info = dict(closing_rows_in=c_rows_in, closing_bad_snapshot=int(bad_snap.sum()), points_mismatch=pm, agreement=agree,
                odds_game=odds_game, close=close)
    return k, excl, info


class team_total_history:
    """Per team and season: game dates and game totals (regular season), with a strict date < D average."""

    def __init__(self, g_reg: pd.DataFrame):
        self.d = {}
        for side in ("home", "away"):
            t = pd.DataFrame({"team": g_reg[f"{side}_id"], "season": g_reg["season"], "date": g_reg["date"], "tot": g_reg["actual"]})
            self.d.setdefault("all", []).append(t)
        t = pd.concat(self.d["all"]).sort_values(["team", "season", "date"])
        self.store = {k: (grp["date"].values, np.cumsum(grp["tot"].values)) for k, grp in t.groupby(["team", "season"])}

    def avg_before(self, team, season, date):
        dates, cs = self.store.get((team, season), (np.array([], dtype="datetime64[ns]"), np.array([])))
        n = int(np.searchsorted(dates, np.datetime64(date), side="left"))
        if n == 0:
            return np.nan, 0
        return cs[n - 1] / n, n


# --------------------------------------------------------------------------------------------- features
def _team_series(g_reg: pd.DataFrame, cols: dict):
    """Per (team, season): sorted arrays: dates and the requested value columns (home/away aware).
    cols: name -> (home_col, away_col) in g_reg. Games sorted by (date, game_id)."""
    recs = []
    for side, other in (("home", "away"), ("away", "home")):
        d = pd.DataFrame({"team": g_reg[f"{side}_id"], "season": g_reg["season"], "date": g_reg["date"],
                          "game_id": g_reg["game_id"], "is_home": side == "home"})
        for name, (hc, ac) in cols.items():
            d[name] = g_reg[hc if side == "home" else ac]
        recs.append(d)
    t = pd.concat(recs).sort_values(["team", "season", "date", "game_id"])
    out = {}
    for k, grp in t.groupby(["team", "season"]):
        out[k] = {"date": grp["date"].values, "is_home": grp["is_home"].values,
                  **{n: grp[n].values.astype(float) for n in cols}}
    return out


def _window_mean(series, team, season, date, name, n):
    """Mean of the last n values strictly before `date` in the same season; NaN if fewer than n games."""
    s = series.get((team, season))
    if s is None:
        return np.nan
    idx = int(np.searchsorted(s["date"], np.datetime64(date), side="left"))
    if idx < n:
        return np.nan
    return float(np.mean(s[name][idx - n: idx]))


def season_to_date_means(g_reg: pd.DataFrame):
    """For league averages: per season, sorted game dates and cumulative sums of total, pace and the two teams'
    3PA rates (sum of both)."""
    out = {}
    for s, grp in g_reg.groupby("season"):
        grp = grp.sort_values(["date", "game_id"])
        out[s] = dict(date=grp["date"].values, tot=np.cumsum(grp["actual"].values), pace=np.cumsum(grp["pace"].values),
                      tpar=np.cumsum((grp["home_tpar"] + grp["away_tpar"]).values))
    return out


def compute_features(g_reg: pd.DataFrame, clean: pd.DataFrame, f8_cols: pd.DataFrame | None = None) -> pd.DataFrame:
    """Features f1-f7, f9, f10 for every cleaned game (f8 is attached separately by the caller)."""
    seasons = sorted(g_reg["season"].unique())
    league = season_to_date_means(g_reg)
    # last 30 days of previous season average total
    prev_last30 = {}
    for i, s in enumerate(seasons):
        grp = g_reg[g_reg.season == s]
        last = grp["date"].max()
        sel = grp[grp["date"] >= last - pd.Timedelta(days=29)]
        prev_last30[s] = (sel["actual"].mean(), len(sel), last)
    first_date = {s: g_reg[g_reg.season == s]["date"].min() for s in seasons}

    gi = g_reg.set_index("game_id")
    pace_ser = _team_series(g_reg, {"pace": ("pace", "pace"), "tpar": ("home_tpar", "away_tpar")})
    # team dates (for back-to-back): all regular-season games
    played = {}
    for side in ("home", "away"):
        for tid, dt in zip(g_reg[f"{side}_id"], g_reg["date"]):
            played.setdefault(tid, set()).add(dt)

    # error history (actual - line) only from cleaned lined games
    cl = clean.merge(g_reg[["game_id", "season", "date", "home_id", "away_id", "actual"]], on="game_id")
    cl["err"] = cl["actual"] - cl["line"]
    err_ser = _team_series(cl.sort_values(["date", "game_id"]), {"err": ("err", "err")})

    rows = []
    for r in cl.sort_values(["date", "game_id"]).itertuples():
        s, D = r.season, r.date
        L = league[s]
        n_prior = int(np.searchsorted(L["date"], np.datetime64(D), side="left"))
        # f1
        if n_prior >= 50:
            lg_total = L["tot"][n_prior - 1] / n_prior
        else:
            i = seasons.index(s)
            lg_total = prev_last30[seasons[i - 1]][0] if i > 0 else np.nan
        f1 = r.line - lg_total
        f2 = r.spread  # abs spread (set in cleaning)
        f3 = float((D - first_date[s]).days)
        f4 = float(int((D - pd.Timedelta(days=1)) in played.get(r.home_id, ())) + int((D - pd.Timedelta(days=1)) in played.get(r.away_id, ())))
        # f5 / f9: last-10 means vs league
        if n_prior >= 1:
            lg_pace = L["pace"][n_prior - 1] / n_prior
            lg_tpar = L["tpar"][n_prior - 1] / (2 * n_prior)
        else:
            lg_pace = lg_tpar = np.nan
        ph = _window_mean(pace_ser, r.home_id, s, D, "pace", 10)
        pa = _window_mean(pace_ser, r.away_id, s, D, "pace", 10)
        th = _window_mean(pace_ser, r.home_id, s, D, "tpar", 10)
        ta = _window_mean(pace_ser, r.away_id, s, D, "tpar", 10)
        f5 = (ph + pa) - 2 * lg_pace
        f5_alt = (ph + pa) - lg_pace
        f9 = (th + ta) - 2 * lg_tpar
        f9_alt = (th + ta) - lg_tpar
        # f6 / f10
        eh5 = _window_mean(err_ser, r.home_id, s, D, "err", 5)
        ea5 = _window_mean(err_ser, r.away_id, s, D, "err", 5)
        f6 = (eh5 + ea5) / 2.0
        eh10 = _window_mean(err_ser, r.home_id, s, D, "err", 10)
        ea10 = _window_mean(err_ser, r.away_id, s, D, "err", 10)
        f10 = eh10 - ea10
        f7 = float(r.home_id in (DENVER, UTAH))
        rows.append(dict(game_id=r.game_id, f1=f1, f2=f2, f3=f3, f4=f4, f5=f5, f6=f6, f7=f7, f9=f9, f10=f10,
                         f5_alt=f5_alt, f9_alt=f9_alt, n_games_before=n_prior))
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------------------- f8
# #56 frozen primary formula, flat index 7791 = (A w0.5, B own, C decay5, D k0, E shift on, F none, G split on, H shrink on)
HALF_LIFE = 5.0
SPLIT_C = 10.0
SHIFT_C = 100.0


def decay_mean(vals: np.ndarray, half_life: float = HALF_LIFE) -> np.ndarray:
    """Exponentially weighted mean of rows (n, m); most recent row (last) has weight 1, a row `a` games older
    0.5 ** (a / half_life)."""
    n = len(vals)
    w = 0.5 ** (np.arange(n - 1, -1, -1) / half_life)
    return (w[:, None] * vals).sum(0) / w.sum()


def team_game_table(g: pd.DataFrame) -> pd.DataFrame:
    """Long team-game table (regular season) with O = pts/pace_game, D = opp_pts/pace_game, P = pace_game."""
    recs = []
    for side in ("home", "away"):
        recs.append(pd.DataFrame({
            "game_id": g["game_id"], "season": g["season"], "date": g["date"], "team": g[f"{side}_id"],
            "is_home": side == "home",
            "O": g[f"{side}_pts"] / g["pace"],
            "D": g[("away" if side == "home" else "home") + "_pts"] / g["pace"],
            "P": g["pace"]}))
    return pd.concat(recs).sort_values(["team", "date", "game_id"]).reset_index(drop=True)


class SeasonRatings:
    """Walk-forward ratings for one season S with an optional prior season (a team-game table of S-1 and its
    league levels). All queries for ET date D use only rows with date < D."""

    def __init__(self, tg_cur: pd.DataFrame, g_cur: pd.DataFrame, prior_tg: pd.DataFrame | None):
        self.cur = {t: grp for t, grp in tg_cur.groupby("team")}
        self.g_dates = np.sort(g_cur["date"].values)
        g_sorted = g_cur.sort_values("date")
        self.g_sorted_dates = g_sorted["date"].values
        self.cum_O = np.cumsum((g_sorted["home_pts"] / g_sorted["pace"] + g_sorted["away_pts"] / g_sorted["pace"]).values) / 2.0
        self.cum_P = np.cumsum(g_sorted["pace"].values)
        self.prior = None
        if prior_tg is not None and len(prior_tg):
            self.prior = {t: decay_mean(grp.sort_values(["date", "game_id"])[["O", "D", "P"]].values)
                          for t, grp in prior_tg.groupby("team")}
            self.Lo_prev = prior_tg["O"].mean()
            self.Lp_prev = prior_tg["P"].mean()

    def league(self, D, shift: bool):
        """(Lo, Lp, r_o, r_p) at date D. Lo/Lp are the shifted prior league levels; with no prior they are the
        season-to-date league levels (reading, flagged) and r = 1."""
        n = int(np.searchsorted(self.g_sorted_dates, np.datetime64(D), side="left"))
        if n == 0:
            if self.prior is None:
                return None
            return self.Lo_prev, self.Lp_prev, 1.0, 1.0
        lo_cur, lp_cur = self.cum_O[n - 1] / n, self.cum_P[n - 1] / n
        if self.prior is None:
            return lo_cur, lp_cur, 1.0, 1.0
        if shift:
            w = n / (n + SHIFT_C)
            r_o = 1 + (lo_cur / self.Lo_prev - 1) * w
            r_p = 1 + (lp_cur / self.Lp_prev - 1) * w
        else:
            r_o = r_p = 1.0
        return self.Lo_prev * r_o, self.Lp_prev * r_p, r_o, r_p

    def team_rating(self, team, D, k: float, r_o: float, r_p: float, split_home: bool | None):
        """Blended (O, D, P) of `team` before D. k = prior weight (games). split_home None -> overall;
        True/False -> blend with the team's rating over only its home/away games, w = n_v / (n_v + 10)."""
        grp = self.cur.get(team)
        n_all = 0
        cur_all = None
        if grp is not None:
            idx = int(np.searchsorted(grp["date"].values, np.datetime64(D), side="left"))
            sub = grp.iloc[:idx]
            n_all = len(sub)
            if n_all:
                cur_all = decay_mean(sub[["O", "D", "P"]].values)
        pr = None
        if self.prior is not None and team in self.prior:
            pr = self.prior[team] * np.array([r_o, r_o, r_p])

        def blend(cur, n):
            if n == 0:
                return pr
            if pr is None or k == 0:
                return cur
            return (k * pr + n * cur) / (k + n)

        overall = blend(cur_all, n_all)
        if split_home is None or overall is None:
            return overall
        subv = sub[sub["is_home"] == split_home] if n_all else None
        n_v = 0 if subv is None else len(subv)
        if n_v == 0:
            return overall
        venue = blend(decay_mean(subv[["O", "D", "P"]].values), n_v)
        w = n_v / (n_v + SPLIT_C)
        return w * venue + (1 - w) * overall


def project_total(away, home, w: float, pace_mode: str):
    """away/home: (O, D, P). Total of two teams' points. pace_mode 'own': each team's points use its own pace;
    'avg': both use the mean pace; 'termown': w*O_A*P_A + (1-w)*D_B*P_B."""
    (oa, da, pa), (oh, dh, ph) = away, home
    if pace_mode == "own":
        return (w * oa + (1 - w) * dh) * pa + (w * oh + (1 - w) * da) * ph
    if pace_mode == "avg":
        pm = (pa + ph) / 2.0
        return (w * oa + (1 - w) * dh) * pm + (w * oh + (1 - w) * da) * pm
    if pace_mode == "termown":
        return w * oa * pa + (1 - w) * dh * ph + w * oh * ph + (1 - w) * da * pa
    raise ValueError(pace_mode)


def walk_forward(sr: SeasonRatings, g_cur: pd.DataFrame, *, k, shift, split, pace_mode, w=0.5):
    """Unadjusted projections and league totals Lt for every game in g_cur (NaN where not defined)."""
    proj = np.full(len(g_cur), np.nan)
    lt = np.full(len(g_cur), np.nan)
    for i, r in enumerate(g_cur.itertuples()):
        lg = sr.league(r.date, shift)
        if lg is None:
            continue
        Lo, Lp, r_o, r_p = lg
        a = sr.team_rating(r.away_id, r.date, k, r_o, r_p, False if split else None)
        h = sr.team_rating(r.home_id, r.date, k, r_o, r_p, True if split else None)
        if a is None or h is None:
            continue
        proj[i] = project_total(a, h, w, pace_mode)
        lt[i] = 2 * Lo * Lp
    return proj, lt


def fit_shrink_slope(proj, lt, actual) -> float:
    """OLS with intercept of (actual - Lt) on (proj - Lt); returns the slope b."""
    m = np.isfinite(proj) & np.isfinite(lt)
    x = proj[m] - lt[m]
    y = actual[m] - lt[m]
    xm, ym = x.mean(), y.mean()
    return float(((x - xm) * (y - ym)).sum() / ((x - xm) ** 2).sum())


def f8_for_season(seasons_games: dict, seasons_tg: dict, S: str, S1: str, S2: str | None, pace_mode: str = "own"):
    """Frozen primary formula's shrunk projection for every regular-season game of S.
    b is fitted on S-1's walk-forward projections (prior S-2 when on disk, k=20, shift on, pace avg, no split)."""
    g1 = seasons_games[S1]
    prior2 = seasons_tg[S2] if S2 in seasons_tg else None
    sr1 = SeasonRatings(seasons_tg[S1], g1, prior2)
    proj1, lt1 = walk_forward(sr1, g1, k=20, shift=True, split=False, pace_mode="avg")
    b = fit_shrink_slope(proj1, lt1, g1["actual"].values)
    gS = seasons_games[S]
    sr = SeasonRatings(seasons_tg[S], gS, seasons_tg[S1])
    proj, lt = walk_forward(sr, gS, k=0, shift=True, split=True, pace_mode=pace_mode)
    return lt + b * (proj - lt), proj, b
