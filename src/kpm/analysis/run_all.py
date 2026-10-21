"""Steps 7-8: run every analysis on the latest gaps run and write results for the report."""
from __future__ import annotations

import itertools
import json
import logging

import numpy as np
import pandas as pd

from kpm.analysis import calibration, convergence, episodes, strategies
from kpm.core import audit, config, db
from kpm.core.config import settings
from kpm.core.fees import FeeModel

log = logging.getLogger(__name__)


def _out_dir():
    d = config.path("report_dir") / "results"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load(conn, run_id: int):
    gaps = pd.read_sql_query(
        "SELECT match_id, ts, k_yes_bid AS yes_bid, k_yes_ask AS yes_ask, k_mid, k_age_s, p_ref, p_age_s, p_unchanged_s, "
        "edge_yes, edge_no, mins_to_kickoff, ttk_bucket, k_volume_1h, k_spread, is_favorite "
        "FROM gaps WHERE run_id=? ORDER BY match_id, ts", conn, params=(run_id,))
    matches = pd.read_sql_query(
        """SELECT mm.match_id, mm.game_id, mm.kalshi_market_id, k.team_abbr, k.settlement_value AS k_settle,
                  k.result AS k_result, p.price_resolution_s AS poly_res, p.price_source AS poly_source,
                  k.price_resolution_s AS kalshi_res, p.open_ts AS poly_open_ts
           FROM market_matches mm JOIN markets k ON k.market_id = mm.kalshi_market_id
           JOIN markets p ON p.market_id = mm.poly_market_id
           WHERE mm.status='approved' AND p.platform='polymarket'""", conn)
    games = pd.read_sql_query("SELECT * FROM games", conn)
    return gaps, matches, games


