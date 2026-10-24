"""Research log #33 (reports/output/cheapleg/PREREGISTRATION.md): does the cheap leg of a "fake lock" win more often
than its price?  READ-ONLY replay of the WebSocket recordings; public GET market data only; no order code.

One time-ordered pass over the raw recordings (same stream merge / book rebuild / outage rules as
kpm.analysis.ws_sim + ws_books), driving the live bot's own Detector (reports/output/ohio_trial/lockcore.py).

    python reports/output/cheapleg/cheapleg.py --raw <dir> [<dir> ...] --markets <markets.json> [...] --out <outdir>
           [--label source|test] [--count-only] [--no-fetch] [--no-coin] [--now <iso>] [--cache <dir>]
           [--live-events <events.jsonl> ...] [--lockwatch <lockwatch_night.jsonl> ...]

Order of work (amendments 6, 7 of the pre-registration):
  1. replay -> lock events -> simulated fills (no result, settlement or P&L is read);
  2. GATE from metadata only (market status and the clock): per game all primary fills settled or >= 7 days since the
     scheduled start; >= 30 games, >= 15 unseen; unseen primary fills >= 200.  Gate failed -> print the gate counts and
     exit 3 with NO statistic computed, printed or written;
  3. only then results are read and every statistic is produced.
--count-only stops after step 1 and the gate counts: it fetches/prints no result, settlement or P&L.
"""
from __future__ import annotations

import argparse
import csv
import heapq
import json
import math
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "reports" / "output" / "ohio_trial"))
sys.path.insert(0, str(ROOT / "src"))

import lockcore as lc  # noqa: E402
from kpm.analysis.ws_books import BookSet, Top, Trade  # noqa: E402

NS = 1_000_000_000
SEED = 20261021
DRAWS = 10_000
COPIES = 500
GAMES_PER_WEEK = 50
MIN_PROFIT = 0.01
REFIRE_S = 60.0
MAX_LEGS = 3                                   # amendment 4: the live bot's max_legs
LATENCIES = {"10": 0.010, "40": 0.040}
ROLES = ("cheapest", "stale", "trigger")
PRIMARY = "cheapest-L40"                       # amendment 8
VERSIONS = [f"{r}-L{l}" for r in ROLES for l in LATENCIES]
PRICE_BUCKETS = [("<1c", 0, 0), ("1-2c", 1, 2), ("3-5c", 3, 5), ("6-10c", 6, 10), ("11-25c", 11, 25),
                 ("26-50c", 26, 50), (">50c", 51, 100)]
GATE_MIN_GAMES, GATE_MIN_UNSEEN, GATE_MIN_UNSEEN_FILLS, SETTLE_WAIT_S = 30, 15, 200, 7 * 86400
TERMINAL = ("settled", "finalized")
TEST_FIRST_NIGHT = (2026, 10, 21)              # test games = every game recorded from 26OCT21 on
TIP_SETTLE_H = (2.0, 4.5)                      # tip (scheduled start) to first KXNBAGAME settlement, hours
RESULT_KEYS = ("result", "settlement_value", "settlement_value_dollars", "settlement_ts", "expiration_value")
MONTHS = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
PHASES = ("pre", "in", "unknown")


# ------------------------------------------------------------------------------------------------ pure helpers
def ticks(p: float) -> int:
    return int(round(p * 10000))


def fee1(p: float) -> float:
    return lc.fee(p, 1)


def parse_leg(leg: str):
    side, tk = leg.split(" ", 1)
    return side, tk


def mtype_of(series: str) -> str:
    """lockcore's label, except the *PROT series (player props, recorded from 26OCT21) are "prop" (amendment 10);
    lockcore would call them "ot"."""
    if series.startswith("KXNBA") and series.endswith("PROT"):
        return "prop"
    return lc.market_type(series)


def night_date(game: str):
    """'26OCT21ATLORL' -> (2026, 10, 21) or None."""
    n = lc.night_of(game)
    if n == "unknown" or n[2:5] not in MONTHS:
        return None
    return 2000 + int(n[:2]), MONTHS[n[2:5]], int(n[5:7])


def pick_legs(legs: list, prices: list, trigger: str | None, last_ask: dict, last_bid: dict) -> dict:
    """-> {'cheapest': idx, 'stale': idx|None, 'trigger': idx|None}.
    cheapest: lowest price, ties -> alphabetically first ticker, then YES before NO.
    stale: among non-trigger legs, the one whose relevant best price (ask of a YES leg, bid of a NO leg) last changed
    longest ago (smallest last-change time); ties -> the same alphabetical rule."""
    parsed = [parse_leg(l) for l in legs]
    cheap = min(range(len(legs)), key=lambda i: (ticks(prices[i]), parsed[i][1], 0 if parsed[i][0] == "YES" else 1))
    trig = next((i for i, (_, tk) in enumerate(parsed) if tk == trigger), None)
    others = [i for i in range(len(legs)) if i != trig]
    stale = None
    if others:
        def lastchg(i):
            side, tk = parsed[i]
            return (last_ask if side == "YES" else last_bid).get(tk, 0)
        stale = min(others, key=lambda i: (lastchg(i), parsed[i][1], 0 if parsed[i][0] == "YES" else 1))
    return {"cheapest": cheap, "stale": stale, "trigger": trig}


def ioc_arrival(top, side: str, lock_price: float, levels=None):
    """IOC buy of 1 contract at the lock price.  top = (bid, bsz, ask, asz) of the leg's ticker, or None if its book is
    invalid.  -> (filled, fill_price, reason).
    YES leg: best price = ask; NO leg: best price = 1 - bid.  Needs best price <= lock price (amendment: fills at the
    better price) AND (amendment 12) cumulative size >= 1 contract at prices at or better than the lock price.
    levels = [(price_ticks, size), ...] ascending price on the buy side (all depth); default = the top level only.
    If the best level holds < 1 contract, the fill price is the level at which the first contract is complete."""
    if top is None:
        return False, None, "invalid_book"
    if side == "YES":
        if top[2] is None:
            return False, None, "no_quote"
        px, sz = ticks(top[2]), top[3]
    else:
        if top[0] is None:
            return False, None, "no_quote"
        px, sz = 10000 - ticks(top[0]), top[1]
    lim = ticks(lock_price)
    if px > lim:
        return False, None, "price_moved"
    lv = levels if levels is not None else [(px, sz)]
    cum = 0.0
    for p, s in lv:
        if p > lim:
            break
        cum += s
        if cum >= 1 - 1e-9:
            return True, p / 10000, "fill"
    return False, None, "no_depth"


def settle_pnl(side: str, result, price: float):
    """-> (status, win, pnl).  YES wins on 'yes', NO wins on 'no'.  Anything else is excluded."""
    if result in ("yes", "no"):
        win = 1 if (result == "yes") == (side == "YES") else 0
        return "settled", win, win - price - fee1(price)
    return ("void" if result == "void" else "unsettled"), None, None


