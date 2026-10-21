"""READ-ONLY post-game analysis of tonight's live Kalshi trading on WAS @ SF (event 26OCT19WASSF, 2026-10-19 ET).

Run from the repo root (the HTTP client's raw-cache paths are relative to the cwd):
    .venv/Scripts/python.exe reports/output/nfl_dip/postgame.py

Inputs : logs/wassf_orders.jsonl, logs/wassf_signals.csv, logs/wassf_watch.csv, and public Kalshi market data
         (GET /markets/{t} and 1-minute candlesticks) through kpm.clients.kalshi.KalshiClient.
Safety : public market-data GETs only; nothing from kpm.execution, no keys, no orders, no portfolio endpoints.
         Responses are not written to the raw cache (persist=False); the only files written are the outputs.
Outputs: postgame.json, postgame_trades.csv, postgame_signals.csv (next to this file).

Sections (all numbers go to postgame.json):
  A  trade ledger, average-cost positions, realized + settlement P&L, check against Kalshi's own numbers
  B  P&L = entry edge + exit edge - fees + market moves (fair values from the live preflights)
  C  markouts of every buy fill at +0/+1/+5/+15/+30 min from candles (fair, thin mid, thin bid) + classification
  D  signal funnel of the auto period (signals -> episodes -> preflights -> holds/aborts -> sent -> filled)
  E  gap persistence (how long book EV stayed >= 1c), scan cadence, signal-to-order delay before auto mode
  F  fair at entry vs the settled outcome, per buy fill
"""
from __future__ import annotations

import bisect
import csv
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from kpm.clients.http import HttpError
from kpm.clients.kalshi import KalshiClient

T_START = time.perf_counter()
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LOGS = ROOT / "logs"

E = "26OCT19WASSF"
MANUAL_TICKER = f"KXNFLWINMARGIN-{E}-WAS1TO6"      # manual order_result / sell_result carry no ticker
PROT_SERIES = ("KXNFLRECPROT", "KXNFLRECYDSPROT", "KXNFLRSHYDSPROT", "KXNFLPASSYDSPROT", "KXNFLPASSTDSPROT",
               "KXNFLTDPROT")
DEEP = {"SF": f"KXNFLGAME-{E}-SF", "WAS": f"KXNFLGAME-{E}-WAS", "TIE": f"KXNFLTIE-{E}-Y",
        "SF7": f"KXNFLSPREAD-{E}-SF7", "SF15": f"KXNFLSPREAD-{E}-SF15",
        "WAS7": f"KXNFLSPREAD-{E}-WAS7", "WAS15": f"KXNFLSPREAD-{E}-WAS15"}
BUCKETS = ["SF1TO6", "SF7TO14", "SF15PLUS", "WAS1TO6", "WAS7TO14", "WAS15PLUS", "TIE"]
EV_OK = 0.01                 # the trader's buy threshold (book EV per contract after fee)
EPISODE_GAP_S = 15           # signal rows of one ticker+outcome < 15 s apart are one episode
HORIZONS = [0, 1, 5, 15, 30]  # markout horizons in minutes
MOVE_TOL = 0.01              # a "move" for the markout classification is >= 1c
MATCH_WINDOW_S = 10          # a buy attempt belongs to a signal row at most 10 s older (auto mode)


