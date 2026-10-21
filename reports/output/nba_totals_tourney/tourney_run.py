"""Research log #39 runner.  Usage (repo root, .venv python):  tourney_run.py smoke | run
  smoke  NO outcomes of 2025-26: 5 discovery games, all 30 projections next to the T-1h market median; R3 convergence
         stats; the fitted rest c values (2024-25 outcomes only); asserts 30 x discovery projections are finite.
  run    discovery leaderboard of 30 versions -> winner (lowest strike Brier) -> gates on the winner only. Only if ALL
         pass is the unseen split loaded; then sized betting + pass bar + coin flip.
#37/#38 code is imported (not modified); totals.sqlite is opened read-only (via #38's shim)."""
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
import tourney as tn  # noqa: E402
import lib  # noqa: E402
import prep  # noqa: E402


def _load_v2_run():
    spec = importlib.util.spec_from_file_location("run_v2_tourney", tn.V2_DIR / "run.py")   # by path: name clash
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


v2 = _load_v2_run()          # also installs #38's read-only sqlite shim on prep / #37 run
pr = v2.pr
THETAS, PRIMARY = pr.THETAS, pr.PRIMARY
GATE_CUTOFF = v2.GATE_CUTOFF


# ------------------------------------------------------------------ sized betting
def run_bets_sized(g, s, theta):
    """Copy of #37's run_bets (why: needs a contract count). Edge uses the fee at count 10 (lib.select_bet); the
    count is size_contracts(edge, theta); net per contract uses the fee recomputed at that count."""
    rows = []
    for ev, gg in s.dropna(subset=["mid"]).groupby("event"):
        strikes = [{"id": r.ticker, "strike": r.strike, "bid": r.bid, "ask": r.ask, "p": r.p} for r in gg.itertuples()]
        b = lib.select_bet(strikes, theta)
        if b is None:
            continue
        y = gg[gg.ticker == b["id"]].y.iloc[0]
        if np.isnan(y):
            continue
        n = tn.size_contracts(b["edge"], theta)
        net = lib.settle(b["side"], b["price"], bool(y), n)
        rows.append({"event": ev, "theta": theta, **b, "yes_result": int(y), "count": n, "net": net,
                     "dollars": n * net})
    return pd.DataFrame(rows)


def coin_flip_sized(s, bets, n=500, seed=lib.SEED):
    """Same games, same contract count per bet; random side on a random strike with mid 15-85c; same fees; hold.
    Compares TOTAL dollars."""
    rng = np.random.default_rng(seed)
    pool = {}
    for ev, gg in s.dropna(subset=["mid", "y"]).groupby("event"):
        gg = gg[(gg.mid >= 0.15 - 1e-12) & (gg.mid <= 0.85 + 1e-12)]
        if len(gg):
            pool[ev] = gg
    bb = bets[bets.event.isin(pool)]
    evs, cnts = list(bb.event), list(bb["count"])
    tots = []
    for _ in range(n):
        tot = 0.0
        for e, c in zip(evs, cnts):
            gg = pool[e]
            r = gg.iloc[int(rng.integers(len(gg)))]
            if rng.integers(2) == 0:
                tot += c * lib.settle("over", r.ask, bool(r.y), int(c))
            else:
                tot += c * lib.settle("under", 1 - r.bid, bool(r.y), int(c))
        tots.append(tot)
    tots = np.array(tots)
    actual = float(bb.dollars.sum())
    return {"n_games": len(evs), "actual_total_usd": actual, "copies_mean_usd": float(tots.mean()),
            "frac_copies_ge_actual": float((tots >= actual).mean())}


def bet_report_sized(bets, g):
    if bets.empty:
        return {"n": 0}
    lo, hi = tn.wmean_boot_ci(bets.dollars.values, bets["count"].values)
    wk = pr.span_weeks(g)
    return {"n": int(len(bets)), "contracts": int(bets["count"].sum()),
            "win_rate": float(((bets.side == "over") == (bets.yes_result == 1)).mean()),
            "mean_net_per_contract": float(bets.dollars.sum() / bets["count"].sum()), "ci95": [lo, hi],
            "total_net_usd": float(bets.dollars.sum()), "weeks": wk, "usd_per_week": float(bets.dollars.sum() / wk),
            "count_distribution": {int(k): int(v) for k, v in bets["count"].value_counts().sort_index().items()},
            "over_under": {k: {"n": int(len(v)), "mean_net_per_contract": float(v.dollars.sum() / v["count"].sum())}
                           for k, v in bets.groupby("side")}}


