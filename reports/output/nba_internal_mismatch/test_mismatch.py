import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import data_io as D  # noqa: E402
import mismatch as MM  # noqa: E402
import pacing  # noqa: E402
import pipeline as P  # noqa: E402


# ---------------------------------------------------------------- fees / slippage
def test_fee_rounding_up_to_1e4():
    assert MM.fee_e4(0.50) == 175 and MM.fee(0.50) == 0.0175           # 0.07*.25 = 0.0175 exactly
    assert MM.fee_e4(0.37) == 164                                      # 0.016317 -> 0.0164 (rounded UP)
    assert MM.fee_e4(0.01) == 7 and MM.fee_e4(0.99) == 7               # 0.000693 -> 0.0007
    assert MM.fee_e4(0.0) == 0 and MM.fee_e4(1.0) == 0
    with pytest.raises(ValueError):
        MM.fee_e4(0.375)


def test_slippage_and_leg_price():
    assert MM.entry_price(0.40) == 0.41
    assert MM.leg_price("yes", 0.38, 0.42) == 0.43                     # ask + 1c
    assert MM.leg_price("no", 0.38, 0.42) == 0.63                      # (1 - bid) + 1c
    # fee(.43) = ceil(.07*.43*.57*1e4 = 171.57) = 172 -> .0172
    assert MM.leg_net(0.43, True) == pytest.approx(1 - 0.43 - 0.0172)
    assert MM.leg_net(0.43, False) == pytest.approx(-0.43 - 0.0172)


def test_two_leg_team_bet_net_math():
    # D > 0 -> other_follows: NO on each team total. Home NO @ (1-0.60)+.01=.41 ; away NO @ (1-0.52)+.01=.49
    legs = [("no", 0.60, 0.64), ("no", 0.52, 0.56)]
    tot, n = MM.bet_net(legs, [False, True])        # home resolved NO (win), away resolved YES (lose)
    f1, f2 = MM.fee(0.41), MM.fee(0.49)
    assert n == 2
    assert tot == pytest.approx((1 - 0.41 - f1) + (0 - 0.49 - f2))
    assert f1 == 0.0170 and f2 == 0.0175     # ceil(.07*.41*.59=.016933)->.0170 ; .07*.49*.51=.017493->.0175


# ---------------------------------------------------------------- medians / D / sides
def test_implied_median():
    pts = [(210.5, .74, .78), (213.5, .60, .64), (216.5, .44, .48), (219.5, .30, .34)]
    # mids .76 .62 .46 .32 ; crossing between 213.5 (.62) and 216.5 (.46): 213.5 + .12/.16*3
    assert MM.implied_median(pts) == pytest.approx(213.5 + 0.12 / 0.16 * 3)
    assert MM.implied_median([(1, .9, .95)]) is None                   # never crosses (all above .5)
    assert MM.implied_median([(1, .4, .5), (2, .3, .4)]) is None       # starts below .5
    wide = [(210.5, .60, .80), (213.5, .60, .64), (216.5, .44, .48), (219.5, .30, .34)]   # first spread .20 dropped
    assert MM.implied_median(wide) == pytest.approx(213.5 + 0.12 / 0.16 * 3)


def test_main_strike():
    q = [("a", 1, .10, .14), ("b", 2, .40, .44), ("c", 3, .55, .70), ("d", 4, .46, .50)]
    assert MM.main_strike(q)[0] == "d"                   # mids .12(out of band) .42 .625(spread .15 too wide) .48
    assert MM.main_strike([("a", 1, .05, .10)]) is None  # mid .075 outside 15-85c
    assert MM.main_strike([("a", 1, .40, .52)]) is None  # spread .12 > .10


def test_d_signs_and_sides():
    assert MM.d_team(110, 108, 215) == 3
    assert MM.d_team(100, 100, 205) == -5
    assert MM.d_1h(110.0, 220.0, 0.5) == 0.0
    # game follows: D>0 -> YES on game total, D<0 -> NO   (both pairs)
    assert MM.side_for("game_follows", 2.0) == ("game", "yes")
    assert MM.side_for("game_follows", -2.0) == ("game", "no")
    # other follows: D>0 -> other book is too high -> NO on it ; D<0 -> YES
    assert MM.side_for("other_follows", 2.0) == ("other", "no")
    assert MM.side_for("other_follows", -2.0) == ("other", "yes")
    assert MM.side_for("game_follows", 0.0) is None


def test_k_fitted_on_discovery_only():
    assert MM.fit_k([0.5, 0.52, 0.54]) == pytest.approx(0.52)
    with pytest.raises(ValueError):
        MM.fit_k([])
    assert (MM.WIN_FIRST, MM.WIN_LAST) == ("2026-02-01", "2026-06-30")
    games = [{"Mg": 220.0, "M1H": 110.0, "res": {}}]
    P.set_d1h(games, 0.52)                                # the frozen discovery k is applied, not refitted
    assert games[0]["D_1H"] == pytest.approx(110.0 - 0.52 * 220.0)


