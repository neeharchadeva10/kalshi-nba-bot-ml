"""In-game +EV trading on WAS @ SF (2026-10-19), requested by the user; logged under research log #20/#21.

  python wassf_trade.py buy  <ticker> yes|no [--send]
  python wassf_trade.py sell <ticker>        [--send]
  python wassf_trade.py scan                  (public data only; prints entry and exit candidates)

HARD LIMITS (in code; nothing on the command line can raise them)
  * Only tickers of this one game, and only win-margin buckets (KXNFLWINMARGIN) or protected props (series ending
    in PROT; YES side only, because protection only makes YES worth MORE than its regular twin).
  * Buy only when EV >= +1c per contract after the taker fee, against a fair value computed HERE, at send time:
      margin bucket: from the winner, tie and spread mids;  protected prop: the regular twin's mid (spread <= 3c).
  * At most $1.00 and 10 contracts per buy, no adding to a ticker already held, and at most $3.50 of buys in total
    tonight (the user's account-wide $5 loss cap had $1.11 used).
  * Every order is immediate-or-cancel. Sells are reduce_only and only when the bid is at or above fair.
  * Dry run unless --send. Every step is appended to logs/wassf_orders.jsonl. No key material is printed or logged.
"""
import json
import math
import sys
import uuid
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

E = "26OCT19WASSF"
ROOT = Path(__file__).resolve().parents[3]
LOG = ROOT / "logs" / "wassf_orders.jsonl"
PROT_SERIES = ("KXNFLRECPROT", "KXNFLRECYDSPROT", "KXNFLRSHYDSPROT", "KXNFLPASSYDSPROT", "KXNFLPASSTDSPROT",
               "KXNFLTDPROT")
MAX_ORDER_USD = Decimal("1.00")
MAX_COUNT = 10
MAX_TOTAL_BUYS_USD = Decimal("5.00")   # raised from 3.50 by the user at 01:23Z (worst case still under the $5 account cap)
MIN_EV = Decimal("0.01")
SCAN_SERIES = ["KXNFLGAME", "KXNFLSPREAD", "KXNFLTIE", "KXNFLWINMARGIN"] + [s for p in PROT_SERIES for s in (p[:-4], p)]


def log(event, **kw):
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **kw}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str))


def D(x):
    return Decimal(str(x)) if x not in (None, "") else None


def fee(p):
    return Decimal("0.07") * p * (1 - p)


def quote(m):
    b, a = D(m.get("yes_bid_dollars")), D(m.get("yes_ask_dollars"))
    return b, a, (b + a) / 2 if b is not None and a is not None else None


EXIT_BUFFER = Decimal("0.005")   # half a cent, for noise in the fair estimate


def exit_ok(sign, b, a, f):
    """Selling must beat holding after the sell fee: YES (sign 1) bid - fee >= fair + buffer;
    NO (sign -1) (1 - ask) - fee >= (1 - fair) + buffer."""
    if sign > 0:
        return b is not None and b - fee(b) >= f + EXIT_BUFFER
    return a is not None and a + fee(a) <= f - EXIT_BUFFER


_PUB = None


def book_market(ticker):
    """The live top of the order book as a market-like dict. The /markets and /events quotes can lag the book by
    seconds (seen 00:46Z), so buy() and sell() price everything from this."""
    global _PUB
    if _PUB is None:
        from kpm.clients.kalshi import KalshiClient
        _PUB = KalshiClient()
    book = _PUB.http.get(f"/markets/{ticker}/orderbook", use_cache=False).get("orderbook_fp") or {}
    best = lambda lv: max(lv, key=lambda x: Decimal(str(x[0]))) if lv else None
    by, bn = best(book.get("yes_dollars") or []), best(book.get("no_dollars") or [])
    return {"yes_bid_dollars": by[0] if by else None, "yes_bid_size_fp": by[1] if by else 0,
            "yes_ask_dollars": str(1 - Decimal(str(bn[0]))) if bn else None, "yes_ask_size_fp": bn[1] if bn else 0}


