import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tails_lib as L
import run_unseen


def P(s, b, a, res="no", age=0):
    return (s, f"T{s}", b, a, age, res)


def test_over_picks_largest_mid_in_band():
    pts = [P(200, .01, .03), P(210, .03, .05), P(220, .06, .08), P(190, .20, .30)]
    pick, _ = L.tail_pick(pts, "over", 0.05)
    assert pick["strike"] == 210            # mid .04; .07 is out of the 0.05 band
    pick, _ = L.tail_pick(pts, "over", 0.10)
    assert pick["strike"] == 220


def test_under_picks_smallest_mid_in_band():
    pts = [P(150, .95, .97), P(140, .92, .94), P(130, .985, .995), P(160, .5, .6)]
    pick, _ = L.tail_pick(pts, "under", 0.05)
    assert pick["strike"] == 150            # mid .96; .93 is outside the .05 band, .99 is larger
    pick, _ = L.tail_pick(pts, "under", 0.10)
    assert pick["strike"] == 140


def test_band_edges_and_mid_099_inclusive():
    assert L.tail_pick([P(1, .00, .02)], "over", .05)[0]["mid"] == 0.01      # mid 0.01 is in [0.01, X]
    assert L.tail_pick([P(1, .00, .01)], "over", .05)[0] is None             # mid 0.005 below
    assert L.tail_pick([P(1, .98, 1.0)], "under", .05)[0]["mid"] == 0.99     # mid .99 inclusive


def test_edge_yes_bid_zero_skips_over():
    pick, _ = L.tail_pick([P(1, 0.0, 0.02)], "over", .05)
    assert pick is not None and L.check_pick(pick, "over") == "yes_bid_below_1c"


def test_edge_yes_ask_one_skips_under():
    pick, _ = L.tail_pick([P(1, 0.98, 1.0)], "under", .05)
    assert pick is not None and L.check_pick(pick, "under") == "yes_ask_above_99c"


def test_missing_quotes_not_candidates():
    assert L.tail_pick([P(1, None, .03), P(2, .02, None)], "over", .05)[0] is None


def test_selection_before_requirement():
    # the largest mid has bid 0 -> no bet (literal reading), not a fallback to the other strike
    pts = [P(1, .01, .03), P(2, .00, .09)]    # mids .02, .045
    pick, _ = L.tail_pick(pts, "over", .05)
    assert pick["strike"] == 2 and L.check_pick(pick, "over") is not None


def test_spread_filter():
    pick, _ = L.tail_pick([P(1, .02, .13)], "over", .10)      # mid .075, spread .11
    assert L.check_pick(pick, "over") == "spread_gt_10c"
    pick, _ = L.tail_pick([P(1, .02, .12)], "over", .10)      # spread exactly .10
    assert L.check_pick(pick, "over") is None


def test_tie_goes_to_lower_strike():
    pick, _ = L.tail_pick([P(5, .02, .04), P(3, .02, .04)], "over", .05)
    assert pick["strike"] == 3


def test_fee_rounding():
    assert L.fee_taker(0.5) == 0.0175
    assert L.fee_taker(0.96) == 0.0027      # 0.002688 -> 0.0027
    assert L.fee_taker(0.99) == 0.0007      # 0.000693 -> 0.0007


def test_slippage_and_net():
    # over tail: buy NO at 1 - bid = .97, +1c = .98, fee .0014 (0.07*.98*.02=.001372)
    assert L.entry_price("over", .03, .05) == 0.98
    assert L.net_side("over", .03, .05, "no") == pytest.approx(1 - .98 - .0014)
    assert L.net_side("over", .03, .05, "yes") == pytest.approx(-.98 - .0014)
    assert L.entry_price("under", .94, .96) == 0.97            # YES at ask .96 + 1c
    assert L.entry_price("over", .03, .05, fav=False) == 0.06   # long shot YES at ask .05 + 1c
    assert L.net_side("over", .03, .05, "yes", fav=False) == pytest.approx(1 - .06 - L.fee_taker(.06))
    assert L.entry_price("under", .94, .96, fav=False) == 0.07  # NO at 1 - .94 = .06 + 1c


def test_slippage_cap_skip_entry_ge_995():
    pick, _ = L.tail_pick([P(1, .01, .03)], "over", .05)     # NO at .99 + 1c = 1.00 -> skip
    assert L.check_pick(pick, "over") == "entry_ge_995"
    pick, _ = L.tail_pick([P(1, .02, .04)], "over", .05)     # NO at .98 + .01 = .99 -> ok
    assert L.check_pick(pick, "over") is None
    pick, _ = L.tail_pick([P(1, .97, .985)], "under", .05)   # mid .9775; YES .985 + .01 = .995 -> skip
    assert L.check_pick(pick, "under") == "entry_ge_995"


def test_no_binary_result_skipped():
    pick, _ = L.tail_pick([P(1, .02, .04, res="scalar")], "over", .05)
    assert L.check_pick(pick, "over") == "no_binary_result"


def rec(game, cp, pts, date="2025-12-01", D=1000):
    return {"game": game, "date": date, "cp": cp, "D": D, "points": pts}


