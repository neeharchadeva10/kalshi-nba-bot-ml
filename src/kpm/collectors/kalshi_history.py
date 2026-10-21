"""Step 2: Kalshi NFL game-winner markets, rules, settlement, 1-min candles, trades."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from kpm.agents.matcher.rules import find_game, kalshi_event_teams, parse_kalshi_event_date
from kpm.clients.kalshi import KalshiClient
from kpm.collectors.common import is_done, load_games, median_spacing_s
from kpm.core import audit, db
from kpm.core.config import settings
from kpm.core.prices import parse_dollars
from kpm.core.teams import kalshi_ticker_team
from kpm.core.timeutil import ceil_to, now_ts, parse_iso

log = logging.getLogger(__name__)
COMPONENT = "collector.kalshi"


def _market_row(m: dict, tier: str) -> dict:
    return {
        "platform": "kalshi", "contract_key": m["ticker"], "platform_market_id": m["ticker"],
        "event_key": m["event_ticker"], "series_key": settings()["kalshi"]["series_ticker"],
        "title": m.get("title"), "subtitle": m.get("yes_sub_title"), "description": None,
        "rules_primary": m.get("rules_primary"), "rules_secondary": m.get("rules_secondary"),
        "outcome_label": m.get("yes_sub_title"), "team_abbr": kalshi_ticker_team(m["ticker"]),
        "open_ts": parse_iso(m.get("open_time")), "close_ts": parse_iso(m.get("close_time")),
        "settled_ts": parse_iso(m.get("settlement_ts")), "status": m.get("status"),
        "result": m.get("result") or None,
        "settlement_value": parse_dollars(m.get("settlement_value_dollars")),
        "data_tier": tier, "raw_ref": f"kalshi:{tier}:markets:{m['ticker']}",
        "meta_json": json.dumps({
            "market_type": m.get("market_type"),
            "expected_expiration_ts": parse_iso(m.get("expected_expiration_time")),
            "occurrence_ts": parse_iso(m.get("occurrence_datetime")),
            "early_close_condition": m.get("early_close_condition"),
            "price_level_structure": m.get("price_level_structure"),
            "no_sub_title": m.get("no_sub_title"),
        }),
        "fetched_ts": now_ts(),
    }


def _field(d: dict, name: str):
    """Historical candles use 'close'/'volume'; the live endpoint uses 'close_dollars'/'volume_fp'."""
    for key in (name, f"{name}_dollars", f"{name}_fp"):
        if d.get(key) is not None:
            return d[key]
    return None


def _candle_row(market_id: int, c: dict) -> dict:
    yb, ya, pr = c.get("yes_bid") or {}, c.get("yes_ask") or {}, c.get("price") or {}
    return {
        "market_id": market_id, "ts": int(c["end_period_ts"]), "interval_s": 60, "source": "kalshi_candle",
        "yes_bid": parse_dollars(_field(yb, "close")), "yes_ask": parse_dollars(_field(ya, "close")),
        "yes_bid_low": parse_dollars(_field(yb, "low")), "yes_ask_high": parse_dollars(_field(ya, "high")),
        "price_last": parse_dollars(_field(pr, "close")), "price_mean": parse_dollars(_field(pr, "mean")),
        "volume": parse_dollars(_field(c, "volume")), "open_interest": parse_dollars(_field(c, "open_interest")),
    }


def discover(client: KalshiClient, conn) -> list[dict]:
    """All KXNFLGAME markets that belong to a regular-season/playoff game in the target season."""
    cfg = settings()
    series = cfg["kalshi"]["series_ticker"]
    games = load_games(conn)
    lo = min(g.kickoff_ts for g in games) - 3 * 86400
    hi = max(g.kickoff_ts for g in games) + 3 * 86400

    raw: dict[str, tuple[dict, str]] = {}
    for m in client.markets(series, historical=True):
        raw[m["ticker"]] = (m, "historical")
    for m in client.markets(series, historical=False, status="settled"):
        raw.setdefault(m["ticker"], (m, "live"))

    by_event: dict[str, list[str]] = defaultdict(list)
    for t, (m, _) in raw.items():
        by_event[m["event_ticker"]].append(t)

    per_game: dict[str, list[tuple[str, list[str], float]]] = defaultdict(list)
    skipped = 0
    for ev, tickers in by_event.items():
        date = parse_kalshi_event_date(ev)
        teams = kalshi_event_teams(ev, tickers)
        game = find_game(games, teams, date) if len(teams) == 2 else None
        if game is None or not (lo <= game.kickoff_ts <= hi):
            skipped += len(tickers)
            continue
        vol = sum(float(raw[t][0].get("volume_fp") or 0) for t in tickers)
        per_game[game.game_id].append((ev, tickers, vol))

    games_by_id = {g.game_id: g for g in games}
    kept, dropped = [], []
    for gid, evs in per_game.items():
        evs.sort(key=lambda x: -x[2])
        best = evs[0]
        # Duplicate or shell events (zero traded volume) are not the real market.
        for ev, tickers, vol in evs[1:] + ([best] if best[2] == 0 else []):
            dropped.append({"game_id": gid, "event": ev, "volume": vol})
        if best[2] == 0:
            continue
        for t in best[1]:
            kept.append({"market": raw[t][0], "tier": raw[t][1], "game": games_by_id[gid]})
    no_market = sorted(set(games_by_id) - {k["game"].game_id for k in kept})
    for d in dropped:
        audit.log(conn, COMPONENT, "drop_event", "event", d["event"], d)
    audit.log(conn, COMPONENT, "discover", "series", series,
              {"listed": len(raw), "kept": len(kept), "skipped_not_in_season": skipped,
               "dropped_events": dropped, "games_without_market": no_market, "cutoff": client.cutoff()})
    if no_market:
        log.warning("Kalshi: no usable %s market for games %s", series, no_market)
    log.info("Kalshi: %d markets listed, %d in season, %d skipped (preseason/other)", len(raw), len(kept), skipped)
    return kept


def run(with_trades: bool = True, force: bool = False, limit: int | None = None) -> None:
    cfg = settings()
    w_before, w_after = cfg["collection"]["window_before_kickoff_s"], cfg["collection"]["window_after_kickoff_s"]
    with db.session() as conn:
        client = KalshiClient(conn)
        series = client.series(cfg["kalshi"]["series_ticker"])
        audit.log(conn, COMPONENT, "series_metadata", "series", series["ticker"],
                  {"fee_type": series.get("fee_type"), "fee_multiplier": series.get("fee_multiplier"),
                   "contract_terms_url": series.get("contract_terms_url")})
        items = discover(client, conn)
        conn.commit()
        if limit:
            items = items[:limit]
        jobs = []
        for it in items:
            m, tier, game = it["market"], it["tier"], it["game"]
            db.upsert(conn, "markets", _market_row(m, tier), ["platform", "contract_key"])
            mid = conn.execute("SELECT market_id FROM markets WHERE platform='kalshi' AND contract_key=?",
                               (m["ticker"],)).fetchone()[0]
            conn.execute("UPDATE markets SET game_id=? WHERE market_id=?", (game.game_id, mid))
            jobs.append({
                "ticker": m["ticker"], "mid": mid, "tier": tier, "hist": tier == "historical",
                "start": max(game.kickoff_ts - w_before, (parse_iso(m.get("open_time")) or 0) // 60 * 60),
                "end": game.kickoff_ts + w_after,
                "do_candles": force or not is_done(conn, COMPONENT, "candles", m["ticker"]),
                "do_trades": with_trades and (force or not is_done(conn, COMPONENT, "trades", m["ticker"])),
            })
        conn.commit()  # never hold a write lock across network calls
        client.cutoff()  # fetch once before threads start

        def fetch(job: dict) -> dict:
            """Network only (runs in worker threads); the main thread does all DB writes."""
            out = {"job": job, "candles": None, "trades": None, "n_trades": 0}
            if job["do_candles"]:
                out["candles"] = client.candlesticks(series["ticker"], job["ticker"], job["start"], job["end"],
                                                     cfg["kalshi"]["candle_interval_min"], job["hist"])
            if job["do_trades"]:
                agg: dict[tuple, list] = defaultdict(lambda: [0.0, 0])
                for t in client.trades(job["ticker"], job["start"], job["end"], client.trades_historical(job["end"])):
                    key = (ceil_to(parse_iso(t["created_time"]), 60), parse_dollars(t["yes_price_dollars"]),
                           t.get("taker_side") or "")
                    agg[key][0] += float(t.get("count_fp") or t.get("count") or 0)
                    agg[key][1] += 1
                    out["n_trades"] += 1
                out["trades"] = agg
            return out

        todo = [j for j in jobs if j["do_candles"] or j["do_trades"]]
        log.info("Kalshi: %d markets need data (%d already complete)", len(todo), len(jobs) - len(todo))
        with ThreadPoolExecutor(max_workers=cfg["kalshi"].get("workers", 8)) as pool:
            futures = [pool.submit(fetch, j) for j in todo]
            for i, fut in enumerate(as_completed(futures), 1):
                r = fut.result()
                job, mid = r["job"], r["job"]["mid"]
                if r["candles"] is not None:
                    rows = [_candle_row(mid, c) for c in r["candles"]]
                    db.insert_many(conn, "price_history", rows)
                    res = median_spacing_s([x["ts"] for x in rows])
                    conn.execute("UPDATE markets SET price_resolution_s=?, price_source='kalshi_candle' "
                                 "WHERE market_id=?", (res, mid))
                    audit.log(conn, COMPONENT, "candles", "market", job["ticker"],
                              {"n": len(rows), "start": job["start"], "end": job["end"], "median_spacing_s": res,
                               "tier": job["tier"]})
                if r["trades"] is not None:
                    rows = [{"market_id": mid, "minute_ts": k[0], "yes_price": k[1], "taker_side": k[2],
                             "count": v[0], "n_trades": v[1]} for k, v in r["trades"].items()]
                    db.insert_many(conn, "trades_1m", rows)
                    audit.log(conn, COMPONENT, "trades", "market", job["ticker"],
                              {"n_trades": r["n_trades"], "n_rows": len(rows)})
                client.http.flush_index(conn)
                conn.commit()
                if i % 20 == 0 or i == len(todo):
                    log.info("Kalshi %d/%d markets done (cache hits=%d fetches=%d retries=%d)", i, len(todo),
                             client.http.stats["hits"], client.http.stats["fetches"], client.http.stats["retries"])