def bootstrap_games(per_game: dict, games: list, draws: int = DRAWS, seed: int = SEED):
    """per_game {game: (sum_pnl, n_fills)}.  Resample whole games with replacement.  -> dict with CI of the mean P&L per
    fill and of the mean P&L per game (zero-fill games included)."""
    if not games:
        return None
    s = np.array([per_game.get(g, (0.0, 0))[0] for g in games])
    n = np.array([per_game.get(g, (0.0, 0))[1] for g in games], dtype=float)
    idx = np.random.default_rng(seed).integers(0, len(games), size=(draws, len(games)))
    ss, nn = s[idx].sum(1), n[idx].sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        per_fill = np.where(nn > 0, ss / nn, np.nan)
    per_game_m = ss / len(games)
    ok = per_fill[~np.isnan(per_fill)]
    out = {"draws": draws, "seed": seed, "ci_mean_per_fill": None, "ci_mean_per_game": None}
    if len(ok):
        out["ci_mean_per_fill"] = [float(np.percentile(ok, 2.5)), float(np.percentile(ok, 97.5))]
    out["ci_mean_per_game"] = [float(np.percentile(per_game_m, 2.5)), float(np.percentile(per_game_m, 97.5))]
    return out


def tip_ns_from(kr: dict | None):
    """Scheduled start = game time - 3 h.  Game time = occurrence_datetime when Kalshi gives one, else
    expected_expiration_time (research log #24 / reports/output/nba_drift: tip = occurrence - 3h, with
    expected_expiration_time standing in where occurrence_datetime is absent)."""
    if not kr:
        return None, None
    for key in ("occurrence_datetime", "expected_expiration_time"):
        v = kr.get(key)
        if v:
            return int((datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp() - 3 * 3600) * NS), key
    return None, None


def derive_tips(games: list, kick_cands: dict, markets: dict):
    """Tip per game, independent of the order of the --markets files (amendment audit finding 7).
    Candidates = every non-None kickoff_raw of the game in any --markets file (the recorder-start snapshot of the API);
    the earliest implied tip wins (they agree in practice; disagreements are reported).  A game with none falls back to
    its KXNBAGAME markets in the API metadata.  Every game must end with a tip (assert)."""
    tips, src, disagree = {}, {}, {}
    for g in games:
        found = []
        for kr in kick_cands.get(g, []):
            tip, key = tip_ns_from(kr)
            if tip is not None:
                found.append((tip, key))
        if found:
            tips[g], src[g] = min(found)[0], min(found)[1]
            if len({f[0] for f in found}) > 1:
                disagree[g] = sorted({f[0] for f in found})
            continue
        api = []
        for t, m in markets.items():
            if lc.series_of(t) == "KXNBAGAME" and lc.game_of(t) == g:
                tip, key = tip_ns_from(m)
                if tip is not None:
                    api.append((tip, key))
        if api:
            tips[g], src[g] = min(api)[0], "api:" + min(api)[1]
    missing = [g for g in games if g not in tips]
    assert not missing, f"games without a scheduled start: {missing}"
    return tips, src, disagree


def check_tip_vs_settle(games: list, tips: dict, markets: dict) -> dict:
    """Audit finding 7/14: the first KXNBAGAME settlement of a game must fall TIP_SETTLE_H hours after its tip.  Games whose
    settlement_ts is not known yet are skipped.  Raises AssertionError on a violation."""
    out = {}
    for g in games:
        sts = [datetime.fromisoformat(m["settlement_ts"].replace("Z", "+00:00")).timestamp()
               for t, m in markets.items() if lc.series_of(t) == "KXNBAGAME" and lc.game_of(t) == g and m.get("settlement_ts")]
        if sts:
            h = (min(sts) - tips[g] / NS) / 3600
            out[g] = round(h, 2)
            assert TIP_SETTLE_H[0] <= h <= TIP_SETTLE_H[1], f"{g}: settlement is {h:.2f} h after the inferred tip"
    return out


def phase_of(tip_ns, ns: int) -> str:
    if tip_ns is None:
        return "unknown"
    return "pre" if ns < tip_ns else "in"


def price_bucket(price: float) -> str:
    c = int(round(price * 100 + 1e-9)) if price >= 0.005 else 0
    for name, lo, hi in PRICE_BUCKETS:
        if lo <= c <= hi:
            return name
    return ">50c"


# ------------------------------------------------------------------------------------------------ books
class _Dropped(list):
    """BookSet.dropped that also records which tickers were invalidated (so their next top is always re-emitted)."""

    def __init__(self, owner):
        super().__init__()
        self._o = owner

    def append(self, t):
        self._o.inval.add(t)
        super().append(t)

    def extend(self, it):
        it = list(it)
        self._o.inval.update(it)
        super().extend(it)


class CBookSet(BookSet):
    """BookSet that clears last_top when a book is invalidated.  ws_books only emits a Top when the top changed, so a
    book that re-validates (new snapshot) with the same top emitted nothing and the Detector never re-checked it.  Now the
    first top after an invalidation is always emitted (audit finding 11)."""

    def __init__(self):
        super().__init__()
        self.inval: set = set()
        self.dropped = _Dropped(self)

    def _emit(self, recv_ns, t):
        if t in self.inval:
            self.inval.discard(t)
            self.last_top.pop(t, None)
        yield from super()._emit(recv_ns, t)


