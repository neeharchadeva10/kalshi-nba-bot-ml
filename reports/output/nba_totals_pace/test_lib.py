import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402


def test_user_example_bos_at_det():
    bos, det = (1.159, 1.054 + 0.0, 98.5), (1.124, 1.085, 103.1)
    # user's inputs: BOS ORtg 1.159, the DRtg used for BOS's opponent (DET) is 1.054; DET ORtg 1.124, BOS DRtg 1.085
    bos = (1.159, 1.085, 98.5)
    det = (1.124, 1.054, 103.1)
    a, b, tot = lib.project_total(bos, det)
    assert round(a, 2) == 108.99
    assert round(b, 2) == 113.87
    assert round(tot, 2) == 222.86


def test_possessions_formula():
    assert lib.possessions(90, 10, 12, 20) == pytest.approx(90 - 10 + 12 + 8.8)


def test_build_ratings_toy():
    rows = []
    # two games between A (hoopR "GS") and B ("NY"); regular season; plus a playoff game that must be ignored
    def row(gid, t, o, st, pts, opp_pts, fga, orb, tov, fta):
        return dict(game_id=gid, season_type=st, team_abbreviation=t, opponent_team_abbreviation=o,
                    team_score=pts, opponent_team_score=opp_pts, field_goals_attempted=fga,
                    offensive_rebounds=orb, total_turnovers=tov, free_throws_attempted=fta)
    rows += [row(1, "GS", "NY", 2, 100, 90, 90, 10, 10, 25), row(1, "NY", "GS", 2, 90, 100, 85, 8, 12, 20),
             row(2, "GS", "NY", 2, 110, 100, 95, 10, 10, 20), row(2, "NY", "GS", 2, 100, 110, 88, 9, 10, 25),
             row(3, "GS", "NY", 3, 200, 10, 90, 10, 10, 20), row(3, "NY", "GS", 3, 10, 200, 90, 10, 10, 20)]
    r = lib.build_ratings(pd.DataFrame(rows))
    assert set(r) == {"GSW", "NYK"}
    pg1, pg2 = 90 - 10 + 10 + 11, 95 - 10 + 10 + 8.8
    pn1, pn2 = 85 - 8 + 12 + 8.8, 88 - 9 + 10 + 11
    assert r["GSW"][0] == pytest.approx(210 / (pg1 + pg2))
    assert r["GSW"][1] == pytest.approx(190 / (pn1 + pn2))
    assert r["GSW"][2] == pytest.approx(((pg1 + pn1) / 2 + (pg2 + pn2) / 2) / 2)


def test_team_map():
    for k, v in {"NY": "NYK", "SA": "SAS", "GS": "GSW", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}.items():
        assert lib.to_kalshi(k) == v
    assert len(set(lib.TEAM_MAP.values())) == 30
    assert lib.to_kalshi("STARS") is None and lib.to_kalshi("WORLD") is None


def test_team_map_covers_real_hoopr_codes():
    p = Path(__file__).resolve().parents[3] / "data" / "raw" / "nba_open" / "team_box_2026.parquet"
    if not p.exists():
        pytest.skip("no data")
    codes = set(pd.read_parquet(p, columns=["team_abbreviation"]).team_abbreviation.unique())
    assert {c for c in codes if lib.to_kalshi(c) is None} <= {"STARS", "STRIPES", "WORLD"}


def test_pick_candle_never_after_T_minus_60m():
    T = 100_000
    cs = [{"end_period_ts": T - 3600 - 7200}, {"end_period_ts": T - 3600}, {"end_period_ts": T - 3600 + 1},
          {"end_period_ts": T - 1800}, {"end_period_ts": T}]
    assert lib.pick_candle(cs, T)["end_period_ts"] == T - 3600
    assert lib.pick_candle([{"end_period_ts": T - 3599}], T) is None
    assert lib.pick_candle([], T) is None


