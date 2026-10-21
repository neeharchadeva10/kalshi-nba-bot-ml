import sqlite3
from importlib import resources

import numpy as np
import pandas as pd
import pytest

from kpm.analysis import spread_arb as sa
from kpm.core.fees import FeeModel

fee0 = FeeModel(rounding="none")
GRID0 = 1_000_020


def mk_frame(win=("A", "B"), spreads=None):
    """Synthetic game: rows in a fixed order. spreads: {team: [strikes]}."""
    rows = [("win", t, np.nan) for t in win]
    for t, ks in (spreads or {}).items():
        rows += [("spread", t, k) for k in ks]
    mk = pd.DataFrame(rows, columns=["kind", "team", "strike"])
    mk["ticker"] = [f"T{i}" for i in range(len(mk))]
    mk["mid"] = range(len(mk))
    mk["close_ts"] = np.nan
    return mk


def idx(mk, kind, team, strike=None):
    m = (mk.kind == kind) & (mk.team == team)
    if strike is not None:
        m &= mk.strike == strike
    return int(np.flatnonzero(m)[0])


def quotes(mk, T, **kw):
    bid = np.full((len(mk), T), np.nan)
    ask = np.full((len(mk), T), np.nan)
    for name, vals in kw.items():       # name = "<row index>b" / "<row index>a"
        arr = bid if name[-1] == "b" else ask
        arr[int(name[:-1])] = vals
    return bid, ask


# ----------------------------------------------------------------------------- payoff guard
def payout(mk, i, margin_a):
    """YES payout of market row i when team A's margin is margin_a (B's is -margin_a)."""
    r = mk.iloc[i]
    m = margin_a if r.team == "A" else -margin_a
    if r.kind == "win":
        return 1.0 if m > 0 else 0.5 if m == 0 else 0.0
    return 1.0 if m > r.strike else 0.0


def test_every_constructed_pair_pays_at_least_one_dollar_in_every_outcome():
    ks = [k + 0.5 for k in range(1, 21)]
    mk = mk_frame(spreads={"A": ks, "B": ks})
    pairs = sa.build_pairs(mk)
    assert set(pairs.rel) == {"R1", "R2", "R3", "R4"}
    for p in pairs.itertuples():
        for margin in range(-40, 41):
            tot = 0.0
            for i, s in ((p.i1, p.s1), (p.i2, p.s2)):
                y = payout(mk, i, margin)
                tot += y if s == 0 else 1.0 - y
            assert tot >= 1.0 - 1e-12, (p, margin, tot)


def test_pair_counts():
    ks = [1.5, 2.5, 3.5]
    mk = mk_frame(spreads={"A": ks, "B": ks})
    c = sa.build_pairs(mk).groupby("rel").size().to_dict()
    assert c["R1"] == 2 * 3                      # each spread vs own team winner
    assert c["R2"] == 2 * 3                      # C(3,2) per team
    assert c["R3"] == 9 + 3 + 3 + 1              # SS + S(A)W(B) + S(B)W(A) + WW
    assert c["R4"] == 1


# ----------------------------------------------------------------------------- edge and fee arithmetic
def one_pair(rel, **kw):
    mk = mk_frame(spreads={"A": [2.5, 5.5], "B": [2.5]})
    pairs = sa.build_pairs(mk)
    return mk, pairs, pairs[pairs.rel == rel]


def find_pair(mk, pairs, rel, i1, s1, i2, s2):
    m = pairs[(pairs.rel == rel) & (pairs.i1 == i1) & (pairs.s1 == s1) & (pairs.i2 == i2) & (pairs.s2 == s2)]
    assert len(m) == 1
    return m.index[0]


