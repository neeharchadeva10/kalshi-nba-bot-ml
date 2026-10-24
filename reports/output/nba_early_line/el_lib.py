"""Research log #58 library: does the points formula beat Kalshi's EARLY total line?  (read-only study)

Everything here is pure or reads files already on disk; nothing touches the network or any order/portfolio endpoint.
Reused code (loaded by file path, not edited): #44 implied_total, #46 fee_taker, #56 fa_lib / run_confirm, #42 grid code.
This module must not be shadowed: its name is unique on purpose (#44/#46 have common.py / pipeline.py)."""
from __future__ import annotations

import bisect
import hashlib
import importlib.util
import json
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
RO = ROOT / "reports" / "output"
INJ = RO / "nba_injury_speed"
INGAME = RO / "nba_ingame_overreaction"
FA_DIR = RO / "nba_formula_accuracy"
GRID_DIR = RO / "nba_totals_grid"
PACE_DIR = RO / "nba_totals_pace"

SEED = 20261022
DISC_FIRST, DISC_LAST = "2025-10-21", "2026-01-22"       # ET game dates, inclusive
UNSEEN_FIRST, UNSEEN_LAST = "2026-01-23", "2026-04-12"   # last regular-season ET date in events_list
TE_HOURS = (8, 6, 4)                                      # t_e = T - 8h / 6h / 4h
GAPS = (2, 4, 6)
FORMS = ("F56", "F42c")
MAX_AGE_S = 1800                                          # quote / candle staleness limit (30 min)
LINE_SPREAD = 0.15
MAIN_SPREAD = 0.10
MAIN_MID = (0.15, 0.85)
SLIP = 0.01
MIN_N = 60
LEAN = 4.0                                                # F42c = #42 winner + 4.0 points
HOURLY_EXCLUDE_GAP = 1.5
REPS = 500
N_BOOT = 10000


# ------------------------------------------------------------------ loading reused code
def _load_by_path(name, path):
    """Import a file under a unique module name; undo any sys.path changes it makes (#44's common.py inserts folders)."""
    saved = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location(name, str(path))
        m = importlib.util.module_from_spec(spec)
        sys.modules[name] = m
        spec.loader.exec_module(m)
    finally:
        sys.path[:] = saved
    return m


C44 = _load_by_path("el_ext_c44", INJ / "common.py")            # implied_total (also #46 has a copy)
C46 = _load_by_path("el_ext_c46", INGAME / "common.py")         # fee_taker
implied_total = C44.implied_total
fee_taker = C46.fee_taker


def load_fa():
    """(fa_lib, run_confirm, grid_lib, grid_run) imported the way #56's repro_check / run_confirm do, with our folder kept first."""
    if str(FA_DIR) not in sys.path:
        sys.path.insert(1, str(FA_DIR))
    import fa_lib as fa
    import grid_lib as gl
    import run_confirm as rc
    import grid_run as gr
    return fa, rc, gl, gr


# ------------------------------------------------------------------ quotes (pure)
def quotes_at(series, t, max_age=MAX_AGE_S):
    """series: {ticker: (strike, ends[sorted], bids, asks)}.  For each market the LAST candle ending at or before t and no
    older than max_age.  Returns [(ticker, strike, bid, ask, end_ts)].  A candle ending after t is never returned."""
    out = []
    for tk, (strike, ends, bids, asks) in series.items():
        i = bisect.bisect_right(ends, t)
        if i == 0:
            continue
        e = ends[i - 1]
        if t - e > max_age:
            continue
        assert e <= t
        out.append((tk, strike, bids[i - 1], asks[i - 1], e))
    return out


def line_from(points):
    """Implied median total (YES-mid 0.50 crossing; books with spread <= 0.15) from quotes_at points, or None."""
    return implied_total([(s, b, a) for _, s, b, a, _ in points])


def main_strike(points):
    """YES mid nearest 0.50 among 0<bid<=ask<1, spread <= 0.10, mid within 15-85c.  Ties -> the lower strike.
    Returns (ticker, strike, bid, ask, end_ts) or None."""
    best = None
    for tk, s, b, a, e in points:
        try:
            b, a = float(b), float(a)
        except (TypeError, ValueError):
            continue
        if not (0 < b <= a < 1) or a - b > MAIN_SPREAD + 1e-9:
            continue
        mid = (a + b) / 2
        if mid < MAIN_MID[0] - 1e-12 or mid > MAIN_MID[1] + 1e-12:
            continue
        key = (round(abs(mid - 0.5), 9), float(s))
        if best is None or key < best[0]:
            best = (key, (tk, float(s), b, a, int(e)))
    return None if best is None else best[1]