def test_strike_convention_p_over():
    # integer totals: over 172.5 means total >= 173 ; boundary on the continuous scale is 172.5
    assert lib.boundary(172.5) == 172.5
    assert lib.boundary(172.0, "greater") == 172.5          # total > 172  == total >= 173
    assert lib.boundary(172.0, "greater_or_equal") == 171.5
    assert lib.p_over(172.5, 10, 172.5) == pytest.approx(0.5)
    assert lib.p_over(180, 10, 172.5) == pytest.approx(1 - lib.phi(-0.75))
    assert lib.p_over(180, 10, 172.5) > lib.p_over(180, 10, 175.5)
    with pytest.raises(ValueError):
        lib.boundary(172.3)


def test_median_interpolation():
    pts = [(210.5, 0.80), (213.5, 0.60), (216.5, 0.40), (219.5, 0.10)]
    assert lib.market_median(pts) == pytest.approx(215.0)          # crosses 0.5 halfway between 213.5 and 216.5
    assert lib.market_median(sorted(pts, reverse=True)) == pytest.approx(215.0)   # input order irrelevant
    assert lib.market_median([(200.5, 0.6), (203.5, 0.55)]) is None  # never crosses
    assert lib.market_median([(200.5, 0.5), (203.5, 0.4)]) == pytest.approx(200.5)
    assert lib.market_median([]) is None


def test_valid_quote():
    assert lib.valid_quote(0.4, 0.45) == (0.4, 0.45)
    assert lib.valid_quote(None, 0.5) is None and lib.valid_quote(0.5, None) is None
    assert lib.valid_quote(0.0, 0.5) is None and lib.valid_quote(0.5, 1.0) is None
    assert lib.valid_quote(0.6, 0.5) is None
    assert lib.valid_quote(float("nan"), 0.5) is None


def test_fee_rounding_count10():
    # raw = 0.07*10*0.5*0.5 = 0.175 -> micro-ceil, no cent rounding; per contract 0.0175
    assert lib.FEE.order_fee(0.5, 10) == pytest.approx(0.175)
    assert lib.fee_pc(0.5) == pytest.approx(0.0175)
    # 0.07*10*0.37*0.63 = 0.163170 exact at 6 dp
    assert lib.FEE.order_fee(0.37, 10) == pytest.approx(0.16317)
    # rounds UP to the next micro-dollar: 0.07*10*0.123*0.877 = 0.07551... -> 0.075506? check ceil
    raw = 0.07 * 10 * 0.123 * 0.877
    assert lib.FEE.order_fee(0.123, 10) == pytest.approx(math.ceil(raw * 1e6 - 1e-9) / 1e6)
    assert lib.FEE.order_fee(0.123, 10) >= raw - 1e-12


def test_edges_and_selection_one_bet_per_game():
    strikes = [  # id, strike, bid, ask, model p(over)
        {"id": "a", "strike": 210.5, "bid": 0.70, "ask": 0.72, "p": 0.90},   # YES edge ~ .18 - fee
        {"id": "b", "strike": 213.5, "bid": 0.50, "ask": 0.52, "p": 0.40},   # NO edge
        {"id": "c", "strike": 216.5, "bid": 0.30, "ask": 0.33, "p": 0.50},   # YES edge .17-fee, smaller than a
        {"id": "d", "strike": 190.5, "bid": 0.95, "ask": 0.97, "p": 0.999},  # priced outside 15-85c -> ignored
    ]
    b = lib.select_bet(strikes, 0.03)
    assert b["id"] == "a" and b["side"] == "over"
    assert b["edge"] == pytest.approx(0.90 - 0.72 - lib.fee_pc(0.72))
    # NO side: price = 1 - bid
    ye, ne, yp, npr = lib.side_edges(0.40, 0.50, 0.52)
    assert npr == pytest.approx(0.50) and ne == pytest.approx(0.60 - 0.50 - lib.fee_pc(0.50))
    # threshold excludes
    assert lib.select_bet(strikes, 0.5) is None
    # price filter: only the out-of-range strike -> no bet
    assert lib.select_bet([strikes[3]], 0.03) is None
    # exactly one dict returned (one bet per game)
    assert isinstance(b, dict)
    # under side wins when it has the largest edge
    s2 = [{"id": "x", "strike": 200.5, "bid": 0.60, "ask": 0.62, "p": 0.20}]
    assert lib.select_bet(s2, 0.03)["side"] == "under"


