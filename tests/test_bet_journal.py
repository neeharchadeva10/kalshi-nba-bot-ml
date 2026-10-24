import json
import sys
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import bet_journal as bj  # noqa: E402
import trade_ledger as tl  # noqa: E402


def test_clv_sign_yes_and_no():
    # YES bought at 0.50, market YES mid at close 0.55 -> +5c; NO bought at 0.50 with YES mid 0.55 -> NO mid 0.45 -> -5c
    _, _, mid_yes = bj.close_mid_for("yes", 0.54, 0.56)
    _, _, mid_no = bj.close_mid_for("no", 0.54, 0.56)
    assert bj.clv_cents(0.50, mid_yes) == pytest.approx(5.0)
    assert mid_no == pytest.approx(0.45)
    assert bj.clv_cents(0.50, mid_no) == pytest.approx(-5.0)
    assert bj.clv_cents(0.5, None) is None
    assert bj.close_mid_for("yes", None, 0.5)[2] is None


def test_slippage_hold_roi():
    assert bj.slippage_cents(0.51, 0.50) == pytest.approx(1.0)       # paid 1c worse
    assert bj.slippage_cents(0.49, 0.50) == pytest.approx(-1.0)
    assert bj.slippage_cents(0.5, None) is None
    t0 = datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 22, 2, 30, tzinfo=timezone.utc)
    assert bj.hold_hours(t0, t1) == pytest.approx(6.5)
    assert bj.roi_pct(0.5, 1.0) == 50.0 and bj.roi_pct(None, 1.0) is None and bj.roi_pct(1, 0) is None


def test_drawdown_and_streak():
    dd, st = bj.max_drawdown_and_streak([1, -2, -1, 3, -1, -1, -1, -1, 5])
    assert dd == pytest.approx(4.0)          # peak 1 -> then -2,-1 => cum -2 (dd 3); later 4->0 (peak 4? cum after 3 = 1)
    assert st == 4
    assert bj.max_drawdown_and_streak([]) == (0.0, 0)


def test_main_line_and_buckets():
    assert bj.main_line({230.5: (0.7, 0.72), 232.5: (0.48, 0.52), 234.5: (0.2, 0.25)}) == 232.5
    assert bj.main_line({1: (None, 0.5)}) is None
    assert [bj.gap_bucket(x) for x in (3.9, 4, 6, -8, "")] == ["<4", "4-6", "6-8", "8+", "n/a"]
    assert [bj.price_bucket(x) for x in (0.1, 0.4, 0.6, 0.9)] == ["0-30c", "30-50c", "50-70c", "70c+"]
    assert [bj.min_bucket(x) for x in (-5, 10, 30, 60, 120, "")] == ["after tip", "0-15", "15-45", "45-90", "90+", "n/a"]


def test_attribution_tags():
    a = bj.attribution(side_lbl="OVER", result_win=True, margin=7.5, ot=False, clv=2.0, line_dir="toward_us",
                       line_move=2.0, proj=230, line=225.5, total=233.0, slip_c=1.0, filled=True)
    for s in ("won by 7.5 pts", "positive CLV", "line moved 2 pts our way", "model error 3.0 pts vs market error 7.5 pts",
              "filled 1c worse than the ping"):
        assert s in a
    b = bj.attribution(side_lbl="UNDER", result_win=False, margin=-0.5, ot=True, clv=-1.0, line_dir="against_us",
                       line_move=-1.0, proj=None, line=None, total=None, slip_c=None, filled=True)
    assert "lost by 0.5 (near-miss)" in b and "overtime decided it (against us)" in b and "line moved 1 pts against us" in b
    assert bj.attribution(side_lbl="", result_win=None, margin=None, ot=None, clv=None, line_dir="n/a", line_move=None,
                          proj=None, line=None, total=None, slip_c=None, filled=False) == "not filled"
    assert "pending" in bj.attribution(side_lbl="", result_win=None, margin=None, ot=None, clv=None, line_dir="n/a",
                                       line_move=None, proj=None, line=None, total=None, slip_c=None, filled=False,
                                       pending=True)


def test_versions():
    assert bj.version_of({"rule": "grid_winner", "grid_t": 4.0}, "early_overs_40") == "#40 v5"
    assert bj.version_of({"rule": "grid_winner", "grid_t": 8.0}, "early_overs_40") == "#40 v4"
    assert bj.version_of(None, "lockbot_26OCT20") == "lockbot 26OCT20"
    assert bj.version_of(None, "manual_other") == ""


# ---------------------------------------------------------------- integration with fakes
class FakeClient:
    def market(self, t):
        if t.startswith("KXNBAOT"):
            return {"market": {"result": "yes", "occurrence_datetime": "2026-10-22T02:00:00Z"}}
        return {"market": {"result": "no", "expiration_value": "206", "occurrence_datetime": "2026-10-22T02:00:00Z"}}


def _fill(i, ticker, side, yes, ts, order_id):
    return {"action": "buy", "count_fp": "2.00", "created_time": ts, "exchange_index": 0, "fee_cost": "0.0200",
            "fill_id": f"f{i}", "is_taker": False, "order_id": order_id, "side": side, "ticker": ticker,
            "yes_price_dollars": f"{yes:.4f}", "no_price_dollars": f"{1 - yes:.4f}", "ts": 1}


