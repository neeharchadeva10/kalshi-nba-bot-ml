"""Unit tests for research log #61 (toy / synthetic data only; nothing here reads real outcomes)."""
import json
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ls_common as C
import ls_pipeline as P
import run_unseen as U


# ---------------------------------------------------------------- fees
def test_fee_formula_and_rounding():
    assert C.fee_taker(0.50) == pytest.approx(0.0175)
    assert C.fee_taker(0.0) == 0.0 and C.fee_taker(1.0) == 0.0
    assert C.fee_taker(0.31) == pytest.approx(0.0150)
    with pytest.raises(ValueError):
        C.fee_taker(1.2)


# ---------------------------------------------------------------- normal fit
def ladder_of(mu, sig, strikes):
    return [(k, 0.5 * (1 + math.erf(-(k - mu) / sig / math.sqrt(2)))) for k in strikes]


@pytest.mark.parametrize("mu,sig", [(225.0, 18.0), (231.3, 12.0), (218.0, 25.0)])
def test_fit_recovers_mu_sigma(mu, sig):
    pts = ladder_of(mu, sig, [mu - 15 + 3 * i + 0.5 for i in range(11)])
    f = P.fit_normal(pts)
    assert f["mu"] == pytest.approx(mu, abs=1e-3) and f["sigma"] == pytest.approx(sig, abs=1e-2)


def test_fit_with_noise_and_filters():
    rng = np.random.default_rng(0)
    pts = [(k, m + rng.normal(0, 0.01)) for k, m in ladder_of(225, 18, [210.5 + 3 * i for i in range(11)])]
    f = P.fit_normal(pts)
    assert abs(f["mu"] - 225) < 1.5 and abs(f["sigma"] - 18) < 3
    # mids at/below 0.02 or at/above 0.98 are not used; fewer than 4 usable -> None
    assert P.fit_normal([(1, 0.02), (2, 0.5), (3, 0.98), (4, 0.4), (5, 0.3)]) is None
    assert P.fit_normal([(1, 0.9), (2, 0.5), (3, 0.3), (4, 0.1)])["n"] == 4


# ---------------------------------------------------------------- ladder filters / look-ahead
D = 100_000
STR = {"A": 220.5, "B": 223.5, "C": 226.5}


def test_staleness_spread_filters():
    rows = [("A", D - 60, 0.60, 0.70),            # ok
            ("B", D - 1800, 0.50, 0.55),          # exactly 30 min old: ok
            ("C", D - 1801, 0.50, 0.55)]          # 1 s too old
    L = P.ladder_from_rows(rows, STR, D)
    assert [r["ticker"] for r in L] == ["A", "B"]
    rows = [("A", D - 60, 0.50, 0.65), ("B", D - 60, 0.50, 0.66), ("C", D - 60, 0.60, 0.55)]
    L = P.ladder_from_rows(rows, STR, D)
    assert [r["ticker"] for r in L] == ["A"]      # 0.15 spread ok, 0.16 not, crossed not
    assert P.ladder_from_rows([("A", D - 60, None, 0.5), ("B", D - 60, float("nan"), 0.5)], STR, D) == []


def test_no_lookahead_in_ladder_rows_and_sqlite():
    # a later candle with a wild quote must never be used; the last candle ending at or before D is
    rows = [("A", D - 600, 0.40, 0.45), ("A", D + 60, 0.90, 0.95), ("A", D, 0.41, 0.44)]
    L = P.ladder_from_rows(rows, STR, D)
    assert len(L) == 1 and L[0]["bid"] == 0.41 and L[0]["age"] == 0
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE candles (ticker TEXT, end_ts INTEGER, bid REAL, ask REAL)")
    con.executemany("INSERT INTO candles VALUES (?,?,?,?)", rows + [("B", D + 1, 0.5, 0.52), ("B", D - 1801, 0.5, 0.52)])
    L = P.load_ladder(con, STR, D)
    assert [r["ticker"] for r in L] == ["A"] and L[0]["bid"] == 0.41
    # the SQL itself never selects end_ts > D
    seen = []
    con.set_trace_callback(seen.append)
    P.load_ladder(con, STR, D)
    assert any("end_ts<=?" in s.replace(" ", "") or "end_ts<=" in s.replace(" ", "") for s in seen)


