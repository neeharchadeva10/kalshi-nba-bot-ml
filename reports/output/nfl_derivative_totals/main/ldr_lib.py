"""Research log #68 (NFL derivative totals vs the game line) -- MAIN library.

Implements reports/output/nfl_derivative_totals/PREREGISTRATION.md literally.  Read-only on every input; no network, no
kpm.execution, no keys.  Readings of ambiguous points are listed in main/README.md.

Re-uses (does not modify) the #66 code in reports/output/nfl_totals/main: nfl_lib (games, fee/net arithmetic, game bootstrap,
luck helper, audit helpers) and part_c (candle snapshot at tau, two-sided test, implied median, main-strike picker).

Contamination rule: nothing here prints or saves an outcome statistic.  Outcomes enter only through `attach_actual`, which
refuses rows outside the window the caller names ('select' = ET gameday before 2026-01-01, 'test' = on or after).

Sign convention (spread): S = implied median HOME margin (home points - away points).  A home favourite has S > 0.
  sp_markets rows: team T, strike K -> YES iff T wins by MORE than K (K is a half-integer > 0).
  home-team row  (T = home): point (x = +K, P(home margin > x) = YES mid)
  away-team row  (T = away): YES iff home margin < -K, so P(home margin > -K) = 1 - YES mid: point (x = -K, bid' = 1-ask, ask' = 1-bid)
  S = the first x (ascending) where the mid crosses 0.50, linearly interpolated (C.implied_median).
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent                                        # reports/output/nfl_derivative_totals
NFL_MAIN = STUDY.parent / "nfl_totals" / "main"
if str(NFL_MAIN) not in sys.path:
    sys.path.append(str(NFL_MAIN))                         # appended: this folder's gates.py wins over #66's gates.py
import nfl_lib as N  # noqa: E402
import part_c as C  # noqa: E402

MAIN_OUT = STUDY / "main_out"
KALSHI_DIR = N.KALSHI_DIR                                  # reports/output/nfl_totals/kalshi (written by #66's downloader)
SPREAD_DB = N.ROOT / "data" / "nfl_spread.sqlite"
PBP_DIR = N.PBP_DIR

# ------------------------------------------------------------------ pre-registered constants
SERIES = {"tot": "KXNFLTOTAL", "tt": "KXNFLTEAMTOTAL", "h1": "KXNFL1HTOTAL", "h2": "KXNFL2HTOTAL"}
DERIV_LADDERS = ("tt_home", "tt_away", "h1", "h2")
LADDER_SERIES = {"tot": "KXNFLTOTAL", "tt_home": "KXNFLTEAMTOTAL", "tt_away": "KXNFLTEAMTOTAL", "h1": "KXNFL1HTOTAL", "h2": "KXNFL2HTOTAL"}
FAMILY_OF = {"tt_home": "tt", "tt_away": "tt", "h1": "half", "h2": "half"}
FAMILY_LADDERS = {"tt": ("tt_home", "tt_away"), "half": ("h1", "h2")}
TAUS = ("T24h", "T60")                                      # enumeration order of the grid (tau outermost)
GAPS = (1, 2, 3)
FAMILIES = ("tt", "half")
MAX_SPREAD = 0.10
MIN_BETS = 60
SPLIT_DAY = "2026-01-01"                                    # select: et_date < SPLIT_DAY ; test: >= SPLIT_DAY
R1_SEASONS = tuple(range(2015, 2025))
AGREE_SEED, AGREE_N = 20261028, 100
GAP_TOL = 1e-9
KALSHI_ALIAS = {"JAC": "JAX", "LAR": "LA", "WSH": "WAS", "STL": "LA", "SD": "LAC", "OAK": "LV"}


# ------------------------------------------------------------------ versions
def all_versions() -> list:
    """12 versions, enumeration order: tau (T24h, T60) x g (1, 2, 3) x family (tt, half)."""
    return [f"{fam}|{tau}|g{g}" for tau in TAUS for g in GAPS for fam in FAMILIES]


def parse_version(v: str) -> dict:
    fam, tau, g = v.split("|")
    assert fam in FAMILIES and tau in TAUS and g[0] == "g"
    return {"version": v, "family": fam, "tau": tau, "g": float(g[1:])}


# ------------------------------------------------------------------ series filter, team identification
def series_mask(df: pd.DataFrame, series: str) -> pd.Series:
    """True for rows of ONE series: event_ticker (else ticker) starts with '<series>-'; an explicit `series` column must also match.
    'KXNFLTOTAL-' does not match 'KXNFLTEAMTOTAL-' (different strings), likewise for the 1H / 2H series."""
    key = df["event_ticker"] if "event_ticker" in df.columns else df["ticker"]
    ok = key.astype(str).str.startswith(series + "-")
    if "series" in df.columns:
        ok &= (df["series"] == series)
    return ok


_SUFFIX = re.compile(r"^([A-Z]+)(\d+(?:\.\d+)?)$")


def norm_team(code):
    return KALSHI_ALIAS.get(code, code)


def team_side(suffix, home: str, away: str):
    """KXNFLTEAMTOTAL ladder owner from the market ticker suffix 'PHI43' (team code letters + strike digits): 'home' / 'away' / None.
    The letters are compared with the nflverse home/away codes (Kalshi alias codes JAC/LAR/WSH are normalised first)."""
    m = _SUFFIX.match(str(suffix or ""))
    if not m:
        return None
    c = norm_team(m.group(1))
    h, a = norm_team(home), norm_team(away)
    if c == h and c != a:
        return "home"
    if c == a and c != h:
        return "away"
    return None


def tag_ladders(mk: pd.DataFrame, key: str, games: pd.DataFrame) -> pd.DataFrame:
    """Adds the column `ladder`.  For tt: 'tt_home' / 'tt_away' from team_side (None -> row dropped).  Other series: the series key."""
    mk = mk.copy()
    if key != "tt":
        mk["ladder"] = key
        return mk
    g = games.drop_duplicates("game_id").set_index("game_id")
    home, away = mk.game_id.map(g.home), mk.game_id.map(g.away)
    suf = mk["ticker_suffix"] if "ticker_suffix" in mk.columns else mk.ticker.str.rsplit("-", n=1).str[-1]
    mk["ladder"] = ["tt_" + s if (s := team_side(x, h, a)) else None for x, h, a in zip(suf, home, away)]
    return mk[mk.ladder.notna()].reset_index(drop=True)


# ------------------------------------------------------------------ pbp halves, r1
def implausible_ids() -> set:
    return N.implausible_ids()


def load_halves(seasons, pbp_dir=PBP_DIR, exclude=None) -> pd.DataFrame:
    """End-of-Q2 score per game from the nflverse pbp (Amendment 1): total_home_score / total_away_score (the score at the END of the play)
    on the LAST play with qtr <= 2 (by play_id).  The first Q3 play is not used (it could itself score, e.g. a kickoff-return TD).
    Columns: game_id, h1_home, h1_away.  Games without such a play and excluded ids are absent."""
    exclude = set(implausible_ids() if exclude is None else exclude)
    out = []
    for s in seasons:
        f = Path(pbp_dir) / f"play_by_play_{s}.parquet"
        if not f.exists():
            continue
        p = pd.read_parquet(f, columns=["game_id", "play_id", "qtr", "total_home_score", "total_away_score"])
        p = p[(p.qtr <= 2) & p.total_home_score.notna() & p.total_away_score.notna() & ~p.game_id.isin(exclude)]
        out.append(last_h1_scores(p))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["game_id", "h1_home", "h1_away"])


def last_h1_scores(p: pd.DataFrame) -> pd.DataFrame:
    """p: plays with qtr <= 2 (game_id, play_id, total_home_score, total_away_score).  The row with the largest play_id per game."""
    p = p.sort_values(["game_id", "play_id"], kind="stable").groupby("game_id").tail(1)
    return pd.DataFrame({"game_id": p.game_id.values, "h1_home": p.total_home_score.values.astype(float),
                         "h1_away": p.total_away_score.values.astype(float)})


def fit_r1(games: pd.DataFrame, halves: pd.DataFrame, seasons=R1_SEASONS) -> dict:
    """r1 = mean over games of (1H points / game points), nflverse 2015-2024 (pbp end-of-Q2 score / games.csv final incl. OT).
    Games with a zero final total, a 1H above the final, or a missing half are dropped (counted)."""
    g = games.drop_duplicates("game_id")
    g = g[g.season.isin(seasons) & g.home_score.notna() & g.away_score.notna()]
    m = g[["game_id", "season", "home_score", "away_score"]].merge(halves, on="game_id", how="left")
    n_all = len(m)
    m["h1"] = m.h1_home + m.h1_away
    m["fin"] = m.home_score + m.away_score
    ok = m.h1.notna() & (m.fin > 0) & (m.h1 <= m.fin)
    ratio = (m.h1 / m.fin)[ok]
    return {"r1": float(ratio.mean()), "std": float(ratio.std(ddof=1)), "n_games": int(ok.sum()), "n_games_in_seasons": int(n_all),
            "n_dropped": int(n_all - ok.sum()), "seasons": [int(min(seasons)), int(max(seasons))],
            "definition": "mean over games of (end-of-Q2 points / final points incl OT); pbp last qtr<=2 play total_*_score (Amendment 1); 39 pbp-bad games excluded"}


def load_r1(path=None) -> float:
    p = Path(path) if path else MAIN_OUT / "r1.json"
    if not p.exists():
        raise FileNotFoundError(f"{p} missing: run fit_r1_main.py")
    return float(json.loads(p.read_text())["r1"])


# ------------------------------------------------------------------ settlement values (outcomes) -- only via attach_actual
def actuals_table(games: pd.DataFrame, halves: pd.DataFrame, game_ids=None) -> pd.DataFrame:
    """Per game: home, away, total, h1 (pbp end of Q2), h2 = total - h1 (includes OT).  Indexed by game_id.  NaN where unknown."""
    g = games.drop_duplicates("game_id").set_index("game_id")
    if game_ids is not None:
        g = g.loc[g.index.intersection(list(game_ids))]
    h = halves.drop_duplicates("game_id").set_index("game_id")
    out = pd.DataFrame({"home": g.home_score.astype(float), "away": g.away_score.astype(float)})
    out["total"] = out.home + out.away
    out["h1"] = (h.h1_home + h.h1_away).reindex(out.index)
    out["h2"] = out.total - out.h1
    return out


ACTUAL_COL = {"tot": "total", "tt_home": "home", "tt_away": "away", "h1": "h1", "h2": "h2"}


def window_of_day(et_date) -> str:
    return "select" if str(et_date) < SPLIT_DAY else "test"


def window_rows(tab: pd.DataFrame, window: str) -> pd.DataFrame:
    assert window in ("select", "test")
    w = np.array([window_of_day(d) for d in tab.et_date.values], dtype=object) if len(tab) else np.array([], dtype=object)
    return tab[w == window].copy()


def attach_actual(tab: pd.DataFrame, act: pd.DataFrame, window: str) -> pd.DataFrame:
    """Adds `actual` (the settled value of the row's ladder).  REFUSES if any row is outside `window`."""
    if len(tab):
        w = {window_of_day(d) for d in tab.et_date.values}
        if w - {window}:
            raise RuntimeError(f"attach_actual refused: rows outside the {window} window")
    out = tab.copy()
    vals = []
    for gid, lad in zip(out.game_id.values, out.ladder.values):
        vals.append(act[ACTUAL_COL[lad]].get(gid, np.nan))
    out["actual"] = np.array(vals, dtype=float)
    return out


# ------------------------------------------------------------------ Kalshi loading (derivative + total series, no outcomes used)
MK_COLS = ["ticker", "event_ticker", "series", "floor_strike", "strike_type", "result", "game_id", "match_status", "ticker_suffix",
           "home_code", "away_code"]


def drop_unsettled_derivative(mk: pd.DataFrame) -> pd.DataFrame:
    """Amendment 1 (reading 7): per derivative series (tt, h1, h2) and game, drop ALL its markets unless every one settled 'yes'/'no'
    (as #66's load_kalshi does for KXNFLTOTAL).  The deep KXNFLTOTAL ladder is not filtered (only the fair centre uses it)."""
    d = mk.skey.isin(["tt", "h1", "h2"])
    ok = mk.groupby(["skey", "game_id"]).result.transform(lambda r: bool(r.isin(["yes", "no"]).all()))
    return mk[~d | ok.astype(bool)].reset_index(drop=True)


def load_kalshi_all(kdir=KALSHI_DIR) -> dict:
    """Markets of the four series (each filtered by its own event_ticker prefix), matched to an nflverse game, 'greater' strikes.
    Candle files are cut to those tickers.  Returns {'markets': df with column `skey`, 'c1m', 'c1h'}."""
    kd = Path(kdir)
    mk = pd.read_parquet(kd / "markets.parquet")
    mk = mk[[c for c in MK_COLS if c in mk.columns]]
    parts = []
    for key, ser in SERIES.items():
        m = mk[series_mask(mk, ser)].copy()
        m["skey"] = key
        parts.append(m)
    mk = pd.concat(parts, ignore_index=True)
    mk = mk[(mk.match_status == "matched") & mk.game_id.notna() & (mk.strike_type == "greater") & mk.floor_strike.notna()]
    mk = drop_unsettled_derivative(mk)
    cols = ["ticker", "end_ts", "yes_bid_close_f", "yes_ask_close_f"]
    keep = set(mk.ticker)
    c1m = pd.read_parquet(kd / "candles_1m.parquet", columns=cols)
    c1h = pd.read_parquet(kd / "candles_1h.parquet", columns=cols)
    return {"markets": mk.reset_index(drop=True), "c1m": c1m[c1m.ticker.isin(keep)], "c1h": c1h[c1h.ticker.isin(keep)]}


# ------------------------------------------------------------------ spread ladder (home margin)
def spread_snapshot_frames(mk: pd.DataFrame, quotes: pd.DataFrame, kick: pd.Series, tau: str) -> pd.DataFrame:
    """The spread ladders at decision time kick - offset.  mk: [mid, game_id, team, strike]; quotes: [mid, ts, bid_e4, ask_e4];
    kick: game_id -> kick_s.  For each market the LAST quote with ts in [decision - max_age, decision] (the same windows as the
    totals snapshot at this tau); counted only if two-sided (C.two_sided).  Returns [game_id, mid, team, strike, bid, ask, ts, decision_s]."""
    off, age, _ = C.TAUS[tau]
    m = mk[["mid", "game_id", "team", "strike"]].copy()
    m["decision_s"] = m.game_id.map(kick).astype(float) - off
    q = quotes[["mid", "ts", "bid_e4", "ask_e4"]].merge(m, on="mid", how="inner")
    q = q[(q.ts <= q.decision_s) & (q.ts >= q.decision_s - age)]
    q = q.sort_values(["mid", "ts"], kind="stable").groupby("mid").tail(1)
    bid, ask = q.bid_e4.astype(float).values / 1e4, q.ask_e4.astype(float).values / 1e4
    k = C.two_sided(bid, ask)
    q = q[k]
    return pd.DataFrame({"game_id": q.game_id.values, "mid": q.mid.values, "team": q.team.values, "strike": q.strike.values.astype(float),
                         "bid": bid[k], "ask": ask[k], "ts": q.ts.values, "decision_s": q.decision_s.values}).reset_index(drop=True)


def home_margin_points(is_home: np.ndarray, strike, bid, ask) -> tuple:
    """Spread rows -> points of the home-margin ladder P(home margin > x): (x, bid', ask').  See the module docstring."""
    is_home = np.asarray(is_home, bool)
    strike, bid, ask = (np.asarray(a, float) for a in (strike, bid, ask))
    x = np.where(is_home, strike, -strike)
    b = np.where(is_home, bid, 1.0 - ask)
    a = np.where(is_home, ask, 1.0 - bid)
    return x, b, a


def ladder_median(x, bid, ask, max_spread=MAX_SPREAD):
    """Implied median: strikes with spread <= max_spread, linear interpolation of the YES mid at 0.50 (C.implied_median).  NaN if none."""
    x, bid, ask = (np.asarray(a, float) for a in (x, bid, ask))
    keep = (ask - bid) <= max_spread + 1e-9
    r = C.implied_median(x[keep], ((bid + ask) / 2)[keep]) if keep.sum() >= 2 else None
    return float("nan") if r is None else float(r)


def spread_S(snap_sp: pd.DataFrame, games: pd.DataFrame) -> pd.Series:
    """game_id -> S (implied median HOME margin).  Rows whose team is neither the nflverse home nor away are dropped."""
    if snap_sp.empty:
        return pd.Series(dtype=float)
    g = games.drop_duplicates("game_id").set_index("game_id")
    d = snap_sp.copy()
    d["home"] = d.game_id.map(g.home)
    d["away"] = d.game_id.map(g.away)
    d = d[(d.team == d.home) | (d.team == d.away)]
    out = {}
    for gid, e in d.groupby("game_id"):
        x, b, a = home_margin_points((e.team == e.home).values, e.strike.values, e.bid.values, e.ask.values)
        out[gid] = ladder_median(x, b, a)
    return pd.Series(out, dtype=float)


def load_spread_snapshots(games: pd.DataFrame, game_ids, db=SPREAD_DB) -> dict:
    """{tau: snapshot frame} from the (read-only) sqlite.  Only quotes whose ts is inside the tau window are read."""
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        mk = pd.read_sql("select mid, game_id, team, strike from sp_markets where kind='spread'", con)
        mk = mk[mk.game_id.isin(set(game_ids))]
        kick = games.drop_duplicates("game_id").set_index("game_id").kick_s
        out = {}
        for tau in TAUS:
            off, age, _ = C.TAUS[tau]
            frames = []
            for gid, d in mk.groupby("game_id"):
                k = kick.get(gid)
                if k is None or not np.isfinite(k):
                    continue
                dec = int(k) - off
                ph = ",".join("?" * len(d))
                q = pd.read_sql(f"select mid, ts, bid_e4, ask_e4 from sp_quotes where mid in ({ph}) and ts >= ? and ts <= ?", con,
                                params=list(map(int, d.mid.values)) + [dec - age, dec])
                if len(q):
                    frames.append(q)
            quotes = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["mid", "ts", "bid_e4", "ask_e4"])
            out[tau] = spread_snapshot_frames(mk, quotes, kick, tau)
        return out
    finally:
        con.close()


