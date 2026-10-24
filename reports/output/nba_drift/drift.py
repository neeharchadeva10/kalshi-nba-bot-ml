"""Research log #24 analysis (reports/output/nba_drift/PREREGISTRATION.md). Implements the pre-registration as written.

Run: .venv/Scripts/python.exe reports/output/nba_drift/drift.py [NBA|NFL]  -> results.json + README.md
Pure helpers (fee, clv, elo, split, last_at_or_before) are unit-tested in test_drift.py.
Interpretation choices where the text is silent are listed in INTERPRETATION and written into the README.
"""
from __future__ import annotations

import bisect
import json
import math
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
SEED = 20261020
ENTRIES = {"T-24h": 24 * 3600, "T-6h": 6 * 3600, "T-1h": 3600}
STRATS = ["V1 favourite", "V2 underdog", "V3 home", "V4 away", "V5 momentum", "V6 reversal", "V7 elo edge"]
SIZE = 10
NBA_SEASON_START = int(datetime(2025, 10, 21, tzinfo=timezone.utc).timestamp())
ELO_K, ELO_HOME, ELO_START, ELO_EDGE = 20.0, 100.0, 1500.0, 0.03

INTERPRETATION = [
    "Quote validity: a candle side counts as missing when bid <= 0 or ask >= 1 or bid > ask (repo clean_quote).",
    "Exclusions are per game and per entry time: a game is skipped at E when EITHER team market has no valid bid/ask at "
    "the entry candle or at the close candle (so the side choice never depends on a missing quote).",
    "A candle is chosen first (last one closing at or before E / T-5min), then tested for validity; no staleness limit.",
    "V1/V2 pick by entry mid vs 0.5 (V1: higher-mid team if > 0.5; V2: lower-mid team if < 0.5; a 0.5/0.5 game is skipped).",
    "V5/V6: change = mid(E) - mid(last candle at or before E-24h), per team; V5 buys the team with the larger positive "
    "change, V6 the team with the larger negative change; skipped if either history mid is missing or the change is 0.",
    "V7: edge = Elo probability - entry mid; buy the team with edge >= 0.03 (larger edge if both). Elo updates on every "
    "game with a known result in tip order (excluded-for-quotes games still update Elo); warm-up games are not traded.",
    "NFL Elo and warm-up restart per season (1500 at each season start; first 2 weeks of each season are warm-up).",
    "Split: all games with a clean result, sorted by tip; first ceil(N/2) = discovery, the rest = unseen (same split for "
    "every version).",
    "Bootstrap CI: percentile 2.5 / 97.5 of the mean over 10,000 resamples of whole games (one trade per game); the "
    "rule-2 bound is the 2.5th percentile.",
    "Coin flip: 500 copies, each buys a uniformly random team of the same unseen games the strategy traded; share of "
    "copies with mean CLV net >= the strategy's.",
    "$/week = unseen mean CLV net x 10 x (unseen trades / unseen weeks); unseen weeks = ISO weeks with at least one unseen game (amendment 2026-10-20).",
    "NBA: occurrence_datetime is missing on most historical markets; expected_expiration_time (equal to occurrence_datetime "
    "wherever both exist) is used instead. Hourly candles were fetched from max(open, tip-50h) so the T-24h entry has its "
    "24h lookback.",
]


# ----------------------------------------------------------------------------------------------- pure helpers
def fee(p: float, n: int = SIZE) -> float:
    """Per-contract fee when trading n contracts at price p (dollars): ceil(0.07*n*p*(1-p) to 0.0001) / n."""
    raw = 0.07 * n * p * (1 - p) / 0.0001
    return math.ceil(round(raw, 6)) * 0.0001 / n


def valid_quote(bid, ask) -> bool:
    return (bid is not None and ask is not None and not math.isnan(bid) and not math.isnan(ask)
            and bid > 0 and ask < 1 and bid <= ask)


def mid(bid, ask) -> float:
    return (bid + ask) / 2.0


def last_at_or_before(ts: list, t: float):
    """Index of the last timestamp <= t in a sorted list (None when there is none)."""
    i = bisect.bisect_right(ts, t)
    return i - 1 if i > 0 else None


