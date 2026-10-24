"""Unit tests for research log #46 (toy data only)."""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C
import pipeline as P


def toy_pbp():
    rows = []
    n = 0
    base = pd.Timestamp("2026-01-01T00:00:00Z")
    for per, off in ((1, 0), (2, 40), (3, 100), (4, 140)):
        for k in range(3):
            n += 1
            rows.append({"game_play_number": n, "period_number": per, "home_score": n, "away_score": 2 * n,
                         "wallclock": (base + pd.Timedelta(minutes=off + k)).strftime("%Y-%m-%dT%H:%M:%SZ")})
    df = pd.DataFrame(rows)
    return df.sample(frac=1, random_state=1)     # unsorted on purpose


def test_checkpoints_from_pbp():
    cp = P.checkpoints_from_pbp(toy_pbp())
    t0 = int(pd.Timestamp("2026-01-01T00:00:00Z").timestamp())
    assert cp["Q1"] == t0 + 40 * 60 and cp["HT"] == t0 + 100 * 60 and cp["Q3"] == t0 + 140 * 60
    assert cp["end_q4"] == t0 + 142 * 60 and cp["game_end"] == t0 + 142 * 60
    assert cp["final_total"] == 12 + 24


def mk_ec(series, strikes, results=None):
    ser = {t: (list(ts), list(q)) for t, (ts, q) in series.items()}
    return P.EventCandles(ser, strikes, results or {})


def test_decision_candle_no_lookahead():
    ec = mk_ec({"a": ([100, 150, 159, 160, 400], [(0.4, 0.5)] * 5), "b": ([161, 500], [(0.4, 0.5)] * 2)}, {"a": 200, "b": 210})
    # C=100 -> need >= 160 : first candle ending >=160 is 160 (not 159)
    assert P.decision_time(ec, 100, 10_000) == 160
    # after the game end -> None
    assert P.decision_time(ec, 100, 159) is None
    assert P.decision_time(ec, 500, 10_000) is None
    # quotes carried forward only from candles ending <= decision time
    assert ec.at("b", 160) is None and ec.at("b", 161)[0] == 161
    assert ec.at("a", 399)[0] == 160


def test_implied_total_and_no_crossing():
    pts = [(220, 0.80, 0.84), (230, 0.55, 0.59), (240, 0.30, 0.34)]
    v = C.implied_total(pts)
    m1, m2 = 0.57, 0.32
    assert v == pytest.approx(230 + (m1 - 0.5) / (m1 - m2) * 10)
    assert C.implied_total([(220, 0.3, 0.34), (230, 0.2, 0.24)]) is None       # all below 0.5
    assert C.implied_total([(220, 0.8, 0.84), (230, 0.7, 0.74)]) is None       # all above 0.5
    assert C.implied_total([(220, 0.8, 0.99), (230, 0.2, 0.3)]) is None        # wide spread dropped / no crossing
    assert C.implied_total([(220, None, 0.5)]) is None


def test_main_strike():
    pts = [(220, 0.70, 0.74), (230, 0.48, 0.55), (240, 0.46, 0.50)]
    # 230 spread 0.07 mid .515 ; 240 mid .48 -> 230 (|.015| vs |.02|)
    assert C.main_strike(pts) == (230, 0.48, 0.55)
    assert C.main_strike([(230, 0.3, 0.5)]) is None      # spread 0.20 > 0.10


def test_fade_follow_side_mapping():
    assert C.side_for("fade", 230, 224) == "no"      # line went up -> fade buys NO
    assert C.side_for("fade", 218, 224) == "yes"
    assert C.side_for("follow", 230, 224) == "yes"
    assert C.side_for("follow", 218, 224) == "no"
    assert C.side_quotes("yes", 0.40, 0.45) == (0.45, 0.40)
    a, b = C.side_quotes("no", 0.40, 0.45)
    assert a == pytest.approx(0.60) and b == pytest.approx(0.55)


def test_fee_rounding():
    assert C.fee_taker(0.5) == 0.0175
    assert C.fee_taker(0.45) == 0.0174          # 0.07*.45*.55=0.017325 -> up to 0.0174
    assert C.fee_taker(0.0) == 0.0 and C.fee_taker(1.0) == 0.0


