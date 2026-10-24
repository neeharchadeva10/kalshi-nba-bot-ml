"""Stage B of research log #30: backtest of the 6 pre-registered versions + reports. Run from repo root after
build_rows.py:  python reports/output/weather_nbm/analyze.py   -> results.json (+ bets_*.csv.gz)."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SPLIT = "2025-07-01"
SEED = 20261021
N_BOOT = 10_000
N_COIN = 500
THETAS = {"3c": 300, "5c": 500, "8c": 800}
CLIP = 1e-3
PRIMARY = ("morning", "5c")
PRICE_BUCKETS = [(100, 1000, "1-10c"), (1000, 3000, "10-30c"), (3000, 7000, "30-70c"), (7000, 9000, "70-90c"),
                 (9000, 9900, "90-99c")]


def order_fee_e4(price):
    price = np.asarray(price, dtype=np.int64)
    num = 7 * 10 * price * (10_000 - price)
    return -((-num) // 1_000_000)            # ceil, integer e4 dollars per 10-contract order


def season(day):
    m = int(day[5:7])
    return {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM", 6: "JJA", 7: "JJA", 8: "JJA"}.get(m, "SON")


def bucket(price):
    for lo, hi, lab in PRICE_BUCKETS:
        if lo <= price <= hi and (price > lo or lo == 100):
            return lab
    return "other"


def prep(df):
    df = df.copy()
    df["period"] = np.where(df["day"] < SPLIT, "discovery", "unseen")
    v = df["valid"] == 1
    df["bid"] = df["bid_e4"].where(v)
    df["ask"] = df["ask_e4"].where(v)
    yes = (df["result"] == "yes").astype(int)
    df["yes"] = yes
    ask = df["ask"].fillna(5000).astype(np.int64).to_numpy()
    nop = 10_000 - df["bid"].fillna(5000).astype(np.int64).to_numpy()
    pe = df["p"].to_numpy() * 1e4
    fy, fn = order_fee_e4(ask), order_fee_e4(nop)
    df["edge_y"] = pe - ask - fy / 10.0
    df["edge_n"] = (1e4 - pe) - nop - fn / 10.0
    # net of a 10-contract order (e4 dollars) if bought YES / NO
    df["net_y"] = 10 * (yes.to_numpy() * 10_000 - ask) - fy
    df["net_n"] = 10 * ((1 - yes.to_numpy()) * 10_000 - nop) - fn
    df["price_y"] = ask
    df["price_n"] = nop
    df["cluster"] = df["station"] + "|" + df["day"]
    return df


def make_bets(df, theta_e4):
    v = df[df["valid"] == 1]
    ey, en = v["edge_y"].to_numpy(), v["edge_n"].to_numpy()
    take_y = (ey >= theta_e4) & (ey >= en)
    take_n = (en >= theta_e4) & (en > ey)
    b = v[take_y | take_n].copy()
    side_y = take_y[(take_y | take_n)]
    b["side"] = np.where(side_y, "YES", "NO")
    b["price"] = np.where(side_y, b["price_y"], b["price_n"])
    b["net"] = np.where(side_y, b["net_y"], b["net_n"])          # e4 dollars per 10 contracts
    b["pbucket"] = [bucket(x) for x in b["price"]]
    b["season"] = [season(d) for d in b["day"]]
    return b


def boot_ratio(S, N, seed=SEED, n_boot=N_BOOT, chunk=500):
    D = len(S)
    rng = np.random.default_rng(seed)
    out = np.empty(n_boot)
    done = 0
    p = np.full(D, 1.0 / D)
    while done < n_boot:
        k = min(chunk, n_boot - done)
        W = rng.multinomial(D, p, size=k).astype(np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[done:done + k] = (W @ S) / (W @ N)
        done += k
    return out


def cluster_arrays(df_valid, bets):
    """per station-day sums over all eligible (valid-quote) station-days of the period."""
    cl = pd.Index(df_valid["cluster"].unique())
    S = bets.groupby("cluster")["net"].sum().reindex(cl).fillna(0.0).to_numpy() / 1e4
    N = 10.0 * bets.groupby("cluster").size().reindex(cl).fillna(0).to_numpy()
    return S, N, cl


def coin_flip(vp, bets, actual_total, n_copies=N_COIN, seed=SEED):
    """vp: valid-quote rows of the period; bets: model bets. Each copy: same station-days, same bet count per
    station-day, random distinct markets among the valid ones, random side, same price rules and fees."""
    cnt = bets.groupby("cluster").size()
    if cnt.empty:
        return dict(share=None, n_copies=n_copies, copies_total_mean=None)
    sub = vp[vp["cluster"].isin(cnt.index)].copy()
    sub = sub.sort_values("cluster", kind="stable")
    cl_codes, uniq = pd.factorize(sub["cluster"])
    need = cnt.reindex(uniq).to_numpy()
    starts = np.r_[0, np.flatnonzero(np.diff(cl_codes)) + 1]
    n = len(sub)
    rng = np.random.default_rng(seed)
    ny, nn = sub["net_y"].to_numpy(), sub["net_n"].to_numpy()
    totals = np.empty(n_copies)
    size = np.diff(np.r_[starts, n])
    for c in range(n_copies):
        key = rng.random(n)
        # rank within cluster: sort by (cluster, key)
        order = np.lexsort((key, cl_codes))
        rank = np.empty(n, dtype=np.int64)
        rank[order] = np.arange(n) - np.repeat(starts, size)
        take = rank < need[cl_codes]
        side = rng.random(n) < 0.5
        totals[c] = np.where(side, ny, nn)[take].sum()
    share = float(np.mean(totals >= actual_total))
    return dict(share=share, n_copies=n_copies, copies_total_mean_dollars=float(totals.mean() / 1e4),
                copies_total_p95_dollars=float(np.percentile(totals, 95) / 1e4))


def groups(b):
    out = {}
    for name in ("station", "tz", "season", "nbm_version", "side", "pbucket"):
        g = b.groupby(name)["net"].agg(["size", "sum"])
        out[name] = {str(k): dict(bets=int(r["size"]), net_dollars=round(r["sum"] / 1e4, 2),
                                  mean_net_per_contract=round(r["sum"] / 1e4 / (10 * r["size"]), 4))
                     for k, r in g.iterrows()}
    return out


def weeks_stat(b):
    if b.empty:
        return dict(weeks_with_bets=0, dollars_per_week=None)
    wk = [date.fromisoformat(d).isocalendar()[:2] for d in b["day"]]
    nw = len(set(wk))
    tot = b["net"].sum() / 1e4
    return dict(weeks_with_bets=nw, dollars_per_week=round(tot / nw, 3))


def scores(df):
    v = df[(df["valid"] == 1)]
    y = v["yes"].to_numpy()
    pm = v["p"].to_numpy()
    mid = ((v["bid"] + v["ask"]) / 2e4).to_numpy()

    def ll(p):
        p = np.clip(p, CLIP, 1 - CLIP)
        return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    return dict(n=int(len(v)), brier_model=float(np.mean((pm - y) ** 2)), brier_market=float(np.mean((mid - y) ** 2)),
                logloss_model=ll(pm), logloss_market=ll(mid),
                disagree_ge_5c=float(np.mean(np.abs(pm - mid) >= 0.05)),
                disagree_ge_10c=float(np.mean(np.abs(pm - mid) >= 0.10)))


def calib_table(df):
    v = df[df["valid"] == 1]
    edges = [0, .02, .05, .1, .2, .3, .5, .7, .8, .9, .95, .98, 1.0001]
    out = []
    mid = (v["bid"] + v["ask"]) / 2e4
    for name, p in (("model", v["p"]), ("market_mid", mid)):
        c = pd.cut(p, edges, right=False)
        g = pd.DataFrame(dict(p=p, y=v["yes"], c=c)).groupby("c", observed=True).agg(n=("y", "size"), mean_p=("p", "mean"),
                                                                                  freq=("y", "mean"))
        out.append({name: [dict(bucket=str(k), n=int(r.n), mean_p=round(r.mean_p, 4), freq_yes=round(r.freq, 4))
                           for k, r in g.iterrows()]})
    return out


def main():
    res = dict(preregistration="reports/output/weather_nbm/PREREGISTRATION.md", split=SPLIT, seed=SEED,
               primary=dict(decision=PRIMARY[0], theta=PRIMARY[1]), versions={}, quality={}, calibration={}, nbm={})
    brier = {}
    for typ in ("morning", "evening"):
        df = prep(pd.read_csv(HERE / f"rows_{typ}.csv.gz"))
        brier[typ] = {}
        for per in ("discovery", "unseen"):
            d = df[df["period"] == per]
            brier[typ][per] = scores(d)
            res["calibration"][f"{typ}_{per}"] = calib_table(d)
        res["quality"][typ] = dict(brier[typ], all_periods=scores(df))
        vp_all = df[df["valid"] == 1]
        for tname, th in THETAS.items():
            bets_all = make_bets(df, th)
            ver = dict(decision=typ, theta=tname)
            for per in ("discovery", "unseen"):
                vp = vp_all[vp_all["period"] == per]
                b = bets_all[bets_all["period"] == per]
                S, N, cl = cluster_arrays(vp, b)
                n_bets = int(len(b))
                mean_pc = float(b["net"].sum() / 1e4 / (10 * n_bets)) if n_bets else None
                bs = boot_ratio(S, N) if n_bets else np.array([np.nan])
                ci = dict(lo95_two_sided=float(np.nanpercentile(bs, 2.5)), hi95=float(np.nanpercentile(bs, 97.5)),
                          lo_5pct_one_sided=float(np.nanpercentile(bs, 5))) if n_bets else None
                cf = coin_flip(vp, b, b["net"].sum()) if n_bets else None
                ver[per] = dict(station_days_eligible=int(len(cl)), station_days_with_bets=int(b["cluster"].nunique()),
                                bets=n_bets, yes_bets=int((b["side"] == "YES").sum()), no_bets=int((b["side"] == "NO").sum()),
                                mean_net_per_contract=mean_pc, total_net_dollars=float(b["net"].sum() / 1e4),
                                win_rate=float(((b["net"] > 0).mean())) if n_bets else None,
                                bootstrap=ci, coin_flip=cf, **weeks_stat(b), breakdown=groups(b) if n_bets else {})
            res["versions"][f"{typ}_{tname}"] = ver
            if (typ, tname) == PRIMARY:
                bets_all.drop(columns=["cluster"]).to_csv(HERE / "bets_primary.csv.gz", index=False)
        # NBM bias / sigma summary (per event, one row per station-day)
        ev = df.drop_duplicates(["station", "day"]).copy()
        ev["year"] = ev["day"].str[:4]
        e2 = ev[ev["y_obs"].notna()].copy()
        e2["err"] = e2["y_obs"] - e2["txn"]
        nb = {}
        for key, g in e2.groupby(["station", "year"]):
            nb[f"{key[0]}|{key[1]}"] = dict(n=int(len(g)), mean_obs_minus_txn=round(g["err"].mean(), 2),
                                            sd_obs_minus_txn=round(g["err"].std(), 2) if len(g) > 1 else None,
                                            mean_xnd=round(g["xnd"].mean(), 2), mean_b_cal=round(g["b_cal"].mean(), 2),
                                            mean_k_cal=round(g["k_cal"].mean(), 2))
        allst = {s: dict(n=int(len(g)), mean_obs_minus_txn=round(g["err"].mean(), 2), sd=round(g["err"].std(), 2),
                         mean_xnd=round(g["xnd"].mean(), 2)) for s, g in e2.groupby("station")}
        res["nbm"][typ] = dict(by_station=allst, by_station_year=nb,
                               pooled=dict(n=int(len(e2)), mean_err=round(e2["err"].mean(), 3), sd_err=round(e2["err"].std(), 3)),
                               staleness_quote_age_hours_median=float(df.loc[df["valid"] == 1, "qage_s"].median() / 3600))
    # pass bar
    for k, v in res["versions"].items():
        u, d = v["unseen"], v["discovery"]
        typ = v["decision"]
        r = {}
        r["1_mean_net_pos_both"] = bool(d["mean_net_per_contract"] is not None and u["mean_net_per_contract"] is not None
                                       and d["mean_net_per_contract"] > 0 and u["mean_net_per_contract"] > 0)
        r["2_unseen_ci_lower_gt_0"] = bool(u["bootstrap"] and u["bootstrap"]["lo95_two_sided"] > 0)
        r["3_unseen_bets_ge_200"] = bool(u["bets"] >= 200)
        r["4_coin_flip_share_le_5pct"] = bool(u["coin_flip"] and u["coin_flip"]["share"] is not None and u["coin_flip"]["share"] <= 0.05)
        r["5_dollars_per_week_ge_2"] = bool(u["dollars_per_week"] is not None and u["dollars_per_week"] >= 2.0)
        r1_5 = all(r.values())
        bm, bk = brier[typ]["unseen"]["brier_model"], brier[typ]["unseen"]["brier_market"]
        r["6_brier_model_le_market_or_within_0.002_while_1to5_pass"] = bool(bm <= bk or (bm <= bk + 0.002 and r1_5))
        v["pass_bar"] = r
        v["passes_all"] = bool(all(r.values()))
    (HERE / "results.json").write_text(json.dumps(res, indent=1))
    pk = f"{PRIMARY[0]}_{PRIMARY[1]}"
    print("PRIMARY", pk, res["versions"][pk]["pass_bar"], "PASS" if res["versions"][pk]["passes_all"] else "FAIL")
    for k, v in res["versions"].items():
        print(k, "disc", v["discovery"]["bets"], v["discovery"]["mean_net_per_contract"], "| unseen", v["unseen"]["bets"],
              v["unseen"]["mean_net_per_contract"], v["unseen"]["bootstrap"], v["unseen"]["coin_flip"], v["unseen"]["dollars_per_week"])


if __name__ == "__main__":
    main()
