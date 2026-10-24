"""Tests for #36 job 2: refusal gates, discovery STOP flow, split, fee/slippage in the betting rule, price-source handling.
Synthetic data only; nothing here reads real prices or outcomes."""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analysis as A  # noqa: E402
import build as B  # noqa: E402
import data as D  # noqa: E402
import guards as G  # noqa: E402
import nbmodel as NB  # noqa: E402
import pipeline as P  # noqa: E402
import run_discovery as RD  # noqa: E402
import run_unseen as RU  # noqa: E402
from test_props import _synth  # noqa: E402

OK_STATUS = {"pts_reb_ast": {"selected": 5, "cached": 5, "missing": 0}, "3pt": {"selected": 3, "cached": 3, "missing": 0}}


# ------------------------------------------------------------------ refusal gates
def test_unseen_refuses_without_audit(tmp_path):
    (tmp_path / "threes_download_complete.flag").write_text("x")
    why = G.unseen_refusals(tmp_path, OK_STATUS)
    assert len(why) == 1 and "AUDIT_OK" in why[0]


def test_unseen_refuses_if_results_exist(tmp_path):
    (tmp_path / "AUDIT_OK").write_text("x")
    (tmp_path / "threes_download_complete.flag").write_text("x")
    (tmp_path / "results_unseen.json").write_text("{}")
    why = G.unseen_refusals(tmp_path, OK_STATUS)
    assert len(why) == 1 and "results_unseen.json" in why[0]


def test_unseen_refuses_without_threes_flag(tmp_path):
    (tmp_path / "AUDIT_OK").write_text("x")
    why = G.unseen_refusals(tmp_path, OK_STATUS)
    assert len(why) == 1 and "threes_download_complete.flag" in why[0]


def test_unseen_refuses_if_either_download_incomplete(tmp_path):
    (tmp_path / "AUDIT_OK").write_text("x")
    (tmp_path / "threes_download_complete.flag").write_text("x")
    bad = {"pts_reb_ast": {"selected": 5, "cached": 4, "missing": 1}, "3pt": {"selected": 3, "cached": 3, "missing": 0}}
    assert any("pts_reb_ast" in w for w in G.unseen_refusals(tmp_path, bad))
    bad = {"pts_reb_ast": OK_STATUS["pts_reb_ast"], "3pt": {"selected": 0, "cached": 0, "missing": 0}}
    assert any("3pt" in w for w in G.unseen_refusals(tmp_path, bad))     # an empty selection is not "complete"
    assert G.unseen_refusals(tmp_path, OK_STATUS) == []


def test_unseen_refuses_if_discovery_stop_or_missing(tmp_path):
    assert "missing" in G.discovery_stop(tmp_path)
    (tmp_path / "results_discovery.json").write_text(json.dumps({"verdict": "STOP: model not better than market"}))
    assert "STOP" in G.discovery_stop(tmp_path)
    (tmp_path / "results_discovery.json").write_text(json.dumps({"verdict": "gates passed", "betting_discovery": {}}))
    assert G.discovery_stop(tmp_path) is None


def test_run_unseen_main_exits_before_loading_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(RU, "HERE", tmp_path)
    monkeypatch.setattr(D, "download_status", lambda: OK_STATUS)
    monkeypatch.setattr(P, "load_all", lambda *a, **k: pytest.fail("data must not be loaded when refused"))
    with pytest.raises(SystemExit) as e:
        RU.main()
    assert "REFUSED" in str(e.value) and "AUDIT_OK" in str(e.value)
    assert not (tmp_path / "results_unseen.json").exists()


# ------------------------------------------------------------------ discovery STOP flow
def _fake_S():
    mrows = [{"ticker": "T", "stat": "PTS", "excl": None, "row": 0, "quote_excl": None, "p": 0.5, "bid": .4, "ask": .5,
              "result": "yes", "gk": ("d", "g"), "tip": 1}]
    return {"selected_missing": [], "disc": {("d", "g")}, "uns": set(), "games_tip": {("d", "g"): 1}, "rows": [], "mrows": mrows,
            "counts": {"markets": 1}, "games": {}, "unmatched": {}}


