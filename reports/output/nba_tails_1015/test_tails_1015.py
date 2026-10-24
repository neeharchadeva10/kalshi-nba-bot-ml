import math
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tails1015_lib as L
import run_unseen


def P(s, b, a, res="no", age=0):
    return (s, f"T{s}", b, a, age, res)


# ---- band edges
def test_over_band_lower_inclusive_upper_exclusive():
    assert L.tail_pick([P(1, .09, .11)], "over")[0]["mid"] == 0.10          # 0.10 in
    assert L.tail_pick([P(1, .08, .11)], "over")[0] is None                  # 0.095 out
    assert L.tail_pick([P(1, .14, .16)], "over")[0] is None                  # 0.15 out
    assert L.tail_pick([P(1, .13, .1599)], "over")[0] is not None            # just below .15 in


def test_under_band_lower_exclusive_upper_inclusive():
    assert L.tail_pick([P(1, .84, .86)], "under")[0] is None                 # 0.85 out
    assert L.tail_pick([P(1, .85, .8601)], "under")[0] is not None           # just above .85 in
    assert L.tail_pick([P(1, .89, .91)], "under")[0]["mid"] == 0.90          # 0.90 in
    assert L.tail_pick([P(1, .90, .9202)], "under")[0] is None               # >0.90 out


def test_missing_quote_not_candidate():
    assert L.tail_pick([P(1, None, .12), P(2, .11, None)], "over")[0] is None


# ---- nearest to center and ties
def test_over_nearest_center_not_largest():
    pts = [P(200, .10, .12), P(210, .12, .13), P(220, .13, .15)]   # mids .11 .125 .14
    assert L.tail_pick(pts, "over")[0]["strike"] == 210


def test_over_tie_lower_strike():
    pts = [P(230, .12, .13), P(220, .11, .14)]                      # both mid .125
    assert L.tail_pick(pts, "over")[0]["strike"] == 220
    pts = [P(220, .12, .13), P(230, .11, .14)]
    assert L.tail_pick(pts, "over")[0]["strike"] == 220


def test_over_tie_symmetric_distances():
    pts = [P(300, .11, .12), P(310, .13, .14)]                      # mids .115 and .135: both .01 from .125
    assert L.tail_pick(pts, "over")[0]["strike"] == 300


def test_under_nearest_center_and_tie_higher_strike():
    pts = [P(150, .85, .90), P(160, .86, .89), P(170, .88, .90)]    # .875 .875 .89
    assert L.tail_pick(pts, "under")[0]["strike"] == 160            # .875 tie between 150 (mid .875) and 160 -> higher
    pts = [P(160, .86, .89), P(150, .87, .88)]
    assert L.tail_pick(pts, "under")[0]["strike"] == 160
    pts = [P(1, .855, .875), P(2, .875, .895)]                      # mids .865 and .885, both .01 away
    assert L.tail_pick(pts, "under")[0]["strike"] == 2


# ---- requirements checked on the chosen strike, no fallback
def test_no_fallback_when_chosen_fails_spread():
    pts = [P(200, .06, .20),      # mid .13, spread .14, nearest to .125 -> chosen, fails
           P(210, .10, .15)]      # mid .125?? .125 exactly nearer
    # make the failing one the nearest
    pts = [P(200, .07, .18), P(210, .10, .1999)]                    # mids .125 (spread .11) and .15 (out)
    pick, _ = L.tail_pick(pts, "over")
    assert pick["strike"] == 200
    assert L.check_pick(pick) == "spread_gt_10c"
    pts = [P(200, .07, .18), P(210, .10, .13)]                      # 210 mid .115 farther; still 200 chosen, no fallback
    pick, _ = L.tail_pick(pts, "over")
    assert pick["strike"] == 200 and L.check_pick(pick) == "spread_gt_10c"
    rows = L.bet_rows({"game": "G", "date": "2026-02-01", "cp": "HT", "D": 1, "points": pts})
    assert [r["skip"] for r in rows if r["tail"] == "over"] == ["spread_gt_10c"]


def test_spread_exactly_10c_ok_and_bid_ask_requirements():
    assert L.check_pick(dict(bid=.07, ask=.17, result="no")) is None        # 0.10 spread allowed
    assert L.check_pick(dict(bid=.07, ask=.1701, result="no")) == "spread_gt_10c"
    assert L.check_pick(dict(bid=.0, ask=.05, result="no")) == "yes_bid_below_1c"
    assert L.check_pick(dict(bid=.95, ask=1.0, result="yes")) == "yes_ask_above_99c"
    assert L.check_pick(dict(bid=.95, ask=.99, result="yes")) is None
    assert L.check_pick(dict(bid=.01, ask=.05, result="no")) is None
    assert L.check_pick(dict(bid=.10, ask=.14, result="scalar")) == "no_binary_result"
    assert L.check_pick(dict(bid=.14, ask=.10, result="no")) == "crossed_quote"


def test_scalar_result_skipped_and_counted():
    rec = {"game": "G", "date": "2026-02-01", "cp": "Q3", "D": 1, "points": [P(200, .11, .14, res="scalar")]}
    rows = L.bet_rows(rec)
    assert [r["skip"] for r in rows] == ["no_binary_result", "no_strike_in_band"]
    out, _, bets = L.analyse([rec], {})
    assert out["scalar_results_skipped"] == 1 and bets == []


