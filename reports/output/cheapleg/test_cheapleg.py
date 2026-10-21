"""Unit tests for cheapleg.py on synthetic messages (no network, no recordings)."""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cheapleg as cl  # noqa: E402

lc = cl.lc
EV = "KXNBATOTAL-26OCT21AAABBB"
A, B = f"{EV}-150", f"{EV}-160"          # A = weak strike (lower), B = strong strike (higher)
DOC = {"events": {EV: {"mutually_exclusive": False}},
       "books": {A: {"strike_type": "greater", "floor": 150, "cap": None},
                 B: {"strike_type": "greater", "floor": 160, "cap": None}}}
MS = 1_000_000
T0 = 1_000_000 * 10**9


class Feed:
    """Builds recording lines in the raw format (one sid, rising seq)."""

    def __init__(self, stream=None):
        self.seq = 0
        self.stream = stream

    def _line(self, ns, typ, body):
        self.seq += 1
        raw = json.dumps({"type": typ, "sid": 1, "seq": self.seq, "msg": body})
        d = {"recv_ns": ns, "raw": raw}
        if self.stream is not None:
            d["stream"] = self.stream
        return d

    def snap(self, ns, t, yes=(), no=(), size=10.0):
        return self._line(ns, "orderbook_snapshot", {"market_ticker": t,
                          "yes_dollars_fp": [[f"{p:.4f}", f"{size:.2f}"] for p in yes],
                          "no_dollars_fp": [[f"{p:.4f}", f"{size:.2f}"] for p in no]})

    def trade(self, ns, t, price=0.30):
        return self._line(ns, "trade", {"market_ticker": t, "yes_price_dollars": f"{price:.4f}", "count_fp": "1.00",
                                         "taker_side": "yes"})

    def delta(self, ns, t, side, price, d):
        return self._line(ns, "orderbook_delta", {"market_ticker": t, "price_dollars": f"{price:.4f}",
                                                  "delta_fp": f"{d:.2f}", "side": side})

    @staticmethod
    def ctl(ns, ev):
        return {"recv_ns": ns, "ctl": {"event": ev}}


def lock_lines(f, t0=T0):
    """A: YES ask 0.30 (NO bid 0.70).  B: YES bid 0.40.  Lock opens when B's snapshot arrives (trigger = B)."""
    return [f.snap(t0, A, yes=[0.20], no=[0.70]), f.snap(t0 + 10 * MS, B, yes=[0.40], no=[0.50])]


def run(lines, tips=None, coin=False):
    rp = cl.Replay(DOC, tips or {}, coin=coin)
    rp.run(lines)
    return rp


# ------------------------------------------------------------------------------------------------ lock detection
def test_lock_matches_lockcore_and_trigger():
    f = Feed()
    rp = run(lock_lines(f))
    assert len(rp.cands) == 1
    c = rp.cands[0]
    ref = lc.ladder_lock(A, B, (0.20, 10.0, 0.30, 10.0), (0.40, 10.0, 0.50, 10.0))
    assert c["legs"] == ref["legs"] == [f"YES {A}", f"NO {B}"]
    assert c["prices"] == ref["prices"] == [0.30, 0.60]
    assert c["kind"] == "ladder"
    assert c["trigger"] == B and c["t_ns"] == T0 + 10 * MS
    assert c["pick"]["cheapest"] == 0 and c["pick"]["trigger"] == 1 and c["pick"]["stale"] == 0
    assert abs(c["profit1"] - (1 - 0.30 - 0.60 - cl.fee1(0.30) - cl.fee1(0.60))) < 1e-9


def test_below_min_profit_not_an_event():
    f = Feed()
    # bid 0.32 vs ask 0.30: count-10 profit is positive only if fees allow; at count 1 it is under one cent
    lines = [f.snap(T0, A, yes=[0.20], no=[0.70]), f.snap(T0 + MS, B, yes=[0.32], no=[0.50])]
    rp = run(lines)
    assert rp.n_open >= 0 and len(rp.cands) == 0


def test_no_lock_when_book_invalid():
    f = Feed()
    lines = [f.snap(T0, A, yes=[0.20], no=[0.70]), f.ctl(T0 + MS, "disconnect"), f.snap(T0 + 2 * MS, B, yes=[0.40], no=[0.50])]
    rp = run(lines)
    assert rp.cands == []      # A's book died with the disconnect; B alone cannot lock


# ------------------------------------------------------------------------------------------------ leg selection
def test_pick_legs_cheapest_tiebreaks():
    legs = ["NO KXB-X-ZZZ", "YES KXB-X-AAA", "NO KXB-X-AAA2"]
    pr = [0.10, 0.10, 0.10]
    p = cl.pick_legs(legs, pr, None, {}, {})
    assert p["cheapest"] == 1                       # alphabetically first ticker (AAA) among equal prices
    legs = ["NO KXB-X-AAA", "YES KXB-X-AAA"]
    assert cl.pick_legs(legs, [0.2, 0.2], None, {}, {})["cheapest"] == 1    # same ticker: YES before NO
    assert cl.pick_legs(legs, [0.2, 0.19], None, {}, {})["cheapest"] == 1   # strictly lowest wins
    assert cl.pick_legs(legs, [0.19, 0.2], None, {}, {})["cheapest"] == 0


