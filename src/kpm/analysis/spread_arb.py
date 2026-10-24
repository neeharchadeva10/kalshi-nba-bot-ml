"""NFL winner vs spread arbitrage: analysis of the pre-registered test.

Spec: reports/output/nfl_spread_arb/PREREGISTRATION.md (relations R1-R4, no forward fill, episodes, opportunities,
strict/loose fill checks, pass criteria, data-quality checks D1-D5). Data: data/nfl_spread.sqlite (schema in
core/spread_schema.sql); kickoff/scores/week come from the games table of data/kpm.sqlite (opened read-only).

Leg convention. Every pair is two legs; a leg is "buy YES at the ask" (side 0) or "buy NO at 1 - bid" (side 1).
gross edge = 1 - price_1 - price_2 (every pair pays at least $1, see test_spread_arb payoff enumeration), and
net edge at C contracts = gross - (taker_fee(price_1, C) + taker_fee(price_2, C)) / C.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from kpm.core import config
from kpm.core.fees import FeeModel
from kpm.core.timeutil import ET

log = logging.getLogger(__name__)

SIZES = (1, 10, 100)
GRID_S = 60
EPS = 1e-9
RELATIONS = ("R1", "R2", "R3", "R4")
HIST_LO, HIST_HI, HIST_BINS = -1.2, 1.2, 240_000          # net-edge histogram: 1e-5 resolution
MANUAL_MIN_NET, MANUAL_MIN_FILL, MANUAL_MIN_DUR, MANUAL_C, MANUAL_PER_WEEK = 0.01, 10.0, 2, 10, 2.00
BOT_MIN_NET, BOT_MIN_FILL, BOT_MIN_DUR, BOT_MAX_C, BOT_PER_WEEK = 0.01, 1.0, 1, 100, 5.00
STRICT_WINDOW = (0, 60)         # (t0, t0 + 60]
LOOSE_WINDOW = (-60, 60)        # [t0 - 60, t0 + 60]
FETCH_WINDOW = (-60, 120)       # trade prints fetched per leg: [t0 - 60, t0 + 120]
SPREAD_RULE = re.compile(r"If .+ wins .*?by more than ([\d.]+) points")   # real text: "If KC wins the DEN at KC game ... by more than 9.5 points"
TIE_RULE = re.compile(r"\$0?\.50|50/50|50 cents")


def default_fee() -> FeeModel:
    return FeeModel(taker_multiplier=0.07, maker_fraction=0.25, rounding="micro_ceil")


def out_dir_default() -> Path:
    return config.PROJECT_ROOT / "reports" / "output" / "nfl_spread_arb"


# ----------------------------------------------------------------------------- relations
def build_pairs(mk: pd.DataFrame) -> pd.DataFrame:
    """All two-leg pairs of R1-R4 for the markets of ONE game.

    mk: rows with kind ('win'|'spread'), team, strike. Output: rel, variant, i1, s1, i2, s2 where i* are row
    positions of mk and s* is 0 (buy YES at ask) or 1 (buy NO at 1 - bid).
    """
    kind, team, strike = mk["kind"].to_numpy(), mk["team"].to_numpy(), mk["strike"].to_numpy(dtype=float)
    teams = sorted(set(team.tolist()))
    W = {t: [i for i in range(len(mk)) if kind[i] == "win" and team[i] == t] for t in teams}
    S = {t: sorted((i for i in range(len(mk)) if kind[i] == "spread" and team[i] == t), key=lambda i: strike[i])
         for t in teams}
    rows: list[tuple] = []
    for t in teams:
        for w in W[t]:                                          # R1: W(T) YES + S(T,K) NO
            for s in S[t]:
                rows.append(("R1", "", w, 0, s, 1))
        for a in S[t]:                                          # R2: S(T,K1) YES + S(T,K2) NO, K1 < K2
            for b in S[t]:
                if strike[a] < strike[b]:
                    rows.append(("R2", "", a, 0, b, 1))
    if len(teams) == 2:
        A, B = teams
        for a in S[A]:                                          # R3: NO on both of two exclusive outcomes
            for b in S[B]:
                rows.append(("R3", "SS", a, 1, b, 1))
            for w in W[B]:
                rows.append(("R3", "SW", a, 1, w, 1))
        for b in S[B]:
            for w in W[A]:
                rows.append(("R3", "SW", b, 1, w, 1))
        for wa in W[A]:
            for wb in W[B]:
                rows.append(("R3", "WW", wa, 1, wb, 1))
                rows.append(("R4", "", wa, 0, wb, 0))           # R4: W(A) YES + W(B) YES
    return pd.DataFrame(rows, columns=["rel", "variant", "i1", "s1", "i2", "s2"]).astype(
        {"i1": int, "s1": int, "i2": int, "s2": int})


# ----------------------------------------------------------------------------- per-game edge arrays
@dataclass
class GameEval:
    pairs: pd.DataFrame
    grid: np.ndarray
    P1: np.ndarray                  # price paid on leg 1, shape (pairs, T); NaN = leg side missing at that t
    P2: np.ndarray
    gross: np.ndarray
    net: dict = field(default_factory=dict)     # C -> net edge. C=10 is dense; others are exact where gross > 0
    #                                             and equal to gross elsewhere (an upper bound that is <= 0 anyway).


def evaluate_game(pairs: pd.DataFrame, bid: np.ndarray, ask: np.ndarray, grid: np.ndarray, fee: FeeModel,
                  sizes=SIZES) -> GameEval:
    """bid/ask: (markets, T) YES quotes at exact candle timestamps, NaN = empty side. No forward fill anywhere."""
    P = np.stack([ask, 1.0 - bid])                              # (2, M, T): price paid for side 0 / side 1
    P1 = P[pairs["s1"].to_numpy(), pairs["i1"].to_numpy()]
    P2 = P[pairs["s2"].to_numpy(), pairs["i2"].to_numpy()]
    gross = 1.0 - P1 - P2
    ge = GameEval(pairs, grid, P1, P2, gross)
    pos = gross > 0                                             # NaN compares False
    for c in sizes:
        if c == 10:
            ge.net[c] = gross - fee.per_contract_array(P1, c) - fee.per_contract_array(P2, c)
        else:
            n = gross.copy()
            n[pos] = gross[pos] - fee.per_contract_array(P1[pos], c) - fee.per_contract_array(P2[pos], c)
            ge.net[c] = n
    return ge


def runs_2d(above: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Maximal runs of True along axis 1 of a boolean (pairs, T) matrix on the contiguous 60 s grid.
    Returns (row, start_idx, end_idx) with end inclusive (same semantics as analysis.episodes.find_episodes)."""
    n, t = above.shape
    pad = np.zeros((n, t + 2), dtype=bool)
    pad[:, 1:-1] = above
    starts = pad[:, 1:-1] & ~pad[:, :-2]
    ends = pad[:, 1:-1] & ~pad[:, 2:]
    r1, s = np.nonzero(starts)
    r2, e = np.nonzero(ends)
    return r1, s, e                                             # np.nonzero is row-major so the two align


