"""Tests for scripts/score_model_track.py (research #59 forward tracking).  Network and the heavy model are injected/faked except in the
two real-data tests (marked by name `real_`), which only use box scores dated before the game (no outcome of any tracked game)."""
import datetime as dt
import io
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import score_model_track as T  # noqa: E402

UTC = dt.timezone.utc
NIGHT = dt.date(2026, 10, 22)
START = dt.datetime(2026, 10, 22, 23, 0, tzinfo=UTC)      # 7 pm ET


def game(key="26OCT22CLEPHI", away="CLE", home="PHI", start=START, mid="m1"):
    return {"key": key, "away": away, "home": home, "id": mid, "start": T.iso(start), "title": "x"}


def fake_predictor(shift=0.0):
    def f(frozen, box, gs):
        return {g["key"]: {"key": g["key"], "e1_away": 110.0 + shift, "e1_home": 112.0 + shift, "b1_away": 108.0 + shift,
                           "b1_home": 111.0 + shift} for g in gs}
    return f


def run_once(tmp, now, games, predictor=None, fetch=None, box=None, prev=None, **kw):
    # prev: the previous night's Kalshi games (default none = no league games yesterday = fresh box)
    sched = lambda api, night: games if night == NIGHT else (prev or {})  # noqa: E731
    return T.run(now=now, logs=tmp / "logs", box_path=box or (tmp / "box.parquet"), api=object(), schedule_fn=sched,
                 predictor=predictor or fake_predictor(), integrity=lambda: "deadbeef", refresh=False, fetch_live=fetch, log=lambda *a: None, **kw)


@pytest.fixture
def box(tmp_path):
    p = tmp_path / "box.parquet"
    pd.read_parquet(T.BOX_2027).to_parquet(p)
    return p


def preds_of(tmp):
    return T.read_jsonl(tmp / "logs" / "predictions.jsonl")


# ---------------------------------------------------------------- lock rule
def test_lock_rule_30_minutes(tmp_path, box):
    games = {"A": game("26OCT22AAAXXX", start=START), "B": game("26OCT22BBBYYY", away="DEN", home="OKC", start=START)}
    games = {"26OCT22CLEPHI": game(), "26OCT22DENOKC": game("26OCT22DENOKC", "DEN", "OKC", START + dt.timedelta(hours=2), "m2"),
             "26OCT22LALGSW": game("26OCT22LALGSW", "LAL", "GSW", START + dt.timedelta(hours=3), "m3")}
    now = START - dt.timedelta(minutes=31)
    s = run_once(tmp_path, now, games, box=box)
    assert {r["key"] for r in preds_of(tmp_path)} == set(games)          # all three are >= 30 min away
    # a game exactly 30 min away locks, 29 min does not, one already started does not
    tmp2 = tmp_path / "b"
    tmp2.mkdir()
    g2 = {"X": game("26OCT22CLEPHI", start=START), "Y": game("26OCT22DENOKC", "DEN", "OKC", START, "m2"),
          "Z": game("26OCT22LALGSW", "LAL", "GSW", START, "m3")}
    g2["X"]["start"] = T.iso(now + dt.timedelta(minutes=30))
    g2["Y"]["start"] = T.iso(now + dt.timedelta(minutes=29))
    g2["Z"]["start"] = T.iso(now - dt.timedelta(minutes=5))
    s = run_once(tmp2, now, {v["key"]: v for v in g2.values()}, box=box)
    assert [r["key"] for r in preds_of(tmp2)] == ["26OCT22CLEPHI"]
    why = {r["key"]: r["reason_not_locked"] for r in s["tonight"]["rows"]}
    assert "29 min" in why["26OCT22DENOKC"] and why["26OCT22LALGSW"] == "started"


