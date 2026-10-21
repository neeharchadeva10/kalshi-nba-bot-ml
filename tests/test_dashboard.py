"""Localhost dashboard: JSON API, empty/missing database, kill-switch protection, static files, loopback-only bind,
secret redaction, Fetch Metadata isolation, limits in force, strategy awareness, honest P&L figures, the status of every
trade (settled and won or lost, open, not filled, skipped and why, paper only) and the money figures behind the graphs.

The server's clock is frozen at NOW (the fixture patches ``server.time``), and every fixture timestamp is relative to it,
so no assertion depends on how long the suite has been running.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
from datetime import datetime
from importlib import resources
from pathlib import Path
from types import SimpleNamespace

import pytest

from kpm.dashboard import server

SCHEMA = resources.files("kpm.core").joinpath("schema.sql").read_text(encoding="utf-8")
NOW = 1_791_226_800.0                  # 2026-10-05T19:00:00Z; the server's clock is frozen here
STATIC = Path(server.__file__).resolve().parent / "static"


def make_db(path: Path, with_rows: bool = True) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    if with_rows:
        conn.executemany("INSERT INTO bot_runs VALUES (?,?,?,?,?,?,?)", [
            ("run-old", "pair_h2", "watch", NOW - 7200, NOW - 3600, "kill switch", json.dumps({"max_loss_usd": 5})),
            ("run-new", "pair_h2", "live", NOW - 900, None, None,
             json.dumps({"max_loss_usd": 5, "api_key_id": "SHOULD-NOT-SHOW"})),
        ])
        conn.execute("INSERT INTO bot_heartbeat VALUES (?,?,?,?)", (
            "run-new", NOW - 1, "monitoring",
            json.dumps({"hour_ticker": "KXBTC15M-26OCT051500-00", "loss_used_usd": 0.25, "balance_usd": 24.5})))
        quotes = []
        for i, ts in enumerate((NOW - 600, NOW - 300, NOW - 30)):
            for strike, edge in (("KXBTCD-26OCT0515-T84999.99", -0.03 + 0.04 * (i == 2)), ("KXBTCD-26OCT0515-T85249.99", -0.08)):
                quotes.append(("run-new", ts, "KXBTC15M-26OCT051500-00", strike, "hourly YES + 15m DOWN",
                               0.55, 10, 0.47, 20, 1.02, 0.01, edge))
        quotes.append(("run-new", NOW - 3600, "KXBTC15M-26OCT051400-00", "KXBTCD-26OCT0514-T84999.99",
                       "15m UP + hourly NO", 0.5, 5, 0.52, 5, 1.02, 0.01, -0.03))       # outside the 30-minute window
        quotes.append(("run-old", NOW - 60, "KXBTC15M-26OCT051500-00", "KXBTCD-26OCT0515-T84999.99",
                       "hourly YES + 15m DOWN", 0.5, 5, 0.5, 5, 1.0, 0.01, -0.01))      # another run
        conn.executemany("INSERT INTO bot_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", quotes)
        cols = ("run_id, detected_ts, hour_ticker, strike_ticker, kind, contracts, paper_cost, paper_fees, paper_edge, "
                "live, outcome, live_cost, live_fees, latency_ms, payout, realized_pnl, paper_realized_pnl")
        conn.executemany(f"INSERT INTO bot_opportunities ({cols}) VALUES ({','.join('?' * 17)})", [
            ("run-old", NOW - 5000, "H", "KXBTCD-26OCT0514-T84999.99", "hourly YES + 15m DOWN", 1, 0.96, 0.02, 0.02,
             0, "paper_only", None, None, None, None, None, 0.02),
            ("run-new", NOW - 100, "H", "KXBTCD-26OCT0515-T84999.99", "hourly YES + 15m DOWN", 1, 0.95, 0.03, 0.02,
             1, "both_filled", 0.96, 0.03, 850.0, 1.0, 0.01, 0.02),
            ("run-new", NOW - 50, "H", "KXBTCD-26OCT0515-T85249.99", "15m UP + hourly NO", 1, 0.97, 0.02, 0.01,
             1, "one_leg_unwound", 0.50, 0.01, 1200.0, None, -0.02, None),
        ])
        ocols = ("client_order_id, run_id, opportunity_id, ts_sent, ts_ack, ticker, side, action, count, limit_price, "
                 "tif, status, order_id, filled, avg_fill_price, fees, dry_run, response_json")
        conn.executemany(f"INSERT INTO bot_orders ({ocols}) VALUES ({','.join('?' * 18)})", [
            ("c-old", "run-old", 1, NOW - 5000, NOW - 4999.9, "KXBTCD-26OCT0514-T84999.99", "yes", "buy", 1, 0.5,
             "ioc", "dry_run", None, 0, None, None, 1, None),
            ("c-1", "run-new", 2, NOW - 100, NOW - 99.6, "KXBTCD-26OCT0515-T84999.99", "yes", "buy", 1, 0.55,
             "ioc", "executed", "o1", 1, 0.55, 0.02, 0, json.dumps({"order": {"status": "executed"}, "token": "x"})),
            ("c-2", "run-new", 2, NOW - 99, NOW - 98.5, "KXBTC15M-26OCT051500-00", "no", "buy", 1, 0.41,
             "ioc", "executed", "o2", 1, 0.41, 0.01, 0, None),
        ])
        conn.executemany("INSERT INTO bot_events (run_id, ts, level, kind, message, data_json) VALUES (?,?,?,?,?,?)", [
            ("run-old", NOW - 5000, "info", "start", "old run started", None),
            (None, NOW - 950, "warn", "start", "no run yet", None),
            ("run-new", NOW - 900, "info", "start", "watching", json.dumps({"caps": {"max_loss_usd": 5}})),
            ("run-new", NOW - 100, "info", "opportunity", "pair found", None),
            ("run-new", NOW - 10, "error", "error", "order rejected", json.dumps({"reason": "insufficient"})),
        ])
        conn.executemany("INSERT INTO audit_log (ts, component, entity_type, entity_id, action, details) "
                         "VALUES (?,?,?,?,?,?)", [
            (1, "analysis.crypto_final_minute", "market", "M1", "trades", "{}"),
            (2, "analysis.crypto_final_minute", "market", "M1", "trades", "{}"),   # duplicate: counted once
            (3, "analysis.crypto_final_minute", "market", "M2", "trades", "{}"),
            (4, "analysis.crypto_cross_bounds", "market", "M3", "pair", "{}"),
            (5, "analysis.crypto_final_minute", None, None, "analysis", "{}"),
        ])
    conn.commit()
    conn.close()
    return path


def write_research(report_dir: Path) -> None:
    d = report_dir / "mechanical_edges"
    d.mkdir(parents=True)
    (d / "h1_summary.json").write_text('{"markets_with_data": 11825, "observations": 52185, "ratio": NaN, '
                                       '"buckets_passing": []}', encoding="utf-8")
    (d / "h1_final_minute_verdict.csv").write_text(
        "sec_b,price_b,early_c,oos_mean_c,oos_markets,pass_size,PASS\n"
        "0-5s,80-90c,-6.17,4.0,39,False,False\n"
        "5-15s,90-95c,,nan,255,True,False\n", encoding="utf-8")
    (d / "h2_summary.json").write_text(json.dumps({"pairs": 2943, "by_split": []}), encoding="utf-8")
    (d / "h2_cross_bounds_summary.csv").write_text(
        "split,hours,violations,mean_profit_c,persist_next_min\n"
        "early,180,10,0.351,0.0\n"
        "all,783,69,0.627,0.2029\n", encoding="utf-8")
    (d / "h4_lip_summary.json").write_text(json.dumps({"active_programs": 5695, "checked_books": 300}), encoding="utf-8")


def db_exec(path: Path, sql: str, rows: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    conn.executemany(sql, rows)
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server, "time", SimpleNamespace(time=lambda: NOW))


@pytest.fixture
def cfg(tmp_path: Path) -> dict:
    return {
        "paths": {"db_path": str(tmp_path / "kpm.sqlite"), "report_dir": str(tmp_path / "reports")},
        "trading": {
            "enabled": False, "max_loss_usd": 5.0, "max_cost_per_order_usd": 1.0, "max_contracts_per_order": 1,
            "max_orders_per_minute": 10, "allowed_series": ["KXBTC15M", "KXBTCD"],
            "kill_switch_file": str(tmp_path / "flags" / "KILL"),
            "pair": {"window_min": 15, "strikes_each_side": 2, "min_edge_usd": 0.001, "poll_interval_s": 1.0,
                     "contracts": 1},
        },
        "dashboard": {"host": "127.0.0.1", "port": 8765, "refresh_s": 2},
    }


@pytest.fixture
def live(cfg: dict, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server, "settings", lambda: cfg)
    srv = server.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(5)


def call(srv, method: str, path: str, headers: dict | None = None, host: str | None = None):
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, headers={"Host": host or f"127.0.0.1:{port}", **(headers or {})})
        r = conn.getresponse()
        return r.status, r, r.read()
    finally:
        conn.close()


def get_json(srv, path: str) -> dict:
    status, resp, body = call(srv, "GET", path)
    assert status == 200, body
    assert resp.getheader("Content-Type").startswith("application/json")
    assert resp.getheader("Cache-Control") == "no-store"
    return json.loads(body)


def test_status_has_latest_run_heartbeat_caps_and_kill_state(live, cfg):
    make_db(Path(cfg["paths"]["db_path"]))
    d = get_json(live, "/api/status")
    assert {"run", "heartbeat", "caps", "kill_switch_active", "server_time"} <= d.keys()
    assert d["run"]["run_id"] == "run-new" and d["run"]["mode"] == "live" and d["strategy"] == "pair_h2"
    assert d["run"]["config"]["api_key_id"] == "[redacted]"           # never echo anything that looks like a secret
    assert d["heartbeat"]["status"] == "monitoring"
    assert d["heartbeat"]["detail"]["hour_ticker"] == "KXBTC15M-26OCT051500-00"
    assert d["heartbeat"]["age_s"] == pytest.approx(1.0)
    caps = d["caps"]
    assert caps["max_loss_usd"] == 5.0 and caps["max_cost_per_order_usd"] == 1.0
    assert caps["max_contracts_per_order"] == 1 and caps["enabled"] is False
    assert caps["allowed_series"] == ["KXBTC15M", "KXBTCD"] and caps["pair"]["window_min"] == 15
    assert d["kill_switch_active"] is False
    assert d["server_time"] == NOW
    assert d["latest_quote"]["hour_ticker"] == "KXBTC15M-26OCT051500-00"
    assert d["stats"]["run"]["opportunities"] == 2 and d["stats"]["run"]["live_orders"] == 2
    assert d["stats"]["all"]["opportunities"] == 3
    assert d["stats"]["run"]["realized_pnl"] == pytest.approx(-0.01)
    assert d["stats"]["run"]["last_ts"] == NOW - 50
    assert d["dashboard"]["refresh_s"] == 2


def test_quotes_are_latest_run_within_window_oldest_first(live, cfg):
    make_db(Path(cfg["paths"]["db_path"]))
    d = get_json(live, "/api/quotes?minutes=30")
    assert d["run_id"] == "run-new"
    ts = [r["ts"] for r in d["rows"]]
    assert len(ts) == 6 and ts == sorted(ts)                            # 3 polls x 2 strikes, not the hour-old one
    assert min(ts) >= NOW - 30 * 60
    assert {"strike_ticker", "kind", "cost", "fees", "edge", "leg_a_ask", "leg_b_ask"} <= d["rows"][0].keys()
    assert len(get_json(live, "/api/quotes?minutes=6")["rows"]) == 4
    assert len(get_json(live, f"/api/quotes?since={NOW - 60}")["rows"]) == 2
    assert len(get_json(live, "/api/quotes?minutes=30&run=all")["rows"]) == 7
    assert get_json(live, "/api/quotes?minutes=abc")["minutes"] == 30   # bad input falls back to the default


def test_lists_are_newest_first_for_latest_run_or_all(live, cfg):
    make_db(Path(cfg["paths"]["db_path"]))
    opp = get_json(live, "/api/opportunities?limit=100")
    assert opp["run_id"] == "run-new"
    assert [r["detected_ts"] for r in opp["rows"]] == sorted((r["detected_ts"] for r in opp["rows"]), reverse=True)
    both = next(r for r in opp["rows"] if r["outcome"] == "both_filled")
    assert both["live_edge"] == pytest.approx(0.01) and both["edge_change"] == pytest.approx(-0.01)
    assert both["fees_pending"] is False
    unwound = next(r for r in opp["rows"] if r["outcome"] == "one_leg_unwound")
    assert unwound["live_edge"] is None
    assert len(get_json(live, "/api/opportunities?run=all")["rows"]) == 3
    assert len(get_json(live, "/api/opportunities?limit=1")["rows"]) == 1

    orders = get_json(live, "/api/orders")
    assert [r["client_order_id"] for r in orders["rows"]] == ["c-2", "c-1"]
    assert orders["rows"][1]["response"]["token"] == "[redacted]"
    assert "response_json" not in orders["rows"][0]
    assert len(get_json(live, "/api/orders?run=all")["rows"]) == 3

    events = get_json(live, "/api/events?limit=200")
    msgs = [r["message"] for r in events["rows"]]
    assert msgs == ["order rejected", "pair found", "watching", "no run yet"]   # newest first, run-less one kept
    assert events["rows"][0]["data"] == {"reason": "insufficient"}
    assert len(get_json(live, "/api/events?run=all")["rows"]) == 5
    # run-old ended an hour before the run-less event, so that event is not shown with it
    assert [r["message"] for r in get_json(live, "/api/events?run=run-old")["rows"]] == ["old run started"]


def test_research_summaries_rows_and_progress(live, cfg):
    make_db(Path(cfg["paths"]["db_path"]))
    write_research(Path(cfg["paths"]["report_dir"]))
    d = get_json(live, "/api/research")
    assert {"h1_summary", "h1_verdict", "h2_summary", "h2_by_split", "h4_summary", "progress"} <= d.keys()
    assert d["h1_summary"]["markets_with_data"] == 11825 and d["h1_summary"]["ratio"] is None   # NaN -> null
    assert d["h1_verdict"][0] == {"sec_b": "0-5s", "price_b": "80-90c", "early_c": -6.17, "oos_mean_c": 4.0,
                                  "oos_markets": 39, "pass_size": False, "PASS": False}
    assert d["h1_verdict"][1]["early_c"] is None and d["h1_verdict"][1]["oos_mean_c"] is None
    assert [r["split"] for r in d["h2_by_split"]] == ["early", "all"] and d["h2_by_split"][1]["violations"] == 69
    assert d["h4_summary"]["active_programs"] == 5695
    p = d["progress"]
    assert p["h1_final_minute_trades"]["done"] == 2 and p["h1_final_minute_trades"]["total"] == 11835
    assert p["h2_cross_bounds_pairs"]["done"] == 1 and p["h2_cross_bounds_pairs"]["total"] == 2952


@pytest.mark.parametrize("db_state", ["missing", "no_tables"])
def test_empty_or_missing_database_gives_empty_results(live, cfg, db_state):
    db_path = Path(cfg["paths"]["db_path"])
    if db_state == "no_tables":
        sqlite3.connect(db_path).close()
    st = get_json(live, "/api/status")
    assert st["run"] is None and st["heartbeat"] is None and st["kill_switch_active"] is False
    assert st["strategy"] is None and st["other_live_runs"] == []
    assert st["caps"]["max_loss_usd"] == 5.0
    assert get_json(live, "/api/quotes")["rows"] == []
    for path in ("/api/opportunities", "/api/orders", "/api/events", "/api/opportunities?run=all"):
        assert get_json(live, path)["rows"] == []
    for path in ("/api/performance", "/api/performance?run=all"):          # the money figures: an empty answer, never an error
        p = get_json(live, path)
        assert p["trades"] == [] and p["open"] == [] and p["view"] == "real" and p["summary"]["settled"] == 0
        assert p["summary"]["pnl"] is None and p["summary"]["signals"] == 0 and p["base"] == {"pnl": 0.0, "paper_pnl": 0.0}
    r = get_json(live, "/api/research")
    assert r["h1_summary"] is None and r["h1_verdict"] == [] and r["h2_by_split"] == [] and r["h4_summary"] is None
    if db_state == "missing":
        assert r["progress"]["h1_final_minute_trades"]["done"] is None
        assert not db_path.exists()                                    # read-only: never creates the database


def test_the_database_is_opened_read_only(tmp_path):
    path = make_db(tmp_path / "ro.sqlite")
    conn = server._open_db(path)
    assert conn is not None
    assert conn.execute("SELECT COUNT(*) FROM bot_runs").fetchone()[0] == 2
    for sql in ("CREATE TABLE x (a)", "DELETE FROM bot_runs", "INSERT INTO bot_events (ts, level, kind, message) "
                "VALUES (1, 'info', 'start', 'm')"):
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute(sql)
    conn.close()
    assert server._open_db(tmp_path / "nope.sqlite") is None and server._open_db(None) is None


def test_kill_needs_post_and_header_then_creates_file(live, cfg):
    kill = Path(cfg["trading"]["kill_switch_file"])
    status, _, _ = call(live, "POST", "/api/kill")
    assert status == 403 and not kill.exists()
    status, _, _ = call(live, "POST", "/api/kill", {"X-KPM-Kill": "0"})
    assert status == 403 and not kill.exists()
    status, resp, _ = call(live, "GET", "/api/kill", {"X-KPM-Kill": "1"})
    assert status == 405 and resp.getheader("Allow") == "POST" and not kill.exists()
    status, _, _ = call(live, "POST", "/api/kill", {"X-KPM-Kill": "1", "Origin": "http://evil.example"})
    assert status == 403 and not kill.exists()
    status, _, _ = call(live, "POST", "/api/kill", {"X-KPM-Kill": "1", "Sec-Fetch-Site": "cross-site"})
    assert status == 403 and not kill.exists()
    status, resp, _ = call(live, "OPTIONS", "/api/kill", {"Origin": "http://evil.example",
                                                         "Access-Control-Request-Method": "POST",
                                                         "Access-Control-Request-Headers": "x-kpm-kill"})
    assert status in (403, 405) and resp.getheader("Access-Control-Allow-Origin") is None
    assert resp.getheader("Access-Control-Allow-Headers") is None and not kill.exists()

    port = live.server_address[1]
    status, _, body = call(live, "POST", "/api/kill", {"X-KPM-Kill": "1", "Origin": f"http://127.0.0.1:{port}",
                                                       "Sec-Fetch-Site": "same-origin"})
    assert status == 200 and json.loads(body)["ok"] is True
    assert json.loads(body)["kill_switch_path"] == str(kill)            # where it was written, in full
    text = kill.read_text(encoding="utf-8")
    assert "dashboard STOP button" in text and re.search(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", text)
    d = get_json(live, "/api/status")
    assert d["kill_switch_active"] is True and d["kill_switch_path"] == str(kill)


@pytest.mark.parametrize("headers", [
    {"Origin": "null"}, {"Sec-Fetch-Site": "same-site"}, {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"},
    {"Origin": "https://127.0.0.1:{port}"}, {"Origin": "http://127.0.0.1:1"}, {"Origin": "http://localhost.evil.example:{port}"},
])
def test_kill_refuses_more_cross_origin_shapes(live, cfg, headers):
    port = live.server_address[1]
    headers = {k: v.format(port=port) for k, v in headers.items()}
    status, _, _ = call(live, "POST", "/api/kill", {"X-KPM-Kill": "1", **headers})
    assert status == 403 and not Path(cfg["trading"]["kill_switch_file"]).exists()


@pytest.mark.parametrize("host", ["evil.example", "evil.example:{port}", "127.0.0.1:1", "localhost.evil.example:{port}",
                                  "127.0.0.1:{port}@evil.example", ""])
def test_foreign_host_header_is_rejected(live, cfg, host):
    make_db(Path(cfg["paths"]["db_path"]))
    port = live.server_address[1]
    host = host.format(port=port)
    for path in ("/api/status", "/api/performance", "/api/orders", "/", "/static/app.js"):
        status, _, body = call(live, "GET", path, host=host or " ")
        assert status in (400, 403), (host, path)
        assert b"run-new" not in body
    status, _, _ = call(live, "POST", "/api/kill", {"X-KPM-Kill": "1"}, host=host or " ")
    assert status in (400, 403) and not Path(cfg["trading"]["kill_switch_file"]).exists()


def test_localhost_names_are_accepted(live):
    port = live.server_address[1]
    for host in (f"localhost:{port}", f"LOCALHOST:{port}", f"[::1]:{port}", f"127.0.0.1:{port}"):
        status, _, _ = call(live, "GET", "/api/status", host=host)
        assert status == 200, host


def test_host_allowed_parsing():
    assert server.host_allowed("127.0.0.1:8765", 8765)
    assert server.host_allowed("localhost:8765", 8765)
    assert server.host_allowed("[::1]:8765", 8765)
    assert server.host_allowed("127.0.0.1", 80)
    assert not server.host_allowed("127.0.0.1", 8765)
    assert not server.host_allowed("127.0.0.1:8766", 8765)
    assert not server.host_allowed("::1:8765", 8765)
    assert not server.host_allowed("[::1]x:8765", 8765)
    assert not server.host_allowed(None, 8765)


@pytest.mark.parametrize("port_s", ["²", "８７６５", "٨٧٦٥", "87 65", "+8765", "8765²"])
def test_a_non_ascii_port_is_a_refusal_not_a_server_error(live, port_s):
    assert server.host_allowed(f"127.0.0.1:{port_s}", 8765) is False         # no ValueError from int()
    status, _, body = call(live, "GET", "/api/status", host=f"127.0.0.1:{port_s}".encode("utf-8").decode("latin-1"))
    assert status == 403, body


def test_static_files_served_without_listing_or_traversal(live):
    status, resp, body = call(live, "GET", "/")
    assert status == 200 and resp.getheader("Content-Type").startswith("text/html") and b"STOP BOT" in body
    assert "script-src 'self'" in resp.getheader("Content-Security-Policy")
    status, resp, _ = call(live, "GET", "/static/app.js?v=1")
    assert status == 200 and resp.getheader("Content-Type").startswith("text/javascript")
    status, resp, _ = call(live, "GET", "/static/style.css")
    assert status == 200 and resp.getheader("Content-Type").startswith("text/css")
    for path in ("/static/../server.py", "/static/..%2fserver.py", "/static/%2e%2e%2fserver.py", "/static/..%5cserver.py",
                 "/static/..\\server.py", "/static/%2e%2e/%2e%2e/core/config.py", "/static/", "/static",
                 "/static/./index.html", "/static/INDEX.HTML", "/static/index.html%00.js", "//evil.example/static/app.js",
                 "/static/index.html::$DATA", "/server.py"):
        status, _, body = call(live, "GET", path)
        assert status in (400, 403, 404), path
        assert b"BaseHTTPRequestHandler" not in body and b"lru_cache" not in body, path


def test_refuses_to_bind_anything_but_loopback(cfg, monkeypatch):
    monkeypatch.setattr(server, "settings", lambda: cfg)
    for host in ("0.0.0.0", "::", "192.168.1.10", "", "example.com"):
        with pytest.raises(ValueError):
            server.make_server(host, 0)
    with pytest.raises(ValueError):
        server.serve(host="0.0.0.0", port=0)
    cfg["dashboard"]["host"] = "0.0.0.0"
    with pytest.raises(ValueError):
        server.make_server(None, 0)
    srv = server.make_server("localhost", 0)
    try:
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.server_close()


# ============================================================================================ Fetch Metadata isolation
@pytest.fixture
def work_counter(monkeypatch):
    """Counts database opens: a refused request must not cause any."""
    opened = []
    real = server._open_db
    monkeypatch.setattr(server, "_open_db", lambda p: opened.append(p) or real(p))
    return opened


@pytest.mark.parametrize("site", ["cross-site", "same-site", "CROSS-SITE"])
@pytest.mark.parametrize("path", ["/api/status", "/api/quotes?run=all&minutes=240", "/api/opportunities", "/api/orders",
                                  "/api/performance", "/api/performance?run=all", "/api/events", "/api/research",
                                  "/static/app.js", "/static/style.css", "/"])
def test_a_cross_site_get_is_refused_before_any_work(live, cfg, work_counter, site, path):
    make_db(Path(cfg["paths"]["db_path"]))
    status, _, body = call(live, "GET", path, {"Sec-Fetch-Site": site, "Sec-Fetch-Mode": "no-cors",
                                               "Sec-Fetch-Dest": "image"})
    assert status == 403 and b"run-new" not in body
    assert work_counter == []                                           # no query ran for the cross-site page


@pytest.mark.parametrize("site", ["same-origin", "none", ""])
def test_same_origin_typed_urls_and_plain_clients_are_served(live, cfg, site):
    make_db(Path(cfg["paths"]["db_path"]))
    headers = {"Sec-Fetch-Site": site} if site else {}
    for path in ("/api/status", "/api/events", "/api/performance", "/static/app.js", "/"):
        status, _, _ = call(live, "GET", path, headers)
        assert status == 200, (site, path)


def test_following_a_link_to_the_page_is_allowed_but_nothing_else_cross_site(live, cfg):
    make_db(Path(cfg["paths"]["db_path"]))
    nav = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    assert call(live, "GET", "/", nav)[0] == 200                           # a link from another page to the dashboard
    for path in ("/api/status", "/api/performance", "/static/app.js", "/api/kill"):
        assert call(live, "GET", path, nav)[0] in (403, 405), path
    assert call(live, "GET", "/", {**nav, "Sec-Fetch-Dest": "iframe"})[0] == 403    # but not framed
    assert call(live, "GET", "/", {**nav, "Sec-Fetch-Mode": "no-cors"})[0] == 403
    assert call(live, "POST", "/", nav)[0] in (403, 405)


def test_cross_site_options_and_posts_never_reach_a_handler(live, cfg, work_counter):
    for method in ("OPTIONS", "POST", "PUT", "DELETE"):
        status, resp, _ = call(live, method, "/api/status", {"Sec-Fetch-Site": "cross-site", "Origin": "http://evil.example"})
        assert status == 403 and resp.getheader("Access-Control-Allow-Origin") is None
    assert work_counter == []


# ============================================================================================ secret redaction
SECRET_KEYS = ["KALSHI-ACCESS-KEY", "kalshi-access-signature", "KALSHI-ACCESS-TIMESTAMP", "api-key", "X-API-Key", "api_key_id", "apiKey", "access_key",
               "accessKey", "Access-Key", "credentials", "credential", "Authorization", "authorization", "Bearer",
               "bearer_token", "token", "secret", "password", "passphrase", "private_key", "key_id", "keyId", "key_path",
               "signature", "cookie", "Set-Cookie", "headers", "key", "pem", "auth_header"]


@pytest.mark.parametrize("name", SECRET_KEYS)
def test_secret_looking_keys_are_redacted_at_any_depth(name):
    out = server._parse_json(json.dumps({name: "abc-123", "ok": 1, "nested": {"deep": [{name: "abc-123", "n": 2}]}}))
    assert out[name] == "[redacted]" and out["ok"] == 1
    assert out["nested"]["deep"][0][name] == "[redacted]" and out["nested"]["deep"][0]["n"] == 2
    assert "abc-123" not in json.dumps(out)


@pytest.mark.parametrize("name", ["market_ticker", "hour_ticker", "loss_used_usd", "max_loss_usd", "balance_usd", "last_error",
                                  "strategy", "mode", "status", "kill_switch", "key_fp", "client_order_id", "order_id",
                                  "keys", "window_end_ts", "strike_ticker", "outcome", "price", "count"])
def test_ordinary_keys_are_not_redacted(name):
    assert server._parse_json(json.dumps({name: "value-1"})) == {name: "value-1"}


def test_a_list_of_header_pairs_is_redacted():
    out = server._parse_json(json.dumps({"request": [["KALSHI-ACCESS-KEY", "id-1"], ["Content-Type", "application/json"],
                                                     ["KALSHI-ACCESS-SIGNATURE", "sig-1"]]}))
    assert out["request"] == [["KALSHI-ACCESS-KEY", "[redacted]"], ["Content-Type", "application/json"],
                              ["KALSHI-ACCESS-SIGNATURE", "[redacted]"]]


def test_pem_text_is_redacted_wherever_it_appears():
    pem = "-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEIA0123456789abcdef\n-----END PRIVATE KEY-----"
    out = server._parse_json(json.dumps({"note": "key follows " + pem + " end", "list": [pem], "x": "-----BEGIN RSA PRIVATE KEY-----\nAAAA"}))
    blob = json.dumps(out)
    assert "MC4CAQ" not in blob and "AAAA" not in blob and "BEGIN" not in blob
    assert out["note"].startswith("key follows ") and out["note"].endswith(" end")
    assert server._scrub_text(pem) == "[redacted]"


@pytest.mark.parametrize("text,gone", [
    ("{'api_key_id': 'abc-123', 'x': 1}", "abc-123"),                               # a Python repr, not JSON
    ('KALSHI-ACCESS-KEY: abc-123', "abc-123"),
    ("sent headers KALSHI-ACCESS-SIGNATURE=c2lnbmF0dXJl and more", "c2lnbmF0dXJl"),
    ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
    ("call failed with Bearer abc.def.ghi", "abc.def.ghi"),
    ("password=hunter2&user=x", "hunter2"),
    ("url https://x/y?api_key=SECRET123&z=1", "SECRET123"),
    ("access_key: 'AKIA0123'", "AKIA0123"),
    ('{"private_key": "xyz",}', "xyz"),                                             # invalid JSON: a trailing comma
])
def test_text_that_is_not_json_is_scrubbed_not_returned_verbatim(text, gone):
    out = server._parse_json(text)
    assert isinstance(out, str) and gone not in out and "[redacted]" in out


def test_non_json_columns_are_scrubbed_through_the_api(live, cfg):
    path = make_db(Path(cfg["paths"]["db_path"]))
    db_exec(path, "INSERT INTO bot_events (run_id, ts, level, kind, message, data_json) VALUES (?,?,?,?,?,?)", [
        ("run-new", NOW - 5, "error", "error", "failed: api_key_id=abc-123", "{'KALSHI-ACCESS-KEY': 'abc-123', 'x': 1}")])
    db_exec(path, "UPDATE bot_runs SET config_json = ? WHERE run_id = 'run-new'", [("{'api_key_id': 'abc-123'}",)])
    ev = get_json(live, "/api/events")["rows"][0]
    assert "abc-123" not in json.dumps(ev) and "[redacted]" in ev["data"] and "[redacted]" in ev["message"]
    assert "abc-123" not in json.dumps(get_json(live, "/api/status")["run"])


def test_kalshi_access_key_in_every_json_column_is_redacted_through_the_api(live, cfg):
    path = make_db(Path(cfg["paths"]["db_path"]))
    leak = json.dumps({"headers": {"KALSHI-ACCESS-KEY": "id-LEAK"}, "KALSHI-ACCESS-KEY": "id-LEAK",
                       "msg": "KALSHI-ACCESS-KEY: id-LEAK"})
    db_exec(path, "UPDATE bot_runs SET config_json = ? WHERE run_id = 'run-new'", [(leak,)])
    db_exec(path, "UPDATE bot_heartbeat SET detail_json = ? WHERE run_id = 'run-new'", [(leak,)])
    db_exec(path, "UPDATE bot_orders SET response_json = ? WHERE client_order_id = 'c-1'", [(leak,)])
    db_exec(path, "UPDATE bot_events SET data_json = ? WHERE message = 'watching'", [(leak,)])
    for url in ("/api/status", "/api/orders", "/api/events"):
        assert "id-LEAK" not in json.dumps(get_json(live, url)), url


# ============================================================================================ limits and strategy
def test_numeric_strings_in_the_limits_become_numbers(live, cfg):
    cfg["trading"].update(max_loss_usd="5.00", max_cost_per_order_usd="1.00", max_contracts_per_order="1",
                          max_open_exposure_usd="3.00", max_orders_per_minute=10, max_orders_per_session=300,
                          max_clock_skew_s=2.0, min_seconds_to_close=20)
    cfg["trading"]["pair"].update(min_edge_usd="0.001", contracts="1", max_book_age_s="2.0", stop_before_close_s=20)
    cfg["trading"]["late_favorite"] = {"window_start_s": 780, "window_end_s": 840, "up_threshold": "0.60",
                                       "down_threshold": "0.40", "contracts": "1", "max_attempts": 3}
    make_db(Path(cfg["paths"]["db_path"]), with_rows=False)                 # no run: the limits come from the file
    c = get_json(live, "/api/status")["caps"]
    assert c["max_loss_usd"] == 5.0 and isinstance(c["max_loss_usd"], float)
    assert c["max_cost_per_order_usd"] == 1.0 and c["max_contracts_per_order"] == 1 and isinstance(c["max_contracts_per_order"], int)
    assert c["max_open_exposure_usd"] == 3.0 and c["max_orders_per_session"] == 300 and c["min_seconds_to_close"] == 20
    assert c["pair"]["min_edge_usd"] == 0.001 and c["pair"]["contracts"] == 1 and c["pair"]["max_book_age_s"] == 2.0
    assert c["late_favorite"]["up_threshold"] == 0.6 and c["late_favorite"]["down_threshold"] == 0.4
    assert c["late_favorite"]["window_start_s"] == 780 and c["late_favorite"]["max_attempts"] == 3
    assert c["enabled"] is False and c["allowed_series"] == ["KXBTC15M", "KXBTCD"]


@pytest.mark.parametrize("given,expected", [
    ("5.00", 5.0), ("5", 5), ("0.001", 0.001), (" 1 ", 1), ("-2.5", -2.5), ("1e3", 1000.0), (7, 7), (0.5, 0.5),
    (True, True), (None, None), ("abc", "abc"), ("", ""), ("5,00", "5,00"), (["5"], ["5"]), ("nan", "nan"), ("inf", "inf"),
    (float("nan"), None), ("١٢", "١٢")])
def test_as_number(given, expected):
    out = server._as_number(given)
    assert out == expected and type(out) is type(expected)


def test_the_limits_come_from_the_run_and_the_file_differences_are_reported(live, cfg):
    path = make_db(Path(cfg["paths"]["db_path"]))
    cfg["trading"]["max_loss_usd"] = "5.00"
    run_cfg = {"max_loss_usd": "3.00", "max_cost_per_order_usd": "1.00", "max_contracts_per_order": "1", "max_orders_per_minute": 10,
               "min_edge_usd": "0.002", "window_min": 15.0, "poll_interval_s": 1.0, "kill_switch_file": cfg["trading"]["kill_switch_file"]}
    db_exec(path, "UPDATE bot_runs SET config_json = ? WHERE run_id = 'run-new'", [(json.dumps(run_cfg),)])
    d = get_json(live, "/api/status")
    assert d["caps"]["max_loss_usd"] == 3.0 and d["caps_source"] == "run"           # the limit the bot actually runs with
    assert d["caps"]["pair"]["min_edge_usd"] == 0.002 and d["caps"]["max_cost_per_order_usd"] == 1.0
    assert {"key": "max_loss_usd", "run": 3.0, "config": 5.0} in d["caps_drift"]
    assert {"key": "min_edge_usd", "run": 0.002, "config": 0.001} in d["caps_drift"]
    assert not [x for x in d["caps_drift"] if x["key"] in ("max_cost_per_order_usd", "max_orders_per_minute", "kill_switch_file")]


def test_a_different_kill_switch_file_in_the_run_is_reported(live, cfg, tmp_path):
    path = make_db(Path(cfg["paths"]["db_path"]))
    db_exec(path, "UPDATE bot_runs SET config_json = ? WHERE run_id = 'run-new'",
            [(json.dumps({"kill_switch_file": str(tmp_path / "elsewhere" / "STOP")}),)])
    d = get_json(live, "/api/status")
    assert d["kill_switch_path"] == str(Path(cfg["trading"]["kill_switch_file"]))        # STOP writes the file from the config
    assert [x["key"] for x in d["caps_drift"]] == ["kill_switch_file"]
    assert d["caps_drift"][0]["run"] == str(tmp_path / "elsewhere" / "STOP")


def test_without_a_recorded_config_the_file_is_the_source(live, cfg):
    make_db(Path(cfg["paths"]["db_path"]), with_rows=False)
    d = get_json(live, "/api/status")
    assert d["caps_source"] == "config" and d["caps_drift"] == [] and d["caps"]["max_loss_usd"] == 5.0


def test_the_kill_switch_path_is_absolute_and_a_relative_one_sits_under_the_project(live, cfg):
    cfg["trading"]["kill_switch_file"] = "data/KILL"
    d = get_json(live, "/api/status")
    assert d["kill_switch_file"] == "data/KILL"
    assert Path(d["kill_switch_path"]).is_absolute() and d["kill_switch_path"].replace("\\", "/").endswith("/data/KILL")


def test_the_settings_file_is_re_read_when_it_changes(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    f = root / "config" / "settings.yaml"
    f.write_text("a: 1\n", encoding="utf-8")
    cleared = []

    def fake_settings():
        return {"ok": True}

    fake_settings.cache_clear = lambda: cleared.append(1)          # type: ignore[attr-defined]
    monkeypatch.setattr(server, "_config", SimpleNamespace(settings=fake_settings))
    monkeypatch.setattr(server, "PROJECT_ROOT", root)
    monkeypatch.setattr(server, "_cfg_stamp", None)
    server.settings()
    assert len(cleared) == 1                                       # first read
    server.settings()
    server.settings()
    assert len(cleared) == 1                                       # the file did not change: the cached copy is used
    f.write_text("a: 22\nb: 3\n", encoding="utf-8")                # edited while the dashboard runs
    server.settings()
    assert len(cleared) == 2
    f.unlink()
    server.settings()                                              # a missing file is a change too, and never raises
    assert len(cleared) == 3


def test_the_strategy_name_is_in_the_status(live, cfg):
    path = make_db(Path(cfg["paths"]["db_path"]))
    assert get_json(live, "/api/status")["strategy"] == "pair_h2"
    db_exec(path, "INSERT INTO bot_runs VALUES (?,?,?,?,?,?,?)", [("late-1", "late_favorite", "watch", NOW - 60, None, None, "{}")])
    d = get_json(live, "/api/status")
    assert d["strategy"] == "late_favorite" and d["run"]["strategy"] == "late_favorite" and d["run"]["mode"] == "watch"


LATE_CFG = {"strategy": "late_favorite", "mode": "live", "series": "KXBTC15M",
            "rule": {"window_start_s": 780.0, "window_end_s": 840.0, "window_length_s": 900.0,
                     "buy_yes_if_mid_above": "0.60", "buy_no_if_mid_below": "0.40", "order": "IOC at the current ask"},
            "contracts": "1", "max_attempts_per_window": 3, "poll_interval_s": 1.0, "max_book_age_s": 2.0,
            "max_loss_usd": "5.00", "max_cost_per_order_usd": "1.00", "max_contracts_per_order": "1",
            "max_orders_per_minute": 10, "kill_switch_file": "data/KILL", "key_fp": "db6af2da"}


def test_a_late_favorite_run_shows_its_own_settings_and_rows(live, cfg):
    path = make_db(Path(cfg["paths"]["db_path"]), with_rows=False)
    cfg["trading"]["kill_switch_file"] = "data/KILL"
    db_exec(path, "INSERT INTO bot_runs VALUES (?,?,?,?,?,?,?)", [("late-1", "late_favorite", "live", NOW - 600, None, None,
                                                                    json.dumps(LATE_CFG))])
    db_exec(path, "INSERT INTO bot_heartbeat VALUES (?,?,?,?)", [("late-1", NOW - 1, "monitoring", json.dumps(
        {"strategy": "late_favorite", "mode": "live", "status": "monitoring", "market_ticker": "KXBTC15M-26OCT051500-00",
         "window_end_ts": NOW + 40, "loss_used_usd": 0.3, "max_loss_usd": 5.0, "balance_usd": None, "kill_switch": False,
         "last_error": None}))])
    cols = ("run_id, detected_ts, hour_ticker, strike_ticker, kind, contracts, paper_cost, paper_fees, paper_edge, live, "
            "outcome, live_cost, live_fees, latency_ms, payout, realized_pnl, paper_realized_pnl")
    db_exec(path, f"INSERT INTO bot_opportunities ({cols}) VALUES ({','.join('?' * 17)})", [
        ("late-1", NOW - 300, "KXBTC15M-A", "KXBTC15M-A", "late favorite YES", 1, 0.73, 0.0138, None, 1, "filled", 0.74, 0.0135, 412.0,
         1.0, 0.2465, 0.2562),
        ("late-1", NOW - 200, "KXBTC15M-B", "KXBTC15M-B", "late favorite NO", 1, 0.95, 0.0033, None, 1, "filled", 0.96, 0.0027, 380.0,
         0.0, -0.9627, -0.9533),
        ("late-1", NOW - 100, "KXBTC15M-C", "KXBTC15M-C", "late favorite YES", 1, 0.88, 0.0074, None, 1, "filled", 0.88, 0.0074, 295.0,
         None, None, None),
        ("late-1", NOW - 50, "KXBTC15M-D", "KXBTC15M-D", "late favorite NO", 1, 0.84, 0.0094, None, 0, "paper_only", None, None, None,
         None, None, 0.1506),
        ("late-1", NOW - 40, "KXBTC15M-E", "KXBTC15M-E", "late favorite YES", 1, 0.91, 0.0057, None, 1, "none_filled", None, None,
         None, None, None, None)])
    d = get_json(live, "/api/status")
    lf = d["caps"]["late_favorite"]
    assert (lf["up_threshold"], lf["down_threshold"], lf["window_start_s"], lf["window_end_s"]) == (0.6, 0.4, 780.0, 840.0)
    assert lf["max_attempts"] == 3 and lf["contracts"] == 1 and d["caps"]["max_loss_usd"] == 5.0
    assert d["caps_drift"] == []                                      # the file and the run agree
    s = d["stats"]["run"]
    assert (s["opportunities"], s["filled"], s["live_attempts"]) == (5, 3, 4)
    assert s["paired_n"] == 2 and s["paired_realized"] == pytest.approx(-0.7162) and s["paired_paper"] == pytest.approx(-0.6971)
    assert s["pending_n"] == 1                                         # the third trade filled but its market has not settled
    assert s["realized_pnl"] == pytest.approx(-0.7162) and s["paper_realized_pnl"] == pytest.approx(-0.5465)
    rows = get_json(live, "/api/opportunities")["rows"]
    assert [r["hour_ticker"] for r in rows] == ["KXBTC15M-E", "KXBTC15M-D", "KXBTC15M-C", "KXBTC15M-B", "KXBTC15M-A"]
    assert all(r["live_edge"] is None and r["fees_pending"] is False for r in rows)   # no edge concept for a single order


def test_live_fees_that_are_not_recorded_yet_give_no_edge_instead_of_a_wrong_one(live, cfg):
    path = make_db(Path(cfg["paths"]["db_path"]))
    cols = ("run_id, detected_ts, hour_ticker, strike_ticker, kind, contracts, paper_cost, paper_fees, paper_edge, live, "
            "outcome, live_cost, live_fees, latency_ms, payout, realized_pnl, paper_realized_pnl")
    db_exec(path, f"INSERT INTO bot_opportunities ({cols}) VALUES ({','.join('?' * 17)})", [
        ("run-new", NOW - 10, "H", "KXBTCD-26OCT0515-T85499.99", "hourly YES + 15m DOWN", 1, 0.95, 0.03, 0.02, 1, "both_filled",
         0.96, None, 700.0, None, None, None)])
    rows = {r["strike_ticker"]: r for r in get_json(live, "/api/opportunities")["rows"]}
    pending = rows["KXBTCD-26OCT0515-T85499.99"]
    assert pending["live_edge"] is None and pending["edge_change"] is None and pending["fees_pending"] is True
    known = rows["KXBTCD-26OCT0515-T84999.99"]
    assert known["live_edge"] == pytest.approx(0.01) and known["fees_pending"] is False
    # the average delay cost ignores the row with unknown fees: only the row with known fees counts, (0.95-0.96) + 0
    assert get_json(live, "/api/status")["stats"]["run"]["avg_edge_change"] == pytest.approx(-0.01)


def test_a_live_row_whose_outcome_is_not_written_yet_is_handled(live, cfg):
    path = make_db(Path(cfg["paths"]["db_path"]))
    cols = ("run_id, detected_ts, hour_ticker, strike_ticker, kind, contracts, paper_cost, paper_fees, paper_edge, live, "
            "outcome, live_cost, live_fees, latency_ms, payout, realized_pnl, paper_realized_pnl")
    db_exec(path, f"INSERT INTO bot_opportunities ({cols}) VALUES ({','.join('?' * 17)})", [
        ("run-new", NOW - 3, "H", "KXBTCD-26OCT0515-T86000.00", "hourly YES + 15m DOWN", 1, 0.95, 0.03, 0.02, 1, None, None, None,
         None, None, None, None)])                                       # sent, nothing recorded about the result yet
    d = get_json(live, "/api/status")["stats"]["run"]
    assert d["opportunities"] == 3 and d["live_attempts"] == 3 and d["pending_n"] == 0   # not 'awaiting settlement': no outcome yet
    row = next(r for r in get_json(live, "/api/opportunities")["rows"] if r["strike_ticker"].endswith("T86000.00"))
    assert row["outcome"] is None and row["live_edge"] is None and row["fees_pending"] is False


def test_pnl_figures_compare_like_with_like(live, cfg):
    path = make_db(Path(cfg["paths"]["db_path"]))
    d = get_json(live, "/api/status")["stats"]["run"]
    # run-new: a both-filled pair (realized +0.01, paper +0.02) and an unwind (realized -0.02, no twin yet)
    assert d["realized_pnl"] == pytest.approx(-0.01) and d["paper_realized_pnl"] == pytest.approx(0.02)
    assert (d["paired_n"], d["paired_realized"], d["paired_paper"]) == (1, pytest.approx(0.01), pytest.approx(0.02))
    assert d["pending_n"] == 0 and d["unwound"] == 1 and d["both_filled"] == 1 and d["filled"] == 1
    cols = ("run_id, detected_ts, hour_ticker, strike_ticker, kind, contracts, paper_cost, paper_fees, paper_edge, live, "
            "outcome, live_cost, live_fees, latency_ms, payout, realized_pnl, paper_realized_pnl")
    db_exec(path, f"INSERT INTO bot_opportunities ({cols}) VALUES ({','.join('?' * 17)})", [
        ("run-new", NOW - 5, "H", "S3", "hourly YES + 15m DOWN", 1, 0.95, 0.03, 0.02, 1, "both_filled", 0.95, 0.03, 500.0, None,
         None, None)])
    assert get_json(live, "/api/status")["stats"]["run"]["pending_n"] == 1     # filled, not settled yet


def test_a_second_live_run_that_is_still_beating_is_reported(live, cfg):
    path = make_db(Path(cfg["paths"]["db_path"]))
    db_exec(path, "INSERT INTO bot_runs VALUES (?,?,?,?,?,?,?)", [
        ("late-live", "late_favorite", "live", NOW - 7000, None, None, "{}"),          # older than run-new, still running
        ("late-stale", "late_favorite", "live", NOW - 7100, None, None, "{}"),         # no heartbeat for 10 minutes: crashed
        ("late-watch", "late_favorite", "watch", NOW - 7200, None, None, "{}"),        # a watch run is not a danger
        ("late-ended", "late_favorite", "live", NOW - 7300, NOW - 7000, "finished", "{}")])
    db_exec(path, "INSERT INTO bot_heartbeat VALUES (?,?,?,?)", [
        ("late-live", NOW - 3, "waiting", "{}"), ("late-stale", NOW - 600, "waiting", "{}"),
        ("late-watch", NOW - 3, "waiting", "{}"), ("late-ended", NOW - 7000, "stopped", "{}")])
    d = get_json(live, "/api/status")
    assert d["run"]["run_id"] == "run-new"
    assert d["other_live_runs"] == [{"run_id": "late-live", "strategy": "late_favorite", "heartbeat_age_s": 3.0}]


def test_unscoped_events_follow_only_the_run_they_belong_to(live, cfg):
    path = make_db(Path(cfg["paths"]["db_path"]))
    db_exec(path, "INSERT INTO bot_events (run_id, ts, level, kind, message, data_json) VALUES (?,?,?,?,?,?)", [
        (None, NOW - 5 * 86400, "error", "error", "startup error five days ago", None),
        (None, NOW - 905, "error", "error", "startup error just before run-new", None),
        (None, NOW - 3000, "info", "start", "between the two runs", None)])
    new = [r["message"] for r in get_json(live, "/api/events?limit=200")["rows"]]
    assert "startup error just before run-new" in new and "no run yet" in new
    assert "startup error five days ago" not in new and "between the two runs" not in new
    old = [r["message"] for r in get_json(live, "/api/events?run=run-old&limit=200")["rows"]]
    assert old == ["old run started"]                                  # run-old ended at NOW-3600: nothing run-less after that
    assert len(get_json(live, "/api/events?run=all&limit=200")["rows"]) == 8
    assert [r["message"] for r in get_json(live, "/api/events?run=nope")["rows"]] == []


def test_a_message_and_an_end_reason_are_scrubbed(live, cfg):
    path = make_db(Path(cfg["paths"]["db_path"]))
    db_exec(path, "UPDATE bot_runs SET end_reason = ? WHERE run_id = 'run-new'", [("crashed: token=abc-123",)])
    d = get_json(live, "/api/status")
    assert "abc-123" not in json.dumps(d) and "[redacted]" in d["run"]["end_reason"]


# ============================================================================================ the static page
def test_the_page_has_no_inline_script_and_loads_its_assets_from_itself():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and not re.search(r"\son\w+\s*=", html)
    assert all(u.startswith(("/static/", "#")) for u in re.findall(r'(?:src|href)="([^"]+)"', html))
    for needed in ("title-main", "strategy-name", "rule-card", "chart-card", "opp-head", "trades-value", "chart-live", "banners",
                   'aria-live="polite"', "money-tiles", "pnl-svg", "bars-svg", "gauge-svg", "table-view", "data-scope", "data-opps"):
        assert needed in html, needed
    order = [html.index(i) for i in ('id="banners"', 'id="money"', 'id="pnl-card"', 'id="gauge-card"', 'id="opp-head"', 'id="orders-body"',
                                     'id="events-body"')]
    assert order == sorted(order)                                          # status, money, graphs, then the tables


def test_the_script_keeps_the_fixes_the_review_asked_for():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert "Math.abs(used)" not in js and "Math.max(0, used)" in js                  # a net gain is not "loss used"
    assert '"balance_dollars", "kalshi_balance_usd"]' in js and '"kalshi_balance_usd", "balance"]' not in js   # bare 'balance' is cents
    assert "isOpportunity(" in js and "p.edge > 0" not in js                         # markers use the bot's minimum edge
    assert "All strikes are hidden" in js and "last watched" in js and "NOT RESPONDING" in js and "STOPPED" in js
    assert "MODE UNKNOWN" in js and "bannerKey" in js and "announce(" in js
    assert re.search(r"\.tick\s*\{\s*fill:\s*var\(--ink-2\)", css)                   # axis labels at 4.5:1 or better
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js


# ============================================================================================ trade status
HM = lambda ts: datetime.fromtimestamp(ts).strftime("%H:%M")          # noqa: E731  what the server prints for a clock time
LATE_NOW = NOW - 100                                                  # the clock of the trade tests: the market of NOW is still open


def late_row(**kw) -> dict:
    """An opportunity row as the database returns it (a late_favorite signal), with the given columns changed."""
    row = {"id": 1, "run_id": "late-live", "detected_ts": NOW - 5000, "hour_ticker": "KXBTC15M-26OCT051400-00",
           "strike_ticker": "KXBTC15M-26OCT051400-00", "kind": "late favorite NO", "contracts": 1.0, "paper_cost": 0.75,
           "paper_fees": 0.013125, "paper_edge": None, "live": 1, "outcome": "filled", "live_cost": 0.74, "live_fees": 0.0135,
           "latency_ms": 45.0, "payout": 1.0, "realized_pnl": 0.2465, "paper_realized_pnl": 0.236875}
    row.update(kw)
    return row


def test_money_is_written_to_the_cent_with_a_real_minus_sign():
    assert server._money(0.2465, True) == "+$0.25" and server._money(0.2465) == "$0.25"
    assert server._money(-0.9627, True) == "−$0.96" and server._money(-0.9627) == "−$0.96"        # U+2212, like the page
    assert server._money(0.004, True) == "$0.00" and server._money(-0.004, True) == "$0.00"       # no sign on zero
    assert server._money(1234.5) == "$1,234.50" and server._money(0.125) == "$0.13"               # half up, not banker's


@pytest.mark.parametrize("detected,close", [
    (NOW - 5000, NOW - 4500), (NOW - 100, NOW), (NOW - 899.9, NOW), (NOW, NOW + 900), (NOW + 1, NOW + 900)])
def test_a_late_favorite_signal_settles_when_its_quarter_hour_market_closes(detected, close):
    assert server._market_close_ts({"kind": "late favorite YES", "detected_ts": detected}) == close
    assert (close - NOW) % 900 == 0
    assert server._market_close_ts({"kind": "late favorite YES"}) is None


def test_a_pair_settles_on_the_hour():
    assert server._market_close_ts({"kind": "hourly YES + 15m DOWN", "detected_ts": NOW - 100}) == NOW          # NOW is 19:00:00Z
    assert server._market_close_ts({"kind": "15m UP + hourly NO", "detected_ts": NOW + 5}) == NOW + 3600
    assert server._side({"kind": "late favorite NO"}) == "NO" and server._side({"kind": "15m UP + hourly NO"}) is None


CASES = [
    # name, row changes, expected (state, label, tone, result)
    ("won", {}, ("settled", "Settled · WON +$0.25", "good", "won")),
    ("lost", {"payout": 0.0, "live_cost": 0.96, "live_fees": 0.0027, "realized_pnl": -0.9627, "paper_realized_pnl": -0.9533},
     ("settled", "Settled · LOST −$0.96", "crit", "lost")),
    ("open", {"detected_ts": NOW - 200, "payout": None, "realized_pnl": None, "paper_realized_pnl": None},
     ("open", f"Open · settles at {HM(NOW)}", "info", None)),
    ("closed, result not booked yet", {"detected_ts": NOW - 1000, "payout": None, "realized_pnl": None, "paper_realized_pnl": None},
     ("open", f"Open · market closed {HM(NOW - 900)}, waiting for the result", "info", None)),
    ("overdue", {"detected_ts": NOW - 10000, "payout": None, "realized_pnl": None, "paper_realized_pnl": None},
     ("overdue", f"Overdue · no result recorded since {HM(NOW - 9900)}", "warn", None)),
    ("won but the fees ate it", {"payout": 1.0, "live_cost": 0.995, "live_fees": 0.01, "realized_pnl": -0.005},
     ("settled", "Settled · WON −$0.01", "good", "won")),
    ("won at exactly zero", {"payout": 1.0, "live_cost": 0.99, "live_fees": 0.01, "realized_pnl": 0.0},
     ("settled", "Settled · WON $0.00", "good", "won")),
    ("not filled, order sent", {"outcome": "none_filled", "live_cost": None, "live_fees": None, "payout": None, "realized_pnl": None},
     ("not_filled", "Not filled", "", None)),
    ("not filled, nothing sent", {"outcome": "none_filled", "live": 0, "live_cost": None, "live_fees": None, "payout": None,
                                  "realized_pnl": None}, ("not_filled", "Not filled", "", None)),
    # no reason was logged: the price alone is not trusted below every limit the bot has had (99.5c may be inside today's 99.9c)
    ("skipped at 99.5c, reason not recorded", {"outcome": "blocked_by_cap", "live": 0, "paper_cost": 0.995, "live_cost": None,
                                               "payout": None, "realized_pnl": None},
     ("skipped", "Skipped · blocked before sending", "warn", None)),
    ("skipped above every limit, reason not recorded", {"outcome": "blocked_by_cap", "live": 0, "paper_cost": 0.9995, "live_cost": None,
                                                        "payout": None, "realized_pnl": None},
     ("skipped", "Skipped · price above the bot's limit", "", None)),
    ("skipped, reason not recorded", {"outcome": "blocked_by_cap", "live": 0, "paper_cost": 0.80, "live_cost": None, "payout": None,
                                      "realized_pnl": None}, ("skipped", "Skipped · blocked before sending", "warn", None)),
    ("unknown", {"outcome": "unknown", "payout": None, "realized_pnl": None},
     ("unknown", "Unknown · order result not confirmed", "crit", None)),
    ("paper only, twin settled", {"outcome": "paper_only", "live": 0, "live_cost": None, "live_fees": None, "payout": None,
                                  "realized_pnl": None}, ("paper_only", "Paper only", "", None)),
    ("in progress", {"outcome": None, "live": 0, "detected_ts": NOW - 150, "live_cost": None, "payout": None, "realized_pnl": None,
                     "paper_realized_pnl": None}, ("in_progress", "In progress", "info", None)),
    ("no outcome ever written", {"outcome": None, "live": 0, "detected_ts": NOW - 20000, "live_cost": None, "payout": None,
                                 "realized_pnl": None, "paper_realized_pnl": None}, ("no_outcome", "No outcome recorded", "warn", None)),
]


@pytest.mark.parametrize("name,changes,expected", CASES, ids=[c[0] for c in CASES])
def test_the_status_of_a_trade_says_what_became_of_it(name, changes, expected):
    t = server.trade_status(late_row(**changes), LATE_NOW)
    assert (t["state"], t["label"], t["tone"], t["result"]) == expected
    assert t["real"] is (t["state"] in ("settled", "open", "overdue")) and t["side"] == "NO"
    assert t["settled"] is (t["state"] == "settled")                                # only a booked P&L is settled
    if t["state"] != "settled":
        assert t["pnl"] is None or t["real"]                                      # an open trade has no P&L yet


def test_a_settled_trade_carries_its_paper_twin_beside_it():
    t = server.trade_status(late_row(), LATE_NOW)
    assert t["pnl"] == pytest.approx(0.2465) and t["paper_pnl"] == pytest.approx(0.236875) and t["paper_label"] == "paper +$0.24"
    assert t["close_ts"] == NOW - 4500 and t["detail"] is None
    lost = server.trade_status(late_row(payout=0.0, realized_pnl=-0.75, paper_realized_pnl=-0.76), LATE_NOW)
    assert lost["label"] == "Settled · LOST −$0.75" and lost["paper_label"] == "paper −$0.76"
    assert server.trade_status(late_row(payout=1.0, realized_pnl=-0.005), LATE_NOW)["detail"] == \
        "the side won, but fees were larger than the profit"


def test_paper_only_says_how_the_paper_twin_ended():
    row = late_row(outcome="paper_only", live=0, live_cost=None, live_fees=None, payout=None, realized_pnl=None)
    assert server.trade_status(row, LATE_NOW)["detail"] == "paper twin WON +$0.24"
    lost = server.trade_status({**row, "paper_realized_pnl": -0.7631}, LATE_NOW)
    assert lost["label"] == "Paper only" and lost["detail"] == "paper twin LOST −$0.76" and lost["real"] is False
    open_twin = server.trade_status({**row, "detected_ts": NOW - 200, "paper_realized_pnl": None}, LATE_NOW)
    assert open_twin["detail"] == f"paper twin settles at {HM(NOW)}"
    closed = server.trade_status({**row, "detected_ts": NOW - 1000, "paper_realized_pnl": None}, LATE_NOW)
    assert closed["detail"] == f"paper twin: market closed {HM(NOW - 900)}, waiting for the result"


@pytest.mark.parametrize("reason,ask,label", [
    ("price 0.9950 is outside 0.0100..0.9900", None, "price above 99c"),
    ("price 0.0050 is outside 0.0100..0.9900", None, "price below 1c"),
    # the bound is the one the bot named in its message: since 2026-10-06 it takes favorites up to 99.9c
    ("price 0.9995 is outside 0.001..0.999", None, "price above 99.9c"),
    ("price 0.0005 is outside 0.001..0.999", 0.8, "price below 0.1c"),
    ("price 0.9995 is outside 0.001..0.999", 0.80, "price above 99.9c"),            # the logged message decides, not the row's price
    (None, 0.9995, "price above the bot's limit"),                                 # no reason logged, above every limit the bot has had
    (None, 0.995, "blocked before sending"),                                       # no reason logged: 99.5c proves nothing
    # a logged reason wins over a high price: the bot checks the stop, the kill switch and the halt before the price
    ("whatever", 0.9951, "whatever"),
    ("loss cap: 4.30 used + 0.9953 worst case of this order > 5.00 cap", 0.995, "loss cap reached"),
    ("kill switch", 0.9951, "kill switch was on"),
    ("safety is halted", 0.9951, "safety halt"),
    ("the bot is stopping", 0.9951, "bot was stopping"),
    ("refused by safety: balance too low", 0.9951, "refused by the safety check"),
    ("loss cap: 4.5 used + 0.81 worst case of this order > 5.00 cap", 0.8, "loss cap reached"),
    ("kill switch", 0.8, "kill switch was on"),
    ("safety is halted", 0.8, "safety halt"),
    ("refused by safety: balance too low", 0.8, "refused by the safety check"),
    ("collateral 1.2 exceeds the 1.00 per-order ceiling", 0.8, "above the per-order limit"),
    ("the bot is stopping", 0.8, "bot was stopping"),
    ("something new nobody planned for", 0.8, "something new nobody planned for"),
    (None, 0.8, "blocked before sending"), ("", None, "blocked before sending")])
def test_a_skipped_signal_names_its_real_reason(reason, ask, label):
    assert server._skip_label(reason, ask) == label
    assert "blocked by a cap" not in label


def test_a_price_bound_is_written_in_cents_as_short_as_it_can_be():
    assert [server._cents(v) for v in (0.99, 0.999, 0.9999, 0.9995, 0.01, 0.001)] == ["99c", "99.9c", "99.99c", "99.95c", "1c", "0.1c"]


@pytest.mark.parametrize("reason,paper_cost,label,tone", [
    ("price 0.9995 is outside 0.001..0.999", 0.9995, "Skipped · price above 99.9c", ""),        # the price limit doing its job: no warning
    ("price 0.9950 is outside 0.0100..0.9900", 0.995, "Skipped · price above 99c", ""),
    ("loss cap: 4.30 used + 0.9953 worst case of this order > 5.00 cap", 0.995, "Skipped · loss cap reached", "warn"),
    ("kill switch", 0.9951, "Skipped · kill switch was on", "warn"),
    ("safety is halted", 0.9951, "Skipped · safety halt", "warn"),
    ("the bot is stopping", 0.9951, "Skipped · bot was stopping", "warn"),
    ("a reason nobody planned for", 0.9951, "Skipped · a reason nobody planned for", "warn")])
def test_a_signal_priced_above_99c_keeps_the_reason_the_bot_logged(reason, paper_cost, label, tone):
    row = late_row(outcome="blocked_by_cap", live=0, paper_cost=paper_cost, live_cost=None, live_fees=None, payout=None,
                   realized_pnl=None)
    t = server.trade_status(row, LATE_NOW, reason)
    assert (t["state"], t["label"], t["tone"], t["detail"]) == ("skipped", label, tone, reason)


def test_pair_rows_get_a_status_too():
    pair = {"id": 9, "run_id": "r", "detected_ts": NOW - 100, "hour_ticker": "H", "strike_ticker": "S", "kind": "hourly YES + 15m DOWN",
            "contracts": 1.0, "paper_cost": 0.95, "paper_fees": 0.03, "live": 1, "outcome": "both_filled", "live_cost": 0.96,
            "live_fees": 0.03, "payout": 1.0, "realized_pnl": 0.01, "paper_realized_pnl": 0.02}
    assert server.trade_status(pair, LATE_NOW)["label"] == "Settled · WON +$0.01" and server.trade_status(pair, LATE_NOW)["side"] is None
    lost = server.trade_status({**pair, "realized_pnl": -0.02}, LATE_NOW)
    assert lost["label"] == "Settled · LOST −$0.02" and lost["result"] == "lost"                 # a pair always pays $1: judged by P&L
    open_pair = server.trade_status({**pair, "realized_pnl": None, "paper_realized_pnl": None}, LATE_NOW)
    assert open_pair["label"] == f"Open · settles at {HM(NOW)}"
    unwound = server.trade_status({**pair, "outcome": "one_leg_unwound", "realized_pnl": -0.02}, LATE_NOW)
    assert unwound["label"] == "Closed · one leg unwound −$0.02" and unwound["state"] == "unwound" and unwound["real"] is True
    held = server.trade_status({**pair, "outcome": "one_leg_held", "realized_pnl": None}, LATE_NOW)
    assert held["label"] == f"Open · one leg held, settles at {HM(NOW)}" and held["tone"] == "crit"


def test_odd_rows_never_break_the_answer():
    for row in ({}, {"outcome": "filled"}, {"outcome": "filled", "detected_ts": "soon", "realized_pnl": "x"},
                {"outcome": "weird", "kind": None, "contracts": 0}, {"outcome": "none_filled", "live": None}):
        t = server._safe(server.trade_status, row, NOW)
        assert isinstance(t["label"], str) and t["label"] and set(t) >= {"state", "label", "tone", "real", "settled", "close_ts"}


UNBOOKED = {"outcome": None, "live": 1, "detected_ts": LATE_NOW - 3, "live_cost": None, "live_fees": None, "latency_ms": None,
            "payout": None, "realized_pnl": None, "paper_realized_pnl": None}     # sent and filled a moment ago: the bot has written no outcome yet


def test_a_fill_the_bot_has_not_booked_yet_is_an_open_trade():
    """The bot sets live=1 before it sends, writes the fill on the order at the answer, and the row's outcome only some seconds later."""
    row = late_row(**UNBOOKED)
    assert server.trade_status(row, LATE_NOW)["state"] == "in_progress"                           # nothing says a fill happened
    t = server.trade_status(row, LATE_NOW, None, True)                                            # the order says it did
    assert (t["state"], t["label"], t["tone"], t["real"], t["settled"]) == ("open", f"Open · settles at {HM(NOW)}", "info", True, False)
    assert t["side"] == "NO" and t["pnl"] is None and t["close_ts"] == NOW
    # only a fresh, live late_favorite row without an outcome is treated so
    assert server.trade_status({**row, "live": 0}, LATE_NOW, None, True)["state"] == "in_progress"
    assert server.trade_status({**row, "kind": "hourly YES + 15m DOWN"}, LATE_NOW, None, True)["state"] == "in_progress"   # a pair is two legs
    assert server.trade_status({**row, "outcome": "none_filled"}, LATE_NOW, None, True)["state"] == "not_filled"
    stale = server.trade_status({**row, "detected_ts": LATE_NOW - 700}, LATE_NOW, None, True)       # never booked: a warning, not a trade
    assert (stale["state"], stale["tone"], stale["real"]) == ("no_outcome", "warn", False)
    assert server.trade_status({**row, "detected_ts": LATE_NOW - 599}, LATE_NOW, None, True)["real"] is True


