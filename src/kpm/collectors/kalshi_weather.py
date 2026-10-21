"""Weather calibration study collector: every settled market of every daily high/low temperature series in Kalshi's
"Climate and Weather" category, plus 1-minute YES bid/ask candles (close and open) from open to close.
Written to data/weather.sqlite (schema: core/weather_schema.sql). Contract: reports/output/weather_calibration/PREREGISTRATION.md.

Public Kalshi GET endpoints only, through KalshiClient/CachedClient (every response is cached on disk)."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import statistics
import time
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date
from importlib import resources
from pathlib import Path

from kpm.clients.kalshi import KalshiClient
from kpm.collectors.kalshi_spread import ask_e4, bid_e4
from kpm.collectors.kalshi_history import _field
from kpm.core import config, db
from kpm.core.config import settings
from kpm.core.prices import parse_dollars
from kpm.core.timeutil import floor_to, now_ts, parse_iso

log = logging.getLogger(__name__)

DB_NAME = "weather.sqlite"
SUMMARY_PATH = Path("reports/output/weather_calibration/collection_summary.json")
CATEGORY = "Climate and Weather"
MAX_WORKERS = 4                 # RAM: ~1.7 GB free on the machine
MIN_INTERVAL_S = 0.13           # ~7.7 requests/s ceiling (another collector uses up to 6/s of the same budget)

# ---------------------------------------------------------------- hand-written city table
# city key -> (city name, IANA time zone of the city's climate day)
CITIES: dict[str, tuple[str, str]] = {
    "ATL": ("Atlanta", "America/New_York"),
    "AUS": ("Austin", "America/Chicago"),
    "BOS": ("Boston", "America/New_York"),
    "CHI": ("Chicago (Midway)", "America/Chicago"),
    "DAL": ("Dallas (DFW)", "America/Chicago"),
    "DC": ("Washington DC", "America/New_York"),
    "DEN": ("Denver", "America/Denver"),
    "EWR": ("Newark NJ", "America/New_York"),
    "HOU": ("Houston", "America/Chicago"),
    "LAX": ("Los Angeles", "America/Los_Angeles"),
    "LV": ("Las Vegas", "America/Los_Angeles"),
    "MIA": ("Miami", "America/New_York"),
    "MIN": ("Minneapolis", "America/Chicago"),
    "NOLA": ("New Orleans", "America/Chicago"),
    "NYC": ("New York City (Central Park)", "America/New_York"),
    "OKC": ("Oklahoma City", "America/Chicago"),
    "PHIL": ("Philadelphia", "America/New_York"),
    "PHX": ("Phoenix", "America/Phoenix"),
    "SAN": ("San Diego", "America/Los_Angeles"),
    "SATX": ("San Antonio", "America/Chicago"),
    "SDF": ("Louisville", "America/New_York"),
    "SEA": ("Seattle", "America/Los_Angeles"),
    "SFO": ("San Francisco", "America/Los_Angeles"),
    "TTN": ("Trenton NJ", "America/New_York"),
    # international
    "AMS": ("Amsterdam", "Europe/Amsterdam"),
    "BJS": ("Beijing", "Asia/Shanghai"),
    "BER": ("Berlin", "Europe/Berlin"),
    "BRU": ("Brussels", "Europe/Brussels"),
    "DXB": ("Dubai", "Asia/Dubai"),
    "FRA": ("Frankfurt", "Europe/Berlin"),
    "GVA": ("Geneva", "Europe/Zurich"),
    "HKG": ("Hong Kong", "Asia/Hong_Kong"),
    "IST": ("Istanbul", "Europe/Istanbul"),
    "LON": ("London", "Europe/London"),
    "MEX": ("Mexico City", "America/Mexico_City"),
    "BOM": ("Mumbai", "Asia/Kolkata"),
    "PAR": ("Paris", "Europe/Paris"),
    "SAO": ("Sao Paulo", "America/Sao_Paulo"),
    "SEL": ("Seoul", "Asia/Seoul"),
    "SHA": ("Shanghai", "Asia/Shanghai"),
    "SIN": ("Singapore", "Asia/Singapore"),
    "SYD": ("Sydney", "Australia/Sydney"),
    "TYO": ("Tokyo", "Asia/Tokyo"),
    "YYZ": ("Toronto", "America/Toronto"),
}

# series ticker -> city key. Built from the series titles and the rules text of their markets (2026-10-13).
SERIES_CITY: dict[str, str] = {
    # legacy (non-KX) and early-KX tickers
    "HIGHAUS": "AUS", "HIGHCHI": "CHI", "HIGHMIA": "MIA", "HIGHNY": "NYC",
    "KXDENHIGH": "DEN", "KXHOUHIGH": "HOU", "KXHIGHOU": "HOU", "KXHIGHHOU": "HOU",
    # highs
    "KXHIGHAUS": "AUS", "KXHIGHCHI": "CHI", "KXHIGHDEN": "DEN", "KXHIGHLAX": "LAX", "KXHIGHMIA": "MIA",
    "KXHIGHNY": "NYC", "KXHIGHPHIL": "PHIL",
    "KXHIGHTATL": "ATL", "KXHIGHTBOS": "BOS", "KXHIGHTCYYZ": "YYZ", "KXHIGHTDAL": "DAL", "KXHIGHTDC": "DC",
    "KXHIGHTEBBR": "BRU", "KXHIGHTEDDB": "BER", "KXHIGHTEDDF": "FRA", "KXHIGHTEGLL": "LON", "KXHIGHTEHAM": "AMS",
    "KXHIGHTEWR": "EWR", "KXHIGHTHOU": "HOU", "KXHIGHTKSAN": "SAN", "KXHIGHTLFPG": "PAR", "KXHIGHTLSGG": "GVA",
    "KXHIGHTLTFM": "IST", "KXHIGHTLV": "LV", "KXHIGHTMIN": "MIN", "KXHIGHTMMMX": "MEX", "KXHIGHTNOLA": "NOLA",
    "KXHIGHTOKC": "OKC", "KXHIGHTOMDB": "DXB", "KXHIGHTPHX": "PHX", "KXHIGHTRJTT": "TYO", "KXHIGHTRKSI": "SEL",
    "KXHIGHTSAN": "SAN", "KXHIGHTSATX": "SATX", "KXHIGHTSBGR": "SAO", "KXHIGHTSDF": "SDF",   # SDF = Louisville (title says SATX)
    "KXHIGHTSEA": "SEA", "KXHIGHTSFO": "SFO", "KXHIGHTTTN": "TTN", "KXHIGHTVABB": "BOM", "KXHIGHTVHHH": "HKG",
    "KXHIGHTWSSS": "SIN", "KXHIGHTYSSY": "SYD", "KXHIGHTZBAA": "BJS", "KXHIGHTZSPD": "SHA",
    # lows
    "KXLOWAUS": "AUS", "KXLOWCHI": "CHI", "KXLOWDEN": "DEN", "KXLOWLAX": "LAX", "KXLOWMIA": "MIA",
    "KXLOWNY": "NYC", "KXLOWNYC": "NYC", "KXLOWPHIL": "PHIL",
    "KXLOWTATL": "ATL", "KXLOWTAUS": "AUS", "KXLOWTBOS": "BOS", "KXLOWTCHI": "CHI", "KXLOWTCYYZ": "YYZ",
    "KXLOWTDAL": "DAL", "KXLOWTDC": "DC", "KXLOWTDEN": "DEN", "KXLOWTEBBR": "BRU", "KXLOWTEDDB": "BER",
    "KXLOWTEDDF": "FRA", "KXLOWTEGLL": "LON", "KXLOWTEHAM": "AMS", "KXLOWTEWR": "EWR", "KXLOWTHOU": "HOU",
    "KXLOWTKSAN": "SAN", "KXLOWTLAX": "LAX", "KXLOWTLFPG": "PAR", "KXLOWTLSGG": "GVA", "KXLOWTLSSG": "GVA",
    "KXLOWTLTFM": "IST", "KXLOWTLV": "LV", "KXLOWTMIA": "MIA", "KXLOWTMIN": "MIN", "KXLOWTMMMX": "MEX",
    "KXLOWTNOLA": "NOLA", "KXLOWTNYC": "NYC", "KXLOWTOKC": "OKC", "KXLOWTOMDB": "DXB", "KXLOWTPHIL": "PHIL",
    "KXLOWTPHX": "PHX", "KXLOWTRJTT": "TYO", "KXLOWTRKSI": "SEL", "KXLOWTSAN": "SAN", "KXLOWTSATX": "SATX",
    "KXLOWTSBGR": "SAO", "KXLOWTSDF": "SDF", "KXLOWTSEA": "SEA", "KXLOWTSFO": "SFO", "KXLOWTTTN": "TTN",
    "KXLOWTVABB": "BOM", "KXLOWTVHHH": "HKG", "KXLOWTWSSS": "SIN", "KXLOWTYSSY": "SYD", "KXLOWTZBAA": "BJS",
    "KXLOWTZSPD": "SHA",
}

# daily temperature-like series that are deliberately out of scope
EXPLICIT_EXCLUDE = {
    "HIGHUS": "national (United States) maximum, not a city",
    "KXHIGHUS": "national (United States) maximum, not a city",
    "KXCITIESWEATHER": "one combined multi-city market, not a single city-day",
    "KXDVHIGH": "title does not say high or low (Death Valley temperature); no settled markets",
}

_TEMP_WORD = re.compile(r"\btemp", re.I)
_KIND_LOW = re.compile(r"\b(low|lowest|min|minimum)\b", re.I)
_KIND_HIGH = re.compile(r"\b(high|highest|max|maximum)\b", re.I)
_NOT_TEMP = re.compile(r"rain|snow|quake|hurricane|storm|eclipse", re.I)


class WeatherMappingError(RuntimeError):
    """In-scope series with no entry in the hand-written city table (never guessed)."""


# ---------------------------------------------------------------- pure helpers
_MONTHS = {m: i + 1 for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT",
                                           "NOV", "DEC"))}
_EVENT_DAY = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(?:$|[-(])")


def parse_event_day(event_ticker: str) -> str | None:
    """'KXHIGHNY-26OCT14' -> '2026-10-14'. None if the ticker has no valid date block."""
    m = _EVENT_DAY.search(event_ticker or "")
    if not m or m.group(2) not in _MONTHS:
        return None
    try:
        return date(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3))).isoformat()
    except ValueError:
        return None


def parse_source(rules_primary: str | None, rules_secondary: str | None = None) -> str:
    """'TWC' (The Weather Company), 'NWS' (National Weather Service Climatological Report) or 'OTHER'."""
    for text in (rules_primary, rules_secondary):
        t = (text or "").lower()
        if "weather company" in t:
            return "TWC"
        if "national weather service" in t or "nws" in t:
            return "NWS"
    return "OTHER"


def parse_station(rules_primary: str | None) -> str | None:
    """'... recorded at New York City (CLINYC) for ...' -> 'CLINYC'."""
    m = re.search(r"\((CLI[A-Z]{2,4})\)", rules_primary or "")
    return m.group(1) if m else None


_NUM = r"(-?\d+(?:\.\d+)?)"
_RULE_BETWEEN = re.compile(r"\bbetween\s+" + _NUM + r"\s*(?:-|–|to|and)\s*" + _NUM, re.I)
_RULE_GREATER = re.compile(r"\b(?:strictly\s+)?(?:greater\s+than|above)\s+" + _NUM, re.I)
_RULE_LESS = re.compile(r"\b(?:strictly\s+)?(?:(?:less|lower)\s+than|below)\s+" + _NUM, re.I)


def strikes_from_rules(rules: str | None) -> tuple[str | None, float | None, float | None]:
    """Fallback for old markets whose API record has no strike fields: 'between 70-71' / 'greater than 87' /
    'less than 78' in the rules text (the degree sign may be mis-encoded; only the numbers are used)."""
    t = rules or ""
    if m := _RULE_BETWEEN.search(t):
        return "between", float(m.group(1)), float(m.group(2))
    if m := _RULE_GREATER.search(t):
        return "greater", float(m.group(1)), None
    if m := _RULE_LESS.search(t):
        return "less", None, float(m.group(1))
    return None, None, None


def parse_strikes(m: dict) -> tuple[str | None, float | None, float | None, str]:
    """(strike_type, floor_strike, cap_strike, strike_src). src 'api' = fields of the market record ('between' has
    both, 'greater' only floor, 'less' only cap); 'rules' = parsed from the rules text because the API record has
    no strike_type (markets before ~Feb 2025); 'none' = neither worked."""
    st = m.get("strike_type")
    fl, cp = m.get("floor_strike"), m.get("cap_strike")
    fl = float(fl) if fl is not None else None
    cp = float(cp) if cp is not None else None
    if st is None:
        st, fl, cp = strikes_from_rules(m.get("rules_primary"))
        return st, fl, cp, ("rules" if st else "none")
    if st == "between" and (fl is None or cp is None):
        raise ValueError(f"{m.get('ticker')}: between market without both strikes")
    if st == "greater" and fl is None:
        raise ValueError(f"{m.get('ticker')}: greater market without floor_strike")
    if st == "less" and cp is None:
        raise ValueError(f"{m.get('ticker')}: less market without cap_strike")
    return st, fl, cp, "api"


def series_kind(ticker: str, title: str) -> str:
    """'high' / 'low' from the title, cross-checked against the ticker."""
    low, high = bool(_KIND_LOW.search(title or "")), bool(_KIND_HIGH.search(title or ""))
    if low == high:
        raise ValueError(f"{ticker}: cannot tell high from low in title {title!r}")
    kind = "low" if low else "high"
    if (("LOW" in ticker) != (kind == "low")) and ("LOW" in ticker or "HIGH" in ticker):
        raise ValueError(f"{ticker}: title says {kind} but ticker disagrees")
    return kind


def select_series(all_series: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split the Climate-and-Weather series into (in scope with kind/city/tz, excluded with reason).

    In scope = frequency 'daily', a high/low temperature title, not explicitly excluded. Every in-scope series must
    be in SERIES_CITY: otherwise WeatherMappingError lists them all."""
    included, excluded, missing = [], [], []
    for s in sorted(all_series, key=lambda x: x["ticker"]):
        t, title, freq = s["ticker"], s.get("title") or "", s.get("frequency")
        if t in EXPLICIT_EXCLUDE:
            reason = EXPLICIT_EXCLUDE[t]
        elif freq != "daily":
            reason = f"frequency={freq}"
        elif _NOT_TEMP.search(title) or not _TEMP_WORD.search(title) or not (
                _KIND_LOW.search(title) or _KIND_HIGH.search(title)):
            reason = "daily but not a high/low temperature title"
        else:
            reason = None
        if reason:
            excluded.append({"ticker": t, "title": title, "frequency": freq, "reason": reason})
            continue
        if t not in SERIES_CITY or SERIES_CITY[t] not in CITIES:
            missing.append(f"{t} ({title})")
            continue
        city, tz = CITIES[SERIES_CITY[t]]
        included.append({**s, "kind": series_kind(t, title), "city": city, "tz": tz})
    if missing:
        raise WeatherMappingError("in-scope series without a city/time-zone mapping (add to SERIES_CITY): "
                                  + "; ".join(missing))
    return included, excluded