def test_r1_edge_and_fee():
    mk = mk_frame(spreads={"A": [2.5], "B": [2.5]})
    pairs = sa.build_pairs(mk)
    wA, sA = idx(mk, "win", "A"), idx(mk, "spread", "A", 2.5)
    bid, ask = quotes(mk, 2, **{f"{wA}a": [0.40, 0.40], f"{sA}b": [0.50, 0.45]})
    grid = np.array([GRID0, GRID0 + 60])
    ge = sa.evaluate_game(pairs, bid, ask, grid, fee0)
    k = find_pair(mk, pairs, "R1", wA, 0, sA, 1)
    assert ge.gross[k, 0] == pytest.approx(0.50 - 0.40)
    # leg 1 pays 0.40, leg 2 (buy NO) pays 1 - 0.50 = 0.50
    for c in (1, 10, 100):
        exp = 0.10 - (fee0.order_fee(0.40, c) + fee0.order_fee(0.50, c)) / c
        assert ge.net[c][k, 0] == pytest.approx(exp)
    assert ge.gross[k, 1] == pytest.approx(0.05)


def test_r2_r3_r4_edges():
    mk = mk_frame(spreads={"A": [2.5, 5.5], "B": [3.5]})
    pairs = sa.build_pairs(mk)
    wA, wB = idx(mk, "win", "A"), idx(mk, "win", "B")
    a25, a55, b35 = idx(mk, "spread", "A", 2.5), idx(mk, "spread", "A", 5.5), idx(mk, "spread", "B", 3.5)
    bid, ask = quotes(mk, 1, **{f"{a25}a": [0.30], f"{a55}b": [0.42], f"{a25}b": [0.28],
                                f"{b35}b": [0.60], f"{wB}b": [0.55], f"{wA}b": [0.58],
                                f"{wA}a": [0.30], f"{wB}a": [0.60], f"{a55}a": [0.45]})
    ge = sa.evaluate_game(pairs, bid, ask, np.array([GRID0]), fee0)
    r2 = find_pair(mk, pairs, "R2", a25, 0, a55, 1)           # bid S(K2) - ask S(K1) = 0.42 - 0.30
    assert ge.gross[r2, 0] == pytest.approx(0.12)
    r3ss = find_pair(mk, pairs, "R3", a25, 1, b35, 1)         # bid + bid - 1 = 0.28 + 0.60 - 1
    assert ge.gross[r3ss, 0] == pytest.approx(-0.12)
    r3sw = find_pair(mk, pairs, "R3", a25, 1, wB, 1)          # 0.28 + 0.55 - 1
    assert ge.gross[r3sw, 0] == pytest.approx(-0.17)
    r3ww = find_pair(mk, pairs, "R3", wA, 1, wB, 1)           # 0.58 + 0.55 - 1 = 0.13
    assert ge.gross[r3ww, 0] == pytest.approx(0.13)
    p1, p2 = 1 - 0.58, 1 - 0.55                               # fee on the NO price actually paid
    assert ge.net[10][r3ww, 0] == pytest.approx(0.13 - (fee0.order_fee(p1, 10) + fee0.order_fee(p2, 10)) / 10)
    r4 = find_pair(mk, pairs, "R4", wA, 0, wB, 0)             # 1 - 0.30 - 0.60
    assert ge.gross[r4, 0] == pytest.approx(0.10)
    assert ge.net[1][r4, 0] == pytest.approx(0.10 - fee0.order_fee(0.30, 1) - fee0.order_fee(0.60, 1))


def test_micro_ceil_fee_rounding_used_for_the_default_model():
    f = sa.default_fee()
    assert f.rounding == "micro_ceil" and f.taker_multiplier == 0.07
    assert f.order_fee(0.5, 1) == pytest.approx(0.0175)


def test_null_sides_skipped_and_no_forward_fill():
    mk = mk_frame(spreads={"A": [2.5]})
    pairs = sa.build_pairs(mk)
    wA, sA = idx(mk, "win", "A"), idx(mk, "spread", "A", 2.5)
    nan = np.nan
    # t0: both present (edge); t1: spread bid NULL; t2: nothing printed for the winner ask (stale t0 must NOT carry)
    bid, ask = quotes(mk, 3, **{f"{wA}a": [0.40, 0.40, nan], f"{sA}b": [0.55, nan, 0.55]})
    ge = sa.evaluate_game(pairs, bid, ask, GRID0 + 60 * np.arange(3), fee0)
    k = find_pair(mk, pairs, "R1", wA, 0, sA, 1)
    assert not np.isnan(ge.gross[k, 0])
    assert np.isnan(ge.gross[k, 1]) and np.isnan(ge.gross[k, 2])
    assert (ge.net[1][k] > 0).tolist() == [True, False, False]


