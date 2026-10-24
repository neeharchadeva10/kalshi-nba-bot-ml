"""Tests for research log #58 (el_lib, run_unseen gates).
Run: .venv/Scripts/python.exe -m pytest reports/output/nba_early_line/test_early_line.py"""
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import el_lib as E  # noqa: E402


# ------------------------------------------------------------------ helpers
def mk_series(spec):
    """spec {ticker: (strike, [(end_ts, bid, ask), ...])}"""
    out = {}
    for tk, (s, cs) in spec.items():
        cs = sorted(cs)
        out[tk] = (s, [c[0] for c in cs], [c[1] for c in cs], [c[2] for c in cs])
    return out


def toy_rec(n=80, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        for h in E.TE_HOURS:
            L = 220 + rng.normal(0, 5)
            res = "yes" if rng.random() < 0.5 else "no"
            rows.append({"event": f"G{i}", "te_h": h, "skip": None, "L_e": L,
                         "proj_F56": L + rng.choice([-7, -3, 3, 7]) + rng.normal(0, .1),
                         "proj_F42c": L + rng.choice([-7, -3, 3, 7]) + rng.normal(0, .1), "result": res,
                         "yes_bid": .48, "yes_ask": .52,
                         "net_over": E.bet_net("over", res, .48, .52), "net_under": E.bet_net("under", res, .48, .52),
                         "paid_over": E.price_paid("over", .48, .52), "paid_under": E.price_paid("under", .48, .52)})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ L_e staleness / look-ahead
def test_quotes_staleness_limit_30min():
    t = 100000
    s = mk_series({"A": (220.5, [(t - 1800, .5, .55)]), "B": (225.5, [(t - 1801, .5, .55)]), "C": (230.5, [(t, .5, .55)])})
    got = {p[0] for p in E.quotes_at(s, t)}
    assert got == {"A", "C"}                      # exactly 30 min old is allowed, 30 min + 1 s is not


def test_candle_after_te_never_used():
    t = 100000
    s = mk_series({"A": (220.5, [(t - 600, .40, .45), (t + 60, .90, .95)])})
    p = E.quotes_at(s, t)
    assert len(p) == 1 and p[0][2] == .40 and p[0][4] == t - 600
    s2 = mk_series({"A": (220.5, [(t + 1, .9, .95)])})
    assert E.quotes_at(s2, t) == []


def test_loader_never_returns_candles_after_T_minus_1h(tmp_path):
    db = tmp_path / "c.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE mkts (ticker TEXT, event TEXT, strike REAL, strike_type TEXT, result TEXT)")
    c.execute("CREATE TABLE candles (ticker TEXT, end_ts INTEGER, bid REAL, ask REAL)")
    c.execute("INSERT INTO mkts VALUES ('A','EV',220.5,'greater','yes')")
    T = 1_000_000
    for e in (T - 20000, T - 3600, T - 3599, T - 10):
        c.execute("INSERT INTO candles VALUES ('A',?,0.5,0.55)", (e,))
    c.commit()
    c.close()
    conn = E.connect_ro(db)
    series, results = E.load_series_1m(conn, "EV", T)
    assert max(series["A"][1]) == T - 3600 and results == {"A": "yes"}


def test_game_records_use_only_candles_before_te():
    T = 1_000_000
    te8 = T - 8 * 3600
    s = mk_series({"a": (220.5, [(te8 - 60, .78, .82), (te8 + 60, .10, .14)]),
                   "b": (225.5, [(te8 - 60, .48, .52)]), "c": (230.5, [(te8 - 60, .18, .22)])})
    r = E.game_records("EV", T, s, {"b": "yes", "a": "yes", "c": "no"})
    r8 = [x for x in r if x["te_h"] == 8][0]
    # mids .80/.50/.20 at the pre-t_e candles: crossing exactly at strike b (225.5); the post-t_e candle (mid .12) is ignored
    assert r8["L_e"] == pytest.approx(225.5)
    assert r8["ticker"] == "b" and r8["age_s"] == 60 and r8["skip"] is None


