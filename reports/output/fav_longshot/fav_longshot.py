"""Research log #47: favourite-longshot bias in Kalshi NBA/NFL moneylines (pregame).
Implements reports/output/fav_longshot/PREREGISTRATION.md.  Read-only: sqlite files opened with mode=ro, no network.

Prices are integers in e4 (1e-4 dollars) everywhere, so the fee rounding is exact.
"""
import bisect
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
SEED = 20261022
SLIP_E4 = 100                      # +1c on the ask
ONE = 10000
H = 3600
D_LABELS = {"T-24h": 24 * H, "T-3h": 3 * H, "T-1h": 1 * H}
MAX_AGE = {"T-24h": 6 * H, "T-3h": 2 * H, "T-1h": 2 * H}     # older candle = stale, skipped and counted
BANDS = {  # side -> list of (lo, hi) in cents, half-open [lo, hi)
    "FAV": [(55, 70), (70, 85), (85, 95)],
    "LONG-NO": [(5, 15), (15, 30)],
}
SPORTS = ["NBA", "NFL"]
NBA_DISC = ("2025-10-21", "2026-01-22")      # inclusive, ET date of T
NBA_UNSEEN = ("2026-01-23", "2026-06-30")
NBA_SEASON_START = int(datetime(2025, 10, 21, tzinfo=timezone.utc).timestamp())
ET = ZoneInfo("America/New_York")


def all_versions():
    return [(s, d, side, lo, hi) for s in SPORTS for d in D_LABELS for side, bs in BANDS.items() for lo, hi in bs]


def vname(v):
    return f"{v[0]}|{v[1]}|{v[2]}|{v[3]}-{v[4]}"


