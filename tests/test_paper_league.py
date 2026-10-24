"""Tests for scripts/paper_league.py (research log #67). Synthetic data only; no network."""
import gzip
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
import paper_league as pl  # noqa: E402

UTC = timezone.utc
TIP = datetime(2026, 10, 24, 0, 0, tzinfo=UTC)          # tip 00:00Z -> window [22:45Z, 23:00Z], ET date 2026-10-23
KEY = "26OCT23DETMIA"


def T(h, m=0, d=23):
    return datetime(2026, 10, d, h, m, tzinfo=UTC)


def mk(ts, strike, bid, ask, event="KXNBATOTAL-" + KEY, series="KXNBATOTAL", bs=50.0, as_=60.0):
    return {"type": "mkt", "ts_utc": pl.iso(ts), "series": series, "ticker": f"{event}-{int(strike + .5)}", "event_ticker": event,
            "floor_strike": strike, "yes_bid_f": bid, "yes_ask_f": ask, "yes_bid_size_f": bs, "yes_ask_size_f": as_,
            "status": "active", "expected_expiration_time": pl.iso(TIP + timedelta(hours=3))}


LADDER = [(220.5, .80, .84), (224.5, .64, .68), (228.5, .49, .52), (232.5, .30, .33), (236.5, .12, .14), (240.5, .01, .30)]


def snap(ts, ladder=LADDER, **kw):
    return [mk(ts, s, b, a, **kw) for s, b, a in ladder]


def write_tape(root, et_date, rows):
    d = Path(root) / "data" / "paper_tape" / et_date
    d.mkdir(parents=True, exist_ok=True)
    blob = gzip.compress(("\n".join(json.dumps(r) for r in rows) + "\n").encode())
    with open(d / "snap.jsonl.gz", "ab") as fh:
        fh.write(blob)


def write_pred(root, e1=232.0, ts=T(14), key=KEY, start=TIP):
    p = Path(root) / "logs" / "score_model"
    p.mkdir(parents=True, exist_ok=True)
    with open(p / "predictions.jsonl", "a") as fh:
        fh.write(json.dumps({"key": key, "e1_total": e1, "start_utc": pl.iso(start), "ts_utc": pl.iso(ts)}) + "\n")


def write_f42(root, proj, ts=T(18), game=KEY):
    p = Path(root) / "logs"
    p.mkdir(parents=True, exist_ok=True)
    with open(p / "early_overs_picks.jsonl", "a") as fh:
        fh.write(json.dumps({"ts": ts.isoformat(), "game": game, "proj": proj}) + "\n")


def no_proj(games):
    raise RuntimeError("projector should not be needed")


def league(root, now, **kw):
    kw.setdefault("projector", no_proj)
    kw.setdefault("fetch_market", lambda t: (_ for _ in ()).throw(AssertionError("no settlement GET expected")))
    return pl.League(root, now, **kw)


def decisions(root):
    return pl.read_jsonl(Path(root) / "logs" / "paper_league" / "decisions.jsonl")


# ---------------------------------------------------------------- fee / net arithmetic
def test_fee_51c_ask():
    p, fee = pl.entry_cost(51)
    assert float(p) == pytest.approx(0.52) and float(fee) == pytest.approx(0.0175)       # 0.07*.52*.48 = 0.017472 -> ceil to 0.0001


def test_fee_rounds_up_and_extremes():
    assert float(pl.fee_dollars("0.50")) == pytest.approx(0.0175)                  # exactly 0.0175
    assert float(pl.fee_dollars("0.01")) == pytest.approx(0.0007)                  # 0.000693 -> 0.0007
    assert pl.fee_dollars("1.00") == 0


def test_net_arithmetic():
    p, fee = pl.entry_cost(51)
    assert pl.net_of(1, p, fee) == pytest.approx(1 - 0.52 - 0.0175)
    assert pl.net_of(0, p, fee) == pytest.approx(-0.52 - 0.0175)


# ---------------------------------------------------------------- main strike / median
def test_main_strike_nearest_half_with_spread_limit():
    rows = snap(T(22, 50))
    assert pl.main_strike(rows)["floor_strike"] == 228.5                    # mid .505
    # the 240.5 strike has a 29c spread -> excluded even though others are far from .50
    assert pl.main_strike([mk(T(22, 50), 240.5, .01, .30)]) is None


