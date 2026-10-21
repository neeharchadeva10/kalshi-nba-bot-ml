"""Maker simulator for research log #26 (reports/output/maker_ws/PREREGISTRATION.md): quote thin win-margin buckets
around the deep fair value, strict fill model (back of queue, trade fills, cross fills), hold to settlement.

Reuses the #22/#23 replay (``ws_sim``: books per raw dir merged by time, Universe, fair values, validity rules, settlement
fetch) and the statistics helpers (``ws_sim_stats``). Prices are integer 1/10000-dollar ticks inside, dollars in rows.

Interpretations where the pre-registration is silent (always the less optimistic reading) are listed in ``INTERPRETATIONS``
and written into every summary.json.
"""
from __future__ import annotations

import json
import math
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import numpy as np

from kpm.analysis import ws_sim as ws
from kpm.analysis import ws_sim_stats as st
from kpm.analysis.ws_books import Top, Trade

NS, EPS = ws.NS, ws.EPS
LATENCY_S = 1.0                      # placements and cancels take effect 1 s after the decision
CLOSE_CUT_S = 60                     # no quotes in the last 60 s before a market closes
SIZE, MAX_NET = 10, 10
TICKS = 10000
PRIMARY = "M-d2-2S"                  # NBA, delta 2c, two-sided
MARKOUTS = st.MARKOUT_S              # (10, 60, 300)
MARKOUT_FLOOR = -0.02                # rule 6: mean fair markout at +60 s must not be worse than -2c

INTERPRETATIONS = [
    "close time: meta[ticker].close_time if present, else the game's kickoff_raw.expected_expiration_time, else kickoff + 3 h "
    "(the 3 h offset the collector uses to derive kickoff); none known -> no close cut. These are expected ends, not real "
    "closes, so a late game is cut early (less optimistic).",
    "naive strategy: fair = the thin bucket's own (bid+ask)/2, needs both sides and no outage in the minute (a wide thin spread does not block it); it is NOT gated by "
    "the deep inputs' validity (it gets every chance the deep version gets and more).",
    "requote triggers only on: first valid fair, fair moved >= 1c from the last decision fair, or a fill. A quote skipped "
    "because it would cross is not retried until a trigger; an order that would cross at go-live is dropped, not filled.",
    "an unchanged price and size is kept (queue position kept); any change cancels (after L) and places a new back-of-queue order.",
    "a one-sided or empty opposite side counts as 'cannot cross'; prices outside 1c..99c are not quoted.",
    "cancel-in-flight: an order stays fillable until its cancel takes effect (decision + L); an order whose cancel takes "
    "effect at or before go-live never goes live.",
    "inventory is enforced at fill time (fill clamped to the room left), so stale orders cannot breach +-10.",
    "queue ahead only shrinks by public trades at or through our price (cancels ahead of us are not credited).",
    "outage (books dropped): all orders on that ticker die at once and quoting waits for the next trigger.",
    "markouts: own-side value minus our price, using the deep fair (thin mid markout also reported) for both the primary and "
    "the naive twin; a markout with no valid fair/mid at that time is excluded from the mean (coverage reported).",
    "rule 4: per-contract mean of primary minus naive on unseen (naive with no fills counts 0), bootstrap over unseen "
    "games paired between the two, 10,000 draws, seed 20261020; a game with no contracts contributes 0.",
    "rule 6: the contract-weighted mean fair markout at +60 s must be >= -2c over all fills and also over unseen fills.",
    "rule 1/3: 'filled contracts' = contracts filled in markets with a public result; unsettled markets are dropped whole.",
    "an unseen/discovery game is any game with at least one top change of a target market (as in ws-sim).",
    "the NFL run reports the same primary id for its verdict, but the pre-registered primary is NBA; NFL is secondary.",
]


# ------------------------------------------------------------------------------------------------ quote prices
def bid_cents(fair: float, delta: float) -> int:
    """YES bid: floor-to-cent(fair - delta)."""
    return math.floor((fair - delta) * 100 + EPS)


def no_bid_cents(fair: float, delta: float) -> int:
    """NO bid: floor-to-cent((1 - fair) - delta); the YES ask is 100 minus this = ceil-to-cent(fair + delta)."""
    return math.floor(((1 - fair) - delta) * 100 + EPS)


