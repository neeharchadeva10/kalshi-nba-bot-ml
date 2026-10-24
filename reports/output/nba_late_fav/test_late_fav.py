"""Unit tests for research log #35 (run: .venv/Scripts/python.exe -m pytest reports/output/nba_late_fav/test_late_fav.py)."""
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
import run  # noqa: E402

T0 = 1_800_000_000          # arbitrary epoch


# ------------------------------------------------------------------------------------------------ helpers
def pbp_df(rows, home="SA", away="NY", gid=1, date_s="2026-06-13", season_type=3):
    """rows: (period, clock_s, wall_offset_s, home_score, away_score) in sequence order."""
    recs = []
    for k, (per, cs, w, hs, as_) in enumerate(rows):
        recs.append(dict(game_id=gid, game_play_number=k, sequence_number=k, period=per, clock_minutes=int(cs // 60),
                         clock_seconds=float(cs % 60), wall=T0 + w, home_team_abbrev=home, away_team_abbrev=away,
                         game_date=date_s, season_type=season_type, home_score=hs, away_score=as_))
        recs[-1]["clock_s"] = float(cs)
    return pd.DataFrame(recs)


def toy_game(extra_ot=False, with_q4=True):
    rows = [(1, 720, 0, 0, 0), (1, 300, 600, 20, 18), (2, 300, 1500, 50, 40), (3, 300, 2400, 70, 60)]
    if with_q4:
        rows += [(4, 720, 3000, 70, 60), (4, 400, 3300, 80, 70), (4, 360, 3400, 82, 70), (4, 359, 3410, 82, 72),
                 (4, 181, 3900, 90, 80), (4, 180, 3960, 92, 80), (4, 0, 4500, 100, 90)]
    if extra_ot:
        rows += [(5, 300, 4700, 105, 105), (5, 0, 5000, 110, 108)]
    return lib.build_game(1, pbp_df(rows))


def mk_series(pts):
    ts = np.array([p[0] for p in pts], dtype=np.int64)
    return lib.Series(ts, np.array([p[1] for p in pts], float), np.array([p[2] for p in pts], float), np.zeros(len(pts)))


# ------------------------------------------------------------------------------------------------ fee
def test_fee_known_values():
    assert lib.fee_e4(0.50) == 1750            # 0.07*10*0.25 = 0.175 exactly, must not round up
    assert lib.fee(0.50) == pytest.approx(0.175)
    assert lib.fee_e4(0.80) == 1120            # 0.07*10*0.16 = 0.112
    assert lib.fee_e4(0.91) == 574             # 0.07*10*0.0819 = 0.05733 -> ceil at 1e-4 -> 0.0574
    assert lib.fee_e4(0.01) == 70              # 0.07*10*0.0099 = 0.00693 -> 0.0070


def test_fee_ceil_not_round():
    # 0.07 * 10 * 0.91 * 0.09 = 0.05733 -> ceil to 4 dp = 0.0574 = 574 (round-half-even would give 573)
    for pc in range(1, 100):
        p = pc / 100
        exact = 0.07 * 10 * p * (1 - p)
        assert lib.fee_e4(p) / 1e4 >= exact - 1e-12
        assert lib.fee_e4(p) / 1e4 - exact < 1e-4 + 1e-12


def test_net_per_contract():
    assert lib.net_per_contract(0.80, True) == pytest.approx(1 - 0.80 - 0.112 / 10)
    assert lib.net_per_contract(0.80, False) == pytest.approx(-0.80 - 0.112 / 10)


# ------------------------------------------------------------------------------------------------ pbp / E_X
def test_entry_event_clock_exactly_x():
    g = toy_game()
    assert g.t[360] == T0 + 3400            # event at 6:00 exactly counts (<= X:00), first in sequence order
    assert g.t[180] == T0 + 3960
    assert g.margin[360] == 12


def test_entry_skips_earlier_periods_and_ot():
    g = toy_game(extra_ot=True)
    assert g.t[360] == T0 + 3400            # OT (period 5) low clocks never selected; Q1-3 clocks <= 360 ignored
    assert g.max_period == 5


def test_missing_q4():
    g = toy_game(with_q4=False)
    assert g.t[360] is None and g.t[180] is None


def test_q4_never_reaches_threshold():
    rows = [(4, 720, 0, 0, 0), (4, 500, 100, 5, 5), (4, 400, 200, 8, 8)]
    g = lib.build_game(1, pbp_df(rows))
    assert g.t[360] is None


def test_entry_first_in_sequence_even_if_wallclock_steps_back():
    i, w = lib.entry_event_time([4, 4, 4], [361, 359, 300], [100, 90, 95], 360)
    assert (i, w) == (1, 90.0)


def test_period_at():
    g = toy_game()
    assert lib.period_at(g, T0 - 5) is None
    assert lib.period_at(g, T0 + 700) == 1
    assert lib.period_at(g, T0 + 3000) == 4


# ------------------------------------------------------------------------------------------------ decision minute / quotes (no look-ahead)
def test_decision_minute_whole_minute_at_or_after():
    assert lib.decision_minute(3400) == 3480           # 3460 -> next whole minute
    assert lib.decision_minute(3420) == 3480           # exactly on a boundary stays
    assert lib.decision_minute(3421) == 3540
    for t in range(0, 600, 7):
        e = lib.decision_minute(t)
        assert e % 60 == 0 and t + 60 <= e < t + 120


def test_latest_quote_carry_forward_and_staleness():
    s = mk_series([(1000, 0.1, 0.2), (1200, 0.3, 0.4), (2000, 0.5, 0.6)])
    assert s.latest(1200) == (1200, 0.3, 0.4)                  # candle ending exactly at E counts
    assert s.latest(1199) == (1000, 0.1, 0.2)                  # never the later candle
    assert s.latest(1799) == (1200, 0.3, 0.4)                  # carried forward (age 599 s)
    assert s.latest(1801) is None                              # 601 s > 10 min -> stale
    assert s.latest(999) is None                               # nothing before


def _ev(tip):
    return dict(markets={"SAS": "T-SAS", "NYK": "T-NYK"}, result={"SAS": "yes", "NYK": "no"}, tip=tip, settled=None)


def test_decision_never_uses_candle_after_E():
    g = toy_game()
    tX = g.t[360]
    E = lib.decision_minute(tX)
    ev = _ev(T0 - 7200)
    series = {"T-SAS": mk_series([(E - 30, 0.79, 0.80), (E + 60, 0.84, 0.95)]),
              "T-NYK": mk_series([(E - 30, 0.19, 0.21), (E + 60, 0.14, 0.16)])}
    r = lib.decision(ev, series, g, 360, (0.70, 0.92))
    assert r["status"] == "trade" and r["E"] == E and r["ask"] == 0.80 and r["team"] == "SAS"     # carried-forward, not the E+60 candle
    assert r["E"] >= tX + 60


def test_decision_stale_quote_skip():
    g = toy_game()
    E = lib.decision_minute(g.t[360])
    series = {"T-SAS": mk_series([(E - 700, 0.79, 0.80)]), "T-NYK": mk_series([(E - 700, 0.19, 0.21)])}
    assert lib.decision(_ev(T0 - 7200), series, g, 360, (0.70, 0.92))["reason"] == "stale_quote"


def test_decision_band_edges():
    g = toy_game()
    E = lib.decision_minute(g.t[360])
    series = {"T-SAS": mk_series([(E, 0.93, 0.94)]), "T-NYK": mk_series([(E, 0.05, 0.07)])}
    assert lib.decision(_ev(T0 - 7200), series, g, 360, (0.70, 0.92))["reason"] == "none_in_band"
    r = lib.decision(_ev(T0 - 7200), series, g, 360, (0.80, 0.95))
    assert r["status"] == "trade" and r["ask"] == 0.94


def test_bad_wallclock_rules():
    g = toy_game()                                    # tX = T0 + 3400
    assert lib.decision(_ev(T0 - 7200), {}, g, 360, (0.7, 0.92))["reason"] != "bad_wallclock"   # 2.94 h after start
    assert lib.decision(_ev(T0), {}, g, 360, (0.7, 0.92))["reason"] == "bad_wallclock"           # 0.94 h < 1.25 h
    assert lib.decision(_ev(T0 - 5 * 3600), {}, g, 360, (0.7, 0.92))["reason"] == "bad_wallclock"  # > 4 h
    rows = [(4, 720, 3000, 0, 0), (4, 600, 3600, 0, 0), (4, 500, 3200, 0, 0), (4, 360, 3700, 0, 0)]   # 400 s backstep in Q4
    g2 = lib.build_game(1, pbp_df(rows))
    assert g2.max_backstep_q4[360] == 400
    assert not lib.wallclock_ok(g2, 360, T0 + 3700 - 3 * 3600)
    rows[2] = (4, 500, 3500, 0, 0)                                                              # 100 s backstep: fine
    g3 = lib.build_game(1, pbp_df(rows))
    assert lib.wallclock_ok(g3, 360, T0 + 3700 - 3 * 3600)


# ------------------------------------------------------------------------------------------------ band
def test_pick_favorite():
    assert lib.pick_favorite({"A": 0.70, "B": 0.31}, 0.70, 0.92) == ("A", "ok")        # inclusive lower edge
    assert lib.pick_favorite({"A": 0.92, "B": 0.09}, 0.70, 0.92) == ("A", "ok")        # inclusive upper edge
    assert lib.pick_favorite({"A": 0.93, "B": 0.08}, 0.70, 0.92)[0] is None
    assert lib.pick_favorite({"A": 0.69, "B": 0.32}, 0.70, 0.92)[0] is None
    assert lib.pick_favorite({"A": float("nan"), "B": 0.80}, 0.70, 0.92) == ("B", "ok")
    assert lib.pick_favorite({"A": 0.75, "B": 0.75}, 0.70, 0.92) == (None, "both_in_band")


# ------------------------------------------------------------------------------------------------ confirmed fill
def test_confirmed_fill():
    E, ask = 1000.0, 0.85
    pr = [(1000.0, 0.85, 4, "yes"), (1030.0, 0.84, 6, "yes")]
    assert lib.confirmed_fill(pr, E, ask) == (True, 10)
    assert lib.confirmed_fill(pr + [(1100, 0.80, 50, "yes")], E, ask)[1] == 10             # outside window
    assert lib.confirmed_fill([(1000, 0.86, 20, "yes")], E, ask)[0] is False                # price above ask
    assert lib.confirmed_fill([(1000, 0.85, 20, "no")], E, ask)[0] is False                 # wrong taker side
    assert lib.confirmed_fill([(999.5, 0.85, 20, "yes")], E, ask)[0] is False                # before E
    assert lib.confirmed_fill([(1060.0, 0.85, 10, "yes")], E, ask)[0] is True                # E+60 inclusive
    assert lib.confirmed_fill([(1000, 0.85, 9, "yes")], E, ask)[0] is False                  # 9 < 10


# ------------------------------------------------------------------------------------------------ matching
def test_parse_and_match():
    assert lib.parse_event_ticker("KXNBAGAME-26JUN13NYKSAS") == (date(2026, 6, 13), "NYK", "SAS")
    assert lib.parse_event_ticker("KXNBAGAME-26XXX13NYKSAS") is None
    assert set(lib.ESPN_TO_KALSHI.values()).__len__() == 30
    g1 = lib.build_game(1, pbp_df([(1, 720, 0, 0, 0)], home="SA", away="NY", date_s="2026-06-13", gid=1))
    g2 = lib.build_game(2, pbp_df([(1, 720, 0, 0, 0)], home="GS", away="UTAH", date_s="2026-06-14", gid=2))
    pbp = {1: g1, 2: g2}
    ev = {"KXNBAGAME-26JUN13NYKSAS": (date(2026, 6, 13), "NYK", "SAS"),
          "KXNBAGAME-26JUN13UTAGSW": (date(2026, 6, 13), "UTA", "GSW"),          # pbp date is 06-14: matched by +1 day, reported
          "KXNBAGAME-26JUN13LALBOS": (date(2026, 6, 13), "LAL", "BOS")}           # no such game
    m, un, sh = lib.match_games(ev, pbp)
    assert m == {"KXNBAGAME-26JUN13NYKSAS": 1, "KXNBAGAME-26JUN13UTAGSW": 2}
    assert un == ["KXNBAGAME-26JUN13LALBOS"] and sh == ["KXNBAGAME-26JUN13UTAGSW"]


def test_real_pbp_abbreviations_covered():
    p = Path(__file__).resolve().parents[3] / "data" / "raw" / "nba_open" / "play_by_play_2026.parquet"
    if not p.exists():
        pytest.skip("no pbp")
    d = pd.read_parquet(p, columns=["home_team_abbrev", "away_team_abbrev", "season_type"])
    d = d[d.season_type.isin([2, 3, 5])]
    abbrs = set(d.home_team_abbrev) | set(d.away_team_abbrev)
    allstar = {'STARS', 'STRIPES', 'WORLD', 'CAN', 'CHK', 'KEN', 'SHQ'}     # All-Star game pseudo-teams (never on Kalshi)
    assert abbrs - allstar <= set(lib.ESPN_TO_KALSHI), abbrs - allstar - set(lib.ESPN_TO_KALSHI)


# ------------------------------------------------------------------------------------------------ bootstrap / coin flips
def test_bootstrap_deterministic():
    v = np.random.default_rng(1).normal(0.01, 0.3, 200)
    a, b = lib.boot_lower(v), lib.boot_lower(v)
    assert a == b and a[0] < v.mean()
    assert a[0] <= a[1]
    c = lib.boot_lower(v, seed=1)
    assert c != a


def test_coin_candidates_period_and_price():
    minutes = [dict(period=1, asks={"A": 0.80, "B": 0.21}), dict(period=3, asks={"A": 0.82, "B": 0.19}),
               dict(period=3, asks={"A": 0.835, "B": 0.17}), dict(period=4, asks={"A": 0.80, "B": 0.2}),
               dict(period=2, asks={"A": float("nan"), "B": 0.80})]
    c = lib.coin_candidates_matched(minutes, 0.81, 0.01)
    assert c == [(0, "A"), (1, "A"), (4, "B")]
    assert all(minutes[i]["period"] in (1, 2, 3) for i, _ in c)
    assert (3, "A") not in c                      # period 4 excluded
    assert (2, "A") not in c                      # 0.835 is 2.5c away


def test_coin_flip_deterministic_and_skips_empty():
    groups = [[[0.1, -0.9], [0.1]], [], [[-0.5]]]
    m1, n1 = lib.coin_flip(groups, 50, lib.SEED)
    m2, n2 = lib.coin_flip(groups, 50, lib.SEED)
    assert n1 == 2 and np.array_equal(m1, m2)
    assert set(np.round(m1, 6)) <= {round((a - 0.5) / 2, 6) for a in (0.1, -0.9)}


# ------------------------------------------------------------------------------------------------ end to end on a toy universe
def toy_universe():
    """Four games on consecutive days; SAS-like home favourite priced 0.85 at E_X, wins in games 0,1,3, loses in game 2."""
    games, series, prints = [], {}, {}
    for k in range(4):
        pg = lib.build_game(k, pbp_df([(1, 720, 0, 0, 0), (2, 300, 1500, 50, 40), (4, 720, 3000, 70, 60), (4, 360, 3400, 82, 70),
                                       (4, 0, 4500, 100, 90)], gid=k))
        tip = T0 + k * 86400 - 7200
        # shift pbp wallclock to the game's day
        pg.t = {X: (w + k * 86400 if w else w) for X, w in pg.t.items()}
        pg.wall_sorted = pg.wall_sorted + k * 86400
        pg.last_wall += k * 86400
        pg.first_wall += k * 86400
        tX = pg.t[360]
        fav, dog = f"E{k}-SAS", f"E{k}-NYK"
        # a pre-game-ish minute inside Q2 at 0.85 (matched coin candidate), then the E_X minute
        series[fav] = mk_series([(T0 + k * 86400 + 1620, 0.84, 0.85), (tX + 60, 0.84, 0.85)])
        series[dog] = mk_series([(T0 + k * 86400 + 1620, 0.14, 0.16), (tX + 60, 0.14, 0.16)])
        E_ = lib.decision_minute(tX)
        prints[fav] = [(E_ + 10, 0.85, 12, "yes")] if k != 1 else [(E_ + 10, 0.85, 3, "yes")]
        res_f = "no" if k == 2 else "yes"
        ev = dict(markets={"SAS": fav, "NYK": dog}, result={"SAS": res_f, "NYK": "yes" if res_f == "no" else "no"},
                  tip=tip, settled=tip + 9000)
        games.append((f"KXNBAGAME-26JUN{10 + k}NYKSAS", ev, pg))
    return games, series, prints


def test_grid_is_16():
    assert lib.XS == (540, 360, 180, 90)
    assert set(lib.BANDS) == {"60-80", "70-92", "80-95", "90-97"}
    assert lib.BANDS["60-80"] == (0.60, 0.80) and lib.BANDS["90-97"] == (0.90, 0.97)
    assert len(lib.XS) * len(lib.BANDS) == 16
    assert lib.PRIMARY == (360, "70-92")


def test_slippage_applied_and_fee_on_paid_price():
    assert lib.entry_price(0.85) == 0.86 and lib.entry_price(0.97) == 0.98
    games, series, prints = toy_universe()
    tr, _ = run.run_version(games, series, prints, 360, "70-92")
    for t in tr:
        assert t["ask"] == 0.85 and t["paid"] == 0.86                       # displayed ask kept for fill check, paid = ask + 1c
        assert t["fee"] == lib.fee(0.86)                                    # fee on the paid price
        assert t["net_pc"] == pytest.approx((1.0 if t["win"] else 0.0) - 0.86 - lib.fee(0.86) / 10)


def test_end_to_end_toy():
    games, series, prints = toy_universe()
    ev = run.evaluate(games, series, prints, versions={(360, "70-92")}, splits=("discovery", "unseen"))
    res, trades = ev["results"], ev["trades"]
    r = res["X6m_70-92"]
    assert len(trades) == 4 and [t["split"] for t in trades] == ["discovery"] * 2 + ["unseen"] * 2
    assert all(t["ask"] == 0.85 and t["home"] == 1 for t in trades)
    assert [t["confirmed"] for t in trades] == [1, 0, 1, 1]            # fill check uses the displayed ask 0.85, not 0.86
    win_net, lose_net = lib.net_per_contract(0.86, True), lib.net_per_contract(0.86, False)
    assert trades[2]["net_pc"] == pytest.approx(lose_net) and trades[0]["net_pc"] == pytest.approx(win_net)
    assert r["unseen"]["n"] == 2 and r["unseen"]["mean_net_per_contract"] == pytest.approx((win_net + lose_net) / 2)
    assert r["confirmed_unseen"]["n"] == 2
    assert r["unseen_weeks_with_games"] >= 1
    # matched coin flip draws from the Q2 minute (period 2) at 0.85 (+1c slippage) -> same payoff as the real trade here
    cf = r["coin_flips"]["unseen"]["matched_p1to3"]
    assert cf["n_trades_with_candidates"] == 2
    assert cf["copy_mean_avg"] == pytest.approx((win_net + lose_net) / 2)
    again = run.evaluate(games, series, prints, versions={(360, "70-92")}, splits=("discovery", "unseen"))["results"]
    assert again["X6m_70-92"]["coin_flips"] == r["coin_flips"] and again["X6m_70-92"]["unseen_bootstrap_lower_2_5"] == r["unseen_bootstrap_lower_2_5"]


def test_discovery_mode_never_reads_unseen_results():
    games, series, prints = toy_universe()

    class Poison(dict):
        def get(self, *a, **k):
            raise AssertionError("unseen result was read")
    for et, ev, pg in games[2:]:
        ev["result"] = Poison()
    out = run.evaluate(games, series, prints, versions={(360, "70-92")}, splits=("discovery",))
    assert out["results"]["X6m_70-92"]["discovery"]["n"] == 2 and "unseen" not in out["results"]["X6m_70-92"]
    assert all(t["split"] == "discovery" for t in out["trades"])


def test_select_version_best_lb_among_eligible():
    stats = {"a": dict(n=150, lb=0.01), "b": dict(n=99, lb=0.50), "c": dict(n=100, lb=0.02), "d": dict(n=300, lb=float("nan")),
             "e": dict(n=120, lb=-0.1)}
    sel, elig = lib.select_version(stats)
    assert sel == "c" and elig == ["a", "c", "e"]                     # b has < 100 trades, d has no LB
    assert lib.select_version({"x": dict(n=10, lb=1.0)}) == (None, [])
    assert lib.select_version({"x": dict(n=100, lb=0.1), "w": dict(n=100, lb=0.1)})[0] == "w"   # deterministic tie-break


def test_luck_test_deterministic_and_selection_rerun():
    rng = np.random.default_rng(3)
    groups = {v: [[[float(x)] for x in rng.normal(0, 0.3, 4)] for _ in range(30)] for v in ("v1", "v2", "v3")}
    groups["v2"][5] = []                                              # a trade without candidates is dropped, not an error
    a = lib.luck_test(groups, ["v1", "v2", "v3"], 0.0, reps=20, seed=lib.SEED2, draws=300)
    b = lib.luck_test(groups, ["v1", "v2", "v3"], 0.0, reps=20, seed=lib.SEED2, draws=300)
    assert np.array_equal(a["best_lb_per_rep"], b["best_lb_per_rep"]) and a["share_ge_real"] == b["share_ge_real"]
    c = lib.luck_test(groups, ["v1", "v2", "v3"], 0.0, reps=20, seed=lib.SEED2 + 1, draws=300)
    assert not np.array_equal(a["best_lb_per_rep"], c["best_lb_per_rep"])
    one = lib.luck_test(groups, ["v1"], 0.0, reps=20, seed=lib.SEED2, draws=300)
    assert (a["best_lb_per_rep"] >= one["best_lb_per_rep"] - 1).all()   # sanity: shapes
    # the rep result is the MAX over eligible versions: with all copies identical per version the best equals the best constant
    const = {"lo": [[[0.1]] for _ in range(10)], "hi": [[[0.3]] for _ in range(10)]}
    r = lib.luck_test(const, ["lo", "hi"], 0.25, reps=5, seed=1, draws=200)
    assert np.allclose(r["best_lb_per_rep"], 0.3) and r["share_ge_real"] == 1.0 and r["verdict"] == "FAIL"
    r2 = lib.luck_test(const, ["lo", "hi"], 0.35, reps=5, seed=1, draws=200)
    assert r2["share_ge_real"] == 0.0 and r2["verdict"] == "PASS"
    assert lib.LUCK_REPS == 500 and lib.SEED2 == 20261022 and lib.MIN_DISC_TRADES == 100


def test_unseen_refuses_without_audit_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "AUDIT_OK", tmp_path / "AUDIT_OK")
    with pytest.raises(SystemExit) as e:
        run.unseen()
    assert "REFUSED" in str(e.value)
    assert not (Path(run.HERE) / "AUDIT_OK").exists()


