"""Unit tests for research log #56 (toy data; the few real-data tests use SELECTION-period or identity checks only and
never print or assert on a confirmation-season outcome)."""
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fa_lib as fa  # noqa: E402
import grid_lib as gl  # noqa: E402
import run_select as rs  # noqa: E402
import run_confirm as rc  # noqa: E402
import nba_official as no  # noqa: E402
import data_checks as dc  # noqa: E402


# ------------------------------------------------------------------ seasons
def test_season_labels():
    assert fa.year_of("2022-23") == 2023 and fa.year_of("2025-26") == 2026 and fa.year_of("1999-00") == 2000
    assert fa.label_of(2023) == "2022-23" and fa.label_of(2027) == "2026-27"
    assert fa.prev_season("2022-23") == "2021-22"
    with pytest.raises(AssertionError):
        fa.year_of("2022-24")
    assert fa.SELECTION == ("2022-23", "2023-24") and fa.CONFIRM == ("2024-25", "2025-26")


# ------------------------------------------------------------------ no look-ahead on real inputs
def _logging_reader(log, tweak=None):
    def reader(year, kind="team"):
        log.append(int(year))
        df = pd.read_parquet(fa.BOX / f"{kind}_box_{year}.parquet")
        return tweak(year, df) if tweak else df
    return reader


def test_world_reads_only_prior_and_current_and_fit_files():
    log = []
    w = fa.build_world("2023-24", _logging_reader(log))
    assert set(log) == {2022, 2023, 2024}          # S-2 (fit prior), S-1 (prior + fit season), S (current) - never later
    assert "fitted on 2022-23" in w.const_note
    log2 = []
    w2 = fa.build_world("2022-23", _logging_reader(log2))
    assert set(log2) == {2022, 2023} and "no prior-season blend" in w2.const_note


def test_constants_come_from_previous_season_only():
    """Scrambling every score of the current season S leaves S's constants and four-factor fit unchanged."""
    def scramble(year, df):
        if year == 2023:                                     # current season of 2022-23
            df = df.copy()
            df["team_score"] = df.team_score * 1.5 + 7
            df["opponent_team_score"] = df.opponent_team_score * 0.5
        return df
    a = fa.build_world("2022-23")
    b = fa.build_world("2022-23", _logging_reader([], scramble))
    assert np.array_equal(a.consts.b, b.consts.b) and np.array_equal(a.consts.c_b2b, b.consts.c_b2b)
    assert np.array_equal(a.consts.e_cat, b.consts.e_cat) and np.array_equal(a.ff_coef, b.ff_coef)
    assert not np.array_equal(a.cur_tg.pts.values, b.cur_tg.pts.values)


def test_current_rows_strictly_before_game_date():
    """Changing every score on/after the decision date D leaves a game's projection (all 10 rating families) unchanged."""
    w = fa.build_world("2022-23", with_consts=False)
    g = fa.games_frame(w)
    D = pd.Timestamp("2023-01-20")
    sub = g[g.date == D].reset_index(drop=True)
    assert len(sub) >= 3
    consts = gl.Consts(np.zeros((7, 10)), np.zeros((7, 10, 3)), np.ones((7, 10)))
    spec = gl.spec_from_names(dict(gl.DEFAULTS, C="kenpom"))
    base = [gl.project_one(w.season, sub, consts, gl.spec_from_names(dict(gl.DEFAULTS, C=c))) for c in gl.C_OPTS]
    cur2 = w.cur_tg.copy()
    late = cur2.date >= D
    for c in ("pts", "opp_pts", "efgnum", "o_efgnum", "poss", "opp_poss", "tov", "oreb"):
        if c in cur2:
            cur2.loc[late, c] = cur2.loc[late, c] * 3.0 + 11
    cur2["pace"] = (cur2.poss + cur2.opp_poss) / 2.0
    s2 = gl.Season(w.prev_tg, cur2, w.ff_coef)
    after = [gl.project_one(s2, sub, consts, gl.spec_from_names(dict(gl.DEFAULTS, C=c))) for c in gl.C_OPTS]
    for c, x, y in zip(gl.C_OPTS, base, after):
        assert np.array_equal(x, y), c
    # and a change BEFORE D does move them (the test can fail)
    cur3 = w.cur_tg.copy()
    early = cur3.date < D
    cur3.loc[early, "pts"] = cur3.loc[early, "pts"] * 1.2
    s3 = gl.Season(w.prev_tg, cur3, w.ff_coef)
    assert not np.array_equal(gl.project_one(s3, sub, consts, spec), base[gl.C_OPTS.index("kenpom")])


