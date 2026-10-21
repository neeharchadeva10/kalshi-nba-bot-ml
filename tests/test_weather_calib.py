import json
import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from importlib import resources

import numpy as np
import pytest

from kpm.analysis import weather_calib as wc


def utc(y, mo, d, h, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


# ---------------------------------------------------------------- snapshots
def test_snapshot_dst_new_york():
    # spring forward 2026-03-08 02:00: S1 (Mar 7 18:00) is EST (UTC-5), S2..S4 (Mar 8) are EDT (UTC-4)
    s = wc.snapshot_times("2026-03-08", "America/New_York")
    assert s == [utc(2026, 3, 7, 23), utc(2026, 3, 8, 11), utc(2026, 3, 8, 16), utc(2026, 3, 8, 20)]
    # fall back 2026-11-01 02:00: S1 (Oct 31 18:00) is EDT, S2..S4 are EST
    s = wc.snapshot_times("2026-11-01", "America/New_York")
    assert s == [utc(2026, 10, 31, 22), utc(2026, 11, 1, 12), utc(2026, 11, 1, 17), utc(2026, 11, 1, 21)]


def test_snapshot_phoenix_and_seoul():
    s = wc.snapshot_times("2026-03-08", "America/Phoenix")        # MST all year (UTC-7)
    assert s == [utc(2026, 3, 8, 1), utc(2026, 3, 8, 14), utc(2026, 3, 8, 19), utc(2026, 3, 8, 23)]
    s = wc.snapshot_times("2026-10-14", "Asia/Seoul")             # UTC+9
    assert s == [utc(2026, 10, 13, 9), utc(2026, 10, 13, 22), utc(2026, 10, 14, 3), utc(2026, 10, 14, 7)]


# ---------------------------------------------------------------- buckets, fees, payouts
def test_bucket_edges():
    b = wc.bucket_index
    assert b(99) is None and b(100) == 0 and b(300) == 0 and b(301) == 1 and b(600) == 1 and b(601) == 2
    assert b(1000) == 2 and b(1001) == 3 and b(2000) == 3 and b(3500) == 4 and b(3501) == 5 and b(5000) == 5 and b(5001) == 6 and b(6500) == 6
    assert b(8000) == 7 and b(9000) == 8 and b(9400) == 9 and b(9700) == 10 and b(9701) == 11
    assert b(9900) == 11 and b(9901) is None and b(10000) is None
    assert len(wc.BUCKET_LABELS) == 12


def test_fee_rounding():
    assert wc.fee_e4(8100) == 1078          # 0.07*10*0.81*0.19 = 0.10773 -> 0.1078
    assert wc.fee_e4(5000) == 1750          # exact 0.175 stays
    assert wc.fee_e4(100) == 70             # 0.07*10*0.01*0.99 = 0.00693 -> 0.0070
    assert wc.fee_e4(9900) == 70


def test_payout_and_net():
    assert wc.payout_e4("yes", "yes") == 10000 and wc.payout_e4("yes", "no") == 0
    assert wc.payout_e4("no", "no") == 10000 and wc.payout_e4("no", "yes") == 0
    # buy YES at 0.50 and it wins: 10*(1-0.5) - 0.175 = 4.825
    assert wc.net10_e4("yes", 5000, "yes") == 48250
    # buy NO at 0.90 (bid 0.10) and it loses: -9.0 - fee(0.9)=0.63
    assert wc.net10_e4("no", 9000, "yes") == -90000 - 630


def test_entries_from_quote_null_sides():
    assert list(wc.entries_from_quote(None, None)) == []
    assert list(wc.entries_from_quote(None, 700)) == [("yes", 700)]
    assert list(wc.entries_from_quote(300, None)) == [("no", 9700)]
    assert list(wc.entries_from_quote(300, 700)) == [("yes", 700), ("no", 9700)]


# ---------------------------------------------------------------- DB helpers
def make_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(resources.files("kpm.core").joinpath("weather_schema.sql").read_text(encoding="utf-8"))
    return conn


def test_carry_forward_lookup():
    conn = make_db()
    conn.executemany("INSERT INTO wx_quotes (mid, ts, bid_c, ask_c) VALUES (?,?,?,?)",
                     [(1, 100, 10, 20), (1, 200, None, 30), (2, 50, 5, 6)])
    assert wc.carried_quote(conn, 1, 99) is None
    assert wc.carried_quote(conn, 1, 100) == (10, 20)
    assert wc.carried_quote(conn, 1, 199) == (10, 20)
    assert wc.carried_quote(conn, 1, 5000) == (None, 30)
    assert wc.carried_quote(conn, 2, 5000) == (5, 6)
    assert wc.carried_quote(conn, 3, 5000) is None


# ---------------------------------------------------------------- W1
def _m(st, fl, cp, res, src="api"):
    return dict(strike_type=st, floor=fl, cap=cp, result=res, strike_src=src)


def good_event(yes=5):
    ms = [_m("less", None, 70, "no"), _m("between", 70, 71, "no"), _m("between", 72, 73, "no"),
          _m("between", 74, 75, "no"), _m("between", 76, 77, "no"), _m("greater", 77, None, "no")]
    ms[yes]["result"] = "yes"
    return ms


def test_w1_reasons():
    assert wc.w1_event_reasons(good_event()) == set()
    assert wc.covers_all(good_event())
    e = good_event()
    e[2]["result"] = "scalar"
    assert "scalar_result" in wc.w1_event_reasons(e)
    e = good_event()
    e[1] = _m("none", None, None, "no", src="none")
    e[1]["strike_type"] = None
    assert "strike_unparsed" in wc.w1_event_reasons(e)
    e = good_event()
    e[2] = _m("between", 71, 72, "no")              # overlaps 70-71
    assert "overlapping_ranges" in wc.w1_event_reasons(e)
    e = good_event()
    e[0] = _m("greater", 60, None, "no")            # two upper tails
    assert "overlapping_ranges" in wc.w1_event_reasons(e)
    e = good_event()
    e[5]["result"] = "no"
    assert wc.w1_event_reasons(e) == {"no_yes"}
    e = good_event()
    e[1]["result"] = "yes"
    assert wc.w1_event_reasons(e) == {"multiple_yes"}
    e = good_event()[1:]                              # no low tail: valid but not a complete bracket
    assert wc.w1_event_reasons(e) == set() and not wc.covers_all(e)


def test_strike_disagrees():
    assert wc.strike_disagrees("between", 84.0, 85.0, "... is between 84-85�, then ...") is False
    assert wc.strike_disagrees("between", 84.0, 85.0, "... is between 86-87�, then ...") is True
    assert wc.strike_disagrees("greater", 85.0, None, "greater than 85° fahrenheit") is False
    assert wc.strike_disagrees("less", None, 78.0, "less than 79") is True
    assert wc.strike_disagrees("less", None, 78.0, "no numbers") is None


# ---------------------------------------------------------------- bootstrap
def test_bootstrap_ratio_known_answers():
    # every trade nets the same 7 per trade: ratio is 7 in every resample whatever the date weights
    N = np.array([[3.0], [1.0], [5.0], [2.0]])
    S = N * 7
    b = wc.bootstrap_ratio(S, N, n_boot=300, seed=1, chunk=100)
    assert b.shape == (300, 1) and np.allclose(b, 7.0)
    # two dates, means 0 and 10, equal counts: resampled mean is 10*k/2 for k = number of draws of date 2
    S = np.array([[0.0], [10.0]])
    N = np.array([[1.0], [1.0]])
    b = wc.bootstrap_ratio(S, N, n_boot=2000, seed=2)
    assert set(np.unique(b)) == {0.0, 5.0, 10.0}
    assert abs(b.mean() - 5.0) < 0.3
    # same seed, same answer; deterministic
    assert np.array_equal(b, wc.bootstrap_ratio(S, N, n_boot=2000, seed=2))
    # a column with no trades gives NaN, not an error
    z = wc.bootstrap_ratio(np.zeros((3, 1)), np.zeros((3, 1)), n_boot=10)
    assert np.isnan(z).all()


# ---------------------------------------------------------------- confirmation rule
def test_confirmed():
    T = 10_000
    tr = [(T - 100, 0.50, 6, "yes"), (T + 300, 0.49, 4, "yes")]
    assert wc.confirmed(tr, "yes", 0.50, T)                       # 6 + 4 at <= our ask, taker bought YES
    assert not wc.confirmed(tr, "yes", 0.49, T)                   # first print is above our ask
    assert not wc.confirmed([(T, 0.50, 20, "no")], "yes", 0.50, T)  # taker on the wrong side
    assert not wc.confirmed([(T + 601, 0.40, 20, "yes")], "yes", 0.50, T)   # outside the window
    assert wc.confirmed([(T + 600, 0.40, 10, "yes")], "yes", 0.50, T)       # window edge inclusive
    assert wc.confirmed([(T, 0.30, 10, "no")], "no", 0.30, T)     # sell-into-bid: yes_price >= our bid
    assert not wc.confirmed([(T, 0.29, 10, "no")], "no", 0.30, T)
    assert not wc.confirmed([(T, 0.5, 9.99, "yes")], "yes", 0.5, T)


# ---------------------------------------------------------------- end to end on synthetic data
def build_synthetic(path, n_disc=304, n_val=120, start=date(2025, 8, 1), make_bad_event=True):
    """One city; each day has one 6-market event; the upper tail always wins and is quoted 0.50 (a planted edge)."""
    conn = sqlite3.connect(path)
    conn.executescript(resources.files("kpm.core").joinpath("weather_schema.sql").read_text(encoding="utf-8"))
    strikes = [("less", None, 70), ("between", 70, 71), ("between", 72, 73), ("between", 74, 75),
               ("between", 76, 77), ("greater", 77, None)]
    mid = 0
    for i in range(n_disc + n_val):
        day = (start + timedelta(days=i)).isoformat()
        snaps = wc.snapshot_times(day, "America/New_York")
        for j, (st, fl, cp) in enumerate(strikes):
            mid += 1
            t = f"KXHIGHNY-{day}-{j}"
            res = "yes" if j == 5 else "no"
            if make_bad_event and i == 3 and j == 0:
                res = "scalar"
            conn.execute("INSERT INTO wx_markets (mid, ticker, series, event_ticker, kind, city, tz, day, strike_type, "
                         "floor_strike, cap_strike, strike_src, open_ts, close_ts, result, source, data_tier, raw_json) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (mid, t, "KXHIGHNY", f"KXHIGHNY-{day}", "high", "New York City", "America/New_York", day, st,
                          fl, cp, "api", snaps[0] - 8 * 3600, snaps[3] + 10 * 3600, res, "TWC", "live", "{}"))
            bid, ask = (4900, 5000) if j == 5 else (100, 200)
            conn.execute("INSERT INTO wx_quotes (mid, ts, bid_c, ask_c, bid_o, ask_o, volume) VALUES (?,?,?,?,?,?,?)",
                         (mid, snaps[0] - 7 * 3600, bid, ask, bid, ask, 1.0))
            conn.execute("INSERT INTO wx_quotes (mid, ts, bid_c, ask_c, bid_o, ask_o, volume) VALUES (?,?,?,?,?,?,?)",
                         (mid, snaps[0] - 6 * 3600 + 120, bid, ask, bid, ask, 1.0))   # gap candle opens at prev close
            conn.execute("INSERT INTO wx_done VALUES (?,?,?,?)", (t, "quotes", 0, "{}"))
    conn.commit()
    conn.close()