def series_of(ticker):
    return ticker.split("-")[0]


def tradable(ticker):
    return f"-{E}-" in ticker and (series_of(ticker) == "KXNFLWINMARGIN" or series_of(ticker) in PROT_SERIES)


def fair_yes(ticker, get):
    """Fair YES value from the deep markets. get(ticker) -> market dict. None if it can't be trusted."""
    s = series_of(ticker)
    if s in PROT_SERIES:
        b, a, mid = quote(get(ticker.replace(s, s[:-4], 1)))
        return mid if mid is not None and a - b <= Decimal("0.03") else None
    if s == "KXNFLWINMARGIN":
        def mid_or_floor(t):
            # a long shot often has no YES bids at all (e.g. WAS by 15+ at 0/1c); then use half its ask if <= 3c
            b, a, mid = quote(get(t))
            return mid if mid is not None else (a / 2 if a is not None and a <= Decimal("0.03") else None)
        m = {k: mid_or_floor(t) for k, t in {
            "SF": f"KXNFLGAME-{E}-SF", "WAS": f"KXNFLGAME-{E}-WAS", "TIE": f"KXNFLTIE-{E}-Y",
            "SF7": f"KXNFLSPREAD-{E}-SF7", "SF15": f"KXNFLSPREAD-{E}-SF15",
            "WAS7": f"KXNFLSPREAD-{E}-WAS7", "WAS15": f"KXNFLSPREAD-{E}-WAS15"}.items()}
        if any(v is None for v in m.values()):
            return None
        win = {"SF": m["SF"] - m["TIE"] / 2, "WAS": m["WAS"] - m["TIE"] / 2}
        f = {"SF1TO6": win["SF"] - m["SF7"], "SF7TO14": m["SF7"] - m["SF15"], "SF15PLUS": m["SF15"],
             "WAS1TO6": win["WAS"] - m["WAS7"], "WAS7TO14": m["WAS7"] - m["WAS15"], "WAS15PLUS": m["WAS15"],
             "TIE": m["TIE"]}.get(ticker.split("-")[-1])
        return None if f is None else min(max(f, Decimal(0)), Decimal(1))
    return None


def total_buys_usd():
    tot = Decimal(0)
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("event") == "trade_buy_result" and r.get("ok"):
                d = r.get("result") or {}
                n, p, f = D(d.get("fill_count")) or 0, D(d.get("average_fill_price")) or 0, D(d.get("average_fee_paid")) or 0
                # average_fill_price is the YES price; a NO buy costs 1 - that
                tot += n * ((p if r.get("outcome", "yes") == "yes" else 1 - p) + f)
    return tot


def held_tickers():
    """Net contracts per ticker bought/sold through this script, from the order log (public scan uses this)."""
    net = {}
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("event") in ("trade_buy_result", "trade_sell_result") and r.get("ok"):
                d, t = r.get("result") or {}, r.get("ticker")
                n = D(d.get("fill_count")) or 0
                net[t] = net.get(t, 0) + (n if r["event"] == "trade_buy_result" else -n) * (1 if r.get("outcome", "yes") == "yes" else -1)
    return {t: n for t, n in net.items() if n != 0}


def candidates(get, tickers):
    out = []
    for t in tickers:
        if not tradable(t):
            continue
        f = fair_yes(t, get)
        if f is None:
            continue
        b, a, _ = quote(get(t))
        if a is not None and a < 1:
            out.append(("yes", t, a, f - a - fee(a)))
        if series_of(t) == "KXNFLWINMARGIN" and b is not None and b > 0:
            out.append(("no", t, 1 - b, b - f - fee(1 - b)))
    return out


def exec_clients():
    from kpm.execution.client import ExecClient
    from kpm.execution.env import load_env
    env = load_env(require_keys=True, expected="prod")
    return ExecClient(env, live=False), (lambda: ExecClient(env, live=True))


def position(ro, ticker):
    return sum(D(p.get("position_fp")) or 0 for p in ro.positions(ticker=ticker) if p.get("ticker") == ticker)


