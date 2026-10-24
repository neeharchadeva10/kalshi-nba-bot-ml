"""Forward data collector for research log #43 (DraftKings vs Kalshi NBA totals). READ-ONLY.

One Odds API call (DraftKings totals, all NBA events) + public Kalshi KXNBATOTAL orderbook GETs for the same games.
Appends one JSON line per game to data/dk_gap/snapshots.jsonl. No outcomes. The API key is never printed, logged,
saved, or put in an exception message.

Usage: .venv/Scripts/python.exe reports/output/nba_dk_gap/dk_snapshot.py [--once] [--dry-run] [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[3]
ET = ZoneInfo("America/New_York")
KEY_PATH = ROOT / "secrets" / "odds_api_key.txt"
PLACEHOLDER = "PASTE_ODDS_API_KEY_HERE"
RAW_DIR = ROOT / "data" / "raw" / "odds_api"
OUT_PATH = ROOT / "data" / "dk_gap" / "snapshots.jsonl"
LOG_PATH = ROOT / "logs" / "dk_snapshot.log"
URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
PARAMS = {"regions": "us", "markets": "totals", "bookmakers": "draftkings", "oddsFormat": "american"}
GAP_THRESHOLD = 2.0

# Same map as reports/output/nba_totals_tourney/explore/book_vs_kalshi.py NAMES
NAMES = {"Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA",
         "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
         "Detroit Pistons": "DET", "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
         "Los Angeles Clippers": "LAC", "LA Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
         "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP",
         "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
         "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS",
         "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS"}
MON = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()


class SnapshotError(Exception):
    """Error whose message is guaranteed not to contain the API key."""


# ---------------------------------------------------------------- key handling / redaction
def redact(text, key: str | None = None) -> str:
    s = str(text)
    if key:
        s = s.replace(key, "<redacted>")
    return re.sub(r"(apiKey=)[^&\s'\"]+", r"\1<redacted>", s, flags=re.I)


def load_key(path: Path | None = None, env=None) -> str:
    """First non-comment, non-empty line of the key file; else env ODDS_API_KEY. Refuses the placeholder."""
    path = path or KEY_PATH
    env = os.environ if env is None else env
    key = ""
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                key = line
                break
    if not key or key == PLACEHOLDER:
        key = env.get("ODDS_API_KEY", "").strip()
    if not key or key == PLACEHOLDER:
        raise SnapshotError(f"No Odds API key set. Put it on a line in {path} (or set ODDS_API_KEY).")
    return key


def key_is_set(path: Path | None = None, env=None) -> bool:
    try:
        load_key(path, env)
        return True
    except SnapshotError:
        return False


def log(msg: str, key: str | None = None, path: Path | None = None) -> None:
    path = path or LOG_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} {redact(msg, key)}\n")
    except OSError:
        pass


# ---------------------------------------------------------------- Odds API
def fetch_odds(key: str, session=None, timeout: int = 30):
    """One GET. Returns (events_json, headers dict). Raises SnapshotError (key-free)."""
    session = session or requests
    try:
        r = session.get(URL, params={**PARAMS, "apiKey": key}, timeout=timeout)
    except requests.RequestException as e:
        raise SnapshotError(f"Odds API request failed ({type(e).__name__}): {redact(e, key)}"[:300]) from None
    if r.status_code != 200:
        raise SnapshotError(f"Odds API HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError:
        raise SnapshotError("Odds API returned non-JSON") from None
    hdr = {"x-requests-remaining": r.headers.get("x-requests-remaining"),
           "x-requests-used": r.headers.get("x-requests-used")}
    return data, hdr


def save_raw(data, now: datetime, key: str | None = None, raw_dir: Path | None = None) -> Path:
    raw_dir = raw_dir or RAW_DIR
    raw_dir.mkdir(parents=True, exist_ok=True)
    p = raw_dir / f"{now:%Y%m%dT%H%M%SZ}.json"
    text = redact(json.dumps(data), key)  # defensive: body should never hold the key
    p.write_text(text, encoding="utf-8")
    return p


def team_code(name: str):
    return NAMES.get(name)


def parse_z(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def dk_totals(event: dict):
    """-> dict(point, over_price, under_price) from DraftKings' totals market, or None."""
    for bk in event.get("bookmakers") or []:
        if bk.get("key") != "draftkings":
            continue
        for mk in bk.get("markets") or []:
            if mk.get("key") != "totals":
                continue
            over = under = None
            for o in mk.get("outcomes") or []:
                if o.get("name") == "Over":
                    over = o
                elif o.get("name") == "Under":
                    under = o
            if over is None or under is None or over.get("point") is None:
                return None
            if under.get("point") is not None and float(under["point"]) != float(over["point"]):
                return None
            return {"point": float(over["point"]), "over_price": over.get("price"), "under_price": under.get("price")}
    return None


