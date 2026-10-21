"""Polymarket US NFL moneyline markets and 1-minute book-derived prices (read-only).

Each Polymarket US game market has a long side (one team) and a short side (the other team).
Both are stored as separate team contracts so they pair one-to-one with Kalshi's team markets:
  long contract:  yes_ask = longPrice,  yes_bid = 1 - shortPrice
  short contract: yes_ask = shortPrice, yes_bid = 1 - longPrice
"""
from __future__ import annotations

import json
import logging

from kpm.agents.matcher.rules import find_game
from kpm.clients.polymarket_us import PolymarketUSClient
from kpm.collectors.common import is_done, load_games, median_spacing_s
from kpm.core import audit, db
from kpm.core.config import settings
from kpm.core.teams import norm_code, poly_slug_teams
from kpm.core.timeutil import now_ts, parse_iso, to_iso

log = logging.getLogger(__name__)
COMPONENT = "collector.polymarket_us"
SOURCE = "polyus_book"


def side_quotes(long_price: float | None, short_price: float | None) -> dict:
    """Per-side ask/bid from Polymarket US display prices (see module docstring)."""
    if long_price is None or short_price is None:
        return {"long": (None, None), "short": (None, None)}
    lp, sp = float(long_price), float(short_price)
    return {"long": (lp, 1.0 - sp), "short": (sp, 1.0 - lp)}  # (ask, bid)


def _settle_values(s) -> tuple[float | None, float | None]:
    if s is None or s == "":
        return None, None
    v = float(s)
    return v, 1.0 - v


def _result(v: float | None) -> str | None:
    if v is None:
        return None
    return "yes" if v >= 0.999 else "no" if v <= 0.001 else "split"


def _infer_long_settlement(conn, game_id: str, sides: dict) -> float | None:
    """Long-side payoff from the nflverse final score (winner 1, loser 0, tie 0.5)."""
    g = conn.execute("SELECT home_team, away_team, home_score, away_score FROM games WHERE game_id=?",
                     (game_id,)).fetchone()
    long_team = norm_code(((sides.get("long") or {}).get("team") or {}).get("abbreviation"))
    if g is None or g["home_score"] is None or long_team not in (g["home_team"], g["away_team"]):
        return None
    if g["home_score"] == g["away_score"]:
        return 0.5
    winner = g["home_team"] if g["home_score"] > g["away_score"] else g["away_team"]
    return 1.0 if long_team == winner else 0.0


def discover(client: PolymarketUSClient, conn) -> list[tuple[dict, object]]:
    games = load_games(conn)
    lo = to_iso(min(g.kickoff_ts for g in games) - 21 * 86400)
    hi = to_iso(max(g.kickoff_ts for g in games) + 3 * 86400)
    found: dict[str, list[dict]] = {}
    for m in client.markets(lo, hi, closed=True):
        slug = m.get("slug") or ""
        if not slug.startswith("aec-nfl-") or (m.get("sportsMarketType") or "").lower() != "moneyline":
            continue
        away, home, date = poly_slug_teams(slug)
        g = find_game(games, frozenset(x for x in (away, home) if x), date) if away and home else None
        if g is None:
            audit.log(conn, COMPONENT, "no_game", "market", slug, {"question": m.get("question")})
            continue
        found.setdefault(g.game_id, []).append(m)
    games_by_id = {g.game_id: g for g in games}
    out = []
    for gid, ms in found.items():
        g = games_by_id[gid]
        # Flexed games can have a market under the original date and one under the played date:
        # prefer the slug dated on the actual (ET) game date, then the earliest listing.
        ms = sorted(ms, key=lambda x: (poly_slug_teams(x["slug"])[2] != g.et_date, x.get("startDate") or ""))
        if len(ms) > 1:  # keep one market per game; log the rest for audit
            audit.log(conn, COMPONENT, "duplicate_markets", "game", gid,
                      {"kept": ms[0]["slug"], "dropped": [x["slug"] for x in ms[1:]]})
        out.append((ms[0], g))
    audit.log(conn, COMPONENT, "discover", "season", settings()["season"],
              {"games_with_market": len(out), "games_total": len(games)})
    log.info("Polymarket US: NFL moneyline markets for %d of %d games", len(out), len(games))
    return out