def merge_episodes(rows: np.ndarray, starts: np.ndarray, ends: np.ndarray, m1: np.ndarray, m2: np.ndarray) -> np.ndarray:
    """Component id per episode: episodes sharing a market whose [start, end] intervals overlap are merged
    (transitively). m1/m2 are the market indices of the two legs of each episode's pair."""
    n = len(rows)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_market: dict[int, list[int]] = defaultdict(list)
    for k in range(n):
        by_market[int(m1[k])].append(k)
        by_market[int(m2[k])].append(k)
    for ids in by_market.values():
        ids.sort(key=lambda k: starts[k])
        rep, cur_end = ids[0], ends[ids[0]]
        for k in ids[1:]:
            if starts[k] <= cur_end:
                parent[find(k)] = find(rep)
                cur_end = max(cur_end, ends[k])
            else:
                rep, cur_end = k, ends[k]
    return np.array([find(k) for k in range(n)], dtype=int)


def game_opportunities(ge: GameEval, mk: pd.DataFrame, bid: np.ndarray, ask: np.ndarray, info: dict,
                       age: np.ndarray | None = None) -> list[dict]:
    """Episodes (net edge at C=1 > 0 on consecutive snapshots) merged into opportunities, each valued by its best
    pair at t0. info: game_id, season, week, game_type, kickoff_ts."""
    above = ge.net[1] > 0
    if not above.any():
        return []
    rows, s, e = runs_2d(above)
    pr = ge.pairs
    comp = merge_episodes(rows, s, e, pr["i1"].to_numpy()[rows], pr["i2"].to_numpy()[rows])
    groups: dict[int, list[int]] = defaultdict(list)
    for k, c in enumerate(comp):
        groups[int(c)].append(k)
    tickers = mk["ticker"].to_numpy()
    out = []
    for ids in groups.values():
        ids = np.array(ids)
        s0 = int(s[ids].min())
        cand = ids[s[ids] == s0]
        net10 = ge.net[10][rows[cand], s0]
        k = cand[int(np.nanargmax(net10))]
        r = int(rows[k])
        t0 = int(ge.grid[s0])
        legs = {}
        for n in (1, 2):
            i, side = int(pr[f"i{n}"].iat[r]), int(pr[f"s{n}"].iat[r])
            legs[n] = {"ticker": tickers[i], "side": "yes" if side == 0 else "no",
                       "price": float((ask if side == 0 else 1.0 - bid)[i, s0]),
                       "yes_bid": float(bid[i, s0]), "yes_ask": float(ask[i, s0])}
        ko = int(info["kickoff_ts"])
        ages = [float(age[int(pr[f"i{n}"].iat[r]), s0]) if age is not None else float("nan") for n in (1, 2)]
        rec = {
            "game_id": info["game_id"], "season": int(info["season"]), "week": int(info["week"]),
            "game_type": info.get("game_type"), "relation": pr["rel"].iat[r], "variant": pr["variant"].iat[r],
            "phase": "pre" if t0 < ko else "in", "t0": t0, "mins_to_kickoff": (ko - t0) / 60.0,
            "duration": int(e[ids].max() - s0 + 1), "n_episodes": int(len(ids)),
            "n_pairs": int(len(set(rows[ids].tolist()))),
            "leg1_carry_age": ages[0], "leg2_carry_age": ages[1], "max_carry_age": max(ages),
            "gross": float(ge.gross[r, s0]),
            "net1": float(ge.net[1][r, s0]), "net10": float(ge.net[10][r, s0]), "net100": float(ge.net[100][r, s0]),
        }
        for n in (1, 2):
            for key, v in legs[n].items():
                rec[f"leg{n}_{key}"] = v
        out.append(rec)
    return out