DEPTH_CAVEAT = ("Fills of up to 10 contracts are assumed at the T-1h candle bid/ask; no order-book depth data exists, "
                "so size-10 fills may be optimistic.")


def near_dup_stats(P):
    """sd and max |difference| of discovery projections (S0 columns): F4-F1, F5-F2 per rating type, and R2-R1, R3-R1
    for F1. From projections only; no outcomes."""
    out = {}
    for r in tn.RTYPES:
        out[f"F4-F1_{r}"] = (P[f"F4_{r}_S0"] - P[f"F1_{r}_S0"])
        out[f"F5-F2_{r}"] = (P[f"F5_{r}_S0"] - P[f"F2_{r}_S0"])
    for r in ("R2", "R3"):
        out[f"F1_{r}-R1"] = (P[f"F1_{r}_S0"] - P["F1_R1_S0"])
    return {k: {"sd": float(v.std(ddof=1)), "max_abs": float(v.abs().max())} for k, v in out.items()}


def r3_cap_hits(engine):
    """[(label, rounds, last max change)] for adjustments that reached the 100-round cap."""
    return [(x[0], int(x[1]), float(x[2])) for x in engine.r3_rounds if x[1] >= tn.R3_MAX]


def cent_fee_sensitivity(bets):
    """INFO ONLY: per-order fee rounded up to $0.01 (instead of $0.000001), same bets and counts."""
    import math
    if bets.empty:
        return {"n": 0}
    fee = [math.ceil(0.07 * r.count * r.price * (1 - r.price) / 0.01 - 1e-9) * 0.01 for r in bets.itertuples()]
    win = np.where(bets.side == "over", bets.yes_result == 1, bets.yes_result == 0)
    net = bets["count"].values * (win.astype(float) - bets.price.values) - np.array(fee)
    return {"total_net_usd": float(net.sum()), "mean_net_per_contract": float(net.sum() / bets["count"].sum()),
            "vs_micro_total_net_usd": float(bets.dollars.sum())}


