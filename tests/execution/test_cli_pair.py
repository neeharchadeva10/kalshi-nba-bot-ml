"""kpm bot run --strategy pair_h2 on the real stack: batch_create, the completion retry and the unwind, through the real
ExecClient, Safety, Ledger and reconcile. Same harness as test_cli_live.py (cli_world.py, sim_http.py).

The pair: the hourly KXBTCD 'above 85249.99' market and the KXBTC15M market (target 85432.83) both close at 19:00:00Z.
K < T, so the bot buys hourly YES + 15m NO. Books: hourly NO bid 0.54 -> YES ask 0.46; 15m YES bid 0.53 -> NO ask 0.47.
Cost 0.93 plus two taker fees (about 0.0348) leaves an edge of about 3.5 cents.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from kpm.execution import cli, units

from .cli_world import World
from .conftest import TEST_KEY_ID, verify_signature
from .exec_fakes import hourly_market
from .sim_http import zulu

H = 1_791_226_800.0                                   # 2026-10-05T19:00:00Z, an hour close
M15 = "KXBTC15M-26OCT051500-00"
HOURLY = "KXBTCD-26OCT0515-T85249.99"
FEES = units.taker_fee(D(1), D("0.46")) + units.taker_fee(D(1), D("0.47"))
EDGE = 1 - D("0.93") - FEES


@pytest.fixture
def pair_world(tmp_path, monkeypatch, ed25519_key, fake_clock):
    def make(**kw) -> World:
        w = World(tmp_path, monkeypatch, ed25519_key, fake_clock, close_ts=H, ticker=M15, book=("0.53", "0.40"),
                  start_offset=-880.0, **kw)
        w.sim.add_market(hourly_market(ticker=HOURLY, close_time=zulu(H), open_time=zulu(H - 3600), status="active",
                                       result=""), "KXBTCD", book=("0.40", "0.54"), result="yes")
        w.sim.results[M15] = "yes"                                       # the 15m market settles UP: its NO leg loses
        return w

    return make


def run(w: World, **kw) -> int:
    return w.run_bot(strategy="pair_h2", live=True, env="prod", hours=1, **kw)


def test_watch_only_pair_run_writes_the_paper_twin_and_sends_nothing(pair_world):
    w = pair_world(keys=False)
    assert w.run_bot(strategy="pair_h2", hours=1) == 0, w.text
    assert w.t.writes == [] and not any(c.signed for c in w.t.calls)
    (opp,) = w.q("SELECT * FROM bot_opportunities")
    assert (opp["kind"], opp["outcome"], opp["live"], opp["hour_ticker"], opp["strike_ticker"]) == (
        "hourly YES + 15m DOWN", "paper_only", 0, M15, HOURLY)
    assert opp["paper_edge"] == pytest.approx(float(EDGE)) and opp["paper_cost"] == pytest.approx(0.93)
    assert opp["paper_realized_pnl"] == pytest.approx(float(EDGE))                 # payout $1: hourly YES won, 15m NO lost
    assert w.q("SELECT COUNT(*) AS n FROM bot_orders WHERE dry_run = 1")[0]["n"] == 2
    assert w.q("SELECT COUNT(*) AS n FROM bot_quotes")[0]["n"] > 0                 # the live chart's data


def test_a_live_pair_goes_out_as_one_signed_batch_and_settles_riskless(pair_world, ed25519_key):
    w = pair_world(enabled=True)
    assert run(w) == 0, w.text

    (post,) = w.t.writes
    assert (post.method, post.endpoint) == ("POST", "/portfolio/events/orders/batched")
    verify_signature(post, ed25519_key)
    assert post.headers["KALSHI-ACCESS-KEY"] == TEST_KEY_ID
    (batch,) = w.sim.batches
    hourly_leg, m15_leg = batch                                                     # K < T: hourly YES first, then 15m NO
    assert {k: hourly_leg[k] for k in ("ticker", "side", "price", "count", "time_in_force", "exchange_index")} == {
        "ticker": HOURLY, "side": "bid", "price": "0.4600", "count": "1.00", "time_in_force": "immediate_or_cancel",
        "exchange_index": 2}
    assert {k: m15_leg[k] for k in ("ticker", "side", "price", "count", "time_in_force", "exchange_index")} == {
        "ticker": M15, "side": "ask", "price": "0.5300", "count": "1.00", "time_in_force": "immediate_or_cancel",
        "exchange_index": 2}                                                        # buying NO at 0.47 = 'ask' at YES 0.53
    assert not [c for c in w.t.calls if c.method == "DELETE"]

    (opp,) = w.q("SELECT * FROM bot_opportunities")
    assert (opp["outcome"], opp["live"]) == ("both_filled", 1)
    assert opp["live_cost"] == pytest.approx(0.93) and opp["live_fees"] == pytest.approx(float(FEES))
    assert opp["realized_pnl"] == pytest.approx(float(EDGE)) and opp["paper_realized_pnl"] == pytest.approx(float(EDGE))
    rows = w.q("SELECT ticker, status, fill_count FROM exec_orders ORDER BY ticker")
    assert rows == [{"ticker": M15, "status": "executed", "fill_count": "1.00"},
                    {"ticker": HOURLY, "status": "executed", "fill_count": "1.00"}]
    sett = w.q("SELECT ticker, market_result, revenue_cents, check_status FROM exec_settlements ORDER BY ticker")
    assert sett == [{"ticker": M15, "market_result": "yes", "revenue_cents": 0, "check_status": "ok"},      # NO leg lost
                    {"ticker": HOURLY, "market_result": "yes", "revenue_cents": 100, "check_status": "ok"}]  # YES leg won
    led = w.ledger("reader")
    assert led.realized_pnl() == EDGE and led.realized_loss_total() == 0 and led.open_worst_case() == 0
    assert led.unresolved_orders() == []
    blob = w.everything()
    for secret in (TEST_KEY_ID, str(w.pem), "PRIVATE KEY", "KALSHI-ACCESS"):
        assert secret not in blob


def test_a_missing_leg_gets_one_completion_retry_while_the_pair_still_pays(pair_world):
    w = pair_world(enabled=True)
    w.sim.script = ["fill", "nofill", "fill"]                                      # batch: hourly fills, 15m NO does not; retry fills
    assert run(w) == 0, w.text
    assert len(w.sim.batches) == 1 and len(w.sim.created) == 3                      # the batch (2 orders) plus ONE retry
    retry = w.sim.created[-1]
    assert (retry["ticker"], retry["side"], retry["price"]) == (M15, "ask", "0.5300")
    (opp,) = w.q("SELECT outcome FROM bot_opportunities")
    assert opp["outcome"] == "both_filled"
    assert not w.kill.exists() and not [c for c in w.t.calls if c.method == "DELETE"]


def test_when_the_retry_fails_the_filled_leg_is_unwound_with_a_reduce_only_ioc(pair_world):
    w = pair_world(enabled=True)
    w.sim.script = ["fill", "nofill", "nofill", "fill"]                            # batch, retry (no fill), unwind (fills)
    assert run(w) == 0, w.text
    unwind = w.sim.created[-1]
    assert unwind["reduce_only"] is True and unwind["time_in_force"] == "immediate_or_cancel"
    assert unwind["ticker"] == HOURLY and unwind["side"] == "ask"                  # sells the held hourly YES: buys NO
    (opp,) = w.q("SELECT outcome FROM bot_opportunities")
    assert opp["outcome"] == "one_leg_unwound"
    assert len(w.sim.created) == 4 and not w.kill.exists()


def test_a_lost_batch_reply_is_looked_up_and_never_resent(pair_world):
    w = pair_world(enabled=True)
    w.sim.script = ["timeout_placed"]                                              # both orders reached the exchange
    assert run(w) == 0, w.text
    assert len(w.sim.batches) == 1 and len(w.sim.created) == 2                      # found by client_order_id: no resend
    assert w.q("SELECT outcome FROM bot_opportunities")[0]["outcome"] == "both_filled"


def test_the_pair_run_length_is_whole_hourly_windows(pair_world):
    w = pair_world(keys=False)
    assert w.run_bot(strategy="pair_h2", hours=0.2) == 0                            # 0.2 -> 1 hourly window
    assert "for 1 hourly window(s)" in w.text
    assert cli.EXIT_OK == 0
