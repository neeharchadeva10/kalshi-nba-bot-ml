"""Research log #38: per-game ratings from 2025-26 box scores strictly before the game's ET date, blended with
last season's ratings (league-shifted). Pure functions + one class; unit-tested in test_v2.py.
Reuses #37's lib.py (possessions, TEAM_MAP) from reports/output/nba_totals_pace (imported, not modified)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
PACE_DIR = HERE.parent / "nba_totals_pace"
if str(PACE_DIR) not in sys.path:
    sys.path.append(str(PACE_DIR))   # append: v2 run.py must win the name "run"
import lib  # noqa: E402  (#37 lib.py)

LG_SHRINK = 100.0                     # fixed, not tuned (pre-registration, step 2)
K_GRID = (0, 5, 10, 20, 40)
CUR_SEASON_TYPES = (2, 3, 5)          # regular season, playoffs, play-in (preseason = 1 excluded)


def fix_tov(total_turnovers, turnovers):
    """Amendment 1 (#38): tov = total_turnovers if finite and >= turnovers, else turnovers.
    (hoopR has a NaN total_turnovers for CHI 2025-12-01 and zeros for 4 other team-games.)"""
    idx = total_turnovers.index if isinstance(total_turnovers, pd.Series) else None       # keep the caller's index (rows are filtered upstream)
    tot = np.asarray(total_turnovers, float)
    tur = np.asarray(turnovers, float)
    ok = np.isfinite(tot) & (tot >= tur)
    return pd.Series(np.where(ok, tot, tur), index=idx)


def team_games(tb, season_types):
    """One row per team-game: date (ET, datetime64[D]), team (Kalshi code), pts, opp_pts, poss, opp_poss, pace.
    Teams not in TEAM_MAP (All-Star teams) are dropped. Date comes from game_date_time converted to ET."""
    tb = tb[tb.season_type.isin(season_types)].copy()
    tb["poss"] = lib.possessions(tb.field_goals_attempted, tb.offensive_rebounds,
                                 fix_tov(tb.total_turnovers, tb.turnovers), tb.free_throws_attempted)
    opp = tb[["game_id", "team_abbreviation", "poss"]].rename(
        columns={"team_abbreviation": "opponent_team_abbreviation", "poss": "opp_poss"})
    m = tb.merge(opp, on=["game_id", "opponent_team_abbreviation"], how="inner")
    gdt = pd.to_datetime(m.game_date_time)
    if gdt.dt.tz is None:
        gdt = gdt.dt.tz_localize("UTC")
    et = gdt.dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
    out = pd.DataFrame({"date": et.values, "team": m.team_abbreviation.map(lib.to_kalshi).values,
                        "pts": m.team_score.values.astype(float), "opp_pts": m.opponent_team_score.values.astype(float),
                        "poss": m.poss.values.astype(float), "opp_poss": m.opp_poss.values.astype(float)})
    out = out.dropna(subset=["team"]).copy()
    for c in ("pts", "opp_pts", "poss", "opp_poss"):
        assert np.isfinite(out[c].values).all(), f"non-finite box-score input in {c}"
    out["pace"] = (out.poss + out.opp_poss) / 2.0
    return out.sort_values("date", kind="stable").reset_index(drop=True)


def prior_from_tg(tg):
    """From last season's team-games: ({team: (O_prev, D_prev, P_prev)}, Lo_prev, Lp_prev).
    ORtg = sum pts / sum own poss; DRtg = sum opp pts / sum opp poss; pace = mean per-game pace (as #37)."""
    prev = {t: (g.pts.sum() / g.poss.sum(), g.opp_pts.sum() / g.opp_poss.sum(), g.pace.mean())
            for t, g in tg.groupby("team")}
    return prev, float(tg.pts.sum() / tg.poss.sum()), float(tg.pace.mean())


def shrunk_ratio(cur, prev, n_lg, c=LG_SHRINK):
    """r = 1 + (cur/prev - 1) * n_lg / (n_lg + c).  n_lg = 0 -> exactly 1."""
    return 1.0 + (cur / prev - 1.0) * n_lg / (n_lg + c)


def blend(prior, cur, n, k):
    """(k*prior + n*cur)/(k+n). n = 0 -> prior. k = 0 and n >= 1 -> cur only."""
    if n == 0:
        return prior
    return (k * prior + n * cur) / (k + n)


def team_points(o_own, d_opp, pace_game):
    """avg(O_own x pace_game, D_opp x pace_game)  (#37's lib.team_points with the shared pace)."""
    return lib.team_points(o_own, pace_game, d_opp)


class Ratings:
    """Cumulative-sum ratings. A query for ET date D counts only team-games with date < D (searchsorted 'left'),
    so games on the same date never count, for the team or the league."""

    def __init__(self, prev, Lo_prev, Lp_prev, cur_tg):
        self.prev, self.Lo_prev, self.Lp_prev = prev, Lo_prev, Lp_prev
        cur_tg = cur_tg.sort_values("date", kind="stable").reset_index(drop=True)
        self.lg_dates = cur_tg.date.values.astype("datetime64[D]")
        cols = ["pts", "poss", "opp_pts", "opp_poss", "pace"]
        z = np.zeros((1, len(cols)))
        self.lg_cum = np.vstack([z, np.cumsum(cur_tg[cols].values, axis=0)])
        self.team = {}
        for t, g in cur_tg.groupby("team"):
            g = g.sort_values("date", kind="stable")
            self.team[t] = (g.date.values.astype("datetime64[D]"),
                            np.vstack([z, np.cumsum(g[cols].values, axis=0)]))

    @staticmethod
    def _cut(dates, D):
        return int(np.searchsorted(dates, np.datetime64(D, "D"), side="left"))   # games with date < D

    def league(self, D):
        """(r_o, r_p, n_lg)"""
        i = self._cut(self.lg_dates, D)
        if i == 0:
            return 1.0, 1.0, 0
        c = self.lg_cum[i]
        Lo_cur, Lp_cur = c[0] / c[1], c[4] / i
        return (shrunk_ratio(Lo_cur, self.Lo_prev, i), shrunk_ratio(Lp_cur, self.Lp_prev, i), i)

    def parts(self, team, D):
        """All components for one team on ET date D: prior (O,D,P), current (O,D,P) or None, n, r_o, r_p, n_lg."""
        r_o, r_p, n_lg = self.league(D)
        Op, Dp, Pp = self.prev[team]
        prior = (Op * r_o, Dp * r_o, Pp * r_p)
        n, cur = 0, None
        if team in self.team:
            dates, cum = self.team[team]
            n = self._cut(dates, D)
            if n:
                c = cum[n]
                cur = (c[0] / c[1], c[2] / c[3], c[4] / n)
        return {"prior": prior, "cur": cur, "n": n, "r_o": r_o, "r_p": r_p, "n_lg": n_lg}

    def team_rating(self, team, D, k, mode="blend"):
        """mode: 'blend' (k), 'prior' (prior only), 'current' (k=0: current only, n=0 -> prior)."""
        p = self.parts(team, D)
        if mode == "prior":
            return p["prior"], p
        if mode == "current":
            k = 0
        pr, cu, n = p["prior"], p["cur"], p["n"]
        r = tuple(blend(pr[i], (cu[i] if cu else None), n, k) for i in range(3))
        return r, p

    def project(self, away, home, D, k, mode="blend", detail=False):
        ra, pa = self.team_rating(away, D, k, mode)
        rb, pb = self.team_rating(home, D, k, mode)
        pace = (ra[2] + rb[2]) / 2.0                       # shared pace
        a = team_points(ra[0], rb[1], pace)
        b = team_points(rb[0], ra[1], pace)
        if detail:
            return {"proj": a + b, "A": a, "B": b, "pace": pace, "rating_away": ra, "rating_home": rb,
                    "parts_away": pa, "parts_home": pb}
        return a + b


def decision_date(tip, T):
    """Amendment 2: D = ET date of min(hoopR tip, T - 1h)."""
    return et_date(min(int(tip), int(T) - 3600))


def et_date(ts):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return np.datetime64(datetime.fromtimestamp(int(ts), ZoneInfo("America/New_York")).date(), "D")


def load_ratings(box_dir, before=None):
    """before: ET date string; if given, only 2025-26 team-games dated strictly before it are kept (the gate stage
    uses '2026-01-23' so unseen-period scores are not held in memory until the gates pass)."""
    prev, lo, lp = prior_from_tg(team_games(pd.read_parquet(Path(box_dir) / "team_box_2025.parquet"), (2,)))
    cur = team_games(pd.read_parquet(Path(box_dir) / "team_box_2026.parquet"), CUR_SEASON_TYPES)
    if before is not None:
        cur = cur[cur.date < pd.Timestamp(before)].reset_index(drop=True)
    return Ratings(prev, lo, lp, cur)


def project_games(R, games, k, mode="blend"):
    """games needs away, home, tip, T; D = decision_date(tip, T). Returns Series of projections."""
    return pd.Series([R.project(r.away, r.home, decision_date(r.tip, r.T), k, mode) for r in games.itertuples()],
                     index=games.index)


def n_before(R, games):
    out = []
    for r in games.itertuples():
        D = decision_date(r.tip, r.T)
        out.append((R.parts(r.away, D)["n"], R.parts(r.home, D)["n"]))
    return out
