import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lockcore as lc

MS = 1_000_000


def lk(legs, open_ms, life, profit, series="KXNBATOTAL", game="26OCT20AAABBB"):
    return dict(game=game, night=lc.night_of(game), series=series, kind="ladder", legs=legs, open_ns=int(open_ms * MS),
                life_ms=life, profit=profit, is_prop=lc.is_prop_series(series), mtype=lc.market_type(series))


def test_fee_known():
    assert abs(lc.fee(0.5, 1) - 0.0175) < 1e-9


def test_ladder_inversion_and_fees():
    x = lc.ladder_lock("W", "S", (0.3, 1, 0.40, 5), (0.45, 8, 0.5, 1))
    exp = 5 * 0.05 - lc.fee(0.40, 5) - lc.fee(0.55, 5)
    assert x and x["n"] == 5 and abs(x["profit"] - round(exp, 4)) < 1e-9 and exp > 0
    assert len(x["fees"]) == 2 and x["sizes"] == [5, 8]
    assert lc.ladder_lock("W", "S", (0.3, 1, 0.50, 5), (0.45, 8, 0.6, 1)) is None
    assert lc.ladder_lock("W", "S", (0.3, 1, 0.45, 5), (0.45, 8, 0.6, 1)) is None
    assert lc.ladder_lock("W", "S", (0.3, 1, None, 0), (0.45, 8, 0.6, 1)) is None


def test_less_direction_and_meta():
    doc = {"events": {"E": {"mutually_exclusive": False}},
           "books": {"E-X-T100": {"strike_type": "less", "cap": 100.0}, "E-X-T90": {"strike_type": "less", "cap": 90.0}}}
    tops = {"E-X-T100": (0.3, 5, 0.40, 5), "E-X-T90": (0.5, 5, 0.6, 5)}
    ev = []
    det = lc.Detector(doc, lambda t: tops.get(t),
                      lambda e, st, now: ev.append((e, st["legs"], st["open_meta"], st.get("close_meta"))))
    det.update("E-X-T90", 1.0, {"ts_ms": 11})
    assert ev[0][:3] == ("lock_open", ["YES E-X-T100", "NO E-X-T90"], {"ts_ms": 11})
    tops["E-X-T90"] = None
    det.update("E-X-T90", 2.0, {"ts_ms": 22})
    assert ev[1][0] == "lock_close" and ev[1][3] == {"ts_ms": 22}


def test_me_no_and_yes():
    tops = {t: (0.40, 10, 0.45, 10) for t in "abc"}
    out = lc.me_locks("E", list("abc"), tops, False)
    assert len(out) == 1 and out[0]["kind"] == "me_no"
    assert abs(out[0]["profit"] - round(10 * 0.2 - 3 * lc.fee(0.6, 10), 4)) < 1e-9
    tops["b"] = None
    assert lc.me_locks("E", list("abc"), tops, False) == []
    tops = {t: (0.20, 10, 0.30, 10) for t in "abc"}
    assert [x["kind"] for x in lc.me_locks("E", list("abc"), tops, True)] == ["me_yes"]


def test_is_prop():
    for s in ("KXNBAPTS", "KXNBAREB", "KXNBAAST", "KXNBA3PT", "KXNBAPRA", "KXNBASTL", "KXNBABLK", "KXNBASTOCK",
              "KXNBA2D", "KXNBA3D"):
        assert lc.is_prop_series(s)
    for s in ("KXNBAGAME", "KXNBASPREAD", "KXNBATOTAL", "KXNBA1HSPREAD"):
        assert not lc.is_prop_series(s)
    rec = dict(legs=["YES KXNBAREB-26OCT20AAABBB-X-5", "NO KXNBAREB-26OCT20AAABBB-X-7"], open_ns=5, lifetime_ms=12,
               max_profit=1.0)
    n = lc.normalize_close(rec)
    assert n["is_prop"] and n["night"] == "26OCT20" and n["mtype"] == "prop"


def test_normalize_old_record():
    rec = {"event": "lock_close", "t_open": "2026-10-20T19:09:19.939Z", "duration_s": 0.007, "kind": "ladder",
           "legs": ["YES KXNBATOTAL-26OCT20BOSDET-245", "NO KXNBATOTAL-26OCT20BOSDET-247"], "max_profit": 0.0149}
    n = lc.normalize_close(rec)
    assert n and abs(n["life_ms"] - 7) < 1e-9 and n["game"] == "26OCT20BOSDET" and not n["is_prop"]


def test_dedup_grouping():
    a = lk(["YES A", "NO B"], 0, 30, 1.0)
    b = lk(["YES A", "NO C"], 40, 30, 2.0)       # shares YES A, within 50 ms of a -> same event
    c = lk(["YES X", "NO Y"], 10, 30, 0.5)       # shares nothing -> own event
    d = lk(["YES A", "NO D"], 500, 30, 0.7)      # shares a leg but 460 ms later -> own event
    e = lk(["YES B", "NO Z"], 60, 30, 0.2)       # YES B is not NO B -> own event
    ev = lc.dedupe([a, b, c, d, e])
    assert len(ev) == 4
    big = [x for x in ev if x["n_locks"] == 2][0]
    assert big["profit"] == 2.0
    # chain: a-b 40 ms, b-f 40 ms (a-f 80 ms) -> one event
    f = lk(["NO C"], 80, 30, 0.1)
    assert len(lc.dedupe([a, b, f])) == 1


def test_reachable_threshold():
    evs = [lk(["a"], 0, 10, 1), lk(["b"], 0, 10.5, 1), lk(["c"], 0, 9.9, 1)]
    r = lc.reachable(evs, 8)                      # needs >= 10 ms
    assert [e["legs"][0] for e in r] == ["a", "b"]


def test_value_without_biggest_event():
    evs = [lk(["a"], 0, 50, 5.0), lk(["b"], 0, 50, 2.0, game="26OCT21AAABBB"),
           lk(["c"], 0, 50, 1.0, game="26OCT21AAABBB")]
    nights = {"26OCT20", "26OCT21"}
    assert abs(lc.mean_value_per_night(evs, nights) - 4.0) < 1e-9
    assert abs(lc.mean_value_per_night(evs, nights, drop_biggest=True) - 1.5) < 1e-9
    assert lc.value_by_night(evs)["26OCT21"] == 3.0


def test_delay_stats_offset():
    s = [(int((1000 + i) * MS), 1000 + i - 20) for i in range(10)]    # raw delay 20 ms
    assert lc.delay_stats(s, [(0, -5.0)])["median_ms"] == 15.0
    assert lc.delay_stats([], [])["n"] == 0
