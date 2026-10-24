"""READ-ONLY bet journal built on top of the fill ledger (scripts/trade_ledger.py).

One row per POSITION (shard + ticker + side) with its fills aggregated, plus one row per early-overs pick that was
never filled (filled=false). Outputs (logs/, git-ignored; nothing is sent to Discord):
  logs/bet_journal.csv            the journal
  logs/bet_journal_summary.txt    aggregates (strategy / version / date / week / side / gap / price / minutes-to-tip,
                                  bankroll curve, drawdown, losing streak)
  logs/bet_journal_COLUMNS.md     column documentation (generated from COLUMNS below)
  logs/bet_journal_archive/bet_journal_<UTC>.csv   dated copy of every run
  logs/bet_journal_cache.json     game facts + replayed book tops (so reruns are fast and idempotent)
Run by hand:  .venv/Scripts/python.exe scripts/bet_journal.py   (also called at the end of trade_ledger.py)

Sources: ledger fills (Kalshi GETs), logs/early_overs_picks.jsonl (intent + model), data/ws/nba/*/raw recordings
replayed with kpm.analysis.ws_books (closing quote), public GET market (tip time, final total, OT), public candles
as the closing-quote fallback, reports/output/ohio_trial/live_26OCT20/events.jsonl (lock-bot attempts).

Conventions (documented, not hidden):
 * Tip time = Kalshi occurrence_datetime - 3h (the same proxy early_overs_picks.py uses); picks rows' tip_et wins.
 * Position side: YES/NO; for KXNBATOTAL it is shown as OVER (yes) / UNDER (no). Line = market floor_strike.
 * Fills are position legs of their side (see trade_ledger.py); a locked pair is two positions (YES leg + NO leg) of
   one ticker, `ticker_net_pnl` shows the pair's net.
 * Exit: 'held' = rode to settlement; 'hedged' = an opposite-side leg was bought later on the same ticker (locks
   the pair, exit time = that leg's first fill); 'hedge_leg' = this leg completes an earlier opposite leg; 'open'.
 * CLV (cents) = (market price of OUR side at tip) - (average entry price), in cents. Positive = the market moved
   our way (we bought cheaper than the close). Price at tip = mid of the bet side's best bid/ask at the last book
   update at or before tip (WS recording); fallback = close of the hourly candle that ends nearest tip.
 * Line move (KXNBATOTAL only) = main line (strike whose YES mid is closest to 50c) at tip minus at first fill.
   'toward us' = OVER and the line rose, or UNDER and the line fell (the market came toward our projection).
 * bankroll_est_usd = current total cash minus every cash movement after the entry (costs at fill time, payouts at
   settlement time); it ignores deposits/transfers after entry, so it is an estimate.
"""
from __future__ import annotations

import csv
import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
GAME_KEY_RE = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})$")

COLUMNS = {
    "bet_id": "ET date + ticker + side; unique key of the row",
    "strategy_tag": "ledger tag (lockbot_26OCT20, early_overs_bot, early_overs_40, picks_send, exec_log, manual_other)",
    "version": "research-log version, e.g. '#40 v5' (rule grid_winner, T=4); lock bot = 'lockbot 26OCT20'; '' if unknown",
    "et_date": "ET date of the first fill (or of the pick's tip for unfilled picks)",
    "game_key": "e.g. 26OCT21ATLORL (NBA tickers only)",
    "series": "Kalshi series, e.g. KXNBATOTAL",
    "ticker": "market ticker",
    "line": "strike / floor_strike of the market (points for totals)",
    "side": "OVER/UNDER for KXNBATOTAL, else YES/NO",
    "bet_type": "total / moneyline / spread / quarter-half / prop / btc15m / nfl / other",
    "shard": "exchange shard (0-3)",
    "filled": "true if any contract filled; false for a pick that never filled",
    "first_fill_utc": "first fill time UTC", "last_fill_utc": "last fill time UTC",
    "first_fill_et": "first fill time ET", "last_fill_et": "last fill time ET",
    "tip_utc": "estimated tip-off UTC (occurrence_datetime - 3h)",
    "min_before_tip": "minutes from first fill to tip (positive = before tip)",
    "contracts": "total contracts filled", "avg_entry_price": "average entry price of our side, dollars",
    "fees": "total fees paid, dollars", "maker_taker": "taker / maker / mixed",
    "cost_usd": "contracts * price + fees", "bankroll_est_usd": "estimated total cash just before entry (approx.)",
    "stake_pct_bankroll": "cost_usd / bankroll_est_usd * 100",
    "pick_ping_utc": "time of the pick-list row used as the ping (first PLACE NOW row, else first bet row)",
    "pick_ping_taker_price": "taker price of our side at the ping, dollars",
    "pick_limit_price": "maker limit price at the ping, dollars",
    "slippage_c": "(avg entry price - ping taker price) * 100; positive = paid more than the ping (worse)",
    "pick_proj": "model projected total at entry", "pick_gap": "proj - line (signed)",
    "pick_p": "formula win probability p (Phi(|gap|/sigma))", "pick_ev_c": "formula EV in cents (maker)",
    "pick_rule": "threshold rule, e.g. grid_winner |gap|>=4.0",
    "median_line_at_entry": "market main line at first fill (WS; else the pick's strike)",
    "median_line_at_close": "market main line at tip (WS/candles)",
    "line_move_pts": "close main line - entry main line", "line_move_dir": "toward_us / against_us / flat / n/a",
    "exit_type": "held / hedged (opposite leg bought later) / hedge_leg (this leg completes an earlier opposite leg; held to settlement) / open", "exit_time_utc": "settlement time (held) or hedge-leg time",
    "settled_time_utc": "Kalshi settlement time", "hold_hours": "hours from first fill to exit",
    "result": "market result (yes/no) or '' if unsettled", "payout_usd": "dollars paid out to our contracts",
    "net_pnl_usd": "payout - cost (cost includes fees); empty if unsettled", "roi_pct": "net_pnl / cost * 100",
    "win": "true/false (empty if unsettled)", "break_even_wr": "avg entry price + avg fee per contract",
    "close_bid": "bet side best bid at tip", "close_ask": "bet side best ask at tip", "close_mid": "bet side mid at tip",
    "close_source": "ws / candle / none", "clv_c": "CLV in cents (see header)",
    "final_total": "final game total points (market expiration_value, else hoopR box)", "overtime": "true/false/''",
    "margin_vs_line": "from our side: OVER total-line, UNDER line-total (positive = our side won by that much)",
    "ticker_net_pnl_usd": "net P&L of all legs of this ticker (both sides)",
    "lock_attempt_ids": "lock-bot attempt timestamps that produced these fills (events.jsonl)",
    "lock_kind": "lock-bot attempt kind (e.g. me_no)", "lock_legs_attempted": "legs sent in the attempt",
    "lock_legs_filled": "legs with fill_count > 0 in the attempt", "lock_partial_reason": "why the lock was partial",
    "what_went": "auto-generated plain-English attribution tags", "notes": "free text for the user / Opus (kept blank)",
}
COLS = list(COLUMNS)


