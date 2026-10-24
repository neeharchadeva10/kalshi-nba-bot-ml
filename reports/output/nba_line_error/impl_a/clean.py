"""Research log #57, implementation A: data cleaning (pre-registered rules, in this order).

Outputs (reports/output/nba_line_error/):
  games_clean_a.parquet     game_id, date (ET), season, away, home (hoopR codes), line, line_source, spread, actual, tip
  games_excluded_a.csv      every excluded line row with its reason (season, source, game_id, date, home, away, line, reason, detail)
  DATA_CHECKS.md / data_checks_a.json
  odds_points_vs_hoopr_a.csv  every OddsData row whose Points differ from the hoopR final (hoopR wins; the game is kept)

Nothing here reads a model, a feature or y; `actual` comes from hoopR only.
Run:  .venv/Scripts/python.exe reports/output/nba_line_error/impl_a/clean.py"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lea_lib as L  # noqa: E402

REASONS = ["snapshot_at_or_after_commence", "unmatched", "not_regular_season", "ambiguous_match", "duplicate_game",
           "line_missing", "line_disputed", "line_out_of_range_170_275", "line_far_from_team_mean_gt30"]


# ------------------------------------------------------------------ matching (pure)
def match_rows(rows: pd.DataFrame, cand: pd.DataFrame) -> pd.DataFrame:
    """rows: date, home_f, away_f (franchise codes).  cand: game_id, date, home_f, away_f, season_type (all hoopR NBA games).
    A row matches a candidate with the same home/away franchises and date within +-1 day.  If several candidates are
    inside the window the one on the exact date wins when it is unique; otherwise the row is 'ambiguous'.
    Returns a frame with the rows' index and columns game_id, status in {'matched','unmatched','ambiguous'}."""
    by = {}
    for r in cand.itertuples(index=False):
        by.setdefault((r.home_f, r.away_f), []).append((r.date, int(r.game_id)))
    gid, status = [], []
    one = pd.Timedelta(days=1)
    for r in rows.itertuples(index=False):
        c = [(d, g) for d, g in by.get((r.home_f, r.away_f), []) if abs(d - r.date) <= one]
        if len(c) > 1:
            ex = [g for d, g in c if d == r.date]
            if len(ex) == 1:
                c = [(r.date, ex[0])]
        if len(c) == 1:
            gid.append(c[0][1]); status.append("matched")
        elif not c:
            gid.append(-1); status.append("unmatched")
        else:
            gid.append(-1); status.append("ambiguous")
    return pd.DataFrame({"game_id": gid, "status": status}, index=rows.index)


class TeamTotals:
    """Season-to-date mean of game totals per team from hoopR finals, strictly before a date."""

    def __init__(self, tg_season: pd.DataFrame):
        self.d = {}
        t = tg_season.assign(total=tg_season.pts + tg_season.opp_pts).sort_values(["date", "tip"], kind="stable")
        for team, g in t.groupby("team"):
            self.d[team] = (g.date.values.astype("datetime64[D]"), np.concatenate([[0.0], np.cumsum(g.total.values)]))

    def mean_before(self, team, D):
        if team not in self.d:
            return float("nan"), 0
        dates, cum = self.d[team]
        n = int(np.searchsorted(dates, np.datetime64(D, "D"), side="left"))
        return (float(cum[n] / n) if n else float("nan")), n


def sanity_status(line, home, away, D, tt: TeamTotals):
    """None if the line passes, else a (reason, detail).  Range [170,275]; then, if both teams have >= 5 earlier games,
    |line - mean of the two teams' season-to-date average game totals| <= 30."""
    if not (L.LINE_LO <= line <= L.LINE_HI):
        return "line_out_of_range_170_275", f"line={line}"
    mh, nh = tt.mean_before(home, D)
    ma, na = tt.mean_before(away, D)
    if nh >= L.MIN_PRIOR_FOR_MEAN and na >= L.MIN_PRIOR_FOR_MEAN:
        ref = (mh + ma) / 2.0
        if abs(line - ref) > L.MEAN_TOL:
            return "line_far_from_team_mean_gt30", f"line={line} ref={ref:.1f} (n {nh}/{na})"
    return None


def parse_spread(v):
    """OddsData Spread: float, or 'PK' (pick'em) = 0.  Positive = home favoured (checked against the moneylines)."""
    if isinstance(v, str):
        return 0.0 if v.strip().upper() in ("PK", "PICK", "PICKEM") else pd.to_numeric(v, errors="coerce")
    return float(v) if v is not None else float("nan")


