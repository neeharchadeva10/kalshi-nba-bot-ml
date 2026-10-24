"""Unit tests for research log #54 (toy data only; no unseen outcomes touched)."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lm as L
import pipeline as PL

T0 = 1_800_000_000          # toy tip
D0 = T0 - 3600


def ladder_book(mid_by_strike_at, T=T0, spread=0.04):
    """mid_by_strike_at: {strike: [(end_ts, mid), ...]} -> Book."""
    strikes, candles = {}, {}
    for s, pts in mid_by_strike_at.items():
        tk = f"K-{int(s)}"
        strikes[tk] = s
        e, b, a = zip(*[(t, m - spread / 2, m + spread / 2) for t, m in pts])
        candles[tk] = (list(e), list(b), list(a))
    return L.Book(strikes, candles, T)


def flat(mids, ends):
    return {s: [(e, m) for e in ends] for s, m in mids.items()}


def test_crossing_interpolates():
    b = ladder_book(flat({218.5: 0.8, 220.5: 0.6, 222.5: 0.4, 224.5: 0.2}, [D0 - 60]))
    assert L.Book.M(b, D0) == pytest.approx(221.5)


def test_no_crossing_missing():
    b = ladder_book(flat({218.5: 0.3, 220.5: 0.2}, [D0 - 60]))
    assert b.M(D0) is None
    b = ladder_book(flat({218.5: 0.9, 220.5: 0.8}, [D0 - 60]))
    assert b.M(D0) is None


def test_spread_filter_drops_wide_books():
    # the 0.5-crossing strikes are wide (0.2 > 0.15): dropped -> no crossing
    b = ladder_book(flat({218.5: 0.8, 220.5: 0.6, 222.5: 0.4, 224.5: 0.2}, [D0 - 60]), spread=0.20)
    assert b.M(D0) is None


def test_staleness_and_no_lookahead():
    # candle ending after t must not be used; candle older than 10 min must not be used
    b = ladder_book({218.5: [(D0 - 700, 0.8), (D0 + 30, 0.2)], 220.5: [(D0 - 700, 0.2)]})
    assert b.M(D0) is None                       # both strikes' last candle ends 700 s before D: stale
    b = ladder_book({218.5: [(D0 - 600, 0.8), (D0 + 30, 0.2)], 220.5: [(D0 - 600, 0.2)]})
    assert b.M(D0) == pytest.approx(219.5)       # exactly 600 s old is allowed; the later candle (0.2) is ignored
    # one stale strike simply drops out
    b = ladder_book({218.5: [(D0 - 60, 0.8)], 220.5: [(D0 - 60, 0.2)], 224.5: [(D0 - 1200, 0.1)]})
    assert b.M(D0) == pytest.approx(219.5)


def test_M_after_tip_missing():
    b = ladder_book(flat({218.5: 0.8, 220.5: 0.2}, [T0 - 60]))
    assert b.M(T0 + 1) is None
    assert b.M(T0) is not None


def test_move_and_trigger():
    # M(D-2h)=219.5, M(D)=222.5 (move +3.0)
    pts = {218.5: [(D0 - 7200 - 60, 0.8), (D0 - 60, 1.0 - 1e-9 - 0.05)], 220.5: [(D0 - 7200 - 60, 0.2), (D0 - 60, 0.7)],
           224.5: [(D0 - 7200 - 60, 0.1), (D0 - 60, 0.3)]}
    b = ladder_book(pts)
    mv, m1, m0 = L.move_at(b, 2)
    assert m0 == pytest.approx(219.5)
    assert mv == pytest.approx(m1 - m0) and mv > 0
    assert L.triggered(3.0, 3.0) and not L.triggered(2.99, 3.0) and L.triggered(-3.0, 3.0) and not L.triggered(None, 1.5)
    # 4h move is missing (no candles that early)
    assert L.move_at(b, 4)[0] is None


def test_follow_fade_sides():
    assert L.side_for(+2, "follow") == "YES" and L.side_for(-2, "follow") == "NO"
    assert L.side_for(+2, "fade") == "NO" and L.side_for(-2, "fade") == "YES"


def test_fee_and_slippage():
    assert L.fee_taker(0.5) == 0.0175
    assert L.fee_taker(0.51) == 0.0175
    assert L.fee_taker(0.10) == 0.0063
    n = L.bet_nets(bid=0.48, ask=0.52, result="yes")
    p_yes = 0.53
    assert n["price_YES"] == pytest.approx(p_yes)
    assert n["YES"] == pytest.approx(1 - p_yes - L.fee_taker(p_yes))
    p_no = 0.53                                   # 1 - 0.48 + 0.01
    assert n["price_NO"] == pytest.approx(p_no)
    assert n["NO"] == pytest.approx(0 - p_no - L.fee_taker(p_no))
    n2 = L.bet_nets(0.48, 0.52, "no")
    assert n2["NO"] == pytest.approx(1 - 0.53 - L.fee_taker(0.53)) and n2["YES"] < 0


def test_main_strike_filter_and_nearest():
    b = ladder_book(flat({218.5: 0.80, 220.5: 0.55, 222.5: 0.40}, [D0 - 60]))
    m = b.main_strike(D0)
    assert m["strike"] == 220.5
    # wide book (0.12 > 0.10) skipped
    b = ladder_book(flat({220.5: 0.5}, [D0 - 60]), spread=0.12)
    assert b.main_strike(D0) is None
    # mid outside 15-85 skipped
    b = ladder_book(flat({220.5: 0.9, 222.5: 0.1}, [D0 - 60]))
    assert b.main_strike(D0) is None
    # candle after D ignored
    b = ladder_book({220.5: [(D0 + 60, 0.5)]})
    assert b.main_strike(D0) is None


def test_version_grid():
    assert len(L.VERSIONS) == 8 and len(set(L.VERSIONS)) == 8


def test_version_bets_toy():
    ent = {"strike": 220.5, **L.bet_nets(0.48, 0.52, "yes")}
    recs = [{"event": "A", "date": "2025-11-01", "move_2h": 2.0, "move_4h": None, "entry": ent},
            {"event": "B", "date": "2025-11-01", "move_2h": -4.0, "move_4h": -4.0, "entry": ent},
            {"event": "C", "date": "2025-11-01", "move_2h": 5.0, "move_4h": 5.0, "entry": None}]
    f = PL.version_bets(recs, (2, 1.5, "follow"))
    assert [(b["event"], b["side"]) for b in f] == [("A", "YES"), ("B", "NO")]
    g = PL.version_bets(recs, (2, 3.0, "fade"))
    assert [(b["event"], b["side"]) for b in g] == [("B", "YES")]
    assert PL.version_bets(recs, (4, 1.5, "follow"))[0]["event"] == "B"


def test_split_filter():
    assert L.in_split("2025-10-21", L.DISC_FIRST, L.DISC_LAST) and L.in_split("2026-01-22", L.DISC_FIRST, L.DISC_LAST)
    assert not L.in_split("2026-01-23", L.DISC_FIRST, L.DISC_LAST) and not L.in_split("2025-10-20", L.DISC_FIRST, L.DISC_LAST)
    disc = PL.load_events(L.DISC_FIRST, L.DISC_LAST)
    uns = PL.load_events(L.UNSEEN_FIRST, L.UNSEEN_LAST)
    assert disc and uns
    assert max(e["game_date_et"] for e in disc) <= "2026-01-22" and min(e["game_date_et"] for e in uns) >= "2026-01-23"
    assert not ({e["event"] for e in disc} & {e["event"] for e in uns})


def test_selection():
    st = {"a": {"n": 59, "lb": 0.9}, "b": {"n": 60, "lb": -0.1}, "c": {"n": 100, "lb": -0.05}, "d": {"n": 10, "lb": None}}
    assert L.select_version(st) == "c"
    assert L.select_version({"a": {"n": 5, "lb": 1.0}}) is None


def test_luck_deterministic_and_shared():
    rng = np.random.default_rng(1)
    n = 200
    ny = rng.normal(0, 0.5, n)
    nn = -ny
    sets = {i: rng.random(n) < 0.5 for i in range(8)}
    a = L.luck_test(sets, ny, nn, 0.05)
    b = L.luck_test(sets, ny, nn, 0.05)
    assert a == b
    assert L.luck_test(sets, ny, nn, 10.0)["share_reps_as_good"] == 0.0
    assert L.luck_test(sets, ny, nn, -10.0)["verdict"] == "FAIL"
    # all versions with n<60 -> no eligible, share 0 (reps can't reach)
    small = {0: np.arange(n) < 10}
    assert L.luck_test(small, ny, nn, 0.0)["share_reps_as_good"] == 0.0


def test_luck_pure_noise_not_significant():
    rng = np.random.default_rng(2)
    n = 300
    ny = rng.normal(-0.02, 0.5, n)
    nn = rng.normal(-0.02, 0.5, n)
    sets = {i: rng.random(n) < 0.4 for i in range(8)}
    real = max(L.mean_sd_lb(np.where(rng.random(m.sum()) < 0.5, ny[m], nn[m]))["lb"] for m in sets.values())
    assert L.luck_test(sets, ny, nn, real)["share_reps_as_good"] > 0.0   # a noise "winner" is matched by chance reps


def test_ols_slope():
    x = np.arange(20.0)
    r = L.ols_slope(x, 2 * x + 1)
    assert r["slope"] == pytest.approx(2) and r["intercept"] == pytest.approx(1) and r["se"] == pytest.approx(0, abs=1e-9)
    rng = np.random.default_rng(3)
    y = 0.5 * x + rng.normal(0, 1, 20)
    r = L.ols_slope(x, y)
    assert r["se"] > 0 and abs(r["slope"] - 0.5) < 0.3


def test_unseen_gate(tmp_path):
    ok, why = L.unseen_gate(tmp_path)
    assert not ok and "AUDIT_OK" in why
    (tmp_path / "AUDIT_OK").write_text("x")
    (tmp_path / "results_discovery.json").write_text(json.dumps({"selected_version": "v", "luck_test": {"verdict": "FAIL"}}))
    ok, why = L.unseen_gate(tmp_path)
    assert not ok and "luck" in why
    (tmp_path / "results_discovery.json").write_text(json.dumps({"selected_version": "v", "luck_test": {"verdict": "PASS"}}))
    assert L.unseen_gate(tmp_path)[0]
    (tmp_path / "results_unseen.json").write_text("{}")
    ok, why = L.unseen_gate(tmp_path)
    assert not ok and "already exists" in why
    (tmp_path / "results_unseen.json").unlink()
    (tmp_path / "results_discovery.json").write_text(json.dumps({"selected_version": None, "luck_test": {"verdict": "PASS"}}))
    assert not L.unseen_gate(tmp_path)[0]


def test_run_unseen_uses_gate_and_builder_never_made_audit_ok():
    src = (L.HERE / "run_unseen.py").read_text()
    assert "unseen_gate()" in src and src.index("unseen_gate()") < src.index("build_records")
    assert "write_text" not in src.split("unseen_gate()")[0]
