"""H1 (pre-registered in reports/output/mechanical_edges/PREREGISTRATION.md): final-minute settlement averaging on
Kalshi BTC 15-minute markets.

Settlement is the simple average of the 60 one-second BRTI prints before the close, so during the last minute part of
the outcome is already fixed. If the market under-reacts, the favorite is underpriced in the final seconds.
Data : every trade print in the final 90 s of each KXBTC15M market (public trades endpoint), aggregated per market,
       second left, price and taker side (table trades_final).
Fill : a print where the taker bought the favorite (the side priced above 50c) is a fill we could have taken at that
       price. One observation per market and bucket: the volume-weighted price of those prints.
Edge : win - price - exact Kalshi taker fee 0.07*p*(1-p), per contract.
Pass : every period positive; Bonferroni 99.75% day-clustered CI lower bound > 0 on later+fresh; >= 300 markets and a
       median printed size >= 10 contracts; the 60-90 s control bucket's mean is below half the test bucket's.
"""
from __future__ import annotations

import json
import logging
import math
import random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import numpy as np
import pandas as pd

from kpm.analysis.crypto15m import split_of
from kpm.analysis.strategies import cluster_bootstrap_ci
from kpm.clients.kalshi import KalshiClient
from kpm.collectors.common import done_ids
from kpm.core import audit, config, db
from kpm.core.config import settings
from kpm.core.fees import FeeModel
from kpm.core.prices import parse_dollars
from kpm.core.timeutil import parse_iso

log = logging.getLogger(__name__)
COMPONENT = "analysis.crypto_final_minute"
WINDOW_S = 90
SEC_EDGES = [-1, 5, 15, 30, 60, 90]                     # (lo, hi] seconds left; 0 joins the last bucket
SEC_LABELS = ["0-5s", "5-15s", "15-30s", "30-60s", "60-90s (control)"]
PRICE_EDGES = [0.80, 0.90, 0.95, 0.98, 0.995, 1.0001]   # [lo, hi) favorite price
PRICE_LABELS = ["80-90c", "90-95c", "95-98c", "98-99.5c", "99.5-100c"]
CONTROL = "60-90s (control)"
N_TESTS = 20                                            # 4 test time buckets x 5 price buckets
ALPHA = 0.05 / N_TESTS                                  # Bonferroni: 99.75% intervals


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def collect(workers: int | None = None) -> None:
    """Download and aggregate the final-90-second prints of every market in the study window (resumable)."""
    cfg = settings()
    c = cfg["crypto"]
    lo, hi = parse_iso(c["start"]), parse_iso(c["fresh_end"])
    with db.session() as conn:
        client = KalshiClient(conn)
        cut = parse_iso(client.cutoff()["trades_created_ts"])
        rows = conn.execute("SELECT market_id, contract_key, close_ts FROM markets WHERE platform='kalshi' AND series_key=? "
                            "AND close_ts >= ? AND close_ts < ? AND settlement_value IS NOT NULL",
                            (c["series"], lo, hi)).fetchall()
        done = done_ids(conn, COMPONENT, "trades")
        todo = [(r["market_id"], r["contract_key"], int(r["close_ts"])) for r in rows if r["contract_key"] not in done]
        random.Random(7).shuffle(todo)   # a partial download is still a uniform sample across periods
        log.info("final-minute trades: %d of %d markets to fetch", len(todo), len(rows))

        def get(job):
            mid, tk, close = job
            agg: dict[tuple, list] = defaultdict(lambda: [0.0, 0])
            for t in client.trades(tk, close - WINDOW_S, close, close < cut):
                sec = max(0, math.ceil(close - _ts(t["created_time"])))
                key = (sec, parse_dollars(t["yes_price_dollars"]), t.get("taker_side") or "")
                agg[key][0] += float(parse_dollars(t.get("count_fp")) or t.get("count") or 0)
                agg[key][1] += 1
            return job, agg

        with ThreadPoolExecutor(max_workers=workers or cfg["kalshi"].get("workers", 6)) as pool:
            futs = [pool.submit(get, j) for j in todo]
            for i, f in enumerate(as_completed(futs), 1):
                (mid, tk, close), agg = f.result()
                db.insert_many(conn, "trades_final", [
                    {"market_id": mid, "sec_left": k[0], "yes_price": k[1], "taker_side": k[2], "count": v[0],
                     "n_trades": v[1]} for k, v in agg.items() if k[0] <= WINDOW_S])
                audit.log(conn, COMPONENT, "trades", "market", tk, {"rows": len(agg)})
                client.http.flush_index(conn)
                conn.commit()
                if i % 250 == 0 or i == len(todo):
                    log.info("final-minute trades %d/%d (fetches=%d retries=%d)", i, len(todo),
                             client.http.stats["fetches"], client.http.stats["retries"])


