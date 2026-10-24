"""Unit tests for research log #53 (resting orders on the #48/#49 near-misses).  Toy data only; no network; no real outcomes."""
import csv
import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nm_lib as N  # noqa: E402
import fam48 as F48  # noqa: E402
import fam49 as F49  # noqa: E402
import nm_pipeline as P  # noqa: E402


def C(end, bid=0.40, ask=0.44, low=None, high=None, vol=0.0):
    return (end, bid, ask, vol, low, high)


T = 100_000
# ----------------------------------------------------------------------------------------------- fees
def test_maker_fee_rounding_up():
    assert N.maker_fee_e4(0.50) == 44 and N.maker_fee(0.50) == 0.0044          # 0.004375 -> 0.0044
    assert N.maker_fee_e4(0.37) == 41                                           # 0.00407925 -> 0.0041
    assert N.maker_fee_e4(0.15) == 23                                           # 0.00223125 -> 0.0023
    assert N.maker_fee_e4(0.0) == 0 and N.maker_fee_e4(1.0) == 0
    with pytest.raises(ValueError):
        N.maker_fee_e4(0.375)


def test_fee_type_fallback():
    assert N.maker_fee_applies("quadratic_with_maker_fees")
    assert N.maker_fee_applies(None) and N.maker_fee_applies("")            # unknown -> charged
    assert not N.maker_fee_applies("quadratic")                              # a known maker-free type is waived
    assert N.maker_fee(0.50, None) == 0.0044
    assert N.maker_fee(0.50, "quadratic_with_maker_fees") == 0.0044
    assert N.maker_fee(0.50, "quadratic") == 0.0
    assert N.series_of("KXNBAPTS-25NOV18BOSBKN-BOSDWHITEPG-5") == "KXNBAPTS"


def test_load_fee_types_reads_cached_series_only(tmp_path):
    import gzip
    d = tmp_path / "ab"
    d.mkdir()
    with gzip.open(d / "x.json.gz", "wt") as fh:
        json.dump({"series": {"ticker": "KXFOO", "fee_type": "quadratic"}}, fh)
    ft = N.load_fee_types(tmp_path)
    assert ft == {"KXFOO": "quadratic"} and N.load_fee_types(tmp_path / "nope") == {}


def test_net_after_fill_no_slippage_and_sensitivity():
    # YES bought at 0.40, wins: 1 - 0.40 - fee(0.40) ; fee = ceil(0.0175*.4*.6*1e4=42)->0.0042
    assert N.net_after_fill(1.0, 0.40) == pytest.approx(1 - 0.40 - 0.0042)
    assert N.net_after_fill(0.0, 0.40) == pytest.approx(-0.40 - 0.0042)
    # +1c line: price paid .41, fee on .41 (0.0175*.41*.59=.00423325 -> .0043)
    assert N.net_after_fill(1.0, 0.40, None, 0.01) == pytest.approx(1 - 0.41 - 0.0043)
    # non-binary settlement valued at its value
    assert N.net_after_fill(0.35, 0.40) == pytest.approx(0.35 - 0.40 - 0.0042)
    assert N.net_after_fill(1.0, 0.40, "quadratic") == pytest.approx(0.60)    # fee waived only when known maker-free


# ----------------------------------------------------------------------------------------------- limit and fill rule
def test_limit_prices_and_range():
    assert N.limit_for("yes", 0.40, 0.44) == 0.40
    assert N.limit_for("no", 0.40, 0.44) == 0.56                              # 1 - yes_ask
    assert N.limit_ok(0.15) and N.limit_ok(0.85) and not N.limit_ok(0.14) and not N.limit_ok(0.86) and not N.limit_ok(None)
    o = N.simulate_order("yes", 0.10, 0.14, [C(1), C(2, low=0.05)], 1, T, 1.0)
    assert not o["posted"] and not o["filled"]                                # 10c is out of 15-85
    o = N.simulate_order("no", 0.10, 0.14, [C(1), C(2, high=0.99)], 1, T, 1.0)   # NO limit = 0.86 -> out of range
    assert not o["posted"]


def test_fill_yes_strict_trade_through_not_touch():
    cs = [C(1000), C(2000, low=0.40, high=0.45), C(3000, low=0.39, high=0.41)]
    assert N.is_filled("yes", 0.40, cs[:2], 1000, T) == (False, None)         # touch at the limit: no fill
    assert N.is_filled("yes", 0.40, cs, 1000, T) == (True, 3000)              # strictly through
    assert N.is_filled("yes", 0.40, [C(1000), C(2000, low=None, high=None)], 1000, T) == (False, None)   # no trade


