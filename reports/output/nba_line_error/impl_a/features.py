"""Research log #57, implementation A: features f1..f10 and y for every clean game (strictly before the game's ET date).

Output: reports/output/nba_line_error/features_a.parquet with
  game_id, season, date, line, actual, y, f1..f10, and the components behind them (pace10_*, lgpace, tpar10_*, lgtpar, form5_*,
  form10_*, lg_total, f1_source, b2b_*, proj8) and the alternative readings f5_alt1 / f5_nofactor / f6_alt_sum (documented in README_A.md).
y = actual - line.  Every window uses only rows with date < the game's ET date (np.searchsorted side='left' on sorted dates);
windows never cross seasons except f1's documented fallback (last 30 days of last season).
Run:  .venv/Scripts/python.exe reports/output/nba_line_error/impl_a/features.py"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lea_lib as L  # noqa: E402

W_PACE, W_FORM_SHORT, W_FORM_LONG, W_3PA, MIN_LG_GAMES = 10, 5, 10, 10, 50


def _d(x):
    return np.asarray(x).astype("datetime64[D]")


def last_n_mean(dates, vals, D, n):
    """Mean of the last n entries with date < D (dates sorted ascending); NaN if fewer than n exist."""
    i = int(np.searchsorted(dates, np.datetime64(D, "D"), side="left"))
    if i < n:
        return float("nan")
    return float(np.mean(vals[i - n:i]))


def prev_game_gap(dates, D):
    """Days between D and the team's latest earlier game (date < D); None if there is none."""
    i = int(np.searchsorted(dates, np.datetime64(D, "D"), side="left"))
    if i == 0:
        return None
    return int((np.datetime64(D, "D") - dates[i - 1]).astype("int64"))


class SeasonData:
    """Everything one season needs, indexed so that every query is `rows with date < D`."""

    def __init__(self, tg_season: pd.DataFrame, tg_prev: pd.DataFrame | None, clean_s: pd.DataFrame):
        t = tg_season.sort_values(["date", "tip", "game_id"], kind="stable")
        self.first_date = _d(t.date.min()).min()
        g = L.games_from_team_games(t)
        self.g_dates = _d(g.date.values)
        self.g_cum_total = np.concatenate([[0.0], np.cumsum(g.actual.values)])
        home = t[t.venue == "home"].sort_values(["date", "tip", "game_id"], kind="stable")
        self.p_dates = _d(home.date.values)
        self.p_cum_pace = np.concatenate([[0.0], np.cumsum(home.pace.values)])
        t2 = t.assign(tpar=t.tpa / t.fga)
        self.t_dates = _d(t2.date.values)
        self.t_cum_tpar = np.concatenate([[0.0], np.cumsum(t2.tpar.values)])
        self.team = {}
        for team, tt in t2.groupby("team"):
            tt = tt.sort_values(["date", "tip"], kind="stable")
            self.team[team] = {"dates": _d(tt.date.values), "pace": tt.pace.values.astype(float), "tpar": tt.tpar.values.astype(float)}
        # (actual - line) series of each team over its clean-line games
        cl = clean_s.assign(resid=clean_s.actual - clean_s.line)
        long = pd.concat([cl[["date", "tip", "home", "resid"]].rename(columns={"home": "team"}),
                          cl[["date", "tip", "away", "resid"]].rename(columns={"away": "team"})], ignore_index=True)
        self.resid = {}
        for team, tt in long.groupby("team"):
            tt = tt.sort_values(["date", "tip"], kind="stable")
            self.resid[team] = (_d(tt.date.values), tt.resid.values.astype(float))
        # f1 fallback: mean total of the last 30 days of the previous regular season
        self.fallback_total = float("nan")
        if tg_prev is not None and len(tg_prev):
            gp = L.games_from_team_games(tg_prev)
            last = gp.date.max()
            w = gp[gp.date > last - pd.Timedelta(days=30)]
            self.fallback_total = float(w.actual.mean())

    def lg_total(self, D):
        i = int(np.searchsorted(self.g_dates, np.datetime64(D, "D"), side="left"))
        if i >= MIN_LG_GAMES:
            return float(self.g_cum_total[i] / i), "season"
        return self.fallback_total, "prev_last30d"

    def lg_pace(self, D):
        i = int(np.searchsorted(self.p_dates, np.datetime64(D, "D"), side="left"))
        return float(self.p_cum_pace[i] / i) if i else float("nan")

    def lg_tpar(self, D):
        i = int(np.searchsorted(self.t_dates, np.datetime64(D, "D"), side="left"))
        return float(self.t_cum_tpar[i] / i) if i else float("nan")