# ---------------------------------------------------------------- grid / selection / luck
def test_grid_is_16():
    g = MM.grid()
    assert len(g) == 16 == len(set(g))
    assert {v[0] for v in g} == {"team", "1H"} and {v[2] for v in g} == {1, 2, 3, 4}


def test_lb_and_selection_requires_n60():
    x = np.array([0.1, 0.3, -0.2, 0.4])
    assert MM.lb(x) == pytest.approx(x.mean() - 1.96 * x.std(ddof=1) / 2)
    stats = {("a",): {"n": 59, "lb": 0.50}, ("b",): {"n": 60, "lb": 0.01}, ("c",): {"n": 90, "lb": -0.02},
             ("d",): {"n": 70, "lb": float("nan")}}
    assert MM.select_version(stats) == ("b",)             # a has the best LB but n < 60
    assert MM.select_version({("a",): {"n": 10, "lb": 1.0}}) is None


def test_luck_test_deterministic_and_sane():
    rng = np.random.default_rng(1)
    G, V = 120, 4
    ny = rng.normal(0, .5, (G, V))
    nn = rng.normal(0, .5, (G, V))
    incl = rng.random((G, V)) < 0.8
    a = MM.luck_test(ny, nn, incl, 0.05, reps=50)
    b = MM.luck_test(ny, nn, incl, 0.05, reps=50)
    assert a == b
    assert 0.0 <= a[0] <= 1.0
    assert MM.luck_test(ny, nn, incl, 99.0, reps=20)[0] == 0.0          # unreachable LB -> 0 share
    assert MM.luck_test(ny, nn, incl, -99.0, reps=20)[0] == 1.0
    assert MM.luck_test(ny, nn, np.zeros((G, V), bool), -99.0, reps=5)[0] == 0.0   # nothing eligible


def _toy_game(Mg=220.0, Mh=112.0, Ma=111.0):
    """Toy: D_team = +3. Game main strike mid .48 (bid .46 ask .50)."""
    return {"Mg": Mg, "Mh": Mh, "Ma": Ma, "D_team": Mh + Ma - Mg, "D_1H": None, "M1H": None,
            "game_main": ("G", 220.5, .46, .50), "half_main": None,
            "team_main": {"home": ("H", 112.5, .48, .52), "away": ("A", 111.5, .44, .48)},
            "res": {"G": "yes", "H": "yes", "A": "no"}}


def test_bet_mapping_end_to_end_on_toy():
    g = _toy_game()
    # D_team=+3, game_follows -> YES on game, ask .50 -> pays .51
    D_, side, net, ny, nn, nl = P.game_bets(g, "team", "game_follows")
    assert (D_, side, nl) == (3.0, "yes", 1)
    assert net == pytest.approx(1 - 0.51 - MM.fee(0.51)) and net == ny
    # other_follows -> NO on both teams: home NO pays (1-.48)+.01=.53 (home resolved YES -> lose),
    # away NO pays (1-.44)+.01=.57 (away resolved NO -> win); per-contract = mean of the legs
    D_, side, net, ny, nn, nl = P.game_bets(g, "team", "other_follows")
    assert side == "no" and nl == 2
    exp = ((0 - .53 - MM.fee(.53)) + (1 - .57 - MM.fee(.57))) / 2
    assert net == pytest.approx(exp)
    # thresholds: |D|=3 is in for >=1,2,3 and out for >=4
    stats, vl, arr, _ = P.version_table([g])
    for th, expect in ((1, 1), (2, 1), (3, 1), (4, 0)):
        assert stats[("team", "game_follows", th)]["n"] == expect
    assert stats[("1H", "game_follows", 1)]["n"] == 0                  # no 1H data -> not a bet


def test_unsettled_leg_is_skipped_and_counted():
    g = _toy_game()
    g["res"]["G"] = None
    assert P.game_bets(g, "team", "game_follows") is None
    _, _, _, skipc = P.version_table([g])
    assert any("game_follows" in k and "team" in k for k in skipc)


# ---------------------------------------------------------------- look-ahead guards
def test_pick_last_candle_never_after_T_minus_1h():
    T = 100000
    cs = [{"end_ts": T - 7200}, {"end_ts": T - 3600}, {"end_ts": T - 3599}, {"end_ts": T}]
    assert MM.pick_last_candle(cs, T)["end_ts"] == T - 3600
    assert MM.pick_last_candle([{"end_ts": T - 3599}], T) is None