def clv_net(close_mid: float, entry_ask: float) -> float:
    return close_mid - entry_ask - fee(entry_ask)


def settle_pnl(result: float, entry_ask: float) -> float:
    return result - entry_ask - fee(entry_ask)


def exit_pnl(close_bid: float, entry_ask: float) -> float:
    return close_bid - entry_ask - fee(entry_ask) - fee(close_bid)


def elo_prob_home(r_home: float, r_away: float, home_adv: float = ELO_HOME) -> float:
    return 1.0 / (1.0 + 10 ** (-(r_home + home_adv - r_away) / 400.0))


def elo_update(r_home: float, r_away: float, home_won: bool, k: float = ELO_K, home_adv: float = ELO_HOME):
    p = elo_prob_home(r_home, r_away, home_adv)
    d = k * ((1.0 if home_won else 0.0) - p)
    return r_home + d, r_away - d


def split_index(n: int) -> int:
    """Discovery = first ceil(n/2) games."""
    return (n + 1) // 2


def bootstrap_ci(x, draws: int = 10000, seed: int = SEED):
    x = np.asarray(x, dtype=float)
    rng = np.random.default_rng(seed)
    n = len(x)
    if n == 0:
        return (float("nan"), float("nan"))
    means = np.empty(draws)
    for i in range(0, draws, 500):  # chunked to bound memory
        j = min(500, draws - i)
        means[i:i + j] = x[rng.integers(0, n, size=(j, n))].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


# ----------------------------------------------------------------------------------------------- data model
class Mkt:
    __slots__ = ("code", "name", "result", "h_ts", "h_bid", "h_ask", "m_ts", "m_bid", "m_ask")

    def __init__(self, code, name, result):
        self.code, self.name, self.result = code, name, result
        self.h_ts, self.h_bid, self.h_ask = [], [], []   # entry-price series (hourly NBA; 1-min NFL)
        self.m_ts, self.m_bid, self.m_ask = [], [], []   # 1-min series for the close


class Game:
    def __init__(self, gid, tip, season, a: Mkt, b: Mkt):
        self.gid, self.tip, self.season = gid, tip, season
        self.mk = [a, b]
        self.home_idx = None          # index in mk of the home team


def at(series_ts, bids, asks, t):
    """(bid, ask, ts, age) of the last candle at or before t; None if there is none."""
    i = last_at_or_before(series_ts, t)
    if i is None:
        return None
    return bids[i], asks[i], series_ts[i], t - series_ts[i]


# ----------------------------------------------------------------------------------------------- NBA loader
def load_nba():
    conn = sqlite3.connect(ROOT / "data" / "nba_drift.sqlite")
    ev_title = dict(conn.execute("SELECT event_ticker, title FROM events"))
    by_ev = defaultdict(list)
    for r in conn.execute("SELECT ticker,event_ticker,yes_sub_title,result,occurrence_ts,occ_source,status FROM markets"):
        by_ev[r[1]].append(r)
    candles = defaultdict(lambda: {"hour": [], "min": []})
    for t, k, ts, b, a in conn.execute("SELECT ticker,kind,ts,bid,ask FROM candles ORDER BY ts"):
        candles[t][k].append((ts, b, a))
    games, excl = [], Counter()
    flags = {"title_ticker_home_mismatch": [], "title_unresolved": [], "occ_source": Counter(), "code_mismatch": []}
    for ev, ms in sorted(by_ev.items()):
        if len(ms) != 2:
            excl["event without exactly 2 markets"] += 1
            continue
        res = [m[3] for m in ms]
        if sorted(x or "" for x in res) != ["no", "yes"]:
            excl[f"no clean result {res}"] += 1
            continue
        tip = min(m[4] for m in ms) - 3 * 3600
        if tip < NBA_SEASON_START:
            excl["preseason (T before 2025-10-21)"] += 1
            continue
        flags["occ_source"][ms[0][5]] += 1
        mk = []
        for m in ms:
            x = Mkt(m[0].rsplit("-", 1)[1], m[2], 1.0 if m[3] == "yes" else 0.0)
            c = candles[m[0]]
            if c["hour"]:
                x.h_ts, x.h_bid, x.h_ask = [list(z) for z in zip(*c["hour"])]
            if c["min"]:
                x.m_ts, x.m_bid, x.m_ask = [list(z) for z in zip(*c["min"])]
            mk.append(x)
        g = Game(ev, tip, "2025-26", mk[0], mk[1])
        # ticker 'KXNBAGAME-26JUN13NYKSAS' -> away NYK, home SAS (codes are AWAYHOME)
        tail = ev.split("-", 1)[1][7:]
        away_c, home_c = tail[:3], tail[3:]
        if {x.code for x in mk} != {away_c, home_c}:
            flags["code_mismatch"].append(ev)
        tk_home = next((i for i, x in enumerate(mk) if x.code == home_c), None)
        title = re.sub(r"^Game \d+:\s*", "", ev_title.get(ev) or "")
        title = re.sub(r"\s+Winner\??$", "", title)
        ti_home = None
        if " at " in title:
            hname = title.split(" at ", 1)[1].strip().lower()
            cand = [i for i, x in enumerate(mk) if (x.name or "").strip().lower() == hname]
            ti_home = cand[0] if len(cand) == 1 else None
        if ti_home is None:
            flags["title_unresolved"].append((ev, title))
        if ti_home is not None and tk_home is not None and ti_home != tk_home:
            flags["title_ticker_home_mismatch"].append((ev, title))
        g.home_idx = ti_home if ti_home is not None else tk_home
        games.append(g)
    return games, excl, flags