# ======================================================================================= pure helpers
def side_label(series: str, side: str) -> str:
    if series == "KXNBATOTAL":
        return "OVER" if side == "yes" else "UNDER"
    return side.upper()


def bet_type(series: str) -> str:
    if series == "KXNBATOTAL":
        return "total"
    if series == "KXNBAGAME":
        return "moneyline"
    if series == "KXNBASPREAD":
        return "spread"
    if series.startswith("KXNBA") and any(k in series for k in ("1H", "2H", "1Q", "2Q", "3Q", "4Q")):
        return "quarter-half"
    if series.startswith("KXNBA"):
        return "prop"
    if series.startswith("KXBTC"):
        return "btc15m"
    if series.startswith("KXNFL"):
        return "nfl"
    return "other"


def side_book(side: str, yes_bid, yes_ask):
    """(bid, ask) of OUR side from the YES book."""
    if side == "yes":
        return yes_bid, yes_ask
    return (None if yes_ask is None else 1 - yes_ask), (None if yes_bid is None else 1 - yes_bid)


def close_mid_for(side: str, yes_bid, yes_ask):
    b, a = side_book(side, yes_bid, yes_ask)
    if b is None or a is None:
        return b, a, None
    return b, a, (b + a) / 2


def clv_cents(entry_price: float, close_price: float | None):
    """Positive = our side is worth more at the close than we paid."""
    if close_price is None:
        return None
    return (close_price - entry_price) * 100


def slippage_cents(paid: float, ping: float | None):
    return None if ping is None else (paid - ping) * 100


def hold_hours(t0, t1):
    return None if t0 is None or t1 is None else (t1 - t0).total_seconds() / 3600


def roi_pct(pnl, cost):
    return None if pnl is None or not cost else pnl / cost * 100


def main_line(book: dict):
    """book: {strike: (yes_bid, yes_ask)} -> strike with YES mid closest to 0.5 (needs both sides)."""
    best = None
    for k, (b, a) in book.items():
        if b is None or a is None:
            continue
        key = (abs((b + a) / 2 - 0.5), k)
        if best is None or key < best[0]:
            best = (key, k)
    return None if best is None else best[1]


def max_drawdown_and_streak(pnls):
    cum = peak = dd = 0.0
    streak = best = 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
        streak = streak + 1 if p < 0 else 0
        best = max(best, streak)
    return dd, best