def test_hold_and_next_math():
    # YES at ask .45 -> pay .46, wins: 1 - .46 - fee(.46)
    f = C.fee_taker(0.46)
    assert C.net_hold("yes", "yes", 0.45) == pytest.approx(1 - 0.46 - f)
    assert C.net_hold("yes", "no", 0.45) == pytest.approx(-0.46 - f)
    assert C.net_hold("no", "no", 0.55) == pytest.approx(1 - 0.56 - C.fee_taker(0.56))
    # next: sell at bid .60 - 1c = .59 ; both fees
    assert C.net_next(0.45, 0.60) == pytest.approx(0.59 - 0.46 - f - C.fee_taker(0.59))
    rec = {"yes_bid": 0.40, "yes_ask": 0.45, "result": "yes", "xbid": 0.58, "xask": 0.62}
    assert P.bet_net(rec, "yes", "next") == (pytest.approx(C.net_next(0.45, 0.58)), False)
    # NO side exit bid = 1 - xask = .38
    ask_no = 1 - 0.40
    assert P.bet_net(rec, "no", "next")[0] == pytest.approx(C.net_next(ask_no, 1 - 0.62))
    # no exit quote -> hold fallback flagged
    rec2 = dict(rec, xbid=None, xask=None)
    assert P.bet_net(rec2, "yes", "next") == (pytest.approx(C.net_hold("yes", "yes", 0.45)), True)
    rec3 = dict(rec, xbid=0.0)
    assert P.bet_net(rec3, "yes", "next")[1] is True


def test_grid_count():
    g = C.grid()
    assert len(g) == 48 and len(set(g)) == 48


def toy_recs(n=200, seed=1):
    rng = np.random.default_rng(seed)
    recs = {}
    for i in range(n):
        for cp in C.CHECKPOINTS:
            mv = float(rng.choice([-11, -9, -7, -5, 3, 5, 7, 9, 11]))
            l0 = 225.0
            recs[(f"g{i}", cp)] = {"skip": None, "move": mv, "L0": l0, "LC": l0 + mv, "yes_bid": 0.46, "yes_ask": 0.50,
                                   "result": "yes" if rng.random() < 0.5 else "no", "xbid": 0.45, "xask": 0.49,
                                   "final": 225 + int(rng.integers(-15, 15))}
    return recs


def test_version_counts_and_selection():
    recs = toy_recs()
    rows = P.all_version_stats(recs)
    assert len(rows) == 48
    # n monotone in T
    for cp in C.CHECKPOINTS:
        ns = [next(r["n"] for r in rows if r["cp"] == cp and r["T"] == T and r["dir"] == "fade" and r["exit"] == "hold")
              for T in C.THRESHOLDS]
        assert ns == sorted(ns, reverse=True)
    sel = P.select(rows)
    assert sel["n"] >= 60 and sel["lb"] == max(r["lb"] for r in rows if r["n"] >= 60)
    # LB selection ignores small-n versions even with a huge LB
    rows2 = [dict(r) for r in rows] + [{"cp": "Q1", "T": 4, "dir": "fade", "exit": "hold", "n": 59, "lb": 99.0}]
    assert P.select(rows2)["lb"] < 99
    # fade and follow are mirror images on the same bets -> n identical
    for cp, T in [("Q1", 4), ("HT", 8)]:
        a = [r for r in rows if r["cp"] == cp and r["T"] == T and r["dir"] == "fade" and r["exit"] == "hold"][0]
        b = [r for r in rows if r["cp"] == cp and r["T"] == T and r["dir"] == "follow" and r["exit"] == "hold"][0]
        assert a["n"] == b["n"]


def test_luck_test_deterministic():
    recs = toy_recs()
    a = P.luck_test(recs, reps=30, seed=C.SEED)
    b = P.luck_test(recs, reps=30, seed=C.SEED)
    assert a == b
    assert 0.0 <= a["share_reps_ge_real"] <= 1.0
    c = P.luck_test(recs, reps=30, seed=C.SEED + 1)
    assert c["real_best_lb"] == a["real_best_lb"]


def test_calibration_slope_sign():
    recs = {}
    for i in range(50):
        mv = float(i - 25)
        # final = LC - 0.5*move  (overreaction) -> slope -0.5
        recs[(f"g{i}", "Q1")] = {"skip": None, "move": mv, "LC": 225 + mv, "final": 225 + mv - 0.5 * mv}
    cal = P.calibration(recs)
    assert cal["Q1"]["slope"] == pytest.approx(-0.5)


def test_unseen_requires_audit_ok(tmp_path):
    import run_unseen as U
    assert U.refusal(tmp_path)[0] == 2
    (tmp_path / "AUDIT_OK").write_text("x")
    assert U.refusal(tmp_path) is None
    (tmp_path / "results_unseen.json").write_text("{}")
    assert U.refusal(tmp_path)[0] == 3


def game_inputs(q1_offset_min=40, tip=1_000_000, T=None, pre_in_game=False):
    """Toy game: strikes 220/230/240; pre-tip quotes put L0 near 230; in-game candles around checkpoints."""
    strikes = {"a": 220, "b": 230, "c": 240}
    results = {"a": "yes", "b": "yes", "c": "no"}
    pre = {"a": ([tip - 60], [(0.80, 0.84)]), "b": ([tip - 60], [(0.52, 0.56)]), "c": ([tip - 60], [(0.20, 0.24)])}
    c1 = tip + q1_offset_min * 60
    cps = {"Q1": c1, "HT": c1 + 3600, "Q3": c1 + 7200, "end_q4": c1 + 9000, "game_end": c1 + 9100, "final_total": 231}
    ing = {}
    for t, (bid, ask) in {"a": (0.60, 0.64), "b": (0.30, 0.34), "c": (0.05, 0.09)}.items():
        ing[t] = ([c1 + 70, c1 + 3700, c1 + 7300, c1 + 8900], [(bid, ask)] * 4)
    return dict(event="E", date="2026-02-01", tip=tip, T=tip if T is None else T, cps=cps, pre_series=pre, ing_series=ing,
                strikes=strikes, results=results)


