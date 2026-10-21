import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lockwatch as lw


def test_fee_known():
    assert abs(lw.fee(0.5, 1) - 0.0175) < 1e-9


def test_ladder_inversion():
    # weak ask .40 size 5, strong bid .45 size 8 -> n=5; gross .25
    lk = lw.ladder_lock("W", "S", (0.3, 1, 0.40, 5), (0.45, 8, 0.5, 1))
    exp = 5 * 0.05 - lw.fee(0.40, 5) - lw.fee(0.55, 5)
    assert lk and lk["n"] == 5 and abs(lk["profit"] - round(exp, 4)) < 1e-9 and exp > 0


def test_no_inversion():
    assert lw.ladder_lock("W", "S", (0.3, 1, 0.50, 5), (0.45, 8, 0.6, 1)) is None
    assert lw.ladder_lock("W", "S", (0.3, 1, 0.45, 5), (0.45, 8, 0.6, 1)) is None   # zero gross, fees make it negative
    assert lw.ladder_lock("W", "S", (0.3, 1, None, 0), (0.45, 8, 0.6, 1)) is None


def test_less_direction():
    doc = {"events": {"E": {"mutually_exclusive": False}},
           "books": {"E-X-T100": {"strike_type": "less", "cap": 100.0}, "E-X-T90": {"strike_type": "less", "cap": 90.0},
                     "E-X-T10": {"strike_type": "greater", "floor": 10.0}, "E-X-T20": {"strike_type": "greater", "floor": 20.0}}}
    groups, _, _, _ = lw.build_structure(doc)
    # less: YES(cap 90) implies YES(cap 100) -> weak is E-X-T100 (k=100), strong E-X-T90 (k=90)
    tops = {"E-X-T100": (0.3, 5, 0.40, 5), "E-X-T90": (0.5, 5, 0.6, 5), "E-X-T10": (0.3, 5, 0.40, 5), "E-X-T20": (0.5, 5, 0.6, 5)}
    opened = []
    det = lw.Detector(doc, lambda t: tops.get(t), lambda ev, st, now: opened.append((ev, st["legs"])))
    det.update("E-X-T90", 1.0)
    assert ("lock_open", ["YES E-X-T100", "NO E-X-T90"]) in opened
    assert not any("E-X-T90" in l[0] and l[0].startswith("YES E-X-T90") for _, l in opened)
    # greater: weak is floor 10 (E-X-T10), strong floor 20 (E-X-T20)
    det.update("E-X-T20", 2.0)
    assert ("lock_open", ["YES E-X-T10", "NO E-X-T20"]) in opened
    # book goes invalid -> close
    tops["E-X-T90"] = None
    det.update("E-X-T90", 3.0)
    assert ("lock_close", ["YES E-X-T100", "NO E-X-T90"]) in opened


def test_me_no():
    tops = {t: (0.40, 10, 0.45, 10) for t in "abc"}          # sum bids 1.20
    out = lw.me_locks("E", list("abc"), tops, False)
    assert len(out) == 1 and out[0]["kind"] == "me_no"
    exp = 10 * 0.2 - 3 * lw.fee(0.6, 10)
    assert abs(out[0]["profit"] - round(exp, 4)) < 1e-9
    tops["b"] = (0.20, 10, 0.25, 10)                          # sum bids 1.00 -> no lock
    assert lw.me_locks("E", list("abc"), tops, False) == []
    tops["b"] = None                                          # invalid book -> none
    assert lw.me_locks("E", list("abc"), tops, False) == []


def test_me_yes_exhaustive():
    tops = {t: (0.20, 10, 0.30, 10) for t in "abc"}           # sum asks .90
    assert [x["kind"] for x in lw.me_locks("E", list("abc"), tops, True)] == ["me_yes"]
    assert lw.me_locks("E", list("abc"), tops, False) == []
