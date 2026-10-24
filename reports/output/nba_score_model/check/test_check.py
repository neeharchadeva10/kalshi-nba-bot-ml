import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np
import pandas as pd
import check_features as c


def toy(n_days=70, seed=1):
    """4 teams (ids 1-4), seasons 2009 and 2010; 2 games every second day."""
    rng = np.random.default_rng(seed)
    rows, gid = [], 1000
    for S, start in ((2009, "2008-10-28"), (2010, "2009-10-27")):
        for k in range(n_days):
            d = pd.Timestamp(start) + pd.Timedelta(days=int(k * 1.5))
            order = rng.permutation([1, 2, 3, 4])
            for a, b in ((order[0], order[1]), (order[2], order[3])):
                gid += 1
                for t, o, hm in ((a, b, 1), (b, a, 0)):
                    rows.append(dict(game_id=gid, team_id=int(t), team=f"T{t}", opp_id=int(o), season=S, date=d,
                                     home=hm, pts=0., opp_pts=0., fga=float(rng.integers(75, 95)),
                                     oreb=float(rng.integers(5, 15)), tov=float(rng.integers(8, 18)),
                                     fta=float(rng.integers(12, 30))))
    df = pd.DataFrame(rows)
    for g, idx in df.groupby("game_id").groups.items():
        i, j = list(idx)
        p = rng.integers(85, 130, 2)
        df.loc[i, ["pts", "opp_pts"]] = [p[0], p[1]]
        df.loc[j, ["pts", "opp_pts"]] = [p[1], p[0]]
    return df


def test_turnover_guard():
    t, tt = [10, 10, 10, 10], [20, np.nan, 5, 12]
    assert list(c.team_turnovers(t, tt, 2015)) == [20, 10, 10, 12]
    assert list(c.team_turnovers(t, tt, 2008)) == [10, 10, 10, 10]   # turnovers only
    assert list(c.team_turnovers(t, tt, 2012)) == [10, 10, 10, 10]
    assert list(c.team_turnovers(t, tt, 2013)) == [20, 10, 10, 12]
    assert list(c.team_turnovers([10], [np.inf], 2020)) == [10]


def test_possessions_and_game_mean():
    df = toy()
    p = c.add_poss(df)
    r = p.iloc[0]
    assert abs(r.poss_team - 0.96 * (r.fga - r.oreb + r.tov + 0.44 * r.fta)) < 1e-12
    g = p[p.game_id == r.game_id]
    assert np.allclose(g.gposs, g.poss_team.mean())


def test_hand_calc_windows():
    df = toy()
    F = c.Features(df)
    S = 2010
    gids = df[(df.season == S) & (df.team_id == 1)].sort_values("date")
    r = gids.iloc[30]
    o = F.row(r.game_id, 1)
    h = F.tg[(F.tg.team_id == 1) & (F.tg.season == S) & (F.tg.date < r.date)]
    assert len(h) == 30
    assert abs(o["ortg_X_s2d"] - 100 * h.pts.sum() / h.gposs.sum()) < 1e-9
    h10, h5 = h.tail(10), h.tail(5)
    assert abs(o["ortg_X_l10"] - 100 * h10.pts.sum() / h10.gposs.sum()) < 1e-9
    assert abs(o["ortg_X_l5"] - 100 * h5.pts.sum() / h5.gposs.sum()) < 1e-9
    assert abs(o["pace_X_l10"] - h10.gposs.mean()) < 1e-9
    pv = F.tg[(F.tg.team_id == 1) & (F.tg.season == S - 1)]
    assert abs(o["ortg_X_prev"] - 100 * pv.pts.sum() / pv.gposs.sum()) < 1e-9
    assert abs(o["pace_X_prev"] - pv.gposs.mean()) < 1e-9
    y = int(r.opp_id)
    hy = F.tg[(F.tg.team_id == y) & (F.tg.season == S) & (F.tg.date < r.date)].tail(5)
    assert abs(o["drtg_Y_l5"] - 100 * hy.opp_pts.sum() / hy.gposs.sum()) < 1e-9
    assert o["gp_X"] == 30 and o["target"] == r.pts and o["home"] == r.home


def test_missing_window_falls_back_to_prev_season():
    df = toy()
    F = c.Features(df)
    first = df[(df.season == 2010)].sort_values("date").iloc[0]
    o = F.row(first.game_id, first.team_id)
    for k in ("s2d", "l10", "l5"):
        assert o[f"ortg_X_{k}"] == o["ortg_X_prev"] and o[f"drtg_Y_{k}"] == o["drtg_Y_prev"]
    assert o["pace_X_s2d"] == o["pace_X_prev"] and o["pace_Y_l10"] == o["pace_Y_prev"]
    assert o["gp_X"] == 0 and o["dos"] == 0 and o["rest_X"] == 4 and o["b2b_X"] == 0


