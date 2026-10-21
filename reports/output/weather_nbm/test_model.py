"""Unit tests for research log #30 (run: python -m pytest reports/output/weather_nbm/test_model.py)."""
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nbm_model as nm
import nbm_runsel as rs
import analyze as an


# ---------------------------------------------------------------- bracket probability / rounding
def test_bracket_prob_matches_rounding_simulation():
    rng = np.random.default_rng(1)
    mu, sg = 71.3, 2.2
    x = np.rint(rng.normal(mu, sg, 400_000))
    for a, b in [(70, 71), (72, 73), (68, 69), (74, 75)]:
        emp = np.mean((x >= a) & (x <= b))
        assert abs(emp - nm.bracket_prob(a, b, mu, sg)) < 0.004


def test_half_degree_edges():
    # a single integer degree d has width 1 centered on d
    mu, sg = 70.0, 1.0
    p = nm.bracket_prob(70, 70, mu, sg)
    assert math.isclose(p, 0.3829249, abs_tol=1e-6)      # Phi(.5)-Phi(-.5)


def test_open_ended_brackets_and_partition():
    mu, sg = 80.2, 3.0
    # T92 style: "greater than 92" = {93, ...}; T88 style "less than 88" = {..., 87}
    hi = nm.market_interval("greater", 92, None)
    lo = nm.market_interval("less", None, 88)
    assert hi == (93.0, math.inf) and lo == (-math.inf, 87.0)
    assert math.isclose(nm.bracket_prob(*hi, mu, sg), 1 - nm.bracket_prob(-math.inf, 92, mu, sg))
    # a full ladder sums to 1
    ladder = [nm.market_interval("less", None, 78), nm.market_interval("between", 78, 79),
              nm.market_interval("between", 80, 81), nm.market_interval("between", 82, 83),
              nm.market_interval("between", 84, 85), nm.market_interval("greater", 85, None)]
    assert math.isclose(sum(nm.bracket_prob(a, b, mu, sg) for a, b in ladder), 1.0, abs_tol=1e-12)
    # ladder intervals tile the integers
    for (a1, b1), (a2, b2) in zip(ladder, ladder[1:]):
        assert b1 + 1 == a2


def test_sigma_floor():
    assert nm.sigma_of(0.5, 1) == 1.0 and nm.sigma_of(0.5, 4) == 2.0 and nm.sigma_of(2.0, 0) == 1.0


# ---------------------------------------------------------------- calibration
def _synth(n, b_true=1.5, k_true=1.2, seed=0):
    rng = np.random.default_rng(seed)
    txn = rng.integers(50, 90, n)
    xnd = rng.integers(1, 5, n)
    y = np.rint(txn + b_true + rng.normal(0, 1, n) * np.maximum(1, k_true * xnd))
    return txn, xnd, y


def test_fit_recovers_parameters():
    txn, xnd, y = _synth(4000)
    b, k = nm.fit_bk(txn, xnd, y)
    assert abs(b - 1.5) < 0.15 and abs(k - 1.2) < 0.12


def _entries(n, start_day=1, settle_lag_h=10):
    out = []
    for i in range(n):
        d = (datetime(2024, 1, 1) + timedelta(days=start_day + i))
        end = d.replace(tzinfo=timezone.utc) + timedelta(days=1)
        out.append(dict(day=d.date().isoformat(), settled_ts=int((end + timedelta(hours=settle_lag_h)).timestamp()),
                        y=70 + i % 5, txn=69, xnd=2))
    return out


def test_history_excludes_today_future_and_unsettled():
    ent = _entries(100)
    days = [e["day"] for e in ent]
    D = ent[80]["day"]
    # decision 09:00Z on D: day D-1 settled 10h after its end (=10:00Z on D) -> NOT yet known
    dec = int(datetime.fromisoformat(D + "T09:00:00+00:00").timestamp())
    h = nm.select_history(days, ent, D, dec)
    assert all(e["day"] < D for e in h)
    assert ent[79]["day"] not in [e["day"] for e in h]            # settled after the decision time
    assert all(e["settled_ts"] <= dec for e in h)
    assert len(h) == 60 and h[0]["day"] == ent[78]["day"]
    # later decision includes D-1
    h2 = nm.select_history(days, ent, D, dec + 3 * 3600)
    assert h2[0]["day"] == ent[79]["day"]


def test_calibration_ignores_the_future():
    ent = _entries(120)
    days = [e["day"] for e in ent]
    D = ent[90]["day"]
    dec = int(datetime.fromisoformat(D + "T20:00:00+00:00").timestamp())
    base = nm.select_history(days, ent, D, dec)
    # corrupt every outcome on/after D (and the unsettled ones): the selected history must not change
    ent2 = [dict(e) for e in ent]
    for e in ent2[90:]:
        e["y"] = 999
    base2 = nm.select_history(days, ent2, D, dec)
    assert base == base2
    r1 = nm.calibrate(base, base)
    r2 = nm.calibrate(base2, base2)
    assert r1 == r2


def test_calibration_modes_and_shrinkage_weight():
    fake = lambda t, x, y: (float(np.mean(np.array(y) - np.array(t))), 1.0)
    pool = _entries(100)[:60]
    assert nm.calibrate(pool[:10], pool[:10], fit=fake) is None                       # pool < 15 -> no model
    c = nm.calibrate(pool[:14], pool, fit=fake)                                        # station < 15 -> pooled
    assert c["mode"] == "pooled"
    st = [dict(e, y=e["y"] + 6) for e in pool[:30]]                                    # station biased +6
    c2 = nm.calibrate(st, pool, fit=fake)
    pb = fake([e["txn"] for e in pool], [e["xnd"] for e in pool], [e["y"] for e in pool])[0]
    sb = fake([e["txn"] for e in st], [e["xnd"] for e in st], [e["y"] for e in st])[0]
    w = 30 / 60
    assert c2["mode"] == "shrunk" and math.isclose(c2["b"], w * sb + (1 - w) * pb)


