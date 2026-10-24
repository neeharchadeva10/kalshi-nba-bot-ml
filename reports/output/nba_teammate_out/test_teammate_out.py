"""Unit tests for research log #48 (teammate-out props).  Toy data only; no network, no real outcomes."""
import math
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tm_lib as L  # noqa: E402
from pacing import AIMD  # noqa: E402


# ------------------------------------------------------------------ strike selection / pre-game mean (no look-ahead)
def test_pre_game_mean_uses_only_strictly_earlier_games():
    games = [(f"2025-12-{d:02d}", float(d)) for d in range(1, 16)]          # value == day of month
    m, k = L.pre_game_mean(games, "2025-12-12")
    assert k == 10 and m == pytest.approx(sum(range(2, 12)) / 10)           # days 2..11 only
    # a same-day and a future game must change nothing
    games2 = sorted(games + [("2025-12-12", 1000.0), ("2025-12-20", 5000.0)])
    assert L.pre_game_mean(games2, "2025-12-12")[0] == m


def test_pre_game_mean_needs_5_prior_games():
    assert L.pre_game_mean([("2025-12-01", 10.0), ("2025-12-02", 20.0)], "2025-12-10")[0] is None


def test_select_strikes_two_nearest_deterministic():
    strikes = [(9.5, "a"), (14.5, "b"), (19.5, "c"), (24.5, "d"), (29.5, "e")]
    assert [t for _, t in L.select_strikes(strikes, 20.0, 2)] == ["c", "d"]       # 0.5 and 4.5
    assert [t for _, t in L.select_strikes(strikes, 17.0, 2)] == ["b", "c"]       # tie 2.5/2.5 -> lower strike first
    assert [t for _, t in L.select_strikes([(5.5, "x"), (6.5, "y")], 6.0, 1)] == ["x"]
    assert len(L.select_strikes(strikes[:1], 3.0, 2)) == 1


# ------------------------------------------------------------------ toy box
def toy_box(star_games):
    """Team AAA plays g1..g12 on 2025-12-01..12-12.  Star (id 1) plays 36 min in the games listed in star_games (indices 1..12).
    Ben (2): 30 min high usage.  Role (3): 25 min low usage.  Bench (4): 10 min."""
    b = L.Box.__new__(L.Box)
    b.names = {1: "Star One", 2: "Ben Two", 3: "Role Three", 4: "Bench Four"}
    dates = [f"2025-12-{d:02d}" for d in range(1, 13)]
    b.team_games = {"AAA": [(d, f"g{i + 1}") for i, d in enumerate(dates)]}
    b.tg_dates = {"AAA": dates}
    b.team_min = defaultdict(dict)
    b.played = defaultdict(list)
    use = {1: (20, 6, 3), 2: (18, 6, 2), 3: (8, 2, 1), 4: (2, 0, 0)}
    for i, d in enumerate(dates):
        gid = f"g{i + 1}"
        spec = {1: 36.0 if (i + 1) in star_games else 0.0, 2: 30.0, 3: 25.0, 4: 10.0}
        for aid, mn in spec.items():
            if mn > 0:
                b.team_min[("AAA", gid)][aid] = mn
                fga, fta, tov = use[aid]
                b.played[aid].append((d, gid, "AAA", mn, 10.0, 5.0, 3.0, fga, fta, tov, 2))
    b.roster = defaultdict(lambda: defaultdict(set))
    for aid, nm in b.names.items():
        b.roster[("AAA", 2026)][L.norm_name(nm)].add(aid)
    b.actual = {}
    return b


def test_window_uses_only_prior_team_games():
    b = toy_box(set(range(1, 10)) | {11})
    assert [g for _, g in b.team_window("AAA", "2025-12-12")] == [f"g{i}" for i in range(2, 12)]     # g12 (same day) excluded
    ws = b.window_stats(1, "AAA", "2025-12-12")
    assert ws["avg_min"] == pytest.approx(32.4)          # g2..g9 + g11 = 9 x 36 over 10 team games, missed g10 counts 0
    assert ws["played_last3"] == 2                       # g9 and g11 of (g9, g10, g11)
    assert b.window_stats(1, "AAA", "2025-12-01") is None          # no earlier team game


