"""Research log #38 runner.  Usage (repo root, .venv python):  run.py smoke | run
  smoke  NO outcomes: first 10 discovery games at k=10 (n, r_o, r_p, projection, T-1h market median), worked example,
         distribution of n at discovery games.
  run    discovery: 5 MAEs -> choose k -> sigma -> gates (as #37). Only if ALL pass: unseen is loaded and the betting
         rule + pass bar + coin flip are run. Writes results.json, games.csv, bets.csv here.
#37 code is imported (not modified) from ../nba_totals_pace; totals.sqlite is opened read-only."""
from __future__ import annotations

import importlib.util
import json
import sqlite3 as _sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ratings2 as r2  # noqa: E402  (also puts #37's dir on sys.path)
import lib  # noqa: E402  (#37 lib)
import prep  # noqa: E402  (#37 prep)


def _load_pace_run():
    spec = importlib.util.spec_from_file_location("pace_run37", r2.PACE_DIR / "run.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


pr = _load_pace_run()


class _RoSqlite:
    """Why copied: #37's prep/run call sqlite3.connect(TOT) read-write. This shim reroutes any plain path to
    file:...?mode=ro (uri=True), so totals.sqlite is only ever opened read-only. Already-URI calls pass through."""
    def __getattr__(self, name):
        return getattr(_sqlite3, name)

    @staticmethod
    def connect(database, *a, **kw):
        s = str(database)
        if not s.startswith("file:"):
            return _sqlite3.connect(f"file:{Path(s).as_posix()}?mode=ro", uri=True)
        return _sqlite3.connect(database, *a, **kw)


prep.sqlite3 = _RoSqlite()
pr.sqlite3 = _RoSqlite()

THETAS, PRIMARY = pr.THETAS, pr.PRIMARY
K_GRID = r2.K_GRID


# ------------------------------------------------------------------ pure helpers (unit-tested)
def choose_k(maes):
    """maes: {k: MAE}. Lowest MAE; a tie goes to the larger k."""
    return min(maes, key=lambda k: (maes[k], -k))


GATE_CUTOFF = "2026-01-23"    # ET; gate-stage ratings use box scores dated strictly before this


def attach_outcomes(games, strikes, which):
    """#37's attach, then (amendment 3) actual = hoopR total via hoopr_id ONLY. #37 would fill missing actuals from
    kalshi_total (expiration_value); that is overridden here. Missing actuals are counted before any fill; none filled."""
    g, s = pr.attach_outcomes(games, strikes, which)
    h = prep.hoopr_games(2026).set_index("game_id")["total"]
    g["actual"] = g.hoopr_id.map(h)
    g.attrs["missing_actual_before_fill"] = int(g.actual.isna().sum())
    return g, s


def load_games():
    games, strikes, info = prep.build_games()
    return games, strikes, info


def make_proj_fn(R):
    def f(g, k, mode="blend"):
        return r2.project_games(R, g, k, mode)
    f.R = R
    return f


def n_cols(g, R):
    n = r2.n_before(R, g)
    return g.assign(n_away=[a for a, _ in n], n_home=[b for _, b in n])


# ------------------------------------------------------------------ smoke
def smoke():
    R = r2.load_ratings(prep.BOX, before=GATE_CUTOFF)
    games, strikes, info = load_games()
    d = games[games.split == "discovery"]
    print("=== prior sanity (2024-25 regular season, after the tov fix) vs #37 reported values ===")
    print("  Lo_prev %.4f  Lp_prev %.3f" % (R.Lo_prev, R.Lp_prev))
    print("  team  v2 (ORtg, DRtg, pace)               #37")
    for t, ref in (("BOS", "1.175/1.080/99.1"), ("DET", "1.120/1.101/103.1")):
        o, dd, p = R.prev[t]
        print(f"  {t}   {o:.3f}/{dd:.3f}/{p:.1f}   {ref}")
    print("  discovery games with a finite 2025-26 box-score input: asserted in team_games")
    print("discovery games:", len(d), " build info games_by_split:", info["games_by_split"])
    print("league prior: Lo_prev %.4f Lp_prev %.2f" % (R.Lo_prev, R.Lp_prev))
    print("=== first 10 discovery games, k=10 (NO outcomes) ===")
    for r in d.head(10).itertuples():
        D = r2.decision_date(r.tip, r.T)
        det = R.project(r.away, r.home, D, 10, detail=True)
        pa, pb = det["parts_away"], det["parts_home"]
        print(f"{r.event} D={D} n_away={pa['n']} n_home={pb['n']} n_lg={pa['n_lg']} r_o={pa['r_o']:.4f} "
              f"r_p={pa['r_p']:.4f} proj={det['proj']:.2f} (old #37 proj {r.proj:.2f}) market_median={r.median}")
    r = d.iloc[0]
    D = r2.decision_date(r["tip"], r["T"])      # NB: Series.T is the transpose, so index by key
    det = R.project(r.away, r.home, D, 10, detail=True)
    print(f"=== worked example: {r.away} @ {r.home} on {D}, k=10 ===")
    for side, ra, p in (("away " + r.away, det["rating_away"], det["parts_away"]),
                        ("home " + r.home, det["rating_home"], det["parts_home"])):
        print(f"  {side}: n={p['n']} prior(O,D,P)={tuple(round(x, 4) for x in p['prior'])} "
              f"cur={None if p['cur'] is None else tuple(round(x, 4) for x in p['cur'])} "
              f"blend=({ra[0]:.4f},{ra[1]:.4f},{ra[2]:.2f})")
    print(f"  shared pace={det['pace']:.3f}  away pts={det['A']:.2f}  home pts={det['B']:.2f}  total={det['proj']:.2f}")
    ns = r2.n_before(R, d)
    allk = np.array([x for pair in ns for x in pair])
    print("=== n (games before D) per team at discovery games ===")
    print("  teams-games:", len(allk), " min", allk.min(), " median", np.median(allk), " max", allk.max(),
          " share n==0: %.3f  n<5: %.3f  n<20: %.3f" % ((allk == 0).mean(), (allk < 5).mean(), (allk < 20).mean()))
    print("  quantiles 10/25/50/75/90:", np.quantile(allk, [.1, .25, .5, .75, .9]))
    t0 = time.time()
    for k in K_GRID:
        t = time.time()
        pj = r2.project_games(R, d, k)
        print(f"  k={k}: projections {len(pj)} (discovery games {len(d)}), NaN count {int(pj.isna().sum())}, "
              f"non-finite {int((~np.isfinite(pj)).sum())}, {time.time() - t:.2f}s")
        assert np.isfinite(pj).all()
        for mode in (("prior", "current") if k == 10 else ()):
            pm = r2.project_games(R, d, k, mode)
            print(f"  mode={mode}: NaN count {int(pm.isna().sum())}")
            assert np.isfinite(pm).all()
    print(f"  total {time.time() - t0:.2f}s")


# ------------------------------------------------------------------ run
def core_run(games, strikes, info, proj_fn, attach=None, n_boot=10000, out_dir=None, write=True, proj_fn_full=None):
    """proj_fn(games_df, k, mode) -> Series of projections. attach(games, strikes, which) -> (g, s) with outcomes.
    The unseen split is passed to attach ONLY after every gate has passed."""
    attach = attach or attach_outcomes
    out = {"build_info": dict(info), "preregistered_versions": 7, "k_grid": list(K_GRID)}
    games_d = games[games.split == "discovery"].copy()
    gd0, sd0 = attach(games_d, strikes, ["discovery"])
    out["discovery_missing_actual"] = int(gd0.actual.isna().sum())      # counted before anything; nothing is filled
    gd0 = gd0.dropna(subset=["actual"]).copy()
    # ---- 5-model grid on discovery
    projs = {k: proj_fn(gd0, k, "blend") for k in K_GRID}
    for k in K_GRID:
        assert np.isfinite(projs[k].values).all(), f"non-finite projection at k={k}"
    maes = {k: float((projs[k] - gd0.actual).abs().mean()) for k in K_GRID}
    kstar = choose_k(maes)
    out["discovery_mae_by_k"] = {str(k): maes[k] for k in K_GRID}
    out["chosen_k"] = int(kstar)
    gd = gd0.assign(proj=projs[kstar])
    if hasattr(proj_fn, "R"):
        gd = n_cols(gd, proj_fn.R)
    sigma = float((gd.actual - gd.proj).std(ddof=1))
    out["sigma"] = sigma
    out["discovery_mean_error_actual_minus_proj"] = float((gd.actual - gd.proj).mean())
    sd = pr.add_model_p(sd0[sd0.event.isin(gd.event)], gd, sigma)
    acc = pr.accuracy(gd, sd)
    q = sd.dropna(subset=["mid", "y"])
    rt = lib.residual_test(q.mid, q.p, q.y, q.event, n_boot=n_boot)
    cnt, per_wk = pr.gate3_count(q, pr.span_weeks(gd))
    gate1 = bool(acc["model_mae"] <= acc["market_mae"] + 0.5 and acc["model_brier"] <= acc["market_brier"] + 0.002)
    gate2 = lib.gate2_pass(*rt)
    gate3 = bool(per_wk >= 20)
    out["gates"] = {"accuracy": acc, "gate1_pass": gate1,
                    "residual_test": {"coef": rt[0], "ci95": [rt[1], rt[2]], "gate2_pass": bool(gate2)},
                    "volume": {"strikes_over_cost": cnt, "per_week": per_wk, "gate3_pass": gate3}}
    # ---- reported regardless (discovery only)
    out["discovery_by_month"] = pr.by_group(gd, sd, "month")
    out["discovery_by_stage"] = pr.by_group(gd, sd, "stage")
    out["discovery_calibration_model"] = pr.calibration(q, "p")
    out["discovery_calibration_market"] = pr.calibration(q, "mid")
    pieces = {}
    for mode in ("prior", "current"):
        gm = gd.assign(proj=proj_fn(gd, kstar, mode))
        pieces[mode] = {"mae_all_games": float((gm.proj - gm.actual).abs().mean()),
                        "bias_actual_minus_proj": float((gm.actual - gm.proj).mean()),
                        "mae_games_with_median": pr.accuracy(gm, sd)["model_mae"]}
    out["discovery_unblended_at_chosen_k"] = pieces
    if "n_away" in gd:
        allk = np.concatenate([gd.n_away.values, gd.n_home.values])
        out["discovery_n_before_D"] = {"min": int(allk.min()), "median": float(np.median(allk)), "max": int(allk.max()),
                                       "share_n0": float((allk == 0).mean())}
    # extras restored from #37 (discovery)
    qual = q[(q.mid >= 0.15 - 1e-12) & (q.mid <= 0.85 + 1e-12)].event.nunique()
    out["gates"].update({"qualifying_games_15_85": int(qual), "qualifying_games_per_week": qual / pr.span_weeks(gd)})
    out["median_sensitivity_spread_le_10c_discovery"] = pr.median_sensitivity(gd, sd)
    lm, lp_ = lib.logit(q.mid), lib.logit(q.p)
    blend_p = 1 / (1 + np.exp(-(lm + lp_) / 2))
    out["blend_brier_discovery"] = float(((blend_p - q.y) ** 2).mean())
    out["spread_stats_discovery"] = {"median_spread": float((q.ask - q.bid).median()),
                                     "share_spread_ge_20c": float(((q.ask - q.bid) >= 0.2).mean())}
    allp = all([gate1, bool(gate2), gate3])
    out["all_gates_pass"] = bool(allp)
    games_out, bets_out = gd, []
    if not allp:
        out["verdict"] = "gate failed on discovery; no betting rule run, unseen split never loaded"
    else:
        pf_u = proj_fn_full() if proj_fn_full else proj_fn      # full-season ratings only now
        gu, su = attach(games, strikes, ["unseen"])
        out["unseen_missing_actual"] = int(gu.actual.isna().sum())
        gu = gu.dropna(subset=["actual"]).copy()
        gu["proj"] = pf_u(gu, kstar, "blend")
        assert np.isfinite(gu.proj.values).all()
        if hasattr(pf_u, "R"):
            gu = n_cols(gu, pf_u.R)
        su = pr.add_model_p(su[su.event.isin(gu.event)], gu, sigma)
        accu = pr.accuracy(gu, su)
        out["unseen_accuracy"] = accu
        out["unseen_by_month"] = pr.by_group(gu, su, "month")
        out["unseen_by_stage"] = pr.by_group(gu, su, "stage")
        qu = su.dropna(subset=["mid", "y"])
        out["unseen_calibration_model"] = pr.calibration(qu, "p")
        out["unseen_calibration_market"] = pr.calibration(qu, "mid")
        out["versions"] = {}
        for th in THETAS:
            bd, bu = pr.run_bets(gd, sd, th), pr.run_bets(gu, su, th)
            v = {"discovery": pr.bet_report(bd, gd), "unseen": pr.bet_report(bu, gu)}
            if len(bu):
                v["coin_flip_unseen"] = pr.coin_flip(gu, su, bu, n=500, seed=lib.SEED)
            out["versions"][f"theta_{th}"] = v
            bets_out += [bd.assign(split="discovery"), bu.assign(split="unseen")]
        pv = out["versions"][f"theta_{PRIMARY}"]
        u, d_ = pv["unseen"], pv["discovery"]
        bar = {
            "1_mean_net_pos_both": bool(d_.get("n") and u.get("n") and d_["mean_net_per_contract"] > 0 and u["mean_net_per_contract"] > 0),
            "2_unseen_boot_lo_gt0": bool(u.get("n") and u["ci95"][0] > 0),
            "3_unseen_bets_ge_100": bool(u.get("n", 0) >= 100),
            "4_coin_flip_le_5pct": bool(pv.get("coin_flip_unseen", {}).get("frac_copies_ge_actual", 1) <= 0.05),
            "5_usd_per_week_ge_2": bool(u.get("n") and u["usd_per_week_at_10"] >= 2),
            "6_unseen_accuracy": bool(accu["model_mae"] <= accu["market_mae"] + 0.5 and
                                      accu["model_brier"] <= accu["market_brier"] + 0.002)}
        out["pass_bar_primary"] = bar
        out["PASS"] = bool(all(bar.values()))
        games_out = pd.concat([gd, gu])
    if write:
        d = Path(out_dir or HERE)
        games_out.drop(columns=["hoopr_date"], errors="ignore").to_csv(d / "games.csv", index=False)
        (pd.concat(bets_out) if bets_out else pd.DataFrame()).to_csv(d / "bets.csv", index=False)
        (d / "results.json").write_text(json.dumps(out, indent=1, default=str))
    return out


def full_run():
    games, strikes, info = load_games()
    fn = make_proj_fn(r2.load_ratings(prep.BOX, before=GATE_CUTOFF))
    out = core_run(games, strikes, info, fn, proj_fn_full=lambda: make_proj_fn(r2.load_ratings(prep.BOX)))
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    {"smoke": smoke, "run": full_run}[sys.argv[1]]()
