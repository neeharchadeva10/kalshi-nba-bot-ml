"""Research log #61 pipeline: decision-time ladder, normal fit, band/tail bets, versions, luck test, bootstrap, calibration.
Pure functions are unit-tested in test_ladder_shape.py; data access is in build_events()/load_finals()."""
from __future__ import annotations

import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ls_common as C

NAN = float("nan")


# ------------------------------------------------------------------ events / dates
def select_events(first: str, last: str, events_csv: Path | None = None) -> pd.DataFrame:
    """Events whose ET game date is in [first, last] (inclusive). Dates are filtered BEFORE any result/final is read."""
    ev = pd.read_csv(events_csv or (C.INJ / "events_list.csv"))
    ev = ev[(ev.game_date_et >= first) & (ev.game_date_et <= last)]
    return ev.sort_values(["game_date_et", "event"]).reset_index(drop=True)


def decision_time(T: int) -> int:
    return int(T) - C.D_BEFORE_T_S


# ------------------------------------------------------------------ decision-time ladder
def _ok(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def ladder_from_rows(rows, strikes: dict, D: int) -> list[dict]:
    """rows: iterable of (ticker, end_ts, bid, ask) candles. Per ticker the LAST candle with end_ts <= D (candles ending after D
    are never used), no older than 30 min, ask - bid <= 0.15, bid <= ask. Returns [{ticker,K,bid,ask,mid,spread,age}] by strike."""
    last = {}
    for tk, ts, b, a in rows:
        if ts > D or tk not in strikes:
            continue                                  # look-ahead guard
        if tk not in last or ts > last[tk][0]:
            last[tk] = (ts, b, a)
    out = []
    for tk, (ts, b, a) in last.items():
        if not (_ok(b) and _ok(a)):
            continue
        b, a = float(b), float(a)
        age = D - ts
        assert age >= 0
        if age > C.MAX_AGE_S or a < b or a - b > C.LADDER_SPREAD + 1e-9:
            continue
        out.append({"ticker": tk, "K": float(strikes[tk]), "bid": b, "ask": a, "mid": (a + b) / 2, "spread": a - b, "age": age})
    out.sort(key=lambda r: r["K"])
    return out


def load_ladder(conn: sqlite3.Connection, strikes: dict, D: int) -> list[dict]:
    """One query for the event: candles with D-30min <= end_ts <= D only (nothing after D is ever read)."""
    tks = list(strikes)
    if not tks:
        return []
    q = "SELECT ticker, end_ts, bid, ask FROM candles WHERE ticker IN (%s) AND end_ts<=? AND end_ts>=?" % ",".join("?" * len(tks))
    rows = conn.execute(q, tks + [D, D - C.MAX_AGE_S]).fetchall()
    return ladder_from_rows(rows, strikes, D)


def market_median(ladder) -> float | None:
    return C.implied_total([(r["K"], r["bid"], r["ask"]) for r in ladder])


# ------------------------------------------------------------------ normal fit
def _phi_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _phi_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _sse(mu, sig, K, y):
    return sum((yi - (1.0 - _phi_cdf((k - mu) / sig))) ** 2 for k, yi in zip(K, y))


def _lm(mu, sig, K, y, iters=200):
    lam = 1e-2
    cur = _sse(mu, sig, K, y)
    for _ in range(iters):
        jtj = np.zeros((2, 2))
        jtr = np.zeros(2)
        for k, yi in zip(K, y):
            z = (k - mu) / sig
            f = 1.0 - _phi_cdf(z)
            p = _phi_pdf(z)
            j = np.array([p / sig, p * z / sig])       # d f / d (mu, sigma)
            r = yi - f
            jtj += np.outer(j, j)
            jtr += j * r
        improved = False
        for _ in range(12):
            a = jtj + lam * np.diag(np.diag(jtj) + 1e-12)
            try:
                step = np.linalg.solve(a, jtr)
            except np.linalg.LinAlgError:
                lam *= 10
                continue
            mu2, sig2 = mu + step[0], min(max(sig + step[1], 0.5), 200.0)
            new = _sse(mu2, sig2, K, y)
            if new < cur:
                done = abs(step[0]) < 1e-9 and abs(step[1]) < 1e-9
                mu, sig, cur = mu2, sig2, new
                lam = max(lam / 3, 1e-9)
                improved = True
                if done:
                    return mu, sig, cur
                break
            lam *= 10
        if not improved:
            break
    return mu, sig, cur


def fit_normal(points):
    """points: iterable of (strike, yes_mid). Least squares of 1 - Phi((K-mu)/sigma) to the mids of strikes with mid strictly in
    (0.02, 0.98); at least 4 such strikes else None. Multi-start Levenberg-Marquardt (sigma starts 6/18/40, mu start at the 0.5
    crossing or the median strike). Returns {'mu','sigma','sse','n'} or None."""
    pts = sorted((float(k), float(m)) for k, m in points if C.FIT_LO < float(m) < C.FIT_HI)
    if len(pts) < C.FIT_MIN_STRIKES:
        return None
    K = [p[0] for p in pts]
    y = [p[1] for p in pts]
    mu0 = float(np.median(K))
    for (k1, m1), (k2, m2) in zip(pts, pts[1:]):
        if m1 >= 0.5 >= m2 and m1 != m2:
            mu0 = k1 + (m1 - 0.5) / (m1 - m2) * (k2 - k1)
            break
    best = None
    for s0 in (6.0, 18.0, 40.0):
        mu, sig, sse = _lm(mu0, s0, K, y)
        if best is None or sse < best[2] - 1e-15:
            best = (mu, sig, sse)
    return {"mu": float(best[0]), "sigma": float(best[1]), "sse": float(best[2]), "n": len(pts)}


# ------------------------------------------------------------------ bets
def pick_candidate(ladder, M: float, band: str, tail: str):
    """The one ladder strike in the band nearest the band centre; ties -> the lower strike. Returns ladder row or None.
    high: lo <= K - M < hi ; low: lo <= M - K < hi."""
    lo, hi, centre = C.BANDS[band]
    best = None
    for r in ladder:
        d = (r["K"] - M) if tail == "high" else (M - r["K"])
        if lo <= d < hi:
            key = (abs(d - centre), r["K"])
            if best is None or key < best[0]:
                best = (key, r)
    return None if best is None else best[1]


def entry(row: dict, tail: str, direction: str):
    """(side, quoted ask of that side, entry price incl. +1c slippage) or None when entry >= 0.99.
    buy-tail: high -> YES at yes_ask ; low -> NO at 1 - yes_bid.  sell-tail: the opposite side."""
    side_yes = (tail == "high") == (direction == "buy")
    side = "yes" if side_yes else "no"
    ask = row["ask"] if side_yes else 1.0 - row["bid"]
    p = round(ask + C.SLIP, 4)
    if p >= C.SKIP_AT - 1e-9:
        return None
    return side, ask, p


def net_of(side: str, result: str, p: float) -> float:
    return (1.0 if result == side else 0.0) - p - C.fee_taker(p)


def make_candidates(game: str, date: str, ladder, M: float | None, results: dict) -> list[dict]:
    """One record per band x tail for a game (skip reason set when no bet is possible). results: {ticker: 'yes'/'no'}."""
    out = []
    for band in C.BANDS:
        for tail in C.TAILS:
            rec = {"game": game, "date": date, "M": M, "band": band, "tail": tail, "skip": None, "n_ladder": len(ladder)}
            if M is None:
                rec["skip"] = "no_M_crossing"
            else:
                r = pick_candidate(ladder, M, band, tail)
                if r is None:
                    rec["skip"] = "no_strike_in_band"
                elif r["spread"] > C.BET_SPREAD + 1e-9:
                    rec.update(strike=r["K"], ticker=r["ticker"], spread=r["spread"])
                    rec["skip"] = "spread_gt_0.10"
                else:
                    res = results.get(r["ticker"])
                    rec.update(strike=r["K"], ticker=r["ticker"], d=r["K"] - M, yes_bid=r["bid"], yes_ask=r["ask"],
                               spread=r["spread"], age=r["age"], result=res)
                    if res not in ("yes", "no"):
                        rec["skip"] = "no_result"
                    else:
                        for dname in C.DIRECTIONS:
                            e = entry(r, tail, dname)
                            rec[f"{dname}_net"] = NAN if e is None else net_of(e[0], res, e[2])
                            rec[f"{dname}_p"] = NAN if e is None else e[2]
                            rec[f"{dname}_side"] = None if e is None else e[0]
            for dname in C.DIRECTIONS:
                rec.setdefault(f"{dname}_net", NAN)
                rec.setdefault(f"{dname}_p", NAN)
                rec.setdefault(f"{dname}_side", None)
            out.append(rec)
    return out


def version_bets(cands, band, tail, direction):
    """[(game, net, paid)] for a version (entry-skipped bets are absent)."""
    out = []
    for r in cands:
        if r["skip"] or r["band"] != band or r["tail"] != tail:
            continue
        n = r[f"{direction}_net"]
        if not math.isnan(n):
            out.append((r["game"], n, r[f"{direction}_p"]))
    return out


def stats(nets):
    import statistics
    n = len(nets)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "lb": None, "win": None}
    return {"n": n, "mean": statistics.fmean(nets), "sd": statistics.stdev(nets) if n > 1 else NAN, "lb": C.lb_of(nets),
            "win": sum(x > 0 for x in nets) / n}


