"""Research log #56, data check 3b: NBA.com official season team stats (Advanced, PerGame, Regular Season).
Public GET only (stats.nba.com/stats/leaguedashteamstats), browser-like headers, raw response cached to
data/raw/nba_stats_official/, at most 1 request per second, NO request between 17:30 and 21:30 America/New_York
(waits), one attempt per season, no workarounds: a refusal / timeout is recorded as 'not checked'."""
from __future__ import annotations

import json
import time
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / "data" / "raw" / "nba_stats_official"
URL = "https://stats.nba.com/stats/leaguedashteamstats"
ET = ZoneInfo("America/New_York")
SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nba.com/", "Origin": "https://www.nba.com",
    "x-nba-stats-origin": "stats", "x-nba-stats-token": "true"}
PAUSE_START, PAUSE_END = dtime(17, 30), dtime(21, 30)


def in_pause_window(now=None):
    t = (now or datetime.now(ET)).astimezone(ET).time()
    return PAUSE_START <= t < PAUSE_END


def seconds_until_open(now=None):
    now = (now or datetime.now(ET)).astimezone(ET)
    if not in_pause_window(now):
        return 0.0
    end = now.replace(hour=PAUSE_END.hour, minute=PAUSE_END.minute, second=0, microsecond=0)
    return (end - now).total_seconds()


def params(season):
    return {"MeasureType": "Advanced", "PerMode": "PerGame", "Season": season, "SeasonType": "Regular Season",
            "LeagueID": "00", "PlusMinus": "N", "PaceAdjust": "N", "Rank": "N", "Outcome": "", "Location": "",
            "Month": 0, "SeasonSegment": "", "DateFrom": "", "DateTo": "", "OpponentTeamID": 0, "VsConference": "",
            "VsDivision": "", "GameSegment": "", "Period": 0, "LastNGames": 0, "TeamID": 0, "PORound": 0,
            "ShotClockRange": "", "TwoWay": 0, "GameScope": "", "PlayerExperience": "", "PlayerPosition": "",
            "StarterBench": ""}


def cache_path(season):
    return CACHE / f"leaguedashteamstats_Advanced_PerGame_{season}_RegularSeason.json"


def fetch_all(seasons=SEASONS, wait=True, log=print):
    """Returns {season: 'cached'|'fetched'|'not checked: <reason>'}."""
    CACHE.mkdir(parents=True, exist_ok=True)
    out, last = {}, 0.0
    for s in seasons:
        p = cache_path(s)
        if p.exists():
            out[s] = "cached"
            continue
        while True:
            w = seconds_until_open()
            if w <= 0:
                break
            if not wait:
                out[s] = "not checked: inside the 17:30-21:30 ET pause window"
                break
            log(f"pause window: sleeping {w:.0f}s")
            time.sleep(min(w, 300) + 1)
        if s in out:
            continue
        gap = time.time() - last
        if gap < 1.0:
            time.sleep(1.0 - gap)
        last = time.time()
        try:
            r = requests.get(URL, params=params(s), headers=HEADERS, timeout=25)
            if r.status_code != 200:
                out[s] = f"not checked: HTTP {r.status_code}"
                log(s, out[s])
                continue
            js = r.json()
            p.write_text(json.dumps({"url": r.url, "fetched_utc": datetime.utcnow().isoformat(), "response": js}))
            out[s] = "fetched"
        except Exception as e:  # noqa: BLE001  (refusal / timeout / non-JSON: record, do not work around)
            out[s] = f"not checked: {type(e).__name__}"
        log(s, out[s])
    return out


def load_cached(season):
    """{team_name: {'OFF_RATING':..., 'PACE':..., 'GP':...}} or None."""
    p = cache_path(season)
    if not p.exists():
        return None
    rs = json.loads(p.read_text())["response"]["resultSets"]
    rs = rs[0] if isinstance(rs, list) else rs
    h = rs["headers"]
    return {row[h.index("TEAM_NAME")]: {k: row[h.index(k)] for k in ("GP", "OFF_RATING", "DEF_RATING", "PACE")}
            for row in rs["rowSet"]}


if __name__ == "__main__":
    print(json.dumps(fetch_all(), indent=1))