# ------------------------------------------------------------------------------------------------ versions
@dataclass(frozen=True)
class MVersion:
    vid: str
    delta: float
    two_sided: bool
    naive: bool = False              # fair := the thin market's own mid


def versions_for(sport: ws.Sport) -> list[MVersion]:
    """delta {1,2,3}c x {two-sided, bid-only}: 6 per sport (the sport argument only documents that it is the same grid)."""
    return [MVersion(f"M-d{d}-{'2S' if two else 'BID'}", d / 100, two) for d in (1, 2, 3) for two in (True, False)]


def strategies(vs: list[MVersion]) -> list[MVersion]:
    """Each version plus its naive twin (same delta, same rules)."""
    return [x for v in vs for x in (v, MVersion(v.vid + "-N", v.delta, v.two_sided, True))]


# ------------------------------------------------------------------------------------------------ state
@dataclass(eq=False)
class MState:
    """One strategy in one market."""
    strat: MVersion
    target: ws.Target
    net: int = 0                     # net YES position
    fair_ref: float | None = None
    dirty: bool = False
    orders: dict = field(default_factory=lambda: {"yes": None, "no": None})
    timeline: list = field(default_factory=list)       # (ns, net after) for inventory statistics


@dataclass(eq=False)
class QOrder:
    st: MState
    side: str                        # 'yes' = YES bid, 'no' = NO bid (a YES ask)
    ticks: int                       # own-side price
    size: int
    live_ns: int
    fair_ref: float
    cancel_ns: int | None = None
    live: bool = False
    dead: bool = False
    ahead: float = 0.0
    vol: float = 0.0
    filled: int = 0


@dataclass(eq=False)
class FillRec:
    strat: str
    target: ws.Target
    side: str
    count: int
    ticks: int
    fill_ns: int
    kind: str                        # trade | cross
    fair_dec: float
    mo: dict = field(default_factory=dict)


