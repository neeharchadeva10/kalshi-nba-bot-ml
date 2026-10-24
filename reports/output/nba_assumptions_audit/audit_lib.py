"""Research log #63: assumptions audit of the frozen #59 model E1 (DESCRIPTIVE).  Library: E1 reproduction, extra variables, bootstrap, slices.

Read-only: reuses reports/output/nba_score_model/main (never modifies it), reads data/raw/nba_open.  No network.
Residual convention: r = ACTUAL - E1 (so r > 0 means E1 was too low).  (#59's own 'bias' is the opposite sign: E1 - actual.)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STUDY59 = HERE.parent / "nba_score_model"
MAIN = STUDY59 / "main"
if str(MAIN) not in sys.path:
    sys.path.insert(0, str(MAIN))
import sm_lib as S  # noqa: E402
import models as M  # noqa: E402
import run_select as rs  # noqa: E402
import run_test as rt  # noqa: E402

BOX = S.BOX
SEASONS = ("2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26")
VAL_SEASONS, TEST_SEASONS = SEASONS[:3], SEASONS[3:]
BOOT_N = 2000
BOOT_SEED = 20261023
REPRO_TEST_MAE = 14.79634          # #59 results_test.json pooled 2022-26 E1 total MAE
REPRO_VAL_MAE = 14.836             # #59 results_select.json 2019-22 E1 total MAE
BUBBLE_START = pd.Timestamp("2020-07-30")
CUP_FINALS = {401607495, 401734908, 401809839}                      # 2023-12-09 IND@LAL, 2024-12-17 MIL@OKC, 2025-12-16 SA@NY
CUP_SEMIS = {401607493, 401607494, 401734906, 401734907, 401809837, 401809838}
# Trade deadlines (from memory, NOT verified online; only used for the M2 'post-deadline' slice)
DEADLINE = {"2019-20": "2020-02-06", "2020-21": "2021-03-25", "2021-22": "2022-02-10", "2022-23": "2023-02-09",
            "2023-24": "2024-02-08", "2024-25": "2025-02-06", "2025-26": "2026-02-05"}

# Static home time zone (UTC offset in standard time).  Phoenix is put in Mountain (-7) as instructed; Arizona has no DST, so in
# summer-time months Phoenix is really on Pacific clock time (the table ignores DST for every team).
TZ = {"ATL": -5, "BKN": -5, "BOS": -5, "CHA": -5, "CLE": -5, "DET": -5, "IND": -5, "MIA": -5, "NY": -5, "ORL": -5, "PHI": -5,
      "TOR": -5, "WSH": -5, "NJ": -5,
      "CHI": -6, "DAL": -6, "HOU": -6, "MEM": -6, "MIL": -6, "MIN": -6, "NO": -6, "OKC": -6, "SA": -6,
      "DEN": -7, "UTAH": -7, "PHX": -7,
      "GS": -8, "LAC": -8, "LAL": -8, "POR": -8, "SAC": -8, "SEA": -8}
EAST = {"ATL", "BKN", "BOS", "CHA", "CHI", "CLE", "DET", "IND", "MIA", "MIL", "NY", "ORL", "PHI", "TOR", "WSH", "NJ"}
BUBBLE_TZ = -5          # Orlando
CUP_TZ = -8             # Las Vegas (semis and finals)


# ------------------------------------------------------------------ E1 predictions
def load_e1(seasons=SEASONS):
    """Team-row table with E1 prediction (column pred) and target pts; the exact #59 pipeline (frozen.json)."""
    frozen = json.loads((STUDY59 / "frozen.json").read_text())
    D = rs.load_stage(list(seasons))
    pred = np.full(len(D), np.nan)
    for s in seasons:
        m = (D.season == s).values
        pred[m] = rt.season_predictions(frozen, D[m], s, ["E1"])["E1"]
    D["pred"] = pred
    return D


def total_mae(D):
    m = M.game_metrics(D.game_id.values, D.home.values, D.pts.values, D.pred.values)
    return m["total_mae"], m["n_games"], m["bias"]


