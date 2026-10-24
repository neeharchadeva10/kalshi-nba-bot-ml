"""What the live bot does when Kalshi's REST portfolio data lags a fill (the one timing fact the design leaves open, Q19).

The create-order reply says "filled", but GET /portfolio/orders, /fills and /positions still show nothing for a while.
reconcile waits for ``user_data_timestamp`` to catch up (at most 10 s), re-reads 5 s later, and only a discrepancy that
SURVIVES both reads, with ``as_of_time`` past the event, is a real mismatch (design 5.5 item 7). A lag it can ride out is
invisible. A lag that outlasts both reads is NOT a mismatch: nothing is declared and nothing halts (the data is lagging,
not the ledger wrong); it is reported as unconfirmed, and the settlement check, by which time the data has caught up,
verifies the ledger against the exchange's own revenue.
All waits use the CLI's own clock and sleep, so nothing here really sleeps.
"""
from __future__ import annotations

import json

import pytest

from .cli_world import World


@pytest.fixture
def world(tmp_path, monkeypatch, ed25519_key, fake_clock):
    def make(**kw) -> World:
        return World(tmp_path, monkeypatch, ed25519_key, fake_clock, **kw)

    return make


@pytest.mark.parametrize("lag", [0.0, 3.0, 8.0])
def test_a_lag_that_reconcile_can_wait_out_is_invisible(world, lag):
    w = world(enabled=True)
    w.sim.lag_s = lag
    assert w.run_bot(live=True, env="prod", hours=0.1) == 0, w.text
    assert not w.kill.exists()
    (o,) = w.q("SELECT status, fill_count FROM exec_orders")
    assert (o["status"], o["fill_count"]) == ("executed", "1.00")
    assert w.q("SELECT COUNT(*) AS n FROM exec_fills")[0]["n"] == 1             # the fill was fetched once the data caught up
    assert w.q("SELECT outcome FROM bot_opportunities")[0]["outcome"] == "filled"
    assert w.q("SELECT check_status FROM exec_settlements")[0]["check_status"] == "ok"


def test_a_lag_longer_than_both_reads_is_reported_but_is_not_a_mismatch_and_does_not_halt(world):
    """Review finding: a 40 s lag outlasted the 10 s wait and the 5 s re-read, so the post-fill reconcile declared 'fills
    stored 0 != fill_count 1.00' a real mismatch, tripped Safety and wrote data/KILL, although nothing was inconsistent."""
    w = world(enabled=True)
    w.sim.lag_s = 40.0
    w.sim.settle_lag = 0.0
    rc = w.run_bot(live=True, env="prod", hours=0.1)
    assert rc == 0, w.text
    assert not w.kill.exists() and len(w.sim.created) == 1
    assert not [c for c in w.t.calls if c.method == "DELETE"]
    assert w.q("SELECT end_reason FROM bot_runs")[0]["end_reason"] == "finished"
    # the data caught up long before the run ended: the fill and the settlement are in the ledger and agree
    (o,) = w.q("SELECT status, fill_count FROM exec_orders")
    assert (o["status"], o["fill_count"]) == ("executed", "1.00")
    assert w.q("SELECT COUNT(*) AS n FROM exec_fills")[0]["n"] == 1
    assert w.q("SELECT check_status FROM exec_settlements")[0]["check_status"] == "ok"
    assert w.q("SELECT outcome FROM bot_opportunities")[0]["outcome"] == "filled"
    # ... and the lag was reported, not hidden: in the reconcile audit record and as a dashboard event
    seen = [json.loads(r["details"]) for r in w.q("SELECT details FROM audit_log WHERE action = 'reconcile'")]
    assert any(d.get("unconfirmed") for d in seen)
    assert not any(d.get("mismatches") for d in seen)
    assert any("could not be confirmed" in r["message"] for r in w.q("SELECT message FROM bot_events"))