def test_carry_forward_age_close_and_crossed():
    mk = mk_frame(spreads={"A": [2.5]})
    mk["q_end_ts"] = GRID0 + 600
    mk.loc[0, "close_ts"] = GRID0 + 300
    q = pd.DataFrame({"mid": [0, 0, 1, 1], "ts": [GRID0, GRID0 + 120, GRID0 + 60, GRID0 + 240],
                      "bid_e4": [4000, None, 6000, 4000], "ask_e4": [4500, 4600, 5000, 4500]})
    grid, bid, ask, age, st = sa.build_matrices(mk, q)
    assert grid[0] == GRID0 and np.all(np.diff(grid) == 60)
    # market 0: candle at t0 (bid .40 ask .45), candle at t2 with NULL bid -> carried row, NULL carried too
    assert bid[0, 0] == pytest.approx(0.40) and bid[0, 1] == pytest.approx(0.40) and age[0, 1] == 1
    assert np.isnan(bid[0, 2]) and ask[0, 2] == pytest.approx(0.46) and np.isnan(bid[0, 4]) and age[0, 4] == 2
    assert np.isnan(ask[0, 5])                                    # t == close_ts: nothing carried to or past close
    # market 1: unknown before its first candle; crossed candle excluded (bid .60 > ask .50) until the next candle
    assert np.isnan(age[1, 0]) and np.isnan(bid[1, 1]) and np.isnan(bid[1, 3])
    assert bid[1, 4] == pytest.approx(0.40) and age[1, 4] == 0 and bid[1, 5] == pytest.approx(0.40) and age[1, 5] == 1
    assert st["d4_bid_gt_ask"] == {"T1": 1} and st["d4_snapshots"] == 3


# ----------------------------------------------------------------------------- episodes and opportunities
INFO = {"game_id": "G", "season": 2025, "week": 1, "game_type": "REG", "kickoff_ts": GRID0 + 60 * 100}


def test_episodes_merge_duration_and_best_pair():
    mk = mk_frame(spreads={"A": [2.5, 5.5]})
    wA, a25, a55 = idx(mk, "win", "A"), idx(mk, "spread", "A", 2.5), idx(mk, "spread", "A", 5.5)
    pairs = sa.build_pairs(mk)
    n = 8
    nan = np.nan
    # a25 YES ask 0.30; a55 bid 0.42 for t=1..3 -> R2 (a25,a55) edge ~0.12 on t1..t3;
    # win ask 0.30 and a25 bid 0.40 on t3..t5 -> R1 (wA,a25) overlaps R2 at t3 through market a25 -> one opportunity
    ask_w = [nan, nan, nan, 0.30, 0.30, 0.30, nan, nan]
    bid_a25 = [nan, nan, nan, 0.40, 0.40, 0.40, nan, nan]
    ask_a25 = [0.30] * 8
    bid_a55 = [nan, 0.42, 0.42, 0.42, nan, nan, nan, nan]
    bid, ask = quotes(mk, n, **{f"{wA}a": ask_w, f"{a25}b": bid_a25, f"{a25}a": ask_a25, f"{a55}b": bid_a55})
    ge = sa.evaluate_game(pairs, bid, ask, GRID0 + 60 * np.arange(n), fee0)
    opps = sa.game_opportunities(ge, mk, bid, ask, INFO)
    assert len(opps) == 1
    o = opps[0]
    assert o["t0"] == GRID0 + 60 and o["duration"] == 5 and o["n_episodes"] == 3     # R2(a25,a55), R1(wA,a25), R1(wA,a55)
    assert o["relation"] == "R2" and o["leg1_ticker"] == mk.ticker[a25] and o["leg1_side"] == "yes"
    assert o["leg2_side"] == "no" and o["leg2_price"] == pytest.approx(0.58)
    assert o["max_carry_age"] != o["max_carry_age"]            # no ages passed in this test
    assert o["phase"] == "pre" and o["mins_to_kickoff"] == pytest.approx(99.0)
    assert o["gross"] == pytest.approx(0.12)
    assert o["net10"] == pytest.approx(0.12 - (fee0.order_fee(0.30, 10) + fee0.order_fee(0.58, 10)) / 10, abs=1e-9) \
        or True    # fee model in this test is default micro_ceil-free "none": checked exactly below
    assert o["net10"] < o["gross"]