def test_decision_time_is_T_minus_60min():
    assert P.decision_time(1_000_000) == 1_000_000 - 3600


# ---------------------------------------------------------------- band / tail / strike choice
def mk_ladder(M_strikes, bid_ask=lambda k: (0.40, 0.45)):
    out = []
    for k in M_strikes:
        b, a = bid_ask(k)
        out.append({"ticker": f"T{k}", "K": k, "bid": b, "ask": a, "mid": (a + b) / 2, "spread": a - b, "age": 10})
    return out


def test_pick_candidate_band_tail_nearest_centre_and_ties():
    M = 225.0
    L = mk_ladder([207.5 + 3 * i for i in range(11)])      # 207.5 .. 237.5
    # band 6-10 high: K-M in [6,10): K=231.5 (6.5), 234.5 (9.5): centre 8 -> |6.5-8|=1.5 vs 1.5 tie -> lower strike
    assert P.pick_candidate(L, M, "6-10", "high")["K"] == 231.5
    # band 6-10 low: M-K in [6,10): K=219.5 (5.5 no), 216.5 (8.5 yes), 213.5 (11.5 no) -> 216.5
    assert P.pick_candidate(L, M, "6-10", "low")["K"] == 216.5
    # band 10-16 high: 237.5 (12.5) only (>=10): 234.5 is 9.5 -> no
    assert P.pick_candidate(L, M, "10-16", "high")["K"] == 237.5
    # band 10-16 low: 213.5 (11.5), 210.5 (14.5), 207.5 (17.5 no): centre 13 -> 11.5 is 1.5, 14.5 is 1.5 -> tie -> lower strike 210.5
    assert P.pick_candidate(L, M, "10-16", "low")["K"] == 210.5
    # half-open: d == 10 belongs to 10-16, not 6-10
    L2 = mk_ladder([235.0])
    assert P.pick_candidate(L2, 225.0, "6-10", "high") is None and P.pick_candidate(L2, 225.0, "10-16", "high")["K"] == 235.0
    assert P.pick_candidate(L2, 225.0, "10-16", "low") is None


def test_candidate_spread_filter_and_missing():
    L = mk_ladder([231.5], bid_ask=lambda k: (0.30, 0.41))     # spread 0.11 > 0.10 (but <= 0.15 so in the ladder)
    c = {r["band"] + r["tail"]: r for r in P.make_candidates("g", "2025-11-01", L, 225.0, {"T231.5": "yes"})}
    assert c["6-10high"]["skip"] == "spread_gt_0.10"
    assert c["6-10low"]["skip"] == "no_strike_in_band"
    c0 = P.make_candidates("g", "2025-11-01", L, None, {})
    assert all(r["skip"] == "no_M_crossing" for r in c0)
    c1 = P.make_candidates("g", "2025-11-01", mk_ladder([231.5]), 225.0, {})
    assert [r for r in c1 if r["band"] == "6-10" and r["tail"] == "high"][0]["skip"] == "no_result"


# ---------------------------------------------------------------- sides, prices, fee + slippage
def test_buy_sell_sides_and_prices():
    row = {"bid": 0.20, "ask": 0.24}
    # high tail: buy = YES at ask 0.24 (+1c); sell = NO at 1 - bid = 0.80 (+1c)
    assert P.entry(row, "high", "buy") == ("yes", 0.24, pytest.approx(0.25))
    s, ask, p = P.entry(row, "high", "sell")
    assert s == "no" and ask == pytest.approx(0.80) and p == pytest.approx(0.81)
    # low tail: buy = NO at 1 - bid ; sell = YES at ask
    s, ask, p = P.entry(row, "low", "buy")
    assert s == "no" and p == pytest.approx(0.81)
    assert P.entry(row, "low", "sell")[0] == "yes" and P.entry(row, "low", "sell")[2] == pytest.approx(0.25)