def ep_attributes(ep: pd.DataFrame, frames: dict, info: pd.DataFrame) -> pd.DataFrame:
    if ep.empty:
        return ep
    rows = []
    for r in ep.itertuples():
        g = frames[r.match_id]
        i = r.start_idx
        fav = g["is_favorite"].iloc[i]
        backed_fav = None if pd.isna(fav) else int(fav) if r.side == "yes" else 1 - int(fav)
        rows.append({"ttk_bucket": g["ttk_bucket"].iloc[i], "k_spread0": g["k_spread"].iloc[i],
                     "k_volume_1h0": g["k_volume_1h"].iloc[i], "backed_favorite": backed_fav,
                     "mins_to_kickoff0": g["mins_to_kickoff"].iloc[i]})
    out = pd.concat([ep.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    return out.merge(info, on="match_id", how="left")


def closeness_bucket(home_fair_prob: float) -> str:
    """Expected closeness of the game from the de-vigged closing moneyline (known only at kickoff,
    so this is a descriptive split, not a tradable filter)."""
    if pd.isna(home_fair_prob):
        return "n/a"
    fav = max(home_fair_prob, 1 - home_fair_prob)
    return "toss-up (fav <60%)" if fav < 0.60 else "moderate (60-75%)" if fav < 0.75 else "heavy (fav >=75%)"


def listing_bucket(hours: float) -> str:
    """Hours between Polymarket opening the market and the gap appearing."""
    if pd.isna(hours):
        return "n/a"
    return "0-6h" if hours < 6 else "6-24h" if hours < 24 else "1-3d" if hours < 72 else "3d+"


def spread_bucket(s: float) -> str:
    if pd.isna(s):
        return "n/a"
    c = round(s * 100)
    return "1c" if c <= 1 else "2c" if c == 2 else "3c+"


def volume_bucket(v: float, cuts: tuple[float, float]) -> str:
    return "low" if v < cuts[0] else "mid" if v < cuts[1] else "high"


def frequency_table(gaps: pd.DataFrame, eps: pd.DataFrame, info: pd.DataFrame, thresholds: list[float],
                    by: list[str]) -> pd.DataFrame:
    g = gaps.merge(info[["match_id", "game_id", "split", "closeness"]], on="match_id")
    g["edge_best"] = g[["edge_yes", "edge_no"]].max(axis=1)
    rows = []
    keys = by or [None]
    grp_g = g.groupby(by) if by else [((), g)]
    for key, sub in grp_g:
        key = key if isinstance(key, tuple) else (key,)
        valid = sub["edge_best"].notna()
        n_games = sub["game_id"].nunique()
        for thr in thresholds:
            e = eps[eps["threshold"] == thr]
            for col, val in zip(by, key):
                e = e[e[col] == val]
            rows.append({**dict(zip(by, key)), "threshold_c": round(thr * 100),
                         "valid_minutes": int(valid.sum()),
                         "pct_minutes_above": 100 * float((sub.loc[valid, "edge_best"] > thr).mean()) if valid.any() else np.nan,
                         "n_games": int(n_games), "n_episodes": int(len(e)),
                         "games_with_episode": int(e["game_id"].nunique()),
                         "episodes_per_game": len(e) / n_games if n_games else np.nan,
                         "median_duration_min": float(e["duration_min"].median()) if len(e) else np.nan,
                         "p90_duration_min": float(e["duration_min"].quantile(0.9)) if len(e) else np.nan,
                         "pct_lasting_5m+": 100 * float((e["duration_min"] >= 5).mean()) if len(e) else np.nan})
    _ = keys
    return pd.DataFrame(rows)


def gap_profile(gaps: pd.DataFrame, info: pd.DataFrame) -> pd.DataFrame:
    """Per hour before kickoff: how often an after-fee edge existed, raw gap size, Kalshi activity."""
    g = gaps.merge(info[["match_id", "split"]], on="match_id")
    g["edge_best"] = g[["edge_yes", "edge_no"]].max(axis=1)
    g["hour"] = np.floor(g["mins_to_kickoff"] / 60).astype(int)
    g["abs_gap_c"] = 100 * (g["p_ref"] - g["k_mid"]).abs()
    v = g[g["edge_best"].notna()]
    out = v.groupby(["split", "hour"]).agg(
        minutes=("edge_best", "size"),
        pct_above_1c=("edge_best", lambda s: 100 * float((s > 0.01).mean())),
        pct_above_2c=("edge_best", lambda s: 100 * float((s > 0.02).mean())),
        mean_abs_gap_c=("abs_gap_c", "mean"),
        median_k_volume_1h=("k_volume_1h", "median"),
        median_k_spread_c=("k_spread", lambda s: 100 * float(s.median())),
    ).reset_index()
    return out


def convergence_table(conv: pd.DataFrame, horizons: list[str], by: list[str], min_n: int) -> pd.DataFrame:
    rows = []
    grp = conv.groupby(by) if by else [((), conv)]
    for key, sub in grp:
        key = key if isinstance(key, tuple) else (key,)
        for h in horizons:
            s = sub[~sub[f"class_{h}"].isin(["beyond_kickoff", "no_data"])]
            n = len(s)
            r = {**dict(zip(by, key)), "horizon": h, "n_episodes": n, "n_games": int(s["game_id"].nunique())}
            for c in ("kalshi_toward_poly", "poly_toward_kalshi", "both", "neither"):
                r[f"pct_{c}"] = 100 * float((s[f"class_{h}"] == c).mean()) if n else np.nan
            r["mean_kalshi_move_c"] = 100 * float(s[f"kalshi_move_{h}"].mean()) if n else np.nan
            r["mean_poly_move_c"] = 100 * float(s[f"poly_move_{h}"].mean()) if n else np.nan
            r["median_closed_frac"] = float(s[f"closed_frac_{h}"].median()) if n else np.nan
            r["trust"] = "ok" if r["n_games"] >= min_n else "SMALL SAMPLE"
            rows.append(r)
    return pd.DataFrame(rows)


def run_strategy(params: strategies.Params, eps: pd.DataFrame, frames, trades, settle, info, cfg, fee):
    a = cfg["analysis"]
    sig = eps[(eps["threshold"] == params.entry_threshold) & (eps["data"] == params.data)].copy()
    if params.ttk_filter == "excl_last_90m":
        sig = sig[sig["ttk_bucket"] != "last 90m"]
    sig = sig[sig["k_volume_1h0"] >= params.min_volume_1h]
    all_trades, skipped = [], 0
    for gid, gs in sig.groupby("game_id"):
        t, s = strategies.simulate_game(gs, frames, trades, settle, fee, cfg["fees"]["order_size"],
                                        a["fill_window_s"], a["poly_max_staleness_s"])
        all_trades += t
        skipped += s
    df = pd.DataFrame(all_trades)
    if not df.empty:
        df = df.merge(info[["match_id", "game_id", "split", "closeness"]], on="match_id", how="left")
        attrs = sig.set_index(["match_id", "side", "t0"])[["ttk_bucket", "k_spread0", "k_volume_1h0", "backed_favorite"]]
        df = df.join(attrs, on=["match_id", "side", "t0"])
    return df, skipped, len(sig)


def run(run_id: int | None = None) -> dict:
    cfg = settings()
    a = cfg["analysis"]
    fee = FeeModel.from_config(cfg)
    N = cfg["fees"]["order_size"]
    out = _out_dir()
    with db.session() as conn:
        if run_id is None:
            run_id = conn.execute("SELECT MAX(run_id) FROM analysis_runs").fetchone()[0]
        gaps, matches, games = load(conn, run_id)
        trades_1m = pd.read_sql_query("SELECT * FROM trades_1m", conn)
        audit.log(conn, "analysis", "start", "run", run_id, {"gap_rows": len(gaps)})

    info = matches.merge(games[["game_id", "split", "week", "game_type", "kickoff_ts", "home_fair_prob"]], on="game_id")
    info["closeness"] = info["home_fair_prob"].map(closeness_bucket)
    frames = {mid: g.reset_index(drop=True) for mid, g in gaps.groupby("match_id")}
    kmap = dict(zip(matches["kalshi_market_id"], matches["match_id"]))
    trades = {kmap[k]: t for k, t in trades_1m.groupby("market_id") if k in kmap}
    settle = {r.match_id: (None if pd.isna(r.k_settle) else float(r.k_settle)) for r in matches.itertuples()}

    # Two data variants: "all" valid minutes, and "active" minutes where the Polymarket price
    # changed within poly_active_max_unchanged_s (a dormant Polymarket book is not a fair price).
    active_s = a["poly_active_max_unchanged_s"]
    gaps_active = gaps.copy()
    dormant = ~(gaps_active["p_unchanged_s"] <= active_s)
    gaps_active.loc[dormant, ["edge_yes", "edge_no"]] = np.nan
    variants = {"all": gaps, "active": gaps_active}
    vframes = {"all": frames,
               "active": {mid: g.reset_index(drop=True) for mid, g in gaps_active.groupby("match_id")}}

    # --- episodes -------------------------------------------------------------------------
    thresholds = a["edge_thresholds"]
    ep_parts = []
    for name, fr in vframes.items():
        e = pd.concat([episodes.episodes_for_match(g, thresholds, a["grid_s"]) for g in fr.values()],
                      ignore_index=True)
        ep_parts.append(ep_attributes(e, fr, info).assign(data=name))
    ep = pd.concat(ep_parts, ignore_index=True)
    dev_eps = ep[(ep["split"] == "dev") & (ep["threshold"] == thresholds[0]) & (ep["data"] == "all")]
    vcuts = tuple(np.nanquantile(dev_eps["k_volume_1h0"], [1 / 3, 2 / 3])) if len(dev_eps) else (1e3, 1e4)
    ep["spread_bucket"] = ep["k_spread0"].map(spread_bucket)
    ep["volume_bucket"] = ep["k_volume_1h0"].map(lambda v: volume_bucket(v, vcuts))
    ep["favorite"] = ep["backed_favorite"].map({1: "favorite", 0: "underdog"}).fillna("n/a")
    ep["hours_since_poly_open0"] = (ep["t0"] - ep["poly_open_ts"]) / 3600.0
    ep["listing_bucket"] = ep["hours_since_poly_open0"].map(listing_bucket)

    freq_split =pd.concat([frequency_table(variants[n], ep[ep.data == n], info, thresholds, ["split"]).assign(data=n)
                            for n in variants], ignore_index=True)
    freq_ttk = pd.concat([frequency_table(variants[n], ep[ep.data == n], info, thresholds,
                                          ["split", "ttk_bucket"]).assign(data=n) for n in variants], ignore_index=True)
    gap_profile(gaps, info).to_csv(out / "gap_profile_by_hour.csv", index=False)
    freq_close = pd.concat([frequency_table(variants[n], ep[ep.data == n], info, thresholds,
                                            ["split", "closeness"]).assign(data=n) for n in variants], ignore_index=True)

    # --- convergence (prices always from the unmasked frames) -------------------------------
    conv_parts = []
    for (mid, _), e in ep.groupby(["match_id", "data"]):
        conv_parts.append(convergence.measure(frames[mid], e.reset_index(drop=True), a["horizons_min"],
                                              a["converge_tolerance"], a["poly_max_staleness_s"], a["grid_s"]))
    conv = pd.concat(conv_parts, ignore_index=True) if conv_parts else pd.DataFrame()
    hz = [f"{h}m" for h in a["horizons_min"]] + ["kickoff"]
    mg = a["min_games_trust"]
    conv_split = convergence_table(conv, hz, ["data", "split", "threshold"], mg)
    breakdowns = {}
    for dim in ("ttk_bucket", "listing_bucket", "spread_bucket", "volume_bucket", "favorite", "closeness"):
        breakdowns[dim] = convergence_table(conv[conv["threshold"] == 0.02], hz, ["data", "split", dim], mg)

    # --- strategies: pick parameters on dev only, then evaluate holdout -------------------
    grid = [strategies.Params(t, f, v, d) for t, f, v, d in itertools.product(
        thresholds, ["all", "excl_last_90m"], [0.0, 1000.0], ["all", "active"])]
    dev_ep, hold_ep = ep[ep["split"] == "dev"], ep[ep["split"] == "holdout"]
    search = []
    cache: dict = {}
    for p in grid:
        df, skipped, nsig = run_strategy(p, dev_ep, frames, trades, settle, info, cfg, fee)
        cache[p] = df
        for col in ("pnl_gap_close", "pnl_hold"):
            s = strategies.summarize(df, col, N, 500, mg, a["min_trades_trust"])
            search.append({"params": p.label(), "strategy": col.replace("pnl_", ""), "signals": nsig,
                           "skipped_no_fill": skipped, **s})
    search_df = pd.DataFrame(search)

    chosen, results = {}, []
    for strat in ("gap_close", "hold"):
        cand = search_df[(search_df["strategy"] == strat) & (search_df["n_trades"] >= a["min_trades_trust"])]
        if cand.empty:
            cand = search_df[search_df["strategy"] == strat].sort_values("n_trades", ascending=False).head(1)
            note = "no parameter set reached the minimum trade count on dev; picked the largest sample"
        else:
            note = "best mean PnL per contract on dev among sets with enough trades"
        best = cand.sort_values("mean_pnl_cents", ascending=False).iloc[0]
        p = next(x for x in grid if x.label() == best["params"])
        chosen[strat] = {"params": p.label(), "note": note}
        for split_name, eps_ in (("dev", dev_ep), ("holdout", hold_ep)):
            df, skipped, nsig = (cache[p], None, None) if split_name == "dev" else \
                run_strategy(p, eps_, frames, trades, settle, info, cfg, fee)
            if split_name == "holdout":
                df.to_csv(out / f"trades_{strat}_holdout.csv", index=False)
            else:
                df.to_csv(out / f"trades_{strat}_dev.csv", index=False)
            s = strategies.summarize(df, f"pnl_{strat}", N, a["bootstrap_reps"], mg, a["min_trades_trust"])
            results.append({"strategy": strat, "split": split_name, "params": p.label(), **s})
            # breakdowns of the chosen strategy
            if not df.empty:
                df = df.assign(spread_bucket=df["k_spread0"].map(spread_bucket),
                               volume_bucket=df["k_volume_1h0"].map(lambda v: volume_bucket(v, vcuts)),
                               favorite=df["backed_favorite"].map({1: "favorite", 0: "underdog"}).fillna("n/a"))
                for dim in ("ttk_bucket", "spread_bucket", "volume_bucket", "favorite", "closeness"):
                    for val, sub in df.groupby(dim):
                        s2 = strategies.summarize(sub, f"pnl_{strat}", N, 500, mg, a["min_trades_trust"])
                        results.append({"strategy": strat, "split": split_name, "params": p.label(),
                                        "breakdown": dim, "bucket": val, **s2})
    # Post-hoc sensitivity (defined after inspecting the data, so NOT part of the dev/holdout protocol):
    # Polymarket's published price right after listing is often the midpoint of an empty book (~0.50).
    # Re-run the dev-chosen parameters on gaps that opened >= 24h after Polymarket listed the game.
    for strat in ("gap_close", "hold"):
        p = next(x for x in grid if x.label() == chosen[strat]["params"])
        for split_name, eps_ in (("dev", dev_ep), ("holdout", hold_ep)):
            mature = eps_[eps_["hours_since_poly_open0"] >= 24]
            df, skipped, nsig = run_strategy(p, mature, frames, trades, settle, info, cfg, fee)
            s = strategies.summarize(df, f"pnl_{strat}", N, a["bootstrap_reps"], mg, a["min_trades_trust"])
            results.append({"strategy": strat, "split": split_name,
                            "params": p.label() + " (post-hoc: gaps >=24h after Polymarket listing)",
                            "signals": nsig, "skipped_no_fill": skipped, **s})
    # A fixed baseline (no tuning at all) for both splits, as a sanity check on the tuned result.
    base = strategies.Params(0.02, "all", 0.0, "all")
    for split_name, eps_ in (("dev", dev_ep), ("holdout", hold_ep)):
        df, skipped, nsig = run_strategy(base, eps_, frames, trades, settle, info, cfg, fee)
        for strat in ("gap_close", "hold"):
            s = strategies.summarize(df, f"pnl_{strat}", N, a["bootstrap_reps"], mg, a["min_trades_trust"])
            results.append({"strategy": strat, "split": split_name, "params": base.label() + " (baseline)",
                            "signals": nsig, "skipped_no_fill": skipped, **s})
    results_df = pd.DataFrame(results)

    # --- calibration -----------------------------------------------------------------------
    cal_tbl = calibration.game_table(gaps, matches, games)
    cal_scores = calibration.scores(cal_tbl)
    rel = calibration.reliability(cal_tbl.dropna(subset=["poly_kickoff"]).query("y != 0.5")["poly_kickoff"].to_numpy(),
                                  cal_tbl.dropna(subset=["poly_kickoff"]).query("y != 0.5")["y"].to_numpy())

    # --- data quality -----------------------------------------------------------------------
    with db.session() as conn:
        res = pd.read_sql_query("SELECT platform, contract_key, event_key, outcome_label, price_resolution_s, "
                                "price_source FROM markets", conn)
        tok = pd.read_sql_query(
            """SELECT p1.ts, p1.price_mean + p2.price_mean - 1 AS dev FROM price_history p1
               JOIN markets m1 ON m1.market_id = p1.market_id AND m1.platform='polymarket'
               JOIN markets m2 ON m2.platform_market_id = m1.platform_market_id AND m2.market_id > m1.market_id
               JOIN price_history p2 ON p2.market_id = m2.market_id AND p2.ts = p1.ts AND p2.source = p1.source""",
            conn)
        excluded = pd.read_sql_query("SELECT status, COUNT(*) n FROM market_matches GROUP BY status", conn)
    res.to_csv(out / "price_resolution_by_market.csv", index=False)
    quality = {
        "resolution_summary": res.groupby("platform")["price_resolution_s"].describe().round(1).reset_index()
        .to_dict("records"),
        "poly_sources": res[res.platform == "polymarket"]["price_source"].value_counts().to_dict(),
        "poly_token_sum_abs_dev_c": {"mean": 100 * float(tok["dev"].abs().mean()) if len(tok) else None,
                                     "p95": 100 * float(tok["dev"].abs().quantile(0.95)) if len(tok) else None},
        "match_status_counts": excluded.to_dict("records"),
        "volume_bucket_cuts": vcuts,
        "games_in_analysis": {"dev": int(info[info.split == "dev"]["game_id"].nunique()),
                              "holdout": int(info[info.split == "holdout"]["game_id"].nunique())},
    }

    # --- write ---------------------------------------------------------------------------------
    for name, df in {"frequency_by_split": freq_split, "frequency_by_ttk": freq_ttk,
                     "frequency_by_closeness": freq_close,
                     "convergence_by_split": conv_split, "strategy_search_dev": search_df,
                     "strategy_results": results_df, "calibration_games": cal_tbl,
                     "calibration_scores": cal_scores, "calibration_reliability": rel,
                     "episodes": ep, "convergence_episodes": conv}.items():
        df.to_csv(out / f"{name}.csv", index=False)
    for dim, df in breakdowns.items():
        df.to_csv(out / f"convergence_by_{dim}.csv", index=False)
    summary = {"run_id": run_id, "chosen": chosen, "quality": quality}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    with db.session() as conn:
        audit.log(conn, "analysis", "complete", "run", run_id, {"chosen": chosen, "episodes": len(ep)})
    log.info("analysis complete for run %s; results in %s", run_id, out)
    return summary