# ------------------------------------------------------------------ the run (injectable for tests)
def core_run(games, strikes, info, proj_disc, proj_full=None, attach=None, n_boot=10000, out_dir=None, write=True,
             fast=tn.residual_test_fast):
    """proj_disc(games_df) -> DataFrame with the 30 version columns (gate-stage ratings, box scores < 2026-01-23).
    proj_full() -> callable like proj_disc built from the full box scores; called ONLY after every gate passes.
    attach(games, strikes, which) -> (g, s) with outcomes; the unseen split is passed only after every gate passes."""
    attach = attach or v2.attach_outcomes
    out = {"build_info": dict(info), "preregistered_versions": 32, "versions_grid": tn.VERSIONS}
    t0 = time.time()
    games_d = games[games.split == "discovery"].copy()
    gd0, sd0 = attach(games_d, strikes, ["discovery"])
    out["discovery_missing_actual"] = int(gd0.actual.isna().sum())
    gd0 = gd0.dropna(subset=["actual"]).copy()
    maxD = max(v2.r2.decision_date(r.tip, r.T) for r in gd0.itertuples())
    assert maxD < np.datetime64(GATE_CUTOFF, "D"), f"discovery decision date {maxD} not before the gate cutoff"
    P = proj_disc(gd0)
    assert list(P.columns) == tn.VERSIONS and np.isfinite(P.values).all(), "non-finite / missing projections"
    board, keep = {}, {}
    for v in tn.VERSIONS:
        gdv = gd0.assign(proj=P[v].values)
        sigma = float((gdv.actual - gdv.proj).std(ddof=1))
        sdv = pr.add_model_p(sd0[sd0.event.isin(gdv.event)], gdv, sigma)
        acc = pr.accuracy(gdv, sdv)
        q = sdv.dropna(subset=["mid", "y"])
        rt = fast(q.mid, q.p, q.y, q.event, n_boot=n_boot)
        gm = gdv.dropna(subset=["median"])
        board[v] = {"sigma": sigma, "mae": acc["model_mae"], "market_mae": acc["market_mae"],
                    "brier": acc["model_brier"], "market_brier": acc["market_brier"],
                    "bias_actual_minus_proj": float((gdv.actual - gdv.proj).mean()),
                    "resid_coef": rt[0], "resid_ci95": [rt[1], rt[2]],
                    "side_won_by_gap": tn.side_won_table(gm.proj, gm["median"], gm.actual)}
        keep[v] = (gdv, sdv, q, sigma, acc, rt)
        print(f"  [{time.time() - t0:7.1f}s] {v}: mae {acc['model_mae']:.3f} brier {acc['model_brier']:.4f} "
              f"coef {rt[0]:+.3f} [{rt[1]:+.3f},{rt[2]:+.3f}]", flush=True)
    out["leaderboard_discovery"] = board
    out["near_duplicate_structure"] = near_dup_stats(P)
    out["depth_caveat"] = DEPTH_CAVEAT
    win = tn.pick_winner({v: board[v]["brier"] for v in tn.VERSIONS})
    out["winner"] = win
    gdv, sdv, q, sigma, acc, rt = keep[win]
    cnt, per_wk = pr.gate3_count(q, pr.span_weeks(gdv))
    gate1 = bool(acc["model_mae"] <= acc["market_mae"] + 0.5 and acc["model_brier"] <= acc["market_brier"] + 0.002)
    gate2 = lib.gate2_pass(*rt)
    gate3 = bool(per_wk >= 20)
    out["sigma"] = sigma
    out["gates"] = {"accuracy": acc, "gate1_pass": gate1,
                    "residual_test": {"coef": rt[0], "ci95": [rt[1], rt[2]], "gate2_pass": bool(gate2)},
                    "volume": {"strikes_over_cost": cnt, "per_week": per_wk, "gate3_pass": gate3}}
    allp = all([gate1, bool(gate2), gate3])
    out["all_gates_pass"] = bool(allp)
    games_out, bets_out = gdv, []
    if not allp:
        out["verdict"] = "winner failed a gate on discovery; no betting rule run, unseen split never loaded"
    else:
        pf_u = proj_full()                                  # full-season ratings only now
        gu, su = attach(games, strikes, ["unseen"])
        out["unseen_missing_actual"] = int(gu.actual.isna().sum())
        gu = gu.dropna(subset=["actual"]).copy()
        gu["proj"] = pf_u(gu)[win].values
        assert np.isfinite(gu.proj.values).all()
        su = pr.add_model_p(su[su.event.isin(gu.event)], gu, sigma)
        accu = pr.accuracy(gu, su)
        out["unseen_accuracy"] = accu
        out["versions"] = {}
        for th in THETAS:
            bd, bu = run_bets_sized(gdv, sdv, th), run_bets_sized(gu, su, th)
            v = {"discovery": bet_report_sized(bd, gdv), "unseen": bet_report_sized(bu, gu)}
            if len(bu):
                v["coin_flip_unseen"] = coin_flip_sized(su, bu, n=500, seed=lib.SEED)
            v["cent_rounding_fee_sensitivity_info_only"] = {"discovery": cent_fee_sensitivity(bd),
                                                            "unseen": cent_fee_sensitivity(bu)}
            out["versions"][f"theta_{th}"] = v
            bets_out += [bd.assign(split="discovery"), bu.assign(split="unseen")]
        pv = out["versions"][f"theta_{PRIMARY}"]
        u, d_ = pv["unseen"], pv["discovery"]
        bar = {
            "1_mean_net_pos_both": bool(d_.get("n") and u.get("n") and d_["mean_net_per_contract"] > 0
                                        and u["mean_net_per_contract"] > 0),
            "2_unseen_boot_lo_gt0": bool(u.get("n") and u["ci95"][0] > 0),
            "3_unseen_bets_ge_100": bool(u.get("n", 0) >= 100),
            "4_coin_flip_le_5pct": bool(pv.get("coin_flip_unseen", {}).get("frac_copies_ge_actual", 1) <= 0.05),
            "5_usd_per_week_ge_2": bool(u.get("n") and u["usd_per_week"] >= 2),
            "6_unseen_accuracy": bool(accu["model_mae"] <= accu["market_mae"] + 0.5 and
                                      accu["model_brier"] <= accu["market_brier"] + 0.002)}
        out["pass_bar_primary"] = bar
        out["PASS"] = bool(all(bar.values()))
        games_out = pd.concat([gdv, gu])
    out["runtime_s"] = time.time() - t0
    if write:
        d = Path(out_dir or HERE)
        games_out.drop(columns=["hoopr_date"], errors="ignore").to_csv(d / "games.csv", index=False)
        (pd.concat(bets_out) if bets_out else pd.DataFrame()).to_csv(d / "bets.csv", index=False)
        (d / "results.json").write_text(json.dumps(out, indent=1, default=str))
    return out


