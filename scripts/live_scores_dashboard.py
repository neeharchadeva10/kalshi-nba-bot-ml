"""READ-ONLY live NBA total-points dashboard (public Kalshi GETs only, localhost only)."""
import argparse, glob, json, math, os, threading, time
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import requests

BASE = "https://api.elections.kalshi.com/trade-api/v2"
ET = ZoneInfo("America/New_York")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FINAL_STATUSES = {"closed", "complete", "completed", "final", "ended"}
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


# ---------- pure functions ----------
def date_code(d):
    return "%02d%s%02d" % (d.year % 100, MONTHS[d.month - 1], d.day)


def parse_key(event_ticker):
    """'KXNBATOTAL-26OCT21MINMIA' -> ('26OCT21MINMIA','MIN','MIA') or None."""
    pre = "KXNBATOTAL-"
    if not isinstance(event_ticker, str) or not event_ticker.startswith(pre):
        return None
    key = event_ticker[len(pre):]
    if len(key) != 13:
        return None
    return key, key[7:10], key[10:13]


def _rem_seconds(rem):
    try:
        m, s = str(rem).split(":")
        return int(m) * 60 + float(s)
    except Exception:
        return None


def elapsed_minutes(period, remaining):
    try:
        p = int(period)
    except (TypeError, ValueError):
        return None
    secs = _rem_seconds(remaining)
    if p < 1 or secs is None:
        return None
    rem = secs / 60.0
    if p <= 4:
        if rem > 12 or rem < 0:
            return None
        return 12 * (p - 1) + (12 - rem)
    if rem > 5 or rem < 0:
        return None
    return 48 + 5 * (p - 5) + (5 - rem)


def pace_projection(total, elapsed):
    if elapsed is None or total is None or elapsed < 3:
        return None
    if elapsed >= 48:
        return float(total)
    return total * 48.0 / elapsed


def bet_state(side, strike, total, final):
    """Returns (label, points); points = needed (OVER) or room (UNDER)."""
    side = (side or "").upper()
    fl = math.floor(strike)
    if side == "OVER":
        need = max(0, fl + 1 - total)
        if total > strike:
            return "WON (locked)", need
        if final:
            return "LOST", need
        return "LIVE", need
    room = fl - total
    if total > strike:
        return "LOST (locked)", room
    if final:
        return "WON", room
    return "LIVE", room


def market_chance(side, yes_bid, yes_ask):
    try:
        mid = (float(yes_bid) + float(yes_ask)) / 2
    except (TypeError, ValueError):
        return None
    return mid if (side or "").upper() == "OVER" else 1 - mid


def select_pick(rows, game, date_iso, start_iso):
    """Last row for game on the date with ts < start; fallback last row."""
    mine = [r for r in rows if r.get("game") == game and str(r.get("ts", ""))[:10] == date_iso]
    if not mine:
        mine = [r for r in rows if r.get("game") == game]
    if not mine:
        return None
    before = [r for r in mine if start_iso and str(r.get("ts", "")) < start_iso]
    return before[-1] if before else mine[-1]


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def parse_fills(raw, code):
    """Aggregate fills for KXNBATOTAL-<code>* per (ticker, side).
    Kalshi V2 fills: `side` (= `outcome_side`) is the outcome acquired; `action` buy/sell only says which book was hit
    (the bot's NO buys arrive as action=sell, side=no). Every fill is a long leg on its side; scripts/trade_ledger.py
    verified this convention against positions.realized_pnl on 327/327 settled tickers."""
    agg = {}
    fills = (raw or {}).get("fills", {})
    lists = fills.values() if isinstance(fills, dict) else [fills]
    for lst in lists:
        for f in lst or []:
            t = f.get("ticker", "")
            side = f.get("outcome_side") or f.get("side")
            if side not in ("yes", "no") or not t.startswith("KXNBATOTAL-" + code):
                continue
            price = _f(f.get("yes_price_dollars" if side == "yes" else "no_price_dollars"))
            cnt = _f(f.get("count_fp", f.get("count")))
            fee = _f(f.get("fee_cost"))
            a = agg.setdefault((t, side), {"ticker": t, "side": "OVER" if side == "yes" else "UNDER",
                                           "count": 0.0, "cost": 0.0, "fees": 0.0})
            a["count"] += cnt
            a["cost"] += price * cnt
            a["fees"] += fee
    out = []
    for a in agg.values():
        a["entry"] = a["cost"] / a["count"] if a["count"] else 0.0
        a["game"] = a["ticker"].split("-")[1]
        out.append(a)
    return out


def period_label(d, start_et):
    st = (d.get("status") or "").lower()
    p = d.get("period") or 0
    if st in FINAL_STATUSES or d.get("winner"):
        return "Final", "final"
    if not p and not _f(d.get("away_points")) and not _f(d.get("home_points")):
        return "Pre-game " + start_et, "pre"
    lab = ("Q%d" % p) if p <= 4 else ("OT%d" % (p - 4))
    return "%s %s" % (lab, d.get("period_remaining_time") or ""), "live"



# ---------- extension: pure functions ----------
ORD = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}


def quarter_label(d, start_et):
    """(badge text, phase). Prominent quarter label; see tests for each special case."""
    st = (d.get("status") or "").lower()
    lp = (d.get("last_play") or "").lower()
    try:
        p = int(d.get("period") or 0)
    except (TypeError, ValueError):
        p = 0
    rem = d.get("period_remaining_time") or ""
    secs = _rem_seconds(rem)
    if st in FINAL_STATUSES or d.get("winner"):
        return ("Final/OT" if p >= 5 else "Final"), "final"
    if not p and not _f(d.get("away_points")) and not _f(d.get("home_points")):
        return "Tip " + start_et, "pre"
    if "half" in st or "halftime" in lp or "half-time" in lp or (p == 2 and secs == 0):
        return "Halftime", "live"
    if "end" in st and p in (1, 3, 4):
        return "End of " + ORD[p], "live"
    if "end" in st and p == 2:
        return "Halftime", "live"
    if secs == 0 and p in (1, 3):
        return "End of " + ORD[p], "live"
    if secs == 0 and p == 4:
        return "End of 4th", "live"
    left = ("%s left" % rem) if rem else ""
    if p <= 4:
        return ("%s Qtr" % ORD.get(p, str(p)) + (" · " + left if left else "")), "live"
    return ("OT%d" % (p - 4) + (" · " + left if left else "")), "live"


def label_elapsed(label, clock_elapsed):
    """Trust the label over the clock at halftime / end of quarter."""
    if label == "Halftime":
        return 24.0
    if label.startswith("End of "):
        n = {"1st": 1, "3rd": 3, "4th": 4}.get(label[7:])
        if n:
            return 12.0 * n
    return clock_elapsed


def filter_upcoming(upcoming, betkeys):
    """Drop games that already have a fill."""
    return [u for u in upcoming if u["key"] not in betkeys]


def played_text(phase, elapsed):
    if phase == "final":
        return "48 of 48 min played"
    if elapsed is None:
        return ""
    return "%.1f of 48 min played" % min(elapsed, 48.0)


def implied_total(points):
    """points: iterable of (strike, yes_bid, yes_ask). Median total where YES mid crosses 0.50.
    Returns float, '> top strike', '< bottom strike', or None (no valid books)."""
    pts = []
    for s, b, a in points:
        try:
            s, b, a = float(s), float(b), float(a)
        except (TypeError, ValueError):
            continue
        if 0 < b <= a < 1 and a - b <= 0.15:
            pts.append((s, (a + b) / 2))
    pts.sort()
    if not pts:
        return None
    if pts[0][1] < 0.5:
        return "< bottom strike"
    if pts[-1][1] > 0.5:
        return "> top strike"
    for (s1, m1), (s2, m2) in zip(pts, pts[1:]):
        if m1 >= 0.5 >= m2:
            if m1 == m2:
                return s1
            return s1 + (m1 - 0.5) / (m1 - m2) * (s2 - s1)
    return pts[-1][0] if pts[-1][1] == 0.5 else None


def implied_str(v):
    if isinstance(v, (int, float)):
        return "%.1f" % v
    return v if isinstance(v, str) else "n/a"


def side_close(side, mid):
    return mid if side == "OVER" else 1 - mid


def clv_cents(side, close_yes_mid, entry):
    return (side_close(side, close_yes_mid) - entry) * 100


def taker_fee(count, p):
    """0.07*count*P*(1-P) rounded up to $0.0001 (repo convention, matches real fills; ws_sim.py)."""
    return round(math.ceil(0.07 * count * p * (1 - p) / 0.0001 - 1e-6) * 0.0001, 4)


def cashout(side, count, yes_bid, yes_ask, cost, fees):
    try:
        bid, ask = float(yes_bid), float(yes_ask)
    except (TypeError, ValueError):
        return None
    p = bid if side == "OVER" else 1 - ask
    fee = taker_fee(count, p)
    net = count * p - fee
    return {"price": p, "fee": fee, "net": net, "pnl_now": net - cost - fees}


def best_worst(count, cost, fees):
    return count - cost - fees, -(cost + fees)


def ping_time(start_iso):
    st = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    return (st - timedelta(minutes=75)).astimezone(ET)


def fmt_et(dt):
    return dt.strftime("%I:%M %p").lstrip("0")


def on_track(proj, implied):
    if proj is None or not isinstance(implied, (int, float)):
        return "n/a (no market total)"
    gap = proj - implied
    if abs(gap) >= 4:
        return "YES (gap %.1f %s)" % (abs(gap), "over" if gap > 0 else "under")
    return "no (gap %.1f)" % abs(gap)


def order_line(side, strike, limit_c, count=1):
    buy = "YES" if side == "OVER" else "NO"
    return "BUY %s (%s %s) x%d @ %dc LIMIT" % (buy, side, strike, count, int(limit_c))


def decide_outcome(side, res, label, final):
    """(won, awaiting_settlement). Market result wins; else a final game decides via bet_state label."""
    if res in ("yes", "no"):
        return (res == "yes") == (side == "OVER"), False
    if final and label and label.startswith(("WON", "LOST")):
        return label.startswith("WON"), True
    return None, False


BAR_SCALE = 300.0


def bar_window(values=None):
    """Python twin of the page: the points bar is a fixed 0..300 scale."""
    return 0.0, BAR_SCALE


def bar_pos(v):
    """Percent position on the fixed scale, clamped to the bar edges (twin of the page's P())."""
    return min(100.0, max(0.0, v / BAR_SCALE * 100.0))


def fmt_money(x):
    """Same rounding as the page's usdS(): cents, no sign at |x| < 0.005, unicode minus."""
    if x is None:
        return "–"
    if abs(x) < 0.005:
        return "$0.00"
    return ("−$" if x < 0 else "+$") + "%.2f" % abs(x)


def bot_status(logs_dir, now):
    """Read-only: the overs bot's heartbeat file -> {live, placing, age_s} (None if absent or unreadable)."""
    try:
        with open(os.path.join(logs_dir, "early_overs_bot.heartbeat"), encoding="utf-8") as fh:
            h = json.load(fh)
        age = now - datetime.fromisoformat(h["ts_utc"].replace("Z", "+00:00")).timestamp()
        return {"live": bool(h.get("live")), "placing": bool(h.get("placing")), "age_s": round(age)}
    except Exception:  # noqa: BLE001
        return None