def test_never_overwrite_and_no_duplicates(tmp_path, box):
    games = {"26OCT22CLEPHI": game()}
    t1 = START - dt.timedelta(hours=3)
    run_once(tmp_path, t1, games, fake_predictor(0.0), box=box)
    first = preds_of(tmp_path)
    assert len(first) == 1 and first[0]["e1_total"] == 222.0 and first[0]["b1_total"] == 219.0
    for k in range(3):                                        # later runs, even with different predictions, change nothing
        run_once(tmp_path, t1 + dt.timedelta(minutes=30 * (k + 1)), games, fake_predictor(50.0), box=box)
    assert preds_of(tmp_path) == first


def test_record_fields(tmp_path, box):
    run_once(tmp_path, START - dt.timedelta(hours=2), {"26OCT22CLEPHI": game()}, box=box)
    r = preds_of(tmp_path)[0]
    for k in ("ts_utc", "night", "key", "start_utc", "away", "home", "e1_away", "e1_home", "e1_total", "b1_away", "b1_home", "b1_total",
              "frozen_sha", "inputs"):
        assert k in r, k
    assert r["night"] == "2026-10-22" and r["start_utc"] == "2026-10-22T23:00:00Z" and r["frozen_sha"] == "deadbeef"
    assert r["inputs"]["n_2026_27_box_games"] == 14 and r["inputs"]["box_mtime_utc"]


def test_integrity_failure_blocks_locks(tmp_path, box):
    def bad():
        raise SystemExit("REFUSED: hash mismatch")
    s = T.run(now=START - dt.timedelta(hours=2), logs=tmp_path / "logs", box_path=box, api=object(),
              schedule_fn=lambda a, n: {"26OCT22CLEPHI": game()} if n == NIGHT else {}, predictor=fake_predictor(), integrity=bad, refresh=False, log=lambda *a: None)
    assert preds_of(tmp_path) == [] and "integrity" in s["tonight"]["rows"][0]["reason_not_locked"]


def test_implausible_prediction_not_locked(tmp_path, box):
    def pred(frozen, b, gs):
        return {g["key"]: {"e1_away": float("nan"), "e1_home": 100.0, "b1_away": 100.0, "b1_home": 100.0} for g in gs}
    run_once(tmp_path, START - dt.timedelta(hours=2), {"26OCT22CLEPHI": game()}, predictor=pred, box=box)
    assert preds_of(tmp_path) == []


def test_unmapped_team_not_locked(tmp_path, box):
    run_once(tmp_path, START - dt.timedelta(hours=2), {"26OCT22ZZZPHI": game("26OCT22ZZZPHI", "ZZZ", "PHI")}, box=box)
    assert preds_of(tmp_path) == []


# ---------------------------------------------------------------- team codes
def test_team_code_mapping_tricky_six():
    m = T.kalshi_to_hoopr()
    assert m["GSW"] == "GS" and m["NYK"] == "NY" and m["SAS"] == "SA" and m["UTA"] == "UTAH" and m["WAS"] == "WSH" and m["NOP"] == "NO"
    L = T.load_model_modules()["L"]
    assert len(m) == 30 and set(m.values()) <= L.NBA_HOOPR
    for k, v in m.items():
        assert L.FRANCHISE[v] == k                        # round trip hoopR -> Kalshi
    for k in ("CLE", "PHI", "DEN", "OKC", "LAL", "BOS"):
        assert m[k] == k


def test_tricky_teams_reach_the_placeholder_rows():
    L = T.load_model_modules()["L"]
    tmpl = L.read_year(2026)
    tmpl = tmpl[tmpl.season_type == 2]
    m = T.kalshi_to_hoopr()
    gs = [{"key": "k1", "away_hoopr": m["GSW"], "home_hoopr": m["NYK"], "start_utc": "2026-10-23T00:00:00Z"},
          {"key": "k2", "away_hoopr": m["SAS"], "home_hoopr": m["UTA"], "start_utc": "2026-10-23T00:00:00Z"},
          {"key": "k3", "away_hoopr": m["WAS"], "home_hoopr": m["NOP"], "start_utc": "2026-10-23T00:00:00Z"}]
    ph = T.placeholder_rows(tmpl, gs)
    assert set(ph.team_abbreviation) == {"GS", "NY", "SA", "UTAH", "WSH", "NO"}
    tg = L.team_games_from_box(ph, "2026-27")                     # all six survive the franchise filter
    assert set(tg.team) == {"GS", "NY", "SA", "UTAH", "WSH", "NO"} and len(tg) == 6
    assert (ph.game_id < 0).all()


