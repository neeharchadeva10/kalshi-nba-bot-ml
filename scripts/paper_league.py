"""Forward paper league for research log #67 (NBA + NFL totals rule variants and controls). PAPER ONLY.

Safe to run every 30 min (idempotent). Reads the paper tape (data/paper_tape/<ET date>/snap.jsonl.gz), the E1 locked
predictions (logs/score_model/predictions.jsonl), the F42 projections logged by the notifier (logs/early_overs_picks.jsonl;
computed with scripts/early_overs_picks.py's own machinery, read-only, if no logged row exists), nflverse games.csv and the
NFL wind forecasts (data/paper_tape/nfl_wind/*.jsonl). The only network call is the public unauthenticated
GET /markets/{ticker} used to read the settlement result (raw response cached under data/paper_league/settle_raw/).
No orders, no keys, no kpm.execution, nothing to Discord.

Outputs (append-only, never rewritten): logs/paper_league/decisions.jsonl, settlements.jsonl. Derived: summary.json,
paper_league.log, logs/paper_league.heartbeat.

Decision (research log #67): the LAST tape snapshot with ts in [tip - 75 min, tip - 60 min]; main strike = YES mid nearest
0.50 among two-sided strikes with (ask - bid) <= 10c; entry = 1 contract at the side's ask + 1c + fee,
fee = ceil_to_$0.0001(0.07 * P * (1 - P)), P = ask + 0.01. Every input must be time-stamped at or before the snapshot.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kpm.collectors.nba_tip_time import get_tip_time  # noqa: E402

ET = ZoneInfo("America/New_York")
BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
log = logging.getLogger("paper_league")

COIN_SALT = "20261027"
WIN_FROM, WIN_TO = timedelta(minutes=75), timedelta(minutes=60)      # window = [tip - 75 min, tip - 60 min]
GRACE = timedelta(minutes=2)          # wait this long after tip - 60 min so the last in-window snapshot is on disk
MAX_SPREAD_C = 10
SLIP = Decimal("0.01")
COVER_START = {"NBA": datetime(2026, 10, 23, 23, 0, tzinfo=timezone.utc),       # decision time (tip - 60 min) >= this
               "NFL": datetime(2026, 10, 25, 0, 0, tzinfo=ET).astimezone(timezone.utc)}   # kickoff >= 2026-10-25 ET
SETTLE_AFTER = {"NBA": timedelta(hours=2, minutes=30), "NFL": timedelta(hours=3, minutes=30)}
NBA_GAP_4, NBA_GAP_8 = 4.0, 8.0

RULES = {
    "N1": ("NBA", "F42 gap >= 4", False), "N2": ("NBA", "F42 gap >= 8", False),
    "N3": ("NBA", "E1 gap >= 4", False), "N4": ("NBA", "E1 gap >= 8", False),
    "N5": ("NBA", "always UNDER", True), "N6": ("NBA", "always OVER", True), "N7": ("NBA", "coin flip", True),
    "B2": ("NFL", "forecast wind 10-19 open-air UNDER", False), "B3": ("NFL", "forecast wind >= 20 open-air (control)", True),
    "B4": ("NFL", "SNF/MNF UNDER", False), "B5": ("NFL", "TNF UNDER (control)", True),
    "NFL-UNDER": ("NFL", "always UNDER", True), "NFL-OVER": ("NFL", "always OVER", True),
    "NFL-COIN": ("NFL", "coin flip", True),
}
SPORT_RULES = {s: [r for r, v in RULES.items() if v[0] == s] for s in ("NBA", "NFL")}
SERIES = {"NBA": "KXNBATOTAL", "NFL": "KXNFLTOTAL"}


# ------------------------------------------------------------------ small helpers
def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def cents(x) -> int | None:
    try:
        return None if x is None else int(round(float(x) * 100))
    except (TypeError, ValueError):
        return None


def fee_dollars(price_dollars) -> Decimal:
    """Kalshi taker fee for ONE contract: ceil_to_$0.0001(0.07 * P * (1 - P)). Exact decimal arithmetic."""
    p = Decimal(str(price_dollars))
    raw = Decimal("0.07") * p * (Decimal(1) - p)
    return (raw * 10000).to_integral_value(rounding=ROUND_CEILING) / 10000


def entry_cost(ask_c: int):
    """(P, fee) for a taker buy at ask_c cents + 1c slippage."""
    p = (Decimal(ask_c) / 100) + SLIP
    return p, fee_dollars(p)


def net_of(payout: int, p: Decimal, fee: Decimal) -> float:
    return float(Decimal(payout) - p - fee)


def coin_side(game_key: str) -> str:
    """Even parity -> OVER, odd -> UNDER (the registration fixes the hash, not the mapping; see README note)."""
    return "OVER" if int(hashlib.sha256((game_key + COIN_SALT).encode()).hexdigest(), 16) % 2 == 0 else "UNDER"


def append_jsonl(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
    with open(path, "ab") as fh:
        fh.write(blob.encode("utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if ln:
                    try:
                        out.append(json.loads(ln))
                    except ValueError:
                        log.warning("bad json line skipped in %s", path.name)
    except OSError:
        pass
    return out


# ------------------------------------------------------------------ tape
class Tape:
    """Reads data/paper_tape/<ET date>/snap.jsonl.gz, keeping only rows of the requested series (cached per call)."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._cache: dict[tuple, list[dict]] = {}

    def rows(self, et_date: str, series: str) -> list[dict]:
        k = (et_date, series)
        if k not in self._cache:
            self._cache[k] = self._load(et_date, series)
        return self._cache[k]

    def _load(self, et_date: str, series: str) -> list[dict]:
        p = self.root / "data" / "paper_tape" / et_date / "snap.jsonl.gz"
        out = []
        if not p.exists():
            return out
        try:
            with gzip.open(p, "rt", encoding="utf-8") as fh:
                for ln in fh:
                    if series not in ln:
                        continue
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    if r.get("type") == "mkt" and r.get("series") == series:
                        out.append(r)
        except (EOFError, OSError, gzip.BadGzipFile) as exc:      # a member still being appended: keep what was read
            log.warning("tape %s read stopped early: %s", p, exc.__class__.__name__)
        return out

    def event_rows(self, tip: datetime, series: str) -> list[dict]:
        dates = {(tip - WIN_FROM).astimezone(ET).strftime("%Y-%m-%d"), (tip - WIN_TO).astimezone(ET).strftime("%Y-%m-%d")}
        rows = []
        for d in sorted(dates):
            rows += self.rows(d, series)
        return rows