def nba_night(now_et):
    """The NBA 'night' a moment belongs to: ET date of (now - 6 h), so late games stay on the previous night."""
    return (now_et - timedelta(hours=6)).date()


def par_now(line, elapsed):
    """Where the total would be right now if the game were scoring exactly at the line's pace."""
    if line is None or elapsed is None:
        return None
    if elapsed >= 48:
        return float(line)
    return float(line) * elapsed / 48.0


def vs_par(total, par):
    return None if par is None or total is None else total - par


def par_text(side, vs):
    """(sentence, colour class) for the user's side."""
    if vs is None:
        return "", "n"
    if abs(vs) <= 1:
        return "right on pace", "n"
    n = int(round(abs(vs)))
    pts = "%d pt%s" % (n, "" if n == 1 else "s")
    if side == "OVER":
        return (pts + " AHEAD of the over pace", "g") if vs > 0 else (pts + " BEHIND the over pace", "r")
    return (pts + " UNDER the line's pace", "g") if vs < 0 else (pts + " OVER the line's pace", "r")


def parse_resting_orders(raw, code):
    """Resting buy orders on KXNBATOTAL-<code>* with remaining > 0."""
    out = []
    orders = (raw or {}).get("orders", {})
    lists = orders.values() if isinstance(orders, dict) else [orders]
    for lst in lists:
        for o in lst or []:
            t = o.get("ticker", "")
            if o.get("action") != "buy" or o.get("status") != "resting" or not t.startswith("KXNBATOTAL-" + code):
                continue
            rem = _f(o.get("remaining_count_fp", o.get("remaining_count")))
            if rem <= 0:
                continue
            side = o.get("side")
            out.append({"ticker": t, "game": t.split("-")[1], "side": "OVER" if side == "yes" else "UNDER",
                        "price": _f(o.get("yes_price_dollars" if side == "yes" else "no_price_dollars")),
                        "remaining": rem, "created": o.get("created_time")})
    return out


def waiting_games(resting, fill_keys):
    """Game keys with a resting order but no fill."""
    return {r["game"] for r in resting} - set(fill_keys)


def key_date(key):
    return date(2000 + int(key[0:2]), MONTHS.index(key[2:5]) + 1, int(key[5:7]))


WINDOW = (date(2026, 10, 21), date(2026, 11, 3))


def scoreboard(fills, results, clvs=(), picked=None):
    """fills: parse_fills output; results: ticker -> 'yes'/'no'. Window filter applied here.
    picked: optional {'YYYY-MM-DD|GAMEKEY': {'side': 'OVER'/'UNDER', ...}} (logs/early_overs_notified.json); when given,
    only fills on a picked game and side count, so other KXNBATOTAL trades (e.g. a lock bot) stay out of the record.
    Also returns the record per night (by_night) and per side (by_side) over the whole test window."""
    won = lost = pend = 0
    pnl = staked = settled_staked = 0.0
    be_num = be_den = 0.0
    nights, sides = {}, {"OVER": [0, 0], "UNDER": [0, 0]}
    for f in fills:
        try:
            d = key_date(f["game"])
        except (ValueError, IndexError):
            continue
        if not (WINDOW[0] <= d <= WINDOW[1]):
            continue
        if picked is not None and (picked.get(d.isoformat() + "|" + f["game"]) or {}).get("side") != f["side"]:
            continue
        nt = nights.setdefault(d.isoformat(), {"date": d.isoformat(), "won": 0, "lost": 0, "pending": 0, "pnl": 0.0})
        staked += f["cost"] + f["fees"]
        r = results.get(f["ticker"])
        if r not in ("yes", "no"):
            pend += 1
            nt["pending"] += 1
            continue
        w = (r == "yes") == (f["side"] == "OVER")
        won, lost = won + int(w), lost + int(not w)
        p = (f["count"] if w else 0) - f["cost"] - f["fees"]
        pnl += p
        nt["won" if w else "lost"] += 1
        nt["pnl"] += p
        sides.setdefault(f["side"], [0, 0])[0 if w else 1] += 1
        settled_staked += f["cost"] + f["fees"]
        be_num += f["cost"] + f["fees"]
        be_den += f["count"]
    n = won + lost
    clvs = list(clvs)
    return {"won": won, "lost": lost, "pending": pend, "pnl": pnl, "staked": staked,
            "roi": pnl / settled_staked if settled_staked else None,
            "win_rate": won / n if n else None, "breakeven": be_num / be_den if be_den else None,
            "avg_clv": sum(clvs) / len(clvs) if clvs else None, "n_clv": len(clvs),
            "by_night": [nights[k] for k in sorted(nights)], "by_side": sides,
            "window": [WINDOW[0].isoformat(), WINDOW[1].isoformat()]}


def formula_vs_final(proj, strike, total, pick_side):
    if proj is None or strike is None:
        return None
    fe, le = total - proj, total - strike
    closer = "formula" if abs(fe) < abs(le) else ("line" if abs(le) < abs(fe) else "tie")
    won = None
    if pick_side in ("OVER", "UNDER"):
        won = (total > strike) if pick_side == "OVER" else (total < strike)
    return {"formula_err": fe, "line_err": le, "closer": closer, "pick_won": won}


SM_SUMMARY = os.path.join(ROOT, "logs", "score_model", "summary.json")
SM_REFRESH_S = 60


def sm_load(path):
    """Read-only read of the #59 tracker's summary.json; None if missing or unreadable."""
    try:
        with open(path, encoding="utf-8") as fh:
            j = json.load(fh)
        return j if isinstance(j, dict) else None
    except (OSError, ValueError):
        return None


TW_SUMMARY = os.path.join(ROOT, "logs", "trades", "summary.json")
TW_REFRESH_S = 60
TW_TYPES = ("TRADE", "SIGNING", "WAIVER", "TWO_WAY_10_DAY", "POSSIBLE_MOVE")
TW_TIERS = ("star", "starter", "bench", "deep", "none")


def trade_wire_panel(summary, now_et=None, n=8):
    """Latest n alerts from the trade watcher's summary.json, reduced to plain fields (the page escapes the text).
    None when the file is missing / empty so the panel stays hidden."""
    if not isinstance(summary, dict) or not isinstance(summary.get("alerts"), list):
        return None
    rows = []
    for a in summary["alerts"]:
        if not isinstance(a, dict) or not isinstance(a.get("headline"), str):
            continue
        try:
            when = datetime.fromisoformat(str(a.get("ts_utc", "")).replace("Z", "+00:00")).astimezone(ET)
            t = when.strftime("%b %d %H:%M").replace(" 0", " ")
        except ValueError:
            t, when = "?", None
        rows.append({"t": t, "_w": when.timestamp() if when else 0, "type": a.get("type") if a.get("type") in TW_TYPES else "POSSIBLE_MOVE",
                     "headline": a["headline"][:200], "tier": a.get("tier") if a.get("tier") in TW_TIERS else "none",
                     "veteran": bool(a.get("veteran")), "confirmed": str(a.get("status", "")).startswith("confirmed")})
    rows.sort(key=lambda r: r["_w"], reverse=True)
    for r in rows:
        del r["_w"]
    errs = sorted(k for k, v in (summary.get("feeds") or {}).items() if isinstance(v, dict) and not v.get("ok", True))
    return {"alerts": rows[:n], "updated": str(summary.get("updated_utc", ""))[:20], "feed_errors": errs}


IW_SUMMARY = os.path.join(ROOT, "logs", "injuries", "summary.json")
IW_REFRESH_S = 60
IW_TYPES = ("NEW", "STATUS_CHANGE", "REMOVED", "NEWS_INJURY")
IW_STATUSES = ("Out", "Doubtful", "Questionable", "Probable", "Available")


def injury_wire_panel(summary, n=10):
    """Latest n alerts from the injury watcher's summary.json, reduced to plain fields (the page escapes the text).
    None when the file is missing / not a dict so the panel stays hidden."""
    if not isinstance(summary, dict) or not isinstance(summary.get("alerts"), list):
        return None
    rows = []
    for a in summary["alerts"]:
        if not isinstance(a, dict) or not isinstance(a.get("type"), str):
            continue
        try:
            when = datetime.fromisoformat(str(a.get("ts_utc", "")).replace("Z", "+00:00")).astimezone(ET)
            t = when.strftime("%b %d %H:%M").replace(" 0", " ")
        except ValueError:
            t, when = "?", None

        def st(x):
            return x if x in IW_STATUSES else None
        official = a.get("source") == "OFFICIAL" and a.get("official") is True
        rows.append({"t": t, "_w": when.timestamp() if when else 0, "type": a["type"] if a["type"] in IW_TYPES else "NEW",
                     "official": official, "team": str(a.get("team") or "")[:4], "opponent": str(a.get("opponent") or "")[:4],
                     "player": str(a.get("player") or "")[:60], "old": st(a.get("old_status")), "new": st(a.get("new_status")),
                     "headline": str(a.get("headline") or "")[:200] if not official else "",
                     "tier": a.get("tier") if a.get("tier") in TW_TIERS else "none", "veteran": bool(a.get("veteran")),
                     "late": bool(a.get("late")), "bot": bool(a.get("bot_game"))})
    rows.sort(key=lambda r: r["_w"], reverse=True)
    for r in rows:
        del r["_w"]
    errs = sorted(k for k, v in (summary.get("feeds") or {}).items() if isinstance(v, dict) and not v.get("ok", True))
    lo = summary.get("last_official") if isinstance(summary.get("last_official"), dict) else {}
    return {"alerts": rows[:n], "updated": str(summary.get("updated_utc", ""))[:20], "feed_errors": errs,
            "last_report": str(lo.get("creation_et") or "")[:24]}


PL_SUMMARY = os.path.join(ROOT, "logs", "paper_league", "summary.json")
PL_REFRESH_S = 60
PL_NOTE = ("Verdict only after 4 weeks and 100+ bets per rule; compare with the always-under/always-over/coin-flip "
           "controls.")


