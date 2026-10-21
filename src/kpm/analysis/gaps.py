"""Step 6: after-fee edges for buying YES or NO on Kalshi, using the Polymarket price as fair.

  edge_yes = p_ref - yes_ask - fee(yes_ask)
  edge_no  = (1 - p_ref) - no_ask - fee(no_ask),  where no_ask = 1 - yes_bid
"""
from __future__ import annotations

import json
import logging
import subprocess

import numpy as np
import pandas as pd

from kpm import __version__
from kpm.analysis.align import aligned
from kpm.core import audit, db
from kpm.core.config import settings
from kpm.core.fees import FeeModel
from kpm.core.timeutil import now_ts

log = logging.getLogger(__name__)


def compute_edges(p_ref, yes_bid, yes_ask, fee: FeeModel, order_size: float) -> dict:
    """Vectorized edge calculation. Inputs may be scalars or arrays (NaN = missing)."""
    p = np.asarray(p_ref, dtype=float)
    bid = np.asarray(yes_bid, dtype=float)
    ask = np.asarray(yes_ask, dtype=float)
    no_ask = 1.0 - bid
    fee_yes = fee.per_contract_array(ask, order_size)
    fee_no = fee.per_contract_array(no_ask, order_size)
    return {
        "fee_yes": fee_yes, "fee_no": fee_no,
        "edge_yes": p - ask - fee_yes,
        "edge_no": (1.0 - p) - no_ask - fee_no,
    }


def ttk_bucket(seconds_to_kickoff, buckets: dict) -> np.ndarray:
    s = np.asarray(seconds_to_kickoff, dtype=float)
    out = np.full(s.shape, None, dtype=object)
    for label, (lo, hi) in buckets.items():
        out[(s >= float(lo)) & (s < float(hi))] = label
    return out


def _git_rev() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              timeout=5).stdout.strip() or __version__
    except Exception:
        return __version__


def match_frame(conn, m: dict, cfg: dict, fee: FeeModel) -> pd.DataFrame:
    a = cfg["analysis"]
    g = aligned(conn, m["kalshi_market_id"], m["poly_market_id"], m["poly_source"], m["kickoff_ts"], a["grid_s"])
    if g.empty:
        return g
    e = compute_edges(g["p_ref"], g["yes_bid"], g["yes_ask"], fee, cfg["fees"]["order_size"])
    for k, v in e.items():
        g[k] = v
    stale = (g["p_age_s"] > a["poly_max_staleness_s"]) | (g["k_age_s"] > a["kalshi_max_staleness_s"])
    stale |= g["p_ref"].isna()
    g.loc[stale | g["yes_ask"].isna(), ["edge_yes"]] = np.nan
    g.loc[stale | g["yes_bid"].isna(), ["edge_no"]] = np.nan
    g["ttk_bucket"] = ttk_bucket(g["mins_to_kickoff"] * 60, a["ttk_buckets"])
    g["is_favorite"] = int(m["team_fair_prob"] > 0.5) if m["team_fair_prob"] is not None else None
    g["match_id"] = m["match_id"]
    return g


def approved_matches(conn) -> list[dict]:
    q = """SELECT mm.match_id, mm.game_id, mm.kalshi_market_id, mm.poly_market_id, g.kickoff_ts, g.home_team,
                  g.home_fair_prob, k.team_abbr, p.price_source AS poly_source, p.price_resolution_s
           FROM market_matches mm JOIN games g USING (game_id)
           JOIN markets k ON k.market_id = mm.kalshi_market_id
           JOIN markets p ON p.market_id = mm.poly_market_id
           WHERE mm.status = 'approved' AND p.platform = 'polymarket' ORDER BY g.kickoff_ts, mm.match_id"""
    out = []
    for r in conn.execute(q):
        r = dict(r)
        hp = r["home_fair_prob"]
        r["team_fair_prob"] = None if hp is None else (hp if r["team_abbr"] == r["home_team"] else 1 - hp)
        out.append(r)
    return out


GAP_COLS = ["run_id", "match_id", "ts", "yes_bid", "yes_ask", "k_mid", "k_age_s", "p_ref", "p_age_s", "p_unchanged_s",
            "fee_yes", "fee_no", "edge_yes", "edge_no", "mins_to_kickoff", "ttk_bucket", "k_volume_1h", "k_spread",
            "is_favorite"]


def run() -> int:
    cfg = settings()
    fee = FeeModel.from_config(cfg)
    with db.session() as conn:
        matches = approved_matches(conn)
        if not matches:
            raise RuntimeError("no approved matches: run match / review / gate first")
        run_cfg = {"fees": cfg["fees"], "analysis": cfg["analysis"], "split": cfg["split"],
                   "matcher": {k: cfg["matcher"][k] for k in ("version", "approve_threshold")}}
        cur = conn.execute("INSERT INTO analysis_runs (created_ts, config_json, code_version) VALUES (?,?,?)",
                           (now_ts(), json.dumps(run_cfg, sort_keys=True), _git_rev()))
        run_id = cur.lastrowid
        conn.commit()
        total = 0
        for i, m in enumerate(matches, 1):
            g = match_frame(conn, m, cfg, fee)
            if g.empty:
                audit.log(conn, "analysis.gaps", "empty_series", "match", m["match_id"], {})
                continue
            g["run_id"] = run_id
            g = g[GAP_COLS]
            rows = g.astype(object).where(g.notna(), None).values.tolist()
            conn.executemany(
                "INSERT INTO gaps (run_id, match_id, ts, k_yes_bid, k_yes_ask, k_mid, k_age_s, p_ref, p_age_s, "
                "p_unchanged_s, fee_yes, fee_no, edge_yes, edge_no, mins_to_kickoff, ttk_bucket, k_volume_1h, "
                "k_spread, is_favorite) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            total += len(rows)
            conn.commit()  # short write transactions: collectors may be writing concurrently
            if i % 50 == 0:
                log.info("gaps: %d/%d matches, %d rows", i, len(matches), total)
        audit.log(conn, "analysis.gaps", "run_complete", "run", run_id, {"matches": len(matches), "rows": total})
        log.info("gaps run %d: %d rows over %d matches", run_id, total, len(matches))
        return run_id
