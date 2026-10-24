"""Backtest simulator for research log #22 (reports/output/nfl_ws/PREREGISTRATION.md): thin NFL markets vs deep twins.
Generalized to NBA win-margin buckets for log #23 (reports/output/nba_ws/PREREGISTRATION.md) through ``Sport``.

One time-ordered pass over the raw WebSocket recordings (``ws_books``). Full-depth books are rebuilt; every top-of-book
change of a thin market or of one of its fair-value inputs recomputes the fair value and evaluates signals, exits and
maker quotes. Decisions use only book states at or before the decision time. Orders arrive ``L`` later and are filled
from the book as of the last message at or before the arrival time. Pending work (arrivals, markouts, maker placements)
sits in a heap and runs when the stream time passes it.

Prices inside are floats in dollars; book levels are integer 1/10000-dollar ticks (as in ws_books).
"""
from __future__ import annotations

import heapq
import json
import math
import re
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Iterable, Iterator

from kpm.analysis import ws_sim_stats as st
from kpm.analysis.ws_books import BookSet, Top, Trade, raw_files, read_lines

NS = 1_000_000_000
MIN_NS = 60 * NS
EPS = 1e-9
MAX_SPREAD = 0.03
TWIN_WINDOW_NS = 30 * NS
PRIMARY = "A-T-th1-L1-X2"
MAX_COUNT, DOLLARS_PER_ORDER = 10, 1.0
# a bucket suffix is <TEAM><lo>TO<hi> or <TEAM><lo>PLUS (NFL AAA7TO14, AAA15PLUS; NBA LAL1TO5, LAL21PLUS)
BUCKET_RE = re.compile(r"KXNFLWINMARGIN-[^-]+-(?P<team>[A-Z]+?)(?P<lo>\d+)(?:TO(?P<hi>\d+)|(?P<plus>PLUS))")
SPREAD_RE = re.compile(r"KXNFLSPREAD-[^-]+-(?P<team>[A-Z]+?)\d+")
# the bucket range as written in a market title (yes_sub_title): "by 1 to 6", "by 7 to 14", "by 15 or more"
TITLE_RE = re.compile(r"\bby\s+(?P<lo>\d+)\s*(?:(?:to|-|\u2013)\s*(?P<hi>\d+)|(?P<plus>or\s+more|\+|plus))", re.I)
MAKER_CAVEAT = ("Maker versions are exploratory, can never pass, and are optimistic: resting asks that would have crossed "
                "our bid leave no trade print, and an order that would cross at go-live is dropped instead of filled "
                "as a taker.")


@dataclass(frozen=True)
class Sport:
    """What differs between the studies: series names, tie market, version grid, gating and the weekly game count."""
    name: str
    prefix: str
    tie: bool                      # NFL has a tie market and a tie-adjusted win leg; NBA games cannot tie
    protected: bool                # Family B (protected props) exists
    thetas: tuple
    min_games: int
    min_unseen: int
    games_per_week: int

    def __post_init__(self):
        p = self.prefix
        object.__setattr__(self, "bucket_re", re.compile(BUCKET_RE.pattern.replace("KXNFL", p)))
        object.__setattr__(self, "spread_re", re.compile(SPREAD_RE.pattern.replace("KXNFL", p)))


SPORTS = {
    "nfl": Sport("nfl", "KXNFL", True, True, (0.01, 0.02, 0.03), st.MIN_GAMES, st.MIN_UNSEEN, st.GAMES_PER_WEEK),
    "nba": Sport("nba", "KXNBA", False, False, (0.01, 0.02), 20, 10, 50),
}


# ------------------------------------------------------------------------------------------------ prices and fees
def taker_fee(price: float, count: int) -> float:
    """ceil(0.07 * C * P * (1-P), to $0.0001); the epsilon stops float noise (0.175 -> 1750.0000000000002) rounding up."""
    return round(math.ceil(0.07 * count * price * (1 - price) / 0.0001 - 1e-6) * 0.0001, 4)


def fee1(price: float) -> float:
    return taker_fee(price, 1)


def valid_mid(bid: float | None, ask: float | None, longshot: bool = True) -> float | None:
    """Mid of a usable input book: both sides and a spread of 3c or less; a long shot with no bid and an ask of 3c or
    less counts as ask/2 (not for Family B twins: ``longshot=False``). Anything else (one-sided, wide, crossed) is unusable."""
    if ask is None:
        return None
    if bid is None:
        return ask / 2 if longshot and ask <= MAX_SPREAD + EPS else None
    return (bid + ask) / 2 if 0 <= ask - bid <= MAX_SPREAD + EPS else None


