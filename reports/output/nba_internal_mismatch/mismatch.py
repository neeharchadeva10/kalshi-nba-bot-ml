"""Research log #49 library: pure functions, unit-tested in test_mismatch.py.  Implements PREREGISTRATION.md literally."""
from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SEED = 20261022
# AMENDED 2026-10-22: window ET 2026-02-01..2026-06-30 (regular season + playoffs/play-in), games ordered by scheduled start;
# discovery = first n//2 games, unseen = the rest (see build_population.py).
WIN_FIRST, WIN_LAST = "2026-02-01", "2026-06-30"


def split_by_order(n_games):
    """Index i (0-based, games ordered by scheduled start) -> 'discovery' if i < n//2 else 'unseen'."""
    return ["discovery" if i < n_games // 2 else "unseen" for i in range(n_games)]


SLIP = 0.01
THRESHOLDS = (1, 2, 3, 4)
PAIRS = ("team", "1H")
DIRECTIONS = ("game_follows", "other_follows")
MIN_N = 60
LUCK_REPS = 500
SCOPE_PTS = 8.0
MAX_SPREAD_MEDIAN = 0.15
MAX_SPREAD_MAIN = 0.10
MAIN_LO, MAIN_HI = 0.15, 0.85
EPS = 1e-9


# ------------------------------------------------------------------ fees / prices
def fee_e4(price: float, count: int = 1) -> int:
    """Taker fee of ONE order in 1/10000 dollars: ceil(0.07*count*P*(1-P)/0.0001), exact integer maths on the cent price."""
    pc = int(round(price * 100))
    if not 0 <= pc <= 100 or abs(price * 100 - pc) > 1e-6:
        raise ValueError(f"price must be on the cent grid in [0,1]: {price}")
    num = 7 * count * pc * (100 - pc)                    # dollars = num / 1e6 ; ticks of 1e-4 = num / 100
    return -(-num // 100)


def fee(price: float, count: int = 1) -> float:
    return fee_e4(price, count) / 10_000


def entry_price(quote_price: float, slip: float = SLIP) -> float:
    """Price actually paid: displayed ask (YES) or 1 - bid (NO) plus 1c slippage, on the cent grid."""
    return round(quote_price + slip, 2)


def leg_net(price_paid: float, win: bool) -> float:
    """Net $ of ONE contract in ONE order, held to settlement: payout - price - fee (fee on the price paid)."""
    return (1.0 if win else 0.0) - price_paid - fee(price_paid, 1)


# ------------------------------------------------------------------ medians / main strike
def implied_median(points):
    """Copy of nba_injury_speed/common.implied_total (= dashboard logic).  points: (strike, yes_bid, yes_ask).
    Kept: 0 < bid <= ask < 1 and ask - bid <= 0.15.  Strike where the YES mid crosses 0.50 (linear interpolation);
    None if the kept strikes do not bracket 0.50 (first mid < 0.5 or last mid > 0.5) or none are kept."""
    pts = []
    for s, b, a in points:
        try:
            s, b, a = float(s), float(b), float(a)
        except (TypeError, ValueError):
            continue
        if 0 < b <= a < 1 and a - b <= MAX_SPREAD_MEDIAN + EPS:
            pts.append((s, (a + b) / 2))
    pts.sort()
    if not pts:
        return None
    if pts[0][1] < 0.5 or pts[-1][1] > 0.5:
        return None
    for (s1, m1), (s2, m2) in zip(pts, pts[1:]):
        if m1 >= 0.5 >= m2:
            if m1 == m2:
                return s1
            return s1 + (m1 - 0.5) / (m1 - m2) * (s2 - s1)
    return pts[-1][0] if pts[-1][1] == 0.5 else None


def main_strike(quotes):
    """quotes: [(ticker, strike, bid, ask)].  The market whose mid is nearest 0.50 among mids in [0.15, 0.85] with
    0 < bid <= ask < 1 and spread <= 0.10.  Ties -> lower strike.  Returns (ticker, strike, bid, ask) or None."""
    best = None
    for t, s, b, a in quotes:
        if b is None or a is None or not (0 < b <= a < 1):
            continue
        if a - b > MAX_SPREAD_MAIN + EPS:
            continue
        mid = (a + b) / 2
        if not (MAIN_LO - EPS <= mid <= MAIN_HI + EPS):
            continue
        key = (round(abs(mid - 0.5), 9), s)
        if best is None or key < best[0]:
            best = (key, (t, s, b, a))
    return best[1] if best else None


# ------------------------------------------------------------------ mismatch statistics
def d_team(m_home, m_away, m_game):
    return m_home + m_away - m_game


def d_1h(m_1h, m_game, k):
    return m_1h - k * m_game


def fit_k(disc_ratios):
    """k = median of M_1H / M_game over DISCOVERY games only.  Raises on an empty list (no discovery data)."""
    r = [x for x in disc_ratios if x is not None and math.isfinite(x)]
    if not r:
        raise ValueError("no discovery games to fit k")
    return float(np.median(r))


# ------------------------------------------------------------------ bet construction
def grid():
    return [(p, d, th) for p in PAIRS for d in DIRECTIONS for th in THRESHOLDS]


def vname(v):
    return f"{v[0]}|{v[1]}|>={v[2]}"


def side_for(direction, D):
    """Which book and side to buy.  Returns ('game' | 'other', 'yes' | 'no').
    game_follows : D > 0 -> YES on the game total's main strike, D < 0 -> NO  (identical for both pairs).
    other_follows: bet the OTHER book toward the game total, on the side that reduces |D|:
                   D > 0 (other book too high) -> NO on it; D < 0 -> YES
                   (team: on EACH team total's main strike; 1H: on the 1H main strike)."""
    if D == 0:
        return None
    if direction == "game_follows":
        return ("game", "yes" if D > 0 else "no")
    if direction == "other_follows":
        return ("other", "no" if D > 0 else "yes")
    raise ValueError(direction)


def leg_price(side, bid, ask):
    """Taker entry price incl. +1c: YES pays the ask, NO pays 1 - bid."""
    base = ask if side == "yes" else 1.0 - bid
    return entry_price(base)


def bet_net(legs, yes_results):
    """legs: [(side, bid, ask)]; yes_results: [bool] (market resolved YES).  Returns (sum of leg nets, n_legs).
    Two-leg team bet = both legs summed; per-contract net = sum / n_legs."""
    tot = 0.0
    for (side, b, a), yes in zip(legs, yes_results):
        p = leg_price(side, b, a)
        if p >= 1.0:
            raise ValueError("price paid >= $1")
        win = yes if side == "yes" else (not yes)
        tot += leg_net(p, win)
    return tot, len(legs)


# ------------------------------------------------------------------ selection and tests
def lb(x):
    x = np.asarray(x, float)
    n = len(x)
    if n < 2:
        return float("nan")
    return float(x.mean() - 1.96 * x.std(ddof=1) / math.sqrt(n))


def select_version(stats, min_n=MIN_N):
    """stats: {version: {'n':..,'lb':..}}.  Highest LB among n >= min_n (ties -> grid order).  None if none eligible."""
    best, bl = None, None
    for v, s in stats.items():
        if s["n"] >= min_n and s["lb"] == s["lb"]:
            if bl is None or s["lb"] > bl + 1e-15:
                best, bl = v, s["lb"]
    return best


def luck_test(net_yes, net_no, incl, real_best_lb, reps=LUCK_REPS, seed=SEED, min_n=MIN_N):
    """Shared random side per game.  net_yes/net_no/incl: (G x V) arrays (per-contract net if the bet side is YES/NO for
    that game and version; inclusion mask).  Each rep draws one coin per game, shared by all versions, recomputes each
    version's LB (n >= min_n) and takes the best.  Returns (share of reps whose best LB >= real_best_lb, list of best LBs)."""
    net_yes, net_no, incl = (np.asarray(a) for a in (net_yes, net_no, incl))
    G, V = incl.shape
    rng = np.random.default_rng(seed)
    best = []
    for _ in range(reps):
        s = rng.integers(0, 2, size=G).astype(bool)
        net = np.where(s[:, None], net_yes, net_no)
        b = -np.inf
        for j in range(V):
            x = net[incl[:, j], j]
            if len(x) >= min_n:
                l = lb(x)
                if l == l and l > b:
                    b = l
        best.append(b)
    best = np.array(best)
    return float((best >= real_best_lb - 1e-15).mean()), best.tolist()


def boot_mean_lb(x, n=10000, seed=SEED, alpha=0.05):
    x = np.asarray(x, float)
    rng = np.random.default_rng(seed)
    means = x[rng.integers(0, len(x), size=(n, len(x)))].mean(axis=1)
    return float(np.quantile(means, alpha / 2))


# ------------------------------------------------------------------ tickers
def event_suffix(event_ticker):
    return event_ticker.split("-", 1)[1]


def team_of_ticker(ticker):
    """KXNBATEAMTOTAL-26MAR10BOSSAS-BOS98 -> 'BOS'."""
    return ticker.rsplit("-", 1)[1].rstrip("0123456789")


def in_scope(strike, ev, pts=SCOPE_PTS):
    return abs(strike - ev) <= pts + EPS


def scope_ev(kind, m_game):
    """Expected value used ONLY to choose which strikes to download: team = M_game/2 ; 1H = 0.5*M_game."""
    return m_game / 2.0 if kind == "team" else 0.5 * m_game


def parse_team_expiration(v):
    """'Home: 105, Away: 90' -> (105.0, 90.0) else None."""
    m = re.match(r"^\s*Home:\s*(\d+(?:\.\d+)?)\s*,\s*Away:\s*(\d+(?:\.\d+)?)\s*$", v or "")
    return (float(m[1]), float(m[2])) if m else None


def pick_last_candle(candles, T, lead_s=3600):
    """candles: [{'end_ts':..}] -> the LAST candle ending at or before T - lead_s."""
    ok = [c for c in candles if c["end_ts"] <= T - lead_s]
    return max(ok, key=lambda c: c["end_ts"]) if ok else None
