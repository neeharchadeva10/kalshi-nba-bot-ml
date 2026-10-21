"""Higher-timeframe trend following on Kalshi BTC 15-minute markets.

At each window's first available minute, a trend signal from Coinbase closes (past data only) picks a side:
up-trend -> buy YES ("up") at the ask; down-trend -> buy NO at 1 - YES bid. Held to settlement, Kalshi taker
fee, 100 contracts, one trade per market. Signals are chosen on the early period, judged on later and fresh.
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


def swing_trend_states(bar_closes: np.ndarray, n: int = 2) -> np.ndarray:
    """Market-structure trend after each bar of a line chart: +1 = higher high AND higher low, -1 = lower high AND
    lower low, 0 otherwise. A swing high/low at bar i (close above/below the n bars on each side) only counts once
    bar i+n has closed, so the state at bar j uses nothing a trader could not have seen at bar j."""
    m = len(bar_closes)
    state = np.zeros(m, dtype=int)
    highs: list[float] = []
    lows: list[float] = []
    for j in range(m):
        i = j - n
        if i >= n:
            w = bar_closes[i - n:i + n + 1]
            c = bar_closes[i]
            others = np.delete(w, n)
            if c > others.max():
                highs = (highs + [c])[-2:]
            elif c < others.min():
                lows = (lows + [c])[-2:]
        if len(highs) == 2 and len(lows) == 2:
            hh, hl = highs[1] > highs[0], lows[1] > lows[0]
            lh, ll = highs[1] < highs[0], lows[1] < lows[0]
            state[j] = 1 if (hh and hl) else -1 if (lh and ll) else 0
    return state


def swing_trend_at(closes: pd.Series, bar_minutes: int, ts: np.ndarray, n: int = 2) -> np.ndarray:
    """Swing-structure trend on a `bar_minutes` line chart, as of each decision time (last completed bar)."""
    step = bar_minutes * 60
    ends = closes.index[closes.index % step == 0]
    bars = closes.loc[ends].to_numpy(dtype=float)
    states = swing_trend_states(bars, n)
    k = np.searchsorted(ends.to_numpy(), ts, side="right") - 1
    out = np.where(k >= 0, states[np.clip(k, 0, len(states) - 1)], 0)
    return out


def trend_features(closes: pd.Series, ts: np.ndarray) -> pd.DataFrame:
    """closes: 1-minute closes indexed by bar-end ts (gaps forward-filled). Values as of each ts."""
    lc = np.log(closes)
    ema_15m50 = closes.ewm(span=50 * 15, adjust=False).mean()   # ~50-period EMA on 15-minute bars
    ema_1h20 = closes.ewm(span=20 * 60, adjust=False).mean()    # ~20-period EMA on 1-hour bars
    at = lambda s, lag=0: s.reindex(ts - lag).to_numpy()
    swings = {f"swing_{tf}m_n{n}": swing_trend_at(closes, tf, ts, n) for tf in (5, 15, 60) for n in (2, 3)}
    return pd.DataFrame({
        **swings,
        "ret_5m": at(lc) - at(lc, 300), "ret_15m": at(lc) - at(lc, 900),
        "ret_3h": at(lc) - at(lc, 3 * 3600), "ret_12h": at(lc) - at(lc, 12 * 3600),
        "ret_1h": at(lc) - at(lc, 3600), "ret_4h": at(lc) - at(lc, 4 * 3600), "ret_24h": at(lc) - at(lc, 86400),
        "above_ema_15m50": at(closes) > at(ema_15m50), "above_ema_1h20": at(closes) > at(ema_1h20),
    }, index=ts)


def run() -> dict:
    cfg = settings()
    c = cfg["crypto"]
    size = c["order_size"]
    fee = FeeModel.from_config(cfg)
    out = config.path("report_dir") / "crypto_htf"
    out.mkdir(parents=True, exist_ok=True)
    rows = pd.read_parquet(config.path("report_dir") / "crypto15m" / "decision_rows.parquet")
    with db.session() as conn:
        ref = pd.read_sql_query("SELECT ts, close FROM reference_prices WHERE source='coinbase' AND symbol=? ORDER BY ts",
                                conn, params=(c["product"],)).set_index("ts")["close"]
    closes = ref.reindex(np.arange(ref.index.min(), ref.index.max() + 60, 60)).ffill()
    recs, d = [], None
    entries = {"window start": rows.sort_values(["market_id", "ts"]).groupby("market_id").head(1),
               "5 min in": rows[rows["tau"] == 10]}
    for entry_name, ent in entries.items():
        ent = ent[ent["vol_next"] >= 10 * size]
        f = trend_features(closes, ent["ts"].to_numpy())
        d = ent.reset_index(drop=True).join(f.reset_index(drop=True))
        d["y"] = d["settlement_value"].astype(float)
        d["day"] = (d["ts"] // 86400).astype(int)
        recs += _evaluate(d, entry_name, fee, size)
    tab = pd.DataFrame(recs)
    tab["breakeven_win_rate"] = tab["avg_entry"] + fee.per_contract_array(tab["avg_entry"], size)
    tab.to_csv(out / "htf_signals.csv", index=False)
    base = {sp: float(dd["y"].mean()) for sp, dd in d.groupby("split")}
    start = tab[tab["entry"] == "window start"]
    early = start[(start["split"] == "early") & (start["n_trades"] >= 200)].sort_values("mean_c", ascending=False)
    best = early.iloc[0][["signal", "mode"]].to_dict() if len(early) else {}
    res = {"markets": int(len(entries["window start"])), "share_up_by_split": base, "best_on_early": best,
           "best_results": start[(start["signal"] == best.get("signal")) & (start["mode"] == best.get("mode"))]
           .drop(columns=["signal", "mode"]).to_dict("records"),
           "early_follow_signals_positive": int(((start["split"] == "early") & (start["mode"] == "follow") &
                                                 (start["mean_c"] > 0)).sum())}
    (out / "summary.json").write_text(json.dumps(res, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)),
                                      encoding="utf-8")
    with db.session() as conn:
        audit.log(conn, "analysis.crypto_htf", "complete", None, None, {"best": best})
    log.info("higher-timeframe trend test: %d markets; results in %s", res["markets"], out)
    return res


def _evaluate(d: pd.DataFrame, entry_name: str, fee: FeeModel, size: int) -> list[dict]:
    """Win rate and after-fee profit for each trend signal, followed and faded, by period."""
    s5, s15, s1h = np.sign(d["ret_5m"]), np.sign(d["ret_15m"]), np.sign(d["ret_1h"])
    s3h, s12h = np.sign(d["ret_3h"]), np.sign(d["ret_12h"])
    def agree(*cols):
        a = np.vstack([d[c].to_numpy() for c in cols])
        return np.where((a == a[0]).all(axis=0) & (a[0] != 0), a[0], 0)

    sig = {
        "swings: 5m+15m+1h all agree (2-bar swings)": agree("swing_5m_n2", "swing_15m_n2", "swing_60m_n2"),
        "swings: 5m+15m+1h all agree (3-bar swings)": agree("swing_5m_n3", "swing_15m_n3", "swing_60m_n3"),
        "swings: 5m+15m agree": agree("swing_5m_n2", "swing_15m_n2"),
        "swings: 15m+1h agree": agree("swing_15m_n2", "swing_60m_n2"),
        "swings: 1h only": agree("swing_60m_n2"),
        "swings: 15m only": agree("swing_15m_n2"),
        "swings: 5m only": agree("swing_5m_n2"),
        "your setup, short look (5m+15m+1h agree)": np.where((s5 == s15) & (s15 == s1h), s5, 0),
        "your setup, chart look (1h+3h+12h agree)": np.where((s1h == s3h) & (s3h == s12h), s1h, 0),
        "1h trend": np.sign(d["ret_1h"]),
        "4h trend": np.sign(d["ret_4h"]),
        "24h trend": np.sign(d["ret_24h"]),
        "above 50 EMA (15m)": np.where(d["above_ema_15m50"], 1, -1),
        "above 20 EMA (1h)": np.where(d["above_ema_1h20"], 1, -1),
        "1h and 4h agree": np.where(np.sign(d["ret_1h"]) == np.sign(d["ret_4h"]), np.sign(d["ret_1h"]), 0),
        "1h, 4h, 24h agree": np.where((np.sign(d["ret_1h"]) == np.sign(d["ret_4h"])) &
                                       (np.sign(d["ret_4h"]) == np.sign(d["ret_24h"])), np.sign(d["ret_1h"]), 0),
        "strong 1h trend (>0.5%)": np.where(d["ret_1h"].abs() > 0.005, np.sign(d["ret_1h"]), 0),
        "strong 4h trend (>1%)": np.where(d["ret_4h"].abs() > 0.01, np.sign(d["ret_4h"]), 0),
    }
    recs = []
    for name, s in sig.items():
        s = np.asarray(s, float)
        for flip, label in ((1, "follow"), (-1, "fade")):
            side = s * flip
            m = side != 0
            x = d[m]
            up = side[m] > 0
            entry = np.where(up, x["yes_ask"], 1 - x["yes_bid"])
            win = np.where(up, x["y"], 1 - x["y"])
            pnl = win - entry - fee.per_contract_array(entry, size)
            t = x.assign(entry=entry, win=win, pnl=pnl)
            for sp, tt in t.groupby("split"):
                recs.append({"entry": entry_name, "signal": name, "mode": label, "split": sp,
                             "win_rate": float(tt["win"].mean()), "avg_entry": float(tt["entry"].mean()),
                             **summarize(tt["pnl"], tt["day"], size)})
    return recs
