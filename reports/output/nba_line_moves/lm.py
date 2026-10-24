"""Research log #54: do pregame NBA total line moves keep going (follow) or reverse (fade)? Pure functions (unit-tested in
test_line_moves.py). IO lives in pipeline.py. Implements PREREGISTRATION.md literally; see README 'builder notes'.

fee_taker is loaded from #46's common.py and implied_total from #44's common.py (by file path, not copied)."""
from __future__ import annotations

import importlib.util
import itertools
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
INJ = ROOT / "reports" / "output" / "nba_injury_speed"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_C44 = _load("c44_common", INJ / "common.py")
_C46 = _load("c46_common", ROOT / "reports" / "output" / "nba_ingame_overreaction" / "common.py")
fee_taker = _C46.fee_taker
implied_total = _C44.implied_total
et_date = _C44.et_date

SEED = 20261022
DISC_FIRST, DISC_LAST = "2025-10-21", "2026-01-22"
UNSEEN_FIRST, UNSEEN_LAST = "2026-01-23", "2026-04-12"
D_BEFORE_T_S = 60 * 60
STALE_S = 10 * 60
MAIN_LO, MAIN_HI, MAIN_SPREAD = 0.15, 0.85, 0.10
SLIP = 0.01
MIN_N = 60
N_BOOT, N_LUCK, N_COIN = 10000, 500, 500
WS = (2, 4)            # hours
XS = (1.5, 3.0)
DIRS = ("follow", "fade")
VERSIONS = [(w, x, d) for w, x, d in itertools.product(WS, XS, DIRS)]   # 8


def vname(v):
    return f"w{v[0]}h_X{v[1]}_{v[2]}"


# ---------------------------------------------------------------- book
class Book:
    """candles: {ticker: (end_ts list, bid list, ask list)}; strikes: {ticker: strike}."""

    def __init__(self, strikes, candles, T):
        self.T = int(T)
        self.strikes = strikes
        self.c = {}
        for t, (e, b, a) in candles.items():
            e = np.asarray(e, dtype=np.int64)
            o = np.argsort(e)
            self.c[t] = (e[o], np.asarray(b, float)[o], np.asarray(a, float)[o])

    def quote_at(self, ticker, t):
        """(bid, ask, end) of the last candle ending at or before t and no older than STALE_S, else None."""
        e = self.c.get(ticker)
        if e is None:
            return None
        i = int(np.searchsorted(e[0], t, side="right")) - 1
        if i < 0:
            return None
        end = int(e[0][i])
        if t - end > STALE_S:
            return None
        b, a = e[1][i], e[2][i]
        if np.isnan(b) or np.isnan(a):
            return None
        return float(b), float(a), end

    def M(self, t):
        """Implied total at t (YES-mid 0.50 crossing, #44 rule, spread<=0.15); None if no crossing or t after tip."""
        if t > self.T:
            return None
        pts = []
        for tk, s in self.strikes.items():
            q = self.quote_at(tk, t)
            if q is not None and s is not None:
                pts.append((s, q[0], q[1]))
        return implied_total(pts)

    def main_strike(self, t):
        """Strike whose YES mid (quote as of t) is nearest 0.50, mid in 15-85c, 0<bid<=ask<1, ask-bid<=0.10. Tie -> lower strike."""
        best = None
        for tk, s in self.strikes.items():
            q = self.quote_at(tk, t)
            if q is None:
                continue
            b, a, end = q
            if not (0 < b <= a < 1) or a - b > MAIN_SPREAD + 1e-12:
                continue
            mid = (a + b) / 2
            if not (MAIN_LO - 1e-12 <= mid <= MAIN_HI + 1e-12):
                continue
            key = (abs(mid - 0.5), s)
            if best is None or key < best[0]:
                best = (key, tk, s, b, a, end)
        return None if best is None else dict(ticker=best[1], strike=best[2], bid=best[3], ask=best[4], end=best[5])


# ---------------------------------------------------------------- rule
def move_at(book, w_hours):
    """Move = M(D) - M(D-w), D = T - 60 min. None if either is missing. Returns (move, M_D, M_Dw)."""
    D = book.T - D_BEFORE_T_S
    m1 = book.M(D)
    m0 = book.M(D - int(w_hours * 3600))
    if m1 is None or m0 is None:
        return None, m1, m0
    return m1 - m0, m1, m0


def triggered(move, X):
    return move is not None and abs(move) >= X - 1e-12


def side_for(move, direction):
    """follow: up -> YES, down -> NO; fade: opposite. Returns 'YES'/'NO'."""
    up = move > 0
    if direction == "fade":
        up = not up
    return "YES" if up else "NO"


def bet_nets(bid, ask, result):
    """Per-contract net $ for BOTH sides at the entry quote: taker, +1c slippage, fee on the slipped price, held to settlement.
    result 'yes'/'no' (YES wins if 'yes'). Returns {'YES': net, 'NO': net, 'price_YES','price_NO'}."""
    out = {}
    for side, p0 in (("YES", ask), ("NO", 1.0 - bid)):
        p = round(p0 + SLIP, 10)
        if p >= 1.0:
            out[side] = float("nan")
            out["price_" + side] = p
            continue
        win = (result == "yes") == (side == "YES")
        out[side] = (1.0 if win else 0.0) - p - fee_taker(p)
        out["price_" + side] = p
    return out


def in_split(date_et, first, last):
    return first <= date_et <= last


