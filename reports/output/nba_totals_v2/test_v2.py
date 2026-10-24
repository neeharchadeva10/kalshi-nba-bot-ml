"""Unit tests for research log #38 (toy data only; no real outcomes)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ratings2 as r2  # noqa: E402
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("run_v2_38", Path(__file__).resolve().parent / "run.py")
run = importlib.util.module_from_spec(_spec)     # by path: #37 also has a run.py
_spec.loader.exec_module(run)


def tg(rows):
    """rows: (date, team, pts, opp_pts, poss, opp_poss)"""
    df = pd.DataFrame(rows, columns=["date", "team", "pts", "opp_pts", "poss", "opp_poss"])
    df["date"] = pd.to_datetime(df.date)
    df["pace"] = (df.poss + df.opp_poss) / 2
    return df


PREV = {"AAA": (1.10, 1.00, 100.0), "BBB": (1.00, 1.10, 96.0)}


def mk(cur_rows, Lo=1.0, Lp=100.0):
    return r2.Ratings(PREV, Lo, Lp, tg(cur_rows))


# a toy season: AAA plays on 10-20 (vs CCC), 10-21; BBB plays 10-21 (same date as AAA's 2nd game)
CUR = [("2025-10-20", "AAA", 110, 100, 100, 100), ("2025-10-20", "CCC", 100, 110, 100, 100),
       ("2025-10-21", "AAA", 120, 100, 100, 100), ("2025-10-21", "BBB", 100, 120, 100, 100)]


def test_same_date_exclusion():
    R = mk(CUR)
    # on 10-21 AAA has only its 10-20 game; BBB has none; the two 10-21 games are not visible to anyone on 10-21
    assert R.parts("AAA", np.datetime64("2025-10-21"))["n"] == 1
    assert R.parts("BBB", np.datetime64("2025-10-21"))["n"] == 0
    assert R.parts("AAA", np.datetime64("2025-10-21"))["n_lg"] == 2
    assert R.parts("AAA", np.datetime64("2025-10-20"))["n"] == 0
    assert R.league(np.datetime64("2025-10-20"))[2] == 0
    assert R.parts("AAA", np.datetime64("2025-10-22"))["n"] == 2
    # current ratings on 10-21 must equal 10-20 game only (110/100), not the 120 game
    cur = R.parts("AAA", np.datetime64("2025-10-21"))["cur"]
    assert cur[0] == pytest.approx(1.10)


def test_n0_gives_prior_and_shrink_n0():
    R = mk(CUR)
    D = np.datetime64("2025-10-20")      # nobody has played
    (o, d, p), parts = R.team_rating("AAA", D, 10)
    assert (o, d, p) == pytest.approx(PREV["AAA"]) and parts["n"] == 0 and parts["r_o"] == 1.0 and parts["r_p"] == 1.0


def test_k0_current_only():
    R = mk(CUR)
    D = np.datetime64("2025-10-22")
    (o, d, p), parts = R.team_rating("AAA", D, 0)
    assert parts["n"] == 2
    assert o == pytest.approx((110 + 120) / 200) and d == pytest.approx(200 / 200) and p == pytest.approx(100.0)
    assert r2.blend(5.0, 7.0, 3, 0) == 7.0 and r2.blend(5.0, None, 0, 0) == 5.0


def test_blend_formula():
    assert r2.blend(1.0, 2.0, 5, 5) == pytest.approx(1.5)
    assert r2.blend(1.0, 2.0, 30, 10) == pytest.approx((10 + 60) / 40)


def test_shrinkage_formula():
    assert r2.shrunk_ratio(1.1, 1.0, 0) == 1.0
    assert r2.shrunk_ratio(1.1, 1.0, 100) == pytest.approx(1.05)          # n/(n+100) = 1/2
    assert r2.shrunk_ratio(1.1, 1.0, 300) == pytest.approx(1.0 + 0.1 * 0.75)
    assert r2.shrunk_ratio(0.9, 1.0, 100) == pytest.approx(0.95)


def test_league_shift_applied_to_prior():
    # league: 2 team-games before D=10-21 -> n_lg = 2. Lo_cur = (110+100)/200 = 1.05; Lp_cur = 100
    R = mk(CUR, Lo=1.0, Lp=50.0)
    r_o, r_p, n = R.league(np.datetime64("2025-10-21"))
    assert n == 2
    assert r_o == pytest.approx(1 + 0.05 * 2 / 102)
    assert r_p == pytest.approx(1 + (100 / 50 - 1) * 2 / 102)
    (o, d, p), parts = R.team_rating("BBB", np.datetime64("2025-10-21"), 10, mode="prior")
    assert o == pytest.approx(1.00 * r_o) and d == pytest.approx(1.10 * r_o) and p == pytest.approx(96.0 * r_p)


def test_shared_pace_and_hand_projection():
    R = mk([], Lo=1.0, Lp=100.0) if False else r2.Ratings(PREV, 1.0, 100.0, tg(
        [("2025-01-01", "ZZZ", 100, 100, 100, 100), ("2025-01-01", "YYY", 100, 100, 100, 100)]))
    D = np.datetime64("2025-10-21")
    # n_lg = 2: Lo_cur = 1.0 -> r_o = 1; Lp_cur = 100 = Lp_prev -> r_p = 1 ; AAA, BBB have n = 0 -> prior
    det = R.project("AAA", "BBB", D, 10, detail=True)
    pace = (100.0 + 96.0) / 2
    assert det["pace"] == pytest.approx(pace) == pytest.approx(98.0)
    a = (1.10 * pace + 1.10 * pace) / 2          # avg(O_A x pace, D_B x pace)
    b = (1.00 * pace + 1.00 * pace) / 2          # avg(O_B x pace, D_A x pace)
    assert det["A"] == pytest.approx(a) and det["B"] == pytest.approx(b)
    assert det["proj"] == pytest.approx(a + b) == pytest.approx(205.8)


def test_hand_projection_with_blend():
    R = mk(CUR, Lo=1.05, Lp=100.0)   # D = 10-21: n_lg = 2, Lo_cur = 1.05 -> r_o = 1 ; Lp ratio 1 -> r_p = 1
    D = np.datetime64("2025-10-21")
    det = R.project("AAA", "BBB", D, 5, detail=True)
    # AAA: n = 1, cur = (1.10, 1.00, 100) ; blend k=5: (5*prior + cur)/6 ; BBB n = 0 -> prior
    oa, da, pa = (5 * 1.10 + 1.10) / 6, (5 * 1.00 + 1.00) / 6, (5 * 100.0 + 100.0) / 6
    pace = (pa + 96.0) / 2
    A = (oa * pace + 1.10 * pace) / 2
    B = (1.00 * pace + da * pace) / 2
    assert det["proj"] == pytest.approx(A + B)


def test_choose_k_tie_larger():
    assert run.choose_k({0: 15.0, 5: 14.0, 10: 14.0, 20: 14.5, 40: 14.0}) == 40
    assert run.choose_k({0: 15.0, 5: 14.0, 10: 14.2, 20: 14.5, 40: 14.1}) == 5
    assert run.choose_k({0: 14.0, 5: 14.0, 10: 14.0, 20: 14.0, 40: 14.0}) == 40


def test_team_games_filters_and_dates():
    rows = []
    for gid, (stype, abbr, opp, dt) in enumerate([(1, "BOS", "NY", "2025-10-01 20:00"),     # preseason: excluded
                                                  (2, "BOS", "NY", "2025-10-22 23:30"),     # 03:30 UTC next day
                                                  (2, "NY", "BOS", "2025-10-22 23:30"),
                                                  (2, "STARS", "WORLD", "2026-02-15 20:00")]):
        pass
    def row(gid, st, abbr, opp, dt, pts):
        return dict(game_id=gid, season_type=st, team_abbreviation=abbr, opponent_team_abbreviation=opp,
                    game_date_time=pd.Timestamp(dt, tz="America/New_York"), team_score=pts, opponent_team_score=100,
                    field_goals_attempted=80, offensive_rebounds=10, total_turnovers=10, turnovers=10, free_throws_attempted=25)
    tb = pd.DataFrame([row(1, 1, "BOS", "NY", "2025-10-01 20:00", 90), row(1, 1, "NY", "BOS", "2025-10-01 20:00", 100),
                       row(2, 2, "BOS", "NY", "2025-10-22 23:30", 110), row(2, 2, "NY", "BOS", "2025-10-22 23:30", 100),
                       row(3, 2, "STARS", "WORLD", "2026-02-15 20:00", 100), row(3, 2, "WORLD", "STARS", "2026-02-15 20:00", 100),
                       row(4, 3, "BOS", "NY", "2026-05-01 20:00", 100), row(4, 3, "NY", "BOS", "2026-05-01 20:00", 100)])
    out = r2.team_games(tb, r2.CUR_SEASON_TYPES)
    assert set(out.team) == {"BOS", "NYK"} and len(out) == 4          # preseason + All-Star dropped, playoffs kept
    assert str(out.date.iloc[0])[:10] == "2025-10-22"                      # ET date, not the UTC date
    poss = 80 - 10 + 10 + 0.44 * 25
    assert out.poss.iloc[0] == pytest.approx(poss) and out.pace.iloc[0] == pytest.approx(poss)


def test_prior_from_tg():
    t = tg([("2025-01-01", "AAA", 110, 100, 100, 100), ("2025-01-02", "AAA", 100, 100, 100, 90),
            ("2025-01-01", "BBB", 100, 110, 100, 100)])
    prev, lo, lp = r2.prior_from_tg(t)
    assert prev["AAA"][0] == pytest.approx(210 / 200) and prev["AAA"][1] == pytest.approx(200 / 190)
    assert prev["AAA"][2] == pytest.approx((100 + 95) / 2)
    assert lo == pytest.approx(310 / 300) and lp == pytest.approx((100 + 95 + 100) / 3)


def test_lookahead_future_scores_do_not_change_projection():
    base = mk(CUR)
    fut = mk(CUR + [("2025-10-21", "AAA", 999, 1, 100, 100), ("2025-10-22", "AAA", 999, 1, 100, 100)])
    D = np.datetime64("2025-10-21")
    # the same-date (10-21) 999 row and the later 10-22 row must both be excluded for a game on 10-21
    assert fut.project("AAA", "BBB", D, 10) == pytest.approx(base.project("AAA", "BBB", D, 10))
    assert fut.parts("AAA", D)["n"] == 1 and fut.league(D)[2] == 2
    assert fut.parts("AAA", np.datetime64("2025-10-22"))["n"] == 3          # both 10-21 AAA rows visible a day later
    assert fut.project("AAA", "BBB", np.datetime64("2025-10-23"), 10) != pytest.approx(
        base.project("AAA", "BBB", np.datetime64("2025-10-23"), 10))


def test_fix_tov_rule():
    tot = [np.nan, 0.0, 15.0, 12.0, 20.0]
    tur = [13.0, 12.0, 14.0, 12.0, 18.0]
    assert list(r2.fix_tov(tot, tur)) == [13.0, 12.0, 15.0, 12.0, 20.0]    # NaN, 0 -> turnovers; normal kept


def test_team_games_tov_fix_and_finite():
    def row(gid, abbr, opp, tov):
        return dict(game_id=gid, season_type=2, team_abbreviation=abbr, opponent_team_abbreviation=opp,
                    game_date_time=pd.Timestamp("2025-12-01 20:00", tz="America/New_York"), team_score=100,
                    opponent_team_score=100, field_goals_attempted=80, offensive_rebounds=10,
                    total_turnovers=tov, turnovers=13.0, free_throws_attempted=25)
    out = r2.team_games(pd.DataFrame([row(1, "CHI", "ORL", np.nan), row(1, "ORL", "CHI", 15.0)]), (2,))
    assert np.isfinite(out[["pts", "opp_pts", "poss", "opp_poss", "pace"]].values).all()
    chi = out[out.team == "CHI"].iloc[0]
    assert chi.poss == pytest.approx(80 - 10 + 13 + 0.44 * 25)
    assert chi.opp_poss == pytest.approx(80 - 10 + 15 + 0.44 * 25)
    # cumulative ratings stay finite after the repaired row
    R = r2.Ratings(PREV | {"CHI": (1.0, 1.0, 100.0), "ORL": (1.0, 1.0, 100.0)}, 1.0, 100.0, out)
    assert np.isfinite(R.project("CHI", "ORL", np.datetime64("2025-12-02"), 10))


def test_nonfinite_box_input_asserts():
    row = dict(game_id=1, season_type=2, team_abbreviation="BOS", opponent_team_abbreviation="NY",
               game_date_time=pd.Timestamp("2025-12-01 20:00", tz="America/New_York"), team_score=np.nan,
               opponent_team_score=100, field_goals_attempted=80, offensive_rebounds=10, total_turnovers=12.0,
               turnovers=12.0, free_throws_attempted=25)
    row2 = {**row, "team_abbreviation": "NY", "opponent_team_abbreviation": "BOS", "team_score": 100}
    with pytest.raises(AssertionError):
        r2.team_games(pd.DataFrame([row, row2]), (2,))


def test_decision_date_min_tip_T_minus_1h():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    ts = lambda s: int(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=et).timestamp())
    tip = ts("2026-01-24 20:00")
    # T (stale/late) 21:00 -> T-1h = 20:00 = tip: same date
    assert str(r2.decision_date(tip, ts("2026-01-24 21:00"))) == "2026-01-24"
    # T - 1h earlier than the tip and on the previous ET date -> previous date
    assert str(r2.decision_date(tip, ts("2026-01-24 00:30"))) == "2026-01-23"
    # tip earlier than T-1h -> tip date
    assert str(r2.decision_date(tip, ts("2026-01-26 12:00"))) == "2026-01-24"


def test_before_cutoff_drops_later_games(tmp_path):
    import pandas as pd
    t = tg([("2026-01-22", "AAA", 100, 100, 100, 100), ("2026-01-23", "AAA", 999, 1, 100, 100)])
    kept = t[t.date < pd.Timestamp("2026-01-23")]
    assert len(kept) == 1


# ---------------- run never touches unseen when a gate fails
def toy_inputs(n=40):
    rng = np.random.default_rng(1)
    rows, srows = [], []
    for i in range(n):
        split = "discovery" if i < n // 2 else "unseen"
        ev = f"EV{i}"
        rows.append({"event": ev, "split": split, "T": 1_700_000_000 + i * 86400, "median": 220.0,
                     "month": "2025-11", "stage": "regular", "proj": 200.0, "hoopr_id": i, "season": "2025-26"})
        for s in (215.5, 220.5, 225.5):
            srows.append({"event": ev, "ticker": f"{ev}-{s}", "strike": s, "strike_type": "greater",
                          "bid": 0.4, "ask": 0.46, "mid": 0.43, "cvol": 5})
    return pd.DataFrame(rows), pd.DataFrame(srows)


def test_run_stops_when_gate_fails_and_never_loads_unseen(tmp_path):
    games, strikes = toy_inputs()
    calls = []

    def fake_attach(g, s, which):
        calls.append(tuple(which))
        gg = g[g.split.isin(which)].copy()
        ss = s[s.event.isin(gg.event)].copy()
        gg["actual"] = 260.0 + np.arange(len(gg)) % 7   # far from projection 200 -> gate 1 fails
        gg["kalshi_total"] = np.nan
        ss["y"] = (ss.strike < 260).astype(float)
        return gg, ss

    def proj_fn(g, k, mode="blend"):
        return pd.Series(200.0 + k * 0.0, index=g.index)

    out = run.core_run(games, strikes, {"games": 40}, proj_fn, attach=fake_attach, n_boot=20, write=False)
    assert out["all_gates_pass"] is False and out["gates"]["gate1_pass"] is False
    assert calls == [("discovery",)]
    assert not any("unseen" in c for c in calls)
    assert "versions" not in out and "unseen_accuracy" not in out
    assert out["chosen_k"] == 40                   # all 5 MAEs tie -> larger k
    assert set(out["discovery_mae_by_k"]) == {"0", "5", "10", "20", "40"}


def test_ro_shim_rewrites_plain_path(tmp_path):
    import sqlite3
    p = tmp_path / "t.sqlite"
    c = sqlite3.connect(p)
    c.execute("create table a(x)")
    c.commit()
    c.close()
    ro = run._RoSqlite().connect(p)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("insert into a values (1)")