def window_snapshot(rows: list[dict], tip: datetime):
    """rows of ONE event. Returns (snapshot ts string, rows at that ts) for the LAST ts in [tip-75m, tip-60m], else (None, [])."""
    lo, hi = tip - WIN_FROM, tip - WIN_TO
    best = None
    for r in rows:
        t = parse_ts(r.get("ts_utc"))
        if t is not None and lo <= t <= hi and (best is None or t > best[0]):
            best = (t, r["ts_utc"])
    if best is None:
        return None, []
    return best[1], [r for r in rows if r.get("ts_utc") == best[1]]


def quote_of(r: dict):
    """(strike, bid_c, ask_c) when two-sided (0 < bid <= ask < 100), else None."""
    bid, ask = cents(r.get("yes_bid_f", r.get("yes_bid"))), cents(r.get("yes_ask_f", r.get("yes_ask")))
    s = r.get("floor_strike")
    if s is None or bid is None or ask is None or not (0 < bid <= ask < 100):
        return None
    return float(s), bid, ask


def main_strike(snap_rows: list[dict]):
    """YES mid nearest 0.50 among two-sided strikes with (ask - bid) <= 10c; ties -> lower strike. Returns row or None."""
    best = None
    for r in snap_rows:
        q = quote_of(r)
        if q is None or q[2] - q[1] > MAX_SPREAD_C:
            continue
        key = (abs(q[1] + q[2] - 100), q[0])                 # 2*mid distance from 100 (integer cents, exact)
        if best is None or key < best[0]:
            best = (key, r)
    return None if best is None else best[1]


def implied_median(snap_rows: list[dict]):
    """Linear interpolation of the YES mids (all two-sided strikes) at 0.50; None if the ladder never crosses 0.50."""
    pts = sorted({q[0]: (q[1] + q[2]) / 200.0 for q in (quote_of(r) for r in snap_rows) if q}.items())
    for (s0, m0), (s1, m1) in zip(pts, pts[1:]):
        if m0 >= 0.5 > m1:
            return round(s0 + (m0 - 0.5) / (m0 - m1) * (s1 - s0), 4)
    return None


# ------------------------------------------------------------------ inputs
def load_e1(root: Path) -> dict:
    out: dict[str, list] = {}
    for p in read_jsonl(Path(root) / "logs" / "score_model" / "predictions.jsonl"):
        t, k = parse_ts(p.get("ts_utc")), p.get("key")
        if k and t is not None and isinstance(p.get("e1_total"), (int, float)):
            out.setdefault(k, []).append((t, float(p["e1_total"]), p))
    return out


def load_f42_log(root: Path) -> dict:
    out: dict[str, list] = {}
    for p in read_jsonl(Path(root) / "logs" / "early_overs_picks.jsonl"):
        t, g = parse_ts(p.get("ts")), p.get("game")
        if g and t is not None and isinstance(p.get("proj"), (int, float)):
            out.setdefault(g, []).append((t, float(p["proj"])))
    return out


def latest_before(items: list, snap: datetime):
    ok = [x for x in items if x[0] <= snap]
    return max(ok, key=lambda x: x[0]) if ok else None


MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
KEY_RE = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})([A-Z]{2,3})([A-Z]{2,3})$")