# ---------------------------------------------------------------- no look-ahead (real box, forward season only, no outcomes of tracked games)
def _frozen():
    return json.loads((T.STUDY / "frozen.json").read_text())


def _games_on(box, d):
    tab = T.box_game_table(box)
    k2h = {v: k for k, v in T.kalshi_to_hoopr().items()}
    tab = tab[tab.date == d]
    return [{"key": f"{k2h[r.away]}{k2h[r.home]}", "away_hoopr": r.away, "home_hoopr": r.home, "start_utc": "2026-10-21T23:00:00Z"}
            for r in tab.itertuples()]


def test_real_placeholders_never_contribute_and_equal_forward_block():
    """Predictions for the Oct 21 games, made from the box cut BEFORE Oct 21 + placeholders, equal the frozen forward_block-style
    prediction made from the full box; they do not change when the placeholder fill changes, nor when rows dated on/after Oct 21 are
    altered or removed."""
    full = pd.read_parquet(T.BOX_2027)
    D = dt.date(2026, 10, 21)
    gs = _games_on(full, D)
    assert len(gs) >= 3
    cut = full[pd.to_datetime(full.game_date_time).dt.tz_convert("America/New_York").dt.date < D]
    assert 0 < len(cut) < len(full)
    p1 = T.predict_games(_frozen(), cut, gs, 1.0)
    p2 = T.predict_games(_frozen(), cut, gs, 1.3)
    assert p1 == p2                                                # placeholder stats do not matter
    # rows dated on/after D present (and altered) must not change anything
    alt = full.copy()
    m = pd.to_datetime(alt.game_date_time).dt.tz_convert("America/New_York").dt.date >= D
    for c in ("team_score", "opponent_team_score", "field_goals_attempted", "turnovers", "total_turnovers"):
        alt.loc[m, c] = alt.loc[m, c] * 2
    assert T.predict_games(_frozen(), alt, gs, 1.0) == p1
    # equals the forward-block route on the real full box (features of a game use only earlier rows)
    mods = T.load_model_modules()
    S, RT, L = mods["S"], mods["RT"], mods["L"]
    tg = pd.concat([L.team_games_from_box(L.read_year(2026), "2025-26"), L.team_games_from_box(full, S.FORWARD)], ignore_index=True)
    tg["fr"], tg["fr_opp"] = tg.team.map(L.FRANCHISE), tg.opp.map(L.FRANCHISE)
    F = S.build_features(tg, [S.FORWARD])
    F = F[F.date == pd.Timestamp(D)]
    pr = RT.season_predictions(_frozen(), F, S.FORWARD, ["E1", "B1"], reader=lambda y: (full if int(y) == 2027 else L.read_year_fixed(y)))
    k2h = {v: k for k, v in T.kalshi_to_hoopr().items()}
    for i, r in enumerate(F.itertuples()):
        away, home = (r.opp, r.team) if r.home > 0 else (r.team, r.opp)
        key = f"{k2h[away]}{k2h[home]}"
        side = "home" if r.home > 0 else "away"
        for mod in ("E1", "B1"):
            assert p1[key][mod.lower() + "_" + side] == pytest.approx(pr[mod][i], abs=1e-9)