def test_no_early_line_is_skipped():
    T = 1_000_000
    s = mk_series({"a": (220.5, [(T - 8 * 3600 - 5000, .48, .52)])})      # older than 30 min at every t_e
    assert all(x["skip"] == "no_early_line" for x in E.game_records("EV", T, s, {"a": "yes"}))


# ------------------------------------------------------------------ main strike
def test_main_strike_nearest_half_and_filters():
    pts = [("a", 220.5, .60, .64, 1), ("b", 225.5, .46, .50, 1), ("c", 230.5, .30, .34, 1)]
    assert E.main_strike(pts)[0] == "b"
    assert E.main_strike([("x", 1, .80, .95, 1)]) is None           # spread 15c > 10c
    assert E.main_strike([("x", 1, .88, .90, 1)]) is None           # mid 89c outside 15-85c
    assert E.main_strike([("x", 1, .08, .10, 1)]) is None
    assert E.main_strike([("x", 1, .82, .86, 1)])[0] == "x"        # mid 84c inside
    assert E.main_strike([("x", 1, .10, .12, 1)]) is None          # mid 11c
    assert E.main_strike([("x", 1, np.nan, .5, 1)]) is None


def test_main_strike_tie_goes_to_lower_strike():
    pts = [("hi", 226.5, .56, .60, 1), ("lo", 222.5, .40, .44, 1)]      # mids .58/.42 -> equal distance .08
    assert E.main_strike(pts)[0] == "lo"


# ------------------------------------------------------------------ side, fee, slippage
def test_fee_and_rounding():
    assert E.fee_taker(0.5) == pytest.approx(0.0175)
    assert E.fee_taker(0.53) == pytest.approx(0.0175)                 # 0.07*.53*.47 = 0.017437 -> up to .0175
    assert E.fee_taker(0.99) == pytest.approx(0.0007)                 # 0.000693 -> up to .0007
    assert E.fee_taker(0.0) == 0.0


def test_over_buys_yes_ask_under_buys_no_via_bid_plus_slip():
    assert E.price_paid("over", .48, .52) == pytest.approx(.53)
    assert E.price_paid("under", .48, .52) == pytest.approx(.53)       # NO ask = 1 - .48 = .52, +1c
    assert E.price_paid("over", .30, .99) == .99                      # capped
    assert E.bet_net("over", "yes", .48, .52) == pytest.approx(1 - .53 - 0.0175)
    assert E.bet_net("over", "no", .48, .52) == pytest.approx(-.53 - 0.0175)
    assert E.bet_net("under", "no", .40, .44) == pytest.approx(1 - .61 - E.fee_taker(.61))


def test_formula_side():
    assert E.formula_side(230, 225) == "over" and E.formula_side(220, 225) == "under"


def test_bet_arrays_side_and_gap():
    rec = toy_rec(10)
    rec["proj_F56"] = rec.L_e + np.where(np.arange(len(rec)) % 2 == 0, 5.0, -5.0)
    b = E.bet_arrays(rec, "F56", 8, 4)
    assert len(b) == 10 and b.side.isin(["over", "under"]).all()
    assert len(E.bet_arrays(rec, "F56", 8, 6)) == 0
    r8 = rec[rec.te_h == 8].reset_index(drop=True)
    for i, row in b.reset_index(drop=True).iterrows():
        assert row.side == ("over" if r8.proj_F56[i] > r8.L_e[i] else "under")
        assert row.net == pytest.approx(r8.net_over[i] if row.side == "over" else r8.net_under[i])


# ------------------------------------------------------------------ versions / selection / luck
def test_18_versions():
    v = E.versions()
    assert len(v) == 18 and len(set(v)) == 18


def test_select_requires_n_and_takes_best_lb():
    rows = [{"n": 59, "lb": 9.0, "form": "a"}, {"n": 60, "lb": -.1, "form": "b"}, {"n": 100, "lb": -.05, "form": "c"},
            {"n": 100, "lb": None, "form": "d"}]
    assert E.select(rows)["form"] == "c"
    assert E.select([{"n": 5, "lb": 1.0}]) is None