def parse_nba_key(key: str):
    """'26OCT23DETMIA' -> (ET date, away, home); None if it does not parse."""
    m = re.match(r"^(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})$", key)
    if not m or m.group(2) not in MONTHS:
        return None
    from datetime import date
    return date(2000 + int(m.group(1)), MONTHS.index(m.group(2)) + 1, int(m.group(3))), m.group(4), m.group(5)


def default_projector(games: list[dict]) -> dict:
    """F42 projections computed the way scripts/early_overs_picks.py does (RULE grid_winner), read-only import.
    games: [{key, tip}] all on possibly several ET dates. Returns {key: proj}. Only data dated BEFORE each ET date is used
    by that machinery. Raises on failure (caller defers)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import early_overs_picks as eop            # noqa: E402  (heavy import, only when a projection is missing from the log)
    box27 = eop.fetch_box27()
    out: dict = {}
    by_day: dict = {}
    for g in games:
        p = parse_nba_key(g["key"])
        if p:
            by_day.setdefault(p[0], []).append((g["key"], p[1], p[2], g["tip"]))
    for day, gl in by_day.items():
        out.update(eop._grid_tonight().project_winner_for_date(day, gl, box27=box27))
    return out


# ------------------------------------------------------------------ NFL inputs
KALSHI_CODES = {"LA": ["LAR", "LA"], "JAX": ["JAX", "JAC"], "WAS": ["WAS", "WSH"], "LAC": ["LAC"], "LV": ["LV"]}


def load_nfl_games(path: Path) -> list[dict]:
    import pandas as pd
    df = pd.read_csv(path, dtype=str)
    out = []
    for _, r in df.iterrows():
        gd, gt = r.get("gameday"), r.get("gametime")
        if not isinstance(gd, str) or not isinstance(gt, str):
            continue
        try:
            ko = datetime.strptime(gd + " " + gt[:5], "%Y-%m-%d %H:%M").replace(tzinfo=ET).astimezone(timezone.utc)
        except ValueError:
            continue
        if ko < COVER_START["NFL"]:
            continue
        out.append({"game_id": r.get("game_id"), "gameday": gd, "gametime": gt[:5], "weekday": r.get("weekday"),
                    "game_type": r.get("game_type"), "week": int(float(r["week"])) if isinstance(r.get("week"), str) else None,
                    "away": r.get("away_team"), "home": r.get("home_team"),
                    "roof": r.get("roof") if isinstance(r.get("roof"), str) else None,
                    "stadium_id": r.get("stadium_id") if isinstance(r.get("stadium_id"), str) else None, "ko": ko})
    return out


def nfl_event_candidates(g: dict) -> list[str]:
    d = datetime.strptime(g["gameday"], "%Y-%m-%d")
    pre = f"{d:%y}{MONTHS[d.month - 1]}{d:%d}"
    return [f"KXNFLTOTAL-{pre}{a}{h}" for a in KALSHI_CODES.get(g["away"], [g["away"]])
            for h in KALSHI_CODES.get(g["home"], [g["home"]])]


def load_stadium_categories(path: Path):
    """{stadium_id: roof category lowercase} from reports/output/nfl_totals/weather/stadiums.csv, or (None, reason)."""
    if not path.exists():
        return None, f"{path.name} missing"
    try:
        import pandas as pd
        df = pd.read_csv(path, dtype=str)
    except Exception as exc:  # noqa: BLE001
        return None, f"{path.name} unreadable: {exc.__class__.__name__}"
    cat_col = next((c for c in ("roof_category", "category", "roof_type", "roof_class") if c in df.columns), None)
    id_col = "stadium_id" if "stadium_id" in df.columns else None
    if cat_col is None or id_col is None:
        return None, f"{path.name} has no stadium_id / roof category column (columns: {list(df.columns)[:12]})"
    return {str(r[id_col]): str(r[cat_col]).strip().lower() for _, r in df.iterrows()}, None


WIND_FIELDS = ("wind_mph", "wind_kickoff_mph", "wind_10m_mph", "wind_speed_10m_mph", "forecast_wind_mph")


def load_wind_forecasts(root: Path) -> dict:
    """{game_id: [(fetch_utc, wind_mph, row)]} from data/paper_tape/nfl_wind/*.jsonl (rows need game_id, fetch_utc, a wind field)."""
    out: dict[str, list] = {}
    for p in sorted((Path(root) / "data" / "paper_tape" / "nfl_wind").glob("*.jsonl")):
        for r in read_jsonl(p):
            t = parse_ts(r.get("fetch_utc"))
            flat = {**(r.get("values") or {}), **r}                 # scripts/nfl_wind_forecast.py nests them under "values"
            w = next((flat[f] for f in WIND_FIELDS if isinstance(flat.get(f), (int, float))), None)
            if r.get("game_id") and t is not None and w is not None:
                out.setdefault(r["game_id"], []).append((t, float(w), r))
    return out


# ------------------------------------------------------------------ rules
def gap_rule(value, strike, thr):
    if value is None:
        return None
    g = value - strike
    return "OVER" if g >= thr else "UNDER" if g <= -thr else "NONE"


def evaluate_nba(ctx: dict) -> dict:
    """ctx: strike, key, proj, proj_reason, e1, e1_reason. Returns {rule: (status, side, reason, inputs)}."""
    k, out = ctx["strike"], {}
    for rid, val, thr, why in (("N1", ctx["proj"], NBA_GAP_4, ctx["proj_reason"]), ("N2", ctx["proj"], NBA_GAP_8, ctx["proj_reason"]),
                               ("N3", ctx["e1"], NBA_GAP_4, ctx["e1_reason"]), ("N4", ctx["e1"], NBA_GAP_8, ctx["e1_reason"])):
        s = gap_rule(val, k, thr)
        if s is None:
            out[rid] = ("no_decision", None, why, {})
        elif s == "NONE":
            out[rid] = ("no_bet", None, f"|gap| {abs(val - k):.2f} < {thr:g}", {"gap": round(val - k, 4)})
        else:
            out[rid] = ("bet", s, f"gap {val - k:+.2f} >= {thr:g}", {"gap": round(val - k, 4)})
    out["N5"] = ("bet", "UNDER", "always under", {})
    out["N6"] = ("bet", "OVER", "always over", {})
    out["N7"] = ("bet", coin_side(ctx["key"]), "coin flip sha256(key+20261027) parity", {})
    return out


def nfl_wind_for(g: dict, forecasts: dict):
    """(wind or None, reason or None, info) = the latest forecast with fetch_utc <= kickoff - 24 h."""
    rows = forecasts.get(g["game_id"], [])
    if not rows:
        return None, "no forecast rows for this game", {}
    ok = [x for x in rows if x[0] <= g["ko"] - timedelta(hours=24)]
    if not ok:
        return None, "only forecasts fetched < 24 h before kickoff (time-stamp guard)", {"n_rows": len(rows)}
    t, w, _ = max(ok, key=lambda x: x[0])
    return w, None, {"forecast_fetch_utc": iso(t), "forecast_wind_mph": w}


def evaluate_nfl(g: dict, ctx: dict) -> dict:
    """ctx: key, categories (dict|None), cat_reason, forecasts (dict). B2: forecast in [10, 20) mph; B3: >= 20 mph
    (continuous reading of '10-19' / '>= 20'). Open-air = roof in {outdoors, open} AND stadiums.csv category 'open-air'."""
    out = {}
    wind, wreason, winfo = nfl_wind_for(g, ctx["forecasts"])
    open_air, oa_reason = None, None
    if ctx["categories"] is None:
        oa_reason = ctx["cat_reason"]
    elif g["roof"] is None:
        oa_reason = "roof blank in games.csv"
    else:
        open_air = g["roof"] in ("outdoors", "open") and ctx["categories"].get(str(g["stadium_id"])) == "open-air"
        if str(g["stadium_id"]) not in ctx["categories"]:
            open_air, oa_reason = None, f"stadium {g['stadium_id']} not in stadiums.csv"
    for rid, lo, hi in (("B2", 10.0, 20.0), ("B3", 20.0, math.inf)):
        if oa_reason is not None:
            out[rid] = ("no_decision", None, oa_reason, {})
        elif wind is None:
            out[rid] = ("no_decision", None, wreason, winfo)
        elif not open_air:
            out[rid] = ("no_bet", None, "not an open-air stadium", dict(winfo))
        elif lo <= wind < hi:
            out[rid] = ("bet", "UNDER", f"forecast wind {wind:g} mph in [{lo:g}, {hi:g})", dict(winfo))
        else:
            out[rid] = ("no_bet", None, f"forecast wind {wind:g} mph outside [{lo:g}, {hi:g})", dict(winfo))
    reg = g["game_type"] == "REG"
    prime = reg and g["weekday"] in ("Sunday", "Monday") and g["gametime"] >= "19:00"
    tnf = reg and g["weekday"] == "Thursday" and (g["week"] or 0) >= 2
    out["B4"] = ("bet", "UNDER", "SNF/MNF", {}) if prime else ("no_bet", None, "not SNF/MNF", {})
    out["B5"] = ("bet", "UNDER", "TNF week>=2", {}) if tnf else ("no_bet", None, "not TNF week>=2", {})
    out["NFL-UNDER"] = ("bet", "UNDER", "always under", {})
    out["NFL-OVER"] = ("bet", "OVER", "always over", {})
    out["NFL-COIN"] = ("bet", coin_side(ctx["key"]), "coin flip sha256(key+20261027) parity", {})
    return out


# ------------------------------------------------------------------ league
class League:
    def __init__(self, root: Path = ROOT, now: datetime | None = None, projector=None, fetch_market=None,
                 games_csv: Path | None = None, stadiums_csv: Path | None = None, sleep=time.sleep):
        self.root = Path(root)
        self.now = now or datetime.now(timezone.utc)
        self.projector = projector or default_projector
        self.fetch_market = fetch_market or self._http_market
        self.sleep = sleep
        self.games_csv = games_csv
        self.stadiums_csv = stadiums_csv or self.root / "reports" / "output" / "nfl_totals" / "weather" / "stadiums.csv"
        self.dir = self.root / "logs" / "paper_league"
        self.dec_path, self.set_path = self.dir / "decisions.jsonl", self.dir / "settlements.jsonl"
        self.stats = {"pending": 0, "decided_games": 0, "decisions": 0, "settled": 0, "deferred": 0, "errors": []}
        self._sess = None
        self._last_req = 0.0

    # ---- network (public GET /markets/{ticker} only)
    def _http_market(self, ticker: str) -> dict:
        import requests
        if self._sess is None:
            self._sess = requests.Session()
            self._sess.headers["User-Agent"] = "kpm-research/0.1 (read-only paper league)"
        wait = self._last_req + 0.25 - time.monotonic()
        if wait > 0:
            self.sleep(wait)
        self._last_req = time.monotonic()
        r = self._sess.get(f"{BASE_URL}/markets/{ticker}", timeout=20)
        r.raise_for_status()
        return r.json()

    # ---- games
    def nba_games(self, tape: Tape) -> list[dict]:
        preds = load_e1(self.root)
        games: dict[str, dict] = {}
        for k, items in preds.items():
            st = parse_ts(items[-1][2].get("start_utc"))
            if st is not None:
                games[k] = {"sport": "NBA", "key": k, "tip": st, "tip_source": "predictions.start_utc"}
        # tape events with no locked prediction: hoopR schedule, else expected_expiration_time - 3 h (logged)
        today = self.now.astimezone(ET).date()
        for off in (-1, 0, 1, 2):
            d = (today + timedelta(days=off)).strftime("%Y-%m-%d")
            for r in tape.rows(d, SERIES["NBA"]):
                k = r["event_ticker"].split("-", 1)[1]
                if k not in games:
                    tt = get_tip_time(k, parse_ts(r.get("expected_expiration_time")), live=False, now=self.now)
                    if tt.tip is not None:
                        games[k] = {"sport": "NBA", "key": k, "tip": tt.tip,
                                    "tip_source": "hoopR schedule" if tt.source == "hoopr"
                                    else "FALLBACK tape expected_expiration_time - 3h"}
                        log.warning("tip for %s missing from predictions.jsonl; using %s", k, games[k]["tip_source"])
        for g in games.values():
            p = parse_nba_key(g["key"])
            g["night"] = p[0].isoformat() if p else g["tip"].astimezone(ET).strftime("%Y-%m-%d")
            g["event"] = "KXNBATOTAL-" + g["key"]
        return list(games.values())

    def nfl_games(self) -> list[dict]:
        cands = [self.games_csv] if self.games_csv else [
            self.root / "data" / "paper_tape" / "nfl_wind" / "games.csv", self.root / "data" / "raw" / "nflverse" / "games" / "games.csv"]
        cands += sorted((self.root / "data" / "paper_tape" / "nfl_wind").glob("games*.csv")) if not self.games_csv else []
        have = [c for c in cands if c and Path(c).exists()]
        if not have:
            log.warning("no nflverse games.csv found")
            return []
        path = max(have, key=lambda p: Path(p).stat().st_mtime)
        out = []
        for g in load_nfl_games(Path(path)):
            out.append(dict(g, sport="NFL", tip=g["ko"], tip_source="nflverse kickoff", night=g["gameday"],
                            key=None, event=None, events=nfl_event_candidates(g)))
        return out

    # ---- decisions
    def run_decisions(self) -> None:
        tape = Tape(self.root)
        decided = {(r["sport"], r["game_id"], r["rule"]) for r in read_jsonl(self.dec_path)}
        e1, f42 = load_e1(self.root), load_f42_log(self.root)
        games = self.nba_games(tape) + self.nfl_games()
        cats, cat_reason = (None, None)
        forecasts = {}
        if any(g["sport"] == "NFL" for g in games):
            cats, cat_reason = load_stadium_categories(self.stadiums_csv)
            if cats is None:
                log.warning("NFL B2/B3 cannot decide: %s", cat_reason)
            forecasts = load_wind_forecasts(self.root)
        todo = []
        for g in sorted(games, key=lambda g: g["tip"]):
            dec_time = g["tip"] - WIN_TO
            ident = g["key"] or g["game_id"]
            if g["sport"] == "NBA" and dec_time < COVER_START["NBA"]:
                continue
            if g["sport"] == "NFL" and g["tip"] < COVER_START["NFL"]:
                continue
            if all((g["sport"], g.get("game_id") or g["key"], r) in decided for r in SPORT_RULES[g["sport"]]):
                continue
            if self.now < dec_time + GRACE:
                self.stats["pending"] += 1
                continue
            todo.append(g)
        # F42 projections missing from the log -> compute once (read-only machinery); failure defers the game
        need = []
        snaps = {}
        for g in todo:
            ev_rows = self._event_rows(tape, g)
            snap_ts, snap_rows = window_snapshot(ev_rows, g["tip"])
            snaps[id(g)] = (snap_ts, snap_rows)
            if g["sport"] == "NBA" and snap_ts and main_strike(snap_rows) is not None:
                st = parse_ts(snap_ts)
                if latest_before(f42.get(g["key"], []), st) is None:
                    need.append(g)
        computed, comp_err = {}, None
        if need:
            try:
                computed = self.projector([{"key": g["key"], "tip": g["tip"]} for g in need])
            except Exception as exc:  # noqa: BLE001
                comp_err = f"{exc.__class__.__name__}: {exc}"[:200]
                log.warning("F42 projection compute failed (%s)", comp_err)
        rows_out = []
        for g in todo:
            snap_ts, snap_rows = snaps[id(g)]
            rows = self._decide_game(g, snap_ts, snap_rows, e1, f42, computed, comp_err, cats, cat_reason, forecasts, decided)
            if rows is None:
                self.stats["deferred"] += 1
                continue
            self.stats["decided_games"] += 1
            rows_out += rows
        append_jsonl(self.dec_path, rows_out)
        self.stats["decisions"] = len(rows_out)
        for r in rows_out:
            log.info("%s %s %s %s %s%s", r["sport"], r["game_id"], r["rule"], r["status"], r.get("side") or "",
                     f" @ ask {r['ask_c']}c P={r['price']} fee={r['fee']} ({r['reason']})" if r["status"] == "bet" else f" ({r['reason']})")

    def _event_rows(self, tape: Tape, g: dict) -> list[dict]:
        rows = tape.event_rows(g["tip"], SERIES[g["sport"]])
        evs = [g["event"]] if g["sport"] == "NBA" else g["events"]
        keep = [r for r in rows if r.get("event_ticker") in evs]
        if g["sport"] == "NFL" and keep:
            g["event"] = keep[0]["event_ticker"]
            g["key"] = g["event"].split("-", 1)[1]
        return keep

    def _decide_game(self, g, snap_ts, snap_rows, e1, f42, computed, comp_err, cats, cat_reason, forecasts, decided):
        sport, gid = g["sport"], g.get("game_id") or g["key"]
        base = {"sport": sport, "game_id": gid, "game_key": g["key"], "night": g["night"], "tip_utc": iso(g["tip"]),
                "tip_source": g["tip_source"], "window_utc": [iso(g["tip"] - WIN_FROM), iso(g["tip"] - WIN_TO)],
                "decided_utc": iso(self.now)}

        def all_rules(status, reason, **extra):
            return [dict(base, rule=r, label=RULES[r][1], control=RULES[r][2], status=status, side=None, reason=reason, **extra)
                    for r in SPORT_RULES[sport] if (sport, gid, r) not in decided]

        if sport == "NFL" and g["key"] is None:
            g["key"] = g["events"][0].split("-", 1)[1]            # never seen in the tape: key for the record only
            base["game_key"] = g["key"]
        if snap_ts is None:
            log.warning("GAP %s %s: no tape snapshot in %s", sport, gid, base["window_utc"])
            return all_rules("no_decision", "no tape snapshot in the decision window")
        pick = main_strike(snap_rows)
        if pick is None:
            return all_rules("no_decision", "no two-sided strike with spread <= 10c", snapshot_ts=snap_ts)
        snap_t = parse_ts(snap_ts)
        strike, bid_c, ask_c = quote_of(pick)
        common = dict(base, snapshot_ts=snap_ts, ticker=pick["ticker"], strike=strike, yes_bid_c=bid_c, yes_ask_c=ask_c,
                      yes_bid_size=pick.get("yes_bid_size_f"), yes_ask_size=pick.get("yes_ask_size_f"),
                      implied_median=implied_median(snap_rows), n_strikes=len(snap_rows))
        if sport == "NBA":
            ctx = {"strike": strike, "key": g["key"], "proj": None, "proj_reason": None, "e1": None, "e1_reason": None}
            lg = latest_before(f42.get(g["key"], []), snap_t)
            if lg is not None:
                ctx["proj"], common["proj_source"], common["proj_ts"] = lg[1], "logs/early_overs_picks.jsonl", iso(lg[0])
            elif g["key"] in computed:
                ctx["proj"], common["proj_source"] = float(computed[g["key"]]), "computed (early_overs_picks machinery)"
            else:
                if comp_err and self.now < g["tip"] + timedelta(hours=3):
                    return None                                   # transient: retry next run, still before the game ends
                ctx["proj_reason"] = f"no F42 projection logged at or before the decision and compute failed ({comp_err})"
            ev = latest_before(e1.get(g["key"], []), snap_t)
            if ev is not None:
                ctx["e1"], common["e1_ts"] = ev[1], iso(ev[0])
            else:
                late = e1.get(g["key"], [])
                ctx["e1_reason"] = ("E1 prediction time-stamped after the decision (guard)" if late
                                    else "no E1 prediction for this game")
            common["f42_proj"], common["e1_total"] = ctx["proj"], ctx["e1"]
            res = evaluate_nba(ctx)
        else:
            res = evaluate_nfl(g, {"key": g["key"], "categories": cats, "cat_reason": cat_reason, "forecasts": forecasts})
        out = []
        for rid in SPORT_RULES[sport]:
            if (sport, gid, rid) in decided:
                continue
            status, side, reason, inputs = res[rid]
            row = dict(common, rule=rid, label=RULES[rid][1], control=RULES[rid][2], status=status, side=side, reason=reason,
                       inputs=inputs)
            if status == "bet":
                if side == "OVER":
                    side_ask, side_bid, sz_ask, sz_bid, contract = ask_c, bid_c, pick.get("yes_ask_size_f"), pick.get("yes_bid_size_f"), "YES"
                else:                                             # UNDER = buy NO: NO ask = 100 - YES bid
                    side_ask, side_bid, sz_ask, sz_bid, contract = 100 - bid_c, 100 - ask_c, pick.get("yes_bid_size_f"), pick.get("yes_ask_size_f"), "NO"
                p, fee = entry_cost(side_ask)
                row.update(contract=contract, ask_c=side_ask, bid_c=side_bid, ask_size=sz_ask, bid_size=sz_bid,
                           price=float(p), fee=float(fee), count=1)
            out.append(row)
        return out

    # ---- settlements
    def run_settlements(self) -> None:
        decisions = [r for r in read_jsonl(self.dec_path) if r.get("status") == "bet"]
        done = {r["id"] for r in read_jsonl(self.set_path)}
        pending = [r for r in decisions if self._id(r) not in done]
        by_ticker: dict[str, list] = {}
        for r in pending:
            tip = parse_ts(r["tip_utc"])
            if self.now >= tip + SETTLE_AFTER[r["sport"]]:
                by_ticker.setdefault(r["ticker"], []).append(r)
        rows = []
        for ticker, rs in sorted(by_ticker.items()):
            try:
                resp = self.fetch_market(ticker)
            except Exception as exc:  # noqa: BLE001
                self.stats["errors"].append(f"settle {ticker}: {exc.__class__.__name__}")
                log.warning("settlement GET failed for %s: %s", ticker, exc.__class__.__name__)
                continue
            self._cache_raw(ticker, resp)
            res = parse_settlement(resp)
            if res is None:
                continue
            for r in rs:
                rows.append(settle_row(r, res, self.now))
        append_jsonl(self.set_path, rows)
        self.stats["settled"] = len(rows)
        for s in rows:
            log.info("SETTLED %s result=%s payout=%s net=%+.4f", s["id"], s["result"], s["payout"], s["net"])

    @staticmethod
    def _id(r: dict) -> str:
        return f"{r['sport']}|{r['game_id']}|{r['rule']}"

    def _cache_raw(self, ticker: str, resp: dict) -> None:
        d = self.root / "data" / "paper_league" / "settle_raw"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{ticker}.json").write_text(json.dumps({"fetch_utc": iso(self.now), "response": resp}), encoding="utf-8")

    # ---- summary
    def write_summary(self) -> dict:
        s = build_summary(read_jsonl(self.dec_path), read_jsonl(self.set_path), self.now)
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / "summary.json.tmp"
        tmp.write_text(json.dumps(s, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.dir / "summary.json")
        return s


def parse_settlement(resp: dict):
    """From GET /markets/{ticker}: {'result': 'yes'|'no'|'void', 'status': ...} when settled, else None."""
    m = (resp or {}).get("market") or {}
    res = str(m.get("result") or "").lower()
    status = str(m.get("status") or "").lower()
    if res in ("yes", "no") and status in ("settled", "finalized", "determined", "closed", ""):
        return {"result": res, "status": status, "settlement_value": m.get("settlement_value_dollars", m.get("expiration_value"))}
    if res in ("void", "voided", "refund") or status in ("voided",):
        return {"result": "void", "status": status, "settlement_value": None}
    return None


def settle_row(d: dict, res: dict, now: datetime) -> dict:
    p, fee = Decimal(str(d["price"])), Decimal(str(d["fee"]))
    if res["result"] == "void":
        payout, net = None, 0.0
    else:
        won = (res["result"] == "yes") == (d["contract"] == "YES")
        payout = 1 if won else 0
        net = net_of(payout, p, fee)
    return {"id": f"{d['sport']}|{d['game_id']}|{d['rule']}", "sport": d["sport"], "game_id": d["game_id"], "rule": d["rule"],
            "night": d["night"], "ticker": d["ticker"], "side": d["side"], "contract": d["contract"], "result": res["result"],
            "payout": payout, "price": d["price"], "fee": d["fee"], "net": round(net, 6), "settled_utc": iso(now),
            "market_status": res.get("status"), "settlement_value": res.get("settlement_value")}


def build_summary(decisions: list[dict], settlements: list[dict], now: datetime) -> dict:
    sett = {s["id"]: s for s in settlements}
    rules = {}
    for rid, (sport, label, control) in RULES.items():
        rules[rid] = {"sport": sport, "label": label, "control": control, "n": 0, "won": 0, "lost": 0, "open": 0, "void": 0,
                      "net": 0.0, "mean_net": None, "n_no_bet": 0, "n_no_decision": 0, "by_night": {}}
    for d in decisions:
        r = rules.get(d.get("rule"))
        if r is None:
            continue
        if d["status"] == "no_bet":
            r["n_no_bet"] += 1
            continue
        if d["status"] == "no_decision":
            r["n_no_decision"] += 1
            continue
        r["n"] += 1
        nt = r["by_night"].setdefault(d["night"], {"n": 0, "won": 0, "lost": 0, "open": 0, "net": 0.0})
        nt["n"] += 1
        s = sett.get(f"{d['sport']}|{d['game_id']}|{d['rule']}")
        if s is None:
            r["open"] += 1
            nt["open"] += 1
        elif s["result"] == "void":
            r["void"] += 1
        else:
            k = "won" if s["payout"] == 1 else "lost"
            r[k] += 1
            nt[k] += 1
            r["net"] += s["net"]
            nt["net"] += s["net"]
    for r in rules.values():
        settled = r["won"] + r["lost"]
        r["net"] = round(r["net"], 6)
        r["mean_net"] = round(r["net"] / settled, 6) if settled else None
        for nt in r["by_night"].values():
            nt["net"] = round(nt["net"], 6)
    by_sport = {}
    for sp in ("NBA", "NFL"):
        rr = [r for r in rules.values() if r["sport"] == sp]
        by_sport[sp] = {"n": sum(r["n"] for r in rr), "net": round(sum(r["net"] for r in rr), 6)}
    return {"updated_utc": iso(now), "rules": rules, "order": list(RULES), "by_sport": by_sport,
            "controls": [r for r, v in RULES.items() if v[2]], "bets_needed": 100,
            "n_decision_rows": len(decisions), "n_settlements": len(settlements),
            "note": "NO RULE HAS PASSED. Verdict only after 4 weeks and 100+ bets per rule, vs the controls."}


# ------------------------------------------------------------------ run / CLI
def acquire_lock(path: Path, now_ts: float, stale_s: int = 1200):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.exists() and now_ts - path.stat().st_mtime > stale_s:
            path.unlink()
        return os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except (FileExistsError, PermissionError, OSError):
        return None


def setup_logging(root: Path) -> None:
    d = Path(root) / "logs" / "paper_league"
    d.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(d / "paper_league.log", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.handlers[:] = [fh, sh]
    log.setLevel(logging.INFO)


def run(root: Path = ROOT, now: datetime | None = None, **kw) -> dict:
    lg = League(root, now, **kw)
    lock = lg.dir / "run.lock"
    fd = acquire_lock(lock, time.time())
    if fd is None:
        log.warning("another paper_league run holds the lock; exiting")
        return {"skipped": "locked"}
    try:
        try:
            lg.run_decisions()
        except Exception as exc:  # noqa: BLE001  -- one stage failing must not block settlement/summary
            lg.stats["errors"].append(f"decisions: {exc.__class__.__name__}: {exc}"[:300])
            log.exception("decision stage failed")
        try:
            lg.run_settlements()
        except Exception as exc:  # noqa: BLE001
            lg.stats["errors"].append(f"settlements: {exc.__class__.__name__}: {exc}"[:300])
            log.exception("settlement stage failed")
        summ = lg.write_summary()
        hb = {"ts_utc": iso(lg.now), "stats": lg.stats, "n_decision_rows": summ["n_decision_rows"],
              "n_settlements": summ["n_settlements"]}
        hp = Path(root) / "logs" / "paper_league.heartbeat"
        hp.write_text(json.dumps(hb), encoding="utf-8")
        log.info("run done: pending=%d decided_games=%d decision_rows=%d settled=%d deferred=%d errors=%s",
                 lg.stats["pending"], lg.stats["decided_games"], lg.stats["decisions"], lg.stats["settled"],
                 lg.stats["deferred"], lg.stats["errors"])
        return lg.stats
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--now", help="override the clock (ISO UTC); testing only")
    ap.add_argument("--games-csv")
    a = ap.parse_args(argv)
    setup_logging(Path(a.root))
    stats = run(Path(a.root), parse_ts(a.now) if a.now else None, games_csv=Path(a.games_csv) if a.games_csv else None)
    return 1 if stats.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
