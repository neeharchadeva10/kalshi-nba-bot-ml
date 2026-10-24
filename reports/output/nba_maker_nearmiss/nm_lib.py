"""Research log #53 library: pure, unit-tested functions.  Implements PREREGISTRATION.md literally.

Candle tuple used everywhere: (end_ts, bid_close, ask_close, volume, price_low, price_high)
  bid/ask = yes_bid.close / yes_ask.close ; price_low/high = trade price OHLC low/high (None when no trade in the candle).
Read-only; no network; no kpm.execution; no keys.
"""
from __future__ import annotations

import csv
import gzip
import json
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SEED = 20261022
LUCK_REPS = 500
MIN_N = 60
BOOT_DRAWS = 10000
PX_LO, PX_HI = 0.15, 0.85           # limit price range (pre-registration)
EPS = 1e-9
SENS = 0.01                         # +1c sensitivity line (never used for selection)
MAKER_RATE_E4 = 175                 # 0.0175 expressed as 175/10000
MIN_LEAD_E0 = 2 * 3600              # E0 must be <= T - 2h
FILL_DEADLINE = 3600                # fill candle must end <= T - 1h
DISC_LO, DISC_HI = "2025-11-18", "2026-01-22"      # #48 family discovery dates
N48_VERSIONS = 24
N49_VERSIONS = 16

AUDIT_OK = HERE / "AUDIT_OK"
RESULTS_UNSEEN = HERE / "results_unseen.json"
RESULTS_DISC = HERE / "results_discovery.json"

# fee_type per series as found in the cached series definitions (data/raw/kalshi_series_meta).  Only KXNBATOTAL is cached.
# Anything else is "unknown" and is charged the maker fee (pre-registration: unknown -> charge).
FEE_TYPE_CACHE_DIR = ROOT / "data" / "raw" / "kalshi_series_meta"


# ----------------------------------------------------------------------------------------------------------- fees
def load_fee_types(cache_dir: Path = FEE_TYPE_CACHE_DIR) -> dict:
    """{series_ticker: fee_type} from the cached public series responses (no network)."""
    out = {}
    if not Path(cache_dir).exists():
        return out
    for p in Path(cache_dir).rglob("*.json.gz"):
        try:
            with gzip.open(p, "rt", encoding="utf-8") as fh:
                d = json.load(fh)
            s = d.get("series", d)
            if s.get("ticker"):
                out[s["ticker"]] = s.get("fee_type")
        except Exception:   # noqa: BLE001
            continue
    return out


def series_of(ticker: str) -> str:
    return ticker.split("-", 1)[0]


def maker_fee_applies(fee_type) -> bool:
    """Fee is charged if fee_type is quadratic_with_maker_fees OR unknown (None / not cached).  Only an explicitly
    known different type (e.g. 'quadratic' = makers free) waives it."""
    if fee_type is None or fee_type == "":
        return True
    return fee_type == "quadratic_with_maker_fees"