def _patch_flow(monkeypatch, tmp_path, gate_ok):
    monkeypatch.setattr(RD, "OUT", tmp_path / "results_discovery.json")
    monkeypatch.setattr(P, "load_all", lambda *a, **k: _fake_S())
    monkeypatch.setattr(P, "fit_models", lambda rows, disc: {"PTS": {"alpha": 0.1, "n_fit": 1, "beta": np.zeros(2)}})
    monkeypatch.setattr(P, "attach_probs", lambda *a, **k: None)
    monkeypatch.setattr(P, "gate_run", lambda recs, tips, pf: {"all_pass": gate_ok or (pf is True)})   # all-prices run fails if not gate_ok


def test_discovery_stop_when_any_gate_fails_and_no_betting(tmp_path, monkeypatch):
    _patch_flow(monkeypatch, tmp_path, gate_ok=False)
    monkeypatch.setattr(P, "period_report", lambda *a, **k: pytest.fail("betting rule must not run after a failed gate"))
    RD.main()
    r = json.loads((tmp_path / "results_discovery.json").read_text())
    assert r["verdict"] == "STOP: model not better than market" and "betting_discovery" not in r


def test_discovery_runs_betting_only_when_all_gates_pass(tmp_path, monkeypatch):
    _patch_flow(monkeypatch, tmp_path, gate_ok=True)
    seen = []
    monkeypatch.setattr(P, "period_report", lambda recs, tips, th, pf, with_tests: (seen.append((th, pf, with_tests)) or {"n": 0}, []))
    RD.main()
    r = json.loads((tmp_path / "results_discovery.json").read_text())
    assert r["verdict"] == "gates passed" and set(r["betting_discovery"]) == {
        "theta3c_15-85c", "theta3c_all", "theta6c_15-85c", "theta6c_all"}
    assert all(w is False for _, _, w in seen) and len(seen) == 4


def test_discovery_refuses_if_selected_markets_missing(tmp_path, monkeypatch):
    _patch_flow(monkeypatch, tmp_path, gate_ok=True)
    S = _fake_S()
    S["selected_missing"] = [{"ticker": "X"}]
    monkeypatch.setattr(P, "load_all", lambda *a, **k: S)
    with pytest.raises(SystemExit):
        RD.main()
    assert not (tmp_path / "results_discovery.json").exists()


# ------------------------------------------------------------------ fee rounding, slippage, versions
def test_fee_per_order_round_up_at_count_10():
    for price in (0.11, 0.37, 0.5, 0.5123, 0.85, 0.99):
        raw = 0.07 * 10 * price * (1 - price)
        f = NB.order_fee(price, 10)
        assert f >= raw - 1e-12 and f - raw < 1e-6 + 1e-12            # rounded UP to a micro-dollar, never down, never by more
    assert NB.order_fee(0.5, 10) == pytest.approx(0.175)


def test_slippage_added_to_both_sides_and_fee_on_price_paid():
    d = A.decide(0.80, 0.58, 0.60, 0.0)
    assert d["side"] == "yes" and d["price"] == pytest.approx(0.61) and d["fee"] == pytest.approx(NB.fee_per_contract(0.61))
    d = A.decide(0.20, 0.40, 0.42, 0.0)
    assert d["side"] == "no" and d["price"] == pytest.approx(0.61) and d["fee"] == pytest.approx(NB.fee_per_contract(0.61))
    pnl = A.net_per_contract(d["side"], d["price"], d["fee"], "no")
    assert pnl == pytest.approx(1 - 0.61 - NB.fee_per_contract(0.61))


def test_coin_flip_uses_slippage():
    bid, ask = np.full(400, 0.48), np.full(400, 0.52)
    cf = A.coin_flip(bid, ask, ["yes"] * 400, 0.0, copies=50, seed=1)
    fee = NB.fee_per_contract(0.53)
    assert cf["copy_mean"] == pytest.approx(0.5 * (1 - 0.53 - fee) + 0.5 * (-0.53 - fee), abs=0.1)


