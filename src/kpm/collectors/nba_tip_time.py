"""NBA tip-off time from the hoopR schedule (sportsdataverse/hoopR-nba-data, nba_schedule_<season>.parquet).

Kalshi's timestamps are not a schedule: occurrence_datetime - 3 h matched the real tip on 76 of 98 settled games
(expected_expiration_time - 3 h on 1384 of 1418), and missed by 6-8.5 h on playoff games whose time was not set yet.
So the hoopR schedule is the source of truth and Kalshi's time is only a cross-check:

* game in the schedule with a real time, Kalshi time within 15 min (or absent)  -> schedule time ("hoopr");
* Kalshi time more than 15 min away from the schedule                           -> None (skip), warning logged;
* game missing, time TBD / placeholder, or (live only) schedule file > 36 h old:
    live=True  -> None (skip), warning logged;
    live=False -> Kalshi time - 3 h ("fallback"), warning logged.

Public GET of one parquet from raw.githubusercontent.com; cached under data/raw/nba_open/hoopr_schedule/.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

log = logging.getLogger("kpm.nba_tip_time")

ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = ROOT / "data" / "raw" / "nba_open" / "hoopr_schedule"
SCHEDULE_URL = "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/nba/schedules/parquet/nba_schedule_{season}.parquet"
DOWNLOAD = True                          # tests switch this off; then only the cached file (if any) is read
REFRESH_AFTER = timedelta(hours=1)       # re-download the cached file when it is older than this
MAX_SCHEDULE_AGE = timedelta(hours=36)   # live: a schedule file older than this counts as no schedule at all
MAX_DISAGREE = timedelta(minutes=15)     # schedule vs Kalshi - 3 h; more than this -> skip the game
KALSHI_OFFSET = timedelta(hours=3)       # Kalshi time - 3 h = tip (the old rule, now fallback / cross-check only)
ET = ZoneInfo("America/New_York")
UTC = timezone.utc

GAME_KEY_RE = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})$")
MONTHS = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
# hoopR/ESPN abbreviation -> Kalshi code, current franchises (same mapping as scripts/nba_rosters.py)
HOOPR_TO_KALSHI = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS", "NO": "NOP"}
KALSHI_TEAMS = {"ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND", "LAC", "LAL", "MEM",
                "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR", "UTA", "WAS"}
NOT_PLAYED = ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_SUSPENDED")


class TipTime(NamedTuple):
    tip: datetime | None   # UTC; None = skip this game
    source: str            # "hoopr" | "fallback" | "skip"
    note: str              # why (shown in SKIP rows and logs)
    reason: str = ""       # skip kind: missing | placeholder | stale | disagree | no_schedule | bad_key


@dataclass(frozen=True)
class Schedule:
    """{(ET date, away, home): tip UTC, or None when the schedule has the game but no real time yet}."""
    tips: dict
    fetched_at: datetime

    def is_stale(self, now: datetime) -> bool:
        return now - self.fetched_at > MAX_SCHEDULE_AGE


def parse_game_key(key: str):
    """'26OCT23HOUSAS' -> (date(2026, 10, 23), 'HOU', 'SAS'), or None."""
    m = GAME_KEY_RE.match(key or "")
    if not m or m[2] not in MONTHS:
        return None
    try:
        return date(2000 + int(m[1]), MONTHS[m[2]], int(m[3])), m[4], m[5]
    except ValueError:
        return None


def season_for(d: date) -> int:
    """hoopR season = the year the season ENDS (Oct 2026 -> 2027)."""
    return d.year + 1 if d.month >= 9 else d.year


def _team(abbr) -> str | None:
    a = str(abbr or "").strip().upper()
    a = HOOPR_TO_KALSHI.get(a, a)
    return a if a in KALSHI_TEAMS else None


def _parse_utc(s) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC)


def is_placeholder(row: dict) -> bool:
    """No real tip time yet: ESPN marks these time_valid=False, 'M/D - TBD' detail, 00:00 ET; also postponed games."""
    if row.get("time_valid") is False:
        return True
    if "TBD" in str(row.get("status_type_detail") or "").upper():
        return True
    if str(row.get("status_type_name") or "") in NOT_PLAYED:
        return True
    t = row.get("tip")
    return t is None or (t.astimezone(ET).hour == 0 and t.astimezone(ET).minute == 0)


def schedule_from_rows(rows, fetched_at: datetime) -> Schedule:
    """rows: dicts with game_date_time (or tip), away_abbreviation, home_abbreviation, time_valid, status_type_*.
    A (date, away, home) listed twice is ambiguous and stored as a placeholder."""
    tips: dict = {}
    seen: set = set()
    for r in rows:
        r = dict(r)
        t = r.get("tip", r.get("game_date_time"))
        if t is not None and not isinstance(t, datetime):
            t = _parse_utc(t)
        elif t is not None and hasattr(t, "to_pydatetime"):
            t = t.to_pydatetime()
        r["tip"] = t.astimezone(UTC) if t is not None and t.tzinfo else None
        away, home = _team(r.get("away_abbreviation")), _team(r.get("home_abbreviation"))
        if r["tip"] is None or away is None or home is None:
            continue
        k = (r["tip"].astimezone(ET).date(), away, home)
        tips[k] = None if (k in seen or is_placeholder(r)) else r["tip"]
        seen.add(k)
    return Schedule(tips, fetched_at)


def fetch_schedule(season: int, cache_dir: Path | None = None, session=None, now: datetime | None = None) -> Path | None:
    """Cached parquet path, re-downloaded when older than REFRESH_AFTER. A failed download keeps the cached copy
    (its mtime then ages toward MAX_SCHEDULE_AGE). None when there is no file at all."""
    path = (cache_dir or CACHE_DIR) / f"nba_schedule_{season}.parquet"
    now_ts = (now or datetime.now(UTC)).timestamp()
    fresh = path.exists() and now_ts - path.stat().st_mtime < REFRESH_AFTER.total_seconds()
    if DOWNLOAD and not fresh:
        tmp = None
        try:
            import requests
            r = (session or requests).get(SCHEDULE_URL.format(season=season), timeout=60)
            if r.status_code == 200 and r.content[:4] == b"PAR1":
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(f"{path.stem}.{os.getpid()}.{time.time_ns()}.tmp")
                tmp.write_bytes(r.content)
                tmp.replace(path)
                tmp = None
            else:
                log.warning("hoopR schedule %s: HTTP %s, using cached copy if any", season, r.status_code)
        except Exception as e:  # noqa: BLE001
            log.warning("hoopR schedule %s download failed (%s), using cached copy if any", season, type(e).__name__)
        finally:
            if tmp is not None:
                try:
                    tmp.unlink()
                except OSError:
                    pass
    return path if path.exists() else None


_CACHE: dict = {}   # path -> (mtime, Schedule)


def load_schedule(season: int, cache_dir: Path | None = None, now: datetime | None = None) -> Schedule | None:
    """The season's Schedule (fetched_at = file mtime), or None when no file could be had."""
    path = fetch_schedule(season, cache_dir, now=now)
    if path is None:
        return None
    mtime = path.stat().st_mtime
    hit = _CACHE.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    import pandas as pd
    cols = ["game_date_time", "away_abbreviation", "home_abbreviation", "time_valid", "status_type_name",
            "status_type_detail"]
    try:
        df = pd.read_parquet(path, columns=cols)
    except Exception as e:  # noqa: BLE001
        log.warning("hoopR schedule %s unreadable (%s)", path.name, type(e).__name__)
        return None
    df["game_date_time"] = df["game_date_time"].astype(object).where(df["game_date_time"].notna(), None)
    sched = schedule_from_rows(df.to_dict("records"), datetime.fromtimestamp(mtime, UTC))
    _CACHE[path] = (mtime, sched)
    return sched