def attribution(*, side_lbl, result_win, margin, ot, clv, line_dir, line_move, proj, line, total, slip_c, filled,
                pending=False):
    tags = []
    if not filled:
        return "pending (game not played yet)" if pending else "not filled"
    if margin is not None and result_win is not None:
        m = abs(margin)
        if result_win:
            tags.append(f"won by {m:g} pts")
        else:
            tags.append(f"lost by {m:g} (near-miss)" if m <= 1.0 else f"lost by {m:g} pts")
    elif result_win is not None:
        tags.append("won" if result_win else "lost")
    if ot and side_lbl in ("OVER", "UNDER") and result_win is not None and ((side_lbl == "OVER") == result_win):
        tags.append("overtime decided it" if side_lbl == "OVER" else "overtime decided it (against us)")
    elif ot:
        tags.append("went to overtime")
    if clv is not None:
        tags.append(f"positive CLV (+{clv:.1f}c)" if clv > 0 else f"negative CLV ({clv:.1f}c)" if clv < 0 else "CLV flat")
    if line_dir in ("toward_us", "against_us") and line_move is not None:
        tags.append(f"line moved {abs(line_move):g} pts {'our way' if line_dir == 'toward_us' else 'against us'}")
    if proj is not None and line is not None and total is not None:
        me, ke = abs(proj - total), abs(line - total)
        tags.append(f"model error {me:.1f} pts vs market error {ke:.1f} pts "
                    f"({'model closer' if me < ke else 'market closer'})")
    if slip_c is not None:
        if slip_c > 0.05:
            tags.append(f"filled {slip_c:.0f}c worse than the ping")
        elif slip_c < -0.05:
            tags.append(f"filled {-slip_c:.0f}c better than the ping")
        else:
            tags.append("filled at the ping")
    return "; ".join(tags)


def version_of(pick: dict | None, tag: str) -> str:
    if tag == "lockbot_26OCT20":
        return "lockbot 26OCT20"
    if pick is None:
        return ""
    if pick.get("rule") == "grid_winner":
        t = pick.get("grid_t")
        return {4.0: "#40 v5", 8.0: "#40 v4"}.get(t, f"#40 grid T={t}")
    if pick.get("rule") == "formula_side":
        return "#40 v3"
    return "#40 (pre-v4)"


def pick_rule_text(pick: dict | None) -> str:
    if not pick:
        return ""
    if pick.get("rule") == "grid_winner":
        return f"grid_winner |gap|>={pick.get('grid_t')}"
    return str(pick.get("rule") or "formula_side")


# ======================================================================================= data loading
def _jl(path: Path):
    if path.exists():
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def _iso(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def load_picks(root: Path):
    """{(ticker, side 'yes'/'no'): [bet rows in time order]}"""
    out = defaultdict(list)
    for r in _jl(root / "logs/early_overs_picks.jsonl"):
        if r.get("ticker") and (r.get("bet") is True or str(r.get("status", "")).startswith("PLACE NOW")):
            out[(r["ticker"], str(r.get("buy", "")).lower())].append(r)
    for v in out.values():
        v.sort(key=lambda r: r["ts"])
    return out


def pick_tip_utc(row):
    try:
        return datetime.strptime(row["tip_et"], "%Y-%m-%d %H:%M").replace(tzinfo=ET).astimezone(UTC)
    except Exception:
        return None


def load_lock_attempts(root: Path):
    out = []
    for ev in _jl(root / "reports/output/ohio_trial/live_26OCT20/events.jsonl"):
        if ev.get("event") == "attempt":
            out.append(ev)
    return out


# ======================================================================================= cache + game facts
class Cache:
    def __init__(self, path: Path):
        self.path = path
        self.d = {"games": {}, "tops": {}}
        if path.exists():
            try:
                self.d = json.load(open(path, encoding="utf-8"))
            except ValueError:
                pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.d), encoding="utf-8")


def _mkt(client, ticker):
    try:
        m = client.market(ticker)
        return m.get("market", m) if isinstance(m, dict) else None
    except Exception:
        return None


def game_facts(client, cache: Cache, game_key: str, total_ticker: str | None):
    """{tip_utc, overtime, final_total} from public GETs; cached only once the game is final."""
    g = cache.d["games"].get(game_key)
    if g and g.get("final"):
        return g
    info = {"tip_utc": None, "overtime": None, "final_total": None, "final": False}
    ot = _mkt(client, f"KXNBAOT-{game_key}-1")
    if ot:
        occ = ot.get("occurrence_datetime")
        if occ:
            info["tip_utc"] = (_iso(occ) - timedelta(hours=3)).isoformat()
        if ot.get("result") in ("yes", "no"):
            info["overtime"] = ot["result"] == "yes"
    if total_ticker:
        tm = _mkt(client, total_ticker)
        if tm and tm.get("result") in ("yes", "no") and tm.get("expiration_value") not in (None, ""):
            try:
                info["final_total"] = float(tm["expiration_value"])
            except ValueError:
                pass
        if tm and not info["tip_utc"] and tm.get("occurrence_datetime"):
            info["tip_utc"] = (_iso(tm["occurrence_datetime"]) - timedelta(hours=3)).isoformat()
    info["final"] = info["final_total"] is not None and info["overtime"] is not None
    cache.d["games"][game_key] = info
    return info