# ------------------------------------------------------------------ ladders at tau
def snapshots_for(kal: dict, games: pd.DataFrame, tau: str) -> pd.DataFrame:
    """All four series' ladder snapshots at tau (C.snapshot per series, the series' own markets only).  Columns of C.snapshot plus
    `ladder` and `skey`.  Guard: no candle ends after the decision."""
    kick = games.drop_duplicates("game_id").set_index("game_id").kick_s
    tier = C.TAUS[tau][2]
    candles = kal["c1m" if tier == "1m" else "c1h"]
    out = []
    for key in SERIES:
        mk = kal["markets"]
        mk = mk[mk.skey == key]
        mk = tag_ladders(mk, key, games)
        if mk.empty:
            continue
        s = C.snapshot(mk[["ticker", "game_id", "floor_strike"]], candles, kick, tau)
        if s.empty:
            continue
        s = s.merge(mk[["ticker", "ladder"]], on="ticker", how="left")
        s["skey"] = key
        out.append(s)
    if not out:
        return pd.DataFrame(columns=["game_id", "ticker", "strike", "bid", "ask", "end_ts", "decision_s", "tau", "ladder", "skey"])
    s = pd.concat(out, ignore_index=True)
    assert (s.end_ts <= s.decision_s).all(), "look-ahead: a candle ends after the decision time"
    return s