def kalshi_key_for(event: dict, kalshi_keys) -> str | None:
    """Map a DK event to a Kalshi game key (e.g. 26OCT21ATLORL) among `kalshi_keys`: same away+home codes, key date
    equal to the commence ET date (else within 1 day, nearest). None if unmapped."""
    a, h = team_code(event.get("away_team", "")), team_code(event.get("home_team", ""))
    if not a or not h or not event.get("commence_time"):
        return None
    d0 = parse_z(event["commence_time"]).astimezone(ET).date()
    best = None
    for k in kalshi_keys:
        m = re.match(r"^(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})$", k)
        if not m or m.group(4) != a or m.group(5) != h or m.group(2) not in MON:
            continue
        kd = date(2000 + int(m.group(1)), MON.index(m.group(2)) + 1, int(m.group(3)))
        dd = abs((kd - d0).days)
        if dd <= 1 and (best is None or dd < best[0]):
            best = (dd, k)
    return None if best is None else best[1]


# ---------------------------------------------------------------- Kalshi lines
def market_median(points):
    """[(strike, mid)] valid quotes; first 0.5-mid crossing from the low side, interpolated between ADJACENT valid
    strikes (identical to reports/output/nba_totals_pace/lib.py market_median)."""
    pts = sorted(points)
    for (s1, m1), (s2, m2) in zip(pts, pts[1:]):
        if m1 >= 0.5 > m2:
            return s1 + (m1 - 0.5) / (m1 - m2) * (s2 - s1)
    return None


def kalshi_lines(quotes: list[dict], select_strike):
    """quotes: dicts strike,ticker,bid,ask (cents; valid only). -> (main quote or None, median or None)."""
    main = select_strike(quotes)
    med = market_median([(q["strike"], (q["bid"] + q["ask"]) / 200.0) for q in quotes])
    return main, med


def decide(dk_point, median, main):
    """-> dict(gap, would_bet, side, side_bid, side_ask). Side prices from the main strike (cents)."""
    out = {"gap": None, "would_bet": False, "side": None, "side_bid": None, "side_ask": None}
    if dk_point is None or median is None:
        return out
    gap = dk_point - median
    out["gap"] = round(gap, 4)
    out["would_bet"] = bool(abs(gap) >= GAP_THRESHOLD - 1e-9 and main is not None)
    out["side"] = "YES" if gap > 0 else "NO"
    if main is not None:
        if out["side"] == "YES":
            out["side_bid"], out["side_ask"] = main["bid"], main["ask"]
        else:
            out["side_bid"], out["side_ask"] = 100 - main["ask"], 100 - main["bid"]
    return out


def build_row(now, day, key, tip, commence, dk, quotes, select_strike):
    main, med = kalshi_lines(quotes, select_strike)
    d = decide(dk["point"] if dk else None, med, main)
    mins = (tip - now).total_seconds() / 60 if tip else None
    return {"snapshot_utc": now.isoformat(), "et_date": str(day), "game": key, "commence_time": commence,
            "tip_utc": tip.isoformat() if tip else None, "minutes_to_tip": None if mins is None else round(mins, 1),
            "in_window": None if mins is None else bool(45 <= mins <= 75),
            "dk_point": dk["point"] if dk else None, "dk_over_price": dk["over_price"] if dk else None,
            "dk_under_price": dk["under_price"] if dk else None,
            "kalshi_main_strike": main["strike"] if main else None,
            "kalshi_main_ticker": main["ticker"] if main else None,
            "kalshi_main_bid_c": main["bid"] if main else None, "kalshi_main_ask_c": main["ask"] if main else None,
            "kalshi_median": None if med is None else round(med, 4), "n_valid_strikes": len(quotes),
            "strikes": [[q["strike"], q["bid"], q["ask"]] for q in quotes], **d}


def _load_picks():
    spec = importlib.util.spec_from_file_location("early_overs_picks", ROOT / "scripts" / "early_overs_picks.py")
    mod = sys.modules.get("early_overs_picks")
    if mod is None:
        mod = importlib.util.module_from_spec(spec)
        sys.modules["early_overs_picks"] = mod
        spec.loader.exec_module(mod)
    return mod


def kalshi_games(picks, day: date):
    """[(tip_utc, key, [markets])] for open KXNBATOTAL games whose tip (Kalshi rule: occurrence - 3h) is on ET `day`."""
    occ = {}
    for m in picks.all_open_markets("KXNBAGAME"):
        occ.setdefault(picks.game_key(m["event_ticker"]), m["occurrence_datetime"])
    by: dict[str, list] = {}
    for m in picks.all_open_markets("KXNBATOTAL"):
        by.setdefault(picks.game_key(m["event_ticker"]), []).append(m)
    out = []
    for k, ms in by.items():
        o = occ.get(k) or ms[0].get("occurrence_datetime")
        if not o:
            continue
        tip = parse_z(o) - timedelta(hours=3)
        if tip.astimezone(ET).date() == day:
            out.append((tip, k, ms))
    return sorted(out, key=lambda g: (g[0], g[1]))