# ------------------------------------------------------------------ OddsData
def odds_table_names(conn):
    return {n for (n,) in conn.execute("select name from sqlite_master where type='table'")}


def load_odds_season(season: str, conn) -> pd.DataFrame:
    """One row per (date, home, away, occurrence): ou_x/ou_n, spread_x/spread_n, pts_x/pts_n, n_tables.
    X table = 'odds_S' or 'S' (dates 'S-MMDD'); X_new = 'odds_S_new' (plain dates).  Both are read row-by-row on the key."""
    names = odds_table_names(conn)
    tx = next((t for t in (f"odds_{season}", season) if t in names), None)
    tn = f"odds_{season}_new" if f"odds_{season}_new" in names else None

    def one(tab, conv):
        d = pd.read_sql(f'select Date, Home, Away, OU, Spread, Points from "{tab}"', conn)
        d["date"] = d.Date.map(conv)
        d["home_f"] = d.Home.map(L.NAME_TO_FRANCHISE)
        d["away_f"] = d.Away.map(L.NAME_TO_FRANCHISE)
        d["ou"] = pd.to_numeric(d.OU, errors="coerce")
        d["spread"] = d.Spread.map(parse_spread).astype(float)
        d["pts"] = pd.to_numeric(d.Points, errors="coerce")
        d["occ"] = d.groupby(["date", "Home", "Away"]).cumcount()
        return d[["date", "Home", "Away", "home_f", "away_f", "occ", "ou", "spread", "pts"]]

    parts = {}
    if tx:
        parts["x"] = one(tx, L.old_odds_date)
    if tn:
        parts["n"] = one(tn, lambda s: pd.Timestamp(str(s)[:10]))
    if not parts:
        return pd.DataFrame()
    key = ["date", "Home", "Away", "occ"]
    if len(parts) == 2:
        m = parts["x"].merge(parts["n"], on=key, how="outer", suffixes=("_x", "_n"))
        m["home_f"] = m.home_f_x.fillna(m.home_f_n)
        m["away_f"] = m.away_f_x.fillna(m.away_f_n)
        m = m.drop(columns=["home_f_x", "home_f_n", "away_f_x", "away_f_n"])
    else:
        k = next(iter(parts))
        m = parts[k].rename(columns={"ou": f"ou_{k}", "spread": f"spread_{k}", "pts": f"pts_{k}"})
        for c in ("ou", "spread", "pts"):
            other = "n" if k == "x" else "x"
            m[f"{c}_{other}"] = np.nan
    m["n_tables"] = m[["ou_x", "ou_n"]].notna().sum(axis=1)
    return m.sort_values(["date", "Home", "Away", "occ"], kind="stable").reset_index(drop=True)


def odds_line_choice(m: pd.DataFrame) -> pd.DataFrame:
    """line / spread / source / disputed per merged odds row: X_new is used when present (else X); if both exist and differ by
    more than 1.0 point the row is 'disputed'."""
    out = m.copy()
    both = out.ou_x.notna() & out.ou_n.notna()
    out["disputed"] = both & ((out.ou_x - out.ou_n).abs() > L.DISPUTE_TOL)
    out["line"] = out.ou_n.where(out.ou_n.notna(), out.ou_x)
    out["line_source"] = np.where(out.ou_n.notna(), "oddsdata_new", "oddsdata_x")
    out["spread"] = out.spread_n.where(out.spread_n.notna(), out.spread_x)
    out["pts_odds"] = out.pts_n.where(out.pts_n.notna(), out.pts_x)
    return out


