"""Backtest of published 'late favorite' bot rules on Kalshi BTC 15-minute markets.

Source: 'Kalshi Trading Bot v5' (code.processoverprofit.blog, video 14). In the last `window_min` minutes
(0 < minutes left <= window) the bot buys the side (UP = YES, DOWN = NO) whose ask is inside [entry_lo, entry_hi],
once per market, with a marketable limit (ask + 2c). While holding, on the held side's ask:
  stop-loss    ask < stop                          -> sell with a limit at ask - 2c
  take-profit  ask >= fill + tp_pts (if <= $1)     -> sell with a limit at ask - 2c
  otherwise    held to settlement ($1 or $0, no fee)
Optional delta filter (the bot's `Delta`): enter only when |BTC now - window's opening price| >= delta dollars; BTC is
the Coinbase minute close and the opening price is the market's strike.
The bot polls every ~0.8 s; 1-minute candles cannot replay that exactly, so two fill models bracket it:
  close        rules checked on each minute's closing quotes; an exit fills at the bid if bid >= ask - 2c
               (otherwise the limit would not fill and the bot tries again next minute)
  intraminute  touches detected from each minute's extreme quotes; take-profit fills at its limit
               (target - 2c), stop-loss at the lower of its limit and the bid at the minute's close
Fees: Kalshi taker fee per order at the bot's default size of 1 contract, rounded as in config unless overridden.
Kalshi's fills API currently charges the exact formula (e.g. $0.1078 for 10 contracts at 81c): use micro_ceil.
Periods as in the other crypto studies: early (June 2026), later (Jul 1 - Aug 4), fresh (Aug 5 - Oct 4).
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, replace

import numpy as np
import pandas as pd

from kpm.analysis.crypto15m import split_of, summarize
from kpm.core import audit, config, db
from kpm.core.config import settings
from kpm.core.fees import FeeModel
from kpm.core.timeutil import parse_iso

log = logging.getLogger(__name__)
EPS = 1e-9
MODELS = ("close", "intraminute")


@dataclass(frozen=True)
class BotRules:
    name: str
    entry_lo: float = 0.75
    entry_hi: float = 0.85
    window_min: float = 8
    stop: float = 0.40        # 0 disables the stop-loss
    tp_pts: float = 0.10      # 1 holds winners to settlement
    exit_diff: float = 0.02   # the bot's exit limit is the current ask minus this
    size: int = 1
    delta: float = 0.0        # min |BTC - opening price| in dollars at entry; 0 disables


def rule_grid() -> list[BotRules]:
    """The bot's two published defaults, plus the same entries without the stop and/or take-profit."""
    defaults = {(0.75, 0.85): "v5 paper default", (0.80, 0.93): "v5 live default"}
    rules = []
    for lo, hi in [(0.75, 0.85), (0.80, 0.93), (0.85, 0.95), (0.90, 0.95)]:
        for stop in (0.40, 0.0):
            for tp in (0.10, 1.0):
                name = (f"buy {lo * 100:.0f}-{hi * 100:.0f}c | " + (f"stop {stop * 100:.0f}c" if stop else "no stop")
                        + " | " + (f"tp +{tp * 100:.0f}c" if tp < 1 else "hold winners"))
                if stop == 0.40 and tp == 0.10 and (lo, hi) in defaults:
                    name = f"{defaults[(lo, hi)]}: {name}"
                rules.append(BotRules(name, lo, hi, 8, stop, tp))
    for tp in (0.10, 1.0):   # the live default's entries with the delta filter switched on
        for delta in (25.0, 50.0, 100.0):
            name = f"buy 80-93c | stop 40c | {'tp +10c' if tp < 1 else 'hold winners'} | delta >= ${delta:.0f}"
            rules.append(BotRules(name, 0.80, 0.93, 8, 0.40, tp, delta=delta))
    return rules