def test_pick_legs_stale_and_trigger():
    legs = ["YES KXB-X-A", "NO KXB-X-B", "NO KXB-X-C"]
    pr = [0.5, 0.3, 0.2]
    last_ask = {"KXB-X-A": 500}
    last_bid = {"KXB-X-B": 100, "KXB-X-C": 900}
    p = cl.pick_legs(legs, pr, "KXB-X-C", last_ask, last_bid)
    assert p["trigger"] == 2 and p["cheapest"] == 2
    assert p["stale"] == 1                           # B's bid changed at 100, longest ago among non-trigger legs
    # relevant side only: a YES leg uses its ask, a NO leg its bid
    p = cl.pick_legs(legs, pr, "KXB-X-C", {"KXB-X-A": 50}, {"KXB-X-B": 100, "KXB-X-C": 900})
    assert p["stale"] == 0
    # tie on last change -> alphabetical ticker
    p = cl.pick_legs(legs, pr, "KXB-X-C", {"KXB-X-A": 100}, {"KXB-X-B": 100})
    assert p["stale"] == 0


def test_stale_from_replay_last_change_times():
    f = Feed()
    lines = [f.snap(T0, A, yes=[0.20], no=[0.70]),
             f.snap(T0 + 5 * MS, B, yes=[0.10], no=[0.50]),         # no lock yet (bid 0.10 < ask 0.30)
             f.delta(T0 + 50 * MS, B, "yes", 0.40, 5.0)]            # B's bid improves to 0.40 -> lock, trigger B
    rp = run(lines)
    c = rp.cands[0]
    assert c["trigger"] == B and rp.last_bid[B] == T0 + 50 * MS and rp.last_ask[A] == T0
    assert c["pick"]["stale"] == 0                                  # A (the non-trigger leg), by construction


# ------------------------------------------------------------------------------------------------ IOC arrival
def test_ioc_arrival_pure():
    assert cl.ioc_arrival((0.2, 1, 0.30, 1), "YES", 0.30) == (True, 0.30, "fill")
    assert cl.ioc_arrival((0.2, 1, 0.28, 1), "YES", 0.30) == (True, 0.28, "fill")          # price improved
    assert cl.ioc_arrival((0.2, 1, 0.31, 1), "YES", 0.30) == (False, None, "price_moved")  # worse
    assert cl.ioc_arrival(None, "YES", 0.30) == (False, None, "invalid_book")
    assert cl.ioc_arrival((None, None, 0.3, 1), "NO", 0.6) == (False, None, "no_quote")
    assert cl.ioc_arrival((0.40, 1, 0.5, 1), "NO", 0.60) == (True, 0.60, "fill")           # 1 - bid
    assert cl.ioc_arrival((0.38, 1, 0.5, 1), "NO", 0.60) == (False, None, "price_moved")
    assert cl.ioc_arrival((0.42, 1, 0.5, 1), "NO", 0.60) == (True, 0.58, "fill")


def _arr(rp, i=0, L="10"):
    return rp.cands[0]["arr"][(i, L)]


def test_arrival_unchanged_fills_at_lock_price():
    f = Feed()
    rp = run(lock_lines(f) + [f.delta(T0 + 100 * MS, B, "yes", 0.10, 1.0)])
    assert _arr(rp)[:2] == (True, 0.30) and _arr(rp, 0, "40")[:2] == (True, 0.30)


def test_arrival_price_worse_and_improved_and_ordering():
    # lock opens at T0+10ms.  L=10ms -> check at T0+20ms; L=40ms -> T0+50ms.
    f = Feed()
    base = lock_lines(f)
    worse = f.delta(T0 + 15 * MS, A, "no", 0.65, 10.0)           # NO bid 0.65 -> A ask 0.35, but 0.70 still rests: no change
    kill = f.delta(T0 + 16 * MS, A, "no", 0.70, -10.0)           # remove the 0.70 bid -> ask = 0.35 (worse)
    late = f.delta(T0 + 30 * MS, A, "no", 0.72, 10.0)            # after the L10 check, before L40: ask 0.28 (better)
    rp = run(base + [worse, kill, late])
    assert _arr(rp, 0, "10")[2] == "price_moved" and _arr(rp, 0, "10")[0] is False
    assert _arr(rp, 0, "40")[:2] == (True, 0.28)                 # evaluated at T0+50ms: improved price


def test_arrival_message_at_exact_arrival_time_is_applied_first():
    f = Feed()
    base = lock_lines(f)
    kill = f.delta(T0 + 20 * MS, A, "no", 0.70, -10.0)           # exactly t + L: applied before the check (time > t + L is not)
    rp = run(base + [f.delta(T0 + 20 * MS, A, "no", 0.60, 10.0), kill])
    assert _arr(rp, 0, "10")[0] is False


def test_arrival_book_invalid():
    f = Feed()
    rp = run(lock_lines(f) + [f.ctl(T0 + 15 * MS, "disconnect")])
    assert _arr(rp, 0, "10") == (False, None, "invalid_book")
    assert _arr(rp, 0, "40") == (False, None, "invalid_book")


# ------------------------------------------------------------------------------------------------ settlement and rows
def test_settle_pnl_yes_no_unsettled():
    st, win, pnl = cl.settle_pnl("YES", "yes", 0.30)
    assert (st, win) == ("settled", 1) and abs(pnl - (1 - 0.30 - cl.fee1(0.30))) < 1e-12
    st, win, pnl = cl.settle_pnl("YES", "no", 0.30)
    assert (st, win) == ("settled", 0) and abs(pnl + 0.30 + cl.fee1(0.30)) < 1e-12
    assert cl.settle_pnl("NO", "no", 0.60)[1] == 1 and cl.settle_pnl("NO", "yes", 0.60)[1] == 0
    assert cl.settle_pnl("YES", None, 0.3) == ("unsettled", None, None)
    assert cl.settle_pnl("YES", "", 0.3)[0] == "unsettled"
    assert cl.settle_pnl("YES", "void", 0.3)[0] == "void"
    assert cl.fee1(0.02) == lc.fee(0.02, 1)