def test_star_detection_must_have_played_one_of_last_three():
    # plays g1..g9: window g2..g11 -> 8 x 36 / 10 = 28.8 min; last three (g9,g10,g11): played g9 only -> a star event at M=28
    b = toy_box(set(range(1, 10)))
    ws = b.window_stats(1, "AAA", "2025-12-12")
    assert ws["avg_min"] == pytest.approx(28.8) and ws["played_last3"] == 1
    assert L.star_flags(ws["avg_min"], ws["played_last3"], 28) and not L.star_flags(ws["avg_min"], ws["played_last3"], 32)
    # plays g1..g8: last three (g9..g11) all missed -> long-term absence, already priced -> NOT an event
    b2 = toy_box(set(range(1, 9)))
    ws2 = b2.window_stats(1, "AAA", "2025-12-12")
    assert ws2["played_last3"] == 0 and not L.star_flags(ws2["avg_min"], ws2["played_last3"], 28)


def test_star_flags_thresholds():
    assert L.star_flags(28.0, 1, 28) and not L.star_flags(27.99, 3, 28)
    assert not L.star_flags(40, 0, 28)


# ------------------------------------------------------------------ report selection: last report at/before T-1h
class FakeReports(L.Reports):
    def __init__(self, items):
        self.created = [c for c, _, _ in items]
        self.names = [n for _, n, _ in items]
        self.content = {n: {"rows": rows, "nys": []} for _, n, rows in items}
        self._idx = {}


def row(player, status, date="12/12/2025", matchup="ATL@BBB"):
    return dict(date=date, matchup=matchup, team="AtlantaHawks", player=player, status=status, time="07:00(ET)")


def test_last_report_before_decision_time_and_not_after():
    D = 1_000_000
    R = FakeReports([(D - 7200, "r1", [row("One,Star", "Questionable")]),
                     (D - 600, "r2", [row("One,Star", "Out")]),
                     (D + 60, "r3", [row("One,Star", "Available")])])        # published AFTER T-1h: must be ignored
    created, name, rows, skipped = R.last_covering(D, "12/12/2025", "ATL@BBB")
    assert name == "r2" and rows[0]["status"] == "Out" and skipped == 0
    assert R.last_covering(D - 7300, "12/12/2025", "ATL@BBB") is None        # nothing published yet
    assert R.last_covering(D, "12/13/2025", "ATL@BBB") is None               # other date


def test_report_without_the_game_falls_back_to_older_and_counts():
    D = 1_000_000
    other = [row("Z,Z", "Out", matchup="XXX@YYY")]
    R = FakeReports([(D - 7200, "r1", [row("One,Star", "Out")]), (D - 600, "r2", other)])
    created, name, rows, skipped = R.last_covering(D, "12/12/2025", "ATL@BBB")
    assert name == "r1" and skipped == 1


def test_first_out_and_late_news_flag():
    D = 1_000_000
    pn = L.norm_injury_name("One,Star")
    R = FakeReports([(D - 20000, "r0", [row("One,Star", "Questionable")]),
                     (D - 9000, "r1", [row("One,Star", "Out")]),
                     (D - 600, "r2", [row("One,Star", "Out")])])
    fo = R.first_out(D, "12/12/2025", "ATL@BBB", "ATL", pn)
    assert fo == D - 9000 and L.first_out_late(fo, D)                # first Out within 3 h before T-1h -> late news
    R2 = FakeReports([(D - 30000, "r0", [row("One,Star", "Out")]), (D - 600, "r2", [row("One,Star", "Out")])])
    fo2 = R2.first_out(D, "12/12/2025", "ATL@BBB", "ATL", pn)
    assert fo2 == D - 30000 and not L.first_out_late(fo2, D)         # old news
    R3 = FakeReports([(D + 100, "r9", [row("One,Star", "Out")])])
    assert R3.first_out(D, "12/12/2025", "ATL@BBB", "ATL", pn) is None       # a report after T-1h is invisible
    assert not L.first_out_late(None, D)
    assert L.first_out_late(D - 10799, D) and not L.first_out_late(D - 10800, D) and L.first_out_late(D, D)
    assert not L.first_out_late(D + 1, D)


