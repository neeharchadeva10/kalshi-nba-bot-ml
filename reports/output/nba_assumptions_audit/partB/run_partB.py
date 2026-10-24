"""Part B runner (J1-J11).  DESCRIPTIVE; writes partB/results_partB.json.  Run:
  .venv/Scripts/python.exe reports/output/nba_assumptions_audit/partB/run_partB.py [J1 J2 ...]
No network; no Kalshi order/portfolio endpoint; reads local ledger files only."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pb_common as C  # noqa: E402
import pb_lib as L  # noqa: E402

E2 = C.E2
RES_FILE = HERE / "results_partB.json"


def jdump(o):
    def conv(x):
        if isinstance(x, (np.floating,)):
            return None if not np.isfinite(x) else float(x)
        if isinstance(x, (np.integer,)):
            return int(x)
        if isinstance(x, (np.bool_,)):
            return bool(x)
        if isinstance(x, float) and not np.isfinite(x):
            return None
        raise TypeError(type(x))
    return json.dumps(o, indent=1, default=conv)


def load_results():
    return json.loads(RES_FILE.read_text()) if RES_FILE.exists() else {}


def save(key, val):
    r = load_results()
    r[key] = val
    RES_FILE.write_text(jdump(r))


_CTX = {}


def ctx():
    """Shared heavy objects, built once."""
    if not _CTX:
        t = time.time()
        _CTX["D"] = C.load_e1()                           # 2021-26 team rows with E1 pred
        _CTX["G"] = C.game_err(_CTX["D"])
        _CTX["box"] = E2.load_box(range(2021, 2027))
        _CTX["games"] = E2.load_games(C.SEASONS)
        _CTX["reports"] = E2.Reports()
        _CTX["feat"] = pd.read_parquet(E2.FEATURES_PARQUET)
        print(f"ctx built {time.time() - t:.0f}s", flush=True)
    return _CTX


def team_resid(D):
    """r = actual - E1 team points per (game_id, team)."""
    d = D[["game_id", "team", "season", "home", "pts", "pred"]].copy()
    d["r"] = d.pts - d.pred
    return d


# ------------------------------------------------------------------ reproduction check
def repro():
    X = ctx()
    G = X["G"]
    t = G[G.season.isin(C.SEASONS[1:])]
    mae = float(t.err.abs().mean())
    out = {"e1_total_mae_2022_26": mae, "expected": C.REPRO_TEST_MAE, "n_games": int(len(t)),
           "ok": bool(abs(mae - C.REPRO_TEST_MAE) < 5e-6)}
    assert out["ok"], out
    return out


# ------------------------------------------------------------------ J1
def j1():
    X = ctx()
    games = X["games"][X["games"].season.isin(C.SEASONS[1:])]
    T = L.j1_counts(games, X["box"])
    sys.path.insert(0, str(C.OUT / "nba_formula_accuracy"))
    import fa_lib
    close = fa_lib.load_closing()
    G = X["G"]
    G = G[G.season.isin(C.SEASONS[1:])].set_index("game_id")
    e1 = G.err
    line = pd.Series(G.index.map(close).values.astype(float), index=G.index)
    ok = line.notna()
    cerr = (line - G.tot_act)[ok]
    summ, n_close = L.j1_summary(T, cerr, e1)
    # spread of the gap in each key-out group across the 6 variants
    spread = {}
    for ro in L.ROSTERS:
        sub = {k: v for k, v in summ.items() if k.endswith("_" + ro)}
        spread[ro] = {}
        for g in ("0", "1", "2+"):
            gaps = [v["groups"][g]["gap_E1_minus_close"] for v in sub.values() if g in v["groups"]]
            spread[ro][g] = {"min": float(min(gaps)), "max": float(max(gaps)), "range": float(max(gaps) - min(gaps))}
    ci_halfwidth_2plus = np.mean([(v["groups"]["2+"]["gap_hi"] - v["groups"]["2+"]["gap_lo"]) / 2 for v in summ.values() if "2+" in v["groups"]])
    rng = max(s["range"] for ro in spread.values() for s in ro.values())
    res = {"n_games_with_close_2022_26": n_close, "team_rows_fallback_to_own10_share": float(T.team_fallback_own10.mean()),
           "variants": summ, "gap_range_across_variants_by_roster_rule": spread, "mean_ci_halfwidth_2plus_group": float(ci_halfwidth_2plus),
           "max_range_points": float(rng), "verdict": L.verdict(rng)}
    return res


# ------------------------------------------------------------------ J2-J4
def j2_j4():
    X = ctx()
    box, rep = X["box"], X["reports"]
    seasons = list(E2.TRAIN) + list(E2.VALID)
    games = X["games"][X["games"].season.isin(seasons)]
    D = X["D"][X["D"].season.isin(seasons)].copy()
    D["e1"] = D.pred
    D["r"] = D.pts - D.e1
    results = {}
    feats = {}
    rot_list, win_list = (10.0, 15.0, 20.0), (5, 10, 20)
    for rot in rot_list:
        for win in win_list:
            t = time.time()
            F = L.build_features_with(games, box, rep, rot_mpg=rot, window=win)
            feats[(rot, win)] = L.add_q_variants(F)
            print(f"  features rot={rot} win={win} {time.time() - t:.0f}s", flush=True)
    base = feats[(15.0, 10)]
    ref = pd.read_parquet(E2.FEATURES_PARQUET)
    mm = base.merge(ref, on=["game_id", "team"], suffixes=("", "_r"))
    assert max(np.abs(mm[c] - mm[c + "_r"]).max() for c in E2.FEAT_COLS) == 0.0, "baseline rebuild != frozen #62 parquet"

    def run(F, variant):
        cols = ["game_id", "team"] + [c for c in F.columns if c.endswith(("_own", "_opp")) and c[:-4] in
                                       ("out_pts", "q_pts", "back_pts", "out_min", "w05", "all1")]
        M = D.merge(F[cols], on=["game_id", "team"], how="left", validate="one_to_one")
        tr, va = M[M.season.isin(E2.TRAIN)].reset_index(drop=True), M[M.season.isin(E2.VALID)].reset_index(drop=True)
        m, met, pred = L.fit_eval(tr, va, variant)
        return m, met, pred, va

    e1_met = E2.game_metrics(D[D.season.isin(E2.VALID)].game_id.values, D[D.season.isin(E2.VALID)].home.values,
                             D[D.season.isin(E2.VALID)].pts.values, D[D.season.isin(E2.VALID)].pred.values)
    _, base_met, _, _ = run(base, "B")
    results["E1_2023_24"] = {"total_mae": e1_met["total_mae"], "team_mae": e1_met["team_mae"], "n_games": e1_met["n_games"]}
    results["baseline_B_rot15_win10_separate"] = {"total_mae": base_met["total_mae"], "team_mae": base_met["team_mae"]}

    def row(F, variant):
        m, met, _, _ = run(F, variant)
        ci = L.paired_diff(met, base_met)
        return {"total_mae": met["total_mae"], "team_mae": met["team_mae"], "lambda": m["lambda"],
                "diff_total_mae_vs_baseline": ci[0], "diff_lo": ci[1], "diff_hi": ci[2]}

    j2 = {f"rot{int(r)}": row(feats[(r, 10)], "B") for r in rot_list}
    j3 = {f"win{w}": row(feats[(15.0, w)], "B") for w in win_list}
    j4 = {k: row(base, v) for k, v in L.Q_VARIANTS.items()}
    grid = {f"rot{int(r)}_win{w}": row(feats[(r, w)], "B") for r in rot_list for w in win_list}

    def swing(d):
        t = [v["total_mae"] for v in d.values()]
        te = [v["team_mae"] for v in d.values()]
        return {"total_mae_range": float(max(t) - min(t)), "team_mae_range": float(max(te) - min(te))}
    out = {"label": "descriptive; NOT a selection; train 2021-23, validate 2023-24; variant B ridge (lambda by LOSO-CV as in #62)",
           "reference": results, "J2_rotation_threshold": {"rows": j2, **swing(j2)}, "J3_player_window": {"rows": j3, **swing(j3)},
           "J4_questionable_handling": {"rows": j4, **swing(j4)}, "grid_rot_x_window": grid,
           "baseline_rebuild_equals_frozen_parquet": True}
    for k in ("J2_rotation_threshold", "J3_player_window", "J4_questionable_handling"):
        out[k]["verdict"] = L.verdict(max(out[k]["total_mae_range"], out[k]["team_mae_range"]))
    return out


# ------------------------------------------------------------------ J5
def j5():
    X = ctx()
    box = X["box"]
    pmh = L.pm_history(box)
    F = X["feat"]
    F = F[F.season.isin(C.SEASONS[:3])].copy()
    R = team_resid(X["D"])
    M = F.merge(R[["game_id", "team", "r"]], on=["game_id", "team"], how="left", validate="one_to_one")
    M["d"] = [E2.day_int(x) for x in M.date]
    cache = {}

    def sums(row, side):
        """Σ over Out/Doubtful rotation-quality listed players of (ppg, mpg/240, pm per game)."""
        s = row.listed_rot_own if side == "own" else row.listed_rot_opp
        tot = [0.0, 0.0, 0.0]
        npm = 0
        for tok in (s or "").split(";"):
            if not tok:
                continue
            aid, st, _ = tok.split(":")
            if st not in E2.OUT_STATUSES:
                continue
            key = (int(aid), row.d, E2.year_of(row.season))
            if key not in cache:
                cache[key] = L.out_player_values(box, pmh, *key)
            v = cache[key]
            if v is None:
                continue
            tot[0] += v[0]
            tot[1] += v[1] / 240.0
            if np.isfinite(v[2]):
                tot[2] += v[2]
                npm += 1
        return tot

    # listed_rot_opp is not stored: take it from the opponent's own row
    lr = M.set_index(["game_id", "team_k"]).listed_rot_own.to_dict()
    M["listed_rot_opp"] = [lr.get((g, o), "") for g, o in zip(M.game_id, M.opp_k)]
    own = np.array([sums(r, "own") for r in M.itertuples(index=False)])
    opp = np.array([sums(r, "opp") for r in M.itertuples(index=False)])
    y = M.r.values
    names = ["PPG (sum)", "minutes share (sum mpg/240)", "plus_minus per game (sum)"]
    out = {"n_team_games": int(np.isfinite(y).sum()), "seasons": list(C.SEASONS[:3]), "measures": {}}
    has_out = own[:, 0] > 0
    for i, nm in enumerate(names):
        out["measures"][nm] = {
            "own_only_all_games": L.r2_corr(own[:, i], y),
            "own_only_games_with_>=1_out": L.r2_corr(own[has_out, i], y[has_out]),
            "own_and_opp_2_regressors": L.r2_multi(np.column_stack([own[:, i], opp[:, i]]), y)}
    out["all_three_own_regressors"] = L.r2_multi(own, y)
    key = "own_only_all_games"
    best = max(names, key=lambda n: out["measures"][n][key]["r2"])
    out["best_by_R2_own_only_all_games"] = best
    r2s = {n: out["measures"][n][key]["r2"] for n in names}
    out["r2_range"] = float(max(r2s.values()) - min(r2s.values()))
    out["mean_residual_when_own_has_out_rotation_player"] = float(np.nanmean(y[has_out]))
    out["mean_residual_otherwise"] = float(np.nanmean(y[~has_out]))
    gap = abs(r2s["PPG (sum)"] - r2s["minutes share (sum mpg/240)"])
    out["r2_gap_ppg_vs_minutes_share"] = float(gap)
    out["verdict"] = ("CHOICE DOESN'T MATTER" if gap < 0.005 else "MATTERS A LITTLE")
    out["verdict_rule"] = ("PPG vs minutes share: R^2 gap < 0.005 = doesn't matter (declared before the numbers); plus_minus is reported "
                           "separately and is far worse")
    return out


# ------------------------------------------------------------------ J6
def j6():
    X = ctx()
    box = X["box"]
    F = X["feat"]
    df = box.df
    name2aids = {}
    for a, nm in box.names.items():
        name2aids.setdefault(E2.norm_display(nm), []).append(int(a))
    aid_rows = {int(a): (g.d.values.astype(np.int64), g.team.values, g.yr.values.astype(np.int64))
                for a, g in df.sort_values(["d", "game_id"], kind="stable").groupby("aid", sort=False)}
    R = team_resid(X["D"])
    M = F.merge(R[["game_id", "team", "r"]], on=["game_id", "team"], how="left", validate="one_to_one")
    M["d"] = [E2.day_int(x) for x in M.date]
    recs = []
    types_by_row = []
    for row in M.itertuples(index=False):
        nm = []
        if row.n_unmatched_own > 0:
            year = E2.year_of(row.season)
            for tok in row.unmatched_names_own.split(";"):
                raw, st = tok.rsplit("|", 1)
                norm = E2.norm_report_name(raw)
                typ = L.classify_unmatched(norm, row.team_k, row.d, year, name2aids, aid_rows)
                rot = False
                if typ in ("traded_in_or_other_team", "same_team_stale_or_unexplained"):
                    a = name2aids[norm][0]
                    w = box.stats(a, row.d, year)
                    rot = bool(w is not None and w[0] >= E2.ROT_MPG)
                recs.append({"type": typ, "status": st, "rotation_likely": rot, "season": row.season})
                nm.append((typ, st, rot))
        types_by_row.append(nm)
    T = pd.DataFrame(recs)
    out = {"n_unmatched_names": int(len(T)), "n_team_games_total": int(len(M)),
           "n_team_games_with_listing": int((M.n_listed_own > 0).sum()),
           "unmatched_share_of_listed_names": float(M.n_unmatched_own.sum() / max(M.n_listed_own.sum(), 1))}
    if len(T):
        out["share_by_type"] = {k: float(v) for k, v in T.type.value_counts(normalize=True).items()}
        out["count_by_type"] = {k: int(v) for k, v in T.type.value_counts().items()}
        out["share_by_status"] = {k: float(v) for k, v in T.status.value_counts(normalize=True).items()}
        out["rotation_likely_count_(had_history_mpg>=15)"] = int(T.rotation_likely.sum())
        out["rotation_likely_by_type"] = {k: int(v) for k, v in T[T.rotation_likely].type.value_counts().items()}
    M["any_unmatched"] = M.n_unmatched_own > 0
    M["any_rot_likely_unmatched"] = [any(x[2] for x in nm) for nm in types_by_row]
    M["any_out_unmatched"] = [any(x[1] in E2.OUT_STATUSES for x in nm) for nm in types_by_row]
    lst = M[(M.n_listed_own > 0) & M.report_found]
    out["residual_r_actual_minus_E1_team_pts"] = {
        "listed_games_none_unmatched": L.boot_mean_ci(lst[~lst.any_unmatched].r.values),
        "listed_games_>=1_unmatched": L.boot_mean_ci(lst[lst.any_unmatched].r.values),
        "listed_games_>=1_unmatched_Out/Doubtful": L.boot_mean_ci(lst[lst.any_out_unmatched].r.values),
        "listed_games_>=1_rotation_likely_unmatched": L.boot_mean_ci(lst[lst.any_rot_likely_unmatched].r.values),
        "all_games_none_unmatched": L.boot_mean_ci(M[~M.any_unmatched].r.values),
        "all_games_>=1_unmatched": L.boot_mean_ci(M[M.any_unmatched].r.values)}
    none = lst[~lst.any_unmatched].r.values
    out["difference_vs_none_bootstrap"] = {
        ">=1_unmatched": L.boot_diff_ci(lst[lst.any_unmatched].r.values, none),
        ">=1_unmatched_Out/Doubtful": L.boot_diff_ci(lst[lst.any_out_unmatched].r.values, none),
        ">=1_rotation_likely_unmatched": L.boot_diff_ci(lst[lst.any_rot_likely_unmatched].r.values, none)}
    d = out["difference_vs_none_bootstrap"][">=1_rotation_likely_unmatched"]
    out["share_of_listed_team_games_with_rotation_likely_unmatched"] = float(lst.any_rot_likely_unmatched.mean())
    excl = d["lo"] > 0 or d["hi"] < 0
    out["verdict"] = ("MATTERS A LITTLE" if excl and abs(d["diff"]) >= 0.3 else
                      f"CHOICE DOESN'T MATTER (not distinguishable from 0: point estimate {d['diff']:+.2f} pt in "
                      f"{100 * out['share_of_listed_team_games_with_rotation_likely_unmatched']:.0f}% of team-games, CI {d['lo']:+.2f}..{d['hi']:+.2f})")
    out["verdict_rule"] = ("team-games with >=1 rotation-likely unmatched Out name vs none: bootstrap CI of the mean-residual difference excludes 0 "
                           "and |diff| >= 0.3 pts = 'a little' (declared before the numbers; a team-game effect, not a game-total MAE effect)")
    return out


# ------------------------------------------------------------------ J7
def j7():
    X = ctx()
    rep, games = X["reports"], X["games"]
    rows = []
    n_games = n_with60 = n_later = 0
    late_add = []
    for gid, gg in games.groupby("game_id", sort=False):
        n_games += 1
        tip = float(gg.tip.iloc[0])
        away = gg[gg.venue == "away"].iloc[0]
        home = gg[gg.venue == "home"].iloc[0]
        matchup = f"{away.team_k}@{home.team_k}"
        mdy = pd.Timestamp(gg.date.iloc[0]).strftime("%m/%d/%Y")
        c60 = rep.last_covering(tip - E2.D_OFFSET_S, mdy, matchup)
        if c60 is None:
            continue
        n_with60 += 1
        cT = rep.last_covering(tip, mdy, matchup)
        if cT is None or cT[0] == c60[0]:
            continue                                            # no later report: nothing to compare
        n_later += 1
        for tk in (away.team_k, home.team_k):
            l60, nys60 = E2.team_status_map(c60[2], c60[3], tk)
            lT, nysT = E2.team_status_map(cT[2], cT[3], tk)
            if nysT or not lT and not l60:
                continue
            for p, s in l60.items():
                rows.append({"status60": s, "statusT": lT.get(p, "not listed"), "season": gg.season.iloc[0]})
            new_out = [p for p, s in lT.items() if s in E2.OUT_STATUSES and l60.get(p) not in E2.OUT_STATUSES]
            late_add.append(len(new_out))
    T = pd.DataFrame(rows)
    tab = {}
    for s, g in T.groupby("status60"):
        vc = g.statusT.value_counts()
        tab[s] = {"n": int(len(g)), "same_status_share": float((g.statusT == s).mean()),
                  "goes_to": {k: float(v / len(g)) for k, v in vc.items()}}
    out = {"games": n_games, "games_with_a_T-60_report": n_with60, "games_with_a_later_report_before_tip": n_later,
           "listed_player_rows_compared": int(len(T)), "by_status_at_T-60": tab,
           "overall_status_changed_share": float((T.status60 != T.statusT).mean()),
           "mean_new_Out_or_Doubtful_names_per_team_game_after_T-60": float(np.mean(late_add)),
           "share_team_games_with_>=1_new_Out_or_Doubtful": float(np.mean(np.array(late_add) > 0)),
           "note": "compared by raw report name; 'not listed' = absent from the later report (cleared); statuses as parsed (Out, Doubtful, Questionable, Probable, Available)"}
    T3 = T[T.status60.isin(["Out", "Doubtful", "Questionable"])]
    out["changed_share_among_Out_Doubtful_Questionable_at_T-60"] = float((T3.status60 != T3.statusT).mean())
    out["n_Out_Doubtful_Questionable_at_T-60"] = int(len(T3))
    ch = out["changed_share_among_Out_Doubtful_Questionable_at_T-60"]
    out["verdict"] = "CHOICE DOESN'T MATTER" if ch < 0.05 else ("MATTERS A LITTLE" if ch < 0.15 else "MATTERS")
    out["verdict_rule"] = ("share of Out/Doubtful/Questionable names at T-60 whose status changes by the last report before tip, over games "
                           "that have a later report: <5% doesn't matter, <15% a little (declared before the numbers)")
    return out


# ------------------------------------------------------------------ J8
def j8():
    X = ctx()
    S = E2.load_e1()["S"]
    seasons = ["2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
    tg = S.load_all_team_games(seasons)
    tg = tg.sort_values(["season", "date", "tip", "game_id", "team"], kind="stable")
    out = {"by_season": {}}
    errs = {25: [], 50: [], 100: []}
    prev_errs = []
    prev = None
    for s in seasons:
        t = tg[tg.season == s].reset_index(drop=True)
        fo, fp, ft = L.league_to_date(t, len(t) // 2)
        row = {"n_games": int(len(t) // 2), "final": {"ortg": fo, "pace": fp, "game_total": ft}}
        for n in (25, 50, 100):
            o, p, tt = L.league_to_date(t, n)
            row[f"n{n}"] = {"ortg": o, "pace": p, "game_total": tt, "err_ortg": o - fo, "err_pace": p - fp, "err_total": tt - ft}
            errs[n].append((o - fo, p - fp, tt - ft))
        if prev is not None:
            row["prev_season_final"] = {"ortg": prev[0], "pace": prev[1], "game_total": prev[2], "err_total_vs_this_final": prev[2] - ft}
            prev_errs.append((prev[0] - fo, prev[1] - fp, prev[2] - ft))
        prev = (fo, fp, ft)
        out["by_season"][s] = row
    out["mean_abs_error_vs_final"] = {
        f"n{n}": {"ortg": float(np.mean([abs(e[0]) for e in v])), "pace": float(np.mean([abs(e[1]) for e in v])),
                  "game_total_points": float(np.mean([abs(e[2]) for e in v]))} for n, v in errs.items()}
    out["mean_abs_error_vs_final"]["prev_season_final (what E1 uses before game 50)"] = {
        "ortg": float(np.mean([abs(e[0]) for e in prev_errs])), "pace": float(np.mean([abs(e[1]) for e in prev_errs])),
        "game_total_points": float(np.mean([abs(e[2]) for e in prev_errs]))}
    m = out["mean_abs_error_vs_final"]
    sw = abs(m["n25"]["game_total_points"] - m["n100"]["game_total_points"])
    out["mean_signed_error_vs_final_game_total"] = {f"n{n}": float(np.mean([e[2] for e in v])) for n, v in errs.items()}
    out["mean_signed_error_vs_final_game_total"]["prev_season_final"] = float(np.mean([e[2] for e in prev_errs]))
    out["note"] = ("error = the to-date league estimate minus the season's final value; game_total = 2 * ortg * pace / 100. "
                   "The 25/50/100 cut-offs are the number of league games played; E1's rule is 'use the prior-season value until 50 games'. "
                   "The effect on E1 itself is out of scope (E1 is not refit).")
    out["verdict"] = ("MATTERS A LITTLE" if m["n25"]["game_total_points"] > 1.0 else "CHOICE DOESN'T MATTER")
    out["verdict_rule"] = "to-date league game-total error at 25 games >1.0 pt = 'a little' (E1 total MAE is 14.8); declared before the numbers"
    return out


# ------------------------------------------------------------------ J11
def j11():
    import glob
    f = sorted(glob.glob(str(C.ROOT / "logs" / "ledger_raw" / "*.json")))[-1]
    raw = json.loads(Path(f).read_text())
    rows = []
    for shard, lst in raw["fills"].items():
        for fl in lst:
            side = fl.get("outcome_side") or fl.get("side")
            p = float(fl["yes_price_dollars"]) if side == "yes" else float(fl["no_price_dollars"])
            rows.append({"ticker": fl["market_ticker"], "series": fl["market_ticker"].split("-")[0], "count": float(fl["count_fp"]),
                         "price": p, "fee": float(fl["fee_cost"]), "taker": bool(fl["is_taker"]),
                         "yes_p": float(fl["yes_price_dollars"]), "no_p": float(fl["no_price_dollars"])})
    T = pd.DataFrame(rows)
    T["exp_taker"] = [L.fee_expected(c, p, 0.07) for c, p in zip(T["count"], T.price)]
    T["exp_maker"] = [L.fee_expected(c, p, 0.0175) for c, p in zip(T["count"], T.price)]
    T["sym_ok"] = [abs(L.fee_expected(c, y, 0.07) - L.fee_expected(c, n, 0.07)) < 1e-9 for c, y, n in zip(T["count"], T.yes_p, T.no_p)]
    T["exp"] = np.where(T.taker, T.exp_taker, T.exp_maker)
    T["diff"] = T.fee - T.exp
    T["frac_count"] = (T["count"] % 1).abs() > 1e-9
    ex = lambda d: {"n": int(len(d)), "exact_match_share": float((d["diff"].abs() < 5e-5).mean()),
                    "mean_diff_real_minus_formula": float(d["diff"].mean()), "max_abs_diff": float(d["diff"].abs().max()),
                    "sum_real_fee": float(d.fee.sum()), "sum_formula_fee": float(d.exp.sum())}
    bad = T[T["diff"].abs() >= 5e-5]
    out = {"ledger_file": Path(f).name, "n_fills": int(len(T)), "all": ex(T), "taker": ex(T[T.taker]), "maker": ex(T[~T.taker]),
           "integer_counts": ex(T[~T.frac_count]), "fractional_counts": ex(T[T.frac_count]),
           "by_series_top": {s: ex(g) for s, g in T.groupby("series") if len(g) >= 3},
           "yes_no_price_symmetry_ok_all": bool(T.sym_ok.all()),
           "rate_used": "taker 0.07, maker 0.0175 (the maker rate applies where the ledger flags a maker fill)",
           "mismatch_examples": [{k: (float(v) if not isinstance(v, (str, bool)) else v) for k, v in r.items() if k in
                                   ("series", "count", "price", "fee", "exp", "diff", "taker")} for r in bad.head(8).to_dict("records")]}
    out["maker_rows"] = [{k: (float(v) if not isinstance(v, (str, bool)) else v) for k, v in r.items() if k in
                          ("series", "count", "price", "fee", "exp", "diff")} for r in T[~T.taker].to_dict("records")]
    tk = out["taker"]
    rel = abs(tk["sum_real_fee"] - tk["sum_formula_fee"]) / tk["sum_formula_fee"]
    out["taker_total_fee_relative_difference"] = float(rel)
    out["verdict"] = ("CHOICE DOESN'T MATTER" if (rel < 0.01 and tk["max_abs_diff"] < 0.001) else
                      ("MATTERS A LITTLE" if rel < 0.05 else "MATTERS"))
    out["verdict_rule"] = "taker fills: total fee within 1% of the formula and no fill off by >$0.001 = doesn't matter (declared before the numbers)"
    return out


STEPS = {"repro": repro, "J1": j1, "J2_J4": j2_j4, "J5": j5, "J6": j6, "J7": j7, "J8": j8, "J11": j11}


def main(argv):
    if not argv or "J9" in argv or "J10" in argv:
        from pb_j9_j10 import j9, j10
        STEPS["J9"], STEPS["J10"] = j9, j10
    which = argv or list(STEPS)
    for k in which:
        t = time.time()
        print(f"== {k}", flush=True)
        save(k, STEPS[k]())
        print(f"   done {time.time() - t:.0f}s", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