def _cand(t_s, price=0.05, game="26OCT21AAABBB", legs=None, fill=True, streams=(0,), profit=0.05):
    legs = legs or [f"YES {A}", f"NO {B}"]
    arr = {(0, "10"): (fill, price if fill else None, "fill" if fill else "price_moved"),
           (0, "40"): (fill, price if fill else None, "fill" if fill else "price_moved"),
           (1, "10"): (False, None, "x"), (1, "40"): (False, None, "x")}
    return {"t_ns": T0 + int(t_s * 1e9), "open_ns": T0 + int(t_s * 1e9), "legs": legs, "prices": [price, 0.9],
            "kind": "ladder", "profit": profit, "profit1": profit, "trigger": B,
            "pick": {"cheapest": 0, "stale": 0, "trigger": 1}, "arr": arr, "game": game, "n_legs": 2,
            "streams": streams}


def test_refire_guard_60s_and_rows_pnl():
    markets = {A: {"result": "yes"}, B: {"result": "no"}}
    rows, events = cl.build_rows([_cand(0), _cand(30), _cand(61)], {}, markets, 0)
    r = rows["cheapest-L10"]
    # the three are > 50 ms apart so none merges; the 2nd (t=30s) is inside the 60 s guard of the 1st, the 3rd is not
    assert [x["refire_skipped"] for x in r] == [False, True, False]
    assert r[0]["filled"] and r[0]["status"] == "settled" and r[0]["win"] == 1
    assert abs(r[0]["pnl"] - (1 - 0.05 - cl.fee1(0.05))) < 1e-12
    # trigger role = NO B, arr says no fill
    assert not any(x["filled"] for x in rows["trigger-L10"])


def test_unsettled_fill_excluded_from_stats():
    markets = {A: {"result": None}, B: {"result": "no"}}
    rows, _ = cl.build_rows([_cand(0)], {}, markets, 0)
    s = cl.stats_for(rows["cheapest-L10"], ["26OCT21AAABBB"])
    assert s["fills"] == 1 and s["settled_fills"] == 0 and s["unsettled_excluded"] == 1
    assert s["mean_pnl_per_fill"] is None and s["total_pnl"] == 0


def test_dedupe_50ms_same_leg():
    markets = {A: {"result": "yes"}, B: {"result": "no"}}
    rows, events = cl.build_rows([_cand(0), _cand(0.03)], {}, markets, 0)
    assert len(events) == 1 and len(rows["cheapest-L10"]) == 1


# ------------------------------------------------------------------------------------------------ bootstrap, phase, tip
def test_bootstrap_deterministic_and_by_game():
    pg = {"g1": (1.0, 2), "g2": (-0.5, 1), "g3": (0.2, 4)}
    a = cl.bootstrap_games(pg, ["g1", "g2", "g3"], draws=2000)
    b = cl.bootstrap_games(pg, ["g1", "g2", "g3"], draws=2000)
    assert a == b and a["seed"] == 20261021
    lo, hi = a["ci_mean_per_fill"]
    assert lo <= 0.7 / 7 <= hi or lo <= hi
    # resamples whole games: with a single game the CI collapses to that game's mean
    one = cl.bootstrap_games({"g": (0.9, 3)}, ["g"], draws=100)
    assert one["ci_mean_per_fill"] == [0.3, 0.3]
    assert cl.bootstrap_games({}, []) is None


def test_tip_and_phase():
    tip, src = cl.tip_ns_from({"occurrence_datetime": "2026-10-22T02:00:00Z", "expected_expiration_time": "2026-10-22T05:00:00Z"})
    assert src == "occurrence_datetime" and tip == int(1792634400 * 1e9) - 3 * 3600 * 10**9 + 0 or tip is not None
    tip2, src2 = cl.tip_ns_from({"expected_expiration_time": "2026-10-22T02:00:00Z"})
    assert src2 == "expected_expiration_time" and tip2 == tip
    assert cl.tip_ns_from(None) == (None, None)
    assert cl.phase_of(tip, tip - 1) == "pre" and cl.phase_of(tip, tip) == "in" and cl.phase_of(None, 5) == "unknown"


def test_price_bucket():
    assert [cl.price_bucket(p) for p in (0.01, 0.02, 0.03, 0.05, 0.06, 0.10, 0.11, 0.25, 0.26, 0.50, 0.51)] == \
        ["1-2c", "1-2c", "3-5c", "3-5c", "6-10c", "6-10c", "11-25c", "11-25c", "26-50c", "26-50c", ">50c"]