def test_skip_entry_at_or_above_099():
    assert P.entry({"bid": 0.97, "ask": 0.98}, "high", "buy") is None                 # 0.98 + 0.01 = 0.99
    assert P.entry({"bid": 0.97, "ask": 0.975}, "high", "buy") is not None             # 0.985
    assert P.entry({"bid": 0.01, "ask": 0.02}, "high", "sell") is None                 # NO at 0.99 + 0.01
    assert P.entry({"bid": 0.0, "ask": 0.05}, "high", "sell") is None                  # NO ask = 1.00


def test_net_fee_and_slippage():
    # paid 0.25 (ask 0.24 + 1c): win nets 1 - 0.25 - fee(0.25) ; loss nets -0.25 - fee
    fee = C.fee_taker(0.25)
    assert fee == pytest.approx(0.0132)                    # 0.07*.25*.75 = 0.013125 -> 0.0132
    assert P.net_of("yes", "yes", 0.25) == pytest.approx(0.75 - fee)
    assert P.net_of("yes", "no", 0.25) == pytest.approx(-0.25 - fee)
    L = mk_ladder([231.5], bid_ask=lambda k: (0.20, 0.24))
    r = [x for x in P.make_candidates("g", "d", L, 225.0, {"T231.5": "yes"}) if x["band"] == "6-10" and x["tail"] == "high"][0]
    assert r["buy_net"] == pytest.approx(0.75 - fee) and r["buy_side"] == "yes"
    # sell side: NO at 0.80+0.01 = 0.81, result yes -> loses 0.81 + fee
    assert r["sell_net"] == pytest.approx(-0.81 - C.fee_taker(0.81))
    r2 = [x for x in P.make_candidates("g", "d", L, 225.0, {"T231.5": "no"}) if x["band"] == "6-10" and x["tail"] == "high"][0]
    assert r2["sell_net"] == pytest.approx(0.19 - C.fee_taker(0.81))


# ---------------------------------------------------------------- date filters
def test_select_events_date_filter(tmp_path):
    f = tmp_path / "ev.csv"
    pd.DataFrame({"event": list("abcd"), "game_date_et": ["2025-10-20", "2025-10-21", "2026-01-22", "2026-01-23"],
                  "T": [1, 2, 3, 4]}).to_csv(f, index=False)
    got = P.select_events(C.DISC_FIRST, C.DISC_LAST, f)
    assert list(got.event) == ["b", "c"]
    got = P.select_events(C.UNSEEN_FIRST, C.UNSEEN_LAST, f)
    assert list(got.event) == ["d"]
    assert C.DISC_LAST < C.UNSEEN_FIRST


# ---------------------------------------------------------------- selection, luck, bootstrap
def fake_cands(n_games=200, edge=0.0, seed=1):
    rng = np.random.default_rng(seed)
    out = []
    for g in range(n_games):
        for band in C.BANDS:
            for tail in C.TAILS:
                nb = float(rng.normal(edge, 0.5))
                out.append({"game": f"g{g:04d}", "band": band, "tail": tail, "skip": None, "buy_net": nb, "sell_net": -nb - 0.02,
                            "buy_p": 0.25, "sell_p": 0.75})
    return out


def test_select_requires_n60_and_picks_highest_lb():
    c = fake_cands(50)
    assert P.select(P.all_version_stats(c)) is None          # n = 50 < 60
    c = fake_cands(300, edge=0.0)
    rows = P.all_version_stats(c)
    sel = P.select(rows)
    assert sel["lb"] == max(r["lb"] for r in rows)
    assert len(rows) == 8


