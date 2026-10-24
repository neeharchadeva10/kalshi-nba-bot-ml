"""Read-only local dashboard for tonight's WAS @ SF trades: http://127.0.0.1:8766

Reads logs/wassf_orders.jsonl (written by wassf_order.py, wassf_sell.py and wassf_trade.py) and shows every fill with
its price, fee and realized gain/loss (average-cost basis), the open positions marked at the live bid and fair value
(public order books only), and the totals. It sends no orders and needs no keys. The page refreshes every 10 s.
"""
import html
import json
import time
from datetime import datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import wassf_trade as w
from kpm.core.timeutil import ET

PORT = 8766
TITLES = {}
_LIVE = {"ts": 0.0, "data": {}}


def D(x):
    return Decimal(str(x)) if x not in (None, "") else Decimal(0)


def title(t):
    if t not in TITLES:
        try:
            from kpm.clients.kalshi import KalshiClient
            m = KalshiClient().http.get(f"/markets/{t}", use_cache=False)["market"]
            kind = {"KXNFLWINMARGIN": "Margin", "KXNFLRSHYDSPROT": "Rush yds (prot)", "KXNFLRECYDSPROT": "Rec yds (prot)",
                    "KXNFLRECPROT": "Receptions (prot)", "KXNFLPASSYDSPROT": "Pass yds (prot)",
                    "KXNFLPASSTDSPROT": "Pass TDs (prot)", "KXNFLTDPROT": "TDs (prot)"}.get(w.series_of(t), w.series_of(t))
            TITLES[t] = f"{kind}: {m.get('yes_sub_title') or t}"
        except Exception:
            TITLES[t] = t
    return TITLES[t]


def fills():
    """Every fill tonight as (ts, ticker, outcome, action, count, outcome_price, fee_total, order_id)."""
    out = []
    manual = "KXNFLWINMARGIN-26OCT19WASSF-WAS1TO6"
    for line in w.LOG.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        ev = r.get("event")
        if ev not in ("order_result", "sell_result", "trade_buy_result", "trade_sell_result") or not r.get("ok"):
            continue
        d = r.get("result") or {}
        n = D(d.get("fill_count"))
        if n <= 0:
            continue
        t = r.get("ticker", manual)
        o = r.get("outcome", "yes")
        action = "buy" if ev in ("order_result", "trade_buy_result") else "sell"
        yes_px = D(d.get("average_fill_price"))
        px = yes_px if o == "yes" else 1 - yes_px
        out.append((r["ts"], t, o, action, n, px, n * D(d.get("average_fee_paid")), r.get("order_id")))
    return out


def ledger():
    rows, pos, realized = [], {}, Decimal(0)
    for ts, t, o, action, n, px, fee, oid in fills():
        p = pos.setdefault((t, o), {"n": Decimal(0), "cost": Decimal(0)})
        pnl = None
        if action == "buy":
            p["n"] += n
            p["cost"] += n * px + fee
        else:
            avg = p["cost"] / p["n"] if p["n"] else Decimal(0)
            pnl = n * px - fee - avg * n
            realized += pnl
            p["n"] -= n
            p["cost"] -= avg * n
        rows.append(dict(ts=ts, ticker=t, outcome=o, action=action, n=n, px=px, fee=fee, pnl=pnl, oid=oid))
    return rows, pos, realized


def live(tickers):
    if time.time() - _LIVE["ts"] > 8:
        data = {}
        for t in tickers:
            try:
                b, a, _ = w.quote(w.book_market(t))
                data[t] = (b, a, w.fair_yes(t, w.book_market))
            except Exception:
                data[t] = (None, None, None)
        _LIVE.update(ts=time.time(), data=data)
    return _LIVE["data"]


def money(x, sign=False):
    if x is None:
        return ""
    s = f"{'+' if sign and x > 0 else ''}{'-' if x < 0 else ''}${abs(x):.2f}"
    cls = "" if not sign else (" class='g'" if x > 0 else (" class='r'" if x < 0 else ""))
    return f"<span{cls}>{s}</span>"


def et(ts):
    return datetime.fromisoformat(ts).astimezone(ET).strftime("%I:%M:%S %p")