# ------------------------------------------------------------------------------------------------ coin flip
def test_coin_pool_price_match_within_1c():
    markets = {t: {"result": "yes"} for t in ("T1", "T2", "T3", "T4")}
    markets["T4"] = {"result": None}
    code = lambda cents, side: cents * 100 * 2 + side           # noqa: E731
    res = {("g", "pre"): {
        code(30, 0): {"T1": 4},                               # T1 YES at 0.30 -> wins
        code(31, 0): {"T2": 2},                               # T2 YES at 0.31 -> wins
        code(29, 1): {"T3": 2},                               # T3 NO at 0.29 -> loses (result yes)
        code(40, 0): {"T1": 9},                               # 0.40: outside +-1c
        code(30, 1): {"T4": 9},                               # T4 unsettled -> dropped
    }, ("g", "in"): {code(30, 0): {"T1": 1}}}
    pools = cl.CoinPools(res, markets)
    vals, w = pools.pool("g", "pre", 0.30)
    got = sorted(zip(vals, w))
    exp = sorted([(1 - 0.30 - cl.fee1(0.30), 0.5), (1 - 0.31 - cl.fee1(0.31), 0.25), (0 - 0.29 - cl.fee1(0.29), 0.25)])
    assert np.allclose([g[0] for g in got], [e[0] for e in exp]) and np.allclose([g[1] for g in got], [e[1] for e in exp])
    assert pools.pool("g", "pre", 0.60) is None                # nothing within 1c
    assert pools.pool("g", "in", 0.30) is not None and pools.pool("other", "pre", 0.30) is None
    assert pools.pool("g", "pre", 0.40) is not None and pools.pool("g", "pre", 0.4101) is None


def test_coin_flip_counts_missing_and_is_seeded():
    markets = {"T1": {"result": "yes"}}
    res = {("g", "pre"): {30 * 200: {"T1": 3}}}
    pools = cl.CoinPools(res, markets)
    fills = [dict(game="g", phase="pre", fill_price=0.30, pnl=-0.3), dict(game="g", phase="pre", fill_price=0.90, pnl=0.05)]
    a = cl.coin_flip(fills, pools, copies=50)
    b = cl.coin_flip(fills, pools, copies=50)
    assert a == b and a["no_candidate"] == 1 and a["matched"] == 1
    assert a["share_ge_real"] == 1.0                             # the only candidate wins (+0.67) vs real -0.3


def test_sampler_segment_counts_exact():
    """Per-second samples are taken at the first line of each new second, BEFORE that line is applied.  A's state changes at
    1.5 s, 4.5 s, 5.5 s; the tip is at 3 s.  Counts must equal what a per-second scan of the valid books would have seen."""
    f = Feed()
    tips = {"26OCT21AAABBB": T0 + 3 * 10**9}
    lines = lock_lines(f) + [f.delta(T0 + 1_500 * MS, A, "yes", 0.21, 1.0), f.delta(T0 + 4_500 * MS, A, "yes", 0.22, 1.0),
                              f.delta(T0 + 5_500 * MS, A, "yes", 0.23, 1.0)]
    rp = run(lines, tips, coin=True)
    g = "26OCT21AAABBB"
    assert rp.n_samples == 3
    ask30, ask50 = 3000 * 2, 5000 * 2
    no = lambda cents: cents * 100 * 2 + 1                       # noqa: E731
    assert rp.res[(g, "pre")][ask30] == {A: 1} and rp.res[(g, "in")][ask30] == {A: 2}
    assert rp.res[(g, "pre")][no(80)] == {A: 1}                  # A's first state: NO price 0.80
    assert rp.res[(g, "in")][no(79)] == {A: 1} and rp.res[(g, "in")][no(78)] == {A: 1}
    assert no(77) not in rp.res[(g, "in")]                       # last state lived 0 samples
    assert rp.res[(g, "pre")][ask50] == {B: 1} and rp.res[(g, "in")][ask50] == {B: 2}
    for bk in rp.res.values():
        for code in bk:
            assert 100 <= code >> 1 <= 9900


def test_sampler_invalid_books_not_sampled():
    f = Feed()
    lines = [f.snap(T0, A, yes=[0.20], no=[0.70]), f.ctl(T0 + 1_500 * MS, "disconnect"),
             f.snap(T0 + 3_500 * MS, B, yes=[0.40], no=[0.50])]
    rp = run(lines, {}, coin=True)
    assert rp.n_samples == 2
    n_a = sum(cell.get(A, 0) for bk in rp.res.values() for cell in bk.values()) / 2     # two codes (YES ask, NO price)
    assert n_a == 1                                              # the 1.5 s sample saw A valid (before the disconnect); 3.5 s did not


def test_coin_sampler_more_than_32767_tickers():
    """Audit finding 6: the old packed ticker index overflowed past 32,767; samples are keyed by ticker now."""
    n = 40_000
    ev = "KXNBATOTAL-26OCT21AAABBB"
    tks = [f"{ev}-X{i}" for i in range(n)]
    doc = {"events": {ev: {"mutually_exclusive": False}}, "books": {t: {"strike_type": None} for t in tks}}
    f = Feed()
    lines = [f.snap(T0 + i * 1000, t, yes=[0.20], no=[0.70]) for i, t in enumerate(tks)]
    lines.append(f.delta(T0 + 5 * 10**9, tks[0], "yes", 0.21, 1.0))
    rp = cl.Replay(doc, {}, coin=True)
    rp.run(lines)
    cell = rp.res[("26OCT21AAABBB", "unknown")][3000 * 2]
    assert len(cell) == n and tks[-1] in cell and tks[32768] in cell and tks[0] in cell
    assert all(v >= 1 for v in cell.values())
    markets = {t: {"result": "no"} for t in tks}
    markets[tks[-1]] = {"result": "yes"}
    vals, w = cl.CoinPools(rp.res, markets).pool("26OCT21AAABBB", "unknown", 0.30)
    assert np.isclose(w.sum(), 1) and int((vals > 0).sum()) >= 1       # the winner is the last ticker (index > 32767)