class MakerSim(ws.Sim):
    """ws_sim's replay with the #26 maker instead of the #22 strategies (no taker versions, no #22 makers)."""

    def __init__(self, universe: ws.Universe, strats: list[MVersion], close_ns: dict[str, int] | None = None,
                 markouts: bool = True):
        super().__init__(universe, [], markouts=markouts)
        self.strats = list(strats)
        self.close_ns = close_ns or {}
        self.mstate: dict[tuple, MState] = {}
        self.live_orders: dict[str, list[QOrder]] = defaultdict(list)
        self.fills_rec: list[FillRec] = []
        self.placed = Counter()
        self.outage_min: dict[str, int] = {}                        # ticker -> minute of its last outage (books dropped)
        for t, c in self.close_ns.items():
            if t in universe.targets:
                self._push(c - CLOSE_CUT_S * NS, "mk2_cut", t)

    # ---- fair value of a strategy
    def _thin_mid(self, t: str, top: Top | None, ns: int) -> float | None:
        if top is None or t not in self.bs(t).sid_of or self.outage_min.get(t) == ns // ws.MIN_NS:
            return None                                             # a wide thin spread (bad_min) does not block the naive mid
        if top.best_yes_bid is None or top.best_yes_ask is None or top.best_yes_ask < top.best_yes_bid:
            return None
        return (top.best_yes_bid + top.best_yes_ask) / 2

    # ---- decisions
    def _evaluate(self, tg: ws.Target, ns: int) -> None:
        if tg.family != "A":
            return
        t = tg.ticker
        top = self.tops.get(t)
        live = top is not None and t in self.bs(t).sid_of
        deep = self.fair(tg, ns) if live else None
        thin = self._thin_mid(t, top, ns)
        for s in self.strats:
            self._decide(s, tg, thin if s.naive else deep, top, ns)

    def _state(self, s: MVersion, tg: ws.Target) -> MState:
        key = (s.vid, tg.ticker)
        if key not in self.mstate:
            self.mstate[key] = MState(s, tg)
        return self.mstate[key]

    def _cancel(self, o: QOrder | None, ns: int) -> None:
        if o is not None and not o.dead and o.cancel_ns is None:
            o.cancel_ns = ns + int(LATENCY_S * NS)

    def _in_cut(self, t: str, ns: int) -> bool:
        c = self.close_ns.get(t)
        return c is not None and ns >= c - CLOSE_CUT_S * NS

    def _decide(self, s: MVersion, tg: ws.Target, fair: float | None, top: Top | None, ns: int) -> None:
        stt = self._state(s, tg)
        t = tg.ticker
        if fair is None or top is None or self._in_cut(t, ns):
            for side in ("yes", "no"):
                self._cancel(stt.orders[side], ns)
                stt.orders[side] = None
            stt.fair_ref = None
            return
        if not (stt.fair_ref is None or stt.dirty or abs(fair - stt.fair_ref) >= 0.01 - EPS):
            return
        stt.dirty, stt.fair_ref = False, fair
        bid_t = None if top.best_yes_bid is None else round(top.best_yes_bid * TICKS)
        ask_t = None if top.best_yes_ask is None else round(top.best_yes_ask * TICKS)
        want = {"yes": (bid_cents(fair, s.delta), SIZE - stt.net)}
        if s.two_sided:
            want["no"] = (no_bid_cents(fair, s.delta), SIZE + stt.net)
        for side in ("yes", "no"):
            cur = stt.orders[side]
            if side not in want:
                continue
            cents, room = want[side]
            size = min(SIZE, room)
            ticks = cents * 100
            ok = 1 <= cents <= 99 and size >= 1
            if ok and (ask_t is not None and side == "yes" and ticks >= ask_t):
                ok = False                                          # would cross the opposite best: not placed
            if ok and (bid_t is not None and side == "no" and TICKS - ticks <= bid_t):
                ok = False
            if cur is not None and ok and cur.ticks == ticks and cur.size - cur.filled == size:
                cur.fair_ref = fair                                 # unchanged: keep the queue position
                continue
            self._cancel(cur, ns)
            stt.orders[side] = None
            if not ok:
                continue
            o = QOrder(stt, side, ticks, size, ns + int(LATENCY_S * NS), fair)
            stt.orders[side] = o
            self.placed[s.vid] += 1
            self._push(o.live_ns, "mk2_live", o)

    def _do_mk2_live(self, o: QOrder, ns: int) -> None:
        t = o.st.target.ticker
        top = self.tops.get(t)
        if o.dead or (o.cancel_ns is not None and o.cancel_ns <= ns) or not self._live(t) or top is None:
            o.dead = True
            return
        if o.side == "yes":
            crossed = top.best_yes_ask is not None and round(top.best_yes_ask * TICKS) <= o.ticks
            book = self.bs(t).yes[t]
        else:
            crossed = top.best_yes_bid is not None and TICKS - round(top.best_yes_bid * TICKS) <= o.ticks
            book = self.bs(t).no[t]
        if crossed:
            o.dead = True                                           # would cross at go-live: dropped
            return
        o.ahead = book.get(o.ticks, 0.0)                            # back of the queue at our price
        o.live = True
        self.live_orders[t].append(o)

    def _do_mk2_cut(self, t: str, ns: int) -> None:
        for s in self.strats:
            stt = self.mstate.get((s.vid, t))
            if stt:
                for side in ("yes", "no"):
                    self._cancel(stt.orders[side], ns)
                    stt.orders[side] = None
                stt.fair_ref = None

    # ---- fills
    def _on_trade(self, tr: Trade) -> None:
        t = tr.ticker
        if t not in self.u.targets:
            return
        tp = round(tr.yes_price * TICKS)
        hit = False
        for o in list(self.live_orders.get(t, ())):
            if o.dead or (o.cancel_ns is not None and tr.recv_ns >= o.cancel_ns):
                self.live_orders[t].remove(o)
                continue
            if o.side == "yes":
                match = tr.taker_side == "no" and tp <= o.ticks             # a sell hits our bid or goes through it
            else:
                match = tr.taker_side == "yes" and tp >= TICKS - o.ticks    # a buy lifts our YES ask or goes through it
            if not match:
                continue
            o.vol += tr.count
            n = min(o.size, int(o.vol - o.ahead + EPS)) - o.filled
            if n >= 1:
                hit |= self._fill(o, n, tr.recv_ns, "trade")
        if hit:
            self._evaluate(self.u.targets[t], tr.recv_ns)

    def _on_top(self, top: Top) -> None:
        t, ns = top.ticker, top.recv_ns
        for o in list(self.live_orders.get(t, ())):
            if o.dead or (o.cancel_ns is not None and ns >= o.cancel_ns):
                self.live_orders[t].remove(o)
                continue
            if o.side == "yes":
                cross = top.best_yes_ask is not None and round(top.best_yes_ask * TICKS) <= o.ticks
            else:
                cross = top.best_yes_bid is not None and TICKS - round(top.best_yes_bid * TICKS) <= o.ticks
            if cross:
                self._fill(o, o.size - o.filled, ns, "cross")
                o.dead = True
                self.live_orders[t].remove(o)
        super()._on_top(top)                                        # re-evaluates (a fill set dirty -> requote)

    def _fill(self, o: QOrder, n: int, ns: int, kind: str) -> bool:
        stt = o.st
        room = MAX_NET - stt.net if o.side == "yes" else MAX_NET + stt.net
        n = min(n, room, o.size - o.filled)
        if n <= 0:
            return False
        o.filled += n
        stt.net += n if o.side == "yes" else -n
        stt.timeline.append((ns, stt.net))
        stt.dirty = True
        if o.filled >= o.size:
            o.dead = True
        rec = FillRec(stt.strat.vid, stt.target, o.side, n, o.ticks, ns, kind, o.fair_ref)
        self.fills_rec.append(rec)
        self.fills[stt.strat.vid] += n
        if self.markouts:
            for h in MARKOUTS:
                self._push(ns + h * NS, "mo2", (rec, h))
        return True

    def _do_mo2(self, item, ns: int) -> None:
        rec, h = item
        t = rec.target.ticker
        top = self.tops.get(t)
        live = self._live(t)
        mid = None
        if top is not None and live and top.best_yes_bid is not None and top.best_yes_ask is not None:
            mid = (top.best_yes_bid + top.best_yes_ask) / 2
        fair = self.fair(rec.target, ns) if live else None
        px, flip = rec.ticks / TICKS, rec.side == "no"
        val = lambda x: None if x is None else round((1 - x if flip else x) - px, 6)
        rec.mo[h] = (val(mid), val(fair))

    def _on_dropped(self, ns: int, bs) -> None:
        dropped = list(bs.dropped)
        super()._on_dropped(ns, bs)
        for t in dropped:
            self.outage_min[t] = ns // ws.MIN_NS
        for t in dropped:
            for o in self.live_orders.get(t, ()):
                o.dead = True                                       # outage: orders on this ticker die at once
            self.live_orders.pop(t, None)
            for tg in self.u.deps.get(t, ()):
                self._evaluate(tg, ns)                              # a lost input cancels (after L) the dependents' quotes
            for s in self.strats:
                stt = self.mstate.get((s.vid, t))
                if stt:
                    for side in ("yes", "no"):
                        if stt.orders[side] is not None:
                            stt.orders[side].dead = True
                        stt.orders[side] = None
                    stt.fair_ref = None