def _iso_ns(text) -> int | None:
    if not text:
        return None
    dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    return int((dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp() * NS)


# ------------------------------------------------------------------------------------------------ versions
@dataclass(frozen=True)
class Version:
    vid: str
    family: str                    # A (win-margin buckets) | B (protected props)
    kind: str                      # T taker | M maker
    theta: float = 0.0
    latency: float = 1.0
    exit: str = "X2"
    delta: float = 0.0

    @property
    def lat_ns(self) -> int:
        return int(round(self.latency * NS))


def all_versions(sport: Sport = SPORTS["nfl"]) -> list[Version]:
    out = []
    for fam in "AB" if sport.protected else "A":
        for th in sport.thetas if fam == "A" else (0.01, 0.02):           # NFL: 3c added for Family A (amendment 06:05Z)
            for lat in (0.25, 1.0, 5.0):
                for ex in ("X1", "X2"):
                    out.append(Version(f"{fam}-T-th{round(th * 100)}-L{lat:g}-{ex}", fam, "T", theta=th, latency=lat, exit=ex))
    for d in (0.01, 0.02):
        for lat in (0.25, 1.0):
            out.append(Version(f"A-M-d{round(d * 100)}-L{lat:g}", "A", "M", latency=lat, exit="X2", delta=d))
    return out


# ------------------------------------------------------------------------------------------------ universe
@dataclass(frozen=True)
class Target:
    ticker: str
    game: str
    family: str
    legs: tuple = ()               # A: (lo_leg, hi_leg or None); leg = ("win", game_ticker, tie_ticker) | ("sp", ticker)
    twin: str | None = None        # B: the regular market


class Universe:
    """Which markets exist, which are traded (targets), and what each target's fair value reads."""

    def __init__(self, docs: list[dict], sport: Sport = SPORTS["nfl"], check_titles: bool = True):
        self.sport = sport
        self.check_titles = check_titles                       # off only for the historical test (no titles in its data)
        self.game_of: dict[str, str] = {}
        self.kickoff: dict[str, int | None] = {}
        self.targets: dict[str, Target] = {}
        self.deps: dict[str, list[Target]] = defaultdict(list)
        self.skipped: Counter = Counter()
        for doc in docs:
            for g, info in doc["games"].items():
                self._add_game(g, info)

    def _add_game(self, g: str, info: dict) -> None:
        tickers, meta = info["tickers"], info.get("meta") or {}
        have, sp = set(tickers), self.sport
        # other sports have no kickoff rule: order and pregame/in-game use occurrence_datetime as recorded (kickoff_raw)
        self.kickoff[g] = _iso_ns(info.get("kickoff") or (info.get("kickoff_raw") or {}).get("occurrence_datetime"))
        win, tie, spread = {}, None, {}
        for t in tickers:
            self.game_of[t] = g
            series = t.split("-", 1)[0]
            if series == sp.prefix + "GAME":
                win[t.rsplit("-", 1)[1]] = t
            elif series == sp.prefix + "TIE" and sp.tie:
                tie = t
            elif series == sp.prefix + "SPREAD":
                m, strike = sp.spread_re.fullmatch(t), (meta.get(t) or {}).get("floor_strike")
                if not m or strike is None:
                    self.skipped["spread_without_strike"] += 1      # strike convention (n vs n-0.5) is not guessed
                else:
                    spread[(m["team"], float(strike))] = t
        for t in tickers:
            series = t.split("-", 1)[0]
            if series == sp.prefix + "WINMARGIN":
                m = sp.bucket_re.fullmatch(t)
                if m:
                    self._add_bucket(g, t, m, win, tie, spread, meta)
            elif sp.protected and series.endswith("PROT"):
                twin = series[:-4] + t[len(series):]
                if twin in have:
                    self._add(Target(t, g, "B", twin=twin), [twin])
                else:
                    self.skipped["B_no_twin"] += 1

    def _add_bucket(self, g, t, m, win, tie, spread, meta) -> None:
        team, lo = m["team"], int(m["lo"])
        hi = int(m["hi"]) if m["hi"] else None

        def leg(thr: float):                                       # P(team wins by more than thr)
            if thr == 0.5:
                return ("win", win[team], tie) if team in win and (tie or not self.sport.tie) else None   # NBA: no tie leg
            return ("sp", spread[(team, thr)]) if (team, thr) in spread else None

        lo_leg = leg(lo - 0.5)
        hi_leg = leg(hi + 0.5) if hi is not None else None
        if lo_leg is None or (hi is not None and hi_leg is None):
            self.skipped["A_missing_input"] += 1                    # spec: skip a bucket whose strike is not listed
            return
        if self.check_titles and not self._title_agrees(t, lo, hi, meta):
            return
        legs = (lo_leg, hi_leg)
        inputs = [x for lg in legs if lg for x in lg[1:] if x]
        self._add(Target(t, g, "A", legs=legs), inputs)

    def _title_agrees(self, t: str, lo: int, hi: int | None, meta: dict) -> bool:
        """The a..b range read from the ticker must equal the one in the market's title (yes_sub_title); else skip and count."""
        title = (meta.get(t) or {}).get("yes_sub_title")
        if not title:
            self.skipped["A_bucket_no_title"] += 1
            return False
        m = TITLE_RE.search(str(title))
        if m and (int(m["lo"]), int(m["hi"]) if m["hi"] else None) == (lo, hi):
            return True
        self.skipped["A_bucket_title_mismatch"] += 1                # unparseable counts as a mismatch
        return False

    def _add(self, tg: Target, inputs: list[str]) -> None:
        self.targets[tg.ticker] = tg
        for t in {tg.ticker, *inputs}:
            self.deps[t].append(tg)


# ------------------------------------------------------------------------------------------------ simulator state
@dataclass
class Order:
    version: Version
    target: Target
    side: str
    limit: float                   # own-side price
    dec_ns: int
    fair: float | None
    quoted: float                  # size at the limit when decided
    forced: bool = False


@dataclass(eq=False)
class Pos:
    version: Version
    target: Target
    side: str
    count: int
    price: float
    fee_in: float
    dec_ns: int
    fill_ns: int
    fair_dec: float | None
    fill_check: bool | None
    exit_pending: bool = False
    exit_kind: str | None = None     # x2 | settle | unsettled
    exit_ns: int | None = None
    exit_px: float | None = None
    fee_out: float = 0.0
    mo: dict = field(default_factory=dict)


@dataclass(eq=False)
class MakerOrder:
    version: Version
    target: Target
    price: float
    ticks: int
    size: int
    live_ns: int
    fair_ref: float
    cancel_ns: int | None = None
    live: bool = False
    dead: bool = False
    ahead: float = 0.0
    vol: float = 0.0


class Sim:
    def __init__(self, universe: Universe, versions: Iterable[Version], markouts: bool = True):
        self.u = universe
        self.versions = list(versions)
        self.takers = {f: [v for v in self.versions if v.kind == "T" and v.family == f] for f in "AB"}
        self.makers = [v for v in self.versions if v.kind == "M"]
        self.markouts = markouts
        self.sets: dict[int, BookSet] = defaultdict(BookSet)   # one book set per raw dir (its own sid/seq namespace)
        self.owner: dict[str, int] = {}                        # ticker -> stream it was first seen in
        self.two_streams: set[str] = set()                     # tickers whose lines appear in two streams
        self._cur = 0                                          # stream of the line being fed
        self.tops: dict[str, Top] = {}
        self.bad_min: dict[str, int] = {}                      # market -> last minute its book was untrusted
        self.hist: dict[str, deque] = {tg.twin: deque() for tg in universe.targets.values() if tg.twin}
        self.heap: list = []
        self._seq = 0
        self.active: set = set()                               # (vid, ticker, side): pending entry or open position
        self.open_pos: dict[tuple, list[Pos]] = defaultdict(list)
        self.all_pos: list[Pos] = []
        self.orders, self.fills = Counter(), Counter()
        self.mk: dict[tuple, MakerOrder] = {}                  # (vid, ticker) -> current maker order
        self.mk_all: dict[str, list[MakerOrder]] = defaultdict(list)
        self.recent: dict[str, deque] = defaultdict(deque)     # ticker -> (ns, yes_price, taker_side) of recent public trades
        self.spent: dict[tuple, tuple] = {}                    # (vid, ticker, side) -> best own-side level (price, size) when we filled
        self.window: dict[str, list[int]] = {}                 # target -> [first, last] top-change time
        self.game_first: dict[str, int] = {}
        self.gap_open: dict[tuple, int] = {}
        self.gap_life: list[tuple[str, float]] = []            # (game, seconds) at theta = 1c
        self.lines = 0
        self.closed = False                                    # set by finish(): the recording has ended
        self.out_of_order = 0                                  # lines received earlier than one already read in the same stream
        self._max_ns: dict[int, int] = {}

    # ---- plumbing
    def _push(self, ns: int, kind: str, payload) -> None:
        self._seq += 1
        heapq.heappush(self.heap, (ns, self._seq, kind, payload))

    def _flush(self, upto: float) -> None:
        while self.heap and self.heap[0][0] < upto:
            ns, _, kind, payload = heapq.heappop(self.heap)
            getattr(self, "_do_" + kind)(payload, ns)

    def bs(self, t: str) -> BookSet:
        """The book set of the stream that owns ticker t."""
        return self.sets[self.owner.get(t, 0)]

    def _claim(self, t: str) -> bool:
        """A ticker belongs to the stream it is first seen in; lines from a second stream are ignored and counted."""
        if t not in self.u.game_of:
            return True
        if self.owner.setdefault(t, self._cur) != self._cur:
            self.two_streams.add(t)
            return False
        return True

    def _feed(self, line: dict, ns: int) -> None:
        bs = self.sets[self._cur]
        for ev in bs.feed(line):
            if not self._claim(ev.ticker):
                continue
            if isinstance(ev, Top):
                self._on_top(ev)
            else:
                self._on_trade(ev)
        if bs.dropped:
            self._on_dropped(ns, bs)

    def run(self, lines: Iterable[dict]) -> None:
        """Lines carry an optional ``stream`` index (one per raw dir). Out-of-order is counted within a stream only, and a
        control event kills only its own stream's books. ``synthetic`` lines (end-of-stream stop) are not counted."""
        for line in lines:
            ns = line["recv_ns"]
            self._cur = line.get("stream", 0)
            if not line.get("synthetic"):
                self.lines += 1
                self.out_of_order += ns < self._max_ns.get(self._cur, -1)   # e.g. two recorder processes overlapping in time
                self._max_ns[self._cur] = max(self._max_ns.get(self._cur, -1), ns)
            connect = (line.get("ctl") or {}).get("event") == "connect"
            if connect:
                self._feed(line, ns)                                # the gap before a (re)connect is an outage too
            self._flush(ns)                                         # work before this line sees the books as they were
            if not connect:
                self._feed(line, ns)                                # a disconnect/stop kills the books for work at or after it
        self.finish()

    def finish(self) -> None:
        """Nothing is recorded after the last line: pending arrivals, exits and coin-flip decisions find no valid book."""
        self.closed = True
        self._flush(math.inf)

    def add_forced(self, plan: list[tuple[int, str, str, int]], versions: dict[int, Version]) -> None:
        for copy, ticker, side, ns in plan:
            self._push(ns, "forced", (versions[copy], ticker, side))

    # ---- events
    def _on_dropped(self, ns: int, bs: BookSet) -> None:
        for t in bs.dropped:
            self.bad_min[t] = ns // MIN_NS
            if t in self.hist:
                self.hist[t].append((ns, None))
            self._end_gaps(t, ns)
        bs.dropped.clear()

    def _on_top(self, top: Top) -> None:
        t, ns = top.ticker, top.recv_ns
        g = self.u.game_of.get(t)
        if g is None:
            return
        self.game_first.setdefault(g, ns)
        if t not in self.u.deps:
            return
        self.tops[t] = top
        twin = t in self.hist                                  # Family B twins get no long-shot ask/2 rule
        if valid_mid(top.best_yes_bid, top.best_yes_ask, not twin) is None:
            self.bad_min[t] = ns // MIN_NS                     # an invalid input book excludes the rest of its minute
        if t in self.u.targets:
            self._release(t, top)
        if t in self.u.targets:
            w = self.window.setdefault(t, [ns, ns])
            w[1] = ns
        if t in self.hist:
            h = self.hist[t]
            h.append((ns, valid_mid(top.best_yes_bid, top.best_yes_ask, longshot=False)))
            while len(h) >= 2 and h[1][0] <= ns - TWIN_WINDOW_NS:
                h.popleft()
        for tg in self.u.deps[t]:
            self._evaluate(tg, ns)

    def _on_trade(self, tr: Trade) -> None:
        if tr.ticker not in self.u.targets:
            return
        dq = self.recent[tr.ticker]
        dq.append((tr.recv_ns, tr.yes_price, tr.taker_side))
        while dq and dq[0][0] < tr.recv_ns - 10 * NS:
            dq.popleft()
        for o in list(self.mk_all.get(tr.ticker, ())):
            if o.dead or (o.cancel_ns is not None and tr.recv_ns >= o.cancel_ns):
                self.mk_all[tr.ticker].remove(o)
            elif o.live and tr.taker_side == "no" and tr.yes_price <= o.price + EPS:
                o.vol += tr.count                                 # sells hit our price or through it, after we were live
                n = min(o.size, int(o.vol - o.ahead + EPS))
                if n >= 1 and (o.version.vid, o.target.ticker, "yes") not in self.active:
                    self._maker_fill(o, n, tr.recv_ns)

    def _live(self, t: str) -> bool:
        return not self.closed and t in self.bs(t).sid_of

    def _release(self, t: str, top: Top) -> None:
        """Recorded liquidity is not reused: after our fill the side stays blocked until its best level changes."""
        for key in [k for k in self.spent if k[1] == t]:
            if self._level(top, key[2]) != self.spent[key]:
                del self.spent[key]

    @staticmethod
    def _level(top: Top, side: str) -> tuple:
        """Best level we would buy from on this side: (own-side ask price, size)."""
        return (top.best_yes_ask, top.ask_size) if side == "yes" else (
            None if top.best_yes_bid is None else round(1 - top.best_yes_bid, 4), top.bid_size)

    # ---- fair values
    def _mid(self, t: str, ns: int) -> float | None:
        if t not in self.bs(t).sid_of or self.bad_min.get(t) == ns // MIN_NS:
            return None
        top = self.tops.get(t)
        return None if top is None else valid_mid(top.best_yes_bid, top.best_yes_ask)

    def _p_over(self, leg, ns: int) -> float | None:
        if leg[0] == "sp":
            return self._mid(leg[1], ns)
        win, tie = self._mid(leg[1], ns), 0.0 if leg[2] is None else self._mid(leg[2], ns)    # NBA: no tie leg
        return None if win is None or tie is None else win - tie / 2     # tie-adjusted win probability

    def fair(self, tg: Target, ns: int) -> float | None:
        if tg.family == "B":
            return self._fair_b(tg, ns)
        lo = self._p_over(tg.legs[0], ns)
        hi = 0.0 if tg.legs[1] is None else self._p_over(tg.legs[1], ns)
        if lo is None or hi is None or not -EPS <= lo - hi <= 1 + EPS:
            return None                                             # missing input or inconsistent inputs
        return min(max(lo - hi, 0.0), 1.0)

    def _fair_b(self, tg: Target, ns: int) -> float | None:
        h = self.hist[tg.twin]
        w = ns - TWIN_WINDOW_NS
        if tg.twin not in self.bs(tg.twin).sid_of or self.bad_min.get(tg.twin) == ns // MIN_NS or not h or h[0][0] > w:
            return None                                             # not 30 s of twin history yet
        samples = [m for ts, m in h if ts > w] + [next(m for ts, m in reversed(h) if ts <= w)]
        return None if any(m is None for m in samples) else median(samples)

    # ---- book access in the order's own side space
    def _bids(self, t: str, side: str, floor_ticks: int) -> list[tuple[int, float]]:
        bs = self.bs(t)
        book = bs.yes[t] if side == "yes" else bs.no[t]
        return sorted(((p, s) for p, s in book.items() if p >= floor_ticks), reverse=True)

    def _asks(self, t: str, side: str, cap_ticks: int) -> list[tuple[int, float]]:
        bs = self.bs(t)
        other = bs.no[t] if side == "yes" else bs.yes[t]
        return sorted((10000 - p, s) for p, s in other.items() if 10000 - p <= cap_ticks)

    @staticmethod
    def _walk(levels: list[tuple[int, float]], n: int) -> tuple[int, int]:
        got = cost = 0
        for p, s in levels:
            take = min(n - got, int(s + EPS))
            got, cost = got + take, cost + take * p
            if got == n:
                break
        return got, cost

    def _own_bid(self, top: Top, side: str) -> float | None:
        return top.best_yes_bid if side == "yes" else (None if top.best_yes_ask is None else round(1 - top.best_yes_ask, 4))

    # ---- evaluation
    def _evaluate(self, tg: Target, ns: int) -> None:
        t = tg.ticker
        top = self.tops.get(t)
        fair = self.fair(tg, ns) if t in self.bs(t).sid_of and top is not None else None
        if fair is None:
            self._end_gaps(t, ns)
            return
        ask, bid = top.best_yes_ask, top.best_yes_bid
        ev = {"yes": None if ask is None else fair - ask - fee1(ask),
              "no": None if bid is None or tg.family == "B" else bid - fair - fee1(round(1 - bid, 4))}
        for side, e in ev.items():
            self._track_gap(tg, side, e is not None and e >= 0.01 - EPS, ns)
        limits = {"yes": (ask, top.ask_size), "no": (None if bid is None else round(1 - bid, 4), top.bid_size)}
        for v in self.takers[tg.family]:
            for side, e in ev.items():
                if e is None or e < v.theta - EPS or (v.vid, t, side) in self.active or (v.vid, t, side) in self.spent:
                    continue
                self._send_entry(Order(v, tg, side, limits[side][0], ns, fair, limits[side][1]), ns)
        if tg.family == "A":
            for v in self.makers:
                self._maker_step(v, tg, fair, top, ns)
        self._check_exit(tg, fair, top, ns)

    def _send_entry(self, o: Order, ns: int) -> None:
        self.active.add((o.version.vid, o.target.ticker, o.side))
        self.orders[o.version.vid] += 1
        self._push(ns + o.version.lat_ns, "entry", o)

    def _track_gap(self, tg: Target, side: str, on: bool, ns: int) -> None:
        k = (tg.ticker, side)
        if on and k not in self.gap_open:
            self.gap_open[k] = ns
        elif not on and k in self.gap_open:
            self.gap_life.append((tg.game, (ns - self.gap_open.pop(k)) / NS))

    def _end_gaps(self, t: str, ns: int) -> None:
        for side in ("yes", "no"):
            if (t, side) in self.gap_open:
                self.gap_life.append((self.u.game_of[t], (ns - self.gap_open.pop((t, side))) / NS))

    # ---- taker entries
    def _do_forced(self, item, ns: int) -> None:                    # coin-flip decision: no EV test
        v, t, side = item
        top = self.tops.get(t)
        if top is None or not self._live(t) or (v.vid, t, side) in self.active:
            return
        limit, quoted = self._level(top, side)
        if limit is not None:
            self._send_entry(Order(v, self.u.targets[t], side, limit, ns, None, quoted, forced=True), ns)

    def _do_entry(self, o: Order, ns: int) -> None:
        t, v = o.target.ticker, o.version
        key = (v.vid, t, o.side)
        lim = round(o.limit * 10000)
        levels = self._asks(t, o.side, lim) if self._live(t) else []
        avail = sum(int(s + EPS) for _, s in levels)
        count = min(MAX_COUNT, avail, int(DOLLARS_PER_ORDER / o.limit + EPS))
        if count < 1:
            self.active.discard(key)                                # phantom: the quote is gone at arrival
            return
        got, cost = self._walk(levels, count)
        price = cost / got / 10000
        # a public trade confirms the fill only if its taker traded our way (bought YES to match a YES buy, bought NO to
        # match a NO buy), at our price or better, inside [decision, arrival]; the other side of the spread proves nothing
        trade_ok = any(o.dec_ns <= ts <= ns and taker == o.side and ((p <= levels[0][0] / 10000 + EPS) if o.side == "yes"
                                                                       else (1 - p <= levels[0][0] / 10000 + EPS))
                       for ts, p, taker in self.recent[t])
        check = trade_ok or min(o.quoted, avail) >= 5
        pos = Pos(v, o.target, o.side, got, price, taker_fee(price, got), o.dec_ns, ns, o.fair, check)
        top = self.tops.get(t)
        if top is not None:
            self.spent[key] = self._level(top, o.side)              # no re-entry until this level changes
        self._open(pos, ns)

    def _open(self, pos: Pos, ns: int) -> None:
        self.fills[pos.version.vid] += 1
        self.all_pos.append(pos)
        self.open_pos[(pos.target.ticker, pos.side)].append(pos)
        if self.markouts:
            for h in st.MARKOUT_S:
                self._push(ns + h * NS, "mo", (pos, h))
        top = self.tops.get(pos.target.ticker)
        fair = self.fair(pos.target, ns) if pos.target.ticker in self.bs(pos.target.ticker).sid_of else None
        if top is not None and fair is not None:
            self._check_exit(pos.target, fair, top, ns)

    # ---- exits (X2)
    def _check_exit(self, tg: Target, fair: float, top: Top, ns: int) -> None:
        for side in ("yes", "no"):
            book = self.open_pos.get((tg.ticker, side))
            pb = self._own_bid(top, side) if book else None
            if pb is None or pb - fee1(pb) < (fair if side == "yes" else 1 - fair) - 0.01 - EPS:
                continue
            for p in book:
                if p.version.exit == "X2" and not p.exit_pending:
                    p.exit_pending = True
                    self._push(ns + p.version.lat_ns, "exit", (p, pb))

    def _do_exit(self, item, ns: int) -> None:
        p, limit = item
        p.exit_pending = False
        t = p.target.ticker
        if p.exit_kind or not self._live(t):
            return                                                  # no valid book (outage or end of recording): stays held
        levels = self._bids(t, p.side, round(limit * 10000))
        if sum(int(s + EPS) for _, s in levels) < p.count:
            return                                                  # the bid is gone (or too thin) at arrival
        _, proceeds = self._walk(levels, p.count)
        px = proceeds / p.count / 10000
        self._close(p, "x2", ns, px, taker_fee(px, p.count))

    def _close(self, p: Pos, kind: str, ns: int | None, px: float | None, fee: float) -> None:
        p.exit_kind, p.exit_ns, p.exit_px, p.fee_out = kind, ns, px, fee
        self.open_pos[(p.target.ticker, p.side)].remove(p)
        self.active.discard((p.version.vid, p.target.ticker, p.side))

    # ---- markouts
    def _do_mo(self, item, ns: int) -> None:
        p, h = item
        t = p.target.ticker
        top = self.tops.get(t)
        flip = p.side == "no"
        mid = None
        if top is not None and self._live(t) and top.best_yes_bid is not None and top.best_yes_ask is not None:
            mid = (top.best_yes_bid + top.best_yes_ask) / 2
        fair = self.fair(p.target, ns) if self._live(t) else None
        side_px = lambda x: None if x is None else round((1 - x if flip else x) - p.price, 6)
        p.mo[h] = (side_px(mid), side_px(fair))

    # ---- maker (family A, YES bids)
    def _maker_step(self, v: Version, tg: Target, fair: float, top: Top, ns: int) -> None:
        key = (v.vid, tg.ticker)
        cur = self.mk.get(key)
        if cur and cur.dead:
            cur = self.mk.pop(key)
            cur = None
        if (v.vid, tg.ticker, "yes") in self.active or (cur and abs(fair - cur.fair_ref) < 0.01 - EPS):
            return
        if cur:
            cur.cancel_ns = ns + v.lat_ns                           # still fillable until the cancel takes effect
            del self.mk[key]
        q = math.floor((fair - v.delta) * 100 + EPS) / 100
        if top.best_yes_bid is None or top.best_yes_ask is None or not top.best_yes_bid + EPS < q < top.best_yes_ask - EPS:
            return                                                  # only quote inside the spread
        o = MakerOrder(v, tg, q, round(q * 10000), min(MAX_COUNT, int(DOLLARS_PER_ORDER / q + EPS)), ns + v.lat_ns, fair)
        self.mk[key] = o
        self.orders[v.vid] += 1
        self._push(o.live_ns, "mk_live", o)

    def _do_mk_live(self, o: MakerOrder, ns: int) -> None:
        t = o.target.ticker
        top = self.tops.get(t)
        if o.dead or not self._live(t) or top is None or top.best_yes_ask is None or o.price >= top.best_yes_ask - EPS:
            o.dead = True                                           # would have crossed: not modelled, dropped
            return
        o.ahead = self.bs(t).yes[t].get(o.ticks, 0.0)               # back of the queue at our price
        o.live = True
        self.mk_all[t].append(o)

    def _maker_fill(self, o: MakerOrder, n: int, ns: int) -> None:
        v, t = o.version, o.target.ticker
        for x in self.mk_all[t]:
            if x.version is v:
                x.dead = True                                       # no adding: every order of this version stops
        self.mk.pop((v.vid, t), None)
        self.active.add((v.vid, t, "yes"))
        self._open(Pos(v, o.target, "yes", n, o.price, 0.0, o.live_ns, ns, o.fair_ref, None), ns)

    # ---- settlement and output
    def open_tickers(self) -> set[str]:
        return {p.target.ticker for p in self.all_pos if p.exit_kind is None}

    def traded_tickers(self) -> set[str]:
        return {p.target.ticker for p in self.all_pos}

    def settle(self, results: dict[str, str]) -> None:
        """Positions still open take the public market result. A market with no result is dropped and reported as a
        whole, X2 exits included: dropping only the held positions would keep the converged trades and lose the rest."""
        for p in self.all_pos:
            r = results.get(p.target.ticker)
            if p.exit_kind is None:
                if r in ("yes", "no"):
                    self._close(p, "settle", None, 1.0 if r == p.side else 0.0, 0.0)
                else:
                    self._close(p, "unsettled", None, None, 0.0)
            elif r not in ("yes", "no"):
                p.exit_kind = "unsettled"

    def trade_rows(self, kickoff: dict[str, int | None] | None = None) -> list[dict]:
        rows = []
        for p in self.all_pos:
            if p.exit_kind not in ("x2", "settle"):
                continue
            gross = (p.exit_px - p.price) * p.count
            k = (kickoff or {}).get(p.target.game)
            r = {"version": p.version.vid, "game": p.target.game, "ticker": p.target.ticker, "family": p.target.family,
                 "kind": p.version.kind, "side": p.side, "count": p.count, "dec_ns": p.dec_ns, "fill_ns": p.fill_ns,
                 "entry_px": round(p.price, 6), "exit_kind": p.exit_kind, "exit_ns": p.exit_ns,
                 "exit_px": round(p.exit_px, 6), "fee_in": p.fee_in, "fee_out": p.fee_out,
                 "gross": round(gross, 6), "net": round(gross - p.fee_in - p.fee_out, 6), "fair_dec": p.fair_dec,
                 "fill_check": p.fill_check,
                 "phase": "unknown" if k is None else ("pregame" if p.fill_ns < k else "ingame")}
            for h in st.MARKOUT_S:
                thin, fair = p.mo.get(h, (None, None))
                r[f"mo_thin_{h}"], r[f"mo_fair_{h}"] = thin, fair
            rows.append(r)
        return rows


# ------------------------------------------------------------------------------------------------ settlement results
def load_results(path: Path) -> dict[str, str]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def fetch_results(tickers: Iterable[str], cache: dict[str, str], client) -> None:
    """Public GET /markets/{ticker}; only settled results (yes/no) are cached, so unsettled ones are retried next run."""
    for t in sorted(set(tickers) - set(cache)):
        try:
            r = (client.http.get(f"/markets/{t}", use_cache=False).get("market") or {}).get("result")
        except Exception:                                           # a missing market just stays unsettled
            continue
        if r in ("yes", "no"):
            cache[t] = r


# ------------------------------------------------------------------------------------------------ orchestration
def _is_smoke(p: Path) -> bool:
    """msgs-<hour>.jsonl.gz with no process start-epoch suffix: the 2026-10-20 smoke test, excluded from every result
    by the pre-registration (every recording since is msgs-<hour>-<epoch>.jsonl.gz)."""
    parts = p.name[len("msgs-"):-len(".jsonl.gz")].split("-")
    return not (len(parts) > 1 and parts[1].isdigit())


SILENCE_NS = 60 * NS


def _one_dir(i: int, d: str, silences: dict) -> Iterator[dict]:
    """One raw dir = one stream: its lines in file order tagged with the stream index, then a synthetic stop at its last
    time (the recorder is gone: its books die while other streams go on). Gaps over 60 s are only counted (diagnostic)."""
    prev = top = None
    n, total = 0, 0
    for line in read_lines(p for p in raw_files(d) if not _is_smoke(p)):
        ns = line["recv_ns"]
        if prev is not None and ns - prev > SILENCE_NS:
            n, total = n + 1, total + (ns - prev)
        prev, top = ns, ns if top is None else max(top, ns)
        line["stream"] = i
        yield line
    silences[d] = {"gaps_over_60s": n, "seconds": total / NS}
    if top is not None:
        yield {"recv_ns": top, "ctl": {"event": "stop"}, "stream": i, "synthetic": True}


def _stream(raw_dirs: list[str], silences: dict | None = None) -> Iterator[dict]:
    """All raw dirs on one global clock (by recv_ns); each dir keeps its own book namespace (sids restart per recorder)."""
    silences = {} if silences is None else silences
    return heapq.merge(*(_one_dir(i, d, silences) for i, d in enumerate(raw_dirs)), key=lambda x: x["recv_ns"])


def coin_flip(raw_dirs, universe: Universe, slots: list[tuple[str, str]], windows, results, *, copies: int,
              seed: int, latency: float) -> list[float | None]:
    """Second pass: ``copies`` random-entry copies of the primary version (same markets and sides, random times).
    Per the 08:00Z amendment, "count" is the number of entries: each random entry is sized by the normal fill rule at its
    random time (not forced to the real trade's contract count), and the comparison is per contract."""
    plan = st.plan_coin(slots, windows, copies, seed)
    vs = {c: Version(f"coin{c}", "A", "T", latency=latency, exit="X2") for c in range(copies)}
    sim = Sim(universe, [], markouts=False)
    sim.add_forced(plan, vs)
    sim.run(_stream(raw_dirs))
    sim.settle(results)
    rows = sim.trade_rows()
    out = []
    for c in range(copies):
        p = st.pooled([r for r in rows if r["version"] == f"coin{c}"])
        out.append(p["net_per_contract"])
    return out


def run(raw_dirs: list[str], markets_files: list[str], out_dir: str | Path, *, versions: str = "primary",
        results: dict[str, str] | None = None, fetch: bool = True, coin: bool = True, client=None,
        draws: int = st.DRAWS, copies: int = st.COPIES, seed: int = st.SEED, sport: str = "nfl") -> dict:
    t0 = time.time()
    sp = SPORTS[sport]
    docs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in markets_files]
    u = Universe(docs, sp)
    chosen = [v for v in all_versions(sp) if versions == "all" or v.vid == PRIMARY]
    sim = Sim(u, chosen)
    silences: dict = {}
    sim.run(_stream(raw_dirs, silences))

    # results: injected, or cached next to each markets file and fetched read-only for every traded market (a market
    # without a result is dropped whole, X2 exits included)
    need = sim.traded_tickers()
    if results is None:
        results = {}
        for mf in markets_files:
            cache_path = Path(mf).parent / "results.json"
            cache = load_results(cache_path)
            if fetch:
                if client is None:
                    from kpm.clients.kalshi import KalshiClient
                    client = KalshiClient()
                fetch_results(need, cache, client)
                cache_path.write_text(json.dumps(cache, indent=1, sort_keys=True), encoding="utf-8")
            results.update(cache)
    sim.settle(results)
    rows = sim.trade_rows(u.kickoff)
    unsettled = Counter(p.version.vid for p in sim.all_pos if p.exit_kind == "unsettled")

    games = sorted(sim.game_first)
    order_key = {g: (u.kickoff.get(g) or sim.game_first[g]) for g in games}
    discovery, unseen = st.split_games(order_key)
    disc, uns = set(discovery), set(unseen)
    prim_rows = [r for r in rows if r["version"] == PRIMARY]

    coin_share = None
    enough = len(games) >= sp.min_games and len(unseen) >= sp.min_unseen
    if coin and enough:
        uns_rows = [r for r in prim_rows if r["game"] in uns]
        if uns_rows:
            real = st.pooled(uns_rows)["net_per_contract"]
            copies_v = coin_flip(raw_dirs, u, [(r["ticker"], r["side"]) for r in uns_rows],
                                 {t: tuple(w) for t, w in sim.window.items()}, results, copies=copies, seed=seed, latency=1.0)
            coin_share = st.coin_share(real, copies_v)

    vrows = [st.version_row(v.vid, [r for r in rows if r["version"] == v.vid], sim.orders[v.vid], sim.fills[v.vid],
                            disc, uns) | {"unsettled_dropped": unsettled[v.vid]} for v in chosen]
    life = defaultdict(list)
    for g, s in sim.gap_life:
        life[g].append(s)
    summary = {
        "sport": sp.name, "primary": PRIMARY, "maker_caveat": MAKER_CAVEAT,
        "verdict": st.verdict(prim_rows, disc, uns, coin_share, draws=draws, seed=seed, min_games=sp.min_games,
                              min_unseen=sp.min_unseen, games_per_week=sp.games_per_week, unseen_order=unseen,
                              lines_out_of_order=sim.out_of_order, tickers_in_two_streams=len(sim.two_streams)),
        "games": [{"game": g, "kickoff_ns": u.kickoff.get(g), "first_ns": sim.game_first[g],
                   "segment": "discovery" if g in disc else "unseen",
                   "primary": st.pooled([r for r in prim_rows if r["game"] == g]),
                   "gap_lifetimes": st.gap_stats(life[g])} for g in games],
        "gap_lifetimes_all": st.gap_stats([s for _, s in sim.gap_life]),
        "unsettled_dropped": dict(unsettled), "universe_skipped": dict(u.skipped),
        "raw_files_skipped_smoke": [str(p) for d in raw_dirs for p in raw_files(d) if _is_smoke(p)],
        "targets": Counter(t.family for t in u.targets.values()), "lines": sim.lines,
        "lines_out_of_order": sim.out_of_order, "tickers_in_two_streams": sorted(sim.two_streams),
        "stream_silences": silences,
        "versions": vrows, "runtime_s": round(time.time() - t0, 1)}
    _write(Path(out_dir), summary, rows, vrows)
    return summary


def _write(out: Path, summary: dict, rows: list[dict], vrows: list[dict]) -> None:
    import csv
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    for name, data in (("trades.csv", rows), ("versions.csv", vrows)):
        with open(out / name, "w", newline="", encoding="utf-8") as fh:
            if data:
                w = csv.DictWriter(fh, fieldnames=list(data[0]))
                w.writeheader()
                w.writerows(data)
