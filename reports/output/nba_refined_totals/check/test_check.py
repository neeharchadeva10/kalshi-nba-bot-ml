import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check as c


def mk(rows):
    d = pd.DataFrame(rows, columns=["athlete_id", "team", "game_id", "tip", "minutes", "points"])
    d["tip"] = pd.to_datetime(d.tip)
    d["played"] = d.minutes > 0
    return d


def toy(n_games=12, star_minutes=30, star_pts=20):
    """X plays games 1..n; player 1 plays every game; g = game n (own rows must be excluded)."""
    rows = []
    for k in range(1, n_games + 1):
        t = pd.Timestamp("2022-01-01") + pd.Timedelta(days=k)
        rows.append((1, "X", k, t, star_minutes, star_pts))
        rows.append((2, "X", k, t, 10, 2))
    return mk(rows), list(range(1, n_games)), pd.Timestamp("2022-01-01") + pd.Timedelta(days=n_games)


def test_basic_window_and_g_excluded():
    p, xg, tip = toy()
    # add g's own row with absurd stats: must be excluded
    p.loc[len(p)] = [1, "X", 12, tip, 48, 100, True]
    r = c.player_window_stats(p, xg, tip, "X", 20)
    assert list(r.athlete_id) == [1]
    assert r.mpg.iloc[0] == 30 and r.fresh_ppg.iloc[0] == 20


def test_needs_five_team_games():
    p, xg, tip = toy(n_games=5)
    assert len(c.player_window_stats(p, xg, tip, "X", 20)) == 0      # only 4 games before g
    p, xg, tip = toy(n_games=6)
    assert len(c.player_window_stats(p, xg, tip, "X", 20)) == 1


def test_minutes_per_team_game_dnp_is_zero():
    # player 3 plays 40 min in 5 of last 10 team games -> 20 mpg exactly passes M=20 but fails M=28
    rows = []
    for k in range(1, 13):
        t = pd.Timestamp("2022-01-01") + pd.Timedelta(days=k)
        rows.append((9, "X", k, t, 1, 0))
        if 3 <= k <= 11 and k % 2 == 1:      # games 3,5,7,9,11 -> all inside last 10 (2..11)
            rows.append((3, "X", k, t, 40, 10))
    p = mk(rows)
    xg = list(range(1, 12))
    tip = pd.Timestamp("2022-01-01") + pd.Timedelta(days=12)
    r20 = c.player_window_stats(p, xg, tip, "X", 20)
    row = r20[r20.athlete_id == 3]
    # window = games 2..11 (10 games); minutes 5*40/10 = 20 -> passes 20 but 'fresh' needs a game in 9,10,11 -> game 9/11 yes
    assert row.mpg.iloc[0] == pytest.approx(20.0) and row.fresh_ppg.iloc[0] == pytest.approx(5.0)
    assert 3 not in set(c.player_window_stats(p, xg, tip, "X", 28).athlete_id)


def test_fresh_requires_minutes_in_last_three():
    rows = []
    for k in range(1, 13):
        t = pd.Timestamp("2022-01-01") + pd.Timedelta(days=k)
        rows.append((1, "X", k, t, 35 if k <= 8 else 0, 20 if k <= 8 else 0))   # injured since game 9 (DNP rows)
    p = mk(rows)
    xg = list(range(1, 12))
    tip = pd.Timestamp("2022-01-01") + pd.Timedelta(days=12)
    # games 9,10,11 minutes 0 -> not fresh -> excluded even if mpg qualifies (window 2..11: 7 games*35/10 = 24.5)
    assert 1 not in set(c.player_window_stats(p, xg, tip, "X", 20).athlete_id)


def test_current_team_uses_latest_row_before_g():
    p, xg, tip = toy()
    # player 1 traded: his latest row before g is for Y -> not on X
    p.loc[(p.athlete_id == 1) & (p.game_id == 11), "team"] = "Y"
    assert 1 not in set(c.player_window_stats(p, xg, tip, "X", 20).athlete_id)
    # a trade AT g (row for Y in g itself) does not matter: only rows before g count
    p, xg, tip = toy()
    p.loc[len(p)] = [1, "Y", 12, tip, 30, 20, True]
    assert 1 in set(c.player_window_stats(p, xg, tip, "X", 20).athlete_id)


def test_opponent_appearance_not_counted_as_minutes_for_x():
    p, xg, tip = toy()
    # player 5 played for Y in X's games 2..10 (opp rows), then joined X and played game 11 only
    extra = [(5, "Y", k, pd.Timestamp("2022-01-01") + pd.Timedelta(days=k), 30, 15) for k in range(2, 11)]
    extra.append((5, "X", 11, pd.Timestamp("2022-01-01") + pd.Timedelta(days=11), 30, 15))
    p = pd.concat([p, mk(extra)], ignore_index=True)
    r = c.player_window_stats(p, xg, tip, "X", 1)
    assert r[r.athlete_id == 5].mpg.iloc[0] == pytest.approx(3.0)


