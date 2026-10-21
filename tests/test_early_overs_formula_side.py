"""Tests for the formula-side version of the pick list (research log #40)."""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import early_overs_picks as p  # noqa: E402

tn = p.tn


def game(d, a, b, pa, pb, poss_a=100.0, poss_b=100.0):
    pace = (poss_a + poss_b) / 2
    return [dict(date=pd.Timestamp(d), team=a, opp=b, pts=pa, opp_pts=pb, poss=poss_a, opp_poss=poss_b, pace=pace),
            dict(date=pd.Timestamp(d), team=b, opp=a, pts=pb, opp_pts=pa, poss=poss_b, opp_poss=poss_a, pace=pace)]


def tg(games):
    return pd.DataFrame([r for g in games for r in g])


PREV = tg([game("2025-11-01", "AAA", "BBB", 110, 100), game("2025-11-02", "AAA", "CCC", 105, 110),
           game("2025-11-03", "BBB", "CCC", 100, 108), game("2025-11-04", "BBB", "AAA", 101, 112)])
CUR = tg([game("2026-10-01", "AAA", "BBB", 100, 90), game("2026-10-02", "AAA", "CCC", 110, 95),
          game("2026-10-03", "AAA", "BBB", 120, 100), game("2026-10-03", "CCC", "BBB", 99, 101)])


def test_side_rule():
    assert p.choose_side(231.2, 230.5) == "OVER"
    assert p.choose_side(229.9, 230.5) == "UNDER"
    assert p.choose_side(230.5, 230.5) == "UNDER"      # equal -> not strictly above -> UNDER


def test_limit_prices_yes_vs_no():
    # YES bid 48 / ask 52 -> NO bid 48 (= 100 - 52), NO ask 52 (= 100 - 48)
    assert p.limit_for_side("OVER", 46, 52) == ("YES", 46, 52)
    assert p.limit_for_side("UNDER", 46, 52) == ("NO", 48, 54)
    now = p.datetime(2026, 10, 21, 22, 0, tzinfo=p.timezone.utc)
    tip = p.datetime(2026, 10, 21, 23, 0, tzinfo=p.timezone.utc)          # 60 min before tip: inside the place window
    assert "PLACE NOW (limit buy YES @ 0.48)" in p.status_for(now, tip, 48, "YES")
    assert "PLACE NOW (limit buy NO @ 0.48)" in p.status_for(now, tip, 48, "NO")


def test_b2b_from_toy_schedule():
    keys = ["26OCT19AAABBB", "26OCT20AAACCC", "26OCT20DDDEEE", "26OCT22AAABBB", "26OCT23BBBCCC"]
    s = p.build_schedule(keys)
    assert s["AAA"] == [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 22)]
    assert p.b2b_from_schedule(s, "AAA", date(2026, 10, 21)) is True       # played 10-20
    assert p.b2b_from_schedule(s, "AAA", date(2026, 10, 22)) is False      # latest before 10-22 is 10-20
    assert p.b2b_from_schedule(s, "CCC", date(2026, 10, 22)) is False      # played 10-20, 2 days ago -> false for 10-22
    assert p.b2b_from_schedule(s, "BBB", date(2026, 10, 20)) is True       # played 10-19
    assert p.b2b_from_schedule(s, "AAA", date(2026, 10, 19)) is False      # first game of the season
    assert p.b2b_from_schedule(s, "ZZZ", date(2026, 10, 21)) is False      # unknown team
    assert p.b2b_from_schedule(s, "AAA", date(2026, 10, 23)) is True       # 10-22 game is yesterday


def test_projection_matches_tourney_f1_r2_s1_to_1e9():
    eng = tn.Engine(PREV, CUR)
    day = date(2026, 10, 4)
    c = {(f, r): p.C_B2B for f in tn.FORMULAS for r in tn.RTYPES}
    # tourney path: tip 2026-10-04 22:00 UTC -> D = 2026-10-04 ET
    tip = 1791151200
    g = pd.DataFrame({"away": ["AAA"], "home": ["CCC"], "tip": [tip], "T": [tip]})
    assert tn.r2.decision_date(tip, tip) == np.datetime64("2026-10-04")
    ref = eng.project_table(g, c)["F1_R2_S1"].iloc[0]
    sched = p.build_schedule(["26OCT01AAABBB", "26OCT02AAACCC", "26OCT03AAABBB", "26OCT03CCCBBB"])
    nb = int(p.b2b_from_schedule(sched, "AAA", day)) + int(p.b2b_from_schedule(sched, "CCC", day))
    assert nb == 2
    mine = p.project_f1r2s1(eng, "AAA", "CCC", day, nb)
    assert mine == pytest.approx(ref, abs=1e-9, rel=0)
    # no back-to-back: only the constant changes
    assert p.project_f1r2s1(eng, "AAA", "CCC", day, 0) == pytest.approx(mine - 2 * p.C_B2B, abs=1e-9, rel=0)
    assert p.games_before(eng, "AAA", day) == 3 and p.games_before(eng, "CCC", day) == 2


def test_prior_only_when_no_current_games():
    eng = tn.Engine(PREV, PREV.iloc[0:0].copy())
    v = p.project_f1r2s1(eng, "AAA", "BBB", date(2026, 10, 21), 0)
    Lo, Lp, ro, rp = eng.league_level(np.datetime64("2026-10-21"))
    assert ro == 1.0 and rp == 1.0 and Lo == pytest.approx(eng.Lo_prev)    # unshifted
    o = eng.prev
    pace = (o["AAA"][2] + o["BBB"][2]) / 2
    exp = pace * ((o["AAA"][0] + o["BBB"][1]) / 2 + (o["BBB"][0] + o["AAA"][1]) / 2)
    assert v == pytest.approx(exp, abs=1e-9)