def ep(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def iso(t: int | float | None) -> str | None:
    return None if t is None else datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


W0, W1 = ep("2026-10-20T00:00:00Z"), ep("2026-10-20T03:30:00Z")
FETCH0 = W0 - 3 * 3600       # fetch from 21:00Z so the carry-forward has a seed candle at 00:00Z
AUTO_END = ep("2026-10-20T03:24:00Z")


def fnum(x):
    if x in (None, ""):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def r6(x):
    return None if x is None else round(float(x), 6)


def fee(p):                  # the live trader's unrounded taker fee per contract
    return 0.07 * p * (1 - p)


def series_of(t):
    return t.split("-")[0]


def is_prot(t):
    return series_of(t) in PROT_SERIES


def twin_of(t):
    s = series_of(t)
    return s[:-4] + t[len(s):]


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


# ----------------------------------------------------------------------------------------------------------------
# Inputs
# ----------------------------------------------------------------------------------------------------------------
def load_orders():
    rows = []
    for i, line in enumerate((LOGS / "wassf_orders.jsonl").read_text(encoding="utf-8").splitlines()):
        if line.strip():
            r = json.loads(line)
            r["_i"], r["_t"] = i, ep(r["ts"])
            rows.append(r)
    return rows


def load_signals():
    out = []
    with (LOGS / "wassf_signals.csv").open(encoding="utf-8") as f:
        for i, row in enumerate(csv.reader(f)):
            if len(row) < 7:
                continue
            out.append(dict(i=i, ts=row[0], t=ep(row[0]), outcome=row[2], ticker=row[3], listing_price=fnum(row[4]),
                            listing_ev=fnum(row[5]), listing_size=fnum(row[6])))
    return sorted(out, key=lambda s: (s["t"], s["i"]))


def load_watch():
    with (LOGS / "wassf_watch.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["_t"] = ep(r["ts"])
    return rows


ORDERS, SIGNALS, WATCH = load_orders(), load_signals(), load_watch()

# ----------------------------------------------------------------------------------------------------------------
# Order-log parsing: fills, preflights, buy attempts
# ----------------------------------------------------------------------------------------------------------------
RESULT_EVENTS = {"order_result": ("buy", True), "sell_result": ("sell", True),
                 "trade_buy_result": ("buy", False), "trade_sell_result": ("sell", False)}


def find_back(k, events, ticker, max_s=120):
    """Latest row before index k with event in events and this ticker (manual rows: ticker field may be absent)."""
    t_k = ORDERS[k]["_t"]
    for j in range(k - 1, -1, -1):
        r = ORDERS[j]
        if t_k - r["_t"] > max_s:
            return None
        if r["event"] in events and (r.get("ticker") or MANUAL_TICKER) == ticker:
            return r
    return None


def watch_fair_at(t):
    best = None
    for w in WATCH:
        if w["_t"] <= t and fnum(w.get("margin_fair")) is not None:
            best = w
    return (fnum(best["margin_fair"]), best["ts"]) if best else (None, None)


def parse_fills():
    fills, zero = [], []
    for k, r in enumerate(ORDERS):
        if r["event"] not in RESULT_EVENTS:
            continue
        action, manual = RESULT_EVENTS[r["event"]]
        res = r.get("result") if isinstance(r.get("result"), dict) else {}
        ticker = MANUAL_TICKER if manual else r["ticker"]
        outcome = "yes" if manual else r.get("outcome", "yes")
        n = fnum(res.get("fill_count")) or 0.0
        if not r.get("ok") or n <= 0:
            zero.append(dict(ts=r["ts"], ticker=ticker, outcome=outcome, action=action, ok=r.get("ok"),
                             fill_count=n, order_id=r.get("order_id")))
            continue
        ypx = fnum(res.get("average_fill_price"))
        px = ypx if outcome == "yes" else 1 - ypx
        fee_pc = fnum(res.get("average_fee_paid")) or 0.0
        f = dict(ts=r["ts"], t=r["_t"], ticker=ticker, outcome=outcome, action=action, source="manual" if manual else "script",
                 count=n, yes_price=ypx, price=px, fee_per_contract=fee_pc, fee_total=n * fee_pc,
                 order_id=r.get("order_id"), fair_source=None, fair_yes=None, preflight_price=None, preflight_ts=None)
        # fair at the moment of the trade (outcome terms later) and the preflight's quoted price
        if action == "buy" and not manual:
            pf = find_back(k, {"trade_buy_preflight"}, ticker)
            if pf:
                f.update(fair_yes=fnum(pf["fair_yes"]), preflight_price=fnum(pf["price"]), preflight_ts=pf["ts"],
                         preflight_ev=fnum(pf.get("ev_per_contract")), preflight_count=fnum(pf.get("count")),
                         fair_source="trade_buy_preflight")
        elif action == "sell" and not manual:
            pf = find_back(k, {"trade_sell_preflight"}, ticker)
            if pf:
                b, a = fnum(pf.get("bid")), fnum(pf.get("ask"))
                f.update(fair_yes=fnum(pf["fair_yes"]), preflight_ts=pf["ts"], fair_source="trade_sell_preflight",
                         preflight_price=(b if outcome == "yes" else (None if a is None else 1 - a)))
        elif action == "sell" and manual:
            pf = find_back(k, {"sell_preflight"}, ticker)
            if pf:
                f.update(fair_yes=fnum(pf.get("fair")), preflight_price=fnum(pf.get("bid")), preflight_ts=pf["ts"],
                         fair_source="manual sell_preflight")
        else:   # manual buy: the preflight has no fair; use the watcher's fair (task: 0.1375 at 00:05:56Z)
            pf = find_back(k, {"preflight"}, ticker)
            wf, wts = watch_fair_at(r["_t"])
            f.update(fair_yes=wf, preflight_price=fnum(pf.get("ask")) if pf else None, preflight_ts=pf["ts"] if pf else None,
                     fair_source=f"wassf_watch.csv margin_fair @ {wts}")
        fy = f["fair_yes"]
        f["fair"] = None if fy is None else (fy if outcome == "yes" else 1 - fy)
        fills.append(f)
    return fills, zero


FILLS, ZERO_FILLS = parse_fills()


def reason_cat(reason: str) -> str:
    r = reason or ""
    if "buy cap" in r:
        return "budget_cap"
    if "already holding" in r:
        return "already_held"
    if "no trustworthy fair value" in r:
        return "no_fair_value"
    if "too small" in r:
        return "too_small"
    if r.startswith("status"):
        return "not_active"
    if "in the book" in r:
        return "empty_book"
    return "other"


def parse_attempts():
    """Buy attempts in log order: a trade_buy_preflight + its next hold/abort/body for the same ticker, or a lone
    abort raised before the preflight (no fair value / status / empty book). Sell flows are tracked so their
    hold/abort rows are not mistaken for buy outcomes."""
    attempts, open_pf = [], {}
    last_body = {}
    for r in ORDERS:
        ev, t = r["event"], r.get("ticker")
        if ev == "trade_buy_preflight":
            a = dict(kind="preflight", ts=r["ts"], t=r["_t"], ticker=t, outcome=r["outcome"], price=fnum(r["price"]),
                     fair_yes=fnum(r["fair_yes"]), ev=fnum(r["ev_per_contract"]), count=fnum(r.get("count")),
                     held=fnum(r.get("held")), spent=fnum(r.get("spent_tonight")), result=None, reason=None,
                     reason_cat=None, sent=False, filled=0.0)
            a["fair_out"] = None if a["fair_yes"] is None else (a["fair_yes"] if a["outcome"] == "yes" else 1 - a["fair_yes"])
            attempts.append(a)
            open_pf[t] = ("buy", a)
        elif ev == "trade_sell_preflight":
            open_pf[t] = ("sell", None)
        elif ev in ("hold", "abort", "trade_buy_body") and t is not None:
            kind_a = open_pf.pop(t, None)
            if kind_a and kind_a[0] == "buy":
                a = kind_a[1]
                if ev == "hold":
                    a["result"] = "hold"
                elif ev == "abort":
                    a["result"], a["reason"], a["reason_cat"] = "abort", r.get("reason"), reason_cat(r.get("reason"))
                else:
                    a["result"], a["sent"] = "body", bool(r.get("send"))
                    if a["sent"]:
                        last_body[t] = a
            elif kind_a is None and ev == "abort":
                attempts.append(dict(kind="pre_abort", ts=r["ts"], t=r["_t"], ticker=t, outcome=None, price=None,
                                     fair_yes=None, fair_out=None, ev=None, count=None, held=None, spent=None,
                                     result="abort", reason=r.get("reason"), reason_cat=reason_cat(r.get("reason")),
                                     sent=False, filled=0.0))
            elif kind_a is None and ev == "trade_buy_body":   # body without a preflight in between (should not happen)
                pass
        elif ev == "trade_buy_result" and t in last_body:
            res = r.get("result") if isinstance(r.get("result"), dict) else {}
            last_body.pop(t)["filled"] = fnum(res.get("fill_count")) or 0.0
    return attempts


ATTEMPTS = parse_attempts()

# ----------------------------------------------------------------------------------------------------------------
# Public market data: /markets/{t} and 1-minute candles (carried forward)
# ----------------------------------------------------------------------------------------------------------------
CLIENT = KalshiClient()


def get(path, params=None):
    return CLIENT.http.get(path, params, use_cache=False, persist=False)


def cpx(c, key):
    d = c.get(key) or {}
    v = d.get("close_dollars")
    if v not in (None, ""):
        return float(v)
    v = d.get("close")
    return None if v in (None, "") else float(v) / 100.0


class Book:
    """Top of book from 1-minute candles. at(t) = close of the latest candle whose period ended at or before t
    (no look-ahead). A bid of 0 means an empty bid side and an ask of 1 an empty ask side; both become None."""

    def __init__(self, ticker, candles, tier, error=None):
        cs = sorted(candles, key=lambda c: c["end_period_ts"])
        self.ticker, self.tier, self.error = ticker, tier, error
        self.cs = cs
        self.ends = [c["end_period_ts"] for c in cs]
        self.q = [(cpx(c, "yes_bid"), cpx(c, "yes_ask")) for c in cs]

    def at_raw(self, t):
        i = bisect.bisect_right(self.ends, t) - 1
        return (None, None, None) if i < 0 else (*self.q[i], t - self.ends[i])

    def at(self, t):
        b, a, age = self.at_raw(t)
        b = b if b is not None and b > 0 else None
        a = a if a is not None and a < 1 else None
        return b, a, age

    def covering(self, t):
        """The candle whose minute contains t (ends at the next minute boundary), or None if no candle that minute."""
        end = (t // 60 + 1) * 60
        i = bisect.bisect_left(self.ends, end)
        if i < len(self.ends) and self.ends[i] == end:
            c = self.cs[i]
            return {k: {kk: c.get(k, {}).get(kk) for kk in ("open_dollars", "high_dollars", "low_dollars", "close_dollars")}
                    for k in ("yes_bid", "yes_ask")} | {"end": iso(end)}
        return None

    def gap_stats(self):
        ins = [e for e in self.ends if W0 < e <= W1]
        mins = (W1 - W0) // 60
        gaps = [(b - a) // 60 - 1 for a, b in zip(ins, ins[1:])]
        return dict(candles_in_window=len(ins), minutes_in_window=mins, minutes_without_candle=mins - len(ins),
                    longest_gap_min=max(gaps) if gaps else None,
                    first_candle=iso(ins[0]) if ins else None, last_candle=iso(ins[-1]) if ins else None,
                    tier=self.tier, error=self.error)


def fetch_candles(t):
    params = {"start_ts": FETCH0, "end_ts": W1, "period_interval": 1}
    err = None
    try:
        r = get(f"/series/{series_of(t)}/markets/{t}/candlesticks", params)
        if r.get("ticker") not in (None, t):
            raise RuntimeError(f"candle response for {r.get('ticker')} while asking for {t}")
        if r.get("candlesticks"):
            return Book(t, r["candlesticks"], "live")
    except HttpError as e:
        err = str(e)[:200]
    try:
        r = get(f"/historical/markets/{t}/candlesticks", params)
        return Book(t, r.get("candlesticks") or [], "historical", err)
    except HttpError as e:
        return Book(t, [], None, f"{err} | historical: {str(e)[:200]}")


def fetch_market(t):
    try:
        m = get(f"/markets/{t}")["market"]
        if m.get("ticker") != t:
            raise RuntimeError(f"market response for {m.get('ticker')} while asking for {t}")
        return m
    except Exception as e:  # noqa: BLE001 -- record and continue
        return {"_error": f"{type(e).__name__}: {e}"[:300]}


need = set(DEEP.values()) | {f"KXNFLWINMARGIN-{E}-{b}" for b in BUCKETS}
need |= {f["ticker"] for f in FILLS} | {s["ticker"] for s in SIGNALS} | {a["ticker"] for a in ATTEMPTS if a["ticker"]}
need |= {twin_of(t) for t in list(need) if is_prot(t)}
need = sorted(need)
with ThreadPoolExecutor(max_workers=3) as ex:
    BOOKS = dict(zip(need, ex.map(fetch_candles, need)))
    MARKETS = dict(zip(need, ex.map(fetch_market, need)))
T_FETCHED = time.perf_counter()


def mid_of(b, a):
    if b is not None and a is not None:
        return (b + a) / 2
    if b is None and a is not None and a <= 0.03:
        return a / 2          # long shot with an empty bid side (live trader's rule)
    return None


def quote(t, at):
    bk = BOOKS.get(t)
    return bk.at(at) if bk else (None, None, None)


def fair_yes_at(ticker, at):
    """(fair YES, max carry-forward age of the inputs in s, twin spread) from candles, same rules as the trader."""
    if is_prot(ticker):
        b, a, age = quote(twin_of(ticker), at)
        return mid_of(b, a), age, (None if b is None or a is None else a - b)
    if series_of(ticker) == "KXNFLWINMARGIN":
        m, ages = {}, []
        for k, t in DEEP.items():
            b, a, age = quote(t, at)
            m[k] = mid_of(b, a)
            ages.append(age)
        if any(v is None for v in m.values()):
            return None, None, None
        win_sf, win_was = m["SF"] - m["TIE"] / 2, m["WAS"] - m["TIE"] / 2
        f = {"SF1TO6": win_sf - m["SF7"], "SF7TO14": m["SF7"] - m["SF15"], "SF15PLUS": m["SF15"],
             "WAS1TO6": win_was - m["WAS7"], "WAS7TO14": m["WAS7"] - m["WAS15"], "WAS15PLUS": m["WAS15"],
             "TIE": m["TIE"]}.get(ticker.split("-")[-1])
        return (None if f is None else min(max(f, 0.0), 1.0)), max(ages), None
    return None, None, None


def fair_out_at(ticker, outcome, at):
    f, age, sp = fair_yes_at(ticker, at)
    return (None if f is None else (f if outcome == "yes" else 1 - f)), age


def thin_out_at(ticker, outcome, at):
    """Thin market's own (bid, ask, mid) in outcome terms."""
    b, a, age = quote(ticker, at)
    m = mid_of(b, a)
    if outcome == "yes":
        return b, a, m, age
    return (None if a is None else 1 - a), (None if b is None else 1 - b), (None if m is None else 1 - m), age


def settle_value(ticker, outcome):
    """(value per contract in outcome terms, status string, detail). Pending markets fall back to the twin's
    settlement (protected props) or the last computable deep-market fair."""
    m = MARKETS.get(ticker) or {}
    res = (m.get("result") or "").lower()
    if res in ("yes", "no"):
        v = 1.0 if res == "yes" else 0.0
        return (v if outcome == "yes" else 1 - v), "settled", dict(result=res, status=m.get("status"),
                                                                  settlement_ts=m.get("settlement_ts"))
    if res == "scalar":
        v = fnum(m.get("settlement_value_dollars"))
        return (v if outcome == "yes" else 1 - v), "settled", dict(result=res, status=m.get("status"),
                                                                  settlement_value=v)
    # pending
    last_fair, last_t = None, None
    for t in range(W1, W0, -60):
        f, _ = fair_out_at(ticker, outcome, t)
        if f is not None:
            last_fair, last_t = f, t
            break
    detail = dict(result=None, status=m.get("status"), last_computable_deep_fair=r6(last_fair),
                  last_computable_deep_fair_at=iso(last_t))
    if is_prot(ticker):
        tw = MARKETS.get(twin_of(ticker)) or {}
        tres = (tw.get("result") or "").lower()
        detail.update(twin=twin_of(ticker), twin_status=tw.get("status"), twin_result=tres or None,
                      note="protected prop still unsettled; its regular twin is settled. Protection pays the "
                           "pre-kickoff fair price only if the player played in the 1st half but no snap after it, "
                           "which market data cannot confirm")
        if tres in ("yes", "no"):
            v = 1.0 if tres == "yes" else 0.0
            return (v if outcome == "yes" else 1 - v), "pending (valued at twin's settlement)", detail
    return last_fair, "pending (valued at last deep-market fair)", detail


# ----------------------------------------------------------------------------------------------------------------
# A. Ledger and positions
# ----------------------------------------------------------------------------------------------------------------
def section_a():
    pos = {}
    ledger = []
    for f in sorted(FILLS, key=lambda x: (x["t"], x["ticker"])):
        key = (f["ticker"], f["outcome"])
        p = pos.setdefault(key, dict(ticker=key[0], outcome=key[1], qty=0.0, cost=0.0, cost_xf=0.0, fees=0.0,
                                     realized=0.0, realized_xf=0.0, bought=0.0, sold=0.0, trips=[], _open=None))
        n, px, fe = f["count"], f["price"], f["fee_total"]
        p["fees"] += fe
        realized = None
        if f["action"] == "buy":
            if p["qty"] == 0:
                p["_open"] = dict(open_ts=f["ts"], qty=0.0, buy_cost=0.0, buy_fees=0.0, proceeds=0.0, sell_fees=0.0)
            p["qty"] += n
            p["cost"] += n * px + fe
            p["cost_xf"] += n * px
            p["bought"] += n
            p["_open"]["qty"] += n
            p["_open"]["buy_cost"] += n * px
            p["_open"]["buy_fees"] += fe
        else:
            if p["qty"] <= 0:
                raise SystemExit(f"sell without a position: {f}")
            avg, avg_xf = p["cost"] / p["qty"], p["cost_xf"] / p["qty"]
            realized = n * px - fe - avg * n
            p["realized"] += realized
            p["realized_xf"] += n * px - avg_xf * n
            p["cost"] -= avg * n
            p["cost_xf"] -= avg_xf * n
            p["qty"] -= n
            p["sold"] += n
            p["_open"]["proceeds"] += n * px
            p["_open"]["sell_fees"] += fe
            if abs(p["qty"]) < 1e-9:
                p["qty"], p["cost"], p["cost_xf"] = 0.0, 0.0, 0.0
                o = p["_open"]
                o.update(close_ts=f["ts"], how="sold", pnl=o["proceeds"] - o["sell_fees"] - o["buy_cost"] - o["buy_fees"])
                p["trips"].append(o)
                p["_open"] = None
        ledger.append(dict(ts=f["ts"], ticker=f["ticker"], outcome=f["outcome"], action=f["action"],
                           source=f["source"], count=f["count"], yes_fill_price=f["yes_price"], price=r6(px),
                           fee_per_contract=f["fee_per_contract"], fee_total=r6(fe), fair_at_trade=r6(f["fair"]),
                           fair_source=f["fair_source"], preflight_price=r6(f["preflight_price"]),
                           realized_pnl=r6(realized), position_after=p["qty"], order_id=f["order_id"]))
    positions, totals = [], Counter()
    for (t, o), p in pos.items():
        open_val = settle = None
        status, detail = "closed", None
        if p["qty"] > 0:
            settle, status, detail = settle_value(t, o)
            open_val = p["qty"] * settle - p["cost"]
            tr = p["_open"]
            tr.update(close_ts=None, how=status, settle_value=settle,
                      pnl=p["qty"] * settle - tr["buy_cost"] - tr["buy_fees"] + tr["proceeds"] - tr["sell_fees"])
            p["trips"].append(tr)
        mres = (MARKETS.get(t) or {}).get("result") or None
        total = p["realized"] + (open_val or 0.0)
        positions.append(dict(ticker=t, outcome=o, bought=p["bought"], sold=p["sold"], open_qty=p["qty"],
                              open_cost_incl_fees=r6(p["cost"]), open_cost_ex_fees=r6(p["cost_xf"]),
                              realized_pnl=r6(p["realized"]), realized_pnl_ex_fees=r6(p["realized_xf"]),
                              fees=r6(p["fees"]), open_settle_value_per_contract=r6(settle),
                              open_pnl=r6(open_val), total_pnl=r6(total), status=status, market_result=mres,
                              pending_detail=detail,
                              round_trips=[{k: (r6(v) if isinstance(v, float) else v) for k, v in tr.items()}
                                           for tr in p["trips"]]))
        totals["realized"] += p["realized"]
        totals["realized_xf"] += p["realized_xf"]
        totals["fees"] += p["fees"]
        totals["open_cost_xf"] += p["cost_xf"]
        totals["open_cost"] += p["cost"]
        totals["open_settle"] += p["qty"] * (settle or 0.0)
        totals["total"] += total
    positions.sort(key=lambda x: x["total_pnl"])
    kalshi = dict(realized_ex_fee=-0.24, fees=0.46, open_cost=1.84, open_settles_to=0.0, total=-2.54)
    ours = dict(realized_ex_fee=r6(totals["realized_xf"]), fees=r6(totals["fees"]),
                open_cost_ex_fee=r6(totals["open_cost_xf"]), open_settle_value=r6(totals["open_settle"]),
                total=r6(totals["realized_xf"] - totals["fees"] - totals["open_cost_xf"] + totals["open_settle"]))
    any_pending = any(x["status"].startswith("pending") for x in positions)
    # Fee check: does each order's fee equal ceil(0.07*C*p*(1-p)) to $0.0001, with average_fee_paid = that / C truncated
    # to 4 dp? If so, sum(count x average) understates the true fees slightly.
    from decimal import ROUND_CEILING, ROUND_DOWN, Decimal as Dec
    recon, fits = Dec(0), 0
    for f in FILLS:
        c, p_ = Dec(str(f["count"])), Dec(str(f["price"]))
        tot = (Dec("0.07") * c * p_ * (1 - p_)).quantize(Dec("0.0001"), rounding=ROUND_CEILING)
        recon += tot
        fits += (tot / c).quantize(Dec("0.0001"), rounding=ROUND_DOWN) == Dec(str(f["fee_per_contract"])).quantize(Dec("0.0001"))
    fee_check = dict(rule="per-order fee = ceil(0.07 x C x p x (1-p), $0.0001); average_fee_paid = fee / C truncated to 4 dp",
                     orders_consistent=f"{fits}/{len(FILLS)}", reconstructed_total_fees=float(recon),
                     total_pnl_with_reconstructed_fees=r6(totals["total"] - (float(recon) - totals["fees"])))
    return dict(
        n_fills=len(ledger), n_buy_fills=sum(1 for x in ledger if x["action"] == "buy"),
        n_sell_fills=sum(1 for x in ledger if x["action"] == "sell"),
        zero_fill_orders=ZERO_FILLS,
        total_night_pnl=r6(totals["total"]), total_fees=r6(totals["fees"]),
        realized_pnl_incl_fees=r6(totals["realized"]), realized_pnl_ex_fees=r6(totals["realized_xf"]),
        open_cost_incl_fees=r6(totals["open_cost"]), open_cost_ex_fees=r6(totals["open_cost_xf"]),
        open_settle_value=r6(totals["open_settle"]), includes_pending_valuations=any_pending, fee_check=fee_check,
        kalshi_check=dict(kalshi=kalshi, ours=ours,
                          match=abs(ours["total"] - kalshi["total"]) < 0.006 and abs(ours["realized_ex_fee"] + 0.24) < 0.006,
                          note="fees = sum(count x average_fee_paid); average_fee_paid is a 4-dp per-contract "
                               "average, so the sum can differ from Kalshi's cent figure by a fraction of a cent"),
        positions=positions), ledger


A, LEDGER = section_a()

# ----------------------------------------------------------------------------------------------------------------
# B. P&L decomposition
# ----------------------------------------------------------------------------------------------------------------
def section_b():
    rows, by_pos = [], defaultdict(Counter)
    entry = exit_ = fees = 0.0
    for f in FILLS:
        key = f"{f['ticker']}|{f['outcome']}"
        fees += f["fee_total"]
        by_pos[key]["fees"] += f["fee_total"]
        if f["fair"] is None:
            rows.append(dict(ts=f["ts"], ticker=f["ticker"], action=f["action"], edge=None, note="no fair"))
            continue
        if f["action"] == "buy":
            e = f["count"] * (f["fair"] - f["price"])
            entry += e
            by_pos[key]["entry_edge"] += e
            by_pos[key]["fair_in_x_n"] -= f["count"] * f["fair"]
        else:
            e = f["count"] * (f["price"] - f["fair"])
            exit_ += e
            by_pos[key]["exit_edge"] += e
            by_pos[key]["fair_in_x_n"] += f["count"] * f["fair"]
        rows.append(dict(ts=f["ts"], ticker=f["ticker"], outcome=f["outcome"], action=f["action"], count=f["count"],
                         price=r6(f["price"]), fair=r6(f["fair"]), fair_source=f["fair_source"], edge=r6(e),
                         edge_per_contract=r6(e / f["count"])))
    total = A["total_night_pnl"]
    mm = total - entry - exit_ + fees
    per_pos = []
    for p in A["positions"]:
        key = f"{p['ticker']}|{p['outcome']}"
        c = by_pos[key]
        settle_part = (p["open_qty"] * p["open_settle_value_per_contract"]) if p["open_qty"] else 0.0
        mm_p = p["total_pnl"] - c["entry_edge"] - c["exit_edge"] + c["fees"]
        per_pos.append(dict(ticker=p["ticker"], outcome=p["outcome"], total_pnl=p["total_pnl"],
                            entry_edge=r6(c["entry_edge"]), exit_edge=r6(c["exit_edge"]), fees=r6(c["fees"]),
                            market_moves=r6(mm_p),
                            market_moves_check=r6(c["fair_in_x_n"] + settle_part)))
    # closed round trips: how far the fair and the traded price moved between the buy and the sell
    trips, state = [], defaultdict(lambda: dict(n=0.0, px=0.0, fair=0.0, t=None))
    for f in sorted(FILLS, key=lambda x: x["t"]):
        s = state[(f["ticker"], f["outcome"])]
        if f["action"] == "buy":
            s["t"] = s["t"] or f["t"]
            s["px"] += f["count"] * f["price"]
            s["fair"] += f["count"] * (f["fair"] or 0.0)
            s["n"] += f["count"]
        elif s["n"] > 0:
            n = s["n"]
            trips.append(dict(ticker=f["ticker"], outcome=f["outcome"], n=n, open_ts=iso(s["t"]), close_ts=f["ts"],
                              minutes_held=round((f["t"] - s["t"]) / 60, 1), buy_price=r6(s["px"] / n),
                              sell_price=r6(f["price"]), fair_at_buy=r6(s["fair"] / n), fair_at_sell=r6(f["fair"]),
                              price_change=r6(f["price"] - s["px"] / n),
                              fair_change=r6(None if f["fair"] is None else f["fair"] - s["fair"] / n)))
            state[(f["ticker"], f["outcome"])] = dict(n=0.0, px=0.0, fair=0.0, t=None)
    return dict(sum_entry_edge=r6(entry), sum_exit_edge=r6(exit_), total_fees=r6(fees), total_pnl=r6(total),
                market_moves=r6(mm), identity="total = entry_edge + exit_edge - fees + market_moves; "
                "market_moves = sum over sells of n*fair_exit + open n*settle - sum over buys of n*fair_entry",
                market_moves_closed_round_trips=r6(sum(t["n"] * t["fair_change"] for t in trips)),
                market_moves_open_positions_to_settlement=r6(mm - sum(t["n"] * t["fair_change"] for t in trips)),
                closed_round_trips=trips, per_fill=rows, per_position=per_pos,
                exit_rule_note="a rule exit needs bid - fee >= fair + 0.5c after a buy at ask with fair >= ask + fee + 1c, "
                               "so unless the thin bid rallies above the entry ask, an exit can only fire after the fair "
                               "has fallen by at least the spread + both fees + 1.5c: positive entry and exit edges "
                               "with negative market moves are partly built into the rules")


B = section_b()

# ----------------------------------------------------------------------------------------------------------------
# C. Markouts of buy fills
# ----------------------------------------------------------------------------------------------------------------
def classify(d_thin, d_fair, tol=MOVE_TOL):
    """Buys assume fair > price: 'toward' means thin mid up, or fair down (outcome terms)."""
    if d_thin is None or d_fair is None:
        return "n/a"
    if abs(d_thin) < tol and abs(d_fair) < tol:
        return "neither moved"
    if abs(d_thin) >= tol and abs(d_fair) >= tol and (d_thin > 0) == (d_fair > 0):
        return "both moved (game event)"
    if d_thin >= tol and d_fair <= -tol:
        return "converged from both sides"
    if d_thin >= tol and abs(d_fair) < tol:
        return "thin moved toward fair (gap real)"
    if d_fair <= -tol and abs(d_thin) < tol:
        return "fair moved toward thin (fair stale/noisy)"
    return "diverged (gap widened)"


THIN_MID_MAX_SPREAD = 0.10    # a thin-market mid with a wider spread (or an empty side) is not used as its price


def section_c():
    per = []
    for f in FILLS:
        if f["action"] != "buy":
            continue
        row = dict(ts=f["ts"], ticker=f["ticker"], outcome=f["outcome"], count=f["count"], price=r6(f["price"]),
                   fair_preflight=r6(f["fair"]), gap_preflight=r6(None if f["fair"] is None else f["fair"] - f["price"]),
                   horizons={})
        # state at the end of the fill's own minute (<= 59 s after the fill) and the deep candle of that minute
        end = (f["t"] // 60 + 1) * 60
        fe, _ = fair_out_at(f["ticker"], f["outcome"], end)
        row["fair_at_fill_minute_end"] = r6(fe)
        if is_prot(f["ticker"]) and twin_of(f["ticker"]) in BOOKS:
            row["twin_candle_of_fill_minute"] = BOOKS[twin_of(f["ticker"])].covering(f["t"])
        if f["ticker"] in BOOKS:
            row["thin_candle_of_fill_minute"] = BOOKS[f["ticker"]].covering(f["t"])
        base = None
        for k in HORIZONS:
            at = f["t"] + 60 * k
            fo, fage = fair_out_at(f["ticker"], f["outcome"], at)
            tb, ta, tm, tage = thin_out_at(f["ticker"], f["outcome"], at)
            sp = None if tb is None or ta is None else ta - tb
            reliable = tm is not None and sp is not None and sp <= THIN_MID_MAX_SPREAD + 1e-9
            h = dict(at=iso(at), fair=r6(fo), thin_bid=r6(tb), thin_ask=r6(ta), thin_mid=r6(tm), thin_spread=r6(sp),
                     thin_mid_reliable=reliable, fair_inputs_age_s=fage, thin_age_s=tage,
                     fair_markout=r6(None if fo is None else fo - f["price"]),
                     mid_markout=r6(None if tm is None else tm - f["price"]),
                     bid_markout=r6(None if tb is None else tb - f["price"]))
            if k == 0:
                base = dict(fair=fo, mid=tm, ask=ta, reliable=reliable)
                h["fair_candle_minus_preflight"] = r6(None if fo is None or f["fair"] is None else fo - f["fair"])
            else:
                d_fair = None if fo is None or base["fair"] is None else fo - base["fair"]
                if reliable and base["reliable"]:
                    basis, d_thin = "mid", tm - base["mid"]
                elif ta is not None and base["ask"] is not None:
                    basis, d_thin = "ask (mid unusable: wide/empty book)", ta - base["ask"]
                else:
                    basis, d_thin = "none", None
                # entry-snapshot baseline: the fair the trader saw (preflight) and the ask it paid
                d_fair_e = None if fo is None or f["fair"] is None else fo - f["fair"]
                readable = ta is not None and sp is not None and sp <= THIN_MID_MAX_SPREAD + 1e-9
                d_ask_e = (ta - f["price"]) if readable else None
                if readable:
                    lab = classify(d_ask_e, d_fair_e)
                else:
                    lab = ("thin book unreadable (spread > 10c or empty side); fair "
                           + ("n/a" if d_fair_e is None else "down" if d_fair_e <= -MOVE_TOL else "up" if d_fair_e >= MOVE_TOL else "flat"))
                h.update(d_thin=r6(d_thin), thin_basis=basis, d_fair=r6(d_fair),
                         d_fair_vs_preflight=r6(d_fair_e), d_ask_vs_entry_price=r6(d_ask_e),
                         gap_now=r6(None if fo is None or tm is None else fo - tm),
                         gap_now_vs_ask=r6(None if fo is None or ta is None else fo - ta),
                         label=lab, label_candle_baseline=classify(d_thin, d_fair))
            row["horizons"][f"+{k}"] = h
        per.append(row)
    avgs = {}
    for k in HORIZONS:
        key = f"+{k}"
        out = {}
        for m in ("fair_markout", "mid_markout", "bid_markout"):
            xs = [(r["horizons"][key][m], r["count"]) for r in per if r["horizons"][key][m] is not None
                  and (m != "mid_markout" or r["horizons"][key]["thin_mid_reliable"])]
            out[f"{m}_mean_per_fill"] = r6(mean([x for x, _ in xs]))
            out[f"{m}_contract_weighted"] = r6(sum(x * n for x, n in xs) / sum(n for _, n in xs)) if xs else None
            out[f"{m}_n"] = len(xs)
        if k:
            out["fair_drift_vs_preflight_mean"] = r6(mean([r["horizons"][key].get("d_fair_vs_preflight") for r in per]))
            out["fair_drift_vs_candle0_mean"] = r6(mean([r["horizons"][key].get("d_fair") for r in per]))
            out["thin_move_mean"] = r6(mean([r["horizons"][key].get("d_thin") for r in per]))
            out["thin_ask_vs_entry_price_mean"] = r6(mean([r["horizons"][key].get("d_ask_vs_entry_price") for r in per]))
            out["labels"] = dict(Counter(r["horizons"][key]["label"] for r in per))
            out["labels_candle_baseline"] = dict(Counter(r["horizons"][key]["label_candle_baseline"] for r in per))
        avgs[key] = out
    avgs["+0"]["fair_candle_minus_preflight_mean"] = r6(mean([r["horizons"]["+0"]["fair_candle_minus_preflight"] for r in per]))
    avgs["+0"]["gap_preflight_mean"] = r6(mean([r["gap_preflight"] for r in per]))
    avgs["+0"]["gap_preflight_contract_weighted"] = r6(sum(r["gap_preflight"] * r["count"] for r in per) / sum(r["count"] for r in per))
    by_group = {}
    for g, sel in (("margin_buckets", lambda r: not is_prot(r["ticker"])), ("protected_props", lambda r: is_prot(r["ticker"]))):
        rs = [r for r in per if sel(r)]
        by_group[g] = dict(n_fills=len(rs), gap_preflight_mean=r6(mean([r["gap_preflight"] for r in rs])),
                           **{f"+{k}": dict(fair_markout=r6(mean([r["horizons"][f"+{k}"]["fair_markout"] for r in rs])),
                                            bid_markout=r6(mean([r["horizons"][f"+{k}"]["bid_markout"] for r in rs])),
                                            fair_drift_vs_preflight=r6(mean([r["horizons"][f"+{k}"].get("d_fair_vs_preflight") for r in rs])),
                                            thin_ask_vs_entry=r6(mean([r["horizons"][f"+{k}"].get("d_ask_vs_entry_price") for r in rs])))
                              for k in HORIZONS})
    return dict(per_fill=per, averages=avgs, by_group=by_group,
                method="state at time t = close of the latest 1-min candle that ended at or before t (carried "
                       "forward; no look-ahead). +0 is therefore up to 59 s older than the fill. Outcome terms: NO "
                       "prices are 1 - YES. mid_markout averages use only thin mids with spread <= 10c. 'label' "
                       "(primary) compares the entry snapshot -- the preflight fair and the ask paid -- with the candle "
                       "fair and thin ask at +k: ask up >= 1c = thin moved toward fair, fair down >= 1c = fair moved "
                       "toward thin, both >= 1c in the same direction = game event. 'label_candle_baseline' does the "
                       "same from the +0 candle (thin mid, or ask when the mid is unusable). In the protected-prop "
                       "books the ask jumps between sparse resting orders, so their thin moves are noisy.")


C = section_c()

# ----------------------------------------------------------------------------------------------------------------
# D/E. Signal funnel, episodes, persistence
# ----------------------------------------------------------------------------------------------------------------
AUTO_STARTS = [r for r in ORDERS if r["event"] == "auto_started"]
AUTO0 = AUTO_STARTS[0]["_t"] if AUTO_STARTS else None


def build_episodes():
    by_key = defaultdict(list)
    for s in SIGNALS:
        by_key[(s["ticker"], s["outcome"])].append(s)
    eps = []
    for (t, o), ss in by_key.items():
        cur = None
        for s in ss:
            if cur is None or s["t"] - cur["signals"][-1]["t"] >= EPISODE_GAP_S:
                cur = dict(ticker=t, outcome=o, signals=[], attempts=[])
                eps.append(cur)
            cur["signals"].append(s)
            s["ep"] = cur
    eps.sort(key=lambda e: (e["signals"][0]["t"], e["ticker"]))
    for i, e in enumerate(eps):
        e["id"] = i
        e["first_t"], e["last_t"] = e["signals"][0]["t"], e["signals"][-1]["t"]
        e["period"] = "auto" if AUTO0 is not None and e["first_t"] >= AUTO0 else "manual"
    return eps


EPISODES = build_episodes()


def match_attempts():
    """Auto mode: scan() writes the signal row, then calls buy() at once, so each attempt is matched to the latest
    unmatched signal of the same ticker (and outcome, when known) at most MATCH_WINDOW_S older."""
    sig_by_ticker = defaultdict(list)
    for s in SIGNALS:
        s["attempt"] = None
        sig_by_ticker[s["ticker"]].append(s)
    unmatched = []
    for a in ATTEMPTS:
        a["signal"] = None
        if AUTO0 is None or a["t"] < AUTO0:
            continue
        cands = [s for s in sig_by_ticker.get(a["ticker"], []) if s["attempt"] is None
                 and 0 <= a["t"] - s["t"] <= MATCH_WINDOW_S and (a["outcome"] is None or s["outcome"] == a["outcome"])]
        if not cands:
            unmatched.append(a)
            continue
        s = max(cands, key=lambda x: (x["t"], x["i"]))
        s["attempt"], a["signal"] = a, s
        s["ep"]["attempts"].append(a)
    # manual period: attach preflights that follow a signal of the same ticker within 120 s
    for e in EPISODES:
        if e["period"] != "manual":
            continue
        for a in ATTEMPTS:
            if a["ticker"] == e["ticker"] and a["outcome"] in (None, e["outcome"]) and e["first_t"] <= a["t"] <= e["last_t"] + 120 \
                    and a["signal"] is None and (AUTO0 is None or a["t"] < AUTO0):
                e["attempts"].append(a)
                a["manual_episode"] = e["id"]
    return unmatched


UNMATCHED_ATTEMPTS = match_attempts()


def runs_ev_ok(e):
    """Runs of consecutive attempts in the episode whose book EV >= 1c (pre-preflight aborts break a run)."""
    runs, cur = [], None
    seq = sorted(e["attempts"], key=lambda a: a["t"])
    for i, a in enumerate(seq):
        ok = a["kind"] == "preflight" and a["ev"] is not None and a["ev"] >= EV_OK
        if ok:
            if cur is None:
                cur = dict(start=a["t"], end=a["t"], n=0, first=a)
            cur["end"], cur["n"] = a["t"], cur["n"] + 1
            if a["filled"]:
                cur["ended_by"] = "filled"
        elif cur is not None:
            cur["ended_by"] = cur.get("ended_by") or ("book EV < 1c (hold)" if a["kind"] == "preflight"
                                                      else f"pre-preflight abort ({a['reason_cat']})")
            runs.append(cur)
            cur = None
    if cur is not None:
        cur["ended_by"] = cur.get("ended_by") or "signal stopped (listing no longer flagged)"
        runs.append(cur)
    for r in runs:
        r["dur_s"] = r["end"] - r["start"]
    return runs


def episode_row(e):
    pf = [a for a in e["attempts"] if a["kind"] == "preflight"]
    evs = [a["ev"] for a in pf if a["ev"] is not None]
    n_ok = sum(1 for x in evs if x >= EV_OK)
    cats = Counter(a["reason_cat"] for a in e["attempts"] if a["result"] == "abort")
    holds = sum(1 for a in pf if a["result"] == "hold")
    sent = sum(1 for a in e["attempts"] if a["sent"])
    filled = sum(a["filled"] for a in e["attempts"])
    if filled > 0:
        label = "filled"
    elif sent:
        label = "sent_unfilled"
    elif cats.get("budget_cap"):
        label = "budget_capped"
    elif n_ok:
        label = "ev_ok_blocked_other"
    elif pf and holds == len(pf):
        label = "phantom"
    elif not pf and e["attempts"] and all(a["reason_cat"] == "no_fair_value" for a in e["attempts"]):
        label = "no_fair_value"
    elif not e["attempts"]:
        label = "no_attempt"
    else:
        label = "mixed"
    runs = runs_ev_ok(e)
    longest = max(runs, key=lambda r: (r["dur_s"], r["n"])) if runs else None
    # episode markout: first EV-ok preflight's book price vs candle fair / thin mid 5 and 15 min later, and the result
    mk = {}
    if runs:
        a = runs[0]["first"]
        mk = dict(first_ok_ts=a["ts"], first_ok_price=a["price"], first_ok_fair=r6(a["fair_out"]), first_ok_ev=a["ev"])
        for k in (5, 15):
            fo, _ = fair_out_at(e["ticker"], e["outcome"], a["t"] + 60 * k)
            _, _, tm, _ = thin_out_at(e["ticker"], e["outcome"], a["t"] + 60 * k)
            mk[f"fair_p{k}"], mk[f"thin_mid_p{k}"] = r6(fo), r6(tm)
        sv, st, _ = settle_value(e["ticker"], e["outcome"])
        mk.update(settle_value=r6(sv), settle_status=st, settle_minus_price=r6(None if sv is None else sv - a["price"]))
    return dict(id=e["id"], period=e["period"], ticker=e["ticker"], outcome=e["outcome"], first_ts=iso(e["first_t"]),
                last_ts=iso(e["last_t"]), n_signals=len(e["signals"]), n_attempts=len(e["attempts"]),
                n_preflights=len(pf), n_ev_ok=n_ok, max_book_ev=r6(max(evs) if evs else None),
                max_listing_ev=r6(max(s["listing_ev"] for s in e["signals"])), n_holds=holds,
                n_abort_budget_cap=cats.get("budget_cap", 0), n_abort_no_fair=cats.get("no_fair_value", 0),
                n_abort_other=sum(v for k, v in cats.items() if k not in ("budget_cap", "no_fair_value")),
                n_sent=sent, n_filled=filled, label=label,
                longest_ev_ok_run_s=longest["dur_s"] if longest else None,
                longest_ev_ok_run_preflights=longest["n"] if longest else None,
                longest_run_ended_by=longest["ended_by"] if longest else None, **mk)


EP_ROWS = [episode_row(e) for e in EPISODES]


def listing_vs_book(s, a):
    """Split book EV - listing EV into a thin-price part and a fair part (outcome terms, EV = fair - p - fee(p))."""
    p_l, ev_l = s["listing_price"], s["listing_ev"]
    f_l = ev_l + p_l + fee(p_l)               # listing-implied fair in outcome terms
    price_part = -((a["price"] + fee(a["price"])) - (p_l + fee(p_l)))
    fair_part = a["fair_out"] - f_l
    return price_part, fair_part


def section_d():
    a0, a1 = AUTO0, AUTO_END
    sig = [s for s in SIGNALS if a0 <= s["t"] <= a1]
    eps = [e for e in EP_ROWS if e["period"] == "auto"]
    att = [a for a in ATTEMPTS if a0 <= a["t"] <= a1]
    pf = [a for a in att if a["kind"] == "preflight"]
    ev_ok = [a for a in pf if a["ev"] is not None and a["ev"] >= EV_OK]
    holds = [a for a in pf if a["result"] == "hold"]
    aborts = Counter(a["reason_cat"] for a in att if a["result"] == "abort")
    pre_abort = Counter(a["reason_cat"] for a in att if a["kind"] == "pre_abort")
    sent = [a for a in att if a["sent"]]
    filled = [a for a in att if a["filled"] > 0]
    eps_with_pf = [e for e in eps if e["n_preflights"] > 0]
    phantom = [e for e in eps_with_pf if e["label"] == "phantom"]
    # why phantoms: listing vs book split for every matched hold
    causes, parts = Counter(), []
    for a in holds:
        s = a.get("signal")
        if not s:
            continue
        pp, fp = listing_vs_book(s, a)
        parts.append((pp, fp))
        causes["thin price (listing ask/bid stale)" if abs(pp) >= abs(fp) else "fair (deep-market listing stale)"] += 1
    lags = [a["t"] - a["signal"]["t"] for a in att if a.get("signal")]
    # budget-capped opportunities by ticker (book EV >= 1c, blocked only by tonight's cap) -- hindsight, one game
    capped = defaultdict(list)
    for a in att:
        if a["reason_cat"] == "budget_cap":
            capped[(a["ticker"], a["outcome"])].append(a)
    capped_rows = []
    for (t, o), xs in sorted(capped.items(), key=lambda kv: kv[1][0]["t"]):
        sv, st, _ = settle_value(t, o)
        capped_rows.append(dict(ticker=t, outcome=o, n_preflights=len(xs), first_ts=xs[0]["ts"], last_ts=xs[-1]["ts"],
                                first_price=xs[0]["price"], first_fair=r6(xs[0]["fair_out"]), max_ev=r6(max(x["ev"] for x in xs)),
                                median_price=r6(med([x["price"] for x in xs])), settle_value=r6(sv), settle_status=st,
                                settle_minus_first_price=r6(None if sv is None else sv - xs[0]["price"])))
    unmatched_sig = [dict(ts=s["ts"], ticker=s["ticker"], outcome=s["outcome"], listing_price=s["listing_price"],
                          listing_ev=s["listing_ev"]) for s in sig if s.get("attempt") is None]
    return dict(
        window=[iso(a0), iso(a1)], auto_started_events=[r["ts"] for r in AUTO_STARTS],
        signal_rows=len(sig), episodes=len(eps), buy_attempts=len(att), buy_preflights=len(pf),
        preflights_book_ev_ge_1c=len(ev_ok), holds_book_ev_lt_1c=len(holds),
        aborts_by_reason={"budget_cap": aborts.get("budget_cap", 0), "already_held": aborts.get("already_held", 0),
                          "no_fair_value": aborts.get("no_fair_value", 0),
                          "other": {k: v for k, v in aborts.items() if k not in ("budget_cap", "already_held", "no_fair_value")}},
        aborts_before_preflight_by_reason=dict(pre_abort),
        signal_to_attempt_lag_s=dict(median=med(lags), max=max(lags) if lags else None),
        budget_capped_by_ticker=capped_rows,
        budget_capped_hindsight=dict(
            n_ticker_sides=len(capped_rows),
            n_settled_in_favour=sum(1 for x in capped_rows if x["settle_minus_first_price"] is not None and x["settle_minus_first_price"] > 0),
            one_contract_each_at_first_price_settle_minus_price_sum=r6(sum(x["settle_minus_first_price"] for x in capped_rows
                                                                           if x["settle_minus_first_price"] is not None)),
            same_by_fair_at_first_price_sum=r6(sum(x["first_fair"] - x["first_price"] for x in capped_rows)),
            taker_fees_for_those_sum=r6(sum(fee(x["first_price"]) for x in capped_rows)),
            note="hindsight on one game, ex-fee unless stated; outcomes are correlated (same game, same players)"),
        unmatched_signals=unmatched_sig,
        sent=len(sent), filled_orders=len(filled), filled_contracts=sum(a["filled"] for a in filled),
        filled_tickers=[f"{a['ts']} {a['ticker']} {a['outcome']} x{a['filled']}" for a in filled],
        episodes_by_label=dict(Counter(e["label"] for e in eps)),
        episodes_with_preflight=len(eps_with_pf), phantom_episodes=len(phantom),
        phantom_rate_of_episodes_with_preflight=r6(len(phantom) / len(eps_with_pf)) if eps_with_pf else None,
        phantom_or_no_fair_rate_of_all_episodes=r6(sum(1 for e in eps if e["label"] in ("phantom", "no_fair_value"))
                                                   / len(eps)) if eps else None,
        signals_matched_to_attempts=sum(1 for s in sig if s.get("attempt") is not None),
        signals_without_attempt=sum(1 for s in sig if s.get("attempt") is None),
        attempts_without_signal=len([a for a in UNMATCHED_ATTEMPTS if a0 <= a["t"] <= a1]),
        hold_cause_counts=dict(causes),
        hold_listing_vs_book_median_price_part=r6(med([p for p, _ in parts])),
        hold_listing_vs_book_median_fair_part=r6(med([f for _, f in parts])),
        hold_listing_vs_book_note="book EV - listing EV = price part + fair part (outcome terms). Negative price part "
                                  "= the book's price was worse than the listing's; negative fair part = the book-based "
                                  "fair was lower than the listing-based fair.")


D = section_d()


def section_e():
    a0, a1 = AUTO0, AUTO_END
    eps = [e for e in EPISODES if e["period"] == "auto"]
    rows = {r["id"]: r for r in EP_ROWS}
    ok_eps = [e for e in eps if rows[e["id"]]["n_ev_ok"] > 0]
    longest, all_runs = [], []
    for e in ok_eps:
        rs = runs_ev_ok(e)
        all_runs += rs
        longest.append(max(r["dur_s"] for r in rs))
    # per ticker+outcome union of EV-ok time across episodes (gaps < 60 s bridged)
    per_key = defaultdict(list)
    for e in ok_eps:
        for r in runs_ev_ok(e):
            per_key[(e["ticker"], e["outcome"])].append((r["start"], r["end"]))
    spans = {}
    for k, iv in per_key.items():
        iv.sort()
        merged = [list(iv[0])]
        for s, t in iv[1:]:
            if s - merged[-1][1] <= 60:
                merged[-1][1] = max(merged[-1][1], t)
            else:
                merged.append([s, t])
        spans[f"{k[0]} {k[1]}"] = [dict(start=iso(s), end=iso(t), dur_s=t - s) for s, t in merged]
    # scan cadence: consecutive signal rows of the same ticker+outcome
    gaps_in, gaps_all = [], []
    by_key = defaultdict(list)
    for s in SIGNALS:
        if a0 <= s["t"] <= a1:
            by_key[(s["ticker"], s["outcome"])].append(s["t"])
    for ts in by_key.values():
        for x, y in zip(ts, ts[1:]):
            gaps_all.append(y - x)
            if y - x < EPISODE_GAP_S:
                gaps_in.append(y - x)
    # consecutive preflights of the same ticker (book checks)
    pf_gaps = []
    pf_by = defaultdict(list)
    for a in ATTEMPTS:
        if a0 <= a["t"] <= a1:
            pf_by[a["ticker"]].append(a["t"])
    for ts in pf_by.values():
        pf_gaps += [y - x for x, y in zip(ts, ts[1:]) if y - x < EPISODE_GAP_S]
    # whole-scan cadence: distinct signal timestamps (any ticker)
    uniq = sorted({s["t"] for s in SIGNALS if a0 <= s["t"] <= a1})
    # pre-auto manual period: first signal of an episode -> first preflight/order for that ticker
    manual = []
    for e in EPISODES:
        if e["period"] != "manual":
            continue
        later = [r for r in ORDERS if r.get("ticker") == e["ticker"] and r["event"] in ("trade_buy_preflight", "trade_buy_body")
                 and e["first_t"] <= r["_t"] <= e["first_t"] + 300 and (AUTO0 is None or r["_t"] < AUTO0)]
        pre = next((r for r in later if r["event"] == "trade_buy_preflight"), None)
        body = next((r for r in later if r["event"] == "trade_buy_body" and r.get("send")), None)
        res = next((r for r in ORDERS if r["event"] == "trade_buy_result" and r.get("ticker") == e["ticker"]
                    and body and r["_t"] >= body["_t"]), None)
        manual.append(dict(ticker=e["ticker"], outcome=e["outcome"], first_signal=iso(e["first_t"]),
                           listing_price=e["signals"][0]["listing_price"], listing_ev=e["signals"][0]["listing_ev"],
                           preflight_ts=pre["ts"] if pre else None, preflight_price=fnum(pre["price"]) if pre else None,
                           preflight_ev=fnum(pre["ev_per_contract"]) if pre else None,
                           order_ts=body["ts"] if body else None,
                           delay_signal_to_order_s=(body["_t"] - e["first_t"]) if body else None,
                           filled=fnum((res.get("result") or {}).get("fill_count")) if res else None))
    # dry-run -> send delays in the manual period without signal rows (00:27Z)
    dry = []
    for k, r in enumerate(ORDERS):
        if r["event"] == "trade_buy_body" and r.get("send") is False:
            nxt = next((x for x in ORDERS[k + 1:] if x["event"] == "trade_buy_body" and x.get("ticker") == r["ticker"]
                        and x.get("send")), None)
            dry.append(dict(ticker=r["ticker"], dry_run_ts=r["ts"], sent_ts=nxt["ts"] if nxt else None,
                            delay_s=(nxt["_t"] - r["_t"]) if nxt else None))
    for k, r in enumerate(ORDERS):
        if r["event"] == "order_body" and r.get("send") is False:
            nxt = next((x for x in ORDERS[k + 1:] if x["event"] == "order_body" and x.get("send")), None)
            dry.append(dict(ticker=MANUAL_TICKER, dry_run_ts=r["ts"], sent_ts=nxt["ts"] if nxt else None,
                            delay_s=(nxt["_t"] - r["_t"]) if nxt else None))
    return dict(
        episodes_with_ev_ok=len(ok_eps),
        longest_run_per_episode_s=dict(median=med(longest), max=max(longest) if longest else None,
                                       n_single_preflight=sum(1 for e in ok_eps if max(r["n"] for r in runs_ev_ok(e)) == 1),
                                       values=sorted(longest)),
        runs_ended_by=dict(Counter(r["ended_by"] for r in all_runs)),
        ev_ok_spans_per_ticker=spans,
        signal_gap_same_ticker_s=dict(median_within_episode=med(gaps_in), median_all=med(gaps_all),
                                      n_within=len(gaps_in), n_all=len(gaps_all)),
        preflight_gap_same_ticker_s=dict(median=med(pf_gaps), n=len(pf_gaps)),
        distinct_signal_timestamps_gap_s=dict(median=med([y - x for x, y in zip(uniq, uniq[1:])])),
        manual_period_signal_to_order=manual, dry_run_to_send=dry,
        caveat="persistence is measured only while the stale /events listing kept flagging the ticker (listing EV "
               ">= 1.5c and size >= 3), at the scan cadence; a run of one preflight lasted < one cadence.")


E_ = section_e()

# ----------------------------------------------------------------------------------------------------------------
# F. Fair at entry vs outcome
# ----------------------------------------------------------------------------------------------------------------
def section_f():
    rows = []
    for f in FILLS:
        if f["action"] != "buy":
            continue
        v, st, det = settle_value(f["ticker"], f["outcome"])
        rows.append(dict(ts=f["ts"], ticker=f["ticker"], outcome=f["outcome"], count=f["count"], price=r6(f["price"]),
                         fair_at_entry=r6(f["fair"]), outcome_value=r6(v), settle_status=st,
                         hold_to_settlement_pnl=r6(None if v is None else f["count"] * (v - f["price"]) - f["fee_total"]),
                         fair_minus_outcome=r6(None if v is None or f["fair"] is None else f["fair"] - v)))
    held = [r["hold_to_settlement_pnl"] for r in rows if r["hold_to_settlement_pnl"] is not None]
    return dict(per_buy_fill=rows, sum_fair_at_entry_x_count=r6(sum(r["fair_at_entry"] * r["count"] for r in rows if r["fair_at_entry"] is not None)),
                sum_outcome_x_count=r6(sum(r["outcome_value"] * r["count"] for r in rows if r["outcome_value"] is not None)),
                counterfactual_hold_everything_to_settlement_pnl=r6(sum(held)),
                actual_total_pnl=A["total_night_pnl"],
                note="one game; outcomes of the buckets are mutually exclusive and all props depend on the same game, "
                     "so these are not independent draws -- no calibration claim")


F = section_f()

# ----------------------------------------------------------------------------------------------------------------
# Data checks
# ----------------------------------------------------------------------------------------------------------------
def data_checks():
    out = {}
    out["candle_coverage"] = {t: BOOKS[t].gap_stats() for t in sorted(BOOKS)
                              if t in set(DEEP.values()) or t in {f["ticker"] for f in FILLS}
                              or t in {twin_of(f["ticker"]) for f in FILLS if is_prot(f["ticker"])}}
    out["candle_fetch_errors"] = {t: b.error for t, b in BOOKS.items() if b.error}
    out["market_fetch_errors"] = {t: m["_error"] for t, m in MARKETS.items() if "_error" in m}
    # margin fair availability during the game
    mins = list(range(ep("2026-10-20T00:15:00Z"), ep("2026-10-20T03:15:00Z") + 1, 60))
    avail = sum(1 for t in mins if fair_yes_at(f"KXNFLWINMARGIN-{E}-WAS1TO6", t)[0] is not None)
    out["margin_fair_computable_share_0015_0315"] = r6(avail / len(mins))
    # live preflight vs candle reconstruction (all auto-mode buy preflights, book-priced)
    dfair, dprice = [], []
    for a in ATTEMPTS:
        if a["kind"] != "preflight" or AUTO0 is None or a["t"] < AUTO0 or a["t"] > AUTO_END:
            continue
        fo, _ = fair_out_at(a["ticker"], a["outcome"], a["t"])
        tb, ta, _, _ = thin_out_at(a["ticker"], a["outcome"], a["t"])
        if fo is not None and a["fair_out"] is not None:
            dfair.append(abs(fo - a["fair_out"]))
        if ta is not None and a["price"] is not None:
            dprice.append(abs(ta - a["price"]))
    out["preflight_vs_candle"] = dict(
        n_fair=len(dfair), median_abs_fair_diff=r6(med(dfair)), share_fair_within_1c=r6(sum(1 for x in dfair if x <= 0.0100001) / len(dfair)) if dfair else None,
        n_price=len(dprice), median_abs_price_diff=r6(med(dprice)),
        share_price_equal=r6(sum(1 for x in dprice if x < 1e-9) / len(dprice)) if dprice else None,
        note="candle state is the previous minute's close, the preflight is the live book at that second")
    # watcher cross-check for WAS1TO6 (listing quotes every 10-30 s): raw candle closes vs the watcher's bid/ask,
    # by alignment offset and by seconds into the minute (tests that a candle's close = the state at end_period_ts)
    bk = BOOKS.get(MANUAL_TICKER)
    wrows = [w for w in WATCH if W0 <= w["_t"] <= ep("2026-10-20T03:15:00Z")
             and fnum(w.get("margin_bid")) is not None and fnum(w.get("margin_ask")) is not None]

    def same(t, w):
        b, a, _ = bk.at_raw(t)
        return b is not None and a is not None and abs(b - fnum(w["margin_bid"])) < 1e-9 and abs(a - fnum(w["margin_ask"])) < 1e-9
    by_off = {off: r6(sum(same(w["_t"] + off, w) for w in wrows) / len(wrows)) for off in (-60, 0, 30, 60)} if wrows else {}
    bins = {}
    for w in wrows:
        b = f"{(w['_t'] % 60) // 15 * 15:02d}-{(w['_t'] % 60) // 15 * 15 + 14:02d}s"
        bins.setdefault(b, []).append(same(w["_t"], w))
    df = []
    for w in wrows:
        fo, _ = fair_out_at(MANUAL_TICKER, "yes", w["_t"])
        wf = fnum(w["margin_fair"])
        if fo is not None and wf is not None:
            df.append(abs(fo - max(0.0, min(1.0, wf))))
    empty_bid = [w["ts"] for w in wrows if fnum(w["margin_bid"]) == 0]
    out["watch_crosscheck_WAS1TO6"] = dict(
        rows=len(wrows), share_bid_ask_identical_by_offset_s=by_off,
        share_identical_by_seconds_into_minute={k: r6(sum(v) / len(v)) for k, v in sorted(bins.items())},
        median_abs_fair_diff=r6(med(df)), p90_abs_fair_diff=r6(sorted(df)[int(0.9 * len(df))] if df else None),
        watcher_rows_with_empty_bid=len(empty_bid), first_empty_bid=empty_bid[0] if empty_bid else None,
        last_empty_bid=empty_bid[-1] if empty_bid else None)
    # preflight vs fill mismatches
    mm = []
    for f in FILLS:
        if f["preflight_price"] is not None and abs(f["preflight_price"] - f["price"]) > 1e-9:
            mm.append(dict(ts=f["ts"], ticker=f["ticker"], action=f["action"], preflight_price=f["preflight_price"], fill_price=f["price"]))
        if f.get("preflight_count") is not None and abs(f["preflight_count"] - f["count"]) > 1e-9:
            mm.append(dict(ts=f["ts"], ticker=f["ticker"], action=f["action"], preflight_count=f["preflight_count"], fill_count=f["count"]))
    out["preflight_fill_mismatches"] = mm
    out["zero_fill_orders"] = ZERO_FILLS
    # unsettled / determined markets among what we traded
    out["traded_market_status"] = {t: dict(status=(MARKETS.get(t) or {}).get("status"), result=(MARKETS.get(t) or {}).get("result") or None,
                                           close_time=(MARKETS.get(t) or {}).get("close_time"),
                                           settlement_ts=(MARKETS.get(t) or {}).get("settlement_ts"),
                                           twin=twin_of(t) if is_prot(t) else None,
                                           twin_result=((MARKETS.get(twin_of(t)) or {}).get("result") or None) if is_prot(t) else None)
                                   for t in sorted({f["ticker"] for f in FILLS})}
    out["auto_errors"] = [dict(ts=r["ts"], error=r.get("error")) for r in ORDERS if r["event"] == "auto_error"]
    out["auto_started"] = [r["ts"] for r in AUTO_STARTS]
    out["auto_stopped_logged"] = any(r["event"] == "auto_stopped" for r in ORDERS)
    out["last_order_log_event"] = ORDERS[-1]["ts"] if ORDERS else None
    late = [s for s in SIGNALS if s["t"] >= ep("2026-10-20T03:15:00Z")]
    out["late_signals_after_0315"] = dict(n=len(late), max_listing_ev=r6(max((s["listing_ev"] for s in late), default=None)),
                                          examples=[f"{s['ts']} {s['outcome']} {s['ticker']} @{s['listing_price']} ev {s['listing_ev']}" for s in late[:5]])
    out["unmatched_attempts_auto"] = [dict(ts=a["ts"], ticker=a["ticker"], kind=a["kind"], result=a["result"]) for a in UNMATCHED_ATTEMPTS][:20]
    return out


CHECKS = data_checks()

# ----------------------------------------------------------------------------------------------------------------
# Write outputs
# ----------------------------------------------------------------------------------------------------------------
with (HERE / "postgame_trades.csv").open("w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(LEDGER[0]))
    w.writeheader()
    w.writerows(LEDGER)

SIG_COLS = ["ticker", "outcome", "first_ts", "last_ts", "n_signals", "n_preflights", "n_ev_ok", "max_book_ev", "label",
            "period", "id", "n_attempts", "max_listing_ev", "n_holds", "n_abort_budget_cap", "n_abort_no_fair",
            "n_abort_other", "n_sent", "n_filled", "longest_ev_ok_run_s", "longest_ev_ok_run_preflights",
            "longest_run_ended_by", "first_ok_ts", "first_ok_price", "first_ok_fair", "first_ok_ev", "fair_p5",
            "thin_mid_p5", "fair_p15", "thin_mid_p15", "settle_value", "settle_status", "settle_minus_price"]
with (HERE / "postgame_signals.csv").open("w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=SIG_COLS, extrasaction="ignore")
    w.writeheader()
    w.writerows(EP_ROWS)

RUNTIME = time.perf_counter() - T_START
OUT = dict(
    meta=dict(generated_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), event=E,
              window=[iso(W0), iso(W1)], runtime_s=round(RUNTIME, 1), fetch_s=round(T_FETCHED - T_START, 1),
              tickers_fetched=len(need), http_stats=CLIENT.http.stats, read_only=True,
              inputs=["logs/wassf_orders.jsonl", "logs/wassf_signals.csv", "logs/wassf_watch.csv",
                      "GET /markets/{t}", "GET /series/{s}/markets/{t}/candlesticks (1-min)"]),
    A_ledger=A, B_decomposition=B, C_markouts=C, D_funnel=D, E_persistence=E_, F_settlement=F, data_checks=CHECKS)
(HERE / "postgame.json").write_text(json.dumps(OUT, indent=1, default=str), encoding="utf-8")

# ----------------------------------------------------------------------------------------------------------------
# Printed summary
# ----------------------------------------------------------------------------------------------------------------
def p(*a):
    print(*a, flush=True)


def short(t):
    return t.replace(f"-{E}-", "~")


p(f"=== WAS @ SF post-game ({E}), read-only. runtime {RUNTIME:.1f}s ===")
p("\nA. LEDGER")
for x in LEDGER:
    p(f"  {x['ts'][11:19]} {x['action']:4} {short(x['ticker']):44} {x['outcome']:3} x{x['count']:<5g} @{x['price']:.4f} "
      f"fee {x['fee_total']:.4f} fair {x['fair_at_trade']} real {x['realized_pnl']}")
for q in A["positions"]:
    p(f"  POS {short(q['ticker']):44} {q['outcome']:3} total {q['total_pnl']:+.4f} realized {q['realized_pnl']:+.4f} "
      f"open {q['open_qty']:g} ({q['status']}) fees {q['fees']:.4f}")
p(f"  TOTAL night P&L {A['total_night_pnl']:+.4f}  fees {A['total_fees']:.4f}  realized ex-fee {A['realized_pnl_ex_fees']:+.4f}  "
  f"open cost ex-fee {A['open_cost_ex_fees']:.4f} -> settles {A['open_settle_value']:.4f}  Kalshi match: {A['kalshi_check']['match']}")
p(f"  fee check: {A['fee_check']}")
p("\nB. DECOMPOSITION")
p(f"  entry edge {B['sum_entry_edge']:+.4f}  exit edge {B['sum_exit_edge']:+.4f}  fees {B['total_fees']:.4f}  "
  f"market moves {B['market_moves']:+.4f}  total {B['total_pnl']:+.4f}")
for q in B["per_position"]:
    p(f"  {short(q['ticker']):44} {q['outcome']:3} entry {q['entry_edge']:+.3f} exit {q['exit_edge']:+.3f} fees {q['fees']:.3f} "
      f"moves {q['market_moves']:+.3f} total {q['total_pnl']:+.3f}")
p(f"  market moves: closed round trips {B['market_moves_closed_round_trips']:+.4f}, open positions to settlement "
  f"{B['market_moves_open_positions_to_settlement']:+.4f}")
for t in B["closed_round_trips"]:
    p(f"  trip {short(t['ticker']):44} {t['outcome']:3} x{t['n']:<4g} {t['minutes_held']:5.1f} min  price {t['buy_price']}->{t['sell_price']} "
      f"({t['price_change']:+.3f})  fair {t['fair_at_buy']}->{t['fair_at_sell']} ({t['fair_change']:+.4f})")
p("\nC. MARKOUTS (per contract, outcome terms)")
for r in C["per_fill"]:
    hs = r["horizons"]
    p(f"  {r['ts'][11:19]} {short(r['ticker']):44} {r['outcome']:3} @{r['price']:.3f} fair_pre {r['fair_preflight']} "
      f"fair_c0 {hs['+0']['fair']} fair_minEnd {r['fair_at_fill_minute_end']} thin0 {hs['+0']['thin_bid']}/{hs['+0']['thin_ask']}")
    if r.get("twin_candle_of_fill_minute"):
        p(f"      twin candle of fill minute: {r['twin_candle_of_fill_minute']}")
    for k in HORIZONS[1:]:
        h = hs[f"+{k}"]
        p(f"      +{k:<2} fair {h['fair']} thin {h['thin_bid']}/{h['thin_ask']} fairMO {h['fair_markout']} bidMO {h['bid_markout']} "
          f"| vs entry: dask {h['d_ask_vs_entry_price']} dfair {h['d_fair_vs_preflight']} -> {h['label']}"
          f" | vs candle0: dthin({h['thin_basis'][:3]}) {h['d_thin']} dfair {h['d_fair']} -> {h['label_candle_baseline']}")
for k in HORIZONS:
    av = C["averages"][f"+{k}"]
    p(f"  avg +{k:<2} fairMO/fill {av['fair_markout_mean_per_fill']} (cw {av['fair_markout_contract_weighted']})  "
      f"midMO {av['mid_markout_mean_per_fill']} (n {av['mid_markout_n']})  bidMO {av['bid_markout_mean_per_fill']}  "
      f"dfair_vs_pre {av.get('fair_drift_vs_preflight_mean')} dask {av.get('thin_ask_vs_entry_price_mean')} "
      f"{av.get('labels', '')} | candle-baseline {av.get('labels_candle_baseline', '')}")
for g, v in C["by_group"].items():
    p(f"  {g}: n {v['n_fills']} gap_pre {v['gap_preflight_mean']} " + "  ".join(
        f"+{k}: fairMO {v[f'+{k}']['fair_markout']} bidMO {v[f'+{k}']['bid_markout']} dfair {v[f'+{k}']['fair_drift_vs_preflight']} dask {v[f'+{k}']['thin_ask_vs_entry']}"
        for k in HORIZONS))
p("\nD. FUNNEL (auto period)")
for k in ("window", "signal_rows", "episodes", "buy_attempts", "buy_preflights", "preflights_book_ev_ge_1c",
          "holds_book_ev_lt_1c", "aborts_by_reason", "sent", "filled_orders", "filled_contracts", "episodes_by_label",
          "episodes_with_preflight", "phantom_episodes", "phantom_rate_of_episodes_with_preflight",
          "phantom_or_no_fair_rate_of_all_episodes", "signals_without_attempt", "attempts_without_signal",
          "unmatched_signals", "signal_to_attempt_lag_s",
          "hold_cause_counts", "hold_listing_vs_book_median_price_part", "hold_listing_vs_book_median_fair_part"):
    p(f"  {k}: {D[k]}")
p(f"  budget_capped_hindsight: {D['budget_capped_hindsight']}")
for x in D["budget_capped_by_ticker"]:
    p(f"  capped {short(x['ticker']):44} {x['outcome']:3} n {x['n_preflights']:3} {x['first_ts'][11:19]}-{x['last_ts'][11:19]} "
      f"first @{x['first_price']} fair {x['first_fair']} maxEV {x['max_ev']} -> settles {x['settle_value']} ({x['settle_status']})")
p("\nE. PERSISTENCE")
for k in ("episodes_with_ev_ok", "longest_run_per_episode_s", "runs_ended_by", "signal_gap_same_ticker_s",
          "preflight_gap_same_ticker_s", "distinct_signal_timestamps_gap_s"):
    p(f"  {k}: {E_[k]}")
for k, v in E_["ev_ok_spans_per_ticker"].items():
    p(f"  span {short(k)}: {[(s['start'][11:19], s['dur_s']) for s in v]}")
p(f"  manual period: {E_['manual_period_signal_to_order']}")
p(f"  dry-run->send: {E_['dry_run_to_send']}")
p("\nF. FAIR AT ENTRY vs OUTCOME")
for r in F["per_buy_fill"]:
    p(f"  {r['ts'][11:19]} {short(r['ticker']):44} {r['outcome']:3} x{r['count']:<4g} @{r['price']:.3f} fair {r['fair_at_entry']} "
      f"-> {r['outcome_value']} ({r['settle_status']}) hold-to-settle {r['hold_to_settlement_pnl']:+.4f}")
p(f"  counterfactual hold-everything P&L {F['counterfactual_hold_everything_to_settlement_pnl']:+.4f} vs actual {F['actual_total_pnl']:+.4f}")
p("\nDATA CHECKS")
for k in ("margin_fair_computable_share_0015_0315", "preflight_vs_candle", "watch_crosscheck_WAS1TO6",
          "preflight_fill_mismatches", "auto_errors", "auto_started", "auto_stopped_logged", "late_signals_after_0315",
          "candle_fetch_errors", "market_fetch_errors"):
    p(f"  {k}: {CHECKS[k]}")
for t, v in CHECKS["traded_market_status"].items():
    p(f"  {short(t)}: {v}")
for t, g in CHECKS["candle_coverage"].items():
    p(f"  candles {short(t):44} {g['candles_in_window']}/{g['minutes_in_window']} longest gap {g['longest_gap_min']} min, last {g['last_candle']}")
p(f"\nwrote {HERE / 'postgame.json'}, postgame_trades.csv, postgame_signals.csv")