# ------------------------------------------------------------------------------------------------ rows and statistics
def bucket_label(sp: ws.Sport, ticker: str) -> str:
    m = sp.bucket_re.fullmatch(ticker)
    if not m:
        return "?"
    return f"{m['lo']}TO{m['hi']}" if m["hi"] else f"{m['lo']}PLUS"


def settle_rows(sim: MakerSim, results: dict[str, str], sp: ws.Sport) -> tuple[list[dict], Counter]:
    """One row per fill in a market with a public result: net = (payout - our price) * count, payout $1 for the winning side.
    Fills in unsettled markets are dropped whole and counted per strategy."""
    rows, dropped = [], Counter()
    for f in sim.fills_rec:
        r = results.get(f.target.ticker)
        if r not in ("yes", "no"):
            dropped[f.strat] += f.count
            continue
        px = f.ticks / TICKS
        net = ((1.0 if r == f.side else 0.0) - px) * f.count
        row = {"version": f.strat, "game": f.target.game, "ticker": f.target.ticker, "bucket": bucket_label(sp, f.target.ticker),
               "side": f.side, "count": f.count, "price": round(px, 4), "fill_ns": f.fill_ns, "kind": f.kind,
               "fair_dec": f.fair_dec, "result": r, "gross": round(net, 6), "net": round(net, 6)}
        for h in MARKOUTS:
            thin, fair = f.mo.get(h, (None, None))
            row[f"mo_thin_{h}"], row[f"mo_fair_{h}"] = thin, fair
        rows.append(row)
    return rows, dropped