# ---------------------------------------------------------------------------------------------------- fees / net
def fee_e4(p_e4):
    """Kalshi taker fee for one contract at price p (e4): ceil(0.07*P*(1-P) to $0.0001). Exact integer arithmetic."""
    return -(-(7 * p_e4 * (ONE - p_e4)) // 1000000)


def entry_net_e4(entry_e4, win):
    """Net (e4) of a 1-contract taker buy at quoted entry price entry_e4: pays entry+1c slippage + fee, wins 1.0."""
    p = entry_e4 + SLIP_E4
    return (ONE if win else 0) - p - fee_e4(p)


# ---------------------------------------------------------------------------------------------------- candle pick
def pick_candle(rows, D, max_age):
    """rows: iterable of (ts, bid_e4, ask_e4).  Last candle with ts <= D (never later: no look-ahead).
    Returns (row, None) or (None, reason) where reason in {'no_candle','stale'}."""
    best = None
    for r in rows:
        if r[0] <= D and (best is None or r[0] > best[0]):
            best = r
    if best is None:
        return None, "no_candle"
    if D - best[0] > max_age:
        return None, "stale"
    return best, None


# ---------------------------------------------------------------------------------------------------- loaders
def ro(name):
    c = sqlite3.connect(f"file:{(ROOT / 'data' / name).as_posix()}?mode=ro", uri=True)
    return c


def e4(x):
    return None if x is None else int(round(float(x) * ONE))


def nba_split(T):
    d = datetime.fromtimestamp(T, ET).strftime("%Y-%m-%d")
    if NBA_DISC[0] <= d <= NBA_DISC[1]:
        return "discovery"
    if NBA_UNSEEN[0] <= d <= NBA_UNSEEN[1]:
        return "unseen"
    return None


def load_games(sport, split, excl):
    """Games of one sport in one split ('discovery'|'unseen').  Each game: dict(gid, season, T, teams=[{code,win,key}]).
    Everything dropped is counted in excl (Counter)."""
    games = []
    if sport == "NBA":
        c = ro("nba_drift.sqlite")
        by_ev = defaultdict(list)
        for r in c.execute("SELECT ticker,event_ticker,yes_sub_title,result,occurrence_ts FROM markets"):
            by_ev[r[1]].append(r)
        for ev, ms in sorted(by_ev.items()):
            if len(ms) != 2:
                excl["NBA: event without exactly 2 markets"] += 1
                continue
            T = min(m[4] for m in ms) - 3 * H
            if T < NBA_SEASON_START:
                excl["NBA: preseason (T before 2025-10-21)"] += 1
                continue
            sp = nba_split(T)
            if sp != split:
                continue                      # other split: not looked at (no result inspection)
            if sorted(m[3] or "" for m in ms) != ["no", "yes"]:
                excl["NBA: no clean yes/no result (scalar/void)"] += 1
                continue
            games.append({"gid": ev, "season": "2025-26", "T": T,
                          "teams": [{"code": m[0].rsplit("-", 1)[1], "win": m[3] == "yes", "key": m[0]} for m in ms]})
        c.close()
    else:
        c = ro("nfl_spread.sqlite")
        by = defaultdict(list)
        for r in c.execute("SELECT mid,event_ticker,season,team,result,raw_json FROM sp_markets "
                           "WHERE series='KXNFLGAME' AND kind='win'"):
            by[r[1]].append(r)
        want = 2025 if split == "discovery" else 2026
        for ev, ms in sorted(by.items()):
            if ms[0][2] != want:
                continue
            if len(ms) != 2:
                excl["NFL: event without exactly 2 markets"] += 1
                continue
            if sorted(m[4] or "" for m in ms) != ["no", "yes"]:
                excl["NFL: no clean yes/no result (scalar/void/unsettled)"] += 1
                continue
            exps = [datetime.fromisoformat(json.loads(m[5])["expected_expiration_time"].replace("Z", "+00:00")).timestamp()
                    for m in ms]
            T = int(min(exps)) - 3 * H
            games.append({"gid": ev, "season": str(ms[0][2]), "T": T,
                          "teams": [{"code": m[3], "win": m[4] == "yes", "key": m[0]} for m in ms]})
        c.close()
    return games


def make_fetcher(sport):
    """Returns (fetch(key, D, max_age) -> rows, close).  fetch yields at most the last candle with ts <= D."""
    if sport == "NBA":
        c = ro("nba_drift.sqlite")
        q = "SELECT ts,bid,ask FROM candles WHERE ticker=? AND kind='hour' AND ts<=? ORDER BY ts DESC LIMIT 1"

        def fetch(key, D):
            return [(ts, e4(b), e4(a)) for ts, b, a in c.execute(q, (key, D))]
    else:
        c = ro("nfl_spread.sqlite")
        q = "SELECT ts,bid_e4,ask_e4 FROM sp_quotes WHERE mid=? AND ts<=? ORDER BY ts DESC LIMIT 1"

        def fetch(key, D):
            return [(ts, b, a) for ts, b, a in c.execute(q, (key, D))]
    return fetch, c.close


def build_quotes(sport, split, excl):
    """One record per (game, D) with both teams' valid decision-time candles:
    dict(sport, split, season, D, gid, T, teams=[(code, bid_e4|None, ask_e4|None, win)])."""
    games = load_games(sport, split, excl)
    excl[f"{sport}: games in split '{split}'"] = len(games)
    fetch, close = make_fetcher(sport)
    recs = []
    for g in games:
        for dl, off in D_LABELS.items():
            D = g["T"] - off
            tm, reason = [], None
            for t in g["teams"]:
                key = t["key"]            # NBA: ticker; NFL: sp_markets.mid
                row, why = pick_candle(fetch(key, D), D, MAX_AGE[dl])
                if row is None:
                    reason = why
                    break
                tm.append((t["code"], row[1], row[2], t["win"], row[0]))
            if reason:
                excl[f"{sport} {dl}: skipped, {reason} quote (older than {MAX_AGE[dl] // H}h or none)"] += 1
                continue
            recs.append({"sport": sport, "split": split, "season": g["season"], "D": dl, "gid": g["gid"], "T": g["T"],
                         "teams": [(a, b, c_, d) for a, b, c_, d, _ in tm]})
    close()
    return recs


# ---------------------------------------------------------------------------------------------------- contracts / bets
def contracts(rec):
    """All valid contracts of a record: (entry_e4, win, kind, code).  YES entry = ask; NO entry = 1 - YES bid.
    A YES contract needs 0 < ask < 1; a NO contract needs 0 < bid < 1 (so NO ask < 1)."""
    out = []
    for code, bid, ask, win in rec["teams"]:
        if ask is not None and 0 < ask < ONE - SLIP_E4 and (bid is None or bid <= ask):
            out.append((ask, win, "YES", code))
        if bid is not None and SLIP_E4 < bid < ONE and (ask is None or bid <= ask):
            out.append((ONE - bid, not win, "NO", code))
    return out


def select_bet(rec, side, lo, hi, excl=None):
    """Returns (entry_e4, win, code) or None.  One bet per game."""
    lo4, hi4 = lo * 100, hi * 100
    cands = []
    for code, bid, ask, win in rec["teams"]:
        if side == "FAV":
            if ask is not None and 0 < ask < ONE and (bid is None or bid <= ask) and lo4 <= ask < hi4:
                cands.append((ask, ask, win, code))                       # key = ask
        else:
            if bid is not None and 0 < bid < ONE and (ask is None or bid <= ask) and lo4 <= bid < hi4:
                cands.append((bid, ONE - bid, not win, code))              # key = yes bid; NO wins if team lost
    if not cands:
        return None
    if len(cands) == 2:
        k0, k1 = cands[0][0], cands[1][0]
        if k0 == k1:
            if excl is not None:
                excl[f"{rec['sport']} {rec['D']} {side} {lo}-{hi}: both teams in band with equal quote, skipped"] += 1
            return None
        if excl is not None:
            excl[f"{rec['sport']} {rec['D']} {side} {lo}-{hi}: both teams in band, took higher ask/lower bid"] += 1
        cands = [max(cands) if side == "FAV" else min(cands)]
    _, entry, win, code = cands[0]
    return entry, win, code


def bets_for_version(recs, v, excl=None):
    sport, dl, side, lo, hi = v
    rows = []
    for r in recs:
        if r["sport"] != sport or r["D"] != dl:
            continue
        b = select_bet(r, side, lo, hi, excl)
        if b:
            entry, win, code = b
            rows.append({"gid": r["gid"], "T": r["T"], "season": r["season"], "code": code, "entry": entry,
                         "win": win, "net": entry_net_e4(entry, win) / ONE,
                         "maker_net": ((ONE if win else 0) - entry) / ONE})   # info only: fill at quote, no fee/slip
    return rows


# ---------------------------------------------------------------------------------------------------- stats
def lb_stats(nets):
    n = len(nets)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "lb": None}
    a = np.asarray(nets, float)
    m = float(a.mean())
    sd = float(a.std(ddof=1)) if n > 1 else float("nan")
    return {"n": n, "mean": m, "sd": sd, "lb": m - 1.96 * sd / math.sqrt(n) if n > 1 else None}


