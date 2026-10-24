"""BotState: the writer of the dashboard's bot_* tables (src/kpm/core/schema.sql, read by kpm.dashboard.server).

Offline: in-memory SQLite for the unit tests, a temp file for the tests that read the rows back the way the dashboard
does (a separate read-only connection).
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from decimal import Decimal as D
from importlib import resources
from pathlib import Path

import pytest

from kpm.dashboard import server
from kpm.execution import botstate
from kpm.execution.botstate import BotState, clean_json, new_run_id

NOW = 1_791_226_800.0


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def bs(conn):
    return BotState(conn, "run-1", clock=lambda: NOW)


def rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    cur = conn.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


# ============================================================================================ construction and schema
def test_the_bot_tables_are_created_from_schema_sql_when_missing(conn):
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] == 0
    BotState(conn, "r")
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert set(botstate.TABLES) <= have


def test_the_bot_tables_in_schema_sql_match_what_botstate_writes():
    sql = resources.files("kpm.core").joinpath("schema.sql").read_text(encoding="utf-8")
    for table in botstate.TABLES:
        assert re.search(rf"CREATE TABLE IF NOT EXISTS {table}\b", sql), table


def test_existing_tables_are_left_alone(conn):
    schema = resources.files("kpm.core").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.execute("INSERT INTO bot_runs (run_id, strategy, mode, started_ts) VALUES ('old', 'pair_h2', 'watch', 1)")
    conn.commit()
    BotState(conn, "new")
    assert conn.execute("SELECT COUNT(*) FROM bot_runs").fetchone()[0] == 1


@pytest.mark.parametrize("bad", ["", "   ", None, 5])
def test_run_id_must_be_a_non_empty_string(conn, bad):
    with pytest.raises(ValueError):
        BotState(conn, bad)


def test_new_run_id_is_sortable_unique_and_secret_free():
    a, b = new_run_id("late_favorite", now=NOW), new_run_id("late_favorite", now=NOW)
    assert a != b and a.startswith("late_favorite-20261005T190000Z-")
    assert re.fullmatch(r"late_favorite-\d{8}T\d{6}Z-[0-9a-f]{6}", a)
    assert new_run_id("pair_h2", now=NOW + 3600) > new_run_id("pair_h2", now=NOW)


# ============================================================================================ bot_runs
def test_start_writes_the_run_row(bs, conn):
    bs.start("late_favorite", "watch", {"max_loss_usd": D("5.00"), "contracts": 1}, ts=NOW)
    (r,) = rows(conn, "SELECT * FROM bot_runs")
    assert (r["run_id"], r["strategy"], r["mode"], r["started_ts"], r["ended_ts"], r["end_reason"]) == \
           ("run-1", "late_favorite", "watch", NOW, None, None)
    assert json.loads(r["config_json"]) == {"contracts": 1, "max_loss_usd": "5.00"}       # Decimals stay exact
    assert bs.run_row() == r


def test_start_validates_mode_and_strategy(bs):
    with pytest.raises(ValueError, match="mode"):
        bs.start("late_favorite", "paper")
    with pytest.raises(ValueError, match="strategy"):
        bs.start("", "watch")
    with pytest.raises(ValueError, match="strategy"):
        bs.start(None, "live")


def test_a_run_id_is_used_once(bs):
    bs.start("late_favorite", "watch")
    with pytest.raises(sqlite3.IntegrityError):
        bs.start("late_favorite", "watch")


def test_end_keeps_the_first_end_time_and_reason(bs):
    bs.start("late_favorite", "live", ts=NOW)
    bs.end("max loss reached", ts=NOW + 10)
    bs.end("finished", ts=NOW + 99)
    r = bs.run_row()
    assert (r["ended_ts"], r["end_reason"]) == (NOW + 10, "max loss reached")


def test_the_run_config_never_carries_secrets(bs):
    bs.start("late_favorite", "live", {
        "env": "prod", "key_fp": "ab12cd34", "KALSHI_API_KEY_ID": "11111111-2222-3333-4444-555555555555",
        "private_key_path": r"C:\keys\kalshi.pem", "api_key": "k", "signature": "sig", "Authorization": "Bearer x",
        "headers": {"KALSHI-ACCESS-KEY": "x"}, "token": "t", "password": "p", "pem_text": "x",
        "note": "-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----",
        "nested": {"secret": 1, "ok": 2}, "list": [{"key_id": "x", "fine": 1}]})
    cfg = json.loads(bs.run_row()["config_json"])
    assert cfg["env"] == "prod" and cfg["key_fp"] == "ab12cd34"          # the fingerprint is the only identifier
    assert cfg["note"] == "[redacted]" and cfg["nested"] == {"ok": 2} and cfg["list"] == [{"fine": 1}]
    for dropped in ("KALSHI_API_KEY_ID", "private_key_path", "api_key", "signature", "Authorization", "headers",
                    "token", "password", "pem_text"):
        assert dropped not in cfg
    assert "11111111" not in bs.run_row()["config_json"] and "kalshi.pem" not in bs.run_row()["config_json"]


# ============================================================================================ heartbeat
def test_heartbeat_is_one_row_overwritten(bs, conn):
    bs.start("late_favorite", "watch")
    bs.heartbeat("waiting", {"market_ticker": "A"}, ts=NOW)
    bs.heartbeat("monitoring", {"market_ticker": "B"}, ts=NOW + 1)
    (r,) = rows(conn, "SELECT * FROM bot_heartbeat")
    assert (r["run_id"], r["ts"], r["status"]) == ("run-1", NOW + 1, "monitoring")
    assert json.loads(r["detail_json"])["market_ticker"] == "B"


@pytest.mark.parametrize("status", ["waiting", "monitoring", "trading", "stopped", "error"])
def test_heartbeat_statuses_the_dashboard_knows(bs, status):
    bs.heartbeat(status, {})
    assert bs.heartbeat_row()["status"] == status


def test_heartbeat_refuses_an_unknown_status(bs):
    with pytest.raises(ValueError, match="status"):
        bs.heartbeat("sleeping")


def test_heartbeat_detail_always_has_the_contract_keys(bs):
    bs.start("late_favorite", "live")
    bs.heartbeat("monitoring", {})
    d = bs.heartbeat_row()["detail"]
    assert set(botstate.STANDARD_DETAIL) <= set(d)
    assert d["strategy"] == "late_favorite" and d["mode"] == "live" and d["status"] == "monitoring"
    for k in ("market_ticker", "window_end_ts", "loss_used_usd", "max_loss_usd", "balance_usd", "kill_switch", "last_error"):
        assert d[k] is None                                    # unknown is null, never missing


def test_heartbeat_keeps_the_callers_values_and_extras(bs):
    bs.start("late_favorite", "watch")
    bs.heartbeat("trading", {"market_ticker": "KXBTC15M-X", "window_end_ts": 123.5, "loss_used_usd": D("1.25"),
                             "max_loss_usd": D("5.00"), "balance_usd": D("29.43"), "kill_switch": True,
                             "last_error": "boom", "polls": 7})
    d = bs.heartbeat_row()["detail"]
    assert d["market_ticker"] == "KXBTC15M-X" and d["window_end_ts"] == 123.5 and d["kill_switch"] is True
    assert d["loss_used_usd"] == 1.25 and d["max_loss_usd"] == 5.0 and d["balance_usd"] == 29.43     # numbers for the page
    assert d["last_error"] == "boom" and d["polls"] == 7


def test_the_status_argument_wins_over_a_status_inside_the_detail(bs):
    bs.heartbeat("stopped", {"status": "monitoring"})
    d = bs.heartbeat_row()
    assert d["status"] == "stopped" and d["detail"]["status"] == "stopped"


def test_heartbeat_aliases_hour_ticker_and_market_ticker(bs):
    bs.heartbeat("monitoring", {"hour_ticker": "KXBTC15M-H"})                   # the pair bot's key
    d = bs.heartbeat_row()["detail"]
    assert d["market_ticker"] == d["hour_ticker"] == "KXBTC15M-H"
    bs.heartbeat("monitoring", {"market_ticker": "KXBTC15M-M"})                 # the late_favorite bot's key
    d = bs.heartbeat_row()["detail"]
    assert d["market_ticker"] == d["hour_ticker"] == "KXBTC15M-M"              # the dashboard tile reads hour_ticker


def test_heartbeat_identity_comes_from_bot_runs_when_start_was_another_objects_job(conn):
    BotState(conn, "run-9").start("pair_h2", "live")
    other = BotState(conn, "run-9")
    other.heartbeat("monitoring", {})
    d = other.heartbeat_row()["detail"]
    assert (d["strategy"], d["mode"]) == ("pair_h2", "live")


def test_heartbeat_before_any_run_row_has_null_identity(bs):
    bs.heartbeat("waiting", {})
    d = bs.heartbeat_row()["detail"]
    assert d["strategy"] is None and d["mode"] is None


def test_heartbeat_numbers_are_strict_json_even_for_nan_and_inf(bs, conn):
    bs.heartbeat("monitoring", {"loss_used_usd": float("nan"), "balance_usd": D("Infinity"), "x": float("inf")})
    raw = conn.execute("SELECT detail_json FROM bot_heartbeat").fetchone()[0]
    d = json.loads(raw, parse_constant=lambda c: pytest.fail(f"non-strict JSON constant {c}"))
    assert d["loss_used_usd"] is None and d["balance_usd"] is None and d["x"] is None


def test_heartbeat_detail_drops_secrets(bs):
    bs.heartbeat("monitoring", {"key_id": "x", "api_key_path": "y", "last_error": "fine", "Authorization": "z"})
    d = bs.heartbeat_row()["detail"]
    assert d["last_error"] == "fine" and not ({"key_id", "api_key_path", "Authorization"} & set(d))


def test_heartbeat_ts_defaults_to_the_clock(bs):
    bs.heartbeat("waiting", {})
    assert bs.heartbeat_row()["ts"] == NOW


# ============================================================================================ events
def test_event_returns_its_id_and_stores_json_data(bs, conn):
    a = bs.event("info", "start", "hello", {"price": D("0.9500"), "n": 3}, ts=NOW)
    b = bs.event("error", "error", "boom")
    assert b == a + 1
    r = rows(conn, "SELECT * FROM bot_events ORDER BY id")
    assert r[0]["run_id"] == "run-1" and r[0]["ts"] == NOW and r[0]["level"] == "info" and r[0]["kind"] == "start"
    assert json.loads(r[0]["data_json"]) == {"n": 3, "price": "0.9500"}
    assert r[1]["data_json"] is None and r[1]["ts"] == NOW


def test_event_accepts_warning_as_warn(bs):
    bs.event("warning", "cap", "x")
    assert bs.event_rows()[0]["level"] == "warn"


@pytest.mark.parametrize("kind", list(botstate.EVENT_KINDS))
def test_every_documented_event_kind_is_accepted(bs, kind):
    bs.event("info", kind, "m")
    assert bs.event_rows()[0]["kind"] == kind


def test_event_validation(bs):
    with pytest.raises(ValueError, match="level"):
        bs.event("fatal", "start", "x")
    with pytest.raises(ValueError, match="kind"):
        bs.event("info", "party", "x")
    with pytest.raises(ValueError, match="message"):
        bs.event("info", "start", "  ")
    assert bs.event_rows() == []


def test_event_message_is_cut_and_secrets_in_data_are_dropped(bs):
    bs.event("info", "order", "x" * 9000, {"signature": "s", "ok": 1, "pem": "-----BEGIN RSA PRIVATE KEY-----"})
    r = bs.event_rows()[0]
    assert len(r["message"]) == 4000 and json.loads(r["data_json"]) == {"ok": 1}


# ============================================================================================ quotes
QUOTE = dict(ts=NOW, hour_ticker="H", strike_ticker="S1", kind="hourly YES + 15m DOWN", leg_a_ask=D("0.55"),
             leg_a_size=D("10"), leg_b_ask=D("0.46"), leg_b_size=D("20"), cost=D("1.01"), fees=D("0.02"), edge=D("-0.03"))


def test_quotes_are_stored_as_numbers(bs, conn):
    assert bs.quotes([QUOTE, dict(QUOTE, strike_ticker="S2", ts=NOW + 1)]) == 2
    r = rows(conn, "SELECT * FROM bot_quotes ORDER BY ts")
    assert r[0]["run_id"] == "run-1" and r[0]["leg_a_ask"] == 0.55 and r[0]["edge"] == -0.03 and r[0]["leg_b_size"] == 20.0
    assert [x["strike_ticker"] for x in r] == ["S1", "S2"]


def test_the_same_ts_and_strike_replaces(bs, conn):
    bs.quote(**QUOTE)
    bs.quote(**dict(QUOTE, edge=D("0.01")))
    (r,) = rows(conn, "SELECT * FROM bot_quotes")
    assert r["edge"] == 0.01


def test_a_bad_quote_row_writes_nothing(bs, conn):
    with pytest.raises(ValueError, match="missing"):
        bs.quotes([QUOTE, {k: v for k, v in dict(QUOTE, strike_ticker="S2").items() if k != "edge"}])
    assert conn.execute("SELECT COUNT(*) FROM bot_quotes").fetchone()[0] == 0
    assert bs.quotes([]) == 0


def test_quote_values_may_be_null_for_an_empty_side(bs, conn):
    bs.quote(**dict(QUOTE, leg_a_ask=None, leg_a_size=None, cost=None, edge=None))
    r = rows(conn, "SELECT * FROM bot_quotes")[0]
    assert r["leg_a_ask"] is None and r["edge"] is None and r["leg_b_ask"] == 0.46


# ============================================================================================ opportunities
def opp(bs: BotState, **over) -> int:
    kw = dict(detected_ts=NOW, hour_ticker="H", strike_ticker="S", kind="late favorite YES", contracts=D("1"),
              paper_cost=D("0.95"), paper_fees=D("0.003325"), paper_edge=None, live=False, outcome=None)
    kw.update(over)
    return bs.opportunity(**kw)


def test_opportunity_roundtrip_with_a_null_edge(bs):
    i = opp(bs, outcome="paper_only")
    (r,) = bs.opportunity_rows()
    assert r["id"] == i and r["run_id"] == "run-1" and r["detected_ts"] == NOW and r["contracts"] == 1.0
    assert r["paper_cost"] == 0.95 and r["paper_fees"] == 0.003325 and r["paper_edge"] is None
    assert r["live"] == 0 and r["outcome"] == "paper_only" and r["realized_pnl"] is None
    assert bs.opportunity_row(i) == r


@pytest.mark.parametrize("outcome", list(botstate.OUTCOMES))
def test_every_outcome_is_accepted(bs, outcome):
    opp(bs, outcome=outcome)
    assert bs.opportunity_rows()[0]["outcome"] == outcome


def test_filled_is_an_outcome_for_single_order_strategies():
    assert "filled" in botstate.OUTCOMES and "both_filled" in botstate.OUTCOMES


def test_opportunity_validation(bs):
    with pytest.raises(ValueError, match="outcome"):
        opp(bs, outcome="won")
    with pytest.raises(ValueError, match="hour_ticker"):
        opp(bs, hour_ticker="")
    with pytest.raises(TypeError):
        opp(bs, paper_cost=True)
    with pytest.raises(ValueError, match="finite"):
        opp(bs, paper_fees=float("nan"))
    assert bs.opportunity_rows() == []


def test_update_opportunity_columns(bs):
    i = opp(bs)
    bs.update_opportunity(i, live=True, outcome="filled", live_cost=D("0.949"), live_fees="0.0034", latency_ms=123.4,
                          payout=D("1"), realized_pnl=D("0.0476"), paper_realized_pnl=D("0.046675"))
    r = bs.opportunity_row(i)
    assert r["live"] == 1 and r["outcome"] == "filled" and r["live_cost"] == 0.949 and r["live_fees"] == 0.0034
    assert r["latency_ms"] == 123.4 and r["payout"] == 1.0 and r["realized_pnl"] == 0.0476
    bs.update_opportunity(i, live=False, outcome=None)
    r = bs.opportunity_row(i)
    assert r["live"] == 0 and r["outcome"] is None
    bs.update_opportunity(i)                                    # nothing to update is fine


def test_update_opportunity_refuses_other_columns_and_bad_outcomes(bs):
    i = opp(bs)
    with pytest.raises(ValueError, match="cannot update"):
        bs.update_opportunity(i, paper_cost=D("1"))
    with pytest.raises(ValueError, match="cannot update"):
        bs.update_opportunity(i, run_id_="x")
    with pytest.raises(ValueError, match="outcome"):
        bs.update_opportunity(i, outcome="great")
    assert bs.opportunity_row(i)["paper_cost"] == 0.95


def test_update_opportunity_is_scoped_to_the_run_unless_told_otherwise(conn):
    old, new = BotState(conn, "run-old"), BotState(conn, "run-new")
    i = opp(old)
    new.update_opportunity(i, paper_realized_pnl=D("9"))                      # another run's row: not touched
    assert old.opportunity_row(i)["paper_realized_pnl"] is None
    new.update_opportunity(i, run_id="run-old", paper_realized_pnl=D("0.5"))   # a later run settling it explicitly
    assert old.opportunity_row(i)["paper_realized_pnl"] == 0.5
    assert new.opportunity_rows() == []


def test_unsettled_opportunities_lists_other_runs_pending_rows_of_one_strategy(conn):
    a, b, me = BotState(conn, "ra"), BotState(conn, "rb"), BotState(conn, "me")
    a.start("late_favorite", "watch"), b.start("pair_h2", "watch"), me.start("late_favorite", "live")
    x = opp(a, detected_ts=100)
    done = opp(a, detected_ts=110)
    a.update_opportunity(done, paper_realized_pnl=D("0.1"))
    early = opp(a, detected_ts=5)
    late = opp(a, detected_ts=500)
    opp(b, detected_ts=120)                                    # another strategy
    opp(me, detected_ts=130)                                   # this run's own row
    got = me.unsettled_opportunities("late_favorite", detected_between=(50, 200))
    assert [r["id"] for r in got] == [x]
    assert {r["id"] for r in me.unsettled_opportunities("late_favorite", detected_between=(0, 1000))} == {x, early, late}


def test_live_orders_of_an_opportunity_exclude_dry_run_rows(conn):
    a = BotState(conn, "ra")
    i = opp(a)
    a.order(client_order_id="paper-1", ticker="T", side="yes", action="buy", count=1, limit_price=D("0.95"), dry_run=True,
            opportunity_id=i)
    a.order(client_order_id="live-1", ticker="T", side="yes", action="buy", count=1, limit_price=D("0.95"), dry_run=False,
            opportunity_id=i, ts_sent=1)
    other = BotState(conn, "rb")
    assert [o["client_order_id"] for o in other.live_orders_of("ra", i)] == ["live-1"]
    assert other.live_orders_of("ra", i + 100) == []


# ============================================================================================ orders
def order(bs: BotState, coid: str = "c-1", **over) -> None:
    kw = dict(client_order_id=coid, ticker="KXBTC15M-X", side="yes", action="buy", count=D("1"), limit_price=D("0.9500"),
              tif="ioc", status="sent", ts_sent=NOW, dry_run=False)
    kw.update(over)
    bs.order(**kw)


def test_order_roundtrip(bs):
    order(bs, opportunity_id=3, response={"request": {"price": "0.9500"}})
    (o,) = bs.order_rows()
    assert o["client_order_id"] == "c-1" and o["run_id"] == "run-1" and o["opportunity_id"] == 3
    assert (o["side"], o["action"], o["count"], o["limit_price"], o["tif"], o["status"]) == ("yes", "buy", 1.0, 0.95, "ioc", "sent")
    assert o["dry_run"] == 0 and o["ts_ack"] is None and o["filled"] is None
    assert json.loads(o["response_json"]) == {"request": {"price": "0.9500"}}


def test_order_is_replaced_on_the_same_client_order_id(bs):
    order(bs)
    order(bs, status="executed", filled=D("1"), avg_fill_price=D("0.949"), fees=D("0.0034"), ts_ack=NOW + 0.2,
          order_id="ord-1")
    (o,) = bs.order_rows()
    assert o["status"] == "executed" and o["filled"] == 1.0 and o["avg_fill_price"] == 0.949 and o["order_id"] == "ord-1"


def test_order_side_is_the_outcome_not_the_book_side(bs):
    for bad in ("bid", "ask", "YES", ""):
        with pytest.raises(ValueError, match="side"):
            order(bs, side=bad)
    with pytest.raises(ValueError, match="action"):
        order(bs, action="hold")
    order(bs, side="no", action="sell")
    assert bs.order_rows()[0]["side"] == "no"


def test_order_dry_run_flag_is_stored_as_an_int(bs):
    order(bs, "d-1", dry_run=True)
    order(bs, "d-2", dry_run=False)
    assert [o["dry_run"] for o in bs.order_rows()] == [1, 0]


def test_update_order(bs):
    order(bs)
    bs.update_order("c-1", status="partial", filled=D("0.4"), avg_fill_price="0.95", fees=D("0.001"), ts_ack=NOW + 1,
                    order_id="ord-9", opportunity_id=4, response={"data": {"fill_count": "0.40"}})
    (o,) = bs.order_rows()
    assert o["status"] == "partial" and o["filled"] == 0.4 and o["order_id"] == "ord-9" and o["opportunity_id"] == 4
    assert json.loads(o["response_json"]) == {"data": {"fill_count": "0.40"}}
    bs.update_order("c-1", opportunity_id=None, status=None)
    o = bs.order_rows()[0]
    assert o["opportunity_id"] is None and o["status"] is None
    bs.update_order("nope", status="x")                         # an unknown id updates nothing
    bs.update_order("c-1")
    with pytest.raises(ValueError, match="cannot update"):
        bs.update_order("c-1", limit_price=D("0.5"))
    with pytest.raises(ValueError, match="cannot update"):
        bs.update_order("c-1", side="no")


def test_order_response_never_carries_credentials(bs):
    order(bs, response={"request": {"ticker": "T"}, "headers": {"KALSHI-ACCESS-SIGNATURE": "x"}, "private_key": "k",
                        "note": "-----BEGIN PRIVATE KEY-----abc"})
    text = bs.order_rows()[0]["response_json"]
    assert "KALSHI-ACCESS" not in text and "private_key" not in text and "BEGIN" not in text and "[redacted]" in text


# ============================================================================================ value conversion
@pytest.mark.parametrize("v,expected", [(D("0.95"), 0.95), (3, 3.0), ("0.0034", 0.0034), (" 1.5 ", 1.5), (None, None), (0.5, 0.5)])
def test_real_conversion(v, expected):
    assert botstate._real(v, "x") == expected


@pytest.mark.parametrize("v,exc", [(True, TypeError), ("abc", Exception), (float("inf"), ValueError), (float("nan"), ValueError),
                                   (D("NaN"), ValueError), ([1], TypeError)])
def test_real_conversion_refuses_garbage(v, exc):
    with pytest.raises(exc):
        botstate._real(v, "x")


def test_clean_json_handles_the_types_bots_pass():
    out = clean_json({"d": D("1.50"), "t": (1, D("2")), "s": {3}, "b": b"raw", "p": Path("x"), "n": None, "f": 1.5,
                      "bool": True, "inf": math.inf, "nested": {"signature": "x", "v": [D("0.1")]}, 5: "int key"})
    assert out == {"d": "1.50", "t": [1, "2"], "s": [3], "b": None, "p": "x", "n": None, "f": 1.5, "bool": True,
                   "inf": None, "nested": {"v": ["0.1"]}, "5": "int key"}
    assert clean_json({"d": D("1.5")}, decimals="float") == {"d": 1.5}
    json.dumps(out, allow_nan=False)


# ============================================================================================ commit behaviour
def test_every_write_is_committed_at_once_for_the_dashboards_other_connection(tmp_path):
    path = tmp_path / "bot.sqlite"
    writer = sqlite3.connect(path)
    reader = sqlite3.connect(path)
    s = BotState(writer, "r1")
    s.start("late_favorite", "watch")
    assert reader.execute("SELECT COUNT(*) FROM bot_runs").fetchone()[0] == 1
    s.heartbeat("waiting", {})
    assert reader.execute("SELECT status FROM bot_heartbeat").fetchone()[0] == "waiting"
    s.event("info", "start", "x")
    assert reader.execute("SELECT COUNT(*) FROM bot_events").fetchone()[0] == 1
    s.quote(**QUOTE)
    assert reader.execute("SELECT COUNT(*) FROM bot_quotes").fetchone()[0] == 1
    opp(s)
    assert reader.execute("SELECT COUNT(*) FROM bot_opportunities").fetchone()[0] == 1
    order(s)
    assert reader.execute("SELECT COUNT(*) FROM bot_orders").fetchone()[0] == 1
    writer.close(), reader.close()


def test_a_failed_write_does_not_roll_back_work_another_writer_left_open(conn):
    s = BotState(conn, "r1")
    s.start("late_favorite", "watch")
    conn.execute("INSERT INTO audit_log (ts, component, action) VALUES (1, 'x', 'pending')")     # an open transaction
    assert conn.in_transaction
    with pytest.raises(sqlite3.IntegrityError):
        s.start("late_favorite", "watch")                       # primary key violation inside BotState._write
    assert conn.in_transaction                                  # not rolled back: it is not BotState's transaction
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1


def test_a_failed_write_rolls_back_its_own_transaction(conn):
    s = BotState(conn, "r1")
    s.start("late_favorite", "watch")
    with pytest.raises(sqlite3.IntegrityError):
        s.start("late_favorite", "watch")
    assert not conn.in_transaction


# ============================================================================================ the dashboard reads it
def test_the_dashboard_api_reads_what_botstate_wrote(tmp_path):
    path = tmp_path / "kpm.sqlite"
    conn = sqlite3.connect(path)
    s = BotState(conn, "run-lf", clock=lambda: NOW)
    s.start("late_favorite", "live", {"max_loss_usd": D("5.0"), "key_fp": "ab12cd34"}, ts=NOW - 100)
    i = opp(s, kind="late favorite YES", outcome="filled", live=True)
    s.update_opportunity(i, live_cost=D("0.949"), live_fees=D("0.0034"), latency_ms=180.0, payout=D("1"),
                         realized_pnl=D("0.0476"), paper_realized_pnl=D("0.046675"))
    order(s, "c-1", opportunity_id=i, status="executed", filled=D("1"), avg_fill_price=D("0.949"), fees=D("0.0034"),
          ts_ack=NOW + 0.18, response={"request": {"price": "0.9500"}, "data": {"fill_count": "1.00"}})
    s.event("warn", "cap", "live order not sent: balance too low", {"ticker": "T"})
    s.heartbeat("monitoring", {"market_ticker": "KXBTC15M-26OCT051900-00", "window_end_ts": NOW + 60,
                               "loss_used_usd": D("0.95"), "max_loss_usd": D("5.0"), "balance_usd": D("29.43"),
                               "kill_switch": False, "last_error": None})
    conn.close()

    ro = server._open_db(path)
    cfg = {"trading": {"max_loss_usd": 5.0, "kill_switch_file": str(tmp_path / "KILL")}, "dashboard": {"refresh_s": 2}}
    status = server.api_status(ro, cfg, {}, NOW + 2)
    assert status["run"]["strategy"] == "late_favorite" and status["run"]["mode"] == "live"
    assert status["run"]["config"] == {"max_loss_usd": "5.0", "key_fp": "ab12cd34"}
    hb = status["heartbeat"]
    assert hb["status"] == "monitoring" and hb["age_s"] == pytest.approx(NOW + 2 - NOW)
    assert hb["detail"]["loss_used_usd"] == 0.95 and hb["detail"]["balance_usd"] == 29.43
    assert hb["detail"]["hour_ticker"] == hb["detail"]["market_ticker"] == "KXBTC15M-26OCT051900-00"
    stats = status["stats"]["run"]
    assert stats["opportunities"] == 1 and stats["live_attempts"] == 1 and stats["live_orders"] == 1
    assert stats["realized_pnl"] == pytest.approx(0.0476) and stats["paper_realized_pnl"] == pytest.approx(0.046675)
    opps = server.api_opportunities(ro, cfg, {}, NOW)["rows"]
    assert opps[0]["kind"] == "late favorite YES" and opps[0]["outcome"] == "filled" and opps[0]["live_edge"] is None
    orders = server.api_orders(ro, cfg, {}, NOW)["rows"]
    assert orders[0]["status"] == "executed" and orders[0]["response"] == {"request": {"price": "0.9500"},
                                                                         "data": {"fill_count": "1.00"}}
    events = server.api_events(ro, cfg, {}, NOW)["rows"]
    assert events[0]["kind"] == "cap" and events[0]["data"] == {"ticker": "T"}
    ro.close()


def test_the_dashboard_shows_the_pair_strategys_rows_unchanged(tmp_path):
    path = tmp_path / "kpm.sqlite"
    conn = sqlite3.connect(path)
    s = BotState(conn, "run-pair", clock=lambda: NOW)
    s.start("pair_h2", "watch", {}, ts=NOW)
    s.quote(**QUOTE)
    s.opportunity(detected_ts=NOW, hour_ticker="H", strike_ticker="KXBTCD-26OCT0519-T85000", kind="hourly YES + 15m DOWN",
                  contracts=D("1"), paper_cost=D("0.97"), paper_fees=D("0.02"), paper_edge=D("0.01"), live=False,
                  outcome="paper_only")
    conn.close()
    ro = server._open_db(path)
    cfg = {"trading": {}, "dashboard": {}}
    assert server.api_quotes(ro, cfg, {"minutes": ["10"]}, NOW + 5)["rows"][0]["strike_ticker"] == "S1"
    (o,) = server.api_opportunities(ro, cfg, {}, NOW)["rows"]
    assert o["paper_edge"] == pytest.approx(0.01) and o["outcome"] == "paper_only"
    ro.close()
