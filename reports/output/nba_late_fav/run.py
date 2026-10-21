"""Research log #35 backtest: buy the NBA favourite late in Q4 (reports/output/nba_late_fav/PREREGISTRATION.md).

  python run.py smoke   # first 20 discovery games: per-game t_X, E_X, candle used, favourite, ask.  NO P&L, NO win rates, no results read.
  python run.py final   # full run -> results.json, trades.csv, README.md  (only after the audit)
"""
from __future__ import annotations

import csv
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lib  # noqa: E402
from lib import BANDS, COUNT, SEED, XS  # noqa: E402

DB = HERE / "nlf.sqlite"
sys.path.insert(0, str(HERE.parents[3] / "src"))
from kpm.core.timeutil import ET  # noqa: E402


def load_prints(conn):
    out = defaultdict(list)
    for t, ts, p, c, side in conn.execute("SELECT ticker, ts, yes_price, count, taker_side FROM prints"):
        out[t].append((ts, p, c, side))
    return out


def vname(X, band):
    return f"X{X // 60}m_{band}"


def split_index(n_games: int) -> int:
    """Discovery = the first half (ordered by scheduled start)."""
    return n_games // 2


def price_bucket(p):
    lo = math.floor(p * 100 / 5 + 1e-9) * 5
    return f"{lo}-{lo + 4}c"


def margin_bucket(m):
    return "0-5" if m <= 5 else "6-10" if m <= 10 else "11-15" if m <= 15 else "16-20" if m <= 20 else "21+"


def week_key(ts):
    d = datetime.fromtimestamp(ts, ET).date()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


# ------------------------------------------------------------------------------------------------ per-version trades
def run_version(games, series, prints, X, band_name):
    lo, hi = BANDS[band_name]
    n_disc = split_index(len(games))
    trades, skips = [], Counter()
    for gi, (et, ev, pg) in enumerate(games):
        r = lib.decision(ev, series, pg, X, (lo, hi))
        if r["status"] == "skip":
            skips[r["reason"]] += 1
            continue
        team = r["team"]
        res = ev["result"].get(team)
        if res not in ("yes", "no"):
            skips["no_result"] += 1
            continue
        win = res == "yes"
        ask = r["ask"]
        ticker = ev["markets"][team]
        conf, qty = lib.confirmed_fill(prints.get(ticker, []), r["E"], ask)
        away_k, home_k = lib.parse_event_ticker(et)[1:]
        is_home = team == home_k
        hm = pg.margin[X]
        fav_margin = (hm if is_home else -hm) if hm is not None else None
        trades.append(dict(
            version=vname(X, band_name), gi=gi, split="discovery" if gi < n_disc else "unseen", event=et, tip=ev["tip"],
            week=week_key(ev["tip"]), team=team, home=int(is_home), t_x=int(r["tX"]), e_x=int(r["E"]), ask=ask,
            bid=r["bid"], spread=round(ask - r["bid"], 4), win=int(win), fee=lib.fee(ask), net_pc=lib.net_per_contract(ask, win),
            net_usd=COUNT * lib.net_per_contract(ask, win), confirmed=int(conf), print_qty=qty, fav_margin=fav_margin,
            season_type=pg.season_type, ot=int(pg.max_period > 4)))
    return trades, skips