def get_tip_time(game_key: str, kalshi_time=None, *, live: bool, schedule: Schedule | None = ...,
                 now: datetime | None = None) -> TipTime:
    """Tip-off (UTC) for a Kalshi NBA game key. `kalshi_time` = the market's occurrence_datetime (ISO string or
    datetime; expected_expiration_time where that is all the caller has). live=True never guesses: no schedule time
    -> skip. live=False falls back to kalshi_time - 3 h with a warning. `schedule` defaults to load_schedule()."""
    now = now or datetime.now(UTC)
    kt = kalshi_time if isinstance(kalshi_time, datetime) or kalshi_time is None else _parse_utc(kalshi_time)
    guess = kt.astimezone(UTC) - KALSHI_OFFSET if kt is not None else None
    parsed = parse_game_key(game_key)
    if parsed is None:
        log.warning("tip %s: unparseable game key", game_key)
        return TipTime(None, "skip", "unparseable game key", "bad_key")
    if schedule is ...:
        schedule = load_schedule(season_for(parsed[0]), now=now)

    if schedule is None:
        why, kind = "no hoopR schedule file", "no_schedule"
    elif live and schedule.is_stale(now):
        age_h = (now - schedule.fetched_at).total_seconds() / 3600
        why = f"hoopR schedule file is {age_h:.0f}h old (> {MAX_SCHEDULE_AGE.total_seconds() / 3600:.0f}h)"
        kind = "stale"
    elif parsed not in schedule.tips:
        why, kind = "game not in hoopR schedule", "missing"
    elif schedule.tips[parsed] is None:
        why, kind = "hoopR schedule has no real time (TBD/placeholder)", "placeholder"
    else:
        tip = schedule.tips[parsed]
        if guess is not None and abs(guess - tip) > MAX_DISAGREE:
            gap = (guess - tip).total_seconds() / 60
            why = (f"tip disagreement: hoopR {tip:%H:%M}Z vs Kalshi-3h {guess:%H:%M}Z ({gap:+.0f} min, "
                   f"> {MAX_DISAGREE.total_seconds() / 60:.0f})")
            log.warning("tip %s: %s; skipping", game_key, why)
            return TipTime(None, "skip", why, "disagree")
        return TipTime(tip, "hoopr", "hoopR schedule")

    if not live and guess is not None:
        log.warning("tip %s: %s; FALLBACK to Kalshi time - 3h (%s)", game_key, why, guess.isoformat())
        return TipTime(guess, "fallback", f"{why}; Kalshi time - 3h")
    log.warning("tip %s: %s; skipping", game_key, why)
    return TipTime(None, "skip", why, kind)