def test_games_frame_has_no_outcome_columns_and_regular_season_only():
    w = fa.build_world("2022-23", with_consts=False)
    g = fa.games_frame(w)
    assert set(g.columns) == {"game_id", "date", "tip", "T", "away", "home", "season"}
    assert len(g) == 1230 and g.game_id.is_unique and (g["T"] == g["tip"]).all()
    a = fa.actuals_frame(w)
    assert set(g.game_id) <= set(a.index) and (a > 120).all()


# ------------------------------------------------------------------ selection-season filter
def test_selection_stage_only_reads_selection_files_and_refuses_other_seasons():
    log = []
    with pytest.raises(AssertionError):
        rs.season_stage("2024-25")
    with pytest.raises(AssertionError):
        rs.season_stage("2025-26")
    st = rs.season_stage("2022-23", _logging_reader(log), keep_P=False)
    assert set(log) <= {2022, 2023}
    assert st["sums"]["n"] == 1230 and st["has_line"].sum() > 1000
    assert st["sums"]["sabs"].shape == (gl.N_FORMULAS,)


# ------------------------------------------------------------------ thresholds, pushes, lean, wilson
def test_wilson_known_value():
    lo, hi = fa.wilson(50, 100)
    assert abs(lo - 0.4038) < 1e-3 and abs(hi - 0.5962) < 1e-3
    assert fa.wilson(0, 0)[0] != fa.wilson(0, 0)[0]            # NaN


def test_side_table_pushes_excluded_and_sides():
    line = np.array([220.0, 220, 220, 220, 220, 220])
    proj = np.array([226.0, 226, 214, 214, 226, 221])           # over, over, under, under, over, tiny gap
    actual = np.array([225.0, 220, 210, 230, 219, 300])         # win, PUSH, win, loss, loss, (not a bet at T=4)
    t = fa.side_table(proj, line, actual, 4)
    assert t["n"] == 4 and t["wins"] == 2 and t["pushes"] == 1 and abs(t["rate"] - 0.5) < 1e-12
    t0 = fa.side_table(proj, line, actual, 0)
    assert t0["n"] == 5                                         # 6 rows, 1 push
    # proj == line: no side, no bet
    assert fa.side_table(np.array([220.0]), np.array([220.0]), np.array([230.0]), 0)["n"] == 0
    # |d| exactly T is included
    assert fa.side_table(np.array([224.0]), np.array([220.0]), np.array([230.0]), 4)["n"] == 1


def test_select_threshold_min_n_and_highest_lb():
    rng = np.random.default_rng(1)
    N = 4000
    line = np.full(N, 220.0)
    d = rng.normal(0, 6, N)
    proj = line + d
    # outcomes follow the formula side with probability rising with |d| -> larger T has a higher rate but fewer bets
    p = np.clip(0.5 + 0.03 * np.abs(d), 0.5, 0.9)
    follow = rng.random(N) < p
    actual = np.where(follow == (d > 0), 230.0, 210.0)
    tab, best = fa.select_threshold(proj, line, actual)
    assert [t["T"] for t in tab] == [2, 4, 6, 8]
    assert best is not None and best["n"] >= 150
    assert best["lb"] == max(t["lb"] for t in tab if t["n"] >= 150)
    # min_n filter: nothing eligible
    tab2, best2 = fa.select_threshold(proj[:200], line[:200], actual[:200], min_n=10 ** 6)
    assert best2 is None
    # a T whose n < 150 can never win even with a perfect rate
    pr = np.concatenate([np.full(149, 230.0), np.full(2000, 221.0)])
    ln = np.full(len(pr), 220.0)
    ac = np.concatenate([np.full(149, 240.0), np.full(2000, 150.0)])      # T=8 bets: 149, all win; T=2 bets: 2149 mostly lose
    _, b3 = fa.select_threshold(pr, ln, ac)
    assert b3 is None or b3["T"] != 8


