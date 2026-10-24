import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import trade_ledger as tl  # noqa: E402

SECRET = "SECRET-KEY-SENTINEL-123"


def fill(i, ticker, side, action, count, yes, shard=0, ts="2026-10-21T20:00:00.000000Z", taker=True, fee="0.0100",
         order_id=None):
    return {"action": action, "count_fp": f"{count:.2f}", "created_time": ts, "exchange_index": shard,
            "fee_cost": fee, "fill_id": f"f{i}", "is_taker": taker, "order_id": order_id or f"o{i}",
            "side": side, "ticker": ticker, "market_ticker": ticker,
            "yes_price_dollars": f"{yes:.4f}", "no_price_dollars": f"{1 - yes:.4f}", "ts": 1792000000 + i}


class FakeClient:
    def __init__(self, fills_by_shard, settlements=(), positions=None, orders=None):
        self.f = fills_by_shard
        self.s = list(settlements)
        self.pos = positions or {}
        self.ord = orders or {}
        self.calls = []

    def shard_cash(self, s):
        self.calls.append("cash")
        return D("10.0")

    def fills(self, **kw):
        self.calls.append("fills")
        assert "min_ts" not in kw
        return list(self.f.get(kw["exchange_index"], []))

    def positions(self, **kw):
        return list(self.pos.get(kw["exchange_index"], []))

    def orders(self, **kw):
        return list(self.ord.get(kw["exchange_index"], []))

    def settlements(self, **kw):
        return list(self.s)

    def market(self, t):
        return {"market": {"status": "active", "result": ""}}

    def create_order(self, body):
        raise AssertionError("must never be reachable")


def settle(ticker, result, shard=0):
    return {"ticker": ticker, "market_result": result, "exchange_index": shard, "revenue": 100}


def build(fills, settlements=(), src=None, orders=None):
    raw = {"cash": {str(s): "0" for s in tl.SHARDS}, "positions": {}, "orders": orders or {},
           "settlements": list(settlements), "fills": {"0": fills}}
    return tl.build_ledger(raw, src or tl.TagSources())


def test_pnl_yes_no_win_lose():
    fills = [fill(1, "KXA-26OCT21AB-X", "yes", "buy", 2, 0.40),
             fill(2, "KXA-26OCT21AB-Y", "yes", "buy", 1, 0.30),
             fill(3, "KXA-26OCT21AB-Z", "no", "buy", 1, 0.30),
             fill(4, "KXA-26OCT21AB-W", "no", "buy", 1, 0.30)]
    res = [settle("KXA-26OCT21AB-X", "yes"), settle("KXA-26OCT21AB-Y", "no"),
           settle("KXA-26OCT21AB-Z", "no"), settle("KXA-26OCT21AB-W", "yes")]
    r = build(fills, res)["rows"]
    assert r[0]["pnl"] == D("0.60") * 2 - D("0.01")
    assert r[1]["pnl"] == D("-0.30") - D("0.01")
    assert r[2]["pnl"] == D("0.30") - D("0.01")
    assert r[3]["pnl"] == D("-0.70") - D("0.01")


def test_unsettled_is_open():
    r = build([fill(1, "KXA-26OCT21AB-X", "yes", "buy", 3, 0.40)])["rows"][0]
    assert r["pnl"] is None and r["open_count"] == 3


def test_sell_action_is_a_leg_of_its_side():
    # V2: "sell no" at yes 0.44 is a NO leg at 0.56; YES 0.24 + NO 0.56 locks +0.20 gross
    t = "KXA-26OCT21AB-X"
    fills = [fill(1, t, "yes", "buy", 1, 0.24), fill(2, t, "no", "sell", 1, 0.44)]
    r = build(fills, [settle(t, "no")])["rows"]
    assert r[0]["pnl"] == D("-0.24") - D("0.01")
    assert r[1]["pnl"] == D("0.44") - D("0.01")
    assert sum(x["pnl"] for x in r) + D("0.02") == D("0.20")


def test_pagination_all_shards_and_dedupe():
    f0 = [fill(i, "KXA-26OCT21AB-X", "yes", "buy", 1, 0.4, shard=0) for i in range(1, 4)]
    f3 = [fill(10, "KXB-26OCT21AB-X", "yes", "buy", 1, 0.4, shard=3)]
    c = FakeClient({0: f0 + f0[:1], 3: f3})
    raw = tl.pull_all(tl.ReadOnlyGuard(c))
    led = tl.build_ledger(raw, tl.TagSources())
    assert len(led["rows"]) == 4 and {r["shard"] for r in led["rows"]} == {0, 3}
    assert c.calls.count("fills") == 4