def _wmean(rows: list[dict], key: str) -> tuple[float | None, int]:
    """Contract-weighted mean of a markout column and the number of contracts that had a value."""
    pairs = [(r[key], r["count"]) for r in rows if r.get(key) is not None]
    n = sum(c for _, c in pairs)
    return (sum(v * c for v, c in pairs) / n if n else None), n


def completed_pairs(rows: list[dict]) -> int:
    yes, no = Counter(), Counter()
    for r in rows:
        (yes if r["side"] == "yes" else no)[r["ticker"]] += r["count"]
    return sum(min(yes[t], no[t]) for t in yes.keys() & no.keys())


def inventory_stats(sim: MakerSim, vid: str) -> dict:
    """Max |net| and the time-weighted mean |net| over each market's observed window (first to last top change)."""
    mx, area, span = 0, 0.0, 0.0
    for (v, t), stt in sim.mstate.items():
        if v != vid or t not in sim.window:
            continue
        lo, hi = sim.window[t]
        cur, last = 0, lo
        for ns, net in stt.timeline:
            ns = min(max(ns, lo), hi)
            area, last = area + abs(cur) * (ns - last), ns
            cur = net
            mx = max(mx, abs(net))
        area += abs(cur) * (hi - last)
        span += hi - lo
    return {"max_abs_net": mx, "time_weighted_mean_abs_net": area / span if span else None}


def version_row(vid: str, rows: list[dict], sim: MakerSim, disc: set, uns: set, games: int, sp: ws.Sport) -> dict:
    p = st.pooled(rows)
    out = {"version": vid, **p, "orders": sim.placed[vid], "fills_contracts": sim.fills[vid],
           "fills_per_game": p["contracts"] / games if games else None}
    for name, seg in (("discovery", disc), ("unseen", uns)):
        q = st.pooled([r for r in rows if r["game"] in seg])
        out.update({f"{name}_trades": q["trades"], f"{name}_contracts": q["contracts"], f"{name}_net": q["net"],
                    f"{name}_net_per_contract": q["net_per_contract"]})
    tot = max(p["contracts"], 1)
    out["trade_fill_share"] = sum(r["count"] for r in rows if r["kind"] == "trade") / tot if rows else None
    out["cross_fill_share"] = sum(r["count"] for r in rows if r["kind"] == "cross") / tot if rows else None
    out["trade_fills"] = sum(1 for r in rows if r["kind"] == "trade")
    out["cross_fills"] = sum(1 for r in rows if r["kind"] == "cross")
    for h in MARKOUTS:
        out[f"markout_fair_{h}s"], out[f"markout_fair_{h}s_contracts"] = _wmean(rows, f"mo_fair_{h}")
        out[f"markout_thin_{h}s"], _ = _wmean(rows, f"mo_thin_{h}")
    out["markout_settlement"] = p["net_per_contract"]
    out["pairs_completed"] = completed_pairs(rows)
    out.update({f"inventory_{k}": v for k, v in inventory_stats(sim, vid).items()})
    by = defaultdict(list)
    for r in rows:
        by[r["bucket"]].append(r)
    out["by_bucket"] = {b: st.pooled(rs) for b, rs in sorted(by.items())}
    return out


