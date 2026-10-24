"""Unit tests for research log #44 (injury-report speed).  Run: .venv/Scripts/python.exe -m pytest reports/output/nba_injury_speed -q"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
import injury_speed as S  # noqa: E402

T0 = 1_800_000_000          # toy tip
MIN = 60


def rep(name, P, status, nys=()):
    return S.Report(name, P, status, frozenset(nys))


def resolver(mins):
    """mins: {player: minutes}; material if >= 24."""
    def f(team, player):
        m = mins.get(player)
        return (m is not None and m >= 24), m
    return f


P_OK = T0 - 3600           # one hour before tip: inside the window


# ------------------------------------------------------------------ classification / detection
def test_classify_changes():
    assert S.classify_change("Available", "Out") == "DOWN"
    assert S.classify_change("Probable", "Out") == "DOWN"
    assert S.classify_change("Questionable", "Out") == "DOWN"
    assert S.classify_change("Doubtful", "Out") == "DOWN"
    assert S.classify_change(None, "Out") == "DOWN"                 # newly listed as Out
    assert S.classify_change("Out", "Out") is None
    assert S.classify_change("Out", "Available") == "UP"
    assert S.classify_change("Doubtful", "Probable") == "UP"
    assert S.classify_change("Out", None) == "UP"                   # removed from a report that still lists the game
    assert S.classify_change("Doubtful", None) == "UP"
    assert S.classify_change("Questionable", None) is None          # removal of a Questionable is ignored
    assert S.classify_change("Available", "Questionable") is None   # Questionable ignored
    assert S.classify_change("Out", "Questionable") is None
    assert S.classify_change("Questionable", "Available") is None
    assert S.classify_change(None, "Available") is None


def test_detect_down_and_up_and_questionable_ignored():
    r1 = rep("a", P_OK - 1800, {("T1", "star"): "Available", ("T1", "q"): "Available", ("T2", "back"): "Out"})
    r2 = rep("b", P_OK, {("T1", "star"): "Out", ("T1", "q"): "Questionable", ("T2", "back"): "Out"})
    ev = S.detect_events([r1, r2], T0, resolver({"star": 30.0, "q": 30.0, "back": 30.0}))
    assert len(ev) == 1 and ev[0]["direction"] == "DOWN" and ev[0]["status"] == "ok"
    assert [p for p, _ in ev[0]["down"]] == ["star"] and ev[0]["up"] == []
    r3 = rep("c", P_OK + 600, {("T1", "star"): "Out", ("T2", "back"): "Available"})
    ev = S.detect_events([r1, r2, r3], T0, resolver({"star": 30.0, "q": 30.0, "back": 30.0}))
    assert [e["direction"] for e in ev] == ["DOWN", "UP"]
    assert ev[1]["name"] == "c"


def test_detect_newly_listed_out_and_removal():
    r1 = rep("a", P_OK - 1800, {("T1", "x"): "Out"})
    r2 = rep("b", P_OK, {("T1", "x"): "Out", ("T2", "new"): "Out"})          # newly listed Out
    ev = S.detect_events([r1, r2], T0, resolver({"x": 30.0, "new": 28.0}))
    assert len(ev) == 1 and ev[0]["direction"] == "DOWN" and ev[0]["down"][0][0] == "new"
    r3 = rep("c", P_OK + 300, {("T2", "new"): "Out"})                         # x removed while the game is still listed
    ev = S.detect_events([r1, r2, r3], T0, resolver({"x": 30.0, "new": 28.0}))
    assert ev[-1]["direction"] == "UP" and ev[-1]["up"][0][0] == "x"


def test_nys_team_is_excluded():
    r1 = rep("a", P_OK - 1800, {("T1", "x"): "Out"})
    r2 = rep("b", P_OK, {}, nys=["T1"])           # team not yet submitted: not a removal
    assert S.detect_events([r1, r2], T0, resolver({"x": 30.0})) == []


def test_mixed_direction_netting_and_tie():
    r1 = rep("a", P_OK - 600, {("T1", "d"): "Available", ("T2", "u"): "Out"})
    r2 = rep("b", P_OK, {("T1", "d"): "Out", ("T2", "u"): "Available"})
    ev = S.detect_events([r1, r2], T0, resolver({"d": 30.0, "u": 36.0}))
    assert len(ev) == 1 and ev[0]["direction"] == "UP"            # 36 up > 30 down
    ev = S.detect_events([r1, r2], T0, resolver({"d": 36.0, "u": 30.0}))
    assert ev[0]["direction"] == "DOWN"
    ev = S.detect_events([r1, r2], T0, resolver({"d": 30.0, "u": 30.0}))
    assert ev[0]["direction"] is None and ev[0]["status"] == "tie"


def test_window_edges():
    r1 = rep("a", 0, {("T1", "x"): "Available"})
    def st(P):
        r2 = rep("b", P, {("T1", "x"): "Out"})
        return S.detect_events([r1, r2], T0, resolver({"x": 30.0}))[0]["status"]
    assert st(T0 - 6 * 3600) == "ok"             # exactly tip - 6h included
    assert st(T0 - 6 * 3600 - 1) == "outside_window"
    assert st(T0 - 10 * 60) == "ok"              # exactly tip - 10 min included
    assert st(T0 - 10 * 60 + 1) == "outside_window"


def test_non_material_ignored():
    r1 = rep("a", P_OK - 600, {("T1", "x"): "Available"})
    r2 = rep("b", P_OK, {("T1", "x"): "Out"})
    assert S.detect_events([r1, r2], T0, resolver({"x": 20.0})) == []
    assert S.detect_events([r1, r2], T0, resolver({})) == []


def test_era_regex():
    assert S.ERA15_RE.search("Injury-Report_2025-12-22_05_45PM.pdf")
    assert not S.ERA15_RE.search("Injury-Report_2025-12-21_05PM.pdf")


# ------------------------------------------------------------------ material rule
def test_material_rule():
    rows = [("A", "p", f"2025-11-{d:02d}", 30.0) for d in range(1, 6)]            # 5 games at 30 min (Nov 1..5)
    rows += [("A", "short", f"2025-11-{d:02d}", 23.9) for d in range(1, 8)]
    rows += [("A", "few", f"2025-11-{d:02d}", 40.0) for d in range(1, 5)]          # only 4 games
    rows += [("A", "edge", f"2025-11-{d:02d}", 24.0) for d in range(1, 6)]
    m = S.Minutes(rows)
    assert m.material("A", "p", "2025-11-06")[0] is True
    assert m.material("A", "p", "2025-11-05")[0] is False       # only 4 games strictly before Nov 5
    assert m.material("A", "p", "2025-11-06")[1] == 30.0
    assert m.material("A", "short", "2025-11-10")[0] is False   # 23.9 < 24
    assert m.material("A", "few", "2025-11-10")[0] is False     # < 5 games
    assert m.material("A", "edge", "2025-11-06")[0] is True     # exactly 24 counts (>=)
    assert m.material("A", "nobody", "2025-11-06") == (False, None)
    # strictly earlier: a big game ON the date must not count
    m2 = S.Minutes([("A", "z", f"2025-11-{d:02d}", 10.0) for d in range(1, 6)] + [("A", "z", "2025-11-06", 48.0)])
    assert m2.avg("A", "z", "2025-11-06") == (10.0, 5)
    assert m2.material("A", "p2", "2025-11-06", threshold=30.0)[0] is False


# ------------------------------------------------------------------ implied total M
def test_implied_total_crossing_and_missing():
    pts = [(220.5, 0.70, 0.72), (225.5, 0.55, 0.57), (230.5, 0.40, 0.42), (235.5, 0.20, 0.22)]
    m = C.implied_total(pts)
    assert 225.5 < m < 230.5
    assert m == pytest.approx(225.5 + (0.56 - 0.5) / (0.56 - 0.41) * 5)
    assert C.implied_total([(220.5, 0.60, 0.62), (225.5, 0.55, 0.57)]) is None       # never crosses 0.5
    assert C.implied_total([(220.5, 0.40, 0.42)]) is None
    assert C.implied_total([]) is None
    # a wide spread (>0.15) or crossed book is dropped
    assert C.implied_total([(220.5, 0.60, 0.80), (225.5, 0.30, 0.32)]) is None
    assert C.implied_total([(220.5, 0.0, 0.62), (225.5, 0.30, 0.32)]) is None


def toy_book(shift=0.0, T=T0, extra=None):
    """3 strikes; minute candles every minute from T-3h to T-1 min; mids drift by `shift` after minute index 200."""
    strikes = {"S1": 220.5, "S2": 225.5, "S3": 230.5}
    base = {"S1": 0.70, "S2": 0.52, "S3": 0.30}
    candles = {}
    for t, mid in base.items():
        ends, bids, asks = [], [], []
        for i in range(0, 180):
            e = T - 3 * 3600 + i * MIN
            sh = shift if i >= 100 else 0.0
            ends.append(e)
            bids.append(round(mid - 0.01 + sh, 4))
            asks.append(round(mid + 0.01 + sh, 4))
        candles[t] = (ends, bids, asks)
    if extra:
        candles = extra(candles)
    return S.Book(strikes, candles, T)


def test_M_uses_only_candles_at_or_before_x():
    b = toy_book(shift=-0.1)         # from minute 100 on every mid is 0.10 lower -> M falls
    x_before = T0 - 3 * 3600 + 99 * MIN
    x_after = T0 - 3 * 3600 + 120 * MIN
    assert b.M(x_before) > b.M(x_after)
    # the candle ending exactly at x counts, the one ending a second later does not
    e100 = T0 - 3 * 3600 + 100 * MIN
    assert b.M(e100) == b.M(e100 + 59) < b.M(e100 - 1)
    assert b.M(T0 + 1) is None                   # after tip: missing


def test_M_no_crossing_is_missing():
    b = toy_book(shift=0.3)          # all mids >= 0.5 after minute 100
    assert b.M(T0 - 3 * 3600 + 150 * MIN) is None


def test_last_candle_at_or_before():
    b = toy_book()
    e = T0 - 3 * 3600 + 10 * MIN
    assert b.quote_at("S1", e)[2] == e
    assert b.quote_at("S1", e + 59)[2] == e
    assert b.quote_at("S1", e - 1)[2] == e - MIN
    assert b.quote_at("S1", T0 - 3 * 3600 - 1) is None


# ------------------------------------------------------------------ fees
def test_fee_rounding():
    assert C.fee_taker(0.50) == 0.0175
    assert C.fee_taker(0.0) == 0.0
    raw = 0.07 * 0.37 * 0.63
    assert C.fee_taker(0.37) == pytest.approx(np.ceil(raw / 0.0001 - 1e-9) * 0.0001)
    assert C.fee_taker(0.37) >= raw - 1e-12 and C.fee_taker(0.37) - raw < 0.0001
    assert C.fee_taker(0.99) == 0.0007          # 0.000693 rounds UP to 0.0007
    assert C.fee_taker(0.01) == 0.0007
    with pytest.raises(ValueError):
        C.fee_taker(1.2)


# ------------------------------------------------------------------ trade rule
def test_net_math_up_yes_and_down_no():
    # YES: buy ask 0.52, sell bid 0.60
    net, pin, pout, fin, fout = S.leg_net("YES", 0.50, 0.52, 0.60, 0.62)
    assert (pin, pout) == (0.52, 0.60)
    assert net == pytest.approx(0.60 - 0.52 - C.fee_taker(0.52) - C.fee_taker(0.60))
    # NO: buy at 1 - yes_bid(in) = 0.50, sell at 1 - yes_ask(out) = 0.38  (line moved UP against the NO holder)
    net, pin, pout, fin, fout = S.leg_net("NO", 0.50, 0.52, 0.60, 0.62)
    assert (pin, pout) == (pytest.approx(0.50), pytest.approx(0.38))
    assert net == pytest.approx(0.38 - 0.50 - C.fee_taker(0.50) - C.fee_taker(0.38))
    # NO wins when yes falls: in bid 0.50 -> buy NO 0.50 ; out ask 0.30 -> sell NO at 0.70
    net = S.leg_net("NO", 0.50, 0.52, 0.28, 0.30)[0]
    assert net == pytest.approx(0.70 - 0.50 - C.fee_taker(0.50) - C.fee_taker(0.70))


def test_trade_entry_exit_times_and_no_lookahead():
    P = T0 - 2 * 3600 + 17           # report published at an arbitrary second
    b = toy_book(shift=-0.1)
    tr = S.trade_event(b, P, "DOWN")
    assert tr["skip_reason"] is None
    assert tr["entry_end"] >= P + 120 and tr["entry_end"] - (P + 120) < MIN        # first candle at/after P+120
    assert tr["exit_end"] >= P + 1800 and tr["exit_end"] - (P + 1800) < MIN
    assert tr["side"] == "NO"
    # entry price comes from candles ending at/before the entry minute: changing LATER candles must not move it
    def later_crash(c):
        out = {}
        for t, (e, bi, a) in c.items():
            bi, a = list(bi), list(a)
            for i, ee in enumerate(e):
                if ee > tr["entry_end"]:
                    bi[i], a[i] = 0.01, 0.02
            out[t] = (e, bi, a)
        return out
    b2 = toy_book(shift=-0.1, extra=later_crash)
    tr2 = S.trade_event(b2, P, "DOWN")
    assert tr2["entry_price"] == tr["entry_price"] and tr2["entry_end"] == tr["entry_end"]


def test_exit_before_tip_when_close_to_tip():
    P = T0 - 20 * MIN                # P+30min is after tip: exit at last candle ending before tip
    b = toy_book()
    tr = S.trade_event(b, P, "UP")
    assert tr["skip_reason"] is None
    assert tr["exit_end"] == b.last_end_before_tip() < T0
    assert tr["side"] == "YES"
    # entry must also be before tip; P too close to tip -> no entry candle
    tr = S.trade_event(b, T0 - 90, "UP")
    assert tr["skip_reason"] == "no_entry_candle"


def test_skip_when_book_fails_filter():
    def wide(c):
        return {t: (e, [x - 0.2 for x in bi], a) for t, (e, bi, a) in c.items()}
    b = toy_book(extra=wide)
    assert S.trade_event(b, T0 - 2 * 3600, "UP")["skip_reason"] == "entry_book_fails_filter"


def test_main_strike_choice():
    b = toy_book()
    tk, bid, ask, _ = S.main_strike(b, T0 - 3600)
    assert tk == "S2"                # mid 0.52 is nearest 0.50
    # S1 mid 0.70 and S3 mid 0.30 are in 15-85 but farther away


def test_opposite_side_both_computed():
    b = toy_book(shift=-0.1)
    tr = S.trade_event(b, T0 - 2 * 3600, "DOWN")
    assert tr["net_no"] == tr["net"] and tr["net_yes"] == tr["net_opposite"]


# ------------------------------------------------------------------ statistics
def test_shares():
    m = {-30: 230.0, -1: 229.0, 2: 227.0, 60: 225.0}
    tot, pre, late = S.shares(m)
    assert tot == -5.0 and pre == pytest.approx(0.2) and late == pytest.approx(0.4)
    assert S.shares({-30: 230.0, -1: 229.5, 2: 229.5, 60: 230.5})[1:] == (None, None)    # |total| < 1
    assert S.shares({-30: None, 60: 225.0}) == (None, None, None)


def test_bootstrap_and_coinflip_deterministic():
    rng = np.random.default_rng(1)
    nets = rng.normal(0.01, 0.05, 200)
    games = np.repeat(np.arange(100), 2)
    a = S.boot_lb(nets, games)
    b = S.boot_lb(nets, games)
    assert a == b and a["n_draws"] == 10000 and a["n_games"] == 100
    assert a["lb_2.5"] < a["mean"] < a["ub_97.5"]
    # explicit reproduction of the seed
    uniq, inv = np.unique(games, return_inverse=True)
    G = len(uniq)
    Ssum = np.bincount(inv, weights=nets)
    Cn = np.bincount(inv).astype(float)
    r = np.random.default_rng(20261022)
    means = np.concatenate([Ssum[i].sum(1) / Cn[i].sum(1) for i in [r.integers(0, G, (1000, G)) for _ in range(10)]])
    assert a["lb_2.5"] == pytest.approx(np.quantile(means, 0.025))
    ny, nn = rng.normal(0, 0.05, 50), rng.normal(0, 0.05, 50)
    c1, c2 = S.coin_flip(ny, nn, 0.0), S.coin_flip(ny, nn, 0.0)
    assert c1 == c2 and c1["n_copies"] == 500
    assert S.coin_flip(ny, nn, 10.0)["share_as_good"] == 0.0
    assert S.coin_flip(ny, nn, -10.0)["share_as_good"] == 1.0


def test_bootstrap_resamples_games_not_events():
    # one game has all the positive nets: LB must be wide because whole games are dropped
    nets = [1.0] * 10 + [-0.1] * 10
    games = ["g1"] * 10 + ["g2"] * 5 + ["g3"] * 5
    b = S.boot_lb(nets, games)
    assert b["n_games"] == 3 and b["lb_2.5"] < 0 < b["ub_97.5"]


def test_seed_constant():
    assert C.SEED == 20261022 and S.N_BOOT == 10000 and S.N_COIN == 500