def price_paid(side, yes_bid, yes_ask):
    """Taker entry: YES (over) at yes_ask; NO (under) at 1 - yes_bid; plus 1c slippage, capped at 99c."""
    ask = yes_ask if side == "over" else 1.0 - yes_bid
    return min(round(ask + SLIP, 10), 0.99)


def bet_net(side, result, yes_bid, yes_ask):
    """Net per contract held to settlement: win - paid - fee (fee 0.07*P*(1-P) rounded up to $0.0001 at the PAID price)."""
    p = price_paid(side, yes_bid, yes_ask)
    win = 1.0 if ((side == "over" and result == "yes") or (side == "under" and result == "no")) else 0.0
    return win - p - fee_taker(p)


def formula_side(proj, line):
    return "over" if proj > line else "under"


# ------------------------------------------------------------------ per game records
def game_records(event, T, series, results, te_hours=TE_HOURS):
    """series: {ticker: (strike, ends, bids, asks)} holding ONLY candles ending at or before T-3600 (the loader enforces it).
    results: {ticker: 'yes'/'no'/'scalar'}.  One dict per t_e plus the T-1h line from the same 1-minute table."""
    out = []
    l1h_pts = quotes_at(series, T - 3600)
    l_t1h = line_from(l1h_pts)
    for h in te_hours:
        te = T - h * 3600
        pts = quotes_at(series, te)
        assert all(p[4] <= te for p in pts), "look-ahead: a candle ending after t_e was used"
        L = line_from(pts)
        ms = main_strike(pts)
        rec = {"event": event, "T": int(T), "te_h": h, "te": int(te), "n_quoted": len(pts), "L_e": L, "L_T1h_1m": l_t1h,
               "skip": None, "ticker": None, "strike": np.nan, "yes_bid": np.nan, "yes_ask": np.nan, "age_s": np.nan,
               "result": None, "net_over": np.nan, "net_under": np.nan, "paid_over": np.nan, "paid_under": np.nan}
        if L is None:
            rec["skip"] = "no_early_line"
        elif ms is None:
            rec["skip"] = "no_main_strike"
        else:
            tk, s, b, a, e = ms
            res = results.get(tk)
            rec.update(ticker=tk, strike=s, yes_bid=b, yes_ask=a, age_s=te - e, result=res)
            if res not in ("yes", "no"):
                rec["skip"] = "no_result"
            else:
                rec.update(net_over=bet_net("over", res, b, a), net_under=bet_net("under", res, b, a),
                           paid_over=price_paid("over", b, a), paid_under=price_paid("under", b, a))
        out.append(rec)
    return out


# ------------------------------------------------------------------ data access
_G42 = {}


def games_42():
    """#42's game table (prep.build_games via nba_totals_v2/run.load_games): T, hoopr ids, tip, T-1h market median.  No outcomes."""
    if "g" not in _G42:
        fa, rc, gl, gr = load_fa()
        games, strikes, info = gr.v2.load_games()
        g = games[(games.season == "2025-26") & (games.season_type == 2)].reset_index(drop=True)
        _G42["g"] = g
    return _G42["g"]


def load_universe(first, last, allow_unseen=False):
    """Regular-season 2025-26 games with ET date in [first, last] (events_list game_date_et), joined to #42's table
    (T, hoopR id, tip, away/home, T-1h median).  Dates are filtered BEFORE any result or final is read."""
    if not allow_unseen and last > DISC_LAST:
        raise ValueError(f"discovery code may not load dates after {DISC_LAST}")
    if allow_unseen and first < UNSEEN_FIRST:
        raise ValueError(f"unseen code may not load dates before {UNSEEN_FIRST}")
    ev = pd.read_csv(INJ / "events_list.csv")
    ev = ev[(ev.game_date_et >= first) & (ev.game_date_et <= last) & (ev.season_type == 2) & ev.hoopr_gid.notna()]
    ev = ev[["event", "game_date_et", "T"]].rename(columns={"game_date_et": "date", "T": "T_events_list"})
    g = games_42()[["event", "T", "hoopr_id", "tip", "away", "home", "median"]].rename(columns={"median": "median_T1h"})
    u = ev.merge(g, on="event", how="inner").sort_values(["date", "event"]).reset_index(drop=True)
    u["T"] = u["T"].astype(int)
    u["hoopr_id"] = u.hoopr_id.astype(int)
    return u, {"events_list_rows": int(len(ev)), "joined_to_42": int(len(u)), "not_in_42": sorted(set(ev.event) - set(u.event))}