def _make_proj(engine, c):
    return lambda g: engine.project_table(g, c)


def full_run():
    games, strikes, info = v2.load_games()
    eng, eng24, games24 = tn.load_engines(prep.BOX, before=GATE_CUTOFF)
    c, cinfo = tn.fit_all_c(eng24, games24)
    for (f, r), (cv, a, n, nb) in cinfo.items():
        print(f"fit[{f},{r}] a = {a:+.4f}  c = {cv:+.4f}  (n_games {n}, n_b2b_team_games {nb})")

    def full():
        e, _, _ = tn.load_engines(prep.BOX)                  # no cutoff; c stays the 2024-25 fit
        return _make_proj(e, c)
    out = core_run(games, strikes, info, _make_proj(eng, c), proj_full=full)
    out["rest_fit_a_c_n"] = {f"{f}_{r}": {"a_not_applied": v[1], "c": v[0], "n_games": v[2], "n_b2b": v[3]}
                             for (f, r), v in cinfo.items()}
    out["r3_cap_hits_2025_26_gate_stage"] = r3_cap_hits(eng)
    out["r3_cap_hits_2024_25_fit"] = r3_cap_hits(eng24)
    (HERE / "results.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: v for k, v in out.items() if k != "leaderboard_discovery"}, indent=1, default=str))


# ------------------------------------------------------------------ smoke (no 2025-26 outcomes)
def smoke():
    t0 = time.time()
    games, strikes, info = v2.load_games()
    d = games[games.split == "discovery"]
    eng, eng24, games24 = tn.load_engines(prep.BOX, before=GATE_CUTOFF)
    print("discovery games:", len(d), " build info games_by_split:", info["games_by_split"])
    print("=== fitted rest c  (actual - projection = a + c x n_b2b; OLS with intercept; only c applied; 2024-25 regular season) ===")
    c, cinfo = tn.fit_all_c(eng24, games24)
    for (f, r), (cv, a, n, nb) in cinfo.items():
        print(f"  [{f},{r}] a = {a:+.4f} (not applied)  c = {cv:+.4f}   n_games={n}  n_b2b_team_games={nb}")
    t1 = time.time()
    P = eng.project_table(d, c)
    print(f"projection table {P.shape} in {time.time() - t1:.1f}s")
    assert list(P.columns) == tn.VERSIONS
    assert P.shape == (len(d), 30) and np.isfinite(P.values).all(), "non-finite discovery projections"
    print(f"ASSERT OK: all {P.shape[1]} x {P.shape[0]} = {P.size} discovery projections finite")
    print("=== 5 discovery games: 30 projections next to the T-1h market median (NO outcomes) ===")
    for i in [0, 1, 2, 3, 4, 250, 450, 650]:    # first 5 (season start: n small) + 3 later games
        r = d.iloc[i]
        D = tn.r2.decision_date(r["tip"], r["T"])
        print(f"{r.event} D={D} market_median={r['median']:.2f} nb2b={int(eng.project_raw(r.away, r.home, D)[1])}")
        for f in tn.FORMULAS:
            cells = "  ".join(f"{rt}/{s}={P.iloc[i][f'{f}_{rt}_{s}']:.1f}" for rt in tn.RTYPES for s in tn.RESTS)
            print(f"   {f}: {cells}")
    print("=== R3 convergence rounds (all adjustments run: 2025-26 gate-stage snapshots + prior; 2024-25 fit) ===")
    for name, e in (("2025-26 engine", eng), ("2024-25 fit engine", eng24)):
        rr = np.array([x[1] for x in e.r3_rounds])
        print(f"  {name}: adjustments {len(rr)} rounds min {rr.min()} median {np.median(rr):.0f} "
              f"max {rr.max()} hit-cap(100) {int((rr >= 100).sum())}  prior rounds {e.r3_rounds[0][1]}")
    print("R3 cap-hit dates (label, rounds, last max change):")
    print("  2025-26 gate-stage:", r3_cap_hits(eng))
    print("  2024-25 fit:", r3_cap_hits(eng24))
    print("=== near-duplicate structure, discovery projections S0 (sd, max|diff|) ===")
    for k, v in near_dup_stats(P).items():
        print(f"  {k}: sd {v['sd']:.3f} max {v['max_abs']:.3f}")
    print(f"smoke total {time.time() - t0:.1f}s")


if __name__ == "__main__":
    {"smoke": smoke, "run": full_run}[sys.argv[1]]()