# ---- fee + slippage + sides
def test_fee_and_slippage_over_and_under():
    # over: NO at 1 - bid + .01. bid .11 ask .14 -> NO base .89, entry .90
    assert L.entry_price("over", .11, .14, True) == pytest.approx(0.90)
    fee = L.fee_taker(0.90)
    assert fee == pytest.approx(math.ceil(0.07 * .9 * .1 / 1e-4 - 1e-9) * 1e-4)   # 0.0063
    assert fee == pytest.approx(0.0063)
    assert L.net_side("over", .11, .14, "no", True) == pytest.approx(1 - 0.90 - 0.0063)
    assert L.net_side("over", .11, .14, "yes", True) == pytest.approx(-0.90 - 0.0063)
    # long-shot side of the over tail: YES at ask + .01
    assert L.entry_price("over", .11, .14, False) == pytest.approx(0.15)
    assert L.net_side("over", .11, .14, "yes", False) == pytest.approx(1 - 0.15 - L.fee_taker(0.15))
    # under: YES at ask + .01. bid .86 ask .89 -> .90
    assert L.entry_price("under", .86, .89, True) == pytest.approx(0.90)
    assert L.net_side("under", .86, .89, "yes", True) == pytest.approx(1 - 0.90 - 0.0063)
    assert L.net_side("under", .86, .89, "no", True) == pytest.approx(-0.90 - 0.0063)
    # under long shot: NO at 1 - bid + .01 = .15
    assert L.entry_price("under", .86, .89, False) == pytest.approx(0.15)


def test_bet_rows_values():
    rec = {"game": "G", "date": "2026-02-01", "cp": "HT", "D": 5,
           "points": [P(220, .11, .14, "no"), P(150, .86, .89, "yes")]}
    rows = {r["tail"]: r for r in L.bet_rows(rec)}
    assert rows["over"]["entry"] == pytest.approx(0.90) and rows["under"]["entry"] == pytest.approx(0.90)
    assert rows["over"]["net_fav"] == pytest.approx(0.0937) and rows["under"]["net_fav"] == pytest.approx(0.0937)


# ---- look-ahead / data windows
def test_windows_adjacent_and_assert_window():
    assert L.DISC_LAST < L.UNSEEN_FIRST and L.UNSEEN_FIRST == "2026-01-23" and L.UNSEEN_LAST == "2026-04-12"
    assert L.DISC_FIRST == "2025-10-21" and L.DISC_LAST == "2026-01-22"
    recs = [{"date": "2026-01-22"}, {"date": "2025-10-21"}]
    L.assert_window(recs, L.DISC_FIRST, L.DISC_LAST)
    with pytest.raises(AssertionError):
        L.assert_window(recs, L.UNSEEN_FIRST, L.UNSEEN_LAST)
    with pytest.raises(AssertionError):
        L.assert_window([{"date": "2026-01-23"}], L.DISC_FIRST, L.DISC_LAST)
    with pytest.raises(AssertionError):
        L.assert_window([{"date": "2026-04-13"}], L.UNSEEN_FIRST, L.UNSEEN_LAST)


def test_runners_use_the_right_windows():
    d = (HERE / "run_discovery.py").read_text()
    u = (HERE / "run_unseen.py").read_text()
    assert "build_records(L.DISC_FIRST, L.DISC_LAST)" in d and "UNSEEN" not in d
    assert "build_records(L.UNSEEN_FIRST, L.UNSEEN_LAST)" in u and "DISC_" not in u


def test_t1h_excluded_from_rule():
    recs = [{"game": "G", "date": "2026-02-01", "cp": "T-1h", "D": 1, "points": [P(1, .11, .14)]}]
    out, rows, bets = L.analyse(recs, {})
    assert rows == [] and out["pooled"]["n"] == 0


def test_sys_path_not_polluted_and_no_run_unseen_shadow():
    assert not any(p.replace("\\", "/").endswith("nba_ingame_overreaction") for p in sys.path)
    assert Path(run_unseen.__file__).resolve().parent == HERE


# ---- refusal gates
def test_refusal_gates(tmp_path):
    assert run_unseen.refusal(tmp_path)[0] == 2                      # AUDIT_OK missing
    (tmp_path / "AUDIT_OK").write_text("x")
    assert run_unseen.refusal(tmp_path) is None
    (tmp_path / "results_unseen.json").write_text("{}")
    assert run_unseen.refusal(tmp_path)[0] == 3                      # results exist
    (tmp_path / "AUDIT_OK").unlink()
    assert run_unseen.refusal(tmp_path)[0] == 2


def test_builder_never_created_audit_ok_or_unseen_results():
    assert not (HERE / "results_unseen.json").exists()


# ---- coin flip determinism
def _bets(n=60):
    rng = np.random.default_rng(1)
    return [{"net_fav": float(rng.choice([0.09, -0.9])), "net_ls": float(rng.choice([0.8, -0.15]))} for _ in range(n)]


def test_coin_flip_deterministic_and_in_range():
    b = _bets()
    a1, a2 = L.coin_flip_share(b), L.coin_flip_share(b)
    assert a1 == a2 and 0.0 <= a1 <= 1.0
    assert L.coin_flip_share([]) is None


def test_coin_flip_extremes():
    good = [{"net_fav": 0.5, "net_ls": -0.5}] * 50                    # real mean is the max possible
    assert L.coin_flip_share(good) == 0.0
    bad = [{"net_fav": -0.5, "net_ls": 0.5}] * 50
    assert L.coin_flip_share(bad) == 1.0


def test_bootstrap_deterministic():
    byg = {f"g{i}": [0.1 * (i % 3) - 0.1] for i in range(30)}
    assert L.T52.IGP.bootstrap_lb(byg, 1000, L.SEED) == L.T52.IGP.bootstrap_lb(byg, 1000, L.SEED)