def test_process_game_ok_and_l0_from_pre_only():
    recs, why = P.process_game(**game_inputs())
    assert why is None
    r = recs[("E", "Q1")]
    assert r["skip"] is None and r["L0"] == pytest.approx(C.implied_total([(220, .8, .84), (230, .52, .56), (240, .2, .24)]))
    assert r["LC"] < r["L0"]            # in-game quotes dropped the line


def test_ingame_quotes_ignore_pre_tip_candles():
    # in-game candles only for strike b ; strikes a and c exist only pre-tip -> L_C must not use them (no crossing)
    kw = game_inputs()
    kw["ing_series"] = {"b": kw["ing_series"]["b"]}
    recs, why = P.process_game(**kw)
    assert recs[("E", "Q1")]["skip"] == "no_L_C_crossing"
    assert recs[("E", "Q1")]["L0"] is not None


def test_bad_wallclock_skip():
    for off in (10, 85, -5):
        assert P.process_game(**game_inputs(q1_offset_min=off)) == (None, "bad_wallclock")
    assert P.process_game(**game_inputs(q1_offset_min=20))[1] is None
    assert P.process_game(**game_inputs(q1_offset_min=80))[1] is None


def test_stale_L0_skip():
    kw = game_inputs()
    kw["T"] = kw["tip"] - 3601
    assert P.process_game(**kw) == (None, "stale_L0")
    kw["T"] = kw["tip"] - 3600
    assert P.process_game(**kw)[1] is None


def test_missing_period_skips_game():
    kw = game_inputs()
    kw["cps"] = dict(kw["cps"], HT=None)
    assert P.process_game(**kw) == (None, "pbp_missing_period")


def test_bootstrap_zero_bets():
    assert P.bootstrap_lb({}) is None
    assert P.bootstrap_lb({"g": []}) is None
    assert P.bootstrap_lb({"g%d" % i: [0.1] for i in range(5)}, draws=200) == pytest.approx(0.1)


def test_coverage_gate_logic(tmp_path, monkeypatch):
    import sqlite3
    inj = tmp_path / "inj"
    inj.mkdir()
    c = sqlite3.connect(inj / "candles.sqlite")
    c.executescript("CREATE TABLE mkts (ticker TEXT, event TEXT, strike REAL, strike_type TEXT, result TEXT);"
                    "CREATE TABLE done (ticker TEXT, event TEXT, T INTEGER, n INTEGER, note TEXT);")
    c.executemany("INSERT INTO mkts VALUES (?,?,?,?,?)", [("t1", "E1", 1, "g", "yes"), ("t2", "E1", 2, "g", "no")])
    c.executemany("INSERT INTO done VALUES (?,?,?,?,?)", [("t1", "E1", 0, 1, "ok"), ("t2", "E1", 0, 0, "http_404")])
    c.commit(); c.close()
    h = tmp_path / "here"
    h.mkdir()
    g = sqlite3.connect(h / "candles_ingame.sqlite")
    g.execute("CREATE TABLE done (ticker TEXT PRIMARY KEY, event TEXT, tip INTEGER, n INTEGER, note TEXT)")
    g.execute("INSERT INTO done VALUES ('t1','E1',0,1,'ok')")
    g.commit(); g.close()
    pd.DataFrame([{"event": "E1", "game_date_et": "2026-02-01", "hoopr_tip": 1}]).to_csv(tmp_path / "ev.csv", index=False)
    monkeypatch.setattr(C, "INJ", inj)
    monkeypatch.setattr(C, "HERE", h)
    cov = C.coverage("2026-01-23", "2026-04-12", tmp_path / "ev.csv")
    assert cov["markets"] == 2 and cov["ingame_not_final"] == 1 and cov["pre_tip_not_final"] == 0 and cov["not_downloaded"] == 1
    assert cov["pre_tip_notes"] == {"ok": 1, "http_404": 1}


def test_selected_pinned():
    assert C.SELECTED == ("Q1", 10, "follow", "hold") and C.SELECTED in C.grid()


def test_discovery_selected_unchanged():
    import json
    f = C.HERE / "results_discovery.json"
    if not f.exists():
        pytest.skip("no discovery results")
    s = json.loads(f.read_text())["selected"]
    assert (s["cp"], s["T"], s["dir"], s["exit"]) == C.SELECTED
    assert s["n"] == 62 and s["mean"] == pytest.approx(0.11464354838709676) and s["lb"] == pytest.approx(-0.003949086902905755)