# ------------------------------------------------------------------ the game universe
def all_nba_games_index() -> pd.DataFrame:
    """Every hoopR NBA-vs-NBA game of ALL season types (1-5) 2007-08..2025-26 (home/away franchise, date, season_type)."""
    parts = []
    for y in range(2008, 2027):
        tb = L.read_year(y)
        tb = tb[tb.team_abbreviation.isin(L.NBA_HOOPR) & tb.opponent_team_abbreviation.isin(L.NBA_HOOPR)]
        gdt = pd.to_datetime(tb.game_date_time)
        if gdt.dt.tz is None:
            gdt = gdt.dt.tz_localize("UTC")
        et = gdt.dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
        h = pd.DataFrame({"game_id": tb.game_id.astype("int64").values, "date": et.values, "team": tb.team_abbreviation.values,
                          "opp": tb.opponent_team_abbreviation.values, "venue": tb.team_home_away.values,
                          "season_type": tb.season_type.values, "file_year": y})
        h = h[h.venue == "home"]
        parts.append(pd.DataFrame({"game_id": h.game_id.values, "date": h.date.values,
                                   "home_f": h.team.map(L.FRANCHISE).values, "away_f": h.opp.map(L.FRANCHISE).values,
                                   "season_type": h.season_type.values, "file_year": y}))
    return pd.concat(parts, ignore_index=True)


# ------------------------------------------------------------------ per-season cleaning
def clean_odds_season(season, odds_choice, cand, games_s, tt):
    """Pre-1.0 path (seasons before 2020-21): OddsData rows -> kept / excluded lists.  Returns (kept DataFrame, excluded list, counts)."""
    r = odds_choice
    mt = match_rows(r[["date", "home_f", "away_f"]], cand)
    r = r.assign(game_id=mt.game_id.values, status=mt.status.values)
    st_by_id = cand.set_index("game_id").season_type
    r["season_type"] = [int(st_by_id.get(g, -1)) if g >= 0 else -1 for g in r.game_id]
    reason = pd.Series([None] * len(r), index=r.index, dtype=object)
    detail = pd.Series([""] * len(r), index=r.index, dtype=object)
    reason[r.status == "unmatched"] = "unmatched"
    reason[r.status == "ambiguous"] = "ambiguous_match"
    mm = reason.isna() & (r.season_type != 2)
    reason[mm] = "not_regular_season"
    detail[mm] = "season_type=" + r.season_type[mm].astype(str)
    ok = reason.isna()
    dup = ok & r.game_id.where(ok).duplicated(keep=False)          # duplicates among the rows still in play
    reason[dup] = "duplicate_game"
    ok = reason.isna()
    reason[ok & r.disputed] = "line_disputed"
    detail[ok & r.disputed] = ("x=" + r.ou_x.astype(str) + " new=" + r.ou_n.astype(str))[ok & r.disputed]
    ok = reason.isna()
    reason[ok & r.line.isna()] = "line_missing"
    gi = games_s.set_index("game_id")
    for i in r.index[reason.isna()]:
        g = gi.loc[r.at[i, "game_id"]]
        s = sanity_status(float(r.at[i, "line"]), g.home, g.away, g.date, tt)
        if s:
            reason[i], detail[i] = s
    return r, reason, detail


def clean_closing_season(season, closing_s, cand, games_s, tt):
    r = closing_s.copy()
    commence = pd.to_datetime(r.commence_time, utc=True)
    snap = pd.to_datetime(r.snapshot, utc=True)
    r["inplay"] = (snap >= commence)
    st_by_id = cand.set_index("game_id").season_type
    r["season_type"] = [int(st_by_id.get(int(g), -1)) for g in r.game_id]
    reason = pd.Series([None] * len(r), index=r.index, dtype=object)
    detail = pd.Series([""] * len(r), index=r.index, dtype=object)
    reason[r.inplay] = "snapshot_at_or_after_commence"
    detail[r.inplay] = (r.snapshot.astype(str) + " >= " + r.commence_time.astype(str))[r.inplay]
    ok = reason.isna()
    reason[ok & (r.season_type == -1)] = "unmatched"
    ok = reason.isna()
    reason[ok & (r.season_type != 2)] = "not_regular_season"
    ok = reason.isna()
    reason[ok & r.over_under.isna()] = "line_missing"
    ok = reason.isna()
    reason[ok & r.game_id.where(ok).duplicated(keep=False)] = "duplicate_game"
    gi = games_s.set_index("game_id")
    for i in r.index[reason.isna()]:
        g = gi.loc[int(r.at[i, "game_id"])]
        s = sanity_status(float(r.at[i, "over_under"]), g.home, g.away, g.date, tt)
        if s:
            reason[i], detail[i] = s
    return r, reason, detail


