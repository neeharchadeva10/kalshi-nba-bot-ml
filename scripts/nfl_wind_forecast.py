"""Forward wind-forecast tape for research #66 (NFL totals, rule B2/B3).

One run = for every NFL game in nflverse games.csv that kicks off in the next 8 days at an OPEN-AIR stadium (per
reports/output/nfl_totals/weather/stadiums.csv), GET the Open-Meteo forecast API (free, no key) and append one JSON
line per game to data/paper_tape/nfl_wind/<UTC date>.jsonl:
    fetch_utc, game_id, kickoff_utc, hours_before_kickoff, stadium_id, lat, lon, values{wind_mph, gust_mph, temp_f,
    precip_mm at the kickoff hour}, model info (models requested, grid lat/lon/elevation), games_csv_roof.
B2 must use the line whose hours_before_kickoff >= 24 (latest such fetch); the fetch time is the issue-time proof.
Raw responses are kept under data/paper_tape/nfl_wind/raw/<UTC date>/.
games.csv is re-downloaded (temp file, then rename) only when the local copy is older than 24 h.
Read-only: public GETs of Open-Meteo and the nflverse schedule file. No Kalshi, no keys. Scheduling is NOT done here.
Exit code 0 on success (even with 0 games), 1 on any failed fetch.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
GAMES_CSV = ROOT / "data" / "raw" / "nflverse" / "games" / "games.csv"
GAMES_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
STADIUMS = ROOT / "reports" / "output" / "nfl_totals" / "weather" / "stadiums.csv"
OUT_DIR = ROOT / "data" / "paper_tape" / "nfl_wind"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY = ["wind_speed_10m", "wind_gusts_10m", "temperature_2m", "precipitation"]
ET = ZoneInfo("America/New_York")
NAME_OVERRIDES = {("JAX00", "Tottenham Hotspur Stadium"): "LON02"}
# only schedule columns are read (no scores, no lines)
SAFE_COLS = ["game_id", "season", "game_type", "week", "gameday", "gametime", "away_team", "home_team", "roof",
             "stadium_id", "stadium"]
WINDOW_DAYS = 8
THROTTLE_S = 0.3
RETRIES = 6


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}", flush=True)


def kickoff_utc(gameday: str, gametime) -> pd.Timestamp:
    if gametime is None or (isinstance(gametime, float) and pd.isna(gametime)) or str(gametime).strip() == "":
        return pd.NaT
    naive = datetime.strptime(f"{gameday} {str(gametime).strip()}", "%Y-%m-%d %H:%M")
    return pd.Timestamp(naive.replace(tzinfo=ET).astimezone(timezone.utc))


def refresh_games(now: datetime, path: Path = GAMES_CSV, url: str = GAMES_URL, max_age_h: float = 24) -> str:
    """Re-download games.csv if older than max_age_h (temp file, then atomic rename). Never raises."""
    age_h = (now.timestamp() - path.stat().st_mtime) / 3600 if path.exists() else float("inf")
    if age_h <= max_age_h:
        return f"games.csv fresh ({age_h:.1f} h old)"
    tmp = path.with_suffix(".csv.tmp")
    try:
        r = requests.get(url, timeout=120, headers={"User-Agent": "kalshi-pm-research/0.1"})
        r.raise_for_status()
        if not r.content.startswith(b"game_id,"):
            raise ValueError("unexpected games.csv header")
        tmp.write_bytes(r.content)
        tmp.replace(path)
        return f"games.csv re-downloaded ({len(r.content)} bytes; was {age_h:.1f} h old)"
    except Exception as e:                                     # keep the old copy
        tmp.unlink(missing_ok=True)
        return f"games.csv refresh FAILED ({e!r}); using the {age_h:.1f} h old copy"


def upcoming_games(now: datetime, days: int = WINDOW_DAYS, games_path: Path = GAMES_CSV,
                   stadiums_path: Path = STADIUMS) -> pd.DataFrame:
    header = pd.read_csv(games_path, nrows=0).columns
    g = pd.read_csv(games_path, usecols=[c for c in SAFE_COLS if c in header])
    st = pd.read_csv(stadiums_path).set_index("stadium_id")
    g["sid"] = [NAME_OVERRIDES.get((a, b), a) for a, b in zip(g.stadium_id, g.stadium)]
    g["kickoff_utc"] = [kickoff_utc(d, t) for d, t in zip(g.gameday, g.gametime)]
    t0 = pd.Timestamp(now)
    g = g[g.kickoff_utc.notna() & (g.kickoff_utc > t0) & (g.kickoff_utc <= t0 + pd.Timedelta(days=days))].copy()
    g = g[g.sid.map(st.roof_category) == "open-air"]
    g["lat"] = g.sid.map(st.lat)
    g["lon"] = g.sid.map(st.lon)
    return g.sort_values("kickoff_utc")


def parse_values(resp: dict, kickoff: pd.Timestamp) -> dict | None:
    """Pull the kickoff-hour values out of an hourly response (timezone must be UTC)."""
    if resp.get("timezone") not in ("GMT", "UTC"):
        raise ValueError(f"response timezone {resp.get('timezone')!r}")
    h = resp["hourly"]
    t = kickoff.floor("h").strftime("%Y-%m-%dT%H:%M")
    if t not in h["time"]:
        return None
    i = h["time"].index(t)
    return {"valid_hour_utc": t + ":00Z", "wind_mph": h["wind_speed_10m"][i], "gust_mph": h["wind_gusts_10m"][i],
            "temp_f": h["temperature_2m"][i], "precip_mm": h["precipitation"][i]}


def fetch(session: requests.Session, params: dict) -> dict:
    err = None
    for attempt in range(RETRIES):
        try:
            r = session.get(FORECAST_URL, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
            err = f"HTTP {r.status_code} {r.text[:150]}"
            if r.status_code == 400:
                break
        except requests.RequestException as e:
            err = repr(e)
        time.sleep(min(60, 2 ** attempt * 2))
    raise RuntimeError(err)


def run(now: datetime, out_dir: Path = OUT_DIR, games_path: Path = GAMES_CSV, refresh: bool = True) -> int:
    log(refresh_games(now, games_path) if refresh else "games.csv refresh skipped")
    g = upcoming_games(now, games_path=games_path)
    log(f"{len(g)} open-air games kicking off within {WINDOW_DAYS} days")
    out_dir.mkdir(parents=True, exist_ok=True)
    day = now.strftime("%Y-%m-%d")
    raw_dir = out_dir / "raw" / day
    sess = requests.Session()
    sess.headers["User-Agent"] = "kalshi-pm-research/0.1"
    n_ok = n_fail = 0
    lines = []
    for (sid, lat, lon), d in g.groupby(["sid", "lat", "lon"]):
        d0 = d.kickoff_utc.min().strftime("%Y-%m-%d")
        d1 = d.kickoff_utc.max().strftime("%Y-%m-%d")
        params = dict(latitude=lat, longitude=lon, start_date=d0, end_date=d1, hourly=",".join(HOURLY),
                      wind_speed_unit="mph", temperature_unit="fahrenheit", precipitation_unit="mm", timezone="UTC")
        fetch_utc = datetime.now(timezone.utc)
        try:
            resp = fetch(sess, params)
        except Exception as e:
            log(f"FAILED {sid} {d0}..{d1}: {e}")
            n_fail += len(d)
            continue
        raw_dir.mkdir(parents=True, exist_ok=True)
        with gzip.open(raw_dir / f"{sid}_{fetch_utc.strftime('%H%M%S')}.json.gz", "wt", encoding="utf8") as f:
            json.dump({"fetch_utc": fetch_utc.isoformat(), "params": params, "response": resp}, f)
        for r in d.itertuples():
            vals = parse_values(resp, r.kickoff_utc)
            if vals is None:
                log(f"no hourly row for {r.game_id}")
                n_fail += 1
                continue
            lines.append(json.dumps({
                "fetch_utc": fetch_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "game_id": r.game_id,
                "kickoff_utc": r.kickoff_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "hours_before_kickoff": round((r.kickoff_utc - pd.Timestamp(fetch_utc)).total_seconds() / 3600, 3),
                "stadium_id": sid, "lat": lat, "lon": lon,
                "games_csv_roof": None if pd.isna(r.roof) else r.roof, "season": int(r.season),
                "game_type": r.game_type, "values": vals,
                "model": {"requested": "best_match (Open-Meteo default)", "api": FORECAST_URL,
                          "grid_lat": resp.get("latitude"), "grid_lon": resp.get("longitude"),
                          "elevation_m": resp.get("elevation"), "generationtime_ms": resp.get("generationtime_ms")},
            }))
            n_ok += 1
        time.sleep(THROTTLE_S)
    if lines:
        with open(out_dir / f"{day}.jsonl", "a", encoding="utf8") as f:
            f.write("\n".join(lines) + "\n")
    log(f"appended {n_ok} lines to {out_dir / (day + '.jsonl')}; failures={n_fail}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-refresh", action="store_true", help="do not re-download games.csv")
    a = ap.parse_args()
    sys.exit(run(datetime.now(timezone.utc), refresh=not a.no_refresh))
