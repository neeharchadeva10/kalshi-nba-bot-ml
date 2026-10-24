"""Research log #66: Kalshi ladder machinery (shared by Parts A-HK, B-HK and C) and Part C (alt lines).

Schema (kalshi/README.md and kalshi/smoke/): markets.parquet [ticker, event_ticker, floor_strike, strike_type ('greater'), result,
game_id, match_status], candles_1h.parquet / candles_1m.parquet [ticker, end_ts (epoch s, period END), yes_bid_close_f, yes_ask_close_f],
events.parquet [event_ticker, game_id, kickoff_utc (epoch s), ...].  Candles are sparse.  Strike s settles YES iff total > s.

Nothing here loads outcomes except functions that take a `totals` Series explicitly.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import nfl_lib as N

T60_OFFSET_S, T60_MAX_AGE_S = 3600, 3600                  # decision = kickoff - 60 min; last 1-minute candle ending in [decision-60min, decision]
T24_OFFSET_S, T24_MAX_AGE_S = 24 * 3600, 3 * 3600         # decision = kickoff - 24 h; last hourly candle ending in [decision-3h, decision]
TAUS = {"T60": (T60_OFFSET_S, T60_MAX_AGE_S, "1m"), "T24h": (T24_OFFSET_S, T24_MAX_AGE_S, "1h")}
NEAR_ZONE = 7.0
C_MARGINS = (0.0, 0.02, 0.04)
ZONES = ("all", "near", "far")
LS_MAX_ASK = 0.10
BANDS_LO = (41.0, 45.0, 49.0)                              # band 0 < 41 <= band 1 < 45 <= band 2 < 49 <= band 3 (lines are half-integers)
KERNEL_BW = 2.0
W_CLIP = (0.4, 2.0)
MAX_T = 120
MAX_SPREAD = N.MAX_SPREAD


# ------------------------------------------------------------------ quotes, picker, implied median
def two_sided(bid, ask) -> np.ndarray:
    """A usable two-sided quote: both close values present, bid >= 1c, ask <= 99c, ask >= bid (0.00/0.99 are empty-book placeholders)."""
    bid, ask = np.asarray(bid, float), np.asarray(ask, float)
    with np.errstate(invalid="ignore"):
        return np.isfinite(bid) & np.isfinite(ask) & (bid >= 0.01 - 1e-9) & (ask <= 0.99 + 1e-9) & (ask >= bid - 1e-9)


SERIES = "KXNFLTOTAL"
HK_SEASONS = (2025, 2026)


def is_total_series(df: pd.DataFrame) -> pd.Series:
    """True for KXNFLTOTAL rows only (the download also holds KXNFLTEAMTOTAL / KXNFL1HTOTAL / KXNFL2HTOTAL with the same ticker format).
    Uses event_ticker when present (startswith 'KXNFLTOTAL-'), else ticker; an explicit `series` column, when present, must also match."""
    key = df["event_ticker"] if "event_ticker" in df.columns else df["ticker"]
    ok = key.astype(str).str.startswith(SERIES + "-")
    if "series" in df.columns:
        ok &= (df["series"] == SERIES)
    return ok


def implied_median_pair(strikes, mids):
    """Like implied_median but returns (M, i_lo, i_hi) with the indices (into the ORIGINAL arrays) of the two strikes of the crossing, or None."""
    s = np.asarray(strikes, float)
    m = np.asarray(mids, float)
    o = np.argsort(s, kind="stable")
    for a, b in zip(o[:-1], o[1:]):
        if m[a] >= 0.5 >= m[b]:
            return (float(s[a]) if m[a] == m[b] else float(s[a] + (m[a] - 0.5) / (m[a] - m[b]) * (s[b] - s[a])), int(a), int(b))
    return None


def implied_median(strikes, mids):
    """Linear interpolation of the YES mid across strikes (ascending) at 0.50: the first consecutive pair with m1 >= 0.5 >= m2.
    None if the ladder never crosses 0.50 (or has fewer than two strikes)."""
    s = np.asarray(strikes, float)
    m = np.asarray(mids, float)
    o = np.argsort(s, kind="stable")
    s, m = s[o], m[o]
    for i in range(len(s) - 1):
        if m[i] >= 0.5 >= m[i + 1]:
            return float(s[i]) if m[i] == m[i + 1] else float(s[i] + (m[i] - 0.5) / (m[i] - m[i + 1]) * (s[i + 1] - s[i]))
    return None


def pick_main_strike(strikes, bids, asks):
    """The two-sided strike with spread <= 10c whose YES mid is nearest 0.50 (ties -> the lower strike).  Returns the index into the
    arrays, or None."""
    s, b, a = np.asarray(strikes, float), np.asarray(bids, float), np.asarray(asks, float)
    ok = two_sided(b, a) & ((a - b) <= MAX_SPREAD + 1e-9)
    if not ok.any():
        return None
    idx = np.nonzero(ok)[0]
    key = [(round(abs((b[i] + a[i]) / 2 - 0.5), 9), s[i]) for i in idx]
    return int(idx[min(range(len(idx)), key=lambda j: key[j])])


def snapshot(markets: pd.DataFrame, candles: pd.DataFrame, kick: pd.Series, tau: str) -> pd.DataFrame:
    """The ladder at decision time kick - offset: for every market ticker the LAST candle with end_ts in [decision - max_age, decision];
    the quote counts only if that candle's yes_bid_close / yes_ask_close are present and two-sided.
    markets: [ticker, game_id, floor_strike]; candles: [ticker, end_ts, yes_bid_close_f, yes_ask_close_f]; kick: Series game_id -> kick_s.
    Returns [game_id, ticker, strike, bid, ask, end_ts, decision_s, tau] (two-sided rows only)."""
    off, age, _ = TAUS[tau]
    mk = markets[["ticker", "game_id", "floor_strike"]].dropna(subset=["game_id"]).copy()
    mk["decision_s"] = mk.game_id.map(kick).astype(float) - off
    c = candles[["ticker", "end_ts", "yes_bid_close_f", "yes_ask_close_f"]].merge(mk, on="ticker", how="inner")
    c = c[(c.end_ts <= c.decision_s) & (c.end_ts >= c.decision_s - age)]
    c = c.sort_values(["ticker", "end_ts"], kind="stable").groupby("ticker").tail(1)
    c = c[two_sided(c.yes_bid_close_f.values, c.yes_ask_close_f.values)]
    out = pd.DataFrame({"game_id": c.game_id.values, "ticker": c.ticker.values, "strike": c.floor_strike.values.astype(float),
                        "bid": c.yes_bid_close_f.values.astype(float), "ask": c.yes_ask_close_f.values.astype(float),
                        "end_ts": c.end_ts.values, "decision_s": c.decision_s.values, "tau": tau})
    return out.sort_values(["game_id", "strike"], kind="stable").reset_index(drop=True)


def add_median(snap: pd.DataFrame, max_spread=None) -> pd.DataFrame:
    """Adds M (implied median of the game's two-sided ladder; NaN if no crossing) and M_wide (the crossing involves a strike with spread
    > 10c) to every row.  max_spread: if given, only strikes with spread <= max_spread enter the median (the Amendment 6 fallback)."""
    ms, wide = {}, {}
    if snap.empty:
        return snap.assign(M=pd.Series(dtype=float), M_wide=pd.Series(dtype=bool))
    for gid, d in snap.groupby("game_id"):
        if max_spread is not None:
            d = d[(d.ask - d.bid) <= max_spread + 1e-9]
        r = implied_median_pair(d.strike.values, (d.bid.values + d.ask.values) / 2) if len(d) else None
        if r is None:
            ms[gid], wide[gid] = np.nan, False
        else:
            ms[gid] = r[0]
            sp = (d.ask.values - d.bid.values)
            wide[gid] = bool(sp[r[1]] > MAX_SPREAD + 1e-9 or sp[r[2]] > MAX_SPREAD + 1e-9)
    out = snap.copy()
    out["M"] = out.game_id.map(ms).astype(float)
    out["M_wide"] = out.game_id.map(wide).astype(bool)
    return out


def main_strike_table(snap: pd.DataFrame) -> pd.DataFrame:
    """One row per game of `snap` with M and a main strike: game_id, M, K, bid, ask (YES quotes of the main strike).  Games with no
    M or no main strike are absent.  No outcomes."""
    rows = []
    if snap.empty:
        return pd.DataFrame(columns=["game_id", "M", "K", "bid", "ask"])
    for gid, d in add_median(snap).groupby("game_id"):
        M = d.M.iloc[0]
        i = pick_main_strike(d.strike.values, d.bid.values, d.ask.values)
        if i is None or not np.isfinite(M):
            continue
        rows.append((gid, float(M), float(d.strike.values[i]), float(d.bid.values[i]), float(d.ask.values[i])))
    return pd.DataFrame(rows, columns=["game_id", "M", "K", "bid", "ask"])


def with_outcomes(tab: pd.DataFrame, totals: pd.Series) -> pd.DataFrame:
    """Adds total, y (YES settles: total > K), net_over (buy YES at the ask) and net_under (buy NO at 1 - bid) per contract.
    Rows whose game has no total are dropped."""
    t = tab.copy()
    t["total"] = t.game_id.map(totals).astype(float)
    t = t[t.total.notna()].copy()
    t["y"] = (t.total > t.K).astype(float)
    t["net_over"] = N.kalshi_net_arr(t.ask.values, t.y.values)
    t["net_under"] = N.kalshi_net_arr(1.0 - t.bid.values, 1.0 - t.y.values)
    return t.reset_index(drop=True)


# ------------------------------------------------------------------ loaders
def load_kalshi(kdir=N.KALSHI_DIR) -> dict:
    """KXNFLTOTAL markets only (Amendment 6, B-1).  Games whose KXNFLTOTAL markets are not ALL settled yes/no are dropped.
    kal['counts'] reports the games before/after each filter."""
    kd = Path(kdir)
    mk = pd.read_parquet(kd / "markets.parquet")
    mk = mk[[c for c in ["ticker", "event_ticker", "series", "floor_strike", "strike_type", "result", "game_id", "match_status"] if c in mk.columns]]
    mk = mk[is_total_series(mk) & (mk.match_status == "matched") & mk.game_id.notna()]
    settled = mk.groupby("game_id").result.apply(lambda r: bool(r.isin(["yes", "no"]).all()))
    counts = {"n_games_total_series_matched": int(len(settled)), "n_games_dropped_unsettled": int((~settled).sum())}
    mk = mk[mk.game_id.isin(settled[settled].index) & (mk.strike_type == "greater")]
    cols = ["ticker", "end_ts", "yes_bid_close_f", "yes_ask_close_f"]
    c1m = pd.read_parquet(kd / "candles_1m.parquet", columns=cols)
    c1h = pd.read_parquet(kd / "candles_1h.parquet", columns=cols)
    keep = set(mk.ticker)                                           # candles of other series never enter
    return {"markets": mk.reset_index(drop=True), "c1m": c1m[c1m.ticker.isin(keep)], "c1h": c1h[c1h.ticker.isin(keep)], "counts": counts}


def restrict_hk(markets: pd.DataFrame, games: pd.DataFrame) -> tuple:
    """KXNFLTOTAL rows of nflverse seasons 2025-2026 only (HK).  Returns (markets, counts)."""
    mk = markets[is_total_series(markets)]
    season = games.drop_duplicates("game_id").set_index("game_id").season
    s = mk.game_id.map(season)
    keep = mk[s.isin(HK_SEASONS)]
    counts = {"n_games_before_season_filter": int(mk.game_id.nunique()), "n_games_hk_seasons_2025_2026": int(keep.game_id.nunique())}
    return keep, counts


def build_snapshots(kal: dict, games: pd.DataFrame) -> dict:
    """{'T60': snap, 'T24h': snap} with M; decision times from the nflverse kickoff.  Candles end <= decision by construction."""
    kick = games.drop_duplicates("game_id").set_index("game_id").kick_s
    out = {}
    markets, _ = restrict_hk(kal["markets"], games)                  # KXNFLTOTAL only, seasons 2025-2026 only
    for tau, (_, _, tier) in TAUS.items():
        out[tau] = add_median(snapshot(markets, kal["c1m" if tier == "1m" else "c1h"], kick, tau))
    return out


def hk_split(game_ids, games: pd.DataFrame) -> pd.Series:
    """'HK-sel' (ET gameday before 2026-01-01) or 'HK-test' (on or after)."""
    day = games.drop_duplicates("game_id").set_index("game_id").et_date
    d = pd.Series(game_ids).map(day)
    return pd.Series(np.where(d.values < N.HK_SPLIT_DAY, "HK-sel", "HK-test"), index=range(len(d)))


# ------------------------------------------------------------------ fair model (Amendment 0.5)
def band_of(M: float) -> int:
    return int(sum(M >= b for b in BANDS_LO))


def _phi(z):
    return np.exp(-0.5 * z * z) / np.sqrt(2 * np.pi)


def fit_fair(close: np.ndarray, total: np.ndarray) -> dict:
    """Fit on nflverse finals (the caller passes 1999-2024 only).  r = total - close; r by close band; totals for the landing weights."""
    close, total = np.asarray(close, float), np.asarray(total, float)
    r = total - close
    band = np.array([band_of(c) for c in close])
    t = np.arange(MAX_T + 1, dtype=float)
    emp = np.array([(total == x).mean() for x in t])
    smooth = np.array([_phi((x - total) / KERNEL_BW).mean() / KERNEL_BW for x in t])
    w = np.where(smooth > 1e-12, np.clip(emp / np.where(smooth > 1e-12, smooth, 1.0), *W_CLIP), 1.0)
    return {"r": [r[band == b] for b in range(4)], "w": w, "n": int(len(total))}


def fair_pmf(model: dict, M: float) -> np.ndarray:
    """P(T = t | M), t = 0..120: proportional to f_b(t - M) * w_t, f_b = Gaussian KDE (bandwidth 2) of the band's residuals; band from M."""
    r = model["r"][band_of(M)]
    t = np.arange(MAX_T + 1, dtype=float)
    f = _phi(((t - M)[:, None] - r[None, :]) / KERNEL_BW).mean(axis=1) / KERNEL_BW
    p = f * model["w"]
    s = p.sum()
    return p / s if s > 0 else np.full(len(t), np.nan)