# ----------------------------------------------------------------------------------------------- NFL loader
def load_nfl():
    conn = sqlite3.connect(ROOT / "data" / "nfl_spread.sqlite")
    rows = conn.execute("SELECT mid,ticker,event_ticker,game_id,season,week,team,result,raw_json FROM sp_markets "
                        "WHERE series='KXNFLGAME' AND kind='win'").fetchall()
    by_game = defaultdict(list)
    for r in rows:
        by_game[r[2]].append(r)
    q = defaultdict(list)
    for mid_, ts, b, a in conn.execute("SELECT q.mid,q.ts,q.bid_e4,q.ask_e4 FROM sp_quotes q JOIN sp_markets m ON m.mid=q.mid "
                                       "WHERE m.series='KXNFLGAME' ORDER BY q.mid,q.ts"):
        q[mid_].append((ts, None if b is None else b / 1e4, None if a is None else a / 1e4))
    games, excl, flags = [], Counter(), {"home_mismatch_ticker_vs_gameid": [], "tip_vs_kickoff_col_diff": 0}
    try:
        from kpm.core.teams import norm_code
    except Exception:  # noqa: BLE001
        norm_code = None
    for ev, ms in sorted(by_game.items()):
        if len(ms) != 2:
            excl["event without exactly 2 markets"] += 1
            continue
        if sorted(m[7] or "" for m in ms) != ["no", "yes"]:
            excl[f"no clean result {[m[7] for m in ms]}"] += 1
            continue
        exps = [datetime.fromisoformat(json.loads(m[8])["expected_expiration_time"].replace("Z", "+00:00")).timestamp()
                for m in ms]
        tip = int(min(exps)) - 3 * 3600
        mk = []
        for m in ms:
            x = Mkt(m[6], m[6], 1.0 if m[7] == "yes" else 0.0)
            z = q[m[0]]
            if z:
                x.h_ts, x.h_bid, x.h_ask = [list(c) for c in zip(*z)]
                x.m_ts, x.m_bid, x.m_ask = x.h_ts, x.h_bid, x.h_ask
            mk.append(x)
        g = Game(ev, tip, ms[0][4], mk[0], mk[1])
        parts = ms[0][3].split("_")          # nflverse game_id season_week_AWAY_HOME
        away, home = parts[2], parts[3]
        codes = [x.code for x in mk]
        if set(codes) == {away, home}:
            g.home_idx = codes.index(home)
        else:
            excl["team codes differ from game_id (home/away unknown)"] += 1
        tail = ev.split("-", 1)[1][7:]
        if norm_code is not None:
            ok = any(norm_code(tail[:i]) == away and norm_code(tail[i:]) == home for i in (2, 3, 4))
            if not ok:
                flags["home_mismatch_ticker_vs_gameid"].append((ev, ms[0][3]))
        games.append(g)
    return games, excl, flags


