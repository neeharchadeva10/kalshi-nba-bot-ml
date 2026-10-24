"""Research log #63: the assumptions audit of frozen model E1 (DESCRIPTIVE, no rule chosen).  Run once:
    .venv/Scripts/python.exe reports/output/nba_assumptions_audit/run_audit.py
Writes results.json, slices.csv, AUDIT.md next to this file.  Stops if the E1 reproduction is off.
r = actual - E1 everywhere.  Bootstrap: game resampling, 2,000 draws, seed 20261023."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import audit_lib as A  # noqa: E402


# ------------------------------------------------------------------ build
def build():
    D = A.load_e1()
    rep = A.reproduction(D)
    if not rep["ok"]:
        raise SystemExit(f"STOP: E1 reproduction is off: {rep}")
    G = A.game_table(D)
    ot, ot_rep = A.rt.ot_flags(G.game_id.unique(), A.SEASONS)
    G["ot"] = G.game_id.map(ot)                                   # True / False / NaN (no source: 2019-20)
    sched = A.add_schedule_vars(A.team_schedule())
    assert set(sched.game_id) == set(G.game_id), "schedule games differ from the feature games"
    sv = sched.set_index(["game_id", "team"])
    for side, col in (("home", "home"), ("away", "away")):
        idx = pd.MultiIndex.from_arrays([G.game_id.values, G[col].values])
        for v in ("trip_game", "tz_shift", "three_in_four", "four_in_six", "bubble"):
            G[f"{v}_{side[0]}"] = sv.reindex(idx)[v].values
    G["neutral_bubble"] = G.bubble_h.fillna(False).astype(bool)
    # team table
    T = D[["game_id", "team", "opp", "season", "date", "home", "pts", "pred", "gp_x", "ortg_x_season", "drtg_y_season", "pace_x_season",
           "lg_ortg", "lg_pace"]].copy()
    T["r"] = T.pts - T.pred
    T["date"] = pd.to_datetime(T.date)
    st = A.standings_to_date(sched)
    T = T.merge(st[["game_id", "team", "wins_before", "remaining", "cutoff_wins", "behind_cutoff", "eliminated"]], on=["game_id", "team"], how="left")
    cont = A.roster_continuity_table()
    T = T.merge(cont[["game_id", "team", "continuity"]], on=["game_id", "team"], how="left")
    return D, rep, G, T, sched, ot_rep


def starters_minutes():
    """After the fact: usual starters = the 5 players with most starts for the team in the season; their summed minutes per team-game."""
    parts = []
    for s in A.SEASONS:
        pb = A.load_player_box(A.S.year_of(s))
        if pb is None:
            continue
        st = pb[pb.starter.astype(bool)]
        top = st.groupby(["team_abbreviation", "athlete_id"]).size().rename("n").reset_index()
        top = top.sort_values(["team_abbreviation", "n", "athlete_id"], ascending=[True, False, True]).groupby("team_abbreviation").head(5)
        pb = pb.merge(top[["team_abbreviation", "athlete_id"]].assign(usual=1), on=["team_abbreviation", "athlete_id"], how="left")
        m = pb[pb.usual == 1].groupby(["game_id", "team_abbreviation"]).minutes.sum().rename("starter_min").reset_index()
        m = m.rename(columns={"team_abbreviation": "team"})
        m["season"] = s
        parts.append(m)
    return pd.concat(parts, ignore_index=True)


# ------------------------------------------------------------------ slices
def tercile_labels(x):
    q1, q2 = np.nanquantile(x, [1 / 3, 2 / 3])
    return q1, q2


def run_slices(G, T, sched):
    sl = A.Slicer(G, T)
    N = len(G)
    mon = G.date.dt.month
    allm = np.ones(N, bool)
    # ---- E1 / baseline
    sl.game("E1", "ALL games (overall bias)", allm, primary=False)
    sl.team("E1", "ALL team-games (overall team-score bias)", np.ones(len(T), bool), primary=False)
    # ---- D3 neutral sites
    sl.game("D3", "2020 bubble games (from 2020-07-30)", G.neutral_bubble.values, note="nominal home team; played in Orlando")
    sl.game("D3", "NBA Cup finals (3 games)", G.game_id.isin(A.CUP_FINALS).values, note="Las Vegas; n=3")
    sl.game("D3", "NBA Cup semis + finals (9 games)", G.game_id.isin(A.CUP_SEMIS | A.CUP_FINALS).values, note="extra: semis also in Las Vegas")
    sl.game("D3", "control: all other games", (~G.neutral_bubble.values) & ~G.game_id.isin(A.CUP_SEMIS | A.CUP_FINALS).values, primary=False)
    # ---- M2 stability within season
    for m_, nm in ((10, "Oct"), (11, "Nov"), (12, "Dec"), (1, "Jan"), (2, "Feb"), (3, "Mar"), (4, "Apr")):
        sl.game("M2", f"month = {nm}", (mon == m_).values)
    sl.game("M2", "month = May-Sep (2019-20 bubble / odd calendar)", mon.isin([5, 6, 7, 8, 9]).values)
    gpm = (G.gp_h + G.gp_a) / 2
    for lo, hi in ((0, 10), (10, 20), (20, 40), (40, 60), (60, 999)):
        sl.game("M2", f"avg games played by the two teams in [{lo},{hi})", ((gpm >= lo) & (gpm < hi)).values)
    dl = G.season.map(A.DEADLINE).pipe(pd.to_datetime)
    sl.game("M2", "after the trade deadline (dates from memory, unverified)", (G.date > dl).values)
    last = G.groupby("season").date.transform("max")
    sl.game("M2", "last 14 days of the season", (G.date >= last - pd.Timedelta(days=13)).values)
    # ---- M3 early-season carry-over (team scores)
    early = (T.gp_x < 10).values
    sl.team("M3", "team's first 10 games (all seasons)", early)
    sl.team("M3", "control: team games 11+", ~early, primary=False)
    sl.game("M3", "either team in its first 10 games", ((G.gp_h < 10) | (G.gp_a < 10)).values)
    sl.game("M3", "both teams in their first 10 games", ((G.gp_h < 10) & (G.gp_a < 10)).values)
    Te = T[early & T.continuity.notna()].copy()
    Te["pct"] = Te.groupby("gp_x").continuity.rank(pct=True)          # percentile within the same game number (removes the 'few players seen yet' effect)
    cut = {}
    for lab, lo, hi in (("low third", 0, 1 / 3), ("middle third", 1 / 3, 2 / 3), ("top third", 2 / 3, 1.0001)):
        ids = Te.index[(Te.pct > lo) & (Te.pct <= hi)] if lab != "low third" else Te.index[Te.pct <= hi]
        mask = np.zeros(len(T), bool)
        mask[T.index.get_indexer(ids)] = True
        sl.team("M3", f"first 10 games, roster continuity {lab} (seasons 2021-22+)", mask)
        cut[lab] = float(Te.loc[ids, "continuity"].mean()) if len(ids) else None
    # ---- M4 home court by home team
    for t in sorted(set(G.home)):
        sl.game("M4", f"home team = {t}", (G.home == t).values)
    sl.game("M4", "altitude home (DEN, UTAH)", G.home.isin(["DEN", "UTAH"]).values)
    # ---- M5 rest
    longr_h, longr_a = (G.rest_h >= 3) & (G.gp_h > 0), (G.rest_a >= 3) & (G.gp_a > 0)
    sl.game("M5", "back-to-back: home team only", ((G.b2b_h == 1) & (G.b2b_a == 0)).values)
    sl.game("M5", "back-to-back: away team only", ((G.b2b_a == 1) & (G.b2b_h == 0)).values)
    sl.game("M5", "back-to-back: both teams", ((G.b2b_h == 1) & (G.b2b_a == 1)).values)
    sl.game("M5", "3-in-4 (either team)", (G.three_in_four_h.fillna(False).astype(bool) | G.three_in_four_a.fillna(False).astype(bool)).values)
    sl.game("M5", "4-in-6 (either team)", (G.four_in_six_h.fillna(False).astype(bool) | G.four_in_six_a.fillna(False).astype(bool)).values)
    sl.game("M5", "long rest 3+ days (either team, not season opener)", (longr_h | longr_a).values)
    sl.game("M5", "long rest 3+ days (both teams)", (longr_h & longr_a).values)
    sl.game("M5", "control: no back-to-back, no 3-in-4 (neither team)",
            ((G.b2b_h == 0) & (G.b2b_a == 0) & ~G.three_in_four_h.fillna(False).astype(bool) & ~G.three_in_four_a.fillna(False).astype(bool)).values,
            primary=False)
    # ---- M6 travel
    sa, sh_ = G.tz_shift_a, G.tz_shift_h
    sl.game("M6", "away team moved east >= 2 zones since its last game", (sa >= 2).values)
    sl.game("M6", "away team moved west >= 2 zones since its last game", (sa <= -2).values)
    sl.game("M6", "home team moved east >= 2 zones since its last game", (sh_ >= 2).values)
    sl.game("M6", "home team moved west >= 2 zones since its last game", (sh_ <= -2).values)
    for k in (1, 2, 3, 4):
        sl.game("M6", f"away team road-trip game #{k}", (G.trip_game_a == k).values)
    sl.game("M6", "away team road-trip game #5+", (G.trip_game_a >= 5).values)
    # ---- M7 matchup
    rel_h, rel_a = G.pace_h - G.lg_pace, G.pace_a - G.lg_pace
    q1, q2 = tercile_labels(np.concatenate([rel_h.values, rel_a.values]))
    fast_h, fast_a, slow_h, slow_a = rel_h >= q2, rel_a >= q2, rel_h <= q1, rel_a <= q1
    sl.game("M7", "pace pairing: fast vs fast (top third each)", (fast_h & fast_a).values)
    sl.game("M7", "pace pairing: slow vs slow (bottom third each)", (slow_h & slow_a).values)
    sl.game("M7", "pace pairing: fast vs slow", ((fast_h & slow_a) | (slow_h & fast_a)).values)
    off = (T.ortg_x_season - T.lg_ortg).values
    dfn = (T.drtg_y_season - T.lg_ortg).values                     # opponent's defence: HIGH = leaky
    o1, o2 = tercile_labels(off)
    d1, d2 = tercile_labels(dfn)
    for on, om in (("strong offence (top third)", off >= o2), ("weak offence (bottom third)", off <= o1)):
        for dn, dm in (("strong defence (best third)", dfn <= d1), ("weak defence (worst third)", dfn >= d2)):
            sl.team("M7", f"team score: {on} vs {dn}", om & dm)
    # ---- M8 blowouts
    pm = G.pred_margin.abs()
    for lo, hi in ((0, 3), (3, 6), (6, 9), (9, 12), (12, 99)):
        sl.game("M8", f"predicted |margin| in [{lo},{hi})", ((pm >= lo) & (pm < hi)).values)
    # ---- M9 overtime
    known = G.ot.notna()
    isot = G.ot.fillna(False).astype(bool)
    sl.game("M9", "overtime games (after the fact; 2020-21+)", (known & isot).values)
    sl.game("M9", "regulation games (2020-21+)", (known & ~isot).values)
    sl.game("M9", "OT unknown (2019-20: no pbp / player box)", (~known).values, primary=False)
    # ---- M10 era
    for s in A.SEASONS:
        sl.game("M10", f"season {s}", (G.season == s).values)
    # ---- M11 team
    for t in sorted(set(G.home) | set(G.away)):
        sl.game("M11", f"games involving {t} (total)", ((G.home == t) | (G.away == t)).values)
        sl.team("M11", f"{t} own score", (T.team == t).values)
        sl.team("M11", f"{t} points allowed (opponent score vs {t})", (T.opp == t).values)
    # ---- M12 motivation
    ok = (T.season != "2019-20").values
    last10 = ok & (T.remaining <= 10).values
    far = last10 & (T.behind_cutoff >= 4).values
    elim = last10 & T.eliminated.values
    cont_ = last10 & (T.behind_cutoff < 4).values
    sl.team("M12", "last 10 games, 4+ wins behind the play-in line ('far out')", far)
    sl.team("M12", "last 10 games, mathematically eliminated from the play-in line", elim)
    sl.team("M12", "last 10 games, contenders (< 4 wins behind the line)", cont_)
    sl.team("M12", "control: far-out teams earlier (11+ games left, 40+ played)",
            (ok & (T.remaining > 10).values & (T.behind_cutoff >= 4).values & (T.gp_x >= 40).values), primary=False)
    flag = T.assign(far=far)
    fg = flag.groupby("game_id").far.agg(["sum"]).reindex(G.game_id.values)["sum"].values
    lg = (T.assign(l10=last10).groupby("game_id").l10.sum().reindex(G.game_id.values).values)
    sl.game("M12", "total: >=1 far-out team in its last 10", fg >= 1)
    sl.game("M12", "total: both teams far-out in last 10", fg == 2)
    sl.game("M12", "total: last 10 for both teams, no far-out team", (lg == 2) & (fg == 0))
    return sl, cut


def run_m14(G, T, sl):
    sm = starters_minutes()
    T2 = T.merge(sm[["game_id", "team", "starter_min"]], on=["game_id", "team"], how="left")
    lastd = T2.groupby("season").date.transform("max")
    l2w = (T2.date >= lastd - pd.Timedelta(days=13)).values & T2.starter_min.notna().values
    base = (T2.date < lastd - pd.Timedelta(days=13)).values & T2.starter_min.notna().values
    q1, q2 = np.quantile(T2.starter_min[l2w], [1 / 3, 2 / 3])
    low, mid, high = l2w & (T2.starter_min <= q1).values, l2w & (T2.starter_min > q1).values & (T2.starter_min <= q2).values, l2w & (T2.starter_min > q2).values
    sl.team("M14", "last 14 days: usual starters' minutes in the lowest third (after the fact)", low)
    sl.team("M14", "last 14 days: usual starters' minutes in the middle third", mid)
    sl.team("M14", "last 14 days: usual starters' minutes in the top third", high)
    sl.team("M14", "last 14 days: all team-games with starter data", l2w)
    sl.team("M14", "control: earlier in season, same seasons", base, primary=False)
    lowset = T2[low].set_index(["game_id", "team"]).index
    gl = pd.MultiIndex.from_arrays([G.game_id.values, G.home.values])
    ga = pd.MultiIndex.from_arrays([G.game_id.values, G.away.values])
    both = gl.isin(lowset) & ga.isin(lowset)
    either = gl.isin(lowset) | ga.isin(lowset)
    sl.game("M14", "total: both teams in their lowest-third starter minutes (last 14 days)", both)
    sl.game("M14", "total: at least one team low starter minutes (last 14 days)", either)
    d = {"cutoffs_starter_minutes_thirds": [float(q1), float(q2)],
         "mean_starter_min_last14": float(T2.starter_min[l2w].mean()), "mean_starter_min_earlier": float(T2.starter_min[base].mean()),
         "n_team_games_last14": int(l2w.sum()), "seasons_covered": sorted(T2.season[T2.starter_min.notna()].unique().tolist()),
         "corr_starter_min_vs_team_resid_last14": float(np.corrcoef(T2.starter_min[l2w], T2.r[l2w])[0, 1])}
    return d


def m1_calibration(G):
    x, y = G.pred_tot.values, G.act_tot.values
    dec = pd.qcut(x, 10, labels=False)
    rows = []
    for k in range(10):
        m = dec == k
        r = G.r_tot.values[m]
        mean, lo, hi, se = A.boot_ci(r[None, :])
        rows.append({"decile": k + 1, "n": int(m.sum()), "mean_pred": float(x[m].mean()), "mean_actual": float(y[m].mean()),
                     "mean_resid": float(mean[0]), "ci_lo": float(lo[0]), "ci_hi": float(hi[0])})

    def ols(xx, yy):
        b = np.cov(xx, yy, ddof=1)[0, 1] / np.var(xx, ddof=1)
        return b, yy.mean() - b * xx.mean()

    b, a0 = ols(x, y)
    rng = np.random.default_rng(A.BOOT_SEED)
    n = len(x)
    bs = []
    for lo_ in range(0, A.BOOT_N, 250):
        idx = rng.integers(0, n, size=(250, n))
        xs, ys = x[idx], y[idx]
        xm, ym = xs.mean(1, keepdims=True), ys.mean(1, keepdims=True)
        bs.append(((xs - xm) * (ys - ym)).sum(1) / ((xs - xm) ** 2).sum(1))
    bs = np.concatenate(bs)
    mae_raw = float(np.abs(y - x).mean())
    mae_recal = float(np.abs(y - (a0 + b * x)).mean())
    return {"deciles": rows, "slope": float(b), "intercept": float(a0), "slope_ci": [float(np.quantile(bs, .025)), float(np.quantile(bs, .975))],
            "slope_ci_includes_1": bool(np.quantile(bs, .025) <= 1 <= np.quantile(bs, .975)),
            "mae_raw": mae_raw, "mae_after_in_sample_linear_recalibration": mae_recal, "mae_gain_in_sample_UPPER_BOUND": mae_raw - mae_recal,
            "pred_sd": float(x.std()), "actual_sd": float(y.std())}


def e1_checks(G, T):
    r = G.r_tot.values
    n = len(r)
    med = float(np.median(r))
    rng = np.random.default_rng(A.BOOT_SEED)
    meds = np.concatenate([np.median(r[rng.integers(0, n, size=(250, n))], axis=1) for _ in range(A.BOOT_N // 250)])
    mean, lo, hi, se = A.boot_ci(r[None, :])
    rt_ = T.r.values
    z = (r - r.mean()) / r.std()
    return {"n_games": n, "total_mae": float(np.abs(r).mean()), "total_rmse": float(np.sqrt((r ** 2).mean())),
            "rmse_over_mae": float(np.sqrt((r ** 2).mean()) / np.abs(r).mean()),
            "mean_resid": float(mean[0]), "mean_ci": [float(lo[0]), float(hi[0])], "median_resid": med,
            "median_ci": [float(np.quantile(meds, .025)), float(np.quantile(meds, .975))], "mean_minus_median": float(mean[0] - med),
            "skew": float((z ** 3).mean()), "excess_kurtosis": float((z ** 4).mean() - 3),
            "mae_if_shift_by_mean": float(np.abs(r - r.mean()).mean()), "mae_if_shift_by_median": float(np.abs(r - med).mean()),
            "mae_gain_if_shift_by_median_in_sample_UPPER_BOUND": float(np.abs(r).mean() - np.abs(r - med).mean()),
            "rmse_if_shift_by_mean": float(r.std()), "share_abs_resid_gt_20": float((np.abs(r) > 20).mean()),
            "team_score_mean_resid": float(rt_.mean()), "team_score_median_resid": float(np.median(rt_)),
            "team_score_mae": float(np.abs(rt_).mean()), "team_score_rmse": float(np.sqrt((rt_ ** 2).mean()))}


def ot_rates(G):
    k = G.ot.notna()
    pm = G.pred_margin.abs()
    out = []
    for lo, hi in ((0, 3), (3, 6), (6, 9), (9, 12), (12, 99)):
        m = (k & (pm >= lo) & (pm < hi)).values
        v = G.ot[m].astype(float).values
        mean, l_, h_, se = A.boot_ci(v[None, :])
        out.append({"pred_abs_margin_bin": f"[{lo},{hi})", "n": int(m.sum()), "ot_rate": float(mean[0]), "ci_lo": float(l_[0]), "ci_hi": float(h_[0])})
    v = G.ot[k].astype(float).values
    mean, l_, h_, se = A.boot_ci(v[None, :])
    return {"overall": {"n": int(k.sum()), "ot_rate": float(mean[0]), "ci_lo": float(l_[0]), "ci_hi": float(h_[0])}, "by_pred_margin": out}


# ------------------------------------------------------------------ AUDIT.md
ASSUME = {
    "D1": ("Final scores in hoopR are right", "Compared with Kalshi and OddsData in earlier work (not recomputed here)"),
    "D2": ("Possessions = 0.96 x (FGA - OREB + TOV + 0.44 FTA)", "Recomputed team-season ratings with FTA weight 0.40 and 0.475"),
    "D3": ("Every game has a true home team (no neutral sites)", "Residual of bubble games and NBA Cup semis/finals, incl. the home-minus-away margin"),
    "D4": ("All-Star / exhibition games are not in the data", "Counted games per season against the regular-season schedule"),
    "M1": ("E1 is neither too extreme nor too flat", "Deciles of the predicted total and the slope of actual on predicted"),
    "M2": ("Team quality is stable within a season", "Residual by month, by games played, after the deadline, last 14 days"),
    "M3": ("Last season's ratings carry over early in the season", "Team-score residual in a team's first 10 games, by roster continuity"),
    "M4": ("Home court advantage is the same everywhere", "Residual by home team (30) and altitude"),
    "M5": ("Rest effects are simple (b2b flag + rest days)", "Back-to-back, 3-in-4, 4-in-6, long rest"),
    "M6": ("Travel and time zones do not matter", "Zone change since last game, road-trip game number"),
    "M7": ("A matchup is additive (offence and defence average)", "Pace pairings; strong/weak offence vs strong/weak defence"),
    "M8": ("Blowout-prone games are not special", "Residual by predicted size of the margin"),
    "M9": ("Overtime is not modelled", "Residual of OT vs regulation games; OT rate by predicted margin"),
    "M10": ("The scoring era is stable", "Residual by season"),
    "M11": ("The same model fits every team", "Residual by team: games involving, own score, points allowed"),
    "M12": ("Motivation / schedule do not matter", "Last 10 games for out-of-contention teams vs contenders (standings to date). National TV and rivalry data are not available."),
    "M13": ("Injuries", "Done in #62: the total is unaffected, team scores improve (not recomputed here)"),
    "M14": ("Late-season rest and tanking do not matter", "Usual starters' minutes in the last 14 days (after the fact, descriptive)"),
    "E1": ("MAE vs RMSE: E1 is tuned for the median", "Mean vs median of the residual, shift gain, skew"),
}
FIX = {
    "D2": "None needed unless the weight changes: a refit with the same code and a different weight is the only way to know; low priority.",
    "D3": "Flag neutral games; give them no home-court term (pre-register, confirm on new bubble-free games: only Cup games remain).",
    "M1": "Linear recalibration of the total (a + b x E1) or a shrink toward the league mean.",
    "M2": "Add a within-season drift term, month dummies, or a weight on recent games that is tuned on 2008-19.",
    "M3": "Blend last season's rating by roster continuity (needs roster data before game 1).",
    "M4": "Team-specific home-court term with shrinkage (needs more than 7 seasons to be stable).",
    "M5": "Add 3-in-4 / 4-in-6 and long-rest flags to the ridge features.",
    "M6": "Add zone-shift and road-trip-number features.",
    "M7": "Add interaction terms (pace x pace, off x def) to the ridge.",
    "M8": "A margin-dependent shrink of the total toward the league mean.",
    "M9": "Add an expected-OT term driven by the predicted margin (OT rate is near-constant, so a constant shift).",
    "M10": "Season-level intercept re-estimated on the first weeks of each new season, or a drift correction.",
    "M11": "Team random effect with shrinkage.",
    "M12": "Standings / elimination features (pre-game knowable).",
    "M14": "Pre-game 'rest day' flags cannot see starter minutes; only a late-injury / rest-news feed could. Likely not usable.",
    "E1": "Shift the output by the median residual (or train the final stage on absolute error).",
}
NOTE_EXT = {"M13": ("Done in #62", "HOLDS for the total (per #62); team scores improve", "Use injury data for team scores only (#62)."),
            "D1": ("Done in earlier work", "HOLDS (578/578 vs Kalshi; 16 OddsData typos)", "None.")}


def rating(gain):
    """Label from the exact in-sample MAE gain (points of overall MAE) of fixing the slice mean; the proxy is shown beside it."""
    if gain < 0.01:
        return "HOLDS"
    if gain < 0.05:
        return "SMALL ISSUE"
    return "VIOLATED"


def main():
    t0 = time.time()
    D, rep, G, T, sched, ot_rep = build()
    print("reproduction", rep, flush=True)
    sl, cont_means = run_slices(G, T, sched)
    m14 = run_m14(G, T, sl)
    df, k = sl.frame()
    prim = df[df.primary & df.mean_resid.notna()]
    overall = float(G.r_tot.mean())
    overall_t = float(T.r.mean())
    df["points_recoverable_vs_overall_bias"] = np.where(df.target == "total", (df.mean_resid - overall).abs() * df.share,
                                                        (df.mean_resid - overall_t).abs() * df.share)
    ov = np.where(df.target == "total", overall, overall_t)
    df["sig_vs_overall"] = ((df.ci_lo > ov) | (df.ci_hi < ov)) & df.primary & df.mean_resid.notna()
    from math import erf, sqrt
    zz = (df.mean_resid - ov) / df.boot_se
    df["p_vs_overall_normal_approx"] = zz.apply(lambda z: 1 - erf(abs(z) / sqrt(2)) if np.isfinite(z) else np.nan)
    df["survives_bonferroni_vs_overall"] = (df.p_vs_overall_normal_approx < 0.05 / max(len(df[df.primary & df.mean_resid.notna()]), 1)) & df.primary & df.mean_resid.notna()
    prim = df[df.primary & df.mean_resid.notna()]
    d2 = A.d2_sensitivity(A.team_schedule())
    m1 = m1_calibration(G)
    e1 = e1_checks(G, T)
    otr = ot_rates(G)
    games_per_season = G.groupby("season").size().to_dict()
    n_sig = int(prim.sig_95.sum())
    res = {"reproduction": rep, "n_games": int(len(G)), "seasons": list(A.SEASONS), "bootstrap": {"draws": A.BOOT_N, "seed": A.BOOT_SEED, "unit": "game"},
           "residual_convention": "r = actual - E1; E1 bias in #59 is the opposite sign (E1 - actual)",
           "overall_mean_resid_total": overall, "overall_mean_resid_team_score": overall_t, "overall_total_mae": float(G.r_tot.abs().mean()),
           "overall_team_mae": float(T.r.abs().mean()),
           "multiple_looks": {"n_primary_slices": int(len(prim)), "n_significant_95": n_sig, "expected_false_alarms_at_95": 0.05 * len(prim),
                              "n_survive_bonferroni": int(prim.survives_bonferroni.sum()), "bonferroni_alpha": 0.05 / max(len(prim), 1),
                              "n_significant_vs_overall_bias": int(prim.sig_vs_overall.sum()), "n_survive_bonferroni_vs_overall_bias": int(prim.survives_bonferroni_vs_overall.sum())},
           "D2_possession_weight_sensitivity": d2, "D4_games_per_season": {k_: int(v) for k_, v in games_per_season.items()},
           "M1_calibration": m1, "E1_checks": e1, "M9_ot_rates": otr, "ot_flag_report": ot_rep, "M14_descriptive": m14,
           "M3_continuity_means_by_third": cont_means,
           "points_recoverable_definition": "|mean residual| x share of games (or team-games) in the slice: an UPPER-BOUND PROXY for MAE points that fixing that bias could add back; "
                                            "mae_gain_if_slice_bias_fixed is the exact in-sample MAE gain from subtracting the slice mean inside the slice.",
           "slices": df.replace({np.nan: None}).to_dict("records")}
    (HERE / "results.json").write_text(json.dumps(res, indent=1, default=str))
    df.to_csv(HERE / "slices.csv", index=False)
    write_audit(df, res, G)
    print(f"done {time.time() - t0:.0f}s; primary slices {len(prim)}, significant {n_sig}, expected false alarms {0.05 * len(prim):.1f}")


def fmt(r):
    return f"{r.mean_resid:+.2f} [{r.ci_lo:+.2f}, {r.ci_hi:+.2f}] (n={int(r.n)})"


def write_audit(df, res, G):
    prim = df[df.primary & df.mean_resid.notna()]
    rows = []
    d2 = res["D2_possession_weight_sensitivity"]["weights"]
    for id_ in ASSUME:
        what, checked = ASSUME[id_]
        fix = FIX.get(id_, "-")
        if id_ in NOTE_EXT:
            rows.append((id_, what, checked, NOTE_EXT[id_][1], NOTE_EXT[id_][2]))
            continue
        if id_ == "D4":
            rows.append((id_, what, checked, "HOLDS (by construction): games per season " + ", ".join(f"{k}: {v}" for k, v in res["D4_games_per_season"].items()), "None."))
            continue
        if id_ == "D2":
            parts = []
            for w, d in d2.items():
                parts.append(f"w={w}: net-rating corr {d['net']['pearson']:.4f}, pace corr {d['pace']['pearson']:.4f}, ORtg level {d['ortg']['mean_level_change']:+.2f} pts "
                             f"({d['ortg']['mean_level_change_pct']:+.2f}%), pace level {d['pace']['mean_level_change']:+.2f}")
            ok = all(d["net"]["pearson"] > 0.99 and d["pace"]["pearson"] > 0.99 for d in d2.values())
            rows.append((id_, what, checked, ("HOLDS for rankings" if ok else "SMALL ISSUE") + "; levels shift (absorbed only by a refit, not tested): " + "; ".join(parts), fix))
            continue
        sub = prim[prim.id == id_]
        sig = sub[sub.sig_vs_overall]
        if id_ == "M1":
            m1 = res["M1_calibration"]
            txt = f"slope {m1['slope']:.3f} [{m1['slope_ci'][0]:.3f}, {m1['slope_ci'][1]:.3f}], in-sample recalibration gain {m1['mae_gain_in_sample_UPPER_BOUND']:.3f} MAE pts (upper bound)"
            lev = rating(m1["mae_gain_in_sample_UPPER_BOUND"]) if not m1["slope_ci_includes_1"] else "HOLDS"
            if len(sig):
                b = sig.sort_values("mae_gain_if_slice_bias_fixed", ascending=False).iloc[0]
                lev2 = rating(b.mae_gain_if_slice_bias_fixed)
                txt += f"; worst decile {b.slice}: {fmt(b)}, recoverable (vs overall bias) {b.points_recoverable_vs_overall_bias:.3f}"
                order = ["HOLDS", "SMALL ISSUE", "VIOLATED"]
                lev = max(lev, lev2, key=order.index)
            rows.append((id_, what, checked, f"{lev}: {txt}", fix))
            continue
        if id_ == "E1":
            e = res["E1_checks"]
            lev = rating(e["mae_gain_if_shift_by_median_in_sample_UPPER_BOUND"])
            txt = (f"{lev}: mean resid {e['mean_resid']:+.2f} [{e['mean_ci'][0]:+.2f}, {e['mean_ci'][1]:+.2f}], median {e['median_resid']:+.2f} "
                   f"[{e['median_ci'][0]:+.2f}, {e['median_ci'][1]:+.2f}]; mean-median gap {e['mean_minus_median']:+.2f}; RMSE/MAE {e['rmse_over_mae']:.3f}; "
                   f"shifting by the median gains {e['mae_gain_if_shift_by_median_in_sample_UPPER_BOUND']:.3f} MAE pts in-sample (upper bound)")
            rows.append((id_, what, checked, txt, fix))
            continue
        if not len(sub):
            rows.append((id_, what, checked, "no data", fix))
            continue
        if id_ == "D3":
            bb = sub.iloc[0]
            extra = f"; bubble home-minus-away margin residual {bb.mean_margin_resid:+.2f} [{bb.margin_lo:+.2f}, {bb.margin_hi:+.2f}] (n={int(bb.n)})"
        else:
            extra = ""
        if len(sig):
            b = sig.sort_values("mae_gain_if_slice_bias_fixed", ascending=False).iloc[0]
            lev = rating(b.mae_gain_if_slice_bias_fixed)
            rob = "survives Bonferroni" if b.survives_bonferroni_vs_overall else "does NOT survive Bonferroni"
            txt = (f"{lev}: {len(sig)}/{len(sub)} slices differ from the overall bias (CI excludes it); largest: '{b.slice}' {fmt(b)} ({b.target}), "
                   f"recoverable proxy {b.points_recoverable_vs_overall_bias:.3f} pts (raw |mean| x share {b.points_recoverable_proxy:.3f}); exact in-sample MAE gain from fixing the slice mean {b.mae_gain_if_slice_bias_fixed:.3f}; {rob}")
        else:
            b = sub.reindex((sub.mean_resid - (res['overall_mean_resid_total'] if sub.target.iloc[0] == 'total' else res['overall_mean_resid_team_score'])).abs().sort_values(ascending=False).index).iloc[0]
            txt = f"HOLDS: no slice differs from the overall bias ({len(sub)} slices); furthest from it: '{b.slice}' {fmt(b)}"
        rows.append((id_, what, checked, txt + extra, fix))
    m = res["multiple_looks"]
    top = prim[prim.sig_95].sort_values("points_recoverable_proxy", ascending=False).head(5)
    top2 = prim[prim.sig_vs_overall].sort_values("points_recoverable_vs_overall_bias", ascending=False).head(5)
    L = ["# Assumptions audit of E1 (research log #63) - DESCRIPTIVE", "",
         f"Games: {res['n_games']} regular-season games 2019-20..2025-26 (out of sample for every fitted E1 part). Residual r = actual - E1 "
         f"(r > 0: E1 too low). Overall mean residual of the total {res['overall_mean_resid_total']:+.2f}; total MAE {res['overall_total_mae']:.3f}; "
         f"team-score MAE {res['overall_team_mae']:.3f}. Reproduction of #59 passed (pooled 2022-26 MAE {res['reproduction']['test_total_mae']:.5f}, 2019-22 {res['reproduction']['val_total_mae']:.4f}).", "",
         "Labels (audit convention only, not a research rule): the 'size' is the slice with the largest exact in-sample MAE gain among slices whose bootstrap CI excludes E1's overall mean residual. "
         "HOLDS = no such slice, or gain < 0.01 MAE pts; SMALL ISSUE = 0.01-0.05; VIOLATED = >= 0.05. "
         "'Points recoverable' = |mean residual| x share of games in the slice, an upper-bound proxy for the MAE points that fixing that bias could add back "
         "(the exact in-sample MAE gain from subtracting the slice mean is shown next to it and is typically about 10x smaller, because the MAE gain from a shift is second order in the shift; the proxy is a generous upper bound and the labels use the exact gain).", "",
         "| ID | Assumption | What we checked | Result (size in points, 95% CI) | Possible fix |", "|---|---|---|---|---|"]
    for r in rows:
        L.append("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
    L += ["", "## Top 5 by points recoverable (significant slices only; |mean| x share)", "",
          "| # | ID | Slice | Mean resid [95% CI] | n | Share | Recoverable proxy | Exact in-sample MAE gain | Bonferroni |", "|---|---|---|---|---|---|---|---|---|"]
    for i, (_, r) in enumerate(top.iterrows(), 1):
        L.append(f"| {i} | {r.id} | {r.slice} ({r.target}) | {r.mean_resid:+.2f} [{r.ci_lo:+.2f}, {r.ci_hi:+.2f}] | {int(r.n)} | {r.share:.3f} | {r.points_recoverable_proxy:.3f} | {r.mae_gain_if_slice_bias_fixed:.3f} | "
                 f"{'survives' if r.survives_bonferroni else 'no'} |")
    L += ["", "Large slices mostly inherit E1's overall bias, so this is the ranking after subtracting it (|mean - overall mean| x share, slices whose CI excludes the overall mean); "
          "the ASSUMPTION TABLE labels use this version:", "",
          "| # | ID | Slice | Mean resid [95% CI] | n | Recoverable vs overall bias | Exact in-sample MAE gain | Bonferroni |", "|---|---|---|---|---|---|---|---|"]
    for i, (_, r) in enumerate(top2.iterrows(), 1):
        L.append(f"| {i} | {r.id} | {r.slice} ({r.target}) | {r.mean_resid:+.2f} [{r.ci_lo:+.2f}, {r.ci_hi:+.2f}] | {int(r.n)} | {r.points_recoverable_vs_overall_bias:.3f} | {r.mae_gain_if_slice_bias_fixed:.3f} | "
                 f"{'survives' if r.survives_bonferroni_vs_overall else 'no'} |")
    L += ["", "## Multiple-looks warning", "",
          f"This audit looked at {m['n_primary_slices']} slices (about 25 assumptions, but several have many slices: 30 teams x 3, 30 home teams, ...). "
          f"At 95% about {m['expected_false_alarms_at_95']:.1f} slices are expected to look significant by chance; {m['n_significant_95']} had a CI excluding 0 "
          f"(many only because E1's overall mean residual is itself non-zero) and {m['n_significant_vs_overall_bias']} differ from the overall bias. "
          f"With a Bonferroni cut-off (alpha {m['bonferroni_alpha']:.5f}, normal approximation to the bootstrap z) {m['n_survive_bonferroni']} survive (vs 0) and {m['n_survive_bonferroni_vs_overall_bias']} survive (vs the overall bias). "
          "Slices overlap (a game is in a month, a team, a rest class, ...), so they are not independent. Any fix must be pre-registered and confirmed on NEW games "
          "(the forward tracker) before it counts.", "",
          "## Notes", "",
          "- OT flag: play-by-play where available (2024-25, 2025-26), else player-box minutes (2020-21+); 2019-20 has neither, so OT slices exclude it.",
          "- Roster continuity (M3) uses only players who appeared for the team in strictly earlier games of the season (the opening-night roster is unknown), "
          "ranked within the same game number; available from 2021-22 (needs the previous season's player box). The first game has no value.",
          "- M12 excludes 2019-20 (bubble). 'Far out' = 4+ wins behind the conference's 10th-best win total before the game (my definition; PLAN.md gives none). "
          "Trade-deadline dates are from memory and unverified.",
          "- Time zones use a static table by home team; Phoenix = Mountain (-7), no DST, so the clock difference to Pacific is ignored in summer. "
          "Bubble games are treated as Orlando (no shift computed), Cup semis/finals as Las Vegas.",
          "- M14 uses usual starters defined after the fact (top 5 by starts in the season); 2020-21+ only.",
          "- Slice bootstrap CIs are for the mean residual (game resampling, 2,000 draws, seed 20261023); margin CIs for game slices are in slices.csv."]
    (HERE / "AUDIT.md").write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
