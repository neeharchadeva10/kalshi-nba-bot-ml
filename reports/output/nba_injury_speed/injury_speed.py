"""Research log #44 core library: do Kalshi NBA game totals react late to official injury reports?
Implements PREREGISTRATION.md literally.  Pure functions (unit-tested in test_injury_speed.py); IO lives in pipeline.py.

Judgement calls (all listed in the final report): see JUDGEMENT at the bottom of this file.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

import numpy as np

import common as C
from common import SEED, fee_taker, implied_total

# ---------------------------------------------------------------- constants (pre-registered)
WINDOW_BEFORE_TIP_MAX_S = 6 * 3600       # P >= T - 6h
WINDOW_BEFORE_TIP_MIN_S = 10 * 60        # P <= T - 10 min
MATERIAL_MIN = 24.0
MATERIAL_GAMES = 5
OFFSETS_MIN = (-30, -1, 1, 2, 5, 10, 30, 60)
ENTRY_DELAY_S = 120
EXIT_AFTER_S = 30 * 60
MAIN_MID_LO, MAIN_MID_HI, MAIN_MAX_SPREAD = 0.15, 0.85, 0.10
N_BOOT, N_COIN = 10000, 500
ERA15_RE = re.compile(r"_\d{2}_\d{2}[AP]M\.pdf$")


# ---------------------------------------------------------------- minutes / materiality
class Minutes:
    """Average minutes of a player over the games he PLAYED for that team strictly before a date (ISO string).
    Built from the hoopR player box (2025-26 regular season)."""

    def __init__(self, rows):
        """rows: iterable of (team_code, player_norm, game_date_iso, minutes) for games played (minutes > 0)."""
        tmp = {}
        for tc, pn, gd, mn in rows:
            tmp.setdefault((tc, pn), []).append((gd, float(mn)))
        self.d = {}
        for k, v in tmp.items():
            v.sort()
            dates = [x[0] for x in v]
            cs = np.concatenate([[0.0], np.cumsum([x[1] for x in v])])
            self.d[k] = (dates, cs)

    def avg(self, team, player_norm, before_date):
        """(average minutes, games played) over games with date < before_date, or (None, 0)."""
        e = self.d.get((team, player_norm))
        if e is None:
            return None, 0
        dates, cs = e
        n = bisect.bisect_left(dates, before_date)       # games strictly before
        if n == 0:
            return None, 0
        return float(cs[n] / n), n

    def material(self, team, player_norm, before_date, threshold=MATERIAL_MIN, min_games=MATERIAL_GAMES):
        """(is_material, avg minutes).  Material: >= min_games games strictly before the date AND avg >= threshold."""
        a, n = self.avg(team, player_norm, before_date)
        if a is None or n < min_games:
            return False, a
        return a >= threshold - 1e-12, a


# ---------------------------------------------------------------- event detection
DOWN_FROM_NOT = "Out"
UP_FROM = ("Out", "Doubtful")
UP_TO = ("Available", "Probable")


def classify_change(prev_status, cur_status):
    """prev/cur: status string or None (not listed).  Returns 'DOWN', 'UP' or None.
    DOWN: now Out and previously anything but Out (incl. not listed).  UP: was Out/Doubtful and is now Available/Probable,
    or is no longer listed (the caller guarantees the game is still listed).  Questionable changes are ignored."""
    if cur_status == "Out" and prev_status != "Out":
        return "DOWN"
    if prev_status in UP_FROM and (cur_status in UP_TO or cur_status is None):
        return "UP"
    return None


@dataclass
class Report:
    name: str
    P: float                       # UTC epoch seconds (PDF /CreationDate)
    status: dict                   # {(team_glued, player_raw): status} for ONE game
    nys: frozenset = frozenset()   # team_glued names 'not yet submitted' for this game
    era15: bool = False


def detect_events(reports, T, resolve, threshold=MATERIAL_MIN, date_iso=None, minutes=None, counters=None):
    """reports: list of Report for ONE game, each of which lists the game.  Compared in P order with the previous listing report.
    resolve(team_glued, player_raw) -> (is_material, avg_minutes).
    Returns one dict per (game, report) with at least one material change:
      {name, P, era15, direction ('DOWN'/'UP'/None if tie), status ('ok'|'tie'|'outside_window'), down:[(player,min)], up:[...]}"""
    cnt = counters if counters is not None else {}
    out = []
    reps = sorted(reports, key=lambda r: (r.P, r.name))
    for prev, cur in zip(reps, reps[1:]):
        skip_teams = set(prev.nys) | set(cur.nys)
        down, up = [], []
        for key in set(prev.status) | set(cur.status):
            team, player = key
            if team in skip_teams:
                continue
            ch = classify_change(prev.status.get(key), cur.status.get(key))
            if ch is None:
                continue
            mat, mins = resolve(team, player)
            if mins is None:
                cnt["changed_player_no_box_match"] = cnt.get("changed_player_no_box_match", 0) + 1
            if not mat:
                cnt["changed_player_not_material"] = cnt.get("changed_player_not_material", 0) + 1
                continue
            (down if ch == "DOWN" else up).append((player, mins))
        if not down and not up:
            continue
        sd, su = sum(m for _, m in down), sum(m for _, m in up)
        diff = su - sd
        direction = "UP" if diff > 0 else ("DOWN" if diff < 0 else None)
        in_win = (T - WINDOW_BEFORE_TIP_MAX_S) <= cur.P <= (T - WINDOW_BEFORE_TIP_MIN_S)
        status = "outside_window" if not in_win else ("tie" if direction is None else "ok")
        out.append({"name": cur.name, "P": cur.P, "era15": cur.era15, "direction": direction, "status": status,
                    "down": down, "up": up, "prev_name": prev.name})
    return out


# ---------------------------------------------------------------- 1-minute book
class Book:
    """Carry-forward book for one event.  candles: {ticker: (end_ts list, bid list, ask list)}; strikes: {ticker: strike}."""

    def __init__(self, strikes, candles, T):
        self.T = T
        self.strikes = strikes
        self.c = {}
        ends = set()
        for t, (e, b, a) in candles.items():
            order = np.argsort(e)
            e = np.asarray(e, dtype=np.int64)[order]
            self.c[t] = (e, np.asarray(b, dtype=float)[order], np.asarray(a, dtype=float)[order])
            ends.update(e.tolist())
        self.ends = np.array(sorted(ends), dtype=np.int64)       # event-level candle ends

    def quote_at(self, ticker, x):
        """(bid, ask, candle_end) of the last candle ending at or before x for this ticker, else None."""
        e = self.c.get(ticker)
        if e is None:
            return None
        i = int(np.searchsorted(e[0], x, side="right")) - 1
        if i < 0:
            return None
        b, a = e[1][i], e[2][i]
        return (None if np.isnan(b) else float(b), None if np.isnan(a) else float(a), int(e[0][i]))

    def M(self, x):
        """Implied total at time x, only from candles ending at or before x.  Missing (None) if no crossing, or x after tip
        (candles stop at T; in-game prices are not used)."""
        if x > self.T:
            return None
        pts = []
        for t, s in self.strikes.items():
            q = self.quote_at(t, x)
            if q is not None and s is not None:
                pts.append((s, q[0], q[1]))
        return implied_total(pts)

    def first_end_at_or_after(self, x):
        """First event-level candle end >= x and strictly before tip, else None."""
        i = int(np.searchsorted(self.ends, x, side="left"))
        if i < len(self.ends) and self.ends[i] < self.T:
            return int(self.ends[i])
        return None

    def last_end_before_tip(self):
        i = int(np.searchsorted(self.ends, self.T, side="left")) - 1
        return int(self.ends[i]) if i >= 0 else None


def main_strike(book, x):
    """Strike nearest mid 0.50 among strikes (quote as of x) with 0<bid<=ask<1, mid 15-85c and ask-bid <= 0.10.
    Returns (ticker, bid, ask, candle_end) or None.  Tie -> lower strike."""
    best = None
    for t, s in book.strikes.items():
        q = book.quote_at(t, x)
        if q is None or q[0] is None or q[1] is None:
            continue
        b, a, ce = q
        if not (0 < b <= a < 1) or a - b > MAIN_MAX_SPREAD + 1e-12:
            continue
        mid = (a + b) / 2
        if not (MAIN_MID_LO - 1e-12 <= mid <= MAIN_MID_HI + 1e-12):
            continue
        key = (abs(mid - 0.5), s)
        if best is None or key < best[0]:
            best = (key, t, b, a, ce)
    return None if best is None else best[1:]


def leg_net(side, bid_in, ask_in, bid_out, ask_out):
    """Net $ per contract for one round trip.  side 'YES': buy at ask_in, sell at bid_out.
    side 'NO': buy NO at 1-bid_in, sell NO at its bid 1-ask_out.  Both taker fees ($0.0001 round-up)."""
    if side == "YES":
        pin, pout = ask_in, bid_out
    else:
        pin, pout = 1.0 - bid_in, 1.0 - ask_out
    fin, fout = fee_taker(pin), fee_taker(pout)
    return pout - pin - fin - fout, pin, pout, fin, fout


def trade_event(book, P, direction, entry_delay_s=ENTRY_DELAY_S, exit_after_s=EXIT_AFTER_S, exit_at_tip=False,
                entry_mode="first_candle", result=None):
    """Pre-registered rule.  Returns dict with skip_reason (None if traded).
    entry_mode 'first_candle' (primary): E = first event-level candle ending at or after P+entry_delay (and before tip);
    'carry' (information only): E = P+entry_delay exactly, book = last candle at or before it.
    Exit: first event-level candle ending at or after P+exit_after_s, or the last candle ending before tip if that comes
    first / nothing earlier exists (exit_at_tip: always the last candle before tip).  Skip if a candle is missing, the entry
    book fails the filter, the exit quote is invalid or the exit is not after the entry."""
    r = {"skip_reason": None}
    want = P + entry_delay_s
    if entry_mode == "first_candle":
        E = book.first_end_at_or_after(want)
    else:
        E = int(want) if want < book.T else None
    if E is None:
        r["skip_reason"] = "no_entry_candle"
        return r
    r["entry_end"] = E
    ms = main_strike(book, E)
    if ms is None:
        r["skip_reason"] = "entry_book_fails_filter"
        return r
    tk, b_in, a_in, _ = ms
    if exit_at_tip:
        X = book.last_end_before_tip()
    else:
        X = book.first_end_at_or_after(P + exit_after_s)
        if X is None:
            X = book.last_end_before_tip()
    if X is None:
        r["skip_reason"] = "no_exit_candle"
        return r
    if X <= E:
        r["skip_reason"] = "exit_not_after_entry"
        return r
    q = book.quote_at(tk, X)
    if q is None or q[0] is None or q[1] is None or not (0 < q[0] <= q[1] < 1):
        r["skip_reason"] = "exit_quote_invalid"
        return r
    b_out, a_out, _ = q
    side = "YES" if direction == "UP" else "NO"
    net, pin, pout, fin, fout = leg_net(side, b_in, a_in, b_out, a_out)
    other = "NO" if side == "YES" else "YES"
    net_o = leg_net(other, b_in, a_in, b_out, a_out)[0]
    r.update(ticker=tk, strike=book.strikes[tk], side=side, entry_end=E, exit_end=X, entry_price=pin, exit_price=pout,
             fee_in=fin, fee_out=fout, net=net, net_opposite=net_o, net_yes=net if side == "YES" else net_o,
             net_no=net if side == "NO" else net_o)
    if result in ("yes", "no"):       # hold to settlement (information only)
        win = (result == "yes") == (side == "YES")
        r["net_settle"] = (1.0 if win else 0.0) - pin - fin
    return r


# ---------------------------------------------------------------- descriptive measures
def m_series(book, P, cap_at_tip=False):
    """cap_at_tip=True is information only: offsets beyond tip use M(T) instead of missing."""
    if cap_at_tip:
        return {off: book.M(min(P + off * 60, book.T)) for off in OFFSETS_MIN}
    return {off: book.M(P + off * 60) for off in OFFSETS_MIN}


def shares(m):
    """m: dict offset->M or None.  Returns (total_move, pre_share, late_share) (None where undefined).
    total = M(P+60)-M(P-30); pre = (M(P-1)-M(P-30))/total; late = (M(P+60)-M(P+2))/total.  Shares only if |total| >= 1."""
    g = lambda k: m.get(k)
    tot = pre = late = None
    if g(60) is not None and g(-30) is not None:
        tot = g(60) - g(-30)
        if abs(tot) >= 1.0:
            if g(-1) is not None:
                pre = (g(-1) - g(-30)) / tot
            if g(2) is not None:
                late = (g(60) - g(2)) / tot
    return tot, pre, late


def median_or_none(xs):
    xs = [x for x in xs if x is not None]
    return float(np.median(xs)) if xs else None


# ---------------------------------------------------------------- statistics
def boot_lb(nets, games, n=N_BOOT, seed=SEED, chunk=1000):
    """Bootstrap by game (resample games with replacement, pool their events).  Returns dict with mean and quantiles."""
    nets = np.asarray(nets, float)
    uniq, inv = np.unique(np.asarray(games), return_inverse=True)
    G = len(uniq)
    S = np.bincount(inv, weights=nets, minlength=G)
    Cn = np.bincount(inv, minlength=G).astype(float)
    rng = np.random.default_rng(seed)
    means = []
    done = 0
    while done < n:
        k = min(chunk, n - done)
        idx = rng.integers(0, G, size=(k, G))
        means.append(S[idx].sum(1) / Cn[idx].sum(1))
        done += k
    means = np.concatenate(means)
    return {"mean": float(nets.mean()), "lb_2.5": float(np.quantile(means, 0.025)), "lb_5": float(np.quantile(means, 0.05)),
            "ub_97.5": float(np.quantile(means, 0.975)), "n_games": int(G), "n_draws": int(n)}


def coin_flip(net_yes, net_no, actual_mean, n=N_COIN, seed=SEED):
    """500 copies; each copy picks YES or NO at random per event (same strike, same entry and exit times).
    Returns the share of copies whose mean net >= the actual mean (pass if <= 0.05)."""
    ny, nn = np.asarray(net_yes, float), np.asarray(net_no, float)
    rng = np.random.default_rng(seed)
    flips = rng.random((n, len(ny))) < 0.5
    means = np.where(flips, nn[None, :], ny[None, :]).mean(1)
    return {"share_as_good": float(np.mean(means >= actual_mean - 1e-15)), "copy_mean_avg": float(means.mean()),
            "copy_mean_p95": float(np.quantile(means, 0.95)), "n_copies": int(n)}


JUDGEMENT = """
1. First report that lists a game has no previous report -> no comparison (skip, counted as first_listing).
2. A team marked NOT YET SUBMITTED in either report of a pair is excluded from that comparison.
3. 'Average minutes over the team's games' = average over games the player PLAYED (minutes > 0) with date strictly before the
   game's ET date; needs >= 5 such games.
4. M(x) is missing when x is after tip T (candles stop at T).
5. Entry/exit candle = event-level 1-minute candle (any strike); the contract's quote is the carry-forward book at that minute.
6. Candles are fetched T-8h..T (pre-reg: T-6h) so that M(P-30) exists for P near T-6h.
"""
