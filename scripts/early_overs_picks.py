"""Nightly pick list for research log #40 (early-season NBA totals, FORMULA SIDE version). READ-ONLY.

Public unauthenticated GETs only (/markets, /markets/{ticker}/orderbook) plus the free hoopR-nba-data team_box_2027
parquet from GitHub. Never places orders, never uses keys. Prices are fetched live with plain requests (no cache).
Side = OVER if the F1_R2_S1 projection (research log #39 machinery) > the strike, else UNDER.

Usage:  .venv/Scripts/python.exe scripts/early_overs_picks.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import re

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kpm.core.fees import FeeModel  # noqa: E402

sys.path.insert(0, str(ROOT / "reports" / "output" / "nba_totals_tourney"))
import tourney as tn  # noqa: E402  (#39 machinery; also brings in #38 ratings2 and #37 lib)
import numpy as np  # noqa: E402

BASE = "https://api.elections.kalshi.com/trade-api/v2"
ET = ZoneInfo("America/New_York")
FEE = FeeModel(0.07, 0.25, "micro_ceil")
WINDOW_START, WINDOW_END = date(2026, 10, 21), date(2026, 11, 3)
LOG_PATH = ROOT / "logs" / "early_overs_picks.jsonl"
BAND_LO, BAND_HI = 15, 85  # cents, inclusive, on mid
MAX_SPREAD = 10            # cents, inclusive (ask - bid <= 0.10); integer cents so no float tolerance is needed
C_B2B = -1.211             # #39 rest constant for F1_R2 (2024-25 fit), points per team on 0 days' rest
BOX = ROOT / "data" / "raw" / "nba_open"
BOX27_URL = "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/nba/team_box/parquet/team_box_2027.parquet"
GAME_KEY_RE = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})$")
# #40 version 4 (amendment 2026-10-21 ~20:35Z, user-approved): #42's frozen search winner; bet only if |proj - strike| >= 8.
RULE = "grid_winner"           # or "formula_side" (version 3, F1_R2_S1)
GRID_T = 4.0                   # points; #40 version 5 (user, 2026-10-21 ~16:50 ET). Version 4 used 8.0.
GRID_SIGMA = 19.234797122435534  # winner_frozen.json discovery sigma (formula's own odds, information only)


def _grid_tonight():
    """reports/output/nba_totals_grid/tonight.py (frozen winner on a live night), loaded lazily."""
    sys.path.insert(0, str(ROOT / "reports" / "output" / "nba_totals_grid"))
    import tonight  # noqa: E402
    return tonight


def phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = "kpm-research/0.1 (read-only picks)"


# ---------------------------------------------------------------- pure helpers
def cents(x) -> int:
    return int(round(float(x) * 100))


def best_bid_ask_cents(orderbook: dict):
    """Return (yes_bid_c, yes_ask_c) from an orderbook payload; None where a side is empty.

    yes bid = highest yes bid; yes ask = 100 - highest no bid.
    """
    ob = orderbook.get("orderbook_fp") or orderbook.get("orderbook") or {}
    yes = ob.get("yes_dollars") or []
    no = ob.get("no_dollars") or []
    bid = max((cents(p) for p, _ in yes), default=None)
    nb = max((cents(p) for p, _ in no), default=None)
    ask = None if nb is None else 100 - nb
    return bid, ask


def best_no_cents(bid, ask):
    """From yes bid/ask (cents) return (no_bid, no_ask): no bid = 100 - yes ask, no ask = 100 - yes bid."""
    return 100 - ask, 100 - bid


def valid_quote(bid, ask) -> bool:
    return bid is not None and ask is not None and 0 < bid <= ask < 100


def select_strike(quotes: list[dict]):
    """quotes: dicts with strike, bid, ask (cents). Mid closest to 50 within [15,85]; tie -> lower strike."""
    best = None
    for q in quotes:
        if not valid_quote(q["bid"], q["ask"]):
            continue
        mid2 = q["bid"] + q["ask"]  # 2*mid in cents, exact integer arithmetic
        if not (2 * BAND_LO <= mid2 <= 2 * BAND_HI):
            continue
        if q["ask"] - q["bid"] > MAX_SPREAD:  # amendment: spread <= 10c (integer cents, exact)
            continue
        key = (abs(mid2 - 100), q["strike"])
        if best is None or key < best[0]:
            best = (key, q)
    return None if best is None else best[1]


def choose_side(proj: float, strike: float) -> str:
    """OVER if the projected total is strictly above the strike, else UNDER."""
    return "OVER" if proj > strike else "UNDER"


def limit_for_side(side: str, yes_bid_c: int, yes_ask_c: int):
    """(contract 'YES'|'NO', maker limit price in cents, taker price in cents) for the chosen side.
    OVER: buy YES, limit at the YES bid, taker at the YES ask. UNDER: buy NO, limit at the NO bid (= 100 - YES ask),
    taker at the NO ask (= 100 - YES bid)."""
    if side == "OVER":
        return "YES", yes_bid_c, yes_ask_c
    no_bid, no_ask = best_no_cents(yes_bid_c, yes_ask_c)
    return "NO", no_bid, no_ask


def parse_game_key(key: str):
    """'26OCT23HOUSAS' -> (ET date, away, home) using the same ticker parser as #37."""
    pe = tn.lib.parse_event("KXNBATOTAL-" + key)
    return None if pe is None else (date.fromisoformat(pe[0]), pe[1], pe[2])


