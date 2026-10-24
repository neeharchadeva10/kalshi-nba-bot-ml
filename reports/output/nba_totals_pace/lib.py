"""Research log #37 library: the user's pace x efficiency formula for NBA totals vs Kalshi KXNBATOTAL.
Pure functions (unit-tested in test_lib.py). Implements PREREGISTRATION.md exactly; nothing is fitted but sigma."""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from kpm.core.fees import FeeModel  # noqa: E402
from kpm.core.prices import clean_quote  # noqa: E402

FEE = FeeModel(0.07, 0.25, "micro_ceil")   # Kalshi docs: round the order fee UP to $0.000001
COUNT = 10
SEED = 20261021

# hoopR (ESPN) -> Kalshi three-letter codes.
TEAM_MAP = {"ATL": "ATL", "BKN": "BKN", "BOS": "BOS", "CHA": "CHA", "CHI": "CHI", "CLE": "CLE", "DAL": "DAL",
            "DEN": "DEN", "DET": "DET", "GS": "GSW", "HOU": "HOU", "IND": "IND", "LAC": "LAC", "LAL": "LAL",
            "MEM": "MEM", "MIA": "MIA", "MIL": "MIL", "MIN": "MIN", "NO": "NOP", "NY": "NYK", "OKC": "OKC",
            "ORL": "ORL", "PHI": "PHI", "PHX": "PHX", "POR": "POR", "SA": "SAS", "SAC": "SAC", "TOR": "TOR",
            "UTAH": "UTA", "WSH": "WAS"}


def to_kalshi(code: str):
    return TEAM_MAP.get(code)


# ---------- ratings ----------
def possessions(fga, oreb, tov, fta):
    return fga - oreb + tov + 0.44 * fta


def build_ratings(tb):
    """tb: team_box DataFrame for ONE season. Regular season only (season_type == 2).
    Returns {kalshi_code: (ortg, drtg, pace)}.  ORtg = sum pts / sum own poss; DRtg = sum opp pts / sum opp poss;
    pace = mean over games of (own + opp poss)/2."""
    # NOTE: season_type 2 includes the NBA Cup final (so one team has 83 games, 1/83 weight); the All-Star
    # teams (STARS/STRIPES/WORLD) are dropped because they are absent from TEAM_MAP.
    tb = tb[tb.season_type == 2].copy()
    tb["poss"] = possessions(tb.field_goals_attempted, tb.offensive_rebounds, tb.total_turnovers,
                             tb.free_throws_attempted)
    opp = tb[["game_id", "team_abbreviation", "poss"]].rename(
        columns={"team_abbreviation": "opponent_team_abbreviation", "poss": "opp_poss"})
    m = tb.merge(opp, on=["game_id", "opponent_team_abbreviation"], how="inner")
    out = {}
    for t, g in m.groupby("team_abbreviation"):
        k = to_kalshi(t)
        if k is None:
            continue
        out[k] = (g.team_score.sum() / g.poss.sum(), g.opponent_team_score.sum() / g.opp_poss.sum(),
                  ((g.poss + g.opp_poss) / 2).mean())
    return out


# ---------- the user's formula ----------
def team_points(ortg_own, pace_own, drtg_opp):
    return (ortg_own * pace_own + drtg_opp * pace_own) / 2.0


def project_total(ra, rb):
    """ra, rb = (ortg, drtg, pace) of team A and team B. Total = A points + B points."""
    a = team_points(ra[0], ra[2], rb[1])
    b = team_points(rb[0], rb[2], ra[1])
    return a, b, a + b


# ---------- probabilities ----------
def phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def boundary(strike: float, strike_type: str = "greater") -> float:
    """Continuity boundary on the continuous scale for an integer-valued total.
    greater (total > X): X.5 -> X ; integer X -> X+0.5.   greater_or_equal (total >= X): X.5 -> X ; integer X -> X-0.5."""
    frac = strike - math.floor(strike)
    if abs(frac - 0.5) < 1e-9:
        return strike
    if abs(frac) < 1e-9:
        if strike_type == "greater":
            return strike + 0.5
        if strike_type == "greater_or_equal":
            return strike - 0.5
    raise ValueError(f"unsupported strike {strike} {strike_type}")


def p_over(mu: float, sigma: float, strike: float, strike_type: str = "greater") -> float:
    return 1.0 - phi((boundary(strike, strike_type) - mu) / sigma)


# ---------- market median ----------
def valid_quote(bid, ask):
    """(bid, ask) -> (bid, ask) if both sides are present (0<bid<=ask<1), else None."""
    if bid is None or ask is None:
        return None
    b, a = clean_quote(bid, ask)
    if b is None or a is None:
        return None
    return b, a


def market_median(points):
    """points: [(strike, mid)] with valid quotes. Strike where mid crosses 0.5 (mid falls as strike rises),
    linearly interpolated between ADJACENT valid strikes; the first crossing from the low-strike side. None if none."""
    pts = sorted(points)
    for (s1, m1), (s2, m2) in zip(pts, pts[1:]):
        if m1 >= 0.5 > m2:
            return s1 + (m1 - 0.5) / (m1 - m2) * (s2 - s1)
    return None