# ======================================================================================= WS replay for closing quotes
def recording_dirs(root: Path):
    base = root / "data/ws/nba"
    out = []
    if not base.exists():
        return out
    for d in sorted(p for p in base.iterdir() if (p / "raw").is_dir()):
        files = sorted((d / "raw").glob("msgs-*.jsonl.gz"))
        if not files:
            continue
        hrs = [f.name.split("-")[1] for f in files]
        lo = datetime.strptime(min(hrs), "%Y%m%d%H").replace(tzinfo=UTC)
        hi = datetime.strptime(max(hrs), "%Y%m%d%H").replace(tzinfo=UTC) + timedelta(hours=1)
        out.append((d, lo, hi))
    return out


def replay_tops(raw_dir: Path, queries):
    """queries: list of (ts_s, event_ticker). Returns {(ts_s, event): {ticker: (yes_bid, yes_ask)}} using the last
    book update at or before ts (books from kpm.analysis.ws_books; invalid books are dropped by BookSet)."""
    from kpm.analysis import ws_books as wb
    qs = sorted(set(queries))
    res = {}
    if not qs:
        return res
    cur = {}
    books = wb.BookSet()
    i = 0

    def answer_upto(ns):
        nonlocal i
        while i < len(qs) and qs[i][0] * 1_000_000_000 < ns:
            ts, ev = qs[i]
            res[(ts, ev)] = {t: v for t, v in cur.items() if t.startswith(ev + "-")}
            i += 1

    for line in wb.read_lines(wb.raw_files(raw_dir)):
        ns = line["recv_ns"]
        answer_upto(ns)
        if i >= len(qs):
            break
        for e in books.feed(line):
            if isinstance(e, wb.Top):
                cur[e.ticker] = (e.best_yes_bid, e.best_yes_ask)
        for t in books.dropped:
            cur.pop(t, None)
        books.dropped.clear()
    answer_upto(1 << 62)
    return res


def candle_close(kc, ticker, series, tip_ts):
    """Fallback: close price (dollars, YES) of the hourly candle ending nearest to tip. Best effort."""
    try:
        cs = kc.candlesticks(series, ticker, int(tip_ts) - 4 * 3600, int(tip_ts) + 3600, 60, False)
    except Exception:
        return None
    best = None
    for c in cs:
        d = abs(c["end_period_ts"] - tip_ts)
        p = (c.get("price") or {}).get("close_dollars") or (c.get("price") or {}).get("close")
        if p in (None, ""):
            continue
        p = float(p)
        p = p / 100 if p > 1.0 else p
        if best is None or d < best[0]:
            best = (d, p)
    return None if best is None else best[1]


