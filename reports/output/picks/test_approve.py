import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import approve as A

NOW = datetime(2026, 10, 20, 18, 30, tzinfo=timezone.utc)


def tk(**kw):
    t = {"id": "x1", "created_utc": "2026-10-20T18:20:00Z", "ticker": "KXNBAGAME-26OCT20BOSDET-DET",
         "side": "yes", "count": 6, "max_price": 0.48, "reason": ""}
    t.update(kw)
    return t


def test_fee_value():
    assert A.fee(6, 0.48) == Decimal("0.1049")
    assert A.max_cost(6, 0.48) == Decimal("2.9849")


def test_ok():
    assert A.check_ticket(tk(), [], NOW) == []


def test_wrong_series():
    assert A.check_ticket(tk(ticker="KXBTC15M-ABC"), [], NOW)


def test_sell_side():
    assert A.check_ticket(tk(side="sell"), [], NOW)


def test_count_11():
    assert A.check_ticket(tk(count=11), [], NOW)
    assert A.check_ticket(tk(count=0), [], NOW)


def test_cost_over_3():
    assert any("cap" in b for b in A.check_ticket(tk(count=7), [], NOW))


def test_expired():
    assert any("expired" in b for b in A.check_ticket(tk(created_utc="2026-10-20T17:50:00Z"), [], NOW))


def test_total_cap():
    led = [{"event": "order_result", "ticket": "old", "ok": True, "cost_usd": "0.50"}]
    assert any("total cap" in b for b in A.check_ticket(tk(), led, NOW))
    assert A.check_ticket(tk(count=1), led, NOW) == []


def test_reused_id():
    for ev in ("order_result", "send_attempt"):
        assert any("already used" in b for b in A.check_ticket(tk(), [{"event": ev, "ticket": "x1"}], NOW))
