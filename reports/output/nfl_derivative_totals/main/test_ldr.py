"""pytest for research log #68 (NFL derivative totals): fees, fair-centre arithmetic, spread sign convention, series-prefix filtering,
team identification, no candle after tau, picker/median reuse, bets, window refusals, guard refusals, agreement sample, bracket gate,
r1 arithmetic, and a full SYNTHETIC end-to-end.  No real outcome is read by any test.

Run:  .venv/Scripts/python.exe -m pytest reports/output/nfl_derivative_totals/main/test_ldr.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gates as GT  # noqa: E402,F401
import ldr_lib as L  # noqa: E402
import run_select as RS  # noqa: E402
import run_test as RT  # noqa: E402

C, N = L.C, L.N
TEAMS = ["AAA", "BBB", "CCC", "DDD"]


# ------------------------------------------------------------------ synthetic world
def sig(x):
    return 1.0 / (1.0 + np.exp(-x))


def cents(x):
    return np.round(np.clip(x, 0.01, 0.99), 2)


def toy_games(n=40):
    """n games, first half in 2025-10 (select window), second half in 2026-02 (test window)."""
    rows = []
    for i in range(n):
        day = pd.Timestamp("2025-10-05 17:00", tz="UTC") + pd.Timedelta(days=7 * i) if i < n // 2 else \
            pd.Timestamp("2026-02-01 17:00", tz="UTC") + pd.Timedelta(days=7 * (i - n // 2))
        h, a = TEAMS[i % 4], TEAMS[(i + 1) % 4]
        rows.append({"game_id": f"G{i:03d}", "season": 2025 if i < n // 2 else 2026, "home": h, "away": a, "kick_s": int(day.timestamp()),
                     "et_date": day.strftime("%Y-%m-%d"), "home_score": 20.0 + (i * 7) % 13, "away_score": 17.0 + (i * 5) % 11,
                     "spread_line": 3.0})
    return pd.DataFrame(rows)


def ladder_quotes(strikes, center, scale, half=0.02):
    mid = sig((center - np.asarray(strikes, float)) / scale)          # P(X > strike)
    return cents(mid - half), cents(mid + half)


def synth_kalshi(games, M, S, D_off, r1=0.5, leak=True):
    """Markets + candles for the four series.  M, S: dicts game_id -> centre.  D_off: dict ladder -> offset added to the fair centre."""
    mk, c1m, c1h = [], [], []

    def add(skey, ser, gid, suffix, strike, bid, ask, g):
        ev = f"{ser}-{gid}" if skey != "tt" else f"{ser}-{gid}"
        t = f"{ev}-{suffix}"
        mk.append({"ticker": t, "event_ticker": ev, "series": ser, "floor_strike": float(strike), "strike_type": "greater", "result": "yes",
                   "game_id": gid, "match_status": "matched", "ticker_suffix": suffix, "home_code": g.home, "away_code": g.away, "skey": skey})
        for tau, tgt, off in (("T60", c1m, 3600), ("T24h", c1h, 86400)):
            dec = g.kick_s - off
            tgt.append({"ticker": t, "end_ts": dec - 300, "yes_bid_close_f": bid, "yes_ask_close_f": ask})
            if leak:                                               # a wildly different candle AFTER the decision: must never be used
                tgt.append({"ticker": t, "end_ts": dec + 60, "yes_bid_close_f": 0.01, "yes_ask_close_f": 0.02})

    for g in games.itertuples():
        m, s = M[g.game_id], S[g.game_id]
        fair = L.fair_centres(m, s, r1)
        for skey, lad, ser, centre in (("tot", "tot", "KXNFLTOTAL", m), ("h1", "h1", "KXNFL1HTOTAL", fair["h1"] + D_off.get("h1", 0)),
                                       ("h2", "h2", "KXNFL2HTOTAL", fair["h2"] + D_off.get("h2", 0))):
            ks = np.arange(np.floor(centre) - 9.5, centre + 10, 1.0)
            b, a = ladder_quotes(ks, centre, 4.0)
            for k, bb, aa in zip(ks, b, a):
                add(skey, ser, g.game_id, f"{int(k + 0.5)}", k, bb, aa, g)
        for lad, code in (("tt_home", g.home), ("tt_away", g.away)):
            centre = fair[lad] + D_off.get(lad, 0)
            ks = np.arange(np.floor(centre) - 9.5, centre + 10, 1.0)
            b, a = ladder_quotes(ks, centre, 3.0)
            for k, bb, aa in zip(ks, b, a):
                add("tt", "KXNFLTEAMTOTAL", g.game_id, f"{code}{int(k + 0.5)}", k, bb, aa, g)
    return {"markets": pd.DataFrame(mk), "c1m": pd.DataFrame(c1m), "c1h": pd.DataFrame(c1h)}


def synth_spread(games, S):
    """Spread snapshots (the frames spread_snapshot_frames returns) for both taus: home-margin P(Mh > x) = sig((S - x)/4)."""
    mk, q = [], []
    mid = 0
    for g in games.itertuples():
        for team, side in ((g.home, "home"), (g.away, "away")):
            for K in np.arange(0.5, 15, 1.0):
                mid += 1
                p_home_gt = sig((S[g.game_id] - K) / 4.0) if side == "home" else 1.0 - sig((S[g.game_id] + K) / 4.0)
                b, a = cents(p_home_gt - 0.02), cents(p_home_gt + 0.02)
                mk.append({"mid": mid, "game_id": g.game_id, "team": team, "strike": K})
                for off in (3600, 86400):
                    q.append({"mid": mid, "ts": g.kick_s - off - 120, "bid_e4": int(round(b * 1e4)), "ask_e4": int(round(a * 1e4))})
                    q.append({"mid": mid, "ts": g.kick_s - off + 30, "bid_e4": 100, "ask_e4": 200})        # after the decision: must be ignored
    mk, q = pd.DataFrame(mk), pd.DataFrame(q)
    kick = games.set_index("game_id").kick_s
    return {tau: L.spread_snapshot_frames(mk, q, kick, tau) for tau in L.TAUS}


def toy_world(n=40, off=None, seed=3):
    g = toy_games(n)
    rng = np.random.default_rng(seed)
    M = {x: 42.0 + float(rng.integers(-4, 5)) + 0.25 for x in g.game_id}
    S = {x: float(rng.integers(-9, 10)) + 0.25 for x in g.game_id}
    kal = synth_kalshi(g, M, S, off or {})
    sp = synth_spread(g, S)
    return g, M, S, kal, sp


# ------------------------------------------------------------------ fees
def test_fee_arithmetic():
    assert N.kalshi_fee(0.41) == 0.0170                       # 0.07*0.41*0.59 = 0.016933 -> up to 0.0170
    assert N.kalshi_fee(0.30) == 0.0147
    assert N.kalshi_fee(0.50) == 0.0175
    assert N.kalshi_net(0.40, 1) == pytest.approx(1 - 0.41 - 0.0170)
    assert N.kalshi_net(0.40, 0) == pytest.approx(-0.41 - 0.0170)


# ------------------------------------------------------------------ fair centres
def test_fair_centre_arithmetic():
    f = L.fair_centres(44.0, 6.0, 0.5)
    assert f["tt_home"] == 25.0 and f["tt_away"] == 19.0
    assert f["tt_home"] + f["tt_away"] == 44.0 and f["tt_home"] - f["tt_away"] == 6.0
    f = L.fair_centres(44.0, -3.0, 0.5077)
    assert f["h1"] == pytest.approx(0.5077 * 44.0) and f["h1"] + f["h2"] == pytest.approx(44.0)


# ------------------------------------------------------------------ sign convention
def test_home_margin_points_transform():
    x, b, a = L.home_margin_points([True, False], [3.5, 3.5], [0.60, 0.20], [0.64, 0.26])
    assert list(x) == [3.5, -3.5]
    assert b[0] == 0.60 and a[0] == 0.64
    assert b[1] == pytest.approx(0.74) and a[1] == pytest.approx(0.80)            # 1 - ask, 1 - bid


def test_sign_convention_home_favourite_positive():
    g = toy_games(4)
    S = {x: v for x, v in zip(g.game_id, [7.25, -7.25, 0.25, 12.25])}
    sp = synth_spread(g, S)
    for tau in L.TAUS:
        got = L.spread_S(sp[tau], g)
        for gid, s in S.items():
            assert got[gid] == pytest.approx(s, abs=0.35)
        assert got["G000"] > 0 > got["G001"]                                          # home favourite -> S > 0, away favourite -> S < 0


def test_sign_convention_only_one_team_ladder_present():
    """Only the away-team ladder (away favoured by 6.5): S must still come out negative."""
    g = toy_games(1)
    S = {"G000": -6.5}
    sp = synth_spread(g, S)["T60"]
    away_only = sp[sp.team == g.away.iloc[0]]
    assert L.spread_S(away_only, g)["G000"] < 0


def test_spread_snapshot_ignores_quote_after_decision():
    g = toy_games(2)
    sp = synth_spread(g, {"G000": 3.25, "G001": 3.25})
    assert (sp["T60"].ts <= sp["T60"].decision_s).all() and (sp["T24h"].ts <= sp["T24h"].decision_s).all()
    assert not ((sp["T60"].bid == 0.01) & (sp["T60"].ask == 0.02)).any()               # the 1c/2c post-decision quote never selected


# ------------------------------------------------------------------ series filtering and team identification
def test_series_prefix_filter():
    df = pd.DataFrame({"event_ticker": ["KXNFLTOTAL-26OCT26DALPHI", "KXNFLTEAMTOTAL-26OCT26DALPHI", "KXNFL1HTOTAL-26OCT26DALPHI",
                                        "KXNFL2HTOTAL-26OCT26DALPHI", "KXNFLTOTALX-26OCT26DALPHI"]})
    assert df[L.series_mask(df, "KXNFLTOTAL")].index.tolist() == [0]
    assert df[L.series_mask(df, "KXNFLTEAMTOTAL")].index.tolist() == [1]
    assert df[L.series_mask(df, "KXNFL1HTOTAL")].index.tolist() == [2]
    assert df[L.series_mask(df, "KXNFL2HTOTAL")].index.tolist() == [3]
    df["series"] = ["KXNFLTOTAL", "KXNFLTOTAL", "x", "x", "x"]                       # an explicit series column must also agree
    assert df[L.series_mask(df, "KXNFLTEAMTOTAL")].empty
    assert C.is_total_series(df.iloc[[0, 1]]).tolist() == [True, False]               # the #66 filter agrees on KXNFLTOTAL


def test_team_side():
    assert L.team_side("PHI43", "PHI", "DAL") == "home"
    assert L.team_side("DAL43", "PHI", "DAL") == "away"
    assert L.team_side("LAR41", "LA", "SEA") == "home"                                # Kalshi alias LAR -> nflverse LA
    assert L.team_side("JAC20", "CLE", "JAX") == "away"
    assert L.team_side("XXX43", "PHI", "DAL") is None
    assert L.team_side("43", "PHI", "DAL") is None


def test_tt_ladders_tagged_home_away_and_other_series_untouched():
    g, M, S, kal, sp = toy_world(4)
    mk = kal["markets"]
    tt = L.tag_ladders(mk[mk.skey == "tt"], "tt", g)
    assert set(tt.ladder) == {"tt_home", "tt_away"}
    gi = g.set_index("game_id")
    th = tt[tt.ladder == "tt_home"]
    assert all(sfx.startswith(h) for sfx, h in zip(th.ticker_suffix, th.game_id.map(gi.home)))
    assert (L.tag_ladders(mk[mk.skey == "h1"], "h1", g).ladder == "h1").all()


# ------------------------------------------------------------------ no candle after tau; picker reuse
def test_no_candle_after_tau_and_leak_ignored():
    g, M, S, kal, sp = toy_world(4)
    for tau in L.TAUS:
        s = L.snapshots_for(kal, g, tau)
        assert len(s) > 0 and (s.end_ts <= s.decision_s).all()
        assert not ((s.bid == 0.01) & (s.ask == 0.02)).any()                          # the post-decision leak candle (0.01/0.02) never selected


def test_snapshot_assertion_trips_on_future_candle(monkeypatch):
    g, M, S, kal, sp = toy_world(2)
    real = C.snapshot

    def bad(markets, candles, kick, tau):
        s = real(markets, candles, kick, tau)
        s["end_ts"] = s.decision_s + 1
        return s
    monkeypatch.setattr(C, "snapshot", bad)
    with pytest.raises(AssertionError):
        L.snapshots_for(kal, g, "T60")


def test_picker_and_median_reuse():
    g, M, S, kal, sp = toy_world(3)
    snap = L.snapshots_for(kal, g, "T60")
    lt = L.ladder_table(snap)
    for (gid, lad), d in snap.groupby(["game_id", "ladder"]):
        row = lt[(lt.game_id == gid) & (lt.ladder == lad)].iloc[0]
        i = C.pick_main_strike(d.strike.values, d.bid.values, d.ask.values)
        assert row.K == d.strike.values[i]
        ok = (d.ask - d.bid) <= 0.10 + 1e-9
        assert row.D == pytest.approx(C.implied_median(d.strike.values[ok], ((d.bid + d.ask) / 2).values[ok]))
    # the median of a 'tot' ladder is close to its construction centre
    for gid in g.game_id:
        assert lt[(lt.game_id == gid) & (lt.ladder == "tot")].D.iloc[0] == pytest.approx(M[gid], abs=0.4)


def test_wide_strikes_excluded_from_median():
    x = np.array([40.5, 41.5, 42.5, 43.5])
    bid = np.array([0.80, 0.55, 0.10, 0.20])
    ask = np.array([0.84, 0.59, 0.50, 0.24])                   # 42.5 has a 40c spread: dropped; median interpolates 41.5 -> 43.5
    m = L.ladder_median(x, bid, ask)
    assert m == pytest.approx(41.5 + (0.57 - 0.5) / (0.57 - 0.22) * 2.0)


# ------------------------------------------------------------------ bets
def test_side_for_thresholds():
    assert L.side_for(1.0, 1) == "NO" and L.side_for(0.9999999999, 1) == "NO"
    assert L.side_for(0.99, 1) is None and L.side_for(-0.99, 1) is None
    assert L.side_for(-1.0, 1) == "YES" and L.side_for(-3.2, 3) == "YES" and L.side_for(2.9, 3) is None
    assert L.side_for(float("nan"), 1) is None


def _dec_row(**kw):
    r = {"game_id": "G0", "tau": "T60", "ladder": "h1", "family": "half", "D": 24.0, "fair": 22.0, "gap": 2.0, "K": 23.5, "bid": 0.47,
         "ask": 0.51, "usable": True, "actual": 20.0}
    r.update(kw)
    return pd.DataFrame([r])


def test_bet_buy_no_when_gap_positive_and_fee_arithmetic():
    b = L.bets_of(_dec_row(), "half|T60|g2")
    assert len(b) == 1 and b.side[0] == "NO"
    ask_no = 1 - 0.47                                          # NO taker ask = 1 - yes bid
    P = ask_no + 0.01
    assert b.ask_side[0] == pytest.approx(ask_no) and b.win[0] == 1.0                   # actual 20 < 23.5 -> NO wins
    assert b.net[0] == pytest.approx(1 - P - N.kalshi_fee(P))
    assert len(L.bets_of(_dec_row(), "half|T60|g3")) == 0                             # gap 2 < 3
    assert len(L.bets_of(_dec_row(), "tt|T60|g2")) == 0                               # other family
    assert len(L.bets_of(_dec_row(), "half|T24h|g2")) == 0                            # other tau


def test_bet_buy_yes_when_gap_negative():
    b = L.bets_of(_dec_row(gap=-1.5, actual=30.0), "half|T60|g1")
    assert b.side[0] == "YES" and b.ask_side[0] == 0.51 and b.win[0] == 1.0
    P = 0.52
    assert b.net[0] == pytest.approx(1 - P - N.kalshi_fee(P))
    b = L.bets_of(_dec_row(gap=-1.5, actual=23.5), "half|T60|g1")                      # actual == K: YES needs actual > K -> loses
    assert b.win[0] == 0.0


def test_unusable_rows_never_bet():
    assert len(L.bets_of(_dec_row(usable=False), "half|T60|g1")) == 0


def test_twelve_versions():
    v = L.all_versions()
    assert len(v) == 12 and len(set(v)) == 12 and v[0] == "tt|T24h|g1" and v[-1] == "half|T60|g3"


# ------------------------------------------------------------------ windows
def test_attach_actual_refuses_other_window():
    g, M, S, kal, sp = toy_world(8)
    tabs = L.build_tables(kal, g, sp, 0.5)
    dec = tabs["dec"]
    act = L.actuals_table(g, pd.DataFrame({"game_id": g.game_id, "h1_home": 10.0, "h1_away": 7.0}))
    sel, tst = L.window_rows(dec, "select"), L.window_rows(dec, "test")
    assert len(sel) > 0 and len(tst) > 0
    with pytest.raises(RuntimeError):
        L.attach_actual(dec, act, "select")                    # contains test rows
    with pytest.raises(RuntimeError):
        L.attach_actual(sel, act, "test")
    assert "actual" in L.attach_actual(sel, act, "select").columns
    assert set(sel.et_date) < set(dec.et_date) and sel.et_date.max() < L.SPLIT_DAY <= tst.et_date.min()


# ------------------------------------------------------------------ r1, pbp halves, settlement
def test_last_h1_play_score_and_r1_arithmetic():
    # game A: last qtr<=2 play (id 20) ends 7-0; the Q3 kickoff play (id 30, NOT passed: qtr 3) would show a return TD 14-0 -> must not count
    p = pd.DataFrame({"game_id": ["A", "A", "B", "B"], "play_id": [20, 10, 8, 5], "total_home_score": [7.0, 3.0, 3.0, 0.0],
                      "total_away_score": [0.0, 0.0, 0.0, 0.0]})
    h = L.last_h1_scores(p)
    assert h.set_index("game_id").loc["A"].tolist() == [7.0, 0.0] and h.set_index("game_id").loc["B"].tolist() == [3.0, 0.0]
    games = pd.DataFrame({"game_id": ["A", "B", "C"], "season": [2015, 2016, 2016], "home_score": [14.0, 6.0, 20.0], "away_score": [14.0, 6.0, 0.0]})
    r = L.fit_r1(games, h)                                     # A: 7/28 = 0.25, B: 3/12 = 0.25, C has no half -> dropped
    assert r["r1"] == pytest.approx(0.25) and r["n_games"] == 2 and r["n_dropped"] == 1


def test_load_halves_ignores_kickoff_return_td_in_q3(tmp_path):
    rows = [(1, 1, 0, 0), (2, 2, 7, 0), (3, 2, 7, 3), (4, 3, 14, 3)]          # play 4 = Q3 kickoff, returned for a TD: 14-3 after the play
    df = pd.DataFrame(rows, columns=["play_id", "qtr", "total_home_score", "total_away_score"])
    df["game_id"] = "X"
    df.to_parquet(tmp_path / "play_by_play_2020.parquet")
    h = L.load_halves([2020], pbp_dir=tmp_path, exclude=set())
    assert h.iloc[0].h1_home == 7 and h.iloc[0].h1_away == 3


def test_unsettled_derivative_ladders_dropped():
    mk = pd.DataFrame({"skey": ["h1", "h1", "h1", "h2", "h2", "tot", "tot", "tt"], "game_id": ["A", "A", "B", "A", "A", "A", "A", "A"],
                       "result": ["yes", "no", "yes", "yes", "", "", "yes", "no"]})
    out = L.drop_unsettled_derivative(mk)
    assert out.groupby(["skey", "game_id"]).size().to_dict() == {("h1", "A"): 2, ("h1", "B"): 1, ("tot", "A"): 2, ("tt", "A"): 1}


def test_actuals_table_h2_includes_ot_and_team_totals():
    g = pd.DataFrame({"game_id": ["A"], "home_score": [30.0], "away_score": [27.0]})
    h = pd.DataFrame({"game_id": ["A"], "h1_home": [10.0], "h1_away": [14.0]})
    a = L.actuals_table(g, h)
    assert a.loc["A", "total"] == 57 and a.loc["A", "h1"] == 24 and a.loc["A", "h2"] == 33 and a.loc["A", "home"] == 30 and a.loc["A", "away"] == 27


def test_bracket_gate_toy():
    mk = pd.DataFrame({"skey": ["h1"] * 4, "ladder": ["h1"] * 4, "game_id": ["A"] * 4, "floor_strike": [20.5, 23.5, 24.5, 27.5],
                       "result": ["yes", "yes", "no", "no"], "event_ticker": ["KXNFL1HTOTAL-A"] * 4})
    act = pd.DataFrame({"h1": [24.0]}, index=["A"])
    assert L.bracket_gate(mk, act).ok.iloc[0] == True                                  # noqa: E712 (23.5 < 24 <= 24.5)
    act2 = pd.DataFrame({"h1": [26.0]}, index=["A"])
    assert L.bracket_gate(mk, act2).ok.iloc[0] == False                                # noqa: E712


# ------------------------------------------------------------------ guards
def test_run_select_refuses_when_done(tmp_path):
    for f in ("results_select.json", "AUDIT_OK", "results_test.json", "test_started.json"):
        d = tmp_path / ("d_" + f.replace(".", "_"))
        d.mkdir()
        (d / f).write_text("{}")
        with pytest.raises(SystemExit):
            RS.refuse_if_done(d)
    RS.refuse_if_done(tmp_path)                                # empty folder -> no refusal


def test_run_test_refuses_without_audit(tmp_path):
    with pytest.raises(SystemExit):
        RT.check_gates(tmp_path, HERE, ("ldr_lib.py",))        # no AUDIT_OK
    (tmp_path / "AUDIT_OK").write_text(json.dumps({"sha256": {"ldr_lib.py": L.N.sha256_file(HERE / "ldr_lib.py")}}))
    with pytest.raises(SystemExit):
        RT.check_gates(tmp_path, HERE, ("ldr_lib.py",))        # no test_started.json
    (tmp_path / "test_started.json").write_text("{}")
    with pytest.raises(SystemExit):
        RT.check_gates(tmp_path, HERE, ("ldr_lib.py",))        # no results_select.json
    (tmp_path / "results_select.json").write_text(json.dumps({"chosen": None, "stop": True}))
    assert RT.check_gates(tmp_path, HERE, ("ldr_lib.py",))["stop"] is True
    (tmp_path / "results_test.json").write_text("{}")
    with pytest.raises(SystemExit):
        RT.check_gates(tmp_path, HERE, ("ldr_lib.py",))        # holdout already run
    (tmp_path / "results_test.json").unlink()
    (tmp_path / "AUDIT_OK").write_text(json.dumps({"sha256": {"ldr_lib.py": "0" * 64}}))
    with pytest.raises(SystemExit):
        RT.check_gates(tmp_path, HERE, ("ldr_lib.py",))        # hash mismatch


def test_real_study_folder_refuses_run_test():
    assert not (L.STUDY / "AUDIT_OK").exists() or (L.STUDY / "test_started.json").exists()
    if not (L.STUDY / "AUDIT_OK").exists():
        with pytest.raises(SystemExit):
            RT.check_gates()


def test_audit_file_list_covers_code_and_inputs():
    f = set(L.AUDIT_FILES)
    for need in ("ldr_lib.py", "run_select.py", "run_test.py", "../results_select.json", "../main_out/r1.json", "../../nfl_totals/main/part_c.py",
                 "../../nfl_totals/main/nfl_lib.py", "../../../../data/nfl_spread.sqlite", "../../nfl_totals/kalshi/candles_1m.parquet"):
        assert need in f


# ------------------------------------------------------------------ agreement sample
def test_agree_sample_deterministic_and_price_only():
    g, M, S, kal, sp = toy_world(60, off={"h1": 2.0, "tt_home": -2.0})
    dec = L.build_tables(kal, g, sp, 0.5)["dec"]
    a1, a2 = L.agree_sample(dec), L.agree_sample(dec)
    assert a1.equals(a2)
    assert a1[["game_id", "tau", "family"]].drop_duplicates().shape[0] == 100
    assert not {"actual", "net", "win"} & set(a1.columns)
    assert set(a1.side_g1) <= {"YES", "NO", "none"}
    assert len(L.agree_sample(dec, n=5, seed=1)[["game_id", "tau", "family"]].drop_duplicates()) == 5


# ------------------------------------------------------------------ full synthetic end-to-end
def synth_actuals(g, seed=5):
    rng = np.random.default_rng(seed)
    n = len(g)
    home = rng.integers(10, 35, n).astype(float)
    away = rng.integers(10, 35, n).astype(float)
    h1h, h1a = np.minimum(home, rng.integers(0, 20, n)), np.minimum(away, rng.integers(0, 20, n))
    return L.actuals_table(g.assign(home_score=home, away_score=away), pd.DataFrame({"game_id": g.game_id, "h1_home": h1h, "h1_away": h1a}))


def test_end_to_end_synthetic(monkeypatch):
    off = {"h1": 3.0, "h2": -3.0, "tt_home": 2.5, "tt_away": -2.5}                   # derivative ladders centred away from the fair centre
    g, M, S, kal, sp = toy_world(80, off=off)
    tabs = L.build_tables(kal, g, sp, 0.5)
    dec = tabs["dec"]
    # fair centres + gaps match the construction (D = fair + offset, up to ladder interpolation error)
    u = dec[dec.usable]
    assert len(u) > 0.9 * len(dec)
    for lad, o in off.items():
        d = u[u.ladder == lad]
        assert d.gap.median() == pytest.approx(o, abs=0.5)
        np.testing.assert_allclose(d.fair.values, [L.fair_centres(M[x], S[x], 0.5)[lad] for x in d.game_id], atol=0.5)
    # usable counts are per series / season / tau and cover the 4 series + deep markets
    uc = L.usable_counts(dec, tabs["inputs"], g)
    assert {"KXNFLTEAMTOTAL", "KXNFL1HTOTAL", "KXNFL2HTOTAL", "KXNFLTOTAL(M)", "KXNFLSPREAD(S)"} <= set(uc.series)
    assert set(uc.tau) == set(L.TAUS) and set(uc.season) == {2025, 2026}
    # selection on the select window, evaluation on the test window
    act = synth_actuals(g)
    sel_dec = L.attach_actual(L.window_rows(dec, "select"), act, "select")
    monkeypatch.setattr(L, "MIN_BETS", 5)
    res = RS.select(sel_dec)
    assert len(res["rows"]) == 12 and sum(r["n"] for r in res["rows"]) > 0
    assert res["chosen"] is not None
    sel = dict(res, stop=False)                                   # force the holdout path whatever the synthetic sign of the mean
    test_dec = L.window_rows(dec, "test")
    st = tabs["strikes"]
    st = st[[L.window_of_day(g.set_index("game_id").et_date[x]) == "test" for x in st.game_id]]
    out = RT.run_stage(test_dec, st, act, sel)
    assert out["status"] == "run" and out["result"]["verdict"] in ("PASS", "PROMISING", "FAIL")
    assert out["result"]["luck"]["copies"] == 500 and out["result"]["n"] >= 1
    # STOP: no holdout evaluated
    stop = RT.run_stage(test_dec, st, act, {"chosen": None, "stop": True})
    assert stop["status"].startswith("STOPPED")
    # the selection window never carries a test-window game
    assert sel_dec.et_date.max() < L.SPLIT_DAY


def test_end_to_end_bets_hit_known_arithmetic():
    """One hand-built row through bets_of with a real decision table row: net equals the fee formula."""
    g, M, S, kal, sp = toy_world(2, off={"h1": 4.0})
    dec = L.build_tables(kal, g, sp, 0.5)["dec"]
    row = dec[(dec.ladder == "h1") & (dec.tau == "T60") & dec.usable].iloc[[0]].copy()
    assert row.gap.iloc[0] == pytest.approx(4.0, abs=0.5)                              # D above fair -> buy NO
    row["actual"] = row.K.iloc[0] - 3.0
    b = L.bets_of(row, "half|T60|g3")
    assert b.side[0] == "NO" and b.win[0] == 1.0
    P = (1 - row.bid.iloc[0]) + 0.01
    assert b.net[0] == pytest.approx(1 - P - N.kalshi_fee(P))