# ======================================================================================= build
def build_journal(raw, led, client, root: Path = ROOT, now: datetime | None = None, kc=None, replay=True):
    now = now or datetime.now(UTC)
    cache = Cache(root / "logs/bet_journal_cache.json")
    picks = load_picks(root)
    attempts = load_lock_attempts(root)
    leg_by_oid = {}
    for ev in attempts:
        for b, leg in zip(ev.get("bodies", []), ev.get("legs", [])):
            if leg.get("order_id"):
                leg_by_oid[leg["order_id"]] = (ev, b, leg)

    settle = {}
    for s in raw.get("settlements", []):
        settle[(int(s["exchange_index"]), s["ticker"])] = s.get("settled_time")
    # group fills into positions
    groups = defaultdict(list)
    for r in led["rows"]:
        groups[(r["shard"], r["ticker"], r["side"])].append(r)
    tick_pnl = defaultdict(lambda: 0.0)
    tick_pnl_ok = defaultdict(lambda: True)
    for r in led["rows"]:
        if r["pnl"] is None:
            tick_pnl_ok[r["ticker"]] = False
        else:
            tick_pnl[r["ticker"]] += float(r["pnl"])

    # bankroll reconstruction (approximate): list of (epoch, delta)
    cash_now = float(sum((Decimal(str(v)) for v in raw["cash"].values()), Decimal(0)))
    moves = []
    for r in led["rows"]:
        moves.append((r["ts_epoch"], -float(r["count"] * r["price"] + r["fee"])))
        if r["market_result"] == r["side"]:
            st = settle.get((r["shard"], r["ticker"]))
            if st:
                moves.append((_iso(st).timestamp(), float(r["count"])))

    def bankroll_before(t):
        return cash_now - sum(d for ts, d in moves if ts >= t)

    out, tops_needed = [], []
    pos_info = []
    for (shard, ticker, side), fl in groups.items():
        fl.sort(key=lambda r: r["ts_epoch"])
        series, event = fl[0]["series"], fl[0]["event"]
        parts = ticker.split("-")
        game_key = parts[1] if len(parts) > 1 and GAME_KEY_RE.match(parts[1]) and series.startswith("KXNBA") else ""
        pick_rows = picks.get((ticker, side), [])
        tip = None
        if pick_rows:
            tip = pick_tip_utc(pick_rows[0])
        total_ticker = None
        if game_key:
            total_ticker = ticker if series == "KXNBATOTAL" else None
            if total_ticker is None:
                md = root / "data/ws/nba"
                for mj in sorted(md.glob("*/markets.json")):
                    try:
                        g = json.load(open(mj, encoding="utf-8"))["games"].get(game_key)
                    except Exception:
                        g = None
                    if g:
                        total_ticker = next((t for t in g["tickers"] if t.startswith("KXNBATOTAL-")), None)
                        break
            facts = game_facts(client, cache, game_key, total_ticker) if client is not None else {}
            if facts.get("tip_utc") and tip is None:
                tip = _iso(facts["tip_utc"])
        else:
            facts = {}
        pos_info.append((shard, ticker, side, fl, series, event, game_key, pick_rows, tip, facts))
        if tip is not None and series.startswith("KXNBA"):
            tops_needed.append((int(tip.timestamp()), event))
            tops_needed.append((int(fl[0]["ts_epoch"]), event))

    # replay recordings for the tops we need (cached per (ts, event))
    tops = cache.d["tops"]
    todo = [q for q in set(tops_needed) if f"{q[0]}|{q[1]}" not in tops and q[0] + 600 < now.timestamp()]
    if replay and todo:
        remaining = list(todo)
        for d, lo, hi in recording_dirs(root):
            mine = [q for q in remaining if lo.timestamp() + 60 <= q[0] <= hi.timestamp()]
            if not mine:
                continue
            res = replay_tops(d / "raw", mine)
            for (ts, ev), v in res.items():
                tops[f"{ts}|{ev}"] = {t: list(x) for t, x in v.items()}      # {} = covered but no book (cached)
            remaining = [q for q in remaining if f"{q[0]}|{q[1]}" not in tops]
        cache.save()

    for shard, ticker, side, fl, series, event, game_key, pick_rows, tip, facts in pos_info:
        n = sum((r["count"] for r in fl), Decimal(0))
        cost_px = sum((r["count"] * r["price"] for r in fl), Decimal(0))
        fees = sum((r["fee"] for r in fl), Decimal(0))
        avg = float(cost_px / n)
        cost = float(cost_px + fees)
        t0, t1 = fl[0]["ts_epoch"], fl[-1]["ts_epoch"]
        d0 = datetime.fromtimestamp(t0, UTC)
        res = fl[0]["market_result"]
        settled = res in ("yes", "no")
        win = (res == side) if settled else None
        payout = float(n) if win else 0.0
        pnl = payout - cost if settled else None
        mt = {r["taker_or_maker"] for r in fl}
        slbl = side_label(series, side)
        tag = fl[0]["strategy_tag"]
        stt = settle.get((shard, ticker))
        opp = [r for r in groups.get((shard, ticker, "no" if side == "yes" else "yes"), [])]
        opp_t = min((r["ts_epoch"] for r in opp), default=None)
        if opp_t is not None and opp_t >= t0:
            exit_type, exit_t = "hedged", datetime.fromtimestamp(opp_t, UTC)      # an opposite leg locked it later
        elif opp_t is not None:
            exit_type, exit_t = "hedge_leg", (_iso(stt) if stt else None)          # this leg completes an earlier leg
        elif settled:
            exit_type, exit_t = "held", (_iso(stt) if stt else None)
        else:
            exit_type, exit_t = "open", None
        # intent / model
        pr = pick_rows
        ping = next((p for p in pr if str(p.get("status", "")).startswith("PLACE NOW")), pr[0] if pr else None)
        before = [p for p in pr if _iso(p["ts"]).timestamp() <= t0]
        mrow = (before or pr or [None])[-1]
        ping_px = float(ping["taker_price"]) if ping and ping.get("taker_price") is not None else None
        slip = slippage_cents(avg, ping_px)
        # market line facts
        line = None
        if series == "KXNBATOTAL":
            m = re.search(r"-(\d+)$", ticker)
            line = (float(m.group(1)) - 0.5) if m else None
            if mrow and mrow.get("strike") is not None:
                line = float(mrow["strike"])
        ek, ck = f"{int(t0)}|{event}", (f"{int(tip.timestamp())}|{event}" if tip else None)
        etop, ctop = tops.get(ek), tops.get(ck) if ck else None
        pre_tip = tip is not None and t0 <= tip.timestamp()     # CLV/line move only make sense for pre-tip entries
        if not pre_tip:
            ctop = None
        med_e = med_c = None
        if series == "KXNBATOTAL":
            def strikes(tp):
                return {float(re.search(r"-(\d+)$", t).group(1)) - 0.5: tuple(v) for t, v in tp.items()
                        if re.search(r"-(\d+)$", t)} if tp else {}
            med_e, med_c = main_line(strikes(etop)), main_line(strikes(ctop))
            if med_e is None and mrow and mrow.get("strike") is not None:
                med_e = float(mrow["strike"])
        move = None if med_e is None or med_c is None else med_c - med_e
        mdir = "n/a"
        if move is not None:
            mdir = "flat" if move == 0 else ("toward_us" if (move > 0) == (slbl == "OVER") else "against_us")
        # closing quote
        cb = ca = cm = None
        csrc = "none"
        if ctop and ticker in ctop:
            cb, ca, cm = close_mid_for(side, *ctop[ticker])
            csrc = "ws" if cm is not None else "none"
        if cm is None and pre_tip and kc is not None and series.startswith("KXNBA") and tip < now:
            cp = candle_close(kc, ticker, series, tip.timestamp())
            if cp is not None:
                cm = cp if side == "yes" else 1 - cp
                csrc = "candle"
        clv = clv_cents(avg, cm)
        total, ot = facts.get("final_total"), facts.get("overtime")
        margin = None
        if total is not None and line is not None and series == "KXNBATOTAL":
            margin = (total - line) if slbl == "OVER" else (line - total)
        # lock bot
        lk = {}
        if tag == "lockbot_26OCT20":
            hits = [leg_by_oid[r["order_id"]] for r in fl if r["order_id"] in leg_by_oid]
            if hits:
                ev = hits[0][0]
                nlegs = len(ev.get("legs", []))
                filled = sum(1 for lg in ev["legs"] if lg.get("fill_count") not in (None, "0", "0.00") and float(lg["fill_count"]) > 0)
                errs = [f"{b['ticker'].split('-')[-1]}: {lg.get('error') or 'no fill (IOC missed at limit ' + str(b.get('price')) + ')'}"
                        for b, lg in zip(ev.get("bodies", []), ev["legs"]) if float(lg.get("fill_count") or 0) == 0]
                lk = {"lock_attempt_ids": ";".join(sorted({h[0]["time"] for h in hits})), "lock_kind": ev.get("kind"),
                      "lock_legs_attempted": nlegs, "lock_legs_filled": filled,
                      "lock_partial_reason": "complete" if filled == nlegs else "partial: " + " | ".join(errs)}
        what = attribution(side_lbl=slbl, result_win=win, margin=margin, ot=ot, clv=clv, line_dir=mdir,
                           line_move=move, proj=(mrow or {}).get("proj"), line=line, total=total, slip_c=slip,
                           filled=True)
        if tag == "lockbot_26OCT20" and lk:
            what = (what + "; " if what else "") + f"lock attempt {lk['lock_legs_filled']}/{lk['lock_legs_attempted']} legs filled"
        bank = bankroll_before(t0)
        row = {
            "bet_id": f"{d0.astimezone(ET):%Y-%m-%d}-{ticker}-{side}", "strategy_tag": tag,
            "version": version_of(mrow, tag), "et_date": f"{d0.astimezone(ET):%Y-%m-%d}", "game_key": game_key,
            "series": series, "ticker": ticker, "line": line, "side": slbl, "bet_type": bet_type(series),
            "shard": shard, "filled": True,
            "first_fill_utc": d0.strftime("%Y-%m-%d %H:%M:%S"), "last_fill_utc": datetime.fromtimestamp(t1, UTC).strftime("%Y-%m-%d %H:%M:%S"),
            "first_fill_et": fl[0]["fill_time_et"], "last_fill_et": fl[-1]["fill_time_et"],
            "tip_utc": tip.strftime("%Y-%m-%d %H:%M") if tip else "",
            "min_before_tip": round((tip.timestamp() - t0) / 60, 1) if tip else "",
            "contracts": float(n), "avg_entry_price": round(avg, 4), "fees": round(float(fees), 4),
            "maker_taker": mt.pop() if len(mt) == 1 else "mixed", "cost_usd": round(cost, 4),
            "bankroll_est_usd": round(bank, 2) if bank > 0 else "",
            "stake_pct_bankroll": round(cost / bank * 100, 2) if bank > 0 else "",
            "pick_ping_utc": ping["ts"][:19].replace("T", " ") if ping else "", "pick_ping_taker_price": ping_px if ping_px is not None else "",
            "pick_limit_price": (ping or {}).get("limit", ""), "slippage_c": round(slip, 2) if slip is not None else "",
            "pick_proj": (mrow or {}).get("proj", ""), "pick_gap": (mrow or {}).get("proj_minus_strike", ""),
            "pick_p": round((mrow or {}).get("p_formula"), 4) if (mrow or {}).get("p_formula") is not None else "",
            "pick_ev_c": (mrow or {}).get("ev_c", ""), "pick_rule": pick_rule_text(mrow),
            "median_line_at_entry": med_e if med_e is not None else "", "median_line_at_close": med_c if med_c is not None else "",
            "line_move_pts": move if move is not None else "", "line_move_dir": mdir,
            "exit_type": exit_type, "exit_time_utc": exit_t.strftime("%Y-%m-%d %H:%M:%S") if exit_t else "",
            "settled_time_utc": stt[:19].replace("T", " ") if stt else "",
            "hold_hours": round(hold_hours(d0, exit_t), 2) if exit_t else "",
            "result": res, "payout_usd": round(payout, 4) if settled else "",
            "net_pnl_usd": round(pnl, 4) if pnl is not None else "", "roi_pct": round(roi_pct(pnl, cost), 1) if pnl is not None else "",
            "win": "" if win is None else str(win).lower(), "break_even_wr": round(avg + float(fees / n), 4),
            "close_bid": "" if cb is None else round(cb, 4), "close_ask": "" if ca is None else round(ca, 4),
            "close_mid": "" if cm is None else round(cm, 4), "close_source": csrc,
            "clv_c": "" if clv is None else round(clv, 2),
            "final_total": total if total is not None else "", "overtime": "" if ot is None else str(ot).lower(),
            "margin_vs_line": margin if margin is not None else "",
            "ticker_net_pnl_usd": round(tick_pnl[ticker], 4) if tick_pnl_ok[ticker] else "",
            "what_went": what, "notes": "",
        }
        row.update(lk)
        out.append(row)

    # picks that never filled
    filled_keys = {(t, s) for (_, t, s) in groups}
    seen_unfilled = set()
    for (ticker, side), pr in picks.items():
        if (ticker, side) in filled_keys:
            continue
        ping = next((p for p in pr if str(p.get("status", "")).startswith("PLACE NOW")), pr[0])
        tip = pick_tip_utc(ping)
        pend = tip is None or tip > now - timedelta(hours=1)
        key = (ticker, side)
        if key in seen_unfilled:
            continue
        seen_unfilled.add(key)
        last = pr[-1]
        series = ticker.split("-")[0]
        parts = ticker.split("-")
        out.append({
            "bet_id": f"{ping['tip_et'][:10]}-{ticker}-{side}", "strategy_tag": "early_overs_40",
            "version": version_of(last, "early_overs_40"), "et_date": ping["tip_et"][:10],
            "game_key": ping.get("game", ""), "series": series, "ticker": ticker, "line": last.get("strike"),
            "side": side_label(series, side), "bet_type": bet_type(series), "filled": False,
            "tip_utc": tip.strftime("%Y-%m-%d %H:%M") if tip else "",
            "pick_ping_utc": ping["ts"][:19].replace("T", " "), "pick_ping_taker_price": ping.get("taker_price", ""),
            "pick_limit_price": ping.get("limit", ""), "pick_proj": last.get("proj", ""),
            "pick_gap": last.get("proj_minus_strike", ""), "pick_p": last.get("p_formula") or "", "pick_ev_c": last.get("ev_c", ""),
            "pick_rule": pick_rule_text(last), "exit_type": "n/a",
            "what_went": attribution(side_lbl="", result_win=None, margin=None, ot=None, clv=None, line_dir="n/a",
                                     line_move=None, proj=None, line=None, total=None, slip_c=None, filled=False, pending=pend),
            "notes": "",
        })
    out.sort(key=lambda r: (r.get("first_fill_utc") or r.get("tip_utc") or "", r["bet_id"]))
    cache.save()
    return out


