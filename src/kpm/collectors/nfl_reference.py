"""Step 4: NFL schedule, kickoff times (UTC), final scores, closing lines."""
from __future__ import annotations

import logging
import math

from kpm.clients.nfl import load_schedule
from kpm.core import audit, db
from kpm.core.config import settings
from kpm.core.prices import american_to_prob, devig_two_way
from kpm.core.teams import norm_code
from kpm.core.timeutil import et_to_utc_ts, now_ts

log = logging.getLogger(__name__)


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def split_for(week: int, game_type: str, dev_last_week: int) -> str:
    return "dev" if game_type == "REG" and week <= dev_last_week else "holdout"


def run(refresh: bool = False, season: int | None = None) -> int:
    """Load one season. The study season gets dev/holdout splits; any other season is labelled
    'fresh_<season>' so it can serve as an untouched test set."""
    cfg = settings()
    season = season or cfg["season"]
    primary = season == cfg["season"]
    df = load_schedule(season, refresh=refresh)
    df = df[df["game_type"] != "PRE"]
    n = 0
    with db.session() as conn:
        for r in df.to_dict("records"):
            hp, ap = american_to_prob(_num(r.get("home_moneyline"))), american_to_prob(_num(r.get("away_moneyline")))
            home_fair, _ = devig_two_way(hp, ap)
            hs, as_ = _num(r.get("home_score")), _num(r.get("away_score"))
            row = {
                "game_id": r["game_id"], "season": int(r["season"]), "week": int(r["week"]),
                "game_type": r["game_type"],
                "home_team": norm_code(r["home_team"]), "away_team": norm_code(r["away_team"]),
                "kickoff_ts": et_to_utc_ts(r["gameday"], r["gametime"]),
                "home_score": None if hs is None else int(hs), "away_score": None if as_ is None else int(as_),
                "spread_line": _num(r.get("spread_line")), "total_line": _num(r.get("total_line")),
                "home_moneyline": None if _num(r.get("home_moneyline")) is None else int(_num(r["home_moneyline"])),
                "away_moneyline": None if _num(r.get("away_moneyline")) is None else int(_num(r["away_moneyline"])),
                "home_fair_prob": home_fair,
                "split": (split_for(int(r["week"]), r["game_type"], cfg["split"]["dev_last_week"]) if primary
                          else f"fresh_{season}"),
                "fetched_ts": now_ts(),
            }
            db.upsert(conn, "games", row, ["game_id"])
            n += 1
        audit.log(conn, "collector.nfl", "load_schedule", "season", season, {"games": n})
    log.info("stored %d games for %d", n, season)
    return n
