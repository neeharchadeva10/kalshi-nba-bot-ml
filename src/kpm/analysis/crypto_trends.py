"""Market-wide trend scan on Kalshi BTC 15-minute markets (uses `kpm crypto-backtest` decision rows).

Rule families (taker at the ask, 100 contracts, Kalshi fee, held to settlement; one trade per market per rule,
taken at the first qualifying minute; the next minute must have traded >= 10x our size):
  price_time : favorite (or underdog) price bucket x minutes left
  momentum   : BTC move over the last 5 minutes, follow or fade
Rules are chosen on the early period (>= 200 trades, mean > 0) and judged on the later and fresh periods.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from kpm.analysis.crypto15m import summarize
from kpm.core import audit, config, db
from kpm.core.config import settings
from kpm.core.fees import FeeModel

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
TAU_BUCKETS = [(1, 2), (2, 4), (4, 7), (7, 10), (10, 14)]          # (lo, hi] minutes left
FAV_BUCKETS = [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 0.95), (0.95, 0.98), (0.98, 1.00)]


def side_prices(yes_bid, yes_ask):
    """Favorite = the side whose mid is >= 0.5. Returns (fav_is_yes, fav_mid, fav_ask, dog_ask)."""
    b, a = np.asarray(yes_bid, float), np.asarray(yes_ask, float)
    mid = (b + a) / 2
    fav_yes = mid >= 0.5
    fav_mid = np.where(fav_yes, mid, 1 - mid)
    fav_ask = np.where(fav_yes, a, 1 - b)
    dog_ask = np.where(fav_yes, 1 - b, a)
    return fav_yes, fav_mid, fav_ask, dog_ask


def session(ts: int) -> str:
    d = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(ET)
    if d.weekday() >= 5:
        return "weekend"
    return "US day (9-17 ET)" if 9 <= d.hour < 17 else "US night/Asia"


def run() -> dict:
    cfg = settings()
    c = cfg["crypto"]
    size = c["order_size"]
    fee = FeeModel.from_config(cfg)
    base = config.path("report_dir") / "crypto15m"
    out = config.path("report_dir") / "crypto_trends"
    out.mkdir(parents=True, exist_ok=True)
    g = pd.read_parquet(base / "decision_rows.parquet")
    with db.session() as conn:
        ref = pd.read_sql_query("SELECT ts, close FROM reference_prices WHERE source='coinbase' AND symbol=? ORDER BY ts",
                                conn, params=(c["product"],)).set_index("ts")
    full = ref.reindex(np.arange(ref.index.min(), ref.index.max() + 60, 60)).ffill()
    lc = np.log(full["close"])
    g["ret5_bp"] = 1e4 * (lc.reindex(g["ts"]).to_numpy() - lc.reindex(g["ts"] - 300).to_numpy())
    g = g[g["vol_next"] >= 10 * size].copy()
    g["fav_yes"], g["fav_mid"], g["fav_ask"], g["dog_ask"] = side_prices(g["yes_bid"], g["yes_ask"])
    g["y"] = g["settlement_value"].astype(float)
    g["fav_win"] = np.where(g["fav_yes"], g["y"], 1 - g["y"])
    g["day"] = (g["ts"] // 86400).astype(int)
    g["session"] = g["ts"].map(session)
    g = g.sort_values(["market_id", "ts"])

    def take(mask: pd.Series, ask: pd.Series, win: pd.Series, rule: str) -> pd.DataFrame:
        d = g[mask].assign(entry=ask[mask], win=win[mask]).groupby("market_id").head(1)
        d = d.assign(pnl=d["win"] - d["entry"] - fee.per_contract_array(d["entry"], size), rule=rule)
        return d[["market_id", "ts", "split", "day", "session", "tau", "entry", "win", "pnl", "rule"]]

    trades = []
    for lo, hi in TAU_BUCKETS:
        in_tau = (g["tau"] > lo) & (g["tau"] <= hi)
        for flo, fhi in FAV_BUCKETS:
            m = in_tau & (g["fav_mid"] >= flo) & (g["fav_mid"] < fhi)
            trades.append(take(m, g["fav_ask"], g["fav_win"], f"buy favorite {flo:.2f}-{fhi:.2f} | {lo}-{hi} min left"))
            trades.append(take(m, g["dog_ask"], 1 - g["fav_win"], f"buy underdog {1 - fhi:.2f}-{1 - flo:.2f} | {lo}-{hi} min left"))
    up_ask, dn_ask = g["yes_ask"], 1 - g["yes_bid"]
    for thr in (10, 25, 50):
        for lo, hi in [(4, 14), (1, 4)]:
            in_tau = (g["tau"] > lo) & (g["tau"] <= hi)
            rising, falling = in_tau & (g["ret5_bp"] >= thr), in_tau & (g["ret5_bp"] <= -thr)
            trades.append(take(rising, up_ask, g["y"], f"momentum follow up {thr}bp | {lo}-{hi} min"))
            trades.append(take(falling, dn_ask, 1 - g["y"], f"momentum follow down {thr}bp | {lo}-{hi} min"))
            trades.append(take(rising, dn_ask, 1 - g["y"], f"momentum fade up {thr}bp | {lo}-{hi} min"))
            trades.append(take(falling, up_ask, g["y"], f"momentum fade down {thr}bp | {lo}-{hi} min"))
    tr = pd.concat(trades, ignore_index=True)
    tr.to_csv(out / "rule_trades.csv", index=False)

    rows = []
    for (rule, sp), d in tr.groupby(["rule", "split"]):
        s = summarize(d["pnl"], d["day"], size)
        rows.append({"rule": rule, "split": sp, **s,
                     "avg_entry": float(d["entry"].mean()), "win_rate_actual": float(d["win"].mean())})
    tab = pd.DataFrame(rows)
    tab["mispricing_pp"] = 100 * (tab["win_rate_actual"] - tab["avg_entry"])
    tab.to_csv(out / "rules_by_split.csv", index=False)

    early = tab[(tab["split"] == "early") & (tab["n_trades"] >= 200) & (tab["mean_c"] > 0)]
    picks = early.sort_values("mean_c", ascending=False).head(10)["rule"].tolist()
    verdict = []
    for rule in picks:
        r = tab[tab["rule"] == rule].set_index("split")
        get = lambda sp, k: (r.loc[sp, k] if sp in r.index else np.nan)
        later_ci, fresh_ci = get("later", "ci95_c"), get("fresh", "ci95_c")
        survives = all(isinstance(ci, tuple) and ci[0] > 0 for ci in (later_ci, fresh_ci))
        verdict.append({"rule": rule, "early_mean_c": get("early", "mean_c"), "early_n": get("early", "n_trades"),
                        "later_mean_c": get("later", "mean_c"), "later_n": get("later", "n_trades"), "later_ci": later_ci,
                        "fresh_mean_c": get("fresh", "mean_c"), "fresh_n": get("fresh", "n_trades"), "fresh_ci": fresh_ci,
                        "survives_both": survives})
    ver = pd.DataFrame(verdict)
    ver.to_csv(out / "picked_rules_verdict.csv", index=False)

    # session breakdown for the picked rules (descriptive only)
    sess = (tr[tr["rule"].isin(picks)].groupby(["rule", "split", "session"])["pnl"]
            .agg(n="size", mean_c=lambda x: 100 * x.mean()).reset_index())
    sess.to_csv(out / "picked_rules_by_session.csv", index=False)
    # spreads by session (descriptive)
    spreads = g.assign(spread_c=100 * (g["yes_ask"] - g["yes_bid"])).groupby("session")["spread_c"].describe()[["mean", "50%"]]
    res = {"rules_tested": int(tab["rule"].nunique()),
           "early_rules_positive_with_200_trades": int(len(early)),
           "picked": ver.to_dict("records"),
           "spread_by_session_c": spreads.round(3).to_dict("index")}
    (out / "summary.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    with db.session() as conn:
        audit.log(conn, "analysis.crypto_trends", "complete", None, None,
                  {"rules": res["rules_tested"], "picked": len(picks)})
    log.info("crypto trend scan: %d rules; results in %s", res["rules_tested"], out)
    return res