def fair_yes(pmf: np.ndarray, strike: float) -> float:
    """Sum of P(T = t) over t > strike."""
    return float(pmf[int(np.floor(strike)) + 1:].sum())


def save_fair(model: dict, path):
    np.savez(path, r0=model["r"][0], r1=model["r"][1], r2=model["r"][2], r3=model["r"][3], w=model["w"], n=model["n"])


def load_fair(path) -> dict:
    z = np.load(path)
    return {"r": [z["r0"], z["r1"], z["r2"], z["r3"]], "w": z["w"], "n": int(z["n"])}


# ------------------------------------------------------------------ Part C tables and versions
def all_versions_c() -> list:
    v = [f"C|{tau}|m{int(round(m * 100))}|{z}" for tau in ("T24h", "T60") for m in C_MARGINS for z in ZONES]
    return v + [f"C-LS|{tau}" for tau in ("T24h", "T60")]


def parse_version_c(v: str) -> dict:
    p = v.split("|")
    if p[0] == "C":
        return {"version": v, "fam": "C", "tau": p[1], "m": int(p[2][1:]) / 100.0, "zone": p[3]}
    if p[0] == "C-LS":
        return {"version": v, "fam": "C-LS", "tau": p[1], "m": None, "zone": None}
    raise ValueError(v)