def all_version_stats(cands):
    rows = []
    for band, tail, d in C.grid():
        b = version_bets(cands, band, tail, d)
        s = stats([x[1] for x in b])
        n_cell = sum(1 for r in cands if not r["skip"] and r["band"] == band and r["tail"] == tail)
        rows.append({"band": band, "tail": tail, "dir": d, "n_cell_candidates": n_cell, "n_entry_skipped": n_cell - s["n"],
                     "avg_paid": float(np.mean([x[2] for x in b])) if b else None, **s})
    return rows


def select(rows):
    """Highest LB among versions with n >= 60 (ties -> first in grid order)."""
    el = [r for r in rows if r["n"] >= C.MIN_N and r["lb"] is not None and not math.isnan(r["lb"])]
    return max(el, key=lambda r: r["lb"]) if el else None


# ------------------------------------------------------------------ luck test / coin flip
def cell_arrays(cands):
    """{(band, tail): (buy_net array, sell_net array)} over non-skipped candidates (NaN where the entry was skipped),
    candidates sorted by (game) for determinism."""
    arr = {}
    for band in C.BANDS:
        for tail in C.TAILS:
            cs = sorted((r for r in cands if not r["skip"] and r["band"] == band and r["tail"] == tail), key=lambda r: r["game"])
            arr[(band, tail)] = (np.array([r["buy_net"] for r in cs], dtype=float), np.array([r["sell_net"] for r in cs], dtype=float))
    return arr