def test_real_features_ignore_rows_on_or_after_the_game_date():
    S = T.load_model_modules()["S"]
    L = T.load_model_modules()["L"]
    full = pd.read_parquet(T.BOX_2027)
    tg = pd.concat([L.team_games_from_box(L.read_year(2026), "2025-26"), L.team_games_from_box(full, S.FORWARD)], ignore_index=True)
    tg["fr"], tg["fr_opp"] = tg.team.map(L.FRANCHISE), tg.opp.map(L.FRANCHISE)
    F1 = S.build_features(tg, [S.FORWARD])
    tg2 = tg.copy()
    late = (tg2.season == S.FORWARD) & (tg2.date >= pd.Timestamp("2026-10-21"))
    tg2.loc[late, ["pts", "opp_pts", "pace", "poss", "opp_poss"]] *= 1.7
    F2 = S.build_features(tg2, [S.FORWARD])
    on21 = F1.date == pd.Timestamp("2026-10-21")
    assert on21.sum() > 0
    pd.testing.assert_frame_equal(F1[on21][S.FEATS].reset_index(drop=True), F2[on21][S.FEATS].reset_index(drop=True))


def test_box_stats_counts_only_earlier_games():
    full = pd.read_parquet(T.BOX_2027)
    st = T.box_stats(full, dt.date(2026, 10, 22))
    assert st["n_box_games"] == 14 and st["box_max_date"] == "2026-10-21" and st["box_games_prev_day"] >= 3
    assert T.box_stats(full, dt.date(2026, 10, 21))["n_box_games"] == 3 or T.box_stats(full, dt.date(2026, 10, 21))["n_box_games"] < 14


# ---------------------------------------------------------------- results and checks
def fin_fetch(a=110, h=115, status="closed"):
    def f(api, mid):
        det = {"away_points": a, "home_points": h, "status": status, "period": 4}
        return det, {"live_data": {"details": det}}
    return f


def test_result_written_once_and_only_when_final(tmp_path, box):
    games = {"26OCT22CLEPHI": game()}
    run_once(tmp_path, START - dt.timedelta(hours=2), games, box=box)
    end = START + dt.timedelta(hours=3)
    run_once(tmp_path, START + dt.timedelta(minutes=30), games, fetch=fin_fetch(50, 48, "inprogress"), box=box)
    assert T.read_jsonl(tmp_path / "logs" / "results.jsonl") == []
    s = run_once(tmp_path, START + dt.timedelta(minutes=30), games, fetch=fin_fetch(50, 48, "inprogress"), box=box)
    assert s["tonight"]["rows"][0]["live_total"] == 98 and "final_total" not in s["tonight"]["rows"][0]
    for k in range(3):
        run_once(tmp_path, end + dt.timedelta(minutes=30 * k), games, fetch=fin_fetch(), box=box)
    res = [r for r in T.read_jsonl(tmp_path / "logs" / "results.jsonl") if r["type"] == "result"]
    assert len(res) == 1 and res[0]["total"] == 225 and res[0]["source"] == "kalshi_live" and res[0]["key"] == "26OCT22CLEPHI"
    assert (tmp_path / "logs" / "raw" / "final_26OCT22CLEPHI.json").exists()


def test_result_not_recorded_for_unlocked_game(tmp_path, box):
    games = {"26OCT22CLEPHI": game()}
    run_once(tmp_path, START + dt.timedelta(minutes=1), games, fetch=fin_fetch(), box=box)       # started before any run: never locked
    assert preds_of(tmp_path) == [] and T.read_jsonl(tmp_path / "logs" / "results.jsonl") == []


def test_check_record_agree_and_disagree_once(tmp_path, box):
    full = pd.read_parquet(T.BOX_2027)
    tab = T.box_game_table(full)
    r0 = tab.iloc[0]
    k2h = {v: k for k, v in T.kalshi_to_hoopr().items()}
    a, h = k2h[r0.away], k2h[r0.home]
    key = "26OCT21" + a + h
    start = T.iso(dt.datetime.combine(r0.date, dt.time(23, 0), tzinfo=UTC))
    preds = {key: {"key": key, "away": a, "home": h, "start_utc": start}}
    now = dt.datetime(2026, 10, 22, 12, tzinfo=UTC)
    ok = [{"type": "result", "key": key, "away_pts": int(r0.away_pts), "home_pts": int(r0.home_pts), "total": 0}]
    c = T.new_checks(now, ok, preds, full)
    assert len(c) == 1 and c[0]["agree"] is True
    bad = [{"type": "result", "key": key, "away_pts": int(r0.away_pts) + 1, "home_pts": int(r0.home_pts), "total": 0}]
    c = T.new_checks(now, bad, preds, full)
    assert len(c) == 1 and c[0]["agree"] is False
    assert T.new_checks(now, bad + c, preds, full) == []                    # once per game
    assert T.new_checks(now, ok, preds, full.iloc[0:0]) == []              # box does not have the game yet


