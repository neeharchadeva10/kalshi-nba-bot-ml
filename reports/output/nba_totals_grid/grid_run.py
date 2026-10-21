"""Research log #42 runner.  Usage (repo root, .venv python):  grid_run.py smoke | discovery | final
  smoke      NO 2025-26 outcomes: 26,880 x 660 finite discovery projections, sample projections, cross-checks against
             #38 (F1_R1, k=20) and #39 (F1..F5 x R1..R3), fitted constants, timing.
  discovery  attach discovery outcomes, back-test all 134,400 strategies, winner (max LB), luck check, winner
             diagnostics; freezes the winner in winner_frozen.json.  The unseen half is never loaded.
  final      ONLY the frozen winner, once, on the unseen half (refuses without winner_frozen.json).
#37/#38/#39 code is imported (not modified); totals.sqlite is opened read-only (via #38's shim)."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import grid_lib as gl  # noqa: E402
import grid_bets as gb  # noqa: E402
from grid_lib import lib, r2, tn  # noqa: E402
import prep  # noqa: E402  (#37)

FROZEN = HERE / "winner_frozen.json"
FINAL_RESULT = HERE / "final_results.json"
GATE_CUTOFF = gl.GATE_CUTOFF


def _load_v2_run():
    spec = importlib.util.spec_from_file_location("run_v2_grid", tn.V2_DIR / "run.py")   # by path: name clash
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


v2 = _load_v2_run()          # also installs #38's read-only sqlite shim on prep / #37 run
pr = v2.pr


# ------------------------------------------------------------------ data loading
def read_box(year, before=None):
    """team_box parquet; if `before` (ET date string) is given the rows dated on/after it are dropped IMMEDIATELY,
    before any other processing (gate stage: the unseen box scores are never processed)."""
    tb = pd.read_parquet(Path(prep.BOX) / f"team_box_{year}.parquet")
    if before is not None:
        gdt = pd.to_datetime(tb.game_date_time)
        if gdt.dt.tz is None:
            gdt = gdt.dt.tz_localize("UTC")
        et = gdt.dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
        tb = tb[et.values < np.datetime64(before)].copy()
    return tb


class World:
    pass


def load_world(before=None, raw=False):
    """Seasons for 2025-26 (prior 2024-25) and for the 2024-25 constants fit (prior 2023-24).  `before`: box scores of
    2025-26 dated on/after it are not used.  raw=True also keeps the raw box frames (for #39 cross-checks)."""
    w = World()
    tb24, tb25, tb26 = read_box(2024), read_box(2025), read_box(2026, before)
    w.tb = (tb24, tb25, tb26)
    prev24 = gl.team_games_g(tb24, (2,))
    prev25 = gl.team_games_g(tb25, (2,))
    cur25 = gl.team_games_g(tb25, r2.CUR_SEASON_TYPES)
    cur26 = gl.team_games_g(tb26, r2.CUR_SEASON_TYPES)
    assert cur25.date.max() < pd.Timestamp("2025-07-01"), "2024-25 fit data must predate 2025-07-01"
    if before is not None:
        assert cur26.date.max() < pd.Timestamp(before), "gate-stage box scores must predate the cutoff"
    w.ff_coef, w.ff_r2 = gl.fit_four_factor(prev25)          # the 2024-25 regular season only
    w.season = gl.Season(prev25, cur26, w.ff_coef)
    w.season24 = gl.Season(prev24, cur25, w.ff_coef)
    w.games24 = tn.fit_games_2425(cur25)
    w.cur26, w.cur25, w.prev25, w.prev24 = cur26, cur25, prev25, prev24
    w.before = before
    return w


def fit_world_constants(w):
    return gl.fit_constants(w.season24, w.games24)


# ------------------------------------------------------------------ printing
def print_constants(consts, ff_coef, ff_r2):
    print("=== four-factor OLS fit (2024-25 regular season): ORtg = b0 + b1 eFG + b2 TOV% + b3 ORB% + b4 FT/FGA ===")
    print("  coef", np.round(ff_coef, 5).tolist(), " R2 %.4f" % ff_r2)
    print("=== constants per (A, C): c_b2b (per back-to-back team), e_cat[0/1/2] (3+ = reference), b (shrink slope) ===")
    print("  fitted on 2024-25 walk-forward, prior 2023-24, k=20, shift on, pace avg, no split; intercepts not applied")
    print(f"  {'A':6s} {'C':11s} {'c_b2b':>8s} {'e0':>8s} {'e1':>8s} {'e2':>8s} {'b':>7s}")
    for ai, A in enumerate(gl.A_OPTS):
        for ci, C in enumerate(gl.C_OPTS):
            e = consts.e_cat[ai, ci]
            print(f"  {A:6s} {C:11s} {consts.c_b2b[ai, ci]:+8.4f} {e[0]:+8.4f} {e[1]:+8.4f} {e[2]:+8.4f} {consts.b[ai, ci]:7.4f}")


# ------------------------------------------------------------------ smoke
SAMPLE_SPECS = [
    ("default (user's)", dict(A="w0.5", B="avg", C="season", D=20, E="on", F="none", G="off", H="off")),
    ("w1.0 own season", dict(A="w1.0", B="own", C="season", D=20, E="on", F="none", G="off", H="off")),
    ("mult/mult kenpom", dict(A="mult", B="mult", C="kenpom", D=20, E="on", F="none", G="off", H="off")),
    ("add/termown massey +b2b +shrink", dict(A="add", B="termown", C="massey", D=10, E="on", F="b2b", G="off", H="on")),
    ("w0.25/avg fourfactor split cat", dict(A="w0.25", B="avg", C="fourfactor", D=20, E="off", F="cat", G="on", H="off")),
    ("w0.75/own last10 k0", dict(A="w0.75", B="own", C="last10", D=0, E="on", F="none", G="off", H="off")),
    ("w0.5/avg decay5 k40 split", dict(A="w0.5", B="avg", C="decay5", D=40, E="on", F="none", G="on", H="off")),
]
# #39 formula -> (A, B)
F39 = {"F1": ("w0.5", "avg"), "F2": ("mult", "mult"), "F3": ("add", "avg"), "F4": ("w0.5", "termown"),
       "F5": ("add", "termown")}
R39 = {"R1": "season", "R2": "decay10", "R3": "kenpom"}


def smoke():
    t0 = time.time()
    games, strikes, info = v2.load_games()
    d = games[games.split == "discovery"].reset_index(drop=True)
    print("discovery games:", len(d), " build info games_by_split:", info["games_by_split"])
    maxD = max(r2.decision_date(a, b) for a, b in zip(d["tip"], d["T"]))
    assert maxD < np.datetime64(GATE_CUTOFF, "D"), f"discovery decision date {maxD} not before the gate cutoff"
    t1 = time.time()
    w = load_world(GATE_CUTOFF)
    print(f"world loaded in {time.time() - t1:.1f}s; 2025-26 box scores used: {len(w.cur26)} team-games dated "
          f"{w.cur26.date.min().date()} .. {w.cur26.date.max().date()} (cutoff {GATE_CUTOFF})")
    t1 = time.time()
    consts = fit_world_constants(w)
    t_const = time.time() - t1
    print_constants(consts, w.ff_coef, w.ff_r2)
    print(f"constants fitted in {t_const:.1f}s ({len(w.games24)} games of 2024-25)")
    t1 = time.time()
    P = gl.build_projections(w.season, d, consts)
    t_proj = time.time() - t1
    assert P.shape == (gl.N_FORMULAS, len(d)) == (26880, 660), P.shape
    assert np.isfinite(P).all(), "non-finite discovery projections"
    print(f"ASSERT OK: all {P.shape[0]} x {P.shape[1]} = {P.size} discovery projections finite  ({t_proj:.1f}s to build)")
    print("projection scale (no outcomes): per-formula mean over games: min %.1f  median %.1f  max %.1f; "
          "per-formula sd over games: median %.2f" % (P.mean(1).min(), np.median(P.mean(1)), P.mean(1).max(),
                                                      np.median(P.std(1))))
    print("=== sample projections (NO outcomes) next to the T-1h market median ===")
    for gi in (0, 330, 659):
        r = d.iloc[gi]
        D = r2.decision_date(r["tip"], r["T"])
        print(f"{r.event} D={D} market_median={r['median']:.2f}")
        for label, nm in SAMPLE_SPECS:
            sp = gl.spec_from_names(nm)
            print(f"   {label:34s} flat={gl.flat_from_spec(sp):5d}  proj={P[gl.flat_from_spec(sp), gi]:8.2f}")
    # ---- strike table without outcomes
    bt = gb.main_strike_table(d, strikes)
    print(f"main strike found for {int(bt.has_strike.sum())} of {len(d)} discovery games (no outcomes used)")
    # ---- cross-check 1: #38
    spec0 = gl.spec_from_names(gl.DEFAULTS)
    mine = P[gl.flat_from_spec(spec0)]
    ref = r2.project_games(w.season.R1, d, 20).values
    print("=== cross-check #38 (F1_R1, k=20): user's base settings vs ratings2.project_games ===")
    print("  max |diff| = %.3e over %d games" % (np.abs(mine - ref).max(), len(d)))
    assert np.abs(mine - ref).max() < 1e-9
    # ---- cross-check 2: #39
    tb24, tb25, tb26 = w.tb
    c25x = tn.team_games_x(tb25, r2.CUR_SEASON_TYPES)
    eng24 = tn.Engine(tn.team_games_x(tb24, (2,)), c25x)
    games24x = tn.fit_games_2425(c25x)
    c39, _ = tn.fit_all_c(eng24, games24x)
    c26x = tn.team_games_x(tb26, r2.CUR_SEASON_TYPES)
    for col in ("game_id", "date", "team", "opp", "pts", "opp_pts", "poss", "opp_poss", "tip", "season_type", "pace"):
        assert np.array_equal(c26x[col].values, w.cur26[col].values), f"team-games column {col} differs from #39's"
    for col in ("pts", "poss", "opp_pts", "opp_poss"):
        assert np.array_equal(c25x[col].values, w.cur25[col].values)
    print("=== cross-check #39 (team-games columns identical; 30 versions at k=20) ===")
    eng = tn.Engine(tn.team_games_x(tb25, (2,)), c26x)
    P39 = eng.project_table(d, c39)
    worst, worst_s1 = 0.0, 0.0
    for fid, (A, B) in F39.items():
        for rid, ct in R39.items():
            sp = gl.spec_from_names(dict(gl.DEFAULTS, A=A, B=B, C=ct))
            diff0 = np.abs(P[gl.flat_from_spec(sp)] - P39[f"{fid}_{rid}_S0"].values).max()
            worst = max(worst, diff0)
            ai, ci = gl.A_OPTS.index(A), gl.C_OPTS.index(ct)
            same_fit = B == "avg"       # my constants are fitted with pace = avg: same setting as #39's only for F1, F3
            if same_fit:
                sp1 = gl.spec_from_names(dict(gl.DEFAULTS, A=A, B=B, C=ct, F="b2b"))
                diff1 = np.abs(P[gl.flat_from_spec(sp1)] - P39[f"{fid}_{rid}_S1"].values).max()
                worst_s1 = max(worst_s1, diff1)
                assert abs(consts.c_b2b[ai, ci] - c39[(fid, rid)]) < 1e-9
            print(f"  {fid}_{rid} S0 max|diff| {diff0:.2e}" + (f"   S1 max|diff| {diff1:.2e}  (c mine {consts.c_b2b[ai, ci]:+.6f} "
                  f"vs #39 {c39[(fid, rid)]:+.6f})" if same_fit else "   (S1 not comparable: my c is fitted with pace avg)"))
    assert worst < 1e-9 and worst_s1 < 1e-9, (worst, worst_s1)
    print(f"  worst S0 diff {worst:.2e}, worst S1 diff {worst_s1:.2e}  (tolerance 1e-9)")
    rr = np.array([x[1] for x in w.season.r3_rounds])
    print(f"R3 adjustments: {len(rr)}; rounds max {rr.max()} (cap 100 hit {int((rr >= 100).sum())})")
    print(f"smoke total {time.time() - t0:.1f}s  (constants {t_const:.1f}s, 26,880 x 660 projections {t_proj:.1f}s)")


# ------------------------------------------------------------------ discovery
def _dim_summary(flat_df):
    """Per dimension option: eligible strategies, best / median LB, mean of mean net, share with mean > 0 / LB > 0."""
    out = {}
    el = flat_df[flat_df.eligible]
    for dname in gl.DIM_NAMES + ("T",):
        rows = {}
        for opt, g in el.groupby(dname):
            rows[str(opt)] = {"eligible": int(len(g)), "best_lb": float(g.lb.max()), "median_lb": float(g.lb.median()),
                              "mean_of_mean_net": float(g["mean"].mean()), "share_mean_pos": float((g["mean"] > 0).mean()),
                              "share_lb_pos": float((g.lb > 0).mean())}
        out[dname] = rows
    return out


def strategy_table(fin, nondef, thresholds=gb.THRESHOLDS, min_n=gb.MIN_N):
    nT, S = fin["n"].shape
    idx = np.indices(gl.SHAPE).reshape(len(gl.SHAPE), -1)
    cols = {"flat": np.tile(np.arange(S), nT), "T": np.repeat(np.array(thresholds), S)}
    for j, dn in enumerate(gl.DIM_NAMES):
        opts = np.array([str(o) for o in gl.DIM_OPTS[j]])
        cols[dn] = np.tile(opts[idx[j]], nT)
    cols["nondefault"] = np.tile(nondef, nT)
    for k in ("n", "wins", "win_rate", "avg_paid", "mean", "sd", "lb", "total"):
        cols[k] = fin[k].reshape(-1)
    df = pd.DataFrame(cols)
    df["eligible"] = (df.n >= min_n) & np.isfinite(df.lb)
    return df


def core_discovery(games, strikes, info, build_proj, attach, out_dir, n_boot=10000, min_n=gb.MIN_N, n_luck=2000,
                   write=True, frozen_path=None):
    """build_proj(discovery_games) -> (P (S, N), consts); attach(games, strikes, which) -> (g, s) with outcomes.
    The unseen split is never passed to either."""
    t0 = time.time()
    out_dir = Path(out_dir)
    frozen_path = Path(frozen_path or out_dir / "winner_frozen.json")
    if write and frozen_path.exists():
        raise RuntimeError(f"{frozen_path} already exists: discovery was already run and its winner frozen. "
                           "Refusing to overwrite (delete it by hand only if the pre-registration allows a re-run).")
    gd = games[games.split == "discovery"].reset_index(drop=True)
    assert len(gd) and (gd.split == "discovery").all()
    maxD = max(r2.decision_date(a, b) for a, b in zip(gd["tip"], gd["T"]))
    assert maxD < np.datetime64(GATE_CUTOFF, "D"), f"discovery decision date {maxD} not before the gate cutoff"
    P, consts = build_proj(gd)
    assert P.shape[1] == len(gd) and np.isfinite(P).all(), "non-finite / misaligned projections"
    res = {"build_info": dict(info), "n_formulas": int(P.shape[0]), "thresholds": list(gb.THRESHOLDS),
           "n_strategies": int(P.shape[0] * len(gb.THRESHOLDS)), "min_n": min_n}
    g0, s0 = attach(gd, strikes, ["discovery"])
    bt = gb.main_strike_table(gd, s0)
    res["games"] = {"discovery": int(len(gd)), "with_main_strike": int(bt.has_strike.sum()),
                    "valid_settled": int(bt.valid.sum())}
    ba, v = gb.bet_arrays(bt)
    Pv = P[:, v]
    st = gb.strategy_stats(Pv, ba)
    fin = gb.finalize_stats(st)
    nondef = gb.nondefault_counts() if P.shape[0] == gl.N_FORMULAS else np.zeros(P.shape[0], int)
    win = gb.pick_winner(fin["lb"], fin["n"], nondef, min_n=min_n)
    df = strategy_table(fin, nondef, min_n=min_n)
    res["eligible_strategies"] = int(df.eligible.sum())
    res["strategies_with_positive_mean_eligible"] = int(((df["mean"] > 0) & df.eligible).sum())
    res["strategies_with_positive_lb_eligible"] = int(((df.lb > 0) & df.eligible).sum())
    top = df[df.eligible].sort_values(["lb", "nondefault", "T", "flat"], ascending=[False, True, True, True]).head(50)
    res["top50"] = top.to_dict("records")
    res["by_dimension"] = _dim_summary(df)
    if win is None:
        res["winner"] = None
        res["verdict"] = "no eligible strategy (n >= %d)" % min_n
    else:
        ti, fl = win
        spec = gl.spec_from_flat(fl)
        T = gb.THRESHOLDS[ti]
        wrow = df[(df.flat == fl) & (df["T"] == T)].iloc[0].to_dict()
        res["winner"] = {"flat": int(fl), "T": T, "names": {k: str(x) for k, x in gl.spec_names(spec).items()},
                         "stats": {k: (float(wrow[k]) if k not in ("n", "wins") else int(wrow[k]))
                                   for k in ("n", "wins", "win_rate", "avg_paid", "mean", "sd", "lb", "total")}}
        # ---- luck check
        lk = gb.luck_check(Pv, ba, np.random.default_rng(gb.SEED), n_strat=n_luck, min_n=min_n)
        lf = gb.luck_full(Pv, ba, np.random.default_rng(gb.SEED + 1), min_n=min_n)
        wlb = float(wrow["lb"])
        res["luck_check_2000"] = {"drawn": lk["drawn"], "eligible": lk["eligible"], "lb": gb.dist_summary(lk["lb"]),
                                  "mean_net": gb.dist_summary(lk["mean"]), "n_bets": gb.dist_summary(lk["n"]),
                                  "max_lb": float(lk["lb"].max()) if lk["eligible"] else None,
                                  "share_with_lb_ge_winner": float((lk["lb"] >= wlb).mean()) if lk["eligible"] else None}
        res["luck_check_full_info_only"] = {"description": "one random-side strategy per real (formula, threshold) subset",
                                            "lb": gb.dist_summary(lf), "max_lb": float(lf.max()) if len(lf) else None,
                                            "share_with_lb_ge_winner": float((lf >= wlb).mean()) if len(lf) else None}
        ls = gb.luck_shared(Pv, ba, np.random.default_rng(gb.SEED), reps=200, min_n=min_n)
        res["luck_shared_flip_200"] = {"max_lb": gb.dist_summary(ls), "median": float(np.median(ls)),
                                       "q95": float(np.quantile(ls, 0.95)), "max": float(ls.max()),
                                       "share_reps_max_ge_winner_lb": float((ls >= wlb).mean())}
        # ---- winner diagnostics (information only)
        proj_by_event = dict(zip(gd.event.values, P[fl]))
        g1 = g0.assign(proj=g0.event.map(proj_by_event)).dropna(subset=["actual"]).copy()
        sigma = float((g1.actual - g1.proj).std(ddof=1))
        s1 = pr.add_model_p(s0[s0.event.isin(g1.event)], g1, sigma)
        acc = pr.accuracy(g1, s1)
        q = s1.dropna(subset=["mid", "y"])
        rt = lib.residual_test(q.mid, q.p, q.y, q.event, n_boot=n_boot)
        res["winner_diagnostics"] = {"sigma": sigma, "accuracy": acc,
                                     "residual_test": {"coef": rt[0], "ci95": [rt[1], rt[2]]}}
        frozen = {"spec": list(spec), "flat": int(fl), "T": T, "names": res["winner"]["names"],
                  "discovery_stats": res["winner"]["stats"], "constants_pair": consts.pair(spec[0], spec[2]),
                  "sigma": sigma, "preregistration": "reports/output/nba_totals_grid/PREREGISTRATION.md"}
        if write:
            frozen_path.write_text(json.dumps(frozen, indent=1))
            import hashlib
            res["winner_frozen_sha256"] = hashlib.sha256(frozen_path.read_bytes()).hexdigest()
            print("winner_frozen.json SHA-256:", res["winner_frozen_sha256"], flush=True)
    res["runtime_s"] = time.time() - t0
    if write:
        df.to_csv(out_dir / "all_strategies.csv", index=False)
        top.to_csv(out_dir / "leaderboard_top50.csv", index=False)
        (out_dir / "discovery_results.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def discovery():
    games, strikes, info = v2.load_games()
    w = load_world(GATE_CUTOFF)                       # gate stage: box scores before 2026-01-23 ET only
    consts = fit_world_constants(w)
    print_constants(consts, w.ff_coef, w.ff_r2)

    def build(gd):
        t = time.time()
        P = gl.build_projections(w.season, gd, consts)
        print(f"projections {P.shape} in {time.time() - t:.1f}s", flush=True)
        return P, consts
    out = core_discovery(games, strikes, info, build, v2.attach_outcomes, HERE)
    print(json.dumps({k: v for k, v in out.items() if k not in ("top50", "by_dimension")}, indent=1, default=str))
    print("TOP 10 by LB:")
    for r in out["top50"][:10]:
        print({k: (round(x, 4) if isinstance(x, float) else x) for k, x in r.items()})


# ------------------------------------------------------------------ final
def load_frozen(path):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: `final` runs only the winner frozen by `discovery`.")
    return json.loads(path.read_text())


def core_final(games, strikes, make_world, attach, frozen_path, result_path, write=True, out_dir=None):
    """Evaluates exactly the frozen winner on the unseen half.  make_world() -> (season, consts) with full-season
    walk-forward ratings; called only after the frozen file is found."""
    fz = load_frozen(frozen_path)                                  # refuses here, before anything is loaded
    result_path = Path(result_path)
    if result_path.exists():
        raise RuntimeError(f"{result_path} exists: the unseen half is tested once only.")
    spec, T = tuple(fz["spec"]), fz["T"]
    assert gl.flat_from_spec(spec) == fz["flat"], "frozen spec and flat index disagree"
    assert {k: str(x) for k, x in gl.spec_names(spec).items()} == fz["names"], "frozen spec and names disagree"
    season, consts = make_world()
    pair = consts.pair(spec[0], spec[2])
    for key in ("c_b2b", "b"):
        assert abs(pair[key] - fz["constants_pair"][key]) < 1e-9, f"constant {key} differs from the frozen one"
    assert np.allclose(pair["e_cat"], fz["constants_pair"]["e_cat"], atol=1e-9)
    gu, su = attach(games, strikes, ["unseen"])
    assert (gu.split == "unseen").all()
    proj = gl.project_one(season, gu, consts, spec)
    assert np.isfinite(proj).all() and len(proj) == len(gu)
    bt = gb.main_strike_table(gu, su)
    bets = gb.bets_for(proj, bt, T)
    weeks = pr.span_weeks(gu)
    coin = gb.coin_flip(bets, bt, n=500, seed=gb.SEED) if len(bets) else {"frac_copies_ge_actual": 1.0}
    bar, stats = gb.pass_bar(bets, weeks, coin)
    out = {"winner": fz["names"], "T": T, "flat": fz["flat"], "discovery_stats": fz["discovery_stats"],
           "unseen_games": int(len(gu)), "with_main_strike": int(bt.has_strike.sum()), "valid_settled": int(bt.valid.sum()),
           "bets": stats, "win_rate": float(bets.win.mean()) if len(bets) else None,
           "avg_paid": float(bets.paid.mean()) if len(bets) else None,
           "over_under": {k: {"n": int(len(x)), "mean_net": float(x.net.mean())} for k, x in bets.groupby("side")},
           "coin_flip": coin, "pass_bar": bar, "PASS": bool(all(bar.values())),
           "usd_per_week_definition": "sum of net per contract x 10 / weeks spanned by the unseen games' T"}
    if write:
        bets.to_csv(Path(out_dir or result_path.parent) / "final_bets.csv", index=False)
        result_path.write_text(json.dumps(out, indent=1, default=str))
    return out


def final():
    load_frozen(FROZEN)                                           # refuse before loading anything
    games, strikes, info = v2.load_games()

    def make():
        w = load_world(None)                                      # full-season walk-forward ratings
        return w.season, fit_world_constants(w)
    out = core_final(games, strikes, make, v2.attach_outcomes, FROZEN, FINAL_RESULT)
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    {"smoke": smoke, "discovery": discovery, "final": final}[sys.argv[1]]()
