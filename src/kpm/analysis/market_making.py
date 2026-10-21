"""Market-making backtest on Kalshi NFL game-winner markets (passive quotes, maker fees).

Each minute t we decide quotes from that minute's book; fills come from the trades printed in (t, t+60]:
a taker SELLING YES (taker_side 'no') can fill our bid if it traded at or below our bid price; a taker
BUYING YES (taker_side 'yes') can fill our ask if it traded at or above our ask price.

Fill rules (queue position is unknown in the data):
  generous   : any eligible print fills us (as if always first in line)
  realistic  : an improved quote (a new best price) is first in line; a joined quote fills only if
               eligible volume >= 5x our size
  strict     : only prints strictly through our price fill us

Quoting: improve the best bid/ask by 1c when the spread is >= 2c; optionally join at the best price when it
is 1c wide. Never quote a bid above fair - guard or an ask below fair + guard. Inventory is capped. At the
end the position is flattened at the market (taker fee), or held to settlement.
"""
from __future__ import annotations

import itertools
import json
import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from kpm.analysis.edge_hunt import kalshi_game, load
from kpm.analysis.strategies import cluster_bootstrap_ci, max_drawdown
from kpm.core import audit, config, db
from kpm.core.config import settings
from kpm.core.fees import FeeModel

log = logging.getLogger(__name__)
TICK = 0.01
EPS = 1e-9
RULES = ("generous", "realistic", "strict")


@dataclass(frozen=True)
class MMParams:
    mode: str           # improve_only | join_and_improve
    guard: float        # min distance between our quote and fair
    stop_h: float       # stop quoting this many hours before kickoff
    inv_limit: int = 1000
    size: int = 100

    def label(self) -> str:
        return f"{self.mode}|guard={self.guard:.3f}|stop={self.stop_h:g}h"


def our_quotes(bid: float, ask: float, fair: float, p: MMParams) -> tuple[float | None, float | None, bool, bool]:
    """Returns (our_bid, our_ask, bid_improved, ask_improved)."""
    if np.isnan(bid) or np.isnan(ask) or np.isnan(fair) or ask - bid < TICK - EPS:
        return None, None, False, False
    wide = ask - bid >= 2 * TICK - EPS
    if wide:
        qb, qa, imp = bid + TICK, ask - TICK, True
    elif p.mode == "join_and_improve":
        qb, qa, imp = bid, ask, False
    else:
        return None, None, False, False
    qb = qb if qb <= fair - p.guard + EPS else None
    qa = qa if qa >= fair + p.guard - EPS else None
    return qb, qa, imp and qb is not None, imp and qa is not None


def fill_qty(eligible: float, through: float, improved: bool, rule: str, size: int) -> int:
    """Contracts filled out of `size` given eligible volume (at or better than our price) and
    volume strictly through our price."""
    if rule == "generous":
        return int(min(size, eligible))
    if rule == "strict":
        return int(min(size, through))
    if improved:  # realistic: new best price -> first in line
        return int(min(size, eligible))
    return size if eligible >= 5 * size else 0


def event_table(g: pd.DataFrame, side: str, trades: pd.DataFrame) -> pd.DataFrame:
    """Per trade-minute m: the book at m-60 and the eligible/through volumes for the candidate quotes."""
    if trades.empty:
        return pd.DataFrame()
    bid, ask = g[f"bid_{side}"].to_numpy(), g[f"ask_{side}"].to_numpy()
    fair = g["fair_A"].to_numpy() if side == "A" else 1.0 - g["fair_A"].to_numpy()
    idx = pd.Series(np.arange(len(g)), index=g["ts"].to_numpy())
    t = trades.copy()
    t["post_ts"] = t["minute_ts"] - 60
    t = t[t["post_ts"].isin(idx.index)]
    if t.empty:
        return pd.DataFrame()
    t["i"] = idx.loc[t["post_ts"]].to_numpy()
    t["bid"], t["ask"] = bid[t["i"]], ask[t["i"]]
    sell = t["taker_side"] == "no"
    buy = t["taker_side"] == "yes"
    p, c = t["yes_price"], t["count"]
    cols = {
        "sell_le_bid": c.where(sell & (p <= t["bid"] + EPS), 0),
        "sell_lt_bid": c.where(sell & (p < t["bid"] - EPS), 0),
        "sell_le_bid1": c.where(sell & (p <= t["bid"] + TICK + EPS), 0),
        "buy_ge_ask": c.where(buy & (p >= t["ask"] - EPS), 0),
        "buy_gt_ask": c.where(buy & (p > t["ask"] + EPS), 0),
        "buy_ge_ask1": c.where(buy & (p >= t["ask"] - TICK - EPS), 0),
    }
    ev = pd.DataFrame(cols).assign(i=t["i"]).groupby("i").sum().reset_index()
    ev["bid"], ev["ask"], ev["fair"] = bid[ev["i"]], ask[ev["i"]], fair[ev["i"]]
    ev["mins_to_kickoff"] = g["mins_to_kickoff"].to_numpy()[ev["i"]]
    return ev.sort_values("i").reset_index(drop=True)