def test_versions_are_four_betting_variants():
    assert P.THETAS == (0.03, 0.06) and len({P.version_name(t, pf) for t in P.THETAS for pf in (True, False)}) == 4
    assert A.SEED == 20261021


def test_pass_bar_all_six_conditions():
    disc = {"mean": 0.01}
    u = {"mean": 0.02, "boot_lo": 0.001, "n": 250, "usd_per_week": 3.0, "coin_flip": {"share": 0.02}}
    assert all(P.pass_bar(disc, u, 0.20, 0.20).values())
    for k, bad in (("mean", -0.01), ("boot_lo", -0.001), ("n", 199), ("usd_per_week", 1.99)):
        assert not all(P.pass_bar(disc, dict(u, **{k: bad}), 0.20, 0.20).values())
    assert not all(P.pass_bar(disc, dict(u, coin_flip={"share": 0.06}), 0.20, 0.20).values())
    assert not all(P.pass_bar({"mean": -0.01}, u, 0.20, 0.20).values())
    assert not all(P.pass_bar(disc, u, 0.203, 0.20).values()) and all(P.pass_bar(disc, u, 0.2019, 0.20).values())


# ------------------------------------------------------------------ split
def test_split_first_half_by_scheduled_start():
    g = [(100 + i, f"g{i}") for i in range(10)]
    d, u = D.split_games(reversed(g))
    assert d == {f"g{i}" for i in range(5)} and u == {f"g{i}" for i in range(5, 10)} and not (d & u)


# ------------------------------------------------------------------ price source
def test_candle_cache_parsing_and_404(tmp_path):
    p = tmp_path / "x.json.gz"
    assert D.read_cached_candles(p) is None                      # not downloaded
    with gzip.open(p, "wt") as fh:
        json.dump({"status": 404, "response": None}, fh)
    assert D.read_cached_candles(p) == []                          # cached 404 -> downloaded, no quote
    with gzip.open(p, "wt") as fh:
        json.dump({"status": 200, "response": {"candlesticks": [
            {"end_period_ts": 10, "yes_bid": {"close": "0.4000"}, "yes_ask": {"close": "0.4500"}, "volume": "3.00"},
            {"end_period_ts": 20, "yes_bid": {"close": None}, "yes_ask": {"close": "0.5"}, "volume": "0.00"}]}}, fh)
    c = D.read_cached_candles(p)
    assert c[0] == (10, 0.4, 0.45, 3.0) and c[1][1] is None


def test_candle_file_by_stat():
    assert "kalshi_props_hourly_3pt" in str(D.candle_file("3PT", "T", "2026-01-01"))
    f = D.candle_file("PTS", "T", "2026-01-01")
    assert f.parent.parent.name == "kalshi_props_hourly" and f.name == "T.json.gz"


def test_decision_candle_never_uses_candle_after_T_minus_1h():
    tip = 1_000_000
    cs = [(tip - 3600 - 3600, .3, .4, 0), (tip - 3600, .35, .45, 0), (tip - 3600 + 1, .9, .95, 0)]
    assert D.decision_candle(cs, tip)[0] == tip - 3600


def test_markets_outside_selection_counted_not_downloaded(tmp_path):
    box = _synth(tmp_path)

    class Store:
        def report_for_game(self, *a, **k):
            return None
    tip = 1_770_000_000
    mk = {"ticker": "KXNBAPTS-X-LALSTAR-30", "stat": "PTS", "game_date": "2026-02-09", "game_code": "BOSLAL", "team": "LAL",
          "player_key": "STAR", "player_name": "Star Player", "strike": 30, "result": "yes", "tip": tip, "tip_src": "kxnbagame",
          "downloaded": False, "n_candles": None, "has_candle": False, "cand_end": None, "age_min": None, "volume_c": None,
          "bid": None, "ask": None, "raw_bid": None, "raw_ask": None}
    rows, mrows, counts, games, _ = B.build([mk], box, Store())
    assert mrows[0]["quote_excl"] == "not_downloaded" and counts.get("excl_not_downloaded") == 1
    assert P.usable(mrows) == []