# ------------------------------------------------------------------------------------------------ replay
class Replay:
    """Drives lockcore.Detector from merged WS lines.  Collects lock_open candidates (count-1 profit >= $0.01, <= 3 legs)
    with the three single-leg choices and, for each, the IOC arrival outcome at t + L (checked when stream time passes
    t + L).

    Coin-flip sampler (audit finding 13): the pre-registered sample is "every valid-book (ticker, side) priced 1-99c, once
    per second".  Instead of visiting every ticker every second, each ticker's (price codes, valid) state is a SEGMENT that
    is closed when the top changes or the book is invalidated; the number of per-second samples that fell inside the
    segment (counted per game and phase) is added to res[(game, phase)][code][ticker].  This gives the exact sample counts
    (no reservoir, no sampling noise, no ticker index limit) at a cost proportional to the number of top changes."""

    def __init__(self, doc: dict, tips: dict | None = None, coin: bool = False, seed: int = SEED,
                 stream_names: list | None = None):
        self.doc = doc
        self.tips = tips or {}
        self.stream_names = stream_names
        self.sets: dict[int, CBookSet] = defaultdict(CBookSet)
        self.owner: dict[str, int] = {}
        self.two_streams: set = set()
        self.cur = 0
        self.last_ask: dict[str, int] = {}
        self.last_bid: dict[str, int] = {}
        self.prev: dict[str, tuple] = {}
        self.det = lc.Detector(doc, self._tops, self._emit)
        self.cands: list[dict] = []
        self.opens: list[tuple] = []                 # every lock_open: (ns, legs tuple, count-1 profit, n_legs)
        self.heap: list = []
        self._seq = 0
        self.cur_t = None
        self.cur_ns = 0
        self.n_open = self.n_below = self.n_noleg = 0
        self.n_gt3 = self.n_gt3_qualifying = 0
        self.arrival = Counter()
        self.lines = 0
        self.out_of_order = 0
        self._max_ns: dict[int, int] = {}
        self.seen_top: set = set()
        self.coin = coin
        self.rng = random.Random(seed)
        self.seg: dict[str, tuple] = {}              # ticker -> (game, codes, counter snapshot)
        self.cnt: dict[tuple, int] = defaultdict(int)   # (game, phase) -> per-second samples so far
        self.res: dict = {}                          # (game, phase) -> {code: {ticker: seconds}}
        self.last_sec = -1
        self.n_samples = 0
        self.game_of = {t: lc.game_of(t) for t in doc["books"]}
        self.games = sorted(set(self.game_of.values()))

    # ---- books
    def _tops(self, t):
        bs = self.sets.get(self.owner.get(t, 0))
        if bs is None or t not in bs.sid_of:
            return None
        return bs.top(t)

    def _levels(self, tk: str):
        """Buy-side price levels of ticker tk for both sides: [(price_ticks, size)] ascending price, from the opposite book
        (YES ask = 1 - NO bid; NO price = 1 - YES bid)."""
        bs = self.sets.get(self.owner.get(tk, 0))
        return (sorted((10000 - p, s) for p, s in bs.no[tk].items()),
                sorted((10000 - p, s) for p, s in bs.yes[tk].items()))

    # ---- lock events
    def _emit(self, ev, st, now):
        if ev != "lock_open":
            return
        self.n_open += 1
        legs, prices, kind = st["legs"], st["prices"], st["kind"]
        payout = len(legs) - 1 if kind == "me_no" else 1
        profit1 = round(payout - sum(prices) - sum(fee1(p) for p in prices), 4)
        self.opens.append((self.cur_ns, tuple(legs), profit1, len(legs)))
        if len(legs) > MAX_LEGS:                     # amendment 4: the live bot never trades > 3 legs
            self.n_gt3 += 1
            self.n_gt3_qualifying += profit1 >= MIN_PROFIT - 1e-9
            return
        if profit1 < MIN_PROFIT - 1e-9:
            self.n_below += 1
            return
        pick = pick_legs(legs, prices, self.cur_t, self.last_ask, self.last_bid)
        if pick["trigger"] is None:
            self.n_noleg += 1
        streams = tuple(sorted({self.owner.get(parse_leg(l)[1], 0) for l in legs}))
        c = {"t_ns": self.cur_ns, "open_ns": self.cur_ns, "legs": list(legs), "prices": list(prices), "kind": kind,
             "profit": st["profit"], "profit1": profit1, "trigger": self.cur_t, "pick": pick, "arr": {},
             "game": lc.game_of(parse_leg(legs[0])[1]), "n_legs": len(legs), "streams": streams}
        self.cands.append(c)
        for role, i in pick.items():
            if i is None:
                continue
            for lk, lat in LATENCIES.items():
                if (i, lk) not in c["arr"]:
                    c["arr"][(i, lk)] = None
                    self._seq += 1
                    heapq.heappush(self.heap, (self.cur_ns + int(round(lat * NS)), self._seq, c, i, lk))

    def _flush(self, upto: float) -> None:
        h = self.heap
        while h and h[0][0] < upto:
            _, _, c, i, lk = heapq.heappop(h)
            side, tk = parse_leg(c["legs"][i])
            top = self._tops(tk)
            lv = None
            if top is not None:
                ya, na = self._levels(tk)
                lv = ya if side == "YES" else na
            ok, px, why = ioc_arrival(top, side, c["prices"][i], lv)
            self.arrival[why] += 1
            c["arr"][(i, lk)] = (ok, px, why)

    # ---- per-second price-matched sampler (segment method, see class docstring)
    @staticmethod
    def _code_for(ask, bid):
        out = []
        if ask is not None and 100 <= ticks(ask) <= 9900:
            out.append(ticks(ask) * 2)
        if bid is not None and 100 <= 10000 - ticks(bid) <= 9900:
            out.append((10000 - ticks(bid)) * 2 + 1)
        return out

    def _sample(self, ns: int) -> None:
        tips, cnt = self.tips, self.cnt
        for g in self.games:
            cnt[(g, phase_of(tips.get(g), ns))] += 1
        self.n_samples += 1

    def _seg_open(self, t: str, codes: list) -> None:
        if not codes:
            return
        g = self.game_of[t]
        self.seg[t] = (g, codes, tuple(self.cnt[(g, ph)] for ph in PHASES))

    def _seg_close(self, t: str) -> None:
        s = self.seg.pop(t, None)
        if s is None:
            return
        g, codes, snap = s
        for ph, s0 in zip(PHASES, snap):
            d = self.cnt[(g, ph)] - s0
            if d:
                bk = self.res.setdefault((g, ph), {})
                for code in codes:
                    cell = bk.setdefault(code, {})
                    cell[t] = cell.get(t, 0) + d

    # ---- stream
    def _feed(self, line: dict, ns: int) -> None:
        cur = self.cur
        bs = self.sets[cur]
        touched = []
        for ev in bs.feed(line):
            t = ev.ticker
            if self.owner.setdefault(t, cur) != cur:
                self.two_streams.add(t)
                continue
            if isinstance(ev, Top):
                p = self.prev.get(t)
                if p is None or p[0] != ev.best_yes_bid:
                    self.last_bid[t] = ns
                if p is None or p[1] != ev.best_yes_ask:
                    self.last_ask[t] = ns
                self.prev[t] = (ev.best_yes_bid, ev.best_yes_ask)
                self.seen_top.add(t)
                if self.coin and t in self.game_of:
                    self._seg_close(t)
                    self._seg_open(t, self._code_for(ev.best_yes_ask, ev.best_yes_bid))
            # a Trade also drives the Detector (lockwatch_fast: touched = every top and trade ticker)
            touched.append(t)
        if bs.dropped:
            dr = [t for t in bs.dropped if self.owner.get(t, cur) == cur]
            if self.coin:
                for t in dr:
                    if t not in bs.sid_of:           # not re-validated within the same message
                        self._seg_close(t)
            touched.extend(dr)
            bs.dropped.clear()
        done = set()
        for t in touched:
            if t in done:
                continue
            done.add(t)
            self.cur_t, self.cur_ns = t, ns
            self.det.update(t, ns / NS)

    def feed_line(self, line: dict) -> None:
        ns = line["recv_ns"]
        self.cur = line.get("stream", 0)
        if not line.get("synthetic"):
            self.lines += 1
            self.out_of_order += ns < self._max_ns.get(self.cur, -1)       # as ws_sim: counted within a stream only
            self._max_ns[self.cur] = max(self._max_ns.get(self.cur, -1), ns)
        if self.coin and ns // NS != self.last_sec:
            if self.last_sec >= 0:
                self._sample(ns)
            self.last_sec = ns // NS
        connect = (line.get("ctl") or {}).get("event") == "connect"
        if connect:
            self._feed(line, ns)
        self._flush(ns)                      # checks due strictly before this line see the books as they were
        if not connect:
            self._feed(line, ns)

    def finish(self) -> None:
        self._flush(math.inf)
        for t in list(self.seg):
            self._seg_close(t)

    def run(self, lines) -> None:
        for line in lines:
            self.feed_line(line)
        self.finish()


# ------------------------------------------------------------------------------------------------ metadata
def event_of(ticker: str) -> str:
    """Fallback guess only (first two ticker parts).  The API's event_ticker per market is the authority (RACE tickers
    like KXNBARACE-26OCT20OKCSAS-10-SAS belong to event KXNBARACE-26OCT20OKCSAS-10)."""
    return "-".join(ticker.split("-")[:2])


def strip_results(m: dict) -> dict:
    return {k: v for k, v in m.items() if k not in RESULT_KEYS}


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)


