"""Cash out the WAS 1-6 position (log #21), run by the user with `!`. Dry run unless --send.

Sells the WHOLE position at the current YES bid, immediate-or-cancel and reduce_only (it can only shrink the position,
never open a new one; anything not filled at once is cancelled). By default it refuses unless the bid is at or above
the fair value from the deep markets: (WAS mid - tie mid / 2) - WAS-by-7+ mid. --force sells anyway (the user's call).
Every step is appended to logs/wassf_orders.jsonl. No key material is printed or logged.
"""
import json
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from kpm.execution.client import ExecClient, validate_order_body
from kpm.execution.env import load_env

E = "26OCT19WASSF"
TICKER = f"KXNFLWINMARGIN-{E}-WAS1TO6"
LOG = Path(__file__).resolve().parents[3] / "logs" / "wassf_orders.jsonl"


def log(event, **kw):
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **kw}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str))


def mid(m):
    b, a = m.get("yes_bid_dollars"), m.get("yes_ask_dollars")
    if b in (None, "") or a in (None, ""):
        return None
    return (Decimal(str(b)) + Decimal(str(a))) / 2


def main(send: bool, force: bool):
    env = load_env(require_keys=True, expected="prod")
    ro = ExecClient(env, live=False)
    m = ro.market(TICKER)
    bid = Decimal(str(m.get("yes_bid_dollars") or 0))
    bid_sz = Decimal(str(m.get("yes_bid_size_fp") or 0))
    was, tie, was7 = (mid(ro.market(t)) for t in (f"KXNFLGAME-{E}-WAS", f"KXNFLTIE-{E}-Y", f"KXNFLSPREAD-{E}-WAS7"))
    fair = None if None in (was, was7) else was - (tie or Decimal(0)) / 2 - was7
    pos = [p for p in ro.positions(ticker=TICKER) if p.get("ticker") == TICKER]
    held = sum(Decimal(str(p.get("position_fp") or 0)) for p in pos)
    log("sell_preflight", ticker=TICKER, status=m.get("status"), bid=bid, bid_size=bid_sz, fair=fair, held=held,
        force=force)
    if m.get("status") != "active":
        return log("abort", reason=f"market status {m.get('status')}")
    if held <= 0:
        return log("abort", reason="no YES position to sell")
    if bid <= 0:
        return log("abort", reason="no bid to sell into")
    if not force and (fair is None or bid < fair):
        return log("hold", reason=f"bid {bid} is below fair {fair}: holding is worth more (use --force to sell anyway)")
    count = min(held, bid_sz)
    body = validate_order_body({
        "ticker": TICKER, "client_order_id": str(uuid.uuid4()), "side": "ask", "count": f"{count:.2f}",
        "price": f"{bid:.4f}", "time_in_force": "immediate_or_cancel", "reduce_only": True,
        "self_trade_prevention_type": "taker_at_cross", "exchange_index": m.get("exchange_index")})
    log("sell_body", body=body, proceeds_before_fee=count * bid, send=send)
    if not send:
        return
    res = ExecClient(env, live=True).create_order(body)
    log("sell_result", ok=res.ok, order_id=res.order_id, result=getattr(res, "data", None) or repr(res))


if __name__ == "__main__":
    main("--send" in sys.argv, "--force" in sys.argv)
