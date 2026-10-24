"""Research log #37 runner.  Usage (from repo root, .venv python):
  run.py smoke   ratings sanity table + first 10 discovery games: projection next to the T-1h market median. NO outcomes.
  run.py run     gates on discovery; ONLY if all pass: betting rule on discovery + unseen, pass bar, coin flip.
Outputs: results.json, games.csv, bets.csv (run mode).  Outcomes are attached only to the games a stage may see."""
from __future__ import annotations

import json
import sqlite3
import sys

import numpy as np
import pandas as pd

import lib
import prep

HERE = prep.HERE
THETAS = (0.03, 0.06)
PRIMARY = 0.03


# ------------------------------------------------------------------ smoke
def smoke():
    rat = prep.ratings_tables()
    print("=== ratings sanity: (ORtg, DRtg, pace) from regular-season team_box ===")
    print("user example: BOS 1.159/1.085/98.5   DET 1.124/1.054/103.1")
    for season, rt in rat.items():
        for t in ("BOS", "DET"):
            o, d, p = rt[t]
            print(f"  {season} {t}: ORtg {o:.3f}  DRtg {d:.3f}  pace {p:.1f}")
    games, strikes, info = prep.build_games()
    print("=== build info ===")
    for k, v in info.items():
        print(" ", k, (v if not isinstance(v, list) else f"{len(v)} {v[:8]}"))
    disc = games[games.split == "discovery"].head(10)
    print("=== first 10 discovery games (no outcomes) ===")
    print(disc[["event", "T", "T_src", "proj", "median", "n_markets", "n_quoted"]].to_string())
    return games, strikes, info


# ------------------------------------------------------------------ outcomes (attached only when allowed)
def attach_outcomes(games, strikes, which):
    """which: list of split names allowed. Returns (games, strikes) restricted to those splits, with outcomes."""
    g = games[games.split.isin(which)].copy()
    s = strikes[strikes.event.isin(g.event)].copy()
    conn = sqlite3.connect(prep.TOT)
    res = pd.read_sql("SELECT ticker,result,expiration_value,status FROM markets", conn)
    s = s.merge(res, on="ticker", how="left")
    s["y"] = s.result.map({"yes": 1.0, "no": 0.0})            # scalar/None -> NaN (excluded)
    s.loc[s.status != "finalized", "y"] = np.nan               # unsettled excluded
    if g.season.eq("2025-26").any():
        h = prep.hoopr_games(2026).set_index("game_id")["total"]
        g["actual"] = g.hoopr_id.map(h)
    else:
        g["actual"] = np.nan
    s["exp_num"] = s.expiration_value.map(lib.parse_expiration_value)
    ev = s[s.status == "finalized"].dropna(subset=["exp_num"]).groupby("event").exp_num.first()
    g["kalshi_total"] = g.event.map(ev).astype(float)
    g["actual"] = g.actual.fillna(g.kalshi_total)               # forward games: Kalshi settlement value
    return g, s



# ------------------------------------------------------------------ metrics
def add_model_p(s, g, sigma):
    mu = g.set_index("event").proj
    s = s.copy()
    s["mu"] = s.event.map(mu)
    s["p"] = [lib.p_over(m, sigma, k, t) for m, k, t in zip(s.mu, s.strike, s.strike_type)]
    return s


def accuracy(g, s):
    gm = g.dropna(subset=["median", "actual"])
    q = s.dropna(subset=["mid", "y"])
    return {"games": int(len(g)), "games_with_median": int(len(gm)),
            "model_mae": float((gm.proj - gm.actual).abs().mean()) if len(gm) else None,
            "market_mae": float((gm["median"] - gm.actual).abs().mean()) if len(gm) else None,
            "model_mae_all": float((g.proj - g.actual).abs().mean()),
            "strikes_quoted": int(len(q)),
            "model_brier": float(((q.p - q.y) ** 2).mean()) if len(q) else None,
            "market_brier": float(((q.mid - q.y) ** 2).mean()) if len(q) else None}


def by_group(g, s, col):
    out = {}
    for k, gg in g.groupby(col):
        out[str(k)] = {**accuracy(gg, s[s.event.isin(gg.event)]),
                       "bias_actual_minus_proj": float((gg.actual - gg.proj).mean())}
    return out


def calibration(q, col):
    b = pd.cut(q[col], np.linspace(0, 1, 11), include_lowest=True)
    t = q.groupby(b, observed=True).agg(n=("y", "size"), mean_pred=(col, "mean"), hit_rate=("y", "mean"))
    return {str(i): {k: float(v) for k, v in r.items()} for i, r in t.iterrows()}


def gate3_count(q, weeks):
    gap = (q.p - q.mid).abs()
    thr = (q.ask - q.bid) / 2 + q.mid.map(lib.fee_pc)
    return int((gap > thr).sum()), float((gap > thr).sum() / weeks)