def connect_ro(path):
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def load_series_1m(conn, event, T):
    """1-minute candles of one event with end_ts <= T-3600 ONLY (nothing later is ever loaded).  Returns (series, results)."""
    mk = conn.execute("SELECT ticker, strike, result FROM mkts WHERE event=?", (event,)).fetchall()
    strikes = {t: s for t, s, _ in mk}
    results = {t: r for t, _, r in mk}
    series = {}
    if not strikes:
        return series, results
    q = ",".join("?" * len(strikes))
    rows = conn.execute(f"SELECT ticker, end_ts, bid, ask FROM candles WHERE ticker IN ({q}) AND end_ts<=? ORDER BY ticker, end_ts",
                        (*strikes, T - 3600)).fetchall()
    tmp = {}
    for tk, e, b, a in rows:
        d = tmp.setdefault(tk, ([], [], []))
        d[0].append(e)
        d[1].append(np.nan if b is None else b)
        d[2].append(np.nan if a is None else a)
    for tk, (e, b, a) in tmp.items():
        series[tk] = (strikes[tk], e, b, a)
    return series, results


def load_series_hourly(conn, event, T):
    """The hourly table (#37 totals.sqlite) candles with end_ts <= T-3600 for one event."""
    mk = conn.execute("SELECT ticker, floor_strike FROM markets WHERE event_ticker=?", (event,)).fetchall()
    strikes = dict(mk)
    series = {}
    if not strikes:
        return series
    q = ",".join("?" * len(strikes))
    rows = conn.execute(f"SELECT ticker, end_ts, bid, ask FROM candles WHERE ticker IN ({q}) AND end_ts<=? ORDER BY ticker, end_ts",
                        (*strikes, T - 3600)).fetchall()
    tmp = {}
    for tk, e, b, a in rows:
        d = tmp.setdefault(tk, ([], [], []))
        d[0].append(e)
        d[1].append(np.nan if b is None else b)
        d[2].append(np.nan if a is None else a)
    for tk, (e, b, a) in tmp.items():
        series[tk] = (strikes[tk], e, b, a)
    return series


def project_games(universe):
    """F56 and F42c projections for the universe (T, tip, away, home from #42's table).  Box scores before the ET game
    date only (the world classes gate by decision date).  Returns DataFrame event, proj_F56, proj_F42c plus the worlds' notes."""
    fa, rc, gl, gr = load_fa()
    fz = json.loads((FA_DIR / "frozen.json").read_text())
    gdf = pd.DataFrame({"game_id": universe.hoopr_id.values, "date": pd.to_datetime(universe.date).values,
                        "T": universe["T"].values, "tip": universe.tip.values, "away": universe.away.values,
                        "home": universe.home.values, "season": "2025-26"})
    spec56 = tuple(fz["team"]["spec"])
    assert gl.flat_from_spec(spec56) == fz["team"]["flat"] == 7791
    assert fz["primary"] == "team_raw" and fz["forms"]["team_raw"]["shift"] == 0.0
    w56 = fa.build_world("2025-26")
    p56 = gl.project_one(w56.season, gdf, w56.consts, spec56) + fz["forms"]["team_raw"]["shift"]
    w42 = gr.load_world(None)
    c42 = gr.fit_world_constants(w42)
    win = json.loads((GRID_DIR / "winner_frozen.json").read_text())
    p42 = gl.project_one(w42.season, gdf, c42, tuple(win["spec"])) + LEAN
    return pd.DataFrame({"event": universe.event.values, "proj_F56": np.asarray(p56, float), "proj_F42c": np.asarray(p42, float)})


