"""Helpers shared by collectors."""
from __future__ import annotations

import sqlite3
import statistics

from kpm.agents.matcher.rules import GameRef


def load_games(conn: sqlite3.Connection) -> list[GameRef]:
    rows = conn.execute("SELECT game_id, home_team, away_team, kickoff_ts FROM games").fetchall()
    if not rows:
        raise RuntimeError("games table is empty: run `kpm nfl` first")
    return [GameRef(r["game_id"], r["home_team"], r["away_team"], r["kickoff_ts"]) for r in rows]


def median_spacing_s(ts: list[int]) -> int | None:
    ts = sorted(ts)
    d = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    return int(statistics.median(d)) if d else None


def done_ids(conn: sqlite3.Connection, component: str, action: str) -> set[str]:
    """All entity ids already completed for (component, action): one query instead of one per entity."""
    return {r[0] for r in conn.execute("SELECT DISTINCT entity_id FROM audit_log WHERE component=? AND action=?",
                                       (component, action))}


def is_done(conn: sqlite3.Connection, component: str, action: str, entity_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM audit_log WHERE component=? AND action=? AND entity_id=? LIMIT 1",
        (component, action, entity_id),
    ).fetchone() is not None