# ---------------------------------------------------------------- statistics
def mean_sd_lb(nets):
    x = np.asarray(nets, float)
    n = len(x)
    if n < 2:
        return {"n": n, "mean": float(x.mean()) if n else None, "sd": None, "lb": None}
    m, sd = float(x.mean()), float(x.std(ddof=1))
    return {"n": n, "mean": m, "sd": sd, "lb": m - 1.96 * sd / np.sqrt(n)}


def select_version(stats):
    """stats: {version: {'n','lb',...}}. Highest LB among n >= MIN_N; None if none eligible."""
    el = [(s["lb"], v) for v, s in stats.items() if s["n"] >= MIN_N and s["lb"] is not None]
    if not el:
        return None
    return max(el, key=lambda t: (t[0], str(t[1])))[1]


def luck_test(trade_sets, net_yes, net_no, real_best_lb, n_reps=N_LUCK, seed=SEED):
    """trade_sets: {version: boolean array over games (the version's triggered+traded games)}; net_yes/net_no arrays over the
    SAME game axis (all games that have an entry quote). Per rep one random direction per game, shared by all versions; the
    8-version selection (n>=MIN_N, highest normal LB) is re-run per rep. Returns share of reps whose best LB >= real_best_lb."""
    ny, nn = np.asarray(net_yes, float), np.asarray(net_no, float)
    rng = np.random.default_rng(seed)
    pick_yes = rng.random((n_reps, len(ny))) < 0.5
    nets = np.where(pick_yes, ny[None, :], nn[None, :])              # reps x games
    best = np.full(n_reps, -np.inf)
    for v, mask in trade_sets.items():
        mask = np.asarray(mask, bool)
        n = int(mask.sum())
        if n < MIN_N:
            continue
        x = nets[:, mask]
        lb = x.mean(1) - 1.96 * x.std(1, ddof=1) / np.sqrt(n)
        best = np.maximum(best, lb)
    share = float(np.mean(best >= real_best_lb - 1e-15))
    return {"share_reps_as_good": share, "n_reps": int(n_reps), "best_lb_rep_mean": float(np.mean(best[np.isfinite(best)])) if np.isfinite(best).any() else None,
            "best_lb_rep_p95": float(np.quantile(best[np.isfinite(best)], 0.95)) if np.isfinite(best).any() else None,
            "verdict": "PASS" if share <= 0.05 else "FAIL"}


def boot_lb(nets, games, n=N_BOOT, seed=SEED, chunk=1000):
    nets = np.asarray(nets, float)
    uniq, inv = np.unique(np.asarray(games), return_inverse=True)
    G = len(uniq)
    S = np.bincount(inv, weights=nets, minlength=G)
    Cn = np.bincount(inv, minlength=G).astype(float)
    rng = np.random.default_rng(seed)
    means, done = [], 0
    while done < n:
        k = min(chunk, n - done)
        idx = rng.integers(0, G, size=(k, G))
        means.append(S[idx].sum(1) / Cn[idx].sum(1))
        done += k
    means = np.concatenate(means)
    return {"mean": float(nets.mean()), "lb_2.5": float(np.quantile(means, 0.025)), "ub_97.5": float(np.quantile(means, 0.975)),
            "n_games": int(G), "n_draws": int(n)}


def coin_flip(net_yes, net_no, actual_mean, n=N_COIN, seed=SEED):
    ny, nn = np.asarray(net_yes, float), np.asarray(net_no, float)
    rng = np.random.default_rng(seed)
    flips = rng.random((n, len(ny))) < 0.5
    means = np.where(flips, nn[None, :], ny[None, :]).mean(1)
    return {"share_as_good": float(np.mean(means >= actual_mean - 1e-15)), "copy_mean_avg": float(means.mean()), "n_copies": int(n)}


def ols_slope(x, y):
    """y = a + b x. Returns slope, se, intercept, n, r2."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    n = len(x)
    if n < 3 or x.std() == 0:
        return {"n": n, "slope": None, "se": None, "intercept": None, "r2": None}
    xm, ym = x.mean(), y.mean()
    sxx = ((x - xm) ** 2).sum()
    b = ((x - xm) * (y - ym)).sum() / sxx
    a = ym - b * xm
    res = y - a - b * x
    se = float(np.sqrt((res ** 2).sum() / (n - 2) / sxx))
    r2 = 1 - (res ** 2).sum() / ((y - ym) ** 2).sum() if ((y - ym) ** 2).sum() > 0 else None
    return {"n": n, "slope": float(b), "se": se, "intercept": float(a), "r2": None if r2 is None else float(r2)}


# ---------------------------------------------------------------- gates
def unseen_gate(here=HERE):
    """Returns (ok, reason). Order: AUDIT_OK, results_unseen absent, discovery luck PASS and a selected version."""
    import json
    if not (here / "AUDIT_OK").exists():
        return False, "AUDIT_OK does not exist (the unseen half waits for the Opus audit)"
    if (here / "results_unseen.json").exists():
        return False, "results_unseen.json already exists; the unseen half is run once"
    p = here / "results_discovery.json"
    if not p.exists():
        return False, "results_discovery.json missing"
    r = json.loads(p.read_text())
    if not r.get("selected_version"):
        return False, "no version selected in discovery"
    if r.get("luck_test", {}).get("verdict") != "PASS":
        return False, "discovery luck test is not PASS"
    return True, "ok"