def test_the_order_of_a_fill_that_is_being_booked_is_information_not_an_alarm():
    order = {"dry_run": 0, "status": "executed", "filled": 1.0}
    unbooked = server.trade_status(late_row(**UNBOOKED), LATE_NOW, None, True)
    o = server.order_status(order, unbooked, LATE_NOW)
    assert (o["state"], o["label"], o["tone"], o["real"]) == ("open", f"Open · settles at {HM(NOW)}", "info", True)
    # a row that is still "in progress" (a pair's first leg, or a row the lookup did not flag) is told apart from a mystery fill
    pending = server.trade_status(late_row(**UNBOOKED), LATE_NOW)
    o = server.order_status(order, pending, LATE_NOW)
    assert (o["state"], o["label"], o["tone"], o["real"]) == ("booking", "Filled · the bot is recording the trade", "info", True)
    assert "check Kalshi" not in (o["detail"] or "")
    # ... while a fill nothing accounts for keeps its warning: a stale row, a row that says nothing filled, or no row at all
    for trade in (server.trade_status(late_row(**{**UNBOOKED, "detected_ts": LATE_NOW - 700}), LATE_NOW),
                  server.trade_status(late_row(outcome="none_filled"), LATE_NOW)):
        o = server.order_status(order, trade, LATE_NOW)
        assert (o["state"], o["label"], o["tone"]) == ("filled", "Filled", "warn") and "check Kalshi" in o["detail"]
    o = server.order_status(order, None, LATE_NOW)
    assert (o["label"], o["tone"], o["detail"]) == ("Filled", "warn", "no trade row is linked to this order")