def f56_reproduction(universe_all=None):
    """F56 from OUR path (#42 game table -> gl.project_one) vs #56's own 2025-26 projections (run_confirm.season_inputs +
    form_projection, hoopR games_frame, T = tip).  Returns dict with max abs diff over every common 2025-26 regular game."""
    fa, rc, gl, gr = load_fa()
    fz = json.loads((FA_DIR / "frozen.json").read_text())
    sha_ok = (fa.sha256_file(FA_DIR / "frozen.json") == (FA_DIR / "frozen.sha256").read_text().split()[0].strip().lower())
    inp = rc.season_inputs("2025-26", closing=pd.Series(dtype=float))
    ref = rc.form_projection(fz, "team_raw", "2025-26", inp, None, {})
    ref = pd.Series(np.asarray(ref, float), index=inp["games"].game_id.values)
    g = games_42()
    gdf = pd.DataFrame({"game_id": g.hoopr_id.astype(int).values, "date": pd.to_datetime(g.hoopr_date).values, "T": g["T"].values,
                        "tip": g.tip.values, "away": g.away.values, "home": g.home.values, "season": "2025-26"})
    spec56 = tuple(fz["team"]["spec"])
    shift = fz["forms"]["team_raw"]["shift"]
    ids = gdf.game_id.values

    def run(frame):
        v = gl.project_one(inp["world"].season, frame, inp["world"].consts, spec56) + shift
        return pd.Series(np.asarray(v, float), index=ids)

    # (a) #56's own inputs: T := hoopR tip.  This must reproduce #56 to 0.0 on every game.
    at_tip = run(gdf.assign(T=gdf.tip))
    # (b) as used here: #42's T (the market's scheduled start; differs from the hoopR tip only for postponed games)
    as_used = run(gdf)
    common = ref.index.intersection(at_tip.index)
    d_tip = (ref.loc[common] - at_tip.loc[common]).abs()
    d_used = (ref.loc[common] - as_used.loc[common]).abs()
    moved = gdf[gdf["T"] != gdf.tip]
    return {"n_ref_games": int(len(ref)), "n_our_games": int(len(as_used)), "n_common": int(len(common)),
            "max_abs_diff": float(d_tip.max()),
            "max_abs_diff_as_used_with_42_T": float(d_used.max()),
            "n_games_where_42_T_differs_from_hoopr_tip": int(len(moved)),
            "games_where_42_T_differs": [{"hoopr_id": int(i), "T_42": int(t), "tip": int(tp), "diff_as_used": float(d_used.get(i, np.nan))}
                                         for i, t, tp in zip(moved.game_id, moved["T"], moved.tip)],
            "as_used_nonzero_diff_only_on_moved_games": bool(set(d_used[d_used > 0].index) <= set(moved.game_id)),
            "n_nonfinite_ours": int((~np.isfinite(as_used.loc[common])).sum()),
            "frozen_json_sha256_matches": bool(sha_ok), "flat": int(gl.flat_from_spec(spec56))}


def build_records(first, last, allow_unseen=False, limit=None, exclude=()):
    """Universe + projections + per (game, t_e) records for ET dates [first, last].  Returns (records DataFrame, universe, info)."""
    u, uinfo = load_universe(first, last, allow_unseen)
    if limit:
        u = u.iloc[:limit].reset_index(drop=True)
    if len(exclude):
        u = u[~u.event.isin(set(exclude))].reset_index(drop=True)
    assert u.date.between(first, last).all()
    proj = project_games(u)
    conn = connect_ro(INJ / "candles.sqlite")
    rows = []
    for r in u.itertuples():
        series, results = load_series_1m(conn, r.event, int(r.T))
        for rec in game_records(r.event, int(r.T), series, results):
            rec["date"] = r.date
            rows.append(rec)
    conn.close()
    rec = pd.DataFrame(rows).merge(proj, on="event", how="left")
    rec = rec.merge(u[["event", "hoopr_id", "median_T1h"]], on="event", how="left")
    return rec, u, uinfo


# ------------------------------------------------------------------ hourly cross-check
def hourly_check(universe, te_h=4):
    """L_e at T-4h from the 1-minute table vs the hourly table (same rules: last candle <= t_e, <= 30 min old, same crossing)."""
    c1 = connect_ro(INJ / "candles.sqlite")
    c2 = connect_ro(PACE_DIR / "totals.sqlite")
    rows = []
    for r in universe.itertuples():
        T = int(r.T)
        te = T - te_h * 3600
        s1, _ = load_series_1m(c1, r.event, T)
        s2 = load_series_hourly(c2, r.event, T)
        l1 = line_from(quotes_at(s1, te))
        l2 = line_from(quotes_at(s2, te))
        rows.append({"event": r.event, "L_1m": l1, "L_hourly": l2})
    c1.close()
    c2.close()
    return pd.DataFrame(rows)


