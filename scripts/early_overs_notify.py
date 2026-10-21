"""Discord notifier for the nightly NBA totals pick list (research log #40). NOTIFICATIONS ONLY.

Reuses scripts/early_overs_picks.py (public market-data GETs only). Never places orders, never uses Kalshi keys.
Posts to the user's own Discord webhook (secrets/discord_webhook.txt or env DISCORD_WEBHOOK_URL). The URL is
never printed, logged, or put in an error message.

Usage: .venv/Scripts/python.exe scripts/early_overs_notify.py [--test] [--dry-run]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
SECRET_PATH = ROOT / "secrets" / "discord_webhook.txt"
STATE_PATH = ROOT / "logs" / "early_overs_notified.json"
LOG_PATH = ROOT / "logs" / "early_overs_notify.log"
PLACEHOLDER = "PASTE_WEBHOOK_URL_HERE"
URL_PREFIXES = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")
FEE_COEF = 0.0175
# (lo, hi, win_pct, n, ci_lo, ci_hi): F1_R2_S1 2025-26 discovery, exploratory
BUCKETS = [(0, 2, 51.5, 200, 44.6, 58.3), (2, 4, 47.9, 163, 40.3, 55.5), (4, 6, 44.3, 122, 35.8, 53.1),
           (6, 8, 53.0, 100, 43.3, 62.5), (8, float("inf"), 46.7, 75, 35.8, 57.8)]


class NotifyError(Exception):
    """Error whose message is guaranteed not to contain the webhook URL."""


def _load_picks():
    spec = importlib.util.spec_from_file_location("early_overs_picks", ROOT / "scripts" / "early_overs_picks.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["early_overs_picks"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- webhook
def valid_url(url: str) -> bool:
    return bool(url) and url != PLACEHOLDER and url.startswith(URL_PREFIXES)


def load_webhook(path: Path = SECRET_PATH, env=None) -> str:
    env = os.environ if env is None else env
    url = ""
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                url = line
                break
    if not url or url == PLACEHOLDER:
        url = env.get("DISCORD_WEBHOOK_URL", "").strip() or url
    if not url or url == PLACEHOLDER:
        raise NotifyError(f"No Discord webhook set. Put the URL on a line in {path} (or set DISCORD_WEBHOOK_URL).")
    if not valid_url(url):
        raise NotifyError("Discord webhook URL is invalid: it must start with "
                          "https://discord.com/api/webhooks/ or https://discordapp.com/api/webhooks/")
    return url


def post_discord(url: str, content: str, tries: int = 4) -> None:
    """POST one message. Handles 429 retry_after. Raises NotifyError (URL-free) on failure."""
    for i in range(tries):
        try:
            r = requests.post(url, json={"content": content}, timeout=10)
        except requests.RequestException as e:
            if i == tries - 1:
                raise NotifyError(f"Discord post failed ({type(e).__name__})") from None
            time.sleep(2)
            continue
        if r.status_code == 429:
            try:
                ra = float(r.json().get("retry_after", 1))
            except Exception:  # noqa: BLE001
                ra = 1.0
            time.sleep(min(max(ra, 0.1), 30))
            continue
        if r.status_code >= 400:
            raise NotifyError(f"Discord post failed (HTTP {r.status_code})")
        return
    raise NotifyError("Discord post failed (rate limited)")


# ---------------------------------------------------------------- formatting
def bucket_for(diff: float):
    d = abs(diff)
    for b in BUCKETS:
        if b[0] <= d < b[1]:
            return b
    return BUCKETS[-1]


# User, 2026-10-21 ~17:00 ET: "lets do the instant fill" -> real bets are a LIMIT order AT THE ASK (fills now, taker fee).
ORDER_MODE = "taker"           # "maker" = limit at the bid (wait in line)
# User, 2026-10-21 ~17:05 ET: the channel is shared with friends - "just send the picks". No schedule, no cancel checks.
SEND_SCHEDULE = False
SEND_CANCEL = False
TAKER_COEF = 0.07


def fee_coef() -> float:
    return TAKER_COEF if ORDER_MODE == "taker" else FEE_COEF


def order_price(row: dict) -> float:
    return row["taker_price"] if ORDER_MODE == "taker" else row["limit"]


def breakeven_pct(limit_c: int) -> float:
    p = limit_c / 100
    return 100 * (p + fee_coef() * p * (1 - p))


def t12(dt: datetime) -> str:
    return f"{dt:%I:%M %p}".lstrip("0")


def fmt_num(x) -> str:
    return f"{float(x):g}"


def format_pick(row: dict, tip_et: datetime) -> str:
    key = row["game"]
    away, home = key[-6:-3], key[-3:]
    strike = fmt_num(row["strike"])
    if row["side"] == "OVER":
        bet = f"Bet: OVER {strike} -> buy YES on 'Over {strike}' ({row['ticker']})"
        word = "over"
    else:
        bet = f"Bet: UNDER {strike} -> buy NO on 'Over {strike}' ({row['ticker']})"
        word = "under"
    limit_c = int(round(order_price(row) * 100))
    ev_now = None
    if row.get("p_formula") is not None:
        px = limit_c / 100
        ev_now = (row["p_formula"] - px - fee_coef() * px * (1 - px)) * 100
    diff = row["proj"] - row["strike"]
    lo, hi, win, n, cl, ch = bucket_for(diff)
    if row.get("rule") == "grid_winner" and row.get("p_formula") is not None:
        record = (f"Rule: search-winner formula (bets only {row.get('grid_t', 8):g}+ pts from Kalshi's line). Last "
                  f"season's first half: 4+ pt picks won 50% (n=424); 8+ pt picks won 59% (n=204) but 50% on the "
                  f"untouched second half (n=170). Formula says "
                  f"{row['p_formula'] * 100:.0f}% for this pick; EV at {limit_c}c = {ev_now:+.1f}c by the "
                  f"formula's own odds; need {breakeven_pct(limit_c):.1f}% to break even.\n")
    else:
        record = (f"Last season similar picks won {win:.0f}% (95% CI {cl:.0f}-{ch:.0f}%); "
                  f"need {breakeven_pct(limit_c):.1f}% to break even.\n")
    cancel = tip_et - timedelta(minutes=15)
    yn = "YES" if row["side"] == "OVER" else "NO"
    order_line = (f"ORDER: BUY {yn} ({row['side']} {strike}) x 1 share @ {limit_c}c LIMIT"
                  + (" (instant fill at the ask)" if ORDER_MODE == "taker" else " (wait in line at the bid)") + "\n")
    prices_line = ""
    if row.get("yes_bid") is not None and row.get("yes_ask") is not None:
        yb, ya = int(round(row["yes_bid"] * 100)), int(round(row["yes_ask"] * 100))
        prices_line = (f"Prices now on 'Over {strike}': YES (over) bid {yb}c / ask {ya}c; "
                       f"NO (under) bid {100 - ya}c / ask {100 - yb}c\n")
    return (f"NBA totals pick - {away} @ {home} (tip {t12(tip_et)} ET)\n"
            + order_line + prices_line +
            f"{bet}\n"
            f"Shares: 1 contract (= 1 share) at {limit_c}c limit -> cost ~${limit_c / 100:.2f} + "
            f"~${fee_coef() * (limit_c / 100) * (1 - limit_c / 100):.3f} fee; pays $1.00 if it wins\n"
            f"Limit price: {limit_c}c x 1 contract (cost ~${limit_c / 100:.2f} + fee)\n"
            f"Cancel if unfilled by {t12(cancel)} ET. Don't chase.\n"
            f"Formula {row['proj']:.1f} vs line {strike} ({abs(diff):.1f} {word}). "
            + record +
            f"Research pick, not financial advice - you place the bet yourself.\n\n"
            + howto(away, home, row["side"], strike, limit_c, cancel))


def howto(away: str, home: str, side: str, strike: str, limit_c: int, cancel: datetime) -> str:
    tap = "Tap NO (= under)" if side != "OVER" else "Tap YES (= over)"
    return (f"How to place it (Kalshi app or site):\n"
            f'1) Search "{away} {home} total" (e.g. "ATL ORL total") and open Total points.\n'
            f'2) Find the line "Over {strike}". {tap}.\n'
            f"3) Switch the order type to LIMIT. Price: {limit_c}c. Quantity/shares: 1.\n"
            + ("4) Review and submit. At the ask price it should fill right away. If it doesn't (the price moved up), "
               "cancel it - don't pay more.\n" if ORDER_MODE == "taker" else
               "4) Review and submit. It will wait in line until someone sells to you.\n") +
            f"5) At {t12(cancel)}, if it hasn't filled, cancel it (open your resting orders / open orders and "
            f"cancel). Don't raise the price.\n"
            f"Screens can differ a bit - the key parts are: NO/YES, LIMIT, the price, 1 contract.")


def pick_info(row: dict) -> dict:
    return {"side": row["side"], "strike": fmt_num(row["strike"]), "limit_c": int(round(order_price(row) * 100))}


def format_cancel(key: str, tip_et: datetime, info: dict) -> str:
    yn = "YES" if info["side"] == "OVER" else "NO"
    return (f"Cancel check - {key[-6:-3]} @ {key[-3:]} (tip {t12(tip_et)}): if your 1 share {yn} @ {info['limit_c']}c on "
            f'"Over {info["strike"]}" hasn\'t filled, cancel it now. If it filled, you\'re done - it settles after the game.')


def format_schedule(day: date, items: list) -> str:
    """items: [(game_key, tip_et datetime)] sorted by tip."""
    lines = [f"Tonight's schedule - NBA totals ({day:%a %b} {day.day})"]
    for key, tip in items:
        a, h = key[-6:-3], key[-3:]
        s, e = tip - timedelta(minutes=75), tip - timedelta(minutes=45)
        lines.append(f"{a} @ {h} - tip {t12(tip)} ET; place {t12(s)}-{t12(e)}; cancel by {t12(tip - timedelta(minutes=15))}")
    lines.append("Rule: search-winner formula; bet only when it's 4+ points from Kalshi's line (no-bet games get no ping).")
    lines.append("Each bet gets a ping with the pick and steps ~75 min before tip, then a cancel check 15 min before tip.")
    lines.append("All times ET. I'll message you when each window opens. Research picks, not financial advice.")
    return "\n".join(lines)


# ---------------------------------------------------------------- state / log
def load_state(path: Path | None = None) -> dict:
    path = path or STATE_PATH
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict, path: Path | None = None) -> None:
    path = path or STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def log_sent(kind: str, content: str, now: datetime, path: Path | None = None) -> None:
    path = path or LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"--- {now.isoformat()} {kind}\n{content}\n")


def pick_key(day, game: str) -> str:
    return f"{day}|{game}"


def cancel_key(day, game: str) -> str:
    return f"{day}|{game}|CANCEL"


def schedule_key(day) -> str:
    return f"{day}|SCHEDULE"


# ---------------------------------------------------------------- core
def plan_messages(rows, tips: dict, day, now_et: datetime, state: dict, force_schedule=False):
    """Pure: return [(state_key, kind, text)] to send. `tips` maps game key -> tip datetime (ET)."""
    out = []
    if SEND_SCHEDULE and (force_schedule or now_et.hour >= 17) and schedule_key(day) not in state and tips:
        items = sorted(((k, t) for k, t in tips.items()), key=lambda x: (x[1], x[0]))
        out.append((schedule_key(day), "schedule", format_schedule(day, items)))
    for r in rows:
        if not str(r.get("status", "")).startswith("PLACE NOW") or "[outside" in r["status"]:
            continue
        if r.get("side") is None or pick_key(day, r["game"]) in state:
            continue
        out.append((pick_key(day, r["game"]), "pick", format_pick(r, tips[r["game"]])))
    for g, tip in sorted(tips.items(), key=lambda x: (x[1], x[0])):
        if not SEND_CANCEL:
            break
        info = state.get(pick_key(day, g))
        if not isinstance(info, dict) or "side" not in info:  # only games that got a pick ping
            continue
        if cancel_key(day, g) in state or not (tip - timedelta(minutes=15) <= now_et < tip):
            continue
        out.append((cancel_key(day, g), "cancel", format_cancel(g, tip, info)))
    return out


DK_RUNNER = None  # test hook: a callable run in the snapshot thread instead of loading dk_snapshot.py (None in production)


def start_dk_snapshot():
    """Research log #43: one DraftKings-vs-Kalshi snapshot per notifier run, in a background thread so it can never
    block or delay the Discord pings. Returns the thread or None. Never raises; failures go to logs/dk_snapshot.log."""
    try:
        import threading
        if DK_RUNNER is not None:
            t = threading.Thread(target=DK_RUNNER, name="dk_snapshot", daemon=True)
            t.start()
            return t
        path = ROOT / "reports" / "output" / "nba_dk_gap" / "dk_snapshot.py"
        if not path.exists():
            return None
        spec = importlib.util.spec_from_file_location("dk_snapshot", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["dk_snapshot"] = mod
        spec.loader.exec_module(mod)
        if not mod.key_is_set():
            return None
        t = threading.Thread(target=mod.safe_snapshot, name="dk_snapshot", daemon=True)
        t.start()
        return t
    except BaseException:  # noqa: BLE001
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="send one test message and exit")
    ap.add_argument("--dry-run", action="store_true", help="print messages; send nothing; keep state unchanged")
    ap.add_argument("--force-schedule", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc)

    if a.test:
        msg = "Kalshi-PM notifier connected"
        if a.dry_run:
            print(msg)
            return 0
        try:
            post_discord(load_webhook(), msg)
        except NotifyError as e:
            print(f"ERROR: {e}")
            return 1
        log_sent("test", msg, now)
        print("test message sent")
        return 0

    url = None
    if not a.dry_run:
        try:
            url = load_webhook()
        except NotifyError as e:
            print(f"ERROR: {e}")
            return 1

    picks = _load_picks()
    ET = picks.ET
    now_et = now.astimezone(ET)
    day = now_et.date()
    try:
        rows, _n27, games, _eng = picks.compute_rows(day, now)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR computing picks: {type(e).__name__}")
        return 1
    tips = {k: t.astimezone(ET) for t, k, _ in games}
    state = load_state()
    msgs = plan_messages(rows, tips, day, now_et, state, a.force_schedule and a.dry_run)
    if not msgs:
        print(f"{now_et:%Y-%m-%d %H:%M} ET: nothing to send ({len(games)} games tonight)")
        return 0
    rc = 0
    dk_thread = None
    if not a.dry_run and any(k == "pick" for _, k, _ in msgs):  # a window just opened: one snapshot covers all games
        dk_thread = start_dk_snapshot()
    for skey, kind, text in msgs:
        if a.dry_run:
            print(f"[dry-run {kind}]\n{text}\n")
            continue
        try:
            post_discord(url, text)
        except NotifyError as e:
            print(f"ERROR sending {kind} {skey}: {e}")
            rc = 1
            continue
        if kind == "pick":
            g = skey.split("|")[1]
            state[skey] = {"sent": now.isoformat(), **pick_info(next(r for r in rows if r["game"] == g))}
        else:
            state[skey] = now.isoformat()
        save_state(state)
        log_sent(kind, text, now)
        print(f"sent {kind} {skey}")
    if dk_thread is not None:
        try:
            dk_thread.join(timeout=240)  # after all pings are out; daemon thread, so a hang cannot block exit
        except BaseException:  # noqa: BLE001
            pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