# ----------------------------------------------------------------------------- context stats
class ContextAcc:
    """Per relation x season x phase: snapshots/pairs evaluated, share with gross > 0 and net(C=10) > 0, best net edge
    and 99.9th percentile of the net edge at C=10 (histogram, 1e-5 resolution)."""

    def __init__(self):
        self.d: dict[tuple, dict] = {}

    def add(self, ge: GameEval, season: int, kickoff: int) -> None:
        pre_cols = ge.grid < kickoff
        net10 = ge.net[10]
        for rel in RELATIONS:
            sel = (ge.pairs["rel"] == rel).to_numpy()
            if not sel.any():
                continue
            g, n = ge.gross[sel], net10[sel]
            valid = ~np.isnan(g)
            for phase, cols in (("pre", pre_cols), ("in", ~pre_cols)):
                if not cols.any():
                    continue
                v = valid[:, cols]
                if not v.any():
                    continue
                a = self.d.setdefault((rel, season, phase), {
                    "snapshots": 0, "pairs": 0, "gross_pos": 0, "net_pos": 0, "best": -np.inf,
                    "hist": np.zeros(HIST_BINS, dtype=np.int64)})
                a["snapshots"] += int(v.any(axis=0).sum())
                a["pairs"] += int(v.sum())
                gv, nv = g[:, cols][v], n[:, cols][v]
                a["gross_pos"] += int((gv > 0).sum())
                a["net_pos"] += int((nv > 0).sum())
                a["best"] = max(a["best"], float(nv.max()))
                a["hist"] += np.histogram(nv, bins=HIST_BINS, range=(HIST_LO, HIST_HI))[0]

    def frame(self) -> pd.DataFrame:
        rows = []
        edges = np.linspace(HIST_LO, HIST_HI, HIST_BINS + 1)
        for (rel, season, phase), a in sorted(self.d.items()):
            cum = np.cumsum(a["hist"])
            p999 = np.nan
            if cum[-1]:
                p999 = float(edges[np.searchsorted(cum, 0.999 * cum[-1]) + 1])   # upper edge of the quantile bin
            rows.append({"relation": rel, "season": season, "phase": phase, "snapshots_evaluated": a["snapshots"],
                         "pairs_evaluated": a["pairs"],
                         "share_gross_pos": a["gross_pos"] / a["pairs"], "share_net10_pos": a["net_pos"] / a["pairs"],
                         "best_net10": a["best"], "p99_9_net10": p999})
        return pd.DataFrame(rows, columns=["relation", "season", "phase", "snapshots_evaluated", "pairs_evaluated",
                                           "share_gross_pos", "share_net10_pos", "best_net10", "p99_9_net10"])


# ----------------------------------------------------------------------------- fills
def fill_volumes(tr: pd.DataFrame, t0: float, side: str, ref_yes_price: float) -> tuple[float, float]:
    """(strict, loose) qualifying contracts on one leg. tr: columns ts, yes_price, count, taker_side.
    side 'yes' (buy YES at ask = ref): strict needs taker_side 'yes' and yes_price <= ref, in (t0, t0+60].
    side 'no' (buy NO / sell YES into bid = ref): strict needs taker_side 'no' and yes_price >= ref.
    Loose: any print in [t0-60, t0+60] at an as-good price (no taker-side condition)."""
    if tr is None or len(tr) == 0:
        return 0.0, 0.0
    ts, px, cnt = tr["ts"].to_numpy(float), tr["yes_price"].to_numpy(float), tr["count"].to_numpy(float)
    good = px <= ref_yes_price + EPS if side == "yes" else px >= ref_yes_price - EPS
    strict = (ts > t0 + STRICT_WINDOW[0]) & (ts <= t0 + STRICT_WINDOW[1]) & good & (tr["taker_side"].to_numpy() == side)
    loose = (ts >= t0 + LOOSE_WINDOW[0]) & (ts <= t0 + LOOSE_WINDOW[1]) & good
    return float(cnt[strict].sum()), float(cnt[loose].sum())


def fetch_trades(conn: sqlite3.Connection, client, jobs: list[tuple[str, int]]) -> int:
    """Download trade prints (public GET only) for (ticker, t0) windows [t0-60, t0+120] not fetched before."""
    from kpm.core.prices import parse_dollars
    from kpm.core.timeutil import parse_iso
    done = 0
    for ticker, t0 in jobs:
        lo, hi = t0 + FETCH_WINDOW[0], t0 + FETCH_WINDOW[1]
        if conn.execute("SELECT 1 FROM sp_trade_windows WHERE ticker=? AND min_ts=? AND max_ts=?",
                        (ticker, lo, hi)).fetchone():
            continue
        flags = {client.trades_historical(lo), client.trades_historical(hi)}
        rows = []
        for hist in flags:
            for t in client.trades(ticker, lo, hi, hist):
                rows.append((t["trade_id"], ticker, float(parse_iso(t["created_time"])),
                             float(parse_dollars(t["yes_price_dollars"])),
                             float(parse_dollars(t.get("count_fp")) or t.get("count") or 0), t.get("taker_side") or ""))
        conn.executemany("INSERT OR IGNORE INTO sp_trades (trade_id, ticker, ts, yes_price, count, taker_side) "
                         "VALUES (?,?,?,?,?,?)", rows)
        conn.execute("INSERT OR REPLACE INTO sp_trade_windows (ticker, min_ts, max_ts, n) VALUES (?,?,?,?)",
                     (ticker, lo, hi, len(rows)))
        conn.commit()
        done += 1
    return done