def test_choose_primary():
    forms = {"a": {"best": {"lb": 0.51, "T": 4}}, "b": {"best": {"lb": 0.53, "T": 6}}, "c": {"best": None}}
    assert fa.choose_primary(forms) == "b"
    assert fa.choose_primary({"c": {"best": None}}) is None


def test_lean_shift_and_corrected_form():
    proj = np.array([220.0, 224.0, 228.0])
    actual = np.array([224.0, 226.0, 233.0])
    assert abs(fa.lean_shift(proj, actual) - 11 / 3) < 1e-12
    m = fa.mae_rmse_bias(proj, actual)
    assert abs(m["bias"] + 11 / 3) < 1e-12 and abs(m["mae"] - 11 / 3) < 1e-12        # bias = proj - actual
    m2 = fa.mae_rmse_bias(proj + fa.lean_shift(proj, actual), actual)
    assert abs(m2["bias"]) < 1e-12
    blk = rs.form_block("x", "team", np.array([220.0, np.nan, 228.0]), 1.0, np.array([224.0, 1.0, 233.0]), np.array([221.0, 1.0, 230.0]))
    assert blk["games_dropped_nonfinite"] == 1 and blk["accuracy_all_games"]["n"] == 2


def test_season_sums_and_pool():
    P = np.array([[10.0, 20.0, 30.0], [11.0, 19.0, 33.0]])
    act = np.array([12.0, 18.0, 30.0])
    hl = np.array([True, False, True])
    s = fa.season_sums(P, act, hl)
    assert np.allclose(s["sabs"], [4, 5]) and np.allclose(s["se"], [0, 3]) and np.allclose(s["sabs_line"], [2, 4])
    assert np.allclose(s["ssq"], [8, 11])
    p = fa.pool_sums([s, s])
    assert p["n"] == 6 and np.allclose(p["mae"], s["sabs"] / 3) and p["n_line"] == 4
    assert list(fa.rank_formulas(p)) == [0, 1]


# ------------------------------------------------------------------ bootstrap
def test_bootstrap_deterministic_and_paired():
    rng = np.random.default_rng(3)
    act = rng.normal(225, 15, 800)
    line = act + rng.normal(0, 14, 800)
    proj = act + rng.normal(0, 16, 800)
    a = fa.paired_boot_mae_diff(proj, line, act, n_boot=1000)
    b = fa.paired_boot_mae_diff(proj, line, act, n_boot=1000)
    assert a == b and a[1] < a[0] < a[2]
    assert fa.SEED == 20261022 and fa.N_BOOT == 10000
    exact = float(np.mean(np.abs(proj - act) - np.abs(line - act)))
    assert abs(a[0] - exact) < 1e-12
    assert fa.boot_mean(np.zeros(10), 200)[1:] == (0.0, 0.0)


# ------------------------------------------------------------------ gates and hashes
def _frozen_dir(tmp_path, audit=True, results=False, sha="ok"):
    fz = tmp_path / "frozen.json"
    fz.write_text(json.dumps({"primary": "team_raw"}))
    if sha == "ok":
        (tmp_path / "frozen.sha256").write_text(hashlib.sha256(fz.read_bytes()).hexdigest() + "  frozen.json\n")
    elif sha == "bad":
        (tmp_path / "frozen.sha256").write_text("0" * 64 + "  frozen.json\n")
    if audit:
        (tmp_path / "AUDIT_OK").write_text(json.dumps(fa.audit_hashes()))
    if results:
        (tmp_path / "results_confirm.json").write_text("{}")
    return tmp_path