def _lb_arr(x):
    x = x[~np.isnan(x)]
    n = len(x)
    if n < C.MIN_N:
        return None
    return float(x.mean() - 1.96 * x.std(ddof=1) / math.sqrt(n))


def luck_test(cands, reps=C.LUCK_REPS, seed=C.SEED):
    """Random direction (a coin per game x band x tail candidate, same strike and quotes); the 8-version selection is re-run in
    each rep. share = fraction of reps whose best LB >= the real best LB (reps with no eligible version count as not as good)."""
    real = select(all_version_stats(cands))
    real_lb = real["lb"] if real else float("-inf")
    arr = cell_arrays(cands)
    rng = np.random.default_rng(seed)
    best = []
    for _ in range(reps):
        coins = {k: rng.random(len(v[0])) < 0.5 for k, v in arr.items()}    # True: "buy" version bets the buy side
        bl = float("-inf")
        for band, tail, d in C.grid():
            nb, ns = arr[(band, tail)]
            c = coins[(band, tail)]
            x = np.where(c, nb, ns) if d == "buy" else np.where(c, ns, nb)
            v = _lb_arr(x)
            if v is not None:
                bl = max(bl, v)
        best.append(bl)
    best = np.array(best)
    fin = np.isfinite(best)
    share = float((best >= real_lb).mean())
    return {"real_best_lb": real_lb, "reps": reps, "seed": seed, "share_reps_ge_real": share,
            "rep_best_lb_mean": float(best[fin].mean()) if fin.any() else None,
            "rep_best_lb_p95": float(np.quantile(best[fin], 0.95)) if fin.any() else None,
            "n_reps_no_eligible": int((~fin).sum()),
            "verdict": "PASS" if (real is not None and share <= 0.05) else ("NO_ELIGIBLE_VERSION" if real is None else "FAIL")}


