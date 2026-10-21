"""Research log #36 runner.  Stages:
  smoke  : build features for ~20 discovery games, fit NB on discovery rows (box counts only, no prices), print rows and model p next
           to the T-1h mid for 10 markets.  Computes NO Brier / gate / P&L / win rate.
  gates  : the three /model-test gates on DISCOVERY (two runs: mid 15-85c, all prices) -> results.json["gates"]
  bets   : (only if both gate runs pass) the 4 betting versions, split, coin flip, bootstrap, pass bar -> results.json, bets.csv
Run: .venv/Scripts/python.exe reports/output/nba_props/run.py smoke|gates|bets
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analysis as A  # noqa: E402
import build as B  # noqa: E402
import data as D  # noqa: E402
import features as F  # noqa: E402
import nbmodel as NB  # noqa: E402

RESULTS = HERE / "results.json"
THETAS = (0.03, 0.06)


def load_all(game_keys=None, progress=False):
    box = D.Box()
    store = D.ReportStore()
    markets = D.load_markets()
    games_tip = {(m["game_date"], m["game_code"]): m["tip"] for m in markets if m["tip"] is not None}
    disc, uns = D.split_games({(t, k) for k, t in games_tip.items()})
    rows, mrows, counts, games, unm = B.build(markets, box, store, game_keys, progress=progress)
    store.save()
    return {"box": box, "markets": markets, "games_tip": games_tip, "disc": disc, "uns": uns, "rows": rows, "mrows": mrows,
            "counts": counts, "games": games, "unmatched": unm}


def fit_models(rows, disc):
    """One NB per stat, fitted on discovery games only; one dispersion per stat.  Rows with an actual count only."""
    models = {}
    for stat in D.STATS:
        idx = [i for i, r in enumerate(rows) if r["stat"] == stat and r["gk"] in disc and r["y"] is not None]
        X = np.array([rows[i]["vec"] for i in idx])
        y = np.array([rows[i]["y"] for i in idx], dtype=float)
        m = NB.fit_nb(X, y)
        m["n_fit"] = len(idx)
        models[stat] = m
    return models


def attach_probs(rows, mrows, models):
    """model mean / P(stat >= strike) for every included market record (excl None and a row)."""
    by_stat = defaultdict(list)
    for r in mrows:
        if r["excl"] is None and r["row"] is not None:
            by_stat[r["stat"]].append(r)
    for stat, rs in by_stat.items():
        X = np.array([rows[r["row"]]["vec"] for r in rs])
        n = np.array([r["strike"] for r in rs])
        mu = NB.predict_mu(models[stat], X)
        p = NB.nb_sf(mu, models[stat]["alpha"], n)
        for r, m_, p_ in zip(rs, mu, p):
            r["mu"], r["p"] = float(m_), float(p_)


def usable(mrows):
    """market records with a model probability and a valid T-1h quote."""
    return [r for r in mrows if r["excl"] is None and r["row"] is not None and r["quote_excl"] is None and "p" in r]


# ------------------------------------------------------------------ smoke
def smoke(n_games=20):
    S = load_all()
    models = fit_models(S["rows"], S["disc"])
    attach_probs(S["rows"], S["mrows"], models)
    print("NB fits (discovery rows):", {s: {"n": m["n_fit"], "alpha": round(m["alpha"], 4)} for s, m in models.items()})
    print("coefficients (standardised X) per stat; order intercept +", F.FEATURE_NAMES)
    for s, m in models.items():
        print(" ", s, np.round(m["beta"], 3).tolist())
    first = sorted(((t, k) for k, t in S["games_tip"].items() if k in S["disc"]))[:n_games]
    keys = {k for _, k in first}
    print("\nSmoke games:", [k for _, k in first][:5], "...", len(keys))
    sub = [r for r in S["mrows"] if r["gk"] in keys and r["excl"] is None and r["row"] is not None]
    print("\nExample feature rows:")
    seen = set()
    shown = 0
    for r in sub:
        if r["row"] in seen:
            continue
        seen.add(r["row"])
        row = S["rows"][r["row"]]
        print(" ", row["gk"], row["name"], row["team"], row["stat"], {k: round(v, 3) if isinstance(v, float) else v
                                                                      for k, v in row["info"].items()})
        shown += 1
        if shown >= 5:
            break
    print("\nModel p vs T-1h mid (10 markets, discovery smoke games):")
    ok = [r for r in sub if r["quote_excl"] is None and "p" in r]
    for r in ok[:: max(len(ok) // 10, 1)][:10]:
        print(f"  {r['ticker']}  strike {r['strike']}  model_mu {r['mu']:.2f}  p_model {r['p']:.3f}  bid {r['bid']:.2f} ask {r['ask']:.2f} "
              f"mid {(r['bid'] + r['ask']) / 2:.3f}  quote age {r['age_min']:.0f} min")
    cov = Counter()
    for g in S["games"].values():
        cov["games"] += 1
        cov["has_report"] += g["has_report"]
        cov["covered"] += g["covered"]
    print("\ncounts", S["counts"])
    print("injury coverage (all games):", dict(cov))


# ------------------------------------------------------------------ gates
def gate_run(recs, games_tip_period, price_filter):
    """recs: usable discovery records.  price_filter: mid in [0.15, 0.85] if True."""
    sel = [r for r in recs if (not price_filter) or (0.15 <= (r["bid"] + r["ask"]) / 2 <= 0.85)]
    p = np.array([r["p"] for r in sel])
    bid = np.array([r["bid"] for r in sel])
    ask = np.array([r["ask"] for r in sel])
    mid = (bid + ask) / 2
    y = np.array([1.0 if r["result"] == "yes" else 0.0 for r in sel])
    games = [r["gk"] for r in sel]
    tips = [r["tip"] for r in sel]
    out = {"n_markets": len(sel), "price_filter": "mid 15-85c" if price_filter else "all prices"}
    out["brier_model"], out["brier_mid"] = A.brier(p, y), A.brier(mid, y)
    out["logloss_model"], out["logloss_mid"] = A.logloss(p, y), A.logloss(mid, y)
    blend = 1 / (1 + np.exp(-(A.logit(p) + A.logit(mid)) / 2))
    out["brier_blend"], out["logloss_blend"] = A.brier(blend, y), A.logloss(blend, y)
    out["gate1_pass"] = bool(out["brier_model"] <= out["brier_mid"] + 0.002)
    out["residual"] = A.residual_test(p, mid, y, games)
    out["gate2_pass"] = bool(out["residual"]["disagreement"] > 0 and out["residual"]["ci_lo"] > 0)
    out["gate3"] = A.gate3(p, bid, ask, tips, list(games_tip_period))
    out["gate3_pass"] = bool(out["gate3"]["per_week"] >= 20)
    out["all_pass"] = bool(out["gate1_pass"] and out["gate2_pass"] and out["gate3_pass"])
    out["calibration_model"] = A.calib_table(p, y)
    out["calibration_mid"] = A.calib_table(mid, y)
    return out


def gates():
    S = load_all(progress=True)
    models = fit_models(S["rows"], S["disc"])
    attach_probs(S["rows"], S["mrows"], models)
    recs = [r for r in usable(S["mrows"]) if r["gk"] in S["disc"]]
    tips = [S["games_tip"][k] for k in S["disc"]]
    res = {"gates": {"15-85c": gate_run(recs, tips, True), "all": gate_run(recs, tips, False)},
           "counts": S["counts"], "models": {s: {"alpha": m["alpha"], "n_fit": m["n_fit"], "beta": m["beta"].tolist()}
                                             for s, m in models.items()}}
    RESULTS.write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if not kk.startswith("calib")} for k, v in res["gates"].items()},
                     indent=1, default=str))
    return res


# ------------------------------------------------------------------ bets
def make_bets(recs, theta, price_filter):
    bets = []
    for r in recs:
        d = A.decide(r["p"], r["bid"], r["ask"], theta, price_filter=price_filter)
        if d is None:
            continue
        pnl = A.net_per_contract(d["side"], d["price"], d["fee"], r["result"])
        bets.append({**d, "ticker": r["ticker"], "gk": r["gk"], "tip": r["tip"], "stat": r["stat"], "strike": r["strike"],
                     "p": r["p"], "bid": r["bid"], "ask": r["ask"], "mu": r["mu"], "result": r["result"], "pnl": pnl,
                     "won": pnl + d["price"] + d["fee"] > 0.5, "tm_out": r["tm_out"]})
    bets.sort(key=lambda b: (b["tip"], b["ticker"]))
    return bets


def version_report(recs_d, recs_u, disc_tips, uns_tips, theta, price_filter):
    bd, bu = make_bets(recs_d, theta, price_filter), make_bets(recs_u, theta, price_filter)
    out = {"theta": theta, "price_filter": "15-85c" if price_filter else "all"}
    out["discovery"] = A.summarize_bets(bd, disc_tips)
    out["unseen"] = A.summarize_bets(bu, uns_tips)
    if bu:
        lo, hi = A.bootstrap_mean([b["pnl"] for b in bu], [b["gk"] for b in bu])
        out["unseen"].update({"boot_lo": lo, "boot_hi": hi})
        cf = A.coin_flip([b["bid"] for b in bu], [b["ask"] for b in bu], [b["result"] for b in bu], out["unseen"]["mean"],
                         price_filter=price_filter)
        out["coin_flip"] = cf
    return out, bd, bu


def breakdowns(bets):
    def agg(key):
        d = defaultdict(list)
        for b in bets:
            d[key(b)].append(b["pnl"])
        return {str(k): {"n": len(v), "mean": float(np.mean(v))} for k, v in sorted(d.items(), key=lambda kv: str(kv[0]))}
    dist = lambda b: ("<=-2" if b["strike"] - b["mu"] <= -2 else "-2..0" if b["strike"] - b["mu"] <= 0 else  # noqa: E731
                      "0..2" if b["strike"] - b["mu"] <= 2 else "2..5" if b["strike"] - b["mu"] <= 5 else ">5")
    price = lambda b: ("<15c" if b["price"] < 0.15 else "15-30c" if b["price"] < 0.30 else "30-50c" if b["price"] < 0.50  # noqa: E731
                       else "50-70c" if b["price"] < 0.70 else "70-85c" if b["price"] <= 0.85 else ">85c")
    return {"by_stat": agg(lambda b: b["stat"]), "by_strike_distance": agg(dist), "by_price": agg(price),
            "by_teammates_out": agg(lambda b: "tm_out>0" if b["tm_out"] > 0 else "tm_out=0"), "by_side": agg(lambda b: b["side"])}


def bets_stage():
    prior = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    g = prior.get("gates")
    if not g or not all(v["all_pass"] for v in g.values()):
        print("Gates missing or not all passed: NO betting rule is run (pre-registration).")
        return
    S = load_all(progress=True)
    models = fit_models(S["rows"], S["disc"])
    attach_probs(S["rows"], S["mrows"], models)
    for r in S["mrows"]:
        if "p" in r:
            r["tm_out"] = S["rows"][r["row"]]["info"]["tm_out"]
    ok = usable(S["mrows"])
    recs_d = [r for r in ok if r["gk"] in S["disc"]]
    recs_u = [r for r in ok if r["gk"] in S["uns"]]
    disc_tips = [S["games_tip"][k] for k in S["disc"]]
    uns_tips = [S["games_tip"][k] for k in S["uns"]]
    versions, allbets = {}, []
    for theta in THETAS:
        for pf in (True, False):
            rep, bd, bu = version_report(recs_d, recs_u, disc_tips, uns_tips, theta, pf)
            name = f"theta{int(round(theta * 100))}c_{'15-85c' if pf else 'all'}"
            rep["breakdowns_unseen"] = breakdowns(bu)
            rep["breakdowns_discovery"] = breakdowns(bd)
            versions[name] = rep
            for per, bl in (("discovery", bd), ("unseen", bu)):
                for b in bl:
                    allbets.append({"version": name, "period": per, **{k: b[k] for k in (
                        "ticker", "stat", "strike", "side", "price", "fee", "edge", "p", "bid", "ask", "result", "pnl")}})
    prim = versions["theta3c_15-85c"]
    # unseen Brier (model vs mid) on the primary price range
    sel = [r for r in recs_u if 0.15 <= (r["bid"] + r["ask"]) / 2 <= 0.85]
    yb = np.array([1.0 if r["result"] == "yes" else 0.0 for r in sel])
    pb = np.array([r["p"] for r in sel])
    mb = np.array([(r["bid"] + r["ask"]) / 2 for r in sel])
    bm, bk = A.brier(pb, yb), A.brier(mb, yb)
    u, d = prim["unseen"], prim["discovery"]
    bar = {
        "1_mean_pos_both": bool(d["mean"] is not None and u["mean"] is not None and d["mean"] > 0 and u["mean"] > 0),
        "2_boot_lo_pos": bool(u.get("boot_lo") is not None and u["boot_lo"] > 0),
        "3_n_unseen_ge_200": bool(u["n"] >= 200),
        "4_coin_flip_le_5pct": bool(prim.get("coin_flip", {}).get("share", 1.0) <= 0.05),
        "5_usd_week_ge_2": bool(u["usd_per_week"] >= 2.0),
        "6_unseen_brier_ok": bool(bm <= bk + 0.002),
    }
    prior.update({"versions": versions, "pass_bar_primary": bar, "pass": bool(all(bar.values())),
                  "unseen_brier_15_85": {"model": bm, "mid": bk, "n": len(sel)}})
    RESULTS.write_text(json.dumps(prior, indent=1, default=str))
    with open(HERE / "bets.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(allbets[0].keys()) if allbets else ["version"])
        w.writeheader()
        w.writerows(allbets)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if not kk.startswith("breakdowns")} for k, v in versions.items()},
                     indent=1, default=str))
    print("pass bar:", bar, "PASS" if prior["pass"] else "FAIL")


if __name__ == "__main__":
    st = sys.argv[1] if len(sys.argv) > 1 else ""
    {"smoke": smoke, "gates": gates, "bets": bets_stage}.get(st, lambda: print(__doc__))()
