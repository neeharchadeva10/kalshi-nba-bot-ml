"""Structural mispricing scanner for all open Kalshi events (no predictions, public data only).

Checks per event:
  ladder_greater : "above K" markets. For K1 < K2, YES(>K2) implies YES(>K1), so price(>K1) >= price(>K2).
                   If bid(>K2) > ask(>K1): buy YES >K1 at the ask + buy NO >K2 (= sell YES at the bid).
                   Every outcome pays at least $1, so worst-case profit = bid(>K2) - ask(>K1) - fees.
  ladder_less    : "below K" markets, mirror image: for K1 < K2, price(<K1) <= price(<K2).
  sell_all       : mutually exclusive event (at most one YES). Buy every NO (= sell every YES at its bid):
                   worst-case payout n-1, cost sum(1 - bid) -> profit = sum(bid) - 1 - fees. Riskless.
  buy_all        : buy every YES at its ask -> pays exactly $1 only if the outcomes are exhaustive;
                   profit = 1 - sum(ask) - fees. Flagged 'exhaustive: verified' only for contiguous strike sets.
Fees: Kalshi taker formula on every leg (series multiplier taken from the series when available).
Size: the thinnest leg's displayed top-of-book size caps the number of sets.
"""
from __future__ import annotations

import json
import logging
import re
import time

import numpy as np
import pandas as pd

from kpm.clients.kalshi import KalshiClient
from kpm.core import audit, db
from kpm.core.fees import FeeModel
from kpm.core.prices import parse_dollars

log = logging.getLogger(__name__)
EPS = 1e-9


def fetch_open_markets(client: KalshiClient) -> pd.DataFrame:
    """All open events with their markets (live, never served from cache)."""
    rows, cursor = [], None
    while True:
        page = client.http.get("/events", {"status": "open", "with_nested_markets": "true", "limit": 200,
                                           "cursor": cursor}, use_cache=False)
        for ev in page.get("events", []):
            for m in ev.get("markets", []) or []:
                if m.get("status") not in ("active", "open"):
                    continue
                rows.append({
                    "event_ticker": ev["event_ticker"], "series_ticker": ev.get("series_ticker"),
                    "mutually_exclusive": bool(ev.get("mutually_exclusive")), "event_title": ev.get("title"),
                    "ticker": m["ticker"], "title": m.get("yes_sub_title") or m.get("title"),
                    "strike_type": m.get("strike_type"), "floor": m.get("floor_strike"), "cap": m.get("cap_strike"),
                    "custom_strike": json.dumps(m["custom_strike"], sort_keys=True) if m.get("custom_strike") else "",
                    "close_time": m.get("close_time"),
                    "bid": parse_dollars(m.get("yes_bid_dollars")), "ask": parse_dollars(m.get("yes_ask_dollars")),
                    "bid_size": parse_dollars(m.get("yes_bid_size_fp")), "ask_size": parse_dollars(m.get("yes_ask_size_fp")),
                })
        cursor = page.get("cursor")
        if not cursor:
            break
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # no bid -> 0, no ask -> 1 (an empty side cannot be traded against)
    df["bid"] = df["bid"].fillna(0.0)
    df["ask"] = df["ask"].fillna(1.0)
    df[["bid_size", "ask_size"]] = df[["bid_size", "ask_size"]].fillna(0.0)
    return df


