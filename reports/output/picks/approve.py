"""Claude recommends, the user approves: place ONE live IOC buy from a ticket file.

  KALSHI_ENV=prod .venv/Scripts/python.exe reports/output/picks/approve.py <ticket_id> [--send]

Without --send it is a dry run: read-only preflight, prints a summary, and never constructs a live ExecClient.
Every step is appended to logs/picks_orders.jsonl. No key material is printed or logged.
"""
import json
import math
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TICKETS = Path(__file__).resolve().parent / "tickets"
LOG = ROOT / "logs" / "picks_orders.jsonl"

# HARD LIMITS (not read from the ticket)
ALLOWED_SERIES = {"KXNBAGAME", "KXNBASPREAD", "KXNFLGAME", "KXNFLSPREAD"}
MAX_COUNT = 10
MAX_TICKET_COST_USD = Decimal("3.00")   # incl. fee
MAX_TOTAL_USD = Decimal("3.00")         # the user set a $3 max loss on 2026-10-20; change only when the user says
TICKET_TTL = timedelta(minutes=30)


def D(x):
    return Decimal(str(x)) if x not in (None, "") else None


def fee(count, p):
    """Taker fee: ceil(0.07*count*P*(1-P)/0.0001)*0.0001 at P = price paid for the side bought."""
    raw = Decimal("0.07") * Decimal(count) * D(p) * (1 - D(p)) / Decimal("0.0001")
    return Decimal(math.ceil(raw)) * Decimal("0.0001")


def max_cost(count, p):
    return Decimal(count) * D(p) + fee(count, p)


def read_ledger(path=LOG):
    if not Path(path).exists():
        return []
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def ledger_spent(records):
    """Filled cost (price*filled + fees) of all previous order_result records. If a result lacks a logged cost,
    count its full max cost (conservative)."""
    tot = Decimal(0)
    for r in records:
        if r.get("event") == "order_result" and r.get("ok"):
            c = r.get("cost_usd")
            tot += D(c) if c is not None else (D(r.get("max_cost_usd")) or Decimal(0))
    return tot


def ticket_used(records, ticket_id):
    return any(r.get("ticket") == ticket_id and r.get("event") in ("order_result", "send_attempt") for r in records)


def check_ticket(t, ledger, now):
    """Pure limit checks. Returns a list of problems (empty = OK)."""
    bad = []
    try:
        series = str(t["ticker"]).split("-")[0]
        if series not in ALLOWED_SERIES:
            bad.append(f"series {series} not allowed")
        if t.get("side") not in ("yes", "no"):
            bad.append(f"side {t.get('side')!r} must be yes or no (buys only)")
        n = t["count"]
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_COUNT:
            bad.append(f"count {n!r} must be an integer 1..{MAX_COUNT}")
        p = D(t["max_price"])
        if not (0 < p < 1):
            bad.append("max_price must be between 0 and 1")
        if not bad:
            c = max_cost(n, p)
            if c > MAX_TICKET_COST_USD:
                bad.append(f"max cost {c:.4f} > per-ticket cap {MAX_TICKET_COST_USD}")
            spent = ledger_spent(ledger)
            if spent + c > MAX_TOTAL_USD:
                bad.append(f"total cap: {spent:.4f} already spent + {c:.4f} > {MAX_TOTAL_USD}")
        created = datetime.fromisoformat(str(t["created_utc"]).replace("Z", "+00:00"))
        if now - created > TICKET_TTL:
            bad.append("ticket expired (older than 30 minutes)")
        if ticket_used(ledger, t["id"]):
            bad.append("ticket id already used (one shot)")
    except (KeyError, TypeError, ValueError, ArithmeticError) as e:
        bad.append(f"malformed ticket: {e!r}")
    return bad


def log(event, ticket, **kw):
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, "ticket": ticket, **kw}
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str))


def book_ask(ticker, side):
    """Best ask for the side being bought, from the live order book (not the lagging /markets quote)."""
    from kpm.clients.kalshi import KalshiClient
    book = KalshiClient().http.get(f"/markets/{ticker}/orderbook", use_cache=False).get("orderbook_fp") or {}
    best = lambda lv: max(lv, key=lambda x: Decimal(str(x[0]))) if lv else None
    # YES ask = 1 - best NO bid; NO ask = 1 - best YES bid
    other = best(book.get("no_dollars" if side == "yes" else "yes_dollars") or [])
    if not other:
        return None, Decimal(0)
    return 1 - D(other[0]), D(other[1]) or Decimal(0)