def test_report_sets_out_and_doubtful_only():
    import analysis as A
    b = toy_box({1})
    rows = [row("Two,Ben", "Out"), row("Three,Role", "Doubtful"), row("Four,Bench", "Questionable"), row("Nobody,Here", "Out")]
    out, dbt, unm = A.report_sets(rows, "ATL", type("B", (), {"roster": {("ATL", 2026): b.roster[("AAA", 2026)]}})())
    assert out == {2} and dbt == {3} and unm == 1                      # Questionable is active; unknown name counted


def test_decision_candle_ignores_candles_after_decision():
    cs = [(100, 0.4, 0.5, 1), (200, 0.45, 0.55, 1), (300, 0.9, 0.95, 1)]
    assert L.decision_candle(cs, 250)[0] == 200
    assert L.decision_candle(cs, 200)[0] == 200               # ends exactly at T-1h: allowed
    assert L.decision_candle(cs, 99) is None


# ------------------------------------------------------------------ beneficiaries
def test_beneficiary_selection():
    active = [dict(aid=2, avg_min=30.0, usage_pm=0.50), dict(aid=3, avg_min=25.0, usage_pm=0.30),
              dict(aid=4, avg_min=19.9, usage_pm=0.90), dict(aid=5, avg_min=22.0, usage_pm=0.50)]
    assert [a["aid"] for a in L.pick_beneficiaries(active, "top1")] == [2]       # aid 4 is best on usage but < 20 min; 2 beats 5 on minutes
    assert [a["aid"] for a in L.pick_beneficiaries(active, "rotation")] == [2, 3, 5]
    assert L.pick_beneficiaries([dict(aid=9, avg_min=10, usage_pm=1)], "top1") == []
    assert L.pick_beneficiaries([], "rotation") == []


def test_toy_box_usage_ranking_feeds_top1():
    b = toy_box(set(range(1, 10)))
    act = []
    for aid in (2, 3, 4):
        ws = b.window_stats(aid, "AAA", "2025-12-12")
        act.append(dict(aid=aid, avg_min=ws["avg_min"], usage_pm=ws["usage_pm"]))
    assert [a["aid"] for a in L.pick_beneficiaries(act, "top1")] == [2]
    assert {a["aid"] for a in L.pick_beneficiaries(act, "rotation")} == {2, 3}   # bench averages 10 min


# ------------------------------------------------------------------ price / fee / slippage / settlement
def test_fee_rounding_up_to_1e4():
    assert L.taker_fee(0.5) == pytest.approx(0.0175)
    assert L.taker_fee(0.47) == pytest.approx(0.0175)          # 0.017437 rounds UP
    assert L.taker_fee(0.30) == pytest.approx(0.0147)          # exactly 0.0147: float noise must not push it to 0.0148
    assert L.taker_fee(0.0) == 0.0


def test_bet_net_math_win_and_loss():
    net, fee = L.bet_net(1.0, 0.50)                            # fill 0.51 -> fee 0.017493 -> 0.0175
    assert fee == pytest.approx(0.0175)
    assert net == pytest.approx(1.0 - 0.50 - 0.01 - 0.0175)
    net, _ = L.bet_net(0.0, 0.50)
    assert net == pytest.approx(-0.50 - 0.01 - 0.0175)


def test_nonbinary_settlement_value_used():
    assert L.settlement_value("yes", None) == (1.0, False)
    assert L.settlement_value("no", "0.0000") == (0.0, False)
    assert L.settlement_value("scalar", "0.0700") == (0.07, True)
    net, _ = L.bet_net(0.07, 0.40)
    assert net == pytest.approx(0.07 - 0.40 - 0.01 - L.taker_fee(0.41))
    for bad in (("scalar", None), ("scalar", ""), ("scalar", "1.5"), ("void", "0.5")):
        with pytest.raises(ValueError):
            L.settlement_value(*bad)