def test_luck_test_deterministic_and_seeded():
    rec = toy_rec(120)
    a, b = E.luck_test(rec, reps=40), E.luck_test(rec, reps=40)
    assert a == b
    c = E.luck_test(rec, reps=40, seed=123)
    assert c["rep_best_lb_mean"] != a["rep_best_lb_mean"]
    assert a["verdict"] in ("PASS", "FAIL")


def test_luck_test_fails_on_noise_and_passes_on_real_edge():
    noise = toy_rec(200, seed=3)
    assert E.luck_test(noise, reps=60)["verdict"] == "FAIL"      # pure noise: random sides do as well as the best version
    rec = toy_rec(200, seed=4)
    over = rec.proj_F56 > rec.L_e                                # plant an edge: results follow the F56 side
    res = np.where(over, "yes", "no")
    rec["result"] = res
    rec["net_over"] = [E.bet_net("over", r, .48, .52) for r in res]
    rec["net_under"] = [E.bet_net("under", r, .48, .52) for r in res]
    out = E.luck_test(rec, reps=60)
    assert out["verdict"] == "PASS" and out["real_best_lb"] > 0


def test_bootstrap_is_seeded_and_game_clustered():
    ev = np.repeat([f"g{i}" for i in range(50)], 2)
    nets = np.tile([.5, -.5], 50) + np.repeat(np.linspace(-.1, .1, 50), 2)
    a, b = E.bootstrap_lb(ev, nets, draws=1000), E.bootstrap_lb(ev, nets, draws=1000)
    assert a == b and a[1] < a[0] < a[2]
    assert E.bootstrap_lb([], [])[0] is None


# ------------------------------------------------------------------ hourly check
def test_hourly_summary_excludes_gaps_over_1_5():
    df = pd.DataFrame({"event": ["a", "b", "c", "d"], "L_1m": [220., 221., 222., np.nan], "L_hourly": [220.4, 222.6, np.nan, 223.]})
    s, bad = E.hourly_summary(df)
    assert bad == ["b"] and s["both_defined"] == 2 and s["share_within_0.5"] == 0.5 and s["only_1m"] == 1 and s["only_hourly"] == 1
    assert E.hourly_summary(pd.DataFrame({"event": ["x"], "L_1m": [10.], "L_hourly": [11.5]}))[1] == []     # exactly 1.5 is kept


# ------------------------------------------------------------------ date filters
def test_discovery_cannot_load_unseen_dates():
    with pytest.raises(ValueError):
        E.load_universe("2025-10-21", "2026-01-23")
    with pytest.raises(ValueError):
        E.load_universe("2026-01-22", "2026-04-12", allow_unseen=True)


def test_universe_filters_dates_and_regular_season():
    u, info = E.load_universe("2025-10-21", "2025-10-24")
    assert u.date.min() >= "2025-10-21" and u.date.max() <= "2025-10-24" and len(u) > 5
    assert u.event.is_unique


# ------------------------------------------------------------------ refusal gates and hash checks
def make_dir(tmp_path, luck="PASS", selected=True):
    base = tmp_path
    (base / "a.py").write_text("print(1)")
    (base / "b.py").write_text("print(2)")
    files = ("a.py", "b.py", "results_discovery.json")
    (base / "results_discovery.json").write_text(json.dumps({"luck_test": {"verdict": luck},
                                                              "selected": ({"form": "F56", "te_h": 4, "gap": 4} if selected else None)}))
    (base / "AUDIT_OK").write_text(json.dumps(E.audit_hashes(base, files)))
    return base, files


def test_gates_happy_path(tmp_path):
    base, files = make_dir(tmp_path)
    assert E.unseen_gates(base, base, files)["selected"]["form"] == "F56"


def test_gate_no_audit_ok(tmp_path):
    base, files = make_dir(tmp_path)
    (base / "AUDIT_OK").unlink()
    with pytest.raises(SystemExit, match="AUDIT_OK not found"):
        E.unseen_gates(base, base, files)