def reproduction(D):
    val = D[D.season.isin(VAL_SEASONS)]
    test = D[D.season.isin(TEST_SEASONS)]
    v, nv, bv = total_mae(val)
    t, nt, bt = total_mae(test)
    ok = bool(abs(t - REPRO_TEST_MAE) < 5e-6 and abs(v - REPRO_VAL_MAE) < 5e-4)
    return {"test_total_mae": t, "test_n_games": nt, "test_bias_pred_minus_actual": bt, "test_expected": REPRO_TEST_MAE,
            "val_total_mae": v, "val_n_games": nv, "val_bias_pred_minus_actual": bv, "val_expected": REPRO_VAL_MAE, "ok": ok}


def game_table(D):
    """One row per game: home/away teams, E1 and actual points, residuals r = actual - E1, and the pre-game features of the home row (x = home)."""
    h = D[D.home > 0].set_index("game_id")
    a = D[D.home <= 0].set_index("game_id").reindex(h.index)
    G = pd.DataFrame({"game_id": h.index.values, "season": h.season.values, "date": h.date.values, "home": h.team.values,
                      "away": a.team.values, "pred_h": h.pred.values, "pred_a": a.pred.values,
                      "act_h": h.pts.values, "act_a": a.pts.values,
                      "rest_h": h.rest_x.values, "rest_a": a.rest_x.values, "b2b_h": h.b2b_x.values, "b2b_a": a.b2b_x.values,
                      "gp_h": h.gp_x.values, "gp_a": a.gp_x.values,
                      "pace_h": h.pace_x_season.values, "pace_a": a.pace_x_season.values,
                      "ortg_h": h.ortg_x_season.values, "ortg_a": a.ortg_x_season.values,
                      "drtg_h": h.drtg_y_season.values, "drtg_a": a.drtg_y_season.values,
                      "lg_ortg": h.lg_ortg.values, "lg_pace": h.lg_pace.values})
    G["date"] = pd.to_datetime(G.date)
    G["pred_tot"] = G.pred_h + G.pred_a
    G["act_tot"] = G.act_h + G.act_a
    G["r_tot"] = G.act_tot - G.pred_tot
    G["r_h"] = G.act_h - G.pred_h
    G["r_a"] = G.act_a - G.pred_a
    G["r_margin"] = (G.act_h - G.act_a) - (G.pred_h - G.pred_a)
    G["pred_margin"] = G.pred_h - G.pred_a
    return G.sort_values(["date", "game_id"], kind="stable").reset_index(drop=True)


# ------------------------------------------------------------------ bootstrap
def boot_means(V, n_draws=BOOT_N, seed=BOOT_SEED, chunk=250):
    """V: (k, n) array.  Game bootstrap: the same n_draws index resamples for every row of V.  Returns (k, n_draws) means.  Deterministic."""
    V = np.atleast_2d(np.asarray(V, float))
    k, n = V.shape
    rng = np.random.default_rng(seed)
    out = np.empty((k, n_draws))
    for lo in range(0, n_draws, chunk):
        c = min(chunk, n_draws - lo)
        idx = rng.integers(0, n, size=(c, n))
        for j in range(k):
            out[j, lo:lo + c] = V[j][idx].mean(axis=1)
    return out


def boot_ci(V, **kw):
    """(mean, lo, hi, se) per row of V (95% percentile interval)."""
    V = np.atleast_2d(np.asarray(V, float))
    bm = boot_means(V, **kw)
    return V.mean(axis=1), np.quantile(bm, 0.025, axis=1), np.quantile(bm, 0.975, axis=1), bm.std(axis=1, ddof=1)


# ------------------------------------------------------------------ schedule variables
def team_schedule(seasons=SEASONS):
    """One row per team-game from the cleaned hoopR box (same cleaning as #59): game_id, season, date, team, opp, home(bool), pts, opp_pts, fga..."""
    tg = S.load_all_team_games(list(seasons))
    tg["home"] = tg.venue == "home"
    tg["date"] = pd.to_datetime(tg.date)
    return tg.sort_values(["team", "season", "date", "game_id"], kind="stable").reset_index(drop=True)