def test_absent_box_and_s():
    p, xg, tip = toy()
    cands = c.player_window_stats(p, xg, tip, "X", 1)
    g_rows = mk([(2, "X", 12, tip, 10, 2)])            # player 1 has no row at all -> absent
    ab = c.absent_box(cands, g_rows, "X")
    assert list(ab.athlete_id) == [1] and c.s_value(ab) == 20
    g_rows = mk([(1, "X", 12, tip, 0, 0), (2, "X", 12, tip, 10, 2)])   # 0 minutes -> absent
    assert list(c.absent_box(cands, g_rows, "X").athlete_id) == [1]
    assert 1 not in set(c.absent_box(cands, mk([(1, "X", 12, tip, 1, 0)]), "X").athlete_id)


def test_scope():
    assert c.scope_value(12, 9, "game") == 21 and c.scope_value(12, 9, "team") == 12


def test_rolling_b_strictly_before_and_min_n():
    dates = pd.date_range("2022-11-01", periods=40).values
    res = np.arange(40, dtype=float)
    b, n = c.rolling_b(dates, res, pd.Timestamp("2022-11-31") if False else pd.Timestamp("2022-12-01"))
    assert n == 30 and b == pytest.approx(np.mean(np.arange(30)))
    b, n = c.rolling_b(dates, res, pd.Timestamp("2022-11-30"))     # only 29 strictly before
    assert n == 29 and b == 0.0
    # same-day games are excluded
    d2 = np.array(list(dates[:30]) + [dates[30]] * 5, dtype="datetime64[ns]")
    b, n = c.rolling_b(d2, np.zeros(35), pd.Timestamp(dates[30]))
    assert n == 30
    # a huge future value never leaks
    r3 = res.copy(); r3[35] = 1e9
    assert c.rolling_b(dates, r3, pd.Timestamp("2022-12-01"))[0] < 100


def test_ridge_closed_form():
    rng = np.random.default_rng(0)
    Z = rng.normal(size=(500, 3)); Z = (Z - Z.mean(0)) / Z.std(0)
    y = 5 + Z @ np.array([1.0, -2.0, 0.5]) + rng.normal(size=500)
    b0, beta = c.ridge_fit(Z, y, 1000.0)
    assert b0 == pytest.approx(y.mean())
    assert np.allclose(beta, np.linalg.solve(Z.T @ Z + 1000 * np.eye(3), Z.T @ (y - y.mean())))
    b0, beta = c.ridge_fit(Z, y, 0.0)
    assert np.allclose(beta, [1, -2, 0.5], atol=0.15)


def test_ridge_convention_reproduces_frozen_e3():
    """Fitting 2021-22 + 2022-23 with this convention gives frozen_e3.json's b0/coef (discovery seasons only)."""
    j = json.load(open(c.E3 / "frozen_e3.json"))["ridge"]
    e1 = pd.read_parquet(c.E1F); f = pd.read_parquet(c.E3 / "e3_features.parquet")
    m = f.merge(e1[["game_id", "team", "pred", "pts"]], on=["game_id", "team"])
    m = m[m.season.isin(["2021-22", "2022-23"])]
    X = m[j["features"]].values.astype(float)
    mu, sd = X.mean(0), X.std(0)
    b0, beta = c.ridge_fit((X - mu) / sd, (m.pts - m.pred).values, 1000.0)
    assert b0 == pytest.approx(j["b0"], abs=1e-9) and np.abs(beta - np.array(j["coef"])).max() < 1e-9


def test_bets_and_pnl():
    assert c.bet_decision(230, 226, 4) == "OVER" and c.bet_decision(230, 226.5, 4) is None
    assert c.bet_decision(222, 226, 4) == "UNDER" and c.bet_decision(222.5, 226, 4) is None
    assert c.bet_pnl("OVER", 230, 226.5) == pytest.approx(100 / 110)
    assert c.bet_pnl("OVER", 220, 226.5) == -1
    assert c.bet_pnl("UNDER", 220, 226.5) == pytest.approx(100 / 110)
    assert c.bet_pnl("UNDER", 227, 227) == 0 and c.bet_pnl("OVER", 227, 227) == 0


def test_parse_version():
    assert c.parse_version("A|E3|rolling|G8|Q+EARLY") == ("A", "E3", "rolling", 8, {"Q", "EARLY"})
    assert c.parse_version("A|F42|none|G6|none")[4] == set()