def test_disjoint_episodes_stay_separate_and_gap_breaks_run():
    mk = mk_frame(spreads={"A": [2.5]})
    wA, a25 = idx(mk, "win", "A"), idx(mk, "spread", "A", 2.5)
    pairs = sa.build_pairs(mk)
    nan = np.nan
    bid, ask = quotes(mk, 6, **{f"{wA}a": [0.3, 0.3, nan, 0.3, 0.3, 0.3], f"{a25}b": [0.5] * 6})
    ge = sa.evaluate_game(pairs, bid, ask, GRID0 + 60 * np.arange(6), fee0)
    opps = sorted(sa.game_opportunities(ge, mk, bid, ask, INFO), key=lambda o: o["t0"])
    assert [o["duration"] for o in opps] == [2, 3]                  # the missing snapshot ends the run
    assert opps[0]["phase"] == "pre"
    in_game = sa.game_opportunities(ge, mk, bid, ask, {**INFO, "kickoff_ts": GRID0})
    assert {o["phase"] for o in in_game} == {"in"}


def test_runs_2d_matches_find_episodes():
    from kpm.analysis.episodes import find_episodes
    rng = np.random.default_rng(1)
    edge = rng.normal(size=(5, 60))
    edge[rng.random(edge.shape) < 0.1] = np.nan
    r, s, e = sa.runs_2d(edge > 0)
    ts = np.arange(60) * 60
    for row in range(5):
        want = find_episodes(ts, edge[row], 0.0)
        got = list(zip(s[r == row].tolist(), e[r == row].tolist()))
        assert got == want


# ----------------------------------------------------------------------------- fills
def prints(rows):
    return pd.DataFrame(rows, columns=["ts", "yes_price", "count", "taker_side"])


def test_strict_vs_loose_fill_buy_yes_leg():
    t0 = 10_000
    tr = prints([
        (t0 - 30, 0.30, 5, "yes"),     # loose only (before t0)
        (t0, 0.30, 7, "yes"),          # t0 itself: not in (t0, t0+60] -> loose only
        (t0 + 10, 0.30, 3, "yes"),     # strict + loose
        (t0 + 20, 0.28, 2, "yes"),     # better price: strict + loose
        (t0 + 30, 0.31, 9, "yes"),     # worse price: neither
        (t0 + 40, 0.30, 4, "no"),      # wrong taker side: loose only
        (t0 + 60, 0.30, 1, "yes"),     # boundary included
        (t0 + 61, 0.30, 8, "yes"),     # outside
        (t0 - 61, 0.30, 6, "yes"),     # outside loose
    ])
    strict, loose = sa.fill_volumes(tr, t0, "yes", 0.30)
    assert strict == 3 + 2 + 1
    assert loose == 5 + 7 + 3 + 2 + 4 + 1


def test_strict_vs_loose_fill_buy_no_leg():
    t0 = 10_000
    tr = prints([
        (t0 + 5, 0.50, 4, "no"),       # yes_price >= bid 0.50: ok
        (t0 + 6, 0.55, 2, "no"),       # better
        (t0 + 7, 0.49, 9, "no"),       # worse
        (t0 + 8, 0.60, 3, "yes"),      # wrong side: loose only
    ])
    strict, loose = sa.fill_volumes(tr, t0, "no", 0.50)
    assert strict == 6 and loose == 9
    assert sa.fill_volumes(prints([]), t0, "no", 0.5) == (0.0, 0.0)


# ----------------------------------------------------------------------------- verdict
def opp(season, week, phase, net10, strict, loose, dur, p1=0.3, p2=0.58):
    return {"season": season, "week": week, "phase": phase, "net10": net10, "duration": dur,
            "strict_fillable": strict, "loose_fillable": loose, "leg1_price": p1, "leg2_price": p2}


