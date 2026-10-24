"""The dashboard page in a real browser (headless Chrome over the DevTools protocol), for what the JSON tests cannot see:
the script runs without a console error or a Content-Security-Policy violation, nothing overflows a phone-width screen
(390 px, dark mode), and each situation is stated in words: strategy name, LIVE / WATCH-ONLY / STOPPED / NOT RESPONDING,
a mode that is not 'live' or 'watch', real orders under a watch label, an empty database.

Skipped when Node or Chrome is missing (set KPM_TEST_CHROME to the browser binary). The server's clock is frozen, so the
page's ages and countdowns do not depend on how long the suite has been running. Everything is on 127.0.0.1.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import threading
from importlib import resources
from pathlib import Path
from types import SimpleNamespace

import pytest

from kpm.dashboard import server

NOW = 1_791_226_800.0                                  # 2026-10-05T19:00:00Z: the server's frozen clock
PROBE = Path(__file__).resolve().parent / "dashboard_probe.js"
SCHEMA = resources.files("kpm.core").joinpath("schema.sql").read_text(encoding="utf-8")
CANDIDATES = [
    os.environ.get("KPM_TEST_CHROME", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]
CHROME = next((c for c in CANDIDATES if c and Path(c).is_file()), None)
NODE = shutil.which("node")


def node_major() -> int:
    """The probe uses Node's built-in WebSocket client (Node 22 and later)."""
    try:
        out = subprocess.run([NODE, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
        return int(out.lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(not (CHROME and NODE) or node_major() < 22,
                                 reason="needs Node 22+ and a Chrome-family browser (KPM_TEST_CHROME)")]

LATE_CFG = {"strategy": "late_favorite", "mode": "live", "series": "KXBTC15M",
            "rule": {"window_start_s": 780.0, "window_end_s": 840.0, "window_length_s": 900.0,
                     "buy_yes_if_mid_above": "0.60", "buy_no_if_mid_below": "0.40"},
            "contracts": "1", "max_attempts_per_window": 3, "poll_interval_s": 1.0, "max_book_age_s": 2.0,
            "max_loss_usd": "5.00", "max_cost_per_order_usd": "1.00", "max_contracts_per_order": "1", "max_orders_per_minute": 10}
TICKER = "KXBTC15M-26OCT051500-00"


def seed(path: Path, scenario: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    if scenario == "empty":
        conn.commit()
        return

    def run(run_id, strategy, mode, ended=None, reason=None, config=None):
        conn.execute("INSERT INTO bot_runs VALUES (?,?,?,?,?,?,?)",
                     (run_id, strategy, mode, NOW - 7200, ended, reason, json.dumps(config or {})))

    def beat(run_id, age, status="monitoring", **detail):
        conn.execute("INSERT INTO bot_heartbeat VALUES (?,?,?,?)", (run_id, NOW - age, status, json.dumps(
            {"strategy": "x", "status": status, "market_ticker": TICKER, "loss_used_usd": 0.31, "max_loss_usd": 5.0,
             "balance_usd": 28.12, **detail})))

    cols = ("run_id, detected_ts, hour_ticker, strike_ticker, kind, contracts, paper_cost, paper_fees, paper_edge, live, "
            "outcome, live_cost, live_fees, latency_ms, payout, realized_pnl, paper_realized_pnl")

    def opp(run_id, age, kind, ticker, paper_cost, live, outcome, live_cost=None, live_fees=None, realized=None, paper_real=None,
            paper_edge=None, strike=None):
        conn.execute(f"INSERT INTO bot_opportunities ({cols}) VALUES ({','.join('?' * 17)})",
                     (run_id, NOW - age, ticker, strike or ticker, kind, 1, paper_cost, 0.01, paper_edge, live, outcome, live_cost,
                      live_fees, 400.0 if live else None, None, realized, paper_real))

    if scenario in ("late", "ended", "unknown", "stale", "mismatch"):
        mode = {"unknown": "paper_live ", "mismatch": "watch"}.get(scenario, "live")
        ended = NOW - 120 if scenario == "ended" else None
        run("late-1", "late_favorite", mode, ended, "kill switch" if ended else None, LATE_CFG)
        beat("late-1", 200 if scenario == "stale" else 1)
        opp("late-1", 6000, "late favorite YES", "KXBTC15M-A", 0.73, 1, "filled", 0.74, 0.0135, 0.2465, 0.2562)
        opp("late-1", 4200, "late favorite NO", "KXBTC15M-B", 0.95, 1, "filled", 0.96, 0.0027, -0.9627, -0.9533)
        opp("late-1", 2400, "late favorite YES", "KXBTC15M-C", 0.88, 1, "filled", 0.88, 0.0074)
        opp("late-1", 600, "late favorite NO", "KXBTC15M-D", 0.84, 0, "paper_only", paper_real=0.1506)
        conn.execute("INSERT INTO bot_orders (client_order_id, run_id, opportunity_id, ts_sent, ts_ack, ticker, side, action, count, "
                     "limit_price, tif, status, order_id, filled, avg_fill_price, fees, dry_run, response_json) VALUES "
                     "('c1','late-1',1,?,?,?,'yes','buy',1,0.73,'ioc','executed','o1',1,0.74,0.0135,0,?)",
                     (NOW - 6000, NOW - 5999.6, "KXBTC15M-A", json.dumps({"headers": {"KALSHI-ACCESS-KEY": "SECRET-ID"}})))
        conn.execute("INSERT INTO bot_events (run_id, ts, level, kind, message, data_json) VALUES "
                     "('late-1', ?, 'warn', 'error', 'failed with api_key_id=SECRET-ID', NULL)", (NOW - 5000,))
    elif scenario == "pair":
        run("pair-1", "pair_h2", "watch", config={"strategy": "pair_h2", "mode": "watch", "min_edge_usd": "0.001",
                                                  "window_min": 15.0, "strikes_each_side": 2, "poll_interval_s": 1.0,
                                                  "max_loss_usd": "5.00"})
        beat("pair-1", 1, hour_ticker=TICKER, loss_used_usd=0.0)
        # strike 1 ends at +0.0005 (positive, but under the 0.001 minimum edge: NOT an opportunity); strike 2 ends at +0.007
        for i in range(30):
            for strike, edge in (("KXBTCD-26OCT0515-T85699.99", -0.0285 + 0.001 * i), ("KXBTCD-26OCT0515-T85799.99", -0.08 + 0.003 * i)):
                conn.execute("INSERT INTO bot_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", ("pair-1", NOW - 300 + 10 * i, TICKER, strike,
                             "hourly YES + 15m DOWN", 0.55, 10, 0.47, 20, 1.0 - edge, 0.01, edge))
        opp("pair-1", 40, "hourly YES + 15m DOWN", TICKER, 0.95, 0, "paper_only", paper_edge=0.02, strike="KXBTCD-26OCT0515-T85799.99")
    conn.commit()
    conn.close()


@pytest.fixture
def dash(tmp_path, monkeypatch):
    cfg = {"paths": {"db_path": str(tmp_path / "kpm.sqlite"), "report_dir": str(tmp_path / "reports")},
           "trading": {"enabled": False, "max_loss_usd": "5.00", "max_cost_per_order_usd": "1.00", "max_contracts_per_order": "1",
                       "max_orders_per_minute": 10, "max_orders_per_session": 300, "allowed_series": ["KXBTC15M", "KXBTCD"],
                       "kill_switch_file": str(tmp_path / "KILL"),
                       "late_favorite": {"window_start_s": 780, "window_end_s": 840, "up_threshold": "0.60", "down_threshold": "0.40",
                                         "max_attempts": 3, "poll_interval_s": 1.0},
                       "pair": {"window_min": 15, "strikes_each_side": 2, "min_edge_usd": "0.001", "poll_interval_s": 1.0}},
           "dashboard": {"host": "127.0.0.1", "port": 0, "refresh_s": 2}}
    monkeypatch.setattr(server, "settings", lambda: cfg)
    monkeypatch.setattr(server, "time", SimpleNamespace(time=lambda: NOW))
    srv = server.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

    def probe(scenario: str, width: int = 1366, scheme: str = "light", after_js: str = "", query_js: str = "") -> dict:
        db = Path(cfg["paths"]["db_path"])
        if db.exists():
            db.unlink()
        seed(db, scenario)
        out = subprocess.run([NODE, str(PROBE), CHROME, url, str(width), scheme, "4000", "-", after_js, query_js],
                             capture_output=True, text=True, encoding="utf-8", timeout=120)
        assert out.returncode == 0, out.stderr[-600:]
        result = json.loads(out.stdout.strip().splitlines()[-1])
        result["css_hidden"] = set(result["hidden"])
        return result

    probe.kill_file = Path(cfg["trading"]["kill_switch_file"])             # type: ignore[attr-defined]
    yield probe
    srv.shutdown()
    srv.server_close()
    thread.join(5)


def clean(r: dict) -> None:
    """No script error, no CSP violation, nothing leaking out of the viewport (tables scroll inside their own box)."""
    assert r["problems"] == [], r["problems"]
    assert r["layout"]["scrollWidth"] <= r["layout"]["innerWidth"], r["layout"]
    assert r["layout"]["overflowing"] == [], r["layout"]["overflowing"]
    assert "SECRET-ID" not in r["text"] and "KALSHI-ACCESS" not in r["text"]


def test_a_late_favorite_run_is_shown_by_its_strategy_in_words(dash):
    r = dash("late")
    clean(r)
    t = r["text"]
    for needle in ("LIVE", "real orders, real money", "Late favorite bot", "Strategy: late_favorite", "The rule",
                   "Decision window:", "Loss used vs cap", "$0.31 of $5.00", "Kalshi balance", "$28.12", "Trades this run",
                   "4 signals", "3 filled", "Signals", "Settled live trades", "Delay cost", "Detected at", "Filled at",
                   "KXBTC15M-A", "Max orders per run", "300", "api_key_id=[redacted]"):
        assert needle in t, needle
    assert "rule-card" not in r["css_hidden"] and "chart-card" in r["css_hidden"]   # the rule card, not the pair chart
    assert "Edge per strike" not in t                                               # (hidden elements have no inner text)
    assert "LIVE" in r["layout"]["title"] and "Late favorite bot" in r["layout"]["title"]


def test_the_phone_layout_in_dark_mode_does_not_overflow(dash):
    r = dash("late", width=390, scheme="dark")
    clean(r)
    assert r["layout"]["innerWidth"] == 390 and "STOP BOT" in r["text"] and "Late favorite bot" in r["text"]


def test_a_pair_run_shows_the_chart_and_its_minimum_edge(dash):
    r = dash("pair")
    clean(r)
    t = r["text"]
    for needle in ("WATCH-ONLY", "BTC pair bot", "Strategy: pair_h2", "Trading window:", "Min edge", "0.1¢",
                   "above $85,699.99", "above $85,799.99", "Edge per strike", "break-even", "Latest pricing per strike"):
        assert needle in t, needle
    assert "chart-card" not in r["css_hidden"] and "rule-card" in r["css_hidden"]


def test_only_quotes_at_or_above_the_minimum_edge_are_opportunities(dash):
    r = dash("pair", query_js="JSON.stringify({rows: document.querySelectorAll('#latest-body tr').length, "
                              "opp: document.querySelectorAll('#latest-body tr.opp').length, "
                              "marks: document.querySelectorAll('#chart-svg circle.dot.opp').length})")
    clean(r)
    q = json.loads(r["query"])
    assert q["rows"] == 2 and q["opp"] == 1                                # +0.05 cents is under the 0.1 cent minimum
    # only strike 2 reaches the minimum (its edge -0.08 + 0.003 * i is >= 0.001 for the last 3 of 30 polls); strike 1 never does
    assert q["marks"] == 3


def test_hiding_every_strike_says_so_instead_of_blaming_the_trading_window(dash):
    # the legend is rebuilt after every click, so click whatever is still showing, one at a time
    r = dash("pair", after_js="for (let i = 0; i < 4; i++) { const b = document.querySelector('#legend button[aria-pressed=\"true\"]'); "
                              "if (b) b.click(); }")
    clean(r)
    assert "All strikes are hidden" in r["text"] and "Click a strike in the legend to show it." in r["text"]
    assert "No pricing in the last" not in r["text"] and "The bot prices pairs only during the trading window" not in r["text"]


def test_the_chart_can_be_read_with_the_arrow_keys_and_the_values_are_announced(dash):
    keys = ("(() => { const s = document.getElementById('chart-svg'); s.focus(); "
            "s.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowLeft', bubbles: true})); })()")
    r = dash("pair", after_js=keys)
    clean(r)
    assert "above $85,699.99" in r["text"] and "above $85,799.99" in r["text"]


def test_the_stop_button_writes_the_kill_file_and_the_page_shows_where_it_is(dash):
    assert not dash.kill_file.exists()
    stop = "window.confirm = () => true; document.getElementById('stop').click();"      # accept the confirmation dialog
    r = dash("late", after_js=stop)
    clean(r)
    assert dash.kill_file.is_file() and "dashboard STOP button" in dash.kill_file.read_text(encoding="utf-8")
    assert "KILL SWITCH ACTIVE" in r["text"]
    assert f"Kill switch is active ({dash.kill_file} exists)" in r["text"]            # the full path, so it can be found
    assert r["layout"]["title"].startswith("KILLED")


def test_a_stopped_run_is_not_called_live(dash):
    r = dash("ended")
    clean(r)
    t = r["text"]
    assert "STOPPED" in t and "last run was LIVE" in t and "kill switch" in t and "NOT TRADING" in t
    assert "real orders, real money" not in t
    assert r["layout"]["title"].startswith("STOPPED")


def test_a_silent_bot_is_not_called_live(dash):
    r = dash("stale")
    clean(r)
    assert "NOT RESPONDING" in r["text"] and "real orders, real money" not in r["text"] and "No heartbeat for more than" in r["text"]


def test_a_mode_that_is_neither_live_nor_watch_is_called_unknown(dash):
    r = dash("unknown")
    clean(r)
    assert "MODE UNKNOWN" in r["text"] and "no orders sent" not in r["text"] and "neither 'live' nor 'watch'" in r["text"]


def test_real_orders_under_a_watch_label_are_flagged(dash):
    r = dash("mismatch")
    clean(r)
    assert "MODE MISMATCH" in r["text"] and "labelled WATCH-ONLY but 1 real order(s) were sent" in r["text"]
    assert "paper trades only, no orders sent" not in r["text"]


def test_an_empty_database_says_there_is_no_run(dash):
    r = dash("empty")
    clean(r)
    assert "NO BOT RUN" in r["text"] and "No opportunities yet" in r["text"] and "No orders yet" in r["text"]