def add_fills(opps: pd.DataFrame, conn: sqlite3.Connection, client=None, max_fetch: int = 20_000) -> dict:
    """Fetch (unless client is None) and evaluate strict/loose fills of each opportunity's chosen pair."""
    cols = ["leg1_strict", "leg1_loose", "leg2_strict", "leg2_loose", "strict_fillable", "loose_fillable"]
    for c in cols:
        opps[c] = np.nan
    info = {"opportunities": len(opps), "fetched": 0, "skipped_no_fetch": 0}
    if opps.empty:
        return info
    if client is not None:
        order = opps.assign(_p=(opps["net10"] > 0).astype(int)).sort_values(["_p", "net10"], ascending=False)
        n_pos = int((opps["net10"] > 0).sum())
        budget = max(max_fetch, n_pos)
        todo = order.index[:budget]
        info["skipped_no_fetch"] = int(len(order) - len(todo))
        info["fetched_windows"] = fetch_trades(conn, client, [(opps.at[i, f"leg{n}_ticker"], int(opps.at[i, "t0"]))
                                                              for i in todo for n in (1, 2)])
        info["fetched"] = int(len(todo))
        evaluate = list(todo)
    else:
        evaluate = list(opps.index)
    cache: dict[str, pd.DataFrame] = {}
    for i in evaluate:
        t0 = float(opps.at[i, "t0"])
        vols = []
        for n in (1, 2):
            tk, side = opps.at[i, f"leg{n}_ticker"], opps.at[i, f"leg{n}_side"]
            if tk not in cache:
                cache[tk] = pd.read_sql_query("SELECT ts, yes_price, count, taker_side FROM sp_trades WHERE ticker=?",
                                              conn, params=(tk,))
            ref = opps.at[i, f"leg{n}_yes_ask"] if side == "yes" else opps.at[i, f"leg{n}_yes_bid"]
            vols.append(fill_volumes(cache[tk], t0, side, float(ref)))
        opps.at[i, "leg1_strict"], opps.at[i, "leg1_loose"] = vols[0]
        opps.at[i, "leg2_strict"], opps.at[i, "leg2_loose"] = vols[1]
        opps.at[i, "strict_fillable"] = min(vols[0][0], vols[1][0])
        opps.at[i, "loose_fillable"] = min(vols[0][1], vols[1][1])
    return info


# ----------------------------------------------------------------------------- verdict
def net_edge_at(p1: float, p2: float, c: float, fee: FeeModel) -> float:
    return 1.0 - p1 - p2 - (fee.order_fee(p1, c) + fee.order_fee(p2, c)) / c


def add_values(opps: pd.DataFrame, fee: FeeModel) -> pd.DataFrame:
    """Per-opportunity value under each rule: manual_<fill>, bot_<fill>, botC_<fill> (0 when it does not qualify)."""
    o = opps.copy()
    for fill in ("strict", "loose"):
        f = o[f"{fill}_fillable"].fillna(0.0) if f"{fill}_fillable" in o else pd.Series(0.0, index=o.index)
        ok_m = (o["net10"] >= MANUAL_MIN_NET - EPS) & (f >= MANUAL_MIN_FILL) & (o["duration"] >= MANUAL_MIN_DUR)
        o[f"manual_{fill}"] = np.where(ok_m, MANUAL_C * o["net10"], 0.0)
        bot_c = np.minimum(BOT_MAX_C, f)
        bot_net = np.zeros(len(o))
        for k, (idx, r) in enumerate(o.iterrows()):
            if f.iat[k] >= BOT_MIN_FILL and r["duration"] >= BOT_MIN_DUR:
                bot_net[k] = net_edge_at(r["leg1_price"], r["leg2_price"], float(bot_c.iat[k]), fee)
        ok_b = (bot_net >= BOT_MIN_NET - EPS) & (f.to_numpy() >= BOT_MIN_FILL) & (o["duration"].to_numpy() >= BOT_MIN_DUR)
        o[f"botC_{fill}"] = np.where(ok_b, bot_c, 0.0)
        o[f"bot_{fill}"] = np.where(ok_b, bot_c * bot_net, 0.0)
    return o