# ---------------------------------------------------------------- decision-time run selection
def _utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_morning_decision_times_local_standard_time():
    assert rs.morning_decision_utc("2026-07-01", "America/New_York") == _utc("2026-07-01T14:00:00")   # EST, even in summer
    assert rs.morning_decision_utc("2026-01-01", "America/Chicago") == _utc("2026-01-01T15:00:00")
    assert rs.morning_decision_utc("2026-07-01", "America/Phoenix") == _utc("2026-07-01T16:00:00")
    assert rs.morning_decision_utc("2026-07-01", "America/Los_Angeles") == _utc("2026-07-01T17:00:00")


def test_morning_run_must_be_published_before_decision():
    assert rs.choose_morning("2026-07-01", "America/New_York", _utc("2026-07-01T06:53:00")) == "06"
    assert rs.choose_morning("2026-07-01", "America/New_York", _utc("2026-07-01T14:00:01")) is None
    assert rs.choose_morning("2026-07-01", "America/New_York", None) is None


def test_evening_decision_is_21_et_prior_evening():
    assert rs.evening_decision_utc("2026-01-15") == _utc("2026-01-15T02:00:00")       # EST: 21:00 = 02:00Z
    assert rs.evening_decision_utc("2026-07-15") == _utc("2026-07-15T01:00:00")       # EDT: 21:00 = 01:00Z


def test_evening_run_selection_uses_publish_time():
    # winter: 00Z run published 00:59Z is before 02:00Z -> use 00Z
    assert rs.choose_evening("2026-01-15", _utc("2026-01-15T00:59:00"), _utc("2026-01-14T18:55:00")) == "00"
    # summer: 00Z run published 01:04Z, decision at 01:00Z -> too late, fall back to 18Z of the prior day
    assert rs.choose_evening("2026-07-15", _utc("2026-07-15T01:04:00"), _utc("2026-07-14T18:55:00")) == "18"
    # exactly at the decision instant counts as published
    assert rs.choose_evening("2026-07-15", _utc("2026-07-15T01:00:00"), None) == "00"
    # 00Z missing -> 18Z; both missing or both late -> none
    assert rs.choose_evening("2026-07-15", None, _utc("2026-07-14T19:00:00")) == "18"
    assert rs.choose_evening("2026-07-15", None, None) is None
    assert rs.choose_evening("2026-07-15", _utc("2026-07-15T03:00:00"), _utc("2026-07-15T01:30:00")) is None


# ---------------------------------------------------------------- fee and trading rule
def test_fee_matches_formula():
    for p in range(100, 10_000, 100):
        P = p / 1e4
        want = math.ceil(round(0.07 * 10 * P * (1 - P) / 0.0001, 6)) * 0.0001 / 10      # per contract, dollars
        assert math.isclose(nm.fee_per_contract_e4(p) / 1e4, want, abs_tol=1e-9), p
    assert nm.order_fee_e4(5000) == 1750            # 0.07*10*.25 = 0.175 -> $0.1750
    assert nm.order_fee_e4(100) == 70               # 0.07*10*.01*.99 = 0.00693 -> rounds up to $0.0070
    assert int(an.order_fee_e4(100)) == 70


def test_edge_rule_and_net():
    import pandas as pd
    df = pd.DataFrame(dict(valid=[1, 1, 1], p=[0.60, 0.30, 0.50], bid_e4=[4800, 5000, 4900], ask_e4=[5000, 5200, 5100],
                           result=["yes", "no", "yes"], day=["2026-01-01"] * 3, station=["KNYC"] * 3,
                           tz=["America/New_York"] * 3, nbm_version=["5.0"] * 3))
    d = an.prep(df)
    # row0: YES edge = .60-.50-fee(.50)=.10-.0175=.0825 -> YES at theta 5c; net if yes = 1-.50-.0175 per contract
    b = an.make_bets(d, 500)
    assert list(b["side"]) == ["YES", "NO"]
    r0 = b.iloc[0]
    assert r0["net"] == 10 * 5000 - 1750
    # row1: NO price = 1-bid = .50, p(no)=.70, edge=.20-fee; result no -> wins
    r1 = b.iloc[1]
    assert r1["price"] == 5000 and r1["net"] == 10 * (10_000 - 5000) - 1750
    # row2: p .50 vs mid .50 -> no edge, no bet
    assert len(b) == 2
    # higher theta drops the weaker edge
    assert len(an.make_bets(d, 800)) == 2 and len(an.make_bets(d, 1500)) == 1


def test_at_most_one_side_and_invalid_skipped():
    import pandas as pd
    df = pd.DataFrame(dict(valid=[0, 1], p=[0.9, 0.5], bid_e4=[None, 4000], ask_e4=[None, 6000], result=["yes", "no"],
                           day=["2026-01-01"] * 2, station=["KNYC"] * 2, tz=["x"] * 2, nbm_version=["5.0"] * 2))
    d = an.prep(df)
    assert len(an.make_bets(d, 300)) == 0       # invalid quote never traded; 0.5 sits inside the 40-60 spread