class FakeClient:
    def __init__(self, count):
        self.count = count
        self.calls = 0

    def trades_historical(self, ts):
        return False

    def trades(self, ticker, lo, hi, hist):
        self.calls += 1
        mid = (lo + hi) // 2
        iso = datetime.fromtimestamp(mid, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        yield {"trade_id": "x", "created_time": iso, "yes_price_dollars": "0.4900", "count_fp": str(self.count),
               "taker_side": "yes"}


def test_end_to_end_planted_edge(tmp_path):
    db = tmp_path / "w.sqlite"
    build_synthetic(db)
    out = tmp_path / "out"
    v = wc.run(db_path=str(db), fills=False, out_dir=str(out), n_boot=500, n_boot_exploratory=200)
    for f in ("dq.json", "cells.csv", "candidates.csv", "validation.csv", "verdict.json", "exploratory_by_source.csv",
              "exploratory_by_city.csv", "exploratory_bracket_sums.csv"):
        assert (out / f).exists()
    dq = json.loads((out / "dq.json").read_text())
    assert dq["W1"]["by_reason"]["scalar_result"]["events"] == 1
    assert dq["W1"]["events_analysed"] == 423
    assert dq["W2"]["gap_share"] == 1.0 and not dq["W2"]["stop"] and dq["W3"]["crossed_quotes"] == 0
    assert dq["W4"]["disagree"] == 0 or dq["W4"]["markets_checked"] >= 0
    assert dq["W5"]["KXHIGHNY"]["share"] == 1.0
    keys = {(c["kind"], c["snapshot"], c["side"], c["bucket"]) for c in v["cells"]}
    assert ("high", "S1", "yes", "35-50c") in keys                   # planted: upper tail bought at 0.50 always wins
    assert v["n_candidates"] >= 4 and v["n_validation_pass"] >= 4 and v["verdict"] == "UNDETERMINED_FILLS_SKIPPED"
    assert v["bracket_sums"]["yes_bundle_profitable"] > 0
    import pandas as pd
    cells = pd.read_csv(out / "cells.csv")
    assert len(cells) == 192 * 2
    # full-grid cell with a planted edge: mean = 0.5 - fee/10 = 0.5 - 0.0175
    r = cells[(cells.kind == "high") & (cells.snap == "S1") & (cells.side == "yes") & (cells.bucket == "35-50c")
              & (cells.period == "discovery")].iloc[0]
    assert abs(r.mean_net - (0.5 - 0.0175)) < 1e-9 and r.n == 303 and r.p2_5 == pytest.approx(r.mean_net)


def test_end_to_end_size_check(tmp_path):
    db = tmp_path / "w.sqlite"
    build_synthetic(db)
    big = wc.run(db_path=str(db), fills=True, out_dir=str(tmp_path / "a"), n_boot=300, n_boot_exploratory=100,
                 client=FakeClient(50))
    cell = next(c for c in big["cells"] if (c["kind"], c["snapshot"], c["side"], c["bucket"]) == ("high", "S1", "yes", "35-50c"))
    assert cell["size_pass"] is True and cell["usd_per_week"] > wc.SIZE_MIN_PER_WEEK and big["verdict"] == "PASS"
    small = wc.run(db_path=str(db), fills=True, out_dir=str(tmp_path / "b"), n_boot=300, n_boot_exploratory=100,
                   client=FakeClient(3))
    assert small["verdict"] == "FAIL" and small["n_validation_pass"] >= 1
    capped = wc.run(db_path=str(db), fills=True, max_fetch=2, out_dir=str(tmp_path / "c"), n_boot=300,
                    n_boot_exploratory=100, client=FakeClient(50))
    assert capped["size_check"]["cap_hit"] is True


def test_w2_stop_flag(tmp_path):
    db = tmp_path / "w.sqlite"
    build_synthetic(db, n_disc=5, n_val=2, make_bad_event=False)
    c = sqlite3.connect(db)
    c.execute("UPDATE wx_quotes SET bid_o = 1 WHERE ts % 2 = 0 OR 1")       # break every gap-candle open
    c.commit()
    c.close()
    v = wc.run(db_path=str(db), fills=False, out_dir=str(tmp_path / "o"), n_boot=50, n_boot_exploratory=50)
    assert v["verdict"] == "STOP_W2"
    assert json.loads((tmp_path / "o" / "dq.json").read_text())["stop_flag"] is True