def compute_verdict(valued: pd.DataFrame, weeks: dict[int, list[int]], required=(2025, 2026),
                    have_fills: bool = True) -> tuple[dict, pd.DataFrame]:
    """weeks: season -> distinct NFL weeks with games (denominator). Returns (verdict dict, weekly frame)."""
    wk_rows = []
    for season, wl in weeks.items():
        for wk in wl:
            for phase in ("pre", "in"):
                d = valued[(valued["season"] == season) & (valued["week"] == wk) & (valued["phase"] == phase)]
                wk_rows.append({"season": season, "week": wk, "phase": phase, "n_opportunities": len(d),
                                **{c: float(d[c].sum()) for c in valued.columns
                                   if c.startswith(("manual_", "bot_"))}})
    weekly = pd.DataFrame(wk_rows)
    res: dict = {"required_seasons": list(required), "seasons": {}}
    for season, wl in weeks.items():
        n_w = len(wl)
        s = res["seasons"][str(season)] = {"n_weeks": n_w}
        for phase in ("pre", "in"):
            w = weekly[(weekly["season"] == season) & (weekly["phase"] == phase)]
            for fill in ("strict", "loose"):
                s[f"{phase}_{fill}"] = {
                    "manual_total": float(w[f"manual_{fill}"].sum()),
                    "manual_per_week": float(w[f"manual_{fill}"].sum() / n_w) if n_w else 0.0,
                    "bot_total": float(w[f"bot_{fill}"].sum()),
                    "bot_per_week": float(w[f"bot_{fill}"].sum() / n_w) if n_w else 0.0}
    for use, thr in (("manual", MANUAL_PER_WEEK), ("bot", BOT_PER_WEEK)):
        if not have_fills:
            res[use] = {"verdict": "NOT EVALUATED (run without --no-fills)", "threshold_per_week": thr}
            continue
        per = {}
        ok = True
        for season in required:
            sd = res["seasons"].get(str(season))
            if sd is None or sd["n_weeks"] == 0:
                per[str(season)] = None
                ok = False
                continue
            per[str(season)] = sd["pre_strict"][f"{use}_per_week"]
            ok &= per[str(season)] >= thr - EPS
        res[use] = {"verdict": "PASS" if ok else "FAIL", "threshold_per_week": thr, "per_week_by_season": per}
    return res, weekly


# ----------------------------------------------------------------------------- data loading + quality checks
def open_games(path: str | Path | None) -> pd.DataFrame | None:
    p = Path(path) if path else config.path("db_path")
    if not p.exists():
        return None
    conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    try:
        return pd.read_sql_query("SELECT game_id, season, week, game_type, home_team, away_team, kickoff_ts, "
                                 "home_score, away_score FROM games", conn)
    except Exception as exc:                                   # noqa: BLE001 - missing table etc.
        log.warning("games table unavailable (%s)", exc)
        return None
    finally:
        conn.close()


def dq_markets(mk: pd.DataFrame, games: pd.DataFrame | None) -> tuple[set, dict]:
    """D1, D2, D5 on the market table. Returns (excluded mids, report)."""
    rep: dict = {}
    excl: set = set()
    # D1: one game / one team per market; spread strike consistent with ticker suffix (LAR3 <-> 2.5)
    d1 = []
    gi = games.set_index("game_id") if games is not None else None
    for r in mk.itertuples():
        why, info = [], []
        if r.kind == "spread":
            m = re.search(r"-([A-Za-z]+)(\d+)$", r.ticker)
            if m is None or r.strike is None or pd.isna(r.strike):
                why.append("ticker suffix or strike missing")
            elif min(abs(float(m.group(2)) - (float(r.strike) + d)) for d in (-0.5, 0.5)) > EPS:   # real: KC9 <-> 9.5
                info.append(f"suffix {m.group(2)} vs strike {r.strike} (informational)")
        if not r.team:
            why.append("no team")
        if gi is not None and r.game_id in gi.index and r.team not in (gi.at[r.game_id, "home_team"], gi.at[r.game_id, "away_team"]):
            why.append(f"team {r.team} not in game {r.game_id}")
        if why or info:
            d1.append({"ticker": r.ticker, "why": "; ".join(why + info), "excluded": bool(why)})
        if why:
            excl.add(r.mid)
    per_game = mk.groupby("game_id").agg(teams=("team", "nunique"), kick=("kickoff_ts", "nunique"),
                                         seasons=("season", "nunique"), weeks=("week", "nunique"))
    bad_games = per_game[(per_game["teams"] != 2) | (per_game["kick"] != 1) | (per_game["seasons"] != 1) | (per_game["weeks"] > 1)]
    for gid in bad_games.index:
        d1.append({"game_id": gid, "why": f"inconsistent game: {bad_games.loc[gid].to_dict()}"})
        excl.update(mk.loc[mk["game_id"] == gid, "mid"].tolist())
    dup = mk[mk.duplicated(["game_id", "kind", "team", "strike"], keep=False)]
    for r in dup.itertuples():
        d1.append({"ticker": r.ticker, "why": "duplicate (game, kind, team, strike)"})
        excl.add(r.mid)
    rep["D1"] = {"n_markets": len(mk), "n_flagged": len(d1), "flagged": d1[:200]}

    # D5: rules text
    d5 = []
    for r in mk.itertuples():
        txt = " ".join(x for x in (r.rules_primary, r.rules_secondary) if isinstance(x, str))
        if r.kind == "spread":
            m = SPREAD_RULE.search(txt)
            ok = m is not None and r.strike is not None and not pd.isna(r.strike) and abs(float(m.group(1)) - float(r.strike)) < EPS
        else:
            ok = bool(TIE_RULE.search(txt))
        if not ok:
            d5.append({"ticker": r.ticker, "kind": r.kind, "rules": txt[:300]})
            excl.add(r.mid)
    rep["D5"] = {"n_non_matching": len(d5), "non_matching_excluded": d5[:200]}

    # D2: settled results vs nflverse final score
    d2, n_checked = [], 0
    if gi is not None:
        for r in mk.itertuples():
            if r.game_id not in gi.index:
                continue
            g = gi.loc[r.game_id]
            if pd.isna(g["home_score"]) or pd.isna(g["away_score"]):
                continue
            actual = r.settlement_value if r.settlement_value is not None and not pd.isna(r.settlement_value) else \
                {"yes": 1.0, "no": 0.0}.get(str(r.result).lower() if r.result else "", np.nan)
            if pd.isna(actual):
                continue
            margin = (g["home_score"] - g["away_score"]) * (1 if r.team == g["home_team"] else -1)
            exp = float(margin > r.strike) if r.kind == "spread" else (1.0 if margin > 0 else 0.5 if margin == 0 else 0.0)
            n_checked += 1
            if abs(exp - float(actual)) > 1e-6:
                d2.append({"ticker": r.ticker, "expected": exp, "actual": float(actual), "margin": float(margin)})
    rep["D2"] = {"n_checked": n_checked, "n_mismatch": len(d2), "mismatches": d2[:200],
                 "note": None if gi is not None else "games table unavailable; D2 not run"}
    return excl, rep


