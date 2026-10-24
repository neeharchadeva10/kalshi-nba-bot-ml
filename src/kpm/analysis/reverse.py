"""Reverse test: Kalshi as the fair price, trade on Polymarket US.

Per game and per team T (O = the other team), on a 1-minute grid before kickoff:
  fair_T      = Kalshi-implied probability = mean(Kalshi mid of T, 1 - Kalshi mid of O)
  edge_poly_T = fair_T - polyUS_ask_T - polyUS_fee(ask)                       (buy T on Polymarket US)
  arb_T       = 1 - polyUS_ask_T - polyUS_fee - min(Kalshi NO-on-T ask + fee, Kalshi YES-on-O ask + fee)
                (buy T on Polymarket US and not-T on Kalshi: pays exactly $1 whoever wins)
Strategies:
  poly_gap_close : buy T on Polymarket US at the ask; sell at its bid once it reaches fair_T, else at kickoff
  poly_hold      : same entries held to settlement
  arb            : two-leg trade at the entry minute, held to settlement (locked unless settlement rules differ)
Polymarket US history has bid/ask but no volume, so only the Kalshi leg can be fill-checked.
"""
from __future__ import annotations

import itertools
import json
import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from kpm.analysis import convergence, episodes, strategies
from kpm.analysis.align import kalshi_frame
from kpm.analysis.gaps import ttk_bucket
from kpm.core import audit, config, db
from kpm.core.config import settings
from kpm.core.fees import FeeModel

log = logging.getLogger(__name__)
SOURCE = "polyus_book"


# ----------------------------------------------------------------------------- pure calculations
def kalshi_fair(k_mid_t, k_mid_o):
    """Kalshi-implied probability for T using both team books where available."""
    a, b = np.asarray(k_mid_t, float), 1.0 - np.asarray(k_mid_o, float)
    return np.where(np.isnan(a), b, np.where(np.isnan(b), a, (a + b) / 2.0))


def poly_edge(fair_t, p_ask_t, fee_p: FeeModel, size: float):
    ask = np.asarray(p_ask_t, float)
    return np.asarray(fair_t, float) - ask - fee_p.per_contract_array(ask, size)


def arb_edge(p_ask_t, k_bid_t, k_ask_o, fee_p: FeeModel, fee_k: FeeModel, size: float):
    """Locked profit per $1 pair for: buy T on Polymarket US + cheapest not-T leg on Kalshi.

    Returns (edge, kalshi_leg) where kalshi_leg is 'no_t' (Kalshi NO on T's market) or 'yes_o'.
    """
    ask = np.asarray(p_ask_t, float)
    k_no = 1.0 - np.asarray(k_bid_t, float)
    k_yes_o = np.asarray(k_ask_o, float)
    c_no = k_no + fee_k.per_contract_array(k_no, size)
    c_yes = k_yes_o + fee_k.per_contract_array(k_yes_o, size)
    kcost = np.fmin(c_no, c_yes)
    leg = np.where(np.isnan(c_yes) | (c_no <= c_yes), "no_t", "yes_o")
    leg = np.where(np.isnan(kcost), None, leg)
    edge = 1.0 - ask - fee_p.per_contract_array(ask, size) - kcost
    return edge, leg


# ----------------------------------------------------------------------------- data assembly
def load_pairs(conn) -> pd.DataFrame:
    return pd.read_sql_query(
        """SELECT mm.match_id, mm.game_id, mm.kalshi_market_id, mm.poly_market_id, k.team_abbr AS team,
                  k.settlement_value AS k_settle, p.settlement_value AS p_settle,
                  g.kickoff_ts, g.week, g.game_type
           FROM market_matches mm JOIN games g USING (game_id)
           JOIN markets k ON k.market_id = mm.kalshi_market_id
           JOIN markets p ON p.market_id = mm.poly_market_id
           WHERE mm.status = 'approved' AND p.platform = 'polymarket_us'
           ORDER BY g.kickoff_ts, mm.match_id""", conn)