def location_tz(sched, bubble_start=BUBBLE_START, cup_ids=CUP_SEMIS | CUP_FINALS):
    """UTC offset of the venue of each team-game row: home team's zone; Orlando (bubble) from bubble_start in 2019-20; Las Vegas for Cup semis / finals."""
    home_code = np.where(sched.home, sched.team, sched.opp)
    tz = pd.Series(home_code).map(TZ).astype(float).values
    bub = ((sched.season == "2019-20") & (sched.date >= bubble_start)).values
    tz = np.where(bub, BUBBLE_TZ, tz)
    tz = np.where(sched.game_id.isin(cup_ids).values, CUP_TZ, tz)
    return tz, bub


def add_schedule_vars(sched, bubble_start=BUBBLE_START):
    """Per team-game (rows sorted team, season, date): road-trip game number (0 at home), tz shift since the previous game of the same season
    (venue zone now minus venue zone then; positive = moved east; NaN for a team's first game or bubble games), 3-in-4 and 4-in-6 flags.
    Pre-game knowable (the schedule)."""
    s = sched.sort_values(["team", "season", "date", "game_id"], kind="stable").reset_index(drop=True).copy()
    tz, bub = location_tz(s, bubble_start)
    s["loc_tz"], s["bubble"] = tz, bub
    grp = s.groupby(["team", "season"], sort=False)
    # road-trip game number: consecutive away games counted from 1; home game = 0
    away = (~s.home).astype(int).values
    key = (s.team + "|" + s.season).values
    trip = np.zeros(len(s), int)
    run = 0
    for i in range(len(s)):
        if i == 0 or key[i] != key[i - 1]:
            run = 0
        run = run + 1 if away[i] else 0
        trip[i] = run
    s["trip_game"] = trip
    prev_tz = grp.loc_tz.shift(1)
    s["tz_shift"] = (s.loc_tz - prev_tz).where(~s.bubble & ~grp.bubble.shift(1).fillna(False).astype(bool))
    d = s.date
    s["d_prev2"] = (d - grp.date.shift(2)).dt.days
    s["d_prev3"] = (d - grp.date.shift(3)).dt.days
    s["three_in_four"] = (s.d_prev2 <= 3).fillna(False)          # this + two previous games within 4 calendar days
    s["four_in_six"] = (s.d_prev3 <= 5).fillna(False)            # this + three previous within 6 calendar days
    return s


# ------------------------------------------------------------------ roster continuity (M3)
def continuity(prev_minutes: dict, roster: set) -> float:
    """Share of last season's team minutes played by players still on the team: sum(minutes of prev-season players in roster) / sum(all)."""
    tot = float(sum(prev_minutes.values()))
    if tot <= 0:
        return float("nan")
    return float(sum(m for p, m in prev_minutes.items() if p in roster)) / tot


def continuity_by_game(pb_prev, pb_cur):
    """pb_*: regular-season player-box rows of ONE team (athlete_id, game_id, date, minutes).  For each game of the current season:
    roster = players who appeared (minutes > 0) for the team in games with an earlier date (strictly before; same-day/this game excluded);
    returns Series game_id -> continuity (NaN for the team's first game: no earlier appearances)."""
    prev = pb_prev[pb_prev.minutes > 0].groupby("athlete_id").minutes.sum().to_dict()
    cur = pb_cur[pb_cur.minutes > 0].sort_values(["date", "game_id"], kind="stable")
    out = {}
    roster: set = set()
    for dt, grp in cur.groupby("date", sort=True):
        for gid in grp.game_id.unique():
            out[gid] = continuity(prev, roster) if roster else float("nan")
        roster |= set(grp.athlete_id)
    return pd.Series(out, dtype=float)


def load_player_box(year):
    p = BOX / f"player_box_{year}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p, columns=["game_id", "season_type", "game_date", "athlete_id", "team_abbreviation", "minutes", "starter",
                                     "did_not_play"])
    df = df[df.season_type == 2].copy()
    df["minutes"] = pd.to_numeric(df.minutes, errors="coerce").fillna(0.0)
    df["date"] = pd.to_datetime(df.game_date)
    df["game_id"] = df.game_id.astype("int64")
    return df


