"""Is the Polymarket price a trustworthy fair value? Calibration vs final outcomes."""
from __future__ import annotations

import numpy as np
import pandas as pd

HORIZONS_S = {"24h": 86400, "3h": 10800, "kickoff": 0}


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2)) if len(p) else np.nan


def log_loss(p: np.ndarray, y: np.ndarray, eps: float = 1e-4) -> float:
    p = np.clip(p, eps, 1 - eps)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))) if len(p) else np.nan


def reliability(p: np.ndarray, y: np.ndarray, bins: int = 10) -> pd.DataFrame:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    rows = []
    for b in range(bins):
        m = idx == b
        if m.any():
            rows.append({"bin": f"{edges[b]:.1f}-{edges[b + 1]:.1f}", "n": int(m.sum()),
                         "mean_pred": float(p[m].mean()), "actual_rate": float(y[m].mean())})
    return pd.DataFrame(rows)


def snapshot(g: pd.DataFrame, kickoff_ts: int, horizon_s: int, col: str, age_col: str | None,
             max_age_s: int) -> float:
    """Value of `col` at the last grid minute <= kickoff - horizon, if fresh enough."""
    t = kickoff_ts - horizon_s
    sub = g[g["ts"] <= t]
    if sub.empty or t - int(sub["ts"].iloc[-1]) > max_age_s:
        return np.nan
    row = sub.iloc[-1]
    if age_col and (pd.isna(row[age_col]) or row[age_col] > max_age_s):
        return np.nan
    return float(row[col])


def game_table(gaps: pd.DataFrame, matches: pd.DataFrame, games: pd.DataFrame, max_age_s: int = 1800) -> pd.DataFrame:
    """One row per game: home-team probability from each source at each horizon + outcome."""
    home = matches.merge(games[["game_id", "home_team", "kickoff_ts", "home_score", "away_score", "home_fair_prob",
                                "split", "week", "game_type"]], on="game_id")
    home = home[home["team_abbr"] == home["home_team"]]
    rows = []
    for m in home.itertuples():
        g = gaps[gaps["match_id"] == m.match_id].sort_values("ts")
        if g.empty or pd.isna(m.home_score):
            continue
        y = 1.0 if m.home_score > m.away_score else 0.0 if m.home_score < m.away_score else 0.5
        r = {"game_id": m.game_id, "split": m.split, "week": m.week, "y": y, "closing_line": m.home_fair_prob}
        for h, s in HORIZONS_S.items():
            r[f"poly_{h}"] = snapshot(g, m.kickoff_ts, s, "p_ref", "p_age_s", max_age_s)
            r[f"kalshi_{h}"] = snapshot(g, m.kickoff_ts, s, "k_mid", "k_age_s", max_age_s)
        rows.append(r)
    return pd.DataFrame(rows)


def scores(tbl: pd.DataFrame) -> pd.DataFrame:
    """Brier / log loss per source and horizon, ties excluded, on games where all sources exist."""
    t = tbl[tbl["y"] != 0.5]
    out = []
    for h in HORIZONS_S:
        cols = [f"poly_{h}", f"kalshi_{h}"] + (["closing_line"] if h == "kickoff" else [])
        sub = t.dropna(subset=cols)
        for c in cols:
            p, y = sub[c].to_numpy(float), sub["y"].to_numpy(float)
            out.append({"horizon": h, "source": c.split("_")[0] if c != "closing_line" else "closing_line",
                        "n_games": len(sub), "brier": brier(p, y), "log_loss": log_loss(p, y)})
    return pd.DataFrame(out)