def _fee(fee: FeeModel, price: float, size: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    return fee.per_contract(price, max(1.0, min(size, 100.0)))


def ladder_key(ev: pd.DataFrame) -> pd.Series:
    """Markets on the same underlying share all three of: the ticker apart from its trailing strike number
    ('KXNFLREC-...-NOJJOHNSON83-2' -> 'KXNFLREC-...-NOJJOHNSON83-'), Kalshi's custom_strike (player/team ids) and
    the subtitle wording with numbers masked ('Juwan Johnson: 2+' -> 'Juwan Johnson: #+'). One event can hold many
    underlyings (players, teams, cities), and comparing across them is meaningless."""
    blank = pd.Series("", index=ev.index)
    stem = ev["ticker"].str.replace(r"[\d.]+$", "", regex=True)
    title = ev.get("title", blank).fillna("").astype(str).str.replace(r"\d[\d.,]*", "#", regex=True)
    return stem + "|" + ev.get("custom_strike", blank).fillna("").astype(str) + "|" + title


def check_ladder(ev: pd.DataFrame, fee: FeeModel, kind: str) -> list[dict]:
    """Monotonicity violations in strike ladders, one ladder per underlying. kind: 'greater' or 'less'."""
    out = []
    for _, grp in ev.groupby(ladder_key(ev)):
        out += _check_one_ladder(grp, fee, kind)
    return out


def _check_one_ladder(ev: pd.DataFrame, fee: FeeModel, kind: str) -> list[dict]:
    # a true tail has one bound only: some events label exact-count buckets 'less' with floor == cap
    if kind == "greater":
        d = ev[ev["strike_type"].isin(["greater", "greater_or_equal"]) & ev["floor"].notna() & ev["cap"].isna()]
        d = d.sort_values("floor")
        strikes = d["floor"].to_numpy(float)
    else:
        d = ev[ev["strike_type"].isin(["less", "less_or_equal"]) & ev["cap"].notna() & ev["floor"].isna()]
        d = d.sort_values("cap")
        strikes = d["cap"].to_numpy(float)
    out = []
    if len(d) < 2:
        return out
    bid, ask = d["bid"].to_numpy(float), d["ask"].to_numpy(float)
    bsz, asz = d["bid_size"].to_numpy(float), d["ask_size"].to_numpy(float)
    tick = d["ticker"].to_numpy()
    for j in range(1, len(d)):
        for i in range(j):
            if strikes[i] >= strikes[j] - EPS:   # only strictly increasing strikes form a valid ladder pair
                continue
            if kind == "greater":   # buy YES at lower strike i, sell YES at higher strike j
                buy, sell = i, j
            else:                   # buy YES at higher strike j, sell YES at lower strike i
                buy, sell = j, i
            if bid[sell] <= ask[buy] + EPS or ask[buy] >= 1 or bid[sell] <= 0:
                continue
            sets = min(asz[buy], bsz[sell])
            gross = bid[sell] - ask[buy]
            net = gross - _fee(fee, ask[buy], sets) - _fee(fee, 1 - bid[sell], sets)
            if net > EPS:
                out.append({"kind": f"ladder_{kind}", "profit_per_set": net, "max_sets": sets,
                            "legs": [{"ticker": tick[buy], "action": "buy YES", "price": ask[buy], "size": asz[buy]},
                                     {"ticker": tick[sell], "action": "buy NO (sell YES)", "price": 1 - bid[sell],
                                      "size": bsz[sell]}],
                            "details": {"strikes": [strikes[buy], strikes[sell]], "gross": gross}})
    return out


def is_exhaustive_strike_set(ev: pd.DataFrame) -> bool:
    """True when strikes tile the whole line: one 'less' tail, contiguous 'between' buckets, one 'greater' tail."""
    less = ev[ev["strike_type"].isin(["less", "less_or_equal"])]
    more = ev[ev["strike_type"].isin(["greater", "greater_or_equal"])]
    mid = ev[ev["strike_type"] == "between"].sort_values("floor")
    if len(less) != 1 or len(more) != 1 or mid.empty:
        return False
    edges = [float(less["cap"].iloc[0])]
    for r in mid.itertuples():
        if abs(float(r.floor) - edges[-1]) > 0.011:   # allow $0.01 gaps like 74,000 -> 73,999.99
            return False
        edges.append(float(r.cap))
    return abs(float(more["floor"].iloc[0]) - edges[-1]) <= 0.011


def check_mutually_exclusive(ev: pd.DataFrame, fee: FeeModel) -> list[dict]:
    out = []
    if not bool(ev["mutually_exclusive"].iloc[0]) or len(ev) < 2:
        return out
    n = len(ev)
    # sell every YES (buy every NO): riskless with mutual exclusivity alone
    if (ev["bid"] > 0).all():
        sets = float(ev["bid_size"].min())
        gross = float(ev["bid"].sum()) - 1.0
        net = gross - sum(_fee(fee, 1 - b, sets) for b in ev["bid"])
        if net > EPS:
            out.append({"kind": "sell_all", "profit_per_set": net, "max_sets": sets,
                        "legs": [{"ticker": t, "action": "buy NO (sell YES)", "price": 1 - b, "size": s}
                                 for t, b, s in zip(ev["ticker"], ev["bid"], ev["bid_size"])],
                        "details": {"sum_bids": float(ev["bid"].sum()), "n": n}})
    # buy every YES: riskless only if the outcomes are exhaustive
    if (ev["ask"] < 1).all():
        sets = float(ev["ask_size"].min())
        gross = 1.0 - float(ev["ask"].sum())
        net = gross - sum(_fee(fee, a, sets) for a in ev["ask"])
        if net > EPS:
            out.append({"kind": "buy_all", "profit_per_set": net, "max_sets": sets,
                        "legs": [{"ticker": t, "action": "buy YES", "price": a, "size": s}
                                 for t, a, s in zip(ev["ticker"], ev["ask"], ev["ask_size"])],
                        "details": {"sum_asks": float(ev["ask"].sum()), "n": n,
                                    "exhaustive": "verified" if is_exhaustive_strike_set(ev) else "NOT verified"}})
    return out


def scan(df: pd.DataFrame, fee: FeeModel) -> pd.DataFrame:
    found = []
    for ev_tk, ev in df.groupby("event_ticker"):
        hits = check_ladder(ev, fee, "greater") + check_ladder(ev, fee, "less") + check_mutually_exclusive(ev, fee)
        for h in hits:
            found.append({"event_key": ev_tk, "series_key": ev["series_ticker"].iloc[0],
                          "event_title": ev["event_title"].iloc[0], **h})
    out = pd.DataFrame(found)
    if not out.empty:
        out = out[out["max_sets"] >= 1].copy()   # nothing tradable if a leg shows no size
        out["profit_usd"] = out["profit_per_set"] * out["max_sets"]
        # riskless = payout guaranteed by construction; buy_all only when the strikes provably cover every outcome
        out["riskless"] = (out["kind"] != "buy_all") | out["details"].map(lambda d: d.get("exhaustive") == "verified")
        out = out.sort_values(["riskless", "profit_usd"], ascending=False).reset_index(drop=True)
    return out


def verify(client: KalshiClient, opp: dict, fee: FeeModel) -> dict:
    """Re-price an opportunity from each leg's live order book (best price and size on the side we'd hit)."""
    legs, worst = [], []
    for leg in opp["legs"]:
        resp = client.http.get(f"/markets/{leg['ticker']}/orderbook", use_cache=False)
        ob = resp.get("orderbook_fp") or resp.get("orderbook") or {}   # current API: orderbook_fp with *_dollars
        # Kalshi books list resting bids for YES and for NO; buying YES hits the best NO bid (price 1 - no_bid)
        side = "no" if leg["action"].startswith("buy YES") else "yes"
        levels = ob.get(f"{side}_dollars") or ob.get(side) or []
        if not levels:
            return {**opp, "verified": 0, "verify_note": f"empty book for {leg['ticker']}"}
        px, qty = levels[-1][0], levels[-1][1]   # best bid is the last level
        px = float(px) / (100.0 if float(px) > 1 else 1.0)
        cost = 1.0 - px
        legs.append({**leg, "price": cost, "size": float(qty)})
        worst.append(float(qty))
    sets = min(worst)
    total_cost = sum(l["price"] for l in legs) + sum(_fee(fee, l["price"], sets) for l in legs)
    min_payout = 1.0 if opp["kind"] in ("ladder_greater", "ladder_less", "buy_all") else len(legs) - 1.0
    net = min_payout - total_cost
    return {**opp, "legs": legs, "profit_per_set": net, "max_sets": sets, "profit_usd": net * sets,
            "verified": int(net > EPS), "verify_note": "order books confirm" if net > EPS else "gone on order books"}


def run(repeat: int = 1, every_s: int = 60, verify_top: int = 10) -> pd.DataFrame:
    fee = FeeModel()
    all_found = []
    with db.session() as conn:
        client = KalshiClient(conn)
        for k in range(repeat):
            t0 = int(time.time())
            df = fetch_open_markets(client)
            found = scan(df, fee)
            ver = []
            cand = found[found["riskless"]].head(verify_top) if len(found) else found   # only riskless setups
            for _, r in cand.iterrows():
                try:
                    ver.append(verify(client, r.to_dict(), fee))
                except Exception as exc:  # a vanished market or book format change should not stop the scan
                    ver.append({**r.to_dict(), "verified": 0, "verify_note": f"verify failed: {exc}"})
            verified = pd.DataFrame(ver)
            rest = found.drop(index=cand.index).assign(verified=None, verify_note="not re-checked")
            result = pd.concat([verified, rest], ignore_index=True) if len(found) else found
            for r in result.to_dict("records"):
                conn.execute(
                    "INSERT INTO scan_opportunities (scan_ts, kind, event_key, series_key, legs, profit_per_set, "
                    "max_sets, profit_usd, verified, details) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (t0, r["kind"], r["event_key"], r["series_key"], json.dumps(r["legs"], default=float),
                     float(r["profit_per_set"]), float(r["max_sets"]), float(r["profit_usd"]),
                     r.get("verified"), json.dumps({**r.get("details", {}), "note": r.get("verify_note"),
                                                    "riskless": bool(r.get("riskless")),
                                                    "title": r.get("event_title")}, default=str)))
            summary = {"scan": k + 1, "events": int(df["event_ticker"].nunique()) if len(df) else 0,
                       "markets": int(len(df)),
                       "mutually_exclusive_events": int(df.groupby("event_ticker")["mutually_exclusive"].first().sum())
                       if len(df) else 0,
                       "opportunities": int(len(result)),
                       "riskless": int(result["riskless"].astype(bool).sum()) if len(result) else 0,
                       "verified": int((result.get("verified") == 1).sum()) if len(result) else 0}
            audit.log(conn, "scanner.structural", "scan", None, None, summary)
            conn.commit()
            log.info("scan %d/%d: %s", k + 1, repeat, summary)
            all_found.append(result.assign(scan_ts=t0))
            if k + 1 < repeat:
                time.sleep(every_s)
    return pd.concat(all_found, ignore_index=True) if all_found else pd.DataFrame()