def roster_continuity_table(seasons=SEASONS):
    """Rows (game_id, team, continuity) for every season whose previous season's player box exists (2021-22 .. 2025-26)."""
    parts = []
    for s in seasons:
        y = S.year_of(s)
        cur, prev = load_player_box(y), load_player_box(y - 1)
        if cur is None or prev is None:
            continue
        for team, g in cur.groupby("team_abbreviation"):
            p = prev[prev.team_abbreviation == team]
            ser = continuity_by_game(p, g)
            parts.append(pd.DataFrame({"game_id": ser.index.values, "team": team, "continuity": ser.values, "season": s}))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["game_id", "team", "continuity", "season"])


# ------------------------------------------------------------------ standings (M12)
CONF = {"ATL": "E", "BKN": "E", "BOS": "E", "CHA": "E", "CHI": "E", "CLE": "E", "DET": "E", "IND": "E", "MIA": "E", "MIL": "E",
        "NY": "E", "ORL": "E", "PHI": "E", "TOR": "E", "WSH": "E",
        "DAL": "W", "DEN": "W", "GS": "W", "HOU": "W", "LAC": "W", "LAL": "W", "MEM": "W", "MIN": "W", "NO": "W", "OKC": "W",
        "PHX": "W", "POR": "W", "SAC": "W", "SA": "W", "UTAH": "W"}


def standings_to_date(sched):
    """For each team-game row: wins before this game's date (own earlier games only), games remaining incl. this game, the conference's 10th-best
    wins total before this date (play-in line), and wins behind that line.  Uses only results with an earlier date."""
    rows = []
    for season, ss in sched.groupby("season"):
        ss = ss.copy()
        ss["win"] = (ss.pts > ss.opp_pts).astype(int)
        dates = sorted(ss.date.unique())
        pivot = ss.pivot_table(index="date", columns="team", values="win", aggfunc="sum", fill_value=0).reindex(dates, fill_value=0)
        cum_before = pivot.cumsum().shift(1).fillna(0)            # wins strictly before each date
        total_games = ss.groupby("team").size()
        for team, g in ss.groupby("team"):
            g = g.sort_values(["date", "game_id"], kind="stable")
            gpb = np.arange(len(g))
            w = cum_before.loc[g.date.values, team].values
            conf = CONF.get(team)
            cutoff = np.full(len(g), np.nan)
            if conf is not None:
                cols = [t for t in cum_before.columns if CONF.get(t) == conf]
                sub = cum_before.loc[g.date.values, cols].values
                srt = -np.sort(-sub, axis=1)
                cutoff = srt[:, 9] if srt.shape[1] >= 10 else np.full(len(g), np.nan)
            rows.append(pd.DataFrame({"game_id": g.game_id.values, "team": team, "season": season, "wins_before": w, "gp_before": gpb,
                                      "remaining": total_games[team] - gpb, "cutoff_wins": cutoff}))
    st = pd.concat(rows, ignore_index=True)
    st["behind_cutoff"] = st.cutoff_wins - st.wins_before
    st["eliminated"] = (st.wins_before + st.remaining) < st.cutoff_wins
    return st


# ------------------------------------------------------------------ D2: possession formula sensitivity
def poss_w(fga, oreb, tov, fta, w):
    return 0.96 * (fga - oreb + tov + w * fta)


def d2_sensitivity(tg, weights=(0.40, 0.475), base=0.44):
    """Team-season ratings (ortg, drtg, pace; ortg = 100 sum pts / sum game poss) recomputed with FTA weight w; correlation and mean level change vs
    the 0.44 base.  No model refit."""
    tg = tg.copy()

    def ratings(w):
        t = tg.copy()
        t["poss_w"] = poss_w(t.fga, t.oreb, t.tov, t.fta, w)
        opp = t[["game_id", "team", "poss_w"]].rename(columns={"team": "opp", "poss_w": "opp_poss_w"})
        t = t.merge(opp, on=["game_id", "opp"], how="inner")
        t["gposs"] = (t.poss_w + t.opp_poss_w) / 2.0
        g = t.groupby(["season", "team"])
        r = pd.DataFrame({"ortg": 100 * g.pts.sum() / g.gposs.sum(), "drtg": 100 * g.opp_pts.sum() / g.gposs.sum(), "pace": g.gposs.mean()})
        r["net"] = r.ortg - r.drtg
        return r

    b = ratings(base)
    out = {"base_weight": base, "n_team_seasons": int(len(b)), "base_means": b.mean().to_dict(), "weights": {}}
    for w in weights:
        r = ratings(w)
        rec = {}
        for c in ("ortg", "drtg", "pace", "net"):
            rec[c] = {"pearson": float(np.corrcoef(b[c], r[c])[0, 1]), "spearman": float(np.corrcoef(b[c].rank(), r[c].rank())[0, 1]),
                      "mean_level_change": float((r[c] - b[c]).mean()), "mean_level_change_pct": (float(100 * (r[c] - b[c]).mean() / b[c].mean()) if c != "net" else None),
                      "max_abs_team_change": float((r[c] - b[c]).abs().max())}
        out["weights"][str(w)] = rec
    return out


