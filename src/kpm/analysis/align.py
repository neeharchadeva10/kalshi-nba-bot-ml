"""Put a matched Kalshi/Polymarket pair on one 1-minute UTC grid, pre-game only.

Grid timestamps are minute END times T. Kalshi values at T are the close of the candle
ending at T (carried forward through minutes with no candle). Polymarket values at T are
the last point with t <= T (as-of backward join, so there is no look-ahead).
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from kpm.core.prices import clean_quote


def kalshi_frame(conn: sqlite3.Connection, market_id: int) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT ts, yes_bid, yes_ask, volume, price_last FROM price_history "
        "WHERE market_id=? AND source='kalshi_candle' ORDER BY ts", conn, params=(market_id,))
    if df.empty:
        return df
    q = [clean_quote(b, a) for b, a in zip(df["yes_bid"], df["yes_ask"])]
    df["yes_bid"] = [x[0] for x in q]
    df["yes_ask"] = [x[1] for x in q]
    return df


def poly_frame(conn: sqlite3.Connection, market_id: int, source: str) -> pd.DataFrame:
    """Polymarket points plus when the price last changed.

    prices-history emits a point every minute even when nothing trades, so point age alone
    cannot detect a dormant book; p_change_ts (last time the price moved) can.
    """
    p = pd.read_sql_query(
        "SELECT ts AS p_ts, price_mean AS p_ref FROM price_history WHERE market_id=? AND source=? ORDER BY ts",
        conn, params=(market_id, source))
    if not p.empty:
        changed = p["p_ref"].diff().fillna(1.0).abs() > 1e-9
        p["p_change_ts"] = p["p_ts"].where(changed).ffill()
    return p


def aligned(conn: sqlite3.Connection, kalshi_id: int, poly_id: int, poly_source: str, kickoff_ts: int,
            grid_s: int = 60) -> pd.DataFrame:
    k = kalshi_frame(conn, kalshi_id)
    p = poly_frame(conn, poly_id, poly_source)
    if k.empty or p.empty:
        return pd.DataFrame()
    start = max(int(k["ts"].min()), int(np.ceil(p["p_ts"].min() / grid_s) * grid_s))
    end = kickoff_ts - (kickoff_ts % grid_s)  # last minute END at or before kickoff
    if start > end:
        return pd.DataFrame()
    grid = pd.DataFrame({"ts": np.arange(start, end + 1, grid_s, dtype=np.int64)})

    k = k.copy()
    k["k_quote_ts"] = np.where(k["yes_bid"].notna() | k["yes_ask"].notna(), k["ts"], np.nan)
    g = grid.merge(k, on="ts", how="left")
    g["volume"] = g["volume"].fillna(0.0)
    for c in ("yes_bid", "yes_ask", "k_quote_ts"):
        g[c] = g[c].ffill()
    g["k_age_s"] = g["ts"] - g["k_quote_ts"]

    g = pd.merge_asof(g.sort_values("ts"), p.sort_values("p_ts"), left_on="ts", right_on="p_ts",
                      direction="backward")
    g["p_age_s"] = g["ts"] - g["p_ts"]
    g["p_unchanged_s"] = g["ts"] - g["p_change_ts"]
    g["k_mid"] = (g["yes_bid"] + g["yes_ask"]) / 2.0
    g["k_spread"] = g["yes_ask"] - g["yes_bid"]
    g["k_volume_1h"] = g["volume"].rolling(60, min_periods=1).sum()
    g["mins_to_kickoff"] = (kickoff_ts - g["ts"]) / 60.0
    return g.drop(columns=["k_quote_ts", "p_ts", "p_change_ts"])