def seed_late(path: Path) -> dict[str, int]:
    """A live late_favorite run (late-live) with a row for every state a signal can end in, the orders that belong to them, and
    an older watch-only run (late-watch) with paper twins. Returns {name: opportunity id}."""
    make_db(path, with_rows=False)
    conn = sqlite3.connect(path)
    conn.executemany("INSERT INTO bot_runs VALUES (?,?,?,?,?,?,?)", [
        ("late-live", "late_favorite", "live", NOW - 20000, None, None, json.dumps({**LATE_CFG, "api_key_id": "SHOULD-NOT-SHOW"})),
        ("late-watch", "late_favorite", "watch", NOW - 90000, NOW - 80000, "finished", "{}")])
    ids: dict[str, int] = {}
    conn.execute("INSERT INTO bot_heartbeat VALUES (?,?,?,?)", ("late-live", LATE_NOW - 1, "monitoring", json.dumps(
        {"strategy": "late_favorite", "mode": "live", "status": "monitoring", "market_ticker": "KXBTC15M-open", "loss_used_usd": 1.2,
         "max_loss_usd": 5.0, "balance_usd": 28.12, "kill_switch": False})))

    def add(name, run, detected, kind="late favorite NO", outcome="filled", live=1, paper=(0.75, 0.013125), fill=(0.74, 0.0135),
            latency=45.0, payout=None, pnl=None, paper_pnl=None):
        cur = conn.execute(
            "INSERT INTO bot_opportunities (run_id, detected_ts, hour_ticker, strike_ticker, kind, contracts, paper_cost, paper_fees, "
            "live, outcome, live_cost, live_fees, latency_ms, payout, realized_pnl, paper_realized_pnl) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run, detected, f"KXBTC15M-{name}", f"KXBTC15M-{name}", kind, 1, paper[0], paper[1], live, outcome,
             fill[0] if fill else None, fill[1] if fill else None, latency if fill else (61.0 if outcome == "none_filled" else None),
             payout, pnl, paper_pnl))
        ids[name] = cur.lastrowid

    add("won", "late-live", NOW - 5000, payout=1.0, pnl=0.2465, paper_pnl=0.236875)
    add("lost", "late-live", NOW - 4000, "late favorite YES", paper=(0.95, 0.0033), fill=(0.96, 0.0027), latency=60.0, payout=0.0,
        pnl=-0.9627, paper_pnl=-0.9533)
    add("open", "late-live", NOW - 200, paper=(0.88, 0.0074), fill=(0.88, 0.0074), latency=30.0)
    add("waiting", "late-live", NOW - 1000, "late favorite YES", paper=(0.70, 0.0147), fill=(0.70, 0.0147), latency=50.0)
    add("overdue", "late-live", NOW - 10000, paper=(0.66, 0.0157), fill=(0.65, 0.0159), latency=40.0)
    add("skip99", "late-live", NOW - 3000, "late favorite YES", "blocked_by_cap", 0, paper=(0.995, 0.0), fill=None, paper_pnl=0.004)
    add("skipcap", "late-live", NOW - 2900, "late favorite YES", "blocked_by_cap", 0, paper=(0.80, 0.0112), fill=None, paper_pnl=0.18)
    add("skipwho", "late-live", NOW - 2800, "late favorite YES", "blocked_by_cap", 0, paper=(0.80, 0.0112), fill=None)
    add("skipkey", "late-live", NOW - 2700, "late favorite YES", "blocked_by_cap", 0, paper=(0.80, 0.0112), fill=None)
    add("unfilled", "late-live", NOW - 2600, outcome="none_filled", paper=(0.85, 0.0089), fill=None, paper_pnl=0.14)
    add("thin", "late-live", NOW - 2500, outcome="none_filled", live=0, paper=(0.85, 0.0089), fill=None)
    add("unknown", "late-live", NOW - 2400, outcome="unknown", paper=(0.85, 0.0089), fill=None)
    add("fresh", "late-live", NOW - 150, outcome=None, live=0, paper=(0.85, 0.0089), fill=None)
    add("stale", "late-live", NOW - 20000, outcome=None, live=0, paper=(0.85, 0.0089), fill=None)
    add("twinwon", "late-watch", NOW - 85000, outcome="paper_only", live=0, fill=None, paper_pnl=0.236875)
    add("twinlost", "late-watch", NOW - 84000, "late favorite YES", "paper_only", 0, paper=(0.95, 0.0033), fill=None, paper_pnl=-0.9533)
    add("twinopen", "late-watch", NOW - 83000, outcome="paper_only", live=0, fill=None)
    conn.executemany("INSERT INTO bot_events (run_id, ts, level, kind, message, data_json) VALUES (?,?,?,?,?,?)", [
        ("late-live", NOW - 3000, "warn", "cap", "live order not sent: price 0.9950 is outside 0.0100..0.9900",
         json.dumps({"opportunity_id": ids["skip99"]})),
        ("late-live", NOW - 2900, "warn", "cap", "live order not sent: loss cap: 4.5 used + 0.81 worst case of this order > 5.00 cap",
         json.dumps({"opportunity_id": ids["skipcap"]})),
        ("late-live", NOW - 2700, "warn", "cap", "live order not sent: refused by safety: api_key_id=abc-123 was rejected",
         json.dumps({"opportunity_id": ids["skipkey"], "headers": {"KALSHI-ACCESS-KEY": "abc-123"}})),
        ("late-live", NOW - 2699, "info", "cap", "an event whose data is not json", "{not json"),
        ("late-live", NOW - 2698, "info", "cap", "an event that names no opportunity", json.dumps({"x": 1}))])
    ocols = ("client_order_id, run_id, opportunity_id, ts_sent, ts_ack, ticker, side, action, count, limit_price, tif, status, "
             "order_id, filled, avg_fill_price, fees, dry_run, response_json")
    conn.executemany(f"INSERT INTO bot_orders ({ocols}) VALUES ({','.join('?' * 18)})", [
        ("c-won", "late-live", ids["won"], NOW - 4999, NOW - 4998.9, "KXBTC15M-won", "no", "buy", 1, 0.75, "ioc", "executed", "o1", 1,
         0.74, 0.0135, 0, json.dumps({"headers": {"KALSHI-ACCESS-KEY": "abc-123"}})),
        ("c-lost", "late-live", ids["lost"], NOW - 3999, NOW - 3998.9, "KXBTC15M-lost", "yes", "buy", 1, 0.95, "ioc", "executed", "o2", 1,
         0.96, 0.0027, 0, None),
        ("c-open", "late-live", ids["open"], NOW - 199, NOW - 198.9, "KXBTC15M-open", "no", "buy", 1, 0.88, "ioc", "executed", "o3", 1,
         0.88, 0.0074, 0, None),
        ("c-unfilled", "late-live", ids["unfilled"], NOW - 2599, NOW - 2598.9, "KXBTC15M-unfilled", "no", "buy", 1, 0.85, "ioc",
         "canceled", None, 0, None, None, 0, None),
        ("c-unsent", "late-live", ids["skipcap"], NOW - 2899, None, "KXBTC15M-skipcap", "yes", "buy", 1, 0.80, "ioc", "not_sent", None,
         None, None, None, 1, None),
        ("c-nolink", "late-live", None, NOW - 100, NOW - 99.9, "KXBTC15M-nolink", "yes", "buy", 1, 0.8, "ioc", "executed", "o4", 1, 0.8,
         0.01, 0, None),
        ("c-inflight", "late-live", ids["fresh"], NOW - 120, None, "KXBTC15M-fresh", "yes", "buy", 1, 0.8, "ioc", "sent", None, None,
         None, None, 0, None),
        ("c-paper", "late-watch", ids["twinwon"], NOW - 85000, None, "KXBTC15M-twinwon", "no", "buy", 1, 0.75, "ioc", "dry_run", None,
         None, None, None, 1, None)])
    conn.commit()
    conn.close()
    return ids