def ladder_table(snap: pd.DataFrame) -> pd.DataFrame:
    """One row per (game, tau, ladder): D (implied median, strikes with spread <= 10c), K/bid/ask (main strike: C.pick_main_strike),
    n_two_sided, n_spread_ok.  NaN D / K where there is no crossing / no main strike."""
    rows = []
    for (gid, tau, lad), d in snap.groupby(["game_id", "tau", "ladder"], sort=True):
        x, b, a = d.strike.values, d.bid.values, d.ask.values
        D = ladder_median(x, b, a)
        i = C.pick_main_strike(x, b, a)
        rows.append((gid, tau, lad, D, np.nan if i is None else float(x[i]), np.nan if i is None else float(b[i]),
                     np.nan if i is None else float(a[i]), int(len(d)), int(((a - b) <= MAX_SPREAD + 1e-9).sum())))
    return pd.DataFrame(rows, columns=["game_id", "tau", "ladder", "D", "K", "bid", "ask", "n_two_sided", "n_spread_ok"])


def fair_centres(M, S, r1) -> dict:
    """The pre-registered fair centres from the deep markets: tt_home (M+S)/2, tt_away (M-S)/2, h1 r1*M, h2 M - r1*M."""
    return {"tt_home": (M + S) / 2.0, "tt_away": (M - S) / 2.0, "h1": r1 * M, "h2": M - r1 * M}