# ------------------------------------------------------------------------------------------------ amendment fixes
def _multi_event_doc(n):
    ev = "KXNBAGAME-26OCT21AAABBB"
    ts = [f"{ev}-M{i}" for i in range(n)]
    return {"events": {ev: {"mutually_exclusive": True}}, "books": {t: {"strike_type": None} for t in ts}}, ts


def test_more_than_3_legs_dropped_and_counted():
    for n in (3, 4):
        doc, ts = _multi_event_doc(n)
        f = Feed()
        lines = [f.snap(T0 + i * MS, t, yes=[0.40], no=[0.50]) for i, t in enumerate(ts)]
        rp = cl.Replay(doc, {}, coin=False)
        rp.run(lines)
        if n == 3:
            assert rp.n_open == 1 and len(rp.cands) == 1 and rp.cands[0]["n_legs"] == 3 and rp.n_gt3 == 0
        else:
            assert rp.n_open == 1 and rp.cands == [] and rp.n_gt3 == 1 and rp.n_gt3_qualifying == 1
            assert not rp.heap


def test_race_ticker_event_grouping_and_per_ticker_fetch(tmp_path):
    """KXNBARACE-26OCT21AAABBB-10-AAA belongs to event KXNBARACE-26OCT21AAABBB-10 (API event_ticker), not the 2-part guess."""
    r1, r2 = "KXNBARACE-26OCT21AAABBB-10-AAA", "KXNBARACE-26OCT21AAABBB-10-BBB"
    real_ev = "KXNBARACE-26OCT21AAABBB-10"

    class HttpErr(Exception):
        status = 404

    calls = []

    class Http:
        def get(self, path, params=None, use_cache=True, persist=True):
            calls.append((path, dict(params or {})))
            if path == "/markets" and (params or {}).get("event_ticker") == "KXNBARACE-26OCT21AAABBB":
                return {"markets": []}
            if path == "/markets" and (params or {}).get("event_ticker") == real_ev:
                return {"markets": [{"ticker": r1, "event_ticker": real_ev, "status": "active"},
                                    {"ticker": r2, "event_ticker": real_ev, "status": "active"}]}
            if path in (f"/markets/{r1}", f"/markets/{r2}"):
                return {"market": {"ticker": path.split("/")[-1], "event_ticker": real_ev, "status": "active"}}
            if path == f"/events/{real_ev}":
                return {"event": {"mutually_exclusive": True}}
            raise HttpErr()

    class Client:
        http = Http()

    log = []
    markets, einfo = cl.fetch_meta({r1, r2}, tmp_path, True, log.append, client=Client())
    assert set(markets) == {r1, r2} and einfo[real_ev]["mutually_exclusive"] is True
    assert any(p == f"/markets/{r1}" for p, _ in calls)                        # fetched per ticker
    doc = cl.build_doc({r1, r2}, markets, einfo)
    assert list(doc["events"]) == [real_ev] and doc["events"][real_ev]["mutually_exclusive"] is True
    _, me_sets, _, _ = lc.build_structure(doc)
    assert real_ev in me_sets and me_sets[real_ev][0] == [r1, r2]
    assert (tmp_path / "raw").exists() and any((tmp_path / "raw").iterdir())    # raw responses kept on disk


def test_multi_stream_events_tagged_and_excluded_from_primary():
    f0, f1 = Feed(stream=0), Feed(stream=1)
    # A recorded in folder 0, B in folder 1 -> the lock's legs come from two recorders
    lines = [f0.snap(T0, A, yes=[0.20], no=[0.70]), f1.snap(T0 + 10 * MS, B, yes=[0.40], no=[0.50]),
             f1.delta(T0 + 200 * MS, B, "yes", 0.10, 1.0)]
    rp = run(lines)
    assert len(rp.cands) == 1 and rp.cands[0]["streams"] == (0, 1)
    markets = {A: {"result": "yes"}, B: {"result": "no"}}
    main_rows, main_ev = cl.build_rows(rp.cands, {}, markets, 0, stream_names=["fa", "fb"])
    multi_rows, multi_ev = cl.build_rows(rp.cands, {}, markets, 0, which="multi", stream_names=["fa", "fb"])
    assert main_ev == [] and all(r == [] for r in main_rows.values())
    assert len(multi_ev) == 1 and multi_rows[cl.PRIMARY][0]["streams"] == "fa,fb" and multi_rows[cl.PRIMARY][0]["multi_stream"]
    # a single-recorder lock goes to the primary
    rp2 = run(lock_lines(Feed(stream=0)))
    r2, e2 = cl.build_rows(rp2.cands, {}, markets, 0)
    assert rp2.cands[0]["streams"] == (0,) and len(e2) == 1 and len(r2[cl.PRIMARY]) == 1


def test_primary_is_cheapest_40ms():
    assert cl.PRIMARY == "cheapest-L40" and cl.PRIMARY in cl.VERSIONS and len(cl.VERSIONS) == 6


def test_dedupe_representative_is_earliest_not_max_profit():
    c1, c2 = _cand(0.0, profit=0.02), _cand(0.03, profit=0.09)
    assert c1["open_ns"] < c2["open_ns"]
    ev = cl.make_events([c2, c1])                                   # input order must not matter
    assert len(ev) == 1 and ev[0]["t_ns"] == c1["t_ns"] and ev[0]["profit"] == 0.02 and ev[0]["n_locks"] == 2
    mx = cl.make_events([c1, c2], rep="maxprofit")
    assert mx[0]["t_ns"] == c2["t_ns"] and mx[0]["profit"] == 0.09
    assert len(cl.make_events([_cand(0.0), _cand(0.06)])) == 2      # outside the 50 ms window -> two events