def simulate_market(tau, yes_bid, yes_ask, bid_low, ask_high, y: float, r: BotRules, fee: FeeModel,
                    intraminute: bool = False, spot=None, strike: float = np.nan) -> dict | None:
    """One market; arrays in time order, one row per minute close (tau = minutes left). Returns the trade or None."""
    k, side, entry = None, 0, np.nan
    for i in range(len(tau)):
        if not (0 < tau[i] <= r.window_min) or np.isnan(yes_bid[i]) or np.isnan(yes_ask[i]):
            continue
        if r.delta > 0 and (spot is None or not abs(spot[i] - strike) >= r.delta):   # missing prices never qualify
            continue
        up, dn = yes_ask[i], 1.0 - yes_bid[i]
        up_in = r.entry_lo - EPS <= up <= r.entry_hi + EPS
        dn_in = r.entry_lo - EPS <= dn <= r.entry_hi + EPS
        if up_in and (not dn_in or up >= dn):
            k, side, entry = i, 1, up
        elif dn_in:
            k, side, entry = i, -1, dn
        if k is not None:
            break
    if k is None:
        return None
    target = entry + r.tp_pts
    can_tp = target <= 1 + EPS
    exit_px, reason, j = np.nan, None, None
    for j in range(k + 1, len(tau)):
        b, a = yes_bid[j], yes_ask[j]
        if np.isnan(b) or np.isnan(a):
            continue
        ask, bid = (a, b) if side == 1 else (1.0 - b, 1.0 - a)
        if intraminute:
            ask_max, bid_min = (ask_high[j], bid_low[j]) if side == 1 else (1.0 - bid_low[j], 1.0 - ask_high[j])
            if r.stop > 0 and bid_min < r.stop - 0.01 - EPS:   # held ask went below the stop (>= 1c spread)
                exit_px, reason = min(r.stop - 0.01 - r.exit_diff, bid), "stop"
            elif can_tp and ask_max >= target - EPS:
                exit_px, reason = target - r.exit_diff, "take_profit"
        elif tau[j] > 0:   # the close-of-market quote is not tradable
            stop_hit = r.stop > 0 and ask < r.stop - EPS
            tp_hit = can_tp and ask >= target - EPS
            if (stop_hit or tp_hit) and bid > 0 and bid >= ask - r.exit_diff - EPS:
                exit_px, reason = bid, ("stop" if stop_hit else "take_profit")
        if reason:
            break
    win = y if side == 1 else 1.0 - y
    entry_fee = fee.per_contract(entry, r.size)
    if reason:
        exit_px = float(np.clip(exit_px, 0.0, 1.0))
        exit_fee = fee.per_contract(exit_px, r.size)
        pnl = exit_px - exit_fee - entry - entry_fee
    else:
        exit_fee, reason = 0.0, ("settled_win" if win > 0.5 else "settled_loss")
        pnl = win - entry - entry_fee
    return {"tau_entry": float(tau[k]), "side": "UP" if side == 1 else "DOWN", "entry": float(entry),
            "exit": float(exit_px) if reason in ("stop", "take_profit") else float(win), "reason": reason,
            "won_at_settlement": float(win), "fees": entry_fee + exit_fee, "pnl": float(pnl)}


def prepare(conn, c: dict) -> list[dict]:
    """Per-market minute arrays (closing quotes forward-filled, plus each minute's extremes)."""
    lo, hi = parse_iso(c["start"]), parse_iso(c["fresh_end"])
    mk = pd.read_sql_query(
        "SELECT market_id, open_ts, close_ts, settlement_value, meta_json FROM markets WHERE platform='kalshi' "
        "AND series_key=? AND close_ts >= ? AND close_ts < ? AND settlement_value IS NOT NULL",
        conn, params=(c["series"], lo, hi))
    mk["K"] = mk["meta_json"].map(lambda s: json.loads(s).get("floor_strike")).astype(float)
    ref = pd.read_sql_query("SELECT ts, close FROM reference_prices WHERE source='coinbase' AND symbol=? ORDER BY ts",
                            conn, params=(c["product"],)).set_index("ts")["close"]
    spot = ref.reindex(np.arange(ref.index.min(), ref.index.max() + 60, 60)).ffill() if len(ref) else ref
    cd = pd.read_sql_query(
        """SELECT p.market_id, p.ts, p.yes_bid, p.yes_ask, p.yes_bid_low, p.yes_ask_high FROM price_history p
           JOIN markets m ON m.market_id = p.market_id
           WHERE p.source='kalshi_candle' AND m.platform='kalshi' AND m.series_key=? AND m.close_ts >= ? AND m.close_ts < ?""",
        conn, params=(c["series"], lo, hi))
    bad = (cd["yes_bid"] <= 0) | (cd["yes_ask"] >= 1) | (cd["yes_bid"] >= cd["yes_ask"])
    cd.loc[bad, ["yes_bid", "yes_ask"]] = np.nan
    # extremes are trusted only when both sides of the book existed all minute (an empty side reads as 0 or 1)
    cd.loc[(cd["yes_bid_low"] <= 0) | (cd["yes_ask_high"] >= 1), ["yes_bid_low", "yes_ask_high"]] = np.nan
    grid = pd.concat([pd.DataFrame({"market_id": r.market_id, "ts": np.arange(int(r.open_ts) + 60, int(r.close_ts) + 1, 60)})
                      for r in mk.itertuples()], ignore_index=True)
    g = grid.merge(cd, on=["market_id", "ts"], how="left").sort_values(["market_id", "ts"])
    g[["yes_bid", "yes_ask"]] = g.groupby("market_id")[["yes_bid", "yes_ask"]].ffill()
    g["yes_bid_low"] = np.fmin(g["yes_bid_low"].fillna(g["yes_bid"]), g["yes_bid"])
    g["yes_ask_high"] = np.fmax(g["yes_ask_high"].fillna(g["yes_ask"]), g["yes_ask"])
    meta = mk.set_index("market_id")
    out = []
    for mid, d in g.groupby("market_id", sort=False):
        close_ts = int(meta.at[mid, "close_ts"])
        out.append({"market_id": int(mid), "split": split_of(close_ts, c), "day": close_ts // 86400,
                    "y": float(meta.at[mid, "settlement_value"]), "tau": (close_ts - d["ts"].to_numpy()) / 60.0,
                    "K": float(meta.at[mid, "K"]), "spot": spot.reindex(d["ts"].to_numpy()).to_numpy(float),
                    "bid": d["yes_bid"].to_numpy(float), "ask": d["yes_ask"].to_numpy(float),
                    "bid_low": d["yes_bid_low"].to_numpy(float), "ask_high": d["yes_ask_high"].to_numpy(float)})
    return out