def decision_table(ladders: pd.DataFrame, MS: pd.DataFrame, r1: float, games: pd.DataFrame) -> pd.DataFrame:
    """One row per (game, tau, derivative ladder) with the fair centre, D, gap = D - fair, the main strike quote and `usable`
    (D, fair, K, bid, ask all finite).  ladders: ladder_table of the derivative ladders; MS: [game_id, tau, M, S] (NaN allowed)."""
    t = ladders[ladders.ladder.isin(DERIV_LADDERS)].merge(MS, on=["game_id", "tau"], how="left")
    fair = np.full(len(t), np.nan)
    for lad in DERIV_LADDERS:
        k = (t.ladder == lad).values
        fair[k] = fair_centres(t.M.values[k], t.S.values[k], r1)[lad]
    t["fair"] = fair
    t["gap"] = t.D - t.fair
    t["family"] = t.ladder.map(FAMILY_OF)
    g = games.drop_duplicates("game_id").set_index("game_id")
    t["season"] = t.game_id.map(g.season).values
    t["et_date"] = t.game_id.map(g.et_date).values
    t["usable"] = np.isfinite(t[["D", "fair", "K", "bid", "ask"]].values.astype(float)).all(axis=1)
    return t.reset_index(drop=True)


def build_tables(kal: dict, games: pd.DataFrame, sp_snaps: dict, r1: float) -> dict:
    """Everything price-side (no outcomes).  Returns {'dec': decision table (all derivative ladders, usable flag), 'inputs': per
    (game, tau) M and S, 'strikes': derivative-ladder strikes with spread <= 10c (luck pools), 'ladders': all ladder rows}."""
    dec, ins, strikes, lads = [], [], [], []
    for tau in TAUS:
        snap = snapshots_for(kal, games, tau)
        lt = ladder_table(snap)
        lads.append(lt)
        M = lt[lt.ladder == "tot"].set_index("game_id").D
        S = spread_S(sp_snaps.get(tau, pd.DataFrame()), games) if sp_snaps else pd.Series(dtype=float)
        gids = sorted(set(lt.game_id))
        MS = pd.DataFrame({"game_id": gids, "tau": tau, "M": [M.get(g, np.nan) for g in gids], "S": [S.get(g, np.nan) for g in gids]})
        ins.append(MS)
        dec.append(decision_table(lt, MS, r1, games))
        sk = snap[snap.ladder.isin(DERIV_LADDERS) & ((snap.ask - snap.bid) <= MAX_SPREAD + 1e-9)]
        strikes.append(sk[["game_id", "tau", "ladder", "ticker", "strike", "bid", "ask"]])
    cat = lambda xs: pd.concat(xs, ignore_index=True) if xs else pd.DataFrame()  # noqa: E731
    return {"dec": cat(dec), "inputs": cat(ins), "strikes": cat(strikes), "ladders": cat(lads)}