@pytest.fixture
def late(live, cfg, monkeypatch):
    """The late_favorite scenario served at LATE_NOW; returns {name: opportunity id}."""
    monkeypatch.setattr(server, "time", SimpleNamespace(time=lambda: LATE_NOW))
    return seed_late(Path(cfg["paths"]["db_path"]))


def trade_of(rows: list[dict], ids: dict[str, int], name: str) -> dict:
    return next(r for r in rows if r["id"] == ids[name])["trade"]


def add_opp(path: Path, name: str, detected: float, run: str = "late-live", kind: str = "late favorite NO", outcome: str | None = "filled",
            live: int = 1, paper: tuple = (0.75, 0.013125), fill: tuple | None = (0.74, 0.0135), latency: float | None = 45.0,
            payout: float | None = None, pnl: float | None = None, paper_pnl: float | None = None) -> int:
    """One more bot_opportunities row for a test (seed_late's own helper is local to it). Returns its id."""
    conn = sqlite3.connect(path)
    cur = conn.execute(
        "INSERT INTO bot_opportunities (run_id, detected_ts, hour_ticker, strike_ticker, kind, contracts, paper_cost, paper_fees, live, "
        "outcome, live_cost, live_fees, latency_ms, payout, realized_pnl, paper_realized_pnl) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run, detected, f"KXBTC15M-{name}", f"KXBTC15M-{name}", kind, 1, paper[0], paper[1], live, outcome,
         fill[0] if fill else None, fill[1] if fill else None, latency if fill else None, payout, pnl, paper_pnl))
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id