def paper_league_panel(summary):
    """PAPER LEAGUE panel data from logs/paper_league/summary.json (#67): one row per rule with W-L, open, net $, mean
    cents per settled bet and n/100 progress. None when the file is missing / has no rules so the panel stays hidden."""
    if not isinstance(summary, dict) or not isinstance(summary.get("rules"), dict):
        return None
    rows = []
    order = summary.get("order") if isinstance(summary.get("order"), list) else list(summary["rules"])
    for rid in order:
        r = summary["rules"].get(rid)
        if not isinstance(r, dict):
            continue
        mean = r.get("mean_net")
        rows.append({"id": str(rid)[:12], "label": str(r.get("label") or "")[:40], "sport": str(r.get("sport") or "")[:4],
                     "control": bool(r.get("control")), "n": int(_num(r.get("n")) or 0), "won": int(_num(r.get("won")) or 0),
                     "lost": int(_num(r.get("lost")) or 0), "open": int(_num(r.get("open")) or 0),
                     "net": _num(r.get("net")) or 0.0, "mean_c": None if _num(mean) is None else round(mean * 100, 1),
                     "need": int(_num(summary.get("bets_needed")) or 100)})
    return {"rows": rows, "note": PL_NOTE, "updated": str(summary.get("updated_utc", ""))[:20]}


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def score_model_panel(summary, gout, date_iso):
    """Compact SCORE MODEL panel data from the tracker's summary and this night's games (gout: key -> game dict).
    rows: one per LOCKED game of tonight; cum: cumulative accuracy (model E1 vs the user's formula B1)."""
    if not isinstance(summary, dict):
        return None
    m = summary.get("metrics") or {}
    e1, b1 = m.get("E1") or {}, m.get("B1") or {}
    cum = {"n": int(m.get("n_games") or 0), "model_mae": _num(e1.get("total_mae")), "formula_mae": _num(b1.get("total_mae")),
           "model_within10": _num(e1.get("pct_within_10")), "formula_within10": _num(b1.get("pct_within_10"))}
    rows = []
    t = summary.get("tonight") or {}
    if t.get("night") == date_iso:
        for r in t.get("rows") or []:
            if not r.get("locked") or _num(r.get("e1_total")) is None:
                continue
            g = (gout or {}).get(r.get("key")) or {}
            final = live = None
            if g.get("phase") == "final":
                final = g.get("total")
            elif _num(r.get("final_total")) is not None:
                final = r["final_total"]
            elif g.get("phase") == "live":
                live = g.get("total")
            row = {"key": r["key"], "away": r.get("away"), "home": r.get("home"), "model_away": round(r["e1_away"]),
                   "model_home": round(r["e1_home"]), "model_total": round(r["e1_total"]),
                   "formula_total": round(r["b1_total"]) if _num(r.get("b1_total")) is not None else None,
                   "state": "final" if final is not None else "live" if live is not None else "pre",
                   "total": final if final is not None else live, "off": None, "ok": None}
            if final is not None:
                row["off"] = round(abs(r["e1_total"] - final), 1)
                row["ok"] = bool(row["off"] <= 10)
            rows.append(row)
    return {"rows": rows, "cum": cum, "updated_utc": summary.get("updated_utc")}


def last_close(candles):
    """(bid, ask) from the last candle having both; handles 'close_dollars' and 'close'."""
    def val(c, k):
        o = c.get(k) or {}
        v = o.get("close_dollars", o.get("close"))
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v / 100 if v > 1 else v
    for c in reversed(candles or []):
        b, a = val(c, "yes_bid"), val(c, "yes_ask")
        if b is not None and a is not None:
            return b, a
    return None


STOP_WORDS = ("timeout", "foul", "free throw", "end of", "end period", "halftime", "jump ball", "violation",
              "review", "challenge", "injury", "substitution", "ejection", "technical")


def poll_interval(phase):
    """Seconds between score polls: live 2 s, pre-game 30 s, final never (None); unknown 2 s."""
    if phase == "final":
        return None
    return 30.0 if phase == "pre" else 2.0


def clock_running(d):
    """True only if the game clock is plausibly running (used for the estimated ticking clock)."""
    if (d.get("status") or "").lower() != "inprogress":
        return False
    if d.get("last_event_is_timeout"):
        return False
    lp = (d.get("last_play") or "").lower()
    if any(w in lp for w in STOP_WORDS):
        return False
    secs = _rem_seconds(d.get("period_remaining_time"))
    if secs is None or secs == 0:
        return False
    try:
        p = int(d.get("period") or 0)
    except (TypeError, ValueError):
        return False
    if p >= 4 and secs <= 120 and "makes" in lp:
        return False
    return True


def choose_anchor(d, now, first_seen):
    """Epoch at which the feed clock value was true: the feed's last-event ts if sane, else first-seen time."""
    try:
        ts = float(d.get("last_event_created_ts"))
    except (TypeError, ValueError):
        ts = None
    if ts and ts <= now + 5 and now - ts <= 900:
        return ts
    return first_seen


# ---------- HTTP layer ----------
class Kalshi:
    def __init__(self, session=None, min_interval=0.15, backoff_sleep=5.0):
        self.s = session or requests.Session()
        self.mi, self._last, self._lk = min_interval, 0.0, threading.Lock()
        self.backoff_sleep, self.slow_until, self._pw = backoff_sleep, 0.0, 0

    def cur_interval(self):
        return self.mi * 2 if time.time() < self.slow_until else self.mi

    def get(self, path, params=None, prio=False):
        if prio:
            self._pw += 1
        else:  # market polls yield briefly to waiting score fetches
            t0 = time.time()
            while self._pw > 0 and time.time() - t0 < 1.0:
                time.sleep(0.02)
        try:
            with self._lk:  # global throttle
                w = self._last + self.cur_interval() - time.time()
                if w > 0:
                    time.sleep(w)
                self._last = time.time()
        finally:
            if prio:
                self._pw -= 1
        r = self.s.get(BASE + path, params=params, timeout=10)
        if getattr(r, "status_code", 200) == 429:
            self.slow_until = time.time() + 60
            time.sleep(self.backoff_sleep)
            raise requests.HTTPError("rate limited")
        r.raise_for_status()
        return r.json()