def bootstrap_diff(a: dict[str, tuple[float, float]], b: dict[str, tuple[float, float]], games: list[str],
                   draws: int = st.DRAWS, seed: int = st.SEED) -> tuple[float, float] | None:
    """95% interval of (a net/contract - b net/contract), resampling whole games jointly; a side with no contracts counts 0."""
    if not games:
        return None
    games = sorted(games)
    an = np.array([a.get(g, (0.0, 0.0))[0] for g in games]); ac = np.array([a.get(g, (0.0, 0.0))[1] for g in games])
    bn = np.array([b.get(g, (0.0, 0.0))[0] for g in games]); bc = np.array([b.get(g, (0.0, 0.0))[1] for g in games])
    idx = np.random.default_rng(seed).integers(0, len(games), size=(draws, len(games)))

    def stat(n, c):
        s, k = n[idx].sum(axis=1), c[idx].sum(axis=1)
        return np.where(k > 0, s / np.where(k > 0, k, 1.0), 0.0)

    d = stat(an, ac) - stat(bn, bc)
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def verdict(prim: list[dict], naive: list[dict], discovery: set, unseen: set, *, draws: int = st.DRAWS,
            seed: int = st.SEED, games_per_week: int, min_games: int, min_unseen: int, lines_out_of_order: int = 0,
            tickers_in_two_streams: int = 0) -> dict:
    """Pass bar rules 1-6 of the #26 pre-registration for the primary version's rows (``naive`` = its naive twin's rows)."""
    if lines_out_of_order:
        return {"verdict": "INVALID: lines out of order", "lines_out_of_order": lines_out_of_order}
    if tickers_in_two_streams:
        return {"verdict": "INVALID: a ticker appears in two raw dirs", "tickers_in_two_streams": tickers_in_two_streams}
    n_games = len(discovery) + len(unseen)
    if n_games < min_games or len(unseen) < min_unseen:
        return {"verdict": "NOT ENOUGH GAMES", "games": n_games, "unseen_games": len(unseen),
                "need": f">= {min_games} games and >= {min_unseen} unseen"}
    dis = st.pooled([r for r in prim if r["game"] in discovery])
    u_rows = [r for r in prim if r["game"] in unseen]
    uns = st.pooled(u_rows)
    n_rows = [r for r in naive if r["game"] in unseen]
    nai = st.pooled(n_rows)
    pg, ng = st.per_game(u_rows), st.per_game(n_rows)
    lb = st.bootstrap_lower({g: (0.0, 0.0) for g in unseen} | pg, draws, seed)      # games without fills count as 0
    diff = None if uns["net_per_contract"] is None else uns["net_per_contract"] - (nai["net_per_contract"] or 0.0)
    ci = bootstrap_diff(pg, ng, sorted(unseen), draws, seed)
    weekly = uns["net"] / len(unseen) * games_per_week
    mo_all, _ = _wmean(prim, "mo_fair_60")
    mo_uns, _ = _wmean(u_rows, "mo_fair_60")
    rules = {
        "1_positive_discovery_and_unseen": bool((dis["net_per_contract"] or 0) > 0 and (uns["net_per_contract"] or 0) > 0),
        "2_unseen_bootstrap_lower_gt_0": bool(lb is not None and lb > 0),
        "3_unseen_filled_contracts_ge_100": uns["contracts"] >= 100,
        "4_primary_minus_naive_gt_0": bool(diff is not None and diff > 0),
        "5_weekly_ge_2": bool(weekly >= 2),
        "6_fair_markout_60s_ge_minus_2c": bool(mo_all is not None and mo_all >= MARKOUT_FLOOR - EPS
                                               and (mo_uns is None or mo_uns >= MARKOUT_FLOOR - EPS)),
    }
    return {"verdict": "PASS" if all(rules.values()) else "FAIL", "rules": rules, "games": n_games,
            "unseen_games": len(unseen), "discovery": dis, "unseen": uns, "naive_unseen": nai,
            "bootstrap_lower_95": lb, "primary_minus_naive_per_contract": diff, "primary_minus_naive_ci95": ci,
            "projected_per_week": weekly, "markout_fair_60s_all": mo_all, "markout_fair_60s_unseen": mo_uns,
            "ci_note": f"bootstrap over {len(unseen)} unseen games, {draws} draws, seed {seed}"}