def test_quote_filters():
    ok = lambda bid, ask: L.classify_quote((1, bid, ask, 0))[1]      # noqa: E731
    assert ok(0.45, 0.50) == "ok"
    assert ok(0.0, 0.50) == "invalid_quote" and ok(0.5, 1.0) == "invalid_quote" and ok(0.6, 0.5) == "invalid_quote"
    assert ok(None, 0.5) == "invalid_quote"
    assert ok(0.05, 0.15) == "price_out_of_range"             # mid 0.10
    assert ok(0.80, 0.92) == "price_out_of_range"             # mid 0.86
    assert ok(0.35, 0.47) == "spread_too_wide"                # 12c spread
    assert ok(0.40, 0.50) == "ok"                             # spread exactly 10c is allowed
    assert L.classify_quote(None)[1] == "no_candle"


def test_strike_choice_nearest_half():
    mk = lambda s, t, bid, ask: dict(strike=s, ticker=t, candle=(1, bid, ask, 0))     # noqa: E731
    ch, why = L.pick_strike_quote([mk(10.5, "a", 0.55, 0.60), mk(11.5, "b", 0.40, 0.46)])    # mids .575 / .43
    assert ch["ticker"] == "b" and why == "ok" and ch["ask"] == 0.46
    ch, why = L.pick_strike_quote([mk(10.5, "a", 0.05, 0.10), mk(11.5, "b", 0.40, 0.60)])    # a out of range, b spread 20c
    assert ch is None and why == "spread_too_wide"
    ch, why = L.pick_strike_quote([mk(10.5, "a", 0.44, 0.52), mk(11.5, "b", 0.44, 0.52)])    # exact tie -> lower strike
    assert ch["ticker"] == "a"
    assert L.pick_strike_quote([dict(strike=1, ticker="x", candle=None)])[1] == "no_candle"


def test_parse_candles():
    resp = {"candlesticks": [{"end_period_ts": 100, "yes_bid": {"close": "0.4000"}, "yes_ask": {"close": "0.4600"}, "volume": "50.00"},
                             {"end_period_ts": 200, "yes_ask": {"close": "0.50"}}]}
    c = L.parse_candles(resp)
    assert c[0] == (100, 0.40, 0.46, 50.0) and c[1][1] is None
    assert L.parse_candles(None) == [] and L.parse_candles({}) == []


# ------------------------------------------------------------------ grid, LB selection
def test_grid_is_24():
    assert len(L.VERSIONS) == 24 == len(set(L.VERSIONS))
    assert set(L.VERSIONS) == {(s, m, b, t) for s in ("PTS", "REB", "AST") for m in (28, 32) for b in ("top1", "rotation")
                               for t in ("any", "late")}


def test_mean_lb_values():
    s = L.mean_lb([1.0, 3.0])
    assert s["n"] == 2 and s["mean"] == 2.0 and s["sd"] == pytest.approx(math.sqrt(2)) and s["lb"] == pytest.approx(2 - 1.96 * math.sqrt(2) / math.sqrt(2))
    assert L.mean_lb([])["lb"] is None and L.mean_lb([1.0])["lb"] is None


def test_lb_selection_requires_n_60():
    V = L.VERSIONS
    stats = {v: dict(n=100, mean=-0.1, sd=0.5, lb=-0.2) for v in V}
    stats[V[0]] = dict(n=59, mean=0.5, sd=0.1, lb=0.4)          # best LB but n < 60
    stats[V[1]] = dict(n=60, mean=0.0, sd=0.5, lb=-0.1)
    stats[V[2]] = dict(n=200, mean=0.0, sd=0.5, lb=-0.15)
    assert L.select_version(stats) == V[1]
    assert L.select_version({v: dict(n=10, mean=1, sd=1, lb=0.5) for v in V}) is None
    stats[V[3]] = dict(n=70, mean=None, sd=None, lb=None)
    assert L.select_version(stats) == V[1]