def test_gate_bad_audit_json_and_missing_coverage(tmp_path):
    base, files = make_dir(tmp_path)
    (base / "AUDIT_OK").write_text("ok")
    with pytest.raises(SystemExit, match="not JSON"):
        E.unseen_gates(base, base, files)
    ok = E.audit_hashes(base, files)
    del ok["sha256"]["b.py"]
    (base / "AUDIT_OK").write_text(json.dumps(ok))
    with pytest.raises(SystemExit, match="does not cover b.py"):
        E.unseen_gates(base, base, files)


def test_gate_hash_mismatch(tmp_path):
    base, files = make_dir(tmp_path)
    (base / "a.py").write_text("print(999)")
    with pytest.raises(SystemExit, match="changed since the audit"):
        E.unseen_gates(base, base, files)


def test_gate_discovery_results_hash_is_covered(tmp_path):
    base, files = make_dir(tmp_path)
    (base / "results_discovery.json").write_text(json.dumps({"luck_test": {"verdict": "PASS"}, "selected": {"form": "F42c"}}))
    with pytest.raises(SystemExit, match="changed since the audit"):
        E.unseen_gates(base, base, files)


def test_gate_luck_fail_or_no_selection(tmp_path):
    base, files = make_dir(tmp_path, luck="FAIL")
    with pytest.raises(SystemExit, match="luck test"):
        E.unseen_gates(base, base, files)
    base2 = tmp_path / "x"
    base2.mkdir()
    b2, f2 = make_dir(base2, selected=False)
    with pytest.raises(SystemExit, match="luck test"):
        E.unseen_gates(b2, b2, f2)


@pytest.mark.parametrize("marker", ["results_unseen.json", "unseen_started.json"])
def test_gate_once_only(tmp_path, marker):
    base, files = make_dir(tmp_path)
    (base / marker).write_text("{}")
    with pytest.raises(SystemExit, match="REFUSED"):
        E.unseen_gates(base, base, files)


def test_audit_files_cover_dependencies_and_exist():
    must = {"el_lib.py", "run_unseen.py", "run_discovery.py", "checks.py", "results_discovery.json",
            "../nba_injury_speed/common.py", "../nba_ingame_overreaction/common.py", "../nba_formula_accuracy/fa_lib.py",
            "../nba_totals_grid/grid_lib.py", "../nba_totals_grid/winner_frozen.json", "../nba_formula_accuracy/frozen.json"}
    assert must <= set(E.AUDIT_FILES)
    for f in E.AUDIT_FILES:
        if f != "results_discovery.json":
            assert (HERE / f).exists(), f


def test_run_unseen_order_gate_then_marker_then_data():
    src = (HERE / "run_unseen.py").read_text()
    assert src.index("E.unseen_gates()") < src.index("(HERE / \"unseen_started.json\")") < src.index("E.load_universe")
    assert "kpm.execution" not in src and "import kpm" not in src


def test_unseen_pass_bar_logic():
    import run_unseen as R
    assert all(R.pass_bar(70, .02, .001, .03, True).values())
    assert not R.pass_bar(59, .02, .001, .03, True)["2_n_ge_60"]
    assert not R.pass_bar(70, .02, -.001, .03, True)["1_mean_pos_and_boot_lb_pos"]
    assert not R.pass_bar(70, .02, .001, .06, True)["3_coin_flip_share_le_5pct"]
    assert not R.pass_bar(70, .02, .001, .03, False)["4_discovery_luck_passed"]


# ------------------------------------------------------------------ F56 reproduction on a slice
def test_f56_reproduction_on_slice():
    u, _ = E.load_universe("2025-11-03", "2025-11-07")
    proj = E.project_games(u)
    fa, rc, gl, gr = E.load_fa()
    fz = json.loads((E.FA_DIR / "frozen.json").read_text())
    inp = rc.season_inputs("2025-26", closing=pd.Series(dtype=float))
    ref = pd.Series(np.asarray(rc.form_projection(fz, "team_raw", "2025-26", inp, None, {}), float), index=inp["games"].game_id.values)
    mine = pd.Series(proj.proj_F56.values, index=u.hoopr_id.values)
    assert len(mine) > 10 and np.isfinite(mine).all()
    assert (ref.loc[mine.index] - mine).abs().max() == 0.0
    assert np.isfinite(proj.proj_F42c).all() and 190 < proj.proj_F42c.mean() < 260
