"""Shared helpers for the #66 NFL weather build. Weather and schedule only: outcome columns are never loaded.

Public Open-Meteo GETs only (no key). Every raw response is cached to disk; requests are throttled and retried.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
GAMES_CSV = ROOT / "data" / "raw" / "nflverse" / "games" / "games.csv"
CACHE = HERE / "cache"
ET = ZoneInfo("America/New_York")

# Only these games.csv columns are ever read. `wind` and `temp` are observed WEATHER (allowed).
SAFE_COLS = ["game_id", "season", "game_type", "week", "gameday", "weekday", "gametime", "away_team", "home_team",
             "location", "roof", "temp", "wind", "stadium_id", "stadium"]
# Anything outcome- or line-related: must never appear in a loaded frame.
FORBIDDEN_COLS = {"total", "home_score", "away_score", "result", "total_line", "spread_line", "overtime",
                  "away_moneyline", "home_moneyline", "under_odds", "over_odds", "away_spread_odds",
                  "home_spread_odds"}

NAME_OVERRIDES = {("JAX00", "Tottenham Hotspur Stadium"): "LON02"}   # games.csv reuses JAX00 for a London game

OPEN_ROOFS = {"outdoors", "open"}            # games.csv roof values allowed for B2 (Amendment 0 item 6)

# ---------------------------------------------------------------- schedule

def load_games(path: Path | str | None = None) -> pd.DataFrame:
    """Read games.csv with ONLY the safe columns; guarantees no outcome/line column is in the result."""
    path = Path(path or GAMES_CSV)
    header = pd.read_csv(path, nrows=0).columns
    use = [c for c in SAFE_COLS if c in header]
    df = pd.read_csv(path, usecols=use)
    bad = FORBIDDEN_COLS & set(df.columns)
    assert not bad, f"outcome columns leaked into the weather loader: {bad}"
    return df


def kickoff_utc(gameday: str, gametime: str | float | None) -> pd.Timestamp:
    """gameday 'YYYY-MM-DD' + gametime 'HH:MM' read as America/New_York wall time -> UTC (DST-aware).
    Missing gametime -> NaT."""
    if gametime is None or (isinstance(gametime, float) and pd.isna(gametime)) or str(gametime).strip() == "":
        return pd.NaT
    naive = datetime.strptime(f"{gameday} {str(gametime).strip()}", "%Y-%m-%d %H:%M")
    return pd.Timestamp(naive.replace(tzinfo=ET).astimezone(timezone.utc))


def kickoff_hour(ts: pd.Timestamp) -> pd.Timestamp:
    return ts.floor("h") if not pd.isna(ts) else ts


def resolve_stadium_id(stadium_id: str, stadium_name: str) -> str:
    return NAME_OVERRIDES.get((stadium_id, stadium_name), stadium_id)


def load_stadiums(path: Path | str | None = None) -> pd.DataFrame:
    return pd.read_csv(Path(path or HERE / "stadiums.csv")).set_index("stadium_id", drop=False)


def check_stadium_coverage(games: pd.DataFrame, stadiums: pd.DataFrame, lo: int = 1999, hi: int = 2026) -> list[str]:
    """Return the list of stadium ids used by games lo..hi that are NOT in the stadium table (must be empty)."""
    g = games[(games.season >= lo) & (games.season <= hi)]
    ids = {resolve_stadium_id(a, b) for a, b in zip(g.stadium_id, g.stadium)}
    return sorted(i for i in ids if i not in set(stadiums.stadium_id))

# ---------------------------------------------------------------- issue-time guarantee

def variant_guarantee_h(variant: str) -> int:
    """Open-Meteo Previous Runs: '<var>_previous_dayN' is the value from the latest model run initialised at least
    N*24 h before the valid hour (docs: 'the value predicted 24 hours before valid time'; verified empirically in
    README.md). Returns the guaranteed minimum lead, in hours, between model INITIALISATION and the valid hour."""
    if not variant.startswith("previous_day"):
        raise ValueError(f"no issue-time guarantee for variant {variant!r}")
    n = int(variant[len("previous_day"):])
    if n < 1:
        raise ValueError("previous_day0 is the current run: no guarantee")
    return 24 * n


def issue_ok(kickoff: pd.Timestamp, init: pd.Timestamp, min_h: float = 24.0) -> bool:
    """True iff the run was issued at least min_h hours before kickoff."""
    return (kickoff - init) >= pd.Timedelta(hours=min_h)


def guarantee_ok(variant: str, kickoff: pd.Timestamp, valid_hour: pd.Timestamp, min_h: float = 24.0) -> bool:
    """Is a forecast of `variant` valid at `valid_hour` (<= kickoff) guaranteed issued >= min_h before kickoff?
    Worst case the run was initialised exactly guarantee-h before the valid hour; valid_hour <= kickoff only helps."""
    assert valid_hour <= kickoff
    return (kickoff - (valid_hour - pd.Timedelta(hours=variant_guarantee_h(variant)))) >= pd.Timedelta(hours=min_h)

# ---------------------------------------------------------------- HTTP client with cache

class OpenMeteo:
    """Throttled (<= 4 req/s), retrying, disk-caching GET client. A cache hit costs no request."""

    def __init__(self, cache_dir: Path = CACHE, min_interval_s: float = 0.25, retries: int = 8, log=print,
                 max_per_min: float = 400.0):
        self.max_per_min = max_per_min
        self.cache_dir = Path(cache_dir)
        self.min_interval_s = min_interval_s
        self.retries = retries
        self.last = 0.0
        self.n_requests = 0
        self.n_cached = 0
        self.log = log
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "kalshi-pm-research/0.1 (read-only weather archive)"

    @staticmethod
    def key(url: str, params: dict) -> str:
        s = url + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        return hashlib.sha256(s.encode()).hexdigest()[:24]

    @staticmethod
    def weight(params: dict) -> float:
        """Open-Meteo counts a call as max(1, n_vars/10 * n_days/14) 'API calls' against its limits
        (600/min, 5,000/h, 10,000/day on the free tier). We stay under 500/min and ~8,000/day by pacing on this."""
        n_vars = len(str(params.get("hourly", "")).split(","))
        try:
            days = (pd.Timestamp(params["end_date"]) - pd.Timestamp(params["start_date"])).days + 1
        except KeyError:
            days = 7
        return max(1.0, n_vars / 10 * days / 14)

    def get(self, name: str, url: str, params: dict) -> dict:
        path = self.cache_dir / name / f"{self.key(url, params)}.json"
        if path.exists():
            self.n_cached += 1
            return json.loads(path.read_text(encoding="utf8"))["response"]
        path.parent.mkdir(parents=True, exist_ok=True)
        err = None
        for attempt in range(self.retries):
            wait = max(self.min_interval_s, self.weight(params) * 60 / self.max_per_min) - (time.monotonic() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            try:
                r = self.session.get(url, params=params, timeout=60)
                self.n_requests += 1
                if r.status_code == 200:
                    body = r.json()
                    tmp = path.with_suffix(".tmp")
                    tmp.write_text(json.dumps({"url": url, "params": params,
                                               "fetched_utc": datetime.now(timezone.utc).isoformat(),
                                               "response": body}), encoding="utf8")
                    tmp.replace(path)
                    return body
                if r.status_code == 400:        # a real parameter error: do not retry
                    raise RuntimeError(f"400 from {url}: {r.text[:300]}")
                err = f"HTTP {r.status_code}: {r.text[:200]}"
                if r.status_code == 429:
                    if "Daily" in r.text:
                        raise RuntimeError(f"daily limit reached: {r.text[:200]}")
                    time.sleep(65 if "inute" in r.text else 600)     # minutely / hourly limit: wait it out
            except requests.RequestException as e:
                err = repr(e)
            delay = min(120, 2 ** attempt * 2)
            self.log(f"  retry {attempt + 1}/{self.retries} after {delay}s: {err}")
            time.sleep(delay)
        raise RuntimeError(f"giving up on {url} {params}: {err}")

# ---------------------------------------------------------------- parsing

def parse_hourly(resp: dict) -> pd.DataFrame:
    """Open-Meteo hourly block -> DataFrame indexed by tz-aware UTC time, columns = variables (None -> NaN).
    Requires timezone=UTC in the request (checked)."""
    if resp.get("timezone") not in ("GMT", "UTC"):
        raise ValueError(f"response timezone is {resp.get('timezone')!r}, expected GMT")
    h = resp["hourly"]
    df = pd.DataFrame({k: pd.to_numeric(pd.Series(v, dtype="object"), errors="coerce") for k, v in h.items()
                       if k != "time"})
    df.index = pd.to_datetime(h["time"], utc=True)
    df.index.name = "time"
    return df