# ------------------------------------------------------------------ luck test
def toy_luck(real_edge, seed=1):
    """70 real bets, one per night; each night's pool: 5 other players at asks within 2c of the real bet."""
    rng = np.random.default_rng(seed)
    real, pool = {}, []
    for i in range(70):
        night = f"n{i}"
        ask = 0.40 + 0.01 * (i % 5)
        real[f"R{i}"] = dict(night=night, stat="PTS", ask=ask, player=1000 + i, net=real_edge + (0.5 if rng.random() < 0.5 else -0.5))
        for j in range(5):
            win = 1.0 if rng.random() < 0.5 else 0.0
            a = ask + 0.005 * (j - 2)
            pool.append(dict(key=f"P{i}_{j}", night=night, stat="PTS", ask=a, player=i * 10 + j, net=L.bet_net(win, a)[0]))
    return real, pool


def test_luck_matching_rules():
    pool = [dict(night="n1", stat="PTS", ask=0.40, player=1, net=0.0), dict(night="n1", stat="PTS", ask=0.415, player=2, net=0.0),
            dict(night="n1", stat="PTS", ask=0.44, player=3, net=0.0),             # 3c away: outside 2c
            dict(night="n1", stat="REB", ask=0.41, player=4, net=0.0),             # other stat
            dict(night="n2", stat="PTS", ask=0.41, player=5, net=0.0),             # other night
            dict(night="n1", stat="PTS", ask=0.41, player=99, net=0.0)]            # same player as the real bet
    real = {"b1": dict(night="n1", stat="PTS", ask=0.41, player=99, net=0.1),
            "b2": dict(night="n1", stat="PTS", ask=0.90, player=99, net=0.1),
            "b3": dict(night="zz", stat="PTS", ask=0.50, player=99, net=0.1)}
    cand, n_fb, n_empty = L.luck_candidates(real, pool)
    assert sorted(cand["b1"]) == [0, 1]                       # within 2c, same night/stat, not the same player
    assert cand["b2"] == [2] and n_fb == 1                    # nothing within 2c -> nearest priced (counted)
    assert cand["b3"] == [] and n_empty == 1                  # no other player that night -> keeps own net (counted)


def test_luck_test_deterministic_and_seed_sensitive():
    real, pool = toy_luck(0.0)
    cand, _, _ = L.luck_candidates(real, pool)
    pn = [p["net"] for p in pool]
    vk = {L.VERSIONS[0]: sorted(real)}
    rn = {k: r["net"] for k, r in real.items()}
    a = L.luck_test(vk, rn, cand, pn, reps=50, seed=L.SEED)
    b = L.luck_test(vk, rn, cand, pn, reps=50, seed=L.SEED)
    c = L.luck_test(vk, rn, cand, pn, reps=50, seed=L.SEED + 1)
    assert a["rep_best_lbs"] == b["rep_best_lbs"] and a["share"] == b["share"]
    assert a["rep_best_lbs"] != c["rep_best_lbs"]


def test_luck_test_separates_edge_from_noise():
    real, pool = toy_luck(0.45)                                # real bets are far better than their price-matched pool
    cand, _, _ = L.luck_candidates(real, pool)
    vk = {L.VERSIONS[0]: sorted(real)}
    res = L.luck_test(vk, {k: r["net"] for k, r in real.items()}, cand, [p["net"] for p in pool], reps=200, seed=L.SEED)
    assert res["share"] == 0.0
    real0, pool0 = toy_luck(-0.4)                              # real bets worse than pool: copies nearly always do as well
    cand0, _, _ = L.luck_candidates(real0, pool0)
    res0 = L.luck_test({L.VERSIONS[0]: sorted(real0)}, {k: r["net"] for k, r in real0.items()}, cand0, [p["net"] for p in pool0],
                       reps=200, seed=L.SEED)
    assert res0["share"] > 0.9
    # no eligible version (n < 60) -> no verdict
    res1 = L.luck_test({L.VERSIONS[0]: sorted(real)[:10]}, {k: r["net"] for k, r in real.items()}, cand, [p["net"] for p in pool], reps=10)
    assert res1["share"] is None