def test_confirm_gates(tmp_path):
    d = _frozen_dir(tmp_path)
    assert fa.check_confirm_gates(d) == {"primary": "team_raw"}
    for kw, msg in ((dict(audit=False), "AUDIT_OK"), (dict(results=True), "results_confirm"), (dict(sha="bad"), "hash"),
                    (dict(sha="missing"), "frozen.sha256")):
        d2 = tmp_path / ("c" + str(abs(hash(msg))))
        d2.mkdir()
        _frozen_dir(d2, **kw)
        with pytest.raises(SystemExit, match=msg):
            fa.check_confirm_gates(d2)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit, match="frozen.json"):
        fa.check_confirm_gates(empty)


def test_confirm_main_refuses_before_loading_anything(tmp_path, monkeypatch):
    """run_confirm.main must exit at the gates (pointed at an EMPTY folder here, so this test can never start a real
    confirmation run, even after the Opus audit has created AUDIT_OK in the study folder)."""
    monkeypatch.setattr(rc, "HERE", tmp_path)
    monkeypatch.setattr(fa, "BOX", tmp_path / "no_such_dir")        # any attempt to read box scores would fail loudly
    with pytest.raises(SystemExit, match="REFUSED"):
        rc.main()
    assert not (tmp_path / "results_confirm.json").exists()


def test_tampered_frozen_detected(tmp_path):
    d = _frozen_dir(tmp_path)
    (d / "frozen.json").write_text(json.dumps({"primary": "player_raw"}))
    with pytest.raises(SystemExit, match="hash"):
        fa.check_confirm_gates(d)


def test_select_gate_refuses_overwrite(tmp_path):
    fa.check_select_gates(tmp_path, preview=False)
    (tmp_path / "frozen.json").write_text("{}")
    with pytest.raises(SystemExit):
        fa.check_select_gates(tmp_path, preview=False)
    fa.check_select_gates(tmp_path, preview=True)                  # a preview never overwrites frozen files
    (tmp_path / "AUDIT_OK").write_text("x")
    with pytest.raises(SystemExit):
        fa.check_select_gates(tmp_path, preview=True)


# ------------------------------------------------------------------ the winner reproduction
def test_generalised_grid_reproduces_42_winner_on_slice():
    import repro_check
    res = repro_check.run(limit=60)
    assert res["max_abs_diff_projection"] < 1e-6
    assert res["max_abs_diff_consts"]["b"] < 1e-9 and res["max_abs_diff_consts"]["e_cat"] < 1e-9
    assert res["frozen_constants_pair_diff"] < 1e-9


def test_fit_k_restores():
    k0 = gl.K_FIT
    with fa.fit_k(0):
        assert gl.K_FIT == 0
    assert gl.K_FIT == k0 == 20


# ------------------------------------------------------------------ closing lines
def test_load_closing_filters(tmp_path):
    t0 = pd.Timestamp("2024-01-01T20:00:00Z")
    df = pd.DataFrame({"game_id": [1, 2, 3, 4, 5, 2],
                       "over_under": [220.0, 221.0, np.nan, 300.0, 230.0, 999.0],
                       "commence_time": [t0.isoformat()] * 6,
                       "snapshot": [(t0 - pd.Timedelta(minutes=9)).isoformat(), (t0 + pd.Timedelta(minutes=5)).isoformat(),
                                    (t0 - pd.Timedelta(minutes=9)).isoformat(), (t0 - pd.Timedelta(minutes=9)).isoformat(),
                                    t0.isoformat(), (t0 - pd.Timedelta(minutes=9)).isoformat()]})
    p = tmp_path / "c.parquet"
    df.to_parquet(p)
    s = fa.load_closing(p)
    # 1 kept; 2 in-play (snapshot after commence); 3 NaN; 4 outside 180-270; 5 snapshot == commence dropped; dup id 2 first row only
    assert list(s.index) == [1] and s.loc[1] == 220.0


