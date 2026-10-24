"""H2 (pre-registered in reports/output/mechanical_edges/PREREGISTRATION.md): cross-market bounds between Kalshi's
hourly BTC price markets (KXBTCD, "above K at HH:00") and the BTC 15-minute market ending at the same HH:00.

Both settle on the simple average of the 60 one-second BRTI prints before HH:00 (Kalshi rules). With T = the
15-minute market's target (its own opening average):
  K <  T : "above K" can never be less likely than UP  -> buy KXBTCD(>K) YES + buy 15m DOWN pays >= $1 always
  K >= T : "above K" can never be more likely than UP  -> buy 15m UP + buy KXBTCD(>K) NO pays >= $1 always
A violation is a minute close in the final 15 minutes (the close itself excluded) where both legs bought at the ask,
plus exact taker fees, cost less than $1. Data: 1-minute candles of the 2 KXBTCD strikes below and 2 above T.
"""
from __future__ import annotations

import json
import logging
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from kpm.analysis.crypto15m import split_of
from kpm.clients.kalshi import KalshiClient
from kpm.collectors.common import done_ids
from kpm.collectors.kalshi_history import _candle_row
from kpm.collectors.kalshi_series import _row
from kpm.core import audit, config, db
from kpm.core.config import settings
from kpm.core.fees import FeeModel
from kpm.core.timeutil import parse_iso

log = logging.getLogger(__name__)
COMPONENT = "analysis.crypto_cross_bounds"
HOURLY = "KXBTCD"
LOOKBACK_MIN = 15
EPS = 1e-9


def hourly_event(ticker_15m: str) -> str:
    """'KXBTC15M-26OCT051400-00' -> 'KXBTCD-26OCT0514' (same ET date and hour)."""
    return f"{HOURLY}-{ticker_15m.split('-')[1][:-2]}"


def nearest_strikes(markets: list[dict], target: float, n: int = 2) -> list[dict]:
    ms = sorted((m for m in markets if m.get("floor_strike") is not None), key=lambda m: float(m["floor_strike"]))
    below = [m for m in ms if float(m["floor_strike"]) < target][-n:]
    above = [m for m in ms if float(m["floor_strike"]) >= target][:n]
    return below + above


def collect(workers: int | None = None) -> None:
    cfg = settings()
    c = cfg["crypto"]
    lo, hi = parse_iso(c["start"]), parse_iso(c["fresh_end"])
    with db.session() as conn:
        client = KalshiClient(conn)
        cut = parse_iso(client.cutoff()["market_settled_ts"])
        rows = conn.execute("SELECT contract_key, close_ts, meta_json FROM markets WHERE platform='kalshi' AND series_key=? "
                            "AND close_ts >= ? AND close_ts < ? AND close_ts % 3600 = 0", (c["series"], lo, hi)).fetchall()
        done = done_ids(conn, COMPONENT, "pair")
        targets = {r["contract_key"]: json.loads(r["meta_json"]).get("floor_strike") for r in rows}
        todo = [(r["contract_key"], int(r["close_ts"]), float(targets[r["contract_key"]])) for r in rows
                if r["contract_key"] not in done and targets[r["contract_key"]] is not None]
        if len(todo) + len(done) < len(rows):
            log.warning("cross bounds: %d hourly 15-minute markets have no target price and are skipped",
                        sum(t is None for t in targets.values()))
        random.Random(7).shuffle(todo)   # a partial download is still a uniform sample across periods
        log.info("cross bounds: %d of %d hourly 15-minute markets to pair", len(todo), len(rows))

        def get(job):
            tk, close, target = job
            hist = close < cut
            ms = list(client._paginate("/historical/markets" if hist else "/markets", "markets",
                                       {"event_ticker": hourly_event(tk), "limit": 1000}, use_cache=True))
            picked = nearest_strikes(ms, target)
            candles = {m["ticker"]: client.candlesticks(HOURLY, m["ticker"], close - LOOKBACK_MIN * 60, close, 1, hist)
                       for m in picked}
            return job, hist, picked, candles

        with ThreadPoolExecutor(max_workers=workers or cfg["kalshi"].get("workers", 6)) as pool:
            futs = [pool.submit(get, j) for j in todo]
            for i, f in enumerate(as_completed(futs), 1):
                try:
                    (tk, close, target), hist, picked, candles = f.result()
                except Exception as exc:   # e.g. an hour without an hourly event; logged, not fatal
                    log.warning("cross bounds pairing failed: %s", exc)
                    continue
                for m in picked:
                    db.upsert(conn, "markets", _row(m, HOURLY, "historical" if hist else "live"), ["platform", "contract_key"])
                    mid = conn.execute("SELECT market_id FROM markets WHERE platform='kalshi' AND contract_key=?",
                                       (m["ticker"],)).fetchone()[0]
                    db.insert_many(conn, "price_history", [_candle_row(mid, x) for x in candles[m["ticker"]]])
                audit.log(conn, COMPONENT, "pair", "market", tk, {"strikes": [m["ticker"] for m in picked]})
                client.http.flush_index(conn)
                conn.commit()
                if i % 250 == 0 or i == len(todo):
                    log.info("cross bounds %d/%d (fetches=%d retries=%d)", i, len(todo),
                             client.http.stats["fetches"], client.http.stats["retries"])