# ---------------------------------------------------------------- idempotence, atomic writes, lock
def test_run_is_idempotent(tmp_path, box):
    games = {"26OCT22CLEPHI": game()}
    t = START - dt.timedelta(hours=1)
    run_once(tmp_path, t, games, box=box)
    snap = {n: (tmp_path / "logs" / n).read_bytes() for n in ("predictions.jsonl",)}
    for _ in range(3):
        run_once(tmp_path, t + dt.timedelta(minutes=1), games, box=box)
    assert (tmp_path / "logs" / "predictions.jsonl").read_bytes() == snap["predictions.jsonl"]
    run_once(tmp_path, START + dt.timedelta(hours=3), games, fetch=fin_fetch(), box=box)
    r1 = (tmp_path / "logs" / "results.jsonl").read_bytes()
    run_once(tmp_path, START + dt.timedelta(hours=3, minutes=30), games, fetch=fin_fetch(), box=box)
    assert (tmp_path / "logs" / "results.jsonl").read_bytes() == r1
    assert not list((tmp_path / "logs").glob("*.tmp")) and not (tmp_path / "logs" / ".run.lock").exists()


def test_atomic_write_retries_and_leaves_no_tmp(tmp_path, monkeypatch):
    p = tmp_path / "x" / "f.json"
    real, calls = os.replace, {"n": 0}

    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise PermissionError("busy")
        return real(a, b)
    monkeypatch.setattr(T.os, "replace", flaky)
    monkeypatch.setattr(T.time, "sleep", lambda s: None)
    T.atomic_write_text(p, "hello")
    assert p.read_text() == "hello" and calls["n"] == 4 and not list(p.parent.glob("*.tmp"))
    T.append_jsonl_atomic(p, [{"a": 1}])
    T.append_jsonl_atomic(p, [{"a": 2}])
    assert [r["a"] for r in T.read_jsonl(p)] == [1, 2]


def test_jsonl_append_keeps_old_lines(tmp_path):
    p = tmp_path / "p.jsonl"
    T.append_jsonl_atomic(p, [{"a": 1}])
    T.append_jsonl_atomic(p, [{"a": 2}, {"a": 3}])
    T.append_jsonl_atomic(p, [])
    assert [r["a"] for r in T.read_jsonl(p)] == [1, 2, 3]


def test_run_lock(tmp_path):
    p = tmp_path / ".run.lock"
    with T.RunLock(p) as a:
        assert a.held
        with T.RunLock(p) as b:
            assert not b.held
        assert p.exists()                       # the second (failed) holder must not delete the first one's lock
    assert not p.exists()
    p.write_text("1")
    os.utime(p, (1, 1))                         # stale lock is taken over
    with T.RunLock(p) as c:
        assert c.held


# ---------------------------------------------------------------- box refresh
def _box_bytes(n_rows=4):
    df = pd.read_parquet(T.BOX_2027).iloc[:n_rows]
    b = io.BytesIO()
    df.to_parquet(b)
    return b.getvalue()