def read_quotes(picks, ms, sleep: float = 0.12):
    quotes = []
    for m in sorted(ms, key=lambda m: m["floor_strike"]):
        try:
            bid, ask = picks.best_bid_ask_cents(picks.get(f"/markets/{m['ticker']}/orderbook"))
        except Exception:  # noqa: BLE001
            bid = ask = None
        time.sleep(sleep)
        if picks.valid_quote(bid, ask):
            quotes.append({"strike": m["floor_strike"], "ticker": m["ticker"], "bid": bid, "ask": ask})
    return quotes


def rows_from(now, day, events, games, picks, quote_reader=None):
    quote_reader = quote_reader or read_quotes
    by_key = {k: (tip, ms) for tip, k, ms in games}
    rows = []
    for ev in events:
        k = kalshi_key_for(ev, by_key.keys())
        if k is None:
            continue
        tip, ms = by_key[k]
        if tip <= now:  # already tipped; not a pre-game snapshot
            continue
        rows.append(build_row(now, day, k, tip, ev.get("commence_time"), dk_totals(ev),
                              quote_reader(picks, ms), picks.select_strike))
    return rows


def latest_sample(raw_dir: Path | None = None):
    raw_dir = raw_dir or RAW_DIR
    fs = sorted(raw_dir.glob("*.json")) if raw_dir.exists() else []
    return fs[-1] if fs else None


def run_snapshot(now: datetime | None = None, day: date | None = None, dry_run: bool = False, out_path: Path | None = None,
                 key_path: Path | None = None, fetch=None, picks=None):
    """One Odds API call + Kalshi reads; appends rows. Returns (rows, headers). Raises SnapshotError (key-free)."""
    out_path = out_path or OUT_PATH
    fetch = fetch or fetch_odds
    now = now or datetime.now(timezone.utc)
    day = day or now.astimezone(ET).date()
    key = None
    hdr = {}
    if dry_run:
        s = latest_sample()
        if s is None:
            print(f"[dry-run] would GET {URL}?" + "&".join(f"{k}={v}" for k, v in PARAMS.items()) + "&apiKey=<redacted>")
            return [], hdr
        events = json.loads(s.read_text(encoding="utf-8"))
        print(f"[dry-run] using saved sample {s.name}; no Odds API call, nothing written")
    else:
        key = load_key(key_path)
        try:
            events, hdr = fetch(key)
            save_raw(events, now, key)
        except SnapshotError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SnapshotError(f"snapshot failed ({type(e).__name__}): {redact(e, key)}"[:300]) from None
    picks = picks or _load_picks()
    games = kalshi_games(picks, day)
    rows = rows_from(now, day, events, games, picks)
    if not dry_run and rows:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    log(f"snapshot ok: {len(events)} DK events, {len(games)} Kalshi games, {len(rows)} rows, "
        f"remaining={hdr.get('x-requests-remaining')} used={hdr.get('x-requests-used')}", key)
    return rows, hdr


def safe_snapshot() -> None:
    """Notifier entry point: never raises, never prints the key, logs failures to logs/dk_snapshot.log."""
    key = None
    try:
        if not key_is_set():
            return
        try:
            key = load_key()
        except SnapshotError:
            key = None
        run_snapshot()
    except BaseException as e:  # noqa: BLE001  (must never block the Discord pings)
        log(f"snapshot failed ({type(e).__name__}): {redact(e, key)}"[:400], key)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="take one snapshot (default behaviour)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", help="ET date YYYY-MM-DD (default today ET)")
    a = ap.parse_args(argv)
    day = date.fromisoformat(a.date) if a.date else None
    try:
        rows, hdr = run_snapshot(day=day, dry_run=a.dry_run)
    except SnapshotError as e:
        print(f"ERROR: {e}")
        return 1
    if hdr:
        print(f"requests remaining {hdr.get('x-requests-remaining')}, used {hdr.get('x-requests-used')}")
    print(f"{len(rows)} games")
    for r in rows:
        print(f"{r['game']:<14} min_to_tip {r['minutes_to_tip']:>7} DK {r['dk_point']} "
              f"({r['dk_over_price']}/{r['dk_under_price']}) Kalshi main {r['kalshi_main_strike']} "
              f"{r['kalshi_main_bid_c']}/{r['kalshi_main_ask_c']} median {r['kalshi_median']} gap {r['gap']} "
              f"bet={r['would_bet']} {r['side'] or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