# ---------- candle selection ----------
def pick_candle(candles, T, lead_s=3600):
    """candles: [{'end_period_ts':..}] -> the LAST candle ending at or before T - lead_s (never one ending later)."""
    ok = [c for c in candles if c["end_period_ts"] <= T - lead_s]
    return max(ok, key=lambda c: c["end_period_ts"]) if ok else None


# ---------- fees / bets ----------
def fee_pc(price: float, count: int = COUNT) -> float:
    """Taker fee per contract (order fee at `count` contracts, rounded up to $0.000001, divided by count)."""
    return FEE.per_contract(price, count)


def side_edges(p_yes, bid, ask):
    """Return (yes_edge, no_edge, yes_price, no_price) per contract."""
    yp, npr = ask, 1.0 - bid
    return (p_yes - yp - fee_pc(yp), (1.0 - p_yes) - npr - fee_pc(npr), yp, npr)


def select_bet(strikes, theta, lo=0.15, hi=0.85):
    """strikes: list of dicts with keys id, strike, bid, ask, p (model P over). 'Priced 15-85c' = mid in [lo, hi].
    One bet per game: the single (strike, side) with the largest edge, if edge >= theta. Returns dict or None."""
    best = None
    for s in strikes:
        mid = (s["bid"] + s["ask"]) / 2.0
        if not (lo - 1e-12 <= mid <= hi + 1e-12):
            continue
        ye, ne, yp, npr = side_edges(s["p"], s["bid"], s["ask"])
        for side, e, pr in (("over", ye, yp), ("under", ne, npr)):
            if best is None or e > best["edge"]:
                best = {"id": s["id"], "strike": s["strike"], "side": side, "edge": e, "price": pr}
    return best if best is not None and best["edge"] >= theta - 1e-12 else None


def settle(side: str, price: float, yes_result: bool, count: int = COUNT) -> float:
    """Net $ per contract (after taker fee) of buying `side` at `price`, holding to settlement."""
    win = yes_result if side == "over" else (not yes_result)
    return (1.0 if win else 0.0) - price - fee_pc(price, count)


# ---------- stats ----------
def boot_mean_ci(x, n=10000, seed=SEED, alpha=0.05):
    x = np.asarray(x, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(x), size=(n, len(x)))
    means = x[idx].mean(axis=1)
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def logit(p, eps=0.01):
    p = np.clip(np.asarray(p, float), eps, 1 - eps)
    return np.log(p / (1 - p))


def logistic_fit(X, y, iters=50):
    """Plain IRLS logistic regression (tiny ridge for stability). X includes the intercept column."""
    b = np.zeros(X.shape[1])
    for _ in range(iters):
        z = X @ b
        p = 1 / (1 + np.exp(-z))
        w = np.clip(p * (1 - p), 1e-9, None)
        H = X.T @ (X * w[:, None]) + 1e-8 * np.eye(X.shape[1])
        step = np.linalg.solve(H, X.T @ (y - p))
        b = b + step
        if np.max(np.abs(step)) < 1e-9:
            break
    return b


def residual_test(mid, pmodel, y, game_ids, n_boot=10000, seed=SEED):
    """y ~ b0 + b1*logit(mid) + b2*(logit(model) - logit(mid)); returns b2 and its 95% game-bootstrap CI."""
    mid, pmodel, y = (np.asarray(a, float) for a in (mid, pmodel, y))
    game_ids = np.asarray(game_ids)
    lm, lp = logit(mid), logit(pmodel)
    X = np.column_stack([np.ones(len(y)), lm, lp - lm])
    b = logistic_fit(X, y)
    games = np.unique(game_ids)
    gi = [np.where(game_ids == g)[0] for g in games]
    rng = np.random.default_rng(seed)
    bs = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(games), size=len(games))
        idx = np.concatenate([gi[k] for k in pick])
        bs.append(logistic_fit(X[idx], y[idx])[2])
    return float(b[2]), float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))


# ---------- tickers ----------
EV_RE = re.compile(r"^KXNBATOTAL-(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})$")
MONTHS = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}


def parse_event(et: str):
    """KXNBATOTAL-26JUN13NYKSAS -> (date 'YYYY-MM-DD', away, home)."""
    m = EV_RE.match(et)
    if not m:
        return None
    yy, mon, dd, away, home = m.groups()
    return f"20{yy}-{MONTHS[mon]:02d}-{dd}", away, home


def parse_expiration_value(v):
    """Kalshi expiration_value -> float total, or NaN for '' / 'Yes' / 'No' / None / junk."""
    try:
        return float(v) if v not in (None, "") else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def gate2_pass(coef, lo, hi):
    """Gate 2: the disagreement coefficient AND both ends of its 95% CI must be > 0."""
    return bool(coef > 0 and lo > 0 and hi > 0)