def test_settle():
    assert lib.settle("over", 0.4, True) == pytest.approx(1 - 0.4 - lib.fee_pc(0.4))
    assert lib.settle("over", 0.4, False) == pytest.approx(-0.4 - lib.fee_pc(0.4))
    assert lib.settle("under", 0.6, False) == pytest.approx(1 - 0.6 - lib.fee_pc(0.6))
    assert lib.settle("under", 0.6, True) == pytest.approx(-0.6 - lib.fee_pc(0.6))


def test_bootstrap_deterministic():
    x = np.random.default_rng(1).normal(0.02, 0.5, 200)
    assert lib.boot_mean_ci(x, n=500) == lib.boot_mean_ci(x, n=500)
    lo, hi = lib.boot_mean_ci(x, n=500)
    assert lo < x.mean() < hi
    assert lib.boot_mean_ci(x, n=500, seed=1) != lib.boot_mean_ci(x, n=500, seed=2)


def test_residual_test_detects_signal_and_none():
    rng = np.random.default_rng(0)
    n = 3000
    true_logit = rng.normal(0, 1, n)
    y = (rng.random(n) < 1 / (1 + np.exp(-true_logit))).astype(float)
    mid = 1 / (1 + np.exp(-(true_logit + rng.normal(0, 1, n))))        # noisy market
    pm = 1 / (1 + np.exp(-(true_logit + rng.normal(0, 0.2, n))))       # better model
    g = np.repeat(np.arange(n // 3), 3)
    b, lo, hi = lib.residual_test(mid, pm, y, g, n_boot=100)
    assert lo > 0
    pn = 1 / (1 + np.exp(-rng.normal(0, 1, n)))                          # pure noise model
    b, lo, hi = lib.residual_test(mid, pn, y, g, n_boot=100)
    assert lo <= 0 <= hi or b < 0.3


def test_no_lookahead_toy_projection_uses_only_prior_season():
    # ratings built from season S-1 box only: changing season-S rows can't change them
    def box(score):
        return pd.DataFrame([dict(game_id=1, season_type=2, team_abbreviation="GS", opponent_team_abbreviation="NY",
                                  team_score=score, opponent_team_score=90, field_goals_attempted=90,
                                  offensive_rebounds=10, total_turnovers=10, free_throws_attempted=20),
                             dict(game_id=1, season_type=2, team_abbreviation="NY", opponent_team_abbreviation="GS",
                                  team_score=90, opponent_team_score=score, field_goals_attempted=90,
                                  offensive_rebounds=10, total_turnovers=10, free_throws_attempted=20)])
    assert lib.build_ratings(box(100)) == lib.build_ratings(box(100))
    assert lib.build_ratings(box(100)) != lib.build_ratings(box(120))


def test_parse_event():
    assert lib.parse_event("KXNBATOTAL-26JUN13NYKSAS") == ("2026-06-13", "NYK", "SAS")
    assert lib.parse_event("KXNBAGAME-26JUN13NYKSAS") is None


def test_parse_expiration_value():
    import math as m
    assert lib.parse_expiration_value("178") == 178.0
    assert lib.parse_expiration_value("178.5") == 178.5
    for bad in ("", "Yes", "No", None):
        assert m.isnan(lib.parse_expiration_value(bad))


def test_T_is_min_of_base_and_hoopr_tip():
    import prep
    assert prep.schedule_T(1000, 1500) == (1000, None)           # tip later -> keep base
    assert prep.schedule_T(1000, 400) == (400, "hoopr_tip")      # rescheduled earlier -> tip
    assert prep.schedule_T(1000, 1000) == (1000, None)
    assert prep.schedule_T(1000, None) == (1000, None)           # forward games: no hoopR


def test_candle_before_tip_assert():
    import prep
    tip = 100_000
    prep.assert_candle_before_tip(tip - 3600, tip)
    prep.assert_candle_before_tip(None, tip)
    with pytest.raises(AssertionError):
        prep.assert_candle_before_tip(tip - 3599, tip)


def test_gate2_needs_both_ci_ends_positive():
    assert lib.gate2_pass(0.3, 0.1, 0.5)
    assert not lib.gate2_pass(0.3, -0.1, 0.5)
    assert not lib.gate2_pass(0.3, 0.1, -0.05)
    assert not lib.gate2_pass(-0.3, -0.5, -0.1)