def hourly_summary(df, gap=HOURLY_EXCLUDE_GAP):
    both = df.dropna(subset=["L_1m", "L_hourly"]).copy()
    both["absdiff"] = (both.L_1m - both.L_hourly).abs()
    bad = both[both.absdiff > gap]
    return {"games": int(len(df)), "both_defined": int(len(both)),
            "only_1m": int((df.L_1m.notna() & df.L_hourly.isna()).sum()),
            "only_hourly": int((df.L_1m.isna() & df.L_hourly.notna()).sum()),
            "neither": int((df.L_1m.isna() & df.L_hourly.isna()).sum()),
            "median_abs_diff": float(both.absdiff.median()) if len(both) else None,
            "mean_abs_diff": float(both.absdiff.mean()) if len(both) else None,
            "share_within_0.5": float((both.absdiff <= 0.5).mean()) if len(both) else None,
            "n_gt_1.5": int(len(bad)),
            "excluded_games": [{"event": r.event, "L_1m": float(r.L_1m), "L_hourly": float(r.L_hourly)} for r in bad.itertuples()]}, \
        sorted(bad.event)


# ------------------------------------------------------------------ versions, selection, luck, bootstrap
def versions():
    return [(f, h, g) for f in FORMS for h in TE_HOURS for g in GAPS]


def bet_arrays(rec, form, h, g):
    """Bets of one version: DataFrame with event, side, net, win, paid (formula side at the main strike)."""
    r = rec[(rec.te_h == h) & rec.skip.isna()]
    proj = r[f"proj_{form}"].values.astype(float)
    L = r.L_e.values.astype(float)
    m = np.isfinite(proj) & np.isfinite(L) & (np.abs(proj - L) >= g - 1e-12)
    r = r[m]
    over = (r[f"proj_{form}"].values > r.L_e.values)
    net = np.where(over, r.net_over.values, r.net_under.values)
    paid = np.where(over, r.paid_over.values, r.paid_under.values)
    win = np.where(over, r.result.values == "yes", r.result.values == "no").astype(float)
    return pd.DataFrame({"event": r.event.values, "side": np.where(over, "over", "under"), "net": net.astype(float),
                         "win": win, "paid": paid.astype(float)})


def lb_of(x):
    x = np.asarray(x, float)
    n = len(x)
    if n < 2:
        return float("nan")
    return float(x.mean() - 1.96 * x.std(ddof=1) / math.sqrt(n))


def version_row(rec, form, h, g):
    b = bet_arrays(rec, form, h, g)
    n = len(b)
    return {"form": form, "te_h": h, "gap": g, "n": n,
            "win_rate": float(b.win.mean()) if n else None, "avg_paid": float(b.paid.mean()) if n else None,
            "mean_net": float(b.net.mean()) if n else None, "sd": float(b.net.std(ddof=1)) if n > 1 else None,
            "lb": lb_of(b.net.values) if n > 1 else None,
            "n_over": int((b.side == "over").sum()), "n_under": int((b.side == "under").sum())}


def all_version_rows(rec):
    return [version_row(rec, f, h, g) for f, h, g in versions()]


def select(rows, min_n=MIN_N):
    """Highest LB among n >= 60 (ties -> earlier row in versions() order)."""
    el = [r for r in rows if r["n"] >= min_n and r["lb"] is not None and np.isfinite(r["lb"])]
    best = None
    for r in el:
        if best is None or r["lb"] > best["lb"] + 1e-12:
            best = r
    return best


def luck_test(rec, reps=REPS, seed=SEED, min_n=MIN_N):
    """Shared random sides per (game, t_e) (a 50/50 coin, same for every version in a rep); the 18-version selection is
    re-run in each rep.  FAIL if more than 5% of reps reach the real best LB."""
    real = select(all_version_rows(rec), min_n)
    real_lb = real["lb"] if real else float("-inf")
    ok = rec[rec.skip.isna()].reset_index(drop=True)
    idx = {k: i for i, k in enumerate(zip(ok.event, ok.te_h))}
    L = ok.L_e.values.astype(float)
    masks = {}
    for f, h, g in versions():
        proj = ok[f"proj_{f}"].values.astype(float)
        masks[(f, h, g)] = (ok.te_h.values == h) & np.isfinite(proj) & np.isfinite(L) & (np.abs(proj - L) >= g - 1e-12)
    rng = np.random.default_rng(seed)
    nov, nun = ok.net_over.values.astype(float), ok.net_under.values.astype(float)
    best = np.full(reps, -np.inf)
    for i in range(reps):
        over = rng.random(len(ok)) < 0.5
        net = np.where(over, nov, nun)
        for v, m in masks.items():
            n = int(m.sum())
            if n < min_n:
                continue
            x = net[m]
            lb = x.mean() - 1.96 * x.std(ddof=1) / math.sqrt(n)
            if lb > best[i]:
                best[i] = lb
    fin = np.isfinite(best)
    share = float((best >= real_lb - 1e-12).mean()) if real else 1.0
    return {"real_best_lb": real_lb, "real_selected": ({k: real[k] for k in ("form", "te_h", "gap")} if real else None),
            "reps": reps, "seed": seed, "share_reps_ge_real": share,
            "rep_best_lb_mean": float(best[fin].mean()) if fin.any() else None,
            "rep_best_lb_p95": float(np.quantile(best[fin], 0.95)) if fin.any() else None,
            "n_reps_no_eligible": int((~fin).sum()),
            "verdict": "PASS" if (real is not None and share <= 0.05) else "FAIL"}