def poly_frame(conn, market_id: int, max_spread: float) -> pd.DataFrame:
    p = pd.read_sql_query("SELECT ts AS p_ts, yes_bid AS p_bid, yes_ask AS p_ask FROM price_history "
                          "WHERE market_id=? AND source=? ORDER BY ts", conn, params=(market_id, SOURCE))
    ok = (p["p_bid"] > 0) & (p["p_ask"] < 1) & (p["p_ask"] > p["p_bid"]) & (p["p_ask"] - p["p_bid"] <= max_spread)
    p.loc[~ok, ["p_bid", "p_ask"]] = np.nan  # no two-sided book (e.g. 0.50/0.50 placeholder) or too wide
    return p


def game_frame(conn, rows: pd.DataFrame, cfg: dict) -> pd.DataFrame | None:
    """Wide 1-minute frame for one game with both teams' Kalshi and Polymarket US quotes."""
    if len(rows) != 2:
        return None
    pu = cfg["polymarket_us"]
    kickoff = int(rows["kickoff_ts"].iloc[0])
    parts, starts = {}, []
    for side, r in zip("AB", rows.itertuples()):
        k = kalshi_frame(conn, int(r.kalshi_market_id))
        p = poly_frame(conn, int(r.poly_market_id), pu["max_spread"])
        if k.empty or p.empty:
            return None
        starts += [int(k["ts"].min()), int(np.ceil(p["p_ts"].min() / 60) * 60)]
        parts[side] = (k, p)
    start, end = max(starts), kickoff - kickoff % 60
    if start > end:
        return None
    g = pd.DataFrame({"ts": np.arange(start, end + 1, 60, dtype=np.int64)})
    for side, (k, p) in parts.items():
        k = k.copy()
        k["qts"] = np.where(k["yes_bid"].notna() | k["yes_ask"].notna(), k["ts"], np.nan)
        kk = g[["ts"]].merge(k[["ts", "yes_bid", "yes_ask", "volume", "qts"]], on="ts", how="left")
        kk[["yes_bid", "yes_ask", "qts"]] = kk[["yes_bid", "yes_ask", "qts"]].ffill()
        g[f"k_bid_{side}"], g[f"k_ask_{side}"] = kk["yes_bid"].to_numpy(), kk["yes_ask"].to_numpy()
        g[f"k_age_{side}"] = (g["ts"] - kk["qts"]).to_numpy()
        g[f"k_vol1h_{side}"] = kk["volume"].fillna(0).rolling(60, min_periods=1).sum().to_numpy()
        pp = pd.merge_asof(g[["ts"]], p.sort_values("p_ts"), left_on="ts", right_on="p_ts", direction="backward")
        g[f"p_bid_{side}"], g[f"p_ask_{side}"] = pp["p_bid"].to_numpy(), pp["p_ask"].to_numpy()
        g[f"p_age_{side}"] = (g["ts"] - pp["p_ts"]).to_numpy()
    g["mins_to_kickoff"] = (kickoff - g["ts"]) / 60.0
    return g


def team_view(g: pd.DataFrame, side: str, cfg: dict, fee_p: FeeModel, fee_k: FeeModel) -> pd.DataFrame:
    """Per-team columns (T = side, O = other) in the shape convergence/episodes expect."""
    o = "B" if side == "A" else "A"
    a = cfg["analysis"]
    n = cfg["fees"]["order_size"]
    k_mid_t = (g[f"k_bid_{side}"] + g[f"k_ask_{side}"]) / 2
    k_mid_o = (g[f"k_bid_{o}"] + g[f"k_ask_{o}"]) / 2
    fresh_k = (g[f"k_age_{side}"] <= a["kalshi_max_staleness_s"]) & (g[f"k_age_{o}"] <= a["kalshi_max_staleness_s"])
    fresh_p = g[f"p_age_{side}"] <= 120
    fair = np.where(fresh_k, kalshi_fair(k_mid_t, k_mid_o), np.nan)
    p_ask = np.where(fresh_p, g[f"p_ask_{side}"], np.nan)
    p_bid = np.where(fresh_p, g[f"p_bid_{side}"], np.nan)
    arb, leg = arb_edge(p_ask, np.where(fresh_k, g[f"k_bid_{side}"], np.nan),
                        np.where(fresh_k, g[f"k_ask_{o}"], np.nan), fee_p, fee_k, n)
    return pd.DataFrame({
        "ts": g["ts"], "mins_to_kickoff": g["mins_to_kickoff"],
        "fair": fair, "p_ask": p_ask, "p_bid": p_bid, "p_mid": (p_ask + p_bid) / 2,
        "k_bid_t": g[f"k_bid_{side}"], "k_ask_o": g[f"k_ask_{o}"],
        "edge_poly": poly_edge(fair, p_ask, fee_p, n), "arb": arb, "arb_leg": leg,
        "k_vol1h": g[f"k_vol1h_{side}"] + g[f"k_vol1h_{o}"],
        "k_spread_max": np.fmax(g[f"k_ask_{side}"] - g[f"k_bid_{side}"], g[f"k_ask_{o}"] - g[f"k_bid_{o}"]),
        "p_spread": p_ask - p_bid,
        # names expected by convergence.measure
        "k_mid": fair, "p_ref": (p_ask + p_bid) / 2, "p_age_s": g[f"p_age_{side}"],
    })