# ------------------------------------------------------------------ slices
class Slicer:
    """Collects one row per slice: n, share, mean residual (+ game-bootstrap 95% CI), MAE in slice vs overall, points-recoverable proxy."""

    def __init__(self, G, T):
        self.G, self.T, self.rows = G, T, []
        self.mae_g = float(np.abs(G.r_tot).mean())
        self.mae_t = float(np.abs(T.r).mean())

    def game(self, id_, name, mask, primary=True, note=""):
        G = self.G
        mask = np.asarray(mask, bool)
        n = int(mask.sum())
        row = {"id": id_, "slice": name, "target": "total", "primary": primary, "n": n, "share": n / len(G), "note": note}
        if n < 2:
            self.rows.append(row)
            return row
        V = np.vstack([G.r_tot.values[mask], G.r_margin.values[mask], G.r_h.values[mask], G.r_a.values[mask]])
        mean, lo, hi, se = boot_ci(V)
        r = G.r_tot.values[mask]
        row.update({"mean_resid": mean[0], "ci_lo": lo[0], "ci_hi": hi[0], "boot_se": se[0],
                    "mean_margin_resid": mean[1], "margin_lo": lo[1], "margin_hi": hi[1],
                    "mean_home_score_resid": mean[2], "mean_away_score_resid": mean[3],
                    "mae_slice": float(np.abs(r).mean()), "mae_overall": self.mae_g,
                    "mae_gain_if_slice_bias_fixed": float((np.abs(r).mean() - np.abs(r - r.mean()).mean()) * n / len(G))})
        self._finish(row)
        return row

    def team(self, id_, name, mask, primary=True, note=""):
        T = self.T
        mask = np.asarray(mask, bool)
        n = int(mask.sum())
        row = {"id": id_, "slice": name, "target": "team_score", "primary": primary, "n": n, "share": n / len(T), "note": note}
        if n < 2:
            self.rows.append(row)
            return row
        r = T.r.values[mask]
        mean, lo, hi, se = boot_ci(r[None, :])
        row.update({"mean_resid": mean[0], "ci_lo": lo[0], "ci_hi": hi[0], "boot_se": se[0], "mae_slice": float(np.abs(r).mean()),
                    "mae_overall": self.mae_t, "mae_gain_if_slice_bias_fixed": float((np.abs(r).mean() - np.abs(r - r.mean()).mean()) * n / len(T))})
        self._finish(row)
        return row

    def _finish(self, row):
        row["sig_95"] = bool(row["ci_lo"] > 0 or row["ci_hi"] < 0)
        # 'points recoverable' = |mean residual| x share of games in the slice: an UPPER-BOUND PROXY for how many points of MAE could be added back
        row["points_recoverable_proxy"] = abs(row["mean_resid"]) * row["share"]
        row["z"] = row["mean_resid"] / row["boot_se"] if row["boot_se"] > 0 else float("nan")
        self.rows.append(row)

    def frame(self):
        df = pd.DataFrame(self.rows)
        prim = df.primary & df.mean_resid.notna()
        k = int(prim.sum())
        from math import erf, sqrt
        # two-sided p from the bootstrap z (normal approximation), Bonferroni across all primary slices
        p = df.z.apply(lambda z: 1 - erf(abs(z) / sqrt(2)) if np.isfinite(z) else np.nan)
        df["p_normal_approx"] = p
        df["survives_bonferroni"] = (p < 0.05 / max(k, 1)) & prim
        return df, k