def season_features(sd: SeasonData, clean_s: pd.DataFrame, proj8: pd.Series | None) -> pd.DataFrame:
    """clean_s: the season's clean games (game_id, date, home, away, line, spread, actual, ...).  proj8: game_id -> frozen formula
    projection (NaN/absent -> f8 NaN)."""
    rows = []
    for r in clean_s.itertuples(index=False):
        D = np.datetime64(r.date, "D")
        th, ta = sd.team.get(r.home), sd.team.get(r.away)
        lg_tot, src = sd.lg_total(D)
        lgp, lgt = sd.lg_pace(D), sd.lg_tpar(D)
        ph = last_n_mean(th["dates"], th["pace"], D, W_PACE)
        pa = last_n_mean(ta["dates"], ta["pace"], D, W_PACE)
        tph = last_n_mean(th["dates"], th["tpar"], D, W_3PA)
        tpa_ = last_n_mean(ta["dates"], ta["tpar"], D, W_3PA)
        rh, ra = sd.resid.get(r.home), sd.resid.get(r.away)
        f5h = last_n_mean(rh[0], rh[1], D, W_FORM_SHORT) if rh else float("nan")
        f5a = last_n_mean(ra[0], ra[1], D, W_FORM_SHORT) if ra else float("nan")
        f10h = last_n_mean(rh[0], rh[1], D, W_FORM_LONG) if rh else float("nan")
        f10a = last_n_mean(ra[0], ra[1], D, W_FORM_LONG) if ra else float("nan")
        gh, ga = prev_game_gap(th["dates"], D), prev_game_gap(ta["dates"], D)
        b2b_h, b2b_a = int(gh == 1), int(ga == 1)
        pace_sum = ph + pa
        p8 = float(proj8.get(r.game_id, np.nan)) if proj8 is not None else float("nan")
        rows.append({
            "game_id": int(r.game_id), "season": r.season, "date": r.date, "line": float(r.line), "line_source": r.line_source, "actual": float(r.actual),
            "y": float(r.actual) - float(r.line),
            "f1": float(r.line) - lg_tot,
            "f2": abs(float(r.spread)) if np.isfinite(r.spread) else float("nan"),
            "f3": float((D - sd.first_date).astype("int64")),
            "f4": float(b2b_h + b2b_a),
            "f5": pace_sum - 2.0 * lgp,
            "f6": (f5h + f5a) / 2.0,
            "f7": float(r.home in L.ALTITUDE_HOOPR),
            "f8": p8 - float(r.line),
            "f9": (tph + tpa_) - 2.0 * lgt,
            "f10": f10h - f10a,
            "pace10_home": ph, "pace10_away": pa, "lg_pace": lgp, "tpar10_home": tph, "tpar10_away": tpa_, "lg_tpar": lgt,
            "form5_home": f5h, "form5_away": f5a, "form10_home": f10h, "form10_away": f10a, "lg_total": lg_tot,
            "f1_source": src, "b2b_home": b2b_h, "b2b_away": b2b_a, "proj8": p8,
            "f5_alt1": pace_sum - lgp, "f9_alt1": (tph + tpa_) - lgt, "f5_nofactor": (pace_sum - 2.0 * lgp) / 0.96, "f6_alt_sum": f5h + f5a})
    return pd.DataFrame(rows)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", default="games_clean_a.parquet")
    ap.add_argument("--out", default="features_a.parquet")
    a = ap.parse_args(argv)
    import fa_ext as fx
    tg = L.load_team_games()
    clean = pd.read_parquet(L.STUDY / a.clean)
    out = []
    for s in L.ALL_SEASONS:
        cs = clean[clean.season == s].sort_values(["date", "tip", "game_id"], kind="stable").reset_index(drop=True)
        prev = L.label_of(L.year_of(s) - 1)
        sd = SeasonData(tg[tg.season == s], tg[tg.season == prev], cs)
        p8, note = fx.project_season(s, game_ids=cs.game_id.values)
        f = season_features(sd, cs, p8.set_index("game_id").proj)
        out.append(f)
        print(s, len(f), "f8 consts:", note[:70], "| f8 NaN:", int(f.f8.isna().sum()), flush=True)
    F = pd.concat(out, ignore_index=True)
    F["date"] = pd.to_datetime(F.date)
    F.to_parquet(L.STUDY / a.out, index=False)
    print(a.out, F.shape)
    print(F.groupby("season")[L.FEATS].apply(lambda d: d.isna().mean()).round(3).to_string())


if __name__ == "__main__":
    main()
