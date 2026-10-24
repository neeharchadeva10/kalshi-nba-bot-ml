"""J9 (realised slippage of real fills) and J10 (Kalshi T-1h line variants).  Local files only; no network, no order/portfolio endpoint.
J9 reads logs/trade_ledger.csv, logs/early_overs_bot.jsonl, the latest logs/ledger_raw/*.json and the order-book messages that
pb_ws_extract.py copied out of the read-only ws recorder files into _cache/ws_nba_msgs.jsonl."""
from __future__ import annotations

import glob
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

import pb_common as C
import pb_lib as L

E2 = C.E2


# ------------------------------------------------------------------ order-book reconstruction (pure)
class OBook:
    """One ticker's order book from a snapshot + deltas.  bids['yes'] / bids['no'] = {price: size}."""

    def __init__(self):
        self.bids = {"yes": {}, "no": {}}

    def snapshot(self, yes, no):
        self.bids = {"yes": {round(float(p), 4): float(s) for p, s in yes}, "no": {round(float(p), 4): float(s) for p, s in no}}

    def delta(self, side, price, d):
        b = self.bids[side]
        p = round(float(price), 4)
        v = b.get(p, 0.0) + float(d)
        if v <= 1e-9:
            b.pop(p, None)
        else:
            b[p] = v

    def best_bid(self, side):
        b = self.bids[side]
        return max(b) if b else None

    def ask(self, outcome):
        """Price to BUY `outcome` now = 1 - best bid of the opposite outcome."""
        other = "no" if outcome == "yes" else "yes"
        bb = self.best_bid(other)
        return None if bb is None else round(1.0 - bb, 4)


def replay(msgs, until_ms):
    """msgs: list of dicts {recv_ns, m} for ONE ticker and ONE recorder session, in recorded order.  Applies a snapshot, then deltas
    whose exchange time ts_ms is strictly before until_ms.  Returns an OBook or None when no snapshot precedes until_ms."""
    ob = None
    for r in msgs:
        m = r["m"]
        t = m["type"]
        body = m["msg"]
        if t == "orderbook_snapshot":
            if r["recv_ns"] / 1e6 >= until_ms:
                break
            ob = OBook()
            ob.snapshot(body.get("yes_dollars_fp", []), body.get("no_dollars_fp", []))
        elif t == "orderbook_delta" and ob is not None:
            ts = body.get("ts_ms") or r["recv_ns"] / 1e6
            if ts >= until_ms:
                break
            ob.delta(body["side"], body["price_dollars"], body["delta_fp"])
    return ob


def msg_time_ms(r):
    body = r["m"]["msg"]
    if r["m"]["type"] == "orderbook_snapshot":
        return r["recv_ns"] / 1e6
    return body.get("ts_ms") or r["recv_ns"] / 1e6


def stream_asks(lines, wants, stale_ms=600_000):
    """One pass over recorded messages (an iterable of JSON lines, recorded order).  wants = [{key, ticker, outcome, ms}].
    For every (ticker, recorder session) the book is rebuilt and, just BEFORE the first message at/after each fill time, the ask of the
    outcome is recorded (same rule as replay()).  Returns {key: [asks, one per session that had a snapshot and fresh data]}."""
    by_t = defaultdict(list)
    for w in wants:
        by_t[w["ticker"]].append(w)
    for v in by_t.values():
        v.sort(key=lambda w: w["ms"])
    st = {}
    res = defaultdict(list)
    for ln in lines:
        r = json.loads(ln)
        body = r["m"]["msg"]
        tk = body.get("market_ticker")
        if tk not in by_t or r["m"]["type"] not in ("orderbook_snapshot", "orderbook_delta"):
            continue
        sess = r["f"].split("-")[-1]
        s_ = st.setdefault((tk, sess), {"ob": None, "p": 0, "last": None})
        tm = msg_time_ms(r)
        pend = by_t[tk]
        while s_["p"] < len(pend) and pend[s_["p"]]["ms"] <= tm:
            w = pend[s_["p"]]
            if s_["ob"] is not None and s_["last"] is not None and w["ms"] - s_["last"] <= stale_ms:
                a_ = s_["ob"].ask(w["outcome"])
                if a_ is not None:
                    res[w["key"]].append(a_)
            s_["p"] += 1
        if r["m"]["type"] == "orderbook_snapshot":
            s_["ob"] = OBook()
            s_["ob"].snapshot(body.get("yes_dollars_fp", []), body.get("no_dollars_fp", []))
        elif s_["ob"] is not None:
            s_["ob"].delta(body["side"], body["price_dollars"], body["delta_fp"])
        s_["last"] = tm
    for (tk, sess), s_ in st.items():                       # fills after the session's last message (book unchanged since)
        pend = by_t[tk]
        while s_["p"] < len(pend):
            w = pend[s_["p"]]
            if s_["ob"] is not None and s_["last"] is not None and 0 <= w["ms"] - s_["last"] <= stale_ms:
                a_ = s_["ob"].ask(w["outcome"])
                if a_ is not None:
                    res[w["key"]].append(a_)
            s_["p"] += 1
    return res