def send_order(live_factory, body, send, tag, ticker, outcome):
    from kpm.execution.client import validate_order_body
    body = validate_order_body(body)
    log(f"trade_{tag}_body", ticker=ticker, outcome=outcome, body=body, send=send)
    if send:
        res = live_factory().create_order(body)
        log(f"trade_{tag}_result", ticker=ticker, outcome=outcome, ok=res.ok, order_id=res.order_id,
            result=res.data if res.data is not None else repr(res))


def buy(ticker, outcome, send):
    if not tradable(ticker) or outcome not in ("yes", "no"):
        return log("abort", ticker=ticker, reason="not an allowed ticker/side")
    if outcome == "no" and series_of(ticker) in PROT_SERIES:
        return log("abort", ticker=ticker, reason="NO on a protected prop is not allowed (fair is only a lower bound)")
    ro, live = exec_clients()
    m = ro.market(ticker)
    if m.get("status") != "active":
        return log("abort", ticker=ticker, reason=f"status {m.get('status')}")
    bk = book_market(ticker)
    f = fair_yes(ticker, book_market)
    b, a, _ = quote(bk)
    if f is None:
        return log("abort", ticker=ticker, reason="no trustworthy fair value")
    if outcome == "yes":
        if a is None:
            return log("abort", ticker=ticker, reason="no ask in the book")
        price, size, ev = a, D(bk.get("yes_ask_size_fp")) or Decimal(0), f - a - fee(a)
        side, yes_px = "bid", a
    else:
        if b is None:
            return log("abort", ticker=ticker, reason="no bid in the book")
        price, size, ev = 1 - b, D(bk.get("yes_bid_size_fp")) or Decimal(0), b - f - fee(1 - b)
        side, yes_px = "ask", b
    held = position(ro, ticker)
    spent = total_buys_usd()
    count = min(Decimal(MAX_COUNT), (MAX_ORDER_USD / price).to_integral_value(ROUND_FLOOR), size.to_integral_value(ROUND_FLOOR))
    cost = count * (price + fee(price))
    log("trade_buy_preflight", ticker=ticker, outcome=outcome, price=price, fair_yes=f, ev_per_contract=ev, count=count,
        cost=cost, held=held, spent_tonight=spent)
    if ev < MIN_EV:
        return log("hold", ticker=ticker, reason=f"EV {ev:.4f} below {MIN_EV}")
    if held != 0:
        return log("abort", ticker=ticker, reason="already holding this ticker; no adding")
    if count < 1:
        return log("abort", ticker=ticker, reason="size or price too small for one contract under $1")
    if spent + cost > MAX_TOTAL_BUYS_USD:
        return log("abort", ticker=ticker, reason=f"tonight's buy cap: {spent} spent + {cost} > {MAX_TOTAL_BUYS_USD}")
    send_order(live, {"ticker": ticker, "client_order_id": str(uuid.uuid4()), "side": side, "count": f"{count:.2f}",
                      "price": f"{yes_px:.4f}", "time_in_force": "immediate_or_cancel",
                      "self_trade_prevention_type": "taker_at_cross", "exchange_index": m.get("exchange_index")},
               send, "buy", ticker, outcome)