def fetch_meta(recorded: set, cache_dir: Path, fetch: bool, log, strip: bool = False, client=None):
    """-> (markets {ticker: market dict}, einfo {event: {mutually_exclusive}}).  Public GETs only; every raw response is also
    kept in cache_dir/raw.
      1. bulk GET /markets?event_ticker=<guess> per guessed event (first two ticker parts), cached; a cached event is
         reused when every market in it is settled/finalized, otherwise refetched (unless fetch is False);
      2. every recorded ticker the bulk calls did not return is fetched ONE BY ONE with GET /markets/{ticker} (this is how
         RACE tickers are found); the market's own event_ticker then triggers a bulk fetch of its real event;
      3. GET /events/{event} for each event gives mutually_exclusive.
    strip=True (--count-only) drops result / settlement fields from the returned dicts: nothing about outcomes is used."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    raw = cache_dir / "raw"
    raw.mkdir(exist_ok=True)
    state = {"cl": client}

    def cl():
        if state["cl"] is None:
            from kpm.clients.kalshi import KalshiClient
            state["cl"] = KalshiClient()
        return state["cl"]

    def get(path, params=None):
        r = cl().http.get(path, params, use_cache=False, persist=False)
        (raw / (_safe(path.strip("/")) + (("__" + _safe("_".join(f"{k}{v}" for k, v in sorted((params or {}).items())
                                                            if v is not None))) if params else "") + ".json")
         ).write_text(json.dumps(r))
        return r

    markets: dict = {}
    loaded_events: set = set()

    def load_event(ev: str):
        if ev in loaded_events:
            return
        loaded_events.add(ev)
        mp = cache_dir / f"markets_{ev}.json"
        ms = None
        if mp.exists():
            d = json.loads(mp.read_text())
            if not fetch or all(m.get("status") in TERMINAL for m in d["markets"]):
                ms = d["markets"]
        if ms is None and fetch:
            ms, cur = [], None
            while True:
                r = get("/markets", {"event_ticker": ev, "limit": 1000, "cursor": cur})
                ms += r.get("markets", [])
                cur = r.get("cursor")
                if not cur:
                    break
            mp.write_text(json.dumps({"fetched_ts": time.time(), "markets": ms}))
        for m in ms or []:
            markets[m["ticker"]] = m

    guess = sorted({event_of(t) for t in recorded})
    for n, ev in enumerate(guess):
        load_event(ev)
        if (n + 1) % 25 == 0:
            log(f"  metadata {n + 1}/{len(guess)} events")

    n_single = 0
    for t in sorted(recorded - set(markets)):                   # step 2: per-ticker GET for what the bulk calls missed
        mp = cache_dir / f"market_{t}.json"
        d = None
        if mp.exists():
            d = json.loads(mp.read_text())
            if fetch and not d.get("missing") and (d.get("market") or {}).get("status") not in TERMINAL:
                d = None
        if d is None and fetch:
            try:
                d = {"market": get(f"/markets/{t}").get("market")}
                n_single += 1
            except Exception as exc:  # noqa: BLE001
                d = {"missing": True, "error": type(exc).__name__} if getattr(exc, "status", None) == 404 else None
                log(f"  market {t}: {type(exc).__name__}")
            if d is not None:
                mp.write_text(json.dumps(d))
        m = (d or {}).get("market")
        if m:
            markets[t] = m
            if m.get("event_ticker"):
                load_event(m["event_ticker"])
    if n_single:
        log(f"  {n_single} tickers fetched one by one (not returned by their guessed event)")

    einfo: dict = {}
    events = sorted({m.get("event_ticker") or event_of(t) for t, m in markets.items()} | set(guess))
    for ev in events:
        ep = cache_dir / f"event_{ev}.json"
        info = None
        if ep.exists():
            info = json.loads(ep.read_text())
        elif fetch:
            try:
                e = get(f"/events/{ev}")["event"]
                info = {"mutually_exclusive": e.get("mutually_exclusive")}
                ep.write_text(json.dumps(info))
            except Exception as exc:  # noqa: BLE001
                log(f"  event {ev}: {type(exc).__name__}")
        einfo[ev] = info or {"mutually_exclusive": None}
    if strip:
        markets = {t: strip_results(m) for t, m in markets.items()}
    return markets, einfo


def build_doc(recorded: set, markets: dict, einfo: dict) -> dict:
    """The live bot's doc, restricted to the markets that were recorded (= what the recorder subscribed to).  A market's
    event is the API's event_ticker (fallback: the two-part guess when the API does not know the ticker)."""
    events, books = {}, {}
    for t in sorted(recorded):
        m = markets.get(t) or {}
        ev = m.get("event_ticker")
        if not ev or not t.startswith(ev + "-"):
            ev = event_of(t)
        events[ev] = {"mutually_exclusive": (einfo.get(ev) or {}).get("mutually_exclusive")}
        books[t] = {"strike_type": m.get("strike_type"), "floor": m.get("floor_strike"), "cap": m.get("cap_strike")}
    return {"events": events, "books": books}


# ------------------------------------------------------------------------------------------------ events and rows
def dedupe_chains(cands: list, window_ms: float = lc.DEDUP_WINDOW_MS) -> list:
    """Same grouping as lockcore.dedupe (locks sharing any leg and opening within window_ms form one chain, connected
    components) -> list of chains, each a list of candidate indices sorted by (open time, emission order)."""
    n = len(cands)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_leg: dict = {}
    for i, c in enumerate(cands):
        for leg in c["legs"]:
            by_leg.setdefault(leg, []).append((c["open_ns"], i))
    win = window_ms * 1e6
    for L in by_leg.values():
        L.sort()
        for (a, i), (b, j) in zip(L, L[1:]):
            if b - a <= win:
                parent[find(i)] = find(j)
    comp: dict = {}
    for i in range(n):
        comp.setdefault(find(i), []).append(i)
    return [sorted(v, key=lambda i: (cands[i]["open_ns"], i)) for v in comp.values()]


def make_events(cands: list, rep: str = "earliest") -> list:
    """One event per 50 ms chain.  rep='earliest' (amendment 11, the registered rule: the live bot fires on the first
    lock_open) or 'maxprofit' (robustness variant, what lockcore.dedupe does)."""
    out = []
    for ch in dedupe_chains(cands):
        if rep == "earliest":
            r = cands[ch[0]]
        else:
            r = cands[max(ch, key=lambda i: (cands[i]["profit"], -ch.index(i)))]
        out.append(dict(r, n_locks=len(ch), open_ns=min(cands[i]["open_ns"] for i in ch)))
    out.sort(key=lambda e: (e["t_ns"], e["legs"]))
    return out


def is_multi(e: dict) -> bool:
    return len(e.get("streams") or (0,)) > 1


def attach_outcomes(rows_by_v: dict, markets: dict) -> None:
    """Read results (settlement) for the filled rows.  Called only after the gate has passed."""
    for rows in rows_by_v.values():
        for r in rows:
            if r["filled"]:
                res = (markets.get(r["ticker"]) or {}).get("result")
                status, win, pnl = settle_pnl(r["side"], res, r["fill_price"])
                r.update(result=res or None, status=status, win=win, pnl=pnl)


