"""ONE live order the user explicitly approved in chat on 2026-10-19 (log #20): buy 10 YES of
KXNFLWINMARGIN-26OCT19WASSF-WAS1TO6 at most 0.11, immediate-or-cancel (fills now or not at all; nothing left resting).

Pre-flight (all read-only): market active, the ask is still <= 0.11 with >= 10 offered, no existing position or resting
order on this ticker (the user may have bought by hand), enough cash. Without --send it only prints the order body.
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

TICKER = "KXNFLWINMARGIN-26OCT19WASSF-WAS1TO6"
MAX_PRICE = Decimal("0.1100")
COUNT = Decimal("10")
LOG = Path(__file__).resolve().parents[3] / "logs" / "wassf_orders.jsonl"


def log(event, **kw):
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **kw}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str))


def main(send: bool):
    env = load_env(require_keys=True, expected="prod")
    ro = ExecClient(env, live=False)
    m = ro.market(TICKER)
    if m.get("status") != "active":
        return log("abort", reason=f"market status {m.get('status')}")
    ask = Decimal(str(m.get("yes_ask_dollars")))
    ask_sz = Decimal(str(m.get("yes_ask_size_fp") or 0))
    pos = [p for p in ro.positions(ticker=TICKER) if p.get("ticker") == TICKER]
    rest = list(ro.orders(ticker=TICKER, status="resting"))
    ei = m.get("exchange_index")
    cash = ro.shard_cash(ei)
    log("preflight", ticker=TICKER, ask=ask, ask_size=ask_sz, positions=pos, resting=len(rest), cash=cash, exchange_index=ei)
    held = sum(abs(Decimal(str(p.get("position_fp") or p.get("position") or 0))) for p in pos)
    if held > 0 or rest:
        return log("abort", reason="already holding or resting on this ticker (bought by hand?)")
    if ask > MAX_PRICE or ask_sz < COUNT:
        return log("abort", reason=f"ask {ask} x {ask_sz} no longer meets <= {MAX_PRICE} x {COUNT}")
    if cash is None or cash < MAX_PRICE * COUNT + Decimal("0.10"):
        return log("abort", reason=f"cash {cash} too low")
    body = validate_order_body({
        "ticker": TICKER, "client_order_id": str(uuid.uuid4()), "side": "bid", "count": f"{COUNT:.2f}",
        "price": f"{MAX_PRICE:.4f}", "time_in_force": "immediate_or_cancel",
        "self_trade_prevention_type": "taker_at_cross", "exchange_index": ei})
    log("order_body", body=body, send=send)
    if not send:
        return
    live = ExecClient(env, live=True)
    res = live.create_order(body)
    log("order_result", ok=res.ok, order_id=res.order_id, result=getattr(res, "data", None) or repr(res))
    pos = [p for p in ro.positions(ticker=TICKER) if p.get("ticker") == TICKER]
    log("position_after", positions=pos)


if __name__ == "__main__":
    main("--send" in sys.argv)
