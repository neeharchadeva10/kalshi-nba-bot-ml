"""Research log #45 data pipeline: continuity per team-season, odds join, per-game tables. Processed per season."""
from __future__ import annotations

import gc

import numpy as np
import pandas as pd

import lib


def build_season(year: int, prev_rows=None, with_outcome_checks: bool = False):
    """Returns dict(cont, games, info, rows). prev_rows = player rows of season year-1 (loaded if None)."""
    if prev_rows is None:
        prev_rows = lib.load_player_rows(year - 1)
    rows = lib.load_player_rows(year)
    tg = lib.load_team_games(year)
    cont, first3, f3tip = lib.build_continuity(year, prev_rows, rows, tg)
    season_start = tg.date.min()
    odds = lib.load_odds(year)
    matched, n_exact, n_shift, unm = lib.match_odds_to_hoopr(odds, tg)
    nreg = tg.game_id.nunique()
    info = {"season_end": year, "season": f"{year - 1}-{str(year)[2:]}", "season_start_et": season_start,
            "hoopr_regular_games": int(nreg), "odds_rows": int(len(odds)), "matched_exact_date": int(n_exact),
            "matched_shifted_1day": int(n_shift), "odds_unmatched_or_not_regular_season": int(len(unm)),
            "hoopr_regular_games_without_line": int(nreg - len(matched)),
            "match_rate_of_regular_games": round(len(matched) / nreg, 4),
            "teams": int(len(cont)), "players_with_rows_for_2plus_teams_prev_season":
                int((prev_rows.groupby("athlete_id").team_id.nunique() > 1).sum())}
    # data check without outcomes: Spread sign vs moneyline (positive Spread = home favourite -> ML_Home negative)
    sp = matched[(matched.Spread != 0)]
    info["spread_sign_agrees_with_ML_home_fav"] = round(float(((sp.Spread > 0) == (sp.ML_Home < 0)).mean()), 4)
    info["rows_missing_line_or_result"] = int(matched[["OU", "Spread", "Points", "Win_Margin"]].isna().any(axis=1).sum())
    games = lib.build_games(year, cont, f3tip, matched.dropna(subset=["OU", "Spread"]), season_start)
    if with_outcome_checks:       # DISCOVERY seasons only: compare odds-file results with hoopR scores
        hm = tg[tg.team_home_away == "home"][["game_id", "team_score", "opponent_team_score"]]
        x = games.merge(hm, on="game_id")
        info["points_mismatch_vs_hoopR"] = int((x.Points != x.team_score + x.opponent_team_score).sum())
        info["margin_mismatch_vs_hoopR"] = int((x.Win_Margin != x.team_score - x.opponent_team_score).sum())
        info["win_margin_is_home_minus_away"] = bool(info["margin_mismatch_vs_hoopR"] < 0.02 * len(x))
    del rows, tg
    gc.collect()
    return {"cont": cont, "games": games, "info": info}


def build_many(seasons):
    """Sequential, one season of player rows at a time. Returns {year: result} for `seasons`."""
    out = {}
    prev = lib.load_player_rows(min(seasons) - 1)
    for y in range(min(seasons), max(seasons) + 1):
        res = build_season(y, prev, with_outcome_checks=y in lib.DISCOVERY)
        out[y] = res
        prev = lib.load_player_rows(y)
        gc.collect()
    return out


def accuracy_tables(games: pd.DataFrame, window_days=None) -> pd.DataFrame:
    """Per season x continuity tercile (team-game rows: each game counts for both teams, from that team's view),
    games after the team's first 3 only. MAE and bias (actual - line). Spread error in the team's own view:
    team margin vs team's expected margin (+ for favourite)."""
    g = games[~games.excluded_first3]
    if window_days:
        g = g[g.day_idx < window_days]
    rows = []
    for side in ("home", "away"):
        s = 1 if side == "home" else -1
        rows.append(pd.DataFrame({"season_end": g.season_end, "terc": g[f"terc_{side}"],
                                  "spread_err": s * g.Win_Margin - s * g.Spread,
                                  "total_err": g.Points - g.OU}))
    r = pd.concat(rows)
    out = r.groupby(["season_end", "terc"]).agg(
        team_games=("total_err", "size"),
        spread_mae=("spread_err", lambda x: float(np.abs(x).mean())), spread_bias=("spread_err", "mean"),
        total_mae=("total_err", lambda x: float(np.abs(x).mean())), total_bias=("total_err", "mean")).reset_index()
    out["terc"] = out.terc.map({0: "low", 1: "mid", 2: "high"})
    return out