def simulate(ev: pd.DataFrame, g: pd.DataFrame, side: str, settle: float, p: MMParams, rule: str,
             fee: FeeModel) -> dict:
    """Run one market. Returns PnL (flatten and hold), fills, and markouts."""
    fair_all = g["fair_A"].to_numpy() if side == "A" else 1.0 - g["fair_A"].to_numpy()
    q, cash, fills, volume = 0, 0.0, 0, 0
    marks = {1: [], 15: [], 60: []}
    for r in ev.itertuples(index=False):
        if r.mins_to_kickoff < p.stop_h * 60:
            break
        qb, qa, ib, ia = our_quotes(r.bid, r.ask, r.fair, p)
        trades_at = []
        if qb is not None and q < p.inv_limit:
            elig = r.sell_le_bid1 if ib else r.sell_le_bid
            thru = r.sell_le_bid if ib else r.sell_lt_bid  # through an improved bid = prints at the old bid or lower
            n = min(fill_qty(elig, thru, ib, rule, p.size), p.inv_limit - q)
            if n > 0:
                q += n
                cash -= qb * n + fee.order_fee(qb, n, maker=True)
                trades_at.append((+1, qb, n))
        if qa is not None and q > -p.inv_limit:
            elig = r.buy_ge_ask1 if ia else r.buy_ge_ask
            thru = r.buy_ge_ask if ia else r.buy_gt_ask
            n = min(fill_qty(elig, thru, ia, rule, p.size), q + p.inv_limit)
            if n > 0:
                q -= n
                cash += qa * n - fee.order_fee(qa, n, maker=True)
                trades_at.append((-1, qa, n))
        for sgn, px, n in trades_at:
            fills += 1
            volume += n
            for h in marks:  # markout: fair value h minutes after the fill vs our fill price, per contract
                j = min(int(r.i) + 1 + h, len(fair_all) - 1)
                if not np.isnan(fair_all[j]):
                    marks[h].append((sgn * (fair_all[j] - px), n))
    # flatten at the market on the last minute we were allowed to quote (taker), or hold to settlement
    end_i = len(g) - 1 if p.stop_h == 0 else int(np.searchsorted(-g["mins_to_kickoff"].to_numpy(), -p.stop_h * 60))
    end_i = min(max(end_i, 0), len(g) - 1)
    bid_end = g[f"bid_{side}"].iloc[: end_i + 1].dropna()
    ask_end = g[f"ask_{side}"].iloc[: end_i + 1].dropna()
    if q > 0 and len(bid_end):
        b = float(bid_end.iloc[-1])
        flat = cash + q * b - fee.order_fee(b, q)
    elif q < 0 and len(ask_end):
        a = float(ask_end.iloc[-1])
        flat = cash - (-q) * a - fee.order_fee(a, -q)
    else:
        flat = cash
    hold = cash + q * (np.nan if settle is None else settle)
    mk = {f"markout_{h}m_c": (100 * np.average([x for x, _ in v], weights=[w for _, w in v]) if v else np.nan)
          for h, v in marks.items()}
    return {"pnl_flatten": flat, "pnl_hold": hold, "fills": fills, "volume": volume, "end_inventory": q, **mk}


def prepare(cfg: dict) -> tuple[dict, dict, dict, pd.DataFrame]:
    """Per-market event tables. Study-season games split early/later by week; other seasons are
    'fresh_<season>' (never used to choose anything)."""
    with db.session() as conn:
        games, km = load(conn)
        trades_1m = pd.read_sql_query("SELECT market_id, minute_ts, yes_price, taker_side, count FROM trades_1m", conn)
        frames = {}
        for gid, rows in km.groupby("game_id"):
            ko = int(games.loc[games.game_id == gid, "kickoff_ts"].iloc[0])
            g = kalshi_game(conn, rows, ko)
            if g is not None:
                frames[gid] = (g, rows.sort_values("team_abbr").reset_index(drop=True))
    trades = {k: t for k, t in trades_1m.groupby("market_id")}
    gi = games.set_index("game_id")

    def split_of(gid: str) -> str:
        if int(gi.at[gid, "season"]) != cfg["season"]:
            return f"fresh_{int(gi.at[gid, 'season'])}"
        early = gi.at[gid, "game_type"] == "REG" and gi.at[gid, "week"] <= cfg["split"]["dev_last_week"]
        return "early" if early else "later"

    split = {gid: split_of(gid) for gid in frames}
    events = {}
    for gid, (g, rows) in frames.items():
        for side, r in zip("AB", rows.itertuples()):
            events[(gid, side)] = (event_table(g, side, trades.get(int(r.market_id), pd.DataFrame())),
                                   None if pd.isna(r.settlement_value) else float(r.settlement_value))
    return frames, events, split, gi