def maker_fee_e4(price: float, count: int = 1) -> int:
    """0.0175 * count * P * (1-P), rounded UP to $0.0001, in units of $0.0001 (exact integer maths on the cent grid)."""
    pc = int(round(price * 100))
    if not 0 <= pc <= 100 or abs(price * 100 - pc) > 1e-6:
        raise ValueError(f"price must be on the cent grid in [0,1]: {price}")
    num = MAKER_RATE_E4 * count * pc * (100 - pc)         # dollars = num / 1e8 ; units of 1e-4 = num / 1e4
    return -(-num // 10_000)


def maker_fee(price: float, fee_type=None, count: int = 1) -> float:
    return maker_fee_e4(price, count) / 10_000 if maker_fee_applies(fee_type) else 0.0


# ----------------------------------------------------------------------------------------------------------- orders
def cents(x: float) -> float:
    return round(x * 100) / 100


def limit_for(side: str, bid, ask):
    """YES buy: yes_bid close.  NO buy: 1 - yes_ask close.  None if the quote is unusable.  Price on the cent grid."""
    if side == "yes":
        return cents(bid) if bid is not None else None
    if side == "no":
        return cents(1.0 - ask) if ask is not None else None
    raise ValueError(side)


def limit_ok(limit) -> bool:
    return limit is not None and PX_LO - EPS <= limit <= PX_HI + EPS


def e0_of(candles, T, lead_s: int = MIN_LEAD_E0):
    """Post time E0 = end of the EARLIEST candle in the market's file; must be <= T - 2h.  -> (E0 | None, reason)."""
    if not candles:
        return None, "no_candles"
    e0 = min(c[0] for c in candles)
    if e0 > T - lead_s:
        return None, "no_post_window"
    return e0, "ok"


def candle_at(candles, ts):
    for c in candles:
        if c[0] == ts:
            return c
    return None


def trades_through(side: str, limit: float, c) -> bool:
    """Strict trade-through of ONE later candle.  YES buy: price.low < limit.  NO buy: price.high > 1 - limit
    (i.e. a YES trade strictly above the YES ask the order sits behind).  A touch at the limit does not count."""
    if side == "yes":
        return c[4] is not None and c[4] < limit - EPS
    if side == "no":
        return c[5] is not None and c[5] > (1.0 - limit) + EPS
    raise ValueError(side)


def is_filled(side: str, limit: float, candles, e0: int, T: int, deadline_s: int = FILL_DEADLINE):
    """Order posted at e0 at `limit`.  Filled iff some LATER candle (end_ts > e0) ending at or before T - 1h trades strictly
    through the limit.  -> (filled, fill_candle_end | None).  Candles ending after T-1h or at/before e0 are never used."""
    for c in sorted(candles, key=lambda x: x[0]):
        if c[0] <= e0 or c[0] > T - deadline_s:
            continue
        if trades_through(side, limit, c):
            return True, c[0]
    return False, None


def net_after_fill(win_value: float, limit: float, fee_type=None, extra: float = 0.0) -> float:
    """Maker fill at the limit (no slippage) held to settlement.  `extra` = the +1c sensitivity line (price paid + extra, fee on that
    price).  win_value = payout of the bought side (1 / 0 / non-binary settlement value)."""
    paid = cents(limit + extra)
    if paid >= 1.0:
        paid = 0.99 if extra else paid
    return win_value - paid - maker_fee(paid, fee_type)


def side_win_value(side: str, settle_yes: float) -> float:
    """settle_yes = the YES settlement value (1 / 0 / scalar).  YES buyer gets it, NO buyer gets 1 - it."""
    return settle_yes if side == "yes" else 1.0 - settle_yes


def settlement_yes(result, settlement_value_dollars=None):
    """yes -> 1, no -> 0, scalar -> settlement value.  -> (value, nonbinary).  ValueError on anything unusable."""
    if result == "yes":
        return 1.0, False
    if result == "no":
        return 0.0, False
    if result == "scalar":
        if settlement_value_dollars in (None, ""):
            raise ValueError("scalar result without settlement value")
        v = float(settlement_value_dollars)
        if not 0.0 <= v <= 1.0:
            raise ValueError("settlement value out of range")
        return v, True
    raise ValueError(f"unknown result {result!r}")


def simulate_order(side, bid, ask, candles, e0, T, settle_yes, fee_type=None):
    """One resting buy posted at e0 (quote = candle close at e0).  -> dict with limit, posted, filled, win_value, net, net1c.
    posted=False when the limit is outside 15-85c or the quote is unusable (no order is posted, nothing is counted)."""
    lim = limit_for(side, bid, ask)
    if not limit_ok(lim):
        return dict(posted=False, filled=False, limit=lim, reason="limit_out_of_range")
    f, fe = is_filled(side, lim, candles, e0, T)
    wv = side_win_value(side, settle_yes)
    return dict(posted=True, filled=f, limit=lim, fill_end=fe, win_value=wv,
                net=net_after_fill(wv, lim, fee_type) if f else None,
                net1c=net_after_fill(wv, lim, fee_type, SENS) if f else None, reason="ok")


# ----------------------------------------------------------------------------------------------------------- split
def in_48_discovery(game_date: str) -> bool:
    return DISC_LO <= game_date <= DISC_HI


def filter_48_rows(rows):
    """Selection rows restricted to the #48 discovery dates.  Applied BEFORE any candle/outcome is read."""
    return [r for r in rows if in_48_discovery(r["game_date"])]


def read_population(path: Path, split: str):
    """#49 population.csv rows of one split ('discovery' | 'unseen'), ordered (T, event).  Applied before any candle/outcome read."""
    if split not in ("discovery", "unseen"):
        raise ValueError(split)
    with open(path, newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["split"] == split]
    return sorted(rows, key=lambda r: (int(r["T"]), r["event"]))


# ----------------------------------------------------------------------------------------------------------- stats
def mean_lb(x):
    x = np.asarray(list(x), float)
    n = len(x)
    if n == 0:
        return dict(n=0, mean=None, sd=None, lb=None)
    m = float(x.mean())
    if n < 2:
        return dict(n=n, mean=m, sd=None, lb=None)
    sd = float(x.std(ddof=1))
    return dict(n=n, mean=m, sd=sd, lb=m - 1.96 * sd / math.sqrt(n))


def select_version(stats, order, min_n: int = MIN_N):
    """Highest LB among versions with n (FILLED) >= min_n.  Ties -> `order`.  None if no version is eligible."""
    best, bl = None, -math.inf
    for v in order:
        s = stats.get(v)
        if s is None or s["n"] < min_n or s["lb"] is None or s["lb"] != s["lb"]:
            continue
        if s["lb"] > bl + 1e-15:
            best, bl = v, s["lb"]
    return best


def bootstrap_lb(nets_by_game: dict, draws: int = BOOT_DRAWS, seed: int = SEED):
    games = sorted(nets_by_game)
    sums = np.array([sum(nets_by_game[g]) for g in games], float)
    cnt = np.array([len(nets_by_game[g]) for g in games], float)
    rng = np.random.default_rng(seed)
    ix = rng.integers(0, len(games), size=(draws, len(games)))
    means = sums[ix].sum(1) / cnt[ix].sum(1)
    return float(sums.sum() / cnt.sum()), float(np.percentile(means, 2.5))


def luck_candidates(real_bets, pool, tol: float = 0.02):
    """#48 price-matched candidates.  real_bets {key: dict(night, stat, limit, player)}; pool list of dict(night, stat, limit, player).
    For each real bet: pool entries on the same night+stat, ANOTHER player, limit within tol.  If none: nearest-priced others
    (fallback, counted); no other player at all -> empty (the bet keeps its own outcome, counted).  -> (cand, n_fallback, n_empty)."""
    from collections import defaultdict
    idx = defaultdict(list)
    for i, p in enumerate(pool):
        idx[(p["night"], p["stat"])].append(i)
    cand, n_fb, n_empty = {}, 0, 0
    for k in sorted(real_bets):
        b = real_bets[k]
        others = [i for i in idx.get((b["night"], b["stat"]), []) if pool[i]["player"] != b["player"]]
        if not others:
            cand[k] = []
            n_empty += 1
            continue
        near = [i for i in others if abs(pool[i]["limit"] - b["limit"]) <= tol + 1e-9]
        if not near:
            dmin = min(abs(pool[i]["limit"] - b["limit"]) for i in others)
            near = [i for i in others if abs(pool[i]["limit"] - b["limit"]) <= dmin + 1e-9]
            n_fb += 1
        cand[k] = near
    return cand, n_fb, n_empty


# ----------------------------------------------------------------------------------------------------------- gates (unseen)
def unseen_gate(here: Path, disc_results_path: Path = None, unseen_results_path: Path = None, audit_path: Path = None,
                unseen49_exists_fn=None):
    """-> (ok, message).  Order: AUDIT_OK, no previous unseen result, discovery results exist, a selected version, luck test PASS,
    and for a #49-family selection the #49 unseen download must exist.  Pure file checks; nothing is read from candles."""
    audit_path = audit_path or (here / "AUDIT_OK")
    unseen_results_path = unseen_results_path or (here / "results_unseen.json")
    disc_results_path = disc_results_path or (here / "results_discovery.json")
    if not audit_path.exists():
        return False, "REFUSED: AUDIT_OK does not exist (Opus has not signed off the audit)."
    if unseen_results_path.exists():
        return False, "REFUSED: results_unseen.json already exists (the unseen run is ONCE only)."
    if not disc_results_path.exists():
        return False, "REFUSED: results_discovery.json missing."
    disc = json.loads(disc_results_path.read_text())
    sel = disc.get("selected_version")
    if not sel:
        return False, "REFUSED: no version was selected on discovery."
    if (disc.get("luck_test") or {}).get("verdict") != "PASS":
        return False, "REFUSED: discovery luck test is not PASS."
    if sel.get("family") == "49":
        fn = unseen49_exists_fn or unseen49_download_exists
        if not fn():
            return False, "REFUSED: #49 unseen download (team/1H candles for the unseen half) does not exist."
    return True, "ok"


def unseen49_download_exists(min_fraction: float = 0.95) -> bool:
    """True iff #49's candles.sqlite 'done' table covers team/1H markets of >= min_fraction of the UNSEEN games (metadata only)."""
    d49 = ROOT / "reports" / "output" / "nba_internal_mismatch"
    try:
        pop = read_population(d49 / "population.csv", "unseen")
        mk = sqlite3.connect(f"file:{(d49 / 'markets.sqlite').as_posix()}?mode=ro", uri=True)
        cdb = sqlite3.connect(f"file:{(d49 / 'candles.sqlite').as_posix()}?mode=ro", uri=True)
        done = {r[0] for r in cdb.execute("SELECT ticker FROM done WHERE note IN ('ok','http_404')")}
        have = 0
        for r in pop:
            suf = r["event"].split("-", 1)[1]
            ts = [t for (t,) in mk.execute("SELECT ticker FROM markets WHERE event_ticker IN (?,?)",
                                           ("KXNBATEAMTOTAL-" + suf, "KXNBA1HTOTAL-" + suf))]
            if any(t in done for t in ts):
                have += 1
        return bool(pop) and have / len(pop) >= min_fraction
    except Exception:   # noqa: BLE001
        return False


# ----------------------------------------------------------------------------------------------------------- candle parsing
def parse_candle(c):
    def f(side, key="close"):
        v = (c.get(side) or {}).get(key)
        return float(v) if v not in (None, "") else None
    vol = c.get("volume")
    return (int(c["end_period_ts"]), f("yes_bid"), f("yes_ask"), float(vol) if vol not in (None, "") else 0.0,
            f("price", "low"), f("price", "high"))


def parse_candles(resp):
    return sorted((parse_candle(c) for c in (resp or {}).get("candlesticks", []) or []), key=lambda t: t[0])