def build_schedule(game_keys) -> dict:
    """{team: sorted list of game dates} from KXNBAGAME-style game keys."""
    sched: dict[str, set] = {}
    for k in game_keys:
        p = parse_game_key(k)
        if p is None:
            continue
        for t in (p[1], p[2]):
            sched.setdefault(t, set()).add(p[0])
    return {t: sorted(v) for t, v in sched.items()}


def b2b_from_schedule(sched: dict, team: str, day: date) -> bool:
    """True if the team's latest game BEFORE `day` was on day - 1 (0 days' rest). First game of season: False."""
    prev = [d for d in sched.get(team, []) if d < day]
    return bool(prev) and prev[-1] == day - timedelta(days=1)


def project_f1r2s1(eng, away: str, home: str, day: date, b2b_count: int) -> float:
    """F1 total from R2 ratings (k=20, shared pace) + C_B2B per team on a back-to-back. Same code path as #39."""
    D = np.datetime64(day, "D")
    snap = eng.snapshot(D, "R2")
    Lo, Lp, _, _ = eng.league_level(D)
    return float(tn.formula_total("F1", snap[away], snap[home], Lo, Lp) + C_B2B * b2b_count)


def fetch_box27(path: Path = BOX / "team_box_2027.parquet"):
    """Refresh the free hoopR 2026-27 team box (public GET, tiny file). Falls back to the cached copy."""
    tmp = None
    try:
        r = _SESSION.get(BOX27_URL, timeout=60)
        if r.status_code == 200 and len(r.content) > 1000:
            tmp = path.with_name(f"{path.stem}.{os.getpid()}.{time.time_ns()}.tmp")  # unique per process and call
            tmp.write_bytes(r.content)
            tmp.replace(path)
            tmp = None
    except (requests.RequestException, OSError):  # includes PermissionError (file open elsewhere on Windows)
        pass
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass
    return path if path.exists() else None


def load_engine(box_dir: Path = BOX, box27: Path | None = None):
    """Engine with the 2025-26 regular season as prior and 2026-27 regular-season games as current (maybe none)."""
    prev = tn.team_games_x(pd.read_parquet(box_dir / "team_box_2026.parquet"), (2,))
    cur = None
    if box27 is not None:
        raw = pd.read_parquet(box27)
        if len(raw):
            cur = tn.team_games_x(raw, (2,))
    if cur is None or cur.empty:
        cur = prev.iloc[0:0].copy()
    return tn.Engine(prev, cur), (0 if cur.empty else cur.game_id.nunique())