# ----------------------------------------------------------------------------- simulation
@dataclass(frozen=True)
class RParams:
    threshold: float
    max_spread: float

    def label(self) -> str:
        return f"thr={self.threshold:.3f}|poly_spread<={self.max_spread:.2f}"


def _spread_ok(v: pd.DataFrame, max_spread: float) -> np.ndarray:
    return ((v["p_ask"] - v["p_bid"]) <= max_spread + 1e-12).to_numpy()


def simulate_poly(views: dict, game_rows: pd.DataFrame, p: RParams, fee_p: FeeModel, size: float) -> list[dict]:
    """poly_gap_close and poly_hold on the same entries; one open position per game at a time."""
    sigs = []
    for side, v in views.items():
        edge = np.where(_spread_ok(v, p.max_spread), v["edge_poly"], np.nan)
        for s, e in episodes.find_episodes(v["ts"].to_numpy(), edge, p.threshold):
            sigs.append((int(v["ts"].iloc[s]), side, s))
    out, busy = [], -1
    for t0, side, i0 in sorted(sigs):
        if t0 <= busy:
            continue
        v = views[side]
        bid, fair = v["p_bid"].to_numpy(), v["fair"].to_numpy()
        entry = float(v["p_ask"].iloc[i0])
        j_exit = len(v) - 1
        for j in range(i0 + 1, len(v)):
            if not np.isnan(bid[j]) and not np.isnan(fair[j]) and bid[j] >= fair[j]:
                j_exit = j
                break
        jj = j_exit
        while jj > i0 and np.isnan(bid[jj]):
            jj -= 1
        exit_px = float(bid[jj]) if not np.isnan(bid[jj]) else np.nan
        settle = game_rows.loc[game_rows["side"] == side, "p_settle"].iloc[0]
        fee_in = fee_p.per_contract(entry, size)
        fee_out = fee_p.per_contract(exit_px, size) if not np.isnan(exit_px) else np.nan
        out.append({"side": side, "t0": t0, "entry": entry, "edge0": float(v["edge_poly"].iloc[i0]),
                    "exit_ts": int(v["ts"].iloc[j_exit]), "exit_px": exit_px, "closed_by_gap": j_exit < len(v) - 1,
                    "pnl_poly_gap_close": exit_px - entry - fee_in - fee_out,
                    "pnl_poly_hold": (np.nan if pd.isna(settle) else float(settle)) - entry - fee_in,
                    "mins_to_kickoff0": float(v["mins_to_kickoff"].iloc[i0]),
                    "k_spread_max": float(v["k_spread_max"].iloc[i0]), "p_spread": float(v["p_spread"].iloc[i0])})
        busy = int(v["ts"].iloc[j_exit])
    return out