def bootstrap_lb(events, nets, draws=N_BOOT, seed=SEED, chunk=500):
    """Game-cluster bootstrap of the mean net per contract; returns (mean, lb_2.5, ub_97.5)."""
    nets = np.asarray(nets, float)
    if len(nets) == 0:
        return None, None, None
    codes, _ = pd.factorize(np.asarray(events))
    G = codes.max() + 1
    s = np.bincount(codes, weights=nets, minlength=G)
    c = np.bincount(codes, minlength=G).astype(float)
    rng = np.random.default_rng(seed)
    means = np.empty(draws)
    for lo in range(0, draws, chunk):
        k = min(chunk, draws - lo)
        ix = rng.integers(0, G, size=(k, G))
        means[lo:lo + k] = s[ix].sum(1) / c[ix].sum(1)
    return float(nets.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def coin_flip(rec, form, h, g, real_mean, reps=REPS, seed=SEED):
    """500 copies: random side at the SAME strikes/candles/games as the selected version's bets.  Share of copies whose
    mean net >= the real mean."""
    r = rec[(rec.te_h == h) & rec.skip.isna()]
    proj = r[f"proj_{form}"].values.astype(float)
    L = r.L_e.values.astype(float)
    m = np.isfinite(proj) & np.isfinite(L) & (np.abs(proj - L) >= g - 1e-12)
    r = r[m]
    if len(r) == 0:
        return None
    nov, nun = r.net_over.values.astype(float), r.net_under.values.astype(float)
    rng = np.random.default_rng(seed)
    means = np.array([np.where(rng.random(len(r)) < 0.5, nov, nun).mean() for _ in range(reps)])
    return {"reps": reps, "share_as_good": float((means >= real_mean - 1e-12).mean()), "copy_mean_avg": float(means.mean())}


# ------------------------------------------------------------------ reported regardless
def mae_block(rec, finals, n_boot=N_BOOT):
    """Per t_e: MAE of L_e vs the hoopR final beside the T-1h median (#42) and both formulas, on the common games."""
    fa = load_fa()[0]
    r = rec if "final" in rec.columns else rec.merge(finals.rename("final"), left_on="hoopr_id", right_index=True, how="left")
    out = {}
    for h in TE_HOURS:
        x = r[r.te_h == h]
        cols = ["L_e", "median_T1h", "proj_F56", "proj_F42c", "final"]
        c = x.dropna(subset=cols)
        blk = {"n_common": int(len(c)), "n_L_e_only": int(x.dropna(subset=["L_e", "final"]).shape[0])}
        if len(c) >= 2:
            act = c.final.values.astype(float)
            for nm, col in (("L_e", "L_e"), ("T-1h_median", "median_T1h"), ("F56", "proj_F56"), ("F42c", "proj_F42c")):
                blk[nm] = fa.mae_rmse_bias(c[col].values.astype(float), act)
            blk["paired_mae_diff"] = {}
            for a, b in (("L_e", "T-1h_median"), ("F56", "L_e"), ("F42c", "L_e"), ("F56", "T-1h_median"), ("F42c", "T-1h_median")):
                ca = {"L_e": "L_e", "T-1h_median": "median_T1h", "F56": "proj_F56", "F42c": "proj_F42c"}
                d, lo, hi = fa.paired_boot_mae_diff(c[ca[a]].values.astype(float), c[ca[b]].values.astype(float), act, n_boot=n_boot)
                blk["paired_mae_diff"][f"MAE({a}) - MAE({b})"] = {"diff": d, "ci95": [lo, hi]}
        ae = x.dropna(subset=["L_e", "final"])
        if len(ae):
            blk["L_e_all_available"] = fa.mae_rmse_bias(ae.L_e.values.astype(float), ae.final.values.astype(float))
        out[f"T-{h}h"] = blk
    return out


def wilson(w, n, z=1.96):
    if n == 0:
        return [float("nan"), float("nan")]
    p = w / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    hw = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [c - hw, c + hw]


def drift_block(rec, alt_col=None):
    """Drift = L_(T-1h) - L_e (T-1h line from the same 1-minute table; alt_col = #42's hourly T-1h median).  For each form and
    gap: among games with |proj - L_e| >= gap and drift != 0, the share where the formula side (sign of proj - L_e) matches the drift sign."""
    col = alt_col or "L_T1h_1m"
    out = {}
    for h in TE_HOURS:
        x = rec[(rec.te_h == h)].dropna(subset=["L_e", col]).copy()
        x["drift"] = x[col] - x.L_e
        d = x.drift.values.astype(float)
        blk = {"n": int(len(x)), "mean_drift": float(d.mean()) if len(d) else None, "sd_drift": float(d.std(ddof=1)) if len(d) > 1 else None,
               "median_abs_drift": float(np.median(np.abs(d))) if len(d) else None,
               "share_abs_ge_1": float((np.abs(d) >= 1).mean()) if len(d) else None, "forms": {}}
        for f in FORMS:
            blk["forms"][f] = {}
            for g in (0,) + GAPS:
                y = x.dropna(subset=[f"proj_{f}"])
                gap = y[f"proj_{f}"] - y.L_e
                y = y[(gap.abs() >= max(g, 1e-9)) & (y.drift != 0)]
                s = np.sign(y[f"proj_{f}"] - y.L_e)
                match = (np.sign(y.drift) == s)
                n = int(len(y))
                w = int(match.sum())
                blk["forms"][f][f"gap>={g}"] = {"n": n, "match": w, "share": (w / n if n else None), "wilson95": wilson(w, n),
                                                "mean_drift_in_formula_direction": float((y.drift * s).mean()) if n else None}
        out[f"T-{h}h"] = blk
    return out


def availability_block(rec):
    out = {}
    for h in TE_HOURS:
        x = rec[rec.te_h == h]
        n = len(x)
        ms = x.dropna(subset=["strike"])
        sp = (ms.yes_ask - ms.yes_bid)
        out[f"T-{h}h"] = {"games": int(n), "with_L_e": int(x.L_e.notna().sum()), "share_with_L_e": float(x.L_e.notna().mean()) if n else None,
                          "with_main_strike": int(len(ms)), "share_with_main_strike": float(len(ms) / n) if n else None,
                          "skip_counts": {str(k): int(v) for k, v in x.skip.fillna("ok").value_counts().items()},
                          "median_quoted_strikes": float(x.n_quoted.median()) if n else None,
                          "main_strike_spread": ({"mean": float(sp.mean()), "median": float(sp.median()),
                                                  "share_le_0.02": float((sp <= 0.02 + 1e-9).mean()),
                                                  "share_le_0.05": float((sp <= 0.05 + 1e-9).mean()),
                                                  "max": float(sp.max())} if len(ms) else None),
                          "main_strike_quote_age_s": ({"median": float(ms.age_s.median()), "p90": float(ms.age_s.quantile(0.9)),
                                                       "max": float(ms.age_s.max())} if len(ms) else None)}
    return out


# ------------------------------------------------------------------ finals, three sources
def finals_hoopr(ids):
    fa, rc, gl, gr = load_fa()
    h = gr.prep.hoopr_games(2026).set_index("game_id")["total"]
    return h.reindex(list(ids))


def finals_kalshi(events):
    """End-of-game total per event = max expiration_value over NO-settled strikes (totals.sqlite markets).  Only the events given."""
    conn = connect_ro(PACE_DIR / "totals.sqlite")
    out = {}
    for ev in events:
        rows = conn.execute("SELECT expiration_value FROM markets WHERE event_ticker=? AND result='no'", (ev,)).fetchall()
        vals = []
        for (x,) in rows:
            try:
                vals.append(float(x))
            except (TypeError, ValueError):
                pass
        out[ev] = max(vals) if vals else np.nan
    conn.close()
    return pd.Series(out)


def bracket_consistency(events, finals_by_event):
    """Second Kalshi view of the final: every strike's settlement (candles.sqlite mkts.result: yes iff final > strike) must agree
    with the hoopR final.  Returns counts and the list of games with any contradicting strike ('scalar' strikes are ignored)."""
    conn = connect_ro(INJ / "candles.sqlite")
    bad, n_ok, n_nores = [], 0, 0
    for ev in events:
        fin = finals_by_event.get(ev)
        rows = conn.execute("SELECT strike, result FROM mkts WHERE event=? AND result IN ('yes','no')", (ev,)).fetchall()
        if fin is None or not np.isfinite(fin) or not rows:
            n_nores += 1
            continue
        wrong = [(s, r) for s, r in rows if (fin > s) != (r == "yes")]
        if wrong:
            bad.append({"event": ev, "hoopr_final": float(fin), "contradicting_strikes": wrong})
        else:
            n_ok += 1
    conn.close()
    return {"games_consistent": n_ok, "games_inconsistent": len(bad), "games_not_checkable": n_nores, "inconsistent_list": bad}


def odds_file_status():
    p = ROOT / "data" / "raw" / "nba_odds_samples" / "closing_lines_odds_api.parquet"
    df = pd.read_parquet(p)
    score_cols = [c for c in df.columns if any(k in c.lower() for k in ("score", "final", "result", "total_points", "pts"))]
    return {"path": str(p.relative_to(ROOT)), "columns": list(df.columns), "rows": int(len(df)), "score_like_columns": score_cols,
            "has_scores": bool(score_cols)}


# ------------------------------------------------------------------ audit hashes
AUDIT_FILES = (
    "el_lib.py", "checks.py", "run_discovery.py", "run_unseen.py", "results_discovery.json",
    "../nba_injury_speed/common.py", "../nba_ingame_overreaction/common.py",
    "../nba_formula_accuracy/frozen.json", "../nba_formula_accuracy/frozen.sha256",
    "../nba_totals_grid/winner_frozen.json",
    "../nba_formula_accuracy/fa_lib.py", "../nba_formula_accuracy/run_confirm.py",
    "../nba_totals_grid/grid_lib.py", "../nba_totals_grid/grid_bets.py", "../nba_totals_grid/grid_run.py",
    "../nba_totals_pace/lib.py", "../nba_totals_pace/prep.py", "../nba_totals_pace/run.py",
    "../nba_totals_v2/ratings2.py", "../nba_totals_v2/run.py", "../nba_totals_tourney/tourney.py",
)


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit_hashes(base=OUT, files=AUDIT_FILES):
    return {"sha256": {f: sha256_file(Path(base) / f) for f in files}}


def check_audit_ok(d=OUT, base=OUT, files=AUDIT_FILES):
    """AUDIT_OK must be JSON {"sha256": {relative_path: hash}} covering every file in `files`; any problem refuses."""
    try:
        got = json.loads((Path(d) / "AUDIT_OK").read_text())["sha256"]
        assert isinstance(got, dict)
    except Exception:  # noqa: BLE001
        raise SystemExit("REFUSED: AUDIT_OK is not JSON with a 'sha256' map.")
    for f in files:
        if f not in got:
            raise SystemExit(f"REFUSED: AUDIT_OK does not cover {f}.")
        try:
            h = sha256_file(Path(base) / f)
        except OSError:
            raise SystemExit(f"REFUSED: {f} cannot be read.")
        if h != got[f]:
            raise SystemExit(f"REFUSED: {f} changed since the audit (hash mismatch).")


def unseen_gates(d=OUT, base=OUT, files=AUDIT_FILES):
    """All refusal conditions of run_unseen.py, in order.  Returns the parsed discovery results when everything holds."""
    d = Path(d)
    if (d / "results_unseen.json").exists():
        raise SystemExit("REFUSED: results_unseen.json exists; the unseen run happens once.")
    if (d / "unseen_started.json").exists():
        raise SystemExit("REFUSED: unseen_started.json exists; the unseen run already started (once only).")
    if not (d / "AUDIT_OK").exists():
        raise SystemExit("REFUSED: AUDIT_OK not found (only the Opus audit creates it).")
    check_audit_ok(d, base, files)
    try:
        disc = json.loads((d / "results_discovery.json").read_text())
    except Exception:  # noqa: BLE001
        raise SystemExit("REFUSED: results_discovery.json missing or unreadable.")
    if disc.get("luck_test", {}).get("verdict") != "PASS" or not disc.get("selected"):
        raise SystemExit("REFUSED: the discovery luck test did not PASS (or no eligible version).")
    return disc
