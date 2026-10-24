"""Small, heavily capped live order bot for the nightly NBA totals bets (research log #40, formula-side version).
INSTANT-FILL version: it places the user's nightly picks as IOC (immediate-or-cancel) taker orders.

  DRY RUN (default, sends nothing):   .venv/Scripts/python.exe reports/output/early_overs_bot/overs_bot.py
  LIVE (the USER starts this):        $env:KALSHI_ENV="prod"; .venv/Scripts/python.exe reports/output/early_overs_bot/overs_bot.py --live
  STOP:   create the file reports/output/early_overs_bot/STOP  (bot cancels anything open and exits), or Ctrl+C.

Pick source of truth = the Discord ping recorded in logs/early_overs_notified.json (key "<ET day>|<game>" ->
{side, strike, limit_c}). The bot buys exactly that side and strike (ticker KXNBATOTAL-<game>-<ceil(strike)>, checked
against the market's floor_strike) with max price = limit_c (the ask at ping time). If no ping exists by tip-50 min it
computes the pick itself with scripts/early_overs_picks.py compute_rows (never when the row says bet == False) and uses
that row's taker price as the max price. The source used is logged.

Per game: 1 contract, IOC at the side's CURRENT ask, only if that ask <= max price (and 15..85c). No fill -> never
chase: re-check every loop until tip-15 min, send another IOC only when the ask is back <= max price. At most 4 IOC
attempts per game, 40 per night; the game is DONE once 1 contract is filled. Hard caps are in code (see CAPS).
Tip times come from the hoopR schedule (compute_rows). If a schedule reload stops confirming an armed game's tip (not in
the schedule, TBD/postponed, schedule file > 36 h old, or > 15 min from Kalshi), its open orders are cancelled and the
game is closed; filled contracts are left alone.

Authenticated calls (live only), copied from reports/output/picks/approve.py:
  ExecClient(env, live=True).market(ticker)             GET  /markets/{t}                     (status, floor_strike, ei)
  ExecClient(env, live=True).orderbook(ticker)          GET  /markets/{t}/orderbook           (current ask)
  ExecClient(env, live=True).positions(event_ticker=..) GET  /portfolio/positions             (double-exposure check)
  ExecClient(env, live=True).orders(event_ticker=.., status="resting")   GET /portfolio/orders (double-exposure check)
  ExecClient(env, live=True).create_order(body)         POST /portfolio/events/orders         (IOC buy)
  ExecClient(env, live=True).order(order_id)            GET  /portfolio/orders/{id}
  ExecClient(env, live=True).orders(ticker=...)         GET  /portfolio/orders   (fallback status lookup / find by coid)
  ExecClient(env, live=True).cancel_order(oid, ei)      DELETE /portfolio/events/orders/{id}?exchange_index=N
  ExecClient(env, live=True).server_time_skew()         GET  /exchange/status (public, 3 probes)
Never used: cancel_all (it would kill manual orders), amend, decrease, batch calls.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from kpm.execution import units  # noqa: E402  (pure functions only; no network)

ET = ZoneInfo("America/New_York")
LOG_PATH = ROOT / "logs" / "early_overs_bot.jsonl"
LOCK_PATH = ROOT / "logs" / "early_overs_bot.lock"
PING_PATH = ROOT / "logs" / "early_overs_notified.json"       # written by scripts/early_overs_notify.py (read only here)
HEARTBEAT_PATH = ROOT / "logs" / "early_overs_bot.heartbeat"
STOP_PATH = HERE / "STOP"

# ------------------------------------------------------------------ HARD CAPS (code constants, not CLI options)
SERIES = "KXNBATOTAL"
COUNT = 1
MIN_PRICE_C, MAX_PRICE_C = 15, 85           # limit price of the side bought, cents, inclusive
MAX_FILLED_PER_GAME = 1                     # contracts
MAX_ATTEMPTS_PER_GAME = 4                   # IOC orders sent per game
MAX_ATTEMPTS_PER_NIGHT = 40
NIGHT_CAP_USD = Decimal("7.00")
HARD_END = datetime(2026, 11, 3, 23, 59, tzinfo=ET).astimezone(timezone.utc)   # compared as an absolute instant
OPEN_MIN, CLOSE_MIN, CANCEL_MIN = 75, 45, 15  # minutes before the pick-list tip time
FALLBACK_MIN = 50                           # no ping by tip-50: compute the pick ourselves
DAY_START_ET = (11, 0)                      # each day's loop starts here (Sunday afternoon tips)
EMPTY_DAY_GIVE_UP_H = 6                     # no games listed this long after DAY_START_ET: the day is over
SCHEDULE_RELOAD = timedelta(minutes=30)
HEARTBEAT_EVERY = timedelta(seconds=60)
LOOP_S = 60
MAX_CANCEL_TRIES = 10
NOT_FOUND_AFTER = timedelta(minutes=10)     # after cancel_at + this, 3 clean listings without the id -> not_found
NOT_FOUND_MISSES = 3
MAX_SKEW_S = 2.0
POS_TOL = Decimal("0.001")                  # position vs own fills tolerance, contracts
MAX_READ_FAILS = 3                          # consecutive cash/exposure read failures -> placing False
CASH_BUFFER = Decimal("0.10")                # cash must cover limit + fee + this

OPEN_STATES = ("open", "unknown")          # unresolved: may still be live on the exchange
SIDE_MAP = {"OVER": "YES", "UNDER": "NO"}   # OVER buys YES, UNDER buys NO


class _Abort(Exception):
    """Raised from compute_rows' on_header callback to get the game list without fetching any orderbooks."""


def day_start(day: date) -> datetime:
    """The day's loop start as an absolute (UTC) instant; correct across the 2026-11-01 DST change."""
    return datetime(day.year, day.month, day.day, *DAY_START_ET, tzinfo=ET).astimezone(timezone.utc)