# ----------------------------------------------------------------------------------------------- evaluation
def compute_elo(games, sport):
    """Pre-game Elo per game using only earlier results. Returns {gid: (r_home, r_away, warm)}."""
    ratings, out = {}, {}
    seen, first_tip, cur = Counter(), {}, None
    for g in games:
        if g.home_idx is None:
            continue
        if g.season != cur:
            cur = g.season
            ratings = {}
            first_tip[cur] = g.tip
        h, a = g.mk[g.home_idx], g.mk[1 - g.home_idx]
        rh, ra = ratings.get(h.code, ELO_START), ratings.get(a.code, ELO_START)
        warm = seen[cur] < 150 if sport == "NBA" else (g.tip - first_tip[cur] < 14 * 86400)
        out[g.gid] = (rh, ra, warm)
        ratings[h.code], ratings[a.code] = elo_update(rh, ra, h.result == 1.0)
        seen[cur] += 1
    return out


def evaluate(games, E_s):
    """Per game at entry offset E_s: (game, [(entry, close, hist_mid) per team]) or an exclusion reason."""
    rows, ex = [], Counter()
    for g in games:
        e_t, c_t = g.tip - E_s, g.tip - 300
        info, bad = [], None
        for x in g.mk:
            ent = at(x.h_ts, x.h_bid, x.h_ask, e_t)
            clo = at(x.m_ts, x.m_bid, x.m_ask, c_t)
            if ent is None:
                bad = bad or "no entry candle"
            elif not valid_quote(ent[0], ent[1]):
                bad = bad or "no valid bid/ask at entry"
            if clo is None:
                bad = bad or "no close candle"
            elif not valid_quote(clo[0], clo[1]):
                bad = bad or "no valid bid/ask at close"
            hist = at(x.h_ts, x.h_bid, x.h_ask, e_t - 24 * 3600)
            hist_mid = mid(hist[0], hist[1]) if hist and valid_quote(hist[0], hist[1]) else None
            info.append((ent, clo, hist_mid))
        if bad:
            ex[bad] += 1
            continue
        rows.append((g, info))
    return rows, ex


def pick(strategy, g, info, elo):
    """Index (0/1) of the team to buy, or None."""
    em = [mid(i[0][0], i[0][1]) for i in info]
    if strategy == "V1 favourite":
        i = int(em[1] > em[0])
        return i if em[i] > 0.5 else None
    if strategy == "V2 underdog":
        i = int(em[1] < em[0])
        return i if em[i] < 0.5 else None
    if strategy == "V3 home":
        return g.home_idx
    if strategy == "V4 away":
        return None if g.home_idx is None else 1 - g.home_idx
    if strategy in ("V5 momentum", "V6 reversal"):
        if info[0][2] is None or info[1][2] is None:
            return None
        ch = [em[0] - info[0][2], em[1] - info[1][2]]
        if strategy == "V5 momentum":
            i = int(ch[1] > ch[0])
            return i if ch[i] > 0 else None
        i = int(ch[1] < ch[0])
        return i if ch[i] < 0 else None
    if strategy == "V7 elo edge":
        e = elo.get(g.gid)
        if e is None or e[2] or g.home_idx is None:
            return None
        ph = elo_prob_home(e[0], e[1])
        p = [0.0, 0.0]
        p[g.home_idx], p[1 - g.home_idx] = ph, 1 - ph
        edge = [p[0] - em[0], p[1] - em[1]]
        i = int(edge[1] > edge[0])
        return i if edge[i] >= ELO_EDGE - 1e-12 else None
    raise ValueError(strategy)


def trade_vals(g, info, i):
    ent, clo, _ = info[i]
    ask, cmid, cbid = ent[1], mid(clo[0], clo[1]), clo[0]
    return {"clv": clv_net(cmid, ask), "settle": settle_pnl(g.mk[i].result, ask), "exit": exit_pnl(cbid, ask)}


def weeks_span(tips):
    return max((max(tips) - min(tips)) / (7 * 86400.0), 1e-9) if tips else float("nan")


