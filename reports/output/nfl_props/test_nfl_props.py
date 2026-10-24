import sys, subprocess
from pathlib import Path
import numpy as np, pandas as pd, pytest
H = Path(__file__).parent
sys.path.insert(0, str(H))
import nfl_lib as L, shocks, feat, lib


# ---------------- fees / entry
def test_fee_known_values():
    assert L.fee_e4(0.50) == 175
    assert L.fee(0.50) == 0.0175
    assert L.fee_e4(0.10) == 63
    assert L.fee_e4(0.37) == 164          # 0.016317 rounds UP
    assert L.fee_e4(0.99) == 7


def test_entry_and_net():
    assert L.entry_price(0.37) == 0.38
    assert L.net_per_contract(0.38, True) == pytest.approx(1 - 0.38 - L.fee(0.38))
    assert L.net_per_contract(0.38, False) == pytest.approx(-0.38 - L.fee(0.38))


# ---------------- quotes / look-ahead
def _c(end, bid, ask, dollars=False):
    k = "close_dollars" if dollars else "close"
    return dict(end_period_ts=end, yes_bid={k: str(bid)}, yes_ask={k: str(ask)})


def test_quote_never_uses_candle_after_decision():
    cs = [_c(1000, .30, .35), _c(4600, .40, .45), _c(8200, .90, .95)]   # last candle ends AFTER D
    q = L.quote_at(cs, 5000)
    assert q == (0.40, 0.45, 4600)


def test_quote_stale_and_formats():
    assert L.quote_at([_c(1000, .3, .4)], 1000 + L.MAX_QUOTE_AGE_S + 1) is None
    assert L.quote_at([_c(1000, .3, .4, dollars=True)], 1000) == (0.3, 0.4, 1000)
    assert L.quote_at([dict(end_period_ts=10, yes_bid={"close": None}, yes_ask={"close": "0.4"})], 10) is None


def test_usable_and_main_strike():
    assert L.usable(0.45, 0.50) and not L.usable(0.45, 0.60)       # spread 0.15
    assert not L.usable(0.05, 0.12)                                # mid 0.085
    assert not L.usable(0.0, 0.5) and not L.usable(0.9, 1.0)
    assert L.pick_main({40: (0.60, 0.65), 50: (0.40, 0.45), 60: None}) == 50   # mid .425 vs .625
    assert L.pick_main({40: (0.02, 0.05)}) is None


def test_scope_strikes():
    assert L.scope_strikes([20, 30, 40, 50, 60], 44.0) == [40, 50]
    assert L.scope_strikes([20, 30, 40], 35.0) == [30, 40]          # tie -> lower first


# ---------------- shocks, with a look-ahead guard on a toy league
def _toy():
    rows = []
    # team X, 6 games (gkey 202601..202606); RB group carries: P1 20/g, P2 5/g, P3 0; in game 5-6 P3 jumps
    for k in range(1, 7):
        for pid, c in (("P1", 20), ("P2", 5), ("P3", 0 if k < 5 else 10)):
            rows.append(dict(player_id=pid, team="X", season=2026, gkey=202600 + k, use=c))
    return pd.DataFrame(rows)


def _tg():
    return {("X", 2026): [202601 + i for i in range(6)]}


def test_shock_a_picks_next_highest_when_top_out():
    g = _toy(); inj = {("X", 202607): {"out": {"P1"}, "doubtful": set()}}
    tg = {("X", 2026): [202601 + i for i in range(7)]}
    g = pd.concat([g, pd.DataFrame([dict(player_id="P1", team="X", season=2026, gkey=202607, use=0)])])
    r = shocks.detect(g, inj, tg, "X", 2026, 202607)
    assert r["A"] == "P3" or r["A"] == "P2"
    # last-4 shares: P1 80/ (80+20+20)=0.667 >= .5 and Out; P3 has 20 vs P2 20 in last 4 -> tie broken stably, but not P1
    assert r["A"] != "P1"


def test_shock_a_needs_50pct_and_out():
    g = _toy()
    tg = {("X", 2026): [202601 + i for i in range(7)]}
    assert shocks.detect(g, {}, tg, "X", 2026, 202607)["A"] is None
    inj = {("X", 202607): {"out": {"P2"}, "doubtful": set()}}       # P2 share < 50%
    assert shocks.detect(g, inj, tg, "X", 2026, 202607)["A"] is None


def test_shock_b_threshold():
    g = _toy()
    tg = {("X", 2026): [202601 + i for i in range(7)]}
    r = shocks.detect(g, {}, tg, "X", 2026, 202607)
    # P3: last-2 share 20/(40+10+20)... compute: last2 carries P1 40,P2 10,P3 20 -> 20/70=.2857; season: P3 20/ (120+30+20)=.1176 -> jump .168
    assert "P3" in r["B10"] and "P3" not in r["B20"]


