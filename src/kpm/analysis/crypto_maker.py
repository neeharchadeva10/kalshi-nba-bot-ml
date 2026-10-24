"""Resting limit bids on BTC 15-minute favorites priced 90-95c with 4-10 minutes left.

Signal: first minute in a market where the favorite's mid is in [0.90, 0.95) and 4 < minutes left <= 10.
Order : bid for the favorite at its current best bid (YES bid if YES is the favorite; NO bid = 1 - YES ask if NO
        is), resting until 2 minutes before the close, 100 contracts.
Fills  from Kalshi trade prints after the order is placed (aggregated per minute, by price and taker side):
  YES favorite: takers SELLING YES (taker_side 'no') at yes_price <= our bid
  NO favorite : takers BUYING YES (taker_side 'yes') at yes_price >= 1 - our NO bid
  generous  : any eligible volume fills us (up to 100)
  realistic : eligible volume must reach 5x our size (we joined the back of the queue)
  strict    : only prints strictly through our price
Maker fee 0 (series not flagged for maker fees); a sensitivity row adds Kalshi's maker rate (0.0175*p*(1-p)).
Compared with buying the same signals at the ask as a taker (taker fee).
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

import numpy as np
import pandas as pd

from kpm.analysis.crypto15m import summarize
from kpm.clients.kalshi import KalshiClient
from kpm.collectors.common import is_done
from kpm.core import audit, config, db
from kpm.core.config import settings
from kpm.core.fees import FeeModel
from kpm.core.prices import parse_dollars
from kpm.core.timeutil import ceil_to, parse_iso

log = logging.getLogger(__name__)
COMPONENT = "analysis.crypto_maker"
RULES = ("generous", "realistic", "strict")


def signals(rows: pd.DataFrame) -> pd.DataFrame:
    r = rows.copy()
    mid = (r["yes_bid"] + r["yes_ask"]) / 2
    r["fav_yes"] = mid >= 0.5
    r["fav_mid"] = np.where(r["fav_yes"], mid, 1 - mid)
    r["fav_bid"] = np.where(r["fav_yes"], r["yes_bid"], 1 - r["yes_ask"])
    r["fav_ask"] = np.where(r["fav_yes"], r["yes_ask"], 1 - r["yes_bid"])
    m = (r["fav_mid"] >= 0.90) & (r["fav_mid"] < 0.95) & (r["tau"] > 4) & (r["tau"] <= 10)
    s = r[m].sort_values(["market_id", "ts"]).groupby("market_id").head(1)
    s["fav_win"] = np.where(s["fav_yes"], s["settlement_value"], 1 - s["settlement_value"]).astype(float)
    return s


def maker_fill(tr: pd.DataFrame, t0: int, cancel_ts: int, fav_yes: bool, bid: float, rule: str, size: int) -> int:
    """Contracts filled for our resting favorite bid, from per-minute trade aggregates."""
    if tr.empty:
        return 0
    w = tr[(tr["minute_ts"] > t0) & (tr["minute_ts"] <= cancel_ts)]
    if fav_yes:
        sells = w[w["taker_side"] == "no"]
        at = sells.loc[sells["yes_price"] <= bid + 1e-9, "count"].sum()
        through = sells.loc[sells["yes_price"] < bid - 1e-9, "count"].sum()
    else:
        level = 1.0 - bid  # our NO bid at `bid` is hit by takers buying YES at >= 1 - bid
        buys = w[w["taker_side"] == "yes"]
        at = buys.loc[buys["yes_price"] >= level - 1e-9, "count"].sum()
        through = buys.loc[buys["yes_price"] > level + 1e-9, "count"].sum()
    if rule == "generous":
        return int(min(size, at))
    if rule == "strict":
        return int(min(size, through))
    return size if at >= 5 * size else 0


def fetch_trades(sig: pd.DataFrame, series: str) -> None:
    """Download trade prints from each signal minute to the close and store per-minute aggregates."""
    with db.session() as conn:
        client = KalshiClient(conn)
        cut_trades = parse_iso(client.cutoff()["trades_created_ts"])
        tick = dict(conn.execute("SELECT market_id, contract_key FROM markets WHERE series_key=?", (series,)).fetchall())
        todo = [(int(r.market_id), tick[int(r.market_id)], int(r.ts), int(r.close_ts)) for r in sig.itertuples()
                if not is_done(conn, COMPONENT, "trades", tick[int(r.market_id)])]

        def get(job):
            mid, tk, t0, close = job
            agg: dict[tuple, list] = defaultdict(lambda: [0.0, 0])
            for t in client.trades(tk, t0, close, close < cut_trades):
                key = (ceil_to(parse_iso(t["created_time"]), 60), parse_dollars(t["yes_price_dollars"]),
                       t.get("taker_side") or "")
                agg[key][0] += float(t.get("count_fp") or t.get("count") or 0)
                agg[key][1] += 1
            return job, agg

        log.info("crypto maker: fetching trades for %d markets", len(todo))
        with ThreadPoolExecutor(max_workers=settings()["kalshi"].get("workers", 6)) as pool:
            futs = [pool.submit(get, j) for j in todo]
            for i, f in enumerate(as_completed(futs), 1):
                (mid, tk, t0, close), agg = f.result()
                rows = [{"market_id": mid, "minute_ts": k[0], "yes_price": k[1], "taker_side": k[2], "count": v[0],
                         "n_trades": v[1]} for k, v in agg.items()]
                db.insert_many(conn, "trades_1m", rows)
                audit.log(conn, COMPONENT, "trades", "market", tk, {"rows": len(rows), "from_ts": t0})
                client.http.flush_index(conn)
                conn.commit()
                if i % 200 == 0 or i == len(todo):
                    log.info("crypto maker trades %d/%d (fetches=%d retries=%d)", i, len(todo),
                             client.http.stats["fetches"], client.http.stats["retries"])


def run(sample_per_split: int = 500, seed: int = 7) -> dict:
    cfg = settings()
    c = cfg["crypto"]
    size = c["order_size"]
    taker = FeeModel.from_config(cfg)
    maker_sens = FeeModel(0.07, 0.25, "cent_ceil")
    out = config.path("report_dir") / "crypto_maker"
    out.mkdir(parents=True, exist_ok=True)
    rows = pd.read_parquet(config.path("report_dir") / "crypto15m" / "decision_rows.parquet")
    sig = signals(rows)
    # pandas 3 drops the grouping column inside groupby.apply, so sample each period explicitly
    sig = pd.concat([d.sample(min(len(d), sample_per_split), random_state=seed) for _, d in sig.groupby("split")])
    fetch_trades(sig, c["series"])
    with db.session() as conn:
        ids = ",".join(str(int(x)) for x in sig["market_id"])
        tr = pd.read_sql_query(f"SELECT market_id, minute_ts, yes_price, taker_side, count FROM trades_1m "
                               f"WHERE market_id IN ({ids})", conn)
    trades = {k: v for k, v in tr.groupby("market_id")}

    recs = []
    for r in sig.itertuples():
        t_mk = trades.get(int(r.market_id), pd.DataFrame())
        cancel = int(r.close_ts) - 120
        taker_pnl = r.fav_win - r.fav_ask - taker.per_contract(float(r.fav_ask), size)
        rec = {"market_id": int(r.market_id), "split": r.split, "day": int(r.ts // 86400), "tau": float(r.tau),
               "fav_bid": float(r.fav_bid), "fav_ask": float(r.fav_ask), "fav_win": float(r.fav_win),
               "taker_pnl": taker_pnl}
        for rule in RULES:
            n = maker_fill(t_mk, int(r.ts), cancel, bool(r.fav_yes), float(r.fav_bid), rule, size)
            rec[f"filled_{rule}"] = n
            rec[f"maker_pnl_{rule}"] = (r.fav_win - r.fav_bid) if n > 0 else np.nan
            rec[f"maker_pnl_fee_{rule}"] = ((r.fav_win - r.fav_bid - maker_sens.per_contract(float(r.fav_bid), n, maker=True))
                                            if n > 0 else np.nan)
        recs.append(rec)
    res_df = pd.DataFrame(recs)
    res_df.to_csv(out / "signals.csv", index=False)

    summary = []
    for sp, d in res_df.groupby("split"):
        row = {"split": sp, "signals": len(d), "taker": summarize(d["taker_pnl"], d["day"], size),
               "favorite_win_rate_all": float(d["fav_win"].mean())}
        for rule in RULES:
            f = d[d[f"filled_{rule}"] > 0]
            row[rule] = {"fill_rate": float((d[f"filled_{rule}"] > 0).mean()),
                         "favorite_win_rate_when_filled": float(f["fav_win"].mean()) if len(f) else np.nan,
                         "maker_no_fee": summarize(f[f"maker_pnl_{rule}"], f["day"], size),
                         "maker_with_fee": summarize(f[f"maker_pnl_fee_{rule}"], f["day"], size),
                         "taker_same_signals": summarize(f["taker_pnl"], f["day"], size)}
        summary.append(row)
    result = {"sample_per_split": sample_per_split, "summary": summary}
    (out / "summary.json").write_text(json.dumps(result, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)),
                                      encoding="utf-8")
    with db.session() as conn:
        audit.log(conn, COMPONENT, "complete", None, None, {"signals": len(res_df)})
    log.info("crypto maker test: %d signals; results in %s", len(res_df), out)
    return result