def coin_flip(cands, band, tail, direction, real_mean, reps=C.COIN_REPS, seed=C.SEED):
    """Random direction at the same strikes of the selected cell, `reps` copies. Returns share of copies with mean net >= real."""
    nb, ns = cell_arrays(cands)[(band, tail)]
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(reps):
        c = rng.random(len(nb)) < 0.5
        x = np.where(c, nb, ns) if direction == "buy" else np.where(c, ns, nb)
        x = x[~np.isnan(x)]
        means.append(float(x.mean()) if len(x) else NAN)
    m = np.array(means)
    return {"share_ge_real": float((m >= real_mean).mean()) if real_mean is not None else None,
            "copies": reps, "copy_mean_avg": float(np.nanmean(m)) if np.isfinite(m).any() else None}


def bootstrap_lb(nets_by_game, draws=C.BOOT_DRAWS, seed=C.SEED):
    """95% bootstrap lower bound (2.5th percentile) of the mean net, resampling games. None when there are no bets."""
    gl = [np.array(v, dtype=float) for v in nets_by_game.values() if len(v)]
    if not gl:
        return None
    rng = np.random.default_rng(seed)
    boots = [np.concatenate([gl[i] for i in rng.integers(0, len(gl), len(gl))]).mean() for _ in range(draws)]
    return float(np.quantile(boots, 0.025))


# ------------------------------------------------------------------ Part A
def bucket_of(d: float):
    for lo, hi in C.BUCKETS:
        if lo <= d < hi:
            return (lo, hi)
    return None


def calibration_table(game_ladders):
    """game_ladders: [{'game', 'M', 'ladder': [...], 'results': {ticker: 'yes'/'no'}}] (games with M only).
    Per distance bucket d = K - M: n strikes, n games, mean YES mid, realised YES rate."""
    acc = {b: {"mids": [], "yes": [], "games": set()} for b in C.BUCKETS}
    for g in game_ladders:
        if g["M"] is None:
            continue
        for r in g["ladder"]:
            res = g["results"].get(r["ticker"])
            b = bucket_of(r["K"] - g["M"])
            if b is None or res not in ("yes", "no"):
                continue
            acc[b]["mids"].append(r["mid"])
            acc[b]["yes"].append(1.0 if res == "yes" else 0.0)
            acc[b]["games"].add(g["game"])
    rows = []
    for (lo, hi), a in acc.items():
        n = len(a["mids"])
        rows.append({"bucket": f"{lo}..{hi}", "n_strikes": n, "n_games": len(a["games"]),
                     "mean_mid": float(np.mean(a["mids"])) if n else None,
                     "yes_rate": float(np.mean(a["yes"])) if n else None,
                     "yes_minus_mid": float(np.mean(a["yes"]) - np.mean(a["mids"])) if n else None})
    return rows


def sigma_z_summary(fits):
    """fits: [{'game','mu','sigma','final'(or None)}]. Distribution of sigma_mkt and the SD of z = (final - mu)/sigma."""
    sig = np.array([f["sigma"] for f in fits], dtype=float)
    z = np.array([(f["final"] - f["mu"]) / f["sigma"] for f in fits if f.get("final") is not None], dtype=float)
    out = {"n_fits": int(len(sig))}
    if len(sig):
        out["sigma"] = {"median": float(np.median(sig)), "mean": float(sig.mean()),
                        **{f"p{q}": float(np.quantile(sig, q / 100)) for q in (10, 25, 75, 90)}}
    out["n_z"] = int(len(z))
    if len(z) > 1:
        sd = float(z.std(ddof=1))
        out["z"] = {"sd": sd, "mean": float(z.mean()), "approx_se_sd": sd / math.sqrt(2 * (len(z) - 1)),
                    "reading": "SD>1: market too narrow; SD<1: too wide"}
    return out


# ------------------------------------------------------------------ data access
def load_finals(gids) -> dict:
    """hoopR final total (home + away of the last play by game_play_number) per game_id, regular season."""
    gids = sorted({int(g) for g in gids})
    if not gids:
        return {}
    df = pd.read_parquet(C.PBP, columns=["game_id", "game_play_number", "home_score", "away_score", "season_type"],
                         filters=[("game_id", "in", gids)])
    df = df[df.season_type == 2].sort_values(["game_id", "game_play_number"])
    last = df.groupby("game_id").tail(1)
    return {int(r.game_id): int(r.home_score) + int(r.away_score) for r in last.itertuples()}


def event_markets(conn, event):
    rows = conn.execute("SELECT ticker, strike, result FROM mkts WHERE event=?", (event,)).fetchall()
    return {t: s for t, s, _ in rows}, {t: r for t, _, r in rows}


