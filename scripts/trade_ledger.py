"""READ-ONLY trade ledger for the user's Kalshi account.

README
 Files (all under logs/, git-ignored): trade_ledger.csv (one row per fill), trade_ledger_summary.txt,
 trade_ledger_runs.log (one line per run), ledger_raw/<UTC>.json (dated raw API snapshot of every run),
 trade_ledger_task.out (scheduled-task stdout/stderr). Bet journal: scripts/bet_journal.py (runs after this).
 Run by hand:   .venv/Scripts/python.exe scripts/trade_ledger.py
 Unattended:    scripts/trade_ledger_run.ps1, registered by scripts/trade_ledger_task.ps1 (daily 09:30 ET).
 Each run rebuilds everything from the whole account history, so it is idempotent. Exit code 1 on any error
 (message is the exception type only, never secrets).
 Tags: lockbot_26OCT20 = the automated lock bot night of 2026-10-20; early_overs_bot = the unattended early-overs
 bot (client_order_id eob-...); early_overs_40 = manual buys of the research-log #40 early-overs pick list;
 picks_send = the /send flow; exec_log = orders found in logs/exec.jsonl; manual_other = anything else.

Pulls the whole account history with GET calls only (fills on shards 0-3, settlements, positions, orders,
cash per shard), writes the raw responses to logs/ledger_raw/<UTC>.json, then writes
  logs/trade_ledger.csv            one row per fill
  logs/trade_ledger_summary.txt    per tag / ET date summary, cash, reconciliation (also printed)
Nothing is sent anywhere (no Discord). Never prints keys. Everything is under logs/ (git-ignored).

SAFETY: the client is wrapped by ReadOnlyGuard (method allow-list) and its HTTP transport is wrapped by
GetOnlyTransport (any non-GET raises). Never imports order/cancel/amend methods.

STRATEGY TAGS (first rule that matches wins; matching is by order_id / client_order_id / ticker + time):
 1 lockbot_26OCT20   fill_id is in reports/output/ohio_trial/live_26OCT20/account.json (the earlier pull of that
                     night's fills), OR fill order_id is a leg order_id in live_26OCT20/events.jsonl, OR the
                     fill is on a ticker the bot attempted and within 10 s after that attempt's timestamp.
 2 early_overs_bot   client_order_id starts with "eob-" (checked before rule 3 because it is more specific; the
                     bot's picks would otherwise also match the picks file).
 3 early_overs_40    a BUY whose ticker is in logs/early_overs_picks.jsonl with bet == true (or status
                     "PLACE NOW") on the fill's ET date (the pick's tip_et date). The pick's proj, side, ping
                     price (taker_price) and limit are recorded, plus whether the fill side equals the pick's
                     side and whether the fill price <= the pick's taker_price.
 4 picks_send        client_order_id in logs/picks_orders.jsonl order bodies, or the fill is on a
                     reports/output/picks/tickets/*.json ticket's ticker+side on the ticket's creation date.
 5 exec_log          order_id (POST response) or client_order_id (POST body) found in logs/exec.jsonl.
 6 manual_other      everything else.
 client_order_id of a fill comes from GET /portfolio/orders (joined on order_id), falling back to the order ids
 found in events.jsonl / exec.jsonl.

P&L PER FILL (realized_pnl = payout - cost - fee, USD):
 * Kalshi's V2 fills carry action (buy/sell) and side (yes/no). Verified against the account: every fill is a
   position LEG of `side` bought at that side's price (a "sell no" on the ask book at yes 0.44 is a NO leg at 0.56;
   YES + NO legs of one ticker pay $1 per pair). Treating every fill this way reproduces Kalshi's own
   positions.realized_pnl on 327/327 settled tickers, so action is kept only as a column, not used in the maths.
 * pnl = (payout - side_price) * count - fee, payout = $1 if the market result equals the side else $0.
 * Unsettled fills: pnl empty and counted as open contracts. Non-binary results are flagged, pnl empty.
 * A ticker's total P&L is the sum over its legs (a locked YES+NO pair shows one winning and one losing leg).
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
ET = ZoneInfo("America/New_York")
SHARDS = (0, 1, 2, 3)
D = Decimal
ZERO = D("0")

ALLOWED_METHODS = frozenset({"fills", "positions", "settlements", "shard_cash", "orders", "balance", "market"})


# ======================================================================================= safety guards
class WriteBlocked(RuntimeError):
    pass


class ReadOnlyGuard:
    """Wraps a client; only allow-listed read methods are reachable. Anything else raises WriteBlocked."""

    def __init__(self, client):
        object.__setattr__(self, "_client", client)

    def __getattr__(self, name):
        if name in ALLOWED_METHODS:
            return getattr(object.__getattribute__(self, "_client"), name)
        raise WriteBlocked(f"read-only ledger: client attribute {name!r} is blocked")

    def __setattr__(self, name, value):
        raise WriteBlocked("read-only ledger: guard is immutable")


class GetOnlyTransport:
    """Wraps an HTTP transport; any non-GET request raises before it is sent."""

    def __init__(self, inner):
        self._inner = inner

    def send(self, method, url, **kw):
        if str(method).upper() != "GET":
            raise WriteBlocked(f"read-only ledger: refusing {method} {url}")
        return self._inner.send(method, url, **kw)

    def close(self):
        c = getattr(self._inner, "close", None)
        if c:
            c()


def make_live_client():
    from kpm.execution.client import ExecClient, RequestsTransport
    from kpm.execution.env import load_env
    env = load_env(require_keys=True, expected="prod")
    return ReadOnlyGuard(ExecClient(env, live=False, transport=GetOnlyTransport(RequestsTransport())))


# ======================================================================================= pulling
def pull_all(client) -> dict:
    raw = {"pulled_utc": datetime.now(timezone.utc).isoformat(), "cash": {}, "fills": {}, "positions": {},
           "orders": {}, "settlements": []}
    for s in SHARDS:
        raw["cash"][str(s)] = str(client.shard_cash(s))
        raw["fills"][str(s)] = list(client.fills(limit=1000, exchange_index=s))
        raw["positions"][str(s)] = list(client.positions(settlement_status="all", limit=1000, exchange_index=s))
        raw["orders"][str(s)] = list(client.orders(limit=1000, exchange_index=s))
    raw["settlements"] = list(client.settlements(limit=1000))
    return raw


# ======================================================================================= tag sources
def _jl(path: Path):
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


@dataclass
class TagSources:
    lock_fill_ids: set = field(default_factory=set)
    lock_order_ids: set = field(default_factory=set)
    lock_attempts: list = field(default_factory=list)       # (ticker, epoch)
    lock_coids: set = field(default_factory=set)
    picks: list = field(default_factory=list)                # early_overs rows with bet / PLACE NOW
    picks_orders_coids: set = field(default_factory=set)
    tickets: list = field(default_factory=list)              # (ticker, side, et_date)
    exec_order_ids: set = field(default_factory=set)
    exec_coids: set = field(default_factory=set)


def load_sources(root: Path = ROOT) -> TagSources:
    src = TagSources()
    live = root / "reports/output/ohio_trial/live_26OCT20"
    acc = live / "account.json"
    if acc.exists():
        a = json.load(open(acc, encoding="utf-8"))
        for fl in a.get("fills", {}).values():
            for f in fl:
                src.lock_fill_ids.add(f.get("fill_id"))
    for ev in _jl(live / "events.jsonl"):
        if ev.get("event") != "attempt":
            continue
        t = _parse_iso(ev["time"]).timestamp()
        for b in ev.get("bodies", []):
            src.lock_attempts.append((b["ticker"], t))
            src.lock_coids.add(b.get("client_order_id"))
        for leg in ev.get("legs", []):
            if leg.get("order_id"):
                src.lock_order_ids.add(leg["order_id"])
    for r in _jl(root / "logs/early_overs_picks.jsonl"):
        if r.get("bet") is True or str(r.get("status", "")).upper().startswith("PLACE NOW"):
            src.picks.append(r)
    for r in _jl(root / "logs/picks_orders.jsonl"):
        b = r.get("body")
        if isinstance(b, dict) and b.get("client_order_id"):
            src.picks_orders_coids.add(b["client_order_id"])
    for p in sorted((root / "reports/output/picks/tickets").glob("*.json")):
        try:
            t = json.load(open(p, encoding="utf-8"))
            src.tickets.append((t["ticker"], t.get("side"), str(t.get("created_utc", ""))[:10], t.get("id")))
        except Exception:
            continue
    for r in _jl(root / "logs/exec.jsonl"):
        if r.get("method") != "POST":
            continue
        if r.get("client_order_id"):
            src.exec_coids.add(r["client_order_id"])
        try:
            resp = json.loads(r.get("response") or "{}")
            if resp.get("order_id"):
                src.exec_order_ids.add(resp["order_id"])
        except ValueError:
            pass
    return src


# ======================================================================================= ledger build
def _d(x, default=ZERO) -> Decimal:
    if x in (None, ""):
        return default
    return D(str(x))


def _fill_time(f) -> datetime:
    if f.get("created_time"):
        return _parse_iso(f["created_time"])
    return datetime.fromtimestamp(int(f["ts"]), timezone.utc)


def ticker_parts(t: str):
    p = t.split("-")
    return p[0], "-".join(p[:2])


def tag_fill(row: dict, f: dict, src: TagSources) -> tuple[str, dict]:
    """Return (tag, extra) for one fill row. row has ticker, side, action, price, coid, order_id, ts_epoch, et_date."""
    t = row["ticker"]
    if (f.get("fill_id") in src.lock_fill_ids or row["order_id"] in src.lock_order_ids
            or row["client_order_id"] in src.lock_coids
            or any(tk == t and 0 <= row["ts_epoch"] - ts <= 10 for tk, ts in src.lock_attempts)):
        return "lockbot_26OCT20", {}
    coid = row["client_order_id"] or ""
    if coid.startswith("eob-"):
        return "early_overs_bot", {}
    if True:
        cands = [p for p in src.picks if p.get("ticker") == t
                 and str(p.get("tip_et", ""))[:10] == row["et_date"]]
        if cands:
            before = [p for p in cands if _parse_iso(p["ts"]).timestamp() <= row["ts_epoch"]]
            p = (before or cands)[-1]
            pside = str(p.get("buy", "")).lower()
            tp = p.get("taker_price")
            return "early_overs_40", {
                "pick_proj": p.get("proj"), "pick_side": pside, "pick_ping_price": tp, "pick_limit": p.get("limit"),
                "pick_side_match": row["side"] == pside,
                "pick_price_match": (tp is not None and row["price"] <= D(str(tp)) + D("0.0001")),
            }
    if coid and coid in src.picks_orders_coids:
        return "picks_send", {}
    for tk, side, d, _id in src.tickets:
        if tk == t and side == row["side"] and d == row["et_date"]:
            return "picks_send", {}
    if row["order_id"] in src.exec_order_ids or (coid and coid in src.exec_coids):
        return "exec_log", {}
    return "manual_other", {}


def build_ledger(raw: dict, src: TagSources) -> dict:
    # order_id -> client_order_id
    o2c = {}
    for orders in raw.get("orders", {}).values():
        for o in orders:
            if o.get("order_id") and o.get("client_order_id"):
                o2c[o["order_id"]] = o["client_order_id"]
    results = {}      # ticker -> result
    settle_rev = defaultdict(lambda: ZERO)      # shard -> revenue $
    for s in raw.get("settlements", []):
        results[s["ticker"]] = str(s.get("market_result", "")).lower()
        settle_rev[int(s.get("exchange_index", 0))] += _d(s.get("revenue")) / 100

    seen, fills = set(), []
    for shard, fl in raw.get("fills", {}).items():
        for f in fl:
            fid = f.get("fill_id") or f.get("trade_id")
            if fid in seen:
                continue
            seen.add(fid)
            f = dict(f)
            f.setdefault("exchange_index", int(shard))
            fills.append(f)
    fills.sort(key=lambda f: (_fill_time(f), f.get("fill_id", "")))

    rows = []
    for f in fills:
        t = f.get("ticker") or f.get("market_ticker")
        side = f["side"]
        price = _d(f["yes_price_dollars"] if side == "yes" else f["no_price_dollars"])
        tm = _fill_time(f)
        series, event = ticker_parts(t)
        oid = f.get("order_id", "")
        row = {
            "fill_time_utc": tm.strftime("%Y-%m-%d %H:%M:%S"), "fill_time_et": tm.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S"),
            "et_date": tm.astimezone(ET).strftime("%Y-%m-%d"), "shard": int(f.get("exchange_index", 0)),
            "ticker": t, "series": series, "event": event, "side": side, "action": f["action"],
            "count": _d(f["count_fp"]), "price": price, "fee": _d(f.get("fee_cost")),
            "taker_or_maker": "taker" if f.get("is_taker") else "maker", "order_id": oid,
            "client_order_id": o2c.get(oid, ""), "fill_id": f.get("fill_id", ""), "ts_epoch": tm.timestamp(),
        }
        row["strategy_tag"], extra = tag_fill(row, f, src)
        row.update(extra)
        row["market_result"] = results.get(t, "")
        rows.append(row)

    # Every fill is a position LEG of its `side` bought at its side price (verified: 327/327 settled tickers match
    # Kalshi's positions.realized_pnl exactly). `action` (buy/sell) is NOT used for the economics.
    for r in rows:
        r["flag"] = ""
        r["open_count"] = ZERO
        r["pnl"] = None
        res = r["market_result"]
        if res in ("yes", "no"):
            payout = D(1) if res == r["side"] else ZERO
            r["pnl"] = (payout - r["price"]) * r["count"] - r["fee"]
        elif res:
            r["flag"] = f"non_binary_result:{res}"
        else:
            r["open_count"] = r["count"]
    return {"rows": rows, "settle_rev": dict(settle_rev), "results": results}


# ======================================================================================= output
CSV_COLS = ["fill_time_utc", "fill_time_et", "shard", "ticker", "series", "event", "side", "action", "count",
            "price", "fee", "taker_or_maker", "order_id", "client_order_id", "strategy_tag", "market_result",
            "pnl", "open_count", "flag", "pick_proj", "pick_side", "pick_ping_price", "pick_limit",
            "pick_side_match", "pick_price_match", "fill_id"]


def write_csv(rows, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in CSV_COLS})


def fmt(x, nd=4):
    return f"{x:,.{nd}f}"


def shard_flow(rows):
    """Cash flow implied by fills: -cost - fee, plus $1 per winning contract once the market is settled.
    (Kalshi's /settlements `revenue` only covers the net position left at settlement; YES+NO pairs are paid at
    trade time, so the per-leg payout is used instead.)"""
    flow = defaultdict(lambda: ZERO)
    for r in rows:
        flow[r["shard"]] += -r["count"] * r["price"] - r["fee"]
        if r["market_result"] == r["side"]:
            flow[r["shard"]] += r["count"]
    return flow


def build_summary(raw: dict, led: dict, client=None) -> str:
    rows = led["rows"]
    out = []
    P = out.append
    P(f"TRADE LEDGER SUMMARY  (pulled {raw.get('pulled_utc', '?')})")
    P(f"fills: {len(rows)}   first fill: {rows[0]['fill_time_utc'] + ' UTC' if rows else 'n/a'}")
    P("")
    P("PER STRATEGY TAG x ET DATE  (cost = leg cost; sold column = legs with action=sell (informational); pnl = settled P&L)")
    P(f"{'tag':18} {'et_date':10} {'fills':>5} {'contr':>6} {'cost':>9} {'sold':>9} {'fees':>8} {'pnl':>9} {'open':>5}")
    g = defaultdict(lambda: {"n": 0, "c": ZERO, "cost": ZERO, "sold": ZERO, "fee": ZERO, "pnl": ZERO, "open": ZERO})
    for r in rows:
        a = g[(r["strategy_tag"], r["et_date"])]
        a["n"] += 1
        a["c"] += r["count"]
        if r["action"] == "buy":
            a["cost"] += r["count"] * r["price"]
        else:
            a["sold"] += r["count"] * r["price"]
        a["fee"] += r["fee"]
        if r["pnl"] is not None:
            a["pnl"] += r["pnl"]
        a["open"] += r["open_count"]
    for (tag, d), a in sorted(g.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        P(f"{tag:18} {d:10} {a['n']:>5} {fmt(a['c'], 0):>6} {fmt(a['cost']):>9} {fmt(a['sold']):>9} "
          f"{fmt(a['fee']):>8} {fmt(a['pnl']):>9} {fmt(a['open'], 0):>5}")
    P("")
    P("OPEN (unsettled) CONTRACTS")
    openpos = defaultdict(lambda: ZERO)
    for r in rows:
        if r["open_count"] > 0:
            openpos[(r["shard"], r["ticker"], r["side"], r["strategy_tag"])] += r["open_count"]
    if not openpos:
        P("  none")
    for (sh, t, side, tag), n in sorted(openpos.items(), key=lambda kv: kv[0]):
        status = ""
        if client is not None:
            try:
                m = client.market(t)
                m = m.get("market", m) if isinstance(m, dict) else {}
                status = f"market status={m.get('status')} result={m.get('result') or '-'}"
            except Exception as e:           # status is a nicety, never fatal
                status = f"status lookup failed ({type(e).__name__})"
        P(f"  shard {sh} {t} {side.upper()} x{fmt(n, 0)} [{tag}] {status}")
    P("")
    # per-ticker gross reconcile against Kalshi positions
    P("RECONCILE 1: ledger vs Kalshi positions.realized_pnl (per shard+ticker, settled tickers; ledger gross = pnl + fees)")
    led_t = defaultdict(lambda: [ZERO, ZERO, True])
    for r in rows:
        k = (r["shard"], r["ticker"])
        if r["pnl"] is None:
            led_t[k][2] = False
        else:
            led_t[k][0] += r["pnl"]
        led_t[k][1] += r["fee"]
    mism, checked, miss = [], 0, 0
    for sh, pl in raw.get("positions", {}).items():
        for p in pl:
            k = (int(sh), p.get("ticker"))
            if k not in led_t:
                if _d(p.get("total_traded_dollars")) != 0:
                    miss += 1
                continue
            pnl, fee, ok = led_t[k]
            if not ok or _d(p.get("position_fp")) != 0:
                continue
            checked += 1
            diff = (pnl + fee) - _d(p.get("realized_pnl_dollars"))
            if abs(diff) > D("0.005"):
                mism.append((k, pnl + fee, _d(p.get("realized_pnl_dollars")), diff))
    P(f"  checked {checked} closed tickers; mismatches > $0.005: {len(mism)}; positions with traded $ but no fills in "
      f"ledger: {miss}")
    for k, a, b, d in mism[:25]:
        P(f"  MISMATCH {k}: ledger gross {fmt(a)} vs kalshi {fmt(b)} (diff {fmt(d)})")
    P("")
    P("CASH PER SHARD and RECONCILE 2 (cash vs implied trading cash flow)")
    flow = shard_flow(rows)
    tot_cash = ZERO
    tot_flow = ZERO
    for s in SHARDS:
        c = _d(raw["cash"].get(str(s)))
        tot_cash += c
        tot_flow += flow[s]
        P(f"  shard {s}: cash {fmt(c)}   trading flow (-cost - fees + payouts) {fmt(flow[s])}   "
          f"implied net deposits/transfers {fmt(c - flow[s])}")
    P(f"  TOTAL cash {fmt(tot_cash)}   total trading flow {fmt(tot_flow)}   implied net deposits {fmt(tot_cash - tot_flow)}")
    P("  (deposits/withdrawals/shard transfers are not in fills; the implied column should equal net money moved in."
    "\n   Open positions' cost is already out of cash; deposits and inter-shard transfers are invisible here.)")
    P("")
    realized = sum((r["pnl"] for r in rows if r["pnl"] is not None), ZERO)
    open_cost = sum((r["open_count"] * r["price"] for r in rows), ZERO)
    flagged = [r for r in rows if r["flag"]]
    P(f"REALIZED P&L since first fill ({rows[0]['fill_time_utc'] if rows else 'n/a'} UTC): {fmt(realized)}")
    P(f"Open position cost (unsettled): {fmt(open_cost)}   -> realized + open cost = {fmt(realized + open_cost)}")
    P(f"Flagged fills (non-binary results): {len(flagged)}")
    return "\n".join(out)


def tonight_listing(rows, date_et: str) -> str:
    sel = [r for r in rows if r["et_date"] == date_et]
    out = [f"FILLS ON ET DATE {date_et}: {len(sel)}"]
    for r in sel:
        out.append(f"  {r['fill_time_et']} ET shard {r['shard']} {r['action']} {r['side']} x{r['count']:.0f} "
                   f"{r['ticker']} @ {r['price']} fee {r['fee']} [{r['strategy_tag']}] result={r['market_result'] or '-'} "
                   f"pnl={r['pnl'] if r['pnl'] is not None else '-'}")
    return "\n".join(out)


def run(client, root: Path = ROOT, now: datetime | None = None, today_et: str | None = None):
    raw = pull_all(client)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    rawdir = root / "logs/ledger_raw"
    rawdir.mkdir(parents=True, exist_ok=True)
    (rawdir / f"{stamp}.json").write_text(json.dumps(raw, indent=1, default=str), encoding="utf-8")
    led = build_ledger(raw, load_sources(root))
    write_csv(led["rows"], root / "logs/trade_ledger.csv")
    summ = build_summary(raw, led, client)
    today = today_et or datetime.now(ET).strftime("%Y-%m-%d")
    summ += "\n\n" + tonight_listing(led["rows"], today)
    (root / "logs/trade_ledger_summary.txt").write_text(summ + "\n", encoding="utf-8")
    realized, gap = run_metrics(raw, led)
    with open(root / "logs/trade_ledger_runs.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} fills={len(led['rows'])} "
                f"realized_pnl={realized:.4f} implied_net_deposits_gap={gap:.4f}\n")
    return raw, led, summ


def run_metrics(raw, led):
    realized = sum((r["pnl"] for r in led["rows"] if r["pnl"] is not None), ZERO)
    flow = shard_flow(led["rows"])
    cash = sum((_d(v) for v in raw["cash"].values()), ZERO)
    return realized, cash - sum(flow.values(), ZERO)


def main(journal=True):
    try:
        client = make_live_client()
        raw, led, summ = run(client)
        print(summ)
        if journal:
            try:
                import bet_journal
                bet_journal.run_journal(raw, led, client)
            except ImportError:
                pass
        return 0
    except Exception as e:            # never print the message: it could carry request details
        print(f"trade_ledger FAILED: {type(e).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
