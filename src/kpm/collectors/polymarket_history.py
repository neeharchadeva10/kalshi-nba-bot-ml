"""Step 3: Polymarket NFL game moneyline markets (Gamma) and price history (CLOB).

If prices-history for a token is coarser than `fallback_if_resolution_over_s`, a 1-minute
last-trade series is built from the public Data API trades endpoint instead.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict

from kpm.agents.matcher.rules import find_game
from kpm.clients.polymarket import PolymarketClient
from kpm.collectors.common import is_done, load_games, median_spacing_s
from kpm.core import audit, db
from kpm.core.config import settings
from kpm.core.teams import from_name, poly_slug_teams
from kpm.core.timeutil import ceil_to, now_ts, parse_iso

log = logging.getLogger(__name__)
COMPONENT = "collector.polymarket"

# Slug spellings to try for games the series listing missed (nflverse code -> poly code).
_SLUG_CODE = {"LA": ["la", "lar"], "JAX": ["jax", "jac"], "WAS": ["was", "wsh"], "LV": ["lv"], "LAC": ["lac"]}


def _slug_codes(code: str) -> list[str]:
    return _SLUG_CODE.get(code, [code.lower()])


def _moneyline(event: dict) -> dict | None:
    mls = [m for m in event.get("markets", []) if m.get("sportsMarketType") == "moneyline"]
    if not mls:  # older events may lack sportsMarketType; the moneyline question equals the title
        mls = [m for m in event.get("markets", []) if (m.get("question") or "").strip() == (event.get("title") or "").strip()]
    return mls[0] if len(mls) == 1 else None


def _result(price: str | None) -> tuple[str | None, float | None]:
    if price is None:
        return None, None
    v = float(price)
    if v >= 0.999:
        return "yes", 1.0
    if v <= 0.001:
        return "no", 0.0
    return "split", v


def _token_rows(event: dict, mk: dict) -> list[dict]:
    outcomes = json.loads(mk.get("outcomes") or "[]")
    tokens = json.loads(mk.get("clobTokenIds") or "[]")
    prices = json.loads(mk.get("outcomePrices") or "[]") if mk.get("outcomePrices") else [None] * len(tokens)
    resolved = mk.get("umaResolutionStatus") == "resolved"
    rows = []
    for outcome, token, price in zip(outcomes, tokens, prices):
        result, value = _result(price) if resolved else (None, None)
        rows.append({
            "platform": "polymarket", "contract_key": token, "platform_market_id": mk["conditionId"],
            "event_key": event["slug"], "series_key": ",".join(str(s.get("id")) for s in event.get("series", [])),
            "title": mk.get("question"), "subtitle": event.get("title"),
            "description": mk.get("description"),
            "rules_primary": mk.get("description"),
            "rules_secondary": json.dumps({"event_description": event.get("description"),
                                           "resolution_source": mk.get("resolutionSource") or event.get("resolutionSource")}),
            "outcome_label": outcome, "team_abbr": from_name(outcome),
            "open_ts": parse_iso(mk.get("acceptingOrdersTimestamp") or mk.get("startDate")),
            "close_ts": parse_iso(mk.get("closedTime")), "settled_ts": parse_iso(mk.get("closedTime")),
            "status": mk.get("umaResolutionStatus"), "result": result, "settlement_value": value,
            "data_tier": None, "raw_ref": f"polymarket:gamma:event:{event['slug']}",
            "meta_json": json.dumps({
                "sports_market_type": mk.get("sportsMarketType"),
                "game_start_ts": parse_iso(mk.get("gameStartTime") or event.get("startTime")),
                "event_date": event.get("eventDate"), "neg_risk": mk.get("negRisk"),
                "tick_size": mk.get("orderPriceMinTickSize"), "final_score": event.get("score"),
                "outcomes": outcomes, "volume": mk.get("volume"),
            }),
            "fetched_ts": now_ts(),
        })
    return rows


def discover(client: PolymarketClient, conn) -> list[tuple[dict, object]]:
    cfg = settings()
    games = load_games(conn)
    found: dict[str, tuple[dict, object]] = {}
    for sid in cfg["polymarket"]["nfl_series_ids"]:
        for ev in client.series_events(sid, closed=True):
            away, home, date = poly_slug_teams(ev.get("slug", ""))
            if not (away and home):
                continue
            g = find_game(games, frozenset((away, home)), date)
            if g:
                found.setdefault(g.game_id, (ev, g))
    # Fill gaps by direct slug lookup (e.g. playoff games listed under another series).
    missing = [g for g in games if g.game_id not in found]
    for g in missing:
        for a in _slug_codes(g.away):
            for h in _slug_codes(g.home):
                ev = client.event_by_slug(f"nfl-{a}-{h}-{g.et_date}")
                if ev:
                    found[g.game_id] = (ev, g)
                    break
            if g.game_id in found:
                break
    still = [g.game_id for g in games if g.game_id not in found]
    audit.log(conn, COMPONENT, "discover", "season", cfg["season"],
              {"games": len(games), "found": len(found), "missing": still})
    log.info("Polymarket: found events for %d/%d games; missing %s", len(found), len(games), still)
    return list(found.values())


def _trades_series(client: PolymarketClient, condition_id: str, token: str, start: int, end: int) -> list[tuple[int, float, float]]:
    """1-minute last-trade price for `token` (other outcome's trades used as 1 - p)."""
    per_min: dict[int, tuple[int, float]] = {}
    vol: dict[int, float] = defaultdict(float)
    for t in client.trades(condition_id):
        ts = int(t["timestamp"])
        if not (start <= ts <= end) or t.get("price") is None:
            continue
        p = float(t["price"]) if t["asset"] == token else 1.0 - float(t["price"])
        m = ceil_to(ts, 60)
        if m not in per_min or ts >= per_min[m][0]:
            per_min[m] = (ts, p)
        vol[m] += float(t.get("size") or 0)
    return [(m, per_min[m][1], vol[m]) for m in sorted(per_min)]


def run(force: bool = False, limit: int | None = None) -> None:
    cfg = settings()
    pc = cfg["polymarket"]
    w_before, w_after = cfg["collection"]["window_before_kickoff_s"], cfg["collection"]["window_after_kickoff_s"]
    with db.session() as conn:
        client = PolymarketClient(conn)
        events = discover(client, conn)
        conn.commit()
        if limit:
            events = events[:limit]
        resolutions = []
        for i, (ev, game) in enumerate(events, 1):
            mk = _moneyline(ev)
            if mk is None:
                audit.log(conn, COMPONENT, "no_moneyline", "event", ev["slug"], {})
                continue
            for row in _token_rows(ev, mk):
                db.upsert(conn, "markets", row, ["platform", "contract_key"])
                mid = conn.execute("SELECT market_id FROM markets WHERE platform='polymarket' AND contract_key=?",
                                   (row["contract_key"],)).fetchone()[0]
                conn.commit()  # never hold a write lock across network calls
                if not force and is_done(conn, COMPONENT, "prices", row["contract_key"]):
                    continue
                start = max(game.kickoff_ts - w_before, (row["open_ts"] or 0))
                end = game.kickoff_ts + w_after
                hist = client.prices_history(row["contract_key"], start, end, pc["prices_history_fidelity_min"],
                                             pc["prices_history_chunk_s"])
                res = median_spacing_s([h["t"] for h in hist])
                source = "poly_prices_history"
                rows = [{"market_id": mid, "ts": h["t"], "interval_s": 60 * pc["prices_history_fidelity_min"],
                         "source": source, "yes_bid": None, "yes_ask": None, "yes_bid_low": None,
                         "yes_ask_high": None, "price_last": h["p"], "price_mean": h["p"], "volume": None,
                         "open_interest": None} for h in hist]
                fallback_used = False
                if res is None or res > pc["fallback_if_resolution_over_s"]:
                    tr = _trades_series(client, row["platform_market_id"], row["contract_key"], start, end)
                    tres = median_spacing_s([t[0] for t in tr])
                    if tr and (res is None or (tres or 1e9) < res):
                        fallback_used, source, res = True, "poly_trades_1m", tres
                        rows += [{"market_id": mid, "ts": m, "interval_s": 60, "source": source, "yes_bid": None,
                                  "yes_ask": None, "yes_bid_low": None, "yes_ask_high": None, "price_last": p,
                                  "price_mean": p, "volume": v, "open_interest": None} for m, p, v in tr]
                db.insert_many(conn, "price_history", rows)
                conn.execute("UPDATE markets SET price_resolution_s=?, price_source=? WHERE market_id=?",
                             (res, source, mid))
                resolutions.append(res)
                audit.log(conn, COMPONENT, "prices", "token", row["contract_key"],
                          {"slug": ev["slug"], "outcome": row["outcome_label"], "n_points": len(hist),
                           "median_spacing_s": res, "fallback_trades": fallback_used, "start": start, "end": end})
                conn.commit()
            conn.commit()
            if i % 20 == 0 or i == len(events):
                log.info("Polymarket %d/%d events done (cache hits=%d fetches=%d)", i, len(events),
                         client.clob.stats["hits"], client.clob.stats["fetches"])