def test_fill_no_strict_trade_through_not_touch():
    # NO limit 0.56 (yes_ask 0.44): fills only if yes price.high > 0.44
    cs = [C(1000), C(2000, low=0.40, high=0.44), C(3000, low=0.40, high=0.45)]
    assert N.is_filled("no", 0.56, cs[:2], 1000, T) == (False, None)          # touch: no
    assert N.is_filled("no", 0.56, cs, 1000, T) == (True, 3000)
    assert N.trades_through("no", 0.56, C(1, high=0.4401)) and not N.trades_through("no", 0.56, C(1, high=0.44))
    assert N.trades_through("yes", 0.40, C(1, low=0.3999)) and not N.trades_through("yes", 0.40, C(1, low=0.40))


def test_fill_only_later_candles_ending_by_T_minus_1h():
    e0 = 1000
    # a through-trade in the E0 candle itself (end <= e0) can never fill the order posted at e0's close
    assert N.is_filled("yes", 0.40, [C(1000, low=0.30)], e0, T) == (False, None)
    assert N.is_filled("yes", 0.40, [C(900, low=0.30), C(1000)], e0, T) == (False, None)
    # candle ending exactly at T-1h counts; one second later does not
    assert N.is_filled("yes", 0.40, [C(1000), C(T - 3600, low=0.30)], e0, T) == (True, T - 3600)
    assert N.is_filled("yes", 0.40, [C(1000), C(T - 3599, low=0.30)], e0, T) == (False, None)
    # order of the input list must not matter (first qualifying later candle)
    cs = [C(5000, low=0.30), C(2000, low=0.30), C(1000)]
    assert N.is_filled("yes", 0.40, cs, e0, T) == (True, 2000)


def test_simulate_order_values():
    cs = [C(1000, 0.40, 0.44), C(2000, low=0.39)]
    o = N.simulate_order("yes", 0.40, 0.44, cs, 1000, T, 1.0)
    assert o["posted"] and o["filled"] and o["limit"] == 0.40 and o["win_value"] == 1.0
    assert o["net"] == pytest.approx(1 - 0.40 - 0.0042) and o["net1c"] == pytest.approx(1 - 0.41 - 0.0043)
    cs2 = [C(1000, 0.40, 0.44), C(2000, low=0.30, high=0.50)]
    on = N.simulate_order("no", 0.40, 0.44, cs2, 1000, T, 0.0, "quadratic_with_maker_fees")   # NO @ .56, YES settled 0 -> NO wins
    assert on["filled"] and on["limit"] == 0.56 and on["win_value"] == 1.0
    assert on["net"] == pytest.approx(1 - 0.56 - N.maker_fee(0.56))
    unf = N.simulate_order("yes", 0.40, 0.44, [C(1000), C(2000, low=0.40)], 1000, T, 1.0)
    assert unf["posted"] and not unf["filled"] and unf["net"] is None and unf["win_value"] == 1.0


# ----------------------------------------------------------------------------------------------- E0 rule
def test_e0_is_earliest_candle_end_and_needs_two_hours():
    cs = [C(T - 3600 * 3), C(T - 3600 * 2), C(T - 3600)]
    assert N.e0_of(cs, T) == (T - 3 * 3600, "ok")
    assert N.e0_of([C(T - 2 * 3600), C(T - 3600)], T) == (T - 2 * 3600, "ok")            # exactly T-2h is allowed
    assert N.e0_of([C(T - 2 * 3600 + 1), C(T - 3600)], T) == (None, "no_post_window")
    assert N.e0_of([], T) == (None, "no_candles")
    assert N.e0_of(list(reversed(cs)), T)[0] == T - 3 * 3600                               # unordered input


# ----------------------------------------------------------------------------------------------- info cutoff at E0 (#48)
def _fake_reports_obj():
    L = F48.L
    r = L.Reports.__new__(L.Reports)
    r.created = [100, 200, 300, 400]
    r.names = ["a", "b", "c", "d"]
    r.content, r.missing, r._idx = {}, 0, {}
    return r


