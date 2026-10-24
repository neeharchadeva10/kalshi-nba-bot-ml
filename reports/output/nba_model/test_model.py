"""Tests for research log #29. Run: .venv/Scripts/python.exe -m pytest reports/output/nba_model/test_model.py"""
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import collect_props as cp  # noqa: E402
import features as F  # noqa: E402
import model as M  # noqa: E402

drift = F.drift


def test_ticker_parsing():
    assert cp.parse_pts_ticker("KXNBAPTS-26JUN13NYKSAS-SASVWEMBANYAMA1-40", "NYKSAS") == ("SAS", "VWEMBANYAMA1")
    assert cp.parse_pts_ticker("KXNBAPTS-26JUN13NYKSAS-NYKKTOWNS32-30", "NYKSAS") == ("NYK", "KTOWNS32")
    assert cp.parse_pts_ticker("KXNBAPTS-26JUN05NYKSAS-NYJKTOWNS32-30", "NYKSAS") is None  # typo'd team code -> failure
    assert cp.parse_event("KXNBAPTS-26JUN13NYKSAS") == ("KXNBAPTS", __import__("datetime").date(2026, 6, 13), "NYKSAS")
    assert cp.parse_event("garbage") is None
    assert cp.name_from_title("Victor Wembanyama: 40+") == "Victor Wembanyama"


def test_fee_is_drift_fee():
    assert math.isclose(drift.fee(0.5), 0.0175, abs_tol=1e-12)
    assert math.isclose(drift.fee(0.65), 0.01593, abs_tol=1e-12)
    assert drift.fee(0.0) == 0.0


def test_elo_update():
    # equal teams, home wins by 10: p = 1/(1+10^-0.25); winner diff = +100
    p = 1 / (1 + 10 ** (-100 / 400))
    mult = (13 ** 0.8) / (7.5 + 0.006 * 100)
    rh, ra = F.elo_update(1500, 1500, True, 10)
    assert math.isclose(rh - 1500, 20 * mult * (1 - p), rel_tol=1e-12)
    assert math.isclose(rh + ra, 3000.0)
    # road win: winner (away) diff = 1500 - 1600 = -100
    rh, ra = F.elo_update(1500, 1500, False, 10)
    mult2 = (13 ** 0.8) / (7.5 + 0.006 * (-100))
    assert math.isclose(rh - 1500, 20 * mult2 * (0 - p), rel_tol=1e-12)
    assert ra > 1500 > rh


def test_availability_tiny_example():
    # window of 4 observed games; regulars need >= 5 -> use REGULAR_MIN patched via a 10-game window built by hand
    A, B, C, D = "A", "B", "C", "D"
    window = [frozenset({A, B, C})] * 6 + [frozenset({A, B, D})] * 4   # A,B: 10; C: 6; D: 4 (not regular)
    hist = {A: [20.0] * 10, B: [10.0, 30.0] * 5, C: [8.0] * 10, D: [50.0] * 10}
    # game g lists only A and an unknown newcomer E; B and C are regulars who are absent
    miss, key, nreg = F.availability(window, {A, "E"}, set(), hist)
    assert nreg == 3
    assert math.isclose(miss, 20.0 + 8.0)       # B avg 20, C avg 8; D not a regular
    assert key == 1                              # B averages >= 15
    # a regular listed for the other team (traded) is not missing
    miss2, _, _ = F.availability(window, {A, "E"}, {B}, hist)
    assert math.isclose(miss2, 8.0)
    # rolling average uses only the last 10 played games
    hist2 = {A: [100.0] * 5 + [10.0] * 10, B: [0.0], C: [0.0]}
    miss3, _, _ = F.availability(window, set(), set(), hist2)
    assert math.isclose(miss3, 10.0)


