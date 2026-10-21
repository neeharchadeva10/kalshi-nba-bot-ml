"""Small, heavily capped live order bot for the nightly NBA totals bets (research log #40, formula-side version).

  DRY RUN (default, sends nothing):   .venv/Scripts/python.exe reports/output/early_overs_bot/overs_bot.py
  LIVE (the USER starts this):        KALSHI_ENV=prod .venv/Scripts/python.exe reports/output/early_overs_bot/overs_bot.py --live
  STOP:   create the file reports/output/early_overs_bot/STOP  (bot cancels its open orders and exits), or Ctrl+C.

Per game: ONE limit BUY of 1 contract on the chosen side at that side's best bid (post-only, GTC, expires at tip-15),
placed T-75..T-45 min before the pick-list tip time, cancelled if not filled by tip-15. Never re-placed, chased or
modified. Picks come from scripts/early_overs_picks.py compute_rows (unchanged). Hard caps are in code (see CAPS).
Authenticated calls (live only), copied from reports/output/picks/approve.py:
  ExecClient(env, live=True).market(ticker)             GET  /markets/{t}                     (status, exchange_index)
  ExecClient(env, live=True).orderbook(ticker)          GET  /markets/{t}/orderbook           (fresh bid re-check)
  ExecClient(env, live=True).positions(ticker=...)      GET  /portfolio/positions             (double-exposure check)
  ExecClient(env, live=True).orders(ticker=..., status="resting")   GET /portfolio/orders     (double-exposure check)
  ExecClient(env, live=True).create_order(body)         POST /portfolio/events/orders
  ExecClient(env, live=True).order(order_id)            GET  /portfolio/orders/{id}
  ExecClient(env, live=True).orders(ticker=...)         GET  /portfolio/orders   (fallback status lookup / find by coid)
  ExecClient(env, live=True).cancel_order(oid, ei)      DELETE /portfolio/events/orders/{id}?exchange_index=N
  ExecClient(env, live=True).server_time_skew()         GET  /exchange/status (public, 3 probes)
Never used: cancel_all (it would kill manual orders), amend, decrease, batch calls.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
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
STOP_PATH = HERE / "STOP"

# ------------------------------------------------------------------ HARD CAPS (code constants, not CLI options)
SERIES = "KXNBATOTAL"
COUNT = 1
MIN_PRICE_C, MAX_PRICE_C = 15, 85           # limit price of the side bought, cents, inclusive
MAX_ORDERS_PER_GAME = 1
MAX_ORDERS_PER_NIGHT = 14
NIGHT_CAP_USD = Decimal("7.00")
HARD_END = datetime(2026, 11, 3, 23, 59, tzinfo=ET).astimezone(timezone.utc)   # compared as an absolute instant
OPEN_MIN, CLOSE_MIN, CANCEL_MIN = 75, 45, 15  # minutes before the pick-list tip time
DAY_START_ET = (11, 0)                      # each day's loop starts here (Sunday afternoon tips)
EMPTY_DAY_GIVE_UP_H = 6                     # no games listed this long after DAY_START_ET: the day is over
SCHEDULE_RELOAD = timedelta(minutes=30)
LOOP_S = 60
MAX_CANCEL_TRIES = 10
NOT_FOUND_AFTER = timedelta(minutes=10)     # after cancel_at + this, 3 clean listings without the id -> not_found
NOT_FOUND_MISSES = 3
MAX_SKEW_S = 2.0

OPEN_STATES = ("open", "unknown")          # unresolved: may be resting on the exchange
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
def est_fee(price_c: int, count: int = COUNT) -> Decimal:
    """Conservative fee estimate: the TAKER fee (0.07*C*P*(1-P) up to $0.000001) rounded to $0.0001 plus one step
    (units.worst_fee). Makers on this series pay less; over-estimating only makes the cap stricter."""
    return units.worst_fee(Decimal(count), Decimal(price_c) / 100)


def order_cost(o: dict) -> Decimal:
    """Money tied up / spent for one tonight's order, for the nightly cap."""
    p = Decimal(o["price_c"]) / 100
    if o["state"] == "failed":
        return Decimal(0)
    if o["state"] == "canceled":                       # only the filled part is spent
        f = Decimal(str(o.get("filled", 0)))
        return Decimal(0) if f == 0 else f * p + est_fee(o["price_c"], 1) * f
    return Decimal(o["count"]) * p + est_fee(o["price_c"], o["count"])   # open, unknown, filled, not_found