def test_reports_before_cut_at_e0():
    r = _fake_reports_obj()
    assert [c for c, _ in r.reports_before(300)] == [100, 200, 300]            # report 400 (after E0) is never visible
    assert [c for c, _ in r.reports_before(299)] == [100, 200]


def test_collect_uses_e0_not_t_minus_1h_for_reports(monkeypatch):
    """The injury-report cutoff handed to Reports.last_covering must be the market's E0 (here T-3.5h), never T-1h."""
    seen = []

    class FakeReports:
        def last_covering(self, d_ts, mdy, matchup):
            seen.append(d_ts)
            return None

    tip = 1_000_000
    e0 = tip - int(3.5 * 3600)
    cs = [C(e0, 0.40, 0.44), C(e0 + 3600, 0.40, 0.44, low=0.39), C(tip - 3600, 0.40, 0.44)]
    monkeypatch.setattr(F48, "read_ohlc", lambda t, d: cs)
    row = dict(ticker="KXNBAPTS-26JAN01AAABBB-AAAX-20", game_date="2026-01-01", game_code="AAABBB", team="AAA", player_key="X",
               aid=1, stat="PTS", floor_strike=19.5, mean10=20.0, n_prior=10, rank=1, tip=tip)

    class FakeBox:
        pass

    out = F48.collect(FakeBox(), FakeReports(), [row], "2026-01-01", "2026-01-01", {row["ticker"]: ("yes", None)}, {})
    assert seen == [e0] and (tip - 3600) not in seen
    assert out["counts"]["games_no_report"] == 1
    assert len(out["pool"]) == 1 and out["pool"][0]["filled"] and out["pool"][0]["limit"] == 0.40      # pool order simulated at E0


def test_collect_skips_market_without_post_window(monkeypatch):
    tip = 1_000_000
    cs = [C(tip - 3600 * 2 + 5, 0.40, 0.44), C(tip - 3600, 0.40, 0.44, low=0.1)]
    monkeypatch.setattr(F48, "read_ohlc", lambda t, d: cs)
    row = dict(ticker="KXNBAPTS-26JAN01AAABBB-AAAX-20", game_date="2026-01-01", game_code="AAABBB", team="AAA", player_key="X",
               aid=1, stat="PTS", floor_strike=19.5, mean10=20.0, n_prior=10, rank=1, tip=tip)

    class R:
        def last_covering(self, *a):
            raise AssertionError("must not be consulted without a post window")
    out = F48.collect(object(), R(), [row], "2026-01-01", "2026-01-01", {row["ticker"]: ("yes", None)}, {})
    assert out["counts"]["groups_no_post_window"] == 1 and out["pool"] == []


# ----------------------------------------------------------------------------------------------- #49 helpers
def test_quotes_at_uses_only_the_e0_candle():
    cs = {"a": [C(10, 0.4, 0.5), C(20, 0.1, 0.2)], "b": [C(20, 0.3, 0.4)], "c": [C(10, None, 0.5)]}
    q = F49.quotes_at([("a", 1), ("b", 2), ("c", 3)], cs, 10)
    assert q == [("a", 1, 0.4, 0.5)]                                           # b has no candle at E0, c one-sided


def test_unit_summary_mean_over_filled_legs_only():
    o1 = dict(posted=True, filled=True, net=0.5, net1c=0.49, win_value=1.0, limit=0.4, ticker="a")
    o2 = dict(posted=True, filled=False, net=None, net1c=None, win_value=0.0, limit=0.5, ticker="b")
    s = F49.unit_summary([o1, o2])
    assert s["filled"] and s["net"] == 0.5 and s["legs_posted"] == 2 and s["legs_filled"] == 1
    assert s["unfilled_win_values"] == [0.0] and s["filled_win_values"] == [1.0]
    s2 = F49.unit_summary([o2])
    assert s2["posted"] and not s2["filled"] and s2["net"] is None
    o3 = dict(posted=False, filled=False, limit=0.9, reason="limit_out_of_range", ticker="c")
    assert not F49.unit_summary([o3])["posted"]


def test_real_side_matches_49_rule():
    assert F49.real_side("game_follows", 2.0) == "yes" and F49.real_side("game_follows", -2.0) == "no"
    assert F49.real_side("other_follows", 2.0) == "no" and F49.real_side("other_follows", -2.0) == "yes"
    assert F49.real_side("other_follows", 0.0) is None