def test_short_windows_use_available_games():
    df = toy()
    F = c.Features(df)
    r = df[(df.season == 2010) & (df.team_id == 2)].sort_values("date").iloc[3]
    o = F.row(r.game_id, 2)
    assert o["gp_X"] == 3 and o["ortg_X_l10"] == o["ortg_X_s2d"]
    assert o["ortg_X_l5"] == o["ortg_X_s2d"]


def test_league_50_game_rule():
    df = toy()
    F = c.Features(df)
    d = df[df.season == 2010].sort_values(["date", "game_id"])
    prev = F.tg[F.tg.season == 2009]
    # after exactly 49 prior games: previous-season values
    dates = d.drop_duplicates("game_id")
    r49 = dates.iloc[49]
    assert F.tg[(F.tg.season == 2010) & (F.tg.date < r49.date)].game_id.nunique() in (48, 49)
    o = F.row(r49.game_id, r49.team_id)
    n = F.tg[(F.tg.season == 2010) & (F.tg.date < r49.date)].game_id.nunique()
    if n < 50:
        assert abs(o["league_ortg"] - 100 * prev.pts.sum() / prev.gposs.sum()) < 1e-9
        assert abs(o["league_pace"] - prev.drop_duplicates("game_id").gposs.mean()) < 1e-9
    r = dates.iloc[80]
    o = F.row(r.game_id, r.team_id)
    h = F.tg[(F.tg.season == 2010) & (F.tg.date < r.date)]
    assert h.game_id.nunique() >= 50
    assert abs(o["league_ortg"] - 100 * h.pts.sum() / h.gposs.sum()) < 1e-9
    assert abs(o["league_pace"] - h.drop_duplicates("game_id").gposs.mean()) < 1e-9


def test_rest_b2b_cap_and_dos():
    rows = []
    def add(gid, team, d, S=2010):
        for t, o, hm in ((team, 9, 1), (9, team, 0)):
            rows.append(dict(game_id=gid, team_id=t, team=f"T{t}", opp_id=o, season=S, date=pd.Timestamp(d), home=hm,
                             pts=100., opp_pts=100., fga=85., oreb=10., tov=13., fta=20.))
    add(1, 1, "2009-10-27"); add(2, 1, "2009-10-28"); add(3, 1, "2009-10-30"); add(4, 1, "2009-11-10")
    F = c.Features(pd.DataFrame(rows))
    assert (F.row(1, 1)["rest_X"], F.row(1, 1)["b2b_X"]) == (4, 0)
    assert (F.row(2, 1)["rest_X"], F.row(2, 1)["b2b_X"]) == (0, 1)       # consecutive days
    assert (F.row(3, 1)["rest_X"], F.row(3, 1)["b2b_X"]) == (1, 0)       # one day off
    assert (F.row(4, 1)["rest_X"], F.row(4, 1)["b2b_X"]) == (4, 0)       # capped
    assert F.row(4, 1)["dos"] == 14 and F.row(3, 1)["dos"] == 3 and F.row(4, 1)["gp_X"] == 3


def test_no_lookahead():
    df = toy()
    F0 = c.Features(df)
    S = 2010
    d = df[(df.season == S)].sort_values(["date", "game_id"])
    r = d.iloc[90]
    base = F0.row(r.game_id, r.team_id)
    df2 = df.copy()
    mask = (df2.date >= r.date) & (df2.game_id != r.game_id)      # other games on/after D (same-day too)
    rng = np.random.default_rng(5)
    for col in ("pts", "opp_pts", "fga", "oreb", "tov", "fta"):
        df2.loc[mask, col] = df2.loc[mask, col] + rng.integers(1, 30, mask.sum())
    # also change the game's own box stats except the target (score is the target; box stats of g are not features)
    own = df2.game_id == r.game_id
    df2.loc[own, ["fga", "oreb", "tov", "fta", "opp_pts"]] += 7
    new = c.Features(df2).row(r.game_id, r.team_id)
    for k, v in base.items():
        if k == "target":
            continue
        assert new[k] == v or (isinstance(v, float) and abs(new[k] - v) < 1e-12), k
    # control: changing an earlier game DOES change features
    df3 = df.copy()
    prior = (df3.team_id == r.team_id) & (df3.season == S) & (df3.date < r.date)
    df3.loc[prior, "pts"] += 10
    assert c.Features(df3).row(r.game_id, r.team_id)["ortg_X_s2d"] != base["ortg_X_s2d"]
    # sample draw shape
