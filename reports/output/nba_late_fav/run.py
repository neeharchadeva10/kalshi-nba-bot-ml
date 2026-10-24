"""Research log #35 backtest: buy the NBA favourite late in Q4 (reports/output/nba_late_fav/PREREGISTRATION.md, incl. Amendments).

  python run.py smoke      # first 20 discovery games: per-game t_X, E_X, candle used, favourite, ask.  NO P&L.
  python run.py discovery  # 16 versions on the DISCOVERY half only + luck test -> results_discovery.json, discovery_versions.csv
  python run.py unseen     # REFUSES unless reports/output/nba_late_fav/AUDIT_OK exists (created by the auditor, never by the builder)
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
sys.path.insert(0, str(HERE.parents[2] / "src"))
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
def run_version(games, series, prints, X, band_name, gi_range=None, fetched=None):
    """All trades of one version over games[gi_range] (default all).  The split label comes from the FULL list length.
    Games outside gi_range are never touched (their results are never read)."""
    lo, hi = BANDS[band_name]
    n_disc = split_index(len(games))
    rng_ = range(*gi_range) if gi_range else range(len(games))
    trades, skips = [], Counter()
    for gi in rng_:
        et, ev, pg = games[gi]
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
        ask = r["ask"]                                  # displayed ask (fill check and band use this)
        paid = lib.entry_price(ask)                     # ask + 1c slippage; the fee is on the paid price
        ticker = ev["markets"][team]
        conf, qty = lib.confirmed_fill(prints.get(ticker, []), r["E"], ask)
        away_k, home_k = lib.parse_event_ticker(et)[1:]
        is_home = team == home_k
        hm = pg.margin[X]
        fav_margin = (hm if is_home else -hm) if hm is not None else None
        trades.append(dict(
            version=vname(X, band_name), gi=gi, split="discovery" if gi < n_disc else "unseen", event=et, tip=ev["tip"],
            week=week_key(ev["tip"]), team=team, home=int(is_home), t_x=int(r["tX"]), e_x=int(r["E"]), ask=ask, paid=paid,
            bid=r["bid"], spread=round(ask - r["bid"], 4), win=int(win), fee=lib.fee(paid), net_pc=lib.net_per_contract(paid, win),
            net_usd=COUNT * lib.net_per_contract(paid, win), confirmed=int(conf), print_qty=qty,
            prints_fetched=int(fetched is None or (ticker, int(r["E"]) - 1) in fetched), fav_margin=fav_margin,
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
            nets[t] = lib.net_per_contract(lib.entry_price(a), res == "yes") if (a == a and res in ("yes", "no")) else float("nan")
        out.append(dict(ts=ts, period=per, asks=asks, nets=nets))
    return out


def band_of(version: str) -> str:
    return version.split("_", 1)[1]


def build_groups(games, series, trades_by_version):
    """One pass over games (memory: one game's minute grid at a time).  For every real trade returns
      matched[v][k] = candidate minutes (periods 1-3, a team's displayed ask within 1c of the real displayed ask), each a list of net
                      values (slippage included, one per qualifying team);
      plain[v][k]   = every in-game minute where a team's ask is inside the version's band."""
    by_gi = defaultdict(list)
    matched = {v: [None] * len(tr) for v, tr in trades_by_version.items()}
    plain = {v: [None] * len(tr) for v, tr in trades_by_version.items()}
    for v, tr in trades_by_version.items():
        for k, t in enumerate(tr):
            by_gi[t["gi"]].append((v, k, t))
    for gi in sorted(by_gi):
        et, ev, pg = games[gi]
        mins = game_minutes(ev, series, pg)
        for v, k, t in by_gi[gi]:
            lo, hi = BANDS[band_of(v)]
            cm = defaultdict(list)
            for i, team in lib.coin_candidates_matched(mins, t["ask"], 0.01):
                n = mins[i]["nets"][team]
                if n == n:
                    cm[i].append(n)
            matched[v][k] = [x for _, x in sorted(cm.items())]
            pl = defaultdict(list)
            for i, m in enumerate(mins):
                for team, a in m["asks"].items():
                    if a == a and lo - lib.EPS <= a <= hi + lib.EPS and m["nets"][team] == m["nets"][team]:
                        pl[i].append(m["nets"][team])
            plain[v][k] = [x for _, x in sorted(pl.items())]
        del mins
    return matched, plain


def coin_summary(tr, g_matched, g_plain, copies=500, seed=SEED):
    """Matched (periods 1-3, +-1c) and plain (any in-game minute, ask in band) coin flips for one version and one split."""
    if not tr:
        return dict(n_trades=0)
    real = float(np.mean([t["net_pc"] for t in tr]))
    out = dict(n_trades=len(tr), real_mean_all=real)
    for name, groups in (("matched_p1to3", g_matched), ("plain_random", g_plain)):
        means, n_used = lib.coin_flip(groups, copies, seed)
        used = [t for t, g in zip(tr, groups) if g]
        real_used = float(np.mean([t["net_pc"] for t in used])) if used else float("nan")
        out[name] = dict(n_trades_with_candidates=n_used, n_trades_dropped=len(tr) - n_used, real_mean_same_trades=real_used,
                         copy_mean_avg=float(means.mean()) if len(means) else None,
                         copy_mean_p5_p95=[float(np.percentile(means, 5)), float(np.percentile(means, 95))] if len(means) else None,
                         frac_copies_ge_real_all=float((means >= real).mean()) if len(means) else None,
                         frac_copies_ge_real_same_trades=float((means >= real_used).mean()) if len(means) else None)
    return out


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


def evaluate(games, series, prints, versions=None, splits=("discovery",), fetched=None, luck=False, luck_reps=lib.LUCK_REPS,
             luck_draws=10_000, log=print):
    """games sorted by scheduled start.  splits=("discovery",) touches ONLY the first half of the games (unseen games' results are
    never read); ("unseen",) only the second half; both only in tests / after the audit."""
    n_disc = split_index(len(games))
    lo_gi = 0 if "discovery" in splits else n_disc
    hi_gi = len(games) if "unseen" in splits else n_disc
    n_range = hi_gi - lo_gi
    unseen_weeks = weeks_with_games(games, n_disc) if "unseen" in splits else None
    tby, skips_by = {}, {}
    for X in XS:
        for band_name in BANDS:
            if versions and (X, band_name) not in versions:
                continue
            tr, sk = run_version(games, series, prints, X, band_name, (lo_gi, hi_gi), fetched)
            tby[vname(X, band_name)], skips_by[vname(X, band_name)] = tr, sk
    log("trades built", {v: len(t) for v, t in tby.items()})
    matched, plain = build_groups(games, series, tby)
    results = {}
    for v, tr in tby.items():
        r = dict(n_games_in_range=n_range, n_games_total=len(games), n_discovery_games=n_disc, skips=dict(skips_by[v]),
                 skip_share={k: x / n_range for k, x in skips_by[v].items()}, all=summarize(tr))
        for sp in splits:
            idx = [k for k, t in enumerate(tr) if t["split"] == sp]
            sub = [tr[k] for k in idx]
            lb = lib.boot_lower([t["net_pc"] for t in sub])
            conf = [t for t in sub if t["confirmed"]]
            r[sp] = summarize(sub)
            r[f"{sp}_bootstrap_lower_2_5"], r[f"{sp}_bootstrap_lower_5"] = lb
            r[f"confirmed_{sp}"] = summarize(conf)
            r[f"unconfirmed_{sp}"] = summarize([t for t in sub if not t["confirmed"]])
            r[f"prints_fetched_share_{sp}"] = float(np.mean([t["prints_fetched"] for t in sub])) if sub else None
            r[f"breakdowns_{sp}"] = breakdowns(sub)
            r.setdefault("coin_flips", {})[sp] = coin_summary(sub, [matched[v][k] for k in idx], [plain[v][k] for k in idx])
        if "unseen" in splits:
            r["unseen_weeks_with_games"] = unseen_weeks
            r["unseen_usd_per_week"] = (r["unseen"].get("total_usd_10c", 0.0) / unseen_weeks) if unseen_weeks else None
        results[v] = r
    all_trades = [t for tr in tby.values() for t in tr]
    out = dict(results=results, trades=all_trades)
    if "discovery" in splits:
        stats = {v: dict(n=r["discovery"].get("n", 0), lb=r["discovery_bootstrap_lower_2_5"]) for v, r in results.items()}
        sel, elig = lib.select_version(stats)
        out["selection"] = dict(selected=sel, eligible=elig, stats=stats, min_trades=lib.MIN_DISC_TRADES)
        if luck and sel is not None:
            idx_d = {v: [k for k, t in enumerate(tby[v]) if t["split"] == "discovery"] for v in tby}
            groups = {v: [matched[v][k] for k in idx_d[v]] for v in elig}
            real_lb = stats[sel]["lb"]
            lt = lib.luck_test(groups, elig, real_lb, luck_reps, lib.SEED2, luck_draws)
            b = lt["best_lb_per_rep"]
            lt["real_best_lb"] = real_lb
            lt["best_lb_per_rep_summary"] = dict(mean=float(np.nanmean(b)), p5=float(np.nanpercentile(b, 5)),
                                                 median=float(np.nanmedian(b)), p95=float(np.nanpercentile(b, 95)),
                                                 max=float(np.nanmax(b)))
            del lt["best_lb_per_rep"]
            out["luck_test"] = lt
    return out


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
            if X == 360 and 'E' in r:
                n += 1
                infav += r["status"] == "trade"
                gaps += [(r['E'] - e) / 60 for e in es.values() if e]; gaps2.append((r['E'] - r['tX']) / 60)
        print(" | ".join(row))
    print(f"[smoke: games 1-20 of {n_disc} discovery] median minutes t_X->E_X {np.median(gaps2):.2f}, median candle age {np.median(gaps):.2f}; X=6:00 favourite in band 70-92: {infav}/{n}")


def pass_bar(r):
    """Pass bar items 1-6 for ONE version result that contains both splits (the selected version, run on unseen after the audit)."""
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
AUDIT_OK = HERE / "AUDIT_OK"


def _J(o):
    return o.item() if hasattr(o, "item") else str(o)


def load_fetched(conn):
    """(ticker, E-1) windows whose public-print download completed."""
    out = set()
    for t, step in conn.execute("SELECT ticker, step FROM done WHERE step LIKE 'trades:%'"):
        out.add((t, int(step.split(":", 1)[1])))
    return out


def trades_csv(path, trades):
    cols = list(trades[0]) if trades else []
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(trades)


def discovery():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    games, series, rep = lib.build_universe(conn)
    prints, fetched = load_prints(conn), load_fetched(conn)
    n_disc = split_index(len(games))
    print(f"universe: {len(games)} matched games, discovery = first {n_disc} (games 0..{n_disc - 1}); unseen NOT evaluated", flush=True)
    ev = evaluate(games, series, prints, splits=("discovery",), fetched=fetched, luck=True, log=lambda *a: print(*a, flush=True))
    res, sel = ev["results"], ev["selection"]
    out = dict(stage="DISCOVERY ONLY (unseen half not evaluated)",
               universe=dict(n_events=rep["n_events"], n_matched=rep["n_matched"], n_discovery_games=n_disc,
                             n_unseen_games_not_evaluated=len(games) - n_disc, unmatched=rep["unmatched"], shifted=rep["shifted"],
                             bad_parse=rep["bad_parse"], discovery_first_tip=games[0][1]["tip"], discovery_last_tip=games[n_disc - 1][1]["tip"]),
               slippage=lib.SLIP, selection=sel, original_primary=dict(version=lib.PRIMARY_NAME, result=res[lib.PRIMARY_NAME]),
               luck_test=ev.get("luck_test"), versions=res, calibration=calibration(games[:n_disc], series),
               seed=SEED, seed_luck=lib.SEED2)
    (HERE / "results_discovery.json").write_text(json.dumps(out, indent=2, default=_J))
    rows = []
    for v, r in res.items():
        d, cf = r["discovery"], r["coin_flips"]["discovery"]
        m = cf.get("matched_p1to3", {})
        rows.append(dict(version=v, n_trades=d.get("n", 0), eligible_ge_100=int(v in sel["eligible"]), selected=int(v == sel["selected"]),
                         original_primary=int(v == lib.PRIMARY_NAME), win_rate=d.get("win_rate"), mean_ask=d.get("mean_ask"),
                         mean_net_per_contract=d.get("mean_net_per_contract"), total_usd_10c=d.get("total_usd_10c"),
                         boot_lb_2_5=r["discovery_bootstrap_lower_2_5"], boot_lb_5=r["discovery_bootstrap_lower_5"],
                         mean_spread=d.get("mean_spread"), confirmed_n=r["confirmed_discovery"].get("n", 0),
                         confirmed_mean_net=r["confirmed_discovery"].get("mean_net_per_contract"),
                         prints_fetched_share=r["prints_fetched_share_discovery"],
                         matched_coin_frac_ge_real=m.get("frac_copies_ge_real_all"), matched_coin_copy_mean=m.get("copy_mean_avg"),
                         plain_coin_copy_mean=cf.get("plain_random", {}).get("copy_mean_avg"),
                         n_skipped=sum(r["skips"].values())))
    with open(HERE / "discovery_versions.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    trades_csv(HERE / "trades_discovery.csv", ev["trades"])
    print(json.dumps(dict(selection=sel["selected"], luck=ev.get("luck_test")), indent=2, default=_J))


def unseen():
    """Opens the unseen half for the SELECTED version only.  Refuses without AUDIT_OK."""
    if not AUDIT_OK.exists():
        sys.exit(f"REFUSED: {AUDIT_OK} does not exist.  The unseen half is opened only after the auditor creates AUDIT_OK.")
    disc = json.loads((HERE / "results_discovery.json").read_text())
    if (disc.get("luck_test") or {}).get("verdict") != "PASS":
        sys.exit("REFUSED: the discovery luck test did not PASS (Amendment 2026-10-22: the unseen half is not opened).")
    name = disc["selection"]["selected"]
    X, band = int(name.split("_")[0][1:-1]) * 60, band_of(name)
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    games, series, rep = lib.build_universe(conn)
    prints, fetched = load_prints(conn), load_fetched(conn)
    ev = evaluate(games, series, prints, versions={(X, band)}, splits=("unseen",), fetched=fetched)
    r = ev["results"][name]
    r["discovery"] = disc["versions"][name]["discovery"]                 # item 1 needs the discovery mean (already published)
    out = dict(version=name, result=r, pass_bar=pass_bar(r))
    (HERE / "results_unseen.json").write_text(json.dumps(out, indent=2, default=_J))
    trades_csv(HERE / "trades_unseen.csv", ev["trades"])
    print(json.dumps(out["pass_bar"], indent=2))


if __name__ == "__main__":
    {"smoke": smoke, "discovery": discovery, "unseen": unseen}[sys.argv[1]]()
