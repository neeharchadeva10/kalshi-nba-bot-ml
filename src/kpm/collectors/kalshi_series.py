"""Generic Kalshi series collector (non-sports): markets in a close-time range plus 1-minute candles
over each market's own trading window, from the historical or live tier as appropriate. Also loads the
Coinbase reference series for the same period."""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from kpm.clients.coinbase import CoinbaseClient
from kpm.clients.kalshi import KalshiClient
from kpm.collectors.common import is_done, median_spacing_s
from kpm.collectors.kalshi_history import _candle_row
from kpm.core import audit, db
from kpm.core.config import settings
from kpm.core.prices import parse_dollars
from kpm.core.timeutil import now_ts, parse_iso

log = logging.getLogger(__name__)
COMPONENT = "collector.kalshi_series"


def _row(m: dict, series: str, tier: str) -> dict:
    return {
        "platform": "kalshi", "contract_key": m["ticker"], "platform_market_id": m["ticker"],
        "event_key": m["event_ticker"], "series_key": series, "title": m.get("title"),
        "subtitle": m.get("yes_sub_title"), "description": None, "rules_primary": m.get("rules_primary"),
        "rules_secondary": m.get("rules_secondary"), "outcome_label": m.get("yes_sub_title"), "team_abbr": None,
        "game_id": None, "open_ts": parse_iso(m.get("open_time")), "close_ts": parse_iso(m.get("close_time")),
        "settled_ts": parse_iso(m.get("settlement_ts")), "status": m.get("status"), "result": m.get("result") or None,
        "settlement_value": parse_dollars(m.get("settlement_value_dollars")), "data_tier": tier,
        "raw_ref": f"kalshi:{tier}:markets:{m['ticker']}",
        "meta_json": json.dumps({"strike_type": m.get("strike_type"), "floor_strike": m.get("floor_strike"),
                                 "cap_strike": m.get("cap_strike"), "expiration_value": m.get("expiration_value"),
                                 "volume": m.get("volume_fp")}),
        "fetched_ts": now_ts(),
    }


def run(series: str | None = None) -> None:
    cfg = settings()
    c = cfg["crypto"]
    series = series or c["series"]
    lo, hi = parse_iso(c["start"]), parse_iso(c["fresh_end"])
    with db.session() as conn:
        client = KalshiClient(conn)
        cut = parse_iso(client.cutoff()["market_settled_ts"])
        found: dict[str, tuple[dict, str]] = {}
        for m in client.markets(series, historical=True, min_close_ts=lo, max_close_ts=min(hi, cut)):
            ct = parse_iso(m.get("close_time"))
            if ct and lo <= ct < hi:
                found[m["ticker"]] = (m, "historical")
        for m in client.markets(series, historical=False, status="settled", min_close_ts=max(lo, cut), max_close_ts=hi):
            ct = parse_iso(m.get("close_time"))
            if ct and lo <= ct < hi:
                found.setdefault(m["ticker"], (m, "live"))
        log.info("%s: %d settled markets with close in range", series, len(found))
        jobs = []
        for tk, (m, tier) in found.items():
            db.upsert(conn, "markets", _row(m, series, tier), ["platform", "contract_key"])
            mid = conn.execute("SELECT market_id FROM markets WHERE platform='kalshi' AND contract_key=?", (tk,)).fetchone()[0]
            if not is_done(conn, COMPONENT, "candles", tk):
                jobs.append((tk, mid, tier, parse_iso(m["open_time"]), parse_iso(m["close_time"])))
        conn.commit()
        audit.log(conn, COMPONENT, "discover", "series", series, {"markets": len(found), "to_fetch": len(jobs)})
        conn.commit()

        def fetch(job):
            tk, mid, tier, o, cl = job
            return job, client.candlesticks(series, tk, o, cl, 1, tier == "historical")

        with ThreadPoolExecutor(max_workers=cfg["kalshi"].get("workers", 6)) as pool:
            futs = [pool.submit(fetch, j) for j in jobs]
            for i, fut in enumerate(as_completed(futs), 1):
                (tk, mid, tier, o, cl), candles = fut.result()
                rows = [_candle_row(mid, x) for x in candles]
                db.insert_many(conn, "price_history", rows)
                conn.execute("UPDATE markets SET price_resolution_s=?, price_source='kalshi_candle' WHERE market_id=?",
                             (median_spacing_s([r["ts"] for r in rows]), mid))
                audit.log(conn, COMPONENT, "candles", "market", tk, {"n": len(rows), "tier": tier})
                client.http.flush_index(conn)
                conn.commit()
                if i % 500 == 0 or i == len(jobs):
                    log.info("%s candles %d/%d (fetches=%d retries=%d)", series, i, len(jobs),
                             client.http.stats["fetches"], client.http.stats["retries"])

        # Coinbase reference series covering the period plus the volatility lookback
        cb = CoinbaseClient(conn)
        start = lo - (c["vol_lookback_min"] + 30) * 60
        df = cb.candles(c["product"], start, hi)
        rows = [{"source": "coinbase", "symbol": c["product"], "ts": int(r.ts), "open": r.open, "high": r.high,
                 "low": r.low, "close": r.close, "volume": r.volume} for r in df.itertuples()]
        db.insert_many(conn, "reference_prices", rows)
        cb.http.flush_index(conn)
        audit.log(conn, COMPONENT, "reference_prices", "symbol", c["product"], {"n": len(rows)})
        log.info("coinbase %s: %d one-minute bars", c["product"], len(rows))
