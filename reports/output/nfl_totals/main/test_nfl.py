"""pytest for research log #66 (NFL totals): fees/P&L, EW arithmetic, no-look-ahead guards, injury timing, forecast flag, Kalshi candle
timing, picker/median, fair model, bets, guards, and the whole holdout stage on SYNTHETIC data.  No real outcome is read by any test
except the structural checks of the saved artifacts (seasons used in fits).

Run:  .venv/Scripts/python.exe -m pytest reports/output/nfl_totals/main/test_nfl.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_features as BF  # noqa: E402
import gates as GT  # noqa: E402
import nfl_lib as N  # noqa: E402
import part_a as A  # noqa: E402
import part_b as B  # noqa: E402
import part_c as C  # noqa: E402
import run_select as RS  # noqa: E402
import run_test as RT  # noqa: E402


# ------------------------------------------------------------------ toy leagues
def toy_games(n_teams=8, seasons=(2006, 2007, 2008), weeks=17, seed=1):
    """Round-robin-ish synthetic schedule: every week every team plays once; random scores.  Columns as load_games() produces."""
    rng = np.random.default_rng(seed)
    teams = [f"T{i}" for i in range(n_teams)]
    rows = []
    t0 = pd.Timestamp("2006-09-10 17:00", tz="UTC")
    for si, s in enumerate(seasons):
        for w in range(1, weeks + 1):
            perm = rng.permutation(teams)
            kick = t0 + pd.Timedelta(days=365 * si + 7 * (w - 1))
            for a, b in zip(perm[0::2], perm[1::2]):
                rows.append({"game_id": f"{s}_{w:02d}_{a}_{b}", "season": s, "week": w, "game_type": "REG", "home": b, "away": a,
                             "kick_s": int(kick.timestamp()), "et_date": kick.strftime("%Y-%m-%d"), "home_score": float(rng.integers(3, 40)),
                             "away_score": float(rng.integers(3, 40)), "total_line": 44.5, "home_qb_id": f"qb_{b}", "away_qb_id": f"qb_{a}",
                             "home_coach": f"c_{b}", "away_coach": f"c_{a}", "home_rest": 7, "away_rest": 7, "scored": True})
    return pd.DataFrame(rows).sort_values(["kick_s", "game_id"]).reset_index(drop=True)


def hist_of(g):
    tg = N.team_game_table(g)
    return tg, tg[tg.scored][["team", "season", "kick_s", "pts", "pts_allowed"]]


# ------------------------------------------------------------------ fees, P&L
def test_fee_and_net_examples():
    assert N.kalshi_fee(0.52) == 0.0175                      # 51c ask -> P 0.52 -> 0.07*0.52*0.48 = 0.017472 -> rounded UP to 0.0175
    assert N.kalshi_fee(0.51) == 0.0175
    assert N.kalshi_fee(0.50) == 0.0175
    assert N.kalshi_fee(0.30) == 0.0147                      # 0.07*0.3*0.7 = 0.0147 exactly: no rounding up past it
    assert N.kalshi_fee(1.0) == 0.0
    assert N.kalshi_net(0.51, 1) == pytest.approx(1 - 0.52 - 0.0175)
    assert N.kalshi_net(0.51, 0) == pytest.approx(-0.52 - 0.0175)


def test_fee_rounds_up_not_nearest():
    # 0.07 * 0.20 * 0.80 = 0.0112 exactly -> stays 0.0112; 0.07*0.13*0.87 = 0.007917 -> 0.0080 (up)
    assert N.kalshi_fee(0.20) == pytest.approx(0.0112)
    assert N.kalshi_fee(0.13) == pytest.approx(0.0080)


def test_sb_pnl_win_loss_push():
    p = N.sb_pnl([True, True, True, False, False, False], [50, 40, 45, 40, 50, 45], [45, 45, 45, 45, 45, 45])
    assert p == pytest.approx([10 / 11, -1, 0.0, 10 / 11, -1, 0.0])
    s = N.summarize_sb(p)
    assert (s["wins"], s["losses"], s["pushes"], s["n"]) == (2, 2, 2, 6)
    assert s["mean"] == pytest.approx((2 * 10 / 11 - 2) / 6)
    assert N.BREAK_EVEN == pytest.approx(0.5238095, abs=1e-6)


# ------------------------------------------------------------------ EW / prior / blend arithmetic
def test_ew_ratio_hand_example():
    # chronological [20, 30, 10], h = 2: weights (oldest..latest) 0.5^(2/2)=0.5, 0.5^(1/2), 1
    w = np.array([0.5, 0.5 ** 0.5, 1.0])
    expect = (w * np.array([20, 30, 10])).sum() / w.sum()
    assert N.ew_ratio([20, 30, 10], np.ones(3), 2) == pytest.approx(expect)
    assert N.ew_ratio([4, 6], [2, 3], 8) == pytest.approx((0.5 ** (1 / 8) * 4 + 6) / (0.5 ** (1 / 8) * 2 + 3))


def test_prior_and_blend_hand_example():
    assert N.prior_from_end(24.0, 21.0) == pytest.approx(23.0)               # 24 + (21-24)/3
    assert N.prior_from_end(np.nan, 21.0) == pytest.approx(21.0)             # no previous season -> league mean
    assert N.blend_prior(30.0, 2, 23.0, 4) == pytest.approx((2 * 30 + 4 * 23) / 6)
    assert N.blend_prior(np.nan, 0, 23.0, 4) == pytest.approx(23.0)          # n = 0 -> prior only


def test_ew_components_toy_team():
    # season 2006: team X scored 20, 30, 10 in its first three games; season 2007 game 1 is the query (n = 0) and game 3 (n = 2)
    rows = []
    k = 1_000_000
    for s, pts in ((2006, [20, 30, 10, 14]), (2007, [28, 17, 21])):
        for p in pts:
            rows.append({"team": "X", "season": s, "kick_s": k, "pts": float(p), "pts_allowed": 0.0})
            k += 7 * 86400
        k += 100 * 86400
    # other team Y to give a league mean
    for s in (2006, 2007):
        rows.append({"team": "Y", "season": s, "kick_s": 5_000, "pts": 10.0, "pts_allowed": 0.0})
    hist = pd.DataFrame(rows)
    x = hist[hist.team == "X"].sort_values("kick_s")
    q = pd.DataFrame({"team": ["X", "X"], "season": [2007, 2007], "kick_s": [x.kick_s.iloc[4], x.kick_s.iloc[6]]})
    comp = N.ew_components(hist, q, {"O": ("pts", None)}, 2)["O"]
    end = N.ew_ratio([20, 30, 10, 14], np.ones(4), 2)
    lprev = (20 + 30 + 10 + 14 + 10) / 5                                     # league mean of 2006: X's four games plus Y's one
    prior = end + (lprev - end) / 3
    assert comp["n"].tolist() == [0.0, 2.0]
    assert comp["prior"][0] == pytest.approx(prior)
    assert comp["cur"][1] == pytest.approx(N.ew_ratio([28, 17], np.ones(2), 2))
    assert N.blend_prior(comp["cur"], comp["n"], comp["prior"], 4)[1] == pytest.approx((2 * comp["cur"][1] + 4 * prior) / 6)


def test_league_level_window_and_minimum():
    g = toy_games()
    sc = g[g.scored]
    q = g.kick_s.values
    L = N.league_level(sc, q)
    assert np.isnan(L[0])                                                    # no earlier game
    # a late-2006 game: window = every earlier game in the 365 days
    i = int(np.nonzero(g.game_id.values == "2006_17_" + g[g.game_id.str.startswith("2006_17")].game_id.iloc[0][8:])[0][0])
    prev = g[(g.kick_s < g.kick_s[i]) & (g.kick_s >= g.kick_s[i] - 365 * 86400)]
    assert len(prev) >= 50
    assert L[i] == pytest.approx((prev.home_score.sum() + prev.away_score.sum()) / (2 * len(prev)))


# ------------------------------------------------------------------ no look-ahead
def test_apts_prediction_unchanged_when_future_deleted_or_changed():
    g = toy_games()
    tg, hist = hist_of(g)
    full = N.apts_table(g, hist, g[g.scored])
    cols = [c for c in full.columns if c != "game_id"]
    rng = np.random.default_rng(3)
    for i in rng.choice(np.arange(60, len(g)), 8, replace=False):
        row = g.iloc[[i]]
        past = g[g.kick_s < g.kick_s.iloc[i]]                                 # games at/after g deleted
        _, hp = hist_of(past)
        t = N.apts_table(row, hp, past[past.scored])
        np.testing.assert_allclose(t[cols].values, full.iloc[[i]][cols].values, rtol=0, atol=1e-12)
        # altering g's own result and every later result must not move g's prediction
        g2 = g.copy()
        later = g2.kick_s >= g2.kick_s.iloc[i]
        g2.loc[later, ["home_score", "away_score"]] = 99.0
        _, h2 = hist_of(g2)
        t2 = N.apts_table(g2.iloc[[i]], h2, g2[g2.scored])
        np.testing.assert_allclose(t2[cols].values, full.iloc[[i]][cols].values, rtol=0, atol=1e-12)


def test_pbp_features_unchanged_when_future_changed():
    g = toy_games()
    tg = N.team_game_table(g)
    rng = np.random.default_rng(5)
    st = tg[["game_id", "team"]].copy()
    for c in ("off_epa", "def_epa", "plays", "off_drives", "off_pts", "def_drives", "def_pts", "ns_secs", "proe"):
        st[c] = rng.normal(size=len(st)) + 3
    hist = N.pbp_history(st, tg)
    full = N.epa_features(g, hist)
    i = 90
    row = g.iloc[[i]]
    st2 = st.copy()
    later_ids = set(g[g.kick_s >= g.kick_s.iloc[i]].game_id)
    st2.loc[st2.game_id.isin(later_ids), ["off_epa", "def_epa", "plays"]] = 1e6
    f2 = N.epa_features(row, N.pbp_history(st2, tg))
    a = full[full.game_id == row.game_id.iloc[0]].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, f2.reset_index(drop=True))


def test_fits_use_d_rows_only():
    g = toy_games(seasons=(2006, 2007, 2008, 2015))
    g["window"] = [N.window_of(s) for s in g.season]
    tg = N.team_game_table(g)
    rng = np.random.default_rng(2)
    st = tg[["game_id", "team"]].copy()
    st["off_epa"], st["def_epa"], st["plays"] = rng.normal(size=len(st)), rng.normal(size=len(st)), rng.normal(60, 5, len(st))
    hist = N.pbp_history(st, tg)
    ef = N.epa_features(g, hist)
    wide = BF.wide_epa(ef)
    L = pd.Series(44.0 + rng.normal(size=len(g)), index=g.game_id)
    d_ids = g[g.window == "D"].game_id.values
    assert 0 < len(d_ids) < len(g)
    p1, c1 = BF.fit_epa_models(g, tg, wide, L, d_ids)
    tg2 = tg.copy()
    tg2.loc[~tg2.game_id.isin(d_ids), "pts"] = 1000.0                          # wreck every non-D outcome
    p2, c2 = BF.fit_epa_models(g, tg2, wide, L, d_ids)
    for h in N.HS:
        assert c1[h]["coef"] == c2[h]["coef"]
    # ridge: changing a non-D row cannot change a fit made on D rows, and the saved artifacts only name D seasons
    X = rng.normal(size=(300, 4))
    y = X @ np.array([1.0, -2.0, 0.0, 0.5]) + rng.normal(size=300)
    seasons = np.repeat(np.arange(6), 50)
    lam, mse = N.loso_lambda(X, y, seasons, lambdas=(0.1, 10.0, 1e5))
    assert lam in (0.1, 10.0) and set(mse) == {0.1, 10.0, 1e5}
    f = N.ridge_fit(X, y, 0.1)
    assert np.allclose(f["beta"] / f["sd"], [1.0, -2.0, 0.0, 0.5], atol=0.25)
    assert np.abs(N.ridge_fit(X, y, 1e9)["beta"]).max() < 1e-3


@pytest.mark.skipif(not (N.MAIN_OUT / "ares_ridge.json").exists(), reason="build_features not run")
def test_saved_fits_name_only_d_seasons():
    r = json.loads((N.MAIN_OUT / "ares_ridge.json").read_text())
    e = json.loads((N.MAIN_OUT / "epa_ols.json").read_text())
    assert set(r["seasons"]) <= set(N.D_SEASONS)
    assert all(set(v["seasons"]) <= set(N.D_SEASONS) for v in e.values())
    gt = pd.read_parquet(N.MAIN_OUT / "game_table.parquet")
    assert r["n_rows"] <= int((gt.window == "D").sum())
    assert max(v["n_team_games"] for v in e.values()) <= 2 * int((gt.window == "D").sum())
    assert not any(c in gt.columns for c in ("total", "home_score", "away_score"))   # no outcome stored in the table


def test_attach_outcomes_refuses_other_seasons():
    g = toy_games()
    fr = pd.DataFrame({"game_id": g.game_id, "season": g.season})
    with pytest.raises(RuntimeError):
        N.attach_outcomes(fr, g, [2006])
    ok = N.attach_outcomes(fr[fr.season == 2006], g, [2006])
    assert ok.total.notna().all()


# ------------------------------------------------------------------ pbp aggregation
def toy_pbp():
    # game G1: ARI drive 1 (2 plays + TD + XP); BUF drive 2 throws a pick-six, XP by the defence (posteam = NYJ); ARI drive 3 field goal.
    rows = [
        # game_id, play_id, play_type, posteam, defteam, fixed_drive, ps, psp, wp, hsr, gsr, epa, pass_oe
        ("G1", 1, "kickoff", "ARI", "BUF", 1, 0, 0, 0.5, 1800, 3600, 0.0, np.nan),
        ("G1", 2, "run", "ARI", "BUF", 1, 0, 0, 0.5, 1790, 3590, 0.1, -10.0),
        ("G1", 3, "pass", "ARI", "BUF", 1, 0, 0, 0.5, 1760, 3560, 0.3, np.nan),       # 30 s after play 2, both neutral
        ("G1", 4, "pass", "ARI", "BUF", 1, 0, 0, 0.9, 1730, 3530, 0.5, 20.0),          # not neutral (wp .9)
        ("G1", 5, "extra_point", "ARI", "BUF", 1, 0, 7, 0.9, 1720, 3520, 0.0, np.nan),
        ("G1", 6, "kickoff", "BUF", "ARI", 2, 7, 7, 0.5, 1710, 3510, 0.0, np.nan),
        ("G1", 7, "pass", "BUF", "ARI", 2, 7, 7, 0.5, 1700, 3500, -6.0, 5.0),           # intercepted, returned for a TD
        ("G1", 8, "extra_point", "ARI", "BUF", 2, 7, 8, 0.5, 1680, 3480, 0.0, np.nan),  # PAT by the OTHER team: posteam ARI, same fixed_drive 2
        ("G1", 9, "kickoff", "BUF", "ARI", 3, 7, 7, 0.5, 1670, 3470, 0.0, np.nan),
        ("G1", 10, "field_goal", "BUF", "ARI", 3, 7, 10, 0.5, 1600, 3400, 1.0, np.nan),
        ("G1", 11, "qb_kneel", "BUF", "ARI", 3, 10, 10, 0.5, 1590, 3390, -0.1, np.nan),
        ("G1", 12, "pass", "", "", 4, 0, 0, 0.5, 1500, 3300, 0.2, np.nan),               # '' posteam (seen in 1999)
        # excluded game
        ("BAD", 1, "pass", "ARI", "BUF", 1, 0, 0, 0.5, 1800, 3600, 9.0, np.nan),
    ]
    cols = ["game_id", "play_id", "play_type", "posteam", "defteam", "fixed_drive", "posteam_score", "posteam_score_post", "wp",
            "half_seconds_remaining", "game_seconds_remaining", "epa", "pass_oe"]
    p = pd.DataFrame(rows, columns=cols)
    p["qb_kneel"] = (p.play_type == "qb_kneel").astype(float)
    p["qb_spike"] = 0.0
    p["penalty"] = 0.0
    p.loc[2, "penalty"] = 1.0
    p["home_team"], p["away_team"] = "BUF", "ARI"
    p.loc[p.game_id == "BAD", ["home_team", "away_team"]] = ["BUF", "ARI"]
    return p


def test_aggregate_pbp_filters_drives_neutral_and_exclusions():
    tg, gm = N.aggregate_pbp_season(toy_pbp(), {"BAD"})
    assert set(tg.game_id) == {"G1"} and set(gm.game_id) == {"G1"}
    a = tg[tg.team == "ARI"].iloc[0]
    b = tg[tg.team == "BUF"].iloc[0]
    # EPA plays (pass/run, no kneel, epa not null, posteam not null): ARI plays 2, 3, 4; BUF play 7 (pass).  Play 12 has posteam '' -> null.
    assert a.plays == 3 and a.off_epa == pytest.approx((0.1 + 0.3 + 0.5) / 3)
    assert a.def_epa == pytest.approx(-6.0) and b.def_epa == pytest.approx((0.1 + 0.3 + 0.5) / 3)
    assert b.plays == 1
    # PROE: null pass_oe counts as 0 (play 3)
    assert a.proe == pytest.approx((-10.0 + 0.0 + 20.0) / 3)
    # drives: ARI drive 1 = 7 points; BUF drive 2 (pick-six) = 0 (the other team's PAT is ignored), BUF drive 3 = 3 points.
    assert (a.off_drives, a.off_pts) == (1, 7.0)
    assert (b.off_drives, b.off_pts) == (2, 3.0)
    assert (a.def_drives, a.def_pts) == (2, 3.0) and (b.def_drives, b.def_pts) == (1, 7.0)
    # neutral seconds: only the pair (play 2 -> 3), 30 s, both snaps neutral (play 4 has wp 0.9)
    assert a.ns_secs == pytest.approx(30.0) and a.ns_n == 1
    assert gm.pen.iloc[0] == 1.0


# ------------------------------------------------------------------ A-RES schedule features
def test_coach_flags():
    rows = []
    for s, coaches in ((2010, ["A"] * 4), (2011, ["A", "A", "B", "B", "B", "B", "B", "B", "B"])):
        for i, c in enumerate(coaches):
            rows.append({"game_id": f"{s}_{i}", "team": "X", "season": s, "kick_s": s * 1000 + i, "coach": c})
    cf = N.coach_flags(pd.DataFrame(rows)).set_index("game_id")
    assert cf.loc["2011_0", "hc_change"] == 0 and cf.loc["2011_1", "hc_change"] == 0
    assert [cf.loc[f"2011_{i}", "hc_change"] for i in range(2, 9)] == [1, 1, 1, 1, 1, 0, 0]       # change game + the next 4 (distance <= 4)
    assert cf.loc["2011_2", "hc_first"] == 1 and cf.loc["2011_0", "hc_first"] == 0 and cf.loc["2010_0", "hc_first"] == 1


def inj_rows():
    def r(week, gsis, status, dm, team="AAA", season=2013):
        return {"season": season, "week": week, "team": team, "gsis_id": gsis, "report_status": status, "date_modified": dm}
    d = pd.DataFrame([
        r(1, "q1", "Questionable", "2013-09-04 18:00:00+00:00"),
        r(1, "q1", "Out", "2013-09-06 18:00:00+00:00"),          # Friday: latest report before Sunday 09-08  -> Out
        r(1, "q1", "Probable", "2013-09-08 16:00:00+00:00"),     # game day 12:00 ET: NOT before the game date -> ignored
        r(2, "q2", "Out", "2013-09-14 18:00:00+00:00"),
        r(2, "q2", "Probable", "2013-09-15 02:00:00+00:00"),     # 09-14 22:00 ET (still before the 09-15 game date), later -> 0
        r(3, "q3", "Out", None),                                 # undated -> unusable -> 0
        r(4, "q4", "Doubtful", "2013-09-27 18:00:00+00:00"),
    ])
    inj = d.copy()
    dm = pd.to_datetime(inj.date_modified, utc=True)
    inj["dm_et_date"] = dm.dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
    inj.loc[dm.isna(), "dm_et_date"] = np.nan
    inj["dm_s"] = (dm - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()
    return inj


@pytest.mark.parametrize("week,qb,date,expect", [
    (1, "q1", "2013-09-08", 1),        # latest report dated before the game date is the Friday 'Out'; the game-day 'Probable' is ignored
    (2, "q2", "2013-09-15", 0),        # a later (still pre-game-date) report says Probable
    (3, "q3", "2013-09-22", 0),        # only an undated report exists
    (4, "q4", "2013-09-29", 1),        # Doubtful counts
    (1, "q9", "2013-09-08", 0),        # not listed
])
def test_qb_out_cases(week, qb, date, expect):
    tg = pd.DataFrame([
        {"game_id": "prev", "team": "AAA", "season": 2013, "week": week - 1, "kick_s": int(pd.Timestamp("2013-01-01", tz="UTC").timestamp()),
         "qb_id": qb, "scored": True, "et_date": "2013-01-01"}])
    q = pd.DataFrame([{"game_id": "cur", "team": "AAA", "season": 2013, "week": week, "et_date": date,
                       "kick_s": int(pd.Timestamp(date + " 17:00", tz="UTC").timestamp())}])
    assert N.qb_out_flags(tg, q, inj_rows()).qb_out.iloc[0] == expect


def test_qb_out_ignores_games_at_or_after_the_query():
    tg = pd.DataFrame([
        {"game_id": "later", "team": "AAA", "season": 2013, "week": 1, "kick_s": 2_000_000_000, "qb_id": "q1", "scored": True, "et_date": "2033-05-18"},
        {"game_id": "prev", "team": "AAA", "season": 2013, "week": 0, "kick_s": 1_000_000_000, "qb_id": "q9", "scored": True, "et_date": "2001-09-09"}])
    q = pd.DataFrame([{"game_id": "cur", "team": "AAA", "season": 2013, "week": 1, "et_date": "2013-09-08", "kick_s": 1_378_000_000}])
    assert N.qb_out_flags(tg, q, inj_rows()).qb_out.iloc[0] == 0             # previous QB is q9 (the 'later' game is not used)


def test_referee_penalty_feature():
    rows, pen = [], []
    for s in (2010, 2011, 2012):
        for i in range(12):
            rows.append({"game_id": f"{s}_{i}", "season": s, "referee": "R1" if i < 10 else "R2"})
            pen.append({"game_id": f"{s}_{i}", "pen": 10.0 if i < 10 else 20.0})
    g, gm = pd.DataFrame(rows), pd.DataFrame(pen)
    out = N.referee_penalty_feature(g, gm).set_index("game_id").ref_pen
    assert out["2012_0"] == pytest.approx(10.0)                              # R1: 20 games in 2010-11 -> mean 10
    league = (20 * 10.0 + 4 * 20.0) / 24
    assert out["2012_11"] == pytest.approx(league)                           # R2 has only 4 games (< 10) -> league mean of those seasons
    assert np.isnan(out["2010_0"])                                           # no previous seasons at all


# ------------------------------------------------------------------ Part A rules
def toy_frame():
    return pd.DataFrame({"game_id": list("abcdef"), "season": 2010, "close": [44.0, 44.0, 44.5, 44.5, 44.5, 44.5],
                         "apts_h8_k4": [48.0, 40.0, 49.0, 40.0, 45.0, np.nan], "total": [50.0, 41.0, 40.0, 40.0, 45.0, 50.0],
                         "ares_pred": [0.6, -0.7, 0.1, -3.0, 2.0, np.nan]})


def test_decide_and_sb_bets_thresholds_and_pushes():
    f = toy_frame()
    b = A.sb_bets(f, "A-PTS|h8|k4|G4.5")
    # a: +4.0 (no), b: -4.0 (no), c: +4.5 OVER (total 40 -> loss), d: -4.5 UNDER (total 40 < 44.5 -> win), e: +0.5 no, f: NaN
    assert b.game_id.tolist() == ["c", "d"] and b.over.tolist() == [True, False]
    assert b.pnl.tolist() == pytest.approx([-1.0, 10 / 11])
    b4 = A.sb_bets(f, "A-PTS|h8|k4|G3")
    assert b4.game_id.tolist() == ["a", "b", "c", "d"]
    assert b4.pnl.tolist()[0] == pytest.approx(10 / 11) and b4.pnl.tolist()[1] == pytest.approx(10 / 11)
    r = A.sb_bets(f, "A-RES|g0.5")
    assert r.game_id.tolist() == ["a", "b", "d", "e"] and r.over.tolist() == [True, False, False, True]
    row = A.version_row(f, "A-RES|g0.5")
    assert row["n"] == 4 and row["pushes"] == 0 and row["wins"] == 4        # a over 50>44, b under 41<44, d under 40<44.5, e over 45>44.5
    f2 = f.copy()
    f2.loc[0, "total"] = 44.0
    assert A.version_row(f2, "A-RES|g0.5")["pushes"] == 1


def test_versions_are_the_56_registered():
    ids = A.load_version_ids()
    assert len(ids) == 56 and ids == A.all_versions()
    for v in ids:
        A.parse_version(v)


def test_choose_best_ties_and_minimum_bets():
    rows = pd.DataFrame({"version": ["a", "b", "c", "d"], "n": [250, 400, 400, 150], "mean_pnl": [0.05, 0.05, 0.02, 0.9]})
    assert N.choose_best(rows, 200).version == "b"                           # tie on mean -> more bets; d has too few bets
    assert N.choose_best(rows.assign(n=10), 200) is None


def test_select_stop_flag():
    f = toy_frame()
    rows, el, res = RS.select(f, ["A-RES|g0.5"])
    assert res["chosen"] is None and res["stop"] is True and res["n_eligible"] == 0


def test_verdict_a_logic():
    base = {"per_window_sb": {"H1": {"mean": 0.02, "n": 100}, "H2": {"mean": 0.01, "n": 100}, "H3": {"mean": 0.03, "n": 15}},
            "pooled_sb": {"mean": 0.015, "boot_lo": 0.001}, "luck": {"frac_copies_ge_actual": 0.03}, "kalshi": {"n": 40, "mean_net": 0.01}}
    assert A.verdict(base)["verdict"] == "PASS"
    x = json.loads(json.dumps(base))
    x["kalshi"]["n"] = 20
    assert A.verdict(x)["verdict"] == "PROMISING"
    x = json.loads(json.dumps(base))
    x["luck"]["frac_copies_ge_actual"] = 0.2
    assert A.verdict(x)["verdict"] == "FAIL"
    x = json.loads(json.dumps(base))
    x["per_window_sb"]["H3"] = {"mean": -0.5, "n": 5}                         # fewer than 10 bets: ignored
    x["pooled_sb"]["boot_lo"] = -0.01
    assert A.verdict(x)["verdict"] == "PROMISING"


# ------------------------------------------------------------------ Part B
def b_toy():
    return pd.DataFrame({
        "game_id": list("abcdefgh"), "season": 2026, "window": "H3", "game_type": "REG", "week": [3, 3, 3, 1, 5, 5, 5, 5],
        "roof": ["outdoors", "dome", "outdoors", "open", "outdoors", "outdoors", "outdoors", "outdoors"],
        "roof_category": ["open-air", "dome", "retractable", "open-air", "open-air", "open-air", "open-air", "open-air"],
        "wind": [12.0, 12.0, 12.0, 9.0, 19.0, 20.0, np.nan, 10.0],
        "fc_wind_mph": [11.0, 15.0, 15.0, 25.0, 19.9, 20.0, 5.0, 14.0], "forecast_ok": [True, True, True, True, True, False, True, False],
        "obs_wind_mph": [14.0, 0, 0, 0, 10.0, 0, 0, 0],
        "weekday": ["Sunday", "Monday", "Thursday", "Thursday", "Monday", "Sunday", "Sunday", "Sunday"],
        "gametime": ["20:20", "19:00", "20:15", "20:15", "18:59", "13:00", "20:20", "16:25"],
        "close": 44.5, "total": [40.0] * 8})


def test_rule_masks():
    f = b_toy()
    m = {r: B.rule_mask(r, f).tolist() for r in B.RULES}
    assert m["B1"] == [True, False, False, False, True, False, False, True]      # open-air only; wind 10..19 inclusive 19, 20 excluded, NaN excluded
    assert m["B2"] == [True, False, False, False, True, False, False, False]     # forecast_ok False rows never count (h with ok False excluded)
    assert m["B3"] == [False, False, False, True, False, False, False, False]    # fc >= 20 and ok; row f has ok False
    assert m["B4"] == [True, True, False, False, False, False, True, False]      # Sun/Mon at/after 19:00 ET, REG
    assert m["B5"] == [False, False, True, False, False, False, False, False]    # Thursday, week >= 2 (row d is week 1)


def test_b2_ignores_forecasts_not_guaranteed_24h():
    f = b_toy()
    f["forecast_ok"] = False
    assert not B.rule_mask("B2", f).any() and not B.rule_mask("B3", f).any()


def test_hindsight_split_and_evaluate_rule_runs():
    f = b_toy()
    f = pd.concat([f.assign(game_id=lambda d: d.game_id + str(i), season=2026 - i % 2 * 0) for i in range(3)], ignore_index=True)
    h = B.hindsight_split(f)
    assert h["obs_ge_fc"]["n"] + h["obs_lt_fc"]["n"] == 6                      # rows a (14 >= 11) and e (10 < 19.9) x3
    assert h["obs_ge_fc"]["n"] == 3 and h["edge_was_hindsight"] is False       # both groups win here -> not "only where observed exceeded"
    f_h = f.copy()
    f_h.loc[f_h.game_id.str.startswith("e"), "total"] = 50.0                    # the obs < fc group now loses
    assert B.hindsight_split(f_h)["edge_was_hindsight"] is True
    hk = pd.DataFrame({"game_id": f.game_id.values[:6], "net_under": 0.05})
    res = B.evaluate_rule("B2", f, hk)
    assert res["per_window_sb"]["H3"]["n"] == 6 and res["clean_pooled_sb"]["n"] == 6
    assert B.verdict(res)["verdict"] in ("FAIL", "PASS", "PROMISING")
    assert "label" in B.verdict(res) or not res["hindsight_all_forecast_era"]["edge_was_hindsight"]


def test_b_frame_join_columns():
    gt = pd.DataFrame({"game_id": ["a", "b"], "stadium_id": ["S1", "S2"]})
    wf = pd.DataFrame({"game_id": ["a"], "fc_wind_mph": [12.0], "forecast_ok": [True], "obs_wind_mph": [11.0]})
    st = pd.DataFrame({"stadium_id": ["S1", "S2"], "roof_category": ["open-air", "dome"]})
    f = B.b_frame(gt, wf, st)
    assert f.set_index("game_id").roof_category.to_dict() == {"a": "open-air", "b": "dome"}
    assert f.set_index("game_id").forecast_ok.to_dict() == {"a": True, "b": False}


# ------------------------------------------------------------------ Kalshi ladder
def test_implied_median_and_picker():
    s = [40.5, 42.5, 44.5, 46.5]
    assert C.implied_median(s, [0.8, 0.6, 0.4, 0.2]) == pytest.approx(43.5)
    assert C.implied_median(s, [0.8, 0.7, 0.6, 0.55]) is None                 # never crosses 0.50
    assert C.implied_median(s, [0.5, 0.5, 0.4, 0.2]) == pytest.approx(40.5)
    bid = np.array([0.78, 0.58, 0.38, 0.18])
    ask = np.array([0.82, 0.62, 0.42, 0.22])
    assert C.pick_main_strike(s, bid, ask) == 1 or C.pick_main_strike(s, bid, ask) == 2          # tie at |0.1| -> lower strike
    assert C.pick_main_strike(s, bid, ask) == 1
    ask2 = ask.copy()
    ask2[1] = 0.75                                                           # spread 17c -> skipped -> strike 2 (mid 0.40)
    assert C.pick_main_strike(s, bid, ask2) == 2
    assert C.pick_main_strike(s, np.zeros(4), np.ones(4)) is None            # empty-book placeholders are not two-sided


def candle_frame(rows):
    return pd.DataFrame(rows, columns=["ticker", "end_ts", "yes_bid_close_f", "yes_ask_close_f"])


def test_snapshot_candle_end_not_after_decision_and_last_candle_rule():
    kick = pd.Series({"G": 100_000})
    mk = pd.DataFrame({"ticker": ["t1", "t2", "t3", "t4"], "game_id": "G", "floor_strike": [40.5, 42.5, 44.5, 46.5]})
    dec = 100_000 - 3600
    c = candle_frame([
        ("t1", dec - 60, 0.60, 0.64), ("t1", dec, 0.61, 0.65), ("t1", dec + 60, 0.99, 0.99),   # last candle ending <= decision wins (end == decision ok)
        ("t2", dec - 3600, 0.50, 0.54),                                                        # exactly 60 min old: allowed
        ("t3", dec - 3601, 0.40, 0.44),                                                        # too old
        ("t4", dec - 120, 0.30, 0.34), ("t4", dec - 60, 0.0, 0.99),                            # last candle one-sided -> NO quote (no fallback)
    ])
    s = C.snapshot(mk, c, kick, "T60")
    assert (s.end_ts <= s.decision_s).all()
    assert s.set_index("ticker").bid.to_dict() == {"t1": 0.61, "t2": 0.50}
    # T-24h uses the hourly window of 3 h
    kick2 = pd.Series({"G": 200_000})
    dec2 = 200_000 - 86400
    c2 = candle_frame([("t1", dec2 - 10_800, 0.6, 0.64), ("t2", dec2 - 10_801, 0.5, 0.54), ("t3", dec2 + 1, 0.4, 0.44)])
    s2 = C.snapshot(mk, c2, kick2, "T24h")
    assert s2.ticker.tolist() == ["t1"]


# ------------------------------------------------------------------ fair model, Part C
@pytest.fixture(scope="module")
def fair():
    rng = np.random.default_rng(11)
    close = rng.choice(np.arange(36.5, 56.5, 1.0), 4000)
    total = np.rint(close + rng.normal(0, 13, len(close))).clip(6, 100)
    return C.fit_fair(close, total)


def test_bands_boundaries():
    assert [C.band_of(m) for m in (40.9, 41.0, 44.9, 45.0, 48.9, 49.0, 55)] == [0, 1, 1, 2, 2, 3, 3]


def test_fair_model_normalisation_and_shape(fair):
    for M in (38.0, 43.3, 47.5, 52.0):
        p = C.fair_pmf(fair, M)
        assert p.shape == (121,) and p.sum() == pytest.approx(1.0) and (p >= 0).all()
        assert C.fair_yes(p, -1.0) == pytest.approx(1.0)
        assert C.fair_yes(p, 200.0) == 0.0
        s = [C.fair_yes(p, x) for x in np.arange(20.5, 70.5, 1.0)]
        assert all(a >= b - 1e-12 for a, b in zip(s, s[1:]))
        assert abs(C.fair_yes(p, M) - 0.5) < 0.06                              # symmetric residuals -> median close to M
    assert fair["w"].min() >= C.W_CLIP[0] - 1e-12 and fair["w"].max() <= C.W_CLIP[1] + 1e-12
    p = C.fair_pmf(fair, 44.0)
    assert C.fair_yes(p, 44.0) == pytest.approx(p[45:].sum())                    # integer strike: total > 44 means t >= 45


def test_c_bets_arithmetic_and_zones():
    # a single game: M = 44, strikes 41.5 (near), 55.5 (far), fair_yes = 0.60 at 41.5
    tab = pd.DataFrame({
        "game_id": "G", "tau": "T60", "strike": [41.5, 41.5, 55.5, 55.5], "M": 44.0, "dist": [2.5, 2.5, 11.5, 11.5],
        "bid": [0.50, 0.30, 0.02, 0.01], "ask": [0.52, 0.34, 0.06, 0.03],
        "fair_yes": [0.60, 0.60, 0.20, 0.00],
    })
    tab["p_y"] = tab.ask + 0.01
    tab["cost_y"] = tab.p_y + [N.kalshi_fee(p) for p in tab.p_y]
    tab["p_n"] = (1 - tab.bid) + 0.01
    tab["cost_n"] = tab.p_n + [N.kalshi_fee(p) for p in tab.p_n]
    tab["net_y"] = 1.0 - tab.cost_y
    tab["net_n"] = 0.0 - tab.cost_n
    b0 = C.c_bets(tab, "C|T60|m0|all")
    # row0: YES cost .53+.0175=.5475 < .60 -> YES.  row1: YES cost .35+fee(.35)=.3704..<.60 -> YES (bigger edge than NO).  row2: NO cost .99+..>0.8 no, YES .07+.0046 < .20 -> YES.  row3: fair 0: nothing cheap.
    assert b0.side.tolist()[:2] == ["YES", "YES"] and len(b0) == 3
    near = C.c_bets(tab, "C|T60|m0|near")
    far = C.c_bets(tab, "C|T60|m0|far")
    assert len(near) == 2 and len(far) == 1 and near.strike.tolist() == [41.5, 41.5] and far.strike.tolist() == [55.5]
    # row0 edge = .60 - .5475 = .0525: kept at a 4c margin, dropped at 2c-over-.0525? (margins registered: 0, 2c, 4c)
    assert len(C.c_bets(tab, "C|T60|m4|all")) == 3
    tab2 = tab.copy()
    tab2.loc[0, "fair_yes"] = 0.58                                             # edge .0325 < 4c
    assert len(C.c_bets(tab2, "C|T60|m4|all")) == 2 and len(C.c_bets(tab2, "C|T60|m2|all")) == 3
    ls = C.c_bets(tab, "C-LS|T60")
    assert ls.strike.tolist() == [55.5, 55.5] and (ls.side == "NO").all()      # YES ask <= 10c
    assert ls.net.tolist() == pytest.approx(tab.net_n.values[2:].tolist())
    assert len(C.c_bets(tab.assign(tau="T24h"), "C|T60|m0|all")) == 0         # wrong snapshot


def test_c_at_most_one_side_per_strike():
    tab = pd.DataFrame({"game_id": "G", "tau": "T60", "strike": [44.5], "M": 44.5, "dist": [0.0], "bid": [0.1], "ask": [0.9], "fair_yes": [0.5],
                        "cost_y": [0.4], "cost_n": [0.4], "net_y": [0.6], "net_n": [-0.4]})      # both sides look cheap (impossible quote, but test the rule)
    assert len(C.c_bets(tab, "C|T60|m0|all")) == 1


def test_all_c_versions_registered():
    v = C.all_versions_c()
    assert len(v) == 20 and len(set(v)) == 20
    assert N.boot_ci_by_game([1.0, 2.0, 3.0, 4.0], ["a", "b", "c", "d"]) == pytest.approx(N.boot_ci([1.0, 2.0, 3.0, 4.0]))


# ------------------------------------------------------------------ bootstrap and luck
def test_bootstrap_deterministic_and_luck_basic():
    x = np.random.default_rng(0).normal(0.05, 1, 300)
    assert N.boot_ci(x) == N.boot_ci(x)
    m, lo, hi = N.boot_ci(x)
    assert lo < m < hi
    assert N.BOOT_N == 10000 and N.BOOT_SEED == 20261023 and N.LUCK_N == 500 and N.LUCK_SEED == 20261024
    over, under = np.full(50, 1.0), np.full(50, -1.0)
    r = N.luck_random_sides(over, under, 1.0)                               # actual = best possible: copies equal it only if all sides over
    assert r["frac_copies_ge_actual"] == 0.0
    r = N.luck_random_sides(over, under, -1.0)
    assert r["frac_copies_ge_actual"] == 1.0
    pool = {2020: np.r_[np.ones(10), -np.ones(30)]}
    r = N.luck_random_games(pool, {2020: 10}, 1.0)                            # the best possible draw: all ten +1 games: probability ~ 0
    assert r["frac_copies_ge_actual"] == 0.0 and r["n_bets"] == 10
    r = N.luck_random_strikes([(np.array([1.0, -1.0]), np.array([-1.0, 1.0]))], [1], 1.0)
    assert 0.3 < r["frac_copies_ge_actual"] < 0.7                              # sides and strikes random: half of the draws win


# ------------------------------------------------------------------ guards
def test_check_audit_ok_refusals(tmp_path):
    base = tmp_path / "main"
    base.mkdir()
    (base / "a.py").write_text("x = 1\n")
    study = tmp_path
    files = ("a.py",)
    with pytest.raises(SystemExit, match="AUDIT_OK not found"):
        N.check_audit_ok(study, base, files)
    (study / "AUDIT_OK").write_text(json.dumps({"sha256": {"a.py": N.sha256_file(base / "a.py")}}))
    with pytest.raises(SystemExit, match="test_started"):
        N.check_audit_ok(study, base, files)
    (study / "test_started.json").write_text("{}")
    N.check_audit_ok(study, base, files)                                      # all good
    (base / "a.py").write_text("x = 2\n")                                      # file changed after the audit
    with pytest.raises(SystemExit, match="hash mismatch"):
        N.check_audit_ok(study, base, files)
    (base / "a.py").write_text("x = 1\n")
    (study / "results_test.json").write_text("{}")
    with pytest.raises(SystemExit, match="runs once"):
        N.check_audit_ok(study, base, files)
    (study / "results_test.json").unlink()
    (study / "AUDIT_OK").write_text(json.dumps({"sha256": {}}))
    with pytest.raises(SystemExit, match="does not cover"):
        N.check_audit_ok(study, base, files)


def test_run_test_refuses_to_run_here():
    assert not (N.STUDY / "AUDIT_OK").exists() and not (N.STUDY / "test_started.json").exists()
    p = subprocess.run([sys.executable, str(HERE / "run_test.py")], capture_output=True, text=True, cwd=str(HERE))
    assert p.returncode != 0 and "REFUSED" in (p.stderr + p.stdout)
    assert not (N.STUDY / "results_test.json").exists()


def test_run_select_refuses_when_audit_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(N, "STUDY", tmp_path)
    (tmp_path / "AUDIT_OK").write_text("{}")
    with pytest.raises(SystemExit, match="REFUSED"):
        RS.main()


# ------------------------------------------------------------------ the whole holdout stage on SYNTHETIC data
def make_world(seed=0):
    rng = np.random.default_rng(seed)
    seasons = [2006, 2015, 2016, 2023, 2025, 2026]
    rows = []
    for s in seasons:
        for i in range(60 if s == 2025 else 25):
            day = pd.Timestamp(f"{s}-09-10 17:00", tz="UTC") + pd.Timedelta(days=int(i * 2.5))
            tot_med = rng.uniform(38, 52)
            h = float(rng.integers(3, 35))
            a = float(max(0, round(tot_med + rng.normal(0, 10) - h)))
            rows.append({"game_id": f"{s}_{i:02d}_A_B", "season": s, "week": 1 + i % 17, "game_type": "REG", "kick_s": int(day.timestamp()),
                         "et_date": (day - pd.Timedelta(hours=5)).strftime("%Y-%m-%d"), "home_score": h, "away_score": a, "close": tot_med})
    g = pd.DataFrame(rows)
    g["window"] = [N.window_of(s) for s in g.season]
    gt = g[["game_id", "season", "week", "game_type", "kick_s", "et_date", "window", "close"]].copy()
    gt["apts_h8_k4"] = gt.close + rng.normal(0, 4, len(gt))
    gt["apts_h8_k8"] = gt.close + rng.normal(0, 4, len(gt))
    gt["roof"] = rng.choice(["outdoors", "dome", "open"], len(gt))
    gt["wind"] = rng.integers(0, 25, len(gt)).astype(float)
    gt["stadium_id"] = rng.choice(["S1", "S2"], len(gt))
    gt["weekday"] = rng.choice(["Sunday", "Monday", "Thursday"], len(gt))
    gt["gametime"] = rng.choice(["13:00", "20:20"], len(gt))
    wf = pd.DataFrame({"game_id": gt.game_id, "fc_wind_mph": rng.uniform(0, 30, len(gt)), "forecast_ok": rng.random(len(gt)) < 0.8,
                       "obs_wind_mph": rng.uniform(0, 30, len(gt))})
    st = pd.DataFrame({"stadium_id": ["S1", "S2"], "roof_category": ["open-air", "dome"]})
    # synthetic Kalshi ladders for 2025/2026 games
    mk_rows, c1m, c1h = [], [], []
    hk = g[g.season >= 2025]
    for r in hk.itertuples():
        true_med = r.close
        for j, s in enumerate(np.arange(np.floor(true_med) - 10.5, np.floor(true_med) + 11.5, 2.0)):
            pyes = float(np.clip(0.5 - (s - true_med) * 0.03, 0.03, 0.97))
            tk = f"K-{r.game_id}-{j}"
            mk_rows.append({"ticker": tk, "event_ticker": "KXNFLTOTAL-" + r.game_id, "floor_strike": float(s), "strike_type": "greater",
                            "result": "yes" if (r.home_score + r.away_score) > s else "no", "game_id": r.game_id, "match_status": "matched"})
            for off, store in ((3600, c1m), (86400, c1h)):
                end = r.kick_s - off - 120
                c1m_or_h = store
                c1m_or_h.append({"ticker": tk, "end_ts": end, "yes_bid_close_f": round(pyes - 0.02, 2), "yes_ask_close_f": round(pyes + 0.02, 2)})
                c1m_or_h.append({"ticker": tk, "end_ts": r.kick_s - off + 600, "yes_bid_close_f": 0.01, "yes_ask_close_f": 0.99})   # AFTER the decision: must be ignored
    kal = {"markets": pd.DataFrame(mk_rows), "c1m": pd.DataFrame(c1m), "c1h": pd.DataFrame(c1h)}
    cl = g[g.season <= 2024]
    fair = C.fit_fair(np.tile(cl.close.values, 60), np.tile((cl.home_score + cl.away_score).values, 60) + np.random.default_rng(1).integers(-6, 7, 60 * len(cl)))
    return g, gt, wf, st, kal, fair


def test_run_stage_end_to_end_on_synthetic_data():
    g, gt, wf, st, kal, fair = make_world()
    sel = {"parts": {"A": {"chosen": {"version": "A-PTS|h8|k4|G1.5", "n": 300, "mean_pnl": 0.02}, "stop": False}}}
    out = RT.run_stage(g, gt, wf, st, kal, sel, fair)
    assert out["n_hk_games_t60_usable"] > 0 and out["n_hk_games_settled"] == out["n_hk_games_t60_usable"]
    assert out["A"]["verdict"]["verdict"] in ("PASS", "PROMISING", "FAIL")
    assert out["A_secondary"]["version"] == "A-PTS|h8|k8|G4.5" and out["A_secondary"]["verdict"]["verdict"] in ("PASS", "PROMISING", "FAIL")
    assert out["A"]["result"]["kalshi"]["n"] > 0
    assert set(out["B"]) == set(B.RULES)
    assert "chosen" in out["C"] and len(out["C"]["rows_hk_sel"]) == 20
    json.dumps(out, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    # STOP branches
    sel["parts"]["A"]["stop"] = True
    assert RT.run_stage(g, gt, wf, st, kal, sel, fair)["A"]["status"].startswith("STOPPED")


def test_synthetic_kalshi_candles_after_decision_are_never_used():
    g, gt, wf, st, kal, fair = make_world()
    snaps = C.build_snapshots(kal, g)
    kick = g.set_index("game_id").kick_s
    for tau, s in snaps.items():
        assert (s.end_ts <= s.decision_s).all() and (s.decision_s <= s.game_id.map(kick)).all()
        assert (s.bid > 0.01).all() and (s.ask < 0.99).all()                   # the post-decision 0.01/0.99 placeholders never appear


# ------------------------------------------------------------------ saved agreement artifacts (row counts only)
@pytest.mark.skipif(not (N.MAIN_OUT / "agree_versions_D.csv").exists(), reason="build_features not run")
def test_agreement_files_shapes():
    assert len(pd.read_csv(N.MAIN_OUT / "agree_apts_predictions.csv")) == len(pd.read_csv(N.STUDY / "sample_games.csv"))
    assert len(pd.read_csv(N.MAIN_OUT / "agree_epa_teamgames.csv")) == len(pd.read_csv(N.STUDY / "sample_teamgames.csv"))
    assert len(pd.read_csv(N.MAIN_OUT / "agree_versions_D.csv")) == len(pd.read_csv(N.STUDY / "sample_versions_A.csv"))


def test_empty_snapshots_do_not_crash():
    mk = pd.DataFrame({"ticker": pd.Series([], dtype=str), "game_id": pd.Series([], dtype=str), "floor_strike": pd.Series([], dtype=float)})
    cf = candle_frame([]).astype({"ticker": str, "end_ts": "int64", "yes_bid_close_f": float, "yes_ask_close_f": float})
    empty = C.snapshot(mk, cf, pd.Series(dtype=float), "T60")
    assert empty.empty
    assert C.main_strike_table(C.add_median(empty)).empty
    tab = C.prepare_c_table(C.add_median(empty), {"r": [np.array([0.0])] * 4, "w": np.ones(121), "n": 1}, pd.Series(dtype=float))
    assert tab.empty
    assert C.c_bets(tab.assign(tau=pd.Series(dtype=object)), "C|T60|m0|all").empty


def test_gate_functions_on_synthetic():
    ok = GT.bracket_check(pd.DataFrame({"floor_strike": [40.5, 45.5, 50.5], "result": ["yes", "yes", "no"], "game_id": "G", "match_status": "matched", "event_ticker": "KXNFLTOTAL-G"}),
                          pd.DataFrame({"game_id": ["G"], "home_score": [25.0], "away_score": [22.0]}))
    assert ok["pass"] is True                                                   # total 47: highest YES 45.5 < 47 <= lowest NO 50.5
    bad = GT.bracket_check(pd.DataFrame({"floor_strike": [40.5, 48.5], "result": ["yes", "yes"], "game_id": "G", "match_status": "matched", "event_ticker": "KXNFLTOTAL-G"}),
                           pd.DataFrame({"game_id": ["G"], "home_score": [25.0], "away_score": [22.0]}))
    assert bad["pass"] is False
    assert GT.gate_picker()["pass"] and GT.gate_fee()["pass"]


# ------------------------------------------------------------------ rehearsal mode: counts only
def test_rehearsal_output_has_no_pnl_keys_synthetic():
    g, gt, wf, st, kal, fair = make_world()
    sel = {"parts": {"A": {"chosen": {"version": "A-PTS|h8|k4|G1.5"}, "stop": False}}}
    for k in (kal, None):
        c = RT.counts_only(RT.run_stage(g, gt, wf, st, k, sel, fair))
        RT.assert_no_pnl_keys(c)
        flat = json.dumps(c)
        for bad in ("mean", "pnl", "wins", "win_rate", "losses", "pushes", "boot", "frac", "verdict", "mean_net"):
            assert bad not in flat
        assert c["A"]["result"]["per_window_sb"]["H1"]["n"] >= 0
    assert "inputs missing" in RT.counts_only(RT.run_stage(g, gt, wf, st, None, sel, fair))["C"]["status"]
    with pytest.raises(AssertionError):
        RT.assert_no_pnl_keys({"A": {"mean": 0.1}})


@pytest.mark.skipif(not (N.MAIN_OUT / "rehearsal_counts.json").exists(), reason="rehearsal not run")
def test_saved_rehearsal_file_has_no_pnl_keys():
    d = json.loads((N.MAIN_OUT / "rehearsal_counts.json").read_text())
    RT.assert_no_pnl_keys(d)


def test_oof_ares_excludes_held_out_season_from_its_own_fit():
    import run_oof_ares as OO
    rng = np.random.default_rng(4)
    n = 600
    fd = pd.DataFrame({f"f_{c}": rng.normal(size=n) for c in BF.RES_FEATURES})
    fd["season"] = np.repeat(np.arange(2006, 2012), 100)
    fd["close"] = 44.0
    fd["total"] = 44.0 + 2.0 * fd.f_gap_epa + rng.normal(0, 5, n)
    p1 = OO.oof_predictions(fd)
    fd2 = fd.copy()
    fd2.loc[fd2.season == 2008, "total"] += 500.0                              # wrecking one season's outcomes must not move ITS predictions
    p2 = OO.oof_predictions(fd2)
    m = fd.season == 2008
    np.testing.assert_allclose(p1[m], p2[m], atol=1e-12)
    assert not np.allclose(p1[~m], p2[~m])


def test_mixed_series_only_kxnfltotal_is_used():
    g, gt, wf, st, kal, fair = make_world()
    base = C.build_snapshots(kal, g)
    # add KXNFLTEAMTOTAL markets/candles for the same games with wild quotes and strikes
    mk = kal["markets"]
    fake = mk.copy()
    fake["ticker"] = fake.ticker.str.replace("K-", "KT-", regex=False)
    fake["event_ticker"] = fake.event_ticker.str.replace("KXNFLTOTAL-", "KXNFLTEAMTOTAL-", regex=False)
    fake["floor_strike"] = fake.floor_strike - 30.0
    mixed = {"markets": pd.concat([mk, fake], ignore_index=True)}
    for t_ in ("c1m", "c1h"):
        c2 = kal[t_].copy()
        c2["ticker"] = c2.ticker.str.replace("K-", "KT-", regex=False)
        c2["yes_bid_close_f"], c2["yes_ask_close_f"] = 0.88, 0.92
        mixed[t_] = pd.concat([kal[t_], c2], ignore_index=True)
    assert C.is_total_series(fake).sum() == 0 and C.is_total_series(mk).all()
    got = C.build_snapshots(mixed, g)
    for tau in base:
        pd.testing.assert_frame_equal(base[tau].reset_index(drop=True), got[tau].reset_index(drop=True))
        assert not got[tau].ticker.str.startswith("KT-").any()
    # bracket check ignores the other series even if its strikes would "fail" it
    gg = pd.DataFrame({"game_id": ["G"], "home_score": [25.0], "away_score": [22.0]})
    both = pd.DataFrame({"ticker": ["KXNFLTOTAL-A-40", "KXNFLTEAMTOTAL-A-60"], "event_ticker": ["KXNFLTOTAL-A", "KXNFLTEAMTOTAL-A"],
                         "floor_strike": [40.5, 60.5], "result": ["yes", "yes"], "game_id": "G", "match_status": "matched"})
    assert GT.bracket_check(both, gg)["pass"] is True


def test_hk_restricted_to_2025_2026_and_unsettled_games_dropped(tmp_path):
    g, gt, wf, st, kal, fair = make_world()
    g2 = g.copy()
    old = g2.iloc[0].copy()
    mk = kal["markets"].copy()
    extra = mk.iloc[:3].copy()
    extra["game_id"] = old.game_id                                           # a 2006 game with Kalshi rows: must be filtered out
    kal2 = dict(kal, markets=pd.concat([mk, extra], ignore_index=True))
    keep, counts = C.restrict_hk(kal2["markets"], g)
    assert set(g.set_index("game_id").loc[keep.game_id.unique(), "season"]) <= {2025, 2026}
    assert counts["n_games_hk_seasons_2025_2026"] == counts["n_games_before_season_filter"] - 1
    # load_kalshi drops games with any non-settled market
    d = tmp_path
    m = pd.DataFrame({"ticker": ["KXNFLTOTAL-X-1", "KXNFLTOTAL-X-2", "KXNFLTOTAL-Y-1"], "event_ticker": ["KXNFLTOTAL-X", "KXNFLTOTAL-X", "KXNFLTOTAL-Y"],
                      "floor_strike": [40.5, 45.5, 40.5], "strike_type": "greater", "result": ["yes", "", "no"], "game_id": ["X", "X", "Y"],
                      "match_status": "matched"})
    m.to_parquet(d / "markets.parquet")
    c = pd.DataFrame({"ticker": ["KXNFLTOTAL-Y-1"], "end_ts": [1], "yes_bid_close_f": [0.5], "yes_ask_close_f": [0.52]})
    c.to_parquet(d / "candles_1m.parquet")
    c.to_parquet(d / "candles_1h.parquet")
    k = C.load_kalshi(d)
    assert set(k["markets"].game_id) == {"Y"} and k["counts"]["n_games_dropped_unsettled"] == 1


def test_side_preserving_luck_and_controls():
    games = pd.DataFrame({"season": 2020, "total": [50.0] * 20 + [30.0] * 20, "close": 40.0})
    bets = pd.DataFrame({"season": 2020, "over": [True] * 5 + [False] * 5, "pnl": 0.0})
    c = N.side_controls(games, bets, 0.0)
    assert c["side_mix"] == {"n_over": 5, "n_under": 5} and c["always_over"]["n"] == 40
    assert c["luck_side_preserving"]["n_bets"] == 10 and c["luck_side_preserving"]["copies"] == 500
    # a version that always bets the era's winning side is NOT separable from the era bias: the share is large
    over_pool = {2020: np.full(40, 10 / 11)}
    under_pool = {2020: np.full(40, -1.0)}
    r = N.luck_side_preserving(over_pool, under_pool, {2020: 10}, {}, 10 / 11)
    assert r["frac_copies_ge_actual"] == 1.0


def test_rehearsal_refuses_after_audit_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(N, "STUDY", tmp_path)
    (tmp_path / "AUDIT_OK").write_text("{}")
    monkeypatch.setattr(sys, "argv", ["run_test.py", "--rehearsal-counts"])
    with pytest.raises(SystemExit, match="REFUSED"):
        RT.main()


def test_b1_clean_set_is_h3_only():
    assert B.CLEAN_SB["B1"] == ("H3",) and B.CLEAN_SB["B4"] == ("D", "H3") and B.CLEAN_SB["B5"] == ("D", "H3")