def test_split_and_buckets():
    assert run.split_index(10) == 5 and run.split_index(11) == 5
    assert run.price_bucket(0.70) == "70-74c" and run.price_bucket(0.92) == "90-94c"
    assert run.margin_bucket(5) == "0-5" and run.margin_bucket(21) == "21+"


def test_load_pbp_wallclock_is_epoch_seconds(tmp_path):
    """Regression: the wallclock string must become real UTC epoch seconds (an unit mix-up once gave 2026-01-21 for every event)."""
    rows = []
    for k, (per, cm, cs, w) in enumerate([(1, 12, 0, "2025-06-23T00:08:47Z"), (4, 6, 0, "2025-06-23T02:30:10Z")]):
        rows.append(dict(game_id=7, game_play_number=k, sequence_number=k, period=per, clock_minutes=cm, clock_seconds=cs, wallclock=w,
                         home_team_abbrev="OKC", away_team_abbrev="IND", game_date="2025-06-22", season_type=3, home_score=0, away_score=0))
    for y in (2025, 2026):
        pd.DataFrame(rows).to_parquet(tmp_path / f"play_by_play_{y}.parquet")
    g = lib.load_pbp(tmp_path)[7]
    assert g.first_wall == 1750637327.0                      # 2025-06-23T00:08:47Z
    assert g.t[360] == 1750645810.0                          # 2025-06-23T02:30:10Z