def test_book_quotes_toy_db_excludes_late_candles():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE candles (ticker TEXT, end_ts INTEGER, bid REAL, ask REAL, volume REAL)")
    T = 50000
    db.executemany("INSERT INTO candles VALUES (?,?,?,?,?)",
                   [("X", T - 7200, .40, .44, 1), ("X", T - 3600, .45, .49, 1), ("X", T - 1800, .90, .95, 1),
                    ("Y", T - 1800, .50, .52, 1), ("Z", T - 3600, None, .5, 1)])
    q = D.book_quotes(db, [("X", 3.0), ("Y", 4.0), ("Z", 5.0)], T)
    assert q == [("X", 3.0, .45, .49, T - 3600)]            # Y has only a late candle; Z has no bid


def test_scope_and_tickers():
    assert MM.scope_ev("team", 220.0) == 110.0 and MM.scope_ev("1H", 220.0) == 110.0
    assert MM.in_scope(118.5, 110.5) and not MM.in_scope(118.6, 110.5) and MM.in_scope(102.5, 110.5)
    assert MM.team_of_ticker("KXNBATEAMTOTAL-26MAR10BOSSAS-BOS98") == "BOS"
    assert MM.event_suffix("KXNBATOTAL-26MAR10BOSSAS") == "26MAR10BOSSAS"
    assert MM.parse_team_expiration("Home: 105, Away: 90") == (105.0, 90.0)
    assert MM.parse_team_expiration("79") is None


def test_closer_stats_toy():
    g = {"Mg": 220.0, "Mh": 112.0, "Ma": 111.0, "M1H": 108.0, "A_game": 223.0, "A_1H": 110.0}
    c = P.closer_stats([g], 0.5)
    assert c["team"]["n"] == 1 and c["team"]["team_sum_closer"] == 1       # |223-223|=0 < |220-223|=3
    assert c["1H"]["k_x_game_total_closer"] == 1                           # |110-110|=0 < |108-110|=2


# ---------------------------------------------------------------- AIMD pacing with a fake clock
class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, d):
        self.t += d


def test_aimd_pacing_fake_clock():
    fc = FakeClock()
    p = pacing.Pacer(fc.now, fc.sleep)
    assert p.rate == pytest.approx(0.4)
    ts = []
    for _ in range(4):                             # 0.4/s -> 2.5 s spacing
        p.wait()
        ts.append(fc.t)
    assert np.diff(ts) == pytest.approx([2.5, 2.5, 2.5])
    fc.t += 60                                     # +0.1 per 60 s without a 429
    p.wait()
    assert p.rate == pytest.approx(0.5)
    fc.t += 600                                    # capped at the ceiling 1.2
    p.wait()
    assert p.rate == pytest.approx(1.2)
    pause = p.on_429("30")                         # x0.5, Retry-After honoured
    assert pause == 30.0 and p.rate == pytest.approx(0.6)
    t0 = fc.t
    p.wait()
    assert fc.t - t0 >= 30 - 1e-9
    for _ in range(4):
        p.on_429(None)
    assert p.rate == pytest.approx(0.2)            # floor
    fc.t += 59                                     # growth restarts 60 s after the last 429
    p.wait()
    assert p.rate == pytest.approx(0.2)
    fc.t += 2
    p.wait()
    assert p.rate == pytest.approx(0.3)


def test_client_refuses_trading_paths(tmp_path):
    cl = pacing.PacedClient("https://example.invalid", tmp_path)
    with pytest.raises(PermissionError):
        cl.get("/portfolio/orders")


def test_split_is_by_game_order_within_window():
    assert MM.split_by_order(5) == ["discovery", "discovery", "unseen", "unseen", "unseen"]
    assert MM.split_by_order(6) == ["discovery"] * 3 + ["unseen"] * 3
    assert MM.split_by_order(0) == []
    pop = D.load_events("all")
    if pop:                                   # real population.csv: ordered by start, discovery strictly before unseen
        Ts = [int(r["T"]) for r in pop]
        assert Ts == sorted(Ts)
        sp = [r["split"] for r in pop]
        assert sp == MM.split_by_order(len(pop))
        assert all(MM.WIN_FIRST <= r["game_date_et"] <= MM.WIN_LAST for r in pop)
        d_last = max(int(r["T"]) for r in pop if r["split"] == "discovery")
        u_first = min(int(r["T"]) for r in pop if r["split"] == "unseen")
        assert d_last <= u_first


def test_k_ignores_unseen_games():
    disc = [{"Mg": 200.0, "M1H": 100.0}, {"Mg": 200.0, "M1H": 104.0}, {"Mg": 200.0, "M1H": 102.0}]
    k = MM.fit_k([g["M1H"] / g["Mg"] for g in disc])
    unseen = [{"Mg": 200.0, "M1H": 150.0}] * 10
    assert k == pytest.approx(0.51)
    assert MM.fit_k([g["M1H"] / g["Mg"] for g in disc]) == k          # the unseen ratios are never passed in
    assert MM.fit_k([g["M1H"] / g["Mg"] for g in disc + unseen]) != k  # and would have changed it