def describe(d: pd.DataFrame, n_markets: int, size: int) -> dict:
    s = summarize(d["pnl"], d["day"], size)
    wins, losses = d.loc[d["pnl"] > 0, "pnl"], d.loc[d["pnl"] <= 0, "pnl"]
    avg_w = 100 * float(wins.mean()) if len(wins) else np.nan
    avg_l = -100 * float(losses.mean()) if len(losses) else np.nan
    reasons = d["reason"].value_counts(normalize=True)
    days = d.groupby("day")["pnl"].sum() * size
    return {**s, "trades_per_market": len(d) / n_markets, "trades_per_day": len(d) / max(1, d["day"].nunique()),
            "avg_entry": float(d["entry"].mean()), "avg_win_c": avg_w, "avg_loss_c": avg_l,
            "breakeven_win_rate": avg_l / (avg_w + avg_l) if avg_w == avg_w and avg_l == avg_l else np.nan,
            "usd_per_day": float(days.mean()), "losing_days": float((days < 0).mean()),
            **{f"pct_{k}": float(reasons.get(k, 0.0)) for k in ("take_profit", "stop", "settled_win", "settled_loss")}}


def run(fee_rounding: str | None = None) -> dict:
    cfg = settings()
    c = cfg["crypto"]
    fee = FeeModel.from_config(cfg)
    if fee_rounding:
        fee = replace(fee, rounding=fee_rounding)
    out = config.path("report_dir") / "crypto_bot_rules"
    out.mkdir(parents=True, exist_ok=True)
    with db.session() as conn:
        markets = prepare(conn, c)
    n_split = pd.Series([m["split"] for m in markets]).value_counts().to_dict()
    n_split["all"] = len(markets)
    rows, keep = [], []
    for rules in rule_grid():
        for model in MODELS:
            tr = [{"market_id": m["market_id"], "split": m["split"], "day": m["day"],
                   **t} for m in markets
                  if (t := simulate_market(m["tau"], m["bid"], m["ask"], m["bid_low"], m["ask_high"], m["y"], rules,
                                           fee, intraminute=model == "intraminute", spot=m["spot"],
                                           strike=m["K"])) is not None]
            df = pd.DataFrame(tr).assign(rule=rules.name, model=model)
            for sp, d in list(df.groupby("split")) + [("all", df)]:
                rows.append({"rule": rules.name, "model": model, "split": sp, "fee_rounding": fee.rounding,
                             **describe(d, n_split[sp], rules.size)})
            if rules.name.startswith("v5"):
                keep.append(df)
    tab = pd.DataFrame(rows)
    tab.to_csv(out / "results.csv", index=False)
    pd.concat(keep, ignore_index=True).to_csv(out / "trades_v5_defaults.csv", index=False)
    res = {"markets": n_split, "fee_rounding": fee.rounding, "rules": [asdict(r) for r in rule_grid()],
           "v5_defaults": tab[tab["rule"].str.startswith("v5")].to_dict("records")}
    (out / "summary.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    with db.session() as conn:
        audit.log(conn, "analysis.crypto_bot_rules", "complete", None, None,
                  {"markets": len(markets), "rules": len(rule_grid())})
    log.info("bot rules backtest: %d markets, %d rule sets; results in %s", len(markets), len(rule_grid()), out)
    return res