def test_no_lookahead_future_games_do_not_change_detection():
    g = _toy(); tg6 = _tg()
    base = shocks.detect(g, {}, tg6, "X", 2026, 202606)
    fut = pd.concat([g, pd.DataFrame([dict(player_id="P2", team="X", season=2026, gkey=202606 + k, use=999) for k in (1, 2)])])
    tg8 = {("X", 2026): tg6[("X", 2026)] + [202607, 202608]}
    assert shocks.detect(fut, {}, tg8, "X", 2026, 202606) == base
    # and even rows AT the decision game itself are excluded
    at = pd.concat([g, pd.DataFrame([dict(player_id="P2", team="X", season=2026, gkey=202606, use=500)])])
    assert shocks.detect(at, {}, tg6, "X", 2026, 202606) == base


def test_min_prior_games():
    g = _toy()
    tg = {("X", 2026): [202601, 202602, 202603]}
    assert shocks.detect(g, {}, tg, "X", 2026, 202604) == {"A": None, "B10": set(), "B20": set()}


# ---------------- parsing
def test_parse_event_and_alias():
    teams = {"WAS", "SF", "JAX", "LA", "DEN", "KC", "LAC"}
    assert feat.parse_event("KXNFLRSHYDS-26OCT19WASSF", teams) == ("2026-10-19", "WAS", "SF")
    assert feat.parse_event("KXNFLREC-25NOV02JACDEN", teams) == ("2025-11-02", "JAX", "DEN")
    assert feat.parse_event("KXNFLREC-25NOV02LACLAC", teams) is not None
    assert feat.norm_name("A.J. Brown Jr.") == "ajbrown"


# ---------------- stats / selection / luck test
def test_bootstrap_lb_by_game_and_selection():
    s, n = L.by_game(["g1", "g1", "g2"], [1.0, 1.0, -1.0])
    assert s == [2.0, -1.0] and n == [2, 1]
    st = {"a": dict(n=50, lb=-0.1), "b": dict(n=39, lb=0.5), "c": dict(n=60, lb=0.0)}
    assert L.select_version(st)[0] == "c"
    assert L.select_version({"a": dict(n=3, lb=1.0)})[0] is None
    assert L.boot_lb([5.0] * 30, [1] * 30, draws=500) == pytest.approx(5.0)


def test_nine_versions():
    assert len(L.VERSIONS) == 9


def test_luck_test_pure_noise_does_not_fail_often():
    rng = np.random.default_rng(1)
    pool = [dict(paid=0.5, net=float(rng.choice([0.5 - 0.02, -0.5 - 0.02])), game=f"g{i}", player=f"q{i}") for i in range(400)]
    real = {"v": [dict(paid=0.5, week=1, stat="rush", game=f"r{i}", player=f"p{i}") for i in range(60)]}
    lt = L.luck_test(real, {("rush", 1): pool}, real_best_lb=0.6, eligible=["v"], reps=40, seed=3, draws=200)
    assert lt["share_ge_real"] == 0.0 and lt["verdict"] == "PASS"
    lt2 = L.luck_test(real, {("rush", 1): pool}, real_best_lb=-5.0, eligible=["v"], reps=40, seed=3, draws=200)
    assert lt2["verdict"] == "FAIL"


def test_draw_matched_prefers_price_and_excludes_none_left():
    rng = np.random.default_rng(0)
    pool = [dict(paid=0.30, net=1, game="a", player="a"), dict(paid=0.50, net=2, game="b", player="b")]
    assert L.draw_matched(rng, pool, 0.49)["net"] == 2
    assert L.draw_matched(rng, pool, 0.90)["net"] == 2          # nearest when nothing within tol


# ---------------- pacer / http guards
def test_pacer_aimd():
    t = [0.0]
    p = lib.Pacer(clock=lambda: t[0])
    assert p.rate == 0.3
    t[0] = 61; p.maybe_increase(); assert p.rate == pytest.approx(0.4)
    p.on_429(); assert p.rate == pytest.approx(0.2)
    p.on_429(); assert p.rate == 0.2                     # floor
    for _ in range(20):
        t[0] += 61; p.maybe_increase()
    assert p.rate == 1.0                                 # ceiling


def test_client_refuses_account_paths():
    c = lib.KClient()
    with pytest.raises(AssertionError):
        c.get("/portfolio/orders")


def test_nflverse_host_allowlist():
    with pytest.raises(AssertionError):
        lib.download_nflverse("https://example.com/x.csv", "x.csv")


def test_run_unseen_refuses_without_audit_ok():
    if (H / "AUDIT_OK").exists():
        pytest.skip("AUDIT_OK present")
    r = subprocess.run([sys.executable, str(H / "run_unseen.py")], capture_output=True, text=True)
    assert r.returncode != 0 and "REFUSED" in (r.stderr + r.stdout)