def test_refresh_box(tmp_path):
    p = tmp_path / "team_box_2027.parquet"
    p.write_bytes(_box_bytes(10))
    now = dt.datetime.now(UTC)
    calls = []
    getter = lambda u: (calls.append(u), _box_bytes(12))[1]  # noqa: E731
    assert T.refresh_box(p, now=now, getter=getter)["reason"] == "fresh" and calls == []
    os.utime(p, (now.timestamp() - 4 * 3600,) * 2)                      # 4 h old -> refresh
    r = T.refresh_box(p, now=now, getter=getter)
    assert r["refreshed"] and calls == [T.HOOPR_URL] and len(pd.read_parquet(p)) == 12
    assert not list(tmp_path.glob("*.parquet.tmp")) and [x.name for x in tmp_path.iterdir()] == ["team_box_2027.parquet"]
    # invalid download keeps the old file
    os.utime(p, (now.timestamp() - 4 * 3600,) * 2)
    r = T.refresh_box(p, now=now, getter=lambda u: b"<html>not parquet")
    assert not r["refreshed"] and "failed" in r["reason"] and len(pd.read_parquet(p)) == 12 and len(list(tmp_path.iterdir())) == 1
    # a download with fewer rows is rejected
    os.utime(p, (now.timestamp() - 4 * 3600,) * 2)
    r = T.refresh_box(p, now=now, getter=lambda u: _box_bytes(6))
    assert not r["refreshed"] and len(pd.read_parquet(p)) == 12
    # the URL targets only the 2027 file
    assert T.HOOPR_URL.endswith("/team_box/parquet/team_box_2027.parquet") and T.BOX_2027.name == "team_box_2027.parquet"


# ---------------------------------------------------------------- metrics
def toy():
    # actual totals 220 (110+110), 230 (120+110), 200 (95+105)
    preds = {
        "G1": {"key": "G1", "e1_away": 112.0, "e1_home": 108.0, "b1_away": 100.0, "b1_home": 110.0},    # E1 total 220, B1 210
        "G2": {"key": "G2", "e1_away": 100.0, "e1_home": 115.0, "b1_away": 118.0, "b1_home": 112.0},    # E1 215, B1 230
        "G3": {"key": "G3", "e1_away": 100.0, "e1_home": 112.0, "b1_away": 101.0, "b1_home": 111.0},    # E1 212, B1 212
        "G4": {"key": "G4", "e1_away": 1.0, "e1_home": 1.0, "b1_away": 1.0, "b1_home": 1.0}}             # no result: ignored
    res = [{"type": "result", "key": "G1", "away_pts": 110, "home_pts": 110, "total": 220},
           {"type": "result", "key": "G2", "away_pts": 120, "home_pts": 110, "total": 230},
           {"type": "result", "key": "G3", "away_pts": 95, "home_pts": 105, "total": 200},
           {"type": "check", "key": "G3", "agree": True}]
    return preds, res


def test_summary_math_toy_log():
    preds, res = toy()
    m = T.compute_metrics(preds, res)
    assert m["n_games"] == 3
    e1 = m["E1"]
    # errors (pred total - actual): 0, -15, +12  -> MAE 9.0, within 10: 1/3, bias -1.0
    assert e1["total_mae"] == pytest.approx(9.0) and e1["pct_within_10"] == pytest.approx(1 / 3) and e1["bias"] == pytest.approx(-1.0)
    # team errors: G1 (2, -2) G2 (-20, 5) G3 (5, 7) -> mean abs = (2+2+20+5+5+7)/6
    assert e1["team_mae"] == pytest.approx(41 / 6)
    # winner: predicted home margin -4 (actual 0 -> sign 0 -> miss), +15 (actual -10 -> miss), +12 (actual +10 -> hit)
    assert e1["winner_pct"] == pytest.approx(1 / 3)
    b1 = m["B1"]
    # B1 errors: 210-220=-10, 230-230=0, 212-200=+12 -> MAE 22/3, within 10: 2/3
    assert b1["total_mae"] == pytest.approx(22 / 3) and b1["pct_within_10"] == pytest.approx(2 / 3)
    p = m["paired_E1_minus_B1"]
    assert p["diff_point"] == pytest.approx(9.0 - 22 / 3) and "lo" not in p and "needs n" in p["note"]      # n < 10: no CI yet