def main(ticket_id, send):
    path = TICKETS / f"{ticket_id}.json"
    if "/" in ticket_id or "\\" in ticket_id or not path.exists():
        return log("abort", ticket_id, reason="ticket file not found")
    t = json.loads(path.read_text(encoding="utf-8"))
    if t.get("id") != ticket_id:
        return log("abort", ticket_id, reason="ticket id does not match file name")
    bad = check_ticket(t, read_ledger(), datetime.now(timezone.utc))
    if bad:
        return log("abort", ticket_id, reason="; ".join(bad))
    ticker, side, n, p = t["ticker"], t["side"], t["count"], D(t["max_price"])
    cost = max_cost(n, p)

    from kpm.execution.client import ExecClient, validate_order_body
    from kpm.execution.env import load_env
    env = load_env(require_keys=True, expected="prod")
    ro = ExecClient(env, live=False)          # read-only
    m = ro.market(ticker)
    if m.get("status") != "active":
        return log("abort", ticket_id, reason=f"market status {m.get('status')}")
    ask, ask_sz = book_ask(ticker, side)
    pos = [x for x in ro.positions(ticker=ticker) if x.get("ticker") == ticker]
    rest = list(ro.orders(ticker=ticker, status="resting"))
    ei = m.get("exchange_index")
    cash = ro.shard_cash(ei)
    log("preflight", ticket_id, ticker=ticker, side=side, ask=ask, ask_size=ask_sz, positions=pos,
        resting=len(rest), cash=cash, exchange_index=ei, max_cost_usd=cost)
    held = sum(abs(D(x.get("position_fp") or x.get("position") or 0)) for x in pos)
    if held > 0 or rest:
        return log("abort", ticket_id, reason="already holding or resting on this ticker")
    if ask is None or ask > p or ask_sz < n:
        return log("abort", ticket_id, reason=f"ask {ask} x {ask_sz} does not meet <= {p} x {n}")
    if cash is None or cash < cost + Decimal("0.10"):
        return log("abort", ticket_id, reason=f"cash {cash} too low")

    # Order price is always the YES price: YES buy = bid at p; NO buy = ask at 1 - p (same as wassf_trade.py).
    body = validate_order_body({
        "ticker": ticker, "client_order_id": str(uuid.uuid4()), "side": "bid" if side == "yes" else "ask",
        "count": f"{Decimal(n):.2f}", "price": f"{(p if side == 'yes' else 1 - p):.4f}",
        "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross", "exchange_index": ei})
    log("order_body", ticket_id, body=body, send=send)
    print(f"\n{t.get('reason', '')}\nMarket: {m.get('title') or ticker} [{ticker}]\n"
          f"BUY {side.upper()} x{n}, max price {p:.2f}  (IOC: fills now or not at all)\n"
          f"Max cost incl. fee (= max loss): ${cost:.4f}  (fee ${fee(n, p):.4f})\n"
          f"If it wins: payout ${Decimal(n):.2f}, profit ${Decimal(n) - cost:.4f}\n"
          f"Current best {side.upper()} ask: {ask:.2f} x {ask_sz:.0f}")
    if not send:
        print(f"\nDRY RUN - nothing sent. To place it:\n"
              f"KALSHI_ENV=prod .venv/Scripts/python.exe reports/output/picks/approve.py {ticket_id} --send")
        return
    log("send_attempt", ticket_id, ticker=ticker)
    res = ExecClient(env, live=True).create_order(body)
    d = res.data if isinstance(getattr(res, "data", None), dict) else {}
    filled = D(d.get("fill_count")) or Decimal(0)
    yp = D(d.get("average_fill_price"))
    sp = None if yp is None else (yp if side == "yes" else 1 - yp)    # average_fill_price is the YES price
    fee_each = D(d.get("average_fee_paid")) or Decimal(0)
    spent = filled * ((sp or Decimal(0)) + fee_each)
    log("order_result", ticket_id, ok=res.ok, order_id=res.order_id, filled=filled, avg_price=sp,
        cost_usd=spent if res.ok and (filled == 0 or sp is not None) else None, max_cost_usd=cost,
        result=res.data if res.data is not None else repr(res))
    pos = [x for x in ro.positions(ticker=ticker) if x.get("ticker") == ticker]
    log("position_after", ticket_id, positions=pos)
    print(f"\nRESULT: ok={res.ok} filled={filled} avg_price={sp}")


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    if len(a) != 1:
        print(__doc__)
    else:
        main(a[0], "--send" in sys.argv)