# ------------------------------------------------------------------ Kalshi bets (toy strikes, #42 machinery)
def test_kalshi_bets_toy():
    import grid_bets as gb
    games = pd.DataFrame({"event": ["E1", "E2", "E3"]})
    s = pd.DataFrame({"event": ["E1", "E2", "E3"], "ticker": ["a", "b", "c"], "strike": [220.5, 225.5, 230.5],
                      "bid": [0.48, 0.50, 0.49], "ask": [0.52, 0.54, 0.53], "y": [1.0, 0.0, 1.0]})
    bt = gb.main_strike_table(games, s)
    assert bt.valid.all()
    proj = np.array([226.0, 220.0, np.nan])           # E1 over by 5.5 (win), E2 under by 5.5 (win), E3 nan -> no bet
    out = rc.kalshi_bets({"f": proj}, {"f": 4}, bt)
    assert out["f"]["n"] == 2 and abs(out["f"]["win_rate"] - 1.0) < 1e-12
    fee_o = gb.fee1(0.52)
    fee_u = gb.fee1(1 - 0.50)
    want = np.mean([1 - 0.52 - 0.01 - fee_o, 1 - 0.50 - 0.01 - fee_u])
    assert abs(out["f"]["mean_net"] - want) < 1e-12
    assert rc.kalshi_bets({"f": proj}, {"f": None}, bt)["f"]["n"] == 0


def test_edge_verdict():
    side = {"p": {"n": 400, "lb": 0.55}}
    k = {"p": {"n": 200, "mean_net": 0.02, "lb": 0.001}}
    assert rc.edge_verdict("p", side, k)["EDGE"] is True
    assert rc.edge_verdict("p", {"p": {"n": 400, "lb": 0.52}}, k)["EDGE"] is False
    assert rc.edge_verdict("p", side, {"p": {"n": 200, "mean_net": 0.02, "lb": -0.01}})["EDGE"] is False
    assert rc.edge_verdict("p", {"p": {"n": 100, "lb": 0.6}}, k)["EDGE"] is False
    assert rc.edge_verdict("p", side, {})["EDGE"] is False


# ------------------------------------------------------------------ confirmation core machinery, exercised on a SELECTION season
def test_core_confirm_runs_on_selection_season_with_fake_frozen():
    spec = list(gl.spec_from_names(gl.DEFAULTS))
    fz = {"team": {"spec": spec}, "player": None,
          "forms": {"team_raw": {"family": "team", "shift": 0.0, "T": 4}, "team_corr": {"family": "team", "shift": 0.5, "T": 6}},
          "primary": "team_raw"}
    res = rc.core_confirm(fz, None, seasons=("2023-24",), live=None)
    blk = res["seasons"]["2023-24"]["forms"]["team_raw"]
    assert blk["all_games"]["n"] == 1231 or blk["all_games"]["n"] == 1230
    assert "mae_diff_formula_minus_close" in blk["vs_sportsbook_close"]
    assert blk["side_vs_close"]["T"] == 4
    d_raw = res["pooled_confirm_seasons"]["team_raw"]["all_games"]["bias"]
    d_cor = res["pooled_confirm_seasons"]["team_corr"]["all_games"]["bias"]
    assert abs((d_cor - d_raw) - 0.5) < 1e-9


# ------------------------------------------------------------------ misc helpers
def test_nba_official_pause_window():
    et = no.ET
    assert no.in_pause_window(dt.datetime(2026, 10, 22, 17, 30, tzinfo=et))
    assert no.in_pause_window(dt.datetime(2026, 10, 22, 21, 29, tzinfo=et))
    assert not no.in_pause_window(dt.datetime(2026, 10, 22, 17, 29, tzinfo=et))
    assert not no.in_pause_window(dt.datetime(2026, 10, 22, 21, 30, tzinfo=et))
    assert no.seconds_until_open(dt.datetime(2026, 10, 22, 21, 0, tzinfo=et)) == 1800
    assert no.seconds_until_open(dt.datetime(2026, 10, 22, 12, 0, tzinfo=et)) == 0
    p = no.params("2025-26")
    assert p["MeasureType"] == "Advanced" and p["PerMode"] == "PerGame" and p["SeasonType"] == "Regular Season"