def simulate_arb(views: dict, game_rows: pd.DataFrame, trades: dict, p: RParams, fee_p: FeeModel,
                 fee_k: FeeModel, size: float, fill_window_s: int) -> tuple[list[dict], int]:
    out, skipped = [], 0
    for side, v in views.items():
        o = "B" if side == "A" else "A"
        r_t = game_rows[game_rows["side"] == side].iloc[0]
        r_o = game_rows[game_rows["side"] == o].iloc[0]
        edge = np.where(_spread_ok(v, p.max_spread), v["arb"], np.nan)
        for s, _e in episodes.find_episodes(v["ts"].to_numpy(), edge, p.threshold):
            t0 = int(v["ts"].iloc[s])
            leg = v["arb_leg"].iloc[s]
            if leg == "no_t":  # Kalshi NO on T's market: needs prints at yes_price >= T's yes bid
                k_px = 1.0 - float(v["k_bid_t"].iloc[s])
                tr = trades.get(int(r_t.kalshi_market_id), pd.DataFrame())
                fv = strategies.fill_volume(tr, t0, "no", float(v["k_bid_t"].iloc[s]), np.nan, fill_window_s)
                k_payoff = None if pd.isna(r_t.k_settle) else 1.0 - float(r_t.k_settle)
            else:  # Kalshi YES on O's market at O's ask
                k_px = float(v["k_ask_o"].iloc[s])
                tr = trades.get(int(r_o.kalshi_market_id), pd.DataFrame())
                fv = strategies.fill_volume(tr, t0, "yes", np.nan, k_px, fill_window_s)
                k_payoff = None if pd.isna(r_o.k_settle) else float(r_o.k_settle)
            if fv < size:
                skipped += 1
                continue
            p_px = float(v["p_ask"].iloc[s])
            cost = p_px + fee_p.per_contract(p_px, size) + k_px + fee_k.per_contract(k_px, size)
            p_payoff = None if pd.isna(r_t.p_settle) else float(r_t.p_settle)
            realized = (np.nan if p_payoff is None or k_payoff is None else p_payoff + k_payoff) - cost
            out.append({"side": side, "t0": t0, "kalshi_leg": leg, "poly_px": p_px, "kalshi_px": k_px,
                        "edge0": float(v["arb"].iloc[s]), "pnl_arb": realized, "locked": 1.0 - cost,
                        "settlement_mismatch": bool(not np.isnan(realized) and abs(realized - (1.0 - cost)) > 1e-9),
                        "fill_volume": fv, "mins_to_kickoff0": float(v["mins_to_kickoff"].iloc[s]),
                        "k_spread_max": float(v["k_spread_max"].iloc[s]), "p_spread": float(v["p_spread"].iloc[s])})
    return out, skipped


