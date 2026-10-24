import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lockwatch_ml as lm

DOC = json.loads((Path(__file__).parent / "bosdet.json").read_text(encoding="utf-8"))
SPECS = lm.parse_markets(DOC)
LOCKS = lm.build_locks(SPECS)
MARGINS = [m for m in range(-60, 61) if m != 0]
B = "KXNBAGAME-26OCT20BOSDET-BOS"
D = "KXNBAGAME-26OCT20BOSDET-DET"


def sp(team, k):
    return next(t for t, x in SPECS.items() if x["kind"] == "S" and x["team"] == team and x["k"] == k)


def bk(team, lo):
    return next(t for t, x in SPECS.items() if x["kind"] == "M" and x["team"] == team and x["lo"] == lo)


def min_payout(legs):
    return min(sum((lm.settle(SPECS[t], m) if s == "YES" else 1 - lm.settle(SPECS[t], m)) for s, t in legs) for m in MARGINS)


def mk(tops):
    return lambda t: tops.get(t)


def find(kind, legs):
    return next(l for l in LOCKS if l["kind"] == kind and l["legs"] == legs)


def test_mapping_real():
    n_spread = sum(1 for t in DOC["books"] if t.startswith("KXNBASPREAD-26OCT20BOSDET"))
    assert len(SPECS) == 2 + 8 + n_spread
    buckets = {t.split("-")[-1]: (s["team"], s["lo"], s["hi"]) for t, s in SPECS.items() if s["kind"] == "M"}
    assert buckets == {"BOS1TO5": ("BOS", 1, 5), "BOS6TO10": ("BOS", 6, 10), "BOS11TO20": ("BOS", 11, 20), "BOS21PLUS": ("BOS", 21, None),
                       "DET1TO5": ("DET", 1, 5), "DET6TO10": ("DET", 6, 10), "DET11TO20": ("DET", 11, 20), "DET21PLUS": ("DET", 21, None)}
    for team in ("BOS", "DET"):
        ks = sorted(s["k"] for s in SPECS.values() if s["team"] == team and s["kind"] == "S")
        print(team, "ladder strikes:", ks)
        assert ks
    assert {t for t, s in SPECS.items() if s["kind"] == "W"} == {B, D}
    for t, s in SPECS.items():
        assert t.split("-")[-1].startswith(s["team"])


def test_all_locks_brute_force():
    kinds = {l["kind"] for l in LOCKS}
    assert kinds == {"ladder", "cross_no", "tail_a", "tail_b", "bucket_cheap", "bucket_rich", "me_buckets_yes", "me_buckets_no",
                     "me_win_yes", "me_win_no"}, kinds
    for l in LOCKS:
        assert min_payout(l["legs"]) >= l["payout"], l


def test_misspecified_fails():
    assert min_payout([("YES", sp("BOS", 10.5)), ("NO", sp("BOS", 5.5))]) < 1        # reversed ladder
    assert min_payout([("YES", B), ("NO", sp("DET", 5.5))]) < 1                       # wrong cross
    assert min_payout([("NO", B), ("NO", sp("DET", 5.5))]) >= 1                       # right cross
    assert min_payout([("YES", B), ("YES", D)]) == 1 and min_payout([("YES", B), ("YES", B)]) < 1
    m = bk("BOS", 6)
    assert min_payout([("YES", m), ("YES", sp("BOS", 10.5)), ("NO", sp("BOS", 5.5))]) == 1
    assert min_payout([("YES", m), ("YES", sp("BOS", 10.5)), ("NO", sp("BOS", 4.5))]) < 1   # wrong bucket edge
    assert min_payout([("NO", m), ("NO", sp("BOS", 10.5)), ("YES", sp("BOS", 5.5))]) == 2
    assert min_payout([("NO", m), ("NO", sp("BOS", 10.5)), ("YES", sp("BOS", 5.5))]) < 3  # overclaimed payout fails
    assert min_payout([("NO", bk("BOS", 21)), ("YES", sp("BOS", 20.5))]) == 1
    assert min_payout([("NO", bk("BOS", 21)), ("YES", sp("BOS", 22.5))]) < 1


def test_profit_ladder():
    S = sp("BOS", 5.5)
    lk = find("ladder", [("YES", B), ("NO", S)])
    r = lm.eval_lock(lk, mk({B: (0.3, 9, 0.40, 5), S: (0.50, 7, 0.6, 3)}))
    exp = 5 * (1 - 0.40 - 0.50) - lm.fee(0.40, 5) - lm.fee(0.50, 5)   # fees: ceil(.07*5*.24/1e-4)=840 -> .084 ; .0875 -> .0875 (hand)
    assert r["n"] == 5 and r["prices"] == [0.40, 0.50]
    assert abs(r["profit"] - (0.5 - 0.084 - 0.0875)) < 1e-9 and abs(exp - r["profit"]) < 1e-9
    assert lm.eval_lock(lk, mk({B: (0.3, 9, 0.50, 5), S: (0.50, 7, 0.6, 3)})) is None
    assert lm.eval_lock(lk, mk({B: None, S: (0.50, 7, 0.6, 3)})) is None


def test_profit_cross():
    X, Y = sp("BOS", 2.5), sp("DET", 2.5)
    lk = find("cross_no", [("NO", X), ("NO", Y)])
    r = lm.eval_lock(lk, mk({X: (0.55, 4, 0.6, 1), Y: (0.55, 6, 0.6, 1)}))
    # n=4, costs .45+.45; fee each: ceil(.07*4*.45*.55/1e-4)=ceil(693)=693 -> .0693
    assert r["n"] == 4 and abs(r["profit"] - (4 * 0.10 - 2 * 0.0693)) < 1e-9


def test_profit_bucket():
    M, Sh, Sl = bk("BOS", 6), sp("BOS", 10.5), sp("BOS", 5.5)
    r = lm.eval_lock(find("bucket_cheap", [("YES", M), ("YES", Sh), ("NO", Sl)]),
                     mk({M: (0.1, 9, 0.20, 3), Sh: (0.1, 9, 0.10, 8), Sl: (0.60, 5, 0.7, 9)}))
    # n=3, prices .20,.10,.40 ; fees ceil(.07*3*.16/1e-4)=336 ; ceil(.07*3*.09/1e-4)=189 ; ceil(.07*3*.24/1e-4)=504
    assert r["n"] == 3 and r["prices"] == [0.20, 0.10, 0.40]
    assert abs(r["profit"] - (3 * 0.30 - 0.0336 - 0.0189 - 0.0504)) < 1e-9
    r = lm.eval_lock(find("bucket_rich", [("NO", M), ("NO", Sh), ("YES", Sl)]),
                     mk({M: (0.50, 6, 0.6, 3), Sh: (0.50, 4, 0.6, 3), Sl: (0.1, 5, 0.20, 9)}))
    # n=4, prices .5,.5,.2 ; fees ceil(.07*4*.25/1e-4)=700 x2 ; ceil(.07*4*.16/1e-4)=448
    assert r["n"] == 4 and abs(r["profit"] - (4 * (2 - 1.2) - 0.07 - 0.07 - 0.0448)) < 1e-9