# ------------------------------------------------------------------ bets
def side_for(gap: float, g: float):
    """gap >= +g -> 'NO' (UNDER); gap <= -g -> 'YES' (OVER); else None.  (1e-9 tolerance against float noise at the threshold.)"""
    if not np.isfinite(gap):
        return None
    if gap >= g - GAP_TOL:
        return "NO"
    if gap <= -g + GAP_TOL:
        return "YES"
    return None


def bets_of(tab: pd.DataFrame, vid: str) -> pd.DataFrame:
    """Bets of one version on a decision table WITH `actual`: game_id, ladder, side, K, ask_side, win, net.
    1 contract at the taker ask (YES: ask; NO: 1 - bid) + 1c, fee 0.07 P (1-P) rounded up (N.kalshi_net).  YES wins iff actual > K."""
    v = parse_version(vid)
    t = tab[(tab.tau == v["tau"]) & (tab.family == v["family"]) & tab.usable].copy()
    cols = ["game_id", "ladder", "side", "K", "ask_side", "win", "net"]
    if t.empty:
        return pd.DataFrame(columns=cols)
    sides = [side_for(g, v["g"]) for g in t.gap.values]
    t["side"] = sides
    t = t[t.side.notna() & t.actual.notna()].copy()
    if t.empty:
        return pd.DataFrame(columns=cols)
    yes = (t.side == "YES").values
    t["ask_side"] = np.where(yes, t.ask.values, 1.0 - t.bid.values)
    over = (t.actual.values > t.K.values)
    t["win"] = np.where(yes, over, ~over).astype(float)
    t["net"] = N.kalshi_net_arr(t.ask_side.values, t.win.values)
    return t[cols].reset_index(drop=True)