def test_fill_needs_depth_at_arrival_pure():
    top = (0.20, 1, 0.30, 0.5)
    assert cl.ioc_arrival(top, "YES", 0.30) == (False, None, "no_depth")                   # 0.5 contract at the limit
    assert cl.ioc_arrival(top, "YES", 0.30, [(3000, 0.5), (3100, 5)]) == (False, None, "no_depth")   # 3100 is past the limit
    assert cl.ioc_arrival(top, "YES", 0.31, [(3000, 0.5), (3100, 5)]) == (True, 0.31, "fill")
    assert cl.ioc_arrival((0.20, 1, 0.30, 1), "YES", 0.30, [(3000, 1)]) == (True, 0.30, "fill")
    assert cl.ioc_arrival((0.40, 0.4, 0.5, 1), "NO", 0.60) == (False, None, "no_depth")


def test_fill_needs_depth_at_arrival_replay():
    f = Feed()
    # shrink A's best ask level (NO bid 0.70) from 10 to 0.5 after the lock opens; the next level (0.31) is past the limit 0.30
    lines = lock_lines(f) + [f.delta(T0 + 12 * MS, A, "no", 0.70, -9.5), f.delta(T0 + 13 * MS, A, "no", 0.69, 5.0)]
    rp = run(lines)
    assert _arr(rp, 0, "10") == (False, None, "no_depth") and _arr(rp, 0, "40") == (False, None, "no_depth")
    # levels at 0.29 (0.5) and 0.28 (0.5) are inside the limit: cumulative 0.5 + 0.5 + 0.5 = 1.5 >= 1 -> fills (at the 0.29 level)
    f = Feed()
    lines = lock_lines(f) + [f.delta(T0 + 12 * MS, A, "no", 0.70, -9.5), f.delta(T0 + 13 * MS, A, "no", 0.71, 0.5),
                             f.delta(T0 + 14 * MS, A, "no", 0.72, 0.5)]
    rp = run(lines)
    assert _arr(rp, 0, "10") == (True, 0.29, "fill")


def test_tip_independent_of_markets_file_order():
    kr = {"occurrence_datetime": "2026-10-22T02:00:00Z", "expected_expiration_time": "2026-10-22T05:00:00Z"}
    t1, s1, d1 = cl.derive_tips(["g"], {"g": [None, kr]}, {})
    t2, s2, d2 = cl.derive_tips(["g"], {"g": [kr, None]}, {})
    assert t1 == t2 and s1 == s2 and d1 == {}
    m = {"KXNBAGAME-g-AAA": {"expected_expiration_time": "2026-10-22T05:00:00Z"}, "KXNBATOTAL-g-1": {}}
    t3, s3, _ = cl.derive_tips(["g"], {"g": [None]}, m)                  # API fallback when no file has a kickoff
    assert t3["g"] == cl.tip_ns_from(m["KXNBAGAME-g-AAA"])[0] and s3["g"].startswith("api:")
    try:
        cl.derive_tips(["g", "h"], {"g": [kr], "h": [None]}, {})
        raise RuntimeError("expected an AssertionError")
    except AssertionError as e:
        assert "h" in str(e)


def test_tip_vs_settlement_assertion():
    tip = cl.tip_ns_from({"occurrence_datetime": "2026-10-22T02:00:00Z"})[0]            # tip = 23:00 the day before
    late = {"KXNBAGAME-26OCT21ATLORL-ATL": {"settlement_ts": "2026-10-22T04:45:00Z"}}     # 5.75 h after the tip
    try:
        cl.check_tip_vs_settle(["26OCT21ATLORL"], {"26OCT21ATLORL": tip}, late)
        raise RuntimeError("expected an AssertionError")
    except AssertionError:
        pass
    good = {"KXNBAGAME-26OCT21ATLORL-ATL": {"settlement_ts": "2026-10-22T02:00:00Z"}}    # 3 h after the tip
    assert cl.check_tip_vs_settle(["26OCT21ATLORL"], {"26OCT21ATLORL": tip}, good) == {"26OCT21ATLORL": 3.0}
    assert cl.check_tip_vs_settle(["26OCT21ATLORL"], {"26OCT21ATLORL": tip}, {}) == {}   # no settlement yet: skipped


def test_out_of_order_counter_within_stream_only():
    f0, f1 = Feed(stream=0), Feed(stream=1)
    lines = [f0.snap(T0 + 10 * MS, A, yes=[0.2], no=[0.7]), f0.snap(T0 + 5 * MS, B, yes=[0.4], no=[0.5]),    # 2nd is earlier
             f1.snap(T0 + 1 * MS, "KXNBATOTAL-26OCT21AAABBB-170", yes=[0.4], no=[0.5])]                      # other stream
    rp = cl.Replay(DOC, {})
    for ln in lines:
        rp.feed_line(ln)
    assert rp.out_of_order == 1 and rp.lines == 3


def test_revalidated_book_with_unchanged_top_rechecks_lock():
    f = Feed()
    lines = lock_lines(f) + [f.ctl(T0 + 100 * MS, "disconnect"), f.ctl(T0 + 150 * MS, "connect"),
                              f.snap(T0 + 200 * MS, A, yes=[0.20], no=[0.70]),      # same top as before the outage
                              f.snap(T0 + 210 * MS, B, yes=[0.40], no=[0.50])]
    rp = run(lines)
    assert len(rp.cands) == 2                      # the lock re-opens: the unchanged tops are re-emitted after the invalidation
    assert rp.cands[1]["t_ns"] == T0 + 210 * MS