def median_sensitivity(g, s):
    """INFO ONLY: market median from strikes with spread <= 10c (the gate uses the registered median)."""
    meds = {}
    for ev, gg in s.dropna(subset=["mid"]).groupby("event"):
        gg = gg[(gg.ask - gg.bid) <= 0.10 + 1e-12]
        meds[ev] = lib.market_median(list(zip(gg.strike, gg.mid)))
    x = g.assign(m10=g.event.map(meds)).dropna(subset=["m10", "actual"])
    return {"games": int(len(x)), "market_mae_spread_le_10c": float((x.m10 - x.actual).abs().mean()) if len(x) else None,
            "model_mae_same_games": float((x.proj - x.actual).abs().mean()) if len(x) else None}


def span_weeks(g):
    return max((g["T"].max() - g["T"].min()) / (7 * 86400), 1e-9)


# ------------------------------------------------------------------ betting
def run_bets(g, s, theta):
    rows = []
    for ev, gg in s.dropna(subset=["mid"]).groupby("event"):
        strikes = [{"id": r.ticker, "strike": r.strike, "bid": r.bid, "ask": r.ask, "p": r.p}
                   for r in gg.itertuples()]
        b = lib.select_bet(strikes, theta)
        if b is None:
            continue
        y = gg[gg.ticker == b["id"]].y.iloc[0]
        if np.isnan(y):
            continue
        cv = gg[gg.ticker == b["id"]].cvol.iloc[0]
        rows.append({"event": ev, "theta": theta, **b, "yes_result": int(y), "candle_volume": cv,
                     "net": lib.settle(b["side"], b["price"], bool(y))})
    return pd.DataFrame(rows)


def coin_flip(g, s, bets, n=500, seed=lib.SEED):
    """Same games, same count; random strike priced 15-85c (mid) and random side; same fees, hold."""
    rng = np.random.default_rng(seed)
    pool = {}
    for ev, gg in s.dropna(subset=["mid", "y"]).groupby("event"):
        gg = gg[(gg.mid >= 0.15 - 1e-12) & (gg.mid <= 0.85 + 1e-12)]
        if len(gg):
            pool[ev] = gg
    evs = [e for e in bets.event if e in pool]
    means = []
    for _ in range(n):
        tot = 0.0
        for e in evs:
            gg = pool[e]
            r = gg.iloc[int(rng.integers(len(gg)))]
            if rng.integers(2) == 0:
                tot += lib.settle("over", r.ask, bool(r.y))
            else:
                tot += lib.settle("under", 1 - r.bid, bool(r.y))
        means.append(tot / len(evs))
    means = np.array(means)
    actual = float(bets.net.mean())
    return {"n_games": len(evs), "actual_mean": actual, "copies_mean": float(means.mean()),
            "frac_copies_ge_actual": float((means >= actual).mean())}


def bet_report(bets, g):
    if bets.empty:
        return {"n": 0}
    lo, hi = lib.boot_mean_ci(bets.net.values)
    wk = span_weeks(g)
    return {"n": int(len(bets)), "win_rate": float(((bets.side == "over") == (bets.yes_result == 1)).mean()),
            "mean_net_per_contract": float(bets.net.mean()), "ci95": [lo, hi],
            "total_net_usd_at_10": float(bets.net.sum() * lib.COUNT), "weeks": wk,
            "usd_per_week_at_10": float(bets.net.sum() * lib.COUNT / wk),
            "over_under": {k: {"n": int(len(v)), "mean_net": float(v.net.mean())}
                           for k, v in bets.groupby("side")}}


# ------------------------------------------------------------------ main run
def check():
    """Ladder-consistency (amendment 2.4): (total > strike) == (result == yes) on every finalized strike.
    Prints ONLY mismatch counts per split and the mismatching event tickers."""
    games, strikes, info = prep.build_games()
    gm = games[games.event.str.contains("26JAN24GSWMIN")]
    print("26JAN24GSWMIN ->", gm[["event", "hoopr_date", "T_src"]].to_dict("records"))
    for sp in ("discovery", "unseen", "forward"):
        g, s = attach_outcomes(games, strikes, [sp])
        tot = g.set_index("event").actual
        s = s.dropna(subset=["y"]).copy()
        s["tot"] = s.event.map(tot)
        nan_tot = int(s.tot.isna().sum())
        s = s.dropna(subset=["tot"])
        bad = s[(s.tot > s.strike) != (s.y == 1.0)]
        print(f"{sp}: finalized strikes checked {len(s)}, no-total strikes {nan_tot}, "
              f"mismatches {len(bad)} in events {sorted(bad.event.unique())}")


