import gzip
import json

from kpm.analysis import ws_books as wb


def raw(ns, msg):
    return {"recv_ns": ns, "raw": json.dumps(msg)}


def snap(sid, seq, t, yes, no):
    m = {"market_ticker": t, "market_id": "x"}
    if yes:
        m["yes_dollars_fp"] = [[p, c] for p, c in yes]
    if no:
        m["no_dollars_fp"] = [[p, c] for p, c in no]
    return {"type": "orderbook_snapshot", "sid": sid, "seq": seq, "msg": m}


def dlt(sid, seq, t, price, d, side):
    return {"type": "orderbook_delta", "sid": sid, "seq": seq,
            "msg": {"market_ticker": t, "price_dollars": price, "delta_fp": d, "side": side}}


def trade(sid, seq, t, p, c, side):
    return {"type": "trade", "sid": sid, "seq": seq, "msg": {"market_ticker": t, "yes_price_dollars": p, "count_fp": c,
                                                              "taker_side": side}}


def write(tmp_path, name, items):
    d = tmp_path / "raw"
    d.mkdir(exist_ok=True)
    with gzip.open(d / name, "wt") as fh:
        for it in items:
            fh.write(json.dumps(it) + "\n")
    return d


def test_snapshot_delta_trade(tmp_path):
    d = write(tmp_path, "msgs-2026102200.jsonl.gz", [
        {"recv_ns": 1, "ctl": {"event": "connect"}},
        raw(2, {"id": 1, "type": "subscribed", "msg": {"sid": 1}}),
        raw(2, {"id": 2, "type": "ok", "sid": 1, "seq": 0, "msg": {"market_tickers": ["T"]}}),
        raw(3, snap(1, 1, "T", [("0.4000", "10.00"), ("0.4500", "5.00")], [("0.5000", "7.00")])),
        raw(4, dlt(1, 2, "T", "0.4500", "-5.00", "yes")),
        raw(5, dlt(1, 3, "T", "0.5200", "3.00", "no")),
        raw(6, trade(2, 1, "T", "0.4800", "2.00", "yes")),
        raw(7, dlt(1, 4, "T", "0.3000", "1.00", "yes")),        # no top change -> no event
    ])
    ev = list(wb.iter_events(d))
    assert ev[0] == wb.Top(3, "T", 0.45, 5.0, 0.5, 7.0)
    assert ev[1] == wb.Top(4, "T", 0.4, 10.0, 0.5, 7.0)
    assert ev[2] == wb.Top(5, "T", 0.4, 10.0, 0.48, 3.0)         # NO bid 0.52 -> YES ask 0.48
    assert ev[3] == wb.Trade(6, "T", 0.48, 2.0, "yes")
    assert len(ev) == 4


def test_gap_marks_book_stale_until_resnapshot_on_new_sid(tmp_path):
    d = write(tmp_path, "msgs-2026102200.jsonl.gz", [
        raw(1, snap(1, 1, "T", [("0.4000", "10.00")], [("0.5000", "7.00")])),
        raw(2, dlt(1, 2, "T", "0.4100", "1.00", "yes")),
        raw(3, dlt(1, 4, "T", "0.4200", "1.00", "yes")),        # gap: dropped and book untrusted
        {"recv_ns": 4, "ctl": {"event": "seq_gap", "sid": 1}},
        raw(5, dlt(1, 5, "T", "0.4300", "1.00", "yes")),        # still stale
        raw(6, snap(2, 1, "T", [("0.4400", "2.00")], [("0.5000", "7.00")])),   # resubscribe snapshot, new sid
        raw(7, dlt(1, 6, "T", "0.4900", "9.00", "yes")),        # late delta from the old sid is ignored
        raw(8, dlt(2, 2, "T", "0.4400", "1.00", "yes")),
    ])
    tops = [e for e in wb.iter_events(d) if isinstance(e, wb.Top)]
    assert [(e.recv_ns, e.best_yes_bid, e.bid_size) for e in tops] == [(1, 0.4, 10.0), (2, 0.41, 1.0), (6, 0.44, 2.0), (8, 0.44, 3.0)]
    assert wb.summarize(d)["seq_gaps_recorded"] == 1


def test_connect_invalidates_and_files_chain(tmp_path):
    write(tmp_path, "msgs-2026102200.jsonl.gz", [raw(1, snap(1, 1, "T", [("0.4000", "10.00")], []))])
    d = write(tmp_path, "msgs-2026102201.jsonl.gz", [
        {"recv_ns": 2, "ctl": {"event": "connect"}},
        raw(3, dlt(1, 1, "T", "0.4100", "1.00", "yes")),        # no snapshot yet on this connection -> dropped
        raw(4, snap(1, 2, "T", [("0.4200", "1.00")], []))])
    tops = [e for e in wb.iter_events(d) if isinstance(e, wb.Top)]
    assert [e.recv_ns for e in tops] == [1, 4] and tops[0].best_yes_ask is None
    s = wb.summarize(d)
    assert s["markets_with_snapshot"] == 1 and s["by_type"]["orderbook_snapshot"] == 2


def test_truncated_file_does_not_hide_the_next_run(tmp_path):
    d = write(tmp_path, "msgs-2026102200.jsonl.gz", [raw(1, snap(1, 1, "T", [("0.4000", "1.00")], []))])
    full = write(tmp_path, "msgs-2026102200-200.jsonl.gz", [raw(5, snap(1, 1, "T", [("0.4200", "1.00")], []))])
    crashed = full / "msgs-2026102200-100.jsonl.gz"
    with gzip.open(crashed, "wt") as fh:
        for i in range(2000):
            fh.write(json.dumps(raw(2 + i, snap(1, i + 1, "T", [("0.41%02d" % (i % 100), "1.00")], []))) + chr(10))
    data = crashed.read_bytes()
    crashed.write_bytes(data[: len(data) // 2])                  # crash mid-member
    names = [p.name for p in wb.raw_files(d)]
    assert names == ["msgs-2026102200.jsonl.gz", "msgs-2026102200-100.jsonl.gz", "msgs-2026102200-200.jsonl.gz"]
    ns = [e.recv_ns for e in wb.iter_events(d) if isinstance(e, wb.Top)]
    assert ns[0] == 1 and ns[-1] == 5


def test_disconnect_and_stop_invalidate_every_book_until_the_next_snapshot():
    for event in ("disconnect", "stop"):
        books = wb.BookSet()
        for ln in (raw(1, snap(1, 1, "T", [("0.4000", "10.00")], [("0.5000", "7.00")])), {"recv_ns": 2, "ctl": {"event": event}}):
            list(books.feed(ln))
        assert books.sid_of == {} and books.dropped == ["T"]
        tops = [e for ln in (raw(3, dlt(1, 2, "T", "0.4100", "1.00", "yes")),     # same sid, no new snapshot: dropped
                             raw(4, snap(1, 3, "T", [("0.4400", "2.00")], [("0.5000", "7.00")])))
                for e in books.feed(ln) if isinstance(e, wb.Top)]
        assert [(e.recv_ns, e.best_yes_bid) for e in tops] == [(4, 0.44)]
