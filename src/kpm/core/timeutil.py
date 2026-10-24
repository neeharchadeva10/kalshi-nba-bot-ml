"""UTC time helpers. Everything stored is integer epoch seconds in UTC."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def parse_iso(s: str | None) -> int | None:
    """Parse ISO-8601 (with Z, offset, or space separator) to UTC epoch seconds."""
    if not s:
        return None
    s = s.strip().replace(" ", "T", 1)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if len(s) >= 3 and (s[-3] in "+-") and s[-3:].lstrip("+-").isdigit():  # "+00" -> "+00:00"
        s = s + ":00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def et_to_utc_ts(gameday: str, gametime: str) -> int:
    """nflverse kickoff: date 'YYYY-MM-DD' + 'HH:MM' in US Eastern -> UTC epoch."""
    dt = datetime.strptime(f"{gameday} {gametime}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
    return int(dt.timestamp())


def to_iso(ts: int | float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_ts() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp())


def floor_to(ts: int, step: int) -> int:
    return ts - (ts % step)


def ceil_to(ts: int, step: int) -> int:
    return ts if ts % step == 0 else ts + step - (ts % step)