# ======================================================================================= summary
def week_of(et_date: str) -> str:
    d = datetime.strptime(et_date, "%Y-%m-%d")
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def gap_bucket(g):
    if g in ("", None):
        return "n/a"
    g = abs(float(g))
    return "<4" if g < 4 else "4-6" if g < 6 else "6-8" if g < 8 else "8+"


def price_bucket(p):
    c = float(p) * 100
    return "0-30c" if c < 30 else "30-50c" if c < 50 else "50-70c" if c < 70 else "70c+"


def min_bucket(m):
    if m in ("", None):
        return "n/a"
    m = float(m)
    return "after tip" if m < 0 else "0-15" if m < 15 else "15-45" if m < 45 else "45-90" if m < 90 else "90+"


def fnum(v):
    return float(v) if v not in ("", None) else None


def metrics(rows):
    filled = [r for r in rows if r.get("filled")]
    settled = [r for r in filled if r.get("net_pnl_usd") not in ("", None)]
    wins = sum(1 for r in settled if r["win"] == "true")
    pnl = sum(float(r["net_pnl_usd"]) for r in settled)
    cost = sum(float(r["cost_usd"]) for r in settled)
    be = [float(r["break_even_wr"]) for r in settled]
    clvs = [float(r["clv_c"]) for r in filled if r.get("clv_c") not in ("", None)]
    slips = [float(r["slippage_c"]) for r in filled if r.get("slippage_c") not in ("", None)]
    npick = sum(1 for r in rows if r.get("pick_ping_utc"))
    nfill = sum(1 for r in rows if r.get("pick_ping_utc") and r.get("filled"))
    return {"bets": len(filled), "contracts": sum(float(r["contracts"]) for r in filled), "settled": len(settled),
            "win_rate": wins / len(settled) if settled else None, "avg_be": sum(be) / len(be) if be else None,
            "pnl": pnl, "roi": pnl / cost * 100 if cost else None,
            "avg_clv": sum(clvs) / len(clvs) if clvs else None,
            "pos_clv": sum(1 for c in clvs if c > 0) / len(clvs) * 100 if clvs else None, "n_clv": len(clvs),
            "avg_slip": sum(slips) / len(slips) if slips else None,
            "fill_rate": nfill / npick * 100 if npick else None, "n_picks": npick}