def quote_row(mid: int, c: dict) -> tuple:
    """(mid, ts, bid_c, ask_c, bid_o, ask_o, volume) from a historical or live candle. Empty side -> NULL."""
    yb, ya = c.get("yes_bid") or {}, c.get("yes_ask") or {}
    return (mid, int(c["end_period_ts"]),
            bid_e4(_field(yb, "close")), ask_e4(_field(ya, "close")),
            bid_e4(_field(yb, "open")), ask_e4(_field(ya, "open")),
            parse_dollars(_field(c, "volume")))


_RAW_DROP = ("rules_primary", "rules_secondary", "price_ranges", "early_close_condition")


def market_row(m: dict, tier: str, series: dict) -> dict:
    st, fl, cp, st_src = parse_strikes(m)
    return {
        "ticker": m["ticker"], "series": series["ticker"], "event_ticker": m["event_ticker"],
        "kind": series["kind"], "city": series["city"], "tz": series["tz"],
        "day": parse_event_day(m["event_ticker"]),
        "strike_type": st, "floor_strike": fl, "cap_strike": cp, "strike_src": st_src,
        "open_ts": parse_iso(m.get("open_time")), "close_ts": parse_iso(m.get("close_time")),
        "settled_ts": parse_iso(m.get("settlement_ts")),
        "result": m.get("result") or None,
        "settlement_value": parse_dollars(m.get("settlement_value_dollars")), "volume": parse_dollars(m.get("volume_fp")),
        "source": parse_source(m.get("rules_primary"), m.get("rules_secondary")),
        "rules_primary": m.get("rules_primary"), "rules_secondary": m.get("rules_secondary"),
        "data_tier": tier,
        "raw_json": json.dumps({k: v for k, v in m.items() if k not in _RAW_DROP}, sort_keys=True),
    }