def test_main_strike_tie_lower_strike_and_one_sided_ignored():
    rows = [mk(T(22, 50), 230.5, .49, .51), mk(T(22, 50), 226.5, .49, .51), mk(T(22, 50), 228.5, 0.0, .50)]
    assert pl.main_strike(rows)["floor_strike"] == 226.5


def test_implied_median_interpolation():
    rows = [mk(T(22, 50), 220.5, .80, .84), mk(T(22, 50), 228.5, .40, .44)]    # mids .82 and .42
    # crosses .50 at 220.5 + (.82-.5)/(.82-.42)*8 = 226.9
    assert pl.implied_median(rows) == pytest.approx(226.9)
    assert pl.implied_median([mk(T(22, 50), 220.5, .60, .64)]) is None       # no crossing


# ---------------------------------------------------------------- decision window
def test_window_selects_last_snapshot_in_window():
    rows = []
    for ts in (T(22, 40), T(22, 45), T(22, 52), T(23, 0), T(23, 1)):
        rows += snap(ts)
    ts, got = pl.window_snapshot(rows, TIP)
    assert ts == pl.iso(T(23, 0)) and len(got) == len(LADDER)               # inclusive upper bound; 23:01 is out; 22:40 is out


def test_window_none_when_empty():
    ts, got = pl.window_snapshot(snap(T(22, 40)) + snap(T(23, 5)), TIP)
    assert ts is None and got == []


def test_no_snapshot_in_window_gives_no_decision(tmp_path):
    write_pred(tmp_path)
    write_tape(tmp_path, "2026-10-23", snap(T(22, 40)))
    league(tmp_path, T(23, 30)).run_decisions()
    d = decisions(tmp_path)
    assert len(d) == 7 and {r["status"] for r in d} == {"no_decision"}
    assert "no tape snapshot" in d[0]["reason"]


def test_pending_before_window_passed(tmp_path):
    write_pred(tmp_path)
    write_tape(tmp_path, "2026-10-23", snap(T(22, 50)))
    lg = league(tmp_path, T(22, 59))                                          # window not over yet
    lg.run_decisions()
    assert decisions(tmp_path) == [] and lg.stats["pending"] == 1


def test_coverage_start(tmp_path):
    early = TIP - timedelta(hours=2)                                          # decision 21:00Z < 23:00Z coverage start
    write_pred(tmp_path, start=early)
    write_tape(tmp_path, "2026-10-23", snap(T(20, 0), ))
    league(tmp_path, T(23, 30)).run_decisions()
    assert decisions(tmp_path) == []


# ---------------------------------------------------------------- full NBA decision + rules + time-stamp guards
def full_nba(tmp_path, proj=240.0, e1=236.0, e1_ts=T(14), proj_ts=T(18)):
    write_pred(tmp_path, e1=e1, ts=e1_ts)
    if proj is not None:
        write_f42(tmp_path, proj, ts=proj_ts)
    write_tape(tmp_path, "2026-10-23", snap(T(22, 40)) + snap(T(22, 52)))
    lg = league(tmp_path, T(23, 5))
    lg.run_decisions()
    return {r["rule"]: r for r in decisions(tmp_path)}


def test_nba_rules_and_entry(tmp_path):
    d = full_nba(tmp_path, proj=240.0, e1=236.0)                              # main strike 228.5
    assert d["N1"]["status"] == d["N2"]["status"] == "bet" and d["N1"]["side"] == "OVER" == d["N2"]["side"]   # gap +11.5
    assert d["N3"]["side"] == "OVER" and d["N4"]["status"] == "no_bet"          # E1 gap +7.5: >=4 yes, >=8 no
    assert d["N5"]["side"] == "UNDER" and d["N6"]["side"] == "OVER"
    o, u = d["N6"], d["N5"]
    assert o["contract"] == "YES" and o["ask_c"] == 52 and o["price"] == pytest.approx(0.53)
    assert o["fee"] == pytest.approx(0.07 * .53 * .47, abs=1e-4) and o["ask_size"] == 60.0
    assert u["contract"] == "NO" and u["ask_c"] == 51 and u["price"] == pytest.approx(0.52) and u["fee"] == pytest.approx(0.0175)
    assert u["ask_size"] == 50.0                                              # NO ask size = YES bid size
    assert o["snapshot_ts"] == pl.iso(T(22, 52)) and o["strike"] == 228.5 and o["implied_median"] is not None