def observations(tf: pd.DataFrame, mk: pd.DataFrame, fee: FeeModel) -> pd.DataFrame:
    """One row per market x seconds bucket x price bucket: VWAP of prints where the taker bought the favorite."""
    d = tf[tf["yes_price"] != 0.5].copy()
    d["fav_yes"] = d["yes_price"] > 0.5
    d["price"] = np.where(d["fav_yes"], d["yes_price"], 1.0 - d["yes_price"])
    took_fav = np.where(d["fav_yes"], d["taker_side"] == "yes", d["taker_side"] == "no")
    d = d[took_fav & (d["price"] >= PRICE_EDGES[0])].copy()
    d["sec_b"] = pd.cut(d["sec_left"], SEC_EDGES, right=True, labels=SEC_LABELS).astype(str)
    d["price_b"] = pd.cut(d["price"], PRICE_EDGES, right=False, labels=PRICE_LABELS).astype(str)
    d["pv"] = d["price"] * d["count"]
    g = (d.groupby(["market_id", "sec_b", "price_b", "fav_yes"], as_index=False)
         .agg(pv=("pv", "sum"), size=("count", "sum"), prints=("n_trades", "sum")))
    g = g[g["size"] > 0]
    g["vwap"] = g["pv"] / g["size"]
    g = g.merge(mk[["market_id", "settlement_value", "split", "day"]], on="market_id")
    g["win"] = np.where(g["fav_yes"], g["settlement_value"], 1.0 - g["settlement_value"]).astype(float)
    g["fee"] = fee.per_contract_array(g["vwap"].clip(0, 1), 1)
    g["edge"] = g["win"] - g["vwap"] - g["fee"]
    return g.drop(columns=["pv"])


def _stats(d: pd.DataFrame, alpha: float = 0.05, reps: int = 4000) -> dict:
    if d.empty:
        return {"markets": 0}
    v, days = d["edge"].to_numpy(float), d["day"].to_numpy()
    lo, hi = cluster_bootstrap_ci(v, days, reps, alpha=alpha)
    return {"markets": int(d["market_id"].nunique()), "mean_c": 100 * float(v.mean()), "ci_lo_c": 100 * lo,
            "ci_hi_c": 100 * hi, "avg_price": float(d["vwap"].mean()), "win_rate": float(d["win"].mean()),
            "median_size": float(d["size"].median()), "losses": int((d["win"] < 0.5).sum())}