def check_caps(orders_tonight: list[dict], game: str, ticker: str, action: str, count: int, price_c: int,
               now: datetime) -> list[str]:
    """Return the list of violated caps (empty = OK). Pure."""
    bad = []
    if not str(ticker).startswith(SERIES + "-"):
        bad.append(f"series: {ticker} is not {SERIES}")
    if action != "buy":
        bad.append(f"action {action!r} is not buy")
    if count != COUNT or isinstance(count, bool):
        bad.append(f"count {count!r} != {COUNT}")
    if not (isinstance(price_c, int) and MIN_PRICE_C <= price_c <= MAX_PRICE_C):
        bad.append(f"price {price_c!r}c outside {MIN_PRICE_C}-{MAX_PRICE_C}c")
    if sum(1 for o in orders_tonight if o["game"] == game) >= MAX_ORDERS_PER_GAME:
        bad.append(f"already {MAX_ORDERS_PER_GAME} order for game {game} tonight")
    if len(orders_tonight) >= MAX_ORDERS_PER_NIGHT:
        bad.append(f"already {len(orders_tonight)} orders tonight (max {MAX_ORDERS_PER_NIGHT})")
    if isinstance(price_c, int) and 0 < price_c < 100:
        spent = sum((order_cost(o) for o in orders_tonight), Decimal(0))
        new = Decimal(count) * Decimal(price_c) / 100 + est_fee(price_c, count)
        if spent + new > NIGHT_CAP_USD:
            bad.append(f"nightly cap: {spent:.4f} + {new:.4f} > {NIGHT_CAP_USD}")
    if now > HARD_END:
        bad.append(f"after the end date {HARD_END.astimezone(ET):%Y-%m-%d %H:%M} ET")
    return bad


def build_body(ticker: str, outcome: str, price_c: int, exchange_index: int, coid: str, expires: datetime) -> dict:
    """CreateOrderV2 body exactly as approve.py, plus post_only, cancel_order_on_pause (docs/execution_design.md
    lines 390 and 551) and a GTC expiry at the cancel time (backstop)."""
    book_side, yes_price = units.to_book(outcome, Decimal(price_c) / 100)
    return {"ticker": ticker, "client_order_id": coid, "side": book_side,
            "count": units.fmt_count(Decimal(COUNT)), "price": units.fmt_price(yes_price),
            "time_in_force": "good_till_canceled", "post_only": True, "cancel_order_on_pause": True,
            "self_trade_prevention_type": "taker_at_cross",
            "expiration_time": int(expires.timestamp()), "exchange_index": exchange_index}


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


def new_order(day, game, ticker, outcome, price_c, coid, ei, tip, cancel_at, state="unknown") -> dict:
    return {"day": day, "game": game, "ticker": ticker, "outcome": outcome, "price_c": price_c, "count": COUNT,
            "coid": coid, "order_id": None, "state": state, "filled": 0, "exchange_index": ei, "tip": tip,
            "cancel_at": cancel_at, "cancel_tries": 0, "miss": 0}


def is_auth_error(e: BaseException) -> bool:
    return type(e).__name__ == "AuthError" or getattr(e, "status", None) in (401, 403)