def main():
    out_dir = L.STUDY
    tg = L.load_team_games()
    games = L.games_from_team_games(tg)
    cand = all_nba_games_index()
    conn = sqlite3.connect(f"file:{L.ODDS_DB.as_posix()}?mode=ro", uri=True)
    closing = pd.read_parquet(L.CLOSING)

    kept_parts, excl_parts = [], []
    variant_parts = {"fallback": [], "bubble": [], "both": []}
    checks = {"seasons": {}, "odds_x_vs_new": {}, "closing_vs_odds": {}, "hoopr": {}, "pace_check": {}, "odds_points_mismatch": {}}
    pts_rows = []
    gc_season = {}
    # ---- hoopR sanity per season (incl. the 2007-08 prior)
    for s in [L.label_of(y) for y in range(2008, 2027)]:
        gs = games[games.season == s]
        t = tg[tg.season == s]
        ncode = t.groupby("team").size()
        consistent = int(((t[t.venue == "home"].set_index("game_id").pts - t[t.venue == "away"].set_index("game_id").opp_pts).abs() > 0).sum())
        tb = L.read_year(L.year_of(s))
        rr_ = tb[tb.season_type == 2]
        td = L.tov_diagnostics(tb)
        checks["hoopr"][s] = {"oreb_mean": float(rr_.offensive_rebounds.mean()), "dreb_mean": float(rr_.defensive_rebounds.mean()),
                              "tov_mean": float(rr_.turnovers.mean()), "tov_rule": td["rule"], "tov_ratio": td["ratio_total_over_turnovers"],
                              "tov_rows_total_lt_turnovers": td["rows_total_lt_turnovers"], "tov_rows_total_nan": td["rows_total_nan"],"regular_season_games": int(len(gs)), "teams": int(t.team.nunique()),
                              "games_per_team_min": int(ncode.min()), "games_per_team_max": int(ncode.max()),
                              "first_date": str(gs.date.min().date()), "last_date": str(gs.date.max().date()),
                              "score_inconsistent_home_vs_away": consistent,
                              "game_id_duplicated_across_seasons": int(gs.game_id.isin(games[games.season != s].game_id).sum())}
        # possessions check: ORtg x pace / 100 vs points per game, per team-season
        g2 = t.groupby("team").agg(n=("pts", "size"), pts=("pts", "sum"), poss=("poss", "sum"), pace=("pace", "mean"),
                                   ppg=("pts", "mean"), opp_poss=("opp_poss", "sum"))
        g2["ortg"] = 100 * g2.pts / g2.poss
        g2["calc_ppg"] = g2.ortg * g2.pace / 100
        g2["diff"] = (g2.calc_ppg - g2.ppg).abs()
        checks["pace_check"][s] = {"max_abs_diff_ppg": float(g2["diff"].max()), "worst_team": str(g2["diff"].idxmax()),
                                   "pace_min": float(g2.pace.min()), "pace_max": float(g2.pace.max()),
                                   "ortg_min": float(g2.ortg.min()), "ortg_max": float(g2.ortg.max())}
    for s in L.ALL_SEASONS:
        gs = games[games.season == s].reset_index(drop=True)
        gc_season[s] = gs
        tt = TeamTotals(tg[tg.season == s])
        cs = cand[cand.file_year == L.year_of(s)]
        # ---- odds (always loaded when it exists: needed for X vs X_new, closing agreement, points check)
        om = load_odds_season(s, conn)
        oc = odds_line_choice(om) if len(om) else om
        if len(om):
            both = om[om.n_tables == 2]
            d = (both.ou_x - both.ou_n).abs()
            checks["odds_x_vs_new"][s] = {
                "rows_x": int(om.ou_x.notna().sum()), "rows_new": int(om.ou_n.notna().sum()), "rows_in_both": int(len(both)),
                "only_x": int((om.ou_x.notna() & om.ou_n.isna()).sum()), "only_new": int((om.ou_n.notna() & om.ou_x.isna()).sum()),
                "ou_differ_any": int((d > 0).sum()), "ou_differ_gt1": int((d > L.DISPUTE_TOL).sum()),
                "max_abs_ou_diff": float(d.max()) if len(d) else None,
                "spread_differ_any": int(((both.spread_x - both.spread_n).abs() > 0).sum()),
                "points_differ_any": int((both.pts_x != both.pts_n).sum())}
            mt = match_rows(oc[["date", "home_f", "away_f"]], cs if len(cs) else cand)
            oc = oc.assign(game_id=mt.game_id.values, status=mt.status.values)
            mp = oc[oc.status == "matched"].merge(gs[["game_id", "actual", "date"]].rename(columns={"date": "hoopr_date"}), on="game_id", how="left")
            mp = mp[mp.actual.notna()]
            bad = mp[(mp.pts_odds != mp.actual)]
            checks["odds_points_mismatch"][s] = {"matched_regular_rows": int(len(mp)), "points_differ": int(len(bad)),
                                                 "points_differ_gt4": int(((bad.pts_odds - bad.actual).abs() > 4).sum())}
            for r in bad.itertuples():
                pts_rows.append({"season": s, "game_id": r.game_id, "date": str(r.date.date()), "home": r.Home, "away": r.Away,
                                 "odds_points": r.pts_odds, "hoopr_total": r.actual, "diff": r.pts_odds - r.actual})
        # ---- closing agreement
        cl_all = closing.copy()
        cl_all["game_id"] = cl_all.game_id.astype("int64")
        cl_s = cl_all[cl_all.game_id.isin(gs.game_id)]
        if len(om) and len(cl_s) and s >= "2019-20":
            comp = {}
            for tag, col in (("oddsdata_new", "ou_n"), ("oddsdata_x", "ou_x")):
                o = oc[oc.status == "matched"].drop_duplicates("game_id", keep=False)
                o = o[o[col].notna()][["game_id", col]].merge(cl_s[["game_id", "over_under"]], on="game_id")
                dd = (o[col] - o.over_under)
                comp[tag] = {"n_both": int(len(o)), "exact": int((dd == 0).sum()), "abs_le_0.5": int((dd.abs() <= 0.5).sum()),
                             "abs_le_1": int((dd.abs() <= 1).sum()), "abs_gt_1": int((dd.abs() > 1).sum()),
                             "abs_gt_3": int((dd.abs() > 3).sum()), "mean_odds_minus_close": float(dd.mean()) if len(dd) else None,
                             "rmse": float(np.sqrt((dd ** 2).mean())) if len(dd) else None,
                             "median_abs": float(dd.abs().median()) if len(dd) else None,
                             "n_odds_in_range": int(o[col].between(L.LINE_LO, L.LINE_HI).sum()),
                             "rmse_in_range": (lambda q: float(np.sqrt((q ** 2).mean())) if len(q) else None)(dd[o[col].between(L.LINE_LO, L.LINE_HI)])}
            checks["closing_vs_odds"][s] = comp
        # ---- the cleaning proper
        if s >= L.CLOSING_FROM:
            cl = cl_all
            # rows of this season: those matching a hoopR game of this season (any type) plus unmatched rows dated in the season
            gid_all_season = set(cs.game_id)
            inseason = cl[cl.game_id.isin(gid_all_season)]
            lo, hi = cs.date.min() - pd.Timedelta(days=1), cs.date.max() + pd.Timedelta(days=1)
            known = set(cand.game_id)
            unk = cl[~cl.game_id.isin(known)]
            unk = unk[pd.to_datetime(unk.commence_time, utc=True).dt.tz_localize(None).map(lambda d: L.season_of_date_for_unmatched(d)) == s]
            rows = pd.concat([inseason, unk], ignore_index=True)
            rr, reason, detail = clean_closing_season(s, rows, cand, gs, tt)
            n_in = len(rr)
            src = "closing_lines"
            line = rr.over_under.astype(float)
            sp = rr.home_team_spread.astype(float)
            kept_mask = reason.isna()
            keep = pd.DataFrame({"game_id": rr.game_id[kept_mask].astype("int64").values, "line": line[kept_mask].values,
                                 "line_source": src, "spread": sp[kept_mask].values})
            ex = pd.DataFrame({"season": s, "source": src, "game_id": rr.game_id.values, "date": "", "home": "", "away": "",
                               "line": line.values, "reason": reason.values, "detail": detail.values})[~kept_mask.values]
        else:
            if not len(oc):
                raise SystemExit(f"no OddsData for {s}")
            rr, reason, detail = clean_odds_season(s, oc, cs, gs, tt)
            n_in = len(rr)
            kept_mask = reason.isna()
            keep = pd.DataFrame({"game_id": rr.game_id[kept_mask].astype("int64").values, "line": rr.line[kept_mask].values,
                                 "line_source": rr.line_source[kept_mask].values, "spread": rr.spread[kept_mask].values})
            ex = pd.DataFrame({"season": s, "source": "oddsdata", "game_id": rr.game_id.values,
                               "date": rr.date.astype(str).str[:10].values, "home": rr.Home.values, "away": rr.Away.values,
                               "line": rr.line.values, "reason": reason.values, "detail": detail.values})[~kept_mask.values]
        gk = gs.merge(keep, on="game_id", how="inner")
        assert gk.game_id.is_unique, "a game kept twice"
        kept_parts.append(gk)
        excl_parts.append(ex)
        # ---- optional alternative readings of "which line" (written ONLY to games_clean_a__<variant>.parquet, never the default)
        gv = gk.copy()
        if s == "2019-20":                 # reading B: the closing_lines file also holds the 2019-20 bubble games -> use them
            rows_b = cl_all[cl_all.game_id.isin(set(cs.game_id))]
            rrb, reb, _ = clean_closing_season(s, rows_b, cand, gs, tt)
            kb = pd.DataFrame({"game_id": rrb.game_id[reb.isna()].astype("int64").values, "line": rrb.over_under[reb.isna()].astype(float).values,
                               "line_source": "closing_lines", "spread": rrb.home_team_spread[reb.isna()].astype(float).values})
            gb_ = gs.merge(kb, on="game_id", how="inner")
            gv_b = pd.concat([gk[~gk.game_id.isin(gb_.game_id)], gb_], ignore_index=True)
            variant_parts["bubble"].append(gv_b)
            variant_parts["both"].append(gv_b)
            variant_parts["fallback"].append(gk)
        elif s >= L.CLOSING_FROM and len(oc):   # reading B: where no closing line exists fall back to the OddsData line
            rr2, re2, _ = clean_odds_season(s, oc, cs, gs, tt)
            k2 = pd.DataFrame({"game_id": rr2.game_id[re2.isna()].astype("int64").values, "line": rr2.line[re2.isna()].values,
                               "line_source": rr2.line_source[re2.isna()].values, "spread": rr2.spread[re2.isna()].values})
            g2_ = gs.merge(k2, on="game_id", how="inner")
            g2_ = g2_[~g2_.game_id.isin(gk.game_id)]
            g2_ = g2_.assign(line_source="fallback_" + g2_.line_source)
            gfb = pd.concat([gk, g2_], ignore_index=True)
            variant_parts["fallback"].append(gfb)
            variant_parts["both"].append(gfb)
            variant_parts["bubble"].append(gk)
        else:
            for v in ("fallback", "bubble", "both"):
                variant_parts[v].append(gk)
        cnt = reason.fillna("kept").value_counts().to_dict()
        # games with a clean line but a points mismatch are kept (hoopR is the truth)
        checks["seasons"][s] = {
            "line_source": "closing_lines" if s >= L.CLOSING_FROM else "oddsdata (X_new, else X)",
            "hoopr_regular_games": int(len(gs)), "line_rows_in": int(n_in),
            "excluded": {k: int(cnt.get(k, 0)) for k in REASONS}, "rows_out": int(len(gk)),
            "hoopr_games_without_clean_line": int(len(gs) - len(gk))}
        assert n_in == len(gk) + sum(int(cnt.get(k, 0)) for k in REASONS), f"row accounting broken in {s}"
        # fallback info: closing seasons - how many hoopR games lacking a closing line have an OddsData line
        if s >= L.CLOSING_FROM and len(oc):
            miss = set(gs.game_id) - set(gk.game_id)
            fb = oc[(oc.status == "matched") & oc.game_id.isin(miss)]
            checks["seasons"][s]["games_without_closing_but_with_oddsdata_row"] = int(fb.game_id.nunique())

    clean = pd.concat(kept_parts, ignore_index=True)
    clean = clean[["game_id", "date", "season", "away", "home", "line", "line_source", "spread", "actual", "tip"]]
    clean = clean.sort_values(["date", "tip", "game_id"], kind="stable").reset_index(drop=True)
    clean.to_parquet(out_dir / "games_clean_a__strict.parquet", index=False)
    for v, parts in variant_parts.items():
        cv = pd.concat(parts, ignore_index=True)[clean.columns]
        cv.sort_values(["date", "tip", "game_id"], kind="stable").reset_index(drop=True).to_parquet(
            out_dir / f"games_clean_a__{v}.parquet", index=False)
        print("variant", v, len(cv), "games (strict", len(clean), ")")
        if v == "both":        # FINAL set: closing preferred (incl. the 2019-20 bubble), OddsData where no closing line exists ("where present")
            cv.sort_values(["date", "tip", "game_id"], kind="stable").reset_index(drop=True).to_parquet(out_dir / "games_clean_a.parquet", index=False)
            checks["final_games_by_season"] = cv.groupby("season").size().to_dict()
            checks["final_fallback_by_season"] = cv[cv.line_source.str.startswith("fallback")].groupby("season").size().to_dict()
    excl = pd.concat(excl_parts, ignore_index=True)
    excl.to_csv(out_dir / "games_excluded_a.csv", index=False)
    pd.DataFrame(pts_rows).to_csv(out_dir / "odds_points_vs_hoopr_a.csv", index=False)
    (out_dir / "data_checks_a.json").write_text(json.dumps(checks, indent=1, default=str))
    write_md(checks, clean, excl, pts_rows, out_dir / "DATA_CHECKS.md")
    print(f"clean games: {len(clean)}; excluded rows: {len(excl)}")
    for s, c in checks["seasons"].items():
        print(s, c["line_rows_in"], "->", c["rows_out"], {k: v for k, v in c["excluded"].items() if v})


