import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fav_longshot as fl  # noqa: E402
import run_unseen  # noqa: E402


def test_decision_candle_no_lookahead():
    rows = [(100, 4000, 4100), (200, 5000, 5100), (300, 9000, 9100)]
    r, why = fl.pick_candle(rows, 250, 7200)
    assert r == (200, 5000, 5100) and why is None            # the ts=300 candle is in the future of D=250
    r, why = fl.pick_candle(rows, 300, 7200)
    assert r[0] == 300                                        # ending exactly at D is allowed
    assert fl.pick_candle(rows, 50, 7200) == (None, "no_candle")
    assert fl.pick_candle(rows, 300 + 7201, 7200) == (None, "stale")
    assert fl.pick_candle(rows, 300 + 7200, 7200)[0][0] == 300
    # T-24h allows 6h, others 2h
    assert fl.MAX_AGE["T-24h"] == 6 * 3600 and fl.MAX_AGE["T-3h"] == 2 * 3600 and fl.MAX_AGE["T-1h"] == 2 * 3600


def rec(teams, sport="NBA", D="T-1h"):
    return {"sport": sport, "split": "discovery", "season": "x", "D": D, "gid": "g", "T": 0, "teams": teams}


def test_fav_vs_long_no_pricing():
    # team A bid .78 ask .80 (fav, wins); team B bid .18 ask .21 (dog, loses)
    r = rec([("A", 7800, 8000, True), ("B", 1800, 2100, False)])
    entry, win, code = fl.select_bet(r, "FAV", 70, 85)
    assert (entry, win, code) == (8000, True, "A")            # FAV pays the ask
    entry, win, code = fl.select_bet(r, "LONG-NO", 15, 30)    # dog bid .18 in band
    assert entry == 10000 - 1800 and code == "B"              # NO ask = 1 - YES bid
    assert win is True                                        # NO wins because B lost
    r2 = rec([("A", 7800, 8000, False), ("B", 1800, 2100, True)])
    assert fl.select_bet(r2, "LONG-NO", 15, 30)[1] is False   # dog won -> NO loses
    assert fl.select_bet(r, "FAV", 55, 70) is None
    assert fl.select_bet(r, "LONG-NO", 5, 15) is None
    # band edges half-open
    assert fl.select_bet(rec([("A", 6900, 7000, True), ("B", 2900, 3000, False)]), "FAV", 70, 85)[0] == 7000
    assert fl.select_bet(rec([("A", 6900, 7000, True), ("B", 2900, 3000, False)]), "FAV", 55, 70) is None


def test_two_in_band_takes_favourite():
    r = rec([("A", 5500, 5600, True), ("B", 4300, 4400, False)])   # only A in 55-70
    assert fl.select_bet(r, "FAV", 55, 70)[2] == "A"
    r = rec([("A", 5500, 5600, True), ("B", 5600, 5700, False)])   # both in band: higher ask
    assert fl.select_bet(r, "FAV", 55, 70)[2] == "B"
    r = rec([("A", 5500, 5600, True), ("B", 5500, 5600, False)])   # tie -> skipped
    c = Counter()
    assert fl.select_bet(r, "FAV", 55, 70, c) is None and sum(c.values()) == 1


def test_fees_and_slippage():
    assert fl.fee_e4(5000) == 175                              # 0.07*.25 = .0175 exactly
    assert fl.fee_e4(8100) == -(-7 * 8100 * 1900 // 1000000)   # .07*.81*.19=.010773 -> .0108
    assert fl.fee_e4(8100) == 108
    assert fl.fee_e4(9000) == 63                               # .0063 exact
    assert fl.fee_e4(9500) == 34                               # .07*.95*.05=.003325 -> .0034 (rounded UP)
    assert fl.fee_e4(1) == 1                                   # never rounds to zero
    # win at ask .80: pays .81 + fee(.81)=.0108 -> net 1-.81-.0108
    assert fl.entry_net_e4(8000, True) == 10000 - 8100 - 108
    assert fl.entry_net_e4(8000, False) == -8100 - 108


def test_grid_is_30_and_bands():
    v = fl.all_versions()
    assert len(v) == 30 == len(set(v))
    assert Counter(x[2] for x in v) == {"FAV": 18, "LONG-NO": 12}
    assert {(x[3], x[4]) for x in v if x[2] == "FAV"} == {(55, 70), (70, 85), (85, 95)}
    assert {(x[3], x[4]) for x in v if x[2] == "LONG-NO"} == {(5, 15), (15, 30)}


def test_lb_selection_min_n():
    v = fl.all_versions()
    stats = {x: {"n": 10, "lb": 5.0} for x in v}              # big LB but n < 60: ineligible
    stats[v[3]] = {"n": 60, "lb": -0.2}
    stats[v[7]] = {"n": 100, "lb": -0.1}
    stats[v[9]] = {"n": 59, "lb": 9.0}
    assert fl.select_best(stats) == v[7]
    assert fl.select_best({x: {"n": 5, "lb": 1.0} for x in v}) is None
    s = fl.lb_stats([1.0, 0.0, 1.0, 0.0])
    assert s["lb"] == pytest.approx(0.5 - 1.96 * np.std([1, 0, 1, 0], ddof=1) / 2)


def synth(n=200, seed=1):
    rng = np.random.default_rng(seed)
    recs = []
    for i in range(n):
        p = rng.uniform(0.2, 0.8)
        a_win = rng.random() < p
        ask_a = int(p * 1e4) + 100
        bid_a = ask_a - 200
        ask_b = int((1 - p) * 1e4) + 100
        bid_b = ask_b - 200
        recs.append({"sport": "NBA", "split": "discovery", "season": "x", "D": "T-1h", "gid": str(i), "T": i,
                     "teams": [("A", bid_a, ask_a, bool(a_win)), ("B", bid_b, ask_b, not a_win)]})
    return recs


def test_luck_price_matching_and_determinism():
    recs = synth()
    pool = fl.Pool(recs, "NBA", "T-1h")
    for entry in (3000, 5000, 7000):
        lo, hi = pool.range_for(entry)
        assert hi > lo
        assert (np.abs(pool.entry[lo:hi] - entry) <= 200).all()
        if lo > 0:
            assert abs(pool.entry[lo - 1] - entry) > 200
        if hi < len(pool.entry):
            assert abs(pool.entry[hi] - entry) > 200
    bets, stats = fl.version_table(recs)
    # make sure at least one version is eligible with a lowered threshold
    r1 = fl.luck_test(recs, bets, stats, reps=20, min_n=20)
    r2 = fl.luck_test(recs, bets, stats, reps=20, min_n=20)
    assert r1 == r2 and r1["real_best"] is not None
    r3 = fl.luck_test(recs, bets, stats, reps=20, min_n=20, seed=5)
    assert r3["rep_best_lb_mean"] != r1["rep_best_lb_mean"]
    c1 = fl.coin_flip_unseen(recs, bets[("NBA", "T-1h", "FAV", 55, 70)], ("NBA", "T-1h", "FAV", 55, 70), reps=50)
    c2 = fl.coin_flip_unseen(recs, bets[("NBA", "T-1h", "FAV", 55, 70)], ("NBA", "T-1h", "FAV", 55, 70), reps=50)
    assert c1 == c2
    assert fl.bootstrap_lb([0.1] * 50) == pytest.approx(0.1)


def test_unseen_gate_refuses(tmp_path):
    with pytest.raises(SystemExit):
        run_unseen.check_gate(tmp_path)
    (tmp_path / "AUDIT_OK").write_text("x")
    run_unseen.check_gate(tmp_path)