def test_nba_negative_gap_goes_under(tmp_path):
    d = full_nba(tmp_path, proj=224.0, e1=229.0)                              # F42 gap -4.5, E1 gap +0.5
    assert d["N1"]["side"] == "UNDER" and d["N2"]["status"] == "no_bet" and d["N3"]["status"] == "no_bet"


def test_e1_later_than_decision_is_not_used(tmp_path):
    d = full_nba(tmp_path, e1_ts=T(22, 53))                                   # locked after the 22:52 snapshot
    assert d["N3"]["status"] == d["N4"]["status"] == "no_decision" and "after the decision" in d["N3"]["reason"]
    assert d["N1"]["status"] == "bet" and d["N5"]["status"] == "bet"


def test_f42_logged_later_than_decision_triggers_compute_not_lookahead(tmp_path):
    write_pred(tmp_path)
    write_f42(tmp_path, 240.0, ts=T(22, 58))                                  # after the 22:52 snapshot -> ignored
    write_tape(tmp_path, "2026-10-23", snap(T(22, 52)))
    calls = []
    lg = league(tmp_path, T(23, 5), projector=lambda gs: calls.append(gs) or {KEY: 200.0})
    lg.run_decisions()
    d = {r["rule"]: r for r in decisions(tmp_path)}
    assert calls and d["N1"]["f42_proj"] == 200.0 and d["N1"]["proj_source"].startswith("computed") and d["N1"]["side"] == "UNDER"


def test_projection_failure_defers_then_no_decision(tmp_path):
    write_pred(tmp_path)
    write_tape(tmp_path, "2026-10-23", snap(T(22, 52)))
    bad = lambda gs: (_ for _ in ()).throw(RuntimeError("net down"))
    lg = league(tmp_path, T(23, 5), projector=bad)
    lg.run_decisions()
    assert decisions(tmp_path) == [] and lg.stats["deferred"] == 1             # retried next run
    league(tmp_path, TIP + timedelta(hours=4), projector=bad).run_decisions()
    d = {r["rule"]: r for r in decisions(tmp_path)}
    assert d["N1"]["status"] == "no_decision" and d["N5"]["status"] == "bet"


def test_no_e1_prediction(tmp_path):
    write_tape(tmp_path, "2026-10-23", snap(T(22, 52)))
    write_f42(tmp_path, 240.0)
    # no predictions.jsonl: tip falls back to tape expected_expiration_time - 3h (logged in the record)
    league(tmp_path, T(23, 5)).run_decisions()
    d = {r["rule"]: r for r in decisions(tmp_path)}
    assert d["N3"]["status"] == "no_decision" and "FALLBACK" in d["N1"]["tip_source"]


# ---------------------------------------------------------------- coin flip
def test_coin_flip_deterministic_and_matches_spec():
    for k in ("26OCT23DETMIA", "26OCT25CINBAL", "x", "y", "z"):
        par = int(hashlib.sha256((k + "20261027").encode()).hexdigest(), 16) % 2
        assert pl.coin_side(k) == ("OVER" if par == 0 else "UNDER") and pl.coin_side(k) == pl.coin_side(k)
    assert len({pl.coin_side(str(i)) for i in range(50)}) == 2


# ---------------------------------------------------------------- idempotency
def test_idempotent_rerun_no_duplicates(tmp_path):
    full_nba(tmp_path)
    before = (tmp_path / "logs" / "paper_league" / "decisions.jsonl").read_bytes()
    for _ in range(2):
        league(tmp_path, T(23, 20)).run_decisions()
    assert (tmp_path / "logs" / "paper_league" / "decisions.jsonl").read_bytes() == before
    ids = [(r["sport"], r["game_id"], r["rule"]) for r in decisions(tmp_path)]
    assert len(ids) == len(set(ids)) == 7


# ---------------------------------------------------------------- settlement
def market(result, status="finalized"):
    return {"market": {"ticker": "t", "status": status, "result": result, "expiration_value": "233"}}


def test_parse_settlement():
    assert pl.parse_settlement(market("yes"))["result"] == "yes"
    assert pl.parse_settlement(market("no", "settled"))["result"] == "no"
    assert pl.parse_settlement(market("", "active")) is None
    assert pl.parse_settlement({}) is None
    assert pl.parse_settlement(market("void", "voided"))["result"] == "void"


