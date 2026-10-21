"""Breakout test on Kalshi BTC 15-minute markets: when price breaks the last confirmed swing high (low) on a
higher-timeframe line chart, does the next part of the 15-minute window go that way, after paying Kalshi's price?

Swing levels: on `bar_minutes` line bars (closes), bar i is a swing high (low) if its close is above (below) the
n bars on each side; it becomes known when bar i+n closes. At each minute t, H(t)/L(t) are the most recent
swing high/low known by t. A break up happens at the first minute whose close is above H(t) after a close at or
below it (break down: mirror image).
  in_window  : react at the break minute inside a window (>= 2 minutes left): buy the break side at the ask
  next_window: a break in the 15 minutes before a window opens -> buy that side at the window's first minute
Held to settlement, Kalshi taker fee, 100 contracts. June chooses, July tests, Aug-Oct confirms.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from kpm.analysis.crypto15m import summarize
from kpm.core import audit, config, db
from kpm.core.config import settings
from kpm.core.fees import FeeModel

log = logging.getLogger(__name__)


def swing_levels(closes: pd.Series, bar_minutes: int, n: int = 2) -> pd.DataFrame:
    """Most recent confirmed swing high and low as of each minute (index = minute-end ts)."""
    step = bar_minutes * 60
    ends = closes.index[closes.index % step == 0].to_numpy()
    bars = closes.loc[ends].to_numpy(dtype=float)
    hi_t, hi_v, lo_t, lo_v = [], [], [], []
    for i in range(n, len(bars) - n):
        w = bars[i - n:i + n + 1]
        others = np.delete(w, n)
        confirm_ts = ends[i + n]                      # known once bar i+n has closed
        if bars[i] > others.max():
            hi_t.append(confirm_ts); hi_v.append(bars[i])
        elif bars[i] < others.min():
            lo_t.append(confirm_ts); lo_v.append(bars[i])
    idx = closes.index
    H = pd.Series(hi_v, index=hi_t).groupby(level=0).last().reindex(idx, method="ffill")
    L = pd.Series(lo_v, index=lo_t).groupby(level=0).last().reindex(idx, method="ffill")
    return pd.DataFrame({"H": H.to_numpy(), "L": L.to_numpy()}, index=idx)


def breaks(closes: pd.Series, levels: pd.DataFrame) -> pd.Series:
    """+1 at the minute price first closes above the swing high, -1 below the swing low, else 0."""
    c = closes.to_numpy(dtype=float)
    prev = np.r_[np.nan, c[:-1]]
    H, L = levels["H"].to_numpy(), levels["L"].to_numpy()
    up = (c > H) & (prev <= H)
    dn = (c < L) & (prev >= L)
    return pd.Series(np.where(up, 1, np.where(dn, -1, 0)), index=closes.index)


def _trade(d: pd.DataFrame, side: np.ndarray, fee: FeeModel, size: int) -> pd.DataFrame:
    up = side > 0
    entry = np.where(up, d["yes_ask"], 1 - d["yes_bid"])
    win = np.where(up, d["settlement_value"], 1 - d["settlement_value"]).astype(float)
    return d.assign(side=np.where(up, "up", "down"), entry=entry, win=win,
                    pnl=win - entry - fee.per_contract_array(entry, size))


def run() -> dict:
    cfg = settings()
    c = cfg["crypto"]
    size = c["order_size"]
    fee = FeeModel.from_config(cfg)
    out = config.path("report_dir") / "crypto_breakout"
    out.mkdir(parents=True, exist_ok=True)
    rows = pd.read_parquet(config.path("report_dir") / "crypto15m" / "decision_rows.parquet")
    rows = rows[rows["vol_next"] >= 10 * size]
    with db.session() as conn:
        ref = pd.read_sql_query("SELECT ts, close FROM reference_prices WHERE source='coinbase' AND symbol=? ORDER BY ts",
                                conn, params=(c["product"],)).set_index("ts")["close"]
    closes = ref.reindex(np.arange(ref.index.min(), ref.index.max() + 60, 60)).ffill()
    recs, counts = [], {}
    for tf, n in ((60, 2), (60, 3), (15, 2)):
        name = f"{'1h' if tf == 60 else '15m'} chart, {n}-bar swings"
        brk = breaks(closes, swing_levels(closes, tf, n))
        counts[name] = {"breaks_up": int((brk > 0).sum()), "breaks_down": int((brk < 0).sum())}
        # in-window: react at the first break minute inside each window with >= 2 minutes left
        r = rows.assign(brk=brk.reindex(rows["ts"]).to_numpy())
        r = r[(r["brk"] != 0) & (r["tau"] >= 2)].sort_values(["market_id", "ts"]).groupby("market_id").head(1)
        t1 = _trade(r, r["brk"].to_numpy(), fee, size).assign(variant="react in window (at the break)", chart=name)
        # next window: a break in the 15 minutes before the window opened -> trade at the first minute
        first = rows.sort_values(["market_id", "ts"]).groupby("market_id").head(1)
        last_break = brk.replace(0, np.nan).ffill(limit=15)  # direction of a break within the last 15 minutes
        open_ts = first["ts"] - 60
        side = last_break.reindex(open_ts).to_numpy()
        f = first.assign(side_sig=side)
        f = f[~np.isnan(f["side_sig"])]
        t2 = _trade(f, f["side_sig"].to_numpy(), fee, size).assign(variant="next window after a break", chart=name)
        for t in (t1, t2):
            t = t.assign(day=(t["ts"] // 86400).astype(int))
            for (sp, sd), tt in t.groupby(["split", "side"]):
                recs.append({"chart": name, "variant": t["variant"].iloc[0], "split": sp, "direction": sd,
                             "win_rate": float(tt["win"].mean()), "avg_price": float(tt["entry"].mean()),
                             **summarize(tt["pnl"], tt["day"], size)})
            for sp, tt in t.groupby("split"):
                recs.append({"chart": name, "variant": t["variant"].iloc[0], "split": sp, "direction": "both",
                             "win_rate": float(tt["win"].mean()), "avg_price": float(tt["entry"].mean()),
                             **summarize(tt["pnl"], tt["day"], size)})
    tab = pd.DataFrame(recs)
    tab["breakeven_win_rate"] = tab["avg_price"] + fee.per_contract_array(tab["avg_price"], size)
    tab.to_csv(out / "breakout_results.csv", index=False)
    res = {"break_counts_jun_oct": counts, "rows": tab.to_dict("records")}
    (out / "summary.json").write_text(json.dumps(res, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)),
                                      encoding="utf-8")
    with db.session() as conn:
        audit.log(conn, "analysis.crypto_breakout", "complete", None, None, counts)
    log.info("breakout test done; results in %s", out)
    return res