def test_build_journal_end_to_end(tmp_path):
    (tmp_path / "logs").mkdir()
    T = "KXNBATOTAL-26OCT21ATLORL-234"
    T2 = "KXNBATOTAL-26OCT21MILWAS-239"
    pick = {"ts": "2026-10-21T21:30:00+00:00", "game": "26OCT21ATLORL", "tip_et": "2026-10-21 19:00", "ticker": T,
            "strike": 233.5, "buy": "NO", "side": "UNDER", "proj": 228.0, "proj_minus_strike": -5.5, "taker_price": 0.49,
            "limit": 0.48, "status": "PLACE NOW (limit buy NO @ 0.48)", "bet": True, "rule": "grid_winner",
            "grid_t": 4.0, "p_formula": 0.6, "ev_c": 3.0}
    pick2 = dict(pick, ticker=T2, game="26OCT21MILWAS", strike=238.5)
    with open(tmp_path / "logs/early_overs_picks.jsonl", "w") as f:
        f.write(json.dumps(pick) + "\n" + json.dumps(pick2) + "\n")
    # UNDER (NO) bought at 0.51 (yes 0.49) at 22:00Z = 60 min before tip 23:00Z; market result no -> win
    fills = [_fill(1, T, "no", 0.49, "2026-10-21T22:00:00Z", "o1")]
    raw = {"cash": {"0": "10", "1": "0", "2": "0", "3": "0"}, "positions": {}, "orders": {},
           "fills": {"0": fills},
           "settlements": [{"ticker": T, "market_result": "no", "exchange_index": 0, "revenue": 200,
                            "settled_time": "2026-10-22T02:05:00Z"}]}
    led = tl.build_ledger(raw, tl.load_sources(tmp_path))
    assert led["rows"][0]["strategy_tag"] == "early_overs_40"
    ts_tip = int(datetime(2026, 10, 21, 23, 0, tzinfo=timezone.utc).timestamp())
    ts_ent = int(datetime(2026, 10, 21, 22, 0, tzinfo=timezone.utc).timestamp())
    cache = {"games": {}, "tops": {
        f"{ts_tip}|KXNBATOTAL-26OCT21ATLORL": {T: [0.44, 0.46], "KXNBATOTAL-26OCT21ATLORL-232": [0.49, 0.51]},
        f"{ts_ent}|KXNBATOTAL-26OCT21ATLORL": {T: [0.48, 0.50], "KXNBATOTAL-26OCT21ATLORL-236": [0.49, 0.51]}}}
    (tmp_path / "logs/bet_journal_cache.json").write_text(json.dumps(cache))
    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    rows = bj.build_journal(raw, led, FakeClient(), root=tmp_path, now=now, kc=None, replay=False)
    by = {r["ticker"]: r for r in rows}
    r = by[T]
    assert r["filled"] is True and r["side"] == "UNDER" and r["min_before_tip"] == 60.0
    assert r["avg_entry_price"] == 0.51 and r["slippage_c"] == pytest.approx(2.0)    # paid 51c vs 49c ping
    assert r["win"] == "true" and r["result"] == "no" and r["exit_type"] == "held"
    assert r["hold_hours"] == 4.08 and r["version"] == "#40 v5"
    # NO side close: yes book .44/.46 -> NO bid .54 ask .56 mid .55 -> CLV +4c
    assert r["close_mid"] == 0.55 and r["clv_c"] == pytest.approx(4.0) and r["close_source"] == "ws"
    # line: entry main line 236 -> 235.5; close main line 232 -> 231.5: fell 4 pts = toward UNDER
    assert r["line_move_pts"] == -4.0 and r["line_move_dir"] == "toward_us"
    assert r["final_total"] == 206.0 and r["overtime"] == "true" and r["margin_vs_line"] == 27.5
    assert r["net_pnl_usd"] == pytest.approx(2.0 - (1.02 + 0.02), abs=1e-6)
    assert r["roi_pct"] == pytest.approx((2.0 - 1.04) / 1.04 * 100, abs=0.1)
    # the other pick never filled
    u = by[T2]
    assert u["filled"] is False and u["what_went"] == "not filled"
    assert u["pick_ping_taker_price"] == 0.49
    # outputs
    summ = bj.write_outputs(rows, root=tmp_path, now=now)
    assert "BY STRATEGY" in summ and "fill%" in summ
    assert (tmp_path / "logs/bet_journal.csv").exists() and (tmp_path / "logs/bet_journal_COLUMNS.md").exists()
    assert list((tmp_path / "logs/bet_journal_archive").glob("*.csv"))
    header = (tmp_path / "logs/bet_journal.csv").read_text().splitlines()[0].split(",")
    assert "clv_c" in header and "notes" in header and len(header) == len(bj.COLS)


def test_no_clv_when_entered_after_tip(tmp_path):
    (tmp_path / "logs").mkdir()
    T = "KXNBAGAME-26OCT21ATLORL-ATL"
    raw = {"cash": {"0": "10", "1": "0", "2": "0", "3": "0"}, "positions": {}, "orders": {},
           "fills": {"0": [_fill(1, T, "yes", 0.05, "2026-10-22T00:30:00Z", "o1")]}, "settlements": []}
    led = tl.build_ledger(raw, tl.TagSources())
    ts_tip = int(datetime(2026, 10, 21, 23, 0, tzinfo=timezone.utc).timestamp())
    (tmp_path / "logs/bet_journal_cache.json").write_text(json.dumps(
        {"games": {}, "tops": {f"{ts_tip}|KXNBAGAME-26OCT21ATLORL": {T: [0.5, 0.52]}}}))
    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    r = bj.build_journal(raw, led, FakeClient(), root=tmp_path, now=now, kc=None, replay=False)[0]
    assert r["min_before_tip"] == -90.0 and r["clv_c"] == "" and r["close_source"] == "none" and r["exit_type"] == "open"