def full_run():
    games, strikes, info = prep.build_games()
    out = {"build_info": {k: v for k, v in info.items()}, "preregistered_versions": 3}
    # ---- discovery: sigma from discovery games only
    gd, sd = attach_outcomes(games, strikes, ["discovery"])
    miss = int(gd.actual.isna().sum())
    out["discovery_missing_actual"] = miss
    gd = gd.dropna(subset=["actual"])
    sigma = float((gd.actual - gd.proj).std(ddof=1))
    out["sigma"] = sigma
    out["discovery_mean_error_actual_minus_proj"] = float((gd.actual - gd.proj).mean())
    sd = add_model_p(sd[sd.event.isin(gd.event)], gd, sigma)
    gd = gd.copy()
    acc = accuracy(gd, sd)
    q = sd.dropna(subset=["mid", "y"])
    rt = lib.residual_test(q.mid, q.p, q.y, q.event)
    cnt, per_wk = gate3_count(q, span_weeks(gd))
    # info only: games with >=1 strike priced 15-85c (mid), per week; median sensitivity using spread <= 10c quotes
    qual = q[(q.mid >= 0.15 - 1e-12) & (q.mid <= 0.85 + 1e-12)].event.nunique()
    out["gates"] = {"qualifying_games_15_85": int(qual), "qualifying_games_per_week": qual / span_weeks(gd)}
    out["median_sensitivity_spread_le_10c_discovery"] = median_sensitivity(gd, sd)
    gate1 = (acc["model_mae"] <= acc["market_mae"] + 0.5 or acc["model_mae"] <= acc["market_mae"]) and \
            (acc["model_brier"] <= acc["market_brier"] + 0.002)
    gate2 = lib.gate2_pass(*rt)
    gate3 = per_wk >= 20
    out["gates"].update({"accuracy": acc, "gate1_pass": bool(gate1),
                    "residual_test": {"coef": rt[0], "ci95": [rt[1], rt[2]], "gate2_pass": bool(gate2)},
                    "volume": {"strikes_over_cost": cnt, "per_week": per_wk, "gate3_pass": bool(gate3)}})
    # ---- reported regardless (discovery)
    lm, lp = lib.logit(q.mid), lib.logit(q.p)
    blend = 1 / (1 + np.exp(-(lm + lp) / 2))
    out["blend_brier_discovery"] = float(((blend - q.y) ** 2).mean())
    out["discovery_by_month"] = by_group(gd, sd, "month")
    out["discovery_by_stage"] = by_group(gd, sd, "stage")
    out["discovery_calibration_model"] = calibration(q, "p")
    out["discovery_calibration_market"] = calibration(q, "mid")
    out["spread_stats_discovery"] = {"median_spread": float((q.ask - q.bid).median()),
                                     "share_spread_ge_20c": float(((q.ask - q.bid) >= 0.2).mean())}
    # kalshi vs hoopr cross-check (discovery)
    x = gd.dropna(subset=["kalshi_total"])
    out["total_crosscheck_discovery"] = {"n": int(len(x)), "mismatch": int((x.kalshi_total != x.actual).sum())}
    allp = all([gate1, gate2, gate3])
    out["all_gates_pass"] = bool(allp)
    games_out = gd
    bets_out = []
    if not allp:
        out["verdict"] = "the formula is not better than Kalshi's line (gate failed); no betting rule run, unseen untouched"
    else:
        gu, su = attach_outcomes(games, strikes, ["unseen"])
        gu = gu.dropna(subset=["actual"])
        su = add_model_p(su[su.event.isin(gu.event)], gu, sigma)
        accu = accuracy(gu, su)
        out["unseen_accuracy"] = accu
        out["unseen_by_month"] = by_group(gu, su, "month")
        out["unseen_by_stage"] = by_group(gu, su, "stage")
        qu = su.dropna(subset=["mid", "y"])
        out["unseen_calibration_model"] = calibration(qu, "p")
        out["unseen_calibration_market"] = calibration(qu, "mid")
        out["total_crosscheck_unseen"] = {"n": int(gu.kalshi_total.notna().sum()),
                                          "mismatch": int((gu.kalshi_total.notna() & (gu.kalshi_total != gu.actual)).sum())}
        out["versions"] = {}
        for th in THETAS:
            bd, bu = run_bets(gd, sd, th), run_bets(gu, su, th)
            v = {"discovery": bet_report(bd, gd), "unseen": bet_report(bu, gu)}
            if len(bu):
                v["coin_flip_unseen"] = coin_flip(gu, su, bu)
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
    # ---- forward look 2026-27 (not part of the split; reported regardless)
    gf, sf = attach_outcomes(games, strikes, ["forward"])
    gf = gf.dropna(subset=["actual"])
    if len(gf):
        sf = add_model_p(sf[sf.event.isin(gf.event)], gf, sigma)
        gf = gf.assign(stage=np.where(gf.date < "2026-10-17", "preseason", "regular"))
        fwd = {**accuracy(gf, sf), "bias_actual_minus_proj": float((gf.actual - gf.proj).mean()),
               "by_stage": by_group(gf, sf, "stage")}
        if allp:
            fwd["bets"] = {f"theta_{th}": bet_report(run_bets(gf, sf, th), gf) for th in THETAS}
        out["forward_look_2026_27"] = fwd
    else:
        out["forward_look_2026_27"] = {"games": 0}
    games_out.drop(columns=["hoopr_date"], errors="ignore").to_csv(HERE / "games.csv", index=False)
    (pd.concat(bets_out) if bets_out else pd.DataFrame()).to_csv(HERE / "bets.csv", index=False)
    (HERE / "results.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    {"smoke": smoke, "run": full_run, "check": check}[sys.argv[1]]()