def slippage_cents(fill_price, ask):
    if ask is None or (isinstance(ask, float) and np.isnan(ask)):
        return None
    return round(100.0 * (fill_price - ask), 4)


def summarize(vals):
    v = np.asarray([x for x in vals if x is not None and np.isfinite(x)], float)
    if len(v) == 0:
        return {"n": 0}
    return {"n": int(len(v)), "mean_cents": float(v.mean()), "median_cents": float(np.median(v)), "min_cents": float(v.min()),
            "max_cents": float(v.max()), "share_zero": float((v == 0).mean()), "share_positive": float((v > 0).mean()),
            "share_negative": float((v < 0).mean()), "values_cents": [float(x) for x in v][:60]}


# ------------------------------------------------------------------ J9
def j9():
    ledger = pd.read_csv(C.ROOT / "logs" / "trade_ledger.csv")
    raw = json.loads(Path(sorted(glob.glob(str(C.ROOT / "logs" / "ledger_raw" / "*.json")))[-1]).read_text())
    fills = {fl["fill_id"]: fl for lst in raw["fills"].values() for fl in lst}
    rows, wants = [], []
    for r in ledger.itertuples(index=False):
        if not str(r.ticker).startswith("KXNBA"):
            continue
        fl = fills.get(r.fill_id)
        if fl is None:
            continue
        outcome = fl["outcome_side"]
        price = float(fl["yes_price_dollars"] if outcome == "yes" else fl["no_price_dollars"])
        ms = int(pd.Timestamp(fl["created_time"]).value // 1_000_000)
        asks = []
        wants.append({"key": r.fill_id, "ticker": r.ticker, "outcome": outcome, "ms": ms})
        rows.append({"ticker": r.ticker, "fill_id": r.fill_id, "tag": r.strategy_tag, "taker": r.taker_or_maker == "taker", "outcome": outcome,
                     "fill_price": price, "count": float(fl["count_fp"]), "ask_book_before_fill": None, "n_sessions_book": 0, "sessions_agree": None,
                     "ping_price": (None if pd.isna(r.pick_ping_price) else float(r.pick_ping_price)),
                     "pick_limit": (None if pd.isna(r.pick_limit) else float(r.pick_limit)), "fill_time_utc": r.fill_time_utc})
    wsf = C.CACHE / "ws_nba_msgs.jsonl"
    asks_by = stream_asks(open(wsf), [w for w in wants if True]) if wsf.exists() else {}
    for rw in rows:
        a_ = asks_by.get(rw["fill_id"], [])
        rw["ask_book_before_fill"] = float(np.median(a_)) if a_ else None
        rw["n_sessions_book"] = len(a_)
        rw["sessions_agree"] = (len(set(a_)) <= 1) if a_ else None
    T = pd.DataFrame(rows)
    out = {"definition": "slippage = fill price - quoted price of the SAME outcome (cents, + = paid more than quoted)",
           "fills_in_ledger_total": int(len(ledger)), "nba_fills_with_raw_record": int(len(T))}
    tk = T[T.taker]
    out["taker_fills_vs_reconstructed_book_just_before_fill"] = {
        **summarize([slippage_cents(p, a) for p, a in zip(tk.fill_price, tk.ask_book_before_fill)]),
        "n_taker_nba_fills": int(len(tk)), "n_with_book": int(tk.ask_book_before_fill.notna().sum()),
        "rows": [{k: (None if (isinstance(v, float) and not np.isfinite(v)) else v) for k, v in r.items()}
                 for r in tk[["ticker", "tag", "outcome", "fill_price", "ask_book_before_fill", "count", "n_sessions_book", "sessions_agree"]].to_dict("records")]}
    ov = tk[tk.ping_price.notna()]
    out["early_overs_manual_taker_fills_vs_pick_ping_price"] = {
        **summarize([slippage_cents(p, a) for p, a in zip(ov.fill_price, ov.ping_price)]),
        "note": "ping price = the ask seen when the pick was made (decision time), earlier than the fill by minutes to hours"}
    mk = T[~T.taker & T.pick_limit.notna()]
    out["early_overs_maker_fills_vs_own_limit"] = {
        **summarize([slippage_cents(p, a) for p, a in zip(mk.fill_price, mk.pick_limit)]),
        "note": "resting post-only orders: the fill is at (or better than) the limit; not slippage in the taker sense"}
    # bot (IOC) fills: order limit vs average fill, from the bot's own jsonl log
    bot = []
    for ln in open(C.ROOT / "logs" / "early_overs_bot.jsonl"):
        e = json.loads(ln)
        if e.get("event") == "order_attempt" and not e.get("dry"):
            bot.append({"game": e["game"], "max_c": e["max_c"], "price_c": e["price_c"], "body_price_yes_side": e["body"]["price"], "coid": e["coid"]})
    fills_bot = {}
    for ln in open(C.ROOT / "logs" / "early_overs_bot.jsonl"):
        e = json.loads(ln)
        if e.get("event") == "fill" and not e.get("dry"):
            fills_bot[e["game"]] = float(e["avg_cost"])
    for b in bot:
        b["avg_cost"] = fills_bot.get(b["game"])
        b["slippage_vs_bot_quote_cents"] = None if b["avg_cost"] is None else round(100 * b["avg_cost"] - b["price_c"], 4)
        b["slippage_vs_ping_ask_cents"] = None if b["avg_cost"] is None else round(100 * b["avg_cost"] - b["max_c"], 4)
    out["bot_ioc_fills"] = {
        "rows": bot, "vs_ask_the_bot_read_just_before_sending": summarize([b["slippage_vs_bot_quote_cents"] for b in bot]),
        "vs_ping_ask_(decision_time)": summarize([b["slippage_vs_ping_ask_cents"] for b in bot]),
        "note": ("order price_c = the ask the bot read immediately before sending (overs_bot.py line ~868); max_c = the ask at ping time. Both fills "
                 "were at exactly that ask.  No order-book recording exists for the 2026-10-22 NBA games (data/ws/nba/26OCT22 has markets.json "
                 "only), so these two are checked against the bot's own quote, not a recorded book.")}
    book_vals = [slippage_cents(p, a) for p, a in zip(tk.fill_price, tk.ask_book_before_fill)]
    book_vals = [v for v in book_vals if v is not None]
    bot_vals = [b["slippage_vs_bot_quote_cents"] for b in bot if b["slippage_vs_bot_quote_cents"] is not None]
    allv = book_vals + bot_vals
    out["all_paired_taker_fills_combined"] = summarize(allv)
    out["assumed_in_backtests_cents"] = 1.0
    out["n_unpaired_fills_note"] = ("346 KXBTC15M + NFL + MVE fills have no cached quote at order time (the recorders only cover NBA/NFL "
                                    "game boards from 2026-10-20; no candle cache exists for October 2026); 3 NBA lockbot fills lack a book "
                                    "snapshot in the recorder files.")
    n = len(allv)
    mean = float(np.mean(allv)) if n else float("nan")
    out["mean_taker_slippage_cents"] = mean
    out["verdict"] = (f"MATTERS A LITTLE - 1c/contract of assumed cost vs {mean:.2f}c realised; only n={n} one-contract fills, quotes taken "
                      "at the instant of the order (no latency / decision-delay part)")
    return out


# ------------------------------------------------------------------ J10
def j10():
    db = C.OUT / "nba_injury_speed" / "candles.sqlite"
    ev = pd.read_csv(C.OUT / "nba_injury_speed" / "events_list.csv")
    ev = ev[ev.season_type == 2].copy()
    X = C.load_e1()
    G = C.game_err(X).set_index("game_id")
    import sys
    sys.path.insert(0, str(C.OUT / "nba_formula_accuracy"))
    import fa_lib
    close = fa_lib.load_closing()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    VW_LOOKBACK_S = 3600
    rec = []
    for e in ev.itertuples(index=False):
        if e.hoopr_gid not in G.index or G.loc[e.hoopr_gid, "season"] != "2025-26":
            continue
        T = int(e.T)
        x = T - 3600
        mk = conn.execute("SELECT ticker, strike FROM mkts WHERE event=?", (e.event,)).fetchall()
        pts, vw_pts = [], []
        for t, s in mk:
            rows = conn.execute("SELECT end_ts, bid, ask, volume FROM candles WHERE ticker=? AND end_ts<=? AND end_ts>? ORDER BY end_ts",
                                (t, x, x - VW_LOOKBACK_S - 7 * 86400)).fetchall()
            if not rows:
                continue
            last = rows[-1]
            if last[1] is None or last[2] is None:
                continue
            pts.append((s, last[1], last[2]))
            win = [r for r in rows if r[0] > x - VW_LOOKBACK_S and r[1] is not None and r[2] is not None]
            v = L.vw_mid([r[1] for r in win], [r[2] for r in win], [r[3] for r in win]) if win else None
            if v is not None:
                vw_pts.append((s, last[1], last[2], v))
        pm15, pm10 = L.mids(pts, 0.15), L.mids(pts, 0.10)
        # volume-weighted: use the spread filter on the latest quote, the volume-weighted mid over the last hour before T-1h
        ok15 = {s for s, _ in pm15}
        vwm = sorted((s, v) for s, b, a, v in vw_pts if s in ok15)
        actual = float(G.loc[e.hoopr_gid, "tot_act"])
        rec.append({"event": e.event, "game_id": int(e.hoopr_gid), "actual": actual, "n_strikes": len(pts),
                    "cross15": L.crossing(pm15), "cross10": L.crossing(pm10), "main_strike": L.main_strike(pm10),
                    "vw_cross": L.crossing(vwm), "close": float(close.get(e.hoopr_gid, np.nan)),
                    "e1": float(G.loc[e.hoopr_gid, "tot_pred"])})
    R = pd.DataFrame(rec)
    out = {"n_events_2025_26": int(len(R)), "definition": {
        "cross15": "0.50 crossing of the YES mid at the last candle ending <= T-1h, spread <= 0.15 (the baseline = implied_total)",
        "cross10": "same with spread <= 0.10",
        "main_strike": "the strike (a number like 233.5) whose mid is closest to 0.50, spread <= 0.10, tie -> lower strike",
        "vw_cross": "0.50 crossing of the candle-volume-weighted mid over the hour before T-1h (zero-volume hour: last mid); strikes as in cross15"},
        "variants": {}}
    cols = ["cross15", "cross10", "main_strike", "vw_cross"]
    common = R.dropna(subset=cols + ["close"])
    for c in cols:
        d = R.dropna(subset=[c])
        e = d[c] - d.actual
        ec = common[c] - common.actual
        out["variants"][c] = {"n": int(len(d)), "mae": float(e.abs().mean()), "bias_line_minus_actual": float(e.mean()),
                              "n_common": int(len(common)), "mae_on_common_games": float(ec.abs().mean())}
    out["common_games_reference"] = {"n": int(len(common)), "sportsbook_close_mae": float((common.close - common.actual).abs().mean()),
                                     "E1_mae": float((common.e1 - common.actual).abs().mean())}
    base = out["variants"]["cross15"]["mae_on_common_games"]
    out["max_mae_swing_vs_baseline_common_games"] = float(max(abs(v["mae_on_common_games"] - base) for v in out["variants"].values()))
    # paired bootstrap: each variant minus baseline, common games
    out["paired_vs_cross15_common_games"] = {}
    for c in cols[1:]:
        d = (common[c] - common.actual).abs() - (common.cross15 - common.actual).abs()
        out["paired_vs_cross15_common_games"][c] = L.boot_mean_ci(d.values)
    out["verdict"] = L.verdict(out["max_mae_swing_vs_baseline_common_games"])
    return out