def test_data_check_helpers():
    assert dc.fix_odds_date("2021-22-1019") == "2021-10-19" and dc.fix_odds_date("2021-22-0613") == "2022-06-13"
    assert dc.fix_odds_date("2022-10-19 00:00:00") == "2022-10-19"
    assert dc.code_of("Charlotte Bobcats") == "CHA" and dc.code_of("LA Clippers") == "LAC" and dc.code_of("Portland Trail Blazers") == "POR"
    assert dc.season_of_date(dt.date(2023, 1, 5)) == "2022-23" and dc.season_of_date(dt.date(2022, 10, 20)) == "2022-23"


def test_independent_winner_matches_tonight_on_a_toy_day():
    """The independent recomputation equals tonight.py (this is the live-pipeline check; 2026-27 box scores only)."""
    res = dc.check6_live()
    assert res["max_abs_diff"] < 1e-9


def test_audit_ok_hash_mismatch_and_started_marker(tmp_path):
    d = _frozen_dir(tmp_path)
    ok = json.loads((d / "AUDIT_OK").read_text())
    ok["sha256"]["fa_lib.py"] = "0" * 64
    (d / "AUDIT_OK").write_text(json.dumps(ok))
    with pytest.raises(SystemExit, match="changed since the audit"):
        fa.check_confirm_gates(d)
    del ok["sha256"]["fa_lib.py"]
    (d / "AUDIT_OK").write_text(json.dumps(ok))
    with pytest.raises(SystemExit, match="does not cover"):
        fa.check_confirm_gates(d)
    (d / "AUDIT_OK").write_text("x")
    with pytest.raises(SystemExit, match="not JSON"):
        fa.check_confirm_gates(d)
    d2 = tmp_path / "s"
    d2.mkdir()
    _frozen_dir(d2)
    fa.check_confirm_gates(d2)
    fa.start_confirm(d2)
    assert (d2 / "confirm_started.json").exists()
    with pytest.raises(SystemExit, match="confirm_started"):
        fa.check_confirm_gates(d2)
    for f in ("run_confirm.py", "player_model.py", "../nba_totals_grid/grid_bets.py"):
        assert f in fa.AUDIT_FILES


def test_break_even_exact():
    assert fa.BREAK_EVEN == 110 / 210


def test_player_model_accepts_kalshi_codes():
    import player_model as pm
    assert pm._kcode("GSW") == "GSW" and pm._kcode("NYK") == "NYK" and pm._kcode("UTA") == "UTA"
    assert pm._kcode("GS") == "GSW" and pm._kcode("NY") == "NYK" and pm._kcode("UTAH") == "UTA" and pm._kcode("XXX") is None


def test_player_model_gsw_nyk_games_not_nan_and_same_with_hoopr_codes():
    import player_model as pm
    w = fa.build_world("2022-23", with_consts=False)
    g = fa.games_frame(w)
    sub = g[g.away.isin(["GSW", "NYK", "SAS"]) | g.home.isin(["GSW", "NYK", "SAS"])].iloc[:40].reset_index(drop=True)
    cols = ["game_id", "date", "T", "away", "home", "season"]
    a = pm.project_all("2022-23", sub[cols])
    inv = {v: k for k, v in fa.lib.TEAM_MAP.items()}
    sub2 = sub[cols].copy()
    sub2["away"], sub2["home"] = sub2.away.map(inv), sub2.home.map(inv)
    b = pm.project_all("2022-23", sub2)
    for i in range(8):
        assert np.isfinite(a[i]).all()
        assert np.array_equal(a[i], b[i])


def test_player_model_nan_games_at_most_two_per_selection_season():
    import player_model as pm
    for S in fa.SELECTION:
        w = fa.build_world(S, with_consts=False)
        g = fa.games_frame(w)[["game_id", "date", "T", "away", "home", "season"]]
        out = pm.project_all(S, g)
        assert all(int(np.isnan(out[i]).sum()) <= 2 for i in range(8)), S