def peak_rss_mb() -> float | None:
    """Peak working set of this process (Windows ctypes; falls back to resource on POSIX)."""
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        k32, ps = ctypes.windll.kernel32, ctypes.windll.psapi
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        ps.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        if ps.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return pmc.PeakWorkingSetSize / 1e6
    except Exception:
        pass
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except Exception:
        return None


# ---------------------------------------------------------------- discovery
def weather_db_path() -> Path:
    return config.path("db_path").parent / DB_NAME


def _open_weather_db() -> sqlite3.Connection:
    conn = db.connect(weather_db_path())
    conn.executescript(resources.files("kpm.core").joinpath("weather_schema.sql").read_text(encoding="utf-8"))
    conn.commit()
    return conn


def list_series(client: KalshiClient) -> list[dict]:
    return client.http.get("/series", {"category": CATEGORY}, use_cache=False).get("series", [])


def discover_series_markets(client: KalshiClient, conn: sqlite3.Connection, series: dict) -> int:
    """Store every settled market of one series (historical tier first, then live; deduped by ticker).
    Returns the number of markets written. Holds one series in memory at a time."""
    raw: dict[str, tuple[dict, str]] = {}
    for m in client.markets(series["ticker"], historical=True):
        raw[m["ticker"]] = (m, "historical")
    for m in client.markets(series["ticker"], historical=False, status="settled"):
        raw.setdefault(m["ticker"], (m, "live"))
    bad = 0
    for t, (m, tier) in raw.items():
        try:
            row = market_row(m, tier, series)
        except ValueError as exc:
            bad += 1
            log.warning("skipping market: %s", exc)
            continue
        if row["day"] is None:
            log.warning("%s: no measured day in event ticker %s", t, m["event_ticker"])
        if client.is_historical(row["settled_ts"]) != (tier == "historical"):
            log.warning("%s: tier %s disagrees with the cutoff on settled_ts", t, tier)
        db.upsert(conn, "wx_markets", row, ["ticker"])
    # station code: most common one parsed from the series' rules text
    stations = [s for s in (parse_station(m.get("rules_primary")) for m, _ in raw.values()) if s]
    station = max(set(stations), key=stations.count) if stations else None
    db.upsert(conn, "wx_series", {
        "ticker": series["ticker"], "title": series.get("title"), "kind": series["kind"], "city": series["city"],
        "station": station, "tz": series["tz"], "fee_type": series.get("fee_type"),
        "frequency": series.get("frequency"), "raw_json": json.dumps(series, sort_keys=True)}, ["ticker"])
    client.http.flush_index(conn)
    conn.commit()
    return len(raw) - bad


