"""Several 15-minute windows in a row through the real stack, then the dashboard's own numbers on the result.

One live run of 0.8 hours covers four KXBTC15M markets. The first closes 130 s after the start; the books are scripted per
window: a YES favorite that wins, no signal at all, a NO favorite that wins, a YES favorite that loses. The test checks the
per-window state resets, the ledger and cap arithmetic across windows, the session counter, a restart that must not trade
a window twice, and that kpm.dashboard reads what the bot wrote (settled live P&L next to its paper twin).
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from kpm.dashboard import server
from kpm.execution import cli, units

from .cli_world import World
from .exec_fakes import CLOSE_TS, TICKER

FEE_YES_73 = units.taker_fee(D(1), D("0.73"))
FEE_NO_95 = units.taker_fee(D(1), D("0.95"))
FEE_998 = units.taker_fee(D(1), D("0.998"))             # 0.07 x 0.998 x 0.002 = 0.00013972, rounded up to $0.000001
PNL_1 = 1 - D("0.73") - FEE_YES_73                      # window 1: bought YES at 0.73, YES won
PNL_3 = 1 - D("0.95") - FEE_NO_95                       # window 3: bought NO at 0.95, NO won
PNL_4 = -D("0.73") - FEE_YES_73                         # window 4: bought YES at 0.73, NO won
PNL_998_WON = 1 - D("0.998") - FEE_998                  # a favorite bought at 0.998 that wins: under a fifth of a cent
PNL_998_LOST = -D("0.998") - FEE_998                    # ... and one that loses: the whole price plus the fee


@pytest.fixture
def world(tmp_path, monkeypatch, ed25519_key, fake_clock):
    def make(**kw) -> World:
        w = World(tmp_path, monkeypatch, ed25519_key, fake_clock, **kw)
        w.sim.results[next(iter(w.sim.markets))] = "yes"                       # window 1 settles YES
        w.windows = [next(iter(w.sim.markets)), *w.sim.add_following_markets(
            3, books=[("0.50", "0.49"), ("0.05", "0.94"), ("0.72", "0.27")], results=["yes", "no", "no"])]
        return w

    return make


def run_live(w: World, **kw):
    return w.run_bot(live=True, env="prod", hours=0.8, **kw)


def test_four_windows_trade_only_where_the_rule_fires_and_every_trade_settles(world):
    w = world(enabled=True)
    assert run_live(w) == 0, w.text
    sent = [(b["ticker"], b["side"], b["price"]) for b in w.sim.created]
    assert sent == [(w.windows[0], "bid", "0.7300"),                       # YES at the 0.73 ask
                    (w.windows[2], "ask", "0.0500"),                       # NO at 0.95 = 'ask' at the YES price 0.05
                    (w.windows[3], "bid", "0.7300")]                       # no order in the window that had no signal
    assert len(w.t.writes) == 3 and not [c for c in w.t.calls if c.method == "DELETE"]

    opps = {o["hour_ticker"]: o for o in w.q("SELECT * FROM bot_opportunities")}
    assert set(opps) == {w.windows[0], w.windows[2], w.windows[3]}          # window 2 produced no opportunity
    assert [opps[t]["outcome"] for t in (w.windows[0], w.windows[2], w.windows[3])] == ["filled"] * 3
    assert opps[w.windows[0]]["kind"] == "late favorite YES" and opps[w.windows[2]]["kind"] == "late favorite NO"
    for t, pnl in ((w.windows[0], PNL_1), (w.windows[2], PNL_3), (w.windows[3], PNL_4)):
        assert opps[t]["realized_pnl"] == pytest.approx(float(pnl)) and opps[t]["paper_realized_pnl"] == pytest.approx(float(pnl))

    sett = {s["ticker"]: s for s in w.q("SELECT * FROM exec_settlements")}
    assert {t: (s["market_result"], s["revenue_cents"], s["check_status"]) for t, s in sett.items()} == {
        w.windows[0]: ("yes", 100, "ok"), w.windows[2]: ("no", 100, "ok"), w.windows[3]: ("no", 0, "ok")}
    led = w.ledger("reader")
    assert led.realized_pnl() == PNL_1 + PNL_3 + PNL_4
    assert led.realized_loss_total() == -(PNL_1 + PNL_3 + PNL_4) and led.open_worst_case() == 0
    assert led.count_orders_since(None, run_only=False) == 3 and led.unresolved_orders() == [] and led.open_orders() == []

    (run,) = w.q("SELECT * FROM bot_runs")
    assert run["end_reason"] == "finished" and w.q("SELECT status FROM bot_heartbeat")[0]["status"] == "stopped"
    assert w.q("SELECT COUNT(*) AS n FROM bot_orders WHERE dry_run = 0")[0]["n"] == 3


def test_the_dashboard_reads_what_the_bot_wrote(world):
    w = world(enabled=True)
    assert run_live(w) == 0, w.text
    now = w.clock.time()
    cfg = {"trading": w.trading, "dashboard": {"refresh_s": 2}}
    conn = server._open_db(w.db_path)
    try:
        st = server.api_status(conn, cfg, {}, now)
        opps = server.api_opportunities(conn, cfg, {}, now)["rows"]
        orders = server.api_orders(conn, cfg, {}, now)["rows"]
        events = server.api_events(conn, cfg, {}, now)["rows"]
    finally:
        conn.close()
    pnl = float(PNL_1 + PNL_3 + PNL_4)
    assert st["strategy"] == "late_favorite" and st["run"]["mode"] == "live" and st["other_live_runs"] == []
    s = st["stats"]["run"]
    assert (s["opportunities"], s["filled"], s["live_attempts"], s["live_orders"]) == (3, 3, 3, 3)
    assert s["paired_n"] == 3 and s["pending_n"] == 0
    assert s["paired_realized"] == pytest.approx(pnl) and s["paired_paper"] == pytest.approx(pnl)   # delay cost: none in the sim
    assert st["caps"]["max_loss_usd"] == 5.0 and st["caps_source"] == "run" and st["caps_drift"] == []
    assert st["caps"]["late_favorite"]["up_threshold"] == 0.6 and st["caps"]["late_favorite"]["max_attempts"] == 3
    assert [r["outcome"] for r in opps] == ["filled"] * 3 and all(r["fees_pending"] is False for r in opps)
    assert [o["status"] for o in orders] == ["executed"] * 3 and all(o["dry_run"] == 0 for o in orders)
    assert any(e["kind"] == "fill" for e in events) and not [e for e in events if e["level"] == "error"]
    hb = w.q("SELECT detail_json FROM bot_heartbeat")[0]["detail_json"]
    assert '"strategy":"late_favorite"' in hb and '"loss_used_usd"' in hb


def test_a_restart_in_the_same_window_never_trades_it_twice(world):
    w = world(enabled=True)
    assert w.run_bot(live=True, env="prod", hours=0.1) == 0, w.text           # window 1 only: one filled trade
    assert len(w.sim.created) == 1
    w.clock.now = CLOSE_TS - 125                                              # a new process, back inside window 1's start
    w.sim.markets[w.windows[0]]["status"] = "active"
    w.sim.results.pop(w.windows[0], None)
    assert w.run_bot(live=True, env="prod", hours=0.1) == 0, w.text
    assert len(w.sim.created) == 1                                            # the ledger remembers: not again
    assert w.q("SELECT COUNT(*) AS n FROM exec_orders")[0]["n"] == 1
    assert any("already has 1 order(s) in the ledger" in r["message"] for r in w.q("SELECT message FROM bot_events"))


def test_the_session_cap_ends_live_trading_for_the_run_but_not_the_watching(world):
    w = world(enabled=True, trading={"max_orders_per_session": 2})
    assert run_live(w) == 0, w.text
    assert len(w.sim.created) == 2                                            # the third entry is refused by Safety
    opps = w.q("SELECT hour_ticker, outcome FROM bot_opportunities ORDER BY detected_ts")
    assert [o["outcome"] for o in opps] == ["filled", "filled", "blocked_by_cap"]   # the paper twin is still written
    assert w.q("SELECT COUNT(*) AS n FROM exec_orders")[0]["n"] == 2
    assert not w.kill.exists()                                                # a session cap is a refusal, not a trip


@pytest.mark.parametrize("book,kind,side,price", [
    (("0.9970", "0.0020"), "late favorite YES", "bid", "0.9980"),   # YES ask 0.998: a YES buy is a 'bid' at its own price
    (("0.0020", "0.9970"), "late favorite NO", "ask", "0.0020"),    # NO ask 0.998: an 'ask' at the YES price 1 - 0.998 = 0.0020
])
def test_a_99_8_cent_favorite_is_sent_as_a_one_contract_ioc_and_its_paper_twin_still_records(
        tmp_path, monkeypatch, ed25519_key, fake_clock, book, kind, side, price):
    """User decision 2026-10-06: the price bounds are 0.001..0.999 (they were 0.01..0.99), so a favorite at 99-99.9c is
    traded. The real exchange often shows such books in the last two minutes (a watched window fired at a 0.998 ask): the
    bot now SENDS one contract, immediate-or-cancel, priced exactly at the ask on the market's 0.001 tick grid. A YES
    favorite is a 'bid' at the YES price 0.9980; a NO favorite is an 'ask' at the YES price 0.0020 (the NO price 0.998 seen
    from the YES book, converted only by units.to_book). Nothing is refused and nothing trips, and the paper twin records
    the same detection price next to the live fill."""
    bought = kind.rsplit(" ", 1)[1].lower()                                                    # 'yes' | 'no'
    w = World(tmp_path, monkeypatch, ed25519_key, fake_clock, enabled=True, book=book, result=bought)   # one window; the favorite wins
    assert run_live(w) == 0, w.text
    (body,) = w.sim.created                                                                   # exactly one order left the process
    assert body["client_order_id"] and {k: v for k, v in body.items() if k != "client_order_id"} == {
        "ticker": TICKER, "side": side, "price": price, "count": "1.00", "time_in_force": "immediate_or_cancel",
        "self_trade_prevention_type": "taker_at_cross", "exchange_index": 2}
    assert len(w.t.writes) == 1 and not [c for c in w.t.calls if c.method == "DELETE"]
    assert not w.kill.exists() and w.q("SELECT end_reason FROM bot_runs")[0]["end_reason"] == "finished"   # nothing tripped
    assert not w.q("SELECT message FROM bot_events WHERE kind = 'cap'")                       # no price-bound refusal any more
    assert not [e for e in w.q("SELECT message FROM bot_events WHERE level = 'error'") if "price" in e["message"]]
    (row,) = w.q("SELECT * FROM exec_orders")                                                 # the ledger holds the same order
    assert (row["status"], row["side"], row["price"], row["count"], row["time_in_force"], row["reduce_only"]) == (
        "executed", side, price, "1.00", "immediate_or_cancel", 0)
    assert (row["outcome"], row["outcome_price"]) == (bought, "0.9980")
    (order,) = w.q("SELECT * FROM bot_orders")                                                # and so does the dashboard
    assert (order["side"], order["action"], order["tif"], order["status"], order["dry_run"]) == (
        bought, "buy", "ioc", "executed", 0)
    assert order["count"] == 1.0 and order["limit_price"] == pytest.approx(0.998)
    (opp,) = w.q("SELECT * FROM bot_opportunities")                                           # the paper twin and the live fill
    assert opp["kind"] == kind and opp["outcome"] == "filled" and opp["live"] == 1
    assert opp["paper_cost"] == pytest.approx(0.998) and opp["live_cost"] == pytest.approx(0.998)
    assert opp["paper_realized_pnl"] == pytest.approx(float(PNL_998_WON)) and opp["realized_pnl"] == pytest.approx(float(PNL_998_WON))
    led = w.ledger("reader")
    assert led.realized_pnl() == PNL_998_WON and led.realized_loss_total() == 0 and led.open_worst_case() == 0


@pytest.mark.parametrize("book,kind", [
    (("0.9990", "0.0005"), "late favorite YES"),        # YES ask 0.9995: above the 0.999 bound
    (("0.0005", "0.9990"), "late favorite NO"),         # NO ask 0.9995 (= YES price 0.0005): the same bound seen from the other side
])
def test_a_favorite_above_the_0_999_bound_is_still_refused_but_gets_a_paper_twin(tmp_path, monkeypatch, ed25519_key,
                                                                                  fake_clock, book, kind):
    """The bound moved from 0.99 to 0.999, it did not go away. A book that asks 0.9995 (finer than any tick KXBTC15M has, so
    only a bad or hypothetical book shows it) is refused by the bot's own check: nothing is sent, nothing trips, and the paper
    twin records what the rule saw, so the dashboard's paper-only P&L shows what the bound leaves out."""
    w = World(tmp_path, monkeypatch, ed25519_key, fake_clock, enabled=True, book=book)         # one window only
    assert run_live(w) == 0, w.text
    assert w.sim.created == [] and w.t.writes == [] and not w.kill.exists()
    (opp,) = w.q("SELECT * FROM bot_opportunities ORDER BY detected_ts")
    assert opp["kind"] == kind and opp["outcome"] == "blocked_by_cap" and opp["live"] == 0
    assert opp["paper_cost"] == pytest.approx(0.9995) and opp["paper_realized_pnl"] is not None
    reasons = [r["message"] for r in w.q("SELECT message FROM bot_events WHERE kind = 'cap'")]
    assert any("live order not sent: price 0.9995 is outside 0.001..0.999" in m for m in reasons), reasons
    assert w.q("SELECT COUNT(*) AS n FROM exec_orders")[0]["n"] == 0