def version_row(tab: pd.DataFrame, vid: str) -> dict:
    b = bets_of(tab, vid)
    return {"version": vid, "n": int(len(b)), "mean_pnl": float(b.net.mean()) if len(b) else float("nan"),
            "n_games": int(b.game_id.nunique()) if len(b) else 0}


def strike_nets(strikes: pd.DataFrame, act: pd.DataFrame) -> pd.DataFrame:
    """Adds net_y / net_n (net per contract of buying YES / NO at the real price) to the strike pool rows (needs their outcome)."""
    s = strikes.copy()
    a = np.array([act[ACTUAL_COL[l]].get(g, np.nan) for g, l in zip(s.game_id.values, s.ladder.values)], dtype=float)
    s["actual"] = a
    s = s[np.isfinite(a)].copy()
    win_y = (s.actual.values > s.strike.values).astype(float)
    s["net_y"] = N.kalshi_net_arr(s.ask.values, win_y)
    s["net_n"] = N.kalshi_net_arr(1.0 - s.bid.values, 1.0 - win_y)
    return s


def evaluate(tab: pd.DataFrame, strikes_nets: pd.DataFrame, vid: str) -> dict:
    """Window evaluation of one version: mean net, game bootstrap (10,000, seed 20261023), luck (500 copies, seed 20261024: the same
    number of bets per game on random strikes and sides of the same family's usable ladders at the real prices), verdict (as #66 Part C)."""
    v = parse_version(vid)
    b = bets_of(tab, vid)
    mean = float(b.net.mean()) if len(b) else float("nan")
    m, lo, hi = N.boot_ci_by_game(b.net.values, b.game_id.values)
    usable = tab[(tab.tau == v["tau"]) & (tab.family == v["family"]) & tab.usable][["game_id", "ladder"]].drop_duplicates()
    pool = strikes_nets[strikes_nets.tau == v["tau"]].merge(usable, on=["game_id", "ladder"], how="inner")
    cnt = b.groupby("game_id").size().to_dict()
    per_game, ks = [], []
    for gid, d in pool.groupby("game_id", sort=True):
        per_game.append((d.net_y.values, d.net_n.values))
        ks.append(cnt.get(gid, 0))
    luck = N.luck_random_strikes(per_game, ks, mean)
    res = {"version": vid, "n": int(len(b)), "n_games": int(b.game_id.nunique()) if len(b) else 0, "mean_net": mean, "boot_lo": lo,
           "boot_hi": hi, "luck": luck}
    res["verdict"] = C.verdict_c(res)["verdict"]
    return res