# ----------------------------------------------------------------------------- orchestration
def run() -> dict:
    cfg = settings()
    a, pu = cfg["analysis"], cfg["polymarket_us"]
    fee_k, fee_p = FeeModel.from_config(cfg), FeeModel.from_config(cfg, "polymarket_us")
    size = cfg["fees"]["order_size"]
    out = config.path("report_dir") / "reverse"
    out.mkdir(parents=True, exist_ok=True)
    with db.session() as conn:
        pairs = load_pairs(conn)
        if pairs.empty:
            raise RuntimeError("no approved Kalshi/Polymarket US pairs: run polymarket-us, match, review, gate")
        trades_1m = pd.read_sql_query(
            "SELECT * FROM trades_1m WHERE market_id IN (%s)" % ",".join(str(int(x)) for x in pairs.kalshi_market_id),
            conn)
        frames, views = {}, {}
        for gid, rows in pairs.groupby("game_id", sort=False):
            rows = rows.sort_values("team").reset_index(drop=True).assign(side=["A", "B"][:len(rows)])
            g = game_frame(conn, rows, cfg)
            if g is None:
                audit.log(conn, "analysis.reverse", "skip_game", "game", gid, {"pairs": len(rows)})
                continue
            frames[gid] = rows
            views[gid] = {s: team_view(g, s, cfg, fee_p, fee_k) for s in ("A", "B")}
    trades = {k: t for k, t in trades_1m.groupby("market_id")}
    meta = pairs.drop_duplicates("game_id").set_index("game_id")
    split = {gid: ("early" if (r.game_type == "REG" and r.week <= pu["early_last_week"]) else "later")
             for gid, r in meta.iterrows()}

    # --- how often: per-minute frequency of tradable edges ---------------------------------
    rows = []
    for gid, vv in views.items():
        for side, v in vv.items():
            rows.append(v.assign(game_id=gid, side=side, split=split[gid],
                                 spread=v["p_ask"] - v["p_bid"],
                                 ttk_bucket=ttk_bucket(v["mins_to_kickoff"] * 60, a["ttk_buckets"])))
    allv = pd.concat(rows, ignore_index=True)
    freq = []
    groups = [((sp, tb), sub) for (sp, tb), sub in allv.groupby(["split", "ttk_bucket"])]
    groups += [((sp, "all"), sub) for sp, sub in allv.groupby("split")]
    for (sp, tb), sub in groups:
        valid_e = sub["edge_poly"].notna()
        valid_a = sub["arb"].notna()
        r = {"split": sp, "ttk_bucket": tb, "games": sub["game_id"].nunique(), "valid_minutes": int(valid_e.sum()),
             "median_poly_spread_c": 100 * float(sub["spread"].median())}
        for t in (0.01, 0.02, 0.03, 0.05):
            r[f"pct_poly_edge>{round(t * 100)}c"] = 100 * float((sub.loc[valid_e, "edge_poly"] > t).mean()) if valid_e.any() else np.nan
        for t in (0.0, 0.005, 0.01, 0.02):
            r[f"pct_arb>{t * 100:g}c"] = 100 * float((sub.loc[valid_a, "arb"] > t).mean()) if valid_a.any() else np.nan
        freq.append(r)
    freq = pd.DataFrame(freq).sort_values(["split", "ttk_bucket"])

    # hourly profile for the chart
    allv["hour"] = np.floor(allv["mins_to_kickoff"] / 60).astype(int)
    prof = allv.groupby("hour").agg(minutes=("edge_poly", "count"),
                                    pct_poly_edge_1c=("edge_poly", lambda s: 100 * float((s.dropna() > 0.01).mean()) if s.notna().any() else np.nan),
                                    pct_poly_edge_2c=("edge_poly", lambda s: 100 * float((s.dropna() > 0.02).mean()) if s.notna().any() else np.nan),
                                    pct_arb_pos=("arb", lambda s: 100 * float((s.dropna() > 0).mean()) if s.notna().any() else np.nan),
                                    median_spread_c=("spread", lambda s: 100 * float(s.median()))).reset_index()

    # --- who moves after a Polymarket-US-cheap gap opens (2c) ------------------------------
    conv_parts = []
    for gid, vv in views.items():
        for side, v in vv.items():
            e = episodes.find_episodes(v["ts"].to_numpy(), v["edge_poly"].to_numpy(), 0.02)
            if not e:
                continue
            ep = pd.DataFrame({"start_idx": [s for s, _ in e]})
            m = convergence.measure(v.reset_index(drop=True), ep, a["horizons_min"], a["converge_tolerance"], 120)
            conv_parts.append(m.assign(game_id=gid, split=split[gid]))
    conv = pd.concat(conv_parts, ignore_index=True) if conv_parts else pd.DataFrame()
    hz = [f"{h}m" for h in a["horizons_min"]] + ["kickoff"]
    conv_tbl = []
    if not conv.empty:
        for sp, sub in conv.groupby("split"):
            for h in hz:
                s = sub[~sub[f"class_{h}"].isin(["beyond_kickoff", "no_data"])]
                n = len(s)
                conv_tbl.append({"split": sp, "horizon": h, "n_episodes": n, "n_games": s["game_id"].nunique(),
                                 **{f"pct_{c}": 100 * float((s[f"class_{h}"] == c).mean()) if n else np.nan
                                    for c in ("kalshi_toward_poly", "poly_toward_kalshi", "both", "neither")},
                                 "mean_kalshi_move_c": 100 * float(s[f"kalshi_move_{h}"].mean()) if n else np.nan,
                                 "mean_poly_move_c": 100 * float(s[f"poly_move_{h}"].mean()) if n else np.nan})
    conv_tbl = pd.DataFrame(conv_tbl)

    # --- strategies: choose on early weeks, evaluate on later weeks ------------------------
    grid_poly = [RParams(t, ms) for t in (0.01, 0.02, 0.03, 0.05) for ms in (0.03, pu["max_spread"])]
    grid_arb = [RParams(t, ms) for t in (0.0, 0.005, 0.01, 0.02) for ms in (0.03, pu["max_spread"])]

    def run_poly(p: RParams, which: str) -> pd.DataFrame:
        res = []
        for gid, vv in views.items():
            if split[gid] != which:
                continue
            for t in simulate_poly(vv, frames[gid], p, fee_p, size):
                res.append({**t, "game_id": gid})
        return pd.DataFrame(res)

    def run_arb(p: RParams, which: str) -> tuple[pd.DataFrame, int]:
        res, skipped = [], 0
        for gid, vv in views.items():
            if split[gid] != which:
                continue
            t, s = simulate_arb(vv, frames[gid], trades, p, fee_p, fee_k, size, a["fill_window_s"])
            res += [{**x, "game_id": gid} for x in t]
            skipped += s
        return pd.DataFrame(res), skipped

    mg, mt = a["min_games_trust"], a["min_trades_trust"]
    search, results, chosen = [], [], {}
    early_cache = {}
    for p in grid_poly:
        df = run_poly(p, "early")
        early_cache[("poly", p)] = df
        for strat in ("poly_gap_close", "poly_hold"):
            search.append({"strategy": strat, "params": p.label(),
                           **strategies.summarize(df, f"pnl_{strat}", size, 500, mg, mt)})
    for p in grid_arb:
        df, skipped = run_arb(p, "early")
        early_cache[("arb", p)] = df
        search.append({"strategy": "arb", "params": p.label(), "skipped_no_fill": skipped,
                       **strategies.summarize(df, "pnl_arb", size, 500, mg, mt)})
    search = pd.DataFrame(search)
    for strat, grid, kind in (("poly_gap_close", grid_poly, "poly"), ("poly_hold", grid_poly, "poly"),
                              ("arb", grid_arb, "arb")):
        cand = search[(search["strategy"] == strat) & (search["n_trades"] >= mt)]
        note = "best mean PnL per contract on early weeks among sets with enough trades"
        if cand.empty:
            cand = search[search["strategy"] == strat].sort_values("n_trades", ascending=False).head(1)
            note = "no parameter set reached the minimum trade count on early weeks; picked the largest sample"
        best = cand.sort_values("mean_pnl_cents", ascending=False).iloc[0]
        p = next(x for x in grid if x.label() == best["params"])
        chosen[strat] = {"params": p.label(), "note": note}
        for which in ("early", "later"):
            if which == "early":
                df = early_cache[(kind, p)]
            else:
                df = run_poly(p, "later") if kind == "poly" else run_arb(p, "later")[0]
            df.to_csv(out / f"trades_{strat}_{which}.csv", index=False)
            s = strategies.summarize(df, f"pnl_{strat}", size, a["bootstrap_reps"], mg, mt)
            extra = {}
            if strat == "arb" and not df.empty:
                extra = {"settlement_mismatches": int(df["settlement_mismatch"].sum()),
                         "mean_locked_cents": 100 * float(df["locked"].mean())}
            results.append({"strategy": strat, "split": which, "params": p.label(), **s, **extra})
            # Post-hoc check (defined after inspecting the trades): entries where BOTH venues' books were
            # tight. Removes one-minute side-swap glitches and placeholder quotes in wide books.
            if not df.empty:
                tight = df[(df["k_spread_max"] <= 0.03 + 1e-9) & (df["p_spread"] <= 0.03 + 1e-9)]
                s2 = strategies.summarize(tight, f"pnl_{strat}", size, a["bootstrap_reps"], mg, mt)
                results.append({"strategy": strat, "split": which,
                                "params": p.label() + " (post-hoc: both books <= 3c wide at entry)", **s2})
    results = pd.DataFrame(results)

    for name, df in {"frequency": freq, "profile_by_hour": prof, "convergence": conv_tbl,
                     "search_early": search, "results": results}.items():
        df.to_csv(out / f"{name}.csv", index=False)
    summary = {"games": len(views), "games_by_split": pd.Series(split).value_counts().to_dict(),
               "chosen": chosen, "fees": {"polymarket_us": cfg["polymarket_us"]["taker_multiplier"],
                                          "kalshi": cfg["fees"]["taker_multiplier"]}}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    with db.session() as conn:
        audit.log(conn, "analysis.reverse", "complete", None, None, summary)
    log.info("reverse analysis: %d games; results in %s", len(views), out)
    return summary