def test_bootstrap_ci_deterministic_and_n10():
    M = T.load_model_modules()["M"]
    rng = np.random.default_rng(1)
    preds, res = {}, []
    for i in range(12):
        a, h = 100 + rng.integers(-10, 10), 105 + rng.integers(-10, 10)
        preds[f"G{i}"] = {"key": f"G{i}", "e1_away": float(a + 2), "e1_home": float(h - 1), "b1_away": float(a + 5),
                          "b1_home": float(h + 4)}
        res.append({"type": "result", "key": f"G{i}", "away_pts": int(a), "home_pts": int(h), "total": int(a + h)})
    m1, m2 = T.compute_metrics(preds, res), T.compute_metrics(preds, res)
    assert m1 == m2
    p = m1["paired_E1_minus_B1"]
    assert p["draws"] == 10000 and p["seed"] == 20261023 and p["lo"] <= p["diff"] <= p["hi"]
    d = np.array([abs(preds[f"G{i}"]["e1_away"] + preds[f"G{i}"]["e1_home"] - res[i]["total"])
                  - abs(preds[f"G{i}"]["b1_away"] + preds[f"G{i}"]["b1_home"] - res[i]["total"]) for i in range(12)])
    assert (p["diff"], p["lo"], p["hi"]) == M.paired_boot_ci(d)
    # 9 games: no CI; 10 games: CI
    assert "lo" not in T.compute_metrics({k: preds[k] for k in list(preds)[:9]}, res[:9])["paired_E1_minus_B1"]
    assert "lo" in T.compute_metrics({k: preds[k] for k in list(preds)[:10]}, res[:10])["paired_E1_minus_B1"]


def test_empty_metrics():
    m = T.compute_metrics({}, [])
    assert m["n_games"] == 0 and m["E1"] is None and m["paired_E1_minus_B1"] is None


def test_summary_has_tonight_rows(tmp_path, box):
    games = {"26OCT22CLEPHI": game(), "26OCT22DENOKC": game("26OCT22DENOKC", "DEN", "OKC", START + dt.timedelta(hours=2), "m2")}
    run_once(tmp_path, START - dt.timedelta(minutes=45), games, box=box)
    s = run_once(tmp_path, START + dt.timedelta(hours=3), games, fetch=fin_fetch(110, 115), box=box)
    rows = {r["key"]: r for r in s["tonight"]["rows"]}
    r = rows["26OCT22CLEPHI"]
    assert r["locked"] and r["final_total"] == 225 and r["e1_error"] == pytest.approx(222 - 225) and r["b1_error"] == pytest.approx(219 - 225)
    assert s["metrics"]["n_games"] == 2 and s["metrics"]["E1"]["total_mae"] == pytest.approx(3.0)
    on_disk = json.loads((tmp_path / "logs" / "summary.json").read_text())
    assert on_disk["night"] == "2026-10-22" and on_disk["n_locked"] == 2


def test_never_imports_execution():
    import subprocess
    code = ("import sys; sys.path.insert(0, r'%s'); import score_model_track as T; T.load_model_modules(); "
            "import live_scores_dashboard; print(any(m.startswith('kpm.execution') for m in sys.modules))" % (ROOT / "scripts"))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    assert out.stdout.strip().endswith("False"), out.stdout + out.stderr         # fresh interpreter: nothing pulls in kpm.execution
    src = (ROOT / "scripts" / "score_model_track.py").read_text(encoding="utf-8")
    for bad in ("kpm.execution", "/portfolio", "/orders", "/account", "api_key", "private_key"):
        assert bad not in src, bad