# ------------------------------------------------------------------ counts, agreement sample (no outcomes)
def usable_counts(dec: pd.DataFrame, inputs: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """Counts of ladders per (series, season, tau): n_ladders (with any two-sided quote), n_usable.  For the deep markets: the
    KXNFLTOTAL ladder ('tot', has a median) and the spread ('spread', has S).  Counts only."""
    g = games.drop_duplicates("game_id").set_index("game_id")
    rows = []
    if len(dec):
        d = dec.copy()
        d["series"] = d.ladder.map(LADDER_SERIES)
        for (s, se, tau), x in d.groupby(["series", "season", "tau"]):
            rows.append((s, int(se), tau, int(len(x)), int(x.usable.sum())))
    if len(inputs):
        i = inputs.copy()
        i["season"] = i.game_id.map(g.season).values
        for (se, tau), x in i.groupby(["season", "tau"]):
            rows.append(("KXNFLTOTAL(M)", int(se), tau, int(len(x)), int(x.M.notna().sum())))
            rows.append(("KXNFLSPREAD(S)", int(se), tau, int(len(x)), int(x.S.notna().sum())))
    return pd.DataFrame(rows, columns=["series", "season", "tau", "n_ladders", "n_usable"])


def agree_sample(dec: pd.DataFrame, n: int = AGREE_N, seed: int = AGREE_SEED) -> pd.DataFrame:
    """n random (game, tau, family) snapshots with >= 1 usable ladder (seed 20261028, without replacement, drawn from the sorted list).
    Output one row per usable ladder of the snapshot: fair centre, derivative median, gap, main strike, and the side at g = 1, 2, 3
    ('NO' / 'YES' / 'none').  No outcomes."""
    u = dec[dec.usable]
    snaps = u[["game_id", "tau", "family"]].drop_duplicates().sort_values(["game_id", "tau", "family"]).reset_index(drop=True)
    k = min(n, len(snaps))
    pick = np.random.default_rng(seed).choice(len(snaps), size=k, replace=False) if k else np.array([], int)
    sel = snaps.iloc[sorted(pick)]
    out = sel.merge(u, on=["game_id", "tau", "family"], how="left").sort_values(["game_id", "tau", "family", "ladder"])
    out = out[["game_id", "tau", "family", "ladder", "M", "S", "fair", "D", "gap", "K", "bid", "ask"]].reset_index(drop=True)
    for g in GAPS:
        out[f"side_g{g}"] = [side_for(x, g) or "none" for x in out.gap.values]
    return out


# ------------------------------------------------------------------ bracket gate (data integrity only; needs the download)
def bracket_gate(markets: pd.DataFrame, act: pd.DataFrame) -> pd.DataFrame:
    """For every settled ladder (game, ladder): max strike settled YES < actual <= min strike settled NO.  markets: [skey, ladder, game_id,
    floor_strike, result, event_ticker].  Returns one row per (skey, ladder, game): ok flag etc."""
    rows = []
    m = markets[markets.result.isin(["yes", "no"])]
    for (key, lad, gid), d in m.groupby(["skey", "ladder", "game_id"]):
        a = act[ACTUAL_COL[lad]].get(gid, np.nan)
        yes, no = d.floor_strike[d.result == "yes"], d.floor_strike[d.result == "no"]
        hy = float(yes.max()) if len(yes) else None
        ln = float(no.min()) if len(no) else None
        ok = None if not np.isfinite(a) else bool((hy is None or a > hy) and (ln is None or a <= ln))
        rows.append((key, lad, gid, hy, ln, float(a) if np.isfinite(a) else np.nan, ok))
    return pd.DataFrame(rows, columns=["skey", "ladder", "game_id", "max_yes", "min_no", "actual", "ok"])


# ------------------------------------------------------------------ choose / audit
def choose(rows: pd.DataFrame):
    return N.choose_best(rows, MIN_BETS)


OWN_AUDIT = ("ldr_lib.py", "gates.py", "run_select.py", "run_test.py", "make_audit_hashes.py", "fit_r1_main.py", "../PREREGISTRATION.md",
             "../main_out/r1.json", "../main_out/gates.json", "../main_out/agree_snapshots.csv", "../results_select.json")
REUSED_AUDIT = ("../../nfl_totals/main/nfl_lib.py", "../../nfl_totals/main/part_c.py")
INPUT_AUDIT = ("../../../../data/raw/nflverse/games/games.csv", "../../../../data/raw/nflverse/pbp/manifest.json",
               "../../nfl_totals/data/pbp_running_vs_final_implausible.csv", "../../../../data/nfl_spread.sqlite",
               "../../nfl_totals/kalshi/markets.parquet", "../../nfl_totals/kalshi/events.parquet",
               "../../nfl_totals/kalshi/candles_1h.parquet", "../../nfl_totals/kalshi/candles_1m.parquet")
AUDIT_FILES = OWN_AUDIT + REUSED_AUDIT + INPUT_AUDIT


def audit_hashes(base=HERE, files=AUDIT_FILES) -> dict:
    return N.audit_hashes(base, files)


def check_audit_ok(study=STUDY, base=HERE, files=AUDIT_FILES):
    """Refuse unless ../AUDIT_OK covers every audited file with matching hashes AND ../test_started.json exists and ../results_test.json
    does not (the holdout runs once).  Nothing outcome-related is loaded before this passes."""
    N.check_audit_ok(study, base, files)


# ------------------------------------------------------------------ one-call loaders for the run scripts
def price_tables(games: pd.DataFrame, r1: float, kdir=KALSHI_DIR, db=SPREAD_DB) -> tuple:
    """(tables from build_tables, kal) from the downloaded Kalshi parquet files and the spread sqlite.  Price side only."""
    kal = load_kalshi_all(kdir)
    ids = sorted(set(kal["markets"].game_id))
    sp = load_spread_snapshots(games, ids, db)
    return build_tables(kal, games, sp, r1), kal


def settle_actuals(games: pd.DataFrame, game_ids) -> pd.DataFrame:
    """Settlement values for the given games (home/away from games.csv, 1H from pbp end of Q2, 2H = final - 1H incl. OT)."""
    seasons = sorted({int(s) for s in games[games.game_id.isin(set(game_ids))].season})
    return actuals_table(games, load_halves(seasons), game_ids)