def select_best(stat_by_version, min_n=60):
    """Highest LB among versions with n >= min_n.  Returns version tuple or None."""
    el = [(s["lb"], v) for v, s in stat_by_version.items() if s["n"] >= min_n and s["lb"] is not None]
    if not el:
        return None
    return max(el, key=lambda x: (x[0], x[1]))[1]


def version_table(recs, excl=None):
    bets, stats = {}, {}
    for v in all_versions():
        b = bets_for_version(recs, v, excl)
        bets[v] = b
        s = lb_stats([x["net"] for x in b])
        s["win_rate"] = float(np.mean([x["win"] for x in b])) if b else None
        s["mean_entry"] = float(np.mean([x["entry"] for x in b])) / ONE if b else None
        s["maker_mean_info_only"] = float(np.mean([x["maker_net"] for x in b])) if b else None
        stats[v] = s
    return bets, stats


# ---------------------------------------------------------------------------------------------------- luck test
class Pool:
    """Price-sorted pool of (entry_e4, net) for every valid contract (any team, YES or NO) of one sport and D."""

    def __init__(self, recs, sport, dl):
        rows = []
        for r in recs:
            if r["sport"] == sport and r["D"] == dl:
                for entry, win, _, _ in contracts(r):
                    rows.append((entry, entry_net_e4(entry, win) / ONE))
        rows.sort()
        self.entry = np.array([x[0] for x in rows], np.int64)
        self.net = np.array([x[1] for x in rows], float)

    def range_for(self, entry, tol_e4=200):
        lo = int(np.searchsorted(self.entry, entry - tol_e4, side="left"))
        hi = int(np.searchsorted(self.entry, entry + tol_e4, side="right"))
        return lo, hi