def test_settlement_rows_and_summary(tmp_path):
    full_nba(tmp_path, proj=240.0, e1=236.0)
    calls = []

    def fetch(t):
        calls.append(t)
        return market("yes")                                                  # total > 228.5 -> YES

    lg = pl.League(tmp_path, TIP + timedelta(hours=3), projector=no_proj, fetch_market=fetch)
    lg.run_settlements()
    assert len(calls) == 1                                                    # one GET per ticker, shared by all rules
    s = {r["rule"]: r for r in pl.read_jsonl(tmp_path / "logs" / "paper_league" / "settlements.jsonl")}
    assert s["N6"]["payout"] == 1 and s["N6"]["net"] == pytest.approx(1 - 0.53 - float(pl.fee_dollars("0.53")))
    assert s["N5"]["payout"] == 0 and s["N5"]["net"] == pytest.approx(-0.52 - 0.0175)
    lg.run_settlements()                                                      # idempotent
    assert len(pl.read_jsonl(tmp_path / "logs" / "paper_league" / "settlements.jsonl")) == len(s)
    summ = lg.write_summary()
    assert summ["rules"]["N6"]["won"] == 1 and summ["rules"]["N5"]["lost"] == 1 and summ["rules"]["N4"]["n"] == 0
    assert summ["rules"]["N6"]["mean_net"] == pytest.approx(s["N6"]["net"])
    assert "2026-10-23" in summ["rules"]["N6"]["by_night"]


def test_no_settlement_get_before_game_can_be_over(tmp_path):
    full_nba(tmp_path)
    league(tmp_path, TIP + timedelta(hours=1)).run_settlements()              # fetch_market would raise if called


def test_unsettled_market_stays_open(tmp_path):
    full_nba(tmp_path)
    lg = pl.League(tmp_path, TIP + timedelta(hours=3), projector=no_proj, fetch_market=lambda t: market("", "active"))
    lg.run_settlements()
    summ = lg.write_summary()
    assert summ["rules"]["N5"]["open"] == 1 and summ["rules"]["N5"]["won"] == 0


# ---------------------------------------------------------------- NFL
def nfl_game(**kw):
    g = {"game_id": "2026_07_CIN_BAL", "gameday": "2026-10-25", "gametime": "13:00", "weekday": "Sunday", "game_type": "REG",
         "week": 7, "away": "CIN", "home": "BAL", "roof": "outdoors", "stadium_id": "BAL00",
         "ko": datetime(2026, 10, 25, 17, 0, tzinfo=UTC)}
    g.update(kw)
    return g


def fc(game_id, fetch, wind):
    return {game_id: [(fetch, wind, {})]}


def test_nfl_b2_b3_and_guard():
    g = nfl_game()
    cats = {"BAL00": "open-air"}
    ctx = lambda f: {"key": "26OCT25CINBAL", "categories": cats, "cat_reason": None, "forecasts": f}
    ok = g["ko"] - timedelta(hours=30)
    r = pl.evaluate_nfl(g, ctx(fc(g["game_id"], ok, 14.0)))
    assert r["B2"][:2] == ("bet", "UNDER") and r["B3"][0] == "no_bet"
    r = pl.evaluate_nfl(g, ctx(fc(g["game_id"], ok, 22.0)))
    assert r["B3"][:2] == ("bet", "UNDER") and r["B2"][0] == "no_bet"
    # forecast fetched < 24 h before kickoff -> no B2/B3
    r = pl.evaluate_nfl(g, ctx(fc(g["game_id"], g["ko"] - timedelta(hours=23), 14.0)))
    assert r["B2"][0] == r["B3"][0] == "no_decision" and "24 h" in r["B2"][2]
    # latest fetch <= ko-24h wins over an older one and over a later (illegal) one
    f = {g["game_id"]: [(g["ko"] - timedelta(hours=48), 5.0, {}), (g["ko"] - timedelta(hours=25), 12.0, {}),
                        (g["ko"] - timedelta(hours=3), 25.0, {})]}
    assert pl.evaluate_nfl(g, ctx(f))["B2"][0] == "bet"
    # no forecast rows at all
    assert pl.evaluate_nfl(g, ctx({}))["B2"][0] == "no_decision"


def test_nfl_stadium_file_absent_and_not_open_air():
    g = nfl_game()
    f = fc(g["game_id"], g["ko"] - timedelta(hours=30), 14.0)
    r = pl.evaluate_nfl(g, {"key": "k", "categories": None, "cat_reason": "stadiums.csv missing", "forecasts": f})
    assert r["B2"][0] == "no_decision" and "missing" in r["B2"][2] and r["NFL-UNDER"][0] == "bet"
    r = pl.evaluate_nfl(nfl_game(roof="dome"), {"key": "k", "categories": {"BAL00": "open-air"}, "cat_reason": None, "forecasts": f})
    assert r["B2"][0] == "no_bet"
    r = pl.evaluate_nfl(g, {"key": "k", "categories": {"BAL00": "dome"}, "cat_reason": None, "forecasts": f})
    assert r["B2"][0] == "no_bet"