def test_bet_rows_one_per_tail_band():
    rows = L.bet_rows(rec("g", "HT", [P(200, .02, .04, "no")]))
    assert len(rows) == 4 and {(r["tail"], r["band"]) for r in rows} == {(t, b) for t in L.TAILS for b in L.BANDS}
    over05 = [r for r in rows if r["tail"] == "over" and r["band"] == .05][0]
    assert over05["skip"] is None and over05["net_fav"] > 0 and over05["net_ls"] < 0


def test_split_filter_constants():
    assert L.DISC_FIRST == "2025-10-21" and L.DISC_LAST == "2026-01-22" and L.UNSEEN_FIRST == "2026-01-23"
    assert L.DISC_LAST < L.UNSEEN_FIRST


def test_build_discovery_slice_structure_only():
    recs, games, skips = L.build_records(L.DISC_FIRST, "2025-10-24")     # small discovery slice; structure only
    assert recs and all(L.DISC_FIRST <= r["date"] <= "2025-10-24" for r in recs)
    for r in recs:
        for s, tk, b, a, age, res in r["points"]:
            assert age >= 0                                  # quotes are at/before the decision time


def test_lookahead_guard_decision_candle():
    series = {"a": ([100, 150, 161, 300], [(.1, .2), (.1, .2), (.05, .07), (.5, .6)])}
    ec = L.IGP.EventCandles(series, {"a": 1.0}, {"a": "no"})
    d = L.IGP.decision_time(ec, 100, 10_000)       # C+60 = 160 -> candle 161
    assert d == 161
    pts = ec.points_at(d)
    assert pts[0][1:3] == (.05, .07) and pts[0][4] == 161     # the 300 candle is never visible
    assert ec.points_at(160)[0][4] == 150


def test_ingame_guards():
    base = {"Q1": 5000, "HT": 8000, "Q3": 11000, "end_q4": 14000}
    assert L.ingame_game_guard(base, 1000, 1000) is None            # Q1 start 4000s = 66 min after tip
    assert L.ingame_game_guard({**base, "HT": None}, 1000, 1000) == "pbp_missing_period"
    assert L.ingame_game_guard({**base, "Q1": 1000 + 10 * 60}, 1000, 1000) == "bad_wallclock"
    assert L.ingame_game_guard({**base, "Q1": 6000}, 3000, 3000 - 3601) == "stale_L0"


def synth_rows(n_games=80, seed=1, edge=0.0):
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n_games):
        for cp in L.CHECKPOINTS:
            res = "no" if rng.random() < 0.96 + edge else "yes"
            res2 = "yes" if rng.random() < .96 else "no"
            rows += L.bet_rows(rec(f"g{g}", cp, [P(200, .03, .05, res), P(150, .95, .97, res2)], D=g))
    return rows


def test_luck_test_deterministic_and_verdict():
    rows = synth_rows()
    a, b = L.luck_test(rows, 100, L.SEED), L.luck_test(rows, 100, L.SEED)
    assert a == b and a["verdict"] in ("PASS", "FAIL")
    assert L.luck_test(rows, 100, L.SEED + 1)["share_reps_ge_real"] is not None
    vs = L.version_stats(rows)
    assert len(vs) == 12 and L.select(vs) is not None


def test_luck_test_no_eligible():
    rows = synth_rows(n_games=10)
    assert L.luck_test(rows, 10)["verdict"] == "NO_ELIGIBLE"


def test_calibration_buckets():
    assert L.cal_bucket(.01) == (0, 2) and L.cal_bucket(.02) == (2, 5) and L.cal_bucket(.9999) == (98, 100)
    assert L.cal_bucket(.5) is None and L.cal_bucket(.12) == (10, 15)
    cal = L.calibration([rec("g", "HT", [P(1, .02, .04, "yes"), P(2, .01, .03, "no"), P(3, .5, .6, "yes")])])
    d = {c["bucket"]: c for c in cal}
    assert d["2-5c"]["n"] == 2 and d["2-5c"]["yes_rate"] == .5 and d["10-15c"]["n"] == 0


def test_loss_streak():
    bets = [{"D": i, "game": "g", "net_fav": x} for i, x in enumerate([.02, -.9, -.9, .02, -.9])]
    s = L.loss_streak(bets)
    assert s["worst_loss_streak"] == 2 and s["max_drawdown_dollars"] == pytest.approx(2.68)


def test_refusal_gates(tmp_path):
    assert run_unseen.refusal(tmp_path)[0] == 2                      # no AUDIT_OK
    (tmp_path / "AUDIT_OK").write_text("x")
    assert run_unseen.refusal(tmp_path)[0] == 4                      # no discovery results
    (tmp_path / "results_discovery.json").write_text(json.dumps({"luck_test": {"verdict": "FAIL"}, "selected": {"cp": "HT"}}))
    assert run_unseen.refusal(tmp_path)[0] == 4
    (tmp_path / "results_discovery.json").write_text(json.dumps({"luck_test": {"verdict": "PASS"}, "selected": None}))
    assert run_unseen.refusal(tmp_path)[0] == 4
    (tmp_path / "results_discovery.json").write_text(json.dumps({"luck_test": {"verdict": "PASS"}, "selected": {"cp": "HT"}}))
    assert run_unseen.refusal(tmp_path) is None
    (tmp_path / "results_unseen.json").write_text("{}")
    assert run_unseen.refusal(tmp_path)[0] == 3


def test_real_dir_refuses_without_audit_ok():
    if not (L.HERE / "AUDIT_OK").exists():
        assert run_unseen.refusal()[0] == 2