def write_md(checks, clean, excl, pts_rows, path):
    L_ = []
    A = L_.append
    A("# DATA_CHECKS - research #57, implementation A\n")
    A("Generated by impl_a/clean.py. hoopR is the truth for finals. No outcome-vs-line quantity is computed here.\n")
    A("## 1. hoopR regular season (NBA vs NBA only, both teams in the 30 franchises)\n")
    A("| season | games | teams | games/team min-max | first | last | score inconsistent | id dup across seasons |")
    A("|---|---|---|---|---|---|---|---|")
    for s, c in checks["hoopr"].items():
        A(f"| {s} | {c['regular_season_games']} | {c['teams']} | {c['games_per_team_min']}-{c['games_per_team_max']} | {c['first_date']} | {c['last_date']} | {c['score_inconsistent_home_vs_away']} | {c['game_id_duplicated_across_seasons']} |")
    A("\n### hoopR box-score column checks (regular season, per team-game means)\n")
    A("| season | OREB | DREB | TOV (player) | total_turnovers/turnovers | rule used for TOV | rows total<tov | rows total NaN |")
    A("|---|---|---|---|---|---|---|---|")
    for s, c in checks["hoopr"].items():
        A(f"| {s} | {c['oreb_mean']:.2f} | {c['dreb_mean']:.2f} | {c['tov_mean']:.2f} | {c['tov_ratio']:.2f} | {c['tov_rule']} | {c['tov_rows_total_lt_turnovers']} | {c['tov_rows_total_nan']} |")
    A("\nKnown hoopR defects: 2007-08 OREB 8.4 / DREB 11.2 are not real (league OREB is ~11, DREB ~30), so 2007-08 possessions (used only as "
      "the 2008-09 prior of f8) are overstated by ~2.6 per team; 2007-08..2011-12 total_turnovers is doubled (ratio 2.0).\n")
    A("\n## 2. Possession check (0.96 x (FGA - OREB + TOV + 0.44 FTA)); ORtg x pace / 100 vs points per game, every team-season\n")
    A("| season | max abs diff (ppg) | worst team | pace min-max | ORtg min-max |")
    A("|---|---|---|---|---|")
    for s, c in checks["pace_check"].items():
        A(f"| {s} | {c['max_abs_diff_ppg']:.3f} | {c['worst_team']} | {c['pace_min']:.1f}-{c['pace_max']:.1f} | {c['ortg_min']:.1f}-{c['ortg_max']:.1f} |")
    A("\n(pace = mean over games of the two teams' average possessions, so the identity holds only up to the opponent's possessions; "
      "hoopR 2017-18 has no total_turnovers: turnovers are used there.)\n")
    A("## 3. Cleaning: rows in, excluded by reason, rows out (first failing rule, in the order of the columns)\n")
    hdr = ["season", "source", "hoopR reg games", "rows in"] + REASONS + ["rows out", "games w/o clean line"]
    A("| " + " | ".join(hdr) + " |")
    A("|" + "---|" * len(hdr))
    for s, c in checks["seasons"].items():
        A("| " + " | ".join([s, c["line_source"], str(c["hoopr_regular_games"]), str(c["line_rows_in"])] +
                            [str(c["excluded"][k]) for k in REASONS] + [str(c["rows_out"]), str(c["hoopr_games_without_clean_line"])]) + " |")
    A("\nTotals: rows in %d, rows out %d, excluded %d." % (sum(c["line_rows_in"] for c in checks["seasons"].values()),
                                                         sum(c["rows_out"] for c in checks["seasons"].values()), len(excl)))
    A("\nClosing-line seasons: hoopR games without a closing line but with an OddsData row (NOT used as fallback): " +
      ", ".join(f"{s}: {c['games_without_closing_but_with_oddsdata_row']}" for s, c in checks["seasons"].items() if "games_without_closing_but_with_oddsdata_row" in c))
    A("\n## 4. OddsData table X vs X_new (row by row on date/home/away)\n")
    A("| season | rows X | rows new | in both | only X | only new | OU differ (any) | OU differ >1.0 | max abs OU diff | spread differ | points differ |")
    A("|---|---|---|---|---|---|---|---|---|---|---|")
    for s, c in checks["odds_x_vs_new"].items():
        A(f"| {s} | {c['rows_x']} | {c['rows_new']} | {c['rows_in_both']} | {c['only_x']} | {c['only_new']} | {c['ou_differ_any']} | {c['ou_differ_gt1']} | {c['max_abs_ou_diff']} | {c['spread_differ_any']} | {c['points_differ_any']} |")
    A("\n## 5. closing_lines vs OddsData (games in both; closing_lines starts 2020-07-30 so 2019-20 covers the bubble only)\n")
    A("| season | table | n | exact | <=0.5 | <=1 | >1 | >3 | mean(odds - close) | rmse | median abs | odds in range | rmse (in-range rows) |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for s, comp in checks["closing_vs_odds"].items():
        for t, c in comp.items():
            m = "" if c["mean_odds_minus_close"] is None else f"{c['mean_odds_minus_close']:.3f}"
            r = "" if c["rmse"] is None else f"{c['rmse']:.3f}"
            ma = "" if c["median_abs"] is None else f"{c['median_abs']:.2f}"
            ri = "" if c["rmse_in_range"] is None else f"{c['rmse_in_range']:.3f}"
            A(f"| {s} | {t} | {c['n_both']} | {c['exact']} | {c['abs_le_0.5']} | {c['abs_le_1']} | {c['abs_gt_1']} | {c['abs_gt_3']} | {m} | {r} | {ma} | {c['n_odds_in_range']} | {ri} |")
    A("\n## 6. OddsData Points vs hoopR final (hoopR wins; the game is KEPT, mismatches are only listed)\n")
    A("| season | matched regular rows | points differ | differ by >4 |")
    A("|---|---|---|---|")
    for s, c in checks["odds_points_mismatch"].items():
        A(f"| {s} | {c['matched_regular_rows']} | {c['points_differ']} | {c['points_differ_gt4']} |")
    A(f"\nAll {len(pts_rows)} mismatching rows are in odds_points_vs_hoopr_a.csv. First 40:\n")
    A("| season | game_id | date | home | away | odds pts | hoopR total |")
    A("|---|---|---|---|---|---|---|")
    for r in pts_rows[:40]:
        A(f"| {r['season']} | {r['game_id']} | {r['date']} | {r['home']} | {r['away']} | {r['odds_points']} | {r['hoopr_total']} |")
    A("\n## 7. Sanity failures and every other exclusion (see games_excluded_a.csv for all rows)\n")
    for reason in ["line_out_of_range_170_275", "line_far_from_team_mean_gt30", "line_disputed", "duplicate_game", "ambiguous_match", "not_regular_season", "unmatched", "snapshot_at_or_after_commence", "line_missing"]:
        e = excl[excl.reason == reason]
        A(f"\n### {reason}: {len(e)} rows ({', '.join(f'{s}:{n}' for s, n in e.season.value_counts().sort_index().items())})\n")
        if reason in ("line_out_of_range_170_275", "line_far_from_team_mean_gt30", "line_disputed"):
            A("| season | game_id | date | home | away | line | detail |")
            A("|---|---|---|---|---|---|---|")
            for r in e.head(80).itertuples():
                A(f"| {r.season} | {r.game_id} | {r.date} | {r.home} | {r.away} | {r.line} | {r.detail} |")
            if len(e) > 80:
                A(f"... {len(e) - 80} more in the CSV.")
    path.write_text("\n".join(L_) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