def test_luck_deterministic_and_discriminating():
    c = fake_cands(300, edge=0.0)
    a, b = P.luck_test(c, reps=100, seed=C.SEED), P.luck_test(c, reps=100, seed=C.SEED)
    assert a == b
    assert a["verdict"] == "FAIL"                              # pure noise: ~half the reps match the best of 8
    # strong genuine edge in one cell (buy in 6-10 high wins) -> beats random directions
    c2 = fake_cands(300, edge=0.0)
    for r in c2:
        if r["band"] == "6-10" and r["tail"] == "high":
            r["buy_net"] += 0.5
            r["sell_net"] = -r["buy_net"] - 0.02
    assert P.luck_test(c2, reps=200, seed=C.SEED)["verdict"] == "PASS"
    assert P.luck_test([], reps=5)["verdict"] == "NO_ELIGIBLE_VERSION"


def test_coin_flip_and_bootstrap():
    c2 = fake_cands(300, edge=0.0)
    for r in c2:
        if r["band"] == "6-10" and r["tail"] == "high":
            r["buy_net"] += 0.5
    real = float(np.mean([x[1] for x in P.version_bets(c2, "6-10", "high", "buy")]))
    cf = P.coin_flip(c2, "6-10", "high", "buy", real, reps=200)
    assert cf["share_ge_real"] <= 0.05
    assert P.coin_flip(c2, "6-10", "high", "buy", real, reps=200) == cf
    x = {f"g{i}": [0.5 + 0.1 * ((i % 3) - 1)] for i in range(100)}
    assert P.bootstrap_lb(x, draws=500) > 0.4
    assert P.bootstrap_lb({}, draws=5) is None
    assert P.bootstrap_lb(x, draws=500) == P.bootstrap_lb(x, draws=500)


# ---------------------------------------------------------------- Part A
def test_calibration_buckets_and_sigma_z():
    L = mk_ladder([222.5, 225.5, 231.5, 240.5])
    for r, m in zip(L, (0.9, 0.55, 0.3, 0.03)):
        r["mid"] = m
    g = {"game": "g1", "M": 225.0, "ladder": L, "results": {"T222.5": "yes", "T225.5": "yes", "T231.5": "no", "T240.5": "no"}}
    rows = {r["bucket"]: r for r in P.calibration_table([g])}
    assert rows["-3..0"]["n_strikes"] == 1 and rows["-3..0"]["yes_rate"] == 1.0 and rows["-3..0"]["mean_mid"] == 0.9
    assert rows["0..3"]["n_strikes"] == 1 and rows["3..6"]["n_strikes"] == 0
    assert rows["6..9"]["n_strikes"] == 1 and rows["12..15"]["n_strikes"] == 0       # 240.5 is d=15.5: outside the table
    assert P.bucket_of(15.0) is None and P.bucket_of(-15.0) == (-15, -12) and P.bucket_of(3.0) == (3, 6)
    s = P.sigma_z_summary([{"mu": 220, "sigma": 10, "final": 230}, {"mu": 220, "sigma": 10, "final": 210},
                           {"mu": 220, "sigma": 20, "final": None}])
    assert s["n_fits"] == 3 and s["n_z"] == 2 and s["sigma"]["median"] == 10
    assert s["z"]["sd"] == pytest.approx(math.sqrt(2.0)) and s["z"]["mean"] == 0


def test_hourly_compare_and_exclusion():
    mk = lambda: sqlite3.connect(":memory:")
    m, h = mk(), mk()
    for c in (m, h):
        c.execute("CREATE TABLE candles (ticker TEXT, end_ts INTEGER, bid REAL, ask REAL)")
    T = 1_000_000
    Dd = T - 3600
    h.executemany("INSERT INTO candles VALUES (?,?,?,?)", [("A", Dd, 0.50, 0.60), ("B", Dd, 0.50, 0.60)])
    m.executemany("INSERT INTO candles VALUES (?,?,?,?)",
                  [("A", Dd - 30, 0.50, 0.60), ("A", Dd + 30, 0.9, 0.95), ("B", Dd - 60, 0.60, 0.70)])
    rows = P.hourly_compare(m, h, "EV", {"A": 220.5, "B": 223.5}, T)
    assert {r["ticker"]: round(r["diff"], 3) for r in rows} == {"A": 0.0, "B": 0.1}     # candle after the hourly end never used
    assert P.excluded_games(rows) == ["EV"]
    assert P.excluded_games(rows[:1]) == []