def scan_games(api, d):
    code = date_code(d)
    start = datetime(d.year, d.month, d.day, 12, tzinfo=ET).astimezone(timezone.utc)
    params = {"category": "Sports", "minimum_start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 200}
    games = {}
    for _ in range(30):
        j = api.get("/milestones", params)
        ms = j.get("milestones") or []
        for m in ms:
            if m.get("type") != "basketball_game":
                continue
            for et in m.get("related_event_tickers") or []:
                if et.startswith("KXNBATOTAL-" + code):
                    k = parse_key(et)
                    if k:
                        games[k[0]] = {"key": k[0], "away": k[1], "home": k[2], "id": m.get("id"),
                                       "title": m.get("title"), "start": m.get("start_date")}
        cur = j.get("cursor")
        if not cur or not ms:
            break
        params = dict(params, cursor=cur)
    return games


def _ts(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


# ---------- state ----------
class Dash:
    def __init__(self, d, api=None, logs=None, sync=False):
        self.d, self.code, self.date_iso = d, date_code(d), d.isoformat()
        self.sync, self._client, self.synced, self.sync_err = sync, None, None, None
        self.api = api or Kalshi()
        self.logs = logs or os.path.join(ROOT, "logs")
        self.lock = threading.Lock()
        self.games, self.scores, self.markets, self.fills = {}, {}, {}, []
        self.fills_all, self.cash, self.pulled, self.settle = [], {}, None, {}
        self.resting = []
        self.notified, self.picks = {}, []
        self.evmk, self.clv, self.clv_try, self.results = {}, {}, {}, {}
        self.seen, self.fetched, self.next_due = {}, {}, {}
        self.traj_last, self.traj_final, self._traj_loaded = {}, set(), False
        self.err, self.ts = {}, {}
        self.alive = True
        self.sm_path, self._sm = SM_SUMMARY, (0.0, None)
        self.tw_path, self._tw = TW_SUMMARY, (0.0, None)
        self.iw_path, self._iw = IW_SUMMARY, (0.0, None)
        self.pl_path, self._pl = PL_SUMMARY, (0.0, None)

    def pl_panel(self, now=None):
        """Paper-league panel from logs/paper_league/summary.json, re-read at most every PL_REFRESH_S seconds (file read only)."""
        now = time.time() if now is None else now
        if now - self._pl[0] >= PL_REFRESH_S:
            self._pl = (now, paper_league_panel(sm_load(self.pl_path)))
        return self._pl[1]

    def tw_panel(self, now=None):
        """Trade-wire panel from logs/trades/summary.json, re-read at most every TW_REFRESH_S seconds (file read only)."""
        now = time.time() if now is None else now
        if now - self._tw[0] >= TW_REFRESH_S:
            self._tw = (now, trade_wire_panel(sm_load(self.tw_path)))
        return self._tw[1]

    def iw_panel(self, now=None):
        """Injury-wire panel from logs/injuries/summary.json, re-read at most every IW_REFRESH_S seconds (file read only)."""
        now = time.time() if now is None else now
        if now - self._iw[0] >= IW_REFRESH_S:
            self._iw = (now, injury_wire_panel(sm_load(self.iw_path)))
        return self._iw[1]

    def sm_summary(self, now=None):
        """The tracker's summary.json, re-read at most every SM_REFRESH_S seconds (file read only, no network)."""
        now = time.time() if now is None else now
        if now - self._sm[0] >= SM_REFRESH_S:
            self._sm = (now, sm_load(self.sm_path))
        return self._sm[1]

    def stop(self):
        """Ends this night's polling loops (used when the server rolls over to the next night)."""
        self.alive = False

    def _mark(self, name, fn):
        try:
            fn()
            self.err.pop(name, None)
            self.ts[name] = time.time()
        except Exception as e:
            self.err[name] = type(e).__name__

    def do_games(self):
        g = scan_games(self.api, self.d)
        with self.lock:
            self.games = g

    def do_scores(self):
        for g in list(self.games.values()):
            key = g["key"]
            now = time.time()
            if self.next_due.get(key, 0) > now:
                continue
            det0 = self.scores.get(key)
            phase = quarter_label(det0, "")[1] if det0 else None
            iv = poll_interval(phase)
            self.next_due[key] = float("inf") if iv is None else now + iv
            if iv is None and det0:
                continue
            try:
                j = self.api.get("/live_data/basketball_game/milestone/%s" % g["id"], prio=True)
                det = (j.get("live_data") or {}).get("details") or {}
                t = time.time()
                pair = (det.get("period"), det.get("period_remaining_time"))
                with self.lock:
                    self.scores[key] = det
                    self.fetched[key] = t
                    if self.seen.get(key, (None,))[0] != pair:
                        self.seen[key] = (pair, t)
                ph = quarter_label(det, "")[1]
                nv = poll_interval(ph)
                self.next_due[key] = float("inf") if nv is None else t + nv
                self.err.pop("score_" + key, None)
            except Exception as e:
                self.err["score_" + key] = type(e).__name__
                self.next_due[key] = time.time() + 2

    def traj_path(self):
        return os.path.join(self.logs, "live_dashboard", self.date_iso + ".jsonl")

    def do_traj(self, now=None):
        """Append in-game trajectory rows (bet games only): live at most every 30 s per game, plus one final row."""
        now = time.time() if now is None else now
        path = self.traj_path()
        if not self._traj_loaded:
            self._traj_loaded = True
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            r = json.loads(line)
                        except ValueError:
                            continue
                        if r.get("phase") == "final":
                            self.traj_final.add(r.get("game"))
        st = self.build()
        due = {}
        for b in st["bets"]:
            g = b["game_info"]
            key = g["key"]
            if key not in due:
                if g["phase"] == "live" and now - self.traj_last.get(key, -1e9) >= 30:
                    due[key] = True
                elif g["phase"] == "final" and key not in self.traj_final:
                    due[key] = False
                else:
                    due[key] = None
        rows = []
        for b in st["bets"]:
            g = b["game_info"]
            key = g["key"]
            if due.get(key) is None:
                continue
            m = self.markets.get(b["ticker"], {})
            det = self.scores.get(key) or {}
            imp = g.get("implied")
            rows.append({
                "ts_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(), "game": key, "ticker": b["ticker"],
                "side": b["side"], "strike": b["strike"], "period": det.get("period"),
                "clock": det.get("period_remaining_time"), "minutes_played": g.get("elapsed"),
                "away_pts": g["away_pts"], "home_pts": g["home_pts"], "total": g["total"], "par": b.get("par"),
                "vs_par": b.get("vs_par"), "points_left": b.get("points"), "market_implied_total": imp,
                "chance": b.get("chance"), "yes_bid": _f(m.get("yes_bid_dollars"), None),
                "yes_ask": _f(m.get("yes_ask_dollars"), None), "proj": g.get("proj"), "phase": g["phase"]})
        if not rows:
            return
        for key, live in due.items():
            if live is True:
                self.traj_last[key] = now
            elif live is False:
                self.traj_final.add(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def do_event_markets(self):
        for key in list(self.games):
            det = self.scores.get(key) or {}
            if quarter_label(det, "")[1] == "final" and key in self.evmk:
                continue  # frozen last value
            try:
                ms = self.api.get("/markets", {"event_ticker": "KXNBATOTAL-" + key, "limit": 100}).get("markets") or []
                rows = [(m.get("ticker"), m.get("floor_strike"), m.get("yes_bid_dollars"), m.get("yes_ask_dollars"))
                        for m in ms if m.get("floor_strike") is not None]
                if rows or key not in self.evmk:
                    with self.lock:
                        self.evmk[key] = rows
                self.err.pop("ev_" + key, None)
            except Exception as e:
                self.err["ev_" + key] = type(e).__name__

    def pull_live(self):
        """READ-ONLY sync with the Kalshi account: scripts/trade_ledger.py's guarded client (GET-only transport,
        no order methods) and its pull_all (cash, fills, positions, orders, settlements on every shard)."""
        import trade_ledger as TL
        if self._client is None:
            os.environ.setdefault("KALSHI_ENV", "prod")
            self._client = TL.make_live_client()
        return TL.pull_all(self._client)

    def do_ledger(self):
        raw = None
        if self.sync:
            try:
                raw = self.pull_live()
                self.synced, self.sync_err = time.time(), None
            except Exception as e:  # noqa: BLE001
                self.sync_err, self._client = type(e).__name__, None
        files = sorted(glob.glob(os.path.join(self.logs, "ledger_raw", "*.json")))
        fills_all, cash, pulled, settle, resting = [], {}, None, {}, []
        if raw is None and files:
            with open(files[-1], encoding="utf-8") as fh:
                raw = json.load(fh)
        if raw is not None:
            fills_all = parse_fills(raw, "")
            cash, pulled = raw.get("cash") or {}, raw.get("pulled_utc")
            resting = parse_resting_orders(raw, self.code)
            for s in raw.get("settlements") or []:
                if s.get("market_result") in ("yes", "no"):
                    settle[s.get("ticker")] = s["market_result"]
        notified = {}
        p = os.path.join(self.logs, "early_overs_notified.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                notified = json.load(fh)
        picks = []
        p = os.path.join(self.logs, "early_overs_picks.jsonl")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        picks.append(json.loads(line))
                    except ValueError:
                        pass
        with self.lock:
            self.fills_all, self.cash, self.pulled, self.settle = fills_all, cash, pulled, settle
            self.resting = resting
            self.fills = [f for f in fills_all if f["game"].startswith(self.code)]
            self.notified, self.picks = notified, picks

    def do_markets(self):
        for t in ({f["ticker"] for f in self.fills} | {r["ticker"] for r in self.resting}) - set(self.results):  # stop once settled
            try:
                m = self.api.get("/markets/%s" % t).get("market") or {}
                with self.lock:
                    self.markets[t] = m
                    if m.get("result") in ("yes", "no"):
                        self.results[t] = m["result"]
                self.err.pop("mkt_" + t, None)
            except Exception as e:
                self.err["mkt_" + t] = type(e).__name__

    def do_sb_markets(self):
        todo = []
        for f in self.fills_all:
            try:
                if not (WINDOW[0] <= key_date(f["game"]) <= WINDOW[1]) or f["game"].startswith(self.code):
                    continue
            except (ValueError, IndexError):
                continue
            if f["ticker"] not in self.settle and f["ticker"] not in self.results:
                todo.append(f["ticker"])
        for t in set(todo):
            m = self.api.get("/markets/%s" % t).get("market") or {}
            if m.get("result") in ("yes", "no"):
                with self.lock:
                    self.results[t] = m["result"]

    def do_clv(self):
        now = time.time()
        for f in list(self.fills):
            t, key = f["ticker"], f["game"]
            g = self.games.get(key)
            if t in self.clv or not g or not g.get("start"):
                continue
            tip = int(_ts(g["start"]))
            if now < tip + 20 or now - self.clv_try.get(t, 0) < 120:
                continue
            self.clv_try[t] = now
            c = self.api.get("/series/KXNBATOTAL/markets/%s/candlesticks" % t,
                             {"start_ts": tip - 600, "end_ts": tip, "period_interval": 1})
            lc = last_close(c.get("candlesticks"))
            if lc is None:
                with self.lock:
                    self.clv[t] = {"na": "no candles in the 10 min before tip"}
                continue
            mid = (lc[0] + lc[1]) / 2
            m = self.markets.get(t, {})
            main = m.get("floor_strike")
            pts = []
            for (tk, st, _b, _a) in self.evmk.get(key, []):
                if main is not None and abs(float(st) - float(main)) > 16:
                    continue
                cc = self.api.get("/series/KXNBATOTAL/markets/%s/candlesticks" % tk,
                                  {"start_ts": tip - 600, "end_ts": tip, "period_interval": 1})
                l2 = last_close(cc.get("candlesticks"))
                if l2:
                    pts.append((st, l2[0], l2[1]))
            with self.lock:
                self.clv[t] = {"mid": mid, "line_tip": implied_total(pts) if pts else None}

    def build(self):
        with self.lock:
            games, scores, mk = dict(self.games), dict(self.scores), dict(self.markets)
            fills, fills_all = list(self.fills), list(self.fills_all)
            resting = list(self.resting)
            seen, fetched = dict(self.seen), dict(self.fetched)
            notified, picks = dict(self.notified), list(self.picks)
            evmk, clv = dict(self.evmk), dict(self.clv)
            results = dict(self.settle)
            results.update(self.results)
            cash, pulled = dict(self.cash), self.pulled
            err, ts = dict(self.err), dict(self.ts)
        for t, m in mk.items():
            if m.get("result") in ("yes", "no"):
                results[t] = m["result"]
        now = time.time()
        gout = {}
        for key, g in games.items():
            det = scores.get(key) or {}
            try:
                start_et = fmt_et(datetime.fromisoformat(g["start"].replace("Z", "+00:00")).astimezone(ET))
            except Exception:
                start_et = "?"
            label, phase = quarter_label(det, start_et)
            a, h = int(_f(det.get("away_points"))), int(_f(det.get("home_points")))
            total = a + h
            el = 48.0 if phase == "final" else label_elapsed(
                label, elapsed_minutes(det.get("period"), det.get("period_remaining_time")))
            pace = pace_projection(total, el) if phase != "pre" else None
            pk = select_pick(picks, key, self.date_iso, g["start"])
            imp = implied_total([(s, b, a_) for (_t, s, b, a_) in evmk.get(key, [])]) if key in evmk else None
            poss = det.get("possession")
            pk_ts = None
            if pk and pk.get("ts"):
                try:
                    pk_ts = fmt_et(datetime.fromisoformat(pk["ts"]).astimezone(ET))
                except ValueError:
                    pass
            gout[key] = {"key": key, "away": g["away"], "home": g["home"], "away_pts": a, "home_pts": h,
                         "total": total, "clock": label, "phase": phase, "start": g["start"], "start_et": start_et,
                         "elapsed": el, "played": played_text(phase, el), "pace": pace, "has_score": bool(det),
                         "par": par_now(pk.get("strike") if pk else None, el),
                         "clock_secs": _rem_seconds(det.get("period_remaining_time")) if det else None,
                         "anchor_ts": choose_anchor(det, now, seen.get(key, (None, now))[1]) if det else None,
                         "running": bool(phase == "live" and " \u00b7 " in label and clock_running(det)),
                         "clock_prefix": label.split(" \u00b7 ")[0] if (phase == "live" and " \u00b7 " in label) else None,
                         "fetch_age": (now - fetched[key]) if key in fetched else None,
                         "implied": imp, "implied_str": implied_str(imp),
                         "ball": g["away"] if poss == "away" else g["home"] if poss == "home" else None,
                         "last_play": det.get("last_play") or "",
                         "strike": pk.get("strike") if pk else None, "proj": pk.get("proj") if pk else None,
                         "gap": pk.get("proj_minus_strike") if pk else None, "pick_ts_et": pk_ts,
                         "pick_side": pk.get("side") if pk else None, "pick_bet": pk.get("bet") if pk else None,
                         "pinged": (self.date_iso + "|" + key) in notified}
        bets = []
        eff = {}
        tc = tv = tu = 0.0
        w = l = 0
        night = {"settled_pnl": 0.0, "open_mark": 0.0, "open_best": 0.0, "open_worst": 0.0, "cashout_pnl": 0.0}
        betkeys = set()
        clv_vals = []
        for f in fills:
            g = gout.get(f["game"])
            if not g:
                continue
            betkeys.add(f["game"])
            m = mk.get(f["ticker"], {})
            strike = m.get("floor_strike")
            if strike is None:
                try:
                    strike = float(notified.get(self.date_iso + "|" + f["game"], {}).get("strike"))
                except (TypeError, ValueError):
                    strike = g["strike"]
            res = results.get(f["ticker"], "")
            final = g["phase"] == "final"
            label, pts = (None, None)
            if strike is not None:
                label, pts = bet_state(f["side"], float(strike), g["total"], final)
            ch = market_chance(f["side"], m.get("yes_bid_dollars"), m.get("yes_ask_dollars"))
            cost = f["cost"]
            best, worst = best_worst(f["count"], cost, f["fees"])
            co = None
            won, awaiting = decide_outcome(f["side"], res, label, final)
            wide = False
            if awaiting:
                eff[f["ticker"]] = "yes" if won == (f["side"] == "OVER") else "no"
            if won is not None:
                ch = 1.0 if won else 0.0
                value = f["count"] * ch
                w, l = w + int(won), l + int(not won)
                settled = "WON" if won else "LOST"
                unreal = value - cost - f["fees"]
                night["settled_pnl"] += unreal
                best = worst = unreal
            else:
                try:
                    wide = float(m.get("yes_ask_dollars")) - float(m.get("yes_bid_dollars")) > 0.30
                except (TypeError, ValueError):
                    wide = False
                value = ch * f["count"] if ch is not None else None
                settled = None
                unreal = value - cost - f["fees"] if value is not None else None
                night["open_mark"] += unreal or 0
                night["open_best"] += best
                night["open_worst"] += worst
                co = cashout(f["side"], f["count"], m.get("yes_bid_dollars"), m.get("yes_ask_dollars"), cost, f["fees"])
                night["cashout_pnl"] += co["pnl_now"] if co else 0
            imp = g["implied"]
            ref = imp if isinstance(imp, (int, float)) else g["pace"]
            mtxt = None
            if isinstance(imp, (int, float)) and strike is not None:
                d_ = imp - float(strike)
                mtxt = "market expects %.1f → %s by %.1f" % (imp, "over" if d_ > 0 else "under", abs(d_))
            elif isinstance(imp, str):
                mtxt = "market expects " + imp
            if ref is not None and strike is not None:
                diff = ref - float(strike)
                good = diff > 0 if f["side"] == "OVER" else diff < 0
            else:
                diff, good = None, None
            if settled:
                badge = settled
            elif g["phase"] == "pre":
                badge = "PRE-GAME"
            elif label and label.startswith(("WON", "LOST")):
                badge = label
            elif good is None:
                badge = "WAITING"
            else:
                badge = "WINNING ON PACE" if good else "BEHIND ON PACE"
            c = clv.get(f["ticker"])
            if c is None:
                ctxt = "closes at tip" if g["phase"] == "pre" else "pending fetch"
                cv = None
            elif "na" in c:
                ctxt, cv = "n/a (%s)" % c["na"], None
            else:
                cv = clv_cents(f["side"], c["mid"], f["entry"])
                clv_vals.append(cv)
                ctxt = "CLV %+.1fc (entry %.0fc, close %.1fc)" % (cv, f["entry"] * 100, side_close(f["side"], c["mid"]) * 100)
                ctxt += " · line at ping %s, market total at tip %s" % (f2(g["strike"]), implied_str(c.get("line_tip")))
            tc += cost + f["fees"]
            tv += value or 0
            tu += unreal or 0
            par = par_now(float(strike), g["elapsed"]) if strike is not None else None
            vp = vs_par(g["total"], par)
            ptxt, pcol = par_text(f["side"], vp)
            bets.append(dict(f, par=par, vs_par=vp, par_text=ptxt, par_color=pcol, strike=strike, state=label, points=pts, chance=ch, value=value, unrealized=unreal,
                             market_status=m.get("status"), result=res, chance_wide=wide, awaiting_settlement=awaiting, pace_diff=diff, badge=badge, game_info=g,
                             market_txt=mtxt, clv_cents=cv, clv_txt=ctxt, cashout=co, best=best, worst=worst))
        bets.sort(key=lambda b: b["game_info"]["start"] or "")
        wkeys = waiting_games(resting, betkeys)
        waiting = []
        for r in resting:
            g = gout.get(r["game"])
            if not g or r["game"] not in wkeys:
                continue
            m = mk.get(r["ticker"], {})
            strike = m.get("floor_strike")
            if strike is None:
                try:
                    strike = float(notified.get(self.date_iso + "|" + r["game"], {}).get("strike"))
                except (TypeError, ValueError):
                    strike = g["strike"]
            try:
                cancel_by = fmt_et(datetime.fromisoformat(g["start"].replace("Z", "+00:00")).astimezone(ET)
                                   - timedelta(minutes=15))
            except Exception:
                cancel_by = "?"
            waiting.append(dict(r, strike=strike, badge="ORDER WAITING", game_info=g, cancel_by=cancel_by,
                                note="not filled yet \u2014 cancel by %s" % cancel_by))
        waiting.sort(key=lambda w: w["game_info"]["start"] or "")
        rest = [g for k, g in gout.items() if k not in betkeys and k not in wkeys]
        others = sorted((g for g in rest if g["phase"] != "final"), key=lambda g: g["start"] or "")
        finished = []
        for g in sorted((g for g in gout.values() if g["phase"] == "final"), key=lambda g: g["start"] or ""):
            fv = formula_vs_final(g["proj"], g["strike"], g["total"], g["pick_side"])
            finished.append(dict(g, fvf=fv, bet=g["key"] in betkeys))
        scored = [x for x in finished if x["fvf"]]
        fcl = sum(1 for x in scored if x["fvf"]["closer"] == "formula")
        upcoming = []
        for g in sorted((g for g in gout.values() if g["phase"] == "pre"), key=lambda g: g["start"] or ""):
            n = notified.get(self.date_iso + "|" + g["key"])
            u = {"key": g["key"], "away": g["away"], "home": g["home"], "start_et": g["start_et"],
                 "ping_et": fmt_et(ping_time(g["start"])) if g["start"] else "?", "pinged_at": None, "order": None,
                 "on_track": on_track(g["proj"], g["implied"]), "implied_str": g["implied_str"],
                 "proj": g["proj"], "last_gap": g["gap"], "last_gap_et": g["pick_ts_et"]}
            if n:
                try:
                    u["pinged_at"] = fmt_et(datetime.fromisoformat(n["sent"]).astimezone(ET))
                except Exception:
                    u["pinged_at"] = "?"
                try:
                    u["order"] = order_line(n["side"], n["strike"], n["limit_c"])
                except Exception:
                    u["order"] = None
            upcoming.append(u)
        upcoming = filter_upcoming(upcoming, set(betkeys) | wkeys)
        cash_total = sum(_f(v) for v in cash.values()) if cash else None
        pulled_et = None
        if pulled:
            try:
                pulled_et = datetime.fromisoformat(pulled).astimezone(ET).strftime("%H:%M")
            except ValueError:
                pass
        sb = scoreboard(fills_all, dict(eff, **results), clv_vals,   # official results override score-based outcomes
                        picked=notified or None)                      # only the strategy's own picks count
        sb["awaiting"] = len(eff)
        return {"build": BUILD_ID, "server_ts": now, "date": self.date_iso, "now_et": datetime.now(ET).strftime("%H:%M:%S"), "bets": bets,
                "others": others, "waiting": waiting, "finished": finished, "upcoming": upcoming, "n_games": len(gout),
                "formula_closer": [fcl, len(scored)],
                "cash": {"total": cash_total, "asof_et": pulled_et, "shards": cash},
                "night": night, "scoreboard": sb,
                "totals": {"n": len(bets), "cost": tc, "value": tv, "unrealized": tu, "won": w, "lost": l},
                "score_model": score_model_panel(self.sm_summary(now), gout, self.date_iso),
                "trade_wire": self.tw_panel(now),
                "injury_wire": self.iw_panel(now),
                "paper_league": self.pl_panel(now),
                "errors": err, "age_s": {k: round(now - v, 1) for k, v in ts.items()},
                "bot": bot_status(self.logs, now),
                "sync": {"on": self.sync, "age_s": None if self.synced is None else round(now - self.synced),
                         "error": self.sync_err}}

    def start(self):
        def loop(name, fn, every):
            def run():
                while self.alive:
                    self._mark(name, fn)
                    time.sleep(every)
            threading.Thread(target=run, daemon=True).start()
        self._mark("ledger", self.do_ledger)
        loop("games", self.do_games, 600)
        loop("ledger", self.do_ledger, 30 if self.sync else 60)
        loop("scores", self.do_scores, 0.5)
        loop("traj", self.do_traj, 5)
        loop("markets", self.do_markets, 15)
        loop("event_markets", self.do_event_markets, 20)
        loop("clv", self.do_clv, 30)
        loop("sb_markets", self.do_sb_markets, 120)


def f2(x):
    return "n/a" if x is None else "%.1f" % x


# ---------- web ----------
PAGE = r"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>NBA totals live</title><style>
:root{--bg:#fff;--fg:#111;--card:#f3f4f6;--box:#e5e7eb;--mut:#6b7280;--g:#15803d;--r:#b91c1c;--n:#6b7280;--q:#1d4ed8;--gb:#dcfce7;--rb:#fee2e2;--nb:#e5e7eb;--ab:#fef3c7;--ac:#b45309}
@media(prefers-color-scheme:dark){:root{--bg:#111418;--fg:#e5e7eb;--card:#1c2128;--box:#2b323c;--mut:#9ca3af;--g:#4ade80;--r:#f87171;--n:#9ca3af;--q:#60a5fa;--gb:#12351f;--rb:#3b1717;--nb:#2b323c;--ab:#4a3a0f;--ac:#fbbf24}}
*{box-sizing:border-box}
html{font-size:clamp(14px, 0.72vw, 20px)}
html,body{margin:0;padding:0;overflow-x:hidden}
body{background:var(--bg);color:var(--fg);font:1rem/1.35 system-ui,sans-serif;min-height:100vh;display:flex;flex-direction:column;padding:.7rem;gap:.7rem;width:100%}
.m{color:var(--mut);font-size:.85rem}.g{color:var(--g)}.r{color:var(--r)}.n{color:var(--n)}.err{color:var(--r);font-weight:700;font-size:.85rem}
h2{margin:0 0 .5rem;font-size:1rem;letter-spacing:.05em}
.top{display:flex;flex-wrap:wrap;gap:.4rem .9rem;align-items:center;padding-bottom:.5rem;border-bottom:1px solid #8884}
.chip{display:inline-block;font-weight:700;font-size:1.05rem;border-radius:999px;padding:.15rem .75rem;white-space:nowrap;background:var(--nb)}
.chip.g{background:var(--gb);color:var(--g)}.chip.r{background:var(--rb);color:var(--r)}.chip.n{background:var(--nb);color:var(--fg)}.chip.a{background:var(--ab);color:var(--ac)}
.chip.big{font-size:1.6rem;padding:.1rem 1rem}.chip small{font-size:.65rem;font-weight:400;color:var(--mut);margin-left:.3rem}
.sd{display:inline-block;width:.9rem;height:.9rem;border-radius:50%;background:var(--n)}.sd.g{background:var(--g)}.sd.r{background:var(--r)}.sd.a{background:#f59e0b}
.rng{display:inline-flex;align-items:center;gap:.4rem;font-size:.7rem;color:var(--mut)}
.rbar{position:relative;display:inline-block;width:9rem;height:.5rem;background:var(--box);border-radius:.3rem}
.rbar b{position:absolute;top:-.25rem;width:1rem;height:1rem;margin-left:-.5rem;border-radius:50%;background:var(--q)}
.rbar i{position:absolute;top:-.1rem;height:.7rem;border-left:2px solid var(--mut)}
.dyn{display:contents}.info{position:relative;margin-left:auto;cursor:pointer;font-size:1.4rem;line-height:1;user-select:none}
.pop{display:none;position:absolute;right:0;top:1.9rem;width:30rem;max-width:90vw;background:var(--card);border:1px solid #8886;border-radius:.5rem;padding:.8rem 1rem;z-index:20;font-size:.85rem;line-height:1.45;color:var(--fg);box-shadow:0 .4rem 1.2rem #0006}
.info:hover .pop,.info.open .pop{display:block}.pop p{margin:.3rem 0}
#layout{flex:1 0 auto;display:flex;flex-direction:column;gap:.8rem;min-height:0}
#mainsec{flex:1 0 auto;display:flex;flex-direction:column;min-height:0}
#bets{flex:1 0 auto;display:grid;grid-template-columns:repeat(auto-fit,minmax(22rem,1fr));grid-auto-rows:min-content;align-content:start;gap:.8rem}
.card{background:var(--card);border-radius:.6rem;padding:1rem;min-width:0;position:relative}
.bc{display:flex;flex-direction:column;gap:.45rem}
.ctl{display:flex;gap:.5rem;align-items:center}
.ctl a,.ctl button{font:inherit;font-size:.85rem;padding:.15rem .6rem;border-radius:999px;border:1px solid var(--box);background:var(--bg);color:var(--fg);text-decoration:none;cursor:pointer}
.ctl a:hover,.ctl button:hover{border-color:var(--q)}
.card.big{position:fixed;inset:3vh 3vw;z-index:50;overflow:auto;font-size:1.3rem;box-shadow:0 1rem 3rem rgba(0,0,0,.45)}
#bd{display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:40}
#bd.on{display:block}
.more{position:absolute;top:.2rem;right:.6rem;font-size:1.2rem;color:var(--mut);cursor:help;line-height:1}
.pill{display:inline-block;font-weight:700;font-size:1rem;border-radius:999px;padding:.2rem .8rem;white-space:nowrap}
.pill.g{background:var(--gb);color:var(--g)}.pill.r{background:var(--rb);color:var(--r)}.pill.n{background:var(--nb)}.pill.a{background:var(--ab);color:var(--ac)}
.mainline{font-size:1.25rem;font-weight:800}.mainline.a{color:var(--ac)}
.chips{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap}.chips .sp2{margin-left:auto;display:flex;align-items:center;gap:.6rem}
.ring{width:3rem;height:3rem;flex:none}
.q{display:inline-block;background:var(--q);color:#fff;font-weight:700;font-size:1.05rem;border-radius:.4rem;padding:.1rem .6rem;white-space:nowrap}
.sbw{display:flex;gap:.8rem;align-items:center;background:var(--box);border-radius:.5rem;padding:.45rem .8rem}
.sbrows{flex:1;min-width:0}
.sr{display:flex;justify-content:space-between;align-items:baseline}
.sr .st b{font-size:1.4rem}.sr .st small{font-size:.7rem;color:var(--mut);margin-left:.4rem;letter-spacing:.08em}
.sr .sp{font-size:2.2rem;font-weight:800;font-variant-numeric:tabular-nums;text-align:right;min-width:3.2rem;line-height:1.1}
.sr.trail{opacity:.6}
.sr.tot{border-top:1px solid #8886;margin-top:.15rem;padding-top:.05rem}.sr.tot .st small{font-size:.75rem;letter-spacing:.12em}
.bub{position:absolute;top:1.1rem;line-height:1.2rem;font-size:.8rem;font-weight:700;background:#374151;color:#fff;border-radius:.6rem;padding:0 .45rem;z-index:2}
.sbq{width:7.5rem;flex:none;text-align:center;border-left:1px solid #8884;padding-left:.7rem}
.sbq .ql{font-weight:700;font-size:1rem}.sbq .qc{font-size:1.3rem;font-weight:700;font-variant-numeric:tabular-nums}
.sbq small{font-size:.65rem;font-weight:400;color:var(--mut)}.sbq .ago{font-size:.65rem;color:var(--mut)}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:.5rem;text-align:center}.tiles.three{grid-template-columns:repeat(3,1fr)}.tiles.two{grid-template-columns:repeat(2,1fr)}
.tile .tl{font-size:.62rem;letter-spacing:.08em;color:var(--mut);text-transform:uppercase}
.tiles .tile:not(.big) .tv{font-size:1.2rem}.tile.big .tv{font-size:2rem}.tile .tv.g{color:var(--g)}.tile .tv.r{color:var(--r)}.tile .tv{font-size:1.4rem;font-weight:800;line-height:1.1}.tile .ts{font-size:.7rem;color:var(--mut)}
.bar{position:relative;height:1.2rem;background:var(--box);border-radius:.6rem}
.bar i{display:block;height:1.2rem;border-radius:.6rem;background:var(--n)}
.barw{position:relative;padding:1.1rem 0 2rem}
.mk{position:absolute;top:.8rem;height:2rem;width:0;border-left:.18rem solid var(--fg)}
.tri{position:absolute;top:2.3rem;width:0;height:0;border-left:.5rem solid transparent;border-right:.5rem solid transparent;border-bottom:.8rem solid var(--q);margin-left:-.5rem}
.we{position:absolute;bottom:0;font-size:.65rem;color:var(--mut);line-height:1rem;transform:translateX(-50%)}
.gl{position:absolute;top:1.1rem;height:1.2rem;width:0;border-left:1px solid #8886}
.fm{position:absolute;top:0;color:#a855f7;font-size:.95rem;line-height:1;transform:translateX(-50%)}
.fv{color:#a855f7}
.gl-line{display:inline-block;height:1em;border-left:.18rem solid var(--fg);margin-right:.35rem;vertical-align:-.1em}
.mv{color:var(--q)}
.cast{background:var(--box);border-radius:.5rem;padding:.5rem .8rem;font-size:1.1rem}
.dot{display:inline-block;width:.65rem;height:.65rem;border-radius:50%;background:var(--q);margin-right:.4rem}
.lp{color:var(--fg);margin-top:.2rem;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.side{display:grid;grid-template-columns:repeat(auto-fit,minmax(24rem,1fr));gap:.8rem}
.panel{min-width:0}.panel .card{margin-bottom:.5rem}
.frow{display:flex;flex-direction:column;gap:.35rem}.frow .l1{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap}.frow .l1 b{font-size:1.15rem}
.mini2{position:relative;height:.7rem;background:var(--box);border-radius:.4rem}.mini2 i{display:block;height:.7rem;border-radius:.4rem;background:var(--n)}.mini2 u{position:absolute;top:-.2rem;height:1.1rem;border-left:2px solid var(--fg)}
.wr{position:relative;height:1rem;background:var(--box);border-radius:.5rem;margin:.6rem 0}.wr i{display:block;height:1rem;border-radius:.5rem;background:var(--g)}.wr u{position:absolute;top:-.3rem;height:1.6rem;border-left:2px dashed var(--fg)}
.frow1{display:flex;align-items:center;gap:.6rem;padding:.2rem 0;font-size:1rem;border-bottom:1px solid #8883}.frow1>span:first-child{flex:1;min-width:0}
.row2{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin:.3rem 0}
@media(max-width:1100px){#bets{grid-template-columns:repeat(2,1fr)}}
@media(max-width:700px){#bets{grid-template-columns:1fr}.side{grid-template-columns:1fr}}
@media(min-width:1800px){body{height:100vh;overflow:hidden}
#layout{display:grid;grid-template-columns:78fr 22fr;gap:.8rem;flex:1;min-height:0}
#mainsec{min-height:0;overflow-y:auto;padding-right:.3rem}.top{flex-wrap:nowrap}.side{display:block;overflow-y:auto;min-height:0;padding-right:.3rem}
.side .panel{margin-bottom:.9rem}}
</style></head><body><div id=bd onclick="[...EXP].forEach(xp)"></div>
<div class=top><span id=top class=dyn></span><span class=info onclick="this.classList.toggle('open')">ⓘ<span class=pop id=infoPop><p>Final decision is made at the ping using the strike nearest 50% — this is a preview.</p><p>Info only — the test rule is hold to settlement. Cash-out, best and worst are for information.</p><p>Small sample — judge at ~Nov 4 (pre-registered evaluation). Window 2026-10-21 to 2026-11-03.</p><p>Scores: Kalshi live data API. Prices: Kalshi public markets. Read-only.</p><p>Score model: the frozen research #59 model (E1) and your formula (#38), each locked at least 30 min before tip. Accuracy only, no betting. A tick means the model's total landed within 10 points.</p><p>Trade wire: alerts from a free feed watcher (public RSS from RealGM, ESPN, CBS) that reads trades, signings, waivers/buyouts and two-way/10-day deals every 10 minutes. These are headlines, not confirmed facts. A tick means the roster data shows the same team change. Chips show last season's minutes tier and veterans aged 32+. Info only, no betting.</p><p>Injury wire: OFFICIAL rows are changes between the NBA's own injury-report PDFs (checked every 10 minutes): a new listing, a status change, or a player cleared from the report. NEWS rows are headlines from RealGM, ESPN and CBS feeds and are unofficial. LATE means the change came within 90 minutes of tip. BOT GAME means your overs bot has a pick in that game. Chips show the player's minutes tier and veterans aged 32+. Info only, no betting.</p><p>The P&amp;L chip is the unrealized mark: market mid value minus cost and fees. The ring is the market chance on your side. The arrow chip is total vs the line's pace (line × minutes played / 48), coloured for your side.</p></span></span></div><div id=er class=err></div>
<div id=layout>
<div id=mainsec><h2>YOUR BETS</h2><div id=bets></div></div>
<div class=side>
<div class=panel id=upPanel style="display:none"><h2>UPCOMING PINGS</h2><div id=up></div></div>
<div class=panel><h2>FINISHED GAMES (MY BETS)</h2><div id=fin></div></div>
<div class=panel><h2>TOTAL RECORD · #40 TOTALS STRATEGY</h2><div id=sb class=card></div></div>
<div class=panel id=smPanel style="display:none"><h2>SCORE MODEL</h2><div id=sm class=card></div></div>
<div class=panel id=twPanel style="display:none"><h2>TRADE WIRE</h2><div id=tw class=card></div></div>
<div class=panel id=iwPanel style="display:none"><h2>INJURY WIRE</h2><div id=iw class=card></div></div>
<div class=panel id=plPanel style="display:none"><h2>PAPER LEAGUE · NO RULE HAS PASSED</h2><div id=pl class=card></div></div>
</div></div>
<script>
const $=id=>document.getElementById(id),f1=x=>x==null?'-':(+x).toFixed(1),usd=x=>x==null?'-':(x<0?'-$':'$')+Math.abs(x).toFixed(2);
const usdS=x=>x==null?'–':Math.abs(x)<0.005?'$0.00':(x<0?'−$':'+$')+Math.abs(x).toFixed(2);
const cls=v=>v==null||Math.abs(v)<0.005?'n':v>0?'g':'r',pc=x=>x==null?'–':(x*100).toFixed(1)+'%';
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
const chip=(t,c,title)=>`<span class="chip ${c||'n'}"${title?` title="${esc(title)}"`:''}>${t}</span>`;
const BUILD='__BUILD__';
function syncChip(y){if(!y||!y.on)return chip('Kalshi: file only','n','live sync off; using the last ledger file');const ok=y.age_s!=null&&y.age_s<=120&&!y.error;return chip(ok?'Kalshi synced '+y.age_s+'s ago':'Kalshi sync '+(y.error||'waiting'),ok?'g':'r','GET-only pull every 30 s: cash, fills, positions, orders, settlements')}
function botChip(b){if(!b)return chip('🤖 bot off','n','no bot heartbeat file');const ok=b.live&&b.placing&&b.age_s<=300;return chip(ok?'🤖 bot live':'🤖 bot '+(b.age_s>300?'silent':'not placing'),ok?'g':'r','heartbeat '+b.age_s+'s ago; live '+b.live+', placing '+b.placing)}
let OFF=0,GM={};
const NOWS=()=>Date.now()/1000+OFF;
function mmss(x){x=Math.max(0,Math.floor(x));return Math.floor(x/60)+':'+String(x%60).padStart(2,'0')}
function clk(g){
 if(g.phase!='live'||g.clock_secs==null||!g.clock_prefix) return '';
 if(!g.running) return mmss(g.clock_secs)+' <small>⏸ stopped</small>';
 let dt=NOWS()-g.anchor_ts;const paused=dt>45;if(paused)dt=45;
 return mmss(g.clock_secs-dt)+' <small>'+(paused?'⏸ paused':'est.')+'</small>'}
function agoTxt(g){return (g.phase=='live'&&g.anchor_ts)?'last play '+Math.max(0,Math.floor(NOWS()-g.anchor_ts))+' s ago':''}
function sbHtml(g){const pre=g.phase=='pre',a=g.away_pts,h=g.home_pts;
const cl=(x,y)=>a==h?'':(x>y?'lead':'trail');
const row=(c,lab,p,k)=>`<div class="sr ${k}"><div class=st><b>${c}</b><small>${lab}</small></div><div class=sp>${pre?'–':p}</div></div>`;
const ql=g.clock_prefix?g.clock_prefix:g.clock;
return `<div class=sbw><div class=sbrows>${row(g.away,'AWAY',a,cl(a,h))}${row(g.home,'HOME',h,cl(h,a))}<div class="sr tot"><div class=st><small>TOTAL</small></div><div class=sp>${pre?'–':g.total}</div></div></div>
<div class=sbq><div class=ql>${esc(ql)}</div>${pre?'':`<div class=qc data-ck="${g.key}">${clk(g)}</div><div class=ago data-ago="${g.key}">${agoTxt(g)}</div>`}</div></div>`}
function clockTick(){for(const e of document.querySelectorAll('[data-ck]')){const g=GM[e.dataset.ck];if(g)e.innerHTML=clk(g)}
for(const e of document.querySelectorAll('[data-ago]')){const g=GM[e.dataset.ago];if(g)e.textContent=agoTxt(g)}}
function cast(g){return `<div class=cast><div><span class=dot></span>${g.ball?'Ball: <b>'+g.ball+'</b>':'Ball: -'}</div><div class=lp>Last play: ${g.last_play?esc(g.last_play):'-'}</div></div>`}
function ring(p,wide){if(p==null)return '';const v=Math.round(p*100),col=p>=.5?'var(--g)':'var(--r)';
return `<svg class=ring viewBox="0 0 36 36"><circle cx=18 cy=18 r=15.9155 fill=none stroke="var(--box)" stroke-width=4></circle><circle cx=18 cy=18 r=15.9155 fill=none stroke="${col}" stroke-width=4 stroke-dasharray="${v} ${100-v}" stroke-dashoffset=25></circle><text x=18 y=21.5 text-anchor=middle font-size=10 font-weight=700 fill="var(--fg)">${wide?'~':''}${v}%</text></svg>`}
const SCALE=300;
function bubble(g,line,P){const x1=+P(g.total),past=g.total>line,inside=!past&&x1>=8&&x1<=100;
return `<span class=bub style="left:${inside?x1:(x1>92?x1:x1+0.6)}%;transform:${(inside||x1>92)?'translateX(-100%)':'none'}">${g.total}</span>`}
function gauge(g,line,par,pre,b){const imp=typeof g.implied=='number'?g.implied:null,fm=typeof g.proj=='number'?g.proj:null,P=v=>Math.min(100,Math.max(0,v/SCALE*100)).toFixed(1);
const tk=[0,100,200,300].map(v=>`<span class=we style="left:${P(v)}%${v==0?';transform:none':v==300?';transform:none;left:auto;right:0':''}">${v}</span>`).join('');
return `<div class=barw><div class=bar>${pre?'':`<i style="width:${P(g.total)}%"></i>`}</div><span class=gl style="left:33.3%"></span><span class=gl style="left:66.7%"></span><span class=mk style="left:${P(line||0)}%"></span>${imp!=null?`<span class=tri style="left:${P(imp)}%"></span>`:''}${fm!=null?`<span class=fm style="left:${P(fm)}%">◆</span>`:''}${tk}${pre?'':bubble(g,line,P)}</div>`}
function bet(b){const g=b.game_info,pre=g.phase=='pre',c=(b.badge.startsWith('WON')||b.badge=='WINNING ON PACE')?'g':(b.badge.startsWith('LOST')||b.badge=='BEHIND ON PACE')?'r':'n';
const o=b.side=='OVER';
const ml=b.side+' '+b.strike;
const co=b.cashout,mk=typeof g.implied=='number'?g.implied.toFixed(1):esc(g.implied_str);
const det=['formula '+f1(g.proj),b.clv_txt,'cash-out '+(co?usd(co.net)+' net, P&L '+usd(co.pnl_now):'n/a'),'best '+usd(b.best)+' / worst '+usd(b.worst),'naive pace '+(g.pace==null?'n/a':Math.round(g.pace))].concat(b.awaiting_settlement?['final, awaiting settlement']:[]).join('\n');
let vp='';if(!pre&&b.vs_par!=null){const flat=b.par_text=='right on pace',v=Math.round(Math.abs(b.vs_par));vp=chip((flat?'● 0':(b.vs_par>0?'▲ ':'▼ ')+v)+'<small>vs pace</small>',flat?'n':b.par_color)}
const fT=`<div class=tile><div class=tl>Formula</div><div class="tv fv">◆ ${f1(g.proj)}</div></div>`;
const lT=`<div class=tile><div class=tl>Line</div><div class=tv><span class=gl-line></span>${b.strike}</div><div class=ts>${b.side}</div></div>`;
const mT=`<div class=tile><div class=tl>Market expects</div><div class=tv><span class=mv>▲ ${mk}</span></div></div>`;
const dn=b.points!=null&&(o?b.points<=0:b.points<0),pcol=dn?(o?'g':'r'):(b.par_color||'n');
const pT=`<div class="tile big"><div class=tl>Points left</div><div class="tv ${pcol}">${b.points==null?'–':dn?(o?'✓':'✗'):b.points}</div><div class=ts>${dn?'went over':(o?'to go over':'until it goes over')}</div></div>`;
const tiles=`<div class=tiles>${pT}${lT}${mT}${fT}</div>`;
const k=esc(g.key||b.ticker),big=EXP.has(k);
return `<div class="card bc${big?' big':''}" data-k="${k}"><span class=more title="${esc(det)}">⋯</span><div class=ctl><a href="https://www.nba.com/games?date=${DATE}" target=_blank rel=noopener title="Official NBA games page in a new tab. Video needs your NBA app / League Pass / TV login.">▶ Watch</a><button onclick="xp('${k}')" title="Expand / close (Esc)">${big?'✕ Close':'⤢ Expand'}</button></div>${sbHtml(g)}
<div class=chips><span class="pill ${c}">${b.badge}</span><span class=sp2>${vp}${chip(usdS(b.unrealized),cls(b.unrealized))}${ring(b.chance,b.chance_wide)}</span></div>
<div><div class="mainline ${c}">${ml}</div><div class=m>${esc(g.played)} · entry ${(b.entry*100).toFixed(0)}c x${b.count}</div></div>
${tiles}${gauge(g,b.strike,b.par,pre,b)}${pre?'':cast(g)}</div>`}
function wait(w){const g=w.game_info;return `<div class="card bc"><span class=more title="Resting order only: not counted in cost, P&amp;L, best/worst or the scoreboard until it fills.">⋯</span>${sbHtml(g)}<div class=chips><span class="pill a">ORDER WAITING</span></div>
<div><div class="mainline a">${w.side} ${w.strike} · limit ${(w.price*100).toFixed(0)}c x${w.remaining}</div><div class=m>${esc(w.note)}</div></div>
<div class="tiles two"><div class=tile><div class=tl>Line</div><div class=tv><span class=gl-line></span>${w.strike}</div><div class=ts>${w.side}</div></div><div class=tile><div class=tl>Market expects</div><div class=tv><span class=mv>▲ ${typeof g.implied=='number'?g.implied.toFixed(1):esc(g.implied_str)}</span></div></div></div>${g.phase=='pre'?'':cast(g)}</div>`}
function up(u){return `<div class=card><div><b>${u.away} @ ${u.home}</b> <span class=q>Tip ${u.start_et}</span></div><div>${u.pinged_at?'pinged at '+u.pinged_at:'ping ~'+u.ping_et}${u.order?' · '+esc(u.order):''}</div>
<div class=m>On track? <b>${esc(u.on_track)}</b> (formula ${f1(u.proj)} vs mkt ~${esc(u.implied_str)}) · last gap ${f1(u.last_gap)} at ${u.last_gap_et||'-'}</div></div>`}
const isFin=b=>b.game_info.phase=='final'||b.result=='yes'||b.result=='no';
function fin(b){const g=b.game_info,won=b.badge.startsWith('WON'),c=won?'g':b.badge.startsWith('LOST')?'r':'n';
return `<div class=frow1 title="${esc(b.awaiting_settlement?'final, awaiting settlement':'settled')}"><span>${g.away} @ ${g.home} · ${b.side} ${b.strike} · ${g.total}</span><span class=${c}>${won?'✅':'❌'}</span>${chip(usdS(b.unrealized),cls(b.unrealized))}</div>`}
function smRow(r){const st=r.state=='final'?`final ${r.total} <span class=${r.ok?'g':'r'}>${r.ok?'✓':'✗'}</span> off by ${r.off}`:r.state=='live'?`live ${r.total}`:'not started';
return `<div class=frow1><span>${r.away} @ ${r.home} — model ${r.model_away}–${r.model_home} (${r.model_total}) · your formula ${r.formula_total==null?'-':r.formula_total} · ${st}</span></div>`}
function smHtml(m){if(!m)return '';const c=m.cum,p1=x=>x==null?'-':(+x).toFixed(1);
const cl=c.n?`Model avg off ${p1(c.model_mae)} (${c.n}) · your formula ${p1(c.formula_mae)} · within 10: ${c.model_within10==null?'-':Math.round(c.model_within10*100)+'%'}`:'no finished games yet';
return m.rows.map(smRow).join('')+`<div class=row2><b>${cl}</b></div>`}
const TWC={TRADE:'a',SIGNING:'g',WAIVER:'r',TWO_WAY_10_DAY:'n',POSSIBLE_MOVE:'n'},TWL={TRADE:'TRADE',SIGNING:'SIGN',WAIVER:'WAIVE',TWO_WAY_10_DAY:'2-WAY',POSSIBLE_MOVE:'MAYBE'};
function twRow(a){return `<div class=frow1><span><span class=m>${esc(a.t)}</span> <span class="chip ${TWC[a.type]||'n'}">${esc(TWL[a.type]||a.type)}</span> ${esc(a.headline)}${a.tier&&a.tier!='none'?' '+chip(esc(a.tier),'n'):''}${a.veteran?' '+chip('VET','a'):''}</span>${a.confirmed?'<span class=g title="roster data shows the same move">✓</span>':''}</div>`}
function twHtml(w){if(!w||!w.alerts.length)return '';return w.alerts.map(twRow).join('')+(w.feed_errors.length?`<div class="m r">feed error: ${esc(w.feed_errors.join(', '))}</div>`:'')}
const IWL={NEW:'NEW',STATUS_CHANGE:'CHANGE',REMOVED:'CLEARED',NEWS_INJURY:'NEWS'},IWS={Out:'r',Doubtful:'r',Questionable:'a',Probable:'g',Available:'g'};
function iwRow(a){const sc=x=>x?`<span class="${IWS[x]||'n'}">${esc(x)}</span>`:'–';
const what=a.official?`${esc(a.team)} ${esc(a.player)} ${a.type=='REMOVED'?sc(a.old)+' → cleared':a.old?sc(a.old)+' → '+sc(a.new):'listed '+sc(a.new)}`:`${esc(a.headline)}${a.new?' '+sc(a.new):''}`;
return `<div class=frow1><span><span class=m>${esc(a.t)}</span> ${chip(a.official?'OFFICIAL':'NEWS',a.official?'g':'n')} ${what}${a.tier&&a.tier!='none'?' '+chip(esc(a.tier),'n'):''}${a.veteran?' '+chip('VET','a'):''}${a.late?' '+chip('LATE','r'):''}${a.bot?' '+chip('BOT GAME','a'):''}</span></div>`}
function iwHtml(w){if(!w||!w.alerts.length)return '';return w.alerts.map(iwRow).join('')+`<div class=m>${w.last_report?'last report '+esc(w.last_report):''}</div>`+(w.feed_errors.length?`<div class="m r">feed error: ${esc(w.feed_errors.join(', '))}</div>`:'')}
function plRow(r){const c=r.mean_c==null?'–':(r.mean_c>0?'+':'')+r.mean_c.toFixed(1)+'c';
return `<div class=frow1><span>${chip(esc(r.id),r.control?'n':'a',esc(r.label))} ${esc(r.sport)} · ${r.won}–${r.lost}${r.open?' · '+r.open+' open':''} · ${c}/bet · ${r.n}/${r.need}</span>${chip(usdS(r.net),cls(r.net))}</div>`}
function plHtml(p){if(!p||!p.rows.length)return '';return p.rows.map(plRow).join('')+`<div class=m>${esc(p.note)}</div>`}
function rng(n){const lo=n.open_worst,hi=n.open_best,m=n.open_mark;if(!(hi>lo))return '';const p=Math.min(100,Math.max(0,(m-lo)/(hi-lo)*100));
const z=(lo<0&&hi>0)?`<i style="left:${(-lo/(hi-lo)*100).toFixed(1)}%"></i>`:'';
return `<span class=rng><span>${usdS(lo)}</span><span class=rbar>${z}<b style="left:${p.toFixed(1)}%"></b></span><span>${usdS(hi)}</span></span>`}
let EXP=new Set(),DATE='';
function xp(k){EXP.has(k)?EXP.delete(k):EXP.add(k);document.querySelectorAll('[data-k="'+k+'"]').forEach(e=>{const on=EXP.has(k);e.classList.toggle('big',on);const b=e.querySelector('.ctl button');if(b)b.textContent=on?'✕ Close':'⤢ Expand'});const bd=document.getElementById('bd');if(bd)bd.classList.toggle('on',EXP.size>0)}
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&EXP.size){[...EXP].forEach(xp)}});
async function tick(){try{const s=await (await fetch('/api/state')).json();DATE=s.date||DATE;OFF=s.server_ts-Date.now()/1000;
if(s.build&&s.build!==BUILD){try{const t=+sessionStorage.getItem('rl')||0;if(Date.now()-t>30000){sessionStorage.setItem('rl',String(Date.now()));location.reload();return}}catch(e){}}
GM={};for(const x of s.bets)GM[x.game_info.key]=x.game_info;for(const x of (s.waiting||[]))GM[x.game_info.key]=x.game_info;for(const x of s.others)GM[x.key]=x;
const errs=Object.keys(s.errors),ages=Object.values(GM).filter(g=>g.phase=='live'&&g.fetch_age!=null).map(g=>g.fetch_age),mxa=ages.length?Math.max(...ages):0;
const dc=errs.length?'r':mxa<=5?'g':mxa<=30?'a':'r';
const agetip='updated '+s.now_et+' ET · '+Object.entries(s.age_s).map(([k,v])=>k+' '+v+'s').join(', ')+(errs.length?' · errors: '+errs.map(k=>k+' '+s.errors[k]).join(', '):'');
$('er').textContent='';
const n=s.night,c=s.cash,sb=s.scoreboard,tn=n.settled_pnl+n.open_mark;
$('top').innerHTML=`${chip('Cash '+(c.total==null?'-':usd(c.total)),'n','as of '+(c.asof_et||'?')+' ET; shards '+Object.entries(c.shards).map(([k,v])=>k+':'+v).join(' '))}${chip('Total '+sb.won+'–'+sb.lost+(sb.pending?' ('+sb.pending+' open)':'')+' · '+usdS(sb.pnl),cls(sb.pnl)+' big','#40 totals strategy, every bet since '+(sb.window?sb.window[0]:'Oct 21')+' (fees included)')}${chip('Tonight '+s.totals.won+'–'+s.totals.lost+' · '+usdS(tn),cls(tn),'tonight only: settled plus open bets at market value')}${rng(n)}${botChip(s.bot)}${syncChip(s.sync)}<span class="sd ${dc}" title="${esc(agetip)}"></span>
`;
$('bets').innerHTML=(s.bets.filter(b=>!isFin(b)).map(bet).join('')+(s.waiting||[]).map(wait).join(''))||'<div class=m>none</div>';
$('up').innerHTML=s.upcoming.map(up).join('');$('upPanel').style.display=s.upcoming.length?'':'none';
const fb=s.bets.filter(isFin).sort((x,y)=>(x.game_info.start||'')<(y.game_info.start||'')?-1:1),fw=fb.filter(b=>b.badge.startsWith('WON')).length,fpl=fb.reduce((a,b)=>a+(b.unrealized||0),0),fm=s.finished.filter(g=>g.bet),fsc=fm.filter(g=>g.fvf),fcl=fsc.filter(g=>g.fvf.closer=='formula').length;
$('fin').innerHTML=fb.length?`<div class=row2>${chip(fw+'–'+(fb.length-fw)+' · '+usdS(fpl),cls(fpl))}</div>`+fb.map(fin).join('')+(fsc.length?`<div class=row2>${chip('formula closer '+fcl+'/'+fsc.length)}</div>`:''):'<div class=m>–</div>';
$('twPanel').style.display=(s.trade_wire&&s.trade_wire.alerts.length)?'':'none';$('tw').innerHTML=twHtml(s.trade_wire);
$('iwPanel').style.display=(s.injury_wire&&s.injury_wire.alerts.length)?'':'none';$('iw').innerHTML=iwHtml(s.injury_wire);
$('plPanel').style.display=(s.paper_league&&s.paper_league.rows.length)?'':'none';$('pl').innerHTML=plHtml(s.paper_league);
$('smPanel').style.display=s.score_model?'':'none';$('sm').innerHTML=smHtml(s.score_model);
const be=sb.breakeven!=null?sb.breakeven:0.537,wr=sb.win_rate;
$('sb').title=sb.awaiting>0?sb.awaiting+' awaiting Kalshi settlement':'';
$('sb').innerHTML=`<div class=row2>${chip('W '+sb.won,'g big')}${chip('L '+sb.lost,'r big')}${chip(sb.pending+' open','n')}</div>
<div class=row2><b style="font-size:1.6rem" class=${cls(sb.pnl)}>${usdS(sb.pnl)}</b>${chip('staked '+usd(sb.staked))}${chip('ROI '+pc(sb.roi))}${sb.avg_clv==null?'':chip('CLV '+sb.avg_clv.toFixed(1)+'c')}</div>
<div class=wr><i style="width:${wr==null?0:(wr*100).toFixed(1)}%"></i><u style="left:${(be*100).toFixed(1)}%"></u></div><div class=row2>${chip('win '+pc(wr),'n')}${chip('break-even '+pc(be),'n')}</div>
<div class=row2>${Object.entries(sb.by_side||{}).filter(([k,v])=>v[0]+v[1]>0).map(([k,v])=>chip((k=='OVER'?'Overs ':'Unders ')+v[0]+'–'+v[1],'n')).join('')}</div>
${(sb.by_night||[]).map(x=>{const d=new Date(x.date+'T12:00:00');return `<div class=frow1><span>${d.toLocaleDateString('en-US',{month:'short',day:'numeric'})} · ${x.won}–${x.lost}${x.pending?' · '+x.pending+' open':''}</span>${chip(usdS(x.pnl),cls(x.pnl))}</div>`}).join('')}`}
catch(x){$('er').textContent='Dashboard fetch failed: '+x.name}}
tick();setInterval(tick,1000);setInterval(clockTick,1000);
</script></body></html>"""


def _file_mtime():
    try:
        return str(os.path.getmtime(os.path.abspath(__file__)))
    except OSError:
        return "0"


import hashlib
BUILD_ID = hashlib.sha256((PAGE + _file_mtime()).encode("utf-8")).hexdigest()[:12]


def render_page():
    return PAGE.replace("__BUILD__", BUILD_ID)


class Holder:
    """The Dash being served. Without --date it rolls over to the new NBA night (6 AM ET boundary) on its own."""

    def __init__(self, dash, fixed=False, make=Dash):
        self.current, self.fixed, self.make = dash, fixed, make

    def rollover(self, now_et):
        """Swap in a fresh Dash when nba_night(now) moved on; returns True if it did."""
        if self.fixed:
            return False
        d = nba_night(now_et)
        if d == self.current.d:
            return False
        old, new = self.current, self.make(d)
        new.start()
        self.current = new
        old.stop()
        return True

    def watch(self, every=60):
        def run():
            while True:
                time.sleep(every)
                try:
                    if self.rollover(datetime.now(ET)):
                        print("rolled over to %s" % self.current.d, flush=True)
                except Exception as e:  # noqa: BLE001
                    print("rollover error: %s" % type(e).__name__, flush=True)
        threading.Thread(target=run, daemon=True).start()

    def build(self):
        return self.current.build()


def make_handler(dash, port):
    ok_hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.headers.get("Host", "") not in ok_hosts:
                self.send_response(403)
                self.end_headers()
                return
            path = self.path.split("?")[0]
            if path == "/api/state":
                body, ct = json.dumps(dash.build()).encode(), "application/json"
            elif path == "/":
                body, ct = render_page().encode(), "text/html; charset=utf-8"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--port", type=int, default=8768)
    ap.add_argument("--no-sync", action="store_true", help="don't pull the account from Kalshi; use the last ledger file")
    a = ap.parse_args()
    d = datetime.strptime(a.date, "%Y-%m-%d").date() if a.date else nba_night(datetime.now(ET))
    sync = not a.no_sync
    dash = Dash(d, sync=sync)
    dash.start()
    holder = Holder(dash, fixed=bool(a.date), make=lambda day: Dash(day, sync=sync))
    holder.watch()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(holder, a.port))
    print("serving http://127.0.0.1:%d/ for %s%s" % (a.port, d, "" if a.date else " (rolls over to each new night at 6 AM ET)"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