def test_tagging_rules():
    T = "KXNBATOTAL-26OCT21ATLORL-234"
    src = tl.TagSources(
        lock_order_ids={"olock"}, lock_fill_ids={"fidlock"},
        picks=[{"ts": "2026-10-21T19:00:00+00:00", "ticker": T, "tip_et": "2026-10-21 19:00",
                "buy": "NO", "proj": 232.0, "taker_price": 0.49, "limit": 0.48, "bet": True}],
        picks_orders_coids={"pcoid"}, exec_order_ids={"oexec"}, exec_coids=set())
    fills = [fill(1, "KXQ-26OCT21AB-X", "yes", "buy", 1, 0.5, order_id="olock"),
             fill(2, T, "no", "buy", 1, 0.51, order_id="oeob", ts="2026-10-21T22:00:00Z"),
             fill(3, T, "yes", "buy", 1, 0.60, order_id="opick", ts="2026-10-21T22:00:00Z"),
             fill(4, "KXQ-26OCT21AB-Y", "yes", "buy", 1, 0.5, order_id="opsend"),
             fill(5, "KXQ-26OCT21AB-Z", "yes", "buy", 1, 0.5, order_id="oexec"),
             fill(6, "KXQ-26OCT21AB-V", "yes", "buy", 1, 0.5, order_id="oother")]
    orders = {"0": [{"order_id": "oeob", "client_order_id": "eob-123"},
                    {"order_id": "opsend", "client_order_id": "pcoid"}]}
    rows = {r["fill_id"]: r for r in build(fills, src=src, orders=orders)["rows"]}
    assert rows["f1"]["strategy_tag"] == "lockbot_26OCT20"
    assert rows["f2"]["strategy_tag"] == "early_overs_bot"
    assert rows["f3"]["strategy_tag"] == "early_overs_40"
    assert rows["f3"]["pick_side_match"] is False and rows["f3"]["pick_price_match"] is False
    assert rows["f3"]["pick_proj"] == 232.0
    assert rows["f4"]["strategy_tag"] == "picks_send"
    assert rows["f5"]["strategy_tag"] == "exec_log"
    assert rows["f6"]["strategy_tag"] == "manual_other"
    ok = build([fill(7, T, "no", "buy", 1, 0.52, ts="2026-10-21T22:00:00Z")], src=src)["rows"][0]
    assert ok["strategy_tag"] == "early_overs_40" and ok["pick_side_match"] and ok["pick_price_match"]
    s2 = tl.TagSources(lock_attempts=[("KXQ-26OCT21AB-X", tl._parse_iso("2026-10-21T20:00:00Z").timestamp() - 3)])
    assert build([fill(8, "KXQ-26OCT21AB-X", "yes", "buy", 1, 0.5)], src=s2)["rows"][0]["strategy_tag"] == "lockbot_26OCT20"


def test_et_and_series():
    r = build([fill(1, "KXNBAGAME-26OCT21AB-X", "yes", "buy", 1, 0.5, ts="2026-10-22T02:30:00Z")])["rows"][0]
    assert r["fill_time_et"] == "2026-10-21 22:30:00" and r["et_date"] == "2026-10-21"
    assert r["series"] == "KXNBAGAME" and r["event"] == "KXNBAGAME-26OCT21AB"


def test_guard_blocks_writes():
    g = tl.ReadOnlyGuard(FakeClient({}))
    for name in ("create_order", "cancel_order", "amend_order", "batch_create", "_write", "_transport"):
        with pytest.raises(tl.WriteBlocked):
            getattr(g, name)
    with pytest.raises(tl.WriteBlocked):
        g.anything = 1

    class Inner:
        def send(self, method, url, **kw):
            return "sent"
    t = tl.GetOnlyTransport(Inner())
    assert t.send("GET", "u") == "sent"
    for m in ("POST", "DELETE", "PUT", "PATCH"):
        with pytest.raises(tl.WriteBlocked):
            t.send(m, "u")


def test_run_outputs_no_secrets_and_runs_log(tmp_path):
    t = "KXA-26OCT21AB-X"
    c = FakeClient({0: [fill(1, t, "yes", "buy", 1, 0.4), fill(2, "KXA-26OCT21AB-OPEN", "no", "buy", 1, 0.4)]},
                   settlements=[settle(t, "yes")],
                   positions={0: [{"ticker": t, "position_fp": "0.00", "realized_pnl_dollars": "0.6",
                                   "total_traded_dollars": "0.4"}]})
    raw, led, summ = tl.run(tl.ReadOnlyGuard(c), root=tmp_path, today_et="2026-10-21")
    tl.run(tl.ReadOnlyGuard(c), root=tmp_path, today_et="2026-10-21")      # idempotent second run
    text = summ + (tmp_path / "logs/trade_ledger.csv").read_text() + (tmp_path / "logs/trade_ledger_summary.txt").read_text()
    for p in (tmp_path / "logs/ledger_raw").glob("*.json"):
        text += p.read_text()
    assert SECRET not in text and "BEGIN" not in text
    assert "MISMATCH" not in summ and "checked 1 closed tickers" in summ
    assert "KXA-26OCT21AB-OPEN" in summ and "market status=active" in summ
    assert (tmp_path / "logs/trade_ledger.csv").read_text().count("\n") == 3
    lines = (tmp_path / "logs/trade_ledger_runs.log").read_text().strip().splitlines()
    assert len(lines) == 2 and "fills=2" in lines[0] and "realized_pnl=" in lines[0]


def test_main_error_exit_nonzero_no_secret(monkeypatch, capsys):
    def boom():
        raise RuntimeError("x " + SECRET)
    monkeypatch.setattr(tl, "make_live_client", boom)
    rc = tl.main()
    out = capsys.readouterr()
    assert rc != 0 and SECRET not in out.out + out.err