def page():
    rows, pos, realized = ledger()
    open_pos = {k: v for k, v in pos.items() if v["n"] > 0}
    lv = live(sorted({t for t, _ in open_pos}))
    lines = w.LOG.read_text(encoding="utf-8").splitlines()
    last = json.loads(lines[-1]) if lines else {}
    started = [json.loads(x) for x in lines if '"auto_started"' in x or '"auto_stopped"' in x]
    status = started[-1]["event"].replace("auto_", "") if started else "unknown"
    age = (datetime.now().astimezone() - datetime.fromisoformat(last["ts"])).total_seconds() if last else None
    spent, cap = w.total_buys_usd(), w.MAX_TOTAL_BUYS_USD

    trs = "".join(
        f"<tr><td>{et(r['ts'])}</td><td>{'BUY' if r['action'] == 'buy' else 'SELL'} {r['outcome'].upper()}</td>"
        f"<td>{html.escape(title(r['ticker']))}</td><td>{r['n']:.0f}</td><td>{r['px'] * 100:.0f}¢</td>"
        f"<td>${r['fee']:.3f}</td><td>{money(r['pnl'], True) if r['pnl'] is not None else ''}</td></tr>"
        for r in reversed(rows))
    open_mark, ops = Decimal(0), ""
    for (t, o), p in open_pos.items():
        b, a, f = lv.get(t, (None, None, None))
        bid = (b if o == "yes" else (1 - a if a is not None else None))
        fair = (f if o == "yes" else (1 - f if f is not None else None))
        mark = p["n"] * (fair if fair is not None else (bid or 0))
        open_mark += mark
        ops += (f"<tr><td>{html.escape(title(t))}</td><td>{o.upper()}</td><td>{p['n']:.0f}</td>"
                f"<td>${p['cost']:.2f}</td><td>{'' if bid is None else f'{bid * 100:.0f}¢'}</td>"
                f"<td>{'' if fair is None else f'{fair * 100:.1f}¢'}</td><td>{money(mark - p['cost'], True)}</td>"
                f"<td>${p['n']:.0f}</td></tr>")
    open_cost = sum((p["cost"] for p in open_pos.values()), Decimal(0))
    total = realized + open_mark - open_cost
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="10">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>WAS @ SF trades</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#e5e5e5;--g:#0a7d32;--r:#c62828;--card:#f6f6f6}}
@media (prefers-color-scheme: dark){{:root{{--bg:#121212;--fg:#eee;--mut:#999;--line:#2a2a2a;--g:#4cc76f;--r:#ff6b6b;--card:#1d1d1d}}}}
body{{background:var(--bg);color:var(--fg);font:14px system-ui,sans-serif;margin:0;padding:16px;max-width:1000px}}
h1{{font-size:20px;margin:0 0 4px}} .mut{{color:var(--mut)}} .g{{color:var(--g)}} .r{{color:var(--r)}}
.cards{{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}} .c{{background:var(--card);padding:10px 14px;border-radius:8px}}
.c b{{display:block;font-size:20px}} table{{border-collapse:collapse;width:100%;margin:8px 0 20px}}
td,th{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}}
.wrap{{overflow-x:auto}} h2{{font-size:16px;margin:18px 0 4px}}
</style></head><body>
<h1>WAS @ SF — tonight's trades</h1>
<div class="mut">Trader: <b>{status}</b> · last log line {'' if age is None else f'{age:.0f}s ago'} · refreshes every 10 s · read-only</div>
<div class="cards">
<div class="c">Realized<b>{money(realized, True)}</b></div>
<div class="c">Open (marked at fair)<b>{money(open_mark - open_cost, True)}</b></div>
<div class="c">Tonight total<b>{money(total, True)}</b></div>
<div class="c">Buy budget used<b>${spent:.2f} / ${cap:.2f}</b></div>
</div>
<h2>Open positions</h2><div class="wrap"><table><tr><th>Bet</th><th>Side</th><th>Contracts</th><th>Cost</th><th>Bid now</th><th>Fair now</th><th>Gain/loss at fair</th><th>Pays if wins</th></tr>
{ops or '<tr><td colspan=8 class=mut>none</td></tr>'}</table></div>
<h2>Every fill (newest first)</h2><div class="wrap"><table><tr><th>Time (ET)</th><th>Action</th><th>Bet</th><th>Contracts</th><th>Price</th><th>Fee</th><th>Gain/loss on sale</th></tr>
{trs}</table></div>
<div class="mut">Gain/loss on a sale = sale proceeds − sale fee − average cost of those contracts (buy price + buy fee).</div>
</body></html>"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            body, code = page().encode("utf-8"), 200
        except Exception as e:
            body, code = f"dashboard error: {html.escape(repr(e))}".encode("utf-8"), 500
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