# ----------------------------------------------------------------------------------------------- split filters
def test_split_filter_48_discovery_only():
    rows = [dict(game_date=d) for d in ("2025-11-17", "2025-11-18", "2026-01-22", "2026-01-23", "2026-03-01")]
    kept = [r["game_date"] for r in N.filter_48_rows(rows)]
    assert kept == ["2025-11-18", "2026-01-22"]


def test_population_split_filter_49(tmp_path):
    p = tmp_path / "population.csv"
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["event", "game_date_et", "away", "home", "T", "season_type", "split"])
        w.writerow(["E3", "2026-03-01", "A", "B", 30, "regular", "unseen"])
        w.writerow(["E2", "2026-02-02", "A", "B", 20, "regular", "discovery"])
        w.writerow(["E1", "2026-02-01", "A", "B", 10, "regular", "discovery"])
    assert [r["event"] for r in N.read_population(p, "discovery")] == ["E1", "E2"]
    assert [r["event"] for r in N.read_population(p, "unseen")] == ["E3"]
    with pytest.raises(ValueError):
        N.read_population(p, "all")


def test_real_population_has_both_splits_and_k():
    pop = N.read_population(F49.D49 / "population.csv", "discovery")
    assert len(pop) > 100 and all(r["split"] == "discovery" for r in pop)
    assert abs(json.loads(F49.K_FILE.read_text())["k"] - 0.5113) < 5e-5


def test_forty_versions():
    assert len(F48.VERSIONS) == 24 and len(F49.VERSIONS) == 16 and len(P.ORDER) == 40 and len(set(P.ORDER)) == 40
    for v in P.ORDER:
        assert P.version_from_dict(P.version_dict(v)) == v


# ----------------------------------------------------------------------------------------------- refusal gates
def _setup_gate(tmp_path, verdict="PASS", family="48", audit=True, unseen_exists=False):
    if audit:
        (tmp_path / "AUDIT_OK").write_text("x")
    if unseen_exists:
        (tmp_path / "results_unseen.json").write_text("{}")
    sv = dict(family=family, name="v")
    (tmp_path / "results_discovery.json").write_text(json.dumps(dict(selected_version=sv, luck_test=dict(verdict=verdict))))
    return tmp_path


def test_gate_refuses_without_audit_ok(tmp_path):
    ok, msg = N.unseen_gate(_setup_gate(tmp_path, audit=False))
    assert not ok and "AUDIT_OK" in msg


def test_gate_refuses_if_unseen_results_exist(tmp_path):
    ok, msg = N.unseen_gate(_setup_gate(tmp_path, unseen_exists=True))
    assert not ok and "results_unseen" in msg


def test_gate_refuses_unless_luck_pass(tmp_path):
    ok, msg = N.unseen_gate(_setup_gate(tmp_path, verdict="FAIL"))
    assert not ok and "luck" in msg
    (tmp_path / "ok").mkdir()
    ok, msg = N.unseen_gate(_setup_gate(tmp_path / "ok", verdict="PASS"))
    assert ok


def test_gate_refuses_49_without_unseen_download(tmp_path):
    d = _setup_gate(tmp_path, family="49")
    ok, msg = N.unseen_gate(d, unseen49_exists_fn=lambda: False)
    assert not ok and "#49 unseen download" in msg
    ok, msg = N.unseen_gate(d, unseen49_exists_fn=lambda: True)
    assert ok


def test_gate_refuses_with_no_selected_version(tmp_path):
    (tmp_path / "AUDIT_OK").write_text("x")
    (tmp_path / "results_discovery.json").write_text(json.dumps(dict(selected_version=None, luck_test=dict(verdict="PASS"))))
    assert not N.unseen_gate(tmp_path)[0]


def test_real_49_unseen_download_does_not_exist_and_audit_ok_absent():
    assert N.unseen49_download_exists() is False
    assert not N.AUDIT_OK.exists()


def test_run_unseen_script_refuses_now():
    import subprocess
    r = subprocess.run([sys.executable, str(HERE / "run_unseen.py")], capture_output=True, text=True)
    assert r.returncode != 0 and "REFUSED" in (r.stderr + r.stdout)