def calib(p, y, nb=10):
    p, y = np.array(p), np.array(y)
    if len(p) == 0:
        return {}
    b = np.minimum((p * nb).astype(int), nb - 1)
    rows = []
    for k in range(nb):
        m = b == k
        if m.sum():
            rows.append({"bucket": f"{k / nb:.1f}-{(k + 1) / nb:.1f}", "n": int(m.sum()), "mean_mid": float(p[m].mean()),
                         "win_rate": float(y[m].mean())})
    return {"n": int(len(p)), "brier": float(np.mean((p - y) ** 2)), "buckets": rows}


def run_sport(sport, games):
    games = sorted(games, key=lambda g: (g.tip, g.gid))
    n = len(games)
    k = split_index(n)
    disc_ids = {g.gid for g in games[:k]}
    unseen_games = games[k:]
    elo = compute_elo(games, sport)
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()  # noqa: E731
    unseen_weeks = unseen_weeks_cal = weeks_span([g.tip for g in unseen_games])
    res = {"n_games": n, "discovery_games": k, "unseen_games": n - k, "split_tip_utc": iso(games[k].tip),
           "first_tip_utc": iso(games[0].tip), "last_tip_utc": iso(games[-1].tip), "unseen_weeks": unseen_weeks_cal,
           "versions": {}, "exclusions": {}, "descr": {}, "calibration": {}}
    res["unseen_active_weeks"] = len({datetime.fromtimestamp(g.tip, timezone.utc).isocalendar()[:2] for g in unseen_games})
    unseen_weeks = res["unseen_active_weeks"]  # amendment 2026-10-20: weeks with at least one game
    rng_cf = np.random.default_rng(SEED)
    for ename, E_s in ENTRIES.items():
        rows, ex = evaluate(games, E_s)
        res["exclusions"][ename] = {"kept_games": len(rows), "excluded": dict(ex)}
        drift, spreads, ages, ent_mid, ent_res, flip, nfav = [], [], [], [], [], 0, 0
        for g, info in rows:
            for j, (ent, clo, _) in enumerate(info):
                em, cm = mid(ent[0], ent[1]), mid(clo[0], clo[1])
                drift.append(abs(cm - em))
                spreads.append(ent[1] - ent[0])
                ages.append(ent[3])
                ent_mid.append(em)
                ent_res.append(g.mk[j].result)
            em = [mid(i[0][0], i[0][1]) for i in info]
            cm = [mid(i[1][0], i[1][1]) for i in info]
            if em[0] != em[1]:
                nfav += 1
                fav = int(em[1] > em[0])
                flip += int(cm[fav] > 0.5)
        res["descr"][ename] = {
            "team_markets": len(drift), "mean_abs_drift_entry_to_close": float(np.mean(drift)) if drift else None,
            "median_abs_drift": float(np.median(drift)) if drift else None,
            "mean_entry_spread": float(np.mean(spreads)) if spreads else None,
            "median_entry_candle_age_s": float(np.median(ages)) if ages else None,
            "p95_entry_candle_age_s": float(np.percentile(ages, 95)) if ages else None,
            "v1_fav_still_fav_at_close": (flip / nfav) if nfav else None, "v1_fav_games": nfav}
        res["calibration"][ename + " entry mid"] = calib(ent_mid, ent_res)
        if ename == "T-6h":
            cm_all, r_all, cspread = [], [], []
            for g, info in rows:
                for j, (ent, clo, _) in enumerate(info):
                    cm_all.append(mid(clo[0], clo[1]))
                    r_all.append(g.mk[j].result)
                    cspread.append(clo[1] - clo[0])
            res["calibration"]["close mid (T-5m; games valid at T-6h)"] = calib(cm_all, r_all)
            res["descr"]["close_mean_spread"] = float(np.mean(cspread)) if cspread else None
        for s in STRATS:
            tr = []
            for g, info in rows:
                i = pick(s, g, info, elo)
                if i is not None:
                    tr.append((g, info, i, trade_vals(g, info, i)))
            dis = np.array([t[3]["clv"] for t in tr if t[0].gid in disc_ids])
            uns = [t for t in tr if t[0].gid not in disc_ids]
            un = np.array([t[3]["clv"] for t in uns])
            ust = np.array([t[3]["settle"] for t in uns])
            uex = np.array([t[3]["exit"] for t in uns])
            lo, hi = bootstrap_ci(un)
            v = {"n_total": len(tr), "n_disc": int(len(dis)), "n_unseen": int(len(un)),
                 "disc_mean_clv": float(dis.mean()) if len(dis) else None,
                 "unseen_mean_clv": float(un.mean()) if len(un) else None, "unseen_ci_lo": lo, "unseen_ci_hi": hi,
                 "unseen_mean_settle": float(ust.mean()) if len(ust) else None,
                 "unseen_mean_exit": float(uex.mean()) if len(uex) else None,
                 "all_mean_clv": float(np.mean([t[3]["clv"] for t in tr])) if tr else None,
                 "unseen_trades_per_week": len(un) / unseen_weeks,
                 "unseen_usd_per_week": float(un.mean() * SIZE * len(un) / unseen_weeks) if len(un) else None,
                 "unseen_usd_per_active_week": float(un.mean() * SIZE * len(un) / res["unseen_active_weeks"]) if len(un) else None}
            if len(un):  # coin flip on the same unseen games (computed for all; the pass bar names only the primary)
                both = np.array([[trade_vals(g, info, 0)["clv"], trade_vals(g, info, 1)["clv"]] for g, info, _, _ in uns])
                pr = rng_cf.integers(0, 2, size=(500, len(both)))
                cf = both[np.arange(len(both))[None, :], pr].mean(axis=1)
                v["coinflip_share_ge"] = float((cf >= un.mean()).mean())
                v["coinflip_mean"] = float(cf.mean())
            res["versions"][f"{s} @ {ename}"] = v
    return res