def pair_profits(q15: pd.DataFrame, qd: pd.DataFrame, target: float, strike: float, fee: FeeModel) -> pd.DataFrame:
    """Per minute: cost and after-fee profit of the riskless pair for one hourly strike. Quotes are YES bid/ask."""
    d = q15.merge(qd, on="ts", suffixes=("_15", "_d"))
    if strike < target:   # buy hourly YES at its ask + 15m DOWN at 1 - 15m YES bid
        a, b = d["yes_ask_d"], 1.0 - d["yes_bid_15"]
        kind = "hourly YES + 15m DOWN"
    else:                 # buy 15m UP at its ask + hourly NO at 1 - hourly YES bid
        a, b = d["yes_ask_15"], 1.0 - d["yes_bid_d"]
        kind = "15m UP + hourly NO"
    d["kind"] = kind
    d["cost"] = a + b
    d["fees"] = fee.per_contract_array(a.clip(0, 1), 1) + fee.per_contract_array(b.clip(0, 1), 1)
    d["profit"] = 1.0 - d["cost"] - d["fees"]
    return d


def _clean(q: pd.DataFrame) -> pd.DataFrame:
    ok = (q["yes_bid"] > 0) & (q["yes_ask"] < 1) & (q["yes_bid"] < q["yes_ask"])
    return q[ok]


def run() -> dict:
    cfg = settings()
    c = cfg["crypto"]
    fee = FeeModel(rounding="micro_ceil")
    out = config.path("report_dir") / "mechanical_edges"
    out.mkdir(parents=True, exist_ok=True)
    with db.session() as conn:
        m15 = pd.read_sql_query("SELECT market_id, contract_key, close_ts, settlement_value, meta_json FROM markets "
                                "WHERE platform='kalshi' AND series_key=? AND close_ts % 3600 = 0", conn,
                                params=(c["series"],))
        md = pd.read_sql_query("SELECT market_id, contract_key, close_ts, settlement_value, meta_json FROM markets "
                               "WHERE platform='kalshi' AND series_key=?", conn, params=(HOURLY,))
        ids = tuple(int(x) for x in pd.concat([m15["market_id"], md["market_id"]]))
        q = pd.read_sql_query(f"SELECT market_id, ts, yes_bid, yes_ask FROM price_history WHERE source='kalshi_candle' "
                              f"AND market_id IN ({','.join('?' * len(ids))})", conn, params=ids) if ids else pd.DataFrame()
    for m in (m15, md):
        m["strike"] = pd.to_numeric(m["meta_json"].map(lambda s: json.loads(s).get("floor_strike")), errors="coerce")
    m15 = m15[m15["strike"].notna()]
    md = md[md["close_ts"].isin(m15["close_ts"])]
    rows = []
    q = _clean(q)
    qb = {mid: g.drop(columns="market_id") for mid, g in q.groupby("market_id")}
    for r in m15[m15["close_ts"].isin(md["close_ts"])].itertuples():
        close = int(r.close_ts)
        q15 = qb.get(r.market_id)
        if q15 is None:
            continue
        q15 = q15[(q15["ts"] > close - LOOKBACK_MIN * 60) & (q15["ts"] < close)]
        for h in md[md["close_ts"] == close].itertuples():
            qd = qb.get(h.market_id)
            if qd is None:
                continue
            d = pair_profits(q15, qd, float(r.strike), float(h.strike), fee)
            if d.empty:
                continue
            up, above = float(r.settlement_value), float(h.settlement_value)
            payout = (above + (1 - up)) if h.strike < r.strike else (up + (1 - above))
            d = d.assign(hour=r.contract_key, close_ts=close, strike=h.strike, target=r.strike,
                         gap=float(h.strike) - float(r.strike), payout=payout, split=split_of(close, c),
                         day=close // 86400)
            rows.append(d)
    allp = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if allp.empty:
        res = {"pairs": 0}
    else:
        allp["realized"] = allp["payout"] - allp["cost"] - allp["fees"]   # what the pair actually returned
        v = allp[allp["profit"] > EPS].sort_values(["hour", "strike", "ts"])
        nxt = allp.set_index(["hour", "strike", "ts"])["profit"]
        keys = list(zip(v["hour"], v["strike"], v["ts"] + 60))
        v = v.assign(persists=[bool(nxt.get(k, -1) > EPS) for k in keys])
        first = v.groupby(["hour", "strike"]).head(1)   # one trade per pair and hour
        by = []
        for sp, d in [*allp.groupby("split"), ("all", allp)]:
            vv, ff = v[v["split"] == sp] if sp != "all" else v, first[first["split"] == sp] if sp != "all" else first
            by.append({"split": sp, "hours": int(d["hour"].nunique()), "snapshots": int(len(d)),
                       "violations": int(len(vv)), "hours_with_violation": int(vv["hour"].nunique()),
                       "mean_profit_c": 100 * float(ff["profit"].mean()) if len(ff) else np.nan,
                       "realized_mean_c": 100 * float(ff["realized"].mean()) if len(ff) else np.nan,
                       "persist_next_min": float(vv["persists"].mean()) if len(vv) else np.nan,
                       "usd_per_day_1_contract": float(ff["realized"].sum() / max(1, d["day"].nunique())),
                       "payout_below_1": int((d.drop_duplicates(["hour", "strike"])["payout"] < 1).sum())})
        summ = pd.DataFrame(by)
        summ.to_csv(out / "h2_cross_bounds_summary.csv", index=False)
        v.to_csv(out / "h2_cross_bounds_violations.csv", index=False)
        res = {"pairs": int(allp.groupby(["hour", "strike"]).ngroups), "by_split": summ.to_dict("records")}
    (out / "h2_summary.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    with db.session() as conn:
        audit.log(conn, COMPONENT, "analysis", None, None, {"pairs": res.get("pairs", 0)})
    log.info("H2 cross bounds: %s", res)
    return res