def coverage_row(ticker: str, bid: np.ndarray, ask: np.ndarray, age: np.ndarray, grid_minutes: int) -> dict:
    """D3 (Amendment 1) for one market: share of the game's grid minutes with a known (carried) quote, share of NULL
    bids/asks among known minutes, median and max carry age (minutes)."""
    known = ~np.isnan(age)
    n = int(known.sum())
    return {"ticker": ticker, "known_share": n / grid_minutes if grid_minutes else float("nan"),
            "null_bid_share": float(np.isnan(bid[known]).mean()) if n else float("nan"),
            "null_ask_share": float(np.isnan(ask[known]).mean()) if n else float("nan"),
            "median_carry_age": float(np.median(age[known])) if n else float("nan"),
            "max_carry_age": float(age[known].max()) if n else float("nan")}


def summarize_coverage(rows: list[dict]) -> dict:
    d = pd.DataFrame(rows)
    if d.empty:
        return {"n_markets": 0}
    return {"n_markets": len(d), "median_known_share": float(d["known_share"].median()),
            "mean_known_share": float(d["known_share"].mean()),
            "mean_null_bid_share": float(d["null_bid_share"].mean()),
            "mean_null_ask_share": float(d["null_ask_share"].mean()),
            "median_of_median_carry_age": float(d["median_carry_age"].median()),
            "max_carry_age": float(d["max_carry_age"].max()),
            "p99_of_max_carry_age": float(d["max_carry_age"].quantile(0.99)),
            "lowest_known_share": d.sort_values("known_share").head(20)[["ticker", "known_share"]].to_dict("records"),
            "note": "Amendment 1: quotes carried forward from the last candle. The check that the open after a gap equals "
                    "the previous close needs candle opens (not in sp_quotes) and is run separately."}