def prepare_c_table(snap: pd.DataFrame, model: dict, totals: pd.Series | None = None) -> pd.DataFrame:
    """One row per (game, strike) quote of a snapshot with M: fair YES, all-in cost of each side, and (if totals given) the realised net
    per contract of buying YES (net_y) / NO (net_n).  Games without M are dropped."""
    s = snap[snap.M.notna()].copy()
    if s.empty:
        base = list(snap.columns) + ["fair_yes", "p_y", "cost_y", "p_n", "cost_n", "dist"] + (["total", "net_y", "net_n"] if totals is not None else [])
        return pd.DataFrame({c: pd.Series(dtype=float) for c in dict.fromkeys(base)})
    pm = {}
    for gid, M in s.groupby("game_id").M.first().items():
        pm[gid] = fair_pmf(model, float(M))
    s["fair_yes"] = [fair_yes(pm[g], k) for g, k in zip(s.game_id.values, s.strike.values)]
    s["p_y"] = np.minimum(s.ask.values + N.SLIP, 1.0)
    s["cost_y"] = s.p_y + np.array([N.kalshi_fee(p) for p in s.p_y.values])
    ask_no = 1.0 - s.bid.values
    s["p_n"] = np.minimum(ask_no + N.SLIP, 1.0)
    s["cost_n"] = s.p_n + np.array([N.kalshi_fee(p) for p in s.p_n.values])
    s["dist"] = (s.strike - s.M).abs()
    if totals is not None:
        t = s.game_id.map(totals).astype(float)
        s["total"] = t
        s = s[t.notna()].copy()
        win_y = (s.total > s.strike).astype(float)
        s["net_y"] = win_y - s.cost_y
        s["net_n"] = (1.0 - win_y) - s.cost_n
    return s.reset_index(drop=True)