def _f(v, fmt="{:.2f}", pct=False):
    return "-" if v is None else (fmt.format(v) + ("%" if pct else ""))


def fmt_group(title, keyf, rows):
    g = defaultdict(list)
    for r in rows:
        g[keyf(r)].append(r)
    lines = [f"BY {title}",
             f"  {'key':22} {'bets':>4} {'settl':>5} {'win%':>6} {'avgBE%':>7} {'netPnL':>8} {'ROI%':>7} {'avgCLVc':>8} {'CLV>0%':>7} {'slipC':>6} {'fill%':>6}"]
    for k in sorted(g, key=str):
        m = metrics(g[k])
        lines.append(f"  {str(k)[:22]:22} {m['bets']:>4} {m['settled']:>5} "
                     f"{_f(None if m['win_rate'] is None else m['win_rate'] * 100, '{:.0f}'):>6} "
                     f"{_f(None if m['avg_be'] is None else m['avg_be'] * 100, '{:.0f}'):>7} {m['pnl']:>8.2f} "
                     f"{_f(m['roi'], '{:.1f}'):>7} {_f(m['avg_clv'], '{:.1f}'):>8} {_f(m['pos_clv'], '{:.0f}'):>7} "
                     f"{_f(m['avg_slip'], '{:.1f}'):>6} {_f(m['fill_rate'], '{:.0f}'):>6}")
    return lines


