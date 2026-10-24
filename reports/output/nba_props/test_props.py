"""Unit tests for research log #36 (nba_props).  Run: .venv/Scripts/python.exe -m pytest reports/output/nba_props/test_props.py"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analysis as A  # noqa: E402
import collect as C  # noqa: E402
import data as D  # noqa: E402
import features as F  # noqa: E402
import nbmodel as NB  # noqa: E402


# ------------------------------------------------------------------ fees
def test_fee_rounding_up_and_exact_cases():
    assert NB.order_fee(0.5, 10) == pytest.approx(0.175)            # 0.07*10*0.25 exactly, no spurious bump
    assert NB.fee_per_contract(0.5, 10) == pytest.approx(0.0175)
    assert NB.order_fee(0.5, 1) == pytest.approx(0.0175)
    raw = 0.07 * 10 * 0.1234 * (1 - 0.1234)
    assert NB.order_fee(0.1234, 10) == pytest.approx(math.ceil(raw * 1e6) / 1e6)
    assert NB.order_fee(0.1234, 10) >= raw                            # always rounded UP
    assert NB.order_fee(0.0, 10) == 0 and NB.order_fee(1.0, 10) == 0
    with pytest.raises(ValueError):
        NB.order_fee(1.2)


def test_fee_symmetric_in_price():
    assert NB.order_fee(0.2, 10) == NB.order_fee(0.8, 10)


# ------------------------------------------------------------------ NB probability
def test_gammaln_matches_math():
    xs = np.array([0.05, 0.5, 1, 2.5, 7, 11.9, 12, 30.3, 100, 1e4])
    assert np.allclose(NB.gammaln(xs), [math.lgamma(x) for x in xs], atol=1e-9)


def _pmf(k, mu, alpha):
    r = 1 / alpha
    return math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(r / (r + mu)) + k * math.log(mu / (r + mu)))


@pytest.mark.parametrize("mu,alpha,n", [(5.0, 0.3, 8), (20.0, 0.05, 25), (0.7, 1.0, 1), (12.0, 0.2, 0), (3.0, 0.5, 30)])
def test_nb_sf_matches_direct_sum(mu, alpha, n):
    direct = 1 - sum(_pmf(k, mu, alpha) for k in range(n))
    assert float(NB.nb_sf(mu, alpha, n)) == pytest.approx(max(direct, 0.0), abs=1e-9)


def test_nb_sf_properties():
    assert float(NB.nb_sf(7.0, 0.3, 0)) == 1.0                      # P(X>=0)
    s = NB.nb_sf(np.full(10, 9.0), 0.2, np.arange(0, 10))
    assert np.all(np.diff(s) <= 1e-12)                                # decreasing in N
    assert NB.nb_sf(15.0, 0.2, 12) > NB.nb_sf(10.0, 0.2, 12)         # increasing in mu
    # alpha -> 0 is Poisson
    pois = 1 - sum(math.exp(-6) * 6 ** k / math.factorial(k) for k in range(5))
    assert float(NB.nb_sf(6.0, 1e-6, 5)) == pytest.approx(pois, abs=1e-4)


def test_fit_nb_recovers_parameters():
    rng = np.random.default_rng(1)
    n = 6000
    x = rng.normal(size=(n, 2))
    mu = np.exp(1.2 + 0.5 * x[:, 0] - 0.3 * x[:, 1])
    alpha = 0.25
    r = 1 / alpha
    y = rng.poisson(rng.gamma(r, mu / r))
    m = NB.fit_nb(x, y)
    assert m["alpha"] == pytest.approx(alpha, rel=0.15)
    pm = NB.predict_mu(m, x)
    assert np.corrcoef(np.log(pm), np.log(mu))[0, 1] > 0.99
    assert abs(np.mean(pm) - np.mean(y)) < 0.1


# ------------------------------------------------------------------ names / matching
def test_norm_names():
    assert D.norm_name("Nikola Jokić") == "nikolajokic"
    assert D.norm_name("Jaren Jackson Jr.") == "jarenjackson"
    assert D.norm_name("Gary Trent Jr.") == "garytrent"
    assert D.norm_name("P.J. Washington") == "pjwashington"
    assert D.norm_name("Alperen Şengün") == "alperensengun"
    assert D.norm_name("Ron Holland II") == "ronholland"
    assert D.norm_name("Jabari Smith Jr.") == D.norm_injury_name("SmithJr.,Jabari")
    assert D.norm_name("Scotty Pippen Jr.") == D.norm_injury_name("PippenJr.,Scotty")
    assert D.norm_name("Olivier-Maxence Prosper") == D.norm_injury_name("Prosper,Olivier-Maxence")
    assert D.norm_name("Luka Doncic") == D.norm_injury_name("Doncic,Luka")


def test_parse_prop_ticker_and_event():
    assert C.parse_prop_ticker("KXNBAPTS-26JUN13NYKSAS-SASVWEMBANYAMA1-40", "NYKSAS") == ("SAS", "VWEMBANYAMA1")
    assert C.parse_prop_ticker("KXNBAREB-26JUN13NYKSAS-NYKKTOWNS32-10", "NYKSAS") == ("NYK", "KTOWNS32")
    assert C.parse_prop_ticker("KXNBAPTS-26JUN05NYKSAS-NYJKTOWNS32-30", "NYKSAS") is None      # the one real parse failure
    assert C.parse_prop_ticker("KXNBAPTS-26JUN13NYKSAS-NYK-40", "NYKSAS") is None              # nothing after the team code
    d = C.parse_event("KXNBA3PT-26JUN13NYKSAS")
    assert d[0] == "KXNBA3PT" and d[1].isoformat() == "2026-06-13" and d[2] == "NYKSAS"
    assert C.parse_event("garbage") is None


def _write_box(tmp_path, rows):
    cols = ["game_id", "game_date", "game_date_time", "athlete_id", "athlete_display_name", "team_abbreviation",
            "opponent_team_abbreviation", "minutes", "points", "rebounds", "assists", "three_point_field_goals_made",
            "did_not_play", "season_type"]
    df = pd.DataFrame(rows, columns=cols)
    df.to_parquet(tmp_path / "player_box_2026.parquet")
    return tmp_path


def _synth(tmp_path, n_days=40, future_points=None):
    """LAL (aid 1 star, aid 2 role player, aid 3 'Same Name' x2) vs BOS (aid 9).  One game per day, tip 00:00Z."""
    rows = []
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for d in range(n_days):
        t = base + pd.Timedelta(days=d)
        ts = pd.Timestamp(t).tz_convert("America/New_York")
        gid = 1000 + d
        pts1 = 10.0 + d if not (future_points and d == n_days - 1) else future_points
        for aid, nm, tm, opp, mn, pts in ((1, "Star Player", "LAL", "BOS", 30.0, pts1), (2, "Role Guy", "LAL", "BOS", 15.0, 4.0),
                                         (3, "Same Name", "LAL", "BOS", 8.0, 2.0), (4, "Same Name", "LAL", "BOS", 8.0, 2.0),
                                         (9, "Boston Guy", "BOS", "LAL", 30.0, 20.0), (8, "Allowed Guy", "BOS", "LAL", 10.0, 6.0)):
            rows.append([str(gid), t.date() if False else ts.date(), ts, aid, nm, tm, opp, mn, pts, 5.0, 3.0, 1.0, False, 2])
    return D.Box(_write_box(tmp_path, rows), seasons=[2026])


def test_match_player_edge_cases(tmp_path):
    box = _synth(tmp_path, 5)
    assert box.match_player("Star Player", "LAL") == 1
    assert box.match_player("Star Player", "BOS") is None             # wrong team
    assert box.match_player("Same Name", "LAL") is None               # two athletes -> ambiguous -> unmatched
    assert box.match_player("Nobody Here", "LAL") is None
    assert box.match_player("star player", "LAL") == 1


def test_prior_games_no_lookahead(tmp_path):
    box = _synth(tmp_path, 40)
    h = box.hist[1]
    tip_k = h[10][0]
    # decision exactly when game 10 is deemed over (tip + 4h) includes it; one second earlier excludes it
    assert len(box.prior_games(1, tip_k + 4 * 3600)) == 11
    assert len(box.prior_games(1, tip_k + 4 * 3600 - 1)) == 10
    assert len(box.prior_games(1, tip_k - 3600)) == 10                 # a game starting in an hour is not history
    assert box.prior_games(1, h[0][0]) == []


def test_features_unchanged_by_future_data(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = _synth(tmp_path / "a", 40)
    b = _synth(tmp_path / "b", 40, future_points=500.0)             # only the LAST game's points differ
    dec = a.hist[1][-1][0] - 3600                                     # decision for the last game
    for stat in ("PTS",):
        va, ia = F.build_vector(a, 1, "LAL", "BOS", True, stat, "2026-02-09", dec, None, "Star Player")
        vb, ib = F.build_vector(b, 1, "LAL", "BOS", True, stat, "2026-02-09", dec, None, "Star Player")
        assert np.array_equal(va, vb) and ia["rate"] == ib["rate"]
    # ...and it IS used once the game is over (sanity that the data really differ)
    later = a.hist[1][-1][0] + 5 * 3600
    assert F.rate_features(a.prior_games(1, later), "PTS", 2026)[0] != F.rate_features(b.prior_games(1, later), "PTS", 2026)[0]


def test_rate_minutes_shrink(tmp_path):
    box = _synth(tmp_path, 40)
    dec = box.hist[1][30][0] + 4 * 3600 + 10          # 31 games known (idx 0..30)
    prior = box.prior_games(1, dec)
    assert len(prior) == 31
    rate, pmin, n = F.rate_features(prior, "PTS", 2026)
    last20 = prior[-20:]
    r20 = sum(g[5] for g in last20) / sum(g[4] for g in last20)
    rs = sum(g[5] for g in prior) / sum(g[4] for g in prior)
    w = 20 / 30
    assert n == 20 and rate == pytest.approx(w * r20 + (1 - w) * rs)
    assert pmin == pytest.approx(30.0)                                   # mean minutes of the last 10
    assert F.rate_features([], "PTS", 2026) is None


def test_rate_no_current_season_games_uses_window_rate():
    prior = [(i * 1e5, "2025-01-01", 2025, "LAL", 30.0, 12.0, 5.0, 3.0, 1.0, f"g{i}") for i in range(5)]
    rate, pmin, n = F.rate_features(prior, "PTS", 2026)
    assert rate == pytest.approx(12.0 / 30.0) and pmin == 30.0 and n == 5


def _report(rows, covered=True):
    return {"file": "x.pdf", "created": 0.0, "covered": covered, "rows": rows, "nys": []}


def test_teammates_out_rotation_and_status(tmp_path):
    box = _synth(tmp_path, 40)
    dec = box.hist[1][-1][0] - 3600
    rep = _report([
        {"date": "02/09/2026", "time": "", "matchup": "BOS@LAL", "team": "LosAngelesLakers", "player": "Guy,Role", "status": "Out"},
        {"date": "02/09/2026", "time": "", "matchup": "BOS@LAL", "team": "LosAngelesLakers", "player": "Player,Star", "status": "Questionable"},
        {"date": "02/09/2026", "time": "", "matchup": "BOS@LAL", "team": "BostonCeltics", "player": "Guy,Boston", "status": "Out"}])
    tot, n_listed, n_unm = F.teammates_out(box, rep, "LAL", 3, "PTS", dec, 2026)       # asking for aid 3: Role Guy (15 mpg) is out
    assert tot == pytest.approx(4.0) and n_listed == 1
    tot_self, _, _ = F.teammates_out(box, rep, "LAL", 2, "PTS", dec, 2026)             # Role Guy himself is excluded from his own feature
    assert tot_self == 0.0
    assert F.report_status(rep, "LAL", "Star Player") == "Questionable"
    assert F.report_status(rep, "LAL", "Role Guy") == "Out"
    assert F.report_status(rep, "BOS", "Star Player") is None                           # status is team specific
    assert F.report_status(_report([], covered=False), "LAL", "Star Player") is None
    # low-minute teammate listed Out does not count (Allowed Guy: BOS 10 mpg is rotation; make LAL 8-mpg 'Same Name' listed -> ambiguous)
    rep2 = _report([{"date": "d", "time": "", "matchup": "BOS@LAL", "team": "LosAngelesLakers", "player": "Name,Same", "status": "Out"}])
    assert F.teammates_out(box, rep2, "LAL", 1, "PTS", dec, 2026) == (0.0, 0, 1)         # ambiguous name -> counted unmatched


def test_opp_rel_rest(tmp_path):
    box = _synth(tmp_path, 40)
    dec = box.hist[1][-1][0] - 3600
    orel = F.opp_rel(box, "BOS", "PTS", 2026, dec)
    # BOS allows LAL's points; LAL allows BOS's (20+6=26 per game); league average = mean of team totals (LAL's and BOS's)
    assert orel > 0
    rest, b2b = F.rest_features(box, "LAL", "2026-02-08", dec)
    assert (rest, b2b) == (1.0, 1.0)                                        # played the day before
    rest, b2b = F.rest_features(box, "LAL", "2026-02-10", dec)
    assert (rest, b2b) == (3.0, 0.0)
    rest, b2b = F.rest_features(box, "LAL", "2026-03-30", dec)
    assert rest == F.REST_CAP


# ------------------------------------------------------------------ injury report selection
def test_report_selection_by_timestamp(tmp_path):
    pc = tmp_path / "probe.json"
    pc.write_text(json.dumps({"a.pdf": 1000.0, "b.pdf": 2000.0, "c.pdf": 3000.0, "none.pdf": None, "bad.pdf": -1.0}))
    pj = tmp_path / "parsed.json"
    pj.write_text(json.dumps({n: {"header": "", "rows": [{"date": "01/05/2026", "time": "", "matchup": "BOS@LAL", "team": "LosAngelesLakers",
                                                         "player": n, "status": "Out"}], "nys": []} for n in ("a.pdf", "b.pdf", "c.pdf")}))
    st = D.ReportStore(pc, pj, parse_missing=False)
    assert st.select(999.0) == (None, None)
    assert st.select(1000.0) == ("a.pdf", 1000.0)                          # published exactly at the decision time counts
    assert st.select(2999.9) == ("b.pdf", 2000.0)                          # c is published after the decision -> not used
    assert st.select(1e9) == ("c.pdf", 3000.0)
    r = st.report_for_game(2500.0, "01/05/2026", "BOS", "LAL")
    assert r["file"] == "b.pdf" and r["covered"] and r["rows"][0]["player"] == "b.pdf"
    r2 = st.report_for_game(2500.0, "01/06/2026", "BOS", "LAL")              # other date: not covered
    assert not r2["covered"]
    assert st.report_for_game(500.0, "01/05/2026", "BOS", "LAL") is None


# ------------------------------------------------------------------ quotes
def test_decision_candle_and_validity():
    tip = 100000
    cs = [(tip - 3600 - 7200, 0.30, 0.40, 0.0), (tip - 3600, 0.31, 0.41, 1.0), (tip - 3599, 0.99, 0.999, 1.0), (tip, 0.5, 0.6, 1.0)]
    assert D.decision_candle(cs, tip)[0] == tip - 3600                   # ends exactly at T-60 min: allowed; later ones are not
    assert D.decision_candle(cs[:1], tip)[0] == tip - 3600 - 7200        # stale candle is still the last known book
    assert D.decision_candle([cs[2], cs[3]], tip) is None
    assert D.valid_quote(0.3, 0.4) and not D.valid_quote(0.0, 0.4) and not D.valid_quote(0.3, 1.0)
    assert not D.valid_quote(0.5, 0.4) and not D.valid_quote(None, 0.4)


def test_split_games():
    g = [(i, f"g{i}") for i in range(11)]
    d, u = D.split_games(g)
    assert len(d) == 6 and len(u) == 5 and max(int(k[1:]) for k in d) < min(int(k[1:]) for k in u)


# ------------------------------------------------------------------ edge rule / price filter
def test_decide_yes_no_and_filter():
    fee = NB.fee_per_contract
    # YES: p=0.60, ask=0.50 -> pays 0.51 (1c slippage), fee at 0.51
    d = A.decide(0.60, 0.48, 0.50, 0.03)
    assert d["side"] == "yes" and d["price"] == pytest.approx(0.51) and d["edge"] == pytest.approx(0.60 - 0.51 - fee(0.51))
    # NO: p=0.30, bid=0.50 -> pays 0.50 + 0.01
    d = A.decide(0.30, 0.50, 0.52, 0.03)
    assert d["side"] == "no" and d["price"] == pytest.approx(0.51) and d["edge"] == pytest.approx(0.70 - 0.51 - fee(0.51))
    assert A.decide(0.51, 0.49, 0.51, 0.03) is None                       # below theta after slippage and fee
    # theta is inclusive
    e = 0.60 - 0.51 - fee(0.51)
    assert A.decide(0.60, 0.48, 0.50, e - 1e-9) is not None and A.decide(0.60, 0.48, 0.50, e + 1e-6) is None
    # price filter is on the PRICE PAID (incl. slippage), 15-85c inclusive
    assert A.decide(0.60, 0.08, 0.10, 0.03, price_filter=False)["price"] == pytest.approx(0.11)


def test_decide_price_filter_cases():
    # YES at ask 0.10 (pays 0.11, below 15c) is filtered out in the primary version, kept in "all prices"
    assert A.decide(0.30, 0.08, 0.10, 0.03) is None
    assert A.decide(0.30, 0.08, 0.10, 0.03, price_filter=False)["side"] == "yes"
    # NO paying 1-bid+1c = 0.91 (> 85c) filtered; paying exactly 0.85 (bid 0.16) allowed
    assert A.decide(0.02, 0.10, 0.12, 0.03) is None
    assert A.decide(0.02, 0.16, 0.18, 0.001)["side"] == "no" and A.decide(0.02, 0.16, 0.18, 0.001)["price"] == pytest.approx(0.85)
    # ask 0.14 -> pays exactly 0.15 passes; ask 0.13 -> pays 0.14 fails
    assert A.decide(0.40, 0.12, 0.14, 0.03)["price"] == pytest.approx(0.15)
    assert A.decide(0.40, 0.11, 0.13, 0.03) is None
    # price paid never exceeds 99c (no ValueError in the fee)
    assert A.decide(0.999, 0.98, 0.995, 0.0, price_filter=False)["price"] == pytest.approx(0.99)


def test_fee_rounded_up_per_order_at_count_10():
    # 0.07 * 10 * 0.51 * 0.49 = 0.17493 -> per-order total rounded UP to $0.000001, then / 10 per contract
    assert NB.order_fee(0.51, 10) == pytest.approx(0.17493)
    d = A.decide(0.60, 0.48, 0.50, 0.03)
    assert d["fee"] == pytest.approx(0.017493, abs=1e-9)
    assert NB.order_fee(0.1234, 10) >= 0.07 * 10 * 0.1234 * (1 - 0.1234)      # never rounds down


def test_net_per_contract():
    assert A.net_per_contract("yes", 0.4, 0.02, "yes") == pytest.approx(0.58)
    assert A.net_per_contract("yes", 0.4, 0.02, "no") == pytest.approx(-0.42)
    assert A.net_per_contract("no", 0.7, 0.02, "no") == pytest.approx(0.28)
    assert A.net_per_contract("no", 0.7, 0.02, "yes") == pytest.approx(-0.72)


# ------------------------------------------------------------------ gates / stats on synthetic data
def test_residual_test_detects_information():
    rng = np.random.default_rng(3)
    n = 4000
    truth = rng.uniform(0.1, 0.9, n)
    y = (rng.random(n) < truth).astype(float)
    mid = np.clip(truth + rng.normal(0, 0.12, n), 0.02, 0.98)                 # noisy market
    model = np.clip(truth + rng.normal(0, 0.04, n), 0.02, 0.98)               # better model
    games = rng.integers(0, 300, n)
    r = A.residual_test(model, mid, y, games, draws=200)
    assert r["disagreement"] > 0 and r["ci_lo"] > 0
    junk = np.clip(rng.uniform(0.1, 0.9, n), 0.02, 0.98)                      # uninformative model
    r2 = A.residual_test(junk, mid, y, games, draws=200)
    assert r2["ci_lo"] <= 0 or r2["disagreement"] <= 0.2


def test_gate3_counts_weeks():
    wk = 7 * 86400
    base = int(datetime(2026, 1, 5, tzinfo=timezone.utc).timestamp())
    tips = [base + i * 3600 for i in range(30)] + [base + wk + i * 3600 for i in range(10)]
    p = np.array([0.7] * 40)
    bid = np.array([0.38] * 40)
    ask = np.array([0.42] * 40)               # mid .40, gap .30 >> half spread .02 + fee
    g = A.gate3(p, bid, ask, tips, [base, base + wk])
    assert g["events"] == 40 and g["weeks"] == 2 and g["per_week"] == 20.0 and g["min_week"] == 10
    g2 = A.gate3(np.full(40, 0.405), bid, ask, tips, [base, base + wk])     # gap .005 < cost
    assert g2["events"] == 0


def test_brier_logloss_calib():
    assert A.brier([0.5, 0.5], [1, 0]) == 0.25
    assert A.logloss([0.5, 0.5], [1, 0]) == pytest.approx(math.log(2))
    t = A.calib_table([0.1, 0.1, 0.9], [0, 1, 1])
    assert sum(r["n"] for r in t) == 3


def test_bootstrap_and_coin_flip():
    pnl = np.random.default_rng(5).choice([0.5, -0.5], 200)
    games = np.repeat(np.arange(100), 2)
    lo, hi = A.bootstrap_mean(pnl, games, draws=500)
    assert lo < 0 < hi
    pos = np.full(200, 0.3)
    lo, hi = A.bootstrap_mean(pos, games, draws=500)
    assert lo == pytest.approx(0.3)
    # a strategy that always picks the winner at mid prices beats ~all coin flips; a random strategy beats ~half
    n = 400
    rng = np.random.default_rng(0)
    result = np.where(rng.random(n) < 0.5, "yes", "no")
    bid, ask = np.full(n, 0.48), np.full(n, 0.52)
    best = np.mean([A.net_per_contract("yes" if r == "yes" else "no", 0.52 if r == "yes" else 0.52, 0.0175, r) for r in result])
    assert A.coin_flip(bid, ask, result, best)["share"] == 0.0
    mean_rand = A.coin_flip(bid, ask, result, 0)["copy_mean"]
    assert 0.2 < A.coin_flip(bid, ask, result, mean_rand)["share"] < 0.8
    # price filter in the coin flip: sides priced outside 15-85c are not placed (all of these are ~2-4c / 96-98c)
    far = A.coin_flip(np.full(n, 0.02), np.full(n, 0.04), result, 0.0)
    assert far["copy_mean"] == -np.inf or far["share"] == 0.0 or far["copy_mean"] < 0
    assert np.isfinite(A.coin_flip(np.full(n, 0.02), np.full(n, 0.04), result, 0.0, price_filter=False)["copy_mean"])


def test_worst_streak():
    assert A.worst_streak([True, False, False, False, True, False]) == 3