# ------------------------------------------------------------------------------------------------ coin flips
def game_minutes(ev, series, pg):
    """Whole-minute grid over the game (first pbp event .. last event + 60 s).  Per minute: period, ask per team (carried-forward
    quote, valid if the candle ended within 10 min), and net per contract if bought at that ask."""
    teams = list(ev["markets"])
    out = []
    start = int(-(-pg.first_wall // 60) * 60)
    for ts in range(start, int(pg.last_wall + 60) + 1, 60):
        per = lib.period_at(pg, ts)
        if per is None:
            continue
        asks, nets = {}, {}
        for t in teams:
            s_ = series.get(ev["markets"][t])
            q = s_.latest(ts) if s_ is not None and len(s_.ts) else None
            a = q[2] if q else float("nan")
            asks[t] = a
            res = ev["result"].get(t)
            nets[t] = lib.net_per_contract(a, res == "yes") if (a == a and res in ("yes", "no")) else float("nan")
        out.append(dict(ts=ts, period=per, asks=asks, nets=nets))
    return out


def coin_stats(trades, games, series, band_name, minutes_cache):
    """For one version and split: matched (periods 1-3, +-1c) and plain (any in-game minute, ask in band) coin flips."""
    lo, hi = BANDS[band_name]
    res = {}
    for split in ("discovery", "unseen"):
        tr = [t for t in trades if t["split"] == split]
        if not tr:
            res[split] = dict(n_trades=0)
            continue
        real = float(np.mean([t["net_pc"] for t in tr]))
        g_matched, g_plain = [], []
        for t in tr:
            et, ev, pg = games[t["gi"]]
            if et not in minutes_cache:
                minutes_cache[et] = game_minutes(ev, series, pg)
            mins = minutes_cache[et]
            cm = defaultdict(list)
            for i, team in lib.coin_candidates_matched(mins, t["ask"], 0.01):
                cm[i].append(mins[i]["nets"][team])
            g_matched.append([v for k, v in sorted(cm.items())])
            pl = defaultdict(list)
            for i, m in enumerate(mins):
                for team, a in m["asks"].items():
                    if a == a and lo - lib.EPS <= a <= hi + lib.EPS and m["nets"][team] == m["nets"][team]:
                        pl[i].append(m["nets"][team])
            g_plain.append([v for k, v in sorted(pl.items())])
        out = dict(n_trades=len(tr), real_mean_all=real)
        for name, groups in (("matched_p1to3", g_matched), ("plain_random", g_plain)):
            means, n_used = lib.coin_flip(groups, 500, SEED)
            used = [t for t, g in zip(tr, groups) if g]
            real_used = float(np.mean([t["net_pc"] for t in used])) if used else float("nan")
            out[name] = dict(n_trades_with_candidates=n_used, n_trades_dropped=len(tr) - n_used,
                             real_mean_same_trades=real_used,
                             copy_mean_avg=float(means.mean()) if len(means) else None,
                             copy_mean_p5_p95=[float(np.percentile(means, 5)), float(np.percentile(means, 95))] if len(means) else None,
                             frac_copies_ge_real_all=float((means >= real).mean()) if len(means) else None,
                             frac_copies_ge_real_same_trades=float((means >= real_used).mean()) if len(means) else None)
        res[split] = out
    return res


# ------------------------------------------------------------------------------------------------ summaries
def summarize(tr):
    if not tr:
        return dict(n=0)
    v = np.array([t["net_pc"] for t in tr])
    ordered = sorted(tr, key=lambda t: t["tip"])
    streak = best = 0
    for t in ordered:
        streak = streak + 1 if t["win"] == 0 else 0
        best = max(best, streak)
    return dict(n=len(tr), win_rate=float(np.mean([t["win"] for t in tr])), mean_net_per_contract=float(v.mean()),
                total_usd_10c=float(sum(t["net_usd"] for t in tr)), mean_ask=float(np.mean([t["ask"] for t in tr])),
                mean_spread=float(np.mean([t["spread"] for t in tr])), median_spread=float(np.median([t["spread"] for t in tr])),
                worst_losing_streak=best)


def breakdowns(tr):
    keys = dict(
        margin=lambda t: margin_bucket(t["fav_margin"]) if t["fav_margin"] is not None else "na",
        price=lambda t: price_bucket(t["ask"]),
        home_away=lambda t: "home" if t["home"] else "away",
        season=lambda t: {2: "regular", 3: "playoffs", 5: "play-in"}.get(t["season_type"], str(t["season_type"])),
        overtime=lambda t: "OT" if t["ot"] else "regulation")
    out = {}
    for name, fn in keys.items():
        g = defaultdict(list)
        for t in tr:
            g[fn(t)].append(t)
        out[name] = {k: summarize(v) for k, v in sorted(g.items())}
    return out


def weeks_with_games(games, lo_gi):
    return len({week_key(ev["tip"]) for _, ev, _ in games[lo_gi:]})


def evaluate(games, series, prints, versions=None):
    """Full evaluation.  games sorted by scheduled start."""
    n_disc = split_index(len(games))
    unseen_weeks = weeks_with_games(games, n_disc)
    minutes_cache: dict = {}
    results, all_trades = {}, []
    for X in XS:
        for band_name in BANDS:
            if versions and (X, band_name) not in versions:
                continue
            tr, skips = run_version(games, series, prints, X, band_name)
            all_trades += tr
            disc = [t for t in tr if t["split"] == "discovery"]
            uns = [t for t in tr if t["split"] == "unseen"]
            b025, b05 = lib.boot_lower([t["net_pc"] for t in uns])
            conf_u = [t for t in uns if t["confirmed"]]
            conf_d = [t for t in disc if t["confirmed"]]
            r = dict(
                n_games=len(games), n_discovery_games=n_disc, n_unseen_games=len(games) - n_disc,
                skips=dict(skips), skip_share={k: v / len(games) for k, v in skips.items()},
                all=summarize(tr), discovery=summarize(disc), unseen=summarize(uns),
                unseen_bootstrap_lower_2_5=b025, unseen_bootstrap_lower_5=b05,
                unseen_weeks_with_games=unseen_weeks,
                unseen_usd_per_week=(sum(t["net_usd"] for t in uns) / unseen_weeks) if unseen_weeks else None,
                confirmed_unseen=summarize(conf_u), confirmed_discovery=summarize(conf_d),
                unconfirmed_unseen=summarize([t for t in uns if not t["confirmed"]]),
                breakdowns_all=breakdowns(tr), breakdowns_unseen=breakdowns(uns), breakdowns_discovery=breakdowns(disc),
                coin_flips=coin_stats(tr, games, series, band_name, minutes_cache))
            results[vname(X, band_name)] = r
    return results, all_trades


def calibration(games, series):
    """Late-game win rate vs ask bucket (every team's ask at its E_X candle, no band), and the same for periods 1-3 candles."""
    out = {}
    for X in XS:
        b = defaultdict(lambda: [0, 0.0, 0])
        for et, ev, pg in games:
            r = lib.decision(ev, series, pg, X, (-1, 2))     # band wide open: only used to get both teams' asks
            for team, a in (r.get("asks") or {}).items():
                res = ev["result"].get(team)
                if a == a and res in ("yes", "no"):
                    k = b[price_bucket(a) if a >= 0.5 else "<50c"]
                    k[0] += 1
                    k[1] += a
                    k[2] += res == "yes"
        out[f"late_X{X // 60}m"] = {k: dict(n=v[0], mean_ask=v[1] / v[0], win_rate=v[2] / v[0]) for k, v in sorted(b.items())}
    b = defaultdict(lambda: [0, 0.0, 0, set()])
    for et, ev, pg in games:
        for m in game_minutes(ev, series, pg):
            if m["period"] not in (1, 2, 3):
                continue
            for team, a in m["asks"].items():
                res = ev["result"].get(team)
                if a == a and a >= 0.5 and res in ("yes", "no"):
                    k = b[price_bucket(a)]
                    k[0] += 1
                    k[1] += a
                    k[2] += res == "yes"
                    k[3].add(et)
    out["periods_1to3_minutes"] = {k: dict(n_team_minutes=v[0], n_games=len(v[3]), mean_ask=v[1] / v[0], win_rate=v[2] / v[0])
                                   for k, v in sorted(b.items())}
    return out


def pass_bar(res):
    r = res[lib.PRIMARY_NAME]
    c = r["coin_flips"].get("unseen", {}).get("matched_p1to3", {})
    f = c.get("frac_copies_ge_real_all")
    checks = {
        "1_mean_pos_both_halves": bool(r["discovery"].get("mean_net_per_contract", -1) > 0 and r["unseen"].get("mean_net_per_contract", -1) > 0),
        "2_unseen_boot_lb_gt0": bool(r["unseen_bootstrap_lower_2_5"] > 0),
        "3_unseen_trades_ge_100": bool(r["unseen"]["n"] >= 100),
        "4_matched_coin_le_5pct": bool(f is not None and f <= 0.05),
        "5_usd_per_week_ge_2": bool((r["unseen_usd_per_week"] or -1) >= 2),
        "6_confirmed_unseen_n_ge_50_mean_gt0": bool(r["confirmed_unseen"]["n"] >= 50 and r["confirmed_unseen"].get("mean_net_per_contract", -1) > 0),
    }
    return dict(checks=checks, PASS=all(checks.values()))


lib.PRIMARY_NAME = vname(*lib.PRIMARY)


# ------------------------------------------------------------------------------------------------ commands
def smoke():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    games, series, rep = lib.build_universe(conn)
    n_disc = split_index(len(games))
    print(f"events {rep['n_events']} matched {rep['n_matched']} unmatched {len(rep['unmatched'])} shifted {len(rep['shifted'])}")
    gaps, gaps2, infav, n = [], [], 0, 0
    for et, ev, pg in games[:20]:
        row = [et]
        for X, bn in ((360, "70-92"), (180, "70-92")):
            r = lib.decision(ev, series, pg, X, BANDS[bn])
            if r["status"] == "skip" and "tX" not in r:
                row.append(f"X{X // 60}: skip {r['reason']}")
                continue
            es = r.get("cend") or {}
            row.append(f"X{X // 60}: tX={datetime.utcfromtimestamp(r['tX']):%H:%M:%S} E=tX+{(r.get('E', r['tX']) - r['tX']) / 60:.1f}m "
                       + " ".join(f"{t}:candle_age={(r['E'] - e) / 60:.0f}m/ask={r['asks'][t]:.2f}" if e else f"{t}:no_quote" for t, e in es.items())
                       + (f" -> BUY {r['team']}@{r['ask']:.2f}" if r["status"] == "trade" else f" -> {r['reason']}"))
            if X == 360:
                n += 1
                infav += r["status"] == "trade"
                gaps += [(r['E'] - e) / 60 for e in es.values() if e]; gaps2.append((r['E'] - r['tX']) / 60)
        print(" | ".join(row))
    print(f"[smoke: games 1-20 of {n_disc} discovery] median minutes t_X->E_X {np.median(gaps2):.2f}, median candle age {np.median(gaps):.2f}; X=6:00 favourite in band 70-92: {infav}/{n}")


def final():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    games, series, rep = lib.build_universe(conn)
    prints = load_prints(conn)
    results, trades = evaluate(games, series, prints)
    out = dict(universe=dict(n_events=rep["n_events"], n_matched=rep["n_matched"], unmatched=rep["unmatched"],
                             shifted=rep["shifted"], bad_parse=rep["bad_parse"]),
               versions=results, calibration=calibration(games, series), pass_bar_primary=pass_bar(results),
               seed=SEED, primary=lib.PRIMARY_NAME)
    (HERE / "results.json").write_text(json.dumps(out, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    cols = list(trades[0]) if trades else []
    with open(HERE / "trades.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(trades)
    print(json.dumps(out["pass_bar_primary"], indent=2))


if __name__ == "__main__":
    {"smoke": smoke, "final": final}[sys.argv[1]]()