def contracts_for(ask_c: int) -> int:
    return max(1, 100 // ask_c)


def cost_with_fee(price_c: int, n: int, maker: bool = False):
    fee = FEE.order_fee(price_c / 100, n, maker=maker)
    return n * price_c / 100 + fee, fee


def status_for(now: datetime, tip: datetime, bid_c: int | None = None, side: str = "NO") -> str:
    start, end = tip - timedelta(minutes=75), tip - timedelta(minutes=45)
    cancel = tip - timedelta(minutes=15)
    if now < start:
        m = math.ceil((start - now).total_seconds() / 60)
        return f"too early (in {m}m)"
    if now <= end:
        px = "" if bid_c is None else f" @ {bid_c / 100:.2f}"
        return f"PLACE NOW (limit buy {side}{px})"
    if now <= cancel:
        return f"working - cancel if unfilled by {cancel.astimezone(ET):%H:%M}"
    return "past window"


def in_test_window(tip: datetime) -> bool:
    return WINDOW_START <= tip.astimezone(ET).date() <= WINDOW_END


def parse_z(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------------------------------------------------------------- network
def get(path: str, params: dict | None = None, tries: int = 8) -> dict:
    """Public GET with backoff on 429/5xx. Only /markets paths are ever requested."""
    assert path.startswith("/markets")
    delay = 1.0
    for i in range(tries):
        try:
            r = _SESSION.get(BASE + path, params=params, timeout=30,
                             headers={"Cache-Control": "no-cache"})
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            if i == tries - 1:
                r.raise_for_status()
            ra = r.headers.get("Retry-After")
            time.sleep(float(ra) if ra and ra.replace(".", "").isdigit() else delay)
            delay = min(delay * 2, 30)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("unreachable")


def all_open_markets(series: str) -> list[dict]:
    out, cursor = [], None
    while True:
        p = {"series_ticker": series, "status": "open", "limit": 1000}
        if cursor:
            p["cursor"] = cursor
        d = get("/markets", p)
        out += d.get("markets", [])
        cursor = d.get("cursor")
        if not cursor:
            return out


def game_key(event_ticker: str) -> str:
    return event_ticker.split("-", 1)[1]  # e.g. 26OCT23HOUSAS


def games_before(eng, team: str, D) -> int:
    """Number of the team's 2026-27 regular-season games with ET date < D (R2's n, before weighting)."""
    if team not in eng.team:
        return 0
    return int(np.searchsorted(eng.team[team]["dates"], np.datetime64(D, "D"), side="left"))


def recent_game_keys(now: datetime) -> set:
    """Game keys (e.g. 26OCT20HOUOKC) of KXNBAGAME events that closed in the last 21 days, plus open ones.
    Public GET /markets only. Used for each team's previous game date (back-to-back detection)."""
    keys = set()
    for params in ({"min_close_ts": int(now.timestamp()) - 21 * 86400}, {"status": "open"}):
        cursor = None
        for _ in range(30):
            p = {"series_ticker": "KXNBAGAME", "limit": 1000, **params}
            if cursor:
                p["cursor"] = cursor
            try:
                d = get("/markets", p)
            except Exception:  # noqa: BLE001
                break
            for m in d.get("markets", []):
                keys.add(game_key(m["event_ticker"]))
            cursor = d.get("cursor")
            if not cursor:
                break
    return keys


def compute_rows(day: date, now: datetime, on_header=None):
    """Fetch live quotes and build the pick rows for ET date `day`. Returns (rows, n27_games, games, eng).
    `on_header(n27_games, games)` is called once ratings are loaded (used by the CLI to print its header)."""
    totals = all_open_markets("KXNBATOTAL")
    games_occ = {}
    for m in all_open_markets("KXNBAGAME"):
        games_occ.setdefault(game_key(m["event_ticker"]), m["occurrence_datetime"])

    by_game: dict[str, list[dict]] = {}
    for m in totals:
        by_game.setdefault(game_key(m["event_ticker"]), []).append(m)

    games = []
    for key, ms in by_game.items():
        occ = games_occ.get(key) or ms[0].get("occurrence_datetime")
        if not occ:
            continue
        tip = parse_z(occ) - timedelta(hours=3)
        if tip.astimezone(ET).date() == day:
            games.append((tip, key, ms))
    games.sort(key=lambda g: (g[0], g[1]))

    box27 = fetch_box27()
    eng, n27_games = load_engine(BOX, box27)
    sched = build_schedule(recent_game_keys(now) | set(games_occ))
    if on_header:
        on_header(n27_games, games)
    if not games:
        return [], n27_games, games, eng
    grid_proj, grid_err = {}, None
    if RULE == "grid_winner":
        try:
            glist = [(k, parse_game_key(k)[1], parse_game_key(k)[2], t) for t, k, _ in games if parse_game_key(k)]
            grid_proj = _grid_tonight().project_winner_for_date(day, glist, box27=box27)
        except Exception as e:  # noqa: BLE001  -- fail safe: no bets rather than a silent rule change
            grid_err = f"{type(e).__name__}: {e}"
    rows = []
    for tip, key, ms in games:
        quotes, skipped = [], 0
        for m in sorted(ms, key=lambda m: m["floor_strike"]):
            try:
                bid, ask = best_bid_ask_cents(get(f"/markets/{m['ticker']}/orderbook"))
            except Exception:  # noqa: BLE001
                bid = ask = None
            time.sleep(0.12)
            if not valid_quote(bid, ask):
                skipped += 1
                continue
            quotes.append({"strike": m["floor_strike"], "ticker": m["ticker"], "bid": bid, "ask": ask})
        base = {"ts": now.isoformat(), "game": key, "tip_et": f"{tip.astimezone(ET):%Y-%m-%d %H:%M}",
                "used_box27": bool(n27_games)}
        pick = select_strike(quotes)
        if pick is None:
            rows.append({**base, "side": None, "ticker": None, "strike": None, "contracts": None,
                         "status": f"SKIP: no strike with valid quote and mid in 15-85c "
                                   f"({len(quotes)} valid, {skipped} missing)"})
            continue
        p = parse_game_key(key)
        away, home = p[1], p[2]
        if away not in eng.prev or home not in eng.prev:
            rows.append({**base, "side": None, "ticker": pick["ticker"], "strike": pick["strike"], "contracts": None,
                         "status": f"SKIP: no ratings for {away}/{home}"})
            continue
        ba, bh = b2b_from_schedule(sched, away, day), b2b_from_schedule(sched, home, day)
        p_formula = ev_c = None
        if RULE == "grid_winner":
            proj = grid_proj.get(key)
            if proj is None:
                rows.append({**base, "rule": RULE, "bet": False, "side": None, "ticker": pick["ticker"],
                             "strike": pick["strike"], "contracts": None,
                             "status": f"SKIP: no search-winner projection ({grid_err or 'missing'})"})
                continue
            diff = proj - pick["strike"]
            if abs(diff) < GRID_T:
                rows.append({**base, "rule": RULE, "bet": False, "side": None, "buy": None, "ticker": pick["ticker"],
                             "strike": pick["strike"], "contracts": None, "proj": round(proj, 3),
                             "proj_minus_strike": round(diff, 3),
                             "status": f"NO BET (gap {abs(diff):.1f} < {GRID_T:.0f})"})
                continue
        else:
            proj = project_f1r2s1(eng, away, home, day, int(ba) + int(bh))
        side = choose_side(proj, pick["strike"])
        contract, limit_c, taker_c = limit_for_side(side, pick["bid"], pick["ask"])
        n = 1
        cost, fee = cost_with_fee(limit_c, n, maker=True)
        tcost, tfee = cost_with_fee(taker_c, n)
        if RULE == "grid_winner":
            p_formula = phi(abs(proj - pick["strike"]) / GRID_SIGMA)
            ev_c = round((p_formula - limit_c / 100 - fee / n) * 100, 2)
        st = status_for(now, tip, limit_c, contract)
        if not in_test_window(tip):
            st += " [outside test window]"
        dec = tip - timedelta(hours=1)
        rows.append({**base, "decision_et": f"{dec.astimezone(ET):%H:%M}",
                     "min_to_decision": round((dec - now).total_seconds() / 60),
                     "side": side, "buy": contract, "ticker": pick["ticker"], "strike": pick["strike"],
                     "proj": round(proj, 3), "proj_minus_strike": round(proj - pick["strike"], 3),
                     "n_away": games_before(eng, away, day), "n_home": games_before(eng, home, day),
                     "n_lg": int(eng.R1.league(np.datetime64(day, "D"))[2]),
                     "b2b_away": bool(ba), "b2b_home": bool(bh),
                     "yes_bid": pick["bid"] / 100, "yes_ask": pick["ask"] / 100,
                     "limit": limit_c / 100, "taker_price": taker_c / 100, "contracts": n,
                     "cost": round(cost, 6), "maker_fee": fee, "taker_cost": round(tcost, 6),
                     "taker_fee": tfee, "saving_per_contract": round((tcost - cost) / n, 6),
                     "cancel_by_et": f"{(tip - timedelta(minutes=15)).astimezone(ET):%H:%M}",
                     "rule": RULE, "bet": True, "p_formula": p_formula, "ev_c": ev_c, "grid_t": GRID_T,
                     "status": st})

    return rows, n27_games, games, eng


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="ET date YYYY-MM-DD (default: today ET)")
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    day = date.fromisoformat(a.date) if a.date else now.astimezone(ET).date()

    def header(n27_games, games):
        rule = (f"search winner, bet only if |proj - line| >= {GRID_T:g}" if RULE == "grid_winner" else "formula side")
        print(f"Early-season NBA totals pick list (research log #40, {rule}) -- ET date {day}, "
              f"now {now.astimezone(ET):%Y-%m-%d %H:%M} ET")
        if n27_games == 0:
            print("no 2026-27 box scores; prior only")
        else:
            print(f"2026-27 box scores used: {n27_games} regular-season games in team_box_2027 "
                  f"(only games dated before each game's ET date)")
        print("Only games tipping ET 2026-10-21..2026-11-03 count for #40; others are marked 'outside test window'.")

    rows, n27_games, games, _eng = compute_rows(day, now, header)
    if not games:
        print("No open KXNBATOTAL games found for this ET date.")
        print("Research pick list, not financial advice. You place any bet yourself.")
        return 0
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    print("Plan: 1 contract, limit buy at the chosen side's bid (maker). Cancel unfilled 15 min before tip. Don't chase.")
    print(f"{'game':<14}{'tip':>6}{'dec':>6}{'canc':>6}{'min':>5}  {'proj':>7}{'strike':>7}{'diff':>6} {'side':<6}"
          f"{'limit':>6}{'mkr$':>8}{'tkrpx':>6}{'tkr$':>8}  b2b n(a/h/lg)  status / ticker")
    for r in rows:
        if r.get("proj") is None:
            print(f"{r['game']:<14}{r['tip_et'][-5:]:>6}  {r['status']}")
            continue
        if r.get("side") is None:   # grid_winner: projection shown, no bet
            print(f"{r['game']:<14}{r['tip_et'][-5:]:>6}{'':>17}  {r['proj']:>7.1f}{r['strike']:>7}"
                  f"{r['proj_minus_strike']:>+6.1f}  {r['status']} / {r['ticker']}")
            continue
        print(f"{r['game']:<14}{r['tip_et'][-5:]:>6}{r['decision_et']:>6}{r['cancel_by_et']:>6}"
              f"{r['min_to_decision']:>5}  {r['proj']:>7.1f}{r['strike']:>7}{r['proj_minus_strike']:>+6.1f} "
              f"{r['side']:<6}{r['limit']:>6.2f}{r['cost']:>8.4f}{r['taker_price']:>6.2f}{r['taker_cost']:>8.4f}  "
              f"{int(r['b2b_away'])}{int(r['b2b_home'])}  {r['n_away']}/{r['n_home']}/{r['n_lg']}  "
              + (f"p={r['p_formula']:.0%} EV={r['ev_c']:+.1f}c  " if r.get("p_formula") is not None else "")
              + f"{r['status']} / {r['ticker']}")
    with LOG_PATH.open("a", encoding="ascii") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print("Research pick list, not financial advice. You place any bet yourself.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