def test_nfl_primetime_and_thursday():
    c = {"key": "k", "categories": None, "cat_reason": "x", "forecasts": {}}
    assert pl.evaluate_nfl(nfl_game(gametime="20:20"), c)["B4"][0] == "bet"
    assert pl.evaluate_nfl(nfl_game(gametime="16:25"), c)["B4"][0] == "no_bet"
    assert pl.evaluate_nfl(nfl_game(weekday="Monday", gametime="20:15"), c)["B4"][0] == "bet"
    assert pl.evaluate_nfl(nfl_game(weekday="Thursday", gametime="20:15", week=8), c)["B5"][0] == "bet"
    assert pl.evaluate_nfl(nfl_game(weekday="Thursday", gametime="20:15", week=1), c)["B5"][0] == "no_bet"
    assert pl.evaluate_nfl(nfl_game(weekday="Thursday", gametime="20:15", week=8, game_type="POST"), c)["B5"][0] == "no_bet"


def test_nfl_end_to_end(tmp_path):
    ko = datetime(2026, 10, 25, 17, 0, tzinfo=UTC)
    csv = tmp_path / "games.csv"
    csv.write_text("game_id,season,game_type,week,gameday,weekday,gametime,away_team,home_team,roof,stadium_id\n"
                   "2026_07_CIN_BAL,2026,REG,7,2026-10-25,Sunday,13:00,CIN,BAL,outdoors,BAL00\n"
                   "2026_06_X_Y,2026,REG,6,2026-10-18,Sunday,13:00,X,Y,outdoors,Y00\n")        # before coverage start
    ev = "KXNFLTOTAL-26OCT25CINBAL"
    rows = snap(ko - timedelta(minutes=70), ladder=[(44.5, .48, .52), (47.5, .30, .34)], event=ev, series="KXNFLTOTAL")
    write_tape(tmp_path, "2026-10-25", rows)
    lg = league(tmp_path, ko, games_csv=csv)
    lg.run_decisions()
    d = {r["rule"]: r for r in decisions(tmp_path)}
    assert set(d) == set(pl.SPORT_RULES["NFL"]) and all(r["game_id"] == "2026_07_CIN_BAL" for r in d.values())
    assert d["NFL-UNDER"]["strike"] == 44.5 and d["NFL-UNDER"]["ask_c"] == 52 and d["B2"]["status"] == "no_decision"
    assert d["B4"]["status"] == "no_bet"


# ---------------------------------------------------------------- run() wrapper
def test_run_writes_summary_heartbeat_and_lock_released(tmp_path):
    full_nba(tmp_path)
    stats = pl.run(tmp_path, T(23, 30), projector=no_proj, fetch_market=lambda t: market("", "active"))
    assert (tmp_path / "logs" / "paper_league" / "summary.json").exists()
    assert (tmp_path / "logs" / "paper_league.heartbeat").exists()
    assert not (tmp_path / "logs" / "paper_league" / "run.lock").exists() and stats["errors"] == []


def test_load_wind_forecasts_reads_the_forward_fetchers_nested_values(tmp_path):
    # scripts/nfl_wind_forecast.py writes {"fetch_utc", "game_id", ..., "values": {"wind_mph": ...}}
    d = tmp_path / "data" / "paper_tape" / "nfl_wind"
    d.mkdir(parents=True)
    rows = [{"fetch_utc": "2026-10-23T22:25:53Z", "game_id": "2026_07_CIN_BAL", "kickoff_utc": "2026-10-25T17:00:00Z",
             "values": {"valid_hour_utc": "2026-10-25T17:00:00Z", "wind_mph": 12.5, "gust_mph": 20.0}},
            {"fetch_utc": "2026-10-24T22:25:53Z", "game_id": "2026_07_CIN_BAL", "wind_mph": 3.0}]
    (d / "2026-10-23.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    got = pl.load_wind_forecasts(tmp_path)["2026_07_CIN_BAL"]
    assert sorted(w for _, w, _ in got) == [3.0, 12.5]