def build_matrices(mk: pd.DataFrame, q: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict]:
    """Full 1-minute grid per game (Amendment 1). Candles exist only for minutes where the book/trades changed, so
    each market's quote ROW (bid and ask together, NULL included) is carried forward from its last candle until its
    next candle or its end (min(close_ts, q_end_ts); nothing is carried to or past it). Minutes before a market's
    first candle are NaN. Returns grid (T,), bid, ask, age (minutes since the last candle; NaN = unknown), stats.
    Bid > ask snapshots are excluded (D4). Rows follow mk's row order."""
    stats = {"d4_bid_gt_ask": {}, "d4_snapshots": 0, "offgrid": 0}
    M = len(mk)
    if q.empty:
        e = np.empty((M, 0))
        return np.array([], dtype=np.int64), e, e.copy(), e.copy(), stats
    row = {int(m): i for i, m in enumerate(mk["mid"])}
    q = q[q["mid"].isin(row)]
    sp_mids = set(mk.loc[mk["kind"] == "spread", "mid"].astype(int))
    qs = q[q["mid"].isin(sp_mids)]
    tmin = int((qs if len(qs) else q)["ts"].min())
    early = q["ts"] < tmin                                      # keep each market's last pre-grid candle, put at tmin
    if early.any():
        last_early = q[early].sort_values("ts").groupby("mid").tail(1).assign(ts=tmin)
        q = pd.concat([q[~early], last_early])
    off = (q["ts"] - tmin) % GRID_S != 0
    stats["offgrid"] = int(off.sum())
    q = q[~off]
    end = np.fmin(mk["close_ts"].to_numpy(dtype=float),
                  mk["q_end_ts"].to_numpy(dtype=float) if "q_end_ts" in mk else np.full(M, np.inf))
    end = np.where(np.isnan(end), np.inf, end)
    tmax = int(q["ts"].max())
    fin = end[np.isfinite(end)]
    if len(fin):
        tmax = max(tmax, int(fin.max()) - GRID_S)
    grid = np.arange(tmin, tmax + 1, GRID_S, dtype=np.int64)
    T = len(grid)
    ri = q["mid"].map(row).to_numpy()
    ci = ((q["ts"].to_numpy() - tmin) // GRID_S).astype(int)
    rb = np.full((M, T), np.nan)
    ra = np.full((M, T), np.nan)
    rb[ri, ci] = q["bid_e4"].to_numpy(dtype=float) / 10000.0
    ra[ri, ci] = q["ask_e4"].to_numpy(dtype=float) / 10000.0
    has = np.zeros((M, T), dtype=bool)
    has[ri, ci] = True
    crossed = has & (rb > ra)
    for i in np.flatnonzero(crossed.any(axis=1)):
        stats["d4_bid_gt_ask"][mk["ticker"].iat[i]] = int(crossed[i].sum())
    last = np.maximum.accumulate(np.where(has, np.arange(T)[None, :], -1), axis=1)
    known = last >= 0
    lc = np.maximum(last, 0)
    bid = np.where(known, np.take_along_axis(rb, lc, axis=1), np.nan)
    ask = np.where(known, np.take_along_axis(ra, lc, axis=1), np.nan)
    age = np.where(known, np.arange(T)[None, :] - last, np.nan).astype(float)
    after = grid[None, :] >= end[:, None]                       # nothing at or after the market's end
    bid[after] = np.nan
    ask[after] = np.nan
    age[after] = np.nan
    bad = bid > ask                                             # NaN compares False
    stats["d4_snapshots"] = int(bad.sum())
    bid[bad] = np.nan
    ask[bad] = np.nan
    return grid, bid, ask, age, stats


# ----------------------------------------------------------------------------- main
def _iso(ts):
    d = datetime.fromtimestamp(int(ts), timezone.utc)
    return d.strftime("%Y-%m-%d %H:%M:%S"), d.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S %Z")


def run(db_path: str | Path | None = None, fills: bool = True, games_db: str | Path | None = None,
        out_dir: str | Path | None = None, required=(2025, 2026), max_fetch: int = 20_000, client=None) -> dict:
    fee = default_fee()
    db_path = Path(db_path) if db_path else config.PROJECT_ROOT / "data" / "nfl_spread.sqlite"
    if not db_path.exists():
        raise SystemExit(f"{db_path} not found (the collector creates it)")
    out = Path(out_dir) if out_dir else out_dir_default()
    out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=300) if fills else sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        mk_all = pd.read_sql_query("SELECT * FROM sp_markets", conn)
        games = open_games(games_db)
        excl, dq = dq_markets(mk_all, games)
        cov_rows: list[dict] = []
        gi = games.set_index("game_id") if games is not None else None
        mk_ok = mk_all[~mk_all["mid"].isin(excl)]
        ctx = ContextAcc()
        opps: list[dict] = []
        d4_total: dict[str, int] = {}
        d4_snaps = 0
        weeks_by_season: dict[int, set] = defaultdict(set)
        for gid, g in mk_all.groupby("game_id"):                # denominator: every game in the study
            season = int(gi.at[gid, "season"]) if gi is not None and gid in gi.index else int(g["season"].iloc[0])
            wk = gi.at[gid, "week"] if gi is not None and gid in gi.index else g["week"].iloc[0]
            if pd.notna(wk):
                weeks_by_season[season].add(int(wk))
        n_games = 0
        for gid, mk in mk_ok.groupby("game_id"):
            mk = mk.reset_index(drop=True)
            if gi is not None and gid in gi.index:
                r = gi.loc[gid]
                info = {"game_id": gid, "season": r["season"], "week": r["week"], "game_type": r["game_type"],
                        "kickoff_ts": r["kickoff_ts"]}
            else:
                r = mk.iloc[0]
                info = {"game_id": gid, "season": r["season"], "week": r["week"], "game_type": r["game_type"],
                        "kickoff_ts": r["kickoff_ts"]}
            q = pd.read_sql_query(f"SELECT mid, ts, bid_e4, ask_e4 FROM sp_quotes WHERE mid IN "
                                  f"({','.join('?' * len(mk))})", conn, params=[int(m) for m in mk["mid"]])
            grid, bid, ask, age, st = build_matrices(mk, q)
            d4_total.update(st["d4_bid_gt_ask"])
            d4_snaps += st["d4_snapshots"]
            if len(grid) == 0:
                continue
            ends = np.fmin(mk["close_ts"].to_numpy(float), mk["q_end_ts"].to_numpy(float))
            for i in range(len(mk)):
                cov_rows.append(coverage_row(mk["ticker"].iat[i], bid[i], ask[i], age[i],
                                             int((grid < (ends[i] if not np.isnan(ends[i]) else np.inf)).sum())))
            pairs = build_pairs(mk)
            if pairs.empty:
                continue
            ge = evaluate_game(pairs, bid, ask, grid, fee)
            ctx.add(ge, int(info["season"]), int(info["kickoff_ts"]))
            opps += game_opportunities(ge, mk, bid, ask, info, age)
            n_games += 1
            if n_games % 25 == 0:
                log.info("spread-arb: %d games evaluated, %d opportunities so far", n_games, len(opps))
        dq["D3"] = summarize_coverage(cov_rows)
        dq["D4"] = {"n_markets_with_bid_gt_ask": len(d4_total), "n_candle_rows_bid_gt_ask": int(sum(d4_total.values())),
                    "n_snapshots_excluded": int(d4_snaps),
                    "by_market": dict(sorted(d4_total.items(), key=lambda kv: -kv[1])[:100])}
        cols0 = ["game_id", "season", "week", "game_type", "relation", "variant", "phase", "t0", "mins_to_kickoff",
                 "duration", "n_episodes", "n_pairs", "leg1_carry_age", "leg2_carry_age", "max_carry_age", "gross", "net1", "net10", "net100"] + \
                [f"leg{n}_{k}" for n in (1, 2) for k in ("ticker", "side", "price", "yes_bid", "yes_ask")]
        od = pd.DataFrame(opps, columns=cols0)
        fill_info: dict = {"skipped": "--no-fills" if not fills else None}
        if fills:
            if client is None and not od.empty:
                from kpm.clients.kalshi import KalshiClient
                client = KalshiClient(conn=conn)
            fill_info = add_fills(od, conn, client, max_fetch)
        else:
            for c in ("leg1_strict", "leg1_loose", "leg2_strict", "leg2_loose", "strict_fillable", "loose_fillable"):
                od[c] = np.nan
        weeks = {s: sorted(w) for s, w in sorted(weeks_by_season.items())}
        # Amendment 1: failed the strict fill check but the violation stood >= 2 minutes -> reported, never counted
        od["stood_unconfirmed"] = (od["strict_fillable"] < BOT_MIN_FILL) & (od["duration"] >= 2)
        od["stood_minutes"] = np.where(od["stood_unconfirmed"], od["duration"], 0)
        valued = add_values(od, fee)
        verdict, weekly = compute_verdict(valued, weeks, required, have_fills=fills)
        cstats = ctx.frame()
        # ---- outputs
        od_out = valued.copy()
        od_out["t0_utc"], od_out["t0_et"] = zip(*[_iso(t) for t in od_out["t0"]]) if len(od_out) else ([], [])
        od_out.sort_values(["season", "week", "t0"]).to_csv(out / "opportunities.csv", index=False)
        weekly.to_csv(out / "weekly.csv", index=False)
        cstats.to_csv(out / "context_stats.csv", index=False)
        top = od_out.assign(value10=10 * od_out["net10"]).sort_values("value10", ascending=False).head(20)
        top.to_csv(out / "top20.csv", index=False)
        dq["D6_note"] = "top20.csv lists the 10 largest opportunities to re-check by hand against the cached raw responses"
        (out / "data_quality.json").write_text(json.dumps(dq, indent=2, default=_json_default), encoding="utf-8")
        summary = {"games_evaluated": n_games, "markets_excluded": len(excl), "opportunities": len(od),
                   "opportunities_net10_positive": int((od["net10"] > 0).sum()) if len(od) else 0,
                   "fills": fill_info,
                   "stood_unconfirmed": {
                       "n": int(od["stood_unconfirmed"].sum()),
                       "n_pre_net10_ge_1c": int((od["stood_unconfirmed"] & (od["phase"] == "pre") & (od["net10"] >= 0.01)).sum()),
                       "pre_value_at_10_net10_ge_1c": float((10 * od["net10"])[od["stood_unconfirmed"] & (od["phase"] == "pre") & (od["net10"] >= 0.01)].sum()),
                       "note": "not counted in the verdict"}, "verdict": verdict, "weeks_by_season": weeks,
                   "d3_median_known_share": dq["D3"].get("median_known_share"),
                   "best_net10_pre": float(od.loc[od["phase"] == "pre", "net10"].max()) if (od["phase"] == "pre").any() else None}
        (out / "summary.json").write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
        print_summary(summary, cstats, dq)
        return summary
    finally:
        conn.close()


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def print_summary(s: dict, cstats: pd.DataFrame, dq: dict) -> None:
    v = s["verdict"]
    print("\n=== NFL winner vs spread arbitrage (pre-registered) ===")
    print(f"games evaluated {s['games_evaluated']}, markets excluded by D1/D5 {s['markets_excluded']}, "
          f"opportunities {s['opportunities']} (net10>0: {s['opportunities_net10_positive']}), fills: {s['fills']}")
    print(f"D3 median known-quote share {s['d3_median_known_share'] or 0:.1%}; D1 flagged {dq['D1']['n_flagged']}, "
          f"D2 mismatches {dq['D2']['n_mismatch']}/{dq['D2']['n_checked']}, D4 snapshots excluded "
          f"{dq['D4']['n_snapshots_excluded']}, D5 excluded {dq['D5']['n_non_matching']}")
    for season, d in v["seasons"].items():
        print(f"season {season} ({d['n_weeks']} weeks)")
        for key in ("pre_strict", "pre_loose", "in_strict", "in_loose"):
            x = d[key]
            print(f"  {key:11s} manual ${x['manual_total']:9.2f} (${x['manual_per_week']:.2f}/wk)   "
                  f"bot ${x['bot_total']:9.2f} (${x['bot_per_week']:.2f}/wk)")
    for use in ("manual", "bot"):
        print(f"VERDICT {use.upper()}: {v[use]['verdict']}  (need >= ${v[use]['threshold_per_week']:.2f}/wk in both seasons, "
              f"pre-game, strict fills)")
    if len(cstats):
        print("\ncontext (relation x season x phase):")
        print(cstats.to_string(index=False, float_format=lambda x: f"{x:.5g}"))