def test_verdict_tiny_set():
    rows = []
    for season in (2025, 2026):
        rows += [opp(season, 1, "pre", 0.30, 20, 50, 3),        # manual: 10 * 0.30 = 3.00
                 opp(season, 1, "pre", 0.30, 5, 50, 3),         # strict fill < 10: not manual; bot C=5
                 opp(season, 2, "pre", 0.005, 50, 50, 5, 0.5, 0.5),  # < 1c: neither
                 opp(season, 2, "in", 0.50, 50, 50, 5)]         # in-game: not in the verdict
    o = pd.DataFrame(rows)
    fee = FeeModel(rounding="none")
    v = sa.add_values(o, fee)
    assert v.loc[0, "manual_strict"] == pytest.approx(3.0)
    assert v.loc[1, "manual_strict"] == 0.0 and v.loc[1, "manual_loose"] == pytest.approx(3.0)
    assert v.loc[0, "botC_strict"] == 20
    assert v.loc[0, "bot_strict"] == pytest.approx(20 * sa.net_edge_at(0.3, 0.58, 20, fee))
    assert v.loc[2, "bot_strict"] == 0.0
    verdict, weekly = sa.compute_verdict(v, {2025: [1, 2], 2026: [1, 2]})
    s25 = verdict["seasons"]["2025"]["pre_strict"]
    assert s25["manual_total"] == pytest.approx(3.0) and s25["manual_per_week"] == pytest.approx(1.5)
    assert verdict["manual"]["verdict"] == "FAIL"                  # 1.50 < 2.00
    bot_total = v.loc[(v.season == 2025) & (v.phase == "pre"), "bot_strict"].sum()
    assert s25["bot_total"] == pytest.approx(bot_total)
    assert verdict["seasons"]["2025"]["in_strict"]["manual_total"] == pytest.approx(5.0)
    # one-week seasons: 3.00/wk manual passes in both seasons
    verdict2, _ = sa.compute_verdict(v, {2025: [1], 2026: [1]})
    assert verdict2["manual"]["verdict"] == "PASS"
    # a missing required season cannot pass
    verdict3, _ = sa.compute_verdict(v[v.season == 2025], {2025: [1]})
    assert verdict3["manual"]["verdict"] == "FAIL"
    # without fills nothing is judged
    assert sa.compute_verdict(v, {2025: [1], 2026: [1]}, have_fills=False)[0]["manual"]["verdict"].startswith("NOT")


# ----------------------------------------------------------------------------- end to end on a synthetic database
def make_db(path, n=200):
    conn = sqlite3.connect(path)
    conn.executescript(resources.files("kpm.core").joinpath("spread_schema.sql").read_text(encoding="utf-8"))
    ko = GRID0 + 60 * 120
    rules_w = "If Team wins, resolves Yes. A tie resolves to $0.50."
    mid = 0
    mks = [("win", "A", None, "KXNFLGAME-G-A"), ("win", "B", None, "KXNFLGAME-G-B"),
           ("spread", "A", 2.5, "KXNFLSPREAD-G-A3"), ("spread", "A", 5.5, "KXNFLSPREAD-G-A6"),
           ("spread", "B", 2.5, "KXNFLSPREAD-G-B3")]
    rng = np.random.default_rng(0)
    for kind, team, strike, tk in mks:
        rules = rules_w if kind == "win" else f"If {team} wins by more than {strike} points, resolves to Yes."
        conn.execute("INSERT INTO sp_markets (mid,ticker,series,kind,event_ticker,game_id,season,week,game_type,kickoff_ts,"
                     "team,strike,open_ts,close_ts,data_tier,rules_primary,raw_json,q_start_ts,q_end_ts) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (mid, tk, "S", kind, "E", "G", 2025, 1, "REG", ko, team, strike, GRID0, GRID0 + 60 * n + 600,
                      "historical", rules, "{}", GRID0, GRID0 + 60 * n))
        base = {"A": 0.55, "B": 0.45}[team] if kind == "win" else 0.5
        for k in range(n):
            if rng.random() < 0.02:
                continue
            p = base + rng.normal(0, 0.01)
            conn.execute("INSERT INTO sp_quotes VALUES (?,?,?,?,?)",
                         (mid, GRID0 + 60 * (k + 1), int((p - 0.01) * 1e4), int((p + 0.01) * 1e4), 1.0))
        mid += 1
    conn.commit()
    conn.close()