def build_summary(rows):
    out = ["BET JOURNAL SUMMARY", f"rows: {len(rows)} (filled {sum(1 for r in rows if r.get('filled'))}, "
           f"unfilled picks {sum(1 for r in rows if not r.get('filled'))}). Each lock-bot leg is its own row; a locked "
           "pair's net is in ticker_net_pnl_usd. 'slipC' and 'fill%' only exist for pick-list bets.", ""]
    filled = [r for r in rows if r.get("filled")]
    out += fmt_group("STRATEGY", lambda r: r["strategy_tag"], rows) + [""]
    out += fmt_group("VERSION", lambda r: r.get("version") or "(none)", rows) + [""]
    out += fmt_group("ET DATE", lambda r: r["et_date"], rows) + [""]
    out += fmt_group("WEEK", lambda r: week_of(r["et_date"]), rows) + [""]
    out += fmt_group("SIDE", lambda r: r["side"] if r.get("filled") else "(unfilled)", rows) + [""]
    tot = [r for r in rows if r.get("bet_type") == "total"]
    out += fmt_group("GAP BUCKET (totals, |proj-line|)", lambda r: gap_bucket(r.get("pick_gap")), tot) + [""]
    out += fmt_group("PRICE BUCKET (avg entry)", lambda r: price_bucket(r["avg_entry_price"]), filled) + [""]
    out += fmt_group("MINUTES BEFORE TIP", lambda r: min_bucket(r.get("min_before_tip")), filled) + [""]
    settled = sorted((r for r in filled if r.get("net_pnl_usd") not in ("", None)), key=lambda r: r["first_fill_utc"])
    pn = [float(r["net_pnl_usd"]) for r in settled]
    dd, streak = max_drawdown_and_streak(pn)
    out.append(f"BANKROLL CURVE (cumulative net P&L by settled bet, chronological): bets {len(pn)}, "
               f"final {sum(pn):+.2f}, max drawdown {dd:.2f}, longest losing streak {streak}")
    cum = 0.0
    for i, r in enumerate(settled, 1):
        cum += float(r["net_pnl_usd"])
        if len(settled) <= 40 or i % 10 == 0 or i == len(settled):
            out.append(f"  #{i:<4} {r['first_fill_utc']} {r['strategy_tag']:16} {r['ticker'][:38]:38} {float(r['net_pnl_usd']):+7.2f}  cum {cum:+8.2f}")
    out += ["", "NOTE: bet counts mix BTC/NFL/other history (no CLV there: no recordings) with the NBA bets; read the "
            "strategy and date tables first."]
    return "\n".join(out)


def write_outputs(rows, root: Path = ROOT, now: datetime | None = None):
    now = now or datetime.now(UTC)
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    cols = COLS
    for path in (logs / "bet_journal.csv", logs / "bet_journal_archive" / f"bet_journal_{now:%Y%m%dT%H%M%SZ}.csv"):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in cols})
    summ = build_summary(rows)
    (logs / "bet_journal_summary.txt").write_text(summ + "\n", encoding="utf-8")
    md = ["# bet_journal.csv columns", "", "Generated by scripts/bet_journal.py (see its header for conventions).", ""]
    md += [f"- `{k}`: {v}" for k, v in COLUMNS.items()]
    (logs / "bet_journal_COLUMNS.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return summ


def run_journal(raw, led, client, root: Path = ROOT, now: datetime | None = None, replay=True):
    kc = None
    try:
        from kpm.clients.kalshi import KalshiClient
        kc = KalshiClient()
    except Exception:
        kc = None
    rows = build_journal(raw, led, client, root=root, now=now, kc=kc, replay=replay)
    summ = write_outputs(rows, root, now)
    print()
    print(summ)
    return rows


def main():
    try:
        import trade_ledger as tl
        client = tl.make_live_client()
        raw, led, _ = tl.run(client)
        run_journal(raw, led, client)
        return 0
    except Exception as e:
        print(f"bet_journal FAILED: {type(e).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
