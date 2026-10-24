"""Research log #45: DISCOVERY ONLY (2022-23, 2023-24) + luck test. Continuity for all 4 seasons (no outcomes).
Unseen-season outcome columns are never analysed here: only keys/continuity/line-sign checks are built for them.
Outputs: continuity.csv, results_discovery.json, discovery_versions.csv, accuracy_discovery.csv, kalshi_main.csv"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import lib
import pipeline

HERE = lib.HERE
K2H = {"GSW": "GS", "NYK": "NY", "NOP": "NO", "SAS": "SA", "UTA": "UTAH", "WAS": "WSH"}   # Kalshi -> hoopR codes


def kalshi_main_table():
    """2025-26 Kalshi T-1h main strike (mid closest to 0.5) and its prices per game. Prices only, NO outcomes."""
    prep = lib.load_totals_prep()
    games, strikes, info = prep.build_games()
    g = games[games.season == "2025-26"]
    rows = []
    for r in g.itertuples():
        base = {"event": r.event, "date": r.date, "away": r.away, "home": r.home}
        m = lib.main_strike(strikes[strikes.event == r.event])
        if m is None:
            rows.append({**base, "ticker": None})
            continue
        rows.append({**base, "ticker": m.ticker, "strike": m.strike, "strike_type": m.strike_type, "bid": m.bid,
                     "ask": m.ask, "candle_end": m.candle_end, "T": r.T})
    return pd.DataFrame(rows), info


def main():
    seasons = lib.ALL_SEASONS
    res = pipeline.build_many(seasons)
    cont = pd.concat([res[y]["cont"] for y in seasons])
    cont.to_csv(HERE / "continuity.csv", index=False)
    infos = {str(y): res[y]["info"] for y in seasons}
    disc = pd.concat([res[y]["games"] for y in lib.DISCOVERY], ignore_index=True)
    out = {"data_checks": infos, "n_discovery_games_with_line": int(len(disc)),
           "n_discovery_games_excluded_first3": int(disc.excluded_first3.sum()),
           "n_discovery_games_in_bet_pool": int((~disc.excluded_first3).sum())}
    grid = lib.version_grid()
    out["n_versions"] = len(grid)
    vdf = lib.evaluate_versions(disc, grid)
    vdf.to_csv(HERE / "discovery_versions.csv", index=False)
    best = lib.select_best(vdf)
    out["selected"] = None if best is None else {k: (v.item() if hasattr(v, "item") else v) for k, v in best.items()}
    pool = ~disc.excluded_first3
    out["skips"] = {
        "first3_either_team": out["n_discovery_games_excluded_first3"],
        "spread_both_teams_low20_in_pool": int((pool & disc.low20_home & disc.low20_away).sum()),
        "spread_both_teams_low33_in_pool": int((pool & disc.low33_home & disc.low33_away).sum()),
        "pushes_spread_in_pool": int(((disc.Win_Margin == disc.Spread) & pool).sum()),
        "pushes_total_in_pool": int(((disc.Points == disc.OU) & pool).sum())}
    bests, real_lb = lib.luck_test(disc, lib.N_LUCK, lib.SEED, grid)
    share = lib.luck_share(bests, real_lb)
    out["luck_test"] = {"reps": lib.N_LUCK, "seed": lib.SEED, "real_best_lb": real_lb,
                        "share_reps_best_lb_ge_real": share, "rep_best_lb_median": float(np.median(bests)),
                        "rep_best_lb_p95": float(np.quantile(bests, 0.95)),
                        "verdict": ("PASS: distinguishable from luck on discovery (share <= 5%)" if share <= 0.05 else
                                    "FAIL: indistinguishable from luck; unseen not to be opened")}
    acc_all = pipeline.accuracy_tables(disc).assign(window="all post-first-3 games")
    acc_30 = pipeline.accuracy_tables(disc, 30).assign(window="first 30 days")
    acc = pd.concat([acc_all, acc_30])
    acc.to_csv(HERE / "accuracy_discovery.csv", index=False)
    out["accuracy_discovery"] = json.loads(acc.to_json(orient="records"))
    # Kalshi 2025-26 main-strike join: keys + prices only (no outcomes)
    kt, kinfo = kalshi_main_table()
    odds26 = lib.load_odds(2026)
    keyset = {(r.Date, r.home, r.away) for r in odds26.itertuples()}
    kt["in_odds"] = [(d, K2H.get(h, h), K2H.get(a, a)) in keyset for d, h, a in zip(kt.date, kt.home, kt.away)]
    kt.to_csv(HERE / "kalshi_main.csv", index=False)
    out["kalshi_join"] = {"kalshi_2025_26_events": int(len(kt)), "with_main_strike": int(kt.ticker.notna().sum()),
                          "matched_to_odds_by_date_teams": int(kt.in_odds.sum()),
                          "odds_2025_26_rows": int(len(odds26))}
    (HERE / "results_discovery.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: v for k, v in out.items() if k != "accuracy_discovery"}, indent=1, default=str))
    print(vdf[["version", "n", "win_pct", "mean", "lb"]].to_string())


if __name__ == "__main__":
    main()