def build_rows(cands: list, tips: dict, markets: dict | None, now_ts: float = 0, rep: str = "earliest",
               which: str = "main", versions=None, stream_names=None):
    """Dedupe (50 ms, earliest lock per chain), drop/keep multi-stream events (which='main': single-recorder events - the
    registered analysis; 'multi': cross-recorder events, reported separately), then per version the 60 s refire guard and
    the fill outcome.  markets=None -> no result is read (gate / count-only); otherwise settlement P&L is attached.
    -> (rows {version: [row]}, events)."""
    events = make_events(cands, rep) if cands else []
    events = [e for e in events if is_multi(e) == (which == "multi")]
    rows = {v: [] for v in (versions or VERSIONS)}
    for v in rows:
        role, lk = v.split("-L")
        last_buy: dict = {}
        for e in events:
            i = e["pick"][role]
            if i is None:
                continue
            side, tk = parse_leg(e["legs"][i])
            price = e["prices"][i]
            game = e["game"]
            ph = phase_of(tips.get(game), e["t_ns"])
            sn = e.get("streams") or (0,)
            row = dict(version=v, t_ns=e["t_ns"], game=game, phase=ph, kind=e["kind"], mtype=mtype_of(lc.series_of(tk)),
                       ticker=tk, side=side, lock_price=price, refire_skipped=False, filled=False, fill_price=None, fee=None,
                       result=None, status=None, win=None, pnl=None, trigger=e["trigger"],
                       is_cheapest=i == e["pick"]["cheapest"], is_stale=i == e["pick"]["stale"],
                       is_trigger=i == e["pick"]["trigger"], n_legs=e["n_legs"], lock_profit1=e["profit1"],
                       miss_reason=None, streams=",".join((stream_names[s] if stream_names else str(s)) for s in sn),
                       multi_stream=len(sn) > 1)
            lbt = last_buy.get((tk, side))
            if lbt is not None and e["t_ns"] - lbt < REFIRE_S * NS:
                row["refire_skipped"] = True
                rows[v].append(row)
                continue
            arr = e["arr"].get((i, lk))
            ok, px, why = arr if arr else (False, None, "unresolved")
            row["miss_reason"] = None if ok else why
            if ok:
                last_buy[(tk, side)] = e["t_ns"]
                row.update(filled=True, fill_price=px, fee=fee1(px))
            rows[v].append(row)
    if markets is not None:
        attach_outcomes(rows, markets)
    return rows, events


def primary_fills(rows: list) -> list:
    return [r for r in rows if r["filled"] and not r["refire_skipped"]]


def split_games(order: list) -> tuple:
    """Discovery = first ceil(N/2) games by scheduled start, unseen = the rest."""
    h = math.ceil(len(order) / 2)
    return order[:h], order[h:]