def sell(ticker, send):
    if not tradable(ticker):
        return log("abort", ticker=ticker, reason="not an allowed ticker")
    ro, live = exec_clients()
    m = ro.market(ticker)
    bk = book_market(ticker)
    f = fair_yes(ticker, book_market)
    b, a, _ = quote(bk)
    held = position(ro, ticker)
    log("trade_sell_preflight", ticker=ticker, bid=b, ask=a, fair_yes=f, held=held)
    if held == 0 or m.get("status") != "active" or f is None:
        return log("abort", ticker=ticker, reason="nothing held, market not active, or no fair value")
    if held > 0:      # YES held: sell only if the bid, after the sell fee, beats fair by the buffer
        if b is None or b <= 0 or not exit_ok(1, b, a, f):
            return log("hold", ticker=ticker, reason=f"bid {b} minus fee not above fair {f} + {EXIT_BUFFER}")
        side, px, n, outcome = "ask", b, min(held, D(bk.get("yes_bid_size_fp")) or Decimal(0)), "yes"
    else:             # NO held: sell NO (buy YES at the ask) only if NO's bid (1 - ask) after fee beats NO's fair
        if a is None or a >= 1 or not exit_ok(-1, b, a, f):
            return log("hold", ticker=ticker, reason=f"ask {a} plus fee not below fair {f} - {EXIT_BUFFER}")
        side, px, n, outcome = "bid", a, min(-held, D(bk.get("yes_ask_size_fp")) or Decimal(0)), "no"
    n = n.to_integral_value(ROUND_FLOOR)
    if n < 1:
        return log("abort", ticker=ticker, reason="no size to sell into")
    send_order(live, {"ticker": ticker, "client_order_id": str(uuid.uuid4()), "side": side, "count": f"{n:.2f}",
                      "price": f"{px:.4f}", "time_in_force": "immediate_or_cancel", "reduce_only": True,
                      "self_trade_prevention_type": "taker_at_cross", "exchange_index": m.get("exchange_index")},
               send, "sell", ticker, outcome)


def scan(auto=False):
    """Public data only. Prints BUY lines (EV >= 1.5c, size >= 3) and EXIT lines for tickers held via this script.
    auto=True (only from wassf_auto.py, which the user approved with its own permission rule): act on each line at
    once through buy()/sell() with send=True. Those re-price from the live order book and enforce every hard limit."""
    from kpm.clients.kalshi import KalshiClient
    c = KalshiClient()
    cache = {}
    for s in SCAN_SERIES:
        try:
            ev = c.http.get(f"/events/{s}-{E}", {"with_nested_markets": "true"}, use_cache=False)
        except Exception:
            continue
        for mk in ev.get("markets") or ev.get("event", {}).get("markets") or []:
            cache[mk["ticker"]] = mk

    def get(t):
        if t not in cache:
            cache[t] = c.http.get(f"/markets/{t}", use_cache=False)["market"]
        return cache[t]
    now = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    held = held_tickers()      # buy() refuses to add to these, so don't flag them
    for outcome, t, price, ev in sorted(candidates(get, [t for t, mk in cache.items()
                                                         if mk.get("status") == "active" and t not in held]),
                                        key=lambda x: -x[3]):
        m = cache[t]
        size = D(m.get("yes_ask_size_fp") if outcome == "yes" else m.get("yes_bid_size_fp")) or 0
        if ev >= Decimal("0.015") and size >= 3:
            print(f"{now} BUY {outcome.upper()} {t} at {price:.2f} EV +{100 * ev:.1f}c size {size:.0f}", flush=True)
            with (ROOT / "logs" / "wassf_signals.csv").open("a", encoding="utf-8") as f:   # paper record of every signal
                f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')},BUY,{outcome},{t},{price},{ev:.4f},{size}\n")
            if auto:
                buy(t, outcome, send=True)
    for t, n in held_tickers().items():
        f = fair_yes(t, get)
        b, a, _ = quote(get(t))
        if f is None:
            continue
        m = get(t)       # only flag exits there is at least 1 contract of size to sell into
        room = D(m.get("yes_bid_size_fp") if n > 0 else m.get("yes_ask_size_fp")) or 0
        if room >= 1 and ((n > 0 and exit_ok(1, b, a, f)) or (n < 0 and exit_ok(-1, b, a, f))):
            print(f"{now} EXIT {t} held {n} bid {b} ask {a} fair {f:.4f}", flush=True)
            if auto:
                sell(t, send=True)


if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    send = "--send" in sys.argv
    if args[:1] == ["buy"] and len(args) == 3:
        buy(args[1], args[2].lower(), send)
    elif args[:1] == ["sell"] and len(args) == 2:
        sell(args[1], send)
    elif args[:1] == ["scan"]:
        scan()
    else:
        print(__doc__)