def test_bootstrap_by_game_deterministic():
    d = {f"g{i}": [0.1 * (i % 3), -0.05] for i in range(30)}
    assert L.bootstrap_lb(d, 500, 7) == L.bootstrap_lb(d, 500, 7)
    mean, lb = L.bootstrap_lb(d, 500, 7)
    assert lb < mean


# ------------------------------------------------------------------ pacing (AIMD) with a fake clock
class Clk:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, d):
        self.t += d


def mk_aimd():
    c = Clk()
    return AIMD(clock=c, sleep=c.sleep), c


def test_aimd_starts_at_half_a_request_per_second_and_spaces_requests():
    p, c = mk_aimd()
    ts = []
    for _ in range(5):
        p.acquire()
        ts.append(c.t)
    assert p.rate == 0.5 and [round(b - a, 6) for a, b in zip(ts, ts[1:])] == [2.0] * 4


def test_aimd_additive_increase_per_60s_without_429_and_ceiling():
    p, c = mk_aimd()
    p.acquire()
    c.t += 59
    p.acquire()
    assert p.rate == 0.5
    c.t += 2                                   # >= 60 s since start
    p.acquire()
    assert p.rate == pytest.approx(0.6)
    c.t += 60
    p.acquire()
    assert p.rate == pytest.approx(0.7)
    c.t += 100000
    p.acquire()
    assert p.rate == 1.5                       # ceiling


def test_aimd_halves_on_429_floor_and_restarts_the_minute():
    p, c = mk_aimd()
    c.t += 600
    p.acquire()                                # 0.5 + 10 x 0.1 = 1.5
    assert p.rate == pytest.approx(1.5)
    p.on_429()
    assert p.rate == pytest.approx(0.75)
    p.on_429()
    assert p.rate == pytest.approx(0.375)
    p.on_429()
    assert p.rate == pytest.approx(0.2)        # floor (0.1875 -> 0.2)
    p.on_429()
    assert p.rate == pytest.approx(0.2) and p.n_429 == 4
    c.t += 59
    p.acquire()
    assert p.rate == pytest.approx(0.2)        # the 60 s clock restarted at the last 429
    c.t += 2
    p.acquire()
    assert p.rate == pytest.approx(0.3)


def test_aimd_honours_retry_after():
    p, c = mk_aimd()
    p.acquire()
    t0 = c.t
    p.on_429(retry_after=30)
    p.acquire()
    assert c.t - t0 >= 30


# ------------------------------------------------------------------ download safety + guards
def test_download_paths_are_public_market_data_only():
    import props_download as D
    assert D.path_for("KXNBAPTS-X-Y-20") == "/historical/markets/KXNBAPTS-X-Y-20/candlesticks"
    src = (HERE / "props_download.py").read_text()
    for banned in ("portfolio", ".post(", "kpm.execution", "API_KEY", ".env", "private_key"):
        assert banned not in src.replace("No auth, no keys, no order/portfolio endpoints", "")


def test_run_unseen_refuses_without_audit_ok():
    if (HERE / "AUDIT_OK").exists():
        pytest.skip("AUDIT_OK exists")
    r = subprocess.run([sys.executable, str(HERE / "run_unseen.py")], capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "REFUSED" in (r.stdout + r.stderr)
    assert not (HERE / "results_unseen.json").exists()


def test_group_order_discovery_first():
    assert L.group_of("2025-11-18", "2026-04-12") == 0 and L.group_of("2026-01-22", "2026-04-12") == 0
    assert L.group_of("2026-01-23", "2026-04-12") == 1 and L.group_of("2026-04-12", "2026-04-12") == 1
    assert L.group_of("2026-04-14", "2026-04-12") == 2
