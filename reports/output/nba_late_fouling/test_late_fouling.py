"""Unit tests for research log #51 (toy data only)."""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib as L
import run_unseen as RU

T0 = int(pd.Timestamp("2026-01-01T00:00:00Z").timestamp())


def wc(sec):
    return pd.Timestamp(T0 + sec, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def toy_pbp():
    rows = []
    n = 0

    def add(per, mm, ss, h, a, sec, typ="Jump Shot"):
        nonlocal n
        n += 1
        rows.append({"game_play_number": n, "period_number": per, "clock_minutes": mm, "clock_seconds": ss, "home_score": h,
                     "away_score": a, "wallclock": wc(sec), "type_text": typ})
    add(1, 12, 0, 0, 0, 0)
    add(2, 12, 0, 20, 18, 40 * 60)                 # first event of period 2 -> q1
    add(4, 12, 0, 80, 70, 100 * 60)
    add(4, 6, 0, 90, 80, 110 * 60)
    add(4, 5, 0, 92, 80, 120 * 60)                 # exactly 300 -> C_300 here, margin 12
    add(4, 4, 59, 94, 80, 121 * 60)
    add(4, 3, 0, 95, 82, 130 * 60)                 # exactly 180: margin 13
    add(4, 2, 30, 95, 84, 131 * 60, "Free Throw - 1 of 2")
    add(4, 2, 0, 96, 88, 135 * 60)                 # 120: margin 8
    add(4, 1, 0, 96, 90, 138 * 60, "Free Throw - 2 of 2")
    add(4, 0, 0, 100, 90, 140 * 60)
    df = pd.DataFrame(rows)
    return df.sample(frac=1, random_state=3)       # unsorted on purpose


def test_fee_taker_and_rounding():
    assert L.fee_taker(0.5) == 0.0175
    assert L.fee_taker(0.3) == 0.0147
    assert L.fee_taker(0.99) == 0.0007
    assert L.fee_taker(0.43) == pytest.approx(0.0172)        # 0.07*.43*.57 = 0.017157 -> up to 0.0172


def test_checkpoint_selection_first_event_le_R():
    cp = L.late_checkpoints(toy_pbp())
    assert cp["R"][300]["ts"] == T0 + 120 * 60 and cp["R"][300]["margin"] == 12
    assert cp["R"][180]["ts"] == T0 + 130 * 60 and cp["R"][180]["margin"] == 13
    assert cp["R"][120]["ts"] == T0 + 135 * 60 and cp["R"][120]["margin"] == 8
    assert cp["q1"] == T0 + 40 * 60 and cp["final_total"] == 190 and cp["ot"] is False
    assert cp["R"][300]["ft_after"] == 2 and cp["R"][120]["ft_after"] == 1       # strictly after the checkpoint event


def test_checkpoint_missing_when_clock_never_reaches():
    g = toy_pbp()
    g = g[g.clock_minutes * 60 + g.clock_seconds > 150]
    assert L.late_checkpoints(g)["R"][120] is None


def test_ot_flag_and_final_includes_ot():
    g = toy_pbp()
    extra = pd.DataFrame([{"game_play_number": 99, "period_number": 5, "clock_minutes": 4, "clock_seconds": 0, "home_score": 105,
                           "away_score": 95, "wallclock": wc(150 * 60), "type_text": "Jump Shot"}])
    cp = L.late_checkpoints(pd.concat([g, extra]))
    assert cp["ot"] and cp["final_total"] == 200


def test_margin_buckets_and_states():
    assert [L.bucket(m) for m in (0, 3, 4, 6, 7, 10, 11, 14, 15, 19, 20, 40)] == \
        ["0-3", "0-3", "4-6", "4-6", "7-10", "7-10", "11-14", "11-14", "15-19", "15-19", "20+", "20+"]
    assert len(L.versions()) == 12
    r = {"margin": 3}
    assert L.in_state(r, "close", 3) and L.in_state(r, "close", 6) and not L.in_state(r, "blow", 15)
    assert L.in_state({"margin": 15}, "blow", 15) and not L.in_state({"margin": 15}, "blow", 20)
    assert not L.in_state({"margin": 10}, "close", 6) and not L.in_state({"margin": 10}, "blow", 15)
    assert L.side_for_state("close") == "yes" and L.side_for_state("blow") == "no"


def test_net_bet_prices_fee_and_skip():
    # YES at ask .40 + .01 slippage
    n = L.net_bet("yes", "yes", 0.36, 0.40)
    assert n == pytest.approx(1 - 0.41 - L.fee_taker(0.41))
    # NO ask = 1 - yes_bid = .42, +.01 slippage
    n = L.net_bet("no", "yes", 0.58, 0.62)
    assert n == pytest.approx(-0.43 - L.fee_taker(0.43))     # NO loses when the strike resolves yes
    # entry after slippage >= 0.99 or <= 0.01 -> skipped
    assert L.net_bet("yes", "yes", 0.97, 0.98) is None                 # 0.98 + 0.01 = 0.99
    assert L.net_bet("yes", "yes", 0.96, 0.97) is not None             # 0.98
    assert L.net_bet("no", "no", 0.995, 0.999, slip=0.0) is None       # NO ask 0.005 <= 0.01
    assert L.net_bet("no", "no", 0.99, 0.999, slip=0.01) is not None   # NO ask .01 + .01 = .02
    # +2c sensitivity is strictly worse
    assert L.net_bet("yes", "yes", 0.4, 0.45, slip=0.02) < L.net_bet("yes", "yes", 0.4, 0.45)


# ---------- decision candle / look-ahead
def mk_ec(series, strikes, results=None):
    return L.EventCandles({t: (list(ts), list(q)) for t, (ts, q) in series.items()}, strikes, results or {})


def test_decision_candle_first_at_or_after_C_plus_60():
    c = T0 + 7000
    ec = mk_ec({"A": ([c + 30, c + 60, c + 120], [(0.4, 0.5)] * 3)}, {"A": 230.5})
    d, why = L.decision_time(ec, c, c + 5000)
    assert d == c + 60 and why is None
    ec = mk_ec({"A": ([c + 30, c + 59], [(0.4, 0.5)] * 2)}, {"A": 230.5})
    assert L.decision_time(ec, c, c + 5000) == (None, "no_decision_candle")
    ec = mk_ec({"A": ([c + 60 + 301], [(0.4, 0.5)])}, {"A": 230.5})          # 5-minute wait cap
    assert L.decision_time(ec, c, c + 5000) == (None, "no_decision_candle")
    ec = mk_ec({"A": ([c + 60 + 300], [(0.4, 0.5)])}, {"A": 230.5})
    assert L.decision_time(ec, c, c + 5000)[0] == c + 360
    assert L.decision_time(ec, c, c + 100) == (None, "decision_after_game_end")


def test_no_lookahead_quotes_only_before_decision():
    c = T0 + 7000
    ec = mk_ec({"A": ([c + 60, c + 120], [(0.40, 0.46), (0.10, 0.12)])}, {"A": 230.5}, {"A": "yes"})
    info = {"ts": c, "margin": 2, "ft_after": 0}
    r = L.make_record(ec, 300, info, c - 150 * 60, c + 3000, 240, False)
    assert r["D"] == c + 60 and r["yes_bid"] == 0.40 and r["yes_ask"] == 0.46      # the later candle is not used


def test_make_record_skips():
    c = T0 + 7000
    tip = c - 150 * 60
    ec = mk_ec({"A": ([c + 60], [(0.40, 0.46)])}, {"A": 230.5}, {"A": "yes"})
    info = {"ts": c, "margin": 2, "ft_after": 0}
    assert L.make_record(ec, 300, None, tip, c + 3000, 240, False)["skip"] == "no_checkpoint"
    assert L.make_record(ec, 300, info, c - 99 * 60, c + 3000, 240, False)["skip"] == "checkpoint_outside_window"
    assert L.make_record(ec, 300, info, c - 221 * 60, c + 3000, 240, False)["skip"] == "checkpoint_outside_window"
    wide = mk_ec({"A": ([c + 60], [(0.30, 0.46)])}, {"A": 230.5}, {"A": "yes"})
    assert L.make_record(wide, 300, info, tip, c + 3000, 240, False)["skip"] == "no_main_strike_spread"
    nores = mk_ec({"A": ([c + 60], [(0.40, 0.46)])}, {"A": 230.5}, {"A": None})
    assert L.make_record(nores, 300, info, tip, c + 3000, 240, False)["skip"] == "no_result"
    ok = L.make_record(ec, 300, info, tip, c + 3000, 240, False)
    assert ok["skip"] is None and ok["bucket"] == "0-3" and ok["lag"] == 0 and ok["age_s"] == 0


def test_live_main_strike_nearest_half():
    pts = [(228.5, 0.80, 0.84), (230.5, 0.52, 0.58), (232.5, 0.20, 0.25)]
    assert L.main_strike(pts) == (230.5, 0.52, 0.58)


def test_process_game_guards():
    tip = T0
    cps = L.late_checkpoints(toy_pbp())
    ser = {"A": ([T0 + 8000], [(0.4, 0.5)])}
    out, why = L.process_game("E", "2026-01-01", tip, cps, ser, {"A": 230.5}, {"A": "yes"})
    assert why is None and len(out) == 3
    _, why = L.process_game("E", "d", T0 + 40 * 60 - 10 * 60, cps, ser, {"A": 1}, {})     # q1 = tip + 10 min
    assert why == "bad_wallclock"
    _, why = L.process_game("E", "d", T0 - 41 * 60, cps, ser, {"A": 1}, {})              # q1 = tip + 81 min
    assert why == "bad_wallclock"
    _, why = L.process_game("E", "d", tip, cps, {}, {"A": 1}, {})
    assert why == "no_candles"
    assert L.process_game("E", "d", tip, dict(cps, has_p4=False), ser, {}, {})[1] == "pbp_missing_period"


def test_split_filter_applies_before_outcomes():
    assert (L.DISC_FIRST, L.DISC_LAST) == ("2025-10-21", "2026-01-22")
    assert (L.UNSEEN_FIRST, L.UNSEEN_LAST) == ("2026-01-23", "2026-04-12")
    ev = pd.read_csv(L.INJ / "events_list.csv")           # event list only (no outcomes)
    sub = ev[(ev.game_date_et >= L.DISC_FIRST) & (ev.game_date_et <= L.DISC_LAST)]
    assert len(sub) > 0
    only = sub.event.iloc[:1].tolist()
    recs, games, skips = L.build_records(L.UNSEEN_FIRST, L.UNSEEN_LAST, only_events=only)   # discovery event, unseen window
    assert recs == {} and games == [] and skips == {}


def synth_recs(n=400, seed=1, edge=0.0):
    rng = np.random.default_rng(seed)
    recs = {}
    for i in range(n):
        for R in L.RS:
            m = int(rng.choice([1, 2, 5, 9, 16, 22]))
            bid = float(rng.uniform(0.3, 0.6))
            res = "yes" if rng.random() < 0.5 + edge else "no"
            recs[(f"G{i}", R)] = {"R": R, "skip": None, "margin": m, "bucket": L.bucket(m), "yes_bid": bid, "yes_ask": bid + 0.04,
                                  "result": res, "date": "2025-11-01", "D": 1, "LC": 230.0, "final": 231, "ot": False,
                                  "ft_after": 3, "spread": 0.04, "lag": 0, "age_s": 0}
    return recs


def test_select_requires_n60_and_best_lb():
    rows = [{"n": 59, "lb": 5.0}, {"n": 60, "lb": 0.1}, {"n": 100, "lb": 0.2}, {"n": 100, "lb": float("nan")}]
    assert L.select(rows)["lb"] == 0.2
    assert L.select([{"n": 10, "lb": 1.0}]) is None


def test_all_version_stats_counts():
    rows = L.all_version_stats(synth_recs(300))
    assert len(rows) == 12
    by = {(r["R"], r["state"], r["M"]): r for r in rows}
    assert by[(300, "close", 6)]["n"] >= by[(300, "close", 3)]["n"]
    assert by[(300, "blow", 15)]["n"] >= by[(300, "blow", 20)]["n"]
    for r in rows:
        assert r["plus2c_mean"] < r["mean"]


def test_luck_test_deterministic():
    recs = synth_recs(300)
    a = L.luck_test(recs, reps=60, seed=L.SEED)
    b = L.luck_test(recs, reps=60, seed=L.SEED)
    assert a == b and a["reps"] == 60 and a["seed"] == 20261022


def test_luck_test_real_edge_passes():
    recs = synth_recs(600)
    for k, r in recs.items():
        if r["margin"] >= 15:
            r["result"] = "no"          # planted perfect edge for blowout NO bets
    out = L.luck_test(recs, reps=80)
    assert out["verdict"] == "PASS" and out["share_reps_ge_real"] <= 0.05


def test_luck_test_null_often_fails():
    fails = sum(L.luck_test(synth_recs(300, seed=s), reps=60)["verdict"] == "FAIL" for s in range(1, 6))
    assert fails >= 3        # no edge: the real best LB is a noise draw, most worlds must FAIL


def test_bootstrap_lb_deterministic():
    d = {f"g{i}": [0.1 * (i % 3) - 0.05] for i in range(50)}
    assert L.bootstrap_lb(d, draws=200) == L.bootstrap_lb(d, draws=200)


def test_calibration_blocks():
    cal = L.calibration_and_descriptives(synth_recs(50))
    e = cal["R300|0-3"]
    assert e["mean_final_minus_LC"] == pytest.approx(1.0) and e["mean_ft_attempts_after_CR"] == 3 and e["ot_rate"] == 0
    assert len(cal) == 18


def test_refusal_gates(tmp_path):
    assert RU.refusal(tmp_path)[0] == 2                                  # no AUDIT_OK
    (tmp_path / "AUDIT_OK").write_text("x")
    assert RU.refusal(tmp_path)[0] == 4                                  # no discovery results
    dj = tmp_path / "results_discovery.json"
    dj.write_text(json.dumps({"luck_test": {"verdict": "FAIL"}, "selected": {"a": 1}}))
    assert RU.refusal(tmp_path)[0] == 4
    dj.write_text(json.dumps({"luck_test": {"verdict": "PASS"}, "selected": None}))
    assert RU.refusal(tmp_path)[0] == 4
    dj.write_text(json.dumps({"luck_test": {"verdict": "PASS"}, "selected": {"a": 1}, "partial_download": True}))
    assert RU.refusal(tmp_path)[0] == 4
    dj.write_text(json.dumps({"luck_test": {"verdict": "PASS"}, "selected": {"a": 1}}))
    assert RU.refusal(tmp_path) is None
    (tmp_path / "results_unseen.json").write_text("{}")
    assert RU.refusal(tmp_path)[0] == 3


def test_gate_closed_without_audit_ok():
    assert RU.refusal(Path("/nonexistent_dir_xyz"))[0] == 2