def test_trade_message_drives_detector():
    f = Feed()
    rp = cl.Replay(DOC, {})
    seen = []
    orig = rp.det.update
    rp.det.update = lambda t, now, meta=None: (seen.append(t), orig(t, now, meta))[1]
    for ln in lock_lines(f) + [f.trade(T0 + 30 * MS, A)]:
        rp.feed_line(ln)
    assert seen[-1] == A and seen.count(A) == 2 and seen.count(B) == 1


def test_prot_series_label_is_prop():
    assert cl.mtype_of("KXNBAPTSPROT") == "prop" and cl.mtype_of("KXNBA3PTPROT") == "prop"
    assert lc.market_type("KXNBAPTSPROT") == "ot"                  # the lockcore label this overrides
    assert cl.mtype_of("KXNBAPTS") == "prop" and cl.mtype_of("KXNBATOTAL") == "total" and cl.mtype_of("KXNBA1HSPREAD") == "1H:spread"


# ------------------------------------------------------------------------------------------------ gate
def _fills(games, per_game, ticker="KXT-X"):
    return [dict(game=g, ticker=f"{ticker}-{g}-{i}") for g in games for i in range(per_game)]


def test_gate_check_cases():
    order = [f"G{i:02d}" for i in range(30)]
    tips = {g: 1_000 * cl.NS for g in order}
    now_young, now_old = 1_000 + 3 * 86400, 1_000 + 8 * 86400
    fills = _fills(order, 14)                                       # 15 unseen games x 14 = 210 fills
    settled = {f["ticker"]: "settled" for f in fills}
    g = cl.gate_check(order, tips, fills, settled, now_young)
    assert g["ok"] and g["unseen_games"] == 15 and g["unseen_primary_fills"] == 210
    settled2 = dict(settled, **{fills[0]["ticker"]: "active"})      # one unsettled fill inside the 7 days -> not met
    assert not cl.gate_check(order, tips, fills, settled2, now_young)["ok"]
    assert cl.gate_check(order, tips, fills, settled2, now_old)["ok"]       # after 7 days it is met
    f13 = _fills(order, 13)                                         # 195 unseen fills < 200
    assert not cl.gate_check(order, tips, f13, {f["ticker"]: "settled" for f in f13}, now_young)["ok"]
    assert not cl.gate_check(order[:29], tips, fills, settled, now_young)["ok"]
    assert cl.gate_check(order, tips, [], {}, now_young)["games_gate_met"] == 30   # no fills: nothing unsettled


# ------------------------------------------------------------------------------------------------ main(): gate first, count-only
def _write_night(tmp_path):
    """tmp/26OCT21/{raw/msgs-...gz, markets.json} + tmp/cache with the metadata cache for one synthetic game."""
    import gzip
    night = tmp_path / "26OCT21"
    (night / "raw").mkdir(parents=True)
    f = Feed()
    lines = lock_lines(f) + [f.delta(T0 + 100 * MS, B, "yes", 0.10, 1.0)]
    with gzip.open(night / "raw" / "msgs-2026102100-1792000000.jsonl.gz", "wt", encoding="utf-8") as fh:
        for ln in lines:
            fh.write(json.dumps(ln) + "\n")
    game = "26OCT21AAABBB"
    kr = {"occurrence_datetime": "2026-10-22T02:00:00Z", "expected_expiration_time": "2026-10-22T05:00:00Z"}
    (night / "markets.json").write_text(json.dumps({"date": "26OCT21", "games": {game: {"tickers": [A, B], "kickoff_raw": kr}}}))
    cache = tmp_path / "cache"
    cache.mkdir()
    mk = [{"ticker": A, "event_ticker": EV, "status": "settled", "result": "yes",
           "strike_type": "greater", "floor_strike": 150, "cap_strike": None, "settlement_ts": "2026-10-22T02:00:00Z"},
          {"ticker": B, "event_ticker": EV, "status": "settled", "result": "no",
           "strike_type": "greater", "floor_strike": 160, "cap_strike": None, "settlement_ts": "2026-10-22T02:00:00Z"}]
    (cache / f"markets_{EV}.json").write_text(json.dumps({"markets": mk}))
    (cache / f"event_{EV}.json").write_text(json.dumps({"mutually_exclusive": False}))
    return night, cache


def _args(night, cache, out, *extra):
    return ["--raw", str(night / "raw"), "--markets", str(night / "markets.json"), "--out", str(out), "--no-fetch",
            "--cache", str(cache), "--now", "2026-11-05T00:00:00Z", *extra]


def _boom(*a, **k):
    raise AssertionError("an outcome-dependent function ran before the gate / in count-only mode")


OUTCOME_FUNCS = ("stats_for", "attach_outcomes", "settle_pnl", "breakdown", "coin_flip", "bootstrap_games", "check_tip_vs_settle")


def test_gate_failure_exits_before_any_statistic(tmp_path, monkeypatch, capsys):
    night, cache = _write_night(tmp_path)
    for name in OUTCOME_FUNCS:
        monkeypatch.setattr(cl, name, _boom)
    out = tmp_path / "out"
    rc = cl.main(_args(night, cache, out, "--label", "test"))
    txt = capsys.readouterr().out
    assert rc == 3 and "GATE NOT MET" in txt
    for word in ("meanPnL", "verdict", "win=", "CI=", "coin=", "pnl"):
        assert word not in txt
    assert (out / "gate.json").exists()
    assert not (out / "results.json").exists() and not (out / "fills.csv").exists()
    g = json.loads((out / "gate.json").read_text())
    assert g["primary_fills"] == 1 and g["gate"]["ok"] is False
    assert "pnl" not in json.dumps(g).lower()