def simulate_grid(grid: list[MMParams], rules, frames, events, split, gi, fee) -> pd.DataFrame:
    rows_out = []
    for p_, rule in itertools.product(grid, rules):
        for (gid, side), (ev, settle) in events.items():
            if ev.empty:
                continue
            res = simulate(ev, frames[gid][0], side, settle, p_, rule, fee)
            rows_out.append({"params": p_.label(), "inv_limit": p_.inv_limit, "rule": rule, "game_id": gid,
                             "side": side, "split": split[gid], "kickoff_ts": int(gi.at[gid, "kickoff_ts"]), **res})
    return pd.DataFrame(rows_out)


def agg(d: pd.DataFrame) -> dict:
    d = d.sort_values("kickoff_ts")
    v, g_ = d["pnl_flatten"].to_numpy(float), d["game_id"].to_numpy()
    lo, hi = cluster_bootstrap_ci(v, g_, 1000) if len(v) else (np.nan, np.nan)
    vol = d["volume"].sum()
    w = d["volume"].where(d["volume"] > 0)
    return {"markets": len(d), "games": d["game_id"].nunique(), "fills": int(d["fills"].sum()),
            "contracts": int(vol), "total_usd_flatten": float(v.sum()), "total_usd_hold": float(d["pnl_hold"].sum()),
            "per_market_usd": float(v.mean()), "ci95_per_market_usd": (round(lo, 2), round(hi, 2)),
            "edge_c_per_contract": 100 * float(v.sum() / vol) if vol else np.nan,
            "pct_markets_profitable": 100 * float((v > 0).mean()),
            "max_drawdown_usd": max_drawdown(v),
            **{f"markout_{h}m_c": float(np.average(d[f"markout_{h}m_c"].dropna(),
                                                   weights=w[d[f"markout_{h}m_c"].notna()].fillna(0) + 1e-12))
               if d[f"markout_{h}m_c"].notna().any() else np.nan for h in (1, 15, 60)}}


def summarize_grid(allres: pd.DataFrame) -> pd.DataFrame:
    rows = [{"params": params, "inv_limit": inv, "rule": rule, "split": sp, **agg(d)}
            for (params, inv, rule, sp), d in allres.groupby(["params", "inv_limit", "rule", "split"])]
    return pd.DataFrame(rows)


def robustness() -> pd.DataFrame:
    """Neighbourhood of the chosen setting (improve_only, 1c guard, quote to kickoff, 1,000 cap): guards
    0.75-2c, and inventory caps 500/2,000. Nothing here is used to choose; it shows whether the 1c result
    is a lucky point."""
    cfg = settings()
    fee = FeeModel.from_config(cfg)
    out = config.path("report_dir") / "market_making"
    out.mkdir(parents=True, exist_ok=True)
    frames, events, split, gi = prepare(cfg)
    grid = [MMParams("improve_only", gd, 0.0) for gd in (0.0075, 0.01, 0.0125, 0.015, 0.02)]
    grid += [MMParams("improve_only", 0.01, 0.0, inv_limit=lim) for lim in (500, 2000)]
    allres = simulate_grid(grid, RULES, frames, events, split, gi, fee)
    allres.to_csv(out / "robustness_per_market.csv", index=False)
    summ = summarize_grid(allres)
    summ.to_csv(out / "robustness.csv", index=False)
    log.info("market-making robustness done; %d games incl. splits %s", len(frames), sorted(set(split.values())))
    return summ


def run() -> dict:
    cfg = settings()
    fee = FeeModel.from_config(cfg)
    out = config.path("report_dir") / "market_making"
    out.mkdir(parents=True, exist_ok=True)
    frames, events, split, gi = prepare(cfg)
    grid = [MMParams(m, gd, st) for m, gd, st in itertools.product(
        ("improve_only", "join_and_improve"), (0.0, 0.005, 0.01), (0.0, 3.0))]
    allres = simulate_grid(grid, RULES, frames, events, split, gi, fee)
    allres.to_csv(out / "per_market.csv", index=False)
    summ = summarize_grid(allres)
    summ.to_csv(out / "summary_grid.csv", index=False)
    early_real = summ[(summ["rule"] == "realistic") & (summ["split"] == "early")]
    best = early_real.sort_values("total_usd_flatten", ascending=False).iloc[0]
    chosen = summ[summ["params"] == best["params"]].sort_values(["rule", "split"])
    result = {"chosen_params": best["params"],
              "how_chosen": "highest early-week total PnL (flatten) under the realistic fill rule",
              "chosen": chosen.drop(columns=["params"]).to_dict("records"),
              "early_realistic_sets_profitable": int((early_real["total_usd_flatten"] > 0).sum()),
              "early_realistic_sets": int(len(early_real))}
    (out / "summary.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    with db.session() as conn:
        audit.log(conn, "analysis.market_making", "complete", None, None, {"chosen": best["params"]})
    log.info("market-making backtest done; results in %s", out)
    return result