def evaluate(obs: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for (sb, pb), d in obs.groupby(["sec_b", "price_b"]):
        for sp in ("early", "later", "fresh"):
            rows.append({"sec_b": sb, "price_b": pb, "split": sp, **_stats(d[d["split"] == sp])})
        oos = d[d["split"].isin(["later", "fresh"])]
        rows.append({"sec_b": sb, "price_b": pb, "split": "later+fresh", **_stats(oos)})
        rows.append({"sec_b": sb, "price_b": pb, "split": "later+fresh (Bonferroni CI)",
                     **_stats(oos, alpha=ALPHA, reps=20000)})
    tab = pd.DataFrame(rows)
    get = lambda sb, pb, sp, k: tab.loc[(tab["sec_b"] == sb) & (tab["price_b"] == pb) & (tab["split"] == sp), k].squeeze() \
        if ((tab["sec_b"] == sb) & (tab["price_b"] == pb) & (tab["split"] == sp)).any() else np.nan
    verdict = []
    for sb in SEC_LABELS[:-1]:
        for pb in PRICE_LABELS:
            means = [get(sb, pb, sp, "mean_c") for sp in ("early", "later", "fresh")]
            all_pos = all(isinstance(m, float) and m > 0 for m in means)
            bonf_lo = get(sb, pb, "later+fresh (Bonferroni CI)", "ci_lo_c")
            oos_mean = get(sb, pb, "later+fresh", "mean_c")
            n_oos, med = get(sb, pb, "later+fresh", "markets"), get(sb, pb, "later+fresh", "median_size")
            ctrl = get(CONTROL, pb, "later+fresh", "mean_c")
            enough = bool(n_oos == n_oos and n_oos >= 300 and med == med and med >= 10)
            mech = bool(not (ctrl == ctrl) or not (oos_mean == oos_mean) or ctrl < 0.5 * oos_mean)
            verdict.append({"sec_b": sb, "price_b": pb, "early_c": means[0], "later_c": means[1], "fresh_c": means[2],
                            "oos_mean_c": oos_mean, "oos_bonferroni_lo_c": bonf_lo, "oos_markets": n_oos,
                            "median_size": med, "control_oos_mean_c": ctrl,
                            "pass_all_periods": all_pos, "pass_bonferroni": bool(bonf_lo == bonf_lo and bonf_lo > 0),
                            "pass_size": enough, "pass_mechanism": mech,
                            "PASS": all_pos and bool(bonf_lo == bonf_lo and bonf_lo > 0) and enough and mech})
    return tab, pd.DataFrame(verdict)


def run() -> dict:
    cfg = settings()
    c = cfg["crypto"]
    fee = FeeModel(rounding="micro_ceil")   # Kalshi's fills API charges the exact formula
    out = config.path("report_dir") / "mechanical_edges"
    out.mkdir(parents=True, exist_ok=True)
    with db.session() as conn:
        mk = pd.read_sql_query("SELECT market_id, close_ts, settlement_value FROM markets WHERE platform='kalshi' "
                               "AND series_key=? AND settlement_value IS NOT NULL", conn, params=(c["series"],))
        tf = pd.read_sql_query("SELECT * FROM trades_final", conn)
        done = conn.execute("SELECT COUNT(DISTINCT entity_id) FROM audit_log WHERE component=? AND action='trades'",
                            (COMPONENT,)).fetchone()[0]
    mk = mk[mk["market_id"].isin(tf["market_id"].unique())].copy()
    mk["split"] = mk["close_ts"].map(lambda t: split_of(int(t), c))
    mk["day"] = (mk["close_ts"] // 86400).astype(int)
    obs = observations(tf, mk, fee)
    tab, verdict = evaluate(obs)
    tab.to_csv(out / "h1_final_minute_buckets.csv", index=False)
    verdict.to_csv(out / "h1_final_minute_verdict.csv", index=False)
    res = {"markets_with_data": int(mk["market_id"].nunique()), "markets_downloaded": int(done),
           "markets_by_split": mk["split"].value_counts().to_dict(), "observations": int(len(obs)),
           "buckets_passing": verdict.loc[verdict["PASS"], ["sec_b", "price_b"]].to_dict("records")}
    (out / "h1_summary.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    with db.session() as conn:
        audit.log(conn, COMPONENT, "analysis", None, None, {k: v for k, v in res.items() if k != "markets_by_split"})
    log.info("H1 final minute: %s", res)
    return res