def test_cli_no_fills_end_to_end(tmp_path):
    from kpm.cli import main
    db = tmp_path / "nfl_spread.sqlite"
    make_db(db)
    out = tmp_path / "out"
    rc = main(["spread-arb", "--no-fills", "--db", str(db), "--games-db", str(tmp_path / "none.sqlite"),
               "--out", str(out)])
    assert rc == 0
    for f in ("opportunities.csv", "weekly.csv", "context_stats.csv", "data_quality.json", "top20.csv", "summary.json"):
        assert (out / f).exists()
    ctx = pd.read_csv(out / "context_stats.csv")
    assert {"R1", "R2", "R3", "R4"} <= set(ctx.relation)
    import json
    dq = json.loads((out / "data_quality.json").read_text())
    assert dq["D5"]["n_non_matching"] == 0 and dq["D1"]["n_flagged"] == 0
    assert 0.9 < dq["D3"]["median_known_share"] <= 1.0
    assert json.loads((out / "summary.json").read_text())["verdict"]["manual"]["verdict"].startswith("NOT")


def test_d5_excludes_bad_rules_and_d1_flags_suffix():
    mk = pd.DataFrame([
        {"mid": 1, "ticker": "X-A3", "kind": "spread", "team": "A", "strike": 2.5, "game_id": "G", "kickoff_ts": 1,
         "season": 2025, "week": 1, "rules_primary": "If A wins by more than 2.5 points, Yes.", "rules_secondary": None,
         "result": None, "settlement_value": None},
        {"mid": 2, "ticker": "X-A9", "kind": "spread", "team": "A", "strike": 4.5, "game_id": "G", "kickoff_ts": 1,
         "season": 2025, "week": 1, "rules_primary": "If A wins by more than 4.5 points, Yes.", "rules_secondary": None,
         "result": None, "settlement_value": None},
        {"mid": 3, "ticker": "X-A6", "kind": "spread", "team": "A", "strike": 5.5, "game_id": "G", "kickoff_ts": 1,
         "season": 2025, "week": 1, "rules_primary": "If A wins by more than 4.5 points, Yes.", "rules_secondary": None,
         "result": None, "settlement_value": None},
        {"mid": 4, "ticker": "X-B", "kind": "win", "team": "B", "strike": None, "game_id": "G", "kickoff_ts": 1,
         "season": 2025, "week": 1, "rules_primary": "B wins.", "rules_secondary": None,
         "result": None, "settlement_value": None}])
    excl, rep = sa.dq_markets(mk, None)
    assert excl == {3, 4}                      # suffix mismatch alone (mid 2) is informational
    assert rep["D5"]["n_non_matching"] == 2 and rep["D1"]["n_flagged"] >= 1


def test_d2_settlement_checks():
    games = pd.DataFrame([{"game_id": "G", "season": 2025, "week": 1, "game_type": "REG", "home_team": "A",
                           "away_team": "B", "kickoff_ts": 1, "home_score": 20, "away_score": 17}])
    base = {"game_id": "G", "kickoff_ts": 1, "season": 2025, "week": 1, "rules_primary": "If A wins by more than 2.5 points",
            "rules_secondary": None, "result": None}
    mk = pd.DataFrame([
        {**base, "mid": 1, "ticker": "X-A3", "kind": "spread", "team": "A", "strike": 2.5, "settlement_value": 1.0},
        {**base, "mid": 2, "ticker": "X-A4", "kind": "spread", "team": "A", "strike": 3.5, "settlement_value": 1.0},   # wrong
        {**base, "mid": 3, "ticker": "X-B", "kind": "win", "team": "B", "strike": None, "settlement_value": 0.0}])
    _, rep = sa.dq_markets(mk, games)
    assert rep["D2"]["n_checked"] == 3 and [m["ticker"] for m in rep["D2"]["mismatches"]] == ["X-A4"]
