"""EXPLORATORY follow-up to research logs #37-#39. DISCOVERY HALF ONLY. INFORMATION, NOT A TEST.
Models: A = #37 original, B = F1_R1_S0 (#38), C = F4_R2_S1 (#39 winner). Reuses audited code (imports only).
The unseen split is never attached; asserted below."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import tourney as tn  # noqa: E402
import tourney_run as trun  # noqa: E402
import lib  # noqa: E402
import prep  # noqa: E402

v2, pr = trun.v2, trun.pr
TAG = "[EXPLORATORY - discovery half only - information, not a test]"
BUCKETS_GAP = tn.BUCKETS + ((0, float("inf")),)
BUCKETS_STRIKE = ((0, 3), (3, 6), (6, 9), (9, 12), (12, float("inf")))
THETAS = (0.03, 0.06)


def md(df, floatfmt="{:.4f}"):
    df = df.copy()
    cols = [df.index.name or ""] + list(df.columns)
    rows = [[str(i)] + [floatfmt.format(x) if isinstance(x, (float, np.floating)) else str(x) for x in r]
            for i, r in zip(df.index, df.values.tolist())]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def side_won(gm, proj_col="proj"):
    rows = {}
    for lo, hi in BUCKETS_GAP:
        d = (gm[proj_col] - gm["median"]).abs()
        prod = (gm[proj_col] - gm["median"]) * (gm.actual - gm["median"])
        m = (d >= lo) & (d < hi) & (prod != 0)
        rows["all" if hi == float("inf") and lo == 0 else f"[{lo},{hi})"] = {
            "n": int(m.sum()), "win_rate": float((prod[m] > 0).mean()) if m.any() else np.nan}
    return pd.DataFrame(rows).T


def bets_generic(s, theta, mode, lo=0.15, hi=0.85):
    rows = []
    for ev, gg in s.dropna(subset=["mid"]).groupby("event"):
        strikes = [{"id": r.ticker, "strike": r.strike, "bid": r.bid, "ask": r.ask, "p": r.p} for r in gg.itertuples()]
        b = lib.select_bet(strikes, theta, lo=lo, hi=hi)
        if b is None:
            continue
        y = gg[gg.ticker == b["id"]].y.iloc[0]
        if np.isnan(y):
            continue
        n = 10 if mode == "fixed10" else tn.size_contracts(b["edge"], theta)
        net = lib.settle(b["side"], b["price"], bool(y), n)
        rows.append({"event": ev, **b, "yes_result": int(y), "count": n, "net": net, "dollars": n * net,
                     "won": (b["side"] == "over") == (y == 1)})
    return pd.DataFrame(rows)


def summarize(b, weeks, n_boot=10000):
    if b.empty:
        return {"n": 0}
    lo, hi = tn.wmean_boot_ci(b.dollars.values, b["count"].values, n=n_boot)
    out = {"n": len(b), "contracts": int(b["count"].sum()), "win_rate": float(b.won.mean()),
           "avg_price": float(b.price.mean()), "mean_net_per_contract": float(b.dollars.sum() / b["count"].sum()),
           "total_usd": float(b.dollars.sum()), "usd_per_week": float(b.dollars.sum() / weeks),
           "ci95_lo": lo, "ci95_hi": hi}
    for side in ("over", "under"):
        x = b[b.side == side]
        out[f"{side}_n"] = len(x)
        out[f"{side}_win"] = float(x.won.mean()) if len(x) else np.nan
        out[f"{side}_net_pc"] = float(x.dollars.sum() / x["count"].sum()) if len(x) else np.nan
    return out


def edge_side_table(s, g):
    """Every quoted strike (valid quote): bet the side with the larger edge (any sign), 10 contracts, held."""
    med = g.set_index("event")["median"]
    q = s.dropna(subset=["mid", "y"]).copy()
    q["med"] = q.event.map(med)
    q = q.dropna(subset=["med"])
    ye = [lib.side_edges(r.p, r.bid, r.ask) for r in q.itertuples()]
    q["side"] = ["over" if a[0] >= a[1] else "under" for a in ye]
    q["price"] = [a[2] if a[0] >= a[1] else a[3] for a in ye]
    q["edge"] = [max(a[0], a[1]) for a in ye]
    q["won"] = np.where(q.side == "over", q.y == 1, q.y == 0)
    q["net"] = [lib.settle(r.side, r.price, bool(r.y), 10) for r in q.itertuples()]
    q["dist"] = (q.strike - q.med).abs()
    rows = {}
    for lo, hi in BUCKETS_STRIKE:
        m = (q.dist >= lo) & (q.dist < hi)
        x = q[m]
        rows[f"[{lo},{hi})"] = {"n": len(x), "win_rate": float(x.won.mean()), "avg_price": float(x.price.mean()),
                                "mean_net_per_contract": float(x.net.mean()), "mean_edge_claimed": float(x.edge.mean())}
    return pd.DataFrame(rows).T


def baselines(s, g):
    q = s.dropna(subset=["mid", "y"]).copy()
    q = q.sort_values(["event", "strike"])
    month = g.set_index("event")["month"]
    med = g.set_index("event")["median"]
    pa, pb, pc = [], [], []
    for ev, gg in q.groupby("event"):
        r = gg.iloc[int(np.argmin(np.abs(gg.mid.values - 0.5)))]
        yes = lib.settle("over", r.ask, bool(r.y), 10) * 10
        no = lib.settle("under", 1 - r.bid, bool(r.y), 10) * 10
        pa.append({"event": ev, "price": r.ask, "won": r.y == 1, "usd": yes})
        pb.append({"event": ev, "price": 1 - r.bid, "won": r.y == 0, "usd": no})
        m = med.get(ev)
        if m is not None and not np.isnan(m):
            lo_r = gg.iloc[int(np.argmin(np.abs(gg.strike.values - (m - 6))))]
            hi_r = gg.iloc[int(np.argmin(np.abs(gg.strike.values + 0 - (m + 6))))]
            if hi_r.strike > lo_r.strike:
                yl = lib.settle("over", lo_r.ask, bool(lo_r.y), 10) * 10
                nh = lib.settle("under", 1 - hi_r.bid, bool(hi_r.y), 10) * 10
                wy, wn = lo_r.y == 1, hi_r.y == 0
                pc.append({"event": ev, "usd": yl + nh, "yes_leg": yl, "no_leg": nh, "both": wy and wn,
                           "one": wy != wn, "none": (not wy) and (not wn),
                           "cost": lo_r.ask + 1 - hi_r.bid})
    out = {}
    for name, rows in (("a_YES_nearest_50c", pa), ("b_NO_nearest_50c", pb)):
        d = pd.DataFrame(rows)
        d["month"] = d.event.map(month)
        rr = {"all": d}
        rr.update({k: v for k, v in d.groupby("month")})
        out[name] = pd.DataFrame({k: {"n": len(v), "win_rate": float(v.won.mean()), "avg_price": float(v.price.mean()),
                                      "mean_net_per_contract": float(v.usd.sum() / (10 * len(v))),
                                      "total_usd": float(v.usd.sum())} for k, v in rr.items()}).T
    d = pd.DataFrame(pc)
    d["month"] = d.event.map(month)
    rr = {"all": d}
    rr.update({k: v for k, v in d.groupby("month")})
    out["c_middle_pair"] = pd.DataFrame({k: {
        "pairs": len(v), "mean_pair_usd": float(v.usd.mean()), "total_usd": float(v.usd.sum()),
        "yes_leg_usd": float(v.yes_leg.sum()), "no_leg_usd": float(v.no_leg.sum()),
        "share_total_in_between_(both win)": float(v.both.mean()), "share_one_leg_wins": float(v.one.mean()),
        "share_both_lose": float(v.none.mean()), "avg_pair_cost_per_contract": float(v.cost.mean())}
        for k, v in rr.items()}).T
    return out


def main():
    print(TAG)
    games, strikes, info = v2.load_games()
    disc_events = set(games[games.split == "discovery"].event)
    unseen_events = set(games[games.split != "discovery"].event)
    games_d = games[games.split == "discovery"].copy()
    gd0, sd0 = v2.attach_outcomes(games_d, strikes, ["discovery"])
    gd0 = gd0.dropna(subset=["actual"]).copy()
    sd0 = sd0[sd0.event.isin(gd0.event)].copy()
    assert set(gd0.event) <= disc_events and not (set(gd0.event) & unseen_events)
    assert set(sd0.event) <= disc_events and not (set(sd0.event) & unseen_events)
    assert (gd0.split == "discovery").all()
    print(f"discovery games with outcomes: {len(gd0)}; strikes: {len(sd0)}; unseen events present: 0 (asserted)")
    weeks = pr.span_weeks(gd0)

    eng, eng24, games24 = tn.load_engines(prep.BOX, before=trun.GATE_CUTOFF)
    c, _ = tn.fit_all_c(eng24, games24)
    P = eng.project_table(gd0, c)
    models = {"A (#37 original)": gd0.proj.values, "B (F1_R1_S0, #38)": P["F1_R1_S0"].values,
              "C (F4_R2_S1, #39 winner)": P["F4_R2_S1"].values}
    res = {}
    for name, proj in models.items():
        print(f"\n\n######## MODEL {name} {TAG}")
        gm = gd0.assign(proj=proj)
        assert np.isfinite(gm.proj).all()
        sigma = float((gm.actual - gm.proj).std(ddof=1))
        sm = pr.add_model_p(sd0, gm, sigma)
        acc = pr.accuracy(gm, sm)
        print(f"discovery sigma {sigma:.3f}  MAE(with median) {acc['model_mae']:.3f} vs market {acc['market_mae']:.3f}  "
              f"Brier {acc['model_brier']:.4f} vs market {acc['market_brier']:.4f}")
        r = {"sigma": sigma, "accuracy": acc}
        gmed = gm.dropna(subset=["median"])
        sw = side_won(gmed)
        print(f"\n1. Win rate vs the line (actual on formula's side of market median; exact ties dropped) {TAG}")
        print(md(sw))
        r["side_won"] = sw.to_dict("index")
        print(f"\n2. Registered bet rule, mid 15-85c, edge with fee at count 10 {TAG}  (weeks = {weeks:.2f})")
        rows = {}
        for th in THETAS:
            for mode in ("fixed10", "sized1-10"):
                b = bets_generic(sm, th, "fixed10" if mode == "fixed10" else "sized")
                rows[f"theta {th:.2f} {mode}"] = summarize(b, weeks)
        t2 = pd.DataFrame(rows).T
        print(md(t2[[c_ for c_ in t2.columns if c_ in ("n", "contracts", "win_rate", "avg_price", "mean_net_per_contract",
                                                       "total_usd", "usd_per_week", "ci95_lo", "ci95_hi")]]))
        print("over/under split:")
        print(md(t2[[c_ for c_ in t2.columns if c_.startswith(("over", "under"))]]))
        r["registered_rule"] = t2.to_dict("index")
        print(f"\n3a. Alt lines: any quoted strike (mid 0.01-0.99), theta 3c, fixed 10 {TAG}")
        ba = bets_generic(sm, 0.03, "fixed10", lo=0.01, hi=0.99)
        sa = summarize(ba, weeks)
        print(md(pd.DataFrame({"alt_lines": sa}).T))
        r["alt_lines_theta3_fixed10"] = sa
        print(f"\n3b. Every quoted strike, edge-side bet (larger edge, any sign), 10 contracts, by |strike - median| {TAG}")
        et = edge_side_table(sm, gm)
        print(md(et))
        r["edge_side_by_strike_distance"] = et.to_dict("index")
        res[name] = r
    print(f"\n\n######## 4. Market-only baselines (fixed 10 contracts, taker, held; no model) {TAG}")
    bl = baselines(sd0, gd0)
    for k, v in bl.items():
        print(f"\n({k})")
        print(md(v))
    res["baselines"] = {k: v.to_dict("index") for k, v in bl.items()}
    (HERE / "explore_results.json").write_text(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