def verdict(v):
    f = lambda x, d=4: "n/a" if x is None else f"{x:+.{d}f}"  # noqa: E731
    return [
        ("1 mean CLV net > 0 in discovery and unseen", (v["disc_mean_clv"] or -1) > 0 and (v["unseen_mean_clv"] or -1) > 0,
         f"discovery {f(v['disc_mean_clv'])}, unseen {f(v['unseen_mean_clv'])}"),
        ("2 unseen 95% bootstrap lower bound > 0", v["unseen_ci_lo"] > 0, f"CI [{f(v['unseen_ci_lo'])}, {f(v['unseen_ci_hi'])}]"),
        ("3 at least 200 unseen trades", v["n_unseen"] >= 200, f"n_unseen {v['n_unseen']}"),
        ("4 coin flip: <= 5% of 500 random-side copies do as well", v.get("coinflip_share_ge", 1.0) <= 0.05,
         f"share {v.get('coinflip_share_ge', float('nan')):.3f}"),
        ("5 unseen mean hold-to-settlement P&L >= 0", (v["unseen_mean_settle"] if v["unseen_mean_settle"] is not None else -1) >= 0,
         f(v["unseen_mean_settle"])),
        ("6 >= $2/week at 10 contracts (unseen)", (v["unseen_usd_per_week"] or -1) >= 2, f"${(v['unseen_usd_per_week'] or 0):.2f}/wk"),
    ]


def fmt(x, d=4):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.{d}f}"


def table(res):
    L = ["| version | n | disc mean | unseen n | unseen mean | unseen CI | coin-flip share | settle P&L (unseen) | $/wk |",
         "|---|---|---|---|---|---|---|---|---|"]
    for name, v in res["versions"].items():
        cf = f"{v['coinflip_share_ge']:.3f}" if v.get("coinflip_share_ge") is not None else "n/a"
        ci = f"[{fmt(v['unseen_ci_lo'])}, {fmt(v['unseen_ci_hi'])}]" if v["n_unseen"] else "n/a"
        L.append(f"| {name} | {v['n_total']} | {fmt(v['disc_mean_clv'])} | {v['n_unseen']} | {fmt(v['unseen_mean_clv'])} | {ci} | "
                 f"{cf} | {fmt(v['unseen_mean_settle'])} | {fmt(v['unseen_usd_per_week'], 2)} |")
    return "\n".join(L)