# ---------------------------------------------------------------- amendment: fresh vs stale box
def _prev_games(box, extra_missing=False):
    """Kalshi-style games of 2026-10-21 that ARE in the box (fresh), plus optionally one that is not (stale)."""
    tab = T.box_game_table(pd.read_parquet(box))
    k2h = {v: k for k, v in T.kalshi_to_hoopr().items()}
    out = {}
    for r in tab[tab.date == dt.date(2026, 10, 21)].itertuples():
        key = "26OCT21" + k2h[r.away] + k2h[r.home]
        out[key] = {"key": key, "away": k2h[r.away], "home": k2h[r.home], "id": "x", "start": "2026-10-21T23:00:00Z"}
    assert out
    if extra_missing:
        out["26OCT21BOSMIA"] = {"key": "26OCT21BOSMIA", "away": "BOS", "home": "MIA", "id": "y", "start": "2026-10-22T01:00:00Z"}
    return out


def test_stale_box_no_lock_three_hours_before(tmp_path, box):
    s = run_once(tmp_path, START - dt.timedelta(hours=3), {"26OCT22CLEPHI": game()}, box=box, prev=_prev_games(box, True))
    assert preds_of(tmp_path) == [] and "stale box, waiting" in s["tonight"]["rows"][0]["reason_not_locked"]


def test_stale_box_locks_with_flag_at_40_minutes(tmp_path, box):
    run_once(tmp_path, START - dt.timedelta(minutes=40), {"26OCT22CLEPHI": game()}, box=box, prev=_prev_games(box, True))
    r = preds_of(tmp_path)
    assert len(r) == 1 and r[0]["stale_box"] is True and "26OCT21BOSMIA" in r[0]["inputs"]["fresh_note"]
    t2 = tmp_path / "w"
    t2.mkdir()
    run_once(t2, START - dt.timedelta(minutes=46), {"26OCT22CLEPHI": game()}, box=box, prev=_prev_games(box, True))
    assert preds_of(t2) == []                                  # 46 min: still waiting
    run_once(t2, START - dt.timedelta(minutes=29), {"26OCT22CLEPHI": game()}, box=box, prev=_prev_games(box, True))
    assert preds_of(t2) == []                                  # 29 min: too late for any lock


def test_fresh_box_locks_three_hours_before(tmp_path, box):
    run_once(tmp_path, START - dt.timedelta(hours=3), {"26OCT22CLEPHI": game()}, box=box, prev=_prev_games(box))
    r = preds_of(tmp_path)
    assert len(r) == 1 and r[0]["stale_box"] is False


def test_no_games_yesterday_is_fresh(tmp_path, box):
    run_once(tmp_path, START - dt.timedelta(hours=3), {"26OCT22CLEPHI": game()}, box=box, prev={})
    r = preds_of(tmp_path)
    assert len(r) == 1 and r[0]["stale_box"] is False and "no league games" in r[0]["inputs"]["fresh_note"]


def test_prev_schedule_unavailable_uses_box_dates(box):
    full = pd.read_parquet(box)
    k2h = T.kalshi_to_hoopr()
    assert T.box_is_fresh(full, dt.date(2026, 10, 22), None, k2h)[0] is True
    assert T.box_is_fresh(full, dt.date(2026, 10, 25), None, k2h)[0] is False


def test_summary_splits_fresh_and_stale(tmp_path, box):
    run_once(tmp_path, START - dt.timedelta(hours=3), {"26OCT22CLEPHI": game()}, box=box, prev=_prev_games(box))
    g2 = {"26OCT22CLEPHI": game(), "26OCT22DENOKC": game("26OCT22DENOKC", "DEN", "OKC", START + dt.timedelta(hours=2), "m2")}
    run_once(tmp_path, START + dt.timedelta(hours=2) - dt.timedelta(minutes=40), g2, box=box, prev=_prev_games(box, True))
    assert {r["key"]: r["stale_box"] for r in preds_of(tmp_path)} == {"26OCT22CLEPHI": False, "26OCT22DENOKC": True}
    s = run_once(tmp_path, START + dt.timedelta(hours=5), g2, fetch=fin_fetch(110, 115), box=box, prev=_prev_games(box, True))
    assert s["metrics"]["n_games"] == 2
    assert s["metrics_by_freshness"]["fresh"]["n_games"] == 1 and s["metrics_by_freshness"]["stale_box"]["n_games"] == 1