# ------------------------------------------------------------------ single-instance lock
def acquire_lock(path):
    """Hold an exclusive 1-byte lock on `path` for the life of the process (msvcrt on Windows, flock elsewhere).
    Raises SystemExit if another process holds it. Returns the open file; pass it to release_lock."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+b")
    try:
        try:
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except ImportError:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise SystemExit(f"another overs_bot is already running (lock {path.name} is held); refusing to start.")
    return f


def release_lock(f) -> None:
    try:
        try:
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except ImportError:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    f.close()


# ------------------------------------------------------------------ pure helpers
def est_fee(price_c: int, count=COUNT) -> Decimal:
    """Conservative TAKER fee estimate: 0.07*C*P*(1-P) rounded UP to $0.0001, plus one $0.0001 step for the
    rounding fee (units.worst_fee). All our orders are takers now, so this is the real fee class."""
    return units.worst_fee(Decimal(count), Decimal(price_c) / 100)


def order_cost(o: dict) -> Decimal:
    """Money tied up / spent for one tonight's attempt, for the nightly cap. In-flight (open/unknown/not_found)
    orders count in full at their limit; a filled one at its limit (>= the real price); a cancelled/IOC-unfilled one
    only for its filled part; a failed one nothing."""
    p = Decimal(o["price_c"]) / 100
    if o["state"] == "failed":
        return Decimal(0)
    if o["state"] == "canceled":
        f = Decimal(str(o.get("filled", 0)))
        return Decimal(0) if f == 0 else f * p + est_fee(o["price_c"], f)
    return Decimal(str(o["count"])) * p + est_fee(o["price_c"], o["count"])   # open, unknown, filled, not_found


def game_filled(orders: list[dict]) -> Decimal:
    """Contracts filled in these attempts. A record in state 'filled' counts as at least its full count, even if the
    exchange view had no usable fill field."""
    tot = Decimal(0)
    for o in orders:
        f = Decimal(str(o.get("filled", 0)))
        if o.get("state") == "filled":
            f = max(f, Decimal(str(o.get("count", COUNT))))
        tot += f
    return tot


def check_caps(orders_tonight: list[dict], game: str, ticker: str, action: str, count: int, price_c: int,
               now: datetime) -> list[str]:
    """Return the list of violated caps (empty = OK). Pure. `orders_tonight` = every attempt recorded tonight."""
    bad = []
    if not str(ticker).startswith(SERIES + "-"):
        bad.append(f"series: {ticker} is not {SERIES}")
    if action != "buy":
        bad.append(f"action {action!r} is not buy")
    if isinstance(count, bool) or not isinstance(count, (int, Decimal)) or not (0 < count <= COUNT):
        bad.append(f"count {count!r} not in (0, {COUNT}]")
    if not (isinstance(price_c, int) and not isinstance(price_c, bool) and MIN_PRICE_C <= price_c <= MAX_PRICE_C):
        bad.append(f"price {price_c!r}c outside {MIN_PRICE_C}-{MAX_PRICE_C}c")
    mine = [o for o in orders_tonight if o["game"] == game]
    filled = game_filled(mine)
    if isinstance(count, (int, Decimal)) and not isinstance(count, bool) and filled + Decimal(count) > MAX_FILLED_PER_GAME:
        bad.append(f"game {game} already has {filled} filled (max {MAX_FILLED_PER_GAME})")
    if len(mine) >= MAX_ATTEMPTS_PER_GAME:
        bad.append(f"already {len(mine)} attempts for game {game} (max {MAX_ATTEMPTS_PER_GAME})")
    if len(orders_tonight) >= MAX_ATTEMPTS_PER_NIGHT:
        bad.append(f"already {len(orders_tonight)} attempts tonight (max {MAX_ATTEMPTS_PER_NIGHT})")
    if isinstance(price_c, int) and 0 < price_c < 100:
        spent = sum((order_cost(o) for o in orders_tonight), Decimal(0))
        new = Decimal(count) * Decimal(price_c) / 100 + est_fee(price_c, count)
        if spent + new > NIGHT_CAP_USD:
            bad.append(f"nightly cap: {spent:.4f} + {new:.4f} > {NIGHT_CAP_USD}")
    if now > HARD_END:
        bad.append(f"after the end date {HARD_END.astimezone(ET):%Y-%m-%d %H:%M} ET")
    return bad


def build_body(ticker: str, outcome: str, price_c: int, exchange_index: int, coid: str, count=COUNT) -> dict:
    """CreateOrderV2 IOC body, same shape as reports/output/picks/approve.py (time_in_force immediate_or_cancel,
    self_trade_prevention taker_at_cross, no post_only, no expiration_time, no cancel_order_on_pause: exactly the
    live-proven lockbot/approve.py body). `count` defaults to 1; a top-up after a partial fill sends the remainder."""
    book_side, yes_price = units.to_book(outcome, Decimal(price_c) / 100)
    return {"ticker": ticker, "client_order_id": coid, "side": book_side,
            "count": units.fmt_count(Decimal(count)), "price": units.fmt_price(yes_price),
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross", "exchange_index": exchange_index}


def require_prod_env(getter=None) -> None:
    """--live needs KALSHI_ENV exactly 'prod' (the project's loader enforces it again via expected='prod')."""
    if getter is None:
        from kpm.core import config
        getter = config.secret
    if getter("KALSHI_ENV") != "prod":
        raise SystemExit("--live refused: KALSHI_ENV must be exactly 'prod'.")


def _dec(x):
    try:
        return Decimal(str(x))
    except Exception:  # noqa: BLE001
        return Decimal(0)


def parse_order_obj(o: dict) -> dict:
    """GET /portfolio/orders/{id} Order object -> state/filled/remaining/fees/avg cost (fields per docs)."""
    st = o.get("status")
    filled = _dec(o.get("fill_count_fp", o.get("fill_count", 0)))
    remaining = _dec(o.get("remaining_count_fp", o.get("remaining_count", 0)))
    fees = _dec(o.get("taker_fees_dollars", 0)) + _dec(o.get("maker_fees_dollars", 0))
    cost = _dec(o.get("taker_fill_cost_dollars", 0)) + _dec(o.get("maker_fill_cost_dollars", 0))
    avg = None if filled == 0 else cost / filled
    if st == "resting":
        state = "open"
    elif st == "executed":
        state = "filled"
    elif st == "canceled":
        state = "filled" if (filled >= COUNT and remaining == 0) else "canceled"
    else:
        state = "unknown"
    return {"state": state, "filled": filled, "remaining": remaining, "fees": fees, "avg_cost": avg,
            "api_status": st}


def parse_ping(info, game: str):
    """One early_overs_notified.json entry -> pick dict, or (None, reason) if it is not usable. Pure."""
    try:
        side = info["side"]
        if side not in SIDE_MAP:
            return None, f"ping side {side!r} not OVER/UNDER"
        strike = Decimal(str(info["strike"]))
        lim = info["limit_c"]
        if isinstance(lim, bool) or not isinstance(lim, int):
            return None, f"ping limit_c {lim!r} is not an int"
        if not (MIN_PRICE_C <= lim <= MAX_PRICE_C):
            return None, f"ping limit_c {lim}c outside {MIN_PRICE_C}-{MAX_PRICE_C}c"
        if not strike.is_finite() or strike <= 0:
            return None, f"ping strike {strike!r} invalid"
    except (KeyError, TypeError, ArithmeticError, ValueError) as e:
        return None, f"ping malformed: {type(e).__name__}"
    ticker = f"{SERIES}-{game}-{int(strike.to_integral_value(rounding=ROUND_CEILING))}"
    return {"source": "ping", "side": side, "outcome": SIDE_MAP[side].lower(), "ticker": ticker,
            "strike": strike, "max_c": lim}, None


def pick_from_row(r, game: str):
    """compute_rows row -> pick dict, or (None, reason). Never bets a row with bet == False. Pure."""
    if r is None:
        return None, "no row"
    if r.get("bet") is False:
        return None, f"row says NO BET ({r.get('status')})"
    if r.get("side") not in SIDE_MAP or r.get("proj") is None or r.get("strike") is None:
        return None, f"no valid pick/quote: {r.get('status', 'no row')}"
    if r.get("buy") != SIDE_MAP[r["side"]]:
        return None, f"side/buy mismatch side={r.get('side')} buy={r.get('buy')}"
    tk = r.get("ticker")
    if not isinstance(tk, str) or not tk.startswith(f"{SERIES}-{game}-"):
        return None, f"row ticker {tk!r} is not a {SERIES} market of {game}"
    tp = r.get("taker_price")
    if tp is None:
        return None, "row has no taker_price"
    return {"source": "fallback", "side": r["side"], "outcome": r["buy"].lower(), "ticker": tk,
            "strike": Decimal(str(r["strike"])), "max_c": int(round(float(tp) * 100))}, None


def new_order(day, game, n, ticker, outcome, price_c, max_c, coid, ei, tip, cancel_at, state="unknown",
              count=COUNT) -> dict:
    return {"day": day, "game": game, "n": n, "ticker": ticker, "outcome": outcome, "price_c": price_c,
            "max_c": max_c, "count": count, "coid": coid, "order_id": None, "state": state, "filled": 0,
            "exchange_index": ei, "tip": tip, "cancel_at": cancel_at, "cancel_tries": 0, "miss": 0}


def new_game(day, game, pick, tip, ms=None) -> dict:
    ei = next((m.get("exchange_index") for m in (ms or []) if m.get("ticker") == pick["ticker"]), None)
    return {"day": day, "game": game, "tip": tip, "cancel_at": tip - timedelta(minutes=CANCEL_MIN), "ms": ms or [],
            "closed": None, "last_note": None, "dry_ei": ei, **pick}


def is_auth_error(e: BaseException) -> bool:
    return type(e).__name__ == "AuthError" or getattr(e, "status", None) in (401, 403)


def _num(x) -> Decimal | None:
    try:
        return Decimal(str(x))
    except Exception:  # noqa: BLE001
        return None


class PublicReader:
    """Dry-run only: public, unauthenticated, read-only market data via scripts/early_overs_picks.py."""

    def market(self, ticker):
        import early_overs_picks as eop
        return eop.get(f"/markets/{ticker}").get("market") or {}

    def side_ask(self, ticker, outcome):
        """(ask in cents, size at that ask) of the side being bought; either may be None."""
        import early_overs_picks as eop
        ob_ = eop.get(f"/markets/{ticker}/orderbook")
        b = ob_.get("orderbook_fp") or ob_.get("orderbook") or {}
        opp = b.get("no_dollars" if outcome == "yes" else "yes_dollars") or []
        if not opp:
            return None, None
        best = max(opp, key=lambda x: Decimal(str(x[0])))
        return 100 - eop.cents(best[0]), Decimal(str(best[1]))


# ------------------------------------------------------------------ the bot
class Bot:
    def __init__(self, *, client=None, live=False, compute=None, log_path=LOG_PATH, stop_path=STOP_PATH,
                 lock_path=LOCK_PATH, ping_path=PING_PATH, heartbeat_path=HEARTBEAT_PATH, until=date(2026, 11, 3),
                 force_first=False, echo=print, sleep=time.sleep, clock=None, public=None):
        if live and client is None:
            raise ValueError("live mode needs a client")
        if live and force_first:
            raise ValueError("--force-first is dry-run only")
        self.client, self.live = client, live
        self._compute = compute
        self.public = public                                # dry-run reader (market / yes_quote); lazy default
        self.log_path, self.stop_path, self.until = Path(log_path), Path(stop_path), until
        self.lock_path, self.ping_path, self.heartbeat_path = Path(lock_path), Path(ping_path), Path(heartbeat_path)
        self.force_first = force_first
        self.echo, self._sleep = echo, sleep
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.orders: dict[tuple[str, str, int], dict] = {}   # (ET day iso, game, attempt n) -> order record
        self.games: dict[tuple[str, str], dict] = {}         # (ET day iso, game) -> armed game record
        self.sched: dict[str, dict] = {}                     # day iso -> {"games": [(tip, key, ms)], "loaded_at": dt}
        self.forced_done = False
        self.stopped = False
        self.halt_reason: str | None = None
        self.clock_ok = True                                # False after a failed day-start clock check
        self._lock = None
        self._hb_at: datetime | None = None
        self._last_cash = None                              # last successful shard-cash read
        self._read_fails = 0                                # consecutive cash/exposure read failures
        self._waiting: set = set()

    # -------------------------------------------------- logging
    def log(self, event: str, line: str | None = None, **kw):
        rec = {"ts": self.clock().astimezone(timezone.utc).isoformat(timespec="seconds"), "event": event,
               "dry": not self.live, **kw}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str, ensure_ascii=True) + "\n")
        txt = line if line is not None else " ".join(f"{k}={v}" for k, v in kw.items())
        self.echo(f"{rec['ts'][11:19]}Z {event.upper():<14} {txt}".encode("ascii", "replace").decode())

    def note_error(self, e: BaseException, where: str, halt: bool = True):
        """Log an exception; the first 401/403 halts the bot after cancelling (m7) unless halt=False."""
        self.log("error", f"{where}: {type(e).__name__}", where=where, error=type(e).__name__,
                 status=getattr(e, "status", None))
        if halt and is_auth_error(e) and self.halt_reason is None:
            self.halt_reason = f"auth error (401/403) in {where}"

    # -------------------------------------------------- heartbeat (read by scripts/early_overs_notify.py)
    def placing_now(self, day, now: datetime) -> bool:
        """True only when this bot can really place orders right now: live, clock check ok, not halted/stopped,
        before the end date and the nightly caps (attempts, $) not exhausted."""
        if not self.live or not self.clock_ok or self.halt_reason or self.stopped or now > HARD_END:
            return False
        tonight = self._tonight((day or now.astimezone(ET).date()).isoformat())
        if len(tonight) >= MAX_ATTEMPTS_PER_NIGHT:
            return False
        if self._read_fails >= MAX_READ_FAILS:
            return False
        if self._last_cash is not None and \
                self._last_cash < Decimal(MAX_PRICE_C) / 100 + est_fee(MAX_PRICE_C) + CASH_BUFFER:
            return False                                    # cannot afford even a max-price order
        spent = sum((order_cost(o) for o in tonight), Decimal(0))
        return spent + Decimal(MIN_PRICE_C) / 100 + est_fee(MIN_PRICE_C) <= NIGHT_CAP_USD

    def heartbeat(self, day=None, force: bool = False, live=None, placing=None):
        """Atomically write logs/early_overs_bot.heartbeat {ts_utc, pid, live, placing, night}, at most every 60 s
        unless forced or `placing` changed. A dry run never overwrites a fresh heartbeat of a live bot."""
        now = self.clock()
        live = self.live if live is None else live
        placing = (self.placing_now(day, now) if live else False) if placing is None else placing
        changed = placing != getattr(self, "_hb_placing", None)
        if not force and not changed and self._hb_at is not None and now - self._hb_at < HEARTBEAT_EVERY:
            return
        try:
            if not live:
                try:
                    cur = json.loads(self.heartbeat_path.read_text(encoding="utf-8"))
                    ts = datetime.fromisoformat(cur["ts_utc"])
                    if cur.get("live") is True and cur.get("pid") != os.getpid() and \
                            abs((now - ts).total_seconds()) < 180:
                        return                                 # a live bot is alive: don't clobber it
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            night = (day or now.astimezone(ET).date()).isoformat()
            payload = {"ts_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds"), "pid": os.getpid(),
                       "live": bool(live), "placing": bool(placing), "night": night}
            self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.heartbeat_path.with_name(self.heartbeat_path.name + ".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self.heartbeat_path)
            self._hb_at, self._hb_placing = now, placing
        except OSError as e:
            self.log("error", f"heartbeat write failed: {type(e).__name__}", error=type(e).__name__)

    # -------------------------------------------------- reconcile from the log (live records only)
    def reconcile_from_log(self):
        if not self.log_path.exists():
            return
        n = 0
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("dry") is not False:
                continue
            ev = r.get("event")
            if ev == "order_attempt":
                k = (r.get("day"), r.get("game"), int(r.get("attempt", 1)))
                tip, cancel_at = datetime.fromisoformat(r["tip"]), datetime.fromisoformat(r["cancel_at"])
                self.orders[k] = new_order(r["day"], r["game"], k[2], r["ticker"], r["outcome"], r["price_c"],
                                           r.get("max_c", r["price_c"]), r["coid"], r.get("exchange_index"), tip,
                                           cancel_at, count=_num(r.get("count")) or Decimal(COUNT))
                pick = {"source": r.get("source", "log"), "side": r.get("side"), "outcome": r["outcome"],
                        "ticker": r["ticker"], "strike": _num(r.get("strike")), "max_c": r.get("max_c", r["price_c"])}
                self.games[(r["day"], r["game"])] = new_game(r["day"], r["game"], pick, tip)
                n += 1
            elif ev == "order_state":
                o = self.orders.get((r.get("day"), r.get("game"), int(r.get("attempt", 1))))
                if o is not None:
                    o["state"] = r["state"]
                    o["filled"] = r.get("filled", o["filled"])
                    o["order_id"] = r.get("order_id") or o["order_id"]
        for (d, g), gm in self.games.items():                  # a filled game is done; others re-arm with their pick
            if game_filled(self._game_orders(d, g)) >= COUNT:
                gm["closed"] = "filled"
        self.log("reconcile", f"restored {n} live order record(s) from the log", n=n)

    # -------------------------------------------------- schedule
    def _compute_rows(self, day, now, on_header=None, on_skips=None):
        kw = {} if on_skips is None else {"on_skips": on_skips}
        if self._compute is None:
            import early_overs_picks as eop
            return eop.compute_rows(day, now, on_header, **kw)
        return self._compute(day, now, on_header, **kw)

    def load_schedule(self, day: date, now: datetime):
        games, skips = [], []

        def hdr(n27, g):
            games.extend(g)
            raise _Abort()
        try:
            self._compute_rows(day, now, hdr, skips.extend)
        except _Abort:
            pass
        games.sort(key=lambda g: (g[0], g[1]))
        self.sched[day.isoformat()] = {"games": games, "loaded_at": now}
        self._drop_unconfirmed(day.isoformat(), skips)
        for tip, key, _ in games:                           # an armed game whose tip moved EARLIER gets an earlier cancel
            gm = self.games.get((day.isoformat(), key))
            if gm is None or tip == gm["tip"]:
                continue
            if tip < gm["tip"]:
                gm["tip"], gm["cancel_at"] = tip, tip - timedelta(minutes=CANCEL_MIN)
                for o in self._game_orders(day.isoformat(), key):
                    o["tip"], o["cancel_at"] = gm["tip"], gm["cancel_at"]
                self.log("tip_moved", f"{key} tip moved EARLIER to {tip.astimezone(ET):%H:%M} ET; cancel_at now "
                                      f"{gm['cancel_at'].astimezone(ET):%H:%M}", day=day.isoformat(), game=key)
                if now >= gm["cancel_at"] and not gm["closed"]:
                    self._close(gm, "tip moved earlier; past the new cancel time")
            else:
                self.log("tip_moved", f"{key} tip moved LATER to {tip.astimezone(ET):%H:%M} ET; keeping the earlier "
                                      f"cancel time", day=day.isoformat(), game=key)
        self.log("schedule", f"{day} {len(games)} game(s): " +
                 ", ".join(f"{k} tip {t.astimezone(ET):%H:%M}" for t, k, _ in games), day=day.isoformat(),
                 games=[k for _, k, _ in games])

    def _drop_unconfirmed(self, d: str, skips: list[dict]):
        """Armed games whose tip is no longer confirmed by the hoopR schedule (SKIP rows from compute_rows: missing,
        TBD/postponed, stale file, > 15 min from Kalshi): cancel their open orders and close them. Filled contracts are
        never touched. A stale schedule file closes every such game with ONE summary line instead of one per game."""
        hit = [(self.games[(d, r["game"])], r) for r in skips if (d, r["game"]) in self.games]
        if not hit:
            return
        stale = all(r.get("tip_skip") == "stale" for r in skips)
        closed = []
        for gm, r in hit:
            why = "tip no longer confirmed: " + str(r.get("status", "")).removeprefix("SKIP: ")
            for o in self._game_orders(d, gm["game"]):
                if o["state"] in OPEN_STATES:
                    self.cancel_order(o, "tip unconfirmed")
            if gm["closed"]:
                continue
            if stale:
                gm["closed"] = why
                closed.append(gm["game"])
            else:
                self._close(gm, why)
        if stale and closed:
            note = str(skips[0].get("status", "")).removeprefix("SKIP: ")
            self.log("game_closed", f"{len(closed)} armed game(s) closed: {note}: " + ", ".join(closed),
                     day=d, games=closed, reason=f"tip no longer confirmed: {note}")

    # -------------------------------------------------- order state refresh / fills
    def _tonight(self, day: str) -> list[dict]:
        return [o for (d, _, _), o in self.orders.items() if d == day]

    def _game_orders(self, day: str, game: str) -> list[dict]:
        return [o for (d, g, _), o in self.orders.items() if d == day and g == game]

    def _apply(self, o: dict, parsed: dict, source: str):
        """Update an order record from a parsed exchange view and log state changes / fills. Fills only go up, and a
        record that reached 'filled' stays 'filled' (done) whatever a later or sparse view says."""
        if parsed["state"] == "unknown" and parsed["api_status"] is None:
            return                                         # nothing usable: keep the old state (m11)
        prev_state, prev_filled = o["state"], Decimal(str(o["filled"]))
        new_filled = max(prev_filled, parsed["filled"])
        o["filled"] = new_filled
        o["state"] = "filled" if "filled" in (prev_state, parsed["state"]) else parsed["state"]
        if new_filled != prev_filled:
            avg = None if parsed.get("avg_cost") is None else f"{parsed['avg_cost']:.4f}"
            self.log("fill", f"{o['game']} a{o['n']} {o['outcome'].upper()} filled={new_filled} "
                             f"avg_cost={avg} fees={parsed['fees']} (limit {o['price_c']}c)", day=o["day"],
                     game=o["game"], attempt=o["n"], ticker=o["ticker"], order_id=o["order_id"],
                     filled=str(new_filled), avg_cost=avg, fees=str(parsed["fees"]), source=source)
        if o["state"] != prev_state or new_filled != prev_filled:
            self.log("order_state", f"{o['game']} a{o['n']} {prev_state}->{o['state']} filled={new_filled}",
                     day=o["day"], game=o["game"], attempt=o["n"], order_id=o["order_id"], state=o["state"],
                     filled=str(new_filled), source=source)

    def _view(self, o: dict):
        """Current exchange view of one order, or None. Resolves via the ticker listing (works across shards)
        matching order_id or client_order_id FIRST; the single-order GET is only a fallback and its errors (even
        403/404) never halt the bot. A clean listing without the order counts as a miss (-> not_found after
        cancel_at + 10 min and 3 misses)."""
        oid = o["order_id"]
        listed = False
        try:
            kw = {"exchange_index": o["exchange_index"]} if isinstance(o["exchange_index"], int) and \
                not isinstance(o["exchange_index"], bool) else {}
            lst = list(self.client.orders(ticker=o["ticker"], **kw))
            listed = True
        except Exception as e:  # noqa: BLE001
            self.note_error(e, f"orders({o['game']})")
            lst = []
        for x in lst:
            if (oid and x.get("order_id") == oid) or x.get("client_order_id") == o["coid"]:
                if not o["order_id"]:
                    o["order_id"] = x.get("order_id")
                o["miss"] = 0
                return parse_order_obj(x)
        if oid:
            try:
                d = self.client.order(oid)
                if d:
                    o["miss"] = 0
                    return parse_order_obj(d)
            except Exception as e:  # noqa: BLE001
                self.note_error(e, f"order({o['game']}) single GET (fallback; never halts)", halt=False)
        if not listed:
            return None
        o["miss"] += 1
        if o["state"] in OPEN_STATES and self.clock() >= o["cancel_at"] + NOT_FOUND_AFTER and \
                o["miss"] >= NOT_FOUND_MISSES:
            o["state"] = "not_found"                        # full cost stays counted; stops blocking day_done
            self.log("order_state", f"{o['game']} a{o['n']} -> not_found after {o['miss']} listings without the "
                                    f"order (cost stays counted)", day=o["day"], game=o["game"], attempt=o["n"],
                     order_id=o["order_id"], state="not_found", filled=str(o["filled"]), source="listing")
        return None

    def refresh(self):
        if not self.live:
            return
        for o in list(self.orders.values()):
            if o["state"] in OPEN_STATES:
                v = self._view(o)
                if v:
                    self._apply(o, v, "poll")

    # -------------------------------------------------- cancel (backstop: IOC orders should never rest)
    def cancel_order(self, o: dict, reason: str) -> bool:
        if not self.live:
            self.log("cancel", f"DRY would cancel {o['game']} a{o['n']} ({reason})", game=o["game"])
            return True
        o["cancel_tries"] += 1
        if not o["order_id"]:
            v = self._view(o)                               # finds the id by client_order_id
            if v:
                self._apply(o, v, "pre_cancel")
        if o["state"] not in OPEN_STATES:
            return True
        if not o["order_id"]:
            self.log("error", f"{o['game']} cannot cancel: no order id (try {o['cancel_tries']})", game=o["game"])
            return False
        try:
            ei = o["exchange_index"] if isinstance(o["exchange_index"], int) else \
                self.client.market(o["ticker"])["exchange_index"]
            res = self.client.cancel_order(o["order_id"], ei)
            self.log("cancel", f"{o['game']} a{o['n']} {o['ticker']} oid={o['order_id']} reason={reason} "
                               f"outcome={res.outcome} status={res.status}", game=o["game"], attempt=o["n"],
                     order_id=o["order_id"], reason=reason, outcome=res.outcome, http_status=res.status,
                     data=res.data if isinstance(res.data, dict) else None)
            if res.outcome == "auth" and self.halt_reason is None:
                self.halt_reason = "auth error (401/403) on cancel"
        except Exception as e:  # noqa: BLE001
            self.note_error(e, f"cancel({o['game']})")
        v = self._view(o)                                   # always re-read the truth after a cancel attempt
        if v:
            self._apply(o, v, "after_cancel")
        return o["state"] not in OPEN_STATES

    def cancel_due(self, now: datetime):
        """Tip-15: cancel anything still open / unresolved. Filled orders are never touched."""
        for o in list(self.orders.values()):
            if o["state"] in OPEN_STATES and now >= o["cancel_at"] and o["cancel_tries"] < MAX_CANCEL_TRIES:
                self.cancel_order(o, "tip-15")

    def cancel_all_open(self, reason: str):
        for o in list(self.orders.values()):
            if o["state"] in OPEN_STATES:
                self.cancel_order(o, reason)

    # -------------------------------------------------- pings
    def read_pings(self):
        """(pings, ok). A missing file is ok (no pings yet). A file that exists but cannot be parsed (half-written)
        is NOT ok: no fallback is armed on that loop."""
        if not self.ping_path.exists():
            return {}, True
        try:
            d = json.loads(self.ping_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}, False
        return (d, True) if isinstance(d, dict) else ({}, False)

    # -------------------------------------------------- arming (choose the pick) and attempts
    def place_window(self, day: date, now: datetime):
        d = day.isoformat()
        games = self.sched.get(d, {}).get("games", [])
        if now > HARD_END:
            if any(self._startable(d, t, k, now) for t, k, _ in games):
                self.log("caps", f"no orders after {HARD_END.astimezone(ET):%Y-%m-%d %H:%M} ET; skipping", day=d)
            return
        pings, pings_ok = self.read_pings()
        need_fb = []
        forced = None
        if self.force_first and not self.live and not self.forced_done and \
                not any(g["day"] == d for g in self.games.values()):
            forced = next((k for t, k, _ in games if (d, k) not in self.games), None)
        for tip, key, ms in games:
            if (d, key) in self.games or not self._startable(d, tip, key, now, forced == key):
                continue
            info = pings.get(f"{d}|{key}")
            if info is not None:
                pick, why = parse_ping(info, key)
                if pick is None:
                    self.games[(d, key)] = new_game(d, key, {"source": "ping", "side": None, "outcome": None,
                                                              "ticker": None, "strike": None, "max_c": None}, tip, ms)
                    self._close(self.games[(d, key)], f"unusable ping: {why}")
                    continue
                self._arm(d, key, pick, tip, ms, now)
            elif not pings_ok:
                self._note_wait(key, d, "notified file unreadable this loop; no fallback until it parses")
            elif now >= tip - timedelta(minutes=FALLBACK_MIN) or forced == key:
                need_fb.append((tip, key, ms))
            else:
                self._note_wait(key, d, f"no ping yet; waiting (fallback at tip-{FALLBACK_MIN}, "
                                        f"{(tip - timedelta(minutes=FALLBACK_MIN)).astimezone(ET):%H:%M} ET)")
        if need_fb:
            wanted = {k for _, k, _ in need_fb}

            def only_wanted(n27, g):                  # filter IN PLACE so compute_rows fetches books for these games only
                g[:] = [x for x in g if x[1] in wanted]
            try:
                rows, *_ = self._compute_rows(day, now, only_wanted)
            except Exception as e:  # noqa: BLE001  -- armed games must still get their attempt loop
                self.note_error(e, "fallback compute_rows (will retry next loop)")
                rows, need_fb = [], []
            by_game = {r["game"]: r for r in rows}
            for tip, key, ms in need_fb:
                pick, why = pick_from_row(by_game.get(key), key)
                if pick is None:
                    self.games[(d, key)] = new_game(d, key, {"source": "fallback", "side": None, "outcome": None,
                                                              "ticker": None, "strike": None, "max_c": None}, tip, ms)
                    self._close(self.games[(d, key)], f"fallback: {why}")
                    continue
                self._arm(d, key, pick, tip, ms, now)
        for gm in list(self.games.values()):
            if gm["day"] == d and not gm["closed"]:
                if self.halt_reason or self.stop_requested():
                    return
                self.attempt(gm, now)

    def _note_wait(self, key, d, why):
        tag = (key, why)
        if tag not in self._waiting:
            self._waiting.add(tag)
            self.log("wait_ping", f"{key} {why}", game=key, day=d)

    def _startable(self, d, tip, key, now, forced=False) -> bool:
        return forced or (tip - timedelta(minutes=OPEN_MIN) <= now <= tip - timedelta(minutes=CLOSE_MIN))

    def _arm(self, d, key, pick, tip, ms, now):
        gm = new_game(d, key, pick, tip, ms)
        self.games[(d, key)] = gm
        self.log("pick", f"{key} source={pick['source'].upper()} {pick['side']} strike {pick['strike']} "
                         f"{pick['ticker']} buy {pick['outcome'].upper()} max price {pick['max_c']}c",
                 day=d, game=key, source=pick["source"], side=pick["side"], strike=str(pick["strike"]),
                 ticker=pick["ticker"], max_c=pick["max_c"])

    def _close(self, gm, why: str):
        gm["closed"] = why
        self.log("game_closed", f"{gm['game']} closed: {why}", day=gm["day"], game=gm["game"], reason=why)

    def _note(self, gm, why: str, level_event="no_send", **kw):
        """Log a per-loop non-action only when it changes (the loop runs every minute)."""
        if gm["last_note"] == why:
            return
        gm["last_note"] = why
        self.log(level_event, f"{gm['game']} {why}", day=gm["day"], game=gm["game"], **kw)

    # -------------------------------------------------- reads used by an attempt
    def _ask_c(self, gm):
        """(ask cents, size at that ask) of the side being bought, from the live orderbook. The ask of one side is
        100 - the best bid of the other side. (None, None) if the book is one-sided; size None = unknown (dry run)."""
        if self.live:
            book = self.client.orderbook(gm["ticker"])
            lvl = units.best_level(book.get("no" if gm["outcome"] == "yes" else "yes"))
            return (None, None) if lvl is None else (100 - int(round(lvl[0] * 100)), Decimal(str(lvl[1])))
        return self._reader().side_ask(gm["ticker"], gm["outcome"])

    def _reader(self):
        if self.public is None:
            self.public = PublicReader()
        return self.public

    def _market(self, gm):
        return self.client.market(gm["ticker"]) if self.live else self._reader().market(gm["ticker"])

    def _own_positions(self, ev: str) -> dict:
        """Signed contracts this bot has filled per ticker of event `ev` (+YES, -NO), from its own order records."""
        own: dict = {}
        for o in list(self.orders.values()):
            if not str(o.get("ticker")).startswith(ev + "-"):
                continue
            f = max(Decimal(str(o["filled"])), Decimal(str(o["count"])) if o["state"] == "filled" else Decimal(0))
            own[o["ticker"]] = own.get(o["ticker"], Decimal(0)) + (f if o.get("outcome") == "yes" else -f)
        return own

    def _exposure(self, ev: str, ei: int):
        """(foreign positions, resting orders) for the whole event, any source. Raises on read errors. A position
        on a ticker is foreign only by the amount it EXCEEDS the bot's own filled contracts there on the same side
        (tolerance POS_TOL); a lagging position that shows less than our fills is not foreign; the opposite side, a
        manual bet on the same strike on top of ours, and any other strike are all caught."""
        own = self._own_positions(ev)
        pos = list(self.client.positions(event_ticker=ev, exchange_index=ei))
        rest = [x for x in self.client.orders(event_ticker=ev, status="resting", exchange_index=ei)
                if not isinstance(x.get("ticker"), str) or x["ticker"].startswith(ev + "-")]   # API may ignore the filter
        rest = [x for x in rest if x.get("status", "resting") == "resting"]
        exposed = []
        for x in pos:
            t = x.get("ticker")
            if isinstance(t, str) and not t.startswith(ev + "-"):
                continue                                    # a position on another event: not ours to judge
            raw = x.get("position_fp", x.get("position"))
            try:
                if raw is None or t is None:
                    nonzero = True                          # missing/unparsable = exposed
                else:
                    pos_q, mine = Decimal(str(raw).strip()), own.get(t, Decimal(0))
                    if mine == 0:
                        nonzero = pos_q != 0
                    elif pos_q != 0 and (pos_q > 0) != (mine > 0):
                        nonzero = True                      # opposite side of what we hold
                    else:
                        nonzero = abs(pos_q) - abs(mine) > POS_TOL
            except Exception:  # noqa: BLE001
                nonzero = True
            if nonzero:
                exposed.append(t or "?")
        return exposed, rest

    # -------------------------------------------------- one IOC attempt
    def attempt(self, gm, now) -> bool:
        """Try ONE IOC for this armed game now. Returns True if an order was sent (or would have been, dry)."""
        d, key = gm["day"], gm["game"]
        if now >= gm["cancel_at"]:
            self._close(gm, f"tip-{CANCEL_MIN} reached without a fill")
            return False
        mine = self._game_orders(d, key)
        have = game_filled(mine)
        if have >= COUNT or any(o["state"] == "filled" for o in mine):
            self._close(gm, "filled (done)")
            return False
        if any(o["state"] in OPEN_STATES for o in mine):
            return False                                    # a previous attempt is unresolved: wait for refresh
        if len(mine) >= MAX_ATTEMPTS_PER_GAME:
            self._close(gm, f"{MAX_ATTEMPTS_PER_GAME} attempts used without a full fill (filled {have})")
            return False
        want = Decimal(COUNT) - have                        # 1 contract; the remainder after a partial fill (top-up)
        try:
            ask_c, ask_sz = self._ask_c(gm)
        except Exception as e:  # noqa: BLE001
            self.note_error(e, f"orderbook({key})")
            return False
        if ask_c is None:
            self._note(gm, "no ask on the book (one-sided); not sending")
            return False
        if ask_c > gm["max_c"]:
            self._note(gm, f"ask {ask_c}c > max {gm['max_c']}c; not sending, not chasing", ask_c=ask_c)
            return False
        if not (MIN_PRICE_C <= ask_c <= MAX_PRICE_C):
            self._note(gm, f"ask {ask_c}c outside {MIN_PRICE_C}-{MAX_PRICE_C}c; not sending", ask_c=ask_c)
            return False
        if ask_sz is not None and ask_sz < want:
            self._note(gm, f"thin ask: {ask_sz} contract(s) at {ask_c}c < {want} needed; not sending",
                       level_event="skip", ask_c=ask_c)
            return False
        try:
            m = self._market(gm)
        except Exception as e:  # noqa: BLE001
            self.note_error(e, f"market({key})")
            return False
        if m.get("status") != "active":
            self._note(gm, f"market status {m.get('status')}; not sending", level_event="skip")
            return False
        fs = _num(m.get("floor_strike"))
        if fs is None or fs != gm["strike"]:
            self._close(gm, f"strike mismatch: pick {gm['strike']} vs market floor_strike {m.get('floor_strike')!r} "
                            f"on {gm['ticker']}")
            return False
        ei = m.get("exchange_index") if self.live else gm["dry_ei"]
        if self.live:
            if isinstance(ei, bool) or not isinstance(ei, int) or ei < 0:
                self._note(gm, f"exchange_index missing/invalid ({ei!r}); never defaulting", level_event="skip")
                return False
            ev = m.get("event_ticker")                      # exposure at EVENT level: any strike of this game
            if not isinstance(ev, str) or not ev.startswith(SERIES + "-"):
                self._note(gm, f"event_ticker missing/invalid ({ev!r}); failing closed", level_event="skip")
                return False
            try:
                exposed, rest = self._exposure(ev, ei)
            except Exception as e:  # noqa: BLE001
                self._read_fails += 1
                self.note_error(e, f"exposure check({key})")
                self._note(gm, "exposure check failed; failing closed", level_event="skip")
                return False
            if exposed or rest:
                self._close(gm, f"already exposed on event {ev}: positions {exposed}, resting orders {len(rest)} "
                                f"(placed by hand?); skipping the game")
                return False
            need = Decimal(ask_c) / 100 * want + est_fee(ask_c, want) + CASH_BUFFER
            try:
                cash = self.client.shard_cash(ei)           # read-only GET /portfolio/balance?exchange_index=
            except Exception as e:  # noqa: BLE001
                self._read_fails += 1
                self.note_error(e, f"shard_cash({key})")
                self._note(gm, "cash read failed; failing closed (low cash?)", level_event="skip")
                return False
            self._read_fails = 0                            # both reads worked
            self._last_cash = None if cash is None else Decimal(str(cash))
            if cash is None or Decimal(str(cash)) < need:
                self._note(gm, f"low cash: {cash} < {need:.4f} needed; not sending", level_event="skip")
                return False
        else:
            ei = ei if isinstance(ei, int) and not isinstance(ei, bool) else 0
        fresh = self.clock()                                # M3: fresh time/caps right before sending
        if fresh >= gm["cancel_at"]:
            self._note(gm, "cancel time reached while preparing; not sending", level_event="skip")
            return False
        bad = check_caps(self._tonight(d), key, gm["ticker"], "buy", want, ask_c, fresh)
        self.log("caps", f"{key} " + ("OK" if not bad else "BLOCKED: " + "; ".join(bad)), game=key, day=d,
                 ok=not bad, violations=bad)
        if bad:
            if any("nightly cap" in b or "attempts tonight" in b or "end date" in b for b in bad):
                self._close(gm, "caps: " + "; ".join(bad))
            return False
        if self.stop_requested() or self.halt_reason:
            return False
        n = len(mine) + 1
        coid = f"eob-{d}-{key}-a{n}"
        body = build_body(gm["ticker"], gm["outcome"], ask_c, ei, coid, want)
        from kpm.execution.client import validate_order_body
        body = validate_order_body(body)
        o = new_order(d, key, n, gm["ticker"], gm["outcome"], ask_c, gm["max_c"], coid, ei, gm["tip"],
                      gm["cancel_at"], "open" if not self.live else "unknown", want)
        gm["last_note"] = None
        if not self.live:
            self.log("would_send", f"DRY RUN, NOT SENT: POST /portfolio/events/orders {json.dumps(body, sort_keys=True)}"
                     f"  (source {gm['source']}, max {gm['max_c']}c, ask {ask_c}c; assuming it fills)",
                     game=key, attempt=n, body=body, source=gm["source"], ask_c=ask_c, max_c=gm["max_c"])
            o["state"], o["filled"] = "filled", want            # dry run: pretend the IOC filled, game done
            self.orders[(d, key, n)] = o
            if self.force_first:
                self.forced_done = True
            return True
        self.orders[(d, key, n)] = o                        # recorded BEFORE sending: a crash can never double-place
        self.log("order_attempt", f"{key} a{n} SENDING IOC {gm['outcome'].upper()} x{want} @ {ask_c}c "
                                  f"(max {gm['max_c']}c, source {gm['source']}) coid={coid}", day=d, game=key,
                 attempt=n, ticker=gm["ticker"], outcome=gm["outcome"], side=gm["side"], strike=str(gm["strike"]),
                 source=gm["source"], price_c=ask_c, max_c=gm["max_c"], count=str(want), coid=coid,
                 exchange_index=ei, tip=gm["tip"].isoformat(), cancel_at=gm["cancel_at"].isoformat(), body=body)
        late = self.clock()                                 # last look before the POST (the log write can stall)
        why = None
        if late >= gm["cancel_at"]:
            why = "late"
        elif self.stop_requested():
            why = "stop"
        elif late > HARD_END:
            why = "after end date"
        elif self.halt_reason:
            why = "halt"
        if why:
            o["state"] = "failed"
            self.log("order_state", f"{key} a{n} -> failed (late/stop before send: {why}); NOT POSTED", day=d,
                     game=key, attempt=n, order_id=None, state="failed", filled="0", source="presend",
                     reason="late/stop before send")
            return False
        try:
            res = self.client.create_order(body)
        except Exception as e:  # noqa: BLE001
            self.note_error(e, f"create_order({key}) (state unknown, will reconcile)")
            return True
        self._after_create(o, res)
        return True

    def _after_create(self, o, res):
        d, key, n = o["day"], o["game"], o["n"]
        data = res.data if isinstance(res.data, dict) else {}
        self.log("order_sent", f"{key} a{n} outcome={res.outcome} http={res.status} order_id={res.order_id} "
                               f"fill={data.get('fill_count')} remaining={data.get('remaining_count')}",
                 day=d, game=key, attempt=n, ok=res.ok, outcome=res.outcome, http_status=res.status,
                 order_id=res.order_id, data=data or None, error=res.error)
        if res.ok:
            o["order_id"] = res.order_id
            fill, rem = _dec(data.get("fill_count", 0)), _dec(data.get("remaining_count", 0))
            yp = _num(data.get("average_fill_price"))                  # YES price
            sp = None if yp is None else (yp if o["outcome"] == "yes" else 1 - yp)
            fee_each = _dec(data.get("average_fee_paid", 0))
            # a (misbehaving) IOC with contracts left may still be resting: keep it open (counted, cancelled later)
            st = "open" if rem > 0 else ("filled" if fill >= Decimal(str(o["count"])) else "canceled")
            self._apply(o, {"state": st, "filled": fill, "remaining": rem, "fees": fee_each * fill,
                            "avg_cost": sp, "api_status": "created"}, "create")
            if o["order_id"]:                                          # confirm with the exchange's own record
                v = self._view(o)
                if v:
                    self._apply(o, v, "confirm")
        elif res.outcome in ("rejected", "auth", "throttled"):   # definitely not placed (4xx / repeated 429)
            o["state"] = "failed"
            self.log("order_state", f"{key} a{n} -> failed ({res.outcome})", day=d, game=key, attempt=n,
                     order_id=None, state="failed", filled="0", source="create")
            if res.outcome == "auth":
                self.halt_reason = "auth error (401/403) on create"
        else:                                               # unknown or 409 duplicate: may exist; cost stays counted
            self.log("order_state", f"{key} a{n} -> unknown ({res.outcome}); will resolve by client_order_id",
                     day=d, game=key, attempt=n, order_id=None, state="unknown", filled="0", source="create")
            v = self._view(o)
            if v:
                self._apply(o, v, "resolve")

    # -------------------------------------------------- one tick / loops
    def stop_requested(self) -> bool:
        return self.stop_path.exists()

    def tick(self, day: date, now: datetime):
        try:
            self._tick(day, now)
        finally:
            self.heartbeat(day)                             # reflect cap exhaustion / failures from this tick at once

    def _tick(self, day: date, now: datetime):
        d = day.isoformat()
        s = self.sched.get(d)
        self.heartbeat(day)
        self.refresh()                                      # fills and cancels come first, whatever else fails
        self.cancel_due(now)
        if s is None or now - s["loaded_at"] >= SCHEDULE_RELOAD:
            try:
                self.load_schedule(day, now)
            except Exception as e:  # noqa: BLE001
                self.note_error(e, "schedule reload (keeping the old schedule)")
        if not self.clock_ok:
            self.log("skip", f"{day} placing disabled: clock check failed at day start", day=d)
            return
        self.place_window(day, now)

    def day_done(self, day: date, now: datetime) -> bool:
        d = day.isoformat()
        s = self.sched.get(d)
        if s is None:
            return False
        if any(o["state"] in OPEN_STATES and o["cancel_tries"] < MAX_CANCEL_TRIES and o["day"] == d
               for o in self.orders.values()):
            return False
        if not s["games"]:
            return now >= day_start(day) + timedelta(hours=EMPTY_DAY_GIVE_UP_H)
        return all(now > t - timedelta(minutes=CANCEL_MIN) for t, _, _ in s["games"])

    def interruptible_sleep(self, seconds: float):
        remaining = float(seconds)                          # counted down, so an injected sleep drives it in tests
        while remaining > 0 and not self.stop_requested():
            step = min(5.0, remaining)
            self._sleep(step)
            remaining -= step

    def _check_clock(self, soft: bool = False) -> bool:
        """At start (soft=False) a bad clock refuses to start; at later day starts (soft=True) it is logged and that
        day's placing is skipped (refresh and cancel still run)."""
        try:
            skew = float(self.client.server_time_skew())
        except Exception as e:  # noqa: BLE001
            if not soft:
                raise SystemExit(f"clock check failed ({type(e).__name__}); refusing to start live")
            self.log("error", f"clock check failed ({type(e).__name__}); no placing today", error=type(e).__name__)
            return False
        self.log("clock", f"local - exchange skew {skew:+.2f}s", skew_s=skew)
        if abs(skew) > MAX_SKEW_S:
            if not soft:
                raise SystemExit(f"clock skew {skew:+.2f}s exceeds {MAX_SKEW_S}s; fix the PC clock. Refusing to start.")
            self.log("error", f"clock skew {skew:+.2f}s exceeds {MAX_SKEW_S}s; no placing today")
            return False
        return True

    def run(self, first_day: date, once: bool = False):
        if self.live and once:
            raise ValueError("--live --once is refused")
        self.log("start", f"mode={'LIVE' if self.live else 'DRY-RUN'} (IOC instant fills) until={self.until} "
                          f"force_first={self.force_first} caps: series={SERIES} count={COUNT} "
                          f"price={MIN_PRICE_C}-{MAX_PRICE_C}c attempts<={MAX_ATTEMPTS_PER_GAME}/game "
                          f"{MAX_ATTEMPTS_PER_NIGHT}/night night_cap=${NIGHT_CAP_USD} "
                          f"end={HARD_END.astimezone(ET):%Y-%m-%d %H:%M}ET",
                 live=self.live, until=str(self.until), force_first=self.force_first, series=SERIES, count=COUNT,
                 min_price_c=MIN_PRICE_C, max_price_c=MAX_PRICE_C, max_attempts_game=MAX_ATTEMPTS_PER_GAME,
                 max_attempts_night=MAX_ATTEMPTS_PER_NIGHT, night_cap_usd=str(NIGHT_CAP_USD),
                 hard_end=HARD_END.isoformat(), tif="immediate_or_cancel")
        if self.live:
            self._lock = acquire_lock(self.lock_path)       # refuses (SystemExit) if another instance runs
        try:
            if self.live:
                self._check_clock()
                self.reconcile_from_log()
            self._run_days(first_day, once)
        except KeyboardInterrupt:
            self._stop("Ctrl+C")
        finally:
            try:
                if self.live and not self.stopped and any(o["state"] in OPEN_STATES for o in self.orders.values()):
                    self.stopped = True
                    self.log("stop", "exiting with open orders: cancelling them")
                    self.cancel_all_open("exit")
            finally:
                if self.live:
                    self.heartbeat(force=True, live=False, placing=False)  # tell the notifier the bot is gone
                if self._lock is not None:
                    release_lock(self._lock)
                    self._lock = None

    def _run_days(self, first_day: date, once: bool):
        if self.stop_requested():
            return self._stop("STOP file present at start")
        day = first_day
        while day <= self.until:
            if self.live and day != first_day:
                self.clock_ok = self._check_clock(soft=True)      # re-checked once at each later day start
            while True:
                now = self.clock()
                if self.stop_requested():
                    return self._stop("STOP file found")
                try:
                    self.tick(day, now)
                except Exception as e:  # noqa: BLE001
                    self.note_error(e, f"tick: {str(e)[:100]}")
                if self.halt_reason:
                    return self._stop(f"HALT {self.halt_reason}")
                if once:
                    return self.log("exit", "single pass done (--once)")
                if self.day_done(day, self.clock()):
                    break
                self.interruptible_sleep(LOOP_S)
            self.log("day_done", f"{day} finished", day=day.isoformat())
            day += timedelta(days=1)
            if day > self.until:
                break
            nxt = day_start(day)
            self.log("sleep", f"sleeping until {nxt.astimezone(ET):%Y-%m-%d %H:%M} ET")
            while self.clock() < nxt:
                if self.stop_requested():
                    return self._stop("STOP file found")
                self.heartbeat(day)
                self._sleep(5.0)
        self.log("exit", "after the last day")

    def _stop(self, why: str):
        self.stopped = True
        self.log("stop", f"{why}: cancelling all open orders and exiting")
        self.cancel_all_open(why)
        self.log("exit", "stopped")


# ------------------------------------------------------------------ CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default: send nothing, print what would be sent")
    mode.add_argument("--live", action="store_true", help="send real orders (needs KALSHI_ENV=prod and keys in .env)")
    ap.add_argument("--until", default="2026-11-03", help="last ET date to run (default 2026-11-03)")
    ap.add_argument("--once", action="store_true", help="run a single pass and exit (dry-run checks only)")
    ap.add_argument("--force-first", action="store_true",
                    help="DRY-RUN ONLY: show the order for the first game of the day even outside its window")
    ap.add_argument("--date", help="first ET date YYYY-MM-DD (default: today ET)")
    a = ap.parse_args(argv)
    if a.live and (a.force_first or a.date or a.once):
        raise SystemExit("--force-first / --date / --once are dry-run only")
    client = None
    if a.live:
        require_prod_env()
        from kpm.execution.client import ExecClient
        from kpm.execution.env import load_env
        env = load_env(require_keys=True, expected="prod")
        client = ExecClient(env, live=True)
        print(f"live client: {env.banner()}")           # banner holds no key id/path/material
    now = datetime.now(timezone.utc)
    first = date.fromisoformat(a.date) if a.date else now.astimezone(ET).date()
    bot = Bot(client=client, live=a.live, until=date.fromisoformat(a.until), force_first=a.force_first)
    bot.run(first, once=a.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