# ------------------------------------------------------------------ the bot
class Bot:
    def __init__(self, *, client=None, live=False, compute=None, log_path=LOG_PATH, stop_path=STOP_PATH,
                 lock_path=LOCK_PATH, until=date(2026, 11, 3), force_first=False, echo=print, sleep=time.sleep,
                 clock=None):
        if live and client is None:
            raise ValueError("live mode needs a client")
        if live and force_first:
            raise ValueError("--force-first is dry-run only")
        self.client, self.live = client, live
        self._compute = compute
        self.log_path, self.stop_path, self.until = Path(log_path), Path(stop_path), until
        self.lock_path = Path(lock_path)
        self.force_first = force_first
        self.echo, self._sleep = echo, sleep
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.orders: dict[tuple[str, str], dict] = {}    # (ET day iso, game) -> order record
        self.sched: dict[str, dict] = {}                 # day iso -> {"games": [(tip, key, ms)], "loaded_at": dt}
        self.forced_done = False
        self.stopped = False
        self.halt_reason: str | None = None
        self.clock_ok = True                                # False after a failed day-start clock check
        self._lock = None

    # -------------------------------------------------- logging
    def log(self, event: str, line: str | None = None, **kw):
        rec = {"ts": self.clock().astimezone(timezone.utc).isoformat(timespec="seconds"), "event": event,
               "dry": not self.live, **kw}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str, ensure_ascii=True) + "\n")
        txt = line if line is not None else " ".join(f"{k}={v}" for k, v in kw.items())
        self.echo(f"{rec['ts'][11:19]}Z {event.upper():<14} {txt}".encode("ascii", "replace").decode())

    def note_error(self, e: BaseException, where: str):
        """Log an exception; the first 401/403 halts the bot after cancelling (m7)."""
        self.log("error", f"{where}: {type(e).__name__}", where=where, error=type(e).__name__)
        if is_auth_error(e) and self.halt_reason is None:
            self.halt_reason = f"auth error (401/403) in {where}"

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
            key = (r.get("day"), r.get("game"))
            if r.get("event") == "order_attempt":
                self.orders[key] = new_order(r["day"], r["game"], r["ticker"], r["outcome"], r["price_c"], r["coid"],
                                             r.get("exchange_index"), datetime.fromisoformat(r["tip"]),
                                             datetime.fromisoformat(r["cancel_at"]))
                n += 1
            elif key in self.orders and r.get("event") == "order_state":
                o = self.orders[key]
                o["state"] = r["state"]
                o["filled"] = r.get("filled", o["filled"])
                o["order_id"] = r.get("order_id") or o["order_id"]
        self.log("reconcile", f"restored {n} live order record(s) from the log", n=n)

    # -------------------------------------------------- schedule
    def _compute_rows(self, day, now, on_header=None):
        if self._compute is None:
            import early_overs_picks as eop
            return eop.compute_rows(day, now, on_header)
        return self._compute(day, now, on_header)

    def load_schedule(self, day: date, now: datetime):
        games = []

        def hdr(n27, g):
            games.extend(g)
            raise _Abort()
        try:
            self._compute_rows(day, now, hdr)
        except _Abort:
            pass
        games.sort(key=lambda g: (g[0], g[1]))
        self.sched[day.isoformat()] = {"games": games, "loaded_at": now}
        self.log("schedule", f"{day} {len(games)} game(s): " +
                 ", ".join(f"{k} tip {t.astimezone(ET):%H:%M}" for t, k, _ in games), day=day.isoformat(),
                 games=[k for _, k, _ in games])

    # -------------------------------------------------- order state refresh / fills
    def _tonight(self, day: str) -> list[dict]:
        return [o for (d, _), o in self.orders.items() if d == day]

    def _apply(self, o: dict, parsed: dict, source: str):
        """Update an order record from a parsed exchange view and log state changes / fills."""
        if parsed["state"] == "unknown" and parsed["api_status"] is None:
            return                                         # nothing usable: keep the old state (m11)
        prev_state, prev_filled = o["state"], Decimal(str(o["filled"]))
        o["state"], o["filled"] = parsed["state"], parsed["filled"]
        if parsed["filled"] != prev_filled:
            avg = None if parsed.get("avg_cost") is None else f"{parsed['avg_cost']:.4f}"
            self.log("fill", f"{o['game']} {o['outcome'].upper()} filled={parsed['filled']} avg_cost={avg} "
                             f"fees={parsed['fees']} (limit {o['price_c']}c)", day=o["day"], game=o["game"],
                     ticker=o["ticker"], order_id=o["order_id"], filled=str(parsed["filled"]), avg_cost=avg,
                     fees=str(parsed["fees"]), source=source)
        if o["state"] != prev_state or parsed["filled"] != prev_filled:
            self.log("order_state", f"{o['game']} {prev_state}->{o['state']} filled={parsed['filled']}",
                     day=o["day"], game=o["game"], order_id=o["order_id"], state=o["state"],
                     filled=str(parsed["filled"]), source=source)

    def _view(self, o: dict):
        """Current exchange view of one order, or None. Tries GET /portfolio/orders/{id}; if that raises or returns
        nothing, falls back to the ticker listing (works across shards) matching order_id or client_order_id.
        A successful listing without the order counts as a miss (-> not_found after cancel_at + 10 min, 3 misses)."""
        oid = o["order_id"]
        if oid:
            try:
                d = self.client.order(oid)
                if d:
                    o["miss"] = 0
                    return parse_order_obj(d)
            except Exception as e:  # noqa: BLE001
                self.note_error(e, f"order({o['game']})")
        try:
            kw = {"exchange_index": o["exchange_index"]} if isinstance(o["exchange_index"], int) and                 not isinstance(o["exchange_index"], bool) else {}
            lst = list(self.client.orders(ticker=o["ticker"], **kw))
        except Exception as e:  # noqa: BLE001
            self.note_error(e, f"orders({o['game']})")
            return None
        for x in lst:
            if (oid and x.get("order_id") == oid) or x.get("client_order_id") == o["coid"]:
                if not o["order_id"]:
                    o["order_id"] = x.get("order_id")
                o["miss"] = 0
                return parse_order_obj(x)
        o["miss"] += 1
        if o["state"] in OPEN_STATES and self.clock() >= o["cancel_at"] + NOT_FOUND_AFTER and \
                o["miss"] >= NOT_FOUND_MISSES:
            o["state"] = "not_found"                        # full cost stays counted; stops blocking day_done
            self.log("order_state", f"{o['game']} -> not_found after {o['miss']} listings without the order "
                                    f"(cost stays counted)", day=o["day"], game=o["game"], order_id=o["order_id"],
                     state="not_found", filled=str(o["filled"]), source="listing")
        return None

    def refresh(self):
        if not self.live:
            return
        for o in list(self.orders.values()):
            if o["state"] in OPEN_STATES:
                v = self._view(o)
                if v:
                    self._apply(o, v, "poll")

    # -------------------------------------------------- cancel
    def cancel_order(self, o: dict, reason: str) -> bool:
        if not self.live:
            self.log("cancel", f"DRY would cancel {o['game']} ({reason})", game=o["game"])
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
            self.log("cancel", f"{o['game']} {o['ticker']} oid={o['order_id']} reason={reason} outcome={res.outcome} "
                               f"status={res.status}", game=o["game"], order_id=o["order_id"], reason=reason,
                     outcome=res.outcome, http_status=res.status,
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
        """Tip-15: cancel unfilled / partly filled orders. Filled orders are never touched."""
        for o in list(self.orders.values()):
            if o["state"] in OPEN_STATES and now >= o["cancel_at"] and o["cancel_tries"] < MAX_CANCEL_TRIES:
                self.cancel_order(o, "tip-15")

    def cancel_all_open(self, reason: str):
        for o in list(self.orders.values()):
            if o["state"] in OPEN_STATES:
                self.cancel_order(o, reason)

    # -------------------------------------------------- placing
    def place_window(self, day: date, now: datetime):
        d = day.isoformat()
        games = self.sched.get(d, {}).get("games", [])
        todo = []
        for tip, key, ms in games:
            if (d, key) in self.orders:
                continue
            in_win = tip - timedelta(minutes=OPEN_MIN) <= now <= tip - timedelta(minutes=CLOSE_MIN)
            if in_win:
                todo.append((tip, key, ms, True))
        if self.force_first and not self.live and not self.forced_done and not todo:
            first = next(((t, k, m) for t, k, m in games if (d, k) not in self.orders), None)
            if first:
                todo.append((*first, False))
        if not todo:
            return
        if now > HARD_END:
            self.log("caps", f"no orders after {HARD_END.astimezone(ET):%Y-%m-%d %H:%M} ET; skipping "
                             f"{len(todo)} game(s)", day=d)
            return
        wanted = {k for _, k, _, _ in todo}

        def only_wanted(n27, g):                  # filter IN PLACE so compute_rows fetches books for these games only
            g[:] = [x for x in g if x[1] in wanted]
        rows, *_ = self._compute_rows(day, now, only_wanted)
        by_game = {r["game"]: r for r in rows}
        for tip, key, ms, in_win in todo:
            if self.halt_reason or self.stop_requested():
                return
            r = by_game.get(key)
            if r is None or r.get("side") is None or r.get("proj") is None:
                self.log("skip", f"{key} no valid pick/quote: {(r or {}).get('status', 'no row')}", game=key, day=d)
                continue
            if self.place_one(day, tip, key, ms, r, now, in_win) and self.force_first:
                self.forced_done = True

    def _skip(self, key, d, why):
        self.log("skip", f"{key} {why}", game=key, day=d)
        return False

    def place_one(self, day, tip, key, ms, r, now, in_win) -> bool:
        d = day.isoformat()
        if r.get("buy") not in ("YES", "NO") or SIDE_MAP.get(r.get("side")) != r.get("buy"):
            self.log("error", f"{key} side/buy mismatch side={r.get('side')} buy={r.get('buy')}; skipped", game=key)
            return False
        outcome = r["buy"].lower()
        price_c = int(round(r["limit"] * 100))
        bid_c, ask_c = int(round(r["yes_bid"] * 100)), int(round(r["yes_ask"] * 100))
        self.log("decision", f"{key} {r['side']} strike {r['strike']} proj {r['proj']:.2f} {r['ticker']} "
                             f"buy {outcome.upper()} @ {price_c}c (yes bid {bid_c} ask {ask_c})"
                             f"{'' if in_win else ' [FORCED outside window]'}",
                 day=d, game=key, side=r["side"], strike=r["strike"], ticker=r["ticker"], proj=r["proj"],
                 yes_bid=bid_c, yes_ask=ask_c, limit_c=price_c, outcome=outcome)
        # no-cross guard (also applied with post_only): the resting price must be strictly below the ask of that side
        side_bid, side_ask = (bid_c, ask_c) if outcome == "yes" else (100 - ask_c, 100 - bid_c)
        if not (side_bid < side_ask):
            return self._skip(key, d, f"spread < 1c ({side_bid}/{side_ask}); would cross")
        if price_c != side_bid:
            return self._skip(key, d, f"limit {price_c}c is not the side's best bid {side_bid}c")
        bad = check_caps(self._tonight(d), key, r["ticker"], "buy", COUNT, price_c, now)
        self.log("caps", f"{key} " + ("OK" if not bad else "BLOCKED: " + "; ".join(bad)), game=key, day=d,
                 ok=not bad, violations=bad)
        if bad:
            return False
        cancel_at = tip - timedelta(minutes=CANCEL_MIN)
        coid = f"eob-{d}-{key}"
        if not self.live:
            ei = next((m.get("exchange_index") for m in ms if m.get("ticker") == r["ticker"]), None)
            body = build_body(r["ticker"], outcome, price_c,
                              ei if isinstance(ei, int) and not isinstance(ei, bool) else 0, coid, cancel_at)
            from kpm.execution.client import validate_order_body
            validate_order_body(body)
            self.log("would_send", f"DRY RUN, NOT SENT: POST /portfolio/events/orders {json.dumps(body, sort_keys=True)}"
                     + ("" if isinstance(ei, int) else "  (exchange_index unknown in dry run: 0 shown; "
                                                       "live reads it from GET /markets/{t})")
                     + f"  cancel at {cancel_at.astimezone(ET):%H:%M} ET", game=key, body=body)
            self.orders[(d, key)] = new_order(d, key, r["ticker"], outcome, price_c, coid, ei, tip, cancel_at, "open")
            return True

        # ---------------- live-only pre-flight, all read-only
        try:
            m = self.client.market(r["ticker"])
        except Exception as e:  # noqa: BLE001
            self.note_error(e, f"market({key})")
            return False
        if m.get("status") != "active":
            return self._skip(key, d, f"market status {m.get('status')}")
        ei = m.get("exchange_index")
        if isinstance(ei, bool) or not isinstance(ei, int) or ei < 0:
            return self._skip(key, d, f"exchange_index missing/invalid ({ei!r}); never defaulting")
        ev = m.get("event_ticker")                          # M5 at EVENT level: any strike of this game
        if not isinstance(ev, str) or not ev.startswith(SERIES + "-"):
            return self._skip(key, d, f"event_ticker missing/invalid ({ev!r}); failing closed")
        try:                                                # no double exposure from ANY source
            pos = list(self.client.positions(event_ticker=ev, exchange_index=ei))
            rest = list(self.client.orders(event_ticker=ev, status="resting", exchange_index=ei))
        except Exception as e:  # noqa: BLE001
            self.note_error(e, f"exposure check({key})")
            return self._skip(key, d, "exposure check failed; failing closed")
        exposed = []
        for x in pos:
            t = x.get("ticker")
            if isinstance(t, str) and not t.startswith(ev + "-"):
                continue                                    # a position on another event: not ours to judge
            raw = x.get("position_fp", x.get("position"))
            try:
                nonzero = raw is None or t is None or Decimal(str(raw).strip()) != 0   # missing/unparsable = exposed
            except Exception:  # noqa: BLE001
                nonzero = True
            if nonzero:
                exposed.append(t or "?")
        if exposed or rest:
            return self._skip(key, d, f"already exposed on event {ev}: positions {exposed}, resting orders "
                                      f"{len(rest)}")
        # M3: fresh time, window, end date and a fresh book right before sending; no chasing
        fresh = self.clock()
        if not (tip - timedelta(minutes=OPEN_MIN) <= fresh <= tip - timedelta(minutes=CLOSE_MIN)):
            return self._skip(key, d, "window closed while preparing the order")
        bad = check_caps(self._tonight(d), key, r["ticker"], "buy", COUNT, price_c, fresh)
        if bad:
            self.log("caps", f"{key} BLOCKED at send time: " + "; ".join(bad), game=key, day=d, ok=False,
                     violations=bad)
            return False
        if self.stop_requested() or self.halt_reason:
            return self._skip(key, d, "stop/halt requested")
        try:
            book = self.client.orderbook(r["ticker"])
            fb = units.best_bid(book.get(outcome))
            other = units.best_bid(book.get("no" if outcome == "yes" else "yes"))
        except Exception as e:  # noqa: BLE001
            self.note_error(e, f"orderbook({key})")
            return self._skip(key, d, "fresh orderbook failed")
        if fb is None or fb != Decimal(price_c) / 100 or (other is not None and not (fb < 1 - other)):
            return self._skip(key, d, f"stale quote: best {outcome.upper()} bid now {fb} != planned "
                                      f"{Decimal(price_c) / 100} (or would cross); not chasing")
        body = build_body(r["ticker"], outcome, price_c, ei, coid, cancel_at)
        from kpm.execution.client import validate_order_body
        body = validate_order_body(body)
        o = new_order(d, key, r["ticker"], outcome, price_c, coid, ei, tip, cancel_at)
        self.orders[(d, key)] = o                         # recorded BEFORE sending: a crash can never double-place
        self.log("order_attempt", f"{key} SENDING {outcome.upper()} x{COUNT} @ {price_c}c coid={coid}", day=d,
                 game=key, ticker=r["ticker"], outcome=outcome, price_c=price_c, count=COUNT, coid=coid,
                 exchange_index=ei, tip=tip.isoformat(), cancel_at=cancel_at.isoformat(), body=body)
        try:
            res = self.client.create_order(body)
        except Exception as e:  # noqa: BLE001
            self.note_error(e, f"create_order({key}) (state unknown, will reconcile)")
            return True
        data = res.data if isinstance(res.data, dict) else {}
        self.log("order_sent", f"{key} outcome={res.outcome} http={res.status} order_id={res.order_id} "
                               f"fill={data.get('fill_count')} remaining={data.get('remaining_count')}",
                 day=d, game=key, ok=res.ok, outcome=res.outcome, http_status=res.status, order_id=res.order_id,
                 data=data or None, error=res.error)
        if res.ok:
            o["order_id"] = res.order_id
            fill, rem = _dec(data.get("fill_count", 0)), _dec(data.get("remaining_count", 0))
            st = "filled" if fill >= COUNT else ("open" if rem > 0 else "canceled")
            self._apply(o, {"state": st, "filled": fill, "remaining": rem,
                            "fees": _dec(data.get("average_fee_paid", 0)) * fill, "avg_cost": None,
                            "api_status": "created"}, "create")
        elif res.outcome in ("rejected", "auth", "throttled"):   # definitely not placed (4xx / repeated 429)
            o["state"] = "failed"
            self.log("order_state", f"{key} -> failed ({res.outcome})", day=d, game=key, order_id=None,
                     state="failed", filled="0", source="create")
            if res.outcome == "auth":
                self.halt_reason = "auth error (401/403) on create"
        else:                                               # unknown or 409 duplicate: may exist; cost stays counted
            self.log("order_state", f"{key} -> unknown ({res.outcome}); will resolve by client_order_id and cancel "
                                    f"on schedule", day=d, game=key, order_id=None, state="unknown", filled="0",
                     source="create")
            v = self._view(o)
            if v:
                self._apply(o, v, "resolve")
        return True

    # -------------------------------------------------- one tick / loops
    def stop_requested(self) -> bool:
        return self.stop_path.exists()

    def tick(self, day: date, now: datetime):
        d = day.isoformat()
        s = self.sched.get(d)
        self.refresh()                                      # cancels and fills come first, whatever else fails
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
        self.log("start", f"mode={'LIVE' if self.live else 'DRY-RUN'} until={self.until} force_first={self.force_first} "
                          f"caps: series={SERIES} count={COUNT} price={MIN_PRICE_C}-{MAX_PRICE_C}c "
                          f"night_orders<={MAX_ORDERS_PER_NIGHT} night_cap=${NIGHT_CAP_USD} "
                          f"end={HARD_END.astimezone(ET):%Y-%m-%d %H:%M}ET",
                 live=self.live, until=str(self.until), force_first=self.force_first, series=SERIES, count=COUNT,
                 min_price_c=MIN_PRICE_C, max_price_c=MAX_PRICE_C, max_orders_night=MAX_ORDERS_PER_NIGHT,
                 night_cap_usd=str(NIGHT_CAP_USD), hard_end=HARD_END.isoformat(), post_only=True,
                 tif="good_till_canceled")
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