def test_a_99_8_cent_favorite_that_loses_costs_its_whole_price_and_counts_against_the_loss_cap(tmp_path, monkeypatch,
                                                                                                ed25519_key, fake_clock):
    """What the 0.999 bound risks: a favorite bought at 0.998 that loses costs 0.998 plus the fee, about a dollar, and the
    combined $5.00 loss rule books all of it (realized loss) while the run goes on without a trip."""
    w = World(tmp_path, monkeypatch, ed25519_key, fake_clock, enabled=True, book=("0.9970", "0.0020"), result="no")
    assert run_live(w) == 0, w.text
    assert [(b["side"], b["price"], b["count"]) for b in w.sim.created] == [("bid", "0.9980", "1.00")]
    (opp,) = w.q("SELECT * FROM bot_opportunities")
    assert opp["outcome"] == "filled" and opp["payout"] == 0.0
    assert opp["realized_pnl"] == pytest.approx(float(PNL_998_LOST)) and opp["paper_realized_pnl"] == pytest.approx(float(PNL_998_LOST))
    led = w.ledger("reader")
    assert led.realized_pnl() == PNL_998_LOST and led.realized_loss_total() == D("0.998") + FEE_998
    assert led.open_worst_case() == 0 and led.unresolved_orders() == [] and led.open_orders() == []
    assert led.realized_loss_total() < D(w.trading["max_loss_usd"]) and not w.kill.exists()


def test_a_loss_that_leaves_no_room_for_the_next_order_stops_the_run(world):
    from .exec_fakes import seed_realized_loss
    w = world(enabled=True)
    seed_realized_loss(w.ledger(), "4.20")                                    # $0.80 of the cap left: one order fits, not two
    w.sim.results[w.windows[0]] = "no"                                        # and window 1's trade LOSES, using that room
    rc = run_live(w)
    assert len(w.sim.created) == 1 and rc == cli.EXIT_FAIL                    # window 1 trades; window 3 does not fit: stop
    assert w.kill.exists() and "loss cap" in w.kill.read_text(encoding="utf-8")
    assert w.q("SELECT COUNT(*) AS n FROM exec_orders")[0]["n"] == 1
