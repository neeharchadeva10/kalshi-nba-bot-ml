import asyncio
import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import lockbot as lb  # noqa: E402

D = Decimal
ROOT = HERE.parents[2]


def _approve():
    spec = importlib.util.spec_from_file_location("approve_mod", ROOT / "reports" / "output" / "picks" / "approve.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_fee_matches_approve_and_formula():
    ap = _approve()
    for c in (1, 3, 10):
        for p in ("0.01", "0.37", "0.5", "0.9137", "0.99"):
            assert lb.fee(c, p) == ap.fee(c, p)
    assert lb.fee(1, "0.5") == D("0.0175")           # 0.07*.25 = 0.0175
    assert lb.fee(1, "0.43") == D("0.0172")          # 0.017143 -> ceil to 4dp


def test_body_yes_and_no_match_approve_mapping():
    ap = _approve()
    # approve.py mapping (inline in main): side bid/ask, price p vs 1-p, count "%.2f", IOC, taker_at_cross
    for side, p in (("yes", D("0.4300")), ("no", D("0.5600"))):
        b = lb.build_body("KXNBAGAME-26OCT20PHINYK-PHI", side, p, 1, 2, coid="abc-123")
        want_price = p if side == "yes" else 1 - p
        assert b == {"ticker": "KXNBAGAME-26OCT20PHINYK-PHI", "client_order_id": "abc-123",
                     "side": "bid" if side == "yes" else "ask", "count": "1.00", "price": f"{want_price:.4f}",
                     "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross",
                     "exchange_index": 2}
    assert lb.build_body("T-A-B", "no", D("0.56"), 1, 0)["price"] == "0.4400"
    assert lb.build_body("T-A-B", "yes", D("0.43"), 1, 0)["side"] == "bid"
    assert ap.fee  # approve importable


def lock(kind, legs, prices):
    return dict(kind=kind, legs=legs, prices=prices)


def test_make_plan_profit_and_skips():
    lk = lock("ladder", ["YES KXA-G-1", "NO KXA-G-2"], [0.40, 0.50])
    p = lb.make_plan(lk, 1, D("0.01"), 3)
    assert p["reserve"] == D("0.40") + lb.fee(1, "0.40") + D("0.50") + lb.fee(1, "0.50")
    assert p["exp_profit"] == 1 - p["reserve"] and "skip" not in p
    assert "min_profit" in lb.make_plan(lock("ladder", ["YES KXA-G-1", "NO KXA-G-2"], [0.49, 0.50]), 1, D("0.01"), 3)["skip"]
    big = lock("me_no", [f"NO KXA-G-{i}" for i in range(5)], [0.5] * 5)
    assert "legs" in lb.make_plan(big, 1, D("0.01"), 3)["skip"]
    me = lb.make_plan(lock("me_no", ["NO A-G-1", "NO B-G-2", "NO C-G-3"], [0.30, 0.30, 0.30]), 1, D("0.01"), 3)
    assert me["exp_profit"] == 2 - me["reserve"]                       # N-1 payout


def res(plan, fills, avg=None, unknown=()):
    out = []
    for i, (l, f) in enumerate(zip(plan["legs"], fills)):
        out.append(dict(ticker=l["ticker"], side=l["side"], ok=True, fill=D(f), unknown=i in unknown,
                        avg_price=(l["p_side"] if avg is None else avg[i]) if D(f) > 0 else None,
                        fee_each=l["fee"], order_id="x"))
    return out


def test_at_risk_complete_ladder():
    p = lb.make_plan(lock("ladder", ["YES A-G-1", "NO A-G-2"], [0.40, 0.50]), 1, D("0.01"), 3)
    sm = lb.settle_math(p, res(p, [1, 1]))
    assert sm["status"] == "complete" and sm["guaranteed"] == 1 and sm["at_risk"] == 0


def test_at_risk_broken_ladder_one_leg():
    p = lb.make_plan(lock("ladder", ["YES A-G-1", "NO A-G-2"], [0.40, 0.50]), 1, D("0.01"), 3)
    sm = lb.settle_math(p, res(p, [1, 0]))
    assert sm["status"] == "partial" and sm["guaranteed"] == 0
    assert sm["at_risk"] == D("0.40") + lb.fee(1, "0.40")
    assert lb.settle_math(p, res(p, [0, 0]))["status"] == "none"
    assert lb.settle_math(p, res(p, [0, 0]))["at_risk"] == 0


def test_at_risk_me_no_three_legs_partial():
    p = lb.make_plan(lock("me_no", ["NO A-G-1", "NO B-G-2", "NO C-G-3"], [0.30, 0.30, 0.30]), 1, D("0.01"), 3)
    f = lb.fee(1, "0.30")
    full = lb.settle_math(p, res(p, [1, 1, 1]))
    assert full["guaranteed"] == 2 and full["at_risk"] == 0
    two = lb.settle_math(p, res(p, [1, 1, 0]))                          # 2 NOs of 3: worst case one is the YES -> pays 1
    assert two["guaranteed"] == 1 and two["at_risk"] == 2 * (D("0.30") + f) - 1 or two["at_risk"] == 0
    assert two["at_risk"] == max(D(0), 2 * (D("0.30") + f) - 1)
    one = lb.settle_math(p, res(p, [1, 0, 0]))
    assert one["guaranteed"] == 0 and one["at_risk"] == D("0.30") + f
    # me_yes: all-or-nothing payout 1
    py = lb.make_plan(lock("me_yes", ["YES A-G-1", "YES B-G-2"], [0.40, 0.50]), 1, D("0.01"), 3)
    assert lb.settle_math(py, res(py, [1, 0]))["guaranteed"] == 0
    assert lb.settle_math(py, res(py, [1, 1]))["guaranteed"] == 1


def test_unknown_leg_is_conservative():
    p = lb.make_plan(lock("ladder", ["YES A-G-1", "NO A-G-2"], [0.40, 0.50]), 1, D("0.01"), 3)
    sm = lb.settle_math(p, res(p, [1, 0], unknown=(1,)))
    assert sm["status"] == "unknown" and sm["at_risk"] == (D("0.40") + lb.fee(1, "0.40")) + (D("0.50") + lb.fee(1, "0.50"))


def test_parse_leg_like_approve():
    leg = dict(ticker="T", side="no")
    r = lb.parse_leg(leg, 201, {"order_id": "o1", "fill_count": "1.00", "average_fill_price": "0.4400",
                                "average_fee_paid": "0.0172"})
    assert r["ok"] and r["fill"] == 1 and r["avg_price"] == D("0.56") and r["fee_each"] == D("0.0172")
    assert lb.parse_leg(leg, 201, {"order_id": "o1", "fill_count": "0.00"})["fill"] == 0
    assert lb.parse_leg(leg, 400, {})["unknown"] is False
    assert lb.parse_leg(leg, 503, None)["unknown"] is True
    assert lb.parse_leg(leg, None, None, TimeoutError())["unknown"] is True
    assert lb.parse_leg(leg, 201, {})["unknown"] is True


def test_ledger_caps_shared_across_processes(tmp_path):
    a = lb.Ledger(tmp_path / "l.json")
    b = lb.Ledger(tmp_path / "l.json")                                   # second "process", same file
    gc, nc = D("5"), D("6")
    assert a.reserve("r1", "G1", "2.5", gc, nc)[0]
    assert b.reserve("r2", "G2", "2.5", gc, nc)[0]
    ok, why, g, n = b.reserve("r3", "G2", "1.5", gc, nc)                 # night 5 + 1.5 > 6
    assert not ok and why == "night_cap" and n == D("5.0")
    ok, why, *_ = a.reserve("r4", "G1", "2.6", gc, nc)                   # game 2.5 + 2.6 > 5
    assert not ok and why == "game_cap"
    a.settle("r1", "G1", "0")                                            # complete lock: at-risk 0 frees the reservation
    assert b.reserve("r5", "G2", "1.5", gc, nc)[0]
    a.settle("r2", "G2", "2.0")                                          # broken lock keeps its at-risk
    assert a.snapshot("G2") == (D("3.5"), D("3.5"))
    assert not a.reserve("r6", "G1", "2.6", gc, nc)[0]                   # night 3.5 + 2.6 > 6


def test_ledger_atomic_no_tmp_left(tmp_path):
    a = lb.Ledger(tmp_path / "l.json")
    a.reserve("r", "G", "1", 5, 15)
    assert not list(tmp_path.glob("*.tmp")) and not (tmp_path / "l.json.lock").exists()


class Args:
    live = False
    count = 1
    game_cap = "5.00"
    night_cap = "15.00"
    min_profit = D("0.01")
    max_legs = 3
    refire_s = 2.0
    max_attempts_per_game = 15
    max_order_errors = 3


def make_bot(tmp_path, args=None):
    doc = {"date": "26OCT20", "prefix": "KXNBA", "events": {"KXNBAGAME-26OCT20PHINYK": {"mutually_exclusive": True}},
           "books": {"KXNBAGAME-26OCT20PHINYK-PHI": {}, "KXNBAGAME-26OCT20PHINYK-NYK": {}}}
    bot = lb.Bot(doc, None, 1e18, tmp_path, "lockbot_t", "26OCT20", "t", args or Args(), lb.Ledger(tmp_path / "l.json"))
    bot.log_fh = open(tmp_path / "log.jsonl", "a", encoding="utf-8")
    bot.ei = {"KXNBAGAME-26OCT20PHINYK-PHI": 0, "KXNBAGAME-26OCT20PHINYK-NYK": 0}
    return bot


def st(key="k1", prices=(0.40, 0.50)):
    return dict(key=key, kind="me_yes", legs=["YES KXNBAGAME-26OCT20PHINYK-PHI", "YES KXNBAGAME-26OCT20PHINYK-NYK"],
                prices=list(prices), sizes=[5, 5], n=1, open_meta={"recv_ns": 1, "ts_ms": 1})


def run_considers(bot, sts):
    async def go():
        for s in sts:
            bot.consider(s, 0)
        await asyncio.gather(*list(bot.tasks))
    asyncio.run(go())


def test_refire_guard_and_dry_never_builds_client(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("order client constructed in dry mode")
    monkeypatch.setattr(lb, "OrderSender", boom)
    import kpm.execution.client as kc
    monkeypatch.setattr(kc, "ExecClient", boom)
    bot = make_bot(tmp_path)
    s = st()
    run_considers(bot, [s, s])                                           # same key twice, back to back
    assert bot.attempts == 1
    # within 2 s of the first fire: blocked again even after the first finished
    run_considers(bot, [st()])
    assert bot.attempts == 1
    # different key on the same tickers: also blocked inside the window
    run_considers(bot, [st("k2")])
    assert bot.attempts == 1
    log = (tmp_path / "log.jsonl").read_text()
    assert '"would_send"' in log and '"refire_guard"' in log and bot.sender is None


def test_game_cap_halts(tmp_path):
    a = Args()
    a.game_cap = "1.00"                                                  # reserve ~0.9+fees > 1.00? 0.4+0.5+fees=0.9345 ok, 2nd fails
    a.refire_s = 0.0
    bot = make_bot(tmp_path, a)
    run_considers(bot, [st("k1")])
    assert bot.attempts == 1 and bot.halted is None                      # dry settles at 0 at-risk
    # force at-risk to stay: pre-seed the ledger as if a broken lock happened
    bot.ledger.settle("seed", bot.game, "0.5")
    run_considers(bot, [st("k2")])
    assert bot.halted and "game_cap" in bot.halted and bot.attempts == 1


def test_night_cap_halts_other_process(tmp_path):
    a = Args()
    a.night_cap = "1.00"
    a.refire_s = 0.0
    bot = make_bot(tmp_path, a)
    bot.ledger.settle("other_process", "26OCT20OTHER", "0.5")            # another game's process already has 0.5 at risk
    run_considers(bot, [st("k1")])
    assert bot.halted and "night_cap" in bot.halted and bot.attempts == 0


def test_three_order_errors_stop(tmp_path):
    a = Args()
    a.refire_s = 0.0
    bot = make_bot(tmp_path, a)
    bot.live = True

    import concurrent.futures

    class Fake:                                                          # test double, never touches the network
        def __init__(self):
            self.pool = concurrent.futures.ThreadPoolExecutor(2)
            self.posted = 0

        def post(self, leg, body):
            self.posted += 1
            r = lb.parse_leg(leg, 400, {})
            r.update(send_ns=1, response_ns=2)
            return r
    bot.sender = Fake()

    async def go():
        bot.consider(st("k1"), 0)
        await asyncio.sleep(0.2)                                         # sent from consider() itself, before the task runs
        assert bot.sender.posted == 2
        await asyncio.gather(*list(bot.tasks))
        assert bot.order_errors == 2 and bot.halted is None
        bot.consider(st("k2"), 0)
        await asyncio.gather(*list(bot.tasks))
    asyncio.run(go())
    assert bot.order_errors == 4 and bot.halted and "order_errors" in bot.halted
    assert bot.attempts == 2
    n = bot.attempts
    run_considers(bot, [st("k3")])
    assert bot.attempts == n                                             # halted: nothing more is sent


def test_max_attempts(tmp_path):
    a = Args()
    a.max_attempts_per_game = 1
    a.refire_s = 0.0
    bot = make_bot(tmp_path, a)
    run_considers(bot, [st("k1")])
    run_considers(bot, [st("k2")])
    assert bot.attempts == 1 and bot.halted == "max_attempts_per_game"


def test_grow_detector_emits_current_lock_when_profit_rises():
    """A lock that opens small and then grows emits lock_grow with the CURRENT prices (2026-10-21)."""
    import lockbot as lb
    events = []
    det = lb.GrowDetector.__new__(lb.GrowDetector)
    det.open, det.n_opened = {}, 0
    det.emit = lambda ev, st, now: events.append((ev, st))
    seq = iter([0.004, 0.012, 0.010])
    def fake_keys(t):
        p = next(seq)
        lk = dict(kind="me_no", legs=["NO A", "NO B"], prices=[round(0.5 - p, 4), 0.5], sizes=[1, 1], n=1, profit=p)
        return {("ME", "E", "me_no"): (lk, ("A", "B"))}, {("ME", "E", "me_no")}
    det._ladder_keys = lambda t: ({}, set())
    det._me_keys = fake_keys
    det.update("A", 1.0); det.update("A", 2.0); det.update("A", 3.0)
    kinds = [e for e, _ in events]
    assert kinds == ["lock_open", "lock_grow"]                   # grows once (0.004 -> 0.012), not on the dip
    assert events[1][1]["profit"] == 0.012 and events[1][1]["prices"][0] == round(0.5 - 0.012, 4)