# ----------------------------------------------------------------------------------------------- luck determinism
def _toy_inputs(n=100, seed=1):
    rng = np.random.default_rng(seed)
    bets, pool = [], []
    for i in range(n):
        lim = 0.30 + 0.01 * (i % 20)
        filled = bool(rng.random() < 0.9)
        bets.append(dict(family="48", version=None, key=f"K{i}", unit=("d", f"g{i}"), night=f"2026-01-{1 + i % 5:02d}", stat="PTS",
                         aid=i, limit=lim, filled=filled, net=float(rng.normal(0, 0.5)) if filled else None,
                         net1c=0.0, win_value=1.0, settle=1.0, nonbinary=False))
    for i in range(300):
        filled = bool(rng.random() < 0.9)
        pool.append(dict(key=f"P{i}", night=f"2026-01-{1 + i % 5:02d}", stat="PTS", limit=0.30 + 0.01 * (i % 20), player=1000 + i,
                         filled=filled, net=float(rng.normal(0, 0.5)) if filled else None, game=("d", f"p{i}")))
    return bets, pool


def test_luck_test_deterministic_and_seed_sensitive():
    bets, pool = _toy_inputs()
    v0 = F48.VERSIONS[0]
    res48 = dict(bets={v: (list(bets) if v == v0 else []) for v in F48.VERSIONS}, pool=pool)
    units = P.build_units(res48, [])
    a = P.luck_test(res48, [], units, reps=60)
    b = P.luck_test(res48, [], units, reps=60)
    c = P.luck_test(res48, [], units, reps=60, seed=7)
    assert a["rep_best_lbs"] == b["rep_best_lbs"] and a["share"] == b["share"]
    assert a["rep_best_lbs"] != c["rep_best_lbs"]
    assert a["selected"] == ("48",) + v0 and a["share"] is not None and 0.0 <= a["share"] <= 1.0


def test_luck_test_not_run_without_eligible_version():
    bets, pool = _toy_inputs(n=30)
    v0 = F48.VERSIONS[0]
    res48 = dict(bets={v: (list(bets) if v == v0 else []) for v in F48.VERSIONS}, pool=pool)
    r = P.luck_test(res48, [], P.build_units(res48, []), reps=5)
    assert r["share"] is None and r["selected"] is None            # < 60 filled bets in every version


def test_selection_uses_filled_n_only():
    bets, pool = _toy_inputs(n=100)
    for b in bets:
        b["filled"], b["net"] = False, None
    for b in bets[:59]:
        b["filled"], b["net"] = True, 0.1 + 0.01 * (hash(b["key"]) % 3)
    v0 = F48.VERSIONS[0]
    res48 = dict(bets={v: (list(bets) if v == v0 else []) for v in F48.VERSIONS}, pool=pool)
    st = P.version_table(P.build_units(res48, []))
    assert st[("48",) + v0]["signals"] == 100 and st[("48",) + v0]["n"] == 59
    assert N.select_version(st, P.ORDER) is None                       # 59 filled < 60 even though 100 signals


def test_luck_candidates_price_match_and_other_player():
    real = {"a": dict(night="n", stat="PTS", limit=0.40, player=1)}
    pool = [dict(night="n", stat="PTS", limit=0.41, player=2), dict(night="n", stat="PTS", limit=0.43, player=3),
            dict(night="n", stat="PTS", limit=0.40, player=1), dict(night="m", stat="PTS", limit=0.40, player=4)]
    cand, fb, em = N.luck_candidates(real, pool)
    assert cand["a"] == [0] and fb == 0 and em == 0
    cand, fb, em = N.luck_candidates({"a": dict(night="n", stat="REB", limit=0.4, player=1)}, pool)
    assert cand["a"] == [] and em == 1


def test_bootstrap_deterministic():
    d = {"g1": [0.1, 0.2], "g2": [-0.3], "g3": [0.05]}
    assert N.bootstrap_lb(d, 500) == N.bootstrap_lb(d, 500)


def test_parse_candle_ohlc():
    c = {"end_period_ts": 5, "volume": "3.00", "price": {"low": "0.3900", "high": "0.4100", "close": "0.4000"},
         "yes_bid": {"close": "0.3800"}, "yes_ask": {"close": "0.4200"}}
    assert N.parse_candle(c) == (5, 0.38, 0.42, 3.0, 0.39, 0.41)
    c2 = {"end_period_ts": 6, "price": {"low": None, "high": None}, "yes_bid": {"close": None}, "yes_ask": {"close": "0.9900"}}
    assert N.parse_candle(c2) == (6, None, 0.99, 0.0, None, None)
