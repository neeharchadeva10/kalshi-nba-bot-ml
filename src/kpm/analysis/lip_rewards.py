"""H4 step 1 (pre-registered): static economics of Kalshi's Liquidity Incentive Program (LIP).

Kalshi rules (help.kalshi.com, LIP article; CFTC filing 2026-07-15):
- Each program pays period_reward (centi-cents) for its period, split by score; both sides count separately: a
  snapshot score is your share of the YES-bid side plus your share of the NO-bid side.
- Reference price (per side): walking down from the best bid, the first level where cumulative size reaches 1/5 of the
  target size. Orders at or better than it score full size; lower ones size x discount^(ticks below).
- A snapshot counts only if both sides have at least the target size resting.
Estimate: our q contracts resting at each side's best bid; reward = period reward x 1/2 x (our YES share + our NO
share), scaled per day, zero if the book does not currently meet the target on both sides. Collateral: q x (YES bid +
NO bid). This is BEFORE fills: adverse selection is measured in the forward test (step 2).
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from kpm.clients.kalshi import KalshiClient
from kpm.core import audit, config, db
from kpm.core.timeutil import parse_iso

log = logging.getLogger(__name__)
COMPONENT = "analysis.lip_rewards"
TICK = 0.01


def fetch_programs(client: KalshiClient) -> pd.DataFrame:
    rows, cursor = [], None
    while True:
        page = client.http.get("/incentive_programs", {"status": "active", "type": "liquidity", "limit": 10000,
                                                       "cursor": cursor}, use_cache=False)
        rows += page.get("incentive_programs", [])
        cursor = page.get("next_cursor")
        if not cursor:
            break
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["reward_usd"] = df["period_reward"].astype(float) / 10_000          # centi-cents -> dollars
    df["hours"] = (df["end_date"].map(parse_iso) - df["start_date"].map(parse_iso)) / 3600
    df["reward_per_day"] = df["reward_usd"] * 24 / df["hours"].clip(lower=1)
    df["target"] = df["target_size_fp"].astype(float)
    df["discount"] = df["discount_factor_bps"].astype(float) / 10_000
    return df


def side_weights(levels: list[tuple[float, float]], target: float, discount: float) -> tuple[float, float, float]:
    """levels best-first [(price, qty)]. Returns (weighted size, reference price, total depth)."""
    if not levels:
        return 0.0, np.nan, 0.0
    cum, ref = 0.0, levels[-1][0]
    for px, q in levels:
        cum += q
        if cum >= target / 5:
            ref = px
            break
    w = sum(q * (1.0 if px >= ref - 1e-9 else discount ** round((ref - px) / TICK)) for px, q in levels)
    return w, ref, sum(q for _, q in levels)


def our_share(levels: list[tuple[float, float]], q: float, target: float, discount: float) -> float:
    """Share of a side's weighted size if we add q contracts at its best bid."""
    if not levels:
        return 0.0
    best = levels[0][0]
    new = [(best, levels[0][1] + q)] + levels[1:]
    w, _, _ = side_weights(new, target, discount)
    return q / w if w > 0 else 0.0


def book_levels(ob: dict, side: str) -> list[tuple[float, float]]:
    raw = ob.get(f"{side}_dollars") or []
    lv = [(float(p), float(q)) for p, q in raw]
    return sorted(lv, key=lambda x: -x[0])   # best (highest) bid first


def run(top: int = 200, sizes: tuple[int, ...] = (10, 50, 100)) -> dict:
    out = config.path("report_dir") / "mechanical_edges"
    out.mkdir(parents=True, exist_ok=True)
    with db.session() as conn:
        client = KalshiClient(conn)
        progs = fetch_programs(client)
        if progs.empty:
            log.warning("no active liquidity programs")
            return {"programs": 0}
        cand = progs.sort_values("reward_per_day", ascending=False).head(top)

        def book(tk):
            return tk, (client.http.get(f"/markets/{tk}/orderbook", use_cache=False).get("orderbook_fp") or {})

        books = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for f in as_completed([pool.submit(book, t) for t in cand["market_ticker"]]):
                try:
                    tk, ob = f.result()
                    books[tk] = ob
                except Exception as exc:
                    log.warning("orderbook failed: %s", exc)
        client.http.flush_index(conn)
    rows = []
    for p in cand.itertuples():
        ob = books.get(p.market_ticker)
        if ob is None:
            continue
        yes, no = book_levels(ob, "yes"), book_levels(ob, "no")
        wy, ry, dy = side_weights(yes, p.target, p.discount)
        wn, rn, dn = side_weights(no, p.target, p.discount)
        counts = dy >= p.target and dn >= p.target
        yb, nb = (yes[0][0] if yes else np.nan), (no[0][0] if no else np.nan)
        row = {"market": p.market_ticker, "reward_per_day": p.reward_per_day, "period_reward_usd": p.reward_usd,
               "period_hours": p.hours, "target": p.target, "discount": p.discount, "yes_bid": yb, "no_bid": nb,
               "spread": 1 - (yb + nb) if yes and no else np.nan, "yes_depth": dy, "no_depth": dn,
               "yes_weighted": wy, "no_weighted": wn, "book_meets_target": counts, "end": p.end_date}
        for q in sizes:
            share = 0.5 * (our_share(yes, q, p.target, p.discount) + our_share(no, q, p.target, p.discount))
            collateral = q * ((yb if yes else 0) + (nb if no else 0))
            reward = p.reward_per_day * share * (1.0 if counts else 0.0)
            row[f"reward_day_q{q}"] = reward
            row[f"collateral_q{q}"] = collateral
            row[f"pct_per_day_q{q}"] = 100 * reward / collateral if collateral > 0 else np.nan
        rows.append(row)
    tab = pd.DataFrame(rows).sort_values("pct_per_day_q50", ascending=False)
    tab.to_csv(out / "h4_lip_static.csv", index=False)
    by_series = progs.assign(series=progs["market_ticker"].str.split("-").str[0]).groupby("series")["reward_per_day"] \
        .agg(["count", "sum"]).sort_values("sum", ascending=False)
    by_series.to_csv(out / "h4_lip_programs_by_series.csv")
    res = {"active_programs": int(len(progs)), "reward_per_day_total_usd": float(progs["reward_per_day"].sum()),
           "checked_books": int(len(tab)), "meeting_target_now": int(tab["book_meets_target"].sum()),
           "shortlist_q50_ge_0.5pct": int((tab["pct_per_day_q50"] >= 0.5).sum())}
    (out / "h4_lip_summary.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    with db.session() as conn:
        audit.log(conn, COMPONENT, "static", None, None, res)
    log.info("H4 LIP static: %s", res)
    return res