def test_points_and_margin_helpers():
    assert F.player_game_points({9.5: "yes", 14.5: "yes", 19.5: "no"}) == (True, 15.0)
    assert F.player_game_points({9.5: "no"}) == (True, 0.0)
    assert F.player_game_points({9.5: "scalar", 14.5: "scalar"}) == (False, 0.0)
    lad = {"NYK": {1.5: "yes", 2.5: "yes", 3.5: "yes", 4.5: "no"}, "SAS": {1.5: "no", 2.5: "no"}}
    assert F.margin_from_ladders(lad, {"NYK": "4", "SAS": "4"}) == ("NYK", 4, "exp_value")
    assert F.margin_from_ladders(lad, {})[1:] == (4, "ladder_exact")
    assert F.margin_from_ladders(lad, {"NYK": "9"})[2] == "exp_value_outside_ladder_bounds"


def _games(n=60):
    pts, spread, expv = F.load_tables()
    games, _, _ = drift.load_nba()
    return sorted(games, key=lambda g: (g.tip, g.gid))[:n], pts, spread, expv


def test_no_look_ahead_real_data():
    games, pts, spread, expv = _games(400)
    full, _ = F.build_features(games, pts, spread, expv)
    for cut in (120, 250, 399):
        part, _ = F.build_features(games[:cut], pts, spread, expv)
        assert part == full[:cut]           # nothing after game `cut` changes any earlier feature
    # and a feature must not depend on the game's OWN result: flip the last game's result
    g = games[-1]
    flipped = []
    for x in g.mk:
        y = SimpleNamespace(**{s: getattr(x, s) for s in x.__slots__})
        y.result = 1.0 - x.result
        flipped.append(y)
    g2 = SimpleNamespace(gid=g.gid, tip=g.tip, season=g.season, mk=flipped, home_idx=g.home_idx)
    alt, _ = F.build_features(games[:-1] + [g2], pts, spread, expv)
    assert alt[-1] == {**full[-1], "home_win": 1.0 - full[-1]["home_win"]}


def test_same_tip_games_do_not_see_each_other():
    games, pts, spread, expv = _games(400)
    # find two games with the same tip
    by = {}
    for g in games:
        by.setdefault(g.tip, []).append(g)
    grp = next(v for v in by.values() if len(v) >= 2)
    before = [g for g in games if g.tip < grp[0].tip]
    solo, _ = F.build_features(before + [grp[0]], pts, spread, expv)
    both, _ = F.build_features(before + grp, pts, spread, expv)
    first = next(r for r in both if r["gid"] == grp[0].gid)
    assert first == solo[-1]


def test_split_identical_to_24():
    d24 = json.loads((ROOT / "reports" / "output" / "nba_drift" / "results.json").read_text())["NBA"]
    games, _, _ = drift.load_nba()
    games = sorted(games, key=lambda g: (g.tip, g.gid))
    k = drift.split_index(len(games))
    assert (len(games), k) == (d24["n_games"], d24["discovery_games"])
    from datetime import datetime, timezone
    assert datetime.fromtimestamp(games[k].tip, timezone.utc).isoformat() == d24["split_tip_utc"]


def test_logit_recovers_signal_and_bet_rule():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(4000, 2))
    y = (rng.random(4000) < 1 / (1 + np.exp(-(0.3 + 1.0 * X[:, 0])))).astype(float)
    w = M.fit_logit(X, y)
    assert abs(w[1] - 1.0) < 0.15 and abs(w[0] - 0.3) < 0.15 and abs(w[2]) < 0.15
    rows = [{"gid": "g", "home_win": 1.0, "key_missing": 0.0, "both_observed": 1.0}]
    q = {"g": (0.50, 0.55, 0.49, 0.54)}
    b = M.make_bets(rows, [0.60], q, 0.04)         # home edge .60-.50-fee(.50)=0.0825
    assert len(b) == 1 and b[0]["side"] == 0 and math.isclose(b[0]["pnl"], 1 - 0.5 - drift.fee(0.5))
    assert M.make_bets(rows, [0.52], q, 0.04) == []