def add_order(path: Path, coid: str, opp_id: int, name: str, sent: float, status: str = "executed", filled: float | None = 1,
              run: str = "late-live") -> None:
    """A real (dry_run = 0) order of a test, acknowledged 50 ms after it was sent unless it is still 'sent'."""
    db_exec(path, "INSERT INTO bot_orders (client_order_id, run_id, opportunity_id, ts_sent, ts_ack, ticker, side, action, count, "
                  "limit_price, tif, status, filled, avg_fill_price, fees, dry_run) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(coid, run, opp_id, sent, None if status == "sent" else sent + 0.05, f"KXBTC15M-{name}", "no", "buy", 1, 0.75, "ioc", status,
              filled, 0.74 if filled else None, 0.0135 if filled else None, 0)])


def add_cap_event(path: Path, opp_id: int, reason: str, ts: float, run: str = "late-live") -> None:
    db_exec(path, "INSERT INTO bot_events (run_id, ts, level, kind, message, data_json) VALUES (?,?,?,?,?,?)",
            [(run, ts, "warn", "cap", f"live order not sent: {reason}", json.dumps({"opportunity_id": opp_id}))])


def test_a_refused_signal_priced_above_99c_shows_why_the_bot_refused_it(live, late, cfg):
    """The bot checks the stop, the kill switch, the halt and the loss cap as well as the price: a high price is not the reason."""
    path = Path(cfg["paths"]["db_path"])
    before = get_json(live, "/api/performance")["summary"]["skipped_reasons"]
    wanted = {"topcap": (0.995, "loss cap: 4.30 used + 0.9953 worst case of this order > 5.00 cap", "Skipped · loss cap reached", "warn"),
              "topkill": (0.9951, "kill switch", "Skipped · kill switch was on", "warn"),
              "tophalt": (0.9962, "safety is halted", "Skipped · safety halt", "warn"),
              "toplimit": (0.9995, "price 0.9995 is outside 0.001..0.999", "Skipped · price above 99.9c", ""),
              "oldlimit": (0.9950, "price 0.9950 is outside 0.0100..0.9900", "Skipped · price above 99c", "")}
    ids = {}
    for i, (name, (price, reason, _, _)) in enumerate(wanted.items()):
        ids[name] = add_opp(path, name, NOW - 2300 - 10 * i, outcome="blocked_by_cap", live=0, paper=(price, 0.0), fill=None)
        add_cap_event(path, ids[name], reason, NOW - 2299 - 10 * i)
    rows = get_json(live, "/api/opportunities?limit=1000")["rows"]
    for name, (_, reason, label, tone) in wanted.items():
        t = trade_of(rows, ids, name)
        assert (t["label"], t["tone"], t["detail"]) == (label, tone, reason), name
    after = get_json(live, "/api/performance")["summary"]["skipped_reasons"]
    gained = {k: after.get(k, 0) - before.get(k, 0) for k in after}
    assert {k: v for k, v in gained.items() if v} == {"loss cap reached": 1, "kill switch was on": 1, "safety halt": 1,
                                                      "price above 99.9c": 1, "price above 99c": 1}