def gate_check(order: list, tips: dict, fills: list, status_of: dict, now_ts: float) -> dict:
    """Amendments 6 + 7, from METADATA ONLY (market status, clock, fill counts - never a result).
    Per game: all primary fills settled (status settled/finalized), or >= 7 days since the scheduled start.
    ok = every game meets that, >= 30 games, >= 15 unseen games, and >= 200 unseen primary fills."""
    disc, unseen = split_games(order)
    by_game = defaultdict(list)
    for r in fills:
        by_game[r["game"]].append(r["ticker"])
    per = {}
    for g in order:
        tks = by_game.get(g, [])
        pend = sorted({t for t in tks if status_of.get(t) not in TERMINAL})
        old = tips.get(g) is not None and now_ts - tips[g] / NS >= SETTLE_WAIT_S
        per[g] = dict(fills=len(tks), unsettled_fill_tickers=len(pend), days_since_start=None if tips.get(g) is None
                      else round((now_ts - tips[g] / NS) / 86400, 2), ok=(not pend) or old)
    n_unseen_fills = sum(per[g]["fills"] for g in unseen)
    bad = [g for g in order if not per[g]["ok"]]
    ok = (len(order) >= GATE_MIN_GAMES and len(unseen) >= GATE_MIN_UNSEEN and not bad
          and n_unseen_fills >= GATE_MIN_UNSEEN_FILLS)
    return dict(ok=ok, games=len(order), games_gate_met=len(order) - len(bad), unseen_games=len(unseen),
                discovery_games=len(disc), unseen_primary_fills=n_unseen_fills,
                discovery_primary_fills=sum(per[g]["fills"] for g in disc), games_not_gate_met=bad,
                thresholds=dict(min_games=GATE_MIN_GAMES, min_unseen_games=GATE_MIN_UNSEEN,
                                min_unseen_primary_fills=GATE_MIN_UNSEEN_FILLS, settle_wait_days=SETTLE_WAIT_S // 86400),
                per_game=per)


def mean(x):
    return float(sum(x) / len(x)) if x else None


def stats_for(rows: list, games: list) -> dict:
    gs = set(games)
    rs = [r for r in rows if r["game"] in gs and not r["refire_skipped"]]
    fills = [r for r in rs if r["filled"]]
    st = [r for r in fills if r["status"] == "settled"]
    per_game = defaultdict(lambda: [0.0, 0])
    for r in st:
        per_game[r["game"]][0] += r["pnl"]
        per_game[r["game"]][1] += 1
    pg = {g: tuple(v) for g, v in per_game.items()}
    tot = sum(r["pnl"] for r in st)
    n_games = len(games)
    out = dict(games=n_games, lock_events=len(rs), refire_skipped=sum(1 for r in rows if r["game"] in gs and r["refire_skipped"]),
               fills=len(fills), misses=len(rs) - len(fills), fill_rate=(len(fills) / len(rs)) if rs else None,
               settled_fills=len(st), unsettled_excluded=sum(1 for r in fills if r["status"] == "unsettled"),
               void_excluded=sum(1 for r in fills if r["status"] == "void"),
               mean_pnl_per_fill=mean([r["pnl"] for r in st]), win_rate=mean([r["win"] for r in st]),
               mean_price=mean([r["fill_price"] for r in st]), mean_fee=mean([r["fee"] for r in st]), total_pnl=tot,
               pnl_per_game=(tot / n_games) if n_games else None,
               dollars_per_week=(tot / n_games * GAMES_PER_WEEK) if n_games else None,
               miss_reasons=dict(Counter(r["miss_reason"] for r in rs if not r["filled"])))
    out["bootstrap"] = bootstrap_games(pg, list(games))
    return out


def breakdown(rows: list, key) -> dict:
    g = defaultdict(list)
    for r in rows:
        if r["filled"] and not r["refire_skipped"] and r["status"] == "settled":
            g[key(r)].append(r)
    return {k: dict(n=len(v), win_rate=mean([r["win"] for r in v]), mean_price=mean([r["fill_price"] for r in v]),
                    mean_fee=mean([r["fee"] for r in v]), mean_pnl=mean([r["pnl"] for r in v]),
                    total_pnl=sum(r["pnl"] for r in v)) for k, v in sorted(g.items(), key=lambda kv: str(kv[0]))}


# ------------------------------------------------------------------------------------------------ coin flip
class CoinPools:
    """Price-matched pools of random (time, ticker, side) candidates from the same game and phase.
    res[(game, phase)][code][ticker] = number of per-second samples; code = price_ticks * 2 + (1 if NO side)."""

    def __init__(self, res: dict, markets: dict):
        self.res, self.markets = res, markets
        self.cache: dict = {}

    def pool(self, game: str, phase: str, price: float):
        key = (game, phase, ticks(price))
        if key in self.cache:
            return self.cache[key]
        bk = self.res.get((game, phase), {})
        pt = ticks(price)
        pnls, ws = [], []
        for code, cell in bk.items():
            if abs((code >> 1) - pt) > 100:
                continue
            side = "NO" if code & 1 else "YES"
            px = (code >> 1) / 10000
            for tk, secs in cell.items():
                _, _, pnl = settle_pnl(side, (self.markets.get(tk) or {}).get("result"), px)
                if pnl is not None:
                    pnls.append(pnl)
                    ws.append(secs)
        out = (np.array(pnls), np.array(ws, dtype=float) / sum(ws)) if pnls else None
        self.cache[key] = out
        return out


def coin_flip(fills: list, pools: CoinPools, copies: int = COPIES, seed: int = SEED) -> dict:
    """fills: rows (settled fills).  Each copy replaces every real fill with a random price-matched candidate."""
    if not fills:
        return dict(n=0)
    rng = np.random.default_rng(seed)
    tot = np.zeros(copies)
    n_ok, missing, real = 0, 0, []
    for r in fills:
        p = pools.pool(r["game"], r["phase"], r["fill_price"])
        if p is None:
            missing += 1
            continue
        vals, w = p
        tot += vals[rng.choice(len(vals), size=copies, p=w)]
        n_ok += 1
        real.append(r["pnl"])
    if not n_ok:
        return dict(n=len(fills), no_candidate=missing)
    cm = tot / n_ok
    rm = float(np.mean(real))
    return dict(n=len(fills), matched=n_ok, no_candidate=missing, real_mean_matched=rm,
                coin_mean=float(cm.mean()), coin_p5=float(np.percentile(cm, 5)), coin_p95=float(np.percentile(cm, 95)),
                share_ge_real=float((cm >= rm - 1e-12).mean()), copies=copies, seed=seed)


# ------------------------------------------------------------------------------------------------ live comparisons
def _iso_ns(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * NS)


def live_vs_replay(paths: list, cands: list, window_s: float = 2.0) -> dict:
    """Amendment 8: for every live-bot attempt (events.jsonl 'attempt' records), the live outcome of the lock's CHEAPEST
    leg (filled = fill_count >= 1) against the replay's outcome for that same leg at each latency, matching the replay lock
    event by its leg set within +-window_s of the attempt time.  -> {'rows': [...], 'tables': {'L10'|'L40': {(live, replay):
    n}}} with live in {fill, miss}, replay in {fill, miss, no_replay_lock}.  Attempts with an order error/unknown leg are
    counted as 'excluded_error'."""
    by_set = defaultdict(list)
    for c in cands:
        by_set[frozenset(c["legs"])].append(c)
    rows, tables = [], {f"L{lk}": Counter() for lk in LATENCIES}
    excluded = 0
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("event") != "attempt":
                continue
            body_px = {b["ticker"]: float(b["price"]) for b in d.get("bodies", [])}
            lg_ = []
            err = False
            for lg in d.get("legs", []):
                if lg.get("unknown") or not lg.get("ok") or lg["ticker"] not in body_px:
                    err = True
                    break
                side = lg["side"].upper()
                pb = body_px[lg["ticker"]]
                lg_.append(dict(ticker=lg["ticker"], side=side, price=pb if side == "YES" else round(1 - pb, 4),
                                filled=float(lg.get("fill_count") or 0) >= 1.0 - 1e-9, send_ns=lg.get("send_ns")))
            if err or not lg_:
                excluded += 1
                continue
            cheap = min(lg_, key=lambda x: (ticks(x["price"]), x["ticker"], 0 if x["side"] == "YES" else 1))
            at_ns = min((x["send_ns"] for x in lg_ if x["send_ns"]), default=None) or _iso_ns(d["time"])
            key = frozenset(f"{x['side']} {x['ticker']}" for x in lg_)
            near = sorted(((abs(c["t_ns"] - at_ns), c) for c in by_set.get(key, []) if abs(c["t_ns"] - at_ns) <= window_s * NS),
                          key=lambda x: x[0])
            rec = dict(attempt_time=d["time"], kind=d.get("kind"), ticker=cheap["ticker"], side=cheap["side"],
                       live_price=cheap["price"], live_filled=cheap["filled"], n_legs=len(lg_), replay_lock_found=bool(near),
                       nearest_dt_ms=None, replay_cheapest_matches=None)
            live = "fill" if cheap["filled"] else "miss"
            if near:
                c = near[0][1]
                i = c["pick"]["cheapest"]
                rec["nearest_dt_ms"] = round((c["t_ns"] - at_ns) / 1e6, 1)
                rec["replay_cheapest_matches"] = c["legs"][i] == f"{cheap['side']} {cheap['ticker']}"
            for lk in LATENCIES:
                if near:
                    a = near[0][1]["arr"].get((near[0][1]["pick"]["cheapest"], lk))
                    rp = "fill" if (a and a[0]) else "miss"
                else:
                    rp = "no_replay_lock"
                rec[f"replay_L{lk}"] = rp
                tables[f"L{lk}"][(live, rp)] += 1
            rows.append(rec)
    return dict(rows=rows, excluded_error=excluded,
                tables={k: {f"live_{a}|replay_{b}": n for (a, b), n in sorted(v.items())} for k, v in tables.items()})


def lockwatch_overlap(paths: list, opens: list, games: set, window_s: float = 2.0) -> dict:
    """Registered overlap with the server's lockwatch_night lock_open events: leg-set match within +-window_s.
    replay opens = every lock_open the replay saw (any profit, any number of legs)."""
    by_set = defaultdict(list)
    for ns, legs, p1, nl in opens:
        by_set[frozenset(legs)].append(ns)
    lw = []
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("event") == "lock_open" and d.get("game") in games and d.get("open_ns"):
                lw.append(d)
    matched = 0
    used = defaultdict(set)
    for d in lw:
        key = frozenset(d["legs"])
        hit = [ns for ns in by_set.get(key, []) if abs(ns - d["open_ns"]) <= window_s * NS]
        if hit:
            matched += 1
            used[key].update(hit)
    out = dict(lockwatch_lock_opens=len(lw), matched_in_replay=matched, lockwatch_only=len(lw) - matched)
    if lw:
        t0, t1 = min(d["open_ns"] for d in lw), max(d["open_ns"] for d in lw)
        lw_keys = {frozenset(d["legs"]) for d in lw}
        rp = [(ns, legs) for ns, legs, p1, nl in opens if t0 - window_s * NS <= ns <= t1 + window_s * NS]
        rp_match = sum(1 for ns, legs in rp if any(abs(ns - d["open_ns"]) <= window_s * NS for d in lw
                                                   if frozenset(d["legs"]) == frozenset(legs)))
        out.update(replay_opens_in_lockwatch_window=len(rp), replay_matched_in_lockwatch=rp_match,
                   replay_only=len(rp) - rp_match, lockwatch_leg_sets=len(lw_keys))
    return out


# ------------------------------------------------------------------------------------------------ inputs / checks
def check_test_game_set(raw_dirs: list, markets_files: list, games: list, recorded: set) -> None:
    """--label test: the game set must be every game recorded from 26OCT21 on in the given folders - no 26OCT20, no
    hand-picked subset.  Reference = the markets.json that sits next to each raw dir (the recorder's own list)."""
    early = [g for g in games if (night_date(g) or (0, 0, 0)) < TEST_FIRST_NIGHT]
    assert not early, f"games before 26OCT21 (or with an unreadable date) in a test run: {early}"
    ref_games, ref_tickers = set(), set()
    for d in raw_dirs:
        mp = Path(d).parent / "markets.json"
        assert mp.exists(), f"no markets.json next to {d}: cannot verify the test game set"
        doc = json.loads(mp.read_text(encoding="utf-8"))
        for g, info in doc["games"].items():
            ref_games.add(g)
            ref_tickers.update(info["tickers"])
    assert ref_games == set(games), (f"game set differs from the recorded games of the given folders: "
                                     f"missing {sorted(ref_games - set(games))} extra {sorted(set(games) - ref_games)}")
    assert ref_tickers == set(recorded), "recorded ticker set differs from the markets.json files next to the raw dirs"


def stream_name(raw_dir: str) -> str:
    p = Path(raw_dir)
    return p.parent.name if p.name == "raw" else p.name


# ------------------------------------------------------------------------------------------------ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", nargs="+", required=True)
    ap.add_argument("--markets", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", choices=("source", "test"), default="source")
    ap.add_argument("--count-only", action="store_true",
                    help="replay and report ONLY counts (lock events, primary fills, split, gate); no result/settlement/P&L")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--no-coin", action="store_true")
    ap.add_argument("--now", default=None, help="pin 'now' (ISO UTC) for the 7-day rule; default = the clock")
    ap.add_argument("--cache", default=str(HERE / "cache"))
    ap.add_argument("--live-events", nargs="*", default=None,
                    help="live bot events.jsonl file(s) for the live-vs-replay 2x2 (default for --label source: live_26OCT20)")
    ap.add_argument("--lockwatch", nargs="*", default=None, help="lockwatch_night .jsonl file(s) for the overlap report")
    a = ap.parse_args(argv)
    t0 = time.time()
    log = lambda s: print(f"[{time.time() - t0:7.1f}s] {s}", flush=True)  # noqa: E731
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    now_ts = datetime.fromisoformat(a.now.replace("Z", "+00:00")).timestamp() if a.now else time.time()
    if a.live_events is None and a.label == "source":
        a.live_events = [str(ROOT / "reports/output/ohio_trial/live_26OCT20/events.jsonl")]

    # ---- inputs
    recorded, kick_cands = set(), defaultdict(list)
    for mf in a.markets:
        d = json.loads(Path(mf).read_text(encoding="utf-8"))
        for g, info in d["games"].items():
            recorded.update(info["tickers"])
            kick_cands[g].append(info.get("kickoff_raw"))
    games = sorted(kick_cands)
    if a.label == "test":
        check_test_game_set(a.raw, a.markets, games, recorded)
    snames = [stream_name(d) for d in a.raw]

    # ---- metadata (structure; status; results only when not --count-only, and never read before the gate)
    markets, einfo = fetch_meta(recorded, Path(a.cache), not a.no_fetch, log, strip=a.count_only)
    status_of = {t: m.get("status") for t, m in markets.items()}
    tips, tip_src, tip_disagree = derive_tips(games, kick_cands, markets)
    log(f"{len(games)} games, {len(recorded)} recorded tickers, {len(markets)} markets known to the API")
    doc = build_doc(recorded, markets, einfo)

    files = []
    from kpm.analysis.ws_books import raw_files
    from kpm.analysis.ws_sim import _is_smoke, _stream
    for d in a.raw:
        for p in raw_files(d):
            if not _is_smoke(p):
                files.append({"path": str(p), "bytes": p.stat().st_size})

    # ---- replay
    coin = not a.no_coin and not a.count_only
    rp = Replay(doc, tips, coin=coin, stream_names=snames)
    silences: dict = {}
    nl = 0
    for line in _stream(list(a.raw), silences):
        rp.feed_line(line)
        nl += 1
        if nl % 5_000_000 == 0:
            log(f"{nl / 1e6:.0f}M lines, {rp.n_open} lock_open, {len(rp.cands)} candidates")
    rp.finish()
    log(f"replay done: {nl} lines, {rp.n_open} lock_open ({rp.n_below} below $0.01 at count 1, {rp.n_gt3} with > {MAX_LEGS} legs "
        f"dropped), {len(rp.cands)} candidates")

    # ---- fills (no result read) and the gate (metadata only)
    order = sorted(games, key=lambda g: (tips[g], g))
    disc, unseen = split_games(order)
    rows_by_v, devents = build_rows(rp.cands, tips, None, now_ts, stream_names=snames)
    multi_rows, mevents = build_rows(rp.cands, tips, None, now_ts, which="multi", stream_names=snames)
    pfills = primary_fills(rows_by_v[PRIMARY])
    gate = gate_check(order, tips, pfills, status_of, now_ts) if a.label == "test" else None
    fpg = Counter(r["game"] for r in pfills)
    counts = dict(label=a.label, count_only=a.count_only, primary=PRIMARY, now=datetime.fromtimestamp(now_ts, timezone.utc).isoformat(),
                  lines=nl, lines_out_of_order=rp.out_of_order, lock_open_total=rp.n_open, below_min_profit=rp.n_below,
                  dropped_gt3_legs=rp.n_gt3, dropped_gt3_legs_would_qualify=rp.n_gt3_qualifying, candidates=len(rp.cands),
                  deduped_events_single_stream=len(devents), deduped_events_multi_stream=len(mevents),
                  primary_events=len([r for r in rows_by_v[PRIMARY] if not r["refire_skipped"]]),
                  primary_fills=len(pfills), games=order, discovery_games=disc, unseen_games=unseen,
                  primary_fills_per_game={g: fpg.get(g, 0) for g in order},
                  lock_events_per_game=dict(Counter(e["game"] for e in devents)),
                  unseen_primary_fills=sum(fpg.get(g, 0) for g in unseen),
                  discovery_primary_fills=sum(fpg.get(g, 0) for g in disc), gate=gate)
    if a.count_only or (gate is not None and not gate["ok"]):
        # counts / gate only: nothing derived from a result, settlement or P&L exists at this point
        name = "counts.json" if a.count_only else "gate.json"
        (out / name).write_text(json.dumps(counts, indent=1, default=str))
        print(f"\nlabel={a.label} {'COUNT-ONLY' if a.count_only else 'GATE'} lock_open={rp.n_open} candidates={len(rp.cands)} "
              f"deduped={len(devents)} multi_stream={len(mevents)} dropped>3legs={rp.n_gt3}")
        print(f"primary ({PRIMARY}) fills={len(pfills)} per game: {dict(sorted(fpg.items()))}")
        print(f"would-be split: discovery {len(disc)} games / {counts['discovery_primary_fills']} fills; "
              f"unseen {len(unseen)} games / {counts['unseen_primary_fills']} fills")
        if gate is not None:
            print(f"gate: {'MET' if gate['ok'] else 'NOT MET'} games={gate['games']} (need >= {GATE_MIN_GAMES}) "
                  f"unseen_games={gate['unseen_games']} (>= {GATE_MIN_UNSEEN}) unseen_primary_fills={gate['unseen_primary_fills']} "
                  f"(>= {GATE_MIN_UNSEEN_FILLS}) games_not_gate_met={gate['games_not_gate_met']}")
        else:
            print("gate: not applicable (--label source)")
        if not a.count_only:
            print("GATE NOT MET - no statistic computed.")
            return 3
        return 0

    # ---- gate passed (or source night): results are read from here on
    tip_vs_settle = check_tip_vs_settle(games, tips, markets)
    attach_outcomes(rows_by_v, markets)
    attach_outcomes(multi_rows, markets)
    if a.label == "test":
        sets = {"overall": order, "discovery": disc, "unseen": unseen}
    else:
        sets = {"overall": order}
    mx_rows, _ = build_rows(rp.cands, tips, markets, now_ts, rep="maxprofit", versions=[PRIMARY], stream_names=snames)

    pools = CoinPools(rp.res, markets) if coin else None
    versions = {}
    for v in VERSIONS:
        rows = rows_by_v[v]
        vv = {}
        for sname, sg in sets.items():
            s = stats_for(rows, sg)
            if pools is not None:
                gs = set(sg)
                fl = [r for r in rows if r["game"] in gs and r["filled"] and not r["refire_skipped"] and r["status"] == "settled"]
                s["coin_flip"] = coin_flip(fl, pools)
            vv[sname] = s
        vv["by_price_bucket"] = breakdown(rows, lambda r: price_bucket(r["fill_price"]))
        vv["by_market_type"] = breakdown(rows, lambda r: r["mtype"])
        vv["by_kind"] = breakdown(rows, lambda r: r["kind"])
        vv["by_phase"] = breakdown(rows, lambda r: r["phase"])
        f2 = Counter(r["game"] for r in rows if r["filled"] and not r["refire_skipped"])
        vv["fills_per_game"] = {g: f2.get(g, 0) for g in order}
        vv["lock_events_per_game"] = dict(Counter(r["game"] for r in rows if not r["refire_skipped"]))
        vv["multi_stream_events_excluded"] = stats_for(multi_rows[v], order)
        versions[v] = vv
    robust = {sname: stats_for(mx_rows[PRIMARY], sg) for sname, sg in sets.items()}

    # coverage
    cov = {}
    for g in games:
        rec_g = [t for t in recorded if lc.game_of(t) == g]
        listed_g = [t for t in markets if lc.game_of(t) == g]
        cov[g] = dict(recorded=len(rec_g), listed_by_api=len(listed_g), with_book_events=sum(1 for t in rec_g if t in rp.seen_top),
                      recorded_not_in_api=sum(1 for t in rec_g if t not in markets),
                      unsettled_markets=sum(1 for t in rec_g if (markets.get(t) or {}).get("result") not in ("yes", "no")),
                      tip_iso=datetime.fromtimestamp(tips[g] / NS, timezone.utc).isoformat(), tip_source=tip_src[g])
    res = dict(label=a.label, config=dict(primary=PRIMARY, versions=VERSIONS, latencies=LATENCIES, min_profit=MIN_PROFIT,
                                          refire_s=REFIRE_S, dedupe_ms=lc.DEDUP_WINDOW_MS, dedupe_rep="earliest", max_legs=MAX_LEGS,
                                          seed=SEED, draws=DRAWS, copies=COPIES, games_per_week=GAMES_PER_WEEK,
                                          coin_method="segment counts (exact per-second samples)", coin=coin,
                                          now=counts["now"], args=vars(a)),
               inputs=dict(raw_files=files, total_bytes=sum(f["bytes"] for f in files), markets_files=a.markets,
                           streams=snames),
               replay=dict(lines=nl, lines_out_of_order=rp.out_of_order, lock_open_total=rp.n_open,
                           below_min_profit=rp.n_below, dropped_gt3_legs=rp.n_gt3, dropped_gt3_legs_would_qualify=rp.n_gt3_qualifying,
                           candidates=len(rp.cands), deduped_events=len(devents), multi_stream_events=len(mevents),
                           arrival_outcomes=dict(rp.arrival), trigger_not_found=rp.n_noleg,
                           tickers_in_two_streams=sorted(rp.two_streams), silences=silences, coin_samples=rp.n_samples,
                           by_kind=dict(Counter(e["kind"] for e in devents)), legs_hist=dict(Counter(e["n_legs"] for e in devents)),
                           runtime_s=None),
               games=dict(order=order, discovery=sets.get("discovery"), unseen=sets.get("unseen")),
               tips=dict(source=tip_src, disagreements=tip_disagree, settle_minus_tip_h=tip_vs_settle),
               counts=counts, coverage=cov, versions=versions, robustness_maxprofit_primary=robust)

    # pass bar
    if a.label == "test":
        P = versions[PRIMARY]
        d_, u_ = P["discovery"], P["unseen"]
        ci = (u_["bootstrap"] or {}).get("ci_mean_per_fill")
        cf = (u_.get("coin_flip") or {}).get("share_ge_real")
        rules = {
            "1_mean_pnl_positive_discovery_and_unseen": bool((d_["mean_pnl_per_fill"] or 0) > 0 and (u_["mean_pnl_per_fill"] or 0) > 0),
            "2_unseen_ci_lower_gt_0": bool(ci and ci[0] > 0),
            "3_unseen_fills_ge_200": u_["settled_fills"] >= 200,
            "4_coin_share_le_5pct": None if cf is None else cf <= 0.05,
            "5_dollars_per_week_ge_2": bool((u_["dollars_per_week"] or 0) >= 2),
        }
        res["pass_bar"] = dict(gate=gate, rules=rules, verdict="PASS" if all(v for v in rules.values()) else "FAIL")
    else:
        res["pass_bar"] = dict(verdict="source night (not counted)")

    if a.live_events:
        paths = [p for p in a.live_events if Path(p).exists()]
        res["live_vs_replay_2x2"] = live_vs_replay(paths, rp.cands)
    if a.lockwatch:
        res["lockwatch_overlap"] = lockwatch_overlap([p for p in a.lockwatch if Path(p).exists()], rp.opens, set(games))

    res["replay"]["runtime_s"] = round(time.time() - t0, 1)
    (out / "results.json").write_text(json.dumps(res, indent=1, default=str))
    cols = ["version", "time", "t_ns", "game", "phase", "kind", "mtype", "ticker", "side", "lock_price", "refire_skipped", "filled",
            "miss_reason", "fill_price", "fee", "result", "status", "win", "pnl", "trigger", "is_cheapest", "is_stale",
            "is_trigger", "n_legs", "lock_profit1", "streams", "multi_stream"]
    for fname, rbv in (("fills.csv", rows_by_v), ("fills_multistream.csv", multi_rows)):
        with open(out / fname, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for v in VERSIONS:
                for r in rbv[v]:
                    r = dict(r, time=datetime.fromtimestamp(r["t_ns"] / NS, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")
                    w.writerow({k: r.get(k) for k in cols})

    # ---- summary
    print(f"\nlabel={a.label} lock_open={rp.n_open} candidates(>= $0.01, <= 3 legs)={len(rp.cands)} deduped={len(devents)} "
          f"multi_stream_excluded={len(mevents)} dropped>3legs={rp.n_gt3} runtime={res['replay']['runtime_s']}s")
    for v in VERSIONS:
        s = versions[v]["overall"]
        cfs = (s.get("coin_flip") or {}).get("share_ge_real")
        bs = (s["bootstrap"] or {}).get("ci_mean_per_fill")
        print(f"  {v:13s} events={s['lock_events']:6d} fills={s['fills']:6d} rate={s['fill_rate'] or 0:.3f} "
              f"meanPnL={s['mean_pnl_per_fill'] if s['mean_pnl_per_fill'] is None else round(s['mean_pnl_per_fill'], 4)} "
              f"win={s['win_rate'] if s['win_rate'] is None else round(s['win_rate'], 4)} "
              f"price={s['mean_price'] if s['mean_price'] is None else round(s['mean_price'], 4)} "
              f"CI={None if not bs else [round(x, 4) for x in bs]} coin={cfs}")
    print("verdict:", res["pass_bar"]["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