def luck_test(recs, bets, stats, reps=500, seed=SEED, min_n=60):
    """Price-matched random contract replaces each bet (within 2c of the real entry), 30-version selection re-run.
    Returns dict with the real best LB and the share of reps reaching it."""
    real = select_best(stats, min_n)
    if real is None:
        return {"real_best": None, "note": "no version with n >= %d" % min_n}
    real_lb = stats[real]["lb"]
    pools = {(s, d): Pool(recs, s, d) for s in SPORTS for d in D_LABELS}
    elig = [v for v in all_versions() if stats[v]["n"] >= min_n]
    ranges = {}
    for v in elig:
        p = pools[(v[0], v[1])]
        ranges[v] = [p.range_for(b["entry"]) for b in bets[v]]
        if any(hi <= lo for lo, hi in ranges[v]):
            raise AssertionError("empty price-match pool")    # impossible: the real contract is in the pool
    rng = np.random.default_rng(seed)
    best = np.empty(reps)
    for k in range(reps):
        lbs = []
        for v in elig:
            lo = np.array([x[0] for x in ranges[v]])
            hi = np.array([x[1] for x in ranges[v]])
            idx = lo + np.floor(rng.random(len(lo)) * (hi - lo)).astype(int)
            nets = pools[(v[0], v[1])].net[idx]
            lbs.append(lb_stats(nets)["lb"])
        best[k] = max(lbs)
    share = float(np.mean(best >= real_lb))
    return {"real_best": vname(real), "real_best_lb": real_lb, "reps": reps, "seed": seed, "n_versions_eligible": len(elig),
            "share_reps_reaching_real_best_lb": share, "rep_best_lb_mean": float(best.mean()),
            "rep_best_lb_p95": float(np.quantile(best, 0.95)),
            "verdict": "FAIL" if share > 0.05 else "PASS"}


def coin_flip_unseen(recs, bets_v, v, reps=500, seed=SEED):
    """Selected version on unseen: price-matched random contracts, share of copies with mean net >= real mean."""
    pool = Pool(recs, v[0], v[1])
    rg = [pool.range_for(b["entry"]) for b in bets_v]
    lo = np.array([x[0] for x in rg])
    hi = np.array([x[1] for x in rg])
    real = float(np.mean([b["net"] for b in bets_v]))
    rng = np.random.default_rng(seed)
    means = np.empty(reps)
    for k in range(reps):
        idx = lo + np.floor(rng.random(len(lo)) * (hi - lo)).astype(int)
        means[k] = pool.net[idx].mean()
    return {"real_mean": real, "share_copies_as_good": float(np.mean(means >= real)), "reps": reps, "seed": seed}


def bootstrap_lb(nets, draws=10000, seed=SEED):
    """95% LB of the mean, resampling games (one bet per game per version)."""
    a = np.asarray(nets, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(draws, len(a)))
    return float(np.quantile(a[idx].mean(axis=1), 0.025))


# ---------------------------------------------------------------------------------------------------- calibration
def calibration_rows(recs):
    """Win rate vs 5c entry-price bucket, per sport and D, for YES (at ask) and NO (at 1-bid) contracts."""
    acc = defaultdict(list)
    for r in recs:
        for entry, win, kind, _ in contracts(r):
            b = min(entry // 500, 19)
            acc[(r["sport"], r["D"], kind, b)].append((entry, win, entry_net_e4(entry, win) / ONE))
    rows = []
    for (s, d, kind, b), x in sorted(acc.items()):
        n = len(x)
        rows.append({"sport": s, "D": d, "contract": kind, "bucket_lo_c": b * 5, "bucket_hi_c": b * 5 + 5, "n": n,
                     "mean_entry": round(sum(t[0] for t in x) / n / ONE, 4),
                     "win_rate": round(sum(t[1] for t in x) / n, 4),
                     "mean_net_taker": round(sum(t[2] for t in x) / n, 4)})
    return rows


def write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