def request_estimate(open_ts: int | None, close_ts: int | None, per_req: int = 4999) -> int:
    if not open_ts or not close_ts or close_ts <= open_ts:
        return 0
    return -(-(close_ts - floor_to(open_ts, 60)) // (per_req * 60))


# ---------------------------------------------------------------- run
def run(limit: int | None = None, series: str | None = None, force: bool = False,
        discover_only: bool = False) -> dict:
    cfg = settings()
    t0 = time.monotonic()
    conn = _open_weather_db()
    try:
        client = KalshiClient(conn)
        client.http.min_interval_s = max(client.http.min_interval_s, MIN_INTERVAL_S)
        client.cutoff()
        included, excluded = select_series(list_series(client))
        if series:
            included = [s for s in included if s["ticker"] == series]
            if not included:
                raise SystemExit(f"{series}: not an in-scope weather series")
        log.info("%d in-scope series, %d excluded Climate-and-Weather series", len(included), len(excluded))
        for i, s in enumerate(included, 1):
            n = discover_series_markets(client, conn, s)
            log.info("[%d/%d] %s (%s %s): %d settled markets", i, len(included), s["ticker"], s["city"], s["kind"], n)

        tickers = [s["ticker"] for s in included]
        q = ("SELECT mid, ticker, series, data_tier, open_ts, close_ts FROM wx_markets WHERE series IN (%s) "
             "ORDER BY close_ts, ticker" % ",".join("?" * len(tickers)))
        jobs = [dict(mid=r[0], ticker=r[1], series=r[2], tier=r[3], start=floor_to(r[4], 60), end=r[5])
                for r in conn.execute(q, tickers) if r[4] and r[5] and r[5] > r[4]]
        est = sum(request_estimate(j["start"], j["end"]) for j in jobs)
        log.info("%d markets with a candle window; ~%d candle requests", len(jobs), est)
        if limit:   # evenly spaced sample so a smoke run touches both data tiers
            jobs = [jobs[int(i * (len(jobs) - 1) / max(limit - 1, 1))] for i in range(min(limit, len(jobs)))]
        done = {r[0] for r in conn.execute("SELECT ticker FROM wx_done WHERE step='quotes'")}
        todo = [j for j in jobs if force or j["ticker"] not in done]
        del done
        if discover_only:
            todo = []
        log.info("%d markets need candles", len(todo))

        def fetch(job: dict) -> tuple[dict, list[dict]]:
            return job, client.candlesticks(job["series"], job["ticker"], job["start"], job["end"],
                                            cfg["kalshi"]["candle_interval_min"], job["tier"] == "historical")

        workers = MAX_WORKERS
        queue = iter(todo)
        n_done = 0
        f0 = client.http.stats["fetches"]
        t1 = time.monotonic()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            inflight: set = set()       # bounded: finished futures (and their candle lists) are dropped at once
            while True:
                while len(inflight) < 3 * workers and (j := next(queue, None)) is not None:
                    inflight.add(pool.submit(fetch, j))
                if not inflight:
                    break
                finished, inflight = wait(inflight, return_when=FIRST_COMPLETED)
                for fut in finished:
                    job, candles = fut.result()
                    qrows = [quote_row(job["mid"], c) for c in candles]
                    conn.executemany("INSERT OR REPLACE INTO wx_quotes (mid, ts, bid_c, ask_c, bid_o, ask_o, volume) "
                                     "VALUES (?,?,?,?,?,?,?)", qrows)
                    conn.execute("INSERT OR REPLACE INTO wx_done (ticker, step, ts, detail) VALUES (?,?,?,?)",
                                 (job["ticker"], "quotes", now_ts(), json.dumps({"n": len(qrows)})))
                    client.http.flush_index(conn)
                    conn.commit()
                    n_done += 1
                    del fut, candles, qrows
                    if n_done % 100 == 0 or n_done == len(todo):
                        rate = (client.http.stats["fetches"] - f0) / max(time.monotonic() - t1, 1e-9)
                        log.info("candles %d/%d markets (fetches=%d hits=%d retries=%d, %.1f req/s, peak RSS %s MB)",
                                 n_done, len(todo), client.http.stats["fetches"], client.http.stats["hits"],
                                 client.http.stats["retries"], rate, round(peak_rss_mb() or 0))
        run_stats = {"markets_fetched_this_run": n_done, "elapsed_s": round(time.monotonic() - t0, 1),
                     "network_fetches": client.http.stats["fetches"], "cache_hits": client.http.stats["hits"],
                     "retries_429_etc": client.http.stats["retries"],
                     "candle_requests_per_s": round((client.http.stats["fetches"] - f0) / max(time.monotonic() - t1, 1e-9), 2),
                     "peak_rss_mb": round(peak_rss_mb() or 0, 1), "estimated_candle_requests_total": est}
        summary = collection_summary(conn, excluded, run_stats, [s["ticker"] for s in included])
    finally:
        conn.close()
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("summary -> %s", SUMMARY_PATH)
    return summary


def collection_summary(conn: sqlite3.Connection, excluded: list[dict], run_stats: dict,
                       series_filter: list[str] | None = None) -> dict:
    out: dict = {"generated_ts": now_ts(), "run": run_stats}
    per: dict[str, dict] = {}
    for r in conn.execute(
            "SELECT s.ticker, s.title, s.city, s.kind, s.tz, s.station, "
            " COUNT(DISTINCT m.event_ticker), COUNT(m.mid), MIN(m.day), MAX(m.day), "
            " SUM(m.source='TWC'), SUM(m.source='NWS'), SUM(m.source='OTHER'), "
            " SUM(m.data_tier='historical'), SUM(m.data_tier='live'), COALESCE(SUM(m.volume),0) "
            "FROM wx_series s LEFT JOIN wx_markets m ON m.series = s.ticker GROUP BY s.ticker ORDER BY s.ticker"):
        if series_filter is not None and r[0] not in series_filter:
            continue
        per[r[0]] = {"title": r[1], "city": r[2], "kind": r[3], "tz": r[4], "station": r[5],
                     "n_events": r[6], "n_markets": r[7], "first_day": r[8], "last_day": r[9],
                     "source_split": {"TWC": r[10] or 0, "NWS": r[11] or 0, "OTHER": r[12] or 0},
                     "tier_split": {"historical": r[13] or 0, "live": r[14] or 0}, "total_volume": r[15]}
    counts: dict[int, int] = {}
    series_of: dict[int, str] = {}
    for mid, ser, n in conn.execute(
            "SELECT m.mid, m.series, (SELECT COUNT(*) FROM wx_quotes q WHERE q.mid = m.mid) "
            "FROM wx_markets m JOIN wx_done d ON d.ticker = m.ticker AND d.step = 'quotes'"):
        counts[mid], series_of[mid] = n, ser
    by_series: dict[str, list[int]] = defaultdict(list)
    for mid, n in counts.items():
        by_series[series_of[mid]].append(n)
    for ser, d in per.items():
        ns = by_series.get(ser, [])
        d["candles"] = {"markets_done": len(ns), "markets_zero_candles": sum(1 for n in ns if n == 0),
                        "median_candles_per_market": statistics.median(ns) if ns else None}
    allc = list(counts.values())
    total_markets = sum(d["n_markets"] for d in per.values())
    out["series"] = per
    out["totals"] = {
        "in_scope_series": len(per), "series_with_markets": sum(1 for d in per.values() if d["n_markets"]),
        "n_events": sum(d["n_events"] for d in per.values()), "n_markets": total_markets,
        "source_split": {k: sum(d["source_split"][k] for d in per.values()) for k in ("TWC", "NWS", "OTHER")},
        "tier_split": {k: sum(d["tier_split"][k] for d in per.values()) for k in ("historical", "live")},
        "total_volume": sum(d["total_volume"] for d in per.values())}
    nq = conn.execute("SELECT COUNT(*), SUM(bid_c IS NULL), SUM(ask_c IS NULL) FROM wx_quotes").fetchone()
    out["candle_stats"] = {
        "markets_done": len(allc), "markets_pending": total_markets - len(allc),
        "markets_with_zero_candles": sum(1 for n in allc if n == 0),
        "median_candles_per_market": statistics.median(allc) if allc else None,
        "quote_rows": nq[0], "null_bid_close_share": (nq[1] / nq[0]) if nq[0] else None,
        "null_ask_close_share": (nq[2] / nq[0]) if nq[0] else None}
    out["excluded_series"] = excluded
    return out