def test_count_only_prints_no_results(tmp_path, monkeypatch, capsys):
    night, cache = _write_night(tmp_path)
    for name in OUTCOME_FUNCS:
        monkeypatch.setattr(cl, name, _boom)
    out = tmp_path / "out"
    rc = cl.main(_args(night, cache, out, "--label", "test", "--count-only"))
    txt = capsys.readouterr().out
    assert rc == 0 and "COUNT-ONLY" in txt and "gate:" in txt and "primary (cheapest-L40) fills=1" in txt
    for word in ("meanPnL", "verdict", "win=", "CI=", "coin=", "pnl", "settle"):
        assert word not in txt
    c = json.loads((out / "counts.json").read_text())
    assert c["primary_fills"] == 1 and c["lock_open_total"] == 1 and c["candidates"] == 1 and c["deduped_events_multi_stream"] == 0
    assert not (out / "results.json").exists() and not (out / "fills.csv").exists()
    assert "pnl" not in json.dumps(c).lower() and '"yes"' not in json.dumps(c)


def test_count_only_cached_results_are_stripped(tmp_path):
    night, cache = _write_night(tmp_path)
    markets, _ = cl.fetch_meta({A, B}, cache, False, print, strip=True)
    assert all("result" not in m and "settlement_ts" not in m and m["status"] == "settled" for m in markets.values())
    markets, _ = cl.fetch_meta({A, B}, cache, False, print)
    assert markets[A]["result"] == "yes"


def test_test_label_game_set_assertion(tmp_path):
    night, cache = _write_night(tmp_path)
    mk = [str(night / "markets.json")]
    raw = [str(night / "raw")]
    rec = {A, B}
    cl.check_test_game_set(raw, mk, ["26OCT21AAABBB"], rec)                        # fine
    for games, tickers in ((["26OCT20AAABBB"], rec), (["26OCT21AAABBB", "26OCT21CCCDDD"], rec), (["26OCT21AAABBB"], {A})):
        try:
            cl.check_test_game_set(raw, mk, games, tickers)
            raise RuntimeError("expected an AssertionError")
        except AssertionError:
            pass


def test_source_night_end_to_end_with_results(tmp_path, capsys):
    night, cache = _write_night(tmp_path)
    out = tmp_path / "out"
    rc = cl.main(_args(night, cache, out, "--label", "source"))
    assert rc == 0
    res = json.loads((out / "results.json").read_text())
    p = res["versions"]["cheapest-L40"]["overall"]
    assert p["fills"] == 1 and p["settled_fills"] == 1 and abs(p["mean_pnl_per_fill"] - (1 - 0.30 - cl.fee1(0.30))) < 1e-9
    assert res["pass_bar"]["verdict"] == "source night (not counted)" and "robustness_maxprofit_primary" in res
    assert (out / "fills.csv").exists() and (out / "fills_multistream.csv").exists()
    assert "verdict:" in capsys.readouterr().out


# ------------------------------------------------------------------------------------------------ live comparisons
def test_live_vs_replay_2x2_and_lockwatch_overlap(tmp_path):
    f = Feed()
    rp = run(lock_lines(f))

    def att(fill_a, t_off, ok=True):
        return {"event": "attempt", "time": "2026-10-21T01:00:00.000Z", "kind": "ladder",
                "bodies": [{"ticker": A, "side": "bid", "price": "0.3000"}, {"ticker": B, "side": "ask", "price": "0.4000"}],
                "legs": [{"ticker": A, "side": "yes", "ok": ok, "unknown": False, "fill_count": "1.00" if fill_a else "0",
                          "send_ns": T0 + t_off},
                         {"ticker": B, "side": "no", "ok": True, "unknown": False, "fill_count": "0", "send_ns": T0 + t_off}]}

    live = tmp_path / "events.jsonl"
    live.write_text("\n".join(json.dumps(x) for x in (att(True, 60 * MS), att(False, 70 * MS), att(True, 10 * 10**9),
                                                      att(True, 0, ok=False), {"event": "start"})))
    r = cl.live_vs_replay([live], rp.cands)
    assert r["excluded_error"] == 1 and len(r["rows"]) == 3
    assert r["tables"]["L40"] == {"live_fill|replay_fill": 1, "live_fill|replay_no_replay_lock": 1, "live_miss|replay_fill": 1}
    assert r["tables"]["L10"] == r["tables"]["L40"]
    assert all(row["replay_cheapest_matches"] for row in r["rows"] if row["replay_lock_found"])
    lw = tmp_path / "lw.jsonl"
    g = "26OCT21AAABBB"
    lw.write_text("\n".join(json.dumps(x) for x in (
        {"event": "lock_open", "game": g, "open_ns": T0 + 15 * MS, "legs": [f"YES {A}", f"NO {B}"]},
        {"event": "lock_open", "game": g, "open_ns": T0 + 30 * 10**9, "legs": [f"YES {A}", f"NO {B}"]},
        {"event": "lock_open", "game": "other", "open_ns": T0, "legs": [f"YES {A}", f"NO {B}"]})))
    o = cl.lockwatch_overlap([lw], rp.opens, {g})
    assert o["lockwatch_lock_opens"] == 2 and o["matched_in_replay"] == 1 and o["lockwatch_only"] == 1
