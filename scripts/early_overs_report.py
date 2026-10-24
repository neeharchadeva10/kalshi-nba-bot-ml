"""Nightly Discord results report for the NBA totals picks (research log #40). NOTIFICATIONS ONLY.

Reads the pinged picks from logs/early_overs_notified.json, looks up each market result and the final score with
public Kalshi GETs (no auth, no keys), and posts a short win/loss summary to the user's Discord webhook (reusing the
notifier's loader/poster; the URL is never printed). No dollar amounts, no account info: the channel is shared.

Usage: .venv/Scripts/python.exe scripts/early_overs_report.py [--night YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://api.elections.kalshi.com/trade-api/v2"
ET = ZoneInfo("America/New_York")
NOTIFIED_PATH = ROOT / "logs" / "early_overs_notified.json"
REPORTED_PATH = ROOT / "logs" / "early_overs_reported.json"
CACHE_PATH = ROOT / "logs" / "early_overs_report_cache.json"
WINDOW = (date(2026, 10, 21), date(2026, 11, 3))
FINAL_FROM, FINAL_TO = (1, 45), (6, 0)   # ET clock window in which a send is forced even if picks are pending
MAX_LEN = 1900
MIN_INTERVAL = 0.35                       # ~3 requests per second


def _load_notifier():
    spec = importlib.util.spec_from_file_location("early_overs_notify", ROOT / "scripts" / "early_overs_notify.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("early_overs_notify", mod)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- pure helpers
def nba_night(now_et: datetime) -> date:
    """ET date of (now - 6 h): games finishing after midnight still belong to the previous night."""
    return (now_et.astimezone(ET) - timedelta(hours=6)).date()


def market_ticker(game: str, strike) -> str:
    """KXNBATOTAL-<game>-<ceil(strike)>, e.g. 241.5 -> -242."""
    return f"KXNBATOTAL-{game}-{math.ceil(float(strike))}"


def pick_won(side: str, result: str):
    """True/False once the market result is yes/no, else None. yes = total went over the line."""
    if result not in ("yes", "no"):
        return None
    return (side == "OVER" and result == "yes") or (side == "UNDER" and result == "no")


def fmt_num(x) -> str:
    return f"{float(x):g}"


def parse_picks(notified: dict) -> list:
    """[(night_date, game, side, strike_str)] from the notifier state; ignores SCHEDULE/CANCEL keys."""
    out = []
    for k, v in notified.items():
        parts = k.split("|")
        if len(parts) != 2 or parts[1] == "SCHEDULE" or not isinstance(v, dict):
            continue
        if v.get("side") not in ("OVER", "UNDER") or v.get("strike") is None:
            continue
        try:
            d = date.fromisoformat(parts[0])
        except ValueError:
            continue
        out.append((d, parts[1], v["side"], str(v["strike"])))
    return sorted(out)


def in_window(d: date) -> bool:
    return WINDOW[0] <= d <= WINDOW[1]


def in_final_window(now_et: datetime) -> bool:
    t = (now_et.hour, now_et.minute)
    return FINAL_FROM <= t < FINAL_TO


def record(entries) -> tuple:
    """(wins, losses) over entries with won True/False; pending (None) ignored."""
    w = sum(1 for e in entries if e is True)
    l = sum(1 for e in entries if e is False)
    return w, l


def format_line(p: dict) -> str:
    teams = f"{p['game'][7:10]} @ {p['game'][10:13]}"
    base = f"{teams} · {p['side']} {fmt_num(p['strike'])}"
    if p["won"] is None:
        return f"⏳ {base} · pending"
    mark = "✅" if p["won"] else "❌"
    if p.get("away_pts") is None or p.get("home_pts") is None:
        return f"{mark} {base} · final score n/a"
    return f"{mark} {base} · final {p['away_pts'] + p['home_pts']} ({p['away_pts']}–{p['home_pts']})"


def format_report(night: date, picks: list, tonight: tuple, two_week: tuple, forced_pending: bool = False) -> str:
    head = f"NBA totals — {night:%a %b} {night.day}"
    if not picks:
        return f"{head}: no picks tonight (no game was 4+ points off the line)."
    lines = [head + " results"]
    lines += [format_line(p) for p in picks]
    lines.append(f"Tonight: {tonight[0]}–{tonight[1]}")
    lines.append(f"2-week record (Oct 21–Nov 3): {two_week[0]}–{two_week[1]}")
    if forced_pending:
        lines.append("(pending results will count in the next report)")
    text = "\n".join(lines)
    return text[:MAX_LEN]


# ---------------------------------------------------------------- IO
def load_json(path: Path) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_json(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


class PublicGetter:
    """Unauthenticated GET against the public Kalshi API, throttled to ~3 req/s. Returns parsed JSON."""

    def __init__(self, min_interval: float = MIN_INTERVAL):
        self.s = requests.Session()
        self.mi, self._last = min_interval, 0.0

    def __call__(self, path: str, params=None) -> dict:
        for attempt in range(3):
            w = self._last + self.mi - time.time()
            if w > 0:
                time.sleep(w)
            self._last = time.time()
            r = self.s.get(BASE + path, params=params, timeout=15)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        raise requests.HTTPError("rate limited")


def scan_milestones(get, night: date, wanted: set) -> dict:
    """{game_key: {"id", "start"}} for basketball_game milestones whose related events include KXNBATOTAL-<game>."""
    start = datetime(night.year, night.month, night.day, 12, tzinfo=ET).astimezone(timezone.utc)
    params = {"category": "Sports", "minimum_start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 200}
    found = {}
    for _ in range(30):
        j = get("/milestones", dict(params))
        ms = j.get("milestones") or []
        for m in ms:
            if m.get("type") != "basketball_game":
                continue
            for et in m.get("related_event_tickers") or []:
                if et.startswith("KXNBATOTAL-") and et[len("KXNBATOTAL-"):] in wanted:
                    found[et[len("KXNBATOTAL-"):]] = {"id": m.get("id"), "start": m.get("start_date")}
        cur = j.get("cursor")
        if not cur or not ms:
            break
        params["cursor"] = cur
    return found


def resolve_pick(get, cache: dict, night: date, game: str, side: str, strike: str, ms_cache: dict,
                 need_score: bool) -> dict:
    """Result (+score, tip) for one pick. Settled results are cached; pending is never cached."""
    ck = f"{night.isoformat()}|{game}"
    ent = cache.get(ck)
    if not (ent and ent.get("result") in ("yes", "no") and str(ent.get("strike")) == strike):
        ent = None
        try:
            m = (get("/markets/" + market_ticker(game, strike)).get("market")) or {}
        except Exception as e:  # noqa: BLE001
            print(f"WARN market fetch failed for {game} ({type(e).__name__})", file=sys.stderr)
            m = {}
        try:
            fs = float(m.get("floor_strike"))
        except (TypeError, ValueError):
            fs = None
        if fs is not None and fs != float(strike):
            print(f"WARN floor_strike {fs} != pick strike {strike} for {game}; treating as pending", file=sys.stderr)
        elif m.get("result") in ("yes", "no"):
            ent = {"result": m["result"], "strike": strike}
    out = {"game": game, "side": side, "strike": strike, "won": None, "away_pts": None, "home_pts": None,
           "start": None}
    if ent is None:
        return out
    if need_score and ent.get("away_pts") is None:
        try:
            if night not in ms_cache:
                ms_cache[night] = scan_milestones(get, night, {game})
            ms = ms_cache[night]
            if game not in ms:  # the scan was built for specific games; rescan once if this one is missing
                ms.update(scan_milestones(get, night, {game}))
            info = ms.get(game)
            if info:
                ent["start"] = info.get("start")
                det = ((get("/live_data/basketball_game/milestone/%s" % info["id"]).get("live_data") or {})
                       .get("details") or {})
                if det.get("away_points") is not None and det.get("home_points") is not None:
                    ent["away_pts"], ent["home_pts"] = int(float(det["away_points"])), int(float(det["home_points"]))
                    ent["status"] = det.get("status")
        except Exception as e:  # noqa: BLE001
            print(f"WARN score fetch failed for {game} ({type(e).__name__})", file=sys.stderr)
    cache[ck] = ent
    out.update(won=pick_won(side, ent["result"]), away_pts=ent.get("away_pts"), home_pts=ent.get("home_pts"),
               start=ent.get("start"))
    return out


# ---------------------------------------------------------------- main
def run(night: date, now: datetime, get, poster, dry_run: bool, webhook_loader=None, notifier=None,
        notified_path=None, reported_path=None, cache_path=None, log_path=None) -> tuple:
    """Returns (status, text). status: sent | dry-run | already-sent | waiting | error."""
    notified_path = notified_path or NOTIFIED_PATH
    reported_path = reported_path or REPORTED_PATH
    cache_path = cache_path or CACHE_PATH
    now_et = now.astimezone(ET)
    reported = load_json(reported_path)
    if night.isoformat() in reported and not dry_run:
        return "already-sent", ""
    all_picks = [p for p in parse_picks(load_json(notified_path)) if p[0] <= night]
    cache = load_json(cache_path)
    ms_cache = {}
    tonight_rows, others = [], []
    for d, game, side, strike in all_picks:
        if d == night:
            tonight_rows.append(resolve_pick(get, cache, d, game, side, strike, ms_cache, need_score=True))
        elif in_window(d):
            others.append(resolve_pick(get, cache, d, game, side, strike, ms_cache, need_score=False))
    save_json(cache_path, cache)
    tonight_rows.sort(key=lambda p: (p["start"] or "9999", p["game"]))
    n_pending = sum(1 for p in tonight_rows if p["won"] is None)
    tonight = record([p["won"] for p in tonight_rows])
    two_week_rows = tonight_rows if in_window(night) else []
    two_week = record([p["won"] for p in two_week_rows] + [p["won"] for p in others])
    forced = n_pending > 0
    if n_pending and not in_final_window(now_et):
        if dry_run:
            text = format_report(night, tonight_rows, tonight, two_week, forced_pending=False)
            return "dry-run", f"[would NOT send yet: {n_pending} pick(s) pending, outside the final window]\n{text}"
        return "waiting", ""
    text = format_report(night, tonight_rows, tonight, two_week, forced_pending=forced)
    if dry_run:
        return "dry-run", text
    notifier = notifier or _load_notifier()
    try:
        url = (webhook_loader or notifier.load_webhook)()
        (poster or notifier.post_discord)(url, text)
    except notifier.NotifyError as e:
        print(f"ERROR: {e}")
        return "error", text
    reported[night.isoformat()] = now.isoformat()
    save_json(reported_path, reported)
    notifier.log_sent("report", text, now, log_path)
    return "sent", text


def main(argv=None, get=None, poster=None, now=None) -> int:
    for st in (sys.stdout, sys.stderr):  # emoji output must not crash on a cp1252 console / redirect
        try:
            st.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--night", help="YYYY-MM-DD (default: the NBA night for the current time)")
    ap.add_argument("--dry-run", action="store_true", help="print the message; send nothing; keep send-state unchanged")
    a = ap.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    night = date.fromisoformat(a.night) if a.night else nba_night(now.astimezone(ET))
    status, text = run(night, now, get or PublicGetter(), poster, a.dry_run)
    if status == "dry-run":
        print(text)
    elif status == "sent":
        print(f"report sent for {night}")
    elif status == "already-sent":
        print(f"report for {night} already sent")
    elif status == "waiting":
        print(f"{now.astimezone(ET):%Y-%m-%d %H:%M} ET: night {night} still has pending picks; nothing sent")
    return 1 if status == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