def run(force: bool = False) -> None:
    cfg = settings()
    w_before, w_after = cfg["collection"]["window_before_kickoff_s"], cfg["collection"]["window_after_kickoff_s"]
    with db.session() as conn:
        client = PolymarketUSClient(conn)
        items = discover(client, conn)
        conn.commit()
        for i, (m, game) in enumerate(items, 1):
            slug = m["slug"]
            detail = client.market(slug)
            sides = {("long" if s.get("long") else "short"): s for s in detail.get("marketSides", [])}
            raw_settle = client.settlement(slug)
            settle_inferred = False
            if raw_settle is None:  # no exchange record: infer from the official final score, and flag it
                raw_settle = _infer_long_settlement(conn, game.game_id, sides)
                settle_inferred = raw_settle is not None
                audit.log(conn, COMPONENT, "settlement_missing", "market", slug,
                          {"inferred_long_settlement": raw_settle})
            settle_long, settle_short = _settle_values(raw_settle)
            ids = {}
            for side in ("long", "short"):
                s = sides.get(side) or {}
                team = s.get("team") or {}
                value = settle_long if side == "long" else settle_short
                row = {
                    "platform": "polymarket_us", "contract_key": f"{slug}:{side}", "platform_market_id": slug,
                    "event_key": slug, "series_key": "polymarket_us:nfl",
                    "title": detail.get("question"), "subtitle": team.get("name"),
                    "description": detail.get("description"), "rules_primary": detail.get("description"),
                    "rules_secondary": json.dumps({"resolution_source": "https://www.nfl.com/",
                                                   "side": side, "team_name": team.get("name")}),
                    "outcome_label": s.get("description") or team.get("alias"),
                    "team_abbr": norm_code(team.get("abbreviation")),
                    "open_ts": parse_iso(detail.get("startDate")), "close_ts": parse_iso(detail.get("endDate")),
                    "settled_ts": parse_iso(detail.get("endDate")), "status": "closed" if detail.get("closed") else None,
                    "result": _result(value), "settlement_value": value, "data_tier": None,
                    "raw_ref": f"polymarket_us:market:{slug}",
                    "meta_json": json.dumps({"sports_market_type": detail.get("sportsMarketType"),
                                             "game_start_ts": parse_iso(detail.get("gameStartTime")),
                                             "market_id": detail.get("id"), "side": side,
                                             "tick_size": detail.get("orderPriceMinTickSize"),
                                             "settlement_inferred": settle_inferred}),
                    "fetched_ts": now_ts(),
                }
                db.upsert(conn, "markets", row, ["platform", "contract_key"])
                ids[side] = conn.execute("SELECT market_id FROM markets WHERE platform='polymarket_us' AND "
                                         "contract_key=?", (row["contract_key"],)).fetchone()[0]
            conn.commit()  # never hold a write lock across network calls
            if not force and is_done(conn, COMPONENT, "prices", slug):
                continue
            start = max(game.kickoff_ts - w_before, parse_iso(detail.get("startDate")) or 0)
            end = game.kickoff_ts + w_after
            hist = client.price_history(slug, start, end)
            rows = []
            for p in hist:
                q = side_quotes(p.get("longPrice"), p.get("shortPrice"))
                for side in ("long", "short"):
                    ask, bid = q[side]
                    rows.append({"market_id": ids[side], "ts": int(p["timestamp"]), "interval_s": 60, "source": SOURCE,
                                 "yes_bid": bid, "yes_ask": ask, "yes_bid_low": None, "yes_ask_high": None,
                                 "price_last": None, "price_mean": None if ask is None else (ask + bid) / 2,
                                 "volume": None, "open_interest": None})
            db.insert_many(conn, "price_history", rows)
            res = median_spacing_s([int(p["timestamp"]) for p in hist])
            conn.execute("UPDATE markets SET price_resolution_s=?, price_source=? WHERE market_id IN (?,?)",
                         (res, SOURCE, ids["long"], ids["short"]))
            audit.log(conn, COMPONENT, "prices", "market", slug,
                      {"n_points": len(hist), "median_spacing_s": res, "start": start, "end": end,
                       "settlement_long": settle_long})
            conn.commit()
            if i % 20 == 0 or i == len(items):
                log.info("Polymarket US %d/%d markets done (cache hits=%d fetches=%d)", i, len(items),
                         client.http.stats["hits"], client.http.stats["fetches"])
