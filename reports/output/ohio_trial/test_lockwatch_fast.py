import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lockwatch_fast as lf

DOC = {"date": "26OCT20", "prefix": "KXNBA",
       "events": {"KXNBAGAME-26OCT20PHINYK": {}, "KXNBATOTAL-26OCT20PHINYK": {}, "KXNBAGAME-26OCT20LALGSW": {}},
       "books": {"KXNBAGAME-26OCT20PHINYK-PHI": {}, "KXNBATOTAL-26OCT20PHINYK-T210": {},
                 "KXNBAGAME-26OCT20LALGSW-LAL": {}}}


def test_games_filter():
    d = lf.filter_games(DOC, ["26oct20phinyk"])
    assert set(d["books"]) == {"KXNBAGAME-26OCT20PHINYK-PHI", "KXNBATOTAL-26OCT20PHINYK-T210"}
    assert set(d["events"]) == {"KXNBAGAME-26OCT20PHINYK", "KXNBATOTAL-26OCT20PHINYK"}
    assert len(DOC["books"]) == 3                                  # input untouched
    assert len(lf.filter_games(DOC, ["26OCT20PHINYK", "26OCT20LALGSW"])["books"]) == 3
    assert lf.filter_games(DOC, ["NOPE"])["books"] == {}


def test_parse_games():
    assert lf.parse_games("a, b,,") == ["A", "B"] and lf.parse_games(None) == []


def test_json_fallback(monkeypatch):
    raw = '{"type":"orderbook_delta","msg":{"x":1}}'
    for impl in (lf._orjson, None):
        monkeypatch.setattr(lf, "_orjson", impl)
        assert lf.loads(raw)["msg"]["x"] == 1
        assert lf.loads(raw.encode())["type"] == "orderbook_delta"
        try:
            lf.loads("{bad")
            assert False
        except ValueError:
            pass
        assert json.loads(lf.dumps({"a": [1, 2]})) == {"a": [1, 2]}


def test_fast_bookset_matches_stdlib_feed():
    from kpm.analysis.ws_books import BookSet
    msgs = [{"type": "orderbook_snapshot", "sid": 1, "seq": 1, "msg": {"market_ticker": "M",
             "yes_dollars_fp": [["0.5000", "10.00"]], "no_dollars_fp": [["0.4000", "5.00"]]}},
            {"type": "orderbook_delta", "sid": 1, "seq": 2, "msg": {"market_ticker": "M", "price_dollars": "0.5100",
             "delta_fp": "3.00", "side": "yes"}}]
    a, b = BookSet(), lf.make_fast_bookset()
    for i, m in enumerate(msgs):
        ra = list(a.feed({"recv_ns": i, "raw": json.dumps(m)}))
        rb = list(b.feed({"recv_ns": i, "msg_obj": m}))
        assert ra == rb and ra
