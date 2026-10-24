"""Kalshi BTC 15-minute up/down markets vs a volatility-based fair value from Coinbase.

Market: YES pays if the 60-second average of CF Benchmarks' BRTI before the close is >= the 60-second average
before the open (the target, given as floor_strike). At each minute end t inside the window:
  S_t      Coinbase close (bar ending at t), corrected by the Coinbase-vs-BRTI basis measured on the early period
  sigma_t  std of 1-minute log returns over the trailing `vol_lookback_min` minutes (no look-ahead)
  var      sigma^2 * ((tau - 1) + 1/3) + basis variance   (the last minute is a 60s average)
  fair_t   Phi( (ln S_t - ln K) / sqrt(var) )
Strategies (taker, 100 contracts, Kalshi taker fee; settlement has no fee):
  hold   buy the cheap side at the ask when fair beats it by more than a threshold, hold to settlement
  scalp  same entry, sell one minute later at the bid
Settings are chosen on [start, early_end), tested on [early_end, later_end), confirmed on [later_end, fresh_end).
"""
from __future__ import annotations

import itertools
import json
import logging
from math import erf, sqrt

import numpy as np
import pandas as pd

from kpm.analysis.strategies import cluster_bootstrap_ci, max_drawdown
from kpm.core import audit, config, db
from kpm.core.config import settings
from kpm.core.fees import FeeModel
from kpm.core.timeutil import parse_iso

log = logging.getLogger(__name__)


def norm_cdf(z):
    z = np.asarray(z, dtype=float)
    return 0.5 * (1.0 + np.vectorize(erf)(z / sqrt(2.0)))


def settle_variance(sigma_min, tau_min, basis_var: float = 0.0):
    """Variance of ln(settlement average) - ln(S_t) with tau minutes left; the final minute is averaged."""
    s = np.asarray(sigma_min, float)
    t = np.asarray(tau_min, float)
    v = s ** 2 * (np.maximum(t - 1.0, 0.0) + np.minimum(t, 1.0) / 3.0)
    return v + basis_var


def fair_up(spot, strike, sigma_min, tau_min, basis_mean: float = 0.0, basis_var: float = 0.0):
    var = settle_variance(sigma_min, tau_min, basis_var)
    z = (np.log(np.asarray(spot, float)) + basis_mean - np.log(np.asarray(strike, float))) / np.sqrt(var)
    return norm_cdf(z)


def split_of(close_ts: int, c: dict) -> str:
    if close_ts < parse_iso(c["early_end"]):
        return "early"
    if close_ts < parse_iso(c["later_end"]):
        return "later"
    return "fresh"


