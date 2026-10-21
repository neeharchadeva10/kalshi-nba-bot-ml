"""Edge hunt on data already collected (free). Each idea is chosen on the early weeks and judged on the
later weeks only. Kalshi-only ideas use every game with a Kalshi market.

1. kalshi_arb   : both Kalshi team markets bought together for < $1 after fees (YES pair or NO pair)
2. drift        : buy a side at time T before kickoff by a simple rule, sell at the last pre-kickoff minute
                  (or hold to settlement)
3. price_bucket : buy every team priced in a bucket at time T, hold to settlement (favorite-longshot bias)
4. venue_choice : all-in cost on Kalshi vs Polymarket US at the same minute (better odds, not an edge)
5. maker        : resting limit bid on Kalshi vs paying the ask (better odds, with fill rate and selection)
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from kpm.analysis.align import kalshi_frame
from kpm.analysis.episodes import find_episodes
from kpm.analysis.reverse import kalshi_fair
from kpm.analysis.strategies import cluster_bootstrap_ci, fill_volume
from kpm.core import audit, config, db
from kpm.core.config import settings
from kpm.core.fees import FeeModel

log = logging.getLogger(__name__)
HOURS = [120, 72, 48, 24, 6, 3, 1]


def pick_on_early(df: pd.DataFrame, min_n: int) -> tuple[pd.Series, str]:
    """Best mean PnL on the early weeks among sets with enough trades (else the largest sample)."""
    e = df[df["split"] == "early"]
    ok = e[e["n_trades"] >= min_n]
    if ok.empty:
        return e.sort_values("n_trades", ascending=False).iloc[0], f"no set reached {min_n} trades; picked largest"
    return ok.sort_values("mean_c", ascending=False).iloc[0], f"best mean on early weeks among sets with >= {min_n} trades"


# ----------------------------------------------------------------------------- pure calculations
def pair_cost(price_a, price_b, fee: FeeModel, size: float):
    """All-in cost of buying one contract on each of two complementary outcomes."""
    a, b = np.asarray(price_a, float), np.asarray(price_b, float)
    return a + fee.per_contract_array(a, size) + b + fee.per_contract_array(b, size)


def price_bucket(p: float) -> str:
    if pd.isna(p):
        return "n/a"
    edges = [(0.0, 0.2, "<20c"), (0.2, 0.4, "20-40c"), (0.4, 0.6, "40-60c"), (0.6, 0.8, "60-80c"), (0.8, 1.01, ">=80c")]
    return next(lbl for lo, hi, lbl in edges if lo <= p < hi)


def summarize(pnl: pd.Series, games: pd.Series, size: float, reps: int = 2000) -> dict:
    v = pnl.to_numpy(float)
    ok = ~np.isnan(v)
    v, g = v[ok], games.to_numpy()[ok]
    if len(v) == 0:
        return {"n_trades": 0, "n_games": 0}
    lo, hi = cluster_bootstrap_ci(v, g, reps)
    return {"n_trades": int(len(v)), "n_games": int(len(np.unique(g))), "mean_c": 100 * float(v.mean()),
            "ci95_c": (round(100 * lo, 2), round(100 * hi, 2)), "win_rate": float((v > 0).mean()),
            "total_usd": float(v.sum() * size)}


def closing_line_buckets(seasons: list[int]) -> pd.DataFrame:
    """Long-run check of price-bucket ideas: de-vigged sportsbook closing moneyline vs results (nflverse)."""
    from kpm.clients.nfl import load_schedule
    from kpm.core.prices import american_to_prob, devig_two_way

    rows = []
    for season in seasons:
        df = load_schedule(season)
        df = df[df["home_score"].notna() & df["home_moneyline"].notna() & df["away_moneyline"].notna()]
        for r in df.itertuples():
            hp, ap = devig_two_way(american_to_prob(float(r.home_moneyline)), american_to_prob(float(r.away_moneyline)))
            if hp is None or r.home_score == r.away_score:
                continue
            hw = 1.0 if r.home_score > r.away_score else 0.0
            rows += [{"season": season, "p": hp, "y": hw}, {"season": season, "p": ap, "y": 1 - hw}]
    t = pd.DataFrame(rows)
    t["bucket"] = pd.cut(t["p"], [0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0], right=False).astype(str)
    t["era"] = np.where(t["season"] == max(seasons), str(max(seasons)), f"{min(seasons)}-{max(seasons) - 1}")
    g = t.groupby(["era", "bucket"]).agg(n=("y", "size"), implied=("p", "mean"), actual=("y", "mean")).reset_index()
    g["diff_pp"] = 100 * (g["actual"] - g["implied"])
    g["z"] = g["diff_pp"] / (100 * np.sqrt(g["actual"] * (1 - g["actual"]) / g["n"]))
    return g


# ----------------------------------------------------------------------------- data
def load(conn):
    games = pd.read_sql_query("SELECT * FROM games", conn)
    km = pd.read_sql_query("SELECT market_id, game_id, team_abbr, settlement_value FROM markets "
                           "WHERE platform='kalshi' AND game_id IS NOT NULL", conn)
    km = km.groupby("game_id").filter(lambda d: len(d) == 2)
    return games, km


def kalshi_game(conn, rows: pd.DataFrame, kickoff: int) -> pd.DataFrame | None:
    rows = rows.sort_values("team_abbr").reset_index(drop=True)
    fr = {s: kalshi_frame(conn, int(r.market_id)) for s, r in zip("AB", rows.itertuples())}
    if any(f.empty for f in fr.values()):
        return None
    start, end = max(int(f["ts"].min()) for f in fr.values()), kickoff - kickoff % 60
    if start > end:
        return None
    g = pd.DataFrame({"ts": np.arange(start, end + 1, 60, dtype=np.int64)})
    for s, f in fr.items():
        f = f.copy()
        f["qts"] = np.where(f["yes_bid"].notna() | f["yes_ask"].notna(), f["ts"], np.nan)
        m = g[["ts"]].merge(f[["ts", "yes_bid", "yes_ask", "volume", "qts"]], on="ts", how="left")
        m[["yes_bid", "yes_ask", "qts"]] = m[["yes_bid", "yes_ask", "qts"]].ffill()
        g[f"bid_{s}"], g[f"ask_{s}"] = m["yes_bid"].to_numpy(), m["yes_ask"].to_numpy()
        g[f"age_{s}"] = (g["ts"] - m["qts"]).to_numpy()
    g["fair_A"] = kalshi_fair((g["bid_A"] + g["ask_A"]) / 2, (g["bid_B"] + g["ask_B"]) / 2)
    g["mins_to_kickoff"] = (kickoff - g["ts"]) / 60.0
    return g


def at(g: pd.DataFrame, t: int, max_age: int = 3600):
    sub = g[g["ts"] <= t]
    if sub.empty:
        return None
    r = sub.iloc[-1]
    if t - r["ts"] > max_age or r["age_A"] > max_age or r["age_B"] > max_age:
        return None
    return r


# ----------------------------------------------------------------------------- tests
def run() -> dict:
    cfg = settings()
    a = cfg["analysis"]
    size = cfg["fees"]["order_size"]
    fee_k = FeeModel.from_config(cfg)
    fee_km = FeeModel(cfg["fees"]["taker_multiplier"], cfg["fees"]["maker_fraction"], cfg["fees"]["rounding"])
    fee_p = FeeModel.from_config(cfg, "polymarket_us")
    out = config.path("report_dir") / "edge_hunt"
    out.mkdir(parents=True, exist_ok=True)
    with db.session() as conn:
        games, km = load(conn)
        trades_1m = pd.read_sql_query("SELECT * FROM trades_1m", conn)
        polyus = pd.read_sql_query(
            """SELECT p.game_id, p.team_abbr, ph.ts, ph.yes_bid AS p_bid, ph.yes_ask AS p_ask
               FROM markets p JOIN price_history ph ON ph.market_id = p.market_id AND ph.source='polyus_book'
               JOIN market_matches mm ON mm.poly_market_id = p.market_id AND mm.status='approved'
               WHERE p.platform='polymarket_us'""", conn)
        frames = {}
        for gid, rows in km.groupby("game_id"):
            ko = int(games.loc[games.game_id == gid, "kickoff_ts"].iloc[0])
            g = kalshi_game(conn, rows, ko)
            if g is not None:
                frames[gid] = (g, rows.sort_values("team_abbr").reset_index(drop=True))
    trades = {k: t for k, t in trades_1m.groupby("market_id")}
    gi = games.set_index("game_id")
    frames = {gid: v for gid, v in frames.items() if int(gi.at[gid, "season"]) == cfg["season"]}  # study season only
    split = {gid: ("early" if (gi.at[gid, "game_type"] == "REG" and gi.at[gid, "week"] <= cfg["split"]["dev_last_week"])
                   else "later") for gid in frames}
    results = {}

    # 1. Kalshi-only arbitrage -----------------------------------------------------------
    arb_rows, minute_stats = [], []
    for gid, (g, rows) in frames.items():
        fresh = (g["age_A"] <= 3600) & (g["age_B"] <= 3600)
        yes_edge = np.where(fresh, 1 - pair_cost(g["ask_A"], g["ask_B"], fee_k, size), np.nan)
        no_edge = np.where(fresh, 1 - pair_cost(1 - g["bid_A"], 1 - g["bid_B"], fee_k, size), np.nan)
        minute_stats.append({"game_id": gid, "split": split[gid], "minutes": int(fresh.sum()),
                             "yes_pos": int(np.nansum(yes_edge > 0)), "no_pos": int(np.nansum(no_edge > 0)),
                             "max_yes_c": 100 * float(np.nanmax(yes_edge)) if fresh.any() else np.nan,
                             "max_no_c": 100 * float(np.nanmax(no_edge)) if fresh.any() else np.nan})
        for kind, edge in (("yes_pair", yes_edge), ("no_pair", no_edge)):
            for s, _e in find_episodes(g["ts"].to_numpy(), edge, 0.0):
                t0 = int(g["ts"].iloc[s])
                r = g.iloc[s]
                legs = []
                for side, mid in zip("AB", rows["market_id"]):
                    tr = trades.get(int(mid), pd.DataFrame())
                    if kind == "yes_pair":
                        legs.append(fill_volume(tr, t0, "yes", np.nan, float(r[f"ask_{side}"]), a["fill_window_s"]))
                    else:
                        legs.append(fill_volume(tr, t0, "no", float(r[f"bid_{side}"]), np.nan, a["fill_window_s"]))
                arb_rows.append({"game_id": gid, "split": split[gid], "kind": kind, "t0": t0,
                                 "mins_to_kickoff": float(r["mins_to_kickoff"]), "edge": float(edge[s]),
                                 "fillable": min(legs) >= size, "pnl": float(edge[s])})
    arb = pd.DataFrame(arb_rows)
    ms = pd.DataFrame(minute_stats)
    arb.to_csv(out / "kalshi_arb_episodes.csv", index=False)
    results["kalshi_arb"] = {
        sp: {"games": int((ms.split == sp).sum()), "minutes": int(ms.loc[ms.split == sp, "minutes"].sum()),
             "minutes_yes_pair_positive": int(ms.loc[ms.split == sp, "yes_pos"].sum()),
             "minutes_no_pair_positive": int(ms.loc[ms.split == sp, "no_pos"].sum()),
             "episodes": int((arb.split == sp).sum()) if not arb.empty else 0,
             "fillable_episodes": int(((arb.split == sp) & arb.fillable).sum()) if not arb.empty else 0,
             **({"fillable": summarize(arb.loc[(arb.split == sp) & arb.fillable, "pnl"],
                                       arb.loc[(arb.split == sp) & arb.fillable, "game_id"], size)}
                if not arb.empty else {})}
        for sp in ("early", "later")}

    # snapshots for tests 2, 3, 5 -----------------------------------------------------------
    snaps = []
    for gid, (g, rows) in frames.items():
        ko = int(gi.at[gid, "kickoff_ts"])
        close = at(g, ko)
        settle = dict(zip("AB", rows["settlement_value"]))
        for h in HOURS + [0]:
            r = at(g, ko - h * 3600)
            if r is None:
                continue
            prev = at(g, ko - (h + 24) * 3600) if h > 0 else None
            fav = "A" if r["fair_A"] >= 0.5 else "B"
            for side in "AB":
                fair = r["fair_A"] if side == "A" else 1 - r["fair_A"]
                prev_fair = None if prev is None else (prev["fair_A"] if side == "A" else 1 - prev["fair_A"])
                close_fair = None if close is None else (close["fair_A"] if side == "A" else 1 - close["fair_A"])
                snaps.append({"game_id": gid, "split": split[gid], "h": h, "side": side, "is_fav": side == fav,
                              "market_id": int(rows.loc["AB".index(side), "market_id"]), "ts": int(r["ts"]),
                              "fair": fair, "bid": r[f"bid_{side}"], "ask": r[f"ask_{side}"],
                              "momentum_24h": None if prev_fair is None else fair - prev_fair,
                              "close_fair": close_fair,
                              "close_bid": None if close is None else close[f"bid_{side}"],
                              "settle": settle[side]})
    sn = pd.DataFrame(snaps)
    sn = sn[sn["ask"].notna() & sn["bid"].notna()]
    sn["fee_in"] = fee_k.per_contract_array(sn["ask"], size)
    sn["fee_out"] = fee_k.per_contract_array(sn["close_bid"].astype(float), size)
    sn["pnl_to_close"] = sn["close_bid"].astype(float) - sn["ask"] - sn["fee_in"] - sn["fee_out"]
    sn["pnl_hold"] = sn["settle"].astype(float) - sn["ask"] - sn["fee_in"]
    sn["fillable"] = [fill_volume(trades.get(m, pd.DataFrame()), t, "yes", np.nan, k, a["fill_window_s"]) >= size
                      for m, t, k in zip(sn["market_id"], sn["ts"], sn["ask"])]
    sn.to_csv(out / "snapshots.csv", index=False)

    # 2. drift rules: chosen on early, judged on later ---------------------------------------
    rules = {
        "buy_favorite": lambda d: d[d["is_fav"]],
        "buy_underdog": lambda d: d[~d["is_fav"]],
        "momentum": lambda d: d[d["momentum_24h"].astype(float) > 0.005],
        "reversal": lambda d: d[d["momentum_24h"].astype(float) < -0.005],
    }
    grid = []
    for h in HOURS:
        base = sn[(sn["h"] == h) & sn["fillable"]]
        for name, f in rules.items():
            for exit_ in ("pnl_to_close", "pnl_hold"):
                for sp in ("early", "later"):
                    d = f(base[base["split"] == sp])
                    grid.append({"h": h, "rule": name, "exit": exit_, "split": sp,
                                 "mean_drift_c": 100 * float((d["close_fair"].astype(float) - d["fair"]).mean()) if len(d) else np.nan,
                                 **summarize(d[exit_], d["game_id"], size, 500)})
    grid = pd.DataFrame(grid)
    grid.to_csv(out / "drift_grid.csv", index=False)
    best, note = pick_on_early(grid, a["min_trades_trust"])
    later = grid[(grid["split"] == "later") & (grid["h"] == best["h"]) & (grid["rule"] == best["rule"]) &
                 (grid["exit"] == best["exit"])].iloc[0]
    early = grid[(grid["split"] == "early") & (grid["n_trades"] >= a["min_trades_trust"])]
    results["drift"] = {"chosen_on_early": {k: best.get(k) for k in ("h", "rule", "exit", "n_trades", "mean_c", "ci95_c")},
                        "note": note,
                        "later": {k: later.get(k) for k in ("n_trades", "n_games", "mean_c", "ci95_c", "win_rate")},
                        "early_sets_positive": int((early["mean_c"] > 0).sum()), "early_sets": int(len(early))}

    # 3. price buckets (favorite-longshot), hold to settlement ---------------------------------
    sn["bucket"] = sn["ask"].map(price_bucket)
    pb = []
    for h in (24, 3, 0):
        for bk, d in sn[(sn["h"] == h) & sn["fillable"]].groupby("bucket"):
            for sp in ("early", "later"):
                dd = d[d["split"] == sp]
                pb.append({"h": h, "bucket": bk, "split": sp,
                           "win_rate_actual": float(dd["settle"].astype(float).mean()) if len(dd) else np.nan,
                           "avg_ask": float(dd["ask"].mean()) if len(dd) else np.nan,
                           **summarize(dd["pnl_hold"], dd["game_id"], size, 500)})
    pb = pd.DataFrame(pb)
    pb.to_csv(out / "price_buckets.csv", index=False)
    bbest, bnote = pick_on_early(pb, 30)
    blater = pb[(pb["split"] == "later") & (pb["h"] == bbest["h"]) & (pb["bucket"] == bbest["bucket"])].iloc[0]
    pe = pb[(pb["split"] == "early") & (pb["n_trades"] >= 30)]
    results["price_bucket"] = {"chosen_on_early": {k: bbest.get(k) for k in ("h", "bucket", "n_trades", "mean_c", "ci95_c")},
                               "note": bnote,
                               "later": {k: blater.get(k) for k in ("n_trades", "n_games", "mean_c", "ci95_c", "win_rate")},
                               "early_buckets_positive": int((pe["mean_c"] > 0).sum()), "early_buckets": int(len(pe))}

    # 4. venue choice: Kalshi vs Polymarket US all-in cost (better odds) -------------------------
    vc = []
    if not polyus.empty:
        polyus = polyus[(polyus["p_bid"] > 0) & (polyus["p_ask"] < 1) & (polyus["p_ask"] > polyus["p_bid"]) &
                        (polyus["p_ask"] - polyus["p_bid"] <= cfg["polymarket_us"]["max_spread"])]
        for (gid, team), pq in polyus.groupby(["game_id", "team_abbr"]):
            if gid not in frames:
                continue
            g, rows = frames[gid]
            side = "AB"[list(rows["team_abbr"]).index(team)] if team in list(rows["team_abbr"]) else None
            if side is None:
                continue
            ko = int(gi.at[gid, "kickoff_ts"])
            for h in (24, 3, 0.25):
                t = int(ko - h * 3600)
                r = at(g, t)
                p = pq[pq["ts"] <= t].tail(1)
                if r is None or p.empty or t - int(p["ts"].iloc[0]) > 120:
                    continue
                k_ask, p_ask = float(r[f"ask_{side}"]), float(p["p_ask"].iloc[0])
                if np.isnan(k_ask) or abs(k_ask - p_ask) > 0.10:  # skip glitches
                    continue
                ck = k_ask + fee_k.per_contract(k_ask, size)
                cp = p_ask + fee_p.per_contract(p_ask, size)
                vc.append({"game_id": gid, "team": team, "h": h, "kalshi_cost": ck, "polyus_cost": cp,
                           "saving_vs_kalshi": max(0.0, ck - cp), "polyus_cheaper": cp < ck - 1e-9})
    vc = pd.DataFrame(vc)
    vc.to_csv(out / "venue_choice.csv", index=False)
    if not vc.empty:
        results["venue_choice"] = {
            str(h): {"n": int(len(d)), "pct_polyus_cheaper": 100 * float(d["polyus_cheaper"].mean()),
                     "avg_saving_c_when_cheaper": 100 * float(d.loc[d["polyus_cheaper"], "saving_vs_kalshi"].mean())
                     if d["polyus_cheaper"].any() else 0.0,
                     "avg_saving_c_all_bets": 100 * float(d["saving_vs_kalshi"].mean())}
            for h, d in vc.groupby("h")}

    # 5. maker vs taker on Kalshi (better odds) ---------------------------------------------------
    mk = []
    for r in sn[sn["h"].isin([24, 3, 1])].itertuples():
        ko = int(gi.at[r.game_id, "kickoff_ts"])
        t0 = int(r.ts)
        window_end = min(t0 + 1800, ko)
        if window_end <= t0:
            continue
        tr = trades.get(r.market_id, pd.DataFrame())
        w = tr[(tr["minute_ts"] > t0) & (tr["minute_ts"] <= window_end)] if not tr.empty else tr
        # conservative fill: takers selling into the bid traded at least 5x our size at or below our price,
        # or anything printed below our price (traded through)
        at_or_below = w[(w["yes_price"] <= r.bid + 1e-9) & (w["taker_side"] == "no")]["count"].sum() if not w.empty else 0
        through = (w["yes_price"] < r.bid - 1e-9).any() if not w.empty else False
        filled = bool(at_or_below >= 5 * size or through)
        maker_cost = r.bid + fee_km.per_contract(r.bid, size, maker=True)
        taker_cost = r.ask + r.fee_in
        mk.append({"game_id": r.game_id, "h": r.h, "filled": filled, "saving_c": 100 * (taker_cost - maker_cost),
                   "maker_pnl_hold": float(r.settle) - maker_cost, "taker_pnl_hold": float(r.settle) - taker_cost})
    mk = pd.DataFrame(mk)
    mk.to_csv(out / "maker_vs_taker.csv", index=False)
    results["maker"] = {}
    for h, d in mk.groupby("h"):
        f = d[d["filled"]]
        results["maker"][str(h)] = {
            "orders": int(len(d)), "fill_rate": float(d["filled"].mean()),
            "avg_saving_c_if_filled": float(f["saving_c"].mean()) if len(f) else np.nan,
            # selection check: when a resting bid fills, is the outcome worse than average? compare the
            # settlement PnL of filled bids vs taker buys of all orders at the same moments
            "maker_pnl_hold_filled_c": 100 * float(f["maker_pnl_hold"].mean()) if len(f) else np.nan,
            "taker_pnl_hold_all_c": 100 * float(d["taker_pnl_hold"].mean()),
            "taker_pnl_hold_same_orders_c": 100 * float(f["taker_pnl_hold"].mean()) if len(f) else np.nan}

    # 6. long-run check of any price-bucket hint on 20 seasons of sportsbook closing lines -------
    clb = closing_line_buckets(list(range(cfg["season"] - 19, cfg["season"] + 1)))
    clb.to_csv(out / "closing_line_buckets_longrun.csv", index=False)
    results["closing_line_longrun"] = {
        "seasons": f"{cfg['season'] - 19}-{cfg['season']}",
        "max_abs_z_prior_seasons": float(clb.loc[clb["era"] != str(cfg["season"]), "z"].abs().max())}

    (out / "summary.json").write_text(json.dumps(results, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)),
                                      encoding="utf-8")
    with db.session() as conn:
        audit.log(conn, "analysis.edge_hunt", "complete", None, None, {"games": len(frames)})
    log.info("edge hunt done: %d games; results in %s", len(frames), out)
    return results