def main(only=None):
    out, md = {}, ["# Research log #24 results: pre-game drift (NBA primary, NFL secondary)\n",
                   "Generated by drift.py per PREREGISTRATION.md. Interpretation choices:\n"]
    md += [f"- {s}" for s in INTERPRETATION]
    for sport, loader in (("NBA", load_nba), ("NFL", load_nfl)):
        if only and sport != only:
            continue
        games, excl, flags = loader()
        res = run_sport(sport, games)
        res["loader_exclusions"] = dict(excl)
        res["flags"] = {k: (dict(v) if isinstance(v, Counter) else v) for k, v in flags.items()}
        out[sport] = res
        md.append(f"\n## {sport}\n")
        md.append(f"games {res['n_games']} (discovery {res['discovery_games']}, unseen {res['unseen_games']}), "
                  f"{res['first_tip_utc']} .. {res['last_tip_utc']}, split at {res['split_tip_utc']}, "
                  f"unseen weeks with at least one game: {res['unseen_active_weeks']} (calendar span {res['unseen_weeks']:.1f}; $/wk uses weeks with games per the 2026-10-20 amendment).\n")
        md.append(f"loader exclusions: {dict(excl)}; flags: " +
                  json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in res["flags"].items()}, default=str) + "\n")
        for e, d in res["exclusions"].items():
            md.append(f"- {e}: kept {d['kept_games']} games, excluded {d['excluded']}")
        md.append("\n### Descriptives by entry time\n")
        md.append("| E | team markets | mean abs drift (E to close) | mean entry spread | V1 fav still fav at close | median entry candle age s |")
        md.append("|---|---|---|---|---|---|")
        for e in ENTRIES:
            d = res["descr"][e]
            md.append(f"| {e} | {d['team_markets']} | {d['mean_abs_drift_entry_to_close']:.4f} | {d['mean_entry_spread']:.4f} | "
                      f"{d['v1_fav_still_fav_at_close']:.3f} (n {d['v1_fav_games']}) | {d['median_entry_candle_age_s']:.0f} |")
        md.append(f"\nclose mean spread (T-6h sample): {res['descr']['close_mean_spread']:.4f}")
        md.append("\n### Calibration\n")
        for k, c in res["calibration"].items():
            md.append(f"**{k}**: n {c['n']}, Brier {c['brier']:.4f}\n")
            md.append("| bucket | n | mean mid | win rate |\n|---|---|---|---|")
            md += [f"| {b['bucket']} | {b['n']} | {b['mean_mid']:.3f} | {b['win_rate']:.3f} |" for b in c["buckets"]]
            md.append("")
        md.append("### All 21 versions\n")
        md.append(table(res))
        pv = res["versions"]["V1 favourite @ T-6h"]
        rules = verdict(pv)
        allpass = all(r[1] for r in rules)
        res["primary_spec"] = {"pass": allpass, "rules": [(a, bool(b), c) for a, b, c in rules]}
        label = "PRIMARY VERDICT (NBA, V1 favourite, T-6h, CLV net)" if sport == "NBA" else "Same test on NFL (secondary; separate verdict)"
        md.append(f"\n### {label}: {'PASS' if allpass else 'FAIL'}\n")
        md += [f"- {'PASS' if b else 'FAIL'}  rule {a}: {c}" for a, b, c in rules]
        md.append(f"- (exit-at-close mean unseen {fmt(pv['unseen_mean_exit'])}; all-games mean CLV {fmt(pv['all_mean_clv'])})")
        leads = [k for k, v in res["versions"].items()
                 if v["n_unseen"] >= 200 and v["unseen_ci_lo"] > 0 and (v["disc_mean_clv"] or -1) > 0]
        md.append(f"\nVersions with disc>0, unseen CI lower bound>0 and n>=200 (leads only, never passes): {leads}")
        print(f"\n== {sport} == {label}: {'PASS' if allpass else 'FAIL'}")
        for a, b, c in rules:
            print("  ", "PASS" if b else "FAIL", a, "|", c)
        print(table(res))
    (OUT / f"results{'_' + only if only else ''}.json").write_text(json.dumps(out, indent=1, default=str))
    (OUT / f"README{'_' + only if only else ''}.md").write_text("\n".join(md) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    main(sys.argv[1] if len(sys.argv) > 1 else None)