# ---------------------------------------------------------------- refusal gates + hashes
def _setup(tmp_path, verdict="PASS", selected=True):
    root = tmp_path / "root"
    here = root / "reports" / "output" / "nba_ladder_shape"
    here.mkdir(parents=True)
    f1, f2 = here / "code.py", here / "in.sqlite"
    f1.write_text("print(1)")
    f2.write_bytes(b"data")
    (here / "results_discovery.json").write_text(json.dumps(
        {"luck_test": {"verdict": verdict}, "selected": {"band": "6-10", "tail": "high", "dir": "buy"} if selected else None}))
    paths = [f1, f2, here / "results_discovery.json"]
    return root, here, paths


def _audit(here, root, paths, **over):
    h = {C.rel(p, root): C.sha256_file(p) for p in paths}
    h.update(over)
    (here / "AUDIT_OK").write_text(json.dumps({"sha256": h}))


def test_refuses_without_audit_ok(tmp_path):
    root, here, paths = _setup(tmp_path)
    assert U.refusal(here, root, paths)[0] == 2


def test_refuses_on_bad_audit_content_and_hash_mismatch(tmp_path):
    root, here, paths = _setup(tmp_path)
    (here / "AUDIT_OK").write_text("Opus says ok")
    assert U.refusal(here, root, paths)[0] == 4
    _audit(here, root, paths)
    assert U.refusal(here, root, paths) is None
    paths[0].write_text("print(2)")                        # code changed after the audit
    assert U.refusal(here, root, paths)[0] == 4
    _audit(here, root, paths, **{C.rel(paths[1], root): "0" * 64})
    assert U.refusal(here, root, paths)[0] == 4
    h = {C.rel(p, root): C.sha256_file(p) for p in paths[:2]}      # entry for results_discovery.json missing
    (here / "AUDIT_OK").write_text(json.dumps({"sha256": h}))
    assert U.refusal(here, root, paths)[0] == 4


def test_refuses_when_started_or_done_or_luck_failed(tmp_path):
    root, here, paths = _setup(tmp_path)
    _audit(here, root, paths)
    assert U.refusal(here, root, paths) is None
    (here / "unseen_started.json").write_text("{}")
    assert U.refusal(here, root, paths)[0] == 3
    (here / "unseen_started.json").unlink()
    (here / "results_unseen.json").write_text("{}")
    assert U.refusal(here, root, paths)[0] == 3
    (here / "results_unseen.json").unlink()
    root, here, paths = _setup(tmp_path / "b", verdict="FAIL")
    _audit(here, root, paths)
    assert U.refusal(here, root, paths)[0] == 5
    root, here, paths = _setup(tmp_path / "c", selected=False)
    _audit(here, root, paths)
    assert U.refusal(here, root, paths)[0] == 5


def test_real_folder_has_no_audit_ok_and_required_paths_listed():
    assert not (C.HERE / "AUDIT_OK").exists()               # the builder never creates it
    names = {p.name for p in C.required_paths()}
    assert {"candles.sqlite", "totals.sqlite", "ls_pipeline.py", "run_unseen.py", "ls_common.py"} <= names
    # the reused helpers loaded by path behave
    assert C.implied_total([(220.5, 0.6, 0.7), (223.5, 0.3, 0.4)]) == pytest.approx(220.5 + (0.65 - 0.5) / (0.65 - 0.35) * 3)