def test_a_fill_not_yet_booked_is_an_open_trade_everywhere_not_a_warning(live, late, cfg):
    """Right after the order is filled the bot has live=1 on the row and filled=1 on the order, and no outcome for some seconds."""
    path = Path(cfg["paths"]["db_path"])
    before = get_json(live, "/api/performance")
    booking = add_opp(path, "booking", LATE_NOW - 3, outcome=None, fill=None)               # filled 3 s ago, nothing written but live=1
    add_order(path, "c-booking", booking, "booking", LATE_NOW - 3)
    stale = add_opp(path, "unbooked", LATE_NOW - 1000, outcome=None, fill=None)             # filled, and never booked: a real problem
    add_order(path, "c-unbooked", stale, "unbooked", LATE_NOW - 1000)
    sent = add_opp(path, "halfway", LATE_NOW - 2, outcome=None, fill=None)                  # sent, no answer yet
    add_order(path, "c-halfway", sent, "halfway", LATE_NOW - 2, status="sent", filled=None)
    label = f"Open · settles at {HM(NOW)}"

    rows = get_json(live, "/api/opportunities?limit=1000")["rows"]
    t = next(r for r in rows if r["id"] == booking)["trade"]
    assert (t["state"], t["label"], t["tone"], t["real"]) == ("open", label, "info", True)
    assert next(r for r in rows if r["id"] == stale)["trade"]["label"] == "No outcome recorded"
    assert next(r for r in rows if r["id"] == sent)["trade"]["label"] == "In progress"

    orders = {r["client_order_id"]: r for r in get_json(live, "/api/orders?limit=1000")["rows"]}
    assert (orders["c-booking"]["trade"]["label"], orders["c-booking"]["trade"]["tone"]) == (label, "info")      # was "Filled", warn
    assert "check Kalshi" not in json.dumps(orders["c-booking"]["trade"])
    assert (orders["c-unbooked"]["trade"]["label"], orders["c-unbooked"]["trade"]["tone"]) == ("Filled", "warn")
    assert "check Kalshi" in orders["c-unbooked"]["trade"]["detail"]
    assert orders["c-halfway"]["trade"]["label"] == "Sent · waiting for the exchange"

    p = get_json(live, "/api/performance")
    assert booking in [o["id"] for o in p["open"]] and stale not in [o["id"] for o in p["open"]] and sent not in [o["id"] for o in p["open"]]
    assert p["summary"]["open"] == before["summary"]["open"] + 1 and p["summary"]["settled"] == before["summary"]["settled"]
    assert p["summary"]["fees_pending"] == before["summary"]["fees_pending"]                  # an unbooked fill is no "fees not recorded"
    assert p["summary"]["fees"] == pytest.approx(before["summary"]["fees"])                   # ... and adds no figures that are not there
    assert next(o for o in p["open"] if o["id"] == booking)["label"] == label


def test_the_look_for_unbooked_fills_survives_a_database_without_orders(live, cfg, monkeypatch):
    """A database from before bot_orders: the look for unbooked fills finds nothing and nothing breaks."""
    monkeypatch.setattr(server, "time", SimpleNamespace(time=lambda: LATE_NOW))
    path = make_db(Path(cfg["paths"]["db_path"]), with_rows=False)
    db_exec(path, "INSERT INTO bot_runs VALUES (?,?,?,?,?,?,?)", [("r", "late_favorite", "live", NOW - 20000, None, None, "{}")])
    row_id = add_opp(path, "x", LATE_NOW - 3, run="r", outcome=None, fill=None)
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE bot_orders")
    conn.commit()
    conn.close()
    t = next(r for r in get_json(live, "/api/opportunities")["rows"] if r["id"] == row_id)["trade"]
    assert t["label"] == "In progress"
    assert get_json(live, "/api/performance")["summary"]["open"] == 0


def test_every_signal_row_carries_its_status(live, late):
    rows = get_json(live, "/api/opportunities?limit=1000")["rows"]
    assert {r["id"] for r in rows} == {i for n, i in late.items() if not n.startswith("twin")}          # the latest run only
    expect = {"won": "Settled · WON +$0.25", "lost": "Settled · LOST −$0.96", "open": f"Open · settles at {HM(NOW)}",
              "waiting": f"Open · market closed {HM(NOW - 900)}, waiting for the result",
              "overdue": f"Overdue · no result recorded since {HM(NOW - 9900)}",
              "skip99": "Skipped · price above 99c", "skipcap": "Skipped · loss cap reached",
              "skipwho": "Skipped · blocked before sending", "skipkey": "Skipped · refused by the safety check",
              "unfilled": "Not filled", "thin": "Not filled", "unknown": "Unknown · order result not confirmed",
              "fresh": "In progress", "stale": "No outcome recorded"}
    for name, label in expect.items():
        assert trade_of(rows, late, name)["label"] == label, name
    assert trade_of(rows, late, "unfilled")["detail"] == "the order was sent and nothing filled"
    assert trade_of(rows, late, "thin")["detail"] == "no order could be sent (thin book)"
    assert trade_of(rows, late, "skipcap")["detail"].startswith("loss cap: 4.5 used")                 # the raw reason stays available
    assert all("blocked by a cap" not in r["trade"]["label"] for r in rows)
    twins = {r["id"]: r["trade"] for r in get_json(live, "/api/opportunities?run=late-watch")["rows"]}
    assert twins[late["twinwon"]]["detail"] == "paper twin WON +$0.24" and twins[late["twinlost"]]["detail"] == "paper twin LOST −$0.95"
    assert twins[late["twinopen"]]["detail"] == f"paper twin: market closed {HM(NOW - 82800)}, waiting for the result"
    assert {t["label"] for t in twins.values()} == {"Paper only"} and not any(t["real"] for t in twins.values())


def test_the_old_fields_of_a_row_are_unchanged(live, late):
    row = next(r for r in get_json(live, "/api/opportunities?limit=1000")["rows"] if r["id"] == late["won"])
    assert row["outcome"] == "filled" and row["realized_pnl"] == pytest.approx(0.2465) and row["live"] == 1
    assert {"live_edge", "edge_change", "fees_pending", "paper_cost", "latency_ms"} <= row.keys()


def test_every_order_says_real_money_or_paper_and_what_became_of_its_trade(live, late):
    rows = {r["client_order_id"]: r for r in get_json(live, "/api/orders?limit=1000")["rows"]}
    assert set(rows) == {"c-won", "c-lost", "c-open", "c-unfilled", "c-unsent", "c-nolink", "c-inflight"}      # the latest run's orders
    money = {k: r["money"] for k, r in rows.items()}
    assert money == {"c-won": "real money", "c-lost": "real money", "c-open": "real money", "c-unfilled": "real money",
                     "c-unsent": "not sent", "c-nolink": "real money", "c-inflight": "real money"}
    label = {k: r["trade"]["label"] for k, r in rows.items()}
    assert label["c-won"] == "Settled · WON +$0.25" and label["c-lost"] == "Settled · LOST −$0.96"
    assert label["c-open"] == f"Open · settles at {HM(NOW)}"
    assert label["c-unfilled"] == "Not filled" and rows["c-unfilled"]["trade"]["detail"] == "order canceled"
    assert label["c-unsent"] == "Not sent" and label["c-nolink"] == "Filled" and label["c-inflight"] == "Sent · waiting for the exchange"
    assert rows["c-won"]["trade"]["paper_pnl"] == pytest.approx(0.236875)                  # the paper twin travels with the order
    paper = {r["client_order_id"]: r for r in get_json(live, "/api/orders?run=late-watch")["rows"]}["c-paper"]
    assert paper["money"] == "paper" and paper["trade"]["label"] == "Paper only" and paper["trade"]["detail"] == "paper twin WON +$0.24"
    assert "LIVE" not in json.dumps([r["money"] for r in rows.values()]) and "dry run" not in json.dumps(money)