def hourly_compare(minute_conn, hourly_conn, event, strikes: dict, T: int):
    """Per strike: the hourly candle (latest ending <= D) vs the 1-minute quote as of that SAME hourly end_ts.
    Returns list of dicts (ticker, K, t_hourly, mid_hourly, mid_minute, diff, age_minute)."""
    D = decision_time(T)
    out = []
    for tk, K in strikes.items():
        h = hourly_conn.execute("SELECT end_ts, bid, ask FROM candles WHERE ticker=? AND end_ts<=? AND end_ts>=? ORDER BY end_ts DESC LIMIT 1",
                                (tk, D, D - C.MAX_AGE_S)).fetchone()
        if not h or not (_ok(h[1]) and _ok(h[2])):
            continue
        m = minute_conn.execute("SELECT end_ts, bid, ask FROM candles WHERE ticker=? AND end_ts<=? ORDER BY end_ts DESC LIMIT 1",
                                (tk, h[0])).fetchone()
        if not m or not (_ok(m[1]) and _ok(m[2])):
            continue
        mh, mm = (h[1] + h[2]) / 2, (m[1] + m[2]) / 2
        out.append({"event": event, "ticker": tk, "K": K, "t_hourly": h[0], "mid_hourly": mh, "mid_minute": mm,
                    "diff": mm - mh, "age_minute": h[0] - m[0]})
    return out


def open_ro(p: Path):
    return sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)


def quote_check_rows(events: pd.DataFrame):
    """Quote-only comparison (no outcomes read) for the given events -> list of per-strike dicts."""
    mc, hc = open_ro(C.INJ / "candles.sqlite"), open_ro(C.HOURLY / "totals.sqlite")
    rows = []
    try:
        for r in events.itertuples():
            strikes, _ = event_markets(mc, r.event)
            rows += hourly_compare(mc, hc, r.event, strikes, int(r.T))
    finally:
        mc.close()
        hc.close()
    return rows


def excluded_games(rows, tol=C.QUOTE_TOL):
    return sorted({r["event"] for r in rows if abs(r["diff"]) > tol + 1e-9})


def build_events(events: pd.DataFrame, exclude=()):
    """Per-event ladder, M, fit, candidates. Streams per event (one sqlite query per event). Reads settlement results.
    Returns (games, cands, skips): games = [{'game','date','gid','ladder','M','fit','results'}]."""
    mc = open_ro(C.INJ / "candles.sqlite")
    games, cands, skips = [], [], {}
    exclude = set(exclude)
    try:
        for r in events.itertuples():
            if r.event in exclude:
                skips.setdefault("quote_check_excluded", []).append(r.event)
                continue
            strikes, results = event_markets(mc, r.event)
            ladder = load_ladder(mc, strikes, decision_time(int(r.T)))
            if not ladder:
                skips.setdefault("empty_ladder", []).append(r.event)
                continue
            M = market_median(ladder)
            fit = fit_normal([(x["K"], x["mid"]) for x in ladder])
            if M is None:
                skips.setdefault("no_M_crossing", []).append(r.event)
            if fit is None:
                skips.setdefault("no_fit", []).append(r.event)
            games.append({"game": r.event, "date": r.game_date_et, "gid": None if pd.isna(r.hoopr_gid) else int(r.hoopr_gid),
                          "ladder": ladder, "M": M, "fit": fit, "results": results})
            cands += make_candidates(r.event, r.game_date_et, ladder, M, results)
    finally:
        mc.close()
    return games, cands, skips


def part_a(games, finals):
    fits = []
    for g in games:
        if g["fit"] is None:
            continue
        fin = finals.get(g["gid"]) if g["gid"] is not None else None
        fits.append({"game": g["game"], "mu": g["fit"]["mu"], "sigma": g["fit"]["sigma"], "M": g["M"], "n_fit": g["fit"]["n"],
                     "final": fin})
    return fits, sigma_z_summary(fits), calibration_table([g for g in games if g["M"] is not None])


def candidates_frame(cands) -> pd.DataFrame:
    cols = ["game", "date", "M", "band", "tail", "skip", "n_ladder", "strike", "d", "yes_bid", "yes_ask", "spread", "age", "result",
            "buy_side", "buy_p", "buy_net", "sell_side", "sell_p", "sell_net"]
    return pd.DataFrame([{k: r.get(k) for k in cols} for r in cands])