def c_bets(tab: pd.DataFrame, vid: str) -> pd.DataFrame:
    """Bets of one Part C version on a prepared table (needs net_y/net_n): game_id, strike, side ('YES'/'NO'), net.
    C: buy YES if cost_y < fair_yes - m; buy NO if cost_n < (1 - fair_yes) - m; at most one side per strike (the larger edge)."""
    v = parse_version_c(vid)
    t = tab[tab.tau == v["tau"]] if "tau" in tab.columns else tab
    if v["fam"] == "C-LS":
        k = (t.ask.values <= LS_MAX_ASK + 1e-9)
        return pd.DataFrame({"game_id": t.game_id.values[k], "strike": t.strike.values[k], "side": "NO", "net": t.net_n.values[k]})
    m = v["m"]
    ey = t.fair_yes.values - m - t.cost_y.values
    en = (1.0 - t.fair_yes.values) - m - t.cost_n.values
    zone_ok = {"all": np.ones(len(t), bool), "near": t.dist.values <= NEAR_ZONE + 1e-9, "far": t.dist.values > NEAR_ZONE + 1e-9}[v["zone"]]
    buy_y = (ey > 0) & (ey >= en) & zone_ok
    buy_n = (en > 0) & (en > ey) & zone_ok
    k = buy_y | buy_n
    net = np.where(buy_y, t.net_y.values, t.net_n.values)
    return pd.DataFrame({"game_id": t.game_id.values[k], "strike": t.strike.values[k], "side": np.where(buy_y[k], "YES", "NO"), "net": net[k]})