def load_frames(conn, c: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    lo, hi = parse_iso(c["start"]), parse_iso(c["fresh_end"])
    mk = pd.read_sql_query(
        "SELECT market_id, contract_key, open_ts, close_ts, settlement_value, meta_json FROM markets "
        "WHERE platform='kalshi' AND series_key=? AND close_ts >= ? AND close_ts < ?",
        conn, params=(c["series"], lo, hi))
    meta = mk["meta_json"].map(json.loads)
    mk["K"] = meta.map(lambda m: m.get("floor_strike")).astype(float)
    mk["exp_value"] = pd.to_numeric(meta.map(lambda m: m.get("expiration_value")), errors="coerce")
    mk["split"] = mk["close_ts"].map(lambda t: split_of(int(t), c))
    cd = pd.read_sql_query(
        """SELECT p.market_id, p.ts, p.yes_bid, p.yes_ask, p.volume FROM price_history p
           JOIN markets m ON m.market_id = p.market_id
           WHERE p.source='kalshi_candle' AND m.platform='kalshi' AND m.series_key=? AND m.close_ts >= ? AND m.close_ts < ?""",
        conn, params=(c["series"], lo, hi))
    ref = pd.read_sql_query("SELECT ts, open, high, low, close FROM reference_prices WHERE source='coinbase' AND symbol=? "
                            "ORDER BY ts", conn, params=(c["product"],))
    return mk.drop(columns=["meta_json"]), cd, ref


def build_rows(mk: pd.DataFrame, cd: pd.DataFrame, ref: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """One row per market per decision minute (1 <= tau <= 14), plus the next minute's quotes."""
    ref = ref.set_index("ts").sort_index()
    full = ref.reindex(np.arange(ref.index.min(), ref.index.max() + 60, 60))
    full["close"] = full["close"].ffill()
    lr = np.log(full["close"]).diff()
    full["sigma"] = lr.rolling(lookback, min_periods=lookback // 2).std()
    grid = []
    for r in mk.itertuples():
        ts = np.arange(int(r.open_ts) + 60, int(r.close_ts) + 1, 60)
        grid.append(pd.DataFrame({"market_id": r.market_id, "ts": ts}))
    g = pd.concat(grid, ignore_index=True)
    cd = cd.copy()
    cd.loc[(cd["yes_bid"] <= 0) | (cd["yes_ask"] >= 1) | (cd["yes_bid"] >= cd["yes_ask"]), ["yes_bid", "yes_ask"]] = np.nan
    g = g.merge(cd, on=["market_id", "ts"], how="left")
    g[["yes_bid", "yes_ask"]] = g.groupby("market_id")[["yes_bid", "yes_ask"]].ffill()
    g["volume"] = g["volume"].fillna(0)
    g = g.merge(mk[["market_id", "close_ts", "K", "settlement_value", "split"]], on="market_id")
    g["tau"] = (g["close_ts"] - g["ts"]) / 60.0
    g["spot"] = full["close"].reindex(g["ts"]).to_numpy()
    g["sigma"] = full["sigma"].reindex(g["ts"]).to_numpy()
    nxt = g.groupby("market_id")[["yes_bid", "yes_ask", "volume"]].shift(-1)
    g["bid_next"], g["ask_next"], g["vol_next"] = nxt["yes_bid"], nxt["yes_ask"], nxt["volume"]
    return g


def basis_stats(mk: pd.DataFrame, ref: pd.DataFrame) -> tuple[float, float, pd.DataFrame]:
    """Coinbase vs BRTI at settlement: ln(BRTI 60s average) - ln(Coinbase mean of the final minute bar)."""
    r = ref.set_index("ts")
    last = r.reindex(mk["close_ts"])
    cb_avg = ((last["open"] + last["high"] + last["low"] + last["close"]) / 4).to_numpy()
    d = mk.assign(basis=np.log(mk["exp_value"].to_numpy()) - np.log(cb_avg))
    early = d[(d["split"] == "early") & d["basis"].notna()]
    return float(early["basis"].median()), float(early["basis"].var()), d


def summarize(pnl: pd.Series, groups: pd.Series, size: int) -> dict:
    v = pnl.to_numpy(float)
    ok = ~np.isnan(v)
    v, gg = v[ok], groups.to_numpy()[ok]
    if len(v) == 0:
        return {"n_trades": 0}
    lo, hi = cluster_bootstrap_ci(v, gg, 1000)
    return {"n_trades": int(len(v)), "mean_c": 100 * float(v.mean()), "ci95_c": (round(100 * lo, 2), round(100 * hi, 2)),
            "win_rate": float((v > 0).mean()), "total_usd": float(v.sum() * size), "max_drawdown_usd": max_drawdown(v * size)}


def run() -> dict:
    cfg = settings()
    c = cfg["crypto"]
    size = c["order_size"]
    fee = FeeModel.from_config(cfg)
    out = config.path("report_dir") / "crypto15m"
    out.mkdir(parents=True, exist_ok=True)
    with db.session() as conn:
        mk, cd, ref = load_frames(conn, c)
    if mk.empty:
        raise RuntimeError("no crypto markets loaded: run `kpm crypto-collect` first")
    b_mean, b_var, bas = basis_stats(mk, ref)
    g = build_rows(mk, cd, ref, c["vol_lookback_min"])
    g["fair"] = fair_up(g["spot"], g["K"], g["sigma"], g["tau"], b_mean, b_var)
    g["mid"] = (g["yes_bid"] + g["yes_ask"]) / 2
    g["fee_yes"] = fee.per_contract_array(g["yes_ask"], size)
    g["fee_no"] = fee.per_contract_array(1 - g["yes_bid"], size)
    g["edge_yes"] = g["fair"] - g["yes_ask"] - g["fee_yes"]
    g["edge_no"] = (1 - g["fair"]) - (1 - g["yes_bid"]) - g["fee_no"]
    valid = g["fair"].notna() & g["yes_bid"].notna() & g["yes_ask"].notna() & (g["tau"] >= 1)
    g = g[valid].copy()
    g.to_parquet(out / "decision_rows.parquet", index=False)

    res: dict = {"markets": int(mk["market_id"].nunique()), "by_split": mk["split"].value_counts().to_dict(),
                 "basis": {"median_bp": 1e4 * b_mean, "std_bp": 1e4 * np.sqrt(b_var)}}

    # diagnostics: market spread, calibration (model vs market), and whether Kalshi lags Coinbase ----------------
    res["median_spread_c"] = 100 * float((g["yes_ask"] - g["yes_bid"]).median())
    cal = []
    for sp, d in g.groupby("split"):
        for tau in (12, 8, 4, 2):
            s = d[d["tau"] == tau]
            y = s["settlement_value"].to_numpy(float)
            cal.append({"split": sp, "tau_min": tau, "n": len(s),
                        "brier_model": float(np.mean((s["fair"] - y) ** 2)),
                        "brier_market_mid": float(np.mean((s["mid"] - y) ** 2))})
    res["calibration"] = cal
    lag = []
    for sp, d in g.dropna(subset=["bid_next", "ask_next"]).groupby("split"):
        x = (d["fair"] - d["mid"]).to_numpy()
        yv = ((d["bid_next"] + d["ask_next"]) / 2 - d["mid"]).to_numpy()
        slope = float(np.polyfit(x, yv, 1)[0]) if len(x) > 10 else np.nan
        lag.append({"split": sp, "n": len(x), "slope_next_mid_on_gap": slope,
                    "corr": float(np.corrcoef(x, yv)[0, 1]) if len(x) > 10 else np.nan,
                    "median_abs_gap_c": 100 * float(np.median(np.abs(x)))})
    res["lag"] = lag

    # strategies -------------------------------------------------------------------------------------------------
    trades_all = []
    grid = list(itertools.product((0.01, 0.02, 0.03, 0.05, 0.08), (2, 4, 8)))
    for thr, min_tau in grid:
        d = g[(g["tau"] >= min_tau) & (g["vol_next"] >= 10 * size)]
        best = np.where(d["edge_yes"] >= d["edge_no"], "yes", "no")
        edge = np.maximum(d["edge_yes"], d["edge_no"])
        sig = d.assign(side=best, edge=edge)[edge > thr].sort_values(["market_id", "ts"]).groupby("market_id").head(1)
        if sig.empty:
            continue
        yes = sig["side"] == "yes"
        entry = np.where(yes, sig["yes_ask"], 1 - sig["yes_bid"])
        fee_in = np.where(yes, sig["fee_yes"], sig["fee_no"])
        payoff = np.where(yes, sig["settlement_value"], 1 - sig["settlement_value"])
        exit_px = np.where(yes, sig["bid_next"], 1 - sig["ask_next"])
        fee_out = fee.per_contract_array(exit_px, size)
        t = sig.assign(entry=entry, pnl_hold=payoff - entry - fee_in,
                       pnl_scalp=exit_px - entry - fee_in - fee_out, thr=thr, min_tau=min_tau)
        trades_all.append(t[["market_id", "ts", "split", "side", "tau", "edge", "entry", "pnl_hold", "pnl_scalp",
                             "thr", "min_tau"]])
    trades = pd.concat(trades_all, ignore_index=True)
    trades["day"] = (trades["ts"] // 86400).astype(int)  # cluster CIs by day: markets in a day share BTC moves
    trades.to_csv(out / "trades.csv", index=False)

    rows = []
    for (thr, min_tau, sp), d in trades.groupby(["thr", "min_tau", "split"]):
        for strat in ("hold", "scalp"):
            rows.append({"thr": thr, "min_tau": min_tau, "split": sp, "strategy": strat,
                         **summarize(d[f"pnl_{strat}"], d["day"], size)})
    grid_df = pd.DataFrame(rows)
    grid_df.to_csv(out / "strategy_grid.csv", index=False)
    chosen = {}
    for strat in ("hold", "scalp"):
        e = grid_df[(grid_df["split"] == "early") & (grid_df["strategy"] == strat) & (grid_df["n_trades"] >= 200)]
        if e.empty:
            continue
        b = e.sort_values("mean_c", ascending=False).iloc[0]
        sel = grid_df[(grid_df["thr"] == b["thr"]) & (grid_df["min_tau"] == b["min_tau"]) & (grid_df["strategy"] == strat)]
        chosen[strat] = {"thr": float(b["thr"]), "min_tau": int(b["min_tau"]),
                         "results": sel.drop(columns=["thr", "min_tau", "strategy"]).to_dict("records")}
    res["chosen"] = chosen
    res["early_sets_positive"] = {s: int(((grid_df["split"] == "early") & (grid_df["strategy"] == s) &
                                          (grid_df["mean_c"] > 0)).sum()) for s in ("hold", "scalp")}
    (out / "summary.json").write_text(json.dumps(res, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)),
                                      encoding="utf-8")
    with db.session() as conn:
        audit.log(conn, "analysis.crypto15m", "complete", None, None, {"markets": res["markets"], "chosen": chosen})
    log.info("crypto 15m backtest: %d markets; results in %s", res["markets"], out)
    return res
