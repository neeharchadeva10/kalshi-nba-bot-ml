"""#49 pipeline: one split -> per-game records -> 16 versions -> selection -> luck test.  run_discovery.py and run_unseen.py
both use build_games()/version_table(); only the discovery run fits k and selects.  Streams per game (SQLite point lookups)."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter

import numpy as np

import data_io as D
import mismatch as MM

HERE = MM.HERE


def _median_of(quotes):
    return MM.implied_median([(s, b, a) for _, s, b, a, _ in quotes])


def _spread_stats(quotes):
    sp = [a - b for _, _, b, a, _ in quotes]
    return (float(np.mean(sp)) if sp else None)


def build_games(split, k=None, with_outcomes=True):
    """Returns (games, skips).  Each game dict holds medians, D values, books' main strikes and outcomes.
    `k` is needed for D_1H; when None, D_1H is left undefined (the discovery run fits k afterwards and calls finish_1h)."""
    mk = sqlite3.connect(HERE / "markets.sqlite")
    tc = D.totals_conn()
    cdb = D.candle_db()
    skips = Counter()
    games = []
    for ev in D.load_events(split):
        T = int(ev["T"])
        suf = MM.event_suffix(ev["event"])
        g = {"event": ev["event"], "date": ev["game_date_et"], "stype": ev["season_type"], "T": T, "home": ev["home"], "away": ev["away"]}
        gq = D.game_total_quotes(tc, ev["event"], T)
        g["Mg"] = _median_of(gq)
        if g["Mg"] is None:
            skips["no_game_total_median"] += 1
            continue
        g["game_main"] = MM.main_strike([(t, s, b, a) for t, s, b, a, _ in gq])
        g["game_spread"] = _spread_stats(gq)
        m = D.markets_for(mk, ev["event"])
        if not m["team"] and not m["1H"]:
            skips["no_team_or_1H_markets_listed"] += 1
            continue
        # ---- team books
        tq = D.book_quotes(cdb, [(t, s) for t, _, s in m["team"]], T)
        team_of = {t: tm for t, tm, _ in m["team"]}
        qs = {"home": [q for q in tq if team_of[q[0]] == ev["home"]], "away": [q for q in tq if team_of[q[0]] == ev["away"]]}
        g["Mh"], g["Ma"] = _median_of(qs["home"]), _median_of(qs["away"])
        g["team_main"] = {side: MM.main_strike([(t, s, b, a) for t, s, b, a, _ in q]) for side, q in qs.items()}
        g["team_spread"] = _spread_stats(tq)
        g["D_team"] = (MM.d_team(g["Mh"], g["Ma"], g["Mg"]) if g["Mh"] is not None and g["Ma"] is not None else None)
        if g["D_team"] is None:
            skips["team_median_missing"] += 1
        # ---- 1H book
        hq = D.book_quotes(cdb, m["1H"], T)
        g["M1H"] = _median_of(hq)
        g["half_main"] = MM.main_strike([(t, s, b, a) for t, s, b, a, _ in hq])
        g["half_spread"] = _spread_stats(hq)
        if g["M1H"] is None:
            skips["1H_median_missing"] += 1
        g["D_1H"] = None
        if with_outcomes:
            g["res"] = {}
            for t, *_ in ([g["game_main"]] if g["game_main"] else []):
                g["res"][t] = tc.execute("SELECT result FROM markets WHERE ticker=?", (t,)).fetchone()[0]
            for side in ("home", "away"):
                mm = g["team_main"][side]
                if mm:
                    g["res"][mm[0]] = mk.execute("SELECT result FROM markets WHERE ticker=?", (mm[0],)).fetchone()[0]
            if g["half_main"]:
                g["res"][g["half_main"][0]] = mk.execute("SELECT result FROM markets WHERE ticker=?",
                                                         (g["half_main"][0],)).fetchone()[0]
            # actual totals
            ag = tc.execute("SELECT expiration_value FROM markets WHERE event_ticker=? LIMIT 1", (ev["event"],)).fetchone()
            g["A_game"] = float(ag[0]) if ag and ag[0] not in (None, "") else None
            te = mk.execute("SELECT expiration_value FROM markets WHERE event_ticker=? LIMIT 1",
                            ("KXNBATEAMTOTAL-" + suf,)).fetchone()
            pt = MM.parse_team_expiration(te[0]) if te else None
            g["A_home"], g["A_away"] = (pt if pt else (None, None))
            he = mk.execute("SELECT expiration_value FROM markets WHERE event_ticker=? LIMIT 1",
                            ("KXNBA1HTOTAL-" + suf,)).fetchone()
            try:
                g["A_1H"] = float(he[0]) if he and he[0] not in (None, "") else None
            except ValueError:
                g["A_1H"] = None
        games.append(g)
    return games, dict(skips)


def set_d1h(games, k):
    for g in games:
        g["D_1H"] = MM.d_1h(g["M1H"], g["Mg"], k) if g.get("M1H") is not None else None


def legs_for(g, pair, direction):
    """Returns (list of (ticker, bid, ask)) for the bet markets, or None when a main strike is missing."""
    if pair == "team" and direction == "other_follows":
        h, a = g["team_main"]["home"], g["team_main"]["away"]
        if h is None or a is None:
            return None
        return [(h[0], h[2], h[3]), (a[0], a[2], a[3])]
    mm = g["game_main"] if direction == "game_follows" else g["half_main"]
    if mm is None:
        return None
    return [(mm[0], mm[2], mm[3])]


def game_bets(g, pair, direction):
    """Per-contract net if the bet side is YES / NO, for this game and (pair, direction) -- independent of threshold.
    Returns (D, side_real, net_real, net_yes, net_no, n_legs) or None if not betable (counted by caller)."""
    D_ = g["D_team"] if pair == "team" else g["D_1H"]
    if D_ is None:
        return None
    legs = legs_for(g, pair, direction)
    if legs is None:
        return None
    yes = []
    for t, b, a in legs:
        r = g["res"].get(t)
        if r not in ("yes", "no"):
            return None
        yes.append(r == "yes")
    out = {}
    for side in ("yes", "no"):
        tot, n = MM.bet_net([(side, b, a) for _, b, a in legs], yes)
        out[side] = tot / n
    sf = MM.side_for(direction, D_)
    if sf is None:
        return None
    real = out[sf[1]]
    return D_, sf[1], real, out["yes"], out["no"], len(legs)


def version_table(games):
    """Real-side nets per version + matrices for the luck test.  Returns (stats, vlist, arrays, skipcounts)."""
    vlist = MM.grid()
    G = len(games)
    ny, nn = np.zeros((G, len(vlist))), np.zeros((G, len(vlist)))
    incl = np.zeros((G, len(vlist)), bool)
    real = np.zeros((G, len(vlist)))
    skipc = Counter()
    cache = {}
    for i, g in enumerate(games):
        for (pair, direction) in {(v[0], v[1]) for v in vlist}:
            cache[(i, pair, direction)] = game_bets(g, pair, direction)
    stats = {}
    for j, v in enumerate(vlist):
        pair, direction, th = v
        for i, g in enumerate(games):
            b = cache[(i, pair, direction)]
            D_ = g["D_team"] if pair == "team" else g["D_1H"]
            if D_ is None:
                continue
            if abs(D_) < th - MM.EPS:
                continue
            if b is None:
                skipc[f"{MM.vname(v)}:no_bet(main strike missing / unsettled)"] += 1
                continue
            incl[i, j] = True
            real[i, j] = b[2]
            ny[i, j], nn[i, j] = b[3], b[4]
        x = real[incl[:, j], j]
        n = len(x)
        stats[v] = {"n": n, "mean": float(x.mean()) if n else None, "sd": float(x.std(ddof=1)) if n > 1 else None,
                    "lb": MM.lb(x) if n > 1 else float("nan"),
                    "win_rate": float((x > 0).mean()) if n else None,
                    "total_net": float(x.sum()) if n else 0.0}
    return stats, vlist, {"ny": ny, "nn": nn, "incl": incl, "real": real}, dict(skipc)


def closer_stats(games, k):
    """Which market ended closer to the final (discovery-only report).  Team: |Mg-A| vs |Mh+Ma-A|.  1H: |M1H-A1H| vs |k*Mg-A1H|."""
    out = {}
    t = [(abs(g["Mg"] - g["A_game"]), abs(g["Mh"] + g["Ma"] - g["A_game"])) for g in games
         if g.get("A_game") is not None and g["Mh"] is not None and g["Ma"] is not None]
    h = [(abs(g["M1H"] - g["A_1H"]), abs(k * g["Mg"] - g["A_1H"])) for g in games
         if k is not None and g.get("A_1H") is not None and g["M1H"] is not None]
    for name, arr, a_lab, b_lab in (("team", t, "game_total", "team_sum"), ("1H", h, "1H_market", "k_x_game_total")):
        n = len(arr)
        if n == 0:
            out[name] = {"n": 0}
            continue
        a = np.array(arr)
        out[name] = {"n": n, f"{a_lab}_closer": int((a[:, 0] < a[:, 1]).sum()), f"{b_lab}_closer": int((a[:, 1] < a[:, 0]).sum()),
                     "ties": int((a[:, 0] == a[:, 1]).sum()), f"mae_{a_lab}": float(a[:, 0].mean()),
                     f"mae_{b_lab}": float(a[:, 1].mean())}
    return out


def dist(x):
    x = np.array([v for v in x if v is not None], float)
    if len(x) == 0:
        return {"n": 0}
    q = np.quantile(x, [0.05, 0.25, 0.5, 0.75, 0.95])
    return {"n": int(len(x)), "mean": float(x.mean()), "sd": float(x.std(ddof=1)) if len(x) > 1 else None,
            "q05_25_50_75_95": [float(v) for v in q], "abs_ge": {str(th): int((np.abs(x) >= th).sum()) for th in MM.THRESHOLDS}}


def spread_report(games):
    def med(key):
        v = [g[key] for g in games if g.get(key) is not None]
        return {"n": len(v), "median_of_game_mean_spread": float(np.median(v)) if v else None}
    return {"game_total": med("game_spread"), "team_total": med("team_spread"), "first_half": med("half_spread")}


def dump(path, obj):
    path.write_text(json.dumps(obj, indent=2, default=lambda o: None if o != o else str(o)))