def version_row_c(tab: pd.DataFrame, vid: str) -> dict:
    b = c_bets(tab, vid)
    return {"version": vid, "n": int(len(b)), "mean_pnl": float(b.net.mean()) if len(b) else float("nan"),
            "n_games": int(b.game_id.nunique()) if len(b) else 0}


def evaluate_c(tab_test: pd.DataFrame, vid: str) -> dict:
    """HK-test evaluation of the chosen Part C version: mean net, game-bootstrap CI, luck (random strikes/sides at real prices)."""
    b = c_bets(tab_test, vid)
    v = parse_version_c(vid)
    t = tab_test[tab_test.tau == v["tau"]] if "tau" in tab_test.columns else tab_test
    mean = float(b.net.mean()) if len(b) else float("nan")
    m, lo, hi = N.boot_ci_by_game(b.net.values, b.game_id.values)
    per_game, ks = [], []
    cnt = b.groupby("game_id").size().to_dict()
    for gid, d in t.groupby("game_id"):
        per_game.append((d.net_y.values, d.net_n.values))
        ks.append(cnt.get(gid, 0))
    luck = N.luck_random_strikes(per_game, ks, mean)
    return {"version": vid, "n": int(len(b)), "n_games": int(b.game_id.nunique()) if len(b) else 0, "mean_net": mean, "boot_lo": lo,
            "boot_hi": hi, "luck": luck}


def verdict_c(res: dict) -> dict:
    """PASS: HK-test mean net > 0 with game-bootstrap LB > 0 and >= 30 bets and luck < 5%.  PROMISING: mean > 0 and luck < 10%.  Else FAIL."""
    gt0 = lambda x: bool(np.isfinite(x) and x > 0)          # noqa: E731
    lf = res["luck"]["frac_copies_ge_actual"]
    passed = gt0(res["mean_net"]) and gt0(res["boot_lo"]) and res["n"] >= 30 and bool(np.isfinite(lf) and lf < 0.05)
    prom = (not passed) and gt0(res["mean_net"]) and bool(np.isfinite(lf) and lf < 0.10)
    return {"verdict": "PASS" if passed else ("PROMISING" if prom else "FAIL")}