# ------------------------------------------------------------------------------------------------ orchestration
def close_times(docs: list[dict]) -> dict[str, int]:
    """ticker -> close time in ns (see INTERPRETATIONS[0]); tickers with no usable time are absent."""
    out: dict[str, int] = {}
    for doc in docs:
        for info in doc["games"].values():
            meta = info.get("meta") or {}
            raw = info.get("kickoff_raw") or {}
            game_end = ws._iso_ns(raw.get("expected_expiration_time"))
            if game_end is None and info.get("kickoff"):
                game_end = ws._iso_ns(info["kickoff"]) + int(timedelta(hours=3).total_seconds() * NS)
            for t in info["tickers"]:
                c = ws._iso_ns((meta.get(t) or {}).get("close_time")) or game_end
                if c is not None:
                    out[t] = c
    return out


def run(raw_dirs: list[str], markets_files: list[str], out_dir: str | Path, *, versions: str = "primary",
        results: dict[str, str] | None = None, fetch: bool = True, client=None, draws: int = st.DRAWS,
        seed: int = st.SEED, sport: str = "nba") -> dict:
    t0 = time.time()
    sp = ws.SPORTS[sport]
    docs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in markets_files]
    u = ws.Universe(docs, sp)
    vs = [v for v in versions_for(sp) if versions == "all" or v.vid == PRIMARY]
    sim = MakerSim(u, strategies(vs), close_times(docs))
    silences: dict = {}
    sim.run(ws._stream(raw_dirs, silences))

    if results is None:
        results = {}
        need = {f.target.ticker for f in sim.fills_rec}
        for mf in markets_files:
            cache_path = Path(mf).parent / "results.json"
            cache = ws.load_results(cache_path)
            if fetch:
                if client is None:
                    from kpm.clients.kalshi import KalshiClient
                    client = KalshiClient()
                ws.fetch_results(need, cache, client)
                cache_path.write_text(json.dumps(cache, indent=1, sort_keys=True), encoding="utf-8")
            results.update(cache)
    rows, dropped = settle_rows(sim, results, sp)
    return finish(sim, u, sp, vs, rows, dropped, out_dir, silences, draws, seed, t0, raw_dirs)


def finish(sim: MakerSim, u: ws.Universe, sp: ws.Sport, vs: list[MVersion], rows: list[dict], dropped: Counter,
           out_dir: str | Path, silences: dict, draws: int, seed: int, t0: float, raw_dirs: list[str]) -> dict:
    games = sorted(sim.game_first)
    order_key = {g: (u.kickoff.get(g) or sim.game_first[g]) for g in games}
    discovery, unseen = st.split_games(order_key)
    disc, uns = set(discovery), set(unseen)
    by_v = defaultdict(list)
    for r in rows:
        by_v[r["version"]].append(r)
    vrows = []
    for v in vs:
        for vid in (v.vid, v.vid + "-N"):
            vrows.append(version_row(vid, by_v[vid], sim, disc, uns, len(games), sp) | {"unsettled_dropped_contracts": dropped[vid]})
    summary = {
        "sport": sp.name, "primary": PRIMARY, "interpretations": INTERPRETATIONS,
        "verdict": verdict(by_v[PRIMARY], by_v[PRIMARY + "-N"], disc, uns, draws=draws, seed=seed,
                           games_per_week=sp.games_per_week, min_games=sp.min_games, min_unseen=sp.min_unseen,
                           lines_out_of_order=sim.out_of_order, tickers_in_two_streams=len(sim.two_streams)),
        "games": [{"game": g, "segment": "discovery" if g in disc else "unseen",
                   "primary": st.pooled([x for x in by_v[PRIMARY] if x["game"] == g])} for g in games],
        "universe_skipped": dict(u.skipped), "targets": Counter(t.family for t in u.targets.values()),
        "close_times_known": len(sim.close_ns), "lines": sim.lines, "lines_out_of_order": sim.out_of_order,
        "tickers_in_two_streams": sorted(sim.two_streams), "stream_silences": silences,
        "versions": vrows, "runtime_s": round(time.time() - t0, 1)}
    ws._write(Path(out_dir), summary, rows, [{k: v for k, v in r.items() if k != "by_bucket"} for r in vrows])
    return summary