def test_a_real_fill_is_never_shown_as_not_filled_or_as_paper(live, late, cfg):
    db_exec(Path(cfg["paths"]["db_path"]),
            "INSERT INTO bot_orders (client_order_id, run_id, opportunity_id, ts_sent, ticker, side, action, count, limit_price, status, "
            "filled, dry_run) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [("c-odd", "late-live", late["unfilled"], NOW - 50, "KXBTC15M-unfilled", "no", "buy", 1, 0.85, "executed", 1, 0)])
    row = next(r for r in get_json(live, "/api/orders?limit=1000")["rows"] if r["client_order_id"] == "c-odd")
    assert row["money"] == "real money" and row["trade"]["label"] == "Filled" and row["trade"]["tone"] == "warn"
    assert "check Kalshi" in row["trade"]["detail"]                           # the order filled, its trade row says nothing filled


def test_the_status_answers_follow_the_clock_of_the_request(live, late, monkeypatch):
    def label_at(now: float) -> str:
        monkeypatch.setattr(server, "time", SimpleNamespace(time=lambda: now))
        return trade_of(get_json(live, "/api/opportunities?limit=1000")["rows"], late, "open")["label"]

    assert label_at(NOW - 100) == f"Open · settles at {HM(NOW)}"
    assert label_at(NOW + 30) == f"Open · market closed {HM(NOW)}, waiting for the result"
    assert label_at(NOW + 1900) == f"Overdue · no result recorded since {HM(NOW)}"


# ============================================================================================ the money figures
def test_performance_summary_for_a_live_run(live, late):
    p = get_json(live, "/api/performance")
    assert p["run_id"] == "late-live" and p["view"] == "real" and p["strategy"] == "late_favorite" and p["mode"] == "live"
    assert p["since"] == NOW - 20000 and p["truncated"] is False and p["all_runs"] is False
    s = p["summary"]
    assert (s["settled"], s["open"], s["overdue"], s["wins"], s["losses"], s["win_rate"]) == (2, 3, 1, 1, 1, 0.5)
    assert s["pnl"] == pytest.approx(0.2465 - 0.9627) and s["avg_pnl"] == pytest.approx((0.2465 - 0.9627) / 2)
    assert s["paper_pnl"] == pytest.approx(0.236875 - 0.9533) and s["paper_n"] == 2
    assert s["delay_cost"] == pytest.approx((0.2465 - 0.9627) - (0.236875 - 0.9533))                # live minus paper, same trades
    assert s["best"]["pnl"] == pytest.approx(0.2465) and s["best"]["side"] == "NO" and s["best"]["id"] == late["won"]
    assert s["worst"]["pnl"] == pytest.approx(-0.9627) and s["worst"]["side"] == "YES"
    assert s["fees"] == pytest.approx(0.0135 + 0.0027 + 0.0074 + 0.0147 + 0.0159) and s["fees_pending"] == 0     # every real fill, open ones too
    assert s["avg_latency_ms"] == pytest.approx((45 + 60 + 30 + 50 + 40) / 5)
    assert s["avg_fill_vs_paper_cents"] == pytest.approx((-1 + 1 + 0 + 0 - 1) / 5)                   # live fill minus detection, in cents
    assert s["skipped"] == 4 and s["not_filled"] == 2 and s["unknown"] == 1 and s["paper_only"] == 0
    assert s["skipped_reasons"] == {"price above 99c": 1, "loss cap reached": 1, "blocked before sending": 1,
                                    "refused by the safety check": 1}
    assert s["signals"] == 14 and s["first_ts"] == NOW - 20000 and s["last_ts"] == NOW - 150


def test_performance_lists_settled_trades_with_running_totals_and_the_open_ones(live, late):
    p = get_json(live, "/api/performance")
    won, lost = p["trades"]
    assert [t["id"] for t in p["trades"]] == [late["won"], late["lost"]]                              # oldest first
    assert won["close_ts"] == NOW - 4500 and lost["close_ts"] == NOW - 3600 and won["side"] == "NO" and lost["side"] == "YES"
    assert (won["result"], lost["result"]) == ("won", "lost")
    assert won["pnl"] == pytest.approx(0.2465) and won["paper_pnl"] == pytest.approx(0.236875)
    assert won["cum"] == pytest.approx(0.2465) and won["cum_paper"] == pytest.approx(0.236875)
    assert lost["cum"] == pytest.approx(0.2465 - 0.9627) and lost["cum_paper"] == pytest.approx(0.236875 - 0.9533)
    assert won["entry"] == pytest.approx(0.74) and won["paper_entry"] == pytest.approx(0.75) and won["fees"] == pytest.approx(0.0135)
    assert won["ticker"] == "KXBTC15M-won" and won["payout"] == 1.0 and won["latency_ms"] == 45.0
    assert [o["id"] for o in p["open"]] == [late["overdue"], late["waiting"], late["open"]]
    assert [o["state"] for o in p["open"]] == ["overdue", "open", "open"] and p["open"][2]["close_ts"] == NOW
    assert p["open"][2]["label"] == f"Open · settles at {HM(NOW)}" and p["open"][2]["side"] == "NO"
    assert p["base"] == {"pnl": 0.0, "paper_pnl": 0.0}


def test_performance_all_runs_counts_real_trades_and_sets_the_watch_twins_aside(live, late):
    p = get_json(live, "/api/performance?run=all")
    assert p["all_runs"] is True and p["run_id"] is None and p["view"] == "real" and p["mode"] == "mixed" and p["strategy"] == "late_favorite"
    assert p["since"] == NOW - 20000                                       # the first LIVE run, not the older watch-only one
    assert p["summary"]["settled"] == 2 and p["summary"]["paper_only"] == 3 and p["summary"]["signals"] == 17
    assert [t["id"] for t in p["trades"]] == [late["won"], late["lost"]]


def test_performance_of_a_watch_only_run_is_the_paper_twins(live, late):
    p = get_json(live, "/api/performance?run=late-watch")
    assert p["view"] == "paper" and p["mode"] == "watch" and p["run_id"] == "late-watch"
    s = p["summary"]
    assert (s["settled"], s["open"], s["wins"], s["losses"], s["paper_only"], s["signals"]) == (2, 1, 1, 1, 3, 3)
    assert s["pnl"] == pytest.approx(0.236875 - 0.9533) and s["paper_pnl"] == pytest.approx(s["pnl"])
    assert s["fees"] is None and s["avg_latency_ms"] is None and s["delay_cost"] is None        # no real orders, no real fees or delay
    assert [t["result"] for t in p["trades"]] == ["won", "lost"] and p["trades"][0]["pnl"] == p["trades"][0]["paper_pnl"]
    assert p["trades"][1]["cum"] == pytest.approx(0.236875 - 0.9533) and p["trades"][0]["entry"] == pytest.approx(0.75)
    assert [o["id"] for o in p["open"]] == [late["twinopen"]]


def test_performance_limit_keeps_the_newest_trades_and_the_running_totals_before_them(live, late):
    p = get_json(live, "/api/performance?limit=1")
    assert [t["id"] for t in p["trades"]] == [late["lost"]] and p["truncated"] is True
    assert p["base"] == {"pnl": pytest.approx(0.2465), "paper_pnl": pytest.approx(0.236875)}
    assert p["trades"][0]["cum"] == pytest.approx(0.2465 - 0.9627)
    assert p["summary"]["settled"] == 2 and p["summary"]["wins"] == 1                                  # the summary still counts every trade
    assert len(get_json(live, "/api/performance?limit=bad")["trades"]) == 2                            # bad input falls back to the default


def test_performance_for_an_unknown_run_is_empty_not_an_error(live, late):
    p = get_json(live, "/api/performance?run=no-such-run")
    assert p["run_id"] == "no-such-run" and p["trades"] == [] and p["open"] == [] and p["summary"]["signals"] == 0
    assert p["view"] == "real" and p["summary"]["pnl"] is None and p["mode"] is None


def test_performance_fees_not_recorded_yet_are_counted_apart(live, late, cfg):
    db_exec(Path(cfg["paths"]["db_path"]), "UPDATE bot_opportunities SET live_fees = NULL WHERE id = ?", [(late["open"],)])
    s = get_json(live, "/api/performance")["summary"]
    assert s["fees_pending"] == 1 and s["fees"] == pytest.approx(0.0135 + 0.0027 + 0.0147 + 0.0159)


def test_a_loss_after_a_win_shows_in_the_running_total(live, cfg, monkeypatch):
    monkeypatch.setattr(server, "time", SimpleNamespace(time=lambda: LATE_NOW))
    path = make_db(Path(cfg["paths"]["db_path"]), with_rows=False)
    db_exec(path, "INSERT INTO bot_runs VALUES (?,?,?,?,?,?,?)", [("r", "late_favorite", "live", NOW - 20000, None, None, "{}")])
    cols = ("run_id, detected_ts, hour_ticker, strike_ticker, kind, contracts, paper_cost, paper_fees, live, outcome, live_cost, "
            "live_fees, latency_ms, payout, realized_pnl, paper_realized_pnl")
    db_exec(path, f"INSERT INTO bot_opportunities ({cols}) VALUES ({','.join('?' * 16)})", [
        ("r", NOW - 5000 + 900 * i, "H", "H", "late favorite YES", 1, 0.9, 0.0, 1, "filled", 0.9, 0.0, 50.0, pay, pnl, pnl)
        for i, (pay, pnl) in enumerate([(1.0, 0.1), (1.0, 0.1), (1.0, 0.1), (0.0, -0.9), (1.0, 0.1)])])
    p = get_json(live, "/api/performance")
    assert [round(t["cum"], 6) for t in p["trades"]] == [0.1, 0.2, 0.3, -0.6, -0.5]
    assert p["summary"]["wins"] == 4 and p["summary"]["losses"] == 1 and p["summary"]["win_rate"] == 0.8      # small wins, one big loss
    assert p["summary"]["pnl"] == pytest.approx(-0.5) and p["summary"]["worst"]["pnl"] == pytest.approx(-0.9)


def test_performance_rows_without_the_newer_columns_or_tables_are_tolerated(live, cfg):
    path = Path(cfg["paths"]["db_path"])
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE bot_runs (run_id TEXT PRIMARY KEY, strategy TEXT, mode TEXT, started_ts REAL, ended_ts REAL, "
                       "end_reason TEXT, config_json TEXT);"
                       "CREATE TABLE bot_opportunities (id INTEGER PRIMARY KEY, run_id TEXT, detected_ts REAL, hour_ticker TEXT, "
                       "kind TEXT, contracts REAL, live INTEGER, outcome TEXT, realized_pnl REAL);"
                       "INSERT INTO bot_runs VALUES ('r', 'late_favorite', 'live', 1, NULL, NULL, '{}');"
                       "INSERT INTO bot_opportunities VALUES (1, 'r', 1791220000, 'T', 'late favorite YES', 1, 1, 'filled', 0.2);")
    conn.commit()
    conn.close()                                                           # no bot_events, no bot_orders, no cost or fee columns
    p = get_json(live, "/api/performance")
    assert p["summary"]["settled"] == 1 and p["summary"]["pnl"] == pytest.approx(0.2) and p["summary"]["paper_pnl"] is None
    assert get_json(live, "/api/opportunities")["rows"][0]["trade"]["label"] == "Settled · WON +$0.20"
    assert get_json(live, "/api/orders")["rows"] == []


def test_performance_never_writes_to_the_database(live, late, cfg):
    path = Path(cfg["paths"]["db_path"])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    for url in ("/api/performance", "/api/performance?run=all", "/api/performance?run=late-watch", "/api/opportunities",
                "/api/orders", "/api/performance?limit=1"):
        get_json(live, url)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not [p for p in path.parent.iterdir() if p.name.startswith("kpm.sqlite-")]                # no journal, wal or shm left behind


def test_no_secret_like_text_leaves_through_the_status_or_the_money_figures(live, late):
    blobs = [json.dumps(get_json(live, u)) for u in ("/api/performance", "/api/performance?run=all", "/api/performance?run=late-watch",
                                                      "/api/opportunities?limit=1000", "/api/opportunities?run=all&limit=1000",
                                                      "/api/orders?limit=1000", "/api/orders?run=all&limit=1000", "/api/status")]
    for blob in blobs:
        assert "abc-123" not in blob and "SHOULD-NOT-SHOW" not in blob and "KALSHI-ACCESS" not in blob
    rows = get_json(live, "/api/opportunities?limit=1000")["rows"]
    detail = trade_of(rows, late, "skipkey")["detail"]
    assert "[redacted]" in detail and trade_of(rows, late, "skipkey")["label"] == "Skipped · refused by the safety check"
    assert "response_json" not in json.dumps(get_json(live, "/api/orders?limit=1000"))
    won_order = next(r for r in get_json(live, "/api/orders?limit=1000")["rows"] if r["client_order_id"] == "c-won")
    assert won_order["response"] == {"headers": "[redacted]"}                                       # the header block is hidden whole


def test_performance_shape_is_stable(live, late):
    p = get_json(live, "/api/performance")
    assert set(p) == {"run_id", "all_runs", "server_time", "view", "strategy", "mode", "since", "truncated", "summary", "base",
                      "trades", "open"}
    assert set(p["summary"]) == set(server._summary_template())
    assert set(p["trades"][0]) == {"id", "run_id", "ts", "close_ts", "ticker", "side", "contracts", "entry", "paper_entry", "fees", "pnl",
                                   "paper_pnl", "payout", "result", "latency_ms", "cum", "cum_paper"}
    assert set(p["open"][0]) == {"id", "ts", "close_ts", "ticker", "side", "contracts", "state", "label", "entry"}
    assert set(get_json(live, "/api/opportunities")["rows"][0]["trade"]) == {
        "state", "label", "tone", "result", "real", "settled", "pnl", "paper_pnl", "paper_label", "close_ts", "side", "detail"}
    assert p["server_time"] == LATE_NOW


# ============================================================================================ the page words and graphs
def test_the_page_says_what_each_trade_is_in_words_and_never_calls_an_order_live():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for words in ("Settled", "Open · ", "settles at", "waiting for the result", "Overdue", "Skipped", "Not filled",
                  '"real money"', '"paper"', "LIVE bot running", "WATCH-ONLY bot running", "Real P&L (settled)", "Paper P&L (same trades)",
                  "Settled live trades", "Delay cost: none", "Win rate", "Average per trade", "Best trade", "Worst trade", "Fees paid",
                  "Avg fill delay", "Avg fill vs paper", "Open trades", "Wins", "Losses", "Loss used vs cap", "/api/performance"):
        assert words in js or words in html, words
    assert "dry run" not in js and "tag live" not in js                                           # the ambiguous LIVE tag is gone
    assert ".tag.live" not in (STATIC / "style.css").read_text(encoding="utf-8")
    assert "Trade status" in html and "<th>Money</th>" in html and "Order status" in html


def test_the_graphs_are_hand_drawn_svg_with_no_outside_code():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for text in (js, css, html):
        urls = re.findall(r"https?://[^\s\"'`)]+", text)
        assert set(urls) <= {"http://www.w3.org/2000/svg"}, urls                                   # no CDN, no font, no tracker
    assert re.search(r"<script[^>]*\bsrc=\"(?!/static/)", html) is None
    for hook in ("renderPnl", "renderBars", "renderGauge", "renderMoney", "renderTableView", "pnlNearest", "drawBarsHover", "moneyTicks"):
        assert f"function {hook}(" in js, hook
    assert 'S("path"' in js and "createElementNS" in js and "innerHTML" not in js                    # drawn element by element
    # light and dark: the colours of the new marks are tokens, redefined for dark mode, never hex literals in the rules
    for rule in (".bar-win", ".bar-loss", ".pnl-ring", ".gauge-fill", ".gauge-track", ".stat-value", ".key-rect"):
        block = re.search(re.escape(rule) + r"\s*\{([^}]*)\}", css)
        assert block, rule
        assert "#" not in block.group(1), (rule, block.group(1))
    assert "@media (prefers-color-scheme: dark)" in css and "--good" in css and "--critical" in css
    assert re.search(r"\.zero\s*\{[^}]*stroke-width:\s*2", css) and re.search(r"\.ln\s*\{[^}]*stroke-width:\s*2", css)   # 2 px lines


def test_the_page_degrades_when_the_money_endpoint_is_missing():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "async function pollPerf" in js and "state.perfError" in js and "restart it with: kpm dashboard" in js
    assert re.search(r"Promise\.all\(\[pollQuotes\(\), pollPerf\(st\)\]\)", js)                    # its own failure never reaches the main catch
    assert "function safely(" in js and "safely(renderGauge" in js


def test_the_figures_say_which_way_they_point_and_which_fills_they_cover():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "Delay cost (live − paper)" not in js                                                  # "+$0.01" under "cost" was a gain
    for words in ("Delay cost: none · real matched paper", "real beat paper by", "real fell behind paper"):
        assert words in js, words
    assert js.count("all fills, open ones too") == 2 and "on every real fill, open ones too" in js     # fees, fill delay, fill price
    assert "fmtUsd(t.fees)" in js and "fmtMoney(t.fees)" not in js and "fmtUsd(s.fees)" in js          # a fee of 0.09 cent is not "$0.00"


def test_the_choice_of_runs_is_remembered_without_depending_on_the_browser_allowing_it():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert re.search(r"try\s*\{\s*const v = window\.localStorage\.getItem\(SCOPE_KEY\)", js) and "catch (e) { return null; }" in js
    assert re.search(r"try\s*\{\s*window\.localStorage\.setItem\(SCOPE_KEY, v\);\s*\}\s*catch", js)
    assert 'st.run.mode === "live" ? "all" : "latest"' in js                                         # a restart must not blank the money
    assert "scope: null" in js and 'data-scope="latest" aria-pressed="false"' in (STATIC / "index.html").read_text(encoding="utf-8")


# ============================================================================================ the page in a real browser
def _find_chrome() -> str | None:
    for c in (os.environ.get("KPM_TEST_CHROME", ""), r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if c and Path(c).is_file():
            return c
    return None


def _node_major(node: str | None) -> int:
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
        return int(out.lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError, TypeError):
        return 0


CHROME, NODE = _find_chrome(), shutil.which("node")
PROBE = Path(__file__).resolve().parent / "dashboard_probe.js"


def browser_test(fn):
    """Marks a test that drives a real headless browser (skipped without Node 22+ and Chrome; run with -m browser)."""
    fn = pytest.mark.skipif(not (CHROME and NODE and PROBE.is_file()) or _node_major(NODE) < 22,
                            reason="needs Node 22+ and a Chrome-family browser (KPM_TEST_CHROME)")(fn)
    return pytest.mark.browser(fn)


def run_page(srv, width: int = 1366, scheme: str = "light", after_js: str = "", query_js: str = "") -> dict:
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    out = subprocess.run([NODE, str(PROBE), CHROME, url, str(width), scheme, "4000", "-", after_js, query_js], capture_output=True,
                         text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr[-600:]
    return json.loads(out.stdout.strip().splitlines()[-1])


# Hover the newest trade of the profit-and-loss chart and the newest bar of the result chart, as a finger or a mouse would.
HOVER_BOTH =("(() => { for (const sel of ['#pnl-svg > circle.dot.s1', '#bars-svg path.bar']) { const m = Array.from(document.querySelectorAll(sel)).pop(); "
              "const b = m.getBoundingClientRect(); m.ownerSVGElement.dispatchEvent(new PointerEvent('pointermove', "
              "{clientX: b.left + b.width / 2, clientY: b.top + b.height / 2, bubbles: true})); } })()")
PAGE_FACTS = ("JSON.stringify({pnlLines: document.querySelectorAll('#pnl-svg path.ln').length, "
              "realDots: document.querySelectorAll('#pnl-svg > circle.dot.s1').length, "
              "paperRings: document.querySelectorAll('#pnl-svg > circle.pnl-ring').length, "
              "bars: document.querySelectorAll('#bars-svg path.bar').length, wins: document.querySelectorAll('#bars-svg path.bar-win').length, "
              "losses: document.querySelectorAll('#bars-svg path.bar-loss').length, gauge: document.querySelectorAll('#gauge-svg rect.gauge-fill').length, "
              "stat: document.querySelectorAll('#money-tiles .stat').length, pnlTip: document.getElementById('pnl-tip').innerText, "
              "barsTip: document.getElementById('bars-tip').innerText, orderTags: Array.from(document.querySelectorAll('#orders-body .tag')).map(e => e.innerText), "
              "openRows: document.querySelectorAll('#opp-body tr.open').length, zeroLabel: !!Array.from(document.querySelectorAll('#pnl-svg text.zero-tick')).find(t => t.textContent === '$0'), "
              "scope: Array.from(document.querySelectorAll('[data-scope][aria-pressed=\"true\"]')).map(b => b.dataset.scope), "
              "scopeNote: document.getElementById('scope-note').innerText})")


@browser_test
def test_a_live_run_reads_status_money_graphs_tables_in_that_order(live, late):
    r = run_page(live, 1366, "light", HOVER_BOTH, PAGE_FACTS)
    assert r["problems"] == [] and r["layout"]["scrollWidth"] <= r["layout"]["innerWidth"] and r["layout"]["overflowing"] == []
    t, q = r["text"], json.loads(r["query"])
    for needle in ("LIVE bot running", "real orders, real money", "Money so far", "Real P&L (settled)", "−$0.72", "Settled live trades",
                   "Open trades", "Wins", "Losses", "Win rate", "50%", "Average per trade", "Best trade", "Worst trade", "Fees paid",
                   "Avg fill delay", "Avg fill vs paper", "Delay cost: none · real matched paper", "Profit and loss over time", "Result of each trade",
                   "Loss used vs cap", "Settled · WON +$0.25", "Settled · LOST −$0.96", "Open · settles at", "Skipped · price above 99c",
                   "Skipped · loss cap reached", "Not filled", "Trade status", "real money", "3 open trades", "next settles at",
                   "4 signals skipped (price above 99c ×1, loss cap reached ×1, blocked before sending ×1, refused by the safety check ×1)",
                   "detection to the order's answer · all fills, open ones too",
                   "per contract, negative = paid less than paper · all fills, open ones too"):
        assert needle in t, needle
    assert "dry run" not in t and "SHOULD-NOT-SHOW" not in t and "abc-123" not in t and "KALSHI-ACCESS" not in t
    assert "$0.0542" in t                                                       # fees paid, to the 4 decimals the orders table uses
    assert "fees paid $0.0027" in q["barsTip"]                                  # not "$0.00" for a trade that paid a fee
    assert q["scope"] == ["all"] and "every run so far" in q["scopeNote"]       # a live latest run: the money and graphs cover every run
    order = [t.index(s) for s in ("Trades this run", "Money so far", "Profit and loss over time", "Loss used vs cap", "Trades and signals", "Orders")]
    assert order == sorted(order)                                              # status, money, graphs, tables
    assert (q["pnlLines"], q["realDots"], q["paperRings"], q["bars"], q["wins"], q["losses"], q["gauge"]) == (2, 2, 2, 2, 1, 1, 1)
    assert q["zeroLabel"] is True and q["openRows"] >= 1 and q["stat"] == 13
    assert "Real money (running total)" in q["pnlTip"] and "Paper twin, same trades (running total)" in q["pnlTip"]
    assert "bought" in q["pnlTip"] and ("WON" in q["barsTip"] or "LOST" in q["barsTip"]) and "paper twin" in q["barsTip"]
    assert set(q["orderTags"]) <= {"real money", "paper", "not sent"} and "real money" in q["orderTags"]


@browser_test
def test_the_money_section_fits_a_phone_in_dark_mode(live, late):
    r = run_page(live, 390, "dark", HOVER_BOTH, PAGE_FACTS)
    assert r["problems"] == [] and r["layout"]["scrollWidth"] <= 390 and r["layout"]["overflowing"] == []
    assert "LIVE bot running" in r["text"] and "Real P&L (settled)" in r["text"] and json.loads(r["query"])["bars"] == 2


@browser_test
def test_a_watch_only_run_shows_paper_twins_and_says_so(live, late, cfg):
    path = Path(cfg["paths"]["db_path"])                                  # make the watch-only run the latest, and a running one
    db_exec(path, "UPDATE bot_runs SET started_ts = ?, ended_ts = NULL, end_reason = NULL WHERE run_id = 'late-watch'", [(NOW,)])
    db_exec(path, "INSERT INTO bot_heartbeat VALUES (?,?,?,?)", [("late-watch", LATE_NOW - 1, "monitoring", "{}")])
    r = run_page(live, 1366, "light", "", PAGE_FACTS)
    assert r["problems"] == [] and r["layout"]["overflowing"] == []
    for needle in ("WATCH-ONLY bot running", "Paper P&L (no real orders)", "Paper profit and loss over time", "Settled paper trades",
                   "Paper only", "paper twin WON +$0.24"):
        assert needle in r["text"], needle
    q = json.loads(r["query"])
    assert q["pnlLines"] == 1 and q["paperRings"] == 2 and q["realDots"] == 0 and q["bars"] == 2
    assert q["scope"] == ["latest"]                                           # a watch-only run keeps its own paper twins on screen


def _restart(path: Path) -> None:
    """What a restart of the live bot looks like: the old run ended, a new live run is beating, and it has traded nothing yet."""
    db_exec(path, "UPDATE bot_runs SET ended_ts = ?, end_reason = 'restart' WHERE run_id = 'late-live'", [(LATE_NOW - 60,)])
    db_exec(path, "INSERT INTO bot_runs VALUES (?,?,?,?,?,?,?)", [("late-new", "late_favorite", "live", LATE_NOW - 40, None, None, "{}")])
    db_exec(path, "INSERT INTO bot_heartbeat VALUES (?,?,?,?)", [("late-new", LATE_NOW - 1, "monitoring", "{}")])


@browser_test
def test_a_restart_of_the_bot_does_not_blank_the_money_figures_and_graphs(live, late, cfg):
    path = Path(cfg["paths"]["db_path"])
    assert get_json(live, "/api/performance")["summary"]["settled"] == 2
    _restart(path)
    assert get_json(live, "/api/performance")["summary"]["settled"] == 0                   # the latest run alone has nothing to show
    r = run_page(live, 1366, "light", "", PAGE_FACTS)
    assert r["problems"] == [] and r["layout"]["overflowing"] == []
    q = json.loads(r["query"])
    assert q["scope"] == ["all"] and "every run so far" in q["scopeNote"]
    assert "−$0.72" in r["text"] and "Settled live trades" in r["text"] and "No settled trades yet" not in r["text"]
    assert (q["pnlLines"], q["realDots"], q["bars"], q["wins"], q["losses"]) == (2, 2, 2, 1, 1)
    # one click for the latest run alone, and the browser remembers the choice
    pick = "document.querySelector('[data-scope=\"latest\"]').click()"
    saved = ("JSON.stringify({scope: Array.from(document.querySelectorAll('[data-scope][aria-pressed=\"true\"]')).map(b => b.dataset.scope), "
             "stored: window.localStorage.getItem('kpm.dashboard.scope')})")
    r = run_page(live, 1366, "light", pick, saved)
    assert r["problems"] == [] and json.loads(r["query"]) == {"scope": ["latest"], "stored": "latest"}


@browser_test
def test_a_fill_the_bot_has_not_booked_yet_is_open_in_the_tables_the_filter_and_the_tile(live, late, cfg):
    path = Path(cfg["paths"]["db_path"])
    booking = add_opp(path, "booking", LATE_NOW - 3, outcome=None, fill=None)
    add_order(path, "c-booking", booking, "booking", LATE_NOW - 3)
    sent = add_opp(path, "halfway", LATE_NOW - 2, outcome=None, fill=None)
    add_order(path, "c-halfway", sent, "halfway", LATE_NOW - 2, status="sent", filled=None)
    rows = ("JSON.stringify({signals: Array.from(document.querySelectorAll('#opp-body tr')).map(tr => tr.innerText), "
            "orders: Array.from(document.querySelectorAll('#orders-body tr')).map(tr => tr.innerText), "
            "openTile: Array.from(document.querySelectorAll('#money-tiles .stat')).find(s => s.querySelector('.stat-label').textContent === 'Open trades')"
            ".querySelector('.stat-value').textContent})")
    r = run_page(live, 1366, "light", "document.querySelector('[data-opps=\"real\"]').click()", rows)
    assert r["problems"] == [] and r["layout"]["overflowing"] == []
    q = json.loads(r["query"])
    only_real = " | ".join(q["signals"])
    assert "KXBTC15M-booking" in only_real and "KXBTC15M-halfway" not in only_real          # the real-trades filter has the fill, not the half-sent one
    booked = next(o for o in q["orders"] if "KXBTC15M-booking" in o)
    assert re.search(r"Open · settles at \d\d:\d\d", booked) and "check Kalshi" not in booked and "Filled" not in booked   # the old false alarm
    assert "Sent · waiting for the exchange" in next(o for o in q["orders"] if "KXBTC15M-halfway" in o)
    assert q["openTile"] == "4"                                                              # the three that were open, and this one


@browser_test
def test_the_delay_cost_is_never_shown_with_a_plus_sign_when_the_real_trades_did_better(live, late, cfg):
    path = Path(cfg["paths"]["db_path"])
    tile = "Array.from(document.querySelectorAll('#money-tiles .stat')).find(s => s.querySelector('.stat-label').textContent === 'Paper P&L (same trades)').innerText"
    assert "Delay cost: none · real matched paper" in run_page(live, 1366, "light", "", tile)["query"]       # 2 trades, 0.02 cent apart
    db_exec(path, "UPDATE bot_opportunities SET realized_pnl = realized_pnl + 0.05 WHERE id = ?", [(late["won"],)])
    better = run_page(live, 1366, "light", "", tile)
    assert "Delay cost: none · real beat paper by $0.05" in better["query"]                  # the real fill did 5 cents better: no cost
    assert "Delay cost (live" not in better["text"] and "+$0.05" not in better["query"]
    db_exec(path, "UPDATE bot_opportunities SET realized_pnl = realized_pnl - 0.15 WHERE id = ?", [(late["won"],)])
    worse = run_page(live, 1366, "light", "", tile)
    assert "Delay cost: $0.10 · real fell behind paper" in worse["query"]                    # 10 cents behind: that is a cost
