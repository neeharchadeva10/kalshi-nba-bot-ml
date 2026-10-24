"""Unit tests for research #60 (toy data only; no real outcomes)."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mvl_lib as L  # noqa: E402


def test_side_over_under_and_gap_threshold():
    e1 = np.array([230.0, 220.0, 226.0, 224.0])
    line = np.array([224.5, 226.5, 224.5, 224.0])
    act = np.array([230.0, 220.0, 220.0, 230.0])
    t = L.side_table(e1, line, act, 4)
    # game 1: over, gap 5.5, win; game 2: under, gap 6.5, win; game 3 gap 1.5 excluded; game 4 gap 0 excluded
    assert (t["n"], t["wins"]) == (2, 2)
    t0 = L.side_table(e1, line, act, 1)
    # game 3: over (226 > 224.5), actual 220 < line -> loss
    assert (t0["n"], t0["wins"]) == (3, 2)


def test_push_excluded():
    t = L.side_table([230.0, 230.0], [224.0, 224.0], [224.0, 230.0], 4)
    assert (t["n"], t["wins"], t["pushes"]) == (1, 1, 1)


def test_gap_inclusive():
    assert L.side_table([228.0], [224.0], [230.0], 4)["n"] == 1
    assert L.side_table([227.9], [224.0], [230.0], 4)["n"] == 0


def test_wilson_known_value():
    lo, hi = L.wilson(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3) and hi == pytest.approx(0.5962, abs=1e-3)
    assert np.isnan(L.wilson(0, 0)[0])


def test_pass_requires_n_and_lb():
    n = 200
    t = L.side_table(np.full(n, 230.0), np.full(n, 224.5), np.full(n, 240.0), 4)   # all win
    assert t["pass"] is True
    t = L.side_table(np.full(100, 230.0), np.full(100, 224.5), np.full(100, 240.0), 4)  # n < 150
    assert t["pass"] is False
    # 55% of 200 -> LB below 52.38%
    act = np.r_[np.full(110, 240.0), np.full(90, 200.0)]
    t = L.side_table(np.full(200, 230.0), np.full(200, 224.5), act, 4)
    assert t["rate"] == 0.55 and t["pass"] is False


def test_blend_math():
    assert L.blend(220.0, 230.0, 0.2) == pytest.approx(228.0)
    assert L.blend(220.0, 230.0, 0.0) == 230.0 and L.blend(220.0, 230.0, 1.0) == 220.0
    r = L.blend_vs_close(np.array([220.0, 240.0] * 50), np.array([230.0, 230.0] * 50), np.array([230.0, 230.0] * 50), 0.2)
    assert r["close_mae"] == 0.0 and r["blend_mae"] == pytest.approx(2.0) and r["pass"] is False


def test_w_fit_uses_only_passed_validation_games():
    rng = np.random.default_rng(0)
    act = rng.normal(225, 15, 400)
    line = act + rng.normal(0, 3, 400)
    e1_good = act + rng.normal(0, 3, 400)
    w1, _ = L.fit_w(e1_good, line, act)
    assert 0.3 <= w1 <= 0.7                        # two equally good, independent noisy predictors -> near 0.5
    w2, _ = L.fit_w(line + 10, line, act)          # E1 useless -> 0
    assert w2 == 0.0
    # test-season games are never an input: fit_w only sees its arguments (same result regardless of other data)
    w3, tab = L.fit_w(e1_good, line, act)
    assert w3 == w1 and len(tab) == 21 and 0.0 in tab and 1.0 in tab
    # tie -> smaller w
    w4, _ = L.fit_w(np.array([225.0] * 5), np.array([225.0] * 5), np.array([225.0] * 5))
    assert w4 == 0.0


def test_paired_bootstrap_deterministic_and_correct():
    d = np.random.default_rng(1).normal(-0.5, 2, 300)
    a, b = L.boot_ci(d), L.boot_ci(d)
    assert a == b and a[0] == pytest.approx(d.mean()) and a[1] < a[0] < a[2]
    assert L.boot_ci(d, seed=1) != a
    assert L.SEED == 20261023 and L.N_BOOT == 10000


def _bt(n):
    return pd.DataFrame({"valid": np.ones(n, bool), "K": np.full(n, 225.5), "net_over": np.full(n, 0.4), "net_under": np.full(n, -0.6),
                         "paid_over": np.full(n, 0.5), "paid_under": np.full(n, 0.5), "win_over": np.ones(n), "win_under": np.zeros(n)})


def test_kalshi_bets_side_gap_and_invalid():
    bt = _bt(4)
    bt.loc[3, "valid"] = False
    e1 = np.array([232.0, 219.0, 226.0, 240.0])
    med = np.array([225.5, 225.5, 225.5, 225.5])
    b = L.kalshi_bets(e1, med, bt, 4)
    assert list(b["idx"]) == [0, 1]                 # game 2 gap 0.5, game 3 invalid
    assert list(b["over"]) == [True, False]
    assert list(b["net"]) == [0.4, -0.6]
    assert L.kalshi_bets(e1, np.array([np.nan] * 4), bt, 4)["idx"].size == 0


def test_coin_flip_deterministic_and_fair():
    n = 400
    bt = _bt(n)
    b = L.kalshi_bets(np.full(n, 240.0), np.full(n, 225.5), bt, 4)      # all over: net +0.4 each -> actual mean 0.4 = max
    c1, c2 = L.coin_flip(b, bt), L.coin_flip(b, bt)
    assert c1 == c2 and c1["copies"] == 500
    assert c1["frac_copies_ge_actual"] == 0.0 and c1["copies_mean"] == pytest.approx(-0.1, abs=0.03)
    s = L.b_summary(b, bt, 4)
    assert s["pass"] is True and s["win_rate"] == 1.0


def test_b_pass_needs_all_conditions():
    n = 100
    bt = _bt(n)
    b = L.kalshi_bets(np.full(n, 240.0), np.full(n, 225.5), bt, 4)
    assert L.b_summary(b, bt, 4)["pass"] is False                          # n < 150


def test_lookahead_guard_toy():
    """The side/gap logic is a pure function of (e1, line); changing the actual never changes which games are bet."""
    e1 = np.array([230.0, 220.0, 226.0]); line = np.array([224.5, 226.5, 224.5])
    a = L.side_table(e1, line, np.array([240.0, 200.0, 230.0]), 4)
    b = L.side_table(e1, line, np.array([100.0, 300.0, 100.0]), 4)
    assert a["n"] == b["n"] == 2


def test_repro_check():
    assert L.repro_ok(14.79634434941582, 14.796344349415821)
    assert not L.repro_ok(14.796, 14.79634434941582)


def test_run_refuses_if_started(tmp_path, monkeypatch):
    import run
    monkeypatch.setattr(run, "HERE", tmp_path)
    run.refuse_if_started()                                             # fine when neither exists
    (tmp_path / "started.json").write_text("{}")
    with pytest.raises(SystemExit):
        run.refuse_if_started()
    (tmp_path / "started.json").unlink()
    (tmp_path / "results.json").write_text("{}")
    with pytest.raises(SystemExit):
        run.refuse_if_started()
